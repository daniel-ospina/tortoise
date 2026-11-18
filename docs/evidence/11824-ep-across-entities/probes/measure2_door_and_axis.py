"""Measurement fixtures for the EP-across-entity-kinds audit (#11824) — part 2.

door-driven endings, the direct-edge position axis, per-label participation.

See ../README.md §1 for how to run this. The port is NOT baked in: derive it
(``uv run python tools/test_lane.py status`` prints ``uri=…:PORT/…``) and pass
it as ``EP_AUDIT_DB_PORT``. The variable is REQUIRED — there is no fallback,
because a fallback port is exactly the bake-in this docstring forbids.
"""
from __future__ import annotations

import os
import sys
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)

from tortoise.sdk import TortoiseSDK  # noqa: E402

SFX = uuid.uuid4().hex[:8]
PORT = os.environ["EP_AUDIT_DB_PORT"]
os.environ["TORTOISE_DB_URI"] = f"docker://:@127.0.0.1:{PORT}/ep_audit2_{SFX}"
sdk = TortoiseSDK(db_path=None, namespace=None)
sdk._db_uri = os.environ["TORTOISE_DB_URI"]
sdk._proj = None
g = sdk._get_proj().g


def q(c, **p):
    return g.query(c, params=p).result_set


def belief(nid):
    r = q("MATCH (n) WHERE n.id=$id RETURN n.confidence, "
          "coalesce(n.posterior_alpha,0.0), coalesce(n.posterior_beta,0.0)",
          id=nid)
    return r[0] if r else None


def wire_and_run(src, tgt_kind="point"):
    if tgt_kind == "point":
        tgt = sdk.create_point("statement", f"t-{uuid.uuid4().hex[:6]}",
                               status="live")["id"]
    else:
        tgt = sdk.create_object(f"t-{uuid.uuid4().hex[:6]}", "core:goal")["id"]
    op = sdk.create_operator("IMPL", src, [tgt], direction="unidirectional")["id"]
    sdk.compute_confidence(factors=[op], require_calibration=False)
    return tgt, belief(tgt)


print("=" * 96)
print("1. An :Object's `isClosed` written through the DOOR — does the posterior decay?")
print("=" * 96)
obj1 = sdk.create_object(f"o1-{SFX}", "core:goal")["id"]
q("MATCH (n:Object {id:$id}) SET n.posterior_alpha=9.0, n.posterior_beta=1.0, "
  "n.confidence=0.9", id=obj1)
print("  before close:", belief(obj1))
sdk.update_knowledge(obj1, isClosed=True)
print("  after  close:", belief(obj1))
print("  stored shape:", q("MATCH (n:Object {id:$id}) RETURN n.status, "
                         "coalesce(n.isClosed,false), n.pipelineSlot, "
                         "n.posterior_alpha, n.posterior_beta, n.confidence", id=obj1))

print()
print("=" * 96)
print("2. A :Point ended with `retract_point` — decay control (the documented path)")
print("=" * 96)
P = sdk.create_point("statement", f"p1-{SFX}", status="live")["id"]
sdk.set_point_baseline(P, 9.0, 1.0)
print("  before retract:", belief(P))
sdk.retract_point(P)
print("  after  retract:", belief(P))

print()
print("=" * 96)
print("3. Direct-edge endpoints ended on the POSITION axis (Batch 3, Cypher predicate)")
print("=" * 96)
for tag, setter in [
    ("live control", None),
    ("status=superseded", "SET n.status='superseded'"),
    ("isClosed=true", "SET n.isClosed=true"),
    ("pipelineSlot=terminal", "SET n.pipelineSlot='terminal'"),
]:
    # wire WHILE LIVE, then end it — the shape pre-existing data has
    src = sdk.create_point("statement", f"d-{uuid.uuid4().hex[:6]}", status="live")["id"]
    sdk.set_point_baseline(src, 8.0, 2.0)
    tgt = sdk.create_point("statement", f"dt-{uuid.uuid4().hex[:6]}", status="live")["id"]
    sdk.create_direct_edge("IMPL", src, tgt, direction="unidirectional")
    if setter:
        q(f"MATCH (n:Point {{id:$id}}) {setter}", id=src)
    sdk.compute_confidence(factors=[src, tgt], require_calibration=False)
    print(f"  direct edge, wired-then-{tag:<22} -> target {belief(tgt)}")

print()
print("  -- and the DOOR against an already-ended endpoint (operator route) --")
for tag, setter in [("status=superseded", "SET n.status='superseded'"),
                    ("isClosed=true", "SET n.isClosed=true"),
                    ("pipelineSlot=terminal", "SET n.pipelineSlot='terminal'")]:
    src = sdk.create_point("statement", f"e-{uuid.uuid4().hex[:6]}", status="live")["id"]
    sdk.set_point_baseline(src, 8.0, 2.0)
    q(f"MATCH (n:Point {{id:$id}}) {setter}", id=src)
    tgt = sdk.create_point("statement", f"et-{uuid.uuid4().hex[:6]}", status="live")["id"]
    try:
        sdk.create_operator("IMPL", src, [tgt], direction="unidirectional")
        print(f"  create_operator over {tag:<24} WROTE")
    except Exception as exc:
        print(f"  create_operator over {tag:<24} REFUSED {str(exc)[:100]}")

print()
print("=" * 96)
print("4. Per-label participation as an operator INPUT (posterior written on the input?)")
print("=" * 96)
for tag in ("Object", "Subject", "Event"):
    if tag == "Object":
        nid = sdk.create_object(f"i-obj-{SFX}", "core:goal")["id"]
    elif tag == "Subject":
        nid = sdk.create_subject(f"i-sub-{SFX}", "naturalPerson")["id"]
    else:
        nid = sdk.create_event(f"i-evt-{SFX}", "decision")["id"]
    tgt, b = wire_and_run(nid)
    print(f"  {tag:<8} input posterior={belief(nid)}  target={b}")

print()
print("=" * 96)
print("5. Object status='completed' (connector fold word) as an operator INPUT")
print("=" * 96)
C = sdk.create_object(f"c5-{SFX}", "core:workItem")["id"]
q("MATCH (n:Object {id:$id}) SET n.status='completed', n.posterior_alpha=9.0, "
  "n.posterior_beta=1.0, n.confidence=0.9", id=C)
tgt, b = wire_and_run(C)
print("  Object status=completed   ->", b,
      " (participated)" if b and b[1] not in (None, 0.0) else " (dropped)")

print()
print("=" * 96)
print("6. Object as a TARGET — is a posterior written onto the Object?")
print("=" * 96)
srcP = sdk.create_point("statement", f"s6-{SFX}", status="live")["id"]
sdk.set_point_baseline(srcP, 9.0, 1.0)
o_tgt, b = wire_and_run(srcP, tgt_kind="object")
print("  Object target posterior:", b)
print("  Object shape:", q("MATCH (n:Object {id:$id}) RETURN labels(n), n.confidence, "
                        "n.posterior_alpha, n.posterior_beta, n.ep_alpha, n.ep_beta",
                        id=o_tgt))

print()
print("=" * 96)
print("7. Operator node shape, per endpoint label")
print("=" * 96)
for tag in ("Object", "Event", "Subject"):
    if tag == "Object":
        nid = sdk.create_object(f"7o-{SFX}", "core:goal")["id"]
    elif tag == "Subject":
        nid = sdk.create_subject(f"7s-{SFX}", "naturalPerson")["id"]
    else:
        nid = sdk.create_event(f"7e-{SFX}", "decision")["id"]
    tgt = sdk.create_point("statement", f"7t-{uuid.uuid4().hex[:6]}", status="live")["id"]
    op = sdk.create_operator("IMPL", nid, [tgt], direction="unidirectional")["id"]
    print(f"  operator over {tag:<8}", q(
        "MATCH (n:Point {id:$id}) RETURN labels(n), n.is_operator, n.op_type, "
        "n.status", id=op))

print()
print("=" * 96)
print("8. Does an ENTITY ever reach `_hydrate_evidence` (a persisted baseline)?")
print("=" * 96)
try:
    sdk.set_point_baseline(o_tgt, 5.0, 1.0)
except Exception as exc:
    print("  set_point_baseline(Object):", type(exc).__name__, str(exc)[:140])
sdk._evidence = {}
sdk._hydrate_evidence()
print("  hydrated evidence ids:", list(sdk._evidence))

print("\nDONE")
