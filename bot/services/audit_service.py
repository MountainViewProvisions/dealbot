from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

from bot.database import queries
from bot.models.enums import ActionType

log = logging.getLogger(__name__)


async def log_transition(
    db: aiosqlite.Connection,
    deal_id: int,
    actor_profile_id: Optional[int],
    previous_status: str,
    new_status: str,
    action_type: ActionType,
    metadata: Optional[str] = None,
) -> None:
    await queries.insert_audit_log(
        db,
        deal_id=deal_id,
        actor_profile_id=actor_profile_id,
        previous_status=previous_status,
        new_status=new_status,
        action_type=action_type.value,
        metadata=metadata,
    )
    log.debug(
        f"Audit: deal {deal_id} | {previous_status} → {new_status} "
        f"| action={action_type.value} | actor={actor_profile_id}"
    )


async def get_deal_history(
    db: aiosqlite.Connection, deal_id: int
) -> list[aiosqlite.Row]:
    return await queries.get_audit_log_for_deal(db, deal_id)
