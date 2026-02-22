from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from bot.database import queries

log = logging.getLogger(__name__)

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "./backups"))
ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", "")


def _get_fernet():
    """Return a Fernet instance derived from BACKUP_ENCRYPTION_KEY, or None if not configured."""
    if not ENCRYPTION_KEY:
        return None
    try:
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        raw = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(raw))
    except ImportError:
        log.warning("cryptography not installed; backups will not be encrypted.")
        return None


async def run_daily_backup(db: aiosqlite.Connection) -> Optional[Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"dealbot_{ts}.db"
    try:
        src_path = os.getenv("DATABASE_PATH", "./dealbot.db")

        def _do_backup():
            src = sqlite3.connect(src_path)
            dest = sqlite3.connect(str(path))
            with dest:
                src.backup(dest)
            src.close()
            dest.close()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_backup)

        fernet = _get_fernet()
        if fernet:
            enc_path = path.with_suffix(".db.enc")
            enc_path.write_bytes(fernet.encrypt(path.read_bytes()))
            path.unlink()
            path = enc_path
            log.info(f"Encrypted backup: {enc_path}")
        else:
            log.info(f"Backup: {path}")

        _prune(keep=7)
        return path
    except Exception as exc:
        log.exception(f"Backup failed: {exc}")
        return None


def _prune(keep: int = 7) -> None:
    """Remove old backup files beyond the keep count."""
    files = sorted(BACKUP_DIR.glob("dealbot_*"), reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except Exception:
            pass


async def export_all_deals_csv(db: aiosqlite.Connection, user_id: int) -> str:
    rows = await queries.get_all_deals_for_user(db, user_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["deal_uuid", "network", "party_a", "party_b", "type",
                "amount", "currency", "due_date", "status", "created_at"])
    for r in rows:
        w.writerow([r["deal_uuid"], r["network_name"],
                    r["party_a_username"], r["party_b_username"],
                    r["deal_type"], r["amount"], r["currency"],
                    r["due_date"], r["status"], r["created_at"]])
    return buf.getvalue()


async def export_personal_data(db: aiosqlite.Connection, discord_id: int) -> str:
    user = await queries.get_user_by_discord_id(db, discord_id)
    if not user:
        return "No data found."

    deals = await queries.get_all_deals_for_user(db, user["id"])
    notes = await queries.get_all_notes_by_user(db, user["id"])

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["=== DEALS ==="])
    w.writerow(["deal_uuid", "network", "party_a", "party_b", "type",
                "amount", "currency", "due_date", "status", "created_at"])
    for r in deals:
        w.writerow([r["deal_uuid"], r["network_name"],
                    r["party_a_username"], r["party_b_username"],
                    r["deal_type"], r["amount"], r["currency"],
                    r["due_date"], r["status"], r["created_at"]])
    w.writerow([])
    w.writerow(["=== NOTES ==="])
    w.writerow(["deal_uuid", "note_text", "created_at"])
    for n in notes:
        w.writerow([n["deal_uuid"], n["note_text"], n["created_at"]])
    return buf.getvalue()
