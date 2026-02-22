from __future__ import annotations

"""
governance_service.py
=====================
Business logic for Probation Mode and Escrow Confidence Indicator.

Probation Mode
--------------
- Triggered when a host's reputation drops below `probation_threshold`
- Auto-lifted when reputation recovers above threshold
- While on probation:
  * Max open deals capped at `probation_max_open_deals`
  * Deals above `high_value_deal_threshold` are blocked
  * Agency must approve deals (agency_deal_approved flag)

Escrow Confidence Indicator
----------------------------
- Runs during deal creation validation (called from deal_service)
- If either party's reputation < `required_confidence_score`:
  * High-value deals are blocked
  * OR mediator is auto-assigned (if escrow_mediator_enabled)

All DB writes go through governance_queries. Never import deal_service
here to avoid circular dependencies.
"""

import logging
from typing import Optional

import aiosqlite

from bot.database.governance_queries import (
    create_escrow_flag,
    get_governance_config,
    get_probation_status,
    set_probation,
)
from bot.database.reputation_queries import get_host_reputation

log = logging.getLogger("dealbot.governance_service")

# Probation — called from reputation_service after every rep change

async def evaluate_probation(
    db: aiosqlite.Connection,
    discord_id: int,
    guild_id: Optional[int] = None,
    triggered_by: str = "system",
) -> tuple[bool, str]:
    """
    Check whether a host should enter or leave probation based on their
    current reputation vs the guild's configured threshold.

    Returns (changed: bool, message: str).
    """
    cfg = await get_governance_config(db, guild_id)
    threshold = float(cfg["probation_threshold"])

    rep_row = await get_host_reputation(db, discord_id)
    reputation = rep_row["reputation"] if rep_row else 500
    currently_on = bool(rep_row["on_probation"]) if rep_row else False

    should_be_on = reputation < threshold

    if should_be_on and not currently_on:
        await set_probation(db, discord_id, True, reputation, triggered_by)
        log.warning(
            f"Governance: host {discord_id} entered PROBATION "
            f"(rep={reputation} < threshold={threshold})"
        )
        return True, (
            f"⚠️ You have been placed on **probation** "
            f"(reputation {reputation} < {threshold:.0f}). "
            "Deal creation is restricted until your score recovers."
        )

    if not should_be_on and currently_on:
        await set_probation(db, discord_id, False, reputation, triggered_by)
        log.info(
            f"Governance: host {discord_id} PROBATION LIFTED "
            f"(rep={reputation} >= threshold={threshold})"
        )
        return True, (
            f"✅ Your probation has been **lifted** "
            f"(reputation {reputation} ≥ {threshold:.0f}). "
            "Normal deal limits restored."
        )

    return False, ""

async def check_probation_restrictions(
    db: aiosqlite.Connection,
    discord_id: int,
    amount: float,
    open_deal_count: int,
    guild_id: Optional[int] = None,
) -> tuple[bool, str]:
    """
    Validate deal creation against probation constraints.
    Returns (allowed: bool, reason: str).
    """
    status = await get_probation_status(db, discord_id)
    if not status or not status["on_probation"]:
        return True, ""

    cfg = await get_governance_config(db, guild_id)
    max_open = int(cfg["probation_max_open_deals"])
    high_val = float(cfg["high_value_deal_threshold"])

    if open_deal_count >= max_open:
        return False, (
            f"🔒 **Probation restriction:** You may only have **{max_open}** open "
            f"deal(s) while on probation (you have {open_deal_count}). "
            "Improve your reputation to restore normal limits."
        )

    if amount > high_val:
        return False, (
            f"🔒 **Probation restriction:** Deals above "
            f"**{high_val:,.0f}** are blocked while on probation. "
            "Improve your reputation to unlock high-value deals."
        )

    if not status["agency_deal_approved"]:
        return False, (
            "🔒 **Probation restriction:** Your agency owner must approve "
            "deal creation while you are on probation. "
            "Contact your agency owner to request approval."
        )

    return True, ""

# Escrow Confidence Indicator — called from deal_service.create_deal

async def run_escrow_check(
    db: aiosqlite.Connection,
    deal_id: int,
    initiator_discord_id: int,
    counterparty_discord_id: int,
    amount: float,
    guild_id: Optional[int] = None,
) -> tuple[bool, str, bool]:
    """
    Evaluate escrow confidence for both deal parties.

    Returns:
        allowed (bool)          – False means deal is hard-blocked
        message (str)           – Human-readable outcome message
        mediator_assigned (bool) – True if mediator flag was set
    """
    cfg = await get_governance_config(db, guild_id)
    required = float(cfg["required_confidence_score"])
    high_val  = float(cfg["high_value_deal_threshold"])
    mediator_enabled = bool(cfg["escrow_mediator_enabled"])

    rep_a = await get_host_reputation(db, initiator_discord_id)
    rep_b = await get_host_reputation(db, counterparty_discord_id)

    score_a = float(rep_a["reputation"]) if rep_a else 500.0
    score_b = float(rep_b["reputation"]) if rep_b else 500.0

    low_confidence = score_a < required or score_b < required
    is_high_value  = amount > high_val

    if not low_confidence:
        # Both parties pass — record the flag and proceed
        await create_escrow_flag(
            db, deal_id, score_a, score_b,
            mediator_assigned=False, blocked=False,
        )
        return True, "", False

    # Low confidence detected
    if is_high_value:
        if mediator_enabled:
            await create_escrow_flag(
                db, deal_id, score_a, score_b,
                mediator_assigned=True, blocked=False,
            )
            log.info(
                f"Escrow: mediator assigned for deal {deal_id} "
                f"(scores: A={score_a}, B={score_b}, required={required})"
            )
            return (
                True,
                (
                    "⚠️ **Escrow Notice:** One or both parties have low confidence scores. "
                    "A mediator role has been **auto-assigned** to this deal. "
                    f"_(Party A: {score_a:.0f} | Party B: {score_b:.0f} | Required: {required:.0f})_"
                ),
                True,
            )
        else:
            # Block the deal
            reason = (
                f"Confidence scores too low for high-value deal "
                f"(A={score_a:.0f}, B={score_b:.0f}, required={required:.0f})"
            )
            await create_escrow_flag(
                db, deal_id, score_a, score_b,
                mediator_assigned=False, blocked=True,
                block_reason=reason,
            )
            log.warning(
                f"Escrow: high-value deal {deal_id} BLOCKED — {reason}"
            )
            return (
                False,
                (
                    f"🚫 **Escrow Block:** This high-value deal (>{high_val:,.0f}) "
                    "cannot proceed — one or both parties have insufficient confidence scores. "
                    f"_(Party A: {score_a:.0f} | Party B: {score_b:.0f} | Required: {required:.0f})_"
                ),
                False,
            )
    else:
        # Low value deal with low confidence — warn but allow
        await create_escrow_flag(
            db, deal_id, score_a, score_b,
            mediator_assigned=False, blocked=False,
        )
        return (
            True,
            (
                "ℹ️ **Escrow Notice:** One or both parties have lower confidence scores. "
                "Proceed with caution. "
                f"_(Party A: {score_a:.0f} | Party B: {score_b:.0f} | Required: {required:.0f})_"
            ),
            False,
        )
