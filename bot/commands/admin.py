from __future__ import annotations

import io
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.queries import get_command_stats, get_user_by_discord_id, log_command
from bot.models.enums import DealStatus
from bot.services.backup_service import export_all_deals_csv, run_daily_backup
from bot.services.deal_service import admin_resolve_dispute
from bot.utils.embeds import error_embed, info_embed, success_embed

log = logging.getLogger(__name__)

_OWNER_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("BOT_OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}
_MEDIATOR_ROLE = os.getenv("MEDIATOR_ROLE_NAME", "DealMediator")


def _is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.id in _OWNER_IDS:
        return True
    if not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return any(r.name == _MEDIATOR_ROLE for r in interaction.user.roles)


class AdminCommands(commands.GroupCog, name="admin"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    def _deny(self) -> discord.Embed:
        return error_embed(
            f"You need **Administrator** permission or the `{_MEDIATOR_ROLE}` role."
        )

    @app_commands.command(
        name="resolve",
        description="[Admin/Mediator] Resolve a disputed deal.",
    )
    @app_commands.describe(
        deal_id="The 8-character deal ID.",
        resolution="New status to apply.",
        note="Optional admin note recorded in the audit log.",
    )
    @app_commands.choices(resolution=[
        app_commands.Choice(name="Active — resume deal",    value="active"),
        app_commands.Choice(name="Completed — mark done",   value="completed"),
        app_commands.Choice(name="Defaulted — deal failed", value="defaulted"),
    ])
    async def admin_resolve(
        self,
        interaction: discord.Interaction,
        deal_id: str,
        resolution: app_commands.Choice[str],
        note: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(embed=self._deny(), ephemeral=True)
            return

        db = await get_db()
        status_map = {
            "active":    DealStatus.ACTIVE,
            "completed": DealStatus.COMPLETED,
            "defaulted": DealStatus.DEFAULTED,
        }
        ok, msg = await admin_resolve_dispute(
            db, deal_id.upper(), status_map[resolution.value], admin_note=note or None
        )
        await interaction.followup.send(
            embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "admin resolve", ok)

    @app_commands.command(
        name="export",
        description="[Admin] Export deal data as CSV.",
    )
    async def admin_export(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(embed=self._deny(), ephemeral=True)
            return

        db = await get_db()
        user_row = await get_user_by_discord_id(db, interaction.user.id)
        if not user_row:
            await interaction.followup.send(
                embed=error_embed("No user record found."), ephemeral=True
            )
            return

        csv_data = await export_all_deals_csv(db, user_row["id"])
        file = discord.File(
            fp=io.BytesIO(csv_data.encode()),
            filename="dealbot_export.csv",
        )
        await interaction.followup.send(
            content="✅ Deal export ready.",
            file=file,
            ephemeral=True,
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "admin export", True)

    @app_commands.command(
        name="backup",
        description="[Admin] Trigger an immediate database backup.",
    )
    async def admin_backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(embed=self._deny(), ephemeral=True)
            return

        db = await get_db()
        path = await run_daily_backup(db)
        if path:
            await interaction.followup.send(
                embed=success_embed(f"✅ Backup written: `{path}`"), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=error_embed("Backup failed — check server logs."), ephemeral=True
            )
        await log_command(
            db, interaction.guild_id, interaction.user.id, "admin backup", path is not None
        )

    @app_commands.command(
        name="stats",
        description="[Admin] View bot command usage statistics.",
    )
    @app_commands.describe(days="Look-back period in days (default 30).")
    async def admin_stats(self, interaction: discord.Interaction, days: int = 30):
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(embed=self._deny(), ephemeral=True)
            return

        db = await get_db()
        rows = await get_command_stats(db, interaction.guild_id, days)

        if not rows:
            await interaction.followup.send(
                embed=info_embed("Command Stats", "No usage data found."), ephemeral=True
            )
            return

        lines = [
            f"`{r['command']:<20}` calls: **{r['total']}** | ✅ {r['ok']} | ❌ {r['errors']}"
            for r in rows
        ]
        embed = info_embed(
            f"📊 Command Stats — last {days} days",
            "\n".join(lines),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(
            db, interaction.guild_id, interaction.user.id, "admin stats", True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))
