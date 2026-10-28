from __future__ import annotations

import unittest

from app.activity import ActivityStore
from app.config import NodeConfig


NODE = NodeConfig(name="test", api_url="http://localhost", web_url="http://localhost")


class ActivityStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ActivityStore(None, None)

    def event(self, event_type, session="ses_1", **properties):
        return {"type": event_type, "properties": {"sessionID": session, **properties}}

    def test_busy_then_done(self):
        self.store.handle_event(NODE, self.event("session.execution.started"))
        self.assertTrue(self.store.snapshot("test", "ses_1")["busy"])
        self.store.handle_event(NODE, self.event("session.idle"))
        snapshot = self.store.snapshot("test", "ses_1")
        self.assertFalse(snapshot["busy"])
        self.assertTrue(snapshot["done"])

    def test_error(self):
        self.store.handle_event(NODE, self.event("session.execution.started"))
        self.store.handle_event(NODE, self.event("session.execution.failed"))
        snapshot = self.store.snapshot("test", "ses_1")
        self.assertFalse(snapshot["busy"])
        self.assertTrue(snapshot["error"])
        self.assertFalse(snapshot["done"])

    def test_permission_and_question(self):
        self.store.handle_event(NODE, self.event("permission.asked", id="per_1"))
        self.store.handle_event(NODE, self.event("question.asked", id="que_1"))
        snapshot = self.store.snapshot("test", "ses_1")
        self.assertEqual(snapshot["permissions"], 1)
        self.assertEqual(snapshot["questions"], 1)

        self.store.handle_event(NODE, self.event("permission.replied", requestID="per_1"))
        self.store.handle_event(NODE, self.event("question.rejected", requestID="que_1"))
        snapshot = self.store.snapshot("test", "ses_1")
        self.assertEqual(snapshot["permissions"], 0)
        self.assertEqual(snapshot["questions"], 0)

    def test_v2_events(self):
        self.store.handle_event(
            NODE,
            {"type": "permission.v2.asked", "data": {"id": "per_2", "sessionID": "ses_2", "action": "bash"}},
        )
        self.store.handle_event(
            NODE,
            {"type": "permission.v2.replied", "data": {"sessionID": "ses_2", "requestID": "per_2", "reply": "once"}},
        )
        self.assertEqual(self.store.snapshot("test", "ses_2")["permissions"], 0)

    def test_mark_read(self):
        self.store.handle_event(NODE, self.event("session.execution.started"))
        self.store.handle_event(NODE, self.event("session.execution.succeeded"))
        self.assertTrue(self.store.snapshot("test", "ses_1")["done"])
        self.store.mark_read("test", {"ses_1"})
        self.assertFalse(self.store.snapshot("test", "ses_1")["done"])


if __name__ == "__main__":
    unittest.main()


class TreeActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_attention(self):
        from pathlib import Path

        from app.config import load_config
        from app.oc import OcClient

        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config.example.yaml")
        node = config.nodes[0]
        activity = ActivityStore(config, None)
        activity.handle_event(node, {"type": "session.execution.started", "properties": {"sessionID": "s1"}})
        activity.handle_event(
            node,
            {"type": "permission.asked", "properties": {"sessionID": "s1", "id": "per_1"}},
        )

        client = OcClient(config)
        client.activity = activity

        async def fake_get(target, path, params=None):
            if path == "/global/health":
                return {"healthy": True, "version": "1"}
            if path == "/project":
                return [{"id": "p1", "worktree": "/work", "vcs": "git", "time": {"updated": 1}}]
            if path == "/session/status":
                return {}
            raise AssertionError(path)

        async def fake_web(target, path, params=None):
            if path == "/api/session":
                return {
                    "data": [
                        {
                            "id": "s1",
                            "projectID": "p1",
                            "title": "demo",
                            "location": {"directory": "/work"},
                            "time": {"updated": 2},
                        }
                    ]
                }
            raise AssertionError(path)

        client._get = fake_get
        client._get_web = fake_web
        try:
            tree = await client.node_tree(node)
        finally:
            await client.close()

        project = tree["projects"][0]
        self.assertEqual(project["pending_permissions"], 1)
        self.assertEqual(project["busy_sessions"], 1)
        self.assertEqual(project["attention"], "permission")
