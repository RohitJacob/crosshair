from __future__ import annotations

from crosshair.router.classifier import classify, decide_action, resolve_model_category


def test_override_prefix_skips_classification(default_config):
    result = classify("! just build the thing on opus", default_config.router)
    assert result.override is True
    assert result.matched is False


def test_git_commit_routes_to_haiku(default_config):
    result = classify("git commit and push the changes", default_config.router)
    assert result.target == "haiku"
    assert result.rule == "haiku-git-ops"


def test_bug_keyword_disqualifies_haiku(default_config):
    # "fix the git commit bug" should not be haiku — bug keyword excludes it.
    result = classify("fix the git commit bug on main", default_config.router)
    assert result.target != "haiku"


def test_architecture_routes_to_opus(default_config):
    prompt = "Let's architect a new multi-system approach and evaluate tradeoffs"
    result = classify(prompt, default_config.router)
    assert result.target == "opus"


def test_long_prompt_routes_to_opus(default_config):
    prompt = " ".join(["word"] * 250)
    result = classify(prompt, default_config.router)
    assert result.target == "opus"


def test_implementation_routes_to_sonnet(default_config):
    result = classify("build a new UserProfile page component", default_config.router)
    assert result.target == "sonnet"


def test_resolve_model_category_prefers_longest_alias(default_config):
    assert resolve_model_category("claude-4.5-sonnet", default_config.router) == "sonnet"
    assert resolve_model_category("claude-4-haiku", default_config.router) == "haiku"
    assert resolve_model_category("unknown-model", default_config.router) is None


def test_decide_action_blocks_opus_downgrade(default_config):
    action, _ = decide_action("opus", "haiku", default_config.router)
    assert action == "block_downgrade"


def test_decide_action_blocks_haiku_upgrade(default_config):
    action, _ = decide_action("haiku", "sonnet", default_config.router)
    assert action == "block_upgrade"


def test_decide_action_same_tier_is_nudge(default_config):
    # sonnet and gpt-5 are both tier "medium" — no block.
    action, _ = decide_action("sonnet", "gpt-5", default_config.router)
    assert action != "block_downgrade"
    assert action != "block_upgrade"


def test_disabled_router_short_circuits(default_config):
    cfg = dict(default_config.router)
    cfg["enabled"] = False
    result = classify("git commit fast", cfg)
    assert result.matched is False
