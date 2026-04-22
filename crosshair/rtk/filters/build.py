"""Build/lint filters: tsc, ruff, eslint, prettier.

All compress by grouping diagnostics by file/rule. When output is already
JSON-structured (ruff, eslint) we read JSON; otherwise we parse line-by-line.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from crosshair.rtk.filters.base import (
    FilterContext,
    FilterResult,
    run_subprocess,
    truncate_lines,
    which,
)


# --------------------------------------------------------------------------- #
# tsc                                                                         #
# --------------------------------------------------------------------------- #

_TSC_LINE = re.compile(
    r"^(?P<path>[^\s(:].*?)[(:](?P<line>\d+)[,:](?P<col>\d+)\)?\s*[-:]?\s*error\s+(?P<code>TS\d+):\s*(?P<msg>.+)$"
)


def tsc_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`tsc` → errors grouped by file. Passing runs just print `ok`."""
    base = ctx.base_cmd or "tsc"
    # When the user wrapped tsc in pnpm/npx we still resolve to the real
    # tsc binary — running pnpm from a hook loses shell context.
    exe = which("tsc") or which(base) or base
    args = list(argv)
    # Drop any wrapper tokens the registry pattern already consumed but which
    # may still sneak in (``pnpm tsc --noEmit`` → ``tsc --noEmit``).
    while args and args[0] in ("pnpm", "npx", "tsc"):
        args = args[1:]

    # Force `--pretty false` for a stable diagnostic format.
    if not any(a == "--pretty" or a.startswith("--pretty=") for a in args):
        args.extend(["--pretty", "false"])
    cmd = [exe, *args]

    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr
    by_file: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    error_count = 0
    for line in raw.splitlines():
        m = _TSC_LINE.match(line.strip())
        if not m:
            continue
        error_count += 1
        by_file[m.group("path")].append((m.group("line"), m.group("code"), m.group("msg")))

    if proc.returncode == 0 and error_count == 0:
        out = "tsc: ok\n"
    else:
        parts = [f"tsc: {error_count} error(s) in {len(by_file)} file(s)"]
        for path, errs in sorted(by_file.items()):
            parts.append(f"{path}: {len(errs)} error(s)")
            for lineno, code, msg in errs[:8]:
                parts.append(f"  {lineno}: {code} {msg[:140]}")
            if len(errs) > 8:
                parts.append(f"  … {len(errs) - 8} more")
        out = "\n".join(parts) + "\n"

    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="tsc",
    )


# --------------------------------------------------------------------------- #
# ruff                                                                        #
# --------------------------------------------------------------------------- #


def ruff_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`ruff check` → JSON output grouped by rule; `ruff format` → just ok/diff."""
    exe = which("ruff") or "ruff"
    args = list(argv)
    # Defensive: argv is already post-prefix, but if somebody calls us with
    # the bare name we still behave correctly.
    if args and args[0] == "ruff":
        args = args[1:]
    sub = args[0] if args else "check"
    rest = args[1:] if args else []

    if sub == "format":
        cmd = [exe, "format", *rest]
        proc = run_subprocess(cmd, ctx)
        raw = proc.stdout + proc.stderr
        summary = ""
        for line in raw.splitlines():
            if "reformatted" in line or "left unchanged" in line:
                summary = line.strip()
                break
        out = (summary or "ruff format: ok") + "\n"
        return FilterResult(
            stdout=out,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(out),
            filter_name="ruff_format",
        )

    # Use JSON output for check.
    cmd = [exe, "check", "--output-format", "json", *rest]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr

    try:
        diagnostics = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        # Fall back to truncated raw output.
        out = truncate_lines(raw, ctx.max_lines or 80)
        return FilterResult(
            stdout=out,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(out),
            filter_name="ruff",
        )

    if not diagnostics:
        out = "ruff check: ok\n"
        return FilterResult(
            stdout=out,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(out),
            filter_name="ruff",
        )

    by_rule: dict[str, list[dict]] = defaultdict(list)
    for d in diagnostics:
        by_rule[d.get("code") or "?"].append(d)

    parts = [f"ruff: {len(diagnostics)} diagnostic(s), {len(by_rule)} rule(s)"]
    for rule, items in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        parts.append(f"{rule}: {len(items)}× — {items[0].get('message', '')[:120]}")
        for d in items[:3]:
            filename = d.get("filename", "?")
            loc = d.get("location", {})
            line = loc.get("row", "?")
            parts.append(f"  {filename}:{line}")
        if len(items) > 3:
            parts.append(f"  … {len(items) - 3} more")
    out = "\n".join(parts) + "\n"
    return FilterResult(
        stdout=out,
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="ruff",
    )


# --------------------------------------------------------------------------- #
# eslint                                                                      #
# --------------------------------------------------------------------------- #


def eslint_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`eslint` → JSON output grouped by rule-id."""
    base = ctx.base_cmd or "eslint"
    exe = which("eslint") or which(base) or base
    args = list(argv)
    while args and args[0] in ("pnpm", "npx", "eslint"):
        args = args[1:]

    cmd = [exe, "--format", "json", *args]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr

    try:
        reports = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        out = truncate_lines(raw, ctx.max_lines or 80)
        return FilterResult(
            stdout=out,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(out),
            filter_name="eslint",
        )

    by_rule: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    total_errors = total_warnings = 0
    for report in reports:
        path = report.get("filePath", "?")
        for msg in report.get("messages", []):
            rule = msg.get("ruleId") or "(parse)"
            severity = msg.get("severity", 0)
            if severity == 2:
                total_errors += 1
            else:
                total_warnings += 1
            by_rule[rule].append((path, msg.get("line", 0), msg.get("message", "")))

    if not by_rule:
        out = "eslint: ok\n"
    else:
        parts = [f"eslint: {total_errors} error(s), {total_warnings} warning(s), {len(by_rule)} rule(s)"]
        for rule, items in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
            parts.append(f"{rule}: {len(items)}×")
            for path, line, msg in items[:3]:
                parts.append(f"  {path}:{line}  {msg[:120]}")
            if len(items) > 3:
                parts.append(f"  … {len(items) - 3} more")
        out = "\n".join(parts) + "\n"

    return FilterResult(
        stdout=out,
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="eslint",
    )


# --------------------------------------------------------------------------- #
# prettier                                                                    #
# --------------------------------------------------------------------------- #


def prettier_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`prettier --check` → just the list of files that need formatting."""
    base = ctx.base_cmd or "prettier"
    exe = which("prettier") or which(base) or base
    args = list(argv)
    while args and args[0] in ("pnpm", "npx", "prettier"):
        args = args[1:]

    cmd = [exe, *args]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr
    unformatted = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("Checking") and not line.startswith("[")
    ]
    # Keep only file paths (skip summary text from prettier).
    files = [l for l in unformatted if "/" in l or l.endswith((".ts", ".js", ".tsx", ".jsx", ".json", ".md"))]
    summary_line = next((l for l in unformatted if l.startswith("Code style issues") or "check:" in l), "")

    parts: list[str] = []
    if summary_line:
        parts.append(summary_line)
    if files:
        parts.append(f"{len(files)} file(s) need formatting")
        parts.extend(files[:30])
        if len(files) > 30:
            parts.append(f"… {len(files) - 30} more")
    elif proc.returncode == 0:
        parts.append("prettier: ok")

    out = "\n".join(parts) + "\n" if parts else "prettier: no output\n"
    return FilterResult(
        stdout=out,
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="prettier",
    )
