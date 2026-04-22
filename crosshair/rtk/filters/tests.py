"""Test-runner filters: pytest, cargo test, jest/vitest.

Strategy: run the real command, parse the output, keep only failures + a
one-line pass/fail summary. We always propagate the real exit code so the
agent knows whether CI would go green.
"""

from __future__ import annotations

import re

from crosshair.rtk.filters.base import (
    FilterContext,
    FilterResult,
    run_subprocess,
    truncate_lines,
    which,
)


def pytest_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`pytest` → summary line + per-failure blocks only.

    We inject ``--tb=short -q`` when the user hasn't set a traceback style.
    """
    exe = which("pytest") or which("py.test") or "pytest"

    args = list(argv)
    # Drop the leading command token if present (argv may or may not include it).
    if args and args[0] in ("pytest", "py.test"):
        args = args[1:]
    # Handle `python -m pytest` form — filter runs `pytest` directly.
    has_tb = any(a.startswith("--tb") for a in args)
    has_verbose = any(a in ("-q", "--quiet", "-v", "--verbose", "-vv", "-vvv") for a in args)

    extra: list[str] = []
    if not has_tb:
        extra.append("--tb=short")
    if not has_verbose:
        extra.append("-q")
    # Disable color / stdout capture bells.
    extra.append("--color=no")

    cmd = [exe, *extra, *args]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr

    out = _compact_pytest(raw, ctx.max_lines or 60)
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="pytest",
    )


_PYTEST_SUMMARY = re.compile(r"^=+ (.*?) =+$")
_PYTEST_FAILURE_HEADER = re.compile(r"^_+ (.+) _+$")


def _compact_pytest(raw: str, max_lines: int) -> str:
    lines = raw.splitlines()
    if not lines:
        return "pytest: no output\n"

    summary_line = ""
    short_summary: list[str] = []
    failures: list[list[str]] = []
    current: list[str] | None = None
    in_short_summary = False
    for line in lines:
        m = _PYTEST_SUMMARY.match(line)
        if m:
            label = m.group(1).strip().lower()
            if "short test summary info" in label:
                in_short_summary = True
                current = None
                continue
            if "failures" == label or label.startswith("failures"):
                in_short_summary = False
                current = None
                continue
            if any(tok in label for tok in ("passed", "failed", "error", "warning", "skipped")):
                summary_line = line.strip("= ")
                continue
            in_short_summary = False
            current = None
            continue

        if in_short_summary:
            if line.strip():
                short_summary.append(line)
            continue

        if _PYTEST_FAILURE_HEADER.match(line):
            current = [line]
            failures.append(current)
        elif current is not None:
            current.append(line)

    out_parts: list[str] = []
    if summary_line:
        out_parts.append(summary_line)
    if short_summary:
        out_parts.append("--- short summary ---")
        out_parts.extend(short_summary[:30])
        if len(short_summary) > 30:
            out_parts.append(f"… {len(short_summary) - 30} more")

    if failures:
        out_parts.append("--- failures ---")
        for f in failures[:8]:
            chunk = "\n".join(f[:25])
            out_parts.append(chunk)
            if len(f) > 25:
                out_parts.append(f"  … {len(f) - 25} more line(s) in this failure")
        if len(failures) > 8:
            out_parts.append(f"… {len(failures) - 8} more failure(s) omitted")

    if not out_parts:
        # Fallback: truncated raw output so we never lose information entirely.
        return truncate_lines(raw, max_lines)
    return truncate_lines("\n".join(out_parts), max_lines * 3) + "\n"


def cargo_test_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`cargo test` → failures-only with a one-line summary."""
    exe = which("cargo") or "cargo"
    args = list(argv)
    if args and args[0] == "cargo":
        args = args[1:]
    if args and args[0] == "test":
        args = args[1:]
    cmd = [exe, "test", "--color=never", *args]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr
    out = _compact_cargo_test(raw, ctx.max_lines or 80)
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="cargo_test",
    )


def _compact_cargo_test(raw: str, max_lines: int) -> str:
    lines = raw.splitlines()
    summary = ""
    failures: list[str] = []
    in_failure_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("test result:"):
            summary = stripped
            continue
        if stripped.startswith("failures:"):
            in_failure_block = True
            continue
        if stripped.startswith("test tests::") and "FAILED" in stripped:
            failures.append(stripped)
            continue
        if in_failure_block and stripped:
            failures.append(line)
        else:
            # ignore passing / ok / etc. — these dominate the token count
            pass

    parts: list[str] = []
    if summary:
        parts.append(summary)
    else:
        parts.append("(no summary line)")
    if failures:
        parts.append("--- failures ---")
        parts.extend(failures[:50])
        if len(failures) > 50:
            parts.append(f"… {len(failures) - 50} more line(s)")
    out = "\n".join(parts) + "\n"
    return truncate_lines(out, max_lines * 3)


def vitest_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """vitest/jest → keep FAIL lines and per-test errors, drop passing tests.

    We just run the tool and post-filter its output — parsing vitest JSON
    reporter is fragile across versions and pulling in dependencies is off the
    table.
    """
    base = ctx.base_cmd or "vitest"
    exe = which(base) or which("npx") or base

    # If npm/pnpm wrapping, keep the full argv; else prepend the binary.
    if base in ("vitest", "jest") and exe.endswith("/npx"):
        cmd = [exe, base, *argv]
    else:
        cmd = [exe, *argv]

    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr

    kept: list[str] = []
    summary = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("FAIL ") or " FAIL " in stripped:
            kept.append(line)
        elif stripped.startswith("✓") or stripped.startswith("✔"):
            continue
        elif stripped.startswith("✗") or stripped.startswith("✖"):
            kept.append(line)
        elif "Tests:" in stripped or "Test Files" in stripped:
            summary = stripped
        elif stripped.startswith("Error:") or stripped.startswith("at "):
            kept.append(line)

    parts: list[str] = []
    if summary:
        parts.append(summary)
    if kept:
        parts.append("--- failures ---")
        parts.extend(kept[:80])
        if len(kept) > 80:
            parts.append(f"… {len(kept) - 80} more line(s)")
    else:
        parts.append("all tests passing" if proc.returncode == 0 else "(no failure lines found)")

    out = "\n".join(parts) + "\n"
    out = truncate_lines(out, (ctx.max_lines or 60) * 3)
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="vitest",
    )
