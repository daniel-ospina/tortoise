import json, hashlib
orig = json.load(open("dump.json"))
rest = json.load(open("restored.json"))
def sig(d):
    rows = sorted((n["dump_id"], tuple(sorted(n["labels"])),
                   tuple(sorted((k, json.dumps(v, sort_keys=True)) for k, v in n["props"].items())))
                  for n in d["nodes"])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
a, b = sig(orig), sig(rest)
print("original node signature:", a)
print("restored node signature:", b)
print("ROUND-TRIP EXACT (every dump_id, label and prop value):", a == b)
from tortoise.legacy_ids import _edge_digest
print("edge COUNTS orig/rest:", orig["edge_count"], rest["edge_count"],
      "| edge DIGESTS identical:", _edge_digest(orig["edges"]) == _edge_digest(rest["edges"]))
print("node_count:", orig["node_count"], "->", rest["node_count"])
