"""Tester: writes tests from the request and the public signatures only (not the code)."""

from __future__ import annotations

import ast

from core.sandbox import check_code

from .base import Agent, load_prompt
from .coder import extract_code


def signatures(code: str) -> list[str]:
    """Top-level function and class signatures of the solution, without their bodies."""
    out: list[str] = []
    for node in ast.parse(code).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            doc = ast.get_docstring(node) or ""
            out.append(f"def {node.name}({ast.unparse(node.args)})" + (f"  # {doc.splitlines()[0]}" if doc else ""))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [f"{m.name}({ast.unparse(m.args)})" for m in node.body
                       if isinstance(m, ast.FunctionDef) and (not m.name.startswith("_") or m.name == "__init__")]
            out.append(f"class {node.name}: " + ", ".join(methods))
    return out


def problem_in_tests(code: str) -> str | None:
    problems = check_code(code)
    if problems:
        return "; ".join(problems[:3])
    tree = ast.parse(code)
    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body):
        return "no test_ functions found"
    return None


class Tester(Agent):
    name = "tester"

    async def write_tests(self, request: str, sigs: list[str]) -> tuple[str | None, str | None]:
        """Returns (test code, None) or (None, reason). One retry with the problem stated."""
        user = f"User's request:\n{request}\n\nSignatures available from solution.py:\n" + "\n".join(sigs)
        problem = None
        for attempt in (1, 2):
            prompt = user if attempt == 1 else f"{user}\n\nYour previous tests could not be used: {problem}. Try again."
            code = extract_code((await self.call(load_prompt("tester"), prompt)).output)
            problem = problem_in_tests(code)
            if not problem:
                return code, None
        return None, problem


class Fixer(Agent):
    """The Coder again, rewriting the whole solution from failing test output."""
    name = "fixer"

    async def fix(self, request: str, solution: str, tests: str, failures: str) -> str:
        user = (f"User's request:\n{request}\n\nCurrent solution.py:\n```python\n{solution}\n```\n\n"
                f"Tests (test_solution.py):\n```python\n{tests}\n```\n\nTest results:\n{failures}")
        return extract_code((await self.call(load_prompt("fixer"), user)).output)
