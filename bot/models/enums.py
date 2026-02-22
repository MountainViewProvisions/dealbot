from __future__ import annotations
from enum import Enum

class DealStatus(str, Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    ACTIVE               = "active"
    PENDING_COMPLETION   = "pending_completion"
    COMPLETED            = "completed"
    DISPUTED             = "disputed"
    OVERDUE              = "overdue"
    DEFAULTED            = "defaulted"
    CANCELLED            = "cancelled"

class DealType(str, Enum):
    HOSTING   = "hosting"
    GIFTING   = "gifting"
    PROMOTION = "promotion"
    COLLAB    = "collab"
    OTHER     = "other"

class ActionType(str, Enum):
    CREATED    = "created"
    CONFIRMED  = "confirmed"
    ACTIVATED  = "activated"
    COMPLETED  = "completed"
    DISPUTED   = "disputed"
    RESOLVED   = "resolved"
    OVERDUE    = "overdue"
    DEFAULTED  = "defaulted"
    CANCELLED  = "cancelled"
    NOTE_ADDED = "note_added"
    CANCELLED_SYSTEM = "cancelled_system"

VALID_TRANSITIONS: dict[DealStatus, set[DealStatus]] = {
    DealStatus.PENDING_CONFIRMATION: {
        DealStatus.ACTIVE,
        DealStatus.CANCELLED,
    },
    DealStatus.ACTIVE: {
        DealStatus.PENDING_COMPLETION,
        DealStatus.DISPUTED,
        DealStatus.OVERDUE,
        DealStatus.CANCELLED,
    },
    DealStatus.PENDING_COMPLETION: {
        DealStatus.COMPLETED,
        DealStatus.DISPUTED,
        DealStatus.OVERDUE,
    },
    DealStatus.DISPUTED: {
        DealStatus.ACTIVE,
        DealStatus.COMPLETED,
        DealStatus.DEFAULTED,
    },
    DealStatus.OVERDUE: {
        DealStatus.PENDING_COMPLETION,
        DealStatus.COMPLETED,
        DealStatus.DISPUTED,
        DealStatus.DEFAULTED,
    },
    DealStatus.COMPLETED:  set(),
    DealStatus.DEFAULTED:  set(),
    DealStatus.CANCELLED:  set(),
}

def validate_transition(current: DealStatus, target: DealStatus) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())
