---
title: "WS-A design — one write contract for the five doors"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-10
subjects.team: epistemic-team
aboutObjects: tortoise-projection, tortoise-sdk, tortoise-longmem-eval
aboutSubjects: tortoise-write-path-integrity, tortoise-declared-vs-stored
---

# WS-A — One write contract for the five doors

**Parent:** #2820 (WS-A write-path integrity) · **Status:** design v5, awaiting owner approval
**Design-side of:** #2795, #2813, #2814 · **Related:** #2788, #2742, #2825, #2873

> #2820 gate: *"Workstreams A/B/C all require owner approval of a written design before implementation."*
> This document is that design. **Nothing here is implemented until approved.** §6 lists the open
> decisions; §3's fixes are mechanical once the decisions land.
>
> **v2** incorporated a fresh-context design review (13 findings). Load-bearing corrections: D2's
> mechanism is specified (a naive passthrough would crash on dict-valued structural keys), D4 gains an
> explicit deny-list, `content_hash` is classified as **derived** (#2795 names it), and D5 switches to
> snapshot+restore so the P0 bulk-wipe guard stays engaged.
>
> **v3** incorporated a second review cycle (1 new P1, 2 new P2). Corrections: **OD6 rewritten** — v2's
> `replayable=False` for EP props would have silently dropped `confidence` on every rebuild (replay
> *restores* it from the journal; only the payload lane must be blocked); **`derive=` now points at
> cycle-free helpers** (referencing the sdk-private `_content_hash` from `contract.py` is an ImportError);
> **D5's edge claim scoped** to edge-free config labels (no edge-snapshot mechanism exists); the wipe
> guard test rephrased (config survives by restoration, not a filtered `DELETE`); D2's filter allows flat
> lists and its skip-set gains the MERGE key `id`.
>
> **v4** incorporated a third review cycle. Corrections: **OD6(a) scoped to journaled props** — only
> `confidence` is replay-restored; `posterior_alpha/beta` and `lastDreamedAt` are written by
> **unjournaled** EP/dream paths and are lost on rebuild (pre-existing → **#2884**); D7's durability
> assertion scoped accordingly. D2's "flat lists allowed" justification was **factually wrong**
> (`search_keys` is stored as a flat space-joined string; `tags` becomes `:Tag` + `TAGGED` edges) and
> is corrected to "scalar + declared `flatten=list` only". EP-owned set completed (`baseline_source`,
> `inherited_at`, `outdated`), and the second `DETACH DELETE n` site (`:1157`) noted.
>
> **v5** incorporated a SOTA research pass + fresh-context verifier on the `content_hash` decision
> (verdict: CONFIRMED-WITH-CAVEATS). Corrections: the design now **cites the repo's own §11 v3.2 cache
> doctrine** (`ONTOLOGY.md:850-854`) instead of inventing a stricter "derived ⇒ never persist" rule;
> **`pure` becomes a NECESSARY-but-not-sufficient signal** (`content_hash` pure, `embedding` not — see
> **OD9**); the OPERATIVE rule is the per-prop **`replay_source`** (`journal`|`derived`|`none`) in D1; the
> **hash-function immutability precondition** is stated (ids are derived from the hash, so a hash change
> makes `rebuild_all` a migration, not a repair); **v1–v4's "a missing hash is cheap" rationale is
> withdrawn as false** (`_content_exists` has no null-hash fallback → silent duplicates, filed
> separately); **recompute-and-compare drift canary** added as **OD10**; and D2 is explicitly framed as
> the **class-level** fix (give `Point` the **open-set + recompute deny-list** pattern — the deny-list
> itself is **introduced by this design**, D4's `NON_PERSISTABLE_PROPS`; no other layer has one today).
>
> **v5 (revision b)** incorporated a SECOND fresh-context cycle (cycle 6, 2 new P1s + 4 new P2s + 5 P3s,
> all self-inflicted by the v5 edit pass). Corrections: the **duplicated EP-owned block was removed**
> (cycle 5's fix had been inserted *above* the old block, leaving duplicate dict keys whose last-wins
> semantics silently restored the uncorrected `replayable=True`); **`replay_source` is now set on EVERY
> prop** — the six payload props were unset, which would have put the four fields WS-A exists to protect
> *outside* D7's durability union; `none` added to the declared enum and `tags` given an explicit entry;
> the v5 changelog no longer claims `pure` is "the decision rule"; OD9's justification no longer cites a
> deleted rule; D7 gained the golden-vector, `CONTENT_HASH_VERSION`, `replay_source`-completeness and
> non-NULL-derived-hash rows that D1c #2 had referred to but never placed; two dangling sentence
> fragments removed; `Document` added to the `_persist_extra_props` layer list; and two line citations
> corrected (`updatedAt` `:196`→`:194`, `embedding` `:178-184`→`:176-179`).
>
> **Lesson recorded:** cycles 1–4 reviewed *design decisions*; cycles 5–6 reviewed *text*. The v5 pass
> introduced more defects than it fixed, because an edit that adds a field to a declaration must also
> update every consumer of that declaration. Treat D1's block as code, not prose.
>
> **v5 (revision c)** incorporated a THIRD cycle (cycle 7, 2 new P2s + 6 new P3s). Corrections:
> **D2's prescribed `tags` declaration gained `replay_source="none"`** — it contradicted D1 and would
> have red-lined D7's own completeness gate; **the `none` bucket's justification was corrected** (it
> claimed "no journal event exists", which is true for `lastDreamedAt` but **false for `tags`** — the
> `PointAdded` snapshot carries `tags`, the replay writer just ignores it); that discovery is a
> **previously-unknown silent data loss**, filed as **#2897** (every `rebuild_all` drops all `tags` + all
> `TAGGED` edges); D1's block **now parses as valid Python** (a bare `...` was inside the dict literal
> while the same document says "treat D1's block as code") and now **actually DECLARES all twelve
> OD6(b) EP-owned props** (cycle 7 listed them in prose only, leaving OD6(b)'s `payload_writable=False`
> invariant with nothing to bind to); `derive="_now_iso"` (a string among callables) corrected to `ids.now_iso`; `speaker`
> un-mislabeled as payload-sourced; the id-minting enumeration completed (`:19405`, `:19414`, `:19424`);
> the version history's claim that other layers already have the deny-list was corrected at its source;
> a dangling `D1c #5`
> reference removed; and D7 gained a row asserting the `none` bucket is **reported, not silent**.
>
> **v5 (revision d)** — a FOURTH cycle (cycle 8, 2 new P2s + 2 new P3s). Corrections: **the twelve
> OD6(b) EP-owned props are now declared in `POINT_PROPS`, not merely listed in a comment** — without
> this, D2's open-set passthrough would persist a payload-supplied `c_cal`/`posterior_alpha` and
> **overwrite EP state**, the exact failure OD6(b) exists to prevent; and that in turn widens #2884's
> scope from "`posterior_*` + `lastDreamedAt`" to **every** `replay_source="none"` prop. Also: D7's
> declaration-parity row no longer asserts `§4.1 ≡ contract.py` (the surfaces genuinely differ — §4.1
> has 18 Point fields and lacks `tags`/`content_hash`) and is restated as a **diff**, not an equality;
> the key count corrected 12→**22**; the id-minting enumeration gains `sdk.py:1156-1175`
> (`_session_capture_event_id`, which uses `hashlib.sha256` **directly** and so falls OUTSIDE the
> `ids.content_hash` golden-vector guard — noted as a coverage gap in that guard); and the revision-c
> paragraph's two inaccurate claims were corrected.

---

## 1. Problem

A Point reaches the graph through **five doors**, and each door accepts a different subset of the
declared fields. Nothing declares which fields a Point has. The result is silent data loss that varies
by door, and a benchmark that measures a door the product does not use.

This is #2820's *Pattern 1 — Declared ≠ Stored*: the system reports something as stored that never
reaches the graph, with no error raised.

## 2. Evidence

### 2.1 The five doors

| # | Door | Entry point | Acceptance rule | `quote`/`when`/`search_keys`/`source_turn_id`? |
|---|------|-------------|-----------------|---|
| 1 | Commit endpoint | `hosted_api._execute_commit_writes` (`:7936`) | Layer-1 validate; unknown field → **422** | **yes** — enumerated at `:8100-8135` |
| 2 | v2 product capture | `sdk._extract_session_v2` (`:3809-3813`) | none | **no** — silent drop |
| 3 | M2 / EventAPI | `extractor.py` / `mining.py` → `projection._upsert_point_props` | fixed SET list | **no** — silent drop, even live |
| 4 | Direct SDK | `sdk.create_point` | `SET n += $props` | **yes** — but **lost on rebuild** (#2795) |
| 5 | Eval harness | `tools/longmem_eval/ingest_v2._write_payload` | fixed kwargs | **yes** — but **lost on rebuild** (#2795) |

**The product capture (door 2) is the outlier, not the rule.** Three of five doors already persist
these fields. That is what makes this a defect rather than a design choice.

The product writer *already has the values in hand*: the extraction payload carries all four
(`extractor_v2.py:3896-3923`) and `_CAPTURE_PASSTHROUGH_PROPS` (`sdk.py:500`) pulls them out — for the
**API response only**. The reply says "here is the quote, here is the date" while the node stores none
of it. That asymmetry is what made the gap invisible.

### 2.2 The declaration surface (more than four)

The "four declarations" framing is too narrow — the review found several more prop lists that must stay
in sync. The full surface:

| Declaration | Where | Role | D1 treatment |
|---|---|---|---|
| Layer-1 payload schema | `commit_schema.Point` (15 fields) | transient payload; `extra="forbid"` | **source of truth** for payload fields |
| Durable property table | `ONTOLOGY.md` §4.1 | graph properties | **diffed** against `contract.py` (parity test) |
| Replay/live contract | `projection/entities.py::_upsert_point_props` fixed SET list | rebuild **and** door 3 | **replaced** by `contract.py` |
| Capture response projection | `sdk.py:500 _CAPTURE_PASSTHROUGH_PROPS` | public API response whitelist | **kept separate** — see D1b |
| Extractor output contract | `extractor_v2.py` `OUTPUT_CONTRACT` / `_VERBATIM_OPTIONAL_FIELDS` (~`:1005-1024`, `:2189`) | prompt contract | **diffed** (these are what the prompt promises) |
| MCP boundary | `mcp_server.py:715 _SERVER_MANAGED_PROPS` | tenant boundary | **kept** (orthogonal: rejection, not persistence) |
| Commit kind props | `hosted_api.py:13222 _KIND_PROP_KEYS` | commit-door validation | **derived** from `contract.py` |

Currently `ONTOLOGY.md` §4.1 lacks `search_keys` and `source_turn_id`; `_upsert_point_props` also never
writes `speaker` although ONTOLOGY §4.1 lists it. #2742 is the same class (import-time snapshot vs
runtime registry).

**Derivation direction (D1):** `contract.py` is authoritative for *persistence*. `commit_schema.Point`
remains authoritative for the *payload shape*. `_CAPTURE_PASSTHROUGH_PROPS` remains a **separate output
projection** (D1b). Everything else is diffed, not merged.

### 2.3 Why the drift happened

The extractor is shared between product and eval; the **persistence writer was forked**. Each
benchmark-driven extraction improvement (#1533, #1535, #1538, #1539, #1544, #1763, #2165) was applied to
the contract + extractor + **eval writer**, and only sometimes to the product writer. Decisive:

```
$ git show --stat d4da83c57   # #1535 — atomic points, search_keys, speaker via source-turn
 tortoise/commit_schema.py       |  28 +   ← contract gains search_keys + source_turn_id
 tortoise/extractor_v2.py        | 201 +   ← extractor emits them
 tools/longmem_eval/ingest_v2.py |  37 +   ← eval persists them
 tortoise/hosted_api.py          |   4 +   ← commit door gains them
   (tortoise/sdk.py: NOT TOUCHED)          ← product capture writer never learns them
```

(The full stat also touches four test files. Abridged; the `sdk.py` omission is the point.)

`_CAPTURE_PASSTHROUGH_PROPS` (#1529) then whitelisted them into the **API response**, which made the
response look correct and hid the gap. No test compares response props to node props; no test compares
the two writers; and the write-path benchmark grades **prose** (`grading.py:363-389`), so a point scores
1.0 quote-fidelity while storing no `quote` prop.

This is the **write-path half of the inversion already done for retrieval**
(`docs/parity/2026-08-29-retrieval-inversion.md`). Persistence was simply never included.

### 2.4 External validation

- **Dual-write problem** (Confluent, DZone, Auth0): two independent writes with no atomic boundary
  *eventually diverge*. The standard fix is one authoritative write with everything else derived.
- **Anti-Corruption Layer** (Azure/AWS Architecture Center): translate at a boundary; do not let one
  side's semantics compromise the other. The right shape for benchmark instrumentation.
- *"Agent Benchmark Scores Are Measuring the Harness, Not the Model"* (focused.io) — our exact failure.
- **Zep / Graphiti** resolves contradictions by invalidating the **edge** (`valid_at`/`invalid_at`), and
  frames it as *"temporal edge invalidation rather than LLM-driven judgment"* — deliberately not a stored
  verdict. Reinforces D4.
- **PostgreSQL generated columns** — the primary reference for D1c. Postgres **cannot index a *virtual*
  (computed-on-read) generated column**; the error directs you to a **STORED** generated column or an
  expression index. The feature was designed so that a derived value *which needs an index* is
  **materialized** — i.e. persisted, and recomputed on every INSERT/UPDATE. **The analogy supports one
  narrow claim and no more:** a cached derived value may be *materialized when it is needed for an index*.
  Postgres materializes only values **declared** STORED, recomputes them on *row write* only (there is no
  restore/replay notion), and still **reads** the stored value rather than recomputing on read — so this is
  **not** "exactly D1c's shape" and does **not** establish a general recompute-on-replay doctrine. The
  "cannot write a generated column directly" ↔ D2 deny-list parallel is an **analogy**, not an equivalence.
  What the analogy *does* carry over is the requirement that actually matters: **determinism of the
  derivation**, not non-storage (Kleppmann, *DDIA* ch. 10-11: derived data systems materialize their
  output, and the guarantee is that the derivation is reproducible).

---

## 3. Design

### D1 — One persisted-property declaration per layer (authoritative for persistence)

New module `tortoise/projection/contract.py`. Per entity layer (Point, Subject, Object, Document,
Event, Source), one declaration carrying an explicit `source`:

```python
POINT_PROPS: dict[str, Prop] = {
    # `replay_source` is the OPERATIVE rule (D1c #1). Enum: journal | derived | none.
    #   journal — `replayable` because the journaled node snapshot restores it
    #   derived — `replayable` because the replay writer RECOMPUTES it (never copied)
    #   none    — NOT restored by replay → replayable=False. TWO distinct reasons live here,
    #             and they are NOT the same thing (v5 review cycle 7):
    #               lastDreamedAt — UNJOURNALED: no event carries it (#2884)
    #               tags          — JOURNALED but IGNORED: the PointAdded snapshot DOES carry it
    #                               (only embedding + content_hash are stripped, sdk.py:2319-2325)
    #                               but the replay writer never writes it → #2897. Do not
    #                               describe this bucket as "unjournaled"; tags disproves it.
    # EVERY entry MUST set it: D7's durability test is defined as the union of journal +
    # derived, so an unset field silently falls OUTSIDE the very test it needs to pass.
    #
    # All journal-restored (the PointAdded snapshot carries them), except `tags` below:
    # sourced from the payload / capture
    "content":        Prop(str, required=True, source="payload", replay_source="journal", replayable=True),
    "quote":          Prop(str, max_len=200,   source="payload", replay_source="journal", replayable=True),
    "when":           Prop(str, max_len=40,    source="payload", replay_source="journal", replayable=True),
    "search_keys":    Prop(str, flatten=list,  source="payload", replay_source="journal", replayable=True),
    "speaker":        Prop(str,                source="capture", replay_source="journal", replayable=True),
    "source_turn_id": Prop(str | int,          source="payload", replay_source="journal", replayable=True),  # type pending OD1
    # The one explicit EXEMPTION — declared so the omission is visible, not accidental (D2):
    "tags":           Prop(list, source="capture", replay_source="none", replayable=False,
                           owned_by="_sync_tags"),  # raw list + :Tag/TAGGED edges; edge replay out of scope
    # derived — the replay writer RECOMPUTES these; it never copies them from the payload.
    # Per ONTOLOGY.md §11 v3.2 (#398) these are CACHES of a derivation, not payload:
    #   "the derivation is the truth, the cache is a performance artifact."
    # (D1c states which §11 clauses transfer and which do not — §11's own TITLE is
    #  "Reputation (derived, not stored)", so the citation is scoped deliberately.)
    # Excluding them from the preserve-unknown rule is NOT the fix — the fix is the
    # ADDITIVE recompute below. Exclusion alone leaves every rebuilt node content_hash=NULL.
    # `replay_source` DISAMBIGUATES the overloaded `replayable` (v5 review P1): `replayable`
    # means only "survives rebuild_all"; `_emit_event` STRIPS content_hash from the journal
    # (sdk.py:2319-2325) and the #548 snapshot strips it (:1219-1221), so it is NOT
    # journal-restored — while OD6(a) defines `replayable` as journal-restored. Under that
    # reading `content_hash: replayable=True` is false. Keep BOTH fields; do not collapse.
    # `pure` is NECESSARY, NOT SUFFICIENT (D1c #1) — it records why recomputing is
    # reproducible, it does not decide. `derive=` MUST point at CYCLE-FREE helpers:
    # tortoise.sdk imports tortoise.projection at module top (sdk.py:34), so referencing the
    # sdk-private _content_hash from contract.py is an ImportError. Use
    # tortoise/ids.py::content_hash (stdlib-only) and
    # tortoise/embeddings.py::compute_embedding (no tortoise.* imports).
    "content_hash":   Prop(str,    source="derived", replay_source="derived", pure=True,
                           derive=ids.content_hash, replayable=True,
                           guards="skip operators — operators store no content (#548), so a "
                                  "hash over synthesized content matches nothing and would "
                                  "emit canary noise on every operator"),
    "embedding":      Prop(vector, source="derived", replay_source="derived", pure=False,
                           derive=embeddings.compute_embedding,
                           guards="skip operators; require truthy content; wrap in vecf32",
                           replayable=True),  # pure=False — see OD9
    "updatedAt":      Prop(str,    source="derived", replay_source="derived", pure=False,
                           derive=ids.now_iso, replayable=True),  # deliberate recompute — D1c #1
    # OD6(b) EP-owned set — ALL TWELVE are DECLARED here with payload_writable=False, not merely
    # listed in prose (v5 cycle 8 N1): OD6(b)'s invariant has nothing to bind to otherwise, and
    # D2's open-set passthrough would happily persist a payload-supplied `c_cal`/`posterior_alpha`
    # and OVERWRITE EP state — the exact failure OD6(b) exists to prevent.
    # `replay_source="journal"` ONLY for `confidence` (restored from the PointAdded/Promoted node
    # snapshot, entities.py:190). Every other writer (ep.py:266-272, dream.py:258/446/456) emits NO
    # event at all → `"none"`, and #2884's scope is therefore WIDER than "posterior_* +
    # lastDreamedAt": it covers every `"none"` prop below.
    "confidence":      Prop(float, source="ep-owned", replay_source="journal", replayable=True,  payload_writable=False),
    "c_cal":           Prop(float, source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "posterior_alpha": Prop(float, source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "posterior_beta":  Prop(float, source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "ep_alpha":        Prop(float, source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "ep_beta":         Prop(float, source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "baseline_set":    Prop(bool,  source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "baseline_source": Prop(str,   source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "inherited_at":    Prop(str,   source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "lastDreamedAt":   Prop(str,   source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "expiredAt":       Prop(str,   source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    "outdated":        Prop(bool,  source="ep-owned", replay_source="none",    replayable=False, payload_writable=False),
    # Not in POINT_PROPS: `reason` (D4 — NON_PERSISTABLE_PROPS, deny-listed). The ONLY intentional
    # exclusion; every other declared Point prop is above.
}
```

**Consumed by:** door 3's writer, door 4's validation, door 1's enumerated kwargs, door 2's passthrough,
and rebuild's replay. `ONTOLOGY.md` §4.1 gains `search_keys` + `source_turn_id`; a parity test diffs the
two tables.

**`content_hash` is the reason `source` matters.** #2795 names it explicitly, and it **cannot** be
restored by passthrough: `_emit_event` strips it from the journaled point (`sdk.py:2319-2325`, comment
verbatim: *"content_hash is also stripped — it is derived from content"*), the #548 snapshot strips it
(`projection/__init__.py:1219-1221`), and nothing recomputes it. It must be **derived** from `content` in
the replay writer.

**A correction to v1–v4's rationale.** Earlier drafts justified the split with *"a missing hash is the
cheap failure — dedup degrades to a scan."* **That is false for at least one surface and is withdrawn.**
`_content_exists` (`sdk.py:10658`) is a bare `MATCH (n:Point {content_hash:$ch})` with **no null-hash
fallback** — unlike `create_point` (`sdk.py:2490-2500`), which does have one. (`_extract_session_v2` at
`:3798-3806` mirrors it — and v5 cited the mirror instead of the original; corrected.) On a rebuilt graph every
hash is NULL, so `_content_exists` returns `None` for content that *is* present, and `checkpoint()`
files a **silent duplicate**. The honest statement is **both failures are real**: a stale hash returns a
*wrong* node, a missing hash creates a *duplicate* — and duplicates accumulate with ingest volume and
are not self-correcting. The fix is to recompute at every write (including replay) and **not** to treat
the `create_point` fallback as the safety story. The missing `_content_exists` fallback is a
pre-existing bug, filed separately as **#2892** (`_content_exists` has no null-hash fallback → post-rebuild
`checkpoint()` re-files everything as new).

### D1c — Derived values follow the §11 cache doctrine (`pure` is necessary, not sufficient)

v1–v4 invented a rule stricter than the repo's own. `ONTOLOGY.md` §11 (v3.2, #398) already decides this:

> **Derived values may be CACHED, never authoritative:** Source `reliability` is a write-through
> projection of the query-time derivation (recomputed on write events, consistency-checked on read,
> stamped with `reliability_derived_at`) — **the derivation is the truth, the cache is a performance
> artifact.**

**Which clauses transfer — and which do not.** v5 cited §11 as univocal; it is not. §11's *title* is
"Reputation (derived, **not stored**)" and its first bullet is literally *"Not stored (would go stale)"* —
which supports v1–v4's **stricter** rule. The v3.2 clause is the operative one, and it is scoped here
deliberately rather than quoted selectively:

| §11 clause | Transfers? | Why |
|---|---|---|
| "may be **CACHED**" | **yes** | an unpersisted index key is useless — Postgres cannot index a *virtual* generated column and directs you to a **STORED** one |
| "**recomputed on write events**" | **yes** | this is D1's `derive=` |
| "consistency-checked on read" | **no** | dedup *is* the check — a stale hash surfaces as a missed hit |
| "stamped with `reliability_derived_at`" | **no** | the key *is* the hash; a freshness column would be written and never read |
| "**never authoritative**" | **partly — do not overclaim** | `reliability` is a display score; `content_hash` mints **ids** (`pt_<sha>`) and is authoritative *there* |

So the design does **not** say "derived ⇒ never persist". It says: persist the cache, **recompute on
every write**, and do not treat it as authoritative *except* where it mints ids (D1c #2).

1. **`pure` is NECESSARY, not the operative rule.** v5 asserted an *iff* ("safe iff side-effect-free over
   same-node data **and frozen**"). The review falsified it **four ways**, **inside this document**:
   - `updatedAt` is recomputed on replay with wall-clock `$now` (`entities.py:194`) — not a function of
     same-node data, not frozen, and **deliberately** recomputed anyway (now declared `pure=False`).
   - `embedding` is recomputed on replay today (`entities.py:176-179`) while declared `pure=False` (OD9).
   - A version-pinned embedding *would* be `pure=True` — yet is still better **preserved** than
     recomputed (an O(N) model forward-pass at rebuild). Purity does not capture recompute *cost*.
   - "and frozen" was **circular**: that is D1c #2's precondition, not a property of the function.

   **The operative rule is D1's explicit per-prop `replay_source`** (`journal` | `derived` | `none`),
   assigned deliberately rather than inferred, with **no default** — an unset field would fall outside
   D7's durability union. `pure` records *why* a `derived` assignment is defensible; it does not decide.

2. **Hash-function immutability is a precondition — with a CORRECTED mechanism.** v5 claimed `rebuild_all`
   "re-derives ids with the new code". **That is false and is withdrawn.** Replay preserves ids verbatim
   from the journal snapshot (`entities.py:197`, `p["id"]`), and there is **no `content_hash(` call
   anywhere under `tortoise/projection/`** — replay never re-derives an id.

   The real break is **write-time**. `create_point` defaults to `pid = ulid()` (`sdk.py:2533`); content
   addressing is opt-in on specific paths only — the commit door, `_stream_to_payload` (`pt_{sha}`,
   `sdk.py:1519`), event ids (`ev_{sha}`, `:1534`, `:3948`, `:19405`, `:19414`) and content-derived `batch_id`s
   (plus a second `pt_{sha}` site at `:19424`, and `_session_capture_event_id` at `:1156-1175` — which
   mints `ev_<sha>` from `hashlib.sha256` **directly**, not via `ids.content_hash`, so it falls OUTSIDE
   the golden-vector guard below and needs its own pinning). Change the
   hash function and **new** writes mint `pt_<new_sha>` ids that do not match the ids the rebuilt graph
   preserved from the journal, while `_stream_to_payload` still remaps operator `src`/`dst` to the old
   ones → **dedup misses, duplicate Points, orphaned operator endpoints.** The conclusion stands (a hash
   change is a **migration**, not a repair); the mechanism is id *minting*, not id *re-derivation*.

   **Nothing currently enforces this** — `derive=ids.content_hash` references mutable code, so a one-line
   edit to `tortoise/ids.py` silently changes every derivation. D7 therefore adds (a) a **golden-vector
   test** (`content_hash("<fixture>") == "<pinned digest>"`) and (b) a `CONTENT_HASH_VERSION` constant in
   `contract.py` with a documented migration checklist for any bump.

3. **Recompute-and-compare drift canary (optional, OD10) — with a CORRECTED source.** v5 had the canary
   read the node's stored hash. **That is tautological:** `_upsert_point_props` writes `content_hash` in
   the same pass, so the only "stored" value at assert time is the one just computed — and the #548
   snapshot strips `content_hash` (`:1220`), so graph-only Points have none at all.

   The canary must read a **pre-wipe `{id: content_hash}` map** captured in the snapshot phase that
   already runs before the wipe (`projection/__init__.py:1188-1255`). Cost is an O(N) map retained across
   the wipe — **not** "near-zero". Observable: a WARN log line **plus a `drift_warnings` counter in
   `rebuild_all`'s return dict** (a warning nobody surfaces is invisible). **Warn, do not fail**: a hard
   failure turns the disaster-recovery path into an outage, and `PointRevised` legitimately changes content
   after the `PointAdded` snapshot (`sdk.py:4241-4245` journals `new_content`) — a *correct* divergence.

### D1b — The capture response stays a separate output projection

`_CAPTURE_PASSTHROUGH_PROPS` is **not** merged into `POINT_PROPS`. It exists to stop internal
projection state (confidence, status, `operator`/`provenance` dicts) leaking into the public capture
response. D1 governs **persistence**; the response stays an explicit whitelist **subset**.

Consequently D7's parity check is **one-directional**: *every prop in the response exists on the node*
(not the reverse), asserted over the declared extraction-output subset.

### D2 — Replay preserves what it does not recognise — with an explicit skip-set and precedence

> **Class-level, not instance-level.** The review surfaced the deeper framing: the (a) payload fields
> are dropped because **`Point` has no *open-set* preserve mechanism**, not because of a skip-set
> membership decision. `_persist_extra_props` — the deny-list preserve mechanism used by
> `Subject`/`Object`/`Document`/`Event` ×2/`Source` — is **never wired for `Point`**. Live `create_point` is an *open*
> writer (`SET n += $props`); replay `_upsert_point_props` is a *closed* writer (fixed SET list). D2's job
> is to close that gap permanently: give `Point` the same open-set semantics every other layer already
> has, plus an explicit **recompute deny-list** (`embedding`, `content_hash`, `updatedAt`, `_nid`,
> `_graph_id`). Note the deny-list is **introduced by this design** (D4's `NON_PERSISTABLE_PROPS`) —
> no other layer has one today (v5 cycle 7 corrected an earlier claim here that they did).
>
> **⚠️ The precedent is incomplete and must NOT be mirrored literally.** `_persist_extra_props` filters
> only `v is not None` (`entities.py:143-144`) — it has **no type filter**, which is exactly the crash D2
> item 1 warns about for `Point`. So the primitive-type filter is a **new addition to the shared helper**,
> not something `Point` inherits — and the same latent crash therefore exists **today** for
> `Subject`/`Object`/`Document`/`Event`/`Source` if an unknown dict-valued prop ever arrives. Filed as
> pre-existing bug **#2894** — do not fix here; D2's shared-helper change repairs all six layers at once.

`_upsert_point_props` is the **shared live+replay writer** (its own docstring: *"Single source of truth
for Point property parity between apply() and rebuild_all()"*). So D2 changes **door 3's live behaviour
too** — intentional, since that symmetry is the point (door 3 drops props today).

A naive `SET n += $extra` is **wrong** and would crash. Required mechanics:

1. **Value-type filter: scalar only, plus exactly the declared `flatten=list` props.** FalkorDB
   rejects **map/dict-valued** properties (nested structures — lists are fine; `tags` is stored as a raw
   list today, `sdk.py:2578-2581`). `operator` and `provenance` are dict-valued and deliberately
   never persisted (`entities.py:132-149` filters only `None`; `tortoise/api.py:64-75` and
   `sdk.py:5750-5753` put dicts into `event_point`) — exclude them.
   - `search_keys` **is** flattened by declaration: stored as a flat space-joined STRING
     (`_flatten_search_keys_prop`, `sdk.py:913-938`, applied at `:2387`/`:4147`), because FalkorDB's
     fulltext index does not index array-valued properties (`projection/__init__.py:2516-2540`
     migrates pre-R2 arrays back to strings). D1 declares it `Prop(str, flatten=list)`.
   - `tags` **is a genuine raw list node property** (`sdk.py:2578-2581` writes every prop key incl.
     `tags`; `sdk.py:4226-4237` keeps the `TAGGED` edges consistent with `n.tags`;
     `tests/test_sdk.py:726` asserts `["tags"] == ["alpha"]`). D1 now declares it explicitly, so it is
     no longer "undeclared" — the earlier label described the state that D1 fixes. D2's rule:
     `tags` is owned by its own `_sync_tags` path, is **not** passed through
     the generic filter, and **TAGGED-edge replay is out of scope of WS-A**. That last point is a
     **silent data-loss deferral, not a no-op** — replay currently handles `tags` nowhere
     (`rg tags tortoise/projection/` → zero hits), so every rebuild drops the `n.tags` property *and*
     every `TAGGED` edge. Unlike #2884 this is **journaled-but-ignored**, not unjournaled. Filed as
     **#2897** with #2795; D7 must assert it is **reported**, not silently dropped.
     `Prop(list, source="capture", replay_source="none", replayable=False, owned_by="_sync_tags")` so the omission is
     **intentional and visible** rather than an accident of the filter.
   - Otherwise **an undeclared list is denied** — never written raw.
2. **Explicit `_POINT_HANDLED` skip-set** — every fixed SET-clause key, the MERGE key `id`
   (`MERGE (n:Point {id:$id})`, `entities.py:226` — *not* a SET clause, so not covered by item 1),
   structural keys (`operator`, `provenance`, `about_entities`, `extractedFrom`, `_nid`, `_graph_id`),
   and `_META_KEYS`.
3. **Key precedence / ordering.** The passthrough must never overwrite `updatedAt` (stamped with
   rebuild-now, load-bearing for the seq-gated folds — `entities.py:358`) or `embedding` (`vecf32`).
   Declared-props win; passthrough applies only to keys outside the fixed clauses.
4. **Non-persistable deny-list.** See D4 — `reason` must be on it.

On an unrecognised-but-primitive, non-denied prop: persist + emit a drift warning naming the key.

**Why preserve rather than fail-closed:** rebuild is the documented disaster-recovery path. A rebuild
that refuses to run because of an unrecognised prop turns a durability bug into an outage.

### D3 — Instrumentation is an explicit, declared input — not a forked writer

The eval harness stops owning a writer. The shared writer accepts:

```python
instrumentation: dict | None = None   # benchmark-only props, namespaced and declared
```

with `INSTRUMENTATION_PROPS` declared separately from `POINT_PROPS`, so benchmark fields
(`has_answer`, `answer_string_mark`, `lme_question_id`, `lme_session_index`, forged `createdAt`) are
*structurally incapable* of being mistaken for product fields.

**Why:** the fork existed because the harness needed to write benchmark-only data. That is a real need;
the fix is to make it an explicit input to one writer, not a second writer.

### D4 — `reason` is NOT persisted — enforced by a deny-list

`reason` (`NEW`/`REVISES`) is the extractor's **extraction-time opinion**. The structural truth is the
outgoing `CORRECTS` edge that `supersede()` writes.

Verified:
- no production Cypher reads the node property; the only two reads are eval tests self-labelled
  `# (observability)` (`tests/test_ingest_v2_consolidation.py:330`, `tests/test_longmem_runner.py:2008`);
- it is excluded from the content hash as an "LLM artifact" (`commit_schema.py:1054`);
- the commit door does not write it (`hosted_api.py:8114-8135`);
- `docs/ONTOLOGY.md:602` states the principle for the sibling case — *"`challenged` is NOT a state — it
  is a DERIVED condition"*.

**⚠️ Correction to design v1.** v1 claimed the opinion "already has a durable home in the JSONL event
log". **That is false.** The journal entry is a *snapshot of the graph node*
(`sdk.py:2628-2629` → `_emit_event(..., point=self.get_point(pid))`), and `reason` only reaches the
journal *because it was written as a node property*. Remove the property and it leaves the journal too.

So the decision is explicit: **the extraction-time opinion is discarded.** If it is wanted for audit, it
must be journaled deliberately as a non-persisted extraction record — a separate decision, **not** made
by this design.

**Enforcement:** omitting `reason` from `POINT_PROPS` is *not sufficient* under D2 — the passthrough
would persist it (the eval writer passes `reason=` today at `ingest_v2.py:235`, and any tenant can pass
it through the arbitrary-props lane). `reason` therefore goes on an explicit
**`NON_PERSISTABLE_PROPS` deny-list**, applied with a warning in the shared writer.

### D5 — Node classes: derived vs authoritative (fixes #2814)

`rebuild_all` currently runs `MATCH (n) DETACH DELETE n` (`projection/__init__.py:1301`) — wiping
configuration along with derived data.

| Class | Labels | Rebuild behaviour |
|---|---|---|
| **derived / rebuildable** | `:Point`, `:Object`, `:Subject`, `:Document`, `:Event`, `:Source` | wiped and replayed from the journal |
| **authoritative config** | `:PackManifest`, `:PackInstall`, future `:Pipeline`/`:PipelineStage`, graph settings | **preserved** |

**Mechanism: snapshot + restore, not a filtered wipe.** Two reasons:

1. **The P0 production guard depends on the wipe being unconditional.** `_is_bulk_wipe`
   (`projection/__init__.py:74-90`) is the only input to `_assert_test_graph` (`:2045-2075`). Adding a
   predicate to the DELETE (e.g. `AND n.x IS NULL`) reclassifies it as a *non*-bulk-wipe and silently
   **disengages the production guard**. Snapshotting first, then wiping unconditionally, keeps the guard
   engaged.
2. **Edges — scoped, not hand-waved.** `DETACH DELETE` of derived nodes strips any edge between a
   preserved config node and a derived node, and **no edge-snapshot mechanism exists** in either
   precedent — #548 and `:Batch` both snapshot node *properties* only (`:1188-1255`, `:1257+`).
   **D5 is therefore scoped to edge-free config labels.** Verified: no edge into
   `:PackManifest`/`:PackInstall` exists today — those nodes are MERGE-only (`pack_state.py:290`,
   `pack_manifest_store.py:210,224`). If a future `:Pipeline`/`:PipelineStage` needs
   config→derived edges, an edge snapshot `(src, tgt, rel, attrs)` + restore step is required —
   **out of scope here, flagged for #2792.**

This mirrors the existing, proven pattern in the same method: the #548 Point snapshot
(`:1188-1255`) and `:Batch` snapshot (`:1257+`).

> **Second wipe site.** `projection/__init__.py` has a *second* unconditional
> `MATCH (n) DETACH DELETE n` at `:1157` (`rebuild(log)`). Its only caller is test-side, so the
> live risk is low — but if it is ever wired to a product path it needs the same preservation rule.
> One clause, not a scope expansion.

Plus a distinguishable **`config_reset` marker**, so *"never configured"* and *"configured then wiped"*
stop being indistinguishable. This matters beyond tidiness: #2728 makes **absence meaningful** (no
selection marker ⇒ legacy union), so a silent wipe is a **permissions change**, not a settings reset.

A test must pin that the wipe statement **remains** the unconditional `MATCH (n) DETACH DELETE n`
(`:1301`) — config survives by **restoration**, never by a filtered `DELETE` — so `_is_bulk_wipe`
(`:74-90`) and the `_assert_test_graph` production guard (`:2045-2075`) stay engaged.

### D6 — Wire the four fields into product capture

`_extract_session_v2` passes `quote`, `when` (+ `validFrom`), `search_keys`, `source_turn_id` to
`create_point`. The mechanical half of #2813; a no-op once D1 lands.

### D7 — Parity tests (the durable guard)

| Test | Asserts |
|---|---|
| prop durability | every prop the replay writer is **responsible for** survives `rebuild_all` — i.e. every `replay_source="journal"` prop **plus** every `replay_source="derived"` one — including an operator point and an M2 point (the D2 skip-set cases) and `content_hash` (derived). `replay_source="none"` EP state (`lastDreamedAt`, `posterior_alpha/beta`) is **excluded** and tracked separately (#2884) |
| **`replay_source` completeness** | **every** entry in `POINT_PROPS` declares `replay_source` (no default) — an unset field would silently fall outside the durability row above. Fails loudly on an omission. The D1 block parses as valid Python with **22 distinct keys and no duplicates** (v5 cycle 8) |
| **`replay_source="none"` is REPORTED, not silent** | every prop in the `none` bucket is either (a) named in a filed issue, or (b) reported by `rebuild_all` (a `dropped_props` counter / WARN naming the key). Today: `lastDreamedAt` + EP state → **#2884**; `tags` → **#2897**. #2795's 4th indicator requires this — a prop that genuinely cannot be replayed must be *reported*, not dropped silently |
| **hash-function golden vector** | `ids.content_hash("<fixture>") == "<pinned digest>"` — pins the derivation (D1c #2). Guards the write-time id-minting invariant: a change here changes `pt_<sha>`/`ev_<sha>` ids and makes `rebuild_all` a migration, not a repair |
| **`CONTENT_HASH_VERSION`** | the constant exists in `contract.py`, is asserted non-empty, and any bump to it must be accompanied by a migration note — the version is the marker that tells an operator a rebuild is unsafe |
| **derived-props are recomputed, not copied** | after `rebuild_all`, `content_hash` is **non-NULL** on every non-operator point — this is the specific assertion v1–v4 lacked (they excluded `content_hash` from passthrough without adding the additive recompute) |
| writer parity | product writer ≡ eval writer for a shared payload (minus instrumentation) |
| response ⊆ node | every prop in the capture response exists on the node (one-directional, per D1b) |
| declaration parity | **`ONTOLOGY.md` §4.1 vs `contract.py` — a DIFF, not an equality** (v5 cycle 8 N2). The surfaces genuinely differ: §4.1 has 18 Point fields and lacks `tags` + `content_hash`; `POINT_PROPS` has 22 and omits the structural keys (`id`, `pointKind`, `is_operator`, `op_type`, `status`, `authoredBy`, `validFrom`, `validTo`, `createdAt`, `is_episodic`) that belong to `_POINT_HANDLED`, not the declaration. The test asserts the **known, enumerated** difference and fails on any *unexpected* divergence — an `≡` assertion could never pass. Extractor `OUTPUT_CONTRACT` diffed the same way |
| deny-list | `reason` never reaches a node from any door, with a warning |
| wipe guard | `_is_bulk_wipe(<new wipe>) is True` |
| benchmark coverage | `tests/eval/write_path` grades the declared props, not just prose |

---

## 4. What the design fixes

| Issue | How | Status |
|---|---|---|
| #2795 rebuild drops live-only props | D1 (`content_hash` **derived**) + D2 (passthrough for the rest, primitive-filtered) | **closed for the declared props** — v1 missed `content_hash`. **Two known exceptions, both reported rather than repaired, per #2795's 4th indicator:** unjournaled EP state (**#2884**) and `tags` (**#2897** — journaled but ignored). Indicator 4 is satisfied only once `rebuild_all` reports the `none` bucket |
| #2813 capture drops E3 fields; eval lane diverges | D1 + D3 + D6 — one writer, one declaration, instrumentation as input; D7 guards it | **closed in principle** |
| #2814 rebuild wipes config | D5 — snapshot+restore, `config_reset` marker, guard preserved | **closed in principle** |
| #2788 (event state nothing reads) | out of scope — same class, separate fix | — |
| #2742 (import-time snapshot drift) | out of scope — D1's parity test is the same pattern | — |

---

## 5. Sequencing

| # | Step | Closes | Risk |
|---|---|---|---|
| 1 | **D1** — `contract.py` + declaration-parity test. No behaviour change. | — | none |
| 2 | **D2** — skip-set + primitive filter + precedence + `content_hash` derivation + drift warning + prop-durability test. **Lands in the shared writer ⇒ also fixes door 3's live drop.** | #2795 | medium — touches the replay *and* live writer |
| 3 | **D6** — wire the four fields into product capture + response ⊆ node test. | #2813 (product half) | low |
| 4 | **D3** — shared writer + instrumentation input; delete `_write_payload`; repoint LongMemEval. | #2813 (eval half) | **high** — benchmark comparability; baselines re-blessed here (OD5) |
| 5 | **D4** — deny-list + remove the eval `reason=` write + re-point the two observability tests. | — | low |
| 6 | **D5** — snapshot+restore + `config_reset` marker + guard test. | #2814 | medium — touches the P0 guard surface |

Steps 1–3 are independently shippable. Step 6 is independent of 1–5. Step 5 can ride with step 4.

---

## 6. Open decisions for owner (decision register)

| ID | Question | Recommendation | Consequence if different |
|---|---|---|---|
| **OD1** | Canonical graph form of `source_turn_id`: integer index, or turn-node ref string? | **Ref string is currently load-bearing.** Eval turn ids are `lme:{qid}:s{si}:t{idx}` (`ingest_v2.py:211`; call sites `:587`/`:597`) — keyed on the **dataset question id**, not the graph `session_id` — and the read path that derives speaker does not fetch `lme_question_id` (`ingest.py:313-341`, `retrieve.py:469-506`). An integer helper would have to be `turn_node_id(qid, si, idx)`, requiring `lme_question_id` added to the read fetch. **Recommendation: defer the type decision to step 3; declare it `source_turn_id: Prop(str\|int, source="payload")` meanwhile.** | Hardcoding either form in D1 now risks a wrong declaration. |
| **OD2** | Config preservation mechanism: filtered wipe / snapshot+restore / refuse? | **Snapshot+restore** (D5) — keeps `_is_bulk_wipe` engaged. | A filtered wipe silently disengages the P0 production guard. Refusing blocks the recovery path. |
| **OD3** | Instrumentation: declared prop set or a separate `:Instrumentation` label? | **Declared prop set** (`INSTRUMENTATION_PROPS`), no new label. | A new label is an ontology change (WS-B) and needs its own rebuild class. |
| **OD4** | Door 3 (M2/EventAPI): fix in this pass? | **Yes** — D2 lands in the shared writer, so door 3 is fixed by construction. Making it rebuild-only would require a second code path (the divergence we are removing). | Deferring leaves door 3 dropping props live. |
| **OD5** | Re-bless eval baselines in step 4? | **Yes**, same PR, before/after numbers recorded on #2813. | Splitting leaves the harness reporting numbers from a path that no longer exists. |
| **OD6** | **NEW** — Which props may the *payload/instrumentation lane* write, vs which are EP-owned? | **Two separate rules, not one.** (a) **Replay restores only what is journaled.** `confidence` IS restored — pass-1a's `n.confidence=coalesce($cf, n.confidence)` (`entities.py:190`), `$cf` from the journaled node snapshot (`sdk.py:2629`; `get_point` returns raw `properties(n)`, `sdk.py:6062-6076`), and `ConfidenceChanged` is an explicit rebuild no-op (`projection/__init__.py:1513`) with no post-rebuild re-derivation hook. **`posterior_alpha` / `posterior_beta` / `lastDreamedAt` are NOT restored** — their writers (`ep.py:268`, `dream.py:258/446/456`) emit **no journal event at all** (grep `_emit_event` over ep/dream/analyze → empty; `ConfidenceChanged` is emitted nowhere), and the #548 snapshot skips log-covered points (`:1216`). This is a **pre-existing gap this design does not close** → **filed as #2884**. (b) The **payload/instrumentation lane must NOT overwrite** EP state — `payload_writable=False`. EP-owned/derived set = `confidence`, `c_cal`, `posterior_alpha`, `posterior_beta`, `ep_alpha`, `ep_beta`, `baseline_set`, `baseline_source`, `inherited_at`, `lastDreamedAt`, `expiredAt`, `outdated`. | v2's `replayable=False` would silently drop `confidence` on rebuild. v3's blanket "replay restores EP state" would make D7's durability test unsatisfiable for the unjournaled props. |
| **OD7** | **NEW** — Is the extraction-time `reason` opinion wanted for audit at all? | **Discard** (D4). If wanted, journal it deliberately as a non-persisted extraction record. | Choosing "journal it" is a small additional scope in step 5. |
| **OD8** | **NEW** — Where does the `config_reset` marker live: graph property on a singleton node, or operator node? | **Singleton `:GraphMeta` node property.** | An operator node would be rebuild-wiped with the derived class. |
| **OD9** | **NEW** — `embedding` is recomputed on replay (`entities.py:176-179`) but is **not pure**: it depends on the embedding model/version; D1c #1 gives `pure` as a necessary signal for a `derived` assignment. Options: (a) keep recomputing and accept that a rebuild under a new model changes vectors, (b) journal the model id + vector and restore, (c) recompute + record the model id so drift is detectable. | **Choose (c); implement in a follow-up issue. No WS-A code change.** `pure=False` is already declared in the contract so the gap is visible. | Out of WS-A scope (the four fields + durability contract). Changing embedding behaviour would move every retrieval baseline — that is a separate, measurable change, not a silent side effect of a durability fix. |
| **OD10** | **NEW** — Should replay assert `recompute(content_hash) == stored_hash` (drift canary), or recompute blind? **Corrected in v5: the source must be a pre-wipe `{id: content_hash}` map from the snapshot phase (`projection/__init__.py:1188-1255`)** — the node's own value at replay time is the value just written (tautology), and the #548 snapshot strips it (`:1220`). | **Warn, do not fail.** Observable = WARN log + a `drift_warnings` counter in `rebuild_all`'s return dict. Cost = an O(N) map held across the wipe. | Blind recompute silently repairs a partial write or hash-function drift — exactly the class of bug that has been invisible here. But `PointRevised` legitimately changes content after the `PointAdded` snapshot (`sdk.py:4241-4245` journals `new_content`), so a pre-overwrite comparison flags a **correct** divergence and a hard failure would be wrong. Warn gives the signal without the false positive. |

---

## 7. Explicitly out of scope

- The **value layer** (#2782, #2817) and the **pack value-declaration surface** (#2818) — WS-B/WS-C.
- `:Pipeline` / `:PipelineStage` themselves (#2792) — this design only ensures they *survive* rebuild.
- #2788 (event-carried state nothing reads) and #2742 (import-time snapshot) — same class, separate fixes.
- Restoring the missing `docs/audit/2026-08-29-product-cohesion.md` (#2825).
