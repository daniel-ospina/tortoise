#!/usr/bin/env python3
"""Apply the measured re-derivation: pin/table edits in the test, token edits in the documents."""
import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TEST = REPO / "tests/test_12184_shifted_cites.py"
DRY = "--dry" in sys.argv

spec = importlib.util.spec_from_file_location("t12184", TEST)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

_cache = {}


def lines_of(rel):
    if rel not in _cache:
        _cache[rel] = (REPO / rel).read_text(encoding="utf-8").split("\n")
    return _cache[rel]


HINTS = {
    # (old line, token) -> measured line, where the nearest-candidate rule is not enough
    (37534, "`:37434` in `_file_human_approval`"): 37589,
    (20094, "sdk.py:20094"): 20149,
}

def _resolve(tgt, line, frag):
    ls = lines_of(tgt)
    if 0 < line <= len(ls) and frag in ls[line - 1]:
        return line, "ok", []
    c = [i + 1 for i, l in enumerate(ls) if frag in l]
    if len(c) == 1:
        return c[0], "moved", c
    if not c:
        return None, "gone", c
    return min(c, key=lambda x: abs(x - line)), "picked", c


# rows: (kind, doc_rel, token, target_rel, old_line)
rows = []
for doc, token, tgt, line, frag in m._PINS:
    new, state, cands = _resolve(tgt, line, frag)
    if (line, token) in HINTS:
        new = HINTS[(line, token)]
    rows.append(("pins", doc, token, tgt, line, frag, new))
for token, tgt, line, frag in m._MAIN_SET_MISSED:
    new, state, cands = _resolve(tgt, line, frag)
    rows.append(("main_set", None, token, tgt, line, frag, new))
for token, tgt, line, frag, exp in m._REDEFINED_13854:
    new, state, cands = _resolve(tgt, line, frag)
    if (line, token) in HINTS:
        new = HINTS[(line, token)]
    rows.append(("redef", None, token, tgt, line, frag, new))


def shift_token(token: str, old: int, new: int) -> str:
    """Move every cite number in *token* by new-anchor (ranges move together; insertions only).

    The anchor is the number the row's LINE names — or, where the document wrote a token that
    disagreed with the line (the `:37434`/37534 defect), the number nearest it.
    """
    if old == new:
        return token
    nums = [int(x) for x in re.findall(r"\d+", token)]
    if not nums:
        return token
    lo, hi = min(nums), max(nums)
    anchor = old if lo <= old <= hi else min(nums, key=lambda x: abs(x - old))
    d = new - anchor
    return re.sub(r"\d+", lambda mm: str(int(mm.group(0)) + d), token)


# ---- token replacement map (old -> new), only where the number moved
token_map = {}
line_map = {}
for kind, doc, token, tgt, old, frag, new in rows:
    if new is None:
        print(f"!! unresolved {kind} {token!r} old={old}")
        continue
    token_map.setdefault(token, set()).add(shift_token(token, old, new))
    line_map.setdefault((kind, token, old), set()).add(new)
bad = {k: v for k, v in token_map.items() if len(v) > 1}
if bad:
    sys.exit(f"conflicting token rewrites: {bad}")

token_map = {k: next(iter(v)) for k, v in token_map.items() if next(iter(v)) != k}
print(f"{len(token_map)} distinct tokens to re-point")

# ---- edit the test file by AST offsets
src = TEST.read_text()
tree = ast.parse(src)
line_starts = [0]
for ln in src.split("\n"):
    line_starts.append(line_starts[-1] + len(ln) + 1)


def offset(lineno, col):
    # ast columns are UTF-8 byte offsets
    base = line_starts[lineno - 1]
    prefix = src[base:].split("\n")[0].encode("utf-8")[:col]
    return base + len(prefix.decode("utf-8"))


def segment(node):
    return src[offset(node.lineno, node.col_offset):offset(node.end_lineno, node.end_col_offset)]


edits = []


def repl_str(node, new):
    if node.value == new:
        return
    seg = segment(node)
    q = seg[0]
    assert q in "'\"", seg
    body = new.replace("\\", "\\\\").replace(q, "\\" + q)
    edits.append((offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset), f"{q}{body}{q}"))


def repl_int(node, new):
    if node.value == new:
        return
    edits.append((offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset), str(new)))


want = {}
for kind, doc, token, tgt, old, frag, new in rows:
    if new is not None:
        want.setdefault((kind, token, old), new)

for node in tree.body:
    if not isinstance(node, ast.AnnAssign) and not isinstance(node, ast.Assign):
        continue
    tgt_node = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
    name = getattr(tgt_node, "id", None)
    if name not in ("_PINS", "_MAIN_SET_MISSED", "_REDEFINED_13854", "_TOKEN_OCCURRENCES"):
        continue
    value = node.value
    if name == "_TOKEN_OCCURRENCES":
        for k, v in zip(value.keys, value.values):
            tok = k.elts[1]
            if isinstance(tok, ast.Constant) and tok.value in token_map:
                repl_str(tok, token_map[tok.value])
        continue
    for row_node in value.elts:
        elts = row_node.elts
        tok = elts[0] if name in ("_MAIN_SET_MISSED", "_REDEFINED_13854") else elts[1]
        ln = elts[2] if name in ("_MAIN_SET_MISSED", "_REDEFINED_13854") else elts[3]
        kind = {"_PINS": "pins", "_MAIN_SET_MISSED": "main_set", "_REDEFINED_13854": "redef"}[name]
        old = ln.value
        if (kind, tok.value, old) in want:
            repl_int(ln, want[(kind, tok.value, old)])
        if tok.value in token_map:
            repl_str(tok, token_map[tok.value])

# the `five` literal in test_the_five_header_siblings_already_carry_their_pair_from_section_2_5
five_map = {31496: 31551, 31478: 31533, 24458: 24513, 24802: 24857, 24571: 24626}
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "five":
        for row_node in node.value.elts:
            n = row_node.elts[1]
            if n.value in five_map:
                repl_int(n, five_map[n.value])

edits.sort()
out = src
for s, e, txt in reversed(edits):
    out = out[:s] + txt + out[e:]
print(f"{len(edits)} edits in {TEST.name}")
if not DRY:
    TEST.write_text(out)

# ---- edit the documents: token string replacement
for doc in sorted({r[1] for r in rows if r[1]}):
    text = (REPO / doc).read_text(encoding="utf-8")
    n = 0
    for old_tok, new_tok in token_map.items():
        c = text.count(old_tok)
        if c:
            text = text.replace(old_tok, new_tok)
            n += c
    print(f"{doc}: {n} token occurrences re-pointed")
    if not DRY:
        (REPO / doc).write_text(text)
