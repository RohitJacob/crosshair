"""Combine per-signal contributions into a single safepoint decision.

The detector is pure: given a prompt plus the current ``ConversationState``
and the safepoint config, it returns what level of advisory to emit. The
caller decides whether to block, annotate, or append a handoff summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from crosshair.safepoint import signals as sig
from crosshair.state import ConversationState
from crosshair.util import tokenize


@dataclass
class SafepointDecision:
    score: int
    level: int  # 0=none, 1=note, 2=suggest, 3=strong
    label: str  # "none" | "note" | "suggest" | "strong"
    reasons: list[str] = field(default_factory=list)
    signal_names: list[str] = field(default_factory=list)

    @property
    def should_advise(self) -> bool:
        return self.level > 0


def evaluate(
    prompt: str,
    state: ConversationState,
    safepoint_cfg: dict,
) -> SafepointDecision:
    if not safepoint_cfg.get("enabled", True):
        return SafepointDecision(score=0, level=0, label="none")

    stopwords = safepoint_cfg.get("stopwords", []) or []
    current_keywords = tokenize(prompt or "", stopwords)

    collected: list[sig.Signal] = [
        sig.token_bloat(state, safepoint_cfg),
        sig.topic_shift(state, current_keywords, safepoint_cfg),
        sig.completion_marker(prompt or "", safepoint_cfg),
        sig.tool_volume(state, safepoint_cfg),
        sig.file_sprawl(state, safepoint_cfg),
        sig.error_loop(state, safepoint_cfg),
        sig.time_gap(state, safepoint_cfg),
        sig.session_length(state, safepoint_cfg),
    ]

    emitted = [s for s in collected if s.emitted]
    score = sum(s.weight for s in emitted)
    score = min(score, 100)

    thresholds = safepoint_cfg.get("action_thresholds", {}) or {}
    strong = int(thresholds.get("strong", 90))
    suggest = int(thresholds.get("suggest", 70))
    note = int(thresholds.get("note", 50))

    if score >= strong:
        level, label = 3, "strong"
    elif score >= suggest:
        level, label = 2, "suggest"
    elif score >= note:
        level, label = 1, "note"
    else:
        level, label = 0, "none"

    return SafepointDecision(
        score=score,
        level=level,
        label=label,
        reasons=[f"{s.name}: {s.detail}" for s in emitted],
        signal_names=[s.name for s in emitted],
    )


def render_advice(decision: SafepointDecision) -> str:
    """Short human-readable advice string for the hook's user_message."""
    if decision.level == 0:
        return ""
    header = {
        1: "Crosshair: context is getting heavy — jot down a stopping point soon.",
        2: "Crosshair: good spot to wrap up or summarise before continuing.",
        3: "Crosshair: strongly recommend starting a new chat. Handoff summary below.",
    }[decision.level]
    bullets = "\n".join(f"  - {r}" for r in decision.reasons)
    return f"{header}\n{bullets}" if bullets else header
