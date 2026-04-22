"""Small shared helpers with no side effects.

Kept deliberately simple so they can be unit tested without any of the
Cursor-specific hook plumbing.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{1,}")


def approx_tokens(text: str) -> int:
    """Very rough token estimate. Cursor doesn't expose real counts, so we use
    the common GPT/Claude heuristic of ~4 characters per token, with a small
    lower bound so a 3-character prompt never counts as zero.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def tokenize(text: str, stopwords: Iterable[str] = ()) -> list[str]:
    """Lowercase alphanumeric tokens with stopwords dropped.

    Used for topic-shift similarity, not for anything user-visible, so we
    deliberately keep it cheap and deterministic.
    """
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    stops = {s.lower() for s in stopwords}
    return [w for w in words if w not in stops and len(w) > 1]


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    """Set-based Jaccard similarity. Returns 1.0 when both are empty."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def minutes_since(value: str) -> float:
    dt = parse_iso(value)
    if dt is None:
        return 0.0
    now = datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 60.0)


def ensure_dir(path: Path) -> Path:
    path = Path(path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def expand(path: str | Path) -> Path:
    return Path(str(path)).expanduser()


def truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge that returns a new dict without mutating inputs."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def extract_file_paths(text: str) -> list[str]:
    """Pull things that look like file references out of a prompt.

    Catches `@path/file.ext`, backticked `path/file.ext`, and common bare
    file.ext tokens. Used for the handoff summary.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in re.findall(r"@([\w./\-]+(?:/[\w./\-]+)*)", text):
        seen.setdefault(match, None)
    for match in re.findall(r"`([^`]+?\.[A-Za-z0-9]{1,6})`", text):
        seen.setdefault(match.strip(), None)
    for match in re.findall(r"\b([\w./\-]+/[\w./\-]+\.[A-Za-z0-9]{1,6})\b", text):
        seen.setdefault(match, None)
    return list(seen.keys())


def shorten_path(path: str, max_len: int = 60) -> str:
    if not path or len(path) <= max_len:
        return path
    head = path[: max_len // 2 - 1]
    tail = path[-(max_len // 2 - 2) :]
    return f"{head}…{tail}"
