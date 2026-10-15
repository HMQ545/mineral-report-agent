"""Ordinary chat: DeepSeek only, never an MCP tool."""

from __future__ import annotations

CHAT_SYSTEM = (
    "你是矿业智能助手，也可以回答编程与通用知识问题。"
    "当用户只是闲聊或问通用问题时，直接用中文回答，不要调用任何矿业数据工具，"
    "也不要编造行情、储量或新闻数据。回答简洁、具体。"
)


def chat_prompt(text: str, *, history_summary: str = "", recent: list[str] | None = None) -> str:
    parts = []
    if history_summary:
        parts.append(f"会话摘要：{history_summary}")
    if recent:
        parts.append("最近对话：\n" + "\n".join(recent))
    parts.append(f"用户：{text}")
    return "\n\n".join(parts)


__all__ = ["CHAT_SYSTEM", "chat_prompt"]