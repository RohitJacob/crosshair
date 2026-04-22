"""The central rtk registry.

A single list of ``RtkRule`` entries drives both the shell rewriter and the
``rtk <cmd>`` runner (also reachable as ``crosshair rtk <cmd>``). Each rule is
self-contained:

- ``pattern``      regex matched against the leading command line (post env-strip)
- ``rewrite_to``   short label shown in ``rtk list`` (e.g. "git status")
- ``filter``       callable to run directly
- ``category``     category label for analytics
- ``est_savings``  rough savings percent — used for discover/gain displays

Keeping the rules data-only makes it trivial to add/remove/disable commands
via config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from crosshair.rtk.filters.base import FilterContext, FilterFn, FilterResult, passthrough
from crosshair.rtk.filters.build import (
    eslint_filter,
    prettier_filter,
    ruff_filter,
    tsc_filter,
)
from crosshair.rtk.filters.files import (
    find_filter as _find_filter_impl,
    grep_filter,
    ls_filter,
    read_filter,
    tree_filter,
)
from crosshair.rtk.filters.git import (
    git_add,
    git_branch,
    git_commit,
    git_diff,
    git_fetch,
    git_log,
    git_pull,
    git_push,
    git_status,
)
from crosshair.rtk.filters.infra import (
    docker_images_filter,
    docker_logs_filter,
    docker_ps_filter,
)
from crosshair.rtk.filters.tests import cargo_test_filter, pytest_filter, vitest_filter


@dataclass(frozen=True)
class RtkRule:
    name: str
    pattern: re.Pattern[str]
    rewrite_prefixes: tuple[str, ...]
    rewrite_to: str
    filter_fn: FilterFn
    category: str = "Other"
    est_savings: float = 70.0


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# The order matters: first match wins. More specific patterns first.
RTK_COMMANDS: list[RtkRule] = [
    # ------------------------------------------------------------------ Git #
    RtkRule(
        name="git-status",
        pattern=_re(r"^git\s+status(\s|$)"),
        rewrite_prefixes=("git status",),
        rewrite_to="git status",
        filter_fn=git_status,
        category="Git",
        est_savings=80.0,
    ),
    RtkRule(
        name="git-log",
        pattern=_re(r"^git\s+log(\s|$)"),
        rewrite_prefixes=("git log",),
        rewrite_to="git log",
        filter_fn=git_log,
        category="Git",
        est_savings=80.0,
    ),
    RtkRule(
        name="git-diff",
        pattern=_re(r"^git\s+diff(\s|$)"),
        rewrite_prefixes=("git diff",),
        rewrite_to="git diff",
        filter_fn=git_diff,
        category="Git",
        est_savings=75.0,
    ),
    RtkRule(
        name="git-add",
        pattern=_re(r"^git\s+add(\s|$)"),
        rewrite_prefixes=("git add",),
        rewrite_to="git add",
        filter_fn=git_add,
        category="Git",
        est_savings=92.0,
    ),
    RtkRule(
        name="git-commit",
        pattern=_re(r"^git\s+commit(\s|$)"),
        rewrite_prefixes=("git commit",),
        rewrite_to="git commit",
        filter_fn=git_commit,
        category="Git",
        est_savings=92.0,
    ),
    RtkRule(
        name="git-push",
        pattern=_re(r"^git\s+push(\s|$)"),
        rewrite_prefixes=("git push",),
        rewrite_to="git push",
        filter_fn=git_push,
        category="Git",
        est_savings=92.0,
    ),
    RtkRule(
        name="git-pull",
        pattern=_re(r"^git\s+pull(\s|$)"),
        rewrite_prefixes=("git pull",),
        rewrite_to="git pull",
        filter_fn=git_pull,
        category="Git",
        est_savings=85.0,
    ),
    RtkRule(
        name="git-branch",
        pattern=_re(r"^git\s+branch(\s|$)"),
        rewrite_prefixes=("git branch",),
        rewrite_to="git branch",
        filter_fn=git_branch,
        category="Git",
        est_savings=75.0,
    ),
    RtkRule(
        name="git-fetch",
        pattern=_re(r"^git\s+fetch(\s|$)"),
        rewrite_prefixes=("git fetch",),
        rewrite_to="git fetch",
        filter_fn=git_fetch,
        category="Git",
        est_savings=80.0,
    ),
    # ---------------------------------------------------------------- Files #
    RtkRule(
        name="ls",
        pattern=_re(r"^ls(\s|$)"),
        rewrite_prefixes=("ls",),
        rewrite_to="ls",
        filter_fn=ls_filter,
        category="Files",
        est_savings=80.0,
    ),
    RtkRule(
        name="read",
        pattern=_re(r"^(cat|head|tail)(\s|$)"),
        rewrite_prefixes=("cat", "head", "tail"),
        rewrite_to="read",
        filter_fn=read_filter,
        category="Files",
        est_savings=70.0,
    ),
    RtkRule(
        name="grep",
        pattern=_re(r"^(grep|rg)(\s|$)"),
        rewrite_prefixes=("grep", "rg"),
        rewrite_to="grep",
        filter_fn=grep_filter,
        category="Files",
        est_savings=80.0,
    ),
    RtkRule(
        name="find",
        pattern=_re(r"^find(\s|$)"),
        rewrite_prefixes=("find",),
        rewrite_to="find",
        filter_fn=_find_filter_impl,
        category="Files",
        est_savings=70.0,
    ),
    RtkRule(
        name="tree",
        pattern=_re(r"^tree(\s|$)"),
        rewrite_prefixes=("tree",),
        rewrite_to="tree",
        filter_fn=tree_filter,
        category="Files",
        est_savings=75.0,
    ),
    # ---------------------------------------------------------------- Tests #
    RtkRule(
        name="pytest",
        pattern=_re(r"^(python\s+-m\s+pytest|pytest|py\.test)(\s|$)"),
        rewrite_prefixes=("pytest", "py.test", "python -m pytest"),
        rewrite_to="pytest",
        filter_fn=pytest_filter,
        category="Tests",
        est_savings=90.0,
    ),
    RtkRule(
        name="cargo-test",
        pattern=_re(r"^cargo\s+test(\s|$)"),
        rewrite_prefixes=("cargo test",),
        rewrite_to="cargo-test",
        filter_fn=cargo_test_filter,
        category="Tests",
        est_savings=90.0,
    ),
    RtkRule(
        name="vitest",
        pattern=_re(r"^((pnpm|npx)\s+)?(vitest|jest)(\s|$)"),
        rewrite_prefixes=("vitest", "jest", "pnpm vitest", "npx vitest", "pnpm jest", "npx jest"),
        rewrite_to="vitest",
        filter_fn=vitest_filter,
        category="Tests",
        est_savings=85.0,
    ),
    # ---------------------------------------------------------- Build/Lint #
    RtkRule(
        name="tsc",
        pattern=_re(r"^((pnpm|npx)\s+)?tsc(\s|$)"),
        rewrite_prefixes=("tsc", "pnpm tsc", "npx tsc"),
        rewrite_to="tsc",
        filter_fn=tsc_filter,
        category="Build",
        est_savings=80.0,
    ),
    RtkRule(
        name="ruff",
        pattern=_re(r"^ruff(\s|$)"),
        rewrite_prefixes=("ruff",),
        rewrite_to="ruff",
        filter_fn=ruff_filter,
        category="Build",
        est_savings=80.0,
    ),
    RtkRule(
        name="eslint",
        pattern=_re(r"^((pnpm|npx)\s+)?eslint(\s|$)"),
        rewrite_prefixes=("eslint", "pnpm eslint", "npx eslint"),
        rewrite_to="eslint",
        filter_fn=eslint_filter,
        category="Build",
        est_savings=80.0,
    ),
    RtkRule(
        name="prettier",
        pattern=_re(r"^((pnpm|npx)\s+)?prettier(\s|$)"),
        rewrite_prefixes=("prettier", "pnpm prettier", "npx prettier"),
        rewrite_to="prettier",
        filter_fn=prettier_filter,
        category="Build",
        est_savings=70.0,
    ),
    # ------------------------------------------------------------ Infra    #
    RtkRule(
        name="docker-ps",
        pattern=_re(r"^docker\s+ps(\s|$)"),
        rewrite_prefixes=("docker ps",),
        rewrite_to="docker-ps",
        filter_fn=docker_ps_filter,
        category="Infra",
        est_savings=80.0,
    ),
    RtkRule(
        name="docker-images",
        pattern=_re(r"^docker\s+images(\s|$)"),
        rewrite_prefixes=("docker images",),
        rewrite_to="docker-images",
        filter_fn=docker_images_filter,
        category="Infra",
        est_savings=80.0,
    ),
    RtkRule(
        name="docker-logs",
        pattern=_re(r"^docker\s+logs(\s|$)"),
        rewrite_prefixes=("docker logs",),
        rewrite_to="docker-logs",
        filter_fn=docker_logs_filter,
        category="Infra",
        est_savings=85.0,
    ),
]


_RULES_BY_NAME = {r.name: r for r in RTK_COMMANDS}


def find_rule(cmd: str, excluded: Iterable[str] = ()) -> RtkRule | None:
    """Return the first rule whose pattern matches ``cmd``.

    ``excluded`` is a list of base commands (e.g. ``["curl", "playwright"]``) the
    user asked us to leave alone.
    """
    stripped = cmd.strip()
    if not stripped:
        return None
    base_token = stripped.split()[0]
    if base_token in set(excluded):
        return None
    for rule in RTK_COMMANDS:
        if rule.pattern.match(stripped):
            return rule
    return None


def list_filters() -> list[dict[str, object]]:
    """Return a JSON-friendly list of rules for ``rtk list``."""
    return [
        {
            "name": r.name,
            "prefixes": list(r.rewrite_prefixes),
            "rewrite_to": r.rewrite_to,
            "category": r.category,
            "est_savings_pct": r.est_savings,
        }
        for r in RTK_COMMANDS
    ]


def dispatch(name: str) -> FilterFn:
    """Resolve a filter by name (``git-status``, ``ls`` …) for direct execution.

    Unknown names fall back to ``passthrough`` so ``rtk <cmd>`` never errors
    out just because we lack a filter.
    """
    rule = _RULES_BY_NAME.get(name)
    if rule is None:
        return passthrough
    return rule.filter_fn


__all__ = ["RtkRule", "RTK_COMMANDS", "find_rule", "list_filters", "dispatch"]
