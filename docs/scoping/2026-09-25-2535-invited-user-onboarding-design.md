---
title: "#2535 — Invited-user onboarding: what 'joining an org' means"
type: engineering
domain: platform
doc_status: live
subjects.team: organisation-design-team
aboutSubjects: invited-user-onboarding, onboarding
aboutObjects: organization-membership, dashboard, api-keys, permissions-model
created: 2026-09-25
---

# Scoping / design — #2535: invited-user onboarding (design before implementation)

**Issue:** [#2535](https://github.com/daniel-ospina/tortoise/issues/2535) — *Invited-user
onboarding — design before implementation* · **Level:** task · **Complexity:** standard · **Lane:**
`docs/2535-invited-user-onboarding-design`

**Search tool (research PREFLIGHT):** `web_search` with `model="sonar"` — **rung 2**. Rung 1
(`mcp_load seo-intelligence`) is **unavailable on this machine**: `Unknown MCP server
'seo-intelligence'`. S9 epistemic-memory query returned `status: "not_configured"` (no usable
`TORTOISE_API_KEY`) — prior claims were not read; this doc is grounded in the code, the issue
threads, and the recorded decisions instead.

---

## 0. Contradiction test — run FIRST, before any candidate was formed

The skill order is fixed: ask *"is there a decision this would contradict?"* **before** cost,
quality, or convergence. Four recorded decisions were found; three reach the candidates below.

| Recorded decision | Where it is recorded | What it governs | Contradiction result |
|---|---|---|---|
| **Invited users skip onboarding and go straight to the dashboard** | Epic **#2534** body → *Key Decisions* (2026-09-07): *"Invited users — skip onboarding entirely, go straight to the dashboard. Issue #2535 will define what invited-user onboarding looks like."* | The invitee's entry experience | **ALIGNS — not a contradiction.** The "obvious answer" (skip) is not an adoption *over* a decision; it **is** the decision. The route is to execute it, not to re-argue it. |
| **POLICY A** — the SESSION lane of key mint / revoke is owner/admin-only | **#2297** owner decision **2026-09-05**; code comment `tortoise/hosted_api.py:8397`; implemented in **#2366** (merged) | Who may create/revoke API keys from a dashboard session | **HARD STOP for one candidate.** Any invitee experience in which the member **mints their own key during onboarding** contradicts this decision. It is **not adoptable here** — the route is the designated evolution: **#2427** (permissions model) → **#2446** (member self-service keys, phase 2). This doc therefore does **not** design member key minting; it names the dependency. |
| **One free organization per person, ownership-based** | **#2789** owner decision **2026-09-10** | Creating a second *free* org | **Constrains copy, not the flow.** Multi-org *membership* is explicitly allowed (a mere collaborator does not count against the allowance). |
| **The invite-JOIN gates stay membership-scoped (not ownership-scoped)** | `tortoise/hosted_api.py:12894-12900` — the `_owned_free_org_ids` docstring: *"the invite-JOIN gates (`_count_active_free_memberships`) still read the membership-scoped count, and #2789's out-of-scope list pins that semantics."* | The 402 a free-capped invitee gets when joining a second free org | **HARD STOP for a "fix".** The asymmetry (create = ownership, join = membership) is deliberate. The join-side 402 at `hosted_api.py:15233` is **not** a bug; do not "fix" it. |

**Searched:** `~/.pi/agent/state/DECISION-LEDGER.md`; this issue (no comments) and #2534 (only a
close-out notice); `docs/plans/**`; `docs/scoping/**`; Tortoise points (S9 unavailable).

**Contradiction-test verdict:** *adopt the skip (decision #2534) · do not cross POLICY A (route via
#2427/#2446) · do not touch the join-side free-cap (#2789).*

---

## 1. Confirmed problem

The issue body states two premises that the code does not support, and one that is now stale.

**(a) "Is every member an 'admin' by default (current behavior)?" — No.** `owner` is the
**creator's** role only. Invited roles are a closed set `admin | member`; the invite default is
`member`. See §2.

**(b) The invitee is "put through the full org-create onboarding" — no longer true.** Sub-issue
**#2538** (PR #2544, merged 2026-09-07) fixed the propagation: the mount-time accept now calls
`loadTeams()` (`main.jsx:3839`), and the wizard is entered **only when the session has zero
organizations** (`main.jsx:4079-4093`). The issue's own *suggested approach* item 1 — "invited users
skip onboarding entirely (go to dashboard)" — is **already shipped**. This is not a bug waiting to
be fixed; it is the current behaviour.

**(c) The confirmed, still-open problem is the converse: the member's own first-run surface is
undefined.** After the (correct) skip, the invitee lands on a dashboard built around owner/admin
affordances.

- The role-aware connect arms live in the **wizard's connect step**
  (`main.jsx:7198-7200` — `isOwnerAdmin ? … : wizardPasteRow`; `main.jsx:7319-7330` — the key-less
  connector arm *"…Claude signs in to Tortoise, so no API key is needed"* and the non-owner arm
  *"Paste an API key to connect your agent."*). A member **can** reach them — but only by entering
  the creator's wizard: the always-visible **Setup** button is unconditional
  (`main.jsx:8407` → `setWizardStep(0); setWelcomeMode(true)`), and the **re-entry card** is
  role-independent (`main.jsx:6880-6881`) with a CTA that does the same (`main.jsx:8903`) but
  renders **only while the graph is empty**. So the gap is not "unreachable": it is that the only
  route to a member's connect arms is the onboarding wizard they were told to skip — there is no
  member-owned first-run surface, and the dashboard itself renders no chooser or key affordance.
- The **dashboard's own** member guidance is copy only: the re-entry card's member note
  (`onboardingEmptyStateKeyNote.js:96`, #3729) and role-aware empty-state copy. No first-run
  surface confirms which organization the member just joined.
- Members **cannot mint keys** (POLICY A — `create_api_key` gate `hosted_api.py:8417` via
  `_require_owner_admin_if_session` `:14676`), so several dashboard key CTAs are dead ends for them.
  Filed already: **#4628** (empty-state primary action → owner-only API Keys page) and **#4637**
  (owner arms fork/leaf-blind). The keyless-member dead-end is the **#2366**/#2307 line, and the
  sanctioned fix path is **#2427 → #2446**.

> **Confirmed problem definition:** *An invited member is correctly sent straight to the dashboard,
> but nothing on that dashboard is the member's own first-run surface. The member's connect arms
> live only inside the wizard they were told to skip (re-entered via the unconditional Setup
> button, or via the re-entry card while the graph is empty), and several key CTAs are owner-shaped
> dead ends. So "what does the member see when they join" is still undefined.*

---

## 2. The membership model as it is today (all claims `file:line`)

| Fact | Evidence |
|---|---|
| Roles are `owner \| admin \| member`; the column default is `owner` **for the creator's placeholder row** | `supabase/migrations/0003_team_memberships.sql:20` |
| An **invitation** carries role `admin \| member` with default `member`; `CHECK (role IN ('admin','member'))` | `supabase/migrations/0008_invitations.sql:22,36-37` |
| The server rejects any other invite role | `tortoise/hosted_api.py:14827-14828` |
| The dashboard's invite form defaults to `member` | `website/apps/dashboard/src/main.jsx:2080` |
| Accepting an invite creates the membership **under the invited role** and returns it | `tortoise/hosted_api.py:15117` (accept endpoint); membership write `:15287`; response `:15304` |
| Accept arms a **per-member** onboarding slot `member_progress[user_id] = []` — it never advances an org-level step and never fakes org completion | `tortoise/hosted_api.py:15588-15610` |
| Session-lane key **mint** is owner/admin-only (POLICY A) | `tortoise/hosted_api.py:8360`, `:8397`, `:8417`; gate `:14676` |
| A member's sanctioned connect routes are (i) a **key-less OAuth connector** or (ii) a key **an owner/admin shares** | `website/apps/dashboard/src/onboardingEmptyStateKeyNote.js:96` (#3729); wizard arms `main.jsx:7319-7330` |
| The **fork** is per-organization and **set-once** (`SET_ONCE`), not per-member | `tortoise/onboarding/state.py:74,92`; card semantics `website/apps/dashboard/src/wizardFlow.js:233` |
| The wizard has exactly four human steps: org-create → fork → connect → done | `website/apps/dashboard/src/wizardFlow.js:29-58` |
| Onboarding state (fork, steps, `member_progress`) is **org-scoped**, read per selected `?org_id=` | `tortoise/onboarding/state.py:86-98` |
| Join-side free-cap: a user with one active free membership is refused a second free org (402) — membership-scoped **by decision** (#2789) | `tortoise/hosted_api.py:15233`; pin `:12894-12900` |
| The org switcher is **role-independent** and shows for any user with >1 membership | `website/apps/dashboard/src/main.jsx:8589` (account menu); `:9875` (billing) |
| Every org-scoped dashboard read is pinned to the selected org with `?org_id=` (the user-scoped org **list** `/v1/organizations` at `main.jsx:4014` is the deliberate exception) | `website/apps/dashboard/src/main.jsx:5278` (`loadGraphs`), `:5092` (`switchTeam`) |

---

## 3. Q1 — What does "joining an org" mean in the Tortoise model?

**Answer, from the code:** joining is *a membership row and a role*, not an ownership transfer and
not an onboarding run.

1. **Not an admin by default.** The inviter picks `admin` or `member` (dashboard default `member`,
   `main.jsx:2080`); `owner` is never granted by an invite. The member gets the org's graph and the
   org's *existing* keys (listing keys is member-open) but **cannot create, rotate, revoke, or
   toggle keys** (POLICY A, `hosted_api.py:8417`).
2. **Does the member need their own API key?** No — not to *be* a member, and not for the key-less
   connectors. A key is needed **only** if the member connects a *keyed* harness themselves, and
   today the sanctioned answers are "use a key-less connector" or "ask an owner/admin for a key"
   (`onboardingEmptyStateKeyNote.js:96`; `main.jsx:7319-7330`). Whether the member should be able to
   obtain/hold a *personal* durable key is **not decided** — it is the subject of **#2427**
   (permissions) and its phase 2 (**#2446**, manage-own only). **Not decidable here** (§0).
3. **Do they go through the fork card?** No, and they should not: the fork is an **organization-level,
   set-once** fact (`state.py:74,92`) set by whoever first answered it — normally the creator. An
   invitee does not re-answer it, and must not be asked to.
4. **They share the organization's memory graph** — that is the point of the invite; #2789's product
   position ("the person who created the org decided how it's used") is consistent with this.

---

## 4. Q2 — Which onboarding steps are relevant for an invitee?

| Step | Relevant to an invitee? | Why |
|---|---|---|
| **Orientation** | No | Deleted for everyone in #2536. |
| **Org-create** | No | The org exists; the member did not create it. |
| **Fork card** | No | Org-level set-once; the creator's decision (Q1.3). |
| **Connect** | **Yes in substance, no in form** | The member may connect their *own* agent. But the wizard's connect step also owns key **minting**, which POLICY A forbids for a member — so the *step* cannot be handed to a member as-is. Its **role-aware arms already handle this** (`main.jsx:7198-7200`, `:7319-7330`); reaching them means entering the creator's wizard (the unconditional Setup button `main.jsx:8407`, or the re-entry card `main.jsx:6880-6881` while the graph is empty). |
| **Done** | No | Org-level completion. `_arm_invitee_member_progress` deliberately writes only the member's empty slot (`hosted_api.py:15588`), never an org step. |

**So the design space is narrow:** the invitee needs *no* wizard step, but the member connect
guidance the wizard already contains needs a **first-run home the member owns**, rather than a
Setup-button route into the creator's wizard.

---

## 5. Q3 — Multi-org membership and switching

**The switching machinery already exists and is role-independent.**

- `switchTeam(orgId)` (`main.jsx:5092`) re-pins `orgIdRef`/`currentOrgId` and reloads keys, graphs,
  members, backups, and the org-scoped onboarding projection.
- The account menu renders **Switch organization** whenever `teams.length > 1` (`main.jsx:8589`);
  the billing tab mirrors it (`:9875`). Each row is the invited/member org too.
- Fork and onboarding state are **per-org** (`state.py:86-98`), so three orgs with three different
  forks cannot alias each other on a switch.
- The one genuine multi-org constraint is the **join-side free-cap** (`hosted_api.py:15233`): a user
  already holding one free membership is refused a second free org. That is **a recorded decision**
  (#2789), not a defect (§0).

**A real multi-org defect was found and is filed separately (see §8):** accepting an invite *link*
does **not** select the invited org, while accepting the same invite from the account menu **does**.
For a new user (one membership) this is invisible; for an existing multi-org user it lands them on
the wrong organization.

---

## 6. Candidate invitee experiences — alternatives evaluated

| # | Candidate | Verdict | Why |
|---|---|---|---|
| **A** | Invitee runs the wizard, with the org-create/fork steps rendered read-only (what the 2026-09-07 epic *plan* Journey B sketched) | **Rejected** | Contradicts the recorded #2534 decision ("skip entirely") and leaves a member standing on a connect step whose mint arm POLICY A forbids. |
| **B** | Skip to the dashboard, nothing else (the shipped behaviour) | **Kept as the base, insufficient alone** | Correct entry, but the member's connect guidance lives only in the skipped wizard; several dashboard CTAs are owner-shaped (#4628, #4637). |
| **C** | Skip to the dashboard **plus a one-time, member-scoped "You've joined ⟨org⟩" card** that confirms the org and offers the connect routes that work for a member (key-less connector chooser; paste a shared key; "ask an owner/admin" with the #3729 conditional wording) | **RECOMMENDED** | Uses only sanctioned routes — no new permissions, no member mint. Reuses copy/arms that already exist in the wizard's member arms (`main.jsx:7198-7200`, `:7319-7330`) and the note in `onboardingEmptyStateKeyNote.js`. Honours every recorded decision. |
| **D** | Invitee mints their own key as part of onboarding | **REJECTED — HARD STOP** | Directly contradicts POLICY A (#2297, `hosted_api.py:8397`). If it should win, the route is to **reopen #2427** with evidence — never to adopt it here. |
| **E** | Block invites until #2427 lands | **Rejected** | Invites ship now; member *read* access, the key-less connectors, and the org switcher all work today. Blocking trades a live capability for a design gap. |

---

## 7. What is **not** decision-gated (already shipped / already filed)

- **The skip itself** is implemented (#2538, `main.jsx:3839`, `:4079-4093`) — no work.
- **The member CTA dead end** is already filed: **#4628** (micro) and **#4637**. Not re-filed.
- **The member key path** is already designed-for in **#2427** (research) and **#2446** (phase 2).
  Not re-derived here.
- **The join-side free-cap asymmetry** is a **decision** (#2789) — explicitly out of scope.
- **The invite-link org-selection defect** is a bug, not a design question → filed separately as **#5254** (§8).

---

## 8. Defect found in the invite path (filed, not fixed here)

**#5254 — accepting an invite link does not select the invited organization.**

- Mount path: `acceptStashedInvite()` calls `loadTeams()` (`main.jsx:3839`) but **never**
  `switchTeam(...)`. The subsequent pins choose the **first healthy / first selectable** membership
  (`main.jsx:4115-4121`, `:4607-4616`), i.e. not necessarily the invited org.
- Account-menu path: after the same accept it **does** switch — `await loadTeams();
  switchTeam(res.org_id)` (`main.jsx:4646`).
- Effect: a user who is already in Org A and accepts a link to Org B lands on **Org A**. A brand-new
  invitee (single membership) is unaffected.

Filed as **#5254** rather than fixed here, because the mount effect is a concurrency-sensitive
region (Round-8/Round-12/#1912 pin guards) and **no test could exercise it in this environment**
(the local FalkorDB is wedged — see §9). The fix is small (read `org_id` from the accept response
and prefer it when pinning) but must ship with a test.

---

## 9. Open questions requiring the owner (the decision request)

Posted on **#2535** in the protocol shape (context · options · analysis · recommendation).

1. **Is the invitee's post-accept surface "dashboard only", or do we want a one-time member join
   card?** (Candidate B vs C.)
2. **Does an invited member ever need a key of their own?** If yes, the route is #2427 → #2446, and
   #2535 should hand the question to #2427 rather than answer it. If no, the key-less-connector +
   share-a-key story is the final member model and #2427 can record it.
3. **Confirm the join-side free-cap is intended to stay membership-scoped** (#2789's pin). Its
   user-visible effect on an invitee is a 402; if the owner wants collaborator-friendly copy at the
   moment of the 402, that is a copy decision, not a change to the gate.

---

## 10. Evidence index and verification limits

**Primary evidence:** `supabase/migrations/0003_team_memberships.sql`, `:0008_invitations.sql`;
`tortoise/hosted_api.py` (`:8360`, `:8397`, `:8417`, `:12851-12900`, `:14676`, `:14819-15309`,
`:15588`); `tortoise/onboarding/state.py:74-98`;
`website/apps/dashboard/src/main.jsx` (`:1193`, `:2080`, `:3822-3842`, `:3951`, `:4014`,
`:4079-4093`, `:4115-4121`, `:4607-4616`, `:4646`, `:5092`, `:5271`, `:6880`, `:6918`,
`:7198-7200`, `:7319-7330`, `:8407`, `:8589`);
`website/apps/dashboard/src/wizardFlow.js`; `website/apps/dashboard/src/onboardingEmptyStateKeyNote.js`;
`website/apps/dashboard/public/invite-accept.html:155-199`; `tortoise/email_notify.py:252`.

**Decisions:** #2534 (skip invitation), #2297/#2366 POLICY A, #2789 (one free org; membership-scoped
join gates), #3913 (build-fork completion — not used here).

**Related already-open issues:** #2427, #2446, #4628, #4637, #3729, #2366/#2307, #5254.

**What could not be verified:**
- **No test could be run: the local FalkorDB (OrbStack) is wedged** — DB-backed `pytest` fails with
  `redis.exceptions.TimeoutError` and `docker ps` hangs. All claims here are **static** (source
  read); the invite-link org-selection defect is reasoned from code, not reproduced in a browser.
- **S9 epistemic memory unavailable** (`not_configured`) — prior Tortoise claims were not read.
- **The runtime org order** returned by `GET /v1/organizations` was not observed (no live DB), so
  the defect's exact blast radius per membership ordering is inferred, not measured.
