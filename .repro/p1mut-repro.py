"""Reproductions for #10119 and #10368 on current main. Read-only, throws away graph."""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_TEST_MODE", "1")

from tortoise.sdk import TortoiseSDK  # noqa: E402

PROBE_ID = "no-such-point-9202"


def ep_version(sdk):
    rows = sdk._get_proj().g.query(
        "MATCH (m:EpMeta) RETURN m.ep_version").result_set
    return rows[0][0] if rows else None


def point_revised_rows(sdk, pid):
    rows = sdk._get_proj().g.query(
        "MATCH (e:GraphEvent) WHERE e.type='PointRevised' "
        "AND e.id=$id RETURN count(e)", params={"id": pid}).result_set
    return rows[0][0] if rows else 0


print("=" * 70)
print("#10119 — update_point on an ABSENT id")
print("=" * 70)
ns = f"test_10119_{uuid.uuid4().hex[:8]}"
sdk = TortoiseSDK(namespace=ns)
try:
    ep_before = ep_version(sdk)
    rows_before = point_revised_rows(sdk, PROBE_ID)
    reply = sdk.update_point(PROBE_ID, confidence=0.5)
    ep_after = ep_version(sdk)
    rows_after = point_revised_rows(sdk, PROBE_ID)
    print(f"  reply                 : {reply!r}")
    print(f"  PointRevised rows     : {rows_before} -> {rows_after}")
    print(f"  :EpMeta.ep_version    : {ep_before} -> {ep_after}")
    print(f"  _dirty_roots          : {sorted(sdk._dirty_roots)!r}")
    # sweep with every REAL id — the absent id must not survive
    real = [r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN n.id").result_set]
    real += [r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Operator) RETURN n.id").result_set]
    sdk._sweep_dirty_roots(set(real))
    print(f"  after _sweep_dirty_roots(all real ids): "
          f"{sorted(sdk._dirty_roots)!r}")
    print(f"  _auto_dream_mode()    : {sdk._auto_dream_mode()!r}")
finally:
    sdk.close()

print()
print("=" * 70)
print("#10368 — create_point(dedup=True) content fold onto a TERMINAL point")
print("=" * 70)
ns = f"test_10368_{uuid.uuid4().hex[:8]}"
sdk = TortoiseSDK(namespace=ns)
try:
    content = "DIRECT-CTX-9819"
    pid = sdk.create_point("statement", content, dedup=True)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)
    print(f"  after retract: status={before.get('status')!r} "
          f"updatedAt={before.get('updatedAt')!r} "
          f"custom_probe={before.get('custom_probe')!r}")
    sdk.create_point("statement", content, dedup=True, custom_probe="x")
    after = sdk.get_point(pid)
    print(f"  after re-create(dedup): status={after.get('status')!r} "
          f"updatedAt={after.get('updatedAt')!r} "
          f"custom_probe={after.get('custom_probe')!r}")
    mutated = (before.get("updatedAt") != after.get("updatedAt")
               or "custom_probe" in after)
    print(f"  MUTATED: {mutated}")
    # Also: does a generic prop rewrite a SUPERSEDED point?
    p2 = sdk.create_point("statement", "SUP-CTX", dedup=True)["id"]
    p3 = sdk.create_point("statement", "SUP-NEW", dedup=True)["id"]
    sdk.supersede_point(p2, p3)
    b2 = sdk.get_point(p2)
    sdk.create_point("statement", "SUP-CTX", dedup=True, generic_probe="y")
    a2 = sdk.get_point(p2)
    print(f"  superseded point re-fold: updatedAt {b2.get('updatedAt')} -> "
          f"{a2.get('updatedAt')}; status={a2.get('status')!r}; "
          f"generic_probe={a2.get('generic_probe')!r}")
finally:
    sdk.close()
