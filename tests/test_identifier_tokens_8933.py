"""#8933 — identifier preservation on the search lane (owner ruling, 2026-10-26).

The defect this file pins: `tortoise/sparse.py` splits a query on
non-alphanumerics BEFORE dropping all-digit tokens, so `ERR-4032` -> `err`,
`INV-2024-0091` -> `inv`, `capstone-9664` -> `capstone`. Two records whose codes
differ by one digit become the same query, score the same, and the tie-break
drops the newest.

The owner ruling on #8933 REOPENED the #2070 `keep_numeric=False`
byte-identity control for identifier-shaped queries only, in two stages:

* **Stage 1** — a query carrying an identifier keeps its parts and requires
  them ADJACENT (`"err 4032"`): an OR alternative over terms the index already
  stores, so it works on records written before this change.
* **Stage 2** — the identifier gets its own whole copy in the indexed text
  (`ERR-4032` -> `err4032`) via `search_keys`, which is already a field of the
  `Point` fulltext index, and the query emits the same normalised term.

The load-bearing invariant, asserted by `TestRoundTrip`: the SAME normaliser
runs on the write path and the read path. Drift between them means every
identifier query silently returns nothing.

The four acceptance criteria of the ruling map to this file 1:1:
`TestPolicy` (a policy test that REDS when the mechanism is removed),
`TestRoundTrip` (write/read agreement over a spelling table), `TestRawRetrieval`
(a query that matched before still matches; no bare-numeric noise),
`TestDiscrimination` (the #10121-shaped fixture: the just-written exact match
ranks first, not last).
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tests import _live_utils
from tortoise.bm25 import bm25_rank, bm25_scores, bm25_tokens
from tortoise.projection.entities import (
    append_identifier_aliases,
    flatten_search_keys_prop,
    point_identifier_aliases,
)
from tortoise.sparse import (
    IDENTIFIER_ENV,
    MAX_IDENTIFIER_LEN,
    build_or_query,
    expansion_tokens,
    identifier_alias,
    identifier_aliases,
    identifier_phrase_terms,
    identifier_tokens_enabled,
    tokenize_sparse_query,
)

# ── live-FalkorDB availability (the #6673 docker-lane probe) ───────────────
# Mirrors tests/test_search_engine.py: the docker lane is the DEFAULT lane, so
# these tests run on the standard local/CI services job and skip gracefully in
# an embedded-only run instead of reding it.
_LANE_URI = os.environ.get("TORTOISE_DB_URI") or \
    _live_utils.docker_uri("tortoise_test_identifier_8933")
LIVE = False
# Bound BEFORE the `try`: the `finally` must restore the environment even when
# the probe fails at its very first statement (an unreachable lane raises inside
# `_FP.from_uri`, but a broken import raises BEFORE `_old` would be assigned).
# An unbound `_old` in `finally` raises `NameError`, and an exception in
# `finally` replaces the caught one — so the promised "skip gracefully" would
# become a collection ERROR for the whole file (#10203 review P3).
_old = os.environ.get("TORTOISE_DB_URI")
try:
    from tortoise.projection import FalkorProjection as _FP

    os.environ["TORTOISE_DB_URI"] = _LANE_URI
    _probe = _FP.from_uri(_LANE_URI)
    _probe.close()
    LIVE = True
except Exception:
    LIVE = False
finally:
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

requires_live = pytest.mark.skipif(
    not LIVE, reason="requires a live FalkorDB FTS lane (docker services job)")


def _or_terms(query: str) -> list[str]:
    """Split an OR-union query into its terms, keeping a PHRASE whole.

    `build_or_query` joins with `|`, and a phrase term itself contains spaces but
    no `|`, so a plain `.split("|")` is enough here — but the earlier version of
    the R3 test stripped the quotes FIRST and then split, which turned
    `"err 4032"` into the bare digit term `4032` and made the test's own quote
    guard dead code (#10203 review round 6). Quotes are kept: callers compare
    whole terms, and a phrase is one term.
    """
    return [t.strip() for t in query.split("|") if t.strip()]


# ── criterion 1: the policy, and the mechanism that produces it ────────────

class TestPolicy:
    """Indicator (2) of the O/I/T: the identifier policy pinned by a test."""

    def test_two_codes_one_digit_apart_are_different_queries(self):
        """ERR-4032 and ERR-4031 must not collapse to the same query.

        This is the whole defect: before the change both produced `err`, so two
        records whose codes differ by one digit received the same query and the
        tie-break decided which one the caller saw.
        """
        q432 = build_or_query("error ERR-4032 on deploy")
        q431 = build_or_query("error ERR-4031 on deploy")
        assert q432 != q431, (q432, q431)
        assert "err4032" in q432.split("|"), q432
        assert "err4031" in q431.split("|"), q431
        assert "err4032" not in q431 and "err4031" not in q432

    def test_the_distinguishing_token_is_not_a_bare_numeric(self):
        """The reopen is of the IDENTIFIER control, not of #2070's number lane:
        the digits survive as a COMPOSITE (`err4032`) and inside an adjacency
        PHRASE (`"err 4032"`), never as a standalone OR term."""
        terms = build_or_query("error ERR-4032 on deploy").split("|")
        assert "4032" not in terms, terms
        assert "err4032" in terms, terms
        assert '"err 4032"' in terms, terms

    def test_bare_identifier_query_is_not_the_matched_nothing_fallback(self):
        """A query that IS only an identifier used to take the degenerate
        escaped-RAW branch (`ERR\\-4032`), which matches ZERO rows because the
        index stores the segments separately (measured on #8933). Post-fix it
        takes the OR-union branch."""
        q = build_or_query("ERR-4032")
        assert q == 'err|err4032|"err 4032"', q

    def test_mutation_removing_the_mechanism_reds_the_policy(
            self, monkeypatch):
        """Mutation proof (acceptance criterion 1): switch the mechanism off
        and the discrimination disappears — which is exactly what the tests
        above assert is present."""
        monkeypatch.setenv(IDENTIFIER_ENV, "0")
        assert identifier_tokens_enabled() is False
        assert build_or_query("error ERR-4032 on deploy") == \
            build_or_query("error ERR-4031 on deploy")

    def test_switch_resolves_safely(self, monkeypatch):
        """The ruling's fail-safe switch: unset/garbage keep the shipped
        default (ON — a typo never flips the product), an explicit OFF spelling
        disables it."""
        for raw, expected in (("", True), ("nonsense", True), ("1", True),
                              ("off", False), ("0", False), ("false", False)):
            if raw:
                monkeypatch.setenv(IDENTIFIER_ENV, raw)
            else:
                monkeypatch.delenv(IDENTIFIER_ENV, raising=False)
            assert identifier_tokens_enabled() is expected, raw


# ── criterion 2: round-trip agreement (the falsifier's instrument) ─────────

class TestRoundTrip:
    """The write path and the read path must derive the SAME form.

    If they ever disagree, every identifier query silently returns nothing and
    the failure reads as "search is bad" rather than "the halves disagree" —
    the ruling's named risk. Both sides derive through `identifier_alias`, so a
    divergence can only come from a second implementation, which this class
    makes visible.
    """

    @pytest.mark.parametrize("spelling,expected", [
        ("ERR-4032", "err4032"),
        ("err-4032", "err4032"),
        ("Err_4032", "err4032"),
        ("INV-2024-0091", "inv20240091"),
        ("capstone-9664", "capstone9664"),
        ("ISO-9001", "iso9001"),
        ("COVID-19", "covid19"),
    ])
    def test_write_and_read_agree_on_every_separated_spelling(
            self, spelling, expected):
        """The write side is exercised through its ACTUAL door
        (`point_identifier_aliases`, the function `_upsert_point_props` calls on
        the payload), not by re-calling the shared helper with
        `space_separated=False`: a second normaliser introduced inside the
        write-side wrapper — one that kept the separators, or one that also
        accepted the space form — passes a comparison of `identifier_aliases`
        with itself, and then every identifier query silently matches nothing.
        """
        write_side = point_identifier_aliases({"content": spelling})
        read_side = identifier_aliases(spelling)
        assert write_side == read_side == [expected], (write_side, read_side)

    @pytest.mark.parametrize("spelling,expected", [
        ("ERR-4032", "err4032"),
        ("INV-2024-0091", "inv20240091"),
        ("capstone-9664", "capstone9664"),
    ])
    def test_write_and_read_agree_through_the_shared_point_writer(
            self, spelling, expected):
        """The same invariant at the OTHER end of the write path: what the
        writer actually puts in the graph. A journal payload whose
        `search_keys` is a LIST (the E3 spelling the SDK doors send) and one
        that is already FLAT must derive the same aliases, and the append must
        be idempotent — a second application IS the replay path.
        """
        listed = {"content": f"error {spelling} on deploy",
                  "search_keys": ["release", "deploy"]}
        flattened = dict(listed)
        flatten_search_keys_prop(flattened)

        def writer_view(props):
            view = dict(props)
            aliases = point_identifier_aliases(props)
            flatten_search_keys_prop(view)
            append_identifier_aliases(view, aliases)
            append_identifier_aliases(view, point_identifier_aliases(props))
            return view["search_keys"], listed["search_keys"]

        got_listed, untouched = writer_view(listed)
        got_flattened, _ = writer_view(flattened)
        assert got_listed == got_flattened, (got_listed, got_flattened)
        assert got_listed.split() == ["release", "deploy", expected], got_listed
        assert untouched == ["release", "deploy"], untouched

    @pytest.mark.parametrize("typed", ["ERR-4032", "ERR 4032", "ERR\t4032",
                                       "err4032", "Err_4032", "err4032"])
    def test_however_the_person_typed_it_meets_at_one_token(self, typed):
        """The ruling's requirement: "it matches however the person typed it —
        dashes, spaces, or none". A no-separator spelling needs no normalising
        at all: it is already a single index token.

        ``ERR\\t4032`` is in this table because TAB is a whitespace spelling a
        person (and a paste out of a table) really produces, and the read-side
        extractor already admits it (``_SPACED_PAIR_RE``'s ``[ \\t]+``).
        #10203 review round 11 (P2): the ONE normaliser's separator class had
        no TAB, so the extractor yielded the span and the normaliser refused it
        — a candidate extracted and then silently thrown away.
        """
        tokens = tokenize_sparse_query(typed)
        assert "err4032" in tokens, tokens

    def test_write_side_does_not_mint_aliases_from_prose(self):
        """The write side is the PRECISE half: a document saying "sprint 2024"
        must not spend index space on `sprint2024`, and a date is not a code."""
        for prose in ("sprint 2024 planning", "date 2024-01-15",
                      "state-of-the-art", "self-hosted", "B2B"):
            assert identifier_aliases(prose, space_separated=False) == [], prose

    def test_a_date_or_time_is_never_an_identifier(self):
        """#2070's numeric-noise inputs keep their treatment: a span that
        STARTS with a digit is never a candidate."""
        assert identifier_alias("2024-01-15") is None
        assert identifier_alias("27-12") is None
        assert tokenize_sparse_query("time is 27:12") == ["time"]

    @pytest.mark.parametrize("prose", [
        "meeting 27:12",        # TIME
        "spent 27.5 hours",     # DECIMAL
        "budget 12,000 total",  # thousands separator
        "rate 50% today",       # percentage
        "deadline 2024/12/01",  # slash date
    ])
    def test_a_punctuated_quantity_after_a_word_mints_nothing(self, prose):
        """#10203 review round 7 (F3): the space form's punctuation guard must
        cover every separator that makes the digit run the HEAD of a bigger
        token, not just ``-``/``_``.

        The assertion above (``time is 27:12``) could not see this: its letters
        half is the stopword ``is``, so ``_spaced_pair_spans`` drops the span
        BEFORE the guard is consulted — it passes with the guard removed
        entirely. These fixtures use a non-stopword letters half, which is what
        reaches it.

        (1) Fails on the pre-fix guard ``(?![-_])``: each of these minted an
            alias (``meeting27``) and an adjacency phrase (``"meeting 27"``),
            so the ruling's "``27:12`` and ``$40`` contribute none" claim broke
            through this side door — and the phrase then boosted a document
            whose PROSE merely contains the pair.
        (2) Reachable: each shape is ordinary prose a user queries with.
        """
        assert identifier_aliases(prose) == [], prose
        assert identifier_phrase_terms(prose) == [], prose

    def test_a_sentence_final_code_still_mints(self):
        """The guard is ``<punct><digit>``, NOT a bare ``<punct>``: a
        sentence-final ``deploy ERR 4032.`` must keep minting ``err4032``.

        This is the counterweight to the test above — widening the guard must
        not swallow the ordinary shape it exists beside.
        """
        assert identifier_aliases("deploy ERR 4032.") == ["err4032"]
        assert identifier_phrase_terms("deploy ERR 4032.") == ['"err 4032"']
        assert identifier_aliases("deploy err 4032, then retry") == ["err4032"]

    def test_leading_zeros_are_preserved_not_normalised_away(self):
        """INV-2024-0091 vs INV-2024-91 are DIFFERENT codes; the alias must not
        collapse them (leading zeros carry identity)."""
        assert identifier_aliases("INV-2024-0091") == ["inv20240091"]
        assert identifier_aliases("INV-2024-91") == ["inv202491"]

    def test_unicode_adjacent_spans_are_not_identifiers(self):
        """The grammar is ASCII-conservative (ruling risk 1): an accented span
        mints no alias, and its plain tokens are untouched."""
        assert identifier_aliases("Ñ-123") == []
        assert identifier_aliases("Ñ-123", space_separated=False) == []

    def test_bounds_refuse_a_pathological_span(self):
        assert identifier_alias("x-" + "9" * MAX_IDENTIFIER_LEN) is None
        assert identifier_alias("a-1") is None          # one-digit code: weak
        assert identifier_alias(
            "a-11-b-22-c-33-d-44-e-55-f-66") is None    # > MAX segments

    def test_a_one_char_segment_keeps_the_alias_but_drops_the_phrase(self):
        """`A-4032` normalises fine, but the phrase cannot require a 1-char
        token the index discards — so the alias carries it alone."""
        assert identifier_aliases("x A-4032 y") == ["a4032"]
        assert identifier_phrase_terms("x A-4032 y") == []

    @pytest.mark.parametrize("query,alias", [
        ("A-4032", "a4032"), ("x-4032", "x4032"), ("q-4711", "q4711")])
    def test_the_lone_alias_reaches_the_or_union(self, query, alias):
        """The alias "carrying it alone" must be true of the EMITTED query, not
        just of `identifier_aliases` (#10203 review P3-1).

        A span with a 1-char segment yields an alias and NO phrase, so it is the
        only identifier term there is; the degenerate predicate counted terms
        and, seeing exactly one, took the escaped-RAW branch and returned the
        escaped literal `A-4032` — a term the index never stores, so the query matched
        nothing and the alias was silently discarded. Every other token in these
        queries is <2 chars or all-digit, which is what reproduces it.
        """
        assert alias in build_or_query(query).split("|"), build_or_query(query)

    def test_no_identifier_term_is_ever_discarded_by_the_raw_branch(self):
        """The general form of the pin above: while ANY identifier term exists,
        `build_or_query` must not fall back to the escaped literal.
        """
        for query in ("A-4032", "x-4032", "q-4711", "ab-12", "z 99"):
            aliases = identifier_aliases(query)
            phrases = identifier_phrase_terms(query)
            if not (aliases or phrases):
                continue
            emitted = build_or_query(query).split("|")
            assert set(aliases) | set(phrases) <= set(emitted), (query, emitted)


# ── criterion 4: raw retrieval unchanged ───────────────────────────────────

class TestRawRetrieval:
    """The #2070 controls and the "a query that matched before still matches"
    half of the ruling."""

    def test_non_identifier_query_is_byte_identical(self):
        """The canonical non-identifier query keeps its exact pre-#8933 bytes
        (the string `tests/test_sparse.py` pins for the OR-union path)."""
        assert build_or_query("what is ava's favorite board game?") == \
            "favorite|board|game|ava"

    @pytest.mark.parametrize("query,today", [
        ("top 10", "top|top10|\"top 10\""),
        ("step 10 failed", "failed|step|step10|\"step 10\""),
        ("invoice 2024", "invoice|invoice2024|\"invoice 2024\""),
    ])
    def test_the_space_typed_pair_is_a_MEASURED_cost_not_a_silent_one(
            self, query, today):
        """R1's honest boundary: byte-identity holds for a query with no
        identifier SHAPE, but `<word> <2+ digits>` IS a shape this change reads
        as an identifier — the ruling requires that a code "matches however the
        person typed it — dashes, spaces, or none", so `ERR 4032` cannot be
        distinguished syntactically from `top 10`.

        The consequence is pinned here rather than left to be discovered: such
        a query gains an alias term AND an adjacency phrase, so a document whose
        PROSE happens to contain the pair can outrank one that matches all of
        the query's words (#10203 review, measured on a live FalkorDB). That is
        a cost of the ruling's space form, not an accident — a test that
        reds the day someone narrows it, so narrowing is a decision rather
        than a diff.
        """
        assert build_or_query(query) == today

    @pytest.mark.parametrize("sep", [" ", "\t"])
    def test_a_TAB_separated_pair_is_the_VERBATIM_space_one(self, sep):
        """#10203 review round 11 (P2): the extractor/normaliser TAB drift.

        ``_SPACED_PAIR_RE`` admits ``[ \\t]+`` on purpose (a code pasted out of
        a table or a spreadsheet is tab-separated), but ``_IDENTIFIER_SEP_RE`` —
        the ONE normaliser's separator class — had no tab. So
        ``_identifier_segments("invoice\\t2024")`` returned ONE segment,
        ``identifier_alias`` bailed at ``len(segments) < 2``, and the span the
        extractor had just produced was discarded: no alias, no phrase.

        The defect that produced is the #8933 defect itself, re-entering through
        the one whitespace spelling the read side exists to accept —
        ``deploy invoice<TAB>2024`` and ``deploy invoice<TAB>2023`` both became
        the OR union ``invoice|deploy``, i.e. two codes one digit apart were ONE
        query (pre-fix, measured).

        (1) Fails on the pre-fix code: the ``\t`` case yields no alias/phrase
            and the two years produce identical query bytes; the ``sep=" "``
            case passes, which is the point — TAB must be the same spelling.
        (2) Reachable: pasting a code with a tab is the shape ``[ \\t]+`` exists
            for, and no other test covers a tab.
        """
        pair = f"deploy invoice{sep}2024"
        other = f"deploy invoice{sep}2023"
        assert identifier_aliases(pair) == ["invoice2024"], pair
        assert identifier_phrase_terms(pair) == ['"invoice 2024"'], pair
        assert "invoice2024" in build_or_query(pair).split("|")
        assert build_or_query(pair) != build_or_query(other), (
            "two codes one digit apart collapsed to the same query through a "
            f"{sep!r} separator")
        # The WRITE side is unchanged by this: a document whose prose carries
        # the pair in either whitespace spelling still mints no index alias.
        assert identifier_aliases(
            f"sprint{sep}2024 planning", space_separated=False) == [], sep

    def test_the_identifier_tail_is_BOUNDED(self):
        """The OR budget must stay a bound. `max_terms` + the expansion tail is
        not the whole budget once the identifier tail rides it, so each tail is
        capped: a pathological query cannot mint an input-proportional query
        string (and an over-limit query is recorded as an FTS FAILURE, which
        degrades later searches, not just this one).
        """
        from tortoise.sparse import MAX_ALIASES_PER_POINT

        fat = " ".join(f"code-{i:04d}" for i in range(500))
        terms = build_or_query(fat).split("|")
        assert len(terms) <= 12 + 2 * MAX_ALIASES_PER_POINT, len(terms)
        phrases = [t for t in terms if t.startswith('"')]
        assert len(phrases) <= MAX_ALIASES_PER_POINT, len(phrases)

    @pytest.mark.parametrize("query", [
        "time is 27:12", "price was $40", "how much did I spend at $40?",
        "date 2024-01-15", "on 2024-01-15 the deploy failed",
    ])
    def test_no_bare_numeric_token_reaches_the_search_lane(self, query):
        """R3: the #2070 control is NOT silently reversed. Whatever the
        identifier mechanism emits, no all-digit token becomes an OR term.

        #10203 review round 6 (P2): the first version of this test was
        MUTATION-BLIND — every parametrised query carried no identifier at all,
        so it passed unchanged with the mechanism removed, and its
        `.replace('"', "|")` pre-processing made the quote guard below dead
        code (a phrase would have split into its segments). It now also
        asserts the MECHANISM IS PRESENT on an identifier query (so the query
        under test is a real one) and compares against the `keep_numeric=True`
        lane, parsing with the quote-aware splitter.
        """
        terms = _or_terms(build_or_query(query))
        assert not any(t.isdigit() for t in terms), terms

        ident = "time is 27:12 for ERR-4032"
        with_num = _or_terms(build_or_query(ident, keep_numeric=True))
        without = _or_terms(build_or_query(ident))
        assert "err4032" in without, without
        assert "4032" not in without, without
        # The identifier's digits DO reach the numeric lane only when the
        # caller asks for numerics, and then they are the #2070 flag's doing.
        assert "4032" in with_num, with_num

    def test_a_one_character_prefix_matches_however_it_is_typed(self):
        """#10203 review round 6 (P2): the two extractors disagreed on
        one-character prefixes. ``X-4032`` minted the alias ``x4032`` on the
        write side while ``X 4032`` returned NOTHING (the degenerate escaped-RAW
        branch, which matches zero rows) — breaking the ruling's promise that
        the alias matches "however the person typed it — dashes, spaces, or
        none".

        (1) Fails if the read-side extractor narrows its letters segment again.
        (2) Reachable: two spellings of the same one-letter-prefixed code.
        """
        for typed in ("X-4032", "X 4032", "x4032"):
            q = build_or_query(typed)
            assert "x4032" in _or_terms(q), (typed, q)
        assert identifier_aliases("X-4032", space_separated=False) == ["x4032"]

    def test_search_lane_numeric_defaults_are_unchanged(self):
        """Mirrors the #2070 pin: the product search lane's defaults did not
        move — the identifier mechanism is a separate, flag-gated path."""
        from tortoise.search_engine import run_fts_query
        sig = inspect.signature(run_fts_query)
        assert sig.parameters["keep_numeric"].default is False
        assert sig.parameters["expansion_terms"].default is None

    def test_identifier_terms_never_consume_an_original_token_slot(self):
        """R1: the A4 reserved-slot guard. A long alias must never displace a
        shorter ORIGINAL query token — the identifier terms ride the TAIL."""
        originals = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
                     "golf", "hotel", "india", "juliet", "kilo", "lima"]
        q = build_or_query(" ".join(originals) + " ERR-4032")
        terms = q.split("|")
        # The OR-union sorts by length DESC (specificity), so the first 12
        # slots are the 12 original tokens in that order — never the alias.
        assert sorted(terms[:12]) == sorted(originals), q
        assert len(terms[:12]) == len(set(terms[:12])) == 12, q
        assert "err4032" in terms[12:], q
        assert '"err 4032"' in terms[12:], q

    def test_a_mixed_alphanumeric_identifier_keeps_its_own_tokens(self):
        """`cus_9fA2bL` already survived whole; the alias is ADDITIVE and the
        original tokens are still emitted."""
        tokens = tokenize_sparse_query("customer cus_9fA2bL failed")
        assert tokens[:3] == ["customer", "cus", "9fa2bl"], tokens


# ── the DOCUMENT half of the asymmetry (#10203 round 10, F1) ──────────────

class TestDocumentSideLexicalLeg:
    """The in-process BM25 leg tokensises the CORPUS, and documents are INDEXED
    TEXT — so they take the WRITE half of the asymmetry, not the read half.

    The defect these pin (`tortoise/bm25.py:218` pre-fix): `bm25_scores`
    tokenised every document through `bm25_tokens`' read-side default, so the
    prose "sprint 2024 planning" minted `sprint2024` into the corpus token bag —
    the exact token `point_identifier_aliases` (`space_separated=False`)
    deliberately refuses to store. A prose document then became
    indistinguishable from an `sprint-2024` one, the BM25 leg returned a hit the
    graph lane's own fulltext index cannot return for the same document, and the
    bogus token entered `df`/`idf` and `avgdl` (shifting the length
    normalisation of EVERY score in the corpus).

    Non-vacuity is proven in the tests themselves, not by the pre-fix run alone:
    each one also asserts the read-side list DOES carry the token, i.e. that the
    failing condition is reachable and the equality below is not passable by an
    all-empty result.
    """

    PROSE = "sprint 2024 planning"
    CODE = "sprint-2024 planning"

    def test_a_prose_pair_never_mints_a_document_token(self):
        """Mutation: tokenise docs with the read-side default → RED.

        The read side is asserted PRESENT on the same input, so a blanket
        "stop emitting aliases" regression cannot pass this test either.
        """
        read_side = bm25_tokens(self.PROSE)
        assert "sprint2024" in read_side, read_side
        doc_side = bm25_tokens(self.PROSE, space_separated_identifiers=False)
        assert "sprint2024" not in doc_side, doc_side
        assert doc_side == ["sprint", "planning"], doc_side

    def test_the_document_side_matches_the_write_path_exactly(self):
        """One rule, two consumers: the alias set the lexical leg derives from a
        document must equal the set the Point writer stored for it."""
        for text in (self.PROSE, self.CODE, "failure ERR-4032",
                     "INV-2024-0091 broke", "spent 27.5 hours",
                     "meeting 27:12 covers rollout"):
            write_path = point_identifier_aliases({"content": text})
            read_side = identifier_aliases(text)
            doc_side = identifier_aliases(text, space_separated=False)
            assert doc_side == write_path, (text, doc_side, write_path)
            # ... and the leg's own doc tokens carry exactly those aliases.
            toks = bm25_tokens(text, space_separated_identifiers=False)
            assert [t for t in toks if t in set(read_side)] == doc_side, text

    def test_the_alias_query_no_longer_hits_the_prose_document(self):
        """Mutation: pass the read-side default at ``bm25_scores``' document
        call → the prose document scores and this assertion flips to RED."""
        prose = [("prose", self.PROSE)]
        assert bm25_scores("sprint2024", prose) == {}, (
            "a document that merely SAYS the words must not answer the alias "
            "query — the graph lane's index holds no such token for it"
        )
        # Non-vacuity: the same scorer DOES hit once the token is really there.
        assert bm25_scores("sprint2024", [("prose", self.PROSE + " sprint2024")])

    def test_the_identifier_document_still_answers_the_alias_query(self):
        """The fix must keep the discrimination #8933 exists for: an
        `sprint-2024` document (whose writer stored `sprint2024`) still hits,
        and ranks above the prose one when both share the plain terms."""
        ranked = dict(bm25_rank("sprint2024", [
            ("prose", self.PROSE), ("code", self.CODE)]))
        assert set(ranked) == {"code"}, ranked
        # A query that ALSO carries the plain words ranks the code document
        # above the prose one — the alias term is the only difference.
        order = [pid for pid, _ in bm25_rank(
            "sprint2024 planning", [("prose", self.PROSE),
                                    ("code", self.CODE)])]
        assert order[:1] == ["code"], order
        assert "prose" in order, order


class TestExpansionSideSearchKeys:
    """The A4 PRF expansion (`tortoise.sparse.expansion_tokens`) tokenises the
    retrieved pool's harvested `search_keys` — text that ALREADY SITS IN THE
    INDEX — so it takes the WRITE half of the asymmetry too.

    The defect these pin (#10203 CI, shard `test (c)`): `expansion_tokens`
    tokenised each harvested chunk through the READ-side default, so the base
    graph's `couch ikea 800 dollars` alias minted `ikea800` into the SECOND
    FTS pass. That is a QUERY shape the ruling widened for what a person types
    ("matches however the person typed it"), never for text already indexed —
    and because the expansion tail is sorted longest-first into a BOUNDED slot
    budget (`DEFAULT_MAX_EXPANSION_TERMS`), the spurious 7-char composite
    displaced a legitimate term and a golden row fell out of the top-k. The
    flag-OFF golden (`tests/test_assembly_sdk.py::test_flag_off_golden_byte
    _identity`) is where that surfaced; these are the unit-level pins, on the
    embedded lane, so the surface cannot re-open without a live FalkorDB.

    Non-vacuity is proven inside each test: the read-side list is asserted
    PRESENT on the same input (the failing condition is reachable), and the
    ask lane's numeric policy is asserted UNCHANGED, so a blanket "stop
    emitting tokens" regression cannot pass here either.
    """

    PROSE = "couch ikea 800 dollars"
    IDENT = "failure ERR-4032"

    def test_a_prose_pair_never_mints_an_expansion_token(self):
        """Mutation: drop ``space_separated_identifiers=False`` at the
        ``expansion_tokens`` call site → RED."""
        assert "ikea800" in identifier_aliases(self.PROSE), (
            "the read side must still mint it — otherwise this test is "
            "vacuous rather than discriminating")
        exp = expansion_tokens(self.PROSE)
        assert "ikea800" not in exp, exp
        # The plain tokens survive the tightening, and `keep_numeric=True`
        # (the A4 numeric policy) is untouched — this flag only removes the
        # alias, it does not silence the call site.
        assert {"couch", "ikea", "dollars", "800"} <= set(exp), exp

    def test_no_expansion_alias_is_one_the_write_path_refuses(self):
        """The invariant, stated as an equality rather than a blacklist: the
        aliases the expansion carries are exactly the ones the Point writer
        stored for that chunk."""
        for text in (self.PROSE, self.IDENT, "sprint 2024 planning",
                     "INV-2024-0091 broke", "meeting 27:12 covers rollout",
                     "spent 27.5 hours"):
            read_side = set(identifier_aliases(text))
            carried = {t for t in expansion_tokens(text, max_terms=64)
                       if t in read_side}
            stored = set(point_identifier_aliases({"content": text}))
            assert carried == stored, (text, carried, stored)
            assert carried <= read_side, text

    def test_an_identifier_shaped_chunk_still_contributes_its_alias(self):
        """The fix must not cost #8933 anything: an indexed `ERR-4032` chunk
        still expands to the write-side alias the index really holds."""
        exp = expansion_tokens(self.IDENT)
        assert "err4032" in exp, exp
        assert exp == ["failure", "err4032", "4032", "err"], exp
        # Array-valued `search_keys` (the extractor writes a flat string after
        # `_flatten_search_keys_prop`, but raw graph values may be arrays).
        assert "err4032" in expansion_tokens([self.IDENT, self.PROSE])

    def test_the_spurious_alias_cannot_displace_a_bounded_slot(self):
        """The HARM, made mechanical: the expansion tail is capped and sorted
        longest-first, so a READ-side composite outranks the real 4-char term
        `ikea` and takes its slot. Pre-fix with a 4-slot budget the output was
        `['dollars', 'ikea800', 'couch', 'ikea']` — `800` already gone and a
        token no document carries holding a slot."""
        bounded = expansion_tokens(self.PROSE, max_terms=4)
        assert "ikea800" not in bounded, bounded
        assert bounded == ["dollars", "couch", "ikea", "800"], bounded
        # Reachability of the displacement itself, so the assertion above is
        # not merely "the list is short".
        read_side_tail = sorted(
            [*identifier_aliases(self.PROSE), "couch", "ikea", "800"],
            key=len, reverse=True)[:4]
        assert "ikea800" in read_side_tail, read_side_tail


# ── criterion 2 (write side): the shared writer door ───────────────────────

class TestWritePath:
    """Stage 2 on the SHARED Point writer — the door every write reaches
    (SDK doors, the EventAPI/extractor seam, live apply and rebuild replay)."""

    def test_append_is_idempotent(self):
        """R6: re-derivation is a NO-OP — a replay, a rebuild or a second call
        cannot grow the value without bound."""
        from tortoise.projection.entities import (
            append_identifier_aliases,
            flatten_search_keys_prop,
            point_identifier_aliases,
        )
        props = {"content": "error ERR-4032 on deploy",
                 "search_keys": ["release", "deploy"]}
        aliases = point_identifier_aliases(props)
        flatten_search_keys_prop(props)
        for _ in range(3):
            append_identifier_aliases(props, aliases)
            append_identifier_aliases(props, point_identifier_aliases(props))
        assert props["search_keys"] == "release deploy err4032", props

    def test_no_identifier_takes_the_pre_8933_path(self):
        """R5: a plain point is untouched — no `search_keys` key is invented,
        so nothing about its indexed text changes."""
        from tortoise.projection.entities import point_identifier_aliases
        assert point_identifier_aliases(
            {"content": "the gym schedule for tuesday"}) == []
        assert point_identifier_aliases({"content": ""}) == []
        assert point_identifier_aliases({}) == []

    def test_redacted_span_contributes_no_secret_alias(self):
        """R4 (ruling risk 2): the alias is derived from text that has ALREADY
        been scrubbed, and a scrub REPLACES the whole matched value — so no
        secret bytes survive into the aliased form."""
        from tortoise.projection.entities import point_identifier_aliases
        from tortoise.security import redact_secrets

        # Assembled from fragments on purpose: a literal credential-shaped
        # string here trips GitHub push protection on this repo, and the point
        # of the fixture is a value the REDACTOR recognises, not a real key.
        stripe_like = "sk_" + "live_" + "51H8xQ" + "2eZvKYlo2C9abcdefghij"
        raw = f"deploy key {stripe_like} failure ERR-4032 on deploy"
        scrubbed, _redacted = redact_secrets(raw)
        assert stripe_like not in scrubbed, scrubbed
        assert "[REDACTED:" in scrubbed, scrubbed
        aliases = point_identifier_aliases({"content": scrubbed})
        assert aliases == ["err4032"], aliases
        for alias in aliases:
            for fragment in ("sklive", "51h8xq", "2ezvkylo", "abcdefghij"):
                assert fragment not in alias, (fragment, alias)

    def test_switched_off_writes_no_alias(self, monkeypatch):
        from tortoise.projection.entities import point_identifier_aliases
        monkeypatch.setenv(IDENTIFIER_ENV, "0")
        assert point_identifier_aliases({"content": "error ERR-4032"}) == []

    def test_journal_payload_is_never_mutated(self):
        """The alias is DERIVED, not recorded: the payload's own list spelling
        must survive, or a replay would apply a value the journal never held."""
        from tortoise.projection.entities import (
            append_identifier_aliases,
            flatten_search_keys_prop,
            point_identifier_aliases,
        )
        payload = {"content": "ERR-4032", "search_keys": ["alpha", "beta"]}
        aliases = point_identifier_aliases(payload)
        copy = dict(payload)
        flatten_search_keys_prop(copy)
        append_identifier_aliases(copy, aliases)
        assert payload["search_keys"] == ["alpha", "beta"], payload

    def test_the_sdk_create_door_derives_and_stays_equal_to_the_replay(
            self, tmp_path):
        """#10203 review round 7 (F1, blocking): the door the SESSION CAPTURE
        mints through must derive the alias, and live must equal ``replay(journal)``.

        ``create_point`` is the door ``_capture_session_impl`` calls, and it
        only ever ran ``_flatten_search_keys_prop`` — the alias was written by
        ``_upsert_point_props`` (the REPLAY door, `apply`) alone, so a captured
        Point indexed ``err`` + ``4032`` but never ``err4032``. The ruling's
        "dashes, spaces, or none" promise therefore did not hold for the
        primary corpus, and this change INTRODUCED live ≠ replay: the rebuilt
        node carried the alias, the live one did not, and the strip this PR
        adds to ``check_consistency`` hid the asymmetry from the detector that
        exists to catch it.

        (1) Fails on the pre-fix door in two ways: the live node's
            ``search_keys`` is ``None`` (nothing derived), and that ``None``
            differs from the replayed ``err4032`` (the parity assertion).
        (2) Reachable: this is the ordinary product write, not a raw Cypher
            fixture — the discrimination test's corpus is built with raw Cypher
            and so could never see the door at all.
        """
        import uuid

        from tortoise.log import EventLog
        from tortoise.projection import FalkorProjection
        from tortoise.sdk import TortoiseSDK

        log_path = str(tmp_path / "capture.jsonl")
        sdk = TortoiseSDK(
            os.path.join(str(tmp_path), "capture.db"),
            namespace=f"test_ident_capture_{uuid.uuid4().hex[:8]}",
            event_log_path=log_path,
        )
        try:
            pid = sdk.create_point(
                content="error ERR-4032 on deploy", kind="statement")["id"]

            def keys_of(graph):
                rows = graph.query(
                    f"MATCH (n:Point {{id:'{pid}'}}) RETURN n.search_keys"
                ).result_set
                return rows[0][0] if rows else None

            live = keys_of(sdk._get_proj().g)
            assert live is not None and "err4032" in str(live).split(), (
                "the SDK create door — the session-capture mint's door — did "
                f"not derive the alias; node holds {live!r}")
            # An aliased spelling anywhere else must not be able to pass: the
            # query typed with NO separator is what the alias exists to serve.
            assert "err4032" in build_or_query("err4032").split(), live

            replayed = FalkorProjection(
                os.path.join(str(tmp_path), "replay.db"),
                graph_name=f"test_ident_replay_{uuid.uuid4().hex[:8]}")
            try:
                for event in EventLog(log_path).read_all():
                    if event.get("type") == "PointAdded":
                        replayed.apply(event)
                assert keys_of(replayed.g) == live, (
                    "derived != replay(journal) on `search_keys`: the live "
                    "create and the rebuild disagree, so the alias is not a "
                    "pure function of the record")
            finally:
                replayed.close()

            # Derived, never recorded: the journal keeps E3's spelling, which
            # is what lets a rebuild re-derive instead of trusting a value.
            for event in EventLog(log_path).read_all():
                point = event.get("point") or {}
                assert "err4032" not in str(point.get("search_keys") or ""), (
                    "the derived alias reached the JOURNAL: "
                    f"{point.get('search_keys')!r}")
        finally:
            sdk.close()

    def test_the_sdk_update_door_does_not_JOURNAL_the_derived_alias(
            self, tmp_path):
        """#10203 review round 8 (F1/P2): the derived alias must not reach the
        record from the REVISE door either.

        ``update_point`` recomputes ``search_keys`` on a content edit and put the
        result into ``emit_props``, which strips only ``content_hash`` (#1904)
        and the derived vector (#4208). So the ``PointRevised`` record carried a
        producer claim the replay does not honour: ``_revise_point`` re-derives
        from ``new_content`` and the node's own previous keys and never reads
        ``ev["search_keys"]``. That made "derived, never recorded" — the PR
        body, ``.env.example``, ``point_identifier_aliases`` and the create
        door's own snapshot — false from this door onward, and ``search_keys``
        is a COLUMN in ``public.point``, so the derived value became
        authoritative in the record lane (``record_projection`` patches columns
        from the flat ``NodePatch``), which that module's doctrine forbids.

        The create door already had this pin
        (``test_the_sdk_create_door_derives_and_stays_equal_to_the_replay``);
        the revise door had none, because its test only read the NODE.

        (1) Fails on the pre-fix door: the ``PointRevised`` row carries
            ``search_keys='err4032'``. Fails too if the fix over-reaches and
            stops the live NODE from holding the alias (the parity half).
        (2) Reachable: the ordinary SDK create + content edit, with a real
            JSONL journal, replayed through the projection's own revise fold.
        """
        import uuid

        from tortoise.log import EventLog
        from tortoise.projection import FalkorProjection
        from tortoise.sdk import TortoiseSDK

        log_path = str(tmp_path / "revise.jsonl")
        sdk = TortoiseSDK(
            os.path.join(str(tmp_path), "revise.db"),
            namespace=f"test_ident_revise_{uuid.uuid4().hex[:8]}",
            event_log_path=log_path,
        )
        try:
            pid = sdk.create_point(
                content="release notes", kind="statement")["id"]
            sdk.update_point(pid, content="error ERR-4032 on deploy")

            def keys_of(graph):
                rows = graph.query(
                    f"MATCH (n:Point {{id:'{pid}'}}) RETURN n.search_keys"
                ).result_set
                return rows[0][0] if rows else None

            live = keys_of(sdk._proj.g)
            assert live is not None and "err4032" in str(live).split(), live

            events = EventLog(log_path).read_all()
            revised = [e for e in events if e.get("type") == "PointRevised"]
            assert revised, events
            for event in revised:
                assert "err4032" not in str(event.get("search_keys") or ""), (
                    "the DERIVED alias reached the journal from the revise "
                    f"door: {event.get('search_keys')!r}")

            replayed = FalkorProjection(
                os.path.join(str(tmp_path), "revise_replay.db"),
                graph_name=f"test_ident_revise_rp_{uuid.uuid4().hex[:8]}")
            try:
                for event in events:
                    if event.get("type") in ("PointAdded", "PointRevised"):
                        replayed.apply(event)
                assert keys_of(replayed.g) == live, (
                    "derived != replay(journal) on `search_keys` for the "
                    "revise door: the live edit and the rebuild disagree")
            finally:
                replayed.close()
        finally:
            sdk.close()

    def test_a_CALLER_supplied_search_keys_still_rides_the_record(
            self, tmp_path):
        """The strip is scoped to the DERIVED value only.

        A caller-supplied ``search_keys`` on a revision is not recomputable by a
        replay (the value exists only in the call), it rode ``PointRevised``
        before #8933 too, and the #5046 caller-owned parity gap is a separate
        recorded decision — so this fix must not silence it.

        (1) Fails if the strip is written as an unconditional
            ``k != "search_keys"``.
        (2) Reachable: a revision that supplies its own key.
        """
        from tortoise.log import EventLog
        from tortoise.sdk import TortoiseSDK

        log_path = str(tmp_path / "caller.jsonl")
        sdk = TortoiseSDK(
            os.path.join(str(tmp_path), "caller.db"),
            namespace="test_ident_caller_keys",
            event_log_path=log_path,
        )
        try:
            pid = sdk.create_point(
                content="release notes", kind="statement")["id"]
            sdk.update_point(pid, content="tick second",
                             search_keys=["customkey"])
            revised = [e for e in EventLog(log_path).read_all()
                       if e.get("type") == "PointRevised"]
            assert revised, "the revision appended no record at all"
            assert "customkey" in str(revised[-1].get("search_keys") or ""), (
                "a CALLER-supplied search_keys was stripped from the record: "
                f"{revised[-1].get('search_keys')!r}")
        finally:
            sdk.close()

    def test_an_explicit_search_keys_clear_is_not_overwritten_by_the_derivation(
            self, tmp_path):
        """#10203 round 12 (P3): an explicit ``search_keys=None`` IS a clear.

        ``props.get("search_keys")`` cannot tell an explicit ``None`` from an
        ABSENT key, so a content edit combined with the documented
        ``None``-clears-a-property shape (the same one ``tags`` and
        ``embedding`` already use in this door) was REPLACED by the recomputed
        derived value — which keeps the node's previous non-alias keys. The
        caller was told the write landed while the vocabulary it asked to drop
        survived on the node and in the FTS index: the stale-index-value class,
        reached through argument-default ambiguity rather than a bad clause.

        (1) Fails on the pre-fix gate with ``'release deploy'`` (the derivation
            rebuilt the vocabulary the caller cleared) and with a record that
            carries NO ``search_keys`` at all (`_derived_search_keys` came out
            ``True``, so the clear was stripped from both halves).
        (2) Reachable: the ordinary combined call ``update_point(id,
            content=…, search_keys=None)`` — a clear with no content edit never
            enters the derivation at all, which is why only this shape bit.
        """
        from tortoise.log import EventLog
        from tortoise.sdk import TortoiseSDK

        log_path = str(tmp_path / "clear.jsonl")
        sdk = TortoiseSDK(
            os.path.join(str(tmp_path), "clear.db"),
            namespace="test_ident_explicit_clear",
            event_log_path=log_path,
        )
        try:
            pid = sdk.create_point(content="error ERR-4032 on deploy",
                                   kind="statement",
                                   search_keys=["release", "deploy"])["id"]

            def keys():
                rows = sdk._proj.g.query(
                    f"MATCH (n:Point {{id:'{pid}'}}) RETURN n.search_keys"
                ).result_set
                return rows[0][0] if rows else None

            assert keys() == "release deploy err4032", keys()  # PRECONDITION
            sdk.update_point(pid, content="deploy is clean again",
                             search_keys=None)
            assert keys() is None, (
                "the caller's explicit clear was overwritten by the derived "
                f"value — the vocabulary it asked to drop is still indexed: {keys()!r}")
            # The RECORD half: a clear is CALLER intent, not a derived value, so
            # it rides `PointRevised` the way a caller-supplied list does (the
            # sibling above) instead of being stripped with the derived alias.
            revised = [e for e in EventLog(log_path).read_all()
                       if e.get("type") == "PointRevised"]
            assert revised, "the revision appended no record at all"
            assert "search_keys" in revised[-1], (
                "the caller's explicit clear was stripped from the record too: "
                f"{revised[-1]!r}")
            assert revised[-1]["search_keys"] is None, revised[-1]["search_keys"]
        finally:
            sdk.close()


@requires_live
class TestWritePathLive:
    """The same door over a real graph, read back from the node."""

    @pytest.fixture()
    def proj(self):
        import uuid

        from tortoise.projection import FalkorProjection
        p = FalkorProjection.from_uri(
            _LANE_URI, graph_name=f"test_ident8933_{uuid.uuid4().hex[:8]}")
        yield p
        p.close()

    def test_capture_path_stores_the_alias(self, proj):
        proj.apply({"type": "PointAdded",
                    "point": {"id": "p-ident-1",
                              "content": "error ERR-4032 on deploy"}})
        rows = proj.query(
            "MATCH (n:Point {id:'p-ident-1'}) RETURN n.search_keys"
        ).result_set
        assert rows and rows[0][0] == "err4032", rows

    def test_plain_point_stores_no_search_keys(self, proj):
        proj.apply({"type": "PointAdded",
                    "point": {"id": "p-plain-1",
                              "content": "the gym schedule for tuesday"}})
        rows = proj.query(
            "MATCH (n:Point {id:'p-plain-1'}) RETURN n.search_keys"
        ).result_set
        assert rows and rows[0][0] is None, rows

    def _keys(self, proj, pid):
        rows = proj.query(
            f"MATCH (n:Point {{id:'{pid}'}}) RETURN n.search_keys"
        ).result_set
        return rows[0][0] if rows else None

    def test_an_empty_search_keys_list_does_not_replace_the_stored_vocabulary(self, proj):
        """#10203 review round 6 (measured P1): the alias must not become the
        WHOLE value.

        `search_keys: []` is the E3 `PointPayload` DEFAULT spelling, and the
        pre-#8933 writer popped a blank list and wrote nothing (the node kept
        what it held). Materialising the derived alias as the whole value
        turned a stored `"release deploy rollout"` into `"err4032"` — a silent
        loss of the retrieval vocabulary the A4/#2070 expansion leg reads.

        (1) Fails if the not-owned path assigns instead of appends.
        (2) Reachable: one point, two emits, the second carrying the default
            empty list.
        """
        proj.apply({"type": "PointAdded", "point": {
            "id": "p-own-1", "content": "release notes",
            "search_keys": ["release", "deploy", "rollout"]}})
        assert self._keys(proj, "p-own-1") == "release deploy rollout"
        proj.apply({"type": "PointAdded", "point": {
            "id": "p-own-1", "content": "error ERR-4032 on deploy",
            "search_keys": []}})
        stored = self._keys(proj, "p-own-1") or ""
        for token in ("release", "deploy", "rollout", "err4032"):
            assert token in stored.split(), (token, stored)

    def test_an_ARRAY_valued_node_does_not_abort_the_alias_write(self, proj):
        """#10203 review round 8 (F3/P2): the append is TYPE-GUARDED on the node.

        Array-valued ``search_keys`` is a state this repo MAINTAINS, not a
        hypothetical legacy shape: ``flatten_search_keys_prop`` flattens the
        PAYLOAD, a later ``update_entity`` (``SET n += $props``, no flatten,
        #5482) can put an array back, and ``_array_valued_search_keys_exist`` /
        ``_array_search_keys_rows`` exist precisely because such rows can be
        present at any time. ``' ' + <list>`` is not a string expression, so the
        pre-fix clause made the whole ``_persist_extra_props`` statement raise
        ``Type mismatch: expected String or Null but was List`` — a write whose
        only job was to add one alias ABORTED (measured on the embedded lane at
        the previous head, then on both lanes here).

        (1) Fails on the pre-fix clause: the apply raises, so the assertions
            never run. Fails if the guard only survives the raise by DISCARDING
            the array (the vocabulary assertions).
        (2) Reachable: one raw-Cypher point holding an array, then the ordinary
            ``PointAdded`` re-emit whose payload carries no ``search_keys`` —
            the same door the round-6 P1 fix opened.
        """
        proj.g.query(
            "CREATE (n:Point {id:'p-arr-1', content:'plain', "
            "search_keys:['alpha','beta']})")
        proj.apply({"type": "PointAdded", "point": {
            "id": "p-arr-1", "content": "error ERR-4032 on deploy"}})
        stored = self._keys(proj, "p-arr-1") or ""
        assert isinstance(stored, str), (
            "the guard left the node ARRAY-valued (invisible to the fulltext "
            f"index) instead of flattening it: {stored!r}")
        for token in ("alpha", "beta", "err4032"):
            assert token in stored.split(), (token, stored)

    def test_a_content_edit_on_an_ARRAY_valued_node_keeps_its_vocabulary(self, proj):
        """#10203 review round 9 (F1/P1): the REVISE door honours the array state.

        Round 8 type-guarded the ADD door so an array-valued node is not
        aborted and its vocabulary is KEPT. The REVISE door normalised the SAME
        value two ways three lines apart: ``stale`` went through
        ``point_identifier_aliases`` (list-aware, ``:1227-1228``) while ``base``
        took ``old_search_keys if isinstance(old_search_keys, str) else ""`` —
        so every stored token was dropped and the node ended up holding the new
        content's alias ALONE (or lost the property entirely), a silent loss of
        the retrieval vocabulary on a state this repo maintains (#5482's
        ``update_entity`` ``SET n += $props``; a RESTORE/DR graph).

        (1) Fails on the pre-fix code: case A stores ``'err4032'`` instead of
            ``'alpha beta err4032'``, case B removes the property (``None``)
            instead of keeping ``'alpha beta'``.
        (2) Reachable: a raw-Cypher point holding an array, then the ordinary
            revise the SDK / replay door issues for a content edit.
        """
        for pid in ("p-arrev-1", "p-arrev-2"):
            proj.g.query(
                f"CREATE (n:Point {{id:'{pid}', content:'plain', "
                "search_keys:['alpha','beta']})")
        # A — the edit INTRODUCES an identifier: the array's tokens survive and
        #     the alias joins them.
        proj.apply({"type": "PointRevised", "id": "p-arrev-1",
                    "new_content": "error ERR-4032 on deploy"})
        stored = self._keys(proj, "p-arrev-1")
        assert isinstance(stored, str), (
            f"the revise door left the node ARRAY-valued: {stored!r}")
        assert sorted(stored.split()) == ["alpha", "beta", "err4032"], stored
        # B — the edit REMOVES the identifier: the array's tokens survive, the
        #     alias goes, and the property is NOT dropped.
        proj.apply({"type": "PointRevised", "id": "p-arrev-2",
                    "new_content": "deploy is clean again"})
        stored = self._keys(proj, "p-arrev-2")
        assert stored is not None, (
            "a content edit DESTROYED an array-valued search_keys instead of "
            "keeping the tokens it held")
        assert sorted(stored.split()) == ["alpha", "beta"], stored

    def test_a_content_edit_rederives_the_alias_and_retracts_stale_ones(self, proj):
        """#10203 review round 6 (measured P0/P1): a revision is not a capture.

        `update_point` / `_revise_point` write `n.content` and — before this —
        left `search_keys` alone, so an edit that introduced an identifier never
        indexed it and an edit that removed one left a stale alias that still
        matched the old code. The fix is the same rule the door already applies
        to `content_hash` (#1904) and the vector (#4208): recompute the derived
        value in the same write.

        (1) Fails if the revise door stops recomputing (the intro assertion),
            and fails if it stops RETRACTING (the removal assertion).
        (2) Reachable: a creation with its own keys, then two revisions — one
            adding an identifier, one dropping it.
        """
        proj.apply({"type": "PointAdded", "point": {
            "id": "p-rev-1", "content": "release notes",
            "search_keys": ["release", "deploy"]}})
        proj.apply({"type": "PointRevised", "id": "p-rev-1",
                    "new_content": "error ERR-4032 on deploy"})
        assert self._keys(proj, "p-rev-1") == "release deploy err4032"
        proj.apply({"type": "PointRevised", "id": "p-rev-1",
                    "new_content": "deploy is clean again"})
        assert self._keys(proj, "p-rev-1") == "release deploy"

    def test_the_sdk_update_door_rederives_too(self, tmp_path):
        """The SECOND content-edit door. `check_consistency` compares the graph
        against `replay(journal)`, so both doors must produce the SAME value —
        `update_point` writes live (`SET n += $props`) and journals a
        `PointRevised`, which the revise door above folds.

        (1) Fails if the SDK door stops recomputing (the node would keep the
            creation's alias while the replay derives the revision's).
        (2) Reachable: create with keys, edit in an identifier, edit it out,
            edit in a DIFFERENT identifier.
        """
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(db_path=os.path.join(str(tmp_path), "t.db"))
        pid = sdk.create_point(content="release notes", kind="statement",
                               search_keys=["release", "deploy"])["id"]

        def keys():
            rows = sdk._proj.g.query(
                f"MATCH (n:Point {{id:'{pid}'}}) RETURN n.search_keys"
            ).result_set
            return rows[0][0] if rows else None

        sdk.update_point(pid, content="error ERR-4032 on deploy")
        assert keys() == "release deploy err4032", keys()
        sdk.update_point(pid, content="deploy is clean again")
        assert keys() == "release deploy", keys()
        sdk.update_point(pid, content="another ERR-4031 here")
        assert keys() == "release deploy err4031", keys()


# ── criterion 3: discrimination survives on #10121's fixture ───────────────

@requires_live
class TestDiscrimination:
    """On a corpus of look-alike records, the just-written exact match ranks
    FIRST, not last (the #10121 symptom this mechanism exists to relieve)."""

    @pytest.fixture()
    def corpus(self):
        import uuid

        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(
            _LANE_URI, graph_name=f"test_ident8933_disc_{uuid.uuid4().hex[:8]}")
        for n in range(15):
            proj.g.query(
                "CREATE (n:Point {id:$id, content:$c, is_operator:false, "
                "pointKind:'statement'})",
                params={"id": f"p-{n:02d}",
                        "c": f"capstone-{n} write probe 2026-10-2{n}"},
            )
        # The freshly written exact match — id sorts LAST, so a pure tie-break
        # (the #10121 failure) leaves it at the head of nothing.
        proj.g.query(
            "CREATE (n:Point {id:$id, content:$c, is_operator:false, "
            "pointKind:'statement'})",
            params={"id": "p-99", "c": "capstone-9664 write probe 2026-10-26"},
        )
        yield proj
        proj.close()

    def _rank_of(self, proj, query, target):
        from tortoise.search_engine import reset_circuit_breakers, run_fts_query
        reset_circuit_breakers()
        try:
            ids = [row[0] for row in run_fts_query(proj.g, query, limit=25)]
        finally:
            reset_circuit_breakers()
        return ids.index(target) + 1 if target in ids else None

    def _scores(self, proj, query) -> dict[str, float]:
        from tortoise.search_engine import reset_circuit_breakers, run_fts_query
        reset_circuit_breakers()
        try:
            return {row[0]: round(float(row[1]), 6)
                    for row in run_fts_query(proj.g, query, limit=25)}
        finally:
            reset_circuit_breakers()

    def test_the_just_written_exact_match_ranks_first(self, corpus):
        """Measured on this corpus: the fresh exact match is rank 1 of 16 at
        score 3.0, against 2.0 for every look-alike.

        ⚠️ Rank alone is not the proof the sibling test used to claim: the
        product's own ``recency_tie_clause`` breaks a SCORE tie newest-first
        (#10121), so the freshly written point leads a tied group with or
        without this mechanism. The mechanism's contribution is the SCORE, and
        that is what the sibling measures."""
        assert self._rank_of(corpus, "capstone 9664 write probe",
                             "p-99") == 1

    def test_mutation_without_the_mechanism_the_match_has_no_advantage(
            self, corpus, monkeypatch):
        """The mutation proof, restated at #10203 review round 7: with the
        mechanism switched OFF the fresh exact match has NO score advantage
        over the look-alikes.

        This test previously asserted ``rank > 1`` when OFF ("measured here:
        rank 15 of 16"), and that claim is FALSE on the docker lane at this
        head — with the mechanism off, ``run_fts_query`` returns the target
        FIRST. The cause is upstream of #8933 and deliberate:
        ``search_engine.recency_tie_clause`` breaks a score tie newest-first
        (#10121's recorded defect, the reason it is not the id direction), and
        a 3-term OR ties every document in this corpus at 2.0 — so the
        rank-order assertion was measuring the TIE RULE, not the mechanism, and
        reported a mutation that does not exist. It was red at the previous
        head too (``982619da8``), so the PR's recorded "rank 15 of 16" was not
        reproduced by this lane at the head it claimed.

        The honest instrument is the score, which is exactly what the ruling's
        mechanism adds: ON → the target scores 3.0 (alias + adjacency phrase)
        against 2.0 for every look-alike; OFF → every document, the target
        included, scores 2.0 — no advantage, i.e. the shipped pre-#8933
        scoring. Both halves are asserted on the SAME corpus, so the only thing
        differing between them is the env switch.

        (1) Fails if the mechanism stops contributing (the ON gap closes), and
            fails if it stops being switchable (the OFF advantage reappears).
        (2) Reachable: the ON half yields 3.0 vs 2.0 and the OFF half 2.0 vs
            2.0 on this fixture, measured above.
        """
        query = "capstone 9664 write probe"
        monkeypatch.setenv(IDENTIFIER_ENV, "1")
        on = self._scores(corpus, query)
        monkeypatch.setenv(IDENTIFIER_ENV, "0")
        off = self._scores(corpus, query)

        look_alikes = [pid for pid in on if pid != "p-99"]
        assert len(look_alikes) == 15, on
        assert on["p-99"] > max(on[pid] for pid in look_alikes), (
            "the mechanism contributes nothing on this fixture, so the rank pin "
            f"above proves nothing: {on}")
        assert off["p-99"] == max(off[pid] for pid in look_alikes), (
            "with the mechanism OFF the fresh exact match still scores above "
            f"the look-alikes — the mutation is not a mutation: {off}")


# ── the operator contract: the bisect switch must be DISCOVERABLE ──────────

def test_env_example_declares_the_identifier_bisect_switch():
    """#10203 review round 7 (F4, repo rule): ``.env.example`` IS this repo's
    env contract, and the PR calls ``TORTOISE_IDENTIFIER_TOKENS`` "the fail-safe
    bisect switch the ruling asks for" — an operator whose aggregate retrieval
    disappoints must be able to FIND it without reading ``tortoise/sparse.py``.

    The convention is pinned by test for this exact class of switch:
    ``tests/test_bm25_lexical_8488.py::test_env_example_declares_the_bm25_arm_switch``
    and ``tests/test_time_aware_2520.py::test_env_example_declares_the_valid_time_kill_switch``
    (#8487). This is the third of that family.

    (1) Fails on the pre-fix ``.env.example``, which has zero occurrences of the
        name and so nothing for the grep to assert against.
    (2) Reachable: the shipped default is ON, so the switch is the only route
        back to pre-#8933 behaviour without a revert.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    env = (root / ".env.example").read_text()
    assert IDENTIFIER_ENV in env, (
        f"{IDENTIFIER_ENV} (the #8933 fail-safe bisect switch, default ON) is "
        "not declared in .env.example")
    declared = [
        line for line in env.splitlines()
        if line.strip().startswith(f"# {IDENTIFIER_ENV}=")
        or line.strip().startswith(f"{IDENTIFIER_ENV}=")
    ]
    assert declared, (
        f"{IDENTIFIER_ENV} must be shown as a commented assignment (the "
        "example form an operator copies), not just named in prose")


def test_env_example_states_the_TRUE_limit_of_byte_identity():
    """#10203 review round 8 (F1/P2): the operator-facing contract must not
    understate the read side's one behaviour change.

    ``.env.example`` said "A plain non-identifier query is byte-identical to
    before" — and this file's own
    ``TestRawRetrieval::test_the_space_typed_pair_is_a_MEASURED_cost_not_a_silent_one``
    pins the opposite for the space form: ``build_or_query("top 10")`` is
    ``top|top10|"top 10"`` (on ``origin/main``: the raw ``top 10``),
    ``step 10 failed`` -> ``failed|step|step10|"step 10"``, ``invoice 2024`` ->
    ``invoice|invoice2024|"invoice 2024"``. The plan doc §2 already states the
    truth ("Byte-identity holds only for queries with no identifier SHAPE");
    only the operator-facing text understated it.

    Why it matters: the env block is the ONE place an operator decides whether
    to bisect, and the reason the switch exists is "flip it to measure a batch
    whose aggregate retrieval disappoints". Told that the shape is unaffected,
    they attribute such a regression to something else — or flip the switch
    expecting those bytes to move back and cannot explain why they already were.

    (1) Fails on the pre-fix block: the false sentence is present and the
        ``top 10`` example is absent.
    (2) Reachable: both assertions read the shipped ``.env.example``.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    env = (root / ".env.example").read_text()
    start = env.index("── #8933 identifier preservation")
    end = env.index(f"# {IDENTIFIER_ENV}=", start)
    block = env[start:end]

    assert 'A plain non-identifier query is byte-identical to before' not in block, (
        "the env contract claims a plain non-identifier query is byte-identical, "
        "which TestRawRetrieval contradicts for the `<word> <2+ digits>` form")
    assert "identifier SHAPE" in block or "identifier shape" in block, (
        "the byte-identity guarantee must be scoped to queries with no "
        f"identifier shape:\n{block}")
    assert "top 10" in block, (
        "the carve-out must name a concrete example (`top 10`), not only the "
        f"abstract rule:\n{block}")
