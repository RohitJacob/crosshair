"""PreToolUse adapter — rtk shell-command rewrite, matcher ``Bash``."""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.hooks import pre_tool_use as internal
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
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input") or {},
        },
        config,
        logger,
        store,
    )

    updated = result.get("updated_input")
    if not updated:
        return {}, 0, ""

    return (
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": updated,
            }
        },
        0,
        "",
    )
