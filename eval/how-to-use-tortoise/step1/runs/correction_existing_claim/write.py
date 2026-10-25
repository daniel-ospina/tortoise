import json

from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

OLD_ID = "1a1660d461c-819425aa07b0"      # "Our Q3 churn was 4.1%."
SRC = "https://dash.internal.example/churn/q3"
CORRECTED_ON = "2026-10-03"

# ---- cleanup of probe pollution (invalid-kind point + probe event) ----
for p in sdk.paginated_query(limit=100)["results"]:
    if p["pointKind"] == "zzz_not_a_kind":
        print("cleanup point:", p["id"], sdk.delete(p["id"]))
try:
    for e in sdk.get_events():
        nm = e.get("name") or e.get("id")
        if nm == "probe":
            print("cleanup event:", e.get("id"), sdk.delete_entity(e["id"]))
except Exception as e:
    print("event cleanup:", type(e).__name__, e)

# ---- 1. the corrected figure (same kind as the claim it replaces) ----
new = sdk.create_point(
    "statement",
    "Our Q3 churn was 3.2%.",
    when=CORRECTED_ON,
    status="live",
    dedup=True,
    metric="churn",
    period="Q3",
    value="3.2%",
    replacesValue="4.1%",
    basis="duplicate account excluded",
)
print("NEW:", json.dumps(new, indent=2, default=str))
NEW_ID = new["node"]["id"] if "node" in new else new.get("id")

# ---- 2. why the published figure was wrong ----
why = sdk.create_point(
    "rationale",
    "The 4.1% Q3 churn figure was wrong because it included a duplicate account; 3.2% is the figure with the duplicate excluded.",
    when=CORRECTED_ON,
    status="live",
    dedup=True,
)
print("WHY:", json.dumps(why, indent=2, default=str))
WHY_ID = why["node"]["id"] if "node" in why else why.get("id")

# ---- 3. the data-quality defect and when the source changed ----
defect = sdk.create_point(
    "incident",
    "The Q3 churn dashboard double-counted one account, which inflated the published Q3 churn figure to 4.1%; the dedupe fix landed only after that figure had been read from the dashboard.",
    when=CORRECTED_ON,
    status="live",
    dedup=True,
    sourceUrl=SRC,
)
print("DEFECT:", json.dumps(defect, indent=2, default=str))
DEFECT_ID = defect["node"]["id"] if "node" in defect else defect.get("id")

# ---- 4. the correction as a dated event ----
ev = sdk.create_event(
    "Q3 churn figure correction (4.1% -> 3.2%)",
    "correction",
    when=CORRECTED_ON,
    aboutPoint=NEW_ID,   # singular: a list is silently dropped, no edge wired
    aboutDocument=SRC,
)
print("EVENT:", json.dumps(ev, indent=2, default=str))

# NOTE: do not re-run against a populated graph — create_operator would duplicate operators.
# ---- 5. supersede the old claim (CORRECTS + outdated, edge transfer) ----
sup = sdk.supersede_point(OLD_ID, NEW_ID, valid_from=CORRECTED_ON)
print("SUPERSEDE:", json.dumps(sup, indent=2, default=str)[:1200])

# ---- 6. epistemic edges ----
print("OP why->new:", json.dumps(sdk.create_operator("IMPL", WHY_ID, [NEW_ID], label="supports", semantics="supports"), indent=2, default=str)[:900])
print("OP defect->old:", json.dumps(sdk.create_operator("IMPL", DEFECT_ID, [OLD_ID], label="causes", semantics="causes"), indent=2, default=str)[:900])

# ---- 7. provenance ----
print("EDGE old->src:", sdk.create_edge("wasDerivedFrom", OLD_ID, SRC))
print("EDGE new->src:", sdk.create_edge("wasDerivedFrom", NEW_ID, SRC))
print("EDGE defect->src:", sdk.create_edge("references", DEFECT_ID, SRC))

print("TAXONOMY:", json.dumps(sdk.taxonomy(), default=str))
print("POINTS:", json.dumps(sdk.paginated_query(limit=50, include_retracted=True), indent=2, default=str))
