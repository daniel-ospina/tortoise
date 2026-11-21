#!/usr/bin/env python3
"""Re-measure the audit header's drift numerator with the header's own rule.

For every unique `(resolved path, start line)` pair the document writes as ONE token, compare
`git show <pin>:<path> | sed -n '<N>p'` with `git show <main>:<path> | sed -n '<N>p'` text for text.
A pair is COMPARABLE when both blobs have that line, and a DRIFTER when the text differs.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
PIN = "ccb49741b2ec"
MAIN = "ae54df166d"

spec = importlib.util.spec_from_file_location("t", REPO / "tests/test_12184_shifted_cites.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

text = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else (REPO / "docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md").read_text()
spellings, pairs = m._resolved_pairs(text)
blobs = {}


def line_at(rev, path, n):
    key = (rev, path)
    if key not in blobs:
        r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
        blobs[key] = r.stdout.split("\n") if r.returncode == 0 else None
    ls = blobs[key]
    if ls is None or not 0 < n <= len(ls):
        return None
    return ls[n - 1].rstrip()


comparable = drifters = incomparable = 0
drifted = []
for path, n in sorted(pairs):
    a = line_at(PIN, path, n)
    b = line_at(MAIN, path, n)
    if a is None or b is None:
        incomparable += 1
        continue
    comparable += 1
    if a != b:
        drifters += 1
        drifted.append((path, n))
print(f"spellings={spellings} pairs={len(pairs)} collisions={spellings-len(pairs)} "
      f"comparable={comparable} drifters={drifters} incomparable={incomparable}")
if "-v" in sys.argv:
    for p in drifted:
        print("  ", p)
