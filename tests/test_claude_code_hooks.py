"""End-to-end tests for the Claude Code hook adapters — real JSON shapes in,
real state file + hookSpecificOutput JSON out, no mocking of the internal
handlers or state store."""

from __future__ import annotations

from crosshair.claude_code import (
    after_file_edit,
    pre_compact,
    pre_tool_use,
    post_tool_use,
    session_start,
    stop,
    user_prompt_submit,
)


def test_session_start_emits_guidance(tmp_config, logger, state_store):
    output, exit_code, stderr = session_start.run(
        {"session_id": "sess-1"}, tmp_config, logger, state_store
    )
    assert exit_code == 0
    assert stderr == ""
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "haiku" in ctx.lower()


def test_before_submit_blocks_when_model_env_set(tmp_config, logger, state_store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-4-opus")
    output, exit_code, stderr = user_prompt_submit.run(
        {
            "session_id": "sess-1",
            "prompt_id": "gen-1",
            "user_input": "git commit all changes with message: update",
            "cwd": "/workspace",
        },
        tmp_config,
        logger,
        state_store,
    )
    assert exit_code == 2
    assert "haiku" in stderr.lower()
    assert output == {}


def test_before_submit_advisory_without_model_env(tmp_config, logger, state_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    output, exit_code, stderr = user_prompt_submit.run(
        {
            "session_id": "sess-1",
            "prompt_id": "gen-1",
            "user_input": "git commit all changes with message: update",
            "cwd": "/workspace",
        },
        tmp_config,
        logger,
        state_store,
    )
    # No known model to compare against -> never blocks, only advises.
    assert exit_code == 0
    assert stderr == ""
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "haiku" in ctx.lower()


def test_before_submit_override_bypasses_advisory(tmp_config, logger, state_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    output, exit_code, stderr = user_prompt_submit.run(
        {
            "session_id": "sess-1",
            "prompt_id": "gen-1",
            "user_input": "! git commit all changes",
            "cwd": "/workspace",
        },
        tmp_config,
        logger,
        state_store,
    )
    assert exit_code == 0
    assert output == {}


def test_before_submit_accumulates_real_state(tmp_config, logger, state_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    user_prompt_submit.run(
        {
            "session_id": "sess-1",
            "prompt_id": "gen-1",
            "user_input": "build a new feature that logs user clicks",
            "cwd": "/workspace",
        },
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("sess-1")
    assert state.metrics["user_turns"] == 1
    assert state.metrics["estimated_tokens"] > 0
    assert state.workspace == "/workspace"
    assert state.first_prompt.startswith("build a new feature")


def test_before_submit_safepoint_strong_includes_handoff(tmp_config, logger, state_store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    state = state_store.load("sess-1")
    state.metrics["estimated_tokens"] = 200_000
    state.metrics["user_turns"] = 40
    state.first_prompt = "Refactor the auth system end to end"
    state.files_touched = ["src/auth.py", "src/session.py"]
    state_store.save(state)

    output, exit_code, _ = user_prompt_submit.run(
        {
            "session_id": "sess-1",
            "prompt_id": "gen-2",
            "user_input": "continue with the auth refactor",
            "cwd": "/workspace",
        },
        tmp_config,
        logger,
        state_store,
    )
    assert exit_code == 0
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Crosshair handoff" in ctx
    assert "Budget" in ctx


def test_pre_tool_use_rewrites_bash_command(tmp_config, logger, state_store):
    output, exit_code, stderr = pre_tool_use.run(
        {"session_id": "sess-1", "tool_name": "Bash", "tool_input": {"command": "git status"}},
        tmp_config,
        logger,
        state_store,
    )
    assert exit_code == 0
    assert stderr == ""
    spec = output["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "allow"
    assert spec["updatedInput"] == {"command": "rtk git status"}


def test_pre_tool_use_passes_through_unmatched_command(tmp_config, logger, state_store):
    output, exit_code, _ = pre_tool_use.run(
        {"session_id": "sess-1", "tool_name": "Bash", "tool_input": {"command": "echo hi"}},
        tmp_config,
        logger,
        state_store,
    )
    assert exit_code == 0
    assert output == {}


def test_pre_tool_use_ignores_non_bash_tools(tmp_config, logger, state_store):
    output, exit_code, _ = pre_tool_use.run(
        {"session_id": "sess-1", "tool_name": "Edit", "tool_input": {"command": "git status"}},
        tmp_config,
        logger,
        state_store,
    )
    assert exit_code == 0
    assert output == {}


def test_post_tool_use_tallies_calls_and_failures(tmp_config, logger, state_store):
    post_tool_use.run(
        {
            "session_id": "sess-1",
            "tool_name": "Bash",
            "tool_output": {"stdout": "boom trace", "stderr": "exit 1", "exit_code": 1},
        },
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("sess-1")
    assert state.metrics["tool_calls"] == 1
    assert state.metrics["tool_failures"] == 1
    assert state.recent_errors[0]["tool"] == "Bash"
    assert state.recent_errors[0]["error"] == "exit 1"


def test_post_tool_use_success_does_not_record_failure(tmp_config, logger, state_store):
    post_tool_use.run(
        {
            "session_id": "sess-1",
            "tool_name": "Bash",
            "tool_output": {"stdout": "ok", "stderr": "", "exit_code": 0},
        },
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("sess-1")
    assert state.metrics["tool_calls"] == 1
    assert state.metrics["tool_failures"] == 0


def test_after_file_edit_tracks_files(tmp_config, logger, state_store):
    state = state_store.load("sess-1")
    state.workspace = "/workspace"
    state_store.save(state)

    after_file_edit.run(
        {"session_id": "sess-1", "tool_input": {"file_path": "/workspace/src/app.py"}},
        tmp_config,
        logger,
        state_store,
    )
    state = state_store.load("sess-1")
    assert state.metrics["file_edits"] == 1
    assert state.files_touched == ["src/app.py"]


def test_pre_compact_observes_without_blocking(tmp_config, logger, state_store):
    output, exit_code, _ = pre_compact.run(
        {"session_id": "sess-1", "trigger": "auto"}, tmp_config, logger, state_store
    )
    assert output == {}
    assert exit_code == 0


def test_stop_tallies_assistant_tokens_and_records_outcome(tmp_config, logger, state_store):
    output, exit_code, _ = stop.run(
        {
            "session_id": "sess-1",
            "last_assistant_message": "x" * 4000,
            "stop_reason": "end_turn",
        },
        tmp_config,
        logger,
        state_store,
    )
    assert output == {}
    assert exit_code == 0
    state = state_store.load("sess-1")
    assert state.metrics["assistant_turns"] == 1
    assert state.metrics["estimated_tokens"] >= 1000


def test_full_session_round_trip(tmp_config, logger, state_store, monkeypatch):
    """A realistic mini Claude Code session, run through every adapter in
    order, verifying the state store ends up with consistent real numbers —
    not just that each hook returns without error in isolation."""
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    session_start.run({"session_id": "sess-e2e"}, tmp_config, logger, state_store)

    user_prompt_submit.run(
        {
            "session_id": "sess-e2e",
            "prompt_id": "gen-1",
            "user_input": "fix the failing auth test",
            "cwd": "/workspace/app",
        },
        tmp_config,
        logger,
        state_store,
    )

    pre_output, _, _ = pre_tool_use.run(
        {"session_id": "sess-e2e", "tool_name": "Bash", "tool_input": {"command": "pytest tests/test_auth.py"}},
        tmp_config,
        logger,
        state_store,
    )
    assert pre_output["hookSpecificOutput"]["updatedInput"]["command"] == "rtk pytest tests/test_auth.py"

    post_tool_use.run(
        {
            "session_id": "sess-e2e",
            "tool_name": "Bash",
            "tool_output": {"stdout": "1 failed", "stderr": "AssertionError", "exit_code": 1},
        },
        tmp_config,
        logger,
        state_store,
    )
    after_file_edit.run(
        {"session_id": "sess-e2e", "tool_input": {"file_path": "/workspace/app/src/auth.py"}},
        tmp_config,
        logger,
        state_store,
    )
    stop.run(
        {"session_id": "sess-e2e", "last_assistant_message": "Fixed the assertion.", "stop_reason": "end_turn"},
        tmp_config,
        logger,
        state_store,
    )

    state = state_store.load("sess-e2e")
    assert state.workspace == "/workspace/app"
    assert state.metrics["user_turns"] == 1
    assert state.metrics["tool_calls"] == 1
    assert state.metrics["tool_failures"] == 1
    assert state.metrics["file_edits"] == 1
    assert state.metrics["assistant_turns"] == 1
    assert state.files_touched == ["src/auth.py"]
    assert state.recent_errors[0]["error"] == "AssertionError"


def test_claude_hook_cli_entry_is_fail_open(tmp_config, tmp_path, monkeypatch):
    """Malformed JSON must still exit 0 with `{}` on stdout — a bug here must
    never block a Claude Code session."""
    from crosshair import cli

    input_file = tmp_path / "in.json"
    input_file.write_text("not-json")

    monkeypatch.setattr(cli, "load_config_for_host", lambda host: tmp_config)

    captured_out = []
    captured_err = []
    monkeypatch.setattr(cli.sys.stdout, "write", captured_out.append)
    monkeypatch.setattr(cli.sys.stderr, "write", captured_err.append)

    rc = cli.main(["claude-hook", "before-submit", "--input-file", str(input_file)])
    assert rc == 0
    parsed = __import__("json").loads(captured_out[0])
    assert parsed == {}
