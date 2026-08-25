"""UserPromptSubmit adapter.

Claude Code does not reliably expose the model in use to hooks (no field on
this event, no ``$CLAUDE_MODEL`` env var — see the hooks reference). Without
a current model, the router's block/nudge comparison has nothing to compare
against, so ``crosshair.router.classifier.decide_action`` degrades to a no-op
by design, same as it would for an unrecognised model string under Cursor.

Two ways to get real router value back:

1. Set ``router.claude_code_model_env`` in config (default
   ``ANTHROPIC_MODEL``) and export that env var in your shell. This restores
   full block/nudge behaviour, with the same caveat the Claude Code docs
   note: it won't auto-update when you switch models with ``/model``.
2. Otherwise, we still run the classifier against the prompt and surface a
   soft, non-blocking advisory as ``additionalContext`` — real signal without
   pretending to know the current model.
"""

from __future__ import annotations

import os
from typing import Any

from crosshair.config import Config
from crosshair.hooks import before_submit as internal
from crosshair.logs import EventLogger
from crosshair.router.classifier import classify
from crosshair.state import StateStore


def run(
    raw: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> tuple[dict[str, Any], int, str]:
    prompt = raw.get("user_input", "") or ""
    conversation_id = raw.get("session_id", "") or "unknown"
    cwd = raw.get("cwd", "") or ""
    model_env = (config.router or {}).get("claude_code_model_env", "ANTHROPIC_MODEL")
    model = os.environ.get(model_env, "") if model_env else ""

    result = internal.run(
        {
            "prompt": prompt,
            "model": model,
            "conversation_id": conversation_id,
            "generation_id": raw.get("prompt_id", ""),
            "workspace_roots": [cwd] if cwd else [],
        },
        config,
        logger,
        store,
    )

    if result.get("continue") is False:
        reason = result.get("user_message") or "Blocked by crosshair router."
        return {}, 2, reason

    parts = [p for p in (result.get("user_message", ""), _advisory(prompt, model, config)) if p]
    if not parts:
        return {}, 0, ""

    return (
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n\n".join(parts),
            }
        },
        0,
        "",
    )


def _advisory(prompt: str, model: str, config: Config) -> str:
    """Non-blocking task-shape hint, only when we have no real model to
    compare against (otherwise ``internal.run`` already produced a nudge)."""
    if model:
        return ""
    result = classify(prompt, config.router)
    if result.override or not result.matched:
        return ""
    label = (result.rule or result.target or "this task").replace("-", " ")
    return f"Crosshair: this looks like {label}; {result.target} is usually the right model class for it."
