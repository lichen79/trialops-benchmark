# 测试设计文档 — Bedrock 智能体矩阵

**本文档：[English](TESTDESIGN.md) · 中文 · [Français](TESTDESIGN.fr.md)**

本文档解释 `demo/` 基准测试的设计方式、每项设计决策的缘由，以及如何继续：
验证、运行、扩展与解读。它与 `README.md`（结果）、`docs/USERGUIDE.md`
（操作）和 `out-live/REPORT.md`（最新测量分析）相互补充。

## 阅读方式

**请先阅读前部。** 它用较直观的语言解释设计目标、一次运行的完整过程，以及
六个业务任务分别考验什么。**附录**保留精确的函数契约、测试压力点、Harness
内部机制、扩展指南与数据模式，供需要实现或复现时查阅。

## 1. 设计目标

在 Amazon Bedrock 上，用**同一套真实编码环境、相同的 Prompt、工具和执行
限制**，公平比较四个 AI 模型完成 Python 工程任务的能力。

这不是简单地让模型回答编程问题，而是让它像真正的 IT Engineer 一样：阅读
现有代码、理解项目结构和需求、制定方案、修改或新增代码、执行测试、检查
错误并继续修复。最后由独立、权威的隐藏 pytest 测试集判断任务是否真正完成。

> **最重要的设计理念：不评价模型说得有多好，而评价它交付的代码能不能
> 通过测试。**

每次运行最多 **30 次模型调用**、**20 分钟**，完成预算为 **16,384 token**。
所有模型使用同一 System Prompt、同一 Agent Loop 和六个工具：
`list_files`、`read_file`、`write_file`、`edit_file`、`run_tests`、`finish`。

每轮测量为：

```text
6 个任务 × 4 个模型 × 3 次重复 = 72 次运行
```

仓库目前包含两轮独立测量：官方矩阵（2026-11-13）与现场复测
（2026-11-14），合计 144 次运行。

## 2. 一次运行实际测试什么？

1. **阅读**现有代码与项目结构。
2. **分析需求**，制定实现方案。
3. **修改或新增** Python 代码。
4. **执行测试**并检查错误。
5. **根据测试结果继续修复**，直到完成或触及限制。
6. **独立评分**：把模型对生产代码的改动应用到干净副本，再执行权威可见与
   隐藏测试。

可见测试帮助模型理解基本行为；隐藏测试防止它仅靠“看起来合理”的实现或
自己编写的测试得分。模型自写的测试与 pytest 配置绝不会进入评分副本。

## 3. 一个系统、六个模块、两条跨模块集成关系

六个 Job 并不是互不相关的编程题，而是虚构的**临床试验运营系统**
（Clinical Trial Operations）的六个业务模块：

```text
Trial Operations Platform
├── T1 · Eligibility   — 受试者资格审核
├── T2 · Privacy       — 隐私保护与研究数据导出
├── T3 · Audit         — 审计日志、防篡改与恢复
├── T4 · Scheduling    — 预约排期与容量管理
├── T5 · Safety        — 安全性事件随访分诊
└── T6 · Governance    — 数据复用审批工作流

T2 Privacy → T6 Governance：数据导出必须经过复用审批，不能绕过治理流程。
T4 Scheduling → T1 Eligibility：预约之前必须先确认受试者符合参与条件。
```

每个 Job 都从独立基础功能开始，较难的 Job 再增加集成扩展。这样既能看出
模型是否会写一个 Python Function，也能看出它是否理解业务规则、能阅读多个
模块并正确完成系统集成。T2 与 T4 是最明确的跨模块案例；T3 增加审计链的
可信恢复，T6 则提供 T2 必须使用的 executor 接口。

## 4. 用业务语言理解六个 Job

### T1 — Eligibility：受试者资格审核

- **业务场景。** 判断受试者在指定日期是否符合某个 Trial Protocol 的参加
  条件，检查知情同意、年龄、化验指标与排除标记。
- **需要实现。** `evaluate_subject(subject, protocol, on)` 返回是否合格、
  按指定顺序排列的失败原因，以及选中的知情同意版本。
- **工程难点。** 必须选取最新同意，不能因最新版本不合格就退回旧版本；
  年龄要精确到指定日期，不能简单用年份相减；处理闰日生日与当日签署；
  年龄和化验上下限均含边界；错误原因必须严格按序返回；即使选中的版本
  不合格，也要返回该版本。
- **考察能力。** 小型业务规则引擎：日期处理、条件判断、输入校验与确定性
  输出。

### T2 — Privacy：隐私保护与研究数据导出

- **业务场景。** 将临床试验数据用于另一项研究，但不能泄露姓名、Email、
  完整出生日期、受试者 ID、知情同意记录或密钥。
- **需要实现。** `export_subjects` 按研究目的、撤回日期和保留期限过滤；
  使用 HMAC-SHA256 对 `purpose:id` 生成 16 位小写十六进制假名；只导出
  白名单字段；对规范化 JSON 清单计算 SHA256。
  `execute_reuse_export` 必须恰好一次通过 T6 的审批流程，并由 executor
  恰好一次调用导出函数。
- **工程难点。** 按研究目的做域隔离、规范化序列化、重复/非法 ID 处理、
  输入不可变，以及异常信息不能回显任何敏感数据。导出失败时不能留下
  “已成功执行”的错误状态。
- **考察能力。** 数据最小化、假名化、密码学卫生、API Contract，以及跨模块
  恰好一次执行。

### T3 — Audit：审计日志与可信恢复

- **业务场景。** 记录谁在什么时间执行了什么操作，并能够发现事后篡改。
- **需要实现。** `AuditChain` 通过 `previous_hash` 与规范化 SHA256 记录
  哈希形成哈希链；`verify` 返回确定性的错误码；`from_records` 从快照恢复
  并继续追加。
- **工程难点。** 规范化 JSON、UTC 归一化且拒绝微秒、序号与链接检查、重复
  ID 检测，以及恢复对象与原始输入的深拷贝独立。可信的 `expected_head` 和
  `expected_length` 还能发现重新计算哈希的篡改与后缀截断。恢复错误必须
  严格使用 `INVALID_CHAIN:` 格式。
- **考察能力。** Append-only 数据结构、密码学完整性、序列化纪律与恢复设计。

### T4 — Scheduling：预约排期与原子化容量预订

- **业务场景。** 为符合条件的受试者安排一系列就诊时间，同时处理月末、
  休诊日、时区切换和每日有限容量。
- **需要实现。** `plan_visits` 计算候选日期并分配容量；
  `book_subject_visits` 先检查 T1 资格，再提交容量变更。
- **工程难点。** 每个月候选日期都必须从原始日期推导
  （1 月 31 日 → 2 月 28 日 → 3 月 31 日，而不是 3 月 28 日）。
  夏令时开始时不存在的时间按空缺前移，歧义时间使用 `fold=0`。
  只有整个计划成功后才提交容量；只递减已列出的日期；任何失败都保持
  容量原样。资格失败的错误消息必须严格符合格式。
- **考察能力。** 日历算术、zoneinfo/DST、资源分配、类数据库事务的原子性
  与回滚。

### T5 — Safety：安全性事件随访分诊

- **业务场景。** 将一次原始安全性报告及其后续补充报告合并为一个 Case，
  而不是当作互不相关的事件。
- **需要实现。** `triage_events(events, now)` 沿 `follow_up_of` 链接校验并
  合并事件链，输出合并后的病例。
- **工程难点。** 拒绝环、缺失根、重复 ID，以及混合受试者或事件代码的链。
  使用最早发生时间与最高严重性；7/15/30 天上报期限从最早发生时间计算，
  而不是从后续报告到达时间计算；保留来源和不确定性；
  `overdue` 严格按 `report_by < now` 判断；输入顺序不得影响输出。
- **考察能力。** 图遍历、环检测、事件关联、确定性聚合与基于时间的 SLA
  计算。

### T6 — Governance：数据复用审批工作流

- **业务场景。** 申请人提交复用申请，由另一位审核人批准或拒绝；只有具备
  权限且未过期的批准才能执行导出。
- **需要实现。** `apply_reuse_action` 实现
  `draft → submitted → approved → executed`，并支持 rejected 与 expired
  状态，以及供 T2 使用的可选 executor 钩子。
- **工程难点。** 职责分离、角色检查、精确的 `privacy_plan`、代表性与完整性
  元数据、`quality_score >= 0.95`，以及两套不同到期时钟：30 天批准有效期
  与数据保留期限。返回深拷贝、追加 UTC 历史，并正确处理 executor 失败，
  不能记录虚假成功。
- **考察能力。** 有限状态机、基于角色的访问控制、不可变状态转换、到期
  管理与回调契约。

## 5. 能力地图

| Job | 核心工程能力 | 对应真实工程场景 | 集成重点 |
|---|---|---|---|
| T1 | 业务规则与精确日期逻辑 | 规则引擎 / 筛选服务 | — |
| T2 | 假名化、HMAC、规范化输出 | 数据安全与隐私工程 | T6 审批与 executor |
| T3 | 哈希链、完整性、恢复 | Append-only 审计日志 | 可信快照恢复 |
| T4 | 排期、DST、原子化分配 | 预约与资源调度 | T1 资格门禁 |
| T5 | 图校验与事件聚合 | 事件处理 / 病例管理 | — |
| T6 | 有限状态机、RBAC、职责分离 | 企业审批平台 | T2 导出执行 |

这是对技术复杂度的定性归类，不是仓库中的官方难度评分。

## 6. 为什么这套测量可信？

### 可见测试与隐藏测试

| Job | T1 | T2 | T3 | T4 | T5 | T6 | 合计 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 隐藏测试用例 | 19 | 33 | 57 | 26 | 15 | 45 | **195** |

评分器只把 `trialops/*.py` 的改动应用到干净副本，注入隐藏测试，并要求
隐藏套件与全量套件都以 0 退出。模型自写的测试与 pytest 配置被忽略；
越权文件改动会被拒绝；空测试、skip、xfail、xpass 均不能算通过。

### 统一 Harness 与可审计证据

四个模型使用同样的六个工具与执行限制。重复三次可以初步区分稳定通过与
偶然成功：它不足以支持精细统计，却能暴露较大的可靠性差距和确定性失败。
每次运行都保留 `transcript.json`（模型与工具交互）、`diff.patch`（实际
代码改动）、`results.jsonl`（结果）和 `spend.jsonl`（token 与成本），
因此测量过程本身也可审计。

## 7. 两轮实测说明了什么？

| 模型 | 官方（2026-11-13） | 现场复测（2026-11-14） |
|---|---:|---:|
| Kimi K3.1 Pro | 15/18 | 14/18 |
| GLM 5.3 | 9/18 | 10/18 |
| Claude Opus 5.5 | 15/18 | 14/18 |
| Claude Fable 5.2 | **18/18** | **18/18** |
| **合计** | **57/72** | **56/72** |

- **Fable** 是唯一连续两轮全部通过的模型。
- **Kimi 的 T4 失败**并不是完全不会实现预约逻辑：其代码通过了 25/26 个
  隐藏测试，但 `INELIGIBLE:` 错误字符串没有满足精确契约。逻辑正确不等于
  API Contract 正确。
- **Opus 的 T2 失败**暴露了另一种问题：它完成了局部导出功能，却从未实现
  `execute_reuse_export`。完成多阶段需求的一部分不等于完成任务。
- **GLM 的失败主要是过程性失败**：触及 30 次调用上限、留下被评分器拒绝的
  临时文件，以及即使部分代码正确也未能收敛交付。

逐模型、逐作业的详细拆解见 `out-live/REPORT.md` §6。

## 8. 适用范围与推荐阅读顺序

这是一套**规格明确的 Python Coding Agent Benchmark**，不等于完整的软件
工程能力评估。它没有全面测试大型架构、分布式系统、真实数据库并发、生产
部署、长期维护或团队协作。每个任务三次重复适合观察可靠性，不适合非常
精细的统计排名。

建议按以下顺序阅读：

1. `README.md` —— 目标、模型、结果与仓库地图。
2. `tasks/tasks.json` —— 六个 Job 的实际 Prompt。
3. `harness/agent.py` —— Agent 执行循环与 `grade()`（没有单独的
   `grader.py`）。
4. `harness/run_matrix.py` —— 并行泳道、续跑与生命周期事件。
5. `harness/meter.py` —— token 价格、成本与预算硬停。
6. `out-live/REPORT.md` —— 失败证据与五维分析。

附录保留详细设计：原则与架构（§§1–3）、精确任务契约与测试设计（§4）、
Agent 与评分机制（§5）、计量与执行（§§6–7）、统计解释（§8）、扩展指南
（§9）和数据模式（§10）。

# 附录 — 详细技术设计

## 1. 目标与非目标





**测量什么。** 四个基础模型在 Amazon Bedrock 上充当自主编程智能体时，在同一
仓库的六个规约明确的 Python 作业上的**可靠性**与**成本**——统一提示词、统一
工具、统一硬性限制，正确性完全由权威隐藏 pytest 套件裁决。

**刻意不做的事：**

- 不是长周期软件工程——这些是更难但规约明确的作业，可在 ≤30 次调用内完成。
- 不是厂商智能体对比——所有模型使用同一最小循环；厂商自有编程智能体
  （自带工具/脚手架）表现可能不同。
- 不是严格统计排名——每个作业 3 次重复能暴露大的可靠性差距，但无法区分
  相差一次运行的模型。
- 不是 AWS 账单——成本 = 实测 token × 公开标价。

## 2. 设计原则

| 原则 | 落实机制 |
|---|---|
| 厂商中立 | 所有模型共享同一系统提示、同一工具集、同一组限制（`harness/agent.py`）；适配器只翻译线路格式 |
| 评分不可作弊 | 纯净检出评分；智能体自写的测试与 pytest 配置一律丢弃；隐藏套件为权威 |
| 一切可审计 | 每次运行保留逐字 `transcript.json` 与最终 `diff.patch`；`results.jsonl` / `spend.jsonl` / `events.jsonl` 只追加 |
| 可复现 | 固定初始仓库（`taskrepo/`）、固定提示词（`tasks/tasks.json`）、固定隐藏测试（`tasks/*/test_hidden_*.py`）、锁定运行时（Python 3.11、`tzdata==2025.2`）、必须通过全部隐藏测试的独立参考镜像 |
| 成本纪律 | 逐次调用计量入台账；$90 硬停（上限 $100）；矩阵可续跑（已完成 run ID 绝不重复） |
| 诚实报告 | 失败保留并分类（含基础设施类）；路由替换（Global→US CRIS）连同价格一并公开记录，不隐藏 |

## 3. 架构一览

```
                 ┌──────────────────────── run_matrix.py ────────────────────────┐
                 │  expected.json（清单）  12 条泳道 =（模型 × 重复），线程池     │
                 └───────┬───────────────────────────────────────────────────────┘
                         │ 每次运行：全新复制 taskrepo/
                 ┌───────▼────────┐    工具：list/read/write/edit/run_tests/finish
                 │  agent.py 循环 │ ────────────────────────────────────────────┐
                 │  ≤30 次调用    │    ┌─────────────┐      ┌────────────────┐  │
                 │  ≤20 分钟      │──▶│ OpenAIAdapt.│─▶ SigV4 代理 :8787 ─▶ Bedrock OpenAI 接口（Kimi、GLM）
                 └───────┬────────┘    ├─────────────┤      ├────────────────┤  │
                         │             │ ConverseAdap│─▶ boto3 ──────────▶ Bedrock Converse（Opus、Fable）
                         │             └─────────────┘      └────────────────┘  │
                         │ 每次调用 meter.record() ─▶ spend.jsonl（硬停 $90）   │
                 ┌───────▼────────┐                                             │
                 │    grade()     │  纯净检出 + 仅 trialops/*.py 改动           │
                 │  隐藏+全量套件 │  + 注入隐藏套件 → 退出码裁决                │
                 └───────┬────────┘                                             │
                         ▼
            results.jsonl ─▶ summarize.py ─▶ results.json / results.md
            events.jsonl ─▶ dashboard.py  ─▶ http://127.0.0.1:8788（实时）
```

数据永远单向流动：运行器 → 台账 → 读取方。读取方（汇总器、仪表板）绝不写入；
仪表板在设计上严格只读。

## 4. 任务设计

### 4.1 一次性仓库（`taskrepo/`）

一个合成的临床试验运营包（`trialops`），含六个模块与基线可见测试。
**不含真实患者数据**——一切皆为虚构。`pyproject.toml` 锁定
`>=3.11,<3.12` 与 `tzdata==2025.2`；pytest 的 `testpaths = ["tests"]`。
每次运行都从该仓库的全新副本开始，因此运行相互独立，最终的
`diff.patch` 完整记录智能体的全部改动。

### 4.2 六个作业（`tasks/tasks.json`）

每个作业是一条**两段式**长提示：一个自包含的基础特性，再接一个与其他模块
组合的真实集成扩展。初始仓库刻意缺失这些集成 API。

| 作业 | 基础特性 | 集成扩展（区分点） |
|---|---|---|
| t1-eligibility | `evaluate_subject`：版本化知情同意 + 精确年龄/化验边界 + 确定的原因顺序 | （提示追加边界/闰日测试要求） |
| t2-privacy | `export_subjects`：同意/清除过滤、HMAC-SHA256 假名、规范化清单哈希 | `execute_reuse_export`：恰好一次委托给 `governance.apply_reuse_action` |
| t3-audit | `AuditChain` 追加 + 带稳定错误码的 `verify` | `verify(expected_head/length)` + `AuditChain.from_records` 恢复续写 |
| t4-scheduling | `plan_visits`：月末钳制、zoneinfo DST 空缺/歧义、容量/休诊 | `book_subject_visits`：资格门禁 + 原子化持久容量预订 |
| t5-safety | `triage_events`：随访链合并、严重性优先、上报时限 | （提示追加感知 `now`/逾期边界规则） |
| t6-governance | `apply_reuse_action`：角色守卫状态机 + 机器可读错误码 | `privacy_plan` 取代布尔值；`executor=` 导出执行钩子 |

两段式结构的意义：把"能写一个函数"与"读完整段提示并跨模块组合"区分开。
实测结果验证了这一点：失败恰好聚集在集成部分（两次会话中 Opus 在 t2
0/6——从未实现 `execute_reuse_export`；Kimi/GLM 在 t4 的预订集成上 0/6）。

以下小节逐一详述每个作业的契约、设计意图，以及两次已记录会话（官方
2026-11-13 与现场 2026-11-14）中的实测表现；失败证据见 `out-live/REPORT.md`。

#### 作业 t1 —— 版本化知情同意与资格评估（`evaluate_subject`）

- **契约。** `evaluate_subject(subject, protocol, on)` 返回
  `{"eligible": bool, "reasons": list[str], "consent_version": str | None}`。
  受试者：`birth_date`（ISO 日期）、`consents`（`{version, signed_on}`）、
  `labs`（名称 → 数值）、`flags`。方案：`min_age`、`max_age`、
  `required_consent_version`、`consent_valid_from`、`labs`（名称 → 含边界
  `[low, high]`）、`exclusions`。
- **规则。** 在 `on` 当天或之前，按 `signed_on` 在*所有*版本中选取最新的同意
  ——不回退到更旧的同意。原因按确定顺序：`CONSENT_MISSING`、
  `CONSENT_VERSION`、`AGE_BELOW_MIN`、`AGE_ABOVE_MAX`、按方案顺序的各
  `EXCLUSION_<FLAG>`，最后是按化验名排序的 `LAB_<NAME>_LOW/HIGH`。
  年龄在 `on` 当天精确计算；年龄与化验限值均为含边界。即使选中的是错误
  版本，也返回该所选同意的版本。缺失/畸形的必需输入抛出指明字段名的
  `ValueError`——绝不抛出原始 `KeyError` 或 datetime 异常。
- **设计意图。** 高陷阱密度的纯日期/区间逻辑：闰日生日、当日同意、已退役
  同意版本、精确的边界包含、"取最新、不回退"的选择规则，以及完全钉死的
  原因排序。
- **实测。** Kimi、Opus、Fable 两次会话均 3/3；GLM 2/3（其失手为过程性
  失败，而非逻辑错误）。
- **参考。** [ISO 8601](https://en.wikipedia.org/wiki/ISO_8601)（纯日期输入）·
  [Python `datetime`](https://docs.python.org/3/library/datetime.html)（闰年语义）。

#### 作业 t2 —— 治理驱动的研究数据导出（`export_subjects`、`execute_reuse_export`）

- **契约。** `export_subjects(subjects, *, purpose, secret, on)` 返回
  `{"rows": [...], "manifest": {"algorithm": "sha256", "count": N, "sha256":
  digest}}`。仅当记录具备该 `purpose` 的同意且 `signed_on <= on`、（未撤回或
  `withdrawn_on > on`）、且 `purge_after >= on` 时纳入——所有边界均为含边界。
- **规则。** 假名 = `HMAC-SHA256(key=UTF-8(secret), msg=UTF-8(purpose + ':' + id))`
  的前 16 位小写十六进制——带密钥且按目的域隔离。每行只含 `pseudonym`、
  `country`、`birth_year` 与按时间排序的 `visits`；各行按假名排序。仅允许
  白名单字段：`id`、`name`、`email`、完整出生日期、同意记录、`purge_after`
  与密钥绝不输出——包括错误消息之内。ID 必须是 ASCII `[A-Za-z0-9_-]+`；
  重复 ID 抛出带 `DUPLICATE_SUBJECT_ID` 代码的 `ValueError`；JSON 拒绝
  NaN/Infinity（`allow_nan=False`）；输入绝不被修改；空输入合法。清单摘要 =
  对 `json.dumps(rows, sort_keys=True, separators=(',', ':'),
  ensure_ascii=False)` 的 UTF-8 编码做 SHA256——规范化序列化逐字节钉死。
- **集成。** `execute_reuse_export(request, subjects, *, actor, secret, now)`
  恰好一次委托给 `apply_reuse_action(request, 'execute', actor, now,
  executor=...)`；其 executor 恰好一次调用 `export_subjects`；
  `export_subjects` 保持兼容。
- **设计意图。** 以代码实现隐私工程：白名单字段选择、假名域隔离、规范化
  哈希、错误卫生（异常不泄露标识符或密钥），以及与 t6 的严格单次委托契约。
  它也是多段提示的区分器：Opus 实现了 `export_subjects` 却从未编写
  `execute_reuse_export`——两次会话合计 0/6，在导入阶段即失败。
- **参考。** [RFC 2104 — HMAC](https://www.rfc-editor.org/rfc/rfc2104) ·
  [GDPR 第 4(5) 条 假名化](https://gdpr-info.eu/art-4-gdpr/) ·
  [RFC 8785 — JSON 规范化方案](https://www.rfc-editor.org/rfc/rfc8785)。

#### 作业 t3 —— 审计链完整性与恢复（`AuditChain`）

- **契约。** 以 `GENESIS_HASH = '0' * 64` 锚定的只追加哈希链。
  `append(*, event_id, actor, action, at, payload)` 返回记录：`sequence`
  从 1 开始，含 `previous_hash` 与 `hash`。`at` 必须带时区，归一化为
  `YYYY-MM-DDTHH:MM:SSZ`（拒绝微秒）。`hash` = 对除 `hash` 外全部记录字段的
  规范化 JSON 做 SHA256（`sort_keys=True, separators=(',', ':'),
  ensure_ascii=False, allow_nan=False`）。拒绝空的 event/actor/action 字符串
  与重复事件 ID。
- **规则。** `verify(records)` 返回确定性的错误码列表（而非布尔值）：序号
  错乱、`previous_hash` 链接断裂、记录哈希错误、重复 ID、时间戳不递增、
  时间戳/哈希畸形、非 dict 载荷；合法或空链返回 `[]`。扩展的
  `verify(records, *, expected_head, expected_length)` 针对可信锚点增加
  `HEAD_MISMATCH` / `LENGTH_MISMATCH`，且不得重新归一化已存储的时间戳
  表示。这能检出未重哈希的篡改；配合可信锚点还能检出重哈希篡改与后缀
  截断；跨链重放是明确的非目标。
- **恢复。** `AuditChain.from_records(records, *, expected_head,
  expected_length)`：两个可信参数均为必需；输入只物化一次并用 `verify`
  校验；空快照仅在长度为零且为创世头时合法。出错时抛出消息恰好为
  `INVALID_CHAIN:` 加排序后错误码（逗号连接）的 `ValueError`。成功时返回
  持有深拷贝的独立链，恢复 `previous_hash` 与重复 ID 索引；下一次 append
  使用序号 `expected_length + 1` 与恢复后的链头。
- **设计意图。** 微缩版防篡改日志：规范化序列化纪律、确定性错误枚举、
  精确的 `INVALID_CHAIN:` 消息格式（即 GLM 在官方会话中踩中的分隔符
  陷阱），以及恢复不变量——深拷贝独立、索引恢复、序号续接。Kimi 的现场
  r3 在面对畸形快照时抛出原始 `TypeError`，而非契约规定的 `ValueError`
  （隐藏 56/57）。
- **参考。** [哈希链](https://en.wikipedia.org/wiki/Hash_chain) ·
  [RFC 6962 — 证书透明度](https://www.rfc-editor.org/rfc/rfc6962)（生产级
  只追加透明日志）· [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785)。

#### 作业 t4 —— DST 安全的就诊调度与持久化预订（`plan_visits`、`book_subject_visits`）

- **契约。** `plan_visits(*, first_day, offsets_months, hour, minute,
  timezone, closed_days=None, capacity=None)`：每月候选日由 `first_day`
  加 N 个日历月得出，日期钳制到目标月最后一天——永远从 `first_day`
  推导，绝不从已钳制或已改期的前一次就诊推导（1 月 31 日 → 2 月 28 日
  → 3 月 31 日，而非 3 月 28 日）。偏移量为唯一的非负整数；用
  `zoneinfo` 在给定 IANA 时区构造本地时间；拒绝非法时区与越界时间。
- **规则。** 落在 `closed_days` 或剩余容量为零的日期逐天后移到下一个可用
  日期（搜索上限 366 天，否则抛出 `ValueError` `UNSCHEDULABLE` 且不返回
  部分计划）；改日时保留原始本地墙钟时间；不存在的本地时间（春拨快
  空缺）按空缺前移；歧义时间使用 `fold=0`；`capacity` 为日期 → 正剩余
  槽位的映射，绝不被修改；按方案顺序做最早可行贪心分配。返回项：
  `{"offset_months", "local"（带秒）, "utc"（带 Z）}`。
- **集成。** `book_subject_visits(subject, protocol, *, on, first_day,
  offsets_months, hour, minute, timezone, capacity, closed_days=None)` 先调用
  `evaluate_subject(subject, protocol, on)`：若不合格，抛出以
  `INELIGIBLE:` 加有序原因的 `ValueError`，且不改动容量。候选生成、休诊、
  DST 处理、校验与计划内分配全部委托给 `plan_visits`。仅在完全成功后，
  对返回就诊日中**已列出**的容量条目做递减；未列出的日期保持无约束
  行为且绝不插入；任何失败都使容量保持原样；先前已成功调用的提交保持
  有效（顺序槽位竞争）。
- **设计意图。** 最难的作业：日历算术（月末钳制）、时区数据库的现实
  （空缺/回折），以及带回滚的原子预订语义。它还携带钉死格式陷阱——精确
  字符串 `INELIGIBLE:A,B`（无空格）让 Kimi 在两次会话的全部 6 次运行中
  失败，尽管其隐藏测试通过了 25/26。GLM 还漏掉了真实 dict 类型检查
  （接受了 `mappingproxy` 容量）与槽位递减。
- **实测。** Fable 6/6；Opus 官方 3/3、现场 2/3（一次 `.tmp` 文件被判负）；
  Kimi 与 GLM 0/6。
- **参考。** [PEP 615 — `zoneinfo`](https://peps.python.org/pep-0615/) ·
  [IANA 时区数据库](https://www.iana.org/time-zones) ·
  [`datetime.fold`](https://docs.python.org/3/library/datetime.html#datetime.datetime.fold)。

#### 作业 t5 —— 安全性事件随访分诊（`triage_events`）

- **契约。** `triage_events(events, *, now)` 将随访链（经 `follow_up_of`）
  合并为每链一个病例——面向虚构的安全性事件随访，明确不是医学判断。
  事件：`event_id`、`subject_pseudonym`、`event_code`、`onset`（带时区的
  ISO 日期时间）、`seriousness`（`fatal`、`life_threatening`、
  `hospitalization`、`other`）、`source`，可选 `follow_up_of` 与
  `uncertain`。
- **规则。** 拒绝重复事件 ID、缺失根、环、或混合了受试者假名/事件代码的
  链。每个病例：`case_id`（根 ID）、`onset`（最早，UTC `Z`）、
  `seriousness`（按 fatal > life_threatening > hospitalization > other 取
  最高）、`report_by`（最早发作 + 7 天（fatal/life_threatening）、15 天
  （hospitalization）、30 天（other）——时钟从最早发作起算，绝不从随访
  到达时间起算）、`sources`（排序去重）、`event_ids`（排序）、
  `uncertain`（任一事件携带即为 true）、`overdue` 当 `report_by < now`
  （严格小于；相等不算逾期；`now` 必须带时区）。病例按 onset 再按
  `case_id` 排序；输入顺序不得影响输出。未知严重性、无时区时间戳与非法
  链接抛出带稳定代码的 `ValueError`。不做模糊匹配，不编造诊断，不实现
  真实上报规则。
- **设计意图。** 在父指针森林上做图校验（环、缺失根）加确定性、顺序无关
  的聚合；"纯属虚构分诊"的设定考验需求边界纪律。
- **实测。** Kimi、Opus、Fable 两次会话均 3/3；GLM 官方 1/3、现场 2/3，
  失分于 `max_calls` 与临时文件判负。
- **参考。** [ICH E2A 药物警戒指南](https://database.ich.org/sites/default/files/E2A_Guideline.pdf)
  （本作业刻意简化的现实世界原型）·
  [并查集 / 环检测](https://en.wikipedia.org/wiki/Disjoint-set_data_structure)。

#### 作业 t6 —— 数据复用治理工作流（`apply_reuse_action`）

- **契约。** `apply_reuse_action(request, action, actor, now, executor=None)`
  驱动状态机：`draft → submitted → approved → executed`，另有 `rejected`
  与 `expired`。请求携带 `id`、`requester`、`reviewer`、`purpose`、
  `status`、`retention_until`、`privacy_plan`（恰好为
  `{"algorithm": "hmac-sha256", "domain": purpose, "fields": ["pseudonym",
  "country", "birth_year", "visits"]}`）、`metadata`（`representative`、
  `complete`、`quality_score`）与 `history`。
- **规则。** `submit`：仅 requester，draft → submitted。`approve`：仅
  reviewer，submitted → approved，并设 `approval_expires_on = now + 30 天`。
  `reject`：仅 reviewer，submitted → rejected。`execute`：仅 requester，
  approved → executed。`expire`：任何 actor，approved/submitted → expired，
  条件为 `now > approval_expires_on`（approved），否则
  `now > retention_until`。批准要求 requester ≠ reviewer（`SELF_REVIEW`）、
  精确的 `privacy_plan`、representative 且 complete 的元数据
  （`METADATA_REQUIRED`）、`quality_score >= 0.95`、
  `now <= retention_until`（`RETENTION_EXPIRED`）以及非空 purpose。
  执行还要求 actor = requester、`now <= approval_expires_on`
  （`APPROVAL_EXPIRED`）、同样的隐私/元数据检查，以及可调用的
  `executor`。每个动作深拷贝请求（调用方输入绝不被修改），并向 history
  追加 `{action, actor, at}`（带时区的 `now` 归一化为 UTC `Z`）。角色违规
  抛出 `PermissionError`；非法状态或未过检查抛出带稳定代码的
  `ValueError`（`INVALID_STATE` 等）。
- **设计意图。** 工作流严谨性：职责分离、守卫顺序、两套不同的到期时钟
  （批准 vs 留存）、深拷贝纪律，以及 t2 的 `execute_reuse_export` 必须
  恰好调用一次的 `executor` 钩子——跨作业集成接缝。
- **实测。** 最被普遍解决的作业：官方会话中所有模型 3/3；GLM 在现场会话
  因临时文件判负丢掉一次运行。
- **参考。** [职责分离](https://en.wikipedia.org/wiki/Separation_of_duties) ·
  [有限状态机](https://en.wikipedia.org/wiki/Finite-state_machine)。

### 4.3 隐藏测试套件（`tasks/<job>/test_hidden_*.py`）

共 2,249 行，**195 个参数化用例**（各作业 19/33/57/26/15/45）。反复出现的
测试类别是刻意施加的压力点：

- **边界精确性**：含边界的年龄/化验限制、闰日生日、当日同意、
  `report_by < now`（相等*不算*逾期）。
- **时间/时区陷阱**：月末钳制（1 月 31 日 → 2 月 28 日 → 3 月 31 日）、
  DST 春拨快空缺（按空缺前移）、秋拨回歧义（`fold=0`）、UTC `Z` 归一化、
  拒绝微秒。
- **规范化**：`json.dumps(..., sort_keys=True, separators=(',',':'),
  ensure_ascii=False)` 哈希；`allow_nan=False`。
- **精确错误契约**：稳定的机器可读错误码与精确消息格式——例如
  `"INELIGIBLE:" + ",".join(reasons)`、`INVALID_CHAIN:<排序后用逗号连接>`。
  （这种钉死分隔符的风格是刻意的严格性陷阱；见 §9.4。）
- **不可变性与原子性**：输入绝不被修改、深拷贝语义、拒绝重复 ID、
  任何失败即回滚（容量不变）、只有完全成功才提交。
- **对抗性结构**：随访环、缺失根、跨受试者链条、被篡改的审计链接、
  重哈希与未重哈希的编辑、后缀截断、在需要真实 dict 处传入 mappingproxy。

### 4.4 可见测试 vs 隐藏测试

- **可见**（`taskrepo/tests/`，39 个函数）：智能体可用 `run_tests` 自由运行的
  基线覆盖。必要但*不充分*。
- **隐藏**（`tasks/`）：仅在评分时注入纯净检出。智能体永远看不到。
  一次运行只有在隐藏套件与全量套件都以 0 退出、至少 1 个测试通过且
  零 skip/xfail/xpass 时才通过（什么都没收集到的运行判负，而非判过）。

### 4.5 参考镜像（`reference/<job>/`）

每个作业都有**独立**镜像，内含完整正确的实现。其作用是基准真值：在测量
任何模型之前，每个隐藏套件都必须在其镜像上全绿（已验证：19/33/57/26/15/45
= 195 个隐藏用例；README 的 477 还计入镜像可见套件）。布局说明：t1–t4 的
隐藏文件在镜像根目录，t5–t6 需注入 `tests/`——具体做法见
`docs/USERGUIDE.md` §3（或其中文/法文版）。

## 5. 智能体循环（`harness/agent.py`）

### 5.1 固定脚手架

- **系统提示**：刻意简短（"自主编程智能体……最小且正确的改动……完成后调用
  `finish`……没有真人在环"）——测的是模型，不是提示工程。
- **仅六个工具**：`list_files`、`read_file`、`write_file`、`edit_file`、
  `run_tests`、`finish`。`edit_file` 要求恰好一处匹配（安全、确定）；
  `run_tests` 就是 `pytest -q`（90 秒超时，6,000 字符尾部）。
  工具结果截断（回传模型 20,000 字符）以控制上下文。
- **工作区沙箱**：所有路径解析到仓库副本内，越界即拒绝。
- **硬性限制**：30 次调用、20 分钟墙钟、16,384 token 完成预算、
  厂商默认推理设置。对所有模型完全一致。
- **韧性**：每次调用至多 4 次尝试并退避（429 额外等待）；400/404/422 提前
  中止；凭证过期（`401/403` 含 "expired"）以 `credentials_expired` 停止该
  泳道，便于重新登录后续跑。
- **行为遥测**（逐次运行记录）：工具调用数、格式错误 `format_failures`、
  空转 `no_tool_turns`（连续 3 轮无工具调用即以 `no_tool_calls` 结束）、
  `api_errors`、停止原因（`finish`、`max_calls`、`wall_clock`、
  `no_tool_calls`、`api_error`、`credentials_expired`、`budget_hard_stop`）。

### 5.2 两种 API 接口，同一语义

| | OpenAIAdapter（Kimi、GLM） | ConverseAdapter（Opus、Fable） |
|---|---|---|
| 端点 | 经本地 SigV4 代理 `127.0.0.1:8787` 的 Bedrock OpenAI 兼容 Chat Completions | 经 boto3 的 us-west-2 Bedrock Converse |
| 工具模式 | `tools=[{type:function, function:{...}}]` | `toolConfig.tools[].toolSpec`（同一 JSON schema） |
| 用量归一化 | `prompt_tokens`、`completion_tokens`、`completion_tokens_details.reasoning_tokens`、`prompt_tokens_details.cached_tokens`（**缓存是 prompt token 的子集**） | `inputTokens + cacheRead + cacheWrite`（**缓存字段为加性**）、`outputTokens`；无推理细分 |
| 历史中的坏参数 | `_replayable()`：非法 JSON 以 `{}` 重放（Bedrock 每次请求都会重新校验历史调用），并计入失败 | 同一共享辅助函数 |

刻意保留并公开的不对称：Claude 适配器不设缓存点，因此其成本为上限；
Kimi/GLM 的输入 token 有 89–94% 以约 1/10 价格的缓存读取计入。

### 5.3 评分（`grade()`）

1. 将智能体仓库与纯净起点做 diff（忽略 `__pycache__`、`.pytest_cache`）。
2. **白名单**：改动必须在 `trialops/*.py` 或 `tests/test_*.py` 之内。
   其余一切（临时文件、`.tmp`、pytest 配置、辅助模块）→ 自动判负
   `unauthorized file changes: …`。仅这一条规则就命中了最近会话 16 个
   失败中的 7 个（见 `out-live/REPORT.md` §3）。
3. 把 `trialops/` 的改动应用到临时目录中的纯净检出；把权威隐藏测试注入
   `tests/`。
4. 先单独跑隐藏套件，再跑全量套件（`-p no:cacheprovider`、
   `--override-ini addopts=`，防止智能体或环境配置渗入；90 秒超时）。
5. **通过 = 两者皆以 0 退出**，且 ≥1 个通过、零 skip/xfail/xpass。

智能体自写的测试与 pytest 配置绝不进入评分检出——模型无法通过削弱或删除
测试、添加 skip/xfail、调整收集来抬高分数。


## 6. 计量（`harness/meter.py`）

- 单次调用成本 = `新输入 × 输入价 + 缓存读 × 读价 + 缓存写 × 写价 +
  输出 × 输出价`，其中 `新输入 = 输入 − 缓存读 − 缓存写`（§5.2 的归一化让
  两种接口都诚实）。
- 价格是**实际使用路由**的 AWS 标价（Global 与 US CRIS 不同）。例如 Opus
  在 Global 反复 503 后改用 `us.anthropic.claude-opus-5-5`
  （$4.40/$22/$0.22）——替换及其反事实在 README 中公开记录，而非默默吸收。
- 每次调用向 `spend.jsonl` 追加一行；累计总额即**计量器**。到 $90 不再发起
  新调用（`budget_hard_stop`），保护 $100 上限。计量器按输出目录计，
  新的 `P117_OUT` 从 $0.00 开始。

## 7. 矩阵执行与实时可观测性

- **泳道**：每条对应一个（模型, 重复）→ 官方 4×3 共 12 条；每条顺序走完
  六个作业；`ThreadPoolExecutor(12)`。
- **续跑**：`results.jsonl` 中已有的 `run_id` 最先被跳过；Ctrl-C 永远安全；
  被中断运行的部分花费按设计留在台账中。
- **实时状态**（后加的可观测性）：运行器启动时写入 `expected.json`
  （完整计划清单），并向 `events.jsonl` 追加
  `run_start` / `run_end` / `run_skip` 事件。
- **仪表板**（`harness/dashboard.py`，仅标准库、只读）：每 2 秒从这些文件
  重建 预期/完成/运行中/待运行、各模型统计、花费对上限、事件流。
  没有 `events.jsonl` 的旧目录也能渲染——用无结果行的 spend 记录推断在途
  运行。清单永远优先于命令行兜底，因此仪表板精确反映运行器的计划。

## 8. 统计设计与解读

- **每个作业 3 次重复**是刻意的成本/信号权衡：足以暴露可靠性断层
  （如某作业 0/3 vs 3/3）与确定性/随机性之别，不足以区分相差一次运行的
  模型。两次独立 72 运行会话（官方 57、现场 56 次通过）证实：各模型总数
  变化 ≤1 次，且每个确定性失败都精确复现。
- **先读停止原因，再读通过数。** `finish` 失败是内容失败（模型以为已完成
  ——见 Opus t2、Kimi t4）；`max_calls`/`wall_clock` 失败是过程失败
  （从未收敛——GLM 的典型画像）。两者指向不同的修复与不同的能力结论。
- **$/通过**是效率的权威指标；通过率不同时，原始 $/运行具有误导性。
  Claude 成本为上限（无缓存点）。

## 9. 如何继续

### 9.1 验证关卡（每次改动设计后按序执行）

1. **参考关卡**：每个隐藏套件在其参考镜像上全绿（做法见 USERGUIDE §3；
   预期 19/33/57/26/15/45）。
2. **冒烟关卡**：一次便宜运行（`P117_OUT=out-smoke-v2 … --models
   kimi-k3.1-pro --tasks t1 --reps 1`）——端到端验证代理、凭证、评分、
   台账、仪表板，约 $0.08–0.40。
3. **矩阵关卡**：完整 `--reps 3`；用仪表板实时观察；然后 `summarize.py`
   （始终读取 `out/`）。

### 9.2 新增作业（清单）

1. 在 `tasks/tasks.json` 编写提示（`id: tN-name`，两段式：基础特性 + 集成
   扩展；钉死精确错误契约）。
2. 扩展 `taskrepo/`：`trialops/` 加模块桩 + `tests/test_*.py` 基线可见测试。
3. 编写 `tasks/tN-name/test_hidden_<name>.py`——覆盖 §4.3 的压力类别。
4. 创建 `reference/tN-name/`（完整实现）；跑 §9.1.1 直到隐藏套件全绿。
   若镜像根目录已有隐藏文件，直接运行（复制进 `tests/` 会因同名冲突）。
5. 先用一个模型对新作业冒烟（`--tasks tN`），再纳入矩阵。

### 9.3 新增模型（清单）

1. `harness/run_matrix.py:MODELS`——标签 →（`openai` | `converse`，
   Bedrock 模型 ID）。OpenAI 兼容模型走 SigV4 代理；Claude 类走 Converse。
2. `harness/meter.py:PRICES`——**实际调用路由**的标价（§6）。
3. 在 us-west-2 确认模型访问（控制台 → Bedrock）；开放权重模型还需确认
   代理可为该模型族签名。
4. 官方报告还需更新 `summarize.py:ORDER/OPEN` 与
   `dashboard.py:DEFAULT_MODELS/OPEN_WEIGHTS`。先冒烟（§9.1.2）。

### 9.4 已知局限与开放设计问题

- **缓存不对称**：Claude 成本为上限（无缓存点）；可在 Converse 上启用缓存
  检查点以获得同口径成本对比。
- **钉死分隔符陷阱**：`INELIGIBLE:`/`INVALID_CHAIN:` 精确格式断言区分的是
  提示未明确规约的细节。两种公平做法：在提示中明确钉死格式；或保留现状并
  将其作为刻意的严格性陷阱公开报告——但不要静默混用两种哲学。
- **共享 90 秒测试超时**同时作用于智能体 `run_tests` 与评分；病态代码
  （死循环）按原样评分。
- **单一作者偏差**：作业、隐藏测试与评分器由一人编写。对隐藏套件做独立
  评审是性价比最高的可信度提升。
- **重复次数**：3/作业是既定权衡；提高到 5 大约使矩阵成本翻倍（请相应
  预算——计量器硬停按输出目录计）。
- 想法清单：更多集成类作业（区分度最好）；`results.jsonl` 增加逐次难度
  标注；仪表板改 SSE 推送替代 2 秒轮询；每次任务变更时自动跑 §9.1.1 的
  CI 关卡。

## 10. 数据模式（供工具作者）

- `results.jsonl`：`{run_id, model, task, rep, stop, calls, tool_calls,
  format_failures[], no_tool_turns, api_errors[], usage{input, output,
  reasoning, cache_read, cache_write}, usd, wall_s, visible_tests_run,
  model_id, adapter, grade{hidden{exit, passed, failed, errors, skipped,
  xfailed, xpassed, tail}, suite{…}}, pass, finished_at}`
- `spend.jsonl`：`{t, run, model, usage{…}, usd, total_usd}`——每次调用一行。
- `events.jsonl`：`{t, kind: run_start|run_end|run_skip, run_id, model, task,
  rep, …}`（`run_end` 另含 `passed, stop, calls, usd, wall_s`）。
- `expected.json`：`{generated_at, reps, models[], tasks[], runs[{run_id,
  model, task, rep}]}`，按泳道顺序。

