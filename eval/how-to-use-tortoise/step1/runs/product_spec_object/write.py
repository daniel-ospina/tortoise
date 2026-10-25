"""Record the "Beta memory API plan spec v2" revision into the Tortoise graph.

Model chosen (decided from the tool surface alone, no docs):
  * the plan is an Object; the spec document is a Source (provenance);
  * each dated / measured commitment is a `target` Point, so it carries its
    own belief and can be versioned independently;
  * the platform team is a Subject wired to the plan with `ownedBy`;
  * v1's stated values are recorded too and then SUPERSEDED by v2's via
    supersede_point (CORRECTS edge + bi-temporal window + edge transfer) —
    the correction is expressed, never a silent overwrite;
  * the revision itself is an Event (episodic), so the graph remembers that
    the plan changed, when, from what source, and from what values.
"""
from __future__ import annotations

import json

from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

V1_OBJ = "obj-3607bd9d876997f7ea1f29d195"   # pre-existing "Beta memory API plan v1"
SPEC_DATE = "2026-09-30"
SHIP_V1, SHIP_V2 = "2026-10-15", "2026-11-15"
METRIC_V1, METRIC_V2 = "25%", "40%"
SPEC_URL = "internal://premise-labs/beta-memory-api/plan-spec-v2-2026-09-30"

warnings: list[str] = []


def rid(result):
    """Id out of whatever shape a create call returns (flat node or {node})."""
    if not isinstance(result, dict):
        return None
    return result.get("id") or (result.get("node") or {}).get("id")


def step(name, fn, *args, **kwargs):
    try:
        r = fn(*args, **kwargs)
    except Exception as exc:  # report, keep going: one bad prop must not void the write
        warnings.append(f"{name}: {type(exc).__name__}: {exc}")
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return None
    print(f"[ok]   {name}: {rid(r)}")
    return r


# --------------------------------------------------------------- provenance
src = step(
    "source: plan spec v2 (dated 2026-09-30)",
    sdk.create_source,
    SPEC_URL,
    "spec",
    tier="high",
    title="Beta memory API plan - spec v2 (2026-09-30)",
    sourceDate=SPEC_DATE,
)
SRC_ID = rid(src) or SPEC_URL

# --------------------------------------------------------------- ownership
team = step("subject: Platform team", sdk.create_subject, "Platform team", subjectKind="team")
TEAM_ID = rid(team)

# --------------------------------------------------------------- plan v2 (Object)
plan = step(
    "object: Beta memory API plan v2",
    sdk.create_object,
    "Beta memory API plan v2",
    objectKind="plan",
    status="live",
    goalState="active",
    version="v2",
    planVersion="v2",
    specDate=SPEC_DATE,
    ownerTeam="Platform team",
    replacesPlanId=V1_OBJ,
    sourceUrl=SPEC_URL,
)
PLAN_ID = rid(plan)

# ------------------------------------- the two v2 commitments, as target Points
ship2 = step(
    "point: ship target 2026-11-15 (v2)",
    sdk.create_point,
    "target",
    "The beta memory API ships on 2026-11-15 (plan v2 ship target).",
    dedup=True,
    key="beta-memory-api-ship-date",
    planId=PLAN_ID,
    planVersion="v2",
    targetKind="shipDate",
    targetDate=SHIP_V2,
    specDate=SPEC_DATE,
    extractedFrom=SRC_ID,
)
SHIP2_ID = rid(ship2)

metric2 = step(
    "point: success metric 40% of beta orgs writing weekly / rolling 4 weeks (v2)",
    sdk.create_point,
    "target",
    (
        "Plan v2 success = 40% of beta orgs write at least one memory per week, "
        "measured over a rolling 4-week window."
    ),
    dedup=True,
    key="beta-memory-api-success-metric",
    planId=PLAN_ID,
    planVersion="v2",
    targetKind="successMetric",
    threshold=METRIC_V2,
    thresholdValue=0.40,
    cohort="beta organizations",
    action="write at least one memory",
    cadence="per week",
    window="rolling 4-week",
    specDate=SPEC_DATE,
    extractedFrom=SRC_ID,
)
METRIC2_ID = rid(metric2)

# ------------------- what v1 said, kept as history and then explicitly superseded
ship1 = step(
    "point: ship target 2026-10-15 (v1, to be superseded)",
    sdk.create_point,
    "target",
    "The beta memory API ships on 2026-10-15 (plan v1 ship target, superseded by v2).",
    dedup=True,
    key="beta-memory-api-ship-date-v1",
    planId=V1_OBJ,
    planVersion="v1",
    targetKind="shipDate",
    targetDate=SHIP_V1,
    extractedFrom=SRC_ID,
)
SHIP1_ID = rid(ship1)

metric1 = step(
    "point: success metric 25% of beta orgs writing weekly / rolling 4 weeks (v1, to be superseded)",
    sdk.create_point,
    "target",
    (
        "Plan v1 success = 25% of beta orgs write at least one memory per week, "
        "measured over a rolling 4-week window (superseded by v2)."
    ),
    dedup=True,
    key="beta-memory-api-success-metric-v1",
    planId=V1_OBJ,
    planVersion="v1",
    targetKind="successMetric",
    threshold=METRIC_V1,
    thresholdValue=0.25,
    cohort="beta organizations",
    action="write at least one memory",
    cadence="per week",
    window="rolling 4-week",
    extractedFrom=SRC_ID,
)
METRIC1_ID = rid(metric1)

# --------------------------------------------------------------- structural wiring
if PLAN_ID and SHIP2_ID:
    step("edge: plan v2 hasPart ship target", sdk.create_edge, "hasPart", PLAN_ID, SHIP2_ID)
if PLAN_ID and METRIC2_ID:
    step("edge: plan v2 hasPart success metric", sdk.create_edge, "hasPart", PLAN_ID, METRIC2_ID)
if PLAN_ID and TEAM_ID:
    step("edge: plan v2 ownedBy Platform team", sdk.create_edge, "ownedBy", PLAN_ID, TEAM_ID)
if PLAN_ID:
    step("edge: plan v2 wasDerivedFrom plan v1", sdk.create_edge, "wasDerivedFrom", PLAN_ID, V1_OBJ)
if PLAN_ID:
    step("source -> plan v2 (references)", sdk.link_source_to_entity, SPEC_URL, PLAN_ID, "Object", "spec")

# v1 is replaced entirely: it is no longer a live, actively pursued plan.
step(
    "update: plan v1 -> status superseded / goalState abandoned",
    sdk.update,
    V1_OBJ,
    status="superseded",
    goalState="abandoned",
    replacedByPlanId=PLAN_ID,
)

# --------------------------------------------------------------- the corrections
if SHIP1_ID and SHIP2_ID:
    step("supersede: ship 2026-10-15 -> 2026-11-15", sdk.supersede_point, SHIP1_ID, SHIP2_ID)
if METRIC1_ID and METRIC2_ID:
    step("supersede: 25% -> 40% weekly writers", sdk.supersede_point, METRIC1_ID, METRIC2_ID)

# --------------------------------------------------------------- revision Event
ev = step(
    "event: plan revised to v2 on 2026-09-30",
    sdk.create_event,
    (
        "Beta memory API plan revised to v2 (spec dated 2026-09-30): ship date moves "
        "2026-10-15 -> 2026-11-15 and the success bar moves 25% -> 40% of beta orgs "
        "writing at least one memory per week over a rolling 4-week window. v2 replaces "
        "v1 entirely; the plan is owned by the platform team."
    ),
    "planRevision",
    is_episodic=True,
    eventDate=SPEC_DATE,
    specVersion="v2",
    supersededShipDate=SHIP_V1,
    supersededSuccessThreshold=METRIC_V1,
    aboutObject=PLAN_ID,
    aboutSubject=TEAM_ID,
    aboutDocument=SRC_ID,
)
EV_ID = rid(ev)

if EV_ID:
    step(
        "source -> event (derivation/references)",
        sdk.link_source_to_entity,
        SPEC_URL,
        EV_ID,
        "Document",
        "spec",
    )

print("\n=== WRITE RESULT ===")
print(
    json.dumps(
        {
            "planV2": PLAN_ID,
            "team": TEAM_ID,
            "source": SRC_ID,
            "event": EV_ID,
            "shipV2": SHIP2_ID,
            "metricV2": METRIC2_ID,
            "shipV1": SHIP1_ID,
            "metricV1": METRIC1_ID,
            "warnings": warnings,
        },
        indent=1,
    )
)
