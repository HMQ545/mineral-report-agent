"""Turn classification: rules first, DeepSeek only to break ties.

The model may only pick from the fixed intent / action enums; it can never invent
a task or a data source. When a real model is required (``--llm deepseek`` /
``--require-live``) a model failure raises instead of silently degrading.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..llm import LlmError
from .extractor import extract_slots as _extract_for_rules
from .schemas import BUSINESS_INTENTS, Intent, IntentResult, TurnAction

CANCEL_PATTERNS = ("取消", "先不查了", "不用查了", "不查了", "算了", "停止查询", "别查了")
CONTINUE_PATTERNS = ("继续", "接着", "刚才", "之前", "上一个", "上次", "那个查询", "恢复", "未完成")
RESTART_PATTERNS = ("重新查", "再查一遍", "重查", "重新查询", "再来一次")
GENERAL_CHAT_HINTS = (
    "你好", "您好", "hi", "hello", "嗨", "谢谢", "什么是", "是什么", "介绍一下", "解释",
    "为什么", "怎么", "如何", "帮我写", "写个", "python", "asyncio", "协程", "代码",
    "笑话", "天气", "翻译",
)
# Order matters: the first hit wins, so trend is checked before price.
INTENT_KEYWORDS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.DAILY_REPORT, ("日报", "简报", "今日报告", "生成报告", "report")),
    (Intent.NEWS_SEARCH, ("新闻", "消息", "资讯", "报道")),
    (Intent.RESOURCE_QUERY, ("资源量", "储量", "矿产资源", "矿石量", "品位")),
    (Intent.TREND_QUERY, ("走势", "趋势", "trend")),
    (Intent.PRICE_QUERY, ("价格", "报价", "多少钱", "price", "均价", "锂价")),
)


class ModelVerdict(BaseModel):
    """What the model is allowed to answer."""

    intent: Intent = Field(description="one of the fixed intent values")
    action: TurnAction = Field(description="one of the fixed turn actions")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def intent_from_keywords(text: str) -> Intent | None:
    for intent, keywords in INTENT_KEYWORDS:
        if _has_any(text, keywords):
            return intent
    return None


def classify_by_rules(
    text: str,
    *,
    pending_tasks: list[dict[str, Any]] | None = None,
    extracted: dict[str, Any] | None = None,
) -> IntentResult:
    """Deterministic first pass. Only confident cases set a high score."""

    pending = pending_tasks or []
    slots = extracted or {}

    if _has_any(text, CANCEL_PATTERNS):
        # "先不查了，解释一下 Python 协程" cancels the pending task *and* asks
        # something else in the same turn; only a bare cancel ends the turn.
        residual = text
        for pattern in CANCEL_PATTERNS:
            residual = residual.replace(pattern, " ")
        residual_slots, _ = _extract_for_rules(residual)
        residual_intent = intent_from_keywords(residual)
        if residual_intent is not None or _has_any(residual, GENERAL_CHAT_HINTS) or residual_slots:
            return IntentResult(
                intent=residual_intent or Intent.GENERAL_CHAT,
                action=(
                    TurnAction.NEW_TASK if residual_intent is not None else TurnAction.GENERAL_CHAT
                ),
                confidence=0.75,
                reason="本轮同时表达取消与新的诉求",
                also_cancel=True,
            )
        return IntentResult(
            intent=Intent.UNKNOWN,
            action=TurnAction.CANCEL_TASK,
            confidence=0.95,
            reason="用户表达取消",
        )

    if _has_any(text, RESTART_PATTERNS):
        return IntentResult(
            intent=intent_from_keywords(text) or Intent.UNKNOWN,
            action=TurnAction.NEW_TASK,
            confidence=0.9,
            reason="用户要求重新查询（不复用旧任务）",
        )

    if _has_any(text, CONTINUE_PATTERNS):
        return IntentResult(
            intent=intent_from_keywords(text) or Intent.UNKNOWN,
            action=TurnAction.CONTINUE_TASK,
            confidence=0.85,
            reason="用户要求继续之前的任务",
        )

    keyword_intent = intent_from_keywords(text)

    identifying = {
        key: value
        for key, value in slots.items()
        if key in {"commodity", "company", "project", "date", "days"} and value
    }
    def consistent_with_pending() -> list[dict[str, Any]]:
        from .extractor import commodities_in_text as _commodities_in_text

        fresh: list[dict[str, Any]] = []
        for task in pending:
            slots_of_task = task.get("slots") or {}
            contradicted = any(
                value
                and slots_of_task.get(slot)
                and slots_of_task[slot] != value
                for slot, value in identifying.items()
            )
            if not contradicted and identifying.get("commodity"):
                # The task may have been created while the product was still
                # ambiguous; its own wording is then the only evidence we have.
                task_products = set(_commodities_in_text(task.get("description") or ""))
                if slots_of_task.get("commodity"):
                    task_products.add(slots_of_task["commodity"])
                if task_products and identifying["commodity"] not in task_products:
                    contradicted = True
            if contradicted:
                continue
            fresh.append(task)
        return fresh

    if pending and identifying and not _has_any(text, GENERAL_CHAT_HINTS):
        # Slots were supplied and a waiting task is consistent with them: treat as
        # completion. If every pending task contradicts the new slots, this is a
        # new request instead.
        consistent = consistent_with_pending()
        def compatible(task_intent: str) -> bool:
            from .schemas import is_compatible_transition

            if keyword_intent is None:
                return True
            return is_compatible_transition(Intent(task_intent), keyword_intent)

        if consistent and any(compatible(task["intent"]) for task in consistent):
            target = next(
                (task for task in consistent if compatible(task["intent"])), consistent[0]
            )
            return IntentResult(
                intent=keyword_intent or Intent(target["intent"]),
                action=TurnAction.SLOT_COMPLETION,
                confidence=0.8,
                reason="检测到槽位输入且存在可兼容的待补全任务",
            )

    if keyword_intent is not None:
        return IntentResult(
            intent=keyword_intent,
            action=TurnAction.NEW_TASK,
            confidence=0.8,
            reason=f"命中业务关键词 {keyword_intent.value}",
        )

    if _has_any(text, GENERAL_CHAT_HINTS):
        return IntentResult(
            intent=Intent.GENERAL_CHAT,
            action=TurnAction.GENERAL_CHAT,
            confidence=0.7,
            reason="命中闲聊/通用问答特征",
        )

    if slots:
        # Business slots without a recognizable goal -> ask the user to clarify.
        return IntentResult(
            intent=Intent.UNKNOWN,
            action=TurnAction.NEW_TASK,
            confidence=0.3,
            reason="出现业务槽位但无法判断目标",
        )

    return IntentResult(
        intent=Intent.GENERAL_CHAT,
        action=TurnAction.GENERAL_CHAT,
        confidence=0.4,
        reason="无业务特征，按普通聊天处理",
    )


async def classify(
    text: str,
    *,
    backend: Any = None,
    pending_tasks: list[dict[str, Any]] | None = None,
    extracted: dict[str, Any] | None = None,
) -> IntentResult:
    """Rules first; the model may only refine ambiguous cases."""

    ruled = classify_by_rules(text, pending_tasks=pending_tasks, extracted=extracted)
    confident = ruled.confidence >= 0.7 and ruled.action in {
        TurnAction.CANCEL_TASK,
        TurnAction.CONTINUE_TASK,
        TurnAction.NEW_TASK,
        TurnAction.SLOT_COMPLETION,
    }
    if backend is None or ruled.action == TurnAction.CANCEL_TASK or confident:
        return ruled

    candidates = [intent.value for intent in BUSINESS_INTENTS] + [
        Intent.GENERAL_CHAT.value,
        Intent.UNKNOWN.value,
    ]
    actions = [action.value for action in TurnAction]
    user = (
        f"用户输入：{text}\n"
        f"规则初判：intent={ruled.intent.value} action={ruled.action.value}（{ruled.reason}）\n"
        f"待补全任务：{json.dumps(pending_tasks or [], ensure_ascii=False)}\n"
        f"仅允许的 intent：{candidates}\n仅允许的 action：{actions}"
    )
    try:
        verdict = await backend.structured(
            ModelVerdict,
            "你是意图分诊器，只能在给定枚举内选择。",
            user,
            stage="intent_classifier",
        )
        return IntentResult(
            intent=verdict.intent,
            action=verdict.action,
            confidence=verdict.confidence,
            reason=verdict.reason or ruled.reason,
            source="rules+model",
        )
    except Exception as exc:  # noqa: BLE001
        if getattr(backend, "mode", "stub") == "deepseek":
            raise LlmError(f"意图识别调用失败：{type(exc).__name__}: {exc}") from exc
        ruled.source = "rules-fallback"
        ruled.reason = f"{ruled.reason}（模型不可用，使用规则结果）"
        return ruled


__all__ = [
    "CANCEL_PATTERNS",
    "CONTINUE_PATTERNS",
    "GENERAL_CHAT_HINTS",
    "INTENT_KEYWORDS",
    "ModelVerdict",
    "classify",
    "classify_by_rules",
    "intent_from_keywords",
]