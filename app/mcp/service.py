from __future__ import annotations

import asyncio
import posixpath
from contextlib import asynccontextmanager
from typing import Any

from ..config import NodeConfig
from ..oc import OcClient
from .config import McpConfig


def _normalize_directory(value: str) -> str:
    text = str(value).strip()
    if not text.startswith("/"):
        raise ValueError("directory must be an absolute path")
    normalized = posixpath.normpath(text)
    if not normalized.startswith("/"):
        raise ValueError("invalid directory")
    return normalized


def _session_id(value: str) -> str:
    text = str(value).strip()
    if (
        not text
        or len(text) > 200
        or "/" in text
        or "\\" in text
        or text in {".", ".."}
        or any(character.isspace() for character in text)
    ):
        raise ValueError("invalid session id")
    return text


def _under(value: str, root: str) -> bool:
    value = value.rstrip("/") or "/"
    root = root.rstrip("/") or "/"
    if root == "/":
        return True
    return value == root or value.startswith(root + "/")


def _short(value: Any, limit: int = 400) -> Any:
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _scrub_paths(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ("worktree", "directory") if key in value}


def _scrub_session(raw: dict[str, Any]) -> dict[str, Any]:
    item = {
        key: raw.get(key)
        for key in (
            "id",
            "title",
            "slug",
            "agent",
            "model",
            "directory",
            "projectID",
            "parentID",
            "time",
            "cost",
            "tokens",
            "status",
        )
        if key in raw
    }
    if "title" in item:
        item["title"] = _short(item["title"], 300)
    summary = raw.get("summary")
    if isinstance(summary, dict):
        item["summary"] = {
            key: summary.get(key)
            for key in ("additions", "deletions", "files")
            if key in summary
        }
    return item


class McpService:
    """Read-only facade over the hub's opencode clients."""

    def __init__(self, config: McpConfig, oc: OcClient | None = None):
        self.config = config
        self._oc = oc
        self.nodes: dict[str, NodeConfig] = {node.name: node for node in config.hub.nodes}

    @property
    def oc(self) -> OcClient:
        if self._oc is None:
            self._oc = OcClient(self.config.hub)
        return self._oc

    @asynccontextmanager
    async def lifespan(self, _server: Any):
        try:
            yield
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._oc is not None:
            await self._oc.close()
            self._oc = None

    def _node(self, name: str) -> NodeConfig:
        target = self.nodes.get(str(name))
        if target is None:
            raise ValueError(f"unknown node: {name}")
        return target

    def _allowed_roots(self, project: dict[str, Any]) -> list[str]:
        roots = list(project.get("paths") or [])
        if project.get("path") and project.get("kind") != "other":
            roots.insert(0, str(project["path"]))
        if not self.config.expose_projects:
            return roots
        return [
            str(root)
            for root in roots
            if any(_under(str(root), allowed) for allowed in self.config.expose_projects)
        ]

    def _visible_project(self, project: dict[str, Any]) -> dict[str, Any] | None:
        if not self.config.expose_projects:
            return dict(project)
        roots = self._allowed_roots(project)
        if not roots:
            return None
        item = dict(project)
        item["paths"] = roots
        if item.get("kind") != "other":
            path = str(item.get("path") or "")
            if not any(
                _under(path, allowed) for allowed in self.config.expose_projects
            ):
                item["path"] = roots[0]
        return item

    def _visible_projects(self, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for project in projects:
            visible = self._visible_project(project)
            if visible is not None:
                result.append(visible)
        return result

    async def _known_projects(self, node: NodeConfig) -> list[dict[str, Any]]:
        tree = await self.oc.node_tree(node)
        return self._visible_projects(list(tree.get("projects") or []))

    async def _validate_directory(self, node: NodeConfig, directory: str) -> dict[str, Any]:
        normalized = _normalize_directory(directory)
        for project in await self._known_projects(node):
            roots = list(project.get("paths") or [])
            if project.get("kind") != "other" and project.get("path"):
                roots.insert(0, str(project["path"]))
            if any(_under(normalized, str(root)) for root in roots):
                if project.get("kind") == "other":
                    raise ValueError(
                        "directory belongs to the synthetic Other project; "
                        "use list_sessions with project='__other__'"
                    )
                return project
        raise ValueError(f"directory is not an exposed project: {directory}")

    def _matching_project(
        self, projects: list[dict[str, Any]], project: str
    ) -> dict[str, Any] | None:
        wanted = str(project)
        for item in projects:
            if wanted in {
                str(item.get("id") or ""),
                str(item.get("path") or ""),
                str(item.get("b64") or ""),
                str(item.get("name") or ""),
            }:
                return item
        return None

    def _scrub_host(self, data: dict[str, Any]) -> dict[str, Any]:
        config = data.get("config")
        if isinstance(config, dict):
            config.pop("username", None)
        skills = data.get("skills")
        if isinstance(skills, list):
            for skill in skills:
                if isinstance(skill, dict):
                    skill.pop("location", None)
                    skill["description"] = _short(skill.get("description"))
        lsp = data.get("lsp")
        if isinstance(lsp, list):
            for item in lsp:
                if isinstance(item, dict):
                    item.pop("root", None)
        data["paths"] = _scrub_paths(data.get("paths"))
        errors = data.get("errors")
        if isinstance(errors, dict):
            data["errors"] = {key: _short(value) for key, value in errors.items()}
        for key in ("agents", "skills", "mcp", "lsp", "formatters"):
            value = data.get(key)
            if isinstance(value, list) and len(value) > self.config.max_status_items:
                data[key] = value[: self.config.max_status_items]
        return data

    async def list_hosts(self) -> dict[str, Any]:
        """List configured opencode hosts with their online state and version."""
        nodes = list(self.nodes.values())
        health = await asyncio.gather(*(self.oc.health(node) for node in nodes))
        return {
            "hosts": [
                {
                    "name": node.name,
                    "online": bool(item.get("online")),
                    "version": item.get("version"),
                    "error": item.get("error"),
                }
                for node, item in zip(nodes, health)
            ]
        }

    async def get_host_status(
        self, node: str, directory: str | None = None
    ) -> dict[str, Any]:
        """Read provider, agent, skill, MCP, LSP and session status for one host."""
        target = self._node(node)
        if directory:
            directory = _normalize_directory(directory)
            await self._validate_directory(target, directory)
        data = await self.oc.host_status(target, directory)
        return self._scrub_host(data)

    async def list_projects(self, node: str | None = None) -> dict[str, Any]:
        """List projects reported by opencode, optionally for a single host."""
        targets = [self._node(node)] if node else list(self.nodes.values())
        trees = await asyncio.gather(*(self.oc.node_tree(item) for item in targets))
        result = []
        for target, tree in zip(targets, trees):
            projects = []
            for project in self._visible_projects(list(tree.get("projects") or [])):
                if len(projects) >= self.config.max_projects:
                    break
                projects.append(
                    {
                        key: project.get(key)
                        for key in (
                            "id",
                            "name",
                            "path",
                            "paths",
                            "kind",
                            "vcs",
                            "updated",
                            "session_count",
                            "last_session",
                        )
                        if key in project
                    }
                )
            result.append(
                {
                    "node": target.name,
                    "online": bool(tree.get("online")),
                    "version": tree.get("version"),
                    "error": tree.get("error"),
                    "projects": projects,
                }
            )
        return {"nodes": result}

    async def list_sessions(
        self,
        node: str,
        project: str | None = None,
        directory: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        """List sessions for one project or directory on a host."""
        target = self._node(node)
        limit = max(1, min(int(limit), self.config.max_sessions))

        if project:
            projects = await self._known_projects(target)
            matched = self._matching_project(projects, project)
            if matched is None:
                raise ValueError(f"unknown project: {project}")
            sessions = await self.oc.project_sessions(target, matched, limit)
            if self.config.expose_projects:
                roots = matched.get("paths") or [matched.get("path")]
                sessions = [
                    session
                    for session in sessions
                    if not session.get("directory")
                    or any(
                        _under(str(session["directory"]), str(root))
                        for root in roots
                        if root
                    )
                ]
        elif directory:
            directory = _normalize_directory(directory)
            await self._validate_directory(target, directory)
            sessions = await self.oc.sessions(target, directory, limit)
        else:
            raise ValueError("project or directory is required")

        return {
            "node": target.name,
            "project": project,
            "directory": directory,
            "sessions": [_scrub_session(item) for item in sessions],
        }

    async def get_project_status(
        self, node: str, directory: str, include_details: bool = False
    ) -> dict[str, Any]:
        """Read git changes, busy sessions and pending permission/question counts."""
        target = self._node(node)
        directory = _normalize_directory(directory)
        await self._validate_directory(target, directory)
        status = await self.oc.project_status(target, directory)
        status["paths"] = _scrub_paths(status.get("paths"))
        changes = status.get("changes")
        if isinstance(changes, dict):
            items = changes.get("items")
            if isinstance(items, list):
                changes["items"] = [
                    {
                        key: item.get(key)
                        for key in ("file", "additions", "deletions", "status")
                        if key in item
                    }
                    for item in items[: self.config.max_status_items]
                    if isinstance(item, dict)
                ]
        if not include_details:
            status.pop("permissions", None)
            status.pop("questions", None)
        else:
            for key in ("permissions", "questions"):
                value = status.get(key)
                if isinstance(value, list):
                    status[key] = value[: self.config.max_status_items]
        return {"node": target.name, "directory": directory, **status}

    async def get_session_meta(
        self, node: str, session_id: str, directory: str | None = None
    ) -> dict[str, Any]:
        """Read metadata for a single session without message bodies."""
        target = self._node(node)
        session_id = _session_id(session_id)
        if directory:
            directory = _normalize_directory(directory)
            await self._validate_directory(target, directory)
        raw = await self.oc.session_info(target, session_id, directory)
        return {"node": target.name, "session": _scrub_session(raw)}
