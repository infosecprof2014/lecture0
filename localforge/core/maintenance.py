"""Status and settings upkeep.

    python -m core.maintenance status [--json]   health of the whole system
    python -m core.maintenance merge-settings    add new settings to settings.json, keep existing values
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime

import httpx

from .backup import core_running, list_backups
from .config import ROOT, SettingsError, add_missing_settings, load_settings
from .memory_guard import free_ram_bytes

GB = 1024 ** 3


def status() -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    try:
        s = load_settings()
        add("Settings", True, "config/settings.json is valid")
    except SettingsError as exc:
        add("Settings", False, str(exc))
        return {"ok": False, "checks": checks}

    running = core_running(s)
    if running:
        try:
            h = httpx.get(f"http://127.0.0.1:{s.port}/health", timeout=5).json()
            add("Core service", True, f"running on http://127.0.0.1:{s.port} (version {h.get('version')})")
        except (httpx.HTTPError, ValueError) as exc:
            add("Core service", False, f"port {s.port} is in use but /health did not answer: {exc}")
    else:
        add("Core service", None, "not running (start it with scripts\\start-core.ps1)")

    try:
        version = httpx.get(s.ollama_url + "/api/version", timeout=3).json().get("version")
        models = [m["name"] for m in httpx.get(s.ollama_url + "/api/tags", timeout=5).json().get("models", [])]
        add("Ollama", True, f"version {version}, {len(models)} model(s)")
        wanted = s.model if ":" in s.model else s.model + ":latest"
        add("Model", wanted in models, f"{wanted} " + ("installed" if wanted in models else "NOT installed"))
    except (httpx.HTTPError, ValueError):
        add("Ollama", False, f"not reachable at {s.ollama_url}")

    if s.db_file.is_file():
        con = sqlite3.connect(s.db_file)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("conversations", "tasks")
                      if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()}
        finally:
            con.close()
        size = s.db_file.stat().st_size / 1024 / 1024
        add("Database", ok, f"integrity {'ok' if ok else 'FAILED'}, {size:.1f} MB, "
            + ", ".join(f"{v} {k}" for k, v in counts.items()))
    else:
        add("Database", None, "not created yet (made on first start)")

    backups = list_backups(s)
    if backups:
        age_h = (datetime.now().timestamp() - backups[0].stat().st_mtime) / 3600
        add("Backups", age_h <= s.auto_backup_hours * 2,
            f"{len(backups)} backup(s), newest {age_h:.1f} h old ({backups[0].name})")
    else:
        add("Backups", False, "no backups yet (run scripts\\backup.ps1)")

    disk = shutil.disk_usage(ROOT)
    add("Disk", disk.free > 2 * GB, f"{disk.free / GB:.1f} GB free on the LocalForge drive")
    ram = free_ram_bytes()
    reserve = s.ram_reserve_mb * 1024 * 1024
    if ram < reserve:
        add("Memory", False, f"only {ram / GB:.1f} GB free - below the {s.ram_reserve_mb} MB safety reserve")
    elif ram < reserve + 2 * GB:
        add("Memory", None, f"{ram / GB:.1f} GB free - enough if the model is already loaded; "
                            "close programs before a first chat")
    else:
        add("Memory", True, f"{ram / GB:.1f} GB free (a 3B model needs about 2.5 GB)")
    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("status", "merge-settings"):
        print(__doc__)
        return 2
    if argv[0] == "merge-settings":
        added = add_missing_settings()
        print("Added settings: " + (", ".join(added) if added else "none (already up to date)"))
        return 0
    result = status()
    if "--json" in argv:
        print(json.dumps(result))
    else:
        for c in result["checks"]:
            mark = {True: "OK  ", False: "FAIL", None: "--  "}[c["ok"]]
            print(f"[{mark}] {c['name']:<13} {c['detail']}")
        print("\nOverall: " + ("all good" if result["ok"] else "something needs attention"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
