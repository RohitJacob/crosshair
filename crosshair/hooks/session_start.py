"""sessionStart hook: inject the routing guidance so the model itself
understands when to suggest stepping down or stepping up."""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.logs import EventLogger
from crosshair.state import StateStore


def run(
    input_data: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> dict[str, Any]:
    logger.log(
        "session_start",
        conversation_id=input_data.get("conversation_id", ""),
        cursor_version=input_data.get("cursor_version", ""),
        model=input_data.get("model", ""),
    )

    # Prune old state files in the background (best effort).
    try:
        store.prune_stale()
    except Exception:
        pass

    session_cfg = config.session_start or {}
    if not session_cfg.get("enabled", True):
        return {}

    guidance = session_cfg.get("guidance_text", "").strip()
    if not guidance:
        return {}
    return {"additional_context": guidance}
