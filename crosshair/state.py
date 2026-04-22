"""Per-conversation state.

We keep one JSON file per ``conversation_id`` under
``~/.cursor/crosshair/state/``. Each file tracks:

- running estimated token count
- user turn counts, assistant turns, and tool calls
- recent prompt keyword sets (for topic-shift detection)
- files edited / touched
- tool failure counts (for error-loop detection)
- safepoint advisories already sent (to avoid spamming)

Only what's needed for safepoint scoring and the handoff summary is stored.
Prompts are truncated to the configured logging limit; raw prompts are never
retained.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from crosshair.config import Config
from crosshair.util import ensure_dir, expand, now_iso, truncate


def _default_metrics() -> dict[str, int]:
    return {
        "user_turns": 0,
        "assistant_turns": 0,
        "tool_calls": 0,
        "tool_failures": 0,
        "file_edits": 0,
        "estimated_tokens": 0,
    }


@dataclass
class ConversationState:
    conversation_id: str
    created_ts: str = field(default_factory=now_iso)
    updated_ts: str = field(default_factory=now_iso)
    workspace: str = ""
    first_prompt: str = ""
    last_prompt: str = ""
    last_prompt_ts: str = ""
    model_history: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=_default_metrics)
    recent_keywords: list[list[str]] = field(default_factory=list)
    recent_prompts: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    recent_errors: list[dict[str, Any]] = field(default_factory=list)
    completion_markers: list[str] = field(default_factory=list)
    safepoint_level_last: int = 0
    safepoint_last_score: int = 0
    safepoint_last_ts: str = ""
    safepoint_signals_last: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ConversationState":
        base = cls(conversation_id=data.get("conversation_id", ""))
        for k, v in data.items():
            if hasattr(base, k):
                setattr(base, k, v)
        if not base.metrics:
            base.metrics = _default_metrics()
        else:
            # Back-fill keys added in newer versions.
            for k, default in _default_metrics().items():
                base.metrics.setdefault(k, default)
        return base


class StateStore:
    """JSON-backed state store. Atomic writes via temp-file + rename."""

    def __init__(self, config: Config) -> None:
        self.config = config
        state_cfg = config.state or {}
        self.dir = expand(state_cfg.get("dir", "~/.cursor/crosshair/state"))
        self.retain_days = int(state_cfg.get("retain_days", 14))
        self.prompt_limit = int(config.logging.get("truncate_prompts_to", 80))

    def _path(self, conversation_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in conversation_id)
        if not safe:
            safe = "unknown"
        return self.dir / f"{safe}.json"

    def load(self, conversation_id: str) -> ConversationState:
        if not conversation_id:
            conversation_id = "unknown"
        path = self._path(conversation_id)
        if not path.exists():
            return ConversationState(conversation_id=conversation_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ConversationState.from_json(data)
        except (OSError, json.JSONDecodeError):
            return ConversationState(conversation_id=conversation_id)

    def save(self, state: ConversationState) -> None:
        if not state.conversation_id:
            return
        ensure_dir(self.dir)
        state.updated_ts = now_iso()
        path = self._path(state.conversation_id)
        tmp = path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state.to_json(), f, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except OSError:
            # Best effort: try again without atomic rename.
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(state.to_json(), f, ensure_ascii=False, indent=2)
            except OSError:
                pass

    def list_conversations(self) -> list[ConversationState]:
        if not self.dir.exists():
            return []
        results: list[ConversationState] = []
        for path in self.dir.glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append(ConversationState.from_json(data))
            except Exception:
                continue
        results.sort(key=lambda s: s.updated_ts, reverse=True)
        return results

    def reset(self, conversation_id: str) -> bool:
        if not conversation_id:
            return False
        path = self._path(conversation_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def reset_all(self) -> int:
        count = 0
        if not self.dir.exists():
            return 0
        for path in self.dir.glob("*.json"):
            try:
                path.unlink()
                count += 1
            except OSError:
                continue
        return count

    def prune_stale(self) -> int:
        """Delete state files not updated in retain_days. Returns count removed."""
        if not self.dir.exists() or self.retain_days <= 0:
            return 0
        cutoff = time.time() - self.retain_days * 86400
        count = 0
        for path in self.dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    count += 1
            except OSError:
                continue
        return count

    def truncate_prompt(self, text: str) -> str:
        return truncate((text or "").replace("\n", " "), self.prompt_limit)
