"""The production home for *"this embedding writer is legitimately exempt"* (#11085).

**Why this module exists.** `#10541` classified the embedding-writer population — 6 recognised
producers, 9 legitimately exempt, **0 bypasses** — but the only place that classification lived was
`tools/dod_5090_probe.py::WRITER_CENSUS`, which is a **read-only instrument** and says so at its own
`:1264` (*"the census CLASSIFIES the unresolved set; it does not license moving this verdict"*). An
audit instrument is not a product decision: the answer to *"is this writer allowed to store a vector
outside the gate?"* has to be resolvable **at runtime, from `tortoise/`**, or arming the EMBED gate
(`#11086`) would let an undeclared writer bypass silently while the gap was merely *labelled*.

**The reading this applies — (b), decided on `#11085`.** `#5090`'s DoD row 1 has two defensible
readings: **(a)** a *classified* population is enough; **(b)** every writer must route through the
gate **or** be a **named, accepted exception in the product**. This module is (b). Under (a) the
metric goes green while a writer can still bypass the rule while labelled, which is the hole the
rule exists to close.

**What it is NOT.**

* **Not a second rule set.** It speaks to *writer classification* — a different axis from *what earns
  a vector*, which is `value_gate.RULES` (`embed_gate.RULE_SET is value_gate.RULES`). This module
  holds no pattern, no predicate and no rule id, and it imports **nothing from `tortoise`** — see
  "leaf" below.
* **Not a licence to change a writer.** It declares what each writer already does. Nothing consults
  it to decide a write yet; that is `#11086`'s seam.
* **Not an audit log.** The evidence for each entry is the seam, cited in `reason` — never a line
  number, for the reason `tools/dod_5090_probe.py` learned the expensive way. Every citation is
  **re-derived at HEAD**, in the direction that can actually rot: each `reason` must name a
  `module::symbol` the tree still DEFINES, and — where the census has a row — the row's own
  `CENSUS_EVIDENCE` needles must still be inside that symbol
  (`tests/test_embed_writer_register.py::test_every_declaration_cites_a_seam_and_an_owning_issue`,
  `::test_the_instrument_and_the_product_declare_the_same_writers`).
  ⚠️ Census evidence and this register are **not** one population:
  `tortoise.session_indexer::main` is the one entry with no census row (it is the instrument's ONE
  `ungated` write path, carried in the census's row-1 prose), so its citation is re-derived by the
  symbol test alone. A reader who wants "re-derivable by `CENSUS_EVIDENCE`" to cover 100% of this
  file should read it as "by the two tests that re-derive it", which is what they do.

**Discovery, and the edge of the guard.** The guard's population comes from
`tools/dod_5090_probe.py::embedding_writers()`, so the guard is exactly as wide as that discovery —
no wider. Two write shapes are worth naming, because the difference is the difference between a
claim and a slogan:

* **Container keys are SEEN** (`record["embedding"] = …`). This shape was invisible to the
  instrument's clause regex until `#11274`, so a writer using it added nothing to the population and
  this register's guard stayed green over it; discovery now decides it on the AST, and the four
  live instances it had been missing (`api::add_point`, `consistency::_graph_fingerprint`,
  `sdk::_index_gate_read`, `sdk::_write_capture_turns`) are declared here.
* **Map-merges are NOT** (`SET n += $p`, `probe.UNDECIDABLE_WRITE_SHAPES`). Whether the merged map
  carries a vector is data flow, not syntax, and ~60 functions merge a props map. So the honest
  statement is not "no writer can bypass silently" but: **every writer discovery finds outside the
  gate is declared, and the shapes discovery cannot decide are named rather than assumed absent** —
  which is why `sdk::_update_entity`, the live map-merge instance, carries the NAMED `#4094`
  exemption instead of being read as "no write at all".

**Leaf, and it must stay one.** `04-plan.md` §5: a leaf must not live under `tortoise/projection/`,
whose `__init__` is the head of the `projection → pack_state → sdk → projection` cycle where a
module-scope read of the config constants is a **reproduced `ImportError`**
(`projection/__init__.py:1466-1481`). This module imports only the standard library, so importing it
can never enter that cycle — pinned in a subprocess by
`test_the_register_imports_without_entering_the_projection_cycle`.

**No `bypass` class — by construction.** `class` is one of `recognised_producer` / `exempt`. A writer
that is neither is **unclassified**, and :func:`classify` *raises* rather than answering. A
declaration that *asks* for `"bypass"` raises too: a bypass is a defect, never a declaration. The
probe's third class exists only so the instrument can *name* one if it ever finds it
(`bypass=0` measured); it is deliberately not an admissible value here.

**Vocabulary, and one finding.** `#11085`'s body says `gated`; `04-plan.md` §6.2 — higher precedence,
and the vocabulary `tools/dod_5090_probe.py` already uses — says `recognised_producer`. The plan's
word is used here because `gated` would be a **false claim**: the probe measures that *none* of these
six reaches `encode_for_row` (the gate's seam); they reach the canonical clause builder
(`set_embedding_clause`) or the `#5008` session producer. `recognised_producer` is exactly the
claim the seam supports. Recorded on `#11085` rather than silently reconciled.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: A writer whose vector reaches a **recognised** producer — the canonical clause builder
#: (`tortoise/vector_write.py::set_embedding_clause`) or the `#5008` session producer
#: (`compute_session_embedding`, via `SDK._session_embedding`). ⚠️ It does **not** mean
#: `encode_for_row`: none of these six calls it, which is why `gated` is not the class name.
RECOGNISED_PRODUCER = "recognised_producer"

#: A function that is not a producer of a STORED vector at all: it renders the clause, forwards one,
#: journals one, reads one, sets a FLAG or emits a DIAGNOSTIC, or merely MENTIONS one in a
#: comment/docstring — or it stores one under a **named** owner exemption (`#4094`, `#5008`).
EXEMPT = "exempt"

#: ⛔ The admissible classes. A `bypass` is a DEFECT, not a class: it is not here, and
#: :func:`load` refuses a declaration that asks for it.
CLASSES: tuple[str, ...] = (RECOGNISED_PRODUCER, EXEMPT)

#: An owning issue is a GitHub issue reference — the issue that OWNS the exemption's reason, not
#: merely the one that recorded it (see the per-entry `owner_issue` notes).
_OWNER_ISSUE = re.compile(r"^#\d+$")


class UnclassifiedWriterKey(LookupError):
    """A writer nothing declares a class for — the refusal, never a default.

    `#5090`'s rule can only be *"no silent bypass"* if the lookup has no fallback: a writer the
    register does not know is an **unnamed** writer, and the caller must see that rather than a
    permissive answer. Fixed by DECLARING it (:data:`_DECLARATIONS`), never by widening the class set.
    """


class DuplicateWriterDeclaration(ValueError):
    """Two declarations for one `module::symbol` key — the later must not silently win.

    A `dict` literal would let the second entry overwrite the first with no error, which is how a
    stale declaration survives a rewrite. The register is built from an ordered tuple of pairs so the
    collision is *observable*, and :func:`load` raises on it.
    """


class UnknownWriterClass(ValueError):
    """A class outside :data:`CLASSES` — a typo, a retired name, or a requested `bypass`."""


@dataclass(frozen=True)
class WriterClass:
    """One declaration: what the writer is, why, and which issue owns that reading.

    ``reason`` cites the **seam** (the symbol and the shape that decides the class), never a line
    number: `tools/dod_5090_probe.py`'s own census learned that the expensive way — a `path:LINE`
    citation rotted silently when `sdk.py` grew ~43 lines, while a symbol is stable under an
    unrelated edit. That module's ``CENSUS_EVIDENCE`` carries the executable form for the rows it
    has (not all of them: `tortoise.session_indexer::main` has no census row), and this module's
    own test re-derives the symbol every entry cites.
    """

    cls: str
    reason: str
    owner_issue: str


def load(
    declarations: Iterable[tuple[str, WriterClass]],
) -> Mapping[str, WriterClass]:
    """Freeze ``declarations`` into a read-only lookup, refusing anything undeclarable.

    Raises:
        DuplicateWriterDeclaration: the same ``module::symbol`` key twice.
        UnknownWriterClass: a class outside :data:`CLASSES` (including ``"bypass"``).
        ValueError: a key that is not ``module::symbol``, an empty ``reason``, or an
            ``owner_issue`` that is not an issue reference.

    Public because the falsifiers need to drive a malformed register — a guard that can only be
    checked against the tree's current content cannot be checked at all once the content is right.
    """
    frozen: dict[str, WriterClass] = {}
    for key, record in declarations:
        module, _, symbol = key.partition("::")
        if not module or not symbol:
            raise ValueError(
                f"{key!r} is not a `module::symbol` key — the register is keyed the same way "
                "`tools/dod_5090_probe.py` keys its census, so the instrument and the product "
                "cannot disagree about WHICH writer a row is about"
            )
        if key in frozen:
            raise DuplicateWriterDeclaration(
                f"{key!r} is declared twice — a duplicated declaration must not silently win; "
                "delete the stale row or correct the reason"
            )
        if record.cls not in CLASSES:
            raise UnknownWriterClass(
                f"{key!r} declares class {record.cls!r}, which is not one of {CLASSES} — "
                "`bypass` is a defect the instrument can NAME, never a declaration a writer can hold"
            )
        if not record.reason.strip():
            raise ValueError(f"{key!r} carries no reason — a class without a seam is a claim")
        if not _OWNER_ISSUE.match(record.owner_issue):
            raise ValueError(
                f"{key!r} names owner_issue {record.owner_issue!r}, which is not an issue "
                "reference like '#5008'"
            )
        frozen[key] = record
    if not frozen:
        raise ValueError("an empty register declares nothing and would classify nobody")
    return MappingProxyType(frozen)


#: ── The register ────────────────────────────────────────────────────────────────────────────────
#: `owner_issue` is the issue that OWNS the reason:
#:   * `#5008` — the session-chain exemption, stated by the seam that built it;
#:   * `#4094` — the map-merge exemption (`SET n += $p` keeps a caller-supplied `embedding`);
#:   * `#10541` — a *reading of the code* the census recorded (this function is not a producer);
#:   * `#11085` — the three writers that carried **no declared exemption in the product at all**
#:     before this register existed. That is this issue's whole job: reading (b) says an exception
#:     must be NAMED in the product, so the naming is the acceptance — not a re-litigation of what
#:     the writer does, which the probe already read at its seam.
_DECLARATIONS: tuple[tuple[str, WriterClass], ...] = (
    # ── The three that had NO declared exemption (#11085's reason for existing) ───────────────
    (
        "tortoise.hosted_backup::restore_graph",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/hosted_backup.py::restore_graph` writes through the canonical clause "
            "builder (`set_embedding_clause(\"n\", \"r.v\")`) from the BACKED-UP row — the vector "
            "was computed upstream by the writer that produced the backup, so this function "
            "restores one rather than producing one",
            "#11085",
        ),
    ),
    (
        "tortoise.projection.entities::_event_plain_merge",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/projection/entities.py::_event_plain_merge` carries the canonical clause — "
            "`set_embedding_clause(\"e\", preserve_on_null=False)` / "
            "`set_embedding_clause(\"e\")` — over an event whose vector the `#5008` session chain "
            "computed; the fold stores the carrier value",
            "#11085",
        ),
    ),
    (
        "tortoise.projection::_rebuild_all_replay",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/projection/__init__.py::_rebuild_all_replay` calls "
            "`set_embedding_clause(\"n\", \"$emb\")`, the canonical clause, from the replayed "
            "journal the `#5008` chain wrote. ⚠️ It also holds two OTHER embedding statements this "
            "class does not rest on — `SET n.embedding = NULL` (a wipe) and a DIRECT "
            "`n.embedding = $emb` that bypasses the builder. Recorded, not fixed: changing a "
            "writer's behaviour is out of `#11085`'s scope",
            "#11085",
        ),
    ),
    # ── The #5008 session chain ───────────────────────────────────────────────────────────────
    (
        "tortoise.sdk::_session_event_write",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/sdk.py::_session_event_write` — `self._session_embedding(...)` → "
            "`compute_session_embedding` (the documented `#5008` exemption) + "
            "`set_embedding_clause`",
            "#5008",
        ),
    ),
    (
        "tortoise.sdk::ingest_corpus",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/sdk.py::ingest_corpus` — `self._session_embedding(...)` + "
            "`set_embedding_clause(\"e\")`, the same canonical chain",
            "#5008",
        ),
    ),
    (
        "tortoise.sdk::_embedding_repair_attempt",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/sdk.py::_embedding_repair_attempt` DOES compute and store — "
            "`embedding = self._session_embedding(...)` and `set_embedding_clause(\"e\")` in its "
            "own body. It is both a producer on the `#5008` chain and a repair by ROLE, which is "
            "why `exempt` (the #10541 wording for the other nine) would contradict the class the "
            "chain's own seam carries",
            "#5008",
        ),
    ),
    (
        "tortoise.sdk::_write_capture_turns",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_write_capture_turns` assigns `_turn_snapshot[\"embedding\"] = "
            "turn_embs[i] …` — the vector the `#5008` chain computed, or the already-stored "
            "one; the body computes none (no `encode_for_row`, no `set_embedding_clause`). "
            "Exempt as the SANCTIONED TURN-RECORD CARRIER, the same shape as "
            "`tortoise.sdk::_journal_event_raw_write`: its record is what the rebuild reads the "
            "vector back from (`#8488`'s enriched text rides the same snapshot), not a store "
            "reached around the gate",
            "#5008",
        ),
    ),
    # ── The one writer that was exempt only in the instrument's PROSE ─────────────────────────
    (
        "tortoise.session_indexer::main",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/session_indexer.py::main` computes `embedding = "
            "compute_session_embedding(...)` (the `#5008` session producer, defined in that same "
            "module) and hands it to `sdk._event_raw_write_door(...)` — it never reaches the "
            "gated seam `encode_for_row`, which is why the instrument reports it as the ONE write "
            "path outside the gate. Until this register it was exempt only as a PROSE note in "
            "`tools/dod_5090_probe.py`'s row 1 (*\"the session/Turn writers #5008 declares as "
            "exempt\"*) — a declaration in a read-only instrument, which is exactly the shape "
            "`#11085` exists to replace",
            "#5008",
        ),
    ),
    # ── Writers discovered when the filter widened to CONTAINER KEYS (#11274) ────────────────
    #: `_EMBED_WRITE` reads `name.embedding =` clauses; `record["embedding"] = …` puts a vector
    #: into a dict instead, so these were invisible to the instrument — and an invisible writer
    #: is one this register's guard could stay green over. Discovery decides the shape on the AST
    #: now (`tools/dod_5090_probe.py::_writes_an_embedding`), and each entry names what the write
    #: actually is.
    (
        "tortoise.api::add_point",
        WriterClass(
            EXEMPT,
            "`tortoise/api.py::add_point` writes `p[\"embedding\"] = fields[\"embedding\"]` — a "
            "CALLER-supplied vector OVER the server one, in the journal payload. That is the "
            "`#5004` carrier contract, not a producer: the key stays PRESENT so an explicit "
            "`None` means 'this id has no vector', and a foreign vector is restored verbatim "
            "with NO attestation. The server vector is stamped by `tortoise.api::_point` (its "
            "own entry); the SDK surface refuses a caller-supplied `embedding` outright, so "
            "this is reachable only from a direct `EventAPI` caller",
            "#5004",
        ),
    ),
    (
        "tortoise.consistency::_graph_fingerprint",
        WriterClass(
            EXEMPT,
            "`tortoise/consistency.py::_graph_fingerprint` writes "
            "`view[\"embedding\"] = props[\"embedding\"]` — a vector READ OFF THE GRAPH into a "
            "comparison view dict. The target is an in-memory view, never a stored vector: a "
            "READ, the same direction as `tortoise.record_read::turn_prior_vectors`",
            "#11085",
        ),
    ),
    (
        "tortoise.sdk::_index_gate_read",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_index_gate_read` writes `g[\"embedding\"] = rows[0][0]` off "
            "`RETURN e.embedding` — the fast-path ROUTER READ. Every statement the body issues "
            "is a `MATCH … RETURN` and the container it fills is its own `g` dict, so it reads "
            "a vector rather than writing one",
            "#11085",
        ),
    ),
    # ── Named exemptions that DO store (a map-merge the instrument cannot see) ────────────────
    (
        "tortoise.sdk::_update_entity",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_update_entity` — the instrument's match is a COMMENT, but the "
            "function DOES store: `point_props = dict(props)` keeps a caller-supplied `embedding` "
            "through the map-merge (`SET n += $p`), a shape that is DATA FLOW rather than syntax "
            "(`tools/dod_5090_probe.py::UNDECIDABLE_WRITE_SHAPES`), so discovery cannot decide "
            "it. Exempt on the NAMED `#4094` exemption, NOT on absence of a write",
            "#4094",
        ),
    ),
    # ── Writers that store NO vector: the instrument's false positives and its passthroughs ──
    (
        "tortoise.consistency::_compare_views",
        WriterClass(
            EXEMPT,
            "`tortoise/consistency.py::_compare_views` issues no write; the only WRITE-SHAPED "
            "text is a COMMENT quoting what `replay(journal)` cannot produce "
            "(`n.embedding=vecf32($embedding)`). It reads `embedding` off both sides and APPENDS "
            "it to a divergence field list",
            "#10541",
        ),
    ),
    (
        "tortoise.projection::_apply_one",
        WriterClass(
            EXEMPT,
            "`tortoise/projection/__init__.py::_apply_one` — the match is COMMENT PROSE "
            "(`#8488`/`#10523`'s `#   2. a CALLER-VECTOR write`), and the fold states in its own "
            "words that it never SETS a real value. The vector write belongs to the CARRIER "
            "(`_upsert_point_props`); the only property this function touches is the TEXT prop — "
            "`p.pop(\"embeddingText\", None)` — on an in-memory dict",
            "#10541",
        ),
    ),
    (
        "tortoise.projection::_prewipe_synthetic_write_scope",
        WriterClass(
            EXEMPT,
            "`tortoise/projection/__init__.py::_prewipe_synthetic_write_scope` — the match is "
            "inside the scope's DOCSTRING and the statement NULLs the vector "
            "(`SET n.embedding = NULL`); a wipe is not a producer",
            "#10541",
        ),
    ),
    (
        "tortoise.record_read::turn_prior_vectors",
        WriterClass(
            EXEMPT,
            "`tortoise/record_read.py::turn_prior_vectors` — `embedding=row[2]` populates a "
            "dataclass from a returned row: a READ, with no write clause",
            "#10541",
        ),
    ),
    (
        "tortoise.sdk::_event_raw_write_door",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_event_raw_write_door` — a TRANSPORT door: it forwards an "
            "already-computed `embedding` into `query_params` and executes inside "
            "`_live_write_scope` (the §17.1 sanctioned live-write lane); it never computes one",
            "#10541",
        ),
    ),
    (
        "tortoise.sdk::_index_process_unit",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_index_process_unit` — the match is the DIAGNOSTIC STRING "
            "`\"error\": \"indexed with embedding=None \"`, not a write clause. Its store is "
            "DELEGATED to `_session_event_write`, and its two repair sites to "
            "`_embedding_repair_attempt`; every callee is a recognised producer, so this function "
            "produces no vector of its own",
            "#10541",
        ),
    ),
    (
        "tortoise.sdk::_journal_event_raw_write",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_journal_event_raw_write` — the CLAUSE match is the SIGNATURE "
            "DEFAULT (`embedding=None`); the store that actually matters is the dict-key form "
            "`record[\"embedding\"] = embedding`, which discovery reads on the AST since `#11274` "
            "rather than through `_EMBED_WRITE`. It stores no LIVE graph vector, but its journal "
            "IS the sanctioned replay carrier `_upsert_event` consumes — exempt as a carrier, NOT "
            "because no store is reachable through it",
            "#10541",
        ),
    ),
    (
        "tortoise.vector_write::set_embedding_clause",
        WriterClass(
            EXEMPT,
            "`tortoise/vector_write.py::set_embedding_clause` — the clause BUILDER itself; it "
            "renders the `<alias>.embedding = …` clause and stores no vector. Every "
            "`recognised_producer` entry above reaches a vector THROUGH this function",
            "#10541",
        ),
    ),
)

#: The register, frozen. `module::symbol` → :class:`WriterClass`; the same key the instrument uses.
#: ⚠️ Its population is *"every writer whose vector the gate does not see"* — the instrument's
#: ``unresolved_write`` set PLUS its one ``ungated`` write path
#: (`tortoise.session_indexer::main`). A ``gated`` writer needs no declaration: the gate decides it.
#: `tests/test_embed_writer_register.py::test_every_writer_the_instrument_finds_outside_the_gate_is_declared`
#: holds that population in both directions.
EMBED_WRITER_CLASSES: Mapping[str, WriterClass] = load(_DECLARATIONS)


def classify(writer: str) -> WriterClass:
    """The declared class for ``writer``, or raise :class:`UnclassifiedWriterKey`.

    ⛔ No default. An undeclared writer is a refusal, because a permissive fallback would make
    *"no silent bypass"* a claim the code cannot keep: the caller (and `#11086`'s gate) must see the
    hole, not inherit a class nobody chose.
    """
    try:
        return EMBED_WRITER_CLASSES[writer]
    except KeyError:
        raise UnclassifiedWriterKey(
            f"{writer!r} carries no declaration in `tortoise/embed_writer_register.py` — an "
            "unclassified writer is a REFUSAL, not a default: declare it (class, reason, owning "
            "issue) or stop it storing a vector outside the gate"
        ) from None


def is_declared(writer: str) -> bool:
    """Whether ``writer`` carries a declaration. Says *whether*, never *what* — use :func:`classify`."""
    return writer in EMBED_WRITER_CLASSES


def undeclared(writers: Iterable[str]) -> tuple[str, ...]:
    """Every writer in ``writers`` the register does not declare, sorted.

    The enumeration the gate (`#11086`) and the falsifiers share, so *"is anything undeclared?"* is
    answered by one function rather than by each caller's own set arithmetic.
    """
    return tuple(sorted(set(writers) - set(EMBED_WRITER_CLASSES)))
