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
> decomposition (which issues exist, what each owns, how they depend on each other). **Every system-level detail**
> — clock sources and skew, cursor-advance semantics, byte boundaries, which function writes which field, writer
> inventories and field-parity lists, file-path notes, test-fixture specifics, and any open question belonging to
> one system — **lives in the child issue that owns that system.** To implement something, read its issue; this
> document says which issue that is and why. The writing-plans standard-plan header blocks are consequently not
> reproduced here: research is `docs/research/1714-solution-converge.md`, the integration surfaces per issue are
> named in §4, and §5 **is** the journey test map. Superseded text is **deleted**, not annotated.

**Goal.** Give every Tortoise onboarding — hosted wizard and self-hosted prompt — a working memory-capture choice:
GitHub issues/documents and agent sessions captured into the graph, lifecycle-aware, entity-linked and
non-polluting; plus a Data-sources screen presenting **every** integration (shipped and planned) as one standard
grid, where a click on an unshipped tile is the demand signal the operator acts on.

**Team:** epistemic-team · **Role:** product-implementer · **Level:** epic · **Complexity:** complex

**Decisions this epic rests on (settled — do not re-litigate, do not re-derive).**

1. **Recording is a setting, not a consent ritual.** Legal basis is contract + notice (T&C / privacy policy).
   Recording is default-on; the off-switch is kept as good UX; the capture gate is an **opt-out returning 409**
   — the pre-#1927 **403 consent gate is retired and is not to be re-implemented**, and no consent record is written.
2. **The install runs in the agent prompt** (lowest friction — the agent is already in the install environment). The
   dashboard owns **status + the off-switch**: the **wizard** mount never carries the recipe, while the **Settings**
   mount may carry a collapsed manual-install disclosure, a Settings-first user having no other path to it.
3. **Unshipped integrations are visible, badged and clickable at FULL contrast** — the click **is** the demand
   signal. Never styled as available; the tile carries a full-contrast surface + a "Coming soon" badge, is
   **never opacity-muted and never disabled**, so muting is a **label**, not reduced contrast.
4. **Agent-settable first.** Thin UI, capable backend, agent-addressable (R6).
5. **Ship-notification is not machinery.** A click is **recorded**; the operator emails interested teams by hand
   (R2). Nothing in this epic delivers a notification on ship.

---

## 1. Requirements

**R1 — The Data-sources screen is the standard integrations grid.**
One grid of tiles, each a **logo + name**; shipped integrations first, unshipped ones **lower in the same grid at FULL
contrast with a "Coming soon" badge — never opacity-muted, never disabled, and clickable: surface + badge + click is the
demand signal.** An **unsupported** harness is a different class: its reason text is always visible at full contrast and
it carries **no activation control at all** — no native `disabled`, no focusable no-op — so the two unavailability
classes are never confused. No bespoke layout, no novel toggle list. The grid's **read model is named in #3513** (a
server route or a client constant), and the loading/fetch-error/retry states exist only if that source is remote — #3513
pins which, and the e2e asserts the branch that exists. **The grid is not a lossy replacement:** `MemorySources` is also
the only writer of the GitHub source-scope keys and the only trigger for the re-index / index-docs jobs
(`github_issues_scope`, `github_docs_scope`, the per-repo branch selects, and the job status lines with their
starting/completed/failed/expired/quota-partial states), so #3513 carries those surfaces into the tile's sibling panel
rather than dropping them.

> **Repo finding — verified against the tree 2026-09-15.** **(a) THE STANDARD INTEGRATIONS GRID DOES NOT EXIST IN THIS
> REPO:** `website/apps/dashboard/src/` holds no integrations grid, no logo-tile component and no integration logo
> assets; "integration" appears only in comments and embedded install-config text (`main.jsx:1144`, `:7627`,
> `harnesses.js:223`, `:461`). **(b) A LIVE TOGGLE LIST DOES EXIST** — `MemorySources` (`main.jsx:8738`), mounted at
> Settings → Memory sources (`main.jsx:461`) and in the **archived** wizard block (`:6984`). The **Settings** mount is
> replaced by the grid; the **wizard** step is a **net-new** mount, and the archived block is left untouched so the
> rollback path keeps rendering `MemorySources`. Build on `.plans-grid`/`.plan-card` (`index.css:530-550`) and
> `.cards`/`.card` (`:86-87`) — noting `.settings-home .card` clamps a card at the Settings mount (`:989`); #3513 owns
> the logo marks with a monogram fallback against a broken tile.

**R2 — The click is RECORDED, not DELIVERED.**
Clicking an unshipped integration records **which integration, by which team**, in a **queryable** store, so the operator
can email interested teams at ship time. **The canonical store is the existing append-only, team-indexed
`analytics_events`** (the onboarding funnel-event path), which is also what makes §6's *cross-org* demand count
readable; the click is **not** written into the onboarding-state jsonb, so it does not ride #3553's CAS — the local
JSONL fallback is the self-hosted degradation. **Notification is manual, outside the product:** no ship-time delivery
task or ship-notice record; no Requested/Planned/Shipped tracking; no per-user notify channel; no close-the-loop
acceptance.

**R3 — Each click also pings the operator's Telegram** (recorded per click, customer-confirmed; the **send** is
deduped and rate-capped per §5 J4 — N clicks ⇒ one send), aggregate-ready in payload (integration key + team/org +
timestamp + action) so a digest can layer on later, and never blocking or failing the user's request.

**R4 — "Notify me" survives only as a LABEL.**
The **tile click** records the demand (R2); the popup is a **confirmation** with **no posting primary** — its only
affordance is dismiss/"Go back", which cannot retract the record. An exploratory click counts as demand. A "Notify me"
label may sit on the popup as copy only, creating **no delivery obligation, no status record and no extra state**.

**R5 — Capture is a setting with an honest off-switch.**
Recording is default-on. The opt-out returns **409**, writes nothing, and leaves already-captured sessions
intact; re-enabling restores capture. The wizard and the self-hosted prompt write the **same** state keys.

**R6 — Integrations are agent-settable (design principle: thin UI, capable backend, agent-addressable).**
Proven by **J9** in §5. For **every integration this epic supports** — **GitHub issues**, **GitHub documents**,
**agent-session recording** — **an agent can enable/connect it through the tool surface** (MCP tool / API), so the UI
needs no bespoke per-integration flow. **Precision:** issues and documents share **one OAuth connect** but are
**separately enable-able sources** (distinct index jobs and state keys); where the documents path differs, #3540
records the gap. **Scope bound:** only integrations that **already exist** — Drive / Slack / Notion connectors are a
**future** workstream (filed as follow-up), and a missing agent-settable path is a recorded gap in the owning issue.

**R7 — The ask is mechanism-gated and honest.**
No promise without a mechanism. A harness with no install path renders its **reason text always visible**, carries **no
activation control** (no native `disabled`, no focusable no-op) and is positionally distinct from the "Coming soon" badge
group, so reading order is the only interaction. Copy names what is captured and where it goes; the disclosure comes from
runtime structured data via a **non-LLM template** — a wrong disclosure is a UX bug. **The template, its source of truth
and both render sites (agent prompt + dashboard screen) are #3517's**, with a single-source assertion, and the copy's
navigation target is re-pointed by the rename in #3513.

**R8 — Capture is reliable across death modes.**
Exactly one Session per logical conversation. A forked or cloned session file is **not** a second logical conversation:
its copied prefix must resolve to the **parent's** turns. The present positional key (`{session_id}_t{i}`) **cannot** do
that — a fork carries a new header id, so the current scheme would mint a duplicate turn Point per copied turn — and the
shared Pi reader discards entry ids entirely. The fix is a **turn-record contract carrying per-entry identity** (entry
id + role + content + position) emitted by `session_import.parsers`; **#3551 owns that contract and the parser change
(including its second consumer, the `tortoise sessions import` CLI)**, and **#3515 owns the fork/clone rule built on it**,
including what a fork's **post-fork** turns do. Delivery is at-least-once and idempotent server-side — turn Points
**and** the operator/entity edges, since a lost response must not double them — resumable after kill/crash, and the
verification is **falsifiable**: with capture disabled it MUST fail, and a store-sync-only run MUST report the hook as
not live.

**R9 — The wizard does not dead-end.**
`done` is reachable from the new step by both Continue and Skip, and partially-finished setup — source not connected,
harness install not verified, recording off — is **named** on `done` rather than silently passed.

**R10 — Captured sessions are findable, and their processing state is honest.**
A captured session's Source is searchable (never `index_missing`), and "stored" is distinguishable from "stored and
processed" — including the extraction-failed-but-HTTP-200 case. The two surfaces are the `GET /v1/sessions` and
`GET /v1/sessions/{id}` responses and the dashboard's per-session render against the per-harness install pill;
neither may read processed while the other shows no error, and install `active` may coexist with a per-session
extraction failure. A merely **unknown** outcome (`outcome_known: false`) must not render as stored/processed, and the status region
is a **polite live region**.

**R11 — No silent drops.**
An unregistered onboarding-state key or analytics prop fails with **no error** — it is discarded. "All its surfaces" is
therefore enumerated: a state key must be carried by the **two default dicts** (which must **agree** — a set-equality
assertion, because a key landing in only one is invisible to today's registration test), the **allowed-keys set**
(derived from their **union**), the **PATCH model** and **`_PATCH_FIELD_TO_STATE_KEY`** (hyphenated keys reach the PATCH
surface only through it); a prop must be in the **analytics allowlist**, whose additions #3514 owns. The PATCH surface
partitions into exactly three classes — **client-writable**, **server-owned (403)** and **agent-step (422)** — every
member of the allowed-keys and state-key PATCH-field sets falling in exactly one; the partition is an invariant asserted
by test, not a hand-re-enumerated list. Its **derivation source is a canonical server-owned vocabulary** (`FLOW_KEYS` ∪
the operational keys ∪ `{state_version}`) — **not** `FLOW_KEYS` + `STEP_IDS`, which cannot express the server-owned class
and misclassifies the one accepted step field. Operational keys (probes, receipts, last-error, cursors, backfill markers,
`state_version`) are **server-owned**: the generic PATCH must **reject** them, because one client PATCH would otherwise
forge a capture status, reset an index cursor, or defeat the state CAS. **Owner: #3552.**

**R12 — The epic is done when the journeys are proven.**
Every journey in §5 passes its proving test — automated where a runner exists, and the ops checks executed and
logged. The negative controls pass. Every deferral is **filed** with an owner and a trigger.

---

## 2. Epic-level architecture (one statement; detail in the issues)

**Two repositories — current state and target.** The Pi capture mechanism is **real code in an internal tooling
repo** (`agent-infra/extensions/tortoise-capture/` plus a **second overlapping recorder**, `reflect-hook.ts`),
symlinked onto dev machines and released outside the product, so a capture regression is invisible to the product's
gates. **Target: product-owned** (`tortoise/pi-extension/`, mirroring `tortoise/claude-hooks/`) and tested in the
product's CI, with `agent-infra/extensions/` left a **symlink-only dev shim**, kept permanently. #3512 owns
**scaffolding that directory and porting its test suite into CI** — a home without a gate fixes nothing.

**One capture path, one shared primitive — REQUIRED, not current.** The `:Session` MERGE text is **not** owned in one
place: the capture pair (`_capture_session_impl`, `TortoiseSDK.capture_session`) keep **separate** field lists and have
**already drifted** (`machine_id`/`model` hosted-only), the two **commit receivers**, the abandoned-marker and
receipt/reconciler writers, the entity-link writers, the demo seeder and the `longmem` ingest each write their own subset,
and the new store-sync worker is a further writer — **eight loci, not two**. **#3551** introduces the shared
`_write_session_and_turns` primitive and the single `:Session` field-list contract the drivers import, **including the
recording-flag gate at the primitive level** — the gate is called from exactly one site today, while the commit receivers
mint `:Session` ungated. The SDK's `machine_id`/`model` absence stays **knowingly-absent**, not retrofitted (audit-time
stamping would misattribute the machine); #3554 **verifies** the contract rather than declaring a second schema. Capture
is **hybrid**: an in-process hook for promptness + attribution, plus a **store-sync backstop** reading Pi's session
`.jsonl` — the only mechanism for a session that died without a shutdown event.

**Two status axes, never conflated.** *Install* is per-harness (`off` → `install-pending` → `waiting` → `active`,
four members, unchanged). *Extraction* is per-session (`stored` / `stored-and-processed` / `stored-extraction-failed`),
a **response field**, not team state.
**One composition rule — tile, row, badge.** A **tile** is per *integration*; a **row** inside the recording tile is
per *harness* and is the only carrier of install state — the Sessions tile carries no aggregate, only its rows plus a
pointer (the wizard mount names the agent install path, the Settings mount the captured-sessions home). **GitHub is ONE
tile with two separately enable-able sources** (issues and documents share the OAuth connect and each owns its index job
and state keys), so the tile inventory is fixed in #3513 and the grid's ordering, the ≤6-visible bound and the tile count
are derived from it, not re-listed. A **badge** is
the "Coming soon" mark on an unbuilt tile and never encodes a status; a per-harness install value renders as a **state
pill**. The **delivery lane** (`hook` / `store_sync`, on a new property distinct from the existing `capture_extractor`
extraction lane) is monotone. The tile label vocabulary (`Set up` / `Manage` / `Register interest`) is #3513's research
spec. A **tile is a container**: its interactive content — the per-harness rows, the off-switch and the disclosure —
lives in a sibling panel, never inside the tile's own activation control.

---

## 3. The overall plan — slices, order, dependencies

Work is organised as six slices. The **non-obvious cross-lane edges** are named here, so a reader can see the crossings
that the slice order does not imply: **#3518 ← #3516**, **#3540 ← #3513/#3516/#3539**, **#3520 ← #3516**, **#3557 ←
#3520/#3555**, and **#3513 ← #3517** (both edit `SKILL.md`, so #3517's section must land first). Within-slice order is in
the diagram, not repeated here.

| Slice | What it delivers | Owning issues |
|---|---|---|
| **S0 — Ingestion baseline** | Keyed, Events-as-truth, entity-linked, quota-fair GitHub ingestion; remote GitHub-docs extraction; historical-dedupe decision | #3539 |
| **S1 — Placement + capture architecture** | The Pi mechanism's home decided and consolidated; the hybrid capture architecture pinned | #3512 → #3515 |
| **S2 — Provisioning + install** | Capture provisioned and verified for claude + pi; agent-prompt install; honest unsupported rows | #3516, #3517 |
| **S3 — Data-sources screen + demand capture** | The integrations grid as wizard step 3 (`done` → index 4); click capture + ops ping | #3513 → #3514 |
| **S4 — Agent-settable integrations** | Every existing integration enable/connectable from the tool surface | #3540 |
| **S5 — Session storage → indexed source → extraction** | The session Source becomes searchable; stored vs stored-and-processed is visible; the writers share one primitive | #3551 → (#3518 ∥ #3520 ∥ #3552–#3555); #3557 follows #3520 and #3555; #3556 follows #3551 |

**Order (pinned).**

```
S0  #3539                              (ROOT GATE — implementation gate for the shared substrate
     ║                                  (#3540 + docs half); release gate for all other lanes)
     ║
S1   #3512 ──▶ #3515                       (placement PREREQUISITE of the architecture:
     │                                      #3515 changes code whose home #3512 decides)
     ├─▶ S2  #3516 ∥ #3517  (both gated on #3515's architecture; #3520 ◀── #3516, so lane C trails #3516)
     ├─▶ S3  #3513 ──▶ #3514  (needs the live wizard + the existing label constant, not #3516;
     │                         serialised behind #3517's SKILL.md insertion; the click record's
     │                         canonical store is R2's, so #3552/#3553 no longer gate #3514)
     └─▶ S4  #3540  needs the grid (#3513) + #3516's install-status vocabulary + the sources' tool surfaces
S5  #3551 ──▶ #3518 ∥ #3520 ∥ #3552 · #3553 · #3554 · #3555      ← LANE C — branches from #3515 alongside S2/S3
     #3557 ◀── #3520 (read parity), #3555   #3556 (unblocked)       but is NOT parallel with S2
```

**The S0 gate is scoped, not epic-wide.** It is an **implementation** gate for work touching the shared ingestion
substrate (**#3540** and #3539's docs half) and a **release** gate for every other lane: S1–S3 and the S5 lanes open
immediately. #3539 stays one issue — the docs half feeds **#3540**, so #3540 cannot be implemented before it.

**Three lanes run concurrently** after #3515: (A) **#3516 ∥ #3517**; (B) #3513 → #3514; and (C) #3551 →
**#3518 ∥ #3520 ∥ #3552–#3555**, with #3557 following **#3520 and #3555** and #3556 following **#3551**. **#3551 has no
issue dependency** — it consumes #3515's durable record shape and lands first of lane C. Lane A and lane B share two
files (`harnesses.js` copy constants and `SKILL.md`), so they are **not** fully concurrent: **one writer owns each** —
#3517 inserts the SKILL.md install-ask section before #3513's step text, and #3516 owns the harness copy constants while
#3513 consumes them. #3520 and #3518 trail #3516, so lane C trails lane A.

**Why this order.** #3512 must precede #3515/#3516/#3517 — they modify the extension whose home #3512 decides, so
implementing first means implementing in the wrong repo. #3513 needs the live wizard and the existing
`HARNESS_CAPTURE_STATUS_LABEL` constant the recording tile renders — neither a #3516/#3517 deliverable — so
serialising it behind them was unnecessary. **#3551 lands first of all**: #3518, #3520 and #3552–#3555 all re-edit the
capture writers.

---

## 4. Decomposition — what each issue owns

| Issue | Owns | Depends on | Journeys |
|---|---|---|---|
| **#3539** | GitHub ingestion baseline (shared mapper, cursor-correct fetch+diff, partial-batch recovery, rate-limit-safe pagination, lifecycle writes, quota fairness, auto-index, connector wrappers, dedupe decision) + remote docs extraction (fetcher, staging, `/v1/index/docs`, Document-aware quota, deploy config) | — | — (acceptance in #3539) |
| **#3512** | The Pi mechanism's home: **scaffolds `tortoise/pi-extension/`**, product-owned, two recorders consolidated into one, **its test suite ported into the product's CI** | — (S0 is a **release** gate for this lane, not an implementation gate — §3) | prerequisite for J2/J5/J8 |
| **#3515** | The capture architecture (hybrid hook + store-sync, the worker consuming `session_import.parsers`' Pi reader), the guards, the two-part falsifiable verification, the **monotone delivery lane** on a property named distinctly from the existing `capture_extractor` extraction lane, the **409/429-retryable** rule **per record, so one blocked record cannot head-of-line-block the rest of a spool file**, the hook's **per-turn** re-read of the recording flag **resolving the graph override too**, **fail-closed** on a turn-content mismatch, the **cursor-advance rule** (advance only to the last complete newline; a partial tail is never skipped), the **fork/clone rule** built on #3551's turn-record contract, and the ops-check receipts §5 requires | #3512 | J5, J7, J8 |
| **#3516** | Provisioning + verification of capture from onboarding (claude + pi probes, symlinked install, unsupported rows honest, the **set-agreement and `codex: false` tripwire assertions in `harnesses.test.js`** — both **new** — the harness copy constants, and the **client-side enablement** that makes "default-on" true for a user who has not touched `~/.pi/agent/tortoise-config.json`, written **atomically so a racing writer can never silently revert it to `false`**) | #3512, #3515 | J2, J5, J7, J8 |
| **#3517** | Agent-prompt install (a setting, single state path, parity-tested, off-switch named) | #3512, #3515 | J3, J8 |
| **#3513** | The Data-sources wizard step: the **integrations grid** and its **named read model**, the tile inventory + ordering, the grid's loading/fetch-error states (only if the read model is remote), the modal shell + dialog a11y contract (post-action states are #3514's), the **unsupported-row contract** (reason text at full contrast, no activation control), the **sibling panel carrying `MemorySources`' stranded surfaces** (source scope, the re-index / index-docs triggers, the per-repo branch selects, the job status lines), the Settings mount replacing `MemorySources` (**the archived wizard block is untouched**; the wizard mount is net-new), the `done` renumber's **whole surface set — derived, not hand-listed** (`wizardFlow.js` + its test, `wizardStageLabel`'s step-3 arms, the `of N` announce literal, every hard-coded step index and `wizardStep === 3` guard in `main.jsx` including the connect step's three Skip handlers and the done Back, the existing first-timer e2e), and the **stale-navigation rename** (the four user-facing strings that point at the old home, plus the ToS/notice line) | #3515 (the status labels the recording tile renders), #3517 (both edit `SKILL.md`; #3517's section lands first) | J1, J2 |
| **#3514** | Demand capture: the **click record in its canonical store (R2's `analytics_events`)** + its analytics-prop registration + the ops Telegram ping (nothing is delivered), **including the self-hosted trigger surface** (a prompt question or an MCP tool) or a recorded honest difference, the **popup's post-action states and error affordance**, the **dedupe/rate marker written only on a 2xx send**, and the **monthly onboarding-cohort review runnable against the cross-org demand count** | #3513 | J4, J8 |
| **#3540** | Agent-settable enable/connect for the **existing** integrations, through the tool surface — **subject to the same three-class partition as the PATCH surface** (a tool path that could set a server-owned key is the same forgery R11 forbids) | #3513, #3516; **#3539** (S0 implementation gate — §3) | J9 |
| **#3518** | The session `Source` FTS index (captured sessions become findable), and the **backfill decision for the pre-change captured-Source backlog** — no backfill ⇒ the unfindable interval is a recorded honest gap | #3515; **#3551 must land first** (the primitive it indexes); #3516 supplies its end-to-end data | J6 |
| **#3520** | Stored vs stored-and-processed: **owns the extraction-status vocabulary** (`{status, extractor, outcome_known}` on `GET /v1/sessions` + `{id}`, appended at the END), `last_error` agreement on the 2xx path, the dashboard render of the **extraction** vocabulary — a **response field**, never a state key — and the **writer-outcome wording** (`commit_created`/`budget_hold`/`demo_seeded`/`longmem_ingest`), including which of them render and where the demo row is sourced from; the install pill is never driven by a session's extraction status, and the harness-level rollup stays dropped. Its former writer, key-ownership, ontology and count/link items are **#3551–#3555** | #3515, #3551, #3516 | J2, J6 |
| **#3551** | The shared `_write_session_and_turns` primitive — the **`:Session`/turn field-list contract** the drivers import (and the **enumerated writer set**, not "two"), the **turn-record contract carrying per-entry identity** (entry id + role + content + position) with `session_import.parsers` as its single implementation including the `tortoise sessions import` consumer, the turn-id decision for **existing** records, `CONTAINS` wiring, `embed_fn` — plus the **recording-flag check at the primitive level**, so every caller that MERGEs `:Session` inherits the R5/R8 gate (the commit receivers mint `:Session` ungated today), the **writer-parity test over the union of the field lists**, and the stale "never drift" invariant comments; SDK `machine_id`/`model` recorded **knowingly-absent**, not retrofitted | — **(lands first; #3518, #3520, #3552–#3556 all rebase on it)** | prerequisite for J5/J6; J7 |
| **#3552** | Server-ownership of the capture keys: one write path cannot green a capture status, reset an index cursor, or defeat the state CAS (R11) — the PATCH surface's **three classes** are **total and disjoint** over the allowed-keys **and** state-key PATCH-field sets, **derived from a canonical server-owned vocabulary** (not `FLOW_KEYS` + `STEP_IDS`) and asserted behaviourally rather than by enumeration; **adds the set-equality assertion between the two default dicts** and covers **every** write path (PATCH, checkpoint, MCP/tool, agent-step), with the non-state PATCH fields and the one accepted step field as declared carve-outs | #3551 | J2, J7 |
| **#3553** | `state_version` compare-and-set for the non-atomic onboarding-state read-modify-write — **either riding the existing server-owned monotonic `version` on `:OnboardingState`** (already in `FLOW_KEYS`, already PATCH-rejected) **or recorded as a deliberate second counter with a test asserting the two cannot disagree and naming which one a reader trusts** — server-set only, never client-writable either way | #3551 | J2 |
| **#3554** | **Verifies** the canonical `:Session` property schema #3551 declares (**not** a second competing schema) + a re-runnable writer inventory + the `ONTOLOGY.md` `speaker` row correction, recording `capture_ok`'s **knowingly-divergent computation point** (hosted computes it after the verification read, the SDK before it) and the **turn-count shortfall that `capture_ok` does not currently reflect** | #3551 | — |
| **#3555** | The `extracted` count-filter unification + the SDK entity-link gap: **wire `link_session_entities` into the SDK capture path** (the epic's Target is entity-linked capture on one path); divergence is admissible **only** if the in-process entity-resolution dependency is genuinely unavailable, recorded as a gap in #3555 with the reason | #3551 | J6 |
| **#3556** | The server-side `capture_ok` NULL hole + `_capture_abandoned_marker` / `_reconcile_capture_receipts` disagreement with the 2xx contract — **triage before J5 claims no loss** | **#3551** (it edits the same capture handler) | J5 |
| **#3557** | **Add** the SDK `list_sessions` read surface mirroring `GET /v1/sessions` — net-new public API, no SDK method exists today (the tool registry records "(no SDK method)") — plus its export/doc accounting | #3520, #3555 (it mirrors the `extracted` count #3555 unifies) | J8 |
| **#3519** | **DEFERRED, out of this epic** — semantic transcript retrieval | dated gate | — |
| **#3541** | **FOLLOW-UP, out of this epic** — the Drive/Slack/Notion connector + backend workstream | — | — |
| **#3542** | **FOLLOW-UP, out of this epic** — codex session-end capture. The `codex: false` row ships today; the trip is `harnesses.test.js` asserting `HARNESS_CAPTURE_SUPPORT.codex === false` with a comment naming #3542, plus a #3542 acceptance step that flips the constant and that test in the same PR | — | — |

**MECE.** Every journey J1–J9 in §5 has an owning issue; **#3552** owns the key enforcement that makes the 409 gate
un-forgeable. Overlaps earlier revisions carried are retired by construction: the **inline-install surface** (the
*manual-install recipe* is #3517's; the **wizard** mount renders status + a pointer, the **Settings** mount is the one
recorded exception, a Settings-first user having no other path — distinct from the *capture disclosure*, which is #3517's
template rendered at both sites), **status-row rewriting** (#3520 supersedes the static assertions in #3516/#3513), the
**two status axes**, the **fork/clone turn-id rule** (#3551 owns the contract, #3515 the rule), and the **dialog
contract** (shell + a11y #3513; post-action states #3514).
**Declared boundaries, not overlaps:** the demo seeder's `:Session` write is a declared **excluded driver** of the
primitive (sample data), so #3520's `demo_seeded` render must name its own source; the derived-commit producer is
outside turn embedding (no turns) but **not** outside the gate — its `:Session` writes are #3551's; and the click record
lives in `analytics_events` (R2), **not** in the onboarding-state jsonb, so it does not ride **#3553's** CAS.

**Already in the tree — VERIFY, do not rebuild:** the T3 session-filing tool (`tortoise_session_capture`), the
workflows prompt, the T2 backfill import CLI (`tortoise sessions import`), and the session→entity linking pass — each
test-asserted by its owning issue, so a regression is caught, not assumed away. **One gap recorded, not hidden:**
`link_session_entities` runs only on the hosted path, so SDK-captured sessions carry no entity edges — **#3555** wires
it into the SDK capture path (capture-time and `_run_indexing`), and J6 asserts the target.

**Pre-existing bugs.** A bug uncovered in a component below is **fixed inside the owning issue's workstream** when it
blocks that acceptance — the epic's tests must not be green by avoiding it — and anything out of scope is filed per the
file-a-bug rule.

---

## 5. Tests / E2E — how the epic is proven

Layers and runners (verified): dashboard e2e (`RUN_DASHBOARD_E2E=1`, pytest + Playwright); Python integration
against the docker FalkorDB lane; embedded carve-out (`TORTOISE_TEST_CARVE_OUT=1`); node unit (`node --test`);
**ops check** (no runner — manual, executed and logged). Fixture specifics live in the owning issues.

**J1 — First-timer reaches `done` through the Data-sources grid.**
`sign up → provision → org-create → fork → connect → Data-sources grid (index 3) → Continue/Skip → done (index 4)`
→ The grid renders in order; sr-only reads "of 5"; tiles are keyboard/AT reachable; Continue **and** Skip reach `done`.
*Tests:* `tests/e2e/test_dashboard_onboarding.py::test_data_sources_*` (renders-in-order; continue/skip reach done index
4; sr-only reads of-5; keyboard/AT reachable; done Back lands on the grid; the grid's loading/fetch-error branch **that
the chosen read model actually has**; `done` names each partially-finished outcome that applies; the grid renders at the
**Settings** mount with `MemorySources` gone there and its stranded scope/re-index surfaces present in the sibling panel,
and at the **wizard** mount as a net-new grid — `overviewSettings.test.js` (both assertions)
and `wizardArchived.test.js` updated — and the screen renders the disclosure from the runtime template, naming what is
captured and where it goes) · the **derived** renumber surface set (`wizardFlow.test.js`'s exact-step set,
`wizardStageLabel`'s step-3 arms, the `of N` announce literal, the existing first-timer e2e) plus a **source-scan
tripwire** failing on any hard-coded step index or `wizardStep === 3` guard not re-pointed, and
`test_new_step_id_is_in_canonical_vocabulary_and_partition` (the inserted step's id ∈
`STEP_IDS`/`PER_KEY_SEMANTICS`/PATCH model/allowed-keys — the server vocabulary is a renumber surface too) ·
`test_pre_renumber_state_reads_back_after_renumber` (state written by the pre-renumber code stays in the allowed set, so
an in-flight org is not silently reset to "not connected") · a stale-navigation grep (no copy points at the retired home) ·
`test_onboarding_integration.py::test_done_step_server_refresh_fires_on_renumbered_done` (the done-landing effect fires
`refreshOnboarding()` at the renumbered `done`; a client holding the pre-renumber bundle is an accepted limitation)

**J2 — Capture provisioning + status.**
`grid → recording row → claude/pi provisioned (agent-prompt path) → probe fires → off → install-pending →
waiting → active → unsupported harnesses render reason-not-badge`
→ A probe moves `install-pending → waiting`, a receipt `→ active`; unsupported harnesses never render a dead control.
*Tests:* `captureStatus.test.js` · `harnesses.test.js` (set agreement; unsupported reason) ·
`test_session_capture_e2e.py` (**pi probe** — new — status transitions; **hook-dead negative control**) ·
`test_capture_session.py` (store-proven floor + two controls; delivery-lane round-trip; receipt retry; a missing
receipt leaves the harness `waiting`, never a false `active`) · `test_onboarding_endpoints.py` (registration;
last-error pair; verification keys **not** client-writable; `test_patch_surface_partition_is_total_and_disjoint` over
all three classes and both field sets; #3553's parametrized real-path RMW/CAS test **and** its mandatory negative
control) · e2e `test_capture_row_renders_status_not_install_recipe` (tile/row/badge composition; unsupported row
distinct from the badge group, activation a no-op, reason AT-reachable via a focusable `aria-disabled` control
**associated by `aria-describedby`**; the **wizard** mount renders no install recipe while the **Settings** mount
renders the collapsed disclosure; an install pill stays `active` beside a `stored-extraction-failed` session — no
harness-level rollup)

**J3 — Agent-prompt install.**
`tortoise/onboarding/SKILL.md — #3517 owns the install-ask section, inserted immediately before the current §5 (Next
steps), so the ask precedes any capture and the first-capture disclosure keeps its own section → agent asks yes/no →
yes: installs + verifies + names the off-switch location → dashboard reflects the prompt-install before any probe arrives`
→ The prompt answer and the dashboard setting write **identical state keys**; after a yes the harness is not
`install-pending`; the confirmation names the off-switch.
*Tests:* `test_onboarding_session_recording_parity.py` (same keys; prompt-yes not install-pending; prompt-no
installs nothing) · grep/ops gates (the off-switch constant **defined in #3517**; the disclosure emitted from a
non-LLM template; the false-promise grep — **its pattern and file set are defined in #3517**, which owns the gate)

**J4 — Coming-soon click capture.**
`tile click (the demand record is written on the click) → popup is a CONFIRMATION ("Not available yet" + "Go back",
no posting primary) → record queryable per team, deduped → operator Telegram pinged → done`
→ The record is queryable and names the integration + team; dismissal cannot retract it; the ping is bounded (N clicks
⇒ one send, pinned literals); the modal shell + dialog contract are **#3513**'s and the post-action states are
**#3514**'s; a transport failure cannot fail the request. **No delivery, no status tracking, no ship notification**
(R2). The store holds **one record per click**; only the *send* is deduped.
*Tests:* `test_data_source_capture.py` (prop registered; the record lands in the canonical store with `org_key`
server-derived; `test_each_click_writes_a_demand_record`; `test_send_deduped_per_integration_team` and the rate cap as
separate fixtures; `test_click_record_persisted_before_send`; `test_failed_send_is_not_deduped` (429 then 500 then 200 ⇒
the send is retried and the marker is written only on 2xx) with a negative control on marker-write order; a record-write
failure surfaces rather than sending only a ping; the **click → write → popup** ordering and the tile's in-flight state
asserted; a failed write is **visible and recoverable**; Telegram mocked + a raising transport cannot propagate;
concurrent writes; popup states; no cross-team reads;
self-hosted local-JSONL degradation; Telegram skipped when env unset) · e2e (modal dialog contract; popup states; a tile
behind "Show more" still captures — the
≤6-tile bound, the ≤10 total, focus return and reset-on-close pinned in **#3513**; tile keyboard + aria-label; an
unsupported row is not a clickable unbuilt tile) · `test_notify_me_label_creates_no_extra_state` (R4, a **negative
assertion**, binding **only when the label ships** — R4 permits dropping it: dismissing the popup (and the tile click)
writes **only** the base click record; any status record, notify-intent or extra state fails it) ·
ops `ops-telegram-live-send`

**J5 — Capture reliability across death modes (the core ask).**
`session runs → ends by normal exit / Ctrl-C / kill -9 / terminal close / offline / fork-resume → captured with
no loss and no duplicate → recovery sweep drains spool + unsynced files`
→ One Session per logical conversation; the **delivery lane** is monotone (a `store_sync` write never downgrades
`hook`); cursor writes are atomic (tmp + rename) and a corrupt cursor **fails closed** to the last good offset,
recorded; **with capture disabled the verification MUST FAIL**; a hook-dead run is reported hook-not-live;
malformed/truncated/non-UTF8/oversized and cursor-past-EOF are included; dead-letter is **bounded** (a pinned path, a
retention cap, and an honest `N dead-lettered` status); a run in which every POST is 2xx while extraction failed is
**not** counted as "no loss".
*Tests:* `test_store_sync_worker.py` — one row per death mode and guard (normal exit, SIGINT, SIGKILL recovery,
cursor-resume after 500, restart multiplication, fork/resume, sweep drains both substrates, sweep-vs-hook barrier,
claim release/expiry, delivery lane distinguishes lanes, hook-dead control, malformed tail/non-UTF8/oversized, zero-turn
session, cursor-past-EOF, file replaced, backoff exhaustion keeps the batch, permanent-4xx stall,
`test_409_retryable_cursor_not_advanced_not_deadlettered` (driven through the CLI; re-enable then drains the batch),
`test_429_retryable_not_deadlettered`, sweep rate + spool cap + eviction, clock skew, lock failures,
cursor-not-advanced on 500, two concurrent workers on one spool file ⇒ one row, a slow extraction outliving its claim
lease, store-write-fail after graph-write-ok ⇒ recoverable, spool retained, a **fixture-count** no-loss assertion (a
pinned-N session per death mode ⇒ exactly 1 `:Session`, exactly N turn Points with the payload's ids and content, and
exactly one delivery record per mode — "at least one" is not a no-loss proof), and a terminal assertion that a record
carries `capture_ok IS NOT NULL` with a receipt whose `last_error` state agrees,
`test_partial_tail_line_does_not_advance_cursor` (3 complete records + a truncated 4th ⇒ 3 turns delivered and the
cursor at the last `\n`; appending the remainder then delivers the 4th),
`test_single_invalid_byte_does_not_lose_preceding_turns` and `test_split_multibyte_at_read_boundary` (a decode failure
cannot fail the whole file), `test_409_on_one_record_does_not_block_later_records_in_the_same_spool_file`,
`test_spool_eviction_reports_dropped_count` (eviction reports an honest `N dropped`),
`test_repeated_capture_does_not_duplicate_operator_edges` (a lost response and a restart re-POST leave IMPL/NAND/Event/
CONTAINS/entity-link edge counts identical to the single-POST baseline); the concurrency rows **require a released
barrier** (`tests/concurrency_harness.py`) plus a mutation check (drop the lease ⇒ the test fails),
`test_fork_and_clone_do_not_reship_parent_turns` (one `:Session`, no duplicate turn Points),
`test_delivery_lane_not_downgraded_by_concurrent_store_sync`, `test_cursor_survives_torn_write`,
`test_turn_content_mismatch_fails_closed` (stored content unchanged, the conflict recorded),
`test_deadletter_bounded_and_surfaced`, `test_worker_streams_large_session_file`) · ops
`ops-kill9-live-pi-session`, `ops-terminal-close-capture`, `ops-pi-hosted-2xx-leg` · process-level rows are
**spawned** via the `tortoise store-sync` CLI or demoted to ops checks; `store-sync.test.mjs` covers the pure logic;
the backfill CLI by the existing `test_session_import_codex.py` / `test_session_import_desktop.py`

**J6 — The pipeline: captured session → Source → extraction.**
`capture → Source node created → findable via FTS (not index_missing) → extraction runs → entities + Points +
Events + IMPL/NAND operator edges`
→ A captured session's Source **hits** on search; capture and indexer populate the **same** field list; the list and
detail endpoints agree on status and on the `extracted` count; stored is distinguishable from stored-and-processed
(including extraction-failed-but-200) with status × last_error agreeing.
*Tests:* `test_source_fts.py` (findable; capture/indexer field parity; index_missing guard; a Source MERGE failing after
the Session MERGE is retried, never silently unfindable; entity links asserted on **both** the hosted and SDK paths;
`test_preexisting_sources_findable_after_index_with_backfill` and `test_preexisting_sources_honest_gap_recorded` — the
two branches, so neither is a conditional name — plus `test_source_search_text_fallback_when_title_and_summary_absent`,
`test_list_and_detail_status_agree` and `test_list_and_detail_extracted_count_agree`) ·
`test_session_capture_status.py` (extraction-failed-but-200; status ×
last_error; a session whose extraction never runs stays `stored`; `outcome_known: false` never renders stored/processed;
`test_session_writer_outcome_rendered_honestly[commit_created|budget_hold|demo_seeded|longmem_ingest]`; the reconciler
agrees with the 2xx contract on the delete path; extraction set agreement; #3551's writer parity; #3555's count-filter
assertion; new columns appended at the end; a **partial** turn write is never reported ok) · `test_capture_session.py` (full
ontology shape) · `capturedSessions.test.js` (the derivations) · dashboard e2e (the status region is announced as a polite
live region)

**J7 — Opt-out.**
`recording on → turns spooled → off-switch flipped → new capture BLOCKED with 409 + honest copy → already-captured
sessions remain → re-enable works`
→ **Opt-out watermark:** the pre-opt-out backlog is delivered, off-window turns are dropped with an honest recorded
status, the hook re-reads the flag **per turn** — not once at factory time — and stops spooling while opted out so the
spool cap cannot evict pre-opt-out data, and a **409 is retryable, never dead-lettered, the cursor not advanced past
it**. The gate resolves the **graph override before the team default**, the 409 names the deciding layer, and the
watermark comparison is **clock-domain-explicit** — a client-supplied timestamp can never widen or narrow the dropped
window, asserted again under an injected ±1h client clock offset, with a negative control.
*Tests:* `test_capture_session.py` (409 not 403; opt-out writes nothing; the **SDK** path refuses too —
`test_sdk_capture_session_refuses_when_recording_disabled` + a flag-on positive control; an in-flight capture is
rejected when the off-switch PATCH lands; existing sessions survive; re-enable restores the prior install state;
`test_optout_409_retains_backlog_not_deadlettered`; `test_optout_watermark_backlog_delivered_and_window_dropped`;
`test_hook_stops_spooling_while_opted_out`; `test_graph_layer_optout_also_stops_hook_spooling`;
`test_graph_layer_409_surface_is_named`) · `test_q3_decline_then_reenable_consents` · `captureStatus.test.js` (off
wins when recording unset) · dashboard e2e (the **dashboard** off-switch copy names the 409 consequence and is
announced as a polite live region — the agent-facing 409 stays quiet)

**J8 — Self-hosted variant of J1–J7 above.**
`J1 without a dashboard — the surface is the SKILL.md step + tool surface: R1's agent-settable enable and state read
(R6), the honest unsupported reason (R7) and the demand record (R2) transfer; the grid's presentation clauses
(contrast, badge, sr-only) do not · J2 via stdio + probe to the API URL · J3 via the install-ask section + MCP tool ·
J4 → local JSONL, Telegram skipped when the env is unset · J5 identical (hook + store-sync are local) · J6 identical
(local graph) · J7 via the recording tool`
*Tests:* `test_data_source_capture.py::test_selfhosted_*`, `test_selfhosted_j1_mirror` (every enumerated requirement —
R1, R2, R6, R7 **and R9's two clauses** — asserted in its self-hosted form; any that genuinely cannot transfer sits in a
closed, named allowlist, and the allowlist is asserted to equal exactly that set), `test_session_capture_e2e.py` (stdio
honest error; the off-switch blocks the
self-hosted recording tool too), #3557's shared-projection parity + field-enumeration test · ops `ops-selfhosted-parity-run`

**J9 — Agent-settable enable/connect.**
`agent calls the tool surface to enable an existing integration → the state read reflects it → the grid renders
the same state`
*Tests:* the parametrized `test_existing_integrations_agent_settable` (every existing integration has an enable
path **and** a state read), `test_docs_index_tool_registered` / `_enqueues_job` / `_gates_honored`, and
`test_tool_and_dashboard_enable_write_same_keys` — all in #3540.

**Epic gate.** Full docker lane + carve-out + dashboard e2e + node unit suites green, `dist/` rebuilt and committed,
false-promise grep clean — ops check `ops-release-full-suite`. **Every ops check named in this section must carry a
checked-in receipt** (exact command, timestamp, environment, captured output), with a gate test that fails when a named
id has no entry — "executed and logged" with no artifact cannot fail, so it is not a gate. **S0's proving step** is #3539's own acceptance in its
own file set — `test_github_map` / `test_github_indexer` / `test_github_index_lifecycle` / `test_docs_fetcher` and
`test_index_docs_api.py` (which already pins the unchanged-rerun-adds-0-Documents criterion for the docs half) — with a
re-run on an unchanged repo producing 0 new nodes.

**Traceability — a journey with no test is visible here.** J1 new · J2 partial (claude probe exists; pi emitter, negative
control, floor, lane marker new) · J3 new · J4 new · J5 new (the worker test file does not exist) · J6 partial
(`test_capture_session.py` exists; FTS + status files new) · J7 partial (the 409 gate exists; journey assertions new) ·
J8 partial · J9 new. **No journey yet has a complete automated proving test** — the honest state, and why the epic is not
done until §5 is green.

---

## 6. Deferrals, follow-ups and open decisions

**Filed with an owner + trigger (not intentions).**

- **#3519 — semantic transcript retrieval.** Deferred: turn-Point embedding is a retrieval-quality capability the stated
  goal does not require. Good alternative: embed inside the shared write primitive with a pending marker and a re-embed
  path — cost: one task plus an embedding dependency on the capture write path. **Trigger: the dated gate 2026-12-14**,
  re-baselined in #3519 itself. Owner: epistemic-team.
- **#3541 — the Drive/Slack/Notion connector workstream** — out of scope here; the unbuilt tiles stay unbuilt. Owner:
  epistemic-team. **Trigger: the monthly onboarding-cohort review — owned by #3514 and runnable against R2's cross-org
  demand count in `analytics_events` — N=5 distinct orgs requesting one integration, or a customer needing a named
  connector.**
- **#3542 — codex session-end capture** — #1714's Target is amended to pi + claude. #3542 must land before the
  `codex: false` row flips to supported; the trip is §4's assertion. Owner: the #3516 workstream.
- **The research brief** still presents the withdrawn 403 consent contract. **Owner: #3539's lane, which has already
  opened, and must add the superseded banner in the same PR; the false-promise grep's file set includes the brief**, so
  a later reuse without the banner fails the gate.

**Open decisions (escalated, not invented).**

1. **Whether CLI backfill turns with no client timestamp are admissible at all.** Admitting them weakens the store-proven floor; refusing
them may reject legitimate legacy imports. Current position: admitted, recorded, floor disabled for that turn.
2. **The "delete captured sessions" path** promised in the copy: the human decides whether the existing delete path
   satisfies that promise, and whether a verification or a new dependency issue is the right closure.
3. **Splitting this epic** — §3 carries six slices and twenty issues; the open question is whether the epic is split for
   execution (lane-per-PR, §3's three-lane ordering) or run as one batch. A judgement for the human.

---

## 7. Review record

**Tier: High — 4 proportional reviewers** (Structural & Efficiency, Integration, **UX Coherence**, **Failure Mode
Auditor**) **+ the advisory Duplication & Architecture reviewer; cap 10 cycles** — `Complexity: complex` maps to
**High** on the plan-review crosswalk, and the sanctioned tier adjustment is reporting *up*, never down. The earlier
**Low-Medium** run (2 reviewers, cap 3) and its `cycles=9, status=clean` stamp are **deleted, not annotated**: 9 cycles
is past Low-Medium's cap of 3, so that exit required an escalation rather than a signature, and **UX Coherence** and
**Failure Mode Auditor** never ran against text carrying UX requirements (R1, R4, R7) and failure-mode requirements
(R5, R8, R11).

**Cross-artifact corrections required — each child body must match this epic before its implementation lands:** `#3515`
delivery-lane monotonicity (under a property name distinct from `capture_extractor`), the fork/clone turn-id rule,
409/429-retryable, the per-turn flag re-read including the graph override, fail-closed turn-content mismatch, the
`session_import.parsers` reuse, and dropping its `state_version` claim; `#3516` drop "#3540 reads", add the client-side
`autoCapture` enablement and the renumber/Settings-mount assertions; `#3517` the install-ask placement before §5, and the
off-switch constant re-pointed for the grid IA; `#3520` the per-session render must use the extraction vocabulary, never
`HARNESS_CAPTURE_STATUS_LABEL`; `#3513` the unsupported-row contract, the renumber's client surfaces, the archived-block
boundary, and the dialog split from `#3514`; `#3514` the `#3553`/`#3552` edges, the popup error copy, and the dialog
split; `#3518` the two backfill branches, the `_searchText` fallback, and naming `#3551` rather than `#3520`; `#3540` the
`#3539` edge; `#3551` the recording gate (not `state_version`) and the `sdk.py` "never drift" docstring; `#3552`
`state_version`'s class and the three-class invariant derived from the canonical server-owned vocabulary (R11) — **note:
this line still reads `FLOW_KEYS` + `STEP_IDS`, which R11 forbids; unresolved, item 3 below**; `#3553` its own ownership of
the field plus the `(toggle, receipt)` pair — the `(clicks, …)` pair is **stale**, the click record lives in
`analytics_events` (R2); `#3556` its J5 assertion; `#3557` the `#3555` edge; `#3554` `capture_ok`'s divergent computation
point and the `SET`-only writers its predicate misses.

**Cycle log — tier High, N=4 proportional reviewers + the advisory #5, fresh context every cycle.**

| Cycle | Proportional findings | Advisory #5 | Exit |
|---|---|---|---|
| 1 | 37 (1 P0 / 23 P1 / 14 P2) | 5 | issues → fix pass |
| 2 | 39 (1 P0 / 19 P1 / 19 P2) | 5 | issues → fix pass |
| 3 | 35 | 5 | issues → fix pass |
| 4 | 54 (4 P0 / 24 P1 / 24 P2) | 5 (2 P0, 3 P1) | issues → fix pass |
| 5 | 52 (6 P0 / 26 P1 / 20 P2) | 5 + 3 suppressed | **not clean — escalated** |

Recurrence did not fall: cycle 5's set is **not** a strict subset of cycle 4's, and four P0 classes recur. The loop ends
here as an **escalation, not a completion**. **No signature is written**: `status=clean` would be false, and the skill's
`capped` token would be false too (the cap of 10 was not reached) — so the stamp is omitted rather than mis-stated. The
stamp this document's committed HEAD carried (`cycles=9, status=clean, version=2.3.0`, an HTML comment) is **invalid**
and is deleted, not annotated (§7's first paragraph). No stamp line is present in this revision.

**Unresolved — the recurring P0 classes (cycle 5, still open, each with the fix attempted).**

1. **§2's writer count is wrong and its enumeration short.** "eight loci" is not derivable from its own list, and the
tree holds ~13 `:Session` write sites (`delete_session`, `backfill_is_episodic.py`, both `longmem` ingests and the
SET-only capture-state writers are unnamed). *Fix attempted (cycle 4):* replaced "two writers" with a named set — the set
is still incomplete. Needs a mechanical inventory before #3551's contract is written.
2. **The unsupported row carries two contradictory contracts.** R1/R7 ban any activation control ("no focusable
no-op"); J2 asserts "activation a no-op, reason AT-reachable via a focusable `aria-disabled` control". *Fix attempted
(cycle 4):* R1/R7 rewritten to "no activation control" and J2 rewritten — the contradiction survives in J2's phrasing.
One contract must be chosen.
3. **§7's own correction list contradicts R11** (see the corrected line above, and the stale token inside it).
4. **The two default dicts do not agree, so the mandated assertion is red on arrival.** Six keys diverge and no issue
owns the reconciliation. *Fix attempted (cycle 4):* added the assertion — not the reconciliation.
5. **`state_version` versus the existing `version`.** No writer advances the incumbent counter, so a CAS riding it would
compare 1 against 1 (a silent fail-open), and the CAS store itself is unnamed. *Fix attempted (cycle 4):* #3553 now
presents both options — an option list is not a decision.
6. **False-green proofs.** The "no loss" instrument is an existence check whose N is derived from the parser under test,
and the demand-store writer is contracted to "Never raises" and never checks its response. *Fix attempted (cycle 4):*
added a fixture-count assertion — the oracle still comes from the parser.

**Also unresolved (P1/P2, not enumerated in full):** the `#3513 ← #3517` / `#3540 ← #3516` / `#3513 ← #3516` edge
inconsistencies; the tile inventory and the partial-GitHub-tile label; the sibling panel's missing loading/error/retry
matrix; the dedupe marker's store (`analytics_events` is append-only by trigger, so it cannot hold it); `org_key`'s
non-existence; the store-sync worker's language and entry point; the source-scan tripwire failing on the
deliberately-untouched archived wizard; the ops-check receipt gate added in §5 but absent from the epic gate's list; the
`capture-disclosed` FLOW step the new disclosure render may silently consume; and the second JSONL substrate
(`~/.tortoise/session-events/`), which no issue names.

---

## Plan Review — Requires Human Input

**Status:** not clean after 5 cycles — the skill's cap (10) was **not** reached and convergence was **not** reached
(cycle 5 ≥ cycle 4, new dimensions). Explicitly **not** a clean or capped exit.
**Remaining:** P0: 6, P1: ~26, P2: ~20 (cycle 5).

### Unresolved Issues

The six P0 classes above, plus the P1/P2 list. Each carries the fix attempted in the cycle-4/5 pass.

**User decision needed:** (a) pick the unsupported-row contract (inert row vs focusable `aria-disabled`); (b) decide the
CAS token (`version` vs a second counter) — this changes #3553's scope; (c) decide whether the `:Session` writer
consolidation is a full inventory task or a narrower parity task — this changes #3551's size. Everything else is a
correction the next cycle can carry.
