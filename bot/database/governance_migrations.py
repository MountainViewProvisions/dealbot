from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

async def run_governance_migrations(db: aiosqlite.Connection) -> None:
    """
    Create governance tables and extend existing ones safely.
    All operations are idempotent (IF NOT EXISTS / ALTER TABLE guards).
    """
    log.info("Running governance schema migrations…")

    # ── governance_config — per-guild tunable thresholds ───────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS governance_config (
            guild_id                  INTEGER PRIMARY KEY,
            probation_threshold       REAL    NOT NULL DEFAULT 300.0,
            probation_max_open_deals  INTEGER NOT NULL DEFAULT 2,
            high_value_deal_threshold REAL    NOT NULL DEFAULT 5000.0,
            required_confidence_score REAL    NOT NULL DEFAULT 350.0,
            escrow_mediator_enabled   INTEGER NOT NULL DEFAULT 1,
            analytics_weekly_dm       INTEGER NOT NULL DEFAULT 1,
            updated_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── probation_log — history of probation state changes ─────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS probation_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id     INTEGER NOT NULL,
            event          TEXT    NOT NULL,
            reputation_at  INTEGER NOT NULL,
            triggered_by   TEXT    NOT NULL DEFAULT 'system',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_probation_log_user
        ON probation_log (discord_id)
    """)

    # ── escrow_flags — per-deal escrow/mediator assignment record ──────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS escrow_flags (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id             INTEGER NOT NULL UNIQUE,
            confidence_score_a  REAL    NOT NULL DEFAULT 0.0,
            confidence_score_b  REAL    NOT NULL DEFAULT 0.0,
            mediator_assigned   INTEGER NOT NULL DEFAULT 0,
            blocked             INTEGER NOT NULL DEFAULT 0,
            block_reason        TEXT,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_escrow_flags_deal
        ON escrow_flags (deal_id)
    """)

    # ── Extend hosts table with probation columns ───────────────────────────
    async def _add_col(col: str, definition: str) -> None:
        async with db.execute("PRAGMA table_info(hosts)") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if col not in cols:
            await db.execute(f"ALTER TABLE hosts ADD COLUMN {col} {definition}")
            log.info(f"  Migration: added hosts.{col}")

    await _add_col("on_probation",        "INTEGER NOT NULL DEFAULT 0")
    await _add_col("probation_since",     "TIMESTAMP")
    await _add_col("agency_deal_approved", "INTEGER NOT NULL DEFAULT 1")

    await db.commit()
    log.info("Governance migrations complete.")
