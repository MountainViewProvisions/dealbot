from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from bot.database import close_db, init_db
from bot.tasks.reminder_loop import backup_task, reminder_task, weekly_summary_task
from bot.utils.logger import DiscordChannelHandler, setup_logging

load_dotenv()
setup_logging()
log = logging.getLogger("dealbot")

EXTENSIONS = [
    "bot.commands.admin",
    "bot.commands.agency",
    "bot.commands.analytics",
    "bot.commands.deal",
    "bot.commands.governance",
    "bot.commands.help",
    "bot.commands.misc",
    "bot.commands.rate_limits",
    "bot.commands.reputation",
]

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

    log_channel_id = os.getenv("LOG_CHANNEL_ID", "").strip()
    if log_channel_id.isdigit():
        handler = DiscordChannelHandler(bot, int(log_channel_id))
        logging.getLogger().addHandler(handler)
        log.info(f"Error logs mirrored to channel {log_channel_id}")

    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info(f"Synced {len(synced)} command(s) to guild {guild_id} (instant)")
    else:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} global command(s) (up to 1h propagation)")


@bot.event
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    log.exception(f"Slash command error [{interaction.command}]: {error}")
    try:
        msg = "An unexpected error occurred. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


async def main():
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise EnvironmentError("DISCORD_TOKEN not set in .env")

    await init_db()
    log.info("Database initialised.")

    for ext in EXTENSIONS:
        await bot.load_extension(ext)
        log.info(f"  ✓ {ext}")

    # asyncio.get_running_loop() is required inside an async context (get_event_loop is deprecated).
    loop = asyncio.get_running_loop()
    loop.create_task(reminder_task(bot), name="reminder_task")
    loop.create_task(weekly_summary_task(bot), name="weekly_summary_task")
    loop.create_task(backup_task(bot), name="backup_task")
    log.info("Background tasks registered.")

    try:
        await bot.start(token)
    finally:
        await close_db()
        log.info("DealBot v1 shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
