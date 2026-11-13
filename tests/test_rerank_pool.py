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

import inspect
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import rerank  # noqa: E402, RUF100
from tortoise import sdk as sdk_mod

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

    Without the index tie-break a stable sort of equal keys is incidental to the
    sort implementation; with it, a flat score is provably a no-op — which is
    what makes `moved == 0` readable as "the reranker had nothing to say".
    """
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")

    class _Flat:
        def score(self, query, contents):
            return [0.5] * len(contents)

    _inject(_Flat(), monkeypatch)
    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats["applied"] is True and stats["moved"] == 0


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
    assert [r["id"] for r in out] == ["b", "a"]


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
    that node's text — a wrong field would yield a confident, meaningless order."""
    assert sdk_mod._rerank_text_field("point", "Session") is None
    assert sdk_mod._fetch_rerank_pool_texts(object(), "point", "Session", "id",
                                            ["a"]) == {}


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
    # entity_type; the exclusion is asserted in test_the_source_read_carries_no…
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
    step7 = inspect.getsource(sdk_mod.TortoiseSDK.tortoise_fts_query)
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
    step7 = inspect.getsource(sdk_mod.TortoiseSDK.tortoise_fts_query)
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
    lines = inspect.getsource(sdk_mod.TortoiseSDK.tortoise_fts_query).splitlines()
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
        sdk.create_point(kind="statement",
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
    a = sdk.create_point(kind="statement", content="zebrafish husbandry protocol A")
    b = sdk.create_point(kind="statement", content="zebrafish husbandry protocol B")
    op = sdk.create_operator(
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

    def _drops_the_last(graph, entity_type, graph_label, id_field, ids):
        out = real_fetch(graph, entity_type, graph_label, id_field, ids)
        if len(out) > 1:
            out.pop(sorted(out)[-1])
        return out

    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _drops_the_last)
    _inject(_Recording(), monkeypatch)
    trace: list[dict] = []
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=5,
                           leg_trace=trace)

    entry = _rerank_entries(trace)[0]
    assert texts_seen, "the scorer never ran, so this test proves nothing"
    assert "" not in texts_seen[0], texts_seen[0]
    assert entry[rerank.RERANK_SCORED_KEY] == len(texts_seen[0])
    assert entry[rerank.RERANK_UNSCORED_KEY] >= 1


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
    off = sdk.tortoise_fts_query("zebra migration", entity_type="point",
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
    on = sdk.tortoise_fts_query("zebra migration", entity_type="point",
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
    assert (entry[rerank.RERANK_SCORED_KEY]
            + entry[rerank.RERANK_UNSCORED_KEY]) >= candidates
    # ⛔ NO ``else`` HERE: with ``scored == min(4, candidates)`` already asserted
    # above, ``scored + unscored >= candidates`` IS ``unscored >= candidates - 4``,
    # so an else-branch restating it can only fail where the line above already
    # did — a second guard that guards nothing. The exact form covers the no-cut
    # case; the ``>=`` form above covers the cut case.
    if candidates < 5:      # no cut, so the candidate set is exactly what shipped
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
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=3,
                           leg_trace=trace)
    assert _rerank_entries(trace) == []


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
        return len(sdk.tortoise_fts_query(query, entity_type="point",
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
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=4,
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
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=3,
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
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=4,
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
    src = inspect.getsource(sdk_mod.TortoiseSDK.tortoise_fts_query)
    assert src.index("rerank_pool(") < src.index("prefer_latest_ids(")


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
    """
    nonce = uuid.uuid4().hex[:8]
    q = f"zebra {nonce}"
    for i in range(6):
        sdk.create_point(kind="statement", content=f"{q} reown probe {i}")

    off = [r["id"] for r in sdk.tortoise_fts_query(
        q, entity_type="point", limit=3, _rerank_candidates=False)]
    assert len(off) == 3, f"premise: a full page must come back (off={off})"

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk.tortoise_fts_query(
        q, entity_type="point", limit=3, _rerank_candidates=False,
        leg_trace=trace)]
    assert on == off, (
        "a caller that re-owns the order still had its page re-picked by the "
        f"rerank (off={off} on={on})")
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False, f"the skip must be disclosed ({entry})"
    assert entry["reason"] == "order_owned_downstream", entry


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
        sdk.create_point(kind="statement", content=f"{q} order probe {i}")

    off = [r["id"] for r in sdk.tortoise_fts_query(
        q, entity_type="point", limit=3, order_by="confidence")]
    assert len(off) == 3, f"premise: a full page must come back (off={off})"

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk.tortoise_fts_query(
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
        sdk.create_point(kind="statement",
                         content=f"{q} {q} {q} filler passage number {i}")
    marker = sdk.create_point(
        kind="statement",
        content=f"{q} — the exact zebrafish husbandry protocol for the marker")
    marker_id = marker["id"] if isinstance(marker, dict) else marker.id

    off = [r["id"] for r in sdk.tortoise_fts_query(
        q, entity_type="point", limit=2, pool_size=25)]
    assert marker_id not in off, (
        "premise: the marker passage must fall OUTSIDE a limit=2 cut without "
        f"the rerank, or this test proves nothing (off={off})")

    class _MarkerOnly:
        """Scores only the marker; everything else is a flat zero."""

        def score(self, query, texts):
            return [1.0 if "husbandry protocol" in t else 0.0 for t in texts]

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_MarkerOnly(), monkeypatch)
    on = [r["id"] for r in sdk.tortoise_fts_query(
        q, entity_type="point", limit=2, pool_size=25)]
    assert marker_id in on, (
        "the rerank did not promote the marker into the cut — either it ran "
        "AFTER `result_ids[:limit]` (where it can only reorder what already "
        f"survived) or it did not run at all (on={on})")


def test_a_pool_with_no_usable_text_makes_no_scorer_call(sdk, monkeypatch):
    """A pool whose every row came back textless must not COST a model call.

    The trace is already honest without this (the ``not _scored_rows`` arm reports
    ``pool_text_empty`` — the read SUCCEEDED, there was simply nothing usable in
    it), so the guard this pins is about work, not wording:
    without it the read still calls ``rerank_pool(pool=0)``, ``_clamp_int`` turns
    the zero into the default width of 40, and the model is loaded, locked and run
    over twenty empty passages for a result that cannot differ. Reverting the guard
    to ``if True:`` leaves every other test in this file green, so the observation
    is made here: the scorer must never be called.
    """
    _seed(sdk, 4)
    real_fetch = sdk_mod._fetch_rerank_pool_texts
    calls: list[list[str]] = []

    class _Recording:
        def score(self, query, texts):
            calls.append(list(texts))
            return [0.0 for _ in texts]

    def _all_textless(graph, entity_type, graph_label, id_field, ids):
        return {pid: "" for pid in real_fetch(graph, entity_type, graph_label,
                                              id_field, ids)}

    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _all_textless)
    _inject(_Recording(), monkeypatch)
    trace: list[dict] = []
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=5,
                           leg_trace=trace)

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
        sdk.create_point(kind="statement", content=f"{q} real passage {i}",
                         entity_type="point")
    # ⛔ THE STUBBED ROW MUST BE RETRIEVABLE, or the test proves nothing: a point
    # created with ``content=""`` has no terms to match, so it never enters the
    # pool and the injection below never fires (measured here: ``unscored=0``, the
    # test failing at its own premise rather than at the behaviour). Its CONTENT
    # therefore matches the query so fusion retrieves it, and the injected read
    # returns ``""`` for it — which is exactly what the real read does for a row
    # whose text property is null.
    blank = sdk.create_point(kind="statement",
                             content=f"{q} stubbed passage with no text property",
                             entity_type="point")
    blank_id = blank["id"] if isinstance(blank, dict) else blank.id

    real_read = sdk_mod._fetch_rerank_pool_texts

    injected: list[bool] = []

    def _with_a_blank(
        graph, entity_type, graph_label, id_field, ids,
    ):
        out = real_read(graph, entity_type, graph_label, id_field, ids)
        if blank_id in ids:
            out[blank_id] = ""          # what the read itself produces
            injected.append(True)
        return out

    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _with_a_blank)
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(rerank.FakeScorer(), monkeypatch)
    trace: list[dict] = []
    sdk.tortoise_fts_query(q, entity_type="point", limit=4, leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    # THE PREMISE, ASSERTED: the blank row reached the read. Without this the
    # assertions below pass vacuously on a pool that never contained it.
    assert injected, "the stubbed row never entered the pool, so nothing was tested"
    assert entry[rerank.RERANK_UNSCORED_KEY] >= 1, entry
    assert entry[rerank.RERANK_SCORED_KEY] <= 4, entry


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
        pid = _id(sdk.create_point(kind="statement",
                                   content=f"zebra migration claim number {i:02d}"))
        sdk.update_point(pid, createdAt="2026-01-01")
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
    without = [r["id"] for r in sdk.tortoise_fts_query(
        "zebra migration", entity_type="point", limit=5)]
    with_recency = [r["id"] for r in sdk.tortoise_fts_query(
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
    sdk.tortoise_fts_query("zebra migration", entity_type="point", limit=5,
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
        pt = sdk.create_point(kind="statement",
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
        sdk.update_point(pid, createdAt=f"2026-01-0{i + 1}")
        ids.append(pid)

    off = [r["id"] for r in sdk.tortoise_fts_query(
        q, entity_type="point", limit=5, recency_field="createdAt",
        recency_boost=10.0)]
    assert off, "premise: the read must return rows at all"
    assert off == sorted(off, key=ids.index, reverse=True), (
        "premise: step 4b must order newest-first WITHOUT the rerank "
        f"(off={off})")

    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    on = [r["id"] for r in sdk.tortoise_fts_query(
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
    sdk.tortoise_fts_query(q, entity_type="point", limit=5,
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
    sdk.tortoise_fts_query(q, entity_type="point", limit=5,
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
        sdk.create_point(kind="statement",
                         content=f"{q} passage that will read as blank {i}")

    def _all_blank(graph, entity_type, graph_label, id_field, ids):
        # every id comes back (the read SUCCEEDED), every text is empty
        return {pid: "" for pid in ids}

    monkeypatch.setattr(sdk_mod, "_fetch_rerank_pool_texts", _all_blank)
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    _inject(_Positional(), monkeypatch)
    trace: list[dict] = []
    sdk.tortoise_fts_query(q, entity_type="point", limit=4, leg_trace=trace)
    entry = _rerank_entries(trace)[0]
    assert entry["ran"] is False, entry
    assert entry["reason"] == "pool_text_empty", (
        f"an empty-but-successful read must not blame the read itself: {entry}")
    assert entry[rerank.RERANK_SCORED_KEY] == 0, entry
    candidates = _candidate_count(sdk, monkeypatch, q, 4)
    assert candidates > 0, 'premise: the read must return candidates'
    assert entry[rerank.RERANK_UNSCORED_KEY] == candidates, entry
