<!-- research-path: docs/plans/2026-10-09-5362-retroactive-correction.md -->

# Plan — #5362: retroactive (backdated) correction as a first-class operation

**Issue:** daniel-ospina/tortoise#5362 (enhancement, lane `c4-answer-quality`)
**Branch:** `design/5362-retroactive-correction` (base `origin/main` @ `7360bab7d`, 2026-10-09)
**Tier:** complex (ontology semantics + a new write path + a proposed surface change)
**Status:** ⛔ **DESIGN ONLY — no implementation in this branch.** The MCP/SDK surface this needs is
**requested** in §4 and must be approved by Daniel before any code is written. This PR does not
close #5362.

**Pair requirement (owner ruling, #5362):** `supersede_point`'s refusal in #4021 is settled *because*
this operation exists as the first-class route for retroactive correction — "#4021 and this issue must
land as a pair, or #4021's refusal reads as a dead end." §5 measures that condition against `main`
today. **It is currently half-met: #4021 is shipped and this operation is not.**

---

## 0. Process, tier, and deviations (disclosed, not hidden)

This plan is the PRODUCE half of an `issue-scoping` double diamond plus a `writing-plans` design pass.
The diamond's artifacts (divergence, convergence, external research, verification) are summarised here
with their provenance; the process log is §11.

**Deviations, stated rather than buried:**

1. **Dispatch counts are Standard-tier (1 sub-agent per phase), not Complex-tier (2).** #5362 carries
   no `**Complexity Rating**` block, so the tier was inferred. It is a design-only task whose
   capability question the owner has already ruled on, on a box whose load average was **203** at the
   time of writing (`uptime`, 2026-10-09; the fleet itself is the load). The verification gates were
   run at full strength (problem-verify, a duplication/architecture pass, and a `plan-review` cycle) —
   it is the *per-phase diverge/converge agent count* that is reduced, not the gates.
2. **Phase 1.5 external research was executed by the Phase-1 diverge agent** under the `research`
   skill rather than by the controller. Axes were `Ontology = high` and `Architecture = high`;
   findings are in §10. Search tool: **rung 2** — `web_search` with `model="sonar"` named explicitly.
   Rung 1 (`mcp_load seo-intelligence`) is **UNAVAILABLE on this machine** (`mcp_catalog` lists only
   `exa`, `brave-search`, `playwright-browser`, `gemini`, `search-console`, `tortoise`). One query
   returned HTTP 429 and was retried. This is a tooling gap, not a reasoned choice (agent-infra #400
   is the class: a silent fallback erases the config gap).
3. **The solution-converge phase was adjudicated by the controller**, not dispatched, because the
   divergence output already isolated the decision axes and the surface choice is Daniel's to ratify
   (§4). The independent checks are the solution-verify pass and the `plan-review` cycle (§11).
4. **No tests were executed.** The box is loaded and this is a design task. Every semantic claim below
   is derived by reading the code at `7360bab7d`; the one existing zero-length pin
   (`tests/test_validity_windows.py:1293 test_supersede_equal_start_allowed`) was **read**, not run.

---

## 1. Confirmed problem

### 1.1 Premise re-derivation against `main` (`7360bab7d`) — measured, not inherited

| Premise from the issue | Verdict | Evidence |
|---|---|---|
| #4021 landed | ✅ **LANDED** | commit `b2448869d` ("refuse the inverted supersede window… (#4021) (#5506)") is an ancestor of `origin/main` `7360bab7d` (`git merge-base --is-ancestor`) |
| `#4021`'s `OVERRIDES:` marker is present | ✅ **PRESENT** | two comments on #4021, 2026-09-25: the scoping comment (`OVERRIDES: refuse a retroactive successor…`) and the review comment |
| No `correct_at` / `backdate` / retroactive-correction verb exists | ✅ **CONFIRMED ABSENT** | repo-wide `grep -rn "correct_at\|backdate\|retroactive"` over `*.py`/`*.md`/`*.yml` returns: test helpers (`tests/test_reaper.py::_backdate_dir`), a script comment (`tools/pr_lead_time.py:85`), `#5365` test *names*, and prose. No SDK method, no `TOOL_REGISTRY` entry, no `mode`/`retroactive` parameter anywhere on the lifecycle verbs |
| Neither terminal verb accepts an effective/retroactive time | ✅ **CONFIRMED** | `supersede_point` `tortoise/sdk.py:8732` — `valid_from` is a CLAIM that must AGREE with the successor's stored start (agreement guard `sdk.py:8900-8915`); `invalidate_point` `sdk.py:8608` stamps `validTo = now`; `retract_point` `sdk.py:9273` writes no window at all |
| A retroactive successor is refused | ✅ **CONFIRMED, but SCOPED** | `_supersede_window_end(*, old_id, new_id, old_vfs, valid_from, stored_vf, successor_created_at, now)` at `sdk.py:4122`; refusal `sdk.py:4285`; call `sdk.py:8928`. It fires **only when the predecessor's own `validFrom` is present AND orderable** — a `None` start is `continue`d (`sdk.py:~4268`) |
| The affected read path | ✅ **CONFIRMED** | `restore_point_at` `sdk.py:19098`; local `_covers` `sdk.py:19180` (`vf is None or key(vf) <= key(t)`, `vt is None or key(vt) >= key(t)` — **closed** intervals); `_inverted`/`malformed` `sdk.py:19221` |
| The shared window contract | ✅ **CONFIRMED** | `validate_validity_window(valid_from, valid_to)` `tortoise/commit_schema.py:433` (the ONE HOME; analogue of `validate_span` `:396`); equality (`[t,t]`) is **legal**, only strict inversion is refused |
| The fold sites | ✅ **CONFIRMED** | `_fold_point_superseded` `tortoise/projection/entities.py:2139`; `_fold_point_invalidated` `:2246`; `_merge_corrects_edge` `:2044`; the CONSISTENCY reference fold `tortoise/consistency.py:1043-1048` reads `ev.get("valid_to")` |

**Two corrections to the issue's own framing, measured:**

- **The capability is not uniformly absent — it is absent for one input class.** An **open-start**
  predecessor (`validFrom` absent — ONTOLOGY §4.7 vstart row: mining W-4 writes no `validFrom`, #3654)
  is `continue`d by #4021's loop, so `supersede_point(old, new)` **already accepts** a successor whose
  start precedes the (absent) predecessor start, closing the predecessor to `(-∞, vC]`. That is
  ordinary forward contiguity, not a retroactive correction, and it must not be conflated with one.
  The genuinely unexpressible input is: a predecessor whose own `validFrom` is **present and
  orderable** `vP`, and a corrector whose start `vC` sorts **strictly before** `vP`.
- **The missing thing is not the window shape.** The collapse `validTo := validFrom` is **already
  legal, already produced by the existing forward verb on the equality boundary, and already pinned**:
  `supersede_point(old, new)` with `vC == vP` writes `validTo == validFrom` (`sdk.py:4285` is strict
  `<`), and `tests/test_validity_windows.py:1293` asserts it (`assert op["validTo"] == op["validFrom"]
  == "2026-06-10"`, assert at `:1302`). `update_point(old, validTo=old.validFrom)` (`sdk.py:8158` →
  `_refuse_inverted_point_window` `sdk.py:4008` → equality allowed) also writes that shape **today**.
  What is missing is the **first-class, journaled, replay-exact, semantics-documented operation** that
  creates the CORRECTS edge and the terminal stamp in one convergent step — none of which the
  `update_point` route provides (§1.3, R5).

### 1.2 Confirmed problem definition (one sentence)

**`supersede_point` derives the predecessor's `validTo` from the successor's window *start* and
refuses a successor that sorts strictly earlier than a predecessor whose own `validFrom` is present
and orderable (#4021) — so the retroactive input class has no first-class route, and the missing
capability is ONE designed operation that terminalizes the live-tip predecessor, wires the CORRECTS
edge and transfers its edges in a single validated-emit-then-mutate step, collapsing the displaced
predecessor's window to the only well-formed non-forward shape the closed-interval model admits —
`[validFrom, validFrom]` — with a journaled event that an existing fold already replays verbatim.**

Eligibility is stated as a precondition rather than a capability claim: the correction applies when
`vC <= vP` (correct) on the live tip; `vC > vP` is a forward supersede and routes to
`supersede_point`; an absent/unorderable `vC` or `vP` has no defined collapse target and is refused.

### 1.3 Instant analysis (what actually changes)

Tip `P[vP, ∞)`; corrector `C[vC, ∞)`; `vC < vP`; CORRECTS `(C)->(P)`; the correction collapses `P` to
`[vP, vP]`.

| instant | before the correction | after | why |
|---|---|---|---|
| `t < vC` | absence | absence | neither window covers |
| `vC ≤ t < vP` | absence | **C** | `C`'s own assertion, independent of the correction |
| `t = vP` | P | **`ambiguous` (C and P)** | closed intervals: `[vP, vP]` still covers `vP` |
| `t > vP` | P | **C** | the substantive reroute |

Read from `P` itself (the displaced point): `t > vP` now returns honest absence — `P` no longer
answers, which is the intended consequence. Read from `C`: the chain is `[C, P, …]`.

### 1.4 Falsification check

This definition is wrong if any of these holds:

1. **A first-class route already exists.** Falsified today: no verb/param applies an effective time
   *and* terminalizes *and* wires CORRECTS in one operation (`grep` in §1.1). A future verb would
   falsify it.
2. **The representation can express a well-formed empty window.** Falsified today: `_covers`
   (`sdk.py:19180`) matches any `[x,x]`; the only non-covering shape is inverted, which
   `validate_validity_window` refuses and `#5361` flags `malformed`. If a half-open `_covers` were
   adopted, "collapse is the only admissible shape" fails — and so does the fudge argument (§3.3).
3. **A recorded decision forbids the capability.** The record says the opposite (the owner approved
   it). If the owner reverses, the premise fails.
4. **`vC <= vP` is reachable through an existing verb atomically.** Falsified today (§1.1).
5. **The correction's target need not be the live tip.** Falsified today: `_assert_lifecycle_guard`
   (`sdk.py:8480`) refuses a terminal source (#2498, recorded).

### 1.5 Contradiction test (run FIRST, per AGENTS.md)

| Recorded decision | Contradicted? |
|---|---|
| **#4021 — refuse a retroactive successor (fail-closed)** + its `OVERRIDES:` marker | **No.** The new route never persists `validTo < validFrom`; it writes `validTo == validFrom`. #4021's refusal is not relaxed — it is *not selected* by a different verb. #4021's plan §7 explicitly left option (B) "normalise the predecessor window (clamp) … available as a follow-up if the owner rules for it" — and the owner ruled for it on #5362 |
| **#3980/#3984 — nullable `valid_from` agreement guard** | **No.** Untouched. The new route has no `valid_from` kwarg at all |
| **#2498 — one-way terminalization; a dead claim must not be re-stamped** | **No.** The new route terminalizes a *live* tip exactly once; it never re-opens a terminal point |
| **ONTOLOGY §4.7 — the two temporal axes; supersession as a third fact** | **No new axis, no new slot.** Only a semantics statement is added |
| **MCP/SDK surface approval (AGENTS.md / CONTRIBUTING.md)** | **Respected.** §4 requests; nothing is implemented |
| **The #4021 plan's closed-interval boundary ambiguity is "pre-existing, out of scope"** | **Not reopened.** This plan *accepts* the residual rather than reopening it (§3.3), and files the untracked ambiguity separately (§9) |

**Verdict: no contradiction. The candidate is live.** The one departure from an *industry* default
(SQL:2011/DB2 forbid zero-length periods) is not a recorded decision against us — it is marked with an
`OVERRIDES:` line (§3.5).

---

## 2. Scope

### In scope

- The **semantics** of the retroactive correction: precondition, the window it collapses, the
  terminal stamps, and how the chain reads before/after (§3.1–§3.3).
- The **operation's shape** and the **surface request** for it (§3.4, §4).
- Its **journal event** — which event family it emits and why — and its **rebuild fold** (§3.4).
- The **interaction with #4021's refusal**, including the pair requirement (§5).
- The **ONTOLOGY §4.7 statement** required (the ontology is canonical; a code/ontology disagreement
  means the work is unfinished) (§3.6).
- The **preview parity** obligation (the MCP `dry_run=True` preview must refuse/collapse identically;
  the #4021 plan set this precedent by construction, `mcp_server.py:5092`/`:5201`, and the hosted
  mirror `hosted_api.py:13880`).
- Tests and acceptance criteria (§6).

### Out of scope — different roots, filed or already filed (§9)

- The **closed-interval / half-open representation rewrite** and the shared-boundary ambiguity
  (`t == validTo == successor.validFrom` → `ambiguous`). A different root; it flips the meaning of
  every *persisted* inverted window and would collide with #5361's detection. MUST be its own issue.
- **Partial / middle coverage and multi-successor corrections** (the SQL:2011 "split"). The lifecycle
  guard confines the target to the live tip, and `CORRECTS` is 1→1 — so no split arises for a legal
  target. A finite-period correction is a *different feature*.
- **`commit_ops.apply_supersessions`' routing** of a retroactive ingest record into the new operation
  (#5365's root; it now surfaces the skip — `a44d19a10`).
- **The hosted commit path's** 422 mapping and the orphan successor (#5363; landed, `30f281da3`).
- Already-filed window roots: #5358, #5359, #5360, #5361, #5374, #5375.
- Any **transaction-time** change (`createdAt`/`expiredAt` semantics).
- Exposing `restore_point_at` on the MCP surface (§4.4 — flagged, not requested).

**Boundary: equality is allowed, and the direction is what distinguishes the verbs.**
`vC == vP` stays with `supersede_point` (already accepted; `test_supersede_equal_start_allowed`).
The new route owns `vC < vP`. `vC > vP` is a forward supersede and routes to `supersede_point`.
Making the overlap a *refusal with a pointer* (rather than an implicit behaviour) is deliberate: each
verb then has one statable precondition and one outcome.

**The boundary is a partition only for a well-formed, single-node predecessor — and the gaps are
named, not glossed.** Three inputs are refused by BOTH supersede verbs, so the escape is
`retract_point` (window-agnostic) or repairing the record:

1. A **duplicate-id predecessor whose node starts differ** (§3.1 precondition 4 — a differing *set* refuses whether or not it straddles `vC`):
   `supersede_point` raises `InvertedSupersedeWindow` on the late sibling
   (`tests/test_validity_windows.py:1248` builds exactly this fixture), and the new verb refuses
   because the fan-out is not a single start.
2. A **corrector with no stored (or unorderable) `validFrom`** whose forward-resolved start
   (`createdAt`/`now`) sorts before `vP`: the forward verb refuses as inverted, and the new verb
   refuses for want of an effective instant (precondition 3). On MCP this is genuinely stuck —
   `tortoise_supersede` exposes no `valid_from` kwarg (`mcp_server.py:5207`) — so the escape is
   repairing the corrector's stored start or `retract_point`.
3. A **legacy inverted predecessor** (B9) and any **terminal/operator endpoint** (B7/B11): intended
   fail-closed; the escape is repair or `retract_point`.

These are accepted as fail-closed, and the escape is named in both refusal messages. Widening the new
verb to resolve `vC` through `_supersede_window_start_source` (the forward verb's fallback chain) was
considered and rejected: it would make the retroactive verb's effective instant a clock/`createdAt`
fallback rather than the corrector's own declared start — manufacturing the very instant the
operation exists to carry.

---

## 3. Design

### 3.1 The operation

**Precondition (all checked before any mutation or emit):**

**Evaluation order matters and is normative**: steps 1–2, then the two fan-out start-set reads
(steps 3, 4), then the integrity/window checks and the start comparison (steps 5–7). A mixed fan-out
must be reported as a *fan-out* refusal, not as a forward-input refusal, because §2's escape guidance
depends on which message fires.

1. `old_id != new_id` (shared with `supersede_point`).
2. Both exist, neither is an operator, neither is terminal — via the **shared**
   `_assert_lifecycle_guard` (`sdk.py:8480`), never a copy. (Consequence: the target is the **live
   tip**; a mid-chain correction is unreachable and out of scope.) ⚠️ This guard reads `row[0]` only
   (`sdk.py:8506-8516`), so on a duplicate id with **mixed** status it is row-order dependent — a
   **pre-existing** weakness shared with `supersede_point`, declared out of scope here and filed as
   **#7822**; the plan therefore claims this guard as B7/B11's control for **single-node** ids only.
3. **Every node carrying `new_id` carries the SAME present+orderable `validFrom`** — call it `vC`.
   Read the **full sequence**, not `[0]`: `supersede_point` reads the corrector's start first-row-only
   (`stored_vf = vf_rows[0][0]`), so a duplicate-id corrector makes the verdict (and the journaled
   `valid_from`) depend on server row order — the #4021 defect class relocated to the corrector. A
   differing set ⇒ refuse. All-absent ⇒ refuse (no effective instant: a retroactive correction needs a
   declared start, and the `createdAt`/`now` fallback is deliberately **not** used — §2).
   *A NUMERIC epoch is orderable* (`_created_sort_key` → `(0, float)`, and #3985 settled that a
   numeric `0` is a real start), so numerics are **accepted**, not refused.
4. **Every node carrying `old_id` carries the SAME present+orderable `validFrom`** — call it `vP`.
   Point ids are not unique and the fan-out is a tested, graph-reachable shape with starts on **both**
   sides of `vC` (`tests/test_validity_windows.py:1248` builds exactly that). A single scalar `validTo`
   written to every node cannot satisfy a mixed-start fan-out: `validTo := vC` **inverts** the later
   sibling (the #4021 defect, silently) and `validTo := vP` **widens** the earlier one; either way the
   verdict depends on row order. Collect every node's start (the `old_vfs` **sequence** read #4021
   introduced at `sdk.py:8927`); a differing set ⇒ refuse; an all-absent set ⇒ refuse (no collapse
   target — and note an *open-start* predecessor is already handled by forward supersession, §1.1, so
   this is not a capability regression). If the set is one value, the collapse is written **per node**.
5. **No node carrying `old_id` has a strictly inverted stored window** (`validTo < validFrom`) —
   detected with the same `_created_sort_key` measure `_inverted` uses (`sdk.py:19221`). Refuse before
   any mutation: the collapse overwrites `validTo`, which would silently **repair** a legacy
   corruption to `[vP, vP]`, make it audit-clean (`audit.py:665` is strict) and destroy #5361's
   `malformed` signal — resurrecting a point at an instant it never honestly covered.
6. **The corrector's window is OPEN**: `new.validTo` is absent on every node carrying `new_id`.
   Refuse otherwise. This is load-bearing for the operation's own claim: §1.3 says the corrector
   answers `[vC, ∞)`. A corrector whose stored `validTo` is already set (reachable — `update_point`
   carries no lifecycle guard, **#7819**) would cover only `[vC, vC_to]`, so collapsing the
   predecessor would delete coverage the predecessor used to provide, and the journaled
   `valid_from=vC` would assert an authority the graph does not hold. That is a **fail-open** silent
   absence, not a refusal.
7. `vC < vP` — compared with `tortoise/search_engine._created_sort_key`, the same measure `_covers`
   uses. `vC > vP` ⇒ refuse with a pointer to `supersede_point` (the mirror image of #4021's refusal
   pointing at `retract_point`). `vC == vP` ⇒ refuse with a pointer to `supersede_point` (it already
   accepts equality).

⛔ **All of 1–7 live in ONE read-only validator** — a private `_assert_retroactive_correction(...)`
called by the writer, the MCP preview and (if it ever learns the resolution) the hosted mirror — so
the preview cannot drift from the writer (the #3985/#4057 class). See §3.4 and §7 row 8.
**Effect (one validated-emit-then-mutate sequence, mirroring `supersede_point`'s ordering):**

| write | value | note |
|---|---|---|
| `old.validTo` (**per node**) | that node's own `validFrom` | the collapse, written **per matched node** (`SET n.validTo = n.validFrom`) rather than as one scalar to every node — fan-out-safe by construction; the only new graph shape, and the forward verb already produces it on the equality boundary. Each resulting pair is asserted through the **one-home declaration** `validate_validity_window(n.validFrom, n.validTo)` (`commit_schema.py:433`), so the new writer is a *driver* under the shared contract, not a second undeclared writer |
| `old.status` | `'superseded'` | the supersession discriminator — never set by `invalidate_point` (ONTOLOGY §4.7 ‡) |
| `old.outdated` | `true` | shared marker (identifies nothing on its own) |
| `old.expiredAt` | `now` | transaction-time expiry: *when we learned*, not *when it stopped being true* |
| `old.updatedAt` | `now` | one clock read, journaled as `recorded_ts` (#5048: a second read makes live/rebuild drift) |
| `old.validFrom` | **untouched** | a world fact — never re-stamped (the invariant `_fold_point_invalidated` documents at `entities.py:2246`) |
| `new.validTo` | **untouched, and required absent** | precondition 6: the corrector's own assertion must be open, else the correction removes coverage |
| `(new)-[:CORRECTS]->(old)` | MERGE | `_merge_corrects_edge` `entities.py:2044` |
| operator/structural edges | transferred from `old` to `new` | exactly as `supersede_point` (§3.4: one shared implementation) |
| EP invalidation + `_mark_dirty` | as `supersede_point` | #2422 ordering: drops BEFORE the epoch bump |

**Precondition 4 makes the scalar fold equivalent to the per-node write.** The live writer is
per-node (`SET n.validTo = n.validFrom`); `_fold_point_superseded` (`entities.py:2202-2214`) writes
the **scalar** journaled `valid_to` to every node matching the id. They agree **because** precondition
4 guarantees every node's `validFrom == vP` and the event carries `valid_to = vP`. That dependency is
the reason the precondition is not decoration — without it the fold and the live write diverge.

**"Atomic" is not claimed.** The sequence is *validated-emit-then-mutate* — the same posture as
`supersede_point`, which is two queries (the terminal `SET` and the CORRECTS `MERGE`, `sdk.py:9234`
then the MERGE). A crash between them leaves the predecessor terminal with **no CORRECTS edge**; the
retry raises on the terminal guard, so recovery is manual or a rebuild. This is a **pre-existing,
inherited** crash window, not introduced here, and it is recorded as a residual (§3.7, RES-6) rather
than hidden behind the word "atomic".

### 3.2 Semantics — the issue's three open questions, answered

**(a) Which predecessors' windows re-close?** Exactly one: the target. There is no set to choose from,
because the lifecycle guard makes the target the live tip — and the tip is by construction the only
point whose open end can be re-closed without touching a point that already has a successor.

**(b) May a retroactive correction *empty* a predecessor window?** **No — not under this
representation, and the design does not pretend otherwise.** The closed-interval model has **no
well-formed empty window**: `_covers` matches any `[x,x]` at `x`, and the only window that covers no
instant is inverted (`validTo < validFrom`) — which `validate_validity_window`
(`commit_schema.py:433`) refuses, `audit.py:665` flags as `inverted_validity_window`, and #5361
reports as `malformed`. So the correction collapses the predecessor to the **minimum** legal window,
`[vP, vP]`, and accepts **one deliberately-ambiguous instant** at `vP`. Truly emptying the window is
the half-open representation's job and is a different root (§2, R2).

**(c) How does the chain read afterwards?** See §1.3. From `C`: `C` answers `[vC, ∞)` except the
single instant `vP`, where `C` and the collapsed `P` both cover and `restore_point_at` returns
`{ambiguous: true, candidates: [C, P]}`. From `P`: `P` answers nothing after `vP` — honest absence.

### 3.3 The ambiguous instant — stated as created, not inherited

The instant `t = vP` reads `ambiguous` because `P`'s zero-length sentinel and `C`'s open window both
cover it. The **class** is pre-existing — a forward chain `P[vP, vC] ← C[vC, ∞)` already reads
`ambiguous` at `t = vC` because `_covers` is closed on both ends, and the #4021 plan §1 recorded that
as a pre-existing property out of scope. But the **instance is manufactured by this operation**, and
this plan does not launder it as "pre-existing": the correction deliberately mints one
guaranteed-ambiguous instant, and that instance is untracked (§9 files the class).

Why accept it rather than fix it here: the alternatives that remove it are (i) a half-open `_covers`
(R2 — the global boundary-semantics flip: the answer at `t == validTo` changes for **every** window in
**every** chain, so every window consumer must be re-verified; strictly inverted windows remain
detectable, so #5361's detection is **not** defeated — an earlier draft said otherwise and that claim
is retracted), (ii) an inverted window as the empty sentinel (refused by #4021/#5374 — and that WOULD
be reversing a recorded decision), (iii) a new node flag or an edge-carried correction (R3 — moves the
authority for "what is true at t" off the canonical window onto a slot every `_covers` consumer must
consult; strictly larger reader blast radius). The residual is one instant, it is the same failure
class the system already tolerates at every window boundary.

⚠️ **"Fail-safe" has a caveat that must be stated**: `restore_point_at`'s `ambiguous` branch sets
`ambiguous`/`candidates` and returns **without setting `found`** (`sdk.py:19198-19216`). A consumer
reading `res.get("found")` or `res.get("valid_point")` therefore reads the manufactured ambiguity as
"nothing was true at `vP`" — the silent wrong answer the branch exists to prevent. That gap is
**pre-existing** (it applies to every forward boundary today), belongs with the same read-path root,
and is recorded on **#7818**; this plan does not fix it and does not rely on `found` being absent. A fourth alternative — a **chain-aware boundary
tie-break in `restore_point_at`** (prefer the successor at `t == validTo == successor.validFrom`) —
is evaluated as **R10** and rejected: it would make the one SDK-only reader disagree with every other
window consumer (`retrieval`, `assembly`, `time_aware`, `audit`) unless all of them adopted it — the
same all-readers change as R2, plus a new inconsistency between readers.

### 3.4 Operation shape, journal event, and rebuild fold

**Shape.** A new public SDK method with a **single, statable precondition**, and a new MCP tool. The
510-line body of `supersede_point` is **not** duplicated: the window-end resolution becomes an
explicit `end_source` parameter of one private implementation
(`supersede_point` → `end_source=successor_start`; the new verb → `end_source=predecessor_start`),
with #4021's inversion guard active only on the `successor_start` path — so the forward verb's
behaviour stays byte-identical and the edge-transfer / re-run-convergence / mark-dirty machinery is
shared verbatim.
This is the repo's own "one home, two callers / parity by construction" idiom (`_supersede_window_end`,
`_supersede_window_start_source`, `_assert_lifecycle_guard`, `_fold_point_restamp`).

**Journal event — reuse `PointSuperseded`; do not mint a type.** Emit

```python
PointSuperseded(id=old_id, new_id=new_id,
                valid_from=vC,           # the corrector's own start — for the FIRST time ≠ valid_to
                valid_to=vP,             # the collapsed predecessor end
                expired_at=now,
                retroactive=true,        # payload-only discriminator; folds ignore it
                recorded_ts=now)         # #5048 — one clock read, journaled
```

Why reuse rather than a new `PointRetroCorrected` type:

- **Zero fold work, and correct by construction.** `_fold_point_superseded`
  (`entities.py:2139`) replays `valid_to`/`expired_at` **verbatim from the payload**; the CONSISTENCY
  reference fold does the same (`consistency.py:1043-1048`). No fold reads `valid_from` (verified:
  `grep` for `ev.get("valid_from")` across `projection/` and `consistency.py` → none). So the collapse
  is already replayable and live/rebuild parity is free.
- **Every registration site is already correct for it**: `_GRAPH_EVENT_TYPES` (`sdk.py:2503`) —
  membership gates the `:GraphEvent` write; `_POINT_RESTAMP_EVENT_TYPES`
  (`projection/__init__.py:3554`) and `plan_point_restamp_folds` (keyed on the literal
  `"PointSuperseded"`); `classify_terminalizer_miss` (`projection/nonfolded.py:182`);
  `TORN_TAIL_HARMLESS_EVENT_TYPES` (`log.py:138`) — where `PointSuperseded` sits in the
  **RESURRECTION** direction, i.e. a torn tail is refused, which is the fail-closed polarity we want;
  `CLAIM_EVENT_TYPES` (`shared_state/events.py:162`); the `events_poll` catalogue
  (`mcp_server.py:2275`).
- **A new type costs ~8 registration edits, each of which is a silent-fold-miss or a
  fail-closed-refusal site** (a type absent from `_GRAPH_EVENT_TYPES` silently skips the graph store;
  a type absent from `apply()`/`_NO_POINT_FOLD` makes a graph-backed replay **refuse**; a type absent
  from the consistency fold makes `check_consistency` report DIVERGENCE). None of that risk buys
  anything the `retroactive: true` payload key does not: `events_poll` carries the payload, so the
  record is distinguishable for audit consumers.

**One discriminator, asserted as a partition — scoped to writer-produced payloads.** The
`PointSuperseded` type carries both an explicit `retroactive: true` key **and** the payload shape
(`valid_from != valid_to`). The key is kept because the **owner is choosing between event shapes**
(§4.1 row 3: reuse-with-key vs a distinct type) and because `events_poll` consumers read the payload —
but a discriminator defined twice with no assertion is free to disagree, so the invariant is pinned
**for records this writer produces**:
**`retroactive: true` present ⇔ `valid_from != valid_to`** — asserted on BOTH arms (forward: key
absent, `valid_from == valid_to`; retroactive: key present, `valid_from != valid_to`). A
one-directional test on the new arm alone is not a partition assertion. The only existing payload pin
is the forward arm (`tests/test_validity_windows.py:632`, `valid_from == valid_to == "2026-06-14"`).
⚠️ **Scope**: the invariant is about the *payload the writer emits*, not about graph-level
retroactivity — a keyless legacy record cannot violate it, because every `PointSuperseded` emit site
(`sdk.py:8943`) sets `valid_from` and `valid_to` from one value. Graph-level retroactivity is a
separate property the key does not encode.

**The new writer routes through the shared window declaration.** `validate_validity_window`
(`commit_schema.py:433`) is declared "THE ONE HOME for the rule"; the collapse is asserted through it
**per node** (§3.1 effect). This matters because the new verb is otherwise a **second writer** of the
same state `supersede_point` writes (`old.validTo`) — and a second writer with no shared contract
diverges silently, as lost fields rather than as an error. (Writer-enumeration predicate and the
reader-driver exclusion are in §7, note **W**.)

**Rebuild fold: unchanged — and that is the point.** "Its rebuild fold" is the existing
`_fold_point_superseded`. A test pins live-vs-rebuild byte-identity through the existing
`tests/test_pointsuperseded_rebuild.py` (both replay engines) and
`tests/test_commit_supersession_parity.py`.

### 3.5 ONTOLOGY §4.7 statement required

Add to ONTOLOGY §4.7 (`docs/ONTOLOGY.md:1001`), under the `validTo` row / the supersession paragraph:

- **A retroactive correction is a supersession whose predecessor end resolves to the predecessor's
  OWN start**, not to the successor's start. It is the one documented exception to "`validTo` = the
  successor's `validFrom`". Condition: the corrector's stored `validFrom` sorts strictly before the
  predecessor's, both present and orderable, and the predecessor is the live tip. Result:
  `[vP, vP]` — a zero-length window, already legal per `validate_validity_window`.
- **The chain reads afterwards**: the corrector covers `[vC, ∞)`; the displaced predecessor answers
  no instant after `vP` and is `ambiguous` only at `vP`.
- **Scope boundary stated in the doc**: tip-only; `vC >= vP` routes to `supersede_point`; open-start
  and unorderable starts are refused (with the note that an open-start predecessor is already handled
  by forward contiguity); partial/middle coverage is not expressible under `CORRECTS` 1→1.
- **The `OVERRIDES:` line** (also to be posted on #5362 at approval — the marker belongs on the
  artifact a lane reads, and the plan doc is not that artifact):

> **OVERRIDES:** SQL:2011 / IBM DB2 `BUSINESS_TIME` / Postgres forbid zero-length periods and answer a
> fully-subsuming application-time correction by SPLITTING or DELETING the covered row — this model's
> closed intervals admit no well-formed empty window, so the displaced predecessor is collapsed to the
> zero-length `[validFrom, validFrom]` (already legal and already produced by `supersede_point` on the
> equality boundary) and one instant at that boundary is deliberately ambiguous.
>
> **OVERRIDES (intra-file polarity):** `_supersede_window_end`'s comment (`sdk.py:4276-4277`) says "two
> guards in one file must not return opposite verdicts for one input" — the new verb **refuses** an
> unorderable predecessor start where #4021's path **skips** it. The verdicts are deliberately
> opposite because the comparison IS the new operation's whole effect, whereas #4021 compares only in
> order to decide a refusal. The comment is **scoped to the `end_source=successor_start` path**, both
> polarities are pinned by a test over the same input (§6.2 test 4b), and this marker records the
> departure so the next reader does not "tidy" one of them away.

- **Changelog entry** in the ONTOLOGY changelog block, stating the deliberate zero-length departure,
  the tip-only scope, the `#4021` pair, and the filed residual (the closed-interval boundary class).

### 3.6 Interaction with #4021's refusal (the pair)

- **#4021's refusal stays exactly as shipped.** The new verb is a *sibling route*, not a relaxation:
  the inversion guard remains active on the `successor_start` path and is simply not the resolver the
  new verb uses.
- **The refusal message gains a forward pointer — and it must be scrub-stable.** #4021's refusal
  already names `retract_point` as the window-agnostic escape. Once this capability exists, the
  retroactive case should also name the new verb. ⛔ The pointer wording must avoid the scrubber's
  `(host=|at |to )[\w.-]+` rule (`mcp_server.py:1144`): a phrase like "…**to record** a correction" is
  rewritten to `to ***` and reds the existing
  `tests/test_validity_windows.py:1381 test_supersede_refusal_message_survives_scrub`, which asserts
  `_scrub_error(msg) == msg` for the forward refusal. Use a scrub-safe construction (e.g. "…or
  `supersede_retroactively()` for a correction whose start precedes the predecessor” — no `at`/`to`
  followed by a word). This is a message-only change, error-class unchanged, so the hosted 422 mapping
  (#5363) is unaffected. The scrub-stability test is extended to the **modified forward refusal**, not
  only the new verb's message.
- **Pair landing.** The owner's condition is that #4021 and this issue land as a pair or #4021's
  refusal reads as a dead end. §5 measures it. The sequencing mechanism is explicit: the surface
  approval (§4) is the gate, and the implementation PR lands the verb + tool against #4021's shipped
  refusal. **Nothing in this design is implemented until the surface is approved.**

### 3.7 Adversarial threat surface — DECLARED

This is a **write boundary whose correctness is "a caller cannot make the write path fail open"** —
it mints a terminal state and a window. Untrusted input: the `old_id`/`new_id` pair and the stored
temporal properties of both points (reachable through the SDK, MCP and the hosted path).

| # | Adversarial input | Required behaviour |
|---|---|---|
| B1 | `vC > vP` (a forward correction routed here) | refuse BEFORE any mutation/emit, naming `supersede_point` |
| B2 | `vC == vP` | refuse, naming `supersede_point` (it already accepts equality) |
| B3 | corrector's stored `validFrom` absent, or present-but-unorderable (`""`, `"TBD"`) | refuse BEFORE any mutation/emit — no effective instant. ⚠️ A **numeric epoch is orderable** (`_created_sort_key` → `(0, float)`) and is therefore **accepted**, per #3985 — it is NOT an unorderable case, and a test expecting refusal there would contradict a recorded decision |
| B4 | predecessor's stored `validFrom` absent | refuse — no collapse target (and it is not a capability regression: the open-start path is already handled by forward supersession) |
| B5 | predecessor's start present-but-unorderable | refuse — an ordering-fallback comparison must not decide a terminal write. This is the **opposite polarity to #4021's skip on the same input**, deliberately: #4021 *skips* an unorderable start because it only compares in order to refuse, whereas here the comparison IS the operation's whole effect. The `_supersede_window_end` comment forbidding opposite verdicts in one file is **scoped to the `end_source=successor_start` path**, an intra-file `OVERRIDES:` marker records the polarity (§3.5), and one test pins **both** verdicts for the same input |
| B6 | `old_id == new_id` | refuse |
| B7 | either endpoint operator / terminal, on a **single-node** id | refuse via the shared lifecycle guard (#2498). ⚠️ On a **duplicate id with mixed status** that guard reads `row[0]` and is row-order dependent — a **pre-existing** weakness, declared out of scope here and filed as **#7822** |
| B8 | a point id with MULTIPLE nodes (duplicate fan-out), starts **differing** — **whether or not they straddle `vC`** | refuse before any mutation/emit — no single scalar collapse exists for a mixed fan-out (`validTo := vC` inverts the late node; `validTo := vP` widens the early one) |
| B8b | a point id with MULTIPLE nodes, starts **agreeing** | collapse **per node** (`SET n.validTo = n.validFrom`) — no sibling inverted or widened, verdict independent of row order |
| B9 | a predecessor whose window is ALREADY inverted (legacy corruption) | refuse before any mutation/emit (precondition 5) — the collapse would silently *repair* it to `[vP, vP]`, making it audit-clean (`audit.py:665` is strict) and destroying #5361's `malformed` signal |
| B10 | `dry_run=True` on any refusal-class input | refuse identically over the whole **B1–B13** set — no fail-open preview |
| B11 | the corrector is not itself live / is terminal, on a **single-node** id | refuse via the shared guard (same #7822 caveat as B7) |
| B12 | the corrector's own window is **CLOSED** (`new.validTo` present) | refuse (precondition 6) — otherwise the collapse deletes coverage the predecessor provided and the journaled `valid_from=vC` asserts authority the graph does not hold (a **fail-open** silent absence) |
| B13 | a **duplicate-id corrector** (`new_id`) with differing or absent starts | refuse (precondition 3) — a first-row read makes the verdict and the journaled `valid_from` row-order dependent (the #4021 class on the corrector) |

### Residual risk register (accepted, with polarity)

| # | Residual | Polarity | Silent mis-read possible? |
|---|---|---|---|
| RES-1 | `t = vP` reads `ambiguous` (one instant per correction, §3.3) | fail-safe **only if the consumer tests `ambiguous`** — the branch omits `found` (§3.3) and the reader is SDK-only | **Yes**, for `.get("found")` consumers; recorded on **#7818** |
| RES-2 | both-refuse inputs (§2) | fail-safe (refuse; escape named) | no |
| RES-3 | no automated trip for "#5362 never lands" | process | n/a |
| RES-4 | `audit.py:665` flags only strict `k_to < k_from`, so the collapse is audit-invisible (and it never counted overlapping candidates) | fail-safe but **under-reports** | "audit clean" ≠ "no ambiguity" |
| RES-5 | concurrent mutation between the validating read and the write (single-writer assumption, §3.7) | the per-node `SET n.validTo = n.validFrom` cannot invert even under a race; the *semantic*/journal scalar can drift | semantic only |
| RES-6 | crash between the terminal `SET` and the CORRECTS `MERGE` | fail-safe (divergence detectable by `check_consistency`) but not retry-recoverable | no |
| RES-7 | the hosted path 422s and ingest warn-skips a retroactive record | fail-closed / visible skip, but the capability is unreachable there | filed **#7821** |

**Explicitly OUT of scope:** any concurrent mutation between the validating read and the write
(single-writer assumption) — the per-node write is what makes this safe against *inversion*; the
`found`-omission in the ambiguous branch (**#7818**); a mid-chain target (unreachable by the guard); a
partial/middle-span correction (not expressible); duplicate-id **mixed-status** endpoints
(**#7822**); legacy inverted windows already persisted (#5361); the hosted/ingest **routing**
(**#7821**).

Because the surface is declared, the plan review is bounded to 2 cycles and acceptance is
threat-list coverage (§11).

---

## 4. SURFACE REQUEST — for Daniel's approval (do NOT implement before approval)

> **Rule invoked:** AGENTS.md / `CONTRIBUTING.md` §"The MCP tool surface and public SDK methods
> cannot grow by accident". `tools/surface-guard.py` and `config/surface-manifest.yml` are **drift
> controls, not approvals** — a change that updates both together passes them, so a green run is not
> consent. The owner's #5362 ruling explicitly defers this request to design time; this section is it.

### 4.1 Requested additions (primary proposal)

| # | Surface | Name | Signature / shape | What it does | Why it is needed |
|---|---|---|---|---|---|
| 1 | **SDK method** (public, `TortoiseSDK`) | `supersede_retroactively(old_id: str, new_id: str) -> dict` | no time kwarg — the effective instant is the corrector's stored `validFrom` | Terminalizes the live-tip `old_id` as retroactively superseded by `new_id`: collapses `old.validTo` to `old.validFrom`, sets `status='superseded'`/`outdated`/`expiredAt`, MERGEs `CORRECTS (new)->(old)`, transfers edges, emits the journaled `PointSuperseded` (§3.4). Refuses with `ValueError` when the input is not a retroactive correction (`vC >= vP`) — naming `supersede_point` — or when either start is absent/unorderable | **Without it the shipped #4021 refusal is a dead end.** The SDK is where the read path lives (`restore_point_at` is SDK-only), so an SDK-only fix would leave the *write* unreachable for the MCP agents that hit the refusal |
| 2 | **MCP tool** (`TOOL_REGISTRY`) | `tortoise_supersede_retroactively` | `(old_id, new_id, dry_run=False)`, `served: http`, family `memory`; wrapper is `_safe(_quota_gated(_get_org_sdk().supersede_retroactively, "points"), ...)` — `_quota_gated` `mcp_server.py:1040`, `tortoise_supersede` precedent `:2245` | Serves #1, with `dry_run=True` preview parity; the quota wrapper is required, not optional (a bare `_safe(...)` escapes the write-op meter, #681/#308) | The refusal that #5362 exists to complement is exposed over MCP as `tortoise_supersede` (`tool_registry.py:571`) — so without this tool the MCP surface keeps the dead end the owner barred. Agents are the dominant consumer of the MCP surface |
| 3 | **Journal payload** (not a surface addition, but recorded here for completeness) | `PointSuperseded` gains a `retroactive: true` payload key | fold-inert | Distinguishes a retroactive correction in `events_poll` without minting an event type | Mints no new event type, so no registration-site risk (§3.4). **If Daniel prefers a distinct type string, say so** — the cost is the ~8 registration sites enumerated in §3.4, and it is a strictly larger change |

**Follow-on mechanical steps (after approval, per CONTRIBUTING):** re-cut
`config/surface-manifest.yml` (+ `config/surface-order.yml` baseline counts), render
`docs/product/mcp-sdk-surface.md`, and record the per-row approval. **A new public SDK method also reds
an independent declaration**: `tools/sdk_surface.py --check` (run by the required `docs` job,
`.github/workflows/ci.yml:677` compares the public methods against `config/sdk-surface.json` and
`docs/product/sdk-surface-declaration.md` — so those two must be re-cut too, and
`tests/test_sdk_surface.py` (dual-registered) is the test-side pin. `tools/surface-guard.py`'s
`_fingerprint` digests the served function's code object, so the guard reds until the baseline is
re-cut — **that red is not the approval**.

### 4.2 Naming — proposed primary and alternatives

**Primary: `supersede_retroactively` / `tortoise_supersede_retroactively`.** Rationale: the operation
*is* a supersession (terminal status + CORRECTS + edge transfer), and the ONTOLOGY (§4.7 ‡) explicitly
warns that the `outdated` + `CORRECTS` pair alone makes every *invalidated* Point read as superseded.
A name in the `supersede_*` family cannot be confused with `invalidate_point`; a name like
`correct_point` could be. It also does **not** imply a time argument, which matters: there is none.

Alternatives, for Daniel to choose at approval: `correct_retroactively`, `retroactive_supersede`,
`backdate_point`. **`correct_at` (the issue's example) is recommended against**: it implies an
effective-time parameter the operation does not take.

### 4.3 Alternative shape — an operation parameter instead of a new verb (considered, not recommended)

`supersede_point(..., retroactive=False)` + `tortoise_supersede(..., retroactive=False)`, defaulting
to today's behaviour. This is the form the external standards converge on (SQL:2011
`UPDATE … FOR PORTION OF`, XTDB `FOR PORTION OF VALID_TIME`) — but that convergence is about *SQL's
statement grammar*, not about API design, and the standard's *semantics* (split into contiguous rows)
do not apply here (§10). Arguments against it, on outcome quality:

- It makes ONE verb's **precondition disjunctive** (`vC >= vP` forward, `vC < vP` retroactive) and its
  **outcome mode-dependent** (truncate at the corrector's start vs collapse to the predecessor's own
  start). Each of the two operations has one clean, statable contract; a mode makes the contract
  conditional for every caller and every agent reading the tool description — the MCP description is
  the primary discovery surface for agents.
- It makes the shipped forward verb's contract depend on a flag, so the forward verb stops being
  independently reversible.
- It does **not** save the surface gate. ⚠️ An earlier draft said this because "the guard fingerprints
  the served function's code object"; that is only half right and the reasoning was wrong. Measured:
  `tools/surface-guard.py::_fingerprint` digests the **MCP served handler**
  (`tortoise/mcp_server.py:tortoise_supersede`) and the SDK rows are compared as a **name set** — so a
  mode alternative reds the gates because it must edit the **MCP handler**, not because it edits
  `supersede_point`'s body. (This also means Task 1's private refactor reds **nothing**; see §8.) The
  saving is one tool row (and the baseline counts).
- The `transfer_edges=False` leg of `tortoise_supersede` reuses `_preview_invalidate`
  (`mcp_server.py:5060`), so the mode would need an explicit rejection on that leg or the invalidate
  preview silently previews a retroactive supersede.

If Daniel prefers this shape, the semantics in §3 are unchanged and only the surface delta shrinks.

### 4.4 Not requested (flagged)

`restore_point_at` is **SDK-only** — it has no `TOOL_REGISTRY` entry, no MCP handler and no hosted
route (verified: `grep -rn restore_point_at tortoise/tool_registry.py tortoise/mcp_server.py` → empty;
`tools/sdk_rename_table.py:328` maps it to a BETA `get_historical_knowledge`). So the write proposed
here would be **observable over MCP only indirectly** (via `tortoise_get_point` props /
`tortoise_events_poll`), not through the temporal read path that defines its semantics. That asymmetry
is recorded here as an open item and is **not** part of this request — exposing the read path is a
separate surface decision.

---

## 5. `#4021` dependency state (measured against `origin/main` @ `7360bab7d`, 2026-10-09)

| Question | Measured answer |
|---|---|
| Is #4021 landed? | **YES.** `b2448869d` "fix(temporal): refuse the inverted supersede window instead of persisting it (#4021) (#5506)" is an ancestor of `origin/main` (`git merge-base --is-ancestor b2448869d origin/main` → 0) |
| Where is the refusal? | `_supersede_window_end` `tortoise/sdk.py:4122` (the change), `raise InvertedSupersedeWindow` `sdk.py:4285`, call site `sdk.py:8928`; the MCP preview mirrors it (`mcp_server.py:5092`, `:5201`); the hosted pre-write mirror `hosted_api.py:13880` |
| Is the `OVERRIDES:` marker present, as the issue claims? | **YES.** Two comments on #4021 dated 2026-09-25 carry it — the scoping comment ("OVERRIDES: refuse a retroactive successor whose window start precedes the predecessor's own `validFrom` (the 'be liberal / normalise the interval' default)…") and the review comment ("OVERRIDES: the 'be liberal / normalise the interval' default at the supersession write…") |
| Is the **pair** satisfied? | **NO — half-met.** #4021 shipped alone; #5362 is unscoped. The shipped system therefore carries the refusal with no first-class route — the exact state the owner barred ("or #4021's refusal reads as a dead end"). This is recorded here and in the PR body |
| Dependent siblings, so the surrounding input class is not still silent | All measured **landed** on `main`: **#5359** `55b0291b5` (inverted window refused at the point write boundary), **#5363** `30f281da3` (hosted refusal → actionable 422 instead of a retry-advising 500), **#5365** `a44d19a10` (the fail-open supersession skip made visible to the caller) |
| Consequence for this issue | #5363/#5365 closed the *visibility* of the refusal's consequences; the *capability* is still missing, and #5365 is **closed**, so it can no longer host a deferral. #5362 is the second half of the pair, and its sequence gate is the **surface approval** in §4. **Mechanism (added after review):** the pair status is (a) measured in this section, (b) posted as a note on #4021 so the refusal's reader can see the second half is in flight, and (c) an acceptance criterion on #5362 (§6.3-10). ⚠️ There is **no automated trip** for "#5362 never lands": the only reader of (c) is a #5362 reader, and the named triggers are the **surface approval** (§4, the sequence gate) and the **owner** Daniel. That residual is accepted and stated, not implied by prose |

---

## 6. Tests and acceptance criteria

Domain classification for §3.7: **adversarial** (a declared threat surface). Acceptance for review is
threat-list coverage, not reviewer exhaustion (§11).

### 6.1 Test homes (CI registration is part of the deliverable)

| File | Lane (measured in `config/ci-surfaces.yml`) | Why |
|---|---|---|
| `tests/test_validity_windows.py` | `core` (:1793) | the window contract home; `test_supersede_equal_start_allowed` (`:1293`) is the existing zero-length pin this extends |
| `tests/test_dry_run_preview.py` | `core` (:984) | preview parity; the #4021 preview tests (`:959`, `:966`) are the precedent |
| `tests/test_pointsuperseded_rebuild.py` + `tests/test_pointinvalidated_rebuild.py` | registered | **both replay engines** must reproduce the collapse byte-identically (§3.4) |
| `tests/test_pointsuperseded_rebuild.py` | **`ep` (:1968)** | the **both-replay-engine** pin for the `PointSuperseded` family — the right home for the collapse's live-vs-rebuild parity, including the `apply()`/`recover_from_log`/`backup.restore` arm (via `apply_journal_point_restamp`), which no §8 task otherwise exercises. ⚠️ `tests/test_commit_supersession_parity.py` (`api`, :512) is the **hosted commit write-phase** suite (#2193 — `commit_ops.apply_supersessions` routing), **not** a rebuild suite; an earlier draft mis-cited it |
| `tests/test_surface_manifest.py`, `tests/test_tool_registry.py`, `tests/test_surface_resolution.py`, `tests/test_retired_tools.py`, **`tests/test_sdk_surface.py`** | registered (the last is dual-registered `api`+`core`) | tool-count/registry/baseline pins **and the independent SDK-surface declaration** (`tools/sdk_surface.py --check`) — red until the surface is approved AND every baseline is re-cut |
| **new** `tests/test_5362_retroactive_correction.py` | **needs a NEW registration** in `core` (house convention: `tests/test_invalidate_inverted_window_5358.py` `core`:1806, `tests/test_5365_fail_open_visible.py` `core`:1800, `tests/test_validity_windows.py` `core`:1793 — note the `sdk`-lane siblings `test_5359_unchecked_window_writers.py`:2133 and `test_validity_window_contract_5374.py`:2166) | the feature's own lane |

`tortoise/sdk.py`, `tortoise/mcp_server.py`, `tortoise/tool_registry.py` and
`tortoise/projection/__init__.py` are `SHARED_MODULES` (`tools/ci_selection.py:174-232`) ⇒ the change
runs the full matrix.

### 6.2 Position tests (each REDs at the pre-change head)

1. `test_retroactive_correction_collapses_predecessor` — `old[06-10]`, `new[06-01]`:
   `old.validTo == old.validFrom == 06-10`, `status='superseded'`, `outdated=True`, one CORRECTS
   `new→old`, `new.validTo` untouched.
2. `test_retroactive_correction_chain_reads` — via `restore_point_at`: `t=06-05 → new`;
   `t=06-10 → ambiguous(C,P)` (B-NOTE: the deliberate residual, pinned as intended, not as a bug);
   `t=07-01 → new`; entering from `old` at `t=07-01 → found=False`.
3. `test_retroactive_correction_forward_input_routed` — `vC > vP` refuses and names `supersede_point`;
   `vC == vP` refuses and names `supersede_point` (B1, B2).
4. `test_retroactive_correction_missing_or_unorderable_starts` — absent corrector start, absent
   predecessor start, `""`, `"TBD"` (B3, B4, B5). ⚠️ **No numeric-epoch case**: a numeric is
   *orderable* (`_created_sort_key` → `(0, float)`; #3985 settled that `0` is a real start), so the
   operation **accepts** it — an assertion expecting refusal would be unsatisfiable without
   contradicting a recorded decision. A separate assertion pins the **acceptance** instead.
4b. `test_unorderable_start_polarity_is_opposite_by_design` — **the same input** (an unorderable
   predecessor start) through BOTH verbs: `supersede_point` **skips** it (#4021; the `continue` at `sdk.py:4278`, guarded by `if k_old[0] != 0:` at `:4271`) and
   the new verb **refuses** it. Pins the intra-file polarity (§3.5 marker) so the departure cannot be
   "tidied" silently.
4c. `test_retroactive_correction_corrector_fanout_refused` — a duplicate-id **corrector** with starts
   `06-01`/`06-20` against `old` at `06-10`: refuse (B13), with the fixture premise asserted; the
   verdict must not depend on row order (run BOTH insertion orders).
5. `test_retroactive_correction_terminal_and_self_inputs` — `old == new`; terminal source; terminal
   target; operator endpoint (B6, B7, B11).
6. `test_retroactive_correction_duplicate_id_mixed_starts_refuses` — a duplicate-id predecessor whose
   node starts **differ** → refuse before any mutation/emit (B8), asserted for **both** shapes: starts
   that **straddle** `vC` (`06-01`/`06-20` vs `06-10`, the `tests/test_validity_windows.py:1248`
   fixture) **and** starts that are all *after* `vC` (`06-11`/`06-12` vs `06-01`) — precondition 4
   refuses on a differing *set*, not on the straddle, so a test that only covers the straddle would
   pass for an implementation that violates the rule. Run both insertion orders.
6b. `test_retroactive_correction_duplicate_id_equal_starts_collapses_every_node` — the same fan-out
   with **agreeing** starts → accepted, and every node collapsed (`validTo == validFrom` on EACH node;
   B8b). ⚠️ This test does **not** discriminate per-node vs scalar-from-first-row: with agreeing starts
   the two writes are byte-identical (§3.1). The per-node form is justified as race-independence
   (§3.7 RES-5), **not** by this test — an earlier draft's mutation-proof claim ("a scalar write REDs
   test 6b") was false.
7. `test_retroactive_correction_refuses_a_legacy_inverted_predecessor` — `old.validFrom=06-10`,
   `old.validTo=06-01`, corrector `06-05` → refuse before any mutation/emit; assert the **inverted
   window is still inverted afterwards** (no silent repair) and that `_inverted`/`malformed` still
   reports it (B9, precondition 5).
8. `test_retroactive_correction_emits_and_replays_identically` — live vs `rebuild_all` byte-identity
   through the EXISTING fold; asserts `valid_from != valid_to` **and** the `retroactive` key on the
   retroactive arm (§3.4).
8b. `test_pointsuperseded_discriminator_is_a_partition` — the partition invariant of §3.4: forward
   arm (key **absent**, `valid_from == valid_to`) and retroactive arm (key **present**,
   `valid_from != valid_to`), asserting the two definitions agree in both directions.
9. `test_retroactive_correction_message_survives_scrub` — `_scrub_error(str(exc)) == str(exc)` and the
   message names `supersede_point`, mirroring #4021's scrub-stability AC.
10. Preview parity (in `test_dry_run_preview.py`) — a **differential over B1–B13**, not a
    single dated pair: for each refusal class, `dry_run=True` returns the writer's scrubbed error and
    `_graph_counts` is unchanged (B10). One input only would let a preview that reports success on
    another class's refusal pass — the fail-open direction the preview exists to prevent.
11. `test_forward_supersede_behaviour_unchanged` — a regression pin that `supersede_point`'s outputs
    are byte-identical for a representative forward input (the shared-implementation refactor must be
    behaviour-preserving).
12. `test_forward_refusal_message_remains_scrub_stable` — extends
    `tests/test_validity_windows.py:1381` to the **modified forward refusal** (the new pointer,
    §3.6): `_scrub_error(msg) == msg`.

13. `test_retroactive_correction_refuses_a_closed_corrector_window` — `new.validFrom=06-01`,
   `new.validTo=06-05` (reachable via `update_point`, #7819), `old.validFrom=06-10` → refuse (B12,
   precondition 6); assert the graph is **unchanged** (`old` still live, window intact, no event).
   Mutation proof: **drop precondition 6 alone → this test REDs by proceeding**, and the resulting
   state fails the companion assertion that `restore_point_at(new, 07-01)` is not absence — i.e. the
   collapse deleted coverage (`new` covers only `[06-01, 06-05]`) while the journal asserted
   `valid_from=06-01`.
14. `test_retroactive_correction_replay_parity_across_engines` — the retroactive payload through
   `rebuild_all` **and** the `apply()`/`recover_from_log`/`backup.restore` arm
   (`apply_journal_point_restamp`), byte-identical; extend
   `tests/test_pointsuperseded_rebuild.py` (lane `ep`) rather than asserting parity no test produces.
**Mutation proof (to record in the implementation PR):** disable the collapse (resolve
`end_source=successor_start`) → tests 1, 2, 8, 8b, 10, 14 RED; disable the precondition refusals →
tests 3, 4, 4b, 4c, 5, 6, 7, 13 RED; **removing precondition 5 (legacy inversion) alone → test 7
RED**; **removing precondition 6 (closed corrector) alone → test 13 RED**; restore → green. (Tests 6b,
11 and 12 are **regression pins** on the shared refactor, not guard mutations — no mutation proof is
claimed for them: test 11 is exercised by the whole existing lane, test 12 pins the forward message's
scrub-stability directly, and test 6b's claim is withdrawn in its own note.)

### 6.3 Acceptance criteria

1. **No implementation in this PR.** The surface request (§4) is pending Daniel's approval; no
   `TOOL_REGISTRY`, `TortoiseSDK` or manifest change is present in this branch.
2. `supersede_point`'s behaviour is unchanged for every previously accepted/refused input (the shared
   implementation is behaviour-preserving; pinned by test 11 and the existing
   `tests/test_validity_windows.py`, `tests/test_dry_run_preview.py`).
3. The new operation refuses **before** any mutation or emit on every **B1–B13** input (no phantom
   event, no partial write) — the #4021 ordering precedent.
4. Every declared **refusal** class **B1–B13** is covered by a specific named test that REDs when the
   guard is absent — the adversarial-domain acceptance criterion (§11.2). **B8b is the accepted-path
   per-node collapse, not a refusal class**: it is pinned by test 6b, and no mutation proof is claimed
   for it (§6.2).
5. The `t = vP` ambiguity is **pinned as intended behaviour** (test 2), and the ONTOLOGY statement
   records it — the operation does not hide a manufactured ambiguous instant.
6. Live-vs-rebuild parity is byte-identical through the **existing** fold, with no new event type, and
   the single discriminator satisfies the partition invariant on BOTH arms (test 8b).
7. ONTOLOGY §4.7 carries the statement + **both** `OVERRIDES:` lines (§3.5) + the changelog entry.
8. The closed-interval boundary class is **filed as its own issue** (**#7818**), not absorbed; the
   `update_point` guard hole is filed (**#7819**).
9. `tools/surface-guard.py`, `tools/surface_manifest.py check` **and `tools/sdk_surface.py --check`**
   (`ci.yml:677`, inside the required `docs` job) are green **at the current baseline** for this branch
   (no surface change here). In the implementation PR all three are expected RED until the approval is
   recorded and `config/surface-manifest.yml`, `config/surface-order.yml`, `config/sdk-surface.json`
   and the two rendered docs (`docs/product/mcp-sdk-surface.md`,
   `docs/product/sdk-surface-declaration.md`) are re-cut — **each re-cut owned by a task** (§8).
10. **The pair condition has a mechanism, not just a record.** This PR (a) records the measurement in
    §5, (b) posts the pair-status note on #4021, and (c) adds the pair gate to #5362's acceptance
    checklist. No automated trip exists for "#5362 never lands" — that residual is **accepted and
    stated**, so the condition cannot rot silently. #5362 is not closed by this PR.
11. The duplicate-id fan-out is refused on either endpoint when starts differ (tests 6, 4c) and
    collapsed **per node** when they agree (test 6b); the on-issue `OVERRIDES:` markers are posted to
    #5362 (Task 4), not only written in this doc; and `docs/event-catalog.md`'s `PointSuperseded` row
    names both emitters and the `retroactive` key.

---

## 7. Integration surface map

Focused to the surfaces this change touches or whose contract it must not break. `file:line` measured
at `7360bab7d`.

| # | Surface | file:line | Type | Flow | Contract | Key failure modes | In this change |
|---|---|---|---|---|---|---|---|
| 1 | `_supersede_window_end` | `sdk.py:4122` | SDK helper | read + guard | resolve predecessor end; refuse strict inversion | inverted window; silent unreachable point | **gains** an `end_source` parameter |
| 2 | `_supersede_window_start_source` | `sdk.py:4070` | SDK helper | read | PRESENT+ORDERABLE predicate; one home for **2** callers (`_supersede_window_end` `sdk.py:4246`; the hosted mirror `hosted_api.py:13967`) | predicate drift (#3985 class) | unchanged |
| 3 | `InvertedSupersedeWindow` | `sdk.py:3985` | exception | — | named, attributed refusal; scrub-stable message; hosted 422 maps it | a retroactive refusal reusing it would be mislabeled `supersede_window_inverted` | **needs a distinct refusal path/message** |
| 4 | `supersede_point` writer | `sdk.py:8732` (emit `:8943`, stamp `:9234`) | SDK + DB write | write | validated-emit-then-mutate; CORRECTS + status + `validTo` + `expiredAt`; shared guard `:8480` | partial write; live/rebuild drift | **body factored into a shared private impl; bytes unchanged** |
| 5 | new SDK method | proposed | SDK write | write | §3.1 preconditions 1–7 (ONE read-only `_assert_retroactive_correction`) + §3.1 effect | fail-open on any B-class input | **new (pending approval)**; the validator is the **shared** home for the refusals |
| 6 | `restore_point_at` / `_covers` / `_inverted` | `sdk.py:19098` / `:19180` / `:19221` | SDK read (**no MCP/CLI/hosted route**) | read | CORRECTS walk; closed-interval coverage; `ambiguous`/`malformed`/`nearest`; ⚠️ the `ambiguous` branch sets no `found` | `ambiguous` at `t=vP` (deliberate, §3.3); `malformed` must NOT fire; a `.get("found")` consumer reads the ambiguity as absence (**#7818**) | **unchanged** |
| 7 | MCP `tortoise_supersede` + `_preview_supersede` | `mcp_server.py:2229` / `:5092`, `:5201`; `tool_registry.py:571` | MCP tool | write / dry-run | one-way guarantee: preview rejects what the write rejects (#4057) | preview/write disagreement on the retroactive arm | preview must handle the new verb; forward preview unchanged |
| 8 | new MCP tool (`tortoise_supersede_retroactively`) | proposed | MCP tool | write / dry-run | **wrapper must be `_safe(_quota_gated(_get_org_sdk().supersede_retroactively, "points"), …)`** (`_quota_gated` `mcp_server.py:1040`; `tortoise_supersede` precedent `:2245`) — a bare `_safe(...)` would escape quota enforcement and the write-op meter (#681/#308); `dry_run` parity over B1–B13 | a fail-open preview; an unmetered write path | **new (pending approval)** |
| 8b | new `_preview_correct_point` / shared validator | proposed | MCP dry-run | read | must call the **same** `_assert_retroactive_correction` as the writer (ONE home), and must **not** re-implement `_preview_supersede`'s ~400-line edge enumeration | refusal-set drift (#3985/#4057 class); a duplicated edge-enumeration surface | **new (pending approval)**; parameterise the shared preview on the same `end_source` where possible |
| 9 | `_preview_invalidate` | `mcp_server.py:5029`, reused `:5060` | MCP dry-run | read | serves `transfer_edges=False` | a mode leaking into the invalidate preview (avoided: the new verb is separate) | unchanged |
| 10 | `_fold_point_superseded` | `entities.py:2139` | journal/fold | read→write | replays `valid_to`/`expired_at` **verbatim**; no reader of `valid_from` | n/a — already supports an arbitrary end | **unchanged** |
| 11 | consistency reference fold | `consistency.py:1043-1048` | read (verification) | read | `entry["validTo"] = ev.get("valid_to")` | a NOT-registered new type ⇒ graph-vs-reference DIVERGENCE | **unchanged** (a reason to reuse the type) |
| 12 | `TORN_TAIL_HARMLESS_EVENT_TYPES` | `log.py:131-138` | journal integrity | read | NEW types default to REFUSED; `PointSuperseded` is in the RESURRECTION direction | an unlisted new type ⇒ torn-tail refusal | **unchanged** |
| 13 | `_GRAPH_EVENT_TYPES` | `sdk.py:2503` | declaration | — | membership gates the `:GraphEvent` write | a new type absent here silently skips the graph store | **unchanged** |
| 14 | `audit` check 8 | `audit.py:611-682` (`k_to < k_from` `:665`) | read (MCP `tortoise_audit`) | read | flags only strict inversion | **a zero-length window is NOT flagged** ⇒ the collapse is audit-invisible | **decision: deliberate**; noted (§9) |
| 15 | `time_aware.is_stale_entry` / `prefer_latest_order` | `time_aware.py:277` / `:316` | read (rank) | read | stale iff terminal OR `valid_to <= question_date` | the staleness **start date** moves `∞ → vP`; the point is already terminal, so status-driven staleness is unchanged | **behaviour noted** |
| 16 | `retrieval._validity_marker` / `search_engine.fetch_point_epistemic_state` / `assembly` spine | `retrieval.py:865`, `search_engine.py:2944`, `assembly.py:1014` | read (render) | read | render `validFrom`/`validTo` verbatim | a new render shape `[valid vP → vP]` (legal; previously reachable only via the equality path) | **unchanged** |
| 17 | `subgraph_render._render_date` | `subgraph_render.py:183-195` | read (render) | read | `validFrom` → `createdAt` chain | unaffected **iff** `validFrom` is not moved — a reason the design never re-stamps it | **invariant** |
| 18 | `hosted_api._prevalidate_supersede_window` + 422 map | `hosted_api.py:13880` / `:14884` | HTTP | read/write | refuses only a provable inversion; defers on the writer's clock | a legal retroactive commit 422s if the mirror keeps resolving forward | **must be reviewed at implementation** (§8.4) |
| 19 | `commit_ops.apply_supersessions` + `promote_point` | `commit_ops.py:675`; `sdk.py:9344` | ingest write (warn+skip) | write | failure ⇒ WARN + a visible skipped count (#5365, landed `a44d19a10`) | a retroactive `pt_` record stays unapplied even after the capability exists unless routed | **out of scope; routing is #5365's root** |
| 20 | `mining._temporal_wire` / `indexer/github_indexer.py` / `hosted_api` point props | `mining.py:491-520`; `github_indexer.py:668`; `hosted_api.py` (`validFrom = when`) | writers | write | stamp `validFrom` from session/commit/payload dates | these are the **backdating sources** that produce the input class | **out of scope (#5359)** |
| 20b | `docs/event-catalog.md` | `:15` | doc (payload-field record) | — | the declared payload-field catalogue: the `PointSuperseded` row names `TortoiseSDK.supersede_point` as the sole emitter and omits `valid_from`/`valid_to`/`expired_at` (already stale since #1538) | the `retroactive` key — the plan's stated discriminator for `events_poll` consumers — lands in an uncatalogued hole | **must be updated** (Task 4) |
| 20c | `_journal_safe_params` | `projection/__init__.py:2156` | journal→Cypher boundary | read | degrades an unwritable journal value to `None`; its own comment names `valid_to` as the value "that was missed" (#7369) | inert for an orderable stored `vP` | **unchanged** (verified inert) |
| 20d | replay engines: `apply_journal_point_restamp` consumers — `rebuild_all`, `_apply_replay`/`rebuild(EventLog)`, `recover_from_log`, `backup.restore` | `projection/entities.py:1907`; `backup.py:99-104` | journal/fold | read→write | "which replay engines" made explicit so parity is claimed for all of them | an engine not exercised by a test ⇒ untested parity | **unchanged**; test 14 exercises the `apply()` arm |
| 21 | `tools/surface-guard.py` + manifest + order table + rendered doc | `tools/surface-guard.py:367`; `config/surface-manifest.yml` (`:561`, `:571`, `:597`); `config/surface-order.yml`; `docs/product/mcp-sdk-surface.md` | CI gate + declarations | — | `served_from` code digest; registry count; per-row `approval` | **any** surface change reds until re-cut — and a green re-cut is not the approval | **none in this PR** |
| 22 | `tools/sdk_surface.py --check` + `config/sdk-surface.json` + `docs/product/sdk-surface-declaration.md` + `tests/test_sdk_surface.py` | `ci.yml:677` | CI gate + declarations + test | — | **the second, independent SDK-surface declaration** (a new public method reds it) | a new `TortoiseSDK` method with an un-regenerated declaration | **none in this PR**; re-cut required after approval (§4.1) |

> **Note W — how "8 window writers" is counted (D2/D3 auditability).** The predicate is
> `grep -rn 'validTo\|validFrom' tortoise/` intersected with statements that *persist* a window on
> `:Point`; **drivers are excluded** (they delegate to an SDK writer and never emit a `SET`):
> `indexer/github_indexer.py:668` (calls `create_point`/`supersede_point`),
> `hosted_api.py:14259/14406` (sets `point_props["validFrom"]` then delegates). The **direct writers**
> are: `supersede_point`'s stamp (`sdk.py:9234`), `invalidate_point` (`sdk.py:8683`), `update_point`
> (`sdk.py:8359/8365`), `_upsert_point_props`'s coalesce (`projection/entities.py:1114-1115`),
> `_fold_point_superseded` (`entities.py:2202/2206`), `_fold_point_invalidated` (`entities.py:2307/2312`),
> `mining._temporal_wire` (`mining.py:520`, guarded by `_refuse_inverted_point_window`), plus the
> **proposed** verb. Of these, the SDK-prop writers route through the one-home
> `validate_validity_window`; the folds deliberately do not (a rebuild must replay legacy windows).
> The proposed verb joins the first group by routing through the declaration per node (§3.4).

---

## 8. Implementation plan (after surface approval) — task skeleton

> Every task carries Intent / Acceptance / Files, per `writing-plans`. **None of it starts before §4
> is approved.**

### Task 1: Factor the shared supersede implementation (no behaviour change)

**Intent:** make the retroactive path share the forward path's guard/emit/transfer/mark-dirty machinery
rather than duplicating 510 lines, so the two verbs cannot drift.
**Acceptance:** `supersede_point`'s outputs are byte-identical for a representative forward input
(pinned by test 11) and the entire `tests/test_validity_windows.py` + `tests/test_dry_run_preview.py`
lanes pass unchanged.
**Files:** Modify `tortoise/sdk.py` (extract a private `_supersede_impl(..., end_source=…)`; add
`end_source` to `_supersede_window_end` **with a keyword default that preserves the forward verdict**;
the #4021 inversion guard active only on `end_source=successor_start`). ⚠️ The signature change is
**not** `sdk.py`-only — `_supersede_window_end` has **THREE** callers and all three must keep the
forward verdict: `sdk.py:8928` (the SDK's own `supersede_point` call site), `mcp_server.py:5201`
(imported at `:26`, the dry-run preview), and `hosted_api.py:13970` (imported at `:158`, the
commit-time mirror). Without the keyword default the MCP preview becomes a `TypeError`
(or, worse, a fail-open) and the hosted mirror changes its 422 contract — exactly the preview/write
drift §3.6 promises not to create, and the hosted orphan #5363 already suffered. The existing
preview-parity tests are the pin.
**Surface gates: this task reds NOTHING.** The private refactor does not add a registry row and the
guards digest the **MCP served handler** + a **name set** (§4.3), not `supersede_point`'s body.
**Test:** existing lanes (`tests/test_validity_windows.py`, `tests/test_dry_run_preview.py`,
`tests/test_commit_supersession_parity.py`).

### Task 2: The new SDK method + its refusal family

**Intent:** give the retroactive input class a first-class, journaled route, with all refusals in one
read-only home.
**Acceptance:** tests 1–9, 11, 12, 13, 14 in §6.2 pass; every **refusal** class B1–B13 has a named
test that REDs without its guard (§6.2 mutation proof); B8b is pinned as accepted behaviour by test 6b.
**Files:** Modify `tortoise/sdk.py` (new public `supersede_retroactively`; **one** private read-only
`_assert_retroactive_correction(sdk, old_id, new_id)` implementing §3.1 preconditions 1–7 — the writer,
the MCP preview and (if ever routed) the hosted mirror all call it, so the refusal set cannot drift;
a new named refusal distinct from `InvertedSupersedeWindow`; the forward pointer in #4021's message).
Create `tests/test_5362_retroactive_correction.py` + register in `config/ci-surfaces.yml` (`core`).
**Surface gates:** `tools/surface-guard.py`, `tools/surface_manifest.py check` and
`tools/sdk_surface.py --check` (`ci.yml:677`) all RED until Task 3's/§4's re-cut — expected, and a
green re-cut is still not the approval.

### Task 3: MCP tool + preview parity

**Intent:** expose the route where refusal is exposed, with a preview that cannot fail open.
**Acceptance:** test 10 passes as a **differential over every refusal class**; `dry_run` changes nothing.
**Files:** Modify `tortoise/mcp_server.py` (handler wrapped `_safe(_quota_gated(...))`; a preview that
calls `_assert_retroactive_correction` — do **not** copy `_preview_supersede`'s ~400-line edge
enumeration), `tortoise/tool_registry.py`, `config/surface-manifest.yml`, `config/surface-order.yml`,
`config/sdk-surface.json`, `docs/product/mcp-sdk-surface.md`,
`docs/product/sdk-surface-declaration.md`, and re-record `tests/test_sdk_surface.py` if it pins a
count.

### Task 4: ONTOLOGY §4.7 + changelog

**Intent:** the ontology is canonical; a code/ontology disagreement means the work is unfinished.
**Acceptance:** §3.5's statement, the `OVERRIDES:` line and the changelog entry are present; the
frontmatter `title:` and H1 are bumped with the newest changelog entry (the doc's own convention).
**Files:** Modify `docs/ONTOLOGY.md`. Also: post the **two `OVERRIDES:` lines as comments on #5362**
(§3.1/§3.5 require the marker on the issue, not only in this doc — the ledger holds only the index),
and update the `PointSuperseded` row of `docs/event-catalog.md:15` to name both emitters and the
`retroactive` key.

### Task 5: Integration review at the boundaries (no code)

**Intent:** four surfaces in §7 can be disturbed by a legal retroactive write; each gets a written
verdict at implementation time.
**Acceptance:** a recorded decision for each of: `hosted_api._prevalidate_supersede_window` (does it
422 a legal retroactive commit? if yes, it must learn the new resolution or defer — **the routing
decision is filed as #7821**, and the mirror must stay on `end_source=successor_start` until it is
made, so its 422 is correct as-is);
`commit_ops.apply_supersessions` (route vs skip — #5365's root, a separate decision, but the coupling
must be stated); `audit.py` check 8 (should a zero-length window carry a signal?);
`time_aware` staleness start date.
**Files:** none.

---

## 9. Deferred findings — to file separately (different roots, not absorbed)

| Proposed issue | Root | Evidence |
|---|---|---|
| **#7821 — a retroactive correction is unreachable on the hosted + ingest write surfaces** — `hosted_api._prevalidate_supersede_window` resolves the successor **forward** and 422s before minting it; `commit_ops.apply_supersessions` warn-skips the record; `promote_point` swallows its supersede call. The #5362 capability would therefore reach direct SDK/MCP callers only. Routing is a decision about those two pipelines, not about #5362's root | the **reachability** of the new operation on the two pipelines most agents use, separate from its existence | `hosted_api.py:13880`/`:13970`/`:14884`; `commit_ops.py:675`; `sdk.py:9344` |
| **#7822 — `_assert_lifecycle_guard` is row-order dependent on a duplicate id** — it reads `row[0]` while the sibling `_assert_window_start_not_inverted` loops the whole fan-out; on a mixed-status duplicate id the terminal/operator verdict (and the #2498 protection) depends on server row order, and the accepted case re-terminalizes a dead node | a **pre-existing** guard weakness consumed by #5362 (its B7/B11 rows are therefore scoped to single-node ids) | `sdk.py:8480`/`:8506-8516`; stamp `sdk.py:9234` |
| **#7818 — the closed-interval shared-boundary ambiguity is untracked** — `restore_point_at` returns `ambiguous` at `t == validTo == successor.validFrom` for every forward chain; documented only in the #4021 plan's §1 (whose "see §6" pointer is dangling — §6 has no such entry), absent from ONTOLOGY §4.7, and unfiled (searched). #5362 mints a fresh instance of it | the closed-interval `_covers` contract vs the half-open reading ONTOLOGY's prose implies ("the old fact stops being true when the new one starts") | `sdk.py:19180`; `docs/plans/2026-09-25-4021-inverted-predecessor-window.md` §1; `docs/ONTOLOGY.md:1001` |
| **#7819 — `update_point` has no lifecycle guard** — it writes `validTo` (and any caller prop) onto a TERMINAL point; it is the hole that makes the "compose `invalidate_point` + `update_point`" recipe work, and a latent write-path gap in its own right | a missing shared-guard call on an existing writer (`_assert_lifecycle_guard` is called by `supersede_point`/`invalidate_point`/`retract_point`, not by `update_point`) | `sdk.py:8158`; guard `sdk.py:8480`; `_refuse_inverted_point_window` `sdk.py:4008` |

Filed with the plan: **#7818** (closed-interval shared-boundary ambiguity), **#7819** (`update_point`
missing lifecycle guard), **#7821** (hosted/ingest reachability) and **#7822** (lifecycle-guard
row-order) — each checked for duplicates first, and none's root matched an existing issue.
Issue #7818 additionally received the `found`-omission evidence (§3.3) as a comment, since it is the same
read-path root.

**Recorded but deliberately NOT filed** (inside another issue's existing root, per the "symptom of an
existing issue" rule): the third/fourth backdating writers (`indexer/github_indexer.py:668`,
`mining._temporal_wire`) → **#5359**; the hosted 422/orphan → **#5363**; audit's zero-length blind
spot → a decision for Task 5 (may be correct as-is: the shape is legal and intended). ⚠️ An earlier
draft routed the ingest half to **#5365**; **#5365 is closed**, so that deferral was dead — the ingest
and hosted routing now live together on **#7821**.

---

## 10. External research (Phase 1.5 artifact)

> **Findings date:** 2026-10-09
> **Search tool:** `web_search` with `model="sonar"` — **rung 2 (cheapest, priced explicitly)**.
> **Rung 1 (`mcp_load seo-intelligence`) is UNAVAILABLE on this machine** (`mcp_catalog` lists no such
> server). One query returned HTTP 429 and was retried. 4 substantive queries.

| Axis | Framing | Finding | Provenance |
|---|---|---|---|
| Ontology / Architecture | **canonical** | SQL:2011 application-time (`FOR PORTION OF`) and IBM DB2 `BUSINESS_TIME` **split** a row when an UPDATE partially covers its period, and **forbid zero-length periods** (closed-open, `start < end`; DB2 adds an implicit constraint) | canonical — IBM DB2 `BUSINESS_TIME` docs; PostgreSQL SQL:2011 temporal notes |
| Ontology / Architecture | **canonical (corrects the issue body)** | Fowler's *RetroactiveEvent* does **not** mandate "insert at the branch point": it lists **three** adjustment styles (Replacement / Reversal / Difference) and concerns **transaction-time** accounting adjustments, not valid-time head-truncation. The issue's citation overstates its source | canonical — martinfowler.com/eaaDev/RetroactiveEvent.html, AccountingNarrative.html |
| Architecture | **competitor-precedent** | **XTDB** makes a retroactive valid-time write an **operation parameter** (`FOR PORTION OF VALID_TIME`), not a separate verb; an insert behaves like an upsert overwriting the document for that valid-time range. **Datomic** exposes `asOf` and no backdate primitive | competitor-precedent — docs.xtdb.com (time in XTDB; SQL tx reference); Datomic history docs |
| Ontology | **pitfalls** | **Anchor modeling / Data Vault** effectivity satellites model a backdated correction as a new version row with corrected business-effective dates while the earlier row is end-dated so intervals do not overlap; the named pitfall is exactly **"two rows can claim the same instant"** | pitfalls — Data Vault 2.0 community thread; datavault4dbt "How to Track Effectivity"; Scalefree "Late-Arriving Data" |
| Architecture | **precedent (in-domain)** | **Graphiti / Zep** — the model this codebase explicitly mirrors (ONTOLOGY §4.7: "the contiguous Graphiti (Zep) window intent") — has the **same forward-only rewrite** and therefore the same backdating hole, with no designed fix | precedent — Zep/Graphiti documentation (secondary source; unverified against Graphiti source) |
| Ontology | **canonical (academic anchor)** | The bitemporal literature's canonical treatment of a backdated correction is a **transaction-time version**: a new version is added with the SAME valid-time period and a LATER transaction time, so valid time is never re-written. That both (a) grounds the plan's valid-time-vs-transaction-time split (`validTo` = when it stopped being true vs `expiredAt` = when we learned) and (b) **weakens the collapse's pedigree** — the literature's default is to leave the predecessor's valid-time window alone, which would leave `P` and `C` overlapping (permanent `ambiguous` under the closed-interval read path) unless the read path carries a tie-break (R10). The collapse is chosen over that default because Tortoise's read path is window-driven and status-blind, so an untouched overlapping window is unreadable; the departure is marked in §3.5. Added after the solution-verify review flagged the omission | canonical — Snodgrass & Steiner, *Developing Time-Oriented Database Applications in SQL*; Jensen & Snodgrass, bitemporal conceptual modelling (TSQL2 lineage) — cited from the standard literature, **not fetched** (rung-2 budget; recorded as a gap in §14) |

**How the research fed the decision:** the canon *contradicts* the issue body's proposed mechanism at
two points — it forbids zero-length periods and it prefers a split — and neither is available inside
this repo's closed-interval contract. That is what produced the `OVERRIDES:` line (§3.5) instead of a
claim of convergence. The XTDB parameter-form precedent is engaged and answered in §4.3 (it is a
statement-grammar form, and its split semantics do not apply). **A canon that forbids our shape is not
a reason to hide the shape; it is a reason to mark the departure.**

### Integration Docs

No new third-party dependency is introduced (the change is in-repo Python + Cypher). The
`### Integration Docs` block for the implementation PR will be a justified skip (zero third-party
deps; all imported names are in-repo). The external research above is the Phase 1.5 artifact.

---

## 11. Process log — diamond gates

<!-- issue-scoping: v5.1 double diamond + verify -->

### Phase 0 — Tier, domain, shared-code

No `**Complexity Rating**` block on #5362 (labels: `enhancement`, `lane:c4-answer-quality`). Tier
inferred **complex**; dispatch counts run at Standard (deviation 0.1). Skill-domain/shared-code
override: `tortoise/sdk.py` etc. are SHARED_MODULES — a Standard-or-above treatment regardless.

### Checkpoints (#4907)

`parallel_work_check.sh start` → `C1: CLEAR no-board-skip` (no board session).
`parallel_work_check.sh scope` → `C2: CLEAR no-board-skip`.
`parallel_work_check.sh plan` → `C3: CLEAR no-board-skip`.
`CHECKOUT_GUARD_ENFORCE=1` set in the session env for each.

### Phase 1 — problem-diverge (1 agent, adversarial + research)

Produced four alternative framings (single-ended write surface; closed-interval + 1→1 successor;
transaction-time confusion; fold algebra), an assumption map, and 4 adversarial external queries. The
strongest argument against the issue's framing: the causal claim ("the input arises *because* #4021
refuses it") is backwards — the input's existence is a property of the representation — **and the
subsequent codebase pass confirmed the mechanism half of that while refuting the "representation"
half** (§1.1: the shape is already legal and already produced). Boundary and stakeholder lists fed
§2 and §7.

### Phase 2 — problem-converge (1 agent)

Evaluated all five framings; ran the contradiction test first (no contradiction; §1.5); narrowed the
refusal's reach to the present-and-orderable-start case; declared the representation/split/middle
coverage as separate roots; confidence **86**. The confirm's overstatements (its "every terminal
verb" phrasing, and "the only non-inverted write") were **corrected in §1.1/§1.3** after the verifier
challenged them — see §11 verify.

### Phase 2.5 — problem-verify (1 verifier, fresh context)

**cycle 1:** P0=0, P1=5, P2=7, P3=2. Controller actions — **fixed**: (P1-1/P1-4) the
representation-vs-scope rejection is now argued on outcome + blast radius (§3.3, R2) and carries an
`OVERRIDES:` line (§3.5); (P1-2) the XTDB `FOR PORTION OF VALID_TIME` precedent is engaged and
answered (§4.3, §10); (P1-3) the universally-quantified claim is narrowed to the
present-and-orderable-start case and the open-start path is recorded as already-handled (§1.1);
(P1-5) the pair-landing violation is measured and recorded (§5) with an acceptance criterion (§6.3);
(P2-1..P2-7) the edge cases are enumerated (§3.7, §6.2), "which predecessors re-close" and the
cannot-truly-empty answer are stated for the owner (§3.2), the "every terminal verb" mis-statement is
narrowed (§1.1), the untracked boundary ambiguity is filed (§9), the temporal-database canon is added
(§10), and the diverge artifact is summarized (§11 Phase 1). **No P0/P1 remains open.**
**Gate: PASS.**

### Phases 3/4 — codebase explorer + solution-diverge (1 agent, combined; deviation 0.3)

Four distinct approaches were generated and evaluated: **A** new verb (+ shared implementation),
**B** an operation parameter on the existing verb, **C** a representation route (branch-point insert /
half-open `_covers` / node flag / edge-carried correction), **D** compose existing verbs. Each carries
files, architecture, journal/fold, semantics, risks and blast radius. The explorer enumerated the
8 window writers, the 20+ readers, and the registration sites a new event type would touch — which is
what settled the reuse decision in §3.4. Rejected here with reasons: §12.

### Phase 5/5.5 — solution-converge + solution-verify

Converged (controller, deviation 0.3) on **A with the head-collapse semantics and the reused journal
event**, on the quality drivers named in §12. Independent solution-verify and a
duplication/architecture pass are recorded below.

**solution-verify — cycle 1:** see §11.1.

### Phase 7 — plan-review (adversarial domain, cap 2)

See §11.2.

---

### 11.1 solution-verify (1 verifier, fresh context)

**cycle 1:** P0=0, P1=4, P2=4, P3=1. Controller actions — **fixed**: (P1-1, **also the
duplication/architecture reviewer's P0**) the duplicate-id fan-out with **differing** starts is now an
explicit precondition (all nodes must share one present+orderable `validFrom`, else refuse — now §3.1
precondition 4), the collapse is written **per node** (`SET n.validTo = n.validFrom`, §3.1 effect) so
no scalar can invert or widen a sibling, and tests 6/6b pin both halves; (P1-2) the pair requirement
now has a named mechanism and the no-automated-trip residual is stated (§5, §6.3-10); (P1-3) the second
independent surface declaration (`tools/sdk_surface.py --check`, `config/sdk-surface.json`,
`docs/product/sdk-surface-declaration.md`, `tests/test_sdk_surface.py`) is added to §4.1/§6.1/§6.3;
(P1-4, the duplication reviewer's D4) the `PointSuperseded` discriminator is now **one** definition with
a **partition invariant** pinned on both arms (§3.4, test 8b). (P2-5) the chain-aware tie-break is
evaluated as **R10**; (P2-6) the Snodgrass/Jensen canon row is added, and it *weakens* the collapse's
pedigree — recorded rather than smoothed; (P2-7) the forward pointer is specified scrub-safe and the forward-message
test is extended to it (test 12); (P2-8) the both-refuse inputs and their escape
are enumerated (§2). (P3-9) the provenance nits are corrected: `_supersede_window_start_source` has
**2** callers, `supersede_point`'s stamp carries **no CAS** (re-run convergence instead), the pin is
`tests/test_validity_windows.py:1293` (assert `:1302`), `_inverted` is `sdk.py:19221`.
**Duplication/architecture (#688)** — P0=1 (the fan-out, fixed above as P1-1), P1=1 (A6: the
`_supersede_window_end` "no opposite verdicts" comment vs B5's deliberate polarity → comment **scoped**,
both polarities pinned by test 4b, intra-file `OVERRIDES:` marker added §3.5), P2=2 (the writer
enumeration is now auditable with its predicate and driver exclusions, §7 note W; `mcp_server.py:5201`
named in Task 1 with a keyword default). Verdicts: `unify` 1, `keep separate` 1,
`unify-contract-keep-drivers` 2 — all recorded, all folded in.
**Cycle log:** cycle 1 issues → fixed; re-verified in cycle 2 (§11.1b).

### 11.2 plan-review

**Adversarial domain** (a declared `### Adversarial Threat Surface`, §3.7): cap **2 cycles**;
acceptance = every declared threat class covered + no in-scope bypass reproduced. See §11.2b.

### 11.1b solution-verify — cycle 2 (fresh context)

**cycle 2:** P0=0. Cycle 1's fixes held, and the re-read **opened six further defects** — all of them
in the *new* material, which is what a cycle-2 pass is for. Controller actions — **fixed**:

- **(P1) The corrector's own window could be CLOSED** (`new.validTo` present, reachable today via
  `update_point`'s missing lifecycle guard, **#7819**). The collapse would then **delete coverage the
  predecessor provided** and journal a `valid_from` asserting an authority the graph does not hold — a
  fail-open silent absence. → new **precondition 6** (corrector must be open), new threat row **B12**,
  new test **13**.
- **(P1) A legacy inverted predecessor had no guard.** The collapse overwrites `validTo`, silently
  *repairing* the corruption to `[vP, vP]`, making it audit-clean (`audit.py:665` is strict) and
  destroying #5361's `malformed` signal. → new **precondition 5**, B9 rewritten, test 7 now asserts the
  window is **still inverted** afterwards.
- **(P1) The corrector's own duplicate-id fan-out was read first-row-only** (`stored_vf =
  vf_rows[0][0]`) — the #4021 row-order defect relocated to the new endpoint. → new **precondition 3**,
  threat row **B13**, test **4c** (both insertion orders), and an explicit **evaluation order** (steps
  1–2, then 3–4, then 5–7) so a mixed fan-out reports as a fan-out, not as a forward-input refusal.
- **(P1) `_assert_lifecycle_guard` itself reads `row[0]`** (`sdk.py:8506-8516`), so on a mixed-status
  duplicate id its verdict is row-order dependent — a **pre-existing** weakness. The plan therefore
  **scopes** B7/B11 to single-node ids and files the root as **#7822** rather than claiming the guard
  as a control it is not.
- **(P1) Preview parity had no construction** — "test 10" asserted an invariant with nothing
  enforcing it. → §3.1 now requires **ONE** read-only `_assert_retroactive_correction` called by
  writer + preview + (if ever routed) hosted mirror; §7 row 8b; test 10 becomes a **differential over
  every refusal class**, not one dated pair.
- **(P1) The hosted/ingest route was deferred to #5365 — which is CLOSED**, a dead deferral. → filed
  **#7821** (hosted + ingest + `promote_point` reachability) and pinned the mirror to
  `end_source=successor_start` until the owner makes the routing call (§8 Task 5).

Corrected nits (all verified at `7360bab7d`): the corrector-fanout/closed-window classes pushed the
threat table to **B1–B13**; **test 4's numeric-epoch case was unsatisfiable** (a numeric IS orderable
via `_created_sort_key`; #3985 settled that `0` is a real start, so the operation accepts it) —
removed, with an acceptance assertion instead; **test 6b cannot discriminate** per-node vs
scalar-from-first-row when the starts agree (the writes are identical), so its mutation-proof claim is
withdrawn and the per-node form is justified by race-independence; the MCP wrapper is pinned to
`_safe(_quota_gated(…))` (`mcp_server.py:1040`; `tortoise_supersede` `:2245`); `_supersede_window_end`
is named with **all three** callers (`sdk.py:8928`, `mcp_server.py:5201`, `hosted_api.py:13970`); the
**§4.3 fingerprint claim was wrong** (the guard digests the MCP served handler, and the SDK rows are a
name set — so Task 1's private refactor reds **nothing**); the provenance line `:1293`/`:1302`,
`_inverted` `:19221`, `_supersede_window_start_source` = 2 callers, no CAS in the stamp, and the test
lanes (`test_5359_unchecked_window_writers.py` is **`sdk`** not `core`; `test_pointsuperseded_rebuild.py`
is **`ep`**; `test_commit_supersession_parity.py` is `api` and is a **write-phase** suite, not a
rebuild suite) are corrected; §3.3's retracted #5361 claim is deleted, not reworded; §3.4's partition
invariant is scoped to writer-produced payloads; §3.1's "Atomic" claim is replaced by the **bounded
crash window** it actually is; and the residual register **(RES-1…RES-7)** is added.

### 11.2b plan-review (3 parallel reviewers, fresh context)

Dispatched **#1 Structural**, **#2 Integration**, **#4 Failure-Mode** (adversarial domain, cap 2).
Returned **P0=0** for all three. Their substantive findings are the six P1s and the nits already listed
in §11.1b — i.e. the cycle-2 and plan-review passes **converged on the same defect set from different
angles** (the Failure-Mode reviewer independently produced B12/B13), which is the signal that the set
is closed rather than reviewer-specific. Remaining items are the recorded residuals (§3.7) and the
**two owner gates** (§4 surface approval; §5 pair order), not defects.

**Bounded exit (adversarial domain, cap 2 reached):**
`[ADVERSARIAL-BOUND] cycles=2 threat-rows=14 (B1–B13 + B8b) refusal-classes=13 covered=13
accepted-classes=1 (B8b) residuals=9 (RES-1…RES-7 + the two owner gates)`. Cap reached with the in-scope bypass set **empty**: every declared threat class has a named
test that REDs without its guard (§6.2 mutation proof), and the residuals are the deliberately-accepted
fail-safe-by-design or fail-closed-by-design cases above. The remaining §3.7 rows are *accepted
residuals*, not unresolved findings — but per the clean-completion rule this is an **escalation exit
that documents them**, not a "clean" verdict, and it is reported as such.

---

## 12. Rejected alternatives (with reasons)

| # | Alternative | Why rejected |
|---|---|---|
| **R1** | **Relax #4021's refusal in `supersede_point`** (clamp/collapse whenever `vC < vP`), or accept it via a `retroactive=True` mode | Contradicts the recorded owner ruling + `OVERRIDES:` marker on #4021 (the refusal is deliberate and per-verb). The mode form is presented in §4.3 as the alternative *shape* for a separate verb, but the **forward verb's contract stays unconditional** |
| **R2** | **Representation change** — half-open `_covers` `[start, end)` so a zero-length window is genuinely empty (the SQL:2011 answer), plus a well-formed-empty marker | The canonical **destination**, but a **different root** and a global boundary-semantics flip: under `[start, end)` the answer changes at `t == validTo` for **every** window in **every** chain, so `restore_point_at`, `retrieval._validity_marker` (`retrieval.py:865`), `assembly` (`:1014`), `time_aware` (`:277`/`:316`) and `audit` (`:665`) must all be re-verified against the new boundary, and `validate_validity_window`, `_refuse_inverted_point_window`, `_assert_window_start_not_inverted`, `_supersede_window_end`'s refusal and #5361's `malformed` flag must be re-scoped. It would also subsume #5360/#5361/#5374. **Must be its own issue**; adopting it here would breach the #4021 plan's recorded scope boundary without an owner decision. ⚠️ Note what is **not** an objection: a strictly inverted window (`vt < vf`) stays inverted and `_inverted` (`sdk.py:19221`, `kt < kf`) still detects it — only the **zero-length** interval changes meaning. (An earlier draft claimed #5361's detection would be defeated; that was wrong, and the rejection rests on the boundary flip alone.) **#7818** is where this decision belongs |
| **R3** | **New node flag** (`displacedAt`) or **edge-carried correction** (`CORRECTS{retroactive:true, effectiveAt}`), predecessor window untouched | Cleanest story, worst blast radius: it moves the authority for "what is true at `t`" **off** the canonical window and onto a slot every `_covers` consumer must consult (`restore_point_at`, `search_engine:2944`, `assembly:1014`, `retrieval:865`, `time_aware`, `audit`, `consistency`) — trading a one-instant ambiguity for a reader-side change on every surface, and contradicting ONTOLOGY §4.7's declaration that the window is canonical |
| **R4** | **Branch-point insert / re-parent** (Fowler's phrase, read literally) | **Impossible for a strict subsumption**: `P[vP,∞)` and `C[vC,∞)` genuinely overlap, so no non-inverted pair of windows leaves `C` authoritative from `vC` while `P` is non-overlapping. A branch point exists only for a **finite**-period correction (where SQL:2011 splits) — a different feature, and one `CORRECTS` 1→1 cannot express |
| **R5** | **Compose existing verbs**: `invalidate_point(old,new)` + `update_point(old, validTo=old.validFrom)` | Not one operation (a recipe with a mandatory order, unenforced); **non-convergent crash state** (a crash between the calls leaves `P` outdated with an open window ⇒ `ambiguous` over all of `[vP,∞)`, and re-running raises on the terminal guard, so recovery is manual); **wrong terminal status** (`status` stays `live`, the very `outdated`+CORRECTS conflation ONTOLOGY §4.7 warns about); **no edge transfer**; **order-sensitive rebuild parity** (two journal records whose replay order is load-bearing); **depends on the `update_point` no-lifecycle-guard hole** (§9) |
| **R6** | **Mint a new event type** (`PointRetroCorrected`) | ~8 registration edits (`_GRAPH_EVENT_TYPES` `sdk.py:2503`, `CLAIM_EVENT_TYPES` `shared_state/events.py:162`, `_POINT_RESTAMP_EVENT_TYPES` `projection/__init__.py:3554`, a `_fold_point_restamp` arm, `plan_point_restamp_folds` canonicalization, `consistency.py:950/1045`, `TORN_TAIL_HARMLESS_EVENT_TYPES` `log.py:138`, `nonfolded.py:182`, the `events_poll` catalogue `mcp_server.py:2275`), each a silent-fold-miss or fail-closed-refusal site, and it buys nothing the `retroactive: true` payload key does not. **Available as an owner choice** (§4.1 row 3) — the semantics are unchanged either way |
| **R7** | **Status-only route** (`retract_point` + CORRECTS, no window write) | `restore_point_at` is **status-blind** (`_covers` reads only the window), so leaving `P` open-ended makes every `t ≥ vP` return `ambiguous` — strictly worse than the collapse |
| **R8** | **Move `P.validFrom` forward/downward** to hide it from the interval | `validFrom` is a **world fact**, not a mutable boundary — the invariant `_fold_point_invalidated` documents ("neither status nor validFrom is written"). Moving it down fabricates coverage before `vC`; moving it up makes `P` cover `C`'s whole window |
| **R9** | **Leave `P` untouched and let `C`'s open window win by some precedence rule** | No such precedence exists in `_covers`; adding one is an R3-class reader change and makes the answer depend on which id you enter the chain from (the #4021 defect class) |
| **R10** | **A chain-aware boundary tie-break in `restore_point_at`** (prefer the successor at `t == validTo == successor.validFrom`) | It would remove the manufactured `t = vP` instant (§3.3) without R2's representation flip, and would also close #7818. Rejected: `restore_point_at` is **one** of several window consumers and the only one that walks the chain with a successor relationship in hand; `retrieval._validity_marker`, `assembly`, `search_engine.fetch_point_epistemic_state`, `time_aware` and `audit` render the raw window and have no such tie-break available, so the SDK reader would silently disagree with them at every boundary — trading one pinned ambiguity for an untracked reader inconsistency. Fixing that means giving every consumer the tie-break, i.e. R2's blast radius. #7818 is where it belongs |

---

## 13. Open questions for the owner (surface request is §4)

1. **Approve the surface request (§4)?** Primary: `supersede_retroactively` (SDK) +
   `tortoise_supersede_retroactively` (MCP). Alternative shape: a `retroactive=True` mode on the
   existing verbs (§4.3). Naming (§4.2).
2. **Journal event: reuse `PointSuperseded` + `retroactive: true` (recommended, zero fold work) or mint
   a distinct type (§4.1 row 3, R6)?**
3. **Accept the manufactured `t = vP` ambiguity (§3.3), or should the half-open representation (R2) be
   scheduled now as its own issue that supersedes this design?**
4. **Should the retroactive capability also be exposed on the read side** (`restore_point_at` is
   SDK-only — §4.4)?

## 14. What could not be determined

- **The tier was inferred**, not read — #5362 has no Complexity Rating block.
- **Rung-1 research tooling is unavailable on this machine** (`seo-intelligence` MCP absent); the
  external evidence is rung 2, and one XTDB query was rate-limited and taken from the brief rather
  than independently re-verified. The Graphiti/Zep finding is a secondary source, not source code.
- **Nothing was executed**: no test run, no live graph measurement. Every semantic claim is derived by
  reading `7360bab7d`; the one existing zero-length pin was read, not run.
- **The hosted `_prevalidate_supersede_window` coupling** (`hosted_api.py:13880`) is identified but not
  resolved — whether a legal retroactive commit would currently 422 is a Task-5 question (§8).
- **`commit_ops.apply_supersessions` / hosted routing** of a retroactive record is not decided here.
  An earlier draft said it was #5365's root; **#5365 is closed**, so the routing decision (hosted
  commit, ingest record, `promote_point`) is now filed as **#7821** with the coupling stated, not
  resolved.
- **`_assert_lifecycle_guard`'s row-order dependence** on a duplicate id is **not** fixed here
  (filed as **#7822**); this plan's B7/B11 controls are therefore claimed for single-node ids only.
- **The audit blind spot** (a zero-length window is not flagged by `audit.py:665`) is recorded as a
  decision for Task 5, not resolved.
- **The bitemporal-database canon (Snodgrass/Jensen/TSQL2) is cited from the standard literature, not
  fetched** — the rung-2 budget was spent on vendor/competitor sources. The row in §10 was added after
  the solution-verify review flagged the omission, and it **weakens** the collapse's pedigree (the
  literature's default is to leave the predecessor's valid-time window alone, which Tortoise's
  window-driven, status-blind read path cannot tolerate) — that tension is recorded, not smoothed.
- **#5375's citation** ("`_now_iso` defined twice at 2041 and 21906") does **not** hold at `7360bab7d`:
  `grep -n "def _now_iso" tortoise/sdk.py` returns exactly one, `sdk.py:26676`. Either #5375 landed or
  the citation predates a refactor — not verified here.
