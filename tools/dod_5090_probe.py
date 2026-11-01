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
| 1 | `uv run python tools/dod_5090_probe.py --json` (reads the two gate modules + counts the write seams that reach the gate) | a stated predicate exists, ONE rule set (identity, not a copy), AND every embedding write path reaches it with the gate ON by default |
| 2 | same (`written labels ⊆ served labels`) | every label a write path embeds is one `run_vector_query` serves |
| 3 | same (forces all three mechanisms through `run_vector_query`) | `index` / `scan` / `scan_fallback` are distinguishable in the caller's trace |
| 4 | same (searches for a V1 ruling) | **`MET` is unreachable here**: the bullet is *decided **and implemented***, and this row cannot measure implementation — a record at most carries it to `PARTIAL`, and no record is `NOT MET` |

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
successful measurement), `2` when the instrument itself could not measure:
a gate module it scores will not import, a `--census` URI whose engine has no
index API, or a census whose catalogue read came back `UNMEASURED`. Never a
verdict-derived code: a caller must not be able to read `NOT MET` as "the tool
failed".
"""

from __future__ import annotations

import sys

#: `#5128`'s contract, applied to THIS entry point: refuse an interpreter older
#: than the floor BEFORE any other import. The guard must also be PROTECTIVE
#: rather than documentary — nothing below may use 3.12-only SYNTAX, or the file
#: would die in the tokenizer and never reach this statement. That is why the
#: two nested same-quote f-strings in `score_rule_bullet` are built as plain
#: locals: PEP 701 (`f"…{d['at']}…"` inside a `"`-delimited f-string,
#: https://peps.python.org/pep-0701/ — *Python-Version: 3.12*, "quote reuse")
#: cannot be tokenized before 3.12, and `tests/test_dod_5090_probe.py::
#: test_the_tool_parses_under_the_floor` pins that.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/dod_5090_probe.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/dod_5090_probe.py`"
    )

import argparse
import ast
import contextlib
import functools
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERDICTS = ("MET", "PARTIAL", "NOT MET")

#: `#5090`'s four bullets, verbatim from the epic body. Held here so the table
#: cannot silently score a re-worded bullet: the pin
#: `tests/test_dod_5090_probe.py::test_the_four_bullets_are_scored_verbatim`
#: reads them back and the epic body is the source of truth.
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


#: The GATED seam (`#5008`). A write path that embeds a row must reach it.
SEAM_CALL = "encode_for_row"

#: The ungated primitives that ALSO produce a stored vector. `encode_for_store`
#: is documented as the reads/backfills/repairs primitive (so its callers are
#: `producer`, not `writer`), but `compute_session_embedding` feeds the session
#: `:Event` writers — a write path that never touches the gate. Both are
#: measured, because "a NEW writer that calls the raw primitive instead" is
#: invisible to a map that only ever looks for the gated call (this tool's own
#: review found exactly that: `session_indexer.main` and
#: `sdk._session_embedding` embed while containing neither gate symbol).
UNGATED_CALLS: tuple[str, ...] = ("encode_for_store", "compute_session_embedding")

#: `MERGE (n:Label …)` / `CREATE (p:Label …)` — the label the Cypher names.
_NODE_LABEL = re.compile(r"(?:MERGE|CREATE|MATCH)\s*\(\s*(\w+)\s*:\s*(\w+)")

#: An embedding WRITE — a Cypher property assignment or the clause helper.
_EMBED_WRITE = re.compile(
    r"(?:\w+\.embedding|(?<![\w.])embedding)\s*=|set_embedding_clause\("
)


def _strings(node) -> str:
    """Every string literal in ``node`` — where the Cypher actually lives."""
    import ast

    return "\n".join(
        s.value for s in ast.walk(node)
        if isinstance(s, ast.Constant) and isinstance(s.value, str)
    )


def _callees(node) -> set[str]:
    import ast

    return {
        getattr(c.func, "id", None) or getattr(c.func, "attr", None) or ""
        for c in ast.walk(node) if isinstance(c, ast.Call)
    }


#: Writers whose own Cypher names NO label because the write is delegated to
#: the projection (they hand the vector to the same `:Point` clause
#: `_revise_point` uses) — ONE entry, each with the evidence and each REPORTED
#: in the row. Deliberately not a vocabulary: everything else is derived from
#: the Cypher, so a writer that names `:Document` still trips bullet 2.
WRITER_LABEL_OVERRIDES: dict[str, str] = {
    # `tortoise/api.py`'s ingest closure computes the vector and stamps the
    # journal; its Cypher carries no `MERGE (:Label`. It feeds the `:Point`
    # write (`api.py:318` -> projection's point clause).
    "_point": "Point",
}


#: `{root: {module::name, …}}` — filled by `_embedding_writers`, so the row can
#: say which written labels were assigned rather than derived.
_OVERRIDDEN_BY_ROOT: dict[Path, frozenset[str]] = {}


def embedding_writers() -> dict[str, dict]:
    """Every function that produces a stored vector, and what it writes.

    ``{writer: {"callee", "at", "kind", "label"}}`` where ``kind`` is
    ``gated`` (reaches ``encode_for_row``), ``ungated`` (produces a vector the
    gate never sees) or ``producer`` (a function that computes a vector a
    DIFFERENT function writes — `encode_for_store`'s read/backfill callers, and
    the journal stampers). ``writer`` is ``<dotted module>::<function>``, so two
    writers in different modules can never share a key.

    **The label is derived from the Cypher, not from the writer's name.** That
    matters: a name-derived map draws from the same small vocabulary as the
    query path, so its "stored without a query path" set is empty BY
    CONSTRUCTION and the `NOT MET` branch is dead code — a row that cannot come
    back red is machinery, not measurement (this tool's own review, P1). Three
    sources, most local first:

    1. the labels in the function's OWN Cypher literals;
    2. the writer's name stem against the labels its MODULE's Cypher names
       (`_upsert_event` -> `Event`), which is two sources agreeing, not a guess;
    3. otherwise ``UNKNOWN`` — reported, and it holds bullet 2 at `PARTIAL`.
    """

    return _embedding_writers(REPO_ROOT)


def _module_name(path: Path, root: Path) -> str:
    """The DOTTED module path a file is imported as — `tortoise.projection`.

    ⛔ NOT `path.stem`. A stem is the BASENAME, so every package's
    `__init__.py` collapses into `__init__` (8 of them under `tortoise/` today),
    as do `tortoise/monitoring.py` / `tortoise/shared_state/monitoring.py` and
    the two `selfhost.py`. Python's own module identity is the dotted path, and
    a package's module **is** the package: `__init__.py` is *implicitly
    executed, and the objects it defines are bound to names in the package's
    namespace* (docs, the import system §5.2).

    The collapse was not cosmetic: the SECOND writer under a shared key became
    `duplicate`, which `written_labels()` excluded, so a writer whose label
    could not be resolved silently vanished from the set bullet 2 compares
    while bullet 2 still read `MET` (this tool's own review, P2).
    """
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@functools.lru_cache(maxsize=8)
def _embedding_writers(root: Path) -> dict[str, dict]:
    writers: dict[str, dict] = {}
    overridden: set[str] = set()
    seam_module = root / "tortoise/embeddings.py"
    for path in sorted((root / "tortoise").rglob("*.py")):
        if path == seam_module:
            continue  # the primitives themselves are not write paths
        rel = path.relative_to(root).as_posix()
        module = _module_name(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            # ⛔ Keyed by the PATH, not by one shared sentinel: N modules that
            # will not parse used to collapse into a single `UNPARSEABLE`
            # entry carrying ONE path, so the row named a hole it could not
            # locate and the rest were unreported (this tool's own review, P3).
            writers.setdefault(f"UNPARSEABLE:{rel}", {
                "callee": "", "at": rel, "kind": "unparsed",
                "label": "UNKNOWN",
            })
            continue
        module_labels = {lab for _v, lab in _NODE_LABEL.findall(_strings(tree))}
        file_src = path.read_text(encoding="utf-8")
        lines = file_src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            called = _callees(node)
            # The function's own text by LINE RANGE — `ast.get_source_segment`
            # re-splits the whole file per call, which cost 90 s on this tree.
            source = "\n".join(lines[node.lineno - 1:node.end_lineno])
            kind: str | None = None
            if SEAM_CALL in called:
                callee, kind = SEAM_CALL, "gated"
            elif set(UNGATED_CALLS) & called:
                callee = sorted(set(UNGATED_CALLS) & called)[0]
                kind = "ungated"
                # The write clause is a CALL (`set_embedding_clause("n")`), so
                # this reads the function's SOURCE, not its string literals —
                # measured: reading only literals classed
                # `session_indexer.main` (which does write) as a producer and
                # hid a real ungated write path.
                if not _EMBED_WRITE.search(source):
                    kind = "producer"  # computes it; another function writes it
            else:
                # No recognised producer call: if it still WRITES an embedding,
                # the chain it uses is outside this vocabulary and the row would
                # otherwise report `OUTSIDE the gate: 1` for a tree with three
                # (review round 5, P2 — measured: 17 functions carry an
                # embedding-write clause, 9 of them were absent from this map).
                if kind is None and _EMBED_WRITE.search(source):
                    writers[f"{module}::{node.name}"] = {
                        "callee": "unrecognised",
                        "at": f"{rel}:{node.lineno}",
                        "kind": "unresolved_write",
                        "label": "UNKNOWN(unresolved producer)",
                    }
                continue
            own = _strings(node)
            pairs = _NODE_LABEL.findall(own)
            labels = {lab for _v, lab in pairs}
            if len(labels) == 1:
                label = labels.pop()
            elif len(labels) > 1:
                # Two labels in one writer's Cypher: picking one would report a
                # stored label as covered while the other may be unserved.
                label = "UNKNOWN(two labels: " + ",".join(sorted(labels)) + ")"
                writers[f"{module}::{node.name}"] = {
                    "callee": callee, "at": f"{rel}:{node.lineno}",
                    "kind": kind, "label": label,
                }
                continue
            else:
                stem = (node.name[len("_upsert_"):]
                        if node.name.startswith("_upsert_") else node.name)
                hits = {lab for lab in module_labels
                        if stem.lower().endswith(lab.lower())}
                label = hits.pop() if len(hits) == 1 else "UNKNOWN"
            if label == "UNKNOWN" and node.name in WRITER_LABEL_OVERRIDES:
                # The label is recorded, and `score_path_bullet` reports it as
                # an OVERRIDE (not a Cypher derivation) by matching the
                # `module::name` key — so a same-named writer in another module
                # cannot silently inherit it.
                label = WRITER_LABEL_OVERRIDES[node.name]
                overridden.add(f"{module}::{node.name}")
            # ⛔ Keyed by the DOTTED module path, not by the file's stem: a bare
            # name merges two writers across modules (last-path-wins, no
            # sentinel) and silently SHRINKS both rows — `main` is exactly such
            # a name, and it is the only ungated entry today. A STEM is not a
            # module: it collapses every package `__init__.py` into `__init__`
            # (review round 5, P2).
            key = f"{module}::{node.name}"
            if key in writers:
                writers[key] = {
                    "callee": callee,
                    "at": f"{rel}:{node.lineno}",
                    "kind": "duplicate",
                    "label": "UNKNOWN(duplicate writer name)",
                }
                continue
            writers[key] = {
                "callee": callee,
                "at": f"{rel}:{node.lineno}",
                "kind": kind,
                "label": label,
            }
    _OVERRIDDEN_BY_ROOT[root] = frozenset(overridden)
    return writers


def _overridden_writers() -> set[str]:
    """Writers whose label came from a documented OVERRIDE, not from Cypher."""
    return set(_OVERRIDDEN_BY_ROOT.get(REPO_ROOT, frozenset()))


def _write_seams_that_embed() -> dict[str, str]:
    """The GATED seams only: ``{writer: file:line}`` for `#5008`'s call sites."""
    return {w: d["at"] for w, d in embedding_writers().items() if d["kind"] == "gated"}


#: Where a "shipped default" for a flag would live. Searched, not assumed:
#: `measure()` clears the ambient env so a lane's own shell cannot make a
#: default-off gate read as enforced, which also means the ambient value can
#: NEVER answer "is this shipped on" (review round 3, P2).
CONFIG_SURFACES: tuple[str, ...] = (
    ".env.example",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "config",
    ".github/workflows",
)


def flag_in_shipped_configs(flag: str) -> dict:
    """Is ``flag`` switched ON by any configuration this repo ships?

    Returns ``{"on": bool, "files": [...], "searched": [paths]}``. A flag set
    nowhere is a measured `False` — with the list of places that were searched,
    so a reader can see what "nowhere" covered.
    """
    hits: list[str] = []
    searched: list[str] = []
    for rel in CONFIG_SURFACES:
        target = REPO_ROOT / rel
        candidates = ([target] if target.is_file()
                      else sorted(target.rglob("*")) if target.is_dir() else [])
        for path in candidates:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if flag not in text:
                continue
            searched.append(path.relative_to(REPO_ROOT).as_posix())
            for line in text.splitlines():
                stripped = line.lstrip()
                # ⛔ A COMMENTED-OUT line is not a shipped default, and a line
                # that merely MENTIONS the flag (`# see TORTOISE_EMBED_GATE`)
                # is not an assignment. Measured: `# TORTOISE_EMBED_GATE=1`
                # read as ON — the one input that can flip bullet 1 to MET.
                # ⛔ `-` starts a YAML LIST ITEM (`- KEY=1`, standard for a
                # docker-compose `environment:` block), not a comment. Only a
                # `---` document marker is skipped.
                if (not stripped or stripped[0] == "#" or stripped.startswith("---")
                        or stripped.startswith(";") or flag not in stripped):
                    continue
                stripped = stripped.lstrip("-").lstrip()
                value = stripped.split(flag, 1)[1].lstrip("=: \t").strip()
                value = value.split()[0] if value.split() else ""
                value = value.strip("'\"").lower()
                if value in ("1", "true", "yes", "on"):
                    hits.append(f"{path.relative_to(REPO_ROOT)}: {stripped[:90]}")
    return {"on": bool(hits), "files": hits, "searched": searched,
            "surfaces": list(CONFIG_SURFACES)}


def score_rule_bullet() -> Row:
    """Bullet 1 — the rule is stated, is ONE rule set, and is enforced."""
    import tortoise.embed_gate as embed_gate
    import tortoise.value_gate as value_gate

    gate_src = (REPO_ROOT / "tortoise/embed_gate.py").read_text(encoding="utf-8")
    # Stated = the predicate EXISTS, and WIRED = the seam actually calls it.
    # Both are read; a decorative `SEAM` constant is not evidence (review
    # round 3, P3: "the parenthetical is never measured").
    stated_in_gate = "def embedding_verdict" in gate_src
    seam_wired = False
    try:
        import ast as _ast

        seam_src = (REPO_ROOT / "tortoise/embeddings.py").read_text(encoding="utf-8")
        for node in _ast.walk(_ast.parse(seam_src)):
            if (isinstance(node, _ast.FunctionDef) and node.name == "encode_for_row"):
                seg = _ast.get_source_segment(seam_src, node) or ""
                seam_wired = "embed_gate.embedding_verdict" in seg
                break
    except (OSError, SyntaxError):
        seam_wired = False
    stated = stated_in_gate and seam_wired
    # Identity, not equality: a COPY of the rule set is the drift §12.2a names.
    shared = embed_gate.RULE_SET is value_gate.RULES
    writers = embedding_writers()
    gated = {w: d for w, d in writers.items() if d["kind"] == "gated"}
    ungated = {w: d for w, d in writers.items() if d["kind"] == "ungated"}
    unparsed = [w for w, d in writers.items() if d["kind"] == "unparsed"]
    unresolved_writes = {w: d for w, d in writers.items()
                         if d["kind"] == "unresolved_write"}
    duplicates = [w for w, d in writers.items() if d["kind"] == "duplicate"]
    # ⛔ Two different questions, both asked: the ambient env is what
    # `measure()` deliberately clears (so a lane's shell cannot fake it), and
    # the SHIPPED config is where a default actually lives.
    ambient = embed_gate.embed_gate_enabled()
    shipped = flag_in_shipped_configs(embed_gate.ENV_FLAG)
    on_by_default = ambient or shipped["on"]
    # ⛔ Built as locals, never nested inside another f-string: nesting a
    # `'`-delimited f-string inside a `"`-delimited one is PEP 701 **syntax**
    # (`Python-Version: 3.12`), so it cannot be tokenized by the older
    # interpreters this file's `#5128` guard exists to refuse — the guard would
    # be documentary and the file would die before reaching it.
    ungated_at = ", ".join(f"{w} @ {d['at']}" for w, d in sorted(ungated.items()))
    unresolved_at = ", ".join(
        f"{w} @ {d['at']}" for w, d in sorted(unresolved_writes.items())
    )
    notes = [
        f"stated: {stated} (predicate in embed_gate: {stated_in_gate}; "
        f"{SEAM} calls it: {seam_wired})",
        f"one rule set: {shared} (embed_gate.RULE_SET is value_gate.RULES; "
        f"ids={len(value_gate.RULES)})",
        f"write paths reaching the gated seam: {len(gated)} "
        f"({', '.join(sorted(gated)) or 'none'})",
        f"write paths that embed OUTSIDE the gate: {len(ungated)} "
        f"({ungated_at or 'none'})"
        + (" — the session/Turn writers #5008 declares as exempt"
           if ungated else ""),
        f"gate enabled by default: {on_by_default} (ambient "
        f"{embed_gate.ENV_FLAG}={os.environ.get(embed_gate.ENV_FLAG, '')!r}; "
        f"shipped config surface walked: {len(shipped['surfaces'])} "
        f"({', '.join(shipped['surfaces'])}); mentioning it: "
        f"{shipped['searched'] or 'none'}"
        f"; set ON in: {shipped['files'] or 'none'})",
        # ⛔ A module that will not parse is a HOLE in the measurement, not a
        # zero: its writers — gated or bypassing — are invisible to every row
        # (this tool's own review, P2).
        f"unparseable modules (hold this row): {sorted(unparsed) or 'none'}",
        # ⛔ A function that writes an embedding with NO recognised producer
        # call is reported, not assumed clean: the session/Event chain calls
        # the primitive through `self._session_embedding`, so a three-name
        # callee vocabulary cannot see it (review round 5, P2).
        f"functions writing an embedding with no recognised producer call: "
        f"{len(unresolved_writes)} — deliberately OVER-INCLUSIVE (it also lists "
        f"repair/backfill helpers and the clause builder itself, because a "
        f"name-level enumeration cannot tell an exemption from a bypass; a "
        f"bypass that reads as exempt is the failure this row exists to "
        f"prevent): "
        f"{unresolved_at or 'none'}",
        f"duplicate writer names (hold this row): {sorted(duplicates) or 'none'}",
    ]
    if not stated or not shared or not gated:
        verdict = "NOT MET"
    elif not on_by_default or ungated or unparsed or unresolved_writes or duplicates:
        # EITHER condition alone means the rule is not the write path's
        # operative rule: the flag is the operator's, and an ungated writer is
        # a path the rule never sees. Turning the flag ON does not fix the
        # second — which is why it must hold the row, not be masked by it.
        verdict = "PARTIAL"
    else:
        verdict = "MET"
    return Row(
        bullet=1,
        text=BULLETS[0],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 2 — "no vector is stored without a path to query it" ─────────────


def written_labels(writers: dict[str, dict] | None = None) -> dict[str, str]:
    """``{writer: label}`` for every write path that STORES a vector.

    Includes the ungated session writers (they store an `:Event` vector) and
    excludes `producer`s: a function that computes a vector another function
    writes is not a label of its own. A label is `UNKNOWN` only when neither
    the writer's own Cypher nor its module's Cypher resolves it — and that is
    reported, never guessed.
    """
    writers = embedding_writers() if writers is None else writers
    # `unresolved_write` is INCLUDED and mapped to UNKNOWN: a function that
    # writes an embedding without a recognised producer call is a write path
    # whose label this instrument could not resolve, so it must hold the row —
    # excluding it made the row claim `written ⊆ served` over a set that left
    # out the very paths its sibling row flags (this tool's own review, P1).
    # `duplicate` is INCLUDED for the same reason: the later definition
    # overwrote the earlier one's label, so that label is UNRESOLVED — leaving
    # it out made bullet 2 read `MET` while bullet 1 held `PARTIAL` on the same
    # hole (this tool's own review, P2).
    return {
        w: (d["label"] if d["kind"] in ("gated", "ungated")
            else ("UNKNOWN(unresolved producer)" if d["kind"] == "unresolved_write"
                  else "UNKNOWN(duplicate writer name)"))
        for w, d in sorted(writers.items())
        if d["kind"] in ("gated", "ungated", "unresolved_write", "duplicate")
    }


def unparsed_modules(writers: dict[str, dict] | None = None) -> list[str]:
    """Modules whose writers could not be enumerated — a hole, never a zero.

    Returns the repo-relative PATHS: one sentinel key for every unparseable
    module named a hole with no location (this tool's own review, P3).
    """
    writers = embedding_writers() if writers is None else writers
    return sorted(d["at"] for d in writers.values() if d["kind"] == "unparsed")


def served_labels() -> set[str]:
    """The labels `run_vector_query` can query — its declared vocabulary.

    Deliberately NOT the same source as `written_labels`: the written side comes
    from the Cypher literals, the served side from the query vocabulary, so the
    two can disagree — which is the bullet.
    """
    from tortoise.security import VALID_ENTITY_TYPES, entity_label

    # ⛔ The labels the RUNNER actually serves — `run_vector_query` validates
    # against `VALID_ENTITY_TYPES` and then labels the query via `entity_label`.
    # `ENTITY_TYPE_LABELS.values()` is a SECOND, independently maintained
    # declaration: a type added to one and not the other would make this row
    # claim a query path that does not exist (or miss one that does).
    return {entity_label(t) for t in VALID_ENTITY_TYPES}


def score_path_bullet() -> Row:
    """Bullet 2 — every label a write path embeds is one the query path serves."""
    writers = embedding_writers()
    written = written_labels(writers)
    served = served_labels()
    labels = {label for label in written.values() if not label.startswith("UNKNOWN")}
    unknown = {fn: label for fn, label in written.items() if label.startswith("UNKNOWN")}
    duplicates = sorted(w for w, d in writers.items() if d["kind"] == "duplicate")
    unparsed = unparsed_modules(writers)
    orphans = labels - served
    notes = [
        f"written labels: {sorted(labels)}",
        f"served labels (run_vector_query vocabulary): {sorted(served)}",
        f"stored-without-a-query-path: {sorted(orphans) or 'none'}",
    ]
    if unknown:
        notes.append(f"unresolved writers (hold this row at PARTIAL): {sorted(unknown)}")
    if duplicates:
        notes.append(
            "duplicate writer identities (hold this row at PARTIAL — the later "
            f"definition overwrote the earlier one's label): {duplicates}"
        )
    if unparsed:
        notes.append(f"unparseable modules (hold this row at PARTIAL): {unparsed}")
    overridden = sorted(_overridden_writers() & set(written))
    if overridden:
        notes.append(
            f"labels assigned by the DOCUMENTED override, not derived from "
            f"Cypher (reported, as that comment promises): {overridden}"
        )
    # A stored-and-unqueryable class is the bullet's failure; an UNRESOLVED
    # writer (or a collapsed/duplicate identity, or a module that will not
    # parse) is not proof either way, so it can at most hold the row at PARTIAL.
    verdict = "NOT MET" if orphans else (
        "PARTIAL" if (unknown or unparsed or duplicates) else "MET")
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
    # The breakers are module state: snapshot and restore them, because a
    # "read-only" instrument must not un-trip the vector breaker in whatever
    # process imports it (this tool's own review, P2 — `measure()` already does
    # this for the gate flags). ⛔ NOT `deepcopy`: `_CircuitBreaker` holds a
    # `threading.Lock` (`search_engine.py:167`), so a deepcopy raises
    # `TypeError: cannot pickle '_thread.lock' object` — measured, and it fired
    # the moment a real search had registered a breaker, which is exactly the
    # state this restore exists to protect. The SLOT values are primitives.
    import tortoise.search_engine as _se
    from tortoise.search_engine import (
        VECTOR_MECHANISM_KEY,
        reset_circuit_breakers,
        run_vector_query,
    )

    _SLOTS = ("fail_threshold", "cooldown_seconds", "_fails", "_open_until", "_probing")

    def _snapshot() -> dict[str, dict]:
        return {
            name: {slot: getattr(b, slot) for slot in _SLOTS if hasattr(b, slot)}
            for name, b in (getattr(_se, "_BREAKERS", {}) or {}).items()
        }

    snapshot = _snapshot()

    def _restore() -> None:
        breakers = getattr(_se, "_BREAKERS", None)
        if not isinstance(breakers, dict):
            return
        for name, state in snapshot.items():
            b = breakers.get(name)
            if b is None:
                continue
            for slot, value in state.items():
                # A slot we cannot restore is reported by the state it leaves,
                # not fatal to a measurement.
                with contextlib.suppress(Exception):
                    setattr(b, slot, value)

    out: dict[str, str] = {}

    def _entry(trace):
        return next((e for e in trace if e.get("leg") == "vector"), {})

    try:
        return _probe_mechanisms(out, _entry, run_vector_query,
                                 VECTOR_MECHANISM_KEY, reset_circuit_breakers)
    finally:
        _restore()


def _probe_mechanisms(out, _entry, run_vector_query, VECTOR_MECHANISM_KEY,
                      reset_circuit_breakers) -> dict[str, str]:
    reset_circuit_breakers()
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
#: Case-insensitive, because a real ruling is prose ("V1: decided — Point-only,
#: 2026-11-…") and a spelling-sensitive search is a false negative with no way
#: to correct it but editing this tool (this tool's own review, P2).
V1_DECIDED_MARKERS = ("v1: decided", "v1 is decided", "v1 decided")
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
            if any(m in line.lower() for m in V1_DECIDED_MARKERS):
                decided.append(f"{rel}:{i}: {line.strip()}")
            elif re.search(r"\bOPEN\b|\bopen decision\b|human gate|owner decision",
                           line) or "never spends that decision" in line:
                open_lines.append(f"{rel}:{i}: {line.strip()}")
    notes = [
        f"decided markers found: {len(decided)}",
        "implemented: UNMEASURED(not what a decision record proves — a ruling "
        "creates the follow-up work; this row can only see the record)",
    ]
    notes += [f"open: {ln}" for ln in open_lines[:3]]
    # The bullet is "decided AND implemented": with the implementation half
    # unmeasurable, a record can only carry the row to PARTIAL — never MET — and
    # a surface that still says OPEN holds it there (a doc QUOTING the bullet
    # must not flip the row).
    if not decided:
        verdict = "NOT MET"
    elif open_lines:
        verdict = "PARTIAL"
    else:
        verdict = "PARTIAL"
    return Row(
        bullet=4,
        text=BULLETS[3],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── §12.2a — the distinct-predicate count ──────────────────────────────────


#: The modules a predicate can live in without being a second RULE SET: the
#: embed seam's own module graph, walked rather than listed.
EMBED_PATH_ROOTS: tuple[str, ...] = (
    # NOT `tortoise/embeddings.py`: it is the CALLER, and it imports half the
    # tree, so walking it counted 47 unrelated pattern owners as predicates.
    # A predicate has to be imported BY the gate to be reachable from it.
    "tortoise/embed_gate.py",
    "tortoise/value_gate.py",
)


def _resolve(root: Path, parts: list[str]) -> set[str]:
    """The in-tree module file(s) a dotted import path names."""
    out: set[str] = set()
    if not parts:
        return out
    base = root / "tortoise"
    for i in range(1, len(parts) + 1):
        pkg = base.joinpath(*parts[:i])
        for cand in (pkg.with_suffix(".py"), pkg / "__init__.py"):
            if cand.exists():
                out.add(cand.relative_to(root).as_posix())
    return out


def _import_edges(root: Path, tree, path: Path) -> set[str]:
    """In-tree modules one file imports: the AST, because a regex is not.

    ⛔ Both shapes below are IN THIS TREE and both were missed by a one-line
    `from … import …` regex, each producing `count: 1 / MET` on a tree with two
    predicates (review round 3, P2):

    * `import tortoise.kind_index as ki` (a plain `import` — `kind_classifier.py:204`);
    * `from .pkg import (\n    a,\n    b,\n)` (a parenthesised, multi-line list —
      `api.py:175`, `entities.py:28`, `session_indexer.py:32`, …).
    """
    import ast

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "tortoise":
                    targets |= _resolve(root, parts[1:])
        elif isinstance(node, ast.ImportFrom):
            spec = node.module or ""
            if node.level:  # relative: `from .` / `from ..pkg`
                # ⛔ Resolved against the IMPORTING FILE'S package, not always
                # `tortoise/`: `from .rules import _P` inside
                # `tortoise/gates/__init__.py` names `gates/rules.py`, and
                # resolving it as `tortoise/rules.py` dropped a whole subtree
                # (review round 5, P2).
                pkg_dir = path.relative_to(root).parent
                for _ in range(node.level - 1):
                    pkg_dir = pkg_dir.parent
                base = [x for x in pkg_dir.parts if x != "tortoise"]
                parts = [x for x in spec.split(".") if x]
                targets |= _resolve(root, [*base, *parts]) if parts else set()
                for alias in node.names:
                    targets |= _resolve(root, [*base, *parts, alias.name])
            elif spec == "tortoise" or spec.startswith("tortoise."):
                parts = [x for x in spec.split(".")[1:] if x]
                targets |= _resolve(root, parts)
                for alias in node.names:
                    targets |= _resolve(root, [*parts, alias.name])
    return targets


def embed_path_modules() -> set[str]:
    """Every `tortoise/**.py` reachable from the embed seam by LOCAL imports.

    ⛔ A two-file whitelist (this tool's own review, P1) is a search that can
    only find what it already names: a THIRD module owning its own patterns —
    the drift §12.2a forbids — measured as drift-free. This walks the import
    graph instead, so a new `embed_rules.py` imported anywhere on the embed path
    is counted the moment it exists.
    """
    return _embed_path_modules(REPO_ROOT)


@functools.lru_cache(maxsize=8)
def _embed_path_modules(root: Path) -> frozenset[str]:
    edges: dict[str, set[str]] = {}
    for path in sorted((root / "tortoise").rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        mod = path.relative_to(root).as_posix()
        targets: set[str] = _import_edges(root, tree, path)
        edges[mod] = targets
    seen: set[str] = set()
    queue = [r for r in EMBED_PATH_ROOTS if (root / r).exists()]
    while queue:
        r = queue.pop()
        if r in seen:
            continue
        seen.add(r)
        queue.extend(edges.get(r, ()))
    return frozenset(seen)


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
    for rel in sorted(embed_path_modules()):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        # AST, not a text pattern: an ALIASED import (`import re as _re`) is
        # still a pattern, and a substring scan missed exactly that in this
        # tool's own mutation check — the falsifier PASSED on mutated code,
        # which is the one failure a falsifier must not have.
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # ANY module-scope expression that calls `.compile(` owns a pattern —
        # a single assignment AND a collection (`_PATS = [re.compile(…), …]`),
        # which is the most natural shape for "a second pattern LIST" and read
        # `count: 1 / MET` when only the single-assignment form was matched.
        # ⚠️ Deliberate asymmetry, documented: an unrelated pattern-owning
        # helper imported by the gate also counts as an owner (a false DRIFT).
        # Over-reporting drift is the safe direction here; a false `MET` is the
        # failure this whole instrument exists to remove.
        module_level = [n for n in tree.body]
        for node in (m for top in module_level for m in ast.walk(top)
                     if top is not None):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "compile"
                for c in ast.walk(node)
            ):
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
                  graph_names: list[str] | None = None,
                  uri_graph_name: str | None = None) -> dict:
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
    # ⛔ The handle is selected by `graph_name` (the name this row will carry),
    # never by the URI path alone: measuring one graph and labelling another is
    # how a census reads as measured without being reproducible (this tool's own
    # review, P1 — it printed `Point stored=0` for a graph holding 50 vectors).
    # `uri_graph_name` is recorded so a reader can see both.
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

    # ⛔ `{indexed} | {served}` is NOT the population: a label that stores
    # vectors, has no index AND no query path is the worst class this census is
    # for, and drawing the population from the two things it compares omits it
    # by construction (this tool's own review, P1 — a `:Chunk` with 2 stored
    # embeddings never appeared). The graph's own labels are the population.
    indexed: dict[str, list[dict]] = {}
    #: (label-or-None, reason) — matched by LABEL, so a short row (`['Point']`)
    #: is still attached to its label instead of being lost in a repr.
    unreadable: list[tuple[str | None, str]] = []
    for row in rows or []:
        try:
            label, types = row[0], row[2]
        except (IndexError, TypeError):
            row_label = row[0] if isinstance(row, (list, tuple)) and row else None
            unreadable.append((row_label, f"short row {repr(row)[:60]}"))
            continue
        if not isinstance(types, dict):
            unreadable.append((label, f"types cell is {type(types).__name__}"))
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
                # ⛔ These counters describe the row's FULLTEXT bookkeeping
                # (measured: docTableSize / docTrieSize / invertedSize / …),
                # NOT the vector index — a live HNSW holding 76,800 B of
                # payload reported 5,150 B, all of it fulltext. Reported under
                # their real name so the figure cannot be read as a vector size.
                engine_bytes = {k: v for k, v in stats.items()
                                if isinstance(v, int) and k.endswith("Size")} or None
                # A LIST per label: a label with vector indexes on two fields
                # must not have one silently overwrite the other.
                indexed.setdefault(label, []).append({
                    "field": fld,
                    "dimension": dim if dim is not None else "UNMEASURED(no dimension in options)",
                    "engine_catalogue_counters_FULLTEXT_NOT_VECTOR": engine_bytes
                    if engine_bytes is not None
                    else "UNMEASURED(no size counters in the catalogue row)",
                })

    try:
        graph_labels = [r[0] for r in (graph.query("CALL db.labels()").result_set or [])]
        population_source = "CALL db.labels()"
    except Exception as e:
        # ⛔ NOT a narrowed fallback to `indexed | served`: that is the very
        # population bug this census exists to remove, and it silently drops
        # the stored-no-index-no-query-path class (review round 3, P2). A
        # population that could not be read is a measurement that did not
        # happen.
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": f"UNMEASURED(population read failed: "
                             f"{type(e).__name__}: {e})",
                "population": "UNMEASURED(CALL db.labels() failed)",
                "command": CENSUS_COMMAND, "uri_path_graph": uri_graph_name,
                "unreadable_catalogue_rows": unreadable}

    labels: dict[str, dict] = {}
    for label in sorted(set(graph_labels) | set(indexed) | served_labels()):
        try:
            res = graph.query(
                f"MATCH (n:`{label}`) WHERE n.embedding IS NOT NULL RETURN count(n)"
            ).result_set
            stored = int(res[0][0])
        except Exception as e:
            stored = f"UNMEASURED({type(e).__name__}: {e})"
        unreadable_here = [f"{lab}: {why}" for lab, why in unreadable if lab == label]
        found = indexed.get(label) or []
        # ⛔ An index on the LABEL is not an index on the COLUMN counted here.
        # `field` was already read and used to be ignored: a `(Source:vec)`
        # index made a `Source.embedding` vector report `indexed=True`
        # (this tool's own review, P1 — measured on a live engine).
        covering = [ix for ix in found if ix["field"] == "embedding"]
        idx = covering[0] if covering else (found[0] if found else None)
        dim = idx["dimension"] if covering and isinstance(idx["dimension"], int) else None
        # The stored WIDTH is probed once, before either column is written:
        # `unindexed` must not claim "covered" when the index and the stored
        # vectors disagree on dimension (the `#4280` class), and the payload is
        # derived from the same number.
        width = None
        if isinstance(stored, int) and stored > 0:
            try:
                # `size(n.embedding)` is REJECTED on a vecf32 column, so the
                # vector is fetched and measured here (measured: every payload
                # read UNMEASURED before this).
                probe = graph.query(
                    f"MATCH (n:`{label}`) WHERE n.embedding IS NOT NULL "
                    f"RETURN n.embedding LIMIT 1"
                ).result_set
                if probe and probe[0] and probe[0][0] is not None:
                    width = len(probe[0][0])
            except Exception:
                width = None
        if isinstance(stored, str):
            unindexed = "UNMEASURED(stored count unreadable)"
        elif covering and width is not None and dim is not None and width != dim:
            unindexed = f"UNMEASURED(index dim {dim} != stored width {width})"
        elif covering:
            unindexed = 0
        else:
            unindexed = stored
        if isinstance(stored, int) and stored == 0:
            payload = {"derived_payload_B": 0, "how": "no stored vectors on this label"}
        elif dim is None:
            payload = {"derived_payload_B": "UNMEASURED(no VECTOR index on "
                                            "`embedding` — nothing to derive from)",
                       "how": "needs a covering index dimension"}
        elif width is None:
            payload = {"derived_payload_B": "UNMEASURED(stored width not probeable)",
                       "how": "needs a width probe agreeing with the index"}
        elif width != dim:
            payload = {"derived_payload_B": "UNMEASURED(stored width "
                                            f"{width} != index dimension {dim})",
                       "how": "the index and the stored vectors disagree (#4280 class)"}
        else:
            payload = {"derived_payload_B": stored * dim * 4,
                       "how": f"stored {stored} x {dim} dims x 4 B (vecf32), "
                              f"probed width {width} agrees"}
        labels[label] = {
            "stored": stored,
            # An unreadable catalogue row is NOT "this label has no index": it
            # is a row we could not read, and the column says so.
            "indexed": ("UNMEASURED(catalogue row unreadable: "
                        f"{unreadable_here[0]})" if unreadable_here
                        else bool(covering)),
            "unindexed": (f"UNMEASURED(catalogue row unreadable: {unreadable_here[0]})"
                          if unreadable_here else unindexed),
            "vector_indexes_on_label": found or "none",
            "index": idx or "none",
            "payload": payload,
        }
    return {"graph": graph_name, "at": at, "labels": labels,
            "catalogue": "CALL db.indexes()", "population": population_source,
            "command": CENSUS_COMMAND,
            "uri_path_graph": uri_graph_name,
            "unreadable_catalogue_rows": unreadable}


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
        # Exit 2 = "the instrument could not measure" (the docstring's
        # contract). ⛔ A NON-INT SystemExit argument exits **1**, so the message
        # is printed and the code passed explicitly — passing the string made
        # the documented `2` a contract the code never implemented, and a
        # caller could not tell "could not measure" from "a row is NOT MET".
        print(
            "census: an embedded/absent engine has no index API — pass a "
            "docker:// or bolt:// URI (embedded would report indexed=False for "
            "every label, which is NOT the same fact)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    from falkordb import FalkorDB

    db = FalkorDB(host=u.hostname, port=u.port or 6379, password=u.password or "")
    try:
        names = list(db.list_graphs() or [])
    except Exception:
        names = None
    return db, names


# ── The report ─────────────────────────────────────────────────────────────


def measure() -> dict:
    """Run the whole instrument. Clears the gate flags first — a lane's own env
    must not be able to make a default-off gate read as enforced."""
    # Snapshot and RESTORE: an import of a "read-only" instrument must not
    # leave the rest of the caller's process with both gates disabled.
    _flags = {v: os.environ.get(v) for v in ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE")}
    for var in _flags:
        os.environ.pop(var, None)
    try:
        rows = _measure_rows()
    finally:
        for var, value in _flags.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
    return {
        "issue": 10465,
        "epic": 5090,
        "head": _head(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": [asdict(r) for r in rows],
        "predicates": count_embed_predicates(),
        "seams": _write_seams_that_embed(),
    }


def _measure_rows() -> list[Row]:
    rows = [score_rule_bullet(), score_path_bullet(),
            score_fallback_bullet(), score_v1_bullet()]
    return rows


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
        # The other route to the documented `2`: a gate module the instrument
        # SCORES that will not import. Without this the traceback exits 1, which
        # is indistinguishable from a verdict failure.
        print(f"could not measure: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    unmeasured = False
    if args.census:
        if not args.uri:
            report["census"] = {"error": "UNMEASURED(no --uri and no TORTOISE_DB_URI)"}
            unmeasured = True
        else:
            graph_name = args.graph or (args.uri.rstrip("/").rsplit("/", 1)[-1])
            db, names = _connect(args.uri)
            uri_name = args.uri.rstrip("/").rsplit("/", 1)[-1]
            # ⛔ The handle is selected by the NAME the report will carry. Using
            # the URI path while LABELLING the row `--graph` attributes one
            # graph's numbers to another (this tool's own review, P1: it printed
            # `Point stored=0` for a graph holding 50 vectors).
            graph = db.select_graph(graph_name)
            report["census"] = vector_census(
                graph, graph_name, graph_names=names,
                uri_graph_name=uri_name)
            census = report["census"]
            unmeasured = unmeasured or str(census.get("catalogue", "")).startswith(
                "UNMEASURED") or "error" in census

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 2 if unmeasured else 0

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
        # A figure quoted into an epic body must carry the way it was produced
        # (`#10465` target 1) — the docstring says so, and the text path was
        # the one place that did not.
        print(f"  command: {c.get('command', CENSUS_COMMAND)}")
        for label, d in (c.get("labels") or {}).items():
            print(f"  {label:10s} stored={d['stored']!s:12s} "
                  f"indexed={d['indexed']!s:6s} unindexed={d['unindexed']!s:12s} "
                  f"payload={d['payload']['derived_payload_B']}")
        if "error" in c:
            print(f"  {c['error']}")
        if c.get("population"):
            print(f"  population: {c['population']}")
        if c.get("unreadable_catalogue_rows"):
            print("  unreadable catalogue rows: "
                  + "; ".join(c["unreadable_catalogue_rows"][:3]))
    return 2 if unmeasured else 0


def cli(argv: list[str] | None = None) -> int:
    """`main`, with the documented exit contract enforced for every route.

    The docstring promises `2` when the instrument could not measure. A raised
    `ImportError`, a row scorer that throws, or an unreachable engine must all
    land there — not on `1` with a traceback, which a caller cannot tell from a
    red row.
    """
    try:
        return main(argv)
    except SystemExit as e:  # `_connect` refuses an embedded engine this way
        if e.code in (0, None):
            return 0
        # `SystemExit("msg")` carries a STRING code — `int()` on it raised
        # out of this very handler and exited 1 (review round 5, P3).
        return e.code if isinstance(e.code, int) else 2
    except Exception as e:
        print(f"dod-5090-probe: UNMEASURED({type(e).__name__}: {e})",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
