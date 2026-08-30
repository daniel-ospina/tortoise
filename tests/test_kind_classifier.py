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
import time
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

_KEYWORDS = ("ticket", "code", "plan", "workflow", "occurrence", "claim",
             "rule", "standard")


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
        """A dominant top-1 keyword (margin >= MARGIN) above the floor
        assigns via kNN — the margin gate wins over the nearMiss rerank and
        unclassified fallbacks. (Reinstated 2026-08-30 after a code-review
        cycle flagged its removal as unexplained; passes unchanged on the
        post-#2030 classifier.)"""
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

    def test_one_sided_near_miss_tie_breaks_to_primary(self, classifier, monkeypatch):
        """ticket+code tie; dev:issue declares dev:code a nearMiss (one-
        sided) → the rerank prefers dev:issue (the non-decoy), mode=rerank,
        NO adjudication call burned. Also pins the NEGATIVE case for #2030:
        a rerank involving NO retry-declared kind must NOT increment
        near_miss_retries. The registry is stubbed warn-only so the pin is
        manifest-content-independent (a future pack adding retry on
        issue/code must not flip this classifier-behavior test red)."""
        import types
        from tortoise.pack_registry import PackManifest

        def warn_only(ns: str):
            return PackManifest(
                namespace=ns, name=ns, version="0.1.0", tier="free",
                description=ns, path=Path("."),
                extraction={"enforcement": {
                    "default": "warn", "kinds": {}, "relations": {}, "chains": {},
                }},
            )

        packs = {"dev": warn_only("dev"), "pm": warn_only("pm")}
        stub = types.SimpleNamespace(packs=packs, get_pack=lambda ns: packs.get(ns))
        monkeypatch.setattr("tortoise.domain_loader._get_registry", lambda: stub)
        out = classifier.classify_items(_items(("entity", "the ticket code")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "dev:issue"
        assert a["mode"] == "rerank"
        assert out["stats"]["assigned_rerank"] == 1
        assert out["stats"]["adjudication_tail"] == 0
        assert out["stats"]["near_miss_retries"] == 0  # no retry kind involved

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


class TestNearMissRetrySignal:
    """#2030 — the enforcement seam on the classifier near-miss path: a
    retry-declared kind that is rerank-chosen records near_miss_retries
    (the extractor's M3 loop bounds the actual re-attempt; this marks the
    near-miss for it). The hook passes the NAMESPACED index kind; the seam
    resolves it against the declaring pack. Ambient dependency: the hook's
    resolve_enforcement reads the REAL registry — repo packs/ ships
    agent-ops extraction.enforcement.kinds rule: retry, and the conftest
    _packs_env_isolation autouse guarantees TORTOISE_PACKS_DIR is cleared
    so default_packs_dir() resolves to repo packs/."""

    def _require_agent_ops_retry(self):
        """Loud precondition for the tests whose near_miss_retries
        expectations derive from the AMBIENT manifest (agent-ops extraction
        enforcement.kinds rule: retry) + the near-miss structure in the
        real compiled index. A future manifest edit that removes either
        fails with a clear message instead of an opaque stat mismatch."""
        from tortoise.enforcement import resolve_enforcement
        from tortoise.value_extractor import compile_kind_index_spec
        assert resolve_enforcement(kind="agent-ops:rule") == "retry", \
            "TestNearMissRetrySignal needs the installed agent-ops pack to " \
            "declare extraction.enforcement.kinds rule: retry"
        spec = compile_kind_index_spec()
        assert "agent-ops:rule" in spec and "core:standard" in spec, \
            "TestNearMissRetrySignal needs the rule↔standard near-miss " \
            "pair in the compiled kind index"

    def test_rerank_chosen_agent_ops_rule_records_near_miss_retry(self):
        """rule↔standard tie (one-sided nearMiss — rule declares standard)
        → rerank prefers the non-decoy agent-ops:rule → the seam resolves
        retry → near_miss_retries == 1. RED on pre-fix code: the hook sees
        warn (bare-keyed lookup misses agent-ops:rule) → stat absent.
        Production-faithful pair: the bare ref 'standard' resolves through
        near_misses()' any-namespace fallback to core:standard (there is no
        agent-ops:standard kind — same as the real compiled index)."""
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [],
            # bare nearMiss ref, production-faithful (the real manifest
            # declares nearMisses: [standard]); near_misses() resolves it
            # through the any-namespace fallback to core:standard.
            "nearMisses": ["standard"],
        }
        spec["core:standard"] = {
            "text": "core:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        self._require_agent_ops_retry()
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        out = clf.classify_items(_items(("entity", "the rule standard")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "agent-ops:rule"      # rerank-chosen (indicator 3)
        assert a["mode"] == "rerank"
        assert out["stats"]["assigned_rerank"] == 1
        assert out["stats"].get("near_miss_retries") == 1  # seam fired

    def test_classifier_never_re_attempts_encode(self):
        """The classifier is NOT the retry consumer: one classify pass = one
        encode pass (encoder.calls == 1 for a 1-item batch) — the bounded
        re-attempt lives in the extractor's M3 transient-completion loop;
        near_miss_retries is census-only indicator-3 telemetry (#2030).
        Behavioral pin: a classifier-side re-attempt loop would re-encode."""
        spec = dict(FIXTURE_SPEC)
        enc = KeywordEncoder()
        clf = KindClassifier(
            encoder=enc,
            index=KindIndex.build(spec, encoder=enc, persist=False),
            model=None, llm_tail=False,
        )
        enc.calls = 0  # index build consumed encodes; measure the classify pass
        out = clf.classify_items(_items(("entity", "the ticket")))
        assert out["assignments"]["i0"]["kind"] == "dev:issue"
        assert enc.calls == 1  # exactly one encode pass — no re-attempt loop

    def test_near_miss_retries_counts_per_item(self):
        """near_miss_retries is a per-ITEM census counter: a 2-item batch of
        rerank-chosen agent-ops:rule records 2 (not a boolean 0/1) — pins
        the counting semantics for the downstream telemetry contract."""
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": ["standard"],
        }
        spec["core:standard"] = {
            "text": "core:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        self._require_agent_ops_retry()
        out = clf.classify_items(
            _items(("entity", "the rule standard"), ("entity", "the standard rule"))
        )
        assert all(a["mode"] == "rerank" for a in out["assignments"].values())
        assert all(a["kind"] == "agent-ops:rule" for a in out["assignments"].values())
        assert out["stats"]["near_miss_retries"] == 2  # per-item counter, not boolean

    def test_near_miss_retries_mixed_batch_counts_per_item(self):
        """Batch-scoped semantics: one rerank-chosen retry item + one kNN
        item in a SINGLE classify_items call → exactly 1 (per-item
        increment, not per-batch or per-retry-kind)."""
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": ["standard"],
        }
        spec["core:standard"] = {
            "text": "core:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        self._require_agent_ops_retry()
        out = clf.classify_items(
            _items(("entity", "the rule standard"), ("entity", "the rule"))
        )
        assert out["assignments"]["i0"]["mode"] == "rerank"
        assert out["assignments"]["i1"]["mode"] == "knn"
        assert out["stats"]["near_miss_retries"] == 1  # per-item, not per-batch

    def test_retry_kind_knn_assignment_does_not_record(self):
        """The hook records ONLY on the near-miss RERANK branch (#2030): an
        unambiguous (high-margin) kNN assignment of the retry-declared kind
        must NOT increment near_miss_retries — pins the recording scope
        boundary."""
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": ["standard"],
        }
        spec["core:standard"] = {
            "text": "core:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        self._require_agent_ops_retry()
        out = clf.classify_items(_items(("entity", "the rule")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "agent-ops:rule"
        assert a["mode"] == "knn"  # margin 1.0 >= MARGIN — no rerank
        assert out["stats"]["near_miss_retries"] == 0  # rerank-branch only

    def test_decoy_retry_declared_near_miss_records_retry(self):
        """Disjunct 2 of the hook: the RERANK-CHOSEN kind is warn
        (core:standard) but its resolved near-miss set contains a
        retry-declared kind (agent-ops:rule as the decoy) — the signal
        still records. This routes through the #2030 namespaced resolution
        on the near-miss partner, the path a regression could silently
        under-count."""
        spec = dict(FIXTURE_SPEC)
        # Tie order is ALPHABETICAL (KindIndex.kind_names = sorted(spec)):
        # agent-ops:rule sorts before core:standard, so top[0] is rule —
        # the rerank flips to core:standard via the one-sided nearMiss
        # (a_is_decoy: rule ∈ near_misses(standard), standard ∉
        # near_misses(rule)). Order-independent by construction.
        spec["core:standard"] = {
            "text": "core:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": ["rule"],
        }
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        self._require_agent_ops_retry()
        out = clf.classify_items(_items(("entity", "the standard rule")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "core:standard"  # chosen (warn)
        assert a["mode"] == "rerank"
        assert out["stats"]["assigned_rerank"] == 1
        # fired via the near-miss disjunct: near_misses(chosen) → {rule} → retry
        assert out["stats"].get("near_miss_retries") == 1

    def test_mutual_near_miss_tie_goes_to_tail_without_recording(self):
        """Recording scope boundary, third branch: a MUTUAL nearMiss pair
        involving a retry-declared kind routes to the adjudication tail
        (reranked is None) — the hook fires on the rerank branch only, so
        near_miss_retries stays 0 even though agent-ops:rule is involved."""
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": ["standard"],
        }
        spec["core:standard"] = {
            "text": "core:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": ["rule"],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        out = clf.classify_items(_items(("entity", "the rule standard")))
        assert out["stats"]["adjudication_tail"] == 1
        assert out["stats"]["near_miss_retries"] == 0  # rerank branch only
        assert out["assignments"]["i0"]["kind"] == "agent-ops:rule"  # knn top-1 fallback

    def test_hook_resolve_raise_is_fail_open(self, monkeypatch):
        """The hook's own except (kind_classifier.py:287-290) is fail-open:
        a raising resolve_enforcement must not abort the batch. Patch the
        module the hook imports from at call time (from tortoise.enforcement
        import resolve_enforcement INSIDE the try) — tortoise.kind_classifier
        has no such attribute. A call counter proves the seam was actually
        hit (distinguishes "raised and swallowed" from "never invoked")."""
        calls: list[str] = []

        def raiser(*_a, **_k):
            calls.append("resolve")
            raise RuntimeError("seam down")

        monkeypatch.setattr("tortoise.enforcement.resolve_enforcement", raiser)
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": ["standard"],
        }
        spec["agent-ops:standard"] = {
            "text": "agent-ops:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        out = clf.classify_items(_items(("entity", "the rule standard")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "agent-ops:rule"  # still classified via rerank
        assert a["mode"] == "rerank"
        assert out["stats"]["classify_errors"] == 0
        assert calls, "the hook must actually call the seam (fail-open pin)"
        # robust to BOTH pre-init absence and post-init zero (Task 3 adds
        # the always-present key) — never assert absence.
        assert out["stats"].get("near_miss_retries", 0) == 0


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

    def test_deadline_kill_forwards_deadline_aborts(self, monkeypatch):
        """#1787 (PR #1811): a deadline-killed adjudication call forwards
        the extractor's deadline_aborts counter into the session llm rollup.
        The model SLEEPS past the (scaled-down) per-attempt deadline, so the
        _call_once deadline-kill seam is the ONLY reason the call fails — it
        increments batch_stats["deadline_aborts"], the finally block forwards
        it to usage, and classify_items lands usage in out["stats"]["llm"].
        Fail-open kNN top-1 fallback + classify_error census still fire
        (same shape as the BoomModel test).

        Sleep (1.0s) is 20x the pinned 0.05s deadline — the kill provably
        fires, so the assertion cannot be vacuous."""
        import tortoise.extractor_v2 as v2

        class SlowModel(MockModel):
            def complete(self, *, system, user, max_tokens=None):
                # outlast the 0.05s join-timeout — the seam only fires when
                # the call is still alive at t.join(timeout=deadline_s).
                time.sleep(1.0)
                return json.dumps({"i0": "core:plan"})

        # _complete reads these at call time — pin to zero/small so the
        # deadline-killed attempt raises after ONE call, no backoff (same
        # hook as the BoomModel test).
        monkeypatch.setattr(v2, "_COMPLETE_RETRIES", 0)
        monkeypatch.setattr(v2, "_BACKOFF_BASE_S", 0.01)
        monkeypatch.setattr(v2, "_BACKOFF_CAP_S", 0.01)
        # the classifier's _complete_parsed passes no explicit deadline →
        # _complete computes _scaled_deadline(600, max_tokens); pin it to
        # ~0.05s so the 1.0s sleep is a guaranteed kill, not a race.
        monkeypatch.setattr(v2, "_scaled_deadline", lambda base, mt: 0.05)
        clf = self._clf(SlowModel([]))
        out = clf.classify_items(_items(("entity", "the plan workflow")))
        assert out["stats"]["classify_errors"] == 1
        assert out["assignments"]["i0"]["mode"] == "knn", \
            "deadline kill is fail-open — kNN top-1 fallback, not a batch abort"
        usage = out["stats"]["llm"]
        assert usage["deadline_aborts"] >= 1, \
            "the deadline-kill counter must reach the llm rollup (#1787 P2-L)"
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
        # #1784 cycle-3: the degraded branch fires when EmbeddingModel.get()
        # is None (real embedder absent) — it rebuilds persist=False and
        # EVICTS the memo (FIX-N: degraded builds never pin the cache), so
        # two constructions build twice. This test promises the PRODUCTION
        # path (load-then-build with memo+persist), so the embedder must be
        # present: patch get() to return a non-None sentinel.
        from tortoise import embeddings as emb_mod
        monkeypatch.setattr(emb_mod.EmbeddingModel, "get",
                            lambda *a, **k: object())
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
