"""`#11085` — the embedding-exemption register has a production home, and an undeclared writer
cannot bypass silently **within the population the instrument can see**.

⛔ The qualifier is not decoration and `#11086` must not drop it: discovery is
`tools/dod_5090_probe.py::_EMBED_WRITE` (a source-text regex) PLUS
`_writes_an_embedding` (an AST pass that decides the CONTAINER-KEY shape
`rec["embedding"] = …`, `#11274`), so a SHAPE spelled as a map-merge
(`SET n += $p` carrying `{"embedding": …}`), an inline property map
(`MERGE (n:Point {…, embedding: $e})`), a whole-node replace (`SET n = $map`), a property name
INTERPOLATED into the clause (live: `tortoise.cache_projection::node_statement`), or through a
container HELPER / COMPUTED key (`clean.setdefault("embedding", None)`, `d[KEY] = v`) is ABSENT
from discovery — and every test below is green. ⚠️ The SHAPE is what is blind, NOT necessarily the
WRITER carrying it — do not drop this qualifier, which the register's docstring carries:
`tortoise.sdk::update_point`'s computed key sits in the discovered map as `gated`, and
`tortoise.hosted_backup::restore_graph` is IN `WRITER_CENSUS` (declared off its
`set_embedding_clause`, not off its `SET n = $p`). No COUNT of reasons is stated here on purpose —
it already moved inside this PR's own review — but every one of them shares a property: none means
"no such write". A writer is absent when it holds NO decidable statement
(`tortoise.cache_projection::node_statement`, `tortoise.sdk::_emit_event`); when its module is
outside the walk (`tortoise/embeddings.py` is skipped on purpose, which is why
`stamp_journal_embedding` / `_drop_journalled_embedding` hold a DECIDABLE container key and are
still absent); when its module will not parse (discovery carries one `UNPARSEABLE:<rel>` row and
enumerates none of that module's writers); when the store sits at MODULE or CLASS scope, since the
walk visits `def`s only — pinned as `module_scope_container_key` and `class_scope_container_key`
below. `#11274` owns widening it. ⚠️ Together with the name-collision case below, this enumerates
what makes the DISCOVERED MAP unable to NAME OR CLASSIFY a writer; it is not a list of every
reason a writer can escape a declaration — `gated` and `producer` escape it too, by design, and are
stated in the register instead.

⚠️ A NAME-COLLIDING writer is the case here that can be NEITHER absent NOR declarable, and the
mechanism differs by whether the walk reaches the colliding def:

* **BOTH defs discovered** → the KEY stays in the map with its KIND replaced by `"duplicate"`
  (`probe._embedding_writers`'s `_put`), which is outside `NEEDS_A_DECLARATION`: it owes no
  declaration while its class is UNRESOLVED (it may or may not store a vector the gate sees).
* **Colliding def NOT discovered** → `_put` is never reached for it and the key keeps the DISCOVERED
  def's kind. If that kind is `unresolved_write`/`ungated` (measured in both orders), the writer is
  **caught** — the kind is in `NEEDS_A_DECLARATION`. If it is `gated` or `producer`, the key stops at
  a class that owes no declaration and the colliding writer is neither absent nor declarable — the
  same gap, reached through a sibling this file cannot see into.

So a `duplicate` row is the narrow case, and the guard below PINS the population at zero `duplicate`
rows rather than reading the class as acceptable: a writer being IN the map is not the same thing as
being classifiable. What closes the sibling case is the register's closure-claim caveat (§ the
`gated` escape), not this list.

**What `#11085` is.** `#10541` *classified* the embedding-writer population (read `WRITER_CENSUS`
for the class counts rather than a number restated here — the #11274 widening moved them) but left
that classification in `tools/dod_5090_probe.py`'s `WRITER_CENSUS`, which is a **read-only
instrument**. `#5090`'s DoD row 1 has two readings — **(a)**
a classified population is enough, **(b)** every writer must route through the gate *or* be a
**named, accepted exception in the product**. This issue takes (b), because (a) makes the metric
green while a writer can still bypass the rule *while labelled*. `tortoise/embed_writer_register.py`
is the product home; this file is its proof.

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_the_register_resolves_at_runtime_from_tortoise` | the issue's first row — *"resolvable at runtime from `tortoise/`"* | the register moves out of `tortoise/`, under `tortoise/projection/`, or stops being importable without the tooling |
| `test_the_register_imports_without_entering_the_projection_cycle` | `04-plan.md` §5's leaf requirement, EXECUTED | importing the register pulls `tortoise.projection`, `tortoise.pack_state` or `tortoise.sdk` into `sys.modules`, i.e. it re-enters `projection → pack_state → sdk → projection` — the cycle whose config read is a reproduced `ImportError` (`tortoise.projection.__init__::_config_classes` records it). The "imports ONLY the standard library" arm is `test_the_register_is_not_a_second_rule_set`, not this one |
| `test_the_register_is_not_a_second_rule_set` | plan risk R2 — *"the register becomes a second source of truth and drifts from `value_gate.RULES`"* | the register's imports leave `{__future__, collections, re, dataclasses, types}` — every `import` / `from` at EVERY level counts, so a relative `from .x import …` is not exempt — or it names `value_gate`/`embed_gate`/`RULES`/`EMBEDDING_RULES` in code (the ONE regex it holds is the `#\\d+` `owner_issue` format check, which is not a rule) |
| `test_a_duplicate_key_raises` | the issue's third row (D4) | a second declaration silently wins — the stale row survives a rewrite |
| `test_a_bypass_declaration_is_refused` | `04-plan.md` §6.2 — *"no `bypass` class; an unclassified writer is a refusal at export time"* | `bypass` becomes declarable, so the class that means *defect* can be asserted as *accepted* |
| `test_an_undeclared_writer_is_a_refusal_not_a_default` | the same contract, at the LOOKUP | `classify` answers for a writer nobody declared — the fallback that makes "no silent bypass" unkeepable |
| `test_every_writer_the_instrument_finds_outside_the_gate_is_declared` | the issue's second row, LIVE (D3) | the tree grows a writer **discovery decides** whose vector the gate does not see and the product has no declaration for it — the population is the syntax the AST/regex pass can decide, and what it cannot are NAMED (`UNDECIDABLE_WRITE_SHAPES` in the instrument, `INVISIBLE_SHAPES` below for the forms neither pass decides AND the scopes the walk never enters) and pinned by `test_the_DISCOVERY_blind_spot_is_pinned_not_merely_documented` |
| `test_the_live_guard_has_teeth_on_an_INJECTED_writer` | the same guard, driven through **DISCOVERY** (`probe._embedding_writers` on an injected tree) rather than through set arithmetic | the guard goes vacuous when the tree's population is all-declared (the `#10541` guard's own lesson), or the discovery filter regresses and stops seeing a writer — in EITHER the clause shape or the container-key shape (`#11274`) |
| `test_the_register_names_the_shapes_its_discovery_cannot_see` | `#11274` — the guard is exactly as wide as discovery, so discovery's blind spots are NAMED rather than implied | the instrument stops naming a shape it cannot decide, claims to decide one it cannot (or the reverse), or the register's docstring stops scoping its claim to the mechanism |
| `test_the_seam_CALL_population_is_the_one_the_register_names` | the register's ONE duty to a `gated` writer — an accurate statement of WHO is in that population, since it owes no declaration | the sentence that says *why* the shape-holding writers are in the map anyway enumerates a SUBSET of the writers that reach the seam by CALL (it named 4 of 9 at `#11373`'s review), or discovery grows/removes a member |
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
import re
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
#: before this change (`tools/dod_5090_probe.py`'s census comment, quoted verbatim: *"The other
#: three (`restore_graph`, `_event_plain_merge`, `_rebuild_all_replay`) carried NO declared
#: exemption in the PRODUCT until `#11085`"* — the `in the PRODUCT` clause is the distinction this
#: file exists to draw, so do not shorten it). This list is the issue's own acceptance, spelled out
#: so it cannot be
#: satisfied by declaring three unrelated writers.
THE_THREE = (
    "tortoise.hosted_backup::restore_graph",
    "tortoise.projection.entities::_event_plain_merge",
    "tortoise.projection::_rebuild_all_replay",
)

#: Writer kinds whose vector the gate does NOT see, so the product must say what they are.
#: `gated` is excluded — a `gated` writer owes no declaration. ⛔ That is a statement about
#: DECLARATION, never about the gate deciding every vector on that path: `sdk::create_point` and
#: `sdk::update_point` are `gated` and still store a caller-supplied vector verbatim (read the
#: register's closure-claim caveat). `producer` is excluded — it computes a vector a
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
    `import tortoise.pack_manifest_store` break). This asserts the register pulls in NONE of
    `tortoise.projection`, `tortoise.pack_state` or `tortoise.sdk`, so a future edit that reaches for
    any of the three reds here before it reaches a user.
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
        elif isinstance(node, ast.ImportFrom):
            # ⛔ `level` is NOT filtered here (#11373 round-19 review). Requiring `level == 0` left
            # every RELATIVE import out of this set, so `from .value_gate import RULES as _R` — the
            # idiomatic way to reach a sibling from INSIDE the package, i.e. exactly the drift this
            # test exists to catch — kept arm 1 green (measured). Arm 2 missed it too, because an
            # alias is neither a `Name` nor an `Attribute` spelled with a banned identifier, and
            # the subprocess arm stays green because importing `tortoise.value_gate` pulls none of
            # the three cycle modules (measured). A relative import now contributes its target
            # (`""` for `from . import x`), neither of which is in the allowlist below.
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
    """**The silent-bypass guard — scoped to what discovery can SEE.**

    A new writer outside the gate needs a PRODUCT declaration, asserted over the population the
    instrument measures: it discovers that population by walking the tree, and the product must
    already name every member of it. That is what `#11086` (arming the gate) needs to point at, and
    what "an undeclared writer cannot bypass silently" means for this population once the census is
    not the only place the answer lives.

    ⛔ What this does NOT claim: that discovery is exhaustive. A writer whose ONLY embedding store
    neither the regex nor the AST pass can decide (`SET n += $p`, an inline property map,
    `SET n = $map`, a clause held in a module constant) is ABSENT from the census, so it does not
    red here — while a writer that ALSO holds a decidable statement stays in the population
    (`tortoise.hosted_backup::restore_graph`, in the census off its `set_embedding_clause`), UNLESS
    its module is outside the walk: `tortoise/embeddings.py` is skipped on purpose, so
    `stamp_journal_embedding` / `_drop_journalled_embedding` hold a decidable container key and are
    still absent. The
    guard's own inherited lesson, and the reason an earlier version of this docstring ("a writer
    added tomorrow reds HERE") overstated the guard. The blind spot is stated in
    `tortoise/embed_writer_register.py`'s module docstring and PINNED by
    `test_the_DISCOVERY_blind_spot_is_pinned_not_merely_documented` below, so silence is
    distinguishable from coverage. **Widening** discovery is `#11274`, which blocks `#11086`.
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
    # ⛔ `kind == "duplicate"` is OUTSIDE `NEEDS_A_DECLARATION`, so a name-colliding writer owes no
    # declaration and its KIND IS UNRESOLVED — it may or may not store a vector the gate sees. The
    # collapse needs BOTH defs discovered: `_put` is only reached for a def the walk classifies, and
    # a def it does not reach `continue`s before `_put`, leaving the discovered def's own kind in
    # place (measured: a same-named pure reader second leaves `unresolved_write`, which IS
    # declarable). Zero at this head, and the cheapest real repair is that it stays zero: an
    # unresolvable class must not sit in the population at all, or the guard is green over a writer
    # nothing can classify.
    duplicates = sorted(
        key for key, entry in probe.embedding_writers().items()
        if entry.get("kind") == "duplicate"
    )
    assert not duplicates, (
        "discovery collapsed a name-colliding writer into a `duplicate` row, so its kind is "
        "UNRESOLVED — it may store a vector the gate does not see, and it owes NO declaration "
        "either way, which is the gap this guard exists to refuse: "
        f"{duplicates}. Reconcile the collision (rename or merge the two same-named defs in one "
        "module) so discovery can classify the writer. It cannot be DECLARED instead: `duplicate` "
        "is not a declarable class, and declaring a key the instrument does not find outside the "
        "gate reds the agreement test. Do not delete this arm."
    )


def test_the_live_guard_has_teeth_on_an_INJECTED_writer(tmp_path):
    """The same guard, DRIVEN **through discovery** — not through the register's own arithmetic.

    `#10541`'s guard learned this the expensive way: a live exact-set match goes vacuous the moment
    the population is all-classified, and its repair is to keep an injected direction.

    ⛔ The injection belongs in :func:`probe._embedding_writers` — the DISCOVERY half — and not in
    the population handed to :func:`register.undeclared` (`#11085`'s review, P2 / `#11274`):
    injecting into the derived set exercises set arithmetic that
    `test_an_undeclared_writer_is_a_refusal_not_a_default` already covers, and it stays GREEN
    through a regressed discovery filter — which is the failure that actually matters, because an
    undiscovered writer never reaches the refusal at all.

    Both shapes are injected, because a discovery filter can see one and miss the other:

    * the CLAUSE shape (`name.embedding = …` / `set_embedding_clause(…)`) — always seen;
    * the CONTAINER-KEY shape (`record["embedding"] = …`) — INVISIBLE until `#11274`. The live
      instances it was missing are the four the register's container-key bullet names
      (`api::add_point`, `consistency::_graph_fingerprint`, `sdk::_index_gate_read`,
      `sdk::_write_capture_turns`); `sdk._journal_event_raw_write` uses the same key but was already
      in the population through the `embedding=None` in its SIGNATURE, so it is not evidence the
      shape was seen. This half of the test is RED on the pre-`#11274` filter.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "z.py").write_text(
        'def _clause_writer():\n'
        '    return set_embedding_clause("n")\n'
        '\n'
        'def _container_key_writer(rec, vec):\n'
        '    rec["embedding"] = vec\n'
        '\n'
        'def _no_write_at_all(rec):\n'
        '    return rec.get("embedding")\n',
        encoding="utf-8",
    )
    found = probe._embedding_writers(tmp_path)
    written = {k for k, d in found.items() if d["kind"] == "unresolved_write"}
    assert written == {
        "tortoise.z::_clause_writer",
        "tortoise.z::_container_key_writer",
    }, f"discovery missed an injected writer: {sorted(found)}"
    assert "tortoise.z::_no_write_at_all" not in found, (
        "discovery reported a function that only READS the key — a filter that fires on a read "
        "would fill the register with declarations that assert nothing"
    )
    assert register.undeclared(written) == tuple(sorted(written)), (
        "the register declared an injected writer, or stopped refusing one"
    )
    for key in written:
        with pytest.raises(UnclassifiedWriterKey):
            register.classify(key)


def test_the_exported_ROUTE_seams_are_the_ones_the_class_claims_are_used():
    """`RECOGNISED_PRODUCER_SEAMS` is the class's definition AS DATA, and the entries agree.

    `#11444`: the audit instrument credits a `recognised_producer` declaration only when the
    writer's own body reaches one of these seams, so the tuple must be the same definition the
    entries are written against — otherwise the export is stale and every correct declaration
    reads as uncorroborated (a false HOLD, the opposite of the gate that made it reachable).

    ⛔ Pinned from the ENTRIES' side, not by copying the docstring: every `recognised_producer`
    reason must NAME at least one exported seam. What this CANNOT see — because a reason usually
    cites more than one seam, so `any(...)` stays satisfied when a co-named seam is dropped — is the
    FALSE-HOLD direction (a writer whose body reached only the dropped seam stops being credited).
    That direction is pinned where it is observable, in `tests/test_dod_5090_probe.py::
    test_a_seam_REMOVED_from_the_export_holds_the_declarations_that_need_it`, so this test's claim
    is the weaker one it actually asserts.
    """
    seams = register.RECOGNISED_PRODUCER_SEAMS
    assert seams, "an empty seam set would corroborate every route claim"
    assert all(isinstance(s, str) and s for s in seams), seams
    recognised = {
        key: entry for key, entry in EMBED_WRITER_CLASSES.items()
        if entry.cls == RECOGNISED_PRODUCER
    }
    assert recognised, "no recognised_producer entry — re-measure this guard"
    silent = sorted(
        key for key, entry in recognised.items()
        if not any(seam in entry.reason for seam in seams)
    )
    assert not silent, (
        "these recognised_producer entries name none of the exported seams, so either the "
        f"entry's reason is stale or RECOGNISED_PRODUCER_SEAMS is missing one: {silent} "
        f"(exported: {seams})")


def test_the_register_names_the_shapes_its_discovery_cannot_see():
    """`#11274`: the guard is exactly as wide as discovery — so its blind spots are NAMED.

    A register whose claim is "an undeclared writer cannot bypass silently" is only as true as the
    filter that populates it. The INSTRUMENT names these write shapes, and no COUNT is written here
    because it moves: the CLAUSE shape (`probe._EMBED_WRITE`) and the CONTAINER KEY
    (`probe._writes_an_embedding`) are DECIDED, the MAP-MERGE (`probe.UNDECIDABLE_WRITE_SHAPES`)
    is deliberately not; the shapes neither pass can decide are pinned as data in `INVISIBLE_SHAPES`
    below, so this docstring does not count them:

    * CONTAINER KEYS are decided on the AST (`probe._writes_an_embedding`), so they must NOT be in
      the blind list any more;
    * MAP-MERGES (`SET n += $p`) are data flow, not syntax, so they stay blind — and the live
      instance (`sdk._update_entity`) is DECLARED under a named exemption instead of being read
      as "no write". That is the difference between a scoped claim and a slogan.
    """
    blind = probe.UNDECIDABLE_WRITE_SHAPES
    assert blind, "discovery's blind spot must be NAMED in the instrument, never implied"
    assert any("map-merge" in shape for shape in blind), (
        f"the map-merge shape is no longer named as undecidable: {sorted(blind)}"
    )
    assert not any("container" in shape.lower() for shape in blind), (
        "the container-key shape is DECIDED now (#11274) — leaving it in the blind list would "
        "claim a hole the instrument no longer has"
    )
    live = register.classify("tortoise.sdk::_update_entity")
    assert (live.cls, live.owner_issue) == (EXEMPT, "#4094"), live
    assert "UNDECIDABLE_WRITE_SHAPES" in (register.__doc__ or ""), (
        "the register's own docstring must scope its claim to what discovery can decide — a "
        "measured limitation nobody reading the register can see is the claim being wider than "
        "the mechanism"
    )


def test_the_seam_CALL_population_is_the_one_the_register_names():
    """A `gated` writer owes NO declaration — so the register's only duty to it is an accurate
    statement of WHO is in that population, and this is the one claim about it that can go stale.

    `probe._embedding_writers` classes a writer `gated` the moment it CALLS the seam
    (`probe.SEAM_CALL`) — the write predicate is not consulted — so membership is INDEPENDENT of
    the write shape. The register's paragraph that says exactly that therefore has to name every
    member: it named four of them until
    `#11373`'s review measured five more in `tortoise/projection/entities.py` that are in the
    population by this same branch while holding a DIFFERENT write shape (`set_embedding_clause(…)`
    for subject/object/document, a Cypher `n.embedding=…` property SET fed from the params dict for
    `_upsert_point_props`, the bare CALL for `_upsert_event`) — none of them is a container-key
    holder, which turned "every writer" into a claim wider than its
    mechanism — the class this whole line of work exists to remove.

    The list is not counted in the register on purpose (a count re-stales with every writer), so it
    is re-derived here, from the instrument, at HEAD: a member that appears in the tree and not in
    the sentence REDS, and so does a name in the sentence that discovery does not class `gated` by
    CALL.
    """
    by_call = {
        key for key, row in probe.embedding_writers().items()
        if row["kind"] == "gated" and row.get("callee") == probe.SEAM_CALL
    }
    assert by_call, (
        "no writer reaches the seam by CALL — the sentence this test reads has lost its subject, "
        "or discovery stopped taking the `SEAM_CALL` branch"
    )

    doc = register.__doc__ or ""
    anchor = "every writer that reaches the seam by CALL"
    start = doc.find(anchor)
    assert start != -1, (
        "the register no longer states why the shape-holding writers are in the population: "
        f"{anchor!r}"
    )
    # Read the ENUMERATION, not a paragraph: the sentence's list is the maximal run of backticked
    # `module::symbol` tokens after the anchor, separated only by commas / `and` / whitespace. A
    # paragraph slice would over-reach — the same docstring block names `sdk::_update_entity` and
    # `sdk::_journal_event_raw_write` further down for different mechanisms, and neither is in this
    # population.
    separator = re.compile(r"[\s,:()]*(?:and\s+)?`([A-Za-z_][\w.]*)::([A-Za-z_]\w*)`")
    stated: list[tuple[str, str]] = []
    position = start + len(anchor)
    while match := separator.match(doc, position):
        stated.append((match.group(1), match.group(2)))
        position = match.end()
    assert stated, f"the sentence after {anchor!r} names no writer: {doc[position:position + 120]!r}"

    def matches(key: str, module: str, symbol: str) -> bool:
        """`sdk::x` and `tortoise.sdk::x` are the same writer — the register shortens the module."""
        writer_module, writer_symbol = key.split("::", 1)
        return writer_symbol == symbol and (
            writer_module == module or writer_module.endswith(f".{module}")
        )

    unstated = sorted(key for key in by_call if not any(matches(key, *t) for t in stated))
    assert not unstated, (
        "the register says `every` writer that reaches the seam by CALL and then names a subset — "
        f"in the population and not in the sentence: {unstated} (named: {stated})"
    )
    overstated = sorted(t for t in stated if not any(matches(k, *t) for k in by_call))
    assert not overstated, (
        "the sentence names a writer discovery does not class `gated` through the CALL branch — the "
        f"enumeration and the instrument disagree: {overstated}"
    )


def test_the_instrument_and_the_product_declare_the_same_writers():
    """§6.2: the two cannot disagree about WHICH writer a row is about — or about what it is."""
    census = dict(probe.WRITER_CENSUS)
    declared = set(EMBED_WRITER_CLASSES)
    assert set(census) <= declared, (
        "the census classifies writers the product does not declare: "
        f"{sorted(set(census) - declared)}"
    )
    # ⛔ `declared - _writers_outside_the_gate()`, NOT `declared - set(census) - outside`. The old
    # arithmetic subtracted the static census FIRST, so it removed every writer that had a census row
    # before staleness was tested — which is why a writer repaired so it no longer stores outside the
    # gate (the case this arm exists for) was masked, and only a declared writer the census does not
    # carry could ever fire it. The repair restores the direction the failure message claims. How many
    # writers that is stays a shape here and not a number: a count nobody re-derives is the rot class
    # this file deletes elsewhere.
    stale = declared - _writers_outside_the_gate()
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
        # ⛔ NOT pinned to `RECOGNISED_PRODUCER` (`#11085`'s review, P2 / `#11274`): this test's
        # claim is "carries a declaration", and a class asserted here would red a CORRECT later
        # reclassification for a reason this test does not state. What the classes must be is
        # pinned where the claim lives — the instrument/product AGREEMENT test above, and
        # `test_a_bypass_declaration_is_refused`, which pins the live classes to `CLASSES`.
        assert entry.cls in CLASSES, (key, entry)
        assert entry.owner_issue == "#11085", (key, entry)


#: A `module::symbol` citation in a `reason` — the ONLY form this register accepts, because a
#: `path:LINE` citation rots silently (the census's own `sdk.py` +43-lines lesson). Both spellings
#: are admitted so the register does not have to be edited to change one to the other:
#: `tortoise.projection::_apply_one` (dotted) and `tortoise/projection/__init__.py::_apply_one`
#: (a path ending in `.py`).
_SEAM_CITATION = re.compile(
    r"(?P<module>[A-Za-z_][\w./]*?\.py|[A-Za-z_][\w.]*)::(?P<symbol>\w+)"
)


def _cited_seams(reason: str) -> list[tuple[str, str]]:
    return [(m.group("module"), m.group("symbol"))
            for m in _SEAM_CITATION.finditer(reason)]


def _resolve_module(module: str) -> Path | None:
    """The file a dotted module (or a repo-relative `.py` path) names, or None."""
    if module.endswith(".py"):
        for candidate in (REPO_ROOT / module, REPO_ROOT / "tortoise" / module):
            if candidate.is_file():
                return candidate
        return None
    base = REPO_ROOT.joinpath(*module.split("."))
    # ⛔ A PACKAGE wins over a same-named MODULE. `tortoise/projection.py` is a DEPRECATED shim that
    # re-exports the package's names, so resolving `.py` first did two wrong things at once (round-17
    # review): a dotted `tortoise.projection::<symbol>` citation passed on the shim's re-export line
    # `_apply_one = _pkg._apply_one` — a false PASS, since deleting the symbol from the package would
    # not red it — and a symbol only the PACKAGE defines read as moved — a false RED.
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.is_file():
            return candidate
    return None


def test_every_declaration_cites_a_seam_and_an_owning_issue():
    """No entry is a claim with nothing behind it — and the citation is RE-DERIVED.

    `"::" in reason` was the pin here until `#11274` (found by `#11085`'s review, P2): a substring
    test passes on a reason that merely MENTIONS a `::` anywhere — including a citation of a symbol
    the tree has since deleted — so it checked the format of a claim rather than the claim. Each
    cited `module::symbol` must now RESOLVE at this head, which is also what makes the module
    docstring's "re-derived at HEAD" true for the one entry the census has no row for
    (`tortoise.session_indexer::main`).
    """
    for key, entry in EMBED_WRITER_CLASSES.items():
        assert entry.owner_issue.startswith("#"), (key, entry)
        assert entry.cls in CLASSES, (key, entry)
        cited = _cited_seams(entry.reason)
        assert cited, (
            f"{key} carries no `module::symbol` citation — a reason without a seam is exactly the "
            "`path:LINE` citation that rotted in the instrument's first census"
        )
        for module, symbol in cited:
            path = _resolve_module(module)
            assert path is not None, (
                f"{key} cites `{module}::{symbol}`, but `{module}` is not a module in this tree"
            )
            tree = ast.parse(path.read_text(encoding="utf-8"))
            defined = {
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            # A module-level CONSTANT is a citation target too (`UNDECIDABLE_WRITE_SHAPES`), and
            # it rots the same way a symbol does.
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    defined.add(node.target.id)
            assert symbol in defined, (
                f"{key} cites `{module}::{symbol}`, but {path.relative_to(REPO_ROOT)} does not "
                "define it — the seam moved and the declaration's evidence went with it"
            )
    # ⛔ The RESOLVER itself is pinned (round-18 review). `.py`-before-package ordering made a
    # dotted citation of a PACKAGE collide with a same-named deprecated SHIM
    # (`tortoise/projection.py` re-exports `tortoise/projection/__init__.py`), so
    # `tortoise.projection::<symbol>` could be "defined" by the shim's re-export line — a citation
    # that survives deleting the symbol from the package. No citation in the file resolves
    # differently under the two orders TODAY, which is exactly why the ORDER needs its own arm:
    # reverting it reds nothing without this.
    assert _resolve_module("tortoise.projection") == (
        REPO_ROOT / "tortoise" / "projection" / "__init__.py"
    ), "a package must win over a same-named module — `tortoise/projection.py` is a shim"
    assert _resolve_module("tortoise.config") == REPO_ROOT / "tortoise" / "config.py", (
        "`tortoise/config/` carries no `__init__.py`, so the module is the only candidate"
    )


def test_the_DECLARATIONS_tuple_is_what_the_live_map_was_built_from():
    """The frozen map is `_DECLARATIONS`, loaded once — not re-derived at lookup time."""
    from_declarations = register.load(register._DECLARATIONS)
    assert dict(from_declarations) == dict(EMBED_WRITER_CLASSES)
    with pytest.raises(TypeError):
        EMBED_WRITER_CLASSES["tortoise.z::_w"] = _record()  # type: ignore[index]
    assert len(register._DECLARATIONS) == len(EMBED_WRITER_CLASSES)


# ── The discovery blind spot (pinned, not merely documented) ────────────────

#: The entries `_EMBED_WRITE` cannot SEE, one scratch module each — a SPELLING neither pass can
#: decide, or a POSITION (module/class body) the walk never enters. Kept as data so the assertion
#: below reads as the limitation it is measuring, not as hand-written cases.
INVISIBLE_SHAPES = {
    "map_merge": 'def w(tx, p):\n    tx.query("SET n += $p", {"p": p})\n',
    "inline_property_map": (
        'def w(tx, e):\n    tx.query("MERGE (n:Point {id:$id, embedding: $e})")\n'
    ),
    "whole_node_replace": 'def w(tx, m):\n    tx.query("SET n = $m", {"m": m})\n',
    # The LIVE spelling is an f-string handed to the builder (`cache_projection.py::node_statement`:
    # `f", n.{prop} = {form}"`), NOT a bare concatenation into a local — the bare form executes no
    # call and stores nothing, so a body like it was "undetected" for ANY detector and pinned
    # nothing (review round 12).
    "interpolated_property": (
        'def w(tx, prop, form):\n    tx.query(f"SET n.{prop} = {form}")\n'
    ),
    # The two the LITERAL-slice AST pass cannot decide either (round-4 review): a container
    # HELPER is a `Call`, not a store, and a COMPUTED key makes `part.slice` a `Name`, not a
    # `Constant`. Both are live upstream (`sdk::_emit_event`, `sdk::update_point`), so they are
    # pinned here rather than only described in the docstring.
    "container_helper": 'def w(rec, v):\n    rec.setdefault("embedding", v)\n',
    "computed_key": 'def w(rec, key, v):\n    rec[key] = v\n',
    # A clause held in a module CONSTANT (round-10 review): `_EMBED_WRITE` is only ever run over a
    # FUNCTION's own source text (`probe._writes_an_embedding(node, source)`, `source` = the
    # function's line range), so a clause literal at MODULE scope is outside the detector's input;
    # the AST pass only inspects subscript targets — so neither fires. It was NAMED in the `#:`
    # comment above `EMBED_WRITER_CLASSES` (not in `register.__doc__`, which does not name it) and
    # in `test_every_writer_the_instrument_finds_outside_the_gate_is_declared`'s docstring (not in
    # the live-guard docstring, which names only the clause and container-key halves), while nothing
    # pinned it — which is the class this dict exists to make impossible.
    "module_constant_clause": (
        '_CLAUSE = "SET n.embedding = $e"\n'
        'def w(tx):\n    tx.query(_CLAUSE)\n'
    ),
    # A dict LITERAL carrying a constant `"embedding"` key (round-2 review): `_EMBED_WRITE` needs
    # `embedding =` or `set_embedding_clause(`, and `_writes_an_embedding`'s AST pass inspects
    # ASSIGNMENT targets only (`ast.Assign`/`AugAssign`/`AnnAssign` -> a `Subscript` with a
    # `Constant` slice), so an `ast.Dict` handed to a CALL is invisible to both passes. Measured:
    # the writer below is ABSENT from the map entirely. Two carriers keep it from being
    # hypothetical — `sdk::_session_event_write` and `sdk::_embedding_repair_attempt` emit
    # `**{**…, "embedding": embedding}` into the journaled payload (the first spreads `payload`,
    # the second `props` — so the quoted stem is the shared SPELLING, not a literal to grep) — and
    # BOTH are in the
    # discovered map as `unresolved_write` for a DIFFERENT reason (their bodies also call
    # `set_embedding_clause(`), which is exactly the "visible for another door" case the register
    # draws for `sdk::_journal_event_raw_write`. They are NAMED INSTANCES, not the population: the
    # same blind class also travels as a PARAMS dict literal carrying a constant `"embedding"` key —
    # inline (`params={…}`) in `projection.entities::_event_plain_merge`, `_upsert_subject`,
    # `_upsert_object` and `_upsert_document`, and built into a local then handed on by name
    # (`params = {…}` → `params=params`) in `_upsert_point_props`; the fifth of that family,
    # `_upsert_event`, holds no such literal (it computes the vector and hands it to
    # `_event_plain_merge`) — so a reader must not take two as the
    # complete set.
    "dict_literal_embedding_key": (
        'def w(rec, props, v):\n'
        '    rec._emit_event("X", **{**props, "embedding": v})\n'
    ),
    # A container-key store at MODULE or CLASS scope (round-14 review): the walk visits `def`s only
    # (`probe._embedding_writers` iterates `FunctionDef`/`AsyncFunctionDef`), so the literal-slice
    # AST pass is never applied to it and `_EMBED_WRITE` has no function source to read. It is a
    # further reason a writer can be absent while holding a perfectly DECIDABLE key, so it is
    # pinned here rather than left to the header's prose — BOTH positions, because a widening that
    # reached one of them alone would leave the other described rather than pinned (round-15 review).
    "module_scope_container_key": (
        'REC = {}\nREC["embedding"] = [0.25, 0.5]\n'
    ),
    "class_scope_container_key": (
        'class C:\n    REC = {}\n    REC["embedding"] = [0.25, 0.5]\n'
    ),
}


def _scratch_root(tmp_path: Path, name: str, body: str) -> Path:
    """A `tortoise/`-shaped root the probe can walk — no repo copy, one module file."""
    root = tmp_path / name
    (root / "tortoise").mkdir(parents=True)
    (root / "tortoise" / "m.py").write_text(body, encoding="utf-8")
    return root


def test_the_DISCOVERY_blind_spot_is_pinned_not_merely_documented(tmp_path):
    """`#11274` row 1, in the direction that makes SILENCE distinguishable from coverage.

    The guard above discovers through `probe.embedding_writers()`, whose `_EMBED_WRITE` is a
    source-text regex (plus the AST pass `_writes_an_embedding`). Every entry in `INVISIBLE_SHAPES` is
    invisible to discovery — a SPELLING neither pass can decide, or a POSITION (module/class body)
    the walk never enters — so a writer using one is
    ABSENT from the census — not unclassified, absent — and every other test in this file is green.
    That limitation is stated in the register's module docstring; this test is what keeps the
    statement from rotting:

    * in the direction it is written, a reader can check it;
    * and it reds if discovery's breadth changes in EITHER direction — a widened regex means the
      caveat and the register's population are now stale, and the newly-visible writer must be
      declared.

    Measured here (not asserted from the regex's text): the positive control is detected, and every
    invisible shape below is absent from the map entirely. ⚠️ The count is deliberately NOT stated
    here or in the dict above — it re-staled twice while this PR was being reviewed, and a number
    nobody re-derives is exactly what the suite is meant to catch rather than carry.
    """
    controls = {
        "clause": 'def w(tx, e):\n    tx.query("SET n.embedding = $e")\n',
        # the CONTAINER-KEY shape `#11274` added; if this stops being decided, the widening
        # regressed and the register's population shrank silently.
        "container_key": 'def w(rec, e):\n    rec["embedding"] = e\n',
    }
    for name, body in controls.items():
        detected = probe._embedding_writers(_scratch_root(tmp_path, name, body))
        assert detected, (
            f"the detector found nothing in a scratch module using the {name!r} shape — either "
            "discovery regressed or its root handling changed; repair the detector, do not "
            "delete this control"
        )
    for shape, body in INVISIBLE_SHAPES.items():
        found = probe._embedding_writers(_scratch_root(tmp_path, shape, body))
        assert not found, (
            f"`_EMBED_WRITE` NOW sees the {shape!r} shape ({sorted(found)}) — that is an "
            "improvement, not a failure, but it is a CLAIM CHANGE: update the module docstring's "
            "'What the closure claim does NOT cover' block and this test together, and declare "
            "the writer(s) the widened discovery now finds."
        )


#: The name-collision criterion the register's `duplicate` parenthetical states, one scratch module
#: each. `duplicate` requires BOTH defs to reach `_put`, and `_put` is only called for a def that
#: discovery CLASSIFIES — not for every `def` the walk visits. Data, so the assertion reads as the
#: criterion it measures rather than as three hand-written cases.
DUPLICATE_CRITERION = {
    # positive control: `duplicate` must remain REACHABLE, or the two arms below prove nothing
    "both_defs_classified": (
        'def w(tx):\n    tx.query("SET n.embedding = $e")\n'
        '\ndef w(tx2):\n    tx2.query("SET n.embedding = $e")\n',
        "duplicate",
    ),
    # the collider is a pure READER: never classified, so `_put` is not reached for it and the
    # discovered writer's own kind survives — which is in `NEEDS_A_DECLARATION`, i.e. CAUGHT
    "collider_is_a_reader": (
        'def w(tx):\n    tx.query("SET n.embedding = $e")\n'
        '\ndef w(a):\n    return a + 1\n',
        "unresolved_write",
    ),
    # same pair, orders swapped — the criterion must not be read as order-dependent
    "collider_is_a_reader_reversed": (
        'def w(a):\n    return a\n'
        '\ndef w(tx):\n    tx.query("SET n.embedding = $e")\n',
        "unresolved_write",
    ),
}


def test_the_duplicate_criterion_is_put_reached_not_walk_reached(tmp_path):
    """#11373 P2 — `duplicate` needs BOTH defs to reach `_put`, not merely the walk.

    The register's parenthetical read "two same-named defs in one module, BOTH of them reached by
    the walk", which predicts `duplicate` for ANY same-named pair — `ast.walk` reaches every `def`
    in the module, so the stated criterion was satisfied by the reader collision too. It does not
    hold: `_put` is called only for a def discovery CLASSIFIES, so a colliding def that is a pure
    reader never reaches it, the discovered def's own kind stays in the map, and an
    `unresolved_write`/`ungated` sibling lands INSIDE `NEEDS_A_DECLARATION` — caught, not excused.

    A `#11086` reader taking the old wording as the criterion would believe every collision lands
    in the undeclarable `duplicate` class and miss exactly the case that IS declarable, so the
    wording is pinned here rather than left to the next review round to re-measure by hand. The
    both-writers arm is the control: if `duplicate` stops being reachable the two reader arms stop
    discriminating and this test says so instead of passing vacuously.
    """
    for shape, (body, expected) in DUPLICATE_CRITERION.items():
        found = probe._embedding_writers(_scratch_root(tmp_path, shape, body))
        kinds = {name: entry.get("kind") for name, entry in found.items()}
        assert kinds == {"tortoise.m::w": expected}, (
            f"the {shape!r} collision measured {kinds}, not "
            f"{{'tortoise.m::w': {expected!r}}} — discovery's collision rule changed. The "
            "register's `duplicate` parenthetical and this file's header both state this "
            "criterion; re-derive both before changing either."
        )
