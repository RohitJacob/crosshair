"""PreCompact adapter — observation only, never blocks compaction."""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.hooks import pre_compact as internal
from crosshair.logs import EventLogger
from crosshair.state import StateStore


def run(
    raw: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> tuple[dict[str, Any], int, str]:
    internal.run(
        {"conversation_id": raw.get("session_id", "") or "unknown"},
        config,
        logger,
        store,
    )
    return {}, 0, ""
