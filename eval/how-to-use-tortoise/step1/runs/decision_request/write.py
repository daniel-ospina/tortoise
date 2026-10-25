from __future__ import annotations

import json

from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

REQ_DATE = "2026-09-20"
APPROVAL_DEADLINE = "2026-10-15"
FREEZE_DATE = "2027-01-05"
TARGET_DATE = "2027-03-31"
SESSION = "session:ax-eval/run-decision_request"

out: dict = {"nodes": {}, "edges": [], "errors": []}


def rec(key: str, res: dict) -> str:
    out["nodes"][key] = res
    return res["id"]


def edge(relation: str, frm: str, to: str) -> None:
    r = sdk.create_edge(relation, from_id=frm, to_id=to)
    out["edges"].append({"relation": relation, "from": frm, "to": to, "ok": r.get("created")})


# ---- provenance -------------------------------------------------------------
source = sdk.create_source(
    "tortoise://inbox/platform-team/2026-09-20-celery-to-temporal",
    "conversation",
    title="Decision request — platform team, 2026-09-20",
    sourceDate=REQ_DATE,
)
SRC = rec("source", source)

# ---- actors / systems -------------------------------------------------------
S_TEAM = rec("team", sdk.create_subject("Platform team", "team", extractedFrom=SESSION))

O_PIPELINE = rec("pipeline", sdk.create_object("Ingestion pipeline", "workflow", extractedFrom=SESSION))
O_WORKERS = rec("workers", sdk.create_object("Ingestion workers", "other",
                                             description="workers that perform ingestion", extractedFrom=SESSION))
O_CELERY = rec("celery", sdk.create_object("Celery", "tool", extractedFrom=SESSION))
O_TEMPORAL = rec("temporal", sdk.create_object("Temporal", "tool", extractedFrom=SESSION))

# ---- decision + parts -------------------------------------------------------
DECISION = rec("decision", sdk.create_point(
    "decision",
    ("Approve moving the ingestion workers from Celery to Temporal "
     "(requested 2026-09-20 by the platform team; approval required by 2026-10-15)."),
    status="draft",
    decisionStatus="pending_approval",
    requestedBy="Platform team",
    requestedAt=REQ_DATE,
    approvalDeadline=APPROVAL_DEADLINE,
    scope="ingestion workers only",
    extractedFrom=SESSION,
))

OPTION = rec("option", sdk.create_point(
    "option",
    "Migrate the platform team's ingestion workers from Celery to Temporal.",
    status="draft",
    decisionId=DECISION,
    fromTool="Celery",
    toTool="Temporal",
    extractedFrom=SESSION,
))

CRIT_FREEZE = rec("criterion_freeze", sdk.create_point(
    "criterion",
    "Hard constraint: the Celery->Temporal migration must complete before the Q1 freeze on 2027-01-05.",
    status="draft",
    decisionId=DECISION,
    criterionType="hard constraint (deadline)",
    deadline=FREEZE_DATE,
    extractedFrom=SESSION,
))

CRIT_APPROVAL = rec("criterion_approval", sdk.create_point(
    "criterion",
    "Hard constraint: if the migration is not approved by 2026-10-15 the migration window is missed.",
    status="draft",
    decisionId=DECISION,
    criterionType="hard constraint (timing window)",
    deadline=APPROVAL_DEADLINE,
    consequenceIfMissed="migration window missed",
    extractedFrom=SESSION,
))

COST = rec("cost_estimate", sdk.create_point(
    "evidence",
    "The Celery->Temporal migration is estimated to cost approximately 3 engineer-weeks.",
    status="draft",
    decisionId=DECISION,
    estimate="~3 engineer-weeks",
    estimateWeeks=3,
    unit="engineer-weeks",
    extractedFrom=SESSION,
))

GOAL = rec("latency_target", sdk.create_point(
    "target",
    "Platform team goal: sub-5-minute end-to-end ingestion latency by 2027-03-31.",
    targetDate=TARGET_DATE,
    metric="end-to-end ingestion latency",
    threshold="< 5 minutes",
    comparator="sub",
    owner="Platform team",
    extractedFrom=SESSION,
))

# ---- events (about-wiring goes through create_event's kwargs) ---------------
E_REQUEST = rec("request_event", sdk.create_event(
    "Decision request raised: approve Celery->Temporal migration for ingestion workers",
    "occurrence",
    startedAt=REQ_DATE,
    occurredAt=REQ_DATE,
    requestedBy="Platform team",
    summary=("Platform team requested approval to move ingestion workers from Celery to Temporal; "
             "approval needed by 2026-10-15 or the window is missed."),
    aboutSubject=S_TEAM,
    aboutPoint=DECISION,
    aboutObject=O_PIPELINE,
    aboutDocument=SRC,
    extractedFrom=SESSION,
))

E_FREEZE = rec("freeze_event", sdk.create_event(
    "Q1 freeze begins",
    "milestone",
    startedAt=FREEZE_DATE,
    occurredAt=FREEZE_DATE,
    milestoneType="organizational freeze",
    extractedFrom=SESSION,
))

# ---- structural edges -------------------------------------------------------
edge("participatesIn", S_TEAM, E_REQUEST)
edge("references", S_TEAM, GOAL)          # the goal belongs to the requesting team

edge("references", DECISION, OPTION)      # the decision weighs this option
edge("references", DECISION, CRIT_FREEZE)
edge("references", DECISION, CRIT_APPROVAL)
edge("references", COST, OPTION)          # the cost estimate is evidence about the option

edge("uses", OPTION, O_TEMPORAL)          # proposed means relies on Temporal
edge("uses", O_WORKERS, O_CELERY)         # current mechanism is Celery
edge("hasPart", O_PIPELINE, O_WORKERS)    # workers are part of the pipeline
edge("aboutObject", GOAL, O_PIPELINE)     # the latency target is about the pipeline

edge("references", CRIT_FREEZE, E_FREEZE) # the constraint is anchored to the freeze milestone
edge("references", GOAL, E_REQUEST)       # the latency goal was stated in this request

# ---- epistemic layer: the option is the proposed means to the latency target -
op = sdk.create_operator(
    "IMPL",
    source_id=OPTION,
    target_ids=[GOAL],
    label="addresses",
    semantics="addresses",
)
out["nodes"]["operator_option_addresses_target"] = op

print(json.dumps({"ids": {k: v.get("id") for k, v in out["nodes"].items()},
                  "edges": out["edges"]}, indent=2))
