from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.queries import (
    get_all_networks,
    get_user_by_discord_id,
    search_deals_for_user,
    count_deals_for_user,
    log_command,
)
from bot.services import deal_service
from bot.utils.embeds import deal_embed, deal_list_embed, error_embed, success_embed
from bot.utils.pagination import PaginatorView, chunk_list
from bot.utils.validators import validate_dispute_reason, validate_note_text
from bot.utils.wizard import DealWizard

log = logging.getLogger(__name__)

_PAGE_SIZE = 8

class DealCommands(commands.GroupCog, name="deal"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="start", description="Create a new deal via DM wizard.")
    @app_commands.describe(counterparty="The Discord user you want to deal with.")
    async def deal_start(
        self, interaction: discord.Interaction, counterparty: discord.Member
    ):
        db = await get_db()
        if counterparty.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("You cannot create a deal with yourself."), ephemeral=True
            )
            await log_command(db, interaction.guild_id, interaction.user.id, "deal start", False, "self-deal")
            return
        if counterparty.bot:
            await interaction.response.send_message(
                embed=error_embed("You cannot create a deal with a bot."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            "📬 Check your DMs — the wizard is ready!", ephemeral=True
        )
        wizard = DealWizard(
            self.bot, interaction.user, counterparty,
            guild_id=interaction.guild_id or 0,
        )
        success, result = await wizard.run()
        await log_command(db, interaction.guild_id, interaction.user.id,
                          "deal start", success, result if not success else None)

    @app_commands.command(name="confirm", description="Confirm a pending deal.")
    @app_commands.describe(deal_id="The 8-character deal ID.")
    async def deal_confirm(self, interaction: discord.Interaction, deal_id: str):
        await interaction.response.defer(ephemeral=True)
        db  = await get_db()
        ok, msg = await deal_service.confirm_deal(db, deal_id.upper(), interaction.user.id)
        await interaction.followup.send(
            embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "deal confirm", ok, msg if not ok else None)

    @app_commands.command(name="complete", description="Confirm deal completion.")
    @app_commands.describe(deal_id="The 8-character deal ID.")
    async def deal_complete(self, interaction: discord.Interaction, deal_id: str):
        await interaction.response.defer(ephemeral=True)
        db  = await get_db()
        ok, msg = await deal_service.complete_deal(db, deal_id.upper(), interaction.user.id)
        await interaction.followup.send(
            embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "deal complete", ok)

    @app_commands.command(name="dispute", description="Mark a deal as disputed.")
    @app_commands.describe(
        deal_id="The 8-character deal ID.",
        reason="Brief reason for the dispute (10–500 chars).",
    )
    async def deal_dispute(
        self, interaction: discord.Interaction, deal_id: str, reason: str
    ):
        await interaction.response.defer(ephemeral=True)
        valid, err = validate_dispute_reason(reason)
        if not valid:
            await interaction.followup.send(embed=error_embed(err), ephemeral=True)
            return
        db  = await get_db()
        ok, msg = await deal_service.dispute_deal(
            db, deal_id.upper(), interaction.user.id, reason.strip(),
            guild_id=interaction.guild_id,
        )
        await interaction.followup.send(
            embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "deal dispute", ok)

    @app_commands.command(name="note", description="Append a private note to a deal.")
    @app_commands.describe(
        deal_id="The 8-character deal ID.",
        note="Your note text (append-only, 3–1000 chars).",
    )
    async def deal_note(
        self, interaction: discord.Interaction, deal_id: str, note: str
    ):
        await interaction.response.defer(ephemeral=True)
        valid, err = validate_note_text(note)
        if not valid:
            await interaction.followup.send(embed=error_embed(err), ephemeral=True)
            return
        db  = await get_db()
        ok, msg = await deal_service.add_note(db, deal_id.upper(), interaction.user.id, note.strip())
        await interaction.followup.send(
            embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True
        )
        await log_command(db, interaction.guild_id, interaction.user.id, "deal note", ok)

    @app_commands.command(name="view", description="View deal details and notes.")
    @app_commands.describe(
        deal_id="The 8-character deal ID.",
        audit="Include the full audit trail (default: false).",
    )
    async def deal_view(
        self,
        interaction: discord.Interaction,
        deal_id: str,
        audit: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        db   = await get_db()
        data = await deal_service.get_deal_details(
            db, deal_id.upper(), interaction.user.id, include_audit=audit
        )
        if data is None:
            await interaction.followup.send(
                embed=error_embed("Deal not found or you are not a party to it."),
                ephemeral=True,
            )
            return
        embed = deal_embed(data["deal"], data["notes"], data.get("audit"))
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(db, interaction.guild_id, interaction.user.id, "deal view", True)

    @app_commands.command(name="list", description="List your deals with pagination.")
    @app_commands.describe(
        status="Filter by status (leave blank for all).",
        network="Filter by network name (leave blank for all).",
    )
    async def deal_list(
        self,
        interaction: discord.Interaction,
        status: Optional[str] = None,
        network: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        db       = await get_db()
        user_row = await get_user_by_discord_id(db, interaction.user.id)
        if not user_row:
            await interaction.followup.send(
                embed=error_embed("No deals found for your account."), ephemeral=True
            )
            return

        network_id: Optional[int] = None
        if network:
            net = await get_all_networks(db)
            for n in net:
                if n["name"].lower() == network.lower():
                    network_id = n["id"]
                    break
            if not network_id:
                await interaction.followup.send(
                    embed=error_embed(f"Unknown network: `{network}`"), ephemeral=True
                )
                return

        total = await count_deals_for_user(db, user_row["id"], status, network_id)
        if total == 0:
            await interaction.followup.send(
                embed=error_embed("No deals match your filters."), ephemeral=True
            )
            return

        pages = []
        offset = 0
        page_num = 1
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        while offset < total:
            rows = await search_deals_for_user(
                db, user_row["id"], status, network_id,
                limit=_PAGE_SIZE, offset=offset,
            )
            pages.append(deal_list_embed(rows, title="Your Deals", page=page_num, total_pages=total_pages))
            offset += _PAGE_SIZE
            page_num += 1

        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
        else:
            view = PaginatorView(pages)
            msg = await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
            view.message = msg

        await log_command(db, interaction.guild_id, interaction.user.id, "deal list", True)

    @app_commands.command(name="search", description="Find a deal by its ID.")
    @app_commands.describe(deal_id="The 8-character deal ID to look up.")
    async def deal_search(self, interaction: discord.Interaction, deal_id: str):
        await interaction.response.defer(ephemeral=True)
        db   = await get_db()
        data = await deal_service.get_deal_details(
            db, deal_id.upper(), interaction.user.id
        )
        if data is None:
            await interaction.followup.send(
                embed=error_embed("Deal not found or you are not a party to it."),
                ephemeral=True,
            )
            return
        embed = deal_embed(data["deal"], data["notes"])
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DealCommands(bot))
