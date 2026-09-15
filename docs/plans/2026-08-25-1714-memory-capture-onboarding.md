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

> **This document is the EPIC.** It holds the overall plan, the requirements, the tests/E2E plan, and the decomposition
> (which issues exist, what each owns, how they depend on each other). **Every system-level detail** — clock sources and
> skew, cursor-advance semantics, byte boundaries, writer inventories and field-parity lists, file-path notes,
> test-fixture specifics, and any open question belonging to one system — **lives in the child issue that owns that
> system.** Research is `docs/research/1714-solution-converge.md`, the integration surfaces per issue are in §4, and §5
> **is** the journey test map. Superseded text is **deleted**, not annotated.

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
demand signal.** An **unsupported** harness is a different class — **reachable and self-announcing, never inert**: reason
text unconditional and at full contrast, row in the a11y tree, **no activation control at all** (no button, no native
`disabled`, no focusable no-op) — decision #3's full-contrast rule, so unavailability is a **label**, never reduced contrast
or removal from the reading order. No bespoke layout, no novel toggle list. The grid's **read model is named in #3513** (a
server route or a client constant), and the loading/fetch-error/retry states exist only if that source is remote — #3513
pins which, and the e2e asserts the branch that exists. **The grid's fetch/loading state never gates navigation:** Continue and
Skip stay available, and the panel carries its own error/retry. **The grid is not a lossy replacement:** `MemorySources` is also
the only writer of the GitHub source-scope keys and the only trigger for the re-index / index-docs jobs, so #3513 carries its
stranded surfaces — enable controls, nested repo-list states, per-repo branch selects, job status lines, the "Indexed · <relative
time>" label — into the tile's sibling panel (§4).
**The first-render set is never shipped-tiles-only:** when the inventory holds an unshipped tile, at least one sits inside the
visible six. The pinned vocabulary `Set up` / `Manage` / `Register interest` cannot express a **partially-enabled** integration
(issues on, documents off), so #3513 pins that tile's label.

> **Repo finding — verified against the tree 2026-09-15.** **(a) THE GRID DOES NOT EXIST IN THIS REPO:**
> `website/apps/dashboard/src/` holds no integrations grid, no logo-tile component and no logo assets. **(b) A LIVE TOGGLE LIST
> DOES EXIST** — `MemorySources` (`main.jsx:8738`), mounted at Settings → Memory sources (`:461`) and in the **archived** wizard
> block (`:6984`). The **Settings** mount is replaced by the grid; the **wizard** step is net-new, and the archived block is left
> untouched so the rollback path keeps rendering `MemorySources`. Build-on notes live in #3513.

**R2 — The click is RECORDED, not DELIVERED.**
Clicking an unshipped integration records **which integration, by which team**, in a **queryable** store, so the operator
can email interested teams at ship time. **The canonical store is the existing append-only, team-indexed
`analytics_events`** (the onboarding funnel-event path), which is also what makes §6's *cross-org* demand count
readable; the click is **not** written into the onboarding-state jsonb, so it does not ride #3553's CAS. The local JSONL
branch is the **self-hosted** degradation, taken **only when Supabase is unconfigured** — on **hosted** a failed write
**surfaces** and writes **no local file**, the fallback being pinned by the test, never read from a module global.
**Notification is manual, outside the product** — no delivery task, no status tracking, no notify channel (§5 J4).

**R3 — Each click also pings the operator's Telegram** (recorded per click, customer-confirmed; the **send** is
deduped and rate-capped per §5 J4 — N clicks ⇒ one send), and never blocking or failing the user's request.

**R4 — "Notify me" survives only as a LABEL.**
The **tile click** records the demand (R2); the popup is a **confirmation** with **no posting primary**. **Two pinned
strings: the badge reads "Coming soon"; the demand action reads "Register interest"** — verbatim in the tile label and the
dialog copy, and never the badge copy. **On a
failed write only, the dialog carries one inline retry**; otherwise its only control is dismiss/"Go back", which cannot
retract the record, and **dismissal returns the tile to its un-clicked state, so a re-activation re-attempts**. An
exploratory click counts as demand. A "Notify me" string is **non-interactive body copy only** — never a label that reads
like a control — creating **no delivery obligation, no status record and no extra state**.

**R5 — Capture is a setting with an honest off-switch.**
Recording is default-on, enforced at **both** the server read default and the client's **off-wins-when-unset** rule. The
opt-out returns **409**, writes nothing, and leaves already-captured sessions intact; re-enabling restores capture. The 409 is
**classified into three disjoint classes**: the **terminal** one (an off-window turn) is dropped once and honestly counted; the
**transient** one (recording off, will be re-enabled) stays retryable; the **in-flight** one — an **admission refusal** for a
session already being captured (`Retry-After: 30`, reachable via this epic's own two-lane race) — is retryable, never
dead-lettered, and never advances the cursor. The wizard and the self-hosted prompt write the **same** state keys.

**R6 — Integrations are agent-settable (thin UI, capable backend, agent-addressable).**
Proven by **J9** in §5. For **every integration this epic supports** — GitHub issues, GitHub documents, agent-session
recording — **an agent can enable/connect it through the tool surface** (MCP tool / API), so the UI needs no bespoke
per-integration flow; a missing agent-settable path is a recorded gap, and the **recording integration's agent-enable
path is #3540's** (named so J9 covers it by value). **Scope bound:** only integrations that **already exist** — Drive / Slack /
Notion are a **future** workstream (filed as follow-up).

**R7 — The ask is mechanism-gated and honest.**
No promise without a mechanism. A harness with no install path renders its **reason text always visible** and carries **no
activation control** (no native `disabled`, no focusable no-op), positionally distinct from the "Coming soon" badge group (§2).
Copy names what is
captured and where it goes; the **capture disclosure** comes from runtime structured data via a **non-LLM template** — a
wrong disclosure is a UX bug. **The template and its single source of truth are #3517's, together with the agent-prompt
render; #3513 owns the dashboard render of that template and the cross-site single-source assertion**; the copy's
navigation target is re-pointed by the rename in #3513.

**R8 — Capture is reliable across death modes.**
Exactly one Session per logical conversation. A forked or cloned session file is **not** a second logical conversation:
its copied prefix must resolve to the **parent's** turns. The present positional key (`{session_id}_t{i}`) **cannot** do
that — a fork carries a new header id, so the current scheme would mint a duplicate turn Point per copied turn — and the
shared reader **is not a Pi reader at all**: `parse_pi = parse_codex` returns **0 turns** on real `~/.pi/agent/sessions/*/*.jsonl`
(`type:"message"` records the codex walker matches none of), silently. The fix is a **turn-record contract carrying per-entry
identity** (entry id + role + content + position) emitted by `session_import.parsers`; **#3551 owns that contract, the parser
change (a real Pi-shaped branch `type:"message"` → `message.role`/`message.content`, a **checked-in verbatim Pi record as the
pinned fixture for #3551 and #3515**, and a **fail-closed guard raising on a `type:"message"` file parsing to 0 turns**) and
its second consumer, the `tortoise sessions import` CLI**, and **#3515 owns the fork/clone rule built on it**,
including what a fork's **post-fork** turns do. Delivery is at-least-once and idempotent server-side — turn Points **and** the
operator/entity edges — resumable after kill/crash, and the verification is **falsifiable**: with capture disabled it MUST fail,
and a store-sync-only run MUST report the hook as not live.

**R9 — The wizard does not dead-end.**
`done` is reachable from the new step by both Continue and Skip. The **partially-finished** set — source not connected,
install not verified, recording off (harnesses that have a path) — is **named** on `done` **and drives its stage label/lede**
(never "You're all set" above a list of things that are not set; header/body agreement asserted). A harness that **cannot be
captured on this harness** is a separate class: the unsupported reason plus an alternative path where one exists. Copy pinned.

**R10 — Captured sessions are findable, and their processing state is honest.**
A captured session's Source is searchable (never `index_missing`), and "stored" is distinguishable from "stored and
processed" — including the extraction-failed-but-HTTP-200 case. The surfaces are `GET /v1/sessions` and `GET /v1/sessions/{id}`
plus the dashboard's per-session render against the per-harness install pill: neither may read processed while the other shows no
error, and install `active` may coexist with a per-session extraction failure. A merely **unknown** outcome (`outcome_known: false`)
must not render as stored/processed. The status region
is a **polite live region** — and on the **install** axis a per-harness state-pill change is announced the same way, with
`last_error` rendered as an **alert**.

**R11 — No silent drops.**
An unregistered onboarding-state key or analytics prop fails with **no error** — it is discarded; the PATCH model is
therefore **`extra="forbid"`**: a key in **none** of the three classes is **rejected (400/422)**, never silently dropped.
"All its surfaces" is enumerated: a state key must be carried by the **two default dicts** (which must **agree** — a
set-equality assertion, because a key landing in only one is invisible to today's registration test), the **allowed-keys
set** (derived from their **union**), the **PATCH model** and **`_PATCH_FIELD_TO_STATE_KEY`** (hyphenated keys reach the
PATCH surface only through it); a prop must be in the **analytics allowlist**, whose additions #3514 owns. Every write site
(dashboard, agent-prompt path, MCP tool, checkpoint) emits a **registered** key, with a **seeded typo as the mutation
control**. The PATCH model **partitions totally and disjointly over the whole model** — registered keys (**client-writable**),
FLOW/operational keys (**server-owned, 403**) and step fields (**422**) — with the 422 class and the accepted step field
**derived from the model, not carved out**; the partition is an invariant asserted by test, not a hand-re-enumerated list.
Its **derivation source is a canonical server-owned vocabulary** (`FLOW_KEYS` ∪
the operational keys ∪ the CAS token `{state_version}`, whose presence is **contingent on #3553's choice** — if the issue
rides the existing `version`, the vocabulary is `FLOW_KEYS` ∪ operational keys only) — **not** `FLOW_KEYS` + `STEP_IDS`.
Operational keys (probes, receipts, last-error, cursors, backfill markers, `state_version`) are **server-owned** — the generic
PATCH must **reject** them, or one client PATCH would forge a capture status, reset an index cursor, or defeat the state CAS.
**Owner: #3552.**

**R12 — The epic is done when the journeys are proven.**
Every journey in §5 passes its proving test (automated where a runner exists; ops checks executed and logged), the negative
controls pass, and every deferral is **filed** with an owner and a trigger.

---

## 2. Epic-level architecture (one statement; detail in the issues)

**Two repositories — current state and target.** The Pi capture mechanism is **real code in an internal tooling repo**
(`agent-infra/extensions/tortoise-capture/` plus a **second overlapping recorder**, `reflect-hook.ts`), symlinked onto dev
machines and released outside the product, so a capture regression is invisible to the product's gates. **Target:
product-owned** (`tortoise/pi-extension/`, mirroring `tortoise/claude-hooks/`) and tested in the product's CI, with
`agent-infra/extensions/` left a **symlink-only dev shim**, kept permanently. #3512 owns the scaffolding and the CI port —
a home without a gate fixes nothing.

**One capture path, one shared primitive — REQUIRED, not current.** The `:Session` MERGE text is **not** owned in one place:
the capture pair keeps **separately drifted** field lists (`machine_id`/`model` hosted-only) and every other writer adds a
partial subset, so the shape is **many partial field lists**; §2 asserts the shape only — never a count, never an inventory, and
**the complete writer inventory is #3551's** (§8), discovered
mechanically **BY FUNCTION** with its search predicate recorded; §2 asserts the shape only — never a count, never an
inventory. **#3551** introduces the shared `_write_session_and_turns` primitive and the single `:Session` field-list
contract the drivers import, with the recording flag an **explicit parameter of the primitive**: capture drivers pass it
**gated**, while the **derived-commit receiver is exempt by declared decision** (its payload carries no turns — the tree's
`#1910`/`#1927` posture, asserted by `test_commit_works_when_recording_disabled`), and **no component other than the primitive
writes any property of a `:Session` node** — asserted by a source scan keyed on `:Session` + (`SET`|`ON CREATE`|`ON MATCH`),
with the existing carve-outs **named in the contract** so a new one is a reviewed diff. The SDK's `machine_id`/`model`
absence stays **knowingly-absent**, not retrofitted; #3554 **verifies** the contract rather than declaring a second schema. Capture
is **hybrid**: an in-process hook for promptness + attribution, plus a **store-sync backstop** reading Pi's session
`.jsonl` — the only mechanism for a session that died without a shutdown event.

**Two status axes, never conflated.** *Install* is per-harness (`off` → `install-pending` → `waiting` → `active`, unchanged).
*Extraction* is per-session (`stored` / `stored-and-processed` / `stored-extraction-failed`) — a **response field**, not team state.
**One composition rule — tile, row, badge, panel.** A **tile** is per *integration*; its element vocabulary is **name**,
**badge** (unshipped only), a **state-varying action label**, and the `aria-label` pattern for a state-bearing label. A **row** inside
the **Data-sources** container is a **per-harness install-state** row — the only row kind there, never a per-session extraction row; the
extraction axis's single home is the **Settings → Captured sessions** screen, named once here and used verbatim thereafter.
**GitHub's issues and documents are separately enable-able sources sharing one OAuth connect** (each with its own index job and
state keys); the one-tile-vs-two-sub-entries presentation, the ordering, the ≤6-visible bound, the ≤10 total and the tile count are #3513's (§8). A
**badge** is the "Coming soon" mark on an **unbuilt grid tile** and never encodes a status; a **shipped** tile's state is
carried by the **state pill**, never a badge. The **delivery lane** (`hook` / `store_sync`, on a new property distinct from
the existing `capture_extractor` extraction lane) is monotone. A **tile is a container**: its interactive content — the
per-harness rows, the off-switch, the **manual-install disclosure** (available collapsed for supported harnesses
**regardless of install state and of the recording switch** — the Settings-first user's decision #2) and **optionally** a
copy of the capture disclosure — lives in a **sibling panel**, never inside the tile's own activation control; the
**recording tile's sibling panel renders EXPANDED at both mounts** (only the manual-install disclosure is collapsed).
**The capture disclosure is itself always visible at a named position
on the Data-sources screen whenever recording is on** — it is the notice that is the legal basis for default-on recording
(decision #1), so it is never only inside a collapsed sibling panel. The reveal rule, badge placement, the placement of the
unsupported rows, the Sessions tile's pointer rule and the panel's default state are **§4's #3513 clauses**.

---

## 3. The overall plan — slices, order, dependencies

Work is organised as six slices. The **non-obvious cross-lane edges** are named here, so a reader can see the crossings
that the slice order does not imply: **#3518 ← #3516**, **#3540 ← #3513/#3516/#3539**, **#3520 ← #3516**, **#3557 ←
#3520/#3555**, **#3552 ← #3553** (its derivation source contains whatever #3553 decides), **#3515 ← #3551** (its fork/clone
rule is built on #3551's turn-record contract and it consumes the primitive), **#3513 ← #3517** (both edit `SKILL.md`,
so #3517's section must land first), **#3540 ← #3552** and **#3513 ← #3552** (both consume #3552's vocabulary/partition).
Within-slice order is in the diagram, not repeated here.

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
S5  #3551 ──▶ #3518 ∥ #3520 ∥ #3552 · #3553 · #3554 · #3555      ← LANE C — #3551 heads it, branching from S1
     #3557 ◀── #3520 (read parity), #3555   #3556 ◀── #3551         #3515 ◀── #3551 trails it, so S2/S3 are
     #3515 ◀── #3551  → S1 gates S2/S3                              NOT parallel with lane C
```

**The S0 gate is scoped, not epic-wide.** It is an **implementation** gate for work touching the shared ingestion
substrate (**#3540** and #3539's docs half) and a **release** gate for every other lane: S1–S3 and the S5 lanes open
immediately. #3539 stays one issue — the docs half feeds **#3540**, so #3540 cannot be implemented before it.

**Three lanes run concurrently** once the **shared primitive** lands: **#3551** heads lane C, **#3515** trails it — **#3515 depends on #3551** (its fork/clone rule is
built on #3551's turn-record contract and it consumes the primitive) — and the lanes then open as (A) **#3516 ∥ #3517**; (B) #3513 →
#3514; (C) #3551's siblings **#3518 ∥ #3520 ∥ #3552–#3555**, with #3557 following **#3520 and #3555** and #3556 following **#3551**.
**#3551 has no issue dependency** and lands first of lane C. Lane A and lane B share two files (`harnesses.js` copy constants and
`SKILL.md`), so they are **not** fully concurrent: **one writer owns each** — #3517 inserts the SKILL.md install-ask section before
#3513's step text, and **#3516 owns every copy/status constant in `harnesses.js`, which #3517 and #3513 consume read-only** (a
needed new constant is **requested from #3516**, never added by a consumer). **The rule: the owner of a user-facing copy constant is
the owner of the file it lives in** — `harnesses.js` #3516, dashboard-screen copy in `main.jsx` #3513, agent-prompt copy #3517, the
`tortoise_session_capture` tool description in `tortoise/tool_registry.py` #3540, and the team-level 409 detail in
`tortoise/hosted_api.py` #3556. **Both `SKILL.md` writers update the deployed mirror in their own PR** (`deploy-pages.yml` enforces
byte-identity); #3520 and #3518 trail #3516, so lane C trails lane A.

**Why this order.** #3512 must precede #3515/#3516/#3517 — they modify the extension whose home #3512 decides, so
implementing first means implementing in the wrong repo. #3513 needs the live wizard and the existing
`HARNESS_CAPTURE_STATUS_LABEL` constant the recording tile renders, and follows #3517 **solely because both edit
`SKILL.md`** (write-conflict ordering, not semantics). **#3551 lands first of all**: lane C builds on its contract.

---

## 4. Decomposition — what each issue owns

| Issue | Owns | Depends on | Journeys |
|---|---|---|---|
| **#3539** | GitHub ingestion baseline (shared mapper, cursor-correct fetch+diff, partial-batch recovery, rate-limit-safe pagination, lifecycle writes, quota fairness, auto-index, connector wrappers, dedupe decision) + remote docs extraction (fetcher, staging, `/v1/index/docs`, Document-aware quota, deploy config) | — | — (acceptance in #3539) |
| **#3512** | The Pi mechanism's home: **scaffolds `tortoise/pi-extension/`**, product-owned, two recorders consolidated into one, **its test suite ported into the product's CI** with a **dedicated node job carrying its own path trigger** and globs covering `.test.mjs`/`.test.ts` (`store-sync.test.mjs` is named in §5), the **packaging/install surface** for the new home (`pyproject.toml` package-data, wheel content, the installer/`cp` path, the **packaging gate**), plus the **agent-infra-side deliverable** — the separate repo's issue/PR reducing `agent-infra/extensions/` to the shim, the dev-machine symlink repoint, and the **PR ordering** so no window exists where capture is installed but unwired | — (S0 is a **release** gate for this lane, not an implementation gate — §3) | prerequisite for J2/J5/J8 |
| **#3515** | The capture architecture (hybrid hook + store-sync, the worker consuming `session_import.parsers`' Pi reader (its **#3551-pinned verbatim Pi fixture** is the reference)), the guards, the two-part falsifiable verification, the **monotone delivery lane** on a property named distinctly from the existing `capture_extractor` extraction lane, the **409/429-retryable** rule **per record, so one blocked record cannot head-of-line-block the rest of a spool file**, the hook's **per-turn** re-read of the recording flag **resolving the graph override too**, **fail-closed** on a turn-content mismatch, the **cursor model** — a **per-record cursor/lease** plus a **separate file-offset watermark** (never skipping a partial tail; while an interior record is blocked its cursor holds and the file offset advances past it), the **fork/clone rule** built on #3551's turn-record contract, the ops-check receipts §5 requires, the store-sync worker's **implementation language and its `tortoise store-sync` CLI entry point** (§5 pins that name), the **second JSONL substrate (`~/.tortoise/session-events/`)** its sweep drains alongside the spool, and the death-mode additions §8 hands it — the **store-sync-only** and **offline** rows in the pinned-N table, the **spool cap** as a pinned literal (after eviction the cursor **re-anchors to a record boundary**, and `delivered + N dropped (spool cap) + dead-letter == written` holds — mutation control: the retention constant), **spool write failures** (ENOSPC/EACCES, torn append ⇒ non-silent failure, no cursor advance past unwritten bytes), the **live-fork** case (parent still appending; released barrier + mutation check), and a **permanently undecodable interior record** (dead-letter, honest `N dropped`, later records still delivered, cursor advances), the **fixture rows for an unknown entry kind, an entry with no id** (**never positional-id synthesis** — that re-mints R8's defect) **and duplicate ids**, the client's decision on a **2xx carrying `capture_ok = false`** (cursor advance, receipt, consistent across both lanes), and the `~/.tortoise/session-events/` substrate's **regime, schema owner and pre-existing consumer `tools/build_window_transcript.py`** | #3512; **#3551** (the turn-record contract its fork/clone rule is built on) | J5, J7, J8 |
| **#3516** | Provisioning + verification of capture from onboarding (claude + pi probes, symlinked install, unsupported rows honest, the **set-agreement and `codex: false` tripwire assertions in `harnesses.test.js`** — both **new** — the **harness copy constants**, and the **client-side `autoCapture` enablement** — the named config key in its named store — that makes "default-on" true for a user who has not touched `~/.pi/agent/tortoise-config.json`, written **atomically so a racing writer can never silently revert it to `false`**, with default-on enforced at **both** the server read default and the client's **off-wins-when-unset** rule, plus the **local-config substrate's failure modes** (two writers racing on that file — a read-modify-write must not clobber a sibling key or revert the capture flag — a torn write, and a malformed / empty / non-UTF8 / absent config, each with a pinned honest outcome, **never a silent capture-off**) | #3512, #3515 | J2, J5, J7, J8 |
| **#3517** | Agent-prompt install (a setting, single state path, parity-tested, off-switch named) + the **capture-disclosure template, its single source of truth and the agent-prompt render** (its step/state accounting, so the `capture-disclosed` FLOW step is never silently consumed, §8) + the **research-brief banner** (its acceptance, the false-promise grep as its trip — **not** a #3539 merge-blocking condition) | #3512, #3515 | J3, J8 |
| **#3513** | The Data-sources wizard step: the **integrations grid** and its **named read model**, the tile inventory + ordering, the grid's loading/fetch-error states (only if the read model is remote), the modal shell + dialog a11y contract (post-action states are #3514's), the **unsupported-row contract** (reason text at full contrast, no activation control), the **sibling panel carrying `MemorySources`' stranded surfaces** (source scope, the re-index / index-docs triggers, the per-repo branch selects, the job status lines) **plus its loading / fetch-error / retry matrix** (§8), the Settings mount replacing `MemorySources` (**the archived wizard block is untouched**; the wizard mount is net-new), the `done` renumber's **whole surface set — derived, not hand-listed** (`wizardFlow.js` + its test, `wizardStageLabel`'s step-3 arms, the `of N` announce literal, every hard-coded step index and `wizardStep === 3` guard in `main.jsx` including the connect step's three Skip handlers and the done Back, the existing first-timer e2e), the **stale-navigation rename** (the four user-facing strings that point at the old home, plus the ToS/notice line), **wiring `tests/e2e/test_dashboard_onboarding.py` into `.github/workflows/ci.yml`** (a named deliverable — §5's runner note), the **GitHub one-tile-vs-two-sub-entries presentation question** and the grid's **read-model branch decision** (R1), the Sessions tile's **pointer copy** to the **Settings → Captured sessions** screen (§2's single home for the extraction axis), **the dashboard render of #3517's capture-disclosure template at its named always-visible position with the cross-site single-source assertion**, **`config/connector_manifest.yaml` as a tile-inventory input**, **one declaration for the integration inventory** with a `test_cross_surface_harness_vocab_contract`-style assertion (tile keys ↔ click-record keys ↔ #3540's parametrized list), the **stale-navigation grep's file set** (derived mechanically with named exclusions and **naming `tortoise/tool_registry.py` and `tortoise/hosted_api.py`**) (github active; slack / linear / supabase_org inactive, loaded by `tortoise/connector_loader.py`, plus the `integrations/` tree — **every** inactive registry connector gets a coming-soon tile — **#3541 owns only its connector/backend build**), the **panel's reveal a11y contract** (what opens it, single-open, focus behaviour) with the **recording tile's sibling panel EXPANDED at both mounts** (only the manual-install disclosure collapsed; a shipped tile opens one panel at a time with Continue/Skip never gated; an unshipped tile records demand and opens none) and its **empty / not-connected states** (the wizard mount has nothing connected), the **unsupported rows rendering inside that panel**, "Coming soon" badges only on unbuilt grid tiles, the **capture disclosure's always-visible position**, and the Sessions tile carrying **no aggregate, only its pointer copy**, the **harness row matching the connect-step choice** (suppressed when the connect step was skipped; the `'claude'` default is never presented as a user choice), the `done` **header/body agreement** (a named partially-finished item drives the stage label/lede) with the cannot-be-captured class copy, the **"Show more" contract** (label, expand/collapse, announcement, and the priority rule for the visible six), and the **rename surface per file**, updating the deployed `SKILL.md` mirror in **this issue's own PR** (§3; `deploy-pages.yml` enforces byte-identity) | **#3517** (both edit `SKILL.md`; #3517's section lands first); **#3552** (the vocabulary/partition it consumes) | J1, J2 |
| **#3514** | Demand capture: the **click record in its canonical store (R2's `analytics_events`)** + its analytics-prop registration + the ops Telegram ping (nothing is delivered), **the hung-transport case** (a send that neither returns nor raises: the click request completes 2xx within a pinned budget, the record is already persisted, the timeout constant is pinned, and N such clicks are not serialized behind the hung transport), **including the self-hosted trigger surface** (a prompt question or an MCP tool — **required, not optional**; a divergence is gated like R2's others, as a **closed allowlist entry with an owner and a trigger**), the **popup's post-action states and error affordance** (the dialog carries **no posting primary**; "Notify me" is body copy only), the **dedupe/rate marker written only on a 2xx send, in a store that can hold it** (`analytics_events` is append-only by trigger, so the marker is not an update of the record row), the record write's **response checked and its failure surfaced** (on hosted a failed write writes **no local file**; the JSONL branch only when Supabase is unconfigured, pinned by the test rather than a module global) with the record's server-derived **`org_key`** (§8), the **monthly onboarding-cohort review as a receipted ops check**, runnable against the cross-org demand count, click-record **idempotency** (a replayed request writes no second record) with a **released-barrier concurrent-click test asserting exactly one send**, **integration-key validation against #3513's tile inventory**, the **Telegram reuse-or-diverge decision** (whether the ping reuses `tortoise/telegram_push.py` + `alert_store.py` — and how their parked-pending retry reconciles with R3's one-send rule — or deliberately diverges), and the **read half of the demand store** (per-team and cross-org queries, and a non-service-role caller cannot read another team's) (§8) | #3513 | J4, J8 |
| **#3540** | Agent-settable enable/connect for the **existing** integrations, through the tool surface — **subject to the same three-class partition as the PATCH surface** (a tool path that could set a server-owned key is the same forgery R11 forbids), with **#3552's partition as the contract**: the tool paths carry the same three-class partition test, and the tool **holder** is tested (revoked key, expired key, non-owner member each refused with no state write), the **`tortoise_session_capture` tool description** in `tortoise/tool_registry.py`, and the **cross-surface integration-key assertion** with #3513 (§8) | #3513, #3516, **#3552** (the partition it consumes); **#3539** (S0 implementation gate — §3) | J9 |
| **#3518** | The session `Source` FTS index (captured sessions become findable), and the **backfill decision for the pre-change captured-Source backlog** — no backfill ⇒ the unfindable interval is a recorded honest gap | #3515; **#3551 must land first** (the primitive it indexes); #3516 supplies its end-to-end data | J6 |
| **#3520** | Stored vs stored-and-processed: **owns the extraction-status vocabulary** (`{status, extractor, outcome_known}` on `GET /v1/sessions` + `{id}`, appended at the END), `last_error` agreement on the 2xx path, the dashboard render of the **extraction** vocabulary — a **response field**, never a state key — and the **writer-outcome wording** (`commit_created`/`budget_hold`/`demo_seeded`/`longmem_ingest`), including which of them render and where the demo row is sourced from; the install pill is never driven by a session's extraction status, and the harness-level rollup stays dropped; the **Captured-sessions screen's own state contract** (loading / fetch-error+retry / empty / `outcome_known:false`) is also its (§8). Its former writer, key-ownership, ontology and count/link items are **#3551–#3555** | #3515, #3551, #3516 | J2, J6 |
| **#3551** | The shared `_write_session_and_turns` primitive — the **`:Session`/turn field-list contract** the drivers import (and the **complete writer set, discovered BY FUNCTION with its search predicate recorded** — §8 hands this to this issue, and §2 asserts no count), the **turn-record contract carrying per-entry identity** (entry id + role + content + position) with `session_import.parsers` as its single implementation including the `tortoise sessions import` consumer, with a **real Pi-shaped parse branch** (`type:"message"` → `message.role`/`message.content`) plus the **checked-in verbatim Pi fixture for #3551/#3515** and a **fail-closed guard that raises when a `type:"message"` file parses to 0 turns**, and the **`SOURCE_PATTERNS` registration** that makes a parsers-only PR run the import tests, the turn-id decision for **existing** records, `CONTAINS` wiring, `embed_fn` — the **recording flag as an explicit parameter of the primitive** (capture drivers pass it gated; the **derived-commit receiver is exempt by declared decision** — its payload carries no turns), so no capture caller MERGEs `:Session` ungated, the **gate-refusal test walking its own discovered writer set** with a source assertion that **no component other than the primitive writes any property of a `:Session` node** (scan keyed on `:Session` + `SET`|`ON CREATE`|`ON MATCH`), the existing carve-outs **named in the contract**, the **writer-parity test over the union of the field lists**, and the **`entity_links_attempted`/`entity_links_created` semantics** (capture's per-capture vs the index-time re-link's per-index-run derivation) in the contract, and the stale "never drift" invariant comments; SDK `machine_id`/`model` recorded **knowingly-absent**, not retrofitted | — **(lands first; #3515, #3518, #3520, #3552–#3556 all rebase on it)** | prerequisite for J5/J6; J7 |
| **#3552** | Server-ownership of the capture keys: one write path cannot green a capture status, reset an index cursor, or defeat the state CAS (R11) — the PATCH surface's **three classes** are **total and disjoint** over the allowed-keys **and** state-key PATCH-field sets, **derived from a canonical server-owned vocabulary** (not `FLOW_KEYS` + `STEP_IDS`) and asserted behaviourally rather than by enumeration; **reconciles the two default dicts — the set-equality assertion is red on arrival until the diverging keys are reconciled here — adds that assertion** and covers **every** write path (PATCH, checkpoint, MCP/tool, agent-step), with the non-state PATCH fields and the one accepted step field **derived from the model, not carved out**, the **shadow deny-list assertion** (no onboarding jsonb key may shadow a teams column / Team property a credentials path reads), plus the **off-switch role matrix** for `PATCH /v1/onboarding/state` (owner, non-owner member, revoked key, expired key, session-user vs key-authed — who may set/clear `session_recording`, a refusal leaving state untouched, contiguity with the graph override's owner gate) | #3551; **#3553** (its vocabulary contains whatever #3553 decides, so #3553's choice must land first) | J2, J7 |
| **#3553** | `state_version` compare-and-set over the **onboarding-state jsonb read-modify-write** — **this issue owns the choice** (§8), made in its own scoping pass: ride the existing server-owned monotonic `version` on `:OnboardingState` (already in `FLOW_KEYS`, already PATCH-rejected) — no writer advances it today, so a CAS riding it would compare 1 against 1, a silent fail-open, and **if the issue rides `version`, the module-level writer that advances it lives in `tortoise/onboarding/state.py` under `_org_lock` and is landed by #3553 itself** (#3551's primitive must not write onboarding state) — **or** record a deliberate second counter with a test asserting the two cannot disagree and naming which one a reader trusts — server-set only, never client-writable either way — plus the **jsonb-then-graph partial failure** (the graph-second write fails after the jsonb write lands: the retry must converge, no lost key, no user-visible dead-end) and the fact that **`_org_lock` is process-local**, so a second process still loses a concurrent write and the CAS must be a **conditional store affecting 0 rows on a stale version**, with the mutation control being **removal of the predicate** | #3551 | J2 |
| **#3554** | **Verifies** the canonical `:Session` property schema #3551 declares (**not** a second competing schema) + a re-runnable writer inventory + the `ONTOLOGY.md` `speaker` row correction, recording `capture_ok`'s **knowingly-divergent computation point** (hosted computes it after the verification read, the SDK before it) and the **turn-count shortfall that `capture_ok` does not currently reflect**, and the **D9 retrofit audit** — quantify existing `:Session` records with `capture_ok=true` while the turn count is short, and those whose `capture_ok` was computed under the pre- vs post-verification-read ordering, per graph, then decide correct-in-place vs a recorded honest gap, **quantifying the records the `entity_links_*` divergence under-counted** (§8) | #3551 | — |
| **#3555** | The `extracted` count-filter unification + the SDK entity-link gap: **wire `link_session_entities` into the SDK capture path** (the epic's Target is entity-linked capture on one path); divergence is admissible **only** if the in-process entity-resolution dependency is genuinely unavailable, recorded as a gap in #3555 with the reason | #3551 | J6 |
| **#3556** | The server-side `capture_ok` NULL hole + `_capture_abandoned_marker` / `_reconcile_capture_receipts` disagreement with the 2xx contract — **triage before J5 claims no loss** | **#3551** (it edits the same capture handler) | J5 |
| **#3557** | **Add** the SDK `list_sessions` read surface mirroring `GET /v1/sessions` — net-new public API, no SDK method exists today (the tool registry records "(no SDK method)") — plus its export/doc accounting | #3520, #3555 (it mirrors the `extracted` count #3555 unifies) | J8 |
| **#3519** | **DEFERRED, out of this epic** — semantic transcript retrieval | dated gate | — |
| **#3541** | **FOLLOW-UP, out of this epic** — the Drive/Slack/Notion **connector/backend build only** (the tile is not a deferral: every inactive registry connector ships a coming-soon tile in #3513), plus a **scheduled evaluation of R2's demand query that flags while this issue is open** | #3513 | — |
| **#3542** | **FOLLOW-UP, out of this epic** — codex session-end capture. The `codex: false` row ships today; the trip is `harnesses.test.js` asserting `HARNESS_CAPTURE_SUPPORT.codex === false` with a comment naming #3542, plus a #3542 acceptance step that flips the constant and that test in the same PR, and a **dated gate in §6's deferral table** (a constant assertion alone can never trigger the work) | — | — |

**MECE.** Every journey J1–J9 in §5 has an owning issue; **#3552** owns the key enforcement that makes the 409 gate un-forgeable.
Overlaps earlier revisions carried are retired by construction: the **inline-install surface** (the *manual-install recipe* is #3517's; the
**wizard** mount renders status + a pointer, the **Settings** mount the one recorded exception), **status-row rewriting** (#3520
supersedes #3516/#3513's static assertions), the **two status axes**, the **fork/clone turn-id rule** (#3551 the contract, #3515 the rule),
and the **dialog contract** (shell + a11y #3513; post-action states #3514). **Declared boundaries, not overlaps:** the demo seeder's
`:Session` write is a declared **excluded driver** of the primitive (sample data), so #3520's `demo_seeded` render must name its own source;
the derived-commit producer is **exempt from the recording gate by declared decision** (its payload carries no turns); and the click record
lives in `analytics_events` (R2), **not** the onboarding-state jsonb.

**Already in the tree — VERIFY, do not rebuild:** the T3 session-filing tool (`tortoise_session_capture`), the workflows prompt, the T2
backfill import CLI (`tortoise sessions import`) and the session→entity linking pass, each test-asserted by its owning issue.
**Pre-existing bugs** are **fixed inside the owning issue's workstream** when they block that acceptance — the epic's tests must not be
green by avoiding them — and anything out of scope is filed per the file-a-bug rule.

---

## 5. Tests / E2E — how the epic is proven

**Layers and runners (verified):** dashboard e2e (`RUN_DASHBOARD_E2E=1`, pytest + Playwright) — **opt-in, not wired into
`.github/workflows/ci.yml`**: the dashboard journey assertions in J1/J2/J4/J5 are **ops-checked, not CI-run**, until **#3513 wires
`tests/e2e/test_dashboard_onboarding.py` into `ci.yml`** (a named deliverable in §4); Python integration against the docker FalkorDB
lane, with `tests/test_delivery_tenancy.py` the API-side guardian of J7's layer-precedence gate; embedded carve-out
(`TORTOISE_TEST_CARVE_OUT=1`); node unit (`node --test`); **ops check** (no runner — manual, executed and logged).
**Every node test file named in this section must be run by a named CI job** (a checked-in list asserted against the selected
globs, so a named-but-unrun instrument fails loudly); **#3512 lands a dedicated node job for the product-owned extension home
with its own path trigger and globs covering `.test.mjs`/`.test.ts`** — today's only node job globs `src/*.test.js` under
`website/apps/dashboard`, so `store-sync.test.mjs` runs nowhere and §5's instrument list is **derived from those globs**. **A
checked-in deferral table** (issue, trigger kind → date | threshold) gets an instrument that fails when a **dated** gate is past
while its issue is open and **schedules the evaluation of a threshold gate's demand query** — mutation controls: a **seeded
past-date row** and a **seeded threshold row**. R2's cross-org demand query is proven against a **seeded N=5 fixture**, and the
**monthly onboarding-cohort review is a receipted ops check**. Fixture specifics live in the owning issues.

**J1 — First-timer reaches `done` through the Data-sources grid.**
`sign up → provision → org-create → fork → connect → Data-sources grid (index 3) → Continue/Skip → done (index 4)`
→ The grid renders in order; sr-only reads "of 5"; tiles are keyboard/AT reachable; Continue **and** Skip reach `done`.
*Tests:* `tests/e2e/test_dashboard_onboarding.py::test_data_sources_*` (renders-in-order; continue/skip reach done index
4; sr-only reads of-5; keyboard/AT reachable; done Back lands on the grid; the grid's loading/fetch-error branch **that
the chosen read model actually has**; `done` names each partially-finished outcome that applies **and its stage label/lede
agrees** (a cannot-be-captured harness is named by its reason, never "install not verified"); **at least one unshipped tile sits
inside the visible six at first render**; the grid renders at the **Settings** mount with `MemorySources` gone there and its
stranded scope/re-index surfaces present in the sibling panel (**recording tile's panel expanded at first paint**), and at the
**wizard** mount as a net-new grid — `overviewSettings.test.js` **three affected assertions, the Settings-home `<MemorySources …>`
render rewritten to assert the grid there** — and `wizardArchived.test.js` updated — and the screen
renders the **capture disclosure** at its named always-visible position, **without opening the sibling panel** while recording
is on, naming what is captured and where it goes) · the **derived** renumber surface set — **enumerated in §4, not here** — plus
a **source-scan tripwire** failing on any hard-coded step index or `wizardStep === 3` guard not re-pointed, its file set
**derived mechanically** (a glob over the dashboard source plus a **named exclusion list**) with a **seeded-violation control**
(all #3513's, §8) ·
`test_new_step_contributes_no_server_state_key` (the step adds **no** entry to either default dict, the allowed-keys set or the
PATCH model; its id is never a `STEP_IDS` member; the server-owned vocabulary is #3552's, parametric on #3553's branch) ·
`test_pre_renumber_state_reads_back_after_renumber` (state written by the pre-renumber code stays in the allowed set, so
an in-flight org is not silently reset to "not connected") · a stale-navigation grep (file set derived mechanically with named
exclusions and a **seeded-violation control**) · the done-landing refresh (`refreshOnboarding()` at the renumbered `done`) is
**client-side: in the dashboard e2e set or a node source-scan tripwire, never in the server-side TestClient file**

**J2 — Capture provisioning + status.**
`grid → recording row → claude/pi provisioned (agent-prompt path) → probe fires → off → install-pending →
waiting → active → unsupported harnesses render reason-not-badge`
→ A probe moves `install-pending → waiting`, a receipt `→ active`; unsupported harnesses never render a dead control.
*Tests:* `captureStatus.test.js` · `harnesses.test.js` (set agreement; unsupported reason) ·`test_session_capture_e2e.py` (**pi probe** — new — status transitions; **hook-dead negative control**) ·
`test_capture_session.py` · `test_onboarding_endpoints.py` (registration; last-error
pair; verification keys **not** client-writable; `test_capture_surface_keys_shared_across_defaults`'s **set-equality** assertion
and `_ALLOWED_STATE_KEYS`' **union** derivation — R11's named proof; an unregistered PATCH key is **rejected** (`extra="forbid"`)
and a **seeded typo across all four write sites** is the mutation control; `test_patch_surface_partition_is_total_and_disjoint` over all
three classes and both field sets; #3553's parametrized real-path RMW/CAS test **and** its mandatory negative control) · e2e
`test_capture_row_renders_status_not_install_recipe` (tile/row/badge composition; unsupported row distinct from the badge group,
its reason text **in the a11y tree at full contrast** and **no activation control of any kind inside the row**; a **state-pill
change in a polite live region with `last_error` as an alert**; the **wizard** mount omits the **manual-install** recipe while the
**Settings** mount renders it collapsed, **regardless of install state and of the recording switch**, as does the capture
disclosure at its named always-visible position, and **both mounts render the recording tile's sibling panel expanded at first
paint** (rows, unsupported reason, pill and off-switch visible without opening anything; the cannot-be-captured class shows its
reason and alternative path); an install pill stays `active` beside a `stored-extraction-failed` session — no
harness-level rollup)

**J3 — Agent-prompt install.**
`tortoise/onboarding/SKILL.md — #3517 owns the install-ask section, inserted immediately before the current §5 (Next
steps), so the ask precedes any capture and the first-capture **capture disclosure** keeps its own section → agent asks yes/no →
yes: installs + verifies + names the off-switch location → dashboard reflects the prompt-install before any probe arrives`
→ The prompt answer and the dashboard setting write **identical state keys**; after a yes the harness is not
`install-pending`; the confirmation names the off-switch.
*Tests:* `test_onboarding_session_recording_parity.py` (same keys; prompt-yes not install-pending; prompt-no
installs nothing) · grep/ops gates (the prompt's off-switch copy is #3517's, the `harnesses.js` constant #3516's (§3); the
**capture disclosure** emitted from a non-LLM template; the false-promise grep — **pattern and file set defined in #3517** (the
set **includes the research brief**), which owns the gate, with a **seeded-violation control**: a fixture carrying the withdrawn
403 wording must fail it)

**J4 — Coming-soon click capture.**
`tile click (the demand record is written on the click) → popup is a CONFIRMATION ("Not available yet" + "Go back",
no posting primary) → record queryable per team, deduped → operator Telegram pinged → done`
→ Queryable per team; dismissal cannot retract it; the ping is bounded (N clicks ⇒ one send, pinned literals); the modal shell +
dialog contract are **#3513**'s, the post-action states **#3514**'s; a transport failure cannot fail the request. **No delivery,
no status tracking, no ship notification** (R2). The store holds **one record per click**; only the *send* is deduped.
*Tests:* `test_data_source_capture.py` (prop registered; the record lands in the canonical store with `org_key`
server-derived; `test_each_click_writes_a_demand_record`; `test_send_deduped_per_integration_team` and the rate cap as
separate fixtures; `test_click_record_persisted_before_send`; `test_failed_send_is_not_deduped` (429 then 500 then 200 ⇒
the send is retried and the marker is written only on 2xx); a record-write
failure surfaces rather than sending only a ping; the **click → write → popup** ordering and the tile's in-flight state
asserted; **on a failed write the dialog carries exactly one inline retry** — otherwise dismiss is its only control, and
**dismissal returns the tile to its un-clicked state, so a re-activation re-attempts**; **the badge reads `Coming soon` and the
demand action reads `Register interest` verbatim** in the tile label and the dialog copy; Telegram mocked + a raising transport cannot
propagate; no cross-team reads; the local-JSONL branch asserted **only when Supabase is unconfigured** (on hosted a failed write
surfaces and writes no local file; the fallback is pinned by the test, not a module global); Telegram skipped when env unset) · e2e (modal dialog
contract; a tile behind "Show more" still captures — the ≤6-tile bound, focus return and reset-on-close pinned
in **#3513**; tile keyboard + aria-label; an unsupported row is not a clickable unbuilt tile; **at least one unshipped tile sits
in the visible six at first render**; **no control in the dialog writes a record or state**) ·
`test_notify_me_label_creates_no_extra_state` (R4: dismissing the popup (and the tile click) writes **only** the base click
record — no status record, notify-intent or extra state — binding only when the copy ships) · ops `ops-telegram-live-send`

**J5 — Capture reliability across death modes (the core ask).**
`session runs → ends by normal exit / Ctrl-C / kill -9 / terminal close / offline / fork-resume → captured with
no loss and no duplicate → recovery sweep drains spool + unsynced files`
→ One Session per logical conversation; the **delivery lane** is monotone (a `store_sync` write never downgrades
`hook`); cursor writes are atomic (tmp + rename) and a corrupt cursor **fails closed** to the last good offset,
recorded; **with capture disabled the verification MUST FAIL**; a hook-dead run is reported hook-not-live;
malformed/truncated/non-UTF8/oversized and cursor-past-EOF are included; dead-letter is **bounded** (a pinned path, a
retention cap, and an honest `N dead-lettered` status); a run in which every POST is 2xx while extraction failed is
**not** counted as "no loss".
*Tests:* `test_store_sync_worker.py` — one row per death mode and guard (**the full list is §4's #3515 row**), plus the epic's
oracles: a **fixture-count** no-loss assertion (a pinned-N session per death mode ⇒ exactly 1 `:Session`, exactly N turn Points
carrying the payload's ids and content, and that Session's **lane equal to the death mode** — "at least one" is not a no-loss
proof, and **the expected N is a pinned literal per death mode in the fixture, never derived from the parser under test**);
`test_409_retryable_cursor_not_advanced_not_deadlettered` (through the CLI; re-enable then drains the batch),
`test_429_retryable_not_deadlettered` and `test_409_on_one_record_does_not_block_later_records_in_the_same_spool_file`;
`test_partial_tail_line_does_not_advance_cursor`; a **table-driven failure-taxonomy row** over {400, 401, 402, 403, 409 ×3, 429,
503, transport-refused, socket-timeout, hung-socket} asserting per code — cursor advanced?, dead-lettered?, retried?, honesty counter
incremented? — with a **pinned per-record response budget** for the hung case and a **barrier proving a hung record does not block
later records**; the **concurrent same-turn dual-lane row** (the hook POST racing the store-sync sweep over one turn range ⇒ exactly
1 `:Session`, exactly N turn Points, **no `N dropped` increment**, lane intact — released barrier; the mutation control removes the
in-flight reservation); two concurrent workers on one spool file ⇒ one row, driven by a **released
barrier** (`tests/concurrency_harness.py`) with a mutation check (drop the lease ⇒ the test fails);
`test_fork_and_clone_do_not_reship_parent_turns`; `test_delivery_lane_not_downgraded_by_concurrent_store_sync`;
`test_cursor_survives_torn_write`; `test_turn_content_mismatch_fails_closed`; `test_deadletter_bounded_and_surfaced`;
`test_capture_ok_never_true_with_a_short_turn_count` (mutation control: truncate a turn); the no-loss proof is the pinned-N counts,
ids/content and lane — **never `capture_ok`**, a flag written by the implementation under test — and the receipt's `last_error` agrees ·
`test_commit_endpoint.py::test_commit_works_when_recording_disabled` (the derived-commit receiver's **declared exemption**) ·
the **gate-refusal test walking #3551's discovered writer set**, asserting the primitive is the only `MERGE … :Session` site ·
ops `ops-kill9-live-pi-session`, `ops-terminal-close-capture`, `ops-pi-hosted-2xx-leg` · process-level rows are **spawned** via
the `tortoise store-sync` CLI or demoted to ops checks; `store-sync.test.mjs` covers the pure logic; the backfill CLI by the
existing `test_session_import_codex.py` / `test_session_import_desktop.py`

**J6 — The pipeline: captured session → Source → extraction.**
`capture → Source node created → findable via FTS (not index_missing) → extraction runs → entities + Points +
Events + IMPL/NAND operator edges`
→ A captured session's Source **hits** on search; capture and indexer populate the **same** field list; list and detail agree on
status and on the `extracted` count; stored is distinguishable from stored-and-processed (including
extraction-failed-but-200).
*Tests:* `test_source_fts.py` (findable; capture/indexer field parity; index_missing guard; a Source MERGE failing after the
Session MERGE is retried, never silently unfindable; **one cross-path parity/monotonicity test** for the drifted
`entity_links_attempted`/`entity_links_created` writers (capture's per-capture vs the index-time re-link's per-index-run); the two
**pre-existing-source** branches, each a named test, so neither is a conditional name; `test_list_and_detail_status_agree`;
`test_list_and_detail_extracted_count_agree`) · `test_session_capture_status.py` (extraction-failed-but-200; status ×
last_error; a session whose extraction never runs stays `stored`; `outcome_known: false` never renders stored/processed; the
writer outcomes each render honestly; the reconciler agrees with the 2xx contract on the delete path; #3551's writer parity;
#3555's count-filter assertion; new columns appended at the end; a **partial** turn write is never reported ok) ·
`test_capture_session.py` (full ontology shape) · `capturedSessions.test.js` (the derivations; the screen's **loading / fetch-error+retry / empty / `outcome_known:false`** states
— #3520's contract) · dashboard e2e (the status
region is a polite live region)

**J7 — Opt-out.**
`recording on → turns spooled → off-switch flipped → new capture BLOCKED with 409 + honest copy → already-captured
sessions remain → re-enable works`
→ **Opt-out watermark:** the pre-opt-out backlog is delivered, off-window turns are dropped with an honest recorded
status, the hook re-reads the flag **per turn** — not once at factory time — and stops spooling while opted out so the
spool cap cannot evict pre-opt-out data. **The 409 has three disjoint classes, told apart by a three-way** wire discriminator the
client keys on (a field, never the bare status code): the **terminal** one (an off-window turn) is **dropped exactly once with the
cursor advancing past it**, increments the honest `N dropped` counter, and leaves later records in the same file delivering; the
**transient** one — recording off, will be re-enabled — stays **retryable, never dead-lettered, cursor not advanced**; the **in-flight**
one — a session already being captured (`Retry-After: 30`), reachable via the hook POST racing the sweep — is **retryable, never
dead-lettered, cursor not advanced**, with a **released-barrier test** and a **mutation control removing the discriminator**; every
**409 raise site in the capture path is enumerated BY FUNCTION**. A
**failed recording-flag read is fail-closed**: the hook stops spooling, surfaces the error, proves the
already-spooled on-window prefix intact, and takes a **fresh verdict** when the read recovers — never resuming a stale
one. Precedence is the **tree's**, not the reverse: a **team-level OFF master-kills a graph override of `true`**
(`tests/test_delivery_tenancy.py::test_team_off_master_kill_beats_graph_override`), so the 409 names the deciding layer,
and the watermark comparison is **clock-domain-explicit** — a client-supplied timestamp can never widen or narrow the
dropped window, asserted again under an injected ±1h client clock offset, with a negative control.
*Tests:* `test_capture_session.py` (409 not 403; opt-out writes nothing; the **SDK** path refuses too —
`test_sdk_capture_session_refuses_when_recording_disabled` + a flag-on positive control; an in-flight capture is
rejected when the off-switch PATCH lands; existing sessions survive; re-enable restores the prior install state;
`test_optout_watermark_backlog_delivered_and_window_dropped`;
`test_hook_stops_spooling_while_opted_out`; `test_graph_layer_409_surface_is_named`; `test_offwindow_409_dropped_once_cursor_advances_and_is_counted` (terminal; later
records still deliver); `test_transient_409_retryable_not_deadlettered`;
`test_flag_read_failure_fails_closed_and_takes_fresh_verdict`; the **four-combination** team on/off × override
true/false/null assertion over `tests/test_delivery_tenancy.py` with the decisive layer reported) ·
`test_q3_decline_then_reenable_consents` · `captureStatus.test.js` (off
wins when recording unset) · dashboard e2e (the **dashboard** off-switch copy names the 409 consequence and is
announced as a polite live region — the agent-facing 409 stays quiet)

**J8 — Self-hosted variant of J1–J7 above.**
`J1 without a dashboard — the surface is the SKILL.md step + tool surface: **R6's** agent-settable enable and state read, the honest unsupported reason (R7) and the demand record (R2) transfer; the grid's presentation clauses
(contrast, badge, sr-only) do not · J2 via stdio + probe to the API URL · J3 via the install-ask section + MCP tool ·
J4 → local JSONL, Telegram skipped when the env is unset · J5 identical (hook + store-sync are local) · J6 identical
(local graph) · J7 via the recording tool`
*Tests:* `test_data_source_capture.py::test_selfhosted_*`, `test_selfhosted_j1_mirror` (every enumerated requirement —
R1, R2, R6, R7 and **R9's two clauses via their self-hosted carrier: the install-ask closing summary and a tool-surface state
read naming each unfinished item plus its re-run path** — asserted in its self-hosted form; the **enumerated set is a pinned
literal** and any that genuinely cannot transfer sits in a closed, named allowlist asserted **by value** against it — the prose
list and the test's list are identical), `test_session_capture_e2e.py` (stdio
honest error; the off-switch blocks the self-hosted recording tool too), #3557's shared-projection parity + field-enumeration
test · ops `ops-selfhosted-parity-run`

**J9 — Agent-settable enable/connect.**
`agent calls the tool surface to enable an existing integration → the state read reflects it → the grid renders
the same state`
*Tests:* the parametrized `test_existing_integrations_agent_settable` (every existing integration has an enable path **and** a
state read), `test_docs_index_tool_registered` / `_enqueues_job` / `_gates_honored`,
`test_tool_and_dashboard_enable_write_same_keys`, the **tool-path three-class partition** test (#3552's partition is the
contract) and the **tool-holder** cases (revoked key, expired key, non-owner member each refused with no state write) — #3540.

**Epic gate.** Full docker lane + carve-out + dashboard e2e (`test_dashboard_onboarding.py`, CI-run once #3513 wires it in) + node unit
suites green, **`dist/` rebuilt as a per-issue convention** — each dashboard-touching issue rebuilds in its own PR **and extends
`distBundle.test.js`'s pinned probe list with this change's new strings** (no "matches a fresh build" instrument exists),
false-promise grep clean, **and the receipt gate green — every ops check named here carries a checked-in receipt** bound to the
**commit identity it was produced at** (equal to, or an ancestor of, the gate's HEAD), a **timestamp inside the gate window**, a
**command string containing the named check id**, and **non-empty output matching the expected marker** (the pinned `N dropped`
line, the exact-1 `:Session` line); the receipt is **emitted by the check itself — exit code + stdout — and the gate asserts both**,
with a **mutation control proving the gate goes red on a stale, SHA-mismatched or fabricated receipt**. Each owning issue records its
own receipt; **#3515 owns the gate test**. Every new Python test file named here must be registered in `config/ci-surfaces.yml`
(the `manifest-integrity` job fails otherwise), and process-spawning / concurrency rows need `slow_files` classification. Ops check
`ops-release-full-suite`. **S0's proving step** is #3539's own acceptance in its own file set (`test_github_map` / `test_github_indexer`
/ `test_github_index_lifecycle` / `test_docs_fetcher` / `test_index_docs_api.py`, which already pins the unchanged-rerun-adds-0-Documents
criterion) with a re-run on an unchanged repo producing 0 new nodes.

**Traceability — a journey with no test is visible here.** J1 new · J2 partial (claude probe exists; pi emitter, negative control,
floor, lane marker new) · J3 new · J4 new · J5 new (the worker test file does not exist) · J6 partial (`test_capture_session.py`
exists; FTS + status files new) · J7 partial (the 409 gate exists; journey assertions new) · J8 partial · J9 new. **No journey
yet has a complete automated proving test** — the honest state, and why the epic is not done until §5 is green.
---

## 6. Deferrals, follow-ups and open decisions

**Filed with an owner + trigger (not intentions).**

- **#3519 — semantic transcript retrieval.** Deferred: turn-Point embedding is a retrieval-quality capability the goal does not
  require; the alternative is embedding inside the shared write primitive with a pending marker and a re-embed path (cost: one
  task plus an embedding dependency on the capture write path). **Trigger: the dated gate 2026-12-14** (re-baselined in #3519). Owner: epistemic-team.
- **#3541 — the Drive/Slack/Notion connector/backend build** — out of scope here; the tiles ship as coming-soon in #3513, so no tile is
  deferred. Owner: epistemic-team. **Trigger: the monthly onboarding-cohort review — a receipted ops check owned by #3514, runnable
  against R2's cross-org demand count, with the deferral instrument's scheduled evaluation flagging while #3541 is open — N=5 orgs.**
- **#3542 — codex session-end capture** — #1714's Target is amended to pi + claude. #3542 must land before the
  `codex: false` row flips to supported; the trip is §4's assertion, and the **dated gate in the deferral table** (a constant
  assertion alone can never trigger the work). Owner: the #3516 workstream.
- **The research brief** still presents the withdrawn 403 consent contract. **Owner: #3517**, as an **explicit acceptance
  item**: its false-promise grep (whose file set includes the brief) is the trip, so #3517's acceptance requires the banner.
  Not "the same PR", and **not** a #3539 merge-blocking condition.

**Open decisions (escalated, not invented).**

1. **Whether CLI backfill turns with no client timestamp are admissible at all.** Admitting them weakens the store-proven floor;
refusing them may reject legitimate legacy imports. Current position: admitted, recorded, floor disabled for that turn.
2. **The "delete captured sessions" path** promised in the copy: the human decides whether the existing delete path satisfies
that promise, and whether a verification or a new dependency issue is the right closure.
3. **Splitting this epic** — §3 carries six slices, **17 in-epic issues** and **3 deferred/follow-up rows** (§4); execution shape (lane-per-PR vs one batch) is a human judgement.

---

## 7. Review record

**Tier: High — 4 proportional reviewers** (Structural & Efficiency, Integration, **UX Coherence**, **Failure Mode
Auditor**) **+ the advisory Duplication & Architecture reviewer; cap 10 cycles** — `Complexity: complex` maps to
**High** on the
plan-review crosswalk. The earlier **Low-Medium** run (2 reviewers, cap 3) and its `cycles=9, status=clean` stamp are **deleted,
not annotated**: 9 cycles is past that cap, so the exit required an escalation, and **UX Coherence** / **Failure Mode Auditor**
never ran against text carrying UX (R1, R4, R7) and failure-mode (R5, R8, R11) requirements.

**Cross-artifact corrections required — each child body must match this epic before its implementation lands.** `#3515`: the **409
taxonomy** (three classes, J7) and drop its `state_version` claim; `#3551`: the **turn-record contract** (entry id + role + content +
position) with its `session_import.parsers` implementation and `tortoise sessions import` consumer — **body and complexity corrected
before dispatch** — plus the **real Pi read branch, pinned verbatim Pi fixture, 0-turn fail-closed guard and `SOURCE_PATTERNS`
registration**, and the `sdk.py` "never drift" docstring; `#3516`: drop **only** its "#3540 reads" claim — the client-side `autoCapture`
enablement **stays**, named canonically (config key + store) with the atomic-write requirement; `#3517`: the install-ask placement before
§5, the **capture-disclosure template + single source of truth + agent-prompt render**, and the **research-brief banner it authors** (its
false-promise grep is the trip; **not** #3539's acceptance); `#3513`: the renumber's client surfaces and the dialog split from `#3514`;
`#3514`: **no posting primary** ("Notify me" is body copy only); `#3540`: the tool-description rename in
`tortoise/tool_registry.py`; `#3552`: **rewriting `test_state_keys_registered_parametrized`** (client-writable
keys keep the 200 round-trip; server-owned keys gain a rejection assertion **and** the server-side write path keeping them populated),
`_ALLOWED_STATE_KEYS` deriving from the **union**, and the **`github_org` direction** (drop the DEFAULT-only dead keys **or** classify
them server-owned; never union them into the client-writable set while `_github_credentials` reads the teams column); `#3553`: the
`(toggle, receipt)` pair (the `(clicks, …)` pair is **stale**) and jsonb-then-graph convergence; `#3512`: the home + **permanently-kept
symlink shim** + the agent-infra-side and packaging deliverables.

**Cycle log — tier High, N=4 proportional reviewers + the advisory #5, fresh context every cycle.**

| Cycle | Proportional findings | Advisory #5 | Exit |
|---|---|---|---|
| 1 | 37 (1 P0 / 23 P1 / 14 P2) | 5 | issues → fix pass |
| 2 | 39 (1 P0 / 19 P1 / 19 P2) | 5 | issues → fix pass |
| 3 | 35 | 5 | issues → fix pass |
| 4 | 54 (4 P0 / 24 P1 / 24 P2) | 5 (2 P0, 3 P1) | issues → fix pass |
| 5 | 52 (6 P0 / 26 P1 / 20 P2) | 5 + 3 suppressed | **not clean — escalated** |
| 6 | 46 (5 P0 / 23 P1 / 18 P2) | 5 (1 P0, 2 P1, 2 P2) | issues → fix pass |
| 7 | 45 (2 P0 / 24 P1 / 19 P2) | 5 (2 P1, 3 P2) | issues → fix pass |
| 8 | 45 (3 P0 / 27 P1 / 15 P2) | 5 (1 P0, 3 P1, 1 P2) | issues → fix pass |

**Cycle 7 — not a strict subset of cycle 6 either**, again dominated by **new, tree-verified** defects; counts falling (54 → 52 → 46 → 45), **not clean**.

**Cycle 8 — not a strict subset of cycle 7 either**, again dominated by new, tree-verified defects (a third 409 class on the capture wire, an
invalidated Pi-reader premise, four test lanes that cannot run what §5 claims); counts keep falling (54 → 52 → 46 → 45 → 44), **not clean**.

**Cycle 8 — not a strict subset of cycle 7 either.** Again dominated by **new, tree-verified** defects — a third 409 class on the capture
wire, an invalidated Pi-reader premise, four test lanes that cannot run what §5 claims; counts keep falling (54 → 52 → 46 → 45 → 44), **not clean**.

**Triage intervention, between cycles 5 and 6 — the recurring findings were the epic holding decisions it does not own.** Each
cycle-5 recurring finding was dispositioned into exactly one bucket — **pushed down** (a comment records the resolution and the new
owner), **resolved here**, or **genuinely human** — recorded in **§8**, after which the loop **re-ran**; the committed stamp
(`cycles=9, status=clean, version=2.3.0`) is **deleted, not annotated** (§7's first paragraph).

---

## 8. Pushed-down resolutions

**Where each recurring finding's decision lives.** A routing record, not a restatement: the decisions are in the owning issue, the
contract in §1–§5. Every **Owner** that is an issue number has a comment on that issue recording the resolution and that it owns it.

| Recurring finding (§7's recurring findings) | Resolution — where the decision now lives | Owner |
|---|---|---|
| Writer-consolidation size + gate-refusal coverage + the single-`MERGE … :Session` assertion | §2 states the shape only, no count; the issue discovers the writer set BY FUNCTION and owns the coverage | **#3551** |
| CAS token — ride the incumbent `version` or record a deliberate second counter | The issue's own scoping pass; it lands any writer it needs in `tortoise/onboarding/state.py` (#3551's primitive must not write onboarding state) | **#3553** |
| The two default dicts do not agree, so the mandated set-equality assertion is red on arrival | The issue owns the **reconciliation**, not only the assertion | **#3552** |
| The no-loss fixture-count oracle is derived from the parser under test | Expected N is a **pinned literal per death mode** in the fixture, never derived from the parser | **#3515** |
| The demand-store writer's unchecked response / missing `org_key` / marker-less dedupe, click-record idempotency, integration-key validation against the tile inventory, the Telegram reuse-or-diverge question and the demand store's read half | All the issue's; §5's J4 rows assert them | **#3514** |
| The store-sync worker's **language** and **CLI entry point**, the **second JSONL substrate**, the spool/fork/undecodable death-mode rows, and the fixture rows (unknown entry kind, no id — **never positional-id synthesis** — duplicate ids) plus the **2xx-with-`capture_ok=false`** decision | All are the issue's; only the **`tortoise store-sync` entry point is an epic-pinned contract (§5)** | **#3515** |
| The tile inventory, the **one-tile-vs-two-sub-entries** presentation of GitHub's two sources, the partial-GitHub-tile label, the Sessions tile's **pointer copy**, the panel's reveal a11y and empty/not-connected states, the "Show more" contract, and the grid's read-model branch | The issue owns each; §2 keeps only the invariant (separately enable-able sources, one OAuth connect; extraction's single home); the deployed `SKILL.md` mirror is not pushed down — each `SKILL.md`-editing issue updates it in its own PR (§3) | **#3513** |
| The source-scan tripwire fails on the deliberately untouched archived wizard block | The tripwire's file set — **derived mechanically** with a **named** exclusion of the archived block and a **seeded-violation control** — is the issue's | **#3513** |
| The `capture-disclosed` FLOW step the new **capture-disclosure** render may silently consume | The **capture disclosure**'s step/state accounting is the issue's | **#3517** |
| The `#3513 ← #3515` edge: dropped — the install status labels are the **existing** `HARNESS_CAPTURE_STATUS_LABEL` constant | **Resolved here**; §4 drops the edge, §3 keeps only #3517 as #3513's ordering edge | epic — §3/§4 |
| The tool paths' three-class partition and the tool holder's refusal cases | The issue owns them, with #3552's partition as the contract | **#3540** |
| The D9 retrofit audit of existing `:Session` records (short turn counts under `capture_ok=true`; pre- vs post-verification-read ordering) | The issue owns the quantification and the correct-in-place-vs-recorded-gap decision | **#3554** |
| The unsupported row carried two contradictory contracts — inert, or a focusable `aria-disabled` no-op | **Resolved here** from evidence on record: decision #3 plus R1/R7 give one contract — **reachable and self-announcing, never inert** (reason text unconditional, at full contrast, in the a11y tree) with **no activation control of any kind**. The contradictory clause is deleted from J2 | epic — §1 R1/R7, §5 J2 |
| §7's own correction list contradicted R11 (a stale `FLOW_KEYS` + `STEP_IDS` token); the dependency-edge inconsistencies (`#3513 ← #3517`, `#3540 ← #3516`, `#3513 ← #3516`); the receipt gate missing from the epic gate's list | **Resolved here** — the stale note is deleted and R11's canonical vocabulary governs; §3's edge set is the single statement; the receipt gate is named in the epic gate | epic — §3/§5/§7 |
| Whether the epic runs as **one batch** or is **split for execution** (lane-per-PR vs one pass) | **Unanswered — genuinely human.** The answer changes execution shape, not the plan's content, and neither option is safe to assume | **HUMAN** |
| **#3516** — the local-config substrate's failure modes (racing writers, torn write, malformed / empty / non-UTF8 / absent config) | All the issue's, each with a pinned honest outcome — never a silent capture-off | **#3516** |
| **#3515** — fixture rows and one client decision | Fixtures for an **unknown entry kind**, an **entry with no id** and **duplicate ids**; the **2xx with `capture_ok = false`** client decision | **#3515** |
| **#3552** — the off-switch role matrix (owner / non-owner member / revoked key / expired key / session-user vs key-authed) | The matrix is the issue's; a refusal leaves state untouched | **#3552** |
| **#3553** — jsonb-then-graph partial failure, process-local `_org_lock`, and the CAS's stale-version behaviour | All the issue's; the mutation control is removal of the predicate | **#3553** |
| **#3514** — the hung-transport case | A send that neither returns nor raises: the click request completes 2xx within a pinned budget, the record is already persisted, the timeout constant is pinned, and N such clicks are not serialized behind the hung transport | **#3514** |
| **#3512** — the agent-infra-side deliverable | The separate repo's issue/PR reducing `agent-infra/extensions/` to the shim, the dev-machine symlink repoint, the **PR ordering** so no window exists where capture is installed but unwired, plus the **packaging/install surface** for the new home (`pyproject.toml` package-data, wheel content, the installer/`cp` path, the packaging gate) | **#3512** |
| A Pi session file's records are `type:"message"`, which `parse_pi = parse_codex` matches none of — a silent 0-turn parse | Real Pi-shaped parse branch, checked-in verbatim Pi fixture for #3551/#3515, fail-closed 0-turn guard | **#3551** |
| `entity_links_attempted` / `entity_links_created` are written inline by two drifted writers (per-capture vs per-index-run), so the counter is non-monotone | Both properties in #3551's contract; one cross-path parity/monotonicity test replaces the per-path assertions; #3554 quantifies the under-count | **#3551** |
| **#3520** — the Captured-sessions screen's own state contract (loading / fetch-error+retry / empty / `outcome_known:false`) | The screen's states are the issue's, asserted in §5 J6 | **#3520** |

---

## Plan Review — Requires Human Input

**Status:** **not clean after 8 cycles** — and explicitly neither of the skill's two stamps. `status=clean` would be false; `capped`/`stalled` would also be false (the tier cap is **10**, so it was not reached, and no stuckness detector fired). **No signature is written.** The machine-readable `operations/logs/cycle-status.yaml` is deliberately **not** created — this commit carries the plan file only — so the same fields are recorded here:

```yaml
exit_reason: escalated-not-capped
cycles: 8
issues_per_cycle: [37, 39, 35, 54, 52, 46, 45, 45]
plan_modified_per_cycle: [true, true, true, true, true, true, true, true]
detector_fired: ''
fingerprint_recurrence_last_cycle: null
```

**Why it stopped here rather than at the cap — the honest measurement.** **Recurrence is zero:** no finding from cycle N came back in cycle N+1 as the same defect, every fix pass resolved what it addressed, and each cycle's set is made of **new, tree-verified** defects (cycle 6: 5 P0; cycle 7: 2; cycle 8: 2 — a third 409 class on the capture wire, and an invalidated premise: `parse_pi = parse_codex` silently parses **0 turns** on real Pi session files). The counts fall (54→52→46→45→45) without approaching zero, because each fix pass adds normative text — the artifact the reviewers read — so every addition is new surface. `honest-stuck` does not fire (the count is not non-decreasing), `convergence` does not fire (no cycle's set is a strict subset of the previous cycle's), and `zero-progress` does not fire (the document changed every cycle). Cycles **9 and 10 were not run**: on a 639-line epic this is a fixed point, and the surviving defects are specific and actionable rather than recurring. Whether to spend them is a human call — this is an **escalation, never a completion**.

**Unresolved after cycle 8 (each cycle's attempted fixes are in the cycle log).**

1. **[P1]** The 409 raise-site enumeration **BY FUNCTION** is required by J7 but is #3515's to produce, not the epic's.
2. **[P1]** J5's per-code truth table (which code drops, retries or dead-letters) is stated as a shape, with its content #3515's.
3. **[P1]** #3542's deferral carries a dated gate but no literal date (only #3519 has one).
4. **[P1]** The wizard-mount harness-row rule and the Captured-sessions state contract are owned and stated, but not asserted in J2 (they are asserted in J1/J6).
5. **[P1]** "Every write site emits a registered key" is a stated invariant with a seeded-typo control, but no four-site parametrized test is named.
6. **[P1]** The integration-key cross-surface assertion is a #3513/#3540 deliverable, not in J1/J2's list.
7. **[P2]** The §5 layers claim ("every node test file named in §5 is executed by a named CI job") is itself unasserted.
8. **[P2]** `test_selfhosted_j1_mirror`'s owning issue is stated in §5 but not in §4's Journeys column.
9. **[P2]** The multi-process click-dedupe case (two API processes against one store) is #3514's.
10. **[HUMAN]** One batch vs split for execution (§6, open decision 3), plus §6's two other open decisions (CLI-backfill timestamp admissibility; the "delete captured sessions" closure).

**Triage record — the intervention that produced §8.** The cycle-5 recurring register was dispositioned **13 pushed down / 4 resolved here / 1 human**, every pushed-down row carrying a comment on its owning issue recording the resolution and the new ownership: **#3512, #3513, #3514, #3515, #3516, #3517, #3540, #3551, #3552, #3553, #3554** (16 comments, including the cycle-6/7 follow-ups on the same issues). Cycle 6's findings were dispositioned the same way (6 further push-downs, 10 more comments).

