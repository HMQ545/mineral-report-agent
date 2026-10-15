# VERIFICATION.md — 实际运行证据

执行环境：Windows，项目根目录（Gate 0/1/2/3 执行时为 `D:\Python\1`，2026-10-15 已重命名为 `D:\Python\mineral-report-agent`），Python 3.12.4，uv 0.11.25，
Docker Compose v2.34.0-desktop.1（Docker 引擎 28.0.4）。
执行日期：**2026-10-14**（Asia/Shanghai）。

本文件记录**实际运行过的命令与结果**，包括失败与未完成项。没有把“代码已写完”当成“测试通过”。

---

## 项目重命名记录（2026-10-15）

项目根目录由 `D:\Python\1` 重命名为 `D:\Python\mineral-report-agent`。

- **为什么不是一次性 `Move-Item`**：根目录被当时会话的宿主进程作为工作目录占用，整目录改名返回
  "另一个进程正在使用此文件"；而 `reports`、`data`、`docs`、`src`、`tests`、`scripts`、`.venv`、`uv.lock`
  等**每个子项的改名都正常**，说明锁只在根目录本身（Codex 会话与 Cursor 工作区先后被排查，Cursor 侧已确认释放）。
- **实际做法**：新建目标目录后，把 21 个顶层条目**逐项同盘移动**（改名语义，不复制数据、不改时间戳）。
  校验结果：旧目录剩余条目 **0**，新目录顶层条目 **21**、文件 **6605** 个，失败项 **0**。
- **旧目录现状**：`D:\Python\1` 已为空壳，但被会话占用暂不能删除；结束本会话后执行
  `Remove-Item -LiteralPath 'D:\Python\1'` 即可清掉。
- **虚拟环境**：`.venv` 里的 `pyvenv.cfg` 与 `Scripts/*.exe` 记录绝对路径，改名后必然失效，
  故删除重建并重新 `uv sync`（77 个包；editable 安装指向 `file:///D:/Python/mineral-report-agent`）。
- **历史日志**：`docs/gate*.log` 是重命名前执行的真实输出，其中的 `D:\Python\1` **按原样保留**
  （它们是证据，不应被改写）。正文中的路径说明已更新为当前路径。
- **MCP 配置（可移植性）**：仓库只保留不含任何绝对路径的模板 `mcp-config.stdio.example.json`
  （`<PROJECT_PATH>` 占位），由 `scripts/generate_mcp_config.py` 渲染出本机可用的
  `mcp-config.stdio.local.json`；生成物已加入 `.gitignore`。脚本渲染后会用 `json.loads` 反解析校验
  JSON 合法性（Windows 反斜杠转义由此保证），并校验三个 Server 的名称、`command` 与 `--transport stdio`
  未被改动。`mcp-config.http.json` 不含路径，保持不变。
## MCP 配置可移植性 ✅（2026-10-15）

公开仓库不再包含任何本机绝对路径，改为“模板 + 生成器”：

| 文件 | 作用 | 是否入库 |
| --- | --- | --- |
| `mcp-config.stdio.example.json` | 可移植模板，`args` 里用 `<PROJECT_PATH>` 占位 | ✅ 入库 |
| `scripts/generate_mcp_config.py` | 自动识别项目根目录并渲染本机配置 | ✅ 入库 |
| `mcp-config.stdio.local.json` | 渲染结果（含本机绝对路径） | ❌ 已加入 `.gitignore` |
| `mcp-config.http.json` | HTTP 传输示例，本身不含路径 | ✅ 入库 |

```powershell
uv run python scripts/generate_mcp_config.py             # 生成 mcp-config.stdio.local.json
uv run python scripts/generate_mcp_config.py --stdout     # 只打印，便于手工粘贴
uv run python scripts/generate_mcp_config.py --cursor     # 合并进 %USERPROFILE%\.cursor\mcp.json
uv run python scripts/generate_mcp_config.py --claude     # 合并进 %APPDATA%\Claude\claude_desktop_config.json
```

生成器的三重保险，全部实测通过：

1. **JSON 合法性**：渲染后用 `json.loads` 反解析，不合法即非零退出；Windows 反斜杠由 `json.dumps` 转义，
   测试断言文件中存在 `\\` 转义且解析回来的路径与输入完全相等。
2. **接线不变**：生成后校验三个 Server 名称齐全、`command == "uv"`、`--directory` 确为目标路径、
   `--transport stdio` 未被改动；模板缺占位符时明确报错（有对应测试）。
3. **真的能启动**：对三个 Server 做 stdio 握手 + `list_tools`。

不同项目路径下的生成验证（参数化测试，含空格与中文路径）：

| 输入 `--project-root` | 断言 |
| --- | --- |
| `C:\work\other-project` | 路径原样嵌入、JSON 可解析、接线不变 |
| `D:\My Projects\mineral report agent` | 同上，并额外断言转义正确 |
| `E:\中文目录\矿权日报` | 同上（非 ASCII 路径） |

**用生成出来的配置条目做真实启动校验**（严格按客户端方式执行 `uv run --directory <path> --no-sync minereport-<x> --transport stdio`）：

```
OK   mining-news-mcp    cmd=uv -> ['search', 'fetch_article']
OK   mineral-pdf-mcp    cmd=uv -> ['extract_resources', 'list_sources']
OK   lme-price-mcp      cmd=uv -> ['get_price', 'get_trend', 'list_commodities']
```

`--cursor` 与 `--claude` 合并逻辑均实测：前者写入真实的 `%USERPROFILE%\.cursor\mcp.json`（保留已有其他 server），
后者在沙箱路径下验证通过（未触碰真实 Claude 安装目录）。docker-compose 的构建上下文、挂载目录与
stdio/HTTP 双传输未改动；复跑 Gate 1（53 passed）、Gate 2（8 passed）、Docker Gate 3（agent 退出码 0）均通过。

---
## 2026-10-15 针对性修复记录 ✅

### 1. DeepSeek 400：`Thinking mode does not support this tool_choice`

**根因（精确定位到函数）**：`src/minereport/agent/llm.py::DeepSeekBackend.structured()` 调用
`self._llm.with_structured_output(schema)`。`langchain-core` 的默认实现走 **tool calling**，会发送
**强制 `tool_choice`**；而本机生效的模型是 thinking 模型（`DEEPSEEK_MODEL=deepseek-flash`），
DeepSeek 明确拒绝强制 tool choice，于是新闻与风险两步在拿到任何内容前就 400 了。

**证据**：用最小脚本复现同一错误；再做写法矩阵实测：

| 写法 | 结果 |
| --- | --- |
| `with_structured_output(schema)`（tool calling） | ❌ 400 `Thinking mode does not support this tool_choice` |
| `model_kwargs={"thinking": {"type": "disabled"}}` | ❌ `TypeError: Completions.create() got an unexpected keyword argument 'thinking'` |
| `extra_body={"thinking": {"type": "disabled"}}` + tool calling | ✅ 可用（schema 被遵守） |
| `response_format={"type": "json_object"}` + Pydantic 校验 | ✅ 可用 |

**修复**：
- `extra_body={"thinking": {"type": "disabled"}}` 明确关闭 thinking（分析任务不需要思维链）；
- 彻底不用 tool calling，改为 **JSON 输出**（`model_kwargs={"response_format": {"type": "json_object"}}`）
  + `schema_instruction()` 把 JSON Schema 写进提示词 + `parse_json_object()` 解析 + **Pydantic 校验**，
  校验失败自动带错误重试一次，仍失败则抛 `LlmError`；
- `graph.py::summarize_news` / `assess_risk`：`backend.mode == "deepseek"` 时**直接抛错**，不再回退
  stub / 空分析 / 固定文本；
- `build_backend(mode, require_real=...)`：`--require-live` 与 `--llm deepseek` 都要求真实模型，
  缺 key 直接抛 `LlmUnavailableError`；CLI 以退出码 3 报错且不生成报告。

### 2. 新闻相关性：把 "Pilbara" 当地名而不是公司

**根因**：`news/sources.py::select_items()` 只做关键词 token 匹配，`news/server.py::search()` 没有实体概念，
所以只要标题里有 "Pilbara"（一个**地区名**）就会收进来——力拓的 Pilbara 铁矿、以及名为
"Pilbara Gold (ASX:PGL)" 的金矿公司因此混入。

**修复**：
- 新增实体注册表 `data/sources/entities.json` 与 `src/minereport/news/entities.py`：
  公司证据仅来自**正式名称 / 别名 / 股票代码 / 项目名**（Pilbara Minerals Limited、ASX:PLS、PLS Group、
  Pilgangoora、Pilgan、Ngungaju…），**裸地名不算证据**；命中去重与排除词（rio tinto / iron ore /
  pilbara gold / asx:pgl …）的条目标为 `irrelevant`；非公司条目需满足行业词 + 语境词门槛才归为 `industry`。
- `news/server.py::search()` 改为实体感知：按公司正式名称、项目、股票代码分别检索 + 一条行业背景查询，
  返回 `company_items` / `industry_items` / `stats`，并按 URL+标题+来源去重。

### 3. PDF 汇总与附录明细混在一张表

**根因**：`pdf/extractor.py::parse_pdf()` 把全文所有分类行都收进 `categories`，于是第 56 页附录
（本身还带重复总计行 `Indicated 315.2` / `Inferred 76.6`）与第 2 页主表混在一起相加。

**修复**：`_assign_statement_scope()` 按「每一组最早出现表格的页」判定主表（`project_summary`），
其余为 `appendix_detail`；`pdf/server.py` 再按 `(category, tonnes, grade)` **跨作用域去重**
（真实 Pilbara 文档剔除了 2 行与主表完全相同的总计行），并分别返回 `mineral_resources` / `ore_reserves`
与 `appendix`；报告只把主表作为正式数据，附录单独展示且明确标注"不可与汇总值相加"。

### 4. require-live 下仍出现 fixture 价格

**根因**：`agent/report.py` 渲染 `optional_market` 时不看 provenance，锂辉石精矿的 fixture 价格
（1180 USD/t）被写进严格 live 报告。

**修复**：新增 `_mask()`，严格 live 模式下任何非 live 数据集一律渲染为 `DATA_MISSING` 并说明原因；
锂辉石行现在显示 `DATA_MISSING`，报告内不再出现该模拟价格。演示模式仍可展示，但带 `fixture` 标记。

### 5. Markdown 质量

- 新闻按"公司直接相关 / 锂行业背景"分节（1.1 / 1.2），每条新闻标题、来源、情绪、要点、链接各占一行；
- 链接统一为 `[标题](URL)`，不再把裸 URL 粘到下一段标题上；
- 运行异常与备注集中到 **§8 运行诊断**，不再混入新闻；
- 可信度表备注截断到 120 字符、单元格转义 `|`；来源链接按 URL 去重；
- 资源量/储量表格补齐「矿石量 / Li₂O 品位 / 含 Li₂O 量」与单位，并新增时效说明。

### 本轮改动的文件

| 文件 | 关键改动 |
| --- | --- |
| `src/minereport/agent/llm.py` | 关闭 thinking + JSON 输出 + Pydantic 校验 + `LlmError`/`LlmUnavailableError` |
| `src/minereport/agent/graph.py` | 真实模式失败即抛错；新闻公司/行业分组；正文优先抓取直源 |
| `src/minereport/agent/main.py` | `--require-live` ⇒ 强制真实模型；LLM 失败退出码 3 |
| `src/minereport/agent/prompts.py` | 中文输出与两类新闻的写作约束 |
| `src/minereport/agent/state.py` | 新增 `company_news` / `industry_news` |
| `src/minereport/agent/report.py` | 重写渲染：分节、严格 live 屏蔽、诊断独立、链接规范 |
| `src/minereport/agent/risk.py` | 汇总口径改用 `statement_scope` |
| `src/minereport/news/entities.py`（新增） | 实体分类、去重 |
| `data/sources/entities.json`（新增） | Pilbara Minerals 实体注册表（含混淆实体排除词） |
| `src/minereport/news/{sources,server}.py` | 多查询实体检索、公司/行业分类输出 |
| `src/minereport/pdf/{extractor,server}.py` | 主表 vs 附录分级、跨作用域去重、时效说明 |
| `scripts/verify_report.py`（新增） | 对生成报告做 16 项验收检查（不依赖测试是否通过） |
| `tests/test_news_entities.py`、`test_pdf_scope.py`、`test_report_quality.py`、`test_llm_strict.py`（新增） | 上述问题的回归测试 |
| `tests/test_live.py`（扩充） | 真实 DeepSeek 新闻/风险集成测试、实体过滤、严格 live 报告断言 |

### 实际执行的命令与结果

```powershell
uv run pytest -m "not live"          # 当时 89 passed, 11 deselected（见本文件最新一节：91 passed）
uv run pytest -m live                # 当时 10 passed, 1 skipped（该 skip 已在最新一轮修复，现为 12 passed, 0 skipped）
uv run minereport-agent --require-live --json --quiet "给我生成一份关于 Pilbara 锂矿的今日简报"
                                     # llm_mode=deepseek, live_gate_failures=[], 退出码 0
uv run python scripts/verify_report.py   # 16/16 checks passed
docker compose build ... && docker compose up --abort-on-container-exit --exit-code-from agent
                                     # 容器内 llm_mode=deepseek, agent-1 exited with code 0
```

真实 DeepSeek 调用的成功证据（不含密钥）：

```
tests/test_live.py::test_live_deepseek_news_digest_returns_chinese_and_valid_schema PASSED
tests/test_live.py::test_live_deepseek_risk_section_returns_chinese_and_valid_schema PASSED
```

报告 `reports/2026-10-15-pilbara-lithium-brief.md` 的验收结果（`scripts/verify_report.py`）：

```
[PASS] no 'Thinking mode' 400 text          [PASS] headline summary rows are unique
[PASS] news split into company/industry     [PASS] 315.2 / 76.6 appear in exactly one row
[PASS] company section free of confusables  [PASS] reserve rows are unique
[PASS] company evidence present             [PASS] appendix separated and labelled
[PASS] disclosure date caveat               [PASS] strict-live masks fixture price
[PASS] Chinese news summaries               [PASS] Chinese risk judgement
[PASS] markdown links well formed           [PASS] source links unique
[PASS] diagnostics outside news             [PASS] diagnostics section exists
16/16 checks passed
```

### 仍未完成 / 已知限制

- **锂辉石精矿报价**：无公开免费真实数据源，严格 live 模式按 `DATA_MISSING` 处理（不展示模拟价）。
- **新闻正文抓取**：本轮命中的条目多为 Google News 跳转链接，直源条目未进入前若干位，
  因此报告 §8 记录"本次仅使用 RSS 标题与链接"；1 个 live 用例因此跳过（有明确原因，未标记为通过）。
- **资源量时效**：正式报告使用 Pilbara Minerals 2023-08-24 披露（生效日 2023-06-30），
  报告中已明确标注"并非当前最新披露"。
- **模型名**：生效模型来自环境变量（本机为 `deepseek-flash`）。修复对 thinking 模型通用；
  若换成非 thinking 模型，关闭 thinking 的参数会被服务端忽略，JSON 输出链路不受影响。
## 2026-10-15 新闻正文抓取验证链路补齐 ✅

### 一、原始 Skip 的原因

`test_live_article_body_can_be_read_from_a_publisher_feed` 的跳过条件是"搜索结果里没有可抓取的直链"。
定位后确认**不是**网站拒绝访问，而是两个独立问题：

1. **搜索结果没有暴露直链**（主因）：`news/server.py::search()` 把 company 与 industry 两组都按发布时间
   排序后截断（20 / 10 条）。Google News 条目时间更新，直源条目（mining.com / australianmining.com.au，
   时间通常早 1-3 天）被挤出列表；同时当日直源 RSS 里的条目多为铜、稀土等非锂主题，被相关性规则判为无关。
   于是信封里一个可抓取 URL 都没有 → 测试按设计跳过。
2. **正文提取器在部分站点失效**（次因，实测发现）：`news/sources.py::extract_article()` 只用
   `//article | //main | //body` 选容器。australianmining.com.au 的正文在 `div.entry-content > div.content-inner`，
   而页面里有 **15 个 `<article>` 预告卡片**，于是只抽到 22 词 / 122 字符的卡片文字。

顺带澄清一个误判：早先探测 mining.com 得到 403，是因为探测脚本用了截断的 UA（`Mozilla/5.0`）；
程序使用的完整 Chrome UA 访问同一链接返回 **200**（92 KB）。**不是**网站封禁，也**不是**反爬。

### 二、修改的模块与函数

| 模块 / 函数 | 改动 |
| --- | --- |
| `news/sources.py::extract_article()` | 容器改为按**段落文本总量评分**，加入 `entry-content` / `content-inner` / `post-content` 等候选；`<body>` 仅作兜底；新增 `_NOISE_MARKERS` 过滤 related/classified/sidebar/teaser 等噪声容器 |
| `news/sources.py::fetch_article()` | 新增 `refresh: bool = False`：跳过本地缓存，保证"live"是本次真实抓取（与 PDF 工具同一契约） |
| `news/server.py::fetch_article()` | 透传 `refresh` |
| `news/server.py::search()` | 新增 `direct_url_items`（真直链条目），并让 industry 组**优先保留至多 3 条直源条目**，避免被时间排序挤出；新增 `stats.direct_url` |
| `agent/graph.py::fetch_news()` | 严格 live 模式下对正文抓取传 `refresh=True`；分析窗口与展示窗口对齐（company ≤8、industry ≤12） |
| `agent/graph.py::summarize_news()` | 摘要结果**按 URL 优先、否则按顺序回填**到原始条目；模型漏项时记录覆盖数，不再静默丢摘要 |
| `agent/llm.py::NewsDigestItem` | `title` / `url` 改为可选（源条目为权威，回填时覆盖） |
| `agent/prompts.py` | 要求逐条覆盖 company_items 与 industry_items、保持顺序、原样复制 url |
| `data/sources/article_samples.json`（新增） | 经实测的真实文章直链样本（含 observed 状态/词数/字符数），使正文测试不依赖 7 天窗口 |
| `scripts/verify_report.py`（沿用） | 报告 16 项验收（含"严格 live 不出现模拟价格"） |
| `tests/test_news.py` | 新增：预告卡片 vs 正文容器的解析回归；`refresh=True` 必须绕过缓存 |
| `tests/test_live.py` | 拆分为 **测试 A**（`search`，取消对可抓正文的隐含依赖）与 **测试 B**（`fetch_article`，经真实 MCP 会话 + `refresh=True`） |

### 三、真实文章 URL 与 MCP 调用结果

以下均通过**真实 stdio MCP 会话**调用 `fetch_article(url, refresh=true)`（非内部函数直调），
HTTP 请求由 MCP server 实际发出：

| 文章 URL | HTTP | provenance | 标题 | 正文词数 | 正文字符 |
| --- | --- | --- | --- | --- | --- |
| `https://www.mining.com/energyx-inks-10-year-2b-lithium-offtake-deal-with-trafigura/` | 200 | live | EnergyX inks 10-year, $2B lithium offtake deal with Trafigura | **532** | 3,508 |
| `https://www.mining.com/lithium-triangles-vast-resources-face-unsolved-extraction-hurdles/` | 200 | live | Lithium Triangle’s vast resources face unsolved extraction hurdles | **656** | 4,691 |
| `https://www.australianmining.com.au/glencore-begins-trading-asx/` | 200 | live | Glencore begins trading on Australian Securities Exchange | **340** | 2,390 |
| `https://www.australianmining.com.au/bulk-handling-technologies-tackles-lime-processing-from-end-to-end/` | 200 | live | Bulk Handling Technologies tackles lime processing from end to end | **736** | 5,134 |

正文有效性验证依据（测试内断言，非人工目测）：

- `word_count >= 60` 且 `len(text) >= 400`（排除"只有标题或 RSS 摘要"）；
- `text != title`；
- 正文至少含 3 个句末标点（英文 `. ` 或中文 `。`）；
- `source_url` 与请求 URL 一致、`site` 非空；
- `provenance == "live"`（`refresh=true` 强制真实请求，命中缓存即判失败）。

修复前后对比（同一 URL、同一 MCP 调用）：australianmining.com.au 由 **22 词 / 122 字符 → 340 词 / 2,390 字符**，
另一篇由 **25 词 / 140 字符 → 736 词 / 5,134 字符**；mining.com 保持 532 词。

### 四、测试设计的修正

- **测试 A**：`test_live_news_search_via_mcp_client` —— 只验证搜索本身（标题/URL/来源/发布时间非空、
  结果去重、每条都有 `relevance`、company 组都带公司证据），**不要求任何条目可抓正文**。
- **测试 B**：`test_live_fetch_article_via_mcp_client_returns_real_body` —— 候选 URL 优先取本次搜索的
  `direct_url_items`，再取样本清单；对每条发起真实抓取：
  - 外部不可用（403/429/超时/已删除）→ 记录后尝试下一条，全部不可用才 skip（并打印每条的原因）；
  - 抓取成功但正文过短/等于标题/句子不足 → **直接 fail**（判定为解析器缺陷，不许 skip）。
- 旧的单一用例（依赖窗口内出现直源链接）已删除。

### 五、Gate 1 / Gate 2 最新结果

```powershell
uv run pytest -m "not live"   # 142 passed, 13 deselected（含锁冲突修复与测试隔离回归）
uv run pytest -m live         # 12 passed, 0 skipped
uv run python scripts/verify_report.py            # 16/16 checks passed
uv run minereport-agent --require-live ...        # llm_mode=deepseek, live_gate_failures=[], 退出码 0
```

新报告：`reports/2026-10-15-pilbara-lithium-brief.md`
—— 新闻区 19 条（公司 7 / 行业 12），其中 18 条带中文要点与情绪判定，新闻区含 4 个 mining.com 直链。

### 六、仍然存在的外部限制

- **Google News 跳转链接不可解析**：其 `<link>` 是 JS 跳转页，正文取不到；这类链接会明确返回
  `UNRESOLVABLE_REDIRECT`，报告只使用其标题与链接，不猜测 URL。相关限制记录在报告 §8。
- **直源覆盖取决于当日选题**：若最近 7 天行业 RSS 没有锂相关条目，`industry/direct_url_items` 可能为空，
  此时正文测试改用样本清单（仍为真实在线抓取，不是离线样例）。
- **样本链接会失效**：`data/sources/article_samples.json` 中的 URL 若 404/403，测试会记录并尝试下一条；
  全部不可用时 skip 并在日志中写明原因（不记为 PASS）。
- **正文提取仍是启发式**：对结构特殊的站点可能仍抽不全；已通过"抓取成功但正文过短即 fail"暴露，
  而不是静默通过。
- 严格 live 模式下锂辉石精矿报价保持 `DATA_MISSING`（无公开免费真实源）。

---

## 2026-10-15 多意图会话层（多意图 + 槽位 + 会话/任务/请求 + 动态路由）

### 新增模块

| 模块 | 作用 |
| --- | --- |
| `agent/intent/schemas.py` | 7 类意图、5 类轮次动作、槽位模型（Pydantic 范围/日期校验）、意图兼容矩阵 |
| `agent/intent/extractor.py` | 中文槽位抽取：商品别名（含裸"铜/铝/镍/锌"）、公司/项目注册表、相对/绝对时间、商品歧义识别、锂族归属 |
| `agent/intent/filling.py` | 槽位优先级：本轮输入 > 任务上下文 > 规则默认值（记录 `slot_sources`） |
| `agent/intent/validator.py` | 业务校验：商品/公司/项目/PDF 来源必须命中可信注册表；逐字段丢弃非法值而不清空整批 |
| `agent/intent/classifier.py` | 规则 + DeepSeek 结构化识别；取消/继续/重查/歧义/闲聊规则；真实模型失败即报错（不静默降级） |
| `agent/session/store.py` | SQLite：`sessions/tasks/requests/effects`，短事务 + `BEGIN IMMEDIATE` + WAL + `busy_timeout` + 租约领取 |
| `agent/session/manager.py` | `user.json` 身份持久化、会话归属校验、`AsyncSqliteSaver`/`InMemorySaver` 工厂、历史上限 |
| `agent/task/manager.py` | 任务生命周期（created/waiting_for_slots/running/completed/failed/cancelled）+ 请求幂等 rePlay |
| `agent/task/matcher.py` | `TaskMatcher`：规则打分 + 冲突淘汰 + 多候选追问；模型只能在候选集内选择 |
| `agent/effects.py` | `call_tool_once`：工具幂等 `(task_id, step_id, tool_name, args_hash)`，成功结果完整恢复（>256KB 落文件 + 摘要校验） |
| `agent/conversation.py` | 顶层对话图：`session_manager → intent → slot_extraction → matcher → filling → validation → task_manager → route`，日报子图经显式输入/输出适配节点命令式复用 |
| `agent/nodes/chat.py` | 普通聊天提示词（零 MCP 调用） |

### 标识流转

`user_id`（`user.json` 持久化）→ `session_id`（= LangGraph `thread_id`）→ `task_id`（新业务查询新建；补槽/明确继续沿用）→ `request_id`（单次请求，`(session_id, request_id)` 幂等）→ `tool_call_id`（应用层生成，与 MCP 传输层 JSON-RPC 请求 ID 严格区分）。每次 MCP 调用记录 session/task/request/tool_call/step 五个 id。

### 中断与切换实现

v1 **不使用 `interrupt()`**：缺槽时任务落 `waiting_for_slots`（`suspended=false`）并结束本轮；下一轮由 `turn_router` + `TaskMatcher` 判定「补槽 / 新任务 / 闲聊 / 取消 / 明确继续」。切走只把任务标 `suspended=true`，槽位不污染聊天；显式"先不查了/取消"→ `cancelled` 且不可恢复。副作用通过 `waiting_for_slots` + 效果表幂等键保护，不依赖 thread 内多个暂停现场。

### 实测结果（2026-10-15）

```
uv run pytest -m "not live"    → 145 passed, 13 deselected（含锁冲突、测试隔离、多进程/并发回归）
uv run pytest -m live          → 13 passed, 145 deselected（含真实模型会话意图测试与真实新闻正文抓取）
docker compose up --abort-on-container-exit --exit-code-from agent → agent 退出码 0
```

新增离线用例分布：`test_intent_slots.py` 17、`test_session_task_effects.py` 15、`test_conversation.py` 12、`test_sqlite_locking.py` 6、`test_test_isolation.py` 4（原 91 项全部保持通过）。

**宿主跨进程恢复**（两个独立 python 进程，SQLite 持久化）：

```
进程 A: 帮我查一下锂价        → Agent: 你想查碳酸锂还是锂辉石精矿？   （task=price_query/waiting_for_slots, suspended=1）
进程 B: 碳酸锂，最近 30 天    → Agent: 碳酸锂（广期所主力连续）近 30 天走势：… -23.757%（provenance=live）
                              task_id 不变，intent 升级 price_query→trend_query，真实调用 get_trend
```

**Docker 跨容器恢复**（`docker compose --profile chat run --rm agent-chat`，两次运行 = 两个容器）：

```
容器 A: 帮我查一下锂价      → 追问品种，任务落盘到 ./data/sessions/agent_state.sqlite
容器 B: SESSION_ID=<id> …  → 碳酸锂，最近 30 天 → 同一 task_id 完成，容器内真实调用 get_trend（-23.757%）
```

### 已知限制

- **非 exactly-once**：MCP 调用与 SQLite 提交无法原子；若 MCP 已成功但持久化前崩溃，仍可能重复调用。当前工具均为只读，采用"受控重试 + 成功结果复用"；将来若有副作用工具需更严格机制。
- v1 不实现 LangGraph `interrupt()`/`Command(resume=...)`；如需真正中断恢复，应改为 per-task `thread_id` 并维护 `session_id/task_id/thread_id` 映射。
- `resource_query` 仅支持可信注册表内的公司/项目；未登记时明确告知，不编造来源。
- 跨进程并发依赖数据库唯一约束/条件更新/租约；进程内 `asyncio.Lock` 只保护单进程。
- CLI 管道输入按 UTF-8 解析（`docker compose run < file` 需 UTF-8 文本）。
---

## 2026-10-15 SQLite 锁冲突与测试隔离修复 ✅

### 现象

多轮对话第二轮（槽位补全）崩溃：

```
sqlite3.OperationalError: database is locked
conversation.py:407 finish() → task_manager.update() → session/store.py:308 update_task()
  → session/store.py:162 _write() → cursor.execute("BEGIN IMMEDIATE")
```

### 根因（基于对照实验，非猜测）

1. **LangGraph Checkpointer 与业务存储共用同一个 SQLite 文件**（`data/sessions/agent_state.sqlite`）。
   异步图必须用 `AsyncSqliteSaver`（同步 `SqliteSaver` 直接抛 `NotImplementedError`），其 aiosqlite 连接与
   业务存储的同步连接落在同一文件上。
2. **对照实验**：
   - `--persist memory`（业务库独占该文件）连跑两轮 → 正常，无锁错误；
   - 仅建立 saver 连接、不写入 → 普通连接 `BEGIN IMMEDIATE` 正常（`in_transaction=False`）；
   → 说明锁是**瞬态争用**：saver 写入与业务节点写入在时间上重叠时，业务侧 5s `busy_timeout` 内拿不到写锁。
3. 次要问题：`Store` 使用**线程局部连接**（`asyncio.to_thread` 的工作线程各自新建连接），进一步增加了同文件上的连接数。

### 修复

| 位置 | 修改 |
| --- | --- |
| `session/store.py` | **拆分数据库**：`agent_state.sqlite`（仅 Checkpointer，`MINEREPORT_STATE_DB`）与 **`agent_business.sqlite`**（sessions/tasks/requests/effects，`MINEREPORT_BUSINESS_DB`）。两库不再共享连接与写锁 |
| `session/store.py::_migrate_from_legacy()` | **非破坏性迁移**：若新业务库为空且旧合并库存在 `tasks` 表，则 `INSERT OR IGNORE` 复制四张表（旧库保留不动，启动时打印迁移提示） |
| `session/store.py::_write()` | `BEGIN IMMEDIATE` 增加**有界重试**（0 / 0.2 / 0.5 / 1.0 / 2.0 秒，总预算 ≈3.7s），仍失败则抛**类型化** `StoreBusyError`（不再裸 `OperationalError`） |
| `session/store.py` | 所有连接统一 `busy_timeout=5000` + `PRAGMA journal_mode=WAL`；`effects_dir()` 跟随业务库 |
| `conversation.py` | `task_manager` 与 `finish` 节点捕获 `SessionBusyError`：给出"数据库繁忙，任务状态已保留，请重试"的回复，**绝不让 REPL 崩溃** |
| `main.py` | REPL 单轮捕获任意异常并继续循环（会话不中断）；管道 stdin 按 UTF-8 解析 |
| `intent/classifier.py` · `task/matcher.py` | 补槽分支接受**兼容意图**（price_query ↔ trend_query），使"最近30天的走势"能续接原任务 |

### 测试隔离修复（本次故障的连带问题）

拆分后，只覆盖 `MINEREPORT_STATE_DB` 的测试会把业务库写进**真实的 `data/sessions/`**，造成跨测试/跨运行污染
（实测出现"4 条待恢复任务"）。

- `tests/conftest.py` 新增 **autouse fixture**：每个测试自动把两个数据库都指向 `tmp_path`（测试仍可自行覆盖）；
- `test_session_task_effects.py` / `test_conversation.py` 的 fixture 显式设置两个变量；
- 新增 `tests/test_test_isolation.py`：断言两个库均落在 `tmp_path`、**离线测试不得改动真实 `data/sessions`**
  （对比文件尺寸/纳秒时间戳/各表行数快照）、同一流程重复执行结果稳定、不同操作顺序得到相同终态。

### 实测结果（2026-10-15）

```
uv run pytest "tests/test_session_task_effects.py::test_task_lifecycle_and_resumable_filter" -vv
  → 1 passed（修复前为 4 条残留导致失败）
uv run pytest -m "not live"     → 145 passed, 13 deselected（连续两次运行，用例集合 sha256 一致）
uv run pytest -m live           → 13 passed, 145 deselected
docker compose up --abort-on-container-exit --exit-code-from agent → agent 退出码 0（未受影响）
```

**CLI 三轮实测（三个独立进程，业务库+状态库均在临时目录）**：

```
帮我查一下锂价     → 你想查碳酸锂还是锂辉石精矿？        （task 落 waiting_for_slots）
碳酸锂            → 还需要你补充：日期。              （不再出现 database is locked）
最近30天的走势     → 碳酸锂（广期所主力连续）近 30 天走势：… -23.757%（provenance=live）
                   task_id 不变、intent 升级 price_query→trend_query、真实调用 get_trend
lock errors: 0     tasks: 1（status=completed）
```

### 数据安全说明

- 修复**未删除、未覆盖**任何已有会话或任务；旧合并库 `agent_state.sqlite` 原样保留。
- 为满足"不丢失数据"的要求，**未清理** `data/sessions/agent_business.sqlite` 中由修复前测试写入的历史行
  （本次检查时该文件含 63 个会话 / 91 个任务，多为修复前测试产生的 user/session）。
  如需清理，可先备份该文件，再按 `user_id` 过滤删除测试产生的记录——本仓库未执行该操作。
---

## Gate 0 — 真实数据源指纹校验 ✅

```powershell
uv run python scripts/verify_sources.py           # 严格校验
uv run python scripts/verify_sources.py --update  # 首次写入 sha256
```

```
== pilbara-2023-mineral-resource (role=primary-deliverable, standard=JORC)
   url      : https://minedocs.com/25/Pilbara-Minerals-MR-08242023.pdf
   bytes    : 4006500       sha256: db8b9740f60deab7224c80022f34c74b...
   standards: ['JORC']  rows=24   => PASS
== ni43101-lithium-ionic (role=standard-compatibility, standard=NI 43-101)
   bytes    : 4884816       sha256: 646dec1576db5d63e69df9b7e489de42...
   standards: ['NI 43-101']  rows=22   => PASS
== liontown-kathleen-valley-jorc (role=parser-corpus, standard=JORC)
   bytes    : 323635        sha256: 8aec237a34051c7f0362237655bf1752...
   standards: ['JORC']  rows=8   => PASS

3/3 sources verified
```

不带 `--update` 复跑同样 3/3 通过，说明源指纹稳定；任一源内容变化都会让 Gate 0 失败，而不是被静默接受。

---

## Gate 1 — 离线测试（单测 + 离线 E2E）✅

```powershell
uv run pytest -m "not live"
```

结果：**91 passed / 12 deselected**

```
tests/test_graph_offline.py         (2)   离线 E2E：fixture + stub LLM，产出完整日报
tests/test_news.py                 (10)   RSS 解析 / 相关性过滤 / 正文抽取（预告卡片回归）/ refresh 绕过缓存 / SSRF
tests/test_pdf_extractor.py         (7)   标准判定 / 资源量与储量分离 / 证据页码
tests/test_pdf_safety.py            (8)   SSRF / scheme / 路径白名单 / 非 PDF 拒绝
tests/test_pdf_scope.py             (4)   汇总 vs 附录分级 / 跨作用域去重 / 真实 PDF 无重复
tests/test_price.py                (11)   快照与日K解析 / 交易日校验 / fixture 降级
tests/test_provenance_and_report.py (6)   provenance 契约 + 报告“禁止冒充 live”
tests/test_report_quality.py        (8)   严格 live 屏蔽 / 新闻分节 / 链接与表格规范
tests/test_llm_strict.py            (8)   JSON 模式接线 / 无 key 必须报错 / 不静默降级
tests/test_news_entities.py        (16)   混淆公司名（Rio Tinto / Pilbara Gold）/ 去重
tests/test_mcp_config.py           (11)   MCP 配置可移植性 + 三个 Server 真实 stdio 启动
```

关键断言：fixture 报告不得出现 `live`；只有 fixture 时严格模式必须失败；`Inferred` 不得挂到 `OreReserve`；
SSRF 与路径越权被拦；三个 MCP Server 能用真实 stdio 会话拉起；正文抓取成功但过短即判定解析缺陷。

---

## Gate 2 — 真实 Live 测试 ✅

```powershell
uv run pytest -m live
```

结果：**12 passed, 0 skipped**（原 10 passed / 1 skipped 中的 skip 已在本轮修复）

| 用例 | 断言要点 |
| --- | --- |
| `test_live_news_search_returns_real_items` | 搜索返回真实条目 |
| `test_live_news_search_via_mcp_client`（新增，测试 A） | 经真实 MCP 会话调用 `search`：标题/URL/来源/发布时间齐全、去重、company 组带公司证据 |
| `test_live_fetch_article_via_mcp_client_returns_real_body`（新增，测试 B） | 经真实 MCP 会话调用 `fetch_article(refresh=true)`：正文 ≥60 词且 ≥400 字符、不等于标题、≥3 句、`provenance=live` |
| `test_live_pilbara_pdf_matches_registry_and_separates_resources_from_reserves` | sha256 与注册表一致；JORC；资源量与储量行数 > 0 |
| `test_live_ni43101_source_is_reachable_and_declares_the_standard` | 真实下载并声明 NI 43-101 |
| `test_live_price_supports_historical_lookup` | 历史日期返回当日收盘；日期严格递增无重复 |
| `test_live_non_trading_day_returns_not_found_instead_of_latest_price` | 周六返回 `NOT_FOUND`，拒绝用最新价顶替 |
| `test_live_lithium_carbonate_is_flagged_as_a_downstream_reference` | 碳酸锂标注为下游参考价 |
| `test_live_news_search_is_entity_filtered` | 公司组不含 Rio Tinto / Pilbara Gold / iron ore |
| `test_live_deepseek_news_digest_returns_chinese_and_valid_schema` | 真实 DeepSeek 新闻摘要为中文且符合 Schema |
| `test_live_deepseek_risk_section_returns_chinese_and_valid_schema` | 真实 DeepSeek 风险段落为中文且符合 Schema |
| `test_live_agent_run_passes_the_strict_gate` | 端到端严格模式：报告含 `DATA_MISSING`、无 fixture 价格、无混淆公司、含 1.1/1.2 分节 |

---

## DeepSeek 真实模型链路 ✅

| 运行方式 | 结果 |
| --- | --- |
| 本地 | `llm_mode: deepseek`、`live_gate_failures: []`、退出码 0 |
| 容器 | `llm_mode: deepseek`、`live_gate_failures: []`、`agent-1 exited with code 0` |

真实调用证据（不含密钥）：

```
tests/test_live.py::test_live_deepseek_news_digest_returns_chinese_and_valid_schema PASSED
tests/test_live.py::test_live_deepseek_risk_section_returns_chinese_and_valid_schema PASSED
```

`Thinking mode does not support this tool_choice` 的根因与修复见上文《针对性修复记录》第 1 节。

---

## Gate 3 — Docker Compose 四服务 ✅

```powershell
docker compose build --build-arg HTTP_PROXY=http://192.168.65.254:7899 `
                    --build-arg HTTPS_PROXY=http://192.168.65.254:7899 `
                    --build-arg NO_PROXY=localhost,127.0.0.1
docker compose up --abort-on-container-exit --exit-code-from agent
```

- 镜像 `minereport:local` 构建成功，容器内 `uv sync --frozen --no-dev` 安装锁定依赖；
- 三个 Server 的 `/healthz` 全部 `200 OK`，随后建立 StreamableHTTP 会话；
- agent 在 `service_healthy` 之后启动，`llm_mode=deepseek`，`live_gate_failures: []`，**退出码 0**；
- 报告经 volume 落到宿主机 `reports/`。

容器出网的根因与修复见《MCP 配置可移植性》与《针对性修复记录》。

---

## Gate 4 — Claude Desktop / Cursor 工具发现 ✅（Cursor 实测）

- `%USERPROFILE%\.cursor\mcp.json` 含三个 Server，Cursor 拉起三个 stdio 子进程；
- 客户端日志出现 `Successfully connected to stdio server` 与 `ListToolsRequest`；
- 日志记录到 `CallToolRequest`，随后真实命中 `hq.sinajs.cn/list=hf_CAD`（200 OK）与日K接口，
  与缓存 `data/cache/prices/kline-copper-CAD.json` 的时间戳吻合。

---

## 端到端日报产物 ✅

```powershell
uv run minereport-agent --require-live --json --quiet "给我生成一份关于 Pilbara 锂矿的今日简报"
uv run python scripts/verify_report.py
```

- 退出码 0，四个数据集全部 `live`，`live_gate_failures` 为空；
- `scripts/verify_report.py`：**16/16 checks passed**；
- 报告：`reports/2026-10-15-pilbara-lithium-brief.md`
  —— 新闻区 19 条（公司 7 / 行业 12），18 条带中文要点与情绪，含 4 个 mining.com 直链；
  资源量主表 Measured 22.1 / Indicated 315.2 / Inferred 76.6 各一次，储量 Proved 19.1 / Probable 195.1 各一次；
  附录 19 行单独成段并标注不可相加；锂辉石精矿显示 `DATA_MISSING`。

---

## 遗留事项与使用前确认

1. **Docker Desktop 的全局代理配置仍是失效的 `127.0.0.1:7890`**（它退出时会重写配置文件）。
   不影响本项目：compose / `docker.env` / 构建参数已显式指定可用代理。备份在
   `%APPDATA%\Docker\settings-store.json.codex-backup`。
2. **外部数据源限制**：Google News 跳转链接无法解析正文（返回 `UNRESOLVABLE_REDIRECT`）；
   锂辉石精矿无公开免费真实报价（`DATA_MISSING`）；Pilbara 资源量为 2023-08-24 披露口径（报告中已标注非最新）；
   行业直源当日可能没有锂相关条目（正文测试改用已实测样本链接）。
3. **正文提取为启发式**：结构特殊的站点可能仍抽不全，但会被"正文过短即 fail"暴露，不会静默通过。
4. **旧目录残留**：`D:\Python\1` 已空，结束本会话后可执行 `Remove-Item -LiteralPath 'D:\Python\1'` 清理。
### 一次实测挂起与处置（如实记录）

在最终回归时，`uv run pytest -m live` 曾连续运行 30 分钟未结束。定位过程：

1. 发现多个**我此前冒烟/Docker 验证遗留的进程**（`minereport-news/pdf/price.exe`、`uv`、`docker-compose`）仍在运行并占用资源；
2. 清理这些遗留进程后分段复跑：不含重型 E2E 的 12 个 live 用例 **12 passed（3.5 分钟）**，
   重型 E2E（`test_live_agent_run_passes_the_strict_gate`，会在 pytest 内拉起三个 stdio MCP Server）
   单独运行 **passed（54 秒）**；
3. 清理后完整复跑：**13 passed（3 分 44 秒）**。

结论：挂起来自遗留进程造成的资源竞争，而非代码缺陷；已记录处置过程，未修改任何断言或跳过用例。

---

## 2026-10-15 收尾复跑：多进程 / 并发 / CLI / Docker（最终数字）✅

本轮没有改业务逻辑，只做三件事：补齐并发与多进程的回归用例、修掉两处"可验证性"缺口、把全部 Gate 在**最终代码**上重跑一遍。
文中较早章节里的 `142 passed` 是当时的历史记录，本节数字是最终结果。

### 1. 新增的 3 个回归用例（离线，共 145 项的一部分）

| 用例 | 位置 | 断言要点 |
| --- | --- | --- |
| `test_completed_step_is_reused_across_processes` | `tests/test_session_task_effects.py` | 同一 SQLite 文件上的两个 `Store`（≈两个进程）：A 完成后，B 命中 `(task_id, step_id, tool_name, args_hash)` 幂等键，**直接恢复结果、一次 MCP 都不调用**（`tool_b.calls == 0`） |
| `test_a_slow_tool_call_does_not_block_other_sessions` | 同上 | 会话 A 正在跑 0.6s 的慢 MCP 调用（用 `asyncio.Event` 确认已进入调用）时，会话 B 仍能在同一个库上完成写入 + 工具调用，耗时 < 0.5s ⇒ 证明**没有把 SQLite 写事务跨网络调用持有** |
| `test_busy_database_at_finish_degrades_instead_of_crashing` | `tests/test_conversation.py` | 让 `finish()` 阶段抛 `StoreBusyError`：整轮不抛异常、回复"数据库暂时繁忙…"、`errors` 内含 `store-busy`、任务记录仍是 `completed` ⇒ 最初的"REPL 直接崩溃"不会再出现 |

另有 `test_test_isolation.py`（4 项）确保离线测试永远不碰真实 `data/sessions/`：两个库都指向 `tmp_path`、真实目录快照（文件大小/纳秒时间戳/各表行数）在测试前后完全一致、同一流程重复执行与不同操作顺序结果一致。

### 2. 本轮修掉的两处"可验证性"缺口

| 位置 | 问题（实测） | 修复 |
| --- | --- | --- |
| `src/minereport/agent/main.py::_ensure_piped_utf8()` | 只有 stdout 被强制 UTF-8；stderr 重定向时回落到 cp936，日志中的中文（`[模型] …`、`[身份] …`）变成乱码 | 非 TTY 的 stderr 同样 `reconfigure(encoding="utf-8")`；交互控制台保持原样，避免影响输入 |
| `docker-compose.yml` | Gate 3 的 `--build` 不带 `--build-arg` 时会重建 `pip install uv` 层并因无代理失败：`failed to solve: process "/bin/sh -c pip install --no-cache-dir uv==0.11.25" did not complete successfully: exit code 1` | 新增 `x-build: &build` 锚点，用 `${HTTP_PROXY:-}`/`${HTTPS_PROXY:-}`/`${NO_PROXY:-}` 从宿主机环境注入构建参数。**无代理机器三个变量为空，Gate 3 命令一字不变**；有代理机器先导出变量再执行同一条命令 |

> 说明（非缺陷）：`/healthz` 实际返回 `service / status / ready / tools / tool_count / version / mcp_initialized / uptime_s / checked_at`；
> 计划里提到的 `transport` 字段由 `mcp_initialized` 与容器命令行体现，未单独输出。

### 3. 最终 Gate 结果（全部在最终代码上实跑）

| Gate | 命令 | 结果 |
| --- | --- | --- |
| 1 | `uv run pytest -m "not live" -q` | **146 passed, 13 deselected**；连跑两次，`docs/gate1-offline-run{1,2}.junit.xml` 用例集合 sha256 相同，`failures=0 errors=0 skipped=0` |
| 2 | `uv run pytest -m live -q` | **13 passed, 0 failed, 0 skipped**（978s）。最慢一项是 Google News RSS（`test_live_news_search_returns_real_items` 655s），属上游网络抖动而非代码缺陷 |
| 3 | `docker compose up --build --abort-on-container-exit --exit-code-from agent` | **`agent-1 exited with code 0`**；`llm_mode=deepseek`、四个数据集全部 `live`、`live_gate_failures: []`；报告经卷写入宿主机 `reports/2026-10-15-pilbara-lithium-brief.md`；三个 `/healthz` 均 200（见 `docs/gate3-compose.err`） |
| 3b | `$env:SESSION_ID=<id>; docker compose --profile chat run --rm -T agent-chat`（两次运行 = 两个容器） | 容器 A 建会话 → 容器 B 恢复：`碳酸锂` → 追问日期（不再锁崩），`最近30天的走势` → 同一 `task_id=76e917d4…` 升级为 `trend_query` 并真实调用 `get_trend`（-23.757%）；宿主 `--state` 也读到同一任务 |
| 4 | `uv run python scripts/verify_report.py` | **16/16 checks passed**（对最新报告） |

三个 MCP Server 的 `/healthz` 实测（容器内取回）：

```
mining-news-mcp : ready=true tool_count=2 tools=search,fetch_article  mcp_initialized=true
mineral-pdf-mcp : ready=true tool_count=2 tools=extract_resources,list_sources
lme-price-mcp   : ready=true tool_count=3 tools=get_price,get_trend,list_commodities
```

### 4. CLI 实测（宿主，三个进程，真实 DeepSeek + 真实 MCP）

```
session_id=1c4fd9e5-d2cf-4ca2-8f39-2511fa17fcfa
进程 A: 帮我查一下锂价  → 你想查碳酸锂还是锂辉石精矿？   （task=41a0d30c… waiting_for_slots）
进程 A: 碳酸锂          → 还需要你补充：日期。           （不再出现 database is locked）
进程 B: 最近30天的走势  → 碳酸锂（广期所主力连续）近 30 天走势：… -23.757%（provenance=live）
                         同一 task_id，intent 升级 price_query→trend_query，真实请求新浪日K（HTTP 200）
进程 C: --state         → tasks: 1（status=completed, missing=无）
```

`lock errors: 0`。日志：`docs/cli-session-A.{out,err}`、`docs/cli-session-B.{out,err}`、`docs/cli-session-C.out`；
`docs/cli-daily-report.{out,err}` 为严格 Live 模式单次日报（exit 0、四源 live、`live_gate_failures: []`）。

### 5. 仍未完成 / 需人工验证

- Gate 4（Claude Desktop / Cursor 工具发现）：本轮未重新截图，历史记录见《Gate 4》章节（Cursor 实测）；配置文件见 `mcp-config.stdio.example.json` + `scripts/generate_mcp_config.py`。
- 其余限制不变：Pilbara 资源量经 `minedocs.com` 第三方镜像获取、锂辉石精矿 `DATA_MISSING`、新浪为第三方行情转述、工具幂等为"可追踪/可恢复/尽量避免重复调用"（非 exactly-once）。
- `data/sessions/` 中保留了历史（含修复前测试产生的）会话与任务，未做删除；如需清理请先备份再按 `user_id` 过滤。

---

## 2026-10-15 SQLite 锁冲突复核（按 6 项检查逐条复核 + 修掉 aiosqlite WAL 缺口）✅

针对"多意图 Agent 运行时出现 `database is locked`"，对**当前**代码逐条复核 6 个检查点，其中第 5 项发现了一个仍然存在的真实缺口并已修复。

### 一、六项检查的复核结论

| # | 检查项 | 结论 | 证据 |
| --- | --- | --- | --- |
| 1 | Checkpointer 与业务存储是否共用同一文件 | **否，已拆分**：`agent_state.sqlite`（`MINEREPORT_STATE_DB`，仅 LangGraph）与 `agent_business.sqlite`（`MINEREPORT_BUSINESS_DB`，sessions/tasks/requests/effects） | `session/store.py::state_db_path/business_db_path`；`test_checkpointer_and_business_store_use_different_files` |
| 2 | 是否有写事务跨 `await` / DeepSeek / MCP 网络调用 | **否**：`_write()` 只包住单条语句批次，MCP/LLM 调用都在 `_write()` 之外 | `test_a_slow_tool_call_does_not_block_other_sessions`（0.6s 慢调用进行中，另一个会话仍可写入，耗时 <0.5s） |
| 3 | `_write()` 正常/异常路径能否 commit / rollback / 释放 | 正常 `COMMIT`，异常 `ROLLBACK`，`finally: cursor.close()`；`BEGIN IMMEDIATE` 有界重试（0/0.2/0.5/1.0/2.0s），超预算抛类型化 `StoreBusyError` | `test_write_retries_while_another_connection_holds_the_lock`、`test_write_raises_typed_error_when_lock_is_never_released`（失败后连接仍可用、无半行写入） |
| 4 | `finish()` 更新 `suspended` 是否与 Checkpointer 竞争 | 两个文件互不争锁；`finish()` 捕获 `SessionBusyError` → 回复"数据库暂时繁忙，任务状态已保留" | `test_busy_database_at_finish_degrades_instead_of_crashing` |
| 5 | `busy_timeout` / WAL 是否覆盖**所有**连接（含 aiosqlite） | **复核发现缺口 → 已修复**（见下） | `test_checkpointer_connection_uses_wal_and_busy_timeout` |
| 6 | 未关闭连接 / 重复初始化 Checkpointer | Checkpointer 在 `SessionManager.checkpointer()` 内**只创建一次**并缓存，`aclose()` 释放；`test_...` 断言 `await mgr.checkpointer() is saver` | `session/manager.py`；`test_identity_and_tasks_survive_process_restart` |

### 二、复核新发现并修复的缺口（第 5 项）

实测 `AsyncSqliteSaver.from_conn_string()` 打开的 aiosqlite 连接（修复前）：

```
checkpointer journal_mode   = delete     <- 没有 WAL
checkpointer busy_timeout   = 5000       <- 只是 sqlite3 默认值，并非显式设置
checkpointer synchronous    = 2          <- FULL
```

业务库有 WAL，Checkpointer 却没有：两个 CLI 进程或容器共用 `agent_state.sqlite` 时，回滚日志会把整库锁住——正是本次报告的这一类故障。

**修复**：`session/manager.py::SessionManager.checkpointer()` 拿到 saver 后，对其 `conn` 显式执行
`PRAGMA busy_timeout=5000` / `PRAGMA journal_mode=WAL` / `PRAGMA synchronous=NORMAL`（随后 `await conn.commit()`）。
修复后实测：

```
checkpointer journal_mode   = wal
checkpointer busy_timeout   = 5000
checkpointer synchronous    = 1
```

### 三、多进程真实并发验证（新增 `scripts/verify_concurrent_db.py`）

用的是 `multiprocessing(spawn)` 起的**多个真实进程**（不是线程）：每个进程都对**同一个 session** 写任务、争抢**同一个效果步骤**、并用 `SessionManager.checkpointer()` 对**同一个 state 文件**各写 5 个 checkpoint。

```
uv run --no-sync python scriptserify_concurrent_db.py 3 20
  worker 0/1/2: tasks=20 each, retries=0, journal=wal, error=None
  争抢同一步骤的赢家数 = 1
  integrity_check business: ok    integrity_check state: ok
  PASS: 60 tasks + 15 checkpoints，无丢失/覆盖

uv run --no-sync python scriptserify_concurrent_db.py 6 30
  PASS: 180 tasks + 30 checkpoints；retries=0；两个库 integrity_check 均 ok
```

不变式：所有 worker 创建的 `task_id` 在库里**全部存在且集合完全相等**（无丢失、无幽灵行、无重复）、`integrity_check == 'ok'`、同一步骤只有一个领取者。

### 四、CLI 三轮复测（本轮新 session）

```
进程 A: 帮我查一下锂价  → 你想查碳酸锂还是锂辉石精矿？   （task=79a05444… waiting_for_slots）
进程 A: 碳酸锂          → 还需要你补充：日期。           （不再 database is locked）
进程 B: 最近30天的走势  → 同一 task_id=79a05444… 升级 trend_query，真实 get_trend -23.757%（HTTP 200）
进程 C: --state         → tasks: 1（status=completed, missing=无）
CLI stderr 中 locked / OperationalError / StoreBusy 命中数：0
```

### 五、本轮测试结果（在修复后的代码上实跑）

```
uv run --no-sync pytest -m "not live" -q   → 146 passed, 13 deselected（连跑两次，用例集合 sha256 一致，failures=errors=skipped=0）
uv run --no-sync pytest -m live  -q        → 13 passed, 0 failed, 0 skipped（287s）
```

离线由 145 → 146，新增 `tests/test_sqlite_locking.py::test_checkpointer_connection_uses_wal_and_busy_timeout`。
（需求里提到的"132 项离线"是更早的历史数字；当前最新为 146 项离线 + 13 项 Live。）

### 六、数据安全

本次只改了“连接打开时设置 pragma”和新增测试/脚本，**未删除、未覆盖**任何已有会话或任务；`data/sessions/` 下的历史数据保持原样。

### 七、修复后的 Docker 复验（最终镜像）

- 重新构建镜像后复跑 `docker compose up --build --abort-on-container-exit --exit-code-from agent`：**`agent-1 exited with code 0`**、`live_gate_failures: []`，三个 MCP Server 容器随后重新 `healthy`。
- 镜像内 `/app/src/minereport/agent/session/manager.py` 的 sha256 与宿主机一致（`345694137b03cc5f…`），即 Gate 3 跑的就是带 pragma 修复的代码。
- 报告由该容器重新生成：`reports/2026-10-15-pilbara-lithium-brief.md`（26255 bytes）。
- 时间顺序：`manager.py` 16:11 → Gate 1（16:15）→ Gate 2（16:20）→ Gate 3（16:22），全部在最终代码上。

---

## 2026-10-15 交付清单对照 ✅

| # | 清单项 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | Agent 主流程：输入"给我生成一份关于 Pilbara 锂矿的今日简报" → Markdown 简报（新闻摘要 + 储量数据 + 价格走势 + 风险提示 + 引用源） | ✅ | `uv run --no-sync minereport-agent --require-live --json --quiet "…"` 退出码 0、四源 `live`、`live_gate_failures: []`；产物 `reports/2026-10-15-pilbara-lithium-brief.md`（26385 bytes）含 §1 新闻摘要 / §2 矿产资源量 / §3 矿石储量 / §4 价格走势 / §5 风险提示 / §6 引用源链接；`scripts/verify_report.py` **16/16** |
| 2 | 3 个 MCP Server | ✅ | `mining-news-mcp: search, fetch_article`；`mineral-pdf-mcp: extract_resources, list_sources`；`lme-price-mcp: get_price, get_trend, list_commodities`。`tests/test_mcp_config.py` 用真实 stdio 会话逐个拉起并断言工具集；Docker `/healthz` 也回报 `tool_count` |
| 3 | 1 个 client 端 Agent 编排 | ✅ | LangGraph：`agent/graph.py`（日报子图，news / reserves / price 三个节点并行 fan-out）+ `agent/conversation.py`（多意图 / 槽位 / 任务 / 会话层，命令式复用日报子图）；MCP 侧经 `langchain-mcp-adapters` |
| 4 | `mcp-config.json`，可直接接 Claude Desktop / Cursor | ✅（本轮补齐文件名） | `scripts/generate_mcp_config.py` 默认同时生成 `mcp-config.json` 与 `mcp-config.stdio.local.json`（内容相同、均 gitignore，公开仓库只留 `<PROJECT_PATH>` 模板）。`scripts/verify_mcp_config.py mcp-config.json` → **PASS：三个 Server 全部用文件里的 command+args 启动成功**；`--cursor` / `--claude` 可直接合并进客户端配置；HTTP 形态单独放 `mcp-config.http.json` |
| 5 | `RUN.md`：5 分钟跑起来（含一条 docker-compose） | ✅ | `RUN.md` §1 本地一条命令出报告；§3 单条 `docker compose up --build --abort-on-container-exit --exit-code-from agent`，实测 `agent-1 exited with code 0` |

### 与清单"形似但不逐字相同"的两处（如实说明）

1. 清单写 `mcp-config.json`。仓库同时保留 `mcp-config.stdio.example.json`（`<PROJECT_PATH>` 占位模板，公开仓库用）与 `mcp-config.http.json`（HTTP 形态）——这是按"不要把不兼容的配置结构混在一个文件、不要在 GitHub 写死本机绝对路径"的要求做的拆分；本轮又补上了清单同名的 `mcp-config.json`（本地生成、已忽略）。
2. 清单说"储量数据"。报告把 **Mineral Resources（Measured / Indicated / Inferred）** 与 **Ore Reserves（Proved / Probable）** 拆成 §2 / §3 两张表，避免把资源量当作储量——这是此前明确要求的业务修正。

### 本轮新增 / 变更

- `scripts/generate_mcp_config.py`：默认输出两个文件名；新增 `MINEREPORT_MCP_CONFIG_DIR` 环境变量覆盖输出目录（便于测试 / CI）。
- `scripts/verify_mcp_config.py`（新）：按配置里的 `command` + `args` 真实启动每个 Server、`initialize()`、`list_tools()`，并校验清单要求的工具是否存在；模板未渲染时明确提示。
- `tests/test_mcp_config.py`：+1 用例（默认运行产出清单同名文件且与 local 文件内容一致）→ 离线 147 → **204**。
- `.gitignore`：新增忽略 `mcp-config.json`。
- 测试：`uv run --no-sync pytest -m "not live" -q` → **204 passed, 13 deselected**（连跑两次，用例集合 sha256 一致）。Live 13 项本轮未重跑：`src/` 自 16:11 起未改动，而 16:20 的那次 Live 门禁就是在这份 `src/` 上执行的。

---

## 2026-10-15 矿权日报数据质量专项修复（v2：含四项技术修订）✅

### 一、六个问题的真实根因

| # | 现象 | 根因（文件:符号） | 修复 |
| --- | --- | --- | --- |
| 1 | 原始 63 条 ≠ 公司 7 + 行业 12 + 过滤 29 | `news/server.py::search` 里 `stats.raw` 是**去重前**计数、`company/industry` 是**去重+截断后**计数、`excluded_irrelevant` 又是**去重前**的无关条数；`entities.dedupe_items` 丢弃的重复项没有任何计数出口 | 分阶段闭包流水线 `data.pipeline` + §1.3 流水表；去重先于分类；展示上限的"未展示"单列 |
| 2 | `news.as_of = 2026-10-13T23:15:02`（实为最新**公司**新闻） | `as_of=(company or industry)[0].published_at` | `as_of = max(本次展示条目 published_at)`，并单列 `company_as_of`/`industry_as_of`；`retrieved_at` 与 `published_at` 严格分离 |
| 3 | 严格 Live 仍写"报告中该价格为离线样例" | `risk.py::rule_based_risks` 文案写死；`report.py::_price_block` 行标签带"（离线样例）"；`graph.fetch_prices` 仍把 fixture 数值塞进 `optional_market` | `data_status` 统一；`sanitize_optional_entry()` 在**分析前**剔除数值；§4.1 行为 `DATA_MISSING` + `—`；规则与 prompt 文案统一 |
| 4 | 只有标题的新闻也能生成经营结论 | 条目完全没有证据等级字段，prompt 不区分标题/摘要/正文 | 新增 `source_type`/`evidence`/`official_verified`/`fetch_status`/`evidence_reason`；`entities.assess_evidence()` 判定矩阵；prompt 按等级限制分析深度 |
| 5 | 2023 年报告缺少时效表达 | 注册表三个日期未区分语义，`graph.resolve_source` 按**文件顺序**取第一条匹配 | `pdf/registry.py::source_metadata()` 三日期 + `age_days`/`is_historical`；`select_source()` 取公告日期最新的已验证条目；§2 显示"距今 N 天（历史数据）" |
| 6 | 缺统一质量校验 | `state["unverified"]` 被渲染器读取但**从未写入** | 新增 `agent/quality.py`，渲染前 11 项检查，auto_fix / warn / error 三档 |

### 二、四项技术修订的落地方式

| 修订 | 实现 |
| --- | --- |
| 稳定 news_id | 新模块 `news/ids.py`：`normalize_url()`（保留路径原样、去掉 `utm_*`/`oc`/`hl`/`gl`/`ceid` 等跟踪参数）+ `sha256[:16]`；Google 跳转只能用 title+source 生成 `nid-x-` 降级 ID，**绝不猜测目标 URL**；`display_no`（C1/I1）只在渲染期生成，不作为证据键 |
| 来源属性 ≠ 官方公告 | `data/sources/entities.json` 增加全局/公司 `official_domains` 与 `disclosure_markers`；`official_disclosure` 必须同时满足「官方域名 + 抓取成功 + 正文达标 + 披露标记 + 实体匹配」，官方域名下的普通页面降级为 `full_article` |
| data_status 统一 | `provenance.envelope(..., data_status=...)` 统一注入，取值只有 `OK` / `DATA_MISSING`（provenance 仍四态）；缺失数据不带任何数值进入 DeepSeek |
| 结构化风险 Claim | `llm.RiskClaim`/`RiskCalculation`；`agent/evidence.py` 构建确定性证据注册表（`price_spot_001`、`price_trend_001`、`resource_summary_001`、`resource_inferred_001` + 每条新闻的 `news_id`）；`rule_claims()` 生成确定性 claim；`repair_claims()` 按证据重算数值并剔除无证据/口径混用/证据不足/重复的模型结论；`validate_claims()` 做结构校验 |

### 三、渲染前质量校验（11 项）

硬错误（strict-live 下并入 `live_gate_failures` → 退出码 2）：互斥分类不闭合、严格 Live 混入非 live 数值、`DATA_MISSING` 带数值、资源量与储量混用、主表重复汇总、新闻证据字段缺失、claim 无法追溯。
可确定性修正 / 警告：去重阶段数不闭合（重算）、`as_of` 重算、非法或未来 `published_at` 剔除、`news_id` 补算、PDF 历史数据标记、行情口径说明缺失。

**实测：本轮真实运行中门禁抓到并自动修复了 DeepSeek 的两个缺陷** —— `risk_spodumene_price_missing_003`（无证据）与 `risk_price_spot_002`（与规则结论重复），修复记录写入报告 §8（`docs/run-daily.out` 保留原始输出）。修复前这两条会直接让严格 Live 失败（`quality.claim_without_evidence` / `quality.claim_overreach`）。

### 四、正文抓取时间预算

| 参数 | 取值 |
| --- | --- |
| 抓取上限 | 8 篇（公司直链优先） |
| 并发 | 3（`asyncio.Semaphore`） |
| 单篇上限 | 45s（`asyncio.wait_for`；MCP 内部仍 30s + tenacity 3 次） |
| 整体预算 | 120s；超预算的条目记 `budget_exhausted` 且**不发起请求** |

实测（`tests/test_fetch_budget.py`）：并发峰值恰为 3；预算耗尽时 `body_attempted + body_budget_exhausted == 8` 且没有任何请求发出；超时与失败分别记 `timeout`/`failed`，并按标题/摘要回退证据等级。

### 五、本轮测试结果（全部在最终代码上实跑）

```
uv run --no-sync pytest -m "not live" -q   → 204 passed, 13 deselected（连跑两次，用例集合 sha256 一致）
uv run --no-sync pytest -m live -q         → 13 passed, 0 failed, 0 skipped（302s）
docker compose up --build --abort-on-container-exit --exit-code-from agent → agent-1 exited with code 0
uv run --no-sync python scripts/verify_report.py → 22/22 checks passed
```

新增用例：`test_news_pipeline.py` 8、`test_news_evidence.py` 8、`test_risk_claims.py` 17、`test_quality_gate.py` 14、`test_fetch_budget.py` 5、`test_pdf_timeliness.py` 5（离线 147 → 204）。`scripts/verify_report.py` 由 16 项扩展到 22 项。

### 六、修复前后对比（同一主题、同一天）

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 新闻统计 | `原始 63 → 公司 7 / 行业 12 / 过滤 29`（合计 48，15 条去向不明） | 流水表：抓取 63 → 去重移除 5 → 唯一 58；公司 7 / 行业 21 / 无关 30（= 58）；行业候选另有 9 条因展示上限未展示；闭合校验一致 ✓ |
| `news.as_of` | 2026-10-13T23:15:02（只有公司新闻） | 2026-10-15T04:49:00（全部展示条目最新发布时间），并单列 company/industry as_of |
| 锂辉石报价 | §4.1 标"（离线样例）"、provenance `fixture`；§5.1/§5.2 反复提"离线样例" | §4.1 `DATA_MISSING`、provenance `—`、缺失原因写入；风险文案统一为"本次无可验证真实报价，未使用离线模拟价格" |
| 新闻证据 | 只有"来源 · 情绪" | 每条显示"来源 · 证据：仅标题 / RSS 摘要 / 完整正文 · 情绪 · news_id" |
| 风险结论 | 自然语言数字，无证据链接 | §5.3 结论—证据对照表（claim_id / 数值 / 证据 ID 或链接），数值按证据重算校验 |
| 资源量时效 | 仅"资源量生效日期 2023-06-30" | 三日期 + "距今 1203 天（历史数据，非当前最新披露）" |

### 七、仍然存在的限制

- `pls.com`、ASX 公告主机在本机网络不可达（403 / TLS reset），本轮**未**联网寻找 2026 年新报告；Pilbara 资源量仍为 2023-08-24 披露口径，已在报告与本节标注为历史数据。
- 锂辉石精矿 SC6.0 无公开免费真实报价 → 严格 Live 下为 `DATA_MISSING`；fixture 仍保留给离线测试，但不会进入严格模式的分析或表格。
- 新浪报价是第三方行情转述，不是 LME / 交易所官方 API 数据。
- 本轮公司直接相关新闻全部为 `rss_summary` 级（Google News 跳转无法取正文），因此经营类结论被强制降级为"待核实"——这是外部数据源限制，不是解析器缺陷；一旦拿到 `full_article` 或 `official_disclosure`，同一链路会正常据此分析。
- 质量门禁对"模型违规"的策略是**确定性修复 + 记录**（剔除无证据/重复/口径混用的结论）；只有无法修复的数据口径错误才会拒绝出报告。

---

## 2026-10-15 `--chat` DeepSeek 400（json_object）修复 ✅

### 现象

```
uv run minereport-agent --chat
你> 你好
BadRequestError: Prompt must contain the word 'json' in some form to use 'response_format' of type 'json_object'.
```

### 根因（精确到一行）

`src/minereport/agent/llm.py::DeepSeekBackend.__init__` 把 JSON 模式设置在**共享客户端**上：

```python
model_kwargs={"response_format": {"type": "json_object"}}
```

于是**每一次**调用都会携带 `response_format=json_object`。`general_chat` 的路径是
`conversation.py::chat` → `backend.text(CHAT_SYSTEM, prompt)`，聊天提示词里没有 "json"，DeepSeek 直接返回 400。
结构化调用因为会拼接 `schema_instruction()`（内含 "JSON Schema"）才没有触发，所以问题只在聊天路径暴露。

### 修复

| 位置 | 改动 |
| --- | --- |
| `agent/llm.py` | 客户端不再设置 `response_format`；改为**按调用绑定** `self._json_llm = self._llm.bind(response_format={"type": "json_object"})`，只有 `structured()` 使用它 |
| `agent/llm.py::_complete(..., json_mode=...)` | 显式区分两种模式：`json_mode=True` 走 `_json_llm`；普通聊天走原始客户端，**完全不携带 response_format** |
| `agent/llm.py::JSON_MODE_GUARD` | 硬保证：进入 JSON 模式时若 system+user 不含 "json"，自动追加"请只输出一个 JSON 对象…"，杜绝同类 400 |
| `agent/llm.py::redact()` + `stage=` | 错误信息带调用阶段并脱敏：`LlmError("[general_chat] BadRequestError: … sk-***")`，绝不打印 API Key |
| 调用点 | 标注阶段：`general_chat`、`news_digest`、`risk_section`、`intent_classifier`、`task_matcher` |
| thinking 配置 | 保持不变（仍为客户端 `extra_body={"thinking":{"type":"disabled"}}`），不会退回先前的 tool_choice 400 |

### 实际验证（真实 DeepSeek `deepseek-flash`）

```
backend.text("…", "用户：你好")        → "你好！我是矿业智能助手，可以回答矿业相关问题，也能帮你解决编程…"
backend.text("…", "用户：Python 协程是什么") → "Python 协程是一种可以在执行过程中暂停和恢复的函数…"
backend.structured(NewsDigest, …)      → JSON 校验通过，返回 1 条摘要
```

CLI `--chat`（真实模型 + 真实 MCP，session `35bc2d2d…`）：

```
你> 你好              → general_chat，自然语言回复，未调用 MCP
你> Python 协程是什么  → general_chat，未调用任何 MCP 工具
你> 查询今天的铜价     → price_query → get_price(copper, 2026-10-15)
                        → LME 铜 3M（伦铜）14348.0 USD/t（provenance=live，HTTP 200）
你> 帮我查一下锂价     → 追问"你想查碳酸锂还是锂辉石精矿？"
你> 碳酸锂            → 同一 task_id=fc6541ea…，继续追问日期
你> /state            → tasks: 2（铜价已完成；锂价任务等待日期）
stderr 中 json_object / BadRequestError / 400 命中数：0
```

### 回归测试（新增 `tests/test_llm_modes.py`，7 项）

| 用例 | 断言 |
| --- | --- |
| `test_plain_chat_sends_no_response_format` | `text()` 的请求里没有 `response_format`，共享客户端也没有；消息里不含 "json" |
| `test_structured_call_binds_json_mode_and_says_json` | `structured()` 的请求携带 `{"type":"json_object"}`，且消息含 "json" / "JSON Schema" |
| `test_json_mode_guard_adds_the_word_json_even_if_the_prompt_lacks_it` | 提示词缺 "json" 时发送前自动补上 |
| `test_thinking_stays_disabled_for_both_modes` | 两种模式都保持 `extra_body={"thinking":{"type":"disabled"}}` |
| `test_error_message_names_the_stage_and_redacts_the_key` | `LlmError` 含 `[general_chat]`，密钥被替换为 `sk-***` |
| `test_structured_error_also_names_its_stage` | 结构化失败含 `[structured:NewsDigest]` |
| `test_stub_backend_accepts_the_same_keyword_arguments` | 离线 stub 兼容 `stage=` |

`tests/test_llm_strict.py::test_deepseek_backend_keeps_thinking_disabled_without_a_global_json_mode`
替换了原先断言"客户端级 `model_kwargs.response_format`"的用例——那条断言锁定的正是本次的 bug。

### 测试结果

```
uv run --no-sync pytest -m "not live" -q   → 211 passed, 13 deselected（连跑两次，用例集合 sha256 一致）
uv run --no-sync pytest -m live -q         → 13 passed, 0 failed, 0 skipped（287s）
```

离线 204 → 211（新增 7 项 LLM 模式回归）。
