"""Task / effect models and the status vocabulary."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    CREATED = "created"
    WAITING_FOR_SLOTS = "waiting_for_slots"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED)


class EffectStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskView(BaseModel):
    """Read-only projection used by the UI layer and tests."""

    task_id: str
    session_id: str
    user_id: str
    intent: str
    status: str
    suspended: bool = False
    slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in {status.value for status in TERMINAL_STATUSES}


__all__ = ["EffectStatus", "TERMINAL_STATUSES", "TaskStatus", "TaskView"]