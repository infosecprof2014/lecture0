"""File writer and video storyboard agents."""

from __future__ import annotations

from typing import Any

from core.media import check_new_file

from .base import Agent, load_prompt

FILE_SCHEMA = {
    "type": "object",
    "properties": {"filename": {"type": "string"}, "content": {"type": "string"}},
    "required": ["filename", "content"],
}

STORYBOARD_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {"image_prompt": {"type": "string"}, "caption": {"type": "string"},
                           "narration": {"type": "string"}},
            "required": ["image_prompt", "caption", "narration"]}},
    },
    "required": ["title", "scenes"],
}


class FileWriter(Agent):
    name = "filewriter"

    def __init__(self, *args, max_kb: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_kb = max_kb

    async def write(self, request: str) -> dict:
        def validate(data: Any) -> str | None:
            if not isinstance(data, dict) or not isinstance(data.get("filename"), str) \
                    or not isinstance(data.get("content"), str):
                return 'expected {"filename": "...", "content": "..."}'
            return check_new_file(data["filename"], data["content"], self.max_kb)
        data, _ = await self.call_json(load_prompt("filewriter"), f"Request:\n{request}", FILE_SCHEMA, validate)
        return {"filename": data["filename"].strip(), "content": data["content"]}


class Storyboarder(Agent):
    name = "storyboard"

    async def plan(self, request: str, max_scenes: int) -> dict:
        def validate(data: Any) -> str | None:
            if not isinstance(data, dict) or not isinstance(data.get("scenes"), list) or not data["scenes"]:
                return 'expected {"title": "...", "scenes": [...]} with at least one scene'
            for i, sc in enumerate(data["scenes"], 1):
                if not isinstance(sc, dict) or not all(isinstance(sc.get(k), str) and sc[k].strip()
                                                       for k in ("image_prompt", "caption", "narration")):
                    return f"scene {i} needs image_prompt, caption and narration"
            return None
        data, _ = await self.call_json(load_prompt("storyboard", max_scenes=max_scenes), f"Video idea:\n{request}",
                                       STORYBOARD_SCHEMA, validate)
        scenes = [{k: sc[k].strip() for k in ("image_prompt", "caption", "narration")} for sc in data["scenes"]]
        return {"title": str(data.get("title", "")).strip() or "Untitled", "scenes": scenes[:max_scenes]}
