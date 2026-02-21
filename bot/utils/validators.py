from __future__ import annotations

from datetime import datetime
from typing import Optional


def validate_amount(raw: str) -> tuple[bool, str, float]:
    try:
        amount = float(raw.replace(",", "").strip())
        if amount <= 0:
            return False, "Amount must be greater than zero.", 0.0
        if amount > 1_000_000_000:
            return False, "Amount exceeds maximum allowed (1,000,000,000).", 0.0
        return True, "", amount
    except ValueError:
        return False, f"'{raw}' is not a valid number.", 0.0


def validate_due_date(raw: str) -> tuple[bool, str, Optional[datetime]]:
    raw = raw.strip()
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return False, "Date must be in YYYY-MM-DD format (e.g. 2025-12-31).", None
    if dt < datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0):
        return False, "Due date cannot be in the past.", None
    return True, "", dt


def validate_note_text(text: str) -> tuple[bool, str]:
    text = text.strip()
    if len(text) < 3:
        return False, "Note must be at least 3 characters."
    if len(text) > 1000:
        return False, "Note must be 1,000 characters or fewer."
    return True, ""


def validate_dispute_reason(text: str) -> tuple[bool, str]:
    text = text.strip()
    if len(text) < 10:
        return False, "Dispute reason must be at least 10 characters."
    if len(text) > 500:
        return False, "Dispute reason must be 500 characters or fewer."
    return True, ""


def validate_currency(raw: str) -> tuple[bool, str, str]:
    cleaned = raw.strip().upper()
    if not cleaned or len(cleaned) > 16:
        return False, "Currency must be 1–16 characters (e.g. USD, diamonds, coins).", ""
    return True, "", cleaned
