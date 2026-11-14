"""Realtime status dashboard for the benchmark matrix.

    python harness/dashboard.py --port 8788          # then open http://127.0.0.1:8788

Stdlib-only and strictly read-only. On every browser poll it rebuilds state from the
files the runner writes inside --out (any P117_OUT directory works):

  expected.json   run manifest written by run_matrix.py at startup
                  (fallback when absent: --models x tasks x --reps)
  events.jsonl    run_start / run_end / run_skip lifecycle events
  results.jsonl   completed runs; authoritative for pass/fail and grading detail
  spend.jsonl     one event per model call; live spend and in-flight call counts

Directories recorded before the dashboard existed (no events.jsonl) still render:
in-flight runs are then inferred from spend rows without a matching results row.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
sys.path.insert(0, str(HERE))

from meter import BUDGET_USD, HARD_STOP_USD  # noqa: E402

DEFAULT_MODELS = ["kimi-k3.1-pro", "glm-5.3", "claude-opus-5.5", "claude-fable-5-2"]
OPEN_WEIGHTS = {"kimi-k3.1-pro", "glm-5.3"}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # concurrent append may leave a partial tail line; next poll picks it up
    return rows


def _epoch(iso_z: str) -> float:
    try:
        return datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _display_out(out: Path) -> str:
    """Show repository-relative output paths when possible (never leak a home directory)."""
    try:
        return out.resolve().relative_to(DEMO.resolve()).as_posix() or "."
    except ValueError:
        return str(out)


def build_state(out: Path, models: list[str], tasks: list[str], reps: int) -> dict:
    now = time.time()

    expected = None
    manifest = out / "expected.json"
    if manifest.exists():
        try:
            expected = json.loads(manifest.read_text()).get("runs")
        except json.JSONDecodeError:
            expected = None
    if not expected:
        expected = [{"run_id": f"{m}__{t}__r{r}", "model": m, "task": t, "rep": r}
                    for r in range(1, reps + 1) for m in models for t in tasks]

    results = {r["run_id"]: r for r in _read_jsonl(out / "results.jsonl")}
    events = _read_jsonl(out / "events.jsonl")
    spend = _read_jsonl(out / "spend.jsonl")

    started = {e["run_id"]: e.get("t", 0.0) for e in events
               if e.get("kind") == "run_start" and "run_id" in e}
    ended = {e["run_id"] for e in events if e.get("kind") in ("run_end", "run_skip")}

    per_run: dict[str, dict] = defaultdict(lambda: {"calls": 0, "usd": 0.0, "last_t": 0.0})
    per_model_spend: dict[str, float] = defaultdict(float)
    total_spend = 0.0
    for s in spend:
        agg = per_run[s["run"]]
        agg["calls"] += 1
        agg["usd"] += s["usd"]
        agg["last_t"] = max(agg["last_t"], s.get("t", 0.0))
        per_model_spend[s["model"]] += s["usd"]
        total_spend += s["usd"]

    # in flight: started (or spent) without a result row and without an end event
    running: dict[str, float] = {}
    for run_id, t0 in started.items():
        if run_id not in results and run_id not in ended:
            running[run_id] = t0
    for run_id, agg in per_run.items():
        if run_id not in results and run_id not in ended:
            running.setdefault(run_id, agg["last_t"])

    exp_models = list(dict.fromkeys(e["model"] for e in expected))
    columns = [m for m in models if m in exp_models] + [m for m in exp_models if m not in models]
    exp_tasks = list(dict.fromkeys(e["task"] for e in expected))
    rows_order = [t for t in tasks if t in exp_tasks] + [t for t in exp_tasks if t not in tasks]

    grid: dict[str, dict[str, list]] = {t: {m: [] for m in columns} for t in rows_order}
    for e in expected:
        m, t, rid = e["model"], e["task"], e["run_id"]
        if t not in grid or m not in grid[t]:
            continue
        cell: dict = {"rep": e["rep"], "run_id": rid}
        if rid in results:
            r = results[rid]
            hidden = r.get("grade", {}).get("hidden", {})
            cell.update(status="pass" if r.get("pass") else "fail", passed=bool(r.get("pass")),
                        stop=r.get("stop", ""), calls=r.get("calls", 0), wall_s=r.get("wall_s", 0.0),
                        usd=r.get("usd", 0.0), hidden_passed=hidden.get("passed", 0),
                        hidden_failed=hidden.get("failed", 0), hidden_errors=hidden.get("errors", 0),
                        finished_at=r.get("finished_at", ""))
        elif rid in running:
            agg = per_run.get(rid, {"calls": 0, "usd": 0.0, "last_t": 0.0})
            cell.update(status="running", started=running[rid], last_t=agg["last_t"],
                        calls=agg["calls"], usd=round(agg["usd"], 6),
                        elapsed_s=round(now - running[rid], 1) if running[rid] else None)
        else:
            cell.update(status="pending")
        grid[t][m].append(cell)
    for t in grid:
        for m in grid[t]:
            grid[t][m].sort(key=lambda c: c["rep"])


    model_stats = {}
    for m in columns:
        rs = [r for r in results.values() if r.get("model") == m]
        passed = [r for r in rs if r.get("pass")]
        usage: dict[str, int] = defaultdict(int)
        for r in rs:
            for k, v in (r.get("usage") or {}).items():
                usage[k] += v or 0
        usd_finished = sum(r.get("usd", 0.0) for r in rs)
        model_stats[m] = {
            "open_weights": m in OPEN_WEIGHTS,
            "done": len(rs), "passed": len(passed),
            "pass_rate": round(len(passed) / len(rs), 4) if rs else None,
            "running": sum(1 for rid in running if rid.startswith(m + "__")),
            "cost_finished": round(usd_finished, 4),
            "cost_ledger": round(per_model_spend.get(m, 0.0), 4),
            "usd_per_pass": round(usd_finished / len(passed), 4) if passed else None,
            "calls_median": st.median(r["calls"] for r in rs) if rs else None,
            "wall_median": round(st.median(r["wall_s"] for r in rs), 1) if rs else None,
            "stops": dict(Counter(r.get("stop", "") for r in rs)),
            "format_failures": sum(len(r.get("format_failures", [])) for r in rs),
            "api_errors": sum(len(r.get("api_errors", [])) for r in rs),
            "no_tool_turns": sum(r.get("no_tool_turns", 0) for r in rs),
            "tokens": dict(usage),
        }

    running_list = []
    for rid, t0 in running.items():
        agg = per_run.get(rid, {"calls": 0, "usd": 0.0, "last_t": 0.0})
        parts = rid.split("__")
        running_list.append({"run_id": rid, "model": parts[0],
                             "task": parts[1] if len(parts) > 1 else "",
                             "elapsed_s": round(now - t0, 1) if t0 else None,
                             "calls": agg["calls"], "usd": round(agg["usd"], 6),
                             "idle_s": round(now - agg["last_t"], 1) if agg["last_t"] else None})
    running_list.sort(key=lambda x: x["run_id"])

    feed = []
    for e in events:
        if e.get("kind") in ("run_start", "run_end", "run_skip"):
            feed.append({"t": e.get("t", 0.0), "kind": e["kind"], "run_id": e.get("run_id", ""),
                         "passed": e.get("passed"), "stop": e.get("stop", e.get("reason", "")),
                         "calls": e.get("calls"), "usd": e.get("usd"), "wall_s": e.get("wall_s")})
    ended_in_events = {e.get("run_id") for e in events if e.get("kind") == "run_end"}
    for rid, r in results.items():
        if rid not in ended_in_events:  # pre-dashboard results: synthesize their end events
            feed.append({"t": _epoch(r.get("finished_at", "")), "kind": "run_end", "run_id": rid,
                         "passed": r.get("pass"), "stop": r.get("stop", ""), "calls": r.get("calls"),
                         "usd": round(r.get("usd", 0.0), 6), "wall_s": r.get("wall_s")})
    feed.sort(key=lambda x: x["t"], reverse=True)
    feed = feed[:15]

    done = sum(1 for e in expected if e["run_id"] in results)
    passed_n = sum(1 for e in expected if e["run_id"] in results and results[e["run_id"]].get("pass"))
    total = len(expected)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "out_dir": _display_out(out),
        "totals": {"expected": total, "done": done, "passed": passed_n, "failed": done - passed_n,
                   "running": len(running_list), "pending": max(0, total - done - len(running_list)),
                   "progress": round(done / total, 4) if total else 0},
        "spend": {"total": round(total_spend, 4), "hard_stop": HARD_STOP_USD, "budget": BUDGET_USD,
                  "by_model": {m: round(v, 4) for m, v in sorted(per_model_spend.items())}},
        "columns": columns, "rows": rows_order,
        "models": model_stats, "grid": grid,
        "running_runs": running_list, "feed": feed,
    }


_PAGE_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>trialops benchmark · live matrix</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#0b0f14; --panel:#121a23; --line:#22303f; --text:#d7e0ea; --muted:#7d8fa3;
          --green:#3fb950; --red:#f85149; --blue:#58a6ff; --gray:#3a4653; --amber:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.45 -apple-system,"SF Pro Text",Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { display:flex; align-items:baseline; gap:16px; padding:18px 24px 10px; flex-wrap:wrap; }
  h1 { font-size:18px; margin:0; font-weight:650; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:18px 0 8px; }
  #meta { color:var(--muted); font-size:12px; }
  .topline { display:flex; gap:12px; padding:6px 24px 0; flex-wrap:wrap; }
  .stat { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 16px; min-width:110px; }
  .stat .num { font-size:20px; font-weight:650; font-variant-numeric:tabular-nums; }
  .stat .lbl { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .num.ok { color:var(--green); } .num.bad { color:var(--red); } .num.live { color:var(--blue); }
  .bars { padding:12px 24px 0; display:grid; gap:8px; }
  .bar { height:8px; background:#1a2431; border-radius:99px; overflow:hidden; position:relative; }
  .bar > div { height:100%; border-radius:99px; transition:width .6s ease; }
  #progressfill { background:var(--blue); } #spendfill { background:var(--amber); }
  .barlbl { font-size:11px; color:var(--muted); display:flex; justify-content:space-between; }
  #cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; padding:16px 24px 0; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  .cardhead { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .cardhead .name { font-weight:650; font-size:13px; }
  .badge { font-size:10px; padding:2px 8px; border-radius:99px; border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
  .badge.open { color:var(--green); border-color:#1e4d2b; }
  .big { display:flex; align-items:baseline; gap:10px; margin:8px 0 10px; }
  .bignum { font-size:26px; font-weight:700; font-variant-numeric:tabular-nums; }
  .bignum.ok { color:var(--green); } .bignum.bad { color:var(--red); }
  .bigsub { color:var(--muted); font-size:13px; }
  .kv { display:grid; grid-template-columns:auto 1fr; gap:2px 10px; font-size:12px; }
  .kv .k { color:var(--muted); } .kv .v { text-align:right; font-variant-numeric:tabular-nums; }
  .wrap { display:grid; grid-template-columns:minmax(0,1fr) 340px; gap:16px; padding:0 24px 24px; align-items:start; }
  @media (max-width:1100px) { .wrap { grid-template-columns:1fr; } }
  table { border-collapse:collapse; width:100%; background:var(--panel); border:1px solid var(--line); border-radius:12px; }
  th, td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:center; }
  th { font-size:11px; color:var(--muted); font-weight:600; }
  th.taskcol { text-align:left; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
  tr:last-child th, tr:last-child td { border-bottom:none; }
  .dot { display:inline-block; width:14px; height:14px; border-radius:50%; margin:0 3px; vertical-align:middle; }
  .dot.pass { background:var(--green); } .dot.fail { background:var(--red); }
  .dot.pending { background:var(--gray); opacity:.55; }
  .dot.running { background:var(--blue); animation:pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity:.35; transform:scale(.85);} 50% { opacity:1; transform:scale(1.1);} }
  aside { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:4px 16px 14px; }
  .runrow, .feedrow { display:flex; gap:8px; align-items:baseline; padding:5px 0; border-bottom:1px solid #1a2431; font-size:12px; flex-wrap:wrap; }
  .runrow:last-child, .feedrow:last-child { border-bottom:none; }
  .rid { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; }
  .ftime { color:var(--muted); font-variant-numeric:tabular-nums; }
  .ftag { font-size:10px; font-weight:700; padding:1px 7px; border-radius:99px; min-width:38px; text-align:center; }
  .ftag.ok { background:#12361d; color:var(--green); } .ftag.bad { background:#3d1512; color:var(--red); }
  .ftag.info { background:#12283f; color:var(--blue); }
  .muted { color:var(--muted); }
</style></head>
"""

_PAGE_BODY = """<body>
<header><h1>trialops benchmark &middot; live matrix</h1><div id="meta">connecting&hellip;</div></header>
<section class="topline">
  <div class="stat"><div class="num" id="s-progress">&ndash;</div><div class="lbl">runs done</div></div>
  <div class="stat"><div class="num ok" id="s-pass">&ndash;</div><div class="lbl">passed</div></div>
  <div class="stat"><div class="num bad" id="s-fail">&ndash;</div><div class="lbl">failed</div></div>
  <div class="stat"><div class="num live" id="s-running">&ndash;</div><div class="lbl">running</div></div>
  <div class="stat"><div class="num" id="s-pending">&ndash;</div><div class="lbl">pending</div></div>
  <div class="stat"><div class="num" id="s-spend">&ndash;</div><div class="lbl">spend (list price)</div></div>
</section>
<section class="bars">
  <div><div class="bar"><div id="progressfill" style="width:0%"></div></div></div>
  <div><div class="bar"><div id="spendfill" style="width:0%"></div></div>
       <div class="barlbl"><span>budget meter</span><span id="s-spendcap"></span></div></div>
</section>
<section id="cards"></section>
<div class="wrap">
  <div><h2>matrix &middot; one dot per repetition</h2><table id="matrix"></table></div>
  <aside>
    <h2>running now</h2><div id="running"></div>
    <h2>latest events</h2><div id="feed"></div>
  </aside>
</div>
<script>
"""

_PAGE_JS = """
const POLL = 2000;
function el(tag, cls, text){ const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e; }
function setText(id,x){ document.getElementById(id).textContent=x; }
function fmtUsd(x){ if(x==null) return "\\u2013"; const n=Number(x); return "$"+(n<1&&n>0?n.toFixed(3):n.toFixed(2)); }
function addkv(p,k,v){ p.append(el('span','k',k)); p.append(el('span','v',String(v))); }

async function tick(){
  try { const r=await fetch('/api/state',{cache:'no-store'}); render(await r.json()); }
  catch(e) { /* keep the last frame on transient errors */ }
  finally { setTimeout(tick, POLL); }
}

function dotTitle(c){
  if(c.status==='pending') return c.run_id+' \\u00b7 pending';
  if(c.status==='running') return c.run_id+' \\u00b7 running \\u00b7 '+c.calls+' calls \\u00b7 '+fmtUsd(c.usd);
  const hp=c.hidden_passed||0, hf=c.hidden_failed||0, he=c.hidden_errors||0;
  return c.run_id+' \\u00b7 '+(c.status==='pass'?'PASS':'FAIL')+' \\u00b7 stop='+c.stop+' \\u00b7 '+c.calls+' calls \\u00b7 '+Math.round(c.wall_s)+'s \\u00b7 '+fmtUsd(c.usd)+' \\u00b7 hidden '+hp+'/'+(hp+hf+he);
}

function render(s){
  document.getElementById('meta').textContent = s.out_dir+' \\u00b7 updated '+s.generated_at;
  const t=s.totals;
  setText('s-progress', t.done+'/'+t.expected);
  setText('s-pass', t.passed); setText('s-fail', t.failed);
  setText('s-running', t.running); setText('s-pending', t.pending);
  document.getElementById('progressfill').style.width=(100*t.progress)+'%';
  setText('s-spend', fmtUsd(s.spend.total));
  setText('s-spendcap', 'hard stop '+fmtUsd(s.spend.hard_stop)+' \\u00b7 cap '+fmtUsd(s.spend.budget));
  document.getElementById('spendfill').style.width=Math.min(100,100*s.spend.total/s.spend.hard_stop)+'%';

  const cards=document.getElementById('cards'); cards.replaceChildren();
  for(const m of s.columns){
    const v=s.models[m]; if(!v) continue;
    const card=el('div','card');
    const head=el('div','cardhead');
    head.append(el('span','name',m));
    head.append(el('span','badge '+(v.open_weights?'open':''), v.open_weights?'open weights':'proprietary'));
    card.append(head);
    const big=el('div','big');
    const cls = v.done>0 ? (v.passed===v.done?'ok':'bad') : '';
    big.append(el('span','bignum '+cls,(v.passed==null?0:v.passed)+'/'+v.done));
    big.append(el('span','bigsub', v.pass_rate==null?'\\u2013':Math.round(100*v.pass_rate)+'% pass'));
    card.append(big);
    const kv=el('div','kv');
    addkv(kv,'live runs', v.running>0?v.running:'0');
    addkv(kv,'cost (ledger)', fmtUsd(v.cost_ledger));
    addkv(kv,'$/pass', v.usd_per_pass==null?'\\u2013':fmtUsd(v.usd_per_pass));
    addkv(kv,'median calls', v.calls_median==null?'\\u2013':v.calls_median);
    addkv(kv,'median wall', v.wall_median==null?'\\u2013':v.wall_median+'s');
    addkv(kv,'api errors', v.api_errors);
    addkv(kv,'no-tool turns', v.no_tool_turns);
    card.append(kv);
    cards.append(card);
  }

  const tbl=document.getElementById('matrix'); tbl.replaceChildren();
  const trh=el('tr'); trh.append(el('th','taskcol','task'));
  for(const m of s.columns) trh.append(el('th','',m));
  tbl.append(trh);
  for(const task of s.rows){
    const tr=el('tr'); tr.append(el('th','taskcol',task));
    for(const m of s.columns){
      const td=el('td');
      const cells=(s.grid[task]&&s.grid[task][m])||[];
      for(const c of cells){
        const d=el('span','dot '+c.status);
        d.setAttribute('title', dotTitle(c));
        td.append(d);
      }
      tr.append(td);
    }
    tbl.append(tr);
  }

  const run=document.getElementById('running'); run.replaceChildren();
  if(!s.running_runs.length) run.append(el('div','muted','nothing in flight'));
  for(const r of s.running_runs){
    const row=el('div','runrow');
    row.append(el('span','rid', r.run_id));
    const bits=[r.calls+' calls', fmtUsd(r.usd)];
    if(r.elapsed_s!=null) bits.push(Math.round(r.elapsed_s)+'s elapsed');
    if(r.idle_s!=null && r.idle_s>5) bits.push('idle '+Math.round(r.idle_s)+'s');
    row.append(el('span','muted', bits.join(' \u00b7 ')));
    run.append(row);
  }

  const feed=document.getElementById('feed'); feed.replaceChildren();
  for(const f of s.feed){
    const row=el('div','feedrow');
    row.append(el('span','ftime', f.t?new Date(f.t*1000).toTimeString().slice(0,8):'\u2013'));
    const label = f.kind==='run_end' ? (f.passed?'PASS':'FAIL') : f.kind;
    row.append(el('span','ftag '+(f.kind==='run_end'?(f.passed?'ok':'bad'):'info'), label));
    row.append(el('span','rid', f.run_id));
    const det=[]; if(f.stop)det.push(f.stop); if(f.calls!=null)det.push(f.calls+' calls');
    if(f.usd!=null)det.push(fmtUsd(f.usd)); if(f.wall_s!=null)det.push(Math.round(f.wall_s)+'s');
    row.append(el('span','muted', det.join(' \u00b7 ')));
    feed.append(row);
  }
}
tick();
"""

_PAGE_TAIL = """</script>
</body></html>
"""

PAGE = _PAGE_HEAD + _PAGE_BODY + _PAGE_JS + _PAGE_TAIL


class Handler(BaseHTTPRequestHandler):
    server_version = "p117-dashboard/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/api/state"):
            cfg = self.server.cfg
            state = build_state(cfg["out"], cfg["models"], cfg["tasks"], cfg["reps"])
            self._send(200, json.dumps(state).encode(), "application/json")
        elif self.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # keep stdout clean for the runner's own logs
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Realtime status dashboard for the benchmark matrix.")
    ap.add_argument("--out", default=str(DEMO / "out"),
                    help="matrix output directory (same value as the runner's P117_OUT)")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="fallback model list, used only when expected.json is absent")
    ap.add_argument("--reps", type=int, default=3,
                    help="fallback repetitions, used only when expected.json is absent")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    a = ap.parse_args()

    tasks = [t["id"] for t in json.loads((DEMO / "tasks" / "tasks.json").read_text())]
    httpd = ThreadingHTTPServer((a.host, a.port), Handler)
    httpd.daemon_threads = True
    httpd.cfg = {"out": Path(a.out).resolve(), "models": a.models.split(","), "tasks": tasks,
                 "reps": a.reps}
    print(f"dashboard: http://{a.host}:{a.port}  (out={httpd.cfg['out']})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
