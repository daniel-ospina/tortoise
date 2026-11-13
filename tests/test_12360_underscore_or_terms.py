"""#12360 — the ``_``-preserving OR term, pinned on the EMITTED query.

WHY THIS FILE EXISTS
--------------------
RediSearch treats ``_`` as a **word character**; ``sparse.tokenize_sparse_query``
splits on it (``_SPLIT_RE = [^a-z0-9]+``). So a stored ``web_search`` is ONE
term to the index while the leg emitted ``search|web`` — two terms the index
never holds — and the record was unretrievable by its own name. Measured on
``#12298``'s pinned 300-name population: candidate recall@3 **0.7133 -> 0.9633**
(+25.00 pts, p < 0.001, 75 gains / 0 losses).

⛔ **THE TRAP THIS FILE GUARDS, and why the assertions are written this way.**
``run_fts_query`` **rebuilds** the query through ``build_or_query``
(``search_engine.py``, the R2/#1541 leg), so a term set handed to it is
re-tokenised straight back — ``#12298``'s first harness arm passed
``search|web|web_search`` and measured **+0.33 pts**, the harness destroying the
thing under test. So:

* the mechanism lives INSIDE ``build_or_query`` (one seam, every caller), and
* the assertions that PROVE the mechanism are on the string that reaches
  ``db.idx.fulltext.queryNodes`` (the ``$query`` parameter of the issued Cypher) —
  ``TestTheEmittedQueryCarriesTheComposite``, ``TestBothSurfacesPassTheRawQuery`` and
  ``TestLiveReachability``. NEVER on the input that was handed to the builder: a test of the
  input would pass over a path that never sends it. The remaining classes (``TestRunTerms``,
  ``TestAdditive``, ``TestPin``) assert the builder's RETURN VALUE, which is legitimate only
  because ``run_fts_query`` passes that string through verbatim — the seam that makes it so is
  itself pinned by ``test_it_rides_the_same_seam_the_fused_read_calls`` and
  ``test_builder_keeps_the_door_agnostic``.

The ``_or_terms`` helper is the same phrase-aware splitter
``tests/test_identifier_tokens_8933.py`` uses, repeated here deliberately: a
phrase term (``"err 4032"``) contains no ``|``, so a naive ``.split("|")`` is
correct, but ``str.split()`` would break it into words and quietly stop
checking adjacency.
"""

from __future__ import annotations

import os

import pytest

from tortoise.sparse import (
    MAX_ALIASES_PER_POINT,
    MAX_COMPOSITE_RUN_LEN,
    UNDERSCORE_ENV,
    build_or_query,
    tokenize_sparse_query,
    underscore_run_terms,
)

# ── live-FalkorDB availability (the #6673 docker-lane probe) ───────────────
# Mirrors tests/test_identifier_tokens_8933.py: the docker lane is the DEFAULT
# lane, so the reachability test runs on the standard local/CI services job and
# skips gracefully in an embedded-only run instead of reding it.
_LANE_URI = os.environ.get("TORTOISE_DB_URI") or \
    __import__("tests._live_utils", fromlist=["x"]).docker_uri(
        "tortoise_test_12360")
LIVE = False
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
    """Split an OR-union query into its terms, keeping a PHRASE whole."""
    return query.split("|")


# ── the emitted-query assertions (the trap) ────────────────────────────────

class _CapturingGraph:
    """A graph double that records the Cypher + params of every query."""

    class _R:
        """The FalkorDB result envelope: an empty ``result_set``."""

        def __init__(self):
            self.result_set: list = []

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None, timeout=None):
        self.calls.append((cypher, dict(params or {})))
        return self._R()

    @property
    def sent(self) -> list[str]:
        return [p["query"] for _c, p in self.calls if "query" in p]


class TestTheEmittedQueryCarriesTheComposite:
    """The term must survive to ``$query`` — the failure mode #12298 recorded."""

    def test_run_fts_query_emits_the_composite(self):
        from tortoise.search_engine import run_fts_query

        g = _CapturingGraph()
        run_fts_query(g, "web_search", entity_type="point", limit=3)
        assert len(g.sent) == 1, g.calls
        emitted = g.sent[0]
        assert "web_search" in _or_terms(emitted), (
            "the composite never reached the index query — `run_fts_query` "
            f"re-tokenised it away: {emitted!r}")

    def test_the_label_route_is_the_production_one(self):
        from tortoise.search_engine import run_fts_query

        g = _CapturingGraph()
        run_fts_query(g, "web_search", entity_type="point", limit=3)
        cypher, params = g.calls[0]
        assert "db.idx.fulltext.queryNodes('Point', $query)" in cypher, cypher
        assert params["query"].count("web_search") == 1, params

    @pytest.mark.parametrize("query,composite", [
        ("web_search", "web_search"),
        ("abc_def", "abc_def"),
        ("test_7949_capstone_verdict_check", "test_7949_capstone_verdict_check"),
        ("tests/test_ci_selection.py", "test_ci_selection"),
        ("web_search and test_ci_selection", "test_ci_selection"),
    ])
    def test_the_composite_is_the_term_the_index_holds(self, query, composite):
        assert composite in _or_terms(build_or_query(query)), build_or_query(query)

    def test_it_rides_the_same_seam_the_fused_read_calls(self):
        """The SDK/MCP door and the capture prior reach ONE builder (#12360)."""
        import inspect

        from tortoise import search_engine

        src = inspect.getsource(search_engine.run_fts_query)
        assert "build_or_query(" in src, (
            "run_fts_query no longer builds through build_or_query — the "
            "composite seam moved and this pin is measuring nothing")


# ── both surfaces ─────────────────────────────────────────────────────────

class TestBothSurfacesPassTheRawQuery:
    """The fix must reach the capture prior AND the MCP/SDK search door.

    Both ride ``run_fts_query``: the MCP tool ``tortoise_search`` is registered
    with ``sdk_method="tortoise_fts_query"`` (``tool_registry.py``) and the
    capture prior is ``extractor_v2._fts_rows`` -> ``sdk.tortoise_fts_query``.
    What this pins is the HALF that can silently regress: neither door may
    pre-tokenise the text, because a door that hands a split term list down
    cannot be repaired by any later builder.
    """

    def test_capture_prior_door_hands_over_the_raw_query(self):
        from tortoise.extractor_v2 import _fts_rows

        seen: list[dict] = []

        class _FakeSDK:
            def tortoise_fts_query(self, query, **kw):
                seen.append({"query": query, **kw})
                return []

        _fts_rows(_FakeSDK(), "object", "web_search", limit=3)
        assert seen, "the capture prior never reached the FTS seam"
        assert seen[0]["query"] == "web_search", (
            "the capture prior re-tokenised BEFORE the seam: "
            f"{seen[0]['query']!r}")
        assert "web_search" in _or_terms(build_or_query(seen[0]["query"])), (
            "the term the prior hands down is lost by the builder")

    def test_builder_keeps_the_door_agnostic(self):
        """``run_fts_query`` must not pre-split: it passes ``query`` through."""
        from tortoise.search_engine import run_fts_query

        g = _CapturingGraph()
        run_fts_query(g, "web_search", entity_type="object", limit=3)
        assert "web_search" in g.sent[0].split("|")


# ── additive: the zero-regression property is structural ──────────────────

_QUERIES = [
    "web_search",
    "abc_def",
    "test_7949_capstone_verdict_check",
    "tests/test_ci_selection.py",
    "error ERR-4032 on deploy",
    "gym schedule",
    "what's my fastest 5k",
    "PR #12298",
    "the capstone_verdict check",
    # round 3: the NON-ASCII shapes. These were each returned by their own name
    # with the switch OFF and returned ZERO rows with it ON, because the
    # degenerate branch counted the composites and then dropped the escaped
    # literal — and the composite is the index's term only for an ASCII run
    # (`año_actual` is ONE index term; `o_actual` is held by nothing).
    "año_actual",
    "dé_installer",
    "the_café",
    "café_menu",
    # an EDGE underscore fires too, so this is NOT a byte-identity case
    "foo_ bar",
    "   ",
    "",
]


class TestAdditive:
    def test_no_term_of_the_old_query_is_lost(self, monkeypatch):
        for q in _QUERIES:
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            before = _or_terms(build_or_query(q))
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            after = _or_terms(build_or_query(q))
            missing = [t for t in before if t not in after]
            assert not missing, (
                f"additive contract broken for {q!r}: {missing} vanished "
                f"({before} -> {after})")

    def test_byte_identical_without_any_underscore(self, monkeypatch):
        """The claim is "no `_` AT ALL", not "no INNER `_`" (round 3).

        A run with a leading or trailing ``_`` also fires, so a query carrying
        one is not a control case — asserted below, because the wider wording
        would have a downstream lane measuring this change against exactly such
        a query and treating the movement as impossible.
        """
        for q in ["gym schedule", "error ERR-4032 on deploy", "PR #12298",
                  "what's my fastest 5k", ""]:
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            before = build_or_query(q)
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            assert build_or_query(q) == before, q

    def test_an_edge_underscore_is_not_a_control_case(self, monkeypatch):
        for q, off, on in [("foo_ bar", "foo|bar", "foo|bar|foo_"),
                           ("the report_ is ready", "report|ready",
                            "report|ready|report_"),
                           ("_foo bar", "foo|bar", "foo|bar|_foo")]:
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            assert build_or_query(q) == off, q
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            assert build_or_query(q) == on, q

    def test_the_degenerate_branch_keeps_the_escaped_literal(self, monkeypatch):
        """A composite that the index does NOT hold must not replace the literal.

        ``año_actual`` is ONE index term; the ASCII run splitter can only build
        ``o_actual``, which nothing holds. Counting composites in the degenerate
        predicate without keeping the literal therefore made a previously
        findable record unfindable — the round-3 P1.
        """
        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        for q in ["año_actual", "dé_installer", "x_café", "the_café", "_café"]:
            terms = _or_terms(build_or_query(q))
            assert q in terms, (q, terms)
        # the same-bytes cases are unchanged, and 10:00_u keeps its gain
        assert build_or_query("x__y") == "x__y"
        assert build_or_query("a_") == "a_"
        assert build_or_query("10:00_u") == "10\\:00_u|00_u"

    def test_the_shipped_8933_bytes_are_untouched(self):
        # The #8933 pin's own byte assertion, re-stated here so a future edit
        # to this tail cannot silently move THAT contract either.
        assert build_or_query("ERR-4032") == 'err|err4032|"err 4032"'

    def test_composites_ride_after_the_original_terms(self):
        terms = _or_terms(build_or_query("web_search"))
        assert terms[-1] == "web_search", terms
        assert terms[:2] == ["search", "web"], terms


# ── the pin: reds if the `_`-preserving OR form is dropped ─────────────────

class TestPin:
    def test_pin_reds_without_the_composite(self, monkeypatch):
        """The whole mechanism, in one assertion per shape.

        This is the test the issue asks for: drop the tail from
        ``build_or_query`` and every one of these reds, because the composite is
        the only term in the emitted union that the index holds for a
        ``snake_case`` name.
        """
        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        for q in ["web_search", "abc_def", "test_7949_capstone_verdict_check",
                  "tests/test_ci_selection.py"]:
            assert underscore_run_terms(q) == [q.split("/")[-1].split(".")[0]], q
            assert underscore_run_terms(q)[0] in _or_terms(build_or_query(q)), q

    def test_kill_switch_restores_the_pre_12360_bytes(self, monkeypatch):
        monkeypatch.setenv(UNDERSCORE_ENV, "0")
        assert build_or_query("web_search") == "search|web"
        assert build_or_query("abc_def") == "abc|def"

    def test_the_switch_defaults_on(self, monkeypatch):
        monkeypatch.delenv(UNDERSCORE_ENV, raising=False)
        assert "web_search" in _or_terms(build_or_query("web_search"))

    def test_a_probe_with_only_an_underscore_is_not_false_blocked(self):
        """The probe guard's RUN-COUNT premise is false for `_`, so it defers.

        A `_`-only token whose plain segments mostly die is sent by the read as
        ONE term that the index holds (`a_bc` -> `a_bc`, `x_axis` -> `x_axis`),
        so refusing it by run count was a false block with a false reason — the
        round-6 finding. A token carrying any OTHER separator is still refused,
        because there the index really does split.
        """
        from tortoise.session_source_prose_migration import probe_phrase_rejection

        # ⚠️ the ZERO-token shapes too (#12360 round 7): the tokeniser yields no
        # term for them, which used to hit the term-count refusal — whose reason
        # ("the read OR-joins multi-term input") is false when the read sends one
        # term. Both the ≥2-segment and the all-sub-2-char cases must pass.
        for shape in ("a_bc", "x_axis", "a_web", "aaaa_b",
                      "a_b", "x__y", "a_1", "1_a", "a_b_c"):
            assert probe_phrase_rejection(shape) is None, shape
            assert build_or_query(shape) == shape, shape
        for shape in ("zx-99", "friday,", "a-b"):
            assert probe_phrase_rejection(shape) is not None, shape
        # a CASE-VARIANT of one index term is not an OR over-match (measured:
        # RediSearch lowercases, and `queryNodes('A_B')` returns the record named
        # `a_b`), so it must not be refused.
        for shape in ("A_B", "X_AXIS", "A_1"):
            assert probe_phrase_rejection(shape) is None, shape
        # …and a run past the composite bound cannot be one term the index holds
        # (measured: a 128-char term is returned, 129/131/200 are not), so it is
        # refused rather than certified as a probe that matches nothing.
        # ⚠️ EVERY length past the bound, not just the one with a long segment
        # (round 9: the first cut measured a SEGMENT, so `_`-separated shapes
        # were accepted at any length).
        for too_long in ("a" * 129 + "_b", "a" * 128 + "_b", "a_" * 70 + "b",
                         "_" * 129):
            refusal = probe_phrase_rejection(too_long)
            assert refusal is not None and "128" in refusal, (len(too_long), refusal)
        # a `_` run that the read DOES expand is still refused — by the read's
        # own answer (`_café` -> `_café|_caf`), not by run count.
        refusal = probe_phrase_rejection("_café")
        assert refusal is not None and "expands" in refusal, refusal

    def test_a_garbage_switch_value_is_the_default_not_off(self, monkeypatch):
        monkeypatch.setenv(UNDERSCORE_ENV, "yes please")
        assert "web_search" in _or_terms(build_or_query("web_search"))


# ── the extractor's own contract ──────────────────────────────────────────

class TestRunTerms:
    @pytest.mark.parametrize("text,want", [
        ("web_search", ["web_search"]),
        ("WEB_SEARCH", ["web_search"]),
        ("abc_def", ["abc_def"]),
        ("tests/test_ci_selection.py", ["test_ci_selection"]),
        ("web_search and test_ci_selection", ["web_search", "test_ci_selection"]),
        ("ERR-4032", []),          # no `_` — already a token the tokenizer emits
        ("gym schedule", []),
        ("a_b", ["a_b"]),
    ])
    def test_terms(self, text, want):
        assert underscore_run_terms(text) == want, text

    def test_underscore_only_runs_are_dropped(self):
        """LOSSY on purpose, and the reason is SIGNAL — not that it cannot match.

        MEASURED (#12360 review round 8): the index holds `___` as a term, so a
        multi-token query (`'foo bar ___'` -> `'foo|bar'`) loses that
        alternative; `'foo ___'` is not the example, because only one plain token
        survives there and the read sends the raw string as an implicit AND. The drop is still
        right — an all-underscore run is punctuation, and spending OR budget on
        it is what makes the tail's bound meaningless — but it is not the
        "lossless" the first cut claimed.
        """
        assert underscore_run_terms("___") == []
        assert underscore_run_terms("a ___ b") == []

    def test_length_bound(self):
        """The bound is 128, NOT the alias mechanism's 48 — measured.

        A 48-char bound was the first cut and it dropped
        ``test_the_12360_underscore_or_terms_preserve_the_composite_run`` (61
        chars), which a live index holds and returns by its own name: the exact
        defect #12360 repairs. Live pin:
        ``TestLiveReachability::test_a_long_snake_case_name_is_reachable``.
        """
        over = ("a_" + "b" * MAX_COMPOSITE_RUN_LEN)[:MAX_COMPOSITE_RUN_LEN + 1]
        assert len(over) == MAX_COMPOSITE_RUN_LEN + 1
        assert underscore_run_terms(over) == []
        at_bound = ("a_" + "b" * MAX_COMPOSITE_RUN_LEN)[:MAX_COMPOSITE_RUN_LEN]
        assert len(at_bound) == MAX_COMPOSITE_RUN_LEN
        assert underscore_run_terms(at_bound) == [at_bound]
        # the 48..128 band the alias bound would have lost
        real_shape = ("test_the_12360_underscore_or_terms_preserve_the_"
                      "composite_run")
        assert len(real_shape) == 61
        assert underscore_run_terms(real_shape) == [real_shape]

    def test_budget_bound(self):
        runs = " ".join(f"n_{i}_x" for i in range(MAX_ALIASES_PER_POINT + 4))
        assert len(underscore_run_terms(runs)) == MAX_ALIASES_PER_POINT

    def test_deduped(self):
        assert underscore_run_terms("web_search web_search") == ["web_search"]

    def test_deduped_against_the_plain_terms(self):
        # `a_b` where `a_b` is already a plain token: impossible today (the
        # tokenizer splits it), and the dedupe is the guard that keeps the
        # union honest if that ever changes.
        q = build_or_query("web_search")
        assert q.split("|").count("web_search") == 1, q

    def test_the_terms_carry_no_redisearch_special(self):
        from tortoise.sparse import _REDI_SEARCH_SPECIAL

        for t in underscore_run_terms("tests/test_ci_selection.py a-b_c"):
            assert not _REDI_SEARCH_SPECIAL.search(t), t

    def test_tokenizer_still_splits_this_is_a_query_side_repair(self):
        # The write/fallback sides keep the split: this fix must NOT be read as
        # a tokenizer change (a wider blast radius, rejected on #12350).
        assert tokenize_sparse_query("web_search") == ["web", "search"]


# ── the real path, on a real graph ────────────────────────────────────────

@requires_live
class TestLiveReachability:
    """``abc_def`` is #12298's crispest case: ZERO rows for its own name.

    Run on the real index, through ``run_fts_query``, with the switch the only
    difference between the two arms — i.e. the before/after the falsifier asks
    for, at fixture scale (the population-scale numbers are in
    ``tools/underscore_or_terms_12360.py``'s artifact).
    """

    @pytest.fixture()
    def proj(self):
        import uuid

        _FP.from_uri(_LANE_URI)  # probe
        p = _FP.from_uri(_LANE_URI, graph_name=f"test_12360_{uuid.uuid4().hex[:8]}")
        yield p
        p.close()

    @staticmethod
    def _wait_indexed(proj, expected: int, timeout_s: float = 60.0) -> None:
        """FalkorDB builds the fulltext index on a BACKGROUND cycle.

        A query issued straight after the writes can book a present name as a
        miss — the harness artefact #12360's scope item 3 names, in the worst
        direction. A timeout is a refusal, never a proceed.
        """
        import time

        t0 = time.time()
        last = -1
        while time.time() - t0 < timeout_s:
            for row in proj.g.query("CALL db.indexes()").result_set:
                if not row or row[0] != "Object":
                    continue
                stats = next((c for c in row
                              if isinstance(c, dict) and "numDocuments" in c), None)
                last = int(stats["numDocuments"]) if stats else -1
                break
            if last >= expected:
                return
            time.sleep(0.2)
        raise AssertionError(
            f"the Object index reports {last} documents of {expected} after "
            f"{timeout_s}s — a query now would book a present name as a miss")

    def test_abc_def_is_findable_and_was_not_before(self, proj, monkeypatch):
        from tortoise.search_engine import run_fts_query

        proj._upsert_object({"id": "o-12360-abc", "name": "abc_def",
                             "object_kind": "core:other"})
        self._wait_indexed(proj, 1)

        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        ids = [i for i, _s in run_fts_query(proj.g, "abc_def",
                                            entity_type="object", limit=3)]
        assert "o-12360-abc" in ids, (
            "the composite OR term did not reach the index — "
            f"emitted {build_or_query('abc_def')!r}, got {ids!r}")

        monkeypatch.setenv(UNDERSCORE_ENV, "0")
        before = [i for i, _s in run_fts_query(proj.g, "abc_def",
                                               entity_type="object", limit=3)]
        assert "o-12360-abc" not in before, (
            "the OFF arm found the record — the fixture is not measuring the "
            f"defect ({before!r}); if the tokenizer changed, re-measure #12360")

    def test_a_long_snake_case_name_is_reachable(self, proj, monkeypatch):
        """A 61-char run — this repo's own naming style — must ALSO be found.

        The bound is 128 rather than the alias mechanism's 48 because the index
        holds composites this long (measured). Without this pin, tightening the
        bound back to 48 would look safe: it reds only on a name nobody in the
        measured population has.
        """
        from tortoise.search_engine import run_fts_query

        name = ("test_the_12360_underscore_or_terms_preserve_the_composite_run")
        assert len(name) == 61
        proj._upsert_object({"id": "o-12360-long", "name": name,
                             "object_kind": "core:other"})
        self._wait_indexed(proj, 1)

        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        ids = [i for i, _s in run_fts_query(proj.g, name, entity_type="object",
                                            limit=3)]
        assert "o-12360-long" in ids, (
            f"a 61-char composite was dropped: {build_or_query(name)!r}")

        monkeypatch.setenv(UNDERSCORE_ENV, "0")
        before = [i for i, _s in run_fts_query(proj.g, name, entity_type="object",
                                               limit=3)]
        assert "o-12360-long" not in before, (
            "the OFF arm found a 61-char snake_case name — the fixture is not "
            f"measuring the defect ({before!r})")


    def test_the_non_ascii_class_keeps_its_literal(self, proj, monkeypatch):
        """A non-ASCII `snake_case` name stays findable — it was, and then wasn't.

        The index holds ``año_actual`` as ONE term, so the escaped literal is
        what finds it, while the ASCII run splitter can only offer ``o_actual``.
        Both arms must return the record: the change has to be additive here as
        much as anywhere, and the regression this pins was measured on a lane
        rather than reasoned about.
        """
        from tortoise.search_engine import run_fts_query

        name = "año_actual"
        proj._upsert_object({"id": "o-12360-nonascii", "name": name,
                             "object_kind": "core:other"})
        self._wait_indexed(proj, 1)

        for setting, arm in (("0", "OFF"), ("1", "ON")):
            monkeypatch.setenv(UNDERSCORE_ENV, setting)
            ids = [i for i, _s in run_fts_query(proj.g, name,
                                                entity_type="object", limit=3)]
            assert "o-12360-nonascii" in ids, (
                f"{arm} arm lost a non-ASCII name it could find: "
                f"{build_or_query(name)!r} -> {ids!r}")

    def test_the_128_char_bound_is_live(self, proj, monkeypatch):
        """The bound's UPPER end is a live fact — and the earlier arm was vacuous.

        The first attempt used ``'a_' + 'b'*126``, which is a DEGENERATE token:
        the read sends the escaped literal and finds the record whatever the
        bound is (measured — patching the bound to 48 kept it green). This name
        is 128 chars of SHORT segments joined by `_`, so the segments are terms
        the index does NOT hold (the index holds the whole name as one term) and
        the composite is the only thing that can find it. Lowering the bound
        therefore reds here, which is what the docstring in ``sparse.py`` claims.
        """
        from tortoise.search_engine import run_fts_query

        name = "ab_" * 42 + "ab"
        assert len(name) == 128
        assert underscore_run_terms(name) == [name]
        # the composite is ESSENTIAL: every plain segment is a term the index
        # does not hold, because `_` is a word character to the index.
        assert set(build_or_query(name).split("|")) - {name}, (
            "expected plain segments in the union as well")
        proj._upsert_object({"id": "o-12360-128", "name": name,
                             "object_kind": "core:other"})
        self._wait_indexed(proj, 1)

        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        ids = [i for i, _s in run_fts_query(proj.g, name, entity_type="object",
                                            limit=3)]
        assert "o-12360-128" in ids, (
            f"the index holds a 128-char composite but the read dropped it: "
            f"{build_or_query(name)[:120]!r}")

        monkeypatch.setenv(UNDERSCORE_ENV, "0")
        off = [i for i, _s in run_fts_query(proj.g, name, entity_type="object",
                                            limit=3)]
        assert "o-12360-128" not in off, (
            "the OFF arm found a 128-char composite by its segments — the "
            f"fixture is not measuring the composite ({off!r})")

        over = name + "b"
        assert len(over) == 129
        assert underscore_run_terms(over) == [], (
            "129 must exceed the bound — if this passes, the bound moved and "
            "sparse.py's docstring probe is stale")


# ── the committed evidence ────────────────────────────────────────────────

_EVIDENCE = (__import__("pathlib").Path(__file__).resolve().parent.parent
             / "docs" / "research" / "2026-11-13-12360-underscore-or-terms")


class TestTheRecordedMeasurement:
    """The artifact is a record, so an edit to it must red.

    Not a re-measurement (that needs a live lane and 300 queries —
    ``tools/underscore_or_terms_12360.py`` does it): what this pins is that the
    numbers the PR and the brief quote are the numbers the artifact holds, on
    BOTH surfaces, and that the artifact still describes the population
    committed beside it.
    """

    @staticmethod
    def _artifact(name="artifact.json") -> dict:
        import json

        path = _EVIDENCE / name
        assert path.exists(), f"missing evidence artifact {path}"
        return json.loads(path.read_text())

    def test_both_surfaces_clear_the_falsifier(self):
        art = self._artifact()
        for surface in ("mcp_search", "capture_prior"):
            v = art["verdict"][surface]
            assert v["delta_pts"] >= 10.0, (surface, v)
            assert v["regressions"] == 0, (surface, v)
            assert v["p"] < 1e-12, (surface, v)
        assert art["verdict"]["passes"] is True

    def test_the_isolation_arm_reproduces_12298(self):
        # The port must land on the SAME numbers #12298 published (0.7133 ->
        # 0.9633, 75 gains / 0 losses). A drift here means this is measuring
        # something else.
        arm = self._artifact()["arms"]["fts_leg"]
        assert arm["off"]["recall"]["3"] == 0.7133
        assert arm["on"]["recall"]["3"] == 0.9633
        assert arm["paired"]["3"]["gains"] == 75
        assert arm["paired"]["3"]["losses"] == 0
        assert arm["off"]["rows_zero"]["15"] == 21
        assert arm["on"]["rows_zero"]["15"] == 0

    def test_the_identifier_fixture_moves(self):
        for surface in ("mcp_search", "capture_prior"):
            fx = self._artifact()["fixtures"][surface]
            assert fx["reachable"]["off"] == 3, (surface, fx["reachable"])
            assert fx["reachable"]["on"] == 5, (surface, fx["reachable"])

    def test_the_population_is_the_one_the_artifact_names(self):
        import hashlib

        art = self._artifact()
        raw = (_EVIDENCE / "population.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == art["population"]["sha256"]
        assert art["population"]["n"] == 300

    def test_the_repeat_run_agrees_on_the_numbers(self):
        a = self._artifact("artifact.json")["arms"]
        b = self._artifact("artifact-repeat.json")["arms"]
        for surface in ("mcp_search", "capture_prior", "fts_leg"):
            for arm in ("off", "on"):
                assert a[surface][arm]["recall"] == b[surface][arm]["recall"], (
                    surface, arm)
                assert a[surface][arm]["ndcg@10"] == b[surface][arm]["ndcg@10"], (
                    surface, arm)


    def test_the_declared_user_search_set_is_pinned(self):
        """The contract's SECOND surface — the row round 1 added — is pinned.

        Without this, `user_search_set` and `query_population_substitution`
        could be deleted from the artifact and every test would stay green,
        while the brief and the PR body kept asserting a measurement the harness
        no longer makes.
        """
        art = self._artifact()
        uss = art["user_search_set"]
        assert uss["available"] is True, uss
        assert uss["n"] == 50, uss
        assert uss["with_underscore"] == [], uss
        for door in ("mcp_search", "capture_prior"):
            row = uss["arms"][door]
            assert row["identical_rank_vectors"] == row["n"] == 50, (door, row)
            assert row["delta_pts"] == 0.0, (door, row)
        assert uss["emitted_bytes_identical"] == 50, uss
        sub = art["verdict"]["query_population_substitution"]
        assert isinstance(sub, str) and len(sub) > 80, sub


    def test_the_published_ndcg_and_p_are_pinned(self):
        """nDCG@10 and p were only ever cross-checked run-to-run (round 5).

        ``test_the_repeat_run_agrees_on_the_numbers`` compares the two committed
        runs, so an edit applied to BOTH files left the figures quoted in
        ``.env.example``, the brief and the PR body unguarded with every test
        green. They are pinned here to the published values instead.
        """
        art = self._artifact()
        for door in ("fts_leg", "mcp_search", "capture_prior"):
            arm = art["arms"][door]
            assert arm["off"]["ndcg@10"] == 0.6917, (door, arm["off"]["ndcg@10"])
            assert arm["on"]["ndcg@10"] == 0.9273, (door, arm["on"]["ndcg@10"])
            verdict = art["verdict"][door]
            assert verdict["delta_pts"] == 25.0, (door, verdict)
            assert verdict["p"] == 5.293955920339377e-23, (door, verdict["p"])
            assert verdict["p_display"] == "<1e-22", (door, verdict)
            assert verdict["regressions"] == 0 and verdict["passes_10pts"], door
        assert art["verdict"]["passes"] is True

class TestHarnessStatistics:
    """The two numbers the verdict rests on, checked against hand values."""

    def test_mcnemar_unanimous_is_the_tail(self):
        from tools.underscore_or_terms_12360 import mcnemar_exact

        # 5 discordant pairs, all one direction: 2 * 0.5**5
        assert mcnemar_exact(5, 0) == pytest.approx(2 * 0.5 ** 5)
        assert mcnemar_exact(0, 0) == 1.0
        # symmetric in the discordant counts
        assert mcnemar_exact(3, 7) == mcnemar_exact(7, 3)

    def test_ndcg_decays_with_rank_and_stops_at_ten(self):
        import math

        from tools.underscore_or_terms_12360 import ndcg10

        assert ndcg10(1) == 1.0
        assert ndcg10(2) == pytest.approx(1 / math.log2(3))
        assert ndcg10(10) == pytest.approx(1 / math.log2(11))
        assert ndcg10(11) == 0.0
        assert ndcg10(None) == 0.0
