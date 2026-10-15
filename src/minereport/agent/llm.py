"""DeepSeek backend plus a deterministic stub used by the offline test suite.

Root cause of the ``Thinking mode does not support this tool_choice`` 400:

``langchain``'s ``with_structured_output(schema)`` defaults to *tool calling*,
which sends a forced ``tool_choice``. DeepSeek's thinking models reject a forced
tool choice, so every structured call failed before any content was produced.

The fix has two parts, both verified against the live API:
1. disable thinking for these analysis calls via ``extra_body`` (DeepSeek does
   *not* accept ``model_kwargs={"thinking": ...}`` - that raises a TypeError);
2. stop using tool calling altogether and ask for JSON
   (``response_format={"type": "json_object"}``), then validate with Pydantic.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class LlmError(RuntimeError):
    """Raised when a real model call fails. Never swallowed into fake output."""


class LlmUnavailableError(LlmError):
    """Raised when a real model was demanded but cannot be configured."""


class Intent(BaseModel):
    topic: str = Field(description="Short subject of the brief, e.g. Pilbara lithium")
    news_query: str = Field(description="Search query for the news feed")
    commodity: str = Field(description="Canonical commodity key for the price toolkit")


class NewsDigestItem(BaseModel):
    # title/url may be omitted by the model: the source item is authoritative and
    # the graph re-keys every digest entry back onto its input item.
    title: str = ""
    url: str = ""
    news_id: str = Field(default="", description="稳定新闻 ID，必须原样复制输入条目里的 news_id")
    source: str = ""
    published_at: str | None = None
    summary: str = Field(default="", description="中文要点，最多两句话")
    sentiment: str = Field(default="neutral", description="positive | neutral | negative")


class NewsDigest(BaseModel):
    items: list[NewsDigestItem] = Field(default_factory=list)
    overall: str = ""


class RiskCalculation(BaseModel):
    """How a claimed number was derived - re-computed by the quality gate."""

    method: str = Field(default="", description="percentage_change | share | 留空表示不可复算")
    start_value: float | None = None
    end_value: float | None = None
    numerator: float | None = None
    denominator: float | None = None


class RiskClaim(BaseModel):
    claim_id: str = Field(default="", description="稳定结论 ID，例如 risk_price_trend_001")
    claim: str = Field(default="", description="中文结论，最多一句话")
    value: float | None = None
    unit: str = ""
    calculation: RiskCalculation | None = None
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="必须引用 evidence_index 里真实存在的 ID，不得编造",
    )


class RiskSection(BaseModel):
    bullets: list[str] = Field(default_factory=list)
    judgement: str = ""
    claims: list[RiskClaim] = Field(default_factory=list)


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# DeepSeek refuses response_format=json_object unless the messages mention JSON,
# so the JSON-mode path always carries this sentence as a hard guarantee.
JSON_MODE_GUARD = "请只输出一个 JSON 对象，不要输出 Markdown 代码块或任何解释文字。"

_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def redact(text: str) -> str:
    """Never let an API key reach a log line or an exception message."""

    return _API_KEY_RE.sub("sk-***", str(text))



def _extract_json_block(text: str) -> Any:
    """Pull the first fenced JSON block out of a prompt (used by the stub)."""

    match = JSON_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a model answer that should be a JSON object.

    Models sometimes wrap JSON in a Markdown fence or add a sentence around it,
    so unwrap before failing.
    """

    if not text or not text.strip():
        raise LlmError("model returned an empty response")

    candidate = text.strip()
    fenced = JSON_BLOCK_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise LlmError(f"model response is not JSON: {text[:200]!r}") from None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LlmError(f"model response is not valid JSON: {exc}: {text[:200]!r}") from exc

    if not isinstance(parsed, dict):
        raise LlmError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def schema_instruction(schema: type[BaseModel], *, language: str = "中文") -> str:
    """Describe the required JSON shape; json_mode needs it spelled out."""

    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return (
        "你必须只输出一个 JSON 对象，不要输出 Markdown 代码块、解释或多余文字。\n"
        f"JSON 必须符合以下 JSON Schema：\n{schema_json}\n"
        f"所有面向读者的文本字段一律使用{language}。"
    )


class DeepSeekBackend:
    """Real LLM calls. The key is only ever read from the environment/.env."""

    mode = "deepseek"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        api_base: str,
        disable_thinking: bool = True,
    ) -> None:
        from langchain_deepseek import ChatDeepSeek

        self.model = model
        extra_body: dict[str, Any] = {}
        if disable_thinking:
            # A forced tool_choice is rejected while thinking is on; we do not
            # need reasoning tokens for a bounded JSON extraction task anyway.
            extra_body["thinking"] = {"type": "disabled"}

        self._llm = ChatDeepSeek(
            model=model,
            api_key=api_key,
            api_base=api_base,
            temperature=0,
            max_retries=2,
            extra_body=extra_body or None,
        )
        # JSON mode is bound per *runnable*, never on the shared client. A
        # client-level ``response_format`` also applied to plain chat, and
        # DeepSeek rejects ``json_object`` unless the prompt contains the word
        # "json" -- that is exactly the 400 seen with `--chat` + "你好".
        self._json_llm = self._llm.bind(response_format={"type": "json_object"})

    async def _complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        stage: str = "llm",
    ) -> str:
        """One completion. ``json_mode`` decides whether JSON output is demanded.

        Plain chat must NOT carry ``response_format``: DeepSeek answers 400 when
        ``json_object`` is requested without the word "json" in the prompt.
        """

        prompt_user = user
        if json_mode and "json" not in f"{system} {prompt_user}".lower():
            prompt_user = f"{prompt_user}\n\n{JSON_MODE_GUARD}"
        llm = self._json_llm if json_mode else self._llm
        try:
            message = await llm.ainvoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt_user},
                ]
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as LlmError, never swallowed
            raise LlmError(f"[{stage}] {type(exc).__name__}: {redact(str(exc))}") from exc
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else str(content)

    async def structured(
        self,
        schema: type[BaseModel],
        system: str,
        user: str,
        *,
        stage: str | None = None,
    ) -> BaseModel:
        """JSON-mode call + Pydantic validation, with one self-repair retry."""

        label = stage or f"structured:{schema.__name__}"
        prompt = system + "\n\n" + schema_instruction(schema)
        attempt_user = user
        last_error: Exception | None = None

        for attempt in range(2):
            raw = await self._complete(prompt, attempt_user, json_mode=True, stage=label)
            try:
                return schema.model_validate(parse_json_object(raw))
            except (LlmError, ValidationError) as exc:
                last_error = exc
                attempt_user = (
                    user
                    + f"\n\n上一次回复无法通过校验（第 {attempt + 1} 次尝试）：{exc}\n"
                    + "请只返回符合上述 JSON Schema 的 JSON 对象，不要包含任何其他内容。"
                )
        raise LlmError(
            f"[{label}] {schema.__name__} JSON validation failed after 2 attempts: {redact(str(last_error))}"
        )

    async def text(self, system: str, user: str, *, stage: str = "text") -> str:
        """Plain-text completion: no response_format, no JSON requirement."""

        return await self._complete(system, user, json_mode=False, stage=stage)


class StubBackend:
    """Deterministic offline backend: never claims to be a real model."""

    mode = "stub"
    model = "stub"

    async def structured(
        self,
        schema: type[BaseModel],
        system: str,
        user: str,
        *,
        stage: str | None = None,
    ) -> BaseModel:
        payload = _extract_json_block(user)
        if schema is NewsDigest:
            items_payload = payload if isinstance(payload, dict) else {}
            items = []
            for entry in items_payload.get("items", []):
                text = (entry.get("excerpt") or entry.get("snippet") or "").strip()
                summary = re.sub(r"\s+", " ", text)[:180] or "（stub 模式未收到正文）"
                items.append(
                    NewsDigestItem(
                        title=entry.get("title", ""),
                        url=entry.get("url", ""),
                        source=entry.get("source", ""),
                        published_at=entry.get("published_at"),
                        summary=f"[stub 摘要] {summary}",
                        sentiment="neutral",
                    )
                )
            return NewsDigest(
                items=items,
                overall=f"[stub 模式] 共 {len(items)} 条新闻，情绪判定为中性（未经真实模型分析）。",
            )
        if schema is RiskSection:
            rules = payload if isinstance(payload, list) else payload.get("rules", [])
            return RiskSection(
                bullets=[
                    f"[stub 建议] {rule.get('message', '')}"
                    for rule in rules[:5]
                    if isinstance(rule, dict)
                ],
                judgement=(
                    "[stub 模式] 本条综合判断由确定性占位逻辑生成，不是 DeepSeek 的真实输出；"
                    "请配置 DEEPSEEK_API_KEY 后重新生成以获得真实分析。"
                ),
            )
        if schema is Intent:
            mapping = payload if isinstance(payload, dict) else {}
            return Intent(
                topic=mapping.get("query", "unknown"),
                news_query=mapping.get("query", "unknown"),
                commodity=mapping.get("commodity", "lithium_carbonate"),
            )
        raise ValueError(f"stub backend has no recipe for {schema.__name__}")

    async def text(self, system: str, user: str, *, stage: str = "text") -> str:
        return "[stub 模式] 未调用真实模型。"


def build_backend(mode: str = "auto", *, require_real: bool = False) -> tuple[Any, list[str]]:
    """Return ``(backend, notes)``.

    ``mode="deepseek"`` (or ``require_real=True``) demands the real model and
    raises instead of quietly falling back to the stub.
    """

    notes: list[str] = []
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()

    if mode == "stub" and not require_real:
        notes.append("按显式参数使用 stub 后端（未调用任何真实模型）。")
        return StubBackend(), notes

    if require_real or mode == "deepseek":
        if not key:
            raise LlmUnavailableError(
                "real DeepSeek output was required but DEEPSEEK_API_KEY is not configured"
            )
        notes.append(
            f"真实模型模式：{model}（thinking 已关闭；结构化调用走 JSON 模式 + Pydantic 校验，"
            "普通聊天走文本模式）。"
        )
        return DeepSeekBackend(model=model, api_key=key, api_base=base), notes

    if not key:
        notes.append(
            "未检测到 DEEPSEEK_API_KEY，回退到 stub 后端；报告页脚会明确标注，"
            "不会把 stub 输出伪装成真实模型分析。"
        )
        return StubBackend(), notes

    notes.append(f"自动模式：使用真实模型 {model}（thinking 已关闭）。")
    return DeepSeekBackend(model=model, api_key=key, api_base=base), notes


__all__ = [
    "DeepSeekBackend",
    "Intent",
    "LlmError",
    "LlmUnavailableError",
    "NewsDigest",
    "NewsDigestItem",
    "RiskSection",
    "StubBackend",
    "build_backend",
    "JSON_MODE_GUARD",
    "parse_json_object",
    "redact",
    "schema_instruction",
]