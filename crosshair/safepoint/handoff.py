"""Handoff summary generator.

Produces a paste-ready markdown snippet the user can drop into a new chat.
Strict char limits on every field keep the summary cheap — the whole point is
to reduce tokens, not add to them.
"""

from __future__ import annotations

from typing import Iterable

from crosshair.state import ConversationState
from crosshair.util import shorten_path, truncate


def build_handoff_summary(
    state: ConversationState,
    safepoint_cfg: dict,
    include_next_steps: bool = True,
) -> str:
    handoff_cfg = (safepoint_cfg or {}).get("handoff", {}) or {}
    max_files = int(handoff_cfg.get("include_max_files", 8))
    max_errors = int(handoff_cfg.get("include_max_errors", 3))

    task = truncate(state.first_prompt or state.last_prompt or "(no prompt recorded)", 160)
    last = truncate(state.last_prompt or "", 140)

    tokens = int(state.metrics.get("estimated_tokens", 0))
    user_turns = int(state.metrics.get("user_turns", 0))
    assistant_turns = int(state.metrics.get("assistant_turns", 0))
    tool_calls = int(state.metrics.get("tool_calls", 0))
    tool_failures = int(state.metrics.get("tool_failures", 0))
    file_edits = int(state.metrics.get("file_edits", 0))

    files = state.files_touched or []
    files = files[-max_files:] if len(files) > max_files else files
    files_md = (
        "\n".join(f"  - @{shorten_path(f)}" for f in files)
        if files
        else "  - (no files edited yet)"
    )

    errors = _distinct_errors(state.recent_errors or [], max_errors)
    errors_md = "\n".join(f"  - {e}" for e in errors) if errors else "  - (none outstanding)"

    progress_lines = []
    if file_edits:
        progress_lines.append(f"  - {file_edits} file edit(s) across {len(state.files_touched)} file(s)")
    if tool_calls:
        progress_lines.append(
            f"  - {tool_calls} tool call(s) ({tool_failures} failure(s))"
        )
    if assistant_turns:
        progress_lines.append(f"  - {assistant_turns} assistant turn(s)")
    if not progress_lines:
        progress_lines.append("  - (session just started)")

    next_steps = ""
    if include_next_steps:
        next_steps = "\n**Next steps**\n"
        next_steps += "\n".join(
            f"  - {s}" for s in _infer_next_steps(state, errors)
        )
        next_steps += "\n"

    budget_note = f"~{tokens:,} est. tokens, {user_turns} user turn(s)"

    return (
        "## Crosshair handoff\n\n"
        f"**Task**: {task}\n\n"
        f"**Latest message**: {last or '(none)'}\n\n"
        "**Progress**\n"
        f"{chr(10).join(progress_lines)}\n\n"
        "**Key files**\n"
        f"{files_md}\n\n"
        "**Outstanding**\n"
        f"{errors_md}\n"
        f"{next_steps}"
        f"\n_Budget_: {budget_note}."
    )


def _distinct_errors(entries: Iterable[dict], limit: int) -> list[str]:
    seen: dict[str, int] = {}
    for e in list(entries)[-20:]:
        tool = e.get("tool") or "tool"
        msg = truncate(e.get("error") or e.get("failure_type") or "error", 80)
        key = f"{tool}: {msg}"
        seen[key] = seen.get(key, 0) + 1
    # Sort by count desc, keep top N.
    ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
    return [f"{k} (×{v})" if v > 1 else k for k, v in ordered[:limit]]


def _infer_next_steps(state: ConversationState, errors: list[str]) -> list[str]:
    steps: list[str] = []
    if errors:
        steps.append(
            f"Re-address the outstanding error(s): {errors[0].split(' (×', 1)[0]}"
        )
    if state.files_touched:
        steps.append(
            "Continue iterating on "
            + ", ".join(shorten_path(f) for f in state.files_touched[-2:])
        )
    if state.last_prompt:
        steps.append(
            f"Pick up from: {truncate(state.last_prompt, 80)}"
        )
    if not steps:
        steps.append("State the new task in a fresh chat.")
    return steps[:4]
