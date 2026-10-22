from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

PROJECT = "/work/demo"
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

        if parsed.path == "/global/health":
            self._json({"healthy": True, "version": "fake-1.0"})
        elif parsed.path == "/project":
            self._json(
                [
                    {"id": "global", "worktree": "/", "time": {"updated": 1}},
                    {"id": "demo", "worktree": PROJECT, "vcs": "git", "time": {"updated": 3}},
                ]
            )
        elif parsed.path == "/api/session":
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
                            "tokens": {"input": 1000, "output": 200, "reasoning": 0},
                            "time": {"created": 1, "updated": 2},
                            "location": {"directory": PROJECT},
                        },
                        {
                            "id": "ses_other",
                            "projectID": "global",
                            "title": "Other session",
                            "agent": "build",
                            "model": {"providerID": "fake", "id": "model"},
                            "time": {"created": 3, "updated": 4},
                            "location": {"directory": "/work/other"},
                        },
                    ]
                }
            )
        elif parsed.path == "/session":
            if query.get("directory") == [PROJECT]:
                self._json(
                    [
                        {
                            "id": SESSION_ID,
                            "title": "Demo session",
                            "slug": "demo",
                            "agent": "build",
                            "model": {"providerID": "fake", "id": "model"},
                            "directory": PROJECT,
                            "cost": 0.01,
                            "tokens": {"input": 1000, "output": 200, "reasoning": 0},
                            "summary": {"additions": 4, "deletions": 2, "files": 1},
                            "time": {"created": 1, "updated": 2},
                        }
                    ]
                )
            else:
                self._json([])
        elif parsed.path == "/session/status":
            self._json({SESSION_ID: {"type": "busy"}})
        elif parsed.path == "/vcs/status":
            self._json([{"file": "demo.py", "additions": 2, "deletions": 1, "status": "modified"}])
        elif parsed.path == "/permission":
            self._json([])
        elif parsed.path == "/question":
            self._json([{"id": "q1"}])
        else:
            self._json({"error": "not found"}, 404)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOC)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        cls.tmp = tempfile.TemporaryDirectory()
        port = cls.server.server_address[1]
        config = {
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
        }
        cls.config_path = Path(cls.tmp.name) / "config.yaml"
        cls.config_path.write_text(yaml.safe_dump(config))

        os.environ["AGENTHUB_CONF"] = str(cls.config_path)
        os.environ["AGENTHUB_PROXY"] = "0"

        from app.main import app

        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()
        os.environ.pop("AGENTHUB_CONF", None)
        os.environ.pop("AGENTHUB_PROXY", None)
        os.environ.pop("AGENTHUB_STATE_DB", None)

    def setUp(self):
        os.environ["AGENTHUB_STATE_DB"] = str(Path(self.tmp.name) / f"state-{uuid.uuid4().hex}.db")

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(self.app)

    def test_health_and_config(self):
        with self._client() as client:
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            config = client.get("/api/config").json()
            self.assertEqual(config["name"], "homelab")
            self.assertEqual(config["nodes"], [{"name": "fake", "proxy_port": 8671}])

            settings = client.get("/api/settings").json()
            self.assertEqual(settings["name"], "homelab")
            self.assertEqual(settings["nodes"][0]["name"], "fake")
            self.assertNotIn("password", settings["nodes"][0])

    def test_host_status(self):
        with self._client() as client:
            status = client.get(
                "/api/host-status", params={"node": "fake", "directory": PROJECT}
            ).json()
        self.assertEqual(status["node"], "fake")
        self.assertEqual(status["health"]["version"], "fake-1.0")
        self.assertEqual(status["projects"], 2)
        self.assertEqual(status["pending_questions"], 1)
        self.assertIn("config", status["errors"])

    def test_tree(self):
        with self._client() as client:
            tree = client.get("/api/tree").json()
        node = tree["nodes"][0]
        self.assertEqual(node["name"], "fake")
        self.assertTrue(node["online"])
        self.assertEqual(node["version"], "fake-1.0")
        self.assertEqual([project["name"] for project in node["projects"]], ["demo", "Other"])
        self.assertEqual(node["projects"][0]["path"], PROJECT)
        self.assertEqual(node["projects"][0]["session_count"], 1)
        self.assertEqual(node["projects"][1]["path"], "__other__")
        self.assertEqual(node["projects"][1]["kind"], "other")
        self.assertEqual(node["projects"][1]["paths"], ["/work/other"])

    def test_sessions_and_status(self):
        with self._client() as client:
            sessions = client.get("/api/sessions", params={"node": "fake", "directory": PROJECT}).json()
            self.assertEqual(sessions["sessions"][0]["title"], "Demo session")
            self.assertEqual(sessions["sessions"][0]["status"], "busy")

            other = client.get("/api/sessions", params={"node": "fake", "directory": "__other__"}).json()
            self.assertEqual(other["sessions"][0]["title"], "Other session")

            status = client.get("/api/project-status", params={"node": "fake", "directory": PROJECT}).json()
            self.assertEqual(status["changes"]["files"], 1)
            self.assertEqual(status["changes"]["additions"], 2)
            self.assertEqual(status["changes"]["deletions"], 1)
            self.assertEqual(status["busy_sessions"], 1)
            self.assertEqual(status["pending_permissions"], 0)
            self.assertEqual(status["pending_questions"], 1)

    def test_settings_and_host_crud(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            shutil.copy2(self.config_path, config_path)
            original = os.environ["AGENTHUB_CONF"]
            os.environ["AGENTHUB_CONF"] = str(config_path)
            try:
                with self._client() as client:
                    response = client.patch("/api/settings", json={"name": "test lab"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["name"], "test lab")

                    response = client.post(
                        "/api/hosts",
                        json={
                            "name": "extra",
                            "api_url": "http://192.0.2.20:4096",
                            "web_url": "http://192.0.2.20:4097",
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        [node["name"] for node in response.json()["nodes"]], ["fake", "extra"]
                    )
                    self.assertEqual(response.json()["nodes"][1]["proxy_port"], 8672)

                    response = client.patch(
                        "/api/hosts/extra",
                        json={
                            "name": "extra2",
                            "api_url": "http://192.0.2.21:4096",
                            "web_url": "http://192.0.2.21:4097",
                            "proxy_port": 8699,
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["nodes"][1]["name"], "extra2")
                    self.assertEqual(response.json()["nodes"][1]["proxy_port"], 8699)

                    response = client.delete("/api/hosts/extra2")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual([node["name"] for node in response.json()["nodes"]], ["fake"])

                    response = client.delete("/api/hosts/fake")
                    self.assertEqual(response.status_code, 400)
            finally:
                os.environ["AGENTHUB_CONF"] = original

    def test_unknown_project(self):
        with self._client() as client:
            response = client.get("/api/sessions", params={"node": "fake", "directory": "/nope"})
            self.assertEqual(response.status_code, 404)

    def test_favorites(self):
        with self._client() as client:
            self.assertEqual(client.get("/api/favorites").json()["keys"], [])
            response = client.post("/api/favorites", json={"node": "fake", "path": PROJECT})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/favorites").json()["keys"], [f"fake:{PROJECT}"])

            response = client.delete("/api/favorites", params={"node": "fake", "path": PROJECT})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/favorites").json()["keys"], [])

            response = client.post("/api/favorites", json={"node": "missing", "path": PROJECT})
            self.assertEqual(response.status_code, 404)

    def test_static_files(self):
        with self._client() as client:
            index = client.get("/")
            self.assertEqual(index.status_code, 200)
            self.assertIn('class="brand-word"', index.text)
            self.assertIn("sah - homelab", index.text)
            script = client.get("/static/app.js")
            self.assertEqual(script.status_code, 200)
            self.assertIn("openProject", script.text)


if __name__ == "__main__":
    unittest.main()
