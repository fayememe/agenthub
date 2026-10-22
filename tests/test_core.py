from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from app.config import NodeConfig, load_config, update_config
from app.oc import OcClient, b64url

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_example_config(self):
        config = load_config(ROOT / "config.example.yaml")
        self.assertEqual(config.port, 8670)
        self.assertEqual(config.name, "agenthub")
        self.assertEqual([node.name for node in config.nodes], ["local", "node2"])
        self.assertEqual([node.proxy_port for node in config.nodes], [8671, 8672])

    def test_update_config_backup_and_add_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            shutil.copy2(ROOT / "config.example.yaml", path)

            def mutate(data):
                data["hub"]["name"] = "test hub"
                data["nodes"].append(
                    {
                        "name": "node3",
                        "api_url": "http://192.0.2.11:4096",
                        "web_url": "http://192.0.2.11:4097",
                    }
                )

            config = update_config(path, mutate)
            self.assertEqual(config.name, "test hub")
            self.assertEqual([node.name for node in config.nodes], ["local", "node2", "node3"])
            self.assertEqual(config.nodes[-1].proxy_port, 8673)
            self.assertTrue(path.with_name("config.yaml.bak").exists())

    def test_auth_header(self):
        node = NodeConfig(name="x", api_url="http://x", web_url="http://y", password="secret")
        expected = "Basic " + base64.b64encode(b"opencode:secret").decode()
        self.assertEqual(node.auth_header, expected)

    def test_b64url(self):
        value = "/home/me/git/agenthub"
        self.assertEqual(b64url(value), base64.urlsafe_b64encode(value.encode()).decode().rstrip("="))
        self.assertNotIn("=", b64url(value))


class ProjectTests(unittest.IsolatedAsyncioTestCase):
    async def test_projects_skip_global(self):
        config = load_config(ROOT / "config.example.yaml")
        client = OcClient(config)

        async def fake_get(node, path, params=None):
            self.assertEqual(path, "/project")
            return [
                {"id": "global", "worktree": "/", "time": {"updated": 1}},
                {"id": "a", "name": "Custom Agent Hub", "worktree": "/home/me/git/agenthub", "vcs": "git", "time": {"updated": 3}},
                {"id": "b", "worktree": "/home/me/git/other-project", "vcs": "git", "time": {"updated": 2}},
            ]

        client._get = fake_get
        try:
            projects = await client.projects(config.nodes[0])
        finally:
            await client.close()

        self.assertEqual([project["name"] for project in projects], ["Custom Agent Hub", "other-project"])
        self.assertEqual(projects[0]["b64"], b64url("/home/me/git/agenthub"))


class StatusTests(unittest.TestCase):
    def test_project_status(self):
        import asyncio

        from app.oc import OcClient

        config = load_config(ROOT / "config.example.yaml")

        async def fake_get(node, path, params=None):
            if path == "/vcs/status":
                return [
                    {"file": "a", "additions": 2, "deletions": 1},
                    {"file": "b", "additions": 3, "deletions": 0},
                ]
            if path == "/permission":
                return [{"id": "1"}]
            if path == "/question":
                return []
            if path == "/session/status":
                return {"s1": {"type": "busy"}, "s2": {"type": "idle"}}
            raise AssertionError(path)

        async def run():
            client = OcClient(config)
            client._get = fake_get
            try:
                return await client.project_status(config.nodes[0], "/p")
            finally:
                await client.close()

        status = asyncio.run(run())
        self.assertEqual(status["changes"]["files"], 2)
        self.assertEqual(status["changes"]["additions"], 5)
        self.assertEqual(status["changes"]["deletions"], 1)
        self.assertEqual(status["busy_sessions"], 1)
        self.assertEqual(status["pending_permissions"], 1)
        self.assertEqual(status["pending_questions"], 0)


if __name__ == "__main__":
    unittest.main()
