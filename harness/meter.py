"""Running spend meter: measured tokens x AWS public list price, hard stop before the cap.

Prices are USD per 1M tokens, Amazon Bedrock Standard tier, on-demand, as listed on
https://aws.amazon.com/bedrock/pricing/ (snapshot: data-snapshot-2026-11-11/raw-aws-bedrock-pricing.html;
live route-price audit: 2026-11-13).
`option` is the routing the harness actually uses (Global or US geo cross-region profile).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

HARD_STOP_USD = 90.0   # no new model call once the meter reaches this
BUDGET_USD = 100.0     # the cap the hard stop protects


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    option: str = ""


# Actual billed-route rates. Opus Global CRIS is $4/$20/$0.20 cache read; the final
# replacement runs used US CRIS after repeated Global 503s, so their meter uses $4.40/$22/$0.22.
PRICES: dict[str, Price] = {
    "kimi-k3.1-pro": Price(4.00, 20.00, 0.20, 5.00, "Global CRIS"),
    "glm-5.3": Price(1.848, 5.808, 0.3432, 2.31, "US CRIS"),
    "claude-opus-5.5": Price(4.40, 22.00, 0.22, 5.50, "US CRIS"),
    "claude-fable-5-2": Price(8.00, 40.00, 0.20, 10.00, "Global CRIS"),
}


def call_cost(model: str, usage: dict) -> float:
    """Cost of one call from normalised usage: input (incl. cached), cached_read, cache_write, output."""
    p = PRICES[model]
    cached = usage.get("cache_read", 0) or 0
    written = usage.get("cache_write", 0) or 0
    fresh = max(0, (usage.get("input", 0) or 0) - cached - written)
    cr = p.cache_read if p.cache_read is not None else p.input
    cw = p.cache_write if p.cache_write is not None else p.input
    return (fresh * p.input + cached * cr + written * cw + (usage.get("output", 0) or 0) * p.output) / 1e6


class Meter:
    def __init__(self, ledger: Path):
        self.ledger = ledger
        self.lock = threading.Lock()
        self.spent = 0.0
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                if line.strip():
                    self.spent += json.loads(line)["usd"]

    def allow(self) -> bool:
        with self.lock:
            return self.spent < HARD_STOP_USD

    def record(self, run_id: str, model: str, usage: dict) -> float:
        usd = call_cost(model, usage)
        with self.lock:
            self.spent += usd
            with self.ledger.open("a") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "run": run_id, "model": model,
                                    "usage": usage, "usd": round(usd, 6),
                                    "total_usd": round(self.spent, 6)}) + "\n")
        return usd
