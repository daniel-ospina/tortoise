"""Record the 2026-08 quarterly portfolio update into the Tortoise graph.

Model used:
  Source  -> the memo itself (provenance for every claim)
  Subject -> the three named parties (two portfolio companies, one investor)
  Object  -> the two inert things the memo refers to (board seat, tranche)
  Event   -> the three dated things that happened
  Point   -> the standing facts/measurements and the forward decision
  Edges   -> who each node is about + the audit->tranche gate (direct IMPL)
"""
from __future__ import annotations

from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

MADE: dict[str, list] = {"Point": [], "Subject": [], "Object": [], "Event": [], "Source": []}
EDGES: list[tuple[str, str, str]] = []


def edge(relation: str, from_id: str, to_id: str, fallback: str = "related") -> None:
    try:
        r = sdk.create_edge(relation, from_id, to_id)
        EDGES.append((relation, from_id, to_id))
        print(f"edge {relation}: {from_id} -> {to_id} created={r.get('created')}")
    except Exception as exc:  # fall back to the neutral structural relation
        print(f"edge {relation} refused ({type(exc).__name__}: {str(exc)[:160]}) -> {fallback}")
        r = sdk.create_edge(fallback, from_id, to_id)
        EDGES.append((fallback, from_id, to_id))
        print(f"edge {fallback}: {from_id} -> {to_id} created={r.get('created')}")


# ---------------------------------------------------------------- source (memo)
src = sdk.create_source(
    "memo://our-fund/portfolio/2026-08-quarterly-update",
    "portfolioUpdateMemo",
    title="Quarterly portfolio update, 2026-08",
    sourceDate="2026-08",
    updatePeriod="2026-08",
    author="our fund (portfolio manager)",
    scope="portfolio-wide quarterly update",
)
SRC = src["id"]
MADE["Source"].append(SRC)
print("source:", SRC)

# ---------------------------------------------------------------------- subjects
northwind = sdk.create_subject(
    "Northwind Logistics", "company",
    role="portfolio company", sector="logistics",
)
solstice = sdk.create_subject(
    "Solstice Robotics", "company",
    role="portfolio company", sector="robotics",
)
acme = sdk.create_subject(
    "Acme Capital", "investor",
    role="co-investor / funding counterparty",
)
NW, SOL, ACME = northwind["id"], solstice["id"], acme["id"]
MADE["Subject"] += [NW, SOL, ACME]
print("subjects:", NW, SOL, ACME)

# ----------------------------------------------------------------------- objects
seat = sdk.create_object(
    "Northwind Logistics board seat", "governanceSeat",
    company="Northwind Logistics",
    heldBy="our fund",
    seatCount=1,
)
tranche2 = sdk.create_object(
    "Acme Capital tranche 2 to Solstice Robotics", "tranche",
    trancheNumber=2,
    investor="Acme Capital",
    recipient="Solstice Robotics",
)
SEAT, TRANCH2 = seat["id"], tranche2["id"]
MADE["Object"] += [SEAT, TRANCH2]
print("objects:", SEAT, TRANCH2)

# ------------------------------------------------------------------------ events
series_b = sdk.create_event(
    "Northwind Logistics closed a Series B round", "milestone",
    occurredOn="2026-07-30",
    round="Series B",
    company="Northwind Logistics",
    extractedFrom=SRC,
    aboutSubject=NW,
)
E_SERIESB = series_b["node"]["id"] if "node" in series_b else series_b["id"]
MADE["Event"].append(E_SERIESB)

tranche_release = sdk.create_event(
    "Acme Capital released tranche 2 to Solstice Robotics", "disbursement",
    occurredOn="2026-09-02",
    investor="Acme Capital",
    recipient="Solstice Robotics",
    precondition="safety audit passed",
    extractedFrom=SRC,
    aboutSubject=SOL,
    aboutObject=TRANCH2,
)
E_TRANCHE = tranche_release["node"]["id"] if "node" in tranche_release else tranche_release["id"]
MADE["Event"].append(E_TRANCHE)

audit = sdk.create_event(
    "Solstice Robotics safety audit passed", "conditionMet",
    company="Solstice Robotics",
    passedBefore="2026-09-02",
    result="passed",
    extractedFrom=SRC,
    aboutSubject=SOL,
)
E_AUDIT = audit["node"]["id"] if "node" in audit else audit["id"]
MADE["Event"].append(E_AUDIT)
print("events:", E_SERIESB, E_TRANCHE, E_AUDIT)

# ------------------------------------------------------------------------ points
p_arr = sdk.create_point(
    "observation",
    "Northwind Logistics ARR is $8.4M as of 2026-08, up from $5.1M at our entry.",
    extractedFrom=SRC, credibility="medium",
    metric="ARR", value="$8.4M", priorValue="$5.1M", asOf="2026-08",
)
P_ARR = p_arr["id"]
MADE["Point"].append(P_ARR)

p_seat = sdk.create_point(
    "observation",
    "The board seat our fund holds at Northwind Logistics is unchanged as of the 2026-08 update.",
    extractedFrom=SRC, credibility="medium",
    company="Northwind Logistics", subject="board seat", statusWord="unchanged",
)
P_SEAT = p_seat["id"]
MADE["Point"].append(P_SEAT)

p_head = sdk.create_point(
    "observation",
    "Solstice Robotics headcount is 74 people as of 2026-09-30, up from 61 in Q1 2026.",
    extractedFrom=SRC, credibility="medium",
    metric="headcount", value=74, priorValue=61, asOf="2026-09-30",
)
P_HEAD = p_head["id"]
MADE["Point"].append(P_HEAD)

p_nodew = sdk.create_point(
    "decision",
    "We are not adding new portfolio names this quarter (2026-08 update).",
    extractedFrom=SRC,
    decisionScope="portfolio", period="2026-Q3", decision="no new investments",
)
P_NODEC = p_nodew["id"]
MADE["Point"].append(P_NODEC)

p_gate = sdk.create_point(
    "observation",
    "Acme Capital's tranche 2 release to Solstice Robotics was gated on the safety audit passing; the audit passed and the tranche released 2026-09-02.",
    extractedFrom=SRC, credibility="medium",
    gatingCondition="safety audit", recipient="Solstice Robotics", investor="Acme Capital",
)
P_GATE = p_gate["id"]
MADE["Point"].append(P_GATE)
print("points:", P_ARR, P_SEAT, P_HEAD, P_NODEC, P_GATE)

# ------------------------------------------------------------------------- edges
# who each claim is about
edge("aboutSubject", P_ARR, NW)
edge("aboutSubject", P_SEAT, NW)
edge("aboutSubject", P_HEAD, SOL)
edge("aboutSubject", P_GATE, SOL)
edge("aboutSource", P_NODEC, SRC)

# the two objects and their parties
edge("related", SEAT, NW)
edge("related", P_SEAT, SEAT)
edge("related", TRANCH2, ACME)
edge("related", TRANCH2, SOL)
edge("aboutObject", E_TRANCHE, TRANCH2)

# the event the update itself is about (the memo reports the quarter)
edge("related", SRC, NW)
edge("related", SRC, SOL)

# the gate: audit passing IMPLies the tranche release (one-way, no belief damping)
gate = sdk.create_direct_edge(
    "IMPL", E_AUDIT, E_TRANCHE,
    direction="->", label="gatedBy", weight=0.9,
)
print("direct edge:", gate.get("created"))

# ---------------------------------------------------------------- report counts
kinds = {k["kind"]: k["count"] for k in sdk.list_pointkinds()}
print("POINT_KINDS:", kinds)
print("MADE:", {k: len(v) for k, v in MADE.items()})
print("EDGES:", EDGES)
