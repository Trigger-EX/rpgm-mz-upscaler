"""Small sqlite cache for model translations (the glossary is cheap and is never cached)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class Cache:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._mem: dict[tuple[str, str], str] = {}
        self._db: sqlite3.Connection | None = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(str(path), check_same_thread=False)
                self._db.execute("CREATE TABLE IF NOT EXISTS t (text TEXT, backend TEXT, result TEXT, ts REAL, PRIMARY KEY (text, backend))")
                self._db.commit()
            except (OSError, sqlite3.Error):
                self._db = None

    def get(self, text: str, backend: str) -> str | None:
        with self._lock:
            if (text, backend) in self._mem:
                return self._mem[(text, backend)]
            if self._db is not None:
                row = self._db.execute("SELECT result FROM t WHERE text=? AND backend=?", (text, backend)).fetchone()
                if row:
                    self._mem[(text, backend)] = row[0]
                    return row[0]
        return None

    def put(self, text: str, backend: str, result: str) -> None:
        with self._lock:
            self._mem[(text, backend)] = result
            if self._db is not None:
                try:
                    self._db.execute("INSERT OR REPLACE INTO t VALUES (?,?,?,?)", (text, backend, result, time.time()))
                    self._db.commit()
                except sqlite3.Error:
                    pass

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
            if self._db is not None:
                self._db.execute("DELETE FROM t")
                self._db.commit()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
