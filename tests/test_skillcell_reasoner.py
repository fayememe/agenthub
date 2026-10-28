from __future__ import annotations

import hashlib
import unittest

from skillcell.models import Evidence, EvidenceKind, EvidenceRef, SourceRef
from skillcell.reasoner import OpenCodeReasoner, ReasonerError


def evidence() -> Evidence:
    excerpt = "actually check the remote before merging"
    return Evidence(
        id="ev_1",
        ref=EvidenceRef(source_id="src", session_id="ses_1", message_id="msg_1"),
        kind=EvidenceKind.USER_CORRECTION,
        excerpt=excerpt,
        content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
        created_at=1000,
    )


class FakeClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.created: list[dict] = []
        self.prompts: list[dict] = []

    async def create_session(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "ses_audit", "title": kwargs.get("title")}

    async def prompt(self, session_id, text, **kwargs):
        self.prompts.append({"session_id": session_id, "text": text, **kwargs})
        return {"info": {"id": "msg_audit"}, "parts": [{"type": "text", "text": self.text}]}


class ReasonerTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_fenced_json_and_builds_prompt(self):
        text = """```json
{"proposals":[{"title":"Safe git","artifact_type":"skill","scope":"personal","score":88,"summary":"s","problem":"p","triggers":["a"],"exclusions":["b"],"evidence_ids":["ev_1"]}]}
```"""
        client = FakeClient(text)
        reasoner = OpenCodeReasoner(
            client,
            directory="/audit",
            agent="build",
            model={"providerID": "p", "modelID": "m"},
        )
        result = await reasoner.propose(
            [evidence()],
            [{"name": "safe-git", "description": "Existing"}],
            SourceRef(source_id="src", node="node-a", project_id="p1", directory="/work/demo"),
        )
        self.assertEqual(result.session_id, "ses_audit")
        self.assertEqual(result.proposals[0]["title"], "Safe git")
        self.assertEqual(result.proposals[0]["score"], 88)
        self.assertEqual(client.created[0]["directory"], "/audit")
        self.assertEqual(client.created[0]["title"], "skillcell: src")
        prompt = client.prompts[0]
        self.assertEqual(prompt["session_id"], "ses_audit")
        self.assertIn("ev_1", prompt["text"])
        self.assertIn("safe-git", prompt["text"])
        self.assertFalse(prompt["tools"]["bash"])

    async def test_focus_is_added_to_prompt(self):
        client = FakeClient('{"proposals":[]}')
        reasoner = OpenCodeReasoner(client)
        await reasoner.propose(
            [evidence()],
            [],
            SourceRef(source_id="src", directory="/work/demo"),
            focus="release hardening full-send mode",
        )
        self.assertIn("release hardening", client.prompts[0]["text"])
        self.assertIn("User focus", client.prompts[0]["text"])

    async def test_bare_json_is_accepted(self):
        client = FakeClient('{"proposals":[]}')
        reasoner = OpenCodeReasoner(client)
        result = await reasoner.propose(
            [evidence()],
            [],
            SourceRef(source_id="src", directory="/work/demo"),
        )
        self.assertEqual(result.proposals, ())

    async def test_invalid_json_raises(self):
        client = FakeClient("not json")
        reasoner = OpenCodeReasoner(client)
        with self.assertRaises(ReasonerError):
            await reasoner.propose(
                [evidence()],
                [],
                SourceRef(source_id="src", directory="/work/demo"),
            )

    async def test_empty_response_raises(self):
        client = FakeClient("")
        reasoner = OpenCodeReasoner(client)
        with self.assertRaises(ReasonerError):
            await reasoner.propose(
                [evidence()],
                [],
                SourceRef(source_id="src", directory="/work/demo"),
            )

    async def test_evidence_budget_is_limited(self):
        client = FakeClient('{"proposals":[]}')
        reasoner = OpenCodeReasoner(client, max_evidence_chars=2000)
        items = [evidence() for _ in range(100)]
        await reasoner.propose(items, [], SourceRef(source_id="src", directory="/work/demo"))
        prompt = client.prompts[0]["text"]
        self.assertLess(len(prompt), 10000)


if __name__ == "__main__":
    unittest.main()
