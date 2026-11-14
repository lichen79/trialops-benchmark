# User Guide — Reproducing the Bedrock Model Benchmark

This guide reproduces the full demo measurement from scratch: the 72-run agent
matrix (4 models × 6 jobs × 3 repetitions, ≈ $39 at list price, ≈ 45–90 minutes),
the optional smoke run, the realtime status dashboard, and the final summary
artifacts (`out/results.json`, `out/results.md`).

Everything runs from `presentation117/demo/`. All commands below assume:

```bash
cd presentation117/demo
```

## Quick reference (full procedure in one block)

```bash
# 0. one-time setup
uv venv /tmp/p117/venv --python 3.11
uv pip install --python /tmp/p117/venv/bin/python httpx boto3 "pytest" "tzdata==2025.2"
aws sts get-caller-identity --profile <your-aws-profile>                      # credentials valid?
k3eval proxy --region us-west-2 --profile <your-aws-profile> --host 127.0.0.1 --port 8787 \
  --model us.moonshotai.kimi-k3 &                                  # SigV4 proxy for Kimi/GLM

# 1. preflight: reference mirrors must be green (see §3 for the full loop)
cd reference/t4-scheduling && /tmp/p117/venv/bin/python -m pytest -q test_hidden_scheduling.py && cd ../..

# 2. smoke run (1 run, ≈ $0.20–0.40)
P117_OUT=out-smoke /tmp/p117/venv/bin/python harness/run_matrix.py \
  --models kimi-k3.1-pro --tasks t1 --reps 1

# 3. full matrix (72 runs, ≈ $39) — terminal 1
/tmp/p117/venv/bin/python harness/run_matrix.py --reps 3

# 4. realtime dashboard — terminal 2 (optional but recommended)
python3 harness/dashboard.py --port 8788       # open http://127.0.0.1:8788

# 5. final summary artifacts
/tmp/p117/venv/bin/python harness/summarize.py
```

## 1. Prerequisites

| Requirement | Details | Check |
|---|---|---|
| Python 3.11 | `taskrepo/pyproject.toml` pins `>=3.11,<3.12`; the grading runtime is 3.11 with `tzdata==2025.2`. Any 3.11 works; `uv` is the fastest way to get one. | `uv python list \| grep 3.11` |
| AWS credentials | An AWS CLI profile whose credentials may call Bedrock in **us-west-2**. This guide uses the placeholder `<your-aws-profile>`; substitute your own AWS CLI profile name everywhere. | `aws sts get-caller-identity --profile <your-aws-profile>` |
| Bedrock model access | Enabled in us-west-2 for: `global.moonshotai.kimi-k3.1`, `us.zai.glm-5.3`, `us.anthropic.claude-opus-5-5`, `global.anthropic.claude-fable-5-2` (AWS Console → Bedrock → Model access). GLM 5.3 additionally has an enterprise eligibility gate and is cross-Region-only. | Console |
| `k3eval` SigV4 proxy | Kimi and GLM are served on Bedrock's OpenAI-compatible surface, which the harness reaches through a local signing proxy on `127.0.0.1:8787`. The Claudes use boto3 Converse directly and do not need the proxy (but start it anyway — the harness auto-detects the AWS profile from the proxy process). | `ps aux \| grep "k3eval proxy"` |
| Money | ≈ $39 list price for the matrix + ≈ $6 smoke. The harness hard-stops new model calls at **$90** against a **$100** cap. | — |

> **Note on the original environment:** the README references `/tmp/p116/venv`.
> That venv is broken today (its interpreter no longer resolves its own
> site-packages). Create a fresh one per the quick reference; do not try to
> repair it.

## 2. Setup

```bash
uv venv /tmp/p117/venv --python 3.11
uv pip install --python /tmp/p117/venv/bin/python httpx boto3 pytest "tzdata==2025.2"
```

Harness dependencies, deliberately minimal:

| Package | Used by |
|---|---|
| `httpx` | `OpenAIAdapter` → proxy (Kimi, GLM) |
| `boto3` | `ConverseAdapter` → Bedrock Converse (Opus, Fable) |
| `pytest` | agent `run_tests` tool and the grader |
| `tzdata==2025.2` | pinned tz database for DST-safe scheduling tests |

The dashboard (`harness/dashboard.py`) is **stdlib-only** — any Python 3 runs it,
no venv required.

Start the signing proxy (terminal 0, leave it running):

```bash
k3eval proxy --region us-west-2 --profile <your-aws-profile> --host 127.0.0.1 --port 8787 \
  --model us.moonshotai.kimi-k3
```

`run_matrix.py` reads the `--profile` value out of the proxy's process command
line at runtime (`_proxy_profile()`); credentials are never written to disk by
the harness.

## 3. Preflight checks (≈ 2 minutes)

1. **Proxy alive:** `ps aux | grep "k3eval proxy"` shows the process, and
   `lsof -iTCP:8787 -sTCP:LISTEN` shows the listener.
2. **Credentials valid:** `aws sts get-caller-identity --profile <your-aws-profile>`.
   If your profile is SSO-backed: `aws sso login --profile <your-aws-profile>`.
3. **Reference mirrors green** — proves the tasks, hidden tests, and grader are
   self-consistent before you spend money. Each mirror in `reference/<job>/`
   contains a complete implementation. Two layouts exist, so two recipes:

   ```bash
   # t1–t4: the hidden suite already sits at the mirror root — run it directly
   for job in t1-eligibility t2-privacy t3-audit t4-scheduling; do
     ( cd reference/$job && /tmp/p117/venv/bin/python -m pytest -q -p no:cacheprovider \
       $(ls test_hidden_*.py) | tail -1 )
   done
   # t5–t6: inject the authoritative hidden suite into a tests/ dir
   for job in t5-safety t6-governance; do
     ( cd reference/$job && mkdir -p tests && cp ../../tasks/$job/test_hidden_*.py tests/ \
       && /tmp/p117/venv/bin/python -m pytest -q -p no:cacheprovider | tail -1; rm -rf tests )
   done
   ```

   Expected: **19 / 33 / 57 / 26 / 15 / 45 passed = 195 hidden tests**, matching
   the README. Do **not** copy a hidden file into `tests/` when a copy already
   sits at the mirror root: same-basename modules collide (`import file
   mismatch`). The mirrors are independent per job — always run from inside one
   mirror, and note the grading runtime is 3.11 with `tzdata==2025.2`.

## 4. Smoke run (≈ $0.20–0.40, ≈ 1 minute)

One run end-to-end — agent session, grading, ledger, artifacts — into a
throwaway output directory:

```bash
P117_OUT=out-smoke /tmp/p117/venv/bin/python harness/run_matrix.py \
  --models kimi-k3.1-pro --tasks t1 --reps 1
```

Expected log line (times will vary):

```
HH:MM:SS kimi-k3.1-pro__t1-eligibility__r1: PASS stop=finish calls=9 fmt=0 api_err=0 $0.151 41s hidden 19/19 | meter $0.15
```

Verify the artifacts exist: `out-smoke/results.jsonl` (1 row), `out-smoke/spend.jsonl`
(one row per model call), `out-smoke/events.jsonl` (run_start + run_end),
`out-smoke/expected.json`, and `out-smoke/runs/<run_id>/{transcript.json,diff.patch}`.
`harness/summarize.py` always reads `out/`, never the smoke directory — do not
"summarize" smoke data.

## 5. Full matrix (72 runs, ≈ $39 list price)

```bash
/tmp/p117/venv/bin/python harness/run_matrix.py --reps 3
```

What to expect:

- **Plan:** `tasks/tasks.json` × 4 models × reps 1–3. Twelve lanes run in
  parallel — one per (model, rep); tasks sequential inside a lane.
- **Duration:** ≈ 45–90 min. GLM lanes are the long pole (median ≈ 323 s/run).
- **Resumability:** the runner skips every `run_id` already present in
  `out/results.jsonl`. Ctrl-C and rerun the same command at any time. Spend from
  an interrupted run stays in `spend.jsonl` (append-only) and is counted in the
  meter — a rerun therefore costs the partial amount twice, by design.
- **Budget rails:** once the meter reaches $90 no new model call starts
  (`stop=budget_hard_stop`; unfinished lanes log `skipped, meter at hard stop`).
- **Credentials:** on expired AWS credentials (`401/403` containing "expired")
  the affected worker stops its lane with `credentials_expired`; other lanes
  continue. Re-login and rerun — completed runs are not repeated.
- Useful subsets: `--models kimi-k3.1-pro,glm-5.3`, `--tasks t1,t4` (matches both
  `t1-eligibility` and prefix `t1`), `--reps 1`.

## 6. Realtime dashboard

In a second terminal (any Python 3, no venv needed):

```bash
python3 harness/dashboard.py --port 8788
# open http://127.0.0.1:8788
```

Point `--out` at the same directory the runner writes (must equal the runner's
`P117_OUT`, default `demo/out`):

```bash
python3 harness/dashboard.py --out out-smoke --port 8789   # watch the smoke run instead
```

The page refreshes every 2 s and is strictly read-only. What you see:

| Panel | Content |
|---|---|
| Topline | runs done / passed / failed / running / pending; total spend vs the $90 hard-stop bar |
| Model cards | live pass count and rate, ledger cost, $/pass, median calls & wall, API errors, no-tool turns; "open weights" badge |
| Matrix | one dot per repetition: **green** pass · **red** fail · **pulsing blue** running · **gray** pending. Hover a dot for stop reason, calls, wall, cost, hidden-test score |
| Running now | in-flight runs with live call count, spend so far, elapsed/idle seconds |
| Latest events | last 15 `run_start` / `run_end` / `run_skip` events |

Data sources (all inside `--out`): `expected.json` (run manifest written by the
runner at startup), `events.jsonl` (lifecycle events), `results.jsonl`
(authoritative pass/fail), `spend.jsonl` (authoritative live cost and in-flight
call counts). Flags `--models` / `--reps` are only fallbacks for directories
recorded before `expected.json` existed; when the manifest is present it always
wins, so the dashboard mirrors exactly what the runner planned.

## 7. Final summary

```bash
/tmp/p117/venv/bin/python harness/summarize.py
```

Writes `out/results.json` and `out/results.md` (and prints the table). Only the
four official models are counted; extra rows in `results.jsonl` are ignored.

Expected headline numbers (original measurement, 2026-11-13):

| Model | Passed | Solved 3/3 | Median calls | Median wall | Total | Cost/pass |
|---|---:|---:|---:|---:|---:|---:|
| Kimi K3.1 Pro | 15/18 | 5/6 | 9.5 | 46.7 s | $3.86 | $0.257 |
| GLM 5.3 | 9/18 | 1/6 | 30.0 | 323.0 s | $9.19 | $1.021 |
| Claude Opus 5.5 | 15/18 | 5/6 | 6.0 | 57.3 s | $6.27 | $0.418 |
| Claude Fable 5.2 | 18/18 | 6/6 | 5.0 | 155.6 s | $20.00 | $1.111 |

Small deviations run-to-run are normal (temperature, retries, latency); large
ones are not — investigate before comparing.

## 8. Output artifacts reference

Inside every output directory (`out/`, `out-smoke/`, any `P117_OUT`):

| File | Written by | Contents |
|---|---|---|
| `expected.json` | runner, at startup | Planned runs in lane order (`run_id`, `model`, `task`, `rep`) |
| `events.jsonl` | runner | One lifecycle event per line: `run_start`, `run_end` (with `passed`, `stop`, `calls`, `usd`, `wall_s`), `run_skip` |
| `results.jsonl` | runner | One row per finished run: counts, `usage` tokens, `usd`, `wall_s`, `stop`, `format_failures`, `api_errors`, `grade` (hidden + full suite), `pass`, `finished_at` |
| `spend.jsonl` | meter | One row per model call: normalized `usage`, `usd`, running `total_usd` |
| `runs/<run_id>/transcript.json` | runner | Verbatim per-call content, tool calls, tool results, usage |
| `runs/<run_id>/diff.patch` | runner | `diff -ruN` of the agent's repo vs the pristine starter |
| `results.json`, `results.md` | summarizer | Aggregated tables for the four official models |

A run passes only when **both** the hidden suite and the full suite exit 0, with
at least one test passed and zero skips/xfails/xpasses. Any change outside
`trialops/*.py` or `tests/test_*.py` is an automatic fail (`unauthorized file
changes` in the grade tail). Stop reasons: `finish`, `max_calls` (30),
`wall_clock` (20 min), `no_tool_calls` (3 consecutive idle turns), `api_error`,
`credentials_expired`, `budget_hard_stop`.

## 9. Troubleshooting

| Symptom | Cause → Fix |
|---|---|
| `No module named pytest` from `/tmp/p116/venv` | That venv is broken (lost its site-packages link). Create the fresh venv in §2 — do not repair it. |
| Worker logs `credentials expired, stopping this worker` | SSO/STS credentials lapsed → `aws sso login --profile <your-aws-profile>`, then rerun the same command; completed runs are skipped. |
| Repeated `503` on a `global.*` inference profile | Global CRIS capacity throttling (this hit Opus in the original run). Switch the model ID in `harness/run_matrix.py:MODELS` to the US profile (e.g. `us.anthropic.claude-opus-5-5`) **and** update the price in `harness/meter.py:PRICES` ($4.40/$22/$0.22 US CRIS vs $4/$20/$0.20 Global). Document the substitution as the README does. |
| Kimi/GLM lanes all fail fast with connection errors | Proxy not listening → restart `k3eval proxy …` (§2); check `lsof -iTCP:8787 -sTCP:LISTEN`. |
| Grade tail says `unauthorized file changes: …` | By design: the agent left scratch/probe files outside `trialops/` or non-`test_*` files in `tests/`. The run legitimately failed. |
| Grade tail shows `ImportError: cannot import name …` | The agent missed part of the multi-part prompt (e.g. `execute_reuse_export`). Also a legitimate failure — inspect `runs/<run_id>/diff.patch`. |
| `error: tests timed out after 90s` in a transcript | Agent code likely loops forever; the run is graded as-is. |
| Dashboard shows all pending while the runner works | `--out` mismatch: the dashboard must read the same directory as the runner's `P117_OUT`. |
| Dashboard port busy | `--port 8789` (or any free port). |
| Need a clean slate | `rm -rf out out-smoke out-opus-us /tmp/p117/runs` — resets results **and** the spend meter. There is no other state. |

## 10. Fair-play notes

- Fix prompt, tools, limits, and hidden tests **before** a measured set, exactly
  as `harness/agent.py` and `tasks/` do now; editing them mid-matrix invalidates
  comparisons.
- Keep `harness/meter.py:PRICES` aligned with the AWS list prices of the routes
  you actually use (Global vs US CRIS), or cost-per-pass comparisons drift.
- Three repetitions expose large reliability gaps but cannot rank models one run
  apart — report pass counts, not single-run impressions.


