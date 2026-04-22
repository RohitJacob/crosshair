"""stop hook: agent loop ended. Record outcome for analytics and, if it looks
like a natural ending, flag it in state so the next before-submit can nudge
the user toward a new chat."""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.logs import EventLogger
from crosshair.state import StateStore
from crosshair.util import now_iso


def run(
    input_data: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> dict[str, Any]:
    conversation_id = input_data.get("conversation_id", "") or "unknown"
    status = input_data.get("status", "unknown")
    loop_count = input_data.get("loop_count", 0)
    model = (input_data.get("model") or "").lower()

    state = store.load(conversation_id)
    state.safepoint_last_ts = now_iso()
    store.save(state)

    logger.log(
        "stop",
        conversation_id=conversation_id,
        generation_id=input_data.get("generation_id", ""),
        model=model,
        status=status,
        loop_count=loop_count,
        estimated_tokens=state.metrics.get("estimated_tokens", 0),
    )
    return {}
