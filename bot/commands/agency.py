from __future__ import annotations

"""
Agency Cog  –  drop into bot/commands/ and add "bot.commands.agency" to EXTENSIONS.

Required .env vars:
  ADMIN_DISCORD_ID       – your Discord user ID (receives DM approval requests)
  REQUIRE_HOST_APPROVAL  – "1" = agency owner must approve hosts (default: 0)
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.agency_queries import (
    add_host_note,
    approve_agency,
    approve_host_join,
    create_agency,
    delete_pending_registration,
    get_agency_by_id,
    get_agency_by_name,
    get_deals_for_agency,
    get_host_by_discord_id,
    get_hosts_for_agency,
    get_notes_for_host,
    get_pending_registration,
    join_agency,
    leave_agency,
    list_all_agencies,
    store_pending_registration,
)

log = logging.getLogger("dealbot.agency")

def _admin_id() -> int:
    return int(os.getenv("ADMIN_DISCORD_ID", "0"))

def _require_host_approval() -> bool:
    return os.getenv("REQUIRE_HOST_APPROVAL", "0") == "1"

async def _dm(bot: commands.Bot, user_id: int, content: str) -> bool:
    try:
        user = await bot.fetch_user(user_id)
        await user.send(content)
        return True
    except Exception as exc:
        log.warning(f"Could not DM {user_id}: {exc}")
        return False

async def _get_owned_agency(db, owner_discord_id: int):
    """Return the approved agency owned by this Discord user, or None."""
    async with db.execute(
        "SELECT * FROM agencies WHERE owner_discord_id=? AND status='approved'",
        (owner_discord_id,),
    ) as cur:
        return await cur.fetchone()

class AgencyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # /register_agency ---------------------------------------------------

    @app_commands.command(
        name="register_agency",
        description="Request registration of a new agency. The bot admin must approve it.",
    )
    @app_commands.describe(name="Unique name for your agency (max 64 chars).")
    async def register_agency(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        name = name.strip()
        if not name or len(name) > 64:
            await interaction.followup.send("❌ Agency name must be 1–64 characters.", ephemeral=True)
            return

        db = await get_db()
        if await get_agency_by_name(db, name):
            await interaction.followup.send(f"❌ An agency named **{name}** already exists.", ephemeral=True)
            return
        if await get_pending_registration(db, name):
            await interaction.followup.send(f"⏳ A request for **{name}** is already pending admin approval.", ephemeral=True)
            return

        await store_pending_registration(db, name, interaction.user.id)

        admin_id = _admin_id()
        u = interaction.user
        dm_ok = await _dm(
            self.bot, admin_id,
            f"📋 **Agency Registration Request**\n\n"
            f"**{u}** (`{u.id}`) wants to register agency:\n> **{name}**\n\n"
            f"Approve with:\n`/approve_agency` → name: `{name}` | user_id: `{u.id}`"
        )
        if dm_ok:
            await interaction.followup.send(
                f"✅ Registration request for **{name}** sent to the admin. You'll be DM'd once approved.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⚠️ Request recorded, but the admin couldn't be reached by DM. Contact them directly.",
                ephemeral=True,
            )
        log.info(f"Agency registration requested: '{name}' by {u} ({u.id})")

    # /approve_agency ----------------------------------------------------

    @app_commands.command(
        name="approve_agency",
        description="[ADMIN ONLY] Approve a pending agency registration.",
    )
    @app_commands.describe(
        name="The agency name to approve.",
        user_id="Discord user ID who will become the agency owner.",
    )
    async def approve_agency_cmd(
        self, interaction: discord.Interaction, name: str, user_id: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id != _admin_id():
            await interaction.followup.send("🔒 Only the bot admin can approve agencies.", ephemeral=True)
            return

        name = name.strip()
        try:
            owner_id = int(user_id.strip())
        except ValueError:
            await interaction.followup.send("❌ `user_id` must be a valid integer.", ephemeral=True)
            return

        db = await get_db()
        existing = await get_agency_by_name(db, name)
        if existing and existing["status"] == "approved":
            await interaction.followup.send(f"❌ Agency **{name}** is already approved.", ephemeral=True)
            return
        if not existing:
            await create_agency(db, name)

        ok = await approve_agency(db, name, owner_id)
        if not ok:
            await interaction.followup.send(f"❌ Could not approve **{name}**. Check the name.", ephemeral=True)
            return

        await delete_pending_registration(db, name)
        await _dm(self.bot, owner_id,
                  f"🎉 Your agency **{name}** has been approved!\n"
                  f"Hosts can join with `/join_agency` → `name: {name}`.")
        await interaction.followup.send(f"✅ Agency **{name}** approved. Owner: <@{owner_id}>.", ephemeral=True)
        log.info(f"Agency '{name}' approved by admin, owner={owner_id}")

    # /join_agency -------------------------------------------------------

    @app_commands.command(
        name="join_agency",
        description="Register yourself as a host under an approved agency.",
    )
    @app_commands.describe(name="Name of the agency to join.")
    async def join_agency_cmd(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        agency = await get_agency_by_name(db, name.strip())
        if not agency or agency["status"] != "approved":
            await interaction.followup.send(f"❌ No approved agency named **{name}** found.", ephemeral=True)
            return

        host = await get_host_by_discord_id(db, interaction.user.id)
        if host and host["agency_id"] == agency["id"] and host["join_status"] == "active":
            await interaction.followup.send(f"ℹ️ You are already an active member of **{agency['name']}**.", ephemeral=True)
            return

        needs_approval = _require_host_approval()
        await join_agency(db, interaction.user.id, agency["id"], pending_approval=needs_approval)

        if needs_approval:
            if agency["owner_discord_id"]:
                await _dm(self.bot, agency["owner_discord_id"],
                          f"👤 **Host Join Request**\n"
                          f"**{interaction.user}** (`{interaction.user.id}`) wants to join **{agency['name']}**.\n"
                          f"Approve with `/approve_host user_id: {interaction.user.id}`")
            await interaction.followup.send(f"⏳ Join request for **{agency['name']}** sent to the owner.", ephemeral=True)
        else:
            await interaction.followup.send(f"✅ You are now a member of **{agency['name']}**!", ephemeral=True)
        log.info(f"{interaction.user} ({interaction.user.id}) joined agency '{agency['name']}'")

    # /approve_host ------------------------------------------------------

    @app_commands.command(
        name="approve_host",
        description="[Agency Owner] Approve a pending host join request.",
    )
    @app_commands.describe(user_id="Discord user ID of the host to approve.")
    async def approve_host_cmd(self, interaction: discord.Interaction, user_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(user_id.strip())
        except ValueError:
            await interaction.followup.send("❌ `user_id` must be a valid integer.", ephemeral=True)
            return

        db = await get_db()
        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency:
            await interaction.followup.send("❌ You are not the owner of any approved agency.", ephemeral=True)
            return

        host = await get_host_by_discord_id(db, target_id)
        if not host or host["agency_id"] != agency["id"]:
            await interaction.followup.send("❌ That user has no pending request for your agency.", ephemeral=True)
            return

        ok = await approve_host_join(db, target_id)
        if not ok:
            await interaction.followup.send("❌ Approval failed — host may already be active.", ephemeral=True)
            return

        await _dm(self.bot, target_id, f"✅ You've been approved to join **{agency['name']}**!")
        await interaction.followup.send(f"✅ <@{target_id}> is now an active member of **{agency['name']}**.", ephemeral=True)

    # /leave_agency ------------------------------------------------------

    @app_commands.command(name="leave_agency", description="Leave your current agency.")
    async def leave_agency_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        host = await get_host_by_discord_id(db, interaction.user.id)
        if not host or not host["agency_id"]:
            await interaction.followup.send("ℹ️ You are not a member of any agency.", ephemeral=True)
            return
        await leave_agency(db, interaction.user.id)
        await interaction.followup.send("✅ You have left your agency.", ephemeral=True)

    # /my_agency ---------------------------------------------------------

    @app_commands.command(name="my_agency", description="View your agency membership status.")
    async def my_agency(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        host = await get_host_by_discord_id(db, interaction.user.id)
        if not host or not host["agency_id"]:
            await interaction.followup.send("ℹ️ You are not a member of any agency. Use `/join_agency`.", ephemeral=True)
            return
        agency = await get_agency_by_id(db, host["agency_id"])
        if not agency:
            await interaction.followup.send("⚠️ Agency data missing. Contact the bot admin.", ephemeral=True)
            return
        status_label = "✅ Active" if host["join_status"] == "active" else "⏳ Pending Approval"
        embed = discord.Embed(title="🏢 Your Agency", colour=discord.Colour.blurple())
        embed.add_field(name="Agency", value=agency["name"], inline=True)
        embed.add_field(name="Status", value=status_label, inline=True)
        embed.add_field(
            name="Owner",
            value=f"<@{agency['owner_discord_id']}>" if agency["owner_discord_id"] else "_unknown_",
            inline=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # /agency_info -------------------------------------------------------

    @app_commands.command(name="agency_info", description="View information about an agency.")
    @app_commands.describe(name="Agency name.")
    async def agency_info(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        agency = await get_agency_by_name(db, name.strip())
        if not agency or agency["status"] != "approved":
            await interaction.followup.send(f"❌ No approved agency named **{name}** found.", ephemeral=True)
            return
        hosts = await get_hosts_for_agency(db, agency["id"])
        active_count = sum(1 for h in hosts if h["join_status"] == "active")
        embed = discord.Embed(title=f"🏢 {agency['name']}", colour=discord.Colour.blurple())
        embed.add_field(
            name="Owner",
            value=f"<@{agency['owner_discord_id']}>" if agency["owner_discord_id"] else "_unknown_",
            inline=True,
        )
        embed.add_field(name="Active Hosts", value=str(active_count), inline=True)
        embed.set_footer(text=f"Created: {agency['created_at']}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # /list_agencies -----------------------------------------------------

    @app_commands.command(name="list_agencies", description="List all approved agencies.")
    async def list_agencies(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        agencies = await list_all_agencies(db)
        if not agencies:
            await interaction.followup.send("📭 No approved agencies yet.", ephemeral=True)
            return
        lines = [
            f"• **{a['name']}** — owner: <@{a['owner_discord_id']}>"
            if a["owner_discord_id"] else f"• **{a['name']}**"
            for a in agencies
        ]
        embed = discord.Embed(
            title="🏢 Approved Agencies",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # /agency_deals ------------------------------------------------------

    @app_commands.command(
        name="agency_deals",
        description="[Agency Owner] View deals involving your agency's hosts.",
    )
    async def agency_deals(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency:
            await interaction.followup.send("❌ You are not the owner of any approved agency.", ephemeral=True)
            return
        deals = await get_deals_for_agency(db, agency["id"])
        if not deals:
            await interaction.followup.send(f"📭 No deals found for **{agency['name']}**.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"📊 Deals — {agency['name']}",
            colour=discord.Colour.gold(),
            description=f"Showing {min(len(deals), 20)} of {len(deals)} deal(s).",
        )
        for deal in deals[:20]:
            embed.add_field(
                name=f"`{deal['deal_uuid'][:8]}…` · {deal['status'].upper()}",
                value=(
                    f"{deal['party_a_username']} ↔ {deal['party_b_username']}\n"
                    f"{deal['network_name']} · {deal['currency']} {deal['amount']:,.2f}\n"
                    f"Due: {deal['due_date'][:10]}"
                ),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # /agency_hosts ------------------------------------------------------

    @app_commands.command(
        name="agency_hosts",
        description="[Agency Owner] List all hosts in your agency.",
    )
    async def agency_hosts(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency:
            await interaction.followup.send("❌ You are not the owner of any approved agency.", ephemeral=True)
            return
        hosts = await get_hosts_for_agency(db, agency["id"])
        if not hosts:
            await interaction.followup.send(f"📭 No hosts in **{agency['name']}** yet.", ephemeral=True)
            return
        lines = [
            f"{'✅' if h['join_status'] == 'active' else '⏳'} <@{h['discord_id']}> (`{h['discord_id']}`) — {h['join_status']}"
            for h in hosts
        ]
        embed = discord.Embed(
            title=f"👥 Hosts — {agency['name']}",
            description="\n".join(lines),
            colour=discord.Colour.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # /add_host_note -----------------------------------------------------

    @app_commands.command(
        name="add_host_note",
        description="[Agency Owner] Add a note or flag to a host in your agency.",
    )
    @app_commands.describe(
        host_id="Discord user ID of the host.",
        note="Note or flag text (max 1000 chars).",
    )
    async def add_host_note_cmd(
        self, interaction: discord.Interaction, host_id: str, note: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(host_id.strip())
        except ValueError:
            await interaction.followup.send("❌ `host_id` must be a valid integer.", ephemeral=True)
            return
        note = note.strip()
        if not note or len(note) > 1000:
            await interaction.followup.send("❌ Note must be 1–1000 characters.", ephemeral=True)
            return

        db = await get_db()
        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency:
            await interaction.followup.send("❌ You are not the owner of any approved agency.", ephemeral=True)
            return
        host = await get_host_by_discord_id(db, target_id)
        if not host or host["agency_id"] != agency["id"]:
            await interaction.followup.send("❌ That user is not a member of your agency.", ephemeral=True)
            return

        note_id = await add_host_note(
            db,
            host_discord_id=target_id,
            author_discord_id=interaction.user.id,
            note=note,
        )
        await interaction.followup.send(f"✅ Note #{note_id} added to <@{target_id}>.", ephemeral=True)
        log.info(f"Note #{note_id} added to host {target_id} in agency '{agency['name']}'")

    # /view_host_notes ---------------------------------------------------

    @app_commands.command(
        name="view_host_notes",
        description="[Agency Owner] View notes/flags for a host in your agency.",
    )
    @app_commands.describe(host_id="Discord user ID of the host.")
    async def view_host_notes(self, interaction: discord.Interaction, host_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(host_id.strip())
        except ValueError:
            await interaction.followup.send("❌ `host_id` must be a valid integer.", ephemeral=True)
            return

        db = await get_db()
        agency = await _get_owned_agency(db, interaction.user.id)
        if not agency:
            await interaction.followup.send("❌ You are not the owner of any approved agency.", ephemeral=True)
            return
        host = await get_host_by_discord_id(db, target_id)
        if not host or host["agency_id"] != agency["id"]:
            await interaction.followup.send("❌ That user is not a member of your agency.", ephemeral=True)
            return

        notes = await get_notes_for_host(db, target_id)
        if not notes:
            await interaction.followup.send(f"📭 No notes found for <@{target_id}>.", ephemeral=True)
            return

        embed = discord.Embed(title=f"📝 Notes for <@{target_id}>", colour=discord.Colour.orange())
        for n in notes[:15]:
            embed.add_field(
                name=f"#{n['id']} · {n['created_at'][:10]} by <@{n['author_discord_id']}>",
                value=n["note"],
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgencyCog(bot))
    log.info("AgencyCog loaded.")
