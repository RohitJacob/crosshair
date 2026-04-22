"""Runs a filter from argv, handles tee / passthrough / failure recovery.

The public entry point is ``run_filter(argv, ctx)``. It looks up the registry,
calls the matching filter, records the event, and returns the ``FilterResult``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Sequence

from crosshair.rtk.filters.base import FilterContext, FilterResult, passthrough
from crosshair.rtk.registry import find_rule
from crosshair.rtk.tracking import RtkEvent, record_event
from crosshair.util import now_iso


def run_filter(argv: Sequence[str], ctx: FilterContext | None = None) -> FilterResult:
    """Execute a filter for ``argv`` and return its captured output.

    Behaviour matrix:

    +------------------------------+--------------------------------------+
    | argv matches a rule          | run that filter                      |
    +------------------------------+--------------------------------------+
    | argv does NOT match a rule   | run as passthrough (logs as such)    |
    +------------------------------+--------------------------------------+
    | filter raises an exception   | fall back to passthrough, print warn |
    +------------------------------+--------------------------------------+

    The ``FilterContext`` defaults to the current working directory and env.
    """
    argv = list(argv)
    if not argv:
        return FilterResult(stderr="rtk: no command\n", exit_code=2, passthrough=True)
    ctx = ctx or FilterContext()

    cmdline = " ".join(argv)
    rule = find_rule(cmdline)

    start = time.monotonic()
    if rule is None:
        result = passthrough(argv, ctx)
        filter_name = "passthrough"
        category = "Other"
    else:
        # Remember which binary the user invoked, then strip the matching
        # prefix so the filter sees only the args it needs to care about.
        if ctx.base_cmd is None:
            ctx.base_cmd = argv[0]
        sub_argv = _strip_prefix(argv, rule.rewrite_prefixes)
        try:
            result = rule.filter_fn(sub_argv, ctx)
        except Exception as exc:  # noqa: BLE001 — fail open on any filter bug
            print(f"[rtk] filter {rule.name!r} crashed: {exc}; falling back to passthrough", file=sys.stderr)
            result = passthrough(argv, ctx)
            rule = None
        filter_name = rule.name if rule else "passthrough"
        category = rule.category if rule else "Other"

    elapsed_ms = int((time.monotonic() - start) * 1000)

    record_event(
        RtkEvent(
            ts=now_iso(),
            filter=filter_name,
            cmd=cmdline,
            category=category,
            exit_code=result.exit_code,
            original_chars=result.original_chars,
            filtered_chars=result.filtered_chars,
            elapsed_ms=elapsed_ms,
            passthrough=result.passthrough or rule is None,
        )
    )
    return result


def _strip_prefix(argv: list[str], prefixes: tuple[str, ...]) -> list[str]:
    """Drop the leading tokens that match any of ``prefixes``.

    ``prefixes`` is a tuple of human-readable forms (``"git status"``, ``"pnpm tsc"``).
    We tokenise each and pick the longest that matches the head of ``argv``.
    """
    best_strip = 0
    for p in prefixes:
        tokens = p.split()
        if len(tokens) > len(argv):
            continue
        if argv[: len(tokens)] == tokens and len(tokens) > best_strip:
            best_strip = len(tokens)
    return argv[best_strip:] if best_strip else list(argv)


def execute_and_stream(argv: Sequence[str], ctx: FilterContext | None = None) -> int:
    """Run a filter and stream its output to stdout/stderr. Returns exit code.

    This is the form the ``crosshair rtk <cmd>`` CLI uses — it preserves the
    subprocess's exit code and routes stderr naturally.
    """
    result = run_filter(argv, ctx)
    if result.stdout:
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
    if result.stderr:
        sys.stderr.write(result.stderr)
        sys.stderr.flush()
    return result.exit_code


__all__ = ["run_filter", "execute_and_stream"]
