"""TEMPORARY review probe (#10675 cycle 5) — deleted before handoff."""
from __future__ import annotations

import copy
import json

import pytest

from tortoise.sdk import TortoiseSDK
from tools.longmem_eval.retrieve import (
    EVAL_RETRIEVAL_BUDGET_MS,
    retrieve_for_question,
)
from tests.test_session_reinjection import (
    _degradations,
    _fresh_uri,
    _leg_mix,
    _question,
    _require_one_leg_mix,
    _seed_graph,
)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))
    for k in ("TORTOISE_LME_SESSION_REINJECTION", "TORTOISE_LME_CONTEXT_ITEMS",
              "TORTOISE_LME_FLOOR_TERM", "TORTOISE_LME_GUARD_PER_SESSION_CAP",
              "TORTOISE_LME_COVERAGE_LOOP"):
        monkeypatch.delenv(k, raising=False)


def _read(sdk, q, **kw):
    return retrieve_for_question(
        sdk, q, ks=(5,), top_k=10, pool_size=60,
        retrieval_budget_ms=EVAL_RETRIEVAL_BUDGET_MS, **kw)


def test_probe_real_traces(monkeypatch, capsys):
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        _seed_graph(sdk)
        q = _question()
        off = _read(sdk, q)
        on = _read(sdk, q, session_reinjection=True)
        with capsys.disabled():
            print("\n[probe] OFF legs:", json.dumps(off.get("legs"), indent=1))
            print("[probe] ON  legs:", json.dumps(on.get("legs"), indent=1))
            print("[probe] mixes:", _leg_mix(off), _leg_mix(on))
            print("[probe] OFF degradations:", _degradations(off))
            print("[probe] ON  degradations:", _degradations(on))

            # P1 headline: the real armed/degraded-free read must not look degraded
            assert _degradations(off) == [], _degradations(off)
            assert _degradations(on) == [], _degradations(on)

            # a genuine timeout is still detected
            to = {"legs": [{"leg": "fts", "ran": True, "degraded": True,
                            "reason": "timeout", "count": 0}]}
            assert _degradations(to) == [("fts", "timeout")], _degradations(to)

            # a fallback that SERVED rows is still counted
            served = {"legs": [{"leg": "fallback", "ran": True,
                                "degraded": True,
                                "reason": "tfidf_snapshot", "count": 11}]}
            assert _degradations(served) == [("fallback", "tfidf_snapshot")]

            # ── reachability: inject a STRUCTURAL divergence into the REAL
            # armed trace and require FAIL (not skip, not pass).
            diverged = copy.deepcopy(on)
            diverged["legs"] = list(diverged.get("legs") or []) + [
                {"leg": "structural", "ran": True, "degraded": False,
                 "reason": "ok", "count": 3}]
            print("[probe] diverged degradations:", _degradations(diverged))
            try:
                _require_one_leg_mix(off, diverged, test="probe-real-divergence")
            except BaseException as exc:  # noqa: BLE001
                print("[probe] divergence outcome:", type(exc).__name__)
                assert not isinstance(exc, pytest.skip.Exception), (
                    f"STILL FAIL-OPEN: skipped on a real trace: {exc}")
                assert isinstance(exc, pytest.fail.Exception), (
                    f"unexpected {type(exc).__name__}: {exc}")
            else:
                raise AssertionError(
                    "no divergence detected at all — the injection did not "
                    "reach the guard")

            # ── the same shape but with a serving fallback: SKIP is correct
            fallback_served = copy.deepcopy(on)
            fallback_served["legs"] = list(fallback_served.get("legs") or []) + [
                {"leg": "structural", "ran": True, "degraded": False,
                 "reason": "ok", "count": 3},
                {"leg": "fallback", "ran": True, "degraded": True,
                 "reason": "tfidf_snapshot", "count": 11}]
            try:
                _require_one_leg_mix(off, fallback_served, test="probe-served")
            except BaseException as exc:  # noqa: BLE001
                print("[probe] served-fallback outcome:", type(exc).__name__)
                assert isinstance(exc, pytest.skip.Exception), (
                    f"a genuinely degraded read must SKIP, got {exc}")
            else:
                raise AssertionError("a results-bearing fallback did not void "
                                     "the comparison")
    finally:
        sdk.close()
