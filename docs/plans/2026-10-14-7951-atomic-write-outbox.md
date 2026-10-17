---
title: "#7951 increment 1 — the outbox on `create_point`: append the record, then write the cache"
type: engineering
domain: platform
doc_status: draft
created: 2026-10-14
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7951, #7950, STORAGE-ARCHITECTURE.md §17.1, tortoise/sdk.py create_point, FalkorDB cache"
---

# #7951 increment 1 — the outbox ordering on `create_point`

**Parent epic:** `#7879` · **Issue:** `#7951` (P0-blocker) · **Capstone:** `#7950`
**Architecture of record:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner ruling, 2026-10-08),
§17.1 (the write-path rule), §17.2 (coherence + the completeness marker)
**Adopted decision (do not re-open):** issue comment `6170585712` — mechanism (1), the atomic write **in
the OUTBOX form**, plus mechanism (2), the per-projection watermark `last_applied_seq`.
**OVERRIDES (carried from `#7951`):** the convergent single-write-authority default — the raw handle
(`proj.g.query` / `proj.g._g.query` / `proj.db.select_graph`) remains an ALLOWED direct cache write until
the atomic write lands, because that handle is the product's own live write path and a blanket refusal
breaks it (`#8389`). Deliberate and temporary; closure route is mechanism (1).

**Status measured at:** `origin/main` `72e42deea` (2026-10-14). Branch
`fix/7951-atomic-write-outbox`, worktree `.worktrees/7951-atomic-write`.
**Review:** scope-verify cycle 1 = `ISSUES FOUND`; cycle 2 = `ISSUES FOUND` (adversarial cap = 2 cycles).
Every finding is answered below or declared as a residual; the exit is an
`[ADVERSARIAL-BOUND]` exit, recorded in the PR — never reported as clean.

---

## 1. Why this increment

The capstone `#7950` proved the chain **built but not wired**: on an ARMED journal-backed projection
`proj.apply` and `proj.query` are refused, but `proj.g.query("CREATE …")` writes the cache with **0
journal records** (`docs/evidence/7950-capstone/s1-bypass-probe.log`); 442 mutating `.query(` sites in 38
files, **288 on the raw handle**. The 288 cannot be closed by blocking the handle (`#8389`: the handle
*is* the SDK's own live write path). They must be closed by **routing the doors through the chokepoint**.

The adopted decision names the shape: **one atomic write, in the OUTBOX form — the journal append is the
transaction, and the cache is projected from the record.**

**This increment routes the single highest-traffic door — `TortoiseSDK.create_point` — through the
outbox ordering.** Today it writes the Point to the cache **before** the record exists (the recorded
apply-first deviation, pinned by `tests/test_7951_enforce_atomic_write.py`). After this increment the
record is the commit point: the cache write is only attempted once the truth holds the record, and a
failed cache write leaves a record that a later replay materialises.

## 2. Scope of THIS increment (one deliverable)

**`create_point`'s FRESH-CREATE path appends the `PointAdded` record BEFORE it writes the Point to the
cache, and the journal payload is built from the write intent (every property the cache write will
write), not read back from the cache.**

That is the ordering half of mechanism (1), on the highest-traffic path. It does **not** claim the whole
of §17.1, the raw handle is not closed, and the cache is still written by the `CREATE` (which writes
exactly the record's properties) rather than by `rebuild_from_record` — residuals §6.

### What changes, precisely

Today (`tortoise/sdk.py:5877-6777`): builds `_create_map`/`_create_params` → `CREATE (n:Point {…})` on
the raw handle → `_sync_tags`/`_link_source` raw MERGEs → `get_point` read-back → `_emit_event("PointAdded",
point=_snapshot)` — **cache ahead of truth**.

After this increment (fresh-create path only):

1. builds `_create_map` / `_create_params` (unchanged);
2. **builds the journal `point` payload from `_create_map`/`_create_params`** — the write intent, which
   is every property the `CREATE` writes (including the `#2199` baseline fields, `embedding`,
   `ep_dirty*`, `sourceVersionTransit`, caller props; `content_hash` rides `props` and is recomputed on
   replay by `_POINT_DENY`, #2795);
3. **`_emit_event("PointAdded", {"id", "kind", "content_hash"}, point=payload)` — the COMMIT POINT.**
   If the append failed, the cache is **NOT** written; `create_point` returns without a node (derived
   can never precede truth). The signal is `_emit_event`'s **returned per-call outcome** (`False` on a
   failed append; the JSONL `log.append` handler, `sdk.py:5752-5760`) — NOT the shared
   `_journal_write_failures` counter delta, which a concurrent writer on the same instance can move
   (`#5612`); the sibling doors still use the delta (§6.10);
4. the cache write — `CREATE (n:Point …)`, `_sync_tags`, `_link_source` — **unchanged**, but now wrapped
   in `with _live_write_scope("create_point"):` so the door declares itself to the §17.1 guard;
5. `get_point(pid)` read-back (unchanged) as the write-landed proof; `_mark_dirty` / in-memory mirrors
   unchanged.

### The pre-fold snapshot builder

A new private module-level helper evaluates each `_create_map` expression against `_create_params`:

| expression form | value |
|---|---|
| `"true"` / `"false"` | `True` / `False` |
| `"$name"` | `_create_params["name"]` |
| `"vecf32($name)"` | `_create_params["name"]` (RAW; the record is the intent — the node stores the narrowed form) |
| anything else | **raise** — fail-closed, never mis-journal |

A test asserts `set(payload) == set(_create_map)` (no key dropped) and that an unknown expression
**form** raises.

## 3. Ordering contract, stated as a rule (with its pre-append writes named)

**Journal-first, then project** — already recorded at the module top of `tortoise/sdk.py`, argued from
the two crash windows: die between append and cache ⇒ the cache **lags**, replay heals it; die between
cache and append ⇒ the cache is **ahead of truth with no repair path**. The rule is the order whose
residual window is replay-healable.

**Three writes still precede the append, and they are NAMED rather than hidden** (cycle-2 finding):

| pre-append write | class | why it stays |
|---|---|---|
| `_advance_ep_version` → `MERGE (m:EpMeta) SET m.ep_version` (`sdk.py:6505,16578`) | control-plane epoch | its value is written **into** the payload (`ep_dirty_at`), so it must precede the record; `rebuild_all` has zero `EpMeta` references — it is not derived from the journal |
| `_emit_event` → `:GraphEvent` append (`sdk.py:5613`) | **truth**-layer (the graph's own journal, `STORAGE-ARCHITECTURE.md` §3) | §17.1 constrains derived-vs-truth; this is truth-vs-truth and is not in scope |
| `resolve_source_versions` → `SET s.canonicalUrl/s.urlAliases` (`sdk.py:6537`, `source_identity.py:216`) | an idempotent adopt-on-touch on a `:Source` | the adopted value is written **into** the payload (`sourceVersionTransit`), so it must precede the record. **Residual** — named, not claimed closed |

The ordering assertion is therefore scoped to the **Point statements** (`CREATE`/`MERGE` on `:Point` and
its edges), and a test pins that scope.

## 4. Adversarial Threat Surface (bounded: cap 2 cycles)

| # | threat class | the test that must cover it | fails pre-change? |
|---|---|---|---|
| T1 | the Point cache write happens before the journal append | spy on `proj.g.query`; assert the `PointAdded` line is present BEFORE any Point `CREATE`/`MERGE` (scoped to Point statements per §3) | **YES** |
| T2 | a failed append still writes the cache (derived ahead of truth) | monkeypatch the JSONL `log.append` to raise; assert **no Point exists** and `_journal_write_failures` incremented | **YES** |
| T3a | the payload drops a property the `CREATE` writes | the payload equals the exact expected map (not just `set(payload) == set(_create_map)`, which a builder iterating `create_map` satisfies structurally — #7951 review) | no (guard — the builder is closed over the known forms) |
| T3b | the payload drifts from the live node (live ≠ replay) | live `properties(n)` == `rebuild_all` properties as full maps **excluding `updatedAt`/`embedding`/the EP-owned `baseline_*` fields** (recorded residual 6: the fold deny-lists the latter), plus the **edge set** as `(type, target, edge-properties)` — non-empty guard on each. A PARITY guard, not a defect detector: on `origin/main` the payload IS the read-back, so it cannot fail there (#7951 review) | no (guard) |
| T4 | an unknown `_create_map` expression form is mis-journaled | helper raises on an unknown form | **YES** |
| T5 | the commit survives a failed cache write | force the `CREATE` to fail once; assert the journal holds the record and a later `rebuild_all` materialises the node | **YES** |
| T6 | a double append (two `PointAdded` per create) | exactly one line per fresh create (`dedup=False`) — mutation guard | no (guard) |
| T7 | the pre-fold payload is not the record actually replayed | `rebuild_all` on the journal materialises the point (T5 covers the failure arm; this is the happy arm) | **YES** |
| T8 | the record carries the RAW caller object, so it either (a) breaks the append on a non-JSON value or (b) claims a property the store DROPS | a `datetime`/`Decimal`/`bytes`/`set` prop lands the write, appends one encodable record and does not touch `_journal_write_failures`; a nested-dict prop is OMITTED from the record; a driver-coerced number rides as the parsed literal | no (guard — a MUTATION guard, NOT a pre-change detector: on `origin/main` the payload IS the post-`CREATE` read-back, so the offending object never reaches `json.dumps` and the test passes there (measured by the #8697 review). It reddens only under the mutation it names: journal the raw caller value) |

## 5. Wiring Check

| touch point | type | covered by | status |
|---|---|---|---|
| `create_point` append-before-cache | code | `tests/test_7951_create_point_outbox.py` (T1, T2, T6) | planned |
| pre-fold snapshot builder | code | same (T3a, T4) | planned |
| rebuild parity (maps + edge set) | code | same (T3b, T7) | planned |
| **flip the apply-first pin** | test | `tests/test_7951_enforce_atomic_write.py::test_create_point_is_apply_first_a_recorded_deviation` → assert `cache_at_journal == [0]` and rename | **must edit** |
| **update the landing test** | test | `tests/test_7209_write_landing.py::test_create_point_journals_no_point_that_is_not_there` — under the outbox the record IS committed before the read-back guard; rewrite to the new contract | **must edit** |
| **delete contract exception 2 / the `create_point` deviation sentence** | docs | module-top contract `tortoise/sdk.py:2672-2687`, `KNOWN DEVIATIONS` | **must edit** |
| **register the new test file** | config | `config/ci-surfaces.yml` on the same surfaces as the sibling `test_7951_*.py` (`core`, `api`, `ep`, `onboarding`, `sdk`, `eval`); `--integrity` is a hard gate | **must edit** |
| MCP / public SDK surface | none | `tools/surface_manifest.py check` (82 rows) | no surface change |
| executable demonstration | evidence | `docs/evidence/7951-create-point-outbox/` | planned |

## 6. Residuals — declared, not absorbed

1. **The raw handle stays open** for the other ~287 sites; this routes ONE door. The `OVERRIDES:` line
   above remains in force.
2. **The cache is written by the `CREATE`, not by `rebuild_from_record`.** The ordering + "the record is
   the commit point" arrive here; the record projection + `last_applied_seq` watermark (increment 2) do
   not. The `CREATE` writes exactly the record's properties, so live materialisation and replay
   materialisation read the same intent.
3. **The dedup-hit path is untouched** — `create_point(dedup=True)` on a hit routes to `update_point`
   (`sdk.py:6378`), still apply-first. The claim is scoped to the **fresh-create** path.
4. **`WriteNotLandedError` is kept** (read-back-empty), with its contract text updated to state the
   truth may already be committed. Retry-safety of a committed append needs the idempotency key/watermark
   (increment 2) and is recorded, not claimed.
5. **The pre-append writes of §3** (`:EpMeta`, `:GraphEvent`, the `:Source` adopt-on-touch) are named.
   `:GraphEvent` in particular is a truth-layer write to the cache; its move to the record is not this
   increment.
6. **The pre-existing replay-parity defect the fold exposed** (cycle-2 P0): `_POINT_DENY` drops
   `ep_alpha`/`ep_beta`/`baseline_set`/`baseline_source` on replay (`entities.py:1502-1510`, no SET
   clause), so a `rebuild_all` does not restore the `#2199` baseline the live create wrote. **Not
   introduced here** (this increment does not fold live); **Filed: #8699.** It is why replacing the
   `CREATE` with the fold was rejected for this increment.
7. **The append-failure arm leaves an orphan `:GraphEvent`** (round-3 verify P1). `_emit_event` writes
   the `:GraphEvent` store BEFORE the JSONL append (`sdk.py:5653-5683` vs `5814`), so when the JSONL
   append fails and the cache write is now skipped, the graph event store holds a `PointAdded` for a
   Point that was never written. Pre-existing for `_apply_and_journal`/`_doc_write` (same shape), and
   **extended to `create_point` by this increment** — named rather than hidden. `WriteNotLandedError`
   for this arm no longer claims "nothing was journaled"; it names the orphan event. Fixing the order
   inside `_emit_event` (JSONL before `:GraphEvent`) is a separate unit because `_emit_event`'s graph
   branch must still run when no JSONL is configured. **Filed: #8696.**
8. **`aboutEntities` stays a node property live** (the `CREATE` writes it) and is dropped by the replay
   fold — the same parity class as (6). `create_point`'s return shape is unchanged by this increment.
9. **The payload states the store's OUTCOME, not the raw caller object** (the #7951 review P1).   `_journal_snapshot_from_create_map` puts every value through `projection._stored_prop_value`, so a
   JSON-native value rides verbatim, a driver-coerced number rides as the parsed literal, and a value
   the store keeps no property for is OMITTED. **CLOSED for caller props by `_sanitize_props`, not by the
   guarded handle**: a number the numeric domain ALTERS (an int outside INT64) is refused at prop
   INTAKE (`tortoise/sdk.py::_sanitize_props` → `numeric_domain.reject_unrepresentable_number`, #4647)
   BEFORE any append, so the "refused on the cache write after a committed append" ordering is NOT
   reachable on `create_point`. Measured on the #8697 head for `2**70`, `{"k": 2**70}`, `[2**70]`,
   `Fraction(2**70)` and `Decimal(2**70)`: `ValueError` with `journal_delta == 0`, zero `PointAdded`
   records. The one remaining route to that ordering is a future non-prop `_create_map` value (a
   server-side expression, which the builder fails closed on) — named, not claimed closed.
10. **The SIBLING doors still gate on the `_journal_write_failures` counter delta** (`_doc_write`,
    `_apply_and_journal`, `_create_entity`). `create_point` now gates on `_emit_event`'s returned
    per-call outcome (`True`/`False`/`None`), which removes the shared-state read for this door; the
    same change to the siblings is a separate increment because their gates are pinned by their own
    tests. The shared root — concurrent appends on one instance — is **#5612**.

## 7. Next increments (recorded so the next lane does not re-derive)

- **2** — the record projection on the live path + `last_applied_seq` (journal + record in ONE Postgres
  transaction); the dedup-hit/`update_point` door.
  **LANDED SO FAR: the `update_point` DOOR (increment 2a)** —
  `docs/plans/2026-10-17-7951-update-point-outbox.md`: the unguarded revision arms append
  `PointRevised` before the cache write and a failed append skips the cache; the PROMOTE arm stays
  apply-first (its guarded `WHERE` can refuse) and is pinned in both directions. **STILL OPEN:** the
  record projection on the live path and `last_applied_seq` — still zero production call sites.
- **3** — the cache driven from the *record*, and the fold made the live Point writer (which requires
  fixing residual 6 first: give `_upsert_point_props` presence-gated `baseline_*` clauses).
- **4** — the remaining raw-handle doors (`_sync_tags`, `_link_source`, deletes, `onboarding/state.py`,
  `onboarding/seed.py`, `hosted_api.py`, `__main__.py`, connectors), then gate the handle.
- **5** — wire `reconcile` as the S2 backstop (observed, not merely checkable).

## 8. Rejected alternatives

1. **A blanket refusal on the guarded handle `proj.g.query`.** REJECTED — `#8389` verbatim; carried as the
   recorded `OVERRIDES:` departure.
2. **Replace the raw `CREATE` with `proj.apply` (the fold) in this increment.** REJECTED — cycle-2
   reproduced that the fold **drops** `ep_alpha`/`ep_beta`/`baseline_set`/`baseline_source`
   (`_POINT_DENY`, no clause), so a born-live decide part would lose its `#2199` prior on the write
   itself. That is a replay-parity defect to fix on its own (residual 6), not to smuggle into a P0
   ordering fix.
3. **Extend the lexical/AST inventory.** REJECTED — `#8315`'s terminal finding: no lexical scan can be
   sound by construction.
4. **Wait for the whole 288-site surface.** REJECTED — that is how the P0 stays open; the adopted decision
   requires reviewable increments.
