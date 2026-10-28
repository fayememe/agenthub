from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from .clock import now_ms
from .models import (
    Evidence,
    EvidenceKind,
    EvidenceRef,
    PrivacyClass,
    SessionPage,
    SessionSummary,
    SourceRef,
)
from .opencode import OpenCodeClient, OpenCodeError
from .redact import redact


CORRECTION_RE = re.compile(
    r"(?i)\b(actually|instead|don't|do not|wrong|should|must|next time|remember)\b"
)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _json_snippet(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _truncate(value, limit)
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return _truncate(text, limit)


def _looks_like_correction(text: str) -> bool:
    return bool(CORRECTION_RE.search(text))


def _evidence_id(
    source_id: str,
    session_id: str,
    message_id: str | None,
    part_id: str | None,
    kind: EvidenceKind,
    content_hash: str,
) -> str:
    seed = "|".join(
        [
            source_id,
            session_id,
            message_id or "",
            part_id or "",
            kind.value,
            content_hash,
        ]
    )
    return "ev_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


class OpenCodeSource:
    def __init__(
        self,
        client: OpenCodeClient,
        node_name: str,
        *,
        explicit_sources: Sequence[SourceRef] | None = None,
        exclude: Sequence[str] = (),
        privacy_class: PrivacyClass = PrivacyClass.PRIVATE,
        max_excerpt_chars: int = 4000,
        max_tool_chars: int = 1200,
        message_limit: int = 200,
    ):
        if max_excerpt_chars < 200:
            raise ValueError("max_excerpt_chars must be at least 200")
        if max_tool_chars < 100:
            raise ValueError("max_tool_chars must be at least 100")
        self.client = client
        self.node_name = node_name
        self._explicit_sources = list(explicit_sources) if explicit_sources is not None else None
        self._exclude = tuple(item for item in exclude if item)
        self.privacy_class = privacy_class
        self.max_excerpt_chars = max_excerpt_chars
        self.max_tool_chars = max_tool_chars
        self.message_limit = message_limit
        self._sources: dict[str, SourceRef] | None = None

    async def list_sources(self) -> list[SourceRef]:
        if self._sources is None:
            await self._discover_sources()
        return sorted((self._sources or {}).values(), key=lambda item: item.source_id)

    async def refresh_sources(self) -> list[SourceRef]:
        self._sources = None
        return await self.list_sources()

    async def source_for(self, source_id: str) -> SourceRef | None:
        sources = await self.list_sources()
        return next((item for item in sources if item.source_id == source_id), None)

    async def list_sessions(
        self,
        source: SourceRef,
        cursor: str | None = None,
        limit: int = 100,
    ) -> SessionPage:
        page = await self.client.sessions(
            directory=source.directory,
            cursor=cursor,
            limit=limit,
            order="asc",
        )
        return SessionPage(
            sessions=tuple(self._summary(source, item) for item in page.items),
            cursor=page.next_cursor,
            has_more=page.has_more,
        )

    async def recent_sessions(
        self,
        source: SourceRef,
        since_ms: int,
        *,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> list[SessionSummary]:
        cursor: str | None = None
        found: list[SessionSummary] = []
        seen: set[str] = set()
        for _ in range(max(1, max_pages)):
            page = await self.client.sessions(
                directory=source.directory,
                cursor=cursor,
                limit=page_size,
                order="desc",
            )
            if not page.items:
                break
            stop = False
            for item in page.items:
                summary = self._summary(source, item)
                if summary.updated_at < since_ms:
                    stop = True
                    break
                if summary.id not in seen:
                    seen.add(summary.id)
                    found.append(summary)
            if stop or not page.next_cursor:
                break
            cursor = page.next_cursor
        return found

    async def read_session_evidence(
        self,
        source: SourceRef,
        session_id: str,
        *,
        message_limit: int | None = None,
    ) -> list[Evidence]:
        messages = await self.client.messages(
            session_id,
            directory=source.directory,
            limit=self.message_limit if message_limit is None else message_limit,
        )
        return self.extract_evidence(source, session_id, messages)

    async def read_evidence(self, ref: EvidenceRef) -> Evidence:
        source = await self.source_for(ref.source_id)
        if source is None:
            raise KeyError(f"unknown source: {ref.source_id}")
        if ref.message_id:
            message = await self.client.message(
                ref.session_id,
                ref.message_id,
                directory=source.directory,
            )
            messages = [message]
        else:
            messages = await self.client.messages(
                ref.session_id,
                directory=source.directory,
                limit=self.message_limit,
            )
        items = self.extract_evidence(source, ref.session_id, messages)
        for item in items:
            if ref.message_id and item.ref.message_id != ref.message_id:
                continue
            if ref.part_id and item.ref.part_id != ref.part_id:
                continue
            return item
        raise KeyError("evidence not found")

    def extract_evidence(
        self,
        source: SourceRef,
        session_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> list[Evidence]:
        items: list[Evidence] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            info = message.get("info") if isinstance(message.get("info"), dict) else {}
            role = str(info.get("role") or "")
            message_id = str(info.get("id") or message.get("id") or "") or None
            created_at = self._message_time(info)
            parts = message.get("parts") if isinstance(message.get("parts"), list) else []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "")
                part_id = str(part.get("id") or "") or None
                if part_type == "text":
                    if part.get("ignored") or part.get("synthetic"):
                        continue
                    text = str(part.get("text") or "").strip()
                    if not text:
                        continue
                    kind = (
                        EvidenceKind.USER_CORRECTION
                        if role == "user" and _looks_like_correction(text)
                        else EvidenceKind.OTHER
                    )
                    excerpt = f"{role or 'unknown'}: {text}"
                elif part_type == "tool":
                    state = part.get("state") if isinstance(part.get("state"), dict) else {}
                    status = str(state.get("status") or "")
                    kind = (
                        EvidenceKind.TOOL_ERROR
                        if status == "error" or state.get("error")
                        else EvidenceKind.OTHER
                    )
                    excerpt = self._tool_excerpt(part, state, status)
                else:
                    continue
                excerpt = _truncate(redact(excerpt), self.max_excerpt_chars)
                content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                items.append(
                    Evidence(
                        id=_evidence_id(
                            source.source_id,
                            session_id,
                            message_id,
                            part_id,
                            kind,
                            content_hash,
                        ),
                        ref=EvidenceRef(
                            source_id=source.source_id,
                            session_id=session_id,
                            message_id=message_id,
                            part_id=part_id,
                        ),
                        kind=kind,
                        excerpt=excerpt,
                        content_hash=content_hash,
                        privacy_class=self.privacy_class,
                        created_at=created_at,
                    )
                )
        return items

    async def _discover_sources(self) -> None:
        sources: dict[str, SourceRef] = {}
        if self._explicit_sources is not None:
            for source in self._explicit_sources:
                if not self._is_excluded(source):
                    sources[source.source_id] = source
            self._sources = sources
            return

        projects = await self.client.projects()
        for raw in projects:
            project_id = str(raw.get("id") or "")
            if not project_id or project_id == "global":
                continue
            path = str(raw.get("worktree") or "")
            try:
                directories = await self.client.project_directories(project_id)
            except OpenCodeError:
                directories = []
            if directories:
                path = directories[0]
            if not path or path == "/":
                continue
            source = SourceRef(
                source_id=f"{self.node_name}:{project_id}",
                node=self.node_name,
                project_id=project_id,
                directory=path,
            )
            if not self._is_excluded(source):
                sources[source.source_id] = source
        self._sources = sources

    def _is_excluded(self, source: SourceRef) -> bool:
        directory = source.directory or ""
        for pattern in self._exclude:
            if source.source_id == pattern:
                return True
            if directory and (
                directory == pattern
                or directory.startswith(pattern.rstrip("/") + "/")
            ):
                return True
        return False

    def _summary(self, source: SourceRef, raw: dict[str, Any]) -> SessionSummary:
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        updated = (raw.get("time") or {}).get("updated") if isinstance(raw.get("time"), dict) else 0
        directory = raw.get("directory") or location.get("directory")
        return SessionSummary(
            id=str(raw.get("id") or ""),
            source_id=source.source_id,
            title=str(raw.get("title") or raw.get("id") or ""),
            updated_at=int(updated or 0),
            project_id=raw.get("projectID"),
            directory=directory,
        )

    def _message_time(self, info: dict[str, Any]) -> int:
        value = info.get("time") if isinstance(info.get("time"), dict) else {}
        created = value.get("created") if isinstance(value, dict) else None
        return int(created) if isinstance(created, (int, float)) else now_ms()

    def _tool_excerpt(
        self,
        part: dict[str, Any],
        state: dict[str, Any],
        status: str,
    ) -> str:
        tool = str(part.get("tool") or "tool")
        lines = [f"tool {tool} status={status or 'unknown'}"]
        input_text = _json_snippet(state.get("input"), self.max_tool_chars)
        output_text = _json_snippet(state.get("output"), self.max_tool_chars)
        error_text = _json_snippet(state.get("error"), self.max_tool_chars)
        if input_text:
            lines.append(f"input: {input_text}")
        if output_text:
            lines.append(f"output: {output_text}")
        if error_text:
            lines.append(f"error: {error_text}")
        return "\n".join(lines)
