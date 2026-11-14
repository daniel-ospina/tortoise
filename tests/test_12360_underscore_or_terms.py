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
``tests/test_identifier_tokens_8933.py`` uses — copied verbatim, ``strip``/filter
and all — repeated here deliberately: a phrase term (``"err 4032"``) contains no
``|``, so a naive ``.split("|")`` is correct, but ``str.split()`` would break it
into words and quietly stop checking adjacency.
"""

from __future__ import annotations

import os
import unicodedata

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
    """Split an OR-union query into its terms, keeping a PHRASE whole.

    ⛔ THE BODY IS COPIED VERBATIM from :func:`tests.test_identifier_tokens_8933`'
    helper, and the module docstring says so (P2, `#12368` review): a bare
    ``query.split("|")`` is the same FUNCTION for every term that carries no
    padding, and different for one that does — MEASURED,
    ``build_or_query('a_b ')`` is ``'a_b |a_b'``, whose raw split is
    ``['a_b ', 'a_b']`` and whose stripped split is ``['a_b', 'a_b']``. A docstring
    that claims the two are the same splitter has to be true of the shape the
    branch under test emits, not only of the tidy ones.
    """
    return [t.strip() for t in query.split("|") if t.strip()]


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

    def test_a_non_ascii_word_is_not_truncated_into_a_fake_composite(
            self, monkeypatch):
        """A composite the ASCII splitter builds from a Unicode word is a FRAGMENT.

        ``[A-Za-z0-9_]+`` is an ASCII class while the index treats Unicode letters
        as word characters, so splitting ``año_actual`` at the ``ñ`` yields
        ``o_actual`` — a fragment of the index term ``año_actual`` that NOTHING
        holds (``underscore_run_terms``' own module comment says so). Emitting it
        is not additive-but-harmless: the read then sends TWO alternatives, so
        ``probe_phrase_rejection`` refused a valid, reachable ONE-index-term probe
        — a FALSE BLOCK, measured on the default-ON arm across five shapes that
        the switch-OFF arm ACCEPTED (#12368 review).

        ⛔ THIS IS THE PIN. Reverting the abutment refusal — back to
        ``_COMPOSITE_SPLIT_RE.split(text)`` — reds it on BOTH the byte assertion
        and the probe assertion, for every shape below.

        The property asserted is stronger than "additive": these bytes are
        IDENTICAL on both arms, because the switch can only add a term that
        EXISTS, and no such term exists for a truncated Unicode word.
        """
        from tortoise.session_source_prose_migration import probe_phrase_rejection

        shapes = ("año_actual", "the_café", "_café", "dé_installer", "x_café")
        for word in shapes:
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            before = build_or_query(word)
            assert probe_phrase_rejection(word) is None, word
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            assert build_or_query(word) == before, word
            assert probe_phrase_rejection(word) is None, word

        # ⚠️ DECOMPOSED (NFD) input, asserted on the property THIS fix owns
        # (#12368 second review). A combining mark is not alphanumeric, so an
        # `isalnum()`-only abutment test left the false block in place for NFD:
        # NFD `_café` is `_cafe` + U+0301 and the splitter truncating at the mark
        # emitted `_cafe` — REFUSED on the ON arm, ACCEPTED on the OFF arm.
        # ⛔ The property is therefore "the switch cannot move NFD bytes OR the
        # probe verdict", NOT "NFD is accepted": it is pinned as "same on both
        # arms" so a verdict difference cannot be mistaken for a control.
        # ⚠️ The MECHANISM this comment used to state was FALSE (#12368 fourth
        # review, corrected by measurement) — and the assertions below could not
        # see it, because they compare the two arms and never the verdict.
        # NFD `año_actual` is `an` + U+0303 + `o_actual`, and the token CARRIES
        # `_`, so `deferred` is True: the run-count branch this comment used to
        # name is SKIPPED BY CONSTRUCTION, and the verdict is the read's own
        # answer. MEASURED here: `probe_phrase_rejection` ACCEPTS NFD
        # `año_actual`/`the_café`/`x_café` on both arms (one term), and still
        # REFUSES NFD `dé_installer` on both because `build_or_query` expands it
        # into two different terms ('installer|de'). On `origin/main` all four are
        # refused — three by the run-count branch, `dé_installer` by the TERM
        # COUNT (`it tokenises to 2 term(s)`) — so those three accepts are NOT
        # pre-existing: this change cures them. NFD `_café` is accepted on both
        # sides too, and NOT because of where its mark sits: all four collapse to
        # ONE surviving token (`['actual']`, and `['cafe']` for the other three —
        # `an`/`the` are stopwords, `o`/`x` are 1-char), so the degenerate branch
        # sends the escaped RAW string, which IS the index term. `dé_installer`
        # keeps two tokens, which is why it is the one still refused.
        for word in shapes:
            nfd = unicodedata.normalize("NFD", word)
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            off_built, off_ok = build_or_query(nfd), (
                probe_phrase_rejection(nfd) is None)
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            assert build_or_query(nfd) == off_built, word
            assert (probe_phrase_rejection(nfd) is None) == off_ok, word
            # ⛔ The VERDICT itself, pinned — not only the arm-to-arm equality the
            # comment above says cannot see a wrong mechanism. The four shapes that
            # collapse to ONE surviving token are ACCEPTED; `dé_installer` is
            # REFUSED, a remaining false block on the read path (`build_or_query`
            # expands it into `'installer|de'`). Measured on both arms.
            accepted = probe_phrase_rejection(nfd) is None
            assert accepted is (word != "dé_installer"), (
                f"{word!r} NFD verdict moved to accepted={accepted}: the comment "
                "above names which of these the probe ACCEPTS, and pinning only "
                "the two arms reads green whichever way the verdict goes")

        # ⛔ NON-ASCII SEPARATORS DO NOT EXIST ON THIS LANE (#12368 third review).
        # An earlier revision of this test asserted the OPPOSITE — that the run
        # after a NBSP/thin space "really is an index term and its composite must
        # still be emitted" — and that premise was FALSE. Measured on FalkorDB
        # 4.20.6 (one `:Object` per shape, segment `b_c` queried against the
        # `Object` FULLTEXT index, reported by WHICH document comes back): `b_c`
        # returns ONLY the ASCII-space document; for U+00A0, U+2009, U+3000,
        # U+2028, U+202F, an emoji and a CJK character the whole string stayed ONE
        # index term. So these must gain NO composite — and they do not.
        for sep in ("\u00a0", "\u2009", "\u3000", "\u2028", "\u202f",
                    "\U0001f389", "\u4e2d"):
            word = f"{sep}b_c"
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            before = build_or_query(word)
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            assert build_or_query(word) == before, (word, before)
            assert underscore_run_terms(word) == [], word

        # …but the ASCII space IS a real separator: the composite after it is a
        # genuine index term (the same probe returns the ASCII-space document for
        # `b_c`) and must keep being emitted. This is the control that stops the
        # non-ASCII rule from swallowing everything.
        word = " b_c"
        monkeypatch.setenv(UNDERSCORE_ENV, "0")
        before = build_or_query(word)
        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        assert build_or_query(word) != before, (word, before)
        assert "b_c" in _or_terms(build_or_query(word)), word

        # …and the refusal must NOT over-reach: a pure-ASCII word still gains its
        # composite, which is the mechanism this PR exists for.
        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        assert _or_terms(build_or_query("web_search")) == [
            "search", "web", "web_search"]
        # A MULTI-token query that happens to carry a Unicode word is still
        # refused — on both arms, and for the ordinary reason (several terms),
        # not because of the truncation: the composite was never the issue there.
        # ⚠️ `naïve_web` belongs HERE, not above: the ASCII tokeniser splits the
        # accented segment itself (`web|na|ve`), so the read really does expand it
        # and refusing the probe is correct on BOTH arms — a pre-existing
        # behaviour this PR does not move. It is listed with its own assertion
        # rather than dropped, so the next lane cannot mistake it for a control.
        for multi in ("b años_web", "naïve_web"):
            monkeypatch.setenv(UNDERSCORE_ENV, "0")
            before = build_or_query(multi)
            assert probe_phrase_rejection(multi) is not None, multi
            monkeypatch.setenv(UNDERSCORE_ENV, "1")
            assert build_or_query(multi) == before, multi

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

    def test_the_degenerate_dedupe_is_case_insensitive(self, monkeypatch):
        """The dedupe must fold case, because the INDEX does (round 10, P2).

        ``underscore_run_terms`` lowercases its term while the escaped literal
        keeps the query's case, so a bare ``c != literal`` emitted the SAME
        RediSearch term twice for a capitalised token — ``A_bc|a_bc`` — and
        MEASURED on a lane ``queryNodes('Object','A_bc')`` and ``…'a_bc'`` return
        the same record, i.e. the second alternative addresses the identical term
        and is pure waste. ⛔ THIS ASSERTION IS THE PIN for that fix: reverting the
        dedupe to ``c != literal`` reds it (the guard in
        ``session_source_prose_migration`` does NOT — its comparison is itself
        case-folded, so it stays green over the restored bug).
        """
        monkeypatch.setenv(UNDERSCORE_ENV, "1")
        for capitalised in ("A_bc", "X_axis", "My_class", "X__Y", "A_B"):
            assert build_or_query(capitalised) == capitalised, capitalised
        # …and the shapes that MUST keep two terms are untouched by the fold
        assert build_or_query("10:00_u") == "10\\:00_u|00_u"
        # ⚠️ `año_actual` USED TO BE HERE as "a shape that must keep two terms" —
        # it kept `o_actual`, which is not a term at all but a TRUNCATION of the
        # index term `año_actual` that nothing holds (#12368 review). It is now
        # correctly ONE term, and that property is pinned by
        # `test_a_non_ascii_word_is_not_truncated_into_a_fake_composite` below.
        assert _or_terms(build_or_query("web_search")) == [
            "search", "web", "web_search"]

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
        round-6 finding, and round 7 extended it to the ZERO-token shapes below.
        ⛔ Scoped, not generalised (round 9): a token carrying a separator the
        read ESCAPES is still refused (the three shapes below), and so is one
        whose alphanumeric runs number more than one — the guard's rule is the
        ESCAPE set plus the run count, NOT "the index splits". `friday.`/
        `friday?`/`friday+`/`friday/` are ACCEPTED: none of them is escaped by
        `escape_redisearch_literal`, and each leaves ONE alphanumeric run, so the
        read sends them as one literal. ⚠️ They are still SPLIT by the index
        (`_indexed_term_runs` is ``re.split(r"[\\W_]+", …)``, so `friday?` yields
        `friday`) — the earlier revision of this line called them "in neither
        set", which is false and contradicted the sentence above it (#12368
        review round 13).
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
        # ⛔ ONE DISTINCT ALTERNATIVE IS NOT ONE PROBE (review round 12, P2). A
        # doubled accented fragment makes the read OR-join alternatives that
        # merely COINCIDE — `build_or_query('cafécafé') == 'caf|caf'` — while the
        # index holds `cafécafé` as ONE term, so the probe matches NOTHING and
        # certifies `ran=True, leaked=[]` over a still-present sentence. That is
        # the FALSE PASS this guard exists to prevent, and accepting it was a
        # regression against `origin/main` (which refused these).
        for doubled in ("cafécafé", "überüber", "testétest"):
            refusal = probe_phrase_rejection(doubled)
            assert refusal is not None and "expands" in refusal, (doubled, refusal)
            assert len(build_or_query(doubled).split("|")) > 1, doubled
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
        # ⛔ AND THE BARE TOKEN, WHICH THE BOUND USED TO MISS ENTIRELY (#12368
        # independent review, P2). The bound was gated on `deferred`, which needs
        # a `_`, so every SEPARATOR-FREE token past the bound was ACCEPTED —
        # `probe_phrase_rejection('a' * 129)` returned None, i.e. the guard
        # certified "the pre-fix prose is gone" for a probe that can match
        # nothing, because the index holds nothing above 128 chars. The
        # truncation is a property of the INDEX, not of `_`, so this pins the
        # AT-BOUND case too: 128 is accepted (the index returns it), 129/200 are
        # refused (it returns nothing). The shapes above are ALL `_`-carrying, so
        # none of them covered this one.
        bare_at_bound = "a" * MAX_COMPOSITE_RUN_LEN
        assert probe_phrase_rejection(bare_at_bound) is None, (
            "the index DOES hold a 128-char term — refusing it would be a false "
            "block")
        for past in (MAX_COMPOSITE_RUN_LEN + 1, 200):
            bare = "a" * past
            refusal = probe_phrase_rejection(bare)
            assert refusal is not None and "128" in refusal, (past, refusal)
        # ⛔ AND THE TWO SHAPES THAT FALSIFIED THE OBVIOUS REPAIRS (#12368 second
        # independent review — both P2, both measured on a live Object index).
        #
        # (1) THE UNIT IS BYTES, NOT CHARACTERS. RediSearch's length limit is UTF-8
        # bytes, so a character-counted bound let `'é' * 65` (65 chars, 130 bytes)
        # through while the index truncates it: measured 64 chars / 128 bytes
        # returns rows, 65 chars / 130 bytes returns none. A character bound here
        # would still be a false PASS for any multi-byte probe.
        assert probe_phrase_rejection("\u00e9" * 64) is None, (
            "64 chars is exactly 128 BYTES and the index holds it")
        assert probe_phrase_rejection("\u00e9" * 65) is not None, (
            "65 chars is 130 BYTES, past the index's limit — accepting it is a "
            "false PASS")
        #
        # (2) THE QUANTITY IS A REACHABLE INDEX TERM, NOT THE WHOLE TOKEN. This
        # token is 129 chars, but the read sends it as ONE literal and the index
        # splits at `!`, so it genuinely reaches the 120-byte term and returns
        # rows. Refusing it is a FALSE BLOCK — the class this branch repaired
        # three times. Only a run that is itself over the bound is unreachable.
        reaches = "a" * 120 + "!" * 9
        assert len(reaches) == 129 and probe_phrase_rejection(reaches) is None, (
            "the read reaches `'a' * 120` through the literal — refusing it is a "
            "false block")
        # ⛔ AND THE ALL-DIGIT SHAPE, WHICH `2279d087`/`d0595175` STILL MISSED
        # (#12368 review round 16, P2). The bare-number acceptance returns None
        # BEFORE the bound, so every SEPARATOR-FREE ALL-DIGIT token past the bound
        # was accepted while every non-digit one was refused — measured at
        # `d0595175`: `'1' * 129` and `'1' * 200` were ACCEPTED, `'a' * 129`
        # REFUSED. Same false PASS class (the read sends a literal the index
        # truncates), reached through the branch that returns early. The fix is
        # ORDER: the bound now runs first, and a number the index CAN hold is
        # still accepted — that acceptance is pinned in
        # `tests/test_11070_source_prose_repair.py:3127`, while the row below pins
        # the AT-BOUND number HERE (#12368 review round 17: this comment first
        # pointed at a row in this file that does not exist).
        assert probe_phrase_rejection("1" * MAX_COMPOSITE_RUN_LEN) is None, (
            "a 128-byte number is one the index holds — refusing it is a false "
            "block")
        for past in (MAX_COMPOSITE_RUN_LEN + 1, 200):
            digits = "1" * past
            refusal = probe_phrase_rejection(digits)
            assert refusal is not None and "128" in refusal, (past, refusal)
        # ⛔ CORRECTED (#12368 review): `_café` is NOT an expansion case. The
        # composite `_caf` is a TRUNCATION of the index term `_café` — the
        # ASCII-only splitter splits at `é` — so nothing holds it and the read
        # now sends the literal alone, byte-identical to the switch-OFF arm. It
        # is therefore ACCEPTED, as it was before this PR. Pinned by
        # `test_a_non_ascii_word_is_not_truncated_into_a_fake_composite`.
        for shape in ("_café", "año_actual", "the_café", "dé_installer",
                      "x_café"):
            assert probe_phrase_rejection(shape) is None, shape

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
