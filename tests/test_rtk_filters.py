"""Filter-level tests.

We stub ``run_subprocess`` so we never actually spawn git/ls/pytest during
tests. The focus is on the compression logic and the exit-code plumbing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crosshair.rtk.filters import base as fbase
from crosshair.rtk.filters import git as git_filter
from crosshair.rtk.filters import tests as tests_filter
from crosshair.rtk.filters import build as build_filter
from crosshair.rtk.filters import files as files_filter
from crosshair.rtk.filters.base import FilterContext
from crosshair.rtk.runner import run_filter
from crosshair.rtk import tracking


def _fake_proc(stdout: str = "", stderr: str = "", code: int = 0):
    cp = subprocess.CompletedProcess(args=["x"], returncode=code, stdout=stdout, stderr=stderr)
    return cp


def _patch_subprocess(monkeypatch, module, result: subprocess.CompletedProcess) -> None:
    monkeypatch.setattr(module, "run_subprocess", lambda argv, ctx, check=False: result)


@pytest.fixture()
def ctx(tmp_path) -> FilterContext:
    return FilterContext(cwd=tmp_path, max_lines=50)


# --------------------------------------------------------------------------- #
# git filters                                                                 #
# --------------------------------------------------------------------------- #


def test_git_status_compresses_output(monkeypatch, ctx: FilterContext) -> None:
    raw = "\n".join(
        [
            "## main...origin/main",
            " M src/a.py",
            " M src/b.py",
            "?? src/c.py",
            "A  src/d.py",
        ]
    ) + "\n"
    _patch_subprocess(monkeypatch, git_filter, _fake_proc(stdout=raw, code=0))
    res = git_filter.git_status([], ctx)
    assert res.exit_code == 0
    assert "branch: main" in res.stdout
    # Must mention all four files by path
    for p in ("src/a.py", "src/b.py", "src/c.py", "src/d.py"):
        assert p in res.stdout
    assert res.original_chars == len(raw)
    assert res.filtered_chars == len(res.stdout)


def test_git_status_clean_repo(monkeypatch, ctx: FilterContext) -> None:
    raw = "## main...origin/main\n"
    _patch_subprocess(monkeypatch, git_filter, _fake_proc(stdout=raw, code=0))
    res = git_filter.git_status([], ctx)
    assert "clean" in res.stdout


def test_git_log_applies_default_format(monkeypatch, ctx: FilterContext) -> None:
    captured_argv: list[list[str]] = []

    def fake(argv, _ctx, check=False):
        captured_argv.append(list(argv))
        return _fake_proc(stdout="abc1234 initial commit (Alice, 2 days ago)\n")

    monkeypatch.setattr(git_filter, "run_subprocess", fake)
    git_filter.git_log([], ctx)
    assert captured_argv and "--pretty=format:%h %s (%an, %ar)" in captured_argv[0]
    assert "-n" in captured_argv[0]


def test_git_add_returns_ok_on_success(monkeypatch, ctx: FilterContext) -> None:
    _patch_subprocess(monkeypatch, git_filter, _fake_proc(stdout="", code=0))
    res = git_filter.git_add(["."], ctx)
    assert res.stdout.strip() == "ok"
    assert res.exit_code == 0


def test_git_add_preserves_error(monkeypatch, ctx: FilterContext) -> None:
    _patch_subprocess(
        monkeypatch,
        git_filter,
        _fake_proc(stdout="", stderr="fatal: pathspec did not match\n", code=128),
    )
    res = git_filter.git_add(["nonexistent"], ctx)
    assert res.exit_code == 128
    assert "pathspec" in res.stderr


def test_git_commit_extracts_sha(monkeypatch, ctx: FilterContext) -> None:
    _patch_subprocess(
        monkeypatch,
        git_filter,
        _fake_proc(stdout="[main abc1234] add thing\n 1 file changed\n", code=0),
    )
    res = git_filter.git_commit(["-m", "add thing"], ctx)
    assert res.stdout.strip() == "ok abc1234"
    assert res.exit_code == 0


def test_git_push_returns_branch(monkeypatch, ctx: FilterContext) -> None:
    def fake_subprocess(argv, _ctx, check=False):
        if argv[1] == "push":
            return _fake_proc(code=0, stderr="To github.com:x\n   ab..cd  main -> main\n")
        if argv[1] == "branch" and "--show-current" in argv:
            return _fake_proc(stdout="main\n")
        return _fake_proc()

    monkeypatch.setattr(git_filter, "run_subprocess", fake_subprocess)
    res = git_filter.git_push([], ctx)
    assert res.stdout.strip() == "ok main"
    assert res.exit_code == 0


def test_git_pull_summarises(monkeypatch, ctx: FilterContext) -> None:
    raw = (
        "Updating 1234..5678\nFast-forward\n"
        " src/a.py | 5 +++--\n"
        " 1 files changed, 3 insertions(+), 2 deletions(-)\n"
    )
    _patch_subprocess(monkeypatch, git_filter, _fake_proc(stdout=raw, code=0))
    res = git_filter.git_pull([], ctx)
    assert res.stdout.strip() == "ok 1 files +3 -2"


def test_git_diff_keeps_stat_and_trims_context(monkeypatch, ctx: FilterContext) -> None:
    seq = iter(
        [
            # --stat run
            _fake_proc(stdout=" src/a.py | 4 ++--\n 1 file changed, 2 insertions(+), 2 deletions(-)\n"),
            # real diff
            _fake_proc(
                stdout=(
                    "diff --git a/src/a.py b/src/a.py\n"
                    "--- a/src/a.py\n"
                    "+++ b/src/a.py\n"
                    "@@ -1,3 +1,3 @@\n"
                    " import os\n"
                    "-import sys\n"
                    "+import sys  # new\n"
                    " import re\n"
                )
            ),
        ]
    )
    monkeypatch.setattr(git_filter, "run_subprocess", lambda *a, **kw: next(seq))
    res = git_filter.git_diff([], ctx)
    assert "1 file changed" in res.stdout
    assert "-import sys" in res.stdout
    assert "+import sys  # new" in res.stdout
    # context lines (those without +/-) must have been removed
    assert "import os" not in res.stdout.replace("# Changes ---", "")
    assert "import re" not in res.stdout.replace("# Changes ---", "")
    # On a real-world diff this would also be smaller; we don't assert it here
    # because the compression marker can exceed tiny-test-input bytes.


# --------------------------------------------------------------------------- #
# files: ls / read / grep / find                                              #
# --------------------------------------------------------------------------- #


def test_ls_filter_groups_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    (tmp_path / "README.md").write_text("y" * 100)
    ctx = FilterContext(cwd=tmp_path)
    res = files_filter.ls_filter([], ctx)
    assert "1 dir(s)" in res.stdout
    assert "1 file(s)" in res.stdout
    assert "README.md" in res.stdout
    assert "src/" in res.stdout


def test_read_filter_truncates_long_file(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1000)))
    ctx = FilterContext(cwd=tmp_path, max_lines=50)
    res = files_filter.read_filter(["big.txt"], ctx)
    assert "line0" in res.stdout
    assert "… 950 more line(s)" in res.stdout
    assert res.filtered_chars < res.original_chars


def test_grep_filter_groups_by_file(monkeypatch, ctx: FilterContext) -> None:
    raw = "\n".join(
        [
            "src/a.py:12:def foo():",
            "src/a.py:47:    foo()",
            "src/b.py:3:def bar():",
        ]
    ) + "\n"
    _patch_subprocess(monkeypatch, files_filter, _fake_proc(stdout=raw, code=0))
    res = files_filter.grep_filter(["grep", "foo", "src/"], ctx)
    assert "3 match(es)" in res.stdout
    assert "src/a.py: 2 match(es)" in res.stdout
    assert "src/b.py: 1 match(es)" in res.stdout
    assert "12:" in res.stdout


def test_find_filter_summary(monkeypatch, ctx: FilterContext) -> None:
    raw = "\n".join(f"./src/file{i}.py" for i in range(50)) + "\n"
    _patch_subprocess(monkeypatch, files_filter, _fake_proc(stdout=raw, code=0))
    res = files_filter.find_filter([".", "-name", "*.py"], ctx)
    assert "50 result(s)" in res.stdout
    assert "… 20 more" in res.stdout


# --------------------------------------------------------------------------- #
# tests: pytest                                                               #
# --------------------------------------------------------------------------- #


def test_pytest_filter_keeps_only_summary_and_failures(monkeypatch, ctx: FilterContext) -> None:
    raw = (
        "============================= test session starts =============================\n"
        "collected 10 items\n"
        "tests/test_a.py .........F [100%]\n"
        "\n"
        "=================================== FAILURES ===================================\n"
        "___________________________________ test_x ______________________________________\n"
        "tests/test_a.py:10: AssertionError: assert 1 == 2\n"
        "\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
        "========================== 1 failed, 9 passed in 0.12s =========================\n"
    )
    _patch_subprocess(monkeypatch, tests_filter, _fake_proc(stdout=raw, code=1))
    res = tests_filter.pytest_filter([], ctx)
    assert "1 failed" in res.stdout
    assert "test_x" in res.stdout
    # No passing test names should leak into output
    assert "tests/test_a.py:10" in res.stdout
    assert "test session starts" not in res.stdout
    assert res.exit_code == 1
    assert res.filtered_chars < res.original_chars


# --------------------------------------------------------------------------- #
# build: tsc / ruff                                                           #
# --------------------------------------------------------------------------- #


def test_tsc_filter_groups_by_file(monkeypatch, ctx: FilterContext) -> None:
    raw = (
        "src/a.ts(1,5): error TS2304: Cannot find name 'foo'.\n"
        "src/a.ts(3,10): error TS2339: Property 'bar' does not exist on type '{}'.\n"
        "src/b.ts(2,1): error TS1005: ';' expected.\n"
    )
    _patch_subprocess(monkeypatch, build_filter, _fake_proc(stdout=raw, code=1))
    res = build_filter.tsc_filter(["tsc"], ctx)
    assert "3 error(s) in 2 file(s)" in res.stdout
    assert "src/a.ts: 2 error(s)" in res.stdout
    assert "src/b.ts: 1 error(s)" in res.stdout
    assert res.exit_code == 1


def test_ruff_filter_groups_by_rule(monkeypatch, ctx: FilterContext) -> None:
    import json

    payload = [
        {"code": "F401", "message": "imported but unused", "filename": "a.py", "location": {"row": 1, "column": 1}},
        {"code": "F401", "message": "imported but unused", "filename": "b.py", "location": {"row": 2, "column": 1}},
        {"code": "E501", "message": "line too long", "filename": "a.py", "location": {"row": 5, "column": 1}},
    ]
    _patch_subprocess(monkeypatch, build_filter, _fake_proc(stdout=json.dumps(payload), code=1))
    res = build_filter.ruff_filter(["ruff", "check"], ctx)
    assert "3 diagnostic(s)" in res.stdout
    assert "F401: 2×" in res.stdout
    assert "E501: 1×" in res.stdout


# --------------------------------------------------------------------------- #
# runner: dispatch + tracking                                                 #
# --------------------------------------------------------------------------- #


def test_runner_dispatches_known_filter_and_tracks(monkeypatch, tmp_path: Path) -> None:
    # Point the tracking log at a tmp file so we don't touch the user's.
    log_path = tmp_path / "rtk.ndjson"
    monkeypatch.setattr(tracking, "RTK_LOG_PATH", log_path)

    # Swap in a canned git status output.
    raw = "## main\n M foo.py\n"
    _patch_subprocess(monkeypatch, git_filter, _fake_proc(stdout=raw, code=0))

    res = run_filter(["git", "status"], FilterContext(cwd=tmp_path))
    assert res.exit_code == 0
    assert "foo.py" in res.stdout

    events = list(tracking.iter_events(log_path))
    assert len(events) == 1
    assert events[0].filter == "git-status"
    assert events[0].original_chars == len(raw)


def test_runner_passes_through_unknown(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "rtk.ndjson"
    monkeypatch.setattr(tracking, "RTK_LOG_PATH", log_path)

    # Stub out the passthrough subprocess call so we don't spawn real binaries.
    def fake_which(name: str):
        return "/bin/echo" if name == "echo" else None

    monkeypatch.setattr(fbase, "which", fake_which)
    monkeypatch.setattr(
        fbase,
        "run_subprocess",
        lambda argv, ctx, check=False: _fake_proc(stdout="hello\n"),
    )

    res = run_filter(["echo", "hello"], FilterContext(cwd=tmp_path))
    assert res.passthrough is True
    events = list(tracking.iter_events(log_path))
    assert events[0].filter == "passthrough"


def test_runner_falls_back_on_filter_exception(monkeypatch, tmp_path: Path) -> None:
    """If a filter raises, the runner must fall back to a passthrough rather
    than bubbling the exception up into Cursor."""
    from crosshair.rtk import registry

    log_path = tmp_path / "rtk.ndjson"
    monkeypatch.setattr(tracking, "RTK_LOG_PATH", log_path)

    def explode(*_a, **_kw):
        raise RuntimeError("boom")

    # Rebind the filter_fn on the frozen rule by swapping it on the list.
    original_rule = next(r for r in registry.RTK_COMMANDS if r.name == "git-status")
    idx = registry.RTK_COMMANDS.index(original_rule)
    patched_rule = registry.RtkRule(
        name=original_rule.name,
        pattern=original_rule.pattern,
        rewrite_prefixes=original_rule.rewrite_prefixes,
        rewrite_to=original_rule.rewrite_to,
        filter_fn=explode,
        category=original_rule.category,
        est_savings=original_rule.est_savings,
    )
    monkeypatch.setitem(registry._RULES_BY_NAME, "git-status", patched_rule)
    registry.RTK_COMMANDS[idx] = patched_rule
    try:
        # passthrough will still call fbase.which+run_subprocess.
        monkeypatch.setattr(fbase, "which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            fbase,
            "run_subprocess",
            lambda argv, ctx, check=False: _fake_proc(stdout="raw\n"),
        )

        res = run_filter(["git", "status"], FilterContext(cwd=tmp_path))
        assert res.passthrough is True
        events = list(tracking.iter_events(log_path))
        assert events[0].filter == "passthrough"
    finally:
        registry.RTK_COMMANDS[idx] = original_rule
