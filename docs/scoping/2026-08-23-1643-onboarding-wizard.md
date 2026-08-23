# Issue #1643 — In-dashboard onboarding wizard (workspace, plan, key, harness, skills, data sources, STATE seed)

**Level:** task · **complexity:** complex · **base:** origin/main

## Confirmed Problem
The first-run experience is dead ends: the welcome card links OUT to static docs, the graph-missing state is a raw-curl card, and there's no guided path to connect an agent, learn the skills, seed state, or connect data sources. The user lands in a capable product with no on-ramp.

## Solution (converged — journey approved by the user 2026-08-23)
A 7-step in-dashboard wizard (progress bar, skippable, resumable) replacing the welcome card's dead links + the graph-missing curl card:

1. **STEP 0 — Name your workspace**: team confirm/rename (the provisioning auto-creates the first team), create-another (`POST /v1/teams`), and multi-team SELECT for returning users (the existing switcher surfaced here).
2. **STEP 1 — Choose your plan**: free default (no card), upgrade CTAs (existing checkout), skippable — account decisions before the credential.
3. **STEP 2 — Your API key**: reveal once (A13) + Copy.
4. **STEP 3 — Connect your tool**: the HARNESS CHOOSER ported into the dashboard (Claude Code / Codex / Cursor / Pi one-click config + Copy — currently only on website/docs.html#mcp). Shared data module (`src/harnesses.js`) mirroring product/pricing.js.
5. **STEP 4 — Your agent's toolkit** (post-setup, in context — research: teach skills when the user can act): 
   - `how-to-use-tortoise` (passive — shipped)
   - `tortoise-decide` (invoke — PACKAGE `skills/tortoise-decide/SKILL.md` from `graph-scripts/decide.py` + the documented 7-step workflow)
6. **STEP 5 — Connect data sources** (NEW): GitHub — guided source-config (repo + state) for the EXISTING `tortoise/connectors/github.py` (issues → Events via the server's `gh` CLI; docs → extraction is a follow-on ingestion path). A first-ingest preview.
7. **STEP 6 — "Try it" = seed STATE**: ask-your-agent path + a guided sample — create an Object (`{workspace}`, in_progress) + a statement Point (`aboutObject`, authored by the user) — populating STATE per the ontology (Objects carry lifecycle + confidence; Points argue about them).
8. **STEP 7 — You're set**: overview alive (Object + Point + Events), what's next.

**Re-entry**: the wizard re-opens at the right step for returning users with an empty/0-point graph (Steps 4–7); complete → normal dashboard.

## Verifier-gate findings (2026-08-23 — folded in)

### D3 correction — decide.py is BROKEN; fix before packaging
`graph-scripts/decide.py:157` calls `sdk.create_point(..., context=ctx, dedup=True)` — `create_point` raises TypeError on `context` (removed in #49), so EVERY point soft-fails and `create_operator` falls back to STUB points/operators (#329/#6713 — the corruption class #334 remediated). **Task order:** fix decide.py (drop `context=`; anchors per #49) + a smoke test, THEN package. The 7-step workflow is NOT documented anywhere in docs/ — the SKILL.md work AUTHORS it (decide.py's input shape maps to options→criteria→findings→edges→truth_edges→relevance_edges→EP confidence). The SKILL.md must be TOOL-based (MCP: tortoise_create_point/create_operator/compute_confidence/check_structure) with decide.py as the self-host variant (decide.py needs a local FalkorDB — hosted tenants can't run it).

### D4 correction — the GitHub OAuth + indexer ALREADY EXISTS; use it, not the gh-CLI connector
`tortoise/connectors/github.py` (gh-CLI) is a SELF-HOST artifact — NOT in the hosted runtime (no gh in the Docker image; no cross-tenant story; a server-wide gh identity would be a shared cross-tenant credential). The hosted API ALREADY ships: `POST /v1/onboarding/github/connect` (OAuth + CSRF + GITHUB_STATES TTL), `GET /v1/onboarding/github/callback` (exchanges code → encrypt_token → github_token_enc — column-REVOKED, service-role-only), `GET /v1/onboarding/github/status` (repos_count), `POST /v1/index/github` + `GET /v1/index/github/{job_id}` (GitHubIndexer REST jobs, cross-tenant isolated), + `github_connected`/`github_indexed` onboarding state. **STEP 5 re-converges onto this surface**: connect = OAuth redirect (popup + state restore); config = repo selection; preview = status + a REST fetch using the team's decrypted token. Security: per-team encrypted token = no new token surface. The docs/extraction ingestion is a follow-on (out of scope here).

### D5 correction — NEW endpoints required for the STATE seed
`POST /v1/points` does NOT support aboutObject (CreatePointRequest = content/kind/tags; extra fields silently dropped). No /v1/objects endpoint. **Required:** (a) `POST /v1/objects` wrapping `sdk.create_object` (deterministic id by name → idempotent); (b) an `about_object` option on the point write that wires the `(p)-[:aboutObject]->(o)` edge (mirror the create_entity event-branch pattern) — NEVER a bare prop (would store a node property, non-canonical).

### Re-entry + state model correction
Use the EXISTING `/v1/onboarding/state` model (onboarding_complete, github_connected, github_indexed, demo_created, team_created) — do NOT invent a new marker. Specify: (a) the wizard writes onboarding_complete on completion; (b) the marker is written only after provisionInApp succeeds; (c) the claim funnel (#1511) runs first — a claimed empty anon team re-enters at STEP 4 (specify + don't clear mid-wizard markers); (d) gate the wizard on `authed && !mountError` (#1559); (e) point_count is the DEFAULT graph's count — a multi-graph user with data in a non-default graph re-enters (gate on the default graph or the graph list); (f) `graph_ready === false` means the STEP 6 seed write is also the graph-recovery write — define the failure fallback.

### Harness chooser source of truth
The chooser lives in welcome.html (HARNESS_* data with copy analytics wired to /v1/onboarding/state harness/section enums) — NOT docs.html's static cards. Port from welcome.html's data + the backend enums.

### Anon teams + OAuth specifics
- STEP 0 is session-gated; define wizard behavior for key-login anon teams (claim funnel first; the D4/D5 endpoints accept tt_ keys via get_current_team, so steps 4–7 are reachable).
- STEP 5 needs the OAuth popup + state restore (the GITHUB_STATES TTL pattern).

## Key decisions
- **D1 — wizard UI**: one step at a time with a progress bar; state in the dashboard (React), resumable via a localStorage/cookie marker (`tt_onboarding_step` non-secret).
- **D2 — harness chooser**: port the docs' per-harness commands into `src/harnesses.js` (single source; docs page can import the same data later).
- **D3 — tortoise-decide**: ship `skills/tortoise-decide/SKILL.md` in this work (wrapping decide.py + the workflow) so the primer teaches something invokable.
- **D4 — data-source step**: the GitHub connector is server-side (`gh` CLI). The wizard's connect step = guided repo/state config + a "fetch issues" preview via a small API endpoint (session-gated) that runs the connector's poll for the chosen repo. The raw GitHub token NEVER crosses origins (the connector uses the server's gh auth — no new secret surface). Docs/extraction ingestion is a follow-on (flag as out-of-scope here).
- **D5 — STATE seed**: the guided sample creates the Object + aboutObject statement via the existing SDK paths (a new small endpoint OR the existing /v1/points + an object-create endpoint if one exists — verify; prefer reusing `POST /v1/points` with aboutObject props if supported, else a minimal `POST /v1/objects`).
- **D6 — the welcome card**: the provisioning key-reveal stays; the wizard replaces the card's LINKS section (Go to keys / MCP / SDK links → the wizard steps).

## Edge cases
- Returning multi-team user: wizard starts at STEP 0 as a team SELECTOR (not naming).
- Empty-graph re-entry: wizard re-opens at STEP 4 (skills) with steps 4–7 available.
- Wizard skipped/closed: a non-intrusive "Continue setup" affordance (header dot/banner) until activated.
- GitHub connect with no gh auth on the server: the preview returns a clear "configure the server's GitHub access" state (not an error card).
- The plan step + the key reveal keep their current non-blocking semantics.
- A13 (reveal-once): the key is shown in STEP 2 exactly once; re-entry never re-reveals.
- The STATE sample is idempotent (a repeated click doesn't duplicate the Object/Point).

## Rejected alternatives
- External-docs onboarding (welcome links out): the user explicitly rejected dead ends.
- Raw-curl card for the empty graph: rejected (dead end).
- Building an OAuth GitHub connect: out of scope — the connector is server-side gh-CLI; a user-facing OAuth would be a larger separate feature (flag as a follow-up).
- Teaching the skills BEFORE the harness setup: rejected (research: teach in context, post-setup).

## Complexity
| Domain | Rating |
|--------|--------|
| Architecture | high (wizard, source-config endpoint, skill packaging) |
| UX | high (the journey) |
| Security | medium (no new token surface — server gh auth; session-gated endpoints) |
| Ontology | low (STATE sample follows the state-centric model) |

## Verification (target)
First-timer walks the full wizard → overview alive (Object + Point). Returning empty-graph user re-enters at STEP 4. Harness chooser copies work. tortoise-decide SKILL.md exists + is linked. Data-source step connects a GitHub repo with a preview. e2e covers the journey (wizard steps, harness copy, skills, STATE seed, source connect mock).
