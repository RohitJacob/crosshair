"""Command-string → rewritten-string logic used by the ``preToolUse`` hook.

The rewriter is a pure function: given a shell command line and a list of
excluded base commands, it returns the rewritten form (or ``None`` if nothing
changed). It has no side effects so it can be unit-tested in isolation.

Supported shapes:

- Single commands: ``git status`` → ``crosshair rtk git status``
- Compound with ``&&``/``||``/``;``: each segment rewritten independently
- Pipes: only the producer (first segment) is rewritten — filters consume
  full stdout
- Backgrounding (`` & ``): same as sequential operators
- Heredocs (``<<``), arithmetic expansion (``$((``): passthrough (too unsafe
  to tokenize reliably with a tiny lexer)
- Env prefixes (``sudo``, ``VAR=val``): preserved; rewrite operates on the
  command after the prefix
- Already-rewritten commands (``rtk ...`` / ``crosshair rtk ...``): passthrough
- ``RTK_DISABLED=1`` env prefix: explicit opt-out
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

from crosshair.rtk.registry import RtkRule, find_rule

# The CLI invocation we rewrite commands to. Using the module form means we
# don't depend on ``crosshair`` being on PATH at hook time.
REWRITE_CMD = "crosshair rtk"


# Env-prefix grammar used before a command: ``sudo ``, ``env `` or ``VAR=val ``.
# Matches a run of such prefixes followed by whitespace.
_ENV_PREFIX = re.compile(
    r"^(?:"
    r"(?:sudo(?:\s+-[A-Za-z]+)*\s+)"
    r"|(?:env\s+(?:-[A-Za-z]+\s+)*(?:[A-Z_][A-Z0-9_]*=\S+\s+)*)"
    r"|(?:[A-Z_][A-Z0-9_]*=\S+\s+)"
    r")+"
)


def rewrite_command(cmd: str, excluded: Iterable[str] = ()) -> str | None:
    """Rewrite ``cmd`` or return ``None`` if there's nothing to do.

    Idempotent: passing an already-rewritten command yields ``None``.
    """
    trimmed = cmd.strip()
    if not trimmed:
        return None

    # Heredocs / arithmetic expansion: bail entirely.
    if "<<" in trimmed or "$((" in trimmed:
        return None

    # Already RTK (single segment).
    if _has_rewrite_prefix(trimmed):
        # Still walk compounds so the second segment of
        # `rtk git status && cargo test` gets rewritten.
        if not _has_compound(trimmed):
            return None

    # Explicit opt-out env var.
    if trimmed.startswith("RTK_DISABLED=1"):
        return None

    excluded_set = frozenset(excluded)
    rewritten, any_change = _rewrite_compound(trimmed, excluded_set)
    if not any_change:
        return None
    return rewritten


# --------------------------------------------------------------------------- #
# internals                                                                   #
# --------------------------------------------------------------------------- #


class Token(NamedTuple):
    value: str
    kind: str  # "text" | "sep" | "pipe"
    start: int
    end: int


def _has_compound(cmd: str) -> bool:
    return any(tok in cmd for tok in ("&&", "||", ";", "|", " & "))


def _has_rewrite_prefix(cmd: str) -> bool:
    return cmd == "rtk" or cmd.startswith("rtk ") or cmd.startswith(f"{REWRITE_CMD} ")


def _tokenize_compound(cmd: str) -> list[Token]:
    """Split on ``&&``, ``||``, ``;``, ``|`` and (single space) ``&`` while
    respecting single/double-quoted strings."""
    tokens: list[Token] = []
    in_single = False
    in_double = False
    escape = False
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\":
            escape = True
            i += 1
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if in_single or in_double:
            i += 1
            continue
        # Match multi-character ops first.
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            tokens.append(Token(cmd[i : i + 2], "sep", i, i + 2))
            i += 2
            continue
        if c == ";":
            tokens.append(Token(";", "sep", i, i + 1))
            i += 1
            continue
        if c == "|":
            tokens.append(Token("|", "pipe", i, i + 1))
            i += 1
            continue
        if c == "&" and i + 1 < n and cmd[i + 1] == " ":
            tokens.append(Token("&", "sep", i, i + 1))
            i += 1
            continue
        i += 1
    return tokens


def _rewrite_compound(cmd: str, excluded: frozenset[str]) -> tuple[str, bool]:
    """Walk operator-separated segments, rewrite each. Return (result, changed)."""
    tokens = _tokenize_compound(cmd)

    if not tokens:
        rewritten = _rewrite_segment(cmd, excluded)
        return (rewritten or cmd), rewritten is not None

    out: list[str] = []
    any_change = False
    seg_start = 0
    for tok in tokens:
        seg = cmd[seg_start : tok.start].strip()
        if tok.kind == "pipe":
            # Only the producer (the first segment before the pipe) gets
            # rewritten. Tail of the pipe keeps its original form.
            rewritten = _rewrite_segment(seg, excluded) or seg
            if rewritten != seg:
                any_change = True
            out.append(rewritten)
            out.append(" ")
            # Keep everything from here to EOL as-is.
            out.append(cmd[tok.start :])
            return "".join(out), any_change
        # operator (&&, ||, ;, &)
        rewritten = _rewrite_segment(seg, excluded) or seg
        if rewritten != seg:
            any_change = True
        out.append(rewritten)
        out.append(f" {tok.value} ")
        seg_start = tok.end
        while seg_start < len(cmd) and cmd[seg_start] == " ":
            seg_start += 1

    tail = cmd[seg_start:].strip()
    rewritten_tail = _rewrite_segment(tail, excluded) or tail
    if rewritten_tail != tail:
        any_change = True
    out.append(rewritten_tail)
    return "".join(out), any_change


def _rewrite_segment(seg: str, excluded: frozenset[str]) -> str | None:
    """Rewrite a single segment, preserving env prefix and trailing redirects."""
    trimmed = seg.strip()
    if not trimmed:
        return None

    # Strip env prefix (sudo / VAR=val / env VAR=val).
    m = _ENV_PREFIX.match(trimmed)
    env_prefix = trimmed[: m.end()] if m else ""
    body = trimmed[m.end() :] if m else trimmed

    # Explicit opt-out anywhere in the env prefix.
    if "RTK_DISABLED=1" in env_prefix:
        return None

    # Already rewritten — no-op.
    if _has_rewrite_prefix(body):
        return None

    # Strip trailing stdio redirects (`>`, `>>`, `2>&1`, `2>file`, ...).
    body_core, redirect_suffix = _split_trailing_redirects(body)

    rule = find_rule(body_core, excluded=excluded)
    if rule is None:
        return None

    rewritten_body = f"{REWRITE_CMD} {body_core}"
    return f"{env_prefix}{rewritten_body}{redirect_suffix}"


# Recognises trailing redirects we want to preserve verbatim, e.g.
#   `git status > out`, `git status 2>&1`, `git status 2>/dev/null`.
_REDIRECT_RE = re.compile(
    r"(\s+(?:\d*>>?\S*(?:\s+\S+)?|<\S+)(?:\s+\d*>>?\S*(?:\s+\S+)?)*\s*)$"
)


def _split_trailing_redirects(cmd: str) -> tuple[str, str]:
    m = _REDIRECT_RE.search(cmd)
    if not m:
        return cmd, ""
    return cmd[: m.start()].rstrip(), cmd[m.start() :]


__all__ = ["rewrite_command", "REWRITE_CMD"]
