---
title: "#9664 capstone — the ChatGPT onboarding journey: the measurable half, and three steps corrected"
type: evidence
domain: platform
doc_status: draft
created: 2026-10-22
subjects.team: epistemic-team
aboutSubjects: Tortoise onboarding, MCP connector, ChatGPT
aboutObjects: "#9664, #9340, #2208, tortoise-onboarding, website/docs.html, app.premiselabs.co/skills"
---

# #9664 — capstone acceptance: the ChatGPT onboarding journey

**Status: the run has NOT been executed. This directory holds the half of it that an
agent can measure, and it records three of the ten steps as WRONG-AS-WRITTEN.**

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
| `02-step-premise-census.log` | `probes/step_premise_census.py`: the MCP tool names the steps name, the canonical onboarding gates, and the RFC 8707 resource forms a connector may declare. |
| `03-onboarding-closure-tests.log` | steps 5/8/9's server-side closure contract, MEASURED by running its pins (`tests/test_onboarding_auto_complete.py`, 42 passed). |
| `04-graph-scoping-tests.log` | step 7 — `tortoise_list_graphs` returns only the entitled graph (`tests/test_cross_tenant_read_isolation.py`, 9 passed, seeded present/absent pairs + negative controls). |
| `05-dashboard-copy-tests.log` | steps 3/10's copy constants (`node --test` over the pure modules; no `node_modules` needed, 38 passed). |

Measured at `git HEAD 653980445f` (2026-10-22), with the census (`02`) re-run at
`88358d152` after its rows were made to measure the clause they print; deployed
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
| 2 | create graph; Settings → Configured packs, narrow → the chosen set persists; `configured` reflects narrowing | NOT-COVERED-HERE | premise STATIC: `GET/PUT /v1/graphs/{graph_id}/packs` returns `configured`, and `false` means *never explicitly configured → legacy catalog union*, not *no packs* (`website/apps/dashboard/src/packs.js:1-30`). Persistence needs a browser. |
| 3 | Setup-guide card → choose ChatGPT → the card's steps, as rendered | **WRONG-AS-WRITTEN** | ChatGPT has **no dashboard surface at all**: `#2698` deleted its tab, `#2912`'s families exclude it, and the source says so in terms — *"chatgpt … has NO dashboard surface: … Its live carrier is the public setup docs page (#4836)"* (`harnesses.js:865-867`). MEASURED: the served door is `https://tortoise.premiselabs.co/docs#chatgpt` (HTTP 200, section present, `01` log). |
| 4 | ChatGPT: add custom MCP server = `CANONICAL_MCP_URL` → OAuth; consent shows the right workspace; token issued | NOT-COVERED-HERE | premises MEASURED: PRM + AS metadata are live (DCR `/register`, `/oauth/authorize`, `/oauth/token`, PKCE `S256`, `resource=https://api.premiselabs.co/mcp`), an unauthenticated `POST /mcp` is **challenged** with `resource_metadata` (401, `01` log). The consent chooser is **org**-scoped (`_selectable_orgs`, `oauth.py:845`). The ChatGPT side needs the human Pro auth; the recipe differs from the step — see correction 3. |
| 5 | `connection-written` + `harness-connected` close **at token issue** | **WRONG-AS-WRITTEN** | MEASURED (census rows 5a-5e): `connection-written` is in **no** completion gate and is a CLIENT checkpoint (`#3451`, `_NON_AGENT_STEPS`, `_CHECKPOINT_STEPS`); `harness-connected` is filed by the server on the **first successful agent write** (`#3784`/`#3913`), i.e. at step 8. Nothing closes at token issue. |
| 6 | the agent calls `tortoise_load_skill` | **WRONG-AS-WRITTEN** | MEASURED (census rows 6a-6e): no such tool — `0` of the 81 registered tools contains `skill`, and the name occurs in no pre-existing file in the repo (only in this PR's own files: this evidence set and `tests/test_9664_premise_census.py`). ChatGPT is *skillless by design* (`HARNESS_SKILLLESS`), and the served onboarding document it is handed names the **advertised** `tortoise_overview(section="health")` for verification (the legacy `tortoise_health` it mentions ×11 is retired — census rows 6b/6c) plus `tortoise_onboarding_state` ×6, `tortoise_create_point` ×3, `tortoise_file_decision` ×1, `load_skill` ×0 (`01` log). Those are whole-document **mention** counts, and they are not the instruction this path follows — measured per section: §3b ×4 (`SKILL.md:242, 336, 492, 501`), §4 ×3 (`:600, 607, 617`), **§7 ×1 (`:901`)**, §5 ×0, plus the frontmatter pair and one retirement disclosure (`:641`). The §7 mention is the one a ChatGPT runner still meets: the document's ChatGPT carve-out is the §4 bullet, and §7's decide protocol (`:901`, "Verify the live connection first with `tortoise_health`") is **not** carved out — capstone step 9 runs exactly that protocol, so step 9's trace must be read against the retirement (`#5439` tracks the copy). |
| 7 | `tortoise_list_graphs` returns only entitled graphs | VERIFIED-HERE (contract layer) | `tortoise_list_graphs` filters to `org_{org_id}`/`team_{org_id}` in HTTP mode (`mcp_server.py:3266-3284`, the filter at `:3282-3283`), pinned by 9 tests with a seeded present/absent pair and a negative control per seam (`04` log). Through ChatGPT: NOT COVERED. |
| 8 | file the first points → `first-points-filed` closes | VERIFIED-HERE (server layer) | 42 tests green (`03` log), including the call-site layer (`-k handler_env`) that reds if a point write stops observing the step. Through ChatGPT: NOT COVERED. |
| 9 | run a decision → `decide-completed` closes | VERIFIED-HERE (server layer) | same 42 tests; `decide-completed` is filed only on a decision-shaped write (`tortoise_file_decision`, or a `pointKind` in `DECISION_SHAPED_POINT_KINDS`) — both documented decide protocols. Through ChatGPT: NOT COVERED. |
| 10 | capture disclosure card: capture unavailable for ChatGPT, not silence | VERIFIED-HERE (copy) | `HARNESS_CAPTURE_SUPPORT.chatgpt === false` renders `HARNESS_CAPTURE_REASON.chatgpt` = *"ChatGPT connects from its own cloud — session capture isn't available for it"* (`harnessStatusCard.jsx:120`), pinned by 38 green tests (`05` log). Not silence. |

## The three corrections

**3 — the door is the public docs page, not the Setup-guide card.** ChatGPT is key-less
OAuth with no dashboard surface; the steps live at `website/docs.html#chatgpt` (served at
`https://tortoise.premiselabs.co/docs#chatgpt`). The declared recipe is also different from
the step: **Enable Developer mode** (chatgpt.com → Settings → Security and login) → open
`chatgpt.com/plugins` → **+** → create a Developer-mode app → server URL
`https://api.premiselabs.co/mcp`, no key → **Choose OAuth** → **Scan Tools** → sign in to
Tortoise → **Authorize** → pick the organization. Corrected observation: *"the docs page's
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
path's instruction — measured per section: §3b ×4 (`:242, 336, 492, 501`), §4 ×3
(`:600, 607, 617`), **§7 ×1 (`:901`)**, §5 ×0, plus the frontmatter pair and the one
retirement disclosure (`:641`). The §7 one matters for this run: the document's ChatGPT
carve-out is the §4 bullet, so a ChatGPT agent reaching §7's decide protocol is still told
"verify the live connection first with `tortoise_health`" — which is **step 9** of this
capstone, and that name is retired. Read step 9's trace against the retirement
(`tortoise_overview(section="health")`), not as a broken product; the copy itself is
`#5439`'s. Corrected observation: *"the tool-call trace begins
`tortoise_overview(section="health")` → `tortoise_onboarding_state` → `tortoise_create_point`,
`tortoise_health` never appears in the grant because it is retired, and `tortoise_load_skill`
is never called because it does not exist."*

## Blocker on the "deliberate ugly cases"

The four ugly cases (#9340) — an explicit non-default graph, a duplicate graph **name**, a
graph id from another workspace, a soft-deleted graph — have **no addressable form on the
connector today**. MEASURED (census rows `4+ugly`): `parse_resource` accepts only the bare
MCP resource, the origin root, and `…/mcp/organizations/<org_id>`; a graph-scoped resource
is `REFUSED(400 invalid_resource)` (`oauth.py:812-840`), and the access token carries no
`graph_id`, so every connector lands on the workspace's default graph. Those cases are
therefore **BLOCKED on #9340**, not "not covered": they cannot pass, and a capstone closed
on them would be recording a capability we did not ship.

## The explicit uncovered list (what the run still owes)

1. Step 1 — sign-up, workspace naming, `team-named` closing (browser).
2. Step 2 — narrowing packs and the persisted `configured` flag (browser + tenant).
3. Steps 4/6/7/8/9 **through ChatGPT** — the tool-call trace as the model actually emits it,
   the OAuth consent as ChatGPT renders it. Needs a ChatGPT Plus/Pro account and a human to
   complete the sign-in/authorize (`#9664` step 4 says so explicitly).
4. Step 10 — the card **as rendered** (the copy is pinned by test; the rendering is not).
5. The four ugly cases — BLOCKED on #9340.

Screenshots and the model's tool-call trace remain outstanding; nothing in this directory
substitutes for them.

## Re-running the measurable half

```bash
uv sync --extra embeddings --extra parity
.venv/bin/python docs/evidence/9664-capstone/probes/step_premise_census.py
uri="$(.venv/bin/python tools/test_lane.py uri)"; eval "$uri"       # a private container — never the canonical store
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
