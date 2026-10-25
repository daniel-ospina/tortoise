"""Record the 'contradicting_claim' input into the Tortoise graph.

Existing state:
  AUDIT   1a1660ca005  "Solstice Robotics passed its safety audit on 2026-08-28."
  TRANCHE 1a1660ca020  "Acme released tranche 2."
  OP      1a1660ca027  IMPL  AUDIT --supports--> TRANCHE
"""
import json

from tortoise.sdk import TortoiseSDK

AUDIT = "1a1660ca005-deacc7abfd22"
TRANCHE = "1a1660ca020-56da3faef0b5"
OP = "1a1660ca027-f7d58fb90b42"
AS_OF = "2026-10-06"


def show(tag, r):
    print(f"--- {tag} ---")
    print(json.dumps(r, default=str, ensure_ascii=False)[:1500])


sdk = TortoiseSDK()

# 1. The scope limitation: the Aug audit covered hardware only, never firmware.
scope = sdk.create_point(
    "statement",
    "The safety audit Solstice Robotics passed on 2026-08-28 was scoped to hardware only; "
    "it did not cover the firmware.",
    dedup=True,
    key="solstice-audit-2026-08-28-scope",
    credibility="medium",
    asOf=AS_OF,
    scopeCovers="hardware",
    scopeExcludes="firmware",
)
show("scope point", scope)

# 2. The outstanding second audit, covering firmware, still open.
fw_audit = sdk.create_point(
    "statement",
    "A second safety audit covering Solstice Robotics' firmware was still open as of 2026-10-06; "
    "it has not concluded.",
    dedup=True,
    key="solstice-firmware-audit-2026-10-06",
    credibility="medium",
    asOf=AS_OF,
    auditStatus="open",
    scopeCovers="firmware",
    firstAudit=f"point:{AUDIT}",
)
show("firmware audit point", fw_audit)

# 3. The two open findings the auditors flagged.
findings = sdk.create_point(
    "issue",
    "Auditors have flagged two open findings in the Solstice Robotics firmware-scope audit "
    "(as of 2026-10-06).",
    dedup=True,
    key="solstice-firmware-audit-open-findings",
    credibility="medium",
    asOf=AS_OF,
    openFindings=2,
    findingsStatus="open",
    audit=f"point:{fw_audit['id']}",
)
show("findings point", findings)

scope_id = scope["id"]
fw_id = fw_audit["id"]
fnd_id = findings["id"]

# 4. The disclosure event, so the 2026-10-06 dating survives as an episodic record.
EVENT_NAME = (
    "Solstice Robotics audit scope disclosed: 2026-08-28 audit was hardware-only, "
    "firmware audit still open with two open findings"
)
# NOTE (tool-surface finding): create_event only wires ``about*`` when the value is
# a single id STRING; a list is silently dropped. So the primary link goes through
# create_event and the other two are minted as structural ``related`` edges
# (``aboutPoint`` is NOT a predicate create_edge accepts).
ev_id = None
for e in sdk.get_events("observation", limit=200):
    if e.get("name") != EVENT_NAME:
        continue
    linked = sdk._get_proj().g.query(
        f"MATCH (e)-[r]->() WHERE e.id = '{e['id']}' RETURN count(r)"
    ).result_set[0][0]
    if linked:
        ev_id = e["id"]           # already wired by a prior run: reuse
        print("--- event (reused) ---", ev_id)
    else:
        sdk.delete(e["id"])       # bare event from a run that mis-passed a list
if ev_id is None:
    ev = sdk.create_event(EVENT_NAME, "observation", occurredAt=AS_OF, aboutPoint=scope_id)
    show("event", ev)
    ev_id = ev["id"]

for target in (fw_id, fnd_id):
    show(f"edge related {ev_id}->{target}", sdk.create_edge("related", ev_id, target))

# 5. The scope fact is the same audit, narrowed -> relate it to the existing audit claim.
for relation, f, t in [
    ("related", scope_id, AUDIT),
    ("related", fw_id, AUDIT),
    ("related", fnd_id, fw_id),
    ("related", fw_id, TRANCHE),
]:
    show(f"edge {relation} {f}->{t}", sdk.create_edge(relation, f, t))

# 6. The core epistemic action: the existing IMPL bridge (audit => tranche release) is
#    damped, not refuted. The audit happened and still speaks to hardware; what it cannot
#    do is vouch for the firmware half of the release, and the firmware audit is unresolved
#    with two open findings. Major dampening within the sanctionted [0.10, 0.50] band.
mit = sdk.mitigate_operator(
    OP,
    reason=(
        "The 2026-08-28 audit was scoped to hardware only and did not cover firmware "
        f"(point {scope_id}); the firmware audit is still open with two open findings "
        f"(points {fw_id}, {fnd_id}) as of {AS_OF}. The audit therefore cannot vouch for the "
        "firmware portion of what the tranche-2 release rests on."
    ),
    strength=0.5,
    credibility="medium",
)
show("mitigation", mit)

# 7. Verify final state.
print("\n=== VERIFY ===")
for p in sdk.query():
    print(p["id"], p["pointKind"], "|", p["content"][:110])
proj = sdk._get_proj()
res = proj.g.query(
    "MATCH (a)-[r]->(b) WHERE type(r) IN ['mitigated_by','IMPL','NAND','related','aboutPoint'] "
    "RETURN coalesce(a.id,a.name), type(r), coalesce(b.id,b.name)"
)
print(json.dumps(res.result_set, default=str, ensure_ascii=False))
