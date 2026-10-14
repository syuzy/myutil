from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    seq: int = Field(ge=1)
    timestamp: datetime
    elapsed_ms: float = Field(ge=0)
    session_id: str
    page_id: str | None = None
    frame_id: str | None = None
    annotation_id: str | None = None
    source: str
    type: str
    payload: dict[str, Any]


class Annotation(BaseModel):
    id: str
    name: str
    goal: str | None = None
    expected_result: str | None = None
    tags: list[str] = Field(default_factory=list)
    start_seq: int
    end_seq: int | None = None
