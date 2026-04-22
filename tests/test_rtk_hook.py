"""Tests for the preToolUse hook.

These verify the contract we return to Cursor:

- rewriteable commands → ``{"permission": "allow", "updated_input": {"command": ...}}``
- passthrough commands → ``{}``
- non-shell tools → ``{}`` (we only care about Shell)
- disabled config → ``{}``
- buggy rewriter → ``{}`` (fail open)
"""

from __future__ import annotations

from crosshair.config import Config
from crosshair.hooks import pre_tool_use


def _run(tmp_config: Config, logger, state_store, *, cmd: str, tool: str = "Shell"):
    return pre_tool_use.run(
        {"tool_name": tool, "tool_input": {"command": cmd}},
        tmp_config,
        logger,
        state_store,
    )


def test_hook_rewrites_known_command(tmp_config, logger, state_store):
    out = _run(tmp_config, logger, state_store, cmd="git status")
    assert out["permission"] == "allow"
    assert out["updated_input"]["command"] == "rtk git status"


def test_hook_passthrough_for_unknown(tmp_config, logger, state_store):
    assert _run(tmp_config, logger, state_store, cmd="echo hi") == {}


def test_hook_passthrough_for_already_rewritten(tmp_config, logger, state_store):
    # Idempotent on both the new short form and the legacy long form so in-flight
    # commands queued before this change don't get double-wrapped.
    assert _run(tmp_config, logger, state_store, cmd="rtk git status") == {}
    assert _run(tmp_config, logger, state_store, cmd="crosshair rtk git status") == {}


def test_hook_ignores_non_shell_tools(tmp_config, logger, state_store):
    out = _run(tmp_config, logger, state_store, cmd="git status", tool="Read")
    assert out == {}


def test_hook_disabled_via_config(tmp_config, logger, state_store):
    tmp_config.data["rtk"] = {"enabled": False}
    assert _run(tmp_config, logger, state_store, cmd="git status") == {}


def test_hook_respects_exclude_list(tmp_config, logger, state_store):
    tmp_config.data["rtk"] = {"enabled": True, "exclude_commands": ["git"]}
    # Even though the rewriter would match "git status", the exclusion skips it.
    assert _run(tmp_config, logger, state_store, cmd="git status") == {}


def test_hook_fails_open_on_rewriter_error(monkeypatch, tmp_config, logger, state_store):
    def explode(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(pre_tool_use, "rewrite_command", explode)
    out = _run(tmp_config, logger, state_store, cmd="git status")
    assert out == {}


def test_hook_rewrites_compound(tmp_config, logger, state_store):
    out = _run(tmp_config, logger, state_store, cmd="git add . && git commit -m 'x'")
    assert out["permission"] == "allow"
    assert (
        out["updated_input"]["command"]
        == "rtk git add . && rtk git commit -m 'x'"
    )
