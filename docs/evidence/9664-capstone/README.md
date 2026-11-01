---
title: "#9664 capstone — the ChatGPT onboarding journey: the measurable half, and three steps corrected"
type: evidence
domain: platform
doc_status: live
created: 2026-10-22
updated: 2026-10-26
subjects.team: epistemic-team
aboutSubjects: Tortoise onboarding, MCP connector, ChatGPT
aboutObjects: "#9664, #9340, #2208, tortoise-onboarding, website/docs.html, app.premiselabs.co/skills"
---

# #9664 — capstone acceptance: the ChatGPT onboarding journey

**Status: the run HAS been executed (2026-10-26) and is published in
[`run-2026-10-26/`](run-2026-10-26/INDEX.md). This file is the half an agent can measure
without a browser, and it records three of the ten steps as WRONG-AS-WRITTEN.**

The run's verdict table is the sibling index. Read the two together: this file says what
*should* happen and why a step's wording was wrong; the run says what *did* happen, through the two
surfaces it drove (the Tortoise dashboard and the real ChatGPT UI), with a dated screenshot or a
trace for each row, and it names the two remaining blockers — `#8617` (step 2a, graph creation
refused by the FalkorDB ACL layer) and `#10088` (step 4, the authorize page's sign-in delivers the
code to the marketing host). The claims this file makes about ChatGPT's surface, and about the
connector's ugly cases, have been superseded since they were written, and each is marked where it
lives: the status line above, **replaced** with the run's own status rather than annotated; row 3 and
its §3 narrative of the same correction, both **marked `SUPERSEDED` in place** — #9660 reversed the
"no dashboard surface" reading on 2026-10-23, two days after the build this run drove; and the "no
addressable form on the connector" paragraph, **marked `SUPERSEDED` in place below**.

Issue #9664 defines a *manual, agent-run clickthrough* across three surfaces (Tortoise
web, the ChatGPT UI, our MCP server). It is deliberately **not** a script and **not** a CI
job, and this directory does not make it one: nothing here encodes a verdict, drives a
journey, or is selected by CI. What it does is measure the **premises** each step rests
on, so that the human/agent run can distinguish *the product is broken* from *the step
does not describe the product we ship*. Three of the ten steps did not describe it.

Every claim below is `MEASURED` (this directory's logs) or `STATIC` (a file:line in the
tree that a re-run must re-verify). A step with no measurement is `NOT COVERED` — never
`verified`, and never inferred from an adjacent step.

| Artifact | What it measures |
|---|---|
| `01-production-surface.log` | the **deployed** surface (`api.premiselabs.co`, `tortoise.premiselabs.co`, `app.premiselabs.co`) — deploy state, OAuth discovery, the unauthenticated challenge, and the two live documents steps 3 and 6 actually use. Verified against **production**, not `main` (the `#2208` lesson recorded on capstone #2008). |
| `02-step-premise-census.log` | `probes/step_premise_census.py`: the MCP tool names the steps name, the canonical onboarding gates, step **2**'s three pack-resolution arms (`configured: false` is not the catalog union), and the RFC 8707 resource forms a connector may declare. |
| `03-onboarding-closure-tests.log` | steps 5/8/9's server-side closure contract, MEASURED by running its pins (`tests/test_onboarding_auto_complete.py`, 42 passed). |
| `04-graph-scoping-tests.log` | step 7 — `tortoise_list_graphs` returns only the entitled graph (`tests/test_cross_tenant_read_isolation.py`, 9 passed, seeded present/absent pairs + negative controls). |
| `05-dashboard-copy-tests.log` | steps 3/10's copy constants (`node --test` over the pure modules; no `node_modules` needed, 38 passed). |

Measured at `git HEAD 653980445f` (2026-10-22), with the census (`02`) re-run at
`b39c70d8c` after its rows were made to measure the clause they print, and again at
`2227e52de` (2026-10-25) when step 2 became a MEASURED row instead of a `STATIC`
one (the rows were first added at `b966664db`; the shipped log is the re-run after
the round-1 review fix, whose fail-closed guards change no printed row — the log
body is byte-identical under them), and again at `a310c1d9c` (2026-10-25) for the round-4 review's two
residuals — row 2b's *"the catalog union (#2714 indicator 3)"* clause and the
starter set's `TORTOISE_STARTER_PACKS` override. Only **one printed row changes**
there (2b's evidence line gains the write-gate reading the clause now measures:
`None` admits, an explicit set constrains; every other row, and the `25 premises, 0
unmeasured sections` total, is unchanged); deployed
`commit_sha 81f5594dce` — an
ancestor of `origin/main` `2bf86487c1`, **39 commits behind** it
(`git rev-list --count 81f5594dce..2bf86487c1` → `39`; deploy lag, not a stale or
rolled-back deploy; the served surfaces are therefore older than the tree this was measured
against, which is why the two live documents are read from production rather than the repo).

## Per-step verdict

Verdict vocabulary: **VERIFIED-HERE** (measured in this directory, at the layer named) ·
**NOT-COVERED-HERE** (needs a browser / a ChatGPT account / the human Pro auth — the
reason is given, and it is the explicit uncovered list) · **WRONG-AS-WRITTEN** (the step
contradicts the shipped system; the corrected wording follows the table) · **BLOCKED**.

| # | As written | Verdict | Basis |
|---|---|---|---|
| 1 | sign up, name workspace → `team-named` closes | NOT-COVERED-HERE | premise MEASURED: `team-named` is auto-satisfied at org-create and is never an agent act (`state._NON_AGENT_STEPS`, census row 1). The clickthrough itself needs a browser. |
| 2 | create graph; Settings → Configured packs, narrow → the chosen set persists; `configured` reflects narrowing | NOT-COVERED-HERE | premise MEASURED (census rows 2a-2e): `GET/PUT /v1/graphs/{graph_id}/packs` returns `configured`, and `false` means *never explicitly configured* — the catalog union only when the graph ALSO has no `:PackInstall` records; with records it resolves to `recorded ∪ data`, narrower than the catalog whenever a catalog namespace has neither a record nor any data in the graph (measured: catalog 7, starter 5, `catalog−resolved = portfolio, venture`). A provisioned default graph is in the records arm — **attribution, not measured here**: the seeding calls are `supabase_control._mint_default_graph_row_best_effort`, invoked from `provision_org` (`supabase_control.py:3160`) and `provision_org_with_token` (`:3229`), which `hosted_api.py` reaches at `:11542`, `:21641`, `:28586`, `:40739` and `:40752`, and the narrowing that follows was measured by #9691's embedded probe; what this census measures is the resolution and the self-heal. A record-less graph narrows again on its FIRST packs READ (census row 2e), so step 2's *"the default is catalog union + core"* is false for the surface the user opens (`docs/EXPANSION_PACKS.md` §"Per-graph pack selection (#2728)", #9691, `pack_state.resolved_pack_set`) — not *no packs* (`website/apps/dashboard/src/packs.js:1-30`). Persistence needs a browser. |
| 3 | Setup-guide card → choose ChatGPT → the card's steps, as rendered | **WRONG-AS-WRITTEN** | ChatGPT has **no dashboard surface at all**: `#2698` deleted its tab, `#2912`'s families exclude it, and the source says so in terms — *"chatgpt … has NO dashboard surface: … Its live carrier is the public setup docs page (#4836)"* (`harnesses.js:865-867` @ `81f5594dce`). MEASURED: the served door is `https://tortoise.premiselabs.co/docs#chatgpt` (HTTP 200, section present, `01` log). **SUPERSEDED in place 2026-10-23 by `#9660`** — the sentence above is exact for the deployed build this run drove (`81f5594dce`, 2026-10-21), and `#9660` (`ebad38f29`) landed two days later, so `chatgpt` is now a first-class chooser family (`harnesses.js:880`) and the served onboarding document says so itself (`run-2026-10-26/skill-tortoise-onboarding.md:138-141`), with the docs page kept as the reference copy. Updating row 3 against the current tree is `#9664`'s remaining work, not this publication's. |
| 4 | ChatGPT: add custom MCP server = `CANONICAL_MCP_URL` → OAuth; consent shows the right workspace; token issued | NOT-COVERED-HERE | premises MEASURED: PRM + AS metadata are live (DCR `/register`, `/oauth/authorize`, `/oauth/token`, PKCE `S256`, `resource=https://api.premiselabs.co/mcp`), an unauthenticated `POST /mcp` is **challenged** with `resource_metadata` (401, `01` log). The consent chooser is **org**-scoped (`_selectable_orgs`, `oauth.py:845`). The ChatGPT side needs the human Pro auth; the recipe differs from the step — see correction 3. |
| 5 | `connection-written` + `harness-connected` close **at token issue** | **WRONG-AS-WRITTEN** | MEASURED (census rows 5a-5e): `connection-written` is in **no** completion gate and is a CLIENT checkpoint (`#3451`, `_NON_AGENT_STEPS`, `_CHECKPOINT_STEPS`); `harness-connected` is filed by the server on the **first successful agent write** (`#3784`/`#3913`), i.e. at step 8. Nothing closes at token issue. |
| 6 | the agent calls `tortoise_load_skill` | **WRONG-AS-WRITTEN** | MEASURED (census rows 6a-6e): no such tool — `0` of the 81 registered tools contains `skill`, and the name occurs in no pre-existing **surface or implementation** — every occurrence is either in a file this PR adds or edits, or in the census probe, its pin and its log, where the probe and the log **measure** its absence and the pin only keeps the row selectable (`git grep tortoise_load_skill`). ChatGPT is *skillless by design* (`HARNESS_SKILLLESS`), and the served onboarding document it is handed names the **advertised** `tortoise_overview(section="health")` for verification (the legacy `tortoise_health` it mentions ×11 is retired — census rows 6b/6c) plus `tortoise_onboarding_state` ×6, `tortoise_create_point` ×3, `tortoise_file_decision` ×1, `load_skill` ×0 (`01` log). Those are whole-document **mention** counts, and they are not the instruction this path follows — measured per section: §3b ×4 (`SKILL.md:249, 343, 499, 508`), §4 ×3 (`:607, 614, 624`), **§7 ×1 (`:908`)**, §5 ×0, plus the frontmatter pair and one retirement disclosure (`:648`). The §7 mention is the one a ChatGPT runner still meets: the document's ChatGPT carve-out is the §4 bullet, and §7's decide protocol (`:907-908`, "Verify the live connection first with `tortoise_health`") is **not** carved out — capstone step 9 runs exactly that protocol, so step 9's trace must be read against the retirement (`#5439` tracks the copy). |
| 7 | `tortoise_list_graphs` returns only entitled graphs | VERIFIED-HERE (contract layer) | `tortoise_list_graphs` filters to `org_{org_id}`/`team_{org_id}` in HTTP mode (`mcp_server.py:3653-3688`, the tenant set at `:3679` and the filter at `:3687`), pinned by 9 tests with a seeded present/absent pair and a negative control per seam (`04` log). Through ChatGPT: NOT COVERED. |
| 8 | file the first points → `first-points-filed` closes | VERIFIED-HERE (server layer) | 42 tests green (`03` log), including the call-site layer (`-k handler_env`) that reds if a point write stops observing the step. Through ChatGPT: NOT COVERED. |
| 9 | run a decision → `decide-completed` closes | VERIFIED-HERE (server layer) | same 42 tests; `decide-completed` is filed only on a decision-shaped write (`tortoise_file_decision`, or a `pointKind` in `DECISION_SHAPED_POINT_KINDS`) — both documented decide protocols. Through ChatGPT: NOT COVERED. |
| 10 | capture disclosure card: capture unavailable for ChatGPT, not silence | VERIFIED-HERE (copy) | `HARNESS_CAPTURE_SUPPORT.chatgpt === false` renders `HARNESS_CAPTURE_REASON.chatgpt` = *"ChatGPT connects from its own cloud — session capture isn't available for it"* (`harnessStatusCard.jsx:120`), pinned by 38 green tests (`05` log). Not silence. |

**The `NOT COVERED`-through-ChatGPT half of this table is now measured — though not all of it
*through ChatGPT*.** Rows **6, 7, 8 and 9** were observed through the real ChatGPT UI on 2026-10-26
with dated screenshots and traces; rows **2b and 10** were observed on the **Tortoise dashboard**
(`app.premiselabs.co`), which is where the packs dialog and the capture-disclosure card live — row 10
is the dashboard's own harness list rendering ChatGPT's reason, not a ChatGPT screen. Rows 3 and 5
were observed *as corrected*; row 2a and row 4 are the two blockers left, `#8617` and `#10088`. Row 1
was observed as **state, not as a sign-up** — the run started from an existing org and read
`team-named` complete in `tortoise_onboarding_state`, so the sign-up path itself is still
unexercised. Read this table for *what the step should be*, and the run index for *what happened* —
this one is deliberately not rewritten per row, because its basis column
is a claim about the repo at a measured commit.

**Step 2's premise became `MEASURED` last** (census rows 2a-2e). It was the only step
whose basis this table recorded as `STATIC` — the state in which nothing re-read the
premise (steps 3 and 10 have no census row either; their premises are measured by
`01` and `05`, and no step row has ever carried `STATIC` but step 2's) — which is how #9691
falsified step 2's parenthetical — *"the default is catalog union + core"* — by hand instead
of by the instrument. The rows now drive the real `pack_state.resolved_pack_set` through all
three arms over a fake graph handle that supplies only the INPUTS, so the same defect
prints `FALSIFIED`; the pack surface's own pins
(`tests/test_pack_selection_by_graph.py`, 52 passed) agree with every row. What the rows
do **not** measure is that provisioning *reaches* the seeder — that stays an attribution
to the three call sites above, and the row says nothing about it.

## The three corrections

**3 — the door is the public docs page, not the Setup-guide card.** As measured on the deployed
`81f5594dce`, ChatGPT is key-less OAuth with no dashboard surface, and the steps live at
`website/docs.html#chatgpt` (served at
`https://tortoise.premiselabs.co/docs#chatgpt`). The recipe declared there *then* was also different
from the step: **Enable Developer mode** (chatgpt.com → Settings → Security and login) → open
`chatgpt.com/plugins` → **+** → create a Developer-mode app → server URL
`https://api.premiselabs.co/mcp`, no key → **Choose OAuth** → **Scan Tools** → sign in to
Tortoise → **Authorize** → pick the organization. **Both halves of this correction are superseded
in place, and row 3's SUPERSEDED note above is the marker:** `#9660` (`ebad38f29`, 2026-10-23) gave
ChatGPT its own chooser family and rewrote the served page's recipe to `chatgpt.com/plugins` →
**Add** → **Add custom MCP server** — the published onboarding copy carries the same recipe
(`run-2026-10-26/skill-tortoise-onboarding.md:124-132`). Corrected observation, kept as the dated
record of what the run saw: *"the docs page's
ChatGPT section, as served, and ChatGPT's Developer-mode app creation + Scan Tools + OAuth
consent naming the right organization."*

**5 — the only step that closes near a connection is `harness-connected`, and it closes at
step 8.** `connection-written` is a *client* record of having written an MCP config file;
the server cannot observe it (`#3451`). A ChatGPT connection has no config file to write,
so the ChatGPT path should be expected to leave `connection-written` **unset — and that is
not a failure**: it is in no completion gate (`_GATE_SELF` / `_GATE_BUILD` / `_GATE_COMPACT`
require `harness-connected` + `first-points-filed`, plus `decide-completed` +
`team-named` on the self fork). Corrected observation: *"at token issue: nothing.
`harness-connected` + `first-points-filed` close on step 8's first successful
`tortoise_create_point`; `decide-completed` on step 9's decision."*

**6 — the pointer prompt is a document, not a tool call.** The human opens
`https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md`, copies the whole
document, and pastes it into the chat (ChatGPT has no local skills). The agent then follows
*that document*: `tortoise_overview(section="health")` to verify — the document's ChatGPT
section says so in terms, because the legacy `tortoise_health` name is retired and is absent
from `tools/list` and from this connector's tool grant (`#3883`) — then
`tortoise_onboarding_state` to read state, `tortoise_create_point` to file. The `×11`
`tortoise_health` count in the `01` log is a whole-document **mention** count, not this
path's instruction — measured per section: §3b ×4 (`:249, 343, 499, 508`), §4 ×3
(`:607, 614, 624`), **§7 ×1 (`:908`)**, §5 ×0, plus the frontmatter pair and the one
retirement disclosure (`:648`). The line numbers are those of the **published copy**
(`run-2026-10-26/skill-tortoise-onboarding.md`, the served document as of the run); it has
seven more lines than the 2026-10-22 measurement head (`653980445f`, see the measurement note
above), while the census's own re-run head (`a310c1d9c`) already carries the published
numbering — the drift is measured against that earlier head only, and the counts are unchanged
by it: all nine lines still fall in the sections named here. The §7 one matters for this run: the document's ChatGPT
carve-out is the §4 bullet, so a ChatGPT agent reaching §7's decide protocol is still told
"verify the live connection first with `tortoise_health`" — which is **step 9** of this
capstone, and that name is retired. Read step 9's trace against the retirement
(`tortoise_overview(section="health")`), not as a broken product; the copy itself is
`#5439`'s. Corrected observation: *"the tool-call trace begins
`tortoise_overview(section="health")` → `tortoise_onboarding_state` → `tortoise_create_point`,
`tortoise_health` never appears in the grant because it is retired, and `tortoise_load_skill`
is never called because it does not exist."*

## Blocker on the "deliberate ugly cases"

> **SUPERSEDED 2026-10-26 by the run — read this paragraph as history, not as the current
> state.** It was measured before #9340's *tool-argument* ship (`#9699`). The run then observed,
> through the ChatGPT UI: a `graph` **tool argument** exists on graph-touching tools; a **read**
> scoped to a foreign graph id reaches Tortoise and is refused
> `INVALID_ARGUMENT: No graph 'org_000…' is reachable from this workspace`; and a **write**
> carrying a graph argument is blocked by the client before it ever reaches us (`#10084`). So the
> blocker moved from *"the connector has no addressable form"* to *"the primary client will not
> deliver the write shape"* — a different layer, a different owner. What follows is the
> pre-#9699 reading, kept because the resource-vs-argument distinction it draws is still how the
> OAuth-scoped form behaves.

The four ugly cases (#9340) — an explicit non-default graph, a duplicate graph **name**, a
graph id from another workspace, a soft-deleted graph — have **no addressable form on the
connector** *via the OAuth resource*. MEASURED (census rows `4+ugly`): `parse_resource` accepts only the bare
MCP resource, the origin root, and `…/mcp/organizations/<org_id>`; a graph-scoped resource
is `REFUSED(400 invalid_resource)` (`oauth.py:812-840`), and the access token carries no
`graph_id`, so every connector lands on the workspace's default graph. Those cases are
therefore **BLOCKED on #9340**, not "not covered": they cannot pass, and a capstone closed
on them would be recording a capability we did not ship.

## The explicit uncovered list (what the run still owes)

**Discharged by the run on 2026-10-26 (`run-2026-10-26/INDEX.md`) except where noted:**

1. Step 1 — sign-up, workspace naming, `team-named` closing (browser). → **DONE for `team-named`**
   (`step1*`, observed as **state** — `completed_steps` in `step7-graphs-and-step5-onboarding-state.txt`).
   **Sign-up and workspace naming remain unexercised** — the run started from an existing org, so no
   sign-up was performed (see the row-1 paragraph above).
2. Step 2 — narrowing packs and the persisted `configured` flag (browser + tenant).
   *The premise* is MEASURED (census rows 2a-2e); what is uncovered is the click. → **DONE for 2b**
   (`step2g`–`step2l`: narrow → save → reload, with `configured` flipping) — **not** `step2m`–`step2o`,
   which do **not** carry the narrowed set (`step2m` and `step2o` are all five Active; `step2n` shows
   `agent-ops` Off *with* the save banner on screen); see the index's 2b row for what each frame shows;
   **2a remains a FAIL** on `#8617`.
3. Steps 4/6/7/8/9 **through ChatGPT** — the tool-call trace as the model actually emits it,
   the OAuth consent as ChatGPT renders it. Needs a ChatGPT Plus/Pro account and a human to
   complete the sign-in/authorize (`#9664` step 4 says so explicitly). → **6/7/8/9 DONE**
   (`step6*`, `step7*`, `step8*`, `step9*`); **step 4 BLOCKED** on `#10088` — ChatGPT's **connect
   dialog** renders, but the authorize page's sign-in returns the code to the marketing host, so the
   authorize page's **consent view** is never reached and no token is issued.
   It needs a product fix, not a human keystroke.
4. Step 10 — the card **as rendered** (the copy is pinned by test; the rendering is not). → **DONE**
   — `step10-capture-disclosure-tall-…130452.png` is the frame that carries the ChatGPT row
   (`step10-…-as-rendered-…130416.png` is a blank Settings page, per the index's filename note).
5. The four ugly cases — BLOCKED on #9340. → **re-measured**: the graph *argument* is addressable and
   a foreign id is refused correctly; the **write** shape is blocked by the ChatGPT client
   (`#10084`). See the run index's per-case table and the SUPERSEDED note above.

Screenshots and the model's tool-call traces now exist for every step, with ONE scope to the word
"screenshots": step 5 is trace-only (`step6e-full-trace.txt`,
`step7-graphs-and-step5-onboarding-state.txt`) because the run's dashboard half never rendered a
step-5 screen — there is no `step5*` artifact. Every other row carries a dated screenshot. What
remains is **two product blockers**, not two missing artifacts: `#8617` (step 2a) and `#10088`
(step 4).

## Re-running the measurable half

```bash
uv sync --extra embeddings --extra parity
.venv/bin/python docs/evidence/9664-capstone/probes/step_premise_census.py
uri="$(.venv/bin/python tools/test_lane.py uri)"; eval "$uri"       # a private container — never the canonical store
.venv/bin/python -m pytest tests/test_9664_premise_census.py -q -p no:cacheprovider   # the census's own guards
.venv/bin/python -m pytest tests/test_onboarding_auto_complete.py -q -p no:cacheprovider
.venv/bin/python -m pytest tests/test_cross_tenant_read_isolation.py -q -p no:cacheprovider
(cd website/apps/dashboard && node --test src/captureStatus.test.js src/harnesses.test.js)
```

The census probe exits `3` when a premise could not be measured — fail closed. A premise
counted as falsified is **data**, not a probe failure: after a fix, the same row must flip.
Each row measures exactly the clause it prints: a two-clause premise is read clause by clause
(the org-create init fragment for "auto-satisfied", `_NON_AGENT_STEPS` for "never an agent
act"), and "every completion gate" is the three named gates `_GATE_SELF` / `_GATE_BUILD` /
`_GATE_COMPACT` — `state._GATES` holds only the first two, so reading it would answer a
question about the compact gate it never asked.
