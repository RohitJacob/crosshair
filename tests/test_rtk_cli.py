"""Tests for the standalone ``rtk`` console-script entry point.

``rtk_main`` is the shorter form the preToolUse rewriter produces; it must
dispatch to the exact same handlers as ``crosshair rtk <argv>`` and honour the
same argv shape (including the REMAINDER semantics for ``rtk git status -s``).
"""

from __future__ import annotations

import pytest

from crosshair.cli import rtk_main


def test_rtk_main_no_args_shows_usage(capsys):
    code = rtk_main([])
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: rtk ")
    # Must not leak the long form in the usage line.
    assert "crosshair rtk" not in err


def test_rtk_main_list_exits_zero(capsys):
    code = rtk_main(["list"])
    assert code == 0
    out = capsys.readouterr().out
    # The rewrite-target column uses REWRITE_CMD which is now "rtk".
    assert "rtk git status" in out
    assert "crosshair rtk git status" not in out


def test_rtk_main_rewrite_prints_result(capsys):
    code = rtk_main(["rewrite", "git", "status"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == "rtk git status"


def test_rtk_main_rewrite_unknown_is_no_op(capsys):
    code = rtk_main(["rewrite", "echo", "hi"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "(no rewrite) echo hi"


def test_rtk_main_rewrite_requires_command(capsys):
    code = rtk_main(["rewrite"])
    assert code == 2
    err = capsys.readouterr().err
    assert "usage: rtk rewrite" in err


def test_rtk_main_gain_handles_empty_log(capsys):
    # No events logged in the test environment — the command should still
    # succeed and print the header.
    code = rtk_main(["gain"])
    assert code == 0
    assert "CROSSHAIR RTK" in capsys.readouterr().out
