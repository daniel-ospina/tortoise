"""#2165 Task 6 — ask() connected-assembly branch + ask_assembled (docker-lane).

Golden-evidence record (R17): the EVIDENCE STRINGS below were CAPTURED from
the deterministic flag-OFF legacy lane and the flag-ON fired lane on the
Task-1 fixture graph at Task-6 Step 1 (clean HEAD) and are COMMITTED as the
byte-identity contract.

⛔ INVALIDATION POLICY (plan Task 6, R17): ANY change affecting ask-lane
evidence selection or rendering invalidates these goldens and forces
RE-CAPTURE + RE-REVIEW — never reflexive regeneration. The affected surface
includes: the #2070 knob series (A1–A7), retrieval query/embedding/ranking-
algorithm changes, annotate/dedup changes, assemble_context caps/order
semantics, and _render_block/render_context text. The goldens are the
rendered output of the WHOLE pipeline, not just the named knobs. Re-capture
via the docker lane on tests/_assembly_graph.build_base_graph fixtures with
question_date="2026-09-10" + a FakeReader (deterministic; no LLM in the
evidence path).

#3095: the goldens were RE-CAPTURED under this policy after #3018
(`fix(retrieval): deterministic ranking order for a static store`) changed
the engine's opaque fulltext scan order. The row SET is unchanged for every
golden — only the order of the same rows moved. That claim is mechanically
guarded, not just recorded: ``_FROZEN_CHUNKS`` holds the PRE-#3018 chunk
multisets as an independent (never-re-captured) record for the five
re-captured legacy goldens, and ``_assert_golden`` checks content first and
order second so the two failure modes stay distinguishable.

Docker lane only (live FalkorDB — dedicated per-test graph with fulltext,
deleted at teardown)."""
import contextlib
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import tests._assembly_graph as ag
from tortoise.sdk import TortoiseSDK

# ── Live-FalkorDB + FTS availability (same gate as test_assembly_fixtures) ──
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


@pytest.fixture
def sdk(monkeypatch):
    uri = _fresh_uri()
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    s = TortoiseSDK()
    # per-test reader-cache namespace: the ask reader cache is a module-level
    # LRU keyed ask:<namespace> — a shared "default" would leak one test's
    # FakeReader into the next (order-dependent replies).
    monkeypatch.setattr(s, "_namespace", f"t6-{uuid.uuid4().hex[:8]}")
    try:
        yield s
    finally:
        name = uri.rsplit("/", 1)[-1]
        with contextlib.suppress(Exception):
            s._get_proj().db.select_graph(name).delete()
        s.close()


@pytest.fixture(autouse=True)
def _ask_env_clean(monkeypatch):
    """Hermetic env: the assembly + W4 flags start CLEAN (unset) for every
    test; tests opt in explicitly."""
    monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    monkeypatch.delenv("TORTOISE_W4_ENRICHMENT", raising=False)
    yield


from tests.test_ask_sdk import FakeReader, _install_fake  # noqa: E402

Q_DATE = "2026-09-10"

# ── Golden evidence records (captured Task-6 Step 1, deterministic) ──────
# #3095 RE-CAPTURE (R17 invalidation policy): commit #3018
# (`fix(retrieval): deterministic ranking order for a static store`) removed
# the post-CREATE write whose fulltext index-statistic skew had produced the
# engine's old opaque scan order, so the legacy lane's row ORDER moved. The
# ROW SET is byte-for-byte unchanged for every golden (verified by diff on
# tests/_assembly_graph.build_base_graph / build_out_of_subgraph_gold,
# question_date="2026-09-10", FakeReader, embedder pinned off) — only the
# order of the same rows changed. Re-captured from the docker lane per the
# policy above; the golden assertions in
# ``test_flag_off_golden_byte_identity`` etc. now pin the invariant
# explicitly via ``_assert_golden`` — a CONTENT chunk-multiset check
# (``_FROZEN_CHUNKS``, header + rows, order-insensitive, duplicate-aware)
# followed by the ORDER byte check — so a future order drift is diagnosed as
# a re-capture, not a content regression.
GOLD_LEGACY_CURRENT = """Current Date: 2026-09-10

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] the new sofa was delivered on the first of september

[session ?] talked about phone battery replacement shop with a friend"""
GOLD_LEGACY_MISFIRE = """Current Date: 2026-09-10

[session ?] the dog chewed the corner of the dog bed cushion

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] the new sofa was delivered on the first of september

[session ?] talked about phone battery replacement shop with a friend

[session ?] took the dog to the vet for the chewed cushion"""
GOLD_LEGACY_AGO = """Current Date: 2026-09-10

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] the new sofa was delivered on the first of september

[session ?] talked about phone battery replacement shop with a friend"""
GOLD_LEGACY_COMPARE = """Current Date: 2026-09-10

[session ?] the dog chewed the corner of the dog bed cushion

[session ?] the new sofa was delivered on the first of september

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] talked about phone battery replacement shop with a friend

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] took the dog to the vet for the chewed cushion"""
GOLD_LEGACY_CANARY = """Current Date: 2026-09-10

[session ?] the dog chewed the corner of the dog bed cushion

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] the new sofa was delivered on the first of september

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] talked about phone battery replacement shop with a friend

[session ?] took the dog to the vet for the chewed cushion

[session ?] [valid since 2026-09-05] on the fifth of september we moved the bookshelf into the study and bought a reading lamp - the same week the old couch was discussed and the dog bed got chewed, but the bookshelf was the newest thing we bought that month"""
CANARY_QUESTION = ("which came first - buying the couch or the "
                  "dog bed getting chewed?")

# ── FROZEN row sets — the INDEPENDENT content record (#3095) ─────────────
# The byte-goldens above are re-captured whenever the engine's opaque
# fulltext ORDER moves (R17). That makes them useless as a content guard: a
# reflexive regeneration (the exact thing R17 forbids) satisfies them by
# construction. These frozensets were transcribed from the PRE-#3018
# capture and are NOT re-captured — they are the independent record that
# makes "order drift" mechanically distinguishable from "content loss".
# Update ONLY with a documented, reviewed content change (never as a
# re-capture). Scope: the flag-OFF legacy lane's five goldens.
_ROW_COUCH_STATUS = frozenset({
    "[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars",
    "[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead",
    "[session ?] the new sofa was delivered on the first of september",
    "[session ?] talked about phone battery replacement shop with a friend",
})
_ROW_COUCH_DOGBED = _ROW_COUCH_STATUS | frozenset({
    "[session ?] the dog chewed the corner of the dog bed cushion",
    "[session ?] took the dog to the vet for the chewed cushion",
})
_ROW_CANARY = _ROW_COUCH_DOGBED | frozenset({
    "[session ?] [valid since 2026-09-05] on the fifth of september we moved the bookshelf into the study and bought a reading lamp - the same week the old couch was discussed and the dog bed got chewed, but the bookshelf was the newest thing we bought that month",
})
_FROZEN_ROWS = {
    "what is the current status of the couch?": _ROW_COUCH_STATUS,
    "compare the couch and the dog bed, which should i keep?": _ROW_COUCH_DOGBED,
    "what was the couch status two weeks ago?": _ROW_COUCH_STATUS,
    "which came first - the couch or the dog bed?": _ROW_COUCH_DOGBED,
    CANARY_QUESTION: _ROW_CANARY,
}
# The full frozen CHUNK multiset per question: the literal row sets above plus
# the rendered header chunk. Built from the literals (never from the
# goldens), so it stays an independent record of the PRE-#3018 output.
_HEADER_CHUNK = "Current Date: 2026-09-10"
_FROZEN_CHUNKS = {
    q: tuple(sorted(rows | {_HEADER_CHUNK}))
    for q, rows in _FROZEN_ROWS.items()
}


GOLD_FIRED_CURRENT = """Current Date: 2026-09-10

[session ?] [SUPERSEDED BY: sofa] STATE (couch): superseded by sofa on 2026-09-01

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead"""
GOLD_FIRED_COMPARE = """Current Date: 2026-09-10

[session ?] couch and dog bed both appeared on 2026-08-10

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] the dog chewed the corner of the dog bed cushion

[session ?] took the dog to the vet for the chewed cushion"""
GOLD_FIRED_INTERVAL = """Current Date: 2026-09-10

[session ?] 22 days between buying the couch and selling the couch

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead"""
GOLD_FIRED_CURRENT_HOSTED = """Current Date: 2026-09-10

[session ?] [SUPERSEDED BY: sofa] STATE (couch): superseded by sofa on 2026-09-01

[session 0] bought the grey couch from ikea

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session 0] sold the old couch and ordered a sofa

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead"""
GOLD_FIRED_COMPARE_HOSTED = """Current Date: 2026-09-10

[session ?] couch and dog bed both appeared on 2026-08-10

[session 0] bought the grey couch from ikea

[session ?] [valid since 2026-08-10] bought the grey couch from ikea for 800 dollars

[session 0] sold the old couch and ordered a sofa

[session ?] [valid since 2026-09-01] sold the old couch and ordered a new sofa instead

[session ?] the dog chewed the corner of the dog bed cushion

[session ?] took the dog to the vet for the chewed cushion"""


def _ask(sdk, monkeypatch, q, *, flag_on=False, question_type=None,
         question_date=Q_DATE):
    if flag_on:
        monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    else:
        monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    _install_fake(sdk, monkeypatch, reply="GOLD")
    return sdk.ask(q, question_date=question_date,
                   question_type=question_type)


# ── Step 1/2: flag-OFF byte-identity + unrouted flag-ON byte-identity ─────
def _evidence_chunks(evidence: str) -> tuple[str, ...]:
    """Order-insensitive MULTISET of an evidence block's chunks (the
    ``Current Date:`` header included) — the content half of the golden
    contract.

    A tuple of the chunk-sorted chunks, NOT a set: a set erases multiplicity,
    so a DUPLICATED row would compare equal and the byte assertion below
    would then report it as an order drift — pointing the reader at a
    re-capture (which would launder the regression permanently)."""
    return tuple(sorted(ag.evidence_chunks(evidence)))


def _assert_golden(evidence: str, question: str, gold: str) -> None:
    """Two-layer golden gate (#3095): CONTENT then ORDER.

    Layer 1 (content) compares the full chunk multiset — header and rows,
    order-insensitively — against ``_FROZEN_CHUNKS``, frozen from the
    PRE-#3018 capture and never re-captured. So a reflexive regeneration of
    the byte-golden still fails here, and a dropped, duplicated, or
    re-rendered chunk reports as a *content regression*.

    Layer 2 (order) compares the re-captured byte-golden; when the engine's
    opaque fulltext sequence moves it reports as an *order drift*, which is
    R17 re-capture territory rather than a bug.

    Content is asserted FIRST on purpose: a byte assert first would abort
    before the content comparison, making the two failure modes
    indistinguishable (the trap the first cut of this fix fell into).

    Scope: the five flag-OFF legacy goldens. The FIRED/hosted goldens were
    not invalidated by #3018 and remain bare byte-equality."""
    live = _evidence_chunks(evidence)
    frozen = _FROZEN_CHUNKS[question]
    assert live == frozen, (
        f"CONTENT regression on {question!r}: the evidence chunk multiset "
        "(header + rows, order-insensitive) moved. This is NOT an order "
        "drift — do NOT re-capture the golden, that would launder it; "
        "investigate the pipeline. "
        # multiset diagnostics on purpose: a set difference reports a pure
        # DUPLICATE as "Missing: []; unexpected: []" — the regression the
        # multiset comparison exists to catch.
        f"Missing: {sorted((Counter(frozen) - Counter(live)).elements())}; "
        f"unexpected: {sorted((Counter(live) - Counter(frozen)).elements())}")
    assert evidence == gold, (
        f"ROW ORDER drifted on {question!r}: the chunk multiset is intact, "
        "so this is the engine's opaque fulltext sequence moving, not a "
        "content change — verify the set then RE-CAPTURE + RE-REVIEW per "
        "the R17 policy at the top of this module.")


def test_flag_off_golden_byte_identity(sdk, monkeypatch):
    """Flag OFF == the committed legacy golden (capture-time record)."""
    ag.build_base_graph(sdk)
    for q, gold in [
        ("what is the current status of the couch?", GOLD_LEGACY_CURRENT),
        ("compare the couch and the dog bed, which should i keep?",
         GOLD_LEGACY_MISFIRE),
        ("what was the couch status two weeks ago?", GOLD_LEGACY_AGO),
        # #3095: GOLD_LEGACY_COMPARE was re-captured but never asserted by
        # any test (dead since its introduction) — its byte-identity
        # contract is now actually enforced.
        ("which came first - the couch or the dog bed?", GOLD_LEGACY_COMPARE),
    ]:
        res = _ask(sdk, monkeypatch, q, flag_on=False)
        _assert_golden(res["evidence"], q, gold)
        # NOTE: the legacy lane's retrieval_degraded is the AMBIENT
        # no-embedder signal (vector leg ran:False) — NOT part of this
        # golden contract; only EVIDENCE equality is pinned here.


def test_flag_on_unrouted_byte_identity(sdk, monkeypatch):
    """Flag ON + UNROUTED (misfire/ago — classifier shape=None) == the SAME
    legacy golden: the branch precedes retrieval and fires nothing."""
    ag.build_base_graph(sdk)
    for q, gold in [
        ("compare the couch and the dog bed, which should i keep?",
         GOLD_LEGACY_MISFIRE),
        ("what was the couch status two weeks ago?", GOLD_LEGACY_AGO),
    ]:
        res = _ask(sdk, monkeypatch, q, flag_on=True)
        _assert_golden(res["evidence"], q, gold)


def test_fired_routing_exact_goldens(sdk, monkeypatch):
    """Flag ON + FIRED: the EXACT assembled evidence goldens (state header +
    chronological dated spine). Two sessions (08-10 buy / 09-01 sell) each
    contribute dated lines."""
    ag.build_base_graph(sdk)
    from tortoise.retrieval import estimate_tokens_ask
    cases = [
        ("what is the current status of the couch?", GOLD_FIRED_CURRENT),
        ("which came first - the couch or the dog bed?", GOLD_FIRED_COMPARE),
        ("how many days between buying the couch and selling the couch?",
         GOLD_FIRED_INTERVAL),
    ]
    for q, gold in cases:
        res = _ask(sdk, monkeypatch, q, flag_on=True)
        assert res["evidence"] == gold, q
        assert res["retrieval_degraded"] is False, q
        # alignment invariant on the FIRED path: context_tokens ALWAYS
        # matches what the reader consumed
        assert res["context_tokens"] == estimate_tokens_ask(res["evidence"]), q
    # structural pin: the fired current-state block states the supersession
    assert "[session ?] [SUPERSEDED BY: sofa] STATE (couch): superseded by " \
        "sofa on 2026-09-01" in GOLD_FIRED_CURRENT
    # ≥2 dated lines from ≥2 sessions
    assert "[valid since 2026-08-10]" in GOLD_FIRED_CURRENT
    assert "[valid since 2026-09-01]" in GOLD_FIRED_CURRENT


def test_hosted_shape_fired_goldens(sdk, monkeypatch):
    """R6: the hosted-variant Event-aboutObject graph adds its Event spine
    rows (startedAt-dated, [session N]) — a SUPERSET of the base render."""
    ag.build_base_graph(sdk)
    ag.build_hosted_variant(sdk)
    res = _ask(sdk, monkeypatch, "what is the current status of the couch?",
               flag_on=True)
    assert res["evidence"] == GOLD_FIRED_CURRENT_HOSTED, res["evidence"]
    assert "[session 0] bought the grey couch from ikea" in res["evidence"]
    res2 = _ask(sdk, monkeypatch,
                "which came first - the couch or the dog bed?", flag_on=True)
    assert res2["evidence"] == GOLD_FIRED_COMPARE_HOSTED, res2["evidence"]


def test_positive_fts_paraphrase_fires(sdk, monkeypatch):
    """Docker positive: 'the couch I bought in March' resolves via name-token
    FTS (source=fts) AND the fired path assembles the state-header block."""
    ag.build_base_graph(sdk)
    aa = sdk.ask_assembled(
        "what is the current status of the couch I bought in March?",
        question_date=Q_DATE)
    assert aa.fired is True, "paraphrase subject must route via FTS"
    assert aa.shape == "current-state"
    assert any(s["name"] == "couch" for s in aa.subjects), aa.subjects
    assert "STATE (couch): superseded by sofa on 2026-09-01" in aa.evidence


def test_out_of_subgraph_gold_a1_b0(sdk, monkeypatch):
    """R16(b) pre-registered canary: flag-OFF legacy ADMITS ≥1 of the
    out-of-subgraph gold (A≥1) while the assembled arm admits ZERO (B=0)."""
    ag.build_base_graph(sdk)
    o = ag.build_out_of_subgraph_gold(sdk)
    legacy = _ask(sdk, monkeypatch, o["question"], flag_on=False)
    assert o["question"] == CANARY_QUESTION, o["question"]
    _assert_golden(legacy["evidence"], o["question"], GOLD_LEGACY_CANARY)
    assert "reading lamp" in legacy["evidence"], \
        "A≥1: legacy must admit the out-of-subgraph gold"
    aa = sdk.ask_assembled(o["question"], question_date=Q_DATE)
    assert aa.fired is True, "the canary compare must fire"
    assert "reading lamp" not in aa.evidence,         "B=0: assembled walks only the resolved subjects' subgraphs"
    assert all("bookshelf" not in (h.get("content") or "")
               for h in aa.post_cap_lines)


def test_one_half_unresolved_legacy_fallback(sdk, monkeypatch):
    """Fired-but-unresolved (one half names a NONEXISTENT object) → legacy
    fallback, byte-identical to flag OFF, no error."""
    ag.build_base_graph(sdk)
    q = "which came first - the couch or the hoverboard?"
    off = _ask(sdk, monkeypatch, q, flag_on=False)
    on = _ask(sdk, monkeypatch, q, flag_on=True)
    assert on["evidence"] == off["evidence"]
    # (the unrouted flag-ON path is legacy — its retrieval_degraded carries
    # the ambient no-embedder signal; only evidence equality is pinned)


def test_validation_precedes_fired_branch(sdk, monkeypatch):
    """Validation ALWAYS precedes the branch: control chars / oversized /
    bad question_date on a FIRED-shape question → AskValidationError."""
    from tortoise.exceptions import AskValidationError
    ag.build_base_graph(sdk)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    for bad in [
        "what is the current status of the couch?\x00control",
        "x" * 2001,
    ]:
        with pytest.raises(AskValidationError):
            sdk.ask(bad, question_date=Q_DATE)
    with pytest.raises(AskValidationError):
        sdk.ask("what is the current status of the couch?",
                question_date="not-a-date")


def test_exactly_one_reader_call_and_no_legacy_rerun(sdk, monkeypatch):
    """Fired path: EXACTLY ONE model call. A reader raise maps to
    AskReaderUnavailable with ZERO legacy re-run (retrieval must never fire
    again on the fired path)."""
    import tortoise.sdk as sdk_mod
    from tortoise.exceptions import AskReaderUnavailable
    calls = {"n": 0}
    raise_on = {"n": 0}

    def _flaky(model, *, system, user):
        calls["n"] += 1
        if raise_on["n"]:
            raise AskReaderUnavailable("reader unavailable (flaky)")
        return "42", 5

    monkeypatch.setattr(sdk_mod, "_ask_reader_complete", _flaky)
    monkeypatch.setattr(sdk, "tortoise_fts_query",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("legacy retrieval must NOT run")))
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    ag.build_base_graph(sdk)
    res = sdk.ask("what is the current status of the couch?",
                  question_date=Q_DATE)
    assert calls["n"] == 1
    assert res["evidence"] == GOLD_FIRED_CURRENT
    # reader-raise-on-fired: evict the per-namespace cached reader, then the
    # NEXT call raises AskReaderUnavailable — ZERO legacy re-run (the fts
    # AssertionError would surface if retrieval ever re-ran)
    cache = sdk_mod._ask_reader_cache()
    cache.pop(f"ask:{getattr(sdk, '_namespace', 'default')}", None)
    raise_on["n"] = 1
    with pytest.raises(AskReaderUnavailable):
        sdk.ask("what is the current status of the couch?",
                question_date=Q_DATE)
    assert calls["n"] == 2  # no second reader attempt, no legacy re-run


def test_w4_both_flags_why_on_real_rows(sdk, monkeypatch):
    """Both flags on (W4 + assembly): with enrichment attached, why survives
    on REAL evidence points (entries carry the point ids the reader saw);
    synthesized no-id rows contribute none. With enrich_items RAISING the
    fired path degrades TYPED (response still returns; why empty) — never a
    raw raise or partial block."""
    import tortoise.why as why_mod
    ag.build_base_graph(sdk)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    _install_fake(sdk, monkeypatch, reply="GOLD")

    # (b) enrich attaches a why block to every REAL (id-carrying) row
    def _attach(proj, items):
        out = []
        for item in items:
            if not item.get("id"):
                out.append(item)
                continue
            enriched = dict(item)
            enriched["why"] = {"support_chain": []}
            out.append(enriched)
        return out

    monkeypatch.setattr(why_mod, "enrich_items", _attach)
    res = sdk.ask("what is the current status of the couch?",
                  question_date=Q_DATE)
    assert res["evidence"] == GOLD_FIRED_CURRENT  # why keys never alter text
    why = res.get("why", [])
    assert why, "W4 must attach entries on the fired path"
    # every entry's point_id belongs to a REAL evidence row (pure assembly
    # read of the same fired block — no reader); synthesized no-id lines
    # never produced an entry
    aa = sdk.ask_assembled("what is the current status of the couch?",
                           question_date=Q_DATE)
    ev_ids = {h.get("id") for h in aa.post_cap_lines if h.get("id")}
    assert all(e.get("point_id") in ev_ids for e in why), why
    assert len(why) <= 4

    # (a) enrich RAISES -> typed degrade: response returns, no why keys, no
    # partial block
    def _boom_enrich(proj, items):
        raise RuntimeError("w4 exploded")

    monkeypatch.setattr(why_mod, "enrich_items", _boom_enrich)
    res2 = sdk.ask("what is the current status of the couch?",
                   question_date=Q_DATE)
    assert res2["evidence"] == GOLD_FIRED_CURRENT
    assert res2.get("why") == []


def test_qtype_on_the_wire_unchanged(sdk, monkeypatch):
    """qtype on the wire reports the caller/detector value unchanged — the
    branch never rewrites it."""
    from tortoise.reader import detect_question_type
    ag.build_base_graph(sdk)
    res = _ask(sdk, monkeypatch, "what is the current status of the couch?",
               flag_on=True, question_type="knowledge-update")
    assert res["question_type"] == "knowledge-update"
    res2 = _ask(sdk, monkeypatch, "what is the current status of the couch?",
                flag_on=True)
    assert res2["question_type"] == detect_question_type(
        "what is the current status of the couch?")


def test_no_successor_record_name_only_and_degraded_false(sdk, monkeypatch):
    """Orphan successor + torn superseded row: name-only annotations (never
    a fabricated link); retrieval_degraded=False on the fired path (the D8
    gate sees [] — an empty supersededBy row can never flip it)."""
    ag.build_base_graph(sdk)
    ag.build_supersession_chain_variants(sdk)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    _install_fake(sdk, monkeypatch, reply="GOLD")
    res = sdk.ask("what is the current status of the orphan-src?",
                  question_date=Q_DATE)
    assert "STATE (orphan-src): superseded by successor-never-created" in \
        res["evidence"]
    assert "no successor record found" in res["evidence"]
    assert res["retrieval_degraded"] is False, res["evidence"]
    res2 = sdk.ask("what is the current status of the torn-row?",
                   question_date=Q_DATE)
    assert "STATE (torn-row): superseded (successor unknown)" in \
        res2["evidence"]
    assert res2["retrieval_degraded"] is False


def test_malformed_date_row_undated_not_raise(sdk, monkeypatch):
    """Malformed stored date (garbage when + SENTINEL createdAt) anchored to
    a subject → SINGLE pinned outcome: undated-tier row renders, never a
    raise (Task 4 pin carried to the fired surface)."""
    ag.build_base_graph(sdk)
    proj = sdk._get_proj()
    sdk.create_point(
        "statement", "the event with an unparseable date on the record",
        id="pMalformed", session_id="sess-malformed",
        is_episodic=True, status="draft", quote="unparseable date",
        createdAt=ag.UNDATED_SENTINEL, when="not-a-real-date-2026")
    ag._link_point_about(proj, "pMalformed", ["couch"])
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    _install_fake(sdk, monkeypatch, reply="GOLD")
    res = sdk.ask("what is the current status of the couch?",
                  question_date=Q_DATE)
    assert "unparseable date" in res["evidence"]
    assert res["retrieval_degraded"] is False


def test_caps_bind_and_no_starvation(sdk, monkeypatch):
    """Fired caps: assemble_context item cap (40) + 8000-token + 32 KiB byte
    caps bind post-CAP lines; slice-level truncation is authoritative BEFORE
    the item cap (admission.truncated on the hub)."""
    ag.build_base_graph(sdk)
    ag.build_hub_graph(sdk, n_points=60)
    aa = sdk.ask_assembled(
        "which came first - the hub-subject or the dog bed?",
        question_date=Q_DATE)
    assert aa.fired is True
    assert aa.admission.get("truncated") is True  # hub slice capped at fetch
    assert aa.admission.get("rows_requested") == 80  # 40 x 2 subjects
    assert len(aa.post_cap_lines) <= 40, len(aa.post_cap_lines)
    assert aa.context_tokens <= 8000
    assert len(aa.evidence.encode("utf-8")) <= 32768
    # the quiet co-subject (dog bed) is never starved by the hub
    texts = [h.get("content") or "" for h in aa.post_cap_lines]
    assert any("dog bed cushion" in t for t in texts), "quiet subject rows"


def test_no_starvation_whole_hit_skip_survivor(sdk, monkeypatch):
    """NO-STARVATION SURVIVOR SEMANTICS: an over-budget MID-list row is
    whole-hit DROPPED (token skip) while a LATER row after it is still
    admitted — never a strict tail drop (assemble_context contract)."""
    ag.build_base_graph(sdk)
    proj = sdk._get_proj()
    sdk.create_point(
        "statement", "HUGEBLOCK " + ("zzz filler detail " * 6000),
        id="pGiant", session_id="sess-giant", is_episodic=True,
        status="draft", createdAt="2026-08-11")
    ag._link_point_about(proj, "pGiant", ["couch"])
    aa = sdk.ask_assembled(
        "which came first - the couch or the dog bed?", question_date=Q_DATE)
    assert aa.fired is True
    texts = [h.get("content") or "" for h in aa.post_cap_lines]
    # the giant 6k-word row (>8000-token budget alone) is skipped
    assert not any("HUGEBLOCK" in t for t in texts)
    # rows AFTER the giant (couch sold 09-01; dog bed rows) still admitted
    assert any("sold the old couch" in t for t in texts), \
        "later row after an over-budget mid row must survive"
    assert any("dog bed cushion" in t for t in texts)
    assert aa.context_tokens <= 8000


def test_assembly_answer_contract_shape(sdk, monkeypatch):
    """ASK_ASSEMBLED CONTRACT: AssemblyAnswer field shape pinned (Task 7's
    eval arm reads post_cap_lines for gold admission + answer for
    conversion — field names are the arm's contract)."""
    import dataclasses

    from tortoise.assembly import AssemblyAnswer
    ag.build_base_graph(sdk)
    aa = sdk.ask_assembled("what is the current status of the couch?",
                           question_date=Q_DATE)
    fields = {f.name for f in dataclasses.fields(AssemblyAnswer)}
    assert fields == {"fired", "shape", "question_type", "subjects",
                      "slices", "post_cap_lines", "admission", "evidence",
                      "context_tokens", "answer", "retrieval_degraded"}
    assert aa.fired is True and aa.shape == "current-state"
    assert aa.answer is None  # pure-assembly mode (no reader supplied)
    assert isinstance(aa.slices, dict)
    assert {"state_rows", "timeline_rows", "evidence_rows"} <= set(
        aa.slices)
    # reader-supplied mode fills answer via the ONE reader call. The
    # per-namespace reader cache makes the FIRST reader the model for the
    # namespace, so pin the answer against the installed fake's reply.
    _install_fake(sdk, monkeypatch, reply="READER ANSWER")
    aa2 = sdk.ask_assembled("what is the current status of the couch?",
                            question_date=Q_DATE,
                            _reader_factory=lambda: FakeReader(
                                reply="READER ANSWER", tokens_out=6))
    assert aa2.answer == "READER ANSWER"
    aa3 = sdk.ask_assembled("compare the couch and the dog bed, which "
                            "should i keep?", question_date=Q_DATE)
    assert aa3.fired is False and aa3.answer is None and aa3.evidence == ""


def test_hosted_delegated_client_raises(sdk, monkeypatch):
    """TORTOISE_API_URL set (no local graph) → ask_assembled raises
    AskRetrievalUnavailable with a clear message."""
    from tortoise.exceptions import AskRetrievalUnavailable
    monkeypatch.setenv("TORTOISE_API_URL", "https://example.test")
    with pytest.raises(AskRetrievalUnavailable):
        sdk.ask_assembled("what is the current status of the couch?")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)


def test_contentless_resolved_subjects_legacy_fallback(sdk, monkeypatch):
    """P1-1: both halves RESOLVE but the subjects have no points/events
    (nothing for the assembled block to say) -> fired=False, legacy runs —
    the ONE reader call is never burned on an empty fired block."""
    sdk.create_entity("object", "alpha", objectKind="core:furniture",
                      is_episodic=True)
    sdk.create_entity("object", "beta", objectKind="core:furniture",
                      is_episodic=True)
    q = "which came first - the alpha or the beta?"
    off = _ask(sdk, monkeypatch, q, flag_on=False)
    on = _ask(sdk, monkeypatch, q, flag_on=True)
    # fired-but-empty must fall through to legacy (no empty-block reader call)
    assert on["evidence"] == off["evidence"]
    aa = sdk.ask_assembled(q, question_date=Q_DATE)
    assert aa.fired is False


def test_raw_object_hit_flat_superseded_by_guard(sdk, monkeypatch):
    """RAW-OBJECT-HIT GUARD: a flat-string superseded_by forced into the
    assembled list degrades TYPED (AskRetrievalUnavailable via the fired
    envelope) — NEVER an AttributeError leaking to the caller."""
    import tortoise.assembly as amod
    from tortoise.exceptions import AskRetrievalUnavailable
    ag.build_base_graph(sdk)
    _orig = amod.synthesize_hits

    def _poison(slices, *, shape, candidates=(), halves=(),
                successors_verified=frozenset()):
        hits = _orig(slices, shape=shape, candidates=candidates,
                     halves=halves, successors_verified=successors_verified)
        hits.append({"content": "raw object row",
                     "superseded_by": "sofa"})  # FLAT — never emitted shape
        return hits

    monkeypatch.setattr(amod, "synthesize_hits", _poison)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    with pytest.raises(AskRetrievalUnavailable):
        sdk.ask("what is the current status of the couch?",
                question_date=Q_DATE)


def test_r14_drift_guard(sdk, monkeypatch):
    """R14: _assemble_connected is referenced ONLY by ask()'s branch and
    ask_assembled (exactly two CALL sites in sdk.py)."""
    src = Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py"
    text = src.read_text()
    assert text.count("_assemble_connected(") == 2, \
        text.count("_assemble_connected(")
