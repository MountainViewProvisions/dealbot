from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

async def run_reputation_migrations(db: aiosqlite.Connection) -> None:
    """
    Safely extend the hosts table with reputation columns and
    create the reputation_log table.  All operations are idempotent.
    """
    log.info("Running reputation schema migrations…")

    # ── reputation_log table ────────────────────────────────────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reputation_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id       INTEGER NOT NULL,
            deal_id       INTEGER,
            change_amount INTEGER NOT NULL,
            reason        TEXT    NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Unique guard: one entry per (host_id, deal_id, reason)
    # deal_id can be NULL for manual admin adjustments — those are not de-duped
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rep_log_host_deal_reason
        ON reputation_log (host_id, deal_id, reason)
        WHERE deal_id IS NOT NULL
    """)

    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_rep_log_host
        ON reputation_log (host_id)
    """)

    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_rep_log_deal
        ON reputation_log (deal_id)
    """)

    # ── extend hosts table with reputation columns ───────────────────────────
    async def _add_col(table: str, col: str, definition: str) -> None:
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if col not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            log.info(f"  Migration: added {table}.{col}")

    await _add_col("hosts", "reputation",              "INTEGER NOT NULL DEFAULT 500")
    await _add_col("hosts", "strikes",                 "INTEGER NOT NULL DEFAULT 0")
    await _add_col("hosts", "completed_deals",         "INTEGER NOT NULL DEFAULT 0")
    await _add_col("hosts", "failed_deals",            "INTEGER NOT NULL DEFAULT 0")
    await _add_col("hosts", "dispute_count",           "INTEGER NOT NULL DEFAULT 0")
    await _add_col("hosts", "consecutive_clean_deals", "INTEGER NOT NULL DEFAULT 0")
    await _add_col("hosts", "last_reputation_update",  "TIMESTAMP")

    await db.commit()
    log.info("Reputation migrations complete.")
