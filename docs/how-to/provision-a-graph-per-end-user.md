---
title: "Provision a graph per end-user (hosted API)"
type: how-to
domain: platform
doc_status: live
created: 2026-11-13
ownedBy: epistemic-team
aboutSubjects: Tortoise hosted credential model
aboutObjects: "#12447, #2111, #2112, #2114, #8693, #557, #12444, tortoise/hosted_api.py, tortoise/supabase_control.py"
---

# Provision a graph per end-user (hosted API)

**The task.** You are building an app where every end-customer has an agent with
private memory. You want one account-level credential to create **one memory
graph per end-user**, and to hand each end-user's process a credential that
reaches *only their* graph and **cannot** create more.

**That is one call.** It is shipped, it is supported, and — until #12447 — it was
invisible: the obvious request failed with a message that named neither the right
endpoint nor the missing scope.

> **Canonical terms.** **Organisation account** is defined in
> `docs/ONTOLOGY.md` §5 (Core Kind Vocabulary, the account-layer note) and, with
> the account-vs-subject distinction, in `docs/registry-graph-schema.md`
> ("Definitions — account layer vs in-graph Subjects"), whose schema is where the
> `Graph` and scoped `APIKey` rows live. In this guide: an **organisation account**
> is the billing and plan boundary; it owns many **memory graphs**; a
> **credential** (API key) authenticates to an account or to one graph. The mid
> tier is called **Builder** in the product and `pro` in the API (#12444) — they
> are the same plan.

---

## 0. What you get, in one paragraph

```text
account-level key  ──►  POST /v1/organizations/{org_id}/graphs
                          └─► 201 { graph: {...}, key: {...}, key_plaintext, revealed_once }
                                │
                                └─► hand `key_plaintext` to the end-user's process
                                      it reaches THAT graph only, and cannot provision
```

The 201 **also mints the graph's key**, so provisioning an end-user's graph and
handing them a credential is a single request — no second call, no separate
"create key" step, no way to end up with a graph that has no key (a failed mint
rolls the graph back).

---

## 1. Confirm the plan and the credential

**Plan.** Any tier from **`solo`** up may provision custom graphs (#2111
Gate #2 blocks only `free`/`anon`):

| tier | custom graphs | `max_api_keys` |
|---|---|---|
| `free` / `anon` | **blocked** — `402`, header `X-Upgrade-CTA: pro` | 2 / 1 |
| `solo` | **1** (the default graph fills the second slot) | 5 |
| `pro` — "Builder" | unlimited (`max_graphs` is `null`) | 10 |
| `team` | unlimited | 20 |

Read your own limits rather than guessing — one call, with the account-level key:

```bash
curl -s https://app.premiselabs.co/v1/team \
  -H "Authorization: Bearer $TORTOISE_API_KEY"
# → {"org_id": "...", "tier": "pro", "max_graphs": null, "max_api_keys": 10, ...}
```

`org_id` from that response is the `{org_id}` in every call below.

⛔ **`max_api_keys` is the real bound on this model, not `max_graphs`.** Every
provisioned graph gets one key, and per-graph keys are counted against the
account's key allowance (`2 / 5 / 10 / 20`). An account on `pro` can hold
unlimited graphs but only **10** keys, so a graph-per-end-user model inside one
account is bounded by the key cap. Hitting it is a `409` *and* a rollback: the
graph is not created, so you never get a keyless graph. Delete graphs you no
longer need (step 5) or move to a plan with a higher key allowance.

**Credential.** The call needs a key holding the **`graphs:create`** scope. Two
credentials usually have it already:

- the account's **original full-access key** (the `tt_` key minted at signup —
  `legacy_full_access`), or
- an **owner-class key you minted with `graphs:create`** via `POST /v1/team/keys`
  (an owner or admin can do this from the dashboard; the allowlist also carries
  `graphs:delete` for step 5).

A **minted key cannot**: they are one-level-deep by design (§4).

---

## 2. Provision the graph

```bash
curl -s -X POST "https://app.premiselabs.co/v1/organizations/$ORG_ID/graphs" \
  -H "Authorization: Bearer $TORTOISE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "enduser-42", "scopes": ["graphs:read", "graphs:write"]}'
```

- `name` — required, unique among the account's live graphs (`409` if taken).
- `scopes` — optional; the scopes the **minted** key gets. They are filtered
  through the child policy, so only `graphs:read` and `graphs:write` can survive
  (`graphs:create` and the other escalation scopes are stripped, not rejected).
  Omitted or all-filtered ⇒ the safe default, `["graphs:read"]`.

The `201` body:

```json
{
  "graph": {"id": "g_...", "name": "enduser-42", "kind": "custom",
            "namespace": "org_<org>_g_...", "status": "active",
            "created_at": "..."},
  "key": {"id": "...", "graph_id": "g_...", "scopes": ["graphs:read", "graphs:write"],
          "created_at": "..."},
  "key_plaintext": "tk_...",
  "revealed_once": true
}
```

`key_plaintext` is shown **once** — nothing retrieves it again (the store holds
only a salted hash). Persist it into your own secret store before dropping the
response.

Persist `graph.id` too. There is no key-driven **REST** graph-listing route —
`GET /v1/graphs` is session-only and answers an API key with
`403 GRAPH_LISTING_VIA_SESSION` — but the id is not lost if you miss it: keep it
from this 201, or recover it later from **`GET /v1/team/keys`** (each row carries
its key's `graph_id`; it needs the account's full-access key, or a key holding
`keys:manage`) or from the MCP tool **`tortoise_list_graphs`**, which answers a
key-driven HTTP MCP caller with the calling account's graph names. `GET /v1/team`
with a key reports limits (`max_graphs`, `max_api_keys`), not graph ids.

**Do not call `POST /v1/graphs` for this.** That endpoint is the *dashboard's*
session-authenticated alias for the same service. With an API key it answers
`403 PROVISION_VIA_ORG_ENDPOINT`, telling you exactly this — and with a session
it works, which is why the dashboard's own "create graph" button is not the
builder path.

---

## 3. Hand the minted key to the end-user's process

The `tk_` key is graph-bound and scoped:

- it authenticates **only against its own graph** — a request that would reach
  another graph, or the account's default graph, is refused
  (`_assert_graph_owned`, `_require_scope`);
- it carries data-plane scopes only (`graphs:read` / `graphs:write`);
- it cannot reach account or team surfaces. `GET /v1/team` answers
  `403 GRAPH_SCOPED_TEAM_SURFACE — "Graph-scoped keys cannot access team overview"`,
  and team management, key minting and graph deletion all refuse it.

So the end-user's runtime needs nothing but that key and the base URL:

```bash
curl -s https://app.premiselabs.co/v1/points \
  -H "Authorization: Bearer tk_..."      # lands in enduser-42's graph, only
```

---

## 4. The rule: one level deep — and why it is the feature

**A key minted by provisioning can never provision.** Not "is not granted the
scope by default" — *cannot hold it*. Three independent locks:

1. **Child policy** — the mint filters requested scopes through
   `_MINTABLE_SCOPES = ("graphs:read", "graphs:write")`
   (`hosted_api._mint_graph_key`). Escalation scopes (`graphs:create`,
   `graphs:delete`, `keys:manage`, `team:manage`) never pass.
2. **Database CHECK** — `chk_minted_key_no_escalation` refuses a `delegation_depth = 0`
   row carrying an escalation scope, so a mint that bypassed the app still cannot
   be stored.
3. **The door** — `create_org_graph` refuses any `deleg = 0` caller in its first
   checks, before any read or write (`403 PROVISION_REQUIRES_ORG_KEY`), which is
   why the rule holds even for a row written straight into the control plane by
   something else. A `deleg = 0` key with **no** data scope never reaches that arm
   at all — the auth dependency refuses it earlier as `KEY_NOT_USER_MINTED`.

**This matters because it is what makes the model safe to hand out.** Your
end-user's credential is a *capability*, not a *delegate*: if their process is
compromised, the blast radius is their own graph. If a minted key could hold
`graphs:create`, one compromised end-user credential could create graphs
indefinitely inside your account, and a per-end-user credential would be an
account-level credential with extra steps. Treat the constraint as a property you
depend on, not as an inconvenience:

> **Never mint the end-user's key from an end-user's key.** Provision from the
> account-level key on your server, and only ever hand out what the mint returns.

---

## 5. Churn: delete a graph when an end-user leaves

```bash
curl -s -X DELETE "https://app.premiselabs.co/v1/graphs/$GRAPH_ID?org_id=$ORG_ID" \
  -H "Authorization: Bearer $TORTOISE_API_KEY"   # needs graphs:delete (owner-class)
```

This is a **soft delete** — a tombstone, not an erasure — but read the three
caveats before you build a churn story on it:

- **The window is 7 days** (`retention.RESTORE_WINDOW_DAYS`). Restore works inside
  it; after it the purge erases the graph's data and every backup artifact, and
  nothing is recoverable. The control-plane **tombstone row is kept** (audit) and
  stamped as purged — so the graph's *name* can still be seen while its data is
  gone. A "restore when the customer comes back" flow built on a longer horizon
  silently finds nothing.
- **Restore is not callable with your API key.**
  `POST /v1/graphs/trash/{graph_id}/restore` is a *session + owner/admin*
  surface, so it is a dashboard/operator step, not something your server can do
  with the credential this guide uses.
- **A restored graph has no live keys.** The delete revoked every key bound to
  the graph and restore does not resurrect them — re-mint and re-deliver to the
  end user (step 2). The data itself is intact (the graph's namespace is
  gid-keyed and untouched by the tombstone) and restore re-occupies the quota
  slot.

The delete itself revokes every key bound to the graph, drops its ACL user, frees
the quota slot and the graph name, and physically deletes no data inside the
window. `graphs:delete` is **not** in the child policy, so a minted key can never
do this — deletion is an account-level operation, which is the same
one-level-deep rule seen from the other side.

---

## 6. Refusals, and what each one means

**The credential-scope refusals** are `403` with
`detail: {"error_code", "message"}` — four added by #12447
(`PROVISION_VIA_ORG_ENDPOINT`, `PROVISION_REQUIRES_ORG_KEY`,
`PROVISION_SCOPE_REQUIRED`, `GRAPH_LISTING_VIA_SESSION`) plus the one they are
modelled on, `GRAPH_SCOPED_TEAM_SURFACE` (#2114). The plan/quota gates below them
keep their pre-existing shape: a **string** `detail`, plus a header where one
exists (`X-Upgrade-CTA` on the 402, `X-Graph-Quota` on the graph-quota 409 — the
key-cap 409 carries the string detail and no header at all). Do not write a
client that assumes `detail.error_code` exists on every error.

| `error_code` | You did | Remedy |
|---|---|---|
| `PROVISION_VIA_ORG_ENDPOINT` | called `POST /v1/graphs` with an API key (the session alias) | call `POST /v1/organizations/{org_id}/graphs`, §2 |
| `PROVISION_REQUIRES_ORG_KEY` | called the provisioning door with a **minted** key | provision from the account-level key; §4 |
| `PROVISION_SCOPE_REQUIRED` | called it with an account key that lacks `graphs:create` | mint/grab a key carrying `graphs:create`, §1 |
| `GRAPH_LISTING_VIA_SESSION` | called `GET /v1/graphs` with an API key | keep `graph.id` from the 201, or recover it from `GET /v1/team/keys` / the MCP tool `tortoise_list_graphs`, §2 |
| `GRAPH_SCOPED_TEAM_SURFACE` | a minted key touching a team/account surface | expected — the key is graph-scoped, §3 |
| `402` + `X-Upgrade-CTA: pro` | the account is `free`/`anon` | upgrade to Builder (`pro`), §1 |
| `409` + `X-Graph-Quota: n/max` | the graph quota is full | `solo` holds 1 custom graph; upgrade, or delete a graph |
| `409` "API key limit reached" | the **key** cap is full | delete an end-user's graph (frees its key), or upgrade |

`GET /v1/team/keys` deserves one line of its own, because it is how a builder
recovers graph ids and rotates credentials with a key on the server: it needs the
account's full-access key or a key carrying `keys:manage`, and each row carries
`graph_id`, `scopes`, `delegation_depth`, `enabled` and `expires_at` — no
plaintext, ever.

---

## 7. What is not shipped — and what this model is not

**A separate account per end-user** — the tenant-of-tenant model where each
end-user gets their own *organisation* and membership — is **designed, not
built** (`#8693`, gated on `#557` S1). What ships today is the model in this
guide: **one account, one graph per end-user, one graph-scoped key per end-user**.
The end-user has no Tortoise account, no membership row and no PII on our side —
they hold a key. If you need per-end-user *accounts* (their own billing, their own
teammates), that is `#8693` and it is not available yet.

⛔ **And the shape in this guide is not the sub-tenancy tier.**
`docs/scoping/2026-10-19-8693-end-customer-credential-design.md` records this
exact shape — *one embedder org, one graph per end user, credential bound to that
graph* — as **Design B, a rejected alternative**: it contradicts that design's
D2 (each end user = one *team* + one graph), and the key-cap arithmetic in §1 is
precisely why it was rejected as the tier. So:

- it is **supported as a builder pattern** — that is what this guide documents,
  and it is the only per-end-user isolation that ships today;
- using it **as** the tenant-of-tenant tier is not a decision this guide makes.
  That is a reopen of `#557` Q1, argued in its own home, not a conclusion drawn
  from a how-to.

If you are designing the tier rather than integrating against it, read `#8693`
first and do not treat this guide as its endorsement.

---

## See also

- `docs/authorization-architecture.md` — the tenancy contract, the graph-as-isolation-boundary
  decision, and the enforcement spine (`_require_scope`, `_assert_graph_owned`,
  `_reject_graph_bound_org_surface`).
- `docs/registry-graph-schema.md` — the `Graph` / scoped `APIKey` / quota schema behind these calls.
- `docs/scoping/2026-10-19-8693-end-customer-credential-design.md` — the tenant-of-tenant design (§7).
- `docs/product/beta-sdk-surface.md` — the tenancy API a builder needs, and where it is headed.
- #12447 (this guide's issue) · #2111 (provisioning service) · #2112 (key lifecycle) ·
  #2114 (scope/binding enforcement) · #12444 (Builder = `pro`).
