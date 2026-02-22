from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

log = logging.getLogger(__name__)

# Constants

REP_MIN = 0
REP_MAX = 1000
REP_DEFAULT = 500

# Tier helper (pure function — no DB needed)

def get_reputation_tier(score: int) -> str:
    if score >= 800:
        return "Elite"
    if score >= 600:
        return "Verified"
    if score >= 400:
        return "Trusted"
    if score >= 200:
        return "Caution"
    return "Unverified"

TIER_EMOJI: dict[str, str] = {
    "Elite":      "🏆",
    "Verified":   "✅",
    "Trusted":    "🤝",
    "Caution":    "⚠️",
    "Unverified": "❓",
}

TIER_COLOR: dict[str, int] = {
    "Elite":      0xFFD700,  # gold
    "Verified":   0x57F287,  # green
    "Trusted":    0x5865F2,  # blurple
    "Caution":    0xFEE75C,  # yellow
    "Unverified": 0x99AAB5,  # grey
}

# Host reputation read/write

async def get_host_reputation(
    db: aiosqlite.Connection, discord_id: int
) -> Optional[aiosqlite.Row]:
    """Return the full hosts row for the given discord_id, or None."""
    async with db.execute(
        "SELECT * FROM hosts WHERE discord_id=?", (discord_id,)
    ) as cur:
        return await cur.fetchone()

async def ensure_host_exists(db: aiosqlite.Connection, discord_id: int) -> None:
    """Create a hosts row with default reputation if one doesn't exist."""
    await db.execute(
        "INSERT OR IGNORE INTO hosts (discord_id) VALUES (?)", (discord_id,)
    )
    await db.commit()

async def apply_reputation_delta(
    db: aiosqlite.Connection,
    discord_id: int,
    delta: int,
    deal_id: Optional[int],
    reason: str,
    *,
    increment_completed: bool = False,
    increment_failed: bool = False,
    increment_strikes: bool = False,
    increment_disputes: bool = False,
    reset_consecutive: bool = False,
    increment_consecutive: bool = False,
) -> bool:
    """
    Atomically update reputation for a host.

    Returns True if the update was applied, False if it was a duplicate
    (same host_id + deal_id + reason already logged).
    """
    await ensure_host_exists(db, discord_id)

    # ── de-duplication guard ────────────────────────────────────────────────
    if deal_id is not None:
        async with db.execute(
            "SELECT id FROM reputation_log WHERE host_id=? AND deal_id=? AND reason=?",
            (discord_id, deal_id, reason),
        ) as cur:
            if await cur.fetchone():
                log.debug(
                    f"Skipping duplicate reputation update: host={discord_id} "
                    f"deal={deal_id} reason={reason}"
                )
                return False

    # ── fetch current score ─────────────────────────────────────────────────
    async with db.execute(
        "SELECT reputation, consecutive_clean_deals FROM hosts WHERE discord_id=?",
        (discord_id,),
    ) as cur:
        row = await cur.fetchone()

    current_rep = row["reputation"] if row else REP_DEFAULT
    current_consec = row["consecutive_clean_deals"] if row else 0

    new_rep = max(REP_MIN, min(REP_MAX, current_rep + delta))

    # ── build SET clause dynamically ────────────────────────────────────────
    parts = [
        "reputation = ?",
        "last_reputation_update = CURRENT_TIMESTAMP",
    ]
    params: list = [new_rep]

    if increment_completed:
        parts.append("completed_deals = completed_deals + 1")
    if increment_failed:
        parts.append("failed_deals = failed_deals + 1")
    if increment_strikes:
        parts.append("strikes = strikes + 1")
    if increment_disputes:
        parts.append("dispute_count = dispute_count + 1")
    if reset_consecutive:
        parts.append("consecutive_clean_deals = 0")
    elif increment_consecutive:
        new_consec = current_consec + 1
        parts.append("consecutive_clean_deals = ?")
        params.append(new_consec)

    params.append(discord_id)
    await db.execute(
        f"UPDATE hosts SET {', '.join(parts)} WHERE discord_id=?",
        params,
    )

    # ── log entry ───────────────────────────────────────────────────────────
    await db.execute(
        """
        INSERT OR IGNORE INTO reputation_log (host_id, deal_id, change_amount, reason)
        VALUES (?, ?, ?, ?)
        """,
        (discord_id, deal_id, delta, reason),
    )

    await db.commit()
    return True

async def admin_adjust_reputation(
    db: aiosqlite.Connection,
    discord_id: int,
    delta: int,
    reason: str,
) -> tuple[int, int]:
    """
    Admin manual adjustment — always applied (no deal_id, no de-dup).
    Returns (old_score, new_score).
    """
    await ensure_host_exists(db, discord_id)

    async with db.execute(
        "SELECT reputation FROM hosts WHERE discord_id=?", (discord_id,)
    ) as cur:
        row = await cur.fetchone()

    old = row["reputation"] if row else REP_DEFAULT
    new = max(REP_MIN, min(REP_MAX, old + delta))

    await db.execute(
        "UPDATE hosts SET reputation=?, last_reputation_update=CURRENT_TIMESTAMP WHERE discord_id=?",
        (new, discord_id),
    )
    # Admin adjustments use deal_id=NULL — logged but NOT de-duped
    await db.execute(
        "INSERT INTO reputation_log (host_id, deal_id, change_amount, reason) VALUES (?, NULL, ?, ?)",
        (discord_id, delta, reason),
    )
    await db.commit()
    return old, new

# Leaderboard

async def get_reputation_leaderboard(
    db: aiosqlite.Connection,
    limit: int = 10,
) -> list[aiosqlite.Row]:
    """Return top N hosts by reputation score."""
    async with db.execute(
        """
        SELECT h.discord_id,
               h.reputation,
               h.completed_deals,
               h.failed_deals,
               h.dispute_count,
               h.strikes
        FROM hosts h
        ORDER BY h.reputation DESC, h.completed_deals DESC
        LIMIT ?
        """,
        (limit,),
    ) as cur:
        return await cur.fetchall()

# Agency reputation summary

async def get_agency_reputation_summary(
    db: aiosqlite.Connection, agency_id: int
) -> dict:
    """
    Returns avg, max, min reputation and total disputes for an agency's
    active hosts.
    """
    async with db.execute(
        """
        SELECT
            AVG(h.reputation)   AS avg_rep,
            MAX(h.reputation)   AS max_rep,
            MIN(h.reputation)   AS min_rep,
            SUM(h.dispute_count) AS total_disputes,
            COUNT(*)            AS host_count
        FROM hosts h
        WHERE h.agency_id=? AND h.join_status='active'
        """,
        (agency_id,),
    ) as cur:
        row = await cur.fetchone()

    return {
        "avg_rep":       round(row["avg_rep"] or 0, 1),
        "max_rep":       row["max_rep"] or 0,
        "min_rep":       row["min_rep"] or 0,
        "total_disputes": row["total_disputes"] or 0,
        "host_count":    row["host_count"] or 0,
    }

# Reputation history for a host

async def get_reputation_log(
    db: aiosqlite.Connection,
    discord_id: int,
    limit: int = 20,
) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT id, deal_id, change_amount, reason, created_at
        FROM reputation_log
        WHERE host_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (discord_id, limit),
    ) as cur:
        return await cur.fetchall()
