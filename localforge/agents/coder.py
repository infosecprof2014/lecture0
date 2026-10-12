"""Coder: writes Python code for one step."""

from __future__ import annotations

import ast
import re

from .base import Agent, load_prompt

FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text: str) -> str:
    """The code inside the first ``` block, or the whole answer if there is no block."""
    match = FENCE.search(text)
    if match:
        return match.group(1).strip()
    # An unclosed fence (answer cut off by the token limit): drop the opening line.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return stripped.strip()


def syntax_problem(code: str) -> str | None:
    """Checks the code is valid Python by parsing it. Nothing is executed."""
    if not code.strip():
        return "the answer contained no code"
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return f"SyntaxError on line {exc.lineno}: {exc.msg}"
    return None


class Coder(Agent):
    name = "coder"

    async def write(self, request: str, plan: list[dict], step_index: int,
                    earlier_code: list[str], issues: list[str], previous_code: str | None) -> str:
        step = plan[step_index]
        lines = [f"Overall request:\n{request}", "", "Plan:"]
        lines += [f"{i}. {s['title']}: {s['description']}" for i, s in enumerate(plan, 1)]
        lines += ["", f"Current step: {step_index + 1}. {step['title']}", step["description"]]
        if earlier_code:
            lines += ["", "Code already written for earlier steps:", "```python", "\n\n".join(earlier_code), "```"]
        if issues:
            lines += ["", "Your previous code for this step:", "```python", previous_code or "", "```",
                      "", "Reviewer issues to fix:"] + [f"- {i}" for i in issues]
        call = await self.call(load_prompt("coder"), "\n".join(lines))
        return extract_code(call.output)
