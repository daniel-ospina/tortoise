---
title: "#2779 — organization display name vs identifier (two-layer org naming)"
type: engineering
subjects.team: organisation-design-team
domain: platform
doc_status: approved
aboutSubjects: tortoise
aboutObjects: hosted-api
created: 2026-09-10
---

<!-- issue-scoping: v5.1 double diamond + verify -->

# #2779 — Organization display name vs identifier

**Complexity rating:** Standard (issue `tier=standard`)
**Issue:** [#2779](https://github.com/daniel-ospina/tortoise/issues/2779)
**Product decision (2026-09-10, PO):** **option 2 — separate the display name from
the identifier.** Options 1 (auto-slugify the single field) and 3 (message-only) are
rejected; see §4.
**Related:** #2701 (graph display-name precedent), #2789 / PR #2822 (one-free-org
entitlement, merged), #2547 (wizard already allows spaces), #2323 (name-first
provisioning), #1903 (graph-name parity), #2023 (registry-lane `team_{name}` parity),
#2391 (team → organization copy sweep), #2810 (cancel lifecycle, cross-lane
entitlement), #2809 (latent-red dashboard e2e), #2778 (dialog input contrast — fixed
and merged; explicitly **not** re-opened here).

---

## 1. Problem Framing

### 1.1 What the user sees

```
input:  test org for multi-organisation
error:  Invalid organization name — letters, numbers, dash, underscore only
```

The offender is the **space**. Nothing in the UI says so.

### 1.2 What is actually true on `origin/main` (traced, 2026-09-10, `a76f98fb6`)

The issue was written against an older tree. Four of its premises are now stale, and
the real defect is **narrower and different** than the issue describes:

| Issue premise | Actual state on main | Evidence |
|---|---|---|
| The space is rejected client-side | **Only one of three** client entry points rejects it | `main.jsx:4164-4170` rejects; `wizardFlow.js:139-148` allows; `main.jsx:4118-4121` allows |
| The space is rejected server-side | **No** — the server already accepts spaces on the org-create route | `hosted_api.py:9175` `^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$` (since #2547 / `efc2b2375`) |
| The field "does double duty as name *and* id" | The id is already independent in the Supabase lane (`uuid4().hex[:26]`); the *namespace* is still name-derived in the registry lane | `hosted_api.py:9214`; `sdk.py:14373` `graph_name = f"team_{name}".replace(' ', '_')` |
| Fix = make the error actionable | Necessary but **not sufficient** — the field is still labelled and behaves like an identifier at one entry point | `main.jsx:4168-4170` |

The consequence: the reported bug is a **one-line client-drift defect**
(`main.jsx:4169` did not get the #2547/#2323 update that `wizardFlow.orgNameError`
got), and the issue's acceptance criteria ("the same normalisation applies to every
org-name entry point so the two surfaces can't drift") is **structurally
unsatisfiable** while two answers to "what is a valid org name?" live in two files.

### 1.3 Why the space restriction exists at all

`team_name` historically flowed into the data-plane namespace:

- registry lane: `sdk.py:14373` — `graph_name = f"team_{name}".replace(' ', '_')`
  (`tortoise/sdk.py:14323` `team_create`), so the **display string is the storage
  key**;
- migration `0011_teams_name_unique.sql` — `uq_teams_name` was added precisely
  because two teams sharing `name` would share `team_{name}` → one FalkorDB
  namespace;
- `0006_teams.sql:41` — `graph_name text NOT NULL, -- sdk.team_create uses
  team_{name}, NOT team_{id}`.

Since #1903 (`docs/plans/2026-08-30-team-graph-name-parity.md`) the Supabase lane
mints `graph_name = team_{team_id}` (an opaque 26-hex id), so **`uq_teams_name` no
longer protects anything** — the namespace is id-derived there. The index survives
as a vestigial constraint that forbids two unrelated users from both naming their
personal org "Personal". The registry lane still has the coupling (tracked, open, as
**#2023**).

### 1.4 Confirmed problem definition

> **The organization name field is doing two jobs in one string, and the two jobs
> have different rules. The product decision is to split them: a free-text display
> name for humans, and a restrictive identifier (`team_id`, the graph namespace key)
> derived from the display name, collision-resolved, shown to the user, and editable.
> The reported error message is a symptom of that conflation, not the disease — and
> the reason the two client entry points have drifted apart is that there is no
> single shared validator to drift *from*.**

### 1.5 Assumptions (tagged)

| Assumption | Status | Evidence / falsifier |
|---|---|---|
| `teams.id` has no format CHECK, so a slug id is schema-legal | **[validated]** | `0006_teams.sql:24-27` — "text PRIMARY KEY … NO format CHECK" |
| The identifier rule (`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`) must not loosen | **[validated]** | `hosted_api.py:1192` (`_id_pattern`), `hosted_backup.py:757` `_validate_team_id` — a space in the namespace fails every downstream `_make_sdk(namespace=…)` call |
| Display names may repeat across accounts | **[unverified → product call]** | Orgs are personal, not a global namespace, so global `uq_teams_name` is wrong; but this changes 409 semantics. §5.3 decides and §5.5 lists the test updates. |
| The user wants to see/edit the derived id | **[validated]** | Product decision in the issue prompt ("must see the derived identifier and be able to edit it") |
| No third-party dependency is introduced | **[validated]** | Pure in-repo Python + React. The slugify helper needs `unicodedata` (stdlib); `tortoise/sdk.py` already imports `re`/`uuid` locally inside `team_create`. |

### 1.6 Boundary & stakeholders

- **In scope:** the display-name/identifier split, server + client validation
  unification, derivation + collision rules, backfill, every org-name display
  surface, API shape, MCP docstring, docs. Note the two **name-as-identity**
  readers that D4 breaks and that are therefore absorbed: the CLI
  `key create` reuse path (`tortoise/__main__.py:5697-5703`) and the name-keyed
  migration dedup (`tortoise/sdk.py:14932-14941`).
- **Out of scope (filed or owned elsewhere):** the graph display-name precedent
  itself (#2701, already shipped); the invite-join free-cap and the cancel-lifecycle
  hole (#2810); the dialog input contrast (#2778, merged); the latent-red dashboard
  e2e blob-switch defect (#2809); the paid-new-org checkout flow (#2789, merged).
- **Stakeholders not named in the issue:** selfhost/registry-lane users
  (`team_create` namespace coupling — #2023), the `tenant-provision` Edge Function
  (first-org path, `index.ts:327-370`), and **MCP stdio clients** whose
  `tortoise_team_create` docstring advertises "duplicate team names raise an error"
  (`mcp_server.py:2139`).

### 1.7 Adversarial findings (devil's advocate)

1. **"This is really a duplicate-validation-logic bug, not a naming-model bug."**
   Half true. Unifying validation would fix the reported symptom in one line; it
   would not fix the fact that `test org for multi-organisation` becomes a *storage
   key* in the registry lane, nor that the user cannot see or change the key. The
   split is the fix that also removes the reason the rule exists.
2. **"Deriving `team_id` from the display name is a downgrade — opaque ids are
   safer."** Real risk. A user-editable, guessable identifier is a weaker default
   than a random 26-hex id. Mitigation: the identifier is **never** an auth
   credential (auth is by session/API key + membership), and every org is
   access-controlled by membership, not by id secrecy. Documented as a decision,
   not a silent trade.
3. **"Renaming the id would orphan graphs."** It must not be possible. The
   identifier is **immutable after creation** in this design (see §5.4). Only the
   display name is renameable — exactly the #2701 shape (`set_graph_name` renames
   `graphs.name`, never `graphs.namespace`, `supabase_control.py:2652-2660`).

---

## 2. The two-layer model

Two strings, different owners, different rules.

| Layer | Field | Owner | Rule | Mutability |
|---|---|---|---|---|
| **Display name** | `teams.name` (Supabase) / `Team.name` (registry / SDK) | the user | free text: non-blank after trim, ≤ 64 chars, no control characters; **spaces and non-ASCII letters allowed**; internal whitespace runs collapsed to one space | **renameable** |
| **Identifier** | `teams.id` (Supabase PK) / `Team.id` (registry) — the key from which the graph namespace is derived | derived from the display name at creation, then owned by the system | `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` — **unchanged** | **immutable** |

The namespace is `team_{identifier}` in **both** lanes (§5.2). The display name never
appears in a namespace, a URL path, a FalkorDB graph name, or an auth claim.

### 2.1 Why `name` keeps its column name

The column is not renamed. It already *means* "display name" in the Supabase lane
(`provision_team(p_team_name=name)` while `p_graph_name=team_{team_id}`,
`hosted_api.py:9214-9234`), and it matches the shipped #2701 shape exactly:

> `graph_metadata` — "the namespace (`teams.graph_name`) is NEVER the display name —
> display is cosmetic, the namespace is the data-plane storage key"
> (`tortoise/supabase_control.py:2467-2468`)

Renaming the column to `display_name` would touch every reader
(`supabase_control.py:82` `_TEAM_BASE_SELECT`, `:871`, `:1284`, `:2457`, the export
path, invites, billing) for zero semantic gain. The additivity keeps the change
mergeable and reviewable.

### 2.2 The precedent this follows (#2701)

#2701 split a graph's **display name** from its **namespace** with an additive
`name` field on the `graphs` row and an explicit "namespace is never the display
name" contract:

- `tortoise/sdk.py:14786` `graph_set_name(team_id, graph_id, name)` — writes
  `g.name`, leaves `g.id`/`g.kind`/`g.namespace` untouched;
- `tortoise/supabase_control.py:2646` `set_graph_name(cp, team_id, graph_id, name)` —
  PATCHes `graphs.name`; the default graph's display row (`kind='default'`) is
  created on demand and its `namespace` is set from `teams.graph_name`;
- `tortoise/supabase_control.py:2443` `graph_metadata(cp, team_id)` reads the
  display name back and falls back to the literal `"default"`.

The org case is the same shape one level up: the **identifier is the "namespace"**,
the display name is the cosmetic label, and the upgrade path is additive.

---

## 3. Confirmed problem → API shape

### 3.1 `POST /v1/teams` (session auth) — `tortoise/hosted_api.py:9157`

```jsonc
// request
{ "name": "test org for multi-organisation",   // display name, free text, REQUIRED
  "id":   "test-org-for-multi-organisation" }  // identifier, OPTIONAL
```

- `name` — required, free text. Validated by the shared `validate_display_name()`
  (§5.1), **not** by a regex copied into the route.
- `id` — optional. When absent, the server derives it (§5.2) and resolves
  collisions (§5.3). When present, it is validated against `_id_pattern`
  (`hosted_api.py:1192`, reused — not duplicated), and a collision is a **409 whose
  message names the identifier**, never a bare "already exists" string.
- **Behaviour preservation when `id` is absent** is the slice-1 requirement: the
  response and stored rows are byte-identical to today (Supabase lane keeps its
  26-hex mint; registry lane keeps `team_create`'s ulid) until slice 2 turns
  derivation on.

```jsonc
// response (unchanged shape, #2789-compatible)
{ "team_id": "test-org-for-multi-organisation",
  "graph_name": "team_test-org-for-multi-organisation",
  "tier": "free",
  "name": "test org for multi-organisation" }
```

`GET /v1/teams` (`hosted_api.py:8969`) already returns both fields per row
(`team_id` at `:8997`, `team_name` at `:8998`) — no response-shape change is needed
for the switcher.

### 3.2 Every other org-creation entry point

| Entry point | Route / function | Change |
|---|---|---|
| Account-menu create | `POST /v1/teams` → `_create_team_supabase_lane` / `_create_team_registry_lane` | §3.1 |
| Onboarding wizard first org | `tenant-provision` Edge Function `index.ts:327-370` (`TEAM_NAME_RE`, slug fallback, `sha256(user_id)[:26]` id) | accept the free-text display name; keep the deterministic id until slice 2; align the namespace to `team_{team_id}` (§5.2). **NB:** the deterministic `sha256(user_id)[:26]` id means the *first* org can never get a name-derived id without breaking retry idempotency — document the deliberate exception. |
| Onboarding second org | `POST /v1/onboarding/team` → `create_onboarding_team` (`:17449`) / `_create_onboarding_team_lane` (`:17516`) | same shared validator; optional `id` |
| Paid new org | `POST /v1/billing/checkout/new-org` (`:20845`, body model `:2762`) | same shared validator; `id` rides Stripe `metadata` and is used at webhook provisioning |
| Internal provision (selfhost only) | `/internal/provision` (`:1176-1198`) | `_name_pattern` (`:1193`) becomes the shared validator; `_id_pattern` (`:1192`) is reused as-is for `team_id` |

### 3.3 The error-message contract

Every rejection must name the offending character **and** the corrected suggestion.
This is part of the acceptance criteria, not a nicety:

```
Remove the space — identifiers can't contain spaces. Try: test-org-for-multi-organisation
```

For the **display name** field the suggestion is usually unnecessary (spaces are
legal there), so the display-name error only fires for genuinely invalid input
(blank, > 64 chars, control characters) and names the problem:

```
Organization name can't be empty.
Organization name must be 64 characters or fewer (this one is 71).
Organization name can't contain control characters.
```

For the **identifier** field:

```
Identifier can't contain " ". Try: test-org-for-multi-organisation
Identifier must start with a letter or number. Try: org-acme
Identifier "registry" is reserved. Try: org-registry
Identifier "acme" is already used. Try: acme-2
```

### 3.4 The display-name validator

```
validate_display_name(raw) -> str            # raises ValueError with the message
```
1. `trim()`
2. reject if empty → `"Organization name can't be empty."`
3. collapse internal whitespace runs (`\s+` → `" "`)
4. reject any C0/C1 control character → `"…can't contain control characters."`
5. reject `len > 64` → `"…must be 64 characters or fewer (this one is N)."`
6. return the normalized string

Nothing else is restricted. Emoji, accents, CJK, and mixed case survive intact.
This is a deliberate widening of `_name_pattern` (`hosted_api.py:1193`, currently
`^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$`) — the regex is replaced by the function, and
the function is the single source of truth for display names in the API.

---

## 4. Solution Diamond — options

The issue offered three options. The PO chose option 2; the alternatives are
recorded with the conditions under which each *would* have won, per the
`issue-scoping` hypothesis rule.

### 4.1 Options compared

| # | Approach | What ships | Cannot solve | Verdict |
|---|---|---|---|---|
| **1** | **Auto-slugify the single field** — convert invalid chars to `-`, keep one field | the reported error, in one line | the field still *is* the namespace in the registry lane; the user never sees or controls the key; the display name still cannot contain spaces (contradicts the acceptance criterion "`test org for multi-organisation` must be accepted **and displayed**") | ❌ rejected |
| **2** | **Split display name from identifier** — free-text name, derived editable id | the acceptance criteria in full, both lanes, a permanent end to client drift | nothing the design requires; costs a migration + more surfaces | ✅ **chosen** |
| **3** | **Message-only** — keep rejection, name the character | the "no dead end" clause | the "accepted with a normalised id" clause; the user still cannot name their org what they want | ❌ rejected |

**Option 2 wins on outcome quality, not on diff size.** Option 1 is a smaller diff
and would have been defensible if orgs were throwaway; they are not — the identifier
is the graph namespace, and the user is stuck with it forever. Option 3 leaves the
underlying model conflation in place. This is the `Good > Easy` call.

### 4.2 Sub-decisions

| Sub-decision | Chosen | Rejected alternative | Why |
|---|---|---|---|
| **D1. Storage shape** | reuse `teams.name` as the display name; `teams.id` stays the identifier | add a new `display_name` column, keep `name` as the id | a new column means a dual-write window and a backfill for a field that already holds exactly the right value; the #2701 precedent reuses `name` for display and keeps the key separate |
| **D2. Namespace** | `team_{id}` in both lanes | keep registry `team_{name}` | #1903 already moved the Supabase lane; leaving registry different means the two lanes disagree about where the data lives (#2023). One convention, one class of bug. |
| **D3. Identity of the derived id** | derived slug, collision-suffixed, **immutable after creation** | mutable id | a mutable namespace orphans every point, key, backup and export derived from it; #2701's whole point is that a rename touches the display only |
| **D4. Display-name uniqueness** | **drop** `uq_teams_name`; uniqueness is on the identifier | keep the global unique name index | two unrelated users cannot both call their personal org "Personal" — a global constraint on a personal label is simply wrong; the index's stated purpose (namespace protection, `0011_teams_name_unique.sql`) has been obsolete since #1903 |
| **D5. Identifier visibility** | always shown in the create dialog and editable; shown next to the display name in the switcher when it differs | hide it | the product decision requires the user to see and edit it; the switcher needs it to disambiguate two orgs with the same display name |
| **D6. Slugify location** | one Python function + one mirrored JS function, pinned by a **shared test-vector table** | server-only (round-trip per keystroke) | a network round-trip for a pure string transform is wasteful; the mirror is pinned by vectors so drift is a test failure, not a runtime surprise. A server preview endpoint is listed as a deferred enhancement (§8). |

---

## 5. Design detail

### 5.1 Shared validators (new module)

New file `tortoise/org_naming.py` (pure, no I/O, no imports from `hosted_api`):

```python
DISPLAY_NAME_MAX = 64
ID_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')   # moved from hosted_api:1192
RESERVED_IDENTIFIERS = frozenset(
    {"registry", "default", "system", "admin", "api", "team"})

def validate_display_name(raw: str) -> str: ...   # §3.4
def slugify_id(display_name: str) -> str: ...     # §5.2
def resolve_id(candidate: str, taken: set[str]) -> str: ...  # §5.3
def identifier_error(candidate: str) -> str | None: ...      # §3.3 copy
```

`slugify_id` and `RESERVED_IDENTIFIERS` are introduced in **slice 1** (the registry
lane needs the charset-safe namespace derivation immediately — §7); `resolve_id`
lands in slice 2.

`hosted_api.py:1192` `_id_pattern` becomes a re-export of `ID_PATTERN`; the route,
`hosted_backup._validate_team_id` (`:757`) and `org_naming` then share one object.

### 5.2 Derivation (`slugify_id`) — the rules

Deterministic, pure, no I/O:

1. Unicode NFKD normalize; drop combining marks (`unicodedata.combining`) so
   `Café` → `Cafe`.
2. Replace **every maximal run** of characters outside `[A-Za-z0-9_-]` with a single
   `-` (a run, so `"a   b"` → `a-b`, never `a---b`).
3. Strip leading/trailing `-` and `_`.
4. Truncate to 64.
5. If the result is empty, or does not start with `[A-Za-z0-9]`, prefix `org-`.
6. If the result is a reserved key, prefix `org-` (`registry` → `org-registry`).

**Case is preserved** (`Acme Corp` → `Acme-Corp`). Rationale: the regex permits
uppercase, the existing server slugify (`hosted_api.py:4508`
`re.sub(r'[^a-zA-Z0-9_-]', '-', team_name)`) preserves case, and lowercasing an
identifier the user just typed is a surprise. (Deliberate non-decision: lowercasing
is a defensible alternative — it is called out for the implementer in §8.)

Step 6's reserved set exists because `_make_sdk(namespace=…)` has a special value:
`namespace="registry"` builds the **control-plane** SDK
(`hosted_api.py:199-215`, and the repo's convention `_make_sdk(namespace="registry")`
vs `_make_sdk(namespace=team_id)`). A team whose identifier is literally `registry`
would be the worst possible collision. Derived ids must never take it, nor `default`,
`system`, `admin`, `api`, or `team`.

The mirror lives in `website/apps/dashboard/src/orgNaming.js` exporting
`slugifyOrgId(name)` and `orgIdentifierError(id)`, driven by
`website/apps/dashboard/src/orgNaming.test.js`, which reads the **same**
`tests/fixtures/org_naming_vectors.json` the Python test reads (§7 T-1). One table,
two runtimes, drift is a red test.

### 5.3 Collision resolution

Identifier space = `teams.id` (PK, unique) / `Team.id` in the registry.

- Preferred id available → use it.
- Otherwise append `-2`, `-3`, … (bounded at `-50`), then a 6-char random suffix
  (`-a1b2c3`), then 409.
- **Atomicity:** the Supabase lane already runs the whole gate+provision chain under
  `_team_create_lock(user_id)` (`hosted_api.py:9040` def; taken at `:9188`, `:9191`) and the `id` PK is the
  DB backstop — a losing racer sees the PK violation and retries the next suffix.
  The registry lane's in-process lock is documented as not multi-process-safe
  (`hosted_api.py:9326-9330`, #1954); the retry-on-`ControlPlaneError` loop over the
  next suffix is the backstop there, and it stays documented as such.
- If the *user supplied* `id` and it is taken → **409 naming it**, no silent suffix
  (a user-typed identifier that comes back different is worse than an error).

### 5.4 Rename semantics

- **Display name: renameable.** `PATCH /v1/teams/{id}` (or the existing team-update
  seam) writes `teams.name` / `Team.name`; anything derived from the identifier is
  untouched. This mirrors `graph_set_name` (`sdk.py:14786`).
- **Identifier: immutable.** Not exposed on any PATCH surface. Any future rename is a
  migration (new namespace + copy), not a field write.
- **The rename surface itself is not required by #2779** — it is listed as a slice-4
  optional so the split is honest about which field is which; if it ships, it uses
  the same `validate_display_name`.

### 5.5 Migration / backfill

One migration, plus one conditional data step, both safe on a live fleet.

**M1 — `supabase/migrations/<ts>_teams_display_name.sql`** (the **only** migration in
this design)

```sql
-- 0011's unique index on teams.name predates #1903: it guarded team_{name}
-- as a shared namespace. Since #1903 the Supabase namespace is team_{team_id},
-- so the index protects nothing and wrongly forbids two unrelated users from
-- giving their personal org the same display name (#2779).
DROP INDEX IF EXISTS public.uq_teams_name;
-- The namespace IS still a shared resource — guard it at the real key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_graph_name ON public.teams (graph_name);
```

**Safety of the index swap — pre-flight required.** `NOT NULL` on `graph_name`
(`0006_teams.sql:41`) and `id` being the PK (`:24`) do **not** by themselves prove
the existing `graph_name` values are unique; a unique index still fails on duplicate
non-NULL values. `graph_name` was historically `team_{name}` (`0006_teams.sql:41`
comment), and `uq_teams_name` is what kept *those* values distinct — so the
pre-#1903 rows are the risk, not the post-#1903 rows. M1 therefore ships a
**pre-flight duplicate scan** in the migration:

```sql
DO $$
DECLARE dupes int;
BEGIN
  SELECT count(*) INTO dupes FROM (
    SELECT graph_name FROM public.teams GROUP BY graph_name HAVING count(*) > 1
  ) d;
  IF dupes > 0 THEN
    RAISE EXCEPTION 'uq_teams_graph_name: % duplicate graph_name group(s) — '
                    'resolve before applying (#2779)', dupes;
  END IF;
END $$;
```

If the pre-flight trips, the resolution is a data fix (rename the colliding rows'
`graph_name` to `team_{id}`) run as its own step, not a silent index skip.

**Backfill:** none required for the **name** column. `teams.name` already holds what
the user typed (spaces included, since #2547), and `teams.id` is already the opaque
identifier. Existing orgs are therefore already in the target shape and keep their
ids forever. The only conditional data step is the `graph_name` pre-flight above,
which touches only rows that predate #1903 and actually collide.

**M2 — registry/selfhost backfill (a data step, NOT a migration):** registry-lane `Team` nodes
keep `name` (display) and `id` (identifier) as-is. Their `graph_name` may already be
`team_{name_with_underscores}`; the **stored** `graph_name` remains authoritative for
reads (#1903's contract), so nothing moves. Only *newly created* registry teams get
`team_{id}` (§5.2/D2), closing #2023.

**Rollback:** M1 is reversible (`CREATE UNIQUE INDEX uq_teams_name` fails if
duplicates already exist — accept that the rollback is a no-op once a duplicate
display name has been created; document it). No data migration, so no data rollback.

### 5.6 Surfaces that show, take, or store an org name

Every one of these is a slice target. Line numbers are `a76f98fb6`.

**Server write / validate**

| Surface | Location | What it does today | Disposition |
|---|---|---|---|
| Org-create (account menu) | `hosted_api.py:9157` `create_team`, `:9242` supabase lane, `:9323` registry lane | name regex inline (`:9175`) | shared validator; optional `id` (§3.1) |
| Onboarding second org | `:17449` `create_onboarding_team` (validation `:17468-17472`), `:17516` `_create_onboarding_team_lane` | length check + charset regex | shared validator; optional `id` |
| Paid new org | `:20845` `billing_checkout_new_org`, model `:2762` | its own regex at `:20856` | shared validator; `id` in Stripe metadata |
| Internal provision (selfhost) | `:1188-1198` | `_id_pattern` + `_name_pattern` | `_name_pattern` → shared validator; `_id_pattern` referenced, not copied |
| Edge Function first org | `supabase/functions/tenant-provision/index.ts:327-347` | `TEAM_NAME_RE` + slug fallback; deterministic `sha256(user_id)[:26]` id at `:355-370` | accept free-text name; namespace → `team_{team_id}`; deterministic-id exception documented §3.2 |
| SDK `team_create` | `tortoise/sdk.py:14323` | `graph_name = f"team_{name}".replace(' ','_')` (`:14373`) and a name-keyed duplicate guard (`:14406`); its own name regex at `:14368` is `^[a-zA-Z0-9][a-zA-Z0-9_ -]*$` — spaces allowed, no `{0,63}` cap, so it is NOT `_id_pattern` | slice 1: namespace via `slugify_id(name)` (charset-safe while the id stays opaque); slice 2: `team_{team_id}`, guard re-keyed to `id`, shared validator (#2023) |
| **CLI `key create`** | `tortoise/__main__.py:5660` `_cmd_key_create` — reuses an existing team by **name** (`:5697-5700` loops `MATCH (t:Team) RETURN t.id, t.name` and matches `tname == args.name`) then `sdk.team_create(args.name)` (`:5703`) | treats the display name as identity for idempotency | slice 1: route `args.name` through the shared validator; slice 2: **reuse by derived `id`, never by name** — under D4 two orgs may share a display name and the name-match would silently return another org's team |
| Name-keyed mutation helper | `tortoise/sdk.py:14932-14941` — dedups `Team` nodes via `MATCH (t:Team {name:$name}) RETURN count(t) > 0` then skips | assumes name → identity | slice 2: key on `id` once display names may repeat (one-shot path, lower materiality than the CLI) |
| Name-keyed duplicate guard (same file) | `tortoise/sdk.py:14406` — `MATCH (t:Team {name:$name}) RETURN count(t) > 0` inside `team_create` | the create-time name uniqueness guard | slice 2: covered by "the duplicate guard moves from `name` to `id`" |
| Supabase control plane | `tortoise/supabase_control.py` `provision_team`, `team_by_name`, `team_list`, `_TEAM_BASE_SELECT:82`, reads `:871`, `:1284` | reads/writes `name` | unchanged — `name` is the display name |

**Dashboard (all of `website/apps/dashboard/src/main.jsx` unless noted)**

| Surface | Location | Disposition |
|---|---|---|
| Account-menu create submit | `:4158-4190` `handleCreateTeam`; validator `:4164-4170` | **the bug**: replace the inline regex with `orgIdentifierError`/`orgNameError`; send `{name, id?}` |
| Paid-new-org submit | `:4118-4121` `startNewOrgCheckout` | shared validator; `id` in the request |
| Wizard org step | `:4206-4245` `handleWizardCreateOrg`; validator `wizardFlow.js:139-148` `orgNameError` (spaces already OK) | switch to the shared validator module; show + allow editing the derived id |
| Create dialog markup | `:7180-7230` (org name + plan inputs) | add the identifier field + live preview |
| Wizard org markup | `:6155-6180` | add the identifier preview |
| Account blob (current org) | `:1664-1665` | show display name; append the identifier when it differs or on hover/tooltip |
| Org switcher rows | `:7084-7086` | display name primary; identifier shown when two rows share a display name |
| Pending invites | `:7105`, `:6204` | display name (identifier tooltip) |
| Team `<select>` fallback | `:8311` | display name (+ identifier when ambiguous) |
| Billing tab team context | `:8300` `<h2>Billing — {currentTeamName or 'this organization'}</h2>`, `:8306` `aria-label="Billing organization"` | display name |
| Connect-step key naming | `:4389-4395` `orgForKey` (the durable key label) | display name |
| Wizard welcome header | `:5933-5941` `shownOrgName` | display name |
| Members tab | `:8233` `<h2>Members</h2>` | **verified: the Members surface renders a title only, no org name** — listed so the implementer does not hunt for one |
| #2789 three-option dialog | `:4085-4150`, `ownedFreeOrgs[0].team_id` | **no conflict** — the dialog already targets `team_id`, which is the identifier; the upgrade action keys off the id, so a duplicate display name cannot misroute it |
| Checkout-return name matching | `:2323-2330` (`t.team_name === newOrgName`, `startsWith(prefix)`) | **replace with id matching** — the registry lane's differently-minted id is why the name hack exists (#2789 §S-B); once the id is the identity, the hack goes |

**Other surfaces**

| Surface | Location | Disposition |
|---|---|---|
| MCP `tortoise_team_create` | `tortoise/mcp_server.py:2135-2144` — docstring at `:2139` says "duplicate team names raise an error"; returns `{name, graph_name, api_key, id}` | optional `team_id` param; docstring describes display name vs identifier; `idempotentHint` re-checked |
| MCP Subject `organization` | `mcp_server.py:2218`, `:2839` | **unrelated** — the ontology `Subject/organization` node, not the tenant org. Do not touch. Called out so the implementer does not "fix" it. |
| Admin/ops | `tortoise/hosted_api.py:14360`, `:14411` — the API-key reveal/recover response dicts (`team_name` + `graph_name`); there is no admin team-list route | include the display name + identifier pair so ops can tell them apart |
| Email | onboarding offer email takes `display_name` = the **person**, not the org (`tenant-provision/index.ts:~410`) | unchanged; verify no org name is interpolated as an identifier |
| Docs | `docs/plans/2026-08-30-team-graph-name-parity.md`, `docs/plans/2026-09-02-2003-W7-onboarding-plan.md`, `docs/plans/2026-08-03-supabase-auth-signup.md` | align naming language; slice 4 |

### 5.7 What must stay true (invariants)

1. `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` on the identifier — **unchanged**.
2. Identifier uniqueness — PK + the new `uq_teams_graph_name`.
3. `POST /v1/teams` validation in **both** lanes (Supabase control plane + registry
   `Team` nodes) answers the same question the same way.
4. ≤ 64 chars on both layers (display name ≤ 64 *characters*; identifier ≤ 64
   *bytes/ASCII chars* — the identifier is ASCII by construction, so both hold).
5. `_owned_free_org_ids` / `_one_free_org_detail` (#2789) keep working: they key on
   `team_id`, the identifier, which is exactly the field that gets stronger.
6. The #2791 input-contrast tripwire stays green — the name input keeps no
   class/style/background (`modalInputContrastTripwire.test.js`).

---

## 6. Acceptance criteria → verification plan

| # | Criterion | Verification |
|---|---|---|
| 1 | `POST /v1/teams {"name":"test org for multi-organisation"}` → 200; `name` stored verbatim; `team_id` matches `_id_pattern` | endpoint test, both lanes |
| 2 | The identifier shown in the dialog **is** the identifier returned by the server | node test (vectors) + e2e |
| 3 | A second org with the same display name → **allowed**, distinct identifiers (`acme`, `acme-2`) | endpoint test (supersedes the 409 test, §5.5) |
| 4 | A user-supplied `id` that is taken → **409 naming the id**, no silent suffix | endpoint test |
| 5 | Supplying an invalid `id` → 422 naming the offending character and a suggestion; supplying a reserved `id` (`registry`, `default`, …) → 422 naming the reserved word | endpoint test |
| 6 | `Acme Corp`, `Café Ltd`, `Ünïcode 日本` all produce valid ASCII identifiers | vector table, both runtimes |
| 7 | Blank / 65-char / control-char display names → 422 with a specific message | endpoint test |
| 8 | No two teams ever share a graph namespace; `graph_name == team_{id}` for every new team, both lanes | migration + registry-lane test (#2023) |
| 9 | Existing orgs are untouched by the migration (id, name, graph_name unchanged); the `graph_name` pre-flight fails loudly on a duplicate | migration test on fixture rows incl. a pre-#1903 `team_{name}` row |
| 10 | Every org-name entry point rejects the same input with the same rule | one parametrised matrix over all entry points |
| 11 | The dashboard never shows a validation error that fails to name the problem | node test over the validator's message set |
| 12 | `GET /v1/teams` carries both `team_id` and `team_name`; the switcher renders both when they differ | e2e |
| 13 | #2789's three-option dialog, entitlement count and paid-new-org path regress nothing | existing `tests/test_one_free_org_entitlement.py` + e2e |

---

## 7. Slice plan (each independently mergeable)

The failing-sibling-feature lesson (a 6h subagent cap) is a slicing requirement, not
a preference: **every slice must leave `main` consistent and green on its own.**

**Slice 1 — display name is free text, and the namespace charset stays safe.**
`tortoise/org_naming.py` (`validate_display_name`, `ID_PATTERN`,
`identifier_error`, `slugify_id`, **and `RESERVED_IDENTIFIERS`**); route adoption at `hosted_api.py:9175`,
`:1198`, `:17468-17472`, `:20856`, `tenant-provision`; **the registry-lane namespace fix**
— `tortoise/sdk.py:14373` replaces
`graph_name = f"team_{name}".replace(' ','_')` with
`graph_name = f"team_{slugify_id(name)}"`, because the display name is now free text
and must not reach `select_graph` unslugged; `tortoise/__main__.py:5658`
`_cmd_key_create` routes `args.name` through the shared validator;
`main.jsx:4164-4170` uses the shared validator instead of its inline regex;
`wizardFlow.orgNameError` delegates to it. **No identifier derivation, no migration,
no schema change** — the identifier is still today's opaque mint
(`uuid4().hex[:26]` / ulid) and the id contract is untouched; only the namespace's
*charset* changes, and only for names that were previously rejected. `slugify_id`
lands here rather than in slice 2 **on purpose**: slicing the namespace fix away from
the free-text widening would ship an unsafe intermediate state (`{"name": "a/b"}` →
graph key `team_a/b`).
**Tests:** `tests/test_org_naming.py` (validator + message copy + `slugify_id`
vectors), `tests/test_create_team_name_free_text.py` (both lanes; spaces/unicode
accepted, blank/65/control rejected),
`tests/test_registry_namespace_charset.py` (`{"name": "a/b"}` → `team_a-b`),
`tests/test_cli_key_create.py` (validator adoption), `wizardFlow.test.js` vectors,
`tests/e2e/test_dashboard_identity.py::test_create_team_success` (already written to
expect spaces accepted — `:539-541`; currently latent-red, module-skipped behind
`RUN_DASHBOARD_E2E=1` at `:30`) **including its `:531-535` assertion rewritten**
(`bad@name!` is a legal display name under the new rule; assert the org is created
with identifier `bad-name` rather than narrowing the rule). **Exit:** the reported
symptom is gone on every surface, no two validators can drift again, and no free-text
string can reach a namespace.

**Slice 2 — derive the identifier (server model + migration).**
`resolve_id` (on top of slice 1's `slugify_id`); `POST /v1/teams` accepts optional
`id` and returns the derived one; `sdk.team_create(name, team_id=None)` with
`team_{id}` namespace and shared validation (#2023); the reserved-identifier gate
enforced on **both** the derived and the user-supplied path;
`sdk.py:14406` + `:14932-14941` re-keyed off `name`;
`tortoise/__main__.py:5697-5703` reuse re-keyed off `id`; `tenant-provision`
namespace parity; migration M1 (drop `uq_teams_name`, add `uq_teams_graph_name`, with
a    pre-flight duplicate scan); `supabase/tests/pglite/validate.mjs:177-178` and
   `tests/fake_control_plane.py:474-484` updated in the same commit. Display names may
   now repeat; identifier collisions get
`-2`/`-3`/random; a user-supplied taken id gets 409.
**Tests:** `tests/test_org_naming.py::test_slugify_vectors` (shared JSON fixture),
`test_identifier_collision_resolution`, `test_teams_name_not_unique`,
`test_registry_lane_namespace_is_id_derived` (closes #2023),
`test_migration_teams_graph_name_unique`. **Exit:** new orgs have derived ids and
identical namespace semantics in both lanes; existing orgs are byte-identical.

**Slice 3 — the dashboard surfaces.**
`orgNaming.js` mirror + shared fixtures; derived-identifier field with live preview
and edit in the account-menu dialog (`main.jsx:7180-7230`) and the wizard org step
(`:6155-6180`); switcher/header/invites/team-select show the identifier when it
disambiguates; checkout-return matching by id (`:2323-2330`).
**Tests:** `website/apps/dashboard/src/orgNaming.test.js` (same vectors),
`node --test` tripwires for the dialog field + message copy, e2e
`test_create_team_shows_derived_id`, `test_switcher_disambiguates_duplicate_names`.
**Exit:** the user sees and can edit the identifier; two same-named orgs are
tellable apart.

**Slice 4 — MCP, docs, ops, optional display-name rename.**
`tortoise_team_create` optional `team_id` + docstring (`mcp_server.py:2129-2143`);
admin/ops payloads carry both fields (`hosted_api.py:14360`, `:14411`); docs
alignment; optional `PATCH` display-name rename (§5.4) — only if the product wants
it, and it is safe to ship later because the identifier is immutable.
**Tests:** `tests/test_mcp_team_create.py` (docstring + optional arg),
`tests/test_admin_teams_payload.py`, doc lint.

---

## 8. Deliberately left open for the implementer

| Open question | Recommendation | Why it is safe to defer |
|---|---|---|
| Lowercase the derived identifier? | preserve case (the existing signup slugify does) | pure function, one test vector; changing later only affects new orgs |
| A server `POST /v1/teams/id-preview` endpoint (single source of truth, debounced as the user types) | start with the mirrored pure function + shared vectors; add the endpoint if drift is ever observed | the mirror is pinned by a shared vector table, so drift is a red test rather than a user-visible bug |
| Should the identifier be shown permanently in the switcher, or only on hover/when ambiguous? | only when ambiguous or on hover — the display name is the thing humans scan | pure presentation; no data impact |
| Should display-name collisions warn ("another of your orgs is called Acme")? | yes, a soft inline warning from the already-loaded `/v1/teams` list | client-only, no API change |
| `tenant-provision`'s deterministic `sha256(user_id)[:26]` id for the *first* org | leave deterministic (retry idempotency for hook redelivery is load-bearing — `index.ts:~355`) | first-org ids are not name-derived by design; document it |
| Display-name rename surface | defer to slice 4 | the identifier is immutable, so no data hazard |
| Whether `Team.name` should also carry a separate `display_name` property in the registry graph | no — `name` is the display name, `id` is the identifier; same shape as `teams` | consistency with the Supabase lane and with `graphs.name` |

**Product decisions to confirm before slice 2 lands** (flagged, not blocking slice 1):
1. Display names may repeat globally (D4). This changes the 409-on-duplicate-name
   behaviour and updates `tests/test_one_free_org_entitlement.py` and the dashboard
   e2e that assert it.
2. Identifiers are user-editable at creation but immutable afterwards (D3).

---

## 9. Extra issues for the register

| Finding | Action |
|---|---|
| `uq_teams_name` is vestigial (namespace moved to `team_{team_id}` in #1903) and now blocks legitimate duplicate display names | **absorbed** — it is a hard dependency of D4 and is dropped in slice 2's migration |
| Registry lane still namespaces by `team_{name}` | **absorbed** — it is the same bug in the other lane, and #2023 is already open to track it |
| `main.jsx` and `wizardFlow.js` hold two different org-name rules | **absorbed** — slice 1's shared validator is the fix |
| CLI `key create` reuses a team by display **name** (`tortoise/__main__.py:5697-5703`) — under D4 that can return another org's team | **absorbed** — it is a name-as-identity reader broken by the same decision; slice 2 re-keys it on the identifier |
| `sdk.py:14932-14941` migration dedup keys `Team` nodes by `name` | **absorbed** — same class, one-shot path; slice 2 |
| `supabase/tests/pglite/validate.mjs` pins the existence of `uq_teams_name` | **absorbed** — a schema validator that must move to `uq_teams_graph_name` when M1 runs (slice 2) |
| `tests/fake_control_plane.py:474-484` fakes the `uq_teams_name` unique-name parity | **absorbed** — the fake must mirror the index swap and the duplicate-display-name allowance, or the slice-2 tests assert against a fiction (slice 2) |
| `tests/e2e/test_dashboard_identity.py::test_create_team_success` is latent-red for the spaces path (`:539-541`) | **absorbed** — slice 1 makes it green; #2809 owns the separate blob-switch defect in the same test |
| MCP `tortoise_team_create` advertises "duplicate team names raise an error", which D4 falsifies | **absorbed** — slice 4 |
| No rename surface for an org display name | **deferred** to slice 4 (optional) |

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Dropping `uq_teams_name` lets the fleet accumulate duplicate display names | that is the intent (D4); the identifier stays unique via PK + `uq_teams_graph_name`; the switcher shows the identifier to disambiguate |
| A derived id is guessable where an opaque id was not | the identifier is not an auth credential — access is membership-controlled; documented in §1.7 |
| Client/server slugify drift | mirrored pure functions pinned by one shared vector fixture consumed by both test suites (§5.2) |
| Migration on a live fleet | M1 is index-only (DROP + CREATE UNIQUE) with a pre-flight duplicate scan that fails loudly; the pre-#1903 `team_{name}` rows are the only collision risk, and resolving them is an explicit data step (§5.5) |
| A **user-supplied** id could take a reserved key (`registry`, `default`, …) and collide with the control-plane graph | `RESERVED_IDENTIFIERS` is enforced on **both** the derived and the user-supplied path through one gate — not just inside `slugify_id` (§5.2 step 6) |
| Widening the display name to free text lets arbitrary characters reach a namespace before slice 2 lands | slice 1 routes the registry lane's `graph_name` through `slugify_id` too, so the namespace charset is safe from slice 1 onward (§7) |
| Slice 2 breaks a slice-1 client | slice 2 keeps `id` optional and the response shape unchanged; a slice-1 client sends no `id` and keeps working |
| Registry-lane collision race (no multi-process lock) | bounded retry on `ControlPlaneError` + PK/`Team.id` uniqueness; the #1954 caveat remains documented rather than papered over |
| Scope creep into #2023 / #2810 | #2023's namespace half is absorbed (1-line, same lane); #2810's entitlement hole is **not** touched |

---

## 11. Complexity ratings

| Domain | Rating | Rationale |
|---|---|---|
| UX | standard | an identifier field + live preview + edit in two dialogs, plus disambiguation copy across the switcher |
| Architecture | standard | a shared validator/derivation module, an optional API field, one additive migration; the namespace convention already exists (#1903) |
| Ontology | low | no new node/table/relationship; `teams.name` keeps its meaning, the identifier stays the PK |

---

## 12. Verification gates

**Cycle log.** Fresh-context verifiers were dispatched over the two Diamonds (one on
the problem/evidence claims, one adversarial on gaps). All cited code on `a76f98fb6`.
Cycle 1 and 2 used 2 parallel verifiers; cycles 3 and 4 used 1 focused verifier each
(re-verification of applied fixes).

### Cycle 1 — 2 verifiers

| Verifier | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| Problem / evidence | 0 | 0 | 1 | 2 | 1 |
| Adversarial gaps | 1 | 2 | 3 | 0 | 0 |

**Fixed (P0/P1):**

1. **P0 — reserved identifier reachable via a user-supplied `id`.** `"registry"`
   satisfies `_id_pattern` and is not "taken", so guarding only the *derived* path
   left `_make_sdk(namespace="registry")` (the control-plane SDK) collidable. Both
   paths now pass through `identifier_error`, whose contract includes the reserved
   set (§5.1, §5.2 step 6, §10; plan slice 2 step 2).
2. **P1 — slice 1 left the registry lane's namespace name-derived.** Widening the
   display name to free text while `sdk.py:14373` still built
   `f"team_{name}".replace(' ','_')` let `"a/b"` reach `select_graph`. Slice 1 now
   routes the registry namespace through `slugify_id` too; the `team_{id}` switch
   stays in slice 2 (§5.6, §7, §10; plan slice 1 step 6).
3. **P1 — the free-text rule contradicts an existing e2e assertion.**
   `tests/e2e/test_dashboard_identity.py:531-535` fills `bad@name!` and expects
   rejection. Under D4 that is a legitimate display name (derived id `bad-name`), so
   the assertion is rewritten in slice 1 rather than the rule being narrowed
   (`docs/plans/2026-09-10-2779-org-display-name-vs-id.md` slice 1).

**Also incorporated (P2/P3/P4):** the `uq_teams_graph_name` rationale was a
non-sequitur (NOT NULL + a PK on `id` do not imply a unique `graph_name`) → §5.5 now
carries a migration pre-flight duplicate scan and names the pre-#1903 `team_{name}`
risk; the CLI `key create` name-as-identity reuse
(`tortoise/__main__.py:5697-5703`) and the name-keyed migration dedup
(`tortoise/sdk.py:14932-14941`) were added to §1.6, §5.6, §7 and §9; the
`hosted_api.py:9176-9180` citation was corrected to `:9175`; the
`supabase/tests/pglite/validate.mjs` index pin was added to §9.

### Cycle 2 — 2 verifiers (fresh)

| Verifier | P0 | P1 | P2 | P3 | P4 | Verdict |
|---|---|---|---|---|---|---|
| Problem / evidence | 0 | 1 | 1 | 1 | 1 | not clean |
| Adversarial gaps | 0 | 1 | 2 | 2 | 2 | not clean |

Cycle 2 did **not** return a clean verdict. It found that the cycle-1 fixes had been
applied inconsistently: §7's slice-1 paragraph still said "No derivation" and omitted
the registry-lane namespace fix that §5.6/§10/§12 claimed it contained (P1 ×2, one per
verifier), plus stale citations (`sdk.py:14377` → `:14373`, `hosted_api.py:9179` →
`:9175`, `:20857` → `:20856`), a missing reserved-word message in §3.3, an unlabelled
name-keyed guard (`sdk.py:14406`), a "Two migrations" vs "one migration"
contradiction, and a duplicated step number in the plan.

**Fixed:** §7 slice 1 rewritten (adds `slugify_id`, `RESERVED_IDENTIFIERS`, the
registry-lane namespace fix and the `__main__.py` validator adoption); the plan's
slice-2 step list renumbered 1–7 with the duplicate `slugify_id` definition removed;
all four stale citations corrected; §3.3 gained
`Identifier "registry" is reserved. Try: org-registry` and acceptance criterion 5
was extended to the reserved case; §5.5's opener became "One migration, plus one
conditional data step"; `sdk.py:14406` added to §5.6.

### Cycle 3 — 1 verifier (fresh)

7 of 10 checks OK; 2 P2 remaining (both fixed): `RESERVED_IDENTIFIERS` was missing
from the scoping doc's §5.1 module block and §7 slice-1 parenthetical, and §5.5's
opener still read "Two migrations". One P3 (the plan's now-vestigial slice-2 step 2)
was removed and the list renumbered. Cycle 3 also correctly flagged that the cycle-2
row in an earlier revision of this section asserted a clean verdict that no artifact
backed — that row has been replaced with the actual (non-clean) result above.

### Cycle 4 — 1 verifier (fresh)

All 4 checks OK; **NO ISSUES FOUND**.

**Status: cycled clean, with the second-model coherence gate outstanding.** Cycle 4
returned NO ISSUES FOUND on the corrected artifacts. The P0 and all P1s from cycle 1,
and the P1s raised in cycle 2, are fixed and were re-verified. Cycles 2 and 3 were
**not** clean exits; they were fix cycles. Under AGENTS.md a full clean completion also
requires the second-model gate, which was **not** dispatched inside this task's timebox
(see below) — so this is a **clean cycle-verifier exit with an outstanding gate**, not a
full clean completion. The implementer must run the second-model coherence check at
`plan-review` time before treating the design as final.

**Second-model coherence check:** not run. The `task` tool's two initial dispatches
(the codebase explorer and the first pair of verifiers) each stalled for ~20 min
with zero tool activity (`everSawTool=false`) and returned no output, so the heavy
sub-agent path was reset to compact prompts before cycle 1 completed. A separate
second-model pass was not dispatched within this task's timebox; `§7`'s slice plan
and the plan doc's Integration Surface Map stand as the coherence artifact, and the
implementer should run the second-model gate at `writing-plans`/`plan-review` time.

**Wiring check:** §5.6 (surfaces) + §7 (slices) + the plan's Integration Surface Map.
The CLI writer, the migration pre-flight, the pglite schema validator, the name-keyed
`team_create` guard and `tests/fake_control_plane.py:474-484` (which fakes the
`uq_teams_name` unique-name parity and must change with slice 2's index swap) are the
touch points the verification cycles added. No uncovered touch point remains **after**
those five are included; the wiring claim is scoped to them.
