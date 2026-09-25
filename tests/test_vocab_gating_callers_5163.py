"""#5163 — the three CALLERS that still compiled the catalog union after #2714.

#2714 gated the compile SEAMS (``compile_value_brief`` / ``compile_vocab``) on a
graph's installed pack set, but three callers kept passing no gate. This file
pins the gated behaviour of each, per the issue's table:

  1. ``compile_kind_index_spec`` — the classify-later kind INDEX (#1695), which
     the classifier retrieves over.
  2. ``validate_summary`` → ``_object_kind_vocab`` — the local S1 summary
     enforcer's objectKind set (a process-global memo, now gate-keyed).
  3. the hosted commit door (``POST /v1/sessions/commit``) — the WRITE GATE.

Before/after is the assertion itself: pre-#5163 caller 1 had no
``installed_namespaces`` parameter (the gated calls below raise TypeError) and
callers 2/3 read the process-global union, so every "rejected" case returned
the union's verdict (accepted).

Each test states the doctrine's two questions inline as ``FAIL-ON`` and
``REACHABLE``.

Sibling of ``tests/test_vocab_gating_by_graph.py`` (the SEAM tests, #2714):
this file tests the CALLERS; the seam tests remain the ones that pin the
compile itself.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import numpy as np
import pytest

from tortoise.value_extractor import (
    _object_kind_vocab,
    compile_kind_index_spec,
    validate_summary,
)

DEV = "dev"
MARKETING = "marketing"
#: A real kindDef'd point kind per pack (so the asserts are about NAMESPACES,
#: not a declare-vs-kindDef distinction).
DEV_POINT = "dev:requirement"
MARKETING_POINT = "marketing:contentBrief"
#: A real kindDef'd object/document kind per pack (the enforcer's vocabulary).
DEV_OBJECT = "dev:code"
MARKETING_OBJECT = "marketing:campaign"

TEST_ORG_ID = "team-001"
TEST_TEAM = {
    "org_id": TEST_ORG_ID,
    "key_id": "test-key-001",
    "legacy_full_access": True,
    "tier": "free",
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": None,
}


def _ns(kind: str) -> str:
    return kind.split(":", 1)[0]


def _pack_kinds(spec: dict, ns: str) -> set[str]:
    return {k for k in spec if _ns(k) == ns}


def _clear_caches() -> None:
    """Reset the kind-spec memo between tests. The objectKind vocab memo
    needs no reset: post-#5163 it is keyed by the gate, so an entry can never
    be served to a differently-gated caller."""
    from tortoise.value_extractor import _clear_kind_spec_cache
    _clear_kind_spec_cache()


@pytest.fixture(autouse=True)
def _isolated_value_caches():
    """The gate-keyed memos must not leak between tests (a stale entry would
    make the gated/ungated contrast pass for the wrong reason)."""
    _clear_caches()
    yield
    _clear_caches()


class _NullEncoder:
    """Minimal encoder for building a KindIndex without the embedder (the
    index's ``kind_names`` are what this file inspects)."""

    def encode(self, texts):
        return np.zeros((len(texts), 3)), False


# ══════════════════════════════════════════════════════════════════════════
# 1. compile_kind_index_spec — the classify-later kind INDEX
# ══════════════════════════════════════════════════════════════════════════

class TestCaller1ClassifyLaterKindIndex:

    def test_gate_narrows_the_kind_index_spec(self):
        """The kind INDEX the classifier retrieves over must be gated.

        FAIL-ON: the spec ignores ``installed_namespaces`` (the pre-fix
        behaviour — the parameter did not exist), so the classifier can
        assign a kind from a pack the graph does not install.
        REACHABLE: the ungated catalog spec really carries
        ``marketing:*`` kindDefs and the gated one really keeps ``dev:*``.
        """
        ungated = compile_kind_index_spec()
        gated = compile_kind_index_spec(installed_namespaces={DEV})
        assert _pack_kinds(ungated, MARKETING), \
            "fixture: the union spec must carry marketing kinds"
        assert _pack_kinds(gated, MARKETING) == set(), \
            "a dev-only gate must drop every marketing kind from the index"
        # NOTE: use an OBJECT kind — a point-only kind (dev:requirement) is
        # excluded from the index by design (FIX A, #1695); this test is
        # about the namespace gate, not the point-kind exclusion.
        assert DEV_OBJECT in gated, "the installed pack's kinds must survive"

    def test_gate_is_in_the_memo_key(self):
        """A gate-blind memo would serve the union spec to a gated caller.

        FAIL-ON: the memo key is the packs_dir alone, so populating the union
        first makes the gated call a memo hit (the #5163 warning: fixing the
        call is not enough if the memo still serves the ungated set).
        REACHABLE: the union compile is populated FIRST here, so the gated
        call is a genuine second lookup with a different gate.
        """
        union = compile_kind_index_spec()
        assert _pack_kinds(union, MARKETING)
        gated = compile_kind_index_spec(installed_namespaces={DEV})
        assert _pack_kinds(gated, MARKETING) == set(), \
            "the gated call was served the union's memoized spec"

    def test_gate_never_touches_the_core_sections(self):
        """An EMPTY gate still carries the core sections (core is never gated).

        FAIL-ON: the gate is applied to the core loops too, so a graph with
        no packs loses its core object kinds.
        REACHABLE: the core sections are non-empty in the real compile.
        """
        gated = compile_kind_index_spec(installed_namespaces=set())
        assert {"core:Project", "core:other"} <= set(gated)
        assert _pack_kinds(gated, MARKETING) == set()

    def test_classifier_index_omits_non_installed_kinds(self):
        """End-to-end at the classifier: the built index is gate-scoped.

        FAIL-ON: ``KindClassifier`` never forwards the gate, so its index
        carries the union's marketing kinds.
        REACHABLE: the index's ``kind_names`` are asserted to contain the
        installed pack's kind and to exclude the non-installed namespace, so
        both directions are observed on a non-empty index.
        """
        from tortoise.kind_classifier import KindClassifier
        clf = KindClassifier(encoder=_NullEncoder(),
                             installed_namespaces={DEV}, llm_tail=False)
        names = set(clf.index.kind_names)
        assert DEV_OBJECT in names
        assert not any(n.startswith(MARKETING + ":") for n in names), \
            "the classifier's index still offers a non-installed pack's kinds"


# ══════════════════════════════════════════════════════════════════════════
# 2. _object_kind_vocab / validate_summary — the S1 summary enforcer
# ══════════════════════════════════════════════════════════════════════════

class TestCaller2SummaryEnforcerGate:

    def test_object_kind_vocab_gate_excludes_non_installed(self):
        """The enforcer's objectKind set must be gate-scoped.

        FAIL-ON: ``_object_kind_vocab`` compiles the ungated brief, so a
        dev-only graph still accepts a ``marketing:*`` objectKind.
        REACHABLE: ``marketing:campaign`` is a real kindDef and the core
        forms remain present under the gate (both directions observed).
        """
        gated = _object_kind_vocab({DEV})
        assert MARKETING_OBJECT not in gated
        assert DEV_OBJECT in gated
        assert "core:Project" in gated and "Project" in gated

    def test_union_survives_a_gated_call_in_the_same_process(self):
        """The memo is PER GATE — a gated call must not poison the union.

        FAIL-ON: a single process-global memo slot, so the (gated) first call
        leaves the union caller without marketing kinds — or the reverse
        (the union is populated first and served to the gated caller). Both
        orders are exercised across this class and ``test_gate_is_in_the_memo_key``.
        REACHABLE: both sets are non-empty and genuinely differ.
        """
        gated = _object_kind_vocab(frozenset({DEV}))
        assert MARKETING_OBJECT not in gated
        union = _object_kind_vocab()
        assert MARKETING_OBJECT in union, \
            "the ungated caller was served the gated memo"

    def test_validate_summary_rejects_a_non_installed_object_kind(self):
        """The enforcer returns an objectKind error under the graph gate.

        FAIL-ON: ``validate_summary`` ignores its gate (and its existing
        ``vocab`` param), so a dev-only graph's summary passes a
        ``marketing:*`` objectKind.
        REACHABLE + POSITIVE CONTROL: the identical summary is ACCEPTED
        ungated, so the rejection is caused by the gate alone.
        """
        s = {"state": [{"name": "X", "objectKind": MARKETING_OBJECT}],
             "decisions": [], "logic": []}
        assert validate_summary(s) == []
        errs = validate_summary(s, installed_namespaces={DEV})
        assert any("objectKind" in e for e in errs), errs

    def test_validate_summary_accepts_installed_and_core(self):
        """The positive control: installed pack kinds + core still pass."""
        for kind in (DEV_OBJECT, "Project"):
            s = {"state": [{"name": "X", "objectKind": kind}],
                 "decisions": [], "logic": []}
            assert validate_summary(s, installed_namespaces={DEV}) == [], kind


class TestCaller2SdkV1Wiring:
    """The production wiring: ``TortoiseSDK._commit_session_v1`` resolves the
    graph's gate and threads it to the enforcer."""

    def test_sdk_v1_enforcer_uses_the_graph_gate(self, tmp_path):
        """A dev-only graph's v1 commit rejects a marketing objectKind.

        FAIL-ON: the SDK path passes no gate, so the enforcer reads the
        process-global union and the summary passes.
        REACHABLE: the graph carries a real ``:PackInstall`` record for
        ``dev`` and the summary really carries a ``marketing:*`` objectKind,
        so the error is caused by the graph's own install set.
        """
        from tests.test_extractor_v2 import MockModel
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(db_path=str(tmp_path / "g5163.db"),
                          namespace="test_g5163_v1")
        sdk._get_proj().g.query(
            "MERGE (p:PackInstall {namespace: $ns}) "
            "SET p.version='0.0.0', p.status='active', p.source='starter'",
            params={"ns": DEV})
        out = sdk.commit_session(
            summary={"session": {"summary": "S"},
                     "state": [{"name": "artifact",
                                "objectKind": MARKETING_OBJECT}],
                     "decisions": [], "logic": [], "issues": []},
            extractor="v1", extractor_model=MockModel(
                lambda system, user:
                '{"entities": [], "events": [], "points": [], '
                '"operators": []}'),
            base_url="http://unused", api_key="k")
        assert any("objectKind" in e for e in out["errors"]), out["errors"]


# ══════════════════════════════════════════════════════════════════════════
# 3. The hosted commit door — the WRITE GATE
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client():
    """TestClient with the auth override + a temp embedded DB (mirrors
    ``tests/test_commit_endpoint.py``)."""
    from fastapi.testclient import TestClient

    from tests._http_fixtures import patched_tortoise_sdk
    from tortoise.hosted_api import app, get_current_org

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        # Pre-warm the lazy embedding singleton OUTSIDE the request: the first
        # graph write embeds, and a cold model load INSIDE the request can
        # exceed the API's 10 s transport wait bound (504) — a harness
        # artifact, since this file tests the GATE and not the bound. A no-op
        # when the embeddings extra is absent (EmbeddingModel.get() → None).
        from tortoise.embeddings import EmbeddingModel
        EmbeddingModel.get()
        app.dependency_overrides[get_current_org] = lambda: dict(TEST_TEAM)
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc
        app.dependency_overrides.clear()


def _seed_install(namespace: str, *, status: str = "active") -> None:
    """Write ONE ``:PackInstall`` record into the tenant graph."""
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sdk._get_proj().g.query(
        "MERGE (p:PackInstall {namespace: $ns}) "
        "SET p.version='0.0.0', p.status=$st, p.source='starter'",
        params={"ns": namespace, "st": status})


def _commit_payload(point_kind: str, session_id: str = "s1") -> dict:
    from tests.test_commit_schema import _finalize, _point, _raw_payload
    return _finalize(_raw_payload(
        points=[_point(0, pointKind=point_kind)], session_id=session_id))


def _post(client, payload: dict):
    return client.post("/v1/sessions/commit", json=payload)


class TestCaller3HostedCommitDoor:

    def test_door_rejects_a_non_installed_kind(self, client):
        """The live commit path must enforce per-graph approval (a 422).

        FAIL-ON: ``validate_payload_dict(raw)`` is called with ``vocab=None``,
        so Layer-1 uses the process-global union and ACCEPTS
        ``marketing:contentBrief`` on a dev-only graph.
        REACHABLE: the seeded ``dev`` record is the only activation, the
        payload really carries the marketing point kind, and the positive
        control below really is accepted — so the 422 is caused by the gate.
        """
        _seed_install(DEV)
        r = _post(client, _commit_payload(MARKETING_POINT))
        assert r.status_code == 422, r.text
        body = r.json()["detail"]
        assert body.get("code") == "calibration_mismatch", body
        assert "points[0].pointKind" in body

    def test_door_accepts_an_installed_kind(self, client):
        """The positive control for the rejection above."""
        _seed_install(DEV)
        r = _post(client, _commit_payload(DEV_POINT))
        assert r.status_code == 200, r.text

    def test_door_keeps_the_union_when_there_are_no_records(self, client):
        """#2714 indicator 3 at the door: no ``:PackInstall`` records ⇒ union.

        FAIL-ON: the door treats "no records" as an empty gate, breaking every
        pre-#318 / self-hosted / restored graph.
        REACHABLE: the docstring's contrast is the test above — the SAME
        marketing kind is 422'd once a (dev-only) record exists.
        """
        r = _post(client, _commit_payload(MARKETING_POINT))
        assert r.status_code == 200, r.text

    def test_door_keeps_a_namespace_already_in_the_data(self, client):
        """Indicator 2's back-compat clause: historical data stays writable.

        FAIL-ON: the door uses the record-only set, so a graph carrying
        ``marketing:campaign`` data under a dev-only install loses the
        ability to write marketing kinds.
        REACHABLE: a real ``objectKind='marketing:campaign'`` node is written
        into the graph, so the data union has a row to find.
        """
        import tortoise.hosted_api as ha_mod
        _seed_install(DEV)
        sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
        sdk._get_proj().g.query(
            "MERGE (n:Object {id: 's5163-historical', objectKind: $k})",
            params={"k": MARKETING_OBJECT})
        r = _post(client, _commit_payload(MARKETING_POINT))
        assert r.status_code == 200, r.text
