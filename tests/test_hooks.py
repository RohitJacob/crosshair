"""End-to-end hook handler tests — we pipe a JSON input through and assert
the output matches what Cursor expects."""

from __future__ import annotations

import json
from pathlib import Path

from crosshair.hooks import after_file_edit, after_response, before_submit, post_tool, session_start, stop
from crosshair.state import StateStore


def _input(**kwargs):
    base = {
        "conversation_id": "conv-1",
        "generation_id": "gen-1",
        "model": "claude-4-opus",
        "hook_event_name": "beforeSubmitPrompt",
        "cursor_version": "1.7.2",
        "workspace_roots": ["/workspace"],
        "user_email": None,
        "transcript_path": None,
    }
    base.update(kwargs)
    return base


def test_session_start_emits_guidance(tmp_config, logger, state_store):
    out = session_start.run({"conversation_id": "c"}, tmp_config, logger, state_store)
    assert "additional_context" in out
    assert "haiku" in out["additional_context"].lower()


def test_before_submit_blocks_opus_for_git(tmp_config, logger, state_store):
    out = before_submit.run(
        _input(prompt="git commit all changes with message: update"),
        tmp_config,
        logger,
        state_store,
    )
    assert out["continue"] is False
    assert "haiku" in out.get("user_message", "").lower()


def test_before_submit_override_bypasses_block(tmp_config, logger, state_store):
    out = before_submit.run(
        _input(prompt="! git commit all changes"),
        tmp_config,
        logger,
        state_store,
    )
    assert out["continue"] is True


def test_before_submit_allows_matching_model(tmp_config, logger, state_store):
    out = before_submit.run(
        _input(prompt="git commit all changes", model="claude-4-haiku"),
        tmp_config,
        logger,
        state_store,
    )
    assert out["continue"] is True


def test_before_submit_accumulates_state(tmp_config, logger, state_store: StateStore):
    before_submit.run(
        _input(prompt="build a new feature that logs user clicks"),
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("conv-1")
    assert state.metrics["user_turns"] == 1
    assert state.metrics["estimated_tokens"] > 0
    assert state.first_prompt.startswith("build a new feature")


def test_before_submit_safepoint_strong_includes_handoff(tmp_config, logger, state_store):
    # Seed state well past the hard token threshold so evaluate() returns level 3.
    state = state_store.load("conv-1")
    state.metrics["estimated_tokens"] = 200_000
    state.metrics["user_turns"] = 40
    state.first_prompt = "Refactor the auth system end to end"
    state.files_touched = ["src/auth.py", "src/session.py"]
    state_store.save(state)

    out = before_submit.run(
        _input(prompt="continue with the auth refactor"),
        tmp_config,
        logger,
        state_store,
    )
    msg = out.get("user_message", "")
    assert "Crosshair handoff" in msg
    assert "Budget" in msg


def test_after_response_accumulates_tokens(tmp_config, logger, state_store):
    after_response.run(
        {
            "conversation_id": "conv-1",
            "response": "x" * 4000,
        },
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("conv-1")
    assert state.metrics["estimated_tokens"] >= 1000
    assert state.metrics["assistant_turns"] == 1


def test_post_tool_records_failure(tmp_config, logger, state_store):
    post_tool.run(
        {
            "conversation_id": "conv-1",
            "tool_name": "Shell",
            "tool_output": "err",
            "failure_type": "error",
            "error_message": "exit 1",
        },
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("conv-1")
    assert state.metrics["tool_failures"] == 1
    assert state.recent_errors[0]["tool"] == "Shell"


def test_after_file_edit_tracks_files(tmp_config, logger, state_store):
    after_file_edit.run(
        {
            "conversation_id": "conv-1",
            "tool_input": {"path": "/workspace/src/app.py"},
        },
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("conv-1")
    assert state.metrics["file_edits"] == 1
    assert state.files_touched == ["/workspace/src/app.py"] or state.files_touched == ["src/app.py"]


def test_stop_records_outcome(tmp_config, logger, state_store):
    out = stop.run(
        {
            "conversation_id": "conv-1",
            "status": "completed",
            "loop_count": 3,
            "model": "claude-4-sonnet",
        },
        tmp_config,
        logger,
        state_store,
    )
    # No special output required; it's just observation.
    assert isinstance(out, dict)


def test_cli_hook_entry_is_fail_open(tmp_config, logger, state_store, tmp_path, monkeypatch):
    """Malformed JSON into the hook entry should still produce `{}` on stdout
    and exit 0 — Cursor fails open on garbage output."""
    from crosshair import cli

    input_file = tmp_path / "in.json"
    input_file.write_text("not-json")

    # Monkeypatch load_config so the entry uses our tmp_config.
    monkeypatch.setattr(cli, "load_config", lambda: tmp_config)

    captured = []

    class _Stdout:
        def write(self, text):
            captured.append(text)

    monkeypatch.setattr(cli.sys, "stdout", _Stdout())

    rc = cli.main(["hook", "before-submit", "--input-file", str(input_file)])
    assert rc == 0
    assert captured, "hook entry must write something"
    parsed = json.loads(captured[0])
    # Fail-open shape: either {} or {"continue": true}. Anything else means
    # we would block a user prompt on garbage input — not allowed.
    assert parsed == {} or parsed.get("continue") is True
    assert "user_message" not in parsed
