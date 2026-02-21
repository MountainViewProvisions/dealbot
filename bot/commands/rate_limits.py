from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.queries import upsert_server_config, log_command
from bot.services.rate_limit_service import get_limits
from bot.utils.embeds import error_embed, rate_limits_embed, success_embed

log = logging.getLogger(__name__)

_OWNER_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("BOT_OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}


def _is_server_admin(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    return (
        interaction.user.guild_permissions.administrator
        or interaction.user.id in _OWNER_IDS
    )


class RateLimitCommands(commands.GroupCog, name="rate_limits"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="view", description="View current server rate limits.")
    async def rl_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db     = await get_db()
        limits = await get_limits(db, interaction.guild_id)
        await interaction.followup.send(embed=rate_limits_embed(limits), ephemeral=True)
        await log_command(db, interaction.guild_id, interaction.user.id, "rate_limits view", True)

    @app_commands.command(
        name="set",
        description="[Admin] Configure server rate limits.",
    )
    @app_commands.describe(
        max_open="Max open deals per user (e.g. 10).",
        max_volume="Max daily deal volume per user (e.g. 50000).",
        cooldown_hours="Dispute cooldown in hours (e.g. 24).",
        reminder_mins="Reminder loop frequency in minutes (e.g. 30).",
        weekly_summary="Enable weekly deal summary DMs (true/false).",
        mediator_role="Role name with mediator permissions.",
    )
    async def rl_set(
        self,
        interaction: discord.Interaction,
        max_open: float = 10.0,
        max_volume: float = 50_000.0,
        cooldown_hours: float = 24.0,
        reminder_mins: float = 30.0,
        weekly_summary: bool = True,
        mediator_role: str = "DealMediator",
    ):
        await interaction.response.defer(ephemeral=True)
        if not _is_server_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("You need **Administrator** permission to change limits."),
                ephemeral=True,
            )
            return

        if not (1 <= max_open <= 500):
            await interaction.followup.send(
                embed=error_embed("max_open must be between 1 and 500."), ephemeral=True
            )
            return
        if not (0 < max_volume <= 1_000_000_000):
            await interaction.followup.send(
                embed=error_embed("max_volume must be between 1 and 1,000,000,000."),
                ephemeral=True,
            )
            return
        if not (0 <= cooldown_hours <= 720):
            await interaction.followup.send(
                embed=error_embed("cooldown_hours must be between 0 and 720 (30 days)."),
                ephemeral=True,
            )
            return
        if not (5 <= reminder_mins <= 1440):
            await interaction.followup.send(
                embed=error_embed("reminder_mins must be between 5 and 1440."),
                ephemeral=True,
            )
            return

        db = await get_db()
        await upsert_server_config(
            db,
            guild_id=interaction.guild_id,
            max_open_deals=max_open,
            max_daily_volume=max_volume,
            dispute_cooldown_hours=cooldown_hours,
            reminder_freq_minutes=reminder_mins,
            weekly_summary_enabled=weekly_summary,
            mediator_role_name=mediator_role,
        )

        from bot.models.dataclasses import ServerLimits
        new_limits = ServerLimits(
            guild_id=interaction.guild_id,
            max_open_deals=max_open,
            max_daily_volume=max_volume,
            dispute_cooldown_hours=cooldown_hours,
            reminder_freq_minutes=reminder_mins,
            weekly_summary_enabled=weekly_summary,
            mediator_role_name=mediator_role,
        )
        await interaction.followup.send(
            content="✅ Server limits updated.",
            embed=rate_limits_embed(new_limits),
            ephemeral=True,
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "rate_limits set", True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RateLimitCommands(bot))
