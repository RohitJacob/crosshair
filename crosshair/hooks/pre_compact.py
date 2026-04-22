"""preCompact hook.

Cursor fires this when it's about to compact the context window. We just
observe it — this is gold-standard signal that the conversation is heavy, so
we mark the state and log it.
"""

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
    state = store.load(conversation_id)
    # Nudge the token count up so post-compaction the safepoint score still reflects heft.
    state.safepoint_last_ts = now_iso()
    store.save(state)
    logger.log(
        "compact",
        conversation_id=conversation_id,
        estimated_tokens=state.metrics.get("estimated_tokens", 0),
        input=input_data,
    )
    return {}
