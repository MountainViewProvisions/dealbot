from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import discord

from bot.database import get_db
from bot.database import queries
from bot.services.deal_service import system_mark_overdue

log = logging.getLogger(__name__)

_WEEKLY_ENABLED = os.getenv("WEEKLY_SUMMARY_ENABLED", "true").lower() == "true"


async def run_reminders(bot: discord.Client) -> None:
    db = await get_db()

    upcoming_48 = await queries.get_deals_due_within_hours(db, 48)
    for deal in upcoming_48:
        await _dm_parties(bot, deal, _upcoming_msg(deal, hours=48))

    upcoming_24 = await queries.get_deals_due_within_hours(db, 24)
    for deal in upcoming_24:
        await _dm_parties(bot, deal, _upcoming_msg(deal, hours=24))

    newly_overdue = await queries.get_overdue_active_deals(db)
    await system_mark_overdue(db)
    for deal in newly_overdue:
        await _dm_parties(bot, deal, _overdue_msg(deal))

    log.info(
        f"Reminder cycle: {len(upcoming_48)} 48h | "
        f"{len(upcoming_24)} 24h | {len(newly_overdue)} overdue"
    )


async def run_weekly_summary(bot: discord.Client) -> None:
    if not _WEEKLY_ENABLED:
        return
    db = await get_db()
    users = await queries.get_all_users_with_active_deals(db)
    sent = 0
    for u in users:
        deals = await queries.get_all_active_deals_for_user(db, u["user_id"])
        if not deals:
            continue
        lines = [
            f"📋 **Weekly Deal Summary** — "
            f"{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}"
        ]
        for d in deals:
            from bot.utils.embeds import STATUS_EMOJI
            emoji = STATUS_EMOJI.get(d["status"], "•")
            lines.append(
                f"{emoji} `{d['deal_uuid']}` [{d['network_name']}] "
                f"{d['party_a_username']} ↔ {d['party_b_username']} "
                f"— **{d['status']}** (due {d['due_date'][:10]})"
            )
        try:
            user = await bot.fetch_user(u["discord_id"])
            await user.send("\n".join(lines))
            sent += 1
        except discord.Forbidden:
            log.debug(f"Weekly DM blocked for {u['discord_id']}")
        except Exception as exc:
            log.warning(f"Weekly DM failed for {u['discord_id']}: {exc}")
    log.info(f"Weekly summary sent to {sent} user(s).")


def _upcoming_msg(deal, hours: int) -> str:
    icon = "🔶" if hours <= 24 else "⏰"
    urgency = "**URGENT —** " if hours <= 24 else ""
    return (
        f"{icon} {urgency}**Deal Reminder** — `{deal['deal_uuid']}` "
        f"on **{deal['network_name']}**\n"
        f"Due in ≤**{hours}h** (due: {deal['due_date'][:10]})\n"
        f"Parties: `{deal['party_a_username']}` ↔ `{deal['party_b_username']}`\n"
        f"Use `/deal complete {deal['deal_uuid']}` to mark done."
    )


def _overdue_msg(deal) -> str:
    return (
        f"🔴 **Deal Overdue** — `{deal['deal_uuid']}` on **{deal['network_name']}**\n"
        f"Was due: {deal['due_date'][:10]}\n"
        f"Parties: `{deal['party_a_username']}` ↔ `{deal['party_b_username']}`\n"
        f"Status set to **overdue**. Use `/deal complete` or `/deal dispute` to resolve.\n"
        f"_If this remains unresolved it may be escalated to **defaulted** by an admin._"
    )


async def _dm_parties(bot: discord.Client, deal, message: str) -> None:
    for discord_id in (deal["party_a_discord_id"], deal["party_b_discord_id"]):
        try:
            user = await bot.fetch_user(discord_id)
            await user.send(message)
        except discord.Forbidden:
            log.debug(f"DMs disabled for {discord_id}; reminder skipped.")
        except Exception as exc:
            log.warning(f"Could not DM {discord_id}: {exc}")
