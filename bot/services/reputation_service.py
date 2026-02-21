from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from bot.database import queries
from bot.models.dataclasses import GlobalReputation, NetworkReputation

log = logging.getLogger(__name__)

_HALF_LIFE_DAYS  = 180.0
_LAMBDA          = math.log(2) / _HALF_LIFE_DAYS
_SCORE_MULT      = 50.0
_SCORE_ANCHOR    = 500.0


def _age_days(ts_str: str) -> float:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(tz=timezone.utc) - dt).total_seconds() / 86_400)
    except Exception:
        return 0.0


def _deal_weight(amount: float, age_days: float) -> float:
    recency = math.exp(-_LAMBDA * age_days)
    volume  = math.log10(1.0 + max(0.0, amount))
    return recency * volume


def calculate_score(rows: list) -> float:
    raw = 0.0
    for row in rows:
        w      = _deal_weight(row["amount"], _age_days(row["created_at"]))
        status = row["status"]
        if status == "completed":
            raw += w
        elif status in ("defaulted", "overdue"):
            raw -= w
    return round(max(0.0, min(1000.0, _SCORE_ANCHOR + raw * _SCORE_MULT)), 1)


async def get_reputation(
    db: aiosqlite.Connection,
    target_discord_id: int,
    target_discord_name: str,
    filter_network_name: Optional[str] = None,
) -> GlobalReputation:
    user = await queries.get_user_by_discord_id(db, target_discord_id)
    if not user:
        return GlobalReputation(discord_name=target_discord_name)

    network_id: Optional[int] = None
    if filter_network_name:
        net = await queries.get_network_by_name(db, filter_network_name)
        if net:
            network_id = net["id"]

    rows = await queries.get_reputation_rows(db, user["id"], network_id)

    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["network_name"], []).append(row)

    global_rep = GlobalReputation(discord_name=target_discord_name)
    all_rows: list = []

    for net_name, net_rows in sorted(grouped.items()):
        completed = sum(1 for r in net_rows if r["status"] == "completed")
        defaulted = sum(1 for r in net_rows if r["status"] == "defaulted")
        overdue   = sum(1 for r in net_rows if r["status"] == "overdue")
        disputed  = sum(1 for r in net_rows if r["status"] == "disputed")
        volume    = sum(r["amount"] for r in net_rows)
        score     = calculate_score(net_rows)

        net_rep = NetworkReputation(
            network_name=net_name,
            completed=completed, defaulted=defaulted,
            overdue=overdue, disputed=disputed,
            total_deals=len(net_rows),
            total_volume=volume, weighted_score=score,
        )
        global_rep.by_network.append(net_rep)
        all_rows.extend(net_rows)
        global_rep.total_completed += completed
        global_rep.total_defaulted += defaulted
        global_rep.total_overdue   += overdue
        global_rep.total_disputed  += disputed
        global_rep.total_deals     += len(net_rows)
        global_rep.total_volume    += volume

    global_rep.weighted_score = calculate_score(all_rows)
    return global_rep
