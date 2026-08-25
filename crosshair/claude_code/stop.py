"""Stop adapter.

Claude Code has no separate "assistant response finished" event the way
Cursor splits ``afterAgentResponse`` from ``stop`` — ``Stop`` carries
``last_assistant_message`` directly, so this adapter tallies assistant tokens
and records the turn outcome in one pass.
"""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.hooks import after_response as after_response_internal
from crosshair.hooks import stop as stop_internal
from crosshair.logs import EventLogger
from crosshair.state import StateStore


def run(
    raw: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> tuple[dict[str, Any], int, str]:
    conversation_id = raw.get("session_id", "") or "unknown"

    after_response_internal.run(
        {
            "conversation_id": conversation_id,
            "response": raw.get("last_assistant_message", "") or "",
        },
        config,
        logger,
        store,
    )
    stop_internal.run(
        {
            "conversation_id": conversation_id,
            "status": raw.get("stop_reason", "end_turn"),
            "loop_count": 0,
        },
        config,
        logger,
        store,
    )
    return {}, 0, ""
