"""afterAgentResponse hook.

Accumulates tokens produced by the assistant and bumps the assistant turn
counter. No user_message output; observation only.
"""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.logs import EventLogger
from crosshair.state import StateStore
from crosshair.util import approx_tokens


def run(
    input_data: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> dict[str, Any]:
    conversation_id = input_data.get("conversation_id", "") or "unknown"
    # Cursor calls this field "response" or "message" depending on version.
    text = (
        input_data.get("response")
        or input_data.get("message")
        or input_data.get("text")
        or ""
    )
    if not isinstance(text, str):
        text = str(text)

    state = store.load(conversation_id)
    state.metrics["estimated_tokens"] = int(state.metrics.get("estimated_tokens", 0)) + approx_tokens(text)
    state.metrics["assistant_turns"] = int(state.metrics.get("assistant_turns", 0)) + 1
    store.save(state)

    logger.log(
        "assistant_response",
        conversation_id=conversation_id,
        chars=len(text),
        estimated_tokens=state.metrics["estimated_tokens"],
    )
    return {}
