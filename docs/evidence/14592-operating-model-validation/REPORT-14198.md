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

supersessions recorded      : 1
supersessions naming the pair: 1
  superseded=pt_fc8b3e47…  supersedes_by=pt_9af28546…
  evidence='declared NAND contradiction against a claim from an earlier capture (cross-session reversal; the older claim is retained with a closed interval)'

VERDICT: the cross-document contradiction is RECORDED
```

The `before` warning is **byte-identical** to the one the recorded run wrote —
that is the check that the replay is a replay, not a different run.

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
| **positive (read)** | a current-scope query for the old claim's own wording no longer answers with it; the terminal scope still does | ❌ | ✅ |
| **negative (support)** | the *same* two documents with the model declaring `IMPL` (support) close **nothing**; the older claim keeps its open interval and stays current | ✅ | ✅ |
| **negative (undeclared)** | a corpus that reverses itself in **wording** but declares no edge gains no contradiction | ✅ | ✅ |

Suite: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_14198_contradiction_closure.py -q`
→ **4 passed** (2 red on `HEAD~1`).

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
   change does not need it — the closure is decided from the S3 prior set, which
   the run already has — but a *different* endpoint whose identity lookup is
   partial is still held with no store. That is a separate repair.
4. **The `partial` lookup still costs recall.** In the corpus run 11 of 25
   doc-05 candidates went unchecked. When the older endpoint is *not* in the S3
   prior set, this change correctly fails open (a warning, no closure) — a
   contradiction that was never retrieved cannot be closed.
