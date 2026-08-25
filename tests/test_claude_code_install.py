"""Tests for ``crosshair install --host claude-code``."""

from __future__ import annotations

import json
from pathlib import Path

from crosshair import cli


def _run_install(tmp_path: Path, *extra_args: str) -> dict:
    settings_file = tmp_path / "settings.json"
    rc = cli.main(
        [
            "install",
            "--host",
            "claude-code",
            "--python",
            "/fake/venv/bin/python",
            "--hooks-file",
            str(settings_file),
            *extra_args,
        ]
    )
    assert rc == 0
    return json.loads(settings_file.read_text())


def test_install_writes_expected_events_and_matchers(tmp_path: Path) -> None:
    data = _run_install(tmp_path)
    hooks = data["hooks"]
    assert set(hooks.keys()) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "Stop",
    }

    pre_tool = hooks["PreToolUse"][0]
    assert pre_tool["matcher"] == "Bash"
    assert pre_tool["hooks"][0]["command"].endswith("claude-hook pre-tool-use")

    post_tool_matchers = {g["matcher"] for g in hooks["PostToolUse"]}
    assert post_tool_matchers == {"", "Edit|Write|MultiEdit|NotebookEdit"}


def test_install_emits_sys_path_isolation_flags(tmp_path: Path) -> None:
    data = _run_install(tmp_path)
    for event, groups in data["hooks"].items():
        for group in groups:
            for entry in group["hooks"]:
                cmd = entry["command"]
                assert "PYTHONSAFEPATH=1" in cmd, f"{event!r} missing PYTHONSAFEPATH=1: {cmd!r}"
                assert "-u PYTHONPATH" in cmd, f"{event!r} missing env -u PYTHONPATH: {cmd!r}"
                assert "/fake/venv/bin/python" in cmd
                assert "-m crosshair claude-hook" in cmd


def test_install_is_idempotent(tmp_path: Path) -> None:
    data1 = _run_install(tmp_path)
    data2 = _run_install(tmp_path)
    for event in data1["hooks"]:
        assert len(data1["hooks"][event]) == len(data2["hooks"][event]), (
            f"re-running install duplicated {event!r} groups"
        )


def test_install_no_rtk_skips_pre_tool_use(tmp_path: Path) -> None:
    data = _run_install(tmp_path, "--no-rtk")
    assert "PreToolUse" not in data["hooks"]


def test_install_preserves_unrelated_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "Notification": [
                        {"matcher": "", "hooks": [{"type": "command", "command": "notify-me"}]}
                    ]
                },
                "disableAllHooks": False,
            }
        )
    )
    rc = cli.main(
        [
            "install",
            "--host",
            "claude-code",
            "--python",
            "/fake/venv/bin/python",
            "--hooks-file",
            str(settings_file),
        ]
    )
    assert rc == 0
    data = json.loads(settings_file.read_text())
    assert data["disableAllHooks"] is False
    assert data["hooks"]["Notification"][0]["hooks"][0]["command"] == "notify-me"
    assert "SessionStart" in data["hooks"]


def test_uninstall_removes_only_crosshair_groups(tmp_path: Path) -> None:
    _run_install(tmp_path)
    settings_file = tmp_path / "settings.json"
    data = json.loads(settings_file.read_text())
    data.setdefault("hooks", {}).setdefault("Notification", []).append(
        {"matcher": "", "hooks": [{"type": "command", "command": "notify-me"}]}
    )
    settings_file.write_text(json.dumps(data))

    rc = cli.main(
        ["uninstall", "--host", "claude-code", "--hooks-file", str(settings_file)]
    )
    assert rc == 0
    data = json.loads(settings_file.read_text())
    assert "SessionStart" not in data["hooks"]
    assert "PreToolUse" not in data["hooks"]
    assert data["hooks"]["Notification"][0]["hooks"][0]["command"] == "notify-me"
