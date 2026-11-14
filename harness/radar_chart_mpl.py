"""Render the enhanced five-dimension radar chart with Matplotlib.

    uv run --python 3.11 --with numpy --with matplotlib \
      python harness/radar_chart_mpl.py

Scores are computed from out/results.jsonl and out-live/results.jsonl by
radar_chart.aggregate (never hardcoded). Produces high-resolution PNG charts in
English, Chinese and French. The original stdlib SVG generator remains available
as harness/radar_chart.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D

from radar_chart import DEMO, DIMENSIONS, MODELS, TEXT, aggregate, read_rows

GUIDE = {
    "en": {
        "breakdown": "SCORE BREAKDOWN",
        "line1": "C  Correctness     R  Requirements     I  Integration",
        "line2": "E  Efficiency        D  Delivery discipline",
        "read_title": "HOW TO READ THIS CHART",
        "read1": "Higher is better on every axis. The radar shows capability scores;",
        "read2": "finish count and median tool calls are shown separately as context.",
        "detail": "Median calls: {calls:g}  |  Finish: {finishes}/36",
        "efficiency": "Efficiency normalization: 50% call-budget economy + 50% natural-finish rate.",
    },
    "zh-CN": {
        "breakdown": "维度得分",
        "line1": "C  代码正确性   R  需求理解   I  跨模块集成",
        "line2": "E  工具调用效率   D  工程交付纪律",
        "read_title": "如何阅读此图",
        "read1": "所有轴均为越高越好。雷达图展示能力得分；",
        "read2": "自然结束次数与调用中位数单独列出，作为效率背景。",
        "detail": "调用中位数：{calls:g}  |  自然结束：{finishes}/36",
        "efficiency": "效率归一化：50% 调用预算经济性 + 50% 自然结束率。",
    },
    "fr": {
        "breakdown": "DÉTAIL DES SCORES",
        "line1": "C  Exactitude     R  Exigences     I  Intégration",
        "line2": "E  Efficacité      D  Discipline de livraison",
        "read_title": "COMMENT LIRE CE GRAPHIQUE",
        "read1": "Plus le score est élevé, mieux c'est sur chaque axe. Le radar montre les capacités ;",
        "read2": "les finish et les appels médians sont indiqués séparément comme contexte.",
        "detail": "Appels médians : {calls:g}  |  Finish : {finishes}/36",
        "efficiency": "Normalisation : 50 % économie d'appels + 50 % de finish naturels.",
    },
}

COLORS = {
    "kimi-k3.1-pro": "#159A62",
    "glm-5.3": "#E45756",
    "claude-opus-5.5": "#3977D5",
    "claude-fable-5-2": "#E39A16",
}
DRAW_ORDER = ["glm-5.3", "kimi-k3.1-pro", "claude-opus-5.5", "claude-fable-5-2"]
LEGEND_ORDER = ["claude-fable-5-2", "claude-opus-5.5", "kimi-k3.1-pro", "glm-5.3"]


def configure_fonts(language: str) -> None:
    """Use an installed CJK font for the Chinese chart; DejaVu otherwise."""
    plt.rcParams["axes.unicode_minus"] = False
    if language != "zh-CN":
        plt.rcParams["font.family"] = ["DejaVu Sans"]
        return
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            font_manager.fontManager.addfont(candidate)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=candidate).get_name()
            return
    plt.rcParams["font.family"] = ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimHei", "DejaVu Sans"]


def render(summary: dict[str, dict], language: str, output: Path) -> None:
    configure_fonts(language)
    t, guide = TEXT[language], GUIDE[language]
    dimensions = t["axes"]
    scores = {key: [summary[key]["radar_scores"][d] for d in DIMENSIONS] for key, _, _ in MODELS}

    n = len(dimensions)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    fig = plt.figure(figsize=(16, 10), facecolor="white")
    ax = fig.add_axes([0.08, 0.10, 0.53, 0.68], polar=True)
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 110)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dimensions, fontsize=12, fontweight="semibold", color="#253247")
    ax.tick_params(axis="x", pad=22)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=9, color="#68758A")
    ax.set_rlabel_position(0)
    ax.yaxis.grid(True, color="#D8DEE8", linewidth=0.9)
    ax.xaxis.grid(True, color="#D8DEE8", linewidth=0.9)
    ax.spines["polar"].set_color("#B9C3D0")
    ax.spines["polar"].set_linewidth(1.0)

    # Weaker profiles first, strongest last; light fills keep lines readable.
    for key in DRAW_ORDER:
        values = scores[key]
        closed = values + values[:1]
        color = COLORS[key]
        ax.plot(angles, closed, color=color, linewidth=2.7,
                marker="o", markersize=6, markeredgecolor="white", markeredgewidth=0.9,
                label=summary[key]["label"], zorder=4 if key == "claude-fable-5-2" else 3)
        ax.fill(angles, closed, color=color, alpha=0.055, zorder=2)

    # Label GLM's low/ambiguous vertices directly: bottom-right Integration
    # (66.7) must not be mistaken for bottom-left Tool efficiency (9.7).
    for index, offset in [(2, (10, -2)), (3, (0, -16)), (4, (-8, 8))]:
        ax.annotate(f"{scores['glm-5.3'][index]:.1f}",
                    (angles[index], scores["glm-5.3"][index]),
                    textcoords="offset points", xytext=offset,
                    fontsize=9, fontweight="bold", color=COLORS["glm-5.3"],
                    bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                              edgecolor=COLORS["glm-5.3"], linewidth=0.7, alpha=0.95),
                    zorder=5)

    fig.text(0.055, 0.955, t["title"], fontsize=23, fontweight="bold", color="#172235", ha="left")
    fig.text(0.055, 0.918, t["subtitle"], fontsize=12, color="#526176", ha="left")

    fig.text(0.665, 0.855, guide["breakdown"], fontsize=12, fontweight="bold", color="#253247")
    fig.text(0.665, 0.825, guide["line1"], fontsize=9.5, color="#59677B")
    fig.text(0.665, 0.802, guide["line2"], fontsize=9.5, color="#59677B")

    y = 0.755
    for key in LEGEND_ORDER:
        entry = summary[key]
        color = COLORS[key]
        fig.add_artist(Line2D([0.665, 0.695], [y + 0.006, y + 0.006],
                              transform=fig.transFigure, color=color, linewidth=4,
                              solid_capstyle="round"))
        fig.text(0.708, y, entry["label"], fontsize=12, fontweight="bold", color="#202B3B")
        scoreline = "  /  ".join(f"{v:.1f}" for v in scores[key])
        fig.text(0.665, y - 0.031, scoreline, fontsize=11, color="#33445B", family="DejaVu Sans Mono")
        detail = guide["detail"].format(calls=entry["median_calls"], finishes=entry["natural_finishes"])
        fig.text(0.665, y - 0.057, detail, fontsize=10, color="#59677B")
        y -= 0.145

    fig.text(0.665, 0.165, guide["read_title"], fontsize=11, fontweight="bold", color="#253247")
    fig.text(0.665, 0.132, guide["read1"], fontsize=9.5, color="#59677B")
    fig.text(0.665, 0.108, guide["read2"], fontsize=9.5, color="#59677B")

    fig.text(0.055, 0.052, t["footer1"], fontsize=9.5, color="#59677B")
    fig.text(0.055, 0.029, guide["efficiency"], fontsize=9.5, color="#59677B")
    fig.text(0.055, 0.006, t["footer2"].replace("harness/radar_chart.py", "harness/radar_chart_mpl.py"),
             fontsize=9.5, color="#59677B")

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--official", type=Path, default=DEMO / "out" / "results.jsonl")
    parser.add_argument("--live", type=Path, default=DEMO / "out-live" / "results.jsonl")
    parser.add_argument("--output", type=Path, default=DEMO / "docs" / "assets" / "five-dimensions-radar.png")
    args = parser.parse_args()

    summary = aggregate(read_rows([args.official, args.live]))
    for language in ("en", "zh-CN", "fr"):
        output = args.output if language == "en" else args.output.with_name(
            f"{args.output.stem}.{language}{args.output.suffix}")
        render(summary, language, output)
        print(f"wrote {output}")


if __name__ == "__main__":
    main()


