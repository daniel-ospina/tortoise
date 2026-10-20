---
title: "#8693 — end-customer (tenant-of-tenant) credential self-service: the design"
type: design
domain: platform
doc_status: live
created: 2026-10-19
subjects.team: epistemic-team
aboutSubjects: Tortoise hosted credential model
aboutObjects: "#8693, #557, #524, #2427, #8692, #8694, #9177, #2446, #318, docs/authorization-architecture.md §4 option D, docs/research/2026-10-15-2427-team-permission-model.md §4.3/§7, docs/scoping/2026-08-15-557-sub-tenancy-scoping.md"
---

# #8693 — end-customer (tenant-of-tenant) credential self-service: the design

**Issue:** `#8693` (phase 5 of the `#2427` permission model, `Level: epic`, team:epistemic-team)
**Deliverable:** design only. **No implementation, no surface cut** (`AGENTS.md` → MCP/SDK Surface
Approval; §9 below is a surface *request*, not a cut).
**Parent model:** `docs/research/2026-10-15-2427-team-permission-model.md` (§4.3 actor map, §7
over-scope call). **Governing tier design:** `docs/scoping/2026-08-15-557-sub-tenancy-scoping.md`
(owner decisions Q1–Q7, 2026-08-15) · `#557`.

---

## §0 Status — and what re-measurement changed about the blocker

The issue body says the tier is blocked because "there is no end-user auth layer — `#557` is open
and unbuilt; `#524` is open". Measured 2026-10-19, that framing is **stale on both counts and on
one code fact that changes the design's shape**:

**How this document cites code.** Every reference to `tortoise/hosted_api.py` is a **symbol
address** — `hosted_api.py::<symbol>` for a definition or handler, `@app.<verb>("<route>")` for a
registered route — never an absolute line number. The repo's own rule governs
(`tests/test_extractor_v4_citation_rot.py`, the `#8506` guard): *the symbol name is authoritative,
the line number is a convenience*. In a 4,700-line module that a merge moves by hundreds of lines, a
number rots on work that has nothing to do with this design — it did, twice, before this rule was
applied here — while a renamed or removed symbol still reds. The pin suite that enforces this is
`tests/test_8693_end_customer_credential_design.py`, which resolves every address below against the
module at HEAD.

| Issue-body claim | Measured 2026-10-19 | Effect |
|---|---|---|
| "`#524` (OAuth 2.1 / token→team mapping) is **open**" | **CLOSED / COMPLETED** 2026-08-14; `tortoise/oauth.py` ships auth-code + PKCE, DCR, RFC 8707 token→team mapping, `/oauth/token`, `/oauth/revoke` (`@app.get("/.well-known/oauth-protected-resource")`, `@app.get("/oauth/authorize")`, `@app.post("/oauth/consent")`, `@app.post("/oauth/token")`, `@app.post("/oauth/revoke")`, `@app.post("/register")` — discovery, authorize, consent, token, revoke, DCR) | The v2 upgrade path (§4) is buildable on shipped machinery |
| "blocked on #557 … do not start before #557's **provisioning model** is settled" | **Settled.** Owner decisions Q1–Q7 recorded 2026-08-15 on `#557`; scoping doc `docs/scoping/2026-08-15-557-sub-tenancy-scoping.md` is `doc_status: live`; `#318` (the prerequisite pack-isolation epic) is **CLOSED** with `pack_registry.py`/`pack_state.py` merged | **The blocker named in the issue is met → DESIGN is unblocked** (implementation stays gated on `#557` S1) |
| "no end-user auth layer" | True, and **still true** — but the tier does not need one: `#557` Q2 chose *key-only for v1* (no end-user account, no consent screen) | §2/§3 design a credential, not an auth layer |
| (body proposes RFC 8693 as "the external shape") | `#557` Q2 (**owner**, 2026-08-15) chose **key-only for v1**, with RFC 8693-style token exchange as **the designed post-launch upgrade** | §1 D1. RFC 8693 is **not adopted for v1** — it is the recorded v2 step (§4) |
| (body: delegated credential "mints" a scoped token) | C5 (`#2114`) **landed**: per-graph key binding, `_require_scope`, `_reject_graph_bound_org_surface` are live (`hosted_api.py::_assert_graph_owned`, `::_require_scope`, `::_reject_graph_bound_org_surface`) | The delegated credential has a *shipped* enforcement spine; the design composes it rather than inventing one |

**Verdict:** the design work this issue asks for is **unblocked and done here**. What remains
blocked is *building* it: the end-user team provisioning + the cross-team provisioning/lifecycle
capability (§9.1) are `#557` S1 (`2026-08-15-557-sub-tenancy-scoping.md` §Approach), which has zero
sub-issues today.

---

## §1 The recorded decisions this design must obey (contradiction test FIRST)

Per `AGENTS.md` → USER QUESTIONS PROTOCOL, the contradiction test runs **before** cost or fit. Every
convergent external practice below was tested against a recorded decision, and a contradiction is
**not a candidate for adoption** — the route is a reopen of that decision, argued separately.

| # | Recorded decision | Source | Status in this design |
|---|---|---|---|
| **D1** | End-user auth is **key-only for v1**: the embedder's backend mints a *short-lived, per-end-user-team-scoped key at login*; no end-user Tortoise account, no consent screen. **Token exchange (RFC 8693-style) is the designed post-launch upgrade**; full federation is the north star for large embedders. `#524`'s OAuth stays for the **embedder's own team**, never the end-user path | `#557` owner comment 2026-08-15 (Q2), scoping doc §Open questions Q2 | **Obeyed.** v1 = a scoped `tk_` credential in the end-user's team (§3). RFC 8693 = the recorded v2 step (§4). Federation = out of scope |
| **D2** | **Graph-per-end-user**, provisioning under the embedder's account: each end user = **one team + one graph**; per-end-user metering line items rolled into the embedder's bill; `pro`+ tier; provisioning rate limit in S1 | `#557` owner comment 2026-08-15 (Q1, Q3, Q4, Q6), scoping doc §Context "Model" | **Obeyed.** The credential lives in the end-user's *own team*, so the isolation boundary is the shipped namespace boundary (§5), and per-end-user meter lines fall out of the per-team ledger |
| **D3** | An end user is **never** a row in the builder's `org_memberships`; the mechanism is a credential the builder's app vends under its own authority. Not a fifth role | `#2427` brief §4.3 | **Obeyed.** No membership, no user row, no identity stored (§2) |
| **D4** | Enforcement stays in the app-layer resolver behind `authorize(principal, action, resource)`; **no external ReBAC/ABAC engine now** — carries an `OVERRIDES:` ruling | `docs/authorization-architecture.md` §4 (`OVERRIDES:`, `#2083` scope line) | **Obeyed.** The delegated credential is enforced by the existing `_require_scope` / `_reject_minted_delegated_key` / `_assert_graph_owned` spine. No engine, no tuple store |
| **D5** | `#2297` POLICY A: the **session lane** of key mint is owner/admin-gated; members do not mint durable org keys | `#2297` owner decision 2026-09-05, `hosted_api.py::create_api_key` docstring | **Not engaged.** End users have no session and no membership; the builder-side mint runs on the **key lane** (D13), whose gates are unchanged. No reopen needed |

### The rejected alternative, recorded so it is not re-invented

**Design B — one embedder org, one graph per end user, credential bound to that graph.** This is the
cheapest shape on paper: every surface it needs is shipped (`POST /v1/team/keys` with `{graph_id,
scopes}`, `rotate`, `DELETE`, `api_key_occupy` slot credit, per-graph `_assert_graph_owned`), and
nothing cross-team is required.

- **It contradicts D2** (one *team* + one graph per end user) → per the contradiction test it is
  **not a candidate for adoption**; the route would be to reopen `#557`'s Q1 with the evidence.
- **It also fails arithmetically, independent of the decision:** credentials minted in the embedder's
  org consume the embedder's `max_api_keys`, measured from `product/pricing.json` as **2 (free) / 5
  (solo) / 10 (pro) / 20 (team)**. An embedder with 11 end users could not mint an 11th credential on
  the `pro` tier the same decision gates sub-tenancy to (D2 Q4). The cap is per org; sub-tenancy
  therefore *requires* the credential to live in the end user's own team — which is what D2 already
  says. Design B is recorded here as the evidence a reopen would use, **not** as a plan.
- **Second reason, corrected after review — key-auth graph creation DOES exist.** Earlier drafts of
  this section claimed `POST /v1/graphs` was the only route and reachable with a session token
  alone; that was wrong, and the corrected version is stronger, not weaker. Programmatic creation
  ships under key
  auth at `POST /v1/organizations/{org_id}/graphs` (`create_org_graph`, registered by
  `@app.post("/v1/organizations/{org_id}/graphs")`). What Design B would need is an **owner-class key
  carrying `graphs:create`** (the scope check inside `hosted_api.py::create_org_graph`; the scope is
  in `hosted_api.py::_OWNER_SCOPE_ALLOWLIST`) that
  is **not** a minted child — `deleg=0` keys are rejected outright (the `delegation_depth == 0`
  guard in `create_org_graph`, "Minted keys
  cannot provision graphs", and the child policy cannot mint `graphs:create` at all). Handing the
  end-user lane that class of key is exactly the owner-class authority §3.3 property 1 refuses to
  give it. (`POST /v1/graphs`, `@app.post("/v1/graphs")`, is the session
  alias for the same provisioning service — not the only programmatic path.)

---

## §2 Identity model

**A Tortoise end-user subject does not exist, deliberately.** The design gives the tier the smallest
identity surface that satisfies `#557`'s indicators:

| Question | Design answer |
|---|---|
| How is an end user represented? | **As a builder-held opaque id.** The builder's own database maps `(app_user_id) → (end_user_team_id, credential_id)`. Tortoise stores the team, the graph, and the credential row — never the person |
| Is there a Tortoise account or membership? | **No.** No `org_memberships` row, no `users` row, no email, no consent (D3, D1) |
| What is the trust anchor? | **The builder's server**, holding the provisioning credential. Tortoise trusts it exactly as it trusts any org owner's key — no third authority is introduced |
| What must Tortoise *not* learn? | Any PII. The credential's `name` label carries the builder's opaque id (≤ the existing label bounds, `_clean_key_label`); builders must not put emails or names there (**stated as a builder obligation**, §7) |
| Can Tortoise correlate an end user across builders? | No. Separate embedder org namespaces, separate teams, opaque ids |
| What does "the same end user" mean across a builder's two apps? | **Two credentials, two teams.** Tortoise does not model identity continuity; if the builder wants one memory per person across its apps, it reuses one end-user team — its choice, not a Tortoise concept |

**Why this is the right size.** The alternative — a real subject record — would create a second,
half-built auth system the tier does not need (D1 says no end-user login), and would put personal
data into a component whose access story is not designed for it. The credential is the identity.

---

## §3 The delegated credential — v1 contract (key-only, per D1)

### 3.1 Shape

| Property | Value | Why |
|---|---|---|
| Class | `tk_` API key, **`delegation_depth = 0`** (a minted child) | C2/C3 child policy: a minted key can never mint, rotate itself, or reach account management (`hosted_api.py::_reject_minted_delegated_key`) |
| Lives in | the **end-user's team** (its own org, D2) | keeps the credential off the embedder's `max_api_keys` budget (§1) and makes the team/graph namespace the isolation boundary (§5) |
| Scopes | `["graphs:read","graphs:write"]` — the full `_MINTABLE_SCOPES` set, nothing else | `graphs:create`/`graphs:delete`/`keys:manage`/`team:manage` are escalation scopes that a key-minted child can never hold (`hosted_api.py::_MINTABLE_SCOPES`, DB CHECK `chk_minted_key_no_escalation`) |
| Graph binding | **recommended: `graph_id` = the end-user's graph** — but see 3.2 | a graph-bound key is rejected from org-level surfaces outright (`_reject_graph_bound_org_surface`) and pinned by `_assert_graph_owned` — defence in depth over the team boundary |
| Expiry | **set** (days, `#2426` `expires_in`/`expires_at`), minted by the builder at login | D1 says "short-lived at login"; expiry is the only time bound a child credential has (`Never` must not be the default for this tier — a leaked end-user credential with no expiry is a permanent tenant foothold) |
| Lineage | `created_by_key_id` = the builder's provisioning key; `created_by` = the acting org actor | already recorded at mint (`supabase_control.py:1056`, `:1161`); it is what makes the compromise trail answerable (§6) |

### 3.2 The one measured constraint that shapes provisioning

`_ensure_graph_exists` (`hosted_api.py::_ensure_graph_exists`) refuses to bind a key to a team's **default**
graph: a graph-bound mint 404s unless the target is a **custom** graph row (`kind != 'default'`),
because the default graph is addressed by an org-wide key (`teams.graph_name`) and is not
key-bindable.

So the choice is structural, not stylistic:

- **If `#557` provisions each end-user team with its graph as the team's default graph** (the
  `provision_team` shape: `graph_name = team_{name}`), then the end-user credential **cannot** be
  graph-bound, and isolation rests on the **team boundary alone** — which is still structural (a
  separate org is a separate graph namespace, `#318`, `docs/authorization-architecture.md` §3.2), but
  it forfeits the second lock.
- **If `#557` provisions a *custom* graph per end-user team** (creation is already one call inside
  that team's provisioning, `create_org_graph`, `hosted_api.py::create_org_graph`), the credential is
  graph-bound, fail-closed on every org-level surface, and survives the team later gaining a second
  graph.

**Recommendation: the second.** It is one provisioning detail (a custom graph instead of the default
one) and it buys a second, independently-enforced lock for the entire tier. This is a *requirement
handed to `#557` S1*, not a change to it.

### 3.3 The minting authority (the crown-jewel credential)

D2/Q1: the embedder mints **per-end-user child keys** and there is **never** a master-tenant
impersonation surface. Today the mint endpoint acts only on the **caller's own org**
(`create_api_key` → `_mint_key(org["org_id"], …)`, `hosted_api.py::create_api_key`) — there is no
cross-team mint. That capability is `#557` S1 and it is a **surface request**, not something this
design cuts (§9).

Required properties of that authority, stated here so S1 is judged against them:

1. **Scoped, not impersonating**: it can mint inside teams it provisioned, and nothing else. Never a
   session-for-another-user, never a master token.
2. **Short-lived**: the minting capability itself expires (D2/Q1 "short-lived minting capability").
3. **Per-mint audit**: every end-user credential mint writes an `api_key_create` row naming the
   acting credential (`created_by_key_id`) — the row already carries the lineage; the *audit* side is
   §6.
4. **Revocable as a unit, and its revocation must reach its children** — measured gap, §5.3
   (`#8694`).
5. **Lifecycle reach over what it minted** — the same authority must be able to **rotate** and
   **revoke** the credentials it minted, or §5's Replace button cannot work. Measured at this head:
   it cannot today (both verbs are pinned to the caller's own org, §5.2). Same boundary as property
   1 — teams it provisioned, nothing else; the required verb set is in §9.1.

---

## §4 The v2 upgrade path (recorded, not built)

D1 already chose the upgrade; this design only has to keep the door open and state the delta.

| | v1 (key-only, D1) | v2 (RFC 8693-style token exchange, D1's recorded upgrade) |
|---|---|---|
| What the end user holds | nothing — the builder's app holds a `tk_` credential server-side | a short-lived token issued by the **builder's** authority for **Tortoise** as audience |
| What Tortoise consumes | a `tk_` Bearer key (shipped path, C5 spine) | `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` on `/oauth/token`, subject token from the builder, `act` claim naming the builder |
| Machinery | shipped | `tortoise/oauth.py` (mint/introspect/revoke, RFC 8707 resource mapping) — **needs a token-exchange grant**, i.e. a surface change with owner approval (§9) |
| Why not now | — | D1: post-launch, "when revocation/audit demand it". Adopting it for v1 would reverse an owner decision |

**Design constraint carried forward:** the v1 credential row must be expressible as the *exchange
output* later — same team, same scopes, same lineage, same revoke verb. That is why §3 keeps the
credential coarse (`graphs:read`/`graphs:write`, no per-resource grants): a token exchange minting
anything finer would be a different design and would need D4 (§1) reopened.

---

## §5 The "my credential leaked" UX — one Replace, and who does what

### 5.1 What the end user sees

One action, in **the builder's app**, in plain language — no keys, no tokens, no Tortoise:

> **Access problem?** · **Replace my access** — your old access stops working immediately and this
> device gets new access. Nothing else changes.

- **One action.** No confirm-email, no re-invite, no support ticket, no Tortoise login (there is no
  Tortoise account — D1/§2).
- **Same powers after Replace.** The replacement inherits the row's class (same team, same graph, same
  scopes) — the primitive cannot widen or demote (`rotate`, `hosted_api.py::rotate_api_key`, inherits
  `graph_id`/`scopes`/`delegation_depth`/lineage; body `scopes`/`graph_id` are 422).
- **Old credential dead, atomically.** `rotate` is create-then-claim-revoke: the displaced row is
  revoke-claimed in the same operation, so there is **no window with both live** — the compromise
  pattern the comparables prescribe (`#2427` §4.4).
- **Word to drop:** "Refresh". `#2427` §7 ruled it out of the vocabulary (expiry is immutable in
  place); the tier inherits that ruling.

### 5.2 What actually happens (the builder's three obligations)

1. **Replace endpoint**: on the button, the builder's backend calls Tortoise `rotate` against the
   end-user's credential id, inside that end-user's team, using the provisioning credential. One
   round trip **from the builder's app** — but **not one shipped call.** Measured at this head, the
   cross-team lifecycle reach does **not** exist: both lifecycle verbs are pinned to the caller's
   own org — `rotate` in `hosted_api.py::rotate_api_key`, `DELETE /v1/team/keys/{key_id}` in
   `hosted_api.py::revoke_api_key` (both lanes: the supabase lane and the registry lane) — each
   calling
   `_ensure_key_in_pinned_org(org["org_id"], row.get("org_id"), required=True)`, whose contract is
   that the resolved org "must own the key UNCONDITIONALLY"
   (`hosted_api.py::_ensure_key_in_pinned_org`). A target key in the
   end-user's team therefore raises `403 "Not your API key"` for a builder-org credential. Key auth
   cannot escape the pin: on the key lane the org is resolved *from the key itself* and `?org_id=`
   is ignored (the `revoke_api_key` docstring states it). The end-user credential cannot
   self-rotate either — every `deleg=0` caller is rejected by
   `_reject_minted_delegated_key(org, "rotate API keys")`
   (`hosted_api.py::_reject_minted_delegated_key`). So this step is **one call the builder
   makes plus one capability Tortoise must grant**: the cross-team provisioning authority of §9.1
   extended to the lifecycle verbs. Until that lands, the button 403s for every end user.
2. **Re-propagate**: the response is the **only** time the new plaintext exists (reveal-once). The
   builder stores it and re-issues its own session/app token; Tortoise keeps only the hash.
3. **Say nothing about Tortoise**: the end user must never learn the platform credential exists —
   that is the whole point of D3.

**What the builder must NOT have to do:** revoke-then-mint as two calls (a window with the old key
live and a cap-loss risk), edit scopes, or touch the graph.

### 5.3 The containment case — when the *builder's* credential leaks

The end-user tier's blast radius is the builder's **provisioning** credential (D2/Q1: "a stolen child
key = one end-user space, not everything"). Containing it means: revoke the provisioning credential
**and** every end-user credential it minted. Measured today, two gaps block that:

1. **Revocation does not cascade** — `#8694` (OPEN): `created_by_key_id` is recorded but no code
   consults the parent's `revoked_at`; a live child survives its parent's revocation indefinitely
   (`#2427` §2.4).
2. **No cross-team enumeration** — the provisioning credential's children live in *other* teams, and
   every key-listing surface is org-scoped (`GET /v1/team/keys` — `@app.get("/v1/team/keys")`, handler
`hosted_api.py::list_api_keys`).

**Design position:** per-end-user Replace/Revoke (§5.1, §5.2) does not depend on **either of these
two containment gaps** — replacing one credential the builder already has on record needs no child
enumeration and no cascade. **Containment does.** But Replace/Revoke does depend on a capability
that is equally missing, and §5.2 measures it: the **cross-team lifecycle reach**. "The end-user
credential is reachable through its own team" is true only for a caller *inside* that team, which
the builder's credential is not; and the child cannot rotate itself
(`hosted_api.py::_reject_minted_delegated_key`). The tier's stated
isolation claim ("a stolen child key = one end-user space") is only true for the *child*; the
*parent* leak is currently unbounded in N **and** the parent cannot revoke what it minted. So
**three** things must land before this tier is exposed to real end users: `#8694` (cascade) or a
provisioning-side child enumeration; a builder-side child enumeration; and §9.1's lifecycle
authority. The fallback runbook until then is **not** "N cross-team revoke calls issued from the
builder's own record" — that runbook carries the same 403 as the Replace button; it is "the owner
revokes the parent, and the children die with it" once `#8694` lands. Recorded as preconditions
(§10), not silently assumed. The one shape that needs no cross-team reach is named in §9.1 (an
in-team manager credential), and it is a fallback, not the recommendation.

### 5.4 What the end user cannot do (and should not)

- No Tortoise UI, no self-service key list, no scope editing, no graph choice — the builder's app is
  the only surface. **Tortoise provides the primitive; the builder provides the affordance.**
- No access to *another* end user's data even with a live credential: separate team, separate graph
  namespace, and (if §3.2's recommendation is taken) a graph-bound key (§5 of `#557`'s scoping doc,
  §5 here).

---

## §6 Isolation and metering

### 6.1 Isolation — three independent locks, all shipped or specified

| Lock | Mechanism | State |
|---|---|---|
| Namespace | the end-user **team** = its own graph namespace (`org_{org_id}`); no tenant selector exists on any surface | **shipped** (`#318`, `docs/authorization-architecture.md` §3.2) |
| Scope | `tk_` child carries only `graphs:read`/`graphs:write`; `_require_scope` is the FIRST statement of every data handler (write implies read; read never satisfies a write) | **shipped** C5 (`hosted_api.py::_require_scope`) |
| Graph binding | `_assert_graph_owned` pins a graph-bound key to its own graph and fails closed on a vanished graph; graph-bound keys are 403 on org-level surfaces (`_reject_graph_bound_org_surface`) | **shipped** C5 (`hosted_api.py::_assert_graph_owned`, `::_reject_graph_bound_org_surface`) — *available only if §3.2's custom-graph recommendation is taken* |

Plus the structural dormancy rule: a minted (`deleg=0`) key is rejected from every management surface
by the DI gate (`_reject_minted_delegated_key`), so an end-user credential can never mint, rotate,
revoke, list keys, touch billing, or reach dashboard login.

### 6.2 Metering — free per end user, roll-up is `#557`'s

`MeteringRecord`'s identity **is** `(org_id, period_start)` (`tortoise/metering.py:6`, `:108`). With
D2 (one team per end user) the **per-end-user line item is already the ledger's own granularity** —
nothing new is needed to meter per end user; `get_current_usage(org_id)` answers it per team.

What does **not** exist, measured: any embedder↔end-user-team linkage. There is no parent/embedder
column in the control plane (`supabase/migrations/0006_teams.sql` — `teams` has no parent field) and
no embedder concept anywhere in `tortoise/`. So "rolled into the embedder's bill" is an
**aggregation over a parent link that `#557` S3 must add** (one column, or a `sub_tenants` table).

**Contract this design fixes for S3** (so the tier and any future per-end-user cap agree):

- the parent link is the **embedder's team id**, one direction, set at provisioning;
- billing reads `sum(child.teams)` per period — never a re-derivation from keys;
- the end user sees **nothing** about usage (D2/Q3); the builder sees the aggregate **and** the
  per-end-user lines;
- per-end-user *caps* are explicitly a follow-up (D2/Q6), not v1.

### 6.3 Cross-embedder isolation

Two embedders' end users are in different org namespaces under different embedder accounts — the same
structural guarantee `#318` verified in staging, and `#524`'s RFC 8707 resource-tree rejection
(`invalid_resource`) is the exfiltration guard precedent for the v2 token path.

---

## §7 What the builder implements vs what Tortoise provides

The issue's indicator, answered without hedging:

| # | Capability | **Builder implements** | **Tortoise provides** (shipped / specified) |
|---|---|---|---|
| 1 | Know which end user is which | the entire identity map (`app_user_id → team_id, credential_id`), and the obligation to keep PII out of labels | an opaque team + graph + credential; **no identity store** (§2) |
| 2 | Create an end-user tenant | the call at signup/login, keyed to its own user id | sub-tenant provisioning `#557` S1 (**specified, unbuilt**) + `#318` default-pack activation |
| 3 | Mint the end-user credential | one server-side call at login, with the expiry it wants | the mint, the child-policy clamping (scopes ∩ mintable, depth 0, no escalation), lineage — **shipped** primitives; the **cross-team** minting authority is the surface request (§9) |
| 4 | The "leaked" affordance (button, copy, flow) | **all of it** — the UI, the wording, the re-issue of its own session | `rotate`: atomic, cap-neutral, class-inheriting, reveal-once (**shipped**, `#4355`); `DELETE` for terminal revoke (**shipped**) — **but both only within the caller's own team** (`_ensure_key_in_pinned_org`, §5.2). Reaching an end-user credential from the builder's org is a surface request (§9.1), **not shipped** |
| 5 | Isolation guarantee | nothing | the namespace boundary + scope resolution + (recommended) graph binding — **shipped** (§6.1) |
| 6 | Rate limiting / abuse | the login-time mint cadence it drives | `abuse.py` key-create velocity evaluation on mint **and** rotate (`#308` R2, `_abuse_evaluate_keys`); per-embedder provisioning rate limit is `#557` S1 (D2/Q6) |
| 7 | Audit | reads it, keeps its own copy of the end-user map | append-only, RLS-tenant-scoped `audit_events`; `api_key_create` / `api_key_rotate` rows (**shipped**); **revoke is NOT audited — measured gap, §8, filed as #9177**; `keys.audit.read` has no read surface (`#2427` §4.1) |
| 8 | Containment of a leaked *provisioning* credential | issues N per-end-user revokes from its own map — which itself needs §9.1's lifecycle reach | `#8694` (cascade) + child enumeration + cross-team revoke — **all three missing** (§5.3) |
| 9 | Metering into its bill | sees the aggregate | per-team ledger **shipped**; parent-link roll-up specified for `#557` S3 (§6.2) |

**One-line summary:** *Tortoise provides the credential lifecycle for a tenant it already isolates;
the builder provides the identity, the affordance, and the containment runbook.*

---

## §8 Measured state (evidence table)

| Fact | Evidence |
|---|---|
| Mint endpoint acts on the caller's own org only | `hosted_api.py::create_api_key` → `_mint_key(org["org_id"], …)` |
| Child mint: `deleg=0`, scopes ∩ `_MINTABLE_SCOPES`, an escalation-scope request is a 403 (never silently stripped) | `hosted_api.py::create_api_key` (child-policy clamp), `hosted_api.py::_MINTABLE_SCOPES` |
| Minted keys cannot mint / rotate / reach management | `hosted_api.py::_reject_minted_delegated_key` — the definition, and every handler that calls it (mint, rotate, management) |
| Data-plane scope enforcement (write implies read, never post-filter) | `hosted_api.py::_require_scope` |
| Graph-bound key ownership pre-check + org-surface rejection | `hosted_api.py::_assert_graph_owned`, `hosted_api.py::_reject_graph_bound_org_surface` |
| A key can only bind a **custom** graph; the default graph is not key-bindable | `hosted_api.py::_ensure_graph_exists` (404 `Unknown graph`) |
| Atomic, cap-neutral, class-inheriting replacement | `hosted_api.py::rotate_api_key` (`rotate`, `#4355`), `tortoise/quota.py:487` (`api_key_occupies_slot`) |
| Key cap per org = 2 / 5 / 10 / 20 (free/solo/pro/team) | `product/pricing.json` → `tiers.*.max_api_keys` |
| Metering identity is `(org_id, period_start)` | `tortoise/metering.py:6`, `:108`, `get_current_usage` |
| No embedder/parent linkage in the control plane | `supabase/migrations/0006_teams.sql:23-43` (no parent column); no `embedder` concept in `tortoise/` |
| Graph creation: **two routes, one service** — key auth exists (`graphs:create`, non-minted key only) plus a session alias | `@app.post("/v1/organizations/{org_id}/graphs")` → `create_org_graph` (`key_ctx: dict = Depends(get_current_org_gated)`; `deleg=0` rejected, then the `graphs:create` scope check); `@app.post("/v1/graphs")` (`POST /v1/graphs`, session alias) |
| `rotate` is pinned to the caller's OWN org — a cross-team target key is a 403 | `hosted_api.py::rotate_api_key` (`_ensure_key_in_pinned_org(…, required=True)`); helper contract `hosted_api.py::_ensure_key_in_pinned_org` ("must own the key UNCONDITIONALLY") → `403 "Not your API key"` |
| `DELETE /v1/team/keys/{key_id}` carries the same pin on **both** lanes; key auth cannot override it | `hosted_api.py::revoke_api_key` — two `_ensure_key_in_pinned_org(…, required=True)` call sites (supabase lane, registry lane); handler docstring "Org-scoped"; the key lane resolves the org from the key and ignores `?org_id=` |
| A minted (`deleg=0`) credential cannot rotate anything, itself included | `hosted_api.py::_reject_minted_delegated_key(org, "rotate API keys")`, called from `rotate_api_key` |
| `api_key_create` and `api_key_rotate` are audited | `hosted_api.py::create_api_key` (audits `api_key_create`), `hosted_api.py::rotate_api_key` (audits `api_key_rotate`) |
| The member self-service / recovery lanes audit a **third** action, `api_key_mint` — not evidence for create/rotate | `_async_audit(…, "api_key_mint", …)` inside `hosted_api.py::session_key` and `hosted_api.py::_session_key_supabase` (the two `#2446` lanes, resolved by symbol — no line numbers, see §0); named here so the three action names are not conflated |
| **Key revoke writes NO audit row** | `hosted_api.py::revoke_api_key` (handler body, zero `_async_audit`); `supabase_control.revoke_api_key:1377` is a bare PATCH — **gap, filed as #9177** |
| Parent revocation does not cascade to children | `#8694` OPEN (`#2427` §2.4) |
| `#524` OAuth machinery shipped | `tortoise/oauth.py`; `@app.get("/.well-known/oauth-protected-resource")`, `@app.get("/oauth/authorize")`, `@app.post("/oauth/consent")`, `@app.post("/oauth/token")`, `@app.post("/oauth/revoke")`, `@app.post("/register")` (DCR) |

---

## §9 SURFACE REQUEST — needs Daniel's approval, **not cut here**

`AGENTS.md` → MCP/SDK Surface Approval: no tool is added to `TOOL_REGISTRY`, no public `TortoiseSDK`
method is added, by this design. What the tier needs is named here so the approval decision and the
`#557` decomposition have one contract to build against:

1. **v1 — cross-team provisioning + lifecycle authority (required, REST/internal only).** A
   capability that mints a child credential **inside a team the caller provisioned**, **and** can
   **rotate and revoke the credentials it minted**, under the caller's own authority.
   **Verbs in scope:** `POST /v1/team/keys` (mint) **plus** `POST /v1/team/keys/{key_id}/rotate` and
   `DELETE /v1/team/keys/{key_id}` — only for a key whose `created_by_key_id` names the calling
   credential. Without the two lifecycle verbs §5's Replace/Revoke story cannot be built at all: as
   of this head both are pinned to the caller's own org (§5.2, §8) and the child cannot rotate
   itself (`hosted_api.py::_reject_minted_delegated_key`). The authority is the same "teams it provisioned, nothing else"
   boundary as minting (§3.3 properties 1 and 5) — it is **not** a master token and **not**
   impersonation (D2/Q1).
   **The alternative shape, stated so nobody re-derives it:** a per-end-user *manager* credential
   held **inside** the end-user's team, used to rotate that team's end-user credential. It needs no
   cross-team reach, but it needs a **deleg-NULL provisioning lane** (`rotate` rejects every
   `deleg=0` caller at `hosted_api.py::_reject_minted_delegated_key`) and it places an owner-class credential in the very team the end
   user's graph lives in, which weakens §6.1's dormancy argument. Recorded as the fallback.
   Candidate shapes for the cross-team authority, cheapest first:
   - **S-a (recommended)**: extend the existing mint route with an explicit, separately-authorised
     *target team* parameter — reusing the shipped `create_api_key` child-policy path, so the
     created class cannot be widened by the caller.
   - **S-b**: an embedder-scoped service credential (`embedder_…`) carrying a
     `subtenant:provision`-class scope, minting only into its own child teams.
   - **S-c**: keep it off the public surface entirely — the `/internal/provision` pattern (Edge
     Function, caller-JWT, `#802`) so nothing new is reachable by customer keys.
   - **No MCP tool and no SDK method** is proposed for any of these: end users never speak MCP to
     Tortoise, and builders already have REST/Edge-Function provisioning.
2. **v2 only — token exchange** (`grant_type=…token-exchange` on `/oauth/token`) is the recorded
   upgrade (D1) and needs its own approval when it is scheduled. It is **not** part of this design's
   ask.
3. **Not requested:** an end-user login, an end-user consent screen, per-resource grants, a policy
   engine (D4 forbids it), any master-tenant impersonation (D2/Q1 forbids it).

If the owner prefers to defer *all* surface growth, the tier can ship v1 with **S-c only** at the
cost of a provisioning path that lives in the hosted control plane rather than the product API.

---

## §10 Preconditions, residuals, non-goals

### Preconditions before this tier is exposed to real end users

1. `#557` S1 provisioning (teams + custom graphs, per §3.2) — unbuilt, scoped, zero sub-issues.
2. **Cross-team lifecycle authority (§9.1)** — or the in-team manager shape it names as the
   fallback. Without it, the Replace button and the containment runbook both 403 from the builder's
   org (§5.2, §5.3), so the tier's headline UX is unimplementable.
3. `#8694` (revocation cascade) **or** a provisioning-side child enumeration — otherwise the
   provisioning-credential compromise is unbounded in N (§5.3).
4. An audit row on **revoke** (`#9177`, §8) — the compromise story's "old credential dead" must be
   *checkable* afterwards, not merely true.
5. `keys.audit.read`-equivalent read surface (`#2427` §4.1) — without it the builder cannot *see* the
   trail it is accountable for.

### Accepted residuals

- R-1: no per-end-user caps in v1 (D2/Q6) — one end user can spend the team's allowance; the bound is
  the team's tier limits, not a per-user quota.
- R-2: the builder is trusted with the end-user map and with the credential plaintext (reveal-once).
  Tortoise cannot detect a builder that leaks a live credential to its own user — by design the tier
  assumes the builder's server is the trust anchor (§2).
- R-3: end-user team deletion / export semantics are `#557`'s, not this design's.

**Non-goals** (unchanged from the issue): any implementation; `#557`'s data-isolation layer; per-member
grants (phase 4 of `#2427`); an end-user Tortoise account; federation.

---

## §11 Acceptance indicators for this design (the issue's own list, made checkable)

1. **Identity model** — an end user is an opaque builder-held id; no Tortoise account, no membership
   row, no PII (§2) ✔
2. **Delegated credential contract** — class, team placement, scopes, expiry, lineage, and the
   cross-team minting **and lifecycle** authority named as a surface request rather than cut (§3,
   §5.2, §9.1) ✔
3. **Compromise UX** — one Replace action, one backend call **from the builder's app** (the
   Tortoise-side cross-team reach is the §9.1 request, measured as absent — §5.2), same powers, old
   credential dead atomically, and the containment preconditions stated (§5, §10) ✔
4. **Isolation + metering mapping** — three shipped locks, per-team ledger free, roll-up contract
   handed to `#557` S3 (§6) ✔
5. **Abuse + audit** — velocity evaluation shipped; per-embedder provisioning limit in S1; builder-
   readable audit with its two measured gaps named and filed (§7, §8 — revoke audit: `#9177`) ✔
6. **Builder vs Tortoise** — the nine-row split in §7 ✔

---

## Sources

- `#2427` brief — `docs/research/2026-10-15-2427-team-permission-model.md` §2.3–2.4, §4.1–4.4, §7
- `#557` scoping + owner decisions — `docs/scoping/2026-08-15-557-sub-tenancy-scoping.md`; `#557`
  comments 2026-08-13 (direction) and 2026-08-15 (Q1–Q7)
- `#524` scoping — `docs/scoping/2026-08-15-524-oauth-mcp-scoping.md`; shipped `tortoise/oauth.py`
- Policy/authorization decisions — `docs/authorization-architecture.md` §3.2, §4 (option D,
  `OVERRIDES:`), §5
- Code: `tortoise/hosted_api.py`, `tortoise/quota.py`, `tortoise/metering.py`,
  `tortoise/supabase_control.py`, `product/pricing.json`, `supabase/migrations/0006_teams.sql`
- Open gaps filed/owned elsewhere: `#8694` (cascade), `#9177` (revoke writes no audit row),
  `#8692` (vocabulary + holder field), `#2446`
  (member self-service), `#557` (sub-tenancy provisioning)
