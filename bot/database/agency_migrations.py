from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

async def run_agency_migrations(db: aiosqlite.Connection) -> None:
    """Create agency-related tables if they don't exist."""
    log.info("Running agency schema migrations…")

    await db.executescript("""
        -- Pending agency registration requests
        CREATE TABLE IF NOT EXISTS agency_requests (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_name          TEXT    UNIQUE NOT NULL,
            requester_discord_id INTEGER NOT NULL,
            requested_at         TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- Approved agencies
        CREATE TABLE IF NOT EXISTS agencies (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT    UNIQUE NOT NULL,
            owner_discord_id  INTEGER,
            status            TEXT    NOT NULL DEFAULT 'pending',
            created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- Hosts linked to agencies
        CREATE TABLE IF NOT EXISTS hosts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id  INTEGER UNIQUE NOT NULL,
            agency_id   INTEGER REFERENCES agencies(id) ON DELETE SET NULL,
            join_status TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Notes / flags that agency owners add to hosts
        CREATE TABLE IF NOT EXISTS host_notes (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            host_discord_id    INTEGER NOT NULL,
            author_discord_id  INTEGER NOT NULL,
            note               TEXT    NOT NULL,
            created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_agencies_name        ON agencies(name);
        CREATE INDEX IF NOT EXISTS idx_hosts_agency         ON hosts(agency_id);
        CREATE INDEX IF NOT EXISTS idx_host_notes_host      ON host_notes(host_discord_id);
        CREATE INDEX IF NOT EXISTS idx_agency_requests_name ON agency_requests(agency_name);
    """)

    await db.commit()
    log.info("Agency migrations complete.")
