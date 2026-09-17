"""#2813 (read side) — can the ASK path retrieve a point by its E3 entity keys?

The E3 extractor writes per-point ``search_keys`` (aliases / verbatim tokens,
``tortoise/commit_schema.py:284``). Three read-side links are claimed by the
codebase; this file EXECUTES the product ask handler over stubs and a live
graph and reports which of them actually hold.

Verified here by execution (not by reading source):

  L1  the Point FTS index is created over ``('content', 'search_keys')``
      (``tortoise/projection/__init__.py:2713``) and the degraded/embedded
      snapshot corpus is ``index_text(content, search_keys)``
      (``tortoise/fallback_snapshot.py:132``) — a query token that occurs
      ONLY in a point's ``search_keys`` matches that point.

  L2  entity/fact-augmented key expansion (C2 #2518) is implemented
      (``tortoise/sdk.py:13486``), reachable via
      ``tortoise_fts_query(entity_key_expansion=True)``, and armed by the
      EVAL lane only (``tools/longmem_eval/retrieve.py:1276``).

The open read-side link this file pins:

  L3  the ask lane's own retrieval call (``tortoise/sdk.py:13898`` —
      ``hits = self.tortoise_fts_query(question, limit=..., pool_size=...,
      include_terminal=True, leg_trace=..., keep_numeric=...,
      search_keys_prf=..., fusion_weights=..., fusion_k=...)``) does **not**
      arm ``entity_key_expansion``, so C2 never runs for a product ask.

Acceptance discipline: every assertion below is produced by EXECUTING the
real ``TortoiseSDK.ask`` handler (or the real retrieval entry it calls) —
never by grepping source for a spelling. The captured retrieval payload is
asserted DEEP-EQUAL, and the value the ask lane receives from retrieval is
asserted on its ids.

Runs against a live FalkorDB (docker lane) on a DEDICATED per-test graph;
FTS is the backend under test, so the embedded tier cannot stand in.
"""
from __future__ import annotations

import inspect
import os
import sys
import textwrap
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.sdk import TortoiseSDK


# ── live FalkorDB availability (the FTS backend the link needs) ─────────────
def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time so the
    module never captures it at import (#221 test-isolation lint)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe2813"
        probe = TortoiseSDK()
        probe._get_proj().g.query("RETURN 1")
        probe.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


FALKORDB_AVAILABLE = _falkordb_available()

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane)")


def _uri() -> str:
    return os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")


# ── fixture graph ───────────────────────────────────────────────────────────
ANCHOR = "road bike repairs"
QUESTION = "how much did the road bike repairs cost me in total"

#: Matches the question's own tokens — what keeps the fts leg non-empty (the
#: C2 pass requires fts hits to expand from).
SEED_ID = "p2813seed0"
SEED_CONTENT = "the road bike repairs cost 120 dollars at the shop"
SEED_KEYS = "road bike repairs bill paid 120 dollars"

#: The point under test: its CONTENT shares no token with the question, and
#: it is linked to the SAME Object anchor. Only its E3 ``search_keys`` can
#: join it to the question's vocabulary.
KEY_ONLY_ID = "p2813keyonly1"
KEY_ONLY_CONTENT = "the mechanic called me back with a quote"
KEY_ONLY_KEYS = "extra charge wheels saturday service fee"

#: Zero overlap with the question / the anchor / the keys — cannot rank in
#: the sparse leg under either arm.
DISTRACTOR_TOPIC = "cooking pasta recipes with tomato basil sauce"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT so the differential isolates the SPARSE entity
    path (mirrors tests/test_entity_key_expansion.py). No model load, no
    network."""
    import tortoise.embeddings as emb
    monkeypatch.setattr(emb, "compute_embedding", staticmethod(lambda c: None))
    monkeypatch.setattr(emb.EmbeddingModel, "get", staticmethod(lambda: None))


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A fresh dedicated graph: the Object anchor, the seed point, the
    key-only point (same anchor), and 10 distractors with zero token
    overlap with anything under test."""
    monkeypatch.setenv("TORTOISE_DB_URI", f"{_uri()}_{uuid.uuid4().hex[:10]}")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point("statement", SEED_CONTENT, id=SEED_ID,
                         session_id="s2813a", search_keys=SEED_KEYS,
                         status="draft")
        sdk.create_point("statement", KEY_ONLY_CONTENT, id=KEY_ONLY_ID,
                         session_id="s2813b", search_keys=KEY_ONLY_KEYS,
                         status="draft")
        for i in range(10):
            sdk.create_point("statement", f"{DISTRACTOR_TOPIC} {i}",
                             id=f"p2813dist{i}", session_id=f"dist2813{i}",
                             status="draft")
        proj.g.query(
            "MATCH (p:Point), (o:Object {name:$name}) WHERE p.id IN $ids "
            "MERGE (p)-[:aboutObject]->(o)",
            params={"name": ANCHOR, "ids": [SEED_ID, KEY_ONLY_ID]})
        yield sdk
    finally:
        sdk.close()


# ── the retrieval entry, exercised directly ────────────────────────────────

def _retrieve_ids(sdk, *, entity_key_expansion: bool) -> list[str]:
    return [h["id"] for h in sdk.tortoise_fts_query(
        QUESTION, limit=40, pool_size=120,
        entity_key_expansion=entity_key_expansion)]


# ── premise + canary (GREEN today; the canary REDs on the named mutation) ──

def test_plain_query_cannot_reach_the_key_only_point(seeded_sdk):
    """Premise of the differential: without the entity arm the key-only
    point is invisible — its content shares no token with the question and
    its keys are reachable only through the entity expansion."""
    ids = _retrieve_ids(seeded_sdk, entity_key_expansion=False)
    assert SEED_ID in ids, "the seed must match the question's own tokens"
    assert KEY_ONLY_ID not in ids, (
        "premise broken: the key-only point is reachable WITHOUT the entity "
        "arm, so the differential below would be vacuous")


def test_entity_retrieval_is_the_link_that_carries_the_key_only_point(
        seeded_sdk):
    """EXECUTED differential on the real retrieval entry.

    * LEGITIMATE FORM — the arm intact: the question's Object anchor resolves
      through the Object-name index and the linked point's E3 ``search_keys``
      are harvested into the second sparse pass, so the key-only point is
      RETRIEVED (GREEN).
    * MUTATION that REDs this test — neuter the harvest, i.e. entity
      retrieval is broken (``TortoiseSDK._entity_key_expansion_pass`` →
      ``None``): the key-only point disappears and the assertion below fails.
      (Verified by running the mutation; see the branch report.)
    """
    off = _retrieve_ids(seeded_sdk, entity_key_expansion=False)
    on = _retrieve_ids(seeded_sdk, entity_key_expansion=True)
    assert KEY_ONLY_ID not in off, "the entity arm must be what carries it"
    assert KEY_ONLY_ID in on, (
        "entity retrieval must surface the point linked to the question's "
        f"Object anchor via its E3 search_keys; armed pool = {on}")


# ── L3: what the REAL ask handler hands retrieval ──────────────────────────

class _StubReader:
    """Minimal stand-in for the ask-lane reader (``_ask_reader_complete`` is
    stubbed too, so only ``decr_inflight`` is exercised)."""

    model = "stub-reader"

    def decr_inflight(self) -> None:  # pragma: no cover - trivial
        pass


def _install_ask_stubs(monkeypatch, capture: dict) -> None:
    """Stub ONLY the stages downstream of retrieval (reader, build) and
    RECORD the retrieval seam. The handler itself — ``TortoiseSDK.ask``,
    verbatim — and the retrieval entry it calls are the real ones."""
    import tortoise.sdk as sdkmod

    real_fts = TortoiseSDK.tortoise_fts_query

    def _recording_fts(self, *args, **kwargs):
        capture["args"] = args
        capture["kwargs"] = kwargs
        out = real_fts(self, *args, **kwargs)
        capture["returned"] = out
        return out

    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _recording_fts)
    monkeypatch.setattr(TortoiseSDK, "_ask_reader_model",
                        lambda self, factory=None: _StubReader())
    monkeypatch.setattr(sdkmod, "_ask_reader_complete",
                        lambda model, *, system, user: ("stub answer", 3))


#: The kwargs the ask lane hands retrieval (``sdk.py:13898``), minus the
#: per-run ``leg_trace`` list identity. Frozen so the assertion below is a
#: DEEP-EQUAL on the observed payload rather than a membership probe.
_ASK_LANE_PAYLOAD = {
    "limit": 40,
    "pool_size": 120,
    "include_terminal": True,
    "keep_numeric": True,
    "search_keys_prf": True,
    "fusion_weights": None,
    "fusion_k": 60,
}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#2813 read side: the ask lane's retrieval call (sdk.py:13898) does "
        "not arm ``entity_key_expansion``, so C2 entity retrieval never runs "
        "for a product ask (deliberate gate — the product lanes adopt C2 "
        "after the sealed A/B delta lands). This XPASSes the moment the "
        "ask-lane call site arms it; drop the marker then."),
)
def test_ask_lane_payload_deep_equals_the_entity_retrieval_payload(
        seeded_sdk, monkeypatch):
    """Execute the real ``TortoiseSDK.ask`` over stubs and assert the payload
    it hands the retrieval entry DEEP-EQUALS the payload entity retrieval
    requires — today's payload plus ``entity_key_expansion=True``.

    RED at the missing key while ``sdk.py:13898`` does not arm it.
    """
    capture: dict = {}
    _install_ask_stubs(monkeypatch, capture)
    seeded_sdk.ask(QUESTION)

    assert capture.get("kwargs"), "the ask lane never reached retrieval"
    observed = {k: v for k, v in capture["kwargs"].items()
                if k != "leg_trace"}
    expected = {**_ASK_LANE_PAYLOAD, "entity_key_expansion": True}
    assert observed == expected, (
        "the ask lane's retrieval payload must carry the entity-key arm; "
        f"observed {sorted(observed)}; differing: "
        + repr({k: (v, observed.get(k, "<absent>"))
                for k, v in expected.items()
                if observed.get(k, "<absent>") != v}))


# ── two directions on the REAL ask handler's source ────────────────────────

#: The one call-site token the arm hangs off. The extraction is anchored on it
#: so a moved/renamed call site fails LOUDLY instead of silently testing a
#: stale copy.
_ASK_CALL_ANCHOR = "search_keys_prf=search_keys_prf,"

#: The REAL ask handler's source, captured from the shipped class at import
#: time so the variant builder below never re-reads a monkeypatched method.
_ASK_SOURCE = textwrap.dedent(inspect.getsource(TortoiseSDK.ask))

_ARM = "entity_key_expansion=True,"


def _bind_ask_variant(monkeypatch, *, arm_entity_key_expansion: bool) -> None:
    """Bind ``TortoiseSDK.ask`` to the REAL handler's source, executed with at
    most ONE documented delta at its retrieval call site — idempotent, so it
    holds whether or not the shipped handler already carries the arm.

    ``arm_entity_key_expansion=True`` → add ``entity_key_expansion=True,`` to
    the retrieval payload (the LEGITIMATE FORM). ``False`` → remove it (the
    shipped shape / the MUTATION: the arm dropped from the ask-lane call).
    """
    import tortoise.sdk as sdkmod

    src = _ASK_SOURCE
    assert _ASK_CALL_ANCHOR in src, (
        "the ask-lane retrieval call site moved — re-anchor this extraction "
        f"(anchor {_ASK_CALL_ANCHOR!r} not found)")
    armed_anchor = f"{_ASK_CALL_ANCHOR} {_ARM}"
    if arm_entity_key_expansion:
        if armed_anchor not in src:
            src = src.replace(_ASK_CALL_ANCHOR, armed_anchor, 1)
    else:
        src = src.replace(armed_anchor, _ASK_CALL_ANCHOR)
    assert src.count(_ARM) == (1 if arm_entity_key_expansion else 0)

    namespace: dict = {}
    exec(compile(src, "<TortoiseSDK.ask:extracted>", "exec"),
         sdkmod.__dict__, namespace)
    monkeypatch.setattr(TortoiseSDK, "ask", namespace["ask"])


def _run_ask(seeded_sdk, monkeypatch):
    """Run whatever ``TortoiseSDK.ask`` is bound and return (ids the ask lane
    received from retrieval, the payload it handed retrieval)."""
    capture: dict = {}
    _install_ask_stubs(monkeypatch, capture)
    seeded_sdk.ask(QUESTION)
    return ([h.get("id") for h in (capture.get("returned") or [])],
            capture.get("kwargs") or {})


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#2813 read side: same missing arm as the payload test above — the "
        "ask lane cannot retrieve a point linked to the question's Object "
        "anchor by its E3 search_keys until sdk.py:13898 arms C2. XPASSes on "
        "the one-argument fix; drop the marker then."),
)
def test_ask_lane_returns_point_matched_only_by_its_e3_keys(
        seeded_sdk, monkeypatch):
    """The behaviour the payload is for: EXECUTE the SHIPPED ask handler and
    assert on the value retrieval hands it — the key-only point must be in
    the evidence pool.

    RED while the arm is missing (today), GREEN the moment the ask-lane call
    site arms it.
    """
    ids, _kwargs = _run_ask(seeded_sdk, monkeypatch)
    assert ids, "the ask lane received an empty pool"
    assert SEED_ID in ids, "the seed must reach the ask lane"
    assert KEY_ONLY_ID in ids, (
        "the ask path must retrieve the point linked to the question's "
        f"Object anchor via its E3 search_keys; ask lane received {ids}")


@pytest.mark.parametrize(
    "arm_entity_key_expansion,expect_retrieved",
    [(True, True), (False, False)])
def test_the_missing_link_is_one_argument_at_the_ask_call_site(
        seeded_sdk, monkeypatch, arm_entity_key_expansion, expect_retrieved):
    """Both directions on the real handler's source:

    * ``armed=False`` — the arm removed from the ask-lane call (what the
      shipped call site does today): the key-only point is NOT retrieved;
    * ``armed=True`` — the SAME handler with the single added kwarg: the
      key-only point IS retrieved and the seed is never displaced.

    Proves the fix is one argument at one call site, not new machinery.
    """
    _bind_ask_variant(monkeypatch,
                      arm_entity_key_expansion=arm_entity_key_expansion)
    ids, kwargs = _run_ask(seeded_sdk, monkeypatch)

    if arm_entity_key_expansion:
        assert kwargs.get("entity_key_expansion") is True, (
            "the legitimate form must hand retrieval the entity-key arm; "
            f"payload keys = {sorted(kwargs)}")
        assert SEED_ID in ids, "the arm must be additive, never displacing"
    else:
        assert "entity_key_expansion" not in kwargs, (
            "the unarmed variant must not carry the arm; "
            f"payload keys = {sorted(kwargs)}")

    assert (KEY_ONLY_ID in ids) is expect_retrieved, (
        f"arm_entity_key_expansion={arm_entity_key_expansion}: key-only "
        f"point retrieved={KEY_ONLY_ID in ids}, expected {expect_retrieved} "
        f"(pool ids: {ids})")
