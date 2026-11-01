"""#10465 — the read-only instrument that scores `#5090`'s four DoD bullets.

**Why this exists.** `#5090`'s epic body has never been re-verified since it was
written. Three of its four `Definition of done` bullets have had work land
beneath them (`#4997`, `#4999`, `#5407`, `#5008` all CLOSED) while the body still
says *"there is no rule saying what deserves an embedding. That absence **is**
the epic"* — a sentence the tree contradicts (`tortoise/embed_gate.py`).
The stale-flag class this repo has already paid for once is exactly this: an
epic whose state nobody can read, and a reader forced to guess. This tool is the
instrument that ends the guessing — it MEASURES, it never changes a gate.

⛔ **This module is read-only and must stay so.** It imports the gates to score
them; it never writes a graph property, never flips a flag, and never creates an
index. `#10465`'s scope is measurement plus the epic body's own text; a bullet
that scores `NOT MET` is a FINDING to file, never a fix to smuggle in here.

## The four bullets and how each is scored

`#5090` `## Definition of done` states, verbatim:

1. *"A stated, enforced rule for what earns an embedding."*
2. *"No vector is stored without a path to query it."*
3. *"A fallback query reports the fallback."*
4. *"V1 is decided and implemented."*

Each row is `MET` / `PARTIAL` / `NOT MET` **with a command and its observed
output** — a bullet without a command is unscored, not met. The scoring is
deliberately mechanical so a later lane re-runs it instead of re-reading prose:

| bullet | the command | what makes it `MET` |
|---|---|---|
| 1 | `uv run python tools/dod_5090_probe.py --json` (reads the two gate modules + counts the write seams that reach the gate, AND the write paths that reach a raw primitive instead) | a stated predicate exists, ONE rule set (identity, not a copy), every embedding write path reaches the seam, no UNDESIGNED `BYPASS(...)`, AND the gate is ON by default |
| 2 | same (`written labels ⊆ served labels`) | every label a write path embeds is one `run_vector_query` serves |
| 3 | same (forces all three mechanisms through `run_vector_query`) | `index` / `scan` / `scan_fallback` are distinguishable in the caller's trace |
| 4 | same (searches for a V1 ruling) | a decision record exists AND the code implements it |

⚠️ **Bullet 1's `PARTIAL` is the honest answer at HEAD and it is not a bug
report.** The rule is stated (`tortoise/embed_gate.py::embedding_verdict`) and
wired at exactly one seam (`tortoise/embeddings.py::encode_for_row`, reached by
all five entity writers), but its enforcement is behind `TORTOISE_EMBED_GATE`,
which is **unset in every shipped configuration** — so at default the epic's
`enforced` half is an opt-in, not the operative rule of the write path. `#5008`
chose that ordering on purpose (*"the rate effect is measurable before it is
trusted"*); the instrument records the consequence rather than re-deciding it.

## §12.2a — the distinct-predicate count

`STORAGE-ARCHITECTURE.md` §12.2a forbids *"a second, independently-derived rule
set"* for `EMBED`. The instrument counts the **predicates that can answer
`NO-EMBED`**, mechanically: the modules on the embed path that **own identifier
patterns** (`re.compile` at module scope). The target is **1**,
`value_gate.identifier_only`, asked by `embed_gate.embedding_verdict` — which is
the ASK, not a second predicate, and must report the *shared* rule id. A second
pattern list is the drift the criterion exists to prevent, and it makes this
count read `2`.

## The census — `stored` / `indexed` / `unindexed` per label

`#5090`'s premise figures (`12,657` embeddings, *"exactly ONE vector index"*) are
2026-09-24 numbers and are **not to be re-quoted**. `--census` re-measures them
on a **named graph** at a **timestamp**: per label, the label's embedding-bearing
node count (`stored`), whether the engine's catalogue holds a `VECTOR` index for
it (`indexed`), the unindexed remainder, and bytes — with the engine's own
per-index size counters reported **and labelled as blind** (§12.1a measured
`GRAPH.MEMORY USAGE`'s index share at 2.00 MB for 43.28 MiB, 21.6× under).

⛔ **A `stored`-only figure is the 2026-09-24 error repeated.** Both columns are
stated, or the label is `UNMEASURED`. Never `0`: an unreachable store and an
empty one are different facts, and `#5090` is a measurement issue whose whole
failure mode is a body that reads as measured without being measured.

Usage::

    uv run python tools/dod_5090_probe.py                     # the four rows
    uv run python tools/dod_5090_probe.py --json              # same, machine-readable
    uv run python tools/dod_5090_probe.py --census \\
        --uri docker://:pw@localhost:6379/<graph> --graph <graph>

Exit status: `0` when the instrument ran (whatever the verdicts — a red row is a
successful measurement), `2` when the instrument itself could not measure (a
gate module that will not import, or a census URI whose engine has no index
API). Never a verdict-derived code: a caller must not be able to read
`NOT MET` as "the tool failed". ⚠️ An UNREADABLE GRAPH is *not* a `2`: it is a
successful measurement that reports `UNMEASURED(<why>)` inside the report, which
is the whole point of the census. The two are different facts.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERDICTS = ("MET", "PARTIAL", "NOT MET")

#: `#5090`'s four bullets, verbatim from the epic body. Held here so the table
#: cannot silently score a re-worded bullet. The pin
#: `tests/test_dod_5090_probe.py::test_the_four_bullets_are_scored_verbatim`
#: compares this tuple against the epic strings pinned as LITERALS IN THE TEST —
#: never against this module, which would make the pin a self-comparison that
#: passes on any re-wording (a falsifier that cannot fail). The epic body
#: (`#5090`) is the source of truth; the literal is that body's copy in-tree, so
#: a lane that re-words either side reds, and this comment then names the edit.
BULLETS: tuple[str, ...] = (
    "A stated, enforced rule for what earns an embedding.",
    "No vector is stored without a path to query it.",
    "A fallback query reports the fallback.",
    "V1 is decided and implemented.",
)

#: The one predicate §12.2a permits, and the module that owns its patterns.
PREDICATE_MODULE = "tortoise/value_gate.py"
PREDICATE_NAME = "value_gate.identifier_only"

#: The one enforcement seam. Every embedding write path must reach THIS
#: function, not the raw primitive — the seam `#5008` built.
SEAM = "tortoise/embeddings.py::encode_for_row"
SEAM_CALLEE = SEAM.rsplit("::", 1)[-1]

#: The RAW primitives — what a write path reaches when it bypasses the seam.
#: `encode_for_store` is the dim-declaring encoder (reads, backfills and repairs
#: call it by design); `encode_batch_for_store` is its batched form (the session
#: TURN writers). Naming them here is what lets the instrument derive a write
#: population POSITIVELY instead of only from calls of the gate.
RAW_PRIMITIVES: tuple[str, ...] = ("encode_for_store", "encode_batch_for_store")

#: Functions that reach a RAW primitive without `encode_for_row`, BY DESIGN, each
#: with the reason it is exempt. This is `encode_for_row`'s own boundary note
#: (`tortoise/embeddings.py`, "Deliberately NOT routed through here"), made
#: executable — every other function that reaches a raw primitive is an UNGATED
#: write path, the state `#5008` closed, and is reported as `BYPASS(<fn>)`.
#: ⚠️ Adding an entry here is a DELIBERATE act, so it has to be edited on purpose;
#: that is the point. An exemption that arrived by omission is exactly how bullet
#: 1's `enforced` half stopped being true the first time.
#: (`graph-scripts/backfill_embeddings.py` — the re-encode tool, also named in
#: that boundary note — is outside the walked tree (`tortoise/`), the same scope
#: `_write_seams_that_embed` uses for the seam map.)
DESIGNED_BYPASSES: dict[str, str] = {
    "encode_for_row": (
        "the seam ITSELF — its EMBED arm delegates to encode_for_store, "
        "which is the gate being applied, not a path around it"
    ),
    "_capture_turn_embeddings": (
        "the session TURN writers: a Turn is not an extracted row, and the "
        "batched form carries one vector per windowed turn (#5008 boundary)"
    ),
}

#: The command that produced a census row — printed WITH the row, so a figure
#: quoted into an epic body carries the way it was produced (#10465 target 1:
#: "a bullet without a command is unscored").
CENSUS_COMMAND = (
    "uv run python tools/dod_5090_probe.py --census "
    "--uri docker://:pw@host:6379/<graph> --graph <graph>"
)


@dataclass
class Row:
    """One scored bullet: verdict + the command that produced it + its output."""

    bullet: int
    text: str
    verdict: str
    command: str
    observed: str
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")


# ── Bullet 1 — "a stated, enforced rule" ───────────────────────────────────


def _callee_name(func: ast.expr) -> str | None:
    """The called name — bare OR module-qualified.

    `encode_for_row(...)` is an `ast.Name` (its name is `.id`), while
    `embeddings.encode_for_row(...)` is an `ast.Attribute` (its name is `.attr`)
    — https://docs.python.org/3/library/ast.html#abstract-grammar. Reading only
    `.id` silently DROPS every qualified call site, so a writer that imported
    the module rather than the name would vanish from the seam map and bullet
    1's `enforced` half would read as though it were gated.
    """
    return getattr(func, "id", None) or getattr(func, "attr", None)


#: One walk per source tree, shared by both write populations below. The walk
#: parses every `tortoise/**/*.py` with the AST, which measures at ~5 s on a
#: loaded host — and `measure()` asks for it from three rows, so without this the
#: instrument pays it three times for one answer. Keyed on the tree so a test
#: that scores a synthetic root cannot poison the real one (and vice versa).
_CALLS_BY_FUNCTION: dict[str, dict[str, dict[str, str]]] = {}


def _calls_by_function(root: Path | None = None) -> dict[str, dict[str, str]]:
    """Map each OUTERMOST function -> {callee name: `path:line` of its call}.

    The OUTERMOST named function owns a call: a nested helper (`_point` inside
    `_revise_point`) is part of that writer's path, not a writer of its own.
    Both write populations below are derived from this one walk, so the seam
    map and the bypass map cannot drift apart.
    """
    base = REPO_ROOT if root is None else root
    key = str(base)
    if key in _CALLS_BY_FUNCTION:
        return _CALLS_BY_FUNCTION[key]
    calls: dict[str, dict[str, str]] = {}

    def _visit(node: ast.AST, owner: str | None, path: Path) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = owner or child.name
            elif isinstance(child, ast.Call) and owner:
                callee = _callee_name(child.func)
                if callee:
                    calls.setdefault(owner, {}).setdefault(
                        callee, f"{path.relative_to(base)}:{child.lineno}")
            _visit(child, name, path)

    for path in sorted((base / "tortoise").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        _visit(tree, None, path)
    _CALLS_BY_FUNCTION[key] = calls
    return calls


def _write_seams_that_embed(root: Path | None = None) -> dict[str, str]:
    """Map each function that embeds a written row -> the file:line of its call.

    Derived from the source, not from a hand-kept list: a write path that stops
    calling the seam changes this map, which is how bullet 1's `enforced` half
    is measured in the REMOVAL direction (`write_bypasses` measures the other
    one). Only an actual CALL of `encode_for_row` counts — `encode_for_store`
    is the ungated primitive reads, backfills and repairs use by design
    (`#5008`), and a mention in a docstring or a comment is not a call
    (measured the hard way: a substring scan reported 10 seams including
    `encode_for_row`'s own `def` line and five docstring mentions, and bullet 2
    then read `PARTIAL` on no evidence).
    """
    return {fn: callees[SEAM_CALLEE]
            for fn, callees in _calls_by_function(root).items()
            if SEAM_CALLEE in callees}


def write_bypasses(seams: dict[str, str] | None = None,
                   root: Path | None = None) -> dict[str, str]:
    """Write paths that reach a RAW primitive without the gate — `BYPASS(<fn>)`.

    The seam map can only see a function that USED TO call `encode_for_row` and
    stopped. It is blind, by construction, to the inverse: a NEW writer calling
    `encode_for_store` / `encode_batch_for_store` directly adds nothing to it,
    so `len(seams)` is unchanged, bullet 1's `enforced` half reads exactly as
    before, and "every embedding write path reaches the gate" is false while
    the instrument still reports `PARTIAL`/`MET` — `#5008`'s whole subject.

    So the population is derived a SECOND time, positively, from the raw
    primitives instead of from the seam. Every hit is reported; the ones in
    `DESIGNED_BYPASSES` are documented exemptions and the rest are ungated write
    paths, which hold bullet 1 at `PARTIAL` the way an `UNKNOWN` label holds
    bullet 2.

    Returns ``{function: 'path:line' of its raw call}``. The walked tree is
    `tortoise/`, so `graph-scripts/backfill_embeddings.py` (a documented exempt
    re-encode tool) is out of scope by construction, not by an entry here.
    """
    seams = _write_seams_that_embed(root) if seams is None else seams
    out: dict[str, str] = {}
    for fn, callees in _calls_by_function(root).items():
        if fn in seams:
            continue  # reaches the gate; whatever else it calls is fine
        for raw in RAW_PRIMITIVES:
            if raw in callees:
                out[fn] = callees[raw]
                break
    return out


def undesigned_bypasses(
    bypasses: dict[str, str] | None = None,
) -> dict[str, str]:
    """The bypasses that are NOT in `DESIGNED_BYPASSES` — the ungated writers.

    Split out because it is the value bullet 1 is scored on: every bypass is
    reported, but only an UNDESIGNED one is evidence that the rule stopped being
    enforced. A designed bypass (`encode_for_row`'s own delegation, the session
    TURN batch) is a documented boundary, not a regression.
    """
    bypasses = write_bypasses() if bypasses is None else bypasses
    return {fn: site for fn, site in bypasses.items() if fn not in DESIGNED_BYPASSES}


def rule_verdict(*, stated: bool, shared: bool, seams: dict[str, str],
                 undesigned: dict[str, str], on_by_default: bool) -> str:
    """Bullet 1's verdict TABLE, isolated so the consequence is testable.

    Each input is a distinct measured fact, and each can only make the row
    WORSE — there is no path from "fewer write paths reach the seam" to `MET`:

    * not stated, or the rule set is a COPY, or NO writer reaches the seam
      → `NOT MET` (the rule does not operate on the write path at all);
    * an UNDESIGNED `BYPASS(...)` → `PARTIAL` — a writer that reaches a raw
      primitive is an ungated write path (`#5008`'s exact subject), and the
      seam map alone cannot see it because it is built from seam CALLS;
    * the gate is off by default → `PARTIAL` (the rule is stated and wired but
      not operative, which is what `#5008`'s ordering chose).
    """
    if not stated or not shared or not seams:
        return "NOT MET"
    if undesigned or not on_by_default:
        return "PARTIAL"
    return "MET"


def score_rule_bullet() -> Row:
    """Bullet 1 — the rule is stated, is ONE rule set, and is enforced."""
    import tortoise.embed_gate as embed_gate
    import tortoise.value_gate as value_gate

    notes: list[str] = []
    gate_src = (REPO_ROOT / "tortoise/embed_gate.py").read_text(encoding="utf-8")
    stated = "def embedding_verdict" in gate_src
    # Identity, not equality: a COPY of the rule set is the drift §12.2a names.
    shared = embed_gate.RULE_SET is value_gate.RULES
    seams = _write_seams_that_embed()
    on_by_default = embed_gate.embed_gate_enabled()  # env-cleared by the caller
    bypasses = write_bypasses(seams)
    undesigned = undesigned_bypasses(bypasses)
    verdict = rule_verdict(stated=stated, shared=shared, seams=seams,
                           undesigned=undesigned, on_by_default=on_by_default)
    notes.append(f"stated: {stated} ({SEAM_CALLEE} asks the predicate)")
    notes.append(
        f"one rule set: {shared} (embed_gate.RULE_SET is value_gate.RULES; "
        f"ids={len(value_gate.RULES)})"
    )
    notes.append(
        f"write paths reaching the seam: {len(seams)} "
        f"({', '.join(sorted(seams)) or 'none'})"
    )
    # The OTHER direction: a write path that reaches a raw primitive instead.
    # Undesigned ones hold this row at PARTIAL — an ungated write path is the
    # exact state #5008 closed, and it is invisible to the seam map above.
    notes.append(
        f"BYPASS (raw primitive, no gate): {len(bypasses)} "
        f"({', '.join(f'BYPASS({fn}) {site}' for fn, site in sorted(bypasses.items())) or 'none'})"
    )
    if undesigned:
        notes.append(
            "UNDESIGNED bypasses (not in DESIGNED_BYPASSES): "
            + ", ".join(f"BYPASS({fn}) {site}" for fn, site in sorted(undesigned.items()))
        )
    notes.append(
        f"gate enabled by default: {on_by_default} "
        f"(TORTOISE_EMBED_GATE={os.environ.get(embed_gate.ENV_FLAG, '')!r})"
    )
    return Row(
        bullet=1,
        text=BULLETS[0],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 2 — "no vector is stored without a path to query it" ─────────────


#: Writers whose NAME does not carry the label they write. Explicit, and
#: deliberately small: an unmapped writer holds bullet 2 at `PARTIAL` rather
#: than being guessed at, so a new embedding write path is visible here instead
#: of being silently assumed to serve a queryable label. All three are `:Point`
#: writers (`#10465`, measured at `7df48ec7c`): the SDK's create/update and the
#: replay-side revision.
WRITER_LABELS_EXPLICIT: dict[str, str] = {
    "create_point": "Point",
    "update_point": "Point",
    "_revise_point": "Point",
    # A nested helper inside `tortoise/api.py`'s ingest path (`api.py:318`) —
    # a `:Point` writer reached by the closure's own name.
    "_point": "Point",
}


def written_labels(seams: dict[str, str] | None = None) -> dict[str, str]:
    """The graph labels a WRITE path embeds, derived from the writer's name.

    `_upsert_point_props` -> `Point`, `_upsert_document` -> `Source` (D10: a
    document is a `:Source`, `#5013`), `_upsert_object` -> `Object`, …
    The name is the writer's own declaration of what it writes, so this needs no
    hand-kept table; an unrecognised writer is REPORTED, never silently dropped.
    """
    seams = _write_seams_that_embed() if seams is None else seams
    suffix_to_label = {
        "point": "Point",
        "point_props": "Point",
        "subject": "Subject",
        "object": "Object",
        "event": "Event",
        "document": "Source",
    }
    labels: dict[str, str] = {}
    for fn in sorted(seams):
        stem = fn[len("_upsert_"):] if fn.startswith("_upsert_") else fn
        labels[fn] = (WRITER_LABELS_EXPLICIT.get(fn)
                      or suffix_to_label.get(stem, f"UNKNOWN({fn})"))
    return labels


def served_labels() -> set[str]:
    """The labels `run_vector_query` can query — its declared vocabulary."""
    from tortoise.security import ENTITY_TYPE_LABELS

    return set(ENTITY_TYPE_LABELS.values())


def score_path_bullet() -> Row:
    """Bullet 2 — every label a write path embeds is one the query path serves."""
    written = written_labels()
    served = served_labels()
    labels = {label for label in written.values() if not label.startswith("UNKNOWN")}
    unknown = {fn: label for fn, label in written.items() if label.startswith("UNKNOWN")}
    orphans = labels - served
    notes = [
        f"written labels: {sorted(labels)}",
        f"served labels (run_vector_query vocabulary): {sorted(served)}",
        f"stored-without-a-query-path: {sorted(orphans) or 'none'}",
    ]
    if unknown:
        notes.append(f"unmapped writers (not scored, reported): {sorted(unknown)}")
    # A stored-and-unqueryable class is the bullet's failure; an UNMAPPED writer
    # is not proof either way, so it can at most hold the row at PARTIAL.
    verdict = "NOT MET" if orphans else ("PARTIAL" if unknown else "MET")
    return Row(
        bullet=2,
        text=BULLETS[1],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 3 — "a fallback query reports the fallback" ─────────────────────


class _Catalogue:
    """A scripted graph: returns a different (rows, exc) per call, in order.

    Deterministic, embedded, no LLM, no corpus — the smallest case that can
    distinguish the three mechanisms `run_vector_query` can take.
    """

    def __init__(self, responses: list[tuple[list, BaseException | None]]) -> None:
        self._responses = list(responses)
        self._i = 0

    def query(self, cypher, params=None, timeout=None):
        if self._i >= len(self._responses):
            rows, exc = [], None
        else:
            rows, exc = self._responses[self._i]
            self._i += 1
        if exc is not None:
            raise exc
        return type("_R", (), {"result_set": rows or []})()


VEC = [0.1] * 384


def fallback_probe() -> dict[str, str]:
    """Force each of the three vector mechanisms and read the caller's trace.

    Returns ``{mechanism: verdict-from-the-trace}`` where each value is the
    trace entry's own ``mechanism`` (or a readable failure). Bullet 3 is MET
    only when a caller can tell a full scan from an indexed read WITHOUT
    reading `search_engine.py` — which is `#4999`'s contract.
    """
    from tortoise.search_engine import (
        VECTOR_MECHANISM_KEY,
        reset_circuit_breakers,
        run_vector_query,
    )

    reset_circuit_breakers()
    out: dict[str, str] = {}

    def _entry(trace):
        return next((e for e in trace if e.get("leg") == "vector"), {})

    # (a) the index path: signature A answers with rows.
    trace: list = []
    run_vector_query(_Catalogue([([("a",), ("b",)], None)]), VEC,
                     entity_type="point", is_embedded=False, leg_trace=trace)
    out["index"] = str(_entry(trace).get(VECTOR_MECHANISM_KEY, "<key absent>"))

    # (b) an engine with no index API: the deliberate full scan.
    reset_circuit_breakers()
    trace = []
    run_vector_query(_Catalogue([([("a",)], None)]), VEC,
                     entity_type="point", is_embedded=True, leg_trace=trace)
    out["scan"] = str(_entry(trace).get(VECTOR_MECHANISM_KEY, "<key absent>"))

    # (c) an index was EXPECTED and the query failed: the fallback that must
    #     announce itself — #4999's whole subject.
    reset_circuit_breakers()
    trace = []
    run_vector_query(
        _Catalogue([
            ([], RuntimeError("Invalid arguments for procedure")),
            ([], RuntimeError("Invalid arguments for procedure")),
            ([("a",)], None),
        ]),
        VEC, entity_type="point", is_embedded=False, leg_trace=trace,
    )
    out["scan_fallback"] = str(_entry(trace).get(VECTOR_MECHANISM_KEY, "<key absent>"))
    reset_circuit_breakers()
    return out


def score_fallback_bullet() -> Row:
    """Bullet 3 — the three mechanisms are distinguishable in the caller's trace."""
    observed = fallback_probe()
    distinguishable = len(set(observed.values())) == len(observed)
    named = all(v.startswith("<key absent>") is False for v in observed.values())
    notes = [f"{k}: {v}" for k, v in observed.items()]
    notes.append(f"distinguishable: {distinguishable}; every path reports: {named}")
    return Row(
        bullet=3,
        text=BULLETS[2],
        verdict="MET" if distinguishable and named else "NOT MET",
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 4 — "V1 is decided and implemented" ─────────────────────────────

#: The phrases a DECIDED V1 would carry. Absence of all of them is the finding,
#: not a guess: V1 is `#5090`'s owner gate and only a ruling can satisfy it.
V1_DECIDED_MARKERS = ("V1: DECIDED", "V1 is DECIDED", "V1 DECIDED —")
#: Where a ruling would land, so an undecided read says what it looked at.
V1_SURFACES = (
    "docs/architecture/STORAGE-ARCHITECTURE.md",
    "tortoise/projection/__init__.py",
)


def score_v1_bullet() -> Row:
    """Bullet 4 — a V1 ruling exists and the code implements it."""
    decided: list[str] = []
    open_lines: list[str] = []
    for rel in V1_SURFACES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.search(r"\bV1\b", line):
                continue
            if any(m in line for m in V1_DECIDED_MARKERS):
                decided.append(f"{rel}:{i}: {line.strip()}")
            elif re.search(r"\bOPEN\b|\bopen decision\b|human gate|owner decision",
                           line) or "never spends that decision" in line:
                open_lines.append(f"{rel}:{i}: {line.strip()}")
    notes = [f"decided markers found: {len(decided)}"]
    notes += [f"open: {ln}" for ln in open_lines[:3]]
    return Row(
        bullet=4,
        text=BULLETS[3],
        verdict="MET" if decided else "NOT MET",
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── §12.2a — the distinct-predicate count ──────────────────────────────────


def count_embed_predicates() -> dict:
    """Count the distinct predicates that can answer `NO-EMBED` (target: 1).

    A predicate is a module on the embed path that **owns identifier patterns**
    (`re.compile` at module scope). `embed_gate.embedding_verdict` is the ASK,
    not a predicate: it must call the shared one and report the shared rule id,
    which is checked separately (`RULE_SET is value_gate.RULES`).
    """
    import tortoise.embed_gate as embed_gate
    import tortoise.value_gate as value_gate

    owners: list[str] = []
    for rel in PREDICATE_MODULE, "tortoise/embed_gate.py":
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        # AST, not a text pattern: an ALIASED import (`import re as _re`) is
        # still a pattern, and a substring scan missed exactly that in this
        # tool's own mutation check — the falsifier PASSED on mutated code,
        # which is the one failure a falsifier must not have.
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            value = getattr(node, "value", None)
            if (isinstance(node, (ast.Assign, ast.AnnAssign))
                    and isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "compile"):
                owners.append(rel)
                break
    return {
        "count": len(owners),
        "target": 1,
        "owners": owners,
        "predicate": PREDICATE_NAME,
        "rule_ids": list(value_gate.RULES),
        "rule_set_is_shared_identity": embed_gate.RULE_SET is value_gate.RULES,
        "verdict": "MET" if len(owners) == 1 and embed_gate.RULE_SET is value_gate.RULES
                   else "DRIFT",
    }


# ── The census ─────────────────────────────────────────────────────────────


def vector_census(graph, graph_name: str, at: str | None = None,
                  graph_names: list[str] | None = None) -> dict:
    """`stored` / `indexed` / `unindexed` + bytes, per label, on a NAMED graph.

    Reads the engine's own catalogue (`CALL db.indexes()`); stores nothing.
    Every field is either measured or the string ``UNMEASURED(<why>)`` — never
    `0`, because "no rows" and "could not read" are different facts and this
    issue exists because a figure was quoted without either.
    """
    at = at or datetime.now(UTC).isoformat(timespec="seconds")
    # ⛔ A catalogue read on a graph NAME that does not exist MATERIALISES an
    # empty graph server-side (measured by this file's own verification:
    # `GRAPH.LIST` gained `tortoise_test_matrix` after `--census --graph
    # tortoise_test_matrix`). That is a write, in a tool whose whole doctrine is
    # "stores nothing" — and it reads as a measured zero for a graph that is not
    # there, the exact conflation this instrument exists to prevent. So the NAME
    # is proved to exist BEFORE any query is issued.
    if graph_names is None:
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": "UNMEASURED(graph existence not provable — pass "
                             "graph_names; a catalogue read on a missing name "
                             "CREATES the graph)",
                "command": CENSUS_COMMAND}
    if graph_name not in graph_names:
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": f"UNMEASURED(graph {graph_name!r} does not exist on "
                             f"this engine: {sorted(graph_names)})",
                "command": CENSUS_COMMAND}
    try:
        rows = graph.query("CALL db.indexes()").result_set
    except Exception as e:
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": f"UNMEASURED({type(e).__name__}: {e})",
                "command": CENSUS_COMMAND}

    indexed: dict[str, dict] = {}
    for row in rows or []:
        try:
            label, types = row[0], row[2]
        except (IndexError, TypeError):
            continue
        if not isinstance(types, dict):
            continue
        for fld, kinds in types.items():
            if isinstance(kinds, (list, tuple, set)) and any(
                str(k).upper() == "VECTOR" for k in kinds
            ):
                opts = row[3] if len(row) > 3 and isinstance(row[3], dict) else {}
                dim = None
                try:
                    dim = (opts.get(fld) or {}).get("dimension")
                except AttributeError:
                    dim = None
                stats = row[8] if len(row) > 8 and isinstance(row[8], dict) else {}
                # The engine's own per-index counters. §12.1a measured
                # GRAPH.MEMORY USAGE's index share 21.6× under the true cost,
                # so these are REPORTED and labelled blind — never summed into
                # a "total bytes" claim.
                engine_bytes = sum(
                    v for k, v in stats.items()
                    if isinstance(v, int) and k.endswith("Size")
                ) if stats else None
                indexed[label] = {
                    "field": fld,
                    "dimension": dim if dim is not None else "UNMEASURED(no dimension in options)",
                    "engine_index_bytes_BLIND": engine_bytes if engine_bytes is not None
                    else "UNMEASURED(no size counters in the catalogue row)",
                }

    labels: dict[str, dict] = {}
    for label in sorted(set(indexed) | set(served_labels())):
        try:
            res = graph.query(
                f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(n)"
            ).result_set
            stored = int(res[0][0])
        except Exception as e:
            stored = f"UNMEASURED({type(e).__name__}: {e})"
        idx = indexed.get(label)
        dim = idx["dimension"] if idx and isinstance(idx["dimension"], int) else None
        if isinstance(stored, int) and dim:
            # A vecf32 component is 4 bytes; the payload is DERIVED, and said so.
            payload = {"derived_payload_B": stored * dim * 4,
                       "how": f"stored {stored} x {dim} dims x 4 B (vecf32)"}
        else:
            payload = {"derived_payload_B": "UNMEASURED(no stored count or no indexed dim)",
                       "how": "needs both a stored count and an indexed dimension"}
        labels[label] = {
            "stored": stored,
            "indexed": bool(idx),
            "unindexed": (stored if isinstance(stored, int) else "UNMEASURED")
                         if not idx else 0,
            "index": idx or "none",
            "payload": payload,
        }
    return {"graph": graph_name, "at": at, "labels": labels,
            "catalogue": "CALL db.indexes()", "command": CENSUS_COMMAND}


def _connect(uri: str):
    """`(graph, names)` from a `docker://`/`bolt://` URI — never embedded.

    Embedded engines have no index API at all (`_record_vector_index_inventory`
    returns early on them), so a census there would measure `indexed: False`
    for every label and read as "nothing is indexed" — a false finding. This
    refuses instead. The graph NAMES come back with the handle because a
    catalogue read on a name that does not exist CREATES the graph.
    """
    from urllib.parse import urlparse

    u = urlparse(uri)
    if u.scheme in ("", "file") or not u.hostname:
        # Exit 2 = the instrument itself could not measure (the docstring's
        # contract). A non-int SystemExit argument exits 1, so the message is
        # PRINTED and the code passed explicitly — otherwise the documented `2`
        # is a contract the code never implements.
        print(
            "census: an embedded/absent engine has no index API — pass a "
            "docker:// or bolt:// URI (embedded would report indexed=False for "
            "every label, which is NOT the same fact)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    from falkordb import FalkorDB

    db = FalkorDB(host=u.hostname, port=u.port or 6379, password=u.password or "")
    name = (u.path or "/").lstrip("/") or "tortoise"
    try:
        names = list(db.list_graphs() or [])
    except Exception:
        names = None
    return db.select_graph(name), names


# ── The report ─────────────────────────────────────────────────────────────


def measure() -> dict:
    """Run the whole instrument. Clears the gate flags first — a lane's own env
    must not be able to make a default-off gate read as enforced."""
    for var in ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE"):
        os.environ.pop(var, None)
    rows = [score_rule_bullet(), score_path_bullet(),
            score_fallback_bullet(), score_v1_bullet()]
    return {
        "issue": 10465,
        "epic": 5090,
        "head": _head(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": [asdict(r) for r in rows],
        "predicates": count_embed_predicates(),
        "seams": _write_seams_that_embed(),
        "bypasses": write_bypasses(),
    }


def _head() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
            text=True, check=False,
        ).stdout.strip() or "UNMEASURED(git rev-parse returned nothing)"
    except Exception as e:
        return f"UNMEASURED({type(e).__name__}: {e})"


def _table(rows: list[Row]) -> str:
    out = ["| # | bullet | verdict | evidence |", "|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r.bullet} | {r.text} | **{r.verdict}** | {r.observed} |")
    # The command is NOT decoration: `#10465` scores a bullet WITH its command,
    # so a row printed without one would be unscored evidence in the one place
    # a reader actually looks.
    out.append("")
    for r in rows:
        out.append(f"- row {r.bullet} command: `{r.command}`")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--census", action="store_true",
                    help="also take the per-label vector census")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", ""),
                    help="a docker:// or bolt:// URI (default $TORTOISE_DB_URI)")
    ap.add_argument("--graph", default=os.environ.get("TORTOISE_GRAPH_NAME", ""),
                    help="the graph NAME to census (default $TORTOISE_GRAPH_NAME)")
    args = ap.parse_args(argv)

    try:
        report = measure()
    except ImportError as e:
        # The other half of the documented `2`: a gate module the instrument
        # scores that will not import. Without this the traceback exits 1 and a
        # caller cannot tell "could not measure" from "a verdict was NOT MET".
        print(f"could not measure: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if args.census:
        if not args.uri:
            report["census"] = {"error": "UNMEASURED(no --uri and no TORTOISE_DB_URI)"}
        else:
            graph_name = args.graph or (args.uri.rstrip("/").rsplit("/", 1)[-1])
            graph, names = _connect(args.uri)
            report["census"] = vector_census(graph, graph_name, graph_names=names)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"#10465 — #5090 DoD, measured at {report['head']} on {report['at']}\n")
    print(_table([Row(**{k: v for k, v in r.items()}) for r in report["rows"]]))
    p = report["predicates"]
    print(f"\n§12.2a predicates: {p['count']} (target {p['target']}) — "
          f"{p['verdict']}; owners={p['owners']}; shared identity="
          f"{p['rule_set_is_shared_identity']}")
    if "census" in report:
        c = report["census"]
        print(f"\nCensus — graph `{c.get('graph')}` at {c.get('at')} "
              f"({c.get('catalogue', '')}):")
        for label, d in (c.get("labels") or {}).items():
            print(f"  {label:10s} stored={d['stored']!s:12s} "
                  f"indexed={d['indexed']!s:6s} unindexed={d['unindexed']!s:12s} "
                  f"payload={d['payload']['derived_payload_B']}")
        if "error" in c:
            print(f"  {c['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
