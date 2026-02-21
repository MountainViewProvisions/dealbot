from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.queries import get_all_networks, log_command
from bot.services.reputation_service import get_reputation
from bot.utils.embeds import error_embed, reputation_embed

log = logging.getLogger(__name__)


class RepCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rep", description="View a user's deal reputation.")
    @app_commands.describe(
        user="The Discord user to look up.",
        network="Optional: filter by network (e.g. TikTok, BigoLive).",
    )
    async def rep(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        network: Optional[str] = None,
    ):
        await interaction.response.defer()
        db = await get_db()

        if network:
            all_nets = await get_all_networks(db)
            valid = {n["name"].lower() for n in all_nets}
            if network.lower() not in valid:
                net_str = ", ".join(n["name"] for n in all_nets)
                await interaction.followup.send(
                    embed=error_embed(f"Unknown network `{network}`.\nValid: {net_str}")
                )
                return

        rep_data = await get_reputation(
            db=db,
            target_discord_id=user.id,
            target_discord_name=user.display_name,
            filter_network_name=network,
        )
        embed = reputation_embed(rep_data, network_filter=network)
        await interaction.followup.send(embed=embed)
        await log_command(db, interaction.guild_id, interaction.user.id, "rep", True)

    @rep.autocomplete("network")
    async def network_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        db = await get_db()
        networks = await get_all_networks(db)
        return [
            app_commands.Choice(name=n["name"], value=n["name"])
            for n in networks
            if current.lower() in n["name"].lower()
        ][:25]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RepCommands(bot))
