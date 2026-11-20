#!/usr/bin/env python3
"""The #12901 final-batch privatisation sweep — one map, one rewrite, one verifier.

WHY THIS IS A TOOL AND NOT A PATCH
----------------------------------
The transformation is mechanical and it is LARGE: every off-surface method leaves the public
`TortoiseSDK` surface, and a receiver-typed walk of this repo finds thousands of references.
A hand-edited patch of that size cannot be reviewed and cannot be re-verified; the one thing a
reviewer needs — *"how many references are left, and which ones did the sweep refuse to
guess at?"* — would have to be re-derived by whoever reads it next. So the map is derived here
(never typed), the rewrite is performed here, and `--check` answers the review question from
the same walk that `--apply` used. A second implementation would be a second answer.

MECHANISM: PRIVATISATION, NEVER DELETION
----------------------------------------
An off-surface method becomes `_name`; the body stays, so the engine keeps every capability.
Two prior attempts died on the same hazard, which `--plan` refuses in advance: **`_name` may
already exist on the class**, and a naive rename then silently SHADOWS it (Python keeps the
later `def`) — `delete_entity` became infinite self-recursion and `audit` swapped bodies with
the audit-event logger. Such a name takes `_name__svc` instead, and its call sites are
re-pointed to that spelling.

SWEEPING BY RECEIVER EXPRESSION, NOT BY PARENTHESIS
---------------------------------------------------
`x.method(` is only one of the shapes a reference takes. The walk below matches the
`Attribute` NODE, so it also reaches `asyncio.to_thread(sdk.create_object, …)`,
`_safe(sdk.method, …)`, `self._search_fn = self.tortoise_fts_query`,
`TortoiseSDK.create_direct_edge` and `sdk["x"].method` — all of which a parenthesis-shaped
search misses. String-shaped references (`getattr(sdk, "query")`, `monkeypatch.setattr(sdk,
"query", …)`, `setattr(cls, "tortoise.sdk.TortoiseSDK.query", …)`) are swept in a second arm.

WHICH RECEIVERS ARE SWEPT — THE HARD PART
-----------------------------------------
47 of the 66 names are ordinary words that OTHER objects also answer to: `query` (a FalkorDB
handle), `update` (a dict), `delete` (an HTTP client), `create_point` (a projection). A
name-scoped rewrite is therefore not a sweep, it is a sabotage: on this tree the 66 names carry
22,651 `Attribute` references and roughly half of them belong to those other objects.

So a receiver is rewritten only when THIS FILE contains evidence that it carries the SDK
surface:
  * `self` inside the `TortoiseSDK` class body (sdk.py), or inside a subclass of it;
  * an identifier whose own name says SDK — `sdk`, `sdk2`, `org_sdk`, `_org_sdk`;
  * a name bound from an SDK constructor or fixture (`X = TortoiseSDK(...)`, `X = _get_sdk()`,
    a `@pytest.fixture` whose returned/yielded value is one), or annotated `TortoiseSDK`;
  * an attribute that says SDK (`self.sdk`, `obj._sdk`, `mod.TortoiseSDK`);
  * a class that STANDS IN for the SDK — it implements at least two of the APPROVED surface
    (a real stand-in does; a FalkorDB/registry/mock-HTTP double implements none), or it
    subclasses `TortoiseSDK`. Its `def` names are renamed with it, because production code
    reaches it through the private spelling.

Everything else is RESIDUE: reported with its receiver expression, never guessed at. The
residue is the sweep's visible edge, and `--check` fails while a typed reference survives so
that "did we finish" is answered by the same walk that did the work.

USAGE
    uv run python tools/sdk_privatise.py --report   # classification, collisions, stand-ins, residue
    uv run python tools/sdk_privatise.py --plan     # the rename map + collision audit
    uv run python tools/sdk_privatise.py --apply    # rewrite
    uv run python tools/sdk_privatise.py --check    # exit 1 while any TYPED ref survives
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
import sys
import warnings
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
COLLISION_SUFFIX = "__svc"

#: The commit the sweep was APPLIED against (#13899 F3). `--check` derives the rename map from
#: THIS revision and not from a moving `merge-base`: once this branch merges, `merge-base HEAD
#: origin/main` resolves to a commit whose class already holds the 39 approved names, so the map
#: would be empty and `--check` would answer "0 references" over an unswept tree — a vacuous
#: PASS, which is the one thing a verifier must never do. Pinned, so the question stays the same
#: question forever; a future sweep re-pins it deliberately.
SWEEP_BASE = "28ca66878d7c617a2f28a8025ddaa40352adfcb2"

#: Non-vacuity of the map: `SWEEP_BASE` holds this many off-surface methods, and a check that
#: derived a different number is answering about a different tree. A count that silently went to
#: zero is the vacuous pass again, one level up.
SWEEP_NAMES = 66

#: The residue the sweep REFUSED to guess at, pinned as `(rows, digest)` over the sorted rows so
#: that a NEW untyped shape — the ordinary-word receivers (`x.query`, `x.update`, `x.delete`) that
#: cannot be typed from evidence — reds `--check` instead of being silently absorbed into a fresh
#: count each review (#13899 F6). Re-pin deliberately with `--repin`, in a commit that says why.
#:
#: RE-PINNED (11776, bfbf6195136edc2c) → (11782, 0f00153d4691e3de) by #13899's fix round, and
#: the move is the intended direction: the 6 `reg.query(` sites in `test_delivery_tenancy.py`,
#: `test_issue_4010_sessions_unlimited.py` and `test_mcp_http.py` were restored from the vendor
#: PRIVATE `reg._query(` to the public `query` (P2 — `reg` is a FalkorDB `Graph`, never the SDK), so
#: the `reg.query` row went 403 → 409 and nothing else moved. A re-pin here is a DECLARATION of a
#: known shape, not a new allowance: adding an SDK-holding receiver to the residue still requires
#: the same deliberate re-pin with its own reason.
SWEEP_RESIDUE = (11782, "0f00153d4691e3de")

#: A name that says "this is the SDK" in its own spelling. Deliberately a WORD match, so
#: `sdk`, `sdk2`, `org_sdk`, `_get_org_sdk` and `sdk_factory` all qualify while `sudoku` —
#: and, importantly, `queue`-shaped accidental substrings — do not.
_SDKISH = re.compile(r"(?i)(?:^|_)sdk\d*(?:_|$)")


def approved_methods() -> set[str]:
    """The owner-approved set, read from the declaration — never re-typed here."""
    from tortoise.__all__ import __all__ as approved

    return set(approved) - {"Tortoise"}


def _sdk_source(rev: str | None = None) -> str:
    """`tortoise/sdk.py` at `rev` (a git revision) or the working tree when `rev` is None."""
    if rev is None:
        return SDK_SRC.read_text(encoding="utf-8")
    out = subprocess.run(
        ["git", "show", f"{rev}:tortoise/sdk.py"], cwd=ROOT, capture_output=True, text=True
    )
    if out.returncode != 0:
        raise SystemExit(f"cannot read tortoise/sdk.py at {rev!r}: {out.stderr.strip()}")
    return out.stdout


def class_body(src: str | None = None) -> ast.ClassDef:
    tree = ast.parse(src if src is not None else self_source())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return node
    raise SystemExit("no top-level `TortoiseSDK` class in tortoise/sdk.py")


def self_source() -> str:
    return SDK_SRC.read_text(encoding="utf-8")


def class_bindings(src: str | None = None) -> dict[str, str]:
    """EVERY name bound in the class body, mapped to its kind.

    Any binding kind counts, not just `def`: a `@property`, a class-level assignment or a
    nested class called `_query` collides with a `_query` method just as hard, and checking
    only functions would let exactly that through.
    """
    out: dict[str, str] = {}
    for node in class_body(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = "def"
        elif isinstance(node, ast.ClassDef):
            out[node.name] = "class"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = "annassign"
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = "assign"
    return out


def off_surface_names(src: str | None = None) -> list[str]:
    """Public methods on `TortoiseSDK` that are NOT on the approved surface."""
    approved = approved_methods()
    public = {
        n.name
        for n in class_body(src).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    }
    return sorted(public - approved)


@dataclass(frozen=True)
class Entry:
    name: str
    private: str
    collided: bool


def rename_map(rev: str | None = None) -> list[Entry]:
    """The map: off-surface name -> the private spelling it takes.

    The collision arm is the whole reason this is derived rather than typed: `audit` and
    `delete_entity` already own `_audit` / `_delete_entity` on this class, and a naive rename
    would shadow them.

    `rev` reads the class at a git revision — the map the sweep was APPLIED with. After
    `--apply` the live class no longer holds the names, so `--check` MUST be given the base
    or it derives an empty map and reports a vacuous zero.
    """
    src = _sdk_source(rev)
    bindings = class_bindings(src)
    out = []
    for name in off_surface_names(src):
        plain = "_" + name
        if plain in bindings:
            out.append(Entry(name, plain + COLLISION_SUFFIX, True))
        else:
            out.append(Entry(name, plain, False))
    return out


def py_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "*.py"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    out = []
    for f in tracked + untracked:
        if f.startswith(("archive/", ".venv/", ".worktrees/")):
            continue
        p = ROOT / f
        if p.is_file():
            out.append(p)
    return sorted(set(out))


def residue_digest(rows: Counter) -> str:
    """A stable digest of the residue SET (row -> count), for the `SWEEP_RESIDUE` pin."""
    text = "\n".join(f"{row}\t{rows[row]}" for row in sorted(rows))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        fn = node.func
        return fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
    return ""


def _annotation_is_sdk(node: ast.expr | None) -> bool:
    if node is None:
        return False
    text = ast.unparse(node)
    return "TortoiseSDK" in text


def _is_sdk_value(value: ast.expr | None) -> bool:
    """Is this expression a value an SDK is built from? Evidence from THIS file only.

    The arm is the CALL's own name — `TortoiseSDK(...)`, `_get_sdk()`, `_registry_sdk()`.
    """
    if not isinstance(value, ast.Call):
        return False
    return _SDKISH.search(_call_name(value)) is not None or _call_name(value) in {
        "TortoiseSDK", "Tortoise",
    }


def _scope_body(node: ast.AST) -> list[ast.AST]:
    """Every node belonging to THIS scope — nested scopes are excluded, they bind their own."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


_SCOPE_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Scope:
    """One lexical scope, and what ITS OWN bindings prove about the names it binds."""

    lo: int
    hi: int
    typed: frozenset[str]
    untyped: frozenset[str]


def _scope_bindings(node: ast.AST) -> tuple[set[str], set[str]]:
    """The (typed, untyped) names this scope's OWN statements bind.

    Every arm is evidence from THIS file, never a guess about what a name probably means:
      * `X = TortoiseSDK(...)` / `X = <a name that says sdk>(...)`;
      * `X: TortoiseSDK = ...`, and a parameter annotated `TortoiseSDK`;
      * `with <sdk-ish>() as X:`.
    """
    typed: set[str] = set()
    untyped: set[str] = set()

    def bind(name: str, is_typed: bool) -> None:
        (typed if is_typed else untyped).add(name)

    for n in _scope_body(node):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    bind(target.id, _is_sdk_value(n.value))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            bind(n.target.id, _annotation_is_sdk(n.annotation) or _is_sdk_value(n.value))
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if isinstance(item.optional_vars, ast.Name):
                    bind(item.optional_vars.id, _is_sdk_value(item.context_expr))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        for arg in (*args.args, *args.kwonlyargs, *args.posonlyargs):
            if _annotation_is_sdk(arg.annotation):
                bind(arg.arg, True)
    # A name bound BOTH ways in ONE scope is ambiguous and is NOT rewritten. This is the
    # `#13899` F2 lesson, measured in `tortoise/hosted_api.py`: `reg` is a real SDK in one
    # function (`reg = _registry_sdk()`) and a FalkorDB handle in the next
    # (`reg = sdk._get_registry()`), and a FILE-WIDE set rewrote 80 graph calls into
    # `reg._query(` — a dependency on a vendor private method. Scope, then ambiguity.
    return typed - untyped, untyped


class Typing:
    """Scope-resolved SDK evidence for a module: which local names carry the surface, WHERE."""

    def __init__(self, scopes: list[Scope]) -> None:
        # Innermost FIRST: the smallest span containing the line decides.
        self._scopes = sorted(scopes, key=lambda s: (s.hi - s.lo, s.lo))
        typed: set[str] = set()
        for s in scopes:
            typed |= s.typed
        self._any_typed = typed

    def typed(self, name: str, line: int) -> bool:
        """Does the NEAREST enclosing scope that binds `name` type it as SDK?"""
        for s in self._scopes:
            if not (s.lo <= line <= s.hi):
                continue
            if name in s.typed:
                return True
            if name in s.untyped:
                return False
        return name in self._any_typed

    def typed_anywhere(self, name: str) -> bool:
        """For a name reached through an ATTRIBUTE (`mod.fixture.method`): the receiver names
        the module, not the scope, so the nearest-scope rule cannot decide it."""
        return name in self._any_typed


def sdk_typing(tree: ast.Module) -> Typing:
    """The scope-resolved typing for one module, fixtures included.

    A `@pytest.fixture` whose RETURN or YIELD is an SDK value types the FIXTURE'S NAME in the
    scope the fixture is defined in — the name its consumers reach it by. ⛔ RETURN/YIELD
    only: an earlier attempt typed a fixture from any mention of `TortoiseSDK(` in its body,
    which typed a FastAPI `TestClient` fixture that merely built an SDK inside itself and
    rewrote `client.delete(` into `client._delete(` — a false positive this arm exists to make
    impossible.
    """
    parents: dict[int, ast.AST] = {}
    scope_nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
        if isinstance(node, _SCOPE_NODES):
            scope_nodes.append(node)
    bindings: dict[int, tuple[set[str], set[str]]] = {
        id(n): _scope_bindings(n) for n in scope_nodes
    }
    for node in scope_nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            (getattr(d, "attr", None) or getattr(d, "id", None)) == "fixture"
            for d in node.decorator_list
        ):
            continue
        own_typed = bindings[id(node)][0]
        if not any(
            isinstance(sub, (ast.Return, ast.Yield))
            and (
                _is_sdk_value(sub.value)
                or (isinstance(sub.value, ast.Name) and sub.value.id in own_typed)
            )
            for sub in _scope_body(node)
        ):
            continue
        owner = node
        while not isinstance(parents.get(id(owner)), _SCOPE_NODES) and id(owner) in parents:
            owner = parents[id(owner)]
        enclosing = parents.get(id(owner))
        if enclosing is None or id(enclosing) not in bindings:
            continue
        typed, untyped = bindings[id(enclosing)]
        typed.add(node.name)
        untyped.discard(node.name)
        bindings[id(enclosing)] = (typed, untyped)
    scopes = []
    for n in scope_nodes:
        lo = 1 if isinstance(n, ast.Module) else n.lineno
        hi = getattr(n, "end_lineno", None) or lo
        typed, untyped = bindings[id(n)]
        scopes.append(Scope(lo, hi, frozenset(typed), frozenset(untyped)))
    return Typing(scopes)


def sdk_standin_classes(tree: ast.Module, rmap: dict[str, str]) -> set[str]:
    """Classes that STAND IN for the SDK — they implement the APPROVED surface.

    This is deliberately stricter than "defines several off-surface names": a FalkorDB
    handle, a registry double and a mock HTTP client all answer to `query` / `delete` /
    `update` — the same WORDS, a different contract. Production code reaches a stand-in
    through the private spelling, so its `def`s move; those other objects must not. The
    approved-surface requirement is the discriminator, because a real stand-in implements
    the verbs production actually calls and a `g.query` mock implements none of them.
    """
    approved = approved_methods()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if any(
            (isinstance(b, ast.Name) and b.id == "TortoiseSDK")
            or (isinstance(b, ast.Attribute) and b.attr == "TortoiseSDK")
            for b in node.bases
        ):
            out.add(node.name)
            continue
        names = {
            n.name
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if len(names & approved) >= 2:
            out.add(node.name)
    return out


def receiver_is_sdk(
    recv: ast.expr, typing: Typing, standins: set[str], in_sdk_class: bool, line: int
) -> bool:
    """Is this receiver something that carries the SDK surface? Evidence only, never a guess."""
    if isinstance(recv, ast.Name):
        if recv.id == "self":
            # `self` is typed ONLY inside the `TortoiseSDK` class body. Typing every
            # `self.x.<name>` renamed `self._dirty_roots.update()` into `._update()`.
            return in_sdk_class
        if recv.id == "TortoiseSDK":
            return True
        return (
            bool(_SDKISH.search(recv.id))
            or typing.typed(recv.id, line)
            or recv.id in standins
        )
    if isinstance(recv, ast.Attribute):
        if recv.attr == "TortoiseSDK" or _SDKISH.search(recv.attr):
            return True
        return isinstance(recv.value, ast.Name) and typing.typed_anywhere(recv.attr)
    if isinstance(recv, ast.Call):
        name = _call_name(recv)
        return name == "TortoiseSDK" or bool(_SDKISH.search(name)) or typing.typed_anywhere(name)
    if isinstance(recv, ast.Subscript):
        # `sdk["x"].method` — typed by what is being subscripted.
        return receiver_is_sdk(recv.value, typing, standins, in_sdk_class, line)
    return False


def _class_ranges(tree: ast.Module) -> list[tuple[int, int, str]]:
    return [
        (n.lineno, n.end_lineno or n.lineno, n.name)
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
    ]


def _in_sdk_class(line: int, ranges: list[tuple[int, int, str]]) -> bool:
    return any(lo <= line <= hi and nm == "TortoiseSDK" for lo, hi, nm in ranges)


def _def_name_span(path: Path, node: ast.AST, name: str) -> tuple[int, int] | None:
    """The (start, end) columns of the NAME in `def <name>(`, read from the source line.

    ⛔ NOT `col_offset + 4`: for an `AsyncFunctionDef`, `col_offset` points at `async`, so
    offset arithmetic writes `asyn_deletedelete`, a syntax error the sweep then cannot see
    because the file no longer parses.
    """
    line = path.read_text(encoding="utf-8").splitlines()[node.lineno - 1]
    m = re.search(rf"\bdef\s+({re.escape(name)})\b", line[node.col_offset:])
    if not m:
        return None
    start = node.col_offset + m.start(1)
    return start, start + len(name)


@dataclass
class FileEdits:
    attrs: list[tuple[int, int, int, str]]
    defs: list[tuple[int, int, int, str]]
    strings: list[tuple[int, int, int, str]]
    residue: Counter


#: A string literal naming one of the methods: `"query"`, `"sdk.query"`,
#: `"tortoise.sdk.TortoiseSDK.query"`. Swept only when the surrounding call makes the SDK
#: unambiguous (`getattr(<typed>, …)`, `setattr(<typed>, …)`) or when the literal itself names
#: the class (`TortoiseSDK.<name>` / `tortoise.sdk.TortoiseSDK.<name>`).
_STRING_CLASS_REF = re.compile(r"(?:^|\.)TortoiseSDK\.([A-Za-z_][A-Za-z0-9_]*)$")
_STRING_PLAIN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)$")


def edits_for(path: Path, rmap: dict[str, str]) -> FileEdits:
    src = path.read_text(encoding="utf-8")
    # SOUND, not a heuristic: an attribute, `def` or string reference to a name cannot exist
    # without that name's literal text in the file, so a file containing none of them has no
    # edit and no residue. It is what keeps `--check` a gate people will actually run.
    if not any(name in src for name in rmap):
        return FileEdits([], [], [], Counter())
    try:
        with warnings.catch_warnings():
            # These files are READ, never run: a `\s`-in-docstring warning from someone else's
            # module is not this gate's business, and it buries the verdict line.
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(src)
    except SyntaxError:
        return FileEdits([], [], [], Counter({"<<syntax-error>>": 1}))
    in_sdk = path.resolve() == SDK_SRC.resolve()
    standins = sdk_standin_classes(tree, rmap)
    typing = sdk_typing(tree)
    ranges = _class_ranges(tree)
    lines = src.splitlines()
    attrs: list[tuple[int, int, int, str]] = []
    defs: list[tuple[int, int, int, str]] = []
    strings: list[tuple[int, int, int, str]] = []
    residue: Counter = Counter()

    for node in ast.walk(tree):
        # ── attribute accesses ────────────────────────────────────────────────
        if isinstance(node, ast.Attribute) and node.attr in rmap:
            in_class = in_sdk and _in_sdk_class(node.lineno, ranges)
            if not receiver_is_sdk(node.value, typing, standins, in_class, node.lineno):
                residue[f"{ast.unparse(node.value)[:40]}.{node.attr}"] += 1
                continue
            if node.value.end_lineno != node.end_lineno:
                residue[f"{node.attr} (multiline receiver)"] += 1
                continue
            start = node.value.end_col_offset  # EXCLUSIVE: this is the '.'
            end = node.end_col_offset
            if lines[node.lineno - 1][start:end] != "." + node.attr:
                residue[f"{node.attr} (unexpected span)"] += 1
                continue
            attrs.append((node.lineno, start, end, "." + rmap[node.attr]))
            continue

        # ── string-shaped references ──────────────────────────────────────────
        if isinstance(node, ast.Call):
            fn = _call_name(node)
            # A DECLARED BINDING: `sdk_method="create_point"` in `tool_registry.py` is
            # documented as "Attribute name on TortoiseSDK", so it moves with the
            # attribute. It is not an attribute access and no parenthesis search sees it.
            for kw in node.keywords:
                if kw.arg != "sdk_method":
                    continue
                lit = kw.value
                if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                    m = _STRING_PLAIN.match(lit.value)
                    if m and m.group(1) in rmap:
                        strings.append(
                            (lit.lineno, lit.col_offset + 1,
                             lit.col_offset + 1 + len(lit.value), rmap[m.group(1)])
                        )
            if fn in {"getattr", "setattr", "delattr", "hasattr"} and len(node.args) >= 2:
                target, literal = node.args[0], node.args[1]
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    m = _STRING_PLAIN.match(literal.value)
                    if m and m.group(1) in rmap and receiver_is_sdk(
                        target, typing, standins, False, literal.lineno
                    ):
                        strings.append(
                            (literal.lineno, literal.col_offset + 1,
                             literal.col_offset + 1 + len(literal.value), rmap[m.group(1)])
                        )
                        continue
                    m2 = _STRING_CLASS_REF.match(literal.value)
                    if m2 and m2.group(1) in rmap:
                        new = literal.value[: m2.start(1)] + rmap[m2.group(1)]
                        strings.append(
                            (literal.lineno, literal.col_offset + 1,
                             literal.col_offset + 1 + len(literal.value), new)
                        )
                        continue
                    if m and m.group(1) in rmap:
                        residue[f"string {literal.value!r} on untyped receiver"] += 1

        # ── stand-in `def`s ───────────────────────────────────────────────────
        if isinstance(node, ast.ClassDef) and node.name in standins:
            # ⛔ `TortoiseSDK` is itself a stand-in by the approved-surface rule, and the
            # class-body block at the end of this function emits its `def`s. Emitting them
            # here TOO puts two edits on the SAME span, and applying both mangles the name
            # (`def query` -> `def _queryy`) because the second one's columns were computed
            # against the pre-first-edit line. One owner per span.
            if node.name == "TortoiseSDK":
                continue
            for m in node.body:
                if (
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and m.name in rmap
                ):
                    span = _def_name_span(path, m, m.name)
                    if span is None:
                        residue[f"def {m.name} (span not located)"] += 1
                        continue
                    defs.append((m.lineno, span[0], span[1], rmap[m.name]))

    if in_sdk:
        for m in class_body().body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in rmap:
                span = _def_name_span(SDK_SRC, m, m.name)
                if span is None:
                    raise SystemExit(
                        f"cannot locate the name span for `def {m.name}` at "
                        f"tortoise/sdk.py:{m.lineno}"
                    )
                defs.append((m.lineno, span[0], span[1], rmap[m.name]))
    return FileEdits(attrs, defs, strings, residue)


def apply_edits(path: Path, edits: list[tuple[int, int, int, str]]) -> int:
    if not edits:
        return 0
    lines = path.read_text(encoding="utf-8").split("\n")
    by_line: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for lineno, start, end, repl in edits:
        by_line[lineno].append((start, end, repl))
    for lineno, spans in by_line.items():
        spans.sort(key=lambda s: s[0], reverse=True)  # right-to-left: spans stay valid
        line = lines[lineno - 1]
        for start, end, repl in spans:
            line = line[:start] + repl + line[end:]
        lines[lineno - 1] = line
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(edits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", action="store_true", help="the rename map + collision audit")
    ap.add_argument("--report", action="store_true",
                    help="classification, collisions, stand-ins and every untyped residue shape")
    ap.add_argument("--apply", action="store_true", help="rewrite the tree")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 while any TYPED reference to a now-private name survives, or "
                         "the residue set moved off its pin, or the map derived from the "
                         "pinned base is empty/wrong-sized (a vacuous verifier is a failure)")
    ap.add_argument(
        "--base", default=None,
        help=f"rev to derive the rename map from (default for --check: the PINNED sweep base "
             f"{SWEEP_BASE[:12]}; default for --plan/--apply/--report: the working tree). "
             "⛔ `--check` MUST NOT default to `merge-base origin/main`: after this branch "
             "merges the live class no longer holds the names, the map is empty, and the "
             "check would answer 0 references over an unswept tree — a vacuous PASS.")
    ap.add_argument("--repin", action="store_true",
                    help="print the SWEEP_RESIDUE pin for the current tree (deliberate re-pin)")
    ap.add_argument("--top", type=int, default=25, help="residue rows to print")
    args = ap.parse_args()
    if not (args.plan or args.apply or args.check or args.report or args.repin):
        ap.print_help()
        return 2

    if args.base is None and (args.check or args.repin):
        args.base = SWEEP_BASE

    entries = rename_map(rev=args.base)
    rmap = {e.name: e.private for e in entries}

    if args.check and len(entries) != SWEEP_NAMES:
        # A map that is empty (or a different size) means the check is answering about a
        # different tree than the one the sweep was applied to. That is a FAILURE, not a pass:
        # a verifier whose answer cannot vary is the fail-open this file exists to prevent.
        print(
            f"VACUOUS CHECK: the rename map derived from {args.base[:12]} holds "
            f"{len(entries)} name(s), expected exactly {SWEEP_NAMES}. `--check` cannot answer "
            "\"did we finish\" from a map that is empty or has drifted; re-pin SWEEP_BASE / "
            "SWEEP_NAMES deliberately, in a commit that says why.",
            file=sys.stderr,
        )
        return 2

    if args.plan or args.report:
        print(f"{len(entries)} off-surface methods on TortoiseSDK\n")
        for i, e in enumerate(entries, 1):
            mark = f"  ⚠️ COLLISION -> {e.private}" if e.collided else ""
            print(f"{i:2d}. {e.name:26s} -> {e.private}{mark}")
        print(f"\ncollisions: {sum(1 for e in entries if e.collided)}")

    total_attrs = total_defs = total_strings = 0
    residue_all: Counter = Counter()
    per_file_attrs: Counter = Counter()
    # ONE walk per invocation: the check block below re-derives the same counts, so running
    # this loop for `--check` too doubled the gate's wall clock for nothing (measured: 4m20 →
    # 2m10 on the tree at this head).
    walk_here = args.plan or args.report or args.apply
    for p in py_files() if walk_here else ():
        e = edits_for(p, rmap)
        residue_all.update(e.residue)
        if e.attrs or e.defs or e.strings:
            per_file_attrs[p.relative_to(ROOT).as_posix()] = len(e.attrs)
            total_attrs += len(e.attrs)
            total_defs += len(e.defs)
            total_strings += len(e.strings)
            if args.apply:
                n = apply_edits(p, e.attrs + e.defs + e.strings)
                print(f"  rewrote {n:5d} reference(s) in {p.relative_to(ROOT)}")

    if args.apply:
        print(f"\nTOTAL rewrites: attr={total_attrs} def={total_defs} string={total_strings}")

    if args.report:
        print(f"\nTYPED rewrites available: {total_attrs} attribute accesses, "
              f"{total_defs} `def` lines, {total_strings} string references")
        print("\ntop files by attribute rewrites:")
        for f, c in per_file_attrs.most_common(12):
            print(f"  {c:5d}  {f}")
        print(f"\nUNTYPED receivers NOT swept (the visible edge): {sum(residue_all.values())}")
        for r, c in residue_all.most_common(args.top):
            print(f"  {c:5d}  {r}")

    if args.check or args.repin:
        after = 0
        after_residue: Counter = Counter()
        for p in py_files():
            e2 = edits_for(p, rmap)
            after += len(e2.attrs) + len(e2.defs) + len(e2.strings)
            after_residue.update(e2.residue)
        digest = residue_digest(after_residue)
        if args.repin:
            print(f"SWEEP_RESIDUE = ({sum(after_residue.values())}, {digest!r})")
            return 0
        print(f"typed references still pointing at a public name: {after}")
        print(f"untyped receivers left alone (deliberate, reported): {sum(after_residue.values())}")
        pinned = SWEEP_RESIDUE
        if (sum(after_residue.values()), digest) != pinned:
            print(
                "the residue SET moved off its pin: a reference shape that was not there when "
                "this sweep was verified is now on the tree, and the ordinary-word receivers "
                "(`x.query`, `x.update`, `x.delete`) are exactly the shape a typed-reference "
                "count cannot see. Read the rows below, fix or declare the new shape, then "
                f"re-pin with --repin.\n  observed: ({sum(after_residue.values())}, "
                f"{digest!r})\n  pinned:   {pinned}",
                file=sys.stderr,
            )
            for r, c in after_residue.most_common(10):
                print(f"  {c:5d}  {r}", file=sys.stderr)
            return 1
        return 1 if after else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
