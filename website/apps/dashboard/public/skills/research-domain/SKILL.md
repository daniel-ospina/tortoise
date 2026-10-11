---
name: research-domain
description: Research a domain against the graph in a loop — go broad on a subject, mine the findings into Tortoise, ask the graph's UC2 "gaps" recall where the reasoning is load-bearing but under-supported, research exactly those gaps, and repeat until the analysis is thorough (default 3 cycles). Invoke when asked to research a domain, go deep on a subject, or produce a sourced analysis that says what is still unknown.
domain: capability
type: Workflow
status: live
tags: [tortoise, graph, research, uc2, gaps, mining, epistemic-loop, closed-loop]
summary: "The graph-guided research LOOP (part of tortoise-decide): research(broad) → mine → UC2 gaps → research(gaps) → mine → repeat. The graph, not the note-taking, decides what to research next."
created: 2026-10-11
updated: 2026-10-11
allowed-tools: read write bash grep
---

> ⛔ **Read this skill in full before starting a domain research loop.** The loop's control signal is a graph query, not a feeling that enough has been read — skipping the mine or UC2 step turns this into ordinary research with extra filing.

# research-domain

Research a subject **as a closed loop over the graph**. Ordinary research collects
sources and stops when the reader loses interest; this loop asks the graph where
the reasoning is *load-bearing but under-supported* and sends the next research
round at exactly those weak spots. Each cycle narrows what is left to learn, so
every round has a reason and the loop can state when it is done.

This is the researched-loop half of `tortoise-decide` — it is what makes the
decision's criteria/findings evidence-backed instead of asserted.

## Which Tortoise are you on?

- **Hosted (cloud):** your agent uses the Tortoise MCP tools (`tortoise_*`).
  If you installed the skills via the onboarding wizard, the MCP server is
  already wired. Configuring it by hand? Use the equivalent project-scoped
  `.mcp.json` entry: `{"type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": {"Authorization": "Bearer ${TORTOISE_API_KEY}"}}` — that file is **committable**, so it carries the env reference, never the key. A project-scope server stays **⏸ Pending approval** until you approve it once — start `claude` in the project and allow the prompt, or run `/mcp`.
- **Self-hosted:** your agent uses the same `tortoise_*` MCP tools against a
  local MCP server pointed at your own FalkorDB, or the Python SDK directly
  (`sdk.create_point(...)`, `sdk.recall_gaps(...)`, `sdk.review_connections(...)`).

The steps below name the MCP tools, which is the same surface for both audiences.

## The loop (authoritative)

```text
1. SCOPE        one domain label + the question the loop must answer
2. RESEARCH     go broad — sourced claims, not prose summaries
3. MINE         file each claim with provenance; connect it to itself AND to
                what the graph already holds
4. UC2          ask the graph: load-bearing but under-supported claims
5. RE-RESEARCH  target the returned gaps (and only those) → back to step 3
6. STOP         cycle budget (default 3) OR convergence → thorough analysis
```

**Step 1 — scope.** Fix a short domain label (`2026-Q3-solid-state-batteries`)
and the question. Everything filed in this loop is scoped to that label so the
gaps recall can be topic-scoped rather than population-wide.

**Step 2 — research broad.** Use whatever research depth the task needs —
Perplexity/web search for breadth and current state, scholarly search for
primary literature, vendor documentation only for vendor claims. (The fleet's
modular `research` skill wraps this step; if it is not installed, the agent's own
research tooling is the substitute.) Output of this step is **claims with
sources**, not a narrative: "X is true — because <source>", one claim per line.

**Step 3 — mine.** This is the step that makes the next cycle possible. For each
claim:

1. `tortoise_create_point(kind="statement", content=<the claim>,
   extractedFrom=<source url>, props={"sourceKind": "T1"})` — file the claim
   with its provenance. Tiers are the SourceKind taxonomy in
   `how-to-use-tortoise`: T0 direct observation … T4 speculative. A claim with
   no tier is a claim you cannot grade later.
2. Connect it to **itself** — the claims in this round support or contradict
   each other. `tortoise_create_operator("IMPL", src, [targets])` where the
   source implies the target; `tortoise_create_operator("NAND", src, [targets])`
   where it conflicts.
3. Connect it to **what the graph already holds** — this is the half that is
   usually skipped, and the half that makes the graph a memory instead of a
   dump. `tortoise_review_connections(mode="add", scope="<domain label>")`
   returns related-but-missing connections (`from`, `to`,
   `suggested_relation`, `reason`, `similarity`) against the pre-existing graph.
   Review them and wire the ones that hold — suggestions are a nudge, never an
   enforced write.

**Step 4 — UC2 (find the weak spots).** Ask the graph what it cannot yet
support:

```text
tortoise_recall(mode="gaps", query="<domain label>")
```

Returns the claims that carry epistemic **load** (outgoing IMPL + NAND — other
claims lean on them) with little **support** (incoming IMPL + `extractedFrom`
Source edges), ranked by `load / (1 + support)`. Each result carries the
`gaps_ranking` breakdown (`outgoing_impl`, `outgoing_nand`, `incoming_impl`,
`source_count`, `load`, `support`, `confidence`) so you can see *why* it is a
gap. Preset: `min_load=1`, `max_support=2`, `limit=20` — all overridable per
call. Use `kind=` instead of `query=` for a population scan across a pointKind.

Run `tortoise_audit()` alongside it: it reports the wiring-quality defects the
gaps score does not — missing `sourceKind`, missing `sourceDate`, IMPL/NAND
edges pointing at superseded points, and low-confidence operators with no
mitigation. A gap that is a *defect* (no provenance) is fixed by filing; a gap
that is a *question* (load-bearing, genuinely unsupported) is the next research
round.

**Step 5 — re-research, then repeat.** Take the top gaps (the loop is a
prioritisation device, so work them in rank order) and research each one as a
question: "the graph rests on <claim>; what evidence settles it?" Mine the
answers back through step 3. Then re-run step 4.

**Step 6 — stop.** Two reasons, and the second is the better one:

- **Budget exhausted** — the cycle count (default 3, a parameter: fewer for a
  quick scan, more for a deep dive).
- **Converged** — `mode="gaps"` returns nothing above `min_load`, or the round's
  mining added no new claim. Convergence is the honest exit; a bare cycle count
  is the fallback for a subject that keeps opening new gaps.

A loop that has run out of cycle budget **without** converging is a finding, not
a failure — say so, and name the remaining gaps. Never report the analysis as
thorough when the graph still lists open gaps.

## Cycle log (keep it, it is the audit trail)

Record one line per cycle — it is what makes the "thorough by cycle 3" claim
checkable rather than asserted:

```text
cycle 3 | researched: <the gaps this round targeted> | filed: <n> claims,
  <m> edges | gaps: <n> open before → <m> after | sources: <new source urls>
```

## Output contract

```text
Domain: <label>
Question: <the question the loop answered>
Cycles run: <n> (exit: converged | budget)
Findings:
  1. <claim> — <confidence> — sources: <urls> — rests on: <edges>
  ...
Open gaps (still load-bearing, still under-supported):
  1. <claim> — load <n> / support <m> — <what would settle it>
Contradictions found: <n> (see analyse-for-contradictions)
```

## Quality gates

- **G1 — provenance.** Every claim filed has an `extractedFrom` source and a
  `sourceKind` tier. A cycle that files unsourced claims has not mined.
- **G2 — connection.** Every cycle runs `review_connections(mode="add")` and
  reports what it wired to the pre-existing graph. Zero connections to prior
  graph content is a red flag, not a clean round.
- **G3 — control.** Every cycle after the first is driven by the previous
  cycle's `mode="gaps"` output. Re-researching something that was not a gap is
  how the loop degenerates into ordinary research.
- **G4 — structure.** `tortoise_check_structure()` (or
  `tortoise_overview(section="structure_check")`) before presenting: no orphan
  operators, no dangling refs. A loop that runs on a structurally broken graph
  ranks garbage.

## Error handling

- If a research call fails, report it and **do not** mine fabricated claims in
  its place — an empty round is a fine outcome, an invented source is not.
- If `tortoise_recall(mode="gaps")` returns nothing on the *first* cycle, the
  problem is usually upstream: nothing was wired with load. Check that the mined
  claims have outgoing IMPL/NAND edges before concluding the domain is
  well-supported.
- If the graph is unreachable, say so and continue the research with sources
  listed in the output — Tortoise is advisory, never a hard block.

## Anti-patterns

- ⛔ **Never mine a claim without its source.** An unsourced Point is
  indistinguishable from an assumption, and `mode="gaps"` will (correctly)
  report it as a gap forever.
- ⛔ **Never let the loop skip step 3's *pre-existing* connection half.** Mining
  a claim into an isolated island produces no load, so the next UC2 pass sees
  nothing — the loop silently stops learning.
- ⛔ **Never NAND a claim for weak relevance.** Relevance is expressed as a
  mitigation on the *operator* (strength `[0.10, 0.50]`), never as a NAND
  against the claim — see `how-to-use-tortoise` "Truth vs Weight".
- ⛔ **Never summarise instead of filing.** The graph is the memory; a chat
  summary is a derived projection of it. If a claim only exists in the final
  report and never as a Point, the next session cannot build on it.
- ⛔ **Never loop past convergence to hit a cycle target.** Extra cycles on a
  converged domain manufacture redundant edges, and redundant edges change EP
  rankings.
