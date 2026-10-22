from __future__ import annotations

import base64
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class NodeConfig:
    name: str
    api_url: str
    web_url: str
    username: str = "opencode"
    password_env: str = "OPENCODE_SERVER_PASSWORD"
    password: str | None = None
    proxy_port: int | None = None

    @property
    def secret(self) -> str:
        return self.password or os.environ.get(self.password_env, "")

    @property
    def auth_header(self) -> str:
        raw = f"{self.username}:{self.secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()


@dataclass(frozen=True)
class HubConfig:
    host: str
    port: int
    proxy_base_port: int
    name: str
    refresh_interval_s: int
    request_timeout_s: float
    state_db: str
    nodes: tuple[NodeConfig, ...]

    @property
    def proxy_enabled(self) -> bool:
        return os.environ.get("AGENTHUB_PROXY", "1").lower() not in {"0", "false", "no"}


def default_config_path() -> Path:
    env = os.environ.get("AGENTHUB_CONF")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "agenthub" / "config.yaml"


def load_config(path: str | Path | None = None) -> HubConfig:
    config_path = Path(path).expanduser() if path else default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"agenthub config not found: {config_path}")

    data = yaml.safe_load(config_path.read_text()) or {}
    hub = data.get("hub") or {}
    nodes_data = data.get("nodes") or []
    if not nodes_data:
        raise ValueError("config.nodes must contain at least one node")

    base_port = int(hub.get("proxy_base_port", 8671))
    names: set[str] = set()
    ports: set[int] = set()
    nodes: list[NodeConfig] = []

    for index, raw in enumerate(nodes_data):
        if not isinstance(raw, dict):
            raise ValueError("each node must be a mapping")
        name = str(raw.get("name") or "").strip()
        if not name or name in names:
            raise ValueError(f"invalid or duplicate node name: {name!r}")
        if "api_url" not in raw or "web_url" not in raw:
            raise ValueError(f"node {name!r} requires api_url and web_url")
        port = int(raw.get("proxy_port") or base_port + index)
        if port in ports:
            raise ValueError(f"duplicate proxy port: {port}")
        names.add(name)
        ports.add(port)
        nodes.append(
            NodeConfig(
                name=name,
                api_url=str(raw["api_url"]).rstrip("/"),
                web_url=str(raw["web_url"]).rstrip("/"),
                username=str(raw.get("username", "opencode")),
                password_env=str(raw.get("password_env", "OPENCODE_SERVER_PASSWORD")),
                password=raw.get("password"),
                proxy_port=port,
            )
        )

    return HubConfig(
        host=str(hub.get("host", "0.0.0.0")),
        port=int(hub.get("port", 8670)),
        proxy_base_port=base_port,
        name=str(hub.get("name", "homelab")),
        refresh_interval_s=int(data.get("refresh_interval_s", 20)),
        request_timeout_s=float(data.get("request_timeout_s", 6)),
        state_db=str(
            os.environ.get("AGENTHUB_STATE_DB")
            or data.get("state_db")
            or "~/.local/share/agenthub/agenthub.db"
        ),
        nodes=tuple(nodes),
    )


def config_file_path() -> Path:
    return default_config_path()


def update_config(path: str | Path, mutate) -> HubConfig:
    """Mutate the raw YAML config, back it up, then validate and load it."""
    config_path = Path(path).expanduser()
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")

    mutate(data)

    backup = config_path.with_name(config_path.name + ".bak")
    shutil.copy2(config_path, backup)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    try:
        return load_config(config_path)
    except Exception:
        shutil.copy2(backup, config_path)
        raise
