"""Git filters.

Pattern follows the Rust rtk: run git with a minimal output format and
compress further when safe. For trivial write commands (``add``, ``commit``,
``push``, ``pull``, ``fetch``) we return ``ok`` + a one-line summary — Claude
never needs to see the 15 lines of progress output.
"""

from __future__ import annotations

import re

from crosshair.rtk.filters.base import (
    FilterContext,
    FilterResult,
    passthrough,
    run_subprocess,
    truncate_lines,
    which,
)


def _git(argv: list[str], ctx: FilterContext, default_cmd: str | None = None):
    """Run git with the resolved binary and given argv. Returns subprocess result."""
    exe = which("git") or "git"
    cmd = [exe]
    if default_cmd:
        cmd.append(default_cmd)
    cmd.extend(argv)
    return run_subprocess(cmd, ctx)


def git_status(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git status` → branch + counts by state. Down from ~30 lines to ~5."""
    # Native porcelain v2 output is already compact and stable.
    proc = _git(["-b", "--porcelain=v1", *argv], ctx, default_cmd="status")
    if proc.returncode != 0:
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(proc.stdout),
            filtered_chars=len(proc.stdout),
            filter_name="git_status",
        )

    branch = ""
    counts: dict[str, int] = {}
    files: list[tuple[str, str]] = []
    for raw_line in proc.stdout.splitlines():
        if raw_line.startswith("##"):
            branch = raw_line[2:].strip()
            continue
        if len(raw_line) < 3:
            continue
        state = raw_line[:2].strip() or "?"
        path = raw_line[3:]
        counts[state] = counts.get(state, 0) + 1
        files.append((state, path))

    if not files:
        out = f"branch: {branch}\nclean\n"
    else:
        parts = [f"branch: {branch}"] if branch else []
        state_labels = {
            "M": "modified",
            "MM": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "U": "unmerged",
            "??": "untracked",
            "!!": "ignored",
            "AM": "added+modified",
        }
        summary = ", ".join(
            f"{count} {state_labels.get(state, state)}"
            for state, count in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        parts.append(summary)
        # Show up to 30 file lines — enough for most edits without blowing tokens.
        for state, path in files[:30]:
            parts.append(f"  {state:<2} {path}")
        if len(files) > 30:
            parts.append(f"  … {len(files) - 30} more file(s)")
        out = "\n".join(parts) + "\n"

    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(proc.stdout),
        filtered_chars=len(out),
        filter_name="git_status",
    )


def git_log(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git log` → `%h %s (%an)` oneline format, default -n 20."""
    user_wants_format = any(
        a.startswith("--format") or a.startswith("--pretty") or a == "--oneline" for a in argv
    )
    user_caps_count = any(a.startswith("-n") or a == "--max-count" or a.startswith("--max-count=") for a in argv)

    cmd_args: list[str] = []
    if not user_wants_format:
        cmd_args += ["--pretty=format:%h %s (%an, %ar)"]
    if not user_caps_count:
        cmd_args += ["-n", "20"]
    cmd_args.extend(argv)

    proc = _git(cmd_args, ctx, default_cmd="log")
    if proc.returncode != 0:
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(proc.stdout),
            filtered_chars=len(proc.stdout),
            filter_name="git_log",
        )

    out = proc.stdout
    max_lines = ctx.max_lines or 40
    compact = truncate_lines(out, max_lines)
    return FilterResult(
        stdout=compact,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(out),
        filtered_chars=len(compact),
        filter_name="git_log",
    )


_DIFF_HUNK_HEADER = re.compile(r"^@@ .* @@")
_DIFF_FILE_HEADER = re.compile(r"^diff --git ")


def git_diff(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git diff` → `--stat` summary, then truncated hunks (+/- lines only)."""
    if any(a in ("--stat", "--numstat", "--shortstat") for a in argv):
        return passthrough(["git", "diff", *argv], ctx)

    # Run stat first for the compact summary.
    stat_proc = _git(["--stat", *argv], ctx, default_cmd="diff")
    if stat_proc.returncode != 0:
        return FilterResult(
            stdout=stat_proc.stdout,
            stderr=stat_proc.stderr,
            exit_code=stat_proc.returncode,
            original_chars=len(stat_proc.stdout),
            filtered_chars=len(stat_proc.stdout),
            filter_name="git_diff",
        )

    diff_proc = _git(list(argv), ctx, default_cmd="diff")
    raw = diff_proc.stdout
    compact = _compact_diff(raw, ctx.max_lines or 400)

    out_parts = [stat_proc.stdout.rstrip()]
    if compact.strip():
        out_parts.append("--- Changes ---")
        out_parts.append(compact)
    out = "\n".join(out_parts) + ("\n" if out_parts else "")

    return FilterResult(
        stdout=out,
        stderr=diff_proc.stderr,
        exit_code=diff_proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="git_diff",
    )


def _compact_diff(raw: str, max_lines: int) -> str:
    """Keep file headers, hunk headers, and +/- lines. Drop context lines."""
    kept: list[str] = []
    dropped_context = 0
    for line in raw.splitlines():
        if not line:
            continue
        if _DIFF_FILE_HEADER.match(line):
            kept.append(line)
        elif line.startswith("+++") or line.startswith("---"):
            kept.append(line)
        elif _DIFF_HUNK_HEADER.match(line):
            kept.append(line)
        elif line.startswith("+") or line.startswith("-"):
            kept.append(line)
        else:
            dropped_context += 1
    if dropped_context:
        kept.append(f"… {dropped_context} context line(s) omitted")
    return truncate_lines("\n".join(kept), max_lines)


def git_add(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git add` → `ok` when it succeeds; original error otherwise."""
    proc = _git(list(argv), ctx, default_cmd="add")
    if proc.returncode == 0:
        return FilterResult(
            stdout="ok\n",
            stderr=proc.stderr,
            exit_code=0,
            original_chars=len(proc.stdout),
            filtered_chars=3,
            filter_name="git_add",
        )
    return FilterResult(
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(proc.stdout),
        filtered_chars=len(proc.stdout),
        filter_name="git_add",
    )


def git_commit(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git commit` → `ok <short-sha>`."""
    proc = _git(list(argv), ctx, default_cmd="commit")
    if proc.returncode != 0:
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(proc.stdout),
            filtered_chars=len(proc.stdout),
            filter_name="git_commit",
        )
    # git prints to stdout: "[branch abcd123] message"
    sha_match = re.search(r"\b([0-9a-f]{7,12})\b", proc.stdout)
    sha = sha_match.group(1) if sha_match else "ok"
    out = f"ok {sha}\n"
    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=0,
        original_chars=len(proc.stdout),
        filtered_chars=len(out),
        filter_name="git_commit",
    )


def git_push(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git push` → `ok <branch>`; push progress goes to stderr and we drop it."""
    proc = _git(list(argv), ctx, default_cmd="push")
    if proc.returncode != 0:
        # Keep the real error text — we still want to see why it failed.
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(proc.stdout) + len(proc.stderr),
            filtered_chars=len(proc.stdout) + len(proc.stderr),
            filter_name="git_push",
        )
    branch = ""
    branch_proc = _git(["--show-current"], ctx, default_cmd="branch")
    if branch_proc.returncode == 0:
        branch = branch_proc.stdout.strip()
    out = f"ok {branch}\n" if branch else "ok\n"
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=0,
        original_chars=len(proc.stdout) + len(proc.stderr),
        filtered_chars=len(out),
        filter_name="git_push",
    )


def git_pull(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git pull` → `ok N files +X -Y`."""
    proc = _git(list(argv), ctx, default_cmd="pull")
    raw = proc.stdout + proc.stderr
    if proc.returncode != 0:
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(raw),
            filter_name="git_pull",
        )
    files_changed = 0
    added = deleted = 0
    m = re.search(r"(\d+)\s+files?\s+changed", raw)
    if m:
        files_changed = int(m.group(1))
    m = re.search(r"(\d+)\s+insertions?\(\+\)", raw)
    if m:
        added = int(m.group(1))
    m = re.search(r"(\d+)\s+deletions?\(-\)", raw)
    if m:
        deleted = int(m.group(1))
    if files_changed or added or deleted:
        out = f"ok {files_changed} files +{added} -{deleted}\n"
    elif "Already up to date" in raw:
        out = "ok up-to-date\n"
    else:
        out = "ok\n"
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=0,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="git_pull",
    )


def git_branch(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git branch` → current branch first + count for the rest."""
    proc = _git(list(argv), ctx, default_cmd="branch")
    if proc.returncode != 0 or not proc.stdout.strip():
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(proc.stdout),
            filtered_chars=len(proc.stdout),
            filter_name="git_branch",
        )
    current = ""
    others: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if line.startswith("*"):
            current = line[1:].strip()
        elif line.strip():
            others.append(line.strip())
    parts = [f"* {current}"] if current else []
    if others:
        visible = others[:10]
        parts.extend(f"  {b}" for b in visible)
        if len(others) > 10:
            parts.append(f"  … {len(others) - 10} more branch(es)")
    else:
        parts.append("  (no other branches)")
    out = "\n".join(parts) + "\n"
    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(proc.stdout),
        filtered_chars=len(out),
        filter_name="git_branch",
    )


def git_fetch(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`git fetch` → `ok` or `ok <updates>`; fetch progress is always on stderr."""
    proc = _git(list(argv), ctx, default_cmd="fetch")
    if proc.returncode != 0:
        return FilterResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(proc.stdout) + len(proc.stderr),
            filtered_chars=len(proc.stdout) + len(proc.stderr),
            filter_name="git_fetch",
        )
    updates = [l for l in proc.stderr.splitlines() if "->" in l]
    out = "ok\n" if not updates else f"ok {len(updates)} update(s)\n"
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=0,
        original_chars=len(proc.stdout) + len(proc.stderr),
        filtered_chars=len(out),
        filter_name="git_fetch",
    )
