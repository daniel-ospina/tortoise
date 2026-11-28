---
title: "#12943 — the counted live-write population is CLOSED and NAMED, and the demo seeder is in it"
type: plan
domain: platform
doc_status: live
created: 2026-11-24
ownedBy: platform-team
aboutSubjects: tortoise
aboutObjects: tortoise-hosted-api, tortoise-cache-projection, tortoise-storage
---
<!-- research-path: issue daniel-ospina/tortoise#12943 comment carrying `<!-- research: v1 -->` (7236051267) -->
<!-- plan-review: cycles=1, status=open, version=2.3.0 -->
<!-- ⛔ THE SCHEMA'S OTHER TWO VALUES ARE `clean` AND `threat-surface-covered`, and this lane may
     record NEITHER: it has no `task` tool by dispatch contract, so the plan's review round IS the
     harness reviewer at the PR head, and round 1 returned three P2 findings (all fixed). The
     authoritative CLEAN verdict for this head is the signed attestation the harness mints
     (`review recorded: reviews/14810.json verdict=… @ <sha> (daniel-ospina/tortoise)`), not this
     line — and a line claiming `clean` before that exists would be the exact fabrication the
     review-discipline rule forbids. `status=open` is the honest value here; do not "fix" it to
     `clean` without an attestation at this head. (Review round 1, P2-3.) -->

# #12943 — the counted population is closed, and the lane that was outside it is inside

## 1. Confirmed problem

The instrument #13363 landed (`metrics()['cache_write_targets']`) answers *"where did THIS process's
live cache writes land"* — the per-process reading that turns #12943's split (`8 objects in the
record, 0 in the cache, nothing raises`) into one comparable number. It is correct on **every lane it
names**. What is not true is **the population it claims**.

Measured 2026-11-24 at `origin/main` = `31a320e392`, on an embedded SDK, driving the sanctioned
in-process writer's own demo lane:

```text
seed result:        {'status': 'demo_created', 'points': 9}
targets after seed: {}                 # metrics()['cache_write_targets']
nodes written:      [[26]]
labels:             [['Meta', 1], ['Point', 10], ['Session', 1], ['Tag', 14]]
```

**26 nodes written, registry `{}` — byte-identical to "this process took no live cache write".** That
is the false-PASS shape the registry exists to end, occurring **inside the registry's own claimed
population**: `note_live_write_target`'s docstring opens *"Called from every gateway that writes the
cache WITHOUT folding a record"* (`tortoise/projection/__init__.py:4748`), and `_seed_demo_graph`
(`tortoise/hosted_api.py:20376`) is exactly such a gateway. It **chooses its graph** —
`sdk = sdk if sdk is not None else _org_sdk(org_id)` at `:20407` — so the helper's stated residual
(which excuses a raw writer *because it does not choose a graph*) does not reach it.

Two further facts make this a bookkeeping defect rather than a policy one:

1. **The same class has now produced a finding in three consecutive review cycles** (#13363 rounds 5, 6
   and 7 — each found exactly one more lane). A **universal** claim cannot be satisfied by
   enumeration; every cycle that hunts for another lane finds one. A **closed, named set** makes the
   claim true and terminates the search.
2. **Three prose drifts from #13363's post-merge review are still present**, all of the class this
   issue spent cycles 9–12 removing — *prose claiming more than the registry does*. A reader who
   trusts them re-derives the same false absolute.

**Falsifier (carried from the research block, and it did not fire):** if the registry already recorded
a target after `_seed_demo_graph` ran, this lane would not be a hole and only the prose would be left.
The measurement above is the disproof of that. **Confidence:** high — the number is a direct reading,
and the lane is one hop from the sanctioned writer.

## 2. Alternatives considered

| # | Alternative | Why not |
|---|---|---|
| 1 | **Only add the missing `note_live_write_target` call** | Closes this lane and leaves the universal false — the next lane found is the next finding. Fixes the instance, not the class. |
| 2 | **Only close the named set (prose), record the demo seeder as residual** | The owner offered this as option 2, and it does terminate the search — but the demo seeder **chooses its graph**, so the residual's own justification ("such a writer cannot hide the split") is false for it. Leaving a known graph-choosing lane uncounted while the docstring says the set is closed would re-create the exact drift being fixed, one layer down. |
| 3 | **Add the call AND make the named set mechanical (chosen)** | Cheap: one call, one constant, one AST census. The census is what makes "closed" **checkable** instead of another prose promise — a new lane fails the suite until it is declared, which is `tests/test_9124_declared_live_doors.py`'s established idiom in this repo (*"derived from the SOURCE … so a function added later cannot slip in undeclared"*). |
| 4 | Derive the counted set at RUNTIME (wrap the helper, census call sites dynamically) | The helper is already the single chokepoint; what is missing is *which callers are supposed to exist*. A runtime census answers "who called", never "who should have". It also cannot fail a PR. |

Chosen: **3**. It closes the instance and the DECLARATION drift, and each half is independently
falsifiable.

## 3. Complexity and domains

`standard`. Domains: `code` (one behavioural call site), `docs-product` (three docstrings become
verifiable statements), `protocol`-adjacent (the counted-set declaration is an instrument contract —
but **not** gate-enforcement: nothing here refuses a write or a merge, so no `Threat classes`
sub-block and the adversarial 2-cycle bound does not apply).

## 4. Integration Surface Map

| surface | what it is | test layer |
|---|---|---|
| `hosted_api._seed_demo_graph` | the lane under fix; reached from `public_demo` REST (`:41105 → :41153 → :41161`) and MCP (`mcp_server.py:9015, 9024`), and from `/internal/demo` (`:20635`) | unit, against a real embedded graph |
| `cache_leg._COUNTED_LIVE_WRITE_LANES` | new declaration; read by the census test and cited by both docstrings | unit (AST census over `tortoise/`) |
| `projection.note_live_write_target` | unchanged behaviour; docstring's population claim replaced | unchanged (existing 5 lanes stay pinned) |
| `projection._apply_event._skip` | unchanged behaviour; summary line + marked-exit enumeration corrected | unchanged (existing corpus test) |
| `metrics()['cache_write_targets']` | unchanged shape; NEW non-empty reading for the demo lane | unit |

## 5. Research

`<!-- research: v1 -->` on #12943 (comment `7236051267`). It settles four things this plan rests on:
the universal claim is false (`projection/__init__.py:4748`); the demo lane chooses its graph
(`hosted_api.py:20407`), so the residual does not excuse it; the drift list from #13363's post-merge
review is still live (`projection/__init__.py:12971`, `cache_leg.py:271`, the two missing marked arms);
and the repo already has the idiom for a mechanical census (`tests/test_9124_declared_live_doors.py`).
Externally, the CQRS literature's own failure catalogue names "a projector that skips messages" as a
first-class divergence class and verifies projections by **per-projection** lag/freshness SLO — a
measure you cannot compute over a population you cannot enumerate, which is why the answer is a
**closed set** rather than one more call site.

## 6. Wiring

| thing touched | every call site / importer / consumer | what it does there |
|---|---|---|
| `note_live_write_target` (called) | `projection/__init__.py:12957` `FalkorProjection.apply` | the fold's one call, after a returned non-skipped fold |
| | `projection/edges.py:1572` `_record_structural_edges` | the structural-edge mint |
| | `onboarding/state.py:711` `_run` | declared live gateway |
| | `webhook_delivery.py:239` `_run` | declared live gateway |
| | `why_snapshot.py:149` `_run` | declared live gateway |
| | `hosted_api.py:25218` `_capture_session_impl._sweep_orphaned_writes` | raw graph-choosing sweep |
| | `hosted_api.py:31000` `delete_session._delete_session_sync` | raw graph-choosing sweep |
| | **`hosted_api.py` `_seed_demo_graph` (NEW)** | **raw graph-choosing demo seed — the lane this change adds** |
| `note_live_write_target` (defined) | `tortoise/cache_leg.py:236,278` | docstring's "ONE helper" and the replay-window exclusion — both stay true |
| `_COUNTED_LIVE_WRITE_LANES` (NEW) | `tests/test_12943_cache_leg.py` (census test) | the authority the census compares the AST against |
| | `cache_leg.note_cache_write_target` docstring | cites it as the closed set, replacing "SEVEN call sites, and the list is maintained rather than implied" |
| | `projection.note_live_write_target` docstring | cites it as the closed set, replacing the universal |
| `_seed_demo_graph` (callers, unchanged) | `hosted_api.py:20657` `/internal/demo`; `:41153`/`:41161` `public_demo` REST; `:45824`, `mcp_server.py:9015, 9024` | none change behaviour; they inherit a correct registry reading |

## 7. Implementation steps

1. **Prove the hole (already measured).** `_seed_demo_graph` on an embedded SDK → `26 nodes`,
   `targets {}`. → *test:* `test_the_demo_seeder_records_its_target` — RED before the fix.
2. **`hosted_api._seed_demo_graph`:** one `note_live_write_target(proj)` after the sentinel statement
   (`:20594-20599`), the same shape `delete_session` uses (*"One call for the lane, after the last
   statement"*). The `already_seeded` early return issues only a read and stays uncounted. →
   *test:* step 1's test goes green; deleting the call reds it (mutation proof).
3. **`cache_leg._COUNTED_LIVE_WRITE_LANES`:** the eight `(module, qualname)` rows above, next to
   `cache_write_targets`. → *test:* `test_the_declared_counted_set_is_the_ACTUAL_call_sites` — an AST
   census over `tortoise/` for `note_live_write_target(...)` calls, enclosing-class/function qualified,
   compared **exactly** to the constant (a stale row fails, a new undeclared lane fails).
4. **`cache_leg.note_cache_write_target` docstring:** `SEVEN` → `EIGHT`, add the demo-seeder bullet;
   delete the false `"a lane that could choose would have to go through a fold or a mint."` and say
   what is true — a graph-choosing lane calls the helper directly, which is why three of the eight do.
5. **`projection.note_live_write_target` docstring:** replace `"Called from every gateway that writes
   the cache WITHOUT folding a record,"` with the closed-set statement naming
   `cache_leg._COUNTED_LIVE_WRITE_LANES`; add the demo seeder to the bulleted population.
6. **`projection._apply_event._skip` docstring:** correct the summary line (`"counts writes LANDED on
   a graph"` → it counts a fold that RETURNED and that no mark covered); add the two missing marked
   arms (`SessionDeleted` with no id, `EventRecorded` with no writable `eventId`); correct
   the old wording *"an empty merge_ids"* → *a `merge_ids` with no usable `str` id*
   (`{"merge_ids": [None, 7]}` is
   non-empty and IS marked — the arm's own comment measures that case).
7. **Regression:** `tests/test_12943_cache_leg.py` (whole file) + `tests/test_9124_declared_live_doors.py`
   + `tests/test_7951_delete_resurrection.py` + `ruff check` on the changed files.

## 8. Rounds

`**Rounds:** 1 (verifiers: 1, class: code)` — the plan's review round is the harness reviewer at the
PR head (this lane has no `task` tool by dispatch contract, so no separate PLAN-VERIFY verifier is
dispatched). Round 1 returned three P2 findings, all fixed: a third hand count left in
`cache_leg.py`'s `_TARGETS` docstring, an over-broad census claim (it binds the declaration against
the call sites, not the omission direction), and this marker being an unfilled template. A capped or
unclean exit is never reported as clean — see the marker's own note above.

### Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

+ **R1 — the registry over-reports the demo lane**: `note_live_write_target` is called on a path that
  writes nothing (e.g. after the `already_seeded` early return, or after a read), so a process reads
  a target it never wrote → caught by `test_the_demo_seeder_records_its_target` driving BOTH arms: the
  `already_seeded` re-run must leave the registry unchanged, and only the seeding run may move it.
+ **R2 — the observability hook breaks the write it observes**: `_seed_demo_graph` is on the
  provisioning/demo path, and a raise inside the hook would drop a seed that had already landed
  (the `#5148` rule) → caught by the same test's positive assertion (the seed returns
  `{'status': 'demo_created'}` AND the registry is non-empty in the same call) plus
  `note_live_write_target`'s own total-guard, which already wraps every step including endpoint
  resolution.
+ **R3 — the declared set rots into prose again**: a ninth lane is added and the docstring keeps
  saying "every gateway" → caught by `test_the_declared_counted_set_is_the_ACTUAL_call_sites`, which
  fails on an undeclared call site AND on a stale row. This is the risk the whole change exists for,
  so it gets a mechanical check rather than a promise.
+ **R4 — the census is blind and claims otherwise**: the AST scan misses a call shape (an alias, a
  `getattr`, a handle stashed on an object) → caught by the census test declaring its blind spots in
  its docstring and asserting the *discovered* set is non-empty and contains the eight known rows, so
  a scanner that silently finds nothing fails instead of comparing `[] == []`.

**Out of scope — must NOT be raised** (note, file, do not chase):

+ `record-reconcile` diverging on an operator Point's `operator`/`pointKind` — a different root, filed
  as **#12949**; #12943's remaining acceptance target, untouched here.
+ The read-side fallback (`TORTOISE_RECORD_SERVE`) — **#12941**, merged; nothing here changes it.
+ Why the demo seed writes raw Cypher rather than folding a record — a §17.1 architectural question
  with its own home (`#13440`), and reversing it is not in this change's gift.
+ Style/prose preference in the three docstrings beyond making their claims TRUE.
+ A ninth-lane hunt: this change's answer to that class is the census, not another enumeration cycle.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.
