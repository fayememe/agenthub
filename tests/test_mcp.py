from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

PROJECT = "/work/demo"
OTHER = "/work/other"
SESSION_ID = "ses_test"


class FakeOC(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not str(self.headers.get("Authorization", "")).startswith("Basic "):
            self._json({"error": "unauthorized"}, 401)
            return

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if path == "/global/health":
            self._json({"healthy": True, "version": "fake-1.0"})
        elif path == "/config":
            self._json({"username": "sensitive-user", "model": "fake/model"})
        elif path == "/provider":
            self._json({"connected": ["fake"], "all": [{"id": "fake"}]})
        elif path == "/agent":
            self._json([{"name": "build", "description": "build agent"}])
        elif path == "/skill":
            self._json([{"name": "demo", "description": "demo skill", "location": "/home/me/.skills/demo"}])
        elif path == "/mcp":
            self._json({"demo": {"status": "connected"}})
        elif path == "/lsp":
            self._json([{"id": "py", "name": "python", "root": "/home/me/work", "status": "ready"}])
        elif path == "/formatter":
            self._json([{"name": "ruff", "extensions": [".py"], "enabled": True}])
        elif path == "/path":
            self._json(
                {
                    "home": "/home/me",
                    "state": "/home/me/.local/state",
                    "config": "/home/me/.config",
                    "worktree": PROJECT,
                    "directory": PROJECT,
                }
            )
        elif path == "/session/status":
            self._json({SESSION_ID: {"type": "busy"}})
        elif path == "/permission":
            self._json([{"id": "p1", "permission": "bash", "patterns": ["rm -rf /"]}])
        elif path == "/question":
            self._json([{"id": "q1", "questions": [{"header": "Q", "question": "OK?"}]}])
        elif path == "/project":
            self._json(
                [
                    {"id": "global", "worktree": "/", "time": {"updated": 1}},
                    {"id": "demo", "worktree": PROJECT, "vcs": "git", "time": {"updated": 3}},
                ]
            )
        elif path == "/api/session":
            self._json(
                {
                    "data": [
                        {
                            "id": SESSION_ID,
                            "projectID": "demo",
                            "title": "Demo session",
                            "agent": "build",
                            "model": {"providerID": "fake", "id": "model"},
                            "cost": 0.01,
                            "tokens": {"input": 1000, "output": 200},
                            "time": {"created": 1, "updated": 2},
                            "location": {"directory": PROJECT},
                            "share": {"url": "https://share.example/ses_test"},
                        },
                        {
                            "id": "ses_other",
                            "projectID": "global",
                            "title": "Other session",
                            "agent": "build",
                            "time": {"created": 3, "updated": 4},
                            "location": {"directory": OTHER},
                        },
                    ]
                }
            )
        elif path == "/session":
            if query.get("directory") == [PROJECT]:
                self._json(
                    [
                        {
                            "id": SESSION_ID,
                            "title": "Demo session",
                            "slug": "demo",
                            "agent": "build",
                            "directory": PROJECT,
                            "summary": {"additions": 4, "deletions": 2, "files": 1},
                            "time": {"created": 1, "updated": 2},
                        }
                    ]
                )
            else:
                self._json([])
        elif path == f"/session/{SESSION_ID}":
            self._json(
                {
                    "id": SESSION_ID,
                    "title": "Demo session",
                    "slug": "demo",
                    "agent": "build",
                    "directory": PROJECT,
                    "time": {"created": 1, "updated": 2},
                    "share": {"url": "https://share.example/ses_test"},
                }
            )
        elif path == "/vcs/status":
            self._json([{"file": "demo.py", "additions": 2, "deletions": 1, "status": "modified"}])
        else:
            self._json({"error": "not found"}, 404)


class McpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOC)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        cls.tmp = tempfile.TemporaryDirectory()
        port = cls.server.server_address[1]
        cls.config_path = Path(cls.tmp.name) / "config.yaml"
        cls.config_path.write_text(
            yaml.safe_dump(
                {
                    "hub": {"host": "127.0.0.1", "port": 8670, "proxy_base_port": 8671},
                    "refresh_interval_s": 20,
                    "request_timeout_s": 2,
                    "nodes": [
                        {
                            "name": "fake",
                            "api_url": f"http://127.0.0.1:{port}",
                            "web_url": f"http://127.0.0.1:{port}",
                            "username": "opencode",
                            "password": "test",
                        }
                    ],
                    "mcp": {
                        "host": "127.0.0.1",
                        "port": 8680,
                        "public_url": "https://mcp.example.test",
                        "auth": {"mode": "bearer", "token_env": "TEST_MCP_TOKEN"},
                        "allowed_hosts": ["testserver"],
                        "expose_projects": ["/work"],
                    },
                }
            )
        )
        os.environ["TEST_MCP_TOKEN"] = "test-token"

        from app.mcp.config import load_mcp_config

        cls.config = load_mcp_config(cls.config_path)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()
        os.environ.pop("TEST_MCP_TOKEN", None)

    def _service(self):
        from app.mcp.service import McpService

        return McpService(self.config)

    def test_config_requires_token(self):
        from app.mcp.config import load_mcp_config

        old = os.environ.pop("TEST_MCP_TOKEN")
        try:
            with self.assertRaises(ValueError):
                load_mcp_config(self.config_path)
        finally:
            os.environ["TEST_MCP_TOKEN"] = old

    def test_config_filters_nodes(self):
        self.assertEqual([node.name for node in self.config.hub.nodes], ["fake"])
        self.assertEqual(self.config.host, "127.0.0.1")
        self.assertEqual(self.config.port, 8680)
        self.assertEqual(self.config.auth_mode, "bearer")
        self.assertIn("mcp.example.test", self.config.allowed_hosts)

    def test_read_only_tools(self):
        async def run():
            service = self._service()
            try:
                hosts = await service.list_hosts()
                self.assertEqual(hosts["hosts"][0]["name"], "fake")
                self.assertTrue(hosts["hosts"][0]["online"])

                status = await service.get_host_status("fake")
                self.assertNotIn("username", status["config"])
                self.assertEqual(status["paths"], {"worktree": PROJECT, "directory": PROJECT})
                self.assertNotIn("location", status["skills"][0])
                self.assertNotIn("root", status["lsp"][0])

                projects = await service.list_projects("fake")
                names = [item["name"] for item in projects["nodes"][0]["projects"]]
                self.assertEqual(names, ["demo", "Other"])

                sessions = await service.list_sessions("fake", project=PROJECT)
                self.assertEqual(sessions["sessions"][0]["title"], "Demo session")
                self.assertEqual(sessions["sessions"][0]["status"], "busy")
                self.assertNotIn("share", sessions["sessions"][0])

                other = await service.list_sessions("fake", project="__other__")
                self.assertEqual(other["sessions"][0]["title"], "Other session")

                project_status = await service.get_project_status("fake", PROJECT)
                self.assertEqual(project_status["changes"]["files"], 1)
                self.assertNotIn("permissions", project_status)
                self.assertNotIn("questions", project_status)
                self.assertEqual(
                    project_status["paths"], {"worktree": PROJECT, "directory": PROJECT}
                )

                detailed = await service.get_project_status(
                    "fake", PROJECT, include_details=True
                )
                self.assertEqual(detailed["pending_permissions"], 1)
                self.assertEqual(detailed["pending_questions"], 1)

                meta = await service.get_session_meta("fake", SESSION_ID)
                self.assertEqual(meta["session"]["title"], "Demo session")
                self.assertEqual(meta["session"]["status"], "busy")
                self.assertNotIn("share", meta["session"])

                with self.assertRaises(ValueError):
                    await service.get_project_status("fake", "/etc")
                with self.assertRaises(ValueError):
                    await service.get_project_status("fake", f"{PROJECT}/../../etc")
                with self.assertRaises(ValueError):
                    await service.get_session_meta("fake", "../../config")
                with self.assertRaises(ValueError):
                    await service.list_projects("missing")
            finally:
                await service.aclose()

        asyncio.run(run())

    def test_http_auth_and_health(self):
        from starlette.testclient import TestClient

        from app.mcp.server import build_app

        app, service, _mcp = build_app(self.config)
        try:
            with TestClient(app, base_url="http://testserver") as client:
                self.assertEqual(client.get("/healthz").json()["status"], "ok")

                response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1})
                self.assertEqual(response.status_code, 401)
                self.assertIn("Bearer", response.headers.get("www-authenticate", ""))

                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                    headers={
                        "Authorization": "Bearer test-token",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                self.assertEqual(response.status_code, 200)
        finally:
            asyncio.run(service.aclose())


if __name__ == "__main__":
    unittest.main()
