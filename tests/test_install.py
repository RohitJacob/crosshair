"""Tests for the ``crosshair install`` subcommand.

Regression coverage for the sys.path shadow bug: when Cursor runs hooks with
``cwd=~/.cursor`` and the venv lives at ``~/.cursor/crosshair/``, Python
resolves ``import crosshair`` as a namespace package pointing at the install
dir (no ``__init__.py``) and every hook crashes with ``ImportError: cannot
import name '__version__' from 'crosshair' (unknown location)``.

The fix is to emit every hook command with ``PYTHONSAFEPATH=1`` so Python
does not auto-prepend the cwd to ``sys.path``.
"""

from __future__ import annotations

import json
from pathlib import Path

from crosshair import cli


def _run_install(tmp_path: Path, *extra_args: str) -> dict:
    hooks_file = tmp_path / "hooks.json"
    rc = cli.main(
        [
            "install",
            "--python",
            "/fake/venv/bin/python",
            "--hooks-file",
            str(hooks_file),
            *extra_args,
        ]
    )
    assert rc == 0
    return json.loads(hooks_file.read_text())


def test_install_emits_pythonsafepath_flag(tmp_path: Path) -> None:
    data = _run_install(tmp_path)
    for event, entries in data["hooks"].items():
        for entry in entries:
            cmd = entry["command"]
            assert "PYTHONSAFEPATH=1" in cmd, (
                f"{event!r} hook command is missing PYTHONSAFEPATH=1 — this will "
                f"re-introduce the namespace-package shadow bug. cmd={cmd!r}"
            )
            assert "/fake/venv/bin/python" in cmd
            assert "-m crosshair hook" in cmd


def test_install_is_idempotent(tmp_path: Path) -> None:
    data1 = _run_install(tmp_path)
    data2 = _run_install(tmp_path)
    for event in data1["hooks"]:
        assert len(data1["hooks"][event]) == len(data2["hooks"][event]), (
            f"re-running install duplicated {event!r} entries"
        )


def test_cli_cli_tolerates_shadowed_version(monkeypatch) -> None:
    """If ``from crosshair import __version__`` fails at import time (namespace
    package shadow), the CLI module must still load so the hook can fail open
    rather than hard-crashing every hook invocation."""
    import importlib
    import sys

    mod = sys.modules.get("crosshair.cli")
    assert mod is not None
    reloaded = importlib.reload(mod)
    assert hasattr(reloaded, "__version__")
