from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import aiosqlite

log = logging.getLogger(__name__)


async def upsert_user(db: aiosqlite.Connection, discord_id: int, discord_name: str) -> int:
    await db.execute(
        """
        INSERT INTO users (discord_id, discord_name) VALUES (?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET discord_name = excluded.discord_name
        """,
        (discord_id, discord_name),
    )
    await db.commit()
    async with db.execute("SELECT id FROM users WHERE discord_id=?", (discord_id,)) as cur:
        row = await cur.fetchone()
    return row["id"]


async def get_user_by_discord_id(db: aiosqlite.Connection, discord_id: int) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT * FROM users WHERE discord_id=?", (discord_id,)) as cur:
        return await cur.fetchone()


async def get_all_networks(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM networks ORDER BY name") as cur:
        return await cur.fetchall()


async def get_network_by_name(db: aiosqlite.Connection, name: str) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT * FROM networks WHERE LOWER(name)=LOWER(?)", (name,)) as cur:
        return await cur.fetchone()


async def get_network_by_id(db: aiosqlite.Connection, nid: int) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT * FROM networks WHERE id=?", (nid,)) as cur:
        return await cur.fetchone()


async def upsert_profile(
    db: aiosqlite.Connection, user_id: int, network_id: int, platform_username: str
) -> int:
    await db.execute(
        """
        INSERT INTO user_network_profiles (user_id, network_id, platform_username)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, network_id)
        DO UPDATE SET platform_username = excluded.platform_username
        """,
        (user_id, network_id, platform_username),
    )
    await db.commit()
    async with db.execute(
        "SELECT id FROM user_network_profiles WHERE user_id=? AND network_id=?",
        (user_id, network_id),
    ) as cur:
        row = await cur.fetchone()
    return row["id"]


async def get_profile_by_id(db: aiosqlite.Connection, profile_id: int) -> Optional[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT unp.*, u.discord_id, u.discord_name, n.name AS network_name
        FROM user_network_profiles unp
        JOIN users u ON u.id = unp.user_id
        JOIN networks n ON n.id = unp.network_id
        WHERE unp.id=?
        """,
        (profile_id,),
    ) as cur:
        return await cur.fetchone()


async def get_profiles_for_user(db: aiosqlite.Connection, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT unp.*, n.name AS network_name
        FROM user_network_profiles unp
        JOIN networks n ON n.id = unp.network_id
        WHERE unp.user_id=?
        ORDER BY n.name
        """,
        (user_id,),
    ) as cur:
        return await cur.fetchall()


async def create_deal(
    db: aiosqlite.Connection,
    deal_uuid: str,
    guild_id: Optional[int],
    network_id: int,
    party_a_profile_id: int,
    party_b_profile_id: int,
    deal_type: str,
    amount: float,
    currency: str,
    due_date: str,
) -> int:
    async with db.execute(
        """
        INSERT INTO deals
            (deal_uuid, guild_id, network_id, party_a_profile_id, party_b_profile_id,
             deal_type, amount, currency, due_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_confirmation')
        """,
        (deal_uuid, guild_id, network_id, party_a_profile_id, party_b_profile_id,
         deal_type, amount, currency, due_date),
    ) as cur:
        deal_id = cur.lastrowid
    await db.execute("INSERT INTO deal_confirmations (deal_id) VALUES (?)", (deal_id,))
    await db.commit()
    return deal_id


async def get_deal_by_uuid(db: aiosqlite.Connection, deal_uuid: str) -> Optional[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT
            d.*,
            n.name               AS network_name,
            pa.platform_username AS party_a_username,
            pb.platform_username AS party_b_username,
            ua.discord_id        AS party_a_discord_id,
            ub.discord_id        AS party_b_discord_id,
            ua.discord_name      AS party_a_discord_name,
            ub.discord_name      AS party_b_discord_name,
            dc.party_a_confirmed,
            dc.party_b_confirmed,
            dc.completion_confirmed_by_a,
            dc.completion_confirmed_by_b
        FROM deals d
        JOIN networks n               ON n.id  = d.network_id
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id = d.party_b_profile_id
        JOIN users ua                 ON ua.id = pa.user_id
        JOIN users ub                 ON ub.id = pb.user_id
        JOIN deal_confirmations dc    ON dc.deal_id = d.id
        WHERE d.deal_uuid=?
        """,
        (deal_uuid,),
    ) as cur:
        return await cur.fetchone()


async def update_deal_status(
    db: aiosqlite.Connection,
    deal_id: int,
    status: str,
    disputed_reason: Optional[str] = None,
) -> None:
    if disputed_reason is not None:
        await db.execute(
            "UPDATE deals SET status=?, disputed_reason=? WHERE id=?",
            (status, disputed_reason, deal_id),
        )
    else:
        await db.execute("UPDATE deals SET status=? WHERE id=?", (status, deal_id))
    await db.commit()


async def set_party_confirmed(db: aiosqlite.Connection, deal_id: int, is_party_a: bool) -> None:
    col = "party_a_confirmed" if is_party_a else "party_b_confirmed"
    await db.execute(f"UPDATE deal_confirmations SET {col}=1 WHERE deal_id=?", (deal_id,))
    await db.commit()


async def set_completion_confirmed(db: aiosqlite.Connection, deal_id: int, is_party_a: bool) -> None:
    col = "completion_confirmed_by_a" if is_party_a else "completion_confirmed_by_b"
    await db.execute(f"UPDATE deal_confirmations SET {col}=1 WHERE deal_id=?", (deal_id,))
    await db.commit()


async def search_deals_for_user(
    db: aiosqlite.Connection,
    user_id: int,
    status_filter: Optional[str] = None,
    network_filter: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    clauses = ["(ua.id=? OR ub.id=?)"]
    params: list = [user_id, user_id]
    if status_filter:
        clauses.append("d.status=?")
        params.append(status_filter)
    if network_filter:
        clauses.append("d.network_id=?")
        params.append(network_filter)
    where = " AND ".join(clauses)
    params += [limit, offset]
    async with db.execute(
        f"""
        SELECT d.deal_uuid, d.status, d.deal_type, d.amount, d.currency,
               d.due_date, d.created_at, n.name AS network_name,
               pa.platform_username AS party_a_username,
               pb.platform_username AS party_b_username
        FROM deals d
        JOIN networks n               ON n.id  = d.network_id
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id = d.party_b_profile_id
        JOIN users ua                 ON ua.id = pa.user_id
        JOIN users ub                 ON ub.id = pb.user_id
        WHERE {where}
        ORDER BY d.created_at DESC
        LIMIT ? OFFSET ?
        """,
        params,
    ) as cur:
        return await cur.fetchall()


async def count_deals_for_user(
    db: aiosqlite.Connection,
    user_id: int,
    status_filter: Optional[str] = None,
    network_filter: Optional[int] = None,
) -> int:
    clauses = ["(ua.id=? OR ub.id=?)"]
    params: list = [user_id, user_id]
    if status_filter:
        clauses.append("d.status=?")
        params.append(status_filter)
    if network_filter:
        clauses.append("d.network_id=?")
        params.append(network_filter)
    where = " AND ".join(clauses)
    async with db.execute(
        f"""
        SELECT COUNT(*) AS cnt FROM deals d
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id = d.party_b_profile_id
        JOIN users ua ON ua.id = pa.user_id
        JOIN users ub ON ub.id = pb.user_id
        WHERE {where}
        """,
        params,
    ) as cur:
        row = await cur.fetchone()
    return row["cnt"]


async def count_open_deals_for_user(db: aiosqlite.Connection, user_id: int) -> int:
    async with db.execute(
        """
        SELECT COUNT(*) AS cnt FROM deals d
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id = d.party_b_profile_id
        WHERE (pa.user_id=? OR pb.user_id=?)
          AND d.status NOT IN ('completed','defaulted','cancelled')
        """,
        (user_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    return row["cnt"]


async def get_daily_volume_for_user(db: aiosqlite.Connection, user_id: int) -> float:
    today = date.today().isoformat()
    async with db.execute(
        """
        SELECT COALESCE(SUM(d.amount), 0) AS vol FROM deals d
        JOIN user_network_profiles pa ON pa.id = d.party_a_profile_id
        WHERE pa.user_id=? AND DATE(d.created_at)=?
        """,
        (user_id, today),
    ) as cur:
        row = await cur.fetchone()
    return float(row["vol"])


async def get_last_dispute_time(db: aiosqlite.Connection, user_id: int) -> Optional[str]:
    async with db.execute(
        "SELECT last_dispute_at FROM user_rate_limits WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["last_dispute_at"] if row else None


async def set_last_dispute_time(db: aiosqlite.Connection, user_id: int, ts: str) -> None:
    await db.execute(
        """
        INSERT INTO user_rate_limits (user_id, last_dispute_at) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET last_dispute_at=excluded.last_dispute_at
        """,
        (user_id, ts),
    )
    await db.commit()


async def get_server_config(
    db: aiosqlite.Connection, guild_id: int
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM server_config WHERE guild_id=?", (guild_id,)
    ) as cur:
        return await cur.fetchone()


async def upsert_server_config(
    db: aiosqlite.Connection,
    guild_id: int,
    max_open_deals: int,
    max_daily_volume: float,
    dispute_cooldown_hours: int,
    reminder_freq_minutes: int,
    weekly_summary_enabled: bool,
    mediator_role_name: str,
) -> None:
    await db.execute(
        """
        INSERT INTO server_config
            (guild_id, max_open_deals, max_daily_volume, dispute_cooldown_hours,
             reminder_freq_minutes, weekly_summary_enabled, mediator_role_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            max_open_deals         = excluded.max_open_deals,
            max_daily_volume       = excluded.max_daily_volume,
            dispute_cooldown_hours = excluded.dispute_cooldown_hours,
            reminder_freq_minutes  = excluded.reminder_freq_minutes,
            weekly_summary_enabled = excluded.weekly_summary_enabled,
            mediator_role_name     = excluded.mediator_role_name
        """,
        (guild_id, max_open_deals, max_daily_volume, dispute_cooldown_hours,
         reminder_freq_minutes, 1 if weekly_summary_enabled else 0, mediator_role_name),
    )
    await db.commit()


async def get_deals_due_within_hours(db: aiosqlite.Connection, hours: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT d.*, n.name AS network_name,
               pa.platform_username AS party_a_username,
               pb.platform_username AS party_b_username,
               ua.discord_id AS party_a_discord_id,
               ub.discord_id AS party_b_discord_id
        FROM deals d
        JOIN networks n ON n.id=d.network_id
        JOIN user_network_profiles pa ON pa.id=d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id=d.party_b_profile_id
        JOIN users ua ON ua.id=pa.user_id
        JOIN users ub ON ub.id=pb.user_id
        WHERE d.status IN ('active','pending_completion')
          AND d.due_date <= datetime('now', ? || ' hours')
          AND d.due_date >  datetime('now')
        """,
        (str(hours),),
    ) as cur:
        return await cur.fetchall()


async def get_overdue_active_deals(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT d.*, n.name AS network_name,
               pa.platform_username AS party_a_username,
               pb.platform_username AS party_b_username,
               ua.discord_id AS party_a_discord_id,
               ub.discord_id AS party_b_discord_id
        FROM deals d
        JOIN networks n ON n.id=d.network_id
        JOIN user_network_profiles pa ON pa.id=d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id=d.party_b_profile_id
        JOIN users ua ON ua.id=pa.user_id
        JOIN users ub ON ub.id=pb.user_id
        WHERE d.status IN ('active','pending_completion')
          AND d.due_date < datetime('now')
        """
    ) as cur:
        return await cur.fetchall()


async def mark_overdue_deals(db: aiosqlite.Connection) -> int:
    async with db.execute(
        """
        UPDATE deals SET status='overdue'
        WHERE status IN ('active','pending_completion')
          AND due_date < datetime('now')
        """
    ) as cur:
        count = cur.rowcount
    await db.commit()
    return count


async def get_all_active_deals_for_user(
    db: aiosqlite.Connection, user_id: int
) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT d.deal_uuid, d.status, d.due_date, n.name AS network_name,
               pa.platform_username AS party_a_username,
               pb.platform_username AS party_b_username
        FROM deals d
        JOIN networks n ON n.id=d.network_id
        JOIN user_network_profiles pa ON pa.id=d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id=d.party_b_profile_id
        JOIN users ua ON ua.id=pa.user_id
        JOIN users ub ON ub.id=pb.user_id
        WHERE (ua.id=? OR ub.id=?)
          AND d.status NOT IN ('completed','defaulted','cancelled')
        ORDER BY d.due_date ASC
        """,
        (user_id, user_id),
    ) as cur:
        return await cur.fetchall()


async def get_all_users_with_active_deals(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT DISTINCT u.discord_id, u.id AS user_id
        FROM users u
        JOIN user_network_profiles unp ON unp.user_id=u.id
        JOIN deals d ON (d.party_a_profile_id=unp.id OR d.party_b_profile_id=unp.id)
        WHERE d.status NOT IN ('completed','defaulted','cancelled')
        """
    ) as cur:
        return await cur.fetchall()


async def get_reputation_rows(
    db: aiosqlite.Connection,
    user_id: int,
    network_id: Optional[int] = None,
) -> list[aiosqlite.Row]:
    network_filter = "AND d.network_id=?" if network_id else ""
    params: list = [user_id, user_id]
    if network_id:
        params.append(network_id)
    async with db.execute(
        f"""
        SELECT n.name AS network_name, d.amount, d.status, d.created_at
        FROM deals d
        JOIN networks n ON n.id=d.network_id
        JOIN user_network_profiles pa ON pa.id=d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id=d.party_b_profile_id
        WHERE (pa.user_id=? OR pb.user_id=?)
          AND d.status NOT IN ('pending_confirmation','cancelled','disputed')
          {network_filter}
        ORDER BY d.created_at DESC
        """,
        params,
    ) as cur:
        return await cur.fetchall()


async def insert_audit_log(
    db: aiosqlite.Connection,
    deal_id: int,
    actor_profile_id: Optional[int],
    previous_status: str,
    new_status: str,
    action_type: str,
    metadata: Optional[str] = None,
) -> None:
    await db.execute(
        """
        INSERT INTO deal_audit_log
            (deal_id, actor_profile_id, previous_status, new_status, action_type, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (deal_id, actor_profile_id, previous_status, new_status, action_type, metadata),
    )
    await db.commit()


async def get_audit_log_for_deal(db: aiosqlite.Connection, deal_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT al.*, unp.platform_username AS actor_username
        FROM deal_audit_log al
        LEFT JOIN user_network_profiles unp ON unp.id=al.actor_profile_id
        WHERE al.deal_id=?
        ORDER BY al.timestamp ASC
        """,
        (deal_id,),
    ) as cur:
        return await cur.fetchall()


async def add_note(
    db: aiosqlite.Connection,
    deal_id: int,
    author_profile_id: int,
    note_text: str,
) -> int:
    async with db.execute(
        "INSERT INTO deal_notes (deal_id, author_profile_id, note_text) VALUES (?, ?, ?)",
        (deal_id, author_profile_id, note_text),
    ) as cur:
        note_id = cur.lastrowid
    await db.commit()
    return note_id


async def get_notes_for_deal(db: aiosqlite.Connection, deal_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT dn.*, unp.platform_username AS author_username
        FROM deal_notes dn
        JOIN user_network_profiles unp ON unp.id=dn.author_profile_id
        WHERE dn.deal_id=?
        ORDER BY dn.created_at ASC
        """,
        (deal_id,),
    ) as cur:
        return await cur.fetchall()


async def log_command(
    db: aiosqlite.Connection,
    guild_id: Optional[int],
    user_id: Optional[int],
    command: str,
    success: bool = True,
    detail: Optional[str] = None,
) -> None:
    await db.execute(
        """
        INSERT INTO command_usage_log (guild_id, user_id, command, success, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (guild_id, user_id, command, 1 if success else 0, detail),
    )
    await db.commit()


async def get_command_stats(
    db: aiosqlite.Connection,
    guild_id: Optional[int] = None,
    days: int = 30,
) -> list[aiosqlite.Row]:
    params: list = [days]
    guild_clause = ""
    if guild_id:
        guild_clause = "AND guild_id=?"
        params.insert(0, guild_id)
    async with db.execute(
        f"""
        SELECT command,
               COUNT(*) AS total,
               SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS ok,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS errors
        FROM command_usage_log
        WHERE timestamp >= datetime('now', '-' || ? || ' days')
          {guild_clause}
        GROUP BY command
        ORDER BY total DESC
        """,
        params,
    ) as cur:
        return await cur.fetchall()


async def get_all_deals_for_user(db: aiosqlite.Connection, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT d.*, n.name AS network_name,
               pa.platform_username AS party_a_username,
               pb.platform_username AS party_b_username
        FROM deals d
        JOIN networks n ON n.id=d.network_id
        JOIN user_network_profiles pa ON pa.id=d.party_a_profile_id
        JOIN user_network_profiles pb ON pb.id=d.party_b_profile_id
        WHERE pa.user_id=? OR pb.user_id=?
        ORDER BY d.created_at DESC
        """,
        (user_id, user_id),
    ) as cur:
        return await cur.fetchall()


async def get_all_notes_by_user(db: aiosqlite.Connection, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT dn.*, d.deal_uuid
        FROM deal_notes dn
        JOIN user_network_profiles unp ON unp.id=dn.author_profile_id
        JOIN deals d ON d.id=dn.deal_id
        WHERE unp.user_id=?
        ORDER BY dn.created_at DESC
        """,
        (user_id,),
    ) as cur:
        return await cur.fetchall()
