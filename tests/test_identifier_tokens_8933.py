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
from tortoise.projection.entities import (
    append_identifier_aliases,
    flatten_search_keys_prop,
    point_identifier_aliases,
)
from tortoise.sparse import (
    IDENTIFIER_ENV,
    MAX_IDENTIFIER_LEN,
    build_or_query,
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

    @pytest.mark.parametrize("typed", ["ERR-4032", "ERR 4032", "err4032",
                                       "Err_4032", "err4032"])
    def test_however_the_person_typed_it_meets_at_one_token(self, typed):
        """The ruling's requirement: "it matches however the person typed it —
        dashes, spaces, or none". A no-separator spelling needs no normalising
        at all: it is already a single index token."""
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
        identifier mechanism emits, no all-digit token becomes an OR term."""
        terms = build_or_query(query).replace('"', "|").split("|")
        assert not any(t.strip().isdigit() for t in terms
                       if not t.strip().startswith('"')), terms

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

    def test_the_just_written_exact_match_ranks_first(self, corpus):
        """Measured on this corpus: the fresh exact match is rank 1 of 16 at
        score 3.0, against 2.0 for every look-alike."""
        assert self._rank_of(corpus, "capstone 9664 write probe",
                             "p-99") == 1

    def test_mutation_without_the_mechanism_the_match_is_not_first(
            self, corpus, monkeypatch):
        """The same corpus and query with the mechanism switched OFF reproduces
        the SHIPPED behaviour the ruling measured: the fresh exact match scores
        2.0 like every look-alike, and the id tie-break puts it in the tail
        (measured here: rank 15 of 16). That is what makes the test above a
        real pin rather than a coincidence."""
        monkeypatch.setenv(IDENTIFIER_ENV, "0")
        rank = self._rank_of(corpus, "capstone 9664 write probe", "p-99")
        assert rank is not None and rank > 1, rank
