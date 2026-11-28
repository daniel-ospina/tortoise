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

⛔ THREE gates keep that honest, and EACH is pinned by a case that reds when its
gate is removed. The arm is SYMMETRIC on emission order (the model may write the
pair either way, and `_canonicalize_nand_direction` cannot repair a cross-capture
NAND); the two rivals are ordered on the SAME instants the write path refuses on
(#14198: *"facts are compared on world-time dates, not arrival order"*), so a
backfill is DECLINED rather than emitted as a window the consumer refuses; and
the emitted side must be a point this capture will WRITE (`_written_point_ids` —
removing it reds the event-side AND minted-successor cases, measured). ⚠️ An
earlier revision of this paragraph described a separate `pt_` gate and a
`_capture_ids` gate; both were removed as INERT (every payload point id satisfies
them by construction), so a docstring naming them would send the next maintainer
looking for a guard that no longer exists.

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
    # ⛔ READ THROUGH THE SDK, NOT A RAW CYPHER RECEIVER. `tools/sdk_privatise.py
    # --check` is a RATCHET on untyped `.query(`/`.update(` receivers, and this
    # fixture used to add two of them to a file that had none — which reds the
    # REQUIRED `surface-guard` job (measured: `the untyped-receiver count ROSE
    # by 2 … tests/test_14198_contradiction_closure.py`). `_tortoise_fts_query`
    # is the product's own read path, returns the same live statement rows, and
    # carries `valid_from` — which is the prior's world-time start, so the
    # `when` the arm orders on is still exercised.
    rows = _scan(sdk, include_terminal=False)
    return {"mode": "real", "degraded": False, "reason": None,
            "entities": [], "events": [],
            # ⛔ `when` IS PART OF THE REAL PRIOR ROW: `_enrich_point_priors`
            # copies the stored `p.when` onto every candidate, and the arm reads
            # it to order the two rivals by WORLD TIME (#14198: "facts are
            # compared on world-time dates, not arrival order"). A fixture that
            # omitted it exercised only the arrival-order fallback and could not
            # see the backfill gate at all (review F2 of #14794).
            "points": [{"id": r["id"], "content": r["content"],
                        "kind": "statement",
                        "when": r.get("valid_from") or r.get("when") or ""}
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

    made: list = []

    def _make(relationship: str, coverage: str = "partial", **shape):
        monkeypatch.setattr(
            "tortoise.extractor_v2.search_graph",
            lambda sdk, embed_list, story, **kw: _fake_search(
                sdk, embed_list, story, coverage=coverage, **kw))
        # ⛔ A UNIQUE FILE PER CALL. Keyed on (relationship, coverage) alone, a
        # test that builds TWO graphs with the same pair — e.g. two world-time
        # shapes of the same declaration — silently opened the SAME database for
        # both, so the second run's prior set contained the first run's points
        # and the case under test was not the case that ran.
        made_n = len(made)
        sdk = TortoiseSDK(
            str(tmp_path / f"t14198-{relationship}-{coverage}-{made_n}.db"))
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


def test_a_backfill_DECLINES_and_says_so(make_graph):
    """A later capture carrying an EARLIER world date (the backfill shape).

    `#14198`'s recorded decision is *"Facts are compared on world-time dates,
    not arrival order"* — so here world time says the PRIOR is the newer fact and
    the newly-ingested claim is the one whose interval should close.

    ⛔ AND THIS CHANNEL CANNOT EXPRESS THAT (review round 2, measured on the
    capture seam): the supersession record's `superseded` side must ALREADY be a
    graph id — `commit_ops.remap_supersession_point_refs` remaps only
    `supersedes_by`, and that asymmetry is deliberate (the `pt_` prefix is the
    record's LANE DISCRIMINATOR downstream). A payload id on that side names no
    node, `apply_supersessions` skips it FAIL-OPEN ("point supersession ref …
    not found — skipped (fail-open)"), both claims stay live — while the
    extractor's own warning claimed the interval was closed. That is precisely
    the false-success class this arm exists to remove, so the arm now DECLINES
    the backfill direction and says what it did and did not do.

    The test that this replaced asserted a closure the CONSUMER could not
    perform: the eval lane hides the gap because its writer creates points with
    `id=pid` verbatim, so payload id == graph id there by construction.
    """
    sdk, stats = make_graph("nand", cert_when="2026-03-05",
                            audit_when="2026-02-03")
    assert stats["supersessions_written"] == 0, (
        "the backfill direction must be DECLINED, not emitted as a record the "
        "consumer will skip while the extractor claims success")
    # both claims are untouched: nothing closed, nothing deleted. The graph is
    # the assertion because the DECLINE must leave the record exactly as it was.
    live_ids = {r["id"] for r in _scan(sdk)}
    assert _pid(CERT) in live_ids, "the prior claim stays current"
    assert _pid(AUDIT) in live_ids, "the newly ingested claim stays current"
    # the warning that explains the decline is asserted in the pure-function
    # test below, which can read the payload's own `warnings` — this test exists
    # for what only a graph can show: nothing was folded and nothing was lost.


def test_a_backfill_declines_in_the_payload_and_names_the_reason():
    """The same decline, read where the payload's own warnings are visible."""
    out, payload = _shape_run({
        "entities": [], "events": [],
        "points": [_statement(AUDIT, when="2026-02-03")],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, _RESOLVED, prior_when="2026-04-01")  # the PRIOR is the newer fact
    assert out.get("supersessions") == [], (
        "the backfill direction cannot be expressed by this channel")
    said = [w for w in out.get("warnings") or [] if "not closed" in w]
    assert said and "cannot express that direction" in said[0]
    # the declared edge is kept as it was — the decline costs no evidence
    assert payload.get("operators"), (
        "declining the closure must not also drop the declared edge")


def test_an_absent_start_declines_and_keeps_the_declared_edge():
    """No world-time instant on the NEW side → DECLINE, and keep the NAND.

    ⛔ THE P2 THIS PINS (#14794 review, measured). The direction gate used to
    fall through to ARRIVAL ORDER when the payload point named no `when`, on the
    reasoning that "with no parseable start there is no instant to compare".
    That premise is false about the WRITE side, so the fallback was not a
    tie-break — it was a coin flip the consumer then refused:

    * `sdk.py` gives a `when`-less point a `validFrom` taken from an ASSERTED
      date in its content (`_asserted = asserted_date(content)`), and
    * a dedup hit writes no props at all, so a restated claim keeps the
      canonical's OLDER stored start.

    Both put the successor's stored start somewhere this pass cannot see; the
    record is emitted inverted, `sdk._supersede_point` refuses the window
    (#4021), and — because this arm SUPPRESSES the operator — the declaration
    ends in NO representation at all: 0 NAND edges, 0 CORRECTS edges, both
    claims live, beside a receipt claiming the interval was closed. Measured on
    the pre-fix head with a `when`-less payload point:
    `operators == []`, `applied: 0`, the consumer raising
    `InvertedSupersedeWindow` — while the SAME payload on the base tree KEEPS
    the NAND, so the arm was strictly worse than not existing on that shape.

    The gate therefore DECLINES and leaves the declared edge exactly as it
    would be without this arm. Both lookup arms are exercised: the decline must
    not become an operator DROP on the wiring route, which is the regression
    that made the loss invisible in the first place.
    """
    for coverage, label in ((_RESOLVED, "resolved"), (_PARTIAL, "partial")):
        out, payload = _shape_run({
            "entities": [], "events": [],
            # ⛔ NO `when` KEY AT ALL: the shape that took the arrival branch.
            "points": [{"content": AUDIT, "pointKind": "statement",
                        "about_entities": ["ColdPath Logistics"],
                        "quote": AUDIT, "search_keys": ["cold-chain audit"]}],
            "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
        }, coverage)
        assert out.get("supersessions") == [], (
            f"[{label}] an unordered pair must not be closed on arrival order — "
            "the consumer refuses the inverted window, and because this arm "
            "suppresses the operator the declaration then survives in NEITHER "
            "representation")
        said = [w for w in out.get("warnings") or [] if "not closed" in w]
        assert said and "BOTH" in said[0], (
            f"[{label}] the decline must name the missing instant as its reason, "
            f"rather than reporting a closure: {said}")
        assert not any("is recorded as a supersession" in w
                       for w in out.get("warnings") or []), (
            f"[{label}] the run must not claim a closure it did not record")
        if coverage is _RESOLVED:
            # ⛔ THE COSTLY HALF: declining must not also DROP the declared edge.
            assert payload.get("operators"), (
                f"[{label}] declining the closure must not suppress the declared "
                "NAND — that is how the pre-fix head lost the declaration")


def test_an_event_side_is_dropped_not_recorded_as_a_supersession(make_graph):
    """A NAND whose emitted side is an Event closes nothing — and says so.

    Passes on the base commit (nothing closes there) AND is real coverage for
    the surviving guard: removing `_written_point_ids` reds THIS case and the
    minted-successor one (measured — review of #14794, cycle 4). The separate
    point-only (`pt_`) test that used to sit beside it was INERT once that guard
    became the authority and has been removed, so this case names the mechanism
    that actually refuses an Event side. It was RED in an earlier cut of the arm,
    which had no point-only gate at all (review F5):
    `_resolve` returns `event_ids` as
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


def test_a_world_time_ordering_is_decided_on_instants_not_date_prefixes(make_graph):
    """The ORDER must be the one the write path REFUSES on.

    REDS PRE-FIX (review of #14794, P2, measured): the gate compared the first
    ten characters of the two `when` values, while `sdk._supersede_window_end`
    decides the #4021 inverted window on INSTANTS
    (`search_engine._created_sort_key`) — and the gate DECLINES a backfill (this
    channel cannot express `superseded = <a claim this capture has not written
    yet>`), so a mis-ordered pair lands on the wrong SIDE of that decision:

    * CROSS-OFFSET — `cert 2026-03-06T01:00:00Z` (arrived first) vs
      `audit 2026-03-05T23:00:00-05:00` (= 04:00Z on 03-06): the date prefixes
      compare BACKWARDS, so the arm saw a BACKFILL and DECLINED a closure the
      instants say is perfectly ordinary (the newer fact arrived later, which is
      the direction this channel carries). Pre-fix: `0` supersessions; the
      contradiction survives as two live truths.
    * SAME DAY, world time says the ARRIVING claim is the older fact
      (`cert 18:00Z` then `audit 09:00Z`): the date prefixes TIE, so the arm took
      the ARRIVAL branch and emitted `superseded=cert, supersedes_by=audit`.
      The consumer refused the inverted window (#4021) — leaving both claims
      live WHILE the same receipt warned that the interval "is recorded as a
      supersession". That false claim is the class this arm exists to remove;
      the decline names it instead.

    So the two shapes pin opposite halves of one measure, and each reds under
    the date-prefix implementation.
    """
    # cross-offset: the date prefixes invert what the instants say
    sdk, stats = make_graph("nand", cert_when="2026-03-06T01:00:00Z",
                            audit_when="2026-03-05T23:00:00-05:00")
    assert stats["supersessions_written"] == 1, (
        "an offset must not flip the direction the instants agree on — this is "
        "an ordinary closure, not a backfill")
    ended = [r for r in _scan(sdk, include_terminal=True)
             if r["id"] == _pid(CERT)]
    assert ended and ended[0]["status"] in ("outdated", "superseded")
    assert _pid(AUDIT) in {r["id"] for r in _scan(sdk)}

    # same day: the arriving claim IS the older fact in world time → declined,
    # and the receipt must not claim otherwise
    sdk2, stats2 = make_graph("nand", cert_when="2026-03-05T18:00:00Z",
                              audit_when="2026-03-05T09:00:00Z")
    assert stats2["supersessions_written"] == 0, (
        "a backfill is decided by INSTANTS; the channel cannot express it")
    live = {r["id"] for r in _scan(sdk2)}
    assert _pid(CERT) in live and _pid(AUDIT) in live, (
        "neither interval may be closed when the direction is declined")
    sample = " ".join(stats2["warnings"]["sample"])
    assert "not closed (#14198)" in sample, (
        "the decline must be named, with the two world times")
    assert "recorded as a supersession" not in sample, (
        "the run must not claim a closure it did not write")


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
    NAND edge, and pre-fix neither closed anything. Both now produce the SAME
    payload — one supersession record, NO operator — because `_supersede_point`
    re-points (and, for a self-edge, deletes) the incident `IMPL|NAND` rows when
    it terminalises the predecessor, so an emitted NAND would be an edge the
    commit immediately removes. The `CORRECTS` edge is the representation on
    both arms.
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

    # ⛔ ONE PAYLOAD, BOTH ARMS, graded WITHOUT a raw Cypher receiver (the
    # `sdk_privatise` ratchet counts those; see `_fake_search`). The resolved
    # route must not ALSO leave the declared NAND between the two rivals — the
    # closure IS the representation.
    _out, _payload = _shape_run({
        "entities": [], "events": [],
        "points": [_statement(AUDIT, when="2026-03-05")],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, _RESOLVED, prior_when="2026-02-03")
    _pair = {_pid(CERT), _pid(AUDIT)}
    assert not [o for o in (_payload.get("operators") or [])
                if {str(o.get("src")), str(o.get("dst"))} & _pair], (
        "the closure IS the representation — the declared NAND between the two "
        "rivals must not also be emitted on the resolved route")
    assert len(_out.get("supersessions") or []) == 1


# ── guards found in review, pinned as pure-function shapes ───────────────────
# `execute_embed` is pure, so each guard is a short assertion instead of a graph
# run. Both shapes below were LIVE defects in an earlier cut of this arm.
PRIOR_ID = _pid(CERT)
_PARTIAL = {"derived": {"points": 25}, "ran": {"points": 14},
            "failed": {"points": 0}}
_RESOLVED = {"derived": {"points": 25}, "ran": {"points": 25},
             "failed": {"points": 0}}


def _shape_run(embed_list, coverage, prior_when="2026-02-03"):
    from tortoise.extractor_v2 import execute_embed

    search = {"points": [{"id": PRIOR_ID, "content": CERT,
                          "kind": "statement", "when": prior_when}],
              "query_coverage": coverage}
    out = execute_embed(embed_list, search, session_id="shape",
                        session_date="2026-03-05")
    return out, (out.get("payload") or {})


def _statement(content, when="2026-03-05"):
    return {"content": content, "pointKind": "statement",
            "about_entities": ["ColdPath Logistics"], "when": when,
            "quote": content, "search_keys": ["cold-chain audit"]}


def test_a_minted_successor_is_never_named_as_a_superseder():
    """The successor must be a point this capture will WRITE.

    ⛔ THE P1 THIS PINS (review of #14794, measured): when the newer side is
    named only inside the edge, the `#2552` mint pre-pass materialises it — and
    the prune deletes every minted endpoint no surviving payload operator
    references. This arm SUPPRESSES the operator, so with a minted successor the
    document's own claim was deleted from the payload, the record named a point
    that was never written (`point supersede … failed: No point …`,
    `supersessions_written: 0`), and the extractor still warned that the
    interval was closed. The arm must DECLINE instead — today's behaviour (the
    mint, the edge, no closure) is strictly better than losing both sides.
    """
    for coverage, label in ((_RESOLVED, "resolved"), (_PARTIAL, "partial")):
        out, payload = _shape_run({
            "entities": [], "events": [], "points": [],
            "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
        }, coverage)
        assert out.get("supersessions") == [], (
            f"[{label}] a minted successor must not be named as a superseder — "
            "the prune would delete it and the closure would be refused")
        assert not any("is recorded as a supersession" in w
                       for w in out.get("warnings") or []), (
            f"[{label}] the run must not claim a closure it did not record")
        if label == "resolved":
            # the minted claim must still reach the payload: the prune only
            # removes what no surviving operator references, and here the
            # operator is NOT suppressed.
            assert {str(p.get("id")) for p in payload.get("points") or []}, (
                "[resolved] the model's own claim must still be written")
        # ⛔ On the PARTIAL arm the claim is not written at all — that is the
        # PRE-EXISTING #10997 hold (`hold_store` is None on the shipping path),
        # not this arm's doing, and it is a further reason to decline.


def test_a_second_refuter_keeps_its_edge():
    """One closure per older claim; a second refuter is still EVIDENCE.

    `apply_supersessions` treats a terminal target as an idempotent no-op and
    skips it SILENTLY, so suppressing BOTH operators erased the second attack
    from the graph with nothing said. Measured on the cut that had the closure
    but not the budget (`26b12fe`): `supersessions: 2`,
    `supersessions_written: 1`, `operators: 0`, one NAND edge nowhere.
    """
    second = "ColdPath does not hold a validated certificate for our routes"
    out, payload = _shape_run({
        "entities": [], "events": [],
        "points": [_statement(AUDIT), _statement(second)],
        "operators": [
            {"op_type": "NAND", "src": AUDIT, "dst": CERT},
            {"op_type": "NAND", "src": second, "dst": CERT},
        ],
    }, _RESOLVED)
    records = out.get("supersessions") or []
    assert len(records) == 1, "one closure per prior claim"
    assert records[0]["superseded"] == PRIOR_ID
    ops = payload.get("operators") or []
    assert len(ops) == 1, (
        "the second refuter must keep its NAND — the payload cannot skip it "
        "silently the way the consumer would")
    assert str(ops[0]["src"]) == _pid(second)


def test_a_declined_backfill_does_not_block_a_closable_refuter():
    """A DECLINE must not spend the one-closure-per-prior budget (#14198 r3).

    ⛔ MEASURED DEFECT: `_closed_prior_ids.add` ran BEFORE the backfill test, so
    a declined pair marked the prior as already closed. A LATER refuter of the
    same prior with a forward world date then returned at the membership guard —
    no record, and NO WARNING — while the only line in the payload about that
    prior was the decline's, which asserts the prior is the NEWER fact. That is
    false for the refuter it silently skipped, and the #14198 defect (two live
    truths) survived in a shape where the pass had everything it needed.

    The budget is spent by a record that is actually WRITTEN, and by nothing
    else, so the forward refuter closes and the declined one keeps its edge.
    """
    later = "ColdPath's certificate was revoked in the March audit"
    out, payload = _shape_run({
        "entities": [], "events": [],
        "points": [_statement(AUDIT, when="2026-02-03"),      # backfill
                   _statement(later, when="2026-05-01")],     # forward
        "operators": [
            {"op_type": "NAND", "src": AUDIT, "dst": CERT},
            {"op_type": "NAND", "src": later, "dst": CERT},
        ],
    }, _RESOLVED, prior_when="2026-04-01")  # the PRIOR is the newer fact
    records = out.get("supersessions") or []
    assert len(records) == 1, (
        "the declined backfill must not consume the closure — the forward "
        f"refuter can close this prior; got {records}")
    assert records[0]["superseded"] == PRIOR_ID
    assert records[0]["supersedes_by"] == _pid(later), (
        "the closure must come from the refuter that CAN express it")
    # The DECLINED edge survives — declining a closure is never dropping
    # evidence. The CLOSING one does not: its operator is suppressed by design,
    # because `_supersede_point` re-points/deletes the incident NAND when it
    # terminalises the predecessor, so the CORRECTS record is the representation.
    ops = {str(o.get("src")) for o in payload.get("operators") or []}
    assert _pid(AUDIT) in ops, (
        "the declined backfill's edge must be kept — it is the only record of "
        "that refutation")
    assert _pid(later) not in ops, (
        "the closing refuter's edge is replaced by the CORRECTS record")


def test_the_closure_order_does_not_depend_on_the_model_list_order():
    """The SAME two refuters must close the same claim in EITHER list order.

    ⛔ MEASURED (review of #14794, cycle 3): with the budget taken above the
    world-time gate, a payload whose DECLINED refuter came first kept 0 closures
    while the same pair in the other order kept 1 — the outcome was a function
    of the model's list order. `test_a_declined_backfill_does_not_block_a_closable_refuter`
    pins the shape; this pins the PROPERTY the shape exists for, because a fix
    that special-cased one order would pass that case and still be order-bound.
    """
    later = "ColdPath's certificate was revoked in the March audit"
    ops = [{"op_type": "NAND", "src": AUDIT, "dst": CERT},
           {"op_type": "NAND", "src": later, "dst": CERT}]
    for label, ordered in (("decline-first", ops), ("closure-first", list(reversed(ops)))):
        out, _payload = _shape_run({
            "entities": [], "events": [],
            "points": [_statement(AUDIT, when="2026-02-03"),
                       _statement(later, when="2026-05-01")],
            "operators": ordered,
        }, _RESOLVED, prior_when="2026-04-01")
        records = out.get("supersessions") or []
        assert len(records) == 1, f"[{label}] exactly one closure, got {records}"
        assert records[0]["superseded"] == PRIOR_ID
        assert records[0]["supersedes_by"] == _pid(later), (
            f"[{label}] the closure must come from the refuter that can express it")


def test_the_backfill_decline_is_instant_granular_not_day_granular():
    """The decline and the consumer must agree on the SAME measure.

    ⛔ MEASURED (review of #14794, P1): the gate first compared `when[:10]`
    (DATE PREFIXES) while `sdk._supersede_window_end` refuses on INSTANTS
    (`search_engine._created_sort_key`). A same-day inversion therefore TIES on
    the date, takes the emitting branch, and the consumer refuses it (#4021) —
    both claims live WHILE the payload warns the interval was closed, which is
    the false-success class this whole arm exists to remove. A cross-offset pair
    fails the other way, declining a closure the instants say is carryable.
    """
    # SAME DAY, two hours apart, the later one already in the graph: a backfill.
    out, _payload = _shape_run({
        "entities": [], "events": [],
        "points": [_statement(AUDIT, when="2026-03-05T08:00:00")],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, _RESOLVED, prior_when="2026-03-05T10:00:00")
    assert out.get("supersessions") == [], (
        "a same-day inversion must be DECLINED, not emitted for the consumer "
        "to refuse while the payload reports a closure")
    assert any("not closed" in w for w in out.get("warnings") or []), (
        "the decline must be stated, not inferred from an absent record")

    # CROSS-OFFSET, the ordinary direction once the offsets are applied:
    # prior 2026-03-06T01:00:00Z, this capture 2026-03-05T23:00:00-05:00
    # (= 04:00Z on 03-06) — the date prefixes invert, the instants do not.
    out, _payload = _shape_run({
        "entities": [], "events": [],
        "points": [_statement(AUDIT, when="2026-03-05T23:00:00-05:00")],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, _RESOLVED, prior_when="2026-03-06T01:00:00Z")
    assert len(out.get("supersessions") or []) == 1, (
        "a cross-offset pair whose INSTANTS are in the ordinary direction must "
        "close — declining it on the date prefix loses a carryable closure")


def test_the_decline_does_not_promise_an_edge_it_cannot_keep():
    """The decline's warning must not assert retention it cannot guarantee.

    `_close_prior_contradiction` returns to a caller that EITHER wires the edge
    (the older side resolved) OR drops it (the `partial` lookup — the #14592
    receipt's own recorded state). The warning first said "the declared edge is
    kept as it was", which is false on the drop route: measured, that payload
    carries the decline sentence AND `operator dropped: src/dst did not resolve`
    with `operators: []`.
    """
    for coverage, label, wires in ((_PARTIAL, "partial", False),
                                   (_RESOLVED, "resolved", True)):
        out, payload = _shape_run({
            "entities": [], "events": [],
            "points": [_statement(AUDIT, when="2026-02-03")],
            "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
        }, coverage, prior_when="2026-04-01")
        assert out.get("supersessions") == []
        said = [w for w in out.get("warnings") or [] if "not closed" in w]
        assert said, f"[{label}] the decline must be stated"
        assert "kept as it was" not in said[0], (
            f"[{label}] the warning must not promise retention — on the "
            "partial route the edge is dropped, and saying otherwise puts two "
            "contradictory lines in one receipt")
        has_op = bool(payload.get("operators"))
        assert has_op is wires, (
            f"[{label}] the decline routes through to the ordinary handling — "
            f"edge wired={wires}, got {has_op}")


def test_an_id_less_prior_row_can_never_be_the_superseded_side():
    """`not in this capture` is NOT `a prior graph id` (review of #14794, P2).

    Consolidation step 1 folds onto a CONTENT-ADDRESSED payload id when a prior
    row carries no `id` (it iterates the prior rows with no id filter, while the
    UPDATE loop below skips id-less priors — the ladder is asymmetric). The
    wiring route treated "the id is not one this capture emitted" as proof that
    the older side is in the graph, so that fallback id could land on
    `superseded`, where `commit_ops.remap_supersession_point_refs` will not
    remap it and `apply_supersessions` skips the record FAIL-OPEN — both claims
    live, warning claiming a closure. The module already names this id class:
    it "names NO graph point — never emit a record onto it".
    """
    prior = {"content": CERT, "kind": "statement", "when": "2026-02-03"}  # no id
    out = None
    from tortoise.extractor_v2 import execute_embed

    search = {"points": [prior], "query_coverage": _RESOLVED}
    out = execute_embed({
        "entities": [], "events": [],
        "points": [_statement(AUDIT)],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, search, session_id="shape", session_date="2026-03-05")
    written = {str(p.get("id")) for p in (out.get("payload") or {}).get("points") or []}
    for rec in out.get("supersessions") or []:
        assert str(rec["superseded"]) in written or rec["superseded"] in {
            str(r.get("id")) for r in search["points"] if r.get("id")}, (
            "the superseded side must name a node the graph HAS — got "
            f"{rec['superseded']!r}, which this payload never writes and the "
            "prior set never names")


def test_a_prior_another_producer_closed_keeps_this_declaration_as_evidence():
    """A prior closed by ANOTHER producer must not swallow this refutation.

    ⛔ MEASURED (#14198 review 4, P1): a point carrying a declared `supersedes`
    ref appends its own record — `CERT ← NEW` — before the operator pass runs.
    `_closed_prior_ids` is this ARM's private budget, so it did not know, and the
    arm appended a SECOND record for the same prior and `continue`d, suppressing
    the operator. `apply_supersessions` skips a terminal target with a bare
    `continue` (no warning, not even a `skipped` entry), so the declared
    contradiction vanished from BOTH representations — no NAND edge, no CORRECTS
    edge to this claim, nothing in the census to notice it — and the warning
    attributed the closure to this pair.

    ⚠️ HONEST SPLIT: on the base commit this test reds ONLY on the last
    assertion (`said == []` — there is no arm, so nothing explains the
    closure); the first two pass there because the NAND survives when no arm
    exists at all. So the structural half is a PIN against the mutation that
    reinstates the private budget alone — measured: `supersessions` gains
    `(CERT, AUDIT)` and `operators` goes `[NAND]` → `[]` — and only the warning
    half is new coverage.
    """
    lapsed = "ColdPath Logistics's cold-chain certificate lapsed in March"
    out = None
    from tortoise.extractor_v2 import execute_embed

    search = {"points": [{"id": PRIOR_ID, "content": CERT, "kind": "statement",
                          "when": "2026-01-05"}],
              "query_coverage": _RESOLVED}
    out = execute_embed({
        "entities": [], "events": [],
        "points": [_statement(lapsed),
                   _statement(AUDIT),
                   {**_statement(lapsed), "supersedes": CERT}],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, search, session_id="shape", session_date="2026-03-05")
    records = out.get("supersessions") or []
    assert [r["superseded"] for r in records].count(PRIOR_ID) == 1, (
        "one record per prior: the consumer drops a second one silently, so a "
        f"duplicate looks like two closures and is one — got {records}")
    ops = (out.get("payload") or {}).get("operators") or []
    assert any(str(o.get("src")) == _pid(AUDIT) and o.get("op_type") == "NAND"
               for o in ops), (
        "the declared NAND must survive as the evidence of this refutation — "
        "the other producer's record already closed the claim, so suppressing "
        "the edge erases the only trace that THIS document was refuted")
    said = [w for w in out.get("warnings") or []
            if "ALREADY closed by" in w]
    assert said, (
        "the receipt must say the claim was already closed, and by which "
        "successor — otherwise the closure is attributed to the wrong document")


def test_the_cross_producer_warning_does_not_promise_retention_on_the_drop_route():
    """The same guard, on the route where the operator IS dropped.

    ⛔ MEASURED (#14198 review 5, P1): the warning said "The declared NAND is
    kept as the evidence of this refutation" UNCONDITIONALLY — false on the
    `partial` identity lookup, which is the #14592 corpus's own recorded state.
    There the arm returns False, the caller falls through, and the operator is
    dropped: the payload carries `operators: []` AND the retention sentence AND
    `operator dropped: src/dst did not resolve …`. A reader auditing the receipt
    concludes the refutation survived as a NAND when in fact neither an edge nor
    a closure by this pair exists — the false-success class this PR exists to
    remove, and the defect round 3 fixed one branch away.
    """
    lapsed = "ColdPath Logistics's cold-chain certificate lapsed in March"
    from tortoise.extractor_v2 import execute_embed

    search = {"points": [{"id": PRIOR_ID, "content": CERT, "kind": "statement",
                          "when": "2026-01-05"}],
              "query_coverage": _PARTIAL}   # the recorded state: the hold route
    out = execute_embed({
        "entities": [], "events": [],
        "points": [_statement(lapsed),
                   _statement(AUDIT),
                   {**_statement(lapsed), "supersedes": CERT}],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": CERT}],
    }, search, session_id="shape", session_date="2026-03-05")
    ops = (out.get("payload") or {}).get("operators") or []
    assert ops == [], (
        "on the hold route this arm does not keep the edge — the pre-existing "
        f"#10997 hold drops it, so the payload cannot be expected to carry it (got {ops})")
    said = [w for w in out.get("warnings") or [] if "ALREADY closed by" in w]
    assert said, "the receipt must still say the claim was already closed"
    assert "kept as the evidence" not in said[0], (
        "the warning must not promise an edge this route drops")
    assert any("operator dropped" in w for w in out.get("warnings") or []), (
        "and the drop must still be stated by its own line")


def test_a_fuzzy_closure_still_discloses_its_recall_band():
    """A closure decided on a NON-EXACT match must still say so (#8503).

    ⛔ MEASURED (#14198 review 4, P2): the NAND route needs a LOCAL warning sink
    — forwarding the resolver's refusals would mint a replace-channel loss that
    did not happen — but the sink first swallowed its SUCCESS line too: `#8503`'s
    recall-band disclosure, which is the only audit trail for terminalising a
    live belief on an edge-trimmed match (and there is no un-supersede API to
    repair a wrong one). Its line reads `point-supersede recall band (#8503):
    ref …` — a DIFFERENT shape from the `point supersedes=` refusals
    (hyphenated, no `=`), so it is forwarded by its own words.
    """
    from tortoise.extractor_v2 import execute_embed

    # ONE edge-trippable frame word in front (`_EDGE_TRIMMABLE` — "a", "the",
    # "we" … are dropped at the edges; "so"/"thus"/"however" are load-bearing
    # and refuse, which is a different test).
    framed = f"a {CERT}"
    search = {"points": [{"id": PRIOR_ID, "content": CERT, "kind": "statement",
                          "when": "2026-01-05"}],
              "query_coverage": _PARTIAL}
    out = execute_embed({
        "entities": [], "events": [],
        "points": [_statement(AUDIT)],
        "operators": [{"op_type": "NAND", "src": AUDIT, "dst": framed}],
    }, search, session_id="shape", session_date="2026-03-05")
    assert out.get("supersessions"), (
        "the edge-trimmed ref must resolve — this test is about what the run "
        "SAYS when it does")
    said = " ".join(str(w) for w in out.get("warnings") or [])
    assert "recall band" in said, (
        "a closure decided on a non-exact match must disclose its recall band; "
        "silently dropping it hides that a live belief was terminalised on a "
        "fuzzy match")
