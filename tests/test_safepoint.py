from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crosshair.safepoint.detector import evaluate
from crosshair.state import ConversationState
from crosshair.util import tokenize


def _state(**overrides) -> ConversationState:
    s = ConversationState(conversation_id="test")
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_empty_state_is_none(default_config):
    decision = evaluate("what's the status?", _state(), default_config.safepoint)
    assert decision.level == 0
    assert decision.score == 0


def test_token_bloat_mid(default_config):
    state = _state(metrics={"estimated_tokens": 160_000, "user_turns": 5,
                             "assistant_turns": 5, "tool_calls": 5,
                             "tool_failures": 0, "file_edits": 0})
    decision = evaluate("ok continue", state, default_config.safepoint)
    assert decision.score >= 40
    assert any("token_bloat" in r for r in decision.reasons)


def test_token_bloat_hard_hits_strong(default_config):
    state = _state(metrics={"estimated_tokens": 190_000, "user_turns": 5,
                             "assistant_turns": 5, "tool_calls": 5,
                             "tool_failures": 0, "file_edits": 0})
    decision = evaluate("ok continue", state, default_config.safepoint)
    assert decision.level == 3
    assert decision.score >= 90


def test_completion_marker_detected(default_config):
    decision = evaluate("thanks, lgtm. let's move on to the next task",
                        _state(), default_config.safepoint)
    assert decision.score >= 25
    assert any("completion_marker" in r for r in decision.reasons)


def test_completion_marker_not_substring(default_config):
    # "thanksgiving" should not match "thanks"
    decision = evaluate("what about thanksgiving holiday scheduling",
                        _state(), default_config.safepoint)
    assert not any("completion_marker" in r for r in decision.reasons)


def test_topic_shift_triggers_after_history(default_config):
    stopwords = default_config.safepoint["stopwords"]
    # Past prompts all about auth; new prompt is about chart rendering.
    state = _state(
        recent_keywords=[
            tokenize("implement login and oauth redirect", stopwords),
            tokenize("fix token refresh and cookie auth", stopwords),
            tokenize("update oauth scopes and login callback", stopwords),
        ]
    )
    decision = evaluate(
        "switch gears, let's render the bar chart with d3",
        state,
        default_config.safepoint,
    )
    assert any("topic_shift" in r for r in decision.reasons)


def test_error_loop_signal(default_config):
    state = _state(
        recent_errors=[
            {"tool": "Shell", "failure_type": "error", "error": "boom"},
            {"tool": "Shell", "failure_type": "error", "error": "boom"},
            {"tool": "Shell", "failure_type": "error", "error": "boom"},
        ]
    )
    decision = evaluate("try again", state, default_config.safepoint)
    assert any("error_loop" in r for r in decision.reasons)


def test_time_gap_signal(default_config):
    past = (datetime.now(tz=timezone.utc) - timedelta(minutes=90)).isoformat()
    state = _state(last_prompt_ts=past)
    decision = evaluate("back", state, default_config.safepoint)
    assert any("time_gap" in r for r in decision.reasons)


def test_combined_signals_reach_strong(default_config):
    stopwords = default_config.safepoint["stopwords"]
    state = _state(
        metrics={"estimated_tokens": 160_000, "user_turns": 60,
                 "assistant_turns": 60, "tool_calls": 60,
                 "tool_failures": 3, "file_edits": 30},
        files_touched=[f"src/file_{i}.py" for i in range(25)],
        recent_keywords=[
            tokenize("login oauth token auth", stopwords),
            tokenize("cookie refresh jwt scopes", stopwords),
            tokenize("session middleware redirect", stopwords),
        ],
        recent_errors=[
            {"tool": "Shell", "failure_type": "error", "error": "boom"},
            {"tool": "Shell", "failure_type": "error", "error": "boom"},
            {"tool": "Shell", "failure_type": "error", "error": "boom"},
        ],
    )
    decision = evaluate(
        "thanks! next, let's render a totally different bar chart with d3",
        state,
        default_config.safepoint,
    )
    assert decision.level == 3
    assert decision.score == 100  # saturates at 100


def test_disabled_safepoint_returns_none(default_config):
    cfg = dict(default_config.safepoint)
    cfg["enabled"] = False
    decision = evaluate("thanks!", _state(), cfg)
    assert decision.level == 0
