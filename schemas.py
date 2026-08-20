from datetime import datetime
from pydantic import BaseModel


class ActivityIn(BaseModel):
    process_name: str
    window_title: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_seconds: float


class ActivityBatchIn(BaseModel):
    events: list[ActivityIn]