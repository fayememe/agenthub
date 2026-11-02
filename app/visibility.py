from __future__ import annotations

from typing import Any


DAY_MS = 24 * 60 * 60 * 1000


def project_visibility(
    project: dict[str, Any],
    *,
    manual_hidden: bool,
    favorite: bool,
    hide_stale_days: int,
    now_ms: int,
) -> tuple[bool, str | None]:
    """Return ``(hidden, reason)`` for a project in the fleet tree."""
    if project.get("kind") == "other":
        return False, None
    if manual_hidden:
        return True, "manual"
    if favorite or hide_stale_days <= 0:
        return False, None
    last = int(project.get("last_session") or project.get("updated") or 0)
    if not last or now_ms - last <= hide_stale_days * DAY_MS:
        return False, None
    if (
        project.get("busy_sessions")
        or project.get("pending_permissions")
        or project.get("pending_questions")
    ):
        return False, None
    return True, "stale"
