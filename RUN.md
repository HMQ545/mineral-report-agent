# RUN.md — 5 分钟跑起来

矿权日报 Agent：3 个 MCP Server（`mining-news-mcp` / `mineral-pdf-mcp` / `lme-price-mcp`）
\+ 1 个 LangGraph Agent Client（DeepSeek），输出带完整数据溯源的 Markdown 日报。

---

## 0. 前置条件（必读）

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows | 命令以 PowerShell 给出；下文用 `<项目根目录>` 代指本仓库路径（例如 `D:\Python\mineral-report-agent`，可放在任意位置） |
| Python | 3.12（本机为 3.12.4） | `uv` 会自动复用系统解释器 |
| uv | 0.11.x | `uv --version` |
| 网络 | 能访问 Google News RSS、mining.com、minedocs.com、`hq.sinajs.cn` | 这是真实数据源，必须联网 |
| DeepSeek Key | **可选** | 不配置时自动使用 stub 后端，并在报告中明确标注 |
| Docker Desktop | **可选**（仅 Gate 3） | 容器也需要能出网；若宿主走本地代理，请在 Docker Desktop → Settings → Resources → Proxies 里配置同一个代理地址 |

> `.env` 由你从 `.env.example` 复制并填写；仓库中不含任何密钥。

---

## 1. 最快路径：本地一条命令生成日报

```powershell
cd <项目根目录>
uv sync
Copy-Item .env.example .env          # 如需真实 LLM，把 DEEPSEEK_API_KEY 填进去
uv run minereport-agent --require-live "给我生成一份关于 Pilbara 锂矿的今日简报"
```
 uv run minereport-agent --chat       #进行多轮对话，可以自由切换任务，可以进行通用对话，可以使用会话id进行会话恢复
预期结果：

- 终端打印完整 Markdown 日报，并输出每个数据集的 provenance；
- 日报写入 `reports\2026-10-14-pilbara-lithium-brief.md`；
- `--require-live` 下若任一必需数据集不是 `live`，进程以退出码 `2` 结束并打印 `LIVE GATE FAILURE: ...`。

常用变体：

```powershell
# 只输出 JSON 摘要（便于脚本化）
uv run minereport-agent --require-live --json --quiet "给我生成一份关于 Pilbara 锂矿的今日简报"

# 演示模式：允许离线样例兜底，但报告中会带“非实时数据”徽标
uv run minereport-agent --llm stub "Pilbara 锂矿今日简报"

# 单独运行某个 MCP Server（stdio）
uv run minereport-news  --transport stdio
uv run minereport-pdf   --transport stdio
uv run minereport-price --transport stdio
```

Agent 默认行为：**未设置 `MINEREPORT_*_URL` 时，自动以 stdio 子进程方式拉起三个 MCP Server**，
无需手动启动任何服务。

---

## 2. 接 Claude Desktop / Cursor 验证（Gate 4）

把下面生成出来的配置合并进客户端的 MCP 配置：

- Claude Desktop：`%APPDATA%\Claude\claude_desktop_config.json`
- Cursor：`%USERPROFILE%\.cursor\mcp.json`

仓库只提供**不含任何绝对路径**的模板 [mcp-config.stdio.example.json](mcp-config.stdio.example.json)（用 `<PROJECT_PATH>` 占位），一键生成本机可用配置：

```powershell
# 在项目根目录执行
uv run python scripts/generate_mcp_config.py             # 生成 mcp-config.json + mcp-config.stdio.local.json
uv run python scripts/generate_mcp_config.py --stdout     # 只打印，便于手工粘贴
uv run python scripts/generate_mcp_config.py --cursor     # 顺便合并进 %USERPROFILE%\.cursor\mcp.json
uv run python scripts/generate_mcp_config.py --claude     # 顺便合并进 %APPDATA%\Claude\claude_desktop_config.json

# 自检：用配置里的 command+args 真实启动三个 Server 并列出工具
uv run python scripts/verify_mcp_config.py                # -> PASS: all 3 servers booted ...
```

默认会写两个**内容完全相同**的文件：`mcp-config.json`（交付清单要求的文件名）与 `mcp-config.stdio.local.json`，两者都在 `.gitignore` 中，个人路径不会进 GitHub。HTTP 形态单独放在 [mcp-config.http.json](mcp-config.http.json)（先 `docker compose up -d` 起服务再用）。脚本把渲染结果再 `json.loads` 反解析一次，因此 Windows 反斜杠一定被正确转义，否则直接报错退出；同时会校验三个 Server 的名称、`command` 与 `--transport stdio` 没有被改动。

手工粘贴时，把生成文件内容整体贴到：

- Cursor：`%USERPROFILE%\.cursor\mcp.json`（或项目内 `.cursor/mcp.json`，那种情况下也可直接用 `${workspaceFolder}`）
- Claude Desktop：`%APPDATA%\Claude\claude_desktop_config.json`

生成的条目保持原有接线：`command: uv` + `--directory <你的检出路径> --no-sync minereport-<x> --transport stdio`。
期望看到 5 个工具：`search`、`fetch_article`（news）、`extract_resources`、`list_sources`（pdf）、
`get_price`、`get_trend`、`list_commodities`（price）。

---

## 3. Docker Compose 四服务（Gate 3）

```powershell
cd <项目根目录>
docker compose up --build --abort-on-container-exit --exit-code-from agent
```

> **能直连互联网的机器：上面这条命令直接用。** 宿主机走本地代理时（容器不能直连互联网），
> 只需在执行前导出下面三个变量——`docker-compose.yml` 会把它们作为构建参数传进镜像，
> **Gate 3 命令本身一字不改**：
>
> ```powershell
> $env:HTTP_PROXY='http://192.168.65.254:7899'    # Docker 网关 IP，见下方查询方法
> $env:HTTPS_PROXY='http://192.168.65.254:7899'
> $env:NO_PROXY='localhost,127.0.0.1'
> docker compose up --build --abort-on-container-exit --exit-code-from agent
> ```
>
> 运行态容器走 `host-gateway` 别名（compose 已内置 `extra_hosts`），代理写在 `docker.env`
> （`HTTP_PROXY=http://host.docker.internal:7899`）；构建容器解析不了 `host.docker.internal`，
> 必须用 Docker 网关 IP。网关 IP 可用
> `docker run --rm --add-host=host.docker.internal:host-gateway python:3.12-slim python -c "import socket;print(socket.gethostbyname('host.docker.internal'))"` 查得。

- 3 个 MCP Server：`restart: unless-stopped`，以 `--transport streamable-http` 监听 `8101/8102/8103`，
  各自暴露 `GET /healthz`（同时验证 MCP 初始化与工具发现），并有 Docker healthcheck。
- `agent`：`restart: "no"`，等三个服务 `service_healthy` 后运行一次，生成报告后退出；
  报告通过 `./reports:/app/reports` 落到宿主机。
- 镜像内使用 `uv sync --frozen --no-dev` 安装 `uv.lock` 锁定的依赖。

健康检查单独看：

```powershell
curl http://127.0.0.1:8101/healthz      # 容器内/宿主端口映射后
```

返回示例：

```json
{"service":"mining-news-mcp","status":"ok","ready":true,
 "tools":["search","fetch_article"],"tool_count":2,
 "version":"0.1.0","mcp_initialized":true}
```

> Docker 构建与运行都需要容器出网（拉依赖 + 取真实数据）。若宿主机通过本地代理上网，
> 必须让 Docker Desktop 使用同一个代理，否则容器内 HTTPS 会失败。

---

## 8. 多轮对话（多意图 Agent）

```powershell
# 本地：进入持续对话（默认 SQLite 持久化）
uv run minereport-agent --chat

# 恢复一个已有会话（也可用环境变量 MINEREPORT_SESSION_ID）
uv run minereport-agent --chat --session-id <SESSION_ID>

# 查看/取消当前会话的任务，以及本地用户身份
uv run minereport-agent --state  --session-id <SESSION_ID>
uv run minereport-agent --cancel --session-id <SESSION_ID>
```

会话内命令：`/state` 查看意图/槽位/任务状态，`/tasks` 列出任务，`/cancel` 取消未完成任务，`/exit` 退出。

**Docker 交互模式（独立 profile，不影响 Gate 3）**：

```powershell
docker compose --profile chat run --rm -T agent-chat                  # 新会话
$env:SESSION_ID="<SESSION_ID>"; docker compose --profile chat run --rm -T agent-chat  # 恢复会话
# -T = 不要分配 TTY，便于管道输入 / CI；在真实终端里直接手打时可以去掉 -T
```

`data/sessions/` 通过卷持久化：`user.json`（本地身份）+ `agent_state.sqlite`（LangGraph Checkpointer，`thread_id = session_id`）+ `agent_business.sqlite`（sessions / tasks / requests / effects，另有 `effects/` 放大结果文件）。容器重建后同一 `session_id` 仍可恢复；该目录已在 `.gitignore` 中忽略。

**身份规则**：首次运行生成并保存 `user_id`；`--user-id` 与本地记录不一致时**默认拒绝**，需要显式切换时使用 `--switch-user`。`--user-id` 只是本地标识，**不是身份认证**。

**意图与任务**：支持 `daily_report / news_search / resource_query / price_query / trend_query / general_chat / unknown` 七类；缺槽会追问（"锂价"会追问碳酸锂还是锂辉石精矿）；补槽沿用同一 `task_id`；普通聊天不调用任何 MCP；`price_query` 在用户问"走势"时可兼容升级为 `trend_query`。

---
## 4. 验收 Gate 一览

| Gate | 命令 | 通过标准 |
| --- | --- | --- |
| 0 | `uv run python scripts/verify_sources.py` | 注册表内每个真实 PDF 源 HTTP 200 + `%PDF-` + sha256 一致 + 抽取到资源量分类 |
| 1 | `uv run pytest -m "not live"` | 离线单测 / 离线 E2E 全绿 |
| 2 | `uv run pytest -m live` | 真实调用新闻 / PDF / 行情，逐项断言 `provenance == live` |
| 3 | `docker compose up --build --abort-on-container-exit --exit-code-from agent` | 3 服务 healthy → agent 退出码 0 → 宿主机 `reports/` 出现报告 |
| 4 | 运行 `scripts/generate_mcp_config.py` 并把生成结果接入客户端 | 客户端发现全部工具并成功调用 |
| 并发自检（可选） | `uv run python scripts/verify_concurrent_db.py 6 30` | 多个真实进程同时写同一 session 与同一个 state 文件：无丢失/无覆盖、`integrity_check=ok`、同一步骤仅 1 个领取者 |

实际执行结果与失败记录见 [docs/VERIFICATION.md](docs/VERIFICATION.md)。

---

## 10. LLM 调用模式（2026-10-15 修复）

- **结构化调用**（意图识别、任务匹配、新闻摘要、风险分析）使用 DeepSeek JSON 模式 + Pydantic 校验，消息里始终带明确的 JSON 输出指令。
- **普通聊天**（`general_chat`，例如输入"你好"）使用纯文本模式，**不携带** `response_format`——曾经因为把 JSON 模式设在共享客户端上，导致 `--chat` 报 `Prompt must contain the word 'json'` 400。
- thinking 保持关闭（避免 tool_choice 400）；LLM 失败时错误信息会带调用阶段（`[general_chat]`、`[structured:NewsDigest]` 等）并对 API Key 脱敏。
- 自检：`uv run --no-sync pytest tests/test_llm_modes.py -q`

---

## 9. 数据质量校验与证据分级（v2 行为）

- **新闻统计**：报告 §1.3 输出"新闻处理流水"表，互斥分类（公司 + 行业 + 无关）之和等于去重后条数，并单列"因展示上限未展示"；数字对不上会被质量门禁拦下。
- **证据分级**：每条新闻标注 `证据：官方公告 / 完整正文 / RSS 摘要 / 仅标题` 与稳定 `news_id`；标题/摘要级记录不会被当作经营事实依据。
- **缺失行情**：严格 Live 模式（`--require-live`）下无可验证报价的商品显示 `DATA_MISSING`（`provenance=—`），数值在进入风险分析**之前**就被剔除；演示模式仍会显示 fixture 并明确标注。
- **风险结论可追溯**：报告 §5.3 是"结论—证据对照"表（`claim_id` / 数值 / 证据 ID）；数值会按证据重新计算校验，模型编造或口径混用的结论会被自动剔除并记入 §8。
- **资源量时效**：§2 分列 `effective_date` / `announced_date` / `fetched_at` 与"距今 N 天（历史数据）"。
- **质量门禁失败**：strict-live 下无法修复的数据口径问题会写入 `live_gate_failures` 并让 `minereport-agent` 以退出码 2 结束（报告仍会落盘，便于排查）。
- **正文抓取预算**：最多 8 篇、并发 3、单篇 45s、整体 120s；超预算记 `budget_exhausted` 且不再发请求。
- 自检：`uv run python scripts/verify_report.py`（22 项检查）、`uv run python scripts/verify_concurrent_db.py`（多进程 SQLite 并发）。

---

## 5. 数据源与可信度约定

| 用途 | 真实来源 | 备注 |
| --- | --- | --- |
| 新闻 | Google News RSS（时新性）+ mining.com / australianmining.com.au 直源（真实正文 URL） | Bing News RSS 为兜底 |
| 矿产资源量 / 矿石储量 | **Pilbara Minerals 2023-08-24 披露原文**（minedocs.com 镜像，sha256 入库） | 官方域名本网络 403，镜像托管情况在报告中显式标注 |
| NI 43-101 兼容性 | Lithium Ionic 的 NI 43-101 技术报告 | 仅验证解析器，不作为任何项目的资源量来源 |
| LME 铜/铝/镍/锌 | 新浪外盘（`hf_CAD` 等）**第三方转述** | 报告中标注 `venue_official=false`，不声称来自 LME 官方 API |
| 碳酸锂 | 广期所连续合约（`nf_LC0`） | 下游参考价，不是 Pilbara 锂辉石精矿销售价 |
| 锂辉石精矿 | 无免费真实源 → **离线样例** | live 模式不作为真实行情 |

provenance 四态：`live`（本次真实获取）/ `cached`（真实获取后的本地缓存）/
`fixture`（仓库离线样例）/ `synthetic`（程序合成）。报告顶部有来源与可信度表，
非 live 段落一律带“非实时数据”徽标。

---

## 6. 排查表

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `LIVE GATE FAILURE: news ...` | 新闻源不可达或窗口内无条目 | 检查网络；`--days` 放宽；确认不是 `provenance=fixture` |
| `LIVE GATE FAILURE: reserves ...` | PDF 源被墙 / 文档改版导致 sha256 变化 | 重新跑 `scripts/verify_sources.py --update` 并检查注册表 |
| `NOT_FOUND ... latest price is deliberately NOT substituted` | 查询了非交易日 | 这是预期行为，换一个真实交易日 |
| 报告中出现“非实时数据”徽标 | 该数据集降级为 cached/fixture | 演示模式正常；严格验收请加 `--require-live` |
| 端口 8101/8102/8103 被占用 | 本地已有服务 | 换 `--port`，或在 compose 中改端口并同步 `MINEREPORT_*_URL` |
| Docker 构建卡在 `pip install uv` / `uv sync` | 容器无法访问 PyPI | 给构建显式传代理：`docker compose build --build-arg HTTP_PROXY=... --build-arg HTTPS_PROXY=...`（见第 3 节） |
| 容器内取数全部失败 | 容器没有出口网络 | 让 Docker Desktop 使用与宿主相同的代理 |

---

## 7. 目录速览

```
src/minereport/common/   共享层：provenance / http(SSRF) / cache / fixtures / cli / health
src/minereport/news/     mining-news-mcp
src/minereport/pdf/      mineral-pdf-mcp（解析器 + 安全 + 源注册表）
src/minereport/price/    lme-price-mcp
src/minereport/agent/    LangGraph 工作流 + DeepSeek + 报告渲染
data/sources/            真实源注册表（含 sha256）
data/fixtures/           离线样例（新闻 / 价格 / 合成 PDF）
scripts/                 verify_sources.py（Gate 0）、build_fixtures.py
reports/                 生成的 Markdown 日报
docs/VERIFICATION.md     实际运行证据
```

