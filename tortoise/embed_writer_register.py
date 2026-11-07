"""The production home for *"this embedding writer is legitimately exempt"* (#11085).

**Why this module exists.** `#10541` classified the embedding-writer population (the class counts
move as writers are declared, so read `WRITER_CENSUS` rather than a number restated here) but the
only place that classification lived was
`tools/dod_5090_probe.py::WRITER_CENSUS`, which is a **read-only instrument** and says so in its own
words (*"the census CLASSIFIES the unresolved set; it does not license moving this verdict"*). An
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
  holds no *rule* pattern, no predicate and no rule id — its only regex is the `owner_issue` format
  check (`_OWNER_ISSUE`) — and it imports **nothing from `tortoise`** — see "leaf" below.
* **Not a licence to change a writer.** It declares what each writer already does. Nothing consults
  it to decide a write yet; that is `#11086`'s seam.
* **Not an audit log.** The evidence for each entry is the seam, cited in `reason` — never a line
  number, for the reason `tools/dod_5090_probe.py` learned the expensive way. Every citation is
  **re-derived at HEAD**, in the direction that can actually rot: each `reason` must name a
  `module::symbol` the tree still DEFINES, and — where the census has a row — the row's own
  `CENSUS_EVIDENCE` needles must still be inside that symbol. The halves are re-derived by
  DIFFERENT tests and no single one covers all of them: the `module::symbol` half by
  `tests/test_embed_writer_register.py::test_every_declaration_cites_a_seam_and_an_owning_issue`,
  the class AGREEMENT half by `::test_the_instrument_and_the_product_declare_the_same_writers`
  (which reads no `CENSUS_EVIDENCE` at all), and the `CENSUS_EVIDENCE` needles by
  `tests/test_dod_5090_probe.py::test_every_census_citation_re_derives_at_this_head` (via
  `probe.census_evidence_violations()`).
  ⚠️ Census evidence and this register are **not** one population:
  `tortoise.session_indexer::main` is the one entry with no census row (it is the instrument's ONE
  `ungated` write path, carried in the census's row-1 prose), so its citation is re-derived by the
  symbol test alone. A reader who wants "re-derivable by `CENSUS_EVIDENCE`" to cover 100% of this
  file should read it as "by the three tests that re-derive it, one half each", which is what they
  do.

**Discovery, and the edge of the guard.** The guard's population comes from
`tools/dod_5090_probe.py::embedding_writers()`, so the guard is exactly as wide as that discovery —
no wider. Two write shapes are worth naming, because the difference is the difference between a
claim and a slogan:

* **Container keys are SEEN — the LITERAL-ASSIGNMENT form** (`record["embedding"] = …`), for every
  module discovery walks AND inside a `def`: the walk visits `def`s only
  (`probe._embedding_writers`), so a container key at MODULE or CLASS scope is ABSENT no matter which
  module it sits in — pinned as `INVISIBLE_SHAPES`' `module_scope_container_key` and
  `class_scope_container_key`. This shape was invisible to the
  instrument's clause regex until `#11274`, so a writer carrying NOTHING ELSE the instrument
  matches — neither the `_EMBED_WRITE` regex nor a recognised producer call (`encode_for_row`,
  `encode_for_store`, `compute_session_embedding`) — added nothing to the population and this
  register's guard stayed green over it; discovery now decides the shape on the AST. The four live
  instances it had been missing (`api::add_point`, `consistency::_graph_fingerprint`,
  `sdk::_index_gate_read`, `sdk::_write_capture_turns`) are declared here. ⚠️ Membership is NOT
  evidence the shape was seen: a writer can be in this population for another reason. 
  `sdk::_journal_event_raw_write` holds the shape (`record["embedding"] = …`) and is ALSO matched by
  `_EMBED_WRITE` on the `embedding=None` in its SIGNATURE, and — because the `encode_for_row` branch
  assigns `gated` WITHOUT consulting the write predicate OR the shape — the population ALSO takes
  every writer that reaches the seam by CALL: `api::_point`, `projection::_revise_point`,
  `projection.entities::_upsert_point_props`, `projection.entities::_upsert_subject`,
  `projection.entities::_upsert_object`, `projection.entities::_upsert_document`,
  `projection.entities::_upsert_event`, `sdk::create_point`, `sdk::update_point`. ⛔ That list is
  not counted here and is not its own authority — the INSTRUMENT is:
  `tests/test_embed_writer_register.py::test_the_seam_CALL_population_is_the_one_the_register_names`
  re-derives it at HEAD and reds when this sentence loses a member or names one discovery does not
  class that way. (The sentence itself is prose a lane edits — a pin can read it, not rewrite it;
  the red is what makes the edit unavoidable, which is the whole mechanism.) It
  had to be re-derived once already: this paragraph named four of the population (the AST pass's own
  exemplars) while five `projection.entities` writers reach the seam by the `encode_for_row` CALL
  branch, one file over — and they do NOT hold the container-key shape (`set_embedding_clause(…)`
  for subject/object/document, a Cypher graph-property SET (`n.embedding=…`) fed from the params
  dict for `_upsert_point_props`, the CALL itself for `_upsert_event`), which is exactly why the
  shape-blind branch had to admit them and why a census of
  that shape cannot count them — so "every" read as a complete extension of a
  population a `#11086` reader would then have under-counted. Membership and visibility are different
  questions; the writers whose membership the AST pass actually bought are `api::add_point`,
  `consistency::_graph_fingerprint`, `sdk::_index_gate_read` and `sdk::_write_capture_turns` (the
  four named at the start of this bullet), and nothing else.
  ⚠️ "Every module discovery walks" is not every module in `tortoise/`: the walk SKIPS `tortoise/embeddings.py` on purpose (the
  seam primitives are not write paths), and two functions there use exactly this form —
  `stamp_journal_embedding` (`payload["embedding"] = vec`) and `_drop_journalled_embedding`
  (`payload["embedding"] = None`). They are the seam, so they are declared nowhere and found
  nowhere; an enforcement point must not read their absence as "no such write".
* **Map-merges are NOT** (`SET n += $p`, `probe.UNDECIDABLE_WRITE_SHAPES`). Whether the merged map
  carries a vector is data flow, not syntax, and ~60 functions merge a props map. So the honest
  statement is not "no writer can bypass silently" but: **every writer discovery finds outside the
  gate is declared, and the shapes discovery cannot decide are named rather than assumed absent** —
  which is why `sdk::_update_entity`, one live map-merge instance, carries the NAMED `#4094`
  exemption instead of being read as "no write at all" — and `sdk::update_point` is the other
  KNOWN instance: a `SET n += $props` merge that carries a caller-supplied vector verbatim, classed
  `gated` and declared nowhere (read the closure-claim caveat below for what that does and does not
  mean). ⚠️ "Known" is load-bearing: the shape is undecidable by syntax (above), so neither this
  sentence nor the instrument can say these are the ONLY two — a reader scoping the residual must
  not read two as the complete population.

**Leaf, and it must stay one.** `04-plan.md` §5: a leaf must not live under `tortoise/projection/`,
whose `__init__` is the head of the `projection → pack_state → sdk → projection` cycle where a
module-scope read of the config constants is a **reproduced `ImportError`**
(`tortoise.projection.__init__::_config_classes` records the reproduced `ImportError`, both for a
module-scope call and for a module-scope read of the constants). This module imports only the
standard library — pinned by
`test_the_register_is_not_a_second_rule_set`, which reads this file's own `import` statements — so
importing it can never ENTER that cycle, which
`test_the_register_imports_without_entering_the_projection_cycle` proves separately in a subprocess
by asserting that `tortoise.projection`, `tortoise.pack_state` and `tortoise.sdk` never reach
`sys.modules` (a narrower claim than "imports nothing from `tortoise`": the package `__init__` still
loads `tortoise` and its config).

**⚠️ What the closure claim does NOT cover — read this before quoting `#11086`.** "No silent
bypass" holds for the population **discovery decides**, and that population is bounded by the
write shapes `tools/dod_5090_probe.py` can *decide*, not by "every writer". `#11274` widened it
(the CONTAINER-KEY shape `record["embedding"] = …` is now decided on the AST, which is why
`tortoise.sdk::_write_capture_turns` — invisible when this module was first written — is in the
census), and it named the shape it deliberately will not decide:
`UNDECIDABLE_WRITE_SHAPES` (`SET n += $p` — whether the merged map carries a vector is DATA FLOW,
not syntax). Measured at this head, further shapes are still ABSENT from the census — not
unclassified, absent, which is why the guard in `tests/test_embed_writer_register.py` stays green
for them: an inline property map (`MERGE (n:Point {…, embedding: $e})`), a whole-node replace
(`SET n = $map`), a property name INTERPOLATED into the clause, the container-HELPER /
COMPUTED-KEY forms a regex and a literal-slice test both miss
(`clean.setdefault("embedding", None)`, `d[KEY] = v`), and a DICT LITERAL carrying a constant
`"embedding"` key (`**{**payload, "embedding": v}`) — the regex cannot read it (no `=` follows the
key) and the AST pass never visits it (its targets are assignments, not an `ast.Dict` handed to a
call) — and those forms are not hypothetical. ⚠️ The SHAPE is what is blind, not necessarily the
WRITER carrying it: the interpolation is live in `tortoise.cache_projection::node_statement` and the
helper in `tortoise.sdk::_emit_event`, and BOTH of those writers are genuinely ABSENT from the
census (neither holds a decidable statement, so neither is in `WRITER_CENSUS` or this register at
all); by contrast the computed key in `tortoise.sdk::update_point` is in the instrument's
**discovered map** (`embedding_writers()`) as `gated` — it carries no `WRITER_CENSUS` row at all,
and that table's class vocabulary (`recognised_producer`/`exempt`/`bypass`) cannot spell `gated` —
while `tortoise.hosted_backup::restore_graph` is IN `WRITER_CENSUS` and is declared here off its
`set_embedding_clause`, NOT off its whole-node replace. The dict literal has carriers through
ANOTHER door too, and they are INSTANCES, not the population: `tortoise.sdk::_session_event_write`
and `tortoise.sdk::_embedding_repair_attempt` hold the `**{**…, "embedding": …}` spelling and are in
the discovered map as `unresolved_write` because their bodies ALSO call `set_embedding_clause(`, so
their membership is not evidence the literal was seen — exactly the distinction the
`sdk::_journal_event_raw_write` bullet below draws. The same blind class also travels as a
PARAMS dict literal carrying a constant `"embedding"` key — built inline (`params={…}`) in
`tortoise.projection.entities::_event_plain_merge`, `_upsert_subject`, `_upsert_object` and
`_upsert_document`, and built into a local then handed on by name (`params = {…}` → `params=params`)
in `_upsert_point_props`; the fifth of that family, `_upsert_event`, holds NO such literal — it
computes the vector and hands it to `_event_plain_merge`, which builds the dict — and a reader must
not take two as the complete set, nor take any quoted spelling as a literal to grep: the SHAPE is
what this paragraph names, not an instance list and not a search string. The authority
for SHAPES is
`tests/test_embed_writer_register.py::INVISIBLE_SHAPES`, which holds both the shapes neither pass can
spell AND the SCOPES the walk never enters: a count written
here re-stales with every shape added, so this paragraph names forms and instances and does not
count them.
⚠️ `tortoise.sdk::_journal_event_raw_write` is
visible for a DIFFERENT reason — `_EMBED_WRITE` matches the `embedding=None` in its SIGNATURE — so
it is not evidence that a literal subscript is what gets a carrier seen; the writers that genuinely
owe their visibility to `#11274`'s AST pass are the ones the container-key bullet above names as
found — not every container-key declaration in the table. A writer is also
excluded from the declaration requirement when discovery classes it `gated` (a bare call to anything
*named* `encode_for_row`), `producer`, `duplicate` (two same-named defs in one module that BOTH
reach `_put` — `_put` is only reached for a def discovery CLASSIFIES, so a colliding def that is a
pure reader never reaches it and the discovered def's own kind stays in the map, leaving an
`unresolved_write`/`ungated` sibling CAUGHT rather than excused; the test header's collision note
carries the measured cases) or when its
module will not parse. ⛔ **The `gated` escape is not hypothetical, and it is the widest one:**
`tortoise.sdk::create_point` and `tortoise.sdk::update_point` reach `encode_for_row` on the normal
path, so discovery classes them `gated` and owes them no declaration — yet **a caller-supplied
`embedding` is stored VERBATIM by a recorded decision** (PR `#3018` review P2: `tortoise.sdk::_sanitize_props`
rejects the embedding IDENTITY props and deliberately not `embedding` itself, and
`tortoise.sdk::create_point` assigns `_create_map["embedding"]` from `_embedding_expr` AFTER the
props loop), i.e. the gate does not decide that path. Both are reachable from the MCP surface
(`tortoise_create_point`, `tortoise_update_point`), so the sentence this register can keep is
**"no writer discovery finds OUTSIDE the gate is undeclared"** — never "no caller can supply a
vector the gate never saw". Closing the second is `#11086`'s seam, not this file's claim.
⛔ So this register is a **product declaration for a population discovery decides**, and `#11086`
must not read it as a closure proof. The residual shapes are pinned (not merely described) by
`test_the_DISCOVERY_blind_spot_is_pinned_not_merely_documented`, and widening discovery further is
`#11274`'s direction — the durable repair belongs to the instrument, because a hand-written table
can never cover a detector's blind spots.

**No `bypass` class — by construction.** `class` is one of `recognised_producer` / `exempt`. A writer
that is neither is **unclassified**, and :func:`classify` *raises* rather than answering. A
declaration that *asks* for `"bypass"` raises too: a bypass is a defect, never a declaration. The
probe's third class exists only so the instrument can *name* one if it ever finds it
(`bypass=0` measured); it is deliberately not an admissible value here.

**Vocabulary, and one finding.** `#11085`'s body says `gated`; `04-plan.md` §6.2 — higher precedence,
and the vocabulary `tools/dod_5090_probe.py` already uses — says `recognised_producer`. The plan's
word is used here because `gated` would be a **false claim**: the probe measures that *no writer in
this class* reaches `encode_for_row` (the gate's seam); they reach the canonical clause builder
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
#: `encode_for_row`: no writer in this class calls it, which is why `gated` is not the
#: class name. ⛔ And this spelling is NOT the instrument's `kind == "producer"`, which means
#: "computes a vector another function writes" — the two vocabularies collide on the word and
#: disagree on the meaning (`tortoise/sdk.py::_session_embedding` is the instrument's `producer`
#: and is not declared here at all). `tools/dod_5090_probe.py` keeps that wart because `#10541`
#: named the class; this module carries the warning instead of dropping it.
RECOGNISED_PRODUCER = "recognised_producer"

#: A function that is not a producer of a STORED vector at all: it renders the clause, forwards one,
#: journals one, reads one, sets a FLAG or emits a DIAGNOSTIC, or merely MENTIONS one in a
#: comment/docstring — or it stores one under a **named** owner exemption (`#4094` today; and
#: `tortoise.sdk::_write_capture_turns`, a `#5008` session writer that DOES store, is exempt as the
#: sanctioned turn-record CARRIER — so `#5008` is NOT a class marker here: `_session_event_write`
#: and `ingest_corpus` are `recognised_producer` and `_write_capture_turns` is this class).
#: ⚠️ The class therefore folds two very different dispositions into one value — "no write to gate"
#: (`tortoise.consistency::_compare_views`) and "a write gated out by a ruling"
#: (`tortoise.sdk::_update_entity`) — which an enforcement point cannot tell apart; a consumer that
#: needs the difference must carry it (`#11086`, arming the gate — `#11274`'s scope is the falsifiers
#: and explicitly adds no register entry or field), not re-derive it from this string.
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


#: `#11444`. The class's definition is a claim about a ROUTE, so it is EXPORTED as data: the audit
#: instrument that credits a `recognised_producer` declaration re-derives it against the writer's own
#: callees, and it reads THIS tuple rather than holding a second spelling of the same names. A copy
#: would drift in the false-hold direction — a seam added here would silently make every correct
#: declaration look uncorroborated. Names only: not a pattern set, not a rule set.
RECOGNISED_PRODUCER_SEAMS: tuple[str, ...] = (
    "set_embedding_clause",
    "_session_embedding",
    "compute_session_embedding",
)


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
            "`n.embedding = $emb` on the `embedding_verbatim` branch — and ⛔ that direct write is "
            "**not** a defect awaiting a fix: it is a RECORDED DECISION (`#5004` / PR #3018's TYPE "
            "CHANGE GUARD — `tortoise/vector_write.py` records that a caller-owned float64 list \"must "
            "not be silently narrowed through `vecf32`\", and that reversing it is \"never a quiet "
            "re-route through :func:`set_embedding_clause`\"), taken so a CALLER-owned vector keeps "
            "its raw form instead of being narrowed through `vecf32`. Re-routing it is a REOPEN of "
            "that decision, never a tidy-up. The class above speaks to the clause statement, not "
            "to the unit: an enforcement point must not whitelist the raw write under a producer "
            "class",
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
            "why `exempt` (the `#10541` wording — see `EXEMPT`'s own docstring, which covers BOTH "
            "'no write to gate' and 'a write gated out by a ruling') would contradict "
            "the class the chain's own seam carries",
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
            "with NO attestation. The server vector is stamped by `tortoise.api::_point` (its own "
            "`gated` classification, not an entry here). ⛔ A caller-supplied `embedding` is NOT "
            "refused by the SDK — "
            "`create_point`/`update_point` store one VERBATIM by a recorded decision "
            "(`tortoise.sdk::_sanitize_props` rejects the embedding IDENTITY props, not "
            "`embedding` itself; `_create_map[\"embedding\"]` is assigned AFTER the props "
            "loop from `_embedding_expr`; PR `#3018` review P2), so a TENANT can supply one "
            "on those paths. ⚠️ But this row is not their journal half, and it is not dead: "
            "it guards a vector handed straight to `add_point`'s `fields` — a DIRECT "
            "`EventAPI` caller. ⛔ `tests/test_write_path_unreachable_seam_5148.py` pins that the "
            "key is CARRIED, NOT that it reaches this branch: the test runs under an unimportable "
            "seam, where the branch body is a re-assignment `p.update(fields)` has already made, "
            "so it stays green if the block is deleted (its own docstring: \"This pins the GUARD, "
            "not the ASSIGNMENT ORDER\"). The RE-NORMALISATION this branch performs when the seam "
            "IS importable is what the test does not cover. "
            "The tenant doors journal in `tortoise.sdk`: `create_point` through "
            "`_journal_snapshot_from_create_map` → `_emit_event`'s "
            "`clean.setdefault(\"embedding\", None)`, `update_point` through the "
            "`PointRevised` record's `emit_props` (`update_point`'s emit). Their "
            "GATE-side half is a `gated` classification and therefore OUTSIDE this "
            "register's population — see the closure-claim caveat above",
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
#: ⚠️ Its population is **the population the instrument MEASURES**, not "every writer whose vector
#: the gate does not see": the ``unresolved_write`` set PLUS the ``ungated`` write path
#: (`tortoise.session_indexer::main`). A ``gated`` writer needs no declaration — the gate decides the
#: write it GATES. ⛔ That is not the same sentence as "the gate decides every vector this writer
#: stores": the module docstring's caveat refutes the wider one for `create_point`/`update_point`,
#: whose caller-supplied vector is stored verbatim. On the CREATE path `create_point` calls
#: `encode_for_row` (the derive is behind no `props` guard) and then discards the DERIVED result
#: **when the caller supplied one** (`if "embedding" in props:` pops the caller vector, and
#: `_create_map["embedding"] = _embedding_expr` is assigned AFTER the props loop, so the caller
#: branch wins — with no caller vector the derived one is what gets stored) — but that derive is NOT
#: reached on every call, so the verb is not "unconditionally": a `dedup=True` HIT returns at the
#: dedup fold BEFORE it, and its props leg hands `props` (a caller vector included) to
#: `update_point`, which skips its own derive for the same key (`"embedding" in props`) and merges
#: it verbatim. So on the create path the gate RUNS and its verdict is OVERWRITTEN, and where the
#: caller supplied a vector on the update path it is not called at all — including `create_point`'s
#: own `dedup=True` delegation, which is the MCP door's default. A content-only revise DOES reach
#: `encode_for_row` (`update_point` derives when `content` arrives without `embedding`), which is
#: part of why `update_point` classes as `gated`; neither outcome is "the gate decides this vector".
#: ⛔ Symbols only, never a line number: this block is comment prose
#: and nothing re-derives it, which is the rot `WriterClass.reason`'s rule exists to prevent.
#: ⛔ The difference between those two sentences is load-bearing and is stated in the module
#: docstring: the census's discovery is a source-text regex PLUS the AST pass that decides the
#: LITERAL container key (`rec["embedding"] = …`), and writers neither can spell (a map-merge, a
#: COMPUTED dict-key form (`d[KEY] = v`), an inline property map, a clause held in a module constant
#: or an INTERPOLATED property name) are found BY SHAPE in no case — where one of them does reach the
#: population it is for ANOTHER reason, and the live instances named here differ exactly so:
#: `sdk::_update_entity` (a map-merge) arrives as an `unresolved_write` and is declared here as
#: `exempt` under the named `#4094` exemption, `sdk::update_point`'s computed key arrives as `gated`
#: and is therefore declared nowhere, and `cache_projection::node_statement`'s interpolated property
#: arrives not at all. `#11274` NAMES the shape discovery will not decide
#: (`UNDECIDABLE_WRITE_SHAPES`) instead of guessing at it.
#: Forward — every writer DISCOVERY FINDS outside the gate is declared (never "every writer", see the
#: docstring's closure-claim caveat):
#: `tests/test_embed_writer_register.py::test_every_writer_the_instrument_finds_outside_the_gate_is_declared`.
#: Reverse — no declaration outlives its writer:
#: `tests/test_embed_writer_register.py::test_the_instrument_and_the_product_declare_the_same_writers`.
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

    ⛔ Precondition: ``writers`` must ALREADY be restricted to writers outside the gate. This
    register's population is the instrument's ``unresolved_write`` ∪ ``ungated`` KINDS, and that
    restriction is the CALLER's to apply (this module imports nothing from ``tortoise`` and ships
    no filter, so it cannot apply it for you;
    `tests/test_embed_writer_register.py::NEEDS_A_DECLARATION` is the same tuple of discovery kinds
    spelled for the suite — `tests/` is not shipped in the wheel (only
    `../tests/sample_transcript.txt` is), so a runtime
    consumer in ``tortoise/`` cannot import it from there). Handing it the instrument's whole map
    answers with the ``gated``/``producer`` writers that need no declaration, and :func:`classify`
    then raises for each of them — a false refusal on a correct write. ⛔ ``gated`` and ``producer``
    are statements about DECLARATION, never about the gate deciding every vector on that path:
    `tortoise.sdk::create_point` and `tortoise.sdk::update_point` are ``gated`` yet store a
    caller-supplied vector VERBATIM — read the module docstring's closure-claim caveat before
    reading their absence from this enumeration as coverage.
    """
    return tuple(sorted(set(writers) - set(EMBED_WRITER_CLASSES)))
