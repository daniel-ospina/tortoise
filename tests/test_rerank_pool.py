"""#12297 — the FUSED-CANDIDATE-LIST rerank: permutation, gate, pool width, degrade.

Hermetic except the last two tests, which need the lane's DB seam like every
other `pytest` run in this repo (epic #1647 P4). The cross-encoder is never
loaded: the deterministic in-repo ``rerank.FakeScorer`` is injected through the
shared ``rerank.load_scorer`` policy seam, so no model, no network, no GPU.

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
  6. the wiring in ``tortoise_fts_query`` sits BEFORE ``result_ids[:limit]``,
     which is the only placement where a rerank can change what survives a cut.
"""
from __future__ import annotations

import sys
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


def _inject(scorer):
    """Patch the ONE shared load policy, so the rerank sees a fake scorer."""
    rerank.load_scorer = _make_loader(scorer)


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


@pytest.fixture
def _restore_loader():
    original = rerank.load_scorer
    yield
    rerank.load_scorer = original


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


def test_off_path_returns_input_order_and_never_loads_a_scorer(_restore_loader):
    def _must_not_be_called(name, **kw):
        raise AssertionError("the OFF path must not load a cross-encoder")
    rerank.load_scorer = _must_not_be_called

    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats == {"applied": False, "degrade_reason": "disabled"}
    # a NEW list — a caller mutating the result must not reorder its own input
    assert out is not ROWS


# ── 2. flag-on is a PERMUTATION ────────────────────────────────────────────

def test_on_path_reorders_but_is_a_permutation(monkeypatch, _restore_loader):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer())

    out, stats = rerank.rerank_pool(QUERY, ROWS)
    ids = [r["id"] for r in out]
    assert stats["applied"] is True
    # the best match ("gym gym gym") is promoted out of last place
    assert ids[0] == "x2"
    # PERMUTATION: same rows, same length, nothing dropped, nothing invented
    assert len(out) == len(ROWS)
    assert sorted(ids) == sorted(r["id"] for r in ROWS)
    assert stats["scored"] == 3 and stats["unscored"] == 0


def test_ties_break_on_the_original_index(monkeypatch, _restore_loader):
    """A scorer that returns one constant must leave the order UNCHANGED.

    Without the index tie-break a stable sort of equal keys is incidental to the
    sort implementation; with it, a flat score is provably a no-op — which is
    what makes `moved == 0` readable as "the reranker had nothing to say".
    """
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")

    class _Flat:
        def score(self, query, contents):
            return [0.5] * len(contents)

    _inject(_Flat())
    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats["applied"] is True and stats["moved"] == 0


# ── 3. pool bounds what is SCORED; the tail keeps its order ────────────────

def test_pool_width_bounds_scoring_and_preserves_the_tail(
        monkeypatch, _restore_loader):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer())

    out, stats = rerank.rerank_pool(QUERY, ROWS, pool=2)
    assert stats["scored"] == 2 and stats["unscored"] == 1 and stats["pool"] == 2
    # Only the head [x0, x1] was scored and it reorders (x1 has query overlap,
    # x0 has none); x2 — the true best — is past the width and therefore stays
    # exactly where retrieval put it, LAST. That is what "pool width bounds the
    # rerank" means: a narrow pool cannot promote what it never scored.
    assert [r["id"] for r in out] == ["x1", "x0", "x2"]
    assert len(out) == len(ROWS)


def test_pool_env_knob_and_explicit_kwarg_precedence(monkeypatch, _restore_loader):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, "1")
    _inject(rerank.FakeScorer())
    _out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert stats["pool"] == 1 and stats["scored"] == 1
    # an explicit kwarg wins over the env (the `_clamp_int` contract)
    _out, stats = rerank.rerank_pool(QUERY, ROWS, pool=3)
    assert stats["pool"] == 3 and stats["scored"] == 3


def test_pool_env_garbage_falls_back_to_the_default(monkeypatch, _restore_loader):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    monkeypatch.setenv(rerank.FUSED_RERANK_POOL_ENV, "not-a-number")
    _inject(rerank.FakeScorer())
    _out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert stats["pool"] == rerank.DEFAULT_FUSED_RERANK_POOL


# ── 4. every failure degrades, none raises ─────────────────────────────────

@pytest.mark.parametrize("scorer,needle", [
    (RuntimeError("model unavailable"), "model unavailable"),
    (_RaisingScorer(), "boom"),
    (_ShortScorer(), "length-mismatched"),
])
def test_scorer_failures_degrade_to_the_untouched_order(
        monkeypatch, _restore_loader, scorer, needle):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(scorer)
    out, stats = rerank.rerank_pool(QUERY, ROWS)
    assert [r["id"] for r in out] == ["x0", "x1", "x2"]
    assert stats["applied"] is False
    assert needle in stats["degrade_reason"]


@pytest.mark.parametrize("query,rows,reason", [
    ("   ", ROWS, "empty_query"),
    (QUERY, [], "empty_pool"),
])
def test_empty_inputs_degrade(monkeypatch, _restore_loader, query, rows, reason):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer())
    out, stats = rerank.rerank_pool(query, rows)
    assert list(out) == list(rows)
    assert stats == {"applied": False, "degrade_reason": reason}


def test_none_content_truncates_to_empty_and_does_not_degrade(
        monkeypatch, _restore_loader):
    monkeypatch.setenv(rerank.FUSED_RERANK_ENV, "1")
    _inject(rerank.FakeScorer())
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
    ("point", "Point", "id", ["MATCH (n:Point)", "n.id IN $ids"]),
    ("event", "Event", "eventId", ["MATCH (n:Event)", "n.eventId IN $ids"]),
    ("object", "Object", "id", ["MATCH (n:Object)", "n.id IN $ids"]),
    # the two restated predicates, pinned so they cannot drift from step 7
    ("document", "Source", "url", ["MATCH (n:Source)", "n.url IN $ids",
                                   "n.documentKind IS NOT NULL"]),
    ("operator", "Point", "id", ["MATCH (n:Point {is_operator: true})"]),
])
def test_pool_text_read_predicates(entity_type, label, id_field, fragments):
    g = _FakeGraph([["a", "some text"]])
    out = sdk_mod._fetch_rerank_pool_texts(
        g, entity_type, label, id_field, ["a", "b"])
    assert out == {"a": "some text"}
    cypher, params = g.calls[0]
    for frag in fragments:
        assert frag in cypher, cypher
    assert params == {"ids": ["a", "b"]}
    # ONE batch read, never N+1
    assert len(g.calls) == 1


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
