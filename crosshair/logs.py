"""NDJSON event logging.

Every recommendation, override, completion, safepoint nudge, tool outcome,
and compaction event is appended as one JSON object per line. Failures are
swallowed so logging can never block Cursor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crosshair.config import Config
from crosshair.util import ensure_dir, expand, now_iso, truncate


class EventLogger:
    """Append-only NDJSON writer scoped to a loaded Config."""

    def __init__(self, config: Config) -> None:
        self.config = config
        logging_cfg = config.logging or {}
        self.path = expand(logging_cfg.get("path", "~/.cursor/crosshair/logs/events.ndjson"))
        self.prompt_limit = int(logging_cfg.get("truncate_prompts_to", 80))
        self.debug_enabled = bool(logging_cfg.get("debug_enabled", False))
        self.debug_path = expand(
            logging_cfg.get("debug_path", "~/.cursor/crosshair/logs/debug.ndjson")
        )

    def log(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {"event": event, "ts": now_iso(), **fields}
        self._write(self.path, payload)

    def debug(self, location: str, **fields: Any) -> None:
        if not self.debug_enabled:
            return
        payload: dict[str, Any] = {
            "location": location,
            "ts": now_iso(),
            **fields,
        }
        self._write(self.debug_path, payload)

    def snippet(self, text: str) -> str:
        if text is None:
            return ""
        safe = text.replace("\n", " ").replace("\r", " ")
        return truncate(safe, self.prompt_limit)

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            ensure_dir(path.parent)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            # Logging must never raise.
            pass
