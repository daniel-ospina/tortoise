"""Unit tests for tortoise.sparse — the shared OR-union tokenizer + index-text
builder (R2, issue #1541 D1/D4).

Both sparse stacks (RediSearch FTS and the sklearn TF-IDF fallback) import
these functions — the surface-11 normalization contract is a shared code
path, and this module pins its behavior.
"""
from __future__ import annotations  # noqa: I001

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sparse import (  # noqa: I001
    SPARSE_STOPWORDS, tokenize_sparse_query, build_or_query, index_text,
)


class TestTokenizeSparseQuery:
    def test_basic_multitoken(self):
        assert tokenize_sparse_query("gym schedule 5pm") == ["gym", "schedule", "5pm"]

    def test_lowercased(self):
        assert tokenize_sparse_query("Gym Schedule") == ["gym", "schedule"]

    def test_punctuation_and_reserved_chars_stripped(self):
        # R2 D1: punctuation/reserved chars never survive into a token —
        # a query containing '|', '(', or '@' cannot change operator
        # semantics (structural escape).
        assert tokenize_sparse_query("gym's, 5pm!") == ["gym", "5pm"]
        assert tokenize_sparse_query("ab|cd (ef) @gh") == ["ab", "cd", "ef", "gh"]
        assert "|" not in "".join(tokenize_sparse_query("foo|bar"))

    def test_min_length_two(self):
        assert tokenize_sparse_query("a b cd") == ["cd"]

    def test_all_digits_dropped(self):
        # 27:12 → 27, 12 — numeric noise; 5k is NOT all-digits → kept.
        assert tokenize_sparse_query("time is 27:12") == ["time"]
        assert tokenize_sparse_query("my 5k pb") == ["5k", "pb"]

    def test_stopwords_filtered(self):
        assert tokenize_sparse_query("what is the gym schedule") == ["gym", "schedule"]
        assert tokenize_sparse_query("my fastest 5k") == ["fastest", "5k"]
        for w in ("what", "when", "where", "who", "which", "how",
                  "my", "your", "i", "you", "me", "did", "do", "does",
                  "have", "has", "the", "a", "an", "of", "in", "on", "at"):
            assert w in SPARSE_STOPWORDS

    def test_deterministic_source_order(self):
        assert tokenize_sparse_query("zebra apple mango") == ["zebra", "apple", "mango"]
        assert tokenize_sparse_query("zebra apple mango") == tokenize_sparse_query("zebra apple mango")

    def test_empty_and_none_inputs(self):
        assert tokenize_sparse_query("") == []
        assert tokenize_sparse_query("   ") == []

    def test_stopword_only_input(self):
        assert tokenize_sparse_query("what is my") == []


class TestBuildOrQuery:
    def test_multitoken_union(self):
        # length-desc specificity ordering: longer (more specific) token first
        assert build_or_query("gym schedule") == "schedule|gym"

    def test_stopwords_removed_from_union(self):
        assert build_or_query("what's my fastest 5k") == "fastest|5k"

    def test_length_desc_specificity_ordering(self):
        # longer tokens first (specificity), stable ties keep source order
        assert build_or_query("run gym marathon") == "marathon|run|gym"
        assert build_or_query("ab cd ef") == "ab|cd|ef"  # equal length → source order

    def test_token_length_cap(self):
        # 13 surviving tokens → capped at 12, longest first (ties stable)
        tokens = [f"tok{i:02d}" for i in range(13)]
        q = build_or_query(" ".join(tokens))
        parts = q.split("|")
        assert len(parts) == 12
        # all 6 chars → stable source order, last (tok12) cut
        assert "tok12" not in parts
        assert parts[0] == "tok00"

    def test_cap_does_not_bite_short_paraphrase(self):
        # a one-token-drop paraphrase (2 tokens) loses nothing
        assert build_or_query("gym schedule 5pm") == "schedule|gym|5pm"
        assert build_or_query("gym schedule") == "schedule|gym"

    def test_empty_input_returns_raw(self):
        # never send an empty query string to queryNodes
        assert build_or_query("") == ""
        assert build_or_query("   ") == "   "

    def test_stopword_only_returns_raw(self):
        assert build_or_query("what is my") == "what is my"

    def test_single_token_returns_raw(self):
        # byte-identical to the pre-R2 passthrough for degenerate inputs
        assert build_or_query("gym") == "gym"
        assert build_or_query("Gym") == "Gym"

    def test_max_terms_kwarg(self):
        assert build_or_query("ab cd ef gh", max_terms=2) == "ab|cd"


class TestIndexText:
    def test_content_and_search_keys(self):
        assert index_text("personal best 5K time is 27:12",
                          "fastest 5k running pb") == \
            "personal best 5K time is 27:12 fastest 5k running pb"

    def test_absent_search_keys_content_only(self):
        assert index_text("gym schedule 6pm", None) == "gym schedule 6pm"
        assert index_text("gym schedule 6pm", "") == "gym schedule 6pm"

    def test_none_and_empty_parts_skipped(self):
        assert index_text(None, "", "only") == "only"

    def test_list_part_flattened(self):
        # E3's pre-flatten graph values are arrays; each element joins as a chunk
        assert index_text("a b", ["c d", "e"]) == "a b c d e"
        assert index_text(["fastest 5k", "running pb"], None) == "fastest 5k running pb"

    def test_stringified_non_str(self):
        assert index_text("x", 42) == "x 42"


class TestFallbackTfidfSearchKeys:
    """The embedded-stack counterpart of the D3 FTS index test: a point whose
    CONTENT lacks the alias token surfaces via search_keys (R2 D4)."""

    def test_alias_only_query_surfaces_point(self):
        from tortoise.search_engine import fallback_tfidf
        points = [
            {"id": "p1", "content": "personal best 5K time is 27:12",
             "pointKind": "statement", "search_keys": ["fastest 5k", "running pb"]},
            {"id": "p2", "content": "gym schedule 5pm",
             "pointKind": "statement"},
        ]
        hits = fallback_tfidf("fastest 5k", points, limit=5)
        ids = [h["id"] for h in hits]
        assert "p1" in ids, f"alias-only point not surfaced: {ids}"
        # the returned payload keeps the REAL content, never the alias text
        p1 = next(h for h in hits if h["id"] == "p1")
        assert p1["content"] == "personal best 5K time is 27:12"

    def test_absent_search_keys_byte_identical_content(self):
        from tortoise.search_engine import fallback_tfidf
        points = [{"id": "p1", "content": "gym schedule 5pm",
                   "pointKind": "statement"}]
        hits = fallback_tfidf("gym schedule", points, limit=5)
        assert [h["id"] for h in hits] == ["p1"]
        assert hits[0]["content"] == "gym schedule 5pm"
