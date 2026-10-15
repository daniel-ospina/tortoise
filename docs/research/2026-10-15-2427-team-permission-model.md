---
title: "Team permission management — hierarchy, permission-based key lifecycle, end-customer self-service (#2427)"
type: research
domain: platform
doc_status: draft
subjects.team: epistemic-team
created: 2026-10-15
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-beta
issue: 2427
tags: [auth, permissions, keys, authorization, ux, rbac, key-lifecycle]
relatedIssues: "#2427, #2446, #2297, #2366, #2380, #2426, #2307, #2535, #4628, #557, #2082, #8692, #8693, #8694"
---

# Team permission management (#2427) — research brief

> **Findings date:** 2026-10-15.
> **Measurement tree:** branch `feat/2427-tm`, base `639137d4d` (main). Every "as built
> today" claim below carries a `file:line` measured on that tree; the external findings
> carry their date and source.
> **Corrected (review round 1, 2026-10-15):** §2.3 item 1 — `rotate` bounds the credential's
> privilege **class**, not its expiry. Expiry is body-settable on rotate (`expires_in` 1..366
> days or an ISO `expires_at`, `hosted_api.py:11365`, `:11413-11414`) and a bootstrap row can
> never be rotated at all (409, `:11374-11384`). §4.1 gains the `org.read` row that the §4.2
> `member` bundle references.
> **Search tool:** `web_search` with `model="sonar"` — **rung 2**. Rung 1
> (`mcp__seo-intelligence__perplexity_research`) is unavailable on this machine
> (`seo-intelligence` is not in the MCP catalog); rung 3 (`exa`) was not needed.
> **Verification:** the fresh-session research verifier of the `research` skill is
> **parent-run** for this lane (this lane has no `task` tool); §10 states the limits of
> self-verification and the adversarial case is carried in §5 and §9.
> **No production code is changed by this issue.** The deliverable is this brief plus
> the follow-up issues in §9.

## 0. Re-measurement — where the issue body is stale

The issue body describes today's gates as "binary owner/admin". That is still true, but
four of its premises were measured and need correcting before the design is read.

| Body premise | Measured 2026-10-15 | Consequence |
|---|---|---|
| "configurable expiry (#2426) is the natural building block" (future) | **Landed.** `_validate_mint_expiry` on any mint class; expiry is **immutable after creation** (no PATCH-expiry); `rotate` inherits the displaced row's `expires_at` **only when the body omits one** (`hosted_api.py:11365`, `:11413-11414`) | Expiry is available now. "Refresh = extend" in place is not a missing feature but a rejected one — but note that `rotate` **does** accept a body-set expiry, so it can mint a longer-lived replacement (§2.3 item 1) |
| member key surfaces are the open gap (#2366/#2307) | **Partly overtaken.** #2426 shipped, and **#2446 already exists** — "member self-service keys — rotate/replace + turn off OWN keys (phase 2 of the #2427 permission model)" | Phase 2 is filed. This brief must answer what phase 2's design premise (`created_by`) actually reaches, and what phases 3+ are |
| "end customers of apps built on Tortoise" | **No end-user auth layer exists.** #557 (sub-tenancy epic) is open and unbuilt; #524 (OAuth 2.1) open | The end-customer tier is a **v2 design**, not a v1 permission-table change |
| key list is an owner-only surface | **Not server-side.** `GET /v1/team/keys` is `_require_keys_manage` only — a **member session passes** (`_require_keys_manage` returns early when `key_id is None`, `hosted_api.py:5830-5836`). Only the *dashboard* hides it | `keys.read` is already member-visible in the API. A permission model must decide the inventory-visibility question explicitly, not inherit it by accident |
| one `admin` role is enough | Admin is **owner minus export/delete** only (`_require_owner` requires `role == 'owner'` exactly, `hosted_api.py:19767-19835`); invite/role-change accept only `admin`/`member` (`:19894`, `:21673`) | The hierarchy is effectively a **two-bit** model (strict-owner bit, admin bit), which is what makes "permissions" the right refactor rather than a fourth role |

## 1. Confirmed problem

**The binary role gate is the wrong seam for key lifecycle, because key lifecycle is not a
role-shaped question.** Three measured dead-ends all reduce to one sentence: *a role decides
who may touch a key, and a role cannot express "this member may touch this key".*

- **Members hold no durable key they can manage.** A member may mint only the 24h
  **bootstrap** key (`POST /v1/session/key`, `purpose=bootstrap`, member-open,
  `created_by = user_id`); `purpose=recovery` (durable) is owner/admin-only
  (`hosted_api.py:25614-25630`, #2380). So a member re-bootstraps daily (#2366 dead-end).
- **An owner-minted key for a member is not the member's.** `created_by` is the **minter**,
  not the holder. #2446 scopes member actions to `created_by == member user_id`, so the
  common case — an owner mints a key and hands it to a member — leaves the member unable to
  replace or turn it off. The #2366 dead-end survives phase 2 unchanged in that case.
- **A compromised key has no self-service remedy below admin.** Rotate and revoke both
  carry `_require_owner_admin_if_session` (`:11330`, `:11707`), so the member who leaked
  their own key must file a ticket, while the leaked key stays live.

**Reframed problem statement.** A team member holding a credential has no legitimate,
self-service way to keep it alive, replace it when it leaks, or retire it — and the team
owner has no way to say "you may do this for *your* key and no other", because the only
vocabulary is a role. The fix is a **permission** vocabulary of which today's role checks
are the coarse special case, plus an **ownership** field that distinguishes the holder from
the minter.

**Falsification check.** If members turn out never to hold durable credentials in practice,
or if the bootstrap key is the intended permanent member credential, the ownership-holder
premise is unnecessary and `created_by` suffices. Measured today both are false: #2446's own
context says "the legit member door" was closed (#2366), and #2535 explicitly defers "does
an invited member ever need a key of their own?" **to this issue** (#2535 §9). The premise
is checked again in §5 option B.

## 2. The model as built (measured)

### 2.1 Roles

| Role | Source of truth | Who gets it | Notes |
|---|---|---|---|
| `owner` | `org_memberships.role` (Supabase) / `Membership.role` (registry) | the creator; never invitable, never demotable (`:19894`, `:21688`) | all powers |
| `admin` | same | invited with `role=admin` | owner **minus** `org.export`, `org.delete` |
| `member` | same | invited with `role=member` (default) | control-plane read + the surfaces listed in §2.2 |

### 2.2 The key lifecycle endpoints and their gates today

| Surface | Route | Gate measured | Permission it implies |
|---|---|---|---|
| Mint (session lane) | `POST /v1/team/keys` | `_require_owner_admin_if_session` (POLICY A) + D13 class matrix | `keys.mint.team` |
| Mint (key lane) | `POST /v1/team/keys` | `keys:manage` on scoped keys; `deleg=0` cannot mint | `keys.mint.team` (capability) |
| Rotate / replace | `POST /v1/team/keys/{id}/rotate` | `_require_keys_manage` + `_require_owner_admin_if_session` + pinned-org | `keys.rotate.any` |
| List | `GET /v1/team/keys` | `_require_keys_manage` — **member session passes** | `keys.read` (all rows) |
| Revoke | `DELETE /v1/team/keys/{id}` | `_require_keys_manage` + `_require_owner_admin_if_session` | `keys.revoke.any` |
| Enable / disable | `PATCH /v1/team/keys/{id}` | `_require_owner_admin` (session, #1148) | `keys.toggle.any` |
| Bootstrap mint | `POST /v1/session/key` `purpose=bootstrap` | member-open; 24h; cap-exempt | `keys.mint.own` (ephemeral) |
| Recovery mint | `POST /v1/session/key` `purpose=recovery` | owner/admin (POLICY A, #2380) | `keys.recover.any` |
| Members | `GET/POST/DELETE/PATCH /v1/organizations/{id}/members`, `/v1/invites` | `_require_owner_admin` | `members.read` / `members.manage` |
| Export | `GET /v1/organizations/{id}/export` | `_require_owner` (strict) | `org.export` (owner) |
| Delete | `DELETE /v1/organizations/{id}` | `_require_owner` (strict) | `org.delete` (owner) |
| Dashboard login | `PATCH /v1/team/dashboard-login` | `_require_owner_admin` | `team.settings` |
| Data plane | every data handler | `_require_scope("graphs:read"\|"graphs:write")` first statement | `graphs.read` / `graphs.write` |

### 2.3 Measured safety properties the permission model must not break

1. **Rotate cannot widen the credential's privilege CLASS — but expiry is body-settable and
   is NOT bounded by the displaced row.** The replacement inherits the displaced row's
   `graph_id`, its `scopes ∩ child policy`, its `delegation_depth` and `created_via`; a
   body-supplied `graph_id`/`scopes` is rejected 422 precisely so that class cannot be widened
   (`hosted_api.py:11353-11362`, inheritance at `:11386-11415`). Expiry is a separate matter:
   the handler parses the body's expiry FIRST
   (`expires_at = _validate_mint_expiry(payload)`, `:11365` — accepting `expires_in` 1..366 days
   or an ISO `expires_at`, `:4452-4510`) and only inherits the displaced row's value when the
   body omitted one (`if expires_at is None: expires_at = row.get("expires_at")`,
   `:11413-11414`), then passes it to `_mint_key(..., expires_at=expires_at, ...)`
   (`:11423-11428`). **No comparison is made against the displaced row's expiry**, so a rotate
   can extend a credential's lifetime by up to 366 days. What is inherited — and cannot widen —
   is the privilege *class*, exactly as the endpoint's own docstring states ("the body cannot
   set privilege").
   **The bootstrap illustration is unreachable.** A bootstrap row can never be rotated:
   `quota.api_key_occupies_slot` returns False for `created_via == 'bootstrap'`
   (`tortoise/quota.py:487-527`), so the handler 409s "API key is not active — rotate requires a
   live key that occupies a key slot" (`:11374-11384`) *before* any expiry inheritance runs. The
   guard that makes the ephemeral credential non-launderable through `rotate` is therefore the
   **cap predicate, not expiry inheritance** — and by the same predicate a member cannot
   re-mint their own bootstrap key through `rotate` at all (§8, phase 2). Expiry-widening is a
   real, currently-unbounded path for any key that *does* occupy a slot; #2446's member replace
   path must therefore bound it (see §4.2, §8).
2. **Rotate is cap-neutral and atomic.** The replacement consumes the released slot and the
   displaced row is claimed in one operation, with compensation on a lost claim
   (`:11421-11480`, #4355).
3. **One level of delegation.** A `deleg=0` child can never hold `keys:manage`
   (`chk_minted_key_no_escalation`), so it can never mint.
4. **Ownership today = minter.** `api_keys.created_by` is the minting user's id; there is no
   holder/assignee column (`tortoise/hosted_api.py:5300`, `supabase/migrations/0007_api_keys.sql:24`).

### 2.4 The gap this brief found that no issue tracks

**Revoking a parent key does not revoke its delegated children.** `created_by_key_id` is
recorded at mint (`:10727`) and read back on resolution (`:5301`), but **no code consults the
parent's `revoked_at`** — a live `deleg=0` child survives its parent's revocation
indefinitely. The "revocation cascades" half of the issue's indicator is therefore
unimplemented, not merely undecided. Filed as #8694 (§9).

## 3. External findings — how comparables model the same three layers

**Convergent pattern 1 — separate the *user-owned* credential from the *org-owned*
credential, and let the holder self-manage theirs.** Datadog distinguishes org-owned API
keys from user-owned personal access tokens, and PATs "inherit that user's permissions"
and may be scoped; Supabase's scoped personal tokens "cannot exceed the creator's existing
access"; GitHub fine-grained PATs are user-owned, repo- and permission-scoped, and GA since
March 2025. **This is the closest structural match to #2446** — and note *all three name the
holder, not the minter*, which is the distinction our `created_by` collapses.

**Convergent pattern 2 — roles stay few and coarse; permissions are designed first.** "Design
permissions before roles, start with three to five common roles, keep hierarchies shallow,
and use tenant/resource scope instead of role proliferation"; RBAC's known failure is *role
explosion* when a role is minted per permission combination. This is directly compatible
with #2446's own sentence "roles = bundles; member hat gets manage-own-keys".

**Convergent pattern 3 — bound the credential in time, not only in scope.** NIST-facing
guidance and vendor practice converge on short-lived, time-bound, narrowly scoped credentials
with permanent keys as a documented exception. Our #2426 made expiry **available** but
opt-in (Never remains the default and the byte-identical legacy mint shape is pinned).

**Convergent pattern 4 — for the tenant-of-tenant tier, the delegated short-lived token
is the mechanism, not a nested key vault.** RFC 8693 (OAuth 2.0 Token Exchange) is the
normative shape: exchange a user token for a scoped, audience-restricted, revocable token
carrying an actor claim, so the end user never holds the platform's credential. This maps
onto `docs/authorization-architecture.md` §4 **option D** (customer IAM / delegation).

**Topology guidance — RBAC vs ReBAC.** ReBAC/Zanzibar is recommended for hierarchical and
shared resources and for "list everything a user can access"; RBAC stays right for flat
job-function permissions; production systems commonly combine the two. **But** this is
already `docs/authorization-architecture.md` §4's decision surface, which carries an
`OVERRIDES:` ruling against adopting an external engine now. §6 applies the contradiction
test.

## 4. Recommendation

**Recommendation: a permission vocabulary, roles as bundles over it, and an `owner_user_id`
(holder) field — in that order.** Concretely:

### 4.1 Permission matrix (recommended)

Permissions are the primitive; today's role checks become their coarse bindings.

| Permission | Ends up gating | Today's gate |
|---|---|---|
| `org.read` | org overview: tier, usage, limits (`GET /v1/team`) | `get_current_org_session_ungated` — ungated; a member session passes (`hosted_api.py:6329`, `:8917`) |
| `keys.mint.team` | mint an org-owned (or owner-class) key | `_require_owner_admin_if_session` + D13 |
| `keys.mint.own` | mint a key the caller holds (bounded) | bootstrap lane only (24h) |
| `keys.read.own` | list the caller's own keys | none (own-slice new) |
| `keys.read.all` | list the org's key inventory (ids, prefixes, lineage) | `_require_keys_manage` (sessions pass — see §0) |
| `keys.rotate.own` | replace a key the caller holds | absent (admin-only) |
| `keys.rotate.any` | replace any key | rotate route |
| `keys.revoke.own` | revoke a key the caller holds | absent (admin-only) |
| `keys.revoke.any` | revoke any key | `DELETE` route |
| `keys.toggle.own` / `.any` | enable/disable | `PATCH` route (`.any` only) |
| `keys.audit.read` | read the key audit trail | audit events exist, no read surface |
| `members.read` / `members.manage` | member list / invite / remove / role change | `_require_owner_admin` |
| `team.settings` | dashboard-login, recording defaults | `_require_owner_admin` |
| `org.export` / `org.delete` | export / delete the whole account | `_require_owner` (strict) |
| `graphs.read` / `graphs.write` / `graphs.create` / `graphs.delete` | data plane + provisioning | `_require_scope`, owner-mint-only escalation scopes |

### 4.2 Role bundles — and the two bits that stay coarse

| Role | Bundle |
|---|---|
| `owner` | every permission |
| `admin` | every permission **except** `org.export`, `org.delete` |
| `member` | `org.read`, `keys.read.own`, `keys.rotate.own`, `keys.revoke.own`, `keys.toggle.own`, `keys.audit.read` — **plus `keys.mint.own`, which requires the reopen in §6, and without it a member can only manage a key an owner assigned to them**. `keys.rotate.own` carries an additional constraint the rotate primitive does not supply: **replacement expiry ≤ displaced expiry** (§2.3 item 1) — otherwise a member's rotate could launder an expiring own key into a durable one |

**Keep strict.** `org.export` and `org.delete` stay bound to the `owner` role, exactly as
today: both are irreversible or whole-tenant disclosures (`_require_owner`,
`hosted_api.py:19767-19835`), and a permission that can be delegated to an admin is a
different product promise than "only the account owner can export or delete". Encoding them
as permissions is fine; making them *grantable below owner* is a product change this brief
does not recommend.

**The ownership correction.** Introduce `api_keys.owner_user_id` (the **holder**) distinct
from `created_by` (the **minter**). `NULL` = **org-owned** (admin-managed: a shared key must
never be rotatable by a member who happens to hold a copy). This is the single change that
makes #2446 reachable for the owner-mints-for-member case (§5 option A2), and it is
additive: existing rows migrate with `owner_user_id = created_by` only where the mint was a
self-service bootstrap; all others stay `NULL` (org-owned).

### 4.3 Tiered actor map

| Actor | Tier | Keys today | With this model |
|---|---|---|---|
| Team owner | account | everything | everything |
| Admin | account | everything but export/delete | same, as a bundle |
| Member | account | 24h bootstrap; can list; cannot rotate/revoke | own-slice read/replace/turn-off; **team mint = reopen** |
| App builder | account (they own their org) | same as owner/admin of that org | unchanged — this tier is already served |
| App **end user** | tenant-of-tenant | **none — no auth layer** | delegated short-lived credential (v2, §7) |

The fourth row matters for scoping: "end customers of the applications built by your
customers" is **not a fifth role on the Tortoise account** — it is a credential the *builder's*
app vends to its own user, exchanged server-side under the builder's own authority. Modelling
it as a role inside the customer's org would put the builder's end users into the builder's
membership table, which is both a data-protection and a UX error.

### 4.4 Compromise flows — rotate, refresh, revoke

| Word | What it should mean | Supported today |
|---|---|---|
| **Replace / rotate** | one action: new secret, same powers (graph, scopes, delegation), old secret dead. Expiry is **not** inherited when the body carries one — the primitive can lengthen it (≤366 days), so "same expiry" must be enforced by the caller (§2.3 item 1) | yes — `rotate` primitive, atomic and cap-neutral |
| **Revoke** | terminal: the secret is dead; recovery is a replace | yes |
| **Refresh** | **ambiguous and partly incoherent.** If it means "extend the existing key in place", the answer is *no*: expiry is immutable in place by design (#2426). If it means "mint a replacement with a later expiry", `rotate` can already do that by body (§2.3 item 1) — which is exactly why the caller, not the primitive, must decide whether that is allowed. If it means "get a working secret again", it is `replace` | `replace` only |
| **Cascade** | revoking a parent kills its delegated children | **no** — §2.4, filed as #8694 |

**The "my key leaked" moment, as a two-step and a one-step.** Planned rotation keeps the
existing overlap path (mint → deploy → revoke old). A *confirmed compromise* must be the
**one-step atomic replace** — because the current implementation revokes the displaced row in
the same operation, there is no window in which both secrets are live, which is exactly what
the comparables prescribe for compromise and what the overlap pattern is *not* for.
Diagnosis of an already-leaked key also needs the audit trail visible to its holder; today
audit events are written (`api_key_create`) but there is no read surface (`keys.audit.read`).

## 5. Design options evaluated

**Option A — permission table + role bundles (recommended).** Three roles, a flat
permission enum, `authorize(principal, permission, surface)` where `_require_scope` /
`_require_owner_admin` are today, plus the holder field.

- Reach: every dead-end in §1; no new role; the UI keeps three hats.
- Cost: one enum, one resolver, one ownership column, one migration, the endpoint gates
  re-expressed.
- Tradeoff: does **not** answer "member A may rotate member B's key" — which is the point
  (that stays an admin action).

**Option A2 — A plus per-member grant exceptions.** Owner may additionally grant a named
permission to a named member (`keys.rotate.any` for a team lead).

- Reach: the "powerful" half of the owner's framing.
- Cost and risk: this is where **role explosion returns through the side door** — a
  per-member grant table needs an expiry, a recertification sweep, and an audit read
  surface, or it becomes standing privilege nobody remembers granting. The external guidance
  (design permissions first, keep roles few, put variation in *scope* not *roles*) points the
  other way.
- Verdict: **defer — phase 4, not v1.** The permission vocabulary is the prerequisite that
  makes it possible later; shipping grants before the vocabulary is how the sprawl starts.

**Option B — keep binary roles; fix only the member self-service gap by making the bootstrap
key durable.** No permission table; extend the bootstrap TTL, or let members mint a durable
key directly.

- Strongest argument for it: it is the smallest possible change, and if members are rare and
  the bootstrap key is *intended* to be the member credential, the rest is speculative.
- Why it is rejected on outcome quality, not diff size: (1) it makes the **ephemeral**
  credential durable, which discards the only time-bound member control we have, at the exact
  moment we gained the tool to bound it; (2) it does not fix the owner-mints-for-member case
  at all; (3) it leaves admin as a two-bit role that still cannot express "own only". If the
  premise check in §1 ever flips — members never hold durable keys — Option B collapses into
  "no work", which is the honest outcome to record, not to design around.

**Option C — adopt a relationship engine (SpiceDB / OpenFGA) now.** Tuples
(`team` → `graph` → `key`), relations from `memberOf` / `ownedBy`.

- Reach: the strongest model for ownership-as-relationships, and it answers "list everything
  this principal can touch" natively.
- **Not adoptable here.** `docs/authorization-architecture.md` §4 carries an explicit
  **`OVERRIDES:`** ruling — the industry default (externalize the decision into a Zanzibar-style
  engine) is *deliberately not adopted now*; enforcement stays in the app-layer resolver behind
  `authorize(principal, action, resource)` until a per-resource binding exists. The comparison
  in §3 does not change that; it is the evidence a reopen would use, not a licence to adopt.
- Verdict: **out of scope; route = reopen `#2083`'s scope decision** per the contradiction test.

## 6. Contradiction test / adoption gate

Run **first**, before cost or fit, per the user-questions protocol.

| External finding | Contradicts a recorded decision? | Verdict |
|---|---|---|
| Roles = few coarse bundles; permissions designed first | No | **Adopt** (recorded here; no owner question) |
| Members self-manage a credential they *hold* | No — this is the **already owner-approved direction** of #2446 ("the compromised-key moment for a team member = one Replace key button on their OWN key row") | **Adopt** |
| Members **mint their own durable** key | **Yes — POLICY A.** #2297's owner decision (2026-09-05) gates the SESSION lane of key mint owner/admin-only; #2366/#2380 implemented it; #2535 §6 records "invitee mints their own key during onboarding" as a **HARD STOP** against it | **Not a candidate for adoption.** Route: **reopen #2297** with this evidence in front of the owner. The brief records the argument; the build issue is a decision request, not a work ticket |
| Expiring-by-default credentials | No explicit decision *for* `Never` as default, but the byte-identical legacy mint shape **is** pinned deliberately (`test_writer_inventory` exact-equality) | **Owner question, not adopted.** Recommended for v1: keep `Never` the default, make expiry prominent at mint |
| External ReBAC engine | **Yes — `docs/authorization-architecture.md` §4 `OVERRIDES:`** | **Not adoptable.** Route = reopen the `#2083` scope decision |
| RFC 8693 token exchange for end users | No recorded decision against; `#2082` §4 option D anticipates it | Aligned but **blocked on an unbuilt end-user auth layer** → v2, §7 |

**Adoption gate:** `adopt` (permission table + role bundles + holder field);
`reopen #2297` (member durable mint);
`owner question` (default expiry);
`reopen #2083` scoped (ReBAC).

## 7. Over-scoped for v1 — the exaggeration check the owner invited

| In the owner's framing | Verdict |
|---|---|
| "end customers of the applications built by your customers can rotate a key" | **OVER-SCOPED for v1.** Needs an end-user auth layer that does not exist (#557 open/unbuilt, #524 open). It is also not a role in the customer's org (§4.3). v1 = team members only |
| "refresh it if it gets compromised" | **Partly incoherent.** Refresh ≠ extend in place (expiry is immutable in place, §4.4 — but a body-set expiry on `rotate` *can* lengthen a replacement, §2.3 item 1). v1 vocabulary: **Replace** and **Revoke**; drop "Refresh" from the UI entirely |
| permission grants per member | **OVER-SCOPED (defer to phase 4).** The permission *vocabulary* is v1; grants need recertification machinery (§5 option A2) |
| "super admin or owner" as two tiers | **Moot.** There is no super-admin tier and none is needed; `owner` is already the strict tier and `admin` the delegated one |
| a ReBAC/Zanzibar engine | **OUT OF SCOPE** and contradicting a recorded `OVERRIDES:` ruling (§6) |

**Core (v1):** the permission vocabulary and its resolver; roles as bundles; the holder
field (`owner_user_id`) and the org-owned/shared-key rule; member replace-own and turn-off-own
(#2446); audit visibility for the holder; revocation cascade for delegated children (#8694).

## 8. Phased path from today's binary gates

| Phase | Content | State |
|---|---|---|
| 0 | configurable expiry + `isManagedKey` (#2426) | **LANDED** |
| 1 | this brief — the model, the matrix, the over-scope call | **this doc** |
| 2 | member self-service on **own** keys: replace + turn-off (#2446) | filed, open — **needs the holder-field correction of §4.2 to be reachable, and a decision on the bootstrap 409 below** |
| 3 | the permission vocabulary + `authorize(principal, permission, surface)` + role bundles; every gate in §2.2 re-expressed | **filed by this brief as #8692** |
| 4 | per-member grant exceptions (only if phase 3 shows a real need) | not filed, deliberately |
| 4b | revocation cascade to delegated children | **filed as #8694** |
| 5 | end-customer (tenant-of-tenant) delegated credential self-service | **filed as #8693**, blocked on #557 / #524 |

Sequencing note: phase 2 can land before phase 3 — #2446 only needs the **holder field**, not
the whole vocabulary. Phase 3 is the prerequisite for any *new* capability (e.g. an "own"
slice broader than replace/turn-off).

**Phase 2 open question — member replace cannot reach a bootstrap holder through `rotate`.**
`rotate` 409s on any row `api_key_occupies_slot` rejects, and that predicate excludes
`created_via='bootstrap'` (`tortoise/quota.py:487-527`; `hosted_api.py:11374-11384`). The
bootstrap key is the only durable-ephemeral credential a member holds today (§1), so a member
self-service replace built on the admin `rotate` route would 409 on exactly the case §1 is
about. Two ways out, and this needs a decision rather than a silent widening:

- **Keep the 409** (provisional recommendation) and give members a distinct replace that mints
  through the bootstrap lane with a bounded expiry — the cap predicate is what keeps rotation
  slot-neutral, and relaxing it for members reopens the cap hole the 409 was added to close.
- **Extend `rotate` to the bootstrap lane** with an explicit bounded expiry (≤ the displaced
  row's), which is a change to the cap predicate and therefore belongs in a decision, not in a
  phase-2 implementation detail.

## 9. Gaps and risks this closes, and what was filed

| Dead-end | Closed by |
|---|---|
| #2366 member holds no manageable durable key | holder field + (with the #2297 reopen) `keys.mint.own`; #2446 for the self-managed half |
| #2380 recovery gate leaves members keyless after 24h | bounded by design; the durable answer is the reopen, not a widening of `recovery` |
| #2307 / #4628 member sent to an owner-only surface | the own-slice read (`keys.read.own`) + a member-visible own-keys view; #2446 supplies the UX, #4628 tracks the empty-state symptom |
| #2426 expiry as an unused primitive | expiry becomes the bound on `keys.mint.own` instead of a manual setting |
| "revocation cascades" (issue indicator) | **#8694** — measured: no parent-liveness check exists (§2.4) |

**Issues filed by this brief:**

- **#8692 — phase 3: the permission vocabulary + role bundles + `authorize(...)`**, with the
  endpoint→permission matrix of §4.1 as its acceptance table.
- **#8693 — phase 5: end-customer (tenant-of-tenant) credential self-service**, blocked on
  #557 / #524, explicitly out of v1 scope.
- **#8694 — bug: revoking a parent key does not revoke its delegated children** (measured
  gap, §2.4).

**Recorded as decision requests (not work tickets):**

- Reopen **#2297** for `keys.mint.own` (member durable mint) — evidence in §3, argument in §6.
- Default-expiry policy — owner question, §6.

## 10. Confidence, sources, limits

| Claim | Confidence | Basis |
|---|---|---|
| Today's gates, the `created_by`-only ownership, the class-only inheritance of `rotate` (expiry is body-settable and unbounded by the displaced row, `:11365`/`:11413-11414`), the bootstrap 409 (`quota.py:487-527`), the absent parent-liveness check | **High** | measured on this tree (`hosted_api.py`, `quota.py`, `0007_api_keys.sql`), each cited by line |
| User-owned vs org-owned credential separation; roles-as-bundles; short-lived-credential convergence | **High** | three or more independent vendor sources (Datadog, Supabase, GitHub) and independent practitioner guidance |
| RFC 8693 as the tenant-of-tenant mechanism | **Medium** | normative spec plus two independent secondary sources; not validated against a comparable that ships it for exactly this tier |
| "Replace is the right one-step compromise UX" | **High** for the mechanism, **medium** for the copy | the primitive is atomic and cap-neutral; the wording is a UX decision, not a research finding |
| ReBAC not adopted | **High** | it is a recorded decision, not a finding; recorded so the next reader does not re-litigate it |

**Sources.** Datadog, *Personal Access Tokens* and *API and Application Keys*; Supabase,
*Personal Access Tokens* and *Access Control*; GitHub, *Fine-grained PATs are now generally
available* (2025-03-18) and *Managing your personal access tokens*; Stripe, *Best practices
for managing secret API keys*; Zuplo, *API key rotation lifecycle management* and
*fine-grained API authorization*; OneUptime / Corsair / RoxyAPI rotation guides; CISA
eviction-strategies CM0083; OpenFGA *What is ReBAC?* and *Authorization concepts*; Authzed
*An introduction to Google Zanzibar*; Manning, *Taming authorization: RBAC, ABAC, ReBAC*
(ch. 17); Evolveum, *Role explosion*; credential-governance and RBAC design guides; IETF
**RFC 8693**, *OAuth 2.0 Token Exchange*; Zitadel, *Token Exchange*; Google Cloud, *Best
practices for protecting developer credentials*. URLs were read on 2026-10-15 and are
deliberately not inlined as links, matching this repo's docs convention.

**Internal:** `tortoise/hosted_api.py` (`_require_owner_admin` :19641, `_require_owner`
:19767, `_require_owner_admin_if_session` :19741, `_require_keys_manage` :5822, create
:10986, rotate :11277, list :11511, revoke :11657, toggle :11851, session key :25535, invites
:19888, members :21582-21664, export :21848, delete :23043);
`supabase/migrations/0007_api_keys.sql`; `docs/auth-architecture.md` §6;
`docs/authorization-architecture.md` §2, §4, §7; `docs/scoping/2026-09-25-2535-invited-user-onboarding-design.md`
§0, §9; `docs/epics/2026-08-29-agent-driven-onboarding-1976/09-subgraphs-permissions-note.md`.

**What this brief does not claim.** It does not claim the fine-grained data-plane layer
(`#2082`) is in scope or should be built; it does not select a policy engine; it does not
decide the `#2297` reopen (it argues for it); and it does not validate the end-customer tier
against a shipping comparable. The fresh-context research verifier is parent-run for this
lane; the counter-argument to the recommendation is carried in §5 option B, and the two
claims most likely to be wrong if re-measured are the reachability of #2446's `created_by`
premise (re-read `rotate`/`revoke` gates after phase 2 lands) and the `owner_user_id`
migration backfill rule.
