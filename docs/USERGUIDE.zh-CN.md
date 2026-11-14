# 用户指南 — 复现 Bedrock 模型基准测试

**本指南：[English](USERGUIDE.md) · 中文 · [Français](USERGUIDE.fr.md)**

本指南从零复现完整的演示测量：72 次运行的智能体矩阵（4 个模型 × 6 个作业
× 3 次重复，标价约 $39，约 45–90 分钟）、可选的冒烟运行、实时状态仪表板，
以及最终汇总产物（`out/results.json`、`out/results.md`）。

所有命令均在 `demo/`（本仓库根目录）下执行。

## 快速参考（完整流程）

```bash
# 0. 一次性环境搭建
uv venv /tmp/p117/venv --python 3.11
uv pip install --python /tmp/p117/venv/bin/python httpx boto3 pytest "tzdata==2025.2"
aws sts get-caller-identity --profile <your-aws-profile>                      # 凭证是否有效？
k3eval proxy --region us-west-2 --profile <your-aws-profile> --host 127.0.0.1 --port 8787 \
  --model us.moonshotai.kimi-k3 &                                  # Kimi/GLM 的 SigV4 代理

# 1. 预检：参考镜像必须全绿（完整循环见 §3）
cd reference/t4-scheduling && /tmp/p117/venv/bin/python -m pytest -q test_hidden_scheduling.py && cd ../..

# 2. 冒烟运行（1 次运行，约 $0.10–0.40）
P117_OUT=out-smoke-v2 /tmp/p117/venv/bin/python harness/run_matrix.py \
  --models kimi-k3.1-pro --tasks t1 --reps 1

# 3. 完整矩阵（72 次运行，约 $39）—— 终端 1
/tmp/p117/venv/bin/python harness/run_matrix.py --reps 3

# 4. 实时仪表板 —— 终端 2（可选但推荐）
python3 harness/dashboard.py --port 8788       # 打开 http://127.0.0.1:8788

# 5. 最终汇总产物
/tmp/p117/venv/bin/python harness/summarize.py
```

## 1. 前置条件

| 要求 | 说明 | 检查 |
|---|---|---|
| Python 3.11 | `taskrepo/pyproject.toml` 锁定 `>=3.11,<3.12`；评分运行时为 3.11 且 `tzdata==2025.2`。推荐用 `uv` 安装。 | `uv python list \| grep 3.11` |
| AWS 凭证 | 一个可在 **us-west-2** 调用 Bedrock 的 AWS CLI 配置。本指南使用占位符 `<your-aws-profile>`，请全部替换为你自己的 AWS CLI 配置名。 | `aws sts get-caller-identity --profile <your-aws-profile>` |
| Bedrock 模型访问 | 在 us-west-2 启用：`global.moonshotai.kimi-k3.1`、`us.zai.glm-5.3`、`us.anthropic.claude-opus-5-5`、`global.anthropic.claude-fable-5-2`（控制台 → Bedrock → Model access）。GLM 5.3 另有企业资格门槛，仅支持跨区域路由。 | 控制台 |
| `k3eval` SigV4 代理 | Kimi 与 GLM 由 Bedrock 的 OpenAI 兼容接口提供服务，框架经本地 `127.0.0.1:8787` 签名代理访问。Claude 直接使用 boto3 Converse，不需要代理（但仍建议启动——框架会从代理进程自动读取 AWS profile）。 | `ps aux \| grep "k3eval proxy"` |
| 预算 | 矩阵约 $39 标价 + 约 $6 冒烟。计量器在 **$90** 硬停（上限 **$100**）。 | — |

> **关于原始环境的说明：** 旧文档曾引用 `/tmp/p116/venv`，该虚拟环境已损坏
> （解释器无法解析自身 site-packages）。请按快速参考新建环境，不要尝试修复。

## 2. 环境搭建

```bash
uv venv /tmp/p117/venv --python 3.11
uv pip install --python /tmp/p117/venv/bin/python httpx boto3 pytest "tzdata==2025.2"
```

框架依赖刻意保持最小：

| 包 | 用途 |
|---|---|
| `httpx` | `OpenAIAdapter` → 代理（Kimi、GLM） |
| `boto3` | `ConverseAdapter` → Bedrock Converse（Opus、Fable） |
| `pytest` | 智能体 `run_tests` 工具与评分器 |
| `tzdata==2025.2` | DST 安全调度测试锁定的时区数据库 |

仪表板（`harness/dashboard.py`）**仅使用标准库**——任意 Python 3 即可运行，
无需虚拟环境。

启动签名代理（终端 0，保持运行）：

```bash
k3eval proxy --region us-west-2 --profile <your-aws-profile> --host 127.0.0.1 --port 8787 \
  --model us.moonshotai.kimi-k3
```

`run_matrix.py` 在运行时从代理进程的命令行读取 `--profile` 值
（`_proxy_profile()`）；框架不会把凭证写入磁盘。

## 3. 预检（约 2 分钟）

1. **代理在线：** `ps aux | grep "k3eval proxy"` 能看到进程，且
   `lsof -iTCP:8787 -sTCP:LISTEN` 能看到监听。
2. **凭证有效：** `aws sts get-caller-identity --profile <your-aws-profile>`。
   SSO 配置请先 `aws sso login --profile <your-aws-profile>`。
3. **参考镜像全绿** —— 在花钱之前证明作业、隐藏测试与评分器自洽。
   `reference/<job>/` 中每个镜像都包含完整实现。存在两种布局，对应两种做法：

   ```bash
   # t1–t4：隐藏套件已在镜像根目录 —— 直接运行
   for job in t1-eligibility t2-privacy t3-audit t4-scheduling; do
     ( cd reference/$job && /tmp/p117/venv/bin/python -m pytest -q -p no:cacheprovider \
       $(ls test_hidden_*.py) | tail -1 )
   done
   # t5–t6：把权威隐藏套件注入 tests/ 目录
   for job in t5-safety t6-governance; do
     ( cd reference/$job && mkdir -p tests && cp ../../tasks/$job/test_hidden_*.py tests/ \
       && /tmp/p117/venv/bin/python -m pytest -q -p no:cacheprovider | tail -1; rm -rf tests )
   done
   ```

   预期：**19 / 33 / 57 / 26 / 15 / 45 全部通过 = 195 个隐藏测试**，与 README 一致。
   当镜像根目录已有同名隐藏文件时，**不要**再把它复制进 `tests/`：同名模块会冲突
   （`import file mismatch`）。各镜像相互独立——务必在单个镜像目录内运行；
   评分运行时为 3.11 且 `tzdata==2025.2`。

## 4. 冒烟运行（约 $0.10–0.40，约 1 分钟）

完整走一遍——智能体会话、评分、台账、产物——输出到一次性目录：

```bash
P117_OUT=out-smoke-v2 /tmp/p117/venv/bin/python harness/run_matrix.py \
  --models kimi-k3.1-pro --tasks t1 --reps 1
```

预期日志（时间会有波动）：

```
HH:MM:SS kimi-k3.1-pro__t1-eligibility__r1: PASS stop=finish calls=9 fmt=0 api_err=0 $0.151 41s hidden 19/19 | meter $0.15
```

确认产物存在：`out-smoke-v2/results.jsonl`（1 行）、`spend.jsonl`（每次调用一行）、
`events.jsonl`（run_start + run_end）、`expected.json`，以及
`runs/<run_id>/{transcript.json,diff.patch}`。
`harness/summarize.py` 只读取 `out/`，绝不读取冒烟目录——不要对冒烟数据做汇总。

## 5. 完整矩阵（72 次运行，约 $39 标价）

```bash
/tmp/p117/venv/bin/python harness/run_matrix.py --reps 3
```

预期情况：

- **计划：** `tasks/tasks.json` × 4 个模型 × 重复 1–3。12 条泳道并行——
  每条对应一个（模型, 重复）；泳道内作业顺序执行。
- **时长：** 约 45–90 分钟。GLM 泳道最慢（中位约 323–571 秒/次）。
- **可续跑：** 已在 `out/results.jsonl` 中的 `run_id` 会被跳过。可随时 Ctrl-C
  后用同一命令续跑。被中断运行已产生的花费保留在 `spend.jsonl`（只追加）——
  重跑该次会重复计费，这是刻意设计。
- **预算护栏：** 计量器到 $90 后不再发起新调用（`stop=budget_hard_stop`）。
- **凭证过期：** 受影响泳道以 `credentials_expired` 停止，其余继续；
  重新登录后续跑，已完成运行不会重复。
- 常用子集：`--models kimi-k3.1-pro,glm-5.3`、`--tasks t1,t4`、`--reps 1`。

## 6. 实时仪表板

第二个终端（任意 Python 3，无需虚拟环境）：

```bash
python3 harness/dashboard.py --port 8788
# 打开 http://127.0.0.1:8788
```

`--out` 必须指向运行器写入的同一目录（等于运行器的 `P117_OUT`，默认 `demo/out`）：

```bash
python3 harness/dashboard.py --out out-smoke-v2 --port 8789   # 改为观察冒烟运行
```

页面每 2 秒刷新，严格只读。内容：

| 面板 | 内容 |
|---|---|
| 顶栏 | 已完成 / 通过 / 失败 / 运行中 / 待运行；总花费相对 $90 硬停的进度条 |
| 模型卡片 | 实时通过数与通过率、台账成本、每次通过成本、调用/耗时中位数、API 错误、空转轮次；"open weights" 徽章 |
| 矩阵 | 每个重复一个圆点：**绿** 通过 · **红** 失败 · **蓝色脉动** 运行中 · **灰** 待运行。悬停查看停止原因、调用数、耗时、成本、隐藏测试得分 |
| 正在运行 | 在途运行的实时调用数、已产生成本、已运行/空闲秒数 |
| 最新事件 | 最近 15 条 `run_start` / `run_end` / `run_skip` 事件 |

数据源（均在 `--out` 内）：`expected.json`（运行器启动时写入的运行清单）、
`events.jsonl`（生命周期事件）、`results.jsonl`（通过/失败的权威记录）、
`spend.jsonl`（实时成本与在途调用数的权威记录）。`--models` / `--reps` 参数
仅在没有 `expected.json` 的旧目录上兜底；清单存在时始终以清单为准。

## 7. 最终汇总

```bash
/tmp/p117/venv/bin/python harness/summarize.py
```

写出 `out/results.json` 与 `out/results.md`（并打印表格）。只统计四个官方模型。
官方测量（2026-11-13）的头条数字：Kimi 15/18（$0.257/通过）、GLM 9/18（$1.021）、
Opus 15/18（$0.418）、Fable 18/18（$1.111）；现场复测（2026-11-14）：
14/10/14/18。小幅波动属正常（温度、重试、延迟）；大幅偏离则需先排查再比较。


## 8. 输出产物参考

每个输出目录（`out/`、`out-smoke-v2/`、任意 `P117_OUT`）内：

| 文件 | 写入方 | 内容 |
|---|---|---|
| `expected.json` | 运行器，启动时 | 按泳道顺序的计划运行（`run_id`、`model`、`task`、`rep`） |
| `events.jsonl` | 运行器 | 每行一条生命周期事件：`run_start`、`run_end`（含 `passed`、`stop`、`calls`、`usd`、`wall_s`）、`run_skip` |
| `results.jsonl` | 运行器 | 每次完成运行一行：计数、`usage` token、`usd`、`wall_s`、`stop`、`format_failures`、`api_errors`、`grade`（隐藏 + 全量套件）、`pass`、`finished_at` |
| `spend.jsonl` | 计量器 | 每次调用一行：归一化 `usage`、`usd`、累计 `total_usd` |
| `runs/<run_id>/transcript.json` | 运行器 | 逐次调用的原文内容、工具调用、工具结果、用量 |
| `runs/<run_id>/diff.patch` | 运行器 | 智能体仓库相对纯净起点的 `diff -ruN` |
| `results.json`、`results.md` | 汇总器 | 四个官方模型的汇总表 |

一次运行只有在隐藏套件与全量套件**都**以 0 退出时才通过，且至少有 1 个
测试通过、零 skip/xfail/xpass。`trialops/*.py` 与 `tests/test_*.py` 之外的
任何改动都会自动判负（评分尾部显示 `unauthorized file changes`）。
停止原因：`finish`、`max_calls`（30）、`wall_clock`（20 分钟）、
`no_tool_calls`（连续 3 轮空转）、`api_error`、`credentials_expired`、
`budget_hard_stop`。

## 9. 故障排查

| 症状 | 原因 → 处理 |
|---|---|
| `/tmp/p116/venv` 报 `No module named pytest` | 该环境已损坏。按 §2 新建——不要修复。 |
| 日志 `credentials expired, stopping this worker` | SSO/STS 过期 → `aws sso login --profile <your-aws-profile>`，同一命令续跑；已完成运行会被跳过。 |
| `global.*` 推理配置反复 `503` | Global CRIS 容量限流（原始运行中命中过 Opus）。把 `harness/run_matrix.py:MODELS` 中的模型 ID 换成 US 配置（如 `us.anthropic.claude-opus-5-5`），**并**更新 `harness/meter.py:PRICES` 的价格（US CRIS $4.40/$22/$0.22 vs Global $4/$20/$0.20），并像 README 那样记录替换。 |
| Kimi/GLM 泳道全部快速连接失败 | 代理未监听 → 重启 `k3eval proxy …`（§2）；检查 `lsof -iTCP:8787 -sTCP:LISTEN`。 |
| 评分尾部 `unauthorized file changes: …` | 刻意设计：智能体把临时/探针文件留在了 `trialops/` 之外，或在 `tests/` 放了非 `test_*` 文件。该次运行确属失败。 |
| 评分尾部 `ImportError: cannot import name …` | 智能体漏掉了多部分提示的另一半（如 `execute_reuse_export`）。同样确属失败——查看 `runs/<run_id>/diff.patch`。 |
| 转录中 `error: tests timed out after 90s` | 智能体代码很可能死循环；按原样评分。 |
| 仪表板在运行器工作时全显示待运行 | `--out` 不匹配：仪表板必须读取与运行器 `P117_OUT` 相同的目录。 |
| 仪表板端口被占 | 换 `--port 8789`（或任意空闲端口）。 |
| 需要清零 | `rm -rf out out-smoke out-opus-us out-smoke-v2 out-live /tmp/p117/runs` —— 同时重置结果与计量器。没有其他状态。 |

## 10. 公平性注意事项

- 在进行受测集合**之前**固定提示词、工具、限制与隐藏测试（如
  `harness/agent.py` 与 `tasks/` 当前状态）；矩阵中途改动会使比较失效。
- 让 `harness/meter.py:PRICES` 与实际调用路由的 AWS 标价保持一致
  （Global 与 US CRIS 不同），否则每次通过成本的比较会漂移。
- 3 次重复能暴露较大的可靠性差距，但无法区分相差一次运行的模型——
  请报告通过次数，而非单次印象。

