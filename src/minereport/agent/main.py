"""``minereport-agent`` CLI: single-shot daily report plus multi-turn chat."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

DEFAULT_QUERY = "给我生成一份关于 Pilbara 锂矿的今日简报"
CHAT_HELP = """命令：
  /state   查看当前会话的意图、槽位与任务状态
  /tasks   列出该会话的全部任务
  /cancel  取消所有未完成任务
  /exit    退出（会话与任务已持久化，可用同一 session_id 恢复）
  /help    显示本帮助
直接输入自然语言即可继续对话。"""


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    from ..common import config

    load_dotenv(config.PROJECT_ROOT / ".env", override=False)


async def run_agent(
    query: str,
    *,
    days: int = 7,
    require_live: bool = False,
    llm_mode: str = "auto",
    out_dir: Path | None = None,
    tools: dict | None = None,
    backend=None,
) -> dict:
    """Run the daily-report workflow end to end and return the final state."""

    from ..common.provenance import utc_now_iso
    from .graph import build_graph, collect_provenance, live_gate_failures
    from .llm import build_backend
    from .mcp_client import open_tools
    from .state import blank_state

    _load_env()
    notes: list[str] = []
    if backend is None:
        # --require-live is the acceptance mode: a real model is mandatory there,
        # and an explicit `--llm deepseek` must never quietly become the stub.
        backend, notes = build_backend(
            llm_mode, require_real=require_live or llm_mode == "deepseek"
        )
    if tools is None:
        tools = await open_tools()

    graph = build_graph(tools, backend)
    state = blank_state(query, days=days, require_live=require_live, llm_mode=backend.mode)
    state["out_dir"] = out_dir
    state["errors"] = list(notes)
    state["retrieved_at"] = utc_now_iso()

    result = await graph.ainvoke(state)
    result["provenance_map"] = collect_provenance(result)
    result["live_gate_failures"] = live_gate_failures(result) if require_live else []
    return result


def _build_backend(args):
    from .llm import build_backend

    return build_backend(args.llm, require_real=args.require_live or args.llm == "deepseek")


def _print_state(store, session_id: str, user_id: str) -> None:
    from .task.manager import TaskManager

    manager = TaskManager(store)
    tasks = manager.all_for_session(session_id, user_id)
    print(f"session_id : {session_id}")
    print(f"user_id    : {user_id}")
    print(f"tasks      : {len(tasks)}")
    for task in tasks:
        missing = "、".join(task.get("missing_slots") or []) or "无"
        print(
            f"  - task_id={task['task_id']} intent={task['intent']} status={task['status']}"
            f" suspended={task['suspended']} missing={missing}"
        )
        if task.get("slots"):
            print(f"      slots={json.dumps(task['slots'], ensure_ascii=False)}")


async def _run_chat(args) -> int:
    from .conversation import run_turn
    from .llm import LlmError
    from .mcp_client import open_tools
    from .session.manager import SessionManager, resolve_user_id
    from .session.store import Store

    _load_env()
    store = Store()
    try:
        user_id, notes = resolve_user_id(args.user_id, switch=args.switch_user, store=store)
    except Exception as exc:  # noqa: BLE001
        print(f"身份校验失败：{exc}", file=sys.stderr)
        return 4
    for note in notes:
        print(f"[身份] {note}", file=sys.stderr)

    session_manager = SessionManager(store, persist=args.persist)
    session_id = session_manager.ensure_session(args.session_id, user_id)
    print(f"[会话] user_id={user_id} session_id={session_id} persist={args.persist}")

    if args.state:
        _print_state(store, session_id, user_id)
        await session_manager.aclose()
        return 0

    if args.cancel:
        from .task.manager import TaskManager

        manager = TaskManager(store)
        pending = manager.resumable(session_id, user_id)
        for task in pending:
            manager.cancel(task["task_id"], "CLI --cancel")
        print(f"已取消 {len(pending)} 个未完成任务。")
        return 0 if pending else 0

    try:
        backend, notes = _build_backend(args)
    except LlmError as exc:
        print(f"LLM FAILURE: {exc}", file=sys.stderr)
        return 3
    for note in notes:
        print(f"[模型] {note}", file=sys.stderr)
    tools = await open_tools()

    async def one_turn(text: str) -> dict:
        return await run_turn(
            store=store,
            session_manager=session_manager,
            tools=tools,
            backend=backend,
            user_id=user_id,
            session_id=session_id,
            request_id=args.request_id or str(uuid.uuid4()),
            text=text,
            require_live=args.require_live,
            out_dir=Path(args.out_dir).expanduser().resolve() if args.out_dir else None,
            persist=args.persist,
        )

    if not args.chat:
        try:
            result = await one_turn(args.query)
        except LlmError as exc:
            print(f"LLM FAILURE — 真实模型调用失败：{exc}", file=sys.stderr)
            return 3
        print(result.get("reply", ""))
        if args.json:
            print(json.dumps(
                {
                    "session_id": session_id,
                    "task_id": result.get("task_id"),
                    "intent": result.get("intent"),
                    "turn_action": result.get("turn_action"),
                    "task_status": result.get("task_status"),
                    "slots": result.get("slots"),
                    "missing_slots": result.get("missing_slots"),
                },
                ensure_ascii=False,
                indent=2,
            ))
        return 0

    print(CHAT_HELP)
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break
        if line == "/help":
            print(CHAT_HELP)
            continue
        if line == "/state":
            _print_state(store, session_id, user_id)
            continue
        if line == "/tasks":
            _print_state(store, session_id, user_id)
            continue
        if line == "/cancel":
            from .task.manager import TaskManager

            manager = TaskManager(store)
            pending = manager.resumable(session_id, user_id)
            for task in pending:
                manager.cancel(task["task_id"], "chat /cancel")
            print(f"已取消 {len(pending)} 个未完成任务。")
            continue

        try:
            result = await one_turn(line)
        except LlmError as exc:
            print(f"[LLM 失败] {exc}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 - the REPL must not die mid-session
            print(f"[错误] {type(exc).__name__}: {exc}", file=sys.stderr)
            print("      会话与已保存的任务未受影响，可直接重试。", file=sys.stderr)
            continue
        print(f"Agent> {result.get('reply', '')}")
        print(
            f"      [intent={result.get('intent')} action={result.get('turn_action')} "
            f"task={(result.get('task_id') or '-')[:8]} status={result.get('task_status')} "
            f"missing={result.get('missing_slots')}]",
            file=sys.stderr,
        )
    await session_manager.aclose()
    print(f"会话已保存：session_id={session_id}（用 --session-id 恢复）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minereport-agent",
        description="Mining-rights daily report agent; `--chat` for multi-turn conversation.",
    )
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY, help="natural language request")
    parser.add_argument("--days", type=int, default=7, help="news window in days (default: 7)")
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="fail if any required dataset is not live (no fixture may stand in)",
    )
    parser.add_argument(
        "--llm",
        choices=["auto", "deepseek", "stub"],
        default="auto",
        help="auto=DeepSeek when DEEPSEEK_API_KEY is set, otherwise an explicit stub",
    )
    parser.add_argument("--out-dir", default=None, help="override the reports directory")
    parser.add_argument("--json", action="store_true", help="print a JSON summary as well")
    parser.add_argument("--quiet", action="store_true", help="do not print the markdown to stdout")
    # conversational layer
    parser.add_argument("--chat", action="store_true", help="start a multi-turn conversation")
    parser.add_argument(
        "--session-id",
        default=os.environ.get("MINEREPORT_SESSION_ID") or None,
        help="resume this session (UUID); also reads MINEREPORT_SESSION_ID",
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get("MINEREPORT_USER_ID") or None,
        help="local user label (NOT authentication); also reads MINEREPORT_USER_ID",
    )
    parser.add_argument(
        "--switch-user",
        action="store_true",
        help="explicitly switch the persisted local user_id (refused otherwise)",
    )
    parser.add_argument("--request-id", default=None, help="client retry id for this request")
    parser.add_argument("--state", action="store_true", help="print session/task state and exit")
    parser.add_argument("--cancel", action="store_true", help="cancel pending tasks and exit")
    parser.add_argument(
        "--persist",
        choices=["sqlite", "memory"],
        default="sqlite",
        help="conversation store: sqlite (default) or memory",
    )
    return parser


def _ensure_piped_utf8() -> None:
    """Piped stdin/stdout/stderr use UTF-8 so `docker compose run < file` works.

    Interactive consoles are left alone for input: Windows already hands Python
    Unicode there, and reconfiguring would risk breaking typed non-ASCII input.
    Redirected streams are forced to UTF-8 because they would otherwise fall back
    to the locale code page (cp936) and turn Chinese diagnostics into mojibake in
    the captured logs.
    """

    try:
        if not sys.stdin.isatty():
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None and not sys.stderr.isatty():
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - older runtimes
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_piped_utf8()
    args = build_parser().parse_args(argv)

    conversational = (
        args.chat or args.state or args.cancel or args.session_id or args.user_id or args.request_id
    )
    if conversational:
        return asyncio.run(_run_chat(args))

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    from .llm import LlmError

    try:
        result = asyncio.run(
            run_agent(
                args.query,
                days=args.days,
                require_live=args.require_live,
                llm_mode=args.llm,
                out_dir=out_dir,
            )
        )
    except LlmError as exc:
        print("LLM FAILURE — 真实模型调用失败，未生成报告（不会降级为 stub）：", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 3

    summary = {
        "query": result.get("query"),
        "topic": result.get("topic"),
        "commodity": result.get("commodity"),
        "llm_mode": result.get("llm_mode"),
        "report_path": result.get("report_path"),
        "provenance": result.get("provenance_map"),
        "live_gate_failures": result.get("live_gate_failures"),
        "errors": result.get("errors"),
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            print(result.get("report_markdown", ""))
        print("\n" + "=" * 72)
        print(f"report : {result.get('report_path')}")
        print(f"llm    : {result.get('llm_mode')}")
        print("provenance:")
        for dataset, info in (result.get("provenance_map") or {}).items():
            print(f"  - {dataset}: {info.get('provenance')} ({info.get('source')})")
        for failure in result.get("live_gate_failures") or []:
            print(f"LIVE GATE FAILURE: {failure}")
        for error in result.get("errors") or []:
            print(f"note/error: {error}")

    if args.require_live and result.get("live_gate_failures"):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())