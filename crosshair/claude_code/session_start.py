"""SessionStart adapter."""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.hooks import session_start as internal
from crosshair.logs import EventLogger
from crosshair.state import StateStore


def run(
    raw: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> tuple[dict[str, Any], int, str]:
    result = internal.run(
        {
            "conversation_id": raw.get("session_id", "") or "unknown",
            "cursor_version": "",
            "model": raw.get("model", ""),
        },
        config,
        logger,
        store,
    )

    context = result.get("additional_context", "")
    if not context:
        return {}, 0, ""

    return (
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        },
        0,
        "",
    )
