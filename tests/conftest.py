"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crosshair.config import Config, load_config
from crosshair.logs import EventLogger
from crosshair.state import StateStore


@pytest.fixture()
def default_config() -> Config:
    return load_config(user_path=Path("/nonexistent.json"))


@pytest.fixture()
def tmp_config(tmp_path: Path, default_config: Config) -> Config:
    data = dict(default_config.data)
    data.setdefault("logging", {})
    data["logging"]["path"] = str(tmp_path / "events.ndjson")
    data.setdefault("state", {})
    data["state"]["dir"] = str(tmp_path / "state")
    return Config(data=data)


@pytest.fixture()
def state_store(tmp_config: Config) -> StateStore:
    return StateStore(tmp_config)


@pytest.fixture()
def logger(tmp_config: Config) -> EventLogger:
    return EventLogger(tmp_config)
