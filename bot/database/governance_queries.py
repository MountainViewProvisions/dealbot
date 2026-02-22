from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

log = logging.getLogger(__name__)

_DEFAULTS = {
    "probation_threshold":       300.0,
    "probation_max_open_deals":  2,
    "high_value_deal_threshold": 5000.0,
    "required_confidence_score": 350.0,
    "escrow_mediator_enabled":   1,
    "analytics_weekly_dm":       1,
}


async def get_governance_config(
    db: aiosqlite.Connection, guild_id: Optional[int]
) -> dict:
    """Return governance config for a guild, falling back to defaults."""
    if guild_id:
        async with db.execute(
            "SELECT * FROM governance_config WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return dict(row)
    return {**_DEFAULTS, "guild_id": guild_id or 0}


async def upsert_governance_config(
    db: aiosqlite.Connection,
    guild_id: int,
    probation_threshold: float,
    probation_max_open_deals: int,
    high_value_deal_threshold: float,
    required_confidence_score: float,
    escrow_mediator_enabled: bool,
    analytics_weekly_dm: bool,
) -> None:
    await db.execute(
        """
        INSERT INTO governance_config (
            guild_id, probation_threshold, probation_max_open_deals,
            high_value_deal_threshold, required_confidence_score,
            escrow_mediator_enabled, analytics_weekly_dm, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(guild_id) DO UPDATE SET
            probation_threshold       = excluded.probation_threshold,
            probation_max_open_deals  = excluded.probation_max_open_deals,
            high_value_deal_threshold = excluded.high_value_deal_threshold,
            required_confidence_score = excluded.required_confidence_score,
            escrow_mediator_enabled   = excluded.escrow_mediator_enabled,
            analytics_weekly_dm       = excluded.analytics_weekly_dm,
            updated_at                = CURRENT_TIMESTAMP
        """,
        (
            guild_id,
            probation_threshold,
            probation_max_open_deals,
            high_value_deal_threshold,   # stored as REAL; do not truncate to int
            required_confidence_score,
            1 if escrow_mediator_enabled else 0,
            1 if analytics_weekly_dm else 0,
        ),
    )
    await db.commit()


async def set_probation(
    db: aiosqlite.Connection,
    discord_id: int,
    on_probation: bool,
    reputation_at: int,
    triggered_by: str = "system",
) -> None:
    """Set or clear probation flag on the hosts row and log the event."""
    await db.execute(
        "INSERT OR IGNORE INTO hosts (discord_id) VALUES (?)", (discord_id,)
    )
    if on_probation:
        await db.execute(
            """
            UPDATE hosts
            SET on_probation=1, probation_since=CURRENT_TIMESTAMP
            WHERE discord_id=?
            """,
            (discord_id,),
        )
        event = "probation_entered"
    else:
        await db.execute(
            """
            UPDATE hosts
            SET on_probation=0, probation_since=NULL
            WHERE discord_id=?
            """,
            (discord_id,),
        )
        event = "probation_lifted"

    await db.execute(
        """
        INSERT INTO probation_log (discord_id, event, reputation_at, triggered_by)
        VALUES (?, ?, ?, ?)
        """,
        (discord_id, event, reputation_at, triggered_by),
    )
    await db.commit()


async def get_probation_status(
    db: aiosqlite.Connection, discord_id: int
) -> Optional[aiosqlite.Row]:
    """Return hosts row fields relevant to probation, or None."""
    async with db.execute(
        """
        SELECT discord_id, on_probation, probation_since,
               reputation, agency_deal_approved
        FROM hosts WHERE discord_id=?
        """,
        (discord_id,),
    ) as cur:
        return await cur.fetchone()


async def get_probation_log(
    db: aiosqlite.Connection, discord_id: int, limit: int = 10
) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT event, reputation_at, triggered_by, created_at
        FROM probation_log
        WHERE discord_id=?
        ORDER BY created_at DESC LIMIT ?
        """,
        (discord_id, limit),
    ) as cur:
        return await cur.fetchall()


async def set_agency_deal_approved(
    db: aiosqlite.Connection, discord_id: int, approved: bool
) -> None:
    await db.execute(
        "UPDATE hosts SET agency_deal_approved=? WHERE discord_id=?",
        (1 if approved else 0, discord_id),
    )
    await db.commit()


async def get_all_probation_hosts(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT discord_id, reputation, probation_since, agency_id
        FROM hosts WHERE on_probation=1
        """,
    ) as cur:
        return await cur.fetchall()


async def create_escrow_flag(
    db: aiosqlite.Connection,
    deal_id: int,
    confidence_a: float,
    confidence_b: float,
    mediator_assigned: bool = False,
    blocked: bool = False,
    block_reason: Optional[str] = None,
) -> None:
    await db.execute(
        """
        INSERT OR REPLACE INTO escrow_flags
            (deal_id, confidence_score_a, confidence_score_b,
             mediator_assigned, blocked, block_reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            deal_id,
            confidence_a,
            confidence_b,
            1 if mediator_assigned else 0,
            1 if blocked else 0,
            block_reason,
        ),
    )
    await db.commit()


async def get_escrow_flag(
    db: aiosqlite.Connection, deal_id: int
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM escrow_flags WHERE deal_id=?", (deal_id,)
    ) as cur:
        return await cur.fetchone()


async def get_agency_analytics(
    db: aiosqlite.Connection,
    agency_id: int,
    days: int = 7,
) -> dict:
    """
    Full analytics snapshot for one agency over the last N days.
    Returns a plain dict ready for embed rendering.
    """
    async with db.execute(
        """
        SELECT
            COUNT(*)                                        AS total_deals,
            SUM(CASE WHEN d.status='completed'  THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN d.status='defaulted'  THEN 1 ELSE 0 END) AS defaulted,
            SUM(CASE WHEN d.status='disputed'   THEN 1 ELSE 0 END) AS disputed,
            SUM(CASE WHEN d.status='overdue'    THEN 1 ELSE 0 END) AS overdue,
            SUM(CASE WHEN d.status='cancelled'  THEN 1 ELSE 0 END) AS cancelled,
            COALESCE(SUM(d.amount), 0.0)                   AS total_volume
        FROM deals d
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id = d.party_b_profile_id
        JOIN users ua ON ua.id = pa.user_id
        JOIN users ub ON ub.id = pb.user_id
        LEFT JOIN hosts ha ON ha.discord_id = ua.discord_id
        LEFT JOIN hosts hb ON hb.discord_id = ub.discord_id
        WHERE (ha.agency_id=? OR hb.agency_id=?)
          AND (ha.join_status='active' OR hb.join_status='active')
          AND d.created_at >= datetime('now', ? || ' days')
        """,
        (agency_id, agency_id, f"-{days}"),
    ) as cur:
        deal_row = await cur.fetchone()

    async with db.execute(
        """
        SELECT h.discord_id, h.reputation, h.completed_deals,
               h.failed_deals, h.dispute_count, h.on_probation
        FROM hosts h
        WHERE h.agency_id=? AND h.join_status='active'
        ORDER BY h.reputation DESC
        LIMIT 10
        """,
        (agency_id,),
    ) as cur:
        host_rankings = await cur.fetchall()

    async with db.execute(
        "SELECT COUNT(*) AS cnt FROM hosts WHERE agency_id=? AND on_probation=1",
        (agency_id,),
    ) as cur:
        prob_row = await cur.fetchone()

    async with db.execute(
        "SELECT COUNT(*) AS cnt FROM hosts WHERE agency_id=? AND join_status='active'",
        (agency_id,),
    ) as cur:
        active_row = await cur.fetchone()

    total = deal_row["total_deals"] or 0
    completed = deal_row["completed"] or 0
    disputed = deal_row["disputed"] or 0

    clean_pct = round((completed / total * 100), 1) if total > 0 else 0.0
    dispute_ratio = round((disputed / total * 100), 1) if total > 0 else 0.0

    return {
        "days":            days,
        "total_deals":     total,
        "completed":       completed,
        "defaulted":       deal_row["defaulted"] or 0,
        "disputed":        disputed,
        "overdue":         deal_row["overdue"] or 0,
        "cancelled":       deal_row["cancelled"] or 0,
        "total_volume":    float(deal_row["total_volume"] or 0),
        "clean_pct":       clean_pct,
        "dispute_ratio":   dispute_ratio,
        "host_rankings":   [dict(r) for r in host_rankings],
        "probation_count": prob_row["cnt"] if prob_row else 0,
        "active_hosts":    active_row["cnt"] if active_row else 0,
    }


async def get_guild_analytics(
    db: aiosqlite.Connection,
    guild_id: int,
    days: int = 7,
) -> dict:
    """Guild-wide analytics for bot owners."""
    async with db.execute(
        """
        SELECT
            COUNT(*)                                                 AS total_deals,
            SUM(CASE WHEN d.status='completed'  THEN 1 ELSE 0 END)  AS completed,
            SUM(CASE WHEN d.status='defaulted'  THEN 1 ELSE 0 END)  AS defaulted,
            SUM(CASE WHEN d.status='disputed'   THEN 1 ELSE 0 END)  AS disputed,
            SUM(CASE WHEN d.status='overdue'    THEN 1 ELSE 0 END)  AS overdue,
            COALESCE(SUM(d.amount), 0.0)                             AS total_volume,
            COUNT(DISTINCT d.party_a_profile_id)                     AS unique_hosts
        FROM deals d
        WHERE d.guild_id=?
          AND d.created_at >= datetime('now', ? || ' days')
        """,
        (guild_id, f"-{days}"),
    ) as cur:
        row = await cur.fetchone()

    async with db.execute(
        "SELECT COUNT(*) AS cnt FROM hosts WHERE on_probation=1"
    ) as cur:
        prob_row = await cur.fetchone()

    async with db.execute(
        "SELECT COUNT(*) AS cnt FROM agencies WHERE status='approved'"
    ) as cur:
        agency_row = await cur.fetchone()

    total = row["total_deals"] or 0
    completed = row["completed"] or 0
    disputed = row["disputed"] or 0

    return {
        "days":            days,
        "total_deals":     total,
        "completed":       completed,
        "defaulted":       row["defaulted"] or 0,
        "disputed":        disputed,
        "overdue":         row["overdue"] or 0,
        "total_volume":    float(row["total_volume"] or 0),
        "unique_hosts":    row["unique_hosts"] or 0,
        "clean_pct":       round(completed / total * 100, 1) if total > 0 else 0.0,
        "dispute_ratio":   round(disputed / total * 100, 1) if total > 0 else 0.0,
        "probation_count": prob_row["cnt"] if prob_row else 0,
        "agency_count":    agency_row["cnt"] if agency_row else 0,
    }
