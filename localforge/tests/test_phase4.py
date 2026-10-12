"""Phase 4: the sandbox and the test -> fix loop (Ollama faked; the sandbox runs for real)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agents.tester import signatures, problem_in_tests
from core.sandbox import check_code, run_tests

from .test_agents import APPROVE, plan, run_task

# ---------------------------------------------------------------- static safety check

@pytest.mark.parametrize("code, fragment", [
    ("import os", "import of 'os'"),
    ("import subprocess as sp", "import of 'subprocess'"),
    ("from socket import socket", "import from 'socket'"),
    ("from urllib.request import urlopen", "import from 'urllib.request'"),
    ("open('x', 'w')", "use of 'open'"),
    ("eval('1')", "use of 'eval'"),
    ("__import__('os')", "use of '__import__'"),
    ("x = ().__class__.__base__.__subclasses__()", "'.__subclasses__'"),
    ("getattr(object, 'x')", "use of 'getattr'"),
    ("def f(:\n  pass", "SyntaxError"),
])
def test_check_code_blocks(code, fragment):
    problems = check_code(code)
    assert problems and any(fragment in p for p in problems), problems


def test_check_code_allows_normal_code():
    code = ('import string, re, math\nfrom collections import Counter\n'
            'class Stack:\n    def __init__(self):\n        self.items = []\n    def __len__(self):\n'
            '        return len(self.items)\n'
            'def is_palindrome(s: str) -> bool:\n    """Doc."""\n    t = [c.lower() for c in s if c.isalnum()]\n'
            '    return t == t[::-1]\n'
            'if __name__ == "__main__":\n    print(is_palindrome("aba"))\n')
    assert check_code(code) == []


# ---------------------------------------------------------------- running tests for real

def project(tmp_path: Path, solution: str, tests: str) -> Path:
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    (d / "solution.py").write_text(solution, encoding="utf-8")
    (d / "test_solution.py").write_text(tests, encoding="utf-8")
    return d


def test_run_tests_pass_and_fail(tmp_path):
    d = project(tmp_path, "def add(a, b):\n    return a + b\n",
                "from solution import *\ndef test_ok():\n    assert add(1, 2) == 3\n"
                "def test_bad():\n    assert add(1, 1) == 3\n")
    r = run_tests(d, timeout_s=10)
    assert r.status == "failed" and r.passed == 1 and r.failed == 1
    assert "test_bad: failed - assert failed: assert add(1, 1) == 3" in r.summary()


def test_run_tests_timeout(tmp_path):
    d = project(tmp_path, "def f():\n    while True:\n        pass\n",
                "from solution import *\ndef test_f():\n    f()\n")
    r = run_tests(d, timeout_s=2)
    assert r.status == "timeout" and r.duration_ms < 6000


def test_run_tests_blocked_before_running(tmp_path):
    d = project(tmp_path, "import os\ndef f():\n    os.remove('x')\n", "from solution import *\ndef test_f():\n    f()\n")
    r = run_tests(d)
    assert r.status == "blocked" and not (d / "_harness.py").exists()


def test_runtime_hook_blocks_writes_and_network(tmp_path):
    """Even code that slips past the static check cannot write files or open sockets."""
    d = project(tmp_path, "def f():\n    return 1\n", "from solution import *\n")
    from core.sandbox import HARNESS
    (d / "_harness.py").write_text(HARNESS, encoding="utf-8")
    (d / "test_solution.py").write_text(
        "import socket\n"
        "def test_write():\n    with open('evil.txt', 'w') as fh:\n        fh.write('x')\n"
        "def test_net():\n    socket.create_connection(('127.0.0.1', 9), timeout=1)\n", encoding="utf-8")
    import subprocess
    from core.sandbox import sandbox_python
    out = subprocess.run([sandbox_python(), "-I", "-B", "_harness.py"], cwd=d, capture_output=True, text=True,
                         timeout=30).stdout
    result = json.loads(out.rsplit("__LOCALFORGE_RESULT__", 1)[1])
    details = {t["name"]: t["detail"] for t in result["tests"]}
    assert "writing files is not allowed" in details["test_write"]
    assert "not allowed" in details["test_net"]
    assert not (d / "evil.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="memory cap on Windows is checked by the acceptance script")
def test_memory_cap(tmp_path):
    d = project(tmp_path, "def f():\n    x = []\n    for _ in range(150):\n        x.append(bytearray(10**7))\n"
                "    return len(x)\n", "from solution import *\ndef test_f():\n    assert f() == 150\n")
    r = run_tests(d, timeout_s=20, memory_mb=256)
    assert r.status == "failed" and "MemoryError" in r.summary()


# ---------------------------------------------------------------- tester helpers

def test_signatures_hide_bodies():
    code = ('def is_palindrome(s: str) -> bool:\n    """Check it."""\n    return True\n'
            'def _helper():\n    pass\nclass Stack:\n    def __init__(self):\n        pass\n'
            '    def push(self, x):\n        pass\n')
    sigs = signatures(code)
    assert sigs[0] == "def is_palindrome(s: str)  # Check it."
    assert sigs[1] == "class Stack: __init__(self), push(self, x)"
    assert "return True" not in "\n".join(sigs)


def test_test_problem():
    assert problem_in_tests("from solution import *\ndef test_a():\n    assert 1\n") is None
    assert "no test_" in problem_in_tests("from solution import *\nx = 1\n")
    assert "import of 'os'" in problem_in_tests("import os\ndef test_a():\n    pass\n")


# ---------------------------------------------------------------- the full loop (fake model, real sandbox)

BUGGY = ('```python\ndef is_palindrome(s):\n    """Check palindrome."""\n'
         '    return "".join(c.lower() for c in s if c.isalnum())\n```')
FIXED = ('```python\ndef is_palindrome(s):\n    """Check palindrome."""\n'
         '    t = "".join(c.lower() for c in s if c.isalnum())\n    return t == t[::-1]\n```')
TESTS = ('```python\nfrom solution import *\n\ndef test_simple():\n    assert is_palindrome("racecar") is True\n\n'
         'def test_sentence():\n    assert is_palindrome("A man, a plan, a canal: Panama") is True\n\n'
         'def test_not():\n    assert is_palindrome("hello") is False\n```')


def test_buggy_code_is_caught_and_fixed(client, fake):
    """The Phase 3 bug (returning the cleaned string) is caught by running tests, then fixed."""
    fake.scripted = [plan("palindrome"), BUGGY, APPROVE, TESTS, FIXED]
    events = run_task(client, "Write is_palindrome(s) ignoring case and punctuation")
    runs = [e for e in events if e["type"] == "test_run"]
    assert [r["status"] for r in runs] == ["failed", "passed"]
    assert runs[0]["failed"] == 3 and runs[1]["passed"] == 3
    assert events[-2] == {"type": "testing_done", "test_status": "passed", "note": ""}
    # the Tester saw signatures only, never the buggy body
    tester_input = next(r for r in fake.chat_requests if "the Tester" in r["messages"][0]["content"])
    assert "def is_palindrome(s)" in tester_input["messages"][1]["content"]
    assert "join(" not in tester_input["messages"][1]["content"]
    task_id = events[0]["task_id"]
    task = client.get(f"/tasks/{task_id}").json()
    assert task["test_status"] == "passed" and len(task["test_runs"]) == 2
    files = client.get(f"/tasks/{task_id}/files").json()["files"]
    assert "t == t[::-1]" in files["solution.py"] and "racecar" in files["test_solution.py"]


def test_fix_rounds_capped(client, fake, settings):
    fake.scripted = [plan("palindrome"), BUGGY, APPROVE, TESTS] + [BUGGY] * settings.max_fix_rounds
    events = run_task(client)
    runs = [e for e in events if e["type"] == "test_run"]
    assert len(runs) == settings.max_fix_rounds + 1 and all(r["status"] == "failed" for r in runs)
    assert client.get(f"/tasks/{events[0]['task_id']}").json()["test_status"] == "failed"


def test_unsafe_solution_never_runs(client, fake):
    unsafe = '```python\nimport os\ndef clean():\n    """Delete."""\n    os.remove("x")\n```'
    fake.scripted = [plan("clean"), unsafe, APPROVE]
    events = run_task(client)
    done = next(e for e in events if e["type"] == "testing_done")
    assert done["test_status"] == "blocked" and "import of 'os'" in done["note"]
    assert not [e for e in events if e["type"] == "test_run"]


def test_tester_gives_up_cleanly(client, fake):
    fake.scripted = [plan("palindrome"), FIXED, APPROVE, "no code here", "```python\nx = 1\n```"]
    events = run_task(client)
    done = next(e for e in events if e["type"] == "testing_done")
    assert done["test_status"] == "no_tests" and events[-1]["status"] == "done"


def test_unsafe_fix_rejected(client, fake):
    fake.scripted = [plan("palindrome"), BUGGY, APPROVE, TESTS, "```python\nimport subprocess\n```"]
    events = run_task(client)
    assert any(e["type"] == "fix_rejected" for e in events)
    assert client.get(f"/tasks/{events[0]['task_id']}").json()["test_status"] == "failed"


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    from core.db import Database
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE tasks (id INTEGER PRIMARY KEY, request TEXT NOT NULL, model TEXT NOT NULL, "
                      "status TEXT NOT NULL, plan TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT);")
    con.close()
    Database(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(tasks)")}
    assert {"test_status", "project_dir"} <= cols
