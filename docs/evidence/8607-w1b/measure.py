"""All remaining #8607 measurements from the committed artifacts."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root: docs/evidence/8607-w1b/<this>
from tortoise.cache_projection import KEY_PROPERTY
from tortoise.legacy_ids import LEGACY, census, is_canonical, shape


def _load(name):
    with open(name) as fh:
        return json.load(fh)


before = _load("dump.json")
after = _load("normalised.json")
plan = _load("plan.json")

# ── 1. per-label node counts, before/after ────────────────────────────────
def per_label(d):
    c = Counter()
    for n in d["nodes"]:
        for label in n["labels"]:
            c[label] += 1
    return c
lb, la = per_label(before), per_label(after)
print("=== per-label node counts (before → after) ===")
for k in sorted(set(lb) | set(la)):
    delta = la[k] - lb[k]
    flag = "" if delta == 0 else f"  ⛔ DELTA {delta:+d}"
    print(f"  {k:<10} {lb[k]:>6} → {la[k]:>6}{flag}")
print(f"  {'TOTAL':<10} {len(before['nodes']):>6} → {len(after['nodes']):>6}"
      f"   node_count field {before['node_count']} → {after['node_count']}")

# ── 2. census before / after ──────────────────────────────────────────────
cb, ca = census(before), census(after)
print("\n=== census (contract predicate, per label) ===")
for tag, c in (("before", cb), ("after", ca)):
    print(f"  {tag}: {c.by_label}")
    print(f"          legacy_shapes={c.legacy_shapes} canonical_shapes={c.canonical_shapes}")

# ── 3. injectivity of the 1,000-node mapping ──────────────────────────────
ren = plan["renames"]
olds, news = [r["old"] for r in ren], [r["new"] for r in ren]
dump_ids = [r["dump_id"] for r in ren]
existing = {n["props"].get(KEY_PROPERTY.get(n["labels"][0], "id"))
            for n in before["nodes"]}
print("\n=== injectivity ===")
print(f"  renames            : {len(ren)}")
print(f"  distinct old keys  : {len(set(olds))}  (dupes: {len(olds)-len(set(olds))})")
print(f"  distinct new keys  : {len(set(news))}  (collisions: {len(news)-len(set(news))})")
print(f"  distinct dump_ids  : {len(set(dump_ids))}")
print(f"  new ∩ pre-existing : {len(set(news) & existing)}")
print(f"  OLD still addressable after pass: {sum(1 for o in set(olds) if o in existing)} (pre-pass, by definition)")
after_keys = {n["props"].get(KEY_PROPERTY.get(n['labels'][0], 'id')) for n in after["nodes"]}
print(f"  OLD still addressable in AFTER: {sum(1 for o in set(olds) if o in after_keys)}")
print(f"  all new canonical : {all(is_canonical(x) for x in news)}")

# ── 4. resolution sample: resolve by NEW (label, key) -> same node ────────
by_dump_id_after = {n["dump_id"]: n for n in after["nodes"]}
by_key = {}
for n in after["nodes"]:
    label = n["labels"][0]
    kp = KEY_PROPERTY.get(label)
    if kp and kp in n["props"]:
        by_key.setdefault((label, n["props"][kp]), []).append(n)
print("\n=== resolution sample (20 migrated nodes, by NEW (label,key)) ===")
sample = ren[:20]
ok = 0
for r in sample:
    hit = by_key.get((r["label"], r["new"]), [])
    node = by_dump_id_after[r["dump_id"]]
    same = (len(hit) == 1 and hit[0]["dump_id"] == r["dump_id"])
    ok += same
    if r is sample[0] or not same:
        print(f"  ({r['label']}, {r['new']}) -> dump_id {hit[0]['dump_id'] if hit else None}"
              f" (expected {r['dump_id']}) {'OK' if same else 'MISMATCH'}")
print(f"  resolved uniquely to the same node: {ok}/{len(sample)}")
print(f"  coverage of the whole rename set : "
      f"{sum(1 for r in ren if (h := by_key.get((r['label'], r['new']), [])) and len(h) == 1 and h[0]['dump_id'] == r['dump_id'])}/{len(ren)}")

# ── 5. leftover legacy shapes after the pass ──────────────────────────────
print("\n=== legacy shapes remaining AFTER ===")
res = Counter()
for n in after["nodes"]:
    s = shape(n["props"].get("id") or n["props"].get("eventId") or "")
    if s == LEGACY:          # shape() never returns "canonical" — that filter was dead
        res[(n["labels"][0], s)] += 1
print(f"  legacy-shaped (label, shape): {dict(res)}")
print(f"  legacy remaining IN SCOPE (Point): {res.get(('Point', LEGACY), 0)}")
