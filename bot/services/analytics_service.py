from __future__ import annotations

"""
analytics_service.py
====================
Aggregates and formats agency/guild analytics for dashboards and weekly DMs.
All DB access goes through governance_queries.
"""

import logging
from typing import Optional

import aiosqlite
import discord

from bot.database.governance_queries import (
    get_agency_analytics,
    get_guild_analytics,
    get_governance_config,
)
from bot.database.reputation_queries import TIER_EMOJI, get_reputation_tier

log = logging.getLogger("dealbot.analytics_service")

# Embed builders

def build_agency_analytics_embed(
    analytics: dict,
    agency_name: str,
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:
    """Build a rich embed for the agency analytics dashboard."""
    days = analytics["days"]
    total = analytics["total_deals"]
    clean_pct = analytics["clean_pct"]
    dispute_ratio = analytics["dispute_ratio"]

    # Dynamic colour based on clean percentage
    if clean_pct >= 80:
        color = 0x57F287  # green
    elif clean_pct >= 60:
        color = 0xFEE75C  # yellow
    else:
        color = 0xED4245  # red

    embed = discord.Embed(
        title=f"📊 Agency Analytics — {agency_name}",
        description=f"Performance snapshot for the last **{days} day(s)**.",
        colour=color,
    )

    # Volume & deal counts
    embed.add_field(
        name="📦 Deal Volume",
        value=(
            f"Total: **{total}** deals\n"
            f"Volume: **{analytics['total_volume']:,.0f}**\n"
            f"Active Hosts: **{analytics['active_hosts']}**"
        ),
        inline=True,
    )

    # Status breakdown
    embed.add_field(
        name="📈 Status Breakdown",
        value=(
            f"✅ Completed: **{analytics['completed']}**\n"
            f"⛔ Defaulted: **{analytics['defaulted']}**\n"
            f"⚠️ Disputed:  **{analytics['disputed']}**\n"
            f"🟠 Overdue:   **{analytics['overdue']}**\n"
            f"⚫ Cancelled: **{analytics['cancelled']}**"
        ),
        inline=True,
    )

    # Health metrics
    health_icon = "🟢" if clean_pct >= 80 else ("🟡" if clean_pct >= 60 else "🔴")
    embed.add_field(
        name="🏥 Health Metrics",
        value=(
            f"{health_icon} Clean Deal %: **{clean_pct}%**\n"
            f"⚠️ Dispute Ratio: **{dispute_ratio}%**\n"
            f"🔒 On Probation: **{analytics['probation_count']}**"
        ),
        inline=False,
    )

    # Host rankings
    rankings = analytics.get("host_rankings", [])
    if rankings:
        lines = []
        medals = ["🥇", "🥈", "🥉"] + ["🔸"] * 7
        for i, h in enumerate(rankings[:10]):
            tier = get_reputation_tier(h["reputation"])
            emoji = TIER_EMOJI[tier]
            prob = " 🔒" if h["on_probation"] else ""
            member_name = f"<@{h['discord_id']}>"
            if guild:
                m = guild.get_member(h["discord_id"])
                if m:
                    member_name = m.display_name
            lines.append(
                f"{medals[i]} **{member_name}** {emoji} {h['reputation']}"
                f" · ✅{h['completed_deals']} ❌{h['failed_deals']}{prob}"
            )
        embed.add_field(
            name="🏆 Host Rankings (by Reputation)",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="🏆 Host Rankings",
            value="_No active hosts yet._",
            inline=False,
        )

    embed.set_footer(text=f"Data window: last {days} day(s). Probation 🔒 = restricted.")
    return embed

def build_guild_analytics_embed(analytics: dict, days: int) -> discord.Embed:
    """Build guild-wide analytics embed for bot owners."""
    clean_pct = analytics["clean_pct"]
    color = 0x57F287 if clean_pct >= 80 else (0xFEE75C if clean_pct >= 60 else 0xED4245)

    embed = discord.Embed(
        title=f"🌐 Guild Analytics Dashboard",
        description=f"Server-wide performance for the last **{days} day(s)**.",
        colour=color,
    )
    embed.add_field(
        name="📦 Deals",
        value=(
            f"Total: **{analytics['total_deals']}**\n"
            f"Hosts Active: **{analytics['unique_hosts']}**\n"
            f"Volume: **{analytics['total_volume']:,.0f}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="📈 Outcomes",
        value=(
            f"✅ Completed: **{analytics['completed']}**\n"
            f"⛔ Defaulted: **{analytics['defaulted']}**\n"
            f"⚠️ Disputed:  **{analytics['disputed']}**\n"
            f"🟠 Overdue:   **{analytics['overdue']}**"
        ),
        inline=True,
    )
    health_icon = "🟢" if clean_pct >= 80 else ("🟡" if clean_pct >= 60 else "🔴")
    embed.add_field(
        name="🏥 Health",
        value=(
            f"{health_icon} Clean %: **{analytics['clean_pct']}%**\n"
            f"⚠️ Dispute Ratio: **{analytics['dispute_ratio']}%**\n"
            f"🔒 Probation: **{analytics['probation_count']}**\n"
            f"🏢 Agencies: **{analytics['agency_count']}**"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild-wide view. Last {days} days.")
    return embed

# Weekly DM summary dispatcher

async def dispatch_weekly_agency_summaries(
    bot: discord.Client,
    db: aiosqlite.Connection,
) -> int:
    """
    Send weekly analytics DMs to all agency owners whose guild has
    analytics_weekly_dm enabled.  Returns count of DMs sent.
    """
    sent = 0
    async with db.execute(
        "SELECT * FROM agencies WHERE status='approved' AND owner_discord_id IS NOT NULL"
    ) as cur:
        agencies = await cur.fetchall()

    for agency in agencies:
        owner_id = agency["owner_discord_id"]
        agency_id = agency["id"]
        agency_name = agency["name"]

        # Check if weekly DM is enabled (use guild_id=None → defaults)
        cfg = await get_governance_config(db, None)
        if not cfg.get("analytics_weekly_dm", True):
            continue

        try:
            analytics = await get_agency_analytics(db, agency_id, days=7)
            embed = build_agency_analytics_embed(analytics, agency_name)
            embed.title = f"📬 Weekly Summary — {agency_name}"

            user = await bot.fetch_user(owner_id)
            await user.send(
                content="Here is your agency's weekly performance summary:",
                embed=embed,
            )
            sent += 1
            log.info(f"Analytics: sent weekly DM to agency owner {owner_id} ({agency_name})")
        except Exception as exc:
            log.warning(f"Analytics: failed to DM owner {owner_id} for agency {agency_name}: {exc}")

    return sent
