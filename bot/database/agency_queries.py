from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

log = logging.getLogger(__name__)

# Agency CRUD

async def create_agency(db: aiosqlite.Connection, name: str) -> int:
    """Insert a pending agency (no owner yet). Returns new agency id."""
    async with db.execute(
        "INSERT INTO agencies (name, status) VALUES (?, 'pending')",
        (name,),
    ) as cur:
        agency_id = cur.lastrowid
    await db.commit()
    return agency_id

async def approve_agency(
    db: aiosqlite.Connection, name: str, owner_discord_id: int
) -> bool:
    """Set agency owner and mark as approved. Returns False if agency not found."""
    async with db.execute(
        "SELECT id FROM agencies WHERE LOWER(name)=LOWER(?)", (name,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return False
    await db.execute(
        "UPDATE agencies SET owner_discord_id=?, status='approved' WHERE id=?",
        (owner_discord_id, row["id"]),
    )
    await db.commit()
    return True

async def get_agency_by_name(
    db: aiosqlite.Connection, name: str
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM agencies WHERE LOWER(name)=LOWER(?)", (name,)
    ) as cur:
        return await cur.fetchone()

async def get_agency_by_id(
    db: aiosqlite.Connection, agency_id: int
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM agencies WHERE id=?", (agency_id,)
    ) as cur:
        return await cur.fetchone()

async def list_all_agencies(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM agencies WHERE status='approved' ORDER BY name"
    ) as cur:
        return await cur.fetchall()

# Host membership

async def upsert_host(
    db: aiosqlite.Connection, discord_id: int
) -> int:
    """Ensure a hosts row exists. Returns host row id."""
    await db.execute(
        "INSERT OR IGNORE INTO hosts (discord_id) VALUES (?)",
        (discord_id,),
    )
    await db.commit()
    async with db.execute(
        "SELECT id FROM hosts WHERE discord_id=?", (discord_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["id"]

async def get_host_by_discord_id(
    db: aiosqlite.Connection, discord_id: int
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM hosts WHERE discord_id=?", (discord_id,)
    ) as cur:
        return await cur.fetchone()

async def join_agency(
    db: aiosqlite.Connection,
    discord_id: int,
    agency_id: int,
    pending_approval: bool = False,
) -> None:
    """Link a host to an agency."""
    status = "pending" if pending_approval else "active"
    await upsert_host(db, discord_id)
    await db.execute(
        "UPDATE hosts SET agency_id=?, join_status=? WHERE discord_id=?",
        (agency_id, status, discord_id),
    )
    await db.commit()

async def approve_host_join(
    db: aiosqlite.Connection, discord_id: int
) -> bool:
    async with db.execute(
        "SELECT id FROM hosts WHERE discord_id=? AND join_status='pending'",
        (discord_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return False
    await db.execute(
        "UPDATE hosts SET join_status='active' WHERE discord_id=?", (discord_id,)
    )
    await db.commit()
    return True

async def leave_agency(db: aiosqlite.Connection, discord_id: int) -> None:
    await db.execute(
        "UPDATE hosts SET agency_id=NULL, join_status=NULL WHERE discord_id=?",
        (discord_id,),
    )
    await db.commit()

async def get_hosts_for_agency(
    db: aiosqlite.Connection, agency_id: int
) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM hosts WHERE agency_id=? ORDER BY created_at",
        (agency_id,),
    ) as cur:
        return await cur.fetchall()

# Host notes

async def add_host_note(
    db: aiosqlite.Connection,
    host_discord_id: int,
    author_discord_id: int,
    note: str,
) -> int:
    async with db.execute(
        """
        INSERT INTO host_notes (host_discord_id, author_discord_id, note)
        VALUES (?, ?, ?)
        """,
        (host_discord_id, author_discord_id, note),
    ) as cur:
        note_id = cur.lastrowid
    await db.commit()
    return note_id

async def get_notes_for_host(
    db: aiosqlite.Connection, host_discord_id: int
) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM host_notes WHERE host_discord_id=? ORDER BY created_at DESC",
        (host_discord_id,),
    ) as cur:
        return await cur.fetchall()

# Agency deal views

async def get_deals_for_agency(
    db: aiosqlite.Connection, agency_id: int, limit: int = 50
) -> list[aiosqlite.Row]:
    """Return deals where either party belongs to this agency (via hosts table)."""
    async with db.execute(
        """
        SELECT d.deal_uuid, d.status, d.deal_type, d.amount, d.currency,
               d.due_date, d.created_at, n.name AS network_name,
               pa.platform_username AS party_a_username,
               pb.platform_username AS party_b_username,
               ua.discord_id AS party_a_discord_id,
               ub.discord_id AS party_b_discord_id
        FROM deals d
        JOIN networks n               ON n.id  = d.network_id
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id = d.party_b_profile_id
        JOIN users ua                 ON ua.id = pa.user_id
        JOIN users ub                 ON ub.id = pb.user_id
        LEFT JOIN hosts ha            ON ha.discord_id = ua.discord_id
        LEFT JOIN hosts hb            ON hb.discord_id = ub.discord_id
        WHERE (ha.agency_id=? OR hb.agency_id=?)
          AND (ha.join_status='active' OR hb.join_status='active')
        ORDER BY d.created_at DESC
        LIMIT ?
        """,
        (agency_id, agency_id, limit),
    ) as cur:
        return await cur.fetchall()

# Pending registration requests (in-memory per process, stored in DB)

async def store_pending_registration(
    db: aiosqlite.Connection, agency_name: str, requester_discord_id: int
) -> None:
    """Upsert a pending agency registration request."""
    await db.execute(
        """
        INSERT INTO agency_requests (agency_name, requester_discord_id)
        VALUES (?, ?)
        ON CONFLICT(agency_name) DO UPDATE SET
            requester_discord_id=excluded.requester_discord_id,
            requested_at=datetime('now')
        """,
        (agency_name, requester_discord_id),
    )
    await db.commit()

async def get_pending_registration(
    db: aiosqlite.Connection, agency_name: str
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM agency_requests WHERE LOWER(agency_name)=LOWER(?)",
        (agency_name,),
    ) as cur:
        return await cur.fetchone()

async def delete_pending_registration(
    db: aiosqlite.Connection, agency_name: str
) -> None:
    await db.execute(
        "DELETE FROM agency_requests WHERE LOWER(agency_name)=LOWER(?)",
        (agency_name,),
    )
    await db.commit()
