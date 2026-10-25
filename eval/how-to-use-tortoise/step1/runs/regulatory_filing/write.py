"""Record the SEC Form D filing (id: regulatory_filing) into the Tortoise graph.

Everything stated in the input is recorded as a node; the document it was read
from is the :Source, and every claim carries extractedFrom provenance to it.
"""
from __future__ import annotations

import json

from tortoise.sdk import TortoiseSDK

URL = ("https://www.sec.gov/Archives/edgar/data/0001234567/"
       "000123456726000001/xslFormDX01/primary_doc.xml")
FILED = "2026-06-11"
RETRIEVED = "2026-10-05"

sdk = TortoiseSDK()
out = {"nodes": [], "edges": [], "errors": [], "advisories": []}


def track(label, res, name):
    out["nodes"].append({"label": label, "id": res["id"], "name": name})
    if res.get("advisories"):
        out["advisories"].append({"node": res["id"], "advisories": res["advisories"]})
    return res["id"]


def edge(relation, a, b):
    r = sdk.create_edge(relation, a, b)
    out["edges"].append({"relation": relation, "from": a, "to": b,
                         "nodes": r.get("edge"), "created": r.get("created"),
                         "nudges": r.get("nudges")})


# ---- Source: the filing document itself -------------------------------
SRC = track("Source", sdk.create_source(
    URL,
    sourceKind="document",
    tier="gold",
    title="SEC Form D - Solstice Robotics Inc. - filed 2026-06-11",
    sourceDate=FILED,
), "SEC Form D primary_doc.xml")

# ---- Subjects: the filer and the signing role -------------------------
ORG = track("Subject", sdk.create_subject(
    "Solstice Robotics Inc.",
    subjectKind="core:organization",
    partyRole="issuer",
), "Solstice Robotics Inc.")

CFO = track("Subject", sdk.create_subject(
    "CFO of Solstice Robotics Inc.",
    subjectKind="core:role",
    roleTitle="Chief Financial Officer",
), "CFO of Solstice Robotics Inc.")

# ---- Event: the filing act -------------------------------------------
EV = track("Event", sdk.create_event(
    "SEC Form D filed by Solstice Robotics Inc. on 2026-06-11",
    "occurrence",
    eventDate=FILED,
    formType="D",
    regulator="U.S. Securities and Exchange Commission",
    accessionNumber="0001234567-26-000001",
    sourceDocument=URL,
), "SEC Form D filing 2026-06-11")

# ---- Object: the offering the filing reports -------------------------
OFF = track("Object", sdk.create_object(
    "Solstice Robotics Inc. SEC Form D offering (2026-06-11)",
    objectKind="other",
    filingType="Form D",
    formType="D",
    regulator="U.S. Securities and Exchange Commission",
    accessionNumber="0001234567-26-000001",
    offeringAmountUsd=40000000,
    amountSoldUsd=12500000,
    investorCount=14,
), "SEC Form D offering")

# ---- Points: the reported facts (each sourced from the filing) -------
P_OFF = track("Point", sdk.create_point(
    "observation",
    "Total offering amount of the SEC Form D filed by Solstice Robotics Inc. "
    "on 2026-06-11 was $40,000,000.",
    extractedFrom=URL,
    metric="totalOfferingAmount",
    value=40000000,
    unit="USD",
    asReported="$40,000,000",
), "total offering amount $40,000,000")

P_SOLD = track("Point", sdk.create_point(
    "observation",
    "Total amount sold in the SEC Form D filed by Solstice Robotics Inc. "
    "on 2026-06-11 was $12,500,000.",
    extractedFrom=URL,
    metric="totalAmountSold",
    value=12500000,
    unit="USD",
    asReported="$12,500,000",
), "total amount sold $12,500,000")

P_INV = track("Point", sdk.create_point(
    "observation",
    "The SEC Form D filed by Solstice Robotics Inc. on 2026-06-11 reports "
    "14 investors.",
    extractedFrom=URL,
    metric="investorCount",
    value=14,
    unit="investors",
    asReported="14",
), "14 investors")

P_SIG = track("Point", sdk.create_point(
    "statement",
    "The SEC Form D filing by Solstice Robotics Inc. is signed by the CFO.",
    extractedFrom=URL,
    signerRole="CFO",
    signedByTitle="Chief Financial Officer",
), "signed by the CFO")

P_RET = track("Point", sdk.create_point(
    "observation",
    "The SEC Form D filing was retrieved from EDGAR on 2026-10-05.",
    extractedFrom=URL,
    retrievedOn=RETRIEVED,
    sourceUrl=URL,
), "retrieved 2026-10-05")

# ---- Edges -----------------------------------------------------------
edge("aboutSubject", EV, ORG)      # filing was made by the issuer
edge("aboutObject", EV, OFF)       # filing is about this offering
edge("aboutSource", EV, SRC)       # filing document
edge("participatesIn", ORG, EV)    # issuer is party to the filing
edge("aboutSubject", OFF, ORG)     # offering belongs to the issuer
edge("wasDerivedFrom", OFF, SRC)   # offering record derived from the filing
edge("memberOf", CFO, ORG)         # the CFO role belongs to the issuer

for p in (P_OFF, P_SOLD, P_INV):
    edge("aboutObject", p, OFF)
    edge("aboutSubject", p, ORG)
edge("aboutSubject", P_SIG, CFO)
edge("aboutSubject", P_SIG, ORG)
edge("aboutSource", P_RET, SRC)

print(json.dumps(out, indent=2, default=str))
