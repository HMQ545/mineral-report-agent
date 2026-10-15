"""Structured JSON mode and plain-text chat mode must not bleed into each other.

Regression for: `minereport-agent --chat` + "你好" ->
`BadRequestError: Prompt must contain the word 'json' ... json_object`.
"""

from __future__ import annotations

import pytest

from minereport.agent.llm import DeepSeekBackend, LlmError, NewsDigest, StubBackend


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _BoundRunnable:
    """Mimics ``runnable.bind(...)``: defaults are merged into each call."""

    def __init__(self, client: "FakeChatDeepSeek", defaults: dict) -> None:
        self._client = client
        self._defaults = defaults

    async def ainvoke(self, messages, **kwargs):
        # Faithful to langchain: bind() merges defaults and calls the underlying
        # runnable, so overrides on the client are still honoured.
        return await self._client.ainvoke(messages, **{**self._defaults, **kwargs})


class FakeChatDeepSeek:
    """Records exactly what would be sent to the API."""

    instances: list["FakeChatDeepSeek"] = []
    reply = '{"items": [], "overall": "好"}'

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list[dict] = []
        FakeChatDeepSeek.instances.append(self)

    def bind(self, **kwargs):
        return _BoundRunnable(self, kwargs)

    async def ainvoke(self, messages, **kwargs):
        return await self._record(messages, kwargs)

    async def _record(self, messages, kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return _Message(self.reply)


def _text(call: dict) -> str:
    return "\n".join(str(message["content"]) for message in call["messages"])


@pytest.fixture
def fake_backend(monkeypatch):
    FakeChatDeepSeek.instances = []
    import langchain_deepseek

    monkeypatch.setattr(langchain_deepseek, "ChatDeepSeek", FakeChatDeepSeek)
    backend = DeepSeekBackend(
        model="deepseek-flash", api_key="sk-test-key", api_base="https://api.deepseek.com"
    )
    return backend, FakeChatDeepSeek.instances[-1]


@pytest.mark.anyio
async def test_plain_chat_sends_no_response_format(fake_backend):
    backend, client = fake_backend
    reply = await backend.text("你是助手。", "用户：你好", stage="general_chat")

    assert reply == FakeChatDeepSeek.reply
    assert len(client.calls) == 1
    call = client.calls[0]
    assert "response_format" not in call["kwargs"], "chat must not request JSON"
    # ...and it must not be inherited from the shared client either.
    assert "response_format" not in (client.kwargs.get("model_kwargs") or {})
    assert "json_object" not in str(call)
    assert "json" not in _text(call).lower()


@pytest.mark.anyio
async def test_structured_call_binds_json_mode_and_says_json(fake_backend):
    backend, client = fake_backend
    await backend.structured(NewsDigest, "你是分析师。", "材料：[]")

    call = client.calls[0]
    assert call["kwargs"]["response_format"] == {"type": "json_object"}
    assert "json" in _text(call).lower()
    assert "JSON Schema" in _text(call)


@pytest.mark.anyio
async def test_json_mode_guard_adds_the_word_json_even_if_the_prompt_lacks_it(fake_backend):
    backend, client = fake_backend
    await backend._complete("系统提示", "用户：你好", json_mode=True, stage="guard")

    assert "json" in _text(client.calls[0]).lower()


@pytest.mark.anyio
async def test_thinking_stays_disabled_for_both_modes(fake_backend):
    backend, client = fake_backend
    assert client.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    await backend.text("s", "u")
    await backend.structured(NewsDigest, "s", "u")
    assert len(client.calls) == 2
    assert client.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.anyio
async def test_error_message_names_the_stage_and_redacts_the_key(monkeypatch):
    import langchain_deepseek

    class Boom(FakeChatDeepSeek):
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("401 Unauthorized for key sk-abcdefgh12345")

    monkeypatch.setattr(langchain_deepseek, "ChatDeepSeek", Boom)
    backend = DeepSeekBackend(
        model="deepseek-flash", api_key="sk-abcdefgh12345", api_base="https://api.deepseek.com"
    )

    with pytest.raises(LlmError) as excinfo:
        await backend.text("s", "u", stage="general_chat")

    message = str(excinfo.value)
    assert "[general_chat]" in message
    assert "sk-abcdefgh12345" not in message
    assert "sk-***" in message


@pytest.mark.anyio
async def test_structured_error_also_names_its_stage(monkeypatch):
    import langchain_deepseek

    class Boom(FakeChatDeepSeek):
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(langchain_deepseek, "ChatDeepSeek", Boom)
    backend = DeepSeekBackend(model="m", api_key="sk-x", api_base="https://api.deepseek.com")
    with pytest.raises(LlmError) as excinfo:
        await backend.structured(NewsDigest, "s", "u")
    assert "[structured:NewsDigest]" in str(excinfo.value)


@pytest.mark.anyio
async def test_stub_backend_accepts_the_same_keyword_arguments():
    """Call sites pass ``stage=``; the offline stub must not break on it."""

    stub = StubBackend()
    assert isinstance(await stub.text("s", "u", stage="general_chat"), str)
    digest = await stub.structured(NewsDigest, "s", "材料：```json\n{}\n```", stage="news_digest")
    assert isinstance(digest, NewsDigest)
