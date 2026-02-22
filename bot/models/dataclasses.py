from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from bot.models.enums import DealStatus

@dataclass
class UserModel:
    id: int
    discord_id: int
    discord_name: str
    created_at: datetime

@dataclass
class NetworkModel:
    id: int
    name: str

@dataclass
class UserNetworkProfile:
    id: int
    user_id: int
    network_id: int
    platform_username: str
    network_name: str = ""
    discord_name: str = ""
    discord_id: int = 0

@dataclass
class DealModel:
    id: int
    deal_uuid: str
    network_id: int
    network_name: str
    party_a_profile_id: int
    party_b_profile_id: int
    deal_type: str
    amount: float
    currency: str
    due_date: datetime
    status: DealStatus
    disputed_reason: Optional[str]
    created_at: datetime
    guild_id: Optional[int] = None
    party_a_username: str = ""
    party_b_username: str = ""
    party_a_discord_id: int = 0
    party_b_discord_id: int = 0
    party_a_discord_name: str = ""
    party_b_discord_name: str = ""
    party_a_confirmed: bool = False
    party_b_confirmed: bool = False
    completion_confirmed_by_a: bool = False
    completion_confirmed_by_b: bool = False

@dataclass
class DealNote:
    id: int
    deal_id: int
    author_profile_id: int
    note_text: str
    created_at: datetime
    author_username: str = ""

@dataclass
class AuditEntry:
    id: int
    deal_id: int
    actor_profile_id: Optional[int]
    previous_status: str
    new_status: str
    action_type: str
    metadata: Optional[str]
    timestamp: datetime
    actor_username: str = ""

@dataclass
class NetworkReputation:
    network_name: str
    completed: int = 0
    defaulted: int = 0
    overdue: int = 0
    disputed: int = 0
    total_deals: int = 0
    total_volume: float = 0.0
    weighted_score: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_deals == 0:
            return 0.0
        return round((self.completed / self.total_deals) * 100, 1)

@dataclass
class GlobalReputation:
    discord_name: str
    total_completed: int = 0
    total_defaulted: int = 0
    total_overdue: int = 0
    total_disputed: int = 0
    total_deals: int = 0
    total_volume: float = 0.0
    weighted_score: float = 0.0
    by_network: list[NetworkReputation] = field(default_factory=list)

    @property
    def global_success_rate(self) -> float:
        if self.total_deals == 0:
            return 0.0
        return round((self.total_completed / self.total_deals) * 100, 1)

@dataclass
class ServerLimits:
    guild_id: int
    max_open_deals: int = 10
    max_daily_volume: float = 50_000.0
    dispute_cooldown_hours: int = 24
    reminder_freq_minutes: int = 30
    weekly_summary_enabled: bool = True
    mediator_role_name: str = "DealMediator"

@dataclass
class RateLimitState:
    open_deal_count: int
    daily_volume: float
    last_dispute_at: Optional[datetime]
