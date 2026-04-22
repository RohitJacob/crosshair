"""Individual safepoint signals.

Each signal takes the current conversation state and the safepoint config and
returns an ``(emitted: bool, contribution: int, detail: str)`` tuple. The
detector sums contributions across all emitted signals.

Signals are intentionally stateless in themselves — all memory lives on the
``ConversationState`` object — so each one is trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from crosshair.state import ConversationState
from crosshair.util import jaccard, minutes_since, tokenize


@dataclass
class Signal:
    name: str
    emitted: bool
    weight: int
    detail: str

    @classmethod
    def empty(cls, name: str) -> "Signal":
        return cls(name=name, emitted=False, weight=0, detail="")


def token_bloat(state: ConversationState, cfg: dict) -> Signal:
    thresholds = cfg.get("token_thresholds", {}) or {}
    weights = cfg.get("weights", {}) or {}
    soft = int(thresholds.get("soft", 100_000))
    mid = int(thresholds.get("mid", 150_000))
    high = int(thresholds.get("high", 180_000))
    tokens = int(state.metrics.get("estimated_tokens", 0))

    if tokens >= high:
        return Signal("token_bloat", True, int(weights.get("token_bloat_high", 80)),
                      f"~{tokens:,} tokens ≥ hard limit {high:,}")
    if tokens >= mid:
        return Signal("token_bloat", True, int(weights.get("token_bloat_mid", 40)),
                      f"~{tokens:,} tokens ≥ mid limit {mid:,}")
    if tokens >= soft:
        return Signal("token_bloat", True, int(weights.get("token_bloat_soft", 20)),
                      f"~{tokens:,} tokens ≥ soft limit {soft:,}")
    return Signal.empty("token_bloat")


def topic_shift(
    state: ConversationState,
    current_keywords: Iterable[str],
    cfg: dict,
) -> Signal:
    history = state.recent_keywords or []
    weights = cfg.get("weights", {}) or {}
    threshold = float(cfg.get("topic_shift_jaccard_max", 0.15))
    history_size = int(cfg.get("topic_history_size", 3))

    current = list(current_keywords or [])
    if len(history) < history_size or len(current) < 3:
        return Signal.empty("topic_shift")

    # Compare against the union of the last N prompts' keywords.
    window: set[str] = set()
    for entry in history[-history_size:]:
        window.update(entry)
    if not window:
        return Signal.empty("topic_shift")

    sim = jaccard(current, window)
    if sim > threshold:
        return Signal.empty("topic_shift")
    return Signal(
        "topic_shift",
        True,
        int(weights.get("topic_shift", 30)),
        f"Jaccard similarity to last {history_size} prompts = {sim:.2f}",
    )


def completion_marker(prompt: str, cfg: dict) -> Signal:
    markers: list[str] = cfg.get("completion_markers", []) or []
    weights = cfg.get("weights", {}) or {}
    lowered = (prompt or "").lower()
    if not lowered:
        return Signal.empty("completion_marker")
    hits = []
    for marker in markers:
        m = marker.lower().strip()
        if not m:
            continue
        # Use word boundaries where possible so "thanks" doesn't match "thanksgiving".
        if re.search(rf"(?:^|[^a-z0-9]){re.escape(m)}(?:[^a-z0-9]|$)", lowered):
            hits.append(marker)
    if not hits:
        return Signal.empty("completion_marker")
    return Signal(
        "completion_marker",
        True,
        int(weights.get("completion_marker", 25)),
        f"Marker(s) hit: {', '.join(hits[:3])}",
    )


def tool_volume(state: ConversationState, cfg: dict) -> Signal:
    threshold = int(cfg.get("tool_volume_threshold", 50))
    weights = cfg.get("weights", {}) or {}
    calls = int(state.metrics.get("tool_calls", 0))
    if calls < threshold:
        return Signal.empty("tool_volume")
    return Signal("tool_volume", True, int(weights.get("tool_volume", 15)),
                  f"{calls} tool calls ≥ {threshold}")


def file_sprawl(state: ConversationState, cfg: dict) -> Signal:
    threshold = int(cfg.get("file_sprawl_threshold", 20))
    weights = cfg.get("weights", {}) or {}
    count = len(state.files_touched or [])
    if count < threshold:
        return Signal.empty("file_sprawl")
    return Signal("file_sprawl", True, int(weights.get("file_sprawl", 15)),
                  f"{count} files touched ≥ {threshold}")


def error_loop(state: ConversationState, cfg: dict) -> Signal:
    threshold = int(cfg.get("error_loop_threshold", 3))
    weights = cfg.get("weights", {}) or {}
    if not state.recent_errors:
        return Signal.empty("error_loop")

    # Count same tool+error_type occurrences.
    counts: dict[tuple[str, str], int] = {}
    for err in state.recent_errors[-10:]:
        key = (err.get("tool") or "", err.get("failure_type") or "error")
        counts[key] = counts.get(key, 0) + 1
    worst_key, worst_count = max(counts.items(), key=lambda kv: kv[1])
    if worst_count < threshold:
        return Signal.empty("error_loop")
    return Signal(
        "error_loop",
        True,
        int(weights.get("error_loop", 20)),
        f"{worst_key[0]} {worst_key[1]} × {worst_count}",
    )


def time_gap(state: ConversationState, cfg: dict) -> Signal:
    threshold = float(cfg.get("time_gap_minutes_threshold", 30))
    weights = cfg.get("weights", {}) or {}
    if not state.last_prompt_ts:
        return Signal.empty("time_gap")
    minutes = minutes_since(state.last_prompt_ts)
    if minutes < threshold:
        return Signal.empty("time_gap")
    return Signal("time_gap", True, int(weights.get("time_gap_minutes", 10)),
                  f"{minutes:.0f} min gap since last prompt")


def session_length(state: ConversationState, cfg: dict) -> Signal:
    threshold = int(cfg.get("session_length_threshold", 50))
    weights = cfg.get("weights", {}) or {}
    turns = int(state.metrics.get("user_turns", 0))
    if turns < threshold:
        return Signal.empty("session_length")
    return Signal("session_length", True, int(weights.get("session_length", 15)),
                  f"{turns} user turns ≥ {threshold}")


__all__ = [
    "Signal",
    "token_bloat",
    "topic_shift",
    "completion_marker",
    "tool_volume",
    "file_sprawl",
    "error_loop",
    "time_gap",
    "session_length",
]
