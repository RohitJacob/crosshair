from __future__ import annotations

from crosshair.safepoint.handoff import build_handoff_summary
from crosshair.state import ConversationState


def _state(**overrides) -> ConversationState:
    s = ConversationState(conversation_id="test")
    s.first_prompt = "Refactor auth middleware to support OAuth2 flows"
    s.last_prompt = "Now let's get the bar chart rendering"
    s.metrics = {
        "estimated_tokens": 142_000,
        "user_turns": 18,
        "assistant_turns": 18,
        "tool_calls": 40,
        "tool_failures": 2,
        "file_edits": 12,
    }
    s.files_touched = [
        "src/auth/middleware.py",
        "src/auth/oauth.py",
        "tests/test_auth.py",
    ]
    s.recent_errors = [
        {"tool": "Shell", "failure_type": "error",
         "error": "pytest tests/test_auth.py failed"},
        {"tool": "Shell", "failure_type": "error",
         "error": "pytest tests/test_auth.py failed"},
    ]
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_handoff_contains_task_and_progress(default_config):
    summary = build_handoff_summary(_state(), default_config.safepoint)
    assert "Refactor auth middleware" in summary
    assert "Progress" in summary
    assert "Key files" in summary


def test_handoff_lists_files_with_at_prefix(default_config):
    summary = build_handoff_summary(_state(), default_config.safepoint)
    assert "@src/auth/middleware.py" in summary


def test_handoff_deduplicates_errors(default_config):
    summary = build_handoff_summary(_state(), default_config.safepoint)
    # Same error listed twice should collapse to "×2"
    assert "×2" in summary


def test_handoff_includes_token_budget(default_config):
    summary = build_handoff_summary(_state(), default_config.safepoint)
    assert "142,000" in summary


def test_handoff_limits_file_count(default_config):
    state = _state(files_touched=[f"src/file_{i}.py" for i in range(20)])
    summary = build_handoff_summary(state, default_config.safepoint)
    # Config default include_max_files=8
    file_lines = [l for l in summary.splitlines() if l.strip().startswith("- @")]
    assert len(file_lines) <= 8


def test_handoff_with_empty_state(default_config):
    state = ConversationState(conversation_id="fresh")
    summary = build_handoff_summary(state, default_config.safepoint)
    assert "Crosshair handoff" in summary
    assert "(no prompt recorded)" in summary or "no files edited" in summary
