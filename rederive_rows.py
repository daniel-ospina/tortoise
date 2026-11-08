"""Update the #7949 bare-cite PIN ROWS to the numbers the tree now carries.

The rows are the DECLARATION half of `test_the_bare_continuation_cites_name_what_
they_say`: the doc must carry the number AND the number must carry the anchor.
So a re-derivation moves both. Numbers are resolved by the row's own anchor,
with the siblings' shift used for an anchor the tree carries many times.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TEST = REPO / "tests/test_7949_capstone_measure.py"


def load():
    spec = importlib.util.spec_from_file_location("capstone_measure_pin2", TEST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capstone_measure_pin2"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    apply = "--apply" in sys.argv
    mod = load()
    src = TEST.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "test_the_bare_continuation_cites_name_what_they_say")
    rows_node = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                     and isinstance(n.targets[0], ast.Name)
                     and n.targets[0].id == "rows")
    rows = ast.literal_eval(rows_node.value)
    sources = {name: p.read_text(encoding="utf-8").splitlines()
               for name, p in (*mod._BUNDLE_SOURCES.items(),
                               *mod._BUNDLE_TOOL_SOURCES.items())}
    pairs: list[tuple[str, str]] = []
    for row in rows:
        cites = row[2]
        source_key = row[3] if len(row) > 3 else "extractor_v2"
        lines = sources[source_key]
        exact, deferred = [], []
        for number, anchor in cites:
            if isinstance(anchor, tuple):
                found = [i + 1 for i, ln in enumerate(lines)
                         if ln.strip() == anchor[1]]
            else:
                found = [i + 1 for i, ln in enumerate(lines) if anchor in ln]
            if len(found) == 1:
                exact.append((int(number), found[0]))
            else:
                deferred.append((int(number), anchor, found))
        deltas = [n - o for o, n in exact]
        delta = max(set(deltas), key=deltas.count) if deltas else 76
        for number, anchor, found in deferred:
            want = number + delta
            pick = min(found, key=lambda n: (abs(n - want), n)) if found else None
            if pick is None or abs(pick - want) > 30:
                print(f"UNRESOLVED {anchor!r} {found[:6]} want {want}")
                continue
            exact.append((number, pick))
        for old, new in sorted(exact):
            if old != new:
                pairs.append((f'("{old}"', f'("{new}"'))
                print(f"{row[0]}: {old} -> {new}")
    region_start = src.index("rows = (", rows_node.lineno and 0)
    region_end = src.index("\n    )", region_start)
    body = src[region_start:region_end]
    for old, new in pairs:
        if body.count(old) < 1:
            print(f"SKIP (not in rows region): {old}")
            continue
        body = body.replace(old, new, 1)
    out = src[:region_start] + body + src[region_end:]
    if apply:
        TEST.write_text(out, encoding="utf-8")
        print("applied")
    else:
        print("DRY RUN — pass --apply")


if __name__ == "__main__":
    main()
