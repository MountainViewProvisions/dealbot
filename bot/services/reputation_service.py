from __future__ import annotations

"""
reputation_service.py
=====================
Single source of truth for all reputation mutations.

Rules
-----
- Reputation range: 0–1000 (clamped, never outside)
- Starting value: 500
- On deal COMPLETED (on-time):    +10 rep, completed_deals+1
  └─ If 10 consecutive clean deals: +25 bonus
- On deal COMPLETED (was OVERDUE): -15 rep, completed_deals+1, reset consecutive
- On deal DEFAULTED:               -40 rep, failed_deals+1, reset consecutive
- On confirmed SCAM (admin):       -100 rep, strikes+1, reset consecutive
- On deal DISPUTED (escalated):    dispute_count+1 (no rep change at escalation)
- Admin manual adjust:             arbitrary delta, always applied
"""

import logging
from typing import Optional

import aiosqlite

from bot.database.reputation_queries import (
    apply_reputation_delta,
    admin_adjust_reputation,
    get_host_reputation,
    ensure_host_exists,
)

log = logging.getLogger("dealbot.reputation_service")

# Reason string constants — used as de-dup keys in reputation_log
REASON_COMPLETED_ONTIME  = "deal_completed_ontime"
REASON_COMPLETED_LATE    = "deal_completed_late"
REASON_FAILED            = "deal_failed"
REASON_SCAM              = "deal_scam_confirmed"
REASON_DISPUTE_ESCALATED = "deal_dispute_escalated"
REASON_STREAK_BONUS      = "streak_bonus_10_clean"

STREAK_THRESHOLD = 10

# Public API

async def on_deal_completed(
    db: aiosqlite.Connection,
    deal_id: int,
    party_a_discord_id: int,
    party_b_discord_id: int,
    was_overdue: bool = False,
) -> None:
    """Call when a deal transitions to COMPLETED. Updates both parties."""
    for discord_id in (party_a_discord_id, party_b_discord_id):
        await _handle_completed(db, discord_id, deal_id, was_overdue)
        await _evaluate_probation_safe(db, discord_id)

async def on_deal_defaulted(
    db: aiosqlite.Connection,
    deal_id: int,
    defaulting_discord_id: int,
) -> None:
    """Call when a deal is resolved as DEFAULTED for a specific party."""
    applied = await apply_reputation_delta(
        db,
        discord_id=defaulting_discord_id,
        delta=-40,
        deal_id=deal_id,
        reason=REASON_FAILED,
        increment_failed=True,
        reset_consecutive=True,
    )
    if applied:
        log.info(f"Reputation: host {defaulting_discord_id} -40 (defaulted) | deal={deal_id}")
    await _evaluate_probation_safe(db, defaulting_discord_id)

async def on_deal_scam_confirmed(
    db: aiosqlite.Connection,
    deal_id: int,
    scammer_discord_id: int,
) -> None:
    """Admin-triggered confirmed scam — maximum reputation penalty."""
    applied = await apply_reputation_delta(
        db,
        discord_id=scammer_discord_id,
        delta=-100,
        deal_id=deal_id,
        reason=REASON_SCAM,
        increment_strikes=True,
        increment_failed=True,
        reset_consecutive=True,
    )
    if applied:
        log.warning(f"Reputation: host {scammer_discord_id} -100 (SCAM confirmed) | deal={deal_id}")
    await _evaluate_probation_safe(db, scammer_discord_id)

async def on_dispute_escalated(
    db: aiosqlite.Connection,
    deal_id: int,
    disputing_discord_id: int,
) -> None:
    """Increment dispute_count — no score change at escalation time."""
    await ensure_host_exists(db, disputing_discord_id)
    applied = await apply_reputation_delta(
        db,
        discord_id=disputing_discord_id,
        delta=0,
        deal_id=deal_id,
        reason=REASON_DISPUTE_ESCALATED,
        increment_disputes=True,
    )
    if applied:
        log.info(f"Reputation: host {disputing_discord_id} dispute_count+1 | deal={deal_id}")

async def on_admin_adjust(
    db: aiosqlite.Connection,
    discord_id: int,
    delta: int,
    reason: str,
    admin_discord_id: int,
) -> tuple[int, int]:
    """Manual admin adjustment. Returns (old_score, new_score)."""
    old, new = await admin_adjust_reputation(db, discord_id, delta, reason)
    sign = "+" if delta >= 0 else ""
    log.info(
        f"Reputation: admin {admin_discord_id} adjusted host {discord_id} "
        f"{sign}{delta} ({old}→{new}) | reason: {reason}"
    )
    await _evaluate_probation_safe(db, discord_id)
    return old, new

# Probation evaluation helper (imported lazily to break circular dep)

async def _evaluate_probation_safe(
    db: aiosqlite.Connection, discord_id: int
) -> None:
    """Fire-and-forget probation check after any rep change."""
    try:
        # Late import to avoid circular: governance_service → reputation_queries (safe)
        from bot.services.governance_service import evaluate_probation
        await evaluate_probation(db, discord_id)
    except Exception as exc:
        log.warning(f"Probation evaluation failed for host {discord_id}: {exc}")

# Internal

async def _handle_completed(
    db: aiosqlite.Connection,
    discord_id: int,
    deal_id: int,
    was_overdue: bool,
) -> None:
    if was_overdue:
        applied = await apply_reputation_delta(
            db,
            discord_id=discord_id,
            delta=-15,
            deal_id=deal_id,
            reason=REASON_COMPLETED_LATE,
            increment_completed=True,
            reset_consecutive=True,
        )
        if applied:
            log.info(f"Reputation: host {discord_id} -15 (late completion) | deal={deal_id}")
    else:
        applied = await apply_reputation_delta(
            db,
            discord_id=discord_id,
            delta=+10,
            deal_id=deal_id,
            reason=REASON_COMPLETED_ONTIME,
            increment_completed=True,
            increment_consecutive=True,
        )
        if applied:
            log.info(f"Reputation: host {discord_id} +10 (completed) | deal={deal_id}")
            await _check_streak_bonus(db, discord_id, deal_id)

async def _check_streak_bonus(
    db: aiosqlite.Connection, discord_id: int, deal_id: int
) -> None:
    row = await get_host_reputation(db, discord_id)
    if not row:
        return
    consec = row["consecutive_clean_deals"]
    if consec > 0 and consec % STREAK_THRESHOLD == 0:
        milestone_reason = f"{REASON_STREAK_BONUS}_{consec}"
        applied = await apply_reputation_delta(
            db,
            discord_id=discord_id,
            delta=+25,
            deal_id=deal_id,
            reason=milestone_reason,
        )
        if applied:
            log.info(
                f"Reputation: host {discord_id} +25 streak bonus "
                f"({consec} clean deals) | deal={deal_id}"
            )
