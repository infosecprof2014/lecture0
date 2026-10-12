"""Run generated code and its tests with layered safety limits.

This is defence in depth, not a perfect sandbox:
1. A static check rejects code that imports or calls anything dangerous (nothing runs).
2. The test process installs a Python audit hook first, which blocks writing files,
   network access, starting programs and deleting things, whatever the code tries.
3. The process runs isolated (-I -B), in the task's project folder, with a minimal
   environment, a wall-clock timeout, and on Windows a Job Object that caps memory and
   forbids child processes (on Linux: resource limits).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("localforge.sandbox")

FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "ctypes", "multiprocessing", "threading",
    "_thread", "signal", "urllib", "http", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
    "requests", "pathlib", "glob", "tempfile", "importlib", "builtins", "pickle", "marshal",
    "shelve", "io", "asyncio", "webbrowser", "winreg", "msvcrt", "_winapi", "select", "selectors",
    "ssl", "pty", "resource", "gc", "inspect", "code", "codeop", "runpy", "zipfile", "tarfile",
    "sqlite3", "fileinput", "mmap", "platform", "sysconfig", "site", "faulthandler", "pdb",
}
FORBIDDEN_CALLS = {
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "help", "exit", "quit", "memoryview",
}
SAFE_DUNDERS = {
    "__name__", "__doc__", "__init__", "__repr__", "__str__", "__eq__", "__ne__", "__lt__",
    "__le__", "__gt__", "__ge__", "__hash__", "__len__", "__iter__", "__next__", "__contains__",
    "__getitem__", "__setitem__", "__delitem__", "__add__", "__sub__", "__mul__", "__bool__",
    "__enter__", "__exit__", "__call__", "__post_init__", "__slots__", "__all__",
}

HARNESS = r'''
import json, sys, time, traceback
import os.path as _p

HERE = _p.dirname(_p.abspath(__file__))
sys.path.insert(0, HERE)
_O_WRITE = 0x0001 | 0x0002 | 0x0008 | 0x0100 | 0x0200 | 0x0400  # WRONLY RDWR APPEND CREAT TRUNC EXCL (Win values)
try:
    import os as _os
    _O_WRITE = _os.O_WRONLY | _os.O_RDWR | _os.O_APPEND | _os.O_CREAT | _os.O_TRUNC | _os.O_EXCL
except Exception:
    pass
_BLOCK_PREFIXES = ("socket.", "subprocess.", "shutil.", "ctypes.", "winreg.", "_winapi.",
                   "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "webbrowser.", "urllib.",
                   "ftplib.", "smtplib.", "http.", "msvcrt.")
_BLOCK = {"os.system", "os.startfile", "os.remove", "os.rmdir", "os.rename", "os.mkdir", "os.chmod",
          "os.truncate", "os.kill", "os.putenv", "os.unsetenv", "os.symlink", "os.link", "os.chdir",
          "os.removexattr", "os.setxattr", "builtins.input", "pdb.Pdb", "sys.settrace", "sys.setprofile"}

def _hook(event, args):
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _O_WRITE):
            raise PermissionError("LocalForge sandbox: writing files is not allowed")
    elif event in _BLOCK or event.startswith(_BLOCK_PREFIXES):
        raise PermissionError("LocalForge sandbox: '%s' is not allowed" % event)

sys.addaudithook(_hook)

result = {"status": "ok", "tests": []}
try:
    import test_solution as _t
except BaseException as e:
    result = {"status": "error", "error": "Could not load the tests: %s: %s" % (type(e).__name__, e), "tests": []}
else:
    for name in sorted(n for n in dir(_t) if n.startswith("test_") and callable(getattr(_t, n))):
        started = time.perf_counter()
        try:
            getattr(_t, name)()
            outcome, detail = "passed", ""
        except AssertionError as e:
            outcome = "failed"
            tb = traceback.extract_tb(e.__traceback__)
            line = tb[-1].line if tb else ""
            detail = "assert failed: %s%s" % (line, (" (" + str(e) + ")") if str(e) else "")
        except BaseException as e:
            outcome, detail = "error", "%s: %s" % (type(e).__name__, e)
        result["tests"].append({"name": name, "outcome": outcome, "detail": detail[:500],
                                "ms": round((time.perf_counter() - started) * 1000)})
sys.stdout.write("\n__LOCALFORGE_RESULT__" + json.dumps(result) + "\n")
sys.stdout.flush()
'''


def check_code(code: str) -> list[str]:
    """Static safety check. Returns problems; an empty list means allowed to run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    problems.append(f"line {node.lineno}: import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if node.level == 0 and mod in FORBIDDEN_MODULES:
                problems.append(f"line {node.lineno}: import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            problems.append(f"line {node.lineno}: use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Name) and node.id.startswith("__") and node.id not in SAFE_DUNDERS:
            problems.append(f"line {node.lineno}: use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr not in SAFE_DUNDERS:
            problems.append(f"line {node.lineno}: access to '.{node.attr}' is not allowed")
    return sorted(set(problems), key=lambda p: int(p.split()[1].rstrip(":")) if p.startswith("line ") else 0)


@dataclass
class TestResult:
    status: str  # passed | failed | error | timeout | blocked
    passed: int = 0
    failed: int = 0
    tests: list[dict] = field(default_factory=list)
    output: str = ""
    duration_ms: int = 0
    memory_cap: str = "not applied"

    def summary(self) -> str:
        lines = [f"{self.status}: {self.passed} passed, {self.failed} failed ({self.duration_ms} ms)"]
        for t in self.tests:
            if t["outcome"] != "passed":
                lines.append(f"- {t['name']}: {t['outcome']} - {t['detail']}")
        if self.status in ("error", "timeout", "blocked") and self.output:
            lines.append(self.output[-1500:])
        return "\n".join(lines)


# ---------------------------------------------------------------- Windows Job Object (memory cap, no children)

def _windows_job(memory_mb: int):
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = EXTENDED()
    # PROCESS_MEMORY | JOB_MEMORY | ACTIVE_PROCESS | KILL_ON_JOB_CLOSE
    info.BasicLimitInformation.LimitFlags = 0x100 | 0x200 | 0x8 | 0x2000
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = info.JobMemoryLimit = memory_mb * 1024 * 1024
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        err = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")

    def assign(proc: subprocess.Popen) -> None:
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    return assign, lambda: k32.CloseHandle(job)


def _posix_limits(memory_mb: int):
    def preexec():  # runs in the child before Python starts
        import resource
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if os.getuid() != 0:  # root ignores RLIMIT_NPROC
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    return preexec


def run_tests(project_dir: Path, *, python: str | None = None, timeout_s: int = 10,
              memory_mb: int = 512) -> TestResult:
    """Run test_solution.py against solution.py in `project_dir` (both already written)."""
    problems = check_code((project_dir / "solution.py").read_text(encoding="utf-8")) + \
        [f"tests: {p}" for p in check_code((project_dir / "test_solution.py").read_text(encoding="utf-8"))]
    if problems:
        return TestResult("blocked", output="Not run - the code failed the safety check:\n" + "\n".join(problems))

    (project_dir / "_harness.py").write_text(HARNESS, encoding="utf-8")
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):  # Python needs these on Windows
        if key in os.environ:
            env[key] = os.environ[key]
    cmd = [python or sys.executable, "-I", "-B", "-X", "utf8", "_harness.py"]
    memory_cap = "not applied"
    started = time.perf_counter()
    kwargs: dict = {}
    close_job = None
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    else:
        kwargs["preexec_fn"] = _posix_limits(memory_mb)
        memory_cap = f"{memory_mb} MB (rlimit)"
    proc = subprocess.Popen(cmd, cwd=project_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kwargs)
    if sys.platform == "win32":
        try:
            assign, close_job = _windows_job(memory_mb)
            assign(proc)
            memory_cap = f"{memory_mb} MB, no child processes (Job Object)"
        except OSError as exc:
            log.warning("Could not apply the sandbox memory cap: %s", exc)
            memory_cap = f"not applied ({exc})"
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        timed_out = True
    finally:
        if close_job:
            close_job()
    duration_ms = round((time.perf_counter() - started) * 1000)
    text = out.decode("utf-8", "replace")
    output, _, payload = text.rpartition("__LOCALFORGE_RESULT__")
    output = output[-4000:] if payload else text[-4000:]

    if timed_out:
        return TestResult("timeout", output=f"Stopped after {timeout_s} s (time limit).\n{output}",
                          duration_ms=duration_ms, memory_cap=memory_cap)
    try:
        data = json.loads(payload.strip().splitlines()[0])
    except (ValueError, IndexError):
        return TestResult("error", output=f"The test process ended without a result (exit {proc.returncode}).\n{output}",
                          duration_ms=duration_ms, memory_cap=memory_cap)
    if data["status"] != "ok":
        return TestResult("error", output=data.get("error", "") + "\n" + output,
                          duration_ms=duration_ms, memory_cap=memory_cap)
    tests = data["tests"]
    passed = sum(t["outcome"] == "passed" for t in tests)
    failed = len(tests) - passed
    status = "passed" if tests and failed == 0 else ("failed" if tests else "error")
    return TestResult(status, passed, failed, tests, output if status != "passed" else "", duration_ms, memory_cap)
