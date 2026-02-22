from __future__ import annotations

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.queries import get_user_by_discord_id, log_command
from bot.services.backup_service import export_personal_data
from bot.utils.embeds import about_embed, error_embed

log = logging.getLogger(__name__)

class MiscCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="about",
        description="What is DealBot? Disclaimer, scope, and data policy.",
    )
    async def about(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=about_embed(), ephemeral=True)
        db = await get_db()
        await log_command(db, interaction.guild_id, interaction.user.id, "about", True)

    @app_commands.command(
        name="export_data",
        description="Download all your personal deal data (GDPR-style export).",
    )
    async def export_data(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db      = await get_db()
        csv_str = await export_personal_data(db, interaction.user.id)
        file    = discord.File(
            fp=io.BytesIO(csv_str.encode()),
            filename=f"dealbot_data_{interaction.user.id}.csv",
        )
        await interaction.followup.send(
            content=(
                "✅ Your personal data export is attached.\n"
                "_Contains all deals and notes associated with your Discord account._"
            ),
            file=file,
            ephemeral=True,
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "export_data", True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MiscCommands(bot))
