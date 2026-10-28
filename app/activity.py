from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import aiohttp

from .config import HubConfig, NodeConfig


def _session_id(event: dict[str, Any]) -> str | None:
    properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
    data_info = data.get("info") if isinstance(data.get("info"), dict) else {}
    value = (
        properties.get("sessionID")
        or data.get("sessionID")
        or event.get("sessionID")
        or info.get("id")
        or data_info.get("id")
    )
    return str(value) if value else None


def _status_type(event: dict[str, Any]) -> str:
    properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    status = properties.get("status") or data.get("status")
    if isinstance(status, dict):
        status = status.get("type") or status.get("status")
    return str(status or "").lower()


def _request_id(event: dict[str, Any]) -> str | None:
    properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    value = (
        properties.get("id")
        or data.get("id")
        or properties.get("requestID")
        or data.get("requestID")
        or properties.get("permissionID")
    )
    return str(value) if value else None


class ActivityStore:
    def __init__(self, config: HubConfig, oc: Any):
        self.config = config
        self.oc = oc
        self.sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self.permission_items: dict[tuple[str, str], dict[str, Any]] = {}
        self.question_items: dict[tuple[str, str], dict[str, Any]] = {}
        self.active_sources: dict[tuple[str, str], set[str]] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []
        self.closed = False

    def snapshot(self, node_name: str, session_id: str) -> dict[str, Any]:
        state = self.sessions.get((node_name, session_id))
        if not state:
            return {"busy": False, "done": False, "error": False, "permissions": 0, "questions": 0}
        return {
            "busy": bool(state["busy"]),
            "done": bool(state["done"]),
            "error": bool(state["error"]),
            "permissions": len(state["permissions"]),
            "questions": len(state["questions"]),
        }

    def decorate(self, node_name: str, sessions: list[dict[str, Any]]) -> None:
        for session in sessions:
            session_id = str(session.get("id") or "")
            if session_id:
                session["activity"] = self.snapshot(node_name, session_id)

    def mark_read(self, node_name: str, session_ids: set[str]) -> None:
        changed = False
        for session_id in session_ids:
            state = self.sessions.get((node_name, session_id))
            if state and (state["done"] or state["error"]):
                state["done"] = False
                state["error"] = False
                state["error_message"] = None
                changed = True
        if changed:
            self.broadcast()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def broadcast(self) -> None:
        event = {"type": "activity", "at": int(time.time() * 1000)}
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _state(self, node_name: str, session_id: str) -> dict[str, Any]:
        key = (node_name, session_id)
        state = self.sessions.get(key)
        if state is None:
            state = {
                "busy": False,
                "done": False,
                "error": False,
                "error_message": None,
                "permissions": set(),
                "questions": set(),
                "ever_busy": False,
                "updated": 0,
            }
            self.sessions[key] = state
        return state

    def handle_event(self, node: NodeConfig, event: dict[str, Any], source: str | None = None) -> None:
        event_type = str(event.get("type") or "")
        session_id = _session_id(event)
        if event_type in {"session.created", "session.deleted"}:
            if self.oc is not None:
                self.oc.invalidate_session_index(node.name)
        if event_type == "session.deleted":
            if session_id:
                self.sessions.pop((node.name, session_id), None)
                self.broadcast()
            return
        if event_type == "session.created":
            self.broadcast()
            return
        if not session_id:
            return

        state = self._state(node.name, session_id)
        changed = False

        if event_type in {"session.execution.started"}:
            state.update({"busy": True, "done": False, "error": False, "error_message": None, "ever_busy": True, "updated": int(time.time() * 1000)})
            changed = True
        elif event_type == "session.status":
            status = _status_type(event)
            if status in {"busy", "running", "retry"}:
                state.update({"busy": True, "done": False, "error": False, "ever_busy": True})
                changed = True
            elif status in {"idle", "done", "completed"}:
                if state["busy"] or state["ever_busy"]:
                    state["busy"] = False
                    state["ever_busy"] = False
                    if not state["error"]:
                        state["done"] = True
                    changed = True
        elif event_type in {"session.execution.succeeded"}:
            state.update({"busy": False, "done": True, "error": False, "error_message": None, "ever_busy": False, "updated": int(time.time() * 1000)})
            changed = True
        elif event_type == "session.idle":
            if state["busy"] or state["ever_busy"]:
                state["busy"] = False
                state["ever_busy"] = False
                if not state["error"]:
                    state["done"] = True
                changed = True
        elif event_type in {"session.execution.failed"}:
            state.update({"busy": False, "done": False, "error": True, "ever_busy": False, "updated": int(time.time() * 1000)})
            changed = True
        elif event_type in {"session.execution.interrupted"}:
            if state["busy"] or state["ever_busy"]:
                state["busy"] = False
                state["ever_busy"] = False
                changed = True
        elif event_type == "session.error":
            properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            state.update(
                {
                    "busy": False,
                    "done": False,
                    "error": True,
                    "ever_busy": False,
                    "error_message": properties.get("error") or data.get("error"),
                    "updated": int(time.time() * 1000),
                }
            )
            changed = True
        elif event_type in {"permission.asked", "permission.v2.asked"}:
            request_id = _request_id(event)
            if request_id and request_id not in state["permissions"]:
                properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                self.permission_items[(node.name, request_id)] = {
                    "id": request_id,
                    "sessionID": session_id,
                    "source": source,
                    "permission": properties.get("permission") or data.get("action") or data.get("permission"),
                    "patterns": properties.get("patterns") or data.get("resources") or [],
                    "always": properties.get("always") or data.get("save") or [],
                    "metadata": properties.get("metadata") or data.get("metadata") or {},
                    "tool": properties.get("tool") or data.get("source") or {},
                    "updated": int(time.time() * 1000),
                }
                state["permissions"].add(request_id)
                changed = True
        elif event_type in {"permission.replied", "permission.v2.replied"}:
            request_id = _request_id(event)
            if request_id and request_id in state["permissions"]:
                state["permissions"].discard(request_id)
                self.permission_items.pop((node.name, request_id), None)
                changed = True
        elif event_type in {"question.asked", "question.v2.asked"}:
            request_id = _request_id(event)
            if request_id and request_id not in state["questions"]:
                properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                self.question_items[(node.name, request_id)] = {
                    "id": request_id,
                    "sessionID": session_id,
                    "source": source,
                    "questions": properties.get("questions") or data.get("questions") or [],
                    "tool": properties.get("tool") or data.get("tool") or {},
                    "updated": int(time.time() * 1000),
                }
                state["questions"].add(request_id)
                changed = True
        elif event_type in {"question.replied", "question.rejected", "question.v2.replied", "question.v2.rejected"}:
            request_id = _request_id(event)
            if request_id and request_id in state["questions"]:
                state["questions"].discard(request_id)
                self.question_items.pop((node.name, request_id), None)
                changed = True

        if changed:
            self.broadcast()

    def resolve_permission(self, node_name: str, request_id: str) -> None:
        item = self.permission_items.pop((node_name, request_id), None)
        if item:
            state = self.sessions.get((node_name, item.get("sessionID")))
            if state:
                state["permissions"].discard(request_id)
            self.broadcast()

    def resolve_question(self, node_name: str, request_id: str) -> None:
        item = self.question_items.pop((node_name, request_id), None)
        if item:
            state = self.sessions.get((node_name, item.get("sessionID")))
            if state:
                state["questions"].discard(request_id)
            self.broadcast()

    async def start(self) -> None:
        for node in self.config.nodes:
            bases: list[str] = []
            for base in (node.api_url, node.web_url):
                if base and base not in bases:
                    bases.append(base)
            for base in bases:
                self.tasks.append(
                    asyncio.create_task(
                        self._event_loop(node, base),
                        name=f"agenthub-events-{node.name}-{base}",
                    )
                )

    async def stop(self) -> None:
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def reload(self, config: HubConfig) -> None:
        await self.stop()
        self.active_sources.clear()
        self.config = config
        self.closed = False
        self.tasks = []
        await self.start()

    async def _reconcile(self, node: NodeConfig, base: str) -> None:
        projects: list[dict[str, Any]] = []
        try:
            data = await self.oc._get_url(node, base, "/project")
            if isinstance(data, list):
                projects = [
                    {"id": item.get("id"), "path": item.get("worktree")}
                    for item in data
                    if isinstance(item, dict)
                    and item.get("id") != "global"
                    and item.get("worktree")
                    and item.get("worktree") != "/"
                ]
        except Exception:
            projects = []

        async def fetch(project: dict[str, Any]) -> tuple[Any, Any]:
            directory = project.get("path")
            if not directory:
                return [], []
            try:
                permissions, questions = await asyncio.gather(
                    self.oc._get_url(node, base, "/permission", {"directory": directory}),
                    self.oc._get_url(node, base, "/question", {"directory": directory}),
                )
                return permissions, questions
            except Exception:
                return [], []

        results = await asyncio.gather(*(fetch(project) for project in projects))

        for store in (self.permission_items, self.question_items):
            for key, item in list(store.items()):
                if key[0] == node.name and item.get("source") == base:
                    store.pop(key, None)

        for permissions, questions in results:
            for item in permissions if isinstance(permissions, list) else []:
                session_id = str(item.get("sessionID") or "")
                request_id = str(item.get("id") or "")
                if session_id and request_id:
                    self.permission_items[(node.name, request_id)] = {
                        "id": request_id,
                        "sessionID": session_id,
                        "source": base,
                        "permission": item.get("permission"),
                        "patterns": item.get("patterns") or [],
                        "always": item.get("always") or [],
                        "metadata": item.get("metadata") or {},
                        "tool": item.get("tool") or {},
                        "updated": int(time.time() * 1000),
                    }
            for item in questions if isinstance(questions, list) else []:
                session_id = str(item.get("sessionID") or "")
                request_id = str(item.get("id") or "")
                if session_id and request_id:
                    self.question_items[(node.name, request_id)] = {
                        "id": request_id,
                        "sessionID": session_id,
                        "source": base,
                        "questions": item.get("questions") or [],
                        "tool": item.get("tool") or {},
                        "updated": int(time.time() * 1000),
                    }

        permissions_by_session: dict[str, set[str]] = {}
        for (node_name, request_id), item in self.permission_items.items():
            if node_name == node.name:
                permissions_by_session.setdefault(str(item.get("sessionID")), set()).add(request_id)
        questions_by_session: dict[str, set[str]] = {}
        for (node_name, request_id), item in self.question_items.items():
            if node_name == node.name:
                questions_by_session.setdefault(str(item.get("sessionID")), set()).add(request_id)
        for key, state in self.sessions.items():
            if key[0] == node.name:
                state["permissions"] = set(permissions_by_session.get(key[1], set()))
                state["questions"] = set(questions_by_session.get(key[1], set()))
        for session_id, ids in permissions_by_session.items():
            self._state(node.name, session_id)["permissions"] = ids
        for session_id, ids in questions_by_session.items():
            self._state(node.name, session_id)["questions"] = ids

        try:
            active = await self.oc._get_url(node, base, "/api/session/active")
            active_data = active.get("data") if isinstance(active, dict) else {}
            if isinstance(active_data, dict):
                self.active_sources[(node.name, base)] = {str(key) for key in active_data.keys()}
        except Exception:
            self.active_sources[(node.name, base)] = set()

        active_ids: set[str] = set()
        for (node_name, _base), ids in self.active_sources.items():
            if node_name == node.name:
                active_ids.update(ids)
        for key, state in self.sessions.items():
            if key[0] == node.name:
                state["busy"] = key[1] in active_ids
                if state["busy"]:
                    state["ever_busy"] = True
        for session_id in active_ids:
            state = self._state(node.name, session_id)
            state["busy"] = True
            state["ever_busy"] = True

        self.broadcast()

    async def _event_loop(self, node: NodeConfig, base: str) -> None:
        backoff = 1
        while not self.closed:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
                async with self.oc.session.get(
                    base + "/global/event",
                    headers={"Authorization": node.auth_header, "Accept": "text/event-stream"},
                    timeout=timeout,
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(f"event stream status {response.status}")
                    backoff = 1
                    try:
                        await self._reconcile(node, base)
                    except Exception:
                        pass
                    self.broadcast()
                    async for raw in response.content:
                        if self.closed:
                            return
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        match = re.search(r'"type"\s*:\s*"([^"]+)"', line)
                        if match and match.group(1).startswith(
                            ("message.", "session.next.", "file.", "lsp.", "todo.", "pty.", "installation.")
                        ):
                            continue
                        try:
                            payload = json.loads(line[5:].strip())
                        except Exception:
                            continue
                        event = payload.get("payload") if isinstance(payload, dict) else None
                        if not isinstance(event, dict):
                            event = payload
                        self.handle_event(node, event, source=base)
            except asyncio.CancelledError:
                return
            except Exception:
                if self.closed:
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
