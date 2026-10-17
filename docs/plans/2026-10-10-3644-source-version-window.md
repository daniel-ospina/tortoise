<!-- research-path: docs/plans/2026-10-10-3644-source-version-window.md -->

# Plan — #3644: the `:Source` version WINDOW (the `validFrom`/`validTo` half of ONTOLOGY §4.6 *Versioning*)

**Issue:** daniel-ospina/tortoise#3644 (P1-severe, `enhancement`)
**Branch:** `feat/3644-tm` (base `90f61ec59`)
**Tier:** standard (one file of production logic + one writer + docs; the *issue* is
broader than this PR — see §7)
**Scope of this PR:** **`Refs #3644`, not `Closes`** — the Source row only.

---

## 1. Confirmed problem (measured, not taken from the issue body)

The issue body is older than main in several places (line numbers, the `capturedAt`
row). What is true on `90f61ec59`, measured:

| Claim | Measured state |
|---|---|
| `_upsert_source` writes no `validFrom`/`validTo`/`expiredAt` | **TRUE** — `git grep -n 'validFrom' tortoise/projection/entities.py` returns only Point-lane hits (`:872`, `:1216`, `:1258`, `:2303`, `:2380`); the Source MERGE/SET clauses carry none |
| `SourceVersioned` carries no window field | **TRUE** — `tortoise/sdk.py:25653-25666` emits `previousContentHash` + the payload; no `validFrom`/`previousValidTo` |
| `docs/durability-posture.md` R2 reads *"declared, never written"* | **TRUE** — R2's last row |
| §4.7's Source column reads ❌/❌ for the window | **TRUE** — §4.7, per-type presence matrix |
| `Event.capturedAt` is still only on two write paths | **FALSE (stale)** — it is now written by `sdk.py:24361` (session capture), `hosted_api.py:14345`, `hosted_api.py:14367` and read at `sdk.py:8272`. The issue's two named paths (`hosted_api.py:9361`, `sdk.py:18527`) no longer exist at those lines. That row is **not** in this PR's scope; the §4.5 matrix cell stays ⚠️ and the residual is recorded in §7 |
| `Object`/`Subject` windows | **still ❌** — `_upsert_object`/`_upsert_subject` write neither, and §4.2/§4.3 cells are unchanged by this PR |

**Confirmed problem definition.** `ONTOLOGY.md` §4.6 declares a `:Source` **version
window** and states that *"the journal record that opens a new version closes the old
one in the same act; it is never a later repair"*. `#5024` landed the *version-anchor*
half (a hash transition journals `SourceVersioned` naming `previousContentHash`; a
no-op re-fetch journals nothing; `rebuild_all` reproduces the node field for field) and
explicitly left the *window* half open. So today the transition record names the
superseded **version** but **closes no window**: an unclosed window reads as *"still
true"* indefinitely — the failure mode the model exists to prevent — and *"closes the
old one in the same act"* is an intention, not a recorded fact. The author's own scope
note on the issue (`2026-09-24`) names exactly this: *"this issue's Source row is now
the only thing standing between §4.6 and the code"*.

**Why the Source row is the right unit for one PR** (and the rest is not silently
absorbed): the `expiredAt` half of the issue is **blocked on a decision that does not
exist yet** — the body itself says *"Removal (once #3590 S2 defines it) sets
`expiredAt`"*, and **#3590 is still OPEN**. Implementing `expiredAt` now would import
the very defect the body warns about (Point's `expiredAt` cannot distinguish *replaced*
from *withdrawn*, because the removal semantics are undefined). The `Object`/`Subject`
rows are live and unblocked, but Object supersession needs the successor window on the
`ObjectSuperseded` payload — a second design, not a mechanical copy of this one.

## 2. Design decision (recorded here — this is the relay for it)

**D1 — a version's valid-time start is the instant the version was OBSERVED.** A
`:Source` version has no content-side date: a version *is* "the content as read at an
instant", so the observation instant is definitional, not the borrowed write clock
§4.1/#3654 forbids for a Point (a session's occurrence date is a fact about the
content, so synthesizing one from the ingest clock asserts what we do not know). It is
also the only instant at which §4.6's mandated close **can** happen: `validTo` of the
old = `validFrom` of the new.

**D2 — one clock, two names for the act.** `create_source` mints the instant **once**
(the `_version_instant` that already fed `updatedAt`, so no second clock read exists to
disagree with) and carries it as `validFrom`. The `SourceVersioned` record carries
**both** window facts: `validFrom` (the new version's open) and `previousValidTo` (the
prior version's close). They are equal by construction — contiguity is guaranteed, not
computed by a fallback — which is what §4.6's *"closes the old one in the same act"*
means. `previousValidTo` is the same information under the name §4.6 uses for the act;
it is **not** a second derivation (the objection #5024 raised against recording the
recomputed `version` ordinal) because both values are one minted token.

**D3 — the node holds the CURRENT version, and its window is OPEN.** `validTo` is
**not** a node property of a Source: with one node per `url`, closing the current
version would claim the identity itself had expired, and Cypher cannot write a NULL
property anyway — **absent IS the open end**, exactly as for an un-superseded Point.
The prior version's close is therefore a **record-only** fact, and the PR keeps it off
the node (`_SOURCE_HANDLED`, exactly like `previousContentHash`).

**D4 — the window is recorded, never synthesized at replay.** `_upsert_source` writes
`s.validFrom = $vf` (ON CREATE) and, gated on the same stored-hash difference as
`version`/`updatedAt`, `$vf` itself (ON MATCH) — **not** `coalesce($vf, $now)`
(which would stamp the replay clock) and **not** `coalesce($vf, s.validFrom)`
(which would let a new version inherit the prior version's start when its record
carries no window). A legacy journal line (pre-#3644) and a `_link_source` stub
carry no window, and an absent start is an **open start**: stamping the replay
clock would invent a version start and diverge from the node the same journal was
written from, and inheriting the old start would make a false claim about the
current version — `SET … = null` clears the property, which FalkorDB does remove
(the legacy-replay test pins it). This is the same rule `#5024` applied to `ingestedAt`/`updatedAt`, and
the reason `validFrom` joins `_SOURCE_NOOP_IGNORED_KEYS` (it is minted per call, so it
can never be the fact that makes an otherwise-identical re-check "different").

**D5 — the window is server-managed.** It is declared in `_SOURCE_NODE_PROPS` (so the
read filter keeps it) and listed in `_SOURCE_SERVER_MANAGED_PROPS` (so
`tortoise_update_entity(url, validFrom=…)` cannot write a window no transition
produced — the same *declared ⇒ writable* trap `#5196` round 3 found for `__runId`).
No caller-supplied valid-time start is accepted, deliberately: a caller-asserted start
needs the inversion guards the Point window needed (#3980, #4021, #5358, #5360). The
server-minted instant is guarded instead — `_mint_source_window_instant` clamps the
mint strictly above the last instant minted in the process, so a fixed or
backward-stepped wall clock can no longer open a zero-width or inverted version
window (the read-path class `audit.inverted_validity_window`).

### Rejected alternatives

| Alternative | Why not |
|---|---|
| Write only `validFrom` on the record, no `previousValidTo` | Cheaper, but leaves *"closes the old one"* implicit: a reader must apply the contiguity rule from §4.6 to know the prior window's end. The record would describe what changed without naming the act the section mandates. (Nothing is gained either — both values are one minted token.) |
| Give the **node** a `validTo` on each transition | Would either zero-length the window (`validTo == validFrom`) or claim the *identity* had expired at a version change — contradicting §4.6's one-node-per-`url` model, where a transition is not a removal. |
| Stamp `validFrom` from `sourceDate` when present | Conflates two declared slots (`dc:date` evidence age vs `prov:generatedAtTime` valid-time start) — §4.1's *"one slot under two spellings"* trap. The producer-asserted start is a separate, later input. |
| `coalesce($vf, $now)` (the `#5024` back-compat shape) | Re-introduces live != replay for every legacy line, and invents a valid-time start no observation supports. |
| `coalesce($vf, s.validFrom)` on the hash-diff gate | Lets a new version inherit the PRIOR version's window start when its record carries none — a false claim about the current version, where **absent (open start)** is the model's own value. Same class as `$now`, one version removed. |
| Do the `Object`/`Subject` rows in the same PR | Object supersession needs a successor window on the `ObjectSuperseded` payload — a second design with its own journal/contiguity contract. Bundling it would ship a second, unreviewed model in the same diff. Filed/recorded as remaining (§7), not abandoned. |
| Implement `expiredAt` now | **Blocked**: the removal field's meaning is #3590 S2's open decision; guessing it would import Point's *replaced-vs-withdrawn* conflation into a fourth type (the issue body's own warning). |

## 3. Integration surface map

| Surface | Covered by | Evidence |
|---|---|---|
| Journal record (`SourceVersioned` payload) | `create_source` transition arm | `tests/test_source_version_window_3644.py::test_a_transition_opens_the_new_window_and_closes_the_prior_one` |
| Replay fold (`_fold_source_versioned` → `_upsert_source`) | live == replay by construction | `…::test_the_window_start_is_the_recorded_instant_not_the_replay_clock`, `…::test_a_chain_of_transitions_stays_contiguous` |
| Repeat-safety (§9.6 journal cost bound) | hash-diff gate + `_SOURCE_NOOP_IGNORED_KEYS` | `…::test_a_noop_recheck_moves_neither_the_window_nor_the_journal` |
| Declared `:Source` node surface | `_SOURCE_NODE_PROPS` (+ the #3998 mirror list) | `tests/test_3998_raw_absent_state.py` (41 passed) |
| Tenant write surface | `_SOURCE_SERVER_MANAGED_PROPS` | `…::test_the_window_start_cannot_be_forged_through_the_entity_surface` |
| CI selection | `config/ci-surfaces.yml` (`core`) | `uv run python tools/ci_selection.py --changed tortoise/projection/entities.py`; `--integrity` |

No DB migration, no MCP tool, no public SDK method or signature change
(`create_source`'s signature is untouched — the instant is minted internally), so the
MCP/SDK surface approval rule is not engaged.

## 4. Tasks

### Task 1 — the writer mints the window instant (one clock)

**Intent:** the record and the node must not be able to disagree about the instant, and
`validFrom` must be a recorded value.
**Acceptance:** `create_source` sets `ev["validFrom"]` from the same `_version_instant`
that feeds `ev["updatedAt"]`; `validFrom` joins `_SOURCE_NOOP_IGNORED_KEYS`; the
`SourceVersioned` emission adds `previousValidTo=payload.get("validFrom")`.
**Files:** Modify `tortoise/sdk.py`.

### Task 2 — the fold writes the node's window through the hash-diff gate

**Intent:** the node's `validFrom` moves only when a version is created or changes, and
never from the replay clock.
**Acceptance:** ON CREATE `s.validFrom = $vf`; ON MATCH (same gate as
`version`/`updatedAt`) `$vf`; `$vf` param; `validFrom` +
`previousValidTo` in `_SOURCE_HANDLED`; `validFrom` in `_SOURCE_NODE_PROPS` and
`_SOURCE_SERVER_MANAGED_PROPS`.
**Files:** Modify `tortoise/projection/entities.py`.

### Task 3 — tests (14; the acceptance criteria of the issue, for this row)

**Intent:** each claim above is falsifiable, and the author's own test spec is met
(*"a rebuild after a transition reproduces the node's window, and the prior window is
recoverable from the journal alone"*).
**Acceptance:** 14 tests covering create/transition/chain/no-op/hash-less/legacy/stub
/leak/forge; registered in `config/ci-surfaces.yml`.
**Files:** Create `tests/test_source_version_window_3644.py`; modify
`config/ci-surfaces.yml`, `tests/test_3998_raw_absent_state.py` (its explicitly
hand-maintained declaration mirror).

### Task 4 — the documentation the author asked to flip

**Intent:** the impl status must not over-claim (the model's ⚠️ convention exists for
exactly this).
**Acceptance:** §4.6 field rows `❌` → `⚠️` for `validFrom`/`validTo` with the
node/record split stated; §4.6 Impl-status paragraph rewritten; §4.7 matrix Source
cells `❌` → `⚠️` + the † footnote; `docs/durability-posture.md` R2 row split
field-by-field; `docs/event-catalog.md`'s `SourceVersioned` entry; changelog v3.23.
**Files:** Modify `docs/ONTOLOGY.md`, `docs/durability-posture.md`,
`docs/event-catalog.md`, `docs/00_index.md`; create this plan doc.

## 5. Verification (exact commands and measured results)

```text
# the new guard — FAIL-BEFORE, then pass
# (the two production files stashed on the same base, test file in place:)
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_source_version_window_3644.py -q -p no:cacheprovider
→ 9 failed, 5 passed   (every window-reading test fails; the 5 that pass are
                       FORWARD guards — no-op repeat-safety, legacy open start,
                       legacy hash change does not inherit, stub open start,
                       journal-only payload)
→ 14 passed            (with the fix; 8–27 s)

# the suites that own the surfaces this touches
tests/test_source_rematerialisation_5024.py  → 32 passed
tests/test_3998_raw_absent_state.py          → 41 passed
tests/test_url_keyed_source_write_4649.py + test_source_identity.py
                                             → 54 passed, 2 xfailed
tests/test_source_fts.py + test_log.py + test_7369_journal_param_boundary.py
                                             → 99 passed, 3 skipped
tests/test_projection.py -k "source or Source or declaration"
                                             → 6 passed, 176 deselected
tests/test_ingest_mode.py                    → 18 passed

# found by the PR's own CI (test-slow (b)), then fixed and pinned:
#   tests/test_ingest_mode.py::TestModeIsomorphism::{test_isomorphic_gated,
#   test_isomorphic_auto} compared the bulk and granular graphs' full property
#   tuples, and the two modes honestly mint DIFFERENT `validFrom` instants —
#   the same class as the `createdAt`/`updatedAt`/`ingestedAt` clock values
#   already listed in that file's `_VOLATILE_PROPS`. `validFrom` now sits in
#   that set (with the reason recorded there); the isomorphism contract keeps
#   comparing every semantic prop (title/contentHash/externalId/version,
#   node/edge sets, statuses, counts).

# lint + CI selection
.venv/bin/ruff check tortoise/sdk.py tortoise/projection/entities.py \
    tests/test_source_version_window_3644.py tests/test_3998_raw_absent_state.py
                                             → All checks passed
uv run python tools/ci_selection.py --integrity    → ✅ all test files classified
uv run python tools/ci_selection.py --changed tortoise/projection/entities.py
    → surfaces ["core"], test_files include test_source_version_window_3644.py
```

**Host-contention caveat (recorded, not hidden):** the embedded lane is redislite-backed
and this box was at load average 26–31 during the run; the only failures observed in the
whole session were `RedisLiteServerStartError` / `redis ConnectionError` at server
START (`/private/tmp/tt_*/redis.socket`), never a failed assertion, and every affected
file passed on re-run. The two that were *not* environmental are recorded above:
the #3998 declaration mirror (a real, deliberate edit) and one of my own test's
contiguity assertion (wrong expectation, fixed — inter-record contiguity is
intra-record `previousValidTo == validFrom`, with monotone opens).

## 6. Acceptance criteria (this row)

- [x] `validFrom` round-trips: set live, survives `rebuild_all`, and `rebuild == live`
      holds field for field (the `#3590` S0 harness, `test_..._5024.py`, still green).
- [x] A version transition **opens** the new window and **closes** the prior one in the
      same act, and the prior window is recoverable **from the journal alone**.
- [x] An absent window is an open start; the replay never synthesizes one.
- [x] §4.6's Impl column, §4.7's footnote and the R2 table state exactly what landed,
      with the ⚠️ convention rather than a ✅ that the node's `validTo` would not earn.
- [ ] (not this PR) the node's `validTo`/`expiredAt` — needs #3590 S2's removal decision.
- [ ] (not this PR) the two further `:Source` `contentHash` writers (`_upsert_document`,
      `_materialize_session_source`) do not maintain the window — pinned wrong by
      `test_pinned_gap__*`, to be inverted when #3644 closes it.

## 7. What remains on #3644 (recorded so it is not silently absorbed)

1. **`Object` (`§4.3`) + `Subject` (`§4.2`) windows** — `validFrom`/`validTo`, still ❌.
   The Object row additionally needs the successor's window on the `ObjectSuperseded`
   payload (the fold runs on the predecessor) and the Point lane's contiguity guards;
   Subject has no supersession edge at all, so its `validTo` needs the (undefined)
   removal semantics first — the same #3590 S2 dependency as `expiredAt`.
2. **`expiredAt` on all four types** — blocked on #3590 S2's removal-field decision.
   The issue body already routes it there (*"Removal (once #3590 S2 defines it) sets
   `expiredAt`"*).
3. **Event `capturedAt` widened to every Event write path** (the issue's Event row) —
   the §4.5 cell is still ⚠️. The issue's cited write paths are **stale** (see §1);
   the remaining paths need to be enumerated before this can be closed, which is a
   separate change from the window.
4. **The two non-`create_source` `:Source` version writers** (PR #8016 code-review
   ISSUE 2, P1). `_upsert_document` (the `DocumentCreated` fold) bumps
   `contentHash` + `version` on the same hash-diff gate but writes no `validFrom`;
   `_materialize_session_source` (the capture path) rewrites `contentHash` without
   advancing `version` and mints no window. *(Updated 2026-10-17, #5097: it is
   no longer "replayed by nothing" — its metadata block DOES replay through a
   trailing `SourceCreated` that `_upsert_source` folds as a stub completion and
   that carries the node's `validFrom` verbatim, so a rebuild restores rather
   than clears a window and never advances `version`. The WINDOW half of this
   deferral is unchanged: the record mints no window and moves none — see
   `docs/ONTOLOGY.md` §4.6 and the `validFrom` row of
   `docs/durability-posture.md`.)* Both therefore leave a
   **present-but-stale** window start on any source the `create_source` path already
   stamped — the node asserts the new version has been valid since the *previous*
   version's start, which is the stale-window failure §4.6 exists to prevent, and
   is reachable only because this PR introduced `validFrom`. Extending the gate to
   them is not a boundary fix: each needs a minted instant that is **also
   replayable** (`_upsert_document` journals `DocumentCreated`;
   `_materialize_session_source` journals its metadata since #5097 but carries no
   window instant at all), i.e. a clock/mint
   decision this PR deliberately does not take. **Recorded, not silenced**: the
   current wrong behaviour is PINNED by
   `tests/test_source_version_window_3644.py::test_pinned_gap__upsert_document_moves_the_anchor_without_the_window`
   and `…::test_pinned_gap__session_capture_moves_the_anchor_without_the_window`,
   which must be **inverted** when this item closes. #3644's own *Writers* section
   already names `_upsert_document` among the writers the temporal model must reach.
