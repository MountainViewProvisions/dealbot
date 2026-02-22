from __future__ import annotations

import uuid
from datetime import datetime, timezone

def generate_deal_id() -> str:
    rand_part = uuid.uuid4().hex[:4].upper()
    now = datetime.now(tz=timezone.utc)
    secs = (now.hour * 3600 + now.minute * 60 + now.second)
    time_part = format(secs % 0x10000, "04X")
    return f"{rand_part}{time_part}"
