---
title: "WS-C design — numeric values as structural fields on an entity"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-10
updated: 2026-10-06
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutObjects: tortoise-extractor, tortoise-commit-schema, tortoise-projection
aboutSubjects: tortoise-value-layer, tortoise-state-and-value-model
governingAgreement: "#2817"
---

# WS-C — Numeric values as structural fields on an entity

**Parent:** #2820 (WS-C — state & value model) · **Issue:** #2817 · **Status:** design v2 (corrected framing), awaiting owner approval
**Feeds:** #2782 (money representation) · **Related:** #2813, #2795, #2818, #2730, #2725, #2687, #2453, #2747, #3011

> #2820's gate requires owner approval of a written design before implementation. This is that design.
> **Nothing here is implemented.** In §7.1, **S1/S2 need declaration, not approval**; **S3** (the HTTP
> validator) is the one genuine contract change, and it needs an **explicit decision**.

**Revision note (v2, 2026-10-06).** v1 was written parser-first: it made a locale-aware numeric parser the
foundation and reified each value as a tagged union of five `valueKind`s. **That foundation is withdrawn.**
§2 states the corrected shape. §3 keeps only the open questions that survive on the smaller surface; §4
lists what the corrected shape excludes. The platform research carried over is in §2.2/§2.4 and §9; v1's
decisions and its test table are not carried over.

---

## 1. Problem and the grounded requirement

**There is no numeric normalization anywhere in the system.** `rg -n
"milh(ão|ao)|amountMinor|amount_minor|valueKind|value_kind|parse_number|normalize_number" tortoise/ tools/`
returns no hits — re-run against this tree 2026-10-06, exit 1, no matches. (`rg -n -E` is `--encoding`, not
`--regexp`, and fails with exit 2 without searching; the form above is valid ripgrep.) Every amount arrives
as a raw string and stays one:
`"R$ 1.234,56"`, `"1,234.56"` and `"1234.56"` are three strings denoting one number, and nothing can
compare, sum or threshold them.

The only structured-value precedent is the **date gate**: `_valid_iso_date`
(`tortoise/extractor_v2.py:969`), mirrored by `Point._iso_date_prefix`
(`tortoise/commit_schema.py:547`). It is the never-guess posture this design adopts.

**The grounded requirement (#2782, verbatim):** *"tranche released / 75% of tranche 1 spent → tranche 2
unlocks"* — a **sum and a ratio against a threshold**.

That requirement is the whole of the demand. A sum and a ratio are **association traversals**: walk the
edges already held, add up the amounts, divide. **Neither needs an index on the amount.** The only
requirement that would force an index is *filter or rank an amount by value*, and nothing grounded asks
for it. The trace is **#2725 → #2782 → #2817**: the corpus asks for a **sum and a threshold**, never a
**sort**.

**What the current path does with an amount.** An undeclared numeric field falls through different write
lanes, and none of them store it as a value:

- The v2 extractor's S5 stage (`execute_embed`, `tortoise/extractor_v2.py:5804`) builds the point entry
  (`pt_entry`, `:6160`) from a fixed field set — a number stays in prose only.
- `EventAPI.add_point` (`tortoise/api.py:187`) and the commit door's point writes
  (`tortoise/hosted_api.py:14104`, `:14244`, in `_execute_commit_writes`) write a fixed property set.
- The projection's Point property list is fixed (`_upsert_point_props`,
  `tortoise/projection/entities.py:801`), but Point carries the same open-set passthrough as
  Subject/Object (`_persist_extra_props`, `tortoise/projection/entities.py:1102`, #2795/#2958): an
  undeclared scalar prop written live by `create_point` (`tortoise/sdk.py:5397-5399`) is replayed by
  `rebuild_all`, flagged by a "not declared" drift warning (`:1110`). Only `_POINT_DENY` members
  (`:715`) and undeclared list props are dropped.
- The HTTP commit validator `Point` (`tortoise/commit_schema.py:500`) is `extra="forbid"` (`:503`): a
  customer **cannot send an amount at all** today.

**Prose is load-bearing.** `VALUE_FIDELITY_RULE` (`tortoise/extractor_v2.py:324`) requires the exact
value to be written into `content`, and the retention graders read `content` only
(`tests/eval/write_path/grading.py:24-27`, `tests/eval/write_path/schema.py:128-142`,
`tests/eval/harness/grading.py:32-45`; the snapshot query is `MEMORY_ROW_QUERY`,
`tests/eval/write_path/runner.py:341`). The design below is therefore additive: the raw text stays,
and the value is derived alongside it.

---

## 2. The decision — a value is a flat structural field on an entity

### 2.1 Shape

A value is a **flat structural field on an entity** — not a `Point`, and not a node of its own.

| Property of the shape | Consequence |
| --- | --- |
| No confidence, no NAND-ability | Operators connect epistemic targets, and EP confidence propagates over atomic beliefs. **A belief can be argued with; an attribute cannot.** A value is not a claim, so it carries no belief and takes no operator edge. |
| No embedding | A value is found by its entity, not by similarity. Nothing retrieves an amount by meaning. |
| No numeric index | The grounded requirement is a sum and a ratio over edges already held. Nothing grounded ranks or range-filters an amount. |
| Not reified into its own node | Reification multiplies node count — and the index bytes that scale with it — for no requirement that exists (§2.2). |

The field is declared on the schema the write path accepts, and the raw text it came from is kept as the
evidence. Storing the value as a small set of scalars (an amount and its currency; the scale is **OD2**)
is what makes the field cheap.

### 2.2 Why this shape is arithmetic, not style — measured

On FalkorDB **the whole graph is RAM** — no spill-to-disk and no eviction on Cloud — at **$0.10/GB-hour ≈
$73/GB/month** on provisioned memory (**MEASURED** — vendor pricing, $0.10/GB-hour × ~730 h; the storage
magnitudes below are measured in `docs/architecture/STORAGE-ARCHITECTURE.md` §2.2 / line 173,
§7 / line 439 and §12.1 / lines 858, 930 — tracked at HEAD `e18dda11b` (PR #7559 merged as
`1c8bc4701`)). There is **no cold tier**: an
unread byte costs the same as a read one. The only documented removal of a graph is `GRAPH.DELETE`
(permanent); per-graph eviction is documented only for Enterprise (self-managed), not Cloud —
**documentation-based, unconfirmed** (its source qualifies it *"not exposed on Cloud as far as we can
establish"* and calls it *"the single most valuable thing to confirm with the vendor"*): **confirm with the
vendor.**

| Unit | Size | Basis |
| --- | --- | --- |
| an `amount` + `currency` field | ~16 bytes | **INFERRED** |
| one embedding | ≈1.5 KB (51.6 MB ÷ ≈33,580 embeddings; §12.1 / line 858 states "1.5 KB each", line 930 "1,830 B ≈ 1.79 KB/vector") | **MEASURED** |
| vector bytes per live `Point` | ≈6.4 KB (51.6 MB ÷ ≈8,032 `Point`s) | **INFERRED** — arithmetic on measured magnitudes |
| one node in the live graph | ≈9 KB (141 MB ÷ 15,521 nodes; denominator-dependent) | **INFERRED** — arithmetic on measured magnitudes |

At 1M values the cost ranges over **roughly two to three orders of magnitude**, each end with its basis
named: **≈$1.17/month** as plain fields (1M × 16 B = 16 MB), **≈$110/month** as nodes each carrying one
embedding (1M × 1.5 KB = 1.5 GB), and **≈$657/month** as nodes at the full per-node cost (1M × 9 KB =
9 GB) — denominator-dependent, since the source also carries ~3 KB/node and 1,594–6,003 B/node variants.
Each figure is **INFERRED** by arithmetic on the unit sizes above — the 16 B field and the ≈9 KB/node are
INFERRED, and the 1.5 KB/embedding is **MEASURED**.

Node count is a second, independent lever. The measured graph carries **45 MB of indices against 51.6 MB
of embeddings and 16 MB of text** (**MEASURED**, §2.2 / line 173, restated §7 / line 439) — the index bytes
scale with how many nodes are written.
Reifying each value multiplies node count; a field does not.

### 2.3 What replaces the parser as the foundation

Neither comparable system ships a locale-aware numeric parser (**unverified** — this design pass did not
establish the absence; the two profiles below are what the research returned):

- **Graphiti (Zep)** passes typed entity schemas (Pydantic) into `add_episode`; the schema guides
  extraction and validates it, and typed attributes land on entities. It keeps the raw episode, and its
  fact edges reference source episodes.
- **Mem0** keeps text as text; structured fields ride alongside it as **filterable metadata**.

Our own controlled ablation agrees on the keep-the-source half: raw **verbatim beats derived by 15.9 /
22.0 pts** (`EXTRACTOR-V4-ARCHITECTURE.md` §2.4; pre-registered #3011).

⇒ **Keep the raw text as the evidence, declare the field on the schema, and validate the value against
the words it came from.**

**Design requirement:** the approach is safe without a locale table only if the value is validated against
its source — a misread `R$ 1.234,56 → 1.234` is an **arithmetic mismatch against the sentence** to be
detected, not a silent wrong number. Nothing implements this check today.

**The locale-aware numeric parser is dropped as the foundation.** #2817 is no longer a parser project.

**Honest research limit.** This design pass did not find a comparable that validates an extracted value
against its source words. The **keep-the-raw-text** half is borrowed practice (Graphiti, Mem0, and our own
ablation); the **validate-against-source** half may be ours and is not borrowed authority. The
arithmetic-mismatch check is stated here as a design requirement, not as a claim about what comparables do.

### 2.4 Representation facts carried over

These are the money-representation findings that survive the corrected shape; the parser-specific research
(separator pattern ladders, magnitude tables) is out of scope per §4 and is referenced only where an open
question needs it (§3).

| Fact | Sources | Confidence |
| --- | --- | --- |
| Never store money as a binary float — `0.1` has no exact binary representation and repeated arithmetic drifts. | PostgreSQL numeric-types docs; Crunchy Data "Working with Money in Postgres"; practitioner write-ups | High (3+ independent categories) |
| Integer minor units are the reliable canonical form: addition/subtraction are exact at a known scale. | Crunchy Data; fintech write-ups; ISO 4217 minor-unit guides | High |
| The minor-unit exponent is **not 2 everywhere** — ISO 4217 permits 0, 1, 2, 3 (and 4 for some). JPY = 0, USD/BRL = 2, KWD/BHD = 3. | ISO 4217:2015 (minor-unit column); Adyen currency-code table | High |
| Currency is part of the value, not a global default — money is amount **+** currency as one unit of meaning (DDD's `Money` value object). | Martin Fowler `Money` guidance; PostgreSQL `money`-type docs (locale-dependent → unsuitable multi-currency) | High |
| Cryptocurrencies have no ISO 4217 code and can need 8+ decimals. | ISO 4217 overview; `currency-core` docs | Medium — emerging |

FalkorDB stores scalars and scalar arrays only; it has **no map/object property type** and **no decimal
type** (the props-passthrough note is `tortoise/sdk.py:2768-2777`). A value therefore cannot be a
`{amount, currency}` map, and a Python `Decimal` would have to be stored as an unindexable, unsummable
string. This is why the value is a set of scalar fields rather than one object.

---

## 3. Open decisions (re-derived register)

The settled shape (§2) shrinks the open set. What remains are **parsing questions on a much smaller
surface**, not the foundation. Numbering is re-derived and does not carry over from v1's register.

| ID | Question | Recommendation | Consequence if different |
| --- | --- | --- | --- |
| **OD1** | Where is the value field declared and typed? | Declare it once, on the accepted point-property surface (the fixed Point property list, `_upsert_point_props`, `tortoise/projection/entities.py:801`) and on the HTTP `Point` model (`tortoise/commit_schema.py:500`). | A parallel declaration drifts from the write path: the field still replays via the open-set passthrough, but the drift warning fires on every rebuild and the HTTP validator refuses it. |
| **OD2** | `minorUnitExponent`: store it on the value, or derive it from a version-pinned ISO 4217 table? | **Store it** (self-describing; no silent runtime table dependency) and check `(currency, exponent)` consistency against the table. | Derive-only is one field leaner, but every reader depends on the table, and a table change silently reinterprets historical values. |
| **OD3** | `asOf` when the text states no date: fall back to `when`, to the session date, or leave it absent? | **Leave it absent** when the text states no date. When a date is taken from elsewhere, record its source so a derived date is never read as a stated one. | A silent default stamps an assertion date the text never asserted. |
| **OD4** | Sub-minor precision (`R$ 0,123`): drop, or round to the currency's exponent? | **Drop the typed value; never round silently.** The raw text remains, so the drop is non-destructive. | Rounding needs a documented direction applied on every write path — a policy decision that does not belong to the parse. |
| **OD5** | Client-supplied values on the direct write path (MCP/SDK/HTTP): derive server-side, validate a client value, or reject it? | **The server derives from the raw text.** A client value is a cross-check; a mismatch is rejected, not silently trusted or overwritten. | Trusting a client value lets an unverified amount (e.g. `999999`) land with no relation to the prose. |

Bare `M` magnitude and locale hints are **dropped, not open**: `M` is 10³ in fixed-income/Roman notation
and 10⁶ in SI (Chicago Manual of Style; Corporate Finance Institute), and a session/document locale hint
has no input surface to carry it and adds a silent-misparse mode. v1 register rows that are not listed here
are closed by the corrected shape or excluded by §4: the five `valueKind`s, magnitude words, locale pattern
ladders, and cross-currency aggregation.

---

## 4. Out of scope

- **Ranking or range-filtering an amount.** The only requirement that would force a numeric index; nothing
  grounded asks for it (§1).
- **The parser.** No locale table, no separator pattern ladder, no magnitude-word table — withdrawn as the
  foundation (§2.3).
- **The five `valueKind`s** (money / percent / multiple / duration / quantity) as a designed union. Each
  kind multiplies stored values, and its justification was the ranking/range case now excluded.
- **Any schema, graph, threshold or gate change in this document.** This document is a design; it changes no code, no
  schema, no graph and no gate.
- **Implementation of any kind.**
- **FX / currency conversion.** A rate is a separate, time-varying fact with its own provenance and `asOf`.
- **Cryptocurrency / non-ISO units.**
- **Multi-value points** (a point asserting several distinct numbers).
- **Identity / MERGE-by-name** — #2730. The value layer must not be smuggled into identity.
- **Re-sealing the eval gold or baselines.**
- **Read-time value synthesis** — surfacing values into a reader's context is a follow-on. Consequence:
  the field adds no measured retention today, because the graders read prose only
  (`tests/eval/write_path/grading.py:24-27`).

---

## 5. Migration and backfill

Existing points already hold numbers as prose. Nothing is rewritten: the field is **added**.

- **The raw text is never modified.** `content` and `quote` are untouched, so content-addressed point ids
  (`point_content_id`, `tortoise/commit_schema.py:1452`) and commit ids are unaffected.
- **Backfill materialises derived values for historical consistency.** The rule adopted is the
  **stricter** of the two found in research:
  - it must be **safe to run twice** (idempotent);
  - it must **never overwrite a newer live write**, using a version / compare-and-set check keyed on
    **"absent or older"**, **not** "absent or differ".
- **Why "absent or older" and not "absent or differ".** A difference check loses the race: between the
  read and the write, a live write can land a newer value, and a "differ" predicate overwrites it.
  Comparing versions — with "older" as the only overwrite trigger — is the check that cannot clobber a
  newer write.
- **The backfill writes properties onto nodes; it changes no commit id.** A commit canonical is a pure
  function of the submitted payload, never of graph state.
- **The backfill is an owner-visible graph write** and is not run without approval (the `how-to-use-tortoise`
  skill governs any Tortoise graph write).

---

## 6. Test strategy

These are requirements on the derivation, not evidence of an implementation that exists.

- **T1 — Verbatim round-trip.** The stored value's `verbatim` is a byte-exact substring of the source, and
  no `content`/`quote` byte changes.
- **T2 — Validate against source (the core test).** For each fixture, the derived amount reconciles
  arithmetically with the sentence it came from. Named regression: `R$ 1.234,56` must not be accepted as
  `1.234`; the mismatch is detected against the source.
- **T3 — Ambiguity drops, never guesses.** `1.234` / `1,234` without a locale anchor produce **no typed
  value**; the raw text remains.
- **T4 — Currency is part of the value.** An unanchored symbol (`$`, `¥`) produces no currency; a
  per-currency exponent is checked against the pinned table (OD2).
- **T5 — The grounded requirement end-to-end.** Given an entity's association edges, the sum of its amounts
  and the ratio against a threshold are computed by traversal, with no index on the amount.
- **T6 — Backfill idempotency and race.** Running the backfill twice produces one value; a newer live write
  is not overwritten by the backfill's "absent or older" check.
- **T7 — Rebuild durability.** After `rebuild_all`, the value field is still present (the persistence
  dependency in §7.1).
- **T8 — Determinism.** The derivation depends only on its inputs: no clock, no network, no process-global
  locale.

---

## 7. Sequencing and required approvals

### 7.1 Surface changes — declaration for S1/S2, a decision for S3

**⛔ THE FIELD BELONGS ON `create_entity`, NOT ON `create_point`.** The approved MCP surface
(`docs/product/canonical-mcp-tools.md`, row 10) has **`create_entity` absorbing `create_point`**,
`create_event`, `create_object`, `create_subject`, `create_document` and `diary_write`, and design
decision #4 of that document says why: *"**Points, Events, Sources, Subjects, Objects and Documents are
all entities** (ontology §1), so one `create_entity` with `type=` covers the whole creator family **with
the right fields per type**."* The SDK list agrees and collapses `create_point` plus four siblings into
**`create_entity(type=)`** (`docs/product/beta-sdk-surface.md`, row 12).

**⇒ A numeric value is the *"right fields per type"* case.** The field therefore rides the surface
migration tracked by **#4282** — **Phase 2** (the SDK) then **Phase 3.1** (the 26 tools implemented on the
frozen SDK) — and **adds no tool and no method**, so it **does not re-cut the frozen manifest** and
`tools/surface-guard.py` is untouched. A lane that adds this field to `create_point` would have its work
discarded at Phase 2.

A design that assumes a field the write path cannot accept cannot be built. **S1 and S2 do not reject the
field today**, because both filter by **deny-list** rather than allow-list; what each needs is
**declaration**, not permission:

| # | Surface | Current state | What is needed |
| --- | --- | --- | --- |
| **S1** | MCP tool — **target `create_entity`** (absorbs today's `tortoise_create_point`, `tortoise/mcp_server.py:1366`; registry entry `tortoise/tool_registry.py:101`). Lands in **#4282 Phase 3.1**, on the frozen SDK | Takes `props` and filters it with a **deny-list** (`_SERVER_MANAGED_PROPS`, `tortoise/mcp_server.py:1285`); unknown keys pass through. | Declare the value field on `create_entity` for the entity types that carry an amount, so it is accepted and documented; no boundary rejection blocks it. |
| **S2** | SDK — **target `create_entity`** (absorbs today's `TortoiseSDK.create_point`, `tortoise/sdk.py:4937`). Lands in **#4282 Phase 2** | Already takes a `**props` passthrough, filtered by `_sanitize_props` (`tortoise/sdk.py:2596`, also a deny-list). | The `**props` passthrough means this is an **allow-list/declaration extension, not a signature change** — which materially lowers the cost of this surface. |
| **S3** | HTTP commit validator `Point` (`tortoise/commit_schema.py:500`, `extra="forbid"` at `:503`) | Closed: a customer **cannot send an amount today**. | Add the value field to the `Point` model, or decide explicitly that the field is server-derived and never client-supplied — a contract change. |

The **persistence surface** is a dependency, not a fourth approval: the projection's Point property list
is fixed (`_upsert_point_props`, `tortoise/projection/entities.py:801`), but Point carries the **same
open-set passthrough** as Subject/Object (`_persist_extra_props`, `tortoise/projection/entities.py:1102`,
#2795/#2958), so an undeclared scalar value written live (`tortoise/sdk.py:5397-5399`) **is** replayed by
`rebuild_all` — flagged by a "not declared" drift warning (`:1110`) rather than dropped. The persistence
gate is therefore **declaration, not permission**: adding the field to the declared list keeps the drift
warning off and documents the field, and it is **inside the approved scope**. What §4 excludes is
*implementation in this document* — no code, schema, graph or gate changes here — not the requirement
being approved.

### 7.2 Sequencing

The work cannot begin at the derivation. The dependency order is:

1. **Owner decisions** — the surfaces (§7.1) and the open register (§3). Nothing below starts before
   these. **S1/S2 need declaration, not permission**, and they ride #4282's Phase 2/3.1; only **S3** (the
   HTTP commit validator) is a genuine contract change, and it is closed today.
2. **Declare the field** on the accepted schema: the Point property list and the HTTP `Point` model. No
   behaviour change.
3. **Derive and validate** — the value is computed from the raw text and checked against the words it came
   from (§2.3).
4. **Backfill** historical prose (§5) — after step 3, and only under the stricter rule.
5. **Consume** — the sum-and-ratio traversal the grounded requirement (#2782) needs.

Steps 2–3 are the smallest reviewable surface. Step 4 is an owner-visible graph write and is not run
without approval.

---

## 8. A surviving correctness defect (not a design question)

`R$ 1.234,56` read with EN conventions is `1.234` — a 1000× error. That misread is a **live correctness
defect on the current path, independent of this design**, and it is tracked as a defect, not as a chapter
of this design. This document does not fix it. The corrected shape is what makes such a misread detectable
once a value field exists — an arithmetic mismatch against the source sentence — but detection is not the
defect's fix, and the defect is not an argument for the withdrawn parser.

---

## 9. Sources

**Internal (repo files; line references checked against this tree, HEAD `e18dda11b`; the two
`~/.swarm/research/…` entries below are machine-local, outside this repo and not at that head):**

- `docs/ONTOLOGY.md` §11 (v3.2, #398) — derived-value cache doctrine (`Derived values may be CACHED, never
  authoritative … the derivation is the truth, the cache is a performance artifact`, `:1515`); §4.1
  Point (`:844`).
- `tortoise/extractor_v2.py` — `VALUE_FIDELITY_RULE:324`, `_s2s4_rules:393`, `_granularity_text:914`,
  `_render_master:719`, `_valid_iso_date:969`, `run_s1:980`, `OUTPUT_CONTRACT:1201`,
  `_verbatim_match:2552`, `_fact_value_contradiction:2743`, `_resolve_source_turn:5592`,
  `execute_embed:5804`, supersession record `:6134-6140`, `pt_entry:6160`.
- `tortoise/commit_schema.py` — `class Point:500` (`extra="forbid"` `:503`), `_iso_date_prefix:547`,
  `_point_canonical:1322`, `canonical_payload:1366`, `compute_client_commit_id:1431`,
  `point_content_id:1452`.
- `tortoise/sdk.py` — `_sanitize_props:2596`, FalkorDB primitives note `:2768-2777`, `TortoiseSDK.create_point:4937`,
  props CREATE-map write `:5397-5399`, `_extract_session_v2:6956` (create_point call `:7257`).
- `tortoise/projection/entities.py` — `_persist_extra_props:758`, `_upsert_point_props:801`, Point prop list
  `_POINT_HANDLED:649` / `_POINT_DENY:715`, MERGE-by-name `_upsert_subject:2180` / `_upsert_object:2253`.
- `tortoise/mcp_server.py:1285` (`_SERVER_MANAGED_PROPS`), `:1366` (`tortoise_create_point`);
  `tortoise/tool_registry.py:101`; `tortoise/api.py:187` (`add_point`); `tortoise/hosted_api.py:14104`,
  `:14244` (the commit door's point writes, in `_execute_commit_writes`); `tortoise/retrieval.py` —
  fact-critical guard `_pkg_differ_value_critical:1682` (`_token_is_fact_critical:1628`),
  `_pkg_norm:1650-1656` ("CURRENCY SYMBOLS ARE CONTENT", #2687); `tortoise/aggregate.py:175-181`;
  `tortoise/pack_registry.py` (`CANONICAL_KINDS:105`).
- Eval graders: `tests/eval/write_path/grading.py:24-27`, `tests/eval/write_path/schema.py:128-142`,
  `tests/eval/write_path/runner.py` (`MEMORY_ROW_QUERY:341`), `tests/eval/harness/grading.py:32-45`.
- `docs/architecture/STORAGE-ARCHITECTURE.md` §2.2 / line 173 — 45 MB indices, 51.6 MB of embeddings,
16 MB text (the ≈33,580-embedding count is §12.1a / line 933); §7 / line 439 — the same split restated
as ≈113 MB of the ~141 MB graph, with the 141 MB / 45 MB pair flagged there as superseded by §1's ruling
block (which reads 143 MB / 46 MB) — **line 439 carries no node or `Point` counts**; §12.1 / line 858
("1.5 KB each") and §12.1a / line 930 ("1,830 B ≈ 1.79 KB/vector") — the per-embedding size. §12.1b is
the unindexed `Object`/`Event` accounting and carries none of these magnitudes. The 15,521-node /
8,032-`Point` figures are this document's own **INFERRED** arithmetic (§2.2 table), not magnitudes from
that source. Tracked at HEAD `e18dda11b` (PR #7559 merged as `1c8bc4701`); the magnitudes are cited from
that source, not from #2782.
- Research: `~/.swarm/research/2026-09-09-typed-values-and-lifecycles.md`,
  `~/.swarm/research/2026-09-09-numeric-storage-audit.md`.
- `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §2.4 (`:127`; the verbatim-vs-derived ablation,
  pre-registered #3011) — §2.4 reports **verbatim beats derived by 15.9 pts (LoCoMo) / 22.0 pts
  (LongMemEval-S)** (`:148`).

**External:**

- PostgreSQL — [Numeric Types](https://www.postgresql.org/docs/current/datatype-numeric.html),
  [Monetary Types](https://www.postgresql.org/docs/current/datatype-money.html).
- Crunchy Data — [Working with Money in Postgres](https://www.crunchydata.com/developers/playground/working-with-money-in-postgres).
- ISO — **ISO 4217:2015** (the currency-code standard; cited by number, because ISO's site rejects automated clients and a link there can never be checked) — see [Wikipedia ISO 4217](https://en.wikipedia.org/wiki/ISO_4217).
- [Adyen currency codes & minor units](https://docs.adyen.com/development-resources/currency-codes).
- [Chicago Manual of Style — `M`/`MM`](https://www.chicagomanualofstyle.org/qanda/data/faq/topics/Abbreviations/faq0094.html);
  [Corporate Finance Institute — MM (Millions)](https://corporatefinanceinstitute.com/resources/fixed-income/mm-millions/).
- [Wikipedia — Long and short scales](https://en.wikipedia.org/wiki/Long_and_short_scales).
- Martin Fowler — Money value object (`https://martinfowler.com/`).
