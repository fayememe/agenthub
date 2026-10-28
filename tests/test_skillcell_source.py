from __future__ import annotations

import unittest

from skillcell.models import EvidenceKind, EvidenceRef, PrivacyClass, SourceRef
from skillcell.opencode import OpenCodeError, SessionList
from skillcell.source import OpenCodeSource


def session(session_id: str, updated: int, title: str = "Demo") -> dict:
    return {
        "id": session_id,
        "projectID": "p1",
        "title": title,
        "time": {"updated": updated},
        "location": {"directory": "/work/demo"},
    }


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.pages: dict[str | None, SessionList] = {}
        self.messages_data: list[dict] = []
        self.message_data: dict | None = None

    async def projects(self) -> list[dict]:
        return [
            {"id": "p1", "worktree": "/work/demo"},
            {"id": "p2", "worktree": "/work/secret"},
            {"id": "global", "worktree": "/"},
        ]

    async def project_directories(self, project_id: str) -> list[str]:
        if project_id == "p1":
            return ["/work/demo"]
        if project_id == "broken":
            raise OpenCodeError("boom")
        return []

    async def sessions(self, **kwargs) -> SessionList:
        self.calls.append(kwargs)
        return self.pages.get(kwargs.get("cursor"), SessionList(items=()))

    async def messages(self, session_id: str, **kwargs) -> list[dict]:
        self.calls.append({"messages": session_id, **kwargs})
        return self.messages_data

    async def message(self, session_id: str, message_id: str, **kwargs) -> dict:
        self.calls.append({"message": message_id, **kwargs})
        if self.message_data is None:
            raise KeyError(message_id)
        return self.message_data


class SourceDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_projects_and_applies_excludes(self):
        client = FakeClient()
        source = OpenCodeSource(client, "node-a", exclude=("/work/secret",))
        items = await source.list_sources()
        self.assertEqual([item.source_id for item in items], ["node-a:p1"])
        self.assertEqual(items[0].directory, "/work/demo")
        self.assertEqual(items[0].node, "node-a")
        self.assertEqual(items[0].project_id, "p1")

    async def test_explicit_sources_are_used(self):
        client = FakeClient()
        explicit = [
            SourceRef(source_id="a", node="n", project_id="p", directory="/a"),
            SourceRef(source_id="b", node="n", project_id="p", directory="/b"),
        ]
        source = OpenCodeSource(client, "node-a", explicit_sources=explicit, exclude=("b",))
        items = await source.list_sources()
        self.assertEqual([item.source_id for item in items], ["a"])
        self.assertEqual(client.calls, [])

    async def test_refresh_sources(self):
        client = FakeClient()
        source = OpenCodeSource(client, "node-a", exclude=("/work/secret",))
        self.assertEqual(len(await source.list_sources()), 1)
        await source.refresh_sources()
        self.assertEqual(len(await source.list_sources()), 1)

    async def test_source_for_unknown(self):
        client = FakeClient()
        source = OpenCodeSource(client, "node-a")
        self.assertIsNone(await source.source_for("missing"))


class SourceSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_sessions_maps_page(self):
        client = FakeClient()
        client.pages[None] = SessionList(
            items=(session("ses_1", 123),),
            next_cursor="next",
        )
        source = OpenCodeSource(client, "node-a")
        page = await source.list_sessions(
            SourceRef(source_id="src", node="node-a", directory="/work/demo")
        )
        self.assertEqual(page.sessions[0].id, "ses_1")
        self.assertEqual(page.sessions[0].title, "Demo")
        self.assertEqual(page.sessions[0].updated_at, 123)
        self.assertEqual(page.cursor, "next")
        self.assertTrue(page.has_more)
        self.assertEqual(client.calls[-1]["order"], "asc")

    async def test_recent_sessions_stops_at_high_watermark(self):
        client = FakeClient()
        client.pages[None] = SessionList(
            items=(session("ses_3", 300), session("ses_2", 200)),
            next_cursor="page-2",
        )
        client.pages["page-2"] = SessionList(
            items=(session("ses_1", 100), session("ses_0", 50)),
        )
        source = OpenCodeSource(client, "node-a")
        items = await source.recent_sessions(
            SourceRef(source_id="src", node="node-a", directory="/work/demo"),
            since_ms=150,
        )
        self.assertEqual([item.id for item in items], ["ses_3", "ses_2"])
        self.assertEqual([call["order"] for call in client.calls], ["desc", "desc"])


class SourceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.source_ref = SourceRef(
            source_id="src",
            node="node-a",
            project_id="p1",
            directory="/work/demo",
        )
        self.client = FakeClient()
        self.messages = [
            {
                "info": {
                    "id": "msg_1",
                    "role": "user",
                    "time": {"created": 1000},
                },
                "parts": [
                    {"id": "prt_1", "type": "text", "text": "actually use --force-with-lease"},
                    {"id": "prt_2", "type": "reasoning", "text": "hidden"},
                    {"id": "prt_3", "type": "text", "text": "   "},
                ],
            },
            {
                "info": {
                    "id": "msg_2",
                    "role": "assistant",
                    "time": {"created": 2000},
                },
                "parts": [
                    {
                        "id": "prt_4",
                        "type": "tool",
                        "tool": "bash",
                        "state": {
                            "status": "error",
                            "input": {"command": "echo sk-abcdefghijklmnop"},
                            "output": "password=hunter2xyz",
                            "error": "boom",
                        },
                    },
                    {
                        "id": "prt_5",
                        "type": "text",
                        "text": "I will check the remote first.",
                    },
                    {
                        "id": "prt_6",
                        "type": "tool",
                        "tool": "read",
                        "state": {"status": "completed", "input": {"filePath": "a"}},
                    },
                ],
            },
        ]

    async def test_extracts_and_redacts_evidence(self):
        source = OpenCodeSource(self.client, "node-a")
        items = source.extract_evidence(self.source_ref, "ses_1", self.messages)
        self.assertEqual(
            [item.kind for item in items],
            [
                EvidenceKind.USER_CORRECTION,
                EvidenceKind.TOOL_ERROR,
                EvidenceKind.OTHER,
                EvidenceKind.OTHER,
            ],
        )
        self.assertEqual(items[0].created_at, 1000)
        self.assertEqual(items[1].created_at, 2000)
        self.assertEqual(items[0].ref.message_id, "msg_1")
        self.assertEqual(items[0].ref.part_id, "prt_1")
        self.assertEqual(items[0].privacy_class, PrivacyClass.PRIVATE)
        combined = "\n".join(item.excerpt for item in items)
        self.assertNotIn("sk-abcdefghijklmnop", combined)
        self.assertNotIn("hunter2xyz", combined)
        self.assertIn("[REDACTED]", combined)
        self.assertIn("tool bash status=error", combined)
        self.assertIn("boom", combined)

    async def test_evidence_ids_are_deterministic(self):
        source = OpenCodeSource(self.client, "node-a")
        first = source.extract_evidence(self.source_ref, "ses_1", self.messages)
        second = source.extract_evidence(self.source_ref, "ses_1", self.messages)
        self.assertEqual([item.id for item in first], [item.id for item in second])

    async def test_truncates_long_evidence(self):
        source = OpenCodeSource(self.client, "node-a", max_excerpt_chars=200)
        messages = [
            {
                "info": {"id": "msg_1", "role": "assistant", "time": {"created": 1}},
                "parts": [{"id": "prt_1", "type": "text", "text": "x" * 1000}],
            }
        ]
        items = source.extract_evidence(self.source_ref, "ses_1", messages)
        self.assertIn("[truncated", items[0].excerpt)
        self.assertLess(len(items[0].excerpt), 300)

    async def test_read_session_evidence(self):
        self.client.messages_data = self.messages
        source = OpenCodeSource(self.client, "node-a")
        items = await source.read_session_evidence(self.source_ref, "ses_1", message_limit=10)
        self.assertEqual(len(items), 4)
        self.assertEqual(self.client.calls[-1]["limit"], 10)

    async def test_read_evidence_by_ref(self):
        self.client.message_data = self.messages[0]
        source = OpenCodeSource(
            self.client, "node-a", explicit_sources=[self.source_ref]
        )
        evidence = await source.read_evidence(
            EvidenceRef(
                source_id="src",
                session_id="ses_1",
                message_id="msg_1",
                part_id="prt_1",
            )
        )
        self.assertEqual(evidence.kind, EvidenceKind.USER_CORRECTION)

    async def test_read_evidence_unknown_source(self):
        source = OpenCodeSource(self.client, "node-a")
        with self.assertRaises(KeyError):
            await source.read_evidence(
                EvidenceRef(source_id="missing", session_id="ses_1")
            )

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):
            OpenCodeSource(self.client, "node-a", max_excerpt_chars=10)
        with self.assertRaises(ValueError):
            OpenCodeSource(self.client, "node-a", max_tool_chars=10)


if __name__ == "__main__":
    unittest.main()
