"""Shared types and helpers for filters.

A ``Filter`` is just a callable ``(argv, ctx) -> FilterResult``. Kept tiny on
purpose — the registry maps command patterns to these callables and the runner
invokes them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence


@dataclass
class FilterContext:
    """Environment a filter runs in. Held separately from argv so tests can
    construct one without spawning a real shell."""

    cwd: Path = field(default_factory=Path.cwd)
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    verbose: int = 0
    max_lines: int | None = None
    tee_dir: Path | None = None
    # The original command binary (e.g. ``"grep"``, ``"rg"``, ``"cat"``). Set
    # by the runner *before* stripping the matched prefix from argv so filters
    # can branch on which tool the user actually asked for.
    base_cmd: str | None = None


@dataclass
class FilterResult:
    """The stdout/stderr/exit_code a filter wants to emit, plus byte stats.

    ``original_chars`` / ``filtered_chars`` feed the savings tracker.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    original_chars: int = 0
    filtered_chars: int = 0
    filter_name: str = ""
    passthrough: bool = False

    @property
    def savings_ratio(self) -> float:
        if self.original_chars <= 0:
            return 0.0
        return max(0.0, 1.0 - self.filtered_chars / self.original_chars)


FilterFn = Callable[[list[str], FilterContext], FilterResult]


def run_subprocess(
    argv: Sequence[str], ctx: FilterContext, *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Minimal subprocess wrapper that captures text stdout/stderr."""
    return subprocess.run(
        list(argv),
        cwd=str(ctx.cwd),
        env=ctx.env,
        capture_output=True,
        text=True,
        check=check,
    )


def which(name: str) -> str | None:
    """Resolve a command from PATH; returns None if not found.

    We always use the full path when running subcommands so hooks called from
    GUI-launched Cursor (where Homebrew paths are missing) still work.
    """
    found = shutil.which(name)
    if found:
        return found
    # Cursor GUI often loses /opt/homebrew/bin and /usr/local/bin.
    for candidate in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
        p = Path(candidate) / name
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return None


def passthrough(argv: list[str], ctx: FilterContext) -> FilterResult:
    """Execute the command as-is and return its output unchanged.

    Used both as a fall-back when a filter crashes and as the default for
    commands we haven't specially optimised.
    """
    if not argv:
        return FilterResult(stderr="rtk: empty argv\n", exit_code=2, passthrough=True)

    exe = which(argv[0])
    if not exe:
        return FilterResult(
            stderr=f"rtk: command not found: {argv[0]}\n",
            exit_code=127,
            passthrough=True,
        )
    try:
        proc = run_subprocess([exe, *argv[1:]], ctx)
    except FileNotFoundError:
        return FilterResult(
            stderr=f"rtk: command not found: {argv[0]}\n",
            exit_code=127,
            passthrough=True,
        )
    return FilterResult(
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(proc.stdout),
        filtered_chars=len(proc.stdout),
        passthrough=True,
    )


def truncate_lines(text: str, max_lines: int, marker_fmt: str = "… {n} more line(s) omitted") -> str:
    """Truncate to N lines with a small ``… N more …`` marker.

    Useful when a filter still wants to show raw output but cap it.
    """
    if max_lines <= 0:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = lines[:max_lines]
    remaining = len(lines) - max_lines
    kept.append(marker_fmt.format(n=remaining))
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def count_tokens(text: str) -> int:
    """Same ~4 chars/token estimate used elsewhere in crosshair."""
    if not text:
        return 0
    return max(1, len(text) // 4)
