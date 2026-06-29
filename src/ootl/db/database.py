"""Async-friendly SQLite wrapper.

SQLite calls are synchronous; to avoid blocking the asyncio event loop we run
each operation in a worker thread via ``asyncio.to_thread``. A single shared
connection (``check_same_thread=False``) is used with WAL journaling, which is
plenty for a chat-bot's workload. Writes are serialised with a lock so the
shared connection is never used concurrently.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------
    async def connect(self) -> None:
        """Open the connection (creating the file/dir) and apply the schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open)
        await self.init_schema()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def init_schema(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        await self.executescript(schema)

    async def close(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await asyncio.to_thread(conn.close)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; call connect() first.")
        return self._conn

    # -- core operations -----------------------------------------------------
    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """Run a write statement; returns lastrowid (or rowcount for updates)."""
        async with self._lock:
            return await asyncio.to_thread(self._execute, sql, params or ())

    def _execute(self, sql: str, params: Sequence[Any]) -> int:
        cur = self.connection.execute(sql, params)
        self.connection.commit()
        return cur.lastrowid if cur.lastrowid else cur.rowcount

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._executemany, sql, list(seq))

    def _executemany(self, sql: str, seq: list[Sequence[Any]]) -> None:
        self.connection.executemany(sql, seq)
        self.connection.commit()

    async def executescript(self, script: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._executescript, script)

    def _executescript(self, script: str) -> None:
        self.connection.executescript(script)
        self.connection.commit()

    async def fetchone(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> sqlite3.Row | None:
        async with self._lock:
            return await asyncio.to_thread(self._fetchone, sql, params or ())

    def _fetchone(self, sql: str, params: Sequence[Any]) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    async def fetchall(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> list[sqlite3.Row]:
        async with self._lock:
            return await asyncio.to_thread(self._fetchall, sql, params or ())

    def _fetchall(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()
