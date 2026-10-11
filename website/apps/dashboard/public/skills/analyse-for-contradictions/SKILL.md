---
name: analyse-for-contradictions
description: Analyse a plan, issue, design, or strategy against the Tortoise graph to spot contradictions with previous decisions and with high-confidence claims. Extracts the document's claims, matches each against graph state, and reports contradictions with the conflicting evidence, its confidence, and what to do about it. Invoke before committing to a plan or design, when a proposal may conflict with something already decided.
domain: capability
type: Workflow
status: live
tags: [tortoise, graph, contradictions, nand, decision-audit, lifecycle, supersession]
summary: "Plan/design vs graph contradiction analysis — extract claims, match against decisions and high-confidence state, surface NAND conflicts with evidence + confidence, and separate genuine conflicts from novel or confirming claims."
created: 2026-10-11
updated: 2026-10-11
allowed-tools: read write bash grep
---

> ⛔ **Read this skill in full before running a contradiction analysis.** The deliverable is the *conflict with its evidence*, not a verdict — a contradiction reported without the point id and edge that carries it is an opinion.

# analyse-for-contradictions

Analyse a plan, issue, design, or strategy **against what the graph already
believes**, and report where it conflicts. The graph holds decisions with
lifecycle (promoted, superseded, deprecated) and confidence trajectories, so
"this plan contradicts a decision you already made" is a queryable fact rather
than a memory test.

This is the read-only counterpart to `tortoise-decide`: decide ranks options,
this skill checks a candidate against the reasoning already committed. It pairs
with `research-domain`, whose loop produces the claims this analysis compares
against.

## Which Tortoise are you on?

- **Hosted (cloud):** your agent uses the Tortoise MCP tools (`tortoise_*`).
  If you installed the skills via the onboarding wizard, the MCP server is
  already wired. Configuring it by hand? Use the equivalent project-scoped
  `.mcp.json` entry: `{"type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": {"Authorization": "Bearer ${TORTOISE_API_KEY}"}}` — that file is **committable**, so it carries the env reference, never the key. A project-scope server stays **⏸ Pending approval** until you approve it once — start `claude` in the project and allow the prompt, or run `/mcp`.
- **Self-hosted:** your agent uses the same `tortoise_*` MCP tools against a
  local MCP server pointed at your own FalkorDB, or the Python SDK directly
  (`sdk.recall_state(...)`, `sdk.review_connections(...)`, `sdk.audit()`).

Every step below is a **read** — this skill never writes to the graph. Filing the
analysis (or superseding a prior decision it invalidates) is a separate,
deliberate act through `how-to-use-tortoise`.

## The 5-step workflow (authoritative)

1. **Extract the claims.** Read the plan/issue/design/strategy and reduce it to
   atomic, checkable assertions — one claim per line, each in the document's own
   words. "We will move the ingest path to a single-flight queue" is a claim;
   "this improves reliability" is a hope and is not checkable.
2. **Match each claim against the graph.** For every claim:
   - `tortoise_recall(mode="state", query=<claim>)` — UC1: the current
     high-confidence state on that subject, object-centric, with superseded /
     deprecated / retracted claims excluded and *contested* claims flagged with
     their counter-evidence attached. This is the primary matcher.
   - `tortoise_search(query=<claim>)` — semantic matcher, for when the graph
     words the same idea differently. Annotates EP confidence but does not
     filter, so low-confidence neighbours stay visible.
   - `tortoise_query(kind="decision")` — the committed decisions, when the claim
     touches a choice the graph has recorded as decided.
3. **Test for contradiction.** A contradiction is one of:
   - **Edge-level** — `tortoise_review_connections(mode="prune",
     scope=<topic>)` flags illogical/stale IMPL/NAND connections with
     `issue in (contradictory, stale, contested)`, a `suggested_action`
     (`review` / `prune` / `re-point`), and a `detail` string. No write, no
     mutation — suggestions only.
   - **Wiring-level** — `tortoise_audit()` runs the naive-IMPL contradiction
     heuristic, plus live IMPL/NAND edges pointing into superseded points. A
     plan that re-asserts something the graph superseded shows up here.
   - **Lifecycle-level** — `tortoise_belief_timeline(<topic>)` returns the dated,
     ordered belief chain: which belief held when, what NANDed it, what
     superseded it. Use it when the claim looks like a *reversal* rather than a
     conflict ("we decided A in March; this plan assumes ¬A").
4. **Grade each match.** Read the confidence, not just the existence:
   `tortoise_get_confidence(<point id>)` → `{mean, variance, alpha, beta}`, or
   the EP breakdown on the search result. Then classify:
   - **CONTRADICTS DECISION** — conflicts with a committed decision. Highest
     severity: this is the one a human must resolve.
   - **CONTRADICTS HIGH-CONFIDENCE CLAIM** — conflicts with a well-supported
     claim (high `mean`, high evidence `total`).
   - **CONTESTED** — the graph itself is split (high NAND share / contention).
     Report it as contested, never as a settled contradiction.
   - **NOVEL** — nothing in the graph speaks to it. Not a conflict — a gap, and
     a candidate for `research-domain`.
   - **CONFIRMS** — the graph already supports it, with the supporting edges.
5. **Report, with the evidence attached.** Every conflict names the conflicting
   Point id, the operator/edge id that carries the conflict, and the confidence.
   Then say what to do about it: revise the plan, supersede the prior decision
   (deliberately, via `how-to-use-tortoise`), or accept the conflict and record
   why.

## Output contract

```text
Input: <plan/issue/design/strategy label>
Claims extracted: <n>

CONTRADICTS DECISION (must resolve)
  1. <claim>
     conflicts with: <decision point id> "<its statement>"
     carried by: <NAND edge id / review_connections issue> — confidence <mean>
     action: <revise the plan | supersede <id> via how-to-use-tortoise>

CONTRADICTS HIGH-CONFIDENCE CLAIM
  2. <claim> — conflicts with <point id> (confidence <mean>, evidence <n> edges)

CONTESTED (graph is split — not a settled conflict)
  3. <claim> — contention <ratio>; counter-evidence: <point ids>

NOVEL (no graph position — research candidates)
  4. <claim>

CONFIRMS
  5. <claim> — supported by <point id> / <edge ids>
```

## Quality gates

- **G1 — atomic claims.** Claims are extracted before matching. A whole paragraph
  matched as one blob yields a contradiction nobody can act on.
- **G2 — evidence per conflict.** Every CONTRADICTS entry carries a point id AND
  the edge/issue that carries the conflict. No id, no finding.
- **G3 — confidence stated.** Every conflict reports the confidence of the
  *conflicting* claim. "Contradicts a low-confidence claim" is usually not a
  finding — it is a claim the graph itself distrusts.
- **G4 — read-only.** This analysis writes nothing. `review_connections` is
  explicitly READ-ONLY; `audit` is read-only. A contradiction analysis that
  starts superseding points has silently become a write workflow and must go
  through `how-to-use-tortoise`.
- **G5 — separation of verdicts.** Novel and contested claims are reported as
  their own classes. Folding them into "contradictions" inflates the conflict
  count and trains the reader to ignore it.

## Error handling

- If `tortoise_recall(mode="state")` returns nothing for a claim, that is a
  **NOVEL** verdict — not a skipped claim, and not a pass.
- If the graph is unreachable, say so plainly and return the extracted claims
  without verdicts. Never mark a claim "consistent" with a graph you could not
  read — an unread graph is missing evidence, and missing evidence is not
  agreement.
- If the input document is too large to extract exhaustively, state the scope
  that was analysed (which sections, which claims) rather than implying full
  coverage.

## Anti-patterns

- ⛔ **Never report a contradiction without the edge or point that carries it.**
  A conflict with no id is an opinion and cannot be verified or superseded.
- ⛔ **Never treat a low-confidence claim as a contradiction.** The graph
  already discounts it; report it as contested or novel instead.
- ⛔ **Never NAND a plan's claim to express "bad fit".** Contradiction is about
  truth; relevance is a mitigation on the operator (`[0.10, 0.50]`). Filing a
  NAND for a disagreement about emphasis corrupts the graph's belief state.
- ⛔ **Never supersede the conflicting decision as part of the analysis.** That
  is a separate, deliberate write through `how-to-use-tortoise`, and the human
  owns it.
- ⛔ **Never conclude "no contradictions" from a search that returned nothing.**
  An empty result means *no matching prior*, which is exactly the NOVEL class.
