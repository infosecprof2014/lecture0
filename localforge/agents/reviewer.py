"""Reviewer: approves a step's code or lists the problems to fix."""

from __future__ import annotations

from typing import Any

from .base import Agent, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "revise"]},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "issues"],
}


def validate(data: Any) -> str | None:
    if not isinstance(data, dict) or data.get("verdict") not in ("approve", "revise"):
        return '"verdict" must be "approve" or "revise"'
    if not isinstance(data.get("issues"), list) or not all(isinstance(i, str) for i in data["issues"]):
        return '"issues" must be a list of strings'
    if data["verdict"] == "revise" and not [i for i in data["issues"] if i.strip()]:
        return '"revise" needs at least one issue'
    return None


class Reviewer(Agent):
    name = "reviewer"

    async def review(self, request: str, step: dict, code: str) -> tuple[str, list[str]]:
        user = (f"Overall request:\n{request}\n\nStep: {step['title']}\n{step['description']}\n\n"
                f"Code to review:\n```python\n{code}\n```")
        data, _ = await self.call_json(load_prompt("reviewer"), user, SCHEMA, validate)
        issues = [i.strip() for i in data["issues"] if i.strip()]
        return data["verdict"], (issues if data["verdict"] == "revise" else [])
