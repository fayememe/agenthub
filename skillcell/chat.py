from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .opencode import OpenCodeClient, OpenCodeError
from .store import CellStore


CONTEXT_START = "[skillcell context]"
CONTEXT_END = "[/skillcell context]"
CONTEXT_RE = re.compile(
    re.escape(CONTEXT_START) + r".*?" + re.escape(CONTEXT_END),
    re.DOTALL,
)
CHAT_SESSION_KEY = "chat_session_id"
CHAT_TITLE = "skillcell: chat"
CHAT_SYSTEM = """You are skillcell, an audit assistant for a library of reusable agent skills.
Help the user understand audit proposals, evidence, quality, duplicates, and next actions.
Use the supplied context. Do not claim that you changed any state: changes happen only
through the UI review buttons. Be concise, concrete, and skeptical. Point out missing
evidence, risks, and whether a proposal is worth turning into a skill.
"""


def strip_context(text: str) -> str:
    return CONTEXT_RE.sub("", text).strip()


def response_text(response: Mapping[str, Any]) -> str:
    parts = response.get("parts") if isinstance(response.get("parts"), list) else []
    texts = [
        str(part.get("text"))
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "text"
        and not part.get("ignored")
        and part.get("text")
    ]
    return "\n\n".join(texts).strip()


def _message_time(info: Mapping[str, Any]) -> int:
    value = info.get("time") if isinstance(info.get("time"), dict) else {}
    created = value.get("created") if isinstance(value, dict) else None
    return int(created) if isinstance(created, (int, float)) else 0


class ChatService:
    def __init__(
        self,
        client: OpenCodeClient,
        store: CellStore,
        *,
        directory: str | None,
        agent: str | None = None,
        model: Mapping[str, Any] | None = None,
        timeout: float = 300.0,
    ):
        self.client = client
        self.store = store
        self.directory = directory
        self.agent = agent
        self.model = dict(model) if model else None
        self.timeout = timeout

    async def ensure_session(self) -> str:
        session_id = self.store.get_meta(CHAT_SESSION_KEY)
        if session_id:
            try:
                await self.client.session(session_id, directory=self.directory)
                return session_id
            except OpenCodeError:
                pass
        return await self.reset()

    async def reset(self) -> str:
        session = await self.client.create_session(
            directory=self.directory,
            title=CHAT_TITLE,
            agent=self.agent,
            model=self.model,
        )
        session_id = session.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise OpenCodeError("chat session creation failed")
        self.store.set_meta(CHAT_SESSION_KEY, session_id)
        return session_id

    async def send(self, message: str, context: str) -> tuple[str, str]:
        session_id = await self.ensure_session()
        prompt = f"{CONTEXT_START}\n{context}\n{CONTEXT_END}\n\n{message}"
        response = await self.client.prompt(
            session_id,
            prompt,
            directory=self.directory,
            agent=self.agent,
            model=self.model,
            system=CHAT_SYSTEM,
            tools={
                "edit": False,
                "write": False,
                "patch": False,
                "bash": False,
                "task": False,
            },
            timeout=self.timeout,
        )
        text = response_text(response)
        if not text:
            raise OpenCodeError("chat returned an empty response")
        return session_id, text

    async def history(self, limit: int = 50) -> list[dict[str, Any]]:
        session_id = self.store.get_meta(CHAT_SESSION_KEY)
        if not session_id:
            return []
        try:
            messages = await self.client.messages(
                session_id,
                directory=self.directory,
                limit=max(1, limit),
            )
        except OpenCodeError:
            return []
        items: list[dict[str, Any]] = []
        for message in messages:
            info = message.get("info") if isinstance(message.get("info"), dict) else {}
            role = str(info.get("role") or "")
            parts = message.get("parts") if isinstance(message.get("parts"), list) else []
            text = "\n\n".join(
                str(part.get("text"))
                for part in parts
                if isinstance(part, dict)
                and part.get("type") == "text"
                and not part.get("ignored")
                and part.get("text")
            ).strip()
            if not text:
                continue
            if role == "user":
                text = strip_context(text)
                if not text:
                    continue
            items.append(
                {
                    "role": role,
                    "text": text,
                    "createdAt": _message_time(info),
                }
            )
        return items
