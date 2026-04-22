"""File-system filters: ls, read, grep, find, tree.

The goal is to keep the structural information an LLM needs (names, counts,
line numbers, matches) while dropping noise like timestamps, permissions, and
repeated surrounding context.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from crosshair.rtk.filters.base import (
    FilterContext,
    FilterResult,
    passthrough,
    run_subprocess,
    truncate_lines,
    which,
)


# --------------------------------------------------------------------------- #
# ls                                                                          #
# --------------------------------------------------------------------------- #


def ls_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """Replace `ls [-la]` with a directory summary: ``N files, M dirs`` plus
    a short listing grouped by type. Keeps argv's target dirs.
    """
    # Extract target paths (non-flag args). Default to current dir.
    targets: list[str] = [a for a in argv if not a.startswith("-")] or ["."]
    show_hidden = any(flag in argv for flag in ("-a", "-A", "--all", "-la", "-al"))

    out_parts: list[str] = []
    original_total = 0

    for target in targets:
        tpath = (ctx.cwd / target).expanduser()
        if not tpath.exists():
            out_parts.append(f"{target}: not found")
            continue
        if tpath.is_file():
            out_parts.append(f"{target} (file, {_human_size(tpath.stat().st_size)})")
            original_total += 200  # rough ls -la line weight
            continue

        try:
            entries = sorted(tpath.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as e:
            out_parts.append(f"{target}: {e}")
            continue

        if not show_hidden:
            entries = [p for p in entries if not p.name.startswith(".")]

        dirs = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        original_total += len(entries) * 80  # rough ls -la line weight

        header = f"{target}/" if len(targets) > 1 else ""
        if header:
            out_parts.append(header)
        out_parts.append(f"{len(dirs)} dir(s), {len(files)} file(s)")

        # Show up to 20 dirs and 30 files; collapse the rest with counts.
        for d in dirs[:20]:
            try:
                count = sum(1 for _ in d.iterdir())
            except OSError:
                count = 0
            out_parts.append(f"  {d.name}/ ({count})")
        if len(dirs) > 20:
            out_parts.append(f"  … {len(dirs) - 20} more dir(s)")

        # Group files by extension when there are many.
        if len(files) > 30:
            by_ext: dict[str, int] = defaultdict(int)
            for f in files:
                ext = f.suffix or "<no-ext>"
                by_ext[ext] += 1
            top = ", ".join(
                f"{count} {ext}" for ext, count in sorted(by_ext.items(), key=lambda kv: -kv[1])[:6]
            )
            out_parts.append(f"  files by type: {top}")
            for f in files[:15]:
                out_parts.append(f"  {f.name} ({_human_size(f.stat().st_size)})")
            out_parts.append(f"  … {len(files) - 15} more file(s)")
        else:
            for f in files:
                try:
                    out_parts.append(f"  {f.name} ({_human_size(f.stat().st_size)})")
                except OSError:
                    out_parts.append(f"  {f.name}")

    text = "\n".join(out_parts) + "\n"
    # Approximate the weight of a real `ls -la`.
    original_chars = max(original_total, len(text))
    return FilterResult(
        stdout=text,
        exit_code=0,
        original_chars=original_chars,
        filtered_chars=len(text),
        filter_name="ls",
    )


def _human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}T"


# --------------------------------------------------------------------------- #
# read / cat / head / tail                                                    #
# --------------------------------------------------------------------------- #


def read_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """Replacement for ``cat``/``head``/``tail``: print file(s) with sane caps.

    ``argv`` contains tokens **after** the base command (``cat``/``head``/``tail``);
    we infer tail mode from ``ctx.base_cmd``. Default shows the first 200 lines
    and respects ``-n N``.
    """
    base = ctx.base_cmd or "cat"
    tail_mode = base == "tail"
    max_lines = ctx.max_lines or 200
    line_mode = False  # whether to number lines

    i = 0
    files: list[str] = []
    while i < len(argv):
        a = argv[i]
        if a in ("-n", "--lines"):
            try:
                max_lines = int(argv[i + 1])
                i += 2
                continue
            except (IndexError, ValueError):
                pass
        if a.startswith("-n"):
            try:
                max_lines = int(a[2:])
                i += 1
                continue
            except ValueError:
                pass
        if a == "--number":
            line_mode = True
        if a == "-f":  # tail -f streaming — never safe to summarise
            return passthrough([base, *argv], ctx)
        if a.startswith("-"):
            i += 1
            continue
        files.append(a)
        i += 1

    if not files:
        # cat with no args reads stdin; passthrough is safest.
        return passthrough([base, *argv], ctx)

    out_chunks: list[str] = []
    original_chars = 0
    for fname in files:
        fpath = (ctx.cwd / fname).expanduser()
        if not fpath.exists():
            out_chunks.append(f"{fname}: not found")
            continue
        try:
            text = fpath.read_text(errors="replace")
        except OSError as e:
            out_chunks.append(f"{fname}: {e}")
            continue

        original_chars += len(text)
        lines = text.splitlines()
        total = len(lines)

        if tail_mode:
            shown = lines[-max_lines:]
            kept = shown
            omitted = max(0, total - len(kept))
            if omitted:
                kept = [f"… {omitted} earlier line(s) omitted"] + kept
        else:
            if total <= max_lines:
                kept = lines
            else:
                kept = lines[:max_lines]
                kept.append(f"… {total - max_lines} more line(s)")

        if len(files) > 1:
            out_chunks.append(f"==> {fname} ({total} lines) <==")
        if line_mode:
            numbered: list[str] = []
            for idx, ln in enumerate(kept, 1):
                if ln.startswith("…"):
                    numbered.append(ln)
                else:
                    numbered.append(f"{idx:>6}  {ln}")
            out_chunks.append("\n".join(numbered))
        else:
            out_chunks.append("\n".join(kept))

    text = "\n".join(out_chunks) + "\n"
    return FilterResult(
        stdout=text,
        exit_code=0,
        original_chars=max(original_chars, len(text)),
        filtered_chars=len(text),
        filter_name="read",
    )


# --------------------------------------------------------------------------- #
# grep / rg                                                                   #
# --------------------------------------------------------------------------- #


def grep_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """Run the real grep/rg and group results by file.

    ``argv`` contains the tokens **after** the ``grep``/``rg`` base command,
    i.e. only flags + pattern + paths. We pick the binary from ``ctx.base_cmd``.

    Output shape:
        path/to/file.py: 5 match(es)
          12:  def foo():
          47:      if foo():
          …
    """
    base = ctx.base_cmd or "grep"
    exe = which(base if base in ("rg", "grep") else "grep") or "grep"
    # Force line numbers and filenames for a stable format. ripgrep already
    # defaults to line numbers + filenames; grep does not.
    extra: list[str] = []
    if base == "grep":
        if not any(a in ("-n", "--line-number") for a in argv):
            extra.append("-n")
        if not any(a in ("-H", "--with-filename") for a in argv):
            extra.append("-H")
    cmd = [exe, *extra, *argv]

    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout
    if proc.returncode not in (0, 1):  # 1 = no matches for grep, not an error
        return FilterResult(
            stdout=raw,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(raw),
            filter_name="grep",
        )

    by_file: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for line in raw.splitlines():
        # Expected formats:
        #   path:LINENO:content   (grep -Hn, rg)
        #   path:content          (grep -H without -n)
        # Binary-match format "Binary file X matches" is kept as-is.
        if line.startswith("Binary file "):
            by_file[line[len("Binary file ") : -len(" matches")]].append(("-", line))
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            if len(parts) == 2:
                path, content = parts
                by_file[path].append(("-", content))
            continue
        path, lineno, content = parts
        by_file[path].append((lineno, content))

    out_parts: list[str] = []
    max_per_file = ctx.max_lines or 5
    total_matches = 0
    for path, entries in sorted(by_file.items()):
        total_matches += len(entries)
        out_parts.append(f"{path}: {len(entries)} match(es)")
        for lineno, content in entries[:max_per_file]:
            trimmed = content.strip()
            if len(trimmed) > 120:
                trimmed = trimmed[:117] + "…"
            out_parts.append(f"  {lineno}:  {trimmed}")
        if len(entries) > max_per_file:
            out_parts.append(f"  … {len(entries) - max_per_file} more match(es)")

    if not out_parts:
        out_parts.append("(no matches)")
    else:
        out_parts.insert(0, f"{total_matches} match(es) in {len(by_file)} file(s)")

    out = "\n".join(out_parts) + "\n"
    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=0 if total_matches else 1,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="grep",
    )


# --------------------------------------------------------------------------- #
# find                                                                        #
# --------------------------------------------------------------------------- #


def find_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """Run real ``find`` and summarise by extension + show first 30 hits."""
    exe = which("find") or "find"
    proc = run_subprocess([exe, *argv], ctx)
    raw = proc.stdout

    if proc.returncode != 0 and not raw:
        return FilterResult(
            stdout=raw,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(raw),
            filter_name="find",
        )

    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        out = "(no matches)\n"
        return FilterResult(
            stdout=out,
            exit_code=proc.returncode,
            original_chars=len(raw),
            filtered_chars=len(out),
            filter_name="find",
        )

    by_ext: dict[str, int] = defaultdict(int)
    for line in lines:
        ext = Path(line).suffix or "<no-ext>"
        by_ext[ext] += 1

    out_parts = [f"{len(lines)} result(s)"]
    top = ", ".join(
        f"{count} {ext}" for ext, count in sorted(by_ext.items(), key=lambda kv: -kv[1])[:6]
    )
    out_parts.append(f"by type: {top}")
    for line in lines[:30]:
        out_parts.append(line)
    if len(lines) > 30:
        out_parts.append(f"… {len(lines) - 30} more")
    out = "\n".join(out_parts) + "\n"

    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="find",
    )


# --------------------------------------------------------------------------- #
# tree                                                                        #
# --------------------------------------------------------------------------- #


def tree_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """Simplified ``tree``: max depth 3, ignores hidden + common heavy dirs."""
    max_depth = 3
    i = 0
    target = "."
    while i < len(argv):
        a = argv[i]
        if a == "-L" and i + 1 < len(argv):
            try:
                max_depth = int(argv[i + 1])
                i += 2
                continue
            except ValueError:
                pass
        if not a.startswith("-"):
            target = a
        i += 1

    tpath = (ctx.cwd / target).expanduser()
    if not tpath.exists() or not tpath.is_dir():
        return FilterResult(
            stdout=f"{target}: not a directory\n",
            exit_code=1,
            original_chars=50,
            filtered_chars=50,
            filter_name="tree",
        )

    ignore = {
        "node_modules", ".git", ".venv", "venv", "target", "dist", "build",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next",
    }
    lines: list[str] = [f"{target}/"]
    file_count = 0
    dir_count = 0

    def walk(p: Path, depth: int, prefix: str) -> None:
        nonlocal file_count, dir_count
        if depth > max_depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        entries = [e for e in entries if not e.name.startswith(".") and e.name not in ignore]
        for idx, e in enumerate(entries[:40]):
            is_last = idx == min(len(entries), 40) - 1
            connector = "└── " if is_last else "├── "
            if e.is_dir():
                try:
                    inner = sum(1 for _ in e.iterdir())
                except OSError:
                    inner = 0
                lines.append(f"{prefix}{connector}{e.name}/ ({inner})")
                dir_count += 1
                new_prefix = prefix + ("    " if is_last else "│   ")
                walk(e, depth + 1, new_prefix)
            else:
                lines.append(f"{prefix}{connector}{e.name}")
                file_count += 1
        if len(entries) > 40:
            lines.append(f"{prefix}… {len(entries) - 40} more entries")

    walk(tpath, 1, "")
    lines.append(f"\n{dir_count} directories, {file_count} files")
    out = "\n".join(lines) + "\n"
    # A real tree -L 3 of a medium repo emits ~200 lines; approximate.
    approx_original = 200 * 80
    return FilterResult(
        stdout=out,
        exit_code=0,
        original_chars=max(approx_original, len(out)),
        filtered_chars=len(out),
        filter_name="tree",
    )
