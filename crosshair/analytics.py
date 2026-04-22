"""Aggregate the NDJSON event log into a compact report."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


def _iter_events(path: Path, days: int | None) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    cutoff = None
    if days is not None and days > 0:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff is not None:
                ts = evt.get("ts")
                if isinstance(ts, str):
                    try:
                        evt_ts = datetime.fromisoformat(ts)
                        if evt_ts.tzinfo is None:
                            evt_ts = evt_ts.replace(tzinfo=timezone.utc)
                        if evt_ts < cutoff:
                            continue
                    except ValueError:
                        pass
            yield evt


def render_report(path: Path, days: int | None = None) -> dict[str, Any]:
    total = 0
    router_actions: Counter[str] = Counter()
    router_by_model: Counter[str] = Counter()
    safepoint_by_level: Counter[str] = Counter()
    safepoint_signal_counts: Counter[str] = Counter()
    tool_calls = 0
    tool_failures = 0
    estimated_tokens = 0
    last_token_per_conv: dict[str, int] = {}

    for evt in _iter_events(path, days):
        total += 1
        name = evt.get("event")
        if name == "router":
            action = evt.get("action", "unknown")
            router_actions[action] += 1
            model = evt.get("model") or "unknown"
            if action != "override":
                router_by_model[model] += 1
        elif name == "safepoint":
            label = evt.get("label") or "note"
            safepoint_by_level[label] += 1
            for s in evt.get("signals", []) or []:
                safepoint_signal_counts[s] += 1
        elif name == "tool":
            tool_calls += 1
            if evt.get("failure_type"):
                tool_failures += 1
        conv_id = evt.get("conversation_id")
        tokens = evt.get("estimated_tokens")
        if isinstance(conv_id, str) and isinstance(tokens, int):
            last_token_per_conv[conv_id] = tokens

    estimated_tokens = sum(last_token_per_conv.values())

    return {
        "summary": {"total_events": total, "days": days},
        "router": {
            "total": sum(router_actions.values()),
            "actions": dict(router_actions),
            "by_model": dict(router_by_model.most_common()),
        },
        "safepoint": {
            "by_level": dict(safepoint_by_level),
            "top_signals": safepoint_signal_counts.most_common(),
        },
        "tools": {
            "calls": tool_calls,
            "failures": tool_failures,
        },
        "tokens": {"estimated": estimated_tokens},
    }
