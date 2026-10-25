from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

NORTHWIND_ID = "obj-84daa500364ed082b525286aad"   # pre-existing Object — MUST NOT duplicate

out = {}

# --- 1. Reuse the existing Object. Record the second spelling as an alias,
#        not as a new node (same legal entity, abbreviated in the SOW).
try:
    out["object_update"] = sdk.update_entity(
        NORTHWIND_ID,
        alternateNames=["Northwind Logistics Ltd"],
        alsoKnownAs="Northwind Logistics Ltd",
    )
except Exception as e:
    print("update_entity failed:", type(e).__name__, e)
    out["object_update"] = None

# --- 2. The signed SOW as the document of record for the name variant.
sow = sdk.create_source(
    url="urn:document:northwind-logistics-sow",
    sourceKind="contract",
    documentKind="contract",
    title="Northwind Logistics — signed Statement of Work (SOW)",
    sourceDate="2026-10-04",
)
print("source:", sow.get("id"))
out["sow"] = sow

# --- 3. Subject: Marta Reyes, the new CFO.
marta = sdk.create_subject(
    "Marta Reyes",
    subjectKind="person",
    role="CFO",
    roleTitle="Chief Financial Officer",
    organization="Northwind Logistics",
    startedRole="2026",
)
print("subject:", marta.get("id"))
out["marta"] = marta

# --- 4. Event: the 2026-10-04 confirmation.
event = sdk.create_event(
    "Northwind Logistics confirmed January renewal (2026-10-04)",
    "customerCommitment",
    startedAt="2026-10-04",
    occurredOn="2026-10-04",
    summary="Northwind Logistics confirmed they will renew in January. Their new CFO is Marta Reyes.",
    aboutObject=NORTHWIND_ID,
    sourceId=out["sow"]["id"],
)
print("event:", event.get("id"))
out["event"] = event

# --- 5. Points (the retrievable claims).
renewal = sdk.create_point(
    "statement",
    "Northwind Logistics confirmed on 2026-10-04 that it will renew in January 2027.",
    confirmedOn="2026-10-04",
    renewalPeriod="2027-01",
    asStated="confirmed they will renew in January",
    aboutObject=NORTHWIND_ID,
    extractedFrom=out["sow"]["id"],
)
print("renewal:", renewal.get("id"))

alias = sdk.create_point(
    "statement",
    "Northwind Logistics is also written 'Northwind Logistics Ltd' in the signed SOW; the two names denote the same entity.",
    aliasOf="Northwind Logistics Ltd",
    aboutObject=NORTHWIND_ID,
    extractedFrom=out["sow"]["id"],
)
print("alias:", alias.get("id"))

cfo = sdk.create_point(
    "statement",
    "Marta Reyes is the new CFO of Northwind Logistics (as of 2026-10-04).",
    effectiveFrom="2026",
    aboutSubject=marta["id"],
    aboutObject=NORTHWIND_ID,
)
print("cfo:", cfo.get("id"))

out.update(renewal=renewal, alias=alias, cfo=cfo)

# --- 6. Structural edges (reification rules: structural stays plain).
def edge(rel, frm, to):
    try:
        r = sdk.create_edge(rel, frm, to)
        print("edge", rel, "->", r)
        return r
    except Exception as e:
        print("edge FAILED", rel, type(e).__name__, e)
        return None

edge("aboutObject", out["renewal"]["id"], NORTHWIND_ID)
edge("aboutObject", out["alias"]["id"], NORTHWIND_ID)
edge("aboutSubject", out["cfo"]["id"], marta["id"])
edge("aboutObject", out["cfo"]["id"], NORTHWIND_ID)
edge("aboutEvent", out["cfo"]["id"], event["id"])
edge("aboutEvent", out["renewal"]["id"], event["id"])
edge("wasDerivedFrom", out["alias"]["id"], out["sow"]["id"])
edge("wasDerivedFrom", out["renewal"]["id"], out["sow"]["id"])
edge("managedBy", NORTHWIND_ID, marta["id"])
edge("hasMember", NORTHWIND_ID, marta["id"])

print("TAXONOMY:", sdk.taxonomy())

# --- 7. Document-of-record link for the event (Event)-[:aboutDocument]->(Source)
edge("aboutDocument", out["event"]["id"], out["sow"]["id"])   # Source carries documentKind, so the document edge is valid
