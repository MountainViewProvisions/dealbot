from __future__ import annotations

from typing import Optional, Sequence

import discord

from bot.models.dataclasses import GlobalReputation, ServerLimits

STATUS_COLOR: dict[str, discord.Color] = {
    "pending_confirmation": discord.Color.yellow(),
    "active":               discord.Color.green(),
    "pending_completion":   discord.Color.blue(),
    "completed":            discord.Color.dark_green(),
    "disputed":             discord.Color.red(),
    "overdue":              discord.Color.orange(),
    "defaulted":            discord.Color.dark_red(),
    "cancelled":            discord.Color.greyple(),
}

STATUS_EMOJI: dict[str, str] = {
    "pending_confirmation": "🟡",
    "active":               "🟢",
    "pending_completion":   "🔵",
    "completed":            "✅",
    "disputed":             "🔴",
    "overdue":              "🟠",
    "defaulted":            "⛔",
    "cancelled":            "⚫",
}

def deal_embed(deal_row, notes=None, audit=None) -> discord.Embed:
    status = deal_row["status"]
    embed = discord.Embed(
        title=f"Deal `{deal_row['deal_uuid']}` — {deal_row['network_name']}",
        color=STATUS_COLOR.get(status, discord.Color.default()),
    )

    embed.add_field(
        name="Parties",
        value=(
            f"**A:** `{deal_row['party_a_username']}` "
            f"({deal_row['party_a_discord_name']})\n"
            f"**B:** `{deal_row['party_b_username']}` "
            f"({deal_row['party_b_discord_name']})"
        ),
        inline=False,
    )
    embed.add_field(name="Type", value=deal_row["deal_type"].title(), inline=True)
    embed.add_field(
        name="Amount",
        value=f"{deal_row['amount']:,.2f} {deal_row['currency']}",
        inline=True,
    )
    embed.add_field(name="Due", value=deal_row["due_date"][:10], inline=True)
    embed.add_field(
        name="Status",
        value=f"{STATUS_EMOJI.get(status, '')} {status.replace('_', ' ').title()}",
        inline=True,
    )
    embed.add_field(
        name="Deal Confirmations",
        value=(
            f"Party A: {'✅' if deal_row['party_a_confirmed'] else '⬜'}\n"
            f"Party B: {'✅' if deal_row['party_b_confirmed'] else '⬜'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Completion Confirms",
        value=(
            f"Party A: {'✅' if deal_row['completion_confirmed_by_a'] else '⬜'}\n"
            f"Party B: {'✅' if deal_row['completion_confirmed_by_b'] else '⬜'}"
        ),
        inline=True,
    )

    if deal_row["disputed_reason"]:
        embed.add_field(
            name="⚠️ Dispute Reason",
            value=deal_row["disputed_reason"][:512],
            inline=False,
        )

    if notes:
        lines = [
            f"`{n['created_at'][:16]}` **{n['author_username']}**: {n['note_text']}"
            for n in notes
        ]
        embed.add_field(
            name=f"📝 Notes ({len(notes)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )

    if audit:
        lines = [
            f"`{a['timestamp'][:16]}` **{a['action_type'].upper()}**: "
            f"`{a['previous_status']}` → `{a['new_status']}`"
            + (f" by {a['actor_username']}" if a.get("actor_username") else " [system]")
            for a in audit[-10:]
        ]
        embed.add_field(
            name=f"🔍 Audit Log (last {min(len(audit), 10)})",
            value="\n".join(lines)[:1024] or "Empty",
            inline=False,
        )

    embed.set_footer(text=f"Created: {deal_row['created_at'][:10]}")
    return embed

def deal_list_embed(
    deals: Sequence,
    title: str = "Your Deals",
    page: int = 1,
    total_pages: int = 1,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple())
    if not deals:
        embed.description = "_No deals found._"
        return embed
    lines = []
    for d in deals:
        emoji = STATUS_EMOJI.get(d["status"], "•")
        lines.append(
            f"{emoji} `{d['deal_uuid']}` [{d['network_name']}] "
            f"**{d['deal_type']}** {d['amount']:,.0f} {d['currency']} "
            f"— due {d['due_date'][:10]}"
        )
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Page {page}/{total_pages}")
    return embed

def reputation_embed(
    rep: GlobalReputation,
    network_filter: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📊 Reputation — {rep.discord_name}",
        color=discord.Color.blurple(),
    )
    if network_filter:
        embed.description = f"Filtered to network: **{network_filter}**"

    score = rep.weighted_score
    if score >= 750:
        tier, icon = "Excellent", "🌟"
    elif score >= 600:
        tier, icon = "Good", "✅"
    elif score >= 400:
        tier, icon = "Fair", "🟡"
    elif score >= 200:
        tier, icon = "Poor", "🟠"
    else:
        tier, icon = "Very Poor", "🔴"

    embed.add_field(
        name=f"{icon} Weighted Score",
        value=f"**{score} / 1000** — {tier}",
        inline=False,
    )
    embed.add_field(
        name="Global Stats",
        value=(
            f"✅ Completed: **{rep.total_completed}**\n"
            f"⛔ Defaulted: **{rep.total_defaulted}**\n"
            f"🟠 Overdue: **{rep.total_overdue}**\n"
            f"⚠️ Disputed: **{rep.total_disputed}**\n"
            f"📊 Total Tracked: **{rep.total_deals}**\n"
            f"📈 Success Rate: **{rep.global_success_rate}%**\n"
            f"💰 Total Volume: **{rep.total_volume:,.0f}**"
        ),
        inline=False,
    )
    for net in rep.by_network:
        embed.add_field(
            name=f"📡 {net.network_name}  (score: {net.weighted_score})",
            value=(
                f"✅ {net.completed} | ⛔ {net.defaulted} | "
                f"🟠 {net.overdue} | ⚠️ {net.disputed}\n"
                f"Rate: {net.success_rate}% | Vol: {net.total_volume:,.0f}"
            ),
            inline=True,
        )
    if not rep.by_network:
        embed.add_field(name="No Data", value="No tracked deals yet.", inline=False)

    embed.set_footer(
        text=(
            "Score = 500 + Σ(recency×volume×outcome). "
            "Disputed deals frozen. "
            "'Defaulted' = recorded status only."
        )
    )
    return embed

def rate_limits_embed(limits: ServerLimits) -> discord.Embed:
    embed = discord.Embed(
        title="⚙️ Server Rate Limits",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Max Open Deals / User", value=str(limits.max_open_deals), inline=True)
    embed.add_field(
        name="Max Daily Volume / User",
        value=f"{limits.max_daily_volume:,.0f}",
        inline=True,
    )
    embed.add_field(
        name="Dispute Cooldown",
        value=f"{limits.dispute_cooldown_hours}h",
        inline=True,
    )
    embed.add_field(
        name="Reminder Frequency",
        value=f"Every {limits.reminder_freq_minutes} min",
        inline=True,
    )
    embed.add_field(
        name="Weekly Summary",
        value="Enabled" if limits.weekly_summary_enabled else "Disabled",
        inline=True,
    )
    embed.add_field(
        name="Mediator Role",
        value=f"`{limits.mediator_role_name}`",
        inline=True,
    )
    return embed

def about_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📖 About DealBot",
        color=discord.Color.blurple(),
        description=(
            "DealBot is a **voluntary, non-custodial deal ledger** for livestream "
            "hosts operating across multiple platforms.\n\n"

            "**What DealBot does:**\n"
            "• Records voluntary deal agreements between Discord users\n"
            "• Tracks deal confirmation, completion, and dispute states\n"
            "• Calculates time-and-volume-weighted reputation scores\n"
            "• Sends DM reminders for upcoming and overdue deals\n"
            "• Provides a full audit log for every state change\n\n"

            "**What DealBot does NOT do:**\n"
            "• Handle, move, hold, or guarantee any money or digital assets\n"
            "• Enforce deals legally, financially, or operationally\n"
            "• Label users as fraudulent — only records objective statuses\n"
            "• Affiliate with or represent BigoLive, TikTok, Tango, or any platform\n\n"

            "**Cross-network:**\n"
            "Reputation is tracked per-network. A default on TikTok does not "
            "affect your BigoLive score. Use `/rep @user` for the full breakdown.\n\n"

            "**Data & Privacy:**\n"
            "All data is stored in a local SQLite database. "
            "Use `/export_data` at any time to receive a CSV of your personal records.\n\n"

            "_Use this bot responsibly and in good faith. "
            "Disputes should be resolved through mutual agreement or mediation._"
        ),
    )
    embed.set_footer(text="DealBot — Open ledger. No money movement. No platform affiliation.")
    return embed

def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=discord.Color.red())

def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=message, color=discord.Color.green())

def info_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=discord.Color.blurple())
