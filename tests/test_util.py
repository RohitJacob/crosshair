from __future__ import annotations

from crosshair.util import (
    approx_tokens,
    deep_merge,
    extract_file_paths,
    jaccard,
    shorten_path,
    tokenize,
    truncate,
)


def test_approx_tokens_empty():
    assert approx_tokens("") == 0
    assert approx_tokens("a") == 1


def test_approx_tokens_scaling():
    assert approx_tokens("a" * 100) == 25


def test_tokenize_drops_stopwords():
    tokens = tokenize("The quick brown Fox", stopwords=["the", "a"])
    assert "the" not in tokens
    assert "quick" in tokens
    assert tokens[-1] == "fox"


def test_jaccard_sets():
    assert jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert jaccard(["a", "b"], ["c", "d"]) == 0.0
    assert 0.0 < jaccard(["a", "b", "c"], ["b", "c", "d"]) < 1.0
    assert jaccard([], []) == 1.0


def test_extract_file_paths_finds_at_and_backticked():
    text = "please edit @src/foo.py and also `src/bar.py` plus some/baz.py inline"
    out = extract_file_paths(text)
    assert "src/foo.py" in out
    assert "src/bar.py" in out
    assert "some/baz.py" in out


def test_shorten_path():
    long = "/very/long/nested/directory/structure/with/some/file/name.txt"
    assert "…" in shorten_path(long, 20)


def test_truncate_respects_limit():
    assert truncate("hello world", 5).endswith("…")
    assert truncate("hi", 99) == "hi"


def test_deep_merge_nested():
    a = {"x": {"y": 1, "z": 2}, "a": 1}
    b = {"x": {"y": 10}, "b": 2}
    assert deep_merge(a, b) == {"x": {"y": 10, "z": 2}, "a": 1, "b": 2}
