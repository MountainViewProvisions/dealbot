from __future__ import annotations

import logging
import os
from typing import Optional

import aiosqlite

from bot.database.migrations import run_migrations

log = logging.getLogger(__name__)

_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db


async def init_db() -> None:
    global _db
    path = os.getenv("DATABASE_PATH", "./dealbot.db")
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.commit()
    log.info(f"Database opened: {path}")
    await run_migrations(_db)


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
        log.info("Database connection closed.")
