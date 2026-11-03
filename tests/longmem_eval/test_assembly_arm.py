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
  * R9 deep-rank substrate (87 rows): the DEFAULT pool-40 admits FEWER THAN
    2 of the two deep golds — the TOLERATED SET is {0, 1}, and that
    one-sided bound (spec R9 / plan Task 7 pin "<2", never "0") is what
    the tests below assert. 0 is the measured post-#3018 state and 1 the
    measured pre-#3018 one; with every row tied on score the two differ only
    in the engine's opaque sequence, so the bound is deliberately one-sided
    (see the #3273 note on the assertion). A-WIDENED (pool 40→120 window AND
    item cap) admits BOTH; the assembled arm admits BOTH on the same graph —
    the pool DEPTH is the discriminator. #3095 re-measurement (post-#3018):
    the pre-#3018 contract had pDeepG1 in-pool; #3018's deterministic-ranking
    fix moved the opaque fulltext order (same rows), so both golds are now
    deep.
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
from tests import _live_utils
from tortoise.ask_lane import run_ask_assembled, run_ask_lane
from tortoise.sdk import TortoiseSDK

_URI = os.environ.get(
    "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
    _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
_PROBE_GRAPH = f"{_URI}_probe"
try:
    os.environ["TORTOISE_DB_URI"] = _PROBE_GRAPH
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    # #8057: the probe WRITES a point, and `create_point` derives an embedding
    # (`encode_for_store` -> `EmbeddingModel.get`). This runs at MODULE IMPORT,
    # so the `_no_embedder` fixture below cannot reach it — and a cold
    # sentence-transformers/torch load is 50-205s (#6960) paid for a probe that
    # only needs the FULLTEXT round-trip. Mask the embedder over the whole
    # probe so its cost cannot depend on whether a model is installed.
    with _live_utils.mask_embedder():
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
def _no_embedder():
    # #8057: the mask has ONE owner (`tests/_live_utils.mask_embedder`) — the
    # module-level probe above needs the same mask, from a place a fixture
    # cannot reach. Hand-rolling the two patches here again is how the probe
    # fell out of step in the first place.
    with _live_utils.mask_embedder():
        yield


@pytest.fixture(autouse=True)
def _env_clean(monkeypatch):
    monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    # #4105: pin the HISTORICAL ask-lane caps. This module's "DEFAULT" arm is
    # the pool-40 shape (its whole R9 contract is a statement about that
    # shape); the product defaults were raised to 200/400/200/16000/128000 bytes and
    # would otherwise admit both deep golds and make the discriminator
    # vacuous. The byte ceiling is pinned too — otherwise it would DERIVE
    # from whatever token cap a widened arm sets.
    monkeypatch.setenv("TORTOISE_ASK_RETRIEVAL_LIMIT", "40")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_ITEM_CAP", "40")
    monkeypatch.setenv("TORTOISE_ASK_POOL_SIZE", "120")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_TOKEN_CAP", "8000")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_BYTE_CAP", "32768")
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
    """A-arm: flag-OFF legacy run_ask_lane() (DEFAULT or WIDENED caps) with the stub
    FakeReader — evidence text is reader-independent (metric a)."""
    from tests.test_ask_sdk import _install_fake
    monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    _install_fake(sdk, monkeypatch, reply="LEGACY-ANSWER")
    if widen:
        monkeypatch.setenv("TORTOISE_ASK_RETRIEVAL_LIMIT", "120")
        monkeypatch.setenv("TORTOISE_ASK_CONTEXT_ITEM_CAP", "120")
        monkeypatch.setenv("TORTOISE_ASK_CONTEXT_TOKEN_CAP", "32000")
        # the widened byte ceiling must follow the widened token cap, or the
        # pinned 32 KiB default would silently neutralise the raise
        monkeypatch.setenv("TORTOISE_ASK_CONTEXT_BYTE_CAP", "256000")
    return run_ask_lane(sdk, question, question_date=Q_DATE)


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
    return run_ask_assembled(sdk, question, question_date=Q_DATE, caps=_caps)


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
    # DEFAULT window admits FEWER than 2 of the two deep golds, written as
    # the named TOLERATED SET {0, 1}. 0 is the measured post-#3018 state and
    # 1 the measured pre-#3018 one; with every row tied on score they differ
    # only in the engine's opaque sequence, not in the pool-depth mechanism
    # this test guards. #3273: the one-sidedness is therefore deliberate —
    # `n_gold_def == 0` would re-pin opaque order and re-create the #3095
    # main-red. The collapse this test exists to catch is the one that makes
    # the A-widened arm VACUOUS, i.e. BOTH golds in DEFAULT; a SINGLE-gold
    # leak is a tolerated order fact, not a pool collapse. A starved or
    # silently-widened DEFAULT window stays loud via the crowd-row and 20..45
    # bounds below.
    n_gold_def = sum(m in a_def["evidence"]
                     for m in ("fieldtrip", "almanac"))
    assert n_gold_def in (0, 1), (
        f"A-default admits both deep golds ({n_gold_def}/2) — the pool-40 "
        "no longer binds above them, so the A-widened arm is vacuous")
    # non-vacuity: a starved/empty default window must fail, not silently
    # pass the `n_gold_def in (0, 1)` guard above (a known in-pool crowd
    # row).
    # #3291: the token must be CROWD-UNIQUE. "deep-subject milestone" also
    # appears in the two GOLD rows (tests/_assembly_graph.py:317), so a gold
    # row ALONE satisfied this control and it did not pin crowd-row admission
    # at all. "milestone was discussed" occurs only in the crowd rows (:301).
    assert "milestone was discussed" in a_def["evidence"], \
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
    r1 = run_ask_assembled(sdk, q, question_date=Q_DATE,
                           _reader_factory=lambda: FakeReader(
                               reply="C", tokens_out=3))
    cache = __import__("tortoise.ask_lane", fromlist=["_ask_reader_cache"])
    cache._ask_reader_cache().pop(
        f"ask:{getattr(sdk, '_namespace', 'default')}", None)
    r2 = run_ask_assembled(sdk, q, question_date=Q_DATE,
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


# ── #2653 N-ary (3-named-subject) ordering: chain + gold admission ─────────
def _nary_gold_admission(handle: dict, text: str) -> list[str]:
    """Which of the substrate's three golds reached the reader-visible text.

    Metric (a) is the ONLY metric measurable for the legacy A arm — a legacy
    result carries evidence TEXT, no point ids — so both arms are measured in
    the same currency here and the reported comparison is apples-to-apples."""
    return [s for s, g in handle["golds"].items() if g["marker"] in text]


def _nary_gold_positions(handle: dict, text: str) -> dict[str, int]:
    """Chunk index of each admitted gold inside the reader-visible window.

    DIAGNOSTIC ONLY — never an assertion input. Which tied row lands at which
    index is the engine's index-time internal order
    (``tests/_assembly_graph.py`` build_deep_rank_substrate note, #3095/#3273),
    so nothing may be pinned to a number. Carried in the geometry assertions'
    messages so a drift reports WHERE the golds sat, not just that a count
    moved."""
    chunks = ag.evidence_chunks(text)
    out: dict[str, int] = {}
    for subject, g in handle["golds"].items():
        for i, c in enumerate(chunks):
            if g["marker"] in c:
                out[subject] = i
                break
    return out


def test_nary_ordering_chain_admits_every_gold_vs_legacy(sdk, monkeypatch):
    """#2653 acceptance, end to end on a live graph: a question ordering THREE
    named subjects routes to ORDERING with three terms, the resolver gates on
    ALL THREE (R1's N-ary form), the renderer states the FULL chronological
    chain over one dated spine, and every subject's gold reaches the reader —
    at DEFAULT caps, i.e. with no widening asked of the caller.

    The A arm is measured on the SAME graph and the SAME question, so the
    comparison is not a strawman. MEASURED (this substrate, 60 crowd rows
    carrying all three question nouns): BOTH arms admit all three golds —
    legacy's pooled window carries them too — so the acceptance indicator "gold
    admission >= legacy A-arm" holds with EQUALITY, and what the N-ary arm buys
    at DEFAULT caps is precision, not admission: 5 evidence chunks (header +
    chain + three golds, ZERO crowd rows) against the legacy window's 41
    chunks, 37 of which are crowd rows. NOT a manufactured win: the substrate
    is not authored to starve legacy, and an arm that improves on parity would
    keep passing.

    ⛔ The legacy arm reaches the golds through the pooled full-text window's
    internal row order — NOT through an entity/edge leg (the shipped chain has
    none; see the substrate docstring). That order is index-time and opaque,
    so the A arm's admission count is REPORTED here and never pinned: the pin
    that used to assert ``len(a_golds) == 3`` was removed for exactly the
    reason ``build_deep_rank_substrate`` records — a pin on which tied row
    falls inside the 40-row window is the assertion that reddened main at
    #3095/#3273. What IS pinned is order-independent: legacy's window stays
    non-vacuous (crowd rows admitted, pool filled), the N-ary arm admits every
    gold structurally, its block carries zero crowd rows, and it is narrower
    than the legacy window it replaces.
    """
    from tortoise.assembly import AssemblyShape, classify_question, extract_subject_terms

    d = ag.build_nary_ordering_substrate(sdk)
    q = d["question"]
    golds = d["golds"]
    dates = [golds[s]["date"] for s in d["subjects"]]

    # (1) ROUTING — the issue's own phrasing extracts THREE terms and rides
    #     the ORDERING shape (the shape the two census rows ride).
    assert classify_question(q) is AssemblyShape.ORDERING, q
    terms = extract_subject_terms(q)
    assert len(terms) == 3, f"expected 3 subject terms, got {terms}"
    # …and a two-option phrasing on the SAME graph still extracts exactly 2:
    # the N-ary extraction must not over-reach into the binary path.
    two = "which came first, the couch or the dog bed?"
    assert len(extract_subject_terms(two)) == 2, extract_subject_terms(two)

    # (2) A arm — legacy, DEFAULT caps, same graph / same question.
    a_def = _legacy(sdk, monkeypatch, q)
    a_golds = _nary_gold_admission(d, a_def["evidence"])
    a_chunks = ag.evidence_chunks(a_def["evidence"])
    # non-vacuity: the legacy window really is a full pool (a starved window
    # would make the precision comparison below meaningless).
    assert d["crowd_marker"] in a_def["evidence"], (
        "A-default must admit in-pool crowd rows")
    assert len(a_chunks) >= 20, (
        f"A-default admitted only {len(a_chunks)} chunks — the pool did not "
        "fill, so the precision comparison below is vacuous")

    # (3) B arm — the N-ary path, SAME default caps.
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    aa = _b_arm(sdk, q)
    assert aa.fired is True, f"the N-ary ordering question must fire: {q}"
    b_golds = _nary_gold_admission(d, aa.evidence)
    blind = sorted(set(d["subjects"]) - set(b_golds))
    assert not blind, (
        f"the N-ary chain must admit EVERY subject's gold; blind: {blind}")
    ids = {h.get("id") for h in aa.post_cap_lines if h.get("id")}
    missing = {g["id"] for g in golds.values()} - ids
    assert not missing, (
        f"assembled post-cap lines must carry all three gold ids; "
        f"missing {sorted(missing)} of {sorted(ids)}")

    # (4) THE ACCEPTANCE COMPARISON — gold admission >= legacy A-arm. NOTE
    # what enforces it: `not blind` above already pins 3/3, so this line is
    # implied by it rather than an independent guard. Kept explicit because
    # it IS the issue's indicator, and labelled so it is never mistaken for a
    # second measurement.
    assert len(b_golds) >= len(a_golds), (
        f"gold admission regressed against the A arm: B={b_golds} "
        f"A={a_golds}")

    # (5) THE SUBSTRATE'S OWN GEOMETRY — what is PINNED and what is only
    #     REPORTED, so a future drift fails instead of leaving two
    #     contradictory sentences in the tree:
    #   (a) PRECISION is what the arm buys, and it is STRUCTURAL — the fired
    #       block carries ZERO crowd rows (crowd rows have no `aboutObject`
    #       edge, so `_subject_order` cannot admit them into a slice: the half
    #       of the geometry where the entity link IS load-bearing).
    #   (b) THE A ARM'S ADMISSION COUNT (measured 3/3) IS DELIBERATELY NOT
    #       PINNED. Legacy reaches the golds through the pooled full-text
    #       window's index-time row order, not an entity leg, and which tied
    #       row lands inside that 40-row window is the assertion
    #       `build_deep_rank_substrate` forbids ("never a specific gold's
    #       index") — #3095/#3273 is the recorded cost of pinning it in this
    #       same module. The count and each gold's chunk index are carried in
    #       the messages below instead, so a drift is reported, not failed.
    #       MEASURED (this head, private lane, 2 passed): A window 41 chunks
    #       with the three golds at chunk indices {couch: 3, dog bed: 1,
    #       bookshelf: 2} — the HEAD of the window (index 0 is the Current-Date
    #       header), which is the position they are authored in. That is the
    #       evidence for reading the parity as an order fact rather than a
    #       relevance leg; it is a measurement, not a pin.
    a_pos = _nary_gold_positions(d, a_def["evidence"])
    b_chunks = ag.evidence_chunks(aa.evidence)
    b_crowd = [c for c in b_chunks if d["crowd_marker"] in c]
    assert not b_crowd, (
        f"the N-ary fired block must carry ZERO crowd rows; got "
        f"{len(b_crowd)} of {len(b_chunks)}: {b_crowd[:2]}")
    assert len(b_chunks) < len(a_chunks), (
        f"the precision claim needs the fired block smaller than the legacy "
        f"window: B={len(b_chunks)} A={len(a_chunks)} "
        f"(A admitted {len(a_golds)}/3 golds, a_golds={a_golds}, at chunk "
        f"indices {a_pos}; A chunks are order-opaque, so NONE of that is "
        f"pinned — only the size comparison is)")

    # (5) THE CHAIN ITSELF — one line, earliest first, every subject named,
    #     the three dates strictly ascending in the order stated.
    chain = next((ln for ln in aa.evidence.splitlines()
                  if "came first" in ln), "")
    assert chain, f"no ordering chain line in evidence:\n{aa.evidence}"
    for subject in d["subjects"]:
        assert subject in chain, (subject, chain)
    pos = [chain.find(day) for day in dates]
    assert all(p >= 0 for p in pos), (dates, chain)
    assert pos == sorted(pos), (
        f"the chain must state the dates in chronological order, got {chain}")

    # (6) PRECISION at DEFAULT caps: the pool is REPLACED, so the crowd noise
    #     that fills the legacy window contributes nothing here, and the
    #     assembled block is narrower than the legacy one it replaces.
    assert d["crowd_marker"] not in aa.evidence, (
        "the fired block must carry no crowd rows")
    b_chunks = ag.evidence_chunks(aa.evidence)
    assert len(b_chunks) < len(a_chunks), (len(b_chunks), len(a_chunks))

    # metric (b) abstention (stub reader only) — unchanged by the N-ary arm.
    assert aa.answer is None

    # (7) A-WIDENED (R15 eval-control honesty): reported, never asserted as a
    #     delta in B's favour — widening legacy adds chunks, it cannot remove
    #     a gold it already admitted.
    a_wid = _legacy(sdk, monkeypatch, q, widen=True)
    a_wid_golds = _nary_gold_admission(d, a_wid["evidence"])
    assert len(a_wid_golds) >= len(a_golds), (a_wid_golds, a_golds)
