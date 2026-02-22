from __future__ import annotations

"""
ReputationCog
=============
Slash commands for the reputation / incentive system.

Commands
--------
/profile view [user]                          – Full host profile + rep tier
/reputation leaderboard                       – Top 10 hosts by rep score
/reputation history [user]                    – Last 20 rep log entries
/admin_rep adjust_reputation <user> <amt>     – Admin manual delta
/admin_rep scam_confirm <user> <deal_id>      – Admin confirms scam (−100, strike)
/agency_rep stats                             – Agency rep summary (owner only)
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import bot.services.reputation_service as rep_svc
from bot.database import get_db
from bot.database.agency_queries import get_agency_by_id, get_host_by_discord_id
from bot.database.governance_queries import get_probation_status
from bot.database.queries import log_command
from bot.database.reputation_queries import (
    TIER_COLOR,
    TIER_EMOJI,
    get_agency_reputation_summary,
    get_host_reputation,
    get_reputation_leaderboard,
    get_reputation_log,
    get_reputation_tier,
)
from bot.utils.embeds import error_embed, info_embed, success_embed

log = logging.getLogger("dealbot.reputation_cog")

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


class ProfileGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="profile", description="Host profile commands.")

    @app_commands.command(name="view", description="View a host's reputation profile.")
    @app_commands.describe(user="The user to look up (defaults to yourself).")
    async def profile_view(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        db = await get_db()

        row = await get_host_reputation(db, target.id)
        host_row = await get_host_by_discord_id(db, target.id)

        rep = row["reputation"] if row else 500
        tier = get_reputation_tier(rep)
        emoji = TIER_EMOJI[tier]
        color = TIER_COLOR[tier]

        embed = discord.Embed(
            title=f"{emoji} {target.display_name} — {tier}",
            colour=color,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        prob_status = await get_probation_status(db, target.id)
        if prob_status and prob_status["on_probation"]:
            since = str(prob_status["probation_since"] or "")[:10]
            embed.add_field(
                name="🔒 PROBATION",
                value=(
                    f"This host is on **probation** (since {since}).\n"
                    "Deal creation is restricted until reputation recovers."
                ),
                inline=False,
            )

        # Reputation bar  ████████░░ 780/1000
        filled = int((rep / 1000) * 10)
        bar = "█" * filled + "░" * (10 - filled)
        embed.add_field(
            name="Reputation",
            value=f"`{bar}` **{rep}** / 1000",
            inline=False,
        )

        if row:
            embed.add_field(name="✅ Completed Deals", value=str(row["completed_deals"]), inline=True)
            embed.add_field(name="❌ Failed Deals",    value=str(row["failed_deals"]),    inline=True)
            embed.add_field(name="⚠️ Disputes",        value=str(row["dispute_count"]),   inline=True)
            embed.add_field(name="🚫 Strikes",         value=str(row["strikes"]),         inline=True)
            streak = row["consecutive_clean_deals"]
            embed.add_field(name="🔥 Clean Streak",    value=str(streak),                 inline=True)
            if row["last_reputation_update"]:
                embed.add_field(
                    name="Last Update",
                    value=str(row["last_reputation_update"])[:10],
                    inline=True,
                )
        else:
            embed.add_field(
                name="ℹ️ Note",
                value="No deal history yet. Defaults shown.",
                inline=False,
            )

        if host_row and host_row["agency_id"]:
            agency = await get_agency_by_id(db, host_row["agency_id"])
            if agency:
                status = "✅" if host_row["join_status"] == "active" else "⏳"
                embed.add_field(
                    name="🏢 Agency",
                    value=f"{status} {agency['name']}",
                    inline=False,
                )

        embed.set_footer(text="Tiers: Unverified → Caution → Trusted → Verified → Elite")
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(db, interaction.guild_id, interaction.user.id, "profile view", True)


class ReputationGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="reputation", description="Reputation commands.")

    @app_commands.command(name="leaderboard", description="Top 10 hosts by reputation score.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=False)
        db = await get_db()
        rows = await get_reputation_leaderboard(db, limit=10)

        if not rows:
            await interaction.followup.send(
                embed=info_embed("Leaderboard", "No reputation data yet."), ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🏆 Reputation Leaderboard — Top 10",
            colour=0xFFD700,
        )
        medals = ["🥇", "🥈", "🥉"] + ["🔸"] * 7

        lines = []
        for i, row in enumerate(rows):
            tier = get_reputation_tier(row["reputation"])
            emoji = TIER_EMOJI[tier]
            try:
                user = interaction.guild.get_member(row["discord_id"]) if interaction.guild else None
                name = user.display_name if user else f"User {row['discord_id']}"
            except Exception:
                name = f"User {row['discord_id']}"
            lines.append(
                f"{medals[i]} **{name}** — {emoji} **{row['reputation']}** "
                f"({tier}) · ✅ {row['completed_deals']} deals"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="Updates after every completed or failed deal.")
        await interaction.followup.send(embed=embed)
        await log_command(db, interaction.guild_id, interaction.user.id, "reputation leaderboard", True)

    @app_commands.command(name="history", description="View reputation change history.")
    @app_commands.describe(user="The user to look up (defaults to yourself).")
    async def history(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user

        if target.id != interaction.user.id and not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("You can only view your own reputation history."),
                ephemeral=True,
            )
            return

        db = await get_db()
        entries = await get_reputation_log(db, target.id, limit=20)

        if not entries:
            await interaction.followup.send(
                embed=info_embed("Reputation History", "No entries found."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📜 Reputation History — {target.display_name}",
            colour=discord.Colour.blurple(),
        )
        lines = []
        for e in entries:
            sign = "+" if e["change_amount"] >= 0 else ""
            deal_ref = f" (deal #{e['deal_id']})" if e["deal_id"] else ""
            lines.append(
                f"`{e['created_at'][:10]}` {sign}{e['change_amount']:+d} — "
                f"{e['reason'].replace('_', ' ')}{deal_ref}"
            )
        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)


class AdminReputationGroup(app_commands.Group):
    """
    Reputation admin commands registered under /admin_rep to avoid colliding
    with the existing AdminCommands GroupCog (which already holds the "admin" name).
    Commands appear as /admin_rep adjust_reputation and /admin_rep scam_confirm.
    """

    def __init__(self):
        super().__init__(
            name="admin_rep",
            description="[Admin] Reputation management commands.",
        )

    def _deny(self) -> discord.Embed:
        return error_embed(
            f"You need **Administrator** permission or the `{_MEDIATOR_ROLE}` role."
        )

    @app_commands.command(
        name="adjust_reputation",
        description="[Admin] Manually adjust a host's reputation score.",
    )
    @app_commands.describe(
        user="The host to adjust.",
        amount="Points to add (positive) or remove (negative). Range ±500.",
        reason="Reason for the adjustment (logged).",
    )
    async def adjust_reputation(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
        reason: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(embed=self._deny(), ephemeral=True)
            return

        if not (-500 <= amount <= 500):
            await interaction.followup.send(
                embed=error_embed("Amount must be between -500 and +500."), ephemeral=True
            )
            return

        reason = reason.strip()
        if not reason:
            await interaction.followup.send(
                embed=error_embed("A reason is required for manual adjustments."), ephemeral=True
            )
            return

        db = await get_db()
        old, new = await rep_svc.on_admin_adjust(
            db,
            discord_id=user.id,
            delta=amount,
            reason=f"admin_manual: {reason}",
            admin_discord_id=interaction.user.id,
        )
        sign = "+" if amount >= 0 else ""
        embed = success_embed(
            f"✅ Adjusted **{user.display_name}**'s reputation\n"
            f"{sign}{amount} → **{old}** ➜ **{new}**\n"
            f"Reason: _{reason}_"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(
            db, interaction.guild_id, interaction.user.id,
            "admin_rep adjust_reputation", True,
            f"user={user.id} delta={amount} reason={reason}",
        )

    @app_commands.command(
        name="scam_confirm",
        description="[Admin] Mark a user as a confirmed scammer (−100 rep, +1 strike).",
    )
    @app_commands.describe(
        user="The scammer.",
        deal_id="Internal deal integer ID (from /deal view or DB).",
    )
    async def scam_confirm(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        deal_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(embed=self._deny(), ephemeral=True)
            return

        db = await get_db()
        await rep_svc.on_deal_scam_confirmed(
            db,
            deal_id=deal_id,
            scammer_discord_id=user.id,
        )
        row = await get_host_reputation(db, user.id)
        new_rep = row["reputation"] if row else "?"
        await interaction.followup.send(
            embed=success_embed(
                f"🚨 **{user.display_name}** confirmed as scammer.\n"
                f"−100 reputation, +1 strike.\n"
                f"New reputation: **{new_rep}**"
            ),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id,
            "admin_rep scam_confirm", True, f"user={user.id} deal={deal_id}",
        )


class AgencyRepGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="agency_rep", description="Agency reputation stats.")

    @app_commands.command(
        name="stats",
        description="[Agency Owner] View reputation statistics for your agency.",
    )
    async def agency_stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()

        async with db.execute(
            "SELECT * FROM agencies WHERE owner_discord_id=? AND status='approved'",
            (interaction.user.id,),
        ) as cur:
            agency = await cur.fetchone()

        if not agency:
            await interaction.followup.send(
                embed=error_embed("You are not the owner of any approved agency."),
                ephemeral=True,
            )
            return

        summary = await get_agency_reputation_summary(db, agency["id"])

        if summary["host_count"] == 0:
            await interaction.followup.send(
                embed=info_embed(f"Agency Stats — {agency['name']}", "No active hosts yet."),
                ephemeral=True,
            )
            return

        avg_tier = get_reputation_tier(int(summary["avg_rep"]))
        embed = discord.Embed(
            title=f"📊 Agency Reputation — {agency['name']}",
            colour=TIER_COLOR[avg_tier],
        )
        embed.add_field(name="Active Hosts",     value=str(summary["host_count"]),   inline=True)
        embed.add_field(name="Avg Reputation",   value=f"{summary['avg_rep']} ({avg_tier})", inline=True)
        embed.add_field(name="⬆ Highest Rep",   value=str(summary["max_rep"]),      inline=True)
        embed.add_field(name="⬇ Lowest Rep",    value=str(summary["min_rep"]),      inline=True)
        embed.add_field(name="⚠️ Total Disputes", value=str(summary["total_disputes"]), inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(
            db, interaction.guild_id, interaction.user.id, "agency_rep stats", True
        )


class ReputationCog(commands.Cog):
    """Registers all reputation-related app_command groups."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.tree.add_command(ProfileGroup())
        self.bot.tree.add_command(ReputationGroup())
        self.bot.tree.add_command(AdminReputationGroup())
        self.bot.tree.add_command(AgencyRepGroup())

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("profile")
        self.bot.tree.remove_command("reputation")
        self.bot.tree.remove_command("admin_rep")
        self.bot.tree.remove_command("agency_rep")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReputationCog(bot))
    log.info("ReputationCog loaded.")
