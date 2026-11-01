"""All remaining #8607 measurements from the committed artifacts."""
import json, sys, hashlib
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tortoise.legacy_ids import census, is_canonical, shape
from tortoise.cache_projection import KEY_PROPERTY

before = json.load(open("dump.json"))
after  = json.load(open("normalised.json"))
plan   = json.load(open("plan-A.json"))

# ── 1. per-label node counts, before/after ────────────────────────────────
def per_label(d):
    c = Counter()
    for n in d["nodes"]:
        for l in n["labels"]:
            c[l] += 1
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
      f"{sum(1 for r in ren if len(by_key.get((r['label'], r['new']), [])) == 1)}/{len(ren)}")

# ── 5. leftover legacy shapes after the pass ──────────────────────────────
print("\n=== legacy shapes remaining AFTER ===")
res = Counter()
for n in after["nodes"]:
    s = shape(n["props"].get("id") or n["props"].get("eventId") or "")
    if s != "canonical":
        res[(n["labels"][0], s)] += 1
print(f"  {dict(res)}")
