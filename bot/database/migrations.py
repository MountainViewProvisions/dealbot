from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

async def _add_column_if_missing(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        cols = [row["name"] for row in await cur.fetchall()]
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        log.info(f"Migration: added {table}.{column}")

async def run_migrations(db: aiosqlite.Connection) -> None:
    log.info("Running schema migrations…")

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id   INTEGER UNIQUE NOT NULL,
            discord_name TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS networks (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        INSERT OR IGNORE INTO networks (name) VALUES
            ('BigoLive'), ('TikTok'), ('Tango'),
            ('YouTube'), ('Twitch'), ('Other');

        CREATE TABLE IF NOT EXISTS user_network_profiles (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            network_id        INTEGER NOT NULL REFERENCES networks(id) ON DELETE CASCADE,
            platform_username TEXT NOT NULL,
            UNIQUE(user_id, network_id)
        );

        CREATE TABLE IF NOT EXISTS deals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_uuid           TEXT UNIQUE NOT NULL,
            guild_id            INTEGER,
            network_id          INTEGER NOT NULL REFERENCES networks(id),
            party_a_profile_id  INTEGER NOT NULL REFERENCES user_network_profiles(id),
            party_b_profile_id  INTEGER NOT NULL REFERENCES user_network_profiles(id),
            deal_type           TEXT NOT NULL,
            amount              REAL NOT NULL,
            currency            TEXT NOT NULL DEFAULT 'USD',
            due_date            TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'pending_confirmation',
            disputed_reason     TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS deal_confirmations (
            deal_id                   INTEGER PRIMARY KEY REFERENCES deals(id) ON DELETE CASCADE,
            party_a_confirmed         INTEGER NOT NULL DEFAULT 0,
            party_b_confirmed         INTEGER NOT NULL DEFAULT 0,
            completion_confirmed_by_a INTEGER NOT NULL DEFAULT 0,
            completion_confirmed_by_b INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS deal_notes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id           INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
            author_profile_id INTEGER NOT NULL REFERENCES user_network_profiles(id),
            note_text         TEXT NOT NULL,
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS deal_audit_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id           INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
            actor_profile_id  INTEGER REFERENCES user_network_profiles(id),
            previous_status   TEXT NOT NULL,
            new_status        TEXT NOT NULL,
            action_type       TEXT NOT NULL,
            metadata          TEXT,
            timestamp         TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_rate_limits (
            user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            last_dispute_at    TEXT,
            daily_volume_date  TEXT,
            daily_volume_total REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS server_config (
            guild_id               INTEGER PRIMARY KEY,
            reminder_freq_minutes  INTEGER NOT NULL DEFAULT 30,
            weekly_summary_enabled INTEGER NOT NULL DEFAULT 1,
            mediator_role_name     TEXT    NOT NULL DEFAULT 'DealMediator',
            max_open_deals         INTEGER NOT NULL DEFAULT 10,
            max_daily_volume       REAL    NOT NULL DEFAULT 50000,
            dispute_cooldown_hours INTEGER NOT NULL DEFAULT 24
        );

        CREATE TABLE IF NOT EXISTS command_usage_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER,
            user_id     INTEGER,
            command     TEXT NOT NULL,
            success     INTEGER NOT NULL DEFAULT 1,
            detail      TEXT,
            timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_deals_a        ON deals(party_a_profile_id);
        CREATE INDEX IF NOT EXISTS idx_deals_b        ON deals(party_b_profile_id);
        CREATE INDEX IF NOT EXISTS idx_deals_status   ON deals(status);
        CREATE INDEX IF NOT EXISTS idx_deals_due      ON deals(due_date);
        CREATE INDEX IF NOT EXISTS idx_deals_guild    ON deals(guild_id);
        CREATE INDEX IF NOT EXISTS idx_audit_deal     ON deal_audit_log(deal_id);
        CREATE INDEX IF NOT EXISTS idx_notes_deal     ON deal_notes(deal_id);
        CREATE INDEX IF NOT EXISTS idx_cmd_log_guild  ON command_usage_log(guild_id);
        CREATE INDEX IF NOT EXISTS idx_cmd_log_user   ON command_usage_log(user_id);
    """)

    await _add_column_if_missing(db, "deals", "guild_id", "INTEGER")

    await db.commit()
    log.info("Migrations complete.")
