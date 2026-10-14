#!/usr/bin/env python3
"""S1 census — the mutating-Cypher write surface at HEAD.

Re-produces test-design §9's method (a paren-balanced read of every `.query(`
argument under `tortoise/**/*.py`, taking the FIRST mutating Cypher verb),
and adds the census the §17.1 enforcement does NOT cover: mutating statements
on the raw handle lane (`proj.g.query`, `proj.g._g.query`, `proj.db...`).

Two quantities, each with its population and denominator stated:
  * total mutating `.query(` sites over `tortoise/**/*.py`
  * of those, how many are raw-handle sites (`X.g.query`) — the lane #8389's own
    commit message says is "deliberately NOT closed".
"""
from __future__ import annotations

import collections
import pathlib
import re

MUT = re.compile(r"\b(MERGE|CREATE|SET|DETACH\s+DELETE|DELETE|REMOVE)\b", re.I)


def sites_for(pattern: re.Pattern[str]):
    sites, files, first = 0, set(), collections.Counter()
    raw_handle_sites = 0
    for f in sorted(pathlib.Path("tortoise").rglob("*.py")):
        t = f.read_text(errors="replace")
        for m in re.finditer(r"\.query\(", t):
            i, d = m.end(), 1
            while d and i < len(t):
                d += (t[i] == "(") - (t[i] == ")")
                i += 1
            v = MUT.search(t[m.end():i])
            if v:
                sites += 1
                files.add(str(f))
                first[re.sub(r"\s+", " ", v.group(1)).upper()] += 1
                # raw-handle lane: the receiver text ends in `.g` (proj.g.query,
                # proj.g._g.query is `._g` — count both as the raw lane)
                recv = t[max(0, m.start() - 40):m.start()]
                if re.search(r"\.g$|\.g\._g$", recv.split("\n")[-1].rstrip()):
                    raw_handle_sites += 1
    return sites, files, first, raw_handle_sites


def main() -> None:
    sites, files, first, raw = sites_for(MUT)
    print("population: tortoise/**/*.py")
    print(f"mutating `.query(` sites: {sites}  files: {len(files)}")
    print(f"first-verb tally: {dict(first.most_common())}")
    print(f"raw-handle sites (`.g.query` / `.g._g.query`): {raw} of {sites}")
    # a second, independent read of the raw lane by direct regex
    raw2 = 0
    raw_files = set()
    for f in sorted(pathlib.Path("tortoise").rglob("*.py")):
        t = f.read_text(errors="replace")
        for m in re.finditer(r"\b\w+\.g\.(?:query|_query)\(", t):
            # only count ones with a mutating verb in the argument
            i, d = m.end(), 1
            while d and i < len(t):
                d += (t[i] == "(") - (t[i] == ")")
                i += 1
            if MUT.search(t[m.end():i]):
                raw2 += 1
                raw_files.add(str(f))
    print(f"raw-handle mutating sites (independent regex): {raw2} files: {len(raw_files)}")


if __name__ == "__main__":
    main()
