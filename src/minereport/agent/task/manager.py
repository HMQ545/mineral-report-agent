"""TaskManager: lifecycle + request idempotency (thin wrapper over the store)."""

from __future__ import annotations

from typing import Any

from ..session.store import OwnershipError, Store
from .models import TERMINAL_STATUSES, TaskStatus


class TaskManager:
    def __init__(self, store: Store) -> None:
        self.store = store

    # ------------------------------------------------------------- request level
    def replay(self, session_id: str, request_id: str) -> dict[str, Any] | None:
        """A repeated ``request_id`` is answered from the recorded result."""

        record = self.store.get_request(session_id, request_id)
        if record and record.get("status") == "completed":
            return record
        return None

    def begin_request(self, session_id: str, request_id: str, task_id: str) -> tuple[bool, dict[str, Any]]:
        return self.store.begin_request(session_id, request_id, task_id)

    def finish_request(
        self, session_id: str, request_id: str, *, status: str, task_id: str | None = None, reply: str | None = None
    ) -> None:
        self.store.finish_request(session_id, request_id, status=status, task_id=task_id, reply=reply)

    # ---------------------------------------------------------------- lifecycle
    def create(
        self,
        *,
        session_id: str,
        user_id: str,
        intent: str,
        slots: dict[str, Any],
        missing: list[str],
        description: str = "",
    ) -> dict[str, Any]:
        status = TaskStatus.RUNNING.value if not missing else TaskStatus.WAITING_FOR_SLOTS.value
        return self.store.create_task(
            session_id=session_id,
            user_id=user_id,
            intent=intent,
            slots=slots,
            missing=missing,
            description=description,
            status=status,
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def resumable(self, session_id: str, user_id: str) -> list[dict[str, Any]]:
        return self.store.list_resumable(session_id, user_id)

    def all_for_session(self, session_id: str, user_id: str) -> list[dict[str, Any]]:
        return self.store.list_tasks(session_id, user_id=user_id)

    def update(self, task_id: str, **fields: Any) -> dict[str, Any]:
        return self.store.update_task(task_id, **fields)

    def require_resumable(self, task_id: str, *, user_id: str, session_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id}")
        if task["user_id"] != user_id or task["session_id"] != session_id:
            raise OwnershipError(f"task {task_id} does not belong to this user/session")
        if task["status"] in {status.value for status in TERMINAL_STATUSES}:
            raise ValueError(f"task {task_id} is {task['status']} and cannot be resumed")
        return task

    def mark_running(self, task_id: str) -> dict[str, Any]:
        return self.update(task_id, status=TaskStatus.RUNNING.value, suspended=False)

    def mark_waiting(self, task_id: str, *, missing: list[str], suspended: bool) -> dict[str, Any]:
        return self.update(
            task_id, status=TaskStatus.WAITING_FOR_SLOTS.value, missing=missing, suspended=suspended
        )

    def complete(self, task_id: str, result: Any | None = None) -> dict[str, Any]:
        return self.update(task_id, status=TaskStatus.COMPLETED.value, result=result or {}, error="")

    def fail(self, task_id: str, error: str) -> dict[str, Any]:
        return self.update(task_id, status=TaskStatus.FAILED.value, error=error)

    def cancel(self, task_id: str, reason: str = "") -> dict[str, Any]:
        return self.update(task_id, status=TaskStatus.CANCELLED.value, error=reason or "")

    def suspend(self, task_id: str) -> dict[str, Any]:
        return self.update(task_id, suspended=True)

    def resume(self, task_id: str) -> dict[str, Any]:
        return self.update(task_id, suspended=False)


__all__ = ["TaskManager"]