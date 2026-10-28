from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.config import config_file_path


DEFAULT_STATE_DB = "~/.local/share/agenthub/skillcell.db"
DEFAULT_SKILLS_ROOT = "~/.config/opencode/skill"
DEFAULT_EXECUTOR_DIRECTORY = "~/.local/share/skillcell/workspace"


@dataclass(frozen=True, slots=True)
class SkillcellSettings:
    enabled: bool = False
    state_db: str = DEFAULT_STATE_DB
    skills_root: str = DEFAULT_SKILLS_ROOT
    executor_node: str | None = None
    executor_directory: str | None = DEFAULT_EXECUTOR_DIRECTORY
    source_nodes: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    auto_scan: bool = False
    interval_s: int = 3600
    lookback_days: float = 7.0
    max_sessions: int = 10
    max_session_pages: int = 5
    message_limit: int = 50
    max_evidence: int = 40
    min_score: float = 60.0
    max_proposals: int = 5
    agent: str | None = "build"
    model: str | dict[str, str] | None = None
    reasoner_timeout_s: float = 300.0
    request_timeout_s: float = 30.0

    @property
    def lookback_ms(self) -> int:
        return max(1, int(self.lookback_days * 24 * 3600 * 1000))

    @property
    def model_ref(self) -> dict[str, str] | None:
        value = self.model
        if isinstance(value, Mapping):
            provider = str(value.get("providerID") or value.get("provider") or "").strip()
            model_id = str(value.get("modelID") or value.get("model") or "").strip()
            if not provider or not model_id:
                raise ValueError("skillcell.model requires providerID and modelID")
            return {"providerID": provider, "modelID": model_id}
        if isinstance(value, str) and "/" in value:
            provider, model_id = value.split("/", 1)
            provider = provider.strip()
            model_id = model_id.strip()
            if not provider or not model_id:
                raise ValueError("skillcell.model requires provider/model")
            return {"providerID": provider, "modelID": model_id}
        if value:
            raise ValueError("skillcell.model must be 'provider/model' or a mapping")
        return None


def _as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError("expected a list or comma-separated string")


def _path(value: Any, *, base: Path, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    path = Path(text or default).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path)


def _clamp(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def load_skillcell_settings(path: str | Path | None = None) -> SkillcellSettings:
    config_path = Path(path).expanduser() if path else config_file_path()
    if not config_path.exists():
        return SkillcellSettings()
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    section = data.get("skillcell") or {}
    if not isinstance(section, dict):
        raise ValueError("skillcell must be a mapping")
    base = config_path.parent
    enabled = bool(section.get("enabled", False))
    executor_node = section.get("executor_node")
    executor_node = str(executor_node).strip() if executor_node else None
    agent = section.get("agent")
    agent = str(agent).strip() if agent else None
    model = section.get("model")
    if isinstance(model, Mapping):
        model = {str(key): str(value) for key, value in model.items()}
    elif model is not None:
        model = str(model).strip()
    return SkillcellSettings(
        enabled=enabled,
        state_db=_path(section.get("state_db"), base=base, default=DEFAULT_STATE_DB),
        skills_root=_path(section.get("skills_root"), base=base, default=DEFAULT_SKILLS_ROOT),
        executor_node=executor_node,
        executor_directory=_path(
            section.get("executor_directory"),
            base=base,
            default=DEFAULT_EXECUTOR_DIRECTORY,
        ),
        source_nodes=_as_list(section.get("source_nodes")),
        exclude=_as_list(section.get("exclude")),
        auto_scan=bool(section.get("auto_scan", False)),
        interval_s=int(_clamp(section.get("interval_s"), 3600, 30, 7 * 24 * 3600)),
        lookback_days=_clamp(section.get("lookback_days"), 7, 0.01, 365),
        max_sessions=int(_clamp(section.get("max_sessions"), 10, 1, 200)),
        max_session_pages=int(_clamp(section.get("max_session_pages"), 5, 1, 50)),
        message_limit=int(_clamp(section.get("message_limit"), 50, 1, 500)),
        max_evidence=int(_clamp(section.get("max_evidence"), 40, 1, 200)),
        min_score=_clamp(section.get("min_score"), 60, 0, 100),
        max_proposals=int(_clamp(section.get("max_proposals"), 5, 1, 20)),
        agent=agent or "build",
        model=model,
        reasoner_timeout_s=_clamp(section.get("reasoner_timeout_s"), 300, 30, 3600),
        request_timeout_s=_clamp(section.get("request_timeout_s"), 30, 5, 300),
    )
