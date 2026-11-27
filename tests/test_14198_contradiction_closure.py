"""#14198 — a contradiction DECLARED across two captures CLOSES the older
claim's interval; it does not leave two rival claims with no marker.

WHY THIS FILE EXISTS
--------------------
`docs/evidence/14592-operating-model-validation/REPORT.md` §G1 measured, on a
fresh graph, that a corpus containing a reversing document produced **no
contradiction at all**: doc-01's *"ColdPath Logistics holds a validated
cold-chain certificate"* and doc-05's *"The cold-chain audit failed ColdPath
Logistics last week"* were both `status: live`, with `supersessions: 0`. The
report's own words: *"It does not represent a contradiction between two
documents."*

The mechanism was NOT that the extractor failed to try. It tried, and declared
the contradiction in the vocabulary the ontology gives it — a `NAND` (its stated
predicate: `refutes`; #2552's declaration channel). The edge was then lost in
the ONE place that cannot cross a document boundary:

    extractor_v2.execute_embed, the operator pass
    ("operator dropped: src/dst did not resolve to an emitted point/event")

`_resolve` reads only THIS capture's `point_ids` / `event_ids`, so a NAND whose
older side lives in a PRIOR capture can never resolve there. On the corpus run
the endpoint was first HELD (`#10997 S3 HOLD` — the identity lookup was
`partial`, and `hold_store` is `None` on the shipping path so the hold was not
persisted), and then dropped. Two of doc-05's three drops were that shape.

WHAT THE FIX IS, AND WHAT IT IS NOT
-----------------------------------
A declared `NAND` with exactly one side resolved in this capture and the OTHER
side resolving in the S3 prior set — or folded onto its graph id by a RESOLVED
identity lookup — now emits the SAME record a declared point-level `supersedes`
ref emits (`commit_ops.apply_supersessions` → `sdk._supersede_point`): the
`CORRECTS` edge, the older claim's `validTo` stamped from the successor's window
start, `expiredAt` stamped from ingestion time, and the older claim **retained
as terminal** — closed, never deleted (§2.0: the record is truth) and never
merged (⛔ no fold).

⛔ THREE gates keep that honest, each pinned by a case below that REDS when it
is removed: the arm is SYMMETRIC on emission order (the model may write the pair
either way, and `_canonicalize_nand_direction` cannot repair a cross-capture
NAND), the emitted side must be a POINT (an Event is a legal NAND endpoint but
has no point-supersede channel — the consumer refuses it), and the two rivals
are ordered by WORLD TIME rather than capture membership (#14198: *"facts are
compared on world-time dates, not arrival order"*), so a backfill does not emit
an inverted window the consumer refuses.

⛔ It is NOT a lexical contradiction detector. The issue's own ruling is that
the MODEL labels a contradiction and code does the interval arithmetic; the
lexical `_boundary`/`classify_consolidation` machinery keeps its present
behaviour (two rivals survive). The negatives below pin that: nothing is
inferred from content, so a corpus without a declared contradiction gains none.

Run:  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_14198_contradiction_closure.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ids import content_hash

CERT = "ColdPath Logistics holds a validated cold-chain certificate"
AUDIT = "The cold-chain audit failed ColdPath Logistics last week"
#: The audit's own event text. An Event is a legal operator endpoint, but there
#: is no point-supersede channel to an Event successor — the arm must decline
#: and let the drop warning tell the truth (review F5 of #14794).
AUDIT_EVENT = "ColdPath Logistics ran its cold-chain audit last week"

#: World-time defaults: the certificate claim precedes the audit that refutes it.
CERT_WHEN = "2026-02-03"
AUDIT_WHEN = "2026-03-05"


def _pid(content: str) -> str:
    return f"pt_{content_hash(content)[:62]}"


def _embed_for(blob: str, *, relationship: str, inverted: bool = False,
               event_side: bool = False,
               cert_when: str = CERT_WHEN,
               audit_when: str = AUDIT_WHEN) -> dict:
    """The model's S2/S4 map, keyed on the transcript blob.

    ``relationship`` is what the three declarations vary: ``nand`` is the
    corpus's declared contradiction, ``impl`` is a declared SUPPORT edge over
    the same pair (the over-fire negative), ``none`` declares nothing at all.

    ``inverted`` emits the SAME NAND in the other order (older claim first) —
    the emission order `_canonicalize_nand_direction` cannot repair across a
    capture boundary (review F3). ``event_side`` makes the contradiction's NEW
    side an Event (review F5). ``cert_when``/``audit_when`` are the world-time
    starts, so a test can invert ARRIVAL order relative to world time — the
    backfill shape (review F2).
    """
    cert_session = "certificate" in blob and "audit" not in blob
    audit_session = "audit" in blob
    if cert_session:
        return {
            "entities": [{"name": "ColdPath Logistics", "kind": "core:organization",
                          "lifecycle": "created", "supersedes": None}],
            "events": [], "operators": [],
            "points": [{"content": CERT, "pointKind": "statement",
                        "about_entities": ["ColdPath Logistics"], "tier": "B",
                        "when": cert_when,
                        "quote": CERT,
                        "search_keys": ["cold-chain certificate"]}],
        }
    if audit_session:
        operators = []
        events: list[dict] = []
        if relationship == "nand":
            if event_side:
                events = [{"content": AUDIT_EVENT,
                           "about_entities": ["ColdPath Logistics"],
                           "when": audit_when}]
                src, dst = AUDIT_EVENT, CERT
            elif inverted:
                src, dst = CERT, AUDIT
            else:
                src, dst = AUDIT, CERT
            operators = [{"op_type": "NAND", "src": src, "dst": dst,
                          "stated_predicate": "refutes"}]
        elif relationship == "impl":
            operators = [{"op_type": "IMPL", "src": AUDIT, "dst": CERT,
                          "stated_predicate": "supports"}]
        return {
            "entities": [{"name": "ColdPath Logistics", "kind": "core:organization",
                          "lifecycle": "created", "supersedes": None}],
            "events": events, "operators": operators,
            "points": [{"content": AUDIT, "pointKind": "statement",
                        "about_entities": ["ColdPath Logistics"], "tier": "B",
                        "when": audit_when,
                        "quote": AUDIT,
                        "search_keys": ["cold-chain audit"]}],
        }
    return {"entities": [], "events": [], "points": [], "operators": []}


def _story_for(transcript: str) -> str:
    if "certificate" in transcript and "audit" not in transcript:
        return "ColdPath Logistics holds a validated cold-chain certificate."
    if "audit" in transcript:
        return "The cold-chain audit failed ColdPath Logistics."
    return "The team discusses the delivery partner."


def _model(relationship: str, **shape):
    def respond(system: str, user: str) -> str:
        if "STORY SUMMARIZER" in system:
            return _story_for(user)
        if "GAP REVIEWER" in system:
            return json.dumps({"entities": [], "events": [], "points": [],
                               "operators": [], "retractions": [],
                               "chain_notes": [], "link_before_create": []})
        if "GRAPH MAPPER" in system:
            return json.dumps(
                _embed_for(user, relationship=relationship, **shape))
        raise AssertionError(f"unexpected system prompt: {system[:60]}")

    class _M:
        def complete(self, *, system: str, user: str) -> str:
            return respond(system, user)

    return _M()


def _fake_search(sdk, embed_list, story, *, coverage: str = "partial", **kw):
    """S3's prior set — the claims that existed BEFORE this capture.

    ⛔ The PARTIAL coverage report is load-bearing, not decoration. It is the
    state the corpus run recorded (`S3 identity partial: the existing-item
    lookup did not run for 11 of 25 candidate(s)`), and `_mint_endpoint`'s
    #10997 gate HOLDS (rather than mints) an endpoint whenever the lookup is not
    `resolved`. Replaying against a `resolved` lookup would take the MINT arm
    and never reach the drop this test exists for.
    """
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {pointKind:'statement'}) "
        "WHERE (p.status IS NULL OR NOT (p.status IN $terminal)) "
        "RETURN p.id, p.content, p.when LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived",
                             "outdated"]},
    ).result_set
    return {"mode": "real", "degraded": False, "reason": None,
            "entities": [], "events": [],
            # ⛔ `when` IS PART OF THE REAL PRIOR ROW: `_enrich_point_priors`
            # copies the stored `p.when` onto every candidate, and the arm reads
            # it to order the two rivals by WORLD TIME (#14198: "facts are
            # compared on world-time dates, not arrival order"). A fixture that
            # omitted it exercised only the arrival-order fallback and could not
            # see the backfill gate at all (review F2 of #14794).
            "points": [{"id": r[0], "content": r[1], "kind": "statement",
                        "when": r[2]}
                       for r in rows],
            "queries_run": 1,
            "query_coverage": {"derived": {"points": 25},
                               # ``partial`` is the RECORDED state (11 of 25 did
                               # not run); ``resolved`` is the other lookup arm,
                               # which takes the MINT route (#4716 mint-vs-E7 folds
                               # the ref onto its graph id) instead of the hold.
                               "ran": {"points": 25 if coverage == "resolved" else 14},
                               "failed": {"points": 0}}}


def _question() -> dict:
    return {
        "question_id": "q14198",
        "haystack_session_ids": ["doc-01", "doc-05"],
        "haystack_dates": ["2026-02-03", "2026-03-05"],
        "haystack_sessions": [
            [{"role": "user",
              "content": f"ColdPath Logistics holds a validated "
                         f"certificate — {CERT}",
              "has_answer": True},
             {"role": "assistant", "content": "noted", "has_answer": False}],
            [{"role": "user",
              "content": f"The audit — {AUDIT}",
              "has_answer": True}],
        ],
    }


@pytest.fixture
def make_graph(tmp_path, monkeypatch):
    """Two captures on one fresh graph; ``relationship`` picks the declaration."""
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
    from tortoise.sdk import TortoiseSDK

    made = []

    def _make(relationship: str, coverage: str = "partial", **shape):
        monkeypatch.setattr(
            "tortoise.extractor_v2.search_graph",
            lambda sdk, embed_list, story, **kw: _fake_search(
                sdk, embed_list, story, coverage=coverage, **kw))
        sdk = TortoiseSDK(str(tmp_path / f"t14198-{relationship}-{coverage}.db"))
        made.append(sdk)
        stats = ingest_haystack_v2(sdk, _question(),
                                   model=_model(relationship, **shape))
        return sdk, stats

    yield _make
    for sdk in made:
        sdk.close()


def _scan(sdk, include_terminal=False):
    return sdk._tortoise_fts_query(query=None, kind="statement",
                                  entity_type="point", limit=20,
                                  include_terminal=include_terminal)


# ── the positive: the corpus's declared contradiction is REPRESENTED ──
def test_declared_cross_capture_contradiction_closes_the_older_claim(make_graph):
    """doc-05's declared NAND closes doc-01's claim: CORRECTS + a closed
    interval, both claims retained, the newer interval still open.

    REDS PRE-FIX: the NAND could not resolve its older endpoint in-capture, so
    it was dropped (`operator dropped: src/dst did not resolve…`) and
    ``supersessions_written`` was 0 — the state #14592 measured.
    """
    sdk, stats = make_graph("nand")
    assert stats["supersessions_written"] == 1, (
        "the declared cross-capture contradiction produced no supersession — "
        "this is the #14198/#14592 loss (older claim never closed)")

    # (1) the older claim is RETAINED and CLOSED, with what/when named on it.
    ended = [r for r in _scan(sdk, include_terminal=True)
             if r["id"] == _pid(CERT)]
    assert ended, "the contradicted claim must still be readable (record is truth)"
    old = ended[0]
    assert old["status"] in ("outdated", "superseded")
    assert old.get("valid_to") == "2026-03-05", (
        "the older world-time interval must end where the newer claim starts")
    assert old["expired_at"], "the ingestion-time expiry must be stamped"
    assert old["valid_from"] == "2026-02-03"

    # (2) the newer claim keeps its interval OPEN.
    live = [r for r in _scan(sdk) if r["id"] == _pid(AUDIT)]
    assert live, "the newer claim is the current one and must be retrievable"
    assert live[0].get("valid_to") in (None, ""), (
        "the newer interval stays open")

    # (3) a reader asking "what is true now" gets ONE answer …
    current_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(CERT) not in current_ids
    assert _pid(AUDIT) in current_ids

    # (4) … and "what did we believe before" still returns the old one, through
    # the CORRECTS chain — which is also the proof that the edge exists.
    hist = sdk.get_historical_knowledge(_pid(AUDIT), "2026-02-20")
    assert hist["found"] is True
    assert hist["valid_point"]["id"] == _pid(CERT)
    assert hist["valid_point"]["valid_to"] == "2026-03-05"
    assert [e["id"] for e in hist["chain"]] == [_pid(AUDIT), _pid(CERT)]


# ── the same declaration, in the shapes a real corpus reaches ────────────────
def test_the_other_emission_order_closes_the_same_claim(make_graph):
    """The SAME declared contradiction, emitted older-claim-first.

    REDS PRE-FIX (review F3, measured): the drop route keyed on `src` alone —
    with the model's NAND written `src = CERT, dst = AUDIT` the resolved side
    was `dst`, `src` was empty, the guard was false and the operator fell
    through to `operator dropped: src/dst did not resolve`. The contradiction
    was still lost on a corpus whose ONLY difference is emission order, and the
    module cannot repair it: `_canonicalize_nand_direction` returns `None` for
    an existing-graph endpoint, which a cross-capture NAND always has.

    The receipt this PR replays carries two `NAND direction canonicalized`
    lines from the same corpus, so the inverted emission is the observed shape,
    not a hypothetical one.
    """
    sdk, stats = make_graph("nand", inverted=True)
    assert stats["supersessions_written"] == 1, (
        "an inverted emission must close the same claim — the arm is symmetric")
    ended = [r for r in _scan(sdk, include_terminal=True)
             if r["id"] == _pid(CERT)]
    assert ended and ended[0]["status"] in ("outdated", "superseded")
    assert ended[0].get("valid_to") == AUDIT_WHEN
    live_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(AUDIT) in live_ids
    assert _pid(CERT) not in live_ids


def test_a_backfill_closes_the_claim_world_time_says_is_older(make_graph):
    """A later capture carrying an EARLIER world date (the backfill shape).

    REDS PRE-FIX (review F2, measured): the direction was decided by CAPTURE
    MEMBERSHIP, so the arm emitted `superseded = prior (2026-03-05),
    supersedes_by = new (2026-02-03)`. `sdk._supersede_point` refuses a
    successor whose resolved start sorts strictly before the predecessor's
    (#4021, `ValueError`), `commit_ops.apply_supersessions` counts it as a
    failed fold, and BOTH claims stay live — the #14198 defect arriving through
    the backfill door, warned as a generic failure.

    `#14198`'s recorded decision is *"Facts are compared on world-time dates,
    not arrival order"*, and it is what the SOTA in that issue does (Zep /
    Graphiti retire the edge with the EARLIER `valid_at`). So the newly
    ingested claim — the older fact — is the one whose interval closes, at the
    prior's own start, and the prior stays current.
    """
    sdk, stats = make_graph("nand", cert_when="2026-03-05",
                            audit_when="2026-02-03")
    assert stats["supersessions_written"] == 1, (
        "a backfilled contradiction must still close a claim — an inverted "
        "window is refused by the consumer and leaves both live")
    ended = [r for r in _scan(sdk, include_terminal=True)
             if r["id"] == _pid(AUDIT)]
    assert ended, "the newly ingested (older) claim must be retained"
    assert ended[0]["status"] in ("outdated", "superseded")
    assert ended[0].get("valid_to") == "2026-03-05", (
        "the older world-time interval ends where the newer claim starts")
    assert ended[0].get("valid_from") == "2026-02-03"
    live_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(CERT) in live_ids, "the newer fact stays current"
    assert _pid(AUDIT) not in live_ids


def test_an_event_side_is_dropped_not_recorded_as_a_supersession(make_graph):
    """A NAND whose emitted side is an Event closes nothing — and says so.

    REDS PRE-FIX (review F5, measured): `_resolve` returns `event_ids` as
    readily as `point_ids`, the arm accepted the event id as the successor, and
    the consumer then dispatched on the `pt_` prefix of the OLD id and refused
    the event SUCCESSOR (`_assert_lifecycle_guard(target)`). Net effect: the
    older claim was NOT closed (the contradiction lost) while the warning
    claimed *"the contradiction is recorded as a supersession"*. There is no
    point-supersede channel to an Event, so the arm must decline and let the
    drop warning tell the truth.
    """
    sdk, stats = make_graph("nand", event_side=True)
    assert stats["supersessions_written"] == 0, (
        "an Event is not a point successor — nothing may be recorded")
    live_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(CERT) in live_ids, "nothing was closed, so it stays current"
    sample = " ".join(stats["warnings"]["sample"])
    assert "operator dropped" in sample, (
        "the honest outcome is the pre-existing drop warning")
    assert "recorded as a supersession" not in sample, (
        "the run must not claim a closure the consumer refused")


# ── negatives: nothing is invented ───────────────────────────────────────────
def test_declared_support_across_captures_closes_nothing(make_graph):
    """Over-fire negative: the SAME two documents, with the model declaring
    SUPPORT (`IMPL`) instead of a contradiction, must close nothing.

    A support edge is not a reversal — an older claim that a newer one agrees
    with keeps its interval open.
    """
    sdk, stats = make_graph("impl")
    assert stats["supersessions_written"] == 0
    live_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(CERT) in live_ids, "an agreed-with claim must stay current"
    assert _pid(AUDIT) in live_ids
    # the older claim's interval is still OPEN, and it is still CURRENT
    unreceived = [r for r in _scan(sdk, include_terminal=True)
                  if r["id"] == _pid(CERT)]
    assert unreceived and unreceived[0].get("valid_to") in (None, "")
    # with no CORRECTS the chain behind the new point is itself — never the cert
    hist = sdk.get_historical_knowledge(_pid(AUDIT), "2026-03-10")
    assert hist["found"] is True
    assert [e["id"] for e in hist["chain"]] == [_pid(AUDIT)]


def test_undeclared_reversal_invents_no_contradiction(make_graph):
    """Over-fire negative: a corpus that reverses itself in its WORDING but
    declares no contradiction gains none.

    This is the measured shape of the #14592 corpus's decision pair — "We
    choose ColdPath Logistics as our delivery partner" vs "We revert to
    NordicFreight …" share only ~0.54 of their tokens and carry no declared
    edge, so no lexical rule may close either: `#14198`'s ruling is that the
    model labels and code does the arithmetic, never that code guesses.
    """
    sdk, stats = make_graph("none")
    assert stats["supersessions_written"] == 0
    live_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(CERT) in live_ids
    assert _pid(AUDIT) in live_ids

    # ⛔ THIS CASE CANNOT FALSIFY THE ARM, and it is kept as a PIN, not as
    # coverage (review F6 of #14794, measured): `_embed_for` emits NO operator
    # at all for the `none` relationship, so no edit to the arm's guard,
    # direction or warning can change these three assertions. They pin the
    # pre-existing lexical machinery (`classify_consolidation` / `_boundary`),
    # which this change does not touch, and they are honest only when read that
    # way. The arm's OVER-fire is graded by the `impl` case above and its
    # UNDER-fire by the positive cases.


# ── the boundary with #14721/#14737: the closed interval is VISIBLE to a read ─
def test_a_current_scope_query_no_longer_answers_with_the_contradicted_claim(
        make_graph):
    """#14592's failing probe, re-asked after the closure.

    The report's `contradiction` probe ranked the claim doc-05 refutes FIRST
    (`status: live`, conf 0.5) and never surfaced the refutation. That was the
    representation gap showing through the read: the ranking half (#14721 /
    #14737) is a different scope, but it can only ever be fixed if the closed
    claim is marked closed. So the assertion here is deliberately about
    VISIBILITY, not about ranking: a current-scope query for the old claim's
    own wording no longer answers with it, and the terminal scope still does.
    """
    sdk, stats = make_graph("nand")
    assert stats["supersessions_written"] == 1

    current = sdk._tortoise_fts_query(
        query="validated cold-chain certificate", kind="statement",
        entity_type="point", limit=10)
    assert _pid(CERT) not in {r["id"] for r in current}, (
        "a claim whose interval is closed must not answer a current-scope query")

    history = sdk._tortoise_fts_query(
        query="validated cold-chain certificate", kind="statement",
        entity_type="point", limit=10, include_terminal=True)
    assert _pid(CERT) in {r["id"] for r in history}, (
        "the record is truth: the closed claim must still be reachable")


# ── the SAME declaration, on the other lookup arm ───────────────────────────
def test_the_closure_does_not_depend_on_which_lookup_arm_ran(make_graph):
    """A RESOLVED identity lookup takes a different internal route to the same
    declaration — `#4716`'s mint-vs-E7 fold puts the older ref on its graph id,
    so the NAND WIRES instead of dropping — and must close the same interval.

    This is the asymmetry that would otherwise make the representation a
    function of lookup quality: measured on the #14592 receipt's own shape, a
    `partial` lookup produced `operators: []` and a `resolved` one produced the
    NAND edge, and pre-fix neither closed anything. Both now close; the resolved
    arm additionally keeps its NAND because that edge carries EP weight and
    nothing in this change may subtract it.
    """
    sdk, stats = make_graph("nand", "resolved")
    assert stats["supersessions_written"] == 1, (
        "the closure must not depend on which lookup arm ran")

    old = [r for r in _scan(sdk, include_terminal=True) if r["id"] == _pid(CERT)]
    assert old and old[0].get("valid_to") == "2026-03-05"
    assert _pid(CERT) not in {r["id"] for r in _scan(sdk)}
    hist = sdk.get_historical_knowledge(_pid(AUDIT), "2026-02-20")
    assert hist["found"] is True
    assert hist["valid_point"]["id"] == _pid(CERT)
