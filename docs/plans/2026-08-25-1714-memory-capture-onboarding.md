---
title: "#1714 Memory-Capture Onboarding — epic plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

<!-- research-path: docs/research/1714-solution-converge.md -->

# Memory-Capture Onboarding — epic #1714

> **This document is the EPIC.** It holds the overall plan, the requirements, the tests/E2E plan, and the
> decomposition (which issues exist, what each owns, how they depend on each other).
> **Every system-level detail — clock sources and skew, cursor-advance semantics, byte boundaries, which
> function writes which field, writer inventories and field-parity lists, file-path-level notes, test-fixture
> specifics, and any open question that is a property of one system — lives in the child issue that owns that
> system.** To implement something, read its issue; this document tells you which issue that is and why.
>
> Superseded text is **deleted**, not annotated. Where an earlier revision said something different, the text
> here is what governs.

**Goal.** Give every Tortoise onboarding — the hosted wizard and the self-hosted prompt — a working
memory-capture choice: GitHub issues/documents and agent sessions captured into the graph, lifecycle-aware,
entity-linked and non-polluting; plus a Data-sources screen that presents **every** integration (shipped and
planned) as one standard grid, where a click on an unshipped tile is the demand signal the operator acts on.

**Team:** epistemic-team · **Role:** product-implementer · **Level:** epic · **Complexity:** complex

**Decisions this epic rests on (settled — do not re-litigate, do not re-derive).**

1. **Recording is a setting, not a consent ritual.** Legal basis is contract + notice (T&C / privacy policy).
   Recording is default-on; the off-switch is kept as good UX; the capture gate is an **opt-out returning 409**
   — the pre-#1927 **403 consent gate is retired and is not to be re-implemented**, and no consent record is written.
2. **The install runs in the agent prompt** (lowest friction — the agent is already in the environment where the
   install happens). The dashboard owns **status + the off-switch**, never an install recipe on a web surface.
3. **Unshipped integrations are visible, muted and clickable** — the click **is** the demand signal. Never styled
   as available; the tile carries a full-contrast surface + a "Coming soon" badge, so muting is a **label**, not
   reduced contrast.
4. **Agent-settable first.** Thin UI, capable backend, agent-addressable (R6).
5. **Ship-notification is not machinery.** A click is **recorded**; the operator emails interested teams by hand
   (R2). Nothing in this epic delivers a notification on ship.

---

## 1. Requirements

**R1 — The Data-sources screen is the standard integrations grid.**
One grid of tiles, each a **logo + name**; shipped integrations first, unshipped ones **lower in the same grid,
visually muted and badged, still clickable**. No bespoke wizard step layout, no novel toggle list, no custom
presentation. Cite the standard pattern, or state plainly that it does not exist.

> **Repo finding — verified against the tree 2026-09-15, and it is a plain statement: THE STANDARD
> INTEGRATIONS GRID DOES NOT EXIST IN THIS REPO.** `website/apps/dashboard/src/` contains no integrations grid,
> no logo-tile component, and no integration logo assets; "integration" appears in that tree only in comments
> and in embedded install-config text (`main.jsx:1144`, `:7627`, `harnesses.js:223`, `:461`). The reusable
> primitives that **do** exist are `.plans-grid` / `.plan-card` (`index.css` — the page-level plan-card grid,
> `grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))`), the generic card grid `.cards` / `.card`
> (`index.css:86-87`), and the `.harness-surfaces` button-tile group (`index.css:647-683` — flex-wrapped `<button>` tiles with a name
> and a hint). **The grid is therefore built** on those existing grid and tile primitives, and the owning issue (#3513) owns adding
> the logo marks for each integration, with a monogram fallback so a missing asset cannot render a broken tile.

**R2 — The click is RECORDED, not DELIVERED.**
Clicking an unshipped integration records **which integration was clicked, by which team**, in a **queryable**
store, so the operator can email the interested teams when the integration ships. **Notification is a manual
operator action outside the product.** Concretely, this epic contains: no ship-time delivery task, mechanism or
channel; no owner-scoped ship-notice record; no per-item Requested/Planned/Shipped status tracking or surface;
no per-user notify channel; and no "close the loop" acceptance that depends on an automated delivery path.

**R3 — Each click also pings the operator's Telegram** (recorded per click, customer-confirmed; the **send** is
deduped and rate-capped per §5 J4 — N clicks ⇒ one send). The payload stays
aggregate-ready (integration key + team/org + timestamp + action) so a digest can be layered on later. The ping
must never block or fail the user's request.

**R4 — "Notify me" survives only as a LABEL.**
The popup's primary affordance may read "Notify me" to sharpen the intent signal, but it creates **no delivery
obligation, no status record and no extra state**. If it complicates the popup, drop it — the click alone is the
signal (R2).

**R5 — Capture is a setting with an honest off-switch.**
Recording is default-on. The opt-out returns **409**, writes nothing, and leaves already-captured sessions
intact; re-enabling restores capture. The wizard and the self-hosted prompt write the **same** state keys.

**R6 — Integrations are agent-settable (design principle: thin UI, capable backend, agent-addressable).**
Proven by **J9** in §5.
For **every integration this epic supports** — the sources that already exist: **GitHub issues**, **GitHub
documents**, **agent-session recording** — **an agent can enable/connect it through the tool surface** (MCP tool /
API), so the UI never needs a bespoke per-integration flow. The grid shows what exists and its state; the wiring is
done by the backend and can be driven by an agent. **Precision:** GitHub issues and GitHub documents share **one
OAuth connect** but are **separately enable-able sources** (distinct index jobs and state keys), so they count as
three sources with two connect surfaces; where the enable path for documents differs, #3540 records it as the gap.
**Scope bound (do not let this balloon):** this applies to the integrations that **already exist**. It does
**not** mean building Google Drive / Slack / Notion connectors in this epic — those stay unbuilt "coming soon"
tiles. Connector/backend work is a **future** workstream, named as a follow-up issue. Where an agent-settable
path is missing for an existing source, it is recorded as a gap in the owning issue.

**R7 — The ask is mechanism-gated and honest.**
No promise without a mechanism. A harness with no install path renders **disabled-with-reason**, never a dead
control. Copy names what is actually captured and where it goes. The disclosure is generated from runtime
structured data via a **non-LLM template** — a wrong disclosure is a UX bug.

**R8 — Capture is reliable across death modes.**
Exactly one Session per logical conversation; at-least-once delivery, idempotent server-side, resumable after
kill/crash. The verification is **falsifiable**: with capture disabled it MUST fail, and a store-sync-only run
MUST report the hook as not live.

**R9 — The wizard does not dead-end.**
`done` is reachable from the new step by both Continue and Skip, and partially-finished setup is **named** on
`done` rather than silently passed.

**R10 — Captured sessions are findable, and their processing state is honest.**
A captured session's Source is searchable (never `index_missing`), and "stored" is distinguishable from
"stored and processed" — including the extraction-failed-but-HTTP-200 case. Neither surface may read processed
while the other shows no error.

**R11 — No silent drops.**
An unregistered onboarding-state key or analytics prop fails with **no error** — it is discarded. "All its
surfaces" is therefore enumerated: a state key must be carried by the **read-time default dict**, the
**derived defaults**, the **provisioning dict stamped at team creation**, the **allowed-keys set**, and the
**PATCH model**; a prop must be in the **analytics allowlist**. Operational/verification keys (probes, receipts,
last-error, cursors, backfill markers, click records) are additionally **server-owned**: the generic PATCH must
**reject** them, because one client PATCH would otherwise forge a capture status or reset an index cursor. **Owner
of the allowed-keys set and that rejection: #3520** (it already owns the server-owned operational keys that join it).

**R12 — The epic is done when the journeys are proven.**
Every journey in §5 passes its proving test — automated where a runner exists, and the ops checks executed and
logged. The negative controls pass. Every deferral is **filed** with an owner and a trigger.

---

## 2. Epic-level architecture (one statement; detail in the issues)

**Two repositories — current state and target.** Today the Pi capture mechanism is **real code in an internal
tooling repo**: the extension at `agent-infra/extensions/tortoise-capture/`, plus a **second overlapping
recorder** (`reflect-hook.ts`) elsewhere in that repo, and it reaches dev machines through a **symlink** from
`~/.pi/agent/extensions/`. It is versioned, tested and released **outside the product**, so a capture regression
is invisible to the product's gates. **Target: product-owned** (`tortoise/pi-extension/`, mirroring the shipped
Claude Code hooks in `tortoise/claude-hooks/`), versioned and tested in the product's CI; `agent-infra/extensions/`
then becomes a **symlink-only dev shim** carrying no capture code. The move is not a rename: #3512 owns
**scaffolding that directory and porting the extension's test suite into the product's CI** — a home without a
gate fixes nothing.

**One capture path, one shared primitive — REQUIRED, not current.** The two server-side session writers
(`_capture_session_impl` and `TortoiseSDK.capture_session`) keep **separate** Session field lists today, and they
have **already drifted** (`machine_id` and `model` are written by the hosted path and not by the SDK path —
verified in the tree). The design target is that both call **one** shared turn/Session write primitive, so the
field list exists once; until it lands the drift is a live defect, and the retrofit audit for sessions already
captured through the SDK path is owned by #3520. Capture is **hybrid** by design: an in-process hook for
promptness + attribution, plus a **store-sync backstop** reading Pi's own session `.jsonl` — the only mechanism
that exists for a session that died without a shutdown event.

**Two status axes, never conflated.** *Install* is per-harness (`off` → `install-pending` → `waiting` →
`active`, unchanged, four members). *Extraction* is per-session (`stored` / `stored-and-processed` /
`stored-extraction-failed`) and is a **response field**, not team state.

**One demand path.** A click posts to one team-scoped demand route, which records the click and pings the ops
chat. Nothing downstream of it delivers anything.

---

## 3. The overall plan — slices, order, dependencies

Work is organised as six independently shippable slices.

| Slice | What it delivers | Owning issues |
|---|---|---|
| **S0 — Ingestion baseline** | Keyed, Events-as-truth, entity-linked, quota-fair GitHub ingestion; remote GitHub-docs extraction; historical-dedupe decision | #3539 |
| **S1 — Placement + capture architecture** | The Pi mechanism's home decided and consolidated; the hybrid capture architecture pinned | #3512 → #3515 |
| **S2 — Provisioning + install** | Capture provisioned and verified for claude + pi; agent-prompt install; honest unsupported rows | #3516, #3517 |
| **S3 — Data-sources screen + demand capture** | The integrations grid as wizard step 3 (`done` → index 4); click capture + ops ping | #3513 → #3514 |
| **S4 — Agent-settable integrations** | Every existing integration enable/connectable from the tool surface | #3540 |
| **S5 — Session storage → indexed source → extraction** | The session Source becomes searchable; stored vs stored-and-processed is visible | #3520's primitive sub-step → #3518; remainder of #3520 ∥ #3518 |

**Order (pinned).**

```
S0  #3539                              (EPIC-WIDE PREREQUISITE — no S1–S5 implementation
     ║                                  starts until #3539's acceptance passes)
     ║
S1   #3512 ──▶ #3515                       (placement is a PREREQUISITE of the architecture:
     │                                      #3515 changes code whose home #3512 decides)
     ├──────────────┬──────────────┐
     ▼              ▼              ▼
S2  #3516        #3517        (independent lane)
     ▲                             S3   #3513 ──▶ #3514      (needs only #3515's status labels
     └── BOTH #3516 and #3517             and the live wizard — not #3516/#3517)
          gated on #3515's architecture
     ┌──────────────────────────────┈┈┈┘
S4  #3540  needs the grid (#3513) + #3516's install-status vocabulary + the sources' tool surfaces
S5  #3520's shared-primitive sub-step → (#3518 ∥ rest of #3520)   ← LANE C — branches from #3515 alongside
     S2/S3 and runs in parallel with them, independent of S4; input = #3515's durable session records
```

**The S0 edge is an epic-WIDE prerequisite, not a per-issue dependency.** No S1–S5 issue begins implementation
until #3539's acceptance passes, so §4's dependency column does not repeat it on every row. **The two halves of
#3539 are one issue deliberately:** the docs-extraction half is consumed by **#3540** (J9's docs-index tool and
its gate), and both halves ride the same shared ingestion substrate (mapper, Document-aware quota, deploy
config), so splitting them would put one substrate change in two concurrent PRs.

**Three lanes run concurrently** after #3515: (A) **#3516 ∥ #3517**; (B) #3513 → #3514; and (C) #3520's shared-primitive sub-step → **#3518 ∥ the rest of #3520**.
**#3519 is deferred out of this epic** to a dated gate.

**Why this order.** #3512 must precede #3515/#3516/#3517 — they all modify the extension whose home #3512
decides, so implementing first means implementing in the wrong repo. #3513 needed only #3515's status labels
all along; running it after #3516/#3517 was an unnecessary serialisation. #3520's **shared-primitive sub-step
lands first**, because #3518 and #3520's status work both touch `sdk.py` — #3518 rebases on it (see its
dependency cell).

---

## 4. Decomposition — what each issue owns

| Issue | Owns | Depends on | Journeys |
|---|---|---|---|
| **#3539** | GitHub ingestion baseline (shared mapper, cursor-correct fetch+diff, lifecycle writes, quota fairness, auto-index, connector wrappers, dedupe decision) + remote docs extraction (fetcher, staging, `/v1/index/docs`, Document-aware quota, deploy config) | — | J6 |
| **#3512** | The Pi mechanism's home: **scaffolds `tortoise/pi-extension/`**, product-owned, two recorders consolidated into one, **its test suite ported into the product's CI** | — | prerequisite for J2/J5/J8 |
| **#3515** | The capture architecture (hybrid hook + store-sync), the lane marker, the guards, the two-part falsifiable verification | #3512 | J5, J8 |
| **#3516** | Provisioning + verification of capture from onboarding (claude + pi probes, symlinked install, unsupported rows honest); **owns the install-status vocabulary** (`off`/`install-pending`/`waiting`/`active`) that #3513 renders and #3540 reads | #3512, #3515 | J2, J5, J7, J8 |
| **#3517** | Agent-prompt install (a setting, single state path, parity-tested, off-switch named) | #3515 | J3, J8 |
| **#3513** | The Data-sources wizard step: the **integrations grid**, ordering, `done` renumber, modal a11y, self-hosted parity | #3515 (status labels only) | J1, J2 |
| **#3514** | Demand capture: the click record + the ops Telegram ping (nothing is delivered), **including the self-hosted trigger surface** (a prompt question or an MCP tool) or a recorded honest difference | #3513 | J4, J8 |
| **#3540** | Agent-settable enable/connect for the **existing** integrations, through the tool surface | #3513, #3516 | J9 |
| **#3518** | The session `Source` FTS index (captured sessions become findable) | #3515; **#3520's shared-primitive sub-step must land first** (the remainder of #3520 runs in parallel) | J6 |
| **#3520** | Stored vs stored-and-processed: **owns the extraction-status vocabulary**; owns the shared writer primitive, the **five-field parity test** that is the recurrence mechanism for the drift, the **retrofit audit for sessions already written through the SDK path** (missing `machine_id`/`model`), and the stale "never drift" invariant comments that assert a scope the code does not have, the **duplicated per-turn Cypher MERGE** (extract it to the same shared helper), and the declaration of the canonical `:Session` property schema, **and — per R11 — the onboarding-state allowed-keys set and the server-owned PATCH rejection** (its own §F already owns `_ALLOWED_STATE_KEYS` / `_PATCH_SERVER_OWNED_KEYS`, in `hosted_api.py` + `onboarding/state.py`) | #3515 | J2, J6 |
| **#3519** | **DEFERRED, out of this epic** — semantic transcript retrieval | dated gate | — |
| **#3541** | **FOLLOW-UP, out of this epic** — the Drive/Slack/Notion connector + backend workstream | — | — |
| **#3542** | **FOLLOW-UP, out of this epic** — codex session-end capture; the `codex: false` row already ships, and #3516 asserts it stays unsupported while #3542 is open | — | — |

**MECE.** Every journey J1–J9 in §5 has at least one owning issue, and J7 is named in its owner's row (#3516 for
the off-switch and its honest row); **#3520** owns the server-owned key enforcement that makes the 409 gate
un-forgeable. The three overlaps earlier revisions carried
are retired by construction: the **inline-install surface** (the recipe is consumed only by the agent-prompt
issue, #3517; #3513 renders status + a pointer), the **status-row rewriting** (#3520 owns the status surface and
supersedes the static assertions in #3516 and #3513), and the **two status axes** (install stays four members;
extraction is a separate session-scoped vocabulary).
**Declared boundaries, not overlaps:** the demo seeder is a writer deliberately **excluded** from the shared
primitive (it writes sample data, not captured sessions), and the derived-commit producer is covered by the FTS
and status issues but **excluded** from turn embedding (it has no turns).

**Already in the tree — VERIFY, do not rebuild:** the T3 session-filing tool (`tortoise_session_capture`) and
the workflows prompt, the T2 backfill import CLI (`tortoise sessions import`), and the session→entity linking
pass (`tortoise/session_link.py`). None of these is work in this epic; each is asserted by a test where the
owning issue says so, so a regression is caught rather than assumed away. For the linking pass the assertion is
owned by **#3520** (the writer-parity owner) and covers **both** runs: the capture-time pass and the
`_run_indexing` completion re-run that resolves sessions captured before their entities materialised. **One gap
recorded there, not hidden:** `link_session_entities` is called only from the hosted path (`hosted_api.py:8574`,
`:20145`) and never from `sdk.py`, so SDK-captured sessions carry no entity edges — #3520 owns either wiring the
call into the SDK path or recording it as an honest hosted/self-hosted difference, and J6 asserts the epic's
"linked to the subject/project entities" target.

**Pre-existing bugs.** If implementing this epic uncovers a bug in one of those components, it is **fixed inside
the owning issue's workstream** when it blocks that issue's acceptance — the epic's tests must not be green by
avoiding it — and anything genuinely out of scope is filed per the repo's file-a-bug rule and referenced from the
owning issue. "Already in the tree" says who writes it; it is never a licence to skip a failing test.
**One known gap, filed not hidden:** `codex` appears in #1714's original Target but is delivered by nobody —
the Target is amended to **pi + claude**. The `codex: false` row **already ships** (`harnesses.js:51`, "backfill
import only"), so the gap is live today rather than future: the follow-up #3542 must exist **before** that row
flips to supported, and the trip is a named assertion in #3516's harness suite that the `codex` capture row stays
unsupported while #3542 is open — so the flip cannot land silently.

---

## 5. Tests / E2E — how the epic is proven

Layers and runners (verified): dashboard e2e (`RUN_DASHBOARD_E2E=1`, pytest + Playwright); Python integration
against the docker FalkorDB lane; embedded carve-out (`TORTOISE_TEST_CARVE_OUT=1`); dashboard node unit
(`node --test`); **ops check** (no runner — a manual procedure, executed and logged). Fixture specifics and exact
row-by-row acceptance live in the owning issues.

**J1 — First-timer reaches `done` through the Data-sources grid.**
`sign up → provision → org-create → fork → connect → Data-sources grid (index 3) → Continue/Skip → done (index 4)`
→ The grid renders in order; sr-only reads "of 5"; tiles are keyboard/AT reachable; Continue **and** Skip
reach `done`.
*Tests:* `tests/e2e/test_dashboard_onboarding.py::test_data_sources_*` (renders-in-order, continue/skip reach
done index 4, sr-only reads of-5, keyboard/AT reachable, done Back lands on the grid) ·
`test_onboarding_integration.py::test_done_step_server_refresh_fires_on_renumbered_done`

**J2 — Capture provisioning + status.**
`grid → recording row → claude/pi provisioned (agent-prompt path) → probe fires → off → install-pending →
waiting → active → unsupported harnesses disabled-with-reason`
→ A probe moves `install-pending → waiting`, a receipt `→ active`; unsupported harnesses never render a dead
control.
*Tests:* `captureStatus.test.js` · `harnesses.test.js` (set agreement; unsupported reason) ·
`test_session_capture_e2e.py` (**pi probe** — new — status transitions; **hook-dead negative control**) ·
`test_capture_session.py` (store-proven floor + its two controls; lane-marker round-trip; receipt retry) ·
`test_onboarding_endpoints.py` (registration; last-error pair; verification keys **not** client-writable) ·
e2e `test_capture_row_renders_status_not_install_recipe`

**J3 — Agent-prompt install.**
`tortoise/onboarding/SKILL.md §6 prompt → agent asks yes/no → yes: installs + verifies + names the off-switch location → dashboard reflects
the prompt-install before any probe arrives`
→ The prompt answer and the dashboard setting write **identical state keys**; after a yes the harness is not
`install-pending`; the confirmation names the off-switch.
*Tests:* `test_onboarding_session_recording_parity.py` (same keys; prompt-yes not install-pending; prompt-no
installs nothing) · grep/ops gates (the off-switch constant **defined in #3517**; the disclosure emitted from a
non-LLM template; the false-promise grep — **its pattern and file set are defined in #3517**, which owns the gate)

**J4 — Coming-soon click capture.**
`click an unbuilt tile → popup ("Not available yet" + primary + "Go back") → the click is RECORDED per team,
deduped → operator Telegram pinged → done`
→ The record is queryable and names the integration + team; the ping is bounded (N clicks ⇒ one send, pinned
literals); the popup has loading/success/error states and a pinned dialog contract (the contract is pinned in
**#3513**, which owns the modal); a transport failure cannot
fail the request. **No delivery, no status tracking, no ship notification** — see R2.
*Tests:* `test_data_source_capture.py` (prop registered; state key on all surfaces; `org_key` server-derived;
dedupe per `(integration, team)`; rate cap N-clicks-one-send; Telegram mocked + raising transport cannot
propagate; concurrent writes; popup states; self-hosted local-JSONL degradation; Telegram skipped when env
unset) · e2e (modal dialog contract; popup states; a tile behind "Show more" still captures; tile
keyboard + aria-label) · `test_notify_me_label_creates_no_extra_state` (R4, a **negative assertion**, binding
**only when the label ships** — R4 permits dropping it entirely: clicking the affordance writes **only**
the base click record — a status record, a notify-intent, or any extra state makes it fail) ·
ops `ops-telegram-live-send`

**J5 — Capture reliability across death modes (the core ask).**
`session runs → ends by normal exit / Ctrl-C / kill -9 / terminal close / offline / fork-resume → captured with
no loss and no duplicate → recovery sweep drains spool + unsynced files`
→ One Session per logical conversation; **with capture disabled the verification MUST FAIL**; a hook-dead run is
reported hook-not-live; malformed/truncated/non-UTF8/oversized and cursor-past-EOF are included.
*Tests:* `test_store_sync_worker.py` — one row per death mode and per guard (normal exit, SIGINT, SIGKILL
recovery, cursor-resume after transport 500, restart multiplication, fork/resume, sweep drains both substrates,
sweep-vs-hook barrier, claim release/expiry, lane marker distinguishes lanes, hook-dead negative control,
malformed tail/non-UTF8/oversized, cursor-past-EOF, file replaced, backoff exhaustion retains the batch,
permanent-4xx stall, sweep rate + spool cap, clock-skew tolerance, lock-failure paths, cursor-not-advanced on
500, non-UTF8 mid-stream) · ops `ops-kill9-live-pi-session`, `ops-terminal-close-capture`, `ops-pi-hosted-2xx-leg`
· process-level rows are **spawned** via the `tortoise store-sync` CLI or demoted to ops checks; a node-level
`store-sync.test.mjs` covers the pure logic; the backfill import CLI is covered by the existing
`test_session_import_codex.py` / `test_session_import_desktop.py`

**J6 — The pipeline: captured session → Source → extraction.**
`capture → Source node created → findable via FTS (not index_missing) → extraction runs → entities + Points +
Events + IMPL/NAND operator edges`
→ A captured session's Source **hits** on search; the capture and indexer paths populate the **same** field list;
stored is distinguishable from stored-and-processed including the extraction-failed-but-200 case, with
status × last_error in agreement.
*Tests:* `test_source_fts.py` (findable; capture/indexer field parity; index_missing guard) ·
`test_session_capture_status.py` (extraction-failed-but-200; status × last_error; extraction set agreement;
five-field writer parity; new columns appended at the end) · `test_capture_session.py` (full ontology shape;
commit-created renders honestly) · `capturedSessions.test.js`

**J7 — Opt-out.**
`recording on → user flips the off-switch → new capture BLOCKED with 409 + honest copy → already-captured
sessions remain → re-enable works`
*Tests:* `test_capture_session.py` (409 not 403; opt-out writes nothing; existing sessions survive; re-enable
restores) · `test_onboarding_endpoints.py::test_q3_decline_then_reenable_consents` · `captureStatus.test.js`
(off wins when recording unset)

**J8 — Self-hosted variant of every journey above.**
`J1 without a dashboard (SKILL.md step) · J2 via stdio + probe to the configured API URL · J3 via the SKILL.md §6 prompt
+ MCP tool · J4 → local JSONL, Telegram skipped when the env is unset · J5 identical (hook + store-sync are
local) · J6 identical (the graph is local) · J7 via the recording tool / re-answering the prompt`
*Tests:* `test_data_source_capture.py::test_selfhosted_*`, `test_session_capture_e2e.py`
(stdio honest error) · ops `ops-selfhosted-parity-run`

**J9 — Agent-settable enable/connect.**
`agent calls the tool surface to enable an existing integration → the state read reflects it → the grid renders
the same state`
*Tests:* the parametrized `test_existing_integrations_agent_settable` (every existing integration has an enable
path **and** a state read), `test_docs_index_tool_registered` / `_enqueues_job` / `_gates_honored`, and
`test_tool_and_dashboard_enable_write_same_keys` — all in #3540.

**Epic gate.** Full docker lane + carve-out + dashboard e2e + node unit suites green, `dist/` rebuilt and
committed, false-promise grep clean — ops check `ops-release-full-suite`.

**Traceability — a journey with no test is visible here.** J1 new · J2 partial (claude probe exists; pi emitter,
negative control, floor, lane marker new) · J3 new · J4 new · J5 new (the worker test file does not exist) ·
J6 partial (`test_capture_session.py` exists; FTS + status files new) · J7 partial (the 409 gate exists; the
journey assertions are new) · J8 partial · J9 new. **No journey yet has a complete automated proving test** — that
is the honest state, and it is why the epic is not done until §5 is green.

---

## 6. Deferrals, follow-ups and open decisions

**Filed with an owner + trigger (not intentions).**

- **#3519 — semantic transcript retrieval.** Deferred: turn-Point embedding is a retrieval-quality capability the
  stated goal does not require, and it cuts against "let's not boil the ocean". Good alternative: embed inside
  the shared write primitive with a pending marker and a re-embed path — cost: one task plus an embedding
  dependency on the capture write path. **Trigger: the dated gate 2026-12-14**, carried and re-baselined in #3519
  itself (to 90 days after the epic ships if that is later) — the date is the trigger, not a complaint. Owner:
  epistemic-team.
- **#3541 — the Drive/Slack/Notion connector + backend workstream** — out of scope here; the unbuilt tiles stay
  unbuilt. Owner: epistemic-team.
- **#3542 — codex session-end capture** — #1714's Target is amended to pi + claude. The `codex: false` row
  already ships; #3542 must exist before it flips to supported, and the trip is #3516's named assertion that the
  row stays unsupported while #3542 is open. Owner: the #3516 workstream.
- **Agent-infra symlink-shim deletion** — the shim is **kept** (a symlink carrying no capture code of its own);
  #3512's pinned decision records deletion as **explicitly NOT in scope for #3512** — it is a follow-up once the
  product artifact ships as the install source. **Owner: the Slice-2 implementer workstream; trigger: the first
  product release that includes the product-owned extension.**
- **The research brief** still presents the withdrawn 403 consent contract; annotate it before the next research
  run reuses it. Owner: epistemic-team.

**Open decisions (escalated, not invented).**

1. **Whether CLI backfill turns with no client timestamp are admissible at all.** Admitting them weakens the
   store-proven floor; refusing them may reject legitimate legacy imports. Current position: admitted, recorded,
   floor disabled for that turn.
2. **The "delete captured sessions" path** promised in the copy must be **verified against the code**, or filed
   as an explicit dependency issue.
3. **Splitting this epic** — the continuation cycles returned 34 → 39 → 30 findings with new surfaces still being
   reached; the proportional review has since exited CLEAN (§7), so the open question is whether to split the epic
   for execution — not whether to keep cycling. A judgement for the human.

---

## 7. Review record

- The plan body and its five review cycles were clean at **v2.3.0** (2026-08-25 plan). That signature described
  **that document only** and is not carried forward.
- The amendment line (§v3.2 → v3.7) ran **three continuation cycles** of fresh reviewers. The last returned
  **30 findings** (2 P0-class) with **no clean verdict**; no `plan-review` signature was written for v3.7.
- Its findings were **not** abandoned: every system-level pin moved down into the owning issue (see §4), the
  system-level detail was removed from this document, and the two P0-class items it raised are closed here as
  decisions — **the ship-notify transport question is resolved by R2** (the record replaces the push; the
  operator emails manually), and **the client-clock source is pinned per lane in #3515/#3516**.
- **This revision** (epic restructure) is reviewed by **fresh proportional reviewers** each cycle at the tier
  stated below; the signature is written only if every proportional reviewer returns a clean verdict.
- **Tier: Low-Medium → 2 proportional reviewers** (Structural & Efficiency, Integration) **+ the advisory
  Duplication & Architecture reviewer.** The restructured document is small and holds no novel architecture —
  that detail lives in the issues — so the High-tier budget it once carried is no longer justified; the advisory
  reviewer runs because the epic declares a new component, a new write path and new shared vocabulary. Its
  registry source was unavailable, so its duplication coverage is repo-search only.
- **Cycle 1 — NOT CLEAN** (9 findings, one P0-class factual error). Fixed: the false claim that the internal copy
  carries no capture code (it is real code today; the shim is the *target*, and #3512 now owns scaffolding the
  product directory and porting its test suite into the product CI); the §3/§4 incoherence on the S0 edge (now an
  epic-wide prerequisite); the unowned install-status vocabulary (→ #3516); the unowned retrofit audit and the
  stale "never drift" invariant comments (→ #3520); R11's unstated registration surfaces (now enumerated); the
  unfiled codex follow-up (→ #3542); the epic describing the shared primitive as existing (now stated as
  required); and the unproven claim that the backfill import CLI is tested.
- **Cycle 2 — NOT CLEAN** (6 findings, 2 P1). Fixed: R6 had **no proving journey** (now **J9**, with its tests);
  the §3 text asserted a #3520-sub-step-before-#3518 ordering that §4 did not encode (now in #3518's dependency
  cell); the lane notation was corrected to `#3516 ∥ #3517`; the §3 diagram arrows were re-drawn so S4's real
  dependency on #3516 and S5's input (#3515) are visible; "second overlapping recorder" was imprecise (it is
  `reflect-hook.ts`, elsewhere in the internal repo); `tortoise/pi-extension/` scaffolding was unowned (→ #3512);
  and the drift's **recurrence mechanism** was unnamed (→ the five-field parity test in #3520). **One advisory
  finding was verified and REJECTED:** the claim that `tortoise sessions import` does not exist is false — the
  CLI is dispatched from `__main__.py`, and its parsers and tests are present.
- **Cycle 3 — NOT CLEAN** (10 findings; 2 P0-class, 3 advisory P1). Fixed: the parallel label on S5 contradicted
  the stated sub-step order (the diagram and #3518's dependency cell now encode *primitive sub-step, then
  parallel*); J7 was absent from every issue's Journeys column (now named in #3516's row); the agent-infra symlink-shim
  deletion had no owner (now recorded in #3512's pinned decision as out of scope for #3512 — owned by the
  Slice-2 implementer workstream, triggered by the first product release containing the product-owned
  extension); R11's allowed-keys enforcement was unowned (now #3520); the false-promise
  grep and the modal dialog contract were named as gates with no definition (now attributed to #3517 and #3513);
  R6's "three integrations" was imprecise about the shared GitHub connect; the notify-me check was mislabelled a
  negative control (it is a negative assertion, reworded); and the advisory reviewer found the **duplicated
  per-turn Cypher MERGE** and the **undeclared `:Session` schema** (both now owned by #3520). **Two P0-class
  findings were verified and REJECTED as false:** the claim that no `session_recording` / 409 opt-out gate exists
  (it does, at the capture route in `hosted_api.py`), and the claim that the `sessions import` CLI does not exist
  (it is dispatched from `__main__.py`, and its parsers are a package, not a single file).
- **Correction (2026-09-15 settlement).** The cycle entries above report **aggregate finding counts that mixed the
  proportional reviewers with the advisory reviewer**, so the non-convergence they describe was never established
  for the proportional gate. Per the skill, the Duplication & Architecture reviewer is **advisory** — its findings
  are dispositioned separately, must not enter the fix phase, and **do not affect convergence**. The two
  proportional reviewers' own verdicts could not be recovered from those logs, so they were re-established by
  re-dispatching them fresh on the current text (cycles 4–9).
- **Cycle 4 — NOT CLEAN** (S&E 2 P2; Integration 3, of which **2 verified false** — this document contains no
  "Phase 7" and no "linked N of M", so those premises did not exist). Fixed: §4 and §6 said the `codex` follow-up
  must exist *before the `codex: false` row ships* — that row **already ships** (`harnesses.js:51`), so the text
  now states the live gap and names #3516's assertion as the trip; the two halves of #3539 are now justified in §3
  (the docs half is consumed by #3540, and both ride one ingestion substrate).
- **Cycle 5 — S&E 2, one REJECTED** (#3519's own body carries the dated re-check **and** a re-baseline clause, so
  the deferral has a real mechanism — the plan now states the re-baseline). Fixed: the §3 lane prose said
  "#3518 ∥ #3520" while the diagram and #3518's dependency cell put the shared-primitive sub-step first; the prose
  now matches. Integration returned **no verdict** (tool wedge) — an unavailable source, recorded as such, not a pass.
- **Cycle 6 — NOT CLEAN** (S&E clean; Integration 2). Fixed: #3514's row now owns the **self-hosted demand
  trigger** (or a recorded honest difference); the R4 notify-me negative assertion is marked as binding only when
  the label ships.
- **Cycle 7 — NOT CLEAN** (Integration clean; S&E 2 P2). Fixed: the S5 diagram line now marks it as **lane C**,
  branching from #3515 in parallel with S2/S3 and independent of S4; #3520's row records that it owns the
  onboarding-state allowed-keys set and the server-owned PATCH rejection (its own §F already owns
  `_ALLOWED_STATE_KEYS` / `_PATCH_SERVER_OWNED_KEYS`).
- **Cycle 8 — NOT CLEAN** (S&E 1; Integration 1). Fixed: the cycle-4 fix had mis-assigned the session→entity
  linking assertion to **#3518** (Source FTS only) — ownership is corrected to **#3520**, and the newly evidenced
  gap is recorded in the plan and in #3520: `link_session_entities` is called **only** from the hosted path
  (`hosted_api.py:8574`/`:20145`) and never from `sdk.py`, so SDK-captured sessions carry no entity edges.
  #3518's body was also updated to carry the shared-primitive dependency the plan already stated.
- **Cycle 9 — CLEAN.** Structural & Efficiency and Integration **both returned `NO ISSUES FOUND`**. The advisory
  reviewer returned `NO ISSUES FOUND — CLEAN`; its cycle-7 finding (the wider `:Session` writer set has no
  epic-level closing gate) carried the verdict `unify-contract-keep-drivers` and is dispositioned to #3520 —
  advisory, so it neither blocked the cycle nor the exit. Its component registry was empty, so its coverage is
  repo-search only: a recorded caveat, not a block.
- **Exit: CLEAN at the proportional gate.** Cycle 1 found issues, so re-review cycles ran; the cycle log is this
  section; the advisory reviewer was parsed against its **full** token and dispositioned. No proportional finding
  remains open. The `plan-review` signature line at the bottom of this document was written at cycle 9, on this
  clean verdict.

<!-- plan-review: cycles=9, status=clean, version=2.3.0 -->
<!-- plan-review advisory (#5 Duplication & Architecture): dispositioned separately and NOT part of convergence —
     cycle 7 `ISSUES:` with verdict `unify-contract-keep-drivers` (the wider `:Session` writer set has no
     epic-level closing gate → owned by #3520); cycle 9 `NO ISSUES FOUND — CLEAN` (component registry empty, so
     its duplication coverage is repo-search only — a recorded caveat, not a block). -->
