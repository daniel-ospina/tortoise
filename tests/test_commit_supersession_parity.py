"""#2164 Task 9a — differential parity: the shared apply_supersessions helper
vs the hosted §6b inline consumer produce IDENTICAL supersession END-STATE.

WHY THIS TEST EXISTS (drift-recurrence guard): #2164's root cause was THREE
divergent consumers of the supersession discipline (capture, eval ingest, and
the hosted commit endpoint), each resolving/folding supersession records
differently. ``tortoise.commit_ops.apply_supersessions`` (used by capture +
eval ingest) is now ONE implementation; the hosted commit path's §6b inline
consumer (``hosted_api._execute_commit_writes``, ~:6140-6185) is the OTHER.
§6b's migration onto the helper is deferred to phase 2 — so THIS PR ships a
differential parity test that pins the interim two-implementation state safe:
the same supersession payload driven through BOTH implementations must leave
IDENTICAL graph end-state (CORRECTS edge, point statuses, Object
.status/.supersededBy).

KNOWN, DELIBERATE ASYMMETRIES — OUT OF PARITY SCOPE (documented, do NOT
assert on them):
- warning channels: helper routes skips/failures to the caller's warn()
  (capture: meta warnings); §6b routes them to the module _logger.
- journal shapes: the helper emits id-style kwargs (full provenance incl.
  session_id on the JSONL line AND the synthesized GraphEvent payload); §6b
  emits positional payload + id kwarg (the phase-2 C2 journaling gap). The
  fold SET is the same either way, so parity asserts GRAPH END-STATE only —
  never journal contents.

Test env: docker lane (TORTOISE_DB_URI set — see AGENTS.md). Each arm's SDK
constructs with its own db_path; under the test-session redirect
(tortoise/projection/__init__.py, epic #1647) the two paths land on two
distinct derived server graphs — the two arms are truly isolated.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

from tortoise import hosted_api
from tortoise.commit_ops import apply_supersessions
from tortoise.commit_schema import (
    BudgetDecision,
    CommitPayload,
    CommitPlan,
    Entity,
    EntityReconcile,
    ExtractorInfo,
    Point,
    PointReconcile,
    ReconcileResult,
    SupersessionRecord,
    Telemetry,
    TelemetryCounts,
    TelemetryExtractor,
    TelemetryModel,
    point_content_id,
)
from tortoise.sdk import TortoiseSDK

# URI-less skip guard — mirrors test_acl_graph_users.py's module skipif
# (docker-lane tests must SKIP, not fail, on the URI-less tier-2 api-surface
# leg: this file is registered in the api surface, which the tier-2 selector
# runs embedded/carve-out when a PR touches hosted_api.py — see AGENTS.md).
# Both arms below assert the derived test_* SERVER-graph redirect, which only
# exists under a server URI (docker lane); a URI-less embedded run lands on
# the default graph and fails the redirect assertion deterministically.
_SUPPORTED = {"docker", "redis", "rediss"}


# Skip reason uses the "requires TORTOISE_DB_URI" exempt family (the
# skip-guard tool's intentional URI-gate prefix — tools/skip-guard.py) so a
# URI-less skip can never trip the CI live-FalkorDB skip guard.
def _server_uri_set() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    scheme = uri.split("://", 1)[0]
    return scheme in _SUPPORTED and bool(urlparse(uri).hostname)


pytestmark = pytest.mark.skipif(
    not _server_uri_set(),
    reason=(
        "requires TORTOISE_DB_URI (docker test-server lane) — parity arms "
        "assert the derived test_* server-graph redirect; URI-less "
        "embedded runs skip (mirrors test_acl_graph_users.py)"
    ),
)


# ── Fixture scenario (mirrors the T3/T4/T7 capture fixtures) ───────────────
# One OLD Point (a prior session's live statement) superseded by a NEW
# content-addressed point; one OLD Object superseded by a successor entity.
OLD_PT_CONTENT = "the gym moved from 6pm to 5pm"
NEW_PT_CONTENT = "the gym session is now at 5pm"
OLD_OBJECT_NAME = "approach-A"
SUCCESSOR_OBJECT_NAME = "approach-B"
OBJECT_KIND = "core:strategy"
PT_EVIDENCE = "fact-value contradiction (later session value change)"
ENTITY_EVIDENCE = "entity lifecycle supersedes"
SESSION_ID = "session_parity_9a"
CAPTURED_AT = "2026-08-11T10:00:00Z"

# The end-state BOTH consumers must converge on (the parity contract).
_EXPECTED_END_STATE = {
    "old_pt_status": "superseded",
    "old_pt_outdated": True,
    "corrects_count": 1,
    "old_object_status": "superseded",
    "old_object_superseded_by": SUCCESSOR_OBJECT_NAME,
    "successor_object_status": "live",
}


def _pt_id(content: str) -> str:
    """Commit-canonical content-addressed point id (commit_schema
    point_content_id — pt_<sha>; E5 #1537 emits supersession refs in this
    format)."""
    return point_content_id(content)


def _seed_baseline(sdk, old_pt_id: str) -> None:
    """The superseded side both consumers receive: an old live statement
    point and an old live Object, both written by an EARLIER session (the
    commit/capture under test only carries the supersession records)."""
    sdk.create_point("statement", OLD_PT_CONTENT, id=old_pt_id, status="live")
    sdk.create_entity("object", OLD_OBJECT_NAME, objectKind=OBJECT_KIND)


def _write_successors(sdk, new_pt_id: str) -> None:
    """The new-session content both consumers carry: the successor point +
    successor entity. Written BEFORE the supersessions apply (the capture/
    commit ordering — a dangling successor would be invisible to recall)."""
    sdk.create_point("statement", NEW_PT_CONTENT, id=new_pt_id, status="live")
    sdk.create_entity("object", SUCCESSOR_OBJECT_NAME, objectKind=OBJECT_KIND)


def _supersession_records(old_pt_id: str, new_pt_id: str) -> list[dict]:
    """The SAME extractor-format supersession records (the extractor_v2
    ``{superseded, supersedes_by, evidence}`` shape) fed to BOTH consumers:
    one pt_ point record, one entity-name record."""
    return [
        {"superseded": old_pt_id, "supersedes_by": new_pt_id, "evidence": PT_EVIDENCE},
        {
            "superseded": OLD_OBJECT_NAME,
            "supersedes_by": SUCCESSOR_OBJECT_NAME,
            "evidence": ENTITY_EVIDENCE,
        },
    ]


def _commit_payload_and_plan(old_pt_id: str, new_pt_id: str):
    """A minimal but valid CommitPayload + CommitPlan driving the hosted
    write phase. §5 reconciles the successor POINT (new), §6 the successor
    ENTITY (new) — the natural pre-§6b writes — and payload.supersessions
    carries the same records Arm A fed the helper. Everything else is empty
    (the §6b consumer does not read reconcile.supersessions; it reads
    payload.supersessions directly, hosted_api.py:6140)."""
    pt = Point(
        id=new_pt_id,
        content=NEW_PT_CONTENT,
        pointKind="statement",
        reason="REVISES",
        confidence=0.9,
        c_cal=0.8,
        about_entities=[],
        source_ref="session.md",
        quote="",
        status="live",
    )
    ent = Entity(name=SUCCESSOR_OBJECT_NAME, kind=OBJECT_KIND, passes_frequency_gate=True)
    payload = CommitPayload(
        schema_version="1",
        session_id=SESSION_ID,
        client_commit_id="parity-9a-ccid",  # validated only at the endpoint
        captured_at=CAPTURED_AT,
        extractor=ExtractorInfo(version="value@1.0.0", mode="byok", calibration_version="v3"),
        summary="parity fixture",
        story_arc="",
        provenance_refs=[],
        sources=[],
        events=[],
        entities=[ent],
        points=[pt],
        operators=[],
        supersessions=[
            SupersessionRecord(**r) for r in _supersession_records(old_pt_id, new_pt_id)
        ],
        telemetry=Telemetry(
            extractor=TelemetryExtractor(
                version="value@1.0.0", mode="byok", calibration_version="v3"
            ),
            model=TelemetryModel(provider="anthropic", id="claude-3-7", cfg_hash="h1"),
            counts=TelemetryCounts(kept=1, candidate=1, segment=1, window=1, empty_windows=0),
            keep_ratio=1.0,
            dedup_hits=0,
        ),
    )
    reconcile = ReconcileResult(
        points=[PointReconcile(point=pt, action="new")],
        entities=[EntityReconcile(entity=ent, action="new")],
    )
    plan = CommitPlan(
        payload=payload,
        duplicate=False,
        first_adjudication=True,
        reconcile=reconcile,
        budget=BudgetDecision(outcome="ok", cumulative_after=2),
    )
    return payload, plan


def _supersession_end_state(sdk, old_pt_id: str, new_pt_id: str) -> dict:
    """Read the supersession-relevant GRAPH END-STATE (never the journals —
    journal shapes deliberately diverge, phase-2 C2 gap)."""
    g = sdk._get_proj().g
    old_pt = g.query(
        "MATCH (p:Point {id:$id}) RETURN p.status, p.outdated",
        params={"id": old_pt_id},
    ).result_set
    corrects = g.query(
        "MATCH (n:Point {id:$n})-[:CORRECTS]->(o:Point {id:$o}) RETURN count(o)",
        params={"n": new_pt_id, "o": old_pt_id},
    ).result_set
    old_obj = g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
        params={"n": OLD_OBJECT_NAME},
    ).result_set
    succ_obj = g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status",
        params={"n": SUCCESSOR_OBJECT_NAME},
    ).result_set
    assert old_pt and old_obj and succ_obj, "fixture nodes missing on graph"
    return {
        "old_pt_status": old_pt[0][0],
        "old_pt_outdated": bool(old_pt[0][1]),
        "corrects_count": int(corrects[0][0]),
        "old_object_status": old_obj[0][0],
        "old_object_superseded_by": old_obj[0][1],
        "successor_object_status": succ_obj[0][0],
    }


def test_parity_helper_vs_hosted_section6b_identical_end_state(tmp_path):
    """THE differential parity test (#2164 Task 9a): the SAME supersession
    records through the shared helper (Arm A — the capture/eval ingest
    consumer) and through the hosted §6b inline consumer (Arm B —
    ``_execute_commit_writes``) must leave IDENTICAL end-state on two
    isolated graphs. The pre-#2164 drift class (a consumer that drops or
    diverges on the fold) fails the equality below."""
    old_pt_id = _pt_id(OLD_PT_CONTENT)
    new_pt_id = _pt_id(NEW_PT_CONTENT)

    # ── Arm A — the shared helper (capture path: write the session's new
    #    successors, then apply_supersessions with payload-format records;
    #    every skip/failure must surface through warn — never silent) ──
    sdk_a = TortoiseSDK(str(tmp_path / "arm-a.db"))
    _seed_baseline(sdk_a, old_pt_id)
    _write_successors(sdk_a, new_pt_id)
    warns: list[str] = []
    applied = apply_supersessions(
        sdk_a._get_proj(),
        sdk_a,
        _supersession_records(old_pt_id, new_pt_id),
        session_id=SESSION_ID,
        warn=warns.append,
    )
    assert applied == 2, f"helper must apply both records: {warns}"
    assert warns == [], f"unambiguous fixtures must not warn: {warns}"

    # ── Arm B — the hosted §6b inline consumer, driven directly through the
    #    real write-phase function the endpoint executes (test_commit_endpoint
    #    treats it as the injectable seam; the endpoint wrapper adds only
    #    Layer-1/reconcile/budget/record-store, which §6b does not read).
    #    §5 writes the successor point, §6 the successor entity, then §6b
    #    consumes payload.supersessions (hosted_api.py:6140-6185). ──
    sdk_b = TortoiseSDK(str(tmp_path / "arm-b.db"))
    # Isolation precondition (non-vacuity): the two db_paths must land on
    # DISTINCT graphs — a shared graph would make the end-state comparison
    # graph-vs-itself (the #942-class vacuous pass this issue's parity guard
    # exists to prevent). The docker-lane redirect derives per-path graphs.
    assert sdk_a._get_proj()._graph_name != sdk_b._get_proj()._graph_name, (
        f"parity arms must run on DISTINCT graphs, got both on {sdk_a._get_proj()._graph_name!r}"
    )
    _seed_baseline(sdk_b, old_pt_id)
    payload, plan = _commit_payload_and_plan(old_pt_id, new_pt_id)
    hosted_api._execute_commit_writes(sdk_b, payload, plan)

    state_a = _supersession_end_state(sdk_a, old_pt_id, new_pt_id)
    state_b = _supersession_end_state(sdk_b, old_pt_id, new_pt_id)
    assert state_a == state_b, (
        "helper and hosted §6b must fold the SAME supersession payload to "
        "the SAME end-state (drift guard #2164):\n"
        f"  helper (arm A): {state_a}\n"
        f"  hosted (arm B): {state_b}"
    )
    assert state_a == _EXPECTED_END_STATE, state_a


def test_hosted_section6b_arm_alone_produces_the_fold(tmp_path):
    """Non-vacuity of the §6b harness side: the hosted consumer ALONE (no
    helper in the picture) must produce the fold — old pt terminal with a
    single CORRECTS edge from the successor, old Object superseded by the
    successor name. If §6b's records were dropped or mis-resolved, this
    test (not just the pairwise equality) fails — the parity test's Arm B is
    proven independently live."""
    old_pt_id = _pt_id(OLD_PT_CONTENT)
    new_pt_id = _pt_id(NEW_PT_CONTENT)
    sdk = TortoiseSDK(str(tmp_path / "arm-b-alone.db"))
    assert sdk._get_proj()._graph_name.startswith("test_"), (
        "docker-lane redirect must isolate this arm on a test_* server graph"
    )
    _seed_baseline(sdk, old_pt_id)
    payload, plan = _commit_payload_and_plan(old_pt_id, new_pt_id)
    hosted_api._execute_commit_writes(sdk, payload, plan)
    state = _supersession_end_state(sdk, old_pt_id, new_pt_id)
    assert state == _EXPECTED_END_STATE, state
