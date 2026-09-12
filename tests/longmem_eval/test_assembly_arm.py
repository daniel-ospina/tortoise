"""#2165 Task 7 — v2-lane eval arm: assembled (B) vs legacy at DEFAULT caps
(A) vs legacy at WIDENED caps (A-widened), gold-id admission + the
pre-registered metric-b abstention, plus the R9 geometry + R16(b) canary
admission table + matched controls + the master-flag guard.

PRE-REGISTERED METRIC SEMANTICS (plan Task 7, second-model P2-4):
  * metric (a) — gold-id ADMISSION is ALWAYS measured (which gold point ids
    reached the post-cap reader-visible lines / the legacy evidence), reader
    independent;
  * metric (b) — reader CONVERSION is measured ONLY with a real pinned
    reader; when only the stub FakeReader is available this arm ABSTAINS and
    records the abstention ("stub reader — conversion not measured"). No
    stub-reader delta is ever reported as conversion evidence.

Geometry contracts (pinned by Task 1's committed fixture tests — Task 7 is
forbidden from editing the substrate):
  * R9 deep-rank substrate (87 rows): the DEFAULT pool-40 admits NEITHER
    deep gold; A-WIDENED (pool 40→120 window AND item cap) admits BOTH; the
    assembled arm admits BOTH on the same graph — the pool DEPTH is the
    discriminator. #3095 re-measurement (post-#3018): the pre-#3018
    contract had pDeepG1 in-pool; #3018's deterministic-ranking fix moved
    the opaque fulltext order (same rows), so both golds are now deep.
  * R16(b) out-of-subgraph canary: A-DEFAULT admits ≥1 gold (A≥1); B admits
    ZERO (B=0) — the canary question's gold lives on a THIRD object outside
    the resolved subjects' subgraphs.

Docker lane only (live FalkorDB with fulltext)."""
from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import tests._assembly_graph as ag
from tortoise.sdk import TortoiseSDK

_URI = os.environ.get(
    "TORTOISE_DB_URI",
    "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
_PROBE_GRAPH = f"{_URI}_probe"
try:
    os.environ["TORTOISE_DB_URI"] = _PROBE_GRAPH
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.create_point(
        "statement", "probe zzqfulltext roundtrip token 7f3a9c", id="pProbe",
        session_id="sess-probe", is_episodic=True, status="draft")
    _hits = _probe.tortoise_fts_query(
        "zzqfulltext roundtrip token", entity_type="point", limit=3)
    if _hits and _hits[0].get("id") == "pProbe":
        FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    with contextlib.suppress(Exception):
        _probe._get_proj().db.select_graph(
            _PROBE_GRAPH.rsplit("/", 1)[-1]).delete()
    with contextlib.suppress(Exception):
        _probe.close()
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane — tier-2 embedded legs skip)")


def _fresh_uri() -> str:
    return f"{_URI}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


@pytest.fixture(autouse=True)
def _env_clean(monkeypatch):
    monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    for k in ("TORTOISE_ASK_RETRIEVAL_LIMIT", "TORTOISE_ASK_CONTEXT_ITEM_CAP",
              "TORTOISE_ASK_CONTEXT_TOKEN_CAP"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def sdk(monkeypatch):
    uri = _fresh_uri()
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    s = TortoiseSDK()
    monkeypatch.setattr(s, "_namespace", f"arm-{uuid.uuid4().hex[:8]}")
    try:
        yield s
    finally:
        name = uri.rsplit("/", 1)[-1]
        with contextlib.suppress(Exception):
            s._get_proj().db.select_graph(name).delete()
        s.close()


Q_DATE = "2026-09-10"


def _n_rows(evidence: str) -> int:
    """Number of evidence CHUNKS (header included) — the evidence-shape count
    the R9 pool-depth bounds compare. Shares ``ag.evidence_chunks`` so the
    parse rule lives in one place."""
    return len(ag.evidence_chunks(evidence))


def _legacy(sdk, monkeypatch, question, *, widen=False):
    """A-arm: flag-OFF legacy ask() (DEFAULT or WIDENED caps) with the stub
    FakeReader — evidence text is reader-independent (metric a)."""
    from tests.test_ask_sdk import _install_fake
    monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    _install_fake(sdk, monkeypatch, reply="LEGACY-ANSWER")
    if widen:
        monkeypatch.setenv("TORTOISE_ASK_RETRIEVAL_LIMIT", "120")
        monkeypatch.setenv("TORTOISE_ASK_CONTEXT_ITEM_CAP", "120")
        monkeypatch.setenv("TORTOISE_ASK_CONTEXT_TOKEN_CAP", "32000")
    return sdk.ask(question, question_date=Q_DATE)


def _b_arm(sdk, question, *, caps=None, widen=False):
    """B-arm selector: assembled via ask_assembled. REFUSES (SystemExit)
    unless the assembly master flag is ON — the arm must never emit an
    A-shaped result under the flag gate."""
    if os.environ.get("TORTOISE_ASK_CONNECTED_ASSEMBLY") not in ("1", "true"):
        raise SystemExit(
            "B-arm refused: TORTOISE_ASK_CONNECTED_ASSEMBLY unset/0 — the "
            "assembled arm must never run under the OFF gate")
    _caps = {"limit": 120, "context_item_cap": 120,
             "context_token_cap": 32000} if widen else (caps or {})
    return sdk.ask_assembled(question, question_date=Q_DATE, caps=_caps)


# ── master-flag guard ──────────────────────────────────────────────────────
def test_b_arm_refuses_without_master_flag(sdk):
    """Step 3b: the B-arm selector REFUSES (explicit abort) when
    TORTOISE_ASK_CONNECTED_ASSEMBLY is unset/0 — never emits an A-shaped
    result under the OFF gate."""
    with pytest.raises(SystemExit):
        _b_arm(sdk, "what is the current status of the couch?")
    os.environ["TORTOISE_ASK_CONNECTED_ASSEMBLY"] = "0"
    with pytest.raises(SystemExit):
        _b_arm(sdk, "what is the current status of the couch?")
    os.environ.pop("TORTOISE_ASK_CONNECTED_ASSEMBLY", None)


# ── R16(b) canary admission table (A≥1 default / B=0) ──────────────────────
def test_r16b_canary_admission_table(sdk, monkeypatch):
    """The canary question's gold (bookshelf-anchored, OUTSIDE the compare
    subjects' subgraphs): A-DEFAULT admits ≥1 (A≥1); B admits ZERO (B=0)."""
    ag.build_base_graph(sdk)
    o = ag.build_out_of_subgraph_gold(sdk)
    a_def = _legacy(sdk, monkeypatch, o["question"])
    assert "reading lamp" in a_def["evidence"], "A≥1 at DEFAULT caps"
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    aa = _b_arm(sdk, o["question"])
    assert aa.fired is True, "the canary compare must fire (B arm)"
    b_text = "".join((h.get("content") or "") for h in aa.post_cap_lines)
    assert "reading lamp" not in aa.evidence, "B=0"
    assert "bookshelf" not in b_text, "B=0 (no gold content in lines)"
    # metric (b) pre-registration: no real pinned reader in this harness
    assert aa.answer is None, ("pure-assembly B arm: conversion is NOT "
                               "measured with the stub (abstained)")


# ── R9 geometry: A-default / A-widened / B admission ──────────────────────
def test_r9_admission_table_default_widened_assembled(sdk, monkeypatch):
    """R9 deep-rank substrate (87 rows): the DEFAULT pool-40 admits FEWER
    THAN 2 of the two deep golds; A-WIDENED (pool 40→120) admits BOTH; the
    assembled arm admits BOTH on the same graph. A-default admission ≠
    A-widened admission (the strawman control cannot collapse) — the POOL
    DEPTH is the discriminator.

    #3095: pre-#3018 this asserted A-default admits G1 ('fieldtrip') only,
    against a substrate whose all-tied FTS scores made the row order an
    opaque engine-internal sequence. #3018 (`deterministic ranking order for
    a static store`) removed the second post-CREATE write whose index-stat
    skew produced that sequence; the stored data is byte-identical, but the
    opaque order moved and G1 fell outside the default window.

    This test owns the ORDER-INDEPENDENT pool-depth contract:
      * A-DEFAULT admits FEWER THAN 2 of the two deep golds (measured
        post-#3018: 0; pre-#3018: 1 — both satisfy it, so a future engine
        re-order that moves a tied row across the cutoff does not falsely
        fail here);
      * A-WIDENED admits BOTH golds on the SAME graph;
      * the DEFAULT window is non-empty and genuinely narrower than the
        widened one (the depth discriminator cannot collapse).
    It deliberately does NOT pin which deep gold lands where. (The
    A-default-admits-something property is pinned on THIS substrate by the
    non-vacuity control below; ``test_r16b_canary_admission_table`` pins the
    A≥1-gold admission on the base-graph canary substrate, a different
    fixture.)"""
    d = ag.build_deep_rank_substrate(sdk)
    q = d["question"]
    a_def = _legacy(sdk, monkeypatch, q)
    # ORDER-INDEPENDENT gold admission (the invariant this test owns): the
    # DEFAULT window admits FEWER than 2 of the two deep golds.
    n_gold_def = sum(m in a_def["evidence"]
                     for m in ("fieldtrip", "almanac"))
    assert n_gold_def < 2, (
        f"A-default admits both deep golds ({n_gold_def}/2) — the pool-40 "
        "no longer binds above them, so the A-widened arm is vacuous")
    # non-vacuity: a starved/empty default window must fail, not silently
    # pass both `not in` assertions above (a known in-pool crowd row)
    assert "deep-subject milestone" in a_def["evidence"], \
        "A-default: the pool-40 must still admit in-pool crowd rows"
    assert _n_rows(a_def["evidence"]) >= 20, \
        "A-default: the pool-40 must still admit in-pool crowd rows"
    a_wid = _legacy(sdk, monkeypatch, q, widen=True)
    assert "fieldtrip" in a_wid["evidence"], "A-widened admits G1"
    assert "almanac" in a_wid["evidence"], "A-widened admits G2"
    # #3095 depth RELATIONSHIP, not just non-emptiness: widening must
    # actually widen, and the DEFAULT window must still be the pool-40
    # default (a default that collapsed, or that silently widened to the
    # 120 fetch, would make the discriminator this whole test owns vacuous).
    n_def, n_wid = _n_rows(a_def["evidence"]), _n_rows(a_wid["evidence"])
    assert n_wid > n_def, (
        f"A-widened must admit MORE chunks than A-default (got {n_wid} vs "
        f"{n_def}) — otherwise the pool depth is not the discriminator")
    assert n_def <= 45, (
        f"A-default admits {n_def} evidence chunks — the DEFAULT window "
        "silently widened (measured 41 chunks: 40 pool rows + header); the "
        "strawman guard would be vacuous")
    assert a_def["evidence"] != a_wid["evidence"], \
        "A-default admission ≠ A-widened admission (strawman guard)"
    # B arm on the SAME graph with a FIRED shape (current-state on the deep
    # subject — the interval canary is single-phrase and structurally does
    # not fire). Widened per-subject fetch + item cap so both dated gold
    # rows (deep rank in legacy terms) reach post_cap_lines.
    b_q = "what is the current status of the deep-subject?"
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    aa = _b_arm(sdk, b_q, widen=True)
    assert aa.fired is True, f"the B question must fire: {b_q}"
    ids = {h.get("id") for h in aa.post_cap_lines}
    assert {d["gold_a"], d["gold_b"]} <= ids, \
        f"assembled must admit BOTH deep golds: got {sorted(ids)}"
    # metric (b) abstention recorded (stub reader only)
    assert aa.answer is None


def test_r9_widened_reach_both_retrievable(sdk, monkeypatch):
    """The A-widened arm admits both golds ONLY because both are retrievable
    at the widened fetch depth (non-vacuous widening)."""
    d = ag.build_deep_rank_substrate(sdk)
    hits = sdk.tortoise_fts_query(d["question"], entity_type="point",
                                  limit=120)
    hit_ids = {h.get("id") for h in hits}
    assert {d["gold_a"], d["gold_b"]} <= hit_ids


# ── matched controls ───────────────────────────────────────────────────────
def test_matched_control_order_shuffle_no_delta(sdk, monkeypatch):
    """Matched control: two independent B-arm runs on the SAME graph yield
    byte-identical reader evidence + identical post-cap admission — an
    order/read shuffle must produce NO delta (the reader delta is zero by
    construction on deterministic slices)."""
    ag.build_base_graph(sdk)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    q = "which came first - the couch or the dog bed?"
    a1 = _b_arm(sdk, q)
    a2 = _b_arm(sdk, q)
    assert a1.fired is a2.fired is True
    assert a1.evidence == a2.evidence
    assert [h.get("id") for h in a1.post_cap_lines] == \
        [h.get("id") for h in a2.post_cap_lines]
    # the stub reader (when supplied) sees the SAME context both times
    from tests.test_ask_sdk import FakeReader
    r1 = sdk.ask_assembled(q, question_date=Q_DATE,
                           _reader_factory=lambda: FakeReader(
                               reply="C", tokens_out=3))
    cache = __import__("tortoise.sdk", fromlist=["_ask_reader_cache"])
    cache._ask_reader_cache().pop(
        f"ask:{getattr(sdk, '_namespace', 'default')}", None)
    r2 = sdk.ask_assembled(q, question_date=Q_DATE,
                           _reader_factory=lambda: FakeReader(
                               reply="C", tokens_out=3))
    assert r1.evidence == r2.evidence and r1.answer == r2.answer


def test_matched_control_superseded_rows_direction(sdk, monkeypatch):
    """+superseded matched control (report-only direction, plan-review P2):
    adding the supersession-chain variants changes the fired evidence ONLY by
    the expected direction — the orphan/torn/excluded rows never fabricate
    links and never flip retrieval_degraded."""
    ag.build_base_graph(sdk)
    ag.build_supersession_chain_variants(sdk)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    aa = _b_arm(sdk, "what is the current status of the orphan-src?")
    assert aa.fired is True
    assert "no successor record found" in aa.evidence
    assert aa.retrieval_degraded is False
    aa2 = _b_arm(sdk, "what is the current status of the torn-row?")
    assert aa2.fired is True
    assert "successor unknown" in aa2.evidence
    assert aa2.retrieval_degraded is False
