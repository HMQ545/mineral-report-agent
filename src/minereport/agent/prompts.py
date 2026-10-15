"""Prompts for the two LLM steps (news digest and risk narrative)."""

from __future__ import annotations

NEWS_SYSTEM = (
    "You are a mining-industry analyst. Summarise only what the supplied items "
    "actually say. Never invent facts, numbers, quotes or sources. If an item has "
    "little content, say so briefly. Reply with the requested JSON object only."
)

NEWS_USER_TEMPLATE = """主题：{topic}
商品：{commodity}
日期：{today}

材料分两类：
- company_items：与目标公司/项目**直接相关**的新闻（有公司正式名称、股票代码或项目名称作为证据）
- industry_items：锂行业背景新闻（与目标公司没有直接关系）

每条材料都带 `news_id`、`source_type`（official/media/rss/unknown）、`evidence`、`fetch_status`：
- `evidence=title_only`：只能复述标题明确表达的信息，不得补充细节；
- `evidence=rss_summary`：不得超出标题与摘要支持的范围；
- `evidence=full_article`：可以依据 `excerpt` 正文内容分析；
- `evidence=official_disclosure`：可以引用公告中的事实；
- `evidence=unverifiable`：只写"尚无法确认"。

写作要求：
- 每条 item 的 summary 用**中文**写 1-2 句，说明谁做了什么、对目标公司或项目有什么影响；
- sentiment 相对目标公司经营基本面判断：positive / neutral / negative；
- 不要逐字翻译英文标题，不要编造材料中没有出现的数字或结论；
- **不得**由股价、评级或板块行情类新闻推断产量下降、成本上升、停产、扩产等经营事实；证据不足时写"尚无法确认"；
- 聚焦产量、成本、价格、项目进展、监管与交易；不要评论市场八卦；
- items 必须**覆盖输入里的每一条**（company_items 与 industry_items 都要），保持输入顺序，并原样复制该条的 url 与 news_id；
- 如果 company_items 为空，overall 必须明确写出"检索期内暂无可验证的公司直接相关新闻"。

```json
{payload}
```
"""

RISK_SYSTEM = (
    "You are a mining risk reviewer. You may only reason from the supplied, "
    "evidence-backed facts. Do not introduce new numbers. Do not soften or hide "
    "data-quality problems: if a dataset is not live, say so plainly. Write in "
    "Chinese. Reply with the requested JSON object only."
)

RISK_USER_TEMPLATE = """主题：{topic}
商品：{commodity}
生成时间：{generated_at}

由确定性规则确立的事实（不得与之矛盾）：
```json
{rules}
```

证据注册表（**只能**引用这里存在的 ID，不得编造 ID、页码、价格序列或来源）：
```json
{evidence_index}
```

证据包（数值摘要）：
```json
{evidence}
```

请用**中文**写 3-5 条风险要点（bullets）、一段简短综合判断（judgement），以及结构化的 `claims`：
- 每条含数值的关键结论都要给 `claim_id`（形如 `risk_price_trend_002`）、`claim`、`value`、`unit`；
- `evidence_ids` 只能引用上面证据注册表里真实存在的 ID（新闻用其 `news_id`，行情用 `price_spot_001`/`price_trend_001`，资源量用 `resource_summary_001`/`resource_inferred_001`）；
- 可复算的指标要给出 `calculation`：涨跌幅用 `{{"method":"percentage_change","start_value":…,"end_value":…}}`，占比用 `{{"method":"share","numerator":…,"denominator":…}}`；
- 碳酸锂是下游参考价，**不得**把它当作 Pilbara 锂辉石精矿的实际销售价格或盈利依据；
- `data_status=DATA_MISSING` 的商品没有任何可用数值，不得据此量化涨跌、收入、成本或利润；照抄上面的缺失表述；
- 找不到充分证据的结论请降级为"待核实"或不要写入。
只能使用上面出现过的数字。
"""