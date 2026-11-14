"""Generate the five-dimension radar chart from the two measured matrices.

    python3 harness/radar_chart.py

Stdlib-only. Reads out/results.jsonl and out-live/results.jsonl, then writes
  docs/assets/five-dimensions-radar.svg   (standalone vector chart)
  docs/assets/five-dimensions-radar.json  (raw and normalized scores)

Radar normalization (0–100):
  correctness  = hidden tests passed / hidden tests executed
  requirements = runs covering all required APIs and exact contracts / 36
  integration  = successful integrated runs on t2/t3/t4/t6 / 24
  efficiency   = 50% * (1 - median_calls/30) + 50% * natural_finish_rate
  discipline   = clean-delivery runs / 36
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics as st
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

DEMO = Path(__file__).resolve().parent.parent
MODELS = [
    ("kimi-k3.1-pro", "Kimi K3.1 Pro", "#2ea043"),
    ("glm-5.3", "GLM 5.3", "#f85149"),
    ("claude-opus-5.5", "Claude Opus 5.5", "#388bfd"),
    ("claude-fable-5-2", "Claude Fable 5.2", "#d29922"),
]
TASKS = ["t1-eligibility", "t2-privacy", "t3-audit", "t4-scheduling", "t5-safety", "t6-governance"]
INTEGRATION = {"t2-privacy", "t3-audit", "t4-scheduling", "t6-governance"}
DIMENSIONS = ["correctness", "requirements", "integration", "efficiency", "discipline"]


def read_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def classify(run: dict) -> tuple[bool, bool, list[str], int, int]:
    """Return requirement failure, integration failure, discipline issues, hidden passes, hidden executed."""
    tail = run["grade"]["hidden"]["tail"]
    hidden = run["grade"]["hidden"]
    passed, failed = hidden["passed"], hidden["failed"]
    missing = re.search(r"cannot import name '([^']+)'", tail)
    ineligible = "INELIGIBLE" in tail and "test_ineligibility" in tail
    invalid_chain = "INVALID_CHAIN" in tail
    requirement_failure = bool(missing) or ineligible or invalid_chain

    task = run["task"]
    integration_failure = False
    if task == "t2-privacy" and missing and "execute_reuse_export" in missing.group(1):
        integration_failure = True
    elif task == "t3-audit" and (invalid_chain or "from_records" in tail):
        integration_failure = True
    elif task == "t4-scheduling":
        if (missing and "book_subject_visits" in missing.group(1)) or ineligible:
            integration_failure = True
        elif re.search(r"test_(booking|multiple_visits|ineligibility)", tail):
            integration_failure = True
    elif task == "t6-governance" and missing:
        integration_failure = True

    issues = []
    if "unauthorized file changes" in tail:
        issues.append("unauthorized-file")
    if run["stop"] != "finish":
        issues.append(f"stop:{run['stop']}")
    if run["format_failures"]:
        issues.append("format")
    if run["api_errors"]:
        issues.append("api")
    return requirement_failure, integration_failure, issues, passed, passed + failed


def aggregate(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for key, label, color in MODELS:
        runs = [r for r in rows if r["model"] == key]
        if len(runs) != 36:
            raise ValueError(f"expected 36 pooled runs for {key}, found {len(runs)}")
        hidden_passed = hidden_executed = requirements = integration = clean = 0
        for run in runs:
            req_fail, int_fail, issues, hp, hn = classify(run)
            hidden_passed += hp
            hidden_executed += hn
            requirements += not req_fail
            if run["task"] in INTEGRATION:
                integration += not int_fail
            clean += not issues
        median_calls = st.median(r["calls"] for r in runs)
        finishes = sum(r["stop"] == "finish" for r in runs)
        scores = {
            "correctness": 100 * hidden_passed / hidden_executed,
            "requirements": 100 * requirements / 36,
            "integration": 100 * integration / 24,
            "efficiency": 50 * (1 - median_calls / 30) + 50 * finishes / 36,
            "discipline": 100 * clean / 36,
        }
        result[key] = {
            "label": label, "color": color, "runs": len(runs),
            "median_calls": median_calls, "natural_finishes": finishes,
            "hidden_passed": hidden_passed, "hidden_executed": hidden_executed,
            "requirement_runs": requirements, "integration_runs": integration,
            "clean_runs": clean, "radar_scores": {d: round(scores[d], 1) for d in DIMENSIONS},
        }
    return result


def esc(text: object) -> str:
    import html
    return html.escape(str(text), quote=True)


def polar(cx: float, cy: float, radius: float, index: int) -> tuple[float, float]:
    angle = math.radians(-90 + index * 72)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def points_for(scores: list[float], cx: float, cy: float, radius: float) -> str:
    points = [polar(cx, cy, radius * score / 100, i) for i, score in enumerate(scores)]
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def render_svg(summary: dict[str, dict]) -> str:
    width, height = 1200, 760
    cx, cy, radius = 415, 410, 225
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="chart-title chart-desc">',
        '<title id="chart-title">Five-dimension model comparison · 五维模型对比</title>',
        '<desc id="chart-desc">Radar chart comparing Kimi K3.1 Pro, GLM 5.3, Claude Opus 5.5 '
        'and Claude Fable 5.2 on correctness, requirements, integration, efficiency and discipline '
        'across 144 measured runs.</desc>',
        '<rect width="1200" height="760" fill="#ffffff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"PingFang SC",'
        '"Microsoft YaHei",sans-serif;fill:#17212b}.muted{fill:#52606d}.small{font-size:12px}'
        '.axis-label{font-size:14px;font-weight:650}.axis-cn{font-size:12px;fill:#52606d}'
        '.legend-name{font-size:14px;font-weight:650}.legend-score{font-size:12px;fill:#52606d}'
        '</style>',
        '<text x="42" y="48" font-size="24" font-weight="700">Five-dimension model comparison · 五维模型对比</text>',
        '<text x="42" y="75" class="muted" font-size="14">144 runs (72 official + 72 live) · normalized scores 0–100</text>',
    ]

    # Grid rings and numeric scale.
    for level in (20, 40, 60, 80, 100):
        r = radius * level / 100
        out.append(f'<polygon points="{points_for([level] * 5, cx, cy, radius)}" fill="none" '
                   f'stroke="#{"c7d0d9" if level < 100 else "8e9aa7"}" stroke-width="1"/>')
        out.append(f'<text x="{cx + 6}" y="{cy - r + 4}" class="muted small">{level}</text>')
    labels = [
        ("Correctness", "代码正确性"),
        ("Requirements", "需求理解"),
        ("Integration", "跨模块集成"),
        ("Tool efficiency", "工具调用效率"),
        ("Delivery discipline", "工程交付纪律"),
    ]
    for i, (english, chinese) in enumerate(labels):
        x, y = polar(cx, cy, radius, i)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.2f}" y2="{y:.2f}" stroke="#c7d0d9"/>')
        lx, ly = polar(cx, cy, radius + 42, i)
        anchor = "middle"
        if i == 1:
            anchor = "start"
        elif i in (3, 4):
            anchor = "end"
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" class="axis-label">{esc(english)}</text>')
        out.append(f'<text x="{lx:.1f}" y="{ly + 18:.1f}" text-anchor="{anchor}" class="axis-cn">{esc(chinese)}</text>')

    # Data polygons and vertices.
    for key, _, _ in MODELS:
        entry = summary[key]
        scores = [entry["radar_scores"][d] for d in DIMENSIONS]
        points = points_for(scores, cx, cy, radius)
        color = entry["color"]
        out.append(f'<polygon points="{points}" fill="{color}" fill-opacity="0.13" '
                   f'stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>')
        for i, score in enumerate(scores):
            x, y = polar(cx, cy, radius * score / 100, i)
            out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}" stroke="#ffffff" stroke-width="1.5"/>')

    # Legend with every plotted score visible in text form.
    out += [
        '<text x="770" y="140" font-size="15" font-weight="700">Scores · C / R / I / E / D</text>',
        '<text x="770" y="162" class="muted small">correctness / requirements / integration / efficiency / discipline</text>',
    ]
    y = 205
    for key, _, _ in MODELS:
        entry = summary[key]
        values = " / ".join(f"{entry['radar_scores'][d]:.1f}" for d in DIMENSIONS)
        out.append(f'<line x1="770" y1="{y - 5}" x2="800" y2="{y - 5}" stroke="{entry["color"]}" stroke-width="4"/>')
        out.append(f'<text x="812" y="{y}" class="legend-name">{esc(entry["label"])}</text>')
        out.append(f'<text x="770" y="{y + 23}" class="legend-score">{values}</text>')
        out.append(f'<text x="770" y="{y + 42}" class="legend-score">{entry["median_calls"]:.1f} median calls · '
                   f'{entry["natural_finishes"]}/36 finish</text>')
        y += 82

    out += [
        '<line x1="770" y1="525" x2="1150" y2="525" stroke="#d5dce4"/>',
        '<text x="770" y="553" font-size="13" font-weight="650">Efficiency normalization</text>',
        '<text x="770" y="577" class="muted small">50% call-budget economy + 50% natural-finish rate</text>',
        '<text x="770" y="597" class="muted small">call-budget economy = (30 − median calls) / 30</text>',
        '<text x="770" y="617" class="muted small">natural-finish rate = finish runs / 36</text>',
        '<text x="42" y="704" class="muted small">Correctness excludes suites that never executed. Integration covers t2/t3/t4/t6.</text>',
        '<text x="42" y="727" class="muted small">Source: out/results.jsonl + out-live/results.jsonl · generated by harness/radar_chart.py</text>',
        '</svg>',
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--official", type=Path, default=DEMO / "out" / "results.jsonl")
    parser.add_argument("--live", type=Path, default=DEMO / "out-live" / "results.jsonl")
    parser.add_argument("--svg", type=Path, default=DEMO / "docs" / "assets" / "five-dimensions-radar.svg")
    parser.add_argument("--json", type=Path, default=DEMO / "docs" / "assets" / "five-dimensions-radar.json")
    args = parser.parse_args()

    rows = read_rows([args.official, args.live])
    if len(rows) != 144:
        raise ValueError(f"expected 144 pooled runs, found {len(rows)}")
    summary = aggregate(rows)

    def repo_path(path: Path) -> str:
        try:
            return path.resolve().relative_to(DEMO.resolve()).as_posix()
        except ValueError:
            return str(path)

    payload = {
        "sessions": [repo_path(args.official), repo_path(args.live)],
        "runs": len(rows),
        "dimensions": DIMENSIONS,
        "normalization": {
            "correctness": "100 * hidden_passed / hidden_executed",
            "requirements": "100 * requirement_runs / 36",
            "integration": "100 * integration_runs / 24 (t2/t3/t4/t6 only)",
            "efficiency": "50 * (1 - median_calls/30) + 50 * natural_finishes/36",
            "discipline": "100 * clean_runs / 36",
        },
        "models": summary,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    svg = render_svg(summary)
    ET.fromstring(svg)  # fail fast if the generated chart is not valid XML
    args.svg.parent.mkdir(parents=True, exist_ok=True)
    args.svg.write_text(svg)
    for key, _, _ in MODELS:
        entry = summary[key]
        values = ", ".join(f"{d}={entry['radar_scores'][d]:.1f}" for d in DIMENSIONS)
        print(f"{entry['label']}: {values}")
    print(f"wrote {args.svg}")
    print(f"wrote {args.json}")


if __name__ == "__main__":
    main()


