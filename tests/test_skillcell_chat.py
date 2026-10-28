from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skillcell.chat import (
    CHAT_SESSION_KEY,
    ChatService,
    strip_context,
)
from skillcell.opencode import OpenCodeNotFound
from skillcell.store import CellStore


class FakeClient:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.created: list[dict] = []
        self.prompts: list[dict] = []
        self.messages_data: list[dict] = []
        self.next_id = 0

    async def session(self, session_id: str, *, directory=None):
        if session_id not in self.sessions:
            raise OpenCodeNotFound("missing")
        return self.sessions[session_id]

    async def create_session(self, **kwargs):
        self.next_id += 1
        session_id = f"ses_chat_{self.next_id}"
        self.sessions[session_id] = {"id": session_id, "title": kwargs.get("title")}
        self.created.append(kwargs)
        return self.sessions[session_id]

    async def prompt(self, session_id, text, **kwargs):
        self.prompts.append({"session_id": session_id, "text": text, **kwargs})
        return {"parts": [{"type": "text", "text": "Useful answer."}]}

    async def messages(self, session_id, *, directory=None, limit=50):
        return self.messages_data


class ChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CellStore(Path(self.tmp.name) / "skillcell.db")
        self.client = FakeClient()
        self.chat = ChatService(
            self.client,
            self.store,
            directory="/audit",
            agent="build",
            model={"providerID": "p", "modelID": "m"},
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def test_session_is_created_and_reused(self):
        first = await self.chat.ensure_session()
        second = await self.chat.ensure_session()
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.created), 1)
        self.assertEqual(self.store.get_meta(CHAT_SESSION_KEY), first)

    async def test_stale_session_is_recreated(self):
        first = await self.chat.ensure_session()
        self.client.sessions.pop(first)
        second = await self.chat.ensure_session()
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.client.created), 2)

    async def test_send_includes_context_and_system(self):
        session_id, text = await self.chat.send("What are the risks?", "Proposal context")
        self.assertEqual(text, "Useful answer.")
        prompt = self.client.prompts[0]
        self.assertEqual(prompt["session_id"], session_id)
        self.assertIn("Proposal context", prompt["text"])
        self.assertIn("What are the risks?", prompt["text"])
        self.assertIn("You are skillcell", prompt["system"])
        self.assertFalse(prompt["tools"]["write"])

    async def test_history_strips_context(self):
        session_id = await self.chat.ensure_session()
        self.client.messages_data = [
            {
                "info": {"role": "user", "time": {"created": 1}},
                "parts": [
                    {
                        "type": "text",
                        "text": "[skillcell context]\nsecret context\n[/skillcell context]\n\nhello",
                    }
                ],
            },
            {
                "info": {"role": "assistant", "time": {"created": 2}},
                "parts": [{"type": "text", "text": "hi"}],
            },
        ]
        items = await self.chat.history()
        self.assertEqual([item["role"] for item in items], ["user", "assistant"])
        self.assertEqual(items[0]["text"], "hello")
        self.assertEqual(items[1]["text"], "hi")
        self.assertEqual(items[0]["createdAt"], 1)
        self.assertEqual(self.store.get_meta(CHAT_SESSION_KEY), session_id)

    async def test_reset_creates_new_session(self):
        first = await self.chat.ensure_session()
        second = await self.chat.reset()
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.get_meta(CHAT_SESSION_KEY), second)

    async def test_strip_context(self):
        self.assertEqual(
            strip_context("[skillcell context]\nfoo\n[/skillcell context]\n\nbar"),
            "bar",
        )
        self.assertEqual(strip_context("plain"), "plain")


if __name__ == "__main__":
    unittest.main()
