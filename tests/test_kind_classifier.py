"""Kind-classifier tests (issue #1695, Task 4): the hybrid kNN → margin
gate → nearMiss rerank → batched LLM adjudication pipeline.

Stub-encoder lane (controlled one-hot fixture vectors — deterministic, no
torch) pins the core logic; a small real-model smoke subset (bge-small
cached, importorskip inside the test body + _require_model + timeout)
verifies the production path end-to-end.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_extractor_v2 import MockModel  # noqa: E402, RUF100
from tortoise.kind_classifier import (  # noqa: E402, RUF100
    UNCLASSIFIED,
    KindClassifier,
    evaluate_bits,
)
from tortoise.kind_index import KindIndex, _clear_index_cache


@pytest.fixture(autouse=True)
def _isolated_kind_caches():
    """Cross-test isolation (mirrors test_kind_index.py's _isolated_cache):
    the module-level memos must not leak built indexes / kind-specs between
    tests — test_two_default_constructions_build_index_once monkeypatches
    the production _DefaultEncoder and would otherwise leave its FakeDefault
    index memoized under the PRODUCTION key (cycle-3 P2 test hygiene)."""
    from tortoise.value_extractor import _clear_kind_spec_cache

    _clear_index_cache()
    _clear_kind_spec_cache()
    yield
    _clear_index_cache()
    _clear_kind_spec_cache()

# ── Controlled fixture: a tiny spec + one-hot keyword encoder ──────────────

FIXTURE_SPEC = {
    "dev:issue": {
        "text": "dev:issue: A tracked work item (synonyms: ticket)",
        "section": "objects",
        "description": "A tracked work item",
        "synonyms": ["ticket"],
        "examples": [],
        "nearMisses": ["dev:code"],
    },
    "dev:code": {
        "text": "dev:code: Source code that implements features",
        "section": "objects",
        "description": "Source code",
        "synonyms": [],
        "examples": [],
        "nearMisses": [],
    },
    "core:plan": {
        "text": "core:plan: A plan state (commitment-state family)",
        "section": "objects",
        "description": "A plan state",
        "synonyms": [],
        "examples": [],
        "nearMisses": [],
    },
    "core:workflow": {
        "text": "core:workflow: A reusable procedural sequence",
        "section": "objects",
        "description": "A reusable sequence",
        "synonyms": [],
        "examples": [],
        "nearMisses": [],
    },
    "core:occurrence": {
        "text": "core:occurrence: A done-state event",
        "section": "events",
        "description": "An occurrence",
        "synonyms": [],
        "examples": [],
        "nearMisses": [],
    },
    "statement": {
        "text": "statement: A durable belief or claim",
        "section": "points",
        "description": "A claim",
        "synonyms": [],
        "examples": [],
        "nearMisses": [],
    },
}

_KEYWORDS = ("ticket", "code", "plan", "workflow", "occurrence", "claim")


class KeywordEncoder:
    """Deterministic one-hot fixture encoder: dimension = keyword presence."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        out = np.zeros((len(texts), len(_KEYWORDS)))
        for i, t in enumerate(texts):
            low = str(t).lower()
            for j, kw in enumerate(_KEYWORDS):
                if kw in low:
                    out[i, j] = 1.0
        return out, False


class BoomEncoder:
    """Fail-open pin: encode() raises — must never propagate."""

    def encode(self, texts):
        raise RuntimeError("embedder down")


@pytest.fixture(scope="module")
def classifier():
    _clear_index_cache()
    clf = KindClassifier(
        encoder=KeywordEncoder(),
        index=KindIndex.build(FIXTURE_SPEC, encoder=KeywordEncoder(), persist=False),
        model=None,
        llm_tail=False,
    )
    yield clf
    _clear_index_cache()


def _items(*specs):
    return [{"id": f"i{i}", "type": t, "text": txt} for i, (t, txt) in enumerate(specs)]


class TestKnnCore:
    def test_top_knn_assigns_high_margin(self, classifier):
        out = classifier.classify_items(_items(("entity", "the ticket fix")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "dev:issue"
        assert a["mode"] == "knn"
        assert out["stats"]["assigned_knn"] == 1

    def test_below_floor_unclassified_terminal(self, classifier):
        # no keyword → zero vector → below SIM_FLOOR → unclassified sentinel
        out = classifier.classify_items(_items(("entity", "xyzzy no idea")))
        a = out["assignments"]["i0"]
        assert a["kind"] == UNCLASSIFIED
        assert a["mode"] == "unclassified"
        assert out["stats"]["below_floor"] == 1
        assert out["stats"]["unclassified"] == 1

    def test_margin_gate_boundary_high_floor(self, classifier):
        """A clear top-1 (margin >= MARGIN) above the floor assigns via kNN
        even when the top-2 share a keyword."""
        out = classifier.classify_items(_items(("entity", "the ticket ticket ticket fix")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "dev:issue"  # ticket dim dominates
        assert a["mode"] == "knn"

    def test_embedder_fail_open_fallback(self):
        clf = KindClassifier(
            encoder=BoomEncoder(),
            index=KindIndex.build(FIXTURE_SPEC, encoder=KeywordEncoder(), persist=False),
            model=None,
            llm_tail=False,
        )
        out = clf.classify_items(_items(("entity", "anything")))
        a = out["assignments"]["i0"]
        assert a["mode"] == "fallback"
        assert a["kind"] == "core:other"  # best core kind for entities
        assert out["stats"]["embedding_errors"] == 1
        assert any("embed failed" in w for w in out["warnings"])


class TestNearMissRerank:
    def test_rerank_failure_fail_open(self, monkeypatch, classifier):
        """A raising near-miss rerank must not abort the whole batch —
        per-item kNN top-1 fallback + classify_error census (FIX J)."""
        def boom(kind):
            raise RuntimeError("nearMisses table corrupt")

        monkeypatch.setattr(classifier.index, "near_misses", boom)
        out = classifier.classify_items(_items(("entity", "the ticket code")))
        assert out["stats"]["classify_errors"] == 1
        a = out["assignments"]["i0"]
        assert a["mode"] == "knn", "kNN top-1 fallback, not a batch abort"
        assert a["kind"] == "dev:code"  # raw kNN top-1 (the rerank never ran)
        assert any("rerank failed" in w for w in out["warnings"])

    def test_one_sided_near_miss_tie_breaks_to_primary(self, classifier):
        """ticket+code tie; dev:issue declares dev:code a nearMiss (one-
        sided) → the rerank prefers dev:issue (the non-decoy), mode=rerank,
        NO adjudication call burned."""
        out = classifier.classify_items(_items(("entity", "the ticket code")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "dev:issue"
        assert a["mode"] == "rerank"
        assert out["stats"]["assigned_rerank"] == 1
        assert out["stats"]["adjudication_tail"] == 0

    def test_mutual_near_miss_tie_goes_to_llm_tail(self):
        """Mutual nearMisses keep kNN order → the item lands in the
        adjudication tail (the LLM decides)."""
        spec = dict(FIXTURE_SPEC)
        spec["dev:code"] = dict(spec["dev:code"], nearMisses=["dev:issue"])
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None,
            llm_tail=False,
        )
        out = clf.classify_items(_items(("entity", "the ticket code")))
        assert out["stats"]["adjudication_tail"] == 1
        # llm_tail off → deterministic kNN top-1 (stable order: dev:code
        # sorts before dev:issue in the index)
        assert out["assignments"]["i0"]["kind"] == "dev:code"
        assert out["assignments"]["i0"]["mode"] == "knn"


class TestAdjudication:
    def _clf(self, model, spec=None):
        return KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec or FIXTURE_SPEC, encoder=KeywordEncoder(), persist=False),
            model=model,
            llm_tail=True,
        )

    def test_batched_object_wrapped_assigns(self):
        """Two tail items → ONE adjudication call; the object-wrapped
        payload parses and assigns (mode=llm). The batched LLM spend is
        recorded in stats['llm'] (the A/B cost gate sees the flag-on arm)."""

        def resp(system, user):
            return json.dumps({"i0": "core:plan", "i1": "core:workflow"})

        clf = self._clf(MockModel(resp))
        # plan+workflow ties (no nearMiss) → both land in the tail
        out = clf.classify_items(
            _items(("entity", "the plan workflow"), ("entity", "the workflow plan"))
        )
        assert out["stats"]["adjudication_calls"] == 1
        assert out["stats"]["assigned_llm"] == 2
        # the adjudication spend reached the classifier's stats
        usage = out["stats"]["llm"]
        assert usage["attempts"] == 1  # one _complete call for the batch
        assert usage["retries"] == 0
        assert usage["truncated"] is False
        assert usage["calls"] == usage["attempts"]
        assert out["assignments"]["i0"]["kind"] == "core:plan"
        assert out["assignments"]["i0"]["mode"] == "llm"
        assert out["assignments"]["i1"]["kind"] == "core:workflow"

    def test_adjudication_compact_keys_map_back(self):
        """FIX I: the adjudication payload uses COMPACT batch-position keys
        (i0, i1, ...) — raw ids like 'entities:the ticket fix#0' get
        normalized by real LLMs (spaces/colons/hashes) and would be
        closed-vocab-rejected, silently burning the kNN fallback. The
        response lookup maps the compact keys back to the real ids."""

        def resp(system, user):
            assert '"i0"' in user and '"i1"' in user
            assert "entities:the ticket fix#0" not in user, \
                "raw ids must not reach the payload"
            return json.dumps({"i0": "core:plan", "i1": "core:workflow"})

        clf = self._clf(MockModel(resp))
        items = [
            {"id": "entities:the ticket fix#0", "type": "entity",
             "text": "the plan workflow"},
            {"id": "events:went live#0", "type": "entity",
             "text": "the workflow plan"},
        ]
        out = clf.classify_items(items)
        assert out["stats"]["assigned_llm"] == 2
        assert out["stats"]["closed_vocab_rejects"] == 0
        assert out["assignments"]["entities:the ticket fix#0"]["kind"] == \
            "core:plan"
        assert out["assignments"]["entities:the ticket fix#0"]["mode"] == \
            "llm"
        assert out["assignments"]["events:went live#0"]["kind"] == \
            "core:workflow"

    def test_adjudication_failure_falls_back(self, monkeypatch):
        """BoomModel → fail-open kNN top-1 fallback AND the failed batch's
        LLM spend is still recorded (the calls were made — the A/B cost gate
        must not under-count the arm). Retry/backoff constants are pinned so
        the fail path never sleeps (cycle-3 P2 test hygiene: the duplicate
        spend test is folded here)."""
        import tortoise.extractor_v2 as v2

        class BoomModel(MockModel):
            def complete(self, *, system, user, max_tokens=None):
                raise RuntimeError("adjudicator down")

        # _complete reads these at call time — pin to zero/small so the
        # transient-classified boom raises after ONE attempt, no backoff.
        monkeypatch.setattr(v2, "_COMPLETE_RETRIES", 0)
        monkeypatch.setattr(v2, "_BACKOFF_BASE_S", 0.01)
        monkeypatch.setattr(v2, "_BACKOFF_CAP_S", 0.01)
        clf = self._clf(BoomModel([]))
        out = clf.classify_items(_items(("entity", "the plan workflow")))
        assert out["stats"]["classify_errors"] == 1
        assert out["assignments"]["i0"]["mode"] == "knn"
        usage = out["stats"]["llm"]
        assert usage["attempts"] == 1, "failed calls are still LLM spend"
        assert usage["retries"] == 0

    def test_bare_array_response_rejected(self):
        """The adjudication contract is object-wrapped: a bare-array
        response is a parse-contract violation → fail-open kNN fallback."""

        def resp(system, user):
            return '["core:plan", "statement"]'

        clf = self._clf(MockModel(resp))
        out = clf.classify_items(_items(("entity", "the plan workflow")))
        assert out["stats"]["classify_errors"] == 1
        assert out["assignments"]["i0"]["mode"] == "knn"

    def test_closed_vocab_reject_non_candidate_pick(self):
        """The adjudicator's pick must come from the item's candidate list
        (closed-vocab gate vs the index) — a minted pick falls back."""

        def resp(system, user):
            return json.dumps({"i0": "worktree"})  # not a candidate

        clf = self._clf(MockModel(resp))
        out = clf.classify_items(_items(("entity", "the plan workflow")))
        assert out["stats"]["closed_vocab_rejects"] == 1
        assert out["assignments"]["i0"]["mode"] == "knn"
        assert any("not a candidate" in w for w in out["warnings"])


class TestClassifierConstruction:
    """The default-construction index path (final-review P1): production
    KindClassifier() builds/persists/loads the content-addressed index ONCE
    per spec (memo+persist); injected stub encoders never touch the
    production memo (their vector space differs)."""

    def test_two_default_constructions_build_index_once(self, monkeypatch, tmp_path):
        """Two production-default KindClassifier() constructions → exactly
        ONE index build (load-then-build with persist on the default path;
        the second construction loads the memoized index)."""
        import tortoise.kind_index as ki
        from tortoise.kind_classifier import KindClassifier

        calls = {"n": 0}

        class FakeDefault:
            def encode(self, texts):
                calls["n"] += 1
                rng = np.random.default_rng(1)
                return rng.standard_normal((len(texts), 4)), False

        monkeypatch.setattr(ki, "_DefaultEncoder", FakeDefault)
        monkeypatch.setattr(ki, "DEFAULT_CACHE_DIR", tmp_path)
        clf1 = KindClassifier(model=None, llm_tail=False)
        clf2 = KindClassifier(model=None, llm_tail=False)
        assert clf1.index is not None and clf2.index is not None
        assert calls["n"] == 1, "one default build for two constructions"
        assert clf1.index.kind_names == clf2.index.kind_names

    def test_stub_encoder_classifier_build_never_memoized(self):
        """An injected stub encoder builds via the stub path — the
        production memo is untouched (a stub build must never shadow the
        production index, and vice versa)."""
        import tortoise.kind_index as ki
        from tortoise.kind_classifier import KindClassifier

        _clear_index_cache()
        clf = KindClassifier(encoder=KeywordEncoder(), model=None, llm_tail=False)
        assert clf.index is not None and len(clf.index) > 0
        with ki._INDEX_LOCK:
            assert ki._INDEX_CACHE == {}, "stub builds must never touch the production memo"

    def test_degraded_build_never_memoized_recovery_rebuilds_good(
            self, monkeypatch, tmp_path):
        """FIX-N recovery: embedder down → the classifier's degraded
        in-process build must NOT stay in the process memo; once the
        embedder is back, a fresh classifier construction produces a
        NON-degraded index (previously the degraded memo entry would have
        dimension-mismatched every item and failed it via the fail-open
        fallback for the process lifetime)."""
        import tortoise.kind_index as ki
        from tortoise.embeddings import EmbeddingModel
        from tortoise.kind_classifier import KindClassifier

        state = {"up": False}

        class FakeDefault:
            def encode(self, texts):
                rng = np.random.default_rng(1)
                return rng.standard_normal((len(texts), 4)), not state["up"]

        def fake_get():
            return object() if state["up"] else None

        monkeypatch.setattr(ki, "_DefaultEncoder", FakeDefault)
        monkeypatch.setattr(ki, "DEFAULT_CACHE_DIR", tmp_path)
        monkeypatch.setattr(EmbeddingModel, "get", staticmethod(fake_get))

        # embedder DOWN: first construction builds degraded in-process
        state["up"] = False
        clf_down = KindClassifier(model=None, llm_tail=False)
        assert clf_down.index.degraded is True
        with ki._INDEX_LOCK:
            assert ki._INDEX_CACHE == {}, \
                "the degraded in-process build must not stay in the memo"

        # embedder UP: a fresh construction must NOT memo-hit the degraded
        # build — it loads/rebuilds a NON-degraded index
        state["up"] = True
        clf = KindClassifier(model=None, llm_tail=False)
        assert clf.index.degraded is False, \
            "recovery must rebuild/load a good index"
        out = clf.classify_items(
            [{"id": "i0", "type": "entity", "text": "the ticket fix"}])
        assert out["stats"]["embedding_errors"] == 0
        assert out["stats"]["classify_errors"] == 0, \
            "no dimension mismatch — the item lane and index agree"
        assert out["assignments"]["i0"]["mode"] in ("knn", "rerank",
                                                       "unclassified")


class TestTypeRestriction:
    def test_events_restricted_to_event_kinds(self, classifier):
        """An event item whose nearest kind is an object kind is restricted
        to event kinds — the nearest EVENT kind wins."""
        out = classifier.classify_items(
            _items(("event", "the ticket code plan workflow occurrence"))
        )
        a = out["assignments"]["i0"]
        assert a["kind"] == "core:occurrence"
        assert a["mode"] == "knn"

    def test_points_restricted_to_point_kinds(self, classifier):
        out = classifier.classify_items(_items(("point", "the plan claim")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "statement"
        assert a["mode"] == "knn"

    def test_entities_see_objects_and_subjects(self, classifier):
        out = classifier.classify_items(_items(("entity", "the plan")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "core:plan"
        assert a["mode"] == "knn"


class TestDeterminism:
    def test_a_prime_shuffle_invariance(self):
        """The classifier is label-order invariant: two indexes built from
        spec dicts with different INSERTION orders assign identically (the
        index sorts the kind names; the render's label order never reaches
        the classifier)."""
        spec_a = dict(FIXTURE_SPEC)
        spec_b = {k: spec_a[k] for k in sorted(spec_a, reverse=True)}
        enc = KeywordEncoder()
        idx_a = KindIndex.build(spec_a, encoder=enc, persist=False)
        idx_b = KindIndex.build(spec_b, encoder=enc, persist=False)
        clf_a = KindClassifier(encoder=enc, index=idx_a, model=None, llm_tail=False)
        clf_b = KindClassifier(encoder=enc, index=idx_b, model=None, llm_tail=False)
        items = _items(("entity", "the ticket fix"), ("point", "the claim"))
        out_a = clf_a.classify_items(items)
        out_b = clf_b.classify_items(items)
        assert out_a["assignments"] == out_b["assignments"]

    def test_repeated_classification_identical(self, classifier):
        items = _items(("entity", "the ticket code"))
        r1 = classifier.classify_items(items)
        r2 = classifier.classify_items(items)
        assert r1["assignments"] == r2["assignments"]


class TestEvalBits:
    def test_evaluate_bits_reports_metrics(self):
        gold = [
            {
                "id": "b1",
                "content": "the ticket fix",
                "type": "entity",
                "gold_kind": "dev:issue",
                "split": "calibrate",
                "provenance": {"source": "t", "author": "a"},
            },
            {
                "id": "b2",
                "content": "the plan workflow",
                "type": "entity",
                "gold_kind": "core:workflow",
                "split": "holdout",
                "provenance": {"source": "t", "author": "a"},
            },
        ]
        result = evaluate_bits(gold, arm="compact", encoder=KeywordEncoder())
        assert result["arm"] == "compact"
        assert result["bits"] == 2
        assert 0.0 <= result["precision"] <= 1.0
        assert "adjudication_tail_rate" in result
        assert result["pack_stratum"] == {"bits": 1, "misses": 0}
        assert result["sentinel_rate"] == 0.0
        assert result["bare_confusions"] == 0

    def test_evaluate_bits_bare_name_confusion_not_exact(self):
        """FIX H: a same-bare-different-namespace match (dev:issue vs
        pm:issue) is a CONFUSION, not a hit — counting it in `exact` would
        inflate precision when the classifier picks the right bare kind in
        the wrong namespace."""
        gold = [
            {
                "id": "b1",
                "content": "the ticket fix",
                "type": "entity",
                "gold_kind": "pm:issue",  # classifier assigns dev:issue
                "split": "calibrate",
                "provenance": {"source": "t", "author": "a"},
            },
        ]
        result = evaluate_bits(gold, arm="compact", encoder=KeywordEncoder())
        assert result["bare_confusions"] == 1
        assert result["precision"] == 0.0, \
            "a bare-only match must not count as exact"
        assert result["pack_stratum"]["misses"] == 0  # bare still matches


# ── Real-model smoke subset (bge-small cached; skips otherwise) ────────────


def _require_model():
    """Skip unless the real embedder is cached (mirrors test_cross_lens)."""
    import os

    from tortoise.embeddings import EmbeddingModel

    cache = os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5")
    if not os.path.isdir(cache):
        pytest.skip("bge-small-en-v1.5 not cached — skipping real-model test")
    if EmbeddingModel.get() is None:
        pytest.skip("bge-small-en-v1.5 unavailable — model load timed out")


class TestRealModelSmoke:
    @pytest.mark.timeout(600)
    def test_classifier_runs_with_real_index_and_eval(self):
        pytest.importorskip("sentence_transformers")
        _require_model()
        from tortoise.value_extractor import compile_kind_index_spec

        spec = compile_kind_index_spec()
        clf = KindClassifier(index=KindIndex.build(spec, persist=False), model=None, llm_tail=False)
        out = clf.classify_items(
            [
                {"id": "r1", "type": "entity", "text": "Tortoise"},
                {"id": "r2", "type": "entity", "text": "the draft-filter fix"},
                {
                    "id": "r3",
                    "type": "point",
                    "text": "single-flash with granularity is the working path",
                },
            ]
        )
        assert len(out["assignments"]) == 3
        for a in out["assignments"].values():
            assert a["kind"], "every item must receive a kind"
            assert a["mode"] in ("knn", "rerank", "unclassified", "fallback")

    @pytest.mark.timeout(600)
    def test_real_index_smoke_mini_gold_eval(self):
        pytest.importorskip("sentence_transformers")
        _require_model()
        from tools.kind_eval import load_gold

        gold = load_gold("tests/fixtures/kinds_gold.mini.jsonl")
        result = evaluate_bits(gold, arm="compact")
        assert result["bits"] == len(gold)
        assert "precision" in result and "top5_hit_rate" in result
