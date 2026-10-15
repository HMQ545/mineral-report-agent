"""LLM policy: JSON-mode wiring, and no silent stub fallback in real mode."""

from __future__ import annotations

import pytest

from minereport.agent import llm


def test_real_mode_without_a_key_raises_instead_of_falling_back(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(llm.LlmUnavailableError):
        llm.build_backend("deepseek", require_real=True)
    with pytest.raises(llm.LlmUnavailableError):
        # --require-live also demands a real model
        llm.build_backend("auto", require_real=True)


def test_explicit_stub_mode_is_allowed(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    backend, notes = llm.build_backend("stub")
    assert backend.mode == "stub"
    assert notes


def test_auto_mode_without_key_labels_the_fallback(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    backend, notes = llm.build_backend("auto")
    assert backend.mode == "stub"
    assert any("DEEPSEEK_API_KEY" in note for note in notes)


def test_deepseek_backend_keeps_thinking_disabled_without_a_global_json_mode(monkeypatch):
    """Thinking stays off (the tool_choice fix) but JSON must not be global.

    The previous wiring put ``response_format`` on the shared client, so
    ``--chat`` + "你好" requested json_object with a prompt that never says
    "json" and DeepSeek answered 400.
    """

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    backend, notes = llm.build_backend("deepseek", require_real=True)

    assert backend._llm.extra_body == {"thinking": {"type": "disabled"}}
    assert backend._llm.model_name == "deepseek-flash"
    assert "response_format" not in (getattr(backend._llm, "model_kwargs", None) or {})
    assert backend._json_llm is not backend._llm, "JSON mode must live on a separate runnable"
    assert any("文本模式" in note for note in notes)


def test_structured_output_does_not_use_tool_calling():
    from unittest.mock import patch

    from langchain_deepseek import ChatDeepSeek

    with patch.object(ChatDeepSeek, "with_structured_output") as patched:
        llm.DeepSeekBackend(
            model="deepseek-flash", api_key="sk-x", api_base="https://api.deepseek.com"
        )
        patched.assert_not_called()


def test_parse_json_object_accepts_fenced_and_plain_json():
    assert llm.parse_json_object('{"a": 1}') == {"a": 1}
    assert llm.parse_json_object('```json\n{"a": 2}\n```') == {"a": 2}
    assert llm.parse_json_object('前言 {"a": 3} 后记') == {"a": 3}


def test_parse_json_object_rejects_garbage():
    with pytest.raises(llm.LlmError):
        llm.parse_json_object("sorry, I cannot help with that")
    with pytest.raises(llm.LlmError):
        llm.parse_json_object("")
    with pytest.raises(llm.LlmError):
        llm.parse_json_object("[1, 2, 3]")


def test_schema_instruction_spells_out_the_schema_in_chinese():
    text = llm.schema_instruction(llm.NewsDigest)
    assert "JSON" in text
    assert "summary" in text
    assert "中文" in text