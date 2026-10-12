"""Shared plumbing for the agents: one model call, recorded, with structured output."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.ollama_client import OllamaClient

PROMPTS = Path(__file__).resolve().parent / "prompts"


class AgentError(Exception):
    """The model's answer could not be used, even after a retry."""


@dataclass
class Call:
    """One model call, for recording in agent_runs."""
    output: str
    tokens: int | None
    duration_ms: int


def load_prompt(name: str, **values: Any) -> str:
    """Read prompts/<name>.md and fill {placeholders} (plain replace: the files contain JSON braces)."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


class Agent:
    name = "agent"

    def __init__(self, ollama: OllamaClient, model: str, *, max_tokens: int, temperature: float,
                 record: Callable[..., Any], before_call: Callable[[], None] = lambda: None):
        """`record(agent, input_text)` must return a callable `finish(status, output, tokens, duration_ms)`."""
        self.ollama = ollama
        self.model = model
        self.options = {"num_predict": max_tokens, "temperature": temperature}
        self.record = record
        self.before_call = before_call

    async def call(self, system: str, user: str, schema: dict | None = None) -> Call:
        """One model call. Cancellation (task cancel) propagates and is recorded as 'cancelled'."""
        self.before_call()
        finish = self.record(self.name, user)
        started = time.perf_counter()
        parts: list[str] = []
        tokens = None
        status = "error"
        try:
            async for chunk in self.ollama.chat_stream(
                self.model,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                format=schema,
                options=self.options,
            ):
                parts.append(chunk.get("message", {}).get("content", ""))
                if chunk.get("done"):
                    tokens = chunk.get("eval_count")
            status = "ok"
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            status = "cancelled" if exc.__class__.__name__ == "CancelledError" else "error"
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000)
            finish(status, "".join(parts), tokens, duration_ms)
        return Call("".join(parts), tokens, duration_ms)

    async def call_json(self, system: str, user: str, schema: dict,
                        validate: Callable[[Any], str | None]) -> tuple[dict, Call]:
        """Model call that must return JSON matching `schema`. Retries once with the problem stated."""
        problem = ""
        for attempt in (1, 2):
            prompt = user if attempt == 1 else (
                f"{user}\n\nYour previous answer could not be used: {problem}\n"
                "Answer again with valid JSON in the required shape only.")
            call = await self.call(system, prompt, schema)
            try:
                data = json.loads(call.output)
            except json.JSONDecodeError as exc:
                problem = f"not valid JSON ({exc.msg})"
                continue
            problem = validate(data) or ""
            if not problem:
                return data, call
        raise AgentError(f"{self.name} gave an unusable answer twice: {problem}")
