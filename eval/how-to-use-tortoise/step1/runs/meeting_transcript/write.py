"""Record the 2026-09-14 pricing/self-serve decision meeting into the Tortoise graph."""

from __future__ import annotations

import json

from tortoise.sdk import TortoiseSDK


def show(label: str, obj) -> None:
    print(f"--- {label}")
    print(json.dumps(obj, indent=1, default=str)[:900])


sdk = TortoiseSDK()

# ---------------------------------------------------------------- 1. Source
# The transcript itself is the primary record of what was said in the room.
SRC_URL = "transcript://meetings/2026-09-14-pricing"
source = sdk.create_source(
    SRC_URL,
    "meetingTranscript",
    tier="T1",
    title="Pricing / self-serve decision meeting — 2026-09-14 (Dana CEO, Ravi PM, Priya Eng)",
    sourceDate="2026-09-14",
)
show("source", source)
SRC = source["id"]

# ---------------------------------------------------------------- 2. Event
event = sdk.create_event(
    "Pricing & self-serve decision meeting",
    "meeting",
    startedAt="2026-09-14T10:00:00Z",
    participants=["Dana", "Ravi", "Priya"],
    classificationLevel="internal",
)
show("event", event)
EV = event["id"]

# ---------------------------------------------------------------- 3. People
dana = sdk.create_subject("Dana", subjectKind="person", role="CEO", org="the company")
ravi = sdk.create_subject("Ravi", subjectKind="person", role="PM", org="the company")
priya = sdk.create_subject("Priya", subjectKind="person", role="Eng", org="the company")
show("subjects", [dana, ravi, priya])
DANA, RAVI, PRIYA = dana["id"], ravi["id"], priya["id"]

# ---------------------------------------------------------------- 4. Objects
tier = sdk.create_object(
    "self-serve tier", objectKind="offering", status="being discontinued", effectiveWindow="Q4 2026"
)
comms = sdk.create_object(
    "self-serve migration communications", objectKind="deliverable", assignee="Ravi"
)
pricing = sdk.create_object("pricing page", objectKind="productSurface")
residency = sdk.create_object(
    "EU data residency", objectKind="openQuestion", state="deferred"
)
show("objects", [tier, comms, pricing, residency])
TIER, COMMS, PRICING, RESIDENCY = (tier["id"], comms["id"], pricing["id"], residency["id"])

# ---------------------------------------------------------------- 5. Points
p_decision = sdk.create_point(
    "decision",
    "The self-serve tier is being killed. Decision made 2026-09-14 by Dana (CEO), effective Q4 2026.",
    extractedFrom=SRC,
    credibility="high",
    decidedBy="Dana",
    decidedOn="2026-09-14",
    effectiveWindow="Q4 2026",
    decisionStatus="decided",
)
show("p_decision", p_decision)

p_comms = sdk.create_point(
    "plan",
    "Ravi owns migration communications for the self-serve shutdown; first draft due 2026-09-25.",
    extractedFrom=SRC,
    credibility="high",
    assignee="Ravi",
    dueDate="2026-09-25",
    workstream="migration comms",
    state="assigned",
)
show("p_comms", p_comms)

p_pricing = sdk.create_point(
    "plan",
    "The pricing page rewrite must land before the migration comms first draft; Priya needs it first.",
    extractedFrom=SRC,
    credibility="high",
    precondition="true",
    assignee="unassigned",
    state="not started",
)
show("p_pricing", p_pricing)

p_estimate = sdk.create_point(
    "estimate",
    "Priya estimates an 80% probability that the pricing page rewrite slips by one week.",
    extractedFrom=SRC,
    credibility="medium",
    estimator="Priya",
    probability=0.8,
    slipWeeks=1,
    statedAs="80% chance we slip a week",
    estimatedOn="2026-09-14",
)
show("p_estimate", p_estimate)

p_residency = sdk.create_point(
    "issue",
    "Open question: EU data residency requirements. Raised by Dana 2026-09-14, explicitly deferred "
    "('at some point') — no owner, no target date, no decision.",
    extractedFrom=SRC,
    credibility="high",
    raisedBy="Dana",
    assignee="unassigned",
    dueDate="none",
    state="open",
    decisionMade="false",
)
show("p_residency", p_residency)

DEC, COM, PRI, EST, RES = (
    p_decision["id"],
    p_comms["id"],
    p_pricing["id"],
    p_estimate["id"],
    p_residency["id"],
)

# ---------------------------------------------------------------- 6. Structural edges
edges = []


def edge(relation: str, frm: str, to: str) -> None:
    r = sdk.create_edge(relation, frm, to)
    edges.append({"relation": relation, "from": frm, "to": to, "created": r.get("created")})
    print("edge", relation, frm, "->", to, r.get("created"))


# who was in the room
for who in (DANA, RAVI, PRIYA):
    edge("participatesIn", who, EV)

# every remembered claim is anchored to the meeting it came from
for p in (DEC, COM, PRI, EST, RES):
    edge("aboutEvent", p, EV)

# who the claims are about / who spoke
edge("aboutSubject", DEC, DANA)      # Dana decided
edge("aboutSubject", COM, RAVI)      # Ravi owns
edge("aboutSubject", EST, PRIYA)     # Priya estimated
edge("aboutSubject", RES, DANA)      # Dana raised it

# what each claim is about
edge("aboutObject", DEC, TIER)
edge("aboutObject", COM, COMMS)
edge("aboutObject", PRI, PRICING)
edge("aboutObject", EST, PRICING)
edge("aboutObject", RES, RESIDENCY)

# the assignment itself
edge("ownedBy", COM, RAVI)

# scheduling: the comms draft depends on the pricing page rewrite
edge("dependsOn", COM, PRI)

# ---------------------------------------------------------------- 7. Epistemic operators
# The decision requires the downstream work.
op_impl = sdk.create_operator(
    "IMPL",
    DEC,
    [COM, PRI],
    label="causes",
    direction="->",
    semantics="causes",
)
show("operator IMPL", op_impl)

# The 80%-slip estimate attacks the certainty of the 2026-09-25 due date.
op_nand = sdk.create_operator(
    "NAND",
    EST,
    [COM],
    label="blocks",
    direction="->",
    semantics="refutes",
)
show("operator NAND", op_nand)

print("\n=== counts ===")
print(json.dumps(sdk.taxonomy(), indent=1))
print("\n=== edges ===")
print(json.dumps(edges, indent=1))
