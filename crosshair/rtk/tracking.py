"""Lightweight local tracking for filter savings.

Each filter run appends one NDJSON line to
``~/.cursor/crosshair/logs/rtk.ndjson``. ``crosshair rtk gain`` and
``crosshair analyze`` read this file.

Kept independent from ``crosshair.logs.EventLogger`` so rtk continues to work
even if the broader logger config is broken.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from crosshair.util import ensure_dir, expand, now_iso


RTK_LOG_PATH = expand("~/.cursor/crosshair/logs/rtk.ndjson")


@dataclass
class RtkEvent:
    ts: str
    filter: str
    cmd: str
    category: str
    exit_code: int
    original_chars: int
    filtered_chars: int
    elapsed_ms: int
    passthrough: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "filter": self.filter,
            "cmd": self.cmd,
            "category": self.category,
            "exit_code": self.exit_code,
            "original_chars": self.original_chars,
            "filtered_chars": self.filtered_chars,
            "elapsed_ms": self.elapsed_ms,
            "passthrough": self.passthrough,
        }


def record_event(event: RtkEvent, log_path: Path | None = None) -> None:
    """Append one event to the ndjson log; swallow any IO error."""
    path = log_path or RTK_LOG_PATH
    try:
        ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")
    except OSError:
        # Tracking is best-effort: never fail a command because we couldn't
        # write a log line.
        pass


def iter_events(log_path: Path | None = None) -> Iterable[RtkEvent]:
    """Read and yield all events; malformed lines are skipped."""
    path = log_path or RTK_LOG_PATH
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                yield RtkEvent(
                    ts=data.get("ts", ""),
                    filter=data.get("filter", ""),
                    cmd=data.get("cmd", ""),
                    category=data.get("category", ""),
                    exit_code=int(data.get("exit_code", 0)),
                    original_chars=int(data.get("original_chars", 0)),
                    filtered_chars=int(data.get("filtered_chars", 0)),
                    elapsed_ms=int(data.get("elapsed_ms", 0)),
                    passthrough=bool(data.get("passthrough", False)),
                )
            except (TypeError, ValueError):
                continue


def summarise(events: Iterable[RtkEvent]) -> dict[str, object]:
    """Return a totals + per-filter breakdown dict for ``rtk gain``."""
    totals = {
        "runs": 0,
        "original_chars": 0,
        "filtered_chars": 0,
        "original_tokens": 0,
        "filtered_tokens": 0,
        "saved_tokens": 0,
        "passthrough_runs": 0,
    }
    by_filter: dict[str, dict[str, int]] = defaultdict(
        lambda: {"runs": 0, "original_chars": 0, "filtered_chars": 0}
    )
    for ev in events:
        totals["runs"] += 1
        totals["original_chars"] += ev.original_chars
        totals["filtered_chars"] += ev.filtered_chars
        if ev.passthrough:
            totals["passthrough_runs"] += 1
        bucket = by_filter[ev.filter or "unknown"]
        bucket["runs"] += 1
        bucket["original_chars"] += ev.original_chars
        bucket["filtered_chars"] += ev.filtered_chars

    totals["original_tokens"] = _tokens(totals["original_chars"])
    totals["filtered_tokens"] = _tokens(totals["filtered_chars"])
    totals["saved_tokens"] = max(0, totals["original_tokens"] - totals["filtered_tokens"])
    totals["savings_pct"] = (
        round(100 * (1 - totals["filtered_chars"] / totals["original_chars"]), 1)
        if totals["original_chars"]
        else 0.0
    )

    breakdown = []
    for name, bucket in sorted(by_filter.items(), key=lambda kv: -kv[1]["original_chars"]):
        saved_chars = max(0, bucket["original_chars"] - bucket["filtered_chars"])
        breakdown.append(
            {
                "filter": name,
                "runs": bucket["runs"],
                "saved_chars": saved_chars,
                "saved_tokens": _tokens(saved_chars),
                "savings_pct": (
                    round(100 * (1 - bucket["filtered_chars"] / bucket["original_chars"]), 1)
                    if bucket["original_chars"]
                    else 0.0
                ),
            }
        )

    return {"totals": totals, "by_filter": breakdown}


def _tokens(chars: int) -> int:
    return max(0, chars // 4)


def clear_log(log_path: Path | None = None) -> bool:
    """Delete the tracking log. Returns True if it was removed."""
    path = log_path or RTK_LOG_PATH
    try:
        if path.exists():
            os.remove(path)
            return True
    except OSError:
        pass
    return False


__all__ = ["RTK_LOG_PATH", "RtkEvent", "record_event", "iter_events", "summarise", "clear_log"]
