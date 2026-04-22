"""Tests for the shell command rewriter.

This is the piece that sits in the preToolUse hook and must be bullet-proof:
never miscategorise a command, never mangle quoting, never rewrite something
we can't handle safely (heredocs, arithmetic expansion, already-rtk).
"""

from __future__ import annotations

import pytest

from crosshair.rtk.rewrite import REWRITE_CMD, rewrite_command


# --------------------------------------------------------------------------- #
# basic matching                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("git status", f"{REWRITE_CMD} git status"),
        ("git status -s", f"{REWRITE_CMD} git status -s"),
        ("git log -n 5 --oneline", f"{REWRITE_CMD} git log -n 5 --oneline"),
        ("git add .", f"{REWRITE_CMD} git add ."),
        ("git commit -m 'msg'", f"{REWRITE_CMD} git commit -m 'msg'"),
        ("ls", f"{REWRITE_CMD} ls"),
        ("ls -la", f"{REWRITE_CMD} ls -la"),
        ("cat src/main.py", f"{REWRITE_CMD} cat src/main.py"),
        ("head -n 20 foo.txt", f"{REWRITE_CMD} head -n 20 foo.txt"),
        ("grep 'pattern' .", f"{REWRITE_CMD} grep 'pattern' ."),
        ("rg 'foo' src/", f"{REWRITE_CMD} rg 'foo' src/"),
        ("find . -name '*.py'", f"{REWRITE_CMD} find . -name '*.py'"),
        ("tree", f"{REWRITE_CMD} tree"),
        ("pytest tests/", f"{REWRITE_CMD} pytest tests/"),
        ("python -m pytest -x", f"{REWRITE_CMD} python -m pytest -x"),
        ("cargo test --release", f"{REWRITE_CMD} cargo test --release"),
        ("pnpm vitest", f"{REWRITE_CMD} pnpm vitest"),
        ("tsc --noEmit", f"{REWRITE_CMD} tsc --noEmit"),
        ("pnpm tsc --noEmit", f"{REWRITE_CMD} pnpm tsc --noEmit"),
        ("ruff check src/", f"{REWRITE_CMD} ruff check src/"),
        ("docker ps", f"{REWRITE_CMD} docker ps"),
    ],
)
def test_rewrites_common_commands(cmd: str, expected: str) -> None:
    assert rewrite_command(cmd) == expected


@pytest.mark.parametrize(
    "cmd",
    [
        "",  # empty
        "echo hello",  # unknown command
        "rtk git status",  # already rewritten
        "crosshair rtk git status",  # already rewritten (our prefix)
        "node server.js",  # no filter
    ],
)
def test_non_matches_return_none(cmd: str) -> None:
    assert rewrite_command(cmd) is None


# --------------------------------------------------------------------------- #
# unsafe constructs                                                           #
# --------------------------------------------------------------------------- #


def test_heredoc_is_left_alone() -> None:
    assert rewrite_command("git commit -m 'x' <<EOF\ndone\nEOF") is None


def test_arith_expansion_is_left_alone() -> None:
    assert rewrite_command("echo $((1+2)) && git status") is None


def test_explicit_opt_out_env_var() -> None:
    assert rewrite_command("RTK_DISABLED=1 git status") is None


# --------------------------------------------------------------------------- #
# compound commands                                                           #
# --------------------------------------------------------------------------- #


def test_and_chain_rewrites_each_segment() -> None:
    rewritten = rewrite_command("git add . && git commit -m 'msg'")
    assert rewritten == f"{REWRITE_CMD} git add . && {REWRITE_CMD} git commit -m 'msg'"


def test_mixed_chain_preserves_ops() -> None:
    rewritten = rewrite_command("git status; git log -n 3")
    assert rewritten == f"{REWRITE_CMD} git status ; {REWRITE_CMD} git log -n 3"


def test_or_chain() -> None:
    rewritten = rewrite_command("git pull || git status")
    assert rewritten == f"{REWRITE_CMD} git pull || {REWRITE_CMD} git status"


def test_partial_chain_only_known_segments() -> None:
    rewritten = rewrite_command("echo start && git status && echo done")
    assert rewritten == f"echo start && {REWRITE_CMD} git status && echo done"


def test_pipe_only_rewrites_producer() -> None:
    # Only the first segment (git status) should be rewritten; head stays raw.
    rewritten = rewrite_command("git status | head -n 5")
    assert rewritten == f"{REWRITE_CMD} git status | head -n 5"


def test_chain_already_rewritten_segment_is_kept() -> None:
    rewritten = rewrite_command("rtk git status && cargo test")
    # left side is already rtk (keep as-is), right side gets rewritten
    assert rewritten == f"rtk git status && {REWRITE_CMD} cargo test"


# --------------------------------------------------------------------------- #
# env prefix / redirects                                                      #
# --------------------------------------------------------------------------- #


def test_sudo_prefix_is_preserved() -> None:
    rewritten = rewrite_command("sudo docker ps")
    assert rewritten == f"sudo {REWRITE_CMD} docker ps"


def test_var_prefix_is_preserved() -> None:
    rewritten = rewrite_command("CI=1 pytest tests/")
    assert rewritten == f"CI=1 {REWRITE_CMD} pytest tests/"


def test_trailing_redirect_is_preserved() -> None:
    rewritten = rewrite_command("git status 2>&1")
    assert rewritten is not None
    assert rewritten.endswith("2>&1")
    assert f"{REWRITE_CMD} git status" in rewritten


def test_excluded_command_is_skipped() -> None:
    # user's curl is in the excluded list → no rewrite
    assert rewrite_command("grep foo .", excluded=["grep"]) is None


def test_idempotency_on_already_rewritten_compound() -> None:
    once = rewrite_command("git status && git log")
    assert once is not None
    # feeding the result back through the rewriter should be a no-op
    assert rewrite_command(once) is None


def test_quoting_protects_operators() -> None:
    # The && is inside single quotes — must NOT be treated as a chain op.
    rewritten = rewrite_command("git log --grep='foo && bar'")
    assert rewritten == f"{REWRITE_CMD} git log --grep='foo && bar'"
