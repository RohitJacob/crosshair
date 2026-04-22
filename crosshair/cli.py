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

from crosshair import __version__
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
    "stop": "stop",
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
    hook_cmd = f'{python_exe} -m crosshair hook {{name}}'

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

    merged = _merge_hooks(existing, hook_cmd, python_exe=python_exe, module_root=module_path)

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
) -> dict[str, Any]:
    out: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    out.setdefault("version", 1)
    hooks = dict(out.get("hooks") or {})

    for short, cursor_name in HOOK_EVENT_MAP.items():
        entries: list[dict[str, Any]] = list(hooks.get(cursor_name) or [])
        cmd = hook_cmd_template.format(name=short)
        # Dedupe: remove any previous crosshair entries first so re-running install is idempotent.
        entries = [
            e
            for e in entries
            if not (isinstance(e, dict) and "crosshair" in str(e.get("command", "")))
        ]
        entries.append(
            {
                "command": cmd,
                "timeout": 5,
            }
        )
        hooks[cursor_name] = entries

    out["hooks"] = hooks
    # Stash an env hint so subsequent installs can locate the module root.
    out.setdefault(
        "_crosshair_hint",
        {
            "python": python_exe,
            "module_root": str(module_root),
            "version": __version__,
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


if __name__ == "__main__":
    raise SystemExit(main())
