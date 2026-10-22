from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from .config import HubConfig, NodeConfig


def b64url(value: str) -> str:
    encoded = base64.urlsafe_b64encode(value.encode()).decode()
    return encoded.rstrip("=")


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "unreachable"
    if isinstance(exc, aiohttp.ClientResponseError):
        return f"http {exc.status}"
    if isinstance(exc, aiohttp.ClientError):
        return str(exc)[:160]
    return (str(exc) or exc.__class__.__name__)[:160]


def _normalize_status(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("type") or value.get("status") or value
    return value


def is_busy(value: Any) -> bool:
    text = str(_normalize_status(value) or "").lower()
    return any(word in text for word in ("busy", "running", "working"))


def _pick(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in keys if key in value}


def _safe_paths(value: Any) -> dict[str, Any]:
    return _pick(value, ("home", "state", "config", "worktree", "directory"))


def _permission_items(value: Any, limit: int = 50) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in value[:limit] if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        items.append(
            {
                "id": raw.get("id"),
                "sessionID": raw.get("sessionID"),
                "permission": raw.get("permission"),
                "patterns": raw.get("patterns") or [],
            }
        )
    return items


def _question_items(value: Any, limit: int = 50) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in value[:limit] if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        questions = []
        for question in raw.get("questions") or []:
            if isinstance(question, dict):
                questions.append(
                    {
                        "header": question.get("header"),
                        "question": question.get("question"),
                        "options": [
                            option.get("label")
                            for option in question.get("options") or []
                            if isinstance(option, dict) and option.get("label")
                        ],
                    }
                )
        items.append(
            {
                "id": raw.get("id"),
                "sessionID": raw.get("sessionID"),
                "questions": questions,
            }
        )
    return items


class OcClient:
    def __init__(self, config: HubConfig):
        self.config = config
        self.session = aiohttp.ClientSession()
        self._index_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._index_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self.session.close()

    async def _get_url(
        self,
        node: NodeConfig,
        base: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_s)
        async with self.session.get(
            base + path,
            params=params,
            headers={"Authorization": node.auth_header},
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise PermissionError("unauthorized")
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _get(self, node: NodeConfig, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._get_url(node, node.api_url, path, params)

    async def _get_web(self, node: NodeConfig, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._get_url(node, node.web_url, path, params)

    async def health(self, node: NodeConfig) -> dict[str, Any]:
        try:
            data = await self._get(node, "/global/health")
            return {
                "online": bool(data.get("healthy", True)),
                "version": data.get("version"),
                "error": None,
            }
        except Exception as exc:
            return {"online": False, "version": None, "error": _error_text(exc)}

    async def host_status(self, node: NodeConfig, directory: str | None = None) -> dict[str, Any]:
        params = {"directory": directory} if directory else None
        results = await asyncio.gather(
            self.health(node),
            self._get(node, "/config", params),
            self._get(node, "/provider", params),
            self._get(node, "/agent", params),
            self._get(node, "/skill", params),
            self._get(node, "/mcp", params),
            self._get(node, "/lsp", params),
            self._get(node, "/formatter", params),
            self._get(node, "/path", params),
            self._get(node, "/session/status", params),
            self._get(node, "/permission", params),
            self._get(node, "/question", params),
            self._get(node, "/project"),
            return_exceptions=True,
        )
        (
            health,
            config,
            providers,
            agents,
            skills,
            mcp,
            lsp,
            formatters,
            paths,
            statuses,
            permissions,
            questions,
            projects,
        ) = results

        errors: dict[str, str] = {}

        def read(value: Any, name: str) -> Any:
            if isinstance(value, BaseException):
                errors[name] = _error_text(value)
                return None
            return value

        health = read(health, "health")
        if not isinstance(health, dict):
            health = {"online": False, "version": None, "error": errors.get("health")}

        config_data = read(config, "config")
        config_summary = _pick(
            config_data,
            ("model", "small_model", "username", "share", "autoupdate", "theme"),
        )
        if isinstance(config_data, dict):
            config_summary["agents"] = sorted((config_data.get("agent") or {}).keys())
            config_summary["modes"] = sorted((config_data.get("mode") or {}).keys())

        provider_data = read(providers, "providers")
        provider_summary = {}
        if isinstance(provider_data, dict):
            connected = provider_data.get("connected")
            provider_summary = {
                "connected": sorted(connected) if isinstance(connected, list) else [],
                "total": len(provider_data.get("all") or []),
            }

        agent_items = []
        for raw in read(agents, "agents") or []:
            if not isinstance(raw, dict):
                continue
            model = raw.get("model")
            agent_items.append(
                {
                    "name": raw.get("name"),
                    "description": raw.get("description"),
                    "mode": raw.get("mode"),
                    "hidden": bool(raw.get("hidden")),
                    "model": (
                        f"{model.get('providerID')}/{model.get('modelID')}"
                        if isinstance(model, dict) and model.get("modelID")
                        else None
                    ),
                }
            )

        skill_items = []
        for raw in read(skills, "skills") or []:
            if isinstance(raw, dict):
                skill_items.append(_pick(raw, ("name", "description", "location")))

        mcp_items = []
        mcp_data = read(mcp, "mcp")
        if isinstance(mcp_data, dict):
            for name, value in sorted(mcp_data.items()):
                status = value.get("status") if isinstance(value, dict) else str(value)
                error = value.get("error") if isinstance(value, dict) else None
                mcp_items.append({"name": name, "status": status, "error": error})

        lsp_items = [
            _pick(raw, ("id", "name", "root", "status"))
            for raw in read(lsp, "lsp") or []
            if isinstance(raw, dict)
        ]
        formatter_items = [
            _pick(raw, ("name", "extensions", "enabled"))
            for raw in read(formatters, "formatters") or []
            if isinstance(raw, dict)
        ]

        status_data = read(statuses, "sessions") or {}
        status_values = list(status_data.values()) if isinstance(status_data, dict) else []
        session_summary = {
            "total": len(status_values),
            "busy": sum(1 for value in status_values if is_busy(value)),
        }

        permission_data = read(permissions, "permissions")
        question_data = read(questions, "questions")
        project_data = read(projects, "projects")

        return {
            "node": node.name,
            "directory": directory,
            "health": health,
            "config": config_summary,
            "providers": provider_summary,
            "agents": agent_items,
            "skills": skill_items,
            "mcp": mcp_items,
            "lsp": lsp_items,
            "formatters": formatter_items,
            "paths": _safe_paths(read(paths, "paths")),
            "sessions": session_summary,
            "pending_permissions": len(permission_data) if isinstance(permission_data, list) else 0,
            "pending_questions": len(question_data) if isinstance(question_data, list) else 0,
            "projects": len(project_data) if isinstance(project_data, list) else 0,
            "errors": errors,
            "updated_at": int(time.time() * 1000),
        }

    async def session_index(self, node: NodeConfig, max_age: float = 8.0, limit: int = 5000) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._index_cache.get(node.name)
        if cached and now - cached[0] < max_age:
            return cached[1]

        lock = self._index_locks.setdefault(node.name, asyncio.Lock())
        async with lock:
            cached = self._index_cache.get(node.name)
            if cached and time.monotonic() - cached[0] < max_age:
                return cached[1]
            data = await self._get_web(node, "/api/session", {"limit": limit, "order": "desc"})
            raw = data.get("data") if isinstance(data, dict) else data
            sessions: list[dict[str, Any]] = []
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                location = item.get("location") if isinstance(item.get("location"), dict) else {}
                sessions.append(
                    {
                        "id": item.get("id"),
                        "projectID": item.get("projectID"),
                        "directory": item.get("directory") or location.get("directory"),
                        "subpath": item.get("subpath"),
                        "title": item.get("title") or item.get("id"),
                        "slug": item.get("slug"),
                        "agent": item.get("agent"),
                        "model": item.get("model"),
                        "updated": (item.get("time") or {}).get("updated"),
                        "cost": item.get("cost"),
                        "tokens": item.get("tokens"),
                        "summary": item.get("summary") or {},
                    }
                )
            self._index_cache[node.name] = (time.monotonic(), sessions)
            return sessions

    async def project_sessions(
        self,
        node: NodeConfig,
        project: dict[str, Any],
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        path = str(project.get("path") or "")
        synthetic = project.get("kind") == "other" or str(project.get("id") or "").startswith("global:")
        try:
            index = await self.session_index(node)
        except Exception:
            return await self.sessions(node, path, limit)

        root = path.rstrip("/")
        allowed = set(project.get("paths") or [])
        picked: list[dict[str, Any]] = []
        for session in index:
            directory = str(session.get("directory") or "")
            project_id = session.get("projectID")
            if synthetic:
                if (allowed and directory in allowed) or (not allowed and directory == path):
                    picked.append(session)
            elif project_id == project.get("id"):
                picked.append(session)
            elif project_id == "global" and (directory == path or (root and directory.startswith(root + "/"))):
                picked.append(session)

        picked.sort(key=lambda item: item.get("updated") or 0, reverse=True)
        picked = picked[: max(1, limit)]

        statuses: dict[str, Any] = {}
        try:
            raw_statuses = await self._get(node, "/session/status", {"directory": path})
            if isinstance(raw_statuses, dict):
                statuses = raw_statuses
        except Exception:
            statuses = {}

        result = []
        for session in picked:
            item = dict(session)
            item["status"] = _normalize_status(statuses.get(session["id"]))
            result.append(item)
        return result

    async def _project_path(self, node: NodeConfig, raw: dict[str, Any]) -> tuple[str, list[str]]:
        path = str(raw.get("worktree") or "")
        sandboxes = [str(item) for item in raw.get("sandboxes") or [] if item]
        if not sandboxes or not raw.get("id"):
            return path, [path] if path else []

        try:
            directories = await self._get(node, f"/project/{quote(str(raw['id']))}/directories")
        except Exception:
            directories = []
        paths = [
            str(item.get("directory"))
            for item in directories
            if isinstance(item, dict) and item.get("directory")
        ]
        if not paths:
            paths = sandboxes
        preferred = paths[0] if paths else path
        return preferred, paths

    async def projects(self, node: NodeConfig) -> list[dict[str, Any]]:
        data = await self._get(node, "/project")
        projects: list[dict[str, Any]] = []
        for raw in data if isinstance(data, list) else []:
            if raw.get("id") == "global":
                continue
            path, paths = await self._project_path(node, raw)
            if not path or path == "/":
                continue
            projects.append(
                {
                    "id": raw.get("id"),
                    "path": path,
                    "paths": paths,
                    "name": str(raw.get("name") or "").strip() or (Path(path).name or path),
                    "vcs": raw.get("vcs"),
                    "updated": (raw.get("time") or {}).get("updated"),
                    "b64": b64url(path),
                }
            )
        projects.sort(key=lambda item: item["updated"] or 0, reverse=True)
        return projects

    async def project_status(self, node: NodeConfig, directory: str) -> dict[str, Any]:
        vcs, permissions, questions, statuses, path_info = await asyncio.gather(
            self._get(node, "/vcs/status", {"directory": directory}),
            self._get(node, "/permission", {"directory": directory}),
            self._get(node, "/question", {"directory": directory}),
            self._get(node, "/session/status", {"directory": directory}),
            self._get(node, "/path", {"directory": directory}),
            return_exceptions=True,
        )
        changes = None
        if isinstance(vcs, list):
            additions = sum(int(item.get("additions") or 0) for item in vcs if isinstance(item, dict))
            deletions = sum(int(item.get("deletions") or 0) for item in vcs if isinstance(item, dict))
            changes = {
                "files": len(vcs),
                "additions": additions,
                "deletions": deletions,
                "items": vcs[:200],
            }
        busy = 0
        total_sessions = 0
        if isinstance(statuses, dict):
            total_sessions = len(statuses)
            busy = sum(1 for value in statuses.values() if is_busy(value))
        return {
            "changes": changes,
            "busy_sessions": busy,
            "total_sessions": total_sessions,
            "pending_permissions": len(permissions) if isinstance(permissions, list) else 0,
            "pending_questions": len(questions) if isinstance(questions, list) else 0,
            "permissions": _permission_items(permissions),
            "questions": _question_items(questions),
            "paths": _safe_paths(path_info),
            "updated_at": int(time.time() * 1000),
        }

    async def sessions(self, node: NodeConfig, directory: str, limit: int = 30) -> list[dict[str, Any]]:
        data = await self._get(node, "/session", {"directory": directory, "limit": limit})
        statuses: Any = {}
        try:
            statuses = await self._get(node, "/session/status", {"directory": directory})
        except Exception:
            statuses = {}
        if not isinstance(statuses, dict):
            statuses = {}

        sessions: list[dict[str, Any]] = []
        for raw in data if isinstance(data, list) else []:
            session_id = raw.get("id")
            summary = raw.get("summary") or {}
            sessions.append(
                {
                    "id": session_id,
                    "title": raw.get("title") or raw.get("slug") or session_id,
                    "slug": raw.get("slug"),
                    "agent": raw.get("agent"),
                    "model": raw.get("model"),
                    "updated": (raw.get("time") or {}).get("updated"),
                    "cost": raw.get("cost"),
                    "tokens": raw.get("tokens"),
                    "summary": summary,
                    "status": _normalize_status(statuses.get(session_id)),
                }
            )
        return sessions

    async def session_info(
        self,
        node: NodeConfig,
        session_id: str,
        directory: str | None = None,
    ) -> dict[str, Any]:
        params = {"directory": directory} if directory else None
        try:
            data = await self._get(
                node, f"/session/{quote(str(session_id), safe='')}", params
            )
            raw = data.get("data") if isinstance(data, dict) and "data" in data else data
        except Exception:
            raw = next(
                (
                    item
                    for item in await self.session_index(node)
                    if item.get("id") == session_id
                ),
                None,
            )
        if not isinstance(raw, dict):
            raise ValueError("session not found")
        statuses: Any = {}
        try:
            statuses = await self._get(node, "/session/status", params)
        except Exception:
            statuses = {}
        if not isinstance(statuses, dict):
            statuses = {}
        result = dict(raw)
        result["status"] = _normalize_status(statuses.get(session_id))
        return result

    async def node_tree(self, node: NodeConfig) -> dict[str, Any]:
        health, projects, index = await asyncio.gather(
            self.health(node),
            self.projects(node),
            self.session_index(node),
            return_exceptions=True,
        )

        if isinstance(health, BaseException):
            health = {"online": False, "version": None, "error": _error_text(health)}
        if isinstance(projects, BaseException):
            error = _error_text(projects)
            projects = []
            if not health["error"]:
                health["error"] = error
        if isinstance(index, BaseException):
            index = []
        if health["error"] == "unauthorized":
            health["error"] = "unauthorized (check password env)"

        by_id = {str(project.get("id")): project for project in projects if project.get("id")}
        for project in projects:
            project["kind"] = "project"
            project["session_count"] = 0
            project["last_session"] = 0

        other_dirs: set[str] = set()
        other_count = 0
        other_last = 0
        for session in index:
            directory = str(session.get("directory") or "")
            project_id = session.get("projectID")
            target = by_id.get(str(project_id)) if project_id else None

            if target is None and directory:
                containing = [
                    project
                    for project in projects
                    if directory == project["path"]
                    or directory.startswith(project["path"].rstrip("/") + "/")
                ]
                if containing:
                    target = max(containing, key=lambda item: len(item["path"]))

            if target is None:
                if directory:
                    other_dirs.add(directory)
                    other_count += 1
                    other_last = max(other_last, session.get("updated") or 0)
                continue

            target["session_count"] += 1
            updated = session.get("updated") or 0
            target["last_session"] = max(target["last_session"], updated)

        if other_count:
            projects.append(
                {
                    "id": "global:other",
                    "path": "__other__",
                    "name": "Other",
                    "vcs": None,
                    "updated": other_last,
                    "b64": "other",
                    "kind": "other",
                    "session_count": other_count,
                    "last_session": other_last,
                    "paths": sorted(other_dirs),
                }
            )

        projects.sort(
            key=lambda item: (
                0 if item.get("kind") == "project" else 1,
                -(item.get("last_session") or item.get("updated") or 0),
            )
        )

        return {
            "name": node.name,
            "online": bool(health["online"]),
            "version": health["version"],
            "error": health["error"],
            "proxy_port": node.proxy_port,
            "projects": projects,
        }

    async def tree(self) -> dict[str, Any]:
        nodes = await asyncio.gather(*(self.node_tree(node) for node in self.config.nodes))
        return {
            "nodes": list(nodes),
            "generated_at": int(time.time() * 1000),
        }
