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
    escape_redisearch_literal,
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
        # empty/whitespace inputs pass through VERBATIM (pre-existing
        # passthrough) — the SDK's classify_query strip-guards FTS
        # submission, so an empty string never reaches queryNodes.
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


class TestBuildOrQueryRedisearchEscape:
    """#1791: the raw-query fallback (≤1 surviving token) previously carried
    RediSearch-special characters into ``db.idx.fulltext.queryNodes`` verbatim,
    raising "RediSearch: Syntax error at offset N" (verified live: ``10:00`` →
    "Syntax error at offset 2" — the revalidation-log signature; ``2:30`` →
    offset 1; ``@speed`` → offset 0). The FTS leg then failed → breaker
    degraded FTS for ALL later queries. RediSearch's own literal-escape is a
    backslash prefix; every escaped form below parses clean on a live server
    (verified empirically), while clean/degenerate inputs WITHOUT special
    chars stay byte-identical (no recall change on normal terms).

    The OR-union path (≥2 tokens) is unaffected by design — tokens are
    alnum-only (structural escape, R2 #1541), so the union ``a|b`` must keep
    its OR pipe unescaped.
    """

    def test_times_colon_escaped(self):
        # the exact #1791 log signature: all-digit tokens dropped → raw
        # passthrough → ':' is a field separator → syntax error. Escaped
        # colon parses clean (literal '10:00' token, matches nothing —
        # numeric noise is dropped by design, but no crash / no breaker trip).
        assert build_or_query("10:00") == r"10\:00"
        assert build_or_query("2:30") == r"2\:30"

    def test_quotes_and_parens_escaped(self):
        # single surviving token around quotes/parens → raw passthrough;
        # quotes (phrase) and parens (grouping) are query syntax.
        assert build_or_query('"(maybe)"') == r'\"\(maybe\)\"'
        assert build_or_query('"hello"') == r'\"hello\"'

    def test_field_modifier_and_filter_chars_escaped(self):
        # '@' field modifier, '{...}' tag/filter, '[...]' range.
        assert build_or_query("@speed") == r"\@speed"
        assert build_or_query("{urgent}") == r"\{urgent\}"
        assert build_or_query("[x]") == r"\[x\]"

    def test_operators_escaped(self):
        # '|' OR, '%' fuzzy, trailing '~' fuzzy, '-' negation, ';'
        # attribute separator — all parsed as operators when unescaped.
        # NOTE: these shapes reach the RAW fallback via the tokenizer's
        # min-length-2 rule (single letters are dropped → ≤1 surviving
        # token) — the same mechanism as test_mixed_token_time_escaped.
        assert build_or_query("A|B") == r"A\|B"
        assert build_or_query("100%") == r"100\%"
        assert build_or_query("a~") == r"a\~"
        assert build_or_query("-x") == r"\-x"
        assert build_or_query("x;y") == r"x\;y"  # ';' attribute separator

    def test_unbalanced_delimiters_escaped(self):
        # the actual crash class for grouping/phrase syntax is the UNBALANCED
        # form (unclosed '(' / '"' → "Syntax error at offset 0").
        assert build_or_query("(maybe") == r"\(maybe"
        assert build_or_query('say"') == r'say\"'

    def test_mixed_token_time_escaped(self):
        # '10:00' is all-digit → 0 surviving tokens; the common real-world
        # shape '10:30am' keeps ONE token ('30am') and still hits the raw
        # fallback with the colon intact.
        assert build_or_query("10:30am") == r"10\:30am"

    def test_escape_covers_every_declared_special_char(self):
        # table-driven pin of the FULL declared set: a future edit that drops
        # any char from _REDI_SEARCH_SPECIAL (the #1791 bug class — e.g. '*'
        # wildcard or '$' params silently changing recall without crashing)
        # must fail here. Direct function test, not only via build_or_query.
        specials = "\\,;:-|@~()[]{}<>=*%\"$"
        for c in specials:
            assert escape_redisearch_literal("a" + c + "b") == "a\\" + c + "b", c

    def test_non_special_punctuation_not_escaped(self):
        # chars outside the RediSearch-special set pass through unescaped —
        # no spurious escaping (apostrophe, period, slash, ? ! _ and space)
        # incl. common ASCII punctuation that must NOT be added to the set.
        for s in ("it's ok", "x.y", "a/b", "q?mark", "bang!x", "x_y",
                  "a b", "x&y", "x#y", "x^y", "x+y", "x`y"):
            assert escape_redisearch_literal(s) == s, s
        assert build_or_query("it's ok") == "it's ok"

    def test_lone_special_char_queries(self):
        # degenerate inputs that are ONLY special chars → 0 surviving tokens
        # → raw fallback → lone escaped operator sent to queryNodes.
        assert build_or_query(":") == r"\:"
        assert build_or_query("(") == r"\("
        assert build_or_query("|") == r"\|"

    def test_max_terms_zero_returns_escaped_raw(self):
        # max_terms=0 would produce an EMPTY OR-union ('' — the #1791 crash
        # class "Syntax error at offset 0"); negative caps degrade to the
        # same escaped-raw fallback as degenerate inputs instead (not the
        # N-1 token slice they previously triggered).
        assert build_or_query("ab cd ef gh", max_terms=0) == "ab cd ef gh"
        assert build_or_query("10:00", max_terms=0) == r"10\:00"
        assert build_or_query("ab cd", max_terms=-1) == "ab cd"

    def test_clean_raw_fallback_byte_identical(self):
        # no special chars → the escape is a strict no-op: degenerate but
        # clean inputs keep the pre-#1791 passthrough bytes exactly.
        assert build_or_query("gym") == "gym"
        assert build_or_query("Gym") == "Gym"
        assert build_or_query("5k") == "5k"
        assert build_or_query("what is my") == "what is my"
        assert build_or_query("it's ok") == "it's ok"  # apostrophe not special
        assert build_or_query("") == ""
        assert build_or_query("   ") == "   "

    def test_or_union_path_unchanged(self):
        # ≥2 surviving tokens → OR-union path is structurally safe (alnum
        # tokens, R2 #1541); pipes stay unescaped; byte-identical.
        assert build_or_query("gym schedule") == "schedule|gym"
        assert build_or_query('he said "hello" (maybe) A|B') == "hello|maybe|said"
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
