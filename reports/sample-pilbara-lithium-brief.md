# Pilgangoora Operation（Pilbara Minerals Limited） · 矿权日报

| 项目 | 值 |
| --- | --- |
| 查询 | `给我生成一份关于 Pilbara 锂矿的今日简报` |
| 生成时间（Asia/Shanghai） | 2026-10-14T23:59:25+08:00 |
| 新闻检索窗口 | 近 7 天 |
| 主商品 | `lithium_carbonate` |
| LLM 模式 | deepseek |
| 验收模式 | require-live（严格，非实时数据不得作为结论依据） |

> **数据可信度声明**：live = 本次真实获取；cached = 此前真实获取后的本地缓存；fixture = 仓库内置离线样例；synthetic = 程序合成。任何非 live 数据在本报告中都带有明确徽标，并且不会被当作实时结论依据。

## 0. 数据来源与可信度

| 数据集 | provenance | 来源 | 数据日期 | 获取时间(UTC) | 备注 |
| --- | --- | --- | --- | --- | --- |
| news | `live` | public-rss:google-news-rss+direct-feeds | 2026-10-14T07:00:00+00:00 | 2026-10-14T15:59:36+00:00 | google-news-rss returned 6 item(s)；direct-feeds returned 4 item(s) |
| reserves | `live` | live download | 2023-06-30 | 2026-10-14T15:59:37+00:00 | 命中源注册表条目 pilbara-2023-mineral-resource（role=primary-deliverable）；Pilbara Minerals 官方域名（pls.com / pilbaraminerals.com.au）在本机网络返回 403，ASX 公告主机 TLS 被重置；本文件为 minedocs.com 托管的同日披露原文镜像，内容归属仍为 Pilbara Minera |
| price | `live` | sina:hq.sinajs.cn | 2026-10-14 | 2026-10-14T15:59:28+00:00 | 下游市场参考价，不是 Pilbara 锂辉石精矿的实际销售结算价 |
| trend | `live` | sina:hq.sinajs.cn | 2026-10-14 | 2026-10-14T15:59:28+00:00 | 按实际交易日返回；周末与交易所假期没有报价属于正常情况。；coverage_vs_weekdays 以工作日为分母估算，未内置交易所假期日历，因此是下界。；下游市场参考价，不是 Pilbara 锂辉石精矿的实际销售结算价 |



### 0.2 运行备注与异常

- news digest LLM step failed: BadRequestError: Error code: 400 - {'error': {'message': 'Thinking mode does not support this tool_choice (request_id: d7faf26e-6087-4a6f-9634-c9a382918fb5)', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_request_error'}}
- risk LLM step failed: BadRequestError: Error code: 400 - {'error': {'message': 'Thinking mode does not support this tool_choice (request_id: ea485562-249e-439f-8e5f-49ec4dc5137e)', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_request_error'}}

## 1. 新闻摘要



**Key facts: LSE:RIO Oyu Tolgoi +29% YTD; $2B+ disposals; Pilbara 323–338Mt - TradingView**（2026-10-14T07:00:00+00:00）
  - 来源：TradingView · 情绪：`unknown`
  - 摘要：Key facts: LSE:RIO Oyu Tolgoi +29% YTD; $2B+ disposals; Pilbara 323–338Mt TradingView
  - 链接：https://news.google.com/rss/articles/CBMixwFBVV95cUxPdHZ0T3MtcGZINzcwdXZhUHNGQnZfVEdXRnQya1U0LS16RFdWV0F0eXl4bTFvUVVacnRZNVlDT2ktTl9NMHMzMHJ1RlNxZHFjSHJJWnFSclQtOGNEWlRqWXVFVFNadXJ0WWVwb2VSQmtvc1kwakwweGktbHZWMDBKekpyRlhoSmxQZzRZTGdtZFRqQmRRQmhHUDlPdDBlN2oyZDBVYjJKNUo5c2pKdFViU1VQODlzRjBhV1VyQktTRl9yeU9jcW1R?oc=5
**Rio Tinto reports 85.7 million tonnes of Pilbara sales; stock pre-market at EUR 83.63 - ad-hoc-news.de**（2026-10-14T06:12:25+00:00）
  - 来源：ad-hoc-news.de · 情绪：`unknown`
  - 摘要：Rio Tinto reports 85.7 million tonnes of Pilbara sales; stock pre-market at EUR 83.63 ad-hoc-news.de
  - 链接：https://news.google.com/rss/articles/CBMiyAFBVV95cUxOb19KeVBjSDNDZDN5Qm03TmFTN1pmVlRobVBSVTF6OWJISFE4eGVzWUtBczlNRGxjYnUyaUwzbTcycWtvTElXak5sLVdydTN1dkRnX01ueWZCa1ppUnB0ZDVNY0hvek1lYTE2WHJGWXdCZTBFcjVDWWFVeHNkV1haX2NGT1EwNFZqLW9FMXU5TzlGa3R5YzZ6RTFjcmlycnNyQk5SWjVyc0ozSDAwWGd6Z1lMU0c1alJqX0VwdGV6QzEyREc3cGxCeQ?oc=5
**Rio Tinto reports strong Pilbara iron ore performance as major projects progress - Australian Mining**（2026-10-14T00:04:57+00:00）
  - 来源：Australian Mining · 情绪：`unknown`
  - 摘要：Rio Tinto reports strong Pilbara iron ore performance as major projects progress Australian Mining
  - 链接：https://news.google.com/rss/articles/CBMiuAFBVV95cUxQUmkyeFhRR2F3YTFIbnpia0xHald3VHY4VGxnQjNrR2l0Um40SnBfQVRvT3JVMXNSSk41WkoweE5Pd0ZNOHQ3TTBsODk4SFM3WjhTR19Md0tmaDdiQ0lMWjd4SElzSC1sLTh1QU9tanZYSzg4ZDA4S0xsOUhKT2lCQy0yLUhrN2VWWmtMdWp4TEJRTzdiLW1fYWNWSjNHX29pTEhIUlRfSEE1RXVWOVVxdWttaVNsdmFB?oc=5
**Pilbara Gold (ASX:PGL) Expands Terra Gold Mineralisation at Roe Hills - Kalkine**（2026-10-13T04:24:00+00:00）
  - 来源：Kalkine · 情绪：`unknown`
  - 摘要：Pilbara Gold (ASX:PGL) Expands Terra Gold Mineralisation at Roe Hills Kalkine
  - 链接：https://news.google.com/rss/articles/CBMiowFBVV95cUxQb0V1TENrbmd1THJwZDNNV1JoSzI3LUIxN3pwRThqR3lNRkg2bzJ4ZFhNVndYeXZ1TnJaSXNrVWtIUmllZ3d5NDJKRHZpaTJnTXRqMTg5NjBYcTN4WHFIRFZpcEVfWHFfZU5idTdZdmtFazBCaUZnYXhpVEduMU9BVkNpV1BhcWpWczVBdjIyUTNXV0NJem5RTjdub29yUGhvdlFV?oc=5
**EnergyX inks 10-year, $2B lithium offtake deal with Trafigura**（2026-10-12T17:40:02+00:00）
  - 来源：direct-feed:mining.com · 情绪：`unknown`
  - 摘要：The deal covers lithium products from both Black Giant in Chile project Lonestar in Texas.
  - 链接：https://www.mining.com/energyx-inks-10-year-2b-lithium-offtake-deal-with-trafigura/
**Lithium Triangle’s vast resources face unsolved extraction hurdles**（2026-10-09T13:53:00+00:00）
  - 来源：direct-feed:mining.com · 情绪：`unknown`
  - 摘要：A review of 43 Andean salt flats highlights how brine chemistry and water availability shape lithium development prospects.
  - 链接：https://www.mining.com/lithium-triangles-vast-resources-face-unsolved-extraction-hurdles/
**UK scientists turn to biology to unlock cleaner lithium**（2026-10-08T12:49:00+00:00）
  - 来源：direct-feed:mining.com · 情绪：`unknown`
  - 摘要：The project aims to separate battery metals using engineered proteins with precisely controlled pores.
  - 链接：https://www.mining.com/uk-scientists-turn-to-biology-to-unlock-cleaner-lithium/
**CleanTech Lithium expands foothold in Argentina**（2026-10-08T12:03:00+00:00）
  - 来源：direct-feed:mining.com · 情绪：`unknown`
  - 摘要：New lithium brine properties near Laguna Verde offer growth potential as the developer targets exploration in 2027.
  - 链接：https://www.mining.com/cleantech-lithium-expands-foothold-in-argentina/

**整体判断**（unavailable）：模型不可用，仅列出原始条目，未做情绪与要点分析。

### 1.1 正文样本（真实抓取）

- EnergyX inks 10-year, $2B lithium offtake deal with Trafigura（https://www.mining.com/energyx-inks-10-year-2b-lithium-offtake-deal-with-trafigura/，539 词）
- Lithium Triangle’s vast resources face unsolved extraction hurdles（https://www.mining.com/lithium-triangles-vast-resources-face-unsolved-extraction-hurdles/，663 词）
- UK scientists turn to biology to unlock cleaner lithium（https://www.mining.com/uk-scientists-turn-to-biology-to-unlock-cleaner-lithium/，498 词）

## 2. 矿产资源量（Mineral Resources）



- 报告主体：Pilbara Minerals Limited
- 项目：Pilgangoora Operation
- 披露/生效日期：2023-06-30
- 标准：JORC
- 文档：https://minedocs.com/25/Pilbara-Minerals-MR-08242023.pdf
- 文档 SHA-256：`db8b9740f60deab7…`（页数 56）
- 标准声明证据（p2）：(30 June 2022) 0.8% 0.6% 0.4% - 100 200 300 400 500 Mineral Resource informing Ore Reserve update The updated JORC 2012 Ore Reserve incorporates drilling data acquired through exploration campaigns completed by Pilbara Minerals between Novem
- 托管说明：Pilbara Minerals 官方域名（pls.com / pilbaraminerals.com.au）在本机网络返回 403，ASX 公告主机 TLS 被重置；本文件为 minedocs.com 托管的同日披露原文镜像，内容归属仍为 Pilbara Minerals Limited。

| 分类 | 吨位 | 品位 | 含金属量 | 页码 | 原文证据 |
| --- | --- | --- | --- | --- | --- |
| Measured | 22.1 Mt | 1.34 % | 0.3 Mt | p2 | Measured 22.1 1.34 146 0.44 0.3 7 |
| Indicated | 315.2 Mt | 1.15 % | 3.6 Mt | p2 | Indicated 315.2 1.15 106 0.53 3.6 74 |
| Inferred | 76.6 Mt | 1.07 % | 0.8 Mt | p2 | Inferred 76.6 1.07 124 0.54 0.8 21 |
| Indicated | 131.8 Mt | 1.28 % | 1,682,000 t | p56 | Indicated 131.8 1.28 101 0.52 1,682,000 29.3 |
| Inferred | 20.1 Mt | 1.3 % | 262,000 t | p56 | Inferred 20.1 1.30 104 0.57 262,000 4.6 |
| Indicated | 43.4 Mt | 1.27 % | 550,000 t | p56 | Indicated 43.4 1.27 216 0.56 550,000 20.7 |
| Inferred | 24.9 Mt | 1.3 % | 323,000 t | p56 | Inferred 24.9 1.30 195 0.44 323,000 10.7 |
| Indicated | 8.1 Mt | 1.36 % | 109,000 t | p56 | Indicated 8.1 1.36 94 0.61 109,000 1.7 |
| Inferred | 0.5 Mt | 1.16 % | 6,000 t | p56 | Inferred 0.5 1.16 67 0.84 6,000 0.1 |
| Indicated | 104.3 Mt | 0.97 % | 1,016,000 t | p56 | Indicated 104.3 0.97 70 0.52 1,016,000 16.0 |
| Inferred | 18.3 Mt | 0.64 % | 116,000 t | p56 | Inferred 18.3 0.64 63 0.56 116,000 2.5 |
| Indicated | 10 Mt | 0.89 % | 89,000 t | p56 | Indicated 10.0 0.89 74 0.50 89,000 1.6 |
| Inferred | 0.7 Mt | 0.52 % | 4,000 t | p56 | Inferred 0.7 0.52 71 0.32 4,000 0.1 |
| Indicated | 5.8 Mt | 0.74 % | 43,000 t | p56 | Indicated 5.8 0.74 112 0.73 43,000 1.4 |
| Inferred | 4.4 Mt | 0.77 % | 34,000 t | p56 | Inferred 4.4 0.77 109 0.81 34,000 1.1 |
| Indicated | 6.6 Mt | 1.24 % | 82,000 t | p56 | Indicated 6.6 1.24 136 0.52 82,000 2.0 |
| Inferred | 5.3 Mt | 0.93 % | 49,000 t | p56 | Inferred 5.3 0.93 117 0.56 49,000 1.4 |
| Inferred | 74.2 Mt | 1.07 % | 794,000 t | p56 | Inferred 74.2 1.07 125 0.54 794,000 20.4 |
| Indicated | 5.1 Mt | 1.31 % | 67,000 t | p56 | Indicated 5.1 1.31 89 0.61 67,000 1.0 |
| Inferred | 2.4 Mt | 0.98 % | 23,000 t | p56 | Inferred 2.4 0.98 100 0.74 23,000 0.5 |
| Indicated | 315.2 Mt | 1.15 % | 3,639,000 t | p56 | Indicated 315.2 1.15 106 0.53 3,639,000 73.7 |
| Inferred | 76.6 Mt | 1.07 % | 817,000 t | p56 | Inferred 76.6 1.07 124 0.54 817,000 21.0 |

> 说明：Measured / Indicated / Inferred 属于**矿产资源量**，不是矿石储量；两者在本报告中严格分列。

## 3. 矿石储量（Ore Reserves）



| 分类 | 吨位 | 品位 | 含金属量 | 页码 | 原文证据 |
| --- | --- | --- | --- | --- | --- |
| Proved | 19.1 Mt | 1.32 % | 0.3 Mt | p3 | Proved 19.1 1.32 133 0.92 0.3 6 |
| Probable | 195.1 Mt | 1.18 % | 2.3 Mt | p3 | Probable 195.1 1.18 100 1.00 2.3 42 |

> 说明：Proved / Probable 属于**矿石储量**，是在经济可采假设下的结果，与矿产资源量不可混用。

## 4. 价格走势

- 标的：碳酸锂（广期所主力连续）（GFEX）
- 合约/规格：GFEX lithium carbonate continuous, CNY/t
- 最新报价：**100,900 CNY/t**（2026-10-14，daily close (settled)）
- 前一交易日：107,060 CNY/t · 变动：-5.75%
- 来源：sina:hq.sinajs.cn · https://hq.sinajs.cn/list=nf_LC0
- 来源说明：广州期货交易所碳酸锂期货连续合约
- 口径说明：下游市场参考价，不是 Pilbara 锂辉石精矿的实际销售结算价

**近 60 天走势**（2026-08-17 ~ 2026-10-14，37 个交易日）

`▇▇▆▆▇▇▆▆▆▇█▇▇▆▅▅▅▅▅▄▄▄▃▄▄▄▄▄▃▃▃▃▂▂▁▁▁`

区间涨跌：-34.27% · 最低 100,900 · 最高 161,000 CNY/t

| 日期 | 收盘 |
| --- | --- |
| 2026-09-16 | 125,820 |
| 2026-09-17 | 131,180 |
| 2026-09-18 | 127,160 |
| 2026-09-21 | 132,500 |
| 2026-09-22 | 134,400 |
| 2026-09-23 | 128,900 |
| 2026-09-24 | 124,420 |
| 2026-09-28 | 119,040 |
| 2026-09-29 | 118,780 |
| 2026-09-30 | 118,420 |
| 2026-10-08 | 117,300 |
| 2026-10-09 | 117,000 |
| 2026-10-12 | 104,740 |
| 2026-10-13 | 107,060 |
| 2026-10-14 | 100,900 |

### 4.1 其他相关市场

| 标的 | 价格 | 单位 | 日期 | provenance | 说明 |
| --- | --- | --- | --- | --- | --- |
| 锂辉石精矿 SC6.0 CIF（离线样例） | 1,180 | USD/t | 2026-10-10 | `fixture` | 锂辉石精矿 SC6.0 CIF 没有公开免费真实数据源。本文件是人工编写的离线样例，只在演示模式使用；live 验收模式下该商品必须报告为 DATA_MISSING。；仅演示模式可用；live 验收模式必须标记为 DATA_MISSING，不得当作真实行情。 |

## 5. 风险提示

### 5.1 规则触发（确定性）

| 级别 | 代码 | 说明 |
| --- | --- | --- |
| high | `price_drawdown` | 近 60 天下游参考价下跌 -34.27%，价格风险显著。 |
| medium | `price_daily_move` | 最近交易日环比变动 -5.75%，短期波动偏大。 |
| medium | `spodumene_unavailable` | 锂辉石精矿暂无公开免费真实价格源，报告中该价格为离线样例，不代表实际销售价。 |

### 5.2 综合判断（deepseek）

模型不可用，本节仅列出确定性规则触发结果，未生成综合判断。

## 6. 引用源链接

- [新闻数据源] https://news.google.com/rss/search
- [资源量文档] https://minedocs.com/25/Pilbara-Minerals-MR-08242023.pdf
- [行情数据源] https://hq.sinajs.cn/list=nf_LC0
- [走势数据源] https://hq.sinajs.cn/list=nf_LC0
- [新闻正文] EnergyX inks 10-year, $2B lithium offtake deal with Trafigura — https://www.mining.com/energyx-inks-10-year-2b-lithium-offtake-deal-with-trafigura/
- [新闻正文] Lithium Triangle’s vast resources face unsolved extraction hurdles — https://www.mining.com/lithium-triangles-vast-resources-face-unsolved-extraction-hurdles/
- [新闻正文] UK scientists turn to biology to unlock cleaner lithium — https://www.mining.com/uk-scientists-turn-to-biology-to-unlock-cleaner-lithium/

## 7. 数据缺失与限制

- 下游市场参考价，不是 Pilbara 锂辉石精矿的实际销售结算价
- 按实际交易日返回；周末与交易所假期没有报价属于正常情况。
- coverage_vs_weekdays 以工作日为分母估算，未内置交易所假期日历，因此是下界。
- 下游市场参考价，不是 Pilbara 锂辉石精矿的实际销售结算价
- Pilbara Minerals 官方域名（pls.com / pilbaraminerals.com.au）在本机网络返回 403，ASX 公告主机 TLS 被重置；本文件为 minedocs.com 托管的同日披露原文镜像，内容归属仍为 Pilbara Minerals Limited。
- news digest LLM step failed: BadRequestError: Error code: 400 - {'error': {'message': 'Thinking mode does not support this tool_choice (request_id: d7faf26e-6087-4a6f-9634-c9a382918fb5)', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_request_error'}}
- risk LLM step failed: BadRequestError: Error code: 400 - {'error': {'message': 'Thinking mode does not support this tool_choice (request_id: ea485562-249e-439f-8e5f-49ec4dc5137e)', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_request_error'}}

---

*本报告由 minereport Agent 生成。数据来源与 provenance 全部记录在 `docs/VERIFICATION.md` 与 `data/sources/*.json`；非 live 数据一律标注，不以模拟数据冒充真实数据。*