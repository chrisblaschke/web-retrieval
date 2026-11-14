"""Shared SQLite cache (one file, WAL) plus in-flight request sharing.

Every agent talks to one service, so a page fetched for one agent is free for the next, and two
agents asking for the same page at the same moment share one download (SingleFlight).
"""

import asyncio
import json
import os
import sqlite3
import threading
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any


class Cache:
    def __init__(self, path: str, max_mb: int, version: str = ""):
        self.version = version
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.max_bytes = max_mb * 1024 * 1024
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma synchronous=normal")
        self.db.execute(
            "create table if not exists kv (k text primary key, v blob not null, expires real not null, created real not null)"
        )
        self.db.execute("create index if not exists kv_created on kv(created)")
        self.hits = 0
        self.misses = 0

    def get(self, ns: str, key: str) -> Any | None:
        with self.lock:
            row = self.db.execute("select v, expires from kv where k = ?", (f"{self.version}:{ns}:{key}",)).fetchone()
        if not row or row[1] < time.time():
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(zlib.decompress(row[0]))

    def set(self, ns: str, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        blob = zlib.compress(json.dumps(value, ensure_ascii=False).encode(), 6)
        now = time.time()
        with self.lock:
            self.db.execute(
                "insert or replace into kv (k, v, expires, created) values (?, ?, ?, ?)",
                (f"{self.version}:{ns}:{key}", blob, now + ttl, now),
            )

    def purge(self) -> int:
        """Drop expired rows, then the oldest rows while the file is over its size budget."""
        with self.lock:
            n = self.db.execute("delete from kv where expires < ?", (time.time(),)).rowcount
            size = os.path.getsize(self.path)
            if size > self.max_bytes:
                total = self.db.execute("select count(*) from kv").fetchone()[0]
                drop = max(1, total // 4)
                n += self.db.execute(
                    "delete from kv where k in (select k from kv order by created limit ?)", (drop,)
                ).rowcount
                self.db.execute("pragma wal_checkpoint(truncate)")
                self.db.execute("vacuum")
        return n

    def stats(self) -> dict:
        with self.lock:
            rows = self.db.execute("select count(*) from kv").fetchone()[0]
        size = os.path.getsize(self.path)
        total = self.hits + self.misses
        return {
            "rows": rows,
            "code_version": self.version,
            "mb": round(size / 1048576, 1),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else None,
        }


class SingleFlight:
    """Concurrent callers with the same key share one execution.

    The work runs as its own task, so a caller that disconnects doesn't cancel it for the others.
    """

    def __init__(self):
        self.inflight: dict[str, asyncio.Task] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]:
        task = self.inflight.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.create_task(fn())
            self.inflight[key] = task

            def done(t: asyncio.Task, key=key):
                self.inflight.pop(key, None)
                if not t.cancelled():
                    t.exception()  # mark retrieved; callers still see it

            task.add_done_callback(done)
        return await asyncio.shield(task), shared
