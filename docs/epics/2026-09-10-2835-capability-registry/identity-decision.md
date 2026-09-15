# Identity decision — the #2835 gate

**Date:** 2026-09-15
**Status:** DECIDED (agent recommendation) — awaiting human ratification
**Answers:** epic #2835 Human Gate #1 — *"What is a component's identity, and how is a rename distinguished from a delete-plus-create, and from id reuse?"*
**Evidence base:** `00-align.md`, `research.md`, `prior-art-scan.md`, plus two fresh external research legs run 2026-09-15, plus the #2977/#3326 review history (8 cycles). **Code line citations are pinned to `@1d53dfef8c438e4d17870276230da83fdac21669`** — this doc's worktree base. Every `file:line` below (all of them under `### Stage 3 — blast radius`) resolves against that commit (see the section's own pin). **The pin is for stability, not because `main` has moved:** as of `dae078701`, `tortoise/sdk.py` is byte-identical between the pinned base and `origin/main`, so the coordinates below are valid on both. **Caveat (carried, not buried):** one of the two research legs did **not complete its mandated review loop** — cycle 1 found 9 issues, cycle 2 found 5, and **cycle 3 (the independent re-review of the fixes) aborted on a tool failure — not a verdict**; a short fetch-free pass returned clean, but that is **not independent re-verification**. Every claim sourced solely from that leg is therefore marked **pending its completed third gate** (notably the Linux generation-counter mechanism and the "no mainstream system" negative, both re-tagged below).

---

## The decision, in one paragraph

**Stop deriving identity from the name.** An entity's durable identity must be an **opaque identifier minted once at creation and carried in every journal event**; the **name is a mutable *natural key*** held in a lookup index, not the identity. A **rename is a journaled mutation event on the stable id** (`Renamed(id, old, new)`), never a delete-plus-create. **Deletion is a terminal journaled event** and a re-creation after deletion **mints a fresh id**. Until the full migration lands, the projection must **fail closed**: when a journal event cannot be resolved to exactly one node, it must **refuse to fold and fail the run** — a refused event is an **assertion failure**, not a warning, and the invariant must assert on the refused set or the refusal passes vacuously.

---

## Why now — the internal evidence

This is not a theoretical concern. #2977 spent **eight review cycles** on identity in the fold. The pattern:

| Cycle | Found | Outcome |
|---|---|---|
| 1–5 | progressively narrower fold holes | each fix closed the reported shape, opened the next |
| 6 | `#3389` — the writer mints a second unjournaled carrier | fixed at the writer (correct direction) |
| 7 | `#3573` P1-A — guard inert when the retraction's id is anchored to a *different* name | open |
| 8 | `#3573` P1-B — a retraction preceding the only journaled registration buries the re-created node | open |

Every one of these is a **data-loss** shape (a live Object replays as `retracted`, and `retracted` is excluded from both recall and object search — so it silently vanishes). On both open P1s, **`main` is more correct than the branch**. That is the signal: patching fold selection is not converging because the fold is being asked to recover information the journal threw away.

The root cause, stated once: **`obj-<sha26(name)>` is a function of the name.** So the id cannot disambiguate two incarnations that share a name, and it changes meaning whenever the name does. Every writer that forgets to journal, and every reader that tries to be clever, is compensating for that one fact.

---

## External evidence — one school, and one leg whose third gate aborted

### The two axes (the finding that reframes the problem)

Renaming/move continuity decomposes into two independent problems, and the industry has solved exactly one:

- **Axis A — path dependence. SOLVED by the mainstream.** Clang USR (`c:@F@main`), Meta Glean (`cxx1.QName { name, scope }`), SCIP descriptors, and Roslyn `SymbolKey` are all **path-independent** — they survive file moves.
- **Axis B — name dependence. SOLVED BY NOBODY among the name-bearing symbol systems examined.** The four systems just listed all derive identity *from the name* — that is the whole defect. The claim is deliberately **not** universal across the table below: the two **content-addressed** rows (Software Heritage, GitHub Blackbird) are the exception, keying on content rather than the name — which makes them renames-blind too (a rename changes the name, not the content, so a content key cannot express one; and any content edit mints a new key). Sourcegraph's move from LSIF to SCIP went *toward* name-derived human-readable symbol ids.

**Tortoise is already on the correct side of Axis A** (name-only, no path). The problem is entirely Axis B.

And the sting: **clangd's index key is `SymbolID` = truncated SHA1 of the USR** — **the same shape** as our `obj-<sha26(name)>` (a name-derived string truncated to a hash and used as a primary key). It shares our **rename** ambiguity, but it is *not* identical: clangd has **no concept of delete/re-create**, so it does not inherit that ambiguity at all; and a Clang USR **includes scope** whereas our hash is **name-only**, so clangd's rename ambiguity is **narrower** than ours. The underlying point still holds — *the mainstream chose the name-derived key.* *We are not making a private mistake; we are on the mainstream path.* The consolation is that the mainstream path is the one with no solution.

### Per-system verification (primary sources, not summaries)

| System | Entity key | Survives move? | Survives rename? |
|---|---|---|---|
| Kythe VName | `signature`+`corpus`+`root`+`path`+`language` | — | **No** — the spec says the signature "does not necessarily need to be stable across different versions of the input" |
| GitHub Stack Graphs | `NodeID { file, local_id }` | no | **No** |
| LSP | URI + range — **no identity field exists** in `DocumentSymbol` | file renames travel as `oldUri→newUri` | **No** |
| Software Heritage | 5 object types (`cnt`,`dir`,`rev`,`rel`,`snp`) — **no entity type** | content: yes; dir: no | **N/A** |
| GitHub Blackbird | Git blob OID | yes (blob is content-only) | **No** at entity level |
| Clang USR / clangd `SymbolID` | context + name → SHA1 | yes | **No** |
| Meta Glean | `QName {name, scope}` | yes | **No** |
| Sourcegraph SCIP | package + name-bearing descriptors | yes | **No** |
| Roslyn `SymbolKey` | containing type + `MetadataName` | yes | **No** |
| Qdrant (Jul 2026) — **Medium** (vendor blog) | path + qualified name | no | **No** — "a rename looks like a delete plus an add" |

**Negative verdict — Medium ⚠️ (inference from absence):** **no *entity-level, name-derived* identity mechanism among the ten systems examined provides rename continuity.** Renames are handled by **heuristics** (Git `log --follow`) or by **the user telling the tool** (Kythe's `vnames.json` is literally a hand-authored path-rewrite rule list). These verdicts are **deductions from the primary specs/source listed below, not from statements that discuss renames** — no system's documentation says "we cannot rename"; the absence is inferred from what each keys on. That is why the tag is **Medium**, not High. It is also **pending the aborted third review gate** of the leg that produced it (see the evidence-base caveat). The only two productized rename-continuity mechanisms found are outside the mainstream and **Low ⚠️ single-source** (Aura, which hashes the function body *excluding its own name* and records rename ops explicitly; and Skir, design-stage). The ETX-2006 proposal — **persistent id, name as a mutable attribute** — remains unproductized, 20 years on, and is exactly what we should adopt.

**Verify-when notes:** **GitHub Blackbird** — re-verify the entity-level rename verdict **when a second independent description of symbol identity surfaces**; its evidential base today is one engineering blog. **Aura and Skir** — re-verify **against vendor/primary docs before citing as productized prior art**: Aura is a **competitor with authoring interest disclosed**, and Skir is **design-stage** (not shipped), so neither is settled prior art without a primary-doc check. **Qdrant** carries a **Medium** tier (vendor blog, Jul 2026), marked in the table above.

### The id-reuse hazard — non-reuse is the norm; one named mechanism (single-publisher)

The prior scan's single query on id-reuse returned nothing. Targeted rephrasing split the finding in two, and the split matters for confidence.

**Non-reuse is the norm (High confidence — two independent normative sources, plus one corroborating default):**
- **NIST SP 800-171 §3.5.5 (normative):** "**Prevent reuse of identifiers for a defined period.**"
- **PLOS Biology 2017 ("Identifiers for the 21st century", normative):** identifiers must never be deleted or reassigned to another record.
- **PostgreSQL `CREATE SEQUENCE` (corroborating, not normative):** `NO CYCLE` is the **default** — the fail-closed choice. `CYCLE` (reuse) must be opted into. This is an **overridable implementation default**: it is evidence that fail-closed is *conventional*, not that non-reuse is *required*.

**Only the first two are normative.** NIST and PLOS are independent normative sources; PostgreSQL corroborates the convention. The claim is therefore **"non-reuse is the norm"** — normative in two independent traditions and conventional in a third — not "non-reuse is universally required."

**A generation counter is one named mechanism (Low ⚠️ single-publisher — Linux only).** Linux `open_by_handle_at(2)`: delete a file, re-create it with the same content, and `stat` reports the **same inode**; the kernel nonetheless fails the stale reference with `ESTALE`. The header says it plainly: `FILEID_INO32_GEN` = *"32bit inode number, 32 bit generation number"*. **Identity is effectively `(stable key, generation)`**, and bumping the generation makes a stale reference *detectably stale* instead of **silently wrong**.

The `Low` tag is not a small caveat: the entire generation-counter mechanism rests on **one publisher** (the Linux man page + the kernel header), and it is the claim that arrived through the **aborted** third review gate. Whether the analogy transfers to our setting at all is examined — and largely rejected — under **R6 in detail** below.

### Event sourcing — what the journal must carry

Four rules — **R1, R3, R4, R7** — **convergent within the event-sourcing literature, one school, downstream of Greg Young** (Fowler, Microsoft CQRS Journey, SoftwareMill, Eventuous, `eventsourcing` docs). This is **not** convergence across independent traditions: every named source draws on the same lineage, and **no independent tradition was found for R1, R3, R4, R7**. The whole bundle is **pending the Greg Young / KurrentDB / EventStoreDB primary read** the gaps section schedules.

1. **Identity is minted, opaque, and carried in the event** — never recomputed from mutable content. The natural key (our name) travels as **separate domain data**.
2. **Rename is a mutation event on the stable id** (`Renamed(id, old, new)`), not delete+create. Delete+create is correct only for a *genuinely different* entity — which then requires a **fresh id**.
3. **Deletion is a terminal event that closes the stream.** Stream *presence* distinguishes "never existed" from "existed and was deleted". A re-creation is a **new stream with a new id** — which is why replay then trivially yields "exists and live": the new id's stream contains `Created` and **no** `Deleted`.
4. **Order by version/log position, never timestamp.** Timestamps are informational (they are not monotonic across producers — a second, independent non-determinism source alongside identity).

The data-warehousing lineage offers an **analogy, not a fix** — and the canonical version of it is the *opposite* of our problem. In canonical **Kimball SCD Type 2**, a tracked attribute change **inserts a new row with a new surrogate key**; **cross-version entity continuity is carried by the natural key**, not the surrogate. SCD2's premise is therefore that **natural keys do not change** — precisely the assumption our domain violates (our natural key *is* the name, and rename is the mutation we must survive). SCD2 is **not** "the fix for our exact bug": its continuity mechanism presupposes the invariant we lack. What we borrow is only the **vocabulary split** (surrogate = durable id, natural = mutable name) and the `valid_from`/`valid_to` versioning pattern. If a *non-Kimball entity-surrogate* variant (a surrogate that stays with the entity across natural-key changes) is meant, that is a different design and must be named as such — it is **not** what SCD2 specifies.

And the verification discipline — **Medium ⚠️ emerging**, practitioner-sourced. The naive form is the testable invariant that would have caught every bug found this week:

```
rebuild_from_zero().materialize() == live.materialize()
```

It is **not sufficient on its own**: once R8's fail-closed rule lands, two *equally incomplete* projections compare equal and the naive form passes vacuously. **R9 in detail** extends it to compare the **refused-event set** as well — see below.

---

## Source disagreements (unresolved)

The research legs did not deliver one voice. Four points are contested or single-sourced, and the doc takes a position on each rather than papering over them. **None of these change the decision** (mint ids; name = mutable natural key; staged migration); they bound the confidence of its supporting claims.

1. **Tombstone semantics — the same word, two mechanisms.** Kafka/Debezium compaction defines a *tombstone* as a **value-less record** telling the log compactor to delete the key's history within a retention window — a **storage-reclamation** mechanism, and a tombstone older than `delete.retention.ms` disappears with no trace. The event-store literature defines *stream closure* as a **terminal domain event** (`Deleted`) that permanently closes the entity's stream — a **semantic** mechanism, never garbage-collected, and the thing replay orders against. **Chosen position: the stream-closure-marker sense.** A tombstone must survive as an event a rebuild can fold; a compactor's tombstone is by construction allowed to be erased. (The Kafka sense is explicitly **rejected** — see R4.)

2. **Event naming style — property-sourced vs intent-revealing.** Whether the event type should name the *property that changed* (`NameChanged`) or the *intent* (`Renamed`) is **contested, and the supporting material is single-source**. **Chosen position: intent-revealing** (`Renamed(id, old, new)`), because the fold and the audit trail both want the domain meaning, and a property-sourced name leaks the storage model into the journal vocabulary. This is a **Low-confidence stylistic choice**, not a finding — nothing in the model depends on it.

3. **Id re-creation policy — refuse vs create-if-missing is an OPEN question in the framework itself.** Axon's framework issue **#3323** leaves the behaviour of `create-if-missing` on a previously-deleted aggregate id **unresolved** — the framework does not prescribe whether a deleted id may be re-created. **Chosen position: refuse.** R5 forbids reuse, so create-if-missing on a deleted id is a **rejected path** in our model; we do not inherit the framework's ambiguity because we close the id space by policy.

4. **Ordering key — prescriptive vs informational.** Ordering by **version/log position** is **prescriptive** across the event-sourcing sources; **timestamps are informational**. The sources agree on the prescription but do not all state the timestamp corollary in the same terms. **Chosen position: position orders, timestamp informs** (R7) — timestamps are not monotonic across producers, so they cannot order a replay.

---

## The model

### Rules

| # | Rule | Rationale |
|---|---|---|
| **R1** | Every entity gets an **opaque id minted once at creation**. It is never derived from the name. | Event sourcing §1; ETX 2006; the root cause above |
| **R2** | The **name is a mutable natural key**, held in a lookup index — not the identity. | SCD2 **vocabulary only** (surrogate vs natural) — the canonical SCD2 continuity mechanism does not transfer (see the note above) |
| **R3** | A **rename is a journaled mutation event on the stable id**. | Event sourcing §2 |
| **R4** | **Deletion is a terminal journaled event** that **closes the entity's stream** (the *stream-closure-marker* sense of "tombstone"). | Event sourcing §3. **Explicitly rejects the Kafka/Debezium compaction sense** of tombstone (Source disagreements §1): a tombstone must be a foldable event, never a reclaimable marker. |
| **R5** | Re-creation after deletion **mints a fresh id** — an id is never reused. | **A policy choice, not a convergent finding:** it is the **safe default** (a fresh id cannot alias a previous incarnation), and it is backed by the non-reuse norm (NIST 800-171; PLOS 2017; PostgreSQL `NO CYCLE` *default*). R5 also **closes the framework's open question** (Axon #3323, Source disagreements §3) rather than answering it. |
| **R6** | The **legacy derived alias** `obj-<sha26(name)>` is a *recyclable natural-key handle*, not an identity: it carries a **generation counter** so a **stale alias reference fails loudly instead of resolving to a different incarnation** — *only if the handle embeds the generation* (`alias@gen`; see R6 in detail). | Linux `FILEID_INO32_GEN` — **Low ⚠️ single-publisher**, and the analogy is partly rejected (see R6 in detail). **Migration-scoped, not a permanent rule.** |
| **R7** | Replay orders by **log position**, never timestamp. | Event sourcing §4 |
| **R8** | The projection **never guesses**. Unresolvable ⇒ **the event is not folded**, it is recorded (`refused` or `journaled-and-flagged`, per failure shape) — **and the run fails** (assertion failure, not a warning). **Every non-folded event enters the asserted set and fails the run; the disposition label is the *record* of the event, never an exemption from the assertion.** | Our own repeated failure mode |
| **R9** | The invariant asserts **`(rebuild == live)` AND an empty refused-event set — where that set holds *every* non-folded event, refused *or* journaled-and-flagged** — a first-class assertion, not an aspiration. | The testable discipline above; R9 in detail |

### R6 in detail — the legacy alias generation counter

R6 as originally stated ("a reused/recycled key carries a generation counter") was **ill-posed**: R5 already forbids reuse, so no *reused key* exists for a counter to qualify. The thing that **can** be recycled is the **derived alias**:

> **R6 (restated): the legacy derived alias `obj-<sha26(name)>` is a *recyclable natural-key handle*, not an identity. It must carry a generation counter so that a stale alias reference fails loudly instead of resolving to a different incarnation.**

- **Carrier:** the generation lives **on the alias, in the name→id lookup index** (the same index R2 defines) — *not* on the entity node. The entity keeps only its opaque id; the alias entry is `(alias_string, generation) → entity_id`.
- **Write path that bumps it:** **entity deletion** (R4's terminal event) removes the current alias entry and bumps the generation for that alias string; **re-creation** (R5) mints a fresh id and installs a **new** alias entry under the **bumped** generation. A reference that captured the pre-deletion generation therefore no longer matches.
- **Read path that returns the loud failure — and the handle format it requires:** resolution **fails loudly** with a structured `stale-alias` error naming both generations and the entity (it does **not** return the new incarnation) **only if the presented generation actually reaches the resolver.** As written elsewhere in this doc, every handle is a **bare string**, and the index is keyed by that single string (`(alias_string, generation) → entity_id`); a caller holding a cached bare alias presents **no** generation, so the resolver would look up the current entry and return the **NEW** incarnation — precisely the "silently wrong" outcome R6 claims to prevent (the same asymmetry the counter-cases (ii)/(iii) below acknowledge). The mechanism is implementable **only if the handle embeds the generation** — i.e. resolution takes `(alias, generation)`, or the handle is spelled `alias@gen`. **Legacy bare-string aliases cannot be checked at all**, because they carry nothing to compare against — which is itself an argument that R6 is **migration-scoped** (it can only protect callers that adopt the new handle format). **If that handle-format change is not made, R6's read-path claim must be dropped**, not left standing as an unimplementable mechanism.

**The strongest counter-case (why the Linux analogy does not transfer):**

- **(i) The counter exists in Linux because the inode number is *unavoidably* reused** — a finite 32-bit space forces it. There the generation is a **compensating control** for a resource ceiling. Our id space is not scarce: we can simply never reuse an opaque id (R5), which is the cleaner fix the counter is imitating.
- **(ii) In Linux the counter qualifies a *handle a remote client cached across time*.** The client holds an opaque handle and cannot tell that the world moved on. A **journal event is an immutable historical record, read positionally** — an old event naming an old incarnation is **correct**, not stale. Re-reading it must return the old incarnation, and applying a generation check to the journal would corrupt replay rather than protect it.
- **(iii) Linux's "stable key" is stable because it is embedded in the client's handle**; the client cannot re-derive it. Our **opaque id is changeable at mint time by design** (R1) — the whole point is that the name no longer determines it — so the "stability" the Linux generation protects is a property we deliberately do not have.

**Verdict — is R6 worth keeping?** **Only for the legacy alias, and only during migration.** R1+R5 already give permanent identity safety: an opaque id is never derived from the name and never reused, so no correct reference can go stale. R6 has value in exactly one window — **Stages 3–4**, while old `obj-<sha26(name)>` aliases still exist and are still being resolved by callers who cached them **and who adopt the `alias@gen` handle format** (a caller still holding a bare string presents no generation, so R6 cannot protect it — see the read path above). It is therefore **scoped to the migration**, not stated as a permanent invariant of the model, and it is **not** "the novel claim" the earlier draft advertised.

### R8 in detail — the fail-closed rule

This is the part that stops the bleeding **before** the migration finishes. The current fold asks *"which node does this retraction refer to?"* and, when the answer is ambiguous, **picks one and buries it**. The correct behaviour when the target is not unambiguously resolvable is:

- **do not fold**,
- **leave the projection in the state it was**,
- **record a structured non-folded entry** — a refusal or a flag — naming the journal position, the candidates, and the failure shape (this is the **refused-event set** R9 asserts on; *every* non-folded event belongs to it, whatever its label),
- **and fail the run.** Collection continues so the full refused set is enumerated, but the run **does not pass** — a refusal raises a structured error and exits non-zero.

**A refused fold is an assertion failure, not a log line** — and a **journaled-and-flagged** fold is not a lesser outcome: it is recorded, and the run still fails (the assertion covers *every* non-folded event, whatever its disposition label). For the **rebuild**, a refusal is a **raised structured error and a non-zero exit** — the pipeline must not stay green. A merely loud warning that leaves the build passing is exactly the anti-pattern this codebase already burned itself on: **failure to resolve must be a positive assertion of what was found**, never a silent (or merely noisy) pass. R9 is what makes it real.

#### R9 must compare the refused set, not just the projections

R9 as stated ("`rebuild == live`") is **defeated by R8 unless it is extended**:

> **R9 (in detail): the invariant compares `(materialize(), refused-event set)` between live and rebuild, and fails on any non-empty refused set — the set that holds every non-folded event, refused or journaled-and-flagged.**

Without that comparison, R8 **manufactures exactly the vacuous pass it is meant to prevent.** Once the fail-closed rule lands, the same underlying bug presents **identically as a refused fold in both live and rebuild**: each side skips the same unresolvable event, each side produces the **same equally incomplete projection**, the two projections compare equal, and the test **passes green** while the event is silently unfolded on both sides. `rebuild == live` alone cannot see it — it compares two projections that are wrong in the same way. Asserting on the refused set is what converts "both sides agree they are incomplete" into a **failure** — and because the set admits *every* non-folded event, not only the ones labelled "refused", a flagged event cannot slip past it. This is why the "positive assertion of what was found" requirement above is **load-bearing infrastructure, not decoration**: the refused set is the thing R9 asserts on.

#### The trade-off is real — a refusal is not cost-free

Refusing a fold is **not** a free win, and "it just degrades to not-yet-derived" is too generous:

- Refusing an **assertion / re-registration** fold is comparatively benign.
- Refusing a **retraction** fold leaves the Object **`live`** — and because `retracted` is excluded from both recall and object search, a retracted entity whose retraction was refused will **leak into recall and object search**. That is the **opposite harm direction** from the one R8 was written for: instead of an entity buried silently, the entity is **not buried at all**.

This is a **deliberate choice**, not a cost-free one. The harm classes: **a leak is visible and recoverable; a burial is silent and is data loss.** We take the leak. The consequence for implementation is that *what the projection does with the node state* must be **gated per failure shape, not applied blanket** — a blanket refusal would convert every unresolvable retraction into a leak, while other shapes are better left untouched. **That gating decides the node state (leak vs. leave untouched) and *never* whether the run is allowed to pass:** refusal and journaled-and-flagged are both **non-folded** outcomes, both enter the refused-event set R9 compares, and both **fail the run**. The flag is the **record** of the non-folded event — the audit trail naming its position, candidates, and shape — **not an exemption** from the assertion. Which shapes are refused (leak) and which are journaled-and-flagged (leave untouched) is a **Stage 0 design decision**, and it must be made explicitly rather than by default. If Stage 0 finds a shape that is genuinely exempt from the fail-the-run assertion, it must **name that shape explicitly here and bound the degraded guarantee in writing** — an unnamed exemption is a green pass over an unfolded event, which is exactly the anti-pattern R8 exists to prevent.

---

## Migration path

The full model is a large change. It is staged so that each step is independently shippable and the system is never worse than it is today.

| Stage | Change | Unblocks / fixes |
|---|---|---|
| **0 — Stop the bleeding** | Add the `rebuild == live` + refused-set invariant test across all apply-based engines. Make the retraction fold **fail closed** (R8) — an assertion failure, not a warning. | **#3326 / #3573** can land or be re-scoped; the two P1 burials become loud, not silent |
| **1 — Journal the real id** | Writers journal the **actual node id**; the fold matches **journaled ids only**, never a derived id. Fix `name[:200]` truncation to one rule (**#3574**). | Removes the "which id did I mean?" class entirely |
| **2 — Journal the rename** | `update_entity(name=…)` emits a journaled rename event (R3). | **#3377** |
| **3 — Mint opaque ids** | New entities get a minted id; `obj-<sha26(name)>` demotes to a **lookup alias**. Backfill existing nodes. **The minted id shape must satisfy the existing `_is_entity_id` guards or those guards migrate with it** (see blast radius below). | The epic's gate, fully |
| **4 — Retire the alias** | Add the R6 generation counter to the **legacy alias index only**, then retire the alias once no caller resolves it. | Migration-scoped; **not** a permanent rule, and no longer "the novel claim" |

Stage 0 is small and is the answer to "what do we do about #3326 this week". Stages 1–4 are the epic.

### Stage 3 — blast radius

> **Line-reference pin: every `file:line` in this section is `@1d53dfef8c438e4d17870276230da83fdac21669`** — this doc's worktree base. Resolve them with `git show 1d53dfef8:<file>`. **This is a stability pin, not a drift warning:** `git diff 1d53dfef8 origin/main -- tortoise/sdk.py` is empty as of `dae078701`, so `sdk.py:17167` is the `aboutSubject` guard on both the base and `main`. Where a symbol name is given alongside the number, the **symbol is the durable reference** and the number is the pin's coordinate.

**This is the "should have been researched before deciding" item.** Stage 3 re-keys the entity primary key, and the current id format is load-bearing in code the decision did not survey. This section is that survey — every line below was read this session — and it constrains the id format Stage 3 may mint.

**The id-shape contract.** `tortoise/sdk.py:994`:

```python
_ENTITY_ID_RE = re.compile(r"^[a-z]{2,3}-[0-9a-f]{26}$")
```

`_is_entity_id` (`tortoise/sdk.py:997-1007`) returns `True` iff `_is_ulid(s)` **or** `_ENTITY_ID_RE.match(s)`. `_is_ulid` (`sdk.py:985-987`) accepts the canonical `<hex>-<12 hex>` form (`_ULID_RE`, `sdk.py:980`) **or** a Crockford base32 ULID (`_CROCKFORD_ULID_RE`, `sdk.py:982`, case-insensitive). So the recognized id shapes are exactly: **`prefix-hex26`** or **ULID**.

**The hazard — a non-matching id is silently classified as a NAME.** In `create_entity(type='event')`, the four `about*` props are extracted and passed to `proj.create_about_edge(...)` **unconditionally**; the name-resolution fallback runs **only when `not _is_entity_id(value)`**:

- `tortoise/sdk.py:17165-17180` — `aboutSubject`/`aboutObject`/`aboutPoint`/`aboutDocument`; the guards sit at `17167`, `17171`, `17175`, `17179`.
- The fallback is `proj._create_about_edges` (`tortoise/projection/edges.py:271-305`), which walks Subject → Object → Event → Document → Point **by name** and, finding none, mints a Subject stub via `_mint_subject_stub` (`edges.py:74-82`): `MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$name` — i.e. **the id string becomes a Subject's name *and* its id**, plus a spurious `aboutSubject` edge.

This is a **code-inspection deduction, not a pinned test** — and the missing test is itself the gap. The deduction: `_is_entity_id(value)` returns `False` for a non-matching id, so the name fallback runs, `_create_about_edges` walks by name, finds none, and `_mint_subject_stub` mints the stub. **No test in the repo covers a non-matching id:** `_is_entity_id` appears under `tests/` exactly once, in a comment (`tests/test_ingest_validation.py:126`). What the suite *does* pin is the **inverse** — correct handling of a **matching** id:

- `tests/test_about_edges.py:176-205` (`test_create_event_prefixed_id_no_stub`) creates the Subject via `sdk.create_subject(...)`, whose id comes from `_entity_name_id` (`tortoise/sdk.py:1351-1361`) as `sub-<sha256(label:name)[:26]>` — which **matches** `_ENTITY_ID_RE` — and asserts **no** stub is created.
- `tests/test_about_edges.py:221-246` (`test_create_event_prefixed_object_no_stub`) pins the same inverse for the `obj-<26hex>` branch.
- `tests/test_about_edges.py:207-219` pins the name path that must keep resolving.

**A minted format that fails `_ENTITY_ID_RE`/`_ULID_RE` would be silently treated as a name and stub-minted on every `about*` write, and no test in the repo would catch it.** That is the sharpest Stage 3 constraint: *mint ids that satisfy `_is_entity_id`, or migrate all four guards (`sdk.py:17167/17171/17175/17179`) in the same change, and add the missing non-matching-id test.*

**Other surfaces keyed on the current id format:**

- **`supersededBy` / `corrected_by`.** `tortoise/commit_ops.py:480-500` — an id-less legacy Object **synthesizes the canonical id** at write time (`from tortoise.sdk import _entity_name_id; obj_id = _entity_name_id("Object", obj_name)`, `498-500`) to fill a journal payload that would otherwise be `{}`. After Stage 3 that synthesis is **wrong** (it journals a name-derived id that no longer identifies the entity) and must be removed or replaced with the journaled id — Stage 1's job. The `corrected_by` path (`sdk.py:4654-4740` `invalidate_point`; `tortoise/mcp_server.py:1670-1676` `tortoise_invalidate(id, corrected_by_id)`) takes ids as caller-supplied **strings** and MATCHes them directly, inheriting the same id-shape assumption through `_resolve_entity`.
- **`about*` edges.** `tortoise/projection/edges.py:344-376` (`create_about_edge`) resolves both endpoints through `_resolve_entity` (`tortoise/projection/__init__.py:2452-2513`), a UNION over per-label **indexed** lookups on `id`/`eventId`/`url` (`_RESOLVE_BRANCHES`, `__init__.py:2446`). The union is shape-agnostic (it matches whatever string it is handed), so it does not itself constrain the format — but it is the read path the four `_is_entity_id` guards exist to protect, and a mis-classified value never reaches it.
- **`_upsert_object`'s id coalesce.** `tortoise/projection/entities.py:492-557` — `MERGE (o:Object {name:$name}) … ON MATCH SET o.id = coalesce($id, o.id)` (`entities.py:527`): the incoming id wins on MATCH, deliberately overriding a stub's random ulid so the canonical id lands. Under Stage 3 the "canonical id" is the minted opaque id, so this clause becomes the **alias-adoption** point and must adopt via *alias→id* lookup rather than receiving a name-derived id from the caller. `entities.py:449-462` documents the accepted trade-off (a late random-ulid re-mention can re-id a canonical node) that Stage 3 must close.
- **`_entity_name_id` cross-name probe.** `tortoise/sdk.py:16829-16872` — before journaling an `ObjectRegistered`, the SDK probes `MATCH (o:Object {id:$cid, name:$name})` (`sdk.py:16858-16864`) with **both** the derived id and the name, explicitly to harden against a **cross-name sha-digest collision** (`16832`). That conjunct is an artifact of `id == f(name)`; under Stage 3 the id no longer correlates with the name, so the probe's semantics change. The block also carries **accepted divergences** (`16839-16847`), including that deletion is **not** journaled — so a deleted Object resurrects on rebuild today.
- **MCP / hosted-API id contract.** `tortoise/mcp_server.py:2379-2390` (`tortoise_get_entity(id)`, `tortoise_update_entity(id, props)`) and `mcp_server.py:2218-2235` (`tortoise_create_object`/`tortoise_create_subject` take a **name**, not an id) — the MCP id surface is "whatever string resolves". The sharper break is the **documented** contract: `tortoise/hosted_api.py:4516-4523` states in the route docstring *"Wraps sdk.create_object — deterministic id by name, idempotent (a repeat returns the canonical node)"*, and `/v1/objects` (`hosted_api.py:4515`) + `/v1/subjects` (`4552-4553`) take `name` and return the node. Under Stage 3 the id is no longer deterministic-by-name, so that docstring and the idempotency proof it advertises both change: the route must stay idempotent by **name-lookup-then-create**, and any client that cached the `id == f(name)` assumption breaks.
- **`ingest` ref validation.** `tortoise/sdk.py:6928-6938` rejects a bundle-local `ref` **shaped like a real node id** (`_is_entity_id`) because `refs.get(x, x)` would silently address an existing node. Stage 3 must keep this check in sync with the new format, or a node id used as a ref becomes a silent shadow.

**Constraint on the minted format — and why the "cheaper" option is not sufficient.** *Either mint ids that satisfy `_is_entity_id` (`prefix-hex26` or ULID), or treat the four `sdk.py:17167/17171/17175/17179` guards — and the shape check at `sdk.py:6928` — as part of the Stage 3 migration and replace them with an alias-index existence check.*

Minting regex-satisfying ids is the **cheaper** option, and it is sufficient once the migration is *complete* — but it is **broken while legacy aliases coexist (Stages 3–4)**, which is exactly the window it would be asked to cover. The failure is concrete:

- A minted opaque id and a legacy `obj-<sha26(name)>` alias are **shape-identical** — both match `^[a-z]{2,3}-[0-9a-f]{26}$` — and `_is_entity_id` (`tortoise/sdk.py:997-1007`) classifies **by shape alone**.
- So during Stages 3–4 the guard routes a **cached legacy alias** down the **id** path: `create_about_edge` (`tortoise/projection/edges.py:344-376`) resolves it through `_resolve_entity` (`tortoise/projection/__init__.py:2452-2513`), which matches `n.id = $alias` and finds **nothing** — the node's id is now minted, so the alias is no longer any node's `id`.
- The **name fallback is skipped by the guard** (`not _is_entity_id(value)` is `False`), and `_create_about_edges` (`edges.py:271-305`) walks **by name only**, so the alias never reaches the alias index either.
- Result: **every cached legacy handle silently no-ops** — the edge call returns `False` — which is the same silent-misclassification harm this section exists to prevent.

Therefore, for any revision in which legacy aliases are still resolvable, the **alias-index / provenance check is the REQUIRED path, not the optional one**, and **"mint ids satisfying the regex" is *knowingly incomplete*, not merely cheaper** — it holds only if paired with the alias-index lookup (or if the legacy aliases are retired in the same change). This is the repo's own rule: AGENTS.md **"Fix Broken Infrastructure — Never Silently Work Around It"** forbids shipping the shape-matching shortcut as if it were the fix, and **"Good > Easy"** makes the honest path the default. Stage 3 **cannot ship without** one of the two — and in the legacy-alias window, only the alias-index path actually holds.

---

## What we are explicitly NOT doing

- **NOT reinventing the code graph.** Glean's unit-hiding + stacked DBs, and Cursor's Merkle + content-keyed cache, are the published, copyable incremental mechanics. (The negative above is **Medium ⚠️** — a finite-set inference, and pending the aborted third gate; if it holds, there is nothing mainstream to copy for identity, and that part is genuinely ours to design.)
- **NOT chasing rename-detection heuristics.** Git `log --follow`-style similarity is a heuristic over an information-poor model. We have the journal — we can record the rename. Choosing a heuristic when we own the event log would be choosing the harder, worse path.
- **NOT patching the fold a ninth time.** #3573's two P1s are evidence for the model change, not two more patches.

---

## Open questions for the human

1. **Ratify the model?** (R1–R9.) The recommendation is yes — but on the **safe-default** reading of the literature, not a convergent finding: the R1, R3, R4, R7 rules are convergent **within one school** (downstream of Greg Young; pending the primary read), the four source disagreements above are carried openly, and the id-reuse policy (R5) is a **chosen safe default** rather than a sourced mandate. The inversion of what we do today is the part with the strongest evidence — internal. R6 is migration-scoped and should be ratified as such, not as a permanent rule.
2. **Stage 0 only, or commit to the staged plan?** Stage 0 is small and stops the bleeding. Stages 1–4 are the epic's real work.
3. **#3326: land or close?** Options: (a) land it behind Stage 0's fail-closed rule, (b) hold it while Stage 1 lands, (c) close and re-implement on the new model. Recommendation: **(a)** — it contains real, correct work (the R8-independent parts), and Stage 0 makes its residual holes loud rather than silent.
4. **Backfill policy for existing graphs.** Stage 3 needs to assign ids to nodes that have none. Migration, or rebuild-from-journal? **(See `### Stage 3 — blast radius`: the minted id format must satisfy the existing `_is_entity_id` guards, or those guards must be migrated with it — this bounds the answer.)**

---

## Confidence and gaps

**High confidence — non-reuse is the norm (two independent normative sources + one corroborating default):** NIST SP 800-171 §3.5.5 (prevent reuse of identifiers for a defined period) and PLOS Biology 2017 (identifiers must never be deleted or reassigned) are **normative**; PostgreSQL `CREATE SEQUENCE` (`NO CYCLE` is the default) **corroborates that fail-closed is conventional** — an overridable default, not a mandate.

**Convergent within one school (NOT across independent traditions) — pending the Young/KurrentDB/EventStoreDB primary read:** identity must be minted not derived; rename is a mutation event; deletion is terminal; ordering by position not timestamp. These four come from `Fowler / Microsoft CQRS Journey / SoftwareMill / Eventuous / eventsourcing docs` — **one school, downstream of Greg Young** — and **no independent tradition was found for R1, R3, R4, R7**. The internal 8-cycle history (#2977/#3573) independently supports minted identity, rename-as-mutation, and terminal deletion; **ordering by position is literature-only**.

**Medium ⚠️ (inference from absence):** **no *entity-level, name-derived* identity mechanism among the ten systems examined provides rename continuity**; the verdicts are **deductions from primary specs/source, not from statements that discuss renames**. Also Medium: the specific rename-event mechanic as directly documented (inferred from general identity principles); SCD typology via secondary sources only; the `rebuild == live` discipline (practitioner sources, no formal standard); GitHub Blackbird's entity-level rename verdict (absence of documentation); the **Qdrant** verdict (vendor blog, Jul 2026).

**Low ⚠️ single-publisher:** the **generation-counter mechanism** — its entire evidential base is the Linux `open_by_handle_at(2)` man page + `include/linux/exportfs.h`, and it is the claim that arrived through the **aborted third review gate**. Also Low single-source: Aura and Skir as the only productized rename-continuity mechanisms (Aura is a competing product with authoring interest disclosed; Skir is design-stage).

**Pending its completed third gate (a gate status, not a confidence tier):** every claim sourced solely from the research leg whose cycle 3 aborted — the **generation-counter mechanism** and the **"no mainstream system" negative**. Both are re-tagged above; **neither is load-bearing for the decision.**

**Policy choices, not findings:** **R5** (mint a fresh id after deletion) is a **safe default** backed by the non-reuse norm — it is not a convergent finding, and it **closes the framework's open question** (Axon #3323) by choice rather than inheriting its answer. **R6** is **migration-scoped** (see R6 in detail); its Linux counter-argument is partly rejected, and it is not a permanent rule.

**Explicit gaps — do not treat as settled:**
- **Greg Young's primary writings and KurrentDB/EventStoreDB primary docs** were not retrieved (secondhand only). This is the canonical source for event-sourcing identity, it should be read before Stage 3 — and it is **why the R1, R3, R4, R7 bundle is one school, not a convergent finding**.
- **Kimball's SCD primary chapter** — typology corroborated via secondary sources only. The canonical SCD2 mechanism (**new surrogate key per tracked change, continuity via the natural key**) is the *opposite* of a fix for a mutable natural key.
- **ISBN/ISAN/ARK/DOI explicit non-reuse rules** — not retrieved; would strengthen R5.
- Kythe, Stack Graphs, Software Heritage, and Blackbird verdicts are **deductions from primary specs/source**, not from statements that discuss renames.
- The "vacant intersection" claim from the prior scan is a **negative over a finite query set** — unverified absence, not proof.
- **The upstream framework's id-re-creation policy is an open question** (Axon #3323) — R5 answers it by **policy**, not by source.
- **The Linux generation counter is single-publisher** and its analogy is partly rejected; treat the mechanism as a *candidate*, not a transferable result.
- **Stage 3's blast radius was not researched before the decision** — added after verifier review; see `### Stage 3 — blast radius` for the real `file:line` evidence and the id-format constraint it imposes.

---

## Related

#2835 (epic) · #2977 (parent of the retraction lane) · #3326 (the blocked PR) · #3573 (the two open P1 burials — Stage 0 makes them loud) · #3574 (`name[:200]` truncation — Stage 1) · #3377 (unjournaled rename — Stage 2) · #3389 (writer-side second carrier — fixed) · #3303 (connector reopen)

**Prior art:** `prior-art-scan.md` §C. **New primary sources this round:** Kythe `storage.proto` + `kythe-storage.txt`; `github/stack-graphs` `graph.rs` + arXiv 2211.01224; LSP 3.17 specification; Software Heritage `swh-model/persistent-identifiers` + `swhid.org/faq`; GitHub Blackbird engineering blog; NIST SP 800-171r2; PLOS Biology 2017 `10.1371/journal.pbio.2001414`; Linux `open_by_handle_at(2)` + `include/linux/exportfs.h`; PostgreSQL `CREATE SEQUENCE`; Fowler *Event Sourcing* + *Bitemporal History*; Microsoft CQRS Journey `Reference_03_ESIntroduction`; `eventsourcing` 9.1.4 docs.
