"""PostToolUse adapter — tool-call tally and failure tracking, matcher ``*``.

Claude Code splits success/failure into ``PostToolUse``/``PostToolUseFailure``.
We only wire the former and derive failure from a non-zero ``exit_code`` in
``tool_output``, which covers the common case (a failed shell command) without
depending on the less-documented failure event's payload shape.
"""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.hooks import post_tool as internal
from crosshair.logs import EventLogger
from crosshair.state import StateStore


def run(
    raw: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> tuple[dict[str, Any], int, str]:
    tool_output = raw.get("tool_output") or {}
    stdout = tool_output.get("stdout", "") if isinstance(tool_output, dict) else ""
    exit_code = tool_output.get("exit_code") if isinstance(tool_output, dict) else None

    failure_type = "error" if isinstance(exit_code, int) and exit_code != 0 else None
    error_message = tool_output.get("stderr", "") if failure_type and isinstance(tool_output, dict) else None

    internal.run(
        {
            "conversation_id": raw.get("session_id", "") or "unknown",
            "tool_name": raw.get("tool_name", ""),
            "tool_output": stdout,
            "failure_type": failure_type,
            "error_message": error_message,
        },
        config,
        logger,
        store,
    )
    return {}, 0, ""
