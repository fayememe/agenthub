from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class FavoriteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS favorites ("
            "node TEXT NOT NULL, "
            "path TEXT NOT NULL, "
            "created_at INTEGER NOT NULL, "
            "PRIMARY KEY (node, path))"
        )
        self._conn.commit()

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT node, path, created_at FROM favorites ORDER BY created_at DESC"
            ).fetchall()
        return [{"node": row[0], "path": row[1], "created_at": row[2]} for row in rows]

    def add(self, node: str, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO favorites (node, path, created_at) VALUES (?, ?, ?)",
                (node, path, int(time.time() * 1000)),
            )
            self._conn.commit()

    def remove(self, node: str, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM favorites WHERE node = ? AND path = ?",
                (node, path),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
