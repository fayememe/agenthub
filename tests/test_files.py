from __future__ import annotations

import unittest

from app.config import NodeConfig
from app.files import (
    FilePathError,
    FileService,
    FileTooLarge,
    OpenCodeFileProvider,
    content_payload,
    normalize_entry,
    relative_path,
)


NODE = NodeConfig(name="node", api_url="http://node", web_url="http://web", password="x")


class FakeOc:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def _get(self, node, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self.responses[path]


class PathTests(unittest.TestCase):
    def test_relative_paths(self):
        self.assertEqual(relative_path(None), ".")
        self.assertEqual(relative_path("."), ".")
        self.assertEqual(relative_path("app/"), "app")
        self.assertEqual(relative_path("app/main.py"), "app/main.py")
        with self.assertRaises(FilePathError):
            relative_path("../secret")
        with self.assertRaises(FilePathError):
            relative_path("/etc/passwd")
        with self.assertRaises(FilePathError):
            relative_path("app\\main.py")
        with self.assertRaises(FilePathError):
            relative_path("app/../secret")
        with self.assertRaises(FilePathError):
            relative_path("", allow_root=False)

    def test_normalize_entry_drops_absolute(self):
        item = normalize_entry(
            {
                "name": "app",
                "path": "app/",
                "absolute": "/home/me/project/app",
                "type": "directory",
                "ignored": False,
            }
        )
        self.assertEqual(
            item,
            {"name": "app", "path": "app/", "type": "directory", "ignored": False},
        )
        self.assertIsNone(normalize_entry({"name": "x"}))


class FileServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_files(self):
        oc = FakeOc(
            {
                "/file": [
                    {
                        "name": "app",
                        "path": "app/",
                        "absolute": "/x/app",
                        "type": "directory",
                        "ignored": False,
                    },
                    {"name": "bad", "path": "", "type": "file", "ignored": False},
                ]
            }
        )
        provider = OpenCodeFileProvider(oc, NODE)
        items = await provider.list("/work/demo", ".")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "app")
        self.assertEqual(oc.calls[0][1], {"directory": "/work/demo", "path": "."})

    async def test_read_file_strips_patch_and_limits(self):
        oc = FakeOc(
            {
                "/file/content": {
                    "type": "text",
                    "content": "hello",
                    "diff": "secret diff",
                    "patch": {"hunks": []},
                }
            }
        )
        provider = OpenCodeFileProvider(oc, NODE)
        payload = await provider.read("/work/demo", "README.md")
        self.assertEqual(payload, {"type": "text", "content": "hello"})
        with self.assertRaises(FileTooLarge):
            content_payload({"type": "text", "content": "x" * (5 * 1024 * 1024 + 1)})

    async def test_file_status(self):
        oc = FakeOc(
            {
                "/vcs/status": [
                    {"file": "app/main.py", "status": "modified", "additions": 3, "deletions": 1},
                    {"bad": True},
                ]
            }
        )
        provider = OpenCodeFileProvider(oc, NODE)
        items = await provider.status("/work/demo")
        self.assertEqual(
            items,
            [{"path": "app/main.py", "status": "modified", "added": 3, "removed": 1}],
        )

    async def test_file_service_uses_factory(self):
        oc = FakeOc({})
        sentinel = object()
        service = FileService(lambda _oc, _node: sentinel)
        self.assertIs(service.provider(oc, NODE), sentinel)
        self.assertIsInstance(FileService().provider(oc, NODE), OpenCodeFileProvider)

    async def test_diff_filters_file(self):
        oc = FakeOc(
            {
                "/vcs/diff": [
                    {
                        "file": "app/main.py",
                        "status": "modified",
                        "additions": 2,
                        "deletions": 1,
                        "patch": "diff --git a/app/main.py b/app/main.py",
                    },
                    {"file": "other.py", "patch": "x"},
                ]
            }
        )
        provider = OpenCodeFileProvider(oc, NODE)
        diff = await provider.diff("/work/demo", "app/main.py")
        self.assertIsNotNone(diff)
        self.assertEqual(diff["status"], "modified")
        self.assertEqual(diff["additions"], 2)
        self.assertEqual(diff["deletions"], 1)
        self.assertFalse(diff["truncated"])
        self.assertIsNone(await provider.diff("/work/demo", "missing.py"))

    async def test_find_files(self):
        oc = FakeOc({"/find/file": ["app/main.py", "README.md"]})
        provider = OpenCodeFileProvider(oc, NODE)
        items = await provider.find("/work/demo", "main", limit=20)
        self.assertEqual(items, ["app/main.py", "README.md"])
        self.assertEqual(oc.calls[0][1]["limit"], 20)


if __name__ == "__main__":
    unittest.main()
