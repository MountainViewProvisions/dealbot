from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import aiosqlite

from bot.database import queries
from bot.models.enums import DealStatus, ActionType, validate_transition
from bot.services import audit_service, rate_limit_service
from bot.utils.id_generator import generate_deal_id

# Imported lazily-ish to avoid circular issues; the module itself is stateless
import bot.services.reputation_service as _rep_svc
import bot.services.governance_service as _gov_svc

log = logging.getLogger(__name__)


async def _transition(
    db: aiosqlite.Connection,
    deal_id: int,
    current: DealStatus,
    target: DealStatus,
    action: ActionType,
    actor_profile_id: Optional[int] = None,
    disputed_reason: Optional[str] = None,
    metadata: Optional[str] = None,
) -> None:
    if not validate_transition(current, target):
        raise ValueError(f"Illegal transition: {current.value} → {target.value}")
    await queries.update_deal_status(db, deal_id, target.value, disputed_reason)
    await audit_service.log_transition(
        db, deal_id, actor_profile_id,
        current.value, target.value, action, metadata,
    )


async def create_deal(
    db: aiosqlite.Connection,
    initiator_discord_id: int,
    initiator_discord_name: str,
    counterparty_discord_id: int,
    counterparty_discord_name: str,
    network_name: str,
    initiator_username: str,
    counterparty_username: str,
    deal_type: str,
    amount: float,
    currency: str,
    due_date: datetime,
    guild_id: Optional[int] = None,
) -> str:
    network = await queries.get_network_by_name(db, network_name)
    if not network:
        raise ValueError(f"Unknown network: '{network_name}'")

    initiator_uid    = await queries.upsert_user(db, initiator_discord_id, initiator_discord_name)
    counterparty_uid = await queries.upsert_user(db, counterparty_discord_id, counterparty_discord_name)

    allowed, reason = await rate_limit_service.check_deal_creation(
        db, initiator_uid, amount, guild_id
    )
    if not allowed:
        raise PermissionError(reason)

    # ── Probation check ─────────────────────────────────────────────────────
    open_count = await queries.count_open_deals_for_user(db, initiator_uid)
    prob_ok, prob_msg = await _gov_svc.check_probation_restrictions(
        db, initiator_discord_id, amount, open_count, guild_id
    )
    if not prob_ok:
        raise PermissionError(prob_msg)

    initiator_pid    = await queries.upsert_profile(db, initiator_uid, network["id"], initiator_username)
    counterparty_pid = await queries.upsert_profile(db, counterparty_uid, network["id"], counterparty_username)

    deal_uuid = generate_deal_id()
    deal_id = await queries.create_deal(
        db,
        deal_uuid=deal_uuid,
        guild_id=guild_id,
        network_id=network["id"],
        party_a_profile_id=initiator_pid,
        party_b_profile_id=counterparty_pid,
        deal_type=deal_type,
        amount=amount,
        currency=currency,
        due_date=due_date.strftime("%Y-%m-%d %H:%M:%S"),
    )

    await audit_service.log_transition(
        db, deal_id, initiator_pid,
        "none", DealStatus.PENDING_CONFIRMATION.value,
        ActionType.CREATED,
    )

    # ── Escrow confidence check ─────────────────────────────────────────────
    try:
        escrow_ok, escrow_msg, _ = await _gov_svc.run_escrow_check(
            db, deal_id, initiator_discord_id, counterparty_discord_id,
            amount, guild_id,
        )
        if not escrow_ok:
            # Hard block — cancel the deal we just created
            await queries.update_deal_status(db, deal_id, "cancelled")
            raise PermissionError(escrow_msg)
        if escrow_msg:
            # Warning / mediator assigned — attach as deal note for visibility
            log.info(f"Escrow notice for deal {deal_uuid}: {escrow_msg}")
    except PermissionError:
        raise
    except Exception as _esc_err:
        log.warning(f"Escrow check failed for deal {deal_uuid}: {_esc_err}")

    log.info(f"Deal {deal_uuid} created | guild={guild_id} | {initiator_discord_id} ↔ {counterparty_discord_id}")
    return deal_uuid


async def confirm_deal(
    db: aiosqlite.Connection,
    deal_uuid: str,
    confirming_discord_id: int,
) -> tuple[bool, str]:
    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if not deal:
        return False, "Deal not found."

    current = DealStatus(deal["status"])
    if current != DealStatus.PENDING_CONFIRMATION:
        return False, f"Deal is not awaiting confirmation (status: `{current.value}`)."

    is_a = deal["party_a_discord_id"] == confirming_discord_id
    is_b = deal["party_b_discord_id"] == confirming_discord_id
    if not is_a and not is_b:
        return False, "You are not a party to this deal."
    if is_a and deal["party_a_confirmed"]:
        return False, "You have already confirmed this deal."
    if is_b and deal["party_b_confirmed"]:
        return False, "You have already confirmed this deal."

    actor_pid = deal["party_a_profile_id"] if is_a else deal["party_b_profile_id"]
    await queries.set_party_confirmed(db, deal["id"], is_a)

    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if deal["party_a_confirmed"] and deal["party_b_confirmed"]:
        await _transition(
            db, deal["id"],
            DealStatus.PENDING_CONFIRMATION, DealStatus.ACTIVE,
            ActionType.ACTIVATED, actor_pid,
        )
        return True, "✅ Both parties confirmed. Deal is now **ACTIVE**."

    await audit_service.log_transition(
        db, deal["id"], actor_pid,
        DealStatus.PENDING_CONFIRMATION.value,
        DealStatus.PENDING_CONFIRMATION.value,
        ActionType.CONFIRMED,
    )
    return True, "✅ Confirmation recorded. Waiting for the other party."


async def complete_deal(
    db: aiosqlite.Connection,
    deal_uuid: str,
    confirming_discord_id: int,
) -> tuple[bool, str]:
    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if not deal:
        return False, "Deal not found."

    current = DealStatus(deal["status"])
    allowed_from = {DealStatus.ACTIVE, DealStatus.PENDING_COMPLETION, DealStatus.OVERDUE}
    if current not in allowed_from:
        return False, f"Deal cannot be completed from status `{current.value}`."

    is_a = deal["party_a_discord_id"] == confirming_discord_id
    is_b = deal["party_b_discord_id"] == confirming_discord_id
    if not is_a and not is_b:
        return False, "You are not a party to this deal."
    if is_a and deal["completion_confirmed_by_a"]:
        return False, "You have already confirmed completion."
    if is_b and deal["completion_confirmed_by_b"]:
        return False, "You have already confirmed completion."

    actor_pid = deal["party_a_profile_id"] if is_a else deal["party_b_profile_id"]
    await queries.set_completion_confirmed(db, deal["id"], is_a)

    if current in {DealStatus.ACTIVE, DealStatus.OVERDUE}:
        await _transition(
            db, deal["id"], current, DealStatus.PENDING_COMPLETION,
            ActionType.COMPLETED, actor_pid,
        )

    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if deal["completion_confirmed_by_a"] and deal["completion_confirmed_by_b"]:
        current_now = DealStatus(deal["status"])
        await _transition(
            db, deal["id"], current_now, DealStatus.COMPLETED,
            ActionType.COMPLETED, actor_pid,
        )
        # ── Reputation hook ─────────────────────────────────────────────────
        # Determine if the deal was ever overdue (status was OVERDUE before completion)
        was_overdue = current_now == DealStatus.OVERDUE or current == DealStatus.OVERDUE
        try:
            await _rep_svc.on_deal_completed(
                db,
                deal_id=deal["id"],
                party_a_discord_id=deal["party_a_discord_id"],
                party_b_discord_id=deal["party_b_discord_id"],
                was_overdue=was_overdue,
            )
        except Exception as _rep_err:
            log.warning(f"Reputation update failed for deal {deal_uuid}: {_rep_err}")
        return True, "🎉 Both parties confirmed. Deal marked **COMPLETED**!"

    return True, "✅ Completion recorded. Waiting for the other party."


async def dispute_deal(
    db: aiosqlite.Connection,
    deal_uuid: str,
    disputing_discord_id: int,
    reason: str,
    guild_id: Optional[int] = None,
) -> tuple[bool, str]:
    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if not deal:
        return False, "Deal not found."

    current = DealStatus(deal["status"])
    if current not in {DealStatus.ACTIVE, DealStatus.PENDING_COMPLETION, DealStatus.OVERDUE}:
        return False, f"Deal cannot be disputed from status `{current.value}`."

    is_a = deal["party_a_discord_id"] == disputing_discord_id
    is_b = deal["party_b_discord_id"] == disputing_discord_id
    if not is_a and not is_b:
        return False, "You are not a party to this deal."

    user_row = await queries.get_user_by_discord_id(db, disputing_discord_id)
    if user_row:
        ok, msg = await rate_limit_service.check_dispute_cooldown(db, user_row["id"], guild_id)
        if not ok:
            return False, msg
        await rate_limit_service.record_dispute(db, user_row["id"])

    actor_pid = deal["party_a_profile_id"] if is_a else deal["party_b_profile_id"]
    await _transition(
        db, deal["id"], current, DealStatus.DISPUTED,
        ActionType.DISPUTED, actor_pid,
        disputed_reason=reason,
        metadata=reason[:500],
    )
    # ── Reputation hook ──────────────────────────────────────────────────────
    try:
        await _rep_svc.on_dispute_escalated(
            db,
            deal_id=deal["id"],
            disputing_discord_id=disputing_discord_id,
        )
    except Exception as _rep_err:
        log.warning(f"Reputation dispute hook failed for deal {deal_uuid}: {_rep_err}")
    return True, "⚠️ Deal marked **DISPUTED**. Reputation updates frozen until resolved."


async def admin_resolve_dispute(
    db: aiosqlite.Connection,
    deal_uuid: str,
    resolution: DealStatus,
    admin_note: Optional[str] = None,
) -> tuple[bool, str]:
    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if not deal:
        return False, "Deal not found."

    current = DealStatus(deal["status"])
    if current != DealStatus.DISPUTED:
        return False, f"Deal is not DISPUTED (currently: `{current.value}`)."

    valid = {DealStatus.ACTIVE, DealStatus.COMPLETED, DealStatus.DEFAULTED}
    if resolution not in valid:
        return False, "Resolution must be one of: active, completed, defaulted."

    meta = f"Admin resolved → {resolution.value}"
    if admin_note:
        meta += f" | note: {admin_note[:200]}"

    await _transition(
        db, deal["id"], DealStatus.DISPUTED, resolution,
        ActionType.RESOLVED, actor_profile_id=None, metadata=meta,
    )

    # ── Reputation hooks on resolution ──────────────────────────────────────
    try:
        if resolution == DealStatus.COMPLETED:
            await _rep_svc.on_deal_completed(
                db,
                deal_id=deal["id"],
                party_a_discord_id=deal["party_a_discord_id"],
                party_b_discord_id=deal["party_b_discord_id"],
                was_overdue=False,
            )
        elif resolution == DealStatus.DEFAULTED:
            # Both parties get the failed-deal penalty when admin resolves as defaulted
            for did in (deal["party_a_discord_id"], deal["party_b_discord_id"]):
                await _rep_svc.on_deal_defaulted(db, deal_id=deal["id"], defaulting_discord_id=did)
    except Exception as _rep_err:
        log.warning(f"Reputation resolution hook failed for deal {deal['deal_uuid']}: {_rep_err}")

    return True, f"✅ Dispute resolved. Deal is now **{resolution.value.upper()}**."


async def system_mark_overdue(db: aiosqlite.Connection) -> int:
    rows = await queries.get_overdue_active_deals(db)
    count = await queries.mark_overdue_deals(db)
    for row in rows:
        await audit_service.log_transition(
            db, row["id"], None,
            row["status"], DealStatus.OVERDUE.value,
            ActionType.OVERDUE,
        )
    if count:
        log.info(f"System marked {count} deal(s) overdue.")
    return count


async def add_note(
    db: aiosqlite.Connection,
    deal_uuid: str,
    author_discord_id: int,
    note_text: str,
) -> tuple[bool, str]:
    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if not deal:
        return False, "Deal not found."

    is_a = deal["party_a_discord_id"] == author_discord_id
    is_b = deal["party_b_discord_id"] == author_discord_id
    if not is_a and not is_b:
        return False, "Only parties to this deal may add notes."

    profile_id = deal["party_a_profile_id"] if is_a else deal["party_b_profile_id"]
    await queries.add_note(db, deal["id"], profile_id, note_text)
    await audit_service.log_transition(
        db, deal["id"], profile_id,
        deal["status"], deal["status"],
        ActionType.NOTE_ADDED,
        metadata=f"Note added ({len(note_text)} chars)",
    )
    return True, "📝 Note added."


async def get_deal_details(
    db: aiosqlite.Connection,
    deal_uuid: str,
    requesting_discord_id: int,
    include_audit: bool = False,
) -> Optional[dict]:
    deal = await queries.get_deal_by_uuid(db, deal_uuid)
    if not deal:
        return None
    is_party = (
        deal["party_a_discord_id"] == requesting_discord_id
        or deal["party_b_discord_id"] == requesting_discord_id
    )
    if not is_party:
        return None
    notes = await queries.get_notes_for_deal(db, deal["id"])
    result: dict = {"deal": deal, "notes": notes}
    if include_audit:
        result["audit"] = await queries.get_audit_log_for_deal(db, deal["id"])
    return result
