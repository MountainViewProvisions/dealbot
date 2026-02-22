from __future__ import annotations

"""
AnalyticsCog
============
Agency Analytics Dashboard — accessible to approved agency owners and bot owners.

Commands
--------
/analytics dashboard [days]       – Agency owner: view agency dashboard
/analytics guild_dashboard [days] – Bot owner: view guild-wide dashboard
/analytics send_summaries         – Bot owner: trigger weekly DM summaries now
/analytics host_breakdown [days]  – Agency owner: per-host performance breakdown
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.governance_queries import get_agency_analytics, get_guild_analytics
from bot.database.queries import log_command
from bot.services.analytics_service import (
    build_agency_analytics_embed,
    build_guild_analytics_embed,
    dispatch_weekly_agency_summaries,
)
from bot.utils.embeds import error_embed, info_embed, success_embed

log = logging.getLogger("dealbot.analytics_cog")

_OWNER_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("BOT_OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}


def _is_bot_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id in _OWNER_IDS


def _is_server_admin(interaction: discord.Interaction) -> bool:
    if _is_bot_owner(interaction):
        return True
    if not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.guild_permissions.administrator


async def _get_owned_agency(db, owner_discord_id: int):
    async with db.execute(
        "SELECT * FROM agencies WHERE owner_discord_id=? AND status='approved'",
        (owner_discord_id,),
    ) as cur:
        return await cur.fetchone()


class AnalyticsGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="analytics", description="Analytics and reporting commands.")

    @app_commands.command(
        name="dashboard",
        description="[Agency Owner] View your agency's analytics dashboard.",
    )
    @app_commands.describe(days="Days of history to include (default 7.0, max 90.0).")
    async def dashboard(
        self, interaction: discord.Interaction, days: float = 7.0
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        days_int = max(1, min(90, int(days)))
        db = await get_db()

        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency and not _is_bot_owner(interaction):
            await interaction.followup.send(
                embed=error_embed(
                    "This command is only available to approved agency owners. "
                    "Use `/analytics guild_dashboard` if you are a bot owner."
                ),
                ephemeral=True,
            )
            return

        if not agency:
            await interaction.followup.send(
                embed=error_embed(
                    "You don't own an agency. Use `/analytics guild_dashboard` instead."
                ),
                ephemeral=True,
            )
            return

        analytics = await get_agency_analytics(db, agency["id"], days=days_int)
        embed = build_agency_analytics_embed(
            analytics,
            agency_name=agency["name"],
            guild=interaction.guild,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(
            db, interaction.guild_id, interaction.user.id, "analytics dashboard", True
        )

    @app_commands.command(
        name="guild_dashboard",
        description="[Bot Owner / Admin] View server-wide deal analytics.",
    )
    @app_commands.describe(days="Days of history to include (default 7.0, max 90.0).")
    async def guild_dashboard(
        self, interaction: discord.Interaction, days: float = 7.0
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not _is_server_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Administrator permission required."), ephemeral=True
            )
            return

        if not interaction.guild_id:
            await interaction.followup.send(
                embed=error_embed("This command must be used inside a server."), ephemeral=True
            )
            return

        days_int = max(1, min(90, int(days)))
        db = await get_db()

        analytics = await get_guild_analytics(db, interaction.guild_id, days=days_int)
        embed = build_guild_analytics_embed(analytics, days_int)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(
            db, interaction.guild_id, interaction.user.id, "analytics guild_dashboard", True
        )

    @app_commands.command(
        name="send_summaries",
        description="[Bot Owner] Immediately dispatch weekly analytics DMs to all agency owners.",
    )
    async def send_summaries(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not _is_bot_owner(interaction):
            await interaction.followup.send(
                embed=error_embed("Only the bot owner can trigger manual summary dispatch."),
                ephemeral=True,
            )
            return

        db = await get_db()
        sent = await dispatch_weekly_agency_summaries(self.bot, db)

        await interaction.followup.send(
            embed=success_embed(
                f"✅ Weekly analytics DMs dispatched to **{sent}** agency owner(s)."
            ),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id,
            "analytics send_summaries", True, f"sent={sent}"
        )

    @app_commands.command(
        name="host_breakdown",
        description="[Agency Owner] Detailed per-host performance breakdown.",
    )
    @app_commands.describe(days="Days of history (default 30.0).")
    async def host_breakdown(
        self, interaction: discord.Interaction, days: float = 30.0
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        days_int = max(1, min(90, int(days)))
        db = await get_db()

        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency and not _is_bot_owner(interaction):
            await interaction.followup.send(
                embed=error_embed("Only agency owners can view host breakdowns."),
                ephemeral=True,
            )
            return
        if not agency:
            await interaction.followup.send(
                embed=error_embed("You don't own an agency."), ephemeral=True
            )
            return

        analytics = await get_agency_analytics(db, agency["id"], days=days_int)
        rankings = analytics.get("host_rankings", [])

        if not rankings:
            await interaction.followup.send(
                embed=info_embed("Host Breakdown", "No active hosts with deal data."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"👥 Host Breakdown — {agency['name']} (last {days_int}d)",
            colour=discord.Colour.blurple(),
        )
        for h in rankings:
            member = interaction.guild.get_member(h["discord_id"]) if interaction.guild else None
            name = member.display_name if member else f"<@{h['discord_id']}>"
            prob_flag = " 🔒 PROBATION" if h["on_probation"] else ""
            embed.add_field(
                name=f"{name}{prob_flag}",
                value=(
                    f"Rep: **{h['reputation']}** | "
                    f"✅ {h['completed_deals']} | "
                    f"❌ {h['failed_deals']} | "
                    f"⚠️ {h['dispute_count']}"
                ),
                inline=False,
            )

        embed.set_footer(
            text=f"Probation 🔒 = deal restrictions active. {days_int}-day window."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(
            db, interaction.guild_id, interaction.user.id, "analytics host_breakdown", True
        )


class AnalyticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.tree.add_command(AnalyticsGroup())

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("analytics")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyticsCog(bot))
    log.info("AnalyticsCog loaded.")
