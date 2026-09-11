---
title: "#2779 — implementation plan (org display name vs identifier)"
type: engineering
subjects.team: organisation-design-team
domain: platform
doc_status: approved
aboutSubjects: tortoise
aboutObjects: hosted-api
created: 2026-09-10
---

# #2779 — Implementation Plan

**Issue:** [#2779](https://github.com/daniel-ospina/tortoise/issues/2779) · **Scoping:** `docs/scoping/2026-09-10-2779-org-display-name-vs-id.md`
**Tier:** Standard · **Chosen design:** two-layer naming — free-text display name + restrictive derived identifier (product decision 2026-09-10, option 2)
**Research path:** in-repo trace only. No third-party dependency, no new external API surface. The only external contract touched (Stripe Checkout `metadata`, #2789) already exists and is only extended with an `org_id` key.

---

## Design summary

1. **Two layers, two rules.**
   - **Display name** = `teams.name` (Supabase) / `Team.name` (registry). Free text:
     non-blank after trim, ≤ 64 chars, no control characters; spaces and non-ASCII
     survive; internal whitespace runs collapse to one space. **Renameable.**
   - **Identifier** = `teams.id` (PK) / `Team.id`; the key the graph namespace is
     derived from. `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` — **unchanged**. **Immutable.**
   - The namespace is `team_{identifier}` in **both** lanes (the Supabase lane
     already does this since #1903; the registry lane is fixed here, closing #2023).
2. **One validator, one deriver.** New pure module `tortoise/org_naming.py`:
   `validate_display_name`, `slugify_id`, `resolve_id`, `identifier_error`,
   `ID_PATTERN`. `hosted_api.py:1192`'s `_id_pattern` becomes a re-export. The
   dashboard mirrors `slugify_id`/`identifier_error` in
   `website/apps/dashboard/src/orgNaming.js`, pinned by **one shared vector fixture**
   (`tests/fixtures/org_naming_vectors.json`) consumed by both test suites.
3. **API.** `POST /v1/teams` gains an **optional** `id`. Absent → derived +
   collision-resolved server-side. Present → validated with `ID_PATTERN`; taken →
   **409 naming the identifier** (no silent suffix). Response shape unchanged
   (`{team_id, graph_name, tier, name}`), so a slice-1 client keeps working.
4. **Migration.** One index-only migration: drop `uq_teams_name` (vestigial since
   #1903 — the namespace is `team_{team_id}`, `0011_teams_name_unique.sql`), add
   `uq_teams_graph_name` (guard the real shared resource). **No data backfill** —
   existing rows are already in the target shape.
5. **Slices.** Four, each independently mergeable and green on its own. Slice 1 is
   the reported-bug fix and touches no schema and no API contract.

---

## Task ordering (slices)

| Slice | Title | Depends on | Independently mergeable because |
|---|---|---|---|
| **1** | Display name is free text + namespace charset safety | — | no schema change, no API contract change; the identifier is still today's opaque mint. Includes the registry-lane namespace charset fix so free text cannot reach `select_graph` unslugged. |
| **2** | Derive the identifier (server + migration) | 1 | `id` is optional; absent-`id` behaviour is byte-identical to today until this lands. |
| **3** | Dashboard surfaces | **2** (derived id) + 1 (validator module) | additive UI over a server that already returns the identifier. **Not** pre-slice-2 mergeable: the id field and the disambiguation test need a server that returns a derived `team_id`. Its two e2e tests are fenced accordingly. |
| **4** | MCP, docs, ops, optional rename | 1 | additive docstring/param/payload fields. |

> **Why this ordering.** A sibling feature recently died to a subagent time cap; the
> mitigation is that **any one slice can be abandoned mid-epic and `main` stays
> consistent**. Slice 1 alone fixes the user-visible bug and is safe in both lanes.
> Slice 2 alone does not change any client-visible behaviour for existing clients.
> Slice 3 needs slice 2's derived id (declared above, not hidden). Slice 4 is
> documentation. Dependencies are **declared**, so no slice is claimed independent
> when it is not.

---

## Slice 1 — Display name is free text (fixes the reported bug)

**Intent:** kill the reported symptom at its root — two different org-name rules in
two client files — without changing the storage model.

### Steps

1. **New module `tortoise/org_naming.py`** (pure; stdlib only — `re`, `unicodedata`):

   ```python
   DISPLAY_NAME_MAX = 64
   ID_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
   RESERVED_IDENTIFIERS = frozenset({"registry", "default", "system", "admin", "api", "team"})

   def validate_display_name(raw: str) -> str:
       """Trim, collapse whitespace runs, reject blank/control/>64.
       Returns the normalized display name; raises ValueError with a message
       that names the problem (never a generic 'invalid name')."""

   def slugify_id(display_name: str) -> str:
       """NFKD -> drop combining marks -> runs of [^A-Za-z0-9_-] become '-'
       -> strip leading/trailing '-'/'_' -> truncate to 64 -> prefix 'org-'
       when empty/leading-non-alnum/reserved. Case preserved.
       Introduced in SLICE 1 because the registry lane needs a charset-safe
       namespace derivation immediately (see step 6)."""

   def identifier_error(candidate: str) -> str | None:
       """None when `candidate` matches ID_PATTERN and is not reserved; else a
       message naming the offending character (or the reserved word) plus a
       suggested replacement."""
   ```

   `identifier_error` walks the string and reports the **first** offending
   character (`Identifier can't contain " ". Try: test-org-for-multi-organisation`),
   so the message can never be generic.

2. **`tortoise/hosted_api.py`** — adopt the shared validator at every org-creation
   entry point:
   - `:1176-1198` `/internal/provision`: `_name_pattern` (`:1193`) is deleted;
     `validate_display_name(team_name)` replaces it. `_id_pattern` (`:1192`) becomes
     `ID_PATTERN` re-exported from `org_naming` (no behaviour change).
   - `:9157` `create_team`: the inline `^[a-zA-Z0-9][a-zA-Z0-9_ -]{0,63}$`
     (`:9175`) is replaced by `validate_display_name`.
   - `:17449` `create_onboarding_team`: the length check + charset regex at
     `:17468-17472` are replaced by `validate_display_name` (the lane itself is
     `_create_onboarding_team_lane` at `:17516`).
   - `:20845` `billing_checkout_new_org`: the inline regex (`:20856`) is replaced by
     `validate_display_name`.
   - All four now raise **422** with the validator's specific message (the current
     routes mix 400/422 — unify on 422 for display-name problems, keeping the
     existing code where a test pins it).

3. **`website/apps/dashboard/src/wizardFlow.js:139-148`** — `orgNameError` delegates
   to a new `website/apps/dashboard/src/orgNaming.js` (`displayNameError(name)`)
   instead of holding its own regex. `orgNameError` stays exported (it is imported at
   `main.jsx:19`) and keeps its exact message strings so the wizard copy does not
   move.

4. **`website/apps/dashboard/src/main.jsx:4164-4170`** — the account-menu
   `handleCreateTeam` validator drops the inline strict regex and calls
   `displayNameError(name)`; the error copy becomes the specific message. **This is
   the reported bug.** `startNewOrgCheckout` (`:4118-4121`) also switches to the
   shared helper.

5. **`supabase/functions/tenant-provision/index.ts:327-347`** — `TEAM_NAME_RE` is
   replaced by the display-name rule (accept free text, keep the deterministic
   `sha256(user_id)[:26]` id and the slug fallback for older callers). This file is
   Deno/TS, so it cannot import the Python module; it carries a copied predicate with
   a comment pointing at `tortoise/org_naming.py` and is covered by the same vector
   fixture via a Deno test.

6. **`tortoise/sdk.py:14323` `team_create`** — **namespace charset safety (this is
   why `slugify_id` lands in slice 1).** The display name is now free text, but
   `:14373` still built `graph_name = f"team_{name}".replace(' ','_')` and passed it
   to `select_graph` — so `{"name": "a/b"}` would have minted a graph key
   `team_a/b`. Slice 1 replaces that line with
   `graph_name = f"team_{slugify_id(name)}"` (the id stays today's ulid; only the
   namespace's charset changes, and only for names that were previously rejected).
   Existing stored `graph_name` values are unaffected — reads use the stored value
   (#1903). **Do not defer this to slice 2**: slicing the namespace fix away from the
   free-text widening would ship an unsafe intermediate state.

7. **`tortoise/__main__.py:5660` `_cmd_key_create`** — route `args.name` through
   `validate_display_name`; keep the name-based reuse in slice 1 (it is still safe
   while display names are unique) so the slice stays a validation-only change.

### Tests (slice 1)

| Test | What it pins |
|---|---|
| `tests/test_org_naming.py::test_validate_display_name_spaces_unicode` | `"test org for multi-organisation"` survives verbatim; `"Café  Ltd"` → `"Café Ltd"` |
| `tests/test_org_naming.py::test_validate_display_name_rejections` | blank, 65-char, control char — each message names the problem |
| `tests/test_org_naming.py::test_identifier_error_names_the_character` | space / leading dash / `@` each produce a message containing the character and a suggestion |
| `tests/test_create_team_name_free_text.py::test_post_teams_accepts_spaces_supabase` | `POST /v1/teams {"name": "test org for multi-organisation"}` → 200, `name` stored verbatim, `team_id` opaque |
| `tests/test_create_team_name_free_text.py::test_post_teams_accepts_spaces_registry` | the registry-lane twin |
| `tests/test_create_team_name_free_text.py::test_all_entry_points_share_the_rule` | parametrised matrix over `/v1/teams`, `/v1/onboarding/team`, `/v1/billing/checkout/new-org`, `/internal/provision` |
| `website/apps/dashboard/src/orgNaming.test.js` | `displayNameError` vectors mirror the Python set |
| `website/apps/dashboard/src/wizardFlow.test.js:73-78` | unchanged assertions stay green (`orgNameError('has space') === null`) |
| `tests/e2e/test_dashboard_identity.py::test_create_team_success:539-541` | fills `good name with spaces` and expects success — **currently latent-red**, module-skipped behind `RUN_DASHBOARD_E2E=1` (`:30`); slice 1 makes it green |
| `tests/e2e/test_dashboard_identity.py::test_create_team_success:531-535` (**rewrite**) | fills `bad@name!` and asserts rejection. Under the free-text rule `@`/`!` are legal in a **display** name (the derived id is `bad-name`), so this assertion is **rewritten**: assert the org is created and its identifier is `bad-name`. Narrowing the rule to keep the old assertion would contradict the acceptance criteria, so the test moves — stated here deliberately. |
| `supabase/tests/pglite/validate.mjs` (name-rule spot check, if it asserts the charset) | updated with the slice-1 predicate |

### Verification commands (slice 1)

```bash
uv run pytest tests/test_org_naming.py tests/test_create_team_name_free_text.py -v
node --test website/apps/dashboard/src/orgNaming.test.js website/apps/dashboard/src/wizardFlow.test.js
RUN_DASHBOARD_E2E=1 uv run pytest tests/e2e/test_dashboard_identity.py::test_create_team_success -v
```

### Rollback (slice 1)

Revert the commit. No schema, no data, no API contract change — a pure code revert.

---

## Slice 2 — Derive the identifier (server model + migration)

**Intent:** the identifier becomes the derived, collision-resolved, user-editable
slug, and the two lanes finally agree on `team_{identifier}`.

### Steps

1. **`tortoise/org_naming.py`** — `slugify_id` and `RESERVED_IDENTIFIERS` are already
   introduced in **slice 1** (step 1) because the registry lane needs the
   charset-safe namespace derivation immediately; slice 2 adds only `resolve_id`:

   ```python
   def resolve_id(candidate: str, taken: set[str]) -> str:
       """Append -2, -3, … up to -50, then a 6-char random suffix; raise when
       exhausted. Callers that received an explicit user-supplied id do NOT
       call this — they 409 instead (a typed id that comes back different is
       worse than an error)."""
   ```

   `RESERVED_IDENTIFIERS` must contain `registry`: `_make_sdk(namespace="registry")`
   (`hosted_api.py:199-215`) is the control-plane SDK, so a team whose identifier is
   literally `registry` is the worst conceivable collision.

2. **`tortoise/hosted_api.py`**
   - `:9157` `create_team`: read `body.get("id")`. **Both paths go through ONE gate:**
     a supplied `id` must pass `identifier_error(id)`, which enforces `ID_PATTERN`
     **and** `RESERVED_IDENTIFIERS` — not just the regex. `"registry"` satisfies the
     regex, would not be "taken", and `_make_sdk(namespace="registry")`
     (`hosted_api.py:199-215`) is the control-plane SDK (`sdk.py:1971` resolves any
     namespace as `f"team_{ns}"`), so a team with `id="registry"` would collide with
     the control plane. Guarding only the derived path leaves that reachable. Then
     uniqueness on `id`; taken → **409 naming it**. Absent → `slugify_id(name)` +
     `resolve_id` against the taken set, inside the existing
     `_team_create_lock(user_id)` (def `:9040`; taken at `:9188`, `:9191`).
   - `_create_team_supabase_lane` (`:9242`): the `uuid4().hex[:26]` mint at `:9284`
     is replaced by the resolved id; `provision_team(p_team_id=…,
     p_graph_name=f"team_{id}")` shape unchanged.
   - `_create_team_registry_lane` (`:9323`): pass the identifier through to
     `sdk.team_create`.
   - `:17449` / `:20845`: same optional-`id` handling. `billing_checkout_new_org`
     adds `org_id` to the Stripe session `metadata` alongside the existing
     `org_name`, and `_provision_new_org_from_checkout` reads it.
   - `:8997-8998` `GET /v1/teams`: no shape change (both fields already returned).

3. **`tortoise/sdk.py:14323` `team_create`** — add keyword
   `team_id: str | None = None`:
   - when given, use it (validate with `identifier_error`, which enforces the
     reserved set); otherwise keep the `ulid()` mint;
   - `graph_name = f"team_{team_id or tid}"` (replaces
     `:14373 f"team_{name}".replace(' ','_')`) — closes **#2023**;
   - validate `name` with `validate_display_name`;
   - the duplicate guard moves from `name` to `id` (`Team {name}` uniqueness is
     removed with `uq_teams_name`).
   - `tortoise/sdk.py:14932-14941` — the name-keyed migration dedup
     (`MATCH (t:Team {name:$name}) RETURN count(t) > 0` → skip) must key on `id`;
     with duplicate display names legal it would otherwise skip a legitimate team.

4. **`tortoise/__main__.py:5697-5703` `_cmd_key_create`** — the CLI reuses an
   existing team by matching `tname == args.name` over
   `MATCH (t:Team) RETURN t.id, t.name`. Under D4 that can silently return a
   **different org's** team when two names collide. Re-key the reuse on the derived
   `id` (compute it with `slugify_id`; accept a `--team-id` option).

5. **`supabase/functions/tenant-provision/index.ts`** — namespace becomes
   `team_{team_id}` (slice 1 made it `team_{slugify_id(name)}`); the deterministic
   `sha256(user_id)[:26]` id for the **first** org is retained unchanged (retry
   idempotency for hook redelivery is load-bearing, `index.ts:~355-370`) and this
   exception is documented in the file.

6. **`supabase/tests/pglite/validate.mjs:177-178` and `tests/fake_control_plane.py:474-484`** —
   the schema validator pins `teams_name_unique`, and the fake control plane reproduces
   the `uq_teams_name` unique-name parity. Both must change in the same commit as M1
   (drop the name-uniqueness assertion / fake, assert `uq_teams_graph_name` instead),
   or CI and the slice-2 tests assert against a fiction.

7. **Migration `supabase/migrations/<ts>_teams_display_name.sql`** — the only
   migration in this design.
   ```sql
   -- 0011's unique index on teams.name guarded team_{name} as a shared
   -- namespace. Since #1903 (/docs/plans/2026-08-30-team-graph-name-parity.md)
   -- the Supabase namespace is team_{team_id}, so the index protects nothing and
   -- wrongly forbids two unrelated users from giving their personal org the same
   -- display name (#2779). Uniqueness moves to the namespace itself.
   DROP INDEX IF EXISTS public.uq_teams_name;
   CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_graph_name ON public.teams (graph_name);
   ```

   Safe **only after the pre-flight**: `NOT NULL` (`0006_teams.sql:41`) and `id` as
   the PK (`:24`) do not by themselves prove the existing `graph_name` values are
   unique — `graph_name` was historically `team_{name}`, and `uq_teams_name` is what
   kept *those* values distinct. M1 therefore opens with a pre-flight duplicate scan:

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

   **No backfill for `name`:** existing rows already hold the display name in `name`,
   the identifier in `id`, and (post-#1903) `team_{id}` in `graph_name`. Only a
   pre-#1903 row that actually collides needs a data fix, and the pre-flight names it
   instead of failing opaquely.

### Tests (slice 2)

| Test | What it pins |
|---|---|
| `tests/test_org_naming.py::test_slugify_vectors` | reads `tests/fixtures/org_naming_vectors.json`; `test org for multi-organisation` → `test-org-for-multi-organisation`; `Acme  Corp` → `Acme-Corp`; `Café Ltd` → `Cafe-Ltd`; `!!!` → `org-`; `registry` → `org-registry`; 80-char → 64 |
| `tests/test_org_naming.py::test_resolve_id_collisions` | `acme` taken → `acme-2` → `acme-3`; >50 → random suffix; exhaustion raises |
| `tests/test_resolve_org_id_api.py::test_derived_id_returned` | `POST /v1/teams {"name": "Acme"}` → `team_id == "Acme"` |
| `tests/test_resolve_org_id_api.py::test_duplicate_display_name_allowed` | two orgs named `Acme` → ids `Acme`, `Acme-2` |
| `tests/test_resolve_org_id_api.py::test_supplied_id_taken_409` | `{"name":"X","id":"acme"}` with `acme` taken → 409 whose message contains `acme` |
| `tests/test_resolve_org_id_api.py::test_supplied_id_invalid_422` | `{"id":"a b"}` → 422 naming the space |
| `tests/test_resolve_org_id_api.py::test_supplied_id_reserved_422` | `{"id":"registry"}`, `{"id":"default"}` → 422 naming the reserved word — **the P0 path** |
| `tests/test_writer_inventory.py::test_duplicate_name_409:657` (**rewrite**) | becomes "two orgs may share a display name; identifiers differ (`acme`, `acme-2`)" |
| `tests/test_writer_inventory.py::test_duplicate_name_race_maps_rpc_409:663-692` (**rewrite**) | currently asserts the literal `uq_teams_name` PostgREST error; re-target it at a duplicate `graph_name` (PK/`uq_teams_graph_name`) violation |
| `supabase/tests/pglite/validate.mjs` | asserts `uq_teams_name` is gone and `uq_teams_graph_name` exists |
| `tests/test_org_naming.py::test_resolve_id_never_reserved` | `slugify_id("registry") == "org-registry"` |
| `tests/test_registry_lane_namespace_parity.py` | `sdk.team_create("Acme Ltd", team_id="acme")` → `graph_name == "team_acme"` (closes #2023) |
| `tests/test_teams_migration_display_name.py` | fixture rows survive `DROP INDEX uq_teams_name`; `uq_teams_graph_name` rejects a duplicate `graph_name` |
| `tests/test_one_free_org_entitlement.py` (updated) | the dup-name 409 assertions become the duplicate-display-name-allowed + collision assertions |
| `tests/test_writer_inventory.py` (updated) | `p_graph_name == f"team_{team_id}"` assertions still hold with a derived id |

### Verification commands (slice 2)

```bash
uv run pytest tests/test_org_naming.py tests/test_resolve_org_id_api.py \
  tests/test_registry_lane_namespace_parity.py tests/test_teams_migration_display_name.py \
  tests/test_one_free_org_entitlement.py tests/test_writer_inventory.py -v
uv run pytest tests/ -v   # full suite, docker lane
```

### Rollback (slice 2)

Code revert + re-apply `CREATE UNIQUE INDEX uq_teams_name`. The index creation
fails if duplicate display names were created during the window — rollback is then a
no-op and duplicates must be renamed first. Document this in the PR body.

---

## Slice 3 — Dashboard surfaces

**Intent:** the user sees the derived identifier, can edit it, and can tell two
same-named orgs apart.

### Steps

1. **`website/apps/dashboard/src/orgNaming.js`** — add `slugifyOrgId(name)` and
   `orgIdentifierError(id)`, mirroring `org_naming.py`. Header comment states the
   mirror is pinned by `tests/fixtures/org_naming_vectors.json` and that any change
   must update both runtimes.
2. **Account-menu create dialog** (`main.jsx:7180-7230`) — below the name field, an
   `Identifier` input (editable, `aria-describedby` linking to a hint) pre-filled by
   `slugifyOrgId(name)` and updating until the user edits it (then it stops
   auto-syncing — a "reset to suggestion" affordance restores it). Submit sends
   `{name, id}`. The server's returned `team_id` is what the success path uses.
3. **Wizard org step** (`main.jsx:6155-6180`) — the same field, pre-filled, editable;
   `provisionInApp` (`:3087`) passes `team_id` through `team_name`-adjacent plumbing
   to `tenant-provision` (which keeps its deterministic id for the first org — the
   field is therefore shown read-only there with a note, see §"Open").
4. **Switcher / header / invites / team select** — display name primary; identifier
   rendered in the dim style when (a) two listed orgs share a display name, or
   (b) the row is hovered/focused. Locations: `main.jsx:1664-1665` (account blob),
   `:7084-7086` (switcher rows), `:7105` + `:6204` (pending invites), `:8311`
   (team `<select>`), `:8300` + `:8306` (Billing heading + its `aria-label`), and
   `:4389-4395` (connect-step key naming) / `:5933-5941` (wizard welcome header).
5. **Checkout-return matching** (`main.jsx:2323-2330`) — replace the
   `t.team_name === newOrgName` / `team_name.startsWith(newOrgNamePrefix)` heuristic
   with matching on the returned `team_id` (already carried as `?new_org=<id>`), and
   drop `newOrgName` from the success URL. The name heuristic existed only because
   the registry lane minted a different id (#2789 §S-B); with the identifier being
   the identity, it is dead code.
6. **`#2789` interaction — no change required, verified:** the three-option dialog
   keys on `ownedFreeOrgs[0].team_id` and `e.teamId`
   (`main.jsx:4085-4150`, `hosted_api.py:9142` `_one_free_org_detail`), i.e. the
   identifier. Duplicate display names cannot misroute "Upgrade current
   organization". The only edit is display copy (show the display name in the
   dialog body, keep the identifier for the action).

### Tests (slice 3)

> **Slice-3 dependency:** the two derived-id e2e tests below need slice 2's server
> (they read a derived `team_id`). They are **fenced to this slice** and this slice
> is declared dependent on slice 2 in the slice table — it is not claimed to be
> mergeable before slice 2. The `node --test` tripwires below are slice-2-independent.

| Test | What it pins | Needs slice 2 |
|---|---|---|
| `website/apps/dashboard/src/orgNaming.test.js` | reads the **same** `org_naming_vectors.json`; `slugifyOrgId` output identical to Python | no |
| `website/apps/dashboard/src/orgNaming.test.js::test_identifier_message_names_character` | `orgIdentifierError('a b')` contains `" "` and a suggestion | no |
| `website/apps/dashboard/src/orgCreateDialog.test.js` (node tripwire) | the dialog renders an editable identifier input; the name input keeps no `class`/`style`/`background` (**#2791 tripwire stays green**: `modalInputContrastTripwire.test.js`) | no |
| `tests/e2e/test_dashboard_identity.py::test_create_team_shows_derived_id` | type `Acme Corp` → the identifier field shows `Acme-Corp`; edit it; create; the switcher shows the edited id | **yes** |
| `tests/e2e/test_dashboard_identity.py::test_switcher_disambiguates_duplicate_names` | two orgs named `Acme` render both identifiers | **yes** |
| `tests/e2e/test_dashboard_identity.py::test_create_team_success` | still green after the `:2323-2330` rewrite | yes (already green from slice 1) |

### Verification commands (slice 3)

```bash
node --test website/apps/dashboard/src/orgNaming.test.js website/apps/dashboard/src/orgCreateDialog.test.js
cd website/apps/dashboard && npm run build
# e2e lane requires slices 1+2 merged; run only these on this slice's branch:
RUN_DASHBOARD_E2E=1 uv run pytest tests/e2e/test_dashboard_identity.py -k "derived_id or switcher_disambiguates or create_team_success" -v
```

### Rollback (slice 3)

Revert the commit. The dialog falls back to sending only `name` — which the slice-2
server accepts and derives.

---

## Slice 4 — MCP, docs, ops (and the optional rename)

**Intent:** every remaining surface describes the two layers accurately, and ops can
tell them apart.

### Steps

1. **`tortoise/mcp_server.py:2135-2144` `tortoise_team_create`** — add optional
   `team_id: str | None = None`; the docstring's "duplicate team names raise an
   error" becomes "the display name is free text; the identifier is derived or
   supplied and must be unique"; re-check `idempotentHint` (it stays `false` for
   name collisions, but note the derived-id path).
2. **`hosted_api.py:14360`, `:14411`** (admin/ops payloads carrying `graph_name`) —
   include `name` and `id` so ops see the display/identifier pair.
3. **Docs** — `docs/plans/2026-08-30-team-graph-name-parity.md` (note the registry
   lane is now closed), `docs/plans/2026-09-02-2003-W7-onboarding-plan.md`, and any
   doc stating the org-name rule, updated to the two-layer model. Link this plan.
4. **Optional: display-name rename** — `PATCH /v1/teams/{team_id}` (or the existing
   team-update seam) accepting `{name}` only, validated with
   `validate_display_name`, writing `teams.name` / `Team.name`; identifier
   untouched. Mirrors `graph_set_name` (`sdk.py:14786`). Safe to defer — the
   identifier is immutable so nothing derived moves.

### Tests (slice 4)

| Test | What it pins |
|---|---|
| `tests/test_mcp_team_create.py` | optional `team_id` honoured; the docstring names both layers; stdio-only guard unchanged (`mcp_server.py:2140-2143`) |
| `tests/test_admin_teams_payload.py` | admin payload carries `id` + `name` |
| doc lint / `pytest tests/test_docs_links.py` (or the repo's doc check) | the updated docs link correctly |
| (optional) `tests/test_team_rename.py` | `PATCH` changes `name` and never `id`/`graph_name` |

---

## Integration Surface Map

| Surface | Layer | Test assignment | Slice |
|---|---|---|---|
| `POST /v1/teams` (both lanes) | integration (FakeControlPlane + registry/docker) | `test_create_team_name_free_text.py`, `test_resolve_org_id_api.py` | 1, 2 |
| `POST /v1/onboarding/team` | integration | same files (matrix) | 1, 2 |
| `POST /v1/billing/checkout/new-org` + webhook provisioning | integration (FakeControlPlane + fake Stripe event) | `test_one_free_org_entitlement.py` (+ `org_id` metadata assertion) | 1, 2 |
| `/internal/provision` (selfhost) | integration | matrix | 1 |
| `tenant-provision` Edge Function | integration (Deno test + vector fixture) | `tests/test_provisioning_edge_function.py` | 1, 2 |
| `sdk.team_create` namespace | unit + integration (embedded registry) | `test_registry_lane_namespace_parity.py` | 2 |
| CLI `key create` writer (`tortoise/__main__.py`) | unit | `test_cli_key_create.py` (validator + id-keyed reuse) | 1, 2 |
| `supabase/tests/pglite/validate.mjs` schema check | db | the pglite validator run itself | 2 |
| `teams` migration (index swap) | db | `test_teams_migration_display_name.py` | 2 |
| Dashboard create dialog + wizard | ui | `orgNaming.test.js`, `orgCreateDialog.test.js`, e2e | 3 |
| Switcher / header / invites / billing context | ui | e2e | 3 |
| `#2789` three-option dialog + entitlement | integration | `test_one_free_org_entitlement.py` (must stay green) | — |
| MCP `tortoise_team_create` | unit | `test_mcp_team_create.py` | 4 |
| Admin/ops payloads | integration | `test_admin_teams_payload.py` | 4 |

---

## Verification Plan (test-routing)

- **Complexity:** standard → unit + integration + one e2e lane per slice; no load
  testing (no new hot path).
- **Docker-lane default** (epic #1647 P4): every Python run uses
  `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
- **Lane coverage:** every API test runs against **both** the Supabase path
  (`FakeControlPlane`) and the registry path — the acceptance criterion "the two
  surfaces can't drift" is the whole point.
- **Full suite gate before each merge:** `uv run pytest tests/ -v`.
- **Dashboard gate:** `node --test` + `npm run build` (+ the existing
  `modalInputContrastTripwire.test.js`).

---

## Open for the implementer

| Item | Recommendation |
|---|---|
| Lowercase the derived identifier | preserve case (matches the existing signup slugify, `hosted_api.py:4508`) — one vector, easily changed for *new* orgs only |
| Server `id-preview` endpoint | start with the mirrored pure function + shared vectors; add the endpoint only if drift is observed |
| The wizard's first-org identifier field | render it **read-only** with a note — `tenant-provision` keeps the deterministic `sha256(user_id)[:26]` id for retry idempotency. Showing an editable field there would be a lie. |
| Display-name collisions warning | soft inline warning from the already-loaded `/v1/teams` list; client-only |
| Round-trip the `org_id` through Stripe metadata | do it in slice 2 — the webhook currently reads `org_name` (`hosted_api.py:~20769`); a rename between checkout and payment would otherwise provision the wrong name |
| `Team` registry node needs a separate `display_name` property? | no — `name` is the display name, `id` is the identifier; same shape as `teams` and as `graphs.name`/`graphs.namespace` (#2701) |

**Product decisions to confirm before slice 2 ships** (do not block slice 1):
1. Display names may repeat globally (dropping `uq_teams_name`). This intentionally
   changes the duplicate-name **409** into a collision-resolved identifier, and
   rewrites the tests that pin 409.
2. Identifiers are user-editable at creation and **immutable** afterwards.

---

## Rollback (epic level)

Each slice is a separate commit/PR and reverts independently. Slice 2 carries the
only migration; its index swap is reversible except when duplicate display names
already exist (documented in the slice-2 PR). No data is ever rewritten, so there is
no data rollback in any slice.
