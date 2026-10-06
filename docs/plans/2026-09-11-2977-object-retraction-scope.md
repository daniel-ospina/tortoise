# #2977 — Object removal is not durable · SCOPE

**Issue:** daniel-ospina/tortoise#2977 · `complexity:standard` (re-rating conditional — §6 D-4)
**Stage:** SCOPE (task-workflow-standard) · 2026-09-11 · **cycle 4** — ⚠️ verifier cap reached; see §8
**Worktree:** `.worktrees/fix/2977-object-retracted` @ `5f42031b8`

> Supersedes the problem statement in the issue body. The issue's *mechanism* is correct; its
> *framing* was not. Corrections in §2; the issue has been updated to match.
> Verification cycle log: §8.

---

## 1. Problem statement (corrected)

**An Object's removal is not durable, and the read path does not agree on what removal means.**

Four defects sit behind the reported symptom. They interact, so each is stated with what it
does and does not cover. (D2 is a constraint on the remedy rather than a defect in its own
right; it is numbered with the others because a remedy that ignores it is wrong.)

### D1 — Removal is invisible to the journal

`_delete_entity` (`tortoise/sdk.py:16150-16164`) loops six labels — `Point, Subject, Object,
Document, Source, Event` (`:16156-16157`) — issuing a bare `MATCH … DETACH DELETE … RETURN
count(n)` (`:16159-16162`) and emitting **no event**. `delete_entity` (`:19110-19113`) is a
pure delegate and adds none.

For a journal-backed SDK the next replay re-creates the Object from its surviving
`ObjectRegistered` line: the entity comes back. Verified — `tests/test_object_registered_journal.py:309-332`
asserts the resurrection and passes today.

**Coverage:** journal-backed SDKs only. See D4.

### D2 — Replay is two engines, and a fix must land in both

- CLI `python -m tortoise rebuild` → `rebuild_all` (`__main__.py:51` →
  `projection/__init__.py:1161`) → **deferred trailing sweep** (`:1528-1543`).
- Embedded auto-recovery → `recover_from_log` (`consistency.py:36`, called from
  `projection/__init__.py:968-969`) → `apply()`, at `consistency.py:122` →
  `projection/__init__.py:1050` → **chronological, folds inline**.
- `rebuild(log)` (`:1156-1159`) is a wipe + `apply()` wrapper, so it behaves as the second.
- A third path exists but is **Object-blind** — the CLI `ImportError` fallback `fold(events)`
  (`__main__.py:53-69` → `InMemoryProjection.rebuild`, `projection/__init__.py:551-552` →
  `_apply_one` `:460`) handles only `PointAdded`/`PointRevised`/`PointRetracted`/`PointsMerged`,
  so it cannot express this defect. Noted so "two engines" is not read as "two code paths".

The two engines have *different ordering semantics*. The sweep exists in `rebuild_all`
precisely because a later event can re-create a node the fold already stamped. A remedy built
for one engine can be silently inert in the other. #2164 is the precedent: an event with no
`rebuild_all` branch **silently fell through** (`:1441-1444`).

`apply()` also **skips unknown event types**, so a journal written by a patched binary and
replayed by an unpatched one silently resurrects. See §6 D-8.

### D3 — "Retracted" is not honoured uniformly on the read path

The terminal vocabulary exists (`commit_ops.py:32-33`) and the supersede/apply layer already
treats `retracted` as terminal (`commit_ops.py:543`, `:595`; `entities.py:608-609`) — but the
retrieval layer gates the exclusion **on the label being `Point`**, at **four** legs:

| Leg | Line | Gate |
|---|---|---|
| FTS | `search_engine.py:452-456` | `if label == "Point"` |
| Vector (index) | `:581-585` | `if label == "Point"` — inside `if not is_embedded:` (`:579`) |
| Vector (brute-force) | `:691-695` | `if label == "Point"` — the **embedded** path, and the fallback when the index query raises |
| Structural | `:825-827` | `label_str == "Point"` |

There is a **second, separate** exclusion point: `sdk.py:12317` applies the `exclude_status`
post-filter only when `graph_label == "Point"`, so `tortoise_fts_query(exclude_status=…)` is
silently ignored for Objects.

The two exclusion vocabularies are **deliberately different** and must not be merged
(`commit_ops.py:25-33`: the recall tuple "is NOT … `search_engine.TERMINAL_EXCLUDED_STATUSES`
(adds `'outdated'`, which recall's object view DOES surface)"; `search_engine.py:39-60`).
Reusing the Point clause for Objects would wrongly hide `outdated` Objects.

Finally, `recall_state` **re-admits** retracted Objects under `include_superseded=True`
(`sdk.py:13869-13872`) while refusing for Points (`:13883-13884`).

**What is new vs. pre-existing.** Raw-leg Object leakage is **pre-existing** — `superseded` is
already written by `ObjectSuperseded` and excluded by no leg; only the `recall_state`
inversion is newly reachable. This distinction governs §5.4.

### D4 — Without a journal the Object is lost, not resurrected

`event_log_path` defaults to `None` (`sdk.py:1778`) and JSONL emission is skipped when it is
(`:2300-2302`). `tortoise/hosted_api.py` contains **zero** references to it. `rebuild_all`
snapshots `:Point` (`:1195`) and `:Batch` (`:1267`) only, then wipes (`:1301`). There is **no
Object snapshot**.

So for a journal-less SDK a rebuild does not resurrect the Object — it destroys every Object.
That is worse than D1 and is *not* fixed by a retraction event. Owned by #2296 (indicator 2);
recorded here so it is not mistaken for solved.

### The hazard that makes the remedy non-trivial

Objects use name-derived ids (`obj-<sha26(name)>`, `sdk.py:1352-1353`). The two node families
differ in one decisive line:

- **Points revive on re-registration** — `_upsert_point_props` assigns
  `n.status=coalesce($st, n.status, 'live')` (`entities.py:187`).
- **Objects do not** — `_upsert_object`'s `ON MATCH` (`:515-519`) never assigns `status`,
  deliberately, per the #1350 clobber guard ("a re-mention cannot reset superseded→live",
  `:522-524`).

Consequence for the shape `Reg(A) → Retracted(A) → Reg(A)` (delete, then re-create):

- `rebuild_all` **without a survivor rule**: retraction folds blind → **`retracted`**.
- `apply()`: folds inline → the third event's `ON MATCH` cannot reset → **`retracted`**.

Both engines bury a live Object. The **Point** family solved this with `last_recreate_seq`
(`:1310`, `:1339`, `:1556-1584`, #2488), which keeps only re-stamping folds positioned after
the id's last re-creation — but it is a `rebuild_all`-local artifact (defined `:1310`,
populated `:1339`, consumed `:1580`) with **no `apply()` counterpart**. The Object sweep never
portable it at all: it folds blind (`:1528-1543`), and its comment (`:1529-1534`) records that
as deliberate for supersession ("first-wins replay would regress incarnation-reuse shapes").

**A retraction lane without a re-creation rule converts "deleted data comes back" into "live
data disappears", on both engines.** See §6 D-1.

---

## 2. What the evidence changed (corrections to the issue)

| # | Issue said | Evidence | Correction |
|---|---|---|---|
| 1 | "standalone … independent of that epic" | `sdk.py:15918-15925`; `ONTOLOGY.md:384`, `:404`; `test_object_registered_journal.py:296-304` | Already documented and green-pinned as an accepted divergence with a "#2296 scope hook". No such link existed in #2296. It is a tracked gap, not a discovery. |
| 2 | (implied) Object-specific | `_delete_entity` loops **six** labels (`sdk.py:16156-16157`) | The mechanism is class-wide; Object is one instance. Point has a lane; Subject is pinned identically (`test_subjectadded_journal.py:458-520`). |
| 3 | "rebuild no longer resurrects" (unqualified) | `sdk.py:1778`; `hosted_api.py` → 0 refs; snapshots `:1195`, `:1267` | Holds **only for journal-backed SDKs**. Without a journal the Object is **lost** (D4). |
| 4 | "the `retracted` status already exists in the vocabulary" | `commit_ops.py:543`, `:595`; `entities.py:608-609` | **Stronger than stated** — not a read-filter coincidence; the apply layer already treats it as terminal. Of the *status vocabulary*, only the writer is missing — but the **recall/search** surface still needs §5.4's four legs and the `sdk.py:12317` post-filter. (`ONTOLOGY.md`'s Object status row is `:396`.) |
| 5 | citation `projection/entities.py:375` | line 375 is inside `_fold_point_invalidated`'s docstring (def `:339`) | Imprecise, not fabricated — the line does assert "like the superseded fold's missing successor". Real constraint is the apply-layer gate `commit_ops.py:593-602`. |
| 6 | one event registry | `sdk.py:723-735`; `tortoise/shared_state/events.py:162-174`; `tortoise/shared_state/tests/test_events_claim.py:19-31` | **Three** hand-synced lists, no parity test between any pair. `_GRAPH_EVENT_TYPES` is not a registry but a **routing gate** (`sdk.py:2269`) for the `:GraphEvent` store — see §6 D-7. |
| 7 | "`ObjectRetracted` does not exist" | `grep -rn ObjectRetracted` → 0 hits outside this document | **Confirmed.** |
| 8 | — | `entities.py:187` vs `:515-519` | **New:** Points revive on re-registration; Objects deliberately do not. This asymmetry is the root of the §1 hazard. |

**Filing defect (mine).** #2977 asserted a standalone discovery and cited none of `#2296`,
`#2309`, tests 14-15, or the Subject twin — the same error class the #2835 Research gate caught
five times. Issue body corrected.

---

## 3. Is #2296 the owner? (No)

- **Dormant.** Opened 2026-09-05, one comment 2026-09-06, **no assignee, no milestone,
  0 sub-issues**, no activity since.
- **Does not enumerate this.** `grep -i "delet\|retract"` over its body returns **nothing**.
  Its indicator 2 is the Object **journal-loss** backstop — the *opposite* failure direction.
- **Map-first by construction.** Its objective is to "produce a durable write-surface **map** …
  and close the remaining gaps **it exposes**". The "#2296 scope hook" annotation is one-way —
  asserted in our code and tests, never in the epic.

**Conclusion:** #2296 is a *consumer* of this fix, not a blocker. Deferring #2977 parks a pinned
data-correctness defect on an unowned audit epic that does not mention it.

---

## 4. Assumptions

| # | Assumption | Status | Falsifier |
|---|---|---|---|
| A1 | Resurrection occurs on replay for journal-backed SDKs | **validated** (`test_object_registered_journal.py:309-332`) | a non-resurrecting replay path |
| A2 | `_delete_entity` emits nothing on any of the six labels | **validated** (`sdk.py:16156-16164`) | an emit inside the loop |
| A3 | Auto-recovery uses `apply()`, not `rebuild_all` | **validated** (`consistency.py:122`) | a different dispatcher |
| A4 | The Object sweep has no re-creation survivor rule | **validated** (`:1528-1543` vs Point `:1556-1584`) | a seq filter in the Object sweep |
| A5 | `_upsert_object` never resets `status`; `_upsert_point_props` does | **validated** (`entities.py:515-519` vs `:187`) | an `ON MATCH` status assignment for Objects |
| A6 | Read exclusion is label-gated at four legs, plus `sdk.py:12317` | **validated** | an ungated Object exclusion |
| A7 | `retracted` is already terminal in the apply layer | **validated** (`commit_ops.py:543`, `:595`) | its absence |
| A8 | No **sanctioned lifecycle** writer sets `Object.status='retracted'` | **validated, with a loophole** — the generic `_update_entity` path (`SET n += $p`, `sdk.py:16144-16147`) accepts any `status`; `_sanitize_props` blocks the sibling flag `outdated` (`:903-907`) but not `status` | an Object retraction writer |
| A9 | Which SDK #2835's GitHub enumerator will use | **unverified** | decides whether the fix reaches its motivating consumer |
| A10 | The Point split (hard-delete live, tombstone on replay) is the accepted contract for live/replay divergence | **unverified** | a stated invariant requiring byte-identity |

---

## 5. Scope boundary

**In scope**

1. A journaled retraction for **Objects** — where it is registered **follows §6 D-7**, which is
   why D-7 must be settled before this item is written. JSONL-only (the `ObjectRegistered`
   precedent) ⇒ no production list at all, plus parity coverage; a `:GraphEvent` decision ⇒
   `sdk.py:723-735` and a stated `:GraphEvent` rebuild consequence. If a claim-event surface is
   wanted, `shared_state/events.py:162-174` + `shared_state/tests/test_events_claim.py:19-31`
   apply. Note `ObjectRegistered` appears in **none** of the three lists today.
2. Emission from the Object branch of `_delete_entity`, only when that branch matched.
3. Replay in **both** engines — an explicit re-creation rule in `rebuild_all`'s Object sweep,
   plus a shared flush for the apply-based paths, reached through the replay-only wrapper
   (§6 D-1(e)). **Not** by modifying `apply()`.
4. The `recall_state` inversion (`sdk.py:13883-13884` has no Object twin) — newly reachable
   through a *supported, durable* lane, though the underlying status is already reachable today
   via the A8 loophole. Also in scope: the four label-gated legs
   (`search_engine.py:452-456`, `:581-585`, `:691-695`, `:825-827`) and the post-filter
   (`sdk.py:12317`), because the issue's target (c) is "`retracted` respected by the
   **recall/search** surface" — without them the fix is invisible to `search()`/FTS/vector.
5. The connector fold guard (`entities.py:923-933`) guards only `<> 'superseded'`, so a GitHub
   `reopened` event un-retracts a deleted Object on replay. Latent today (A8); first-class once
   the lane lands. The guard's predicate needs a decision (§6 D-10).
6. Invert the Object green-pins (tests 14/15), update the "accepted divergence" comments
   (`sdk.py:15918-15925`) and `docs/ONTOLOGY.md` §4.3.

**Out of scope** — recorded with the *actual* blocker, not "→ #2296"

- **Subject / Document / Source / Event durability.** The "no unreplayable journal line"
  invariant (`sdk.py:16034-16037`) is **label-scoped to `ObjectRegistered`** and does not block
  them. Per label the cost is **three** things, not one: a retraction event type, an emission
  branch in the six-label loop (`sdk.py:16156-16157`), and a read-exclusion vocabulary — there
  is no per-label twin of `_RECALL_OBJECT_EXCLUDED_STATUS` (`commit_ops.py:32-33`). The
  *registration* folds already exist for all four (`shared_state/events.py:162-174`). This is a
  cost call, not an impossibility. Consequence to accept explicitly: after the fix
  `delete_entity(subject_id)` still resurrects silently while `delete_entity(object_id)` is
  durable — the public contract becomes label-inconsistent. Mitigation decision: §6 D-9.
- The journal-less **loss** case (D4) → #2296 indicator 2.
- Capture `aboutObject` / `CONTAINS` edge durability on replay → #2296.
- The `mcp_server.py:2391` `tortoise_delete_entity` Point bypass (deletes a Point without
  `PointRetracted`, bypassing `delete()`'s routing) → file separately.
- A user-facing un-retract verb (no requirement found; not invented).

---

## 6. Open decisions

**D-1 · Re-creation rule — the load-bearing choice.** Must work in **both** engines (D2).

- **(a)** Port the Point `last_recreate_seq` survivor rule to the Object sweep.
  *Corrects **`rebuild_all` only*** — the anchor is a `rebuild_all`-local artifact
  (`:1310`, `:1339`, `:1580`) with no `apply()` counterpart, so `apply()` keeps burying.
  **Not sufficient alone.**
- **(b)** Bound v1 to single-incarnation removal; red-pin delete→recreate. Smaller diff, but
  **knowingly worsens** an existing shape, and #2296 never enumerates it.
- **(c)** Keep sticky-terminal Object semantics (#1350): re-creation after removal stays
  removed. Needs **no survivor rule at all** and is consistent with current Object behaviour —
  but it **burns the name permanently** (`obj-<sha26(name)>`), so a component deleted and
  re-added stays invisible. That is wrong for the motivating consumer.
- **(d)** ~~Let a fresh journaled `ObjectRegistered` clear a prior `retracted` status.~~
  **Refuted at cycle-2 verification — do not resurrect this option.** Three independent
  failures: (i) the `rebuild_all` sweep folds *after every creation event* (`:1528-1543`), so
  it re-stamps terminal and erases the clear; (ii) the probe at `sdk.py:15933-15939` is a
  **live-graph** existence test and `_emit_event` is gated on its negation (`:16029`), so once
  a `retracted` tombstone exists **no `ObjectRegistered` line is written at all** — (d) keys on
  a line that will not exist precisely in the state the fix creates; (iii) the fold cannot
  distinguish re-creation from re-mention (both reach `_upsert_object` with no freshness
  signal, `sdk.py:15978`), so it either breaks the #1350 guard or needs a **new journaled
  field** — not "a property of the fold".
- **(e) ← recommendation.** **Give the retraction lane a shared replay-only terminal step.**
  The (a) survivor rule lands in **`rebuild_all`'s Object sweep** — which is where the sweep
  already is (`:1528-1543`) — and the apply-based replay paths get the same flush through a
  replay-only wrapper.

  ***Cycle-4 correction.*** An earlier draft routed only `rebuild(log)` and `recover_from_log`,
  which would have fixed nothing that matters: **`rebuild_all` does not call `apply()`** —
  `:1161-1543` runs its own pass-1a/pass-1b dispatch plus the sweep — and it is the engine D2
  identifies as divergent. It is reached from the CLI (`__main__.py:51`) and
  `migrate_db.py:186`; `rebuild(log)` has **zero production callers** and is therefore not the
  path to converge.

  **Do NOT put the deferral inside `apply()`. *Cycle-3 correction.*** `apply()` is **not**
  replay-only. It is also the live entity funnel (`sdk.py:15997`), the live emitter
  (`api.py:61`), the connector path (`connectors/github.py:263`, `:282`, `:286`, `:320`,
  `:378`), the restore loop (`backup.py:145-147`), the bulk-import loop (`__main__.py:259`),
  and the GitHub indexer (`indexer/github_indexer.py:554-746`). Deferral there would change
  live write semantics and would require a flush contract for every one of those callers —
  an order of magnitude more surface than "two callers".

  Mechanism: (i) the survivor rule in `rebuild_all`'s pass-1a Object dispatch + its sweep;
  (ii) a replay-only wrapper (e.g. `apply_replay(events)`) that loops `apply()` then runs the
  same flush, used by `recover_from_log` (`consistency.py:119-125`). `apply()`'s live callers
  are untouched. Cost = the flush + the pass-1a survivor work + the wrapper — **`rebuild_all`
  is in the routed set, so the benefit is actually delivered.**

  **Third replay site, recorded:** `backup.py:145-147` also loops `apply()` over a restored
  log. Route it too, or list it as an explicit out-of-scope replay site — left unhandled it
  would silently behave like (c) after the fix.

  If (e) is too large for this issue, the honest fallback is **(c) plus explicit documentation
  of burned-name semantics** — **not** (b), which ships a knowingly-worse divergence.

  D-1 and D-3 are orthogonal but must be decided together: **both** (c) and (e) leave a
  *replay* tombstone, so D-3's real question — is the live hard-delete / replay-tombstone
  divergence accepted, or does the live path status-flip? — applies to either.

**D-2 · New event vs. riding the supersession lane.**
Corrected cost of the alternative: relaxing `commit_schema.py:476` (`min_length=1`) and the
`has_visible_distinct` gate (`commit_ops.py:593-603`) is **not sufficient** — an empty
`supersedes_by` is dropped earlier in **three** places: `commit_ops.py:357-360` (record
skipped), `:404-413` (dangling-successor probe), `:202` (`_supersession_fold_order`). So (b)
is ≥3 gate changes + schema. The assembly harm was also overstated:
an **empty** successor produces `superseded_by={"content_snippet": ""}` and no
`[SUPERSEDED BY]` mark (`assembly.py:862-864`, `retrieval.py:411-414`) — the harm materialises
only if a non-empty sentinel is invented, which nothing specifies. (`assembly.py:1063`'s
`names.discard("")` only excludes `""` from the probe set.)
**Recommendation: (a) new event** — clean semantics, and (b) buys less than it appeared to.
Inheritance either way: (b) does **not** escape D-1.

**D-3 · Live semantics.** The `delete()` wrapper is documented *Destructive* and hard-deletes
(`sdk.py:4117-4123`; `delete_entity` itself is a bare delegate, `:19110-19113`); journaled
retraction replays to a *present* node. Either accept observational equivalence (conditional
on §5.4 landing) or make the live path a status-flip so live and replay match. The Point split
(A10) is the precedent. This interacts with D-6.

**D-4 · Complexity re-rating.** The "spans four subsystems" argument does **not** discriminate:
`#2423` (rebuild branch + sweep + edge re-point) and `#2488` (new event type + pass-1b fold +
registry + the `last_recreate_seq` survivor rule) are **both `complexity:standard`**, and #2488
is the exact mechanism cited. **Recommendation: keep `Architecture: standard`** unless a
decision below adds material scope beyond #2488 — most likely **D-1(e) vs (c)** (cross-engine
replay unification is the largest scope adder in this document) plus §5.4's four-leg read
expansion and D-9. **Cycle-3 correction:** an earlier draft keyed this on D-1(d), which D-1
itself now forbids.

**Resolve the rating explicitly — do not inherit it. (c) ⇒ `Architecture: standard`.
(e) ⇒ `Architecture: complex`.** (Routing is identical either way — both go to
`task-workflow-standard` — so this is a labelling/honesty decision, not a gate change.)

**Delivery shape.** (c) is plausibly one PR. (e) splits naturally into a journal/replay PR and
a read-path PR, since §5.4 (four legs + post-filter) is architecturally separable from the
replay work. Decompose owns the final wiring, but the seam is named here.

**D-5 · Edge / orphan policy on retraction.** *(mandated by the issue; was dropped from cycle 1)*
On retraction, do inbound `aboutObject` edges from Events/Points survive as dangling-but-recorded
or get removed? This is **newly reachable** via D1 — live `DETACH DELETE` (`sdk.py:16156-16161`)
drops inbound edges, while replay leaves a tombstone node that pass-2 may re-wire: live/replay
**edge** divergence. Point precedent: `_retract` (`entities.py:267-283`) is a bare MATCH-SET.
Also: what does the fold do for an **orphan retraction** (an event whose Object has no
registration line — mirrors the 0-row warning at `:1537-1543`)? No-op vs tombstone.

**D-6 · Idempotency.** *(mandated by the issue; was dropped from cycle 1)* A double retraction
must not duplicate events or corrupt state. Today this is satisfied implicitly by the hard
delete (the second `MATCH` matches nothing) — a guarantee that **evaporates under D-3's
status-flip option**. D-6 depends on D-3.

**D-7 · JSONL-only, or also a `:GraphEvent`?** `_GRAPH_EVENT_TYPES` (`sdk.py:723-735`) is the
routing gate for the hosted `:GraphEvent` store (`:2269`). `ObjectRegistered` is deliberately
**not** in it ("JSONL-only emission", `sdk.py:16032-16033`). Cycle 1's "all three registries"
silently committed to a GraphEvent write while §5 defers `:GraphEvent` rebuild. Decide
explicitly; if following the Object precedent, §5.1 enumerates the affected surfaces rather
than presupposing one (`ObjectRegistered` appears in **none** of the three lists today).

**D-8 · Forward/rollback compatibility.** A journal written by the patched binary and replayed
by a pre-patch binary hits `apply()` with no branch and silently resurrects (D2). State the
expectation or declare it unsupported.

**D-9 · Signal for the still-non-durable labels.** After the fix `delete_entity` is durable for
Objects and not for five other labels, with no signal. Choose: emit a warning in the
non-durable branches when `_event_log_path` is set; or document; or extend coverage.

**D-10 · Connector guard predicate.** Adding `<> 'retracted'` produces a third narrow literal
while `archived`/`deprecated` stay un-resettable by `github.issue.reopened`. Generalise to
`_RECALL_OBJECT_EXCLUDED_STATUS` (safe — `completed` is not in the tuple, so reopen-after-close
survives) or record the narrow shape as deliberate.

**D-11 · Authoritative rebuild test surface.** *(mandated by the issue; still missing after
cycle 2)* Which test proves non-resurrection? Given D2, it must be a **JSONL rebuild round-trip
through `rebuild_all`**, not only a projection unit test — and, if D-1(e) is chosen, the same
round-trip through `recover_from_log`/`apply()`, since that is the engine that silently diverges.

**D-12 · Terminal precedence when an Object is both superseded and retracted.** The fold order
between the existing supersession lane and the new retraction lane determines final `status`.
No decision currently fixes it.

---

## 7. Provenance

Investigated 2026-09-11 by ten read-only sub-agents across four verification batches
(problem-diverge ×2; an independent verifier and a contradiction-attacker; then two SCOPE
verifiers in each of three cycles).

- **Round 1** corrected three claims and **inverted** one load-bearing direction (an early draft
  held that a fix patching only `apply()` passes the CLI path; the code shows the reverse — the
  CLI sweep is the blind one).
- **Round 2** (cycle-1 gate, both verifiers FAIL) found a nonexistent path, two wrong line
  ranges, a wrong comment citation, an over-claimed remedy, a false cost claim in D-2, and
  mandated decisions dropped entirely.
- **Round 3** (cycle-2 gate: one PASS, one FAIL) refuted option D-1(d) outright and caught two
  further drops — the rebuild test surface was *claimed* fixed in the cycle log but was not, and
  §5.1 contradicted D-7 by still mandating a `:GraphEvent` registration.
- **Round 4** (cycle-3 gate: one PASS, one FAIL) found that the reshaped D-1(e) undercounted
  `apply()`'s callers by an order of magnitude. **Round 5** (cycle-4 gate: one APPROVE, one
  FAIL) then found that (e)'s routing set omitted `rebuild_all` — the only engine that matters.

*Prior wording — "every citation in this document was independently confirmed" — was
over-claimed and is withdrawn. Every citation was confirmed; the substantive and remedy
corrections are enumerated in §8.*

---

## 8. Verification cycle log

**Cycle 1 — 2 parallel SCOPE verifiers → both FAIL.**

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | P1 | `tests/test_events_claim.py` does not exist | → `tortoise/shared_state/tests/test_events_claim.py:19-31` |
| 2 | P1 | Three read legs cited; there are **four** (brute-force fallback `:691-695` is the embedded path) | §1 D3 table now lists four + `sdk.py:12317` |
| 3 | P1 | D-1(a) cannot fix `apply()`; not "both cases correct" | D-1 rewritten; option (d) added |
| 4 | P1 | Mandated Scope decisions dropped (edge/orphan, idempotency) | D-5, D-6 added — **but the third (rebuild test surface) was NOT added; see cycle-2 entry** |
| 5 | P1 | D-2's "relax two gates" is false — three earlier drops | D-2 corrected with `commit_ops.py:357-360`, `:404-413`, `:202` |
| 6 | P1 | §5.4 targeted one layer; misses `sdk.py:12317` and the vocabulary difference | §1 D3 + §5.4 reframed; pre-existing vs new split out |
| 7 | P1 | D-4 re-rating ignores `#2423`/`#2488`, both `standard` | D-4 reversed to keep `standard` |
| 8 | P2 | §5.1 silently committed to a `:GraphEvent` write | D-7 added |
| 9 | P2 | Out-of-scope under-argued; invariant is label-scoped | §5 corrected; D-9 added |
| 10 | P2 | §5.5 fixes one literal, not the terminal set | D-10 added |
| 11 | P2 | §7 universal-verification claim falsified | §7 withdrawn/corrected |
| 12 | P2 | §2 row 7 "0 hits repo-wide" self-refuted | → "outside this document" |
| 13 | P2 | Snapshot line range wrong (`:1173-1176`/`:1246-1266`) | → `:1195` / `:1267` |
| 14 | P2 | Object-sweep comment citation off (`:1539-1545`) | → `:1529-1534` |
| 15 | P2 | §2 row 6 line refs off by one | → `:162-174`, `:19-31` |
| 16 | P2 | D-2 recommendation self-contradictory (arrow keyed to (b)) | D-2 rewritten |
| 17 | P2 | Stray `ONTOLOGY.md:394` | removed |
| 18 | P2 | No forward-compat/rollback decision | D-8 added |

**Cycle 2 — 2 parallel fresh verifiers → one PASS, one FAIL.**

| # | Severity | Finding | Fix |
|---|---|---|---|
| 19 | **P0** | D-1(d) unimplementable: the sweep re-stamps after re-creation; the no-row probe (`sdk.py:15933-15939`) suppresses the very line it keys on; conflicts with #1350 | D-1(d) **refuted in place**; option (e) added and recommended |
| 20 | P1 | §5.1 still mandated a `:GraphEvent` registration, contradicting D-7 | §5.1 rewritten to depend on D-7; `ObjectRegistered` noted as being in **none** of the three lists |
| 21 | P1 | Third mandated decision (rebuild test surface) still missing while §7/§8 claimed it fixed | D-11 added; §8 entry 4 corrected to admit the gap |
| 22 | P1 | §5.4's "split out — see D-9" was a dangling pointer; raw-leg read gap had no owner | §5.4 now brings the four legs + `sdk.py:12317` **into** scope (target (c) covers search) |
| 23 | P2 | D-2 assembly-harm cites the wrong line | → `assembly.py:862-863`, `retrieval.py:411-414` |
| 24 | P2 | No decision on superseded-vs-retracted precedence | D-12 added |
| 25 | P2 | Wipe cited at `:1295` | → `:1301` |
| 26 | P2 | "Three defects" then enumerates four | → "Four defects", D2 flagged as a constraint |
| 27 | P2 | A8 over-stated; `update_entity` is an unguarded enabler | A8 relabelled; §5.4's "newly reachable" qualified |
| 28 | P2 | `_exclude_status_clause` cited without a file | → `search_engine.py:39-60` |
| 29 | P2 | Brute-force leg labelled only "embedded" | → also the index-failure fallback |
| 30 | P2 | "Two engines" undercounts replay paths | Third path noted, and shown Object-blind |
| 31 | P2 | §5 out-of-scope understated per-label cost | → three things per label, named |
| 32 | P2 | D-5's 0-row warning cited at `:1539-1545` | → `:1537-1543` |

**Cycle 3 — 2 parallel fresh verifiers → one FAIL, one PASS.**

| # | Severity | Finding | Fix |
|---|---|---|---|
| 33 | P1 | D-1(e) undercounted `apply()` callers by an order of magnitude — `apply()` is also the **live** funnel, emitter, connector, restore, bulk-import, and indexer paths | (e) reshaped: replay-only wrapper, explicitly *not* a change to `apply()` |
| 34 | P1 | D-4's escalation trigger named D-1(d) — an option D-1 itself forbids | → **(e) vs (c)**, plus §5.4's four-leg expansion and D-9 |
| 35 | P2 | D-7 cross-reference read as contradicting §5.1 | reworded |
| 36 | P2 | D-1↔D-3 coupling rationale unsound (both options leave a replay tombstone) | corrected: D-3 is orthogonal, applies to either |
| 37 | P2 | D-3 cited *Destructive* on the wrong symbol | → `sdk.py:4117-4123` (`delete()`), with `delete_entity` noted as a delegate |

**Cycle 4 — 2 parallel fresh verifiers → one APPROVE, one FAIL.**

| # | Severity | Finding | Fix |
|---|---|---|---|
| 38 | P1 | D-1(e) routed only apply-based engines; **`rebuild_all` — the CLI engine and D-2's divergent one — was not routed** | (e) rewritten: (a) lands in `rebuild_all`'s sweep, wrapper covers the apply-based paths; cost restated |
| 39 | P2 | `backup.py:145-147` is a third bare-`apply()` replay site, unflushed | recorded in D-1(e) |
| 40 | P2 | `apply()` caller list non-exhaustive (`connectors/linear.py:164`, `connectors/slack.py:108`, `:220`, `mining.py:831`, `sdk.py:10796`, `:17761`) | conclusion holds a fortiori; list illustrative |
| 41 | P2 | D-2 assembly citation off by one | → `assembly.py:862-864` |
| 42 | P2 | §2 row 4 ("only the writer is missing") contradicted §1 D3 / §5.4 | qualified |
| 43 | P2 | Front-matter stale and self-contradicting | → cycle 4; "re-rating conditional" |
| 44 | P2 | D-4 left the rating unresolved under its own recommended option | resolved: (c) ⇒ standard, (e) ⇒ complex |
| 45 | P2 | D-1(e) heading over-stated ("unify the two engines") | retitled: shared replay-only terminal step |
| 46 | P2 | Delivery shape (one PR vs two) unstated | named: (e) splits journal/replay from §5.4 read-path |
| 47 | P2 | §7's agent/cycle arithmetic stale | corrected |

⚠️ **Verifier cap reached (4 cycles).** Cycle 4 returned one **APPROVE** ("No P0/P1 scope defect
remains") and one **FAIL** whose sole P1 (#38) is fixed above. Remaining open items are
P2-level. Recorded per the review-loop protocol rather than looping further.

**Gate disposition: PASS on the problem definition and boundary** (all four cycles agreed the
problem selection was right); **⚠️ capped** on the remedy layer, where the P1s were all in
D-1's mechanism rather than in the scope itself.
