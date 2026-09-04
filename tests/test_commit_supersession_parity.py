"""#2193 — the hosted commit write phase's supersession lane, post-migration.

WHAT THIS FILE PINS: #2193 migrated the hosted §6b inline consumer
(``hosted_api._execute_commit_writes``) onto the SHARED
``tortoise.commit_ops.apply_supersessions`` helper — ONE supersession
consumer-side discipline now serves capture (_extract_session_v2), eval
ingest_v2, and the hosted commit endpoint. The #2164 Task 9a differential
parity test is therefore DELETED (its two arms were the helper after the
migration, so the pairwise equality went vacuous); this file holds the
hosted-path coverage that replaces it:

- ``test_hosted_commit_writes_supersession_end_state`` — a supersession
  payload driven through the real hosted write phase leaves the expected
  end-state (CORRECTS edge, point statuses, Object .status/.supersededBy).
- ``test_hosted_commit_wires_apply_supersessions_once`` — the wiring spy:
  the write phase routes payload.supersessions through the helper EXACTLY
  once, with typed records, session_id and a warn callable that DELEGATES
  to the hosted module logger (hosted attribution — the call site wraps it
  in a warn-counting closure so the summary log reserves WARNING for
  records that actually warned). Pre-#2193 §6b's inline loop never called
  the helper — the spy is the regression guard that pins the seam.

Test env: docker lane (TORTOISE_DB_URI set — see AGENTS.md). The SDKs
construct with their own db_path; under the test-session redirect
(tortoise/projection/__init__.py, epic #1647) each path lands on a distinct
derived test_* server graph.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

from tortoise import hosted_api
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

# The end-state the hosted write phase must converge on (the smoke
# contract).
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
    """The superseded side the hosted write phase receives: an old live
    statement point and an old live Object, both written by an EARLIER
    session (the commit under test only carries the supersession records)."""
    sdk.create_point("statement", OLD_PT_CONTENT, id=old_pt_id, status="live")
    sdk.create_entity("object", OLD_OBJECT_NAME, objectKind=OBJECT_KIND)


def _write_successors(sdk, new_pt_id: str) -> None:
    """The new-session content the hosted write phase carries: the successor
    point + successor entity. Written BEFORE the supersessions apply (the
    commit ordering — a dangling successor would be invisible to recall)."""
    sdk.create_point("statement", NEW_PT_CONTENT, id=new_pt_id, status="live")
    sdk.create_entity("object", SUCCESSOR_OBJECT_NAME, objectKind=OBJECT_KIND)


def _supersession_records(old_pt_id: str, new_pt_id: str) -> list[dict]:
    """The extractor-format supersession records (the extractor_v2
    ``{superseded, supersedes_by, evidence}`` shape) fed to the hosted write
    phase: one pt_ point record, one entity-name record."""
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
    carries the supersession records (the hosted §6b helper call reads
    payload.supersessions directly, not reconcile.supersessions). Everything
    else is empty."""
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
    """Read the supersession-relevant GRAPH END-STATE after the hosted write
    phase (the smoke contract — never the journals)."""
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


def test_hosted_commit_writes_supersession_end_state(tmp_path):
    """Hosted-path end-state smoke (#2193): a supersession payload driven
    through the real hosted write phase (_execute_commit_writes — the
    injectable seam the commit endpoint executes; the endpoint wrapper adds
    only Layer-1/reconcile/budget/record-store, which the supersession lane
    does not read) must produce the fold: old pt terminal with a single
    CORRECTS edge from the successor, old Object superseded by the successor
    name. If the shared apply_supersessions call dropped or mis-resolved the
    records, this smoke fails."""
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


def test_hosted_commit_wires_apply_supersessions_once(monkeypatch, tmp_path):
    """#2193 (Tasks 3/4) wiring spy — the hosted commit write phase must
    route payload.supersessions through the SHARED apply_supersessions
    helper: EXACTLY ONE call, typed SupersessionRecords, session_id and warn
    = the hosted module logger (hosted attribution). Pre-#2193 §6b's inline
    loop never calls the helper — this spy fails (zero calls) against the
    old shape and pins the seam after the migration."""
    import tortoise.commit_ops as commit_ops
    calls: list[tuple] = []

    def spy(proj, sdk_, records, **kwargs):
        # mimic a full apply — the Task-4 summary log formats `applied`
        # (a None-returning spy would TypeError inside logging's % fmt)
        calls.append((proj, sdk_, list(records), dict(kwargs)))
        return len(list(records))

    monkeypatch.setattr(commit_ops, "apply_supersessions", spy)
    # hosted-attribution probe: the call site wraps the module logger in a
    # warn-counting closure (hosted_api._supersession_warn) so the summary
    # log can reserve WARNING for records that actually warned. The spy must
    # still see that closure DELEGATE to the hosted module logger — swap the
    # logger's warning for a recorder before driving the commit.
    recorded_warns: list[tuple] = []

    def _recorder(msg, *args, **kwargs):
        recorded_warns.append((msg, args, kwargs))

    monkeypatch.setattr(hosted_api._logger, "warning", _recorder)
    old_pt_id = _pt_id(OLD_PT_CONTENT)
    new_pt_id = _pt_id(NEW_PT_CONTENT)
    sdk = TortoiseSDK(str(tmp_path / "wiring.db"))
    assert sdk._get_proj()._graph_name.startswith("test_"), (
        "docker-lane redirect must isolate the wiring arm on a test_* graph"
    )
    _seed_baseline(sdk, old_pt_id)
    payload, plan = _commit_payload_and_plan(old_pt_id, new_pt_id)
    hosted_api._execute_commit_writes(sdk, payload, plan)
    assert len(calls) == 1, \
        f"apply_supersessions must be called EXACTLY once, got {len(calls)}"
    proj, sdk_, records, kwargs = calls[0]
    assert proj is sdk._get_proj(), "the shared proj must be passed"
    assert sdk_ is sdk, "the committing SDK must be passed"
    assert records == list(payload.supersessions), "typed records passthrough"
    assert kwargs["session_id"] == SESSION_ID, kwargs
    # `==` NOT `is` — Logger.warning is a bound method, a fresh object per
    # access; `is` can never pass. The call site passes a warn-counting
    # closure that DELEGATES to the hosted module logger — probe it routes.
    assert kwargs["warn"] is not hosted_api._logger.warning, (
        "call site must pass the warn-counting wrapper, not the bare logger"
    )
    kwargs["warn"]("__probe__")
    assert recorded_warns and recorded_warns[-1][0] == "__probe__", (
        "the passed warn callable must delegate to hosted_api._logger.warning"
    )
