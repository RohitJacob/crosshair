"""preToolUse hook.

Matcher is ``Shell``. On each shell tool call, rewrite the command string
through ``crosshair.rtk.rewrite_command`` so supported commands get routed
through our Python filters (``crosshair rtk <cmd>``) for big token savings.

Output contract (Cursor):

- ``{}`` — no change, run the original command
- ``{"permission": "allow", "updated_input": {"command": "<rewritten>"}}`` —
  auto-allow and swap in the rewritten command

Fail-open: any exception is caught and logged; we return ``{}`` so the user's
command never gets blocked by our bug.
"""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.logs import EventLogger
from crosshair.rtk.rewrite import rewrite_command
from crosshair.state import StateStore


def run(
    input_data: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> dict[str, Any]:
    tool_name = input_data.get("tool_name") or input_data.get("tool") or ""
    tool_input = input_data.get("tool_input") or {}
    cmd = tool_input.get("command") or ""

    rtk_cfg = getattr(config, "rtk", {}) or {}
    if not rtk_cfg.get("enabled", True):
        return {}

    # Only rewrite shell-like tools. Cursor uses ``Shell`` but we also tolerate
    # other aliases for forward-compat.
    if tool_name and tool_name.lower() not in ("shell", "bash", "terminal"):
        return {}

    if not cmd or not isinstance(cmd, str):
        return {}

    excluded = rtk_cfg.get("exclude_commands", []) or []
    try:
        rewritten = rewrite_command(cmd, excluded=excluded)
    except Exception as exc:  # noqa: BLE001 — fail open on any bug
        logger.log(
            "rtk_rewrite_error",
            command_snippet=cmd[:160],
            error=str(exc)[:300],
        )
        return {}

    if not rewritten or rewritten == cmd:
        logger.log(
            "rtk_rewrite",
            action="passthrough",
            command_snippet=cmd[:160],
        )
        return {}

    logger.log(
        "rtk_rewrite",
        action="rewrite",
        command_snippet=cmd[:160],
        rewritten_snippet=rewritten[:160],
    )
    return {
        "permission": "allow",
        "updated_input": {"command": rewritten},
    }
