"""Run six synthetic trial-operations jobs across four models on Amazon Bedrock.

    /tmp/p116/venv/bin/python presentation117/demo/harness/run_matrix.py --reps 3
Results append to demo/out/results.jsonl; spend to demo/out/spend.jsonl.
Completed run IDs are skipped, so the matrix can be resumed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
sys.path.insert(0, str(HERE))

from agent import ConverseAdapter, OpenAIAdapter, grade, run_agent  # noqa: E402
from meter import HARD_STOP_USD, Meter  # noqa: E402

MODELS = {
    # label: (adapter kind, Bedrock model id)
    "kimi-k3.1-pro": ("openai", "global.moonshotai.kimi-k3.1"),
    "glm-5.3": ("openai", "us.zai.glm-5.3"),
    "claude-opus-5.5": ("converse", "us.anthropic.claude-opus-5-5"),
    "claude-fable-5-2": ("converse", "global.anthropic.claude-fable-5-2"),
}

OUT = Path(os.environ.get("P117_OUT", DEMO / "out"))
WORK = Path("/tmp/p117/runs")
RESULTS = OUT / "results.jsonl"
EVENTS = OUT / "events.jsonl"      # lifecycle events for the realtime dashboard
EXPECTED = OUT / "expected.json"   # run manifest written at startup
_lock = threading.Lock()


def _proxy_profile() -> str | None:
    """The AWS profile the running signing proxy uses (read at runtime, never written anywhere)."""
    ps = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True).stdout
    for line in ps.splitlines():
        if "k3eval proxy" in line and "--profile" in line:
            parts = line.split()
            return parts[parts.index("--profile") + 1]
    return None


def done_runs() -> set[str]:
    if not RESULTS.exists():
        return set()
    return {json.loads(l)["run_id"] for l in RESULTS.read_text().splitlines() if l.strip()}


def _event(kind: str, **fields) -> None:
    """Append one lifecycle event for harness/dashboard.py (append-only, best effort)."""
    with _lock:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **fields}) + "\n")


def worker(label: str, tasks: list[dict], reps: list[int], meter: Meter, log) -> None:
    kind, model_id = MODELS[label]
    adapter = OpenAIAdapter(model_id) if kind == "openai" else ConverseAdapter(model_id, _proxy_profile())
    have = done_runs()
    for rep in reps:
        for task in tasks:
            run_id = f"{label}__{task['id']}__r{rep}"
            if run_id in have:
                continue
            if not meter.allow():
                _event("run_skip", run_id=run_id, model=label, task=task["id"], rep=rep,
                       reason="budget_hard_stop")
                log(f"{run_id}: skipped, meter at hard stop")
                return
            _event("run_start", run_id=run_id, model=label, task=task["id"], rep=rep)
            res = run_agent(adapter, meter, label, task, rep, DEMO / "taskrepo", WORK, OUT)
            hidden = next((DEMO / "tasks" / task["id"]).glob("test_hidden_*.py"))
            g = grade(WORK / run_id / "repo", hidden, DEMO / "taskrepo")
            row = asdict(res) | {"model_id": model_id, "adapter": kind, "grade": g,
                                 "pass": g["suite"]["exit"] == 0 and g["hidden"]["exit"] == 0,
                                 "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            with _lock:
                with RESULTS.open("a") as f:
                    f.write(json.dumps(row) + "\n")
            log(f"{run_id}: {'PASS' if row['pass'] else 'fail'} stop={res.stop} calls={res.calls} "
                f"fmt={len(res.format_failures)} api_err={len(res.api_errors)} ${res.usd:.3f} {res.wall_s:.0f}s "
                f"hidden {g['hidden']['passed']}/{g['hidden']['passed'] + g['hidden']['failed'] + g['hidden']['errors']} "
                f"| meter ${meter.spent:.2f}")
            _event("run_end", run_id=run_id, model=label, task=task["id"], rep=rep,
                   passed=row["pass"], stop=res.stop, calls=res.calls,
                   usd=round(res.usd, 6), wall_s=res.wall_s)
            if res.stop == "credentials_expired":
                log(f"{label}: credentials expired, stopping this worker")
                return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--tasks", default="")
    ap.add_argument("--reps", type=int, default=5)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((DEMO / "tasks" / "tasks.json").read_text())
    if a.tasks:
        keep = a.tasks.split(",")
        tasks = [t for t in tasks if t["id"] in keep or t["id"].split("-")[0] in keep]
    meter = Meter(OUT / "spend.jsonl")
    log_lock = threading.Lock()

    def log(msg: str) -> None:
        with log_lock:
            print(time.strftime("%H:%M:%S"), msg, flush=True)

    log(f"start: meter ${meter.spent:.2f} (hard stop ${HARD_STOP_USD})")
    labels = a.models.split(",")
    # manifest for the dashboard: every run this invocation plans, in lane order
    EXPECTED.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reps": a.reps, "models": labels, "tasks": [t["id"] for t in tasks],
        "runs": [{"run_id": f"{m}__{t['id']}__r{r}", "model": m, "task": t["id"], "rep": r}
                 for r in range(1, a.reps + 1) for m in labels for t in tasks]}, indent=1) + "\n")
    # one lane per (model, repetition): repetitions of a model run side by side, tasks in order within a lane
    lanes = [(m, [r]) for r in range(1, a.reps + 1) for m in labels]
    with ThreadPoolExecutor(len(lanes)) as ex:
        for fut in [ex.submit(worker, m, tasks, reps, meter, log) for m, reps in lanes]:
            fut.result()
    log(f"done: meter ${meter.spent:.2f}")


if __name__ == "__main__":
    main()
