"""#8488 — the in-process Okapi BM25 lexical arm (+ the fusion bound).

What this file pins, and why each test exists:

* the SCORER: idf (a rare term outweighs a ubiquitous one — the measured gap:
  the shipped graph-side scorer gives both the same weight and breaks the tie
  by id, so the rare-token document ranks LAST);
* length normalisation (a short precise document is not out-ranked by a long
  repetitive one);
* the BOUND on the lexical leg's fusion weight (#7704 / #1657) and the
  mutation that reds it — dropping the clamp lets the lexical leg's also-ran
  overtake the dense leg's best hit, i.e. the read's actual answer;
* the READ PATH: the arm is ON by default (owner ruling 2026-10-28), and
  ``TORTOISE_BM25_LEXICAL=0`` opts out,
  and an arm that finds nothing falls back to the shipped scorer instead of
  costing the read its lexical leg;
* the dense-only miss — the acceptance test: on this fixture the DENSE leg
  (``run_vector_query``) does not return the rare-token record at all, the
  lexical leg returns it first, and only the fused read makes it reachable
  inside the caller's ``limit``.

Fixture note (honest scope). The dense leg is driven by a small lexicon
stand-in embedder installed at the ``EmbeddingModel.get`` seam, because the
real bge-small model is not installed in this environment (no
``sentence-transformers``) and downloading it is out of scope. The stand-in
gives EVERY in-lexicon concept word a direction and gives an out-of-lexicon
token NO direction — which is exactly how a subword embedder treats a rare
identifier (it carries no semantics the model has seen). So the fixture proves
the MECHANISM, not a live numeric improvement: the shipped embedder's real
behaviour on this corpus is #8486's measurement, which has not been run.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
from typing import ClassVar

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.bm25 import (
    LEXICAL_WEIGHT_MAX,
    bm25_rank,
    bm25_scores,
    bm25_tokens,
    resolve_lexical_weight,
)
from tortoise.embeddings import EmbeddingModel
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import (
    LEXICAL_DECLINED_KEY,
    LEXICAL_MECHANISM_BM25,
    clamp_lexical_fusion_weight,
    rrf_fusion,
    run_bm25_query,
    run_vector_query,
)

# ── the fixture corpus ────────────────────────────────────────────────────
#: The rare exact token.
#:
#: #8933 UPDATE (owner ruling, 2026-10-26): this fixture used to carry the
#: IDENTIFIER `ERR-4032`, which the shipped search lane shredded to `err` — the
#: numeric half was "the owner's pending decision" and the fixture knowingly
#: depended on it. That decision has LANDED: the search lane now preserves an
#: identifier (`err4032` + the adjacency phrase `"err 4032"`), so the shipped
#: leg matches this record exactly and the rank-lift assertion below lost its
#: headroom (`on.index == off.index`, measured). The rare token is therefore a
#: plain OOV WORD — which is what this file is actually about: a token the dense
#: leg cannot see (the lexicon has no direction for it) and the shipped scorer
#: under-prices (no idf). Re-pinned by name rather than waived; the #8933
#: subject has its own file (`tests/test_identifier_tokens_8933.py`).
RARE = "zebra"

#: Six documents about the query's TOPIC and holding none of its rare token.
TOPIC_DOCS = [
    f"release rollout plan number {i}" for i in range(1, 7)
]
#: A four-document pool for the END-TO-END test. Why four: the lexical leg's
#: fusion weight is bounded below the dense leg's, so a lexical rank gain of N
#: places is worth roughly N/2 dense places at k=60 — against a SIX-document
#: dense tie cluster the rare-token record cannot climb at all (measured, see
#: the PR body), while in a four-document pool the same gain moves it from last
#: to third. The magnitude of the movement at the recorded production balance
#: is the honest measurement here; whether the balance should move is #8486's
#: question, not this lane's.
TOPIC_DOCS_END_TO_END = TOPIC_DOCS[:4]
#: The one document holding the rare token, on an unrelated topic.
TARGET_DOC = f"failure {RARE} billing invoice"

QUERY = f"release rollout {RARE}"

#: This module's subject is the EMBEDDED lane (falkordblite): the in-process
#: arm is gated to it, and every fixture here builds an embedded SDK. Under a
#: supported ``TORTOISE_DB_URI`` the same file would address the shared docker
#: store, so it skips visibly instead (the epic #1647 D-2 convention) rather
#: than failing — or, worse, writing fixture points into a shared graph.
pytestmark = pytest.mark.embedded_only

_SDKS: list[TortoiseSDK] = []


class _LexiconEmbedder:
    """Concept-word stand-in embedder (see the module docstring).

    A text's vector is the normalised sum of the lexicon directions of the
    concept words it contains. A token the lexicon does not know contributes
    NOTHING — the OOV shape that makes an exact identifier invisible to a dense
    leg. Deterministic: same text, same vector, every run.
    """

    DIM = 4
    LEXICON: ClassVar[dict[str, tuple[float, ...]]] = {
        "release": (1.0, 0.0, 0.0, 0.0),
        "rollout": (1.0, 0.1, 0.0, 0.0),
        "plan": (0.9, 0.2, 0.0, 0.0),
        "failure": (0.0, 0.0, 1.0, 0.0),
        "billing": (0.0, 0.0, 0.0, 1.0),
        "invoice": (0.0, 0.0, 0.1, 1.0),
    }

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        rows = []
        for text in texts:
            vec = np.zeros(self.DIM, dtype=np.float64)
            for tok in str(text).lower().split():
                direction = self.LEXICON.get(tok)
                if direction is not None:
                    vec += np.asarray(direction, dtype=np.float64)
            norm = float(np.linalg.norm(vec))
            rows.append(vec / norm if norm else vec)
        return np.stack(rows).astype(np.float32)


@pytest.fixture
def lexicon_embedder(monkeypatch):
    stub = _LexiconEmbedder()
    monkeypatch.setattr(EmbeddingModel, "get", classmethod(
        lambda cls, load_timeout=None: stub))
    return stub


@pytest.fixture(autouse=True)
def _reclaim_sdks():
    yield
    while _SDKS:
        sdk = _SDKS.pop()
        with contextlib.suppress(Exception):
            sdk.close()
        tree = os.path.dirname(sdk._db_path) if sdk._db_path else None
        if tree:
            shutil.rmtree(tree, ignore_errors=True)


def _graph_with_corpus() -> tuple[object, dict[str, str]]:
    """An embedded graph holding the fixture corpus; ids by logical name."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="bm25_8488_"), "test.db")
    sdk = TortoiseSDK(db_path, namespace=f"bm25_8488_{os.urandom(6).hex()}")
    _SDKS.append(sdk)
    ids: dict[str, str] = {}
    for i, content in enumerate(TOPIC_DOCS):
        ids[f"topic{i}"] = sdk.create_point("statement", content)["id"]
    ids["target"] = sdk.create_point("statement", TARGET_DOC)["id"]
    return sdk._get_proj().g, ids


def _query_vec(embedder, query: str) -> list[float]:
    return [float(x) for x in embedder.encode([query])[0]]


# ── the scorer ────────────────────────────────────────────────────────────
class TestBM25Scorer:
    def test_rare_term_outweighs_the_ubiquitous_one(self):
        """idf: the term only ONE document holds dominates the term 8 hold.

        This is the measured gap. The shipped graph-side scorer gives a
        document holding only the rare term the SAME score as the documents
        holding only the ubiquitous one (2.0 each — no idf), and the id
        tie-break then puts the rare one LAST. Mutation: drop the ``idf``
        factor from ``bm25_scores`` → every document scores alike → the
        ranking assertion fails.
        """
        docs = [(f"rel{i}", f"release notes for sprint {i}") for i in range(8)]
        docs.append(("rare", "zebra sighting report"))
        ranked = bm25_rank("release zebra", docs)
        order = [pid for pid, _ in ranked]
        assert order.index("rare") < order.index("rel0"), (
            "a document holding ONLY the rare term must outrank documents "
            f"holding only the ubiquitous one; got {order}"
        )
        scores = dict(ranked)
        assert scores["rare"] > 5 * scores["rel0"]

    def test_length_normalisation_favours_the_shorter_document(self):
        """``b``: on the SAME term frequency, the shorter document wins.

        The shipped scorer ties these two (measured: 2.0 each — it awards one
        point per matched term, so document length is invisible), which leaves
        the id tie-break to decide. Mutation: pass ``b=0`` to ``bm25_rank``
        below → both score identically and the assertion flips.
        """
        short = ("short", "release notes")
        long_ = ("long", "release notes " + " ".join(
            f"filler{i}" for i in range(20)))
        ranked = dict(bm25_rank("release", [short, long_]))
        assert ranked["short"] > ranked["long"], (
            "a 2-token document must outrank a 22-token one at equal term "
            f"frequency; got {ranked}")
        flat = dict(bm25_rank("release", [short, long_], b=0.0))
        assert flat["short"] == pytest.approx(flat["long"]), (
            "b=0 is the no-length-normalisation control")

    def test_term_frequency_saturates_instead_of_scaling_linearly(self):
        """``k1``: more occurrences help, but not proportionally.

        Two documents of the SAME length, one carrying the term three times.
        Mutation: drop the ``f + norm`` denominator (use ``f``) → the score
        scales linearly and the second assertion fails.
        """
        one = ("one", "release alpha beta gamma delta")
        three = ("three", "release release release alpha beta")
        scores = bm25_scores("release", [one, three])
        assert scores["three"] > scores["one"]
        assert scores["three"] < 3 * scores["one"]

    def test_score_tie_breaks_by_id_not_by_dict_order(self):
        """#3019 + #10121: a tie must be a function of the data, and the data
        says NEWEST first.

        #3019 closed the "function of dict order" half with ``(-score, id)``,
        which — because ``id`` is a timestamp-minted ULID — was oldest-first.
        #10121 made it newest-first: the newest match must not be the one a
        ``limit`` truncates.
        """
        docs = [("b", "alpha beta"), ("a", "alpha beta")]
        assert [pid for pid, _ in bm25_rank("alpha", docs)] == ["b", "a"]
        assert [pid for pid, _ in bm25_rank("alpha", list(reversed(docs)))] == [
            "b", "a"]

    def test_recency_outranks_the_id_on_a_tie(self):
        """#10121: the tie follows the RECENCY key the caller supplies.

        Two documents tie on score; the OLDER id holds the NEWER timestamp, so
        recency must put it first even though the id key alone would not. This
        is the arm's half of the shared rule — without it the arm ranks its
        corpus newest-first and then throws the newest match away at
        ``limit`` (measured: absent from ``limit=5`` on a 21-row tie).
        """
        docs = [("b", "alpha beta"), ("a", "alpha beta")]
        ranked = [pid for pid, _ in bm25_rank(
            "alpha", docs, recency={"a": "2026-10-01T00:00:00Z",
                                    "b": "2026-01-01T00:00:00Z"})]
        assert ranked == ["a", "b"], ranked
        # An unlisted doc sorts as oldest, exactly as the shared clause does.
        ranked = [pid for pid, _ in bm25_rank(
            "alpha", docs, recency={"b": "2026-01-01T00:00:00Z"})]
        assert ranked == ["b", "a"], ranked

    def test_no_shared_token_is_absent_rather_than_zero(self):
        """"unscorable" and "scored zero" must stay distinguishable."""
        scores = bm25_scores("zebra", [("a", "release notes")])
        assert scores == {}

    def test_stopword_only_query_scores_nothing(self):
        assert bm25_scores("the of and", [("a", "the of and")]) == {}

    def test_keep_numeric_threads_the_caller_policy_never_decides_it(self):
        """The numeric-token policy is the CALLER's; this module only threads it.

        With the shipped default a BARE numeric token is dropped by the shared
        tokenizer, so `40` in "spend 40 on lights" contributes nothing; with
        ``keep_numeric=True`` it participates. The DEFAULT is untouched
        (asserted here rather than left implicit).

        #8933 UPDATE: the assertion used to ride `ERR-4032`, which was the wrong
        vehicle once the identifier ruling landed — an identifier's digits are
        preserved as the COMPOSITE alias (`err4032`), which is not a numeric
        policy at all and is present on BOTH sides (asserted explicitly below).
        The bare-numeric lever this test exists for is now measured on a bare
        numeric token, where ``keep_numeric`` is the only variable.
        """
        docs = [("d", "spend 40 on lights")]
        with_numeric = bm25_scores("spend 40 on lights", docs, keep_numeric=True)
        without = bm25_scores("spend 40 on lights", docs)
        assert with_numeric["d"] > without["d"] > 0
        # The identifier alias is NOT gated by keep_numeric: the token list
        # carries `err4032` under BOTH policies, because preserving an
        # identifier is a different control from the numeric-token policy
        # (#8933). keep_numeric only ADDS the bare `4032` on top.
        assert "err4032" in bm25_tokens("failure ERR-4032")
        assert "err4032" in bm25_tokens("failure ERR-4032", keep_numeric=True)
        assert "4032" not in bm25_tokens("failure ERR-4032")
        assert "4032" in bm25_tokens("failure ERR-4032", keep_numeric=True)


# ── the bound (#7704 / #1657) ─────────────────────────────────────────────
class TestLexicalWeightBound:
    def test_weight_is_clamped_below_the_dense_leg(self):
        """The BOUND. Mutation: return ``w`` without ``min(w, cap)`` → RED."""
        assert resolve_lexical_weight(50.0, 1.5) == LEXICAL_WEIGHT_MAX
        assert resolve_lexical_weight(1.0, 1.5) == 1.0
        assert resolve_lexical_weight(0.5, 1.5) == 0.5
        # ... and never above the leg it is fused WITH, whatever that is.
        assert resolve_lexical_weight(1.0, 0.4) == 0.4

    def test_default_production_weights_are_unchanged(self):
        """The recorded production default must survive the clamp untouched."""
        assert clamp_lexical_fusion_weight({"vector": 1.5}) == {
            "vector": 1.5, "fts": 1.0}
        assert clamp_lexical_fusion_weight(None) == {"fts": 1.0}

    def test_non_finite_and_negative_weights_degrade(self):
        assert resolve_lexical_weight(float("nan"), 1.5) == 1.0
        assert resolve_lexical_weight(float("inf"), 1.5) == 1.0
        assert resolve_lexical_weight(-3.0, 1.5) == 0.0
        assert resolve_lexical_weight("garbage", 1.5) == 1.0

    def test_a_disabled_dense_leg_does_not_zero_the_lexical_leg(self):
        """A dense weight of 0 DISABLES the dense leg; it does not unbind fts.

        ``TORTOISE_FUSION_WEIGHTS='{"vector": 0}'`` is the natural spelling of
        "the dense leg does not contribute". Capping the lexical weight at the
        dense weight then makes it 0.0 too, and ``rrf_fusion`` scores EVERY
        candidate ``weight * 1/(k+rank)`` — so both legs zero, every hit ties
        at 0.0, the fused order silently degrades to id order, and a caller
        with ``threshold > 0`` gets NO rows at all. The bound's own rule is
        "never more than the strongest leg it is fused WITH"; a disabled leg
        is not the strongest, so the faithful cap is ``LEXICAL_WEIGHT_MAX``.

        Mutation: ``cap = 0.0`` when ``dense <= 0`` (the pre-fix expression
        ``min(LEXICAL_WEIGHT_MAX, dense) if dense > 0 else 0.0``) → both
        assertions below read 0.0 and this reds.
        """
        assert clamp_lexical_fusion_weight({"vector": 0}) == {
            "vector": 0, "fts": LEXICAL_WEIGHT_MAX}
        assert clamp_lexical_fusion_weight({"vector": -2.0}) == {
            "vector": -2.0, "fts": LEXICAL_WEIGHT_MAX}
        assert resolve_lexical_weight(1.0, 0.0) == LEXICAL_WEIGHT_MAX
        # ... and a LOWER explicit lexical weight is still honoured: the
        # unbinding is a ceiling, not a floor.
        assert resolve_lexical_weight(0.5, 0.0) == 0.5

    def test_unbounded_weight_lets_a_lexical_also_ran_beat_the_dense_answer(self):
        """The fused-boundary arithmetic, on the REAL ``rrf_fusion``.

        ``D`` is the dense leg's best hit and the lexical leg's also-ran;
        ``L`` is lexically perfect but dense-weak. With the bound, D wins — the
        read's actual answer keeps the top. Drop the bound (pass the raw 50.0,
        which is the mutation) and L wins: the #7704 shape.
        """
        dense = [("D", 0.9), ("m", 0.5), ("L", 0.1)]
        lexical = [("L", 9.0), ("D", 1.0)]

        bounded = rrf_fusion(
            [dense, lexical], k=60,
            strategy_names=["dense", "lexical"],
            weights={"dense": 1.5,
                     "lexical": resolve_lexical_weight(50.0, 1.5)},
        )
        unbounded = rrf_fusion(
            [dense, lexical], k=60,
            strategy_names=["dense", "lexical"],
            weights={"dense": 1.5, "lexical": 50.0},
        )
        assert next(iter(bounded)) == "D", (
            f"bounded fusion must keep the dense leg's best hit first; {bounded}")
        assert next(iter(unbounded)) == "L", (
            "the mutation (an unclamped weight) is expected to put the "
            f"lexical also-ran first — got {unbounded}")


# ── the read path ─────────────────────────────────────────────────────────
class TestReadPath:
    def test_arm_is_on_by_default(self, monkeypatch, lexicon_embedder):
        """OWNER RULING 2026-10-28: with NO env set, the arm RUNS.

        Pins the default in the direction that matters — a regression back to
        fail-safe-OFF would silently retire the arm and nothing else would
        notice. Mutation: ``env_flag(BM25_ENV, False)`` → no
        ``mechanism="bm25"`` entry, this reds.
        """
        monkeypatch.delenv("TORTOISE_BM25_LEXICAL", raising=False)
        sdk, _target = _e2e_sdk()
        trace: list[dict] = []
        hits = sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=trace)
        assert hits, "guard: the read must return rows"
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   for e in trace), (
            "with no TORTOISE_BM25_LEXICAL in the environment the arm must run "
            f"(owner ruling 2026-10-28); trace={trace}")

    def test_the_opt_out_restores_the_shipped_scorer(self, monkeypatch,
                                                     lexicon_embedder):
        """``TORTOISE_BM25_LEXICAL=0`` ⇒ the lexical rows are the SHIPPED scorer's.

        Not a tautology: the read runs, and each hit's ``scores["fts"]`` must
        match the shipped leg's score for that id — a value BM25 does NOT
        produce on this fixture (1.674 vs the shipped 3.0). Mutation: make
        ``bm25_enabled()`` return True → the scores change and this reds.
        """
        # Explicit, not ambient: this assertion is about the UNSET gate, so it
        # must not depend on the process env a sibling test happened to leave.
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        sdk, target = _e2e_sdk()
        graph = sdk._get_proj().g
        from tortoise.search_engine import run_fts_query
        shipped = dict(run_fts_query(graph, QUERY, entity_type="point",
                                     limit=20))
        bm25_scores = dict(run_bm25_query(graph, QUERY, entity_type="point",
                                          limit=20))
        assert target in shipped and target in bm25_scores, (
            "guard: both scorers must score the record on THIS graph")
        assert shipped[target] != bm25_scores[target], (
            "guard: the two scorers must disagree on this fixture, or the "
            "assertion below cannot tell them apart")

        trace: list[dict] = []
        hits = sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=trace)
        assert all(e.get("mechanism") != LEXICAL_MECHANISM_BM25
                   for e in trace), (
            "the BM25 arm must not run when TORTOISE_BM25_LEXICAL=0 opts "
            "out")
        assert hits, "guard: the read must return rows to compare"
        assert all(h["id"] in shipped for h in hits), (
            "guard: every hit must be a row of the graph the shipped scores "
            "were computed on, or the comparison below goes vacuous")
        for h in hits:
            assert h["scores"]["fts"] == pytest.approx(shipped[h["id"]]), (
                f"hit {h['id']} was not scored by the shipped leg")

    def test_dense_leg_alone_does_not_return_the_record_in_the_window(
            self, lexicon_embedder):
        """The dense-only miss, asserted explicitly.

        Honest scope: the dense leg is what the read fuses when no lexical leg
        can express the token. On this fixture it never returns the
        rare-token record inside a THREE-row window — the window the reader
        actually sees — while it does return it at a wide limit (measured:
        the target sits at similarity 0.414 against the topic documents'
        0.95, so it is in the pool but always behind them). The lexical arm is
        therefore not decorative: it is the only signal that puts the record
        anywhere but last. Mutation: make the fixture's embedder recognise the
        rare token (i.e. delete the signal gap) → the record appears in the
        window and this reds — the test fails exactly when the gap it
        documents stops existing.
        """
        graph, ids = _graph_with_corpus()
        qv = _query_vec(lexicon_embedder, QUERY)
        window = [pid for pid, _ in run_vector_query(
            graph, qv, limit=3, is_embedded=True, entity_type="point")]
        wide = [pid for pid, _ in run_vector_query(
            graph, qv, limit=20, is_embedded=True, entity_type="point")]
        assert ids["target"] not in window, (
            f"fixture invariant: the dense leg's 3-row window is all topic "
            f"documents; got {window}")
        assert ids["target"] in wide, (
            "fixture invariant: the record IS in the dense pool (the miss is "
            "a RANKING miss, not absence) — if this fails the fixture no "
            "longer models a dense window")

    def test_bm25_leg_returns_the_record_first(self, lexicon_embedder):
        """The new path finds what the dense window missed, and ranks it 1st."""
        graph, ids = _graph_with_corpus()
        rows = run_bm25_query(graph, QUERY, entity_type="point", limit=20)
        assert rows and rows[0][0] == ids["target"], (
            f"expected the rare-token record first; got {rows}")
        assert rows[0][1] > 3 * rows[1][1], (
            "the rare term's idf must separate it clearly, not marginally")

    def test_bm25_arm_lifts_the_record_in_the_shipped_read(
            self, monkeypatch, lexicon_embedder):
        """End-to-end on the real fused read: gate OFF vs gate ON.

        Gate OFF is the pre-change path — the dense leg plus the SHIPPED
        lexical scorer. Gate ON re-scores the lexical slot with BM25, and the
        record climbs. The lexical slot is metered ONCE either way (the arm
        re-scores the same slot; it is not a second leg).

        Honest bound statement. The lexical leg's fusion weight is capped below
        the dense leg's (``LEXICAL_WEIGHT_MAX``), so the arm RE-RANKS within the
        pool rather than vaulting past the dense leg's whole list — that is the
        recorded production balance (``{"vector": 1.5}``, k=60, #1657) and
        moving it is a measurement decision (#8486), not a lane's. So BOTH
        claims below are strict on this fixture — the fused POSITION and the
        target's LEXICAL rank — and the re-measurement in the next paragraph is
        why the fused one is strict again rather than "does not regress".

        Mutation: drop the ``raw_results["fts"] = _bm25_hits`` assignment (the
        lexical arm disabled) → the lexical score assertions below go red.

        #10121 (2026-10-27) RE-MEASURED this fixture at its own head, embedded
        lane: gate-off fused rrf 0.0410/0.0403/0.0397/0.0391/0.0385 with the
        target LAST (5/5); gate-on 0.0407/0.0401/0.0395/0.0394/0.0388 with the
        target 3/5. So the lift is 5/5 → 3/5 — the same delta the pre-#10121
        measurement recorded — and the claim below is STRICT (``<``) again.

        Why an intermediate state of this PR had it relaxed to ``<=``, and why
        that reading is superseded rather than dropped: #10121 flips the DENSE
        leg's equal-distance tie (the four topic documents sit in one: vector
        0.9496 ×4 vs the target's 0.4142, while the lexical leg is NOT tied —
        topics 3.0 vs the target's 2.0). Flipping the dense order alone while
        the lexical leg still ordered oldest-first handed the same row different
        ranks on the two legs, so the best lexical rank stopped pairing with the
        best dense rank and the fused spread compressed — a measured intermediate
        state (gate-on 0.0400/0.0398/0.0397/0.0396/0.0395, target last in both),
        not the shipped behaviour. The arm's own recency sort
        (``bm25_rank(recency=...)``) lands in the SAME change and restores the
        pairing; the numbers above are the post-pairing re-measurement, which is
        why this assertion is strict and why no relaxation is left behind.

        Honest scope, unchanged from the fixture note at the module top: the
        embedder here is the lexicon stand-in, so this proves the MECHANISM. The
        live numeric improvement is #8486's measurement (postponed by owner
        ruling) and is NOT claimed here.

        What stays STRICT is the arm's own mechanism: with the gate on the
        target carries the strongest lexical score (1.3863 vs 0.5754 for every
        topic doc) and therefore the best LEXICAL rank — asserted below, and it
        is what the arm is for. The fused POSITION must clear strictly too
        (measured 5/5 -> 3/5 at this head, asserted below): a ``<=`` there would
        be satisfied by an arm that changes nothing.
        """
        sdk, target = _e2e_sdk()
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        from tortoise import retrieval_metering
        tally = retrieval_metering.arm(org_id=None)
        _before = tally.legs.get("fts", 0)
        off = sdk.tortoise_fts_query(QUERY, limit=10)
        off_fts_runs = tally.legs.get("fts", 0) - _before
        off_ids = [h["id"] for h in off]
        assert target in off_ids, (
            f"fixture invariant: the target is in the pool; got {off_ids}")

        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        _before = tally.legs.get("fts", 0)
        on_trace: list[dict] = []
        on = sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=on_trace)
        on_ids = [h["id"] for h in on]

        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   for e in on_trace), (
            f"the lexical leg must be BM25-scored when the gate is on; "
            f"trace={on_trace}")
        # STRICT (#10121 re-measurement): measured 5/5 -> 3/5 at this head. A
        # ``<=`` here would be satisfied by an arm that changes nothing.
        assert on_ids.index(target) < off_ids.index(target), (
            "the BM25-scored lexical leg must rank the rare-token record "
            f"BETTER than the shipped scorer did, not merely not-worse; "
            f"off={off_ids}, on={on_ids}")

        def _lexical_rank(rows: list[dict]) -> int:
            """Rank by lexical SCORE within the page: rows scoring strictly
            higher than the target. Strictly-better rather than position, so a
            tie among the peers cannot make the claim look tighter than it is."""
            target_score = next(
                r["scores"]["fts"] for r in rows if r["id"] == target)
            return sum(1 for r in rows
                       if r["id"] != target
                       and r["scores"]["fts"] > target_score)

        # THE STRICT CLAIM (#10121 review): the arm's mechanism is a lexical
        # re-scoring, so the lexical rank is where it must show. Measured
        # 4 -> 0 on this fixture.
        assert _lexical_rank(on) < _lexical_rank(off), (
            "the arm must strictly improve the target's LEXICAL rank; "
            f"off={_lexical_rank(off)}, on={_lexical_rank(on)}")
        # The lexical SCORE the fusion consumed, not just the order. This is the
        # arm's mechanism and the assertion that must stay strict (#10121).
        on_target = next(h for h in on if h["id"] == target)
        assert on_target["scores"]["fts"] > max(
            h["scores"]["fts"] for h in on if h["id"] != target), (
            f"the record must carry the strongest lexical score; {on_target}")
        # One lexical SLOT, one tally entry — the arm re-scores, it does not
        # add a second lexical leg (#8488 review).
        assert off_fts_runs == 1, f"one lexical slot, one tally entry: {tally}"
        assert tally.legs.get("fts", 0) - _before == 1, (
            f"one lexical slot, one tally entry: {tally}")

    def test_arm_that_finds_nothing_leaves_the_shipped_rows_intact(
            self, monkeypatch, force_sparse_tfidf):
        """Fail-safe: the shipped leg's rows survive an empty BM25 arm.

        ``the`` is dropped by the shared tokenizer, so the BM25 arm returns [].
        The shipped scorer does not filter stopwords, so it still answers. The
        gate-on read must therefore be exactly the gate-off read — same ids,
        same lexical scores — rather than a read with an empty lexical slot.

        Mutation: assign ``raw_results["fts"] = _bm25_hits`` WITHOUT the
        ``if _bm25_hits`` guard → the shipped rows are replaced by [] and the
        read falls through to the TF-IDF tier, so ``scores["fts"]`` becomes
        None and this reds.
        """
        sdk = TortoiseSDK(
            os.path.join(tempfile.mkdtemp(prefix="bm25_8488_fb_"), "t.db"),
            namespace=f"bm25_fb_{os.urandom(6).hex()}")
        _SDKS.append(sdk)
        for content in ("the release notes", "the rollout plan",
                        "the invoice summary"):
            sdk.create_point("statement", content)

        query = "the"
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        off = sdk.tortoise_fts_query(query, limit=5)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        on_trace: list[dict] = []
        on = sdk.tortoise_fts_query(query, limit=5, leg_trace=on_trace)

        assert off and on, ("guard against a vacuous comparison: the shipped "
                            "scorer finds the stopword documents")
        assert all(h["scores"]["fts"] is not None for h in on), (
            "the lexical scores must still come from the shipped leg")
        assert [(h["id"], h["similarity"]) for h in on] == [
            (h["id"], h["similarity"]) for h in off], (
            "a lexical arm that finds nothing must not change the read")
        assert not [e for e in on_trace
                    if e.get("mechanism") == LEXICAL_MECHANISM_BM25
                    and e.get("count")], (
            f"no BM25 rows were produced, so none may enter the read; "
            f"trace={on_trace}")

    def test_query_with_no_lexical_match_leaves_the_fused_set_alone(
            self, monkeypatch, lexicon_embedder):
        """Regression: fusion never corrupts a read the lexical leg has no
        opinion about.

        The fixture's documents share no token with the query, so the lexical
        arm contributes nothing and the fused result set must be exactly what
        the gate-off path returns — same ids, same scores. Mutation: fuse an
        empty lexical list as if it had votes (e.g. add a constant to every
        candidate) → the scores move and this reds.
        """
        sdk = TortoiseSDK(
            os.path.join(tempfile.mkdtemp(prefix="bm25_8488_no_"), "t.db"),
            namespace=f"bm25_no_{os.urandom(6).hex()}")
        _SDKS.append(sdk)
        for content in TOPIC_DOCS:
            sdk.create_point("statement", content)

        query = "zebra xylophone"
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        off = sdk.tortoise_fts_query(query, limit=5)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        on_trace: list[dict] = []
        on = sdk.tortoise_fts_query(query, limit=5, leg_trace=on_trace)

        assert off, "guard: the dense leg answers this query on this fixture"
        # The arm must actually be WIRED for this test to say anything: an
        # unwired arm would also leave the fused set alone. It ran (mechanism
        # written) and matched nothing (count 0) — which is the shape the
        # no-op claim is about.
        arm_entries = [e for e in on_trace
                       if e.get("mechanism") == LEXICAL_MECHANISM_BM25]
        assert arm_entries, (
            f"the arm must have run for this read; trace={on_trace}")
        assert all(not e.get("count") for e in arm_entries), (
            f"fixture invariant: nothing in this corpus matches the query, so "
            f"the arm must match nothing; trace={on_trace}")
        assert [(h["id"], h["similarity"]) for h in on] == [
            (h["id"], h["similarity"]) for h in off]

    def test_lexical_weight_is_bounded_at_the_read_path(
            self, monkeypatch, lexicon_embedder):
        """The BOUND as the READ applies it — not only as a pure function.

        ``TORTOISE_FUSION_WEIGHTS`` is ``json.loads``-ed, so it can carry any
        number. With the gate on, an unbounded ``{"fts": 50}`` must produce the
        SAME fused order as the clamped ``{"fts": 1.0}`` — otherwise the
        lexical leg decides the order by itself (#7704). Mutation: delete the
        ``clamp_lexical_fusion_weight`` call in ``tortoise_fts_query`` → the
        two reads diverge and this reds.
        """
        sdk, _target = _e2e_sdk()
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")

        monkeypatch.setenv("TORTOISE_FUSION_WEIGHTS", '{"vector": 1.5, "fts": 50}')
        unbounded = [(h["id"], h["similarity"])
                     for h in sdk.tortoise_fts_query(QUERY, limit=10)]

        monkeypatch.setenv("TORTOISE_FUSION_WEIGHTS", '{"vector": 1.5, "fts": 1.0}')
        clamped = [(h["id"], h["similarity"])
                   for h in sdk.tortoise_fts_query(QUERY, limit=10)]

        assert unbounded and clamped, "guard: both reads must return rows"
        assert unbounded == clamped, (
            "an over-cap lexical weight must be clamped to the same value as "
            "the cap, so the fused order is identical")

    def test_zero_dense_weight_keeps_a_lexical_ranking_at_the_read_path(
            self, monkeypatch, lexicon_embedder):
        """``{"vector": 0}`` must not take the WHOLE read down with it.

        The pure-function test above pins the cap; this one pins what the cap
        is FOR — the read still ranks. With the dense leg's weight at 0 the
        lexical leg is the only contributor, so the fused order must be the
        lexical order and every fused score must be positive. Pre-fix (cap =
        0.0) every candidate scored ``0.0``: the order became id order and
        every ``similarity`` was 0.0 — the assertions below red on both.

        Fixture note: the target is created LAST here, so it holds the HIGHEST
        id. On this graph id order and the lexical order disagree, which is
        what makes ``hits[0] == target`` discriminating rather than a
        coincidence of creation order.
        """
        sdk = TortoiseSDK(
            os.path.join(tempfile.mkdtemp(prefix="bm25_8488_zd_"), "t.db"),
            namespace=f"bm25_zd_{os.urandom(6).hex()}")
        _SDKS.append(sdk)
        for content in TOPIC_DOCS_END_TO_END:
            sdk.create_point("statement", content)
        target = sdk.create_point("statement", TARGET_DOC)["id"]
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        monkeypatch.setenv("TORTOISE_FUSION_WEIGHTS", '{"vector": 0}')

        trace: list[dict] = []
        hits = sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=trace)
        assert hits, "guard: the read must return rows to compare"
        assert hits[0]["id"] != min(h["id"] for h in hits), (
            "guard: the target must not be the lowest id, or id order would "
            f"satisfy the ranking assertion; got {[h['id'] for h in hits]}")
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   for e in trace), (
            f"guard: the arm must have run for this read; trace={trace}")
        assert hits[0]["id"] == target, (
            "with the dense leg disabled the fused order must be the lexical "
            f"order (rare-token record first); got {[h['id'] for h in hits]}")
        assert all(h["similarity"] > 0.0 for h in hits), (
            "a zeroed lexical weight zeroes EVERY fused score — the ranking "
            f"then survives only as id order; got {hits}")

    def test_expansion_passes_stand_the_arm_down(
            self, monkeypatch, lexicon_embedder):
        """A4/C2 replace the lexical slot AFTER the arm — so the arm stands down.

        ``_search_keys_prf_expansion`` (A4, ``search_keys_prf``) and
        ``_entity_key_expansion_pass`` (C2, ``entity_key_expansion``) each
        RE-RUN the shipped scorer and overwrite ``raw_results["fts"]``. An arm
        that ran before them has its rows discarded while its trace entry still
        claims ``mechanism="bm25"`` — a measurement (starting with #8486)
        would then baseline a read whose lexical order is the SHIPPED scorer's
        while reading a trace that says BM25 produced it.

        Control first: with the same graph and query and NO expansion flag, the
        arm does run — otherwise the stand-down assertion below would pass for
        the wrong reason. Mutation: drop the ``_prf_supersedes_the_arm`` term
        from the gate → the flagged reads carry a ``mechanism="bm25"`` entry
        again and this reds.
        """
        sdk, _target = _e2e_sdk()
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")

        control_trace: list[dict] = []
        # ruff F841: the call's EFFECT is `control_trace`, asserted below — the
        # return value is not read here.
        sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=control_trace)
        assert not [e for e in control_trace
                    if e.get("mechanism") == LEXICAL_MECHANISM_BM25], (
            "guard: gate OFF must never claim a BM25 lexical entry")

        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        arm_trace: list[dict] = []
        sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=arm_trace)
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   and e.get("count") for e in arm_trace), (
            "guard (control): without an expansion flag the arm DOES run on "
            f"this fixture; trace={arm_trace}")

        for lever in ({"search_keys_prf": True},
                      {"entity_key_expansion": True}):
            monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
            off = [(h["id"], h["similarity"])
                   for h in sdk.tortoise_fts_query(QUERY, limit=10, **lever)]
            monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
            on_trace: list[dict] = []
            on = [(h["id"], h["similarity"])
                  for h in sdk.tortoise_fts_query(
                      QUERY, limit=10, leg_trace=on_trace, **lever)]
            assert not [e for e in on_trace
                        if e.get("mechanism") == LEXICAL_MECHANISM_BM25], (
                "the arm must not claim the lexical slot when a later pass "
                f"replaces it ({lever}); trace={on_trace}")
            assert on == off, (
                "a stood-down arm leaves the read exactly as the gate-off "
                f"read ({lever}); off={off}, on={on}")

    def test_relevance_floor_stands_the_arm_down(
            self, monkeypatch, lexicon_embedder):
        """The #4028 floor stand-down, pinned — and it is load-bearing.

        On a floored read the vector leg's answer is "nothing relevant", and
        the arm must not speak for that read: with ``raw_results`` otherwise
        empty the read returns ``_with_read_status([])`` as its DECLARED
        answer, and an arm that ran first would put ``raw_results["fts"]``
        back, so ``if not raw_results:`` would stop firing and a read whose
        answer is "nothing relevant" would start returning BM25 rows. On THIS
        fixture the shipped lexical leg still answers, so the observable pin is
        the other half of the same defect: the arm must not re-score the
        lexical slot at all, i.e. the floored read is byte-identical to the
        gate-OFF floored read and carries no ``mechanism="bm25"`` entry.

        Control first (same graph, same query, no floor): the arm DOES run —
        otherwise the stand-down assertion would pass for the wrong reason.
        Mutation: drop ``and "vector" not in _floored_legs`` from the arm gate
        → the floored read carries a ``mechanism="bm25"`` entry and its rows
        move, and this reds.
        """
        sdk, _target = _e2e_sdk()
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")

        control_trace: list[dict] = []
        control = sdk.tortoise_fts_query(QUERY, limit=10,
                                         leg_trace=control_trace)
        assert control, "guard: the un-floored read answers on this fixture"
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   and e.get("count") for e in control_trace), (
            "guard (control): with no floor the arm runs and matches on this "
            f"fixture; trace={control_trace}")

        # Above every similarity this fixture produces (topic docs ~0.95, the
        # rare-token record ~0.41), so the vector leg is emptied.
        monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY", "0.999")
        floored_trace: list[dict] = []
        floored = [(h["id"], h["similarity"])
                   for h in sdk.tortoise_fts_query(
                       QUERY, limit=10, leg_trace=floored_trace)]
        assert any(e.get("reason") == "below_relevance_floor"
                   for e in floored_trace), (
            "guard: the relevance floor must actually have emptied the "
            f"vector leg; trace={floored_trace}")
        assert not [e for e in floored_trace
                    if e.get("mechanism") == LEXICAL_MECHANISM_BM25], (
            "the arm must not run on a floored read — it would turn a "
            f"\"nothing relevant\" answer into rows; trace={floored_trace}")

        # The same floor with the gate OFF is the reference read: a stood-down
        # arm changes nothing about it (not the ids, not the fused scores).
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        off_floored = [(h["id"], h["similarity"])
                       for h in sdk.tortoise_fts_query(QUERY, limit=10)]
        assert floored and floored == off_floored, (
            "a floored read must be exactly the gate-off floored read; "
            f"off={off_floored}, on={floored}")

    def test_a_server_lane_read_never_runs_the_arm(
            self, monkeypatch, lexicon_embedder):
        """The arm is EMBEDDED-only, and that bound is pinned.

        On the server lane ``run_vector_query`` is index-backed precisely so a
        read need not scan, and this leg has no measured baseline there
        (#8486). Removing ``is_embedded`` from the gate leaves every other test
        green while a production read starts pulling up to
        ``DEFAULT_BM25_CORPUS_CAP`` rows of ``content`` into Python per call.

        The projection here is embedded; only its ``_is_embedded`` report is
        flipped, which is exactly the value the gate reads
        (``getattr(proj, "_is_embedded", True)``). Control first: the same
        graph with the report untouched DOES run the arm. Mutation: drop
        ``is_embedded and`` from the gate → the flipped read carries a
        ``mechanism="bm25"`` entry and this reds.
        """
        sdk, _target = _e2e_sdk()
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")

        control_trace: list[dict] = []
        sdk.tortoise_fts_query(QUERY, limit=10, leg_trace=control_trace)
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   for e in control_trace), (
            "guard (control): on the embedded lane the arm runs; "
            f"trace={control_trace}")

        proj = sdk._get_proj()
        monkeypatch.setattr(proj, "_is_embedded", False, raising=False)
        server_trace: list[dict] = []
        server = sdk.tortoise_fts_query(QUERY, limit=10,
                                        leg_trace=server_trace)
        assert server, ("guard: the shipped legs still answer when the lane is "
                        "reported as the server lane")
        assert not [e for e in server_trace
                    if e.get("mechanism") == LEXICAL_MECHANISM_BM25], (
            "the in-process corpus scan is embedded-only; it must not run on "
            f"the server lane; trace={server_trace}")

    def test_a_windowed_corpus_read_never_replaces_the_shipped_rows(
            self, monkeypatch, lexicon_embedder):
        """A corpus WIDER than the cap declines — the whole-label rows survive.

        The shipped leg answers over the WHOLE label (index-backed); the arm
        reads a ``cap``-row WINDOW of it. Replacing the shipped rows with that
        window makes the read a subset dressed as a complete ranking, silently,
        and #8486 would baseline it as the arm's answer. So the leg returns
        ``[]`` and records ``declined`` — NOT ``degraded``,
        which would relabel this complete read as an impaired memory (see
        ``TestDeclineIsNotAReadDegradation``) — the same fail-safe an empty
        arm gets.

        The cap is driven through the REAL sdk wiring by wrapping
        ``run_bm25_query`` (the sdk imports it at call time), so the assertion
        is about the composition, not about a helper called by the test. Since
        #10121 the window holds the NEWEST ``cap`` rows, and the target is
        created LAST — i.e. it is INSIDE a one-row window rather than outside
        it (the pre-#10121 story read the other way round; the decline does not
        depend on the target's position, only on the corpus exceeding the cap).
        Control first: the same wiring with a cap the corpus fits in DOES change
        the read — otherwise "unchanged" below could be explained by the arm not
        being wired at all.

        Mutation: return ``ranked`` instead of ``[]`` when over the cap → the
        arm's windowed rows enter the read and ``on == off`` reds.
        """
        import tortoise.search_engine as search_engine

        sdk = TortoiseSDK(
            os.path.join(tempfile.mkdtemp(prefix="bm25_8488_cap_"), "t.db"),
            namespace=f"bm25_cap_{os.urandom(6).hex()}")
        _SDKS.append(sdk)
        for content in TOPIC_DOCS_END_TO_END:
            sdk.create_point("statement", content)
        target = sdk.create_point("statement", TARGET_DOC)["id"]

        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        off = [(h["id"], h["similarity"])
               for h in sdk.tortoise_fts_query(QUERY, limit=10)]
        assert off and off[0][0] != target, (
            f"guard: the shipped read ranks the record down; got {off}")

        real = search_engine.run_bm25_query

        def _capped(cap, *args, **kwargs):
            kwargs["corpus_cap"] = cap
            return real(*args, **kwargs)

        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")

        control_trace: list[dict] = []
        monkeypatch.setattr(search_engine, "run_bm25_query",
                            lambda *a, **kw: _capped(1000, *a, **kw))
        control = [(h["id"], h["similarity"])
                   for h in sdk.tortoise_fts_query(
                       QUERY, limit=10, leg_trace=control_trace)]
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   and e.get("count") for e in control_trace), (
            "guard (control): a corpus that fits the cap must produce BM25 "
            f"rows; trace={control_trace}")
        assert control != off, (
            "guard (control): the arm must be WIRED — a roomy cap changes the "
            f"read; off={off}, control={control}")

        tight_trace: list[dict] = []
        monkeypatch.setattr(search_engine, "run_bm25_query",
                            lambda *a, **kw: _capped(1, *a, **kw))
        tight = [(h["id"], h["similarity"])
                 for h in sdk.tortoise_fts_query(
                     QUERY, limit=10, leg_trace=tight_trace)]
        declined = [e for e in tight_trace
                    if e.get(LEXICAL_DECLINED_KEY)]
        assert declined, (
            "the arm must have RUN (and declined) for this read to say "
            f"anything; trace={tight_trace}")
        assert all(e.get("degraded") is False for e in declined), (
            "a declined arm does not IMPAIR the read — the shipped leg answered "
            f"over the whole label; trace={tight_trace}")
        assert not [e for e in declined if e.get("count")], (
            f"a declined read contributes NO rows; trace={tight_trace}")
        assert tight == off, (
            "a corpus wider than the cap must leave the shipped whole-label "
            f"rows exactly as they were; off={off}, tight={tight}")

    def test_numeric_token_policy_is_threaded_not_decided(
            self, monkeypatch, force_sparse_tfidf):
        """#8933 is the OWNER's reopen — the arm must not decide it.

        A numeric identifier query: with the shipped default
        (``keep_numeric=False``) the tokenizer drops ``4032``, so the BM25 arm
        yields nothing and the read keeps the shipped leg's rows. With the
        caller's ``keep_numeric=True`` opt-in the arm answers. Mutation: pass
        ``keep_numeric=True`` in the arm call instead of the caller's value →
        the default read starts claiming BM25 rows and this reds.
        """
        sdk = TortoiseSDK(
            os.path.join(tempfile.mkdtemp(prefix="bm25_8488_num_"), "t.db"),
            namespace=f"bm25_num_{os.urandom(6).hex()}")
        _SDKS.append(sdk)
        sdk.create_point("statement", "invoice ticket 4032 paid")
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")

        default_trace: list[dict] = []
        default = sdk.tortoise_fts_query("4032", limit=5,
                                         leg_trace=default_trace)
        assert default, "guard: the shipped leg answers a raw numeric query"
        assert not [e for e in default_trace
                    if e.get("mechanism") == LEXICAL_MECHANISM_BM25
                    and e.get("count")], (
            "the DEFAULT numeric policy must not be overridden by the arm; "
            f"trace={default_trace}")

        opted_trace: list[dict] = []
        opted = sdk.tortoise_fts_query("4032", limit=5, keep_numeric=True,
                                       leg_trace=opted_trace)
        assert opted, "guard: the opt-in read must return rows"
        # ``count`` is the load-bearing half: ``_record`` writes the mechanism
        # key whenever the leg ran and did NOT decline, so ``mechanism ==
        # "bm25"`` alone is satisfied by an arm that ran and matched NOTHING
        # (count=0) — i.e. by the very regression this test exists to catch (the
        # arm call ignoring the caller's policy). Only a MATCH proves the policy
        # reached it.
        assert any(e.get("mechanism") == LEXICAL_MECHANISM_BM25
                   and e.get("count") for e in opted_trace), (
            "the caller's keep_numeric=True must reach the BM25 arm (it must "
            f"MATCH, not merely run); trace={opted_trace}")


class TestDeclineIsNotAReadDegradation:
    """#8488 follow-up: a DECLINED arm is not an IMPAIRED read.

    The cap's decline used to record ``degraded=True``. That flag is not
    leg-local: ``read_status.classify_leg_trace`` reports the whole READ as
    ``degraded`` when any entry carries it, and ``ask_lane`` folds it into
    ``retrieval_degraded`` the same way. So on a store at the cap, enabling the
    arm relabelled every successful read "the memory layer is IMPAIRED".

    Why that relabel was WRONG — stated on the term's own ground, not on the
    row count. ``degraded`` is the recorded term for a store that did not
    answer or a leg that did not run, and it deliberately OUTRANKS a
    successful read: a rows-returning read with a genuinely missing leg IS
    correctly ``degraded``, because a partial result is not a clean one
    (``classify_read_status`` folds it on top of the content mapping;
    ``tests/test_read_status.py``'s
    ``test_a_leg_that_did_not_run_is_the_boundary_degraded_term`` pins
    exactly that, ``hit_count=3`` and all). Nothing was
    impaired HERE: every leg ran, and the shipped lexical leg answered over
    the WHOLE label; only the arm's alternative SCORER declined. So the
    decline is not an impairment to record — not "a degraded read with rows
    is forbidden" (no such property exists, and a lane that believed in one
    could "fix" the classifier into calling a genuinely absent vector leg on
    a rows-returning read a clean hybrid read).

    ``mechanism="bm25"`` had the mirror-image problem: the key answers "which
    path produced these rows", and a declined arm produced none, so #8486's
    baseline (which counts ``mechanism == "bm25"``) counted reads where BM25
    contributed nothing.

    Mutations, each APPLIED to this tree and the decline/cap selection
    re-run (not reasoned about), naming exactly what goes red:

    * restore ``degraded=True`` on the decline branch →
      ``test_a_declined_arm_does_not_report_the_read_degraded``,
      ``test_a_declined_arm_entry_is_not_itself_degraded``,
      ``test_the_shared_trace_fold_does_not_read_the_decline_as_a_degradation``
      and the two eval-fold tests
      (``test_the_leg_health_predicate_does_not_read_the_decline_as_a_dead_leg``,
      ``test_enabling_the_arm_does_not_move_the_resume_gate``) red, along with
      the two older decline assertions
      (``TestCorpusCap.test_over_the_cap_the_leg_declines_instead_of_window_ranking``
      and ``TestReadPath.test_a_windowed_corpus_read_never_replaces_the_shipped_rows``);
    * restore ``mechanism=LEXICAL_MECHANISM_BM25`` for a declined entry →
      ``test_a_declined_arm_claims_no_bm25_mechanism`` reds (as does
      ``TestCorpusCap``'s mechanism assertion);
    * delete the ``declined=declined`` passthrough in ``_record`` → the
      decline becomes invisible and ALL FIVE decline tests above red on their
      guards (the control test below does not trigger a decline, so it is
      deliberately unaffected by all three mutations — that is what makes it a
      control).

    Non-vacuity is claimed PER MUTATION, never as "all of these tests are red
    on the pre-fix tree": this module imports ``LEXICAL_DECLINED_KEY`` at
    module scope, so reverting ``tortoise/search_engine.py`` alone reds the
    file at COLLECTION and proves nothing about any assertion here.
    """

    @staticmethod
    def _sdk(monkeypatch):
        sdk = TortoiseSDK(
            os.path.join(tempfile.mkdtemp(prefix="bm25_8488_decline_"), "t.db"),
            namespace=f"bm25_decline_{os.urandom(6).hex()}")
        _SDKS.append(sdk)
        for content in TOPIC_DOCS_END_TO_END:
            sdk.create_point("statement", content)
        sdk.create_point("statement", TARGET_DOC)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        return sdk

    @staticmethod
    def _declining(monkeypatch):
        """Drive the cap through the REAL sdk wiring (the sdk imports at call
        time), so the composition is what is measured."""
        import tortoise.search_engine as search_engine

        real = search_engine.run_bm25_query

        def _capped(*args, **kwargs):
            kwargs["corpus_cap"] = 1  # the fixture corpus is 5 rows
            return real(*args, **kwargs)

        monkeypatch.setattr(search_engine, "run_bm25_query", _capped)

    def test_a_declined_arm_does_not_report_the_read_degraded(
            self, monkeypatch, lexicon_embedder):
        """The read returns rows, every leg ran, and the status says so.

        The assertion is on the READ's recorded status (the user-facing term),
        not on the trace: that is the surface the flag actually reached. The
        trace assertion alongside it is what proves the arm DID run and
        decline — otherwise an `available` result could be explained by the arm
        never running at all.
        """
        sdk = self._sdk(monkeypatch)
        self._declining(monkeypatch)
        sink: dict = {}
        trace: list[dict] = []
        rows = sdk.tortoise_fts_query(QUERY, limit=10, read_status_out=sink,
                                      leg_trace=trace)
        assert rows, "guard: the shipped legs must still answer this read"
        assert [e for e in trace if e.get(LEXICAL_DECLINED_KEY)], (
            "guard: the arm must have declined for this test to say anything; "
            f"trace={trace}")
        assert sink.get("status") == "available", (
            "a complete read (rows returned, every leg ran) must not be "
            f"reported as an impaired memory; status={sink.get('status')!r} "
            f"trace={trace}")

    def test_a_declined_arm_entry_is_not_itself_degraded(
            self, force_sparse_tfidf):
        """The leg-local half: the decline carries ``declined``, not `degraded`.

        Pinned separately from the read status because the aggregation is one
        consumer of the flag and ``ask_lane``'s ``retrieval_degraded`` is
        another; a leg entry that says ``degraded`` lies to both.
        """
        graph, _ids = _graph_with_corpus()
        trace: list[dict] = []
        rows = run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                              corpus_cap=1, leg_trace=trace)
        assert rows == [], f"a window is never ranked; got {rows}"
        entry = trace[-1]
        assert entry[LEXICAL_DECLINED_KEY] is True, trace
        assert entry["degraded"] is False, (
            "the decline must not claim the memory layer is impaired: "
            f"{entry}")
        assert entry["ran"] is True, (
            f"the leg DID read the corpus; it declined to RANK it: {entry}")
        assert entry["count"] == 0, trace

    def test_a_declined_arm_claims_no_bm25_mechanism(
            self, force_sparse_tfidf):
        """``mechanism`` answers "which path produced these rows" and BM25
        produced none — so a #8486 baseline counting ``mechanism == "bm25"``
        cannot count a decline as a BM25-scored read.

        The distinguishing signal is ``declined``, which is the truthful
        carrier: the arm ran, read a window, and returned nothing.
        """
        graph, _ids = _graph_with_corpus()
        trace: list[dict] = []
        run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                       corpus_cap=1, leg_trace=trace)
        entry = trace[-1]
        assert entry.get("mechanism") is None, (
            "a declined arm produced no BM25 rows and must not claim the "
            f"mechanism that produced rows; {entry}")
        assert entry.get(LEXICAL_DECLINED_KEY) is True, (
            "the decline must stay VISIBLE once it stops claiming degraded: "
            f"{entry}")

    def test_the_shared_trace_fold_does_not_read_the_decline_as_a_degradation(
            self, force_sparse_tfidf):
        """The RECORDING half: the folds both battery lanes share see a run.

        ``retrieval_preflight.merge_leg_trace`` is the single interpreter of
        this trace shape — ``arms/a4_tortoise`` and ``parity/mabench_tortoise``
        both record their ``retrieval_degraded`` through it — and it reads ONLY
        ``degraded``. So the decline must keep that flag false, or the two
        battery lanes relabel a complete measurement as impaired: the same
        false positive this class removes from the read's status, one layer
        out. What a measurement needs instead is the ``declined`` key on the
        entry; counting it is the baseline's job (see
        :data:`LEXICAL_DECLINED_KEY`), and it must never be routed back through
        ``degraded`` to get there.
        """
        from battery.runner.retrieval_preflight import merge_leg_trace

        graph, _ids = _graph_with_corpus()
        trace: list[dict] = []
        run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                       corpus_cap=1, leg_trace=trace)
        assert trace[-1].get(LEXICAL_DECLINED_KEY) is True, (
            f"guard: the arm must have declined; {trace}")
        legs: list[str] = []
        assert merge_leg_trace(legs, trace) is False, (
            "the shared fold the battery lanes record through must not report "
            f"an impaired measurement for a decline; trace={trace}")
        assert "fts" in legs, (
            f"the declined arm still RAN that leg slot; legs={legs}")

    def test_the_leg_health_predicate_does_not_read_the_decline_as_a_dead_leg(
            self, force_sparse_tfidf):
        """The eval-side fold: ``_leg_entry_dead`` / ``_legs_degraded``.

        ``tools/longmem_eval/run.py`` reads the SAME trace shape one layer out,
        and its predicate keys on ``reason``+``count`` rather than on
        ``degraded``: ``empty_results`` at ``count == 0`` is a DEAD leg. A
        declined arm entry is exactly that shape, so both
        ``_leg_entry_dead`` (which feeds ``_legs_degraded`` → the mid-run
        watchdog's leg-deadness arm) and the resume gate's inline ``dead_fts``
        filter read a decline as a dead retrieval leg.

        The rule the entry already follows is that a decline ASSERTS NOTHING:
        it writes no ``mechanism`` because it produced no rows. By the same
        rule it may not claim the leg was dead — the shipped leg's own entry is
        the one that speaks for the slot. A decline must therefore be
        indistinguishable from the arm never having run, to BOTH predicates.
        """
        from tools.longmem_eval.run import _leg_entry_dead, _legs_degraded

        graph, _ids = _graph_with_corpus()
        arm: list[dict] = []
        run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                       corpus_cap=1, leg_trace=arm)
        entry = arm[-1]
        assert entry.get(LEXICAL_DECLINED_KEY) is True, (
            f"guard: the arm must have declined; {arm}")
        assert entry["reason"] == "empty_results" and entry["count"] == 0, (
            "guard: the decline carries the DEAD-leg shape this predicate "
            f"keys on, or the test proves nothing; {entry}")
        assert _leg_entry_dead(entry) is False, (
            "a declined arm asserts nothing about the leg and must not be "
            f"classified a dead leg; {entry}")
        degraded = _legs_degraded({"legs": [entry]})
        assert degraded == [], (
            "the leg-health predicate feeds the watchdog's leg-deadness arm; "
            f"a decline must not degrade the leg; {degraded}")

    def test_enabling_the_arm_does_not_move_the_resume_gate(
            self, force_sparse_tfidf):
        """The differential: the lever must not change the resume VERDICT.

        Arm-off and arm-declined are the same retrieval — the arm declined to
        rank, so the shipped leg served the slot either way (or did not, on its
        own entry's terms). So an outcome carrying the decline entry must get
        the SAME ``resume_gate_reject_reason`` as the identical outcome without
        it. Anything else is an instrument artifact: a lever that provably did
        not change retrieval flipping a gate's verdict, which on a live run
        re-encodes a good outcome on every resume (the ``index_missing``
        livelock) and, sustained, aborts the run with ``leg_dead``.

        The shipped entry is constructed, not produced: which benign non-live
        reason the embedded lane records (``index_missing`` when the build has
        no FTS index — the shape #1764's note names) is a property of the
        FalkorDBLite build under test, and the assertion here is the
        DIFFERENTIAL, which is indifferent to which benign reason it is.
        """
        from tools.longmem_eval.run import resume_gate_reject_reason

        graph, _ids = _graph_with_corpus()
        arm: list[dict] = []
        run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                       corpus_cap=1, leg_trace=arm)
        declined = arm[-1]
        assert declined.get(LEXICAL_DECLINED_KEY) is True, (
            f"guard: the arm must have declined; {arm}")

        shipped = {"leg": "fts", "ran": True, "degraded": True,
                   "reason": "index_missing", "count": 0}
        # BOTH session states, because the gate has an INDEPENDENT session-zero
        # trigger: on an all-zero recall the outcome is refused either way, so
        # the invariant is the VERDICT being identical — not the verdict being
        # None. Asserting only the surfaced case would let a wrong-but-equal
        # pair pass, and asserting None would re-specify the #1764 gate here
        # instead of the lever's own neutrality.
        for recall in ({"any@5": 0.5}, {"any@5": 0.0}):
            base = {"session_recall@k": dict(recall),
                    "turn_recall@k": {"any@5": 0.5}}
            without_arm = dict(base, legs=[dict(shipped)])
            with_arm = dict(base, legs=[dict(shipped), dict(declined)])
            off = resume_gate_reject_reason(without_arm)
            on = resume_gate_reject_reason(with_arm)
            assert on == off, (
                "enabling the lexical arm must not change the resume verdict "
                "— the decline asserts nothing about the leg. recall="
                f"{recall} arm-on {on!r} vs arm-off {off!r}")
        assert resume_gate_reject_reason(dict(
            {"session_recall@k": {"any@5": 0.5},
             "turn_recall@k": {"any@5": 0.5}},
            legs=[dict(shipped), dict(declined)])) is None, (
            "guard: a surfaced session with a benign shipped entry and a "
            "declined arm is a resumable outcome")

    def test_a_clean_arm_loss_is_not_a_decline(
            self, force_sparse_tfidf):
        """Control: the non-declining empty arm is untouched by this change.

        An arm that ran and matched nothing records ``degraded=False`` WITHOUT
        ``declined`` — so the new key cannot be satisfied by every empty arm,
        and the two states stay distinguishable.
        """
        graph, _ids = _graph_with_corpus()
        trace: list[dict] = []
        rows = run_bm25_query(graph, "the", entity_type="point", limit=20,
                              leg_trace=trace)
        assert rows == [], f"a stopword-only query matches nothing; got {rows}"
        entry = trace[-1]
        assert entry["degraded"] is False, entry
        assert entry.get(LEXICAL_DECLINED_KEY) is None, (
            f"a clean empty arm did not DECLINE anything; {entry}")
        assert entry.get("mechanism") == LEXICAL_MECHANISM_BM25, (
            f"the arm ran its scorer (and found nothing): {entry}")
        # And the exemption must not have swallowed this shape: an arm that ran
        # and matched nothing is still a DEAD lexical leg to the eval folds, or
        # the decline exemption would be "every empty fts entry is healthy" —
        # which is the #1764 gate itself, deleted.
        from tools.longmem_eval.run import _leg_entry_dead

        assert _leg_entry_dead(entry) is True, (
            "a non-declining empty arm is a genuine dead-leg signal and must "
            f"stay classified as one; {entry}")


class TestCorpusCap:
    """The cap is LOUD and DECLINED: a window never replaces a whole-label read.

    The leg asks for one row past the cap, so "exactly cap" (the whole corpus
    fits) is distinguishable from "more than cap" (a window). On a WINDOW it
    returns nothing and records ``declined`` (NOT ``degraded`` — see
    ``TestDeclineIsNotAReadDegradation``), leaving the shipped
    graph-side leg's rows in place — the shipped leg answered over the WHOLE
    label, so swapping in a ranking over the oldest N ids would make the read
    lexically weaker than the leg it replaced, silently.

    Mutations: (a) drop the ``+ 1`` in the ``LIMIT`` → a corpus exactly at the
    cap is misread as truncated and reds the first test; (b) return ``ranked``
    on truncation instead of ``[]`` → the tiny-cap tests red.
    """

    def test_a_corpus_that_exactly_fits_the_cap_is_not_truncated(
            self, force_sparse_tfidf):
        """``cap == corpus size`` is a COMPLETE ranking, not a window.

        The one-extra-row probe is what buys this: without it the leg cannot
        tell the two apart and a store whose size happens to equal the cap
        would lose its lexical leg for nothing.
        """
        graph, ids = _graph_with_corpus()
        corpus = len(TOPIC_DOCS) + 1  # the seven fixture points
        trace: list[dict] = []
        rows = run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                              corpus_cap=corpus, leg_trace=trace)
        assert trace and trace[-1]["degraded"] is False, trace
        assert trace[-1]["reason"] == "ok", trace
        assert rows and rows[0][0] == ids["target"], (
            f"a complete corpus must still rank; got {rows}")

    def test_under_the_cap_the_leg_is_not_degraded(self, force_sparse_tfidf):
        graph, _ids = _graph_with_corpus()
        trace: list[dict] = []
        run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                       corpus_cap=1000, leg_trace=trace)
        assert trace and trace[-1]["degraded"] is False, trace

    def test_over_the_cap_the_leg_declines_instead_of_window_ranking(
            self, force_sparse_tfidf):
        """Over the cap: NO rows, ``declined=True`` — never a windowed ranking.

        ``count=0`` and an empty return are the contract the sdk relies on to
        keep the shipped leg's whole-label rows (``if _bm25_hits``). The
        ``declined`` key is what separates this from a clean empty result, so a
        measurement cannot read a declined window as "the arm ran and found
        nothing" — and it is NOT ``degraded``, which is aggregated into the
        READ's recorded status (see ``TestDeclineIsNotAReadDegradation``).
        """
        graph, _ids = _graph_with_corpus()
        trace: list[dict] = []
        rows = run_bm25_query(graph, QUERY, entity_type="point", limit=20,
                              corpus_cap=1, leg_trace=trace)
        assert rows == [], (
            "a corpus wider than the cap must not be ranked over a window; "
            f"got {rows}")
        assert trace and trace[-1][LEXICAL_DECLINED_KEY] is True, trace
        assert trace[-1]["degraded"] is False, trace
        assert trace[-1]["count"] == 0, trace
        assert trace[-1]["reason"] == "empty_results", trace
        assert trace[-1].get("mechanism") is None, (
            "the leg ran but produced NO BM25 rows, so it may not claim the "
            f"mechanism that produced them; trace={trace}")


class TestServableFamilies:
    """Which families the leg serves is ONE decision, and it is the early return.

    ``run_bm25_query`` scores ``index_text(coalesce(content, _searchText),
    search_keys)``. That is a declared FTS field on ``Point`` (content +
    search_keys) and on ``:Source``/``:Document`` (_searchText) — and it is on
    NEITHER ``:Event`` (declared FTS fields: subject, name) nor ``:Subject``
    (name). The canonical writers put neither ``content`` nor ``_searchText``
    on those labels, so every row would tokenise to ``[]``, the leg would exit
    ``empty_results`` (the R3 D4 term for "ran and found nothing"), and the read
    would have paid a full-label scan — up to ``DEFAULT_BM25_CORPUS_CAP`` rows —
    for a guaranteed nothing.
    """

    def test_refused_families_are_not_scanned(self, force_sparse_tfidf):
        """A refused family must not reach the graph AT ALL — and must not
        claim the ``mechanism`` key.

        Pre-fix, ``event``/``subject`` were in ``BM25_ENTITY_TYPES``: the leg
        issued ``MATCH (n:Event) …`` (a full-label scan) and reported
        ``ran=True, empty_results`` — a trace that claims the leg looked and
        found nothing when it could never match. Mutation (pre-fix state): add
        ``event`` (or ``subject``) back to ``BM25_ENTITY_TYPES`` → the query
        counter below is non-empty and this reds.

        The ``ran is False`` half is the #8486 measurement contract: a
        consumer counting ``mechanism == "bm25"`` must not count a refusal as
        a run (``_record``'s own docstring). Mutation: record the refusal with
        ``ran=True`` → the ``mechanism`` key appears and this reds.
        """
        graph, _ids = _graph_with_corpus()

        class _Counting:
            def __init__(self, inner):
                self.inner = inner
                self.queries: list[str] = []

            def query(self, cypher, params=None, timeout=None):
                self.queries.append(cypher)
                return self.inner.query(cypher, params=params, timeout=timeout)

        for family in ("event", "subject", "operator", "object"):
            spy = _Counting(graph)
            trace: list[dict] = []
            rows = run_bm25_query(spy, QUERY, entity_type=family,
                                  limit=20, leg_trace=trace)
            assert rows == [], (family, rows)
            assert spy.queries == [], (
                f"the {family!r} family is not servable by this leg, so it "
                "must be refused BEFORE the corpus scan — the leg cannot "
                f"score it; scanned: {spy.queries}")
            assert trace and trace[-1]["reason"] == "empty_results", trace
            assert trace[-1]["ran"] is False, (
                f"nothing was read for {family!r}, so the leg did not run; "
                f"trace={trace}")
            assert "mechanism" not in trace[-1], (
                f"a REFUSED family must not claim the BM25 mechanism — a "
                f"consumer counting mechanism=='bm25' would count a read that "
                f"never happened; trace={trace}")

    def test_the_served_families_are_the_ones_with_a_declared_fts_field(self):
        """The tuple and the text projection must agree — no third option.

        Pins the SET (so re-widening it is a deliberate, reviewed edit) and
        that each member is a family whose text this leg actually reads.
        """
        from tortoise.search_engine import BM25_ENTITY_TYPES

        assert BM25_ENTITY_TYPES == ("point", "document", "source"), (
            f"served families changed: {BM25_ENTITY_TYPES} — a family whose "
            "text lives on fields this leg does not read (event/subject) must "
            "bring a per-family text projection AND its parity test with it")


def _e2e_sdk() -> tuple[TortoiseSDK, str]:
    """A gated-off SDK holding the end-to-end fixture corpus + the target id."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="bm25_8488_e2e_"), "t.db")
    sdk = TortoiseSDK(db_path, namespace=f"bm25_e2e_{os.urandom(6).hex()}")
    _SDKS.append(sdk)
    target = sdk.create_point("statement", TARGET_DOC)["id"]
    for content in TOPIC_DOCS_END_TO_END:
        sdk.create_point("statement", content)
    return sdk, target


class TestMutationPointers:
    """A documented mutation must name a test that EXISTS.

    ``tortoise/bm25.py``'s ``LEXICAL_WEIGHT_MAX`` block is the only durable
    link between the bound and the tests that prove it, and it used to name
    ``test_lexical_weight_is_bounded_below_the_dense_leg`` — a test that has
    never existed (the real ones are ``TestLexicalWeightBound.``
    ``test_weight_is_clamped_below_the_dense_leg`` and ``TestReadPath.``
    ``test_lexical_weight_is_bounded_at_the_read_path``). A fixer retracing the
    mutation to check it still bites then finds nothing, and a pointer that
    points nowhere is worse than none: it reads as coverage.

    Mutation (pre-fix): the docstring's stale name → the referenced attribute
    does not resolve and this reds.
    """

    def test_the_bound_points_at_real_tests(self):
        import re
        from pathlib import Path

        import tortoise.bm25 as bm25_module

        source = Path(bm25_module.__file__).read_text()

        # Two shapes are accepted, because both have been used here: a
        # qualified ``TestClass.test_method`` and a BARE ``test_method`` (the
        # stale pointer this test was written for was bare). A bare name
        # resolves if ANY Test class in this module defines it. ``.py`` is
        # excluded so the file name (``tests/test_bm25_lexical_8488.py``) is
        # not read as a test pointer.
        qualified = set(re.findall(
            r"\b(Test[A-Za-z0-9_]+)\.(test_[A-Za-z0-9_]+)", source))
        bare = set(re.findall(
            r"(?<![A-Za-z0-9_.])(test_[A-Za-z0-9_]+)(?![\w.])", source))
        bare -= {name for _cls, name in qualified}

        # Non-vacuity: the pointer text must still BE there, or "every
        # pointer resolves" is trivially true of an empty set.
        assert qualified or bare, (
            "the LEXICAL_WEIGHT_MAX block must name the tests its mutation "
            "reds (a claim with no pointer is unverifiable)")

        this_module = sys.modules[__name__]
        test_classes = [obj for obj in vars(this_module).values()
                        if isinstance(obj, type)]
        missing = []
        for class_name, test_name in sorted(qualified):
            cls = getattr(this_module, class_name, None)
            if cls is None or not hasattr(cls, test_name):
                missing.append(f"{class_name}.{test_name}")
        for test_name in sorted(bare):
            if not any(hasattr(cls, test_name) for cls in test_classes):
                missing.append(test_name)
        assert not missing, (
            "tortoise/bm25.py points a mutation at test(s) that do not "
            f"exist in this file: {missing} — a fixer retracing the mutation "
            "finds nothing and reads it as coverage")


class TestOperatorContract:
    """The arm's only operator lever must be discoverable where operators look."""

    def test_env_example_declares_the_bm25_arm_switch(self):
        """``.env.example`` IS this repo's env contract (``AGENTS.md``).

        #8487 pinned the same thing for its kill switch
        (``test_time_aware_2520.py::test_env_example_declares_the_valid_time_kill_switch``),
        and the comparable read-path knobs (``TORTOISE_POOL_FLOOR``,
        ``TORTOISE_FUSION_WEIGHTS``) are declared there. The arm ships OFF
        *so that it can be measured before adoption* (#8486) — the switch is
        therefore the deliverable's only entry point, and pre-fix it was
        reachable only by reading source.
        """
        from pathlib import Path

        from tortoise.bm25 import BM25_ENV

        root = Path(__file__).resolve().parent.parent
        env = (root / ".env.example").read_text()
        assert BM25_ENV in env, (
            f"{BM25_ENV} (the #8488 lexical-arm gate) is an operator-visible "
            "lever that is not declared in .env.example")
        declared = [
            line for line in env.splitlines()
            if line.strip().startswith(f"# {BM25_ENV}=")
            or line.strip().startswith(f"{BM25_ENV}=")]
        assert declared, (
            f"{BM25_ENV} must be shown as a commented assignment (the example "
            "form an operator copies), not just named in prose")

    def test_env_example_declares_the_DECLINE_contract_truthfully(self):
        """The operator contract for the cap must not teach the OLD decline.

        `.env.example` described the corpus-cap decline as the leg "reporting
        itself ``degraded`` in the leg trace". That is false and it is exactly
        the misreading #8488's follow-up removed: `degraded` is aggregated into
        the READ's recorded status (`read_status.classify_leg_trace`) and into
        `ask_lane`'s `retrieval_degraded`, so an operator on a store over the
        cap was told to expect a complete read to be flagged as impaired memory.
        Post-fix the entry is `declined=True` with `degraded=False` and no
        `mechanism`, so the contract has to say so.

        The sibling test above only asserts the switch is PRESENT, which is why
        the drift went uncaught; this one pins the semantics of the decline.

        It pins the two positive facts by their CANONICAL trace spellings —
        ``declined=True`` and ``degraded=False`` are code identifiers in the leg
        trace, so the contract names them literally: an operator reading
        `.env.example` meets the same spellings the trace emits, and a reword
        ("``degraded`` remains ``False``") is a deliberate edit of the contract
        that this test asks for rather than an innocent change it forbids. The
        two NEGATIVE assertions are the other direction — they refuse either
        spelling of the OLD contract (a `degraded=True` decline, or the
        "reports itself degraded" sentence), so a text that keeps the old
        meaning while avoiding one literal still reds on the other.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        block = _bm25_env_block((root / ".env.example").read_text())
        assert block, "guard: the #8488 block must be findable in .env.example"
        assert "declined=True" in block, (
            ".env.example must name the decline's own trace key (`declined=True`); "
            "without it the only operator-visible account of the cap is the "
            "misleading one: " + block)
        assert "degraded=False" in block, (
            ".env.example must say the decline leaves `degraded` FALSE — the "
            "flag an operator reads as an impaired read: " + block)
        # The two ways the OLD contract can reappear. `degraded=True` covers a
        # restated decline flag; the sentence covers prose that keeps the old
        # meaning while avoiding the literal.
        assert "degraded=True" not in block, (
            ".env.example reintroduces the old decline contract (`degraded=True` "
            "on an arm that produced no rows): " + block)
        assert "reports itself" not in block, (
            ".env.example still says the leg 'reports itself degraded', the "
            "sentence this change exists to remove: " + block)


def _bm25_env_block(env: str) -> str:
    """The `#8488` section of `.env.example` (its `# ── …` header to the next)."""
    lines = env.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if "#8488 BM25 lexical arm" in line), None)
    if start is None:
        return ""
    end = next((j for j in range(start + 1, len(lines))
                if lines[j].startswith("# ──")), len(lines))
    return "\n".join(lines[start:end])

