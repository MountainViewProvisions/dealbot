from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import discord

from bot.database import get_db
from bot.services.reminder_service import run_reminders, run_weekly_summary
from bot.services.backup_service import run_daily_backup
from bot.services.analytics_service import dispatch_weekly_agency_summaries

log = logging.getLogger(__name__)

_REMINDER_MINS = int(os.getenv("REMINDER_LOOP_MINUTES", "30"))

async def reminder_task(bot: discord.Client) -> None:
    await bot.wait_until_ready()
    log.info(f"Reminder task started (interval: {_REMINDER_MINS}m)")
    while not bot.is_closed():
        try:
            await run_reminders(bot)
        except Exception as exc:
            log.exception(f"Reminder task error: {exc}")
        await asyncio.sleep(_REMINDER_MINS * 60)

async def weekly_summary_task(bot: discord.Client) -> None:
    await bot.wait_until_ready()
    log.info("Weekly summary task started.")
    while not bot.is_closed():
        now = datetime.now(tz=timezone.utc)
        if now.weekday() == 6 and now.hour == 0:
            try:
                await run_weekly_summary(bot)
            except Exception as exc:
                log.exception(f"Weekly summary error: {exc}")
            # ── Analytics weekly DMs ────────────────────────────────────────
            try:
                db = await get_db()
                sent = await dispatch_weekly_agency_summaries(bot, db)
                log.info(f"Weekly analytics DMs dispatched: {sent} agency owner(s) notified.")
            except Exception as exc:
                log.exception(f"Weekly analytics dispatch error: {exc}")
            await asyncio.sleep(23 * 3600)
        else:
            await asyncio.sleep(3600)

async def backup_task(bot: discord.Client) -> None:
    await bot.wait_until_ready()
    log.info("Backup task started.")
    while not bot.is_closed():
        now = datetime.now(tz=timezone.utc)
        if now.hour == 2 and now.minute < 30:
            try:
                db   = await get_db()
                path = await run_daily_backup(db)
                if path:
                    log.info(f"Scheduled backup complete: {path}")
            except Exception as exc:
                log.exception(f"Backup task error: {exc}")
            await asyncio.sleep(23 * 3600)
        else:
            await asyncio.sleep(1800)
