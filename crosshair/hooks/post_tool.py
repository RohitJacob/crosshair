"""postToolUse hook.

Counts tool calls, accumulates rough token cost of tool outputs, and records
failures so the error-loop safepoint signal can fire.
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
    tool = input_data.get("tool_name", "")
    output = input_data.get("tool_output", "")
    if not isinstance(output, str):
        try:
            output = str(output)
        except Exception:
            output = ""

    state = store.load(conversation_id)
    state.metrics["tool_calls"] = int(state.metrics.get("tool_calls", 0)) + 1
    state.metrics["estimated_tokens"] = int(state.metrics.get("estimated_tokens", 0)) + approx_tokens(output)

    # postToolUse fires for successful tool uses. The accompanying
    # postToolUseFailure hook (if wired) records errors; but this file also
    # sanity-checks the shape of a known-successful call.
    failure_type = input_data.get("failure_type")
    error_message = input_data.get("error_message")
    if failure_type or error_message:
        state.metrics["tool_failures"] = int(state.metrics.get("tool_failures", 0)) + 1
        state.recent_errors.append(
            {
                "tool": tool,
                "failure_type": failure_type or "error",
                "error": (error_message or "")[:160],
            }
        )
        if len(state.recent_errors) > 20:
            state.recent_errors = state.recent_errors[-20:]

    store.save(state)

    logger.log(
        "tool",
        conversation_id=conversation_id,
        tool=tool,
        duration_ms=input_data.get("duration"),
        output_chars=len(output) if isinstance(output, str) else 0,
        estimated_tokens=state.metrics["estimated_tokens"],
        failure_type=failure_type,
    )
    return {}
