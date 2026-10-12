"""Small async client for the parts of the Ollama API the core uses."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class OllamaUnavailable(Exception):
    """Ollama could not be reached, or broke off mid-answer."""


class OllamaClient:
    def __init__(self, base_url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            # Short connect timeout so "Ollama is down" is reported quickly.
            timeout=httpx.Timeout(timeout_s, connect=3.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def not_running_message(self) -> str:
        return f"Ollama is not running at {self.base_url}. Start the Ollama app, or run: ollama serve"

    async def _get(self, path: str) -> Any:
        try:
            r = await self._client.get(path, timeout=10.0)
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise OllamaUnavailable(f"{self.not_running_message()} ({exc.__class__.__name__})") from exc

    async def version(self) -> str:
        return (await self._get("/api/version")).get("version", "unknown")

    async def installed_models(self) -> list[dict]:
        return (await self._get("/api/tags")).get("models", [])

    async def loaded_models(self) -> list[dict]:
        return (await self._get("/api/ps")).get("models", [])

    async def unload(self, model: str) -> None:
        try:
            r = await self._client.post("/api/generate", json={"model": model, "keep_alive": 0}, timeout=30.0)
            r.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            raise OllamaUnavailable(f"Could not unload {model}: {exc}") from exc

    async def chat_stream(self, model: str, messages: list[dict]) -> AsyncIterator[dict]:
        """Yield Ollama's streamed chat chunks (dicts) until the final one with done=true."""
        body = {"model": model, "messages": messages, "stream": True}
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode("utf-8", "replace")[:300]
                    raise OllamaUnavailable(f"Ollama returned HTTP {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise OllamaUnavailable(f"Ollama error: {chunk['error']}")
                    yield chunk
                    if chunk.get("done"):
                        return
            raise OllamaUnavailable("Ollama closed the connection before the answer finished")
        except httpx.TransportError as exc:
            raise OllamaUnavailable(f"Lost connection to Ollama: {exc.__class__.__name__}") from exc
