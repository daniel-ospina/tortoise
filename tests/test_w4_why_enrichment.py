"""W4 why-layer enrichment tests — issue #2101 (epic #2080, DM-1).

Covers the S6 (structured contract on existing recall surfaces), S8 (EP
fast-path reads) and S15 (ontology vocabulary) surfaces of the #2093
integration-surface map, exercising E2E-1 (why-layer conflict-surfacing)
and E2E-6 (supersession-aware recall) acceptance targets on the search /
analyze / ask / MCP recall_state surfaces.

The docker lane (TORTOISE_DB_URI) is the default test backend; these tests
run against real FalkorDB via the hermetic per-session graph redirect in
tests/conftest.py. The W4 flag (TORTOISE_W4_ENRICHMENT) is set per test —
flag-off byte-identical emission is asserted explicitly.

Contract under test (plan §3.1.1/§3.1.3/§3.1.4/§6.1 + issue #2101):
- Additive-only keys, backward compatible (#1353 D8); flag-off byte-identical.
- Budgets: ≤3 supports, ≤2 conflicts, 1 supersession line, ≤3 dig-deeper.
- Deterministic dig-deeper labels {label, kind, target}, kinds
  supports|nand|superseded|tradeoff (ONTOLOGY §5).
- Empty/null/absent conventions (§3.1.3): null = unset scalar, [] = empty
  collection, absent key = dimension not computed; clean empty is never a
  degradation; degradations fail-open (never break the recall turn).
- Flag-first (UXD 3): warnings + contested top-of-item; conflicts before
  trade-offs/dig-deeper; warnings derived from ep.contested/variance.
- Bounded reads (S8): fixed batch query count — never a per-result loop,
  never full EP propagation on the hot path.
- E2E-1's ranking-participation / flag-off golden-ordering assertions
  (When-3 / Then) are S7-scoped (the ranking boost) and are NOT exercised
  here — this file pins surfacing, additive-key presence + the flag-off
  byte-identical side only.
- E2E-6 bullet 4's bi-temporal human label ("replaced <date>") is the
  §3.1.3 human render; the §6.1 agent view ({line, successor_label})
  carries no date — this file pins the flat valid_to window-end stamp
  riding the enriched hit (canonical flat keys stay canonical).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import search_engine
from tortoise.sdk import (  # noqa: E402, RUF100
    TortoiseSDK,
    _reset_ask_reader_cache_for_tests,
)
from tortoise.why import (
    DIG_DEEPER_KINDS,
    DIG_DEEPER_LABELS,
    W4_MAX_CONFLICTS,
    W4_MAX_DIG_DEEPER,
    W4_MAX_SUPPORT,
    assemble_why_blocks,
    enrich_items,
    item_to_why_entry,
    point_ids_in_raw,
    w4_enrichment_enabled,
)

W4_KEYS = ("warnings", "why", "conflicts", "supersession", "tradeoffs", "dig_deeper")

# Canonical ask-lane response keys (the flag-OFF byte-identical shape; the
# flag-ON shape adds ONLY the "why" key — additive-only, #1353 D8).
CANONICAL_ASK_KEYS = frozenset({
    "answer", "abstained", "question_type", "question_date", "evidence",
    "context_tokens", "model", "provider", "route", "cost_estimate_usd",
    "duration_ms", "retrieval_degraded",
})


# ── Fixtures / helpers ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_ask_state():
    """Reset the shared ask-reader cache between tests (ask-lane hygiene)."""
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


@pytest.fixture
def w4_flag(monkeypatch):
    """W4 flag ON for the test body; restored after."""
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    yield
    monkeypatch.delenv("TORTOISE_W4_ENRICHMENT", raising=False)


def _fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_w4_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:  # noqa: SIM105
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _set_posterior(sdk, pid: str, alpha: float, beta: float):
    """Persist EP posterior params the way compute_confidence does (n.confidence
    = posterior mean; posterior_alpha/beta for variance/contested)."""
    mean = round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c, "
        "n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta, "c": mean},
    )


def _plant_conflicted(sdk, topic: str, alpha: float = 2.0, beta: float = 2.0,
                      kind: str = "statement") -> dict:
    """Plant one conflicted claim: 1 IMPL support + 1 NAND counterargument,
    balanced persisted posterior (variance 0.05 > threshold → contested).
    Returns the claim + supporting + counterargument ids."""
    ev = sdk.create_point("evidence", f"{topic} supporting record alpha")
    claim = sdk.create_point(kind, f"{topic} belief statement")
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    counter = sdk.create_point("statement", f"{topic} counterargument gamma")
    sdk.create_operator("NAND", counter["id"], [claim["id"]])
    _set_posterior(sdk, claim["id"], alpha, beta)
    _set_posterior(sdk, ev["id"], 12.0, 1.0)
    _set_posterior(sdk, counter["id"], 6.0, 1.0)
    return {"claim": claim["id"], "support": ev["id"], "counter": counter["id"]}


def _plant_variance_contested_no_nand(sdk, topic: str) -> dict:
    """Plant one variance-contested claim with ZERO NAND edges: a single
    IMPL support + balanced persisted posterior (alpha=beta=2.0 → variance
    0.05 > threshold) and NO counterargument. The contestation signal comes
    purely from the persisted ep variance — the mirror of the
    NANDed-but-uncontested quadrant."""
    ev = sdk.create_point("evidence", f"{topic} supporting record alpha")
    claim = sdk.create_point("statement", f"{topic} belief statement")
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    _set_posterior(sdk, claim["id"], 2.0, 2.0)      # v=0.05 > threshold
    _set_posterior(sdk, ev["id"], 12.0, 1.0)
    return {"claim": claim["id"], "support": ev["id"]}


def _plant_clean(sdk, topic: str) -> dict:
    """Plant one clean claim: 2 IMPL supports, high-support posterior
    (variance ≈ 0.005 → NOT contested), no NANDs anywhere."""
    ev1 = sdk.create_point("evidence", f"{topic} clean record one")
    ev2 = sdk.create_point("evidence", f"{topic} clean record two")
    claim = sdk.create_point("statement", f"{topic} clean belief statement")
    sdk.create_operator("IMPL", ev1["id"], [claim["id"]])
    sdk.create_operator("IMPL", ev2["id"], [claim["id"]])
    _set_posterior(sdk, claim["id"], 12.0, 1.0)
    _set_posterior(sdk, ev1["id"], 12.0, 1.0)
    _set_posterior(sdk, ev2["id"], 12.0, 1.0)
    return {"claim": claim["id"], "supports": [ev1["id"], ev2["id"]]}


def _plant_decision(sdk, topic: str) -> dict:
    """Plant one decision point: kind=decision + 2 option alternatives
    (IMPL via operators) + a mitigation on each connecting operator, all
    within a conflicted structure (variance > threshold)."""
    ev = sdk.create_point("evidence", f"{topic} decision support record")
    dec = sdk.create_point("decision", f"{topic} decision point")
    opt1 = sdk.create_point("option", f"{topic} alternative one")
    opt2 = sdk.create_point("option", f"{topic} alternative two")
    counter = sdk.create_point("statement", f"{topic} decision counterargument")
    sdk.create_operator("IMPL", ev["id"], [dec["id"]])
    op1 = sdk.create_operator("IMPL", dec["id"], [opt1["id"]])
    op2 = sdk.create_operator("IMPL", dec["id"], [opt2["id"]])
    sdk.create_operator("NAND", counter["id"], [dec["id"]])
    sdk.mitigate_operator(op1["id"], "QA gate + staged rollout")
    sdk.mitigate_operator(op2["id"], "communicate the delay")
    _set_posterior(sdk, dec["id"], 2.0, 2.0)
    _set_posterior(sdk, opt1["id"], 4.0, 1.0)
    _set_posterior(sdk, opt2["id"], 5.0, 2.0)
    _set_posterior(sdk, ev["id"], 12.0, 1.0)
    _set_posterior(sdk, counter["id"], 6.0, 1.0)
    return {"decision": dec["id"], "options": [opt1["id"], opt2["id"]]}


def _plant_superseded(sdk, topic: str) -> dict:
    """Plant one superseded predecessor: conflicted structure (IMPL + NAND,
    variance > threshold) PLUS a CORRECTS edge from a successor and
    status='superseded'. Edges are NOT transferred (the E2E-1 corpus keeps
    the predecessor's conflict structure — supersession and conflict are
    independent dimensions; edge transfer would empty the conflicted
    denominator E2E-1 measures). This raw-state seeding is the E2E-1 corpus
    fixture only — the REAL supersede_point write path (status stamp,
    CORRECTS direction, validTo window end) is exercised in
    test_e2e6_supersession_aware_recall. Returns the predecessor id +
    successor id."""
    planted = _plant_conflicted(sdk, topic)
    old = planted["claim"]
    succ = sdk.create_point("statement", f"{topic} successor belief")
    _set_posterior(sdk, succ["id"], 10.0, 1.0)
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
        "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
        "n.outdated=true, n.validTo='2026-06-01'",
        params={"old": old, "new": succ["id"]},
    )
    return {"old": old, "successor": succ["id"]}


def _search_for(sdk, token: str, limit: int = 10, include_terminal: bool = False) -> list[dict]:
    """Search a distinctive token and return the hits (enriched on the search
    surface with the W4 flag ON)."""
    return sdk.tortoise_fts_query(
        token, limit=limit, include_terminal=include_terminal)


def _recall_all_points(sdk) -> list[dict]:
    """Full-scan retrieval of every Point (kinds used by the corpus) — the
    deterministic retrieval lens for the E2E-1 corpus assertions. NOTE: the
    retrieval RANKING (E2E-1 When-3 ranking participation + flag-off golden
    ordering) is S7-scoped and NOT exercised here — the flag-drift / surface
    tests pin hit PRESENCE + additive-key presence only. This helper
    isolates the ENRICHMENT-surfacing measurement from FTS index staleness
    on the shared session graph (bulk wipes can lag the index)."""
    hits: list[dict] = []
    for kind in ("statement", "decision"):
        hits.extend(sdk.tortoise_fts_query(
            None, kind=kind, limit=1000, include_terminal=True))
    return hits


def _hit_by_id(hits: list[dict], pid: str) -> dict | None:
    for h in hits:
        if h["id"] == pid:
            return h
    return None


# ── S6 contract: flag gating + additive-only backward compat ──────────────

def test_flag_off_emission_byte_identical(monkeypatch):
    """Flag OFF ⇒ NO W4 keys on any surface (byte-identical to today).
    The recall legs locate the seeded claim FIRST (a vacuous absence loop
    over an empty result would pass a surface that silently dropped the
    claim) and then assert absence on that hit + the full result window."""
    monkeypatch.delenv("TORTOISE_W4_ENRICHMENT", raising=False)
    assert w4_enrichment_enabled() is False
    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "flag-off-topic")
        hits = _search_for(sdk, "flag-off-topic")
        claim_hit = _hit_by_id(hits, g["claim"])
        assert claim_hit is not None, "fixture must retrieve"
        for k in W4_KEYS:
            assert k not in claim_hit, f"flag-off leak: {k}"
        # Existing keys untouched.
        assert "ep" in claim_hit and "content" in claim_hit
        # recall_state surface — locate the claim in the window first.
        rec = sdk.recall_state(query="flag-off-topic", limit=10)
        rec_hit = _hit_by_id(rec, g["claim"])
        assert rec_hit is not None, "recall_state must retrieve the claim"
        for k in W4_KEYS:
            assert k not in rec_hit, f"recall flag-off leak: {k}"
        for h in rec:
            for k in W4_KEYS:
                assert k not in h, f"recall flag-off leak: {k}"
        # ask surface: 12-field response WITHOUT the why key; the reader is
        # called EXACTLY once (zero-LLM — enrichment adds no reader calls).
        import tortoise.sdk as sdk_mod
        fake = _FakeReader()
        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
        _reset_ask_reader_cache_for_tests()
        resp = sdk.ask("flag-off-topic belief statement?")
        assert set(resp) == CANONICAL_ASK_KEYS
        assert fake.calls == 1, f"flag-off ask made {fake.calls} reader calls — expected 1"
    finally:
        sdk.close()


def test_flag_on_emits_contract_on_search_surface(w4_flag):
    """Flag ON ⇒ the search surface emits the additive keys with the
    §3.1.1/§6.1 shapes: warnings top-of-item, why.support_chain ≤3,
    conflicts (contested + nands ≤2), supersession view, dig_deeper ≤3."""
    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "contract-topic")
        hits = _search_for(sdk, "contract-topic")
        claim_hit = _hit_by_id(hits, g["claim"])
        assert claim_hit is not None
        # Flag-first: warnings is TOP-OF-ITEM — the documented position rides
        # directly after content and BEFORE point_kind (§3.1.1/UXD 3 — a
        # top-down parser sees the dispute first), pinned exactly rather than
        # the weaker before-ep ordering.
        keys = list(claim_hit.keys())
        assert claim_hit.get("warnings") == ["contested"]
        assert keys.index("warnings") == keys.index("content") + 1, \
            "warnings must ride directly after content (flag-first, UXD 3)"
        assert keys.index("warnings") < keys.index("point_kind"), \
            "warnings must precede point_kind (flag-first)"
        assert keys.index("warnings") < keys.index("ep"), \
            "warnings must precede ep (flag-first)"
        # why block (≤3 supports, shape).
        why = claim_hit.get("why") or {}
        chain = why.get("support_chain") or []
        assert 1 <= len(chain) <= W4_MAX_SUPPORT
        for s in chain:
            assert set(s) == {"point_id", "content_snippet", "edge", "weight"}
            assert s["edge"] == "IMPL"
        # conflicts (contested + nands ≤2).
        conflicts = claim_hit.get("conflicts")
        assert conflicts is not None
        assert conflicts["contested"] is True
        assert 1 <= len(conflicts["nands"]) <= W4_MAX_CONFLICTS
        for n in conflicts["nands"]:
            assert set(n) == {"point_id", "content_snippet", "severity"}
            assert n["severity"] in ("high", "medium")
        # supersession view (1 line, full-enum projection) — value-pinned
        # on the healthy path: line mirrors the item's flat status and a
        # non-superseded claim carries successor_label None (§3.1.3 null
        # convention for an unset scalar).
        ss = claim_hit.get("supersession")
        assert ss is not None and "line" in ss and "successor_label" in ss
        assert ss["line"] == (claim_hit.get("status") or "live"), \
            "supersession line must mirror the flat canonical status on the healthy path"
        assert ss["successor_label"] is None, \
            "a non-superseded claim carries successor_label null (§3.1.3)"
        # dig_deeper (≤3, deterministic {label, kind, target}).
        dd = claim_hit.get("dig_deeper") or []
        assert 1 <= len(dd) <= W4_MAX_DIG_DEEPER
        for p in dd:
            assert set(p) == {"label", "kind", "target"}
            assert p["kind"] in DIG_DEEPER_KINDS
        # Conflict-first: conflicts before trade-offs/dig-deeper in the item.
        assert keys.index("conflicts") < keys.index("dig_deeper")
    finally:
        sdk.close()


def test_clean_points_carry_no_conflict_noise(w4_flag):
    """S6 all-fields-optional: an uncontested point carries no conflict
    noise — no warnings, no conflicts, no tradeoffs, no nand pointers."""
    sdk = _fresh_sdk()
    try:
        g = _plant_clean(sdk, "clean-topic")
        hits = _search_for(sdk, "clean-topic")
        claim_hit = _hit_by_id(hits, g["claim"])
        assert claim_hit is not None
        assert "warnings" not in claim_hit
        assert "conflicts" not in claim_hit
        assert "tradeoffs" not in claim_hit
        dd = claim_hit.get("dig_deeper") or []
        assert all(p["kind"] != "nand" for p in dd), "clean point must not surface a NAND pointer"
        # why + supersession are computed dimensions (present, harmless).
        assert "supersession" in claim_hit
        assert claim_hit["supersession"]["successor_label"] is None
    finally:
        sdk.close()


def test_deterministic_dig_deeper_labels(w4_flag):
    """UXD 4: dig-deeper labels are deterministic verb phrases from the kind
    registry — never LLM prose, never content-derived."""
    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "labels-topic")
        hits = _search_for(sdk, "labels-topic")
        claim_hit = _hit_by_id(hits, g["claim"])
        for p in claim_hit["dig_deeper"]:
            assert p["label"] == DIG_DEEPER_LABELS[p["kind"]], \
                "label must be the registry verb phrase for the kind"
    finally:
        sdk.close()


def test_dig_deeper_cap_and_tradeoff_precedence(w4_flag):
    """§3.1.4 pin: a point carrying ALL FOUR pointer candidates (supports +
    nand + tradeoff + superseded) truncates to the ≤3 cap with the pinned
    precedence (supports → nand → tradeoff → superseded) — a decision point
    never loses its tradeoff pointer to a superseded one. The E2E-1 corpus
    is disjoint-clean by construction (decision vs superseded subsets), so
    only a combined structure can exercise the cap + the precedence rule."""
    sdk = _fresh_sdk()
    try:
        # A superseded decision point: supports + nand + tradeoff (decision
        # structure) + superseded (incoming CORRECTS) = 4 candidates. Raw
        # CORRECTS is deliberate here — supersede_point's edge transfer would
        # strip the decision's outgoing alternatives before the read; the
        # tradeoffs dimension is a read-path projection and the WRITE path is
        # exercised in test_e2e6_supersession_aware_recall.
        d = _plant_decision(sdk, "precedence-topic")
        succ = sdk.create_point("statement", "precedence-topic successor belief")
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
            "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
            "n.outdated=true, n.validTo='2026-08-01'",
            params={"old": d["decision"], "new": succ["id"]},
        )
        hits = _search_for(sdk, "precedence-topic", limit=20,
                           include_terminal=True)
        d_hit = _hit_by_id(hits, d["decision"])
        assert d_hit is not None, "superseded decision point must be retrievable"
        dd = d_hit.get("dig_deeper") or []
        assert len(dd) == W4_MAX_DIG_DEEPER, \
            f"4 candidates must truncate to the ≤{W4_MAX_DIG_DEEPER} cap, got {len(dd)}"
        assert [p["kind"] for p in dd] == ["supports", "nand", "tradeoff"], \
            "precedence supports → nand → tradeoff → superseded must drop the superseded pointer"
    finally:
        sdk.close()


def test_warnings_derived_from_ep_contested(w4_flag):
    """§4.2: warnings = ['contested'] exactly when the item's ep is
    contested (has_ep AND variance > threshold) — absent otherwise.

    Three quadrants pin the derivation rule's DISCRIMINATING cases:
      - contested (NAND + variance > threshold)      → warnings present;
      - clean (no NAND + variance ≤ threshold)       → warnings absent;
      - NANDed-but-uncontested (active NAND + variance ≤ threshold)
        → conflicts {contested: False, nands: [...]} + a nand dig-deeper
        pointer, but warnings ABSENT. Without this quadrant the test passes
        even if warnings were keyed on 'any NAND present'."""
    sdk = _fresh_sdk()
    try:
        g_conf = _plant_conflicted(sdk, "warn-conf", alpha=1.5, beta=1.5)   # v≈0.0625
        g_clean = _plant_clean(sdk, "warn-clean")
        # NANDed-but-uncontested: _plant_conflicted adds an active NAND;
        # alpha=12/beta=1 → variance ≈ 0.0051 ≤ threshold → contested False.
        g_nanded = _plant_conflicted(sdk, "warn-nanded", alpha=12.0, beta=1.0)
        hits = _search_for(sdk, "warn-conf") + _search_for(sdk, "warn-clean") \
            + _search_for(sdk, "warn-nanded")
        conf_hit = _hit_by_id(hits, g_conf["claim"])
        clean_hit = _hit_by_id(hits, g_clean["claim"])
        nanded_hit = _hit_by_id(hits, g_nanded["claim"])
        assert nanded_hit is not None, "NANDed fixture must retrieve"
        assert conf_hit["ep"]["contested"] is True
        assert conf_hit.get("warnings") == ["contested"]
        assert clean_hit["ep"]["contested"] is False
        assert "warnings" not in clean_hit
        # Discriminating quadrant: NAND present, variance ≤ threshold → the
        # dispute still surfaces (conflicts.contested False + nand pointer)
        # but warnings MUST be absent (warnings derive from ep.contested,
        # not from the mere presence of a NAND).
        assert nanded_hit["ep"]["contested"] is False
        assert nanded_hit.get("warnings") is None, \
            "warnings must not key on mere NAND presence"
        nanded_conflicts = nanded_hit.get("conflicts")
        assert nanded_conflicts is not None, \
            "a NANDed point surfaces conflicts even when uncontested"
        assert nanded_conflicts["contested"] is False
        assert len(nanded_conflicts["nands"]) >= 1
        nanded_dd = nanded_hit.get("dig_deeper") or []
        assert any(p["kind"] == "nand" for p in nanded_dd), \
            "dig_deeper must still carry the nand pointer"

        # Fourth quadrant — the mirror discriminating case: a variance-
        # CONTESTED point with ZERO active NAND edges (the contested signal
        # comes purely from persisted ep variance). warnings MUST fire and
        # conflicts {contested: True, nands: []} MUST be emitted (the §3.1.3
        # empty-collection-inside-conflicts value) — a regression keying
        # warnings/conflicts on NAND presence AS A NECESSARY CONDITION
        # passes the other three quadrants but fails here.
        g_var = _plant_variance_contested_no_nand(sdk, "warn-var")
        v_hits = _search_for(sdk, "warn-var")
        var_hit = _hit_by_id(v_hits, g_var["claim"])
        assert var_hit is not None, "variance-contested fixture must retrieve"
        assert var_hit["ep"]["contested"] is True
        assert var_hit.get("warnings") == ["contested"], \
            "contested signal from variance alone must fire warnings"
        var_conflicts = var_hit.get("conflicts")
        assert var_conflicts is not None and var_conflicts["contested"] is True
        assert var_conflicts["nands"] == [], \
            "zero-NAND contested point carries the empty nands collection"
        var_dd = var_hit.get("dig_deeper") or []
        assert all(p["kind"] != "nand" for p in var_dd), \
            "no nand pointer without a NAND edge"
    finally:
        sdk.close()


def test_budgets_enforced(w4_flag):
    """Budgets: ≤3 supports, ≤2 conflicts, ≤3 dig-deeper even when the graph
    carries more structure."""
    sdk = _fresh_sdk()
    try:
        # 5 supports + 5 NANDers on one claim.
        claim = sdk.create_point("statement", "budget-topic belief statement")
        for i in range(5):
            ev = sdk.create_point("evidence", f"budget-topic support record {i}")
            sdk.create_operator("IMPL", ev["id"], [claim["id"]])
        for i in range(5):
            cntr = sdk.create_point("statement", f"budget-topic counterargument {i}")
            sdk.create_operator("NAND", cntr["id"], [claim["id"]])
        _set_posterior(sdk, claim["id"], 2.0, 2.0)
        # limit=50: the 10 planted neighbors all match the topic token — the
        # claim must still be inside the window (FTS index lag on the shared
        # session graph can otherwise push it out of a small limit).
        hits = _search_for(sdk, "budget-topic", limit=50)
        claim_hit = _hit_by_id(hits, claim["id"])
        assert claim_hit is not None, "budget claim must be retrieved"
        # Exact-cap binding (S6): the fixture OVER-provisions (5 supports +
        # 5 NANDers), so the truncation path must cut to exactly the caps —
        # an upper-bound-only assertion would pass a regression that returns
        # 1 support or 0 NANDs.
        assert len(claim_hit["why"]["support_chain"]) == W4_MAX_SUPPORT
        assert len(claim_hit["conflicts"]["nands"]) == W4_MAX_CONFLICTS
        dd = claim_hit["dig_deeper"]
        assert 1 <= len(dd) <= W4_MAX_DIG_DEEPER
        # Deterministic selection (weight-desc / severity-first, then id):
        # the over-provisioned fixture deterministically yields exactly two
        # pointers (supports + nand) — pinned UNCONDITIONALLY (a guarded
        # assertion would silently pass if the nand pointer were dropped).
        assert [p["kind"] for p in dd] == ["supports", "nand"], \
            "dig_deeper must surface the supports + nand pointers (selection drop = regression)"
        assert dd[0]["target"] == claim_hit["why"]["support_chain"][0]["point_id"]
        assert dd[1]["target"] == claim_hit["conflicts"]["nands"][0]["point_id"]
    finally:
        sdk.close()


# ── S6: flag-drift — the additive keys ride ALL FOUR surfaces ─────────────

class _FakeReader:
    """complete() stub for the ask lane (local-lane tests)."""

    def __init__(self, reply: str = "The security review was due May 1."):
        self.reply = reply
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.reply

    def close(self) -> None:
        pass


def _mcp_search(sdk, query: str, **kw) -> list:
    import tortoise.mcp_server as ms
    from tortoise.mcp_auth import _transport_mode
    _token = _transport_mode.set("stdio")
    ms.sdk = sdk
    try:
        return ms.tortoise_search(query, **kw)
    finally:
        _transport_mode.reset(_token)
        ms.sdk = None


def _mcp_recall(sdk, query: str, **kw) -> dict:
    import tortoise.mcp_server as ms
    from tortoise.mcp_auth import _transport_mode
    _token = _transport_mode.set("stdio")
    ms.sdk = sdk
    try:
        return ms.tortoise_recall(query=query, mode="state", **kw)
    finally:
        _transport_mode.reset(_token)
        ms.sdk = None


def _mcp_analyze(sdk, question: str) -> dict:
    import tortoise.mcp_server as ms
    from tortoise.mcp_auth import _transport_mode
    _token = _transport_mode.set("stdio")
    ms.sdk = sdk
    try:
        return ms.tortoise_analyze(question)
    finally:
        _transport_mode.reset(_token)
        ms.sdk = None


def _mcp_ask(sdk, question: str) -> dict:
    import tortoise.mcp_server as ms
    from tortoise.mcp_auth import _transport_mode
    _token = _transport_mode.set("stdio")
    ms.sdk = sdk
    try:
        return asyncio.run(ms.tortoise_ask(question))
    finally:
        _transport_mode.reset(_token)
        ms.sdk = None


def test_flag_drift_all_four_surfaces(w4_flag, monkeypatch):
    """E2E-1 flag-drift: a contested point recalled through ALL FOUR enriched
    surfaces (tortoise_search / tortoise_analyze / tortoise_ask / MCP
    recall_state) surfaces the additive keys on every surface — a surface
    that drops them is flag drift."""
    import tortoise.sdk as sdk_mod
    fake = _FakeReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    _reset_ask_reader_cache_for_tests()

    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "drift-topic")
        token = "drift-topic"

        # 1. tortoise_search (MCP).
        search_hits = _mcp_search(sdk, token, limit=10)
        claim_hit = _hit_by_id(search_hits, g["claim"])
        assert claim_hit is not None, "search must retrieve the point"
        for k in ("warnings", "why", "conflicts", "supersession", "dig_deeper"):
            assert k in claim_hit, f"search surface dropped {k}"
        # Additive-only (backward compat, #1353 D8): the contested rebuild in
        # project_item must PRESERVE the pre-existing keys and values.
        assert claim_hit["id"] == g["claim"]
        assert claim_hit["content"] == "drift-topic belief statement", \
            "flag-on enrichment must preserve the original content"

        # 2. MCP recall_state (mode=state).
        rec = _mcp_recall(sdk, token, limit=10)
        assert "error" not in rec
        rec_hit = _hit_by_id(rec.get("results", []), g["claim"])
        assert rec_hit is not None, "recall_state must retrieve the point"
        for k in ("warnings", "why", "conflicts", "supersession", "dig_deeper"):
            assert k in rec_hit, f"recall_state dropped {k}"

        # 3. tortoise_analyze (MCP) — the additive keys ride the why entries.
        ana = _mcp_analyze(sdk, "where is the disagreement?")
        assert ana.get("pattern") == "disagreement"
        why = ana.get("why") or []
        entry = next((e for e in why if e.get("point_id") == g["claim"]), None)
        assert entry is not None, "analyze why must carry the claim's block"
        for k in ("support_chain", "ep", "conflicts", "supersession", "dig_deeper"):
            assert k in entry, f"analyze why entry dropped {k}"
        assert entry["conflicts"]["contested"] is True
        assert any(p["kind"] == "nand" for p in entry.get("dig_deeper", []))

        # 4. tortoise_ask (MCP) — the why key rides the response; additivity
        # is symmetric with the flag-off pin (canonical 12 + why only); the
        # reader is called EXACTLY once (zero-LLM — enrichment adds zero
        # reader calls). The ask why entry carries the DISPUTE CONTENT for
        # the contested point (mirroring the analyze-surface assertions) —
        # not just the point's presence.
        ask = _mcp_ask(sdk, "what contradicted the drift-topic belief statement?")
        assert set(ask) == CANONICAL_ASK_KEYS | {"why"}, \
            "flag-on ask must add only the why key (additive-only)"
        assert fake.calls == 1, \
            f"ask lane made {fake.calls} reader calls — W4 enrichment must add zero (zero-LLM)"
        ask_why = ask.get("why") or []
        assert ask_why, "ask surface must emit why entries with the flag ON"
        ask_entry = next((e for e in ask_why if e.get("point_id") == g["claim"]), None)
        assert ask_entry is not None, "ask why must include the contested point's block"
        assert ask_entry["conflicts"]["contested"] is True, \
            "the dispute must ride the ask why entry (E2E-1 surfaced-context)"
        assert any(p["kind"] == "nand" for p in ask_entry.get("dig_deeper", [])), \
            "ask why entry must carry the nand dig-deeper pointer"
    finally:
        sdk.close()


def test_flag_off_mcp_surfaces_byte_identical(monkeypatch):
    """Flag OFF ⇒ the MCP surfaces emit no W4 keys (byte-identical). The
    search leg locates the seeded claim FIRST (absence loops over empty
    results pass vacuously — a surface that dropped the claim would leak)."""
    monkeypatch.delenv("TORTOISE_W4_ENRICHMENT", raising=False)
    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "off-mcp-topic")
        search_hits = _mcp_search(sdk, "off-mcp-topic", limit=10)
        claim_hit = _hit_by_id(search_hits, g["claim"])
        assert claim_hit is not None, "MCP search must retrieve the claim"
        for k in W4_KEYS:
            assert k not in claim_hit
        rec = _mcp_recall(sdk, "off-mcp-topic", limit=10)
        rec_hit = _hit_by_id(rec.get("results", []), g["claim"])
        assert rec_hit is not None, "MCP recall_state must retrieve the claim"
        for k in W4_KEYS:
            assert k not in rec_hit
        for h in rec.get("results", []):
            for k in W4_KEYS:
                assert k not in h
        ana = _mcp_analyze(sdk, "where is the disagreement?")
        assert "why" not in ana

        # 4. MCP ask — with the flag OFF the why key is absent and the
        # response stays the canonical 12-field shape (byte-identical). The
        # fake reader keeps the lane local (no provider key required).
        import tortoise.sdk as sdk_mod
        fake = _FakeReader()
        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
        _reset_ask_reader_cache_for_tests()
        ask = _mcp_ask(sdk, "what contradicted the off-mcp-topic belief statement?")
        assert set(ask) == CANONICAL_ASK_KEYS, \
            "flag-off MCP ask must stay byte-identical (12-field response)"
    finally:
        sdk.close()


# ── E2E-1: conflict-surfacing rate on the planted 40-point corpus ─────────

def _seed_e2e1_corpus(sdk) -> dict:
    """Deterministic operator-seeding corpus (E2E-1 Given):

    40 fictional points — 30 conflicted (each: ≥1 IMPL + ≥1 NAND + persisted
    variance > CONTESTED_VARIANCE_THRESHOLD) + 10 clean (high-support
    posterior, no NANDs). Subsets of the 30 (disjoint in this seed, overlaps
    allowed): 10 P9-contested (variance clearly > threshold — pinned by the
    pre-test assertion), 5 decision points (kind=decision, ≥2 option
    alternatives + mitigations), 5 superseded predecessors (CORRECTS +
    status=superseded, conflict structure retained).
    """
    corpus = {"conflicted": [], "p9": [], "decision": [], "superseded": [],
              "clean": []}
    # 10 P9-contested (balanced 1.5/1.5 → variance ≈ 0.0625 >> threshold).
    for i in range(10):
        planted = _plant_conflicted(sdk, f"p9-topic-{i}", alpha=1.5, beta=1.5)
        corpus["conflicted"].append(planted["claim"])
        corpus["p9"].append(planted["claim"])
    # 5 decision points (also conflicted).
    for i in range(5):
        planted = _plant_decision(sdk, f"decision-topic-{i}")
        corpus["conflicted"].append(planted["decision"])
        corpus["decision"].append(planted["decision"])
    # 5 superseded predecessors (also conflicted).
    for i in range(5):
        planted = _plant_superseded(sdk, f"superseded-topic-{i}")
        corpus["conflicted"].append(planted["old"])
        corpus["superseded"].append(planted["old"])
    # 10 more plain conflicted (2.0/2.0 → variance 0.05 > threshold).
    for i in range(10):
        planted = _plant_conflicted(sdk, f"plain-topic-{i}")
        corpus["conflicted"].append(planted["claim"])
    # 10 clean.
    for i in range(10):
        planted = _plant_clean(sdk, f"clean-topic-{i}")
        corpus["clean"].append(planted["claim"])
    assert len(corpus["conflicted"]) == 30
    assert len(corpus["clean"]) == 10
    assert len(corpus["p9"]) == 10
    assert len(corpus["decision"]) == 5
    assert len(corpus["superseded"]) == 5
    return corpus


def _variance_of(sdk, pid: str) -> float:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
        params={"id": pid},
    ).result_set
    a, b = float(rows[0][0]), float(rows[0][1])
    return search_engine._beta_variance(a, b)


def test_e2e1_conflict_surfacing_rate(w4_flag):
    """E2E-1 (headline): on the 40-point planted corpus —

    PRE-TEST: all 10 P9-planted points' persisted variance EXCEEDS
    CONTESTED_VARIANCE_THRESHOLD (calibrated, not aspirational).

    THEN: conflict-surfacing rate ≥ 0.95 of the 30 conflicted points (≥29/30
    surface conflicts.contested:true + ≥1 NAND + a dig-deeper nand pointer);
    0 clean points carry conflict noise; each of the 5 decision points
    surfaces ≥2 alternatives with ep_weight + mitigation."""
    sdk = _fresh_sdk()
    try:
        corpus = _seed_e2e1_corpus(sdk)

        # PRE-TEST assertion (Given): persisted variance above threshold.
        for pid in corpus["p9"]:
            assert _variance_of(sdk, pid) > search_engine.CONTESTED_VARIANCE_THRESHOLD, \
                f"P9-planted point {pid} not calibrated above threshold"

        # When: recall each conflicted point through the search surface.
        surfaced = 0
        all_hits = _recall_all_points(sdk)
        for pid in corpus["conflicted"]:
            # The corpus is recalled through the search surface (full-scan
            # retrieval lens — deterministic; ranking is exercised by the
            # flag-drift tests). include_terminal=True: the 5 superseded
            # corpus points are terminal-status predecessors — recalled
            # through the terminal-inclusive lens (never as current belief).
            claim_hit = _hit_by_id(all_hits, pid)
            assert claim_hit is not None, f"corpus point {pid} not retrieved"
            conflicts = claim_hit.get("conflicts")
            dd = claim_hit.get("dig_deeper") or []
            if (conflicts and conflicts.get("contested") is True
                    and len(conflicts.get("nands") or []) >= 1
                    and any(p["kind"] == "nand" for p in dd)):
                surfaced += 1

        rate = surfaced / len(corpus["conflicted"])
        assert surfaced >= 29, \
            f"conflict-surfacing rate {rate:.2f} — need ≥ 29/30 (E2E-1 ≥ 0.95)"

        # 0 clean points carry conflict noise.
        for pid in corpus["clean"]:
            claim_hit = _hit_by_id(all_hits, pid)
            assert claim_hit is not None, f"clean point {pid} not retrieved"
            assert "conflicts" not in claim_hit, f"clean point {pid} carries conflict noise"
            assert "warnings" not in claim_hit
            dd = claim_hit.get("dig_deeper") or []
            assert all(p["kind"] != "nand" for p in dd), \
                f"clean point {pid} carries a NAND pointer"

        # Decision points: ≥2 alternatives, each with ep_weight + mitigation.
        for pid in corpus["decision"]:
            claim_hit = _hit_by_id(all_hits, pid)
            assert claim_hit is not None, f"decision {pid} not retrieved"
            tradeoffs = claim_hit.get("tradeoffs") or []
            assert len(tradeoffs) >= 2, f"decision {pid} must surface ≥ 2 alternatives"
            for t in tradeoffs:
                assert isinstance(t.get("ep_weight"), (int, float)), \
                    f"alternative {t} missing ep_weight"
                assert t.get("mitigation"), f"alternative {t} missing mitigation"
            # tradeoff pointer present (decision-point precedence) and its
            # label is the deterministic registry phrase (UXD 4 — the label
            # is asserted at the value level, not just kind presence).
            dd = claim_hit.get("dig_deeper") or []
            assert any(p["kind"] == "tradeoff" for p in dd), \
                f"decision {pid} missing tradeoff pointer"
            t_ptr = next(p for p in dd if p["kind"] == "tradeoff")
            assert t_ptr["label"] == DIG_DEEPER_LABELS["tradeoff"], \
                f"decision {pid} tradeoff label is not the registry phrase"
    finally:
        sdk.close()


# ── E2E-6: supersession-aware recall ──────────────────────────────────────

def test_e2e6_supersession_aware_recall(w4_flag):
    """E2E-6: a superseded predecessor surfaces with status superseded +
    supersession view (successor_label) + a superseded-kind 'see what
    changed' pointer — never current-belief framing; the current-view search
    returns the successor. The chain is written through the REAL
    supersede_point verb (E2E-6 Given) — a regression in the write path
    (status stamp, CORRECTS direction, validTo window end) fails here, not
    just the read-side projection. Bi-temporal: the window-end stamp
    supersede_point writes rides the enriched hit as the flat canonical
    valid_to key (the §6.1 view shape {line, successor_label} carries no
    date)."""
    sdk = _fresh_sdk()
    try:
        # Chain A (superseded) → B (superseded) → C (live): contiguous
        # bi-temporal windows via the production write verb.
        a = sdk.create_point("statement", "chain-topic-a original belief statement")
        a_id = a["id"]
        b = sdk.create_point("statement", "chain-topic-b successor belief statement")
        b_id = b["id"]
        sdk.supersede_point(a_id, b_id, valid_from="2026-06-01")
        c = sdk.create_point("statement", "chain-topic-c current belief statement")
        c_id = c["id"]
        sdk.supersede_point(b_id, c_id, valid_from="2026-07-01")

        # Recall A directly (include_terminal — the superseded predecessor).
        hits = _search_for(sdk, "chain-topic-a", limit=20, include_terminal=True)
        a_hit = _hit_by_id(hits, a_id)
        assert a_hit is not None, "superseded predecessor must be retrievable"
        assert a_hit.get("status") == "superseded", "never reads as current belief"
        # Bi-temporal (flat canonical keys stay canonical on the enriched
        # surface): the window END stamped by supersede_point rides the hit.
        assert a_hit.get("valid_to") == "2026-06-01", \
            "supersede_point's validTo window-end stamp must ride the hit"
        ss = a_hit.get("supersession") or {}
        assert ss.get("line") == "superseded"
        assert ss.get("successor_label"), "successor_label must point at B"
        dd = a_hit.get("dig_deeper") or []
        superseded_ptr = next((p for p in dd if p["kind"] == "superseded"), None)
        assert superseded_ptr is not None, "must carry a 'see what changed' pointer"
        assert superseded_ptr["target"] == b_id
        assert superseded_ptr["label"] == "see what changed"

        # Current-view search (default, no include_terminal) returns C — the
        # LIVE successor, not the superseded predecessors. C's own
        # successor-side view (it supersedes B) is asserted: flat
        # supersedes intact + the enriched supersession view (line mirrors
        # status; C supersedes B so its flat supersedes carries B).
        cur = _search_for(sdk, "chain-topic", limit=20)
        cur_ids = {h["id"] for h in cur}
        assert c_id in cur_ids, "current-view must return the successor"
        assert a_id not in cur_ids, "superseded predecessor must not surface as current"
        assert b_id not in cur_ids
        c_hit = _hit_by_id(cur, c_id)
        assert c_hit is not None
        # Additive-only on the live successor: its flat canonical supersedes
        # (the point C supersedes = B) survives enrichment untouched.
        flat_supersedes = c_hit.get("supersedes") or []
        assert any(s.get("id") == b_id for s in flat_supersedes), \
            "C's flat supersedes must still carry B (additive-only, no rename/removal)"
        c_ss = c_hit.get("supersession") or {}
        assert c_ss.get("line") == (c_hit.get("status") or "live"), \
            "live successor's supersession line mirrors its flat status"
        assert c_ss.get("successor_label") is None, \
            "a live point with no superseder carries successor_label null"
        # C supersedes B ⇒ no 'see what changed' pointer on C itself (the
        # pointer rides the superseded side, never the current belief).
        c_dd = c_hit.get("dig_deeper") or []
        assert all(p["kind"] != "superseded" for p in c_dd), \
            "current-belief successor never carries a superseded pointer"

        # Mid-chain B surfaces superseded_by: C + its own see-what-changed pointer.
        bhits = _search_for(sdk, "chain-topic-b", limit=20, include_terminal=True)
        b_hit = _hit_by_id(bhits, b_id)
        assert b_hit is not None
        assert b_hit.get("valid_to") == "2026-07-01", \
            "mid-chain window end (B superseded by C) must ride the hit"
        b_ss = b_hit.get("supersession") or {}
        assert b_ss.get("line") == "superseded"
        assert b_ss.get("successor_label"), "mid-chain successor label must point at C"
        b_dd = b_hit.get("dig_deeper") or []
        assert any(p["kind"] == "superseded" and p["target"] == c_id for p in b_dd), \
            "mid-chain must navigate A→B→C via the superseded pointer"
    finally:
        sdk.close()


def test_superseded_kind_pointer_only_when_supersession_data_exists(w4_flag):
    """§3.1.4: superseded-kind pointers appear ONLY when supersession data
    exists. Two quadrants: (a) a LIVE point with no successor carries no
    superseded pointer; (b) a TERMINAL superseded predecessor whose successor
    was retracted (E2E-6 negative/edge — retracted claims are not
    superseding authority) still reads superseded but carries no successor
    label and no pointer."""
    sdk = _fresh_sdk()
    try:
        # Live point with no supersession data → no superseded pointer.
        g = _plant_conflicted(sdk, "live-no-succ")
        hits = _search_for(sdk, "live-no-succ")
        claim_hit = _hit_by_id(hits, g["claim"])
        dd = claim_hit.get("dig_deeper") or []
        assert all(p["kind"] != "superseded" for p in dd)
        assert claim_hit["supersession"]["successor_label"] is None

        # Terminal superseded predecessor via the REAL supersede + retract
        # verbs: old is superseded by succ, then succ is retracted → old's
        # superseded_by resolves to None (no live successor to point at).
        old = sdk.create_point("statement", "term-succ-topic old belief statement")
        succ = sdk.create_point("statement", "term-succ-topic successor belief")
        sdk.supersede_point(old["id"], succ["id"], valid_from="2026-05-01")
        sdk.retract_point(succ["id"])
        thits = _search_for(sdk, "term-succ-topic", limit=20, include_terminal=True)
        t_hit = _hit_by_id(thits, old["id"])
        assert t_hit is not None, "terminal superseded predecessor must be retrievable"
        assert t_hit.get("status") == "superseded"
        t_ss = t_hit.get("supersession") or {}
        assert t_ss.get("line") == "superseded", \
            "superseded status without a live successor still reads superseded"
        assert t_ss.get("successor_label") is None, \
            "retracted successor is not superseding authority → no successor label"
        t_dd = t_hit.get("dig_deeper") or []
        assert all(p["kind"] != "superseded" for p in t_dd), \
            "no 'see what changed' pointer without a live successor"
    finally:
        sdk.close()


# ── S8: bounded reads + fail-open ─────────────────────────────────────────

def test_assembly_bounded_batch_reads(monkeypatch):
    """S8: the why-block assembly issues a FIXED number of batch queries for
    ANY batch size — never a per-result loop (no N+1), never full EP
    propagation on the hot path."""
    sdk = _fresh_sdk()
    try:
        ids = []
        for i in range(10):
            g = _plant_conflicted(sdk, f"batch-topic-{i}")
            ids.append(g["claim"])
        # A second, larger batch proves the count is size-independent (a
        # per-batch-size branch or chunking would trip the same-count pin).
        ids2 = list(ids)
        for i in range(10, 15):
            g = _plant_conflicted(sdk, f"batch-topic-{i}")
            ids2.append(g["claim"])
        proj = sdk._get_proj()
        real_query = proj.g.query

        from tortoise.projection import _GuardedGraph

        def _count_for(batch: list[str]) -> int:
            calls: list[str] = []

            def _counting_query(self, cypher, params=None, timeout=None):
                calls.append(cypher)
                return real_query(cypher, params=params, timeout=timeout)

            monkeypatch.setattr(_GuardedGraph, "query", _counting_query)
            blocks = assemble_why_blocks(proj, batch)
            assert len(blocks) == len(batch)
            return len(calls)

        # Exactly 6 assembly queries (ep read, supports×2, conflicts,
        # supersession, tradeoffs) — fixed regardless of batch size. The
        # exact count is the structural pin: a per-point loop over a
        # 10-claim fixture would need ≥15 calls AND a merged/dropped read
        # (e.g. folding the dedicated ep read back into the conflict query)
        # would drop below 6 — both directions of drift trip the == 6 pin.
        n10 = _count_for(ids)
        n15 = _count_for(ids2)
        assert n10 == 6, f"10-claim batch issued {n10} queries — expected exactly 6"
        assert n15 == 6, f"15-claim batch issued {n15} queries — count must be size-independent"
    finally:
        sdk.close()


def test_assembly_partial_dimension_fail_open(w4_flag, monkeypatch):
    """S8 fail-open (per-dimension): ONE dimension's query failing degrades
    that dimension only — the rest of the canonical block still emits and
    the recall turn is never broken (distinct from the whole-assembly
    failure, which returns {}).

    The fixture is a DECISION point (its tradeoffs dimension is POPULATED on
    the healthy path) so the assertions discriminate: the healthy run emits
    tradeoffs; the injected tradeoffs-read failure drops ONLY tradeoffs (no
    tradeoff-kind pointer) while ep/support/conflicts/supersession survive.
    A fired-spy proves the forced failure actually raised (a test that stays
    green when the injection never matches is vacuous)."""
    sdk = _fresh_sdk()
    try:
        g = _plant_decision(sdk, "partial-topic")
        proj = sdk._get_proj()
        real_query = proj.g.query
        from tortoise.projection import _GuardedGraph

        # Healthy-path baseline: the decision fixture DOES emit tradeoffs
        # (≥ 2 alternatives) — proving the later absence is caused by the
        # injected failure, not by an empty dimension.
        healthy = assemble_why_blocks(proj, [g["decision"]])
        assert len(healthy[g["decision"]].get("tradeoffs") or []) >= 2, \
            "decision fixture must emit tradeoffs on the healthy path"

        fired: list[str] = []

        def _flaky_query(self, cypher, params=None, timeout=None):
            # Fail ONLY the tradeoffs read (its template matches the
            # mitigated_by pattern); every other read passes through.
            if "mitigated_by" in cypher:
                fired.append(cypher)
                raise RuntimeError("forced tradeoffs failure")
            return real_query(cypher, params=params, timeout=timeout)

        monkeypatch.setattr(_GuardedGraph, "query", _flaky_query)
        blocks = assemble_why_blocks(proj, [g["decision"]])
        assert fired, "the forced tradeoffs failure must actually fire"
        block = blocks[g["decision"]]
        # The non-tradeoff dimensions survive the tradeoff failure.
        assert block["point_id"] == g["decision"]
        assert block["ep"]["contested"] is True
        assert len(block.get("support_chain") or []) >= 1
        assert (block.get("conflicts") or {}).get("contested") is True
        assert "supersession" in block
        # Tradeoffs degraded: absent (no partial tradeoffs array emitted),
        # and no tradeoff-kind dig_deeper pointer.
        assert "tradeoffs" not in block, \
            "failed tradeoffs read must degrade the dimension, not emit a partial array"
        for p in (block.get("dig_deeper") or []):
            assert p["kind"] != "tradeoff"
        # The recall turn is unbroken: the item still enriches (minus the
        # failed dimension) on the flag-on path.
        out = enrich_items(proj, [{"id": g["decision"], "content": "partial-topic decision point"}])
        assert out[0]["id"] == g["decision"]
        assert "conflicts" in out[0]
        assert "tradeoffs" not in out[0]
    finally:
        sdk.close()


def test_supersession_fetch_failure_degrades_honestly(w4_flag, monkeypatch):
    """S8 per-dimension degradation contract (wrong-side guard): when the
    assembly's supersession state-fetch fails, the enriched view must NOT
    fabricate a false ``line: "live"`` for a superseded predecessor — the
    item's OWN flat canonical status (attached by the search path's D8
    decoration, which is unaffected) is the fallback: a superseded point
    reads superseded even on a degraded read.

    Scoping: only tortoise.why's own fetch (the assembly's step-4 state
    read) is patched to raise — the search surface's D8 decoration imports
    fetch_point_epistemic_state from search_engine directly and must
    succeed so the hit carries its flat canonical status/superseded_by."""
    import tortoise.why as why_mod
    sdk = _fresh_sdk()
    try:
        old = sdk.create_point("statement", "fetch-fail old belief statement")
        succ = sdk.create_point("statement", "fetch-fail successor belief")
        sdk.supersede_point(old["id"], succ["id"], valid_from="2026-06-01")
        fired: list[str] = []

        def _explode(*args, **kwargs):
            fired.append("fetch")
            raise RuntimeError("forced state-fetch failure")

        monkeypatch.setattr(why_mod, "fetch_point_epistemic_state", _explode)
        # The block assembles WITHOUT a supersession view (degraded), but the
        # point still gets an enriched item through the search surface with
        # its flat canonical status intact.
        hits = _search_for(sdk, "fetch-fail", limit=20, include_terminal=True)
        assert fired, "the assembly's state fetch must actually fire (and fail)"
        old_hit = _hit_by_id(hits, old["id"])
        assert old_hit is not None, "superseded predecessor must be retrievable"
        assert old_hit.get("status") == "superseded", \
            "flat canonical status rides the hit (D8 decoration unaffected)"
        ss = old_hit.get("supersession") or {}
        assert ss.get("line") == "superseded", \
            "degraded supersession must mirror the flat status — never a fabricated 'live'"
        # The degraded-path successor_label fallback (block has no
        # supersession data, so the label derives from the item's flat
        # superseded_by — the surviving D8 decoration) is pinned here.
        assert ss.get("successor_label"), \
            "degraded view must still surface the successor_label from the flat superseded_by"

        # Pure-function pins for the fallback's remaining branches:
        # (i) BOTH the block supersession AND the item flat status absent
        #     ⇒ the terminal default is the honest "live" (unmeasured view);
        # (ii) flat superseded_by present without a block view ⇒ the label
        #     falls back to the flat superseding point.
        from tortoise.why import project_item as _project
        bare_block = {"support_chain": [], "ep": {"contested": False}}
        assert _project({"id": "x", "content": "x"}, bare_block)["supersession"] == {
            "line": "live", "successor_label": None}, \
            "absent block view + absent flat status must default to live/null"
        fb_block = {"support_chain": [], "ep": {"contested": False}}
        fb_item = {"id": "y", "content": "y", "status": "superseded",
                   "superseded_by": {"id": "succ-1",
                                      "content_snippet": "the flat successor"}}
        fb_ss = _project(fb_item, fb_block)["supersession"]
        assert fb_ss["line"] == "superseded"
        assert fb_ss["successor_label"] == "the flat successor", \
            "flat superseded_by must feed the degraded successor_label"
    finally:
        sdk.close()


def test_assembly_fail_open_never_breaks_turn(w4_flag, monkeypatch):
    """S8 fail-open: with the W4 flag ON, any assembly error degrades to
    'no enrichment keys' — the recall turn is never broken, no
    degraded_reason noise on clean surfaces (the S9 delivery contract owns
    degraded_reason). The w4_flag fixture is REQUIRED: with the flag OFF
    enrich_items returns at its early gate and the injected failure would
    never fire (a vacuous pass). A spy counter proves the failure path
    actually ran before the unchanged-items assertions."""
    import tortoise.why as why_mod
    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "failopen-topic")
        exploded: list[RuntimeError] = []

        def _explode(*args, **kwargs):
            exploded.append(RuntimeError("forced assembly failure"))
            raise exploded[-1]

        monkeypatch.setattr(why_mod, "assemble_why_blocks", _explode)
        # enrich_items returns the items unchanged (flag ON → assembly ran
        # and failed; spy proves it fired rather than short-circuiting).
        items = [{"id": g["claim"], "content": "x", "ep": {"contested": True}}]
        out = enrich_items(sdk._get_proj(), items)
        assert exploded, "assembly failure must have fired on the flag-on path"
        assert out == items, "fail-open must return items unchanged"
        # The search surface still works (no W4 keys, no exception) — the
        # surface-level assembly failure also degrades, never breaks the turn.
        hits = _search_for(sdk, "failopen-topic")
        claim_hit = _hit_by_id(hits, g["claim"])
        assert claim_hit is not None
        for k in W4_KEYS:
            assert k not in claim_hit
    finally:
        sdk.close()


# ── Assembly unit-ish: canonical block shape ──────────────────────────────

def test_assemble_why_blocks_canonical_shape(w4_flag):
    """§3.1.4 canonical why-block: {point_id, support_chain, ep, conflicts,
    supersession, tradeoffs?, dig_deeper?} — the shape /v1/context consumes
    (shared assembly)."""
    sdk = _fresh_sdk()
    try:
        g = _plant_conflicted(sdk, "canon-topic")
        blocks = assemble_why_blocks(sdk._get_proj(), [g["claim"]])
        block = blocks[g["claim"]]
        assert block["point_id"] == g["claim"]
        assert isinstance(block["support_chain"], list)
        assert set(block["ep"]) == {"confidence_mean", "variance", "contested",
                                    "has_ep"}
        assert block["ep"]["contested"] is True
        assert set(block["supersession"]) == {"status", "superseded_by",
                                              "supersedes", "successor_label"}
        assert block["conflicts"]["contested"] is True
        assert len(block["conflicts"]["nands"]) >= 1
        assert any(p["kind"] == "nand" for p in block["dig_deeper"])
        # Uncontested block: no conflicts key. §3.1.3 VALUE-level empty/null
        # conventions on the live point: superseded_by null, supersedes [] —
        # not just absent keys.
        clean = _plant_clean(sdk, "canon-clean")
        cb = assemble_why_blocks(sdk._get_proj(), [clean["claim"]])[clean["claim"]]
        assert "conflicts" not in cb
        assert "tradeoffs" not in cb
        assert cb["supersession"]["superseded_by"] is None
        assert cb["supersession"]["supersedes"] == []
        assert cb["supersession"]["successor_label"] is None
    finally:
        sdk.close()


def test_canonical_ep_real_for_no_nand_point(w4_flag):
    """S8 regression (review P1): the canonical ``ep`` block must be REAL
    for every existing point — including a measured point with ZERO active
    NANDers (the dedicated ep read survives where the old conflict-query-
    rider did not). has_ep must never be fabricated False for a measured
    point."""
    sdk = _fresh_sdk()
    try:
        g = _plant_clean(sdk, "clean-ep-topic")  # 12/1 posterior, no NANDs
        blocks = assemble_why_blocks(sdk._get_proj(), [g["claim"]])
        block = blocks[g["claim"]]
        assert block["ep"]["has_ep"] is True, "measured point must read has_ep True"
        # 12/1 → posterior mean ≈ 0.9231, variance ≈ 0.0051.
        assert block["ep"]["confidence_mean"] == pytest.approx(12 / 13, abs=1e-3)
        assert block["ep"]["variance"] == pytest.approx(
            (12 * 1) / (13 * 13 * 14), abs=1e-4)
        assert block["ep"]["contested"] is False
        assert "conflicts" not in block
        # Non-Point ids never get a fabricated block.
        assert assemble_why_blocks(sdk._get_proj(), ["not-a-real-point-id"]) == {}
        # Empty batch: early gates return cleanly (no queries, no crash).
        assert assemble_why_blocks(sdk._get_proj(), []) == {}
        assert enrich_items(sdk._get_proj(), []) == []
        # Mixed batch: the valid Point item enriches while id-less and
        # non-Point items pass through byte-unchanged (no exception).
        out = enrich_items(sdk._get_proj(), [
            {"id": g["claim"], "content": "clean-ep-topic clean belief statement"},
            {"id": "not-a-real-point", "content": "fake"},
            {"content": "no id"},
        ])
        assert "supersession" in out[0], "valid Point item must enrich in a mixed batch"
        assert out[1] == {"id": "not-a-real-point", "content": "fake"}, \
            "non-Point item must pass through byte-unchanged"
        assert out[2] == {"content": "no id"}, "id-less item must pass through unchanged"
    finally:
        sdk.close()


def test_surface_glue_fail_open_ask_and_analyze(w4_flag, monkeypatch):
    """S8 fail-open at the SURFACE GLUE (the branches that own the ask and
    analyze surfaces' why-layer): a regression that breaks the ask/analyze
    TURN (rather than degrading the why layer) must fail — each surface's
    own fail-open branch is exercised with an injected failure.

      - ask: item_to_why_entry (or the pool enrichment) raising ⇒ the ask
        response still returns the canonical fields + ``why: []`` (the why
        key rides the flag-ON response; an empty collection is the honest
        degraded value, never a broken turn).
      - analyze: the assembly raising inside the wrapper ⇒ the analyze
        response is returned intact WITHOUT a ``why`` key."""
    import tortoise.sdk as sdk_mod
    import tortoise.why as why_mod
    sdk = _fresh_sdk()
    try:
        _plant_conflicted(sdk, "glue-topic")
        fake = _FakeReader()
        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
        _reset_ask_reader_cache_for_tests()

        # ask glue: force item_to_why_entry to raise mid-loop (fired-spy —
        # the why == [] assertion must not pass vacuously if the ask lane
        # retrieved zero hits).
        fired_entries: list[str] = []

        def _explode_entry(*args, **kwargs):
            fired_entries.append("entry")
            raise RuntimeError("forced why-entry failure")

        monkeypatch.setattr(why_mod, "item_to_why_entry", _explode_entry)
        resp = sdk.ask("what contradicted the glue-topic belief statement?")
        assert fired_entries, "the ask why-entry failure must actually fire"
        assert set(resp) == CANONICAL_ASK_KEYS | {"why"}, \
            "flag-on ask keeps the canonical fields + the why key on failure"
        assert resp["why"] == [], \
            "ask glue failure degrades to an empty why collection, never a broken turn"
        assert isinstance(resp.get("answer"), str) and resp["answer"]
        monkeypatch.setattr(why_mod, "item_to_why_entry", item_to_why_entry)
        _reset_ask_reader_cache_for_tests()

        # analyze glue: force the assembly to raise inside the wrapper
        # (fired-spy — the analyze response intact assert must not pass
        # vacuously if the pattern leg returned no raw rows).
        fired_assembly: list[str] = []

        def _explode_assembly(*args, **kwargs):
            fired_assembly.append("assembly")
            raise RuntimeError("forced analyze assembly failure")

        monkeypatch.setattr(why_mod, "assemble_why_blocks", _explode_assembly)
        ana = _mcp_analyze(sdk, "where is the disagreement?")
        assert fired_assembly, "the analyze assembly failure must actually fire"
        assert ana.get("pattern") == "disagreement", \
            "analyze turn must not break when the why assembly fails"
        assert "why" not in ana, \
            "analyze glue failure drops the why key (absent = not computed)"
    finally:
        sdk.close()


def test_analyze_why_ep_real_for_clean_point(w4_flag):
    """Analyze-surface regression: the ``why`` entries on the consensus
    pattern carry a REAL ep (no self-contradiction with the raw row's
    confidence)."""
    sdk = _fresh_sdk()
    try:
        g = _plant_clean(sdk, "consensus-topic")
        ana = _mcp_analyze(sdk, "what is the consensus?")
        assert ana.get("pattern") == "consensus"
        why = ana.get("why") or []
        entry = next((e for e in why if e.get("point_id") == g["claim"]), None)
        assert entry is not None, "consensus why must carry the clean point's block"
        assert entry["ep"]["has_ep"] is True
        assert entry["ep"]["confidence_mean"] == pytest.approx(12 / 13, abs=1e-3)
        assert entry["ep"]["contested"] is False
    finally:
        sdk.close()


def test_nand_snippet_prefers_counterargument_content(w4_flag):
    """Review P2: the NAND ``content_snippet`` is the counterargument's own
    content — never the NAND operator's label (a verb like "opposes")."""
    sdk = _fresh_sdk()
    try:
        ev = sdk.create_point("evidence", "labelled-nand support record")
        claim = sdk.create_point("statement", "labelled-nand belief statement")
        sdk.create_operator("IMPL", ev["id"], [claim["id"]])
        counter = sdk.create_point("statement", "labelled-nand counterargument text")
        sdk.create_operator("NAND", counter["id"], [claim["id"]], label="opposes")
        _set_posterior(sdk, claim["id"], 2.0, 2.0)
        blocks = assemble_why_blocks(sdk._get_proj(), [claim["id"]])
        nands = blocks[claim["id"]]["conflicts"]["nands"]
        assert nands and nands[0]["point_id"] == counter["id"]
        assert "counterargument text" in nands[0]["content_snippet"]
        assert "opposes" not in nands[0]["content_snippet"]
    finally:
        sdk.close()


def test_point_ids_in_raw_extraction():
    """Analyze-surface id extraction: point ids (ULID + pt_<hash>) are
    extracted from raw rows; non-id cells are ignored."""
    raw = [
        ["1a06002b2ae-b460e191039e", "content text", "0.857"],
        ["pt_ab12cd34ef56", "more", "0.5"],
        ["not-an-id", "ignore me"],
    ]
    ids = point_ids_in_raw(raw)
    assert ids == ["1a06002b2ae-b460e191039e", "pt_ab12cd34ef56"]


def test_ontology_vocabulary_registered():
    """S15: dig-deeper kinds + labels are the ONTOLOGY §5 registered
    vocabulary (supports|nand|superseded|tradeoff) — the kinds ROW and the
    label ROW (each registry verb phrase is deterministic, never LLM
    prose). The why-block sections row (the second S15 clause — the code's
    W4_KEYS vocabulary) is pinned phrase-by-phrase so drift between the
    enriched-item keys and the ontology row fails the test."""
    assert set(DIG_DEEPER_KINDS) == {"supports", "nand", "superseded", "tradeoff"}
    for kind in DIG_DEEPER_KINDS:
        assert DIG_DEEPER_LABELS.get(kind)
    ontology = Path(__file__).resolve().parent.parent / "docs" / "ONTOLOGY.md"
    text = ontology.read_text()
    assert "dig_deeper kinds" in text
    assert "supports | nand | superseded | tradeoff" in text
    # Label row (§5): every registry verb phrase must be the ontology
    # vocabulary (the kinds row alone would not pin the labels).
    assert "dig_deeper labels" in text
    for label in DIG_DEEPER_LABELS.values():
        assert label in text, f"ontology §5 label row missing {label!r}"
    # Why-block sections row (§5): the code-level W4_KEYS vocabulary (why /
    # conflicts / supersession / tradeoffs / dig_deeper / warnings) must be
    # registered as response-contract vocabulary — drift between the code's
    # enriched-item keys and the ontology row would otherwise pass silently.
    # Pinned as ONE row literal (three of the six keys also appear elsewhere
    # in ONTOLOGY.md, so per-key presence-anywhere scans would pass a row
    # that silently dropped them).
    assert "why-block sections" in text
    sections_row = "why · conflicts · supersession · tradeoffs · dig_deeper · warnings"
    assert sections_row in text, \
        "ontology §5 why-block sections row drifted from the W4_KEYS vocabulary"


def test_bare_point_empty_conventions(w4_flag):
    """§3.1.3 empty-collection conventions on a bare point (zero IMPL
    supports + zero NANDs, uncontested posterior): support_chain is the
    empty collection [] and the item carries NO why marker (empty chain is
    not a degradation); conflicts/tradeoffs/dig_deeper/warnings absent;
    the supersession view defaults to the flat status line with
    successor_label None — the 'line' value itself pinned (a regression
    that rendered a live point as line:"superseded" would fail here)."""
    sdk = _fresh_sdk()
    try:
        bare = sdk.create_point("statement", "bare-empty belief statement")
        _set_posterior(sdk, bare["id"], 12.0, 1.0)  # uncontested, no edges
        # Canonical block: support_chain == [] (empty collection, present on
        # the block — the assembly always materializes the collection).
        blocks = assemble_why_blocks(sdk._get_proj(), [bare["id"]])
        block = blocks[bare["id"]]
        assert block["support_chain"] == []
        assert block["ep"]["has_ep"] is True
        assert block["ep"]["contested"] is False
        # Enriched item through the search surface: no W4 markers that
        # require content (why is skipped on an empty chain; no conflict
        # noise on an uncontested, NAND-free point).
        hits = _search_for(sdk, "bare-empty", limit=50)
        hit = _hit_by_id(hits, bare["id"])
        assert hit is not None, "bare point must retrieve"
        assert "warnings" not in hit
        assert "why" not in hit, "empty support_chain must not emit a why marker"
        assert "conflicts" not in hit
        assert "tradeoffs" not in hit
        assert "dig_deeper" not in hit, "no pointers exist on a bare point"
        # Supersession view: computed dimension — flat-status line (draft),
        # successor_label None (never a fabricated successor).
        ss = hit.get("supersession")
        assert ss is not None
        assert ss["successor_label"] is None
        assert ss["line"] == (hit.get("status") or "live"), \
            "supersession line must mirror the flat canonical status"
    finally:
        sdk.close()


def test_mixed_edge_support_legs_merge(w4_flag):
    """REVIEW-FIX (code-review gate): a point with BOTH an operator-mediated
    IMPL support AND a direct statement→statement IMPL edge must surface the
    UNION of both legs — the direct leg's second pass used to OVERWRITE the
    operator-mediated chain (silent clobber of one leg on mixed-edge points).

    Leg 1 (operator-mediated): evidence →INPUT→ operator →IMPL→ claim.
    Leg 2 (direct): statement →IMPL→ claim (operator-less reification edge).
    Both supporters must appear in the ≤3 support_chain, ordered by
    (weight desc, point_id). Dedup: a supporter reachable via BOTH legs
    (operator-mediated AND direct edge to the same claim) appears once.
    Cross-leg cap: with >3 candidates across both legs the strongest ≤3
    survive the re-sort (a weak candidate is evicted, never a clobber)."""
    sdk = _fresh_sdk()
    try:
        topic = "mixed-edge-topic"
        strong_op = sdk.create_point("evidence", f"{topic} strong op-mediated record")
        strong_direct = sdk.create_point("statement", f"{topic} strong direct support")
        both_legs = sdk.create_point("evidence", f"{topic} reachable via both legs")
        weak_op = sdk.create_point("evidence", f"{topic} weak op-mediated record")
        claim = sdk.create_point("statement", f"{topic} mixed-edge belief statement")
        # Leg 1: operator-mediated (create_operator wires source →INPUT idx0→
        # op →IMPL→ target). strong_op and weak_op ride this leg.
        sdk.create_operator("IMPL", strong_op["id"], [claim["id"]])
        sdk.create_operator("IMPL", weak_op["id"], [claim["id"]])
        # Leg 2: operator-less direct IMPL edges (reification rule, §8).
        # strong_direct rides direct only; both_legs rides BOTH (op-mediated
        # INPUT path AND a direct edge — the dedup branch).
        sdk.create_direct_edge("IMPL", strong_direct["id"], claim["id"])
        sdk.create_direct_edge("IMPL", both_legs["id"], claim["id"])
        _set_posterior(sdk, claim["id"], 12.0, 1.0)
        # Distinct weights to force a deterministic (weight desc) ordering:
        # strong_op 12/1 → 0.92, strong_direct 9/1 → 0.90, both_legs 6/1 →
        # 0.86, weak_op 1/4 → 0.20 (evicted by the cross-leg ≤3 re-cap).
        _set_posterior(sdk, strong_op["id"], 12.0, 1.0)
        _set_posterior(sdk, strong_direct["id"], 9.0, 1.0)
        _set_posterior(sdk, both_legs["id"], 6.0, 1.0)
        _set_posterior(sdk, weak_op["id"], 1.0, 4.0)

        blocks = assemble_why_blocks(sdk._get_proj(), [claim["id"]])
        chain = blocks[claim["id"]]["support_chain"]
        sup_ids = [s["point_id"] for s in chain]
        # Both legs survive — never a one-leg clobber.
        assert strong_op["id"] in sup_ids, \
            "operator-mediated supporter dropped by the direct-leg merge"
        assert strong_direct["id"] in sup_ids, \
            "direct supporter missing from the merged chain"
        # Dedup: the both-legs supporter appears exactly once.
        assert sup_ids.count(both_legs["id"]) == 1, \
            "supporter reachable via both legs must appear once (dedup)"
        # Cross-leg ≤3 re-cap with deterministic ordering: the strongest 3
        # survive (weak_op at 0.20 evicted), ordered by weight desc.
        assert len(chain) == 3, f"cross-leg re-cap must hold ≤3, got {chain}"
        assert weak_op["id"] not in sup_ids, \
            "weakest candidate must be evicted by the cross-leg re-cap"
        assert sup_ids == [strong_op["id"], strong_direct["id"], both_legs["id"]], \
            "merged chain must sort by (weight desc, point_id)"
    finally:
        sdk.close()


def test_rogue_variance_coerces_and_projection_fail_open(w4_flag, monkeypatch):
    """REVIEW-FIX (code-review gate, two hunks in the same round):
    (1) ``_contested_from_item`` tolerantly coerces a rogue non-float
    ``ep.variance`` (a badly-formed item from a foreign producer) instead of
    raising into the recall turn — treated as unmeasured (False).
    (2) ``enrich_items`` whole-batch fail-open: a projection error mid-loop
    degrades to "no enrichment keys" for the whole batch (byte-identical to
    flag-off) instead of a partial/raising recall."""
    import tortoise.why as why_mod
    sdk = _fresh_sdk()
    try:
        claim = sdk.create_point("statement", "rogue-variance topic belief")
        _set_posterior(sdk, claim["id"], 12.0, 1.0)
        block = assemble_why_blocks(sdk._get_proj(), [claim["id"]])[claim["id"]]

        # (1) rogue variance: string variance + has_ep True → coerced, no raise.
        rogue = {"id": claim["id"], "content": "rogue-variance topic belief",
                 "ep": {"contested": False, "has_ep": True,
                         "variance": "not-a-float", "confidence_mean": 0.9}}
        assert why_mod._contested_from_item(rogue, block) is False, \
            "rogue non-float variance must coerce to unmeasured, never raise"
        # None variance → falsy, no raise, unmeasured.
        assert why_mod._contested_from_item(
            {"id": claim["id"], "ep": {"has_ep": True, "variance": None}}, block) is False
        # (2) projection fail-open: a projection error degrades the WHOLE batch
        # to the original items (byte-identical, no partial keys, no raise).
        def _explode(item, block):  # noqa: ARG001
            raise RuntimeError("projection exploded")

        monkeypatch.setattr(why_mod, "project_item", _explode)
        items = [{"id": claim["id"], "content": "rogue-variance topic belief"}]
        out = why_mod.enrich_items(sdk._get_proj(), items)
        assert out == items, \
            "projection failure must degrade the whole batch to original items"
    finally:
        sdk.close()
