"""PostToolUse adapter for file-editing tools, matcher ``Edit|Write|MultiEdit|NotebookEdit``.

Registered as a second ``PostToolUse`` hook entry alongside ``post_tool_use``
so file-sprawl tracking and tally counting stay independent, mirroring how
Cursor fires ``afterFileEdit`` separately from ``postToolUse``.
"""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.hooks import after_file_edit as internal
from crosshair.logs import EventLogger
from crosshair.state import StateStore


def run(
    raw: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> tuple[dict[str, Any], int, str]:
    internal.run(
        {
            "conversation_id": raw.get("session_id", "") or "unknown",
            "tool_input": raw.get("tool_input") or {},
        },
        config,
        logger,
        store,
    )
    return {}, 0, ""
