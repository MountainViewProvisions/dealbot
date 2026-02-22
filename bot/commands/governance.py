from __future__ import annotations

"""
GovernanceCog
=============
Slash commands for Probation Mode, Escrow Confidence, and Governance Config.

Command groups
--------------
/governance config set    – Admin: configure thresholds
/governance config view   – View current governance config
/governance probation view <user>          – View a user's probation status
/governance probation list                 – Admin: list all probation hosts
/governance probation lift <user>          – Admin: manually lift probation
/governance probation set  <user>          – Admin: manually place on probation
/governance probation approve_deal <user>  – Agency owner: approve a probation host's deal
/governance escrow view <deal_id>          – Admin: view escrow flag for a deal
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.agency_queries import get_host_by_discord_id
from bot.database.governance_queries import (
    get_all_probation_hosts,
    get_escrow_flag,
    get_governance_config,
    get_probation_log,
    get_probation_status,
    set_agency_deal_approved,
    set_probation,
    upsert_governance_config,
)
from bot.database.queries import log_command
from bot.database.reputation_queries import get_host_reputation
from bot.services.governance_service import check_probation_restrictions, evaluate_probation
from bot.utils.embeds import error_embed, info_embed, success_embed

log = logging.getLogger("dealbot.governance_cog")

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


async def _get_owned_agency(db, owner_discord_id: int):
    async with db.execute(
        "SELECT * FROM agencies WHERE owner_discord_id=? AND status='approved'",
        (owner_discord_id,),
    ) as cur:
        return await cur.fetchone()


class GovernanceConfigGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="config", description="Governance configuration.")

    @app_commands.command(
        name="view",
        description="View current governance configuration for this server.",
    )
    async def config_view(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        cfg = await get_governance_config(db, interaction.guild_id)

        embed = discord.Embed(
            title="⚙️ Governance Configuration",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="🔒 Probation",
            value=(
                f"Threshold: **{cfg['probation_threshold']}** rep\n"
                f"Max open deals: **{cfg['probation_max_open_deals']}**\n"
                f"High-value block: **>{cfg['high_value_deal_threshold']:,.0f}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🛡️ Escrow",
            value=(
                f"Required score: **{cfg['required_confidence_score']}**\n"
                f"Mediator auto-assign: **{'On' if cfg['escrow_mediator_enabled'] else 'Off'}**\n"
                f"High-value threshold: **>{cfg['high_value_deal_threshold']:,.0f}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="📊 Analytics",
            value=f"Weekly DM summaries: **{'On' if cfg['analytics_weekly_dm'] else 'Off'}**",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="set",
        description="[Admin] Configure governance thresholds for this server.",
    )
    @app_commands.describe(
        probation_threshold="Rep score below which a host enters probation (default 300.0).",
        probation_max_open="Max open deals while on probation (default 2.0).",
        high_value_threshold="Deal amount above which extra checks apply (default 5000.0).",
        required_confidence="Min rep score for escrow confidence pass (default 350.0).",
        escrow_mediator="Auto-assign mediator when confidence is low (default True).",
        analytics_weekly_dm="Send weekly analytics DMs to agency owners (default True).",
    )
    async def config_set(
        self,
        interaction: discord.Interaction,
        probation_threshold: float = 300.0,
        probation_max_open: float = 2.0,
        high_value_threshold: float = 5000.0,
        required_confidence: float = 350.0,
        escrow_mediator: bool = True,
        analytics_weekly_dm: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Administrator permission required."), ephemeral=True
            )
            return

        if not (0.0 <= probation_threshold <= 1000.0):
            await interaction.followup.send(
                embed=error_embed("probation_threshold must be 0–1000."), ephemeral=True
            )
            return
        if not (1.0 <= probation_max_open <= 50.0):
            await interaction.followup.send(
                embed=error_embed("probation_max_open must be 1–50."), ephemeral=True
            )
            return
        if not (0.0 <= required_confidence <= 1000.0):
            await interaction.followup.send(
                embed=error_embed("required_confidence must be 0–1000."), ephemeral=True
            )
            return

        db = await get_db()
        await upsert_governance_config(
            db,
            guild_id=interaction.guild_id,
            probation_threshold=probation_threshold,
            probation_max_open_deals=int(probation_max_open),
            high_value_deal_threshold=high_value_threshold,
            required_confidence_score=required_confidence,
            escrow_mediator_enabled=escrow_mediator,
            analytics_weekly_dm=analytics_weekly_dm,
        )
        await interaction.followup.send(
            embed=success_embed(
                f"✅ Governance config updated.\n"
                f"Probation threshold: **{probation_threshold}** | "
                f"Max open (probation): **{int(probation_max_open)}** | "
                f"High-value: **>{high_value_threshold:,.0f}** | "
                f"Escrow required: **{required_confidence}** | "
                f"Mediator: **{'On' if escrow_mediator else 'Off'}**"
            ),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id, "governance config set", True
        )


class GovernanceProbationGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="probation", description="Probation management commands.")

    @app_commands.command(
        name="view",
        description="View a user's probation status and history.",
    )
    @app_commands.describe(user="The user to inspect (defaults to yourself).")
    async def probation_view(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user

        if target.id != interaction.user.id and not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("You can only view your own probation status."),
                ephemeral=True,
            )
            return

        db = await get_db()
        status = await get_probation_status(db, target.id)
        history = await get_probation_log(db, target.id, limit=5)

        if not status:
            await interaction.followup.send(
                embed=info_embed(
                    f"Probation Status — {target.display_name}",
                    "No probation record found. Host is in good standing.",
                ),
                ephemeral=True,
            )
            return

        on_prob = bool(status["on_probation"])
        badge = "🔒 ON PROBATION" if on_prob else "✅ Not on Probation"
        colour = 0xED4245 if on_prob else 0x57F287

        embed = discord.Embed(
            title=f"Probation — {target.display_name}",
            colour=colour,
        )
        embed.add_field(name="Status",     value=badge, inline=True)
        embed.add_field(name="Reputation", value=str(status["reputation"]), inline=True)
        if on_prob and status["probation_since"]:
            embed.add_field(
                name="Since", value=str(status["probation_since"])[:10], inline=True
            )
        agency_approved = bool(status["agency_deal_approved"])
        embed.add_field(
            name="Agency Deal Approved",
            value="✅ Yes" if agency_approved else "❌ No",
            inline=True,
        )

        if history:
            lines = [
                f"`{h['created_at'][:10]}` **{h['event'].replace('_', ' ')}** "
                f"(rep={h['reputation_at']}, by {h['triggered_by']})"
                for h in history
            ]
            embed.add_field(
                name="Recent History",
                value="\n".join(lines),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="list",
        description="[Admin] List all hosts currently on probation.",
    )
    async def probation_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Administrator permission required."), ephemeral=True
            )
            return

        db = await get_db()
        hosts = await get_all_probation_hosts(db)

        if not hosts:
            await interaction.followup.send(
                embed=info_embed("Probation List", "No hosts are currently on probation."),
                ephemeral=True,
            )
            return

        lines = [
            f"🔒 <@{h['discord_id']}> — rep **{h['reputation']}**"
            f"{' (agency: ' + str(h['agency_id']) + ')' if h['agency_id'] else ''}"
            for h in hosts
        ]
        embed = discord.Embed(
            title=f"🔒 Hosts on Probation ({len(hosts)})",
            description="\n".join(lines),
            colour=0xED4245,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="lift",
        description="[Admin] Manually lift probation from a user.",
    )
    @app_commands.describe(user="The user to lift probation from.", reason="Reason for manual lift.")
    async def probation_lift(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = "admin override"
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Administrator permission required."), ephemeral=True
            )
            return

        db = await get_db()
        rep_row = await get_host_reputation(db, user.id)
        rep = rep_row["reputation"] if rep_row else 500

        await set_probation(db, user.id, False, rep, triggered_by=f"admin:{interaction.user.id}")
        await interaction.followup.send(
            embed=success_embed(
                f"✅ Probation lifted for **{user.display_name}**.\nReason: _{reason}_"
            ),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id,
            "governance probation lift", True, f"user={user.id}"
        )

    @app_commands.command(
        name="set",
        description="[Admin] Manually place a user on probation.",
    )
    @app_commands.describe(user="The user to place on probation.", reason="Reason.")
    async def probation_set(
        self, interaction: discord.Interaction, user: discord.Member, reason: str = "admin decision"
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Administrator permission required."), ephemeral=True
            )
            return

        db = await get_db()
        rep_row = await get_host_reputation(db, user.id)
        rep = rep_row["reputation"] if rep_row else 500

        await set_probation(db, user.id, True, rep, triggered_by=f"admin:{interaction.user.id}")
        await interaction.followup.send(
            embed=success_embed(
                f"🔒 **{user.display_name}** has been placed on probation.\nReason: _{reason}_"
            ),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id,
            "governance probation set", True, f"user={user.id}"
        )

    @app_commands.command(
        name="approve_deal",
        description="[Agency Owner] Approve a probation host to create deals.",
    )
    @app_commands.describe(user="The probation host to approve for deal creation.")
    async def probation_approve_deal(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()

        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency and not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Only agency owners or admins can approve probation deals."),
                ephemeral=True,
            )
            return

        host = await get_host_by_discord_id(db, user.id)
        if not host:
            await interaction.followup.send(
                embed=error_embed("That user has no host record."), ephemeral=True
            )
            return

        if agency and host["agency_id"] != agency["id"]:
            await interaction.followup.send(
                embed=error_embed("That user is not a member of your agency."), ephemeral=True
            )
            return

        await set_agency_deal_approved(db, user.id, True)
        await interaction.followup.send(
            embed=success_embed(
                f"✅ **{user.display_name}** is approved to create deals "
                "despite probation status.\n"
                "This approval resets after their next deal is created."
            ),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id,
            "governance probation approve_deal", True, f"user={user.id}"
        )


class GovernanceEscrowGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="escrow", description="Escrow confidence commands.")

    @app_commands.command(
        name="view",
        description="View the escrow confidence flag for a deal.",
    )
    @app_commands.describe(deal_id="Internal deal integer ID.")
    async def escrow_view(
        self, interaction: discord.Interaction, deal_id: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _is_admin(interaction):
            await interaction.followup.send(
                embed=error_embed("Administrator permission required."), ephemeral=True
            )
            return

        db = await get_db()
        flag = await get_escrow_flag(db, deal_id)

        if not flag:
            await interaction.followup.send(
                embed=info_embed("Escrow Flag", f"No escrow flag found for deal #{deal_id}."),
                ephemeral=True,
            )
            return

        blocked = bool(flag["blocked"])
        mediator = bool(flag["mediator_assigned"])
        colour = 0xED4245 if blocked else (0xFEE75C if mediator else 0x57F287)

        embed = discord.Embed(
            title=f"🛡️ Escrow Flag — Deal #{deal_id}",
            colour=colour,
        )
        embed.add_field(name="Party A Score", value=f"{flag['confidence_score_a']:.0f}", inline=True)
        embed.add_field(name="Party B Score", value=f"{flag['confidence_score_b']:.0f}", inline=True)
        embed.add_field(
            name="Blocked",
            value="🚫 Yes" if blocked else "✅ No",
            inline=True,
        )
        embed.add_field(
            name="Mediator Assigned",
            value="✅ Yes" if mediator else "No",
            inline=True,
        )
        if flag["block_reason"]:
            embed.add_field(name="Block Reason", value=flag["block_reason"], inline=False)
        embed.set_footer(text=f"Created: {str(flag['created_at'])[:16]}")
        await interaction.followup.send(embed=embed, ephemeral=True)


class GovernanceGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="governance", description="Governance and compliance commands.")
        self.add_command(GovernanceConfigGroup())
        self.add_command(GovernanceProbationGroup())
        self.add_command(GovernanceEscrowGroup())


class GovernanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.tree.add_command(GovernanceGroup())

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("governance")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GovernanceCog(bot))
    log.info("GovernanceCog loaded.")
