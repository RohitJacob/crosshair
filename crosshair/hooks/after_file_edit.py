"""afterFileEdit hook.

Records the unique file paths edited in this conversation so the handoff
summary has real @references and the file_sprawl safepoint signal can fire.
"""

from __future__ import annotations

from pathlib import Path
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
    conversation_id = input_data.get("conversation_id", "") or "unknown"

    path = _extract_path(input_data)
    if not path:
        return {}

    state = store.load(conversation_id)
    relative = _relativize(path, state.workspace or "")
    if relative not in state.files_touched:
        state.files_touched.append(relative)
    state.metrics["file_edits"] = int(state.metrics.get("file_edits", 0)) + 1
    if len(state.files_touched) > 50:
        state.files_touched = state.files_touched[-50:]

    store.save(state)

    logger.log(
        "file_edit",
        conversation_id=conversation_id,
        path=relative,
        total_edits=state.metrics["file_edits"],
    )
    return {}


def _extract_path(data: dict[str, Any]) -> str:
    tool_input = data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "target_file", "file"):
            val = tool_input.get(key)
            if isinstance(val, str) and val:
                return val
    for key in ("path", "file_path", "target_file", "file"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _relativize(path: str, workspace: str) -> str:
    try:
        if workspace and path.startswith(workspace):
            return str(Path(path).relative_to(workspace))
    except ValueError:
        pass
    return path
