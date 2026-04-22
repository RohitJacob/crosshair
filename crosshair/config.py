"""Config loader.

User config lives at ``~/.cursor/crosshair/config.json`` and is deep-merged
over ``config/default.json``. Both files are plain JSON so users don't need to
install PyYAML just to tweak a threshold.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crosshair.util import deep_merge, expand

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.json"
USER_CONFIG_PATH = Path("~/.cursor/crosshair/config.json")


@dataclass
class Config:
    """Thin wrapper around the merged dict so call sites can do .router etc."""

    data: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def router(self) -> dict[str, Any]:
        return self.data.get("router", {})

    @property
    def safepoint(self) -> dict[str, Any]:
        return self.data.get("safepoint", {})

    @property
    def logging(self) -> dict[str, Any]:
        return self.data.get("logging", {})

    @property
    def state(self) -> dict[str, Any]:
        return self.data.get("state", {})

    @property
    def session_start(self) -> dict[str, Any]:
        return self.data.get("session_start", {})


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        # Fail-open with a log. A bad user config should never break Cursor.
        msg = f"[crosshair] ignoring malformed config at {path}: {e}"
        try:
            _emergency_log(msg)
        finally:
            return {}


def _emergency_log(msg: str) -> None:
    try:
        log_path = expand("~/.cursor/crosshair/logs/errors.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        # We never want the config loader to raise.
        pass


def _resolve_default_path() -> Path:
    """Find the shipped default config, whether installed via pip or run from
    the source tree. Looks in several candidate spots.
    """
    candidates = [
        DEFAULT_CONFIG_PATH,
        Path(__file__).resolve().parent / "default.json",
    ]
    env = os.environ.get("CROSSHAIR_DEFAULT_CONFIG")
    if env:
        candidates.insert(0, Path(env))
    for cand in candidates:
        if cand.exists():
            return cand
    return DEFAULT_CONFIG_PATH


def load_config(user_path: Path | None = None) -> Config:
    default_path = _resolve_default_path()
    defaults = _read_json(default_path)
    user_path = user_path or expand(USER_CONFIG_PATH)
    overrides = _read_json(user_path) if user_path.exists() else {}
    merged = deep_merge(defaults, overrides)
    return Config(data=merged, source=user_path if overrides else default_path)


def user_config_path() -> Path:
    return expand(USER_CONFIG_PATH)


def default_config_path() -> Path:
    return _resolve_default_path()
