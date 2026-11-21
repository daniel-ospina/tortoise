#!/usr/bin/env python3
"""Re-derive the citation pins/addresses of tests/test_12184_shifted_cites.py against THIS tree.

Every row is (document, cite token, target file, 1-based line, verbatim fragment) — the rule the
file's own header states. For each row this measures the line in the target that CARRIES the
fragment (never a delta on the old number) and reports the re-pointing, or the ambiguity.
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TEST = REPO / "tests/test_12184_shifted_cites.py"

spec = importlib.util.spec_from_file_location("t12184", TEST)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

_cache = {}


def lines_of(rel):
    if rel not in _cache:
        _cache[rel] = (REPO / rel).read_text(encoding="utf-8").split("\n")
    return _cache[rel]


def resolve(tgt, line, frag):
    ls = lines_of(tgt)
    if 0 < line <= len(ls) and frag in ls[line - 1]:
        return line, "ok", []
    cands = [i + 1 for i, l in enumerate(ls) if frag in l]
    if len(cands) == 1:
        return cands[0], "moved", cands
    return None, ("ambiguous" if cands else "gone"), cands


rows = []
for doc, token, tgt, line, frag in m._PINS:
    new, state, cands = resolve(tgt, line, frag)
    rows.append(dict(table="_PINS", doc=doc, token=token, tgt=tgt, line=line, frag=frag, new=new, state=state, cands=cands, end=None))
for doc, token, line, frag in m._MAIN_SET_MISSED:
    rows.append(dict(table="_MAIN_SET_MISSED", doc=doc, token=token, tgt=None, line=line, frag=frag, new=None, state="no-target", cands=[], end=None))
for token, tgt, line, frag, expected in m._REDEFINED_13854:
    new, state, cands = resolve(tgt, line, frag)
    rows.append(dict(table="_REDEFINED_13854", doc=None, token=token, tgt=tgt, line=line, frag=frag, new=new, state=state, cands=cands, end=expected))

counts = {}
for r in rows:
    counts[r["state"]] = counts.get(r["state"], 0) + 1
print(f"{len(rows)} rows: {counts}")
for r in rows:
    if r["state"] != "ok":
        print(f"  {r['state']:10} {r['table']:18} {r['token'][:58]!r} {r['line']}->{r['new']} cands={r['cands']} frag={r['frag'][:50]!r}")
