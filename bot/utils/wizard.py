from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord

from bot.database import get_db
from bot.database.queries import get_all_networks
from bot.models.enums import DealType
from bot.services.deal_service import create_deal
from bot.utils.validators import (
    validate_amount, validate_currency, validate_due_date
)

log = logging.getLogger(__name__)

_TIMEOUT = 120


class DealWizard:

    def __init__(
        self,
        bot: discord.Client,
        initiator: discord.User,
        counterparty: discord.User,
        guild_id: int,
    ):
        self.bot = bot
        self.initiator = initiator
        self.counterparty = counterparty
        self.guild_id = guild_id

    def _check(self, msg: discord.Message) -> bool:
        return (
            msg.author.id == self.initiator.id
            and isinstance(msg.channel, discord.DMChannel)
        )

    async def _ask(self, dm: discord.DMChannel, prompt: str) -> str | None:
        await dm.send(prompt)
        try:
            reply = await self.bot.wait_for("message", check=self._check, timeout=_TIMEOUT)
            text = reply.content.strip()
            if text.lower() == "cancel":
                await dm.send("❌ Wizard cancelled. Use `/deal start` to try again.")
                return None
            return text
        except asyncio.TimeoutError:
            await dm.send(
                f"⏰ No response in {_TIMEOUT}s. Wizard cancelled. "
                "Use `/deal start` to try again."
            )
            return None

    async def run(self) -> tuple[bool, str]:
        try:
            dm = await self.initiator.create_dm()
        except discord.Forbidden:
            return False, "Please enable DMs from server members so I can run the wizard."

        db = await get_db()
        networks = await get_all_networks(db)

        await dm.send(
            f"👋 **Deal Creation Wizard**\n"
            f"Creating a deal with **{self.counterparty.display_name}**.\n"
            f"Reply to each question below. Type `cancel` at any step to abort.\n"
            f"⏱️ {_TIMEOUT}s timeout per step.\n"
        )

        net_list = "\n".join(f"  `{i+1}.` {n['name']}" for i, n in enumerate(networks))
        raw = await self._ask(dm, f"**[1/6] Network**\n{net_list}\n\nEnter number or name:")
        if raw is None:
            return False, "cancelled"
        network_name: str | None = None
        if raw.isdigit() and 1 <= int(raw) <= len(networks):
            network_name = networks[int(raw) - 1]["name"]
        else:
            for n in networks:
                if n["name"].lower() == raw.lower():
                    network_name = n["name"]
        if not network_name:
            await dm.send("❌ Invalid network. Wizard cancelled.")
            return False, "Invalid network."

        raw = await self._ask(dm, f"**[2/6] Your username on {network_name}:**")
        if raw is None:
            return False, "cancelled"
        initiator_username = raw[:64]

        raw = await self._ask(
            dm,
            f"**[3/6] {self.counterparty.display_name}'s username on {network_name}:**",
        )
        if raw is None:
            return False, "cancelled"
        counterparty_username = raw[:64]

        types = [t.value for t in DealType]
        type_list = "\n".join(f"  `{i+1}.` {t}" for i, t in enumerate(types))
        raw = await self._ask(dm, f"**[4/6] Deal Type**\n{type_list}\n\nEnter number or name:")
        if raw is None:
            return False, "cancelled"
        deal_type: str | None = None
        if raw.isdigit() and 1 <= int(raw) <= len(types):
            deal_type = types[int(raw) - 1]
        else:
            for t in types:
                if t.lower() == raw.lower():
                    deal_type = t
        if not deal_type:
            await dm.send("❌ Invalid deal type. Wizard cancelled.")
            return False, "Invalid deal type."

        raw = await self._ask(
            dm,
            "**[5/6] Amount & Currency**\n"
            "Format: `<amount> <currency>` — e.g. `500 USD` or `1000 diamonds`",
        )
        if raw is None:
            return False, "cancelled"
        parts = raw.split(maxsplit=1)
        ok, err, amount = validate_amount(parts[0] if parts else "")
        if not ok:
            await dm.send(f"❌ {err} Wizard cancelled.")
            return False, err
        currency_raw = parts[1] if len(parts) > 1 else "USD"
        ok2, err2, currency = validate_currency(currency_raw)
        if not ok2:
            await dm.send(f"❌ {err2} Wizard cancelled.")
            return False, err2

        raw = await self._ask(dm, "**[6/6] Due Date** (YYYY-MM-DD, e.g. 2025-12-31):")
        if raw is None:
            return False, "cancelled"
        ok3, err3, due_date = validate_due_date(raw)
        if not ok3:
            await dm.send(f"❌ {err3} Wizard cancelled.")
            return False, err3

        if due_date is None:
            await dm.send("❌ Invalid due date. Wizard cancelled.")
            return False, "Invalid due date."

        summary = (
            f"**📋 Deal Summary — Confirm?**\n"
            f"Network: **{network_name}**\n"
            f"You (`{initiator_username}`) ↔ "
            f"**{self.counterparty.display_name}** (`{counterparty_username}`)\n"
            f"Type: **{deal_type}** | Amount: **{amount:,.2f} {currency}**\n"
            f"Due: **{due_date.strftime('%Y-%m-%d')}**\n\n"
            "Type `confirm` to create, or anything else to cancel:"
        )
        raw = await self._ask(dm, summary)
        if raw is None or raw.lower() != "confirm":
            await dm.send("❌ Deal creation cancelled.")
            return False, "User cancelled."

        try:
            deal_uuid = await create_deal(
                db=db,
                initiator_discord_id=self.initiator.id,
                initiator_discord_name=str(self.initiator),
                counterparty_discord_id=self.counterparty.id,
                counterparty_discord_name=str(self.counterparty),
                network_name=network_name,
                initiator_username=initiator_username,
                counterparty_username=counterparty_username,
                deal_type=deal_type,
                amount=amount,
                currency=currency,
                due_date=due_date,
                guild_id=self.guild_id,
            )
        except PermissionError as exc:
            await dm.send(f"⛔ **Rate limit:** {exc}")
            return False, str(exc)
        except Exception as exc:
            log.exception(f"Wizard create_deal failed: {exc}")
            await dm.send("❌ Unexpected error creating the deal. Please try again.")
            return False, str(exc)

        await dm.send(
            f"✅ **Deal `{deal_uuid}` created!**\n"
            f"Both parties must confirm:\n"
            f"`/deal confirm {deal_uuid}`"
        )

        try:
            cp_dm = await self.counterparty.create_dm()
            await cp_dm.send(
                f"📬 **New Deal Request from {self.initiator.display_name}**\n"
                f"Deal ID: `{deal_uuid}` | Network: **{network_name}**\n"
                f"Type: **{deal_type}** | Amount: **{amount:,.2f} {currency}**"
                f" | Due: **{due_date.strftime('%Y-%m-%d')}**\n\n"
                f"Use `/deal confirm {deal_uuid}` to accept."
            )
        except discord.Forbidden:
            await dm.send(
                f"⚠️ Could not DM **{self.counterparty.display_name}** "
                "(DMs may be disabled). Please notify them manually."
            )
        return True, deal_uuid
