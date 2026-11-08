"""Re-derive the #7949 bundle's line cites from the tree (issue #11528).

The pins are tree-derived by design ("re-derive the number from the tree before
changing this pin"), so this script uses the TESTS' OWN rule tables as the oracle:
it replays `_bundle_sweep`'s loop, derives the correct (start, end) for every
cite whose current number no longer carries what its rule names, and rewrites
only those. Dry run by default; `--apply` writes.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TEST = REPO / "tests/test_7949_capstone_measure.py"


def load_test():
    spec = importlib.util.spec_from_file_location("capstone_measure_pin", TEST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capstone_measure_pin"] = mod
    spec.loader.exec_module(mod)
    return mod


def derive(mod, rule, source_lines):
    """Every (start, end) the rule ACCEPTS, derived from the tree."""
    _label, _source, _phrases, anchors, kind, end_spec = rule
    n = len(source_lines)
    starts = []
    for i in range(1, n + 1):
        line = source_lines[i - 1]
        if kind in ("def", "method-span"):
            if line.lstrip().startswith(anchors[0]):
                starts.append(i)
        else:
            if anchors[0] in line:
                starts.append(i)
    if not starts and kind == "span":
        for i in range(1, n + 1):
            if any(a in source_lines[i - 1] for a in anchors):
                starts.append(i)
    out = []
    for start in starts:
        ends = [start] if end_spec is None else range(start, n + 1)
        for end in ends:
            if end_spec is not None:
                if end_spec[0] == "exact":
                    if source_lines[end - 1].strip() != end_spec[1]:
                        continue
                elif end_spec[1] not in source_lines[end - 1]:
                    continue
            if not mod._bundle_missing(kind, anchors, source_lines,
                                       start, end, end_spec):
                out.append((start, end))
                if end_spec is None:
                    break
                break
    return out


def plan_bundle(mod, apply):
    source_lines = {name: path.read_text(encoding="utf-8").splitlines()
                    for name, path in mod._BUNDLE_SOURCES.items()}
    edits: dict[Path, list[tuple[int, int, str, str]]] = {}
    problems = []
    for doc_name, doc_path in mod._BUNDLE_DOCS:
        text = doc_path.read_text(encoding="utf-8")
        doc_lines = text.splitlines()
        paragraphs = mod._paragraph_spans(text)
        for match in mod._BUNDLE_CITE.finditer(text):
            paragraph = next((s, e) for s, e in paragraphs
                             if s <= match.start() < e)
            line_no = text.count("\n", 0, match.start()) + 1
            if not mod._bundle_in_scope(doc_name, doc_lines[line_no - 1]):
                continue
            if mod._bundle_historical(text[paragraph[0]:paragraph[1]]):
                continue
            source, start, end = match.group(1), int(match.group(2)), match.group(3)
            rule = mod._bundle_rule(source, text[paragraph[0]:paragraph[1]],
                                    paragraph[0], match.start())
            if rule is None:
                problems.append(f"UNMATCHED {doc_name}:{line_no} {match.group(0)}")
                continue
            label, _src, _ph, anchors, kind, end_spec = rule
            end_i = int(end) if end else None
            missing = mod._bundle_missing(kind, anchors, source_lines[source],
                                          start, end_i, end_spec)
            if not missing:
                continue
            seen = derive(mod, rule, source_lines[source])
            if not seen:
                problems.append(
                    f"NO DERIVATION {doc_name}:{line_no} {match.group(0)} "
                    f"({label})")
                continue
            if len(seen) > 1:
                seen.sort(key=lambda se: abs(se[0] - start))
                problems.append(
                    f"AMBIGUOUS {doc_name}:{line_no} {match.group(0)} ({label}) "
                    f"candidates={seen[:6]}")
            new_start, new_end = seen[0]
            new_start, new_end = seen[0]
            new = (f"{new_start}" if end is None
                   else f"{new_start}-{new_end}")
            old = match.group(0)
            edits.setdefault(doc_path, []).append(
                (match.start(), match.end(), old, new))
            print(f"{doc_name}:{line_no}  {old}  ->  {source}.py:{new}"
                  f"   [{label}]")
    for path, items in edits.items():
        if not apply:
            continue
        text = path.read_text(encoding="utf-8")
        for start, end, _old, new in sorted(items, reverse=True):
            prefix = text[start:end].split(":")[0]
            text = text[:start] + f"{prefix}:{new}" + text[end:]
        path.write_text(text, encoding="utf-8")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
    return edits


def bare_rows(mod):
    """The bare `:NNNN` row table, lifted out of the test's own source."""
    tree = ast.parse(TEST.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == \
                "test_the_bare_continuation_cites_name_what_they_say":
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == "rows"):
                    return ast.literal_eval(node.value)
    raise SystemExit("rows table not found")


def plan_bare(mod, apply):
    rows = bare_rows(mod)
    sources = {name: path.read_text(encoding="utf-8").splitlines()
               for name, path in (*mod._BUNDLE_SOURCES.items(),
                                  *mod._BUNDLE_TOOL_SOURCES.items())}
    docs = dict(mod._BUNDLE_DOCS)
    for row in rows:
        doc_label, phrase, cites = row[0], row[1], row[2]
        source_key = row[3] if len(row) > 3 else "extractor_v2"
        lines = sources[source_key]
        path = docs[doc_label]
        text = path.read_text(encoding="utf-8")
        doc_lines = text.splitlines()
        hits = [i for i, ln in enumerate(doc_lines) if phrase in ln]
        if len(hits) != 1:
            print(f"SKIP {doc_label} phrase {phrase!r} x{len(hits)}")
            continue
        idx = hits[0]
        line = doc_lines[idx]
        new_line = line
        # Resolve the UNAMBIGUOUS anchors first, so an ambiguous one (a token the
        # tree carries many times: `return None`, `if not edus:`, a bare `}`)
        # can be placed by the shift its own row's siblings agree on.
        resolved: list[tuple[int, int]] = []
        deferred: list[tuple[str, object]] = []
        for number, anchor in cites:
            if isinstance(anchor, tuple):
                found = [i + 1 for i, ln in enumerate(lines)
                         if ln.strip() == anchor[1]]
            else:
                found = [i + 1 for i, ln in enumerate(lines) if anchor in ln]
            if len(found) == 1:
                resolved.append((int(number), found[0]))
            else:
                deferred.append((number, (anchor, found)))
        deltas = [new - old for old, new in resolved]
        delta = max(set(deltas), key=deltas.count) if deltas else 76
        for number, payload in deferred:
            anchor, found = payload
            if not found:
                print(f"PROBLEM {source_key}.py anchor {anchor!r} -> NONE")
                continue
            want = int(number) + delta
            pick = min(found, key=lambda n: (abs(n - want), n))
            if abs(pick - want) > 30:
                print(f"PROBLEM {source_key}.py anchor {anchor!r} candidates "
                      f"{found[:8]} vs expected {want}")
                continue
            print(f"  (delta {delta:+d}): {anchor!r} -> {pick}")
            resolved.append((int(number), pick))
        for number, new_number in resolved:
            if new_number == number:
                continue
            for pat in (f":{number}", f"-{number}"):
                if pat in new_line:
                    new_number_s = f":{new_number}" if pat[0] == ":" \
                        else f"-{new_number}"
                    new_line = new_line.replace(pat, new_number_s, 1)
                    print(f"{doc_label}:{idx + 1}  {pat} -> {new_number_s}"
                          f"   [{source_key}.py:{new_number}]")
                    break
            else:
                print(f"PROBLEM {doc_label}:{idx + 1} does not carry {number}")
        if new_line != line and apply:
            doc_lines[idx] = new_line
            path.write_text("\n".join(doc_lines) + (
                "\n" if text.endswith("\n") else ""), encoding="utf-8")


def main():
    apply = "--apply" in sys.argv
    mod = load_test()
    print("=== file:line cites (bundle sweep) ===")
    plan_bundle(mod, apply)
    print("\n=== bare `:NNNN` continuations ===")
    plan_bare(mod, apply)
    print("\napplied" if apply else "\nDRY RUN — pass --apply to write")


if __name__ == "__main__":
    main()
