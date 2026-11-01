import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root: docs/evidence/8607-w1b/<this>

from tortoise.legacy_ids import _edge_digest


def _load(name):
    with open(name) as fh:
        return json.load(fh)


orig = _load("dump.json")
rest = _load("restored.json")


def sig(d):
    rows = sorted((n["dump_id"], tuple(sorted(n["labels"])),
                   tuple(sorted((k, json.dumps(v, sort_keys=True)) for k, v in n["props"].items())))
                  for n in d["nodes"])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
a, b = sig(orig), sig(rest)
print("original node signature:", a)
print("restored node signature:", b)
print("ROUND-TRIP EXACT (every dump_id, label and prop value):", a == b)

print("edge COUNTS orig/rest:", orig["edge_count"], rest["edge_count"],
      "| edge DIGESTS identical:", _edge_digest(orig["edges"]) == _edge_digest(rest["edges"]))
print("node_count:", orig["node_count"], "->", rest["node_count"])
