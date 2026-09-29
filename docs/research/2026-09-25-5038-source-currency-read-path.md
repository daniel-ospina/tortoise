---
title: "Source-version currency in the read path — should staleness be default search behaviour? (#5038 O3)"
type: research
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-09-25
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Source-version currency in the read path (#5038 O3)

**Question.** Once a Point records which version of a source it was read from (#5256), where should the
"is this fact still current, stale, or unknown?" check live — a separate script, a public SDK method, an
MCP tool, or the existing default search behaviour?

**Search tool:** `web_search` with `model="sonar"` + `mcp__exa__web_search_exa` for scholarly discovery —
**rung 2 + rung 3** (rung 1 unavailable: `mcp_load seo-intelligence` → `Unknown MCP server`, re-tested
2026-09-25). Three of four initial Perplexity calls returned 429 and were retried.

## Outcome

The contradiction test bites, and it splits the answer in two.

**Already decided — and it matches the instinct behind the question.** The owner ruling on #5038 of
**2026-09-24T12:43:32Z** (*"The policy — B: mark stale, supersede on re-inference"*) already places the
check exactly where the question proposed: *"A currency check reads the version recorded on the
extraction link and compares it to the source's current `contentHash`. That is a read… **no stored
`status` field**, no background job required for the mark to be visible."* The same ruling requires the
stale belief to stay **"still there, still readable, and flagged"** — *"Nothing is silently withdrawn to
satisfy a version bump."*

**Not adoptable — a reopen.** Any proposal to *withhold* stale points from default reads contradicts
Policy B's "still readable, and flagged". It is not a candidate for adoption with a caveat; the route is
to reopen Policy B in its own home (#5038) with the evidence and argue it.

## External findings

| Finding | Tier | Sources |
|---|---|---|
| Validity belongs in the read path, not beside it | **High** | MemStrata (2606.26511), SodaMem (2608.08055), TEPA (2608.07429), Patha spec (4 independent) + RAG practitioner posts (Oracle, twig, particula) |
| A visible flag alone is frequently not acted on | **High** ⚠️ scope-limited | "Revoked but Still Authoritative" (2609.08258 — five systems measured), STALE (2605.06527), TEPA (2608.07429) |
| Fine-grained invalidation beats coarse | **High** | Invalidation Contracts (2609.00243): row-level precision 1.00 vs table-level 0.25 |
| Status is a hard gate; time is a soft ranking bonus | **High** | SodaMem (`β=0.3`) + our existing `status` vs `valid_from`/`valid_to` split |
| Our anchor covers only the provenance-anchored case (~18% class) | **High** | MemStrata's stated scope limit + STALE's implicit-conflict results |
| Zep Graphiti exposes temporal fields on results, but whether superseded edges are filtered by default is undocumented | ⚠️ **single-source** — verify when a new source is available | Zep docs/blog only |

**The decisive measurements.**
- *"Revoked but Still Authoritative"* (2609.08258): **"no system enforces revocation by default: the
  revoked fact is returned wherever the revocation label is visible to the retrieval layer, outranks its
  replacement, and leads agents to the unsafe action."** Five agent-memory systems measured.
- STALE (2605.06527): the **current-state adjudication gap** — new evidence reaches retrieval results in
  **77.5%** of cases, yet only **3.3%** of old entries are judged as needing an update. *"Visibility does
  not imply authority."* Best model evaluated: 55.2%.
- MemStrata (2606.26511 / 2608.20685): forced to answer, RAG serves the superseded value **36–38%** on
  real GitHub history; an **LLM reranker makes it slightly worse (37.7%)**. Latency parity because no
  LLM runs on the read path.
- TEPA (2608.07429): stale-but-*active* memory is **worse than no memory** under full reversal
  (0.210 vs a 0.309 no-memory baseline) — *memory pollution*.
- Invalidation Contracts (2609.00243): row-level eviction precision **1.00**; table-level **0.25**,
  *"destroys co-located entries"*. Argument against ever refreshing a whole source at once.

**Scope limit on that evidence, stated plainly.** These papers measure **revoked/superseded** records
being returned alongside their replacement. Policy B keeps a *stale-but-not-yet-superseded* belief
readable for the window before re-inference produces a successor. Those are **not the same class**, and
the papers do not measure Policy B's window. The evidence is an argument that a flag can be ignored — a
reason to reopen — not a measurement of Policy B itself.

## Contradictions between sources

One, and it is the central one: **Policy B (owner, 2026-09-24) requires a stale belief to stay readable
and flagged; the agent-memory literature measures that a readable flag is not acted on.** Both sides are
recorded above. The literature does **not** contradict the read-time / no-stored-status *mechanism* —
only the "still authoritative" consequence of leaving it non-enforcing. No other source-vs-source
contradiction was identified.

## Recommended shape (Policy-B-compatible — implementable without reopening anything)

1. **Derive at read time** — compare the version recorded on the extraction link with the source's
   current `contentHash`. Nothing written, no stored status, no background job (Policy B's own mechanism).
2. **Report `current` / `stale` / `unknown` on the existing result row**, on the default read surfaces —
   `unknown` when either side is missing. **No withholding.**
3. **Leave the enforcement question to the owner** (see the reopen below).
4. **Keep time soft** — `valid_from`/`valid_to` stay a ranking input, not a filter.

## Open owner question (the reopen)

Policy B chose *"flagged, still readable"* over withdrawal; the literature measures that a visible flag is
frequently not acted on. Options: **(i)** keep Policy B as ruled — flag only, accepting that a consumer
may still act on a stale fact; **(ii)** reopen B toward enforcement on the read path only (stale results
ranked below current ones, or withheld from default reads with the existing history opt-in) while keeping
history fully reachable. **Recommendation: put (ii) to the owner, but (i) is the standing decision and
must not be reversed by an implementer.**

**Adoption gate: `reopen <Policy B, #5038 comment 5814346672, 2026-09-24>` — evidence attached above; the
placement half (read-time, no stored status, existing surfaces) is already decided and needs no
question.**

## Notes

- Step 5.4 (write claims to the epistemic graph) skipped: `TORTOISE_API_KEY` not set on this machine — a
  set-up gap (`not_configured`), **not** an outage.
- Verified by a fresh-context reviewer (research skill Step 5.5), which is what caught the missed
  contradiction and replaced the initial `adopt` verdict with this `reopen`.
