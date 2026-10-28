from __future__ import annotations

from pathlib import PurePosixPath
from collections.abc import Callable
from typing import Any, Protocol

from .config import NodeConfig
from .oc import OcClient


MAX_CONTENT_BYTES = 5 * 1024 * 1024
MAX_DIFF_BYTES = 2 * 1024 * 1024


class FilePathError(ValueError):
    pass


class FileTooLarge(ValueError):
    pass


def relative_path(value: str | None, *, allow_root: bool = True) -> str:
    raw = (value or "").strip()
    if not raw or raw == ".":
        if allow_root:
            return "."
        raise FilePathError("a file path is required")
    if "\x00" in raw or "\\" in raw:
        raise FilePathError("invalid path")
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise FilePathError("absolute paths are not allowed")
    parts = [part for part in path.parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise FilePathError("parent paths are not allowed")
    if not parts:
        if allow_root:
            return "."
        raise FilePathError("a file path is required")
    return "/".join(parts)


def normalize_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    path = str(raw.get("path") or "").strip()
    kind = str(raw.get("type") or "").strip()
    if not name or not path or kind not in {"file", "directory"}:
        return None
    return {
        "name": name.rstrip("/"),
        "path": path,
        "type": kind,
        "ignored": bool(raw.get("ignored")),
    }


def content_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("invalid file content response")
    content = raw.get("content")
    if not isinstance(content, str):
        raise ValueError("invalid file content")
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise FileTooLarge(f"file is larger than {MAX_CONTENT_BYTES} bytes")
    payload = {
        "type": str(raw.get("type") or "text"),
        "content": content,
    }
    encoding = raw.get("encoding")
    mime_type = raw.get("mimeType")
    if encoding:
        payload["encoding"] = str(encoding)
    if mime_type:
        payload["mimeType"] = str(mime_type)
    return payload


class FileProvider(Protocol):
    async def list(self, directory: str, path: str) -> list[dict[str, Any]]: ...

    async def read(self, directory: str, path: str) -> dict[str, Any]: ...

    async def status(self, directory: str) -> list[dict[str, Any]]: ...

    async def diff(self, directory: str, path: str) -> dict[str, Any] | None: ...

    async def find(self, directory: str, query: str, limit: int = 50) -> list[str]: ...


class OpenCodeFileProvider:
    def __init__(self, oc: OcClient, node: NodeConfig):
        self.oc = oc
        self.node = node

    async def list(self, directory: str, path: str) -> list[dict[str, Any]]:
        data = await self.oc._get(
            self.node,
            "/file",
            {"directory": directory, "path": path},
        )
        if not isinstance(data, list):
            raise ValueError("invalid file list response")
        return [entry for item in data if (entry := normalize_entry(item)) is not None]

    async def read(self, directory: str, path: str) -> dict[str, Any]:
        data = await self.oc._get(
            self.node,
            "/file/content",
            {"directory": directory, "path": path},
        )
        return content_payload(data)

    async def status(self, directory: str) -> list[dict[str, Any]]:
        data = await self.oc._get(self.node, "/vcs/status", {"directory": directory})
        if not isinstance(data, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, dict) or not raw.get("file"):
                continue
            items.append(
                {
                    "path": str(raw.get("file")),
                    "status": str(raw.get("status") or ""),
                    "added": int(raw.get("additions") or 0),
                    "removed": int(raw.get("deletions") or 0),
                }
            )
        return items

    async def diff(self, directory: str, path: str) -> dict[str, Any] | None:
        data = await self.oc._get(
            self.node,
            "/vcs/diff",
            {"directory": directory, "mode": "git"},
        )
        if not isinstance(data, list):
            return None
        for raw in data:
            if not isinstance(raw, dict) or str(raw.get("file") or "") != path:
                continue
            patch = str(raw.get("patch") or "")
            encoded = patch.encode("utf-8")
            truncated = len(encoded) > MAX_DIFF_BYTES
            if truncated:
                patch = encoded[:MAX_DIFF_BYTES].decode("utf-8", "ignore")
            return {
                "path": path,
                "status": str(raw.get("status") or ""),
                "additions": int(raw.get("additions") or 0),
                "deletions": int(raw.get("deletions") or 0),
                "patch": patch,
                "truncated": truncated,
            }
        return None

    async def find(self, directory: str, query: str, limit: int = 50) -> list[str]:
        data = await self.oc._get(
            self.node,
            "/find/file",
            {
                "directory": directory,
                "query": query,
                "limit": max(1, min(200, limit)),
            },
        )
        if not isinstance(data, list):
            return []
        return [str(item) for item in data if isinstance(item, str)]


ProviderFactory = Callable[[OcClient, NodeConfig], FileProvider]


class FileService:
    def __init__(self, provider_factory: ProviderFactory | None = None):
        self._provider_factory = provider_factory or OpenCodeFileProvider

    def provider(self, oc: OcClient, node: NodeConfig) -> FileProvider:
        return self._provider_factory(oc, node)
