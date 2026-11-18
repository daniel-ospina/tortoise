#!/usr/bin/env python3
"""The #12901 final-batch privatisation sweep — one map, one rewrite, one verifier.

WHY THIS IS A TOOL AND NOT A PATCH
----------------------------------
The transformation is mechanical and it is LARGE: 66 off-surface methods leave the public
`TortoiseSDK` surface, and a receiver-typed walk of this repo finds ~6.8k references to
them. A hand-edited patch of that size cannot be reviewed and cannot be re-verified; the
one thing a reviewer needs — *"how many references are left?"* — would have to be
re-derived by whoever reads it next. So the map is derived here (never typed), the rewrite
is performed here, and `--check` answers the review question from the same walk that
`--apply` used. A second implementation would be a second answer.

MECHANISM: PRIVATISATION, NEVER DELETION
----------------------------------------
An off-surface method becomes `_name`; the body stays, so the engine keeps every
capability. Two prior attempts died on the same hazard, which `--plan` now refuses in
advance: **`_name` may already exist on the class**, and a naive rename then silently
SHADOWS it (Python keeps the later `def`) — `delete_entity` became infinite self-recursion
and `audit` swapped bodies with the audit-event logger. Such a name takes `_name__svc`
instead, and its call sites are re-pointed to that spelling.

SWEEPING BY RECEIVER EXPRESSION, NOT BY PARENTHESIS
---------------------------------------------------
`x.method(` is only one of the shapes a reference takes. The walk below matches the
`Attribute` NODE, so it also reaches `asyncio.to_thread(sdk.create_object, …)`,
`_safe(sdk.method, …)`, `self._search_fn = self.tortoise_fts_query`,
`TortoiseSDK.create_direct_edge` and `sdk["x"].method` — all of which a parenthesis-shaped
search misses.

WHICH RECEIVERS ARE SWEPT
-------------------------
Only receivers this walk can TYPED as a `TortoiseSDK`:
  * inside `tortoise/sdk.py`, `self.<name>` is the class's own method — unambiguous;
  * elsewhere, a local bound by `X = TortoiseSDK(...)` in the same file, `TortoiseSDK`
    itself, and a module-qualified `mod.TortoiseSDK`.
That is deliberate: `g.query(…)` on a FalkorDB handle and `proj.create_point(…)` on the
projection are NOT this surface, and rewriting them would be the false-positive class that
makes a mechanical sweep dangerous. References on receivers that cannot be typed are
REPORTED (`--check` prints them) rather than guessed at, so the residue is visible.

USAGE
    uv run python tools/sdk_privatise.py --plan     # the classification + collision audit
    uv run python tools/sdk_privatise.py --apply    # rewrite
    uv run python tools/sdk_privatise.py --check    # exit 1 while any typed ref survives
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/sdk_privatise.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SDK_SRC = ROOT / "tortoise" / "sdk.py"
SDK_MODULE = "tortoise/sdk.py"


def approved_methods() -> set[str]:
    """The owner-approved set, read from the declaration — never re-typed here."""
    from tortoise.__all__ import __all__ as approved

    return set(approved) - {"Tortoise"}


def class_body() -> ast.ClassDef:
    tree = ast.parse(SDK_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return node
    raise SystemExit("no top-level `TortoiseSDK` class in tortoise/sdk.py")


def class_bindings() -> set[str]:
    """EVERY name bound in the class body — the collision audit's input.

    Any binding kind counts, not just `def`: a `@property`, a class-level assignment or a
    nested class called `_query` collides with a `_query` method just as hard, and
    checking only functions would let exactly that through.
    """
    out: set[str] = set()
    for n in class_body().body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def instance_attrs() -> set[str]:
    """`self._x = …` anywhere in the module — an instance attribute shadows a method too."""
    src = SDK_SRC.read_text(encoding="utf-8")
    return set(re.findall(r"self\.(_[A-Za-z_][A-Za-z0-9_]*)\s*=", src))


def public_methods() -> list[str]:
    return [
        n.name
        for n in class_body().body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    ]


@dataclass(frozen=True)
class Entry:
    name: str
    private: str
    collided: bool


def rename_map() -> list[Entry]:
    """Off-surface name -> its private spelling, with the collision rule applied."""
    appr = approved_methods()
    bound = class_bindings() | instance_attrs()
    out: list[Entry] = []
    for name in sorted(p for p in public_methods() if p not in appr):
        target = f"_{name}"
        collided = target in bound
        if collided:
            target = f"_{name}__svc"
            if target in bound:
                raise SystemExit(
                    f"{name}: `_name` AND `_name__svc` are both already bound — the "
                    f"collision rule cannot express this rename"
                )
        out.append(Entry(name, target, collided))
    return out


def py_files() -> list[Path]:
    """Tracked + untracked, non-ignored Python files. Bounded: archive/ is reference only."""
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "*.py"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    out = []
    for f in tracked + untracked:
        if f.startswith("archive/") or f.startswith(".venv/") or f.startswith(".worktrees/"):
            continue
        p = ROOT / f
        if p.is_file():
            out.append(p)
    return sorted(set(out))


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        fn = node.func
        return fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
    return ""


def sdk_bound_names(tree: ast.Module) -> set[str]:
    """Local names this file binds from an SDK-like constructor or fixture.

    Two sources, both evidence from THIS file rather than a guess:
      * `X = TortoiseSDK(...)`, and `X = <an SDK stand-in>(...)`;
      * a `@pytest.fixture` whose body yields a `TortoiseSDK(...)` — `lane` and
        `sdk_factory` are the fleet's own spellings for that, and a fixture's name is
        the receiver its consumers use, so typing it here is what reaches them.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if "sdk" in _call_name(node.value).lower():
                out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
            if "sdk" in _call_name(node.value).lower() and isinstance(node.target, ast.Name):
                out.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_fixture = any(
                (getattr(d, "attr", None) or getattr(d, "id", None)) == "fixture"
                for d in node.decorator_list
            )
            if is_fixture and "TortoiseSDK(" in ast.unparse(node):
                out.add(node.name)
    return out


def stub_classes(tree: ast.Module, rmap: dict[str, str]) -> set[str]:
    """Classes that STAND IN for the SDK — they define 3+ of the off-surface methods.

    Their bodies are part of this surface in every way that matters: production code
    reaches them through the same attribute names, so if `create_point` becomes
    `_create_point` the stand-in must answer to that spelling or the door that calls it
    breaks. The threshold of 3 keeps an incidental same-named method out.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        hits = sum(
            1 for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in rmap
        )
        if hits >= 3:
            out.add(node.name)
    return out


def receiver_is_sdk(recv: ast.expr, bound: set[str], in_sdk_module: bool,
                    in_class: bool) -> bool:
    """Is this receiver something that carries the SDK surface?

    Every arm is EVIDENCE, never a guess about what a name probably means — that is the
    difference between a sweep and a search-and-replace:
      * `TortoiseSDK` / `mod.TortoiseSDK` — the class itself;
      * an identifier containing `sdk` — `sdk`, `sdk2`, `org_sdk`, `_get_org_sdk`;
      * a name this file binds from such a constructor or fixture (`bound`);
      * a call to such a factory, e.g. `_get_org_sdk()`;
      * `self.<name>` inside the `TortoiseSDK` class body — its own methods.
    """
    if isinstance(recv, ast.Name):
        # `self` INSIDE the `TortoiseSDK` class body is the one receiver whose type needs
        # no inference at all. ⛔ It is `self` EXACTLY — not `self.<anything>`: an earlier
        # version typed every `self.x.<name>` too, which renamed `self._dirty_roots.update()`
        # and `(...)`-shaped collection calls into `._update(`.
        if recv.id == "self" and in_sdk_module and in_class:
            return True
        return recv.id == "TortoiseSDK" or "sdk" in recv.id.lower() or recv.id in bound
    if isinstance(recv, ast.Attribute):
        if recv.attr == "TortoiseSDK" or "sdk" in recv.attr.lower():
            return True
        if isinstance(recv.value, ast.Name) and recv.attr in bound:
            return True
        if isinstance(recv.value, ast.Call) and "sdk" in _call_name(recv.value).lower():
            return True
        return False
    if isinstance(recv, ast.Call):
        return "sdk" in _call_name(recv).lower() or _call_name(recv) in bound
    if isinstance(recv, ast.Constant):
        return False
    return False


def _class_ranges(tree: ast.Module) -> list[tuple[int, int, str]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out.append((node.lineno, node.end_lineno or node.lineno, node.name))
    return out


def _in_tortoise_class(line: int, tree: ast.Module) -> bool:
    return any(lo <= line <= hi and nm == "TortoiseSDK"
               for lo, hi, nm in _class_ranges(tree))


def edits_for(path: Path, rmap: dict[str, str]) -> tuple[list[tuple[int, int, int, str]], Counter]:
    """(line, start_col, end_col, replacement) attribute rewrites for one file."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], Counter({"<<syntax-error>>": 1})
    in_sdk = path.resolve() == SDK_SRC.resolve()
    bound = sdk_bound_names(tree) | stub_classes(tree, rmap)
    lines = src.splitlines()
    edits: list[tuple[int, int, int, str]] = []
    residue: Counter = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in rmap:
            continue
        in_class = in_sdk and _in_tortoise_class(node.lineno, tree)
        # `super().<name>()` reaches the parent's method: typed only where the parent
        # IS the SDK (a subclass of `TortoiseSDK`), never for an unrelated base.
        if isinstance(node.value, ast.Call) and _call_name(node.value) == "super":
            if not in_class:
                residue[node.attr] += 1
                continue
        elif not receiver_is_sdk(node.value, bound, in_sdk, in_class):
            residue[node.attr] += 1
            continue
        if node.value.end_lineno != node.end_lineno:
            residue[f"{node.attr} (multiline receiver)"] += 1
            continue
        start = node.value.end_col_offset  # `end_col_offset` is EXCLUSIVE: this is the '.'
        end = node.end_col_offset
        if lines[node.lineno - 1][start:end] != "." + node.attr:
            residue[f"{node.attr} (unexpected span)"] += 1
            continue
        edits.append((node.lineno, start, end, "." + rmap[node.attr]))
    return edits, residue


def _def_name_span(path: Path, node: ast.AST, name: str) -> tuple[int, int] | None:
    """The (start, end) columns of the NAME in `def <name>(`, from the source line.

    ⛔ NOT `col_offset + 4`. For an `AsyncFunctionDef`, `col_offset` points at `async`,
    so an offset arithmetic lands inside `async` and writes `asyn_deletedelete` — which
    is a syntax error the sweep then cannot see, because the file no longer parses.
    The `def` keyword is located in the line instead, so both forms work.
    """
    line = path.read_text(encoding="utf-8").splitlines()[node.lineno - 1]
    m = re.search(rf"\bdef\s+({re.escape(name)})\b", line[node.col_offset:])
    if not m:
        return None
    start = node.col_offset + m.start(1)
    return start, start + len(name)


def def_edits(rmap: dict[str, str]) -> list[tuple[int, int, int, str]]:
    """`def <name>(` -> `def <private>(` on the class."""
    out = []
    for n in class_body().body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in rmap:
            span = _def_name_span(SDK_SRC, n, n.name)
            if span is None:
                raise SystemExit(f"cannot locate the name span for `def {n.name}` at "
                                 f"tortoise/sdk.py:{n.lineno}")
            out.append((n.lineno, span[0], span[1], rmap[n.name]))
    return out


def stub_def_edits(path: Path, rmap: dict[str, str]) -> list[tuple[int, int, int, str]]:
    """Rename the same-named methods on the SDK STAND-IN classes this file defines.

    ⛔ ONLY stand-ins — a class that defines 3+ of the off-surface names. A graph client's
    `query`, a store's `update` and a mock HTTP client's `delete` are the same WORDS and
    a different contract: their call sites are `g.query` / `tc.delete`, which this sweep
    correctly leaves alone, so renaming their `def` breaks them. Measured before this
    guard existed: 1,526 `def` lines were renamed, and a mock's `async def delete(`
    became `asyn_deletedelete(` — unparseable.
    """
    if path.resolve() == SDK_SRC.resolve():
        return []
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    stand_ins = stub_classes(tree, rmap)
    out = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef) or cls.name not in stand_ins:
            continue
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in rmap:
                span = _def_name_span(path, n, n.name)
                if span is None:
                    continue
                out.append((n.lineno, span[0], span[1], rmap[n.name]))
    return out


def apply_edits(path: Path, edits: list[tuple[int, int, int, str]]) -> int:
    if not edits:
        return 0
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    by_line: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for ln, s, e, new in edits:
        by_line[ln].append((s, e, new))
    for ln, items in by_line.items():
        text = lines[ln - 1]
        for s, e, new in sorted(items, key=lambda i: -i[0]):
            text = text[:s] + new + text[e:]
        lines[ln - 1] = text
    path.write_text("".join(lines), encoding="utf-8")
    return len(edits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true", help="report the map and collisions")
    ap.add_argument("--apply", action="store_true", help="perform the rewrite")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 while any TYPED reference to a now-private name survives")
    args = ap.parse_args()
    if not (args.plan or args.apply or args.check):
        ap.print_help()
        return 2

    entries = rename_map()
    rmap = {e.name: e.private for e in entries}

    if args.plan:
        print(f"{len(entries)} off-surface methods on TortoiseSDK\n")
        for i, e in enumerate(entries, 1):
            mark = f"  ⚠️ COLLISION -> {e.private}" if e.collided else ""
            print(f"{i:2d}. {e.name:26s} -> {e.private}{mark}")
        print(f"\ncollisions: {sum(1 for e in entries if e.collided)}")

    total = 0
    residue_all: Counter = Counter()
    for p in py_files():
        edits, residue = edits_for(p, rmap)
        residue_all.update(residue)
        if p.resolve() == SDK_SRC.resolve():
            edits = edits + def_edits(rmap)
        else:
            edits = edits + stub_def_edits(p, rmap)
        if args.apply:
            n = apply_edits(p, edits)
            if n:
                print(f"  rewrote {n:5d} reference(s) in {p.relative_to(ROOT)}")
            total += n
    if args.apply:
        print(f"\nTOTAL rewrites: {total}")

    if args.check:
        after_total = 0
        after_residue: Counter = Counter()
        for p in py_files():
            e2, r2 = edits_for(p, rmap)
            after_total += len(e2)
            after_residue.update(r2)
        print(f"typed references still pointing at a private name: {after_total}")
        print(f"untyped receivers NOT swept (deliberate, reported): {sum(after_residue.values())}")
        for name, cnt in after_residue.most_common(25):
            print(f"  {cnt:5d}  {name}")
        return 1 if after_total else 0

    # `--plan` / `--apply` report the untyped residue too: it is the sweep's visible edge.
    print(f"\nuntyped receivers left alone: {sum(residue_all.values())}")
    for name, cnt in residue_all.most_common(15):
        print(f"  {cnt:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
