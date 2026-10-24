"""Record the 'person_appointment' input into the Tortoise graph.

Input:
  2026-09-08: Marta Reyes joined Northwind Logistics as CFO. She was previously
  VP Finance at Halcyon Group. She reports to the CEO, Tobias Lund.

Model:
  Subjects  = the people (actors)
  Objects   = the organisations and the specific role instances
  Event     = the dated appointment (the thing that happened on 2026-09-08)
  Source    = the note the facts were read from (provenance anchor)
  Points    = the individual atomic claims, so each can be cited/superseded
  Edges     = membership / role / reporting structure + provenance + topicality
"""

from __future__ import annotations

from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

K = "person_appointment"
NOTE_DATE = "2026-09-08"
NOTE_URL = "memory://note/person_appointment"
NOTE_TITLE = "Appointment note: Marta Reyes, Northwind Logistics (2026-09-08)"


def sub(name: str, kind: str, **props) -> str:
    return sdk.create_subject(name, kind, dedup=True, **props)["id"]


def obj(name: str, kind: str, **props) -> str:
    return sdk.create_object(name, kind, dedup=True, **props)["id"]


def point(content: str, key: str, **props) -> str:
    return sdk.create_point(
        "statement", content, dedup=True, key=f"{K}:{key}", **props
    )["id"]


# --- Source (provenance) ----------------------------------------------------
src = sdk.create_source(
    NOTE_URL,
    "note",
    title=NOTE_TITLE,
    sourceDate=NOTE_DATE,
)["id"]

# --- Entities ---------------------------------------------------------------
marta = sub("Marta Reyes", "person", description="CFO at Northwind Logistics since 2026-09-08")
tobias = sub("Tobias Lund", "person", description="CEO of Northwind Logistics")

northwind = obj("Northwind Logistics", "organization")
halcyon = obj("Halcyon Group", "organization")

cfo_role = obj("CFO at Northwind Logistics", "role", tenure="current")
ceo_role = obj("CEO at Northwind Logistics", "role")
vp_fin_role = obj("VP Finance at Halcyon Group", "role", tenure="prior")

# --- Event: the dated appointment -------------------------------------------
event = sdk.create_event(
    "Marta Reyes joins Northwind Logistics as CFO",
    "employmentStart",
    date=NOTE_DATE,
)["id"]

# --- Points: the atomic claims ---------------------------------------------
p_join = point(
    "Marta Reyes joined Northwind Logistics as CFO on 2026-09-08.",
    "joined-northwind-cfo",
)
p_prior = point(
    "Marta Reyes was previously VP Finance at Halcyon Group, before joining "
    "Northwind Logistics in 2026.",
    "prior-halcyon-vp-finance",
)
p_reports = point(
    "Marta Reyes reports to Tobias Lund, the CEO of Northwind Logistics.",
    "reports-to-tobias-lund",
)
p_ceo = point(
    "Tobias Lund is the CEO of Northwind Logistics (implied by the note: "
    "'She reports to the CEO, Tobias Lund').",
    "tobias-lund-is-ceo",
)

# --- Structural edges -------------------------------------------------------
edges = [
    # current appointment
    ("memberOf", marta, northwind),
    ("holdsRole", marta, cfo_role),
    ("hasPart", northwind, cfo_role),
    # reporting line
    ("reportsTo", marta, tobias),
    ("holdsRole", tobias, ceo_role),
    ("hasPart", northwind, ceo_role),
    # prior role
    ("holdsRole", marta, vp_fin_role),
    ("related", vp_fin_role, halcyon),
    # the event, and the note that records it
    ("aboutSubject", event, marta),
    ("aboutObject", event, northwind),
    ("aboutSource", event, src),
    # topicality: which entity each claim is about
    ("aboutSubject", p_join, marta),
    ("aboutObject", p_join, northwind),
    ("aboutEvent", p_join, event),
    ("aboutSubject", p_prior, marta),
    ("aboutObject", p_prior, halcyon),
    ("aboutObject", p_prior, vp_fin_role),
    ("aboutSubject", p_reports, marta),
    ("aboutObject", p_reports, tobias),
    ("aboutSubject", p_ceo, tobias),
    ("aboutObject", p_ceo, northwind),
    # provenance: every claim was read out of the note
    ("wasDerivedFrom", p_join, src),
    ("wasDerivedFrom", p_prior, src),
    ("wasDerivedFrom", p_reports, src),
    ("wasDerivedFrom", p_ceo, src),
]

for relation, from_id, to_id in edges:
    res = sdk.create_edge(relation, from_id, to_id)
    print(f"EDGE {relation:16s} {res.get('created')!s:5s} {from_id} -> {to_id}")

print("IDS", {"source": src, "subject_marta": marta, "subject_tobias": tobias,
              "object_northwind": northwind, "object_halcyon": halcyon,
              "object_cfo": cfo_role, "object_ceo": ceo_role,
              "object_vp_fin": vp_fin_role, "event_join": event,
              "point_join": p_join, "point_prior": p_prior,
              "point_reports": p_reports, "point_ceo": p_ceo})
print("TAXONOMY", sdk.taxonomy())
