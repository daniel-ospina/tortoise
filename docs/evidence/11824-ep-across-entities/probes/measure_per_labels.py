"""Measurement fixtures for the EP-across-entity-kinds audit (#11824) — part 1.

See ../README.md §1 for how to run this (a private test lane).

Read-only intent: builds small throwaway graph fixtures in a private test lane
graph and prints observed behaviour. Nothing here is part of the deliverable.
"""
from __future__ import annotations

import os
import sys
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)

from tortoise.sdk import TortoiseSDK  # noqa: E402

SFX = uuid.uuid4().hex[:8]
GRAPH = f"ep_audit_{SFX}"
# The lane's OWN container port. Derive it rather than hard-code it — a baked
# port silently points at whatever lane happens to hold it (a stale container,
# or a peer lane's):
#
#     uv run python tools/test_lane.py status    # prints ``uri=…:PORT/…``
#     EP_AUDIT_DB_PORT=<that PORT> uv run python <this file>
PORT = os.environ.get("EP_AUDIT_DB_PORT", "16416")
os.environ["TORTOISE_DB_URI"] = f"docker://:@127.0.0.1:{PORT}/{GRAPH}"

sdk = TortoiseSDK(db_path=None, namespace=None)
sdk._db_uri = os.environ["TORTOISE_DB_URI"]
sdk._proj = None
g = sdk._get_proj().g


def q(cypher, **params):
    return g.query(cypher, params=params).result_set


def node(nid):
    return q(
        "MATCH (n) WHERE n.id = $id RETURN labels(n), n.status, "
        "coalesce(n.outdated,false), coalesce(n.isClosed,false), n.pipelineSlot, "
        "n.is_operator, n.confidence, coalesce(n.posterior_alpha,n.ep_alpha), "
        "coalesce(n.posterior_beta,n.ep_beta), n.goalState", id=nid)


def belief(nid):
    r = q("MATCH (n) WHERE n.id = $id RETURN n.confidence, "
          "coalesce(n.posterior_alpha,0.0), coalesce(n.posterior_beta,0.0), "
          "coalesce(n.ep_alpha,1.0), coalesce(n.ep_beta,1.0)", id=nid)
    return r[0] if r else None


def show(tag, nid):
    print(f"  {tag:<34} {nid.split('-')[-1][:10]:<12} {node(nid)}")


print("=" * 100)
print("A. WIRING — which labels an operator/direct edge may address")
print("=" * 100)

P1 = sdk.create_point("statement", f"A-point-src-{SFX}", status="live")["id"]
P2 = sdk.create_point("statement", f"A-point-tgt-{SFX}", status="live")["id"]
O1 = sdk.create_object(f"A-object-{SFX}", "core:goal")["id"]
E1 = sdk.create_event(f"A-event-{SFX}", "decision")["id"]
S1 = sdk.create_subject(f"A-subject-{SFX}", "naturalPerson")["id"]
q("MERGE (s:Source {url:$u}) SET s.sourceKind='document', s.id=$i",
  u=f"https://x/{SFX}", i=f"src-{SFX}")
SRC_ID = q("MATCH (s:Source {url:$u}) RETURN s.url, s.id", u=f"https://x/{SFX}")
for tag, nid in [("Point", P1), ("Object", O1), ("Event", E1),
                 ("Subject", S1)]:
    show(tag, nid)

print("\n  -- operator route (create_operator IMPL src=[target]) --")
for tag, src, tgt in [
    ("Point -> Point", P1, P2),
    ("Object -> Point", O1, P2),
    ("Point -> Object", P1, O1),
    ("Event -> Point (direct edge)", E1, P2),
    ("Subject -> Point", S1, P2),
]:
    try:
        if "direct" in tag:
            r = sdk.create_direct_edge("IMPL", src, tgt, direction="unidirectional")
        else:
            r = sdk.create_operator("IMPL", src, [tgt], direction="unidirectional")
        print(f"  {tag:<32} WROTE  op={str(r.get('id'))[-8:]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {tag:<32} REFUSED {type(exc).__name__}: {str(exc)[:110]}")

print("\n  -- :Source endpoint (documented as NOT admitted) --")
try:
    sdk.create_operator("IMPL", SRC_ID[0][1] if SRC_ID[0][1] else SRC,
                        [P1], direction="unidirectional")
    print("  Source -> Point                  WROTE")
except Exception as exc:  # noqa: BLE001
    print(f"  Source -> Point                  REFUSED {type(exc).__name__}: {str(exc)[:150]}")

print("\n  -- operational labels: does a :PipelineStage / :Batch resolve as epistemic? --")
print("  PipelineStage/Batch are not :Point (tortoise/pipeline.py:52, lifecycle_vocabulary.py:63)")
print("  ep.EPISTEMIC_LABELS =", __import__("tortoise.live", fromlist=["x"]).EPISTEMIC_LABELS)

print()
print("=" * 100)
print("B. ENTITY BELIEF SEED — does a stored `confidence` feed EP?")
print("=" * 100)

O2 = sdk.create_object(f"B-object-conf1-{SFX}", "core:goal",
                       confidence=1.0)["id"]
show("Object w/ confidence=1.0", O2)
print("  served belief_value(cols):", belief(O2))

P3 = sdk.create_point("statement", f"B-point-tgt-{SFX}", status="live")["id"]
opB = sdk.create_operator("IMPL", O2, [P3], direction="unidirectional")["id"]
sdk.compute_confidence(factors=[opB], require_calibration=False)
print("  after EP run over IMPL(Object->Point):")
print("    Object posterior:", belief(O2))
print("    Point  posterior:", belief(P3))

try:
    sdk.set_point_baseline(O2, 9.0, 1.0)
    print("  set_point_baseline on an OBJECT: no exception")
except Exception as exc:  # noqa: BLE001
    print(f"  set_point_baseline on an OBJECT: {type(exc).__name__}: {str(exc)[:120]}")
print("  Object ep_alpha after attempt:", q(
    "MATCH (n:Object {id:$id}) RETURN n.ep_alpha, n.baseline_set, "
    "n.posterior_alpha", id=O2))

print()
print("=" * 100)
print("C. POSITION AXIS — is an ended node stripped from a factor INPUT?")
print("=" * 100)


def run_pair(src_id, tgt_label="Point"):
    tgt = sdk.create_point("statement", f"C-tgt-{uuid.uuid4().hex[:6]}",
                           status="live")["id"]
    op = sdk.create_operator("IMPL", src_id, [tgt], direction="unidirectional")["id"]
    sdk.compute_confidence(factors=[op], require_calibration=False)
    return tgt, belief(tgt)


# C1: control — a live Point source with a strong prior moves its target
C1 = sdk.create_point("statement", f"C1-live-{SFX}", status="live")["id"]
sdk.set_point_baseline(C1, 8.0, 2.0)
tgt, _ = run_pair(C1)
CONTROL = belief(tgt)   # the verdict every other case is read against


def verdict(b):
    """Derived from the measurement, never typed.

    Criterion: a factor that RAN with a non-vacuous source leaves its target an
    ASYMMETRIC posterior (alpha != beta). A dropped factor leaves the target
    untouched (no posterior at all, or a symmetric one from an earlier run).
    """
    if not b:
        return "dropped (no row)"
    alpha, beta = b[1], b[2]
    if alpha in (None, 0.0) and beta in (None, 0.0):
        return "dropped (untouched)"
    return "contributed" if alpha != beta else "dropped (symmetric)"


print(f"  C1 live Point (prior 8,2)          -> target {CONTROL}  ({verdict(CONTROL)})")

# C2: superseded on the STATUS axis — must NOT contribute
C2 = sdk.create_point("statement", f"C2-superseded-{SFX}", status="live")["id"]
sdk.set_point_baseline(C2, 8.0, 2.0)
q("MATCH (n:Point {id:$id}) SET n.status = 'superseded'", id=C2)
tgt, b = run_pair(C2)
print(f"  C2 status=superseded (prior 8,2)   -> target {b}  ({verdict(b)})")
print("    C2 shape:", node(C2))

# C3: isClosed=true on the POSITION axis (status left live, flag unset)
C3 = sdk.create_point("statement", f"C3-closed-{SFX}", status="live")["id"]
sdk.set_point_baseline(C3, 8.0, 2.0)
q("MATCH (n:Point {id:$id}) SET n.isClosed = true", id=C3)
tgt, b = run_pair(C3)
print(f"  C3 isClosed=true, status=live      -> target {b}  ({verdict(b)})")
print("    C3 shape:", node(C3))

# C4: terminal pipeline slot on the POSITION axis
C4 = sdk.create_point("statement", f"C4-slot-{SFX}", status="live")["id"]
sdk.set_point_baseline(C4, 8.0, 2.0)
q("MATCH (n:Point {id:$id}) SET n.pipelineSlot = 'terminal'", id=C4)
tgt, b = run_pair(C4)
print(f"  C4 pipelineSlot=terminal           -> target {b}  ({verdict(b)})")
print("    C4 shape:", node(C4))

# C5: an OBJECT ended only by isClosed, carrying a measured posterior
C5 = sdk.create_object(f"C5-closed-object-{SFX}", "core:goal")["id"]
q("MATCH (n:Object {id:$id}) SET n.posterior_alpha = 9.0, "
  "n.posterior_beta = 1.0, n.confidence = 0.9, n.isClosed = true", id=C5)
tgt, b = run_pair(C5)
print(f"  C5 Object isClosed=true (9,1)      -> target {b}  ({verdict(b)})")
print("    C5 shape:", node(C5))

# C6: the same Object, an :Source-shaped control — Source cannot be wired at all
print("\n  control: wiring an ended Point as an operator SEED (compute_confidence(factors=[...])):")
sdk.compute_confidence(factors=[C3], require_calibration=False)
print("    seed C3 (isClosed) posterior:", belief(C3))

print()
print("=" * 100)
print("D. goalState — is it read by EP at all?")
print("=" * 100)

# D1: goal Object with goalState=abandoned + status=live (legacy shape)
G1 = sdk.create_object(f"D1-goal-{SFX}", "core:goal")["id"]
q("MATCH (n:Object {id:$id}) SET n.goalState='abandoned', n.status='live', "
  "n.posterior_alpha=9.0, n.posterior_beta=1.0, n.confidence=0.9", id=G1)
tgt, b = run_pair(G1)
print(f"  D1 goal goalState=abandoned,status=live -> target {b}")
print("    D1 shape:", node(G1))

# D2: same but the write boundary's coherent shape (status also ended)
G2 = sdk.create_object(f"D2-goal-{SFX}", "core:goal")["id"]
q("MATCH (n:Object {id:$id}) SET n.goalState='abandoned', n.status='deprecated', "
  "n.posterior_alpha=9.0, n.posterior_beta=1.0, n.confidence=0.9", id=G2)
tgt, b = run_pair(G2)
print(f"  D2 goal goalState=abandoned,status=deprecated -> target {b}")
print("    D2 shape:", node(G2))

# D3: goalState=met with status=live — is 'met' an ending anywhere?
G3 = sdk.create_object(f"D3-goal-{SFX}", "core:goal")["id"]
q("MATCH (n:Object {id:$id}) SET n.goalState='met', n.status='live', "
  "n.posterior_alpha=9.0, n.posterior_beta=1.0, n.confidence=0.9", id=G3)
tgt, b = run_pair(G3)
print(f"  D3 goal goalState=met,status=live       -> target {b}")

print("\n  -- what the currency predicate itself says about these shapes --")
from tortoise.live import is_terminal_status  # noqa: E402

for tag, st, od, closed, slot, gs in [
    ("D1 goalState=abandoned, status=live", "live", False, False, None, "abandoned"),
    ("D2 goalState=abandoned, status=deprecated", "deprecated", False, False, None, "abandoned"),
    ("D3 goalState=met, status=live", "live", False, False, None, "met"),
]:
    print(f"    is_terminal_status({tag}) = "
          f"{is_terminal_status(st, od, is_closed=closed, stage_slot=slot)}")

print()
print("=" * 100)
print("E. EVENT — does an Event endpoint receive a posterior, and can it seed?")
print("=" * 100)

E5 = sdk.create_event(f"E5-event-src-{SFX}", "decision")["id"]
P9 = sdk.create_point("statement", f"E5-point-tgt-{SFX}", status="live")["id"]
opE = sdk.create_operator("IMPL", E5, [P9], direction="unidirectional")["id"]
sdk.compute_confidence(factors=[opE], require_calibration=False)
print("  Event id present?", q("MATCH (n:Event {id:$id}) RETURN n.id, n.eventId",
                              id=E5))
print("    Event posterior after run:", belief(E5))
print("    Point posterior after run:", belief(P9))
q("MATCH (n:Event {id:$id}) SET n.posterior_alpha=9.0, n.posterior_beta=1.0",
  id=E5)
sdk.compute_confidence(factors=[opE], require_calibration=False)
print("    Event seeded (9,1) -> Point:", belief(P9))

print("\n  -- :Event and :Subject in the TARGET slot (operator source = a live Point) --")
for kind in ("Event", "Subject", "Object"):
    srcP = sdk.create_point("statement", f"E-src-{uuid.uuid4().hex[:6]}",
                            status="live")["id"]
    sdk.set_point_baseline(srcP, 9.0, 1.0)
    if kind == "Event":
        tgt = sdk.create_event(f"E-tgt-{SFX}", "decision")["id"]
    elif kind == "Subject":
        tgt = sdk.create_subject(f"E-tgt-{SFX}", "naturalPerson")["id"]
    else:
        tgt = sdk.create_object(f"E-tgt-{SFX}", "core:goal")["id"]
    op = sdk.create_operator("IMPL", srcP, [tgt], direction="unidirectional")["id"]
    sdk.compute_confidence(factors=[op], require_calibration=False)
    print(f"    {kind:<8} TARGET posterior={belief(tgt)}")

print("\n  -- hydrate_evidence universe --")
sdk._evidence = {}
sdk._hydrate_evidence()
print("    evidence ids:", len(sdk._evidence))
print("    any non-Point in evidence?",
      [pid for pid in sdk._evidence
       if q("MATCH (n:Point {id:$id}) RETURN n.id", id=pid) == []])

print()
print("=" * 100)
print("F. CONTESTED / ANNOTATION SURFACES — per-label coverage")
print("=" * 100)
cont = sdk._ep.get_contested_claims() if hasattr(sdk, "_ep") else None
try:
    from tortoise.ep import TortoiseEP  # noqa: E402

    ep = TortoiseEP(sdk._get_proj())
    rows = ep.get_contested_claims()
    print(f"  get_contested_claims() rows: {len(rows)}")
    for r in rows[:8]:
        print("   ", {k: r[k] for k in list(r)[:4]})
except Exception as exc:  # noqa: BLE001
    print("  get_contested_claims raised:", type(exc).__name__, exc)

print("\nDONE")
