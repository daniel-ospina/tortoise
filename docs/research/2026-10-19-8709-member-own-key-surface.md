---
title: "Member own-key surface — #8709 decision brief (the member's own credential does not exist to be shown)"
type: decision
domain: ux
doc_status: live
created: 2026-10-19
issue: 8709
related: "#2446, #8708, #4628, #8692, #2297, #2535, #2166, #2426, #2380, #2307, #2366, #2427"
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise, dashboard, hosted-api, api-keys
---

# Member own-key surface — decision brief (#8709)

Measured at: feat/8709-tm@5a19c88ec2ae333eea64dfa48860ad9c9cb70f14 on 2026-10-19

**Trigger:** #8709 (item 2 of #2446). The server lane landed in PR #8708 (merged
`ad13e491d`): `POST /v1/session/key` accepts `replace_key_id` (atomic one-step replace of a
held session credential) and `PATCH /v1/team/keys/{id}` admits the member **own-key slice**
(enable/disable only). #8709 owns the **UX decision** those two verbs have no surface behind.

**Routing:** `--domain=ux` (the issue's own label). This repo has no `docs/07_ux/wiki/`
tree, so the brief is filed to `docs/research/` — the same home as the #2784 per-graph
backups UX brief.

**Depth:** Standard. Internal measurement is primary and conclusive (§1–§2).

**External research — degraded, and why it costs nothing here.** `PERPLEXITY_API_KEY` is
unset in this worktree, so the research skill's Step 3 could not run. It is not needed: the
comparables this decision rests on were already gathered and validated by the #2427 brief
(`docs/research/2026-10-15-2427-team-permission-model.md` §3, convergent pattern 1 — Datadog
splits org-owned API keys from user-owned personal access tokens; GitHub fine-grained PATs
are user-owned and live in **user** settings; Supabase scoped personal tokens cannot exceed
their creator's access). That pattern is reused here rather than re-derived.

---

## 0. Problem reframing

**Reframed problem statement (as the issue states it):** *A dashboard member trying to act on
a credential they hold cannot do it from the dashboard, and is shown an owner-only surface as
their only path.*

**5 Whys:**

1. Why can a member not replace or turn off "their" key? — Every row action on the API Keys
   tab is `isOwnerAdmin` render-gated.
2. Why is it render-gated? — The tab is the **organization** inventory: `POST/DELETE
   /v1/team/keys` are `_require_owner_admin` (POLICY A, #2297), and #2166 decided the table
   shows **durable product keys only** — session credentials are excluded by `isManagedKey`.
3. Why is there no second surface for the member's own credential? — Because until PR #8708
   there was no *action* to put on one, and because ADR-010 (#2167/#2246) deliberately
   removed the dashboard's session-key machinery.
4. Why did ADR-010 remove it? — Because the component was **UI-dead**: `mintSessionKey` had
   four automatic callers that nobody needed, and `recoverKey` backed an affordance deleted
   in #1148. The dashboard is session-cookie-only; it never *holds* a key.
5. Why does that leave the member with nothing? — Because the credential the surface would
   list has to be **minted by some client**, and (measured, §2) **no client mints one**.

**How Might We:**

- HMW give a member a working credential remedy **without** inventing a credential that does
  not exist?
- HMW put replace/turn-off **where the credential is minted and held**, instead of in a
  browser that never holds keys?
- HMW keep the API Keys tab's durable-only doctrine (#2166) intact while still letting a
  member see what is theirs?

**Assumption map (the issue's premises, tagged):**

| Premise as filed | Tag |
|---|---|
| A dashboard member has no key row anywhere | `[validated]` — but for a stronger reason (§2) |
| The dashboard never mints a session credential | `[validated]` — `main.jsx:4738-4747` |
| `isManagedKey` excludes `created_via='bootstrap'` | `[validated]` — `sessionKey.js:60-63` |
| **"the only member-owned credential is invisible"** (i.e. it exists, merely filtered) | `[invalidated]` — **nothing mints it** (§2) |
| A member cannot mint a durable key | `[validated]` — `hosted_api.py:27017`, `:27378`, #2535 HARD STOP |
| The #8708 actions are reachable "from the API/CLI" | `[partially invalidated]` — **API only**; no CLI/SDK path exists (§2) |

**Reverse the problem:** *What if the dashboard deliberately had no member key surface at
all, and the remedy lived in the client that mints the credential?* That framing survives
every constraint below; the "add a section to the dashboard" framing does not.

---

## 1. Measured state — claim by claim

All line numbers are this tree (`5a19c88ec`).

| # | Claim (issue body) | Measured verdict | Evidence |
|---|---|---|---|
| 1 | "a dashboard member has **no key row at all**" | **TRUE**, stronger than filed: the row cannot exist because nothing mints the class | §2 |
| 2 | "the dashboard never mints a session credential — `mintSessionKey`/`recoverKey` deleted" | **TRUE** | `website/apps/dashboard/src/main.jsx:4738-4747`, `:8145-8150`; pinned by `src/mintTripwire.test.js` |
| 3 | "`isManagedKey` excludes `created_via='bootstrap'`, so even a CLI-minted session credential never renders" | **TRUE for the table** (#2166 decision), **FALSE as a data gap**: the row is already in client state | `sessionKey.js:60-63`; `main.jsx:8156` (`managedKeys`), `main.jsx:5535` (`keys[]` stays **unfiltered**); `GET /v1/team/keys` (`hosted_api.py:12187`) returns every org row **with `created_via` + `created_by`** (`:12236-12262`), and `_require_keys_manage` (`:6335-6349`) admits **session faces** (no `key_id`) — so a member session already receives its own rows |
| 4 | "the member's API Keys page renders the notice + `No keys yet.`" | **PARTLY STALE.** The table is **not** role-filtered: a member sees the **organization's** durable rows read-only (only the *actions* are `isOwnerAdmin`-gated). `No keys yet.` renders only when the **org** has no durable rows | `main.jsx:11174` (notice), `:11231` (`No keys yet.`), `:11233-11345` (rows + action gate), `:8156` |
| 5 | "a member cannot mint a durable key (`recovery` POLICY A-gated; `keys.mint.own` needs the #2297 reopen, HARD STOP in #2535)" | **TRUE** | `hosted_api.py:27017` and `:27378` (`purpose == "recovery"` → `_require_owner_admin`), #2380, #2535 |
| 6 | "the #8708 actions are reachable only from the API/CLI" | **API only** — no CLI/SDK consumer exists | §2 |

**Also measured (and relevant to any future surface):**

- The holder predicate the server enforces is `_session_key_holder` = `created_by` **iff**
  `created_via='bootstrap'`, else `None` (`hosted_api.py:6363-6396`). `created_by` is the
  **minter**, never the holder, for durable rows (`MEMORY.md:47`).
- The replace lane refuses fail-closed: 404 / 403 "Not your API key" / 409 (revoked or
  expired) **before any mint** (`hosted_api.py:26790-26845`); the replacement inherits the
  displaced row's **absolute** expiry, never widening it (`:27064` registry, `:27411` Supabase).
- The replace response is additive and present only when a replace ran: `key`, `key_prefix`,
  `expires_at`, `key_id`, `replaced_key_id`, `replaced_revoked`, optional `warning`
  (`:27284-27299`).
- The **registry/selfhost** lane refuses the member's turn-off rather than echoing a no-op:
  409 "Turning off a key is not available on this deployment" (`:12724-12734`). Any surface
  must render that refusal, never a success.
- The dashboard's own session profile carries the caller's user id
  (`functions/api/session.ts:80,87`) but `main.jsx` **drops it** (`:4888-4891` keeps only
  `display_name`/`email`). Nothing else client-side knows the caller's id.
- `GET /v1/team/keys` is member-open **by decision** (#1828), which is why
  `hosted_api.py:26816-26818` can say "a member may LIST every org key, so the 404/403 split
  leaks nothing they cannot already read". The narrowing is `keys.read.own` in **#8692**
  (phase 3), explicitly *not* decided here.

---

## 2. The finding: the population is zero

A member's own credential class is `created_via='bootstrap'` — minted only by
`POST /v1/session/key`. **No client in this repo calls that endpoint.** Exhaustive grep over
code (`.py/.js/.jsx/.ts/.html/.sh`, excluding `docs/` and `tests/`):

| File | What it is |
|---|---|
| `tortoise/hosted_api.py` | the handler itself + its own docstrings |
| `tortoise/sdk.py:27335` | a **docstring** mentioning it |
| `tortoise/oauth.py:29` | a **docstring** ("the bridge stays for dashboard flows") |
| `website/apps/dashboard/src/main.jsx` | **comments only** — the consumer was deleted (#2167/#2246) |
| `website/apps/dashboard/src/mintTripwire.test.js` | the static guard that keeps it deleted |

Everything else that exercises it is `tests/` (`134` hits). So:

1. **A member cannot obtain a bootstrap credential from any product surface.** Only a
   hand-written script against the public REST endpoint can. The class is API-reachable and
   **not product-reachable**.
2. **The member's real, in-product credential today is an *owner-minted durable key* they
   paste** into the connect step (`MemberEmptyStateKeyNote`'s "ask an owner or admin" arm).
   That key is **org-owned**: `created_by` is the owner, `_session_key_holder` returns `None`,
   and #2427 §4.2 makes it deliberately unmanageable by whoever merely *holds a copy*
   ("a shared key must never be rotatable by a member who happens to hold a copy").
3. Therefore **#2446 item 2's premise — "the only member-owned credential is invisible" — is
   false of this tree**: the member-owned credential does not exist yet. A surface that lists
   it would render its empty state for **every** member, forever, until a mint ships; and the
   replace/turn-off actions would act on nothing.

That is precisely the failure this repo has already deleted three times: `mintSessionKey`
(#2167), `recoverKey` (#2246), and the "Lost your key? Generate a new one" affordance (#1148)
— each removed because it was **UI-dead**. Building another one would be a fourth.

**The real member gap is upstream of the surface**, and it has two known answers, both
deliberate and both out of #8709's scope: `api_keys.owner_user_id` (the **holder** column,
phase 3 = #8692) and `keys.mint.own` (a POLICY A reopen of #2297, recorded as a HARD STOP
in #2535).

---

## 3. The decision

**D1 — Do not build a member key surface in the dashboard now.** There is no row it could
list (no mint) and no action it could perform on a real credential. Shipping it would create
a permanently-empty surface whose copy promises a credential the product cannot issue.

**D2 — The precondition is the mint, on a surface a member actually uses.** File it (done:
§5). The rule that then governs placement is **the control follows the mint** — the surface
that issues a credential is the surface that offers replace and turn-off. That satisfies
the indicator of #2446 item 2 ("a member can see and act on the credential(s) they hold")
without inventing a browser-held credential.

**D3 — When it exists, the dashboard home for it is the Profile tab, not the API Keys tab.**
Reasons, in order of weight:

1. **#2166 is a recorded decision**: the API Keys table shows durable product keys; session
   credentials are excluded. A second table on that page re-creates the confusion #2166
   removed.
2. **The comparables put user-owned credentials on the user surface** (Datadog PATs vs org
   API keys; GitHub PATs under *user* settings) — and the holder rule (#2427 §4.2) is
   literally user-owned vs org-owned.
3. **The Profile tab already owns "your credentials"** — login methods — and already hosts a
   second session-gated section (`DeleteAccountSection`, `main.jsx:11060-11082`), so the
   section pattern exists.
4. **#4628's own lesson**: a member must never be handed a surface whose copy says they
   cannot act there. The API Keys tab renders exactly that notice for members
   (`main.jsx:11174`); putting the member's *own* actionable rows beneath it would put "you
   can't act here" and "you can act here" on one screen.

**D4 — No dashboard mint and no browser reveal without an explicit ADR-010 amendment.** A
dashboard "Replace key" is a `POST /v1/session/key` call *and* a show-once reveal of a session
credential's plaintext. It is not a filter tweak: `mintTripwire.test.js:72-78` **asserts that
`main.jsx` contains zero `v1/session/key` occurrences**, and `:38-45` asserts zero
bootstrap-purpose mints — guards that exist because ADR-010 made the browser hold no key. The
full spec *is* written (§4.6) so the owner's decision is one word, and the exact tripwire
amendment is named there. It is **rejected as the default** because it would (a) create the
population it lists, (b) put a 24h credential's plaintext into a browser that never holds
keys, and (c) buy little: for a member credential the compromise remedy is already both
steps — kill it, then mint a fresh one (bootstrap is member-open and cap-exempt).

**D5 — The API Keys tab keeps its current posture.** It stays the org inventory; the
member-facing notice stays. Its only obligation is #4628's, already landed: it must never be
a member's *only* path — `GraphMissingEmptyStateActions` sends a member to the connect step.

**D6 — Whatever ships must render the server's refusal verbatim.** The registry lane answers
the member's turn-off with a 409 ("this lane has no per-key disable"); a surface that echoed
success there would report a security action that did not happen
(`hosted_api.py:12724-12734`).

### Options evaluated

| Option | What it is | Verdict |
|---|---|---|
| **A — Member section on the API Keys tab, now** | List own bootstrap rows beside the managed table | **Rejected**: empty for every member (§2); contradicts #2166 by re-adding session credentials to that page |
| **B — Member section on the Profile tab, now** | Same rows, personal surface | **Rejected for now** — right home (D3), wrong time (no population). The spec is §4 and is ready the moment a mint lands |
| **C — Control follows the mint** | Mint + replace + turn-off on the surface that issues the credential; dashboard section only if/when that surface is the dashboard | **RECOMMENDED** — filed as #9179 |
| **D — Dashboard mint + show-once reveal** | Amend ADR-010's tripwire; mint and reveal in the browser | **Rejected as default** (D4). Spec kept at §4.6 for an explicit decision |
| **E — Do nothing, close as unreachable** | Close #8709 as "not a defect" | **Rejected**: the *symptom* is real (a member has no self-service remedy) and only the *cause* moved; it needs the precondition filed, not silence |

### The contradiction test (AGENTS.md)

- **#2166** (table = durable only) → this decision *respects* it: the member surface is a
  different page and a different component, never a filter tweak on that table.
- **ADR-010 / #2167 / #2246** (the browser holds no key; no dashboard session-key mint) →
  *respected*: no mint, no reveal on the dashboard without reopening the decision.
- **#1828** (`GET /v1/team/keys` member-open) → *not* treated as a defect. It is a recorded
  posture; its narrowing is #8692's `keys.read.own`, explicitly not decided here. Any future
  own-row surface must therefore filter **client-side** today *and* keep working when #8692
  slices the payload (a documented seam, not a hidden dependency).
- **#2427 §4.2 holder rule** → *respected and reused as the only role derivation* (§4.4).

No recorded decision is overridden by this brief, so no `OVERRIDES:` line is required.

---

## 4. Surface spec (ready to implement when the population exists)

This is the *verbatim* answer to the issue's items 1–3. It is deliberately implementation-
ready so the follow-up issue needs no further design decision.

### 4.1 Component and placement

- A new section rendered on `tab === 'profile'`, as a **sibling of `ProfileTab`** in the same
  way `DeleteAccountSection` is (`main.jsx:11060-11082`) — presentational component in
  `profile.jsx`, state and handlers from `main.jsx`.
- Heading: **`Your session credentials`**
- Lead line: `These are the credentials Tortoise issued to you for agents and scripts. They
  are yours — an owner or admin does not need to act on them.`
- Empty state (the important one): `No session credentials yet. A credential appears here
  when you sign an agent in with Tortoise from the CLI or SDK — your dashboard sign-in is a
  separate credential and never appears in a key list.`

### 4.2 Rows (the holder rule, client-side)

`ownSessionRows(keys, sessionUserId)` — a **pure** selector in a new
`website/apps/dashboard/src/ownSessionKeys.js` (co-located `node --test`, `sessionKey.js`
precedent):

```text
own ⟺ keys.filter(k => k.created_via === 'bootstrap' && k.created_by === sessionUserId)
```

- `created_by` is the **minter**, so the class check is load-bearing: a durable row a member
  once minted is **org-owned** and must **not** appear (`MEMORY.md:47`, #2427 §4.2).
- `sessionUserId` missing/falsy → **empty list** (fail-closed; never list rows you cannot
  attribute).
- Revoked and expired rows **stay listed** with their status (tombstone doctrine, #2426).

Columns (reuse the existing cells, no new formatters): `Name`, `Prefix`, `Created`,
`Last used` (`formatRelativeTime` + absolute `title`), `Expires` (`fmtExpiry` — amber ≤14d,
terminal `expired`), `Status` (`active` / `disabled` / `expired` / `revoked`), `Actions`.

### 4.3 Actions

| Action | Server call | Copy / behaviour |
|---|---|---|
| **Turn off** | `PATCH /v1/team/keys/{id}` `{enabled:false}` (session auth, `?org_id=` pinned) | `.key-toggle` switch, `role="switch"`, `aria-checked` — **no `confirm()`**: it is reversible in one click. Consequence line, always visible while the switch is on: `Turning this credential off stops the agents using it from authenticating until you turn it back on.` |
| **Turn on** | same, `{enabled:true}` | Same switch. |
| **Replace key** | *not on the dashboard* (D4) — the section names the client's command with a copy button | Row-level affordance text: `To replace it, sign the agent in again from the CLI or SDK — the new credential is shown once, and the old one keeps working until you turn it off.` |
| Expired row | none | Status `expired` + `This credential has expired — sign the agent in again to get a new one.` |

Failure arms, all honest (D6): 403 "Not your API key" → re-read the list and show
`That credential is no longer yours to change — the list has been refreshed.`; 409 revoked →
`This credential was already revoked.`; 409 registry lane → render the server's message
verbatim (`Turning off a key is not available on this deployment — …`); network → revert the
optimistic switch and keep the row (mirror `toggleKeyEnabled`'s revert,
`main.jsx:8013-8046`).

### 4.4 Role derivation

**There is no role gate.** The holder rule *is* the authorization, and the server already
enforces it (PR #8708). Consequences the implementer must not undo:

- The section must **not** be wrapped in `isOwnerAdmin` — a member is the intended actor.
  (Every existing row action on the API Keys tab is `isOwnerAdmin`-gated; copying that pattern
  here is the bug this section exists to fix.)
- Owner/admin see their **own** section too (their bootstrap rows are theirs); their
  org-owned durable rows remain on the API Keys tab. The two surfaces never mix rows.
- The client needs its own user id: `functions/api/session.ts:87` already returns `id` from
  the BFF; `main.jsx:4888` must retain it (`sessionMetaRef.current.id`). **No server change.**
- Registry/selfhost (`identityInv.unsupported`) → **hide the section**, the posture the login-
  methods section already takes; the lane has no per-key disable to act on.

### 4.5 Why the client filter is safe today (and what breaks it later)

`GET /v1/team/keys` is member-open (#1828) and returns **every** org row to a member session
today (§1 row 3), so an own-row filter is *possible* — and that over-disclosure is a
**recorded posture**, not a bug (its narrowing is `keys.read.own`, #8692 phase 3). The
implementation must therefore (a) never *depend* on seeing other rows, and (b) put the seam
in the selector, so #8692's slicing changes the payload, not the component.

### 4.6 The deferred half: dashboard `Replace key` (needs ADR-010 reopened)

Only if the owner decides the dashboard is the mint home. Then, exactly:

- Call `POST /v1/session/key` through the same-origin proxy (`/api/v1/*`,
  `functions/api/v1/[[path]].ts` — a general JSON proxy; no route change needed) with
  `{purpose: 'bootstrap', replace_key_id: <row.id>}`.
- **Confirm** (mirroring `regenerateKey`'s shape, `main.jsx:7626-7636`):
  `Replace ${keyRowDisclosure(row, 'this session credential')}? A new credential is created
  (shown once, with the same expiry) and this one is revoked — any agent using it will stop
  working until you give it the new one.`
- **Reveal** (show once; never stored — the `revealablePlaintext` gateway,
  `sessionKey.js:36-38`, is the only reader):
  `Your new credential (shown once):` + `<code class="key-value">` +
  `expires {fmtExpiryDate(expires_at)} — the same instant as the credential it replaced` +
  `Copy & done`.
  Render the server's `warning` verbatim when `replaced_revoked === false` (both keys live).
- **Tripwire amendment required**: `mintTripwire.test.js:38-45` (`purpose: 'bootstrap'`
  literal) and `:72-78` (`v1/session/key` zero-occurrence) must be re-expressed as
  "no **automatic** mint; exactly one user-initiated replace path", or the guard fails and —
  worse — the guard's meaning (the browser holds no key) silently stops being enforced.
  That is the decision, and it is why this half is not implemented by default.

### 4.7 Tests the follow-up must carry

| Test | Kind |
|---|---|
| `ownSessionKeys.test.js` — truth table: bootstrap+own → listed; bootstrap+other → not; **durable + own `created_by` → not**; revoked/expired → listed with status; null `sessionUserId` → empty | pure, `node --test` |
| `main.jsx` static tripwire — the section's lead line, empty state, and consequence line exist verbatim; the section is **not** inside an `isOwnerAdmin` render gate | source-text tripwire (`keyConfirmDisclosureTripwire` precedent) |
| Toggle revert on 4xx/network | DOM/exec test (`heldRowTruthExec` precedent) |

Any new test file must be registered in `config/ci-surfaces.yml` (unregistered files are
never selected on the relevant diff and `manifest-integrity` fails).

---

## 5. What this brief closes, and what it files

- **#8709** closes as the **decision**: items 1–3 answered (§3, §4); the *implementation* is
  correctly gated on a precondition that does not exist yet.
- **Filed by this brief — #9179**: the precondition — *no client mints a member's own session
  credential, so #2446 item 2 has no population* — with the accept criteria the mint must
  meet (member-open mint on an agent-connect surface, plus replace + turn-off there).
- **Filed by this brief — #9180**: `identity.js::createdByTierClass` has **no consumer**
  (exported, tested, never rendered — the "keys tier display" it was written for never
  shipped). Wire it into the own-credential status cell when §4 lands, or delete it.
  Precedent: `reauthStale` carries the same finding against #3105.
- **Not re-filed, deliberately** (contradiction test): the member-visible org inventory
  (#1828 posture, narrowing = #8692), and the absence of `keys.mint.own` (POLICY A reopen of
  #2297, HARD STOP in #2535).
- **#2446 item 2** remains open; #8692 (holder field) stays the phase that makes a *durable*
  member credential possible.

---

## 6. Risks and gaps

1. **The precondition may never land.** A member's durable credential needs both #8692's
   `owner_user_id` and the #2297 reopen; until then "member-owned" means only the ephemeral
   bootstrap class. If the owner decides the dashboard should *be* the mint surface, §4.6 is
   the whole decision and §4.1–§4.5 is the surrounding work.
2. **Re-measure before implementing §4.** This brief is true of `5a19c88ec`. The three facts
   it turns on — no client mint, `keys[]` unfiltered, the tripwire's pin — each have a
   different owner and can move independently.
3. **Over-disclosure while the client filters** (§4.5): the member already receives every org
   row. It is a recorded posture, not a defect, but a future reader may mistake §4.5 for a
   licence to widen it. #8692 is the narrowing, not this surface.
