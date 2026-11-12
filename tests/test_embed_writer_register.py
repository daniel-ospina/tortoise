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
enumerates none of that module's writers); when the store sits in a MODULE body or a MODULE-LEVEL
class body, since the walk visits `def`s (and, by subtree, whatever those `def`s contain) — pinned
as `module_scope_container_key` and `class_scope_container_key`
below, with `class_inside_a_def` as the opposite-expectation control that keeps the word
`class` from being read as the boundary. `#11274` owns widening it. ⚠️ Together with the name-collision case below, this enumerates
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
| `test_the_register_is_not_a_second_rule_set` | plan risk R2 — *"the register becomes a second source of truth and drifts from `value_gate.RULES`"* | the register's imports leave `{__future__, collections, re, dataclasses, types}` — every `import` / `from` at EVERY level counts, so a relative `from .x import …` is not exempt — or it names `value_gate`/`embed_gate`/`RULES`/`EMBEDDING_RULES` in code (the two regexes it holds are SHAPE checks — the `#\\d+` `owner_issue` format and the Cypher-label shape `_LABEL` — neither a rule) |
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
| `test_a_stores_declaration_that_does_not_answer_is_refused` (`#11492`) | row 2's question — *where does this writer's vector land?* — is ANSWERED by every declaration, never defaulted | a declaration is constructible without `stores`, or `load()` accepts an empty tuple, a mutable list, a non-label string, or a typo'd disposition (any of which would read as an unserved LABEL and hold a correct tree) |
| `test_every_declared_label_re_derives_at_this_head` (`#11492`) | the declared LABELS are re-derived at HEAD, the `CENSUS_EVIDENCE` discipline one field over | a writer's store clause moves to another label (or the needle stops being in the symbol), so the declaration silently answers about the old one |
| `test_the_two_former_runtime_label_writers_now_own_their_label` (`#11492` → `#11641`) | the two writers that held row 2 at `PARTIAL` now carry an ENFORCED label bound — a `LABELS_AT_RUNTIME` declaration is a DECLARED, reviewable statement, and a label tuple is one only when the writer refuses a store outside it | either seam is reverted (`_require_served_restore_labels` for the dump importer, `_RAW_WRITE_DOOR_LABELS` for the door) and the tuple becomes the fabrication the disposition was invented to refuse — or a writer is declared `LABELS_AT_RUNTIME` again without saying why it has no bound |
| `test_a_no_live_vector_declaration_executes_no_store_clause` (`#11492`) | `NO_LIVE_VECTOR` is a claim about ABSENCE, so it must be checked against the symbol's own body plus one hop of module constants | a `no-live-vector` writer's body (or a constant it names) carries a store clause after all |
| `test_a_writer_that_EXECUTES_a_store_is_never_declared_no_live_vector` (`#11492`) | a door that EXECUTES a caller-supplied statement stores for real, so it can never be declared as storing nothing — and since `#11641` it OWNS its label instead of inheriting it | `sdk::_event_raw_write_door` is declared `NO_LIVE_VECTOR`, or it stops executing the caller's statement |
| `test_a_declared_label_set_covers_every_label_its_own_store_targets` (`#11492` / `#11530`) | the OTHER direction of the label tuple: `stores` answers WHERE, so it may not UNDER-name | a store clause's alias is bound to a label the declaration omits, or an alias it names is bound to no label at all (both measured: `#11530` and `#11492`'s third review). Scoped to the ALIAS the clause names — a body-wide rule false-reds three correct writers that bind labels with no vector |
| `test_every_store_alias_pattern_still_sees_a_live_store` (`#11530`) | every member of `_STORE_ALIAS` is a form the writers really use, so no member can be deleted or rot unnoticed | a form (`set_embedding_clause`, `clear_then_set_prefix`, a literal `.embedding =`) is removed, or stops matching every label-declaring writer — which would silently make the completeness pin above inert |

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
    LABELS_AT_RUNTIME,
    NO_LIVE_VECTOR,
    RECOGNISED_PRODUCER,
    STORES_DISPOSITIONS,
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


def _record(cls: str = EXEMPT, reason: str = "`tortoise.z::_w` reads one", owner: str = "#11085",
            stores: tuple[str, ...] | str = NO_LIVE_VECTOR):
    return WriterClass(cls, reason, owner, stores)


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
    # The refusal's REMEDY is the only instruction the next declaration's author reads, so it
    # must name the field that is now REQUIRED. `stores` (#11492) is defaultless: a remedy of
    # `WriterClass(cls, reason, "#N")` raises a bare TypeError at module import instead of this
    # register's designed, NAMED refusal — the failure mode the message exists to prevent.
    assert "stores" in str(excinfo.value), (
        "the refusal tells an author how to declare a writer; `stores` is required, so a remedy "
        f"that omits it cannot be followed: {str(excinfo.value)!r}"
    )
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


#: One scratch module per KIND discovery can emit, with the population verdict the guard's own
#: scope (`NEEDS_A_DECLARATION`) must give it. Data, so the pin reads as the criterion it measures.
DECLARATION_OWED_BY_KIND: dict[str, tuple[str, bool, str]] = {
    "unresolved_write": (
        'def w(tx):\n    tx.query("SET n.embedding = $e")\n',
        True,
        "a writer whose embedding store is real and whose chain is outside the vocabulary",
    ),
    "ungated": (
        "def w(rows, tx):\n"
        '    tx.query("SET n.embedding = $e")\n'
        "    return compute_session_embedding(rows)\n",
        True,
        "a vector produced through an ungated primitive AND stored — the row-1 gap itself",
    ),
    "gated": (
        "def w(text):\n    return encode_for_row(text, 384)\n",
        False,
        "the gate DECIDES this writer's write, so a declaration would be a claim about a path the "
        "rule already covers",
    ),
    "producer": (
        "def w(rows):\n    return encode_for_store(rows)\n",
        False,
        "it computes a vector another (declared) writer stores; declaring it would double-count",
    ),
    "duplicate": (
        'def w(tx):\n    tx.query("SET n.embedding = $e")\n'
        '\ndef w(tx2):\n    tx2.query("SET n.embedding = $e")\n',
        False,
        "a `duplicate` row is an UNRESOLVED kind the register cannot classify — a different defect, "
        "pinned by `test_every_writer_the_instrument_finds_outside_the_gate_is_declared`'s own arm",
    ),
}


def test_NEEDS_A_DECLARATION_is_the_scope_the_guard_is_scored_on(tmp_path):
    """`#11274`: the guard's SCOPE, pinned — dropping a kind must RED, not shrink the work.

    `test_every_writer_the_instrument_finds_outside_the_gate_is_declared` iterates
    `_writers_outside_the_gate()`, which filters discovery's map by `NEEDS_A_DECLARATION`. So that
    tuple IS the guard's scope, and a narrower one reds nothing: it just scores fewer writers.
    ⛔ Measured before this pin: deleting `"unresolved_write"` from it left every test in this file
    green — the whole suite would have certified a guard measuring 9 of the population.
    (`#11274`'s own acceptance names this mutation, so the absence of the pin is what the issue's
    verification bullet had not yet bought.)

    Both DIRECTIONS are driven, each against the real discovery filter on a scratch module — the
    membership assertions are not enough on their own, because a kind could be in the tuple while
    discovery never emits it (a dead name), or emitted by discovery and not in the tuple (a hole).
    """
    assert set(NEEDS_A_DECLARATION) == {"unresolved_write", "ungated"}, (
        f"the guard's scope is {NEEDS_A_DECLARATION!r} — this tuple is the population every "
        "'an undeclared writer cannot bypass silently' claim is scored on, so a change here is a "
        "CLAIM change: state it in the register and re-derive the guard with it"
    )
    owed: set[str] = set()
    for kind, (body, expected, why) in DECLARATION_OWED_BY_KIND.items():
        found = probe._embedding_writers(_scratch_root(tmp_path, kind, body))
        assert found, f"the {kind!r} scratch module produced no writer at all: {why}"
        for key, entry in found.items():
            scored = entry["kind"] in NEEDS_A_DECLARATION
            assert scored is expected, (
                f"{kind}: discovery classed {key} as {entry['kind']!r} and the guard scores that "
                f"kind {scored}, expected {expected} — {why}")
            if scored:
                owed.add(key)
    assert owed, "no kind reached the guard's population — the pin proves nothing"

    # ⛔ The assertion that used to sit here ("`outside` equals the instrument's
    # `NEEDS_A_DECLARATION` members") was a TAUTOLOGY: `_writers_outside_the_gate()` IS that
    # comprehension over the same call, so it could not fail while claiming to establish that "the
    # population the guard derives on the real tree is the one it scores, not a subset of its own
    # kinds" (`#12020` round 5). Do not re-add it in that form. What can fail is what follows — the
    # population is non-empty, and every member is DECLARED — and what the kind→scored direction is
    # carried by is the per-shape pins above, each asserted against a real scratch module rather
    # than against set arithmetic over a derived population.
    outside = _writers_outside_the_gate()
    assert outside, "the outside-gate population is empty — re-measure the guard, do not pass it"
    undeclared = sorted(k for k in outside if not register.is_declared(k))
    assert not undeclared, (
        f"the guard's own scope contains writers the register does not declare: {undeclared} — "
        "the silent-bypass refusal is what this population exists for")


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
      instances are dispositioned, each in its own way instead of being read as "no write":
      `sdk::_update_entity` is DECLARED here under the named `#4094` exemption, and
      `sdk::update_point` merges through `n += $props` while also calling `encode_for_row`, so
      discovery classes it `gated` and it owes no declaration. ⛔ Singular wording here was the
      `#11398` defect: one instance named for a shape that has two reads as "this is the whole
      population". That is the difference between a scoped claim and a slogan.
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
    # ⛔ TWO instances, dispositioned DIFFERENTLY, and both named in the published Blind spot note
    # (`#11398`): the singular wording read as "this is the whole population", while the second
    # instance — `sdk::update_point`, `n += $props` in its promote arm — is `gated` because it also
    # calls the seam, so it owes (and carries) no declaration. Re-derived from the instrument here
    # rather than trusted from the prose.
    shape_note = probe.UNDECIDABLE_WRITE_SHAPES["map-merge (`SET n += $p`)"]
    for instance in ("_update_entity", "update_point"):
        assert instance in shape_note, (
            f"the map-merge blind-spot note names no {instance} — a live instance a reader of the "
            f"row cannot find is the #11398 defect: {shape_note!r}")
    assert not register.is_declared("tortoise.sdk::update_point"), (
        "a `gated` writer owes no declaration, and declaring one would misreport WHERE its vector "
        "lands as an outside-gate judgement")
    assert probe.embedding_writers()["tortoise.sdk::update_point"]["kind"] == "gated", (
        "the second map-merge instance is only dispositioned as 'gated, declared nowhere' while it "
        "classes that way — re-derive it instead of asserting the sentence")
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


def _writer_function_source(at: str) -> str:
    """A writer's own text, by the LINE RANGE `probe._embedding_writers` uses.

    Mirrors the probe's extraction on purpose rather than calling
    `ast.get_source_segment`: the probe joins the node's line range because
    re-splitting the whole file per call cost 90 s on this tree, and a
    re-derivation that read the source a different way could disagree with the
    instrument for a reason that has nothing to do with the claim under test.
    """
    rel, line = at.rsplit(":", 1)
    target = int(line)
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    lines = source.splitlines()
    enclosing = None
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.lineno <= target <= (node.end_lineno or node.lineno)
            # The INNERMOST enclosing def, so a nested writer is not read as its parent.
            and (enclosing is None or node.lineno > enclosing.lineno)
        ):
            enclosing = node
    assert enclosing is not None, (
        f"no function encloses {at} — discovery's `at` does not resolve, so this arm cannot "
        "re-derive anything from it"
    )
    return "\n".join(lines[enclosing.lineno - 1:enclosing.end_lineno])


def test_the_AST_bought_population_is_the_one_the_register_names():
    """The register's OTHER population sentence, held to the standard it applies 30 lines above.

    The bullet closes with "the writers whose membership the AST pass actually bought are
    `api::add_point`, `consistency::_graph_fingerprint`, `sdk::_index_gate_read` and
    `sdk::_write_capture_turns` (the four named at the start of this bullet), and nothing else".
    Nothing re-derived it — the four names appeared in a docstring and nowhere else — so a FIFTH
    container-key-only writer would have been discovered, forced to declare, and this "and nothing
    else" would have silently become false. That is exactly the rot the enumeration one line above
    is pinned against, and the register's own argument for pinning it ("a pin can read it, not
    rewrite it; the red is what makes the edit unavoidable") applies here unchanged.

    `_writes_an_embedding` is `_EMBED_WRITE` OR the container-key AST arm, so of the
    `unresolved_write` population (no recognised producer call, so membership rests on that
    predicate) the writers the AST arm BOUGHT are exactly those the regex does not match. Re-derived
    from the instrument at HEAD rather than stated, and reding in BOTH directions: a member the
    sentence loses, and a name the sentence keeps whose membership the regex already explained.
    """
    doc = register.__doc__ or ""
    anchor = "the writers whose membership the AST pass actually bought are"
    start = doc.find(anchor)
    assert start != -1, (
        "the register no longer separates the writers the AST pass bought from the ones that "
        f"merely hold the shape for another reason: {anchor!r}"
    )
    # The same token reader the CALL-population arm uses: the enumeration is the maximal run of
    # backticked `module::symbol` tokens after the anchor, and `(the four named …)` ends it.
    separator = re.compile(r"[\s,:()]*(?:and\s+)?`([A-Za-z_][\w.]*)::([A-Za-z_]\w*)`")
    stated: list[tuple[str, str]] = []
    position = start + len(anchor)
    while match := separator.match(doc, position):
        stated.append((match.group(1), match.group(2)))
        position = match.end()
    assert stated, (
        f"the sentence after {anchor!r} names no writer: {doc[position:position + 120]!r}"
    )

    bought = {
        key for key, row in probe.embedding_writers().items()
        if row["kind"] == "unresolved_write"
        and not probe._EMBED_WRITE.search(_writer_function_source(row["at"]))
    }
    assert bought, (
        "no writer owes its membership to the container-key AST arm — the sentence this test reads "
        "has lost its subject, or discovery stopped deciding that shape on the AST"
    )

    def matches(key: str, module: str, symbol: str) -> bool:
        writer_module, writer_symbol = key.split("::", 1)
        return writer_symbol == symbol and (
            writer_module == module or writer_module.endswith(f".{module}")
        )

    unstated = sorted(key for key in bought if not any(matches(key, *t) for t in stated))
    assert not unstated, (
        "the register says these are the writers the AST pass bought `and nothing else` — bought "
        f"and not in the sentence: {unstated} (named: {stated})"
    )
    overstated = sorted(t for t in stated if not any(matches(k, *t) for k in bought))
    assert not overstated, (
        "the sentence names a writer whose membership the AST arm did NOT buy — `_EMBED_WRITE` "
        f"already explains it, so it is not one the AST pass bought: {overstated}"
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


def _resolve_module(module: str, root: Path | None = None) -> Path | None:
    """The file a dotted module (or a repo-relative `.py` path) names, or None.

    `root` exists so the resolution RULES can be pinned against synthesized trees — a real repo
    path can only exhibit the case that happens to exist in this tree, and two of the three rules
    below have no live instance.
    """
    root = REPO_ROOT if root is None else root
    if module.endswith(".py"):
        for candidate in (root / module, root / "tortoise" / module):
            if candidate.is_file():
                return candidate
        return None
    base = root.joinpath(*module.split("."))
    # ⛔ A PACKAGE wins over a same-named MODULE. `tortoise/projection.py` is a DEPRECATED shim that
    # re-exports the package's names, so resolving `.py` first did two wrong things at once (round-17
    # review): a dotted `tortoise.projection::<symbol>` citation passed on the shim's re-export line
    # `_apply_one = _pkg._apply_one` — a false PASS, since deleting the symbol from the package would
    # not red it — and a symbol only the PACKAGE defines read as moved — a false RED.
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.is_file():
            return candidate
    return None


def test_the_module_resolver_rules_are_pinned_against_synthesized_trees(tmp_path):
    """The resolver's three rules, each driven by a scratch tree — not by repo paths.

    ⛔ WHY THIS EXISTS (round-19 review). Round 17 deleted an arm asserting
    `_resolve_module("tortoise.config") == tortoise/config.py` on the grounds that no resolver edit
    could move it. That was FALSE — an edit dropping the module candidate moves it — and round 18's
    replacement reason ("every such edit reds the citation loop first") was false too: a resolver
    edit that drops the `.py` fallback *only where a same-named non-package directory exists* moves
    `tortoise.config` and reds NOTHING, because nothing in `_DECLARATIONS` cites it. Driving the
    rules from scratch trees makes each one killable by construction, and removes the dependence on
    which paths this repo happens to contain (the reason `tortoise/config/__init__.py` cannot be
    added: `tests/conftest.py` fails at import, `cannot import name 'RELATIVE_PATH_ERROR'`).
    """
    root = tmp_path / "root"
    (root / "tortoise" / "pkg").mkdir(parents=True)
    (root / "tortoise" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tortoise" / "pkg.py").write_text("", encoding="utf-8")            # the deprecated shim
    (root / "tortoise" / "mod.py").write_text("", encoding="utf-8")
    (root / "tortoise" / "bare").mkdir()                                        # dir, NO `__init__.py`
    (root / "tortoise" / "bare.py").write_text("", encoding="utf-8")

    # (1) a PACKAGE wins over a same-named module — the shim must not answer for the package
    assert _resolve_module("tortoise.pkg", root) == root / "tortoise" / "pkg" / "__init__.py", (
        "a package must win over a same-named module — resolving `.py` first lets a deprecated shim's "
        "re-export line satisfy a citation that the package no longer defines")
    # (2) a module-only name resolves to the module
    assert _resolve_module("tortoise.mod", root) == root / "tortoise" / "mod.py", (
        "a dotted name with no package must resolve to its module")
    # (3) a DIRECTORY that is not a package (no `__init__.py`) does not shadow the module, and is
    # not itself resolvable — this is the `tortoise.config` shape, which round 17's deleted arm was
    # the only thing exercising. Pinned here so the rule survives an edit that special-cases a
    # non-package directory, which is exactly the edit that used to move `tortoise.config` alone.
    assert _resolve_module("tortoise.bare", root) == root / "tortoise" / "bare.py", (
        "a directory without `__init__.py` is not a package, so the module of the same name must "
        "still answer")
    # (4) …and a name with neither a package nor a module is None, not a directory
    assert _resolve_module("tortoise.absent", root) is None, (
        "a dotted name with nothing behind it must be None — a non-package directory is not a module")
    # (5) the `.py`-path form resolves under the root, and under `tortoise/`
    assert _resolve_module("tortoise/mod.py", root) == root / "tortoise" / "mod.py", (
        "a repo-relative `.py` citation must resolve as written")
    assert _resolve_module("mod.py", root) == root / "tortoise" / "mod.py", (
        "a bare `x.py` citation must also be tried under `tortoise/`")


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
        # ⛔ `#11274`: the citation must include the writer's OWN module, not only a sibling it
        # happens to reach. A reason made ONLY of other-module citations resolves fine and says
        # nothing about the writer's own body — which is the half every other pin in this file reads.
        # Asserted per ENTRY (at least one citation resolves to the key's own module), because a
        # legitimate reason cites helpers elsewhere too (`_update_entity` cites
        # `_CANONICAL_ENTITY_ID_PROPS` in `tortoise.projection`).
        own = _resolve_module(key.split("::", 1)[0])
        assert own is not None and own in {
            _resolve_module(module) for module, _symbol in cited
        }, (
            f"{key} cites {[f'{m}::{s}' for m, s in cited]} and NONE of them is the writer's own "
            f"module — the entry's evidence must be reachable from the writer it declares"
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
    # ⛔ NO second arm on `tortoise.config` (round-17 review; reason corrected round-19). It was
    # written as "the module is the only candidate", and both earlier justifications were FALSE:
    # round 17 said "no edit to the resolver can move it" (an edit dropping the module candidate
    # moves it), and round 18 said "every such edit reds the citation loop above first" (an edit that
    # drops the `.py` fallback only where a same-named NON-package directory exists moves
    # `tortoise.config` and reds nothing — nothing in `_DECLARATIONS` cites it). The true reason the
    # arm is gone is that the RESOLUTION RULES are now pinned directly, against synthesized trees, by
    # `test_the_module_resolver_rules_are_pinned_against_synthesized_trees` — so an arm tied to which
    # paths this repo happens to contain adds no killability. (That path cannot be added here in any
    # case: gaining `tortoise/config/__init__.py` fails `tests/conftest.py` at import,
    # `cannot import name 'RELATIVE_PATH_ERROR'`.)


def test_a_stores_declaration_that_does_not_answer_is_refused():
    """`#11492`: every declaration answers row 2's question, and `load()` refuses a shrug.

    Row 2 is *"is every stored vector on a label the query leg can serve?"*, and the one fact it
    needs — WHERE the vector lands — cannot be derived for most writers (15 of the 20 outside-gate
    writers name no usable label in their own Cypher, because a store is not always a
    `MERGE|CREATE (:Label` in the writer's own body: the label may sit in a `MATCH` the derivation
    does not credit, in a module-level constant, behind a property-map merge, behind a store
    delegated to another writer (`_index_process_unit`), in a writer that only BUILDS a clause
    (`set_embedding_clause`), or in the caller's own statement — and those are a SAMPLE: the body may
    hold no store at all (a read, a read into a dict, or comment prose — `_compare_views`,
    `_graph_fingerprint`, `turn_prior_vectors`, `_index_gate_read`, `_apply_one`), carry a value a
    later pass stores (`add_point`, `_journal_event_raw_write`), quote a wipe it does not own
    (`_prewipe_synthetic_write_scope`, whose whole body is a `return` — the `SET n.embedding = NULL`
    it quotes is `_rebuild_all_replay`'s, and that writer is NOT among these fifteen), or interpolate
    the label at runtime (`restore_graph`)), so it is DECLARED. The refusals below are what keeps that
    declaration from degrading into a default: an empty tuple ("unspecified") and a missing field
    would both read to the grader as *nothing to compare*, i.e. a silent pass for the one writer
    nobody checked.

    ⛔ The directions are not symmetric and the message says so: a label that is not a Cypher label
    can never match the served set (the false-hold direction), while an empty tuple or a non-tuple
    is a declaration that does not answer (the false-pass direction). An out-of-vocabulary string is
    neither: `tools/dod_5090_probe.py::_store_reading` refuses it independently and records it in
    `violations`, so row 2 holds at `PARTIAL` and names it — not a `NOT MET`.
    """
    # ⛔ No default: the field is REQUIRED, so a new declaration cannot inherit a safe-looking
    # answer. This is the same pin `test_the_DECLARATIONS_tuple_…` puts on the frozen map.
    with pytest.raises(TypeError):
        WriterClass(EXEMPT, "`tortoise.z::_w` reads one", "#11085")  # type: ignore[call-arg]
    for stores in ((), [], ["Event"], "Event", "", "exmpt", ("Event", ""), ("Ev ent",),
                   ("Event", "Event"), ("Event", 3), ("Event\n",), ("Event ",)):
        with pytest.raises(ValueError):
            register.load([("tortoise.z::_w", _record(stores=stores))])
    # …and every admissible shape still loads, so the refusal is not a blanket one.
    for stores in (("Event",), ("Point", "Subject"), NO_LIVE_VECTOR, LABELS_AT_RUNTIME):
        assert register.load([("tortoise.z::_w", _record(stores=stores))])[
            ("tortoise.z::_w")].stores == stores
    # Every LIVE entry states one of them — the population-wide form of the same pin.
    for key, entry in EMBED_WRITER_CLASSES.items():
        if isinstance(entry.stores, str):
            assert entry.stores in STORES_DISPOSITIONS, (key, entry.stores)
        else:
            assert entry.stores and all(register._LABEL.match(label) for label in entry.stores), (
                key, entry.stores)


#: `#11492` — WHERE each declared vector lands, as re-derivable evidence:
#: `(rel_path, symbol, needle, labels)`. The needle is the store clause itself (or the label constant
#: the clause is built from) and `labels` is the SET OF LABELS THAT CLAUSE NAMES, so the declaration
#: is compared against the evidence rather than against a second copy of itself: ⛔ `#11492`'s review
#: proved the first version — needles with no label set — PASSED with a declaration mutated to
#: `("Batch",)`, i.e. it checked that the store still existed and never that the declaration still
#: described it. A declaration edited to an unserved label would have read `MET` while the vector
#: landed elsewhere, which is #5090 row 2's falsifier inverted.
#:
#: ⛔ Only writers declared with a label tuple appear here: a `NO_LIVE_VECTOR` entry cannot be proven
#: by a needle (that is a claim about ABSENCE, and data flow — `UNDECIDABLE_WRITE_SHAPES`), and
#: `LABELS_AT_RUNTIME` is precisely the entry with no static label to point at. Both are product
#: judgements the register owns; the `stores` field is where a reviewer reads them.
LABEL_EVIDENCE: dict[str, tuple[str, str, str, frozenset[str]]] = {
    # The label and the vector clause in ONE statement.
    "tortoise.projection.entities::_event_plain_merge": (
        "tortoise/projection/entities.py", "_event_plain_merge",
        'MERGE (e:Event {eventId: $eid})', frozenset({"Event"})),
    "tortoise.sdk::_embedding_repair_attempt": (
        "tortoise/sdk.py", "_embedding_repair_attempt",
        'MATCH (e:Event {eventId:$eid})', frozenset({"Event"})),
    "tortoise.sdk::_write_capture_turns": (
        "tortoise/sdk.py", "_write_capture_turns",
        "t.embedding=CASE WHEN turn.emb IS NOT NULL THEN vecf32(turn.emb)",
        frozenset({"Point"})),
    # The label is in the enclosing statement, the vector in the shared clause: the needle is the
    # STORE STATEMENT, and the alias letter it passes is what ties the two together.
    "tortoise.sdk::_session_event_write": (
        "tortoise/sdk.py", "_session_event_write",
        '"MERGE (e:Event {eventId:$eid}) "', frozenset({"Event"})),
    "tortoise.sdk::ingest_corpus": (
        "tortoise/sdk.py", "ingest_corpus",
        '"SET e += $props, " + set_embedding_clause("e")', frozenset({"Event"})),
    "tortoise.session_indexer::main": (
        "tortoise/session_indexer.py", "main",
        '"SET e += $props, " + set_embedding_clause("e")', frozenset({"Event"})),
    # The two where the label is NOT the enclosing word: `Point` in the MATCH of the restore
    # (`:Batch`/`:Session` appear in the same body but carry no vector — the pre-wipe snapshot),
    # and the canonical-label constant for the generic update door (five labels, one tuple).
    "tortoise.projection::_rebuild_all_replay": (
        "tortoise/projection/__init__.py", "_rebuild_all_replay",
        '"MATCH (n:Point {id:$pid}) " + restore_guard', frozenset({"Point"})),
    # The needle names the per-label LOOP, not the loop's variable: `#11836`
    # renamed the unused binding `prop` -> `_prop` and the name-sensitive
    # `"for label, prop in …"` form redded on unchanged store evidence. What the
    # declaration is derived FROM is the iteration over the canonical constant
    # (the `SET n += $p` map-merge inside it is the store), so the variable is
    # not part of the claim and a rename is not a store move.
    # The code site states the same thing in its own terms, and says why the rename
    # cannot reach the write: the primary key is derived THROUGH
    # `_entity_identity_probes()` and never by naming the binding, and the same
    # rename once turned the guarded path into a `NameError` with every test green
    # (`#11836`, VGATE round 13). So a guard that reds on the rename is measuring
    # the rename, not the store — and its message ("the store moved") is a wrong
    # diagnosis of a declaration that is still true.
    # ⛔ `#11954`: and the needle is only the SEARCH KEY, never the proof. `#11936`
    # shortened it to `"in _CANONICAL_ENTITY_ID_PROPS:"`, which is a SUBSTRING:
    # it said the text occurs in the body, not which statement carries it, so a
    # re-targeted loop plus one surviving `if label in _CANONICAL_ENTITY_ID_PROPS:`
    # kept this row green with the store moved (the issue carries the run). The
    # row is therefore also a STRUCTURAL claim, declared in `ITERATED_STORE_CLAIM`
    # and proved on the AST: a `for` whose ITERABLE is that constant, whose body
    # stores, and whose binding the store clause is built from. Both arms must
    # hold — the statement arm (`_carrying_statements`) and the iterated one —
    # which is what a rename of the binding still passes and a re-target cannot.
    "tortoise.sdk::_update_entity": (
        "tortoise/sdk.py", "_update_entity",
        "in _CANONICAL_ENTITY_ID_PROPS:",
        frozenset({"Point", "Subject", "Object", "Source", "Event"})),
    # `#11641`: the two writers whose label used to be decided at RUNTIME, so row 2 could not
    # compare them to the served set. Each now OWNS the label at its own seam, and the needle is
    # that seam — not the store clause, which is deliberately un-nameable in both writers (the dump
    # interpolates its labels; the door executes its caller's statement).
    "tortoise.hosted_backup::restore_graph": (
        "tortoise/hosted_backup.py", "restore_graph",
        "_require_served_restore_labels(",
        frozenset({"Point", "Subject", "Object", "Source", "Event"})),
    "tortoise.sdk::_event_raw_write_door": (
        "tortoise/sdk.py", "_event_raw_write_door",
        "_declared_labels = {label for label, _id_prop in _RAW_WRITE_DOOR_LABELS}",
        frozenset({"Event"})),
}

#: The label sets the label CONSTANT names, cited by the entries that store through them.
#: `(rel_path, symbol, labels)` — re-derived like `CENSUS_EVIDENCE`, so a canonical label added to
#: `_CANONICAL_ENTITY_ID_PROPS` reds instead of silently widening what `_update_entity` declares.
LABEL_CONSTANTS: dict[str, tuple[str, str, frozenset[str]]] = {
    "tortoise.sdk::_update_entity": (
        "tortoise/projection/__init__.py", "_CANONICAL_ENTITY_ID_PROPS",
        frozenset({"Point", "Subject", "Object", "Source", "Event"})),
    # `#11641`. Both writers store through a label that no static alias rule can attribute — the
    # restore interpolates the dump's labels, the door executes the caller's statement — so the
    # completeness claim is carried by a CONSTANT the seam itself enforces.
    "tortoise.hosted_backup::restore_graph": (
        "tortoise/hosted_backup.py", "_VECTOR_RESTORE_LABELS",
        frozenset({"Point", "Subject", "Object", "Source", "Event"})),
    "tortoise.sdk::_event_raw_write_door": (
        "tortoise/sdk.py", "_RAW_WRITE_DOOR_LABELS",
        frozenset({"Event"})),
}


#: Rows whose needle is a SEAM THAT BOUNDS the label set rather than a store clause, because the
#: store is deliberately un-nameable in their own body (`restore_graph` interpolates its labels,
#: `_event_raw_write_door` executes its caller's). Their COMPLETENESS therefore rests on a constant,
#: so every member here must also appear in `LABEL_CONSTANTS` — asserted, not implied.
#: Rows whose store is carried by a MAP MERGE (`SET n += $p`) rather than a nameable vector clause.
#: Exactly one row, and it is the row the probe itself classifies as undecidable — the exemption this
#: row already relies on (`#4094`) is what makes the weaker evidence legitimate. ⛔ Do not grow this
#: set: a row added here stops redding when its vector clause is deleted (measured, `#12020` review
#: round 5 Finding 2), so writing a vector clause is always the better evidence.
MERGE_STORE_CLAIM = frozenset({
    "tortoise.sdk::_update_entity",
})


def _claim_for(key: str) -> str:
    """The DECLARED claim kind for a `LABEL_EVIDENCE` row.

    One definition, because the pin and its falsifiers must agree: when the falsifier hard-coded
    `claim` it defaulted to `store` for the one `merge` row and its OWN control failed, which is the
    drift this function prevents (`#12020` review round 5).
    """
    if key in BOUND_LABEL_EVIDENCE:
        return "bound"
    if key in ASSEMBLED_LABEL_EVIDENCE:
        return "assembly"
    if key in MERGE_STORE_CLAIM:
        return "merge"
    return "store"

BOUND_LABEL_EVIDENCE = frozenset({
    "tortoise.hosted_backup::restore_graph",
    "tortoise.sdk::_event_raw_write_door",
})

#: Rows whose store clause is ASSEMBLED across neighbouring statements rather than issued in the one
#: that names the label (`_rebuild_all_replay` names `:Point` in the query statement while
#: `clear_then_set_prefix` / `set_embedding_clause` build the `SET` fragment one statement earlier).
#: Their claim is therefore "the label statement and a store are the same BLOCK's" — walked up from
#: the carrier, NEVER out of the cited symbol, so a store in another function cannot satisfy it.
ASSEMBLED_LABEL_EVIDENCE = frozenset({
    "tortoise.projection::_rebuild_all_replay",
})

#: Rows whose claim is STRUCTURAL, not textual: the constant the writer's own loop ITERATES, and
#: whose binding the store is built from (`#11954`). A needle cannot carry this claim — a membership
#: test on the same constant is one good-faith refactor away and reads identically to a substring —
#: so the row is proved on the AST (`_iterated_store_problem`): a `for` over this `Name` whose body
#: stores through the loop's target.
ITERATED_STORE_CLAIM: dict[str, str] = {
    "tortoise.sdk::_update_entity": "_CANONICAL_ENTITY_ID_PROPS",
}


def test_every_declared_label_re_derives_at_this_head() -> None:
    """`#11492`: a declared LABEL is re-derived at HEAD — the `CENSUS_EVIDENCE` discipline.

    The `stores` field is what `#5090` DoD row 2 is scored on, so it must rot loudly: a writer
    re-targeted to another label, a store clause that stopped being inside the named symbol, or a
    DECLARATION that drifts off the label its own store names — all three red here. ⛔ The third is
    the one `#11492`'s review caught missing: the first version asserted only that the needle was
    still in the body, so a declaration mutated to `("Batch",)` passed. The label set is now part of
    the evidence — but note WHICH half each pin closes, because `evidence_labels` above is a
    hand-written tuple and comparing it to the declaration only proves they were written
    consistently: THIS pin proves every DECLARED label still exists in the writer, and
    `test_a_declared_label_set_covers_every_label_its_own_store_targets` proves the opposite
    direction (no label the writer's own store clause targets is missing) by deriving it from the
    alias that clause passes — the half a hand-written set cannot close on its own (`#11530`).
    """
    # ⛔ A STRUCTURAL claim is data (`#11954`), so an entry that outlives its row, or an emptied
    # table, must red here rather than quietly widening the statement arm back to a substring.
    assert ITERATED_STORE_CLAIM, "no row carries an iterated-store claim — #11954's arm is unowned"
    stale = sorted(set(ITERATED_STORE_CLAIM) - set(LABEL_EVIDENCE))
    assert not stale, f"ITERATED_STORE_CLAIM names rows that no longer exist: {stale}"
    unoffered = sorted(set(BOUND_LABEL_EVIDENCE | ASSEMBLED_LABEL_EVIDENCE)
                       - set(LABEL_EVIDENCE))
    assert not unoffered, f"a claim-kind table names rows that no longer exist: {unoffered}"
    labelled = {key for key, entry in EMBED_WRITER_CLASSES.items()
                if not isinstance(entry.stores, str)}
    assert labelled, "no declaration names a label — row 2 would resolve nothing"
    assert set(LABEL_EVIDENCE) == labelled, (
        "every label-tuple declaration needs re-derivable evidence, and evidence for a declaration "
        f"that no longer names labels is stale: missing={sorted(labelled - set(LABEL_EVIDENCE))} "
        f"stale={sorted(set(LABEL_EVIDENCE) - labelled)}"
    )
    for key, (rel, symbol, needle, evidence_labels) in sorted(LABEL_EVIDENCE.items()):
        declared = EMBED_WRITER_CLASSES[key].stores
        assert set(declared) == set(evidence_labels), (
            f"{key} declares stores={declared} while its evidence names {sorted(evidence_labels)} "
            "— a declaration that disagrees with its own store clause is what row 2 is scored on"
        )
        path = REPO_ROOT / rel
        assert path.is_file(), (key, rel)
        source = path.read_text(encoding="utf-8")
        # ⛔ `#11954`: the STATEMENT that carries the claim, not "this text occurs". A substring
        # proves the needle is somewhere in the body, so a re-targeted loop plus one surviving
        # membership test kept this row green while the store had moved (the issue reproduced it,
        # and `test_a_RETARGETED_loop_and_a_moved_store_both_FAIL_the_row` drives both directions).
        violation = _evidence_violation(
            source, symbol, needle, iterated=ITERATED_STORE_CLAIM.get(key),
            claim=_claim_for(key))
        if key in BOUND_LABEL_EVIDENCE:
            # A bound row's completeness IS the constant's label set, so the constant arm below has
            # to exist for it — otherwise "the writer is bounded" is a claim with no derivation.
            assert key in LABEL_CONSTANTS, (
                f"{key} declares its labels from a BOUND, so its label set is re-derived by "
                "LABEL_CONSTANTS — and it is absent there, leaving the declaration unowned"
            )
        assert violation is None, (
            f"{key} declares stores={declared} but {rel}::{symbol} no longer carries {needle!r} "
            f"as a statement that stores — {violation}; the store moved, so the declaration is "
            "stale"
        )
    # …and the label CONSTANTS those clauses are built from must still name EXACTLY the same
    # labels — in BOTH directions. ⛔ The one-directional form ("each declared label still
    # appears in the constant") is NOT the property: a label ADDED to the constant widens the
    # set of labels the writer STORES on, while the declaration keeps naming the old ones, so
    # row 2 would never see the new label as an orphan. That is a false PASS — the exact failure
    # `#11492` exists to remove — and the set comparison below is what closes it (mutation: adding
    # `("Batch", "id")` to `_CANONICAL_ENTITY_ID_PROPS` left this test green before it).
    for key, (rel, symbol, labels) in sorted(LABEL_CONSTANTS.items()):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        lines = source.splitlines()
        # BOTH statement shapes, on purpose: this test's own `constants` map above deliberately
        # accepts `ast.Assign` as well as `ast.AnnAssign`, and a constant that only DROPS its type
        # annotation is the SAME constant. Recing on the annotation is a false hold, and it names
        # the wrong cause ("that file does not define it at module level") — verified by mutation.
        assigned = [
            node for node in ast.parse(source).body
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == symbol
            ) or (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == symbol for t in node.targets)
            )
        ]
        defined = "\n".join(
            "\n".join(lines[node.lineno - 1:node.end_lineno]) for node in assigned
        )
        assert defined, f"{key} cites {rel}::{symbol}, which that file does not define at module level"
        # The constant is a tuple of `(label, id-prop)` pairs; each pair's FIRST element is a label.
        identified = {
            element.elts[0].value
            for node in assigned
            if isinstance(node.value, ast.Tuple)
            for element in node.value.elts
            if isinstance(element, ast.Tuple) and element.elts
            and isinstance(element.elts[0], ast.Constant)
            and isinstance(element.elts[0].value, str)
        }
        assert set(EMBED_WRITER_CLASSES[key].stores) == set(labels), key
        assert identified == set(labels), (
            f"{rel}::{symbol} names {sorted(identified)} while {key} declares "
            f"{sorted(EMBED_WRITER_CLASSES[key].stores)} — the two must be the SAME set, in BOTH "
            f"directions: a label ADDED to the constant is one the writer STORES on and the "
            f"declaration must name it {sorted(identified - set(labels))}, while a label DROPPED from "
            f"the constant means the declaration names a store the code no longer has "
            f"{sorted(set(labels) - identified)}"
        )


def test_a_RETARGETED_loop_and_a_moved_store_both_FAIL_the_row():
    """`#11954`'s falsifier, DRIVEN: the mutation the substring arm passed must now RED.

    The defect this closes is precise, and it is not "the needle is wrong": it is that a needle
    proves **the text occurs somewhere in the body**, never **which statement carries it**. So a
    refactor that re-targets the loop and leaves the same text in any other statement kept the row
    green while the store it declares had moved away — reproduced on `origin/main` + `#11936` with
    the whole module green (`#11954` carries the run).

    Each arm below is a SOURCE MUTATION of the real `tortoise/sdk.py`, applied in memory, because a
    guard that can only be checked against the tree's current content cannot be checked at all once
    the content is right. The control (the unmutated source) must pass, and it is asserted FIRST so a
    mutation that quietly stopped applying cannot pass as a red.
    """
    key = "tortoise.sdk::_update_entity"
    rel, symbol, needle, _labels = LABEL_EVIDENCE[key]
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    iterated = ITERATED_STORE_CLAIM[key]

    def violation(text: str) -> str | None:
        return _evidence_violation(text, symbol, needle, iterated=iterated, claim=_claim_for(key))

    # CONTROL — the tree as it stands carries the claim, both arms.
    assert violation(source) is None, violation(source)
    # …and the claim really is in this source, so every mutation below starts from a green row.
    assert source.count(needle) >= 1, needle

    loop = "            for label, _prop in _CANONICAL_ENTITY_ID_PROPS:\n"
    assert source.count(loop) == 1, "the cited loop is no longer in this shape — re-derive the pin"

    # (1) `#11954`'s reproduction, verbatim: the loop re-targeted, the needle preserved in an
    # unrelated `if`. ⛔ The old substring arm reads this as GREEN — asserted, so this test carries
    # the regression direction rather than describing it.
    retargeted = source.replace(
        loop,
        "            if label in _CANONICAL_ENTITY_ID_PROPS:\n"
        "                pass\n"
        "            for label, _prop in _SOME_OTHER_PROPS:\n",
        1,
    )
    assert retargeted != source
    assert needle in _without_comments(retargeted), (
        "the mutation did not preserve the needle — the arm below would red for the wrong reason")
    assert violation(retargeted) is not None, (
        "a RE-TARGETED loop still satisfies the row: this is the #11954 defect, unfixed")

    # (1b) THE STRUCTURAL ARM'S OWN FALSIFIER. The `if` below carries the needle AND a store of
    # its own, so arm (1)'s statement test is satisfied — this is the mutant a count arm and a
    # statement arm both PASS, and the reason `#11954` asks for the AST: no loop iterates the
    # canonical constant any more, so the declaration's labels are no longer derived from anything.
    store_in_the_membership_test = source.replace(
        loop,
        "            if label in _CANONICAL_ENTITY_ID_PROPS:\n"
        '                proj.g.query(f"MATCH (n:{label} {{id:$id}}) SET n.embedding = $e ")\n'
        "            for label, _prop in _SOME_OTHER_PROPS:\n",
        1,
    )
    assert store_in_the_membership_test != source
    # ⛔ MEASURED, so the two arms cannot drift apart silently: WITHOUT the structural claim this
    # very mutant PASSES the row (the membership test it left behind carries a store), and WITH it
    # the row reds. That is the whole justification for the second arm, asserted rather than argued.
    assert _evidence_violation(
        store_in_the_membership_test, symbol, needle) is None, (
        "the mutant is not the statement-arm-only blind spot this arm exists for — the needle is not "
        "carried by a storing statement, so arm (1) alone would already red it")
    assert violation(store_in_the_membership_test) is not None, (
        "a re-targeted loop PLUS a store inside the surviving membership test satisfies the row: "
        "the statement arm alone cannot see this, the iterated-store arm must")

    # (2) A GENUINE store move: the loop still iterates the canonical constant, and the map-merges
    # the declaration is derived from are no longer inside it. Nothing about the needle changed —
    # the row was never about the needle, which is why a substring could not see this either.
    moved = source.replace(
        'SET n += $p "', 'RETURN count(n) "', 1).replace(
        '{{{match_prop}:$id}}) SET n += $props "',
        '{{{match_prop}:$id}}) RETURN count(n) "', 1)
    assert moved != source
    assert needle in moved
    assert violation(moved) is not None, (
        "the store the declaration is derived from was removed and the row still passes")

    # (3) A BINDING RENAME is not a store move (#11836/#11936): the variable is not part of the
    # claim, so the structural arm must not red on it — otherwise the cheapest repair is to edit the
    # declaration that is still true, the trap `#11089`'s capstone finding names.
    renamed = source.replace(
        "for label, _prop in _CANONICAL_ENTITY_ID_PROPS:",
        "for label, prop in _CANONICAL_ENTITY_ID_PROPS:", 1)
    assert renamed != source
    assert violation(renamed) is None, (
        f"a rename of an unused loop binding reds the row: {violation(renamed)}")

    # (4) PROSE IS NOT A STATEMENT: the needle carried by a BARE STRING statement stores nothing.
    # ⛔ `#12020` review, Finding 2: this arm used to re-target the loop as well, so its red was the
    # ITERATED arm's and the prose rule it claimed to pin was never the thing measured (arm (1b)
    # already covers that mutant). The mutation below leaves the loop iterating the canonical
    # constant — the iterated arm HOLDS on it — and moves the needle into a bare string at the top
    # of the function body, so the red can only come from the statement arm. Both halves are
    # asserted, so the two arms cannot drift apart silently the way they did before.
    # ⚠️ The bare string has to sit where NO enclosing statement also carries it: a string inside the
    # loop body is invisible to the statement arm because the `for` itself then carries the text
    # (measured — that is why the first version of this arm could not isolate prose at all). A bare
    # string in a NESTED scope is invisible for the same reason (#12020 review round 2, Finding 2).
    # ⛔ The def line is DERIVED, never hand-copied, and the insert is ASSERTED to have applied:
    # `_update_entity`'s signature has been edited before (#11836 renamed a binding in this very
    # function), and a silent `.replace()` no-op would leave the needle ABSENT from the body rather
    # than prose-carried — measured to pass all four assertions below, because the needle also
    # occurs in the sibling `_delete_entity`, so `_carrying_statements(...) == []` cannot tell
    # "prose" from "gone". That is the same vacuous attribution this arm was fixed for, one level
    # down, so the mutation proves it applied.
    signature = next(line + "\n" for line in source.splitlines()
                     if line.startswith(f"    def {symbol}("))
    prose = source.replace(
        loop,
        "            for label, _prop in (\n"
        "                _CANONICAL_ENTITY_ID_PROPS\n"
        "            ):\n",
        1,).replace(signature, signature + f'        "{needle}"\n', 1,)
    assert prose != source
    fn_prose = _function_of(prose, symbol)
    assert fn_prose.end_lineno is not None
    # The prose mutation really applied: the needle is IN the symbol's body …
    assert needle in _slice(prose.splitlines(), fn_prose), (
        "the prose insert did not apply, so this arm would be measuring an ABSENT needle rather "
        f"than prose: {symbol}'s def line drifted — re-derive `signature`")
    # … but no real statement carries it (the assertion two lines down is the half that would pass
    # vacuously if the insert had silently missed).
    # The loop half of the claim is deliberately UNTOUCHED by this mutant …
    assert _iterated_store_problem(fn_prose, iterated, prose.splitlines(),
                                   merge_store=(_claim_for(key) == "merge")) is None, (
        "the prose mutant moved the loop too — then its red is the iterated arm's, not the statement "
        "arm's, and this arm measures nothing about prose")
    # … and the needle really is left in a bare string, carried by no real statement.
    assert _carrying_statements([fn_prose], prose.splitlines(), needle) == [], (
        "the needle is still carried by a real statement — this mutant does not isolate prose")
    problem = violation(prose)
    assert problem is not None, (
        "a needle left in a docstring/bare string satisfied the row — prose is not a store")
    assert "no STATEMENT" in problem, (
        f"the red is not the statement arm's, so this arm does not pin the prose rule: {problem}")

    # (4b) A RE-BINDING of the loop variable is not an iteration over the constant (#12020 round
    # 3). The loop still iterates `_CANONICAL_ENTITY_ID_PROPS` and every store still interpolates
    # `{label}`, so the TEXT is indistinguishable — but `label` is pinned to one literal, so the
    # declaration's five labels are no longer derived from the constant. Measured at the reviewed
    # head: the whole guard stayed green and row 2 still reported `0 undeclared`.
    rebinding = source.replace(loop, loop + '                label = "Point"\n', 1,)
    assert rebinding != source
    problem_rebinding = violation(rebinding)
    assert problem_rebinding is not None, (
        "re-binding the loop variable to a literal satisfied the iterated-store claim: the row "
        "declares five labels while the code writes one")
    assert "RE-BOUND" in problem_rebinding, (
        f"the red is not the re-binding arm's, so this arm does not pin it: {problem_rebinding}")

    # (5) NO VACUOUS PASS: a source that does not define the symbol cannot be checked, and must RED
    # rather than answer by an empty `any(...)`.
    assert violation("def _other() -> None:\n    pass\n") is not None

    # (6) …and an UNREADABLE source is not a pass either: a file that does not parse raises out of
    # the arm. Silently answering "the claim holds" for evidence nobody could read is the failure
    # mode this whole lane is about, one level up.
    with pytest.raises(SyntaxError):
        violation("def _update_entity(:\n")


#: The clauses that name a store by ALIAS — the only ones this rule can attribute to a label.
#: Keyed by FORM so `test_every_store_alias_pattern_still_sees_a_live_store` can tell a member that
#: ROTS from one that was DELETED (a pin that merely iterates the dict cannot see a deletion).
#:
#: ⛔ A `= NULL` assignment is deliberately excluded: `SET t.embedding=NULL` REMOVES the property
#: (`#4194`) and stores nothing, so counting `t` there would invent a store.
_STORE_ALIAS: dict[str, re.Pattern[str]] = {
    "set_embedding_clause": re.compile(r"set_embedding_clause\(\s*[\"'](\w+)[\"']"),
    "clear_then_set_prefix": re.compile(r"clear_then_set_prefix\(\s*[\"'](\w+)[\"']"),
    "literal-embedding-assignment": re.compile(r"\b(\w+)\.embedding\s*=(?!\s*NULL)"),
}

#: The forms above are the complete set. Pinned so DELETING a member cannot pass silently — the
#: failure a member-iterating pin cannot see, and the one that would silently stop the alias rule
#: from attributing a store written through that helper.
_STORE_ALIAS_FORMS = frozenset(
    {"set_embedding_clause", "clear_then_set_prefix", "literal-embedding-assignment"}
)

#: Writers whose store alias is legitimately bound in a CALLER rather than in the cited symbol, so
#: the completeness pin cannot attribute it. Keyed `(module::symbol, alias)` -> the reason it cannot
#: be attributed here. ⛔ PER-ALIAS on purpose: keyed by writer alone, ONE reviewed entry would
#: excuse every unattributed alias of that writer — including a second, genuinely unserved store
#: that arrives later — reinstating above the rule exactly the "total, not per-alias" drop that
#: `#11530`'s fix closed below it.
#: ⛔ An entry appears here only by a deliberate, reviewed decision — never to buy green, because a
#: writer listed here has its `stores` tuple trusted for what it OMITS, which is the state row 2
#: must be told about. The ONE live entry (`#11641`, `tortoise.hosted_backup::restore_graph`,
#: alias `n`) is a store whose label is INTERPOLATED from a declaration the writer enforces at its
#: own seam — `_require_served_restore_labels` refuses any vector on a label outside it — so nothing
#: static binds `n` to a label while the label set is provably bounded. Every OTHER store must still
#: bind its own alias; the entry is per-alias so a second, genuinely unattributed store in the same
#: writer reds. Pinned live by
#: `test_every_unattributable_store_alias_is_still_unbound_and_store_shaped`.
STORE_ALIAS_UNATTRIBUTABLE: dict[tuple[str, str], str] = {
    # `#11641`. The restore's store clause is `set_embedding_clause("n", "r.v")` over
    # `MATCH (n:<label> {__dump_id:r.id})`, where `<label>` is interpolated from
    # `_VECTOR_RESTORE_LABELS` — a literal an f-string renders, so no static rule can bind `n` to
    # it. The completeness claim is therefore carried by that CONSTANT (re-derived in both
    # directions against `tortoise.security` by `tests/test_11641_runtime_vector_label.py`) and by
    # `_require_served_restore_labels`, which REFUSES a vector-bearing node whose labels include
    # none of them. PER-ALIAS on purpose: a second, genuinely unattributed store in this writer
    # still reds this pin.
    ("tortoise.hosted_backup::restore_graph", "n"): (
        "label is the dump's, bounded to the declared set at the seam — nothing static binds `n`"
    ),
}

#: `(alias:Label` — how a Cypher statement binds an alias to a label. `\w+` deliberately does not
#: match an INTERPOLATED label (`CREATE (n:{safe_labels})`), which no static rule can attribute.
_ALIAS_LABEL = re.compile(r"(?:MERGE|CREATE|MATCH)\s*\(\s*(\w+)\s*:\s*(\w+)")


def test_a_declared_label_set_covers_every_label_its_own_store_targets() -> None:
    """`#11530` / `#11492` review — a label tuple answers WHERE, so it may not UNDER-name.

    ⛔ The OTHER direction from `test_every_declared_label_re_derives_at_this_head`: that pin
    proves each DECLARED label still exists in the writer. This one proves no label the writer's
    own store clause TARGETS is missing from the declaration. Without it, a writer that gains a
    SECOND store keeps every test green while row 2 prints `stored-without-a-query-path: none`
    over an unserved label — measured: `MERGE (b:Batch {id: turn.id}) SET b.embedding =
    vecf32(turn.emb)` inside the real `_TURN_WRITE_CYPHER` kept `tests/test_dod_5090_probe.py`
    and this suite at 189 passed, 1 skipped, byte-identical to the unmutated run, with `Batch`
    absent from `scored_labels()`. That is the false PASS `#11492` exists to remove.

    ⛔ Scoped to the ALIAS the store clause passes, never to every `MERGE|CREATE` label in the
    body: `_rebuild_all_replay` binds `:Batch`/`:Session` for the pre-wipe SNAPSHOT and
    `_event_plain_merge` binds `:Source`/`:Subject` with no vector, so a body-wide rule
    false-reds three CORRECT entries. The alias is what ties the label to the store.
    """
    covered = 0
    for key, (_rel, _symbol, _needle, declared) in sorted(LABEL_EVIDENCE.items()):
        bound: dict[str, set[str]] = {}
        aliases: set[str] = set()
        for body in _declared_symbol_bodies(key):
            for alias, label in _ALIAS_LABEL.findall(body):
                bound.setdefault(alias, set()).add(label)
            for pattern in _STORE_ALIAS.values():
                aliases |= set(pattern.findall(body))
        if not aliases:
            # No statically readable store alias: the completeness claim must be carried by a
            # CONSTANT re-derived in both directions, or this pin is silently INERT for the entry.
            assert key in LABEL_CONSTANTS, (
                f"{key} declares {sorted(declared)} but no store clause in its body names an "
                "alias this pin can attribute, and this branch's ONLY escape is a "
                "LABEL_CONSTANTS entry (which is re-derived in both directions). "
                "⛔ `STORE_ALIAS_UNATTRIBUTABLE` does NOT clear this branch — it is read solely in "
                "the PER-ALIAS branch below — so an entry keyed (key, alias) leaves this "
                "assertion red; do NOT leave the completeness claim unbacked"
            )
            continue
        # ⛔ PER-ALIAS, never total: an alias named by a store clause but bound to no label in the
        # cited body contributes an EMPTY set, and dropping it silently is a false PASS — measured,
        # `SET b.embedding = vecf32(turn.emb)` alongside a resolvable `t` kept this suite at 190
        # passed while `b` stored on unserved `Batch`. A total check (`any alias resolves`) cannot
        # see it.
        unattributed = sorted(
            alias for alias in aliases if not bound.get(alias)
            and (key, alias) not in STORE_ALIAS_UNATTRIBUTABLE
        )
        assert not unattributed, (
            f"{key}: store clause(s) name alias/es {unattributed}, which its own body never binds "
            "to a label, so this pin cannot attribute them and would pass an UNSERVED store "
            "silently. Bind the alias, or — only where it is legitimately bound in a CALLER — add "
            "a reasoned STORE_ALIAS_UNATTRIBUTABLE entry keyed (key, alias). ⛔ A `LABEL_CONSTANTS` "
            "entry does NOT clear this branch (measured): it rescues the no-alias branch above."
        )
        stored = set().union(*(bound.get(alias, set()) for alias in aliases if bound.get(alias)))
        if not stored:
            # Reachable ONLY through a reviewed `STORE_ALIAS_UNATTRIBUTABLE` entry: the assertion
            # above already refused every unattributed alias that is not declared there. Such an
            # entry's `stores` tuple is trusted for what it OMITS — the state row 2 must be TOLD
            # about, which is what the dict entry is for; it is not papered over here.
            continue
        covered += 1
        assert stored <= set(declared), (
            f"{key} declares stores={sorted(declared)} but its own store clause writes through "
            f"alias/es {sorted(aliases)} bound to {sorted(stored)} — "
            f"{sorted(stored - set(declared))} is STORED and NOT declared, so row 2 would print "
            "`stored-without-a-query-path: none` over it. Declare the label, or remove the store."
        )
    assert covered >= 6, (
        f"the alias rule resolved only {covered} entries — it is inert for the rest, which is the "
        "shape this pin must never become"
    )


def test_the_per_alias_refusal_fires_and_its_exemption_is_per_alias(monkeypatch) -> None:
    """The two branches of the per-alias rule, DRIVEN — a guard with no caller is revertible.

    Measured before this test existed: emptying `unattributed` (the pre-`#11530` "total"
    behaviour) left this whole file at `29 passed`, and `STORE_ALIAS_UNATTRIBUTABLE` had no
    caller at all. So both halves of the fix were revertible in silence — the class this file
    already fixed once for `STORE_CLAUSE_MARKERS`.

    Synthetic bodies for the DRIVEN half below. (`STORE_ALIAS_UNATTRIBUTABLE` is no longer empty —
    `#11641` added the restore's interpolated-label alias — so this test's synthetic entries are
    monkeypatched OVER the live dict rather than supplying its only member; that is why each case
    asserts a specific refusal message instead of relying on emptiness.) The bodies themselves are
    synthetic: a resolvable store on `t` alongside a store on `b`, which the body never binds.
    """
    key = "tortoise.sdk::_write_capture_turns"  # declared stores=("Point",)
    real = _declared_symbol_bodies
    bound_ok = 'MATCH (t:Point {id:x}) set_embedding_clause("t")'
    unbound = "\nSET b.embedding = vecf32($e)"

    def only_this_key(k: str) -> list[str]:
        return [bound_ok + unbound] if k == key else real(k)

    monkeypatch.setattr(sys.modules[__name__], "_declared_symbol_bodies", only_this_key)
    with pytest.raises(AssertionError, match="never binds"):
        test_a_declared_label_set_covers_every_label_its_own_store_targets()

    # ⛔ PER-ALIAS: exempting a DIFFERENT alias of the same writer must NOT clear it.
    monkeypatch.setitem(STORE_ALIAS_UNATTRIBUTABLE, (key, "zzz"), "synthetic wrong alias")
    with pytest.raises(AssertionError, match="never binds"):
        test_a_declared_label_set_covers_every_label_its_own_store_targets()

    # …while exempting the alias the clause actually names clears it.
    monkeypatch.setitem(STORE_ALIAS_UNATTRIBUTABLE, (key, "b"), "bound in a caller (synthetic)")
    test_a_declared_label_set_covers_every_label_its_own_store_targets()


def test_every_unattributable_store_alias_is_still_unbound_and_store_shaped() -> None:
    """An exemption must keep earning its place — the sibling `NO_STORE_EXEMPTIONS` standard.

    Keyed `(writer, alias)`, so this asserts both halves of its authority: the alias must still
    be NAMED by a store clause of that writer (otherwise the entry excuses nothing and the pin
    has silently stopped covering a store that moved), and it must still be UNBOUND (otherwise
    the entry now hides a store the alias rule could have attributed, which is strictly worse).
    """
    stale: list[str] = []
    for (key, alias), reason in sorted(STORE_ALIAS_UNATTRIBUTABLE.items()):
        bodies = _declared_symbol_bodies(key)
        named = any(
            alias in found
            for body in bodies
            for pattern in _STORE_ALIAS.values()
            for found in pattern.findall(body)
        )
        resolved = any(
            dict(_ALIAS_LABEL.findall(body)).get(alias) for body in bodies
        )
        if not named:
            stale.append(f"{(key, alias)} — no longer NAMED by a store clause — {reason}")
        elif resolved:
            stale.append(f"{(key, alias)} — now BOUND — {reason}")
    assert not stale, (
        "these `STORE_ALIAS_UNATTRIBUTABLE` entries no longer excuse an unattributable store: an "
        "alias that is no longer NAMED by a store clause makes the entry inert, and one that is "
        "now BOUND hides a store the alias rule could attribute. Re-derive and drop the entry: "
        + "; ".join(stale)
    )


def test_an_unattributable_exemption_stops_earning_its_place(monkeypatch) -> None:
    """The liveness pin's own falsifier — neutering it must not be silent.

    Measured before this test existed: turning the pin's only effect into `if False:` left
    `193 passed, 1 skipped`, and deleting the test outright was equally invisible, because
    `STORE_ALIAS_UNATTRIBUTABLE` WAS empty on the live tree at that measurement (it now holds the
    restore's interpolated-label alias, `#11641` — which makes this falsifier MORE load-bearing, not
    less: one live entry is enough for a neutered pin to stop excusing a stale one). That is the
    class this file fixed once for `STORE_CLAUSE_MARKERS`, and it matters more here: an undetected
    stale exemption re-opens the `#11530` hole, because the completeness pin CLEARS its refusal on
    that key.
    """
    key = "tortoise.sdk::_write_capture_turns"
    module = sys.modules[__name__]

    monkeypatch.setattr(module, "STORE_ALIAS_UNATTRIBUTABLE",
                        {(key, "t"): "synthetic: `t` IS bound in its own body"})
    with pytest.raises(AssertionError, match="now BOUND"):
        test_every_unattributable_store_alias_is_still_unbound_and_store_shaped()

    monkeypatch.setattr(module, "STORE_ALIAS_UNATTRIBUTABLE",
                        {(key, "zzz"): "synthetic: no store clause names this alias"})
    with pytest.raises(AssertionError, match="no longer NAMED"):
        test_every_unattributable_store_alias_is_still_unbound_and_store_shaped()


def test_every_store_alias_pattern_still_sees_a_live_store() -> None:
    """Every member of `_STORE_ALIAS` must still match a store clause of a REAL writer.

    ⛔ Both directions, because they fail differently: a member that ROTS (a helper renamed) stops
    attributing silently, and a member that is DELETED is invisible to a pin that merely iterates
    the dict — so the FORM set is asserted first. `clear_then_set_prefix` is currently redundant on
    every entry (`_write_capture_turns` also carries a literal `t.embedding =`), which is exactly
    why its deletion would otherwise cost nothing and be noticed by nobody.
    """
    assert set(_STORE_ALIAS) == _STORE_ALIAS_FORMS, (
        "a store-clause form was added without pinning it, or REMOVED — a removed form silently "
        "stops the completeness pin (`#11530`) from attributing stores written through it. "
        f"expected={sorted(_STORE_ALIAS_FORMS)} got={sorted(_STORE_ALIAS)}"
    )
    bodies = [body for key in LABEL_EVIDENCE for body in _declared_symbol_bodies(key)]
    dead = sorted(
        form for form, pattern in _STORE_ALIAS.items()
        if not any(pattern.search(body) for body in bodies)
    )
    assert not dead, (
        f"{dead} matches no store clause of any label-declaring writer — a form that sees nothing "
        "cannot attribute the alias it exists for, and every entry using it would pass as 'no "
        "alias', which the completeness pin reads as covered"
    )


def test_the_two_former_runtime_label_writers_now_own_their_label() -> None:
    """`#11641` — the label moved from a RUNTIME decision to an enforced one, in the writer.

    These are the two entries that held `#5090` DoD row 2 at `PARTIAL` under `#11492`: a declaration
    could not name a label, so no served-set comparison was possible. `LABELS_AT_RUNTIME` was the
    HONEST answer then (`no-live-vector` would have been a lie and a guessed label a fabrication)
    and it is the WRONG answer now, because each writer has since been given a bound of its own —
    so declaring a label tuple is no longer a claim about callers or about a file.

    ⛔ This pin is about the BOUND, not the tuple. A tuple alone would be exactly the fabrication
    this disposition was invented to refuse; what makes it true is that the writer REFUSES a store
    outside it, and that refusal is what the needle below reads in the writer's own body. Revert
    either seam and this test reds with the shape it lost, not with a diff nobody can read.
    """
    restore = EMBED_WRITER_CLASSES["tortoise.hosted_backup::restore_graph"]
    assert restore.stores == ("Point", "Subject", "Object", "Source", "Event"), restore.stores
    assert any("_require_served_restore_labels(" in own
               for own in _declared_symbol_bodies("tortoise.hosted_backup::restore_graph")), (
        "`restore_graph` no longer bounds the labels it restores a vector onto — without that "
        "refusal the dump's own labels decide where the vector lands and the label tuple above is "
        "a fabrication. Restore `_require_served_restore_labels` or declare `LABELS_AT_RUNTIME` "
        "again; do not delete this assertion"
    )
    door = EMBED_WRITER_CLASSES["tortoise.sdk::_event_raw_write_door"]
    assert door.stores == ("Event",), door.stores
    assert any("_declared_labels = {label for label, _id_prop in _RAW_WRITE_DOOR_LABELS}" in own
               for own in _declared_symbol_bodies("tortoise.sdk::_event_raw_write_door")), (
        "the door no longer refuses a statement naming a label outside its declared set — without "
        "that refusal the label is the CALLER's again and `stores=(\"Event\",)` is a claim about "
        "callers that nothing re-derives"
    )
    # And the disposition itself must stay a live SHAPE (it is exercised synthetically by
    # `tests/test_dod_5090_probe.py`): no entry here uses it, which is the point — zero writers
    # should have an unnameable label while these two bounds exist.
    #
    # ⚠️ NOT a blanket ban (`#11641` review): the register states this is a legal SHAPE for a writer
    # that genuinely has no bound, so forbidding it outright would contradict that prose — and the
    # failure message would tell a future author to "say why it has none", which the assertion then
    # refused. What is pinned is that reaching for it is ACCOUNTED FOR: the reason carries the
    # `no-bound:` token the register's own note requires. ⛔ That token is a DECLARATION an author
    # types, not evidence — this pin tests only that it is PRESENT, so a reason reading
    # `no-bound: this is a LIE` passes it (`#11641` r5). What catches that is row 2:
    # `scored_labels()` folds any at-runtime writer into its HOLD set, so the disposition can never
    # be reached silently. Both halves are pinned by
    # `test_the_no_bound_marker_pin_still_bites_and_the_LIE_it_cannot_catch_is_row_2s`.
    for key, entry in sorted(EMBED_WRITER_CLASSES.items()):
        if entry.stores != LABELS_AT_RUNTIME:
            continue
        assert "no-bound:" in entry.reason, (
            f"{key} is declared labels-at-runtime with no `no-bound:` marker in its reason — the "
            "disposition is for a writer that genuinely has no bound. ⛔ The token is a DECLARATION "
            "an author types — this pin cannot tell it from a lie; row 2 is what holds the row. "
            "Prefer a bound at the "
            "writer's own seam (`_VECTOR_RESTORE_LABELS`, `_RAW_WRITE_DOOR_LABELS`); if there "
            "genuinely is none, name it with `no-bound:` in the reason and say why."
        )


def test_the_no_bound_marker_pin_still_bites_and_the_LIE_it_cannot_catch_is_row_2s(
        monkeypatch) -> None:
    """`#11641` r5 — what the `no-bound:` token IS, pinned so the prose cannot re-inflate.

    The register's note used to say the marker is *"how a reader tells a writer that genuinely has
    no bound from one whose bound nobody wrote"*. **It is not that**, and the note now says so: the
    pin below tests only that the token is PRESENT in `reason`, so a reason reading
    `no-bound: this is a LIE, the writer has a bound` passes it. What the token is, is a declaration
    an AUTHOR must type — a claim they checked, never evidence that they did. The gate that bites is
    `#5090` DoD row 2, which folds every `LABELS_AT_RUNTIME` writer into the row's HOLD set.

    Both halves are asserted here, because a prose claim with no pin drifts:

    * a marker-LESS at-runtime entry REDS the pin (so the pin cannot be quietly deleted either);
    * a MARKED LIE passes the pin — the limit, asserted rather than left implied;
    * …and `scored_labels()` HOLDS the row on that same lie, which is the real gate.

    ⛔ Nothing here weakens row 2: the second half is exactly why the disposition can never be
    reached SILENTLY, and it is the assertion a future author should extend, not relax.
    """
    this_module = sys.modules[__name__]
    register_module = sys.modules[WriterClass.__module__]

    def _patched(reason: str) -> dict[str, WriterClass]:
        return {**EMBED_WRITER_CLASSES,
                "tortoise.z::_no_bound_probe": WriterClass(
                    EXEMPT, reason, "#11641", stores=LABELS_AT_RUNTIME)}

    # (1) NO marker → the pin bites, naming the writer it wants a reason from.
    monkeypatch.setattr(this_module, "EMBED_WRITER_CLASSES", _patched("a writer with no bound"))
    with pytest.raises(AssertionError, match="no `no-bound:` marker"):
        test_the_two_former_runtime_label_writers_now_own_their_label()

    # (2) A MARKED LIE → the pin passes. The limit, pinned: this is why the register's note no
    # longer claims the marker discriminates.
    lying = _patched("no-bound: this is a LIE, the writer has a bound")
    monkeypatch.setattr(this_module, "EMBED_WRITER_CLASSES", lying)
    test_the_two_former_runtime_label_writers_now_own_their_label()

    # (3) …and ROW 2 catches what the pin cannot: the writer is HELD as at-runtime, not resolved.
    # Injected through the probe's own combinator, so a future refactor of the fold reds here too.
    from tools.dod_5090_probe import scored_labels

    monkeypatch.setattr(register_module, "EMBED_WRITER_CLASSES", lying)
    synthetic = {"tortoise.z::_no_bound_probe": {"kind": "unresolved_write", "at": "probe"}}
    scored = scored_labels(writers=synthetic)
    assert "tortoise.z::_no_bound_probe" in scored["unresolved"], (
        "row 2 no longer HOLDS an at-runtime declaration — the disposition became reachable "
        f"silently: {scored['unresolved']}"
    )
    assert "tortoise.z::_no_bound_probe" not in scored["labels"]


def test_a_writer_that_EXECUTES_a_store_is_never_declared_no_live_vector() -> None:
    """`#11492`'s review finding, pinned: the class is not the answer to WHERE.

    The first revision of this field filled two entries from the writer's CLASS instead of from its
    STORE: `tortoise.sdk::_write_capture_turns` was declared `no-live-vector` while executing
    `_TURN_WRITE_CYPHER`, whose `MERGE (t:Point …)` carries
    `t.embedding=CASE WHEN turn.emb IS NOT NULL THEN vecf32(turn.emb) …`, and
    `tortoise.sdk::_event_raw_write_door` was declared the same while executing a CALLER's
    `proj.g.query(statement, params={"embedding": …})`. Both are live graph vector stores, so both
    declarations were false — and `no-live-vector` is not a harmless label: row 2 REMOVES such a
    writer from the map it audits, so the row would print `stored-without-a-query-path: none` even
    if that store moved to a label `run_vector_query` does not serve.

    This is the class of mistake `#11492` exists to prevent, so it is pinned on the two writers it
    was made on: `no-live-vector` may only be declared where no store statement exists.
    """
    turns = EMBED_WRITER_CLASSES["tortoise.sdk::_write_capture_turns"]
    assert turns.stores == ("Point",), turns.stores
    # ⛔ SCOPED TO THE SYMBOL, not to `sdk.py`. Read whole, this file already contains
    # `MERGE (t:Point {id: turn.id})` TWICE (the statement at :2460 and a COMMENT at :2927), so a
    # whole-file `in` check stayed green after the writer's own statement was removed or retargeted
    # as long as one copy survived anywhere in 50k lines — a pin that did not pin what its message
    # claimed. `_declared_symbol_bodies` is the reader the sibling pin already uses.
    turn_bodies = _declared_symbol_bodies("tortoise.sdk::_write_capture_turns")
    assert any('MERGE (t:Point {id: turn.id})' in own for own in turn_bodies)
    assert any("t.embedding=CASE WHEN turn.emb IS NOT NULL THEN vecf32(turn.emb) " in own
               for own in turn_bodies), (
        "`_TURN_WRITE_CYPHER` stopped writing a vector — re-read this declaration"
    )
    door = EMBED_WRITER_CLASSES["tortoise.sdk::_event_raw_write_door"]
    # `#11641`: the door OWNS the label now, so `no-live-vector` is refused here for the same reason
    # it always was (the store is real), and the tuple is not the fabrication it once would have
    # been (the door refuses a caller statement that names anything outside it).
    assert door.stores == ("Event",), door.stores
    door_bodies = _declared_symbol_bodies("tortoise.sdk::_event_raw_write_door")
    assert any('proj.g.query(statement, params=query_params)' in own for own in door_bodies), (
        "the door no longer EXECUTES the caller's statement — re-read this declaration"
    )


#: The clause shapes that mean a writer's OWN body stores a vector: the two builders that render a
#: store fragment, and the dense constructor that fills one. A `no-live-vector` declaration whose
#: symbol carries one of these is claiming a store does not exist where the body renders one.
STORE_CLAUSE_MARKERS = ("vecf32(", "set_embedding_clause(", "clear_then_set_prefix(")

#: The DIRECT property-assignment store — `SET n.embedding = $v` — which is the instrument's own
#: discovery shape (`tools/dod_5090_probe.py::_EMBED_WRITE`). It must be here or the pin has a hole
#: exactly as wide as that shape: a writer can enter the population BECAUSE of a clause the literal
#: markers above never see, then pass this pin when declared `no-live-vector`, and row 2 pops it from
#: the map it audits while still printing `stored-without-a-query-path: none` (round-2 review, P2).
#: ⛔ `(?!\s*NULL)` — the whitespace belongs INSIDE the lookahead. Written as `\s*=(?!NULL)` the
#: pattern matches the wipe too, because `\s*` backtracks to zero characters and the lookahead then
#: tests the space rather than `NULL` (measured: it flagged `_prewipe_synthetic_write_scope`, whose
#: only occurrence is a docstring quoting `SET n.embedding = NULL`). The wipe assigns NULL and is a
#: WIPE, not a store; the dict-key carriers (`record["embedding"] = …`) are a different spelling and
#: stay out of scope.
STORE_CLAUSE_PATTERNS = (re.compile(r"\.embedding\s*=(?!\s*NULL)"),)

#: The shapes that legitimately NAME a store clause without storing, each with the reason. Kept as
#: data so the exemption is read and re-checked rather than being an invisible `if`: every key must
#: still be a LIVE `no-live-vector` declaration, so the table cannot outlive the shape it exempts.
NO_STORE_EXEMPTIONS: dict[str, str] = {
    "tortoise.vector_write::set_embedding_clause": (
        "the store-clause BUILDER itself: it renders the fragment another (declared) writer "
        "executes, so it names `set_embedding_clause(` by defining it and `vecf32(` while "
        "building the string — neither is a store in this body"
    ),
}


def _without_comments(text: str) -> str:
    """`text` with whole-line `#` comments removed.

    ⛔ A needle test is a test about what the code EXECUTES. `tortoise/sdk.py:2927` is a COMMENT
    carrying `MERGE (t:Point {id: turn.id})`, and it sits INSIDE `_write_capture_turns`'
    line span (2827-3660) — so narrowing the search from the whole file to the symbol did NOT
    exclude it, and the pin stayed green on a source whose real statement had been removed. Only
    whole-line comments are dropped (a trailing `#` can live inside a Cypher string, and cutting
    there would corrupt a needle).
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _constant_names_named_by(node: ast.AST, constants: dict[str, str]) -> list[str]:
    """The NAMES of the module-level constants `node` actually names, in `constants` order."""
    named = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    return [name for name in constants if name in named]


def _constants_named_by(node: ast.AST, constants: dict[str, str]) -> list[str]:
    """The module-level constants `node` actually NAMES, resolved through the AST.

    ⛔ This replaced `if name in own` — a SUBSTRING match over the symbol's source text — which was
    fail-OPEN in exactly the direction the `LABEL_EVIDENCE` pin exists to catch: any constant whose
    NAME happened to occur anywhere in the symbol's body was concatenated into the searched text, so
    a needle could be satisfied by a constant the writer never executes and the pin stayed green
    while the store it describes had moved. Measured inclusion counts under the substring rule:
    `_rebuild_all_replay` 17 constants, `_event_plain_merge` 2, `_update_entity` 4,
    `_write_capture_turns` 6 — only the last genuinely needs the hop.

    A name is resolved whether it is read directly, inside an f-string, or from a nested closure,
    because `ast.walk` descends into all of them.
    """
    named = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    return [text for name, text in constants.items() if name in named]


#: Text that MARKS a store inside the statement that carries it. The three clause forms are
#: `_STORE_ALIAS`'s own (`set_embedding_clause`, `clear_then_set_prefix`, a literal `.embedding =` —
#: never the `NULL` wipe, which removes the property).
#: ⛔ `#12020` review round 5, Finding 2: the bare map-merge (`SET n += $p`) is NOT one of them. It
#: used to be, on the reasoning that the generic update door stores through it — and that credit was
#: a FALSE PASS for every other `store` row, measured: delete `set_embedding_clause(...)` from
#: `_event_plain_merge`'s and `_session_event_write`'s query entirely, leave `SET e += $props`, and
#: both rows stayed GREEN with no vector being written at all. The premise is refuted by this
#: repo's own probe: `tools/dod_5090_probe.py` `UNDECIDABLE_WRITE_SHAPES` states that whether a
#: merged map carries an `embedding` is DATA FLOW, not syntax, and refuses to decide it — so a
#: syntactic claim may not decide it either. The one row whose store genuinely IS a map-merge is
#: declared separately in `MERGE_STORE_CLAIM`, where the weaker evidence is a NAMED exception bound
#: to that row rather than a default inherited by every other one.
#: ⛔ `#12020` review, Finding 1: the `.embedding =` form is NARROWED here, because the first version
#: credited two shapes that store nothing — the false-PASS direction this whole row exists to close.
#:   · `(?!=)` excludes the COMPARISON: `if t.embedding == prior:` is not an assignment, and the
#:     earlier `(?!\s*NULL)` let it through (measured: it matched `t.embedding =`).
#:   · `(?!\s*(?i:null)\b)` excludes the NULL WIPE in ANY spelling, because CYPHER IS
#:     CASE-INSENSITIVE — `SET n.embedding = null` clears the property exactly as `= NULL` does, and
#:     `tortoise/consistency.py` already reads that shape as an `embedding: null` clear. The `\s*`
#:     lives INSIDE the lookahead on purpose: written outside it, the engine backtracks the
#:     whitespace and the lookahead then compares against a space, which matches.
#: Both narrowings are pinned in `test_the_store_carrier_does_not_credit_a_comparison_or_a_wipe`.
#: ⚠️ Residual, stated: this reads the carrier's SOURCE SLICE, so a string literal inside the carrier
#: that quotes a store counts as one. Closing that needs AST-level clause detection, the same class
#: of limit `_declared_symbol_bodies` records for trailing comments.
_STORE_CARRIER = re.compile(
    r"set_embedding_clause\(|clear_then_set_prefix\("
    r"|\b\w+\.embedding\s*=(?!=)(?!\s*(?i:null)\b)"
)

#: The map-merge form, credited ONLY to the rows in `MERGE_STORE_CLAIM` — never generically.
#: `tortoise.sdk::_update_entity` stores its vector by merging a props map (`SET n += $p`) whose
#: contents no syntactic rule can attribute, which is exactly the shape `UNDECIDABLE_WRITE_SHAPES`
#: names. That row is exempt from owing a declaration on that basis (`#4094`), so its evidence is
#: honest only if it SAYS it is the weaker kind — hence a declared claim kind rather than a silent
#: widening of the rule every other row is scored by.
_MERGE_STORE_CARRIER = re.compile(r"\bSET\s+\w+\s*\+=")


#: Scope-defining node types: `ast.stmt` subclasses that establish a RANGE rather than execute a
#: statement. Never carriers in `_carrying_statements` — a scope's body is not the statement that
#: carries its claim, at the cited symbol's level or nested (#12020 review, Finding 2).
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _slice(lines: list[str], node: ast.AST) -> str:
    """The source of `node` by LINE RANGE, comment-stripped."""
    return _without_comments("\n".join(lines[node.lineno - 1:node.end_lineno]))


def _module_constants(tree: ast.Module, lines: list[str]) -> dict[str, tuple[str, ast.stmt]]:
    """Module-level ``NAME = <expr>`` / ``NAME: T = <expr>``: name -> (source, statement).

    Both statement shapes on purpose, for the reason the `LABEL_CONSTANTS` arm below states: a
    constant that only DROPS its type annotation is the SAME constant.
    """
    found: dict[str, tuple[str, ast.stmt]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = (_slice(lines, node), node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = (_slice(lines, node), node)
    return found


def _carrying_statements(universe: list[ast.AST], lines: list[str],
                         needle: str) -> list[tuple[ast.stmt, str]]:
    """The SMALLEST enclosing STATEMENTS of `universe` whose own source carries `needle`.

    ⛔ This is the shape of `#11954`'s fix, and it replaces `assert any(needle in body …)`. A
    substring proves the text occurs **somewhere** in the cited symbol; it does not say **which
    statement carries it**, so a re-targeted loop leaves the row green while the store it describes
    has moved (the issue carries the reproduction). Returning the STATEMENTS, not a bool, is what
    makes that mutation observable: the arm below then asks the *carrying* statement — not the whole
    body — whether it also carries a store.

    Only the MINIMAL statement is returned (a statement whose own descendant statement also carries
    the needle is replaced by that descendant), so a needle in a loop HEAD is attributed to the loop
    and a needle in a loop BODY to the body statement that holds it.

    ⛔ A bare string statement (the docstring shape) is not a carrier. Prose quoting a clause is not
    a claim about what the code executes — the same distinction `_without_comments` draws for whole
    line comments. ⛔ `#12020` review, Finding 2: that rule was INERT until the scope roots below
    were excluded, and the reason is worth keeping: `ast.FunctionDef` IS an `ast.stmt`, so the cited
    symbol was itself a hit whenever the needle appeared anywhere in its body — including a
    docstring — and minimality then returned the FUNCTION as "the statement that carries it",
    whose slice obviously contains a store. Measured on the reviewed head: a needle left only in a
    docstring satisfied the row. Excluding the roots is what makes the prose rule observable at all.
    The residual that remains is a TRAILING comment, which this cannot strip without cutting a `#`
    inside a Cypher string.
    """
    # The cited symbol is the RANGE the claim is bounded TO, never the statement that carries it:
    # `ast.FunctionDef`/`AsyncFunctionDef`/`ClassDef` are `ast.stmt` subclasses, so without this a
    # docstring-only needle answers "the function carries it" — the substring test this whole row
    # replaced, one level up.
    # ⛔ Every scope node is skipped, not just the `universe` ROOTS: a NESTED `def`/`class` inside the
    # cited symbol is its own body, so a needle left in a nested scope's docstring satisfied the arm
    # the same way (measured — the nested `FunctionDef`'s slice contains the nested body's stores).
    # ⛔ `#12020` review round 5, Finding 3: the walk must not DESCEND into a nested scope either.
    # The version above skipped the scope NODE while still walking its body, and the difference is
    # measured, not theoretical: wrap `_event_plain_merge`'s real query statement in a `def _dead()`
    # that is never called and the row stayed GREEN — the writer executes no store at all. Skipping
    # the node alone only stops "the nested def's body" being the carrier; the statements INSIDE it
    # were still carriers. That was the same defect round 4 closed in `_iterated_store_problem`, and
    # the comment above claimed the two shared the rule while they did not.
    # The slice is blanked for the same reason `_slice_own_scope` exists: a compound carrier SPANS
    # its body, so an `if`/`for` whose text encloses a nested def would otherwise match that def's
    # store. A claimed store must be one THIS scope executes.
    # Module CONSTANTS in `universe` are deliberately NOT excluded: a writer may hold its clause in
    # one (`_TURN_WRITE_CYPHER`), and that statement is a real carrier.
    hits: list[tuple[ast.stmt, str]] = []
    for root in universe:
        for node in _walk_own_scope(root):
            if not isinstance(node, ast.stmt):
                continue
            if isinstance(node, _SCOPE_NODES):
                continue  # a scope's body is not a statement that carries the needle
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                continue  # a docstring is prose, not a statement that stores
            text = _slice_own_scope(lines, node)
            if needle in text:
                hits.append((node, text))
    minimal: list[tuple[ast.stmt, str]] = []
    for node, text in hits:
        deeper = any(
            other is not node
            and any(child is other for child in ast.walk(node))
            for other, _ in hits
        )
        if not deeper:
            minimal.append((node, text))
    return minimal


def _slice_own_scope(lines: list[str], statement: ast.stmt) -> str:
    """`_slice`, with the line ranges of NESTED SCOPE DEFINITIONS blanked out.

    ⛔ Skipping a nested scope in the walk is not enough on its own: a compound statement SPANS its
    body, so the `for`'s own line range still contains the text of the nested def it encloses —
    measured, and it is why the first version of round 4's fix still credited a store inside a
    never-called nested function (the `for` itself matched `SET n += $p` from that function's body).
    The claim is about what this scope executes, so the nested scope's lines are not evidence here.
    """
    if not isinstance(statement, ast.stmt):
        return _slice(lines, statement)
    keep = list(lines[statement.lineno - 1:statement.end_lineno])
    base = statement.lineno
    for sub in ast.walk(statement):
        if sub is statement or not isinstance(sub, _SCOPE_LIKE) or not hasattr(sub, "lineno"):
            continue
        for offset in range(sub.lineno - base, sub.end_lineno - base + 1):
            if 0 <= offset < len(keep):
                keep[offset] = ""
    return _without_comments("\n".join(keep))


def _iterated_store_problem(node: ast.AST, constant: str, lines: list[str],
                            *, merge_store: bool = False) -> str | None:
    """Why `node` no longer has a loop over `constant` whose TARGET drives a store, or None.

    ⛔ This is the structural half of the `#11954` row, and the half a substring can never close:
    the claim `tortoise.sdk::_update_entity` makes is not that the NAME
    `_CANONICAL_ENTITY_ID_PROPS` occurs, it is that the per-label loop **iterates** it and the
    loop's binding is what the store is built from (`MATCH (n:{label}) … SET n += $p`). Asserted on
    the AST: a `for` (or comprehension) whose ITERABLE is that `Name`, whose body carries a store
    marker, and whose target name the store statement actually mentions. A membership test on the
    same constant — the natural, good-faith refactor the issue describes — satisfies none of the
    three. ⚠️ It cannot see a store that names the label WITHOUT interpolating the binding
    (`"MATCH (n:" + label`): the claim it checks is the interpolated form the writers on this tree
    use, and a rewritten spelling would red here — a false hold whose repair is to restate the claim,
    never to loosen this arm.

    ⛔ The LOOP SEARCH applies the same scope rule as the store half (`_walk_own_scope`, `#12020`
    round 5). While it was a bare `ast.walk`, the ENTIRE claim could be moved into a nested function
    the writer never calls and both arms still held — measured on the real `tortoise/sdk.py`: the
    control held, and `_update_entity`'s declared `for` re-indented into `def _never_called():` held
    too, so the row asserted "the writer's own loop iterates the constant" about a loop it never
    runs. The store half had carried this rule since round 4; the search that finds the loop is the
    other half of the same claim.

    ⛔ `merge_store` is the `MERGE_STORE_CLAIM` half of the same declaration: the one row whose
    store genuinely IS a map merge (`tortoise.sdk::_update_entity`, `SET n += $p`) is scored by the
    narrower `_STORE_CARRIER` everywhere else, and opts into the merge form HERE and in the
    statement arm — never by widening the generic pattern (#12020 review round 5, Finding 2).
    """
    for sub in _walk_own_scope(node):
        if isinstance(sub, ast.For):
            iterable, body = sub.iter, [sub]
            target = sub.target
            body_nodes = [*sub.body, *sub.orelse]
        elif isinstance(sub, _COMPREHENSION_LIKE) and len(sub.generators) == 1:
            iterable, body = sub.generators[0].iter, [sub]
            target = sub.generators[0].target
            elements = ([sub.key, sub.value] if isinstance(sub, ast.DictComp) else [sub.elt])
            body_nodes = [*elements, *sub.generators[0].ifs]
        else:
            continue
        if not (isinstance(iterable, ast.Name) and iterable.id == constant):
            continue
        # ⛔ The loop's OWN binding, never every `Store` name in its subtree (#12020 round 3). The
        # looser form passed a source that re-binds the loop variable — `label = "Point"` as the
        # body's first statement — while every store still interpolates `{label}`, so the row
        # declared five labels and the code wrote one (`_Point`) with the whole guard green.
        bound = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        # ⛔ A COMPREHENSION'S OWN TARGET is not a RE-BINDING of the loop variable: `[x for label in
        # rows]` binds `label` in the COMPREHENSION's scope, which is the rule `_shadowed_names`
        # applies on the probe side. Counting it red'd a legitimate refactor (measured: a source
        # whose loop is otherwise unchanged plus `_ = [x for label in rows]` returned "RE-BOUND",
        # so the arm failed a writer that still derives every label it stores).
        comprehension_targets = {
            id(elt)
            for b in body_nodes
            for sub in _walk_own_scope(b)
            if isinstance(sub, _COMPREHENSION_LIKE)
            for gen in sub.generators
            for elt in ast.walk(gen.target)
        }
        # …and a RE-BINDING of that name inside the loop body means the store is no longer built
        # from the ITERATED value. This is the half that sees the mutation above: the binding stops
        # holding what the loop produced, so the claim `the labels come from <constant>` is false.
        rebound = {n.id for b in body_nodes for n in _walk_own_scope(b)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                   and n.id in bound and id(n) not in comprehension_targets}
        if rebound:
            return (
                f"`{'`, `'.join(sorted(rebound))}` is RE-BOUND inside the loop over {constant}, so "
                "the store is no longer built from the iterated value — the labels are no longer "
                "derived from that constant")
        # ⛔ `#12020` round 4: the store must execute in the LOOP's OWN scope. `ast.walk` descends
        # into a nested `def`/`class`/`lambda`, so the same defect round 2 fixed in
        # `_carrying_statements` was still open here: a store moved into a nested function the loop
        # never calls — or one whose PARAMETER shadows the loop binding, or a class-body string —
        # satisfied the row while nothing stored per iteration (measured: all three GREEN).
        hits = [n for outer in body for n in _walk_own_scope(outer)
                if isinstance(n, ast.stmt)
                and (_STORE_CARRIER.search(_slice_own_scope(lines, n))
                     or (merge_store
                         and _MERGE_STORE_CARRIER.search(_slice_own_scope(lines, n))))]
        # ⛔ The MINIMAL store statements: without this, the enclosing `for` itself matches (its text
        # contains every store in its body) and any mention of the binding anywhere inside would
        # satisfy the target check — i.e. the arm would degrade to "something in the loop names it".
        stores = [
            n for n in hits
            if not any(other is not n and any(c is other for c in ast.walk(n)) for other in hits)
        ]
        if not stores:
            continue
        # …and the TARGET drives it: the store statement must name the loop's binding, otherwise the
        # loop is beside the store rather than the thing that produces it.
        if any(any(f"{{{t}" in _slice_own_scope(lines, s) for t in bound) for s in stores):
            return None
    return (
        f"no `for … in {constant}` drives a store — the iterable is not that constant, or its "
        "binding is not what the store clause is built from"
    )


#: The comprehension forms, for the loop half of `_iterated_store_problem`.
_COMPREHENSION_LIKE = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

#: Node types that open a NEW SCOPE: a statement inside one executes in that scope, not the
#: enclosing one, so a claim about the enclosing scope must not be satisfied through it.
_SCOPE_LIKE = (*_SCOPE_NODES, ast.Lambda)


def _walk_own_scope(node: ast.AST):
    """`ast.walk(node)`, but never crossing into a nested `def`/`class`/`lambda`.

    ⛔ The same rule `_carrying_statements` applies (`#12020` rounds 1-2), in walk form so the
    sibling arm added by this PR can apply it too rather than each helper growing its own copy of
    "stop at a scope boundary". Round 4 measured the cost of not having it: `_iterated_store_problem`
    credited a store inside a nested, never-called function, which is a row that cannot fail.
    """
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(child for child in ast.iter_child_nodes(current)
                     if not isinstance(child, _SCOPE_LIKE))


def _parent_map(root: ast.AST) -> dict[int, ast.AST]:
    """`id(node) -> node`'s parent, for every node under `root`."""
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _enclosing_statements(statement: ast.stmt, parents: dict[int, ast.AST],
                          stop: ast.AST) -> list[ast.stmt]:
    """`statement`'s enclosing statements, innermost first, stopping BELOW `stop`.

    `stop` is the cited symbol: ancestry is bounded there on purpose, so a store in a caller or in a
    sibling function cannot satisfy this writer's claim."""
    found: list[ast.stmt] = []
    node: ast.AST | None = parents.get(id(statement))
    while node is not None and node is not stop:
        if isinstance(node, ast.stmt):
            found.append(node)
        node = parents.get(id(node))
    return found


def _evidence_violation(source: str, symbol: str, needle: str,
                        *, iterated: str | None = None,
                        claim: str = "store") -> str | None:
    """Why `source` no longer carries the claim its `LABEL_EVIDENCE` row makes, or None.

    Two arms, and BOTH must hold for at least one definition of `symbol`:

    1. **The statement arm** (`#11954`): the needle must be carried by a STATEMENT, and that
       statement's own subtree must also carry a store — i.e. the claim and the store are the same
       statement's, not two facts in one body. ⛔ The substring form this replaces (`needle in
       body`) passed a source whose loop had been re-targeted with the needle preserved in an
       unrelated `if` (measured on the module at `#11954`'s head); this one does not.
       `claim="bound"` is for the two rows whose needle is a SEAM THAT BOUNDS the label set rather
       than a store clause (`restore_graph` interpolates its labels, `_event_raw_write_door`
       executes its caller's statement): the store is deliberately un-nameable there, so the arm
       asks only that the claim is carried by a real statement — and the COMPLETENESS of those two
       rests on `LABEL_CONSTANTS`, which the pin below re-derives in both directions and which this
       function therefore requires to exist for every bound row.
       `claim="merge"` is for the ONE row whose store is a map merge (`MERGE_STORE_CLAIM`): its
       vector is merged in as data the probe itself calls undecidable
       (`UNDECIDABLE_WRITE_SHAPES`), so it is scored by the narrower rule plus that named kind —
       never by widening `_STORE_CARRIER`, which would make every other row green with its vector
       clause deleted (`#12020` review round 5, Finding 2).
    2. **The iterated-store arm**, for a row that names the constant its loop iterates: a `for` over
       that constant whose target drives the store (`_iterated_store_problem`).

    Split out of the pin so a MUTATION can drive it: a guard that can only be checked against the
    tree's current content cannot be checked at all once the content is right (the same reason
    `register.load` is public).
    """
    lines = source.splitlines()
    tree = ast.parse(source)
    constants = _module_constants(tree, lines)
    constant_text = {name: text for name, (text, _node) in constants.items()}
    definitions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol
    ]
    if not definitions:
        # ⛔ NO VACUOUS PASS. A symbol that cannot be read is a claim that cannot be checked, and
        # `any(...)` over an empty list of bodies answers False only by accident of the generator.
        return f"{symbol} is not defined in this source, so the claim cannot be re-derived"
    problems: list[str] = []
    for node in definitions:
        universe: list[ast.AST] = [node]
        universe += [constants[name][1]
                     for name in _constant_names_named_by(node, constant_text)]
        carriers = _carrying_statements(universe, lines, needle)
        if not carriers:
            problems.append(
                f"no STATEMENT in {symbol} carries {needle!r} — a docstring or a comment is not a "
                "store, and the claim has to be carried by code")
            continue
        parents = _parent_map(node)
        carried = []
        for statement, text in carriers:
            view = text + "\n" + "\n".join(
                constant_text[name] for name in _constant_names_named_by(statement, constant_text)
            )
            if claim == "bound" or _STORE_CARRIER.search(view):
                carried.append(text)
            elif claim == "merge" and _MERGE_STORE_CARRIER.search(view):
                # The declared weaker kind (`MERGE_STORE_CLAIM`): the store IS the map merge, whose
                # vector content no syntax can attribute (`UNDECIDABLE_WRITE_SHAPES`). Kept as a
                # NAMED claim so every other row is scored by the narrower `_STORE_CARRIER`.
                carried.append(text)
            elif claim == "assembly":
                # The store clause is ASSEMBLED across neighbouring statements (the label in the
                # statement that issues the query, the clause built one statement earlier), so the
                # claim this row makes is "the label statement and a store are the same BLOCK's".
                # Walked up from the carrier to the cited symbol, never out of it — a store in
                # another function cannot satisfy it.
                for ancestor in _enclosing_statements(statement, parents, node):
                    if _STORE_CARRIER.search(_slice_own_scope(lines, ancestor)):
                        carried.append(text)
                        break
        if not carried:
            problems.append(
                f"{needle!r} is carried by {len(carriers)} statement(s) in {symbol}, and none of "
                "them stores anything — the claim and the store have come apart, which is what a "
                "re-targeted loop looks like to a substring test")
            continue
        if iterated is None:
            return None
        problem = _iterated_store_problem(node, iterated, lines,
                                         merge_store=(claim == "merge"))
        if problem is None:
            return None
        problems.append(f"{symbol}: {problem}")
    return "; ".join(problems)


#: The needle the two `_STORE_CARRIER` / `_carrying_statements` pins below share — the same string
#: `LABEL_EVIDENCE["tortoise.sdk::_update_entity"]` searches for, so the pins exercise the real key.
_PROSE_NEEDLE = "in _CANONICAL_ENTITY_ID_PROPS:"


def _function_of(source: str, name: str) -> ast.FunctionDef:
    """The single `def name` in `source` — the direct input to `_carrying_statements`."""
    return next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_iterated_arm_does_not_credit_a_store_in_a_NESTED_scope() -> None:
    """`#12020` round 4: the iterated arm must see a store in the LOOP's own scope, no deeper.

    The rule was established in `_carrying_statements` (rounds 1-2) and the sibling arm this same
    PR added never got it — so `ast.walk` descended into nested scopes and three shapes measured
    GREEN on the reviewed head while nothing stored per iteration:

    * a store moved into a nested function the loop NEVER CALLS;
    * a store in a nested function whose PARAMETER shadows the loop binding (so the interpolation is
      that parameter, not the iterated value);
    * a store shape inside a nested CLASS BODY.

    Synthetic sources rather than `sdk.py` mutations, because the point is the helper's scope rule
    and a 12-line fixture states it without three `replace()` calls that could silently no-op —
    the defect rounds 2 and 3 each had to fix. The real tree is covered by the row pin.
    """

    def problem(body: str) -> str | None:
        source = ('_KEYS = ("A", "B")\n\n\ndef w(proj):\n'
                  '    for label, _prop in _KEYS:\n' + body)
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "w")
        # `merge_store=True` because this fixture is deliberately the REAL `_update_entity` store
        # shape (a map merge). The row that uses it is the declared `MERGE_STORE_CLAIM` one; the
        # property under test here is the SCOPE rule, not which carrier form is credible.
        return _iterated_store_problem(fn, "_KEYS", source.splitlines(), merge_store=True)

    # CONTROL — the store in the loop's own body holds, so the arms below red for the SCOPE and not
    # because the helper has become always-red.
    assert problem('        proj.g.query(f"MATCH (n:{label}) SET n += $p")\n') is None, (
        "the control no longer holds — every arm below would pass for the wrong reason")

    never_called = problem(
        '        def _helper():\n'
        '            proj.g.query(f"MATCH (n:{label}) SET n += $p")\n')
    assert never_called is not None, (
        "a store inside a nested function the loop never calls satisfies the iterated arm — "
        "nothing stores per iteration")

    shadowing_param = problem(
        '        def _helper(label):\n'
        '            proj.g.query(f"MATCH (n:{label}) SET n += $p")\n')
    assert shadowing_param is not None, (
        "a nested function whose PARAMETER shadows the loop binding satisfies the iterated arm — "
        "the store is built from the parameter, not from the iterated value")

    nested_class = problem(
        '        class _K:\n'
        '            q = f"MATCH (n:{label}) SET n += $p"\n')
    assert nested_class is not None, (
        "a nested CLASS BODY satisfies the iterated arm — a class attribute is not a per-iteration "
        "store")

    # ⛔ The FALSE-RED direction of the same scope rule (`#12020` round 5): a comprehension binds its
    # own target in its own scope, so `[x for label in rows]` inside the loop body rebinds NOTHING
    # the loop's store reads. Counting it as a re-binding red'd a writer that still derives every
    # label it stores — the probe's `_shadowed_names` already skips comprehension targets.
    comprehension = problem(
        '        _ = [x for label in ("C",)]\n'
        '        proj.g.query(f"MATCH (n:{label}) SET n += $p")\n')
    assert comprehension is None, (
        "a comprehension's OWN target counted as a re-binding of the loop variable — `[x for label "
        "in rows]` binds in the comprehension's scope, so this reds correct code")


def test_the_iterated_arm_finds_its_loop_in_the_WRITERS_own_scope() -> None:
    """`#12020` round 5: the arm's LOOP SEARCH must apply the scope rule its store half already has.

    `_iterated_store_problem` located the `for` with a bare `ast.walk(node)`, which descends into
    nested `def`/`class`/`lambda`. So the WHOLE claim could be moved one level down and both arms
    still held: measured on the real `tortoise/sdk.py` at the reviewed head, the control
    (`_evidence_violation(..., iterated="_CANONICAL_ENTITY_ID_PROPS")`) returned `None` (holds) and
    the same source with `_update_entity`'s declared `for` re-indented into `def _never_called():`
    ALSO returned `None` — a row that cannot fail, about a loop the writer never runs.

    Synthetic, for the reason the sibling test states: a 10-line fixture pins the helper's scope
    rule without a `replace()` that could silently no-op, and the real tree stays covered by the row
    pin (`test_every_declared_label_re_derives_at_this_head`).
    """

    def problem(loop_indent: str, body: str) -> str | None:
        source = ('_KEYS = ("A", "B")\n\n\ndef w(proj):\n' + loop_indent + body)
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "w")
        # `merge_store=True`: the fixture deliberately uses the REAL `_update_entity` store shape
        # (a map merge), which only the declared `MERGE_STORE_CLAIM` row may lean on. The property
        # under test is the LOOP's scope, not which carrier form is credible (#12020 round 5,
        # Finding 2).
        return _iterated_store_problem(fn, "_KEYS", source.splitlines(), merge_store=True)

    loop = ('for label, _prop in _KEYS:\n'
            '            proj.g.query(f"MATCH (n:{label}) SET n += $p")\n')

    # CONTROL — a loop in the writer's OWN body holds, so the arm below reds for the SCOPE and not
    # because the helper has become always-red.
    assert problem('    ', loop) is None, (
        "the control no longer holds — the nested-scope arm below would pass for the wrong reason")

    nested = problem('    def _never_called():\n        ',
                     'for label, _prop in _KEYS:\n'
                     '                proj.g.query(f"MATCH (n:{label}) SET n += $p")\n')
    assert nested is not None, (
        "a loop inside a nested function the writer never calls satisfies the iterated arm — the "
        "row would assert that the writer's own loop iterates the constant, about a loop it never "
        "runs")


def test_the_store_carrier_does_not_credit_a_comparison_or_a_wipe() -> None:
    """`#12020` review Finding 1: `_STORE_CARRIER` proves a STORE, so it must not credit these.

    Measured at the reviewed head with the shipped regex: `if t.embedding == prior_ch:` matched
    `t.embedding =`, and `SET n.embedding = null` matched too (only the UPPERCASE spelling was
    excluded). Both credit a statement that stores nothing, which is the over-credit direction this
    row exists to close — and `#11938`-class rows are exactly the shape that would sit in a
    comparison, so it would have gone live with the next row rather than on this tree.
    """
    credited = {
        # A COMPARISON is not an assignment.
        "if t.embedding == prior_ch:",
        "if p.embedding == q:",
        "return t.embedding == 1",
        # The NULL WIPE, in every spelling — Cypher is case-insensitive, so `= null` clears the
        # property exactly as `= NULL` does.
        "SET n.embedding = NULL",
        "SET n.embedding = null",
        "SET n.embedding=Null",
        "SET n.embedding = null RETURN n",
        # Not a store either: no `=` to assign with.
        "if p.embedding >= 1:",
        "t.embedding != q",
    }
    wrongly = sorted(s for s in credited if _STORE_CARRIER.search(s))
    assert not wrongly, (
        "_STORE_CARRIER credits a statement that stores nothing — that is a false PASS in the "
        f"statement arm, which is the direction this row exists to close: {wrongly}")

    stores = {
        "n.embedding = $v",                       # a live assignment, the form the alias rows use
        "SET n.embedding = nullify",               # a VALUE named nullify is a store, not a wipe
        "set_embedding_clause(n, vec)",
        "clear_then_set_prefix(n)",
    }
    missed = sorted(s for s in stores if not _STORE_CARRIER.search(s))
    assert not missed, (
        "_STORE_CARRIER no longer sees a real store — the narrowing above went too far and the "
        f"statement arm now reds on rows that hold: {missed}")

    # ⛔ `#12020` review round 5, Finding 2: the bare MAP MERGE is NOT a generic store, and this is
    # the pin that keeps it out. It used to be credited here, and the cost was measured: deleting
    # `set_embedding_clause(...)` from `_event_plain_merge`'s and `_session_event_write`'s query and
    # leaving `SET e += $props` kept BOTH rows green with no vector written at all. The repo's own
    # probe classes that shape undecidable (`UNDECIDABLE_WRITE_SHAPES`: whether the merged map
    # carries an `embedding` is DATA FLOW, not syntax), so the generic arm may not decide it; the one
    # row that genuinely stores this way DECLARES it in `MERGE_STORE_CLAIM` and is scored by
    # `_MERGE_STORE_CARRIER`.
    assert not _STORE_CARRIER.search("SET n += $p"), (
        "the bare map-merge is credited as a generic store again — every `store` row then stays "
        "green with its vector clause deleted (measured), which is the false PASS this arm exists "
        "to close")
    assert _MERGE_STORE_CARRIER.search("SET n += $p"), (
        "`_MERGE_STORE_CARRIER` no longer sees the merge form, so the ONE declared row that stores "
        "through it would red for the wrong reason")
    assert frozenset({"tortoise.sdk::_update_entity"}) == MERGE_STORE_CLAIM, (
        "the weak-evidence claim kind grew beyond the single row it was declared for — a row added "
        f"here stops redding when its vector clause is deleted: {sorted(MERGE_STORE_CLAIM)}")


def test_a_deleted_vector_clause_or_an_UNCALLED_store_reds_the_row() -> None:
    """`#12020` review round 5, Findings 2 and 3 — DRIVEN on the real tree, not described.

    Both are FALSE PASSES that the shipped guard could not see, found by review and measured on
    `origin/main` + this branch before the fix. Each mutation is applied to the real
    `tortoise/projection/entities.py` in memory, and the unmutated control is asserted FIRST so a
    mutation that quietly stopped applying cannot pass as a red.

    (1) **The vector clause deleted, the map merge left.** `_STORE_CARRIER` used to credit a bare
        `SET n += $p`, so removing `set_embedding_clause(...)` from both `ON CREATE`/`ON MATCH`
        halves left the row GREEN with no vector written at all. The premise was wrong as well as
        unsafe: `tools/dod_5090_probe.py` `UNDECIDABLE_WRITE_SHAPES` states that whether a merged
        map carries an `embedding` is DATA FLOW, not syntax. Only the one declared row
        (`MERGE_STORE_CLAIM`) may lean on it now.
    (2) **The store moved into an UNCALLED nested `def`.** `_carrying_statements` skipped the scope
        NODE while still walking its body, so the statement inside the nested function was still a
        carrier — the row stayed green while the writer executed no store. Round 4 had closed the
        identical hole in `_iterated_store_problem`; the two arms now share `_walk_own_scope`.
    """
    key = "tortoise.projection.entities::_event_plain_merge"
    rel, symbol, needle, _labels = LABEL_EVIDENCE[key]
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    claim = _claim_for(key)
    assert claim == "store", (
        f"{key} is graded as {claim!r} — this pin is about the NARROW rule, so it must not lean on "
        "the merge exception")

    def violation(text: str) -> str | None:
        return _evidence_violation(text, symbol, needle, claim=claim)

    # CONTROL — the tree carries the claim, so every mutant below starts from a green row.
    assert violation(source) is None, violation(source)

    # (1) `set_embedding_clause(...)` removed from BOTH halves; the map merge stays.
    clause = (
        '            "ON CREATE SET e += $props, " + set_embedding_clause("e", preserve_on_null=False) + " "\n'
        '            "ON MATCH SET e += $props, " + set_embedding_clause("e"),\n'
    )
    assert clause in source, "the vector clause moved — re-derive this pin, do not delete it"
    no_clause = source.replace(
        clause,
        '            "ON CREATE SET e += $props "\n'
        '            "ON MATCH SET e += $props",\n',
        1,
    )
    assert no_clause != source
    assert "set_embedding_clause" not in _slice(
        no_clause.splitlines(), _function_of(no_clause, symbol)), (
        "the mutation did not remove the clause — the arm below would red for the wrong reason")
    assert violation(no_clause) is not None, (
        "a store row stays GREEN with its vector clause deleted and only a map merge left — that "
        "is the false PASS `_STORE_CARRIER`'s map-merge alternative caused")

    # (2) The real carrier moved into a nested `def` the function never calls.
    carrier = (
        '        self.g.query(\n'
        '            "MERGE (e:Event {eventId: $eid}) "\n'
        '            "ON CREATE SET e += $props, " + set_embedding_clause("e", preserve_on_null=False) + " "\n'
        '            "ON MATCH SET e += $props, " + set_embedding_clause("e"),\n'
        '            params={"eid": eid, "props": props, "embedding": embedding},\n'
        '        )\n'
    )
    assert carrier in source, "the carrier moved — re-derive this pin, do not delete it"
    uncalled = source.replace(
        carrier,
        "        def _dead():\n"
        + "\n".join("    " + line for line in carrier.rstrip("\n").split("\n"))
        + "\n        # `_dead()` is never called: this writer now executes no store at all.\n",
        1,
    )
    assert uncalled != source
    assert violation(uncalled) is not None, (
        "a store inside an UNCALLED nested scope still satisfies the row — that is a row which "
        "cannot fail, and it is the identical hole round 4 closed in the iterated arm")


def test_a_bare_string_statement_is_not_a_carrier() -> None:
    """`#12020` review Finding 2: the docstring skip is LOAD-BEARING, and this is its only pin.

    ⛔ Measured at the reviewed head: DELETING the skip in `_carrying_statements` left every
    other assertion in this file with its verdict — all 10 `LABEL_EVIDENCE` rows still passed, and
    the prose arm still reded (through a different arm's message). A rule no mutation can observe
    is not pinned, and an assertion whose message says the rule is proved tells the next reader
    something the test does not establish.

    This is the assertion whose verdict FLIPS on that mutation: with the skip the bare string is
    not a carrier (`[]`); without it the `Expr` is returned. The control below keeps it honest in
    the other direction, so it cannot pass by the helper having become always-empty.
    """
    prose_only = (
        "def _f() -> int:\n"
        f'    "{_PROSE_NEEDLE}"\n'
        "    return 1\n"
    )
    hits = _carrying_statements(
        [_function_of(prose_only, "_f")], prose_only.splitlines(), _PROSE_NEEDLE)
    assert hits == [], (
        "a bare string statement counts as a carrier — prose is not code, and the skip in "
        f"_carrying_statements is gone or inert: {[(type(n).__name__, t) for n, t in hits]}")

    # CONTROL — a REAL statement carrying the same text IS returned, so the assertion above cannot
    # pass merely because the helper answers nothing.
    real = (
        "def _f() -> None:\n"
        "    for label, _prop in _CANONICAL_ENTITY_ID_PROPS:\n"
        "        pass\n"
    )
    found = _carrying_statements(
        [_function_of(real, "_f")], real.splitlines(), _PROSE_NEEDLE)
    assert len(found) == 1 and isinstance(found[0][0], ast.For), (
        f"a real statement no longer carries the needle, so the pin above proves nothing: {found}")

    # ⛔ `#12020` review round 2, Finding 2: a NESTED scope's body is not a carrier either. Skipping
    # only the `universe` ROOTS left this hole open one level down — measured: the same prose inside
    # a nested `def` satisfied the statement arm, because the nested `FunctionDef`'s slice contains
    # the nested body's stores. The real nested statement is still found (asserted below), so the
    # exclusion removes a body from being a "carrier", never the statements inside it.
    nested_prose = (
        "def _f() -> None:\n"
        "    def _inner() -> None:\n"
        f'        "{_PROSE_NEEDLE}"\n'
        '        g.query("SET n += $p")\n'
        "    _inner()\n"
    )
    hits_nested = _carrying_statements(
        [_function_of(nested_prose, "_f")], nested_prose.splitlines(), _PROSE_NEEDLE)
    assert hits_nested == [], (
        "a NESTED scope's docstring counts as a carrier — the statement arm can be satisfied by "
        f"prose one level down: {[(type(n).__name__, t) for n, t in hits_nested]}")
    # ⛔ `#12020` review round 5, Finding 3: a real statement INSIDE a nested scope is NOT a carrier
    # either, and this assertion used to say the opposite. Measured: wrap the real carrier of
    # `_event_plain_merge` in a `def _dead()` that is never called and the row stayed GREEN while the
    # writer executed no store at all. `_walk_own_scope` now stops the walk at the scope boundary, so
    # the enclosing scope's evidence cannot come from a function it does not call. This is the same
    # rule round 4 applied in `_iterated_store_problem`; the two arms are one rule again.
    nested_real = (
        "def _f() -> None:\n"
        "    def _inner() -> None:\n"
        "        for label, _prop in _CANONICAL_ENTITY_ID_PROPS:\n"
        "            pass\n"
        "    _inner()\n"
    )
    hits_nested_real = _carrying_statements(
        [_function_of(nested_real, "_f")], nested_real.splitlines(), _PROSE_NEEDLE)
    assert hits_nested_real == [], (
        "a statement inside a nested, UNCALLED scope is credited to the enclosing symbol — that is "
        "a row that cannot fail: the writer executes no store and the pin stays green. "
        f"{[(type(n).__name__, t) for n, t in hits_nested_real]}")
    # …and the OWN-scope carrier of the same text is still found, so the assertion above cannot pass
    # because the helper answers nothing for this source.
    own_scope_real = (
        "def _f() -> None:\n"
        "    for label, _prop in _CANONICAL_ENTITY_ID_PROPS:\n"
        "        pass\n"
    )
    hits_own = _carrying_statements(
        [_function_of(own_scope_real, "_f")], own_scope_real.splitlines(), _PROSE_NEEDLE)
    assert len(hits_own) == 1 and isinstance(hits_own[0][0], ast.For), (
        f"an OWN-scope statement stopped being a carrier — the exclusion went too far: {hits_own}")



def _declared_symbol_bodies(key: str) -> list[str]:
    """Every definition of `module::symbol` in its own file, PLUS one hop of module constants.

    One hop because a store clause can be held in a module-level constant the writer executes
    (`_TURN_WRITE_CYPHER`) — the same reader rule `test_every_declared_label_re_derives_at_this_head`
    applies to its cited needles. ⛔ Whole-line `#` comments are stripped (`_without_comments`): a
    comment quoting a clause is not a store, and one such comment sits inside the very span this
    reader returns, which is how a whole-file `in` check stayed green on a mutated source. ⚠️ The
    boundary, stated rather than implied: DOCSTRINGS and TRAILING comments are NOT stripped, so a
    clause quoted in either still satisfies a needle. Closing that needs AST-range blanking of
    `STRING`/trailing-`COMMENT` tokens, not a line filter; it is a residual, not a claim of coverage.
    (⚠️ Scope that precisely: the DOCSTRING `STRING` and the trailing `COMMENT` token. Blanking every
    `STRING` would erase the Cypher held in string CONSTANTS — `_TURN_WRITE_CYPHER` — which is the
    very text a positive needle must still find.)
    """
    module, symbol = key.split("::", 1)
    if "/" in module or module.endswith(".py"):
        rel = module
    else:
        candidate = module.replace(".", "/")
        # ⛔ PACKAGE first: `tortoise.projection` is a package whose `__init__.py` defines the
        # symbol, and a thin `tortoise/projection.py` shim also exists beside it. Taking the
        # sibling module first made every `tortoise.projection::*` key resolve to the shim, which
        # defines nothing — so the pin would have read as "the writer is gone" for four entries.
        rel = (f"{candidate}/__init__.py" if (REPO_ROOT / candidate).is_dir()
               else f"{candidate}.py")
    path = REPO_ROOT / rel
    assert path.is_file(), (key, rel)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            text = "\n".join(lines[node.lineno - 1:node.end_lineno])
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = text
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            constants[node.target.id] = "\n".join(lines[node.lineno - 1:node.end_lineno])
    bodies: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == symbol):
            own = _without_comments("\n".join(lines[node.lineno - 1:node.end_lineno]))
            bodies.append(own + "\n" + "\n".join(
                _without_comments(text) for text in _constants_named_by(node, constants)))
    assert bodies, f"{key} cites {rel}::{symbol}, which that file does not define"
    return bodies


def test_a_no_live_vector_declaration_executes_no_store_clause() -> None:
    """`#11492` round-2 review (P2): the OTHER half of `stores` is re-derived at HEAD too.

    `test_every_declared_label_re_derives_at_this_head` covers the label-tuple entries, because a
    label is a value it can compare. The `NO_LIVE_VECTOR` / `LABELS_AT_RUNTIME` entries had no
    automated check at all — and that is exactly the half round 1 measured **2 of 12 WRONG** in
    (`_write_capture_turns` and `_event_raw_write_door` each declared `no-live-vector` while
    executing a live vector store). The residual paragraph claimed the label test was the
    mitigation for a false `NO_LIVE_VECTOR`; it never reached that half. This narrows that half to
    the shapes it can read — the clause builders, the dense constructor, AND a direct
    `SET <alias>.embedding = …` (the instrument's own discovery shape).

    ⛔ **No population COUNTS in this sentence on purpose.** The `2 of 12` above is a dated
    measurement and says so; every present-tense count that used to sit here ("the eight
    label-tuple entries", "the twelve … entries") went stale the moment `#11641` moved two writers
    out of this population and into the tuple bucket, and nothing could have redded for it. The
    assertions below read the live population; prose must not restate it.

    ⛔ Stated at its true size: this proves a `no-live-vector` symbol's own body (plus one hop of
    module constants) executes no store clause. It does NOT prove the writer stores nothing — and
    a door that EXECUTES A CALLER-SUPPLIED statement is outside this population altogether.
    `#11641` gave that door a BOUND rather than a body-shaped pin: it owns
    `sdk::_RAW_WRITE_DOOR_LABELS` and refuses a statement naming any other label, and
    `LABEL_EVIDENCE` now carries a needle for it. ⚠️ The bound narrows that half; it does not close
    it: review still catches WHICH label the door's own declaration names, and the bound only
    catches a label OUTSIDE it.
    """
    no_vector = sorted(key for key, entry in EMBED_WRITER_CLASSES.items()
                       if entry.stores == NO_LIVE_VECTOR)
    assert no_vector, "no declaration is `no-live-vector` — this pin would assert nothing"
    # The exemption table may not outlive what it exempts: a key that is no longer a
    # `no-live-vector` declaration is a stale excuse, and a stale excuse is how a real store
    # gets through later.
    assert set(NO_STORE_EXEMPTIONS) <= set(no_vector), (
        "exemptions for writers that are no longer `no-live-vector`: "
        f"{sorted(set(NO_STORE_EXEMPTIONS) - set(no_vector))}"
    )
    offenders: dict[str, list[str]] = {}
    for key in no_vector:
        if key in NO_STORE_EXEMPTIONS:
            continue
        bodies = _declared_symbol_bodies(key)
        hits = [m for m in STORE_CLAUSE_MARKERS if any(m in b for b in bodies)]
        hits += [f"/{p.pattern}/" for p in STORE_CLAUSE_PATTERNS
                 if any(p.search(b) for b in bodies)]
        if hits:
            offenders[key] = hits
    assert not offenders, (
        "these writers are declared `no-live-vector` but their own body executes a store clause "
        f"{offenders} — row 2 REMOVES such a writer from the map it audits, so a real store would "
        "still print `stored-without-a-query-path: none`. Declare the label, or add a reasoned "
        "entry to NO_STORE_EXEMPTIONS"
    )


def test_the_store_clause_markers_still_match_a_real_store() -> None:
    r"""The marker tuple needs a falsifier in the POSITIVE direction (round-3 review, P1).

    ⛔ Measured at `b8ada30ce1b`: setting `STORE_CLAUSE_MARKERS = ()` left this WHOLE file green
    (26 passed) — so the guard's primary mechanism could be deleted by any later "cleanup" with
    nothing red. `test_a_no_live_vector_declaration_executes_no_store_clause` asserts only the
    ABSENCE of offenders, and the sibling positive pin below covers `STORE_CLAUSE_PATTERNS` — not
    this tuple. Every `set_embedding_clause(`-shaped store is seen ONLY by these markers, so
    their detection power was the one thing nothing re-derived.

    The property pinned: each marker is still found in the body of a writer that DECLARES a label,
    through the SAME reader the guard uses (`_declared_symbol_bodies`). A marker that matches no
    storing writer cannot see the store it exists for, and a dead marker is exactly what a
    `no-live-vector` declaration would then hide behind.
    """
    assert STORE_CLAUSE_MARKERS, (
        "the store-clause marker tuple is gone — a `no-live-vector` writer storing only via "
        "`set_embedding_clause(…)` would pass this suite silently"
    )
    labelled = {key for key, entry in EMBED_WRITER_CLASSES.items()
                if not isinstance(entry.stores, str)}
    assert labelled, "no declaration names a label — the markers have nothing to be measured against"
    dead = [marker for marker in STORE_CLAUSE_MARKERS
            if not any(marker in body
                       for key in labelled for body in _declared_symbol_bodies(key))]
    assert not dead, (
        f"{dead} no longer appears in the body of any writer that DECLARES a label — a marker "
        "that matches nothing cannot see the store it exists for, and the absence-of-offenders "
        "pin would stay green over it. Re-derive the marker, or name the store it missed"
    )


def test_the_direct_store_pattern_sees_a_store_and_not_a_wipe() -> None:
    r"""The pattern above needs a falsifier in the POSITIVE direction (round-3 review, P2).

    ⛔ Measured by the reviewer: setting `STORE_CLAUSE_PATTERNS = ()` left
    `test_a_no_live_vector_declaration_executes_no_store_clause` GREEN — so the guard that closes
    the direct-`SET` hole could be deleted by any later "cleanup" with nothing red, which is the
    false pass it exists to prevent. Absence of offenders is not evidence the pattern works; the
    pattern has to be shown to MATCH. Both directions are pinned here, plus the link to discovery.

    ⛔ Stated residual (do not overclaim this pin): it covers the DOTTED spelling (`n.embedding`),
    which is the one the `NO_LIVE_VECTOR` declarations are checked against. `_EMBED_WRITE`'s bare
    alternate (`(?<![\w.])embedding\s*=`, a keyword argument or a local assignment) is NOT covered
    — such a writer could still enter the population and pass declared `no-live-vector`. The
    dict-key carriers (`record["embedding"] = …`) are out of scope by design. And the lookahead is
    case-SENSITIVE, so `SET n.embedding = null` matches — a false RED, never a false pass.
    """
    assert STORE_CLAUSE_PATTERNS, (
        "the direct-store pattern is gone — a `no-live-vector` writer storing only via "
        "`SET n.embedding = $e` would pass this suite"
    )
    def hits(text: str) -> bool:
        return any(p.search(text) for p in STORE_CLAUSE_PATTERNS)

    # MUST MATCH — the shapes that are a real store.
    assert hits("SET n.embedding = $e"), "a direct labelled store must be seen"
    assert hits('SET n.embedding="$x"'), "a quoted store must be seen"
    assert hits("SET n.embedding=$e"), "a store with no inner spaces must be seen"
    # MUST NOT MATCH — the wipe, which is not a store. The whitespace sits INSIDE the lookahead on
    # purpose: `\s*(?!NULL)` backtracks `\s*` to zero characters and then tests the space, so it
    # matches the wipe too (measured — it flagged `_prewipe_synthetic_write_scope`).
    assert not hits("SET n.embedding = NULL"), "the wipe must not read as a store"
    assert not hits("SET n.embedding=NULL"), "the wipe must not read as a store"
    # …and the two must not DRIFT on a STORE: whatever `_EMBED_WRITE` — the instrument's OWN
    # discovery clause — calls a write, this pin must also see, or a writer can enter the
    # population through a clause the pin cannot read. The ONE deliberate divergence is the WIPE:
    # `_EMBED_WRITE` matches `= NULL` as well (it keys on the assignment, not the value), and this
    # pin must NOT — so the divergence is asserted rather than assumed.
    for sample in ("SET n.embedding = $e", "SET n.embedding=$e"):
        assert probe._EMBED_WRITE.search(sample), sample
        assert hits(sample), (
            f"discovery sees a store in {sample!r} but the no-live-vector pin does not — "
            "a writer can enter the population through a clause this pin cannot read"
        )
    assert probe._EMBED_WRITE.search("SET n.embedding = NULL"), (
        "the wipe is expected to match discovery; if that changed, re-read this divergence"
    )
    assert not hits("SET n.embedding = NULL"), "the pin must diverge from discovery on the wipe"


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
    # A container-key store in a MODULE body or a MODULE-LEVEL class body (round-14 review): the walk
    # visits `def`s (`probe._embedding_writers` iterates `FunctionDef`/`AsyncFunctionDef`), so the
    # literal-slice AST pass is never applied to it and `_EMBED_WRITE` has no function source to
    # read. It is a further reason a writer can be absent while holding a perfectly DECIDABLE key,
    # so it is pinned here rather than left to the header's prose — BOTH positions, because a
    # widening that reached one of them alone would leave the other described rather than pinned
    # (round-15 review).
    # ⛔ The scope is the WALK's, not the `class` keyword: a class body nested inside a `def` is
    # part of that def's SUBTREE, so the AST pass DOES reach it and the writer is CAUGHT — pinned by
    # the `class_inside_a_def` POSITIVE control below, so neither half of this entry can be read as
    # covering the other (round-16 review measured the over-claim on a scratch root).
    "module_scope_container_key": (
        'REC = {}\nREC["embedding"] = [0.25, 0.5]\n'
    ),
    "class_scope_container_key": (
        'class C:\n    REC = {}\n    REC["embedding"] = [0.25, 0.5]\n'
    ),
}

#: The blind shapes `tortoise/embed_writer_register.py` cites BY NAME as "pinned as
#: `INVISIBLE_SHAPES`' …". Coupled in BOTH directions against the table: every name here must be an
#: entry (so a pin deleted while the prose keeps citing it reds), and every name here must still
#: appear in the register's docstring (so a citation dropped from the prose reds too).
#:
#: ⛔ A DECLARED list, not one parsed out of the prose, and the reason is MEASURED: two attempts at a
#: derivation each FALSE-BLOCKED correct prose. Selecting on a `*_key` suffix left 6 of the table's
#: 10 names uncoupled (`map_merge`, `container_helper`, `module_constant_clause`, …) and stayed green
#: on a docstring citing `phantom_scope_shape`; selecting on POSITION (a window after the mention)
#: reds when the same true sentence also names the `class_inside_a_def` control, or when the names
#: precede the word, and a 400-char window silently misses a citation past it — measured round-19.
#: Prose admits no shape a pattern can pin without also rejecting correct English, so what this pins
#: is exactly the two names the register claims. A shape named in the prose and absent from this
#: tuple is NOT covered — stated, not implied.
REGISTER_CITED_SHAPES = (
    "class_scope_container_key",
    "module_scope_container_key",
)

#: The shape names `INVISIBLE_SHAPES` must hold — pinned by NAME SET, not by size. A size pin is
#: replaced by substitution (drop one shape, duplicate another, `len` unchanged), and the loop in
#: `test_the_DISCOVERY_blind_spot_is_pinned_not_merely_documented` is SILENT over a table that lost
#: an entry: measured in review round 17, deleting `module_scope_container_key` left this file green,
#: and replacing the whole table with `{}` left it green too — so the register's "pinned as
#: `INVISIBLE_SHAPES`' `module_scope_container_key` and `class_scope_container_key`" sentence would
#: have outlived the pins it names. A new blind shape belongs here AND in the register's "What the
#: closure claim does NOT cover" block.
INVISIBLE_SHAPE_NAMES = (
    # shapes neither pass can decide (the regex sees no `embedding =`/`set_embedding_clause(`,
    # and the AST pass inspects assignment targets only)
    "map_merge",
    "inline_property_map",
    "whole_node_replace",
    "interpolated_property",
    "container_helper",
    "computed_key",
    "module_constant_clause",
    "dict_literal_embedding_key",
    # positions the walk never enters (it visits `def`s only)
    "module_scope_container_key",
    "class_scope_container_key",
)


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
        # The OTHER half of the class-scope entry below, with the OPPOSITE expectation: a class body
        # nested inside a `def` lies in that def's SUBTREE, so the walk reaches it and the writer is
        # CAUGHT. Without this row, "a container key in a MODULE-LEVEL class body is absent" and
        # "CLASS scope is absent" are indistinguishable — and it was the second reading that stood in
        # the register until round 16, over-claiming by exactly the difference this control measures.
        "class_inside_a_def": (
            'def outer():\n    class C:\n        REC = {}\n        REC["embedding"] = [0.25, 0.5]\n'
        '    return C\n'
        'outer()\n'
        'def w(rec, e):\n    rec["embedding"] = e\n'
    ),
    }
    detected_by_shape: dict[str, dict] = {}
    for name, body in controls.items():
        detected = probe._embedding_writers(_scratch_root(tmp_path, name, body))
        detected_by_shape[name] = detected
        assert detected, (
            f"the detector found nothing in a scratch module using the {name!r} shape — either "
            "discovery regressed or its root handling changed; repair the detector, do not "
            "delete this control"
        )
    # The nested-class control must be CAUGHT, not merely seen: `unresolved_write` is in
    # `NEEDS_A_DECLARATION`, so this row pins the half of the class-scope claim the register used to
    # over-state. Asserted here rather than folded into the loop above, because `detected` being
    # truthy would also be satisfied by a row discovery dropped into `gated`.
    nested = detected_by_shape["class_inside_a_def"]
    assert "tortoise.m::outer" in nested and nested["tortoise.m::outer"]["kind"] == "unresolved_write", (
        "a container-key store inside a `def`-nested class body is no longer CAUGHT as "
        f"`unresolved_write` ({nested}) — the register's class-scope caveat now over-claims in the "
        "other direction; repair discovery or restate the caveat, do not drop this row"
    )
    # ⛔ NON-VACUITY + MEMBERSHIP (round-17 review). The loop below is SILENT over a table that lost
    # an entry, so without these rows deleting a shape — or emptying the table — leaves this file
    # green while the register still says "pinned as `INVISIBLE_SHAPES`' …".
    assert INVISIBLE_SHAPES, (
        "`INVISIBLE_SHAPES` is empty, so the loop below proves nothing — every 'the detector does "
        "not see this shape' claim in the register is then unpinned"
    )
    assert set(INVISIBLE_SHAPES) == set(INVISIBLE_SHAPE_NAMES), (
        f"the blind-spot table is {sorted(INVISIBLE_SHAPES)}, not {sorted(INVISIBLE_SHAPE_NAMES)} — "
        "add or remove the shape HERE and in the register's 'What the closure claim does NOT "
        "cover' block together; the register cites two of these by name as pinned"
    )
    # ⛔ The citation the register makes ("pinned as `INVISIBLE_SHAPES`' `module_scope_container_key`
    # and `class_scope_container_key`") is coupled in BOTH directions against `REGISTER_CITED_SHAPES`
    # — see that tuple's comment for the two prose-derivations this replaced, and why each of them
    # false-blocked correct prose. What it does NOT catch, stated rather than implied: a shape named
    # in the prose but absent from the tuple.
    cited = set(REGISTER_CITED_SHAPES)
    assert cited, (
        "`REGISTER_CITED_SHAPES` is empty, so nothing couples the register's citation to the table"
    )
    _missing_pins = sorted(cited - set(INVISIBLE_SHAPES))
    assert not _missing_pins, (
        f"the register cites {_missing_pins} as pinned by `INVISIBLE_SHAPES` and no such entry "
        "exists — the claim would outlive its pin"
    )
    _missing_citation = sorted(n for n in cited if n not in register.__doc__)
    assert not _missing_citation, (
        f"`tortoise/embed_writer_register.py` no longer cites {_missing_citation} — if that citation "
        "was dropped deliberately, drop it from `REGISTER_CITED_SHAPES` too"
    )
    for shape, body in INVISIBLE_SHAPES.items():
        found = probe._embedding_writers(_scratch_root(tmp_path, shape, body))
        assert not found, (
            f"`_EMBED_WRITE` NOW sees the {shape!r} shape ({sorted(found)}) — that is an "
            "improvement, not a failure, but it is a CLAIM CHANGE: update the module docstring's "
            "'What the closure claim does NOT cover' block and this test together, and declare "
            "the writer(s) the widened discovery now finds."
        )


#: The IMPORT-ALIAS shapes, one scratch module each — `#11274`'s breadth defect, DRIVEN.
#: ⛔ Discovery decides a write from the writer's own SOURCE TEXT (`_EMBED_WRITE`) and its CALL
#: NAMES (`_callees`), and an aliased import renames BOTH. Measured at the head that introduced this
#: test: the two writers below produced an EMPTY MAP — not unclassified, ABSENT — so the register's
#: "no undeclared writer can bypass" was false for any writer that imported the clause builder under
#: another name. `(module body, {writer: expected kind}, note)`.
ALIASED_IMPORT_SHAPES: dict[str, tuple[str, dict[str, str], str]] = {
    "module_level_alias": (
        "from tortoise.vector_write import set_embedding_clause as _set\n"
        "\n"
        "def w(proj):\n"
        '    return proj.g.query("MATCH (n:Point) SET n += $p", {"p": _set("n")})\n',
        {"tortoise.m::w": "unresolved_write"},
        "a module-level alias of the clause builder: the write is real and the text pass sees "
        "neither the builder's name nor its clause",
    ),
    "function_level_alias": (
        "def w(proj):\n"
        "    from tortoise.vector_write import clear_then_set_prefix as _clear\n"
        '    return proj.g.query("MATCH (n:Point) " + _clear("n"))\n',
        {"tortoise.m::w": "unresolved_write"},
        "a FUNCTION-level alias: the same reach, bound in the writer's own scope",
    ),
    "aliased_gate_call": (
        "from tortoise.embeddings import encode_for_row as _enc\n"
        "\n"
        "def w(text):\n"
        "    return _enc(text, 384)\n",
        {"tortoise.m::w": "gated"},
        "an aliased GATE call: the gate IS reached, so a declaration is owed by nobody — classing "
        "this as an unresolved producer would make the register declare a writer the rule already "
        "covers (the false-declaration direction)",
    ),
    "plain_control": (
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def w(proj):\n"
        '    return proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")})\n',
        {"tortoise.m::w": "unresolved_write"},
        "the unaliased control: the alias resolution must not change the ordinary spelling",
    ),
    "unrelated_alias_is_not_a_write": (
        "from tortoise.vector_write import set_embedding_clause as _set\n"
        "\n"
        "def w(rows):\n"
        "    return [r for r in rows]\n",
        {},
        "an aliased import that the writer never CALLS is not a write — crediting the import "
        "rather than the call would fill the population with writers that assert nothing",
    ),
    # ⛔ `#12020` round 3 — the three ways an alias is NOT the reach it names. Each was measured
    # to class the writer `gated`, which is OUTSIDE `NEEDS_A_DECLARATION`: the writer stopped owing
    # a declaration while never reaching the gate. That is a false PASS, so every arm below is the
    # `unresolved_write` direction.
    "alias_shadowed_by_a_local_binding": (
        "from tortoise.embeddings import encode_for_row as _enc\n"
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def w(proj, text):\n"
        "    _enc = lambda p: p\n"
        '    return proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")}, _enc(text))\n',
        {"tortoise.m::w": "unresolved_write"},
        "a writer-local rebinding of the alias: `_enc` no longer names the gate seam, so the gate "
        "is NOT reached and a declaration is owed — crediting the import instead of the call hid it",
    ),
    "alias_from_a_foreign_module": (
        "from thirdparty.lib import encode_for_row as _enc\n"
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def w(proj):\n"
        '    return proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")}) or _enc(1)\n',
        {"tortoise.m::w": "unresolved_write"},
        "a DIFFERENT function that merely shares the name: only the real home module makes an "
        "alias the seam, or any library could silence the row by naming a function encode_for_row",
    ),
    # ⛔ `#12020` round 4 — the SCOPE axis of the same fail-open. An import in a scope that is not
    # the writer's is not the writer's reach, and `gated` is outside `NEEDS_A_DECLARATION`.
    "alias_imported_in_a_SIBLING_function": (
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def other():\n"
        "    from tortoise.embeddings import encode_for_row as _enc\n"
        "    return _enc\n"
        "\n"
        "def w(proj):\n"
        '    return proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")}) or _enc(1)\n',
        {"tortoise.m::w": "unresolved_write"},
        "an alias bound in ANOTHER function is not this writer's reach — the module pass walked "
        "every scope, so one sibling's import classed an unrelated writer `gated`",
    ),
    "alias_imported_in_a_nested_def_of_the_writer": (
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def w(proj):\n"
        "    def _inner():\n"
        "        from tortoise.embeddings import encode_for_row as _enc\n"
        "        return _enc\n"
        '    proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")})\n'
        "    return _enc(1)\n",
        {"tortoise.m::w": "unresolved_write"},
        "the alias is bound inside a NESTED def, so a bare call in the writer's own body cannot "
        "reach it — resolving across scopes credited a reach the writer does not have",
    ),
    "alias_rebound_at_MODULE_level": (
        "from tortoise.embeddings import encode_for_row as _enc\n"
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "_enc = lambda text, n: text\n"
        "\n"
        "def w(proj, text):\n"
        '    proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")})\n'
        "    return _enc(text, 384)\n",
        {"tortoise.m::w": "unresolved_write"},
        "a MODULE-level rebinding shadows the import for every writer in the file — the round-3 "
        "shadow check only inspected the writer's subtree and could not see it",
    ),
    "alias_on_a_foreign_receiver": (
        "from tortoise.embeddings import encode_for_row as _enc\n"
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def w(proj, client):\n"
        '    proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")})\n'
        "    return client._enc(1)\n",
        {"tortoise.m::w": "unresolved_write"},
        "an ATTRIBUTE call: only a bare `Name` call can bind an import alias, and `client._enc` is "
        "that object's method — resolving it through the module's import map credited a reach the "
        "writer does not have",
    ),
    # ⛔ `#12020` round 5 — the SCOPE axis again, one level in. `_locally_bound` used `ast.walk`,
    # so a NESTED function's parameter dropped the writer's alias; for a clause-builder alias that
    # deleted a real writer from the population (the `#11274` fail-open, re-created).
    "nested_scope_parameter_does_not_shadow": (
        "from tortoise.vector_write import set_embedding_clause as _set\n"
        "\n"
        "def w(proj):\n"
        "    def helper(_set):\n"
        "        return _set\n"
        '    return proj.g.query("MATCH (y) SET y += $p", {"p": _set("y")})\n',
        {"tortoise.m::w": "unresolved_write"},
        "a nested function's PARAMETER binds only inside that function, so it cannot shadow the "
        "writer's module alias — `ast.walk` collected it anyway and the writer vanished entirely",
    ),
    "attribute_clause_builder_call_is_not_an_alias": (
        "from tortoise.vector_write import set_embedding_clause as _set\n"
        "\n"
        "def w(reader):\n"
        '    return reader._set("n")\n',
        {},
        "`reader._set(…)` is a foreign object's method that shares the alias's spelling: crediting "
        "it reports a writer that owes a declaration and asserts nothing (`_named_callees`' rule, "
        "applied to the write predicate as well)",
    ),
    "alias_rebound_by_a_module_level_DEF": (
        "from tortoise.embeddings import encode_for_row as _enc\n"
        "from tortoise.vector_write import set_embedding_clause\n"
        "\n"
        "def _enc(text):\n"
        "    return text\n"
        "\n"
        "def w(proj, text):\n"
        "    _enc(text)\n"
        '    return proj.g.query("MATCH (n:Point) SET n += $p", {"p": set_embedding_clause("n")})\n',
        {"tortoise.m::w": "unresolved_write"},
        "a module-level `def _enc` rebinds the alias exactly as `_enc = lambda …` does, so the gate "
        "is not reached through it — the own-scope walk skipped nested scope NODES, so this "
        "rebinding was invisible and the writer stayed `gated`",
    ),
}


def test_an_IMPORT_ALIAS_is_the_reach_it_names(tmp_path):
    """`#11274`: discovery must not miss a writer because of how it IMPORTS or ALIASES (`#11398`).

    Driven, not listed: each shape is a module written into `tmp_path` and walked by the real
    `probe._embedding_writers`, so the assertion is about the DISCOVERY FILTER and not about set
    arithmetic over an already-derived population (the distinction `#11274` finding 2 draws).

    The fail-OPEN direction is the one that matters and it was live: the `unresolved_write` shapes
    below were absent from the map entirely, so no register row could ever have been owed for them —
    the "no silent bypass" claim was as wide as a spelling, not as wide as the code. The two
    `gated`/`{}` arms bound the widening in the other direction, because a resolver that credits an
    IMPORT instead of a CALL would manufacture declarations for writers that store nothing.
    """
    for shape, (body, expected, note) in ALIASED_IMPORT_SHAPES.items():
        found = probe._embedding_writers(_scratch_root(tmp_path, shape, body))
        kinds = {name: entry.get("kind") for name, entry in found.items()}
        assert kinds == expected, f"{shape}: {note}\n  measured {kinds}, expected {expected}"
    # …and the discovered writer reaches the REFUSAL, not just the map: a writer discovery now sees
    # and the register does not declare is exactly what "no silent bypass" means here, so it is
    # asserted through the register as well as through `kinds`.
    aliased = set(probe._embedding_writers(
        _scratch_root(tmp_path, "refusal", ALIASED_IMPORT_SHAPES["module_level_alias"][0])))
    assert register.undeclared(aliased) == tuple(sorted(aliased)), (
        "an aliased-import writer discovery now finds is not refused by the register — a "
        "discovered writer that nobody declared must be a refusal, not a default")


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
    wording is pinned here rather than left to the next review round to re-measure by hand: the
    arms below measure the MECHANISM, and `_assert_the_register_states_the_put_criterion` measures
    the SENTENCE, because a criterion pinned only on the instrument side regresses silently when a
    lane edits the register back to the retracted reading (measured: reverting the parenthetical to
    "both of them reached by the walk" left this whole file green). The
    both-writers arm is the control: if `duplicate` stops being reachable the two reader arms stop
    discriminating and this test says so instead of passing vacuously.
    """
    _assert_the_register_states_the_put_criterion()
    for shape, (body, expected) in DUPLICATE_CRITERION.items():
        found = probe._embedding_writers(_scratch_root(tmp_path, shape, body))
        kinds = {name: entry.get("kind") for name, entry in found.items()}
        assert kinds == {"tortoise.m::w": expected}, (
            f"the {shape!r} collision measured {kinds}, not "
            f"{{'tortoise.m::w': {expected!r}}} — discovery's collision rule changed. The "
            "register's `duplicate` parenthetical and this file's header both state this "
            "criterion; re-derive both before changing either."
        )


def _assert_the_register_states_the_put_criterion() -> None:
    """The `duplicate` criterion as the REGISTER states it — pinned in both directions.

    The instrument-side arms above cannot see this: they pass with any wording, so the sentence in
    `register.__doc__` — the thing a `#11086` reader acts on — could be reverted to the retracted
    reading with the whole file green (measured). Reads the parenthetical by its own bracket rather
    than by a paragraph slice, because the same block names `gated` and `producer` for different
    mechanisms.
    """
    doc = register.__doc__ or ""
    anchor = "`duplicate` ("
    start = doc.find(anchor)
    assert start != -1, (
        f"the register no longer states the `duplicate` criterion: {anchor!r} is absent"
    )
    end = doc.find(")", start)
    criterion = " ".join(doc[start:end + 1].split())
    # (i) the MECHANISM has to be in the sentence. `_put` is the function whose call site decides
    # whether a colliding def can displace the discovered one, so a criterion that does not name it
    # (or the `CLASSIFIES` rule that gates it) is describing something else.
    assert "`_put`" in criterion and "CLASSIFIES" in criterion, (
        "the register's `duplicate` parenthetical no longer names the mechanism that decides the "
        f"criterion (a def must reach `_put`, which happens only for a def discovery CLASSIFIES): "
        f"{criterion!r}"
    )
    # (ii) and the RETRACTED reading must not come back. This is the direction the instrument-side
    # arms are blind to: "reachable by the walk" is satisfied by a pure reader, which is exactly the
    # collision that is CAUGHT rather than excused.
    for retracted in ("reached by the walk", "both of them reached"):
        assert retracted not in criterion, (
            f"the register's `duplicate` criterion has been reverted to the retracted reading "
            f"({retracted!r}) — `ast.walk` reaches a pure reader too, so that wording predicts "
            f"`duplicate` for the one collision that is actually declared: {criterion!r}"
        )
