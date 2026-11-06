"""`#11085` — the embedding-exemption register has a production home, and an undeclared writer
cannot bypass silently.

**What `#11085` is.** `#10541` *classified* the embedding-writer population (6 recognised producers,
9 legitimately exempt, 0 bypasses) but left that classification in `tools/dod_5090_probe.py`'s
`WRITER_CENSUS`, which is a **read-only instrument**. `#5090`'s DoD row 1 has two readings — **(a)**
a classified population is enough, **(b)** every writer must route through the gate *or* be a
**named, accepted exception in the product**. This issue takes (b), because (a) makes the metric
green while a writer can still bypass the rule *while labelled*. `tortoise/embed_writer_register.py`
is the product home; this file is its proof.

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_the_register_resolves_at_runtime_from_tortoise` | the issue's first row — *"resolvable at runtime from `tortoise/`"* | the register moves out of `tortoise/`, under `tortoise/projection/`, or stops being importable without the tooling |
| `test_the_register_imports_without_entering_the_projection_cycle` | `04-plan.md` §5's leaf requirement, EXECUTED | the register imports anything from `tortoise`, so it can re-enter `projection → pack_state → sdk → projection` — the cycle whose module-scope config read is a reproduced `ImportError` (`projection/__init__.py:1466-1481`) |
| `test_the_register_is_not_a_second_rule_set` | plan risk R2 — *"the register becomes a second source of truth and drifts from `value_gate.RULES`"* | the register imports `value_gate`/`embed_gate`, or grows a pattern/predicate/rule id |
| `test_a_duplicate_key_raises` | the issue's third row (D4) | a second declaration silently wins — the stale row survives a rewrite |
| `test_a_bypass_declaration_is_refused` | `04-plan.md` §6.2 — *"no `bypass` class; an unclassified writer is a refusal at export time"* | `bypass` becomes declarable, so the class that means *defect* can be asserted as *accepted* |
| `test_an_undeclared_writer_is_a_refusal_not_a_default` | the same contract, at the LOOKUP | `classify` answers for a writer nobody declared — the fallback that makes "no silent bypass" unkeepable |
| `test_every_writer_the_instrument_finds_outside_the_gate_is_declared` | the issue's second row, LIVE (D3) | the tree grows a writer whose vector the gate does not see and the product has no declaration for it — the *silent bypass* this issue exists to prevent |
| `test_the_live_guard_has_teeth_on_an_INJECTED_writer` | the same guard, driven rather than read | the guard goes vacuous when the tree's population is all-declared (the `#10541` guard's own lesson), or the register's enumeration stops naming an undeclared writer |
| `test_the_instrument_and_the_product_declare_the_same_writers` | `04-plan.md` §6.2 — *"the instrument and the product cannot disagree about which writer a row is about"* | a class changes in one place, the census keeps an entry the register dropped, or the register invents one the instrument does not find |
| `test_the_three_writers_that_carried_no_declaration_now_carry_one` | **this issue's own objective** (`03-scope.md` S2) | one of the three named writers loses its declaration, or the declaration stops naming `#11085` as the issue that accepted it |
| `test_every_declaration_cites_a_seam_and_an_owning_issue` | the issue's `{class, reason, owning issue}` shape | an entry carries a claim with no symbol behind it, or an owner that is not an issue |
| `test_the_DECLARATIONS_tuple_is_what_the_live_map_was_built_from` | the frozen map is not silently re-derived | `EMBED_WRITER_CLASSES` stops matching `_DECLARATIONS` (a mutation, a second `load()`, a mutable dict slipping in) |

⛔ **These tests write no graph and flip no gate.** `#11085` changes no writer's behaviour, no schema,
and no MCP/SDK surface — the register is a declaration, and its proof is a refusal.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import dod_5090_probe as probe

from tortoise import embed_writer_register as register
from tortoise.embed_writer_register import (
    CLASSES,
    EMBED_WRITER_CLASSES,
    EXEMPT,
    RECOGNISED_PRODUCER,
    DuplicateWriterDeclaration,
    UnclassifiedWriterKey,
    UnknownWriterClass,
    WriterClass,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_FILE = Path(register.__file__).resolve()

#: The three writers `#11085` names as carrying **no declared exemption in the product at all**
#: before this change (`tools/dod_5090_probe.py`'s census comment: *"the other three carry NO
#: declared exemption"*). This list is the issue's own acceptance, spelled out so it cannot be
#: satisfied by declaring three unrelated writers.
THE_THREE = (
    "tortoise.hosted_backup::restore_graph",
    "tortoise.projection.entities::_event_plain_merge",
    "tortoise.projection::_rebuild_all_replay",
)

#: Writer kinds whose vector the gate does NOT see, so the product must say what they are.
#: `gated` is excluded — the gate itself decides those, and declaring them would be a second
#: answer to a question the seam already answers. `producer` is excluded — it computes a vector a
#: DIFFERENT function writes, and that writer is in the map under its own key.
NEEDS_A_DECLARATION = ("unresolved_write", "ungated")


# ── The production home (the issue's first row) ─────────────────────────────


def test_the_register_resolves_at_runtime_from_tortoise():
    """It must be a `tortoise/` module — not a tool, not a doc, not under `projection/`."""
    assert MODULE_FILE.is_file(), MODULE_FILE
    assert MODULE_FILE.parent == REPO_ROOT / "tortoise", (
        f"the register must live directly in `tortoise/`, not {MODULE_FILE.parent}"
    )
    assert "projection" not in MODULE_FILE.parts, (
        "a leaf must not live under `tortoise/projection/` — that package's `__init__` is the "
        "head of the `projection -> pack_state -> sdk -> projection` cycle (04-plan.md §5)"
    )
    assert EMBED_WRITER_CLASSES, "an empty register declares nothing and classifies nobody"


def test_the_register_imports_without_entering_the_projection_cycle():
    """The leaf property, EXECUTED — a source reading cannot prove an import graph.

    `04-plan.md` §5 records the failure mode exactly: a module-scope read of the config constants
    under `tortoise/projection/` is a **reproduced** `ImportError` (`import tortoise.sdk` /
    `import tortoise.pack_manifest_store` break). This asserts the register pulls in NEITHER
    `tortoise.projection` nor `tortoise.sdk`, so a future edit that reaches for either reds here
    before it reaches a user.
    """
    code = (
        "import sys, tortoise.embed_writer_register as r\n"
        "assert r.EMBED_WRITER_CLASSES\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.split('.')[0] == 'tortoise')))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr
    loaded = set(out.stdout.split())
    assert "tortoise.projection" not in loaded, loaded
    assert "tortoise.pack_state" not in loaded, loaded
    assert "tortoise.sdk" not in loaded, loaded


def test_the_register_is_not_a_second_rule_set():
    """Plan risk R2. It speaks to WRITER CLASSIFICATION, never to *what earns a vector*.

    `value_gate.RULES` is the one rule set (`embed_gate.RULE_SET is value_gate.RULES`). If this
    module ever reads it, restates it, or grows a pattern of its own, the epic has two answers to
    one question and §12.2a's target is broken by the very change that cites it.
    """
    source = MODULE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "collections", "re", "dataclasses", "types"}, (
        f"the register imports {sorted(imported)} — it is a LEAF and must import the standard "
        "library only; importing `tortoise.*` (let alone `value_gate`) is how it becomes a second "
        "rule set or re-enters the projection cycle"
    )
    # ⛔ Prose may NAME the rule set — this module's docstring must be allowed to explain what it is
    # NOT. CODE may not touch it: a docstring saying "`value_gate.RULES` is the one rule set" is the
    # contract being stated, while `value_gate.RULES` as an EXPRESSION would be the drift.
    banned = {"value_gate", "embed_gate", "RULES", "EMBEDDING_RULES"}
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not banned & referenced, (
        f"the register's CODE references {sorted(banned & referenced)} — it speaks to writer "
        "classification, a different axis; `value_gate.RULES` stays the one rule set for what "
        "earns a vector (§12.2a)"
    )


# ── The refusals (§6.2 / §6.3) ─────────────────────────────────────────────


def _record(cls: str = EXEMPT, reason: str = "`tortoise.z::_w` reads one", owner: str = "#11085"):
    return WriterClass(cls, reason, owner)


def test_a_duplicate_key_raises():
    """D4: a duplicated declaration must not silently win."""
    key = "tortoise.z::_w"
    with pytest.raises(DuplicateWriterDeclaration) as excinfo:
        register.load([(key, _record()), (key, _record(RECOGNISED_PRODUCER))])
    assert key in str(excinfo.value)
    # …and the freeze is what raises it: a plain dict comprehension would keep the SECOND entry.
    assert register.load([(key, _record())])[key].cls == EXEMPT


def test_a_bypass_declaration_is_refused():
    """`bypass` is a DEFECT the instrument can name — never a class a writer can hold."""
    assert "bypass" not in CLASSES
    assert probe.CENSUS_BYPASS not in CLASSES, (
        "the instrument's `bypass` class must never become declarable — an accepted exemption "
        "and an observed bypass cannot share a spelling"
    )
    for bad in ("bypass", probe.CENSUS_BYPASS, "", "exmpt"):
        with pytest.raises(UnknownWriterClass):
            register.load([("tortoise.z::_w", _record(bad))])
    live = sorted({entry.cls for entry in EMBED_WRITER_CLASSES.values()})
    assert live == sorted(CLASSES), live


def test_an_undeclared_writer_is_a_refusal_not_a_default():
    """No fallback. A writer nobody declared must raise, in both directions."""
    missing = "tortoise.sdk::_a_writer_nobody_declared"
    assert not register.is_declared(missing)
    assert EMBED_WRITER_CLASSES.get(missing) is None, (
        "a `.get()` that answers is a default, and a default makes 'no silent bypass' a claim "
        "the code cannot keep"
    )
    with pytest.raises(UnclassifiedWriterKey) as excinfo:
        register.classify(missing)
    assert missing in str(excinfo.value)
    assert register.undeclared([missing, *EMBED_WRITER_CLASSES]) == (missing,)


def test_a_declaration_without_a_seam_or_owner_is_refused():
    """`{class, reason, owner_issue}` — a class with no seam behind it is a claim."""
    with pytest.raises(ValueError):
        register.load([("tortoise.z::_w", _record(reason="   "))])
    with pytest.raises(ValueError):
        register.load([("tortoise.z::_w", _record(owner="11085"))])
    with pytest.raises(ValueError):
        register.load([("_w", _record())])  # a bare function name is not an identity
    with pytest.raises(ValueError):
        register.load([])


# ── The live population (D3, both directions) ──────────────────────────────


def _writers_outside_the_gate() -> set[str]:
    """Every writer the instrument reports whose vector the gate does not see."""
    return {
        writer for writer, info in probe.embedding_writers().items()
        if info["kind"] in NEEDS_A_DECLARATION
    }


def test_every_writer_the_instrument_finds_outside_the_gate_is_declared():
    """**The silent-bypass guard.** A new writer outside the gate needs a PRODUCT declaration.

    This is the `#11085` property in one assertion: the instrument discovers the population by
    walking the tree, and the product must already name every member of it. A writer added tomorrow
    without a declaration reds HERE — which is what `#11086` (arming the gate) needs to be able to
    point at, and what "an undeclared writer cannot bypass silently" means once the census is not
    the only place the answer lives.
    """
    outside = _writers_outside_the_gate()
    assert outside, (
        "the instrument found no writer outside the gate — if that is genuinely true the tree "
        "changed shape and this guard is now vacuous; the injected-writer test below carries the "
        "contract in that case, so read this failure before deleting anything"
    )
    assert register.undeclared(outside) == (), (
        "these writers store a vector the gate does not see and carry NO declaration in "
        f"`tortoise/embed_writer_register.py`: {register.undeclared(outside)}"
    )


def test_the_live_guard_has_teeth_on_an_INJECTED_writer():
    """The same guard, DRIVEN — it must not depend on the tree currently holding a hole.

    `#10541`'s own guard learned this the expensive way: a live exact-set match goes vacuous the
    moment the population is all-classified, and its repair is to keep an injected direction. Here
    the injection is the *undeclared* direction: one key the register does not know, named by the
    shared enumeration and refused by the lookup.
    """
    injected = "tortoise.z::_a_new_writer"
    population = _writers_outside_the_gate() | {injected}
    assert register.undeclared(population) == (injected,)
    assert not register.is_declared(injected)
    with pytest.raises(UnclassifiedWriterKey):
        register.classify(injected)


def test_the_instrument_and_the_product_declare_the_same_writers():
    """§6.2: the two cannot disagree about WHICH writer a row is about — or about what it is."""
    census = dict(probe.WRITER_CENSUS)
    declared = set(EMBED_WRITER_CLASSES)
    assert set(census) <= declared, (
        "the census classifies writers the product does not declare: "
        f"{sorted(set(census) - declared)}"
    )
    stale = declared - set(census) - _writers_outside_the_gate()
    assert not stale, (
        "the register declares writers the instrument no longer finds outside the gate "
        f"(a stale declaration survives a rewrite): {sorted(stale)}"
    )
    disagree = {
        key: (census[key][0], EMBED_WRITER_CLASSES[key].cls)
        for key in set(census) & declared
        if census[key][0] != EMBED_WRITER_CLASSES[key].cls
    }
    assert not disagree, (
        "the instrument and the product classify the same writer differently "
        f"(instrument, register): {disagree}"
    )


def test_the_three_writers_that_carried_no_declaration_now_carry_one():
    """`03-scope.md` S2, verbatim: the three that carried none now carry one, naming `#11085`.

    ⛔ `#11085` is the owner on purpose. Reading (b) says an exception must be NAMED in the
    product, so the naming IS the acceptance — not a re-litigation of what each writer does, which
    the instrument already read at its seam. A later lane must not "tidy" that to `#10541`: the
    census *recorded the gap*, it did not accept the exemption.
    """
    for key in THE_THREE:
        assert register.is_declared(key), f"{key} carries no declaration — this issue's objective"
        entry = register.classify(key)
        assert entry.cls == RECOGNISED_PRODUCER, (key, entry)
        assert entry.owner_issue == "#11085", (key, entry)


def test_every_declaration_cites_a_seam_and_an_owning_issue():
    """No entry is a claim with nothing behind it."""
    for key, entry in EMBED_WRITER_CLASSES.items():
        assert entry.owner_issue.startswith("#"), (key, entry)
        assert entry.cls in CLASSES, (key, entry)
        assert "::" in entry.reason, (
            f"{key} carries no symbol citation (`module::symbol`) — a reason without a seam is "
            "exactly the `path:LINE` citation that rotted in the instrument's first census"
        )


def test_the_DECLARATIONS_tuple_is_what_the_live_map_was_built_from():
    """The frozen map is `_DECLARATIONS`, loaded once — not re-derived at lookup time."""
    from_declarations = register.load(register._DECLARATIONS)
    assert dict(from_declarations) == dict(EMBED_WRITER_CLASSES)
    with pytest.raises(TypeError):
        EMBED_WRITER_CLASSES["tortoise.z::_w"] = _record()  # type: ignore[index]
    assert len(register._DECLARATIONS) == len(EMBED_WRITER_CLASSES)
