"""crosshair CLI.

Single entry point for: running as a Cursor hook, installing the hook
configuration, inspecting state, generating handoff summaries, and viewing
analytics over the NDJSON event log.

All subcommands are stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from crosshair import __version__
except ImportError:
    # Defensive fallback: if the ``crosshair`` name resolves as a namespace
    # package (e.g. because Python prepended a cwd like ``~/.cursor`` that
    # contains our install dir onto sys.path), ``__version__`` won't be
    # available. The hook install command now sets ``PYTHONSAFEPATH=1`` to
    # prevent that, but we still want the hook to fail open if an older
    # install missed that flag.
    __version__ = "0.0.0+shadowed"
from crosshair.analytics import render_report
from crosshair.config import Config, default_config_path, load_config, user_config_path
from crosshair.hooks import HANDLERS, get_handler
from crosshair.logs import EventLogger
from crosshair.safepoint.handoff import build_handoff_summary
from crosshair.state import ConversationState, StateStore
from crosshair.util import ensure_dir, expand

HOOK_EVENT_MAP = {
    "session-start": "sessionStart",
    "before-submit": "beforeSubmitPrompt",
    "after-response": "afterAgentResponse",
    "post-tool": "postToolUse",
    "after-file-edit": "afterFileEdit",
    "pre-compact": "preCompact",
    "pre-tool-use": "preToolUse",
    "stop": "stop",
}

# Hooks that need a Cursor matcher. preToolUse only fires for Shell calls in
# our case — both the rtk rewrite and the router-allow live behind that.
HOOK_MATCHERS: dict[str, str] = {
    "pre-tool-use": "Shell",
}

CURSOR_HOOKS_JSON = Path("~/.cursor/hooks.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="crosshair",
        description=(
            "Crosshair: a Cursor hook control layer that routes prompts to "
            "the cheapest-adequate model and detects conversation safepoints."
        ),
    )
    parser.add_argument("--version", action="version", version=f"crosshair {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    hook = sub.add_parser("hook", help="Cursor hook entry (used from hooks.json)")
    hook.add_argument("name", choices=list(HANDLERS.keys()))
    hook.add_argument("--input-file", help="Read stdin from file (debug)", default=None)

    inst = sub.add_parser("install", help="Install Cursor hooks into ~/.cursor/hooks.json")
    inst.add_argument("--python", default=sys.executable, help="Python interpreter to use")
    inst.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    inst.add_argument("--hooks-file", default=str(CURSOR_HOOKS_JSON))
    inst.add_argument(
        "--no-rtk",
        action="store_true",
        help="Install router/safepoint hooks only; skip the rtk preToolUse rewrite hook",
    )

    un = sub.add_parser("uninstall", help="Remove crosshair hooks from Cursor hooks.json")
    un.add_argument("--hooks-file", default=str(CURSOR_HOOKS_JSON))
    un.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="Show per-conversation state summary")
    status = sub.add_parser("show", help="Show full state for a single conversation")
    status.add_argument("conversation_id", help="Conversation ID")

    hand = sub.add_parser("handoff", help="Print the handoff summary for a conversation")
    hand.add_argument("conversation_id", nargs="?", help="Conversation ID (default = most recent)")

    ana = sub.add_parser("analyze", help="Summarise the NDJSON event log")
    ana.add_argument("--days", type=int, default=None, help="Limit to last N days")
    ana.add_argument("--json", action="store_true", help="Emit JSON")

    rst = sub.add_parser("reset", help="Reset state file(s)")
    rst.add_argument("conversation_id", nargs="?", help="ID to reset (default = all)")

    cfg = sub.add_parser("config", help="Print or edit config paths")
    cfg.add_argument("--init", action="store_true", help="Write a user config stub if missing")

    # rtk — we dispatch manually because argparse can't mix subparsers with
    # REMAINDER (we need to accept `crosshair rtk git status -s` verbatim).
    # The same dispatcher is also exposed as the standalone ``rtk`` console
    # script, which is the shorter form the preToolUse rewriter emits.
    rtk = sub.add_parser(
        "rtk",
        help="Token-savings filters for common shell commands (also: `rtk ...`)",
        description=(
            "Run a shell command through an rtk filter, or inspect state.\n"
            "  rtk list           — supported commands\n"
            "  rtk gain           — local savings summary\n"
            "  rtk rewrite <cmd>  — show what the rewriter would do\n"
            "  rtk <cmd> [args]   — run a command through its filter\n"
            "\n"
            "These all also work as `crosshair rtk ...`."
        ),
    )
    rtk.add_argument("rtk_argv", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "hook":
        return _run_hook(args, config)
    if args.command == "install":
        return _cmd_install(args)
    if args.command == "uninstall":
        return _cmd_uninstall(args)
    if args.command == "status":
        return _cmd_status(config)
    if args.command == "show":
        return _cmd_show(config, args.conversation_id)
    if args.command == "handoff":
        return _cmd_handoff(config, args.conversation_id)
    if args.command == "analyze":
        return _cmd_analyze(config, args)
    if args.command == "reset":
        return _cmd_reset(config, args.conversation_id)
    if args.command == "config":
        return _cmd_config(args)
    if args.command == "rtk":
        return _cmd_rtk(args, config)
    parser.print_help()
    return 1


# ---------------------------------------------------------------------------
# Hook runner
# ---------------------------------------------------------------------------


def _run_hook(args: argparse.Namespace, config: Config) -> int:
    """Read JSON from stdin (or --input-file), dispatch, write JSON to stdout.

    Always exits 0 on internal error so Cursor fails open.
    """
    handler = get_handler(args.name)
    if handler is None:
        sys.stdout.write("{}\n")
        return 0

    raw = _read_input(args.input_file)
    try:
        input_data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        input_data = {}

    logger = EventLogger(config)
    store = StateStore(config)

    logger.debug("hook.entry", hook=args.name, keys=list(input_data.keys()))

    try:
        result = handler(input_data, config, logger, store) or {}
    except Exception as exc:  # noqa: BLE001 — never break Cursor
        logger.log(
            "hook_error",
            hook=args.name,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        result = {}

    out = {k: v for k, v in result.items() if not k.startswith("_")}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


def _read_input(input_file: str | None) -> str:
    if input_file:
        try:
            return Path(input_file).read_text(encoding="utf-8")
        except OSError:
            return ""
    try:
        return sys.stdin.read()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


def _cmd_install(args: argparse.Namespace) -> int:
    module_path = Path(__file__).resolve().parent.parent
    python_exe = args.python
    # Cursor runs hooks with ``cwd=~/.cursor``, and the default install dir is
    # ``~/.cursor/crosshair``. Any ``sys.path`` entry that resolves to
    # ``~/.cursor`` turns our install directory into a namespace package and
    # shadows the real editable install, crashing every hook with ``ImportError:
    # cannot import name '…' from 'crosshair' (unknown location)``.
    #
    # Two things can inject ``~/.cursor`` onto ``sys.path``:
    #   1. Python's default behaviour of prepending the cwd ('') — suppressed
    #      by ``PYTHONSAFEPATH=1``.
    #   2. An inherited ``PYTHONPATH`` containing ``.`` (common in dev shells,
    #      e.g. ``PYTHONPATH=.:src/py``) — suppressed by ``env -u PYTHONPATH``.
    #
    # Doing both makes the hook robust regardless of the user's shell env.
    hook_cmd = (
        f"/usr/bin/env -u PYTHONPATH PYTHONSAFEPATH=1 "
        f"{python_exe} -m crosshair hook {{name}}"
    )

    hooks_file = expand(args.hooks_file)
    existing: dict[str, Any] = {}
    if hooks_file.exists():
        try:
            existing = json.loads(hooks_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(
                f"[crosshair] existing {hooks_file} is not valid JSON; refusing to overwrite.",
                file=sys.stderr,
            )
            return 2

    include_rtk = not getattr(args, "no_rtk", False)
    merged = _merge_hooks(
        existing,
        hook_cmd,
        python_exe=python_exe,
        module_root=module_path,
        include_rtk=include_rtk,
    )

    if args.dry_run:
        print(json.dumps(merged, indent=2))
        print(f"\n[crosshair] --dry-run: would write to {hooks_file}", file=sys.stderr)
        return 0

    ensure_dir(hooks_file.parent)
    if hooks_file.exists():
        backup = hooks_file.with_suffix(
            f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        )
        shutil.copy2(hooks_file, backup)
        print(f"[crosshair] backed up existing hooks.json to {backup}", file=sys.stderr)

    hooks_file.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    _ensure_user_dirs()
    print(f"[crosshair] installed hooks into {hooks_file}")
    print(f"[crosshair] module root: {module_path}")
    print("[crosshair] restart Cursor (or start a new composer) for changes to take effect.")
    return 0


def _merge_hooks(
    existing: dict[str, Any],
    hook_cmd_template: str,
    *,
    python_exe: str,
    module_root: Path,
    include_rtk: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    out.setdefault("version", 1)
    hooks = dict(out.get("hooks") or {})

    for short, cursor_name in HOOK_EVENT_MAP.items():
        if short == "pre-tool-use" and not include_rtk:
            # User opted out of rtk — also scrub any previous crosshair entry.
            cleaned = [
                e
                for e in (hooks.get(cursor_name) or [])
                if not (isinstance(e, dict) and "crosshair" in str(e.get("command", "")))
            ]
            if cleaned:
                hooks[cursor_name] = cleaned
            else:
                hooks.pop(cursor_name, None)
            continue

        entries: list[dict[str, Any]] = list(hooks.get(cursor_name) or [])
        cmd = hook_cmd_template.format(name=short)
        # Dedupe: remove any previous crosshair entries first so re-running install is idempotent.
        entries = [
            e
            for e in entries
            if not (isinstance(e, dict) and "crosshair" in str(e.get("command", "")))
        ]
        entry: dict[str, Any] = {"command": cmd, "timeout": 5}
        if short in HOOK_MATCHERS:
            entry["matcher"] = HOOK_MATCHERS[short]
        entries.append(entry)
        hooks[cursor_name] = entries

    out["hooks"] = hooks
    # Stash an env hint so subsequent installs can locate the module root.
    out.setdefault(
        "_crosshair_hint",
        {
            "python": python_exe,
            "module_root": str(module_root),
            "version": __version__,
            "rtk_enabled": include_rtk,
        },
    )
    return out


def _cmd_uninstall(args: argparse.Namespace) -> int:
    hooks_file = expand(args.hooks_file)
    if not hooks_file.exists():
        print(f"[crosshair] no hooks file at {hooks_file}; nothing to do.")
        return 0
    try:
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[crosshair] {hooks_file} is not valid JSON; aborting.", file=sys.stderr)
        return 2

    hooks = dict(data.get("hooks") or {})
    removed = 0
    for cursor_name, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        filtered = [
            e
            for e in entries
            if not (isinstance(e, dict) and "crosshair" in str(e.get("command", "")))
        ]
        removed += len(entries) - len(filtered)
        if filtered:
            hooks[cursor_name] = filtered
        else:
            hooks.pop(cursor_name, None)
    data["hooks"] = hooks
    data.pop("_crosshair_hint", None)

    if args.dry_run:
        print(json.dumps(data, indent=2))
        return 0

    hooks_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[crosshair] removed {removed} hook entries.")
    return 0


def _ensure_user_dirs() -> None:
    for p in [
        "~/.cursor/crosshair",
        "~/.cursor/crosshair/state",
        "~/.cursor/crosshair/logs",
    ]:
        ensure_dir(expand(p))


# ---------------------------------------------------------------------------
# status / show / handoff / analyze / reset / config
# ---------------------------------------------------------------------------


def _cmd_status(config: Config) -> int:
    store = StateStore(config)
    convs = store.list_conversations()
    if not convs:
        print("[crosshair] no tracked conversations yet.")
        return 0
    print(f"{'Updated':<20} {'Conv ID':<36} {'Turns':>6} {'Tokens':>10} {'Lvl':>3}")
    for c in convs[:20]:
        ts = c.updated_ts.replace("T", " ")[:19]
        turns = int(c.metrics.get("user_turns", 0))
        toks = int(c.metrics.get("estimated_tokens", 0))
        print(f"{ts:<20} {c.conversation_id:<36} {turns:>6} {toks:>10,} {c.safepoint_level_last:>3}")
    if len(convs) > 20:
        print(f"... and {len(convs) - 20} older")
    return 0


def _cmd_show(config: Config, conversation_id: str) -> int:
    store = StateStore(config)
    state = store.load(conversation_id)
    print(json.dumps(state.to_json(), indent=2, ensure_ascii=False))
    return 0


def _cmd_handoff(config: Config, conversation_id: str | None) -> int:
    store = StateStore(config)
    if not conversation_id:
        convs = store.list_conversations()
        if not convs:
            print("[crosshair] no tracked conversations.", file=sys.stderr)
            return 1
        state = convs[0]
    else:
        state = store.load(conversation_id)
    summary = build_handoff_summary(state, config.safepoint)
    print(summary)
    return 0


def _cmd_analyze(config: Config, args: argparse.Namespace) -> int:
    path = expand(config.logging.get("path", "~/.cursor/crosshair/logs/events.ndjson"))
    report = render_report(path, days=args.days)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
    return 0


def _print_report(report: dict[str, Any]) -> None:
    print("=" * 60)
    print("  CROSSHAIR ANALYTICS")
    total = report["summary"]["total_events"]
    print(f"  {total:,} events")
    print("=" * 60)
    print()
    print("  Router actions:")
    for action, count in report["router"]["actions"].items():
        pct = 100.0 * count / max(report["router"]["total"], 1)
        print(f"    {action:<18} {count:>6} ({pct:5.1f}%)")
    print()
    print("  Model usage (user turns):")
    for model, count in report["router"]["by_model"].items():
        print(f"    {model:<40} {count:>6}")
    print()
    print("  Safepoint advisories:")
    for level, count in report["safepoint"]["by_level"].items():
        print(f"    {level:<10} {count:>6}")
    if report["safepoint"]["top_signals"]:
        print("    top signals:")
        for name, count in report["safepoint"]["top_signals"][:6]:
            print(f"      - {name:<22} {count:>5}")
    print()
    print("  Tool outcomes:")
    print(f"    calls:    {report['tools']['calls']:>6}")
    print(f"    failures: {report['tools']['failures']:>6}")
    print()
    rtk = report.get("rtk", {}) or {}
    print("  rtk rewrites:")
    print(f"    rewrites:   {rtk.get('rewrites', 0):>6}")
    print(f"    passthrough:{rtk.get('passthrough', 0):>6}")
    print(f"    filter runs:{rtk.get('runs', 0):>6}")
    print(f"    saved tokens (est): {rtk.get('saved_tokens', 0):>9,}  ({rtk.get('savings_pct', 0):.1f}%)")
    print()
    print("  Tokens observed (estimate):")
    print(f"    prompts + responses + tool outputs ≈ {report['tokens']['estimated']:>10,}")
    print("=" * 60)


def _cmd_reset(config: Config, conversation_id: str | None) -> int:
    store = StateStore(config)
    if conversation_id:
        ok = store.reset(conversation_id)
        print(f"[crosshair] {'reset' if ok else 'not found'}: {conversation_id}")
    else:
        count = store.reset_all()
        print(f"[crosshair] cleared {count} state file(s).")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    user_path = user_config_path()
    default_path = default_config_path()
    print(f"default config: {default_path}")
    print(f"user config:    {user_path} {'(exists)' if user_path.exists() else '(missing)'}")
    if args.init and not user_path.exists():
        ensure_dir(user_path.parent)
        user_path.write_text(
            json.dumps(
                {
                    "safepoint": {
                        "action_thresholds": {"note": 50, "suggest": 70, "strong": 90}
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[crosshair] wrote starter user config to {user_path}")
    return 0


# ---------------------------------------------------------------------------
# rtk — run filters, show list, show savings
# ---------------------------------------------------------------------------


def _cmd_rtk(args: argparse.Namespace, config: Config) -> int:
    raw_argv = list(getattr(args, "rtk_argv", []) or [])
    return _dispatch_rtk(raw_argv, config, prog="crosshair rtk")


def rtk_main(argv: list[str] | None = None) -> int:
    """Entry point for the standalone ``rtk`` console script.

    Behaves exactly like ``crosshair rtk <argv>`` but is the shorter form the
    ``preToolUse`` rewriter produces so every rewritten shell command costs
    fewer tokens in the agent context.
    """
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    config = load_config()
    return _dispatch_rtk(raw_argv, config, prog="rtk")


def _dispatch_rtk(raw_argv: list[str], config: Config, *, prog: str) -> int:
    if not raw_argv:
        print(
            f"usage: {prog} <cmd> [args...] | list | gain | rewrite <cmd...>",
            file=sys.stderr,
        )
        return 2

    first = raw_argv[0]
    if first == "list":
        return _rtk_list()
    if first == "gain":
        return _rtk_gain()
    if first == "rewrite":
        return _rtk_rewrite(raw_argv[1:], prog=prog)

    from crosshair.rtk.filters.base import FilterContext
    from crosshair.rtk.runner import execute_and_stream

    ctx = FilterContext(max_lines=config.rtk.get("max_lines_default"))
    return execute_and_stream(raw_argv, ctx)


def _rtk_list() -> int:
    from crosshair.rtk.registry import list_filters
    from crosshair.rtk.rewrite import REWRITE_CMD

    by_cat: dict[str, list[dict[str, Any]]] = {}
    for item in list_filters():
        by_cat.setdefault(str(item["category"]), []).append(item)
    print(f"{'Command':<24} {'Rewrite target':<20} {'Category':<10} {'Est. savings':>12}")
    for category in sorted(by_cat):
        for item in by_cat[category]:
            prefix_display = ", ".join(item["prefixes"])
            rewrite_to = f"{REWRITE_CMD} {item['rewrite_to']}"
            print(
                f"{prefix_display:<24} {rewrite_to:<20} {item['category']:<10} "
                f"{item['est_savings_pct']:>10.0f}%"
            )
    return 0


def _rtk_gain() -> int:
    from crosshair.rtk.tracking import iter_events, summarise

    summary = summarise(iter_events())
    totals = summary["totals"]
    print("=" * 60)
    print("  CROSSHAIR RTK — local savings")
    print("=" * 60)
    print(f"  runs            : {totals['runs']}")
    print(f"  passthrough     : {totals['passthrough_runs']}")
    print(f"  original tokens : {totals['original_tokens']:>10,}")
    print(f"  filtered tokens : {totals['filtered_tokens']:>10,}")
    print(f"  saved tokens    : {totals['saved_tokens']:>10,}  ({totals['savings_pct']:.1f}%)")
    print("-" * 60)
    for row in summary["by_filter"]:
        print(
            f"  {row['filter']:<18} {row['runs']:>5} runs  "
            f"{row['saved_tokens']:>9,} tok saved  {row['savings_pct']:>5.1f}%"
        )
    return 0


def _rtk_rewrite(command_tokens: list[str], *, prog: str = "rtk") -> int:
    from crosshair.rtk.rewrite import rewrite_command

    if not command_tokens:
        print(f"usage: {prog} rewrite <command...>", file=sys.stderr)
        return 2
    cmd = " ".join(command_tokens)
    rewritten = rewrite_command(cmd)
    if rewritten is None:
        print(f"(no rewrite) {cmd}")
    else:
        print(rewritten)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
