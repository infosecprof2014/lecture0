"""Phase 6: backups, restore, settings merge and status."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import zipfile
from dataclasses import replace

import pytest

from core.backup import BackupError, auto_backup, create_backup, list_backups, prune, selftest, verify_backup
from core.config import Settings, add_missing_settings, load_settings
from core.db import Database


@pytest.fixture
def bsettings(settings, tmp_path):
    s = replace(settings, backups_path=str(tmp_path / "backups"), keep_backups=3)
    Database(s.db_file).create_conversation("hello", "llama3.2:3b")
    return s


def test_backup_contains_everything_and_verifies(bsettings):
    path = create_backup(bsettings)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert {"manifest.json", "database/localforge.db", "config/settings.json"} <= names
    assert any(n.startswith("agents/prompts/") for n in names)
    info = verify_backup(path)
    assert info["database_ok"] is True and info["rows"]["conversations"] == 1


def test_backup_is_consistent_while_db_in_use(bsettings):
    con = sqlite3.connect(bsettings.db_file)
    con.execute("BEGIN")
    con.execute("INSERT INTO conversations (title, model, created_at, updated_at) VALUES ('open', 'm', 'x', 'x')")
    path = create_backup(bsettings)  # an uncommitted write must not corrupt or appear in the backup
    con.rollback()
    con.close()
    assert verify_backup(path)["rows"]["conversations"] == 1


def test_old_backups_pruned(bsettings):
    for _ in range(5):
        create_backup(bsettings)
    assert len(list_backups(bsettings)) == 3
    assert prune(bsettings) == []


def test_auto_backup_only_when_old(bsettings):
    assert auto_backup(bsettings).startswith("created")
    assert auto_backup(bsettings).startswith("skipped")
    newest = list_backups(bsettings)[0]
    old = time.time() - 25 * 3600
    os.utime(newest, (old, old))
    assert auto_backup(bsettings).startswith("created")


def test_verify_rejects_damaged_or_foreign_files(tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(BackupError):
        verify_backup(junk)
    foreign = tmp_path / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(BackupError, match="not a LocalForge backup"):
        verify_backup(foreign)


def test_restore_round_trip_selftest():
    result = selftest()
    assert result["ok"], result["checks"]


def test_restore_refused_while_core_running(bsettings, monkeypatch):
    import core.backup as backup
    monkeypatch.setattr(backup, "core_running", lambda s: True)
    with pytest.raises(BackupError, match="Stop the LocalForge core"):
        backup.restore_backup(bsettings, create_backup(bsettings))


def test_merge_settings_keeps_values(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"model": "qwen3:4b", "port": 8001}), encoding="utf-8")
    added = add_missing_settings(p)
    assert "keep_backups" in added and "model" not in added
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["model"] == "qwen3:4b" and data["port"] == 8001 and data["keep_backups"] == 10
    assert add_missing_settings(p) == []
    assert load_settings(p).model == "qwen3:4b"


def test_status_reports_each_part(monkeypatch):
    from core import maintenance
    result = maintenance.status()
    names = [c["name"] for c in result["checks"]]
    assert names[:2] == ["Settings", "Core service"]
    assert {"Database", "Backups", "Disk", "Memory"} <= set(names)
