from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional

def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)

    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "dealbot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

class DiscordChannelHandler(logging.Handler):

    def __init__(self, bot, channel_id: int):
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.channel_id = channel_id
        self.setFormatter(logging.Formatter("%(levelname)s | %(name)s\n%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        import asyncio
        try:
            msg = self.format(record)
            truncated = msg[:1900]
            asyncio.create_task(self._send(truncated))
        except Exception:
            self.handleError(record)

    async def _send(self, message: str) -> None:
        try:
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                await channel.send(f"```\n{message}\n```")
        except Exception:
            pass
