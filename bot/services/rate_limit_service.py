from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from bot.database import queries
from bot.models.dataclasses import ServerLimits

log = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


_GLOBAL_MAX_OPEN   = _env_int("MAX_OPEN_DEALS_PER_USER", 10)
_GLOBAL_MAX_VOLUME = _env_float("MAX_DAILY_DEAL_VOLUME", 50_000)
_GLOBAL_COOLDOWN   = _env_int("DISPUTE_COOLDOWN_HOURS", 24)


async def get_limits(
    db: aiosqlite.Connection, guild_id: Optional[int]
) -> ServerLimits:
    if guild_id:
        row = await queries.get_server_config(db, guild_id)
        if row:
            return ServerLimits(
                guild_id=guild_id,
                max_open_deals=row["max_open_deals"],
                max_daily_volume=float(row["max_daily_volume"]),
                dispute_cooldown_hours=row["dispute_cooldown_hours"],
                reminder_freq_minutes=row["reminder_freq_minutes"],
                weekly_summary_enabled=bool(row["weekly_summary_enabled"]),
                mediator_role_name=row["mediator_role_name"],
            )
    return ServerLimits(
        guild_id=guild_id or 0,
        max_open_deals=_GLOBAL_MAX_OPEN,
        max_daily_volume=_GLOBAL_MAX_VOLUME,
        dispute_cooldown_hours=_GLOBAL_COOLDOWN,
    )


async def check_deal_creation(
    db: aiosqlite.Connection,
    user_id: int,
    amount: float,
    guild_id: Optional[int] = None,
) -> tuple[bool, str]:
    limits = await get_limits(db, guild_id)

    open_count = await queries.count_open_deals_for_user(db, user_id)
    if open_count >= limits.max_open_deals:
        return False, (
            f"You have **{open_count}** open deals. "
            f"Maximum is **{limits.max_open_deals}**. "
            "Complete or resolve existing deals first."
        )

    daily_vol = await queries.get_daily_volume_for_user(db, user_id)
    if daily_vol + amount > limits.max_daily_volume:
        return False, (
            f"This deal would exceed your daily volume limit "
            f"({daily_vol:,.0f} + {amount:,.0f} > {limits.max_daily_volume:,.0f}). "
            "Try again tomorrow."
        )

    return True, ""


async def check_dispute_cooldown(
    db: aiosqlite.Connection,
    user_id: int,
    guild_id: Optional[int] = None,
) -> tuple[bool, str]:
    limits = await get_limits(db, guild_id)
    last_str = await queries.get_last_dispute_time(db, user_id)
    if not last_str:
        return True, ""

    last_dt = datetime.fromisoformat(last_str)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    elapsed = datetime.now(tz=timezone.utc) - last_dt
    cooldown = timedelta(hours=limits.dispute_cooldown_hours)
    if elapsed < cooldown:
        remaining = cooldown - elapsed
        h, rem = divmod(int(remaining.total_seconds()), 3600)
        m = rem // 60
        return False, (
            f"Dispute cooldown active: **{h}h {m}m** remaining "
            f"(server limit: {limits.dispute_cooldown_hours}h)."
        )
    return True, ""


async def record_dispute(db: aiosqlite.Connection, user_id: int) -> None:
    ts = datetime.now(tz=timezone.utc).isoformat()
    await queries.set_last_dispute_time(db, user_id, ts)
