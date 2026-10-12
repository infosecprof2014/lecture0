"""Planner: turns a request into 1..max_steps steps."""

from __future__ import annotations

from typing import Any

from .base import Agent, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
                "required": ["title", "description"],
            },
        }
    },
    "required": ["steps"],
}


def validate(data: Any) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        return 'expected an object with a "steps" list'
    if not data["steps"]:
        return "the plan has no steps"
    for i, step in enumerate(data["steps"], 1):
        if not isinstance(step, dict):
            return f"step {i} is not an object"
        for key in ("title", "description"):
            if not isinstance(step.get(key), str) or not step[key].strip():
                return f'step {i} has no "{key}"'
    return None


class Planner(Agent):
    name = "planner"

    async def plan(self, request: str, max_steps: int) -> tuple[list[dict], bool]:
        """Returns (steps, truncated). Plans longer than max_steps are cut to max_steps."""
        data, _ = await self.call_json(load_prompt("planner", max_steps=max_steps),
                                       f"Request:\n{request}", SCHEMA, validate)
        steps = [{"title": s["title"].strip(), "description": s["description"].strip()} for s in data["steps"]]
        return steps[:max_steps], len(steps) > max_steps
