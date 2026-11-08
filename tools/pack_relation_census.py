#!/usr/bin/env python3
"""The per-pack relation census — declared → writable → sense → written (#11083).

WHY THIS EXISTS
---------------
The graph is built by following the packs' DECLARED connection logic. Until this
tool there was no instrument that compared the two contracts: `pack_registry`
said which declared relations bind a write path, and `pack_hierarchy` read the
declared hierarchies, but nothing asked *"does the writer produce the
connections the packs declare?"* across every pack at once, with the population
derived rather than remembered (#2766's reported half; #11083's product).

THE ONE RULE: STATE THE LAYER PER CELL, AND NEVER INVENT A NUMBER
-----------------------------------------------------------------
"Written" has three different meanings and the issue body quoted all three as
one number: **pipeline** (did the extractor emit it on a corpus run?),
**write path** (can a caller name it at all?) and **callable** (is there a
writer symbol?). So every cell carries its layer, and a cell this tool cannot
measure reads `UNMEASURED` with the issue that owns it — never `0`. That is
#10995's rule ("an unmeasured door reads UNMEASURED, never zero") applied to
packs, and it is why the pipeline section cannot silently look like a failure.

`written` is further split into **direct | mediated | none**, because
`create_operator` does not write `(o1)-[:p]->(o2)`: it writes a bridge

    (:Object)-[:INPUT]->(:Point {label:p, op_type:'IMPL'})-[:IMPL]->(:Object)

The relation IS persisted and the two Objects ARE connected — through an
operator Point. A census that asks only "written?" reports a bridge as an edge
and hides that the Object→Object class is a small minority of the declared
population. ⛔ The count is NOT restated here: `written_counts` in the artifact is
where it lives, and a header that quotes its own metric drifts the moment a
declaration moves (this one did — a header still saying `2 of 36` outlived the
population by two revisions).

TWO SURFACES, ONE POPULATION
----------------------------
`relations[]` and `hierarchies[]` are two declarations of the same thing: the
relation spells the verb, the hierarchy names the levels. A relations-only
census under-counts the declared business logic — measured 6 of 42 at #11083's
stamp — and it cannot see `producedBy` alongside the `Program → Asset` `hasPart`
path at all. Both are read here, from `pack_hierarchy.declared_hierarchy()` —
the module that already owns the join — never re-parsed here.

WHAT THIS TOOL DOES NOT DO
--------------------------
* It writes **no** producer and invokes **no** pipeline. The pipeline column is
  `UNMEASURED` with its owner named; the hierarchy-invocation leg has no owner
  at all and is reported as such rather than absorbed.
* It does not report `producedBy` + the `Program → Asset` `hasPart` path as a
  duplicate. The owner ruled (2026-11-05) that production and containment are
  different assertions and both are written.
* `--write-probe` mints endpoint pairs on a THROWAWAY graph and deletes them. It
  is not a corpus measurement, and it does NOT reach a real graph unless you name
  one with `--uri`: `TORTOISE_DB_URI` is deliberately NOT read (that variable is
  this repo's documented graph setting and in the fleet it routinely names the
  SHARED docker container — an implicit read is a write into an unspecified
  target with no confirmation, the residue class #5084 exists to stop).

USAGE
-----
    uv run python tools/pack_relation_census.py --json            # hermetic
    uv run python tools/pack_relation_census.py --write-probe --json
    uv run python tools/pack_relation_census.py --check           # artifact == re-derivation

`--write-probe` needs a graph, and it MINTS ONE unless `--uri` names a real one
(embedded tempfile is the default; `TORTOISE_DB_URI` is ignored on purpose). The
committed artifact at `docs/evidence/11083/pack-relation-census.json`
is produced with it; the hermetic half re-derives without one.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — CPython parses the
# whole file before executing it, so a guard placed after the imports cannot run:
# an older interpreter dies in `argparse`/a module-level 3.11+-only import with no
# attribution, which is the refusal this entry point owes its caller. The
# preamble (`[docstring] [__future__] import sys` then this `if`) is pinned by
# `tests/test_entry_point_python_guard.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/pack_relation_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/pack_relation_census.py`"
    )

import argparse
import json
import os
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # `python tools/x.py` and `import tools.x`
    sys.path.insert(0, str(REPO_ROOT))

from tortoise.pack_registry import (  # noqa: E402
    CANONICAL_OBJECT_KINDS,
    RELATION_DECLARATION_ONLY,
    RELATION_PREDICATE_NOT_IN_ALLOWLIST,
    RELATION_WRITABLE,
    PackRegistry,
    effective_direction,
    relation_write_verdict,
)

ARTIFACT_REL = "docs/evidence/11083/pack-relation-census.json"

#: The commit that owns the pipeline write leg (`result["relations"]` →
#: Object→Object edges). Named, not assumed: #11237's own body disclaims the
#: pack-hierarchy writer, so that second leg genuinely has no owner and is
#: reported as unowned rather than attributed to a closed issue.
PIPELINE_OWNER = "#11237"
HIERARCHY_INVOCATION_OWNER = None  # no open issue owns it — #11083 scoping §2

#: What `written_direction` records when the write path produced a DIRECT edge.
#: A plain `(a)-[:p]->(b)` carries no direction attribute — the predicate IS the
#: sense — so the cell must say so instead of reading `None`, which is what
#: "this row was never measured" reads as. #10995's rule (an unmeasured door reads
#: UNMEASURED, never 0) applied to a column: three states, three readings.
DIRECT_EDGE_DIRECTION = "n/a: direct edge (the predicate carries the sense)"

#: ONTOLOGY §5's commitment-state object kinds — the family a declared relation
#: has to reach for the business spine (company → goal → initiative → work) to
#: exist at all. `goal`, `plan`, `strategy`, `target`, each with a `goalState`.
COMMITMENT_STATE_KINDS = frozenset({"goal", "plan", "strategy", "target"})

#: A declared predicate the structural writer CANNOT stamp is reported against
#: the structural path's own allowlist — not `relation_write_paths`' union of
#: three writers. `pack_hierarchy._binding` had to separate exactly this pair
#: ("`aboutPoint`/`extractedFrom` read as `structural` there and are refused
#: here"), so the census reports both and flags a disagreement instead of
#: inheriting the union's optimism.
def _structural_writer_predicates() -> frozenset[str]:
    from tortoise.projection.edges import _VALID_EDGE_PREDICATES

    return frozenset(_VALID_EDGE_PREDICATES)


def _operator_edge_type(predicate: str, mechanism: str | None) -> str | None:
    """The graph label an OPERATOR write stamps for this row.

    `create_operator` stamps `[:IMPL]` / `[:NAND]` (the mechanism) on the typed
    edge and carries the declared predicate on the operator Point's `label`.
    The historical fold (`decomposesInto`/`composedOf`/`contains`/`wraps` →
    `hasPart`) applies to the operator's `op_type`, not to a relation predicate,
    so reporting the declared spelling as the *edge* label would name a label no
    writer stamps — which is why `written_label` and `written_edge_type` are
    separate cells.
    """
    return mechanism if mechanism in ("IMPL", "NAND") else None


def _relation_row(pack: str, rel: dict) -> dict:
    predicate = rel.get("predicate")
    verdict, paths = relation_write_verdict(rel)
    mechanism = rel.get("mechanism")
    # Structural wins when a declaration binds both: composition is a plain
    # unweighted edge (ONTOLOGY §2) and `hasPart` is in the structural writer's
    # own set — the same recorded decision `pack_hierarchy` implements.
    if "structural" in paths:
        write_path = "structural"
    elif "operator" in paths:
        write_path = "operator"
    else:
        write_path = None
    declared_direction = rel.get("direction")
    row = {
        "surface": "relations",
        "pack": pack,
        "predicate": predicate,
        "from_kind": rel.get("fromKind") or rel.get("from"),
        "to_kind": rel.get("toKind") or rel.get("to"),
        "cardinality": rel.get("cardinality"),
        "mechanism": mechanism,
        "semantics": rel.get("semantics"),
        "verdict": verdict,
        "write_paths": list(paths),
        "write_path": write_path,
        # The DECLARED per-edge sense (may be absent — most rows resolve it from
        # the meaning), and the sense the row ACTUALLY records.
        "declared_direction": declared_direction,
        "effective_direction": effective_direction(rel),
        "declaration_only": bool(rel.get("declarationOnly")),
        "declaration_only_reason": rel.get("declarationOnlyReason"),
    }
    if write_path == "structural":
        row["written_label"] = predicate
        row["written_edge_type"] = predicate
        row["structural_writer_accepts"] = predicate in _structural_writer_predicates()
    elif write_path == "operator":
        row["written_label"] = predicate  # the operator Point's `label`
        row["written_edge_type"] = _operator_edge_type(predicate, mechanism)
        row["structural_writer_accepts"] = None
    else:
        row["written_label"] = None
        row["written_edge_type"] = None
        row["structural_writer_accepts"] = None
    # A cell whose union verdict claims a structural path the structural WRITER
    # refuses is the latent class `pack_hierarchy._binding` had to separate.
    row["path_disagreement"] = bool(
        "structural" in paths
        and write_path == "structural"
        and predicate not in _structural_writer_predicates()
    )
    # Layer statement: every `written` cell starts UNMEASURED and is filled by
    # `--write-probe` or left with the reason it is not.
    row["written"] = "UNMEASURED (write probe not run)"
    row["written_direction"] = None
    return row


def _hierarchy_rows(packs_dir: Path) -> tuple[dict[str, list[dict]], list[dict]]:
    """The `hierarchies[]` surface, via the module that owns the join."""
    from tortoise.pack_hierarchy import declared_hierarchy, hierarchy_diagnostics

    reg = _load_registry(packs_dir)
    rows: dict[str, list[dict]] = {}
    for entry in declared_hierarchy(reg):
        rows.setdefault(entry.pack, []).append({
            "surface": "hierarchies",
            "pack": entry.pack,
            "predicate": entry.predicate,
            "path": entry.path,
            "from_kind": entry.from_kind,
            "to_kind": entry.to_kind,
            "mechanism": entry.mechanism,
            "semantics": None,
            "verdict": RELATION_WRITABLE,
            "write_paths": [entry.write_path],
            "write_path": entry.write_path,
            "written_label": entry.predicate,
            "written_edge_type": entry.predicate,
            "structural_writer_accepts": (
                entry.predicate in _structural_writer_predicates()
                if entry.write_path == "structural" else None
            ),
            "declared_direction": None,
            # A declared hierarchy path carries no per-edge direction, so the sense
            # is the MEANING's — resolved through `effective_direction`, the same
            # resolver the relations column names, NOT through the raw table. The
            # resolver also applies `SEMANTICS_TRANSITION`, so a hierarchy
            # declaring a retired spelling (`type: holds`) reports the `->` the
            # writer records instead of a blank the reader has to interpret.
            # ⛔ Not a literal either: a hard-coded `"<->"` disagreed with the
            # resolver silently (H7b moves the table and requires these rows to
            # move with it).
            "effective_direction": effective_direction({"semantics": entry.predicate}),
            # Whether a `relations[]` row of this pack declares the same pair —
            # read from `pack_hierarchy`, which owns that join, so the distinct
            # denominator below is MEASURED rather than asserted in prose.
            "restates_relation": bool(
                getattr(entry, "restates_relation", False)),
            "declaration_only": False,
            "declaration_only_reason": None,
            "path_disagreement": False,
            "written": "UNMEASURED (write probe not run)",
            "written_direction": None,
        })
    return rows, [dict(d) for d in hierarchy_diagnostics(reg)]


def _load_registry(packs_dir: Path) -> PackRegistry:
    reg = PackRegistry(packs_dir)
    reg.load_all()
    return reg


def _dangling(packs_dir: Path, all_rows: list[dict]) -> list[dict]:
    """Declared kinds that NO declared connection touches.

    This is the shape the missing commitment spine shows up in: `goal` IS a core
    Object kind (§5's commitment-state family) and 0 of the declared relations
    name it as an endpoint in any pack — so the graph can hold a goal and cannot
    connect one to the work that serves it or the person who owns it. Reported,
    never silently zero.
    """
    used: set[str] = set()
    for row in all_rows:
        for side in ("from_kind", "to_kind"):
            value = row.get(side)
            if isinstance(value, str) and value:
                used.add(value.split(":", 1)[-1].lower())
    reg = _load_registry(packs_dir)
    pack_kinds: dict[str, str] = {}
    for ns, pack in reg.packs.items():
        # OBJECT kinds only: a declared relation or hierarchy connects
        # Objects (and Subjects as targets). An Event kind is not a
        # connector endpoint by design, so listing every event kind would be
        # noise that buries the one gap this section exists to name.
        for bucket in ("object_kinds",):
            for kind in getattr(pack, bucket, ()) or ():
                pack_kinds.setdefault(kind.lower(), f"{ns}:{kind}")
    out: list[dict] = []
    # The COMMITMENT-STATE family is the family the ruling names, and the one
    # whose absence is the finding: `goal` is a core Object kind, the other core
    # kinds are reachable as `subclassOf` parents, and an Event/Subject kind is
    # not a declared-relation endpoint by design. Reporting the whole canonical
    # set would bury the finding in 30 rows of `tag`/`tool`/`user`.
    for kind in sorted(CANONICAL_OBJECT_KINDS & COMMITMENT_STATE_KINDS):
        if kind.lower() in used:
            continue
        out.append({
            "kind": kind,
            "namespace": "core",
            "owner": None,
            "note": (
                "is a core Object kind (ONTOLOGY §5's commitment-state family) "
                "and NO declared relation or hierarchy names it as an endpoint, "
                "in any pack — the graph can hold one but cannot connect it to "
                "the work that serves it or the person who owns it"
            ),
        })
    for kind, full in sorted(pack_kinds.items()):
        if kind in used:
            continue
        out.append({
            # ⛔ The DECLARED spelling, not the lowercased join key. `pack_kinds`
            # is keyed on `kind.lower()` for the endpoint comparison (a declared
            # `fromKind` may spell a kind in any case), but emitting that key
            # named kinds no manifest declares — the artifact said
            # `marketing:competitorcontent` / `pm:kanbanboard` where the packs say
            # `competitorContent` / `kanbanBoard`, and at the time no test read this
            # cell (H2c does now).
            # `full` is `"<ns>:<Kind>"` as declared, one line above.
            "kind": full.split(":", 1)[1],
            "namespace": full.split(":", 1)[0],
            "owner": None,
            "note": (
                "is declared by its pack and no declared relation or hierarchy "
                "names it as an endpoint"
            ),
        })
    return out


def build_census(packs_dir: Path | str = REPO_ROOT / "packs", *,
                 write_probe: bool = False,
                 probe_uri: str | None = None) -> dict:
    packs_dir = Path(packs_dir)
    reg = _load_registry(packs_dir)
    relations_by_pack: dict[str, list[dict]] = {}
    all_rows: list[dict] = []
    for ns in sorted(reg.packs):
        pack = reg.packs[ns]
        rows = [_relation_row(ns, rel) for rel in pack.relations]
        relations_by_pack[ns] = rows
        all_rows.extend(rows)
    hier_by_pack, hier_diagnostics = _hierarchy_rows(packs_dir)
    all_rows.extend(r for rows in hier_by_pack.values() for r in rows)
    # ROWS are not CONNECTIONS. `pack_hierarchy` calls a `hierarchies[]` entry and
    # a `relations[]` row over the same pack + pair + meaning "one declaration
    # spelled twice", so `declared` counts those twice. Derived from each row's
    # own `restates_relation` — the join `pack_hierarchy._matching_row` owns — and
    # never remembered: the number moves the moment a pack declares or drops one.
    restated = sum(
        1 for rows in hier_by_pack.values() for r in rows if r["restates_relation"])

    pack_sections: dict[str, dict] = {}
    for ns in sorted(reg.packs):
        rel_rows = relations_by_pack.get(ns, [])
        h_rows = hier_by_pack.get(ns, [])
        pack_sections[ns] = {
            "relations": rel_rows,
            "hierarchies": h_rows,
            # Derived, never a remembered 42: a disposition change moves it.
            "declared": len(rel_rows) + len(h_rows),
        }

    verdicts = Counter(r["verdict"] for r in all_rows if r["surface"] == "relations")
    written = Counter()
    probe: dict | None = None
    if write_probe:
        probe = _run_write_probe(all_rows, packs_dir, probe_uri)

    for row in all_rows:
        written[row.get("written") or "none"] += 1

    relations_total = sum(len(v) for v in relations_by_pack.values())
    hierarchies_total = sum(len(v) for v in hier_by_pack.values())
    return {
        "issue": 11083,
        "epic": 5087,
        "census": "pack-relation",
        "generated_by": (
            "uv run python tools/pack_relation_census.py --write-probe --json"
            if write_probe else
            "uv run python tools/pack_relation_census.py --json"
        ),
        "layers": {
            "declared": "the pack's own declaration (relations[] + hierarchies[])",
            "writable": "pack_registry.relation_write_verdict — the ONE resolver (#2766)",
            "sense": "pack_registry.effective_direction — per-row, declared direction first",
            "written": (
                "write probe: one node per declared KIND (cached and reused; a "
                "row whose from_kind and to_kind are the same kind mints its "
                "destination its OWN node, so a same-kind row is measured "
                "between two nodes), the row's own write path invoked, the graph "
                "read back (direct | mediated | none). The store is a throwaway "
                "embedded one unless `--uri` names a graph — `TORTOISE_DB_URI` is "
                "deliberately NOT read — and EVERY minted id is DELETED before "
                "the probe returns, with a failed delete reported in "
                "`probe.leftovers` rather than swallowed; the measured counts "
                "are in `probe`"
                if write_probe else
                "UNMEASURED — run with --write-probe"
            ),
            "pipeline": "UNMEASURED — no producer emits declared relations yet",
        },
        "population": {
            "packs": len(reg.packs),
            "relations": relations_total,
            "hierarchies": hierarchies_total,
            "declared": relations_total + hierarchies_total,
            # The DENOMINATOR the prose is allowed to quote. `declared` is the
            # number of ROWS; a hierarchy that restates a `relations[]` row is
            # the same connection declared twice, so it is named here rather than
            # subtracted in a paragraph a reader cannot check.
            "restated_hierarchy_rows": restated,
            "distinct_connections": relations_total + hierarchies_total - restated,
            # ⛔ The join's SCOPE, stated where a reader of the artifact meets it.
            # `_matching_row` is the authority; its match is ORDERED, and that is
            # load-bearing rather than incidental: venture's hierarchies[]
            # `Investment → Program` (hasPart) states the same FACT as its
            # relations[] `partOf` (program → investment) in the INVERSE
            # direction, so the ordered join does not count it and
            # `distinct_connections` is 38, not 37. Counting inverse-named ranks
            # needs a predicate-inversion table — a second parser this artifact
            # deliberately does not have — so the rule is stated instead of the
            # number being widened. (This cell is compared by `--check`; the
            # README named it while it did not exist, which is the defect class
            # `layers.written` produced twice.)
            "note": (
                # ⛔ The RULE only. An earlier wording added "a direction-agnostic
                # join would read one lower" — a DERIVED metric this function never
                # computes, so nothing could notice it going stale: with a second
                # inverse-spelling hierarchy in `packs/`, `distinct_connections`
                # rose by one while the agnostic join fell by two, and `--check`
                # stayed green. A note may state the rule; only a computed cell may
                # state a number (the same rule this module's docstring gives for
                # every other quoted count).
                "the join is ORDERED (pack_hierarchy._matching_row: the same "
                "(fromKind, toKind) in that order, plus the same predicate or the "
                "same semantics). An inverse-spelling row — the same pair declared "
                "the other way round — is NOT counted by that join; counting it "
                "would need a predicate-inversion table, a second parser this "
                "artifact deliberately does not have"
            ),
        },
        # `None` on a hermetic re-derivation (no graph was touched); the measured
        # cleanup contract when `--write-probe` ran. Never compared by `--check`:
        # it is a GRAPH fact, like the `written` cells.
        "probe": probe,
        "verdicts": {
            "writable": verdicts.get(RELATION_WRITABLE, 0),
            "declaration-only": verdicts.get(RELATION_DECLARATION_ONLY, 0),
            "predicate-not-in-allowlist": verdicts.get(
                RELATION_PREDICATE_NOT_IN_ALLOWLIST, 0),
        },
        "written_counts": dict(written),
        "load_errors": {k: list(v) for k, v in reg.errors.items()},
        "unresolved_refs": len(getattr(reg, "unresolved_refs", ()) or ()),
        "packs": pack_sections,
        "dangling": _dangling(packs_dir, all_rows),
        "duplicate_check": {
            "pairs_that_must_NOT_be_flagged": [
                {
                    "pack": "venture",
                    "relation": "producedBy",
                    "hierarchy": "Program → Asset",
                    "why": (
                        "owner ruling 2026-11-05: production and containment are "
                        "DIFFERENT assertions and both are written — reporting "
                        "this pair as a duplicate is the error, not detecting it"
                    ),
                },
            ],
            "flagged_duplicates": [],
        },
        "hierarchy_diagnostics": hier_diagnostics,
        "pipeline": {
            "status": "UNMEASURED",
            "relations_channel": {
                "status": "UNMEASURED",
                "owner": PIPELINE_OWNER,
                "why": (
                    "`result[\"relations\"]` (#10999's channel) has no consumer in "
                    "tortoise/: the code says so itself in `extractor_v2.py`'s S5 "
                    "Job 1 block — \"nothing in this tree consumes "
                    "`result[\"relations\"]`\" — and that block is the ONLY "
                    "support for this UNMEASURED cell, so it is cited by its "
                    "SENTENCE. ⛔ Not by a line number: this string carried "
                    "`:10815`, which is inside the MITIGATES-strength warning "
                    "block, not the S5 Job 1 block — a line cite re-stales on "
                    "every edit above it and `--write-probe` would reproduce the "
                    "stale pointer forever. An unmeasured column reads "
                    "UNMEASURED, never 0 (#10995)."
                ),
            },
            "hierarchy_invocation": {
                "status": "UNMEASURED",
                "owner": HIERARCHY_INVOCATION_OWNER,
                "why": (
                    "`apply_hierarchy_edges` has zero production callers. #11001 "
                    "shipped the read path and closed; #11237 disclaims the "
                    "hierarchy writer in its own body. NO OPEN ISSUE OWNS THIS — "
                    "reported here so it cannot read as measured-and-zero."
                ),
            },
        },
        "capability_vs_pipeline": {
            "note": (
                "`0 written on the Object→Object class` is a PIPELINE reading. "
                "`dev:hasPart` and `dev:dependsOn` land a real direct "
                "(:Object)-[:<predicate>]->(:Object) when a caller names them — "
                "the structural writer stamps the ROW's own predicate, so "
                "`create_edge(\"dependsOn\", a, b)` stores `:dependsOn`, not "
                "`:hasPart` — so the "
                "honest baseline is 0 pipeline-written / caller-writable, and the "
                "gap #11001 was expected to fill is an INVOCATION, not a missing "
                "primitive."
            ),
        },
    }


# ── the write probe ──────────────────────────────────────────────────────────
def _sdk(uri: str | None = None):
    """A THROWAWAY SDK: an embedded tempfile store unless `uri` names a graph.

    ⛔ `TORTOISE_DB_URI` is deliberately NOT read. It is this repo's documented
    graph setting (`AGENTS.md` → Environment) and in the fleet it routinely names
    the SHARED docker container (#5084's residue class), so an implicit read made
    `--write-probe` write into an unspecified target with no confirmation — while
    two docstrings claimed the opposite. Reaching a real graph now takes an
    explicit `--uri`; the default mints its own store and removes it on close.

    An explicit `--uri` is bound through `TORTOISE_DB_URI` for the constructor
    call, because that is the only door `TortoiseSDK` opens for a URI — its first
    positional parameter is the embedded store's FILE path, not a URI. The
    previous value is restored immediately: the SDK captures the URI at
    construction, so this function leaves the process environment as it found it.
    """
    from tortoise.sdk import TortoiseSDK

    if uri:
        # ⛔ NOT `TortoiseSDK(uri)`: its first positional parameter is `db_path`,
        # the EMBEDDED store's FILE path, so a URI handed there never reaches
        # `FalkorProjection.from_uri` — it becomes a `_db_path` of
        # "docker://…" and the first graph touch refuses a *relative path*, a
        # message about the one thing that was not wrong. The SDK opens a URI
        # only through `TORTOISE_DB_URI` (read by `__init__` while `db_path is
        # None`), so the URI is bound THERE and the previous value restored: the
        # SDK captures it at construction, and a probe that leaks an env
        # mutation into the rest of the process is the same class of implicit
        # target this file refuses. Same fix, same reason, as
        # `tools/purge_test_residue.py` (#4028 review P1).
        previous = os.environ.get("TORTOISE_DB_URI")
        os.environ["TORTOISE_DB_URI"] = uri
        try:
            return TortoiseSDK()
        finally:
            if previous is None:
                os.environ.pop("TORTOISE_DB_URI", None)
            else:
                os.environ["TORTOISE_DB_URI"] = previous
    import atexit
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="pack-census-probe-")
    # The store itself is throwaway, so its DIRECTORY is too: a probe that leaves
    # both a graph and a directory behind is not "throwaway" in either sense.
    atexit.register(shutil.rmtree, tmp, True)
    return TortoiseSDK(str(Path(tmp) / "probe.db"))


def _mint_endpoint(sdk, kind_ref: str | None, cache: dict,
                   minted: list[str] | None = None,
                   *, slot: str = "") -> str:
    """One endpoint per declared KIND, minted once and reused — but never the same
    NODE on both sides of one row.

    ⛔ Not one per row. `create_entity` runs the nudge/embedding path (measured
    0.957 s/call on an embedded store; a write is 0.015 s), so minting a fresh
    pair per row cost 84 calls / ≈80 s and put the graph gate over its budget for
    no extra information. Rows that share a kind pair share the node — their
    read-back stays distinguishable because it is keyed on the PREDICATE, and two
    rows with the same predicate over the same kinds are the same question.

    ⛔ EXCEPT when a row's `from_kind` and `to_kind` are the same kind: a per-kind
    cache then hands the SAME id to both sides, so `pm:dependsOn`
    (`pm:card → pm:card`) measured a `(card)-->(card)` SELF-LOOP. That is a real
    edge and it is NOT the declared connection — the declaration is between two
    cards — and worse, it made the `written` cell a false-positive pathway: the
    read-back matches `(a {id:$s})-[r]->(b {id:$d})` with `s == d`, so a writer
    that refused a genuine two-card edge would still read `direct`. The caller
    therefore passes a non-empty `slot` for the second side of such a row, which
    mints that side its own node (keyed `kind#slot`).
    """
    label = (kind_ref or "Object").split(":", 1)[-1]
    # ⛔ Keyed on the NAMESPACED kind, not the bare label. `dev:issue` and
    # `pm:issue` are different declared kinds that merely share a label, and a
    # label-keyed cache handed both the same node — so a row declaring
    # `dev:issue → pm:issue` read back with `s == d`, i.e. the EXACT self-loop
    # false-positive the `slot` above closes for `pm:card → pm:card`, one
    # namespace over: a writer that refused a genuine two-issue edge would still
    # read `direct`. Latent in the shipped catalog (no row is cross-namespace
    # same-label today), which is why it is a correctness fix and not a finding
    # about a current cell.
    kind = (kind_ref or "Object").strip()
    key = f"{kind.lower()}#{slot}" if slot else kind.lower()
    if key in cache:
        return cache[key]
    slug = kind.lower().replace(":", "-")
    name = f"census-probe-{slug}" + (f"-{slot}" if slot else "")
    if label.lower() == "subject":
        node_id = sdk.create_entity("subject", name)["node"]["id"]
    else:
        node_id = sdk.create_entity(
            "object", name, objectKind=label)["node"]["id"]
    cache[key] = node_id
    if minted is not None:
        minted.append(node_id)
    return node_id


def _read_back(sdk, src: str, dst: str, predicate: str) -> tuple[str, str | None]:
    """`(direct|mediated|none, direction read back)` for one written row."""
    g = sdk._get_proj().g
    direct = g.query(
        "MATCH (a {id:$s})-[r]->(b {id:$d}) WHERE type(r) = $p "
        "RETURN count(r)",
        params={"s": src, "d": dst, "p": predicate},
    ).result_set
    if direct and direct[0][0]:
        # ⛔ NOT `None`. A plain `(a)-[:p]->(b)` carries no direction attribute —
        # its sense IS the predicate — but `None` is also what "this row was
        # never measured" reads as, and the whole point of a three-valued cell
        # (#10995) is that a reader can tell the two apart.
        return "direct", DIRECT_EDGE_DIRECTION
    bridged = g.query(
        "MATCH (a {id:$s})-[:INPUT]->(o:Point)-[r]->(b {id:$d}) "
        "WHERE o.label = $p AND type(r) IN ['IMPL','NAND'] "
        "RETURN o.direction",
        params={"s": src, "d": dst, "p": predicate},
    ).result_set
    if bridged:
        return "mediated", bridged[0][0]
    return "none", None


def _delete_minted(sdk, minted: list[str]) -> list[str]:
    """Delete what the probe minted. Returns the ids it could NOT remove.

    ⛔ This exists because two docstrings claimed it without it existing: there
    was no `delete` anywhere in this file while `--write-probe` wrote ~N entities
    and ~N operator Points into whatever graph it was pointed at. A claim about
    cleanup is only true if the cleanup is here, and a FAILED cleanup must be
    loud — a silent leftover is the residue class #5084 exists to stop.

    `minted` is an INSERTION-ORDERED `list`, not a set — that is what makes the
    next sentence about MINT order rather than hash order. ⚠️ The order is NOT
    load-bearing today: `delete_entity` issues `DETACH DELETE`, which removes a
    node and every relationship attached to it in any direction (Cypher's own
    semantics), so deleting an operator first or last detaches the same edges.
    The list stays ordered because mint order is the only thing that could ever
    make the order meaningful, and a comment is not where that gets decided.
    """
    leftovers: list[str] = []
    for node_id in reversed(list(minted)):
        try:
            if not sdk.delete_entity(node_id):
                leftovers.append(node_id)
        except Exception:  # a cleanup failure is REPORTED below, never a crash
            leftovers.append(node_id)
    return leftovers


def _run_write_probe(rows: list[dict], packs_dir: Path,
                     uri: str | None = None) -> dict:
    """Invoke each row's OWN write path and record what the graph says.

    ⛔ `direction=` is not optional. `create_operator`'s signature default is
    `bidirectional` and the semantics chain only fires while the direction IS
    that default — so a probe that omitted it would write `->` for
    `venture:holds` while the census reported `<-`, i.e. a cell measuring
    nothing. The read-back direction is recorded next to the cell so the
    graph test can assert they agree.

    ⛔ `uri` is explicit. `TORTOISE_DB_URI` is not consulted (see `_sdk`), and
    every node this function mints is deleted before it returns — the store is
    throwaway, and so is everything put in it.

    Returns the MEASURED probe contract for the artifact's `probe` section. The
    cleanup half belongs there rather than in a docstring, because it is the one
    property an operator pointing `--uri` at a real graph needs to verify and the
    one that was previously asserted in prose while no delete existed at all.
    """
    sdk = _sdk(uri)
    cache: dict[str, str] = {}
    minted: list[str] = []
    try:
        for row in rows:
            if row["write_path"] is None:
                row["written"] = "none"
                continue
            src = _mint_endpoint(sdk, row.get("from_kind"), cache, minted)
            # A same-kind row (`pm:card → pm:card`) must NOT resolve both sides
            # to one node: that measured a self-loop, not the declared
            # connection, and it let a refused two-entity write still read
            # `direct`. Same kind ⇒ the destination gets its own node.
            same_kind = (row.get("from_kind") or "Object") == \
                (row.get("to_kind") or "Object")
            dst = _mint_endpoint(sdk, row.get("to_kind"), cache, minted,
                                 slot="dst" if same_kind else "")
            try:
                if row["write_path"] == "structural":
                    sdk.create_edge(row["predicate"], src, dst)
                else:
                    written_op = sdk.create_operator(
                        row.get("mechanism") or "IMPL",
                        src,
                        [dst],
                        label=row["predicate"],
                        semantics=row.get("semantics") or row["predicate"],
                        direction=row.get("effective_direction"),
                    )
                    op_id = (written_op or {}).get("id")
                    if op_id:
                        minted.append(op_id)
            except Exception as exc:  # a refusal is a RESULT, not a crash
                row["written"] = "none"
                row["written_error"] = f"{type(exc).__name__}: {exc}"[:300]
                continue
            written, direction = _read_back(sdk, src, dst, row["predicate"])
            # A structural row resolves to `mediated` only when the operator
            # bridge is what carried it; the structural edge is matched by its
            # own predicate above, so a `none` here means the writer refused.
            row["written"] = written
            row["written_direction"] = direction
    finally:
        leftovers = _delete_minted(sdk, minted)
        sdk.close()
    if leftovers:
        print(
            f"WARN: the probe could not delete {len(leftovers)} node(s) it minted "
            f"— they carry the `census-probe-` prefix and are safe to remove: "
            f"{sorted(leftovers)}",
            file=sys.stderr,
        )
    return {
        "store": (
            "the graph `--uri` named" if uri else
            "a throwaway embedded store (`TORTOISE_DB_URI` is deliberately NOT read)"),
        "endpoints": (
            "one node per declared KIND, cached and reused; a row whose from_kind "
            "and to_kind are the same kind mints its destination its own node"
        ),
        "minted": len(minted),
        "deleted": len(minted) - len(leftovers),
        # ⛔ The cell an operator checks before trusting a `--uri` run: empty
        # means the cleanup happened. A non-empty list is a REPORTED residue, not
        # a swallowed one.
        "leftovers": leftovers,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packs-dir", default=str(REPO_ROOT / "packs"))
    ap.add_argument("--json", action="store_true",
                    help="print the census as JSON (default: a short table)")
    ap.add_argument("--write-probe", action="store_true",
                    help="invoke each row's write path on a throwaway graph")
    ap.add_argument("--uri", default=None,
                    help="a real graph URI for --write-probe (default: mint a "
                         "throwaway embedded store). `TORTOISE_DB_URI` is "
                         "deliberately NOT read — a probe that writes to an "
                         "implicit target is the #5084 residue class")
    ap.add_argument("--out", default=None,
                    help=f"write the JSON here (the committed artifact is {ARTIFACT_REL})")
    ap.add_argument("--check", action="store_true",
                    help="compare the committed artifact to a live re-derivation")
    args = ap.parse_args(argv)

    census = build_census(args.packs_dir, write_probe=args.write_probe,
                          probe_uri=args.uri)

    if args.check:
        artifact = REPO_ROOT / ARTIFACT_REL
        if not artifact.exists():
            print(f"MISSING: {ARTIFACT_REL} is not committed", file=sys.stderr)
            return 2
        committed = json.loads(artifact.read_text())
        diffs = _diff_hermetic(committed, census)
        if diffs:
            print(f"DRIFT: {len(diffs)} cell(s) differ from a live re-derivation",
                  file=sys.stderr)
            for d in diffs:
                print(f"  {d}", file=sys.stderr)
            return 1
        print(f"OK: {ARTIFACT_REL} equals a live re-derivation")
        return 0

    payload = json.dumps(census, indent=2, sort_keys=False)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    if args.json or args.out:
        if args.json:
            print(payload)
        return 0

    pop = census["population"]
    print(f"{pop['packs']} packs · {pop['declared']} declared "
          f"({pop['relations']} relations + {pop['hierarchies']} hierarchies) · "
          f"{pop['distinct_connections']} distinct connections "
          f"({pop['restated_hierarchy_rows']} hierarchy row(s) restate a relation)")
    print(f"verdicts: {census['verdicts']}")
    print(f"written : {census['written_counts']}")
    for ns, section in census["packs"].items():
        counts = Counter(
            r["verdict"] for r in section["relations"] + section["hierarchies"])
        print(f"  {ns:20} declared={section['declared']:>3}  {dict(counts)}")
    if census["dangling"]:
        print("dangling kinds (declared, connected to nothing):")
        for d in census["dangling"]:
            print(f"  {d['namespace']}:{d['kind']}")
    return 0


def _diff_hermetic(committed: dict, fresh: dict) -> list[str]:
    """Compare only the cells a HERMETIC re-derivation can answer.

    `written` is excluded by design: it is a graph fact, so comparing it here
    would make the artifact un-checkable without a database — and a check that
    needs a database is run rarely, which is how an artifact rots. The graph
    gate (`tests/test_pack_logic_writeprobe_11083.py`) compares `written`.
    """
    diffs: list[str] = []
    for key in ("population", "verdicts", "dangling", "duplicate_check",
                "load_errors", "unresolved_refs"):
        if committed.get(key) != fresh.get(key):
            diffs.append(f"{key}: committed={committed.get(key)!r} "
                         f"fresh={fresh.get(key)!r}")
    c_packs, f_packs = committed.get("packs") or {}, fresh.get("packs") or {}
    if set(c_packs) != set(f_packs):
        diffs.append(f"packs: committed={sorted(c_packs)} fresh={sorted(f_packs)}")
        return diffs
    skip = {"written", "written_direction", "written_error"}
    for ns in sorted(f_packs):
        for surface in ("relations", "hierarchies"):
            c_rows = c_packs[ns].get(surface) or []
            f_rows = f_packs[ns].get(surface) or []
            if len(c_rows) != len(f_rows):
                diffs.append(
                    f"{ns}.{surface}: committed={len(c_rows)} fresh={len(f_rows)}")
                continue
            for i, (c_row, f_row) in enumerate(zip(c_rows, f_rows, strict=True)):
                c_cells = {k: v for k, v in c_row.items() if k not in skip}
                f_cells = {k: v for k, v in f_row.items() if k not in skip}
                for cell in sorted(set(c_cells) | set(f_cells)):
                    if c_cells.get(cell) != f_cells.get(cell):
                        diffs.append(
                            f"{ns}.{surface}[{i}].{cell}: "
                            f"committed={c_cells.get(cell)!r} "
                            f"fresh={f_cells.get(cell)!r}")
    return diffs


if __name__ == "__main__":
    raise SystemExit(_main())
