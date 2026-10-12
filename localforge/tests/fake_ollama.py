"""A pretend Ollama server, served in-process through httpx.MockTransport."""

from __future__ import annotations

import json

import httpx

GB = 1024 ** 3


class FakeOllama:
    def __init__(self):
        self.running = True
        self.installed = {
            "llama3.2:3b": {"name": "llama3.2:3b", "size": int(1.9 * GB),
                            "details": {"parameter_size": "3.2B", "quantization_level": "Q4_K_M"}},
            "qwen3:4b": {"name": "qwen3:4b", "size": int(2.3 * GB),
                         "details": {"parameter_size": "4.0B", "quantization_level": "Q4_K_M"}},
        }
        self.loaded: list[str] = []
        self.reply = ["Hel", "lo", " there", "!"]
        self.fail_after: int | None = None  # drop the connection after N chunks
        self.chat_requests: list[dict] = []
        self.unloaded: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.running:
            raise httpx.ConnectError("Connection refused", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": list(self.installed.values())})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.loaded]})
        if path == "/api/generate":
            body = json.loads(request.content)
            if body.get("keep_alive") == 0:
                self.unloaded.append(body["model"])
                self.loaded = [m for m in self.loaded if m != body["model"]]
            return httpx.Response(200, json={"done": True})
        if path == "/api/chat":
            body = json.loads(request.content)
            self.chat_requests.append(body)
            if body["model"] not in self.loaded:
                self.loaded.append(body["model"])
            return httpx.Response(200, stream=_ChatStream(self.reply, self.fail_after, request))
        return httpx.Response(404, json={"error": "not found"})


class _ChatStream(httpx.AsyncByteStream):
    def __init__(self, parts: list[str], fail_after: int | None, request: httpx.Request):
        self.parts, self.fail_after, self.request = parts, fail_after, request

    async def __aiter__(self):
        for i, part in enumerate(self.parts):
            if self.fail_after is not None and i >= self.fail_after:
                raise httpx.ReadError("Connection reset", request=self.request)
            yield (json.dumps({"message": {"role": "assistant", "content": part}, "done": False}) + "\n").encode()
        yield (json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                           "eval_count": len(self.parts), "eval_duration": 500_000_000}) + "\n").encode()
