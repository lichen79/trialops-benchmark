# Test Design Document — the Bedrock Agent Matrix

This document explains how the benchmark in `demo/` is designed, why each design
decision was made, and how to proceed: validate, run, extend, and interpret it.
It complements `README.md` (results), `USERGUIDE.md` (operations), and
`out-live/REPORT.md` (the latest measurement analysis).

## 1. Purpose and non-goals

**What it measures.** How reliably and how cheaply four foundation models,
served on Amazon Bedrock, act as autonomous coding agents on six well-specified
Python jobs in one repository — under an identical prompt, identical tools, and
identical hard limits, with correctness decided solely by authoritative hidden
pytest suites.

**What it deliberately is not** (per the README's own limits):

- Not long-horizon software engineering — these are six harder but
  well-specified jobs, completable in ≤ 30 model calls.
- Not a vendor-agent comparison — the harness is the same minimal loop for
  everyone; a vendor's own coding agent (with its own tools/scaffolding) may
  perform differently.
- Not a statistically tight ranking — 3 repetitions per job expose large
  reliability gaps but cannot separate models one run apart.
- Not an AWS bill — costs are measured tokens × public list prices.

## 2. Design principles

| Principle | How it is enforced |
|---|---|
| Vendor neutrality | One system prompt, one tool set, one set of limits for all models (`harness/agent.py`); adapters only translate wire format |
| Grading cannot be gamed | Pristine-checkout grading; agent-authored tests and pytest config are discarded; hidden suites are authoritative |
| Everything auditable | Verbatim `transcript.json` + final `diff.patch` per run; append-only `results.jsonl` / `spend.jsonl` / `events.jsonl` ledgers |
| Reproducibility | Fixed starter repo (`taskrepo/`), fixed prompts (`tasks/tasks.json`), fixed hidden tests (`tasks/*/test_hidden_*.py`), pinned runtime (Python 3.11, `tzdata==2025.2`), independent reference mirrors that must pass every hidden test |
| Cost discipline | Per-call metering to a ledger; hard stop at $90 against a $100 cap; resumable matrix (completed run IDs are never repeated) |
| Honest reporting | Failures are kept and classified (including infra ones); route substitutions (Global→US CRIS) documented with prices, not hidden |

## 3. Architecture at a glance

```
                 ┌──────────────────────────── run_matrix.py ───────────────────────────┐
                 │  expected.json (manifest)   12 lanes = (model × rep), ThreadPool      │
                 └───────┬──────────────────────────────────────────────────────────────┘
                         │ per run: fresh copy of taskrepo/
                 ┌───────▼────────┐    tools: list/read/write/edit/run_tests/finish
                 │  agent.py loop │ ────────────────────────────────────────────────┐
                 │  ≤30 calls     │    ┌─────────────┐        ┌──────────────────┐  │
                 │  ≤20 min wall  │───▶│ OpenAIAdapt.│──▶ SigV4 proxy :8787 ──▶ Bedrock OpenAI surface (Kimi, GLM)
                 └───────┬────────┘    ├─────────────┤        ├──────────────────┤  │
                         │             │ ConverseAdap│──▶ boto3 ──────────────▶ Bedrock Converse (Opus, Fable)
                         │             └─────────────┘        └──────────────────┘  │
                         │ meter.record() per call ──▶ spend.jsonl (hard stop $90)  │
                 ┌───────▼────────┐                                                   │
                 │    grade()     │  pristine checkout + trialops/*.py changes only   │
                 │  hidden+full   │  + injected hidden suite → exit-code verdict      │
                 └───────┬────────┘                                                   │
                         ▼
            results.jsonl ──▶ summarize.py ──▶ results.json / results.md
            events.jsonl ──▶ dashboard.py  ──▶ http://127.0.0.1:8788 (live)
```

Data always flows one way: runner → ledgers → readers. Readers (summarizer,
dashboard) never write; the dashboard is read-only by construction.


## 4. Task design

### 4.1 The throwaway repository (`taskrepo/`)

A synthetic clinical-trial-operations package (`trialops`) with six modules and
baseline visible tests. **No real patient data** — everything is fictional.
`pyproject.toml` pins Python `>=3.11,<3.12` and `tzdata==2025.2`; pytest's
`testpaths = ["tests"]`. Every run starts from a fresh copy of this repo, so
runs are independent and the final `diff.patch` fully captures what the agent did.

### 4.2 The six jobs (`tasks/tasks.json`)

Each job is a single long prompt with a **two-part structure**: a self-contained
base feature, then a realistic integration extension that composes with the
other modules. The starter deliberately omits the integrated APIs.

| Job | Base feature | Integration extension (the discriminator) |
|---|---|---|
| t1-eligibility | `evaluate_subject`: versioned consent + exact age/lab boundaries + deterministic reason order | (prompt adds boundary/leap-day test requirements) |
| t2-privacy | `export_subjects`: consent/purge filtering, HMAC-SHA256 pseudonyms, canonical manifest hash | `execute_reuse_export` delegating exactly once to `governance.apply_reuse_action` |
| t3-audit | `AuditChain` append + `verify` with stable error codes | `verify(expected_head/length)` + `AuditChain.from_records` restore-and-continue |
| t4-scheduling | `plan_visits`: month-end clamping, zoneinfo DST gaps/ambiguous times, capacity/closures | `book_subject_visits`: eligibility gate + atomic persistent capacity reservation |
| t5-safety | `triage_events`: follow-up chain merging, seriousness precedence, reporting deadlines | (prompt adds aware-`now`/overdue edge rules) |
| t6-governance | `apply_reuse_action`: role-guarded state machine with machine-readable error codes | `privacy_plan` replaces the boolean; `executor=` hook for export execution |

Why the two-part structure exists: it separates "can write one function" from
"reads the whole prompt and composes across modules". The measured results
validate this: the integration parts are exactly where failures cluster
(Opus 0/6 on t2 across both sessions never implemented `execute_reuse_export`;
Kimi/GLM 0/6 and 0/6 on t4's booking integration).

The following subsections detail each job's contract, design intent, and
measured behavior across both recorded sessions (official 2026-11-13 and live
2026-11-14; failure evidence in `out-live/REPORT.md`).

#### Job t1 — Versioned consent and eligibility (`evaluate_subject`)

- **Contract.** `evaluate_subject(subject, protocol, on)` returns
  `{"eligible": bool, "reasons": list[str], "consent_version": str | None}`.
  Subject: `birth_date` (ISO date), `consents` (`{version, signed_on}`), `labs`
  (name → number), `flags`. Protocol: `min_age`, `max_age`,
  `required_consent_version`, `consent_valid_from`, `labs` (name → inclusive
  `[low, high]`), `exclusions`.
- **Rules.** Select the latest consent by `signed_on` across *all* versions at
  or before `on` — no fallback to an older consent. Reasons in deterministic
  order: `CONSENT_MISSING`, `CONSENT_VERSION`, `AGE_BELOW_MIN`, `AGE_ABOVE_MAX`,
  each `EXCLUSION_<FLAG>` in protocol order, then `LAB_<NAME>_LOW/HIGH` sorted
  by lab name. Age is computed exactly on `on`; age and lab limits are
  inclusive. The selected consent's version is returned even when it is the
  wrong version. Missing/malformed required input raises `ValueError` naming
  the field — never a raw `KeyError` or datetime exception.
- **Design intent.** Pure date/interval logic with high trap density: leap-day
  birthdays, same-day consent, retired consent versions, exact boundary
  inclusion, the "latest, no fallback" selection rule, and a fully pinned
  reason ordering.
- **Measured.** Solved 3/3 by Kimi, Opus and Fable in both sessions; GLM 2/3
  (its misses were process failures, not logic).
- **References.** [ISO 8601](https://en.wikipedia.org/wiki/ISO_8601) date-only
  inputs · [Python `datetime`](https://docs.python.org/3/library/datetime.html)
  leap-year semantics.

#### Job t2 — Governance-driven research export (`export_subjects`, `execute_reuse_export`)

- **Contract.** `export_subjects(subjects, *, purpose, secret, on)` returns
  `{"rows": [...], "manifest": {"algorithm": "sha256", "count": N, "sha256":
  digest}}`. Include a record only when it has a consent for `purpose` with
  `signed_on <= on` and (no withdrawal or `withdrawn_on > on`), and
  `purge_after >= on` — all boundaries inclusive.
- **Rules.** Pseudonym = first 16 lowercase hex of
  `HMAC-SHA256(key=UTF-8(secret), msg=UTF-8(purpose + ':' + id))` — keyed and
  purpose-domain-separated. Each row carries only `pseudonym`, `country`,
  `birth_year`, chronologically sorted `visits`; rows sorted by pseudonym.
  Allowlist-only: `id`, `name`, `email`, full birth date, consent records,
  `purge_after` and the secret are never emitted — including inside error
  messages. IDs must be ASCII `[A-Za-z0-9_-]+`; duplicates raise `ValueError`
  with code `DUPLICATE_SUBJECT_ID`; JSON rejects NaN/Infinity
  (`allow_nan=False`); inputs never mutated; empty input is valid. Manifest
  digest = SHA256 of `json.dumps(rows, sort_keys=True, separators=(',', ':'),
  ensure_ascii=False)` encoded UTF-8 — canonical serialization pinned
  byte-for-byte.
- **Integration.** `execute_reuse_export(request, subjects, *, actor, secret,
  now)` delegates exactly once to `apply_reuse_action(request, 'execute',
  actor, now, executor=...)`; the executor calls `export_subjects` exactly
  once; `export_subjects` stays compatible.
- **Design intent.** Privacy engineering as code: allowlist field selection,
  pseudonym domain separation, canonical hashing, error hygiene, and a strict
  single-delegation contract with t6. It is also the multi-part-prompt
  discriminator: Opus implemented `export_subjects` but never wrote
  `execute_reuse_export` — 0/6 across both sessions, failing at import time.
- **References.** [RFC 2104 — HMAC](https://www.rfc-editor.org/rfc/rfc2104) ·
  [GDPR Art. 4(5) pseudonymisation](https://gdpr-info.eu/art-4-gdpr/) ·
  [RFC 8785 — JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785).

#### Job t3 — Audit-chain integrity and restore (`AuditChain`)

- **Contract.** Append-only hash chain anchored at `GENESIS_HASH = '0' * 64`.
  `append(*, event_id, actor, action, at, payload)` returns a record with
  `sequence` from 1, `previous_hash`, and `hash`. `at` must be timezone-aware,
  normalized to `YYYY-MM-DDTHH:MM:SSZ` (microseconds rejected). `hash` = SHA256
  over the canonical JSON of every record field except `hash`
  (`sort_keys=True, separators=(',', ':'), ensure_ascii=False,
  allow_nan=False`). Empty event/actor/action strings and duplicate event IDs
  are rejected.
- **Rules.** `verify(records)` returns a deterministic list of error codes, not
  a boolean: bad sequence, broken `previous_hash` linkage, wrong record hash,
  duplicate IDs, non-increasing timestamps, malformed timestamps/hashes,
  non-dict payloads; valid or empty chains return `[]`. The extended
  `verify(records, *, expected_head, expected_length)` adds `HEAD_MISMATCH` /
  `LENGTH_MISMATCH` against trusted anchors and must not re-normalize stored
  timestamp representations. This detects un-rehashed edits and, with the
  trusted anchors, rehashed edits and suffix truncation; cross-chain replay is
  an explicit non-goal.
- **Restore.** `AuditChain.from_records(records, *, expected_head,
  expected_length)`: both trusted arguments required; materialize the input
  once and validate with `verify`; an empty snapshot is valid only with length
  zero and the genesis head. On errors raise `ValueError` with exactly
  `INVALID_CHAIN:` plus the sorted codes joined by commas. On success return an
  independent chain holding a deep copy, with `previous_hash` and the
  duplicate-ID index restored; the next append uses sequence
  `expected_length + 1` and the restored head.
- **Design intent.** Tamper-evident logging in miniature: canonical
  serialization discipline, deterministic error enumeration, the exact
  `INVALID_CHAIN:` message format (the separator trap GLM hit in the official
  session), and restoration invariants — deep-copy independence, restored
  indexes, continued sequence. Kimi's live r3 crashed with a raw `TypeError`
  instead of the contracted `ValueError` on a malformed snapshot (56/57 hidden).
- **References.** [Hash chain](https://en.wikipedia.org/wiki/Hash_chain) ·
  [RFC 6962 — Certificate Transparency](https://www.rfc-editor.org/rfc/rfc6962)
  (production append-only transparency logs) ·
  [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785).

#### Job t4 — DST-safe scheduling with persistent reservations (`plan_visits`, `book_subject_visits`)

- **Contract.** `plan_visits(*, first_day, offsets_months, hour, minute,
  timezone, closed_days=None, capacity=None)`: monthly candidates derive from
  `first_day` + N calendar months, the day clamped to the target month's last
  day — always from `first_day`, never from a previously clamped or rescheduled
  visit (Jan 31 → Feb 28 → Mar 31, not Mar 28). Offsets are unique nonnegative
  integers; local datetimes built with `zoneinfo` in the given IANA zone;
  invalid zones and out-of-range times rejected.
- **Rules.** Days in `closed_days` or with zero remaining capacity move forward
  to the next available day (search bounded to 366 days, else `ValueError`
  `UNSCHEDULABLE` and no partial plan); the original local wall time is
  preserved on day moves; nonexistent local times (spring-forward gap) move
  forward by the gap; ambiguous times use `fold=0`; `capacity` maps dates to
  positive remaining slots and is never mutated; earliest-feasible greedy
  allocation in protocol order. Items: `{"offset_months", "local" (with
  seconds), "utc" (with Z)}`.
- **Integration.** `book_subject_visits(subject, protocol, *, on, first_day,
  offsets_months, hour, minute, timezone, capacity, closed_days=None)` calls
  `evaluate_subject(subject, protocol, on)` first: if ineligible, raise
  `ValueError` with `INELIGIBLE:` plus the ordered reasons and do not touch
  capacity. All candidate generation, closures, DST handling, validation and
  within-plan allocation delegate to `plan_visits`. Only after complete
  success, decrement capacity entries for returned visit days **already
  listed**; unlisted dates retain unconstrained behavior and are never
  inserted; any failure leaves capacity exactly unchanged; earlier successful
  calls remain committed (sequential slot contention).
- **Design intent.** The hardest job: calendar arithmetic (month-end clamp),
  tz-database reality (gaps/folds), and atomic reservation semantics with
  rollback. It also carries the pinned-format trap — the exact string
  `INELIGIBLE:A,B` (no spaces) cost Kimi all 6 runs across both sessions
  despite 25/26 hidden tests passing. GLM additionally missed the real-dict
  type check (accepted `mappingproxy` capacity) and the slot decrement.
- **Measured.** Fable 6/6; Opus 3/3 official and 2/3 live (one `.tmp`-file
  grader reject); Kimi and GLM 0/6.
- **References.** [PEP 615 — `zoneinfo`](https://peps.python.org/pep-0615/) ·
  [IANA Time Zone Database](https://www.iana.org/time-zones) ·
  [`datetime.fold`](https://docs.python.org/3/library/datetime.html#datetime.datetime.fold).

#### Job t5 — Safety follow-up triage (`triage_events`)

- **Contract.** `triage_events(events, *, now)` merges follow-up chains (via
  `follow_up_of`) into one case per chain, for fictional safety-event
  follow-ups — explicitly not medical judgment. Events: `event_id`,
  `subject_pseudonym`, `event_code`, `onset` (timezone-aware ISO datetime),
  `seriousness` (`fatal`, `life_threatening`, `hospitalization`, `other`),
  `source`, optional `follow_up_of` and `uncertain`.
- **Rules.** Reject duplicate event IDs, missing roots, cycles, or chains
  mixing subject pseudonyms or event codes. Per case: `case_id` (root ID),
  `onset` (earliest, UTC `Z`), `seriousness` (highest by fatal >
  life_threatening > hospitalization > other), `report_by` (earliest onset +
  7 days for fatal/life_threatening, 15 for hospitalization, 30 for other —
  the clock runs from earliest onset, never from follow-up arrival), `sources`
  (sorted unique), `event_ids` (sorted), `uncertain` (true if any event has
  it), `overdue` when `report_by < now` (strict; equality is not overdue;
  `now` must be aware). Cases sorted by onset then `case_id`; input order must
  not affect output. Unknown seriousness, naive timestamps and invalid links
  raise `ValueError` with stable codes. No fuzzy matching, no invented
  diagnoses, no real reporting rules.
- **Design intent.** Graph validation on a parent-pointer forest (cycles,
  missing roots) plus deterministic, order-invariant aggregation; the
  "fictional triage only" framing tests requirement-boundary discipline.
- **Measured.** Kimi, Opus and Fable 3/3 in both sessions; GLM 1/3 official
  and 2/3 live, losing runs to `max_calls` and scratch-file rejects.
- **References.** [ICH E2A pharmacovigilance guideline](https://database.ich.org/sites/default/files/E2A_Guideline.pdf)
  (the real-world analogue the task deliberately simplifies) ·
  [Disjoint-set / cycle detection](https://en.wikipedia.org/wiki/Disjoint-set_data_structure).

#### Job t6 — Data-reuse governance workflow (`apply_reuse_action`)

- **Contract.** `apply_reuse_action(request, action, actor, now, executor=None)`
  drives a state machine over `draft → submitted → approved → executed`, plus
  `rejected` and `expired`. The request carries `id`, `requester`, `reviewer`,
  `purpose`, `status`, `retention_until`, `privacy_plan` (exactly
  `{"algorithm": "hmac-sha256", "domain": purpose, "fields": ["pseudonym",
  "country", "birth_year", "visits"]}`), `metadata` (`representative`,
  `complete`, `quality_score`) and `history`.
- **Rules.** `submit`: requester only, draft → submitted. `approve`: reviewer
  only, submitted → approved, sets `approval_expires_on = now + 30 days`.
  `reject`: reviewer only, submitted → rejected. `execute`: requester only,
  approved → executed. `expire`: any actor, approved/submitted → expired when
  `now > approval_expires_on` (approved) otherwise `now > retention_until`.
  Approval requires requester ≠ reviewer (`SELF_REVIEW`), the exact
  `privacy_plan`, representative and complete metadata (`METADATA_REQUIRED`),
  `quality_score >= 0.95`, `now <= retention_until` (`RETENTION_EXPIRED`) and
  a nonempty purpose. Execution additionally requires actor = requester,
  `now <= approval_expires_on` (`APPROVAL_EXPIRED`), the same privacy/metadata
  checks, and a callable `executor`. Every action deep-copies the request
  (caller input never mutated) and appends `{action, actor, at}` to history
  with aware `now` normalized to UTC `Z`. Role violations raise
  `PermissionError`; invalid states or failed checks raise `ValueError` with
  stable codes (`INVALID_STATE`, …).
- **Design intent.** Workflow rigor: separation of duties, guard ordering,
  two different expiry clocks (approval vs retention), deep-copy discipline,
  and the `executor` hook that t2's `execute_reuse_export` must call exactly
  once — the cross-job integration seam.
- **Measured.** The most uniformly solved job: 3/3 for every model in the
  official session; GLM lost one live run to a scratch-file reject.
- **References.** [Separation of duties](https://en.wikipedia.org/wiki/Separation_of_duties) ·
  [Finite-state machine](https://en.wikipedia.org/wiki/Finite-state_machine).

### 4.3 Hidden test suites (`tasks/<job>/test_hidden_*.py`)

2,249 lines total, **195 parametrized cases** (19/33/57/26/15/45 per job). The
recurring test classes are intentional pressure points:

- **Boundary exactness**: inclusive age/lab limits, leap-day birthdays, same-day
  consent, `report_by < now` (equality is *not* overdue).
- **Time/tz traps**: month-end clamping (Jan 31 → Feb 28 → Mar 31), DST
  spring-forward gaps (move forward by the gap), fall-back ambiguity (`fold=0`),
  UTC `Z` normalization, microseconds rejected.
- **Canonicalization**: `json.dumps(..., sort_keys=True, separators=(',',':'),
  ensure_ascii=False)` hashing; `allow_nan=False`.
- **Exact error contracts**: stable machine-readable codes and exact message
  formats — e.g. `"INELIGIBLE:" + ",".join(reasons)`,
  `INVALID_CHAIN:<sorted codes comma-joined>`. (This pinned-separator style is
  the one deliberate strictness trap; see §9.4.)
- **Immutability & atomicity**: inputs never mutated, deep-copy semantics,
  duplicate-ID rejection, rollback on any failure (capacity unchanged),
  commit-only-on-full-success.
- **Adversarial structures**: follow-up cycles, missing roots, cross-subject
  chains, tampered audit links, rehashed vs un-rehashed edits, suffix
  truncation, mappingproxy passed where a real dict is required.

### 4.4 Visible tests vs hidden tests

- **Visible** (`taskrepo/tests/`, 39 functions): baseline coverage the agent may
  run freely with the `run_tests` tool. They are necessary but *not sufficient*.
- **Hidden** (`tasks/`): injected only at grading time, in a pristine checkout.
  The agent never sees them. A run passes **only** when both the hidden suite
  and the full suite exit 0, with ≥ 1 test passed and zero skips/xfails/xpasses
  (a run that quietly collects nothing is a fail, not a pass).

### 4.5 Reference mirrors (`reference/<job>/`)

Each job has an **independent** mirror containing a complete, correct
implementation. Their purpose is ground truth: every hidden suite must pass
against its mirror before any model is measured (verified: 19/33/57/26/15/45 =
195 hidden cases green; the README's 477 counts mirror visible suites too).
Layout note: t1–t4 carry the hidden file at the mirror root, t5–t6 need it
injected into `tests/` — see USERGUIDE.md §3 for the exact recipes.


## 5. The agent loop (`harness/agent.py`)

### 5.1 Fixed scaffolding

- **System prompt**: "autonomous coding agent … minimal, correct changes; keep
  existing behaviour and tests passing; add tests when asked; call `finish` …
  no human in the loop." Short on purpose: it measures the model, not prompt
  engineering.
- **Six tools, nothing more**: `list_files`, `read_file`, `write_file`,
  `edit_file`, `run_tests`, `finish`. `edit_file` demands exactly-one-occurrence
  replacement (safe, deterministic edits); `run_tests` is plain `pytest -q`
  with a 90 s timeout and a 6,000-char tail. Tool results are truncated
  (20,000 chars to the model) to keep contexts bounded.
- **Workspace sandbox**: every path is resolved inside the repo copy; escapes
  are rejected (`path escapes the repository`).
- **Hard limits**: 30 model calls, 20 minutes wall, 16,384-token completions,
  provider-default reasoning settings. Identical for every model.
- **Resilience**: up to 4 attempts per call with backoff (extra wait on 429);
  400/404/422 abort early; expired credentials (`401/403` + "expired") stop the
  lane with `credentials_expired` so it can be resumed after re-login.
- **Behavioral telemetry** (all recorded per run): tool-call counts,
  malformed-argument `format_failures`, idle `no_tool_turns` (3 consecutive
  turns with no tool call end the run as `no_tool_calls`), `api_errors`, and
  the `stop` reason (`finish`, `max_calls`, `wall_clock`, `no_tool_calls`,
  `api_error`, `credentials_expired`, `budget_hard_stop`).

### 5.2 Two API surfaces, one semantics

| | OpenAIAdapter (Kimi, GLM) | ConverseAdapter (Opus, Fable) |
|---|---|---|
| Endpoint | Bedrock OpenAI-compatible Chat Completions via local SigV4 proxy `127.0.0.1:8787` | Bedrock Converse via boto3 in us-west-2 |
| Tool schema | `tools=[{type:function, function:{...}}]` | `toolConfig.tools[].toolSpec` (same JSON schema) |
| Usage normalization | `prompt_tokens`, `completion_tokens`, `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.cached_tokens` (**cached is a subset** of prompt tokens) | `inputTokens + cacheRead + cacheWrite` (**cache fields are additive**), `outputTokens`; no reasoning breakdown |
| Malformed args in history | `_replayable()`: invalid JSON replayed as `{}` (Bedrock re-validates earlier calls every request) with the failure counted | same shared helper |

Known asymmetry (kept deliberately, and disclosed): the Claude adapters set no
cache points, so Claude costs are upper bounds; Kimi/GLM show 89–94% of input
tokens as cache-read at ~1/10th price. Wire formats are translated, not
byte-identical.

### 5.3 Grading (`grade()`)

1. Diff the agent's repo against the pristine starter (ignoring `__pycache__`,
   `.pytest_cache`).
2. **Allowlist**: changes must be inside `trialops/*.py` or `tests/test_*.py`.
   Anything else (scratch files, `.tmp` files, pytest config, helper modules)
   → automatic fail with `unauthorized file changes: …`. This one rule caught
   7 of 16 failures in the latest session (§3 of `out-live/REPORT.md`).
3. Apply only the `trialops/` changes to a fresh pristine checkout in a tempdir;
   inject the authoritative hidden test file into `tests/`.
4. Run the hidden suite alone, then the full suite (`-p no:cacheprovider`,
   `--override-ini addopts=` so neither agent nor environment config can leak
   in; 90 s timeout).
5. **Pass = both exit 0**, with ≥ 1 passed and zero skips/xfails/xpasses.

Agent-authored tests and pytest configuration never enter the grading checkout
by design — a model cannot inflate its score by weakening or deleting tests,
adding skips/xfails, or tuning collection.


## 6. Metering (`harness/meter.py`)

- Per-call cost = `fresh_input × in_price + cache_read × read_price +
  cache_write × write_price + output × out_price`, where `fresh = input −
  cached − written` (the normalization in §5.2 keeps both API surfaces honest).
- Prices are AWS list prices **for the route actually used** (Global vs US
  CRIS). Example: Opus was moved to `us.anthropic.claude-opus-5-5` at
  $4.40/$22/$0.22 after repeated Global 503s — the substitution and its
  counterfactual are documented in the README, not silently absorbed.
- Every call appends one line to `spend.jsonl`; the running total is the
  **meter**. At $90 no new model call starts (`budget_hard_stop`), protecting
  the $100 cap. The meter is per output directory, so a fresh `P117_OUT` starts
  at $0.00.

## 7. Matrix execution and live observability

- **Lanes**: one per (model, rep) → 12 lanes for the official 4×3, each walking
  the six tasks in order; `ThreadPoolExecutor(12)`. Tasks are sequential inside
  a lane, so a lane's early tasks never race its later ones.
- **Resume**: any `run_id` already in `results.jsonl` is skipped before any
  other check; Ctrl-C is always safe, and partial spend of an interrupted run
  stays in the ledger by design.
- **Live state** (added for observability): the runner writes `expected.json`
  (the full planned manifest) at startup and appends
  `run_start` / `run_end` / `run_skip` events to `events.jsonl`.
- **Dashboard** (`harness/dashboard.py`, stdlib-only, read-only): rebuilds
  expected/done/running/pending, per-model stats, spend-vs-cap, and an event
  feed from those four files on every 2 s poll. Directories recorded before
  `events.jsonl` existed still render — in-flight runs are inferred from spend
  rows lacking a results row. The manifest always wins over CLI fallbacks, so
  the dashboard mirrors exactly what the runner planned.

## 8. Statistical design and interpretation

- **3 reps per job** is a deliberate cost/signal trade-off: enough to expose a
  reliability cliff (e.g. 0/3 vs 3/3 on a task) and deterministic-vs-random
  behavior, not enough to rank models one run apart. Two independent 72-run
  sessions (official 57, live 56 passes) confirm: per-model totals moved by
  ≤ 1 run, and every deterministic failure recurred exactly.
- **Interpret stop reasons before pass counts.** A `finish` failure is a
  content failure (the model believed it was done — see Opus t2, Kimi t4); a
  `max_calls`/`wall_clock` failure is a process failure (never converged — the
  GLM profile). They imply different fixes and different capability stories.
- **$ / pass** is the efficiency metric of record; raw $ / run is misleading
  when pass rates differ. Claude costs are upper bounds (no cache points).



## 9. How to proceed

### 9.1 Validation gates (in order, every time the design changes)

1. **Reference gates**: every hidden suite green against its reference mirror
   (recipes in USERGUIDE.md §3; expected 19/33/57/26/15/45).
2. **Smoke gate**: one cheap run (`P117_OUT=out-smoke-v2 … --models
   kimi-k3.1-pro --tasks t1 --reps 1`) — proves proxy, credentials, grading,
   ledger, dashboard end-to-end for ≈ $0.08–0.40.
3. **Matrix gate**: full `--reps 3`; watch progress live on the dashboard;
   then `summarize.py` (always reads `out/`).

### 9.2 Add a new task (checklist)

1. Author the prompt in `tasks/tasks.json` (`id: tN-name`, two-part structure:
   base feature + integration extension; pin exact error contracts).
2. Extend `taskrepo/`: module stub in `trialops/` + baseline visible tests in
   `tests/test_*.py` (necessary-not-sufficient).
3. Write `tasks/tN-name/test_hidden_<name>.py` — cover the §4.3 pressure
   classes (boundaries, tz, canonicalization, exact errors, immutability,
   adversarial structures).
4. Create `reference/tN-name/` with a complete implementation; run gate §9.1.1
   until the hidden suite is fully green. If the mirror carries the hidden file
   at its root, run it directly (copying into `tests/` collides by basename).
5. Smoke one model on the new task (`--tasks tN`) before adding it to a matrix.

### 9.3 Add a new model (checklist)

1. `harness/run_matrix.py:MODELS` — label → (`openai` | `converse`, Bedrock
   model ID). OpenAI-compatible models go through the SigV4 proxy; Claude-class
   models through Converse.
2. `harness/meter.py:PRICES` — the list price **of the route you will call**
   (Global vs US CRIS differ; see §6).
3. Confirm model access in us-west-2 (Console → Bedrock) and, for open-weight
   models, that the proxy signs for that model family.
4. For official reporting also update `summarize.py:ORDER/OPEN` and
   `dashboard.py:DEFAULT_MODELS/OPEN_WEIGHTS`. Smoke first (§9.1.2).

### 9.4 Known limitations and open design questions

- **Cache asymmetry**: Claude costs are upper bounds (no cache points); consider
  enabling cache checkpoints on Converse for a like-for-like cost comparison.
- **Pinned-separator trap**: the `INELIGIBLE:`/`INVALID_CHAIN:` exact-format
  assertions discriminate on a detail the prompt under-specifies. Two fair
  options: pin the exact format in the prompt text, or keep as-is and report it
  as a deliberate strictness trap — but do not mix both philosophies silently.
- **Shared 90 s test timeout** applies to agent `run_tests` and grading alike;
  pathological agent code (infinite loops) is graded as-is.
- **Single-author bias**: one person wrote jobs, hidden tests, and grader.
  Independent review of the hidden suites is the cheapest credibility win.
- **Reps**: 3/job is the documented trade-off; raising to 5 roughly doubles
  matrix cost (budget accordingly — the meter hard stop is per output dir).
- Ideas backlog: more tasks of the integration kind (they discriminate best);
  per-run difficulty annotations in `results.jsonl`; SSE push in the dashboard
  instead of 2 s polling; CI gate that runs §9.1.1 on every task change.

## 10. Data schemas (for tooling authors)

- `results.jsonl`: `{run_id, model, task, rep, stop, calls, tool_calls,
  format_failures[], no_tool_turns, api_errors[], usage{input, output,
  reasoning, cache_read, cache_write}, usd, wall_s, visible_tests_run,
  model_id, adapter, grade{hidden{exit, passed, failed, errors, skipped,
  xfailed, xpassed, tail}, suite{…}}, pass, finished_at}`
- `spend.jsonl`: `{t, run, model, usage{…}, usd, total_usd}` — one row per call.
- `events.jsonl`: `{t, kind: run_start|run_end|run_skip, run_id, model, task,
  rep, …}` (`run_end` adds `passed, stop, calls, usd, wall_s`).
- `expected.json`: `{generated_at, reps, models[], tasks[], runs[{run_id,
  model, task, rep}]}` in lane order.
