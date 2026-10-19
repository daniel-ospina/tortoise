---
title: "Authorization architecture — memory topology ≠ authorization topology (Principal / Scope / Capability, security bindings, provenance-derived scoping) — #2082"
type: design
domain: platform
doc_status: draft
created: 2026-10-11
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# Authorization architecture — memory topology ≠ authorization topology

> **Status: DESIGN + AUDIT, NOT FROZEN.** This is the deliverable of **#2082**
> (research track, sibling of the shipped coarse-boundary epic **#2083**). It
> records the model, the **re-measured** current state, the tenancy-contract
> reversibility audit, and the open questions with the decisions that exist or an
> explicit deferral with a trigger. It commits to no implementation and changes
> no code.

## §0 Scope and the decisions that govern it

**In scope:** the Principal / Scope / Capability model; the security-bindings-by-node-ID
layer; provenance-derived authorization with explicit promotion; the read ≠ write
asymmetry; the current-state audit against the design; the `/v1/context` +
`POST /v1/sessions` reversibility audit; the open questions with recorded
decisions or deferral reasons.

**Out of scope (explicit) and why:**

| Not in this doc | Why |
|---|---|
| The coarse isolation boundary itself | Already **shipped** as epic #2083 (per-graph namespaces, per-graph keys, ACL users). This doc audits it, it does not redesign it. |
| Any MCP tool or public-SDK method | The MCP/SDK surface cannot grow without **Daniel's approval first** (`AGENTS.md` → MCP/SDK Surface Approval; `CONTRIBUTING.md`). A design may name a seam; it may not mint one. |
| Per-user slices inside a graph, agent scratchpad graphs | Deferred with triggers (§7). No caller exists today. |
| Changing the key scope vocabulary or the escalation policy | Owner-minted-escalation is a **recorded** decision (epic #2083 D1/D2, `docs/registry-graph-schema.md`). |

**Recorded decisions this design must not contradict** (contradiction test, applied
before any recommendation below):

1. **Graph = isolation boundary; authorization policy = access boundary**
   (`docs/epics/2026-09-01-2083-multi-graph/00-align.md:28`, `01-scope.md:50`).
2. **Key scopes are capability gates in the control plane, not an agent policy
   system** (`docs/auth-architecture.md` §6.3).
3. **Storage = scoped; enforcement = API layer. No ACL edges in the graph, no
   per-property permission predicates** (`docs/epics/2026-08-29-agent-driven-onboarding-1976/09-subgraphs-permissions-note.md`
   pins 3–4).
4. **Two auth planes, never mixed** — human session vs machine credential (ADR-010).
5. **Escalation scopes are owner-mint-only; delegation is one level deep**
   (`supabase/migrations/20260901000001_graphs_and_key_scopes.sql`).
6. **`session_recording` is per-graph with a team default, default-ON** (#1927).

## §1 The problem

The exploration (2026-09-01, carried in #2082's body and the #1976 note) states the
conceptual core. Restated as a requirement, without the list:

> Tortoise must be able to say **"the graph knows something exists without every
> actor being entitled to know what it says"** — while keeping the permission
> system out of the knowledge system.

Three separations make that possible, and every one of them is a place where a
cheaper system would instead read permissions off the graph:

1. **Memory topology ≠ authorization topology.** Edges describe knowledge; policy
   describes access. A permission engine expressed as a graph query (does a path
   exist from actor to resource?) becomes dangerous the moment there are multiple
   parents, shared resources, or cross-team edges — today's live example is a
   `(Customer X)-[:uses]->(Product Y)` edge, which must **not** make Product Y's
   memory visible to Sales.
2. **Read scope ≠ write scope.** An agent should be able to write to a private
   scratchpad and read shared knowledge. Collapsing both into one "access" verb
   loses the property that makes agent memory safe to share.
3. **Provenance ≠ ownership ≠ access.** Knowledge derived from a restricted source
   stays restricted by default; promotion to a broader scope is an explicit,
   audited operation. A statement *about* an entity is not a grant to that entity.

## §2 Current state — RE-MEASURED 2026-10-11

> **The issue body's premise is stale and is corrected here.** It assumed
> "one-graph-per-team". Epic **#2083 shipped 2026-09-04** (capstone `PASS`, 12/12
> E2E): a team has **N graphs**, keys are **per-graph**, provisioning is a key
> capability, and the tier gate is live. An audit that repeats the body's premise
> would be wrong about the layer this design builds on.

### 2.1 What exists

| Layer | As built today | Evidence |
|---|---|---|
| **Coarse isolation** | One FalkorDB namespace per graph: a custom graph is `org_{org_id}_{gid}`; the DEFAULT graph's namespace is the org namespace itself, and its shape follows the **minting path**, not the deployment lane — `org_{org_id}` from `provision_tenant` (selfhost-only), `register_user`, `agent_signup` (including the registry `kind='default'` node it writes), the Supabase-lane `create_org` + onboarding sub-org; `org_{name}` is STORED only by `sdk.org_create` (the registry create_org lane) — which writes that same `kind='default'` node kind with the other shape, so the minting path decides, not the node kind. `org_{org_id}` is minted in BOTH lanes, so neither "hosted" nor "registry" decides the shape. The `team_{org_id}` shape is the legacy pre-#3543 one, still recognised for back-compat. The default graph is **derived** (the org namespace), never materialized; custom graphs are rows in `graphs` (Supabase) / nodes in the registry graph. Default graph is **not key-bindable** and not deletable. | `tortoise/sdk.py` (`_graph_create`), `tortoise/hosted_api.py` (`_provision_graph`), `tortoise/acl_graph_users.py` (`_graph_namespace`); `docs/registry-graph-schema.md` (§Graph); `supabase/migrations/20260901000001_graphs_and_key_scopes.sql` (the `graphs` table); `supabase/migrations/20260915000001_tenancy_team_to_org.sql` (§NO DATA MIGRATION — the `team_` spelling in earlier migrations is historical and deliberately untouched, #3543) |
| **Native ACL (defense-in-depth)** | One Redis ACL user per graph: `tenant_<gid>` with key pattern `~org_{tid}_{gid}` and a command allowlist `+GRAPH.QUERY +GRAPH.RO_QUERY +PING`. Never `+@all`, `GRAPH.LIST`, `KEYS`, `SCAN`, `CONFIG`. `%R~`/`%W~` are documented-but-broken in 4.20.4, so read-only is command-level. | `tortoise/acl_graph_users.py:1-45` |
| **Credential (access)** | `api_keys.graph_id` (NULL = team-wide → default graph), flat `scopes` jsonb allowlist, `delegation_depth` (NULL = owner-minted, 0 = minted), `created_by_key_id`. Data scopes: `graphs:read`, `graphs:write`. Escalation: `graphs:create`, `graphs:delete`, `keys:manage`, `team:manage` — owner-mint-only, enforced by DB CHECK `chk_minted_key_no_escalation` + resolution. `legacy_full_access` ⇔ `delegation_depth IS NULL AND scopes = []`. | `tortoise/hosted_api.py:10316` (`_MINTABLE_SCOPES`), `:5193-5354` (resolution), `docs/auth-architecture.md` §6 |
| **Enforcement point** | `_require_scope()` is the **first statement** of each data handler (never post-filter). `_reject_graph_bound_org_surface()` refuses graph-bound keys on org-level surfaces; `_assert_graph_owned()` denies cross-graph opens. | `tortoise/hosted_api.py:5895`, `:5919`, `:5745` |
| **Single tenancy seam** | Every data path resolves through one org dict → `_data_sdk()` → one graph name (`_data_graph_name()` mirrors the SDK derivation, so the admission gate and the opener cannot drift). MCP carries the same fields via ContextVars. | `tortoise/hosted_api.py:544`, `:5844` (`_data_sdk`), `tortoise/mcp_auth.py:47-54,830-832` |
| **Provenance (write)** | Capture stamps `source_session` / `source_harness` / `ingested_at` on extracted Points; `extractedFrom` → `:Source` is **inferred from the write context** when the caller does not supply it; Sources carry `sourceKind` + `credibilityTier` (T0–T4); the event journal is the rebuild source. | `tortoise/hosted_api.py:13062`, `tortoise/sdk.py:5375-5430`, `docs/ONTOLOGY.md` §3.3/§4.6 |
| **Principal classes** | Exactly three, all resolved to the same org dict: (a) **session user** — membership role `owner`/`admin`, **no data-plane scopes** (`key_id is None` → allow); (b) **API key** — legacy-full-access / scoped / deleg=0; (c) **OAuth access token** (`oat_`, MCP-only, org-scoped, **no `graph_id`** → default graph). | `tortoise/hosted_api.py:5895-5916`, `tortoise/oauth.py:1801` (`resolve_oauth_access_token`) |

> **Audit finding (fixed):** `docs/registry-graph-schema.md` stated the custom-graph namespace as `team_<org_id>_<gid>` while the code derives `org_{org_id}_{gid}` (`_graph_create` / `_graph_namespace`), and `team_{…}` is the legacy back-compat shape. Corrected under **#8093** — the §Graph cell now states the derived shapes and marks `team_*` as the legacy prefix, pinned by `tests/test_8093_registry_namespace_doc.py`.

### 2.2 What does NOT exist (the gap the design fills)

1. **No agent principal in the auth layer.** An agent is a credential, not an
   identity. Measured: *"No credential→harness binding exists"* — `harness` on a
   capture is the **caller's declaration**, not a server-observed fact
   (`tortoise/hosted_api.py:12068-12105`).
2. **No per-user slice inside a graph.** Membership roles are control-plane only;
   the data plane allows any active member. There is no `W10`/RBAC data-plane scope.
3. **No security-bindings layer.** Nothing maps `resource_id → scope`. No Point,
   Object, or Source carries a security scope, and no resolver consults one.
4. **No node-level or traversal-level authorization.** Scope checks are **verb
   gates on the credential**; they never constrain which nodes a traversal may
   return. A reader entitled to `graphs:read` on a graph today reads the whole graph.
5. **No read ≠ write asymmetry at the resource level.** `graphs:write` implies
   `graphs:read` (`_require_scope`), which is a credential convenience, not a
   resource policy.
6. **No policy engine and no `authorize(principal, action, resource)` seam** —
   the resolver is the seam's shape, but the decision is a flat set membership.

### 2.3 Audit — design element vs what ships today

| Design element | Ships today | Additive to reach the design |
|---|---|---|
| Principal | 3 credential classes, no agent identity | A principal record for the agent (ADR-010 Tier-1 OAuth already issues one per agent; no policy consumes it yet) |
| Scope | org → graph (explicit, derived from the credential) | Add explicit scope nodes below graph (user slice, agent scratchpad) as an OPTIONAL dimension |
| Capability | 2 data verbs + 4 control verbs | Widen the flat enumeration (create/update/delete/link/unlink); keep it flat (the DB CHECK depends on top-level keys) |
| Security bindings | absent | New layer: `resource_id → scope`, container-inherited, node-level override for exceptions only |
| Provenance-derived scoping | absent (provenance is written, grants nothing) | Derive the default binding from the source/container at write time |
| Explicit promotion | absent | One audited operation; never a bulk relabel |
| Read ≠ write | verb-level only | Resource-level: read set ≠ write set for the same principal |
| Pre-filter as traversal | verb pre-filter (first statement) | Substitute the permitted-scope set into the query |

## §3 The model

### 3.1 Principal — who acts

A Principal is any actor the system authenticates: `user`, `agent`, `service`.
Humans arrive as session users; agents arrive as keys or OAuth tokens (ADR-010
keeps the two planes separate and forbids mixing them).

**Rule:** identity is always **server-resolved**. `authoredBy` is caller-supplied
(`tortoise/hosted_api.py:8324`) and `harness` is caller-declared, so neither may
carry authority. Only fields the server stamps from the credential (the credential
id, the resolved org, the resolved graph, and the session id the capture path
stamps) are attestable.

### 3.2 Scope — what the policy is written over

Scope is an **explicit org hierarchy**: `Company → Team(org) → Graph → {user
slice, agent scratchpad}`. Membership uses the canonical structural predicate
`memberOf` (`docs/ONTOLOGY.md` §3.6), which is a *knowledge-layer* fact; the
authorization mapping consumes it as an input but never derives access by
traversing it at request time.

**Scope is never inferred from semantic edges.** `(Customer X)-[:uses]->(Product
Y)` grants nothing. Published hierarchy semantics — `ownedBy` (accountable),
`managedBy` (operational), `memberOf` (membership) — are the only relations the
policy layer may map to scopes, and only through the bindings layer.

### 3.3 Capability — what may be done

Today: `graphs:read`, `graphs:write` (data) + `graphs:create`, `graphs:delete`,
`keys:manage`, `team:manage` (control). The design's data-plane menu is
`read / traverse / compute / write` with object-level verbs
`create / update / delete / link / unlink` — i.e. *create memories and
relationships, but not arbitrarily mutate existing knowledge*.

**Constraint (recorded):** the vocabulary stays **flat and machine-readable**. A
nested shape makes the escalation CHECK vacuous (Postgres `?|` matches top-level
keys only) — the reason the migration chose a flat array
(`20260901000001_graphs_and_key_scopes.sql` header).

### 3.4 Security bindings — a separate layer, by resource id

The layer, not the node, holds the answer:

```text
binding: resource_id → scope       (explicit override)
default: resource's container      (graph, or the containing Object/Project)
```

- **Container inheritance:** "owned by Sales inherits Sales scope unless
  explicitly overridden" — one rule, evaluated on the container.
- **Not** permission fields on every node (the #1976 pin R1: *provenance on every
  node; security scope on governing containers*). R1 is the recorded refinement
  adopted into that note; this doc keeps it.
- **Not** semantic traversal: an edge never grants, whatever its type.
- **Canonical ids are the join key** (`pt_…`, `obj_…`, `sub-…`, `g_…`, session
  ids), which is what makes a later external tuple store a mapping exercise rather
  than a migration (§4).

### 3.5 Read ≠ write asymmetry

| Axis | Agent scratchpad | Shared (team/graph) knowledge |
|---|---|---|
| READ | own writes + shared | entitled subset only |
| WRITE | own scope | explicitly granted scopes only |
| Something shared by default? | no | promotion is explicit |

Four orthogonal axes (`READ / TRAVERSE / COMPUTE / WRITE`) come from the #1976
note's conceptual payoff — a *logical view* over one canonical graph
(`EffectiveGraph(agent) = CanonicalGraph ∩ ReadPolicy`), not a physical partition.
This is why the fine layer rides **inside** a graph rather than minting a graph per
permission boundary (which would sever cross-subgraph edges and EP propagation —
the #1976 tension, recorded as a conscious decision).

### 3.6 Provenance-derived authorization with explicit promotion

Default = **most restrictive**, inherited from the **source** of the knowledge
(Sales conversation → Sales scope). Promotion (Sales-private → Team → Company) is
an explicit operation with an audit event; it is never a bulk relabel of "100
objects from a conversation".

**Consequence for the ontology:** the *entity* (canonical, broad) and the
*statement* (provenance-scoped, restrictive) carry **different** security
contexts — which is already the shape of the model: Subject/Object are nouns, a
Point is an asserted belief, a Source is the provenance anchor
(`docs/ONTOLOGY.md` §1). A statement about Team C stays Sales-scoped and
**references** canonical Team C without contaminating it.

### 3.7 Authorization as a pre-filter, not a post-filter

Authorize scopes once, **push them into the query**; never fetch the full result
set and filter in the app layer (that leaks the *existence* of results — the north
star's exact failure). The precedent is live: `_require_scope` runs as the first
statement of every data handler. The extension substitutes a permitted-scope set
into the traversal rather than issuing per-node authorization calls.

## §4 The policy engine question — the seam, not the vendor

**The seam** (stable regardless of engine):

```text
authorize(principal, action, resource) → allow | deny
```

Today's equivalent is `_require_scope(org, scope, surface)`. An implementation may
be native, external, or customer-supplied behind that seam.

| Option | Shape | Fit against the recorded decisions |
|---|---|---|
| **A. Native policy map (recommended default)** | Scopes + bindings in the control plane; the resolver evaluates; enforcement at the app layer | Matches "storage = scoped, enforcement = API layer" and needs no new surface. Cheapest, and the layer with zero callers does not exist yet |
| **B. External ReBAC (Zanzibar-style: SpiceDB / OpenFGA)** | Objects = our canonical ids; relations from `memberOf`/`ownedBy`; the bindings layer *is* the tuple store | Strongest if per-resource bindings ever become numerous or cross-service. Not now: it externalizes a decision that currently has no resources to bind |
| **C. ABAC / policy-as-code (OPA, Cerbos)** | Attribute rules (source kind, session, time windows) evaluated per request | Best fit for time-bounded or attribute-rich policy (e.g. retention windows). Overlaps heavily with (A) for a two-scope vocabulary |
| **D. Customer IAM / delegation** | We call the customer's authority | Only meaningful for the developer-customer model *if* they ever want to govern their end-users' slices |

How a ReBAC tuple maps onto our ids (so that picking B later is a mapping, not a
rewrite): object type = entity type, object id = our canonical id
(`graph:g_…`, `point:pt_…`, `source:session:s_…`, `team:org_…`), and the relation
is drawn from the structural predicates the ontology already fixes — `memberOf`,
`ownedBy`, `managedBy` (`docs/ONTOLOGY.md` §3.6). Both SpiceDB and OpenFGA define
exactly this object/relation tuple shape (their own documentation: SpiceDB
"Writing Relationships" — `definition document { relation editor: user | team#member }`;
OpenFGA "Concepts" — a relationship tuple is `user, relation, object`), and the
OpenFGA documentation navigation carries a dedicated *Modeling Agents as
Principals* guide. Those sources are
recorded with URLs in `docs/research/2026-09-01-2083-multi-graph/research.md` §Sources.

**OVERRIDES: the industry default for fine-grained authorization — externalize
the decision into a Zanzibar-style relationship engine (SpiceDB / OpenFGA) — is
deliberately not adopted now: we keep enforcement in the app-layer resolver
behind `authorize(principal, action, resource)` and defer the engine until a
per-resource binding actually exists.** Adopting an engine today would rewrite a
working coarse boundary to serve a layer with zero callers. This is a deferral of
an unbuilt layer, not a change to a shipped one — and the recorded decision it
rests on is #2083's scope line ("graph = isolation boundary, key = access
credential, policy = coarse only; the fine-grained policy layer is explicitly
deferred to #2082").

> **Reopening path (per the contradiction test):** the convergent standard is
> not wrong — it describes what the field does, and this doc records the evidence
> in its favour. If the field is ever to win, the route is to reopen the #2083
> scope decision with this evidence in front of the owner, not to adopt over it.

## §5 Statement-level security provenance against the ontology

`#2080`'s W5 `provenance` field is **WRITE provenance** (source session / harness /
timestamp) and is explicitly **not** a security model
(`docs/planning/2026-09-01-2080-gbrain-plan.md` §4.5). This section states how the
security layer reads (and does not read) the same fields.

| Field | Ontology meaning | Security meaning | Attestable? |
|---|---|---|---|
| `extractedFrom` → `:Source` | `pav:retrievedFrom` — this claim came from this source (§3.3) | **The inheritance anchor**: a statement inherits its source's security context | Yes (server-inferred at write time when not supplied, `sdk.py:5375`) |
| `source_session` | write provenance (capture session) | Candidate attestation of the provenance boundary | Yes on the capture path (server-stamped from the credential's session) |
| `source_harness` | write provenance (harness) | **None** — caller-declared; no credential→harness binding exists | No |
| `authoredBy` | `dc:creator` (§4.1) | **None as authority** — caller-supplied | No |
| `sourceKind` | source TYPE vocabulary (§5) | **None** — a type, not an access class | Yes as a value, not as authority |
| `credibilityTier` (T0–T4) | credibility input to belief (§4.6) | **None** — must not be reused as a security level (different axis: belief vs access) | Yes as a value |
| `aboutObject` / `aboutSubject` | reference (§3.2) | **None** — *about ≠ access* | Yes |
| `status` (`draft`/`live`/`retracted`…) | lifecycle (§4.1) | **None** — `draft` inertness is computational, not a confidentiality boundary | Yes |

**The rule this yields:** security context is inherited at write time from the
source/container and stored in the **bindings layer** — never as a label on the
node, never as a query predicate over semantic edges, and never derived from a
caller-supplied field. This keeps the ontology's provenance vocabulary intact
(it stays a belief-engineering vocabulary) while making the security layer precise.

## §6 Tenancy contract audit — `/v1/context` and `POST /v1/sessions`

**(Indicator 3 of #2082.)** Both delivery-shape endpoints resolve tenancy from the
**credential**, through the same seam, with no team/graph identifier in the
contract:

| Surface | Gate | Tenancy resolution | Fail-closed behaviour |
|---|---|---|---|
| `GET /v1/context` | `_require_scope("graphs:read")` first (`hosted_api.py:24252`) | `get_current_org_gated` → org dict → `_data_sdk` opens the **key's graph** (team-wide key → default graph) | 401/403 before any graph open |
| `POST /v1/context` | same dependency + `_require_scope` (`:24278`) | same seam | same |
| `POST /v1/sessions` | `_require_scope("graphs:write")` **before** the quota/reservation work (`:12068`) | same seam; capture lands in the key's graph | 403/409 fail-closed; a bound key whose namespace vanished gets `GRAPH_NOT_FOUND` |
| MCP equivalents | same fields via ContextVars (`mcp_auth.py:830-832`) | same graph resolution | org-wide resolution leaves the default graph |

**Reversibility verdict: the contract is reversible to per-user slices.** It is
already parameterized by a server-resolved scope object, not by a route or a
parameter: scope-by-graph today is one component of that object; a per-user slice
is a second component with the same resolution seam. Two requirements preserve
this, and both hold today:

1. **One resolution seam.** Any future scope component must ride the same
   `get_current_org_*` → `_data_sdk` path (and the MCP ContextVars). A second
   resolution path would let two answers to "which graph?" coexist — the #3365
   drift class.
2. **No scope identifiers in the contract.** The responses expose memory content and
   resource ids, never the tenancy scope itself. Adding a scope identifier would
   freeze today's topology into the wire format.

Named residual: the OAuth (`oat_`) principal is **org-scoped, not graph-scoped**
(`tortoise/oauth.py:1801` — the token row carries `org_id`, not `graph_id`), so an
OAuth agent credential reaches the **default** graph only. That is consistent
today (MCP was team-scoped before #2083 and per-graph MCP resolution rides the key
path), but it is a real asymmetry: *per-graph bound = keys; OAuth = team*. Any
future "agent has its own graph" work must resolve it deliberately.

## §7 Open questions — decisions and deferrals

| # | Question | Status | Decision / deferral, and why | Revisit trigger |
|---|---|---|---|---|
| Q1 | N graphs per team? | **DECIDED** | Shipped (`#2083`): free=1, solo=2, pro/team unlimited (`max_graphs`; owner Gate #2 2026-09-01). Default graph occupies slot 1 | — |
| Q2 | Registry shape: `Graph` entity + key scoped to graph | **DECIDED** | Shipped: `(:Graph)-[:BELONGS_TO]->(:Team)`, `api_keys.graph_id` / `graphs` table | — |
| Q3 | Provisioning auth: capability on a key vs a separate admin key | **DECIDED** | Capability (`graphs:create`), owner-mint-only; one level deep — a provisioned graph's key can never mint | — |
| Q4 | RBAC vs ReBAC vs external IAM? | **DEFER** | No per-resource binding exists, so there is nothing to evaluate an engine against; the recorded scope decision keeps policy coarse-only. Recommendation recorded at §4 option A, with the mapping to B documented so the choice stays a mapping | A first caller needing per-resource decisions (per-user slice, agent-shared knowledge, customer-governed end-user slices) |
| Q5 | Auto-classification vs provenance-scoping? | **DEFER auto-classification; provenance-scoping is the design** | Provenance-derived default (most restrictive) + explicit promotion is the design (§3.6). Auto-classification would re-derive what the source already knows, and would put a classifier in the trust path of every write | A measured case where source-derived scope is provably too coarse to be usable |
| Q6 | `session_recording`: per-graph or per-team? | **DECIDED** | Per-graph override + team default, default-ON (#2083 plan UX decision 6; the #1927 ToS-covered contract preserved) | — |
| Q7 | Per-user slices inside a graph | **DEFER** | Membership roles are control-plane only; the data plane allows any active member (`hosted_api.py:5895-5916`). Building the slice before there is a consumer is the authorization-theater risk | W10/RBAC work, or a product requirement for member-level knowledge visibility |
| Q8 | Does an agent get its own principal now? | **DEFER** | ADR-010 Tier-1 OAuth already issues per-agent identities for MCP, but no policy consumes them, and no credential→harness binding exists (`hosted_api.py:12068`). Today the **graph** is the agent's boundary and the key is its identity | An agent needing to read shared knowledge while writing privately inside one graph (§3.5) |
| Q9 | Does the fine layer need a new MCP/SDK surface? | **NOT NOW** | Any surface change requires **Daniel's approval first** (MCP/SDK Surface Approval rule). The seam (`authorize(...)`) is internal | Q4/Q7 resolving to a caller-visible policy surface |

## §8 Verification — mapping #2082's checklist

| #2082 checklist surface | Expected verification | This doc |
|---|---|---|
| design doc | Principal/Scope/Capability + bindings + provenance model written, reviewed | §3, §5, §4 — reviewed through the PR's code/content review gate |
| tenancy contract | `/v1/context` contract keeps per-user-later reversible | §6 — verdict: reversible, with two named invariants and one named residual |
| current-state audit | one-graph-per-team + registry model documented vs target | §2 — **re-measured 2026-10-11**; the body's one-graph premise is corrected (N graphs per team since #2083) |

## §9 What this doc does not claim

- It does **not** cover the **control plane**. This doc is the data-plane authorization model (§2.2 item 2 defers per-user slices). The control-plane role/permission model for the key and member surfaces — the permission vocabulary, roles as bundles, and the key **holder** field — is #2427: `docs/research/2026-10-15-2427-team-permission-model.md`.
- It does **not** claim the fine-grained layer should be built now. §7 defers it,
  and §4 records why the industry default (an external policy engine) is not
  adopted yet.
- It does **not** select a policy engine. It records a recommendation (A) and the
  fact that B is a mapping rather than a rewrite if a caller appears.
- It does **not** alter provenance semantics. The write-provenance field stays a
  belief-engineering field; security context lives in a separate layer (§5).
- Every "what exists" statement carries its measured evidence; the date of
  measurement is 2026-10-11 on branch `feat/2082-tm`.

## Sources

**Internal (measured):** `tortoise/acl_graph_users.py`; `tortoise/hosted_api.py`
(`_require_scope` :5895, `_data_sdk` :5844, `_data_graph_name` :544,
`_assert_graph_owned` :5745, `_MINTABLE_SCOPES` :10316, `GET /v1/context` :24252,
`POST /v1/context` :24278, `POST /v1/sessions` :12068); `tortoise/mcp_auth.py`;
`tortoise/oauth.py:1801`; `tortoise/sdk.py` (`create_point` :5317, provenance
inference :5375); `supabase/migrations/20260901000001_graphs_and_key_scopes.sql`;
`docs/registry-graph-schema.md`; `docs/auth-architecture.md` (§6);
`docs/ONTOLOGY.md` (§1, §2, §3.3, §3.6, §4.1, §4.6, §5);
`docs/epics/2026-09-01-2083-multi-graph/` (00-align, 01-scope, 03-plan, capstone);
`docs/research/2026-09-01-2083-multi-graph/research.md`;
`docs/planning/2026-09-01-2080-gbrain-plan.md` §4.5/D3;
`docs/epics/2026-08-29-agent-driven-onboarding-1976/09-subgraphs-permissions-note.md`;
`docs/adr/ADR-010-auth-planes-session-agent-key.md`.

**Tests pinning the audited behaviour (not re-run for a doc change; named so the
next reader can re-measure):** `tests/test_tenancy_spine.py`,
`tests/test_delivery_tenancy.py`, `tests/test_acl_graph_users.py`,
`tests/test_control_plane.py`, `tests/test_hosted_api.py`,
`tests/test_onboarding_w6_member_authz.py`.

**External (cited by name; URLs are recorded in
`docs/research/2026-09-01-2083-multi-graph/research.md` §Sources):** AuthZed
SpiceDB docs, "Writing Relationships"; OpenFGA docs, "Concepts" (its navigation
lists a "Modeling Agents as Principals" guide); FalkorDB server configuration
reference (module-load vs runtime parameters, Redis-level authentication); FalkorDB upstream issue for the
`GRAPH.LIST` name leak (`#2652` **in the FalkorDB repo** — not this repo's #2652,
which is an unrelated temporal-resolution issue).
