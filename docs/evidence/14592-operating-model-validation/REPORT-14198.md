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
| 3 | `tortoise/extractor_v2.py:11687` + `:9854` | the channel that **does** cross captures — a point-level `supersedes` ref, resolved against the S3 prior set by `_resolve_point_supersede` — was never used, because the model declared a `NAND` between two *points* instead. |

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
ride the point.

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
`CORRECTS` edge is the representation.

**Three gates on the closure, each pinned by a test that reds when removed.**
The shapes below are not hypothetical — each one was measured against a corpus
or a contract this PR already names:

1. **SYMMETRIC on emission order.** Either side may be the one this capture
   emitted. The drop route keyed on `src` alone for its first cut, so the
   model's other emission order (`src = older, dst = new`) left `src` empty and
   the operator fell through to `operator dropped: src/dst did not resolve`.
   `_canonicalize_nand_direction` cannot repair that: it returns `None` for an
   existing-graph endpoint, which a cross-capture NAND always has — and the
   receipt this PR replays carries two `NAND direction canonicalized` lines
   from the same corpus.
2. **POINT-ONLY on the emitted side.** An Event is a legal operator endpoint,
   but there is no point-supersede channel to an Event successor: the consumer
   dispatches on the `pt_` prefix of the old id and then refuses the successor
   (`_assert_lifecycle_guard`). Emitting the record would lose the closure
   *and* claim it was recorded; an event side now falls through to the drop
   warning, which is the truth.
3. **WORLD TIME, not arrival order.** #14198's recorded decision is *"Facts
   are compared on world-time dates, not arrival order"*, and it is what
   Zep/Graphiti do (the edge with the earlier `valid_at` is the one retired).
   Capture membership alone emitted an **inverted window** for a BACKFILL — a
   later capture carrying an earlier world date — which `_supersede_point`
   refuses (#4021), so the fold failed and both claims stayed live: the #14198
   defect arriving through the backfill door. The pair is now ordered by the
   claims' own window starts, falling back to arrival order only when either
   start is unknown or unparseable (the prior set IS what existed before this
   capture, so that is the honest fallback; equality keeps it too, since a
   zero-length predecessor window is legal).

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
$ python docs/evidence/14592-operating-model-validation/replay_14198.py --arm before
# extractor_v2 from: <tree at HEAD~1>
# new claim (this capture) : pt_9af28546e85d11f414e05b658580e4afe46f2c21ec9a9e58dce89b8a288aa5
# older claim (prior)      : pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078

supersessions recorded      : 0
supersessions naming the pair: 0

warnings naming the pair:
  - operator dropped: src/dst did not resolve to an emitted point/event ('The cold-chain audit failed ColdPath Logistics last week' → 'ColdPath Logistics holds a validated cold-chain certificate')

VERDICT: the cross-document contradiction is NOT RECORDED
```

```text
$ python docs/evidence/14592-operating-model-validation/replay_14198.py --arm after
# extractor_v2 from: <this change>
# new claim (this capture) : pt_9af28546e85d11f414e05b658580e4afe46f2c21ec9a9e58dce89b8a288aa5
# older claim (prior)      : pt_fc8b3e47bb7e0599328d968073671eef8c9cecbc376ca2bbd6e05476f2c078

supersessions recorded      : 1
supersessions naming the pair: 1
  superseded=pt_fc8b3e47…  supersedes_by=pt_9af28546…
  evidence="declared NAND contradiction across captures — the older claim's interval is closed (retained, never deleted)"

warnings naming the pair:
  - operator endpoint is an EARLIER-CAPTURE claim (#14198): NAND dst 'ColdPath Logistics holds a validated cold-chain certificate' resolved to prior point 'pt_fc8b3e47bb7e05993' outside this capture — the contradiction is recorded as a supersession (closed interval 'pt_fc8b3e47bb7e05993' ← 'pt_9af28546e85d11f41', arrival-order), so the older claim is retained and closed rather than left as a second live truth

operators naming the pair : 0

VERDICT: the cross-document contradiction is RECORDED
```

The `before` warning is **byte-identical** to the one the recorded run wrote —
that is the check that the replay is a replay, not a different run. ⛔ `--arm`
is now CHECKED against the imported module and refuses when the label and the
tree disagree; it is no longer a printed label over whatever tree `import`
resolved.

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
the arrival-order fallback (the backfill test covers the other branch).

## 4. Compose with retrieval — what a reader sees now

The report's failing probe (its `contradiction` question) ranked the claim
doc-05 refutes **first**, `status: live`. After the closure the two questions a
reader can ask finally differ:

| question | before | after |
|---|---|---|
| "what is true now" (default scope) | both claims, both `live` | only *"The cold-chain audit failed ColdPath Logistics last week"*; the certificate claim is terminal and **excluded** |
| "what did we believe on 2026-02-20" (`get_historical_knowledge`) | the certificate claim, by luck of ranking | the certificate claim **via the `CORRECTS` chain**, `valid_to=2026-03-05`, `valid_from=2026-02-03` |
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
| **positive (backfill)** | a later capture carrying an EARLIER world date closes the claim world time says is older, at the prior's start (capture-keyed arm emits an inverted window the consumer refuses: `assert 0 == 1`) | ❌ | ✅ |
| **positive (read)** | a current-scope query for the old claim's own wording no longer answers with it; the terminal scope still does | ❌ | ✅ |
| **negative (support)** | the *same* two documents with the model declaring `IMPL` (support) close **nothing**; the older claim keeps its open interval and stays current | ✅ | ✅ |
| **negative (event side)** | a NAND whose emitted side is an Event closes nothing AND does not claim it did (no `recorded as a supersession` warning) | ❌ | ✅ |
| **negative (undeclared)** | a corpus that reverses itself in **wording** but declares no edge gains no contradiction. ⛔ A PIN, not coverage: no mutation of the arm can red it (the `none` shape emits no operator at all) | ✅ | ✅ |

Suite: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_14198_contradiction_closure.py -q`
→ **8 passed**. The three added cases (other emission order, backfill, event
side) were each measured RED under the mutation that reverts their gate — the
`src`-only guard, the arrival-order direction, and the missing point-only
guard — so each row above is falsifiable by an edit to this change.

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
   store. That is a separate repair.
4. **The `partial` lookup still costs recall.** In the corpus run 11 of 25
   doc-05 candidates went unchecked. When the older endpoint is *not* in the S3
   prior set, this change correctly fails open (a warning, no closure) — a
   contradiction that was never retrieved cannot be closed.
5. **An unknown world-time start falls back to arrival order.** The prior's
   `when` reaches the arm through S3's `_enrich_point_priors` (best-effort: a
   failed enrichment, or a caller that builds the prior set by hand, leaves it
   absent), and the new claim's is its own `when` on the payload point. When
   either is missing or unparseable there is no instant to compare, so the arm
   orders the pair by capture membership — the pre-fix order, meaning a
   BACKFILL still emits the inverted window, which the consumer refuses with a
   warning and a skip. The closed shape is in place; the input it needs is the
   graph's own `when`, which this change does not newly require.
6. **The replay's prior set is a reconstruction, not a recording** (§3). It
   proves the arm fires on the receipt's own declaration; it does not prove the
   shipped pipeline closes that corpus, which needs a fresh-graph re-ingest.
