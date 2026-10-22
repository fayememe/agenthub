from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

import yaml

from ..config import HubConfig, default_config_path, load_config

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class McpConfig:
    hub: HubConfig
    host: str = "127.0.0.1"
    port: int = 8680
    path: str = "/mcp"
    public_url: str | None = None
    auth_mode: str = "bearer"
    token: str = ""
    token_env: str = "AGENTHUB_MCP_TOKEN"
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    expose_nodes: tuple[str, ...] = ()
    expose_projects: tuple[str, ...] = ()
    max_sessions: int = 100
    max_projects: int = 200
    max_status_items: int = 100
    rate_limit_per_min: int = 60


def _list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("mcp list values must be strings or lists")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _prefixes(value: object) -> tuple[str, ...]:
    items = _list(value)
    result = []
    for item in items:
        if not item.startswith("/"):
            raise ValueError("mcp.expose_projects must contain absolute paths")
        result.append(posixpath.normpath(item))
    return tuple(dict.fromkeys(result))


def _positive_int(value: object, name: str, default: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"mcp.{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"mcp.{name} must be positive")
    return parsed


def _config_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    env = os.environ.get("AGENTHUB_MCP_CONF") or os.environ.get("AGENTHUB_CONF")
    return Path(env).expanduser() if env else default_config_path()


def load_mcp_config(path: str | Path | None = None) -> McpConfig:
    config_path = _config_path(path)
    hub = load_config(config_path)
    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    section = raw.get("mcp") or {}
    if not isinstance(section, dict):
        raise ValueError("mcp must be a mapping")

    auth = section.get("auth") or {}
    if isinstance(auth, str):
        auth = {"mode": auth}
    if not isinstance(auth, dict):
        raise ValueError("mcp.auth must be a mapping or mode string")

    host = str(os.environ.get("AGENTHUB_MCP_HOST") or section.get("host") or "127.0.0.1")
    port = _positive_int(
        os.environ.get("AGENTHUB_MCP_PORT") or section.get("port"), "port", 8680
    )
    path_value = str(section.get("path") or "/mcp")
    if not path_value.startswith("/"):
        raise ValueError("mcp.path must start with /")
    path_value = "/" + path_value.strip("/") if path_value != "/" else "/"

    public_url = str(section.get("public_url") or "").strip().rstrip("/") or None
    if public_url and urlparse(public_url).scheme not in {"http", "https"}:
        raise ValueError("mcp.public_url must be an http(s) URL")

    auth_mode = str(
        os.environ.get("AGENTHUB_MCP_AUTH") or auth.get("mode") or "bearer"
    ).lower()
    if auth_mode not in {"bearer", "none"}:
        raise ValueError("mcp.auth.mode must be bearer or none")

    token_env = str(auth.get("token_env") or "AGENTHUB_MCP_TOKEN")
    token = (
        os.environ.get("AGENTHUB_MCP_TOKEN")
        or os.environ.get(token_env)
        or str(auth.get("token") or "")
    ).strip()
    if auth_mode == "bearer" and not token:
        raise ValueError(
            f"mcp auth is enabled but no token was found; set {token_env}"
        )
    if auth_mode == "none" and host not in LOOPBACK_HOSTS:
        raise ValueError("mcp.auth.mode=none is only allowed on a loopback host")

    expose_nodes = _list(section.get("expose_nodes"))
    if expose_nodes:
        names = {node.name for node in hub.nodes}
        missing = sorted(set(expose_nodes) - names)
        if missing:
            raise ValueError(f"mcp.expose_nodes contains unknown node(s): {', '.join(missing)}")
        hub = replace(hub, nodes=tuple(node for node in hub.nodes if node.name in expose_nodes))

    allowed_hosts = _list(section.get("allowed_hosts"))
    if not allowed_hosts:
        allowed_hosts = (
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        )
    if public_url:
        allowed_hosts += (urlparse(public_url).netloc,)

    allowed_origins = _list(section.get("allowed_origins"))
    if not allowed_origins and public_url:
        parsed = urlparse(public_url)
        allowed_origins = (f"{parsed.scheme}://{parsed.netloc}",)

    return McpConfig(
        hub=hub,
        host=host,
        port=port,
        path=path_value,
        public_url=public_url,
        auth_mode=auth_mode,
        token=token,
        token_env=token_env,
        allowed_hosts=tuple(dict.fromkeys(allowed_hosts)),
        allowed_origins=tuple(dict.fromkeys(allowed_origins)),
        expose_nodes=expose_nodes,
        expose_projects=_prefixes(section.get("expose_projects")),
        max_sessions=_positive_int(section.get("max_sessions"), "max_sessions", 100),
        max_projects=_positive_int(section.get("max_projects"), "max_projects", 200),
        max_status_items=_positive_int(
            section.get("max_status_items"), "max_status_items", 100
        ),
        rate_limit_per_min=_positive_int(
            section.get("rate_limit_per_min"), "rate_limit_per_min", 60
        ),
    )
