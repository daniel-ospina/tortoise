"""#12297 — the FUSED-CANDIDATE-LIST rerank: permutation, gate, pool width, degrade.

The first five sections are hermetic: the cross-encoder is never loaded (the
deterministic in-repo ``rerank.FakeScorer`` is injected through the shared
``rerank.load_scorer`` policy seam — no model, no network, no GPU). Section 6
runs the WIRING against a real (embedded) store, because the property it pins is
the composed read's, not an isolated function's.

The contract this file exists to pin is ONE sentence: **a reranker reorders, it
never filters.** Everything below is a consequence or a failure mode of that:

  1. default-off is byte-identical AND loads no scorer;
  2. flag-on is a PERMUTATION — same rows, same length, different order;
  3. ``pool`` bounds what is SCORED, and the unscored tail keeps its order;
  4. every failure (no scorer, score exception, length mismatch, empty query)
     degrades to the untouched order and never raises;
  5. the pool's TEXT read names the right property per graph label, and restates
     exactly the two predicates the real content fetch applies — so the rerank
     cannot score a row set the caller never sees;
  6. the wiring in ``tortoise_fts_query`` sits BEFORE ``result_ids[:limit]``
     (the only placement where a rerank can change what survives a cut) and
     BEFORE step 5e's valid-time demotion (relevance may not outrank #8487's
     correctness guarantee); it is a permutation of the read's id set, it takes
     its width from the SAME resolver the scorer does, and it DISCLOSES the cap
     it applied on the leg trace — so the bound is never silent.
"""
from __future__ import annotations

import ast
import inspect
import sys
import uuid
from pathlib import Path
from typing import NamedTuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import rerank  # noqa: E402, RUF100
from tortoise import sdk as sdk_mod
from tortoise.search_engine import declared_degraded_read

QUERY = "when is the gym session"

#: Deliberately WRONG retrieval order: the best match is last. A working
#: reranker promotes it; the ids and the length must not change.
ROWS: list[dict] = [
    {"id": "x0", "content": "weather forecast rain all week"},
    {"id": "x1", "content": "a gym membership card"},
    {"id": "x2", "content": "gym gym gym"},
]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv(rerank.FUSED_RERANK_ENV, raising=False)
    monkeypatch.delenv(rerank.FUSED_RERANK_POOL_ENV, raising=False)
    monkeypatch.delenv(rerank.FUSED_RERANK_MODEL_ENV, raising=False)
    rerank._scorer_cache.clear()
    rerank._fail_cache.clear()
    yield
    rerank._scorer_cache.clear()
    rerank._fail_cache.clear()


def _inject(scorer, monkeypatch):
    """Patch the ONE shared load policy, so the rerank sees a fake scorer.

    ⛔ THROUGH ``monkeypatch``, never a bare assignment: a plain module attribute
    write is undone only by this module's own teardown helper, so a test that
    forgets it leaks the fake scorer into every later test in the SESSION —
    measured: two ``tests/test_product_rerank.py`` tests red once a wiring test
    that omitted the helper ran first. ``monkeypatch`` restores per test.
    """
    monkeypatch.setattr(rerank, "load_scorer", _make_loader(scorer))


def _make_loader(scorer):
    def _load(name, **kw):
        if isinstance(scorer, Exception):
            return None, f"{name}: {scorer!r}"
        return scorer, ""
    return _load


class _RaisingScorer:
    def score(self, query, contents):
        raise RuntimeError("boom")


class _ShortScorer:
    def score(self, query, contents):
        return [1.0]     # wrong length on purpose


class _NoneScorer:
    """A scorer that returns a placeholder instead of a float."""

    def score(self, query, contents):
        return [None] * len(contents)


class _NanScorer:
    def score(self, query, contents):
        return [float("nan")] * len(contents)


# ── 1. default-off is byte-identical and loads no scorer ───────────────────

def test_gate_fails_safe_off(monkeypatch):
    assert rerank.rerank_pool_enabled(True) is True
    assert rerank.rerank_pool_enabled(False) is False
    assert rerank.rerank_pool_enabled(None) is False        # env unset → OFF
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        with pytest.MonkeyPatch.context() as m:
            m.setenv(rerank.FUSED_RERANK_ENV, truthy)
            assert rerank.rerank_pool_enabled(None) is True
    for falsy in ("", "0", "no", "garbage", "2"):
        with pytest.MonkeyPatch.context() as m:
            m.setenv(rerank.FUSED_RERANK_ENV, falsy)
            assert rerank.rerank_pool_enabled(None) is False


def test_off_path_returns_input_order_and_never_loads_a_scorer(monkeypatch):
    def _must_not_be_called(name, **kw):
        raise AssertionError("the OFF path must not load a cross-encoder")
    monkeypatch.setattr(rerank, "load_scorer", _must_not_be_called)

    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats == {"applied": False, "degrade_reason": "disabled"}
    # a NEW list — a caller mutating the result must not reorder its own input
    assert out is not ROWS


# ── 2. flag-on is a PERMUTATION ────────────────────────────────────────────

def test_on_path_reorders_but_is_a_permutation(monkeypatch):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer(), monkeypatch)

    out, stats = rerank.rerank_pool(QUERY, ROWS)
    ids = [r["id"] for r in out]
    assert stats["applied"] is True
    # the best match ("gym gym gym") is promoted out of last place
    assert ids[0] == "x2"
    # PERMUTATION: same rows, same length, nothing dropped, nothing invented
    assert len(out) == len(ROWS)
    assert sorted(ids) == sorted(r["id"] for r in ROWS)
    assert stats["scored"] == 3 and stats["unscored"] == 0


def test_ties_break_on_the_original_index(monkeypatch):
    """A scorer that returns one constant must leave the order UNCHANGED.

    `sorted` is stable, so a flat score is a no-op — that is what makes
    `moved == 0` readable as "the reranker had nothing to say". ⛔ The SLOT
    tie-break is a different property and this test does not reach it: through
    ``rerank_pool`` the scorable slots are ascending, so the second sort key can
    never change the order (dropping it left every test green — measured). It is
    pinned directly in
    ``test_the_tie_break_is_the_original_SLOT_not_the_scorer_position``.
    """
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")

    class _Flat:
        def score(self, query, contents):
            return [0.5] * len(contents)

    _inject(_Flat(), monkeypatch)
    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats["applied"] is True and stats["moved"] == 0


def test_a_pool_whose_scored_window_carries_NO_TEXT_degrades_instead_of_raising(
        monkeypatch):
    """Every candidate in the window is textless: no scorer call, a NAMED reason.

    ⛔ This arm is load-bearing OUTSIDE the SDK: the calibration and control
    tools call ``rerank_pool(..., enabled=True, scorer=...)`` directly, with no
    ``_scored_rows``-style pre-filter, and a mention whose rows carry no text is
    exactly this shape. Deleting the arm makes the function RAISE
    (``min() iterable argument is empty``) in the middle of a measurement run,
    contradicting the docstring's "never raises" — and nothing else reds:
    measured, the whole file stayed green with the arm removed.
    """
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    calls: list[list[str]] = []

    class _Recording:
        def score(self, query, texts):
            calls.append(list(texts))
            return [0.0 for _ in texts]

    blank = [{"id": "a", "content": ""}, {"id": "b", "content": "   "}]
    out, stats = rerank.rerank_pool(
        QUERY, blank, enabled=True, scorer=_Recording())
    assert out == blank, "a degraded arm must return the input order untouched"
    assert stats["applied"] is False
    assert stats["degrade_reason"] == "no_scorable_text", stats
    assert calls == [], "the scorer must not be called when nothing is scorable"


def test_the_tie_break_is_the_original_SLOT_not_the_scorer_position():
    """Equal scores fall back to the SLOT index, not to the position in the
    scorer's own input.

    ⛔ Reachable only with a slot list that is not ascending, which
    ``rerank_pool`` never produces (its ``scorable`` is
    ``range(n_scored)`` filtered) — so this is the ONLY test that can
    distinguish the two orderings, and it is why the second sort key exists:
    ``rerank_pool``'s page correctness must not depend on the caller handing the
    slots over in ascending order.
    """
    head = [{"id": f"x{i}"} for i in range(3)]
    out, moved, top = rerank._reorder_scorable_slots(
        head, [], 3, [2, 0, 1], [0.5, 0.5, 0.5])
    # keyed on (-value, slot): slot 0 (which holds x1) first …
    assert [r["id"] for r in out] == ["x1", "x2", "x0"], out
    assert moved == 3 and top == 0.5


# ── 3. pool bounds what is SCORED; the tail keeps its order ────────────────

def test_pool_width_bounds_scoring_and_preserves_the_tail(
        monkeypatch):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer(), monkeypatch)

    out, stats = rerank.rerank_pool(QUERY, ROWS, pool=2)
    assert stats["scored"] == 2 and stats["unscored"] == 1 and stats["pool"] == 2
    # Only the head [x0, x1] was scored and it reorders (x1 has query overlap,
    # x0 has none); x2 — the true best — is past the width and therefore stays
    # exactly where retrieval put it, LAST. That is what "pool width bounds the
    # rerank" means: a narrow pool cannot promote what it never scored.
    assert [r["id"] for r in out] == ["x1", "x0", "x2"]
    assert len(out) == len(ROWS)


def test_the_pool_key_is_the_CONFIGURED_width_even_on_a_narrow_read(
        sdk, monkeypatch):
    """A 6-candidate read at the shipped width 40 reports ``pool=40``, not 6.

    ⛔ ``RERANK_POOL_KEY``'s docstring forbids exactly one shape —
    ``min(len(candidates), width)`` — and nothing pinned it: rewriting the write
    site to that expression left all 60 tests green (measured), reporting
    ``pool=6`` here. The key is the CAP THE KNOB RESOLVED; the applied bound is
    the min, and ``scored + unscored`` already carries the candidate count.
    """
    _seed(sdk, 6)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer(), monkeypatch)
    trace: list[dict] = []
    on = sdk._tortoise_fts_query("zebra migration", entity_type="point",
                                limit=6, leg_trace=trace)
    candidates = len(on)
    assert candidates < rerank.DEFAULT_FUSED_RERANK_POOL, (
        f"premise: the read must be NARROWER than the shipped width "
        f"({rerank.DEFAULT_FUSED_RERANK_POOL}), got {candidates}")
    entry = _rerank_entries(trace)[0]
    assert entry[rerank.RERANK_POOL_KEY] == rerank.DEFAULT_FUSED_RERANK_POOL, entry
    assert entry[rerank.RERANK_SCORED_KEY] == candidates, entry


def test_the_pool_key_on_the_SKIP_arms_is_the_resolver_and_not_the_constant(
        sdk, monkeypatch):
    """Every rerank ENTRY reports the resolved width — including the ones that
    skipped.

    ⛔ Three documents say so (``RERANK_POOL_KEY``'s docstring, the brief's §5,
    ``.env.example``), and every arm could be replaced by a literal with the file
    green (measured: ``fused_rerank_pool_width()`` → ``40`` at the three skip
    sites left 62 passing). The applied arm has its own pin; these are the other
    THREE shapes — the caller's opt-out, a non-relevance order, and a recency
    order whose reorder could not be applied.

    ⛔ AND THE COUNTERS, not just the reason: all three arms hardcode
    ``scored=0`` / ``unscored=len(result_ids)``, and blanking ``unscored`` to 0
    left every test green (measured) — a skipped read then reported ``scored=0,
    unscored=0``, i.e. "the reranker saw everything and left nothing unseen" on a
    read where it never ran, which is the false all-clear the applied arm's own
    comment forbids.
    """
    _seed(sdk, 4)
    candidates = _candidate_count(sdk, monkeypatch, "zebra migration", 4)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, "7")
    _inject(rerank.FakeScorer(), monkeypatch)

    def _check_skip(entry, reason):
        assert entry["ran"] is False, entry
        assert entry["reason"] == reason, entry
        assert entry[rerank.RERANK_POOL_KEY] == 7, entry
        assert entry[rerank.RERANK_SCORED_KEY] == 0, entry
        assert entry[rerank.RERANK_UNSCORED_KEY] == candidates, entry

    opt_out: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=4,
                           _rerank_candidates=False, leg_trace=opt_out)
    _check_skip(_rerank_entries(opt_out)[0], "order_owned_downstream")

    ordered: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=4,
                           order_by="confidence", leg_trace=ordered)
    _check_skip(_rerank_entries(ordered)[0], "order_by_not_relevance")

    recency: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=4,
                           recency_field="createdAt", recency_boost=10.0,
                           leg_trace=recency)
    _check_skip(_rerank_entries(recency)[0], "recency_order_applied")


def test_the_SCORER_is_handed_the_knob_width_and_not_the_constant(
        sdk, monkeypatch):
    """A knob ABOVE the default must actually raise what gets SCORED.

    ⛔ The only shape that can see it: candidates > DEFAULT. Below (or at) the
    constant the text read hands the scorer at most DEFAULT ids, every row past
    it carries ``""`` and is stripped as unscorable — so a constant passed as
    ``pool=`` is indistinguishable from the knob, and reverting
    ``pool=_pool_n`` to the constant left the whole file green (measured with
    five candidates and a knob of four).
    """
    knob = rerank.DEFAULT_FUSED_RERANK_POOL + 5
    _seed(sdk, knob)
    off = sdk._tortoise_fts_query("zebra migration", entity_type="point",
                                 limit=knob)
    candidates = len(off)
    assert candidates > rerank.DEFAULT_FUSED_RERANK_POOL, (
        f"premise: the read must fuse MORE rows than the default width "
        f"({rerank.DEFAULT_FUSED_RERANK_POOL}), got {candidates}")
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, str(knob))
    _inject(rerank.FakeScorer(), monkeypatch)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point",
                           limit=knob, leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    assert entry[rerank.RERANK_POOL_KEY] == knob, entry
    # …and the SCORER saw that many, not the constant: a trace that reports the
    # knob while scoring fewer is the silent cap wearing the disclosure.
    assert entry[rerank.RERANK_SCORED_KEY] == candidates, entry


def test_pool_env_knob_and_explicit_kwarg_precedence(monkeypatch):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, "1")
    _inject(rerank.FakeScorer(), monkeypatch)
    _out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert stats["pool"] == 1 and stats["scored"] == 1
    # an explicit kwarg wins over the env (the `_clamp_int` contract)
    _out, stats = rerank.rerank_pool(QUERY, ROWS, pool=3)
    assert stats["pool"] == 3 and stats["scored"] == 3


def test_pool_env_garbage_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, "not-a-number")
    _inject(rerank.FakeScorer(), monkeypatch)
    _out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert stats["pool"] == rerank.DEFAULT_FUSED_RERANK_POOL


# ── 4. every failure degrades, none raises ─────────────────────────────────

@pytest.mark.parametrize("scorer,needle", [
    (RuntimeError("model unavailable"), "model unavailable"),
    (_RaisingScorer(), "boom"),
    (_ShortScorer(), "length-mismatched"),
    # ⛔ The contract is "never raises", so a non-numeric or non-finite score
    # degrades like every other failure. Before this, ``float(None)`` inside
    # ``sorted`` raised ``TypeError`` out of the search path, and a ``nan``
    # reached ``top_score`` — where a caller serializing the trace gets NaN,
    # which is not JSON.
    (_NoneScorer(), "non-numeric"),
    (_NanScorer(), "non-finite"),
])
def test_scorer_failures_degrade_to_the_untouched_order(
        monkeypatch, scorer, needle):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(scorer, monkeypatch)
    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats["applied"] is False
    assert needle in stats["degrade_reason"]


@pytest.mark.parametrize("query,rows,reason", [
    ("   ", ROWS, "empty_query"),
    (QUERY, [], "empty_pool"),
])
def test_empty_inputs_degrade(monkeypatch, query, rows, reason):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer(), monkeypatch)
    out, stats = rerank.rerank_pool(query, rows)
    assert list(out) == list(rows)
    assert stats == {"applied": False, "degrade_reason": reason}


def test_none_content_truncates_to_empty_and_does_not_degrade(
        monkeypatch):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer(), monkeypatch)
    rows = [{"id": "a", "content": None}, {"id": "b", "content": "gym gym"}]
    out, stats = rerank.rerank_pool(QUERY, rows)
    assert stats["applied"] is True
    # ⛔ ``a`` was NOT scored, so it keeps the SLOT the caller's order gave it.
    # The earlier expectation was ``["b", "a"]`` — the unscored row demoted below
    # a scored one — which at a fixed ``limit`` pushes it off the page while the
    # trace reports ``moved: 0``. Not scoring a row must not move it.
    assert [r["id"] for r in out] == ["a", "b"], out
    assert stats["scored"] == 1 and stats["unscored"] == 1, stats


# ── 5. the pool TEXT read: right property per label, same predicates ───────

@pytest.mark.parametrize("entity_type,label,expected", [
    ("point", "Point", "content"),
    ("event", "Event", "subject"),
    ("subject", "Subject", "name"),
    ("object", "Object", "name"),
    ("document", "Source", "title"),
    ("source", "Source", "title"),
    ("operator", "Point", "label"),      # a :Point whose text is NOT content
])
def test_text_field_per_graph_label(entity_type, label, expected):
    assert sdk_mod._rerank_text_field(entity_type, label) == expected


def test_unknown_label_has_no_text_field():
    """An unmapped label degrades rather than scoring a property that is not
    that node's text — a wrong field would yield a confident, meaningless order.

    ⛔ ``== {}`` ALONE IS NOT A CHECK ON THE EARLY RETURN: the fetch also returns
    ``{}`` from its own ``except Exception``, so a graph that makes it CRASH is
    indistinguishable from one that is never queried (measured). The graph here
    records calls, and the assertion is that it was never asked.
    """
    assert sdk_mod._rerank_text_field("point", "Session") is None

    class _Recording:
        def __init__(self):
            self.calls: list[str] = []

        def query(self, cypher, params=None):
            self.calls.append(cypher)
            return type("R", (), {"result_set": []})()

    graph = _Recording()
    assert sdk_mod._fetch_rerank_pool_texts(
        graph, "point", "Session", "id", ["a"]) == {}
    assert graph.calls == [], (
        "an unmapped label still queried the graph, so the empty result came "
        f"from a failure rather than from the field check: {graph.calls}")


class _FakeGraph:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))

        class _R:
            result_set = self.rows
        return _R()


@pytest.mark.parametrize("entity_type,label,id_field,fragments", [
    # ⛔ THE COALESCE IS PINNED HERE OR NOWHERE: reverting it to ``n.content``
    # leaves every other test in this file green, because no other test produces a
    # ``:Point`` whose ``content`` is null — and an operator is exactly that row
    # (``create_operator`` writes ``label``/``op_type``/``direction`` and no
    # ``content``), so the revert scores it on ``""``.
    ("point", "Point", "id", ["MATCH (n:Point)", "n.id IN $ids",
                             "coalesce(n.content, n.label, '')"]),
    ("event", "Event", "eventId", ["MATCH (n:Event)", "n.eventId IN $ids"]),
    ("object", "Object", "id", ["MATCH (n:Object)", "n.id IN $ids"]),
    # the two restated predicates, pinned so they cannot drift from step 7
    ("document", "Source", "url", ["MATCH (n:Source)", "n.url IN $ids",
                                   "n.documentKind IS NOT NULL"]),
    # ⛔ ``source`` is the OPPOSITE partition of the same label — document ⟺
    # ``documentKind IS NOT NULL``, source ⟺ ``documentKind IS NULL`` (#5026 /
    # D10) — so it must NOT carry the document predicate. Pinning it as "the same
    # predicate" (an earlier draft did) matches no row and makes the rerank
    # structurally inert on the ``source`` surface, which is a caller-facing MCP
    # entity_type; the exclusion is asserted in
    # test_the_SOURCE_branch_of_step_7_carries_no_documentKind_predicate.
    ("source", "Source", "url", ["MATCH (n:Source)", "n.url IN $ids",
                                 "NOT documentKind IS NOT NULL"]),
    ("operator", "Point", "id", ["MATCH (n:Point {is_operator: true})"]),
])
def test_pool_text_read_predicates(entity_type, label, id_field, fragments):
    g = _FakeGraph([["a", "some text"]])
    out = sdk_mod._fetch_rerank_pool_texts(
        g, entity_type, label, id_field, ["a", "b"])
    assert out == {"a": "some text"}
    cypher, params = g.calls[0]
    for frag in fragments:
        if frag.startswith("NOT "):
            # a NEGATIVE pin: the excluded predicate must really be absent
            assert frag[4:] not in cypher, cypher
        else:
            assert frag in cypher, cypher
    assert params == {"ids": ["a", "b"]}
    # ONE batch read, never N+1
    assert len(g.calls) == 1


def test_the_SOURCE_branch_of_step_7_carries_no_documentKind_predicate():
    """``source`` and ``document`` are opposite partitions; step 7's source branch
    reads ``:Source`` with no ``documentKind`` clause at all.

    The "restated predicates" test asserts a string is present in step 7's SOURCE
    TEXT, which the DOCUMENT branch satisfies — so it could not tell the two apart.
    This one reads the source branch itself.
    """
    step7 = inspect.getsource(sdk_mod.TortoiseSDK._tortoise_fts_query)
    branch = step7.split('elif entity_type == "source":', 1)[1]
    branch = branch.split("elif entity_type ==", 1)[0]
    assert "MATCH (n:Source) WHERE n.url IN $ids RETURN n.url" in branch, branch
    assert "documentKind" not in branch, (
        "step 7's source branch gained a documentKind predicate — document and "
        "source are opposite partitions (#5026), so a shared predicate makes one "
        "of them match nothing")


def test_the_two_restated_predicates_are_pinned_against_step_7():
    """The duplication's ONLY guard: the read's query must still say what the
    real content fetch says.

    §5 of the pool-read rewrite restates step 7's two predicates. Pinning them
    to literals in THIS file would only pin the test to itself — an edit to
    step 7 would leave the rerank scoring a row set the caller never sees, with
    nothing red. So the same predicate TEXT is asserted present in step 7's own
    source: change one side and the other reds.
    """
    step7 = inspect.getsource(sdk_mod.TortoiseSDK._tortoise_fts_query)
    for predicate in ("AND n.documentKind IS NOT NULL ",
                      "MATCH (n:Point {is_operator: true})"):
        assert predicate in step7, predicate
        # …and the pool read still emits it (the other half of the pair).
        assert predicate in inspect.getsource(
            sdk_mod._fetch_rerank_pool_texts), predicate


#: (entity_type, the RESOLVED label, step 7's own branch marker, the property
#: step 7 materialises as that row's ``content``). The map in
#: ``_RERANK_TEXT_FIELD_BY_LABEL`` restates this column, and a restated column
#: that drifts is invisible: the rerank would score a property the caller never
#: sees and return a confident, meaningless order.
STEP7_TEXT_BRANCHES = [
    ("point", "Point", 'if entity_type == "point":', "content"),
    ("event", "Event", 'elif entity_type == "event":', "subject"),
    ("subject", "Subject", 'elif entity_type == "subject":', "name"),
    ("document", "Source", 'elif entity_type == "document":', "title"),
    ("object", "Object", 'elif entity_type == "object":', "name"),
    ("operator", "Point", 'elif entity_type == "operator":', "label"),
    ("source", "Source", 'elif entity_type == "source":', "title"),
]


@pytest.mark.parametrize("entity_type,label,marker,field", STEP7_TEXT_BRANCHES)
def test_the_text_column_is_the_one_STEP_7_MATERIALISES(
        entity_type, label, marker, field):
    """The rerank's text map must name the SAME property step 7 puts in `content`.

    Pinning the map against a literal table in this file would only pin the test
    to itself, which is why the two predicates are cross-checked against the real
    fetch. The text column is the larger restatement of the same kind and was
    left pinned to a literal — so a change to a step-7 branch would silently
    leave the rerank scoring a different property. This reads step 7's OWN slice
    for the branch (marker → the next branch marker) and requires the property
    the map returns to appear in it.
    """
    assert sdk_mod._rerank_text_field(entity_type, label) == field, (
        "the map and this table disagree — update the table WITH the map")

    # Slice by LINE, not by substring: ``'if entity_type =='`` is CONTAINED in
    # ``'elif entity_type =='`` at offset 2, so a substring search for the next
    # branch finds the marker's own tail and slices an empty branch — which reads
    # as a violation on every arm.
    lines = inspect.getsource(sdk_mod.TortoiseSDK._tortoise_fts_query).splitlines()
    starts = [i for i, line in enumerate(lines) if 'entity_type == "' in line]

    def _slice(i: int) -> str:
        later = [j for j in starts if j > i]
        return "\n".join(lines[i:later[0]] if later else lines[i:])

    # The same marker also appears in the id/label SELECTION blocks, which carry
    # no fetch — so take the occurrence whose own slice actually queries the
    # graph. Without this the event arm slices an ``id_field = "eventId"`` block
    # and reads as a violation.
    branch = next((b for b in (_slice(i) for i in starts if marker in lines[i])
                   if "graph.query(" in b), "")
    assert f"n.{field}" in branch, (
        f'step 7\'s {entity_type} branch no longer materialises n.{field} — the '
        f'rerank would score a property the caller never sees')


def test_pool_text_read_is_best_effort():
    class _Broken:
        def query(self, *a, **k):
            raise RuntimeError("graph down")

    assert sdk_mod._fetch_rerank_pool_texts(
        _Broken(), "point", "Point", "id", ["a"]) == {}


def test_pool_text_read_refuses_an_empty_id_list():
    g = _FakeGraph([["a", "x"]])
    assert sdk_mod._fetch_rerank_pool_texts(g, "point", "Point", "id", []) == {}
    assert g.calls == []


# ── 6. the WIRING: placement, permutation, and the DISCLOSED cap ───────────

@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """A real store (embedded), so the read path's own composition is under test.

    The lane's session gate still has to be satisfied by the invoking run; this
    fixture then deletes ``TORTOISE_DB_URI`` so the module is backend-pinned
    instead of depending on which lane happened to run it (the #8447 pattern)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_POOL_FLOOR", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "rerank-pool.db"))
    from tortoise.sdk import TortoiseSDK

    s = TortoiseSDK()
    try:
        yield s
    finally:
        s.close()


def _seed(sdk, n):
    for i in range(n):
        sdk._create_point(kind="statement",
                         content=f"zebra migration claim number {i:02d}",
                         entity_type="point")


def _id(obj):
    return obj["id"] if isinstance(obj, dict) else obj.id


def _rerank_entries(trace):
    return [e for e in trace if e.get("leg") == "rerank"]


def test_a_point_pool_scores_an_operator_on_its_LABEL(sdk):
    """An operator carries NO ``content``, so the point read used to hand the
    scorer ``""`` for it — and a cross-encoder ranks an empty passage ABOVE an
    unrelated real one, so the operator was demoted below every filler.

    Behavioural against a real store, not against the query text: ``create_operator``
    writes ``label``/``op_type``/``direction`` only, so a revert to ``n.content``
    returns ``""`` here and THIS is what reds.
    """
    a = sdk._create_point(kind="statement", content="zebrafish husbandry protocol A")
    b = sdk._create_point(kind="statement", content="zebrafish husbandry protocol B")
    op = sdk._create_operator(
        op_type="IMPL", source_id=_id(a), target_ids=[_id(b)],
        label="zebrafish husbandry protocol OPERATOR")
    op_id = _id(op)

    graph = sdk._get_proj().g
    out = sdk_mod._fetch_rerank_pool_texts(graph, "point", "Point", "id", [op_id])
    assert out.get(op_id) == "zebrafish husbandry protocol OPERATOR", out


def test_a_row_the_read_did_NOT_RETURN_is_never_scored(sdk, monkeypatch):
    """The other half of the empty-row rule: an id the read did not return is not
    a row with empty text — it is no row at all.

    ``_rows`` was once built as ``_pool_texts.get(pid, "")`` over the pool IDS, so
    an excluded id reached the cross-encoder as ``""`` and outranked real passages.
    No fixture produces an excluded id, so the exclusion is injected here and what
    the SCORER receives is asserted directly.
    """
    _seed(sdk, 5)
    real_fetch = sdk_mod._fetch_rerank_pool_texts
    texts_seen: list[list[str]] = []

    class _Recording:
        def score(self, query, texts):
            texts_seen.append(list(texts))
            return [0.0 for _ in texts]

    pool_sizes: list[int] = []

    def _drops_the_last(graph, entity_type, graph_label, id_field, ids):
        pool_sizes.append(len(ids))
        out = real_fetch(graph, entity_type, graph_label, id_field, ids)
        if len(out) > 1:
            out.pop(sorted(out)[-1])
        return out

    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _drops_the_last)
    _inject(_Recording(), monkeypatch)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=5,
                           leg_trace=trace)

    entry = _rerank_entries(trace)[0]
    assert texts_seen, "the scorer never ran, so this test proves nothing"
    # ⛔ NO ``"" not in texts_seen[0]`` AND NO ``scored == len(texts_seen[0])``.
    # Both are unfalsifiable HERE (measured, and then removed): ``rerank_pool``
    # filters ``scorable`` by ``.strip()`` before it calls the scorer, so no
    # sdk-side change can put ``""`` in front of the model; and ``scored`` is
    # derived from that same list, so the second compares a number to itself.
    # What CAN fail is the accounting: the excluded id is a candidate that came
    # back with no text, so it must be counted UNSCORED and not silently dropped
    # from the disclosure. Handing ``rerank_pool`` only the SCORED head (the
    # historical shape) reports ``unscored: 0`` here and reds. Exact, from the
    # recorded pool size — not a bound. (The sdk's own truthiness filter is NOT
    # what these two pin: ``rerank_pool`` strips an empty text itself, so
    # replacing that filter with a membership test leaves both green — measured.
    # The load it avoids is pinned by
    # ``test_a_pool_with_no_usable_text_makes_no_scorer_call``.)
    assert entry[rerank.RERANK_UNSCORED_KEY] == pool_sizes[0] - entry[
        rerank.RERANK_SCORED_KEY], entry
    assert entry[rerank.RERANK_SCORED_KEY] == pool_sizes[0] - 1, (
        f"scored={entry[rerank.RERANK_SCORED_KEY]} pool={pool_sizes[0]}")


def test_an_UNSCORED_row_keeps_its_SLOT_and_is_not_pushed_off_the_page(
        sdk, monkeypatch):
    """A page whose top row has no text must come back with that row still on it.

    ⛔ Measured before this fix: with a zeroed scorer (i.e. the reranker's own
    decision is "no change") and ONE row's pool text blanked, the blanked row —
    FIRST in the fused order — came back LAST, and at the caller's fixed ``limit``
    it dropped off the page while the trace reported ``count: 0``. Silent recall
    loss, and the mirror of the inversion this block already refuses (an excluded
    row PROMOTED over included ones). The unscored row keeps its slot.
    """
    for i in range(5):
        sdk._create_point(kind="statement",
                         content=f"zebra migration claim number {i:02d}")
    real_fetch = sdk_mod._fetch_rerank_pool_texts
    blanked: list[str] = []

    def _blank_the_first(graph, entity_type, graph_label, id_field, ids):
        out = real_fetch(graph, entity_type, graph_label, id_field, ids)
        first = next(iter(ids), None)
        if first in out:
            out[first] = ""
            blanked.append(first)
        return out

    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _blank_the_first)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")

    class _Flat:
        """Every score equal: the reranker asks for NO change."""

        def score(self, query, texts):
            return [0.0 for _ in texts]

    _inject(_Flat(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk._tortoise_fts_query(
        "zebra migration", entity_type="point", limit=5, leg_trace=trace)]
    assert blanked, "premise: one row's text must have been blanked"

    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is True and entry["degraded"] is False, entry
    # the unscored row did not move, so the page is the fused page
    assert on[0] == blanked[0], (
        f"the unscored row was demoted off the top of the page (on={on}, "
        f"blanked={blanked[0]})")
    assert entry[rerank.RERANK_SCORED_KEY] == 4, entry
    assert entry[rerank.RERANK_UNSCORED_KEY] >= 1, entry
    # …and with equal scores nothing moved, which the trace must say
    assert entry["count"] == 0, entry


def test_the_wiring_is_a_permutation_and_discloses_the_cap(
        sdk, monkeypatch):
    """One sentence, two halves: the row SET is unchanged, and the cap is SAID.

    A test that pinned the width without the disclosure would pass while the
    bound stayed silent — and a bound on what the reranker can PROMOTE is a
    result-shaping fact, so the disclosure is part of the contract.
    """
    _seed(sdk, 5)
    # The GATE-OFF control FIRST, before the flag is set, so "same rows" is
    # measured against the shipped order rather than against a second identical
    # run.
    off = sdk._tortoise_fts_query("zebra migration", entity_type="point",
                                 limit=5)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    # The width knob must reach BOTH the pool text read and the scorer: pin the
    # DEFAULT down and set the knob above it, which is exactly the shape that
    # used to be silently ignored (the read sliced with the constant).
    monkeypatch.setattr(rerank, "DEFAULT_FUSED_RERANK_POOL", 2)
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, "4")
    # ⛔ OBSERVE THE TEXT-READ WIDTH, do not infer it. Everything else in this test
    # reads the ENTRY, and the entry's width comes from the resolver. The pre-fix
    # shape was TWO parts — slice at the constant AND re-assemble `head + rest` —
    # and restoring that shape left all 37 tests green: the shortened read is
    # padded back by the empty-content tail, so the lengths still add up and
    # `FakeScorer`'s empty texts tie and keep their indices. What it really does
    # is SCORE two candidates on "" instead of on their own text, which is
    # invisible from lengths — so the ids handed to the batch read are recorded.
    # (Applying the slice ALONE drops rows outright, and the permutation
    # assertion below catches that; this one catches the faithful shape.)
    _real_fetch = sdk_mod._fetch_rerank_pool_texts
    seen_ids: list[list[str]] = []

    def _recording_fetch(graph, entity_type, graph_label, id_field, ids):
        seen_ids.append(list(ids))
        return _real_fetch(graph, entity_type, graph_label, id_field, ids)

    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _recording_fetch)
    _inject(rerank.FakeScorer(), monkeypatch)

    trace: list[dict] = []
    on = sdk._tortoise_fts_query("zebra migration", entity_type="point",
                                limit=5, leg_trace=trace)

    # PERMUTATION, end to end: same ids, same length, nothing dropped. The gate
    # is the ONLY difference between the two reads.
    assert sorted(r["id"] for r in on) == sorted(r["id"] for r in off)
    assert len(on) == len(off)

    entries = _rerank_entries(trace)
    assert len(entries) == 1, trace
    entry = entries[0]
    assert entry["reason"] == "ok" and entry["ran"] is True
    assert entry[rerank.RERANK_POOL_KEY] == 4      # the knob, not the constant

    # SCORED + UNSCORED is the candidate set the rerank was handed. Derive it
    # from the gate-off read at the SAME limit rather than assuming a count: the
    # legs' own windows decide how many rows fuse, and a hand-written number
    # would pin a property of this fixture instead of the contract.
    candidates = len(off)
    # ⛔ THE ASSERTION THAT ACTUALLY DEFENDS THE WIDTH FIX: the batch read was
    # asked for the KNOB's width (4), not the monkeypatched DEFAULT (2). On the
    # pre-fix slice this reads 2 and reds, while every entry-level assertion
    # above still passes.
    assert seen_ids, "the batch text read never ran"
    assert all(len(ids) == min(4, candidates) for ids in seen_ids), seen_ids
    assert entry[rerank.RERANK_SCORED_KEY] == min(4, candidates)
    # ⛔ EQUALITY, NOT ``>=``: the wiring hands over ONE ROW PER CANDIDATE, so
    # ``scored + unscored`` is the whole candidate set at every width — and it
    # was structurally short when only the scored head was handed over (the
    # silent-cap defect reproduced inside the disclosure). A ``>=`` here would
    # pass over exactly that. (An inner ``if candidates < 5`` used to carry the
    # equality on the no-cut case only; measured, it never executed at all —
    # this fixture always has five — so the branch defended nothing.)
    assert (entry[rerank.RERANK_SCORED_KEY]
            + entry[rerank.RERANK_UNSCORED_KEY]) == candidates
    # Degradation is NOT reported through ``degraded``: in a leg trace that flag
    # means "a retrieval leg did not run" and would turn the read STATUS degraded.
    assert entry["degraded"] is False


def test_the_off_path_writes_no_rerank_trace_entry(sdk, monkeypatch):
    """A caller that never opted in pays nothing and is told nothing — the
    entry exists only when the lever ran, so its presence is itself the signal."""
    monkeypatch.delenv(rerank.FUSED_RERANK_ENV, raising=False)
    _seed(sdk, 3)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=3,
                           leg_trace=trace)
    assert _rerank_entries(trace) == []


def test_a_QUERY_LESS_full_scan_discloses_the_rerank_it_could_not_run(
        sdk, monkeypatch):
    """#12382 — an ARMED lever on the query-less path must say it declined.

    ``tortoise_fts_query(None, kind=..., limit=N)`` is a documented
    caller-facing mode (``tool_registry``: "Full-scan mode: omit query, set
    kind"). The rerank block's condition carried ``(query or "").strip()``, so
    this shape left through its own ``not query`` exit WITHOUT an entry —
    measured at #12343's head ``93af561442`` as ``rerank entries: []`` with the
    gate armed: byte-identical to a caller that never armed it. That is the one
    thing the block exists to prevent, and it is the SAME rule its
    ``recency_order_applied`` / ``order_by_not_relevance`` arms already obey.

    The gate-OFF half is asserted here rather than in a second test: the whole
    point is the DIFFERENCE between the two reads, so measuring them apart
    would let one drift while the pair still read as covered.

    ⛔ FIVE CANDIDATES, A THREE-ROW PAGE (review round 3): the arm reports the
    PRE-CUT candidate count, so asserting ``unscored == len(returned_rows)`` on
    a fixture that seeds exactly ``limit`` rows pins the fixture rather than the
    contract — it would go red on a correct read the moment the pool outgrew the
    page. The candidate set is therefore DERIVED from the gate-off read at a
    wide limit, and the cut is exercised deliberately.
    """
    _seed(sdk, 5)
    # 1. GATE OFF — no entry, the pre-existing (and still correct) contract;
    #    the wide-limit read is also the independent candidate count below.
    monkeypatch.delenv(rerank.FUSED_RERANK_ENV, raising=False)
    off_trace: list[dict] = []
    off_rows = sdk._tortoise_fts_query(None, kind="statement",
                                     entity_type="point", limit=50,
                                     leg_trace=off_trace)
    assert len(off_rows) == 5, off_rows
    assert _rerank_entries(off_trace) == []
    # 2. GATE ON — exactly ONE entry, saying the rerank did not run and WHY.
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    on_trace: list[dict] = []
    on_rows = sdk._tortoise_fts_query(None, kind="statement",
                                     entity_type="point", limit=3,
                                     leg_trace=on_trace)
    assert [r["id"] for r in on_rows] == [r["id"] for r in off_rows[:3]], (
        "the disclosure must not change the READ — only the trace")
    entries = _rerank_entries(on_trace)
    assert len(entries) == 1, on_trace
    entry = entries[0]
    assert entry["ran"] is False
    # ``empty_query`` is ``rerank_pool``'s OWN degrade term for this fact, not a
    # new word invented at the wiring — the vocabulary existed and the wiring
    # could never reach it, because this exit fired first.
    assert entry["reason"] == "empty_query", entry
    # ``degraded`` means "a retrieval LEG did not run" in a leg trace; a lever
    # that declined to reorder is not that, so it stays False (a True here would
    # turn the whole read's status degraded for a correct read).
    assert entry["degraded"] is False
    assert entry[rerank.RERANK_SCORED_KEY] == 0
    # The arm reports what the rerank was HANDED — the whole pre-cut candidate
    # set — so it is the wide-limit count, not the three rows this page returned.
    # (Asserting against the page would pin the fixture, not the contract.)
    assert entry[rerank.RERANK_UNSCORED_KEY] == len(off_rows), entry


def test_the_disclosure_does_not_DECLARE_a_full_scan_degraded(sdk,
                                                              monkeypatch):
    """#12382 review round 3 — the entry is audit and must not create a false
    signal one surface over.

    ``declared_degraded_read`` gave a ``query=None`` full scan a single-leg
    "by design" escape by testing that EVERY trace entry was ``structural``. The
    new disclosure is non-structural, so on an ARMED deployment the same correct
    3-row read was declared
    ``degraded_read: True, vector_leg_unavailable: True, reason="leg_absent"`` —
    blaming an embedder the read never consulted, and ``require_hybrid_read`` (a
    real-lane gate) then refused it THROUGH THAT FALSE MARKER. This is the
    composition test: the unit cases live in ``tests/test_2952_degraded_read.py``,
    but only a real store read shows the trace the SDK actually composes.

    ⛔ THIS DOES NOT MAKE A QUERY-LESS READ PASS THAT GATE. Post-fix
    ``require_hybrid_read`` still refuses a ``query=None`` full scan, by design —
    its own positive-proof rule (``_all_vector_arms_measured_empty_scope`` is
    false when no vector arm exists) — so the fix removes a FALSE signal, it does
    not grant the read a verdict it never earned.
    """
    _seed(sdk, 3)
    monkeypatch.delenv(rerank.FUSED_RERANK_ENV, raising=False)
    off: list[dict] = []
    sdk._tortoise_fts_query(None, kind="statement", entity_type="point",
                           limit=3, leg_trace=off)
    assert declared_degraded_read(off) is None

    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    on: list[dict] = []
    rows = sdk._tortoise_fts_query(None, kind="statement", entity_type="point",
                                  limit=3, leg_trace=on)
    assert rows
    # The disclosure IS in the trace (the issue's fix) ...
    assert len(_rerank_entries(on)) == 1, on
    # ... and it changes nothing about how the read is declared.
    assert declared_degraded_read(on) is None, declared_degraded_read(on)


def test_a_read_whose_CANDIDATE_SET_was_emptied_says_empty_pool_not_empty_query(
        sdk, monkeypatch):
    """#12382 — the two non-entries are DIFFERENT facts and get different names.

    A query-less read has nothing to score WITH; a read whose candidate set was
    emptied before the rerank has nothing to score. Reporting the second as
    ``empty_query`` would blame the caller's query for an empty candidate set —
    the mis-direction the ``pool_text_empty`` / ``pool_text_read_failed`` split
    above exists to avoid.

    The candidate set is emptied here by the KIND FILTER, not by an empty store:
    a read with NO hits at all returns from the fallback path before the rerank
    block is reached, so no entry is emitted there — and that read already
    carries its own verdict on every leg it asked (``empty_results``, or
    ``no_embedder`` / ``no_query_vector`` / ``below_relevance_floor`` /
    ``breaker_open``, or the fallback's own ``tfidf_snapshot`` /
    ``no_fallback_applicable``), so a rerank entry would restate one fact twice.
    """
    _seed(sdk, 3)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    trace: list[dict] = []
    # "observation" is a real point kind and nothing in this store carries it, so
    # the fused pool fuses normally and the KIND FILTER empties ``result_ids``
    # before the rerank block — the ``result_ids``-empty exit, reached.
    rows = sdk._tortoise_fts_query("zebra migration", kind="observation",
                                  entity_type="point", limit=3,
                                  leg_trace=trace)
    assert rows == [], "the kind filter must empty the page"
    entries = _rerank_entries(trace)
    assert len(entries) == 1, trace
    entry = entries[0]
    assert entry["ran"] is False
    assert entry["reason"] == "empty_pool", entry
    assert entry["degraded"] is False
    assert entry[rerank.RERANK_SCORED_KEY] == 0
    assert entry[rerank.RERANK_UNSCORED_KEY] == 0


def test_a_CALLER_DECISION_outranks_an_empty_input_in_the_reason(sdk,
                                                               monkeypatch):
    """#12382 review round 1 — the reason names what the READ can act on.

    The new arm sits ABOVE the four existing ones and, like them, is keyed on
    ``result_ids``/the query text — so on these two shapes nothing else fires and
    a bare ``empty_query`` would be the only thing a caller sees. But the read did
    not decline for that reason: it had already decided not to rerank.

    ``recall_state`` is the product path that makes this reachable rather than
    hypothetical: it passes ``_rerank_candidates=False`` AND ``leg_trace`` while
    its own ``query`` defaults to ``None``. Reporting ``empty_query`` there sends
    the reader to supply a query that would still not arm the lever.
    """
    _seed(sdk, 3)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")

    # 1. the caller re-owns the order (``recall_state``'s exact shape)
    owned: list[dict] = []
    sdk._tortoise_fts_query(None, kind="statement", entity_type="point",
                           limit=3, _rerank_candidates=False,
                           leg_trace=owned)
    entries = _rerank_entries(owned)
    assert len(entries) == 1, owned
    assert entries[0]["reason"] == "order_owned_downstream", entries[0]
    assert entries[0]["ran"] is False and entries[0]["degraded"] is False

    # 2. the caller asked for another ORDER — same rule, its own reason.
    ordered: list[dict] = []
    sdk._tortoise_fts_query(None, kind="statement", entity_type="point",
                           limit=3, order_by="confidence", leg_trace=ordered)
    entries = _rerank_entries(ordered)
    assert len(entries) == 1, ordered
    assert entries[0]["reason"] == "order_by_not_relevance", entries[0]
    assert entries[0]["ran"] is False and entries[0]["degraded"] is False

    # 3. and with NEITHER decision in play the empty-input reason returns — the
    #    control that keeps the two above from being satisfied by a blanket
    #    rule that always names a caller decision.
    plain: list[dict] = []
    sdk._tortoise_fts_query(None, kind="statement", entity_type="point",
                           limit=3, leg_trace=plain)
    entries = _rerank_entries(plain)
    assert len(entries) == 1, plain
    assert entries[0]["reason"] == "empty_query", entries[0]

    # 4. the third caller decision — a declared RECENCY order — is the same rule.
    #    ``empty_query`` here would be advice that loops: supplying a query then
    #    reports ``recency_order_applied`` and the lever still does not run.
    recency: list[dict] = []
    sdk._tortoise_fts_query(None, kind="statement", entity_type="point",
                           limit=3, recency_field="createdAt",
                           recency_boost=10.0, leg_trace=recency)
    entries = _rerank_entries(recency)
    assert len(entries) == 1, recency
    assert entries[0]["reason"] == "recency_order_applied", entries[0]
    assert entries[0]["ran"] is False and entries[0]["degraded"] is False


def test_the_recency_skip_CAN_coexist_with_an_emptied_pool(sdk, monkeypatch):
    """#12382 review round 5 — the caller-decision rule covers THIS shape too.

    ``_recency_applied`` is bound at step 4b, when the pool is still non-empty;
    six in-place filters run after it (kind, relationship, traversal, status,
    claim-class, turn-echo), so a read can reach this block with a live recency
    order AND no candidates. The reason is then ``recency_order_applied``, not
    ``empty_pool`` — the same precedence ``order_by_not_relevance`` /
    ``order_owned_downstream`` already take on a filter-emptied pool.

    Nothing pinned this before: every other recency test runs on a non-empty
    pool, so a future "restore ``empty_pool`` first" edit would have reversed the
    caller-decision rule with the whole suite green.
    """
    _seed(sdk, 3)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    trace: list[dict] = []
    rows = sdk._tortoise_fts_query(
        None, kind="statement", entity_type="point", limit=3,
        relationship_filter="IMPL:does-not-exist",
        recency_field="createdAt", recency_boost=10.0, leg_trace=trace)
    assert rows == [], "the relationship filter must empty the pool"
    entries = _rerank_entries(trace)
    assert len(entries) == 1, trace
    entry = entries[0]
    assert entry["reason"] == "recency_order_applied", entry
    assert entry["ran"] is False and entry["degraded"] is False
    # ``unscored == 0`` is the proof the pool really was emptied here.
    assert entry[rerank.RERANK_UNSCORED_KEY] == 0, entry


def test_the_EMPTY_POOL_wins_when_the_query_is_empty_too(sdk, monkeypatch):
    """#12382 review round 2 — both-empty resolves to ``empty_pool``.

    ``rerank_pool`` returns ``empty_pool`` BEFORE ``empty_query``
    (``tortoise/rerank.py``), so the wiring must not invert that order: on a read
    that returned no rows, naming the query sends the caller to supply a query
    that would change nothing, while the actual cause is the filter that emptied
    the candidate set. ``rerank_unscored == 0`` is the proof the pool was empty.

    A ``relationship_filter`` on a predicate nothing in the store carries empties
    ``result_ids`` in place before the rerank block; the ``kind`` FILTER cannot be
    used for this shape because the kind filter only runs when a query is present
    (``kind`` is still passed, because the query-less read is kind-SCOPED).
    """
    _seed(sdk, 3)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    trace: list[dict] = []
    # ``kind`` is REQUIRED for the read to return a pool at all (the full scan is
    # kind-scoped); the relationship filter then empties that pool in place.
    rows = sdk._tortoise_fts_query(
        None, kind="statement", entity_type="point", limit=3,
        relationship_filter="IMPL:does-not-exist", leg_trace=trace)
    assert rows == [], "the relationship filter must empty the page"
    entries = _rerank_entries(trace)
    assert len(entries) == 1, trace
    entry = entries[0]
    assert entry["ran"] is False
    assert entry["degraded"] is False
    assert entry["reason"] == "empty_pool", entry
    assert entry[rerank.RERANK_SCORED_KEY] == 0
    assert entry[rerank.RERANK_UNSCORED_KEY] == 0


def _candidate_count(sdk, monkeypatch, query, limit):
    """The pre-rerank candidate count, measured with the gate OFF.

    ⛔ ``unscored > 0`` CANNOT FAIL on a degraded arm: whenever ``applied`` is
    False the wiring reports ``unscored = len(result_ids)``, and the block only
    runs when ``result_ids`` is non-empty — so the assertion is satisfied by the
    branch's own arithmetic rather than by what the read did. The count has to be
    derived INDEPENDENTLY and compared exactly, which is what this does: the same
    read with the flag unset returns the pre-rerank list, whose length is the
    number of candidates the degraded arm must account for.
    """
    monkeypatch.delenv(rerank.FUSED_RERANK_ENV, raising=False)
    try:
        return len(sdk._tortoise_fts_query(query, entity_type="point",
                                          limit=limit))
    finally:
        monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")

def test_an_opted_in_rerank_that_could_not_read_its_texts_says_so(
        sdk, monkeypatch):
    """A silent no-op on an OPTED-IN lever is not allowed to look like "off".

    The pool text read is best-effort, so a transient graph error used to skip
    the whole block — INCLUDING the trace entry — leaving a caller with the same
    trace a non-opted caller sees. #8487's ``valid_time`` leg records its dead
    read for the same reason.
    """
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _seed(sdk, 4)
    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts",
                        lambda *a, **k: {})
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=4,
                           leg_trace=trace)
    entries = _rerank_entries(trace)
    assert len(entries) == 1, trace
    assert entries[0]["ran"] is False
    assert entries[0]["reason"] == "pool_text_read_failed"
    # and it still accounts for the candidates it never saw — the EXACT count,
    # not merely "> 0" (see _candidate_count for why that cannot fail).
    assert entries[0][rerank.RERANK_SCORED_KEY] == 0
    candidates = _candidate_count(sdk, monkeypatch, "zebra migration", 4)
    assert candidates > 0, "premise: the read must return candidates"
    assert entries[0][rerank.RERANK_UNSCORED_KEY] == candidates


def test_a_read_with_no_text_property_says_that_instead_of_blaming_the_read(
        sdk, monkeypatch):
    """``no_text_field`` is DEFENSIVE (every accepted ``entity_type`` has a
    declared property today), so it is exercised here by patching the resolver —
    otherwise the arm is silently dead and the next label that lacks a field
    reports "the read broke" when the truth is "there is nothing to score"."""
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _seed(sdk, 3)
    monkeypatch.setattr(sdk_mod, "_rerank_text_field", lambda *a, **k: None)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=3,
                           leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    assert entry["reason"] == "no_text_field" and entry["ran"] is False
    candidates = _candidate_count(sdk, monkeypatch, "zebra migration", 3)
    assert candidates > 0, "premise: the read must return candidates"
    assert entry[rerank.RERANK_UNSCORED_KEY] == candidates


def test_a_degraded_scorer_reports_every_candidate_unscored(sdk, monkeypatch):
    """A rerank that ORDERED NOTHING left the whole pool unscored — reporting
    ``unscored: 0`` there is the same false all-clear as the pre-fix applied arm.

    ``rerank_pool`` is the WIDTH BOUND, so it still reads 40 here: the bound
    applied, nothing scored under it."""
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(RuntimeError("model unavailable"), monkeypatch)
    _seed(sdk, 4)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=4,
                           leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False
    assert entry["degraded"] is False, entry   # the DISCLOSED arm, not a leg failure
    assert entry[rerank.RERANK_SCORED_KEY] == 0
    candidates = _candidate_count(sdk, monkeypatch, "zebra migration", 4)
    assert candidates > 0, "premise: the read must return candidates"
    assert entry[rerank.RERANK_UNSCORED_KEY] == candidates
    assert "model unavailable" in entry["reason"]


def test_the_rerank_runs_before_the_valid_time_demotion():
    """#12297 — ORDER IS A CONTRACT, so it is pinned rather than commented.

    #8487 made the valid-time demotion the read's LAST word on order: a
    superseded claim must never be served as current on a present-tense query.
    A relevance reorder placed after it re-promotes exactly that row. The
    composed behaviour needs the temporal branch (intent detection + a state
    map + edge rows) to reach, so the invariant is pinned where it is legible:
    the call order in the source.
    """
    src = inspect.getsource(sdk_mod.TortoiseSDK._tortoise_fts_query)
    assert src.index("rerank_pool(") < src.index("prefer_latest_ids(")


def test_search_sessions_re_owns_the_order_and_does_NOT_pay_for_a_rerank(sdk, monkeypatch):
    """The seventh re-owned order, and the one the opt-out list missed.

    ``search_sessions`` reads a GENEROUS candidate pool, then re-sorts it by
    ``scores["rrf"]`` — the PRE-rerank fused score — and slices the page. A
    rerank inside that read is a full permutation of the same list which the
    sort then discards: it cannot change the page (so nothing looks broken) and
    it pays a model load plus ~207 ms/mention to compute nothing, invisibly.

    ⛔ Pinned by the LOAD, not by the order. A scorer that never runs produces
    the same page as one that runs and is discarded, so an order assertion
    cannot tell the two apart — measured: the trace entry is not even written
    here (``search_sessions`` passes no ``leg_trace``), so the only observable
    is whether the cross-encoder was loaded at all.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce} session"
    for i in range(3):
        # ``subject`` is the Event's TEXT column (``_RERANK_TEXT_FIELD_BY_LABEL``):
        # without it the pool is textless, the load is skipped for that reason
        # instead, and this test would pass over the missing opt-out (measured).
        sdk._create_event(f"{q} probe {i}", "AgentSession",
                         subject=f"{q} body {i}",
                         keywords=[f"kw{i}"], eventStatus="completed")

    loads: list[str] = []

    def _loader(name, **kw):
        loads.append(name)
        return rerank.FakeScorer(), ""

    monkeypatch.setattr(rerank, "load_scorer", _loader)
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    found = sdk.search_knowledge(f"zebra {nonce}", mode="sessions", limit=3)
    assert found, "premise: the search must return sessions, or nothing ran"
    assert loads == [], (
        "search_sessions loaded the cross-encoder for a page it re-sorts by the "
        f"PRE-rerank score and slices (loads={loads})")


class _SeamFlow(NamedTuple):
    """What the value-flow walk attributed, and whether it had to give up.

    ``calls``  — every ``ast.Call`` the mention can reach: the READS.
    ``opaque`` — the mention (or a name carrying it) flows out through a binding
                 shape this walk does NOT model, so **deadness is not proven**.
                 A caller that skips an opaque mention reports a live read as
                 dead, which is #14278 F1's fail-open — so it must count it.
    """

    calls: list
    opaque: bool


def _seam_modelled_binding(node):
    """``(values, targets)`` for a name-binding form this walk MODELS, else None.

    A closed enumeration of the shapes a seam read actually takes: plain
    assignment (the ``_opt = SEAM`` hop), an annotation, a ``for`` target
    (``for fn in (SEAM,): fn(…)``), a comprehension target, a ``with … as``
    target and a walrus. Every form NOT listed here is caught by
    ``_seam_opaque_values``, so a spelling nobody thought of fails closed instead
    of being read as "provably dead" (#14278 F1).
    """
    if isinstance(node, ast.Assign):
        return [node.value], list(node.targets)
    if isinstance(node, ast.AnnAssign):
        return ([node.value] if node.value is not None else []), [node.target]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.iter], [node.target]
    if isinstance(node, ast.comprehension):
        return [node.iter], [node.target]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        values, targets = [], []
        for item in node.items:
            values.append(item.context_expr)
            targets.append(item.optional_vars)
        return values, targets
    if isinstance(node, ast.NamedExpr):
        return [node.value], [node.target]
    return None


def _seam_opaque_values(node):
    """Value positions of a binder this walk does NOT model (#14278 F1).

    The binder/escape kinds of the CURRENT grammar that take a value expression
    and hand it somewhere this walk does not follow: an augmented assignment, a
    value escaping its function (``return``/``yield``), a parameter default or a
    decorator, a class base/keyword, a ``match`` subject or pattern expression, an
    ``except`` clause's exception TYPE (Python CALLS it to match), and a ``type``
    statement's value. When the mention reaches one of these the walk cannot say
    what happens to it — so "no call found" is NOT deadness and the caller must
    fail closed.

    ⛔ This is an enumeration, not a proof about future syntax: it is every binder
    kind in the Language Reference's list ("Naming and binding", §4.2.1) that this
    walk does not model, as of this grammar — including ``type`` parameter lists
    (`def f[T: SEAM]()`). Adding a binding kind to Python is a change to this
    function, and the pin's ``escapes``/``except-as``/``type-param`` rows are the
    tripwire that says so. The one kind deliberately omitted is ``import``, which
    binds module paths (strings and dotted ``Name``s), never an expression that
    could be a seam mention.

    ``kw_defaults`` may hold ``None`` for a required keyword-only parameter; the
    walk treats ``None`` as an expression it does not carry.
    """
    if isinstance(node, ast.AugAssign):
        return [node.value]
    if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
        return [node.value]
    if isinstance(node, ast.Lambda):
        return [*node.args.defaults, *node.args.kw_defaults]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [*node.decorator_list, *node.args.defaults,
                *node.args.kw_defaults,
                *(getattr(node, "type_params", None) or [])]
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases,
                *(k.value for k in node.keywords),
                *(getattr(node, "type_params", None) or [])]
    if isinstance(node, ast.ExceptHandler):
        return [node.type]
    if isinstance(node, ast.TypeAlias):
        return [node.value, *(getattr(node, "type_params", None) or [])]
    if isinstance(node, ast.Match):
        values = [node.subject]
        for case in node.cases:
            for sub in ast.walk(case.pattern):
                if isinstance(sub, ast.expr):
                    values.append(sub)
        return values
    return []


def _seam_read_calls(tree, mention) -> _SeamFlow:
    """Every call `mention` can REACH in `tree`, plus whether the walk gave up.

    ⛔ DEADNESS MUST BE PROVEN, never inferred from a shape this resolver does not
    model (#14278 F1). A mention reaches a call through more shapes than a direct
    call or a one-hop `Name` binding: a container (`_reads = [SEAM]` then
    `_reads[0](…)`), a subscript or attribute dispatch, a second alias hop
    (`_n = _m`), a `for`/comprehension/`with … as` target, a walrus, or being
    handed to something as an argument. Measured at head `59e701977a`: the
    one-hop form skipped the container and two-hop mutants, so both stayed GREEN —
    an undocumented read inheriting a PERMANENT exemption, which is the exact
    failure the arm this feeds exists to catch.

    So the carriers are the mention plus every name bound to an expression that
    CARRIES it (assignment RHS, container element, subscript or attribute
    target, loop/comprehension/with target, walrus value), closed to a fixpoint; a
    call is a read when its CALLEE or one of its ARGUMENTS carries a carrier. A
    call boundary stops the RHS walk — `results = SEAM(…)` carries the call's
    RESULT, not the seam (that call is its own row) — so ordinary result plumbing
    cannot make a name a carrier.

    ⛔ A CARRIER REACHES AN ATTRIBUTE OF THE SAME NAME, because a class body IS its
    class's attribute dictionary (Language Reference §4.2.2): `class _Tbl: seamfn =
    SEAM` then `_Tbl.seamfn(…)` is a real read, and a walk that only matched
    `Name.id` read it as provably dead (round-10 review, P2). For the spelling an
    attribute match still misses — the class name fetched by STRING, `getattr(_Tbl,
    "seamfn")` — a class-body binding whose read the walk could NOT attribute is
    reported ``opaque`` (its name escaped into a namespace we can only then follow
    by string), so that residual is counted rather than exempted.

    ⛔ AND `calls == []` ALONE IS NOT DEADNESS. `59e701977a`'s one-hop walk learnt
    to follow four shapes and still read every untaught shape as dead: measured at
    `96b93db293`, a third read written as `for _fn in (SEAM,): _fn(…)` inside the
    granted owner yielded `[]` and the arm stayed GREEN on three reads (round-9
    review, P2). So a mention that reaches a binding form this walk does not model
    is returned with ``opaque=True``, and the caller must count it as a read.

    ⛔ SO THE ONE THING THIS RETURN VALUE PROVES IS NEGATIVE — AND ONLY ABOUT THE
    SHAPES THIS WALK MODELS: ``calls == [] and not opaque`` means no call this
    resolver can FOLLOW reaches the mention. It is not a statement about the whole
    file: a name dispatched by a string the walk cannot read (`globals()["fn"]`,
    `getattr(obj, "x")`) reaches the mention without ever naming it, which is why
    the class-body case admits rather than denies (round-11 review, P3).
    ``opaque`` is an admission, not a verdict — the caller must not let an opt-out
    on the calls it DID find suppress a mention that also escaped, or the admission
    buys nothing (round-10 review, P3: ``_o = SEAM; _o(…,
    _rerank_candidates=False); return _o`` was skipped as "all opted out" while
    ``opaque`` was True).

    ⛔ KNOWN OVER-BLOCK, deliberately accepted. The carrier set is FILE-scoped and
    keyed on bare names, so binding a mention to a name that the file already uses
    for something else widens what the walk attributes: `for q in (SEAM,): pass`
    where `run` already loops `for q in queries:` makes every `q`-rooted call look
    like a read (round-10 review, P3). That reds a CORRECT tree, which the
    fail-open/fail-closed doctrine rates B rather than A — the opposite defect
    from the one this resolver exists to catch, and the direction the gate must
    err in. It is not repaired by a name-rebinding heuristic here: a file-scoped
    walk cannot tell the two `q`s apart without a scope model, and a cheap guess
    would re-open the fail-open. The count row does NOT detect this: the
    colliding name makes unrelated calls look ATTRIBUTED, so the red it raises
    reports them as resolved reads (round-11 review, P3). What it does report is
    how many mentions it could not attribute, which is why the over-block shows as
    a plain count mismatch rather than a named cause.
    """
    carriers: set = set()
    parent = {id(child): node for node in ast.walk(tree)
              for child in ast.iter_child_nodes(node)}

    def _in_class_body(node) -> bool:
        """Is `node` a statement of a class BODY (so its name IS an attribute)?

        ⛔ The walk STOPS at the first function boundary: §4.2.2 says "the scope of
        names defined in a class block is limited to the class block; it does not
        extend to the code blocks of methods", so a binding inside a method is a
        LOCAL, not an attribute — treating it as a class-body name counted a dead
        local as a live read (round-11 review, P3).
        """
        up = parent.get(id(node))
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return False
            if isinstance(up, ast.ClassDef):
                return True
            up = parent.get(id(up))
        return False

    def _carries(expr) -> bool:
        stack = [expr]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if node is mention:
                return True
            if isinstance(node, ast.Name) and node.id in carriers:
                return True
            if isinstance(node, ast.Attribute) and node.attr in carriers:
                return True
            if isinstance(node, ast.Call):
                continue        # a call is its own row; its VALUE is a result
            stack.extend(ast.iter_child_nodes(node))
        return False

    class_bound = False
    grown = True
    while grown:
        grown = False
        for node in ast.walk(tree):
            parts = _seam_modelled_binding(node)
            if parts is None:
                continue
            values, targets = parts
            if not any(_carries(value) for value in values):
                continue
            if _in_class_body(node):
                class_bound = True
            for target in targets:
                if target is None:
                    continue
                for sub in ast.walk(target):
                    # ⛔ `add`, never `update`: `.update(` is an ordinary-word
                    # receiver the SDK residue sweep counts, and a test-only diff
                    # must not move that pin (measured at `59e701977a`).
                    if isinstance(sub, ast.Name) and sub.id not in carriers:
                        carriers.add(sub.id)
                        grown = True

    reads: list = []
    opaque = False
    for node in ast.walk(tree):
        if _seam_modelled_binding(node) is None and any(
                _carries(value) for value in _seam_opaque_values(node)):
            opaque = True
        if not isinstance(node, ast.Call):
            continue
        if _carries(node.func) or any(
                _carries(arg) for arg in
                (*node.args, *(k.value for k in node.keywords))):
            reads.append(node)
    # ⛔ A CLASS-BODY BINDING IS ALWAYS AN ADMISSION, even when a read was found:
    # the name has escaped into a namespace the walk can only then follow by
    # STRING (`getattr(_Tbl, "seamfn")`), so letting a found read suppress the
    # admission hides a second, unattributed read on the same binding — the exact
    # shape the opt-out paragraph above condemns (round-11 review, P2).
    return _SeamFlow(calls=reads, opaque=opaque or class_bound)


def test_the_grant_grader_follows_EVERY_shape_a_seam_read_can_take():
    """#14278 F1 — the resolver is not vacuous, and it does not free-ride either.

    The census grant on `tools/read_wave_latency.py` is PERMANENT and its only
    recorded reason is a comment, so this resolver is the one thing that grades
    it. A one-hop `Name` resolver read the container and two-hop shapes as "reads
    nothing" and skipped them (measured GREEN at `59e701977a`, RED at its
    parent); the other direction matters just as much — a genuinely dead binding
    must stay dead, or the count silently absorbs a read that went missing. So
    each shape is pinned here by the query its fed call carries, and the synthetic
    module is not in any census tree.

    ⛔ AND THE SHAPES NOBODY TAUGHT IT. The `escapes` mention below is bound by no
    modelled form at all (`return SEAM`), so the walk cannot say what happens to
    it. It must come back `opaque=True` and be COUNTED as a read by the arm: at
    `96b93db293` an untaught spelling (`for _fn in (SEAM,): _fn(…)`) returned `[]`
    and the arm stayed green on three reads (round-9 review). Graded in both
    directions so neither "skip it as dead" nor "call everything dead" can pass.

    ⛔ FIVE ROWS EXIST SO THE PIN CANNOT PASS WITHOUT THE MECHANISM IT CLAIMS:
    `class-attr` needs the attribute carrier (a class body IS the class's
    attribute dict — round-10 P2 read it as dead); `two-hop` is written in REVERSE
    textual order so a single pass cannot resolve it (without it, deleting the
    `while grown` fixpoint left every row green); `except-as` needs the ``except``
    clause's exception TYPE treated as an escape, because Python CALLS it; and
    `type-alias`, `match-subject`, `type-param` and `class-dead` grade the
    enumerations and the class-body admission ADDED for round 10 — without them
    deleting `ast.TypeAlias`, the `ast.Match` opener or the `class_bound`
    admission left the pin green, so those fixes were graded nowhere (round-11
    review, P3).
    """
    source = '''
class SDK:
    def _tortoise_fts_query(self, **kw):
        return []

def run(sdk):
    sdk._tortoise_fts_query(query="warm", limit=10)
    _reads = [sdk._tortoise_fts_query]
    _reads[0](query="container", limit=10)
    _opt = sdk._tortoise_fts_query
    _opt(query="one-hop", limit=10)
    _n = _m
    _m = sdk._tortoise_fts_query
    _n(query="two-hop", limit=10)
    for _fn in (sdk._tortoise_fts_query,):
        _fn(query="for-target", limit=10)
    [_c(query="comprehension", limit=10)
     for _c in [sdk._tortoise_fts_query]]
    if (_w := sdk._tortoise_fts_query):
        _w(query="walrus", limit=10)
    class _Tbl:
        seamfn = sdk._tortoise_fts_query
    _Tbl.seamfn(query="class-attr", limit=10)

def dead(sdk):
    _dead = sdk._tortoise_fts_query

def escapes(sdk):
    return sdk._tortoise_fts_query

def exc(sdk):
    try:
        pass
    except sdk._tortoise_fts_query as _e:
        _e(query="except-as", limit=10)

def typealias(sdk):
    type _T = sdk._tortoise_fts_query

def matched(sdk):
    match sdk._tortoise_fts_query:
        case _:
            pass

def classbodydead(sdk):
    class _C:
        deadfn = sdk._tortoise_fts_query

def generic[T: sdk._tortoise_fts_query]():
    pass
'''
    tree = ast.parse(source)
    mentions = [n for n in ast.walk(tree)
                if isinstance(n, ast.Attribute)
                and n.attr in ("tortoise_fts_query", "_tortoise_fts_query")]
    observed = []
    for mention in mentions:
        flow = _seam_read_calls(tree, mention)
        observed.append((len(flow.calls), flow.opaque, sorted(
            k.value.value for call in flow.calls for k in call.keywords
            if k.arg == "query" and isinstance(k.value, ast.Constant))))
    assert sorted(observed) == [
        (0, False, []),                 # the dead binding: provably no call
        (0, True, []),                  # `return SEAM`: NOT provably dead
        (0, True, []),                  # `except SEAM as e`: Python CALLS it
        (0, True, []),                  # `type _T = SEAM`
        (0, True, []),                  # `match SEAM:` subject
        (0, True, []),                  # dead name bound in a CLASS body
        (0, True, []),                  # PEP 695 `def f[T: SEAM]()` bound
        (1, False, ["comprehension"]),  # `for _c in [SEAM]: _c(…)`
        (1, False, ["container"]),      # `_reads = [SEAM]; _reads[0](…)`
        (1, False, ["for-target"]),     # `for _fn in (SEAM,): _fn(…)`
        (1, False, ["one-hop"]),        # `_opt = SEAM; _opt(…)`
        (1, False, ["two-hop"]),        # `_n = _m` ABOVE `_m = SEAM`
        (1, False, ["walrus"]),         # `if (_w := SEAM): _w(…)`
        (1, False, ["warm"]),           # the direct call
        (1, True, ["class-attr"]),      # `class _Tbl: f = SEAM; _Tbl.f(…)`
    ], f"the resolver mis-graded a shape: {sorted(observed)}"


def test_a_caller_that_RE_OWNS_the_order_opts_the_rerank_out(sdk, monkeypatch):
    """The half of the guard that ``order_by``/recency cannot reach.

    Six product lanes re-own the order DOWNSTREAM (``_review_pool``,
    ``suggest_entry_points``, ``issue_insight``, ``recall_state``,
    ``recall_gaps``, ``_resolve_subgraph_seed``) and pass neither an
    ``order_by`` nor a recency field — so before this arm the rerank re-picked
    their page with the trace reading ``ok``, i.e. invisibly. Each of them
    already passes ``_valid_time_rerank=False`` for the identical reason and now
    passes ``_rerank_candidates=False`` too. Pinned behaviourally: an injected
    positional scorer must not move the page, and the refusal must be disclosed.

    ``search_sessions`` is the seventh and is pinned separately
    (``test_search_sessions_re_owns_the_order_and_does_NOT_pay_for_a_rerank``):
    it passes no ``leg_trace``, so the disclosure cannot be observed there and
    the LOAD is the only signal.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce}"
    for i in range(6):
        sdk._create_point(kind="statement", content=f"{q} reown probe {i}")

    off = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=3, _rerank_candidates=False)]
    assert len(off) == 3, f"premise: a full page must come back (off={off})"

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=3, _rerank_candidates=False,
        leg_trace=trace)]
    assert on == off, (
        "a caller that re-owns the order still had its page re-picked by the "
        f"rerank (off={off} on={on})")
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False, f"the skip must be disclosed ({entry})"
    assert entry["reason"] == "order_owned_downstream", entry

    # ⛔ AND THE OPT-OUT IS "NOT PASSED" AT THE SITES THAT NEED IT. A behaviour
    # test cannot reach `battery/runner/setup.py` or `tools/why_layer_rate.py`,
    # whose false diagnosis is the harm: pin the CALL SITES so a new caller
    # cannot quietly inherit a re-picked page. The behaviour above is the
    # assertion; this stops the sweep from being silently shrunk.
    #
    # ⛔ EVERY SITE IN `tortoise/` BY AST, plus a DERIVED population in `tools/`,
    # `battery/` and `benchmarks/`. Test files are mostly excluded (~90 of them
    # read this method and almost none is a product surface) — but "mostly", not
    # "all": `tests/eval/harness/runner.py` is a recorded-baseline harness and
    # `tests/repro/284_capstone_verify.py` a membership probe, so both are NAMED
    # below. The exclusion is not a claim about `tests/` — because
    # both weaker forms were measured green over a real defect:
    #   * a FILE-SCOPED SUBSTRING missed the call sites inside `tortoise/sdk.py`
    #     (deleting `_rerank_candidates=False` from any of the eight left the
    #     whole suite green, and a driven `limit=3` read shows `recall_state`'s
    #     page literally moving);
    #   * `ast.Call` nodes with `func.attr == "tortoise_fts_query"` missed the
    #     sites that hand the BOUND METHOD to a helper — `mcp_server.py`'s
    #     `_safe(_get_org_sdk().tortoise_fts_query, …)` — so
    #     `tortoise_suggest_entry_points` (default `limit=5`, no leg trace) was
    #     re-picking WHICH ENTITIES RESOLVE with nothing to disclose it;
    #   * and one external file has TWO sites, so `"kwarg" in src` passed with
    #     one of them removed (`tools/ask_pool_admission_probe.py`).
    import ast as _ast
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parent.parent

    #: Sites that INTENTIONALLY run the lever. Everything else must opt out.
    runs_the_lever = {
        ("tortoise/ask_lane.py", "run_ask_lane"):
            "the ask lane — reaches the same read with a relevance order, no "
            "recency and a non-empty question; priced as NOT calibrated in the "
            "brief's §5, i.e. disclosed by the document rather than skipped",
        ("tortoise/extractor_v2.py", "_fts_rows"):
            "the capture noun prior — KEEPS the rerank under its own "
            "OVERRIDES: record (#8487/#12297), measured recall@3 delta",
        ("tortoise/hosted_api.py", "search"): "user search — the surface §5 prices",
        ("tortoise/selfhost_api.py", "search"):
            "user search, self-hosted — the same read, so the same "
            "authorisation to run it",
        ("tortoise/mcp_server.py", "tortoise_query"): "user search (MCP text route)",
        ("tortoise/mcp_server.py", "tortoise_search"): "user search (MCP)",
        ("tortoise/sdk.py", "search_knowledge"):
            "the one SEARCH door (#11609): it is a WRAPPER, not a re-owner — the "
            "page it returns is the read's own page, and the surfaces above "
            "(`tortoise_search`/`tortoise_query`, hosted + self-hosted search) "
            "reach the same read through it, so the rerank is the read those "
            "surfaces price rather than a re-picked downstream order",
    }
    # ⛔ NO ROW FOR ``volunteer_context``. It binds the read as ``_search_fn=`` and
    # the opt-out rides in its ``_search_kwargs`` — MEASURED to matter: before
    # that seam was added, the armed gate re-picked 12 of the 18 ids at the
    # volunteer resolve limit while this census called it intentional. A seam is
    # not an exemption; it must carry the flag, and ``_sites`` reads it.

    def _sites(path: _pl.Path) -> list[tuple[str, bool]]:
        """(enclosing function, opted-out?) for every mention of the read."""
        tree = _ast.parse(path.read_text())
        # ⛔ THE DEEPEST enclosing function, not the first one the walk reaches.
        # ``window_quality_m2.query`` is NESTED inside ``run``, and the walk
        # reaches the outer def first — so every nested site reported its OUTER
        # owner (measured: `query()`'s read reported ``run``), which made an
        # owner-named grant name the wrong function and let any sibling site
        # under the same outer def inherit it.
        parents: dict[int, _ast.AST] = {}
        for n in _ast.walk(tree):
            for child in _ast.iter_child_nodes(n):
                parents[id(child)] = n
        out: list[tuple[str, bool]] = []
        for node in _ast.walk(tree):
            # ⛔ #12901 FINAL batch: the seam was PRIVATISED to `_tortoise_fts_query`
            # (body unchanged, public spelling a tombstone) — and a tombstone can never
            # be CALLED, so matching only the public spelling found no site at all
            # (measured: `scanned == []`, the floor below firing on an empty walk).
            # Both spellings are matched: the census is about the SEAM, not the name,
            # and a site still spelled publicly would be a live call to a dead name.
            if not (isinstance(node, _ast.Attribute)
                    and node.attr in ("tortoise_fts_query", "_tortoise_fts_query")):
                continue
            # the CALL the mention feeds: either it IS the callee, or it is an
            # argument (``_safe(sdk.tortoise_fts_query, …)``)
            call = None
            for c in _ast.walk(tree):
                if not isinstance(c, _ast.Call):
                    continue
                # the mention may be the callee, a positional argument
                # (``_safe(sdk.tortoise_fts_query, …)``) or a KEYWORD VALUE
                # (``_search_fn=sdk.tortoise_fts_query``)
                if (c.func is node or node in c.args
                        or any(node is k.value for k in c.keywords)):
                    call = c
                    break
            # ⛔ THE VALUE, NOT THE NAME. A presence test accepted
            # ``_rerank_candidates=True`` as an opt-out at every site here
            # (measured: the one-word flip at the volunteer seam left all 58
            # tests green while the armed gate re-picked that pool), which is
            # exactly the edit this census exists to catch.
            opted_out = bool(call) and any(
                k.arg == "_rerank_candidates"
                and isinstance(k.value, _ast.Constant)
                and k.value.value is False
                for k in call.keywords)
            if call is not None and not opted_out:
                # a read BOUND as a seam (``_search_fn=sdk.tortoise_fts_query``):
                # the flag rides in the adjacent ``_search_kwargs`` dict, so the
                # call itself carries no keyword for it — and the DICT is
                # evaluated, not grepped, for the reason above
                for k in call.keywords:
                    if k.arg != "_search_kwargs":
                        continue
                    try:
                        opts = _ast.literal_eval(k.value)
                    except (ValueError, SyntaxError):
                        continue        # not a literal: cannot be proven, so not
                    if isinstance(opts, dict) and opts.get(
                            "_rerank_candidates") is False:
                        opted_out = True
                        break
            owner = ""
            parent = parents.get(id(node))
            while parent is not None:
                if isinstance(parent, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    owner = parent.name
                    break
                parent = parents.get(id(parent))
            out.append((owner, opted_out))
        return out

    scanned: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    # ⛔ ``rglob``, not ``glob``: ``tortoise/`` holds 17 subpackages
    # (``projection/``, ``packs/``, ``session_import/``, …), and a caller placed
    # in one of them inherited the armed lever with the census still green.
    # Measured on a scratch tree holding one bare call in ``tortoise/top.py``
    # and one in ``tortoise/subpkg/diag.py``: ``glob`` reported
    # ``['tortoise/top.py:g']``, ``rglob`` reported both. The claim above is
    # "EVERY SITE IN ``tortoise/``", so the walk must mean it.
    for path in sorted((root / "tortoise").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        for owner, opted_out in _sites(path):
            scanned.append(f"{rel}:{owner}")
            if opted_out:
                continue
            seen_pairs.add((rel, owner))
            assert (rel, owner) in runs_the_lever, (
                f"{rel}:{owner}'s read does not opt out of the "
                "rerank and is not on the intentional list — with the gate "
                "armed it re-picks which rows survive limit, undisclosed")
    # ⛔ EXACT MEMBERSHIP, then a loose floor. The named set is the authority for
    # WHICH sites run the lever, so a deleted site reds on ``runs_the_lever``
    # below (a ``>=`` alone never notices one going missing); the floor only has
    # to catch a walk that stopped seeing the package at all, so it is set BELOW
    # the population on purpose — consolidating two call sites onto one read is
    # a legitimate edit, and a floor equal to today's count made it a false
    # "the trees moved".
    assert len(scanned) >= 12, scanned
    assert set(runs_the_lever) == set(seen_pairs), (
        "the sites that run the lever moved: added "
        f"{sorted(set(seen_pairs) - set(runs_the_lever))}, gone "
        f"{sorted(set(runs_the_lever) - set(seen_pairs))}")

    # The NAMED set below is the "must opt out" half; the DERIVED half (further
    # down) is what stops a NEW caller in these trees from staying green — a
    # literal tuple does not grow by itself, which is the failure this comment's
    # own body describes.
    for rel in ("battery/runner/setup.py", "tools/why_layer_rate.py",
                "tools/longmem_eval/retrieve.py", "tools/fusion_formula_ab.py",
                "tools/ask_pool_admission_probe.py",
                "benchmarks/run_report.py", "benchmarks/load_test.py",
                # ⛔ The docs half is NAMED as well as derived. It is the whole
                # reason the derived floor was 3 short of the population: these
                # five are the only files with no other row, so their quiet
                # removal was exactly what a loose ``>=`` permitted.
                "docs/runbook/4107_preference_abstention_diagnostic.py",
                "docs/runbook/5534_a4_ab_diagnostic.py",
                "docs/runbook/w6c_gold_rank_diagnostic.py",
                "docs/runbook/w7a_gold_rank_diagnostic.py",
                "docs/evidence/7983-cost-magnitudes/probes/ledger_probe.py",
                # ⛔ the eval HARNESS is in the census for the same reason the
                # seam block below exists: it binds the read as ``_search_fn=``
                # and its opt-out rides in ``_search_kwargs``. It was added to
                # the product in the same commit that wrote this comment, and a
                # census whose list is a literal tuple does not grow by itself —
                # deleting that kwarg kept every test green (measured).
                "tests/eval/harness/runner.py",
                # ⛔ A MEMBERSHIP PROBE one directory over: `limit=10` and the
                # assertion is `feature["id"] in ids`, so an armed rerank re-picks
                # the page and turns a healthy graph into a false FAIL (measured:
                # it was uncensused and did not opt out).
                "tests/repro/284_capstone_verify.py"):
        found = _sites(root / rel)
        assert found, f"{rel} no longer reads the rerankable page — check the pin"
        for owner, opted_out in found:
            assert opted_out, (
                f"{rel}:{owner} reads the rerankable page without opting out — "
                "with the gate armed its page composition moves and a "
                "membership probe returns a false diagnosis")

    # ⛔ THE DERIVED HALF. Every AST-visible call site in these three trees must
    # either opt out or be on the INTENTIONAL-KEEP list below — a lever that must
    # RUN to be calibrated (the two ask mirrors, the two calibration tools). A new
    # caller that does neither reds here, which is what a literal tuple could
    # never do.
    # ⛔ SITE-GRANULAR ON PURPOSE. The value is the set of FUNCTIONS in that
    # file whose read runs the lever; a bare file name granted every present and
    # future site in it, so a new armed read inherited the grant silently
    # (measured: adding one to `tools/window_quality_m2.py` left this census
    # green). A site outside its file's named owners now has to justify itself
    # like every other caller.
    intentional_keep = {
        # an ask MIRROR: must measure the shipped reader, i.e. the lever ON
        "tools/ask_recall_bench.py": frozenset({
            "_retrieve_pipeline", "_measure_question", "_scan_for_failures"}),
        "tools/gen_ask_transcripts.py": frozenset({"_render_user_message"}),
        # measures the rerank itself
        "tools/candidate_recall_11901.py": frozenset({"run", "window"}),
        "tools/rerank_calibration_12297.py": frozenset({"run"}),
        # ⛔ A MIRROR of the shipped USER door, which keeps the lever ON: the arms pass the
        # door's OWN arguments (no `_rerank_candidates` kwarg at all, because the MCP tool and
        # `GET /v1/search` pass none either) and the shipped/armed split is decided by the
        # `TORTOISE_FUSED_RERANK` env gate ONE FLAG apart (`user_search` vs
        # `user_search_rerank`). Its whole subject is what the lever does to recall@k, so
        # opting it out would measure a reader neither door runs — the same reason
        # `tools/window_quality_m2.py` is on this list rather than opted out. The comment at
        # its `_query_rows` states the argument as the SHIPPED CALL SHAPE, not as a value.
        # `tortoise_fts_query` is the TRACING WRAPPER the capture prior goes through: it
        # forwards its caller's kwargs and adds only `leg_trace`, so injecting an opt-out
        # there would make the wrapper measure a different read than `_fts_rows` performs.
        # ⛔ ITS ENTRY IS 47 LINES DOWN, not here: the merge of `origin/main` brought this
        # registration and this branch carried its own for the same key, so a bare union
        # would have been two dict keys with the same literal (ruff F601, and the second
        # silently wins). The surviving entry is the branch's — it is main's pair PLUS `run`,
        # whose warm-up argument is recorded there — and these lines keep main's rationale
        # for registering the file at all.
        # ⛔ A MIRROR of `extractor_v2._fts_rows`, which keeps the rerank ON
        # DELIBERATELY: `sdk.py`'s `_rerank_candidates` docstring records the
        # departure verbatim — "the capture path (`extractor_v2._fts_rows`)
        # deliberately KEEPS the rerank — it has a measured recall@3 delta and
        # its own `OVERRIDES:` record — while opting out of the valid-time
        # selection. One flag for both would force that departure to choose",
        # and `test_rerank_pool`'s `runs_the_lever` names the same site. This
        # helper's entire contract is to run EXACTLY that leg, so opting it out
        # would make the recall figures it prints describe a reader `_fts_rows`
        # does not run — the mirror would be broken by the fix, not by the gate.
        # That contract is ASSERTED, not assumed, in
        # `tests/test_window_quality_m2.py::
        # test_the_mirror_reads_the_exact_lever_flags_its_reference_leg_does`:
        # this list can only say the file is ALLOWED to run the lever, so the
        # flags it actually passes are pinned against `_fts_rows`'s own.
        "tools/window_quality_m2.py": frozenset({"query"}),
        # ⛔ A MIRROR of the shipped read, on the tool's OWN stated contract:
        # "It calls the shipped read (``sdk.tortoise_fts_query``) exactly as M2
        # did — same function, same ``limit``, same sanitiser — so the arms
        # differ in the LEG, never in the query shape" (`claim_dense_leg_13732.py`
        # docstring). Both sites are ``_query``: the dense/lexical arm and the
        # designed-absence arm. Opting them out would make the paired recall
        # figures describe a reader the shipped call does not run; that is the
        # same grant `window_quality_m2.py` already holds for the same reason.
        "tools/claim_dense_leg_13732.py": frozenset({"_query"}),
        # ⛔ A MIRROR of the MCP tool's own door, and it must pass NO
        # `_rerank_candidates` kwarg BECAUSE the tool passes none (the measured
        # arm is the user's door — `sdk.tortoise_fts_query(entity_type='object',
        # limit=15)`). Opting it out would make the capstone's 0.9700@3 / 0.9800@5
        # describe a reader the tool does not run; the SHIPPED/ARMED split is the
        # arm's own `TORTOISE_FUSED_RERANK` env flag one line apart
        # (`user_search` OFF, `user_search_rerank` ON — that pair IS the +1.13 pt
        # claim), which is exactly the reason the flag exists. `_TracedSdk` is the
        # capture-prior mirror of `extractor_v2._fts_rows` and forwards the same
        # one method, so it runs the same leg.
        # `run` is the WARM-UP read (`sdk.tortoise_fts_query("warm", …)`,
        # result discarded) issued before the index waits. Registered rather
        # than disarmed because it must perform the SAME read the arms perform:
        # disarming it would make the warm read a different call from the arms it
        # precedes, and an ambient `TORTOISE_FUSED_RERANK` would then be warmed on
        # a path no arm takes.
        # ⛔ MEASURED LIMIT (review of #13778): this call runs BEFORE the per-arm
        # env loop, so the lever is OFF there (`rerank_pool_enabled()` is
        # fail-safe) and it does NOT warm the cross-encoder — no per-arm
        # `elapsed_s` figure may be read as a warm start. The registration is
        # about the CALL SHAPE, not about a warmed model.
        # #12901 FINAL batch: the seam was PRIVATISED, so the owner that mentions it is
        # now `_tortoise_fts_query` — the grant set names OWNERS, and the owner moved.
        # Both spellings are listed: the census matches either (see the walk above), so a
        # site under either name must find its grant here.
        "tools/capstone_12186_recall.py": frozenset({
            "tortoise_fts_query", "_tortoise_fts_query", "_query_rows", "run"}),
        # ⛔ A MIRROR of `extractor_v2._fts_rows`, on the tool's OWN stated contract:
        # its `query()` docstring says "EXACTLY the leg ``_fts_rows`` runs, at width
        # ``k``, returning ids", and the call passes the same TWO lever flags the
        # capture leg passes (`_record_reads=False`, `_valid_time_rerank=False`)
        # while omitting `_rerank_candidates` — because `_fts_rows` omits it: the
        # capture path KEEPS the rerank deliberately (the `OVERRIDES:` record at
        # `tortoise/extractor_v2.py:2931`, which `tortoise/sdk.py` cites beside its
        # `_rerank_candidates` parameter, and #12297's measured recall@3 delta).
        # Opting the probe out would make
        # every M5 figure describe a reader `_fts_rows` does not run — the mirror
        # would be broken by the "fix", which is the same ground
        # `window_quality_m2.py` and `capstone_12186_recall.py` hold.
        # ⛔ THE GRANT IS NOT THE WHOLE ARGUMENT, so the flags are ASSERTED against
        # the leg this row names: `tests/test_miss_rate_m5.py::
        # test_the_probe_reads_the_exact_leg_fts_rows_runs` compares the two call
        # sites keyword by keyword — a literal's VALUE, or a derived flag's
        # NORMALISED FORM, at every keyword that is not a site coordinate — and it
        # CALLS the capture leg (`_fts_rows`) with a stub sdk to pin that the read
        # receives no `_rerank_candidates` in any form at all, because a string-level
        # scan could not see a `**mapping` key, a nested `**{...}`, a subscript
        # assignment or a split literal (all four measured green against it) — and pins the
        # arithmetic that makes the grant harmless at the width the probe MEASURES —
        # `DEEP_LIMIT` (50) AT OR ABOVE `DEFAULT_FUSED_RERANK_POOL` (40) — a permutation at
        # equal width is set-preserving — and composition can
        # only move when `limit < pool` (plus the width the read would resolve if
        # the gate were armed). The only sub-pool read this file issues is the
        # width-1 WARM-UP, whose result is discarded.
        "tools/miss_rate_m5.py": frozenset({"query"}),
        # ⛔ A MIRROR of the SHIPPED DOOR, not a diagnostic with its own read: this
        # harness exists to time the real product read (``/v1/search`` and the MCP
        # ``tortoise_search``, i.e. ``TortoiseSDK._tortoise_fts_query``) and to prove
        # the fused ranking did not move — its own docstring records that the
        # ORDERED ids are the verdict, because ``rrf_fusion`` is rank-based, so a
        # scheduling change that merely permutes a page has changed the product.
        # Opting it out would measure the latency of a reader the product never runs,
        # and with the gate armed the re-picked page is exactly what the
        # rank-identity check exists to catch — the same ground
        # `window_quality_m2.py` and `tools/miss_rate_m5.py` hold.
        # ⛔ AND THE MIRROR IS ASSERTED, not declared: `tests/test_14192_read_wave_tool.py::
        # test_the_harness_reads_the_SHIPPED_door_and_names_NO_lever_flags` DERIVES the
        # allowed arguments from the `/v1/search` door's own call site
        # (`tortoise/hosted_api.py::search` hands `query`, `limit`, `read_status_out`,
        # `_truncation_out`) and reds on any other keyword — `kind`, `pool_size`,
        # `order_by`, `claim_class`, `_elevated_timeout_ms`, an opt-out like
        # `_rerank_candidates=False` — on any positional argument, on a splat, on a third
        # reach of the read as a literal/bound literal (a name COMPUTED at runtime is the
        # pin's declared residual), on the artifact's `order`/`legs` iterating the measured
        # page/trace only through a helper or after a rebind, on a second artifact dict
        # carrying both, and on an `os.environ` mutation ANYWHERE in the file through any
        # STATIC spelling the guard resolves (`os.environ`, `from os import environ` incl.
        # `as`, `getattr(os, "environ")`, and their alias/container/bound-mutator chains —
        # the composed `{"p": os.environ.pop}` slot included, #14281 review P2 — its
        # residuals are declared on `_environ_mutations`) — "which arm was in force" is not
        # recorded, see #14289.
        # Out-sinks (`read_status_out`, `_truncation_out`) are instruments, not levers: the
        # `/v1/search` door names them too.
        "tools/read_wave_latency.py": frozenset({"run"}),
    }
    seen_external: set[str] = set()
    # ⛔ ``docs/runbook`` holds the recorded-baseline diagnostics (gold-rank,
    # A/B id-set comparisons) and ``docs/evidence`` the cost-magnitude probes —
    # the same class as the ``tools/`` harnesses, so they must opt out too: their
    # output is the ORDER or the COST the lever moves, and their recorded
    # numbers predate it.
    for tree in ("tools", "battery", "benchmarks", "docs/runbook",
                 "docs/evidence"):
        for path in sorted((root / tree).rglob("*.py")):
            rel = str(path.relative_to(root))
            found = _sites(path)
            if not found:
                continue
            seen_external.add(rel)
            kept_owners = intentional_keep.get(rel, frozenset())
            for owner, opted_out in found:
                if owner in kept_owners:
                    continue
                assert opted_out, (
                    f"{rel}:{owner} reads the rerankable page and neither opts "
                    "out nor is on the intentional-keep list — with the gate "
                    "armed it re-picks which rows survive limit, undisclosed"
                    + (f" ({rel} IS on that list, but only for "
                       f"{sorted(kept_owners)}: a site outside those owners "
                       "inherits a grant with no recorded reason, so name it "
                       "or opt it out)" if kept_owners else ""))
    # Below the population on purpose (consolidation is legitimate; the NAMED
    # rows above are what makes a file's loss red).
    assert len(seen_external) >= 12, (
        f"the derived census saw only {sorted(seen_external)} — the trees moved")

    # ⛔ THE GRANT'S RECORDED REASON IS GRADED, not merely written down (#14278
    # F2). The two claims that justify `read_wave_latency.py`'s PERMANENT
    # exemption — TWO call sites, ONE of them the per-query loop carrying
    # `leg_trace` — are also a COVERAGE property: the grant names the OWNER
    # (`run`), so a third read added inside it inherits the exemption with no
    # recorded reason, and nothing else in this file would notice. (Measured
    # before this arm existed: the comment said "twice", which reads as two
    # EXECUTIONS while the loop executes once per query — a wrong count in the
    # one place the grant's reason lives.) If this reds, the reason and the tool
    # disagree: re-derive the comment above, do not delete this arm.
    harness = _ast.parse((root / "tools" / "read_wave_latency.py").read_text())
    harness_parent = {id(child): parent for parent in _ast.walk(harness)
                      for child in _ast.iter_child_nodes(parent)}

    def _enclosing(node):
        parent = harness_parent.get(id(node))
        while parent is not None:
            if isinstance(parent, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                return parent
            parent = harness_parent.get(id(parent))
        return None

    # ⛔ MENTIONS, NOT CALLS — the census's own granularity. `_sites` above counts
    # every ``ast.Attribute`` spelling of the seam (both names; the census is
    # about the SEAM), so a third read added inside `run` as a BOUND METHOD
    # (``_m = sdk._tortoise_fts_query``) or under the PUBLIC spelling is absorbed
    # by the owner-granular grant and would be invisible to a ``Call``-only
    # predicate (measured: both mutants stayed GREEN against the Call form, which
    # is the gap this line closes).
    # ⛔ AND ONLY THE MENTIONS THE GRANT IS WHAT EXEMPTS. `_sites` records a row for
    # every mention, but it asserts only on the ones that did NOT opt out —
    # opting out is the census's own sanctioned alternative ("name it or opt it
    # out"), so counting an opted-out read here would FALSE-BLOCK the correct fix
    # (measured: a `_rerank_candidates=False` read in `main()` passes the census
    # and reddened this arm — round 4's defect, mirrored).
    def _opted_out(call) -> bool:
        """`_sites`' own rule, so this arm counts exactly what the grant exempts."""
        if call is None:
            return False
        for k in call.keywords:
            if (k.arg == "_rerank_candidates"
                    and isinstance(k.value, _ast.Constant)
                    and k.value.value is False):
                return True
            if k.arg != "_search_kwargs":
                continue
            try:
                opts = _ast.literal_eval(k.value)
            except (ValueError, SyntaxError):
                continue
            if (isinstance(opts, dict)
                    and opts.get("_rerank_candidates") is False):
                return True
        return False

    seam_mentions = [n for n in _ast.walk(harness)
                     if isinstance(n, _ast.Attribute)
                     and n.attr in ("tortoise_fts_query",
                                    "_tortoise_fts_query")]
    # ⛔ RESOLVE THE ALIAS, and count only what is actually a READ. A mention can
    # feed a call (`sdk._tortoise_fts_query(…)`), reach one through a binding, a
    # container, a loop/comprehension/`with` target, a walrus or a second hop, or
    # read nothing at all (a dead binding). Those cases must NOT share a verdict:
    # the census records a row for every mention, but what the grant's reason
    # claims is READS — a dead binding is not one, and a read that opts out needs
    # no grant at all. `_seam_read_calls` follows the value flow to a fixpoint so
    # that "no fed call" means PROVEN dead (#14278 F1: a one-hop resolver read the
    # container and two-hop mutants as dead, so a third undocumented read was
    # exempted with the arm GREEN).
    # ⛔ AND "NO FED CALL" IS ONLY DEAD WHEN THE WALK COULD FOLLOW THE MENTION. A
    # mention bound by a shape the walk does not model comes back `opaque`, and it
    # must be COUNTED, not skipped: at `96b93db293` a third read spelled
    # `for _fn in (SEAM,): _fn(…)` returned `[]`, was skipped as "provably dead",
    # and the arm passed with three reads inside the granted owner (round-9
    # review, P2). Skipping an opaque mention IS the fail-open this arm exists to
    # catch, so the count row fires instead.
    # ⛔ AND AN OPT-OUT MUST NOT SWALLOW AN ADMISSION. The opt-out skip below is
    # keyed on the calls the walk FOUND; an opaque mention also escaped somewhere
    # the walk could not follow, so its opt-out cannot speak for the part it did
    # not see — `_o = SEAM; _o(…, _rerank_candidates=False); return _o` was skipped
    # as "all opted out" while `opaque` was True (round-10 review, P3). The
    # predicate is therefore `not opaque` FIRST, and the count is split into what
    # the walk RESOLVED versus what it had to COUNT unresolved, so the message
    # never claims a read it did not attribute.
    live: list = []
    seam_calls: list = []
    resolved: list = []
    unresolved: list = []
    for mention in seam_mentions:
        flow = _seam_read_calls(harness, mention)
        fed = flow.calls
        if not fed and not flow.opaque:
            continue        # PROVEN dead: nothing here can reach a call
        if not flow.opaque and all(_opted_out(c) for c in fed):
            continue        # the census's sanctioned alternative, no grant needed
        live.append(mention)
        (unresolved if flow.opaque else resolved).append(mention)
        seam_calls.extend(fed)
    # ⛔ COUNT CALL SITES, NOT MENTIONS. The object the recorded reason describes is
    # a set of READS (`:296`'s discarded warm-up, `:305`'s measured loop), and one
    # mention can feed several calls — `_w = SEAM; _w(a); _w(b)` is one mention and
    # TWO reads. While this row asserted on mentions, that spelling kept `live` at
    # 2 and the arm passed with THREE call sites inside the granted owner (round-11
    # review, P2 — the remaining half of round 9's F1-B). Distinct by identity, so a
    # mention and the call that consumes it are not double-counted.
    read_sites = {id(call) for call in seam_calls}
    assert len(read_sites) == 2, (
        f"{len(read_sites)} un-opted-out seam CALL SITE(s) of `tortoise_fts_query` "
        f"in tools/read_wave_latency.py, reached from {len(live)} mention(s) "
        f"({len(resolved)} attributed by the walk, {len(unresolved)} COUNTED "
        "because the binding could not be followed — an unresolvable mention is "
        "not a dead one), but the exemption's recorded reason says TWO (a "
        "discarded warm-up, and the measured loop) and the grant is "
        "owner-granular — a further read inside the granted owner is exempted "
        "with no recorded reason. Re-derive the comment in `intentional_keep`, or "
        "opt the new read out, which needs no grant.")
    owners = {(_enclosing(m).name if _enclosing(m) else None) for m in live}
    assert owners == {"run"}, (
        "the exempted reads are not all inside `run`, the only owner the grant "
        f"names: {sorted(owners, key=str)}")
    traced = [c for c in seam_calls
              if any(k.arg == "leg_trace" for k in c.keywords)]
    assert len(traced) == 1, (
        "the recorded reason says exactly ONE of the two reads is the MEASURED "
        f"loop and attaches `leg_trace`: {len(traced)} of {len(seam_calls)} do")
    parent = harness_parent.get(id(traced[0]))
    while parent is not None and not isinstance(parent, (_ast.For, _ast.AsyncFor)):
        parent = harness_parent.get(id(parent))
    assert parent is not None, (
        "the `leg_trace` read is not inside a `for` loop, so it does not "
        "execute once per query and the recorded reason's `1 + N` is wrong")

    # ⛔ AND THE CENSUS MUST SEE THE SEAM. A read bound as ``_search_fn=`` (the
    # volunteer pipeline does exactly that) is not an ``ast.Call`` on
    # ``tortoise_fts_query``, so the census above was BLIND to it while the
    # armed gate re-picked that pool — measured: 12 of 18 ids replaced at the
    # volunteer resolve limit. Every ``_search_fn=`` binding must carry its
    # opt-out in the adjacent ``_search_kwargs``.
    tree = _ast.parse((root / "tortoise" / "sdk.py").read_text())
    seams = [n for n in _ast.walk(tree)
             if isinstance(n, _ast.keyword) and n.arg == "_search_fn"
             and getattr(n.value, "attr", None) == "tortoise_fts_query"]
    kw_seams = [n for n in _ast.walk(tree)
                if isinstance(n, _ast.keyword) and n.arg == "_search_kwargs"]
    assert len(kw_seams) >= len(seams), (
        f"{len(seams)} ``_search_fn=tortoise_fts_query`` seam(s) but only "
        f"{len(kw_seams)} ``_search_kwargs`` — a seam without kwargs inherits "
        "``_rerank_candidates=True`` and re-picks its pool")
    for kw in kw_seams:
        # ⛔ THE VALUE, NOT THE NAME — ``{'_rerank_candidates': True}`` satisfied
        # the substring form this replaces (measured), and a seam whose kwargs
        # say True re-picks its pool while reading as an opt-out here.
        try:
            opts = _ast.literal_eval(kw.value)
        except (ValueError, SyntaxError) as exc:
            raise AssertionError(
                f"_search_kwargs at line {kw.lineno} is not a literal dict, so "
                "this census cannot prove it opts out — write it as a literal"
            ) from exc
        assert isinstance(opts, dict) and opts.get(
            "_rerank_candidates") is False, (
            "_search_kwargs at line "
            f"{kw.lineno} does not opt out of the rerank: {opts!r}")


def test_a_non_relevance_order_by_is_never_re_picked_by_the_rerank(sdk, monkeypatch):
    """The second pre-cut reorder must not touch a page another ordering owns.

    The rerank runs BEFORE ``result_ids[:limit]``, so on ``order_by="confidence"``
    — sorted by EP belief AFTER the cut — it would change which rows the belief
    sort ever sees, silently dropping high-confidence rows the un-reranked read
    returns. This pins the precondition on the CONDITION rather than on a
    comment: a positional scorer (which reverses a fused page) is injected, and
    the confidence-ordered read must be byte-identical with the gate OFF and ON,
    with the refusal disclosed. Step 5e pins the same scope for its own lever.

    Mutation check: delete ``_rr_order_by_ok`` from the two conditions and the
    ON read stops matching the OFF read (the injected scorer re-picks the page) —
    the measurement is the assertion, not the wording.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce}"
    for i in range(6):
        sdk._create_point(kind="statement", content=f"{q} order probe {i}")

    off = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=3, order_by="confidence")]
    assert len(off) == 3, f"premise: a full page must come back (off={off})"

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=3, order_by="confidence",
        leg_trace=trace)]
    assert on == off, (
        "a confidence-ordered page was re-picked before the cut: the rerank "
        f"changed membership under an ordering that only sorts survivors "
        f"(off={off} on={on})")
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False, f"the skip must be disclosed ({entry})"
    assert entry["reason"] == "order_by_not_relevance", entry
    # ⛔ `degraded` stays False on a SKIP too: in a leg trace that flag means "a
    # retrieval leg did not run", and ``read_status`` folds ANY entry's value into
    # the read's status — a True here reports a healthy confidence-ordered read as
    # DEGRADED to every hosted consumer. Pinned on every entry shape, not just the
    # `ok` one.
    assert entry["degraded"] is False, entry


def test_the_rerank_changes_WHICH_ROW_SURVIVES_THE_CUT(sdk, monkeypatch):
    """#12297 — **THE wiring point**: a candidate fusion pushed past the cut.

    This is the test that pins the placement BEHAVIOURALLY — a rerank placed after
    ``result_ids = result_ids[:limit]`` can only reorder what already survived,
    which is not what "rerank the fused candidate list" means — and it is the one
    that fails for that reason rather than for a textual order check. Every other
    test in this file reads at ``limit >= pool``, where there is NO cut and the
    placement is unobservable, so a relocation leaves them (and their assertions)
    green. ⛔ Not "the only failing test": a post-cut relocation also breaks
    ``test_the_rerank_runs_before_the_valid_time_demotion``, because the cut sits
    below step 5e — measured, 2 failed on the relocation.

    ⛔ ``pool_size`` is NOT load-bearing, and an earlier draft of this docstring
    claimed it was: the legs are asked for ``resolve_pool_size(limit * 2)``, whose
    floor is ``DEFAULT_POOL_SIZE = 120``, so a ``limit=2`` read already retrieves
    more rows than the cut and the marker is in the pool either way (measured
    identical: ``scored=6``, marker promoted, with and without this kwarg). It is
    passed to keep the intent legible; the PREMISE assertion below is what
    actually guarantees the cut is the thing under test, and it is asserted rather
    than assumed.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebrafish {nonce}"
    # ⛔ THE PREMISE MUST BE CONSTRUCTED, NOT ASSUMED. Filler passages repeat the
    # query terms THREE times each; the marker carries them once and is longer.
    # Fusion is term-frequency weighted, so the fillers outrank the marker by
    # construction — measured on this box: ONE occurrence per filler put the
    # marker FIRST in a 6-doc corpus (so the `limit=2` cut contained it and the
    # premise assertion below failed), while three occurrences place it LAST.
    # Without the repetition the test is testing which way the ranker happened to
    # break. The marker's survival must come from the SCORER.
    for i in range(5):
        sdk._create_point(kind="statement",
                         content=f"{q} {q} {q} filler passage number {i}")
    marker = sdk._create_point(
        kind="statement",
        content=f"{q} — the exact zebrafish husbandry protocol for the marker")
    marker_id = marker["id"] if isinstance(marker, dict) else marker.id

    off = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=2, pool_size=25)]
    # the UNCUT gate-off order, read before the gate is armed — the reference the
    # ``count`` assertion below contrasts against
    uncut = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=25, pool_size=25)]
    assert marker_id not in off, (
        "premise: the marker passage must fall OUTSIDE a limit=2 cut without "
        f"the rerank, or this test proves nothing (off={off})")

    class _MarkerOnly:
        """Scores only the marker; everything else is a flat zero."""

        def score(self, query, texts):
            return [1.0 if "husbandry protocol" in t else 0.0 for t in texts]

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_MarkerOnly(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=2, pool_size=25, leg_trace=trace)]
    assert marker_id in on, (
        "the rerank did not promote the marker into the cut — either it ran "
        "AFTER `result_ids[:limit]` (where it can only reorder what already "
        f"survived) or it did not run at all (on={on})")

    # ⛔ AND ``count`` MUST BE THE MOVES THE LEVER MADE. This is the ONE read in
    # this file where rows demonstrably move (the marker jumps from outside the
    # cut to inside it), and the only other assertion on ``count`` anywhere
    # asserted ZERO (the flat-scorer page, where "nothing moved" is the right
    # answer) — so ``count=int(_rr_stats.get("moved") or 0)`` could be replaced
    # by a literal ``0`` with the whole suite green (measured), leaving every
    # caller reading the trace told "the reranker ran and moved nothing" on a
    # page it fully reordered. Derived from the gate-OFF order, not a magic
    # number: the marker's slot before and after.
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is True and entry["reason"] == "ok", entry
    assert entry["count"] > 0, (
        f"the trace says the reranker moved nothing on a page it reordered "
        f"({entry}) [gate-off order: {uncut[:3]}… → gated page: {on}]")


def test_a_pool_with_no_usable_text_makes_no_scorer_call(sdk, monkeypatch):
    """A pool whose every row came back textless must not COST a model call.

    The trace is already honest without this (the ``not _scored_rows`` arm reports
    ``pool_text_empty`` — the read SUCCEEDED, there was simply nothing usable in
    it), so the guard this pins is about work, not wording: without it the read
    reaches ``rerank_pool`` with the RESOLVED width, which LOADS the cross-encoder
    and takes its lock (~6.4 s cold, brief section 6) before returning
    ``no_scorable_text`` — so the lock is the entire cost, and nothing is scored.

    ⛔ OBSERVE THE **LOAD**, NOT THE SCORE. Reverting the guard to ``if True:``
    leaves every other test in this file green, and the first version of THIS test
    stayed green too (measured): ``rerank_pool`` returns ``no_scorable_text`` at its
    own ``if not scorable`` early return BEFORE ``score()`` is ever reached, so a
    scorer-call counter cannot see the revert. The model LOAD is the guard's only
    observable effect — and it is the cost the guard exists to avoid.
    """
    _seed(sdk, 4)
    real_fetch = sdk_mod._fetch_rerank_pool_texts
    loads: list[str] = []
    calls: list[list[str]] = []

    class _Recording:
        def score(self, query, texts):
            calls.append(list(texts))
            return [0.0 for _ in texts]

    def _loader(name, **kw):
        loads.append(name)
        return _Recording(), ""

    def _all_textless(graph, entity_type, graph_label, id_field, ids):
        return {pid: "" for pid in real_fetch(graph, entity_type, graph_label,
                                              id_field, ids)}

    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _all_textless)
    monkeypatch.setattr(rerank, "load_scorer", _loader)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=5,
                           leg_trace=trace)

    assert loads == [], f"the cross-encoder was LOADED for a textless pool: {loads}"
    assert calls == [], f"the scorer ran on a textless pool: {calls}"
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False
    # The REASON is ``pool_text_empty``, not ``pool_text_read_failed``: the read
    # succeeded and returned every id, so blaming it would send a reader to the
    # graph when the data is the problem. Pinned here as well as in
    # ``test_an_ALL_empty_pool_says_nothing_scorable`` because this test is the
    # one that runs the shape, and a wrong reason is invisible to a call-count
    # assertion.
    assert entry["reason"] == "pool_text_empty", entry


def test_a_row_returned_with_NO_TEXT_is_not_scored(sdk, monkeypatch):
    """#12297 — presence in the read is not text; an EMPTY row must go unscored.

    The pool read maps a null/absent property to ``""`` (``str(row[1] or "")``),
    so a row that IS returned can still arrive with nothing to score — a stubbed
    ``:Source`` with no title, any row whose text property is null. A membership
    test (``pid in _pool_texts``) scored those rows on ``""``, and an empty
    passage OUTRANKS an unrelated real one, so the row was PROMOTED on no
    evidence. This test seeds the exact shape and asserts the disclosure says so:
    the empty row is counted unscored, not scored.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce}"
    for i in range(4):
        sdk._create_point(kind="statement", content=f"{q} real passage {i}",
                         entity_type="point")
    # ⛔ THE STUBBED ROW MUST BE RETRIEVABLE, or the test proves nothing: a point
    # created with ``content=""`` has no terms to match, so it never enters the
    # pool and the injection below never fires (measured here: ``unscored=0``, the
    # test failing at its own premise rather than at the behaviour). Its CONTENT
    # therefore matches the query so fusion retrieves it, and the injected read
    # returns ``""`` for it — which is exactly what the real read does for a row
    # whose text property is null.
    blank = sdk._create_point(kind="statement",
                             content=f"{q} stubbed passage with no text property",
                             entity_type="point")
    blank_id = blank["id"] if isinstance(blank, dict) else blank.id

    real_read = sdk_mod._fetch_rerank_pool_texts

    injected: list[bool] = []
    pool_sizes: list[int] = []

    def _with_a_blank(
        graph, entity_type, graph_label, id_field, ids,
    ):
        out = real_read(graph, entity_type, graph_label, id_field, ids)
        pool_sizes.append(len(ids))
        if blank_id in ids:
            out[blank_id] = ""          # what the read itself produces
            injected.append(True)
        return out

    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _with_a_blank)
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(rerank.FakeScorer(), monkeypatch)
    trace: list[dict] = []
    sdk._tortoise_fts_query(q, entity_type="point", limit=4, leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    # THE PREMISE, ASSERTED: the blank row reached the read. Without this the
    # assertions below pass vacuously on a pool that never contained it.
    assert injected, "the stubbed row never entered the pool, so nothing was tested"
    # EXACT, not ``>= 1`` / ``<= 4``: one row came back with no text, every other
    # row of the pool had some, and the pool is far inside the shipped width — so
    # the split is fully determined, and what this pins is that the DISCLOSURE is
    # exact. (It does not pin the sdk's truthiness filter: ``rerank_pool`` strips
    # an empty text itself, so swapping that filter for a membership test leaves
    # THIS test green — measured. The load that filter avoids is pinned by
    # ``test_a_pool_with_no_usable_text_makes_no_scorer_call``.)
    assert entry[rerank.RERANK_UNSCORED_KEY] == 1, entry
    assert entry[rerank.RERANK_SCORED_KEY] == pool_sizes[0] - 1, (
        f"scored={entry[rerank.RERANK_SCORED_KEY]} pool={pool_sizes[0]}")


class _Positional:
    """Scores the LAST row highest — the worst case for a recency page."""

    def score(self, query, texts):
        n = len(texts)
        return [float(i - n) for i in range(n)]


def test_an_all_tied_page_is_NOT_a_recency_order_in_force(sdk, monkeypatch):
    """One date for every row is not an order — the multiplier is an identity.

    ``_recency_factors`` gives a tie group the group's highest factor, so a page
    whose dates all fall in ONE group weighs every row the same and the multiplier
    reorders nothing. A sign test ("did any row carry a date") treated that as "the
    caller's order is in force", suppressed the rerank and had the trace name
    ``recency_order_applied`` — true of a page written in one day (a bulk import,
    a single capture burst) with no row reordered. The predicate is the SPREAD of
    the effective weights.
    """
    # ⛔ THE SAME `createdAt` ON EVERY ROW, through the PUBLIC write door:
    # ``create_point`` stamps a microsecond timestamp, and ``update_point``'s
    # ``**props`` is what overwrites it (``properties={…}`` is NOT a kwarg — it
    # writes a property of that literal name, which FalkorDB refuses, #7369; a
    # first version of this fixture used it and silently changed nothing).
    # `_recency_factors` groups by EQUAL sort key, so distinct timestamps would be
    # distinct dates and the spread would be real — a bulk import or a single
    # capture burst writes exactly the shape below.
    ids = []
    for i in range(5):
        pid = _id(sdk._create_point(kind="statement",
                                   content=f"zebra migration claim number {i:02d}"))
        sdk._update_point(pid, createdAt="2026-01-01")
        ids.append(pid)
    probe = {"recency_field": "createdAt", "recency_boost": 0.5}

    # PREMISE, asserted at the MECHANISM and not only at the order: a no-op ORDER
    # alone cannot tell a tied-date page from an undated one — both leave the
    # sequence untouched (all factors 1.0 vs all 0.0), so if a date-only string
    # ever stopped parsing here this test would go on passing under BOTH the
    # spread predicate and the reverted sign test, covering nothing. The factor
    # map must be NONZERO and UNIFORM: that is what makes the multiplier the
    # identity, and it is the shape the sign test misreads as an order in force.
    # ⛔ From what the GRAPH stores, not from a literal re-statement of the seed:
    # feeding `[(i, "2026-01-01")]` here would keep this test green over a pool
    # that had become genuinely UNDATED (measured — the SET reverted to a REMOVE
    # left it passing), i.e. it would stop covering the case it exists for.
    from tortoise.search_engine import _recency_factors
    stored = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.createdAt",
        params={"ids": ids}).result_set
    assert len(stored) == len(ids) and {d for _i, d in stored} == {"2026-01-01"}, (
        f"premise: every row must carry the ONE shared date (stored={stored})")
    factors = _recency_factors([(i, d) for i, d in stored])
    assert set(factors.values()) == {1.0}, factors
    # …and at the ORDER too: on this page the weighting is a proven no-op — the
    # order with recency is the order without it.
    without = [r["id"] for r in sdk._tortoise_fts_query(
        "zebra migration", entity_type="point", limit=5)]
    with_recency = [r["id"] for r in sdk._tortoise_fts_query(
        "zebra migration", entity_type="point", limit=5, **probe)]
    assert with_recency == without, (
        "premise: a single date group must reorder nothing, or this test proves "
        f"nothing (without={without} with={with_recency})")

    calls: list[list[str]] = []

    class _Recording:
        def score(self, query, texts):
            calls.append(list(texts))
            return [0.0 for _ in texts]

    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(_Recording(), monkeypatch)
    trace: list[dict] = []
    sdk._tortoise_fts_query("zebra migration", entity_type="point", limit=5,
                           leg_trace=trace, **probe)

    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is True, entry
    assert entry["reason"] == "ok", entry
    assert calls, "the lever reported that it ran but never reached the scorer"


def test_a_declared_RECENCY_order_is_never_inverted_by_the_rerank(
        sdk, monkeypatch):
    """#12297 — step 4b's recency order has the last word when a caller asks.

    ``recency_field`` + ``recency_boost`` re-rank the fused set by date BEFORE
    this block, and this block sorts on ``(-score, index)`` alone. Without a guard
    the multiplier is demoted to an exact-tie tie-break and the page comes back
    INVERTED — measured before the fix: newest→oldest with the gate off, exactly
    REVERSED with it on, and the trace said ``ok`` (no degrade, no disclosure).
    Both halves are asserted: the ORDER is unchanged, and the skip is DISCLOSED.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce}"
    ids = []
    for i in range(5):
        pt = sdk._create_point(kind="statement",
                              content=f"{q} recency probe {i}")
        pid = pt["id"] if isinstance(pt, dict) else pt.id
        # distinct increasing timestamps, so "newest" is unambiguous.
        # ⛔ `createdAt=`, not `properties={"createdAt": …}`: `update_point`'s
        # signature is `(self, id, *, is_episodic=None, **props)` — there is no
        # `properties` parameter, so that call wrote a property literally NAMED
        # `properties` (which FalkorDB refuses, #7369) and never touched
        # `createdAt`. The premise still held only because `create_point` had
        # already stamped microsecond-increasing dates, so no update was
        # exercised at all (measured — review round 11).
        sdk._update_point(pid, createdAt=f"2026-01-0{i + 1}")
        ids.append(pid)

    off = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=5, recency_field="createdAt",
        recency_boost=10.0)]
    assert off, "premise: the read must return rows at all"
    assert off == sorted(off, key=ids.index, reverse=True), (
        "premise: step 4b must order newest-first WITHOUT the rerank "
        f"(off={off})")

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk._tortoise_fts_query(
        q, entity_type="point", limit=5, recency_field="createdAt",
        recency_boost=10.0, leg_trace=trace)]
    assert on == off, (
        "the rerank inverted a caller-declared RECENCY order — it must not run "
        f"when recency_field/recency_boost are set (off={off} on={on})")
    entries = _rerank_entries(trace)
    assert entries and entries[0]["ran"] is False, (
        f"the skip must be disclosed in the leg trace, not silent ({entries})")
    assert entries[0]["reason"] == "recency_order_applied", entries[0]
    assert entries[0]["degraded"] is False, entries[0]   # a skip is not a degrade

    # ⛔ ONE ENTRY PER READ, even when the caller ALSO opted out. The two skip
    # arms are not exclusive by construction: a caller that passed
    # ``_rerank_candidates=False`` AND has a live recency spread satisfies both,
    # and before the arms were disjointed this read emitted TWO
    # ``leg="rerank"`` entries with two different reasons (measured — that is
    # exactly the combination `tools/longmem_eval/retrieve.py` passes). A
    # ``[0]``-only assertion cannot see a second entry, so the LENGTH is the
    # assertion. The opt-out is the reason the read had first: it declined the
    # rerank for its own sake, and recency never got a vote.
    trace_both: list[dict] = []
    sdk._tortoise_fts_query(
        q, entity_type="point", limit=5, recency_field="createdAt",
        recency_boost=10.0, _rerank_candidates=False, leg_trace=trace_both)
    both = _rerank_entries(trace_both)
    assert len(both) == 1, (
        f"one read emitted {len(both)} rerank entries: {both}")
    assert both[0]["ran"] is False, both[0]
    assert both[0]["reason"] == "order_owned_downstream", both[0]

    # ⛔ AND THE OTHER HALF — the one that makes the guard a GUARD. Step 4b fails
    # OPEN (its ``except`` arm leaves plain RRF order), so a recency request whose
    # read broke did not reorder anything; keying the skip on the REQUEST would
    # then suppress the rerank and report a cause that never applied. An invalid
    # property name makes 4b's own Cypher raise, which is that path, driven
    # through the public API rather than by patching an internal flag:
    # ``recency_field="bad prop"`` → ``Invalid input 'p'`` → plain RRF → the
    # rerank RUNS. Without the ``_recency_applied`` fix this asserts
    # ``recency_order_applied`` and fails.
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace2: list[dict] = []
    sdk._tortoise_fts_query(q, entity_type="point", limit=5,
                           recency_field="bad prop", recency_boost=10.0,
                           leg_trace=trace2)
    entry2 = _rerank_entries(trace2)[0]
    assert entry2["ran"] is True, (
        "a recency request whose reorder FAILED must not suppress the rerank — "
        f"that is a lever skipped for a cause that never applied ({entry2})")
    assert entry2["reason"] == "ok", entry2

    # ⛔ AND THE THIRD SHAPE — a recency field that is VALID CYPHER and carried by
    # NO row. 4b runs, reads nulls for every candidate, weighs them all 0 and
    # leaves the order untouched: the caller's recency order is NOT in force, so
    # the rerank must still run. Keying the skip on "the query returned" (or on
    # "the ids did not move") reports a cause that never bit — reviewed and
    # measured on the previous round with ``recency_field="sourcePath"``.
    trace3: list[dict] = []
    sdk._tortoise_fts_query(q, entity_type="point", limit=5,
                           recency_field="sourcePath", recency_boost=10.0,
                           leg_trace=trace3)
    entry3 = _rerank_entries(trace3)[0]
    assert entry3["ran"] is True, (
        "an all-undated pool has no recency order to protect — the rerank must "
        f"not be skipped for it ({entry3})")
    assert entry3["reason"] == "ok", entry3


def test_an_ALL_empty_pool_says_nothing_scorable(sdk, monkeypatch):
    """#12297 — "the read broke" and "nothing to score" are different facts.

    The read can SUCCEED and still return no usable text for any pooled row
    (every property null). Reporting that as ``pool_text_read_failed`` sends the
    reader to the graph when the data is the problem; the counts are identical in
    both arms, so the REASON is the only thing that distinguishes them. This arm
    was untested: the empty-text test beside it always leaves scored rows.
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce}"
    for i in range(4):
        sdk._create_point(kind="statement",
                         content=f"{q} passage that will read as blank {i}")

    def _all_blank(graph, entity_type, graph_label, id_field, ids):
        # every id comes back (the read SUCCEEDED), every text is empty
        return {pid: "" for pid in ids}

    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _all_blank)
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    sdk._tortoise_fts_query(q, entity_type="point", limit=4, leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False, entry
    assert entry["reason"] == "pool_text_empty", (
        f"an empty-but-successful read must not blame the read itself: {entry}")
    assert entry[rerank.RERANK_SCORED_KEY] == 0, entry
    candidates = _candidate_count(sdk, monkeypatch, q, 4)
    assert candidates > 0, 'premise: the read must return candidates'
    assert entry[rerank.RERANK_UNSCORED_KEY] == candidates, entry
