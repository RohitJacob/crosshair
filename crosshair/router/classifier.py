"""Task classifier.

Reads the rule list from config and returns a recommended model category
(``haiku`` / ``sonnet`` / ``opus`` / ``gpt-5`` / custom). First matching rule
wins, so order matters in the config.

Each rule supports:

- ``pattern_any``: list of regexes. At least one must match.
- ``pattern_all``: list of regexes. All must match.
- ``pattern_none``: list of regexes. None may match (used to disqualify).
- ``min_words`` / ``max_words``: optional word-count bounds.
- ``target``: category name (key of ``router.models``).

This is a conservative classifier: when no rule matches, we return ``None``
and the router leaves the user alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WORDS_RE = re.compile(r"\S+")


@dataclass
class ClassifierResult:
    target: str | None
    rule: str | None
    word_count: int
    override: bool

    @property
    def matched(self) -> bool:
        return self.target is not None


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns or []]


def _rule_matches(rule: dict[str, Any], prompt: str, words: int) -> bool:
    min_w = rule.get("min_words")
    max_w = rule.get("max_words")
    if isinstance(min_w, int) and words < min_w:
        return False
    if isinstance(max_w, int) and words > max_w:
        return False

    any_patterns = _compile(rule.get("pattern_any", []))
    all_patterns = _compile(rule.get("pattern_all", []))
    none_patterns = _compile(rule.get("pattern_none", []))

    if any_patterns and not any(p.search(prompt) for p in any_patterns):
        return False
    if all_patterns and not all(p.search(prompt) for p in all_patterns):
        return False
    if none_patterns and any(p.search(prompt) for p in none_patterns):
        return False

    # A rule needs at least one positive constraint; word-count-only rules are
    # allowed explicitly (opus-long-analytical uses min_words).
    if not any_patterns and not all_patterns and min_w is None and max_w is None:
        return False
    return True


def resolve_model_category(model_name: str, router_cfg: dict[str, Any]) -> str | None:
    """Given a runtime model string, find its canonical category."""
    if not model_name:
        return None
    lower = model_name.lower()
    models = router_cfg.get("models", {})
    # Longest alias first so "claude-4.5-sonnet" beats "sonnet".
    alias_to_category: list[tuple[str, str]] = []
    for category, meta in models.items():
        for alias in meta.get("aliases", []):
            alias_to_category.append((alias.lower(), category))
    alias_to_category.sort(key=lambda kv: len(kv[0]), reverse=True)
    for alias, category in alias_to_category:
        if alias and alias in lower:
            return category
    return None


def classify(prompt: str, router_cfg: dict[str, Any]) -> ClassifierResult:
    override_prefix = router_cfg.get("override_prefix", "!")
    override = False
    clean = prompt or ""
    stripped = clean.lstrip()
    if override_prefix and stripped.startswith(override_prefix):
        override = True
        clean = stripped[len(override_prefix) :].lstrip()

    words = len(_WORDS_RE.findall(clean))

    if not router_cfg.get("enabled", True) or override:
        return ClassifierResult(target=None, rule=None, word_count=words, override=override)

    for rule in router_cfg.get("rules", []):
        try:
            if _rule_matches(rule, clean, words):
                return ClassifierResult(
                    target=rule.get("target"),
                    rule=rule.get("name"),
                    word_count=words,
                    override=override,
                )
        except re.error:
            # Bad regex in config should never take the agent loop down.
            continue

    return ClassifierResult(target=None, rule=None, word_count=words, override=override)


def decide_action(
    current_category: str | None,
    recommendation: str | None,
    router_cfg: dict[str, Any],
) -> tuple[str, str]:
    """Decide whether to allow / block-downgrade / block-upgrade / note.

    Returns a tuple of (action, template_key) where action is one of:
    ``"allow"``, ``"block_downgrade"``, ``"block_upgrade"``, ``"nudge"``.
    """
    if not recommendation or not current_category:
        return "allow", ""
    if recommendation == current_category:
        return "allow", ""

    downgrade_cfg = router_cfg.get("block_downgrade", {}) or {}
    upgrade_cfg = router_cfg.get("block_upgrade", {}) or {}
    cost_order = {"low": 0, "medium": 1, "medium-high": 2, "high": 3}

    models = router_cfg.get("models", {})
    current_tier = cost_order.get(
        (models.get(current_category, {}) or {}).get("cost_tier", "medium"), 1
    )
    rec_tier = cost_order.get(
        (models.get(recommendation, {}) or {}).get("cost_tier", "medium"), 1
    )

    if rec_tier < current_tier and current_category in (downgrade_cfg.get("from") or []):
        return "block_downgrade", "template"
    if rec_tier > current_tier and current_category in (upgrade_cfg.get("from") or []):
        return "block_upgrade", "template"

    # Cost tiers same or no explicit policy — soft nudge only.
    return "nudge", ""
