from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.store import FavoriteStore


class FavoriteStoreTests(unittest.TestCase):
    def test_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.db"
            store = FavoriteStore(path)
            store.add("node-a", "/work/demo")
            store.add("node-a", "/work/demo")
            store.add("node-b", "/work/other")
            self.assertEqual(len(store.list()), 2)
            store.close()

            reopened = FavoriteStore(path)
            keys = {(item["node"], item["path"]) for item in reopened.list()}
            self.assertEqual(keys, {("node-a", "/work/demo"), ("node-b", "/work/other")})
            reopened.remove("node-a", "/work/demo")
            self.assertEqual([item["node"] for item in reopened.list()], ["node-b"])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
