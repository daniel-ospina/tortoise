# #14198 — a contradiction declared across two captures CLOSES the older claim's interval

**Scope:** the *representation* half of #14198. The ranking half (a query
answering with the superseded side) is #14721/#14737 and is not touched here.
**Measured on:** the committed corpus and receipt of #14592
(`docs/evidence/14592-operating-model-validation/`), replayed offline — no
model call, no graph — because S5 (`extractor_v2.execute_embed`) is a pure
function. `replay_14198.py` (this directory) is the harness: it reads the two
ids and both refs **out of the receipt by id** and refuses rather than
substituting a value it cannot find.

---

## 1. Where the contradiction is lost — named

The extractor **did** declare it. `doc-05`'s capture emitted a `NAND` whose
`stated_predicate` is `refutes` (#2552's declaration channel), between
doc-05's own audit claim and doc-01's certificate claim. The receipt records
both loss steps, verbatim:

```text
operator endpoint HELD not minted (#10997 S3 HOLD): NAND dst named
  'ColdPath Logistics holds a validated cold-chain certificate' but the identity
  lookup was partial, so whether this endpoint already exists was never
  established — held with its edge, NOT persisted (no hold store was supplied)
operator dropped: src/dst did not resolve to an emitted point/event
  ('The cold-chain audit failed ColdPath Logistics last week' →
   'ColdPath Logistics holds a validated cold-chain certificate')
```

Three facts, in the order they bite:

| # | where | what happens |
|---|---|---|
| 1 | `tortoise/extractor_v2.py:12360` (pre-fix) | the operator pass resolves both endpoints through `_resolve`, which reads **only this capture's** `point_ids`/`event_ids`. An older side that lives in a **prior** capture is unresolvable there *by construction* — so a cross-document contradiction is dropped by the resolver's scope, not by any guard rejecting it. |
| 2 | `tortoise/extractor_v2.py:3614` | the `#10997` hold that step 1's failure creates is not persisted — `hold_store` is `None` on the path that ships (no caller in `sdk.py`/`mcp_server.py`/`tool_registry.py` passes one), so a later run cannot drain it. `stats.held: 3`, `supersessions: 0`. |
| 3 | `tortoise/extractor_v2.py:11700` (collect) + `:11763` (resolve) + `:9854` | the channel that **does** cross captures — a point-level `supersedes` ref, resolved against the S3 prior set by `_resolve_point_supersede` — was never used, because the model declared a `NAND` between two *points* instead. |

So the answer to "which of the three" is: **the write path dropped a declared
relation**, and **nothing closed an interval** because the one channel that
would have was never reached. The extractor is not the culprit; the resolver's
scope is.

## 2. The representation chosen, and why it is the ontology's own

A declared `NAND` whose **new** side resolved in this capture and whose
**older** side resolves in the S3 prior set now emits the *same* record a
declared `supersedes` ref emits — a `supersessions` record, whose consumer
(`tortoise/commit_ops.py::apply_supersessions` → `sdk._supersede_point`) writes:

* the **`CORRECTS`** edge (what superseded it),
* the older claim's **`validTo`** = the successor's window start — bi-temporal
  world time, contiguous windows (E6 / #1538),
* the older claim's **`expiredAt`** = ingestion time (when we learned it),

and **retains** the older claim as terminal. Closed, never deleted (§2.0: the
record is truth), never folded (⛔ D12/O4's loss asymmetry is untouched — this
is not a merge). The *source* is carried by the declaration itself: the
successor point is the document that asserted it, and its session/provenance
ride the point. ⚠️ `_supersede_point` also TRANSFERS the predecessor's
structural edges (`extractedFrom` among them) to the successor, so the still-live
claim inherits the closed claim's provenance — correct in the one direction this
arm emits (the newer document supersedes the older one) and the reason the
backfill direction is DECLINED rather than folded backwards (§6.5).

**One closure per claim, across PRODUCERS, not just within this arm.** The
payload's `supersessions` may already name this prior when the operator pass
runs — a point carrying a `supersedes` ref, or the UPDATE fold, both append
earlier — and `apply_supersessions` skips a terminal target with a bare
`continue` (no warning, not even a `skipped` entry). So the arm checks the
records already present and, if one of them closed this prior, DECLINES: the
declared `NAND` stays as the evidence of this refutation and the receipt names
the successor that closed it. Appending a second record there would have looked
like a closure in the payload and been a silent no-op in the graph.

**Both lookup arms, one representation.** The #14592 receipt was recorded on a
`partial` identity lookup, and the two arms take different internal routes to
the same declaration — measured on the receipt's own shape:

| lookup arm | pre-fix payload |
|---|---|
| `partial` (the recorded state) | `operators: []` — the endpoint is HELD, then the edge is dropped |
| `resolved` | `operators: [NAND new→prior]` — `#4716`'s mint-vs-E7 fold puts the ref on its graph id, so the edge *wires* |

Both now close the interval **and produce the same payload** — one
`supersessions` record, no NAND — so the representation is not a function of
lookup quality. The NAND is not also emitted because `_supersede_point`
re-points (and, for a self-edge, deletes) the incident `IMPL|NAND` rows when it
terminalises the predecessor: emitting it would declare an edge the commit
immediately removes, and would hand that door a repoint it can refuse. The
`CORRECTS` edge is the representation. That invariant is graded at the
representation, not at the payload: `test_the_closure_does_not_depend_on_which_
lookup_arm_ran` asserts **no** `IMPL|NAND` survives between the two rivals on
the resolved route — which is the shape a reader can observe, and the one a
future edit that stopped the consumer from removing the edge would break.

**Three gates on the closure, each pinned by a test that reds when removed.**
The shapes below are not hypothetical — each one was measured against a corpus
or a contract this PR already names. ⛔ ONE guard is the authority for "the
superseder must be a writable claim of this capture" (`_written_point_ids`); the
earlier cut also carried `new_id not in _capture_ids` and a `startswith("pt_")`
test, and both were INERT — every payload point id satisfies them by
construction, so neither could ever be the operative refusal (measured: deleting
either left every case green). They are gone rather than left reading as
protection.

1. **SYMMETRIC on emission order.** Either side may be the one this capture
   emitted. The drop route keyed on `src` alone for its first cut, so the
   model's other emission order (`src = older, dst = new`) left `src` empty and
   the operator fell through to `operator dropped: src/dst did not resolve`.
   `_canonicalize_nand_direction` cannot repair that: it returns `None` for an
   existing-graph endpoint, which a cross-capture NAND always has — and the
   receipt this PR replays carries two `NAND direction canonicalized` lines
   from the same corpus.
2. **A WRITABLE POINT, not just a mentioned id.** An Event is a legal operator
   endpoint but has no point lifecycle, and a `#2552` MINTED endpoint can be
   pruned once the operator is suppressed — either one named as the superseder
   yields a record the consumer skips fail-open, while the extractor's warning
   claims the interval was closed. The single `_written_point_ids` guard refuses
   both; an event side falls through to the drop warning, which is the truth.
3. **WORLD TIME, not arrival order — on the CONSUMER'S OWN MEASURE.** #14198's
   recorded decision is *"Facts are compared on world-time dates, not arrival
   order"*, and it is what Zep/Graphiti do (the edge with the earlier `valid_at`
   is the one retired). Capture membership alone emitted an **inverted window**
   for a BACKFILL — a later capture carrying an earlier world date — which
   `_supersede_point` refuses (#4021), so the fold failed and both claims
   stayed live: the #14198 defect arriving through the backfill door.
   ⛔ And a DATE-PREFIX order is not enough: the write path refuses on INSTANTS
   (`sdk._supersede_window_end` → `search_engine._created_sort_key`), so a
   same-day pair (`18:00Z` vs `09:00Z`) TIES on the date and a cross-offset pair
   (`2026-03-05T23:00:00-05:00` = 04:00Z on 03-06 vs `2026-03-06T01:00:00Z`)
   compares BACKWARDS — either way the producer emitted the window the consumer
   then refused, leaving both claims live. The arm now uses the same
   `_created_sort_key` instants, falling back to arrival order only when either
   start names no instant (the prior set IS what existed before this capture, so
   that is the honest fallback; equality keeps it too, since a zero-length
   predecessor window is legal).

No new node kind, no new relation vocabulary, no MCP/SDK surface change.

### What was deliberately NOT built

⛔ **No lexical contradiction detector.** #14198's own ruling is that the
**model** labels "duplicate vs contradiction" and **code** does the interval
arithmetic. `_boundary` / `classify_consolidation` are unchanged: a pair they
call rivals still produces `ADD`, both claims survive. The measurements that
settle this are in §5 — a pure-lexical rule cannot separate
`staging → production` (one attribute, new value) from `gym → yoga` (different
attributes). Guessing there is how a false `CORRECTS` terminalises a live
belief, and there is no un-supersede API to repair it.

## 3. Before / after — the artifact, not a claim

```text
$ git worktree add /tmp/before-14198 3f134560b6c --detach
$ python docs/evidence/14592-operating-model-validation/replay_14198.py \
      --arm before --tree /tmp/before-14198
# replay arm: before
# tree: /tmp/before-14198
# lookup state: partial
# extractor_v2 from: /tmp/before-14198/tortoise/extractor_v2.py
# new claim (this capture) : pt_9af28546e85d11f414e05b658580e4afe46f2c21ec9a9e58dce89b8a288aa5
# older claim (prior)      : pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078

```text
$ PYTHONPATH=<worktree at 875abfd> python docs/evidence/14592-operating-model-validation/replay_14198.py
supersessions recorded      : 0
supersessions naming the pair: 0

warnings naming the pair:
  - operator dropped: src/dst did not resolve to an emitted point/event ('The cold-chain audit failed ColdPath Logistics last week' → 'ColdPath Logistics holds a validated cold-chain certificate')

VERDICT: the cross-document contradiction is NOT RECORDED
```

On the fixed tree, labelled `before`, the same command refuses rather than
mislabelling an `after` run (measured, verbatim):

```text
$ python docs/evidence/14592-operating-model-validation/replay_14198.py --arm before
replay REFUSED: --arm before names an arm the imported module is not (the cross-capture arm is present in …/tortoise/extractor_v2.py) — point PYTHONPATH at the tree you mean instead of relabelling it
```

The AFTER side, verbatim:

```text
$ python docs/evidence/14592-operating-model-validation/replay_14198.py --arm after
# replay arm: after
# tree: /home/cmux/tortoise/.worktrees/14198-contra
# lookup state: partial
# extractor_v2 from: .../14198-contra/tortoise/extractor_v2.py
# new claim (this capture) : pt_9af28546e85d11f414e05b658580e4afe46f2c21ec9a9e58dce89b8a288aa5
# older claim (prior)      : pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078

supersessions recorded      : 1
supersessions naming the pair: 1
  superseded=pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078  supersedes_by=pt_9af28546e85d11f414e05b658580e4afe46f2c21ec9a9e58dce89b8a288aa5
  evidence="declared NAND contradiction across captures — the older claim's interval is closed (retained, never deleted)"

warnings naming the pair:
  - operator endpoint is an EARLIER-CAPTURE claim (#14198): NAND dst 'ColdPath Logistics holds a validated cold-chain certificate' resolved to prior point 'pt_fc8b3e47bb7e05993' outside this capture — the contradiction is recorded as a supersession (closed interval 'pt_fc8b3e47bb7e05993' ← 'pt_9af28546e85d11f41'), so the older claim is retained and closed rather than left as a second live truth

operators naming the pair : 0

VERDICT: the cross-document contradiction is RECORDED
```

The `before` warning is **byte-identical** to the one the recorded run wrote —
that is the check that the replay is a replay, not a different run. ⛔ `--arm`
is CHECKED against the imported module and refuses when the label and the tree
disagree, and `--tree` selects the checkout that is imported (never the ambient
import path — this box's venv has the MAIN checkout installed, so a bare run
measured `/home/cmux/tortoise` and printed a verdict for code it never loaded).

⚠️ **What this artifact proves, exactly.** The prior set here is a
**RECONSTRUCTION**: the receipt does not record doc-05's S3 prior set (its
`result` carries no search/prior dump), and doc-05's own warning says the
identity lookup was PARTIAL (`25 candidates, 11 did not run`). So the run
demonstrates *"IF the prior set contains the certificate claim, the closure now
happens"* — not *"the shipped pipeline closes this corpus's contradiction"*. A
hold is not a closure. The shipped number still requires a fresh-graph
re-ingest (the 5-document run in #14198's owner comment, reading doc-05's
`supersessions`), and this file is not a substitute for it. It also cannot
exercise gate 3: the receipt's point rows carry no `when`, so the replay takes
the arrival-order fallback: the backfill branch DECLINES (§6.5), and the two
instant/precision shapes are covered by
`test_a_backfill_DECLINES_and_says_so` and
`test_a_world_time_ordering_is_decided_on_instants_not_date_prefixes`.

## 4. Compose with retrieval — what a reader sees now

The report's failing probe (its `contradiction` question) ranked the claim
doc-05 refutes **first**, `status: live`. After the closure the two questions a
reader can ask finally differ:

| question | before | after |
|---|---|---|
| "what is true now" (default scope) | both claims, both `live` | only *"The cold-chain audit failed ColdPath Logistics last week"*; the certificate claim is terminal and **excluded** |
| "what did we believe on 2026-02-20" (`get_historical_knowledge`) | **`found: false`** — the instant precedes the audit claim's own `valid_from` (2026-03-05) and no `CORRECTS` edge exists to reach back, so the chain is `[AUDIT]` only | the certificate claim **via the `CORRECTS` chain**, `valid_to=2026-03-05`, `valid_from=2026-02-03` |
| "has anything we relied on ended?" (`ended` / `include_terminal`) | — | the closed claim, with its ending time |

The `#14721`/`#14737` boundary, stated exactly: **the closed interval is now
visible to ranking** — the claim is terminal (`outdated`) and carries `validTo`,
which every current-scope read already filters on — and their work (which of
the *surviving* claims to rank first) is neither implemented nor needed here.

## 5. Both directions

| | assertion | pre-fix | post-fix |
|---|---|---|---|
| **positive** | the declared cross-capture `NAND` writes 1 supersession; older claim retained, `valid_to=2026-03-05`, `expired_at` stamped, `valid_from` kept; newer interval open; `get_historical_knowledge` returns the old claim through `CORRECTS` | ❌ `assert 0 == 1` | ✅ |
| **positive (other lookup arm)** | the same declaration on a `resolved` lookup closes the same interval (pre-fix this arm wrote the NAND and closed nothing) | ❌ `assert 0 == 1` | ✅ |
| **positive (other emission order)** | the SAME declaration with the older claim emitted first closes the same claim (`src`-keyed arm: `assert 0 == 1`) | ❌ | ✅ |
| **positive (instant precision)** | the DIRECTION is decided on `_created_sort_key` instants, not on the date prefix: a cross-offset pair whose date prefixes invert (`2026-03-06T01:00:00Z` vs `2026-03-05T23:00:00-05:00` = 04:00Z) closes (it is an ordinary arrival order), and a same-day pair whose instants make the arriving claim the OLDER fact is DECLINED with a named warning instead of emitting a refused window while claiming a closure | ❌ | ✅ |
| **negative (order-independence)** | two refuters of one claim — one DECLINED, one carryable — close exactly ONE record, naming the carryable refuter, in BOTH list orders. RED on base (it asserts a closure, and base closes nothing) and additionally RED under the mutation that takes the closure slot ABOVE the world-time gate | ❌ | ✅ |
| **negative (backfill, record)** | a later capture carrying an EARLIER world date records **nothing** — world time says the prior is the newer fact, and this channel cannot express that direction: the record's `superseded` side must already be a graph id (`commit_ops.remap_supersession_point_refs` remaps only `supersedes_by`, deliberately — the `pt_` prefix is the lane discriminator), so a payload id there is skipped FAIL-OPEN. ⚠️ The GRAPH-level half of this row passes on the base commit (on base nothing is recorded either, for a different reason) — it is a pin against the mutation that puts the inverted window back | ✅ (pin) | ✅ |
| **negative (backfill, says so)** | …and the payload NAMES the decline, with both world instants, instead of emitting a record the consumer will skip while the warning claims a closure. ⚠️ On base this reds on the WARNING assertion (`assert said and "cannot express that direction" in said[0]` → `assert ([])`), not on the record count — base records nothing at all, for its own reason. The `assert [{'superseded': …}] == []` form belongs to the mutation that re-adds the emitting branch | ❌ | ✅ |
| **negative (backfill does not block a refuter)** | a DECLINED pair must not spend the one-closure-per-prior budget: a later refuter of the same prior with a forward world date still closes it, and the declined pair keeps its edge — pre-fix `_closed_prior_ids.add` ran before the decline, so the forward refuter returned with no record AND no warning while the only line about that prior asserted it was the newer fact | ❌ | ✅ |
| **negative (decline claims no retention)** | the decline's warning does not promise an edge it cannot keep: on the `partial` route the payload drops the operand and says so, on the `resolved` route it wires it — asserted on both routes, because the first wording ("the declared edge is kept as it was") was false on the route the corpus itself ran | ❌ | ✅ |
| **negative (another producer already closed it)** | a prior closed in the SAME payload by a different producer (a point carrying a `supersedes` ref) must not swallow this declaration: `_closed_prior_ids` is this arm's own budget, so appending a second record and `continue`ing deleted the refutation from BOTH representations — the consumer skips a terminal target with a bare `continue` (no warning, not even a `skipped` entry) — while the warning attributed the closure to this pair. Now the NAND is kept and the receipt names the other successor. ⚠️ Reds on base only on the warning half; the structural half is a pin | ⚠️ half | ✅ |
| **positive (fuzzy closure discloses its band)** | a closure decided on an edge-trimmed ("recall band", #8503) match still emits its disclosure — the local warning sink that stops the resolver minting replace-channel refusals must not swallow the resolver's SUCCESS line, which is the only audit trail for terminalising a live belief on a non-exact match | ❌ | ✅ |
| **negative (id-less prior row)** | the `superseded` side must name a row the PRIOR SET names, not merely "an id this capture did not emit": consolidation falls back to a content-addressed payload id when a prior row carries no `id`, and that id would be skipped FAIL-OPEN on the graph while the warning claimed a closure | ✅ (pin) | ✅ |
| **positive (read)** | a current-scope query for the old claim's own wording no longer answers with it; the terminal scope still does | ❌ | ✅ |
| **negative (support)** | the *same* two documents with the model declaring `IMPL` (support) close **nothing**; the older claim keeps its open interval and stays current | ✅ | ✅ |
| **negative (event side)** | a NAND whose emitted side is an Event closes nothing AND does not claim it did (no `recorded as a supersession` warning). Passes on the base commit too, and is REAL coverage for the surviving guard: removing `_written_point_ids` reds this case and the minted-successor one (measured) | ✅ | ✅ |
| **negative (minted successor)** | a `NAND` whose newer side is named only INSIDE the edge closes nothing and claims nothing — the `#2552` mint would be pruned once the operator is suppressed, so the closure would name a point that was never written and the document's own claim would vanish (measured in review as the arm's worst shape: `supersessions_written: 0`, "the contradiction is recorded …" still warned) | ✅ | ✅ |
| **negative (second refuter)** | 2 refuters of one claim → 1 closure, and the SECOND refuter KEEPS its `NAND` (the consumer would skip it silently as an idempotent no-op, so suppressing both would erase that evidence) — pre-fix: `2 operators, 0 closures` | ❌ | ✅ |
| **negative (undeclared)** | a corpus that reverses itself in **wording** but declares no edge gains no contradiction. ⛔ A PIN, not coverage: no mutation of the arm can red it (the `none` shape emits no operator at all) | ✅ | ✅ |

Suite: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_14198_contradiction_closure.py -q`
→ **19 passed**, and **13 RED on the base commit** (`3f134560b6c`): every
positive — order-independence included, since it asserts one closure and the base
closes nothing — plus four negatives whose *new* half is an assertion about what
the run SAYS or about the budget it spends (the backfill-decline's warning, the
declined-pair budget, the decline's retention claim, instant precision), plus the
cross-producer case, which reds there on its warning half only.
The counts are MEASURED, not counted by hand: the base run of this file is
`13 failed, 6 passed`. ⚠️ Two earlier revisions of this paragraph were wrong and
are corrected here rather than re-worded: one said `10 failed, 6 passed` and
called order-independence a pin that "passes on base" (10 + 6 = 16 of 17, and the
case asserts a closure); the next had the right arithmetic for 17 cases and went
stale when two more were added. The cross-producer case's structural half IS a
mutation pin (it reds when the budget is private again); its warning half is new
coverage.

The emission-order, backfill and order-independence cases were each measured RED
under the mutation that reverts their gate (the `src`-only guard; the
arrival-order direction; taking the closure slot above the world-time gate).
⚠️ The event-side case is NOT one of them: `new_id not in _capture_ids` and the
`pt_` test were removed as INERT (cycle 3 — `_written_point_ids` is
`payload_points` minus the mints, so it implies both), and an Event side is
refused by that surviving guard, so this case pins `_written_point_ids` and reds
when IT is removed. It is a pin naming the mechanism that actually refuses, and
the row above says so.
against the arm, and pinned as pure-function tests (`execute_embed` needs no
graph). The instant-precision case was measured RED under the mutation that puts
the date-prefix comparison back — in BOTH directions, which is why it asserts
one closure and one decline.

Regression surface run alongside, all green: `test_extractor_v2`,
`test_consolidation_4way`, `test_never_across_fold_5080`, `test_10997_i3_partial_run`,
`test_10997_identity_hold`, `test_11653_s3_hold_not_add`, `test_supersede_edges`,
`test_ingest_v2_consolidation` → 1186 passed, 2 skipped.

## 6. Residuals — stated, not hidden

1. **The decision pair is still two rivals.** doc-01's *"We choose ColdPath
   Logistics as our delivery partner"* vs doc-05's *"We revert to
   NordicFreight …"* shares **0.538** tokens and carries **no declared edge**
   (measured). It is not closed by this change and must not be: nothing declared
   it. Closing it needs a model-side declaration (the `supersedes` ref, or a
   `NAND`), and the honest next step is on the emitter/prompt side, not here.
2. **Two of doc-05's three drops remain drops, and they must.** The receipt's
   own `HELD` warnings name a `MITIGATES` destination and an `IMPL` destination
   among doc-05's unresolved endpoints; neither is a reversal. `MITIGATES` is
   edge-relevance and `IMPL` is *support* — a support edge naming a prior
   capture's claim could reasonably be wired to that point rather than dropped,
   but that is a different question (and a different failure) from a
   contradiction, so this change leaves both dropping on purpose: the arm is
   `NAND`-only.
3. **`hold_store=None` on the shipping path (#10997) is unchanged.** This
   change does not need it — the closure is decided from the S3 prior set /
   the fold's own graph id, both of which the run already has — but a
   *different* endpoint whose identity lookup is partial is still held with no
   store. That is a separate repair. ⚠️ One measured consequence for the
   CENSUS: when the arm fires on a held endpoint the run still reports the
   `#10997` HELD line *and* the closure line, so `stats["held"]` counts an
   endpoint whose edge was consumed as a closure — the held count can
   overcount by one per closed contradiction. Inert while the store is `None`
   (nothing is persisted to drain); it becomes a double-record only if a store
   is supplied.
4. **The `partial` lookup still costs recall.** In the corpus run 11 of 25
   doc-05 candidates went unchecked. When the older endpoint is *not* in the S3
   prior set, this change correctly fails open (a warning, no closure) — a
   contradiction that was never retrieved cannot be closed.
5. **A BACKFILL is declined, not closed.** When world time says the PRIOR is the
   newer fact, the closure would have to read `superseded = <this capture's
   claim>` — and the record's `superseded` side must already be a GRAPH id,
   because `commit_ops.remap_supersession_point_refs` remaps only
   `supersedes_by` (deliberately: the `pt_` prefix is the record's lane
   discriminator). A payload id there is skipped fail-open, so emitting it would
   produce exactly the false success this change exists to remove. The arm
   declines with a warning that names the two world instants and says no
   interval was closed. ⚠️ It does NOT claim the edge was kept: the decline
   returns to the ordinary handling, which WIRES the NAND when the older side
   resolved and DROPS it when the identity lookup was partial (the #14592
   corpus's own recorded state) — so the warning says exactly that, and the
   adjacent `operator dropped` line carries the rest. The decline also spends no
   budget: `_closed_prior_ids` is filled only by a record actually written, so a
   later refuter of the same prior can still close it. Closing a backfill needs
   a producer of the other shape (a graph-id `superseded`), which is a
   different change.
6. **An unknown world-time start falls back to arrival order** — and the ordering
   is decided on the consumer's OWN instants (`_created_sort_key`), never on a
   date prefix. The prior's
   `when` reaches the arm through S3's `_enrich_point_priors` (best-effort), and
   the new claim's is its own `when` on the payload point. When either is
   missing or unparseable there is no instant to compare, so the arm orders by
   capture membership — the pre-fix order. For a backfill whose dates are
   unknown this means it does NOT decline (the dates are absent) and the
   consumer refuses the inverted window with its own warning and skip. Stated
   rather than hidden: the decline is only as good as the dates. (A TIE is NOT
   this case: two parseable instants that compare equal are a zero-length
   predecessor window, which `_supersede_point` accepts, so a same-day pair is
   ordered by its instants and closes — the date-prefix implementation treated
   it as unknown instead.)
7. **Provenance in the declined direction is not touched** — because nothing is
   folded there. `_supersede_point`'s edge transfer (which moves
   `extractedFrom` and the other structural edges to the successor) therefore
   only ever runs on the arrival-order shape, where the successor is the
   document that asserted the newer claim.
8. **The replay's prior set is a reconstruction, not a recording** (§3). It
   proves the arm fires on the receipt's own declaration; it does not prove the
   shipped pipeline closes that corpus, which needs a fresh-graph re-ingest.
9. **The minted-endpoint exclusion is conservatively WIDE.** A minted endpoint
   that would SURVIVE the prune (some other surviving operator references it) is
   excluded from the successor set too, so a closable closure is declined. Safe
   direction — a lost closure shows up as two live claims, a lost claim does not
   — and narrowing it needs the prune's own reachability computation, which runs
   after this pass. Stated here because the code comment otherwise reads as if a
   minted endpoint is always pruned.
10. **The supersede-channel LOSS metric is untouched by this PR, deliberately.**
   The NAND route calls `_resolve_point_supersede` with a LOCAL warning sink, so
   an unresolvable older ref does not emit `matches no S3 prior` — that marker is
   a REPLACE-channel refusal, and the model declared a `NAND`, not a
   `supersedes` ref. Without the local sink this PR would have re-attributed 2 of
   doc-05's 3 drops on its own corpus to a channel they never touched.
