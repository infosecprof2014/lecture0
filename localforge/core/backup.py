"""Backups of everything LocalForge cannot recreate: database, settings, agent prompts, projects.

    python -m core.backup create            make a backup now
    python -m core.backup auto              make one only if the newest is older than auto_backup_hours
    python -m core.backup list              list backups, newest first
    python -m core.backup verify <zip>      check a backup can be restored
    python -m core.backup restore <zip>     restore (the core must be stopped; a safety backup is made first)
    python -m core.backup selftest          full backup -> change -> restore round trip in a temporary folder
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import ROOT, Settings, load_settings

DB_ARCNAME = "database/localforge.db"
SETTINGS_ARCNAME = "config/settings.json"


class BackupError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_db(src: Path, dest: Path) -> None:
    """Consistent copy of a live SQLite database (safe while the core is running)."""
    with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    sqlite3.connect(dest).close()


def _integrity(db: Path) -> tuple[bool, dict]:
    con = sqlite3.connect(db)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts = {}
        for table in ("conversations", "messages", "tasks", "task_steps", "agent_runs", "test_runs"):
            try:
                counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        return ok, counts
    finally:
        con.close()


def list_backups(settings: Settings) -> list[Path]:
    folder = settings.backups_dir
    if not folder.is_dir():
        return []
    return sorted(folder.glob("localforge-*.zip"), key=lambda p: p.name, reverse=True)


def prune(settings: Settings) -> list[Path]:
    removed = []
    for old in list_backups(settings)[settings.keep_backups:]:
        old.unlink()
        removed.append(old)
    return removed


def create_backup(settings: Settings, reason: str = "manual") -> Path:
    folder = settings.backups_dir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _now().astimezone().strftime("%Y%m%d-%H%M%S")
    target = folder / f"localforge-{stamp}.zip"
    n = 1
    while target.exists():
        target = folder / f"localforge-{stamp}-{n}.zip"
        n += 1

    files = 0
    partial = target.with_suffix(".zip.part")
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
        db_ok, counts = None, {}
        if settings.db_file.is_file():
            snap = Path(tmp) / "localforge.db"
            _snapshot_db(settings.db_file, snap)
            db_ok, counts = _integrity(snap)
            zf.write(snap, DB_ARCNAME)
            files += 1
        settings_file = ROOT / "config" / "settings.json"
        if settings_file.is_file():
            zf.write(settings_file, SETTINGS_ARCNAME)
            files += 1
        for folder_name, base in (("agents/prompts", ROOT / "agents" / "prompts"), ("projects", settings.projects_dir)):
            if base.is_dir():
                for f in sorted(base.rglob("*")):
                    if f.is_file() and "__pycache__" not in f.parts:
                        zf.write(f, f"{folder_name}/{f.relative_to(base).as_posix()}")
                        files += 1
        manifest = {"created": _now().isoformat(timespec="seconds"), "reason": reason, "version": __version__,
                    "files": files, "database_ok": db_ok, "rows": counts}
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    partial.replace(target)  # only complete backups get the .zip name
    prune(settings)
    return target


def verify_backup(path: Path) -> dict:
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise BackupError(f"{path} is not a valid backup file")
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            raise BackupError(f"{path}: damaged file inside: {bad}")
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise BackupError(f"{path}: no manifest.json - not a LocalForge backup")
        manifest = json.loads(zf.read("manifest.json"))
        result = {"file": str(path), "manifest": manifest, "database_ok": None, "rows": {},
                  "has_settings": SETTINGS_ARCNAME in names,
                  "prompts": sum(n.startswith("agents/prompts/") for n in names),
                  "project_files": sum(n.startswith("projects/") for n in names)}
        if DB_ARCNAME in names:
            with tempfile.TemporaryDirectory() as tmp:
                zf.extract(DB_ARCNAME, tmp)
                ok, rows = _integrity(Path(tmp) / DB_ARCNAME)
                result["database_ok"], result["rows"] = ok, rows
                if not ok:
                    raise BackupError(f"{path}: the database inside failed its integrity check")
    return result


def core_running(settings: Settings) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((settings.host if settings.host != "localhost" else "127.0.0.1", settings.port)) == 0


def restore_backup(settings: Settings, path: Path) -> dict:
    if core_running(settings):
        raise BackupError("Stop the LocalForge core first (it is running on port %d)." % settings.port)
    info = verify_backup(path)
    safety = create_backup(settings, reason="before-restore")
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        tmp = Path(tmp)
        if (tmp / DB_ARCNAME).is_file():
            settings.db_file.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(settings.db_file) + suffix).unlink(missing_ok=True)
            shutil.copyfile(tmp / DB_ARCNAME, settings.db_file)
        if (tmp / SETTINGS_ARCNAME).is_file():
            shutil.copyfile(tmp / SETTINGS_ARCNAME, ROOT / "config" / "settings.json")
        if (tmp / "agents" / "prompts").is_dir():
            for f in (tmp / "agents" / "prompts").glob("*"):
                shutil.copyfile(f, ROOT / "agents" / "prompts" / f.name)
        if (tmp / "projects").is_dir():
            if settings.projects_dir.is_dir():
                shutil.rmtree(settings.projects_dir)
            shutil.copytree(tmp / "projects", settings.projects_dir)
    return {"restored": str(path), "safety_backup": str(safety), "rows": info["rows"]}


def auto_backup(settings: Settings) -> str:
    backups = list_backups(settings)
    if backups:
        age_h = (datetime.now().timestamp() - backups[0].stat().st_mtime) / 3600
        if age_h < settings.auto_backup_hours:
            return f"skipped: newest backup is {age_h:.1f} h old ({backups[0].name})"
    return f"created {create_backup(settings, reason='auto')}"


def selftest() -> dict:
    """Backup -> change -> restore in a throwaway folder; never touches the real data."""
    import os
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        (root / "agents" / "prompts").mkdir(parents=True)
        (root / "agents" / "prompts" / "planner.md").write_text("original prompt", encoding="utf-8")
        settings_json = {"port": 18999, "db_path": "database/localforge.db"}
        (root / "config" / "settings.json").write_text(json.dumps(settings_json), encoding="utf-8")
        (root / "projects" / "task-1").mkdir(parents=True)
        (root / "projects" / "task-1" / "solution.py").write_text("x = 1\n", encoding="utf-8")
        (root / "database").mkdir()
        con = sqlite3.connect(root / "database" / "localforge.db")
        con.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute("INSERT INTO conversations (title) VALUES ('kept')")
        con.commit()
        con.close()

        env = {**os.environ, "LOCALFORGE_ROOT": str(root)}
        here = Path(__file__).resolve().parent.parent

        def run(*args: str) -> str:
            out = subprocess.run([sys.executable, "-m", "core.backup", *args], cwd=here, env=env,
                                 capture_output=True, text=True, timeout=120)
            if out.returncode != 0:
                raise BackupError(f"backup {' '.join(args)} failed: {out.stderr or out.stdout}")
            return out.stdout.strip()

        made = run("create").split()[-1]
        # Change everything after the backup...
        con = sqlite3.connect(root / "database" / "localforge.db")
        con.execute("INSERT INTO conversations (title) VALUES ('added later')")
        con.commit()
        con.close()
        (root / "agents" / "prompts" / "planner.md").write_text("edited prompt", encoding="utf-8")
        (root / "projects" / "task-1" / "solution.py").write_text("broken", encoding="utf-8")
        # ...then restore and check it is all back.
        run("restore", made, "--yes")
        con = sqlite3.connect(root / "database" / "localforge.db")
        titles = [r[0] for r in con.execute("SELECT title FROM conversations ORDER BY id")]
        con.close()
        checks = {
            "database": titles == ["kept"],
            "prompts": (root / "agents" / "prompts" / "planner.md").read_text(encoding="utf-8") == "original prompt",
            "projects": (root / "projects" / "task-1" / "solution.py").read_text(encoding="utf-8") == "x = 1\n",
            "safety_backup_made": len(list((root / "backups").glob("localforge-*.zip"))) == 2,
        }
        return {"ok": all(checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.backup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create").add_argument("--reason", default="manual")
    sub.add_parser("auto")
    sub.add_parser("list")
    sub.add_parser("verify").add_argument("file")
    r = sub.add_parser("restore")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    sub.add_parser("selftest")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "selftest":
            result = selftest()
            print(json.dumps(result))
            return 0 if result["ok"] else 1
        settings = load_settings()
        if args.cmd == "create":
            print(f"Backup created: {create_backup(settings, args.reason)}")
        elif args.cmd == "auto":
            print(f"Auto backup {auto_backup(settings)}")
        elif args.cmd == "list":
            for b in list_backups(settings):
                print(f"{b.name}  {b.stat().st_size / 1024:.0f} KB")
        elif args.cmd == "verify":
            print(json.dumps(verify_backup(Path(args.file)), indent=2))
        elif args.cmd == "restore":
            if not args.yes:
                answer = input(f"Restore {args.file}? Current data is backed up first. Type yes: ")
                if answer.strip().lower() != "yes":
                    print("Not restored.")
                    return 1
            print(json.dumps(restore_backup(settings, Path(args.file)), indent=2))
    except (BackupError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
