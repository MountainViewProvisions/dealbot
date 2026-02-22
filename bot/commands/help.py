from __future__ import annotations

"""
HelpCog
=======
Dynamic, permission-aware DM-based help menu for Directive DealBot.

Design
------
- Single /help command (optional `category` autocomplete argument).
- Sends a paginated DM with a Select dropdown for category navigation.
- Permission-aware: filters what each user can see based on their role,
  guild permissions, and agency ownership (checked via the DB layer).
- Curated command catalog with rich arg strings and prerequisite notes.
- Dynamic fallback: any command registered in bot.tree that is NOT in the
  catalog is auto-discovered and appended to an "Other" section — so new
  cogs appear automatically with no manual update needed.
- If the user's DMs are blocked, responds politely in-channel (ephemeral).

Styling matches the existing embed palette (blurple, STATUS_COLOR, footers).
"""

import logging
import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.database.queries import log_command

log = logging.getLogger("dealbot.help")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

_OWNER_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("BOT_OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}
_MEDIATOR_ROLE = os.getenv("MEDIATOR_ROLE_NAME", "DealMediator")

# ---------------------------------------------------------------------------
# Permission tiers
# ---------------------------------------------------------------------------

class HelpLevel(IntEnum):
    """Ordered permission tiers used throughout the help system."""
    EVERYONE      = 0  # Any Discord user
    AGENCY_MEMBER = 1  # Active member of any approved agency
    AGENCY_OWNER  = 2  # Approved agency owner
    ADMIN         = 3  # Server admin, mediator, or bot owner
    BOT_OWNER     = 4  # Listed in BOT_OWNER_IDS env var

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CmdEntry:
    """One command's help record."""
    name: str           # Full slash path, e.g. "deal start"
    description: str    # Short one-liner description
    args: str = ""      # Argument synopsis, e.g. "<deal_id> [audit:bool]"
    level: HelpLevel = HelpLevel.EVERYONE
    note: str = ""      # Prerequisite or context note shown in italics

@dataclass
class Category:
    """A grouping of related commands shown as one embed page."""
    key: str            # Unique key used by Select option values
    emoji: str
    title: str          # Short label for the Select menu option
    subtitle: str       # Longer description shown in the embed
    commands: list[CmdEntry]
    min_level: HelpLevel = HelpLevel.EVERYONE  # Hide this category entirely below this level

# ---------------------------------------------------------------------------
# Curated command catalog
# ---------------------------------------------------------------------------

CATEGORIES: list[Category] = [

    Category(
        key="deals",
        emoji="📋",
        title="Deals",
        subtitle="Create, track, confirm, and resolve deal contracts between two parties.",
        min_level=HelpLevel.EVERYONE,
        commands=[
            CmdEntry(
                name="deal start",
                description="Open the DM wizard to create a new deal with a counterparty.",
                args="<@counterparty>",
                note="Both parties must confirm the deal before it becomes active.",
            ),
            CmdEntry(
                name="deal confirm",
                description="Confirm your side of a pending deal.",
                args="<deal_id>",
                note="The deal becomes ACTIVE once both parties have confirmed.",
            ),
            CmdEntry(
                name="deal complete",
                description="Mark your side of a deal as complete.",
                args="<deal_id>",
                note="Deal is marked COMPLETED once both parties confirm. Overdue deals also use this.",
            ),
            CmdEntry(
                name="deal dispute",
                description="Flag a deal as disputed and freeze reputation changes.",
                args="<deal_id> <reason>",
                note="Reason must be 10–500 characters. Subject to a per-server cooldown.",
            ),
            CmdEntry(
                name="deal note",
                description="Append a private note to one of your deals.",
                args="<deal_id> <note>",
                note="Notes are append-only and visible to both parties. Max 1 000 characters.",
            ),
            CmdEntry(
                name="deal view",
                description="View full deal details, confirmation state, and optionally the audit trail.",
                args="<deal_id> [audit:true]",
            ),
            CmdEntry(
                name="deal list",
                description="Browse your deals with pagination. Supports status and network filters.",
                args="[status] [network]",
                note="Status values: active, pending_confirmation, completed, disputed, overdue, defaulted.",
            ),
            CmdEntry(
                name="deal search",
                description="Look up a specific deal by its 8-character ID.",
                args="<deal_id>",
            ),
        ],
    ),

    Category(
        key="profile",
        emoji="👤",
        title="Profile & Reputation",
        subtitle="View reputation scores, tier standings, deal history, and leaderboards.",
        min_level=HelpLevel.EVERYONE,
        commands=[
            CmdEntry(
                name="profile view",
                description="View a host's full reputation profile — score, tier, streak, disputes, and agency.",
                args="[@user]",
                note="Defaults to your own profile if no user is provided.",
            ),
            CmdEntry(
                name="reputation leaderboard",
                description="Display the top 10 hosts by reputation score server-wide.",
            ),
            CmdEntry(
                name="reputation history",
                description="View the last 20 reputation change events for a host.",
                args="[@user]",
                note="Only admins may view another user's history.",
            ),
        ],
    ),

    Category(
        key="agency",
        emoji="🏢",
        title="Agency",
        subtitle="Register, join, and manage agencies — networks of verified deal hosts.",
        min_level=HelpLevel.EVERYONE,
        commands=[
            CmdEntry(
                name="list_agencies",
                description="List all approved agencies and their owners.",
            ),
            CmdEntry(
                name="agency_info",
                description="View details and active host count for a specific agency.",
                args="<name>",
            ),
            CmdEntry(
                name="register_agency",
                description="Submit a request to register a new agency (requires bot admin approval).",
                args="<name>",
                note="Agency name must be unique and ≤ 64 characters. Admin is notified by DM.",
            ),
            CmdEntry(
                name="join_agency",
                description="Register yourself as a host under an approved agency.",
                args="<name>",
                note="If the server requires host approval, the agency owner is notified to approve you.",
            ),
            CmdEntry(
                name="leave_agency",
                description="Leave your current agency. Your deal history is preserved.",
            ),
            CmdEntry(
                name="my_agency",
                description="View your current agency membership status (active or pending).",
            ),
            CmdEntry(
                name="approve_host",
                description="Approve a pending host join request for your agency.",
                args="<user_id>",
                level=HelpLevel.AGENCY_OWNER,
                note="Only available when REQUIRE_HOST_APPROVAL=1 is set in the bot's environment.",
            ),
            CmdEntry(
                name="agency_deals",
                description="View all deals involving your agency's active hosts.",
                level=HelpLevel.AGENCY_OWNER,
            ),
            CmdEntry(
                name="agency_hosts",
                description="List all hosts in your agency with their join status.",
                level=HelpLevel.AGENCY_OWNER,
            ),
            CmdEntry(
                name="add_host_note",
                description="Add an internal note or flag to a host in your agency.",
                args="<host_id> <note>",
                level=HelpLevel.AGENCY_OWNER,
                note="Notes are private — only visible to the agency owner. Max 1 000 characters.",
            ),
            CmdEntry(
                name="view_host_notes",
                description="View all notes recorded against a host in your agency.",
                args="<host_id>",
                level=HelpLevel.AGENCY_OWNER,
            ),
            CmdEntry(
                name="approve_agency",
                description="Approve a pending agency registration request.",
                args="<name> <user_id>",
                level=HelpLevel.ADMIN,
                note="Only the bot admin (BOT_OWNER_IDS) can run this command.",
            ),
        ],
    ),

    Category(
        key="analytics",
        emoji="📊",
        title="Analytics",
        subtitle="Performance dashboards and weekly summary reports for agency owners and admins.",
        min_level=HelpLevel.AGENCY_OWNER,
        commands=[
            CmdEntry(
                name="analytics dashboard",
                description="View your agency's analytics dashboard — deal volume, outcomes, and host rankings.",
                args="[days:7.0]",
                level=HelpLevel.AGENCY_OWNER,
                note="Shows up to 90 days of history. Defaults to the last 7 days.",
            ),
            CmdEntry(
                name="analytics host_breakdown",
                description="Detailed per-host performance table for your agency.",
                args="[days:30.0]",
                level=HelpLevel.AGENCY_OWNER,
            ),
            CmdEntry(
                name="analytics guild_dashboard",
                description="Server-wide deal analytics — all guilds, all agencies.",
                args="[days:7.0]",
                level=HelpLevel.ADMIN,
            ),
            CmdEntry(
                name="analytics send_summaries",
                description="Immediately dispatch weekly analytics DMs to all agency owners.",
                level=HelpLevel.BOT_OWNER,
                note="Normally dispatched automatically every Sunday at midnight UTC.",
            ),
            CmdEntry(
                name="agency_rep stats",
                description="View reputation statistics (avg, min, max, disputes) for your agency.",
                level=HelpLevel.AGENCY_OWNER,
            ),
        ],
    ),

    Category(
        key="governance",
        emoji="🔒",
        title="Governance",
        subtitle="Probation mode, escrow confidence indicators, and per-server rule configuration.",
        min_level=HelpLevel.EVERYONE,
        commands=[
            CmdEntry(
                name="governance config view",
                description="View current probation thresholds, escrow settings, and analytics config.",
            ),
            CmdEntry(
                name="governance probation view",
                description="View a user's probation status and recent probation history.",
                args="[@user]",
                note="Defaults to yourself. Only admins can view others.",
            ),
            CmdEntry(
                name="governance probation approve_deal",
                description="Approve a probation-restricted host in your agency to create their next deal.",
                args="<@user>",
                level=HelpLevel.AGENCY_OWNER,
                note="Approval resets automatically after the next deal is created.",
            ),
            CmdEntry(
                name="governance probation list",
                description="List all hosts currently on probation across the server.",
                level=HelpLevel.ADMIN,
            ),
            CmdEntry(
                name="governance probation set",
                description="Manually place a user on probation.",
                args="<@user> [reason]",
                level=HelpLevel.ADMIN,
            ),
            CmdEntry(
                name="governance probation lift",
                description="Manually lift probation from a user.",
                args="<@user> [reason]",
                level=HelpLevel.ADMIN,
            ),
            CmdEntry(
                name="governance config set",
                description="Configure governance thresholds (probation cutoff, escrow scores, deal limits).",
                args="[probation_threshold] [probation_max_open] [high_value_threshold] [required_confidence] [escrow_mediator] [analytics_weekly_dm]",
                level=HelpLevel.ADMIN,
                note="All parameters are optional; only supplied values are updated.",
            ),
            CmdEntry(
                name="governance escrow view",
                description="Inspect the escrow confidence flag recorded for a specific deal.",
                args="<deal_id:int>",
                level=HelpLevel.ADMIN,
                note="deal_id is the internal integer ID, visible in the DB or audit log.",
            ),
        ],
    ),

    Category(
        key="rate_limits",
        emoji="⚙️",
        title="Rate Limits",
        subtitle="Per-server deal creation limits, dispute cooldowns, and reminder frequency.",
        min_level=HelpLevel.EVERYONE,
        commands=[
            CmdEntry(
                name="rate_limits view",
                description="View the current server rate limits (open deal cap, daily volume, cooldowns).",
            ),
            CmdEntry(
                name="rate_limits set",
                description="Configure server-wide deal rate limits.",
                args="[max_open:10.0] [max_volume:50000.0] [cooldown_hours:24.0] [reminder_mins:30.0] [weekly_summary:true] [mediator_role]",
                level=HelpLevel.ADMIN,
                note="All parameters are optional floats/bools. Stored per guild.",
            ),
        ],
    ),

    Category(
        key="admin",
        emoji="🛡️",
        title="Admin",
        subtitle="Dispute resolution, data exports, backups, and usage statistics.",
        min_level=HelpLevel.ADMIN,
        commands=[
            CmdEntry(
                name="admin resolve",
                description="Resolve a disputed deal — set it to active, completed, or defaulted.",
                args="<deal_id> <resolution> [note]",
                level=HelpLevel.ADMIN,
                note="Resolution triggers the appropriate reputation hooks for both parties.",
            ),
            CmdEntry(
                name="admin export",
                description="Export all deal data for your account as a CSV file.",
                level=HelpLevel.ADMIN,
            ),
            CmdEntry(
                name="admin backup",
                description="Trigger an immediate database backup (optionally encrypted).",
                level=HelpLevel.ADMIN,
                note="Backups are also run automatically at 02:00 UTC daily.",
            ),
            CmdEntry(
                name="admin stats",
                description="View command usage statistics for this server.",
                args="[days:30]",
                level=HelpLevel.ADMIN,
            ),
            CmdEntry(
                name="admin_rep adjust_reputation",
                description="Manually adjust a host's reputation score by ±500 points.",
                args="<@user> <amount:int> <reason>",
                level=HelpLevel.ADMIN,
                note="All manual adjustments are logged to the audit trail.",
            ),
            CmdEntry(
                name="admin_rep scam_confirm",
                description="Confirm a user as a scammer: −100 reputation and +1 strike.",
                args="<@user> <deal_id:int>",
                level=HelpLevel.ADMIN,
                note="Automatically evaluates probation after applying the penalty.",
            ),
        ],
    ),

    Category(
        key="general",
        emoji="ℹ️",
        title="General",
        subtitle="About DealBot, personal data export, and this help menu.",
        min_level=HelpLevel.EVERYONE,
        commands=[
            CmdEntry(
                name="about",
                description="What is DealBot? Disclaimer, data policy, and scope.",
            ),
            CmdEntry(
                name="export_data",
                description="Download all your personal deal data as a CSV (GDPR-style export).",
                note="Includes all deals and notes associated with your Discord account.",
            ),
            CmdEntry(
                name="help",
                description="Open this interactive help menu in your DMs.",
                args="[category]",
            ),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Embed colour palette (consistent with embeds.py)
# ---------------------------------------------------------------------------

_COLOUR_MAP: dict[str, int] = {
    "deals":       0x5865F2,   # blurple
    "profile":     0x57F287,   # green
    "agency":      0x3498DB,   # blue
    "analytics":   0xFFD700,   # gold
    "governance":  0xED4245,   # red
    "rate_limits": 0x95A5A6,   # grey
    "admin":       0xE67E22,   # orange
    "general":     0x99AAB5,   # light grey
}

_BADGE: dict[HelpLevel, str] = {
    HelpLevel.EVERYONE:      "",
    HelpLevel.AGENCY_MEMBER: "🏢 ",
    HelpLevel.AGENCY_OWNER:  "👑 ",
    HelpLevel.ADMIN:         "🛡️ ",
    HelpLevel.BOT_OWNER:     "🔑 ",
}

_LEVEL_LABEL: dict[HelpLevel, str] = {
    HelpLevel.EVERYONE:      "All users",
    HelpLevel.AGENCY_MEMBER: "Agency members",
    HelpLevel.AGENCY_OWNER:  "Agency owners",
    HelpLevel.ADMIN:         "Admins / Mediators",
    HelpLevel.BOT_OWNER:     "Bot owners",
}

# ---------------------------------------------------------------------------
# Permission resolution (DB-backed, called once per /help invocation)
# ---------------------------------------------------------------------------

async def resolve_user_level(
    interaction: discord.Interaction,
    db,
) -> HelpLevel:
    """
    Return the highest HelpLevel the interacting user qualifies for.
    Order of checks: BOT_OWNER → ADMIN → AGENCY_OWNER → AGENCY_MEMBER → EVERYONE.
    """
    uid = interaction.user.id

    if uid in _OWNER_IDS:
        return HelpLevel.BOT_OWNER

    member = interaction.user
    if isinstance(member, discord.Member):
        if member.guild_permissions.administrator or any(
            r.name == _MEDIATOR_ROLE for r in member.roles
        ):
            return HelpLevel.ADMIN

    # Check agency ownership via DB
    try:
        async with db.execute(
            "SELECT id FROM agencies WHERE owner_discord_id=? AND status='approved'",
            (uid,),
        ) as cur:
            if await cur.fetchone():
                return HelpLevel.AGENCY_OWNER

        # Check active agency membership
        async with db.execute(
            "SELECT id FROM hosts WHERE discord_id=? AND join_status='active'",
            (uid,),
        ) as cur:
            if await cur.fetchone():
                return HelpLevel.AGENCY_MEMBER
    except Exception as exc:
        log.warning(f"Help: DB check failed for {uid}: {exc}")

    return HelpLevel.EVERYONE

# ---------------------------------------------------------------------------
# Dynamic command discovery (auto-includes future commands)
# ---------------------------------------------------------------------------

def _collect_known_names() -> set[str]:
    """Return the set of all command names already in the curated catalog."""
    known: set[str] = set()
    for cat in CATEGORIES:
        for cmd in cat.commands:
            known.add(cmd.name.lower())
    return known


def _walk_tree(tree: app_commands.CommandTree) -> list[tuple[str, str]]:
    """
    Recursively walk the app command tree and return (full_name, description)
    for every leaf command.
    """
    results: list[tuple[str, str]] = []

    def _recurse(cmd, prefix: str = "") -> None:
        full = f"{prefix}{cmd.name}" if prefix else cmd.name
        if isinstance(cmd, app_commands.Group):
            for child in cmd.commands:
                _recurse(child, f"{full} ")
        else:
            results.append((full, getattr(cmd, "description", "") or ""))

    for cmd in tree.get_commands():
        _recurse(cmd)
    return results


def build_extra_category(bot: commands.Bot) -> Optional[Category]:
    """
    Discover any commands in bot.tree that are NOT in the curated catalog
    and return them as an 'Other / New' category.  Returns None if empty.
    This ensures future cogs appear automatically.
    """
    known = _collect_known_names()
    extra: list[CmdEntry] = []
    for name, desc in _walk_tree(bot.tree):
        if name.lower() not in known:
            extra.append(CmdEntry(name=name, description=desc or "No description provided."))
    if not extra:
        return None
    return Category(
        key="other",
        emoji="🔧",
        title="Other / New",
        subtitle="Recently added commands that are automatically included from bot.tree.",
        commands=extra,
        min_level=HelpLevel.EVERYONE,
    )

# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

_FOOTER_TEXT = "Directive DealBot  •  /help [category] to jump to a section"
_MAX_FIELD_VALUE = 1000   # Discord embed field value limit

def _format_cmd_block(entry: CmdEntry, user_level: HelpLevel) -> str:
    """
    Render one command as a compact block:

      `/<name>` [args]
      ↳ Description. [🛡️ Admin only]
      _Note: prerequisite text._
    """
    lines: list[str] = []

    cmd_line = f"`/{entry.name}`"
    if entry.args:
        cmd_line += f"  {entry.args}"
    if entry.level > HelpLevel.EVERYONE and entry.level > user_level:
        # Command is visible but user can't run it — dim it
        cmd_line += f"  *(requires {_LEVEL_LABEL[entry.level]})*"
    elif entry.level > HelpLevel.EVERYONE:
        badge = _BADGE.get(entry.level, "")
        cmd_line += f"  {badge}"

    lines.append(cmd_line)
    lines.append(f"↳ {entry.description}")
    if entry.note:
        lines.append(f"_ℹ️ {entry.note}_")
    return "\n".join(lines)


def build_category_embed(
    cat: Category,
    user_level: HelpLevel,
    bot_user: discord.ClientUser,
    total_cats: int,
    cat_index: int,
) -> list[discord.Embed]:
    """
    Build one or more Discord embeds for a category.  Splits across multiple
    embeds if the content would exceed Discord's 6000-character embed limit.
    """
    colour = _COLOUR_MAP.get(cat.key, 0x5865F2)
    title = f"{cat.emoji} {cat.title}"
    desc = cat.subtitle

    # Filter commands the user should see (hide BOT_OWNER-only from non-owners, etc.)
    # We always show all commands but mark restricted ones — this is more helpful than hiding.
    # Exception: we fully hide BOT_OWNER commands from non-bot-owners for security hygiene.
    visible_cmds = [
        c for c in cat.commands
        if not (c.level == HelpLevel.BOT_OWNER and user_level < HelpLevel.BOT_OWNER)
    ]

    # Partition into field-sized chunks
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for entry in visible_cmds:
        block = _format_cmd_block(entry, user_level)
        if current_len + len(block) + 2 > _MAX_FIELD_VALUE and current:
            chunks.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))

    embeds: list[discord.Embed] = []
    for i, chunk in enumerate(chunks):
        is_first = i == 0
        embed = discord.Embed(
            title=title if is_first else f"{title} (cont.)",
            description=desc if is_first else None,
            colour=colour,
        )
        embed.add_field(name="Commands", value=chunk, inline=False)
        if i == len(chunks) - 1:
            embed.set_footer(
                text=f"{_FOOTER_TEXT}  •  Category {cat_index + 1}/{total_cats}",
                icon_url=bot_user.display_avatar.url if bot_user else None,
            )
        embeds.append(embed)
    return embeds


def build_overview_embed(
    filtered_cats: list[Category],
    user_level: HelpLevel,
    bot_user: discord.ClientUser,
) -> discord.Embed:
    """
    Landing embed shown when /help is first invoked — a table of contents
    with a one-liner per category.
    """
    embed = discord.Embed(
        title="📖 Directive DealBot — Help",
        description=(
            "Welcome! Use the **dropdown below** to browse command categories.\n\n"
            "DealBot is a non-custodial deal ledger for livestream hosts. "
            "It records agreements, tracks completion, and scores reputation — "
            "it does **not** hold or move any funds.\n"
        ),
        colour=0x5865F2,
    )

    toc_lines = [
        f"{cat.emoji} **{cat.title}** — {cat.subtitle}"
        for cat in filtered_cats
    ]
    embed.add_field(
        name="📂 Categories",
        value="\n".join(toc_lines) or "_No categories available._",
        inline=False,
    )

    legend_lines = [
        f"{badge}= {label}"
        for level, badge in _BADGE.items()
        if badge and level <= user_level
    ]
    if legend_lines:
        embed.add_field(
            name="🔑 Permission Legend",
            value="  ".join(legend_lines),
            inline=False,
        )

    embed.add_field(
        name="💡 Tips",
        value=(
            "• Use `/deal start` to create your first deal via the DM wizard.\n"
            "• Use `/profile view` to check your reputation score and tier.\n"
            "• Use `/export_data` to download all your records at any time.\n"
            "• Commands marked *(requires …)* are visible but need a higher permission."
        ),
        inline=False,
    )

    embed.set_footer(
        text=_FOOTER_TEXT,
        icon_url=bot_user.display_avatar.url if bot_user else None,
    )
    if bot_user:
        embed.set_thumbnail(url=bot_user.display_avatar.url)
    return embed

# ---------------------------------------------------------------------------
# Interactive view — dropdown category selector
# ---------------------------------------------------------------------------

class CategorySelect(discord.ui.Select):
    """Dropdown that lets users jump between help categories."""

    def __init__(self, filtered_cats: list[Category]) -> None:
        options = [
            discord.SelectOption(
                label=cat.title,
                value=cat.key,
                emoji=cat.emoji,
                description=cat.subtitle[:100],
            )
            for cat in filtered_cats
        ]
        super().__init__(
            placeholder="Choose a category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpView = self.view  # type: ignore[assignment]
        selected_key = self.values[0]
        await view.show_category(interaction, selected_key)


class HelpView(discord.ui.View):
    """
    Persistent view attached to the overview DM.
    Holds the filtered category list and user level so callbacks can
    re-render without re-hitting the DB.
    """

    def __init__(
        self,
        filtered_cats: list[Category],
        user_level: HelpLevel,
        bot_user: discord.ClientUser,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.filtered_cats = filtered_cats
        self.cat_map: dict[str, Category] = {c.key: c for c in filtered_cats}
        self.user_level = user_level
        self.bot_user = bot_user
        self.message: Optional[discord.Message] = None

        self.add_item(CategorySelect(filtered_cats))

    async def show_category(
        self, interaction: discord.Interaction, key: str
    ) -> None:
        cat = self.cat_map.get(key)
        if not cat:
            await interaction.response.send_message("Category not found.", ephemeral=True)
            return

        idx = next((i for i, c in enumerate(self.filtered_cats) if c.key == key), 0)
        embeds = build_category_embed(
            cat, self.user_level, self.bot_user,
            total_cats=len(self.filtered_cats),
            cat_index=idx,
        )

        # Update the original overview message with the first embed of the selection
        await interaction.response.edit_message(embed=embeds[0], view=self)

        # If the category spilled across multiple embeds, send the overflow as follow-ups
        if len(embeds) > 1:
            for overflow_embed in embeds[1:]:
                await interaction.followup.send(embed=overflow_embed)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                for child in self.children:
                    child.disabled = True  # type: ignore[union-attr]
                await self.message.edit(view=self)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# HelpCog
# ---------------------------------------------------------------------------

class HelpCog(commands.Cog, name="Help"):
    """Registers /help and delivers the interactive DM help menu."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def _cat_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=f"{cat.emoji} {cat.title}", value=cat.key)
            for cat in CATEGORIES
            if current.lower() in cat.key.lower() or current.lower() in cat.title.lower()
        ][:25]

    # ------------------------------------------------------------------
    # /help
    # ------------------------------------------------------------------

    @app_commands.command(
        name="help",
        description="Open the interactive DealBot help menu in your DMs.",
    )
    @app_commands.describe(
        category="Jump directly to a specific category (optional).",
    )
    @app_commands.autocomplete(category=_cat_autocomplete)
    async def help_command(
        self,
        interaction: discord.Interaction,
        category: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        user_level = await resolve_user_level(interaction, db)

        # Build filtered category list + dynamic extras
        extra_cat = build_extra_category(self.bot)
        all_cats = CATEGORIES + ([extra_cat] if extra_cat else [])
        filtered_cats = [c for c in all_cats if user_level >= c.min_level]

        bot_user = self.bot.user

        # ------ Attempt to open a DM channel ------
        try:
            dm = await interaction.user.create_dm()
        except discord.Forbidden:
            await interaction.followup.send(
                embed=_dm_blocked_embed(),
                ephemeral=True,
            )
            await log_command(db, interaction.guild_id, interaction.user.id, "help", False, "dm_blocked")
            return

        # ------ Build and send the overview ------
        overview_embed = build_overview_embed(filtered_cats, user_level, bot_user)
        view = HelpView(filtered_cats, user_level, bot_user)

        try:
            msg = await dm.send(embed=overview_embed, view=view)
            view.message = msg
        except discord.Forbidden:
            await interaction.followup.send(
                embed=_dm_blocked_embed(),
                ephemeral=True,
            )
            await log_command(db, interaction.guild_id, interaction.user.id, "help", False, "dm_blocked")
            return

        # ------ If user asked for a specific category, follow up with it ------
        if category:
            cat = next((c for c in filtered_cats if c.key == category), None)
            if cat:
                idx = filtered_cats.index(cat)
                embeds = build_category_embed(
                    cat, user_level, bot_user,
                    total_cats=len(filtered_cats),
                    cat_index=idx,
                )
                for embed in embeds:
                    await dm.send(embed=embed)
            else:
                await dm.send(
                    f"⚠️ Category `{category}` not found or not available at your permission level."
                )

        # ------ Confirm in-channel ------
        await interaction.followup.send(
            embed=_sent_confirm_embed(interaction.user),
            ephemeral=True,
        )
        await log_command(
            db, interaction.guild_id, interaction.user.id, "help", True,
            f"level={user_level.name} cat={category or 'overview'}"
        )


# ---------------------------------------------------------------------------
# Small utility embeds
# ---------------------------------------------------------------------------

def _dm_blocked_embed() -> discord.Embed:
    return discord.Embed(
        title="📪 DMs Required",
        description=(
            "DealBot's help menu is delivered via **Direct Message** to keep channels tidy.\n\n"
            "Please enable DMs from server members:\n"
            "**User Settings → Privacy & Safety → Allow direct messages from server members**\n\n"
            "Then run `/help` again."
        ),
        colour=0xED4245,
    )


def _sent_confirm_embed(user: discord.User | discord.Member) -> discord.Embed:
    return discord.Embed(
        description=(
            f"📬 Help menu sent to your DMs, {user.mention}!\n"
            "Use the dropdown to navigate between categories."
        ),
        colour=0x57F287,
    )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
    log.info("HelpCog loaded.")
