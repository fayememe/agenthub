from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class OpenCodeAuthError(OpenCodeError):
    pass


class OpenCodeNotFound(OpenCodeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionList:
    items: tuple[dict[str, Any], ...]
    previous_cursor: str | None = None
    next_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        return bool(self.next_cursor)


def parse_sse_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    event = payload.get("payload") if isinstance(payload, dict) else None
    if isinstance(event, dict):
        return event
    return payload if isinstance(payload, dict) else None


def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


class OpenCodeClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str = "opencode",
        password: str | None = None,
        auth_header: str | None = None,
        fallback_url: str | None = None,
        timeout: float = 20.0,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.fallback_url = fallback_url.rstrip("/") if fallback_url else None
        self.timeout = timeout
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if auth_header:
            self._headers["Authorization"] = auth_header
        elif password is not None:
            raw = f"{username}:{password}".encode()
            self._headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> OpenCodeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
    ) -> Any:
        bases = [self.base_url]
        if self.fallback_url and self.fallback_url != self.base_url:
            bases.append(self.fallback_url)
        last_error: OpenCodeError | None = None
        for index, base in enumerate(bases):
            request_timeout = aiohttp.ClientTimeout(
                total=self.timeout if timeout is None else timeout
            )
            try:
                session = self._get_session()
                async with session.request(
                    method,
                    base + path,
                    params=_without_none(params or {}),
                    json=body,
                    headers=self._headers,
                    timeout=request_timeout,
                ) as response:
                    text = await response.text()
                    if response.status == 401:
                        raise OpenCodeAuthError("unauthorized", status=401)
                    if response.status in {404, 405}:
                        error: OpenCodeError = (
                            OpenCodeNotFound("not found", status=response.status)
                            if response.status == 404
                            else OpenCodeError("method not allowed", status=405)
                        )
                        if index < len(bases) - 1:
                            last_error = error
                            continue
                        raise error
                    if response.status >= 400:
                        raise OpenCodeError(
                            f"http {response.status}",
                            status=response.status,
                            body=text[:500],
                        )
                    if response.status == 204 or not text.strip():
                        return None
                    if "json" in (response.content_type or "") or text.lstrip().startswith(
                        ("{", "[")
                    ):
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return text
                    return text
            except OpenCodeAuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = OpenCodeError(f"opencode request failed: {exc}")
                if index < len(bases) - 1:
                    continue
                raise last_error from exc
        raise last_error or OpenCodeError("opencode request failed")

    async def health(self) -> dict[str, Any]:
        data = await self._request("GET", "/global/health")
        return data if isinstance(data, dict) else {}

    async def projects(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/project")
        raw = _unwrap(data)
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    async def project_directories(self, project_id: str) -> list[str]:
        data = await self._request(
            "GET", f"/project/{quote(project_id, safe='')}/directories"
        )
        raw = _unwrap(data)
        if not isinstance(raw, list):
            return []
        return [
            str(item["directory"])
            for item in raw
            if isinstance(item, dict) and item.get("directory")
        ]

    async def skills(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/skill")
        raw = _unwrap(data)
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    async def sessions(
        self,
        *,
        directory: str | None = None,
        project: str | None = None,
        subpath: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        order: str = "asc",
        search: str | None = None,
    ) -> SessionList:
        params = _without_none(
            {
                "directory": directory,
                "project": project,
                "subpath": subpath,
                "cursor": cursor,
                "limit": limit,
                "order": order,
                "search": search,
            }
        )
        data = await self._request("GET", "/api/session", params=params)
        if isinstance(data, dict):
            raw = data.get("data")
            cursor_data = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
        else:
            raw = data
            cursor_data = {}
        items = tuple(
            item for item in raw if isinstance(item, dict) and item.get("id")
        ) if isinstance(raw, list) else ()
        return SessionList(
            items=items,
            previous_cursor=cursor_data.get("previous"),
            next_cursor=cursor_data.get("next"),
        )

    async def session(
        self, session_id: str, *, directory: str | None = None
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/session/{quote(session_id, safe='')}",
            params={"directory": directory},
        )
        value = _unwrap(data)
        if not isinstance(value, dict):
            raise OpenCodeError("invalid session response")
        return value

    async def messages(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/session/{quote(session_id, safe='')}/message",
            params={"directory": directory, "limit": limit},
        )
        raw = _unwrap(data)
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    async def message(
        self,
        session_id: str,
        message_id: str,
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/session/{quote(session_id, safe='')}/message/"
            f"{quote(message_id, safe='')}",
            params={"directory": directory},
        )
        value = _unwrap(data)
        if not isinstance(value, dict):
            raise OpenCodeError("invalid message response")
        return value

    async def session_status(self, *, directory: str | None = None) -> dict[str, Any]:
        data = await self._request(
            "GET", "/session/status", params={"directory": directory}
        )
        return data if isinstance(data, dict) else {}

    async def create_session(
        self,
        *,
        directory: str | None = None,
        title: str | None = None,
        parent_id: str | None = None,
        agent: str | None = None,
        model: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        permission: Any = None,
    ) -> dict[str, Any]:
        body = _without_none(
            {
                "parentID": parent_id,
                "title": title,
                "agent": agent,
                "model": dict(model) if model else None,
                "metadata": dict(metadata) if metadata else None,
                "permission": permission,
            }
        )
        data = await self._request(
            "POST",
            "/session",
            params={"directory": directory},
            body=body,
        )
        value = _unwrap(data)
        if not isinstance(value, dict):
            raise OpenCodeError("invalid session create response")
        return value

    async def prompt_async(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        agent: str | None = None,
        model: Mapping[str, Any] | None = None,
        system: str | None = None,
        tools: Mapping[str, bool] | None = None,
        output_format: Mapping[str, Any] | None = None,
    ) -> None:
        body = _without_none(
            {
                "parts": [{"type": "text", "text": text}],
                "agent": agent,
                "model": dict(model) if model else None,
                "system": system,
                "tools": dict(tools) if tools else None,
                "format": dict(output_format) if output_format else None,
            }
        )
        await self._request(
            "POST",
            f"/session/{quote(session_id, safe='')}/prompt_async",
            params={"directory": directory},
            body=body,
            timeout=60,
        )

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        agent: str | None = None,
        model: Mapping[str, Any] | None = None,
        system: str | None = None,
        tools: Mapping[str, bool] | None = None,
        output_format: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        body = _without_none(
            {
                "parts": [{"type": "text", "text": text}],
                "agent": agent,
                "model": dict(model) if model else None,
                "system": system,
                "tools": dict(tools) if tools else None,
                "format": dict(output_format) if output_format else None,
            }
        )
        data = await self._request(
            "POST",
            f"/session/{quote(session_id, safe='')}/message",
            params={"directory": directory},
            body=body,
            timeout=timeout,
        )
        value = _unwrap(data)
        if not isinstance(value, dict):
            raise OpenCodeError("invalid prompt response")
        return value

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        session = self._get_session()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
        headers = {**self._headers, "Accept": "text/event-stream"}
        async with session.get(
            self.base_url + "/global/event", headers=headers, timeout=timeout
        ) as response:
            if response.status == 401:
                raise OpenCodeAuthError("unauthorized", status=401)
            if response.status >= 400:
                text = await response.text()
                raise OpenCodeError(
                    f"http {response.status}", status=response.status, body=text[:500]
                )
            async for raw in response.content:
                line = raw.decode("utf-8", "ignore").strip()
                event = parse_sse_line(line)
                if event is not None:
                    yield event
