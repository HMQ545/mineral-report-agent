"""TaskMatcher: pick the *right* historical task to resume.

Rules first (never "just take the most recent task"); the model may only choose
among the candidates that the database returned, so it can never invent a
``task_id``. When the choice is not unique the caller must ask the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..intent.extractor import commodities_in_text, extract_commodities, extract_slots
from ..intent.schemas import Intent, is_compatible_transition
from ..session.store import Store
from .manager import TaskManager

UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
SLOT_HINTS = {"days": ("天", "月", "周", "走势区间"), "date": ("日", "价格", "当天")}


class MatcherVerdict(BaseModel):
    task_id: str = ""
    reason: str = ""


@dataclass
class MatchOutcome:
    status: str  # unique | ambiguous | none
    task: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    @property
    def is_unique(self) -> bool:
        return self.status == "unique" and self.task is not None


class TaskMatcher:
    def __init__(self, store: Store, backend: Any = None) -> None:
        self.store = store
        self.manager = TaskManager(store)
        self.backend = backend

    # ------------------------------------------------------------------ scoring
    @staticmethod
    def _score(
        task: dict[str, Any],
        text: str,
        requested_intent: str | None,
        extracted: dict[str, Any],
    ) -> float:
        score = 0.0

        if requested_intent:
            try:
                if is_compatible_transition(Intent(task["intent"]), Intent(requested_intent)):
                    score += 3.0 if task["intent"] == requested_intent else 2.5
            except ValueError:
                pass

        for slot in ("commodity", "company", "project"):
            value = extracted.get(slot)
            known = task.get("slots", {}).get(slot)
            if value and known == value:
                score += 2.0
            elif value and known and known != value:
                # Explicit evidence that contradicts this task: it cannot be the one.
                return -1.0

        description = (task.get("description") or "").lower()
        if description and any(token and token in text.lower() for token in description.split()):
            score += 1.0

        for slot in task.get("missing_slots") or []:
            for hint in SLOT_HINTS.get(slot, ()):
                if hint in text:
                    score += 0.5
                    break

        # No recency term: time must never be the reason a task is selected.
        return score

    @staticmethod
    def _contradicts(task: dict[str, Any], extracted: dict[str, Any]) -> bool:
        """True when the user's explicit slots rule this task out."""

        for slot in ("commodity", "company", "project"):
            value = extracted.get(slot)
            known = (task.get("slots") or {}).get(slot)
            if value and known and value != known:
                return True

        product = extracted.get("commodity")
        if product:
            known_products = set(commodities_in_text(task.get("description") or ""))
            stored = (task.get("slots") or {}).get("commodity")
            if stored:
                known_products.add(stored)
            if known_products and product not in known_products:
                return True
        return False
    # -------------------------------------------------------------------- match
    async def match(
        self,
        text: str,
        *,
        user_id: str,
        session_id: str,
        requested_intent: str | None = None,
        extracted: dict[str, Any] | None = None,
        explicit_task_id: str | None = None,
    ) -> MatchOutcome:
        if extracted is None:
            # Self-contained fallback: callers may omit the slots, but matching
            # must still see what the user actually asked for.
            extracted, _ = extract_slots(text)
        slots = extracted or {}

        candidate_id = explicit_task_id
        if candidate_id is None:
            found = UUID_RE.search(text)
            if found:
                candidate_id = found.group(1)

        if candidate_id:
            try:
                task = self.manager.require_resumable(
                    candidate_id, user_id=user_id, session_id=session_id
                )
            except Exception as exc:  # noqa: BLE001 - surface the reason verbatim
                return MatchOutcome(status="none", reason=f"{type(exc).__name__}: {exc}")
            return MatchOutcome(status="unique", task=task, reason="用户显式给出 task_id")

        resumable = self.manager.resumable(session_id, user_id)
        if not resumable:
            return MatchOutcome(status="none", reason="该会话没有可恢复的未完成任务")

        mentioned = extract_commodities(text)
        if len(mentioned) > 1:
            # "碳酸锂和铜的走势" spans two products: ask, never pick one silently.
            related = [
                task
                for task in resumable
                if task.get("slots", {}).get("commodity") in mentioned
            ] or resumable
            return MatchOutcome(
                status="ambiguous",
                candidates=related,
                reason=f"输入同时提到多个商品（{'、'.join(mentioned)}），需要用户确认要续接哪个任务",
            )

        scored = [(self._score(task, text, requested_intent, slots), task) for task in resumable]
        scored = [(score, task) for score, task in scored if score > 0]

        if not scored:
            # Explicit slots that contradict a task disqualify it here too.
            compatible = [task for task in resumable if not self._contradicts(task, slots)]
            generic_continue = any(
                marker in text for marker in ("继续", "接着", "刚才", "之前", "上一个", "上次", "恢复")
            )
            identifying = {
                key: value
                for key, value in slots.items()
                if key in ("commodity", "company", "project") and value
            }
            if generic_continue and identifying:
                # The user named something specific, yet no pending task matches it
                # (e.g. the copper task was cancelled). Do not auto-resume an
                # unrelated task just because it is the only one left.
                return MatchOutcome(
                    status="none",
                    reason=(
                        "输入中的槽位与任何未完成任务都不匹配（可能是该任务已取消/已完成），"
                        "不会改用其它任务代替"
                    ),
                )
            if generic_continue and len(compatible) == 1:
                return MatchOutcome(
                    status="unique",
                    task=compatible[0],
                    reason="用户要求继续，且只有一个未完成任务与已给槽位一致",
                )
            if generic_continue and len(compatible) > 1:
                return MatchOutcome(
                    status="ambiguous",
                    candidates=compatible,
                    reason=f"用户要求继续，但有 {len(compatible)} 个未完成任务需要确认是哪一个",
                )
            return MatchOutcome(
                status="none", reason="没有与当前输入匹配的未完成任务（不按时间猜测）"
            )

        top = max(score for score, _ in scored)
        tied = [task for score, task in scored if abs(score - top) < 1e-9]
        if len(tied) == 1:
            return MatchOutcome(status="unique", task=tied[0], reason="唯一语义匹配")

        chosen = await self._disambiguate(text, tied)
        if chosen is not None:
            return MatchOutcome(status="unique", task=chosen, reason="模型在候选集合中选定")
        return MatchOutcome(
            status="ambiguous",
            candidates=tied,
            reason=f"有 {len(tied)} 个待补全任务同样可能匹配，需要用户确认",
        )

    async def _disambiguate(self, text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Ask the model to pick one of the DB candidates (never to invent one)."""

        if self.backend is None or getattr(self.backend, "mode", "stub") != "deepseek":
            return None
        listing = [
            {
                "task_id": task["task_id"],
                "intent": task["intent"],
                "slots": task.get("slots"),
                "missing_slots": task.get("missing_slots"),
                "created_at": task.get("created_at"),
            }
            for task in candidates
        ]
        try:
            verdict = await self.backend.structured(
                MatcherVerdict,
                "你只能从给定候选任务中选择一个 task_id；不得编造新的 ID。",
                f"用户输入：{text}\n候选任务：{listing}",
                stage="task_matcher",
            )
        except Exception:  # noqa: BLE001 - ambiguity is an acceptable outcome
            return None
        for task in candidates:
            if task["task_id"] == verdict.task_id:
                return task
        return None


__all__ = ["MatchOutcome", "MatcherVerdict", "TaskMatcher", "UUID_RE"]