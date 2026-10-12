"""Checks each sandbox layer on this machine, and optionally runs reference tests against a task's code.

    python scripts/sandbox_probe.py                      -> JSON with one result per layer
    python scripts/sandbox_probe.py --reference <dir>    -> run reference is_palindrome tests on <dir>/solution.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import load_settings  # noqa: E402
from core.sandbox import HARNESS, check_code, run_tests  # noqa: E402

REFERENCE_TESTS = '''from solution import *

def test_simple():
    assert is_palindrome("racecar") is True

def test_case_spaces_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama") is True

def test_not_palindrome():
    assert is_palindrome("hello") is False

def test_empty():
    assert is_palindrome("") is True
'''


def make(root: Path, name: str, solution: str, tests: str) -> Path:
    d = root / name
    d.mkdir()
    (d / "solution.py").write_text(solution, encoding="utf-8")
    (d / "test_solution.py").write_text(tests, encoding="utf-8")
    return d


def probe() -> dict:
    s = load_settings()
    out: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        problems = check_code("import os\nos.remove('x')\n")
        out["static_check"] = {"ok": bool(problems), "detail": "; ".join(problems)}

        d = make(root, "hook", "def f():\n    return 1\n", "")
        (d / "_harness.py").write_text(HARNESS, encoding="utf-8")
        (d / "test_solution.py").write_text(
            "import socket\n"
            "def test_write():\n    with open('evil.txt', 'w') as fh:\n        fh.write('x')\n"
            "def test_net():\n    socket.create_connection(('127.0.0.1', 9), timeout=1)\n", encoding="utf-8")
        import subprocess
        res = subprocess.run([sys.executable, "-I", "-B", "_harness.py"], cwd=d, capture_output=True,
                             text=True, timeout=60)
        tests = json.loads(res.stdout.rsplit("__LOCALFORGE_RESULT__", 1)[1])["tests"]
        details = {t["name"]: t["detail"] for t in tests}
        out["runtime_hook"] = {
            "ok": "not allowed" in details.get("test_write", "") and "not allowed" in details.get("test_net", "")
            and not (d / "evil.txt").exists(),
            "detail": f"write: {details.get('test_write')} | network: {details.get('test_net')}"}

        d = make(root, "loop", "def f():\n    while True:\n        pass\n", "from solution import *\ndef test_f():\n    f()\n")
        r = run_tests(d, python=sys.executable, timeout_s=3, memory_mb=s.sandbox_memory_mb)
        out["timeout"] = {"ok": r.status == "timeout", "detail": f"{r.status} after {r.duration_ms} ms"}

        # Grows to 1.5 GB in 10 MB pieces: stopped by a working cap, harmless if the cap is missing.
        d = make(root, "memory", "def f():\n    x = []\n    for _ in range(150):\n        x.append(bytearray(10**7))\n"
                 "    return len(x)\n", "from solution import *\ndef test_f():\n    assert f() == 150\n")
        r = run_tests(d, python=sys.executable, timeout_s=30, memory_mb=s.sandbox_memory_mb)
        out["memory_cap"] = {"ok": "MemoryError" in r.summary() and "not applied" not in r.memory_cap,
                             "detail": f"{r.status}; cap: {r.memory_cap}; " + r.summary().splitlines()[-1][:200]}
    return out


def reference(project: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "ref"
        d.mkdir()
        shutil.copy(project / "solution.py", d / "solution.py")
        (d / "test_solution.py").write_text(REFERENCE_TESTS, encoding="utf-8")
        r = run_tests(d, python=sys.executable, timeout_s=10)
        return {"status": r.status, "passed": r.passed, "failed": r.failed, "summary": r.summary()}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--reference":
        print(json.dumps(reference(Path(sys.argv[2]))))
    else:
        print(json.dumps(probe()))
