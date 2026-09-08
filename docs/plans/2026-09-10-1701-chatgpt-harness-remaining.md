---
title: "#1701 ChatGPT harness — remaining slice (consent team chooser + wizard tab + E2E script)"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: live
created: 2026-09-10
---

<!-- research-path: docs/scoping/2026-09-10-1701-remaining-slice-scoping.md -->
<!-- plan-review: cycles=4, status=clean, version=2.3.0 -->

# #1701: ChatGPT harness — remaining slice (consent team chooser + wizard tab + E2E script)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Let ChatGPT users complete MCP OAuth on the hosted endpoint regardless of team count (R1), choose ChatGPT in the onboarding wizard's harness chooser with correct OAuth steps and skills-as-prompt copy (R2), and hand a recorded human E2E script to close indicator 2 (R3).

**Team:** epistemic-team

**Architecture:** Two additive deltas + one evidence doc. R1 = consent-page account-chooser: `consent_preview` returns the user's selectable teams instead of 400-ing when an OAuth client sends no team-scoped RFC 8707 resource (ChatGPT cannot declare one); `parse_resource` additionally tolerates an exact origin-root echo; **suspension semantics are aligned across preview, mint, and exchange** (suspended teams never bind); the consent page renders a team `<select>` and posts the chosen team-scoped resource through the existing (user, team)-binding code. R2 = a 7th `chatgpt` harness in the dashboard vocabulary (teach-human, OAuth-only, key-less) with a dedicated connect-step branch above the existing key gate, capture row disabled-with-reason, and the cross-surface vocab test amended to subset semantics (server capture/analytics Literals stay 6). R3 = a recorded human E2E script + evidence template.

**Pattern research:** Skipped — plan touches zero third-party deps. All patterns are in-repo: Claude Web's skills-as-prompt + teach-human copy (`harnesses.js` `claude-web`), the wizard's existing `wizardCopyStep`/`wizardCopy`/`wizardHarnessContinue` handlers, the consent page's existing preview→authorize JS, and the established `node --test` + pytest string-assert test patterns. OpenAI's ChatGPT surface was externally re-verified 2026-09-10 (developer mode = Settings → Security and login; connector creation now lives at chatgpt.com/plugins as a "Developer-mode app"; auth options OAuth / No Auth / Mixed; AS metadata + PKCE S256 + refresh tokens required) — cited in the scoping doc.

### Integration Surface Map

| Surface | Change | Test layer | Bug-pattern flags |
|---|---|---|---|
| `tortoise/oauth.py` `consent_preview` + `parse_resource` + `_default_team` + `issue_auth_code` | memberships list / origin-root alias / suspension-aligned resolution | pytest (unit via FakeControlPlane) | auth-boundary, contract-ripple |
| `tortoise/oauth.py` `_CONSENT_HTML` + `consent_page_html` | team picker + JS hardening | pytest static-string assertions per behavior | XSS (existing JSON-escape kept), UX states |
| `tests/test_oauth_mcp.py` | contract + boundary + suspension matrix + page-JS strings | pytest | regression on 6 shipped flows |
| `harnesses.js` | chatgpt vocabulary + shared `WORKFLOWS_PROMPT` + `CHATGPT_MCP_URL` | `node --test` | copy exactness (no key, no slash URL) |
| `main.jsx` live connect step + archived gate | key-less chatgpt branch + org-naming line | `node --test` (data) + build + manual ux | branch precedence, member/owner paths, wrong-team bind |
| `harnesses.test.js` | 7-harness exactness | `node --test` | DE2E-5 invariants |
| `tests/test_onboarding_endpoints.py` | subset-semantics vocab test | pytest | frontend/server drift |
| `tests/test_onboarding_analytics_patch.py` | chatgpt beacon-inert pin | pytest | silent 422 / state pollution |
| `tests/e2e/test_dashboard_onboarding.py` | `.harness-tab` count 6→7 | e2e (opt-in `RUN_DASHBOARD_E2E`) | UI drift |
| MemorySources capture panel (main.jsx L7831) | chatgpt row disabled-with-reason | node/build + manual | undefined-reason crash |
| `docs/oauth-mcp.md`, `docs/research/...e2e-script.md` | doc updates + evidence template | n/a | doc drift |

---

### Task 1: `oauth.py` — consent preview team chooser + origin-root tolerance + suspension alignment

**Intent:** Remove the multi-team dead-end for resource-less OAuth clients (ChatGPT) and make team resolution consistent across preview → mint → exchange (suspended teams never bind a grant), keeping the single-team contract byte-identical.

**Acceptance:**
- `parse_resource(base, {base})` and `parse_resource(base, {base}/)` resolve to the bare MCP resource; `{base}/v1/keys`, `{base}/mcp/teams/x/y`, and foreign origins still raise `invalid_resource`.
- No-resource resolution counts only **non-suspended** teams: sole active team → today's single-team shape; >1 active team → `{team_id: None, team_name: None, resource, memberships:[{team_id, team_name, resource}]}` (200); 0 active teams → the existing 403 `invalid_grant`.
- A declared team-scoped resource for a non-member still 403s; a declared resource for a **suspended** team 403s at preview.
- `issue_auth_code` (the consent POST mint) refuses suspended teams (403 `invalid_grant`) — a code is never minted for a team that cannot exchange.
- `/oauth/consent` POST semantics otherwise unchanged (member + active team binds; strict no-resource multi-active-team 400 preserved).

**Files:**
- Modify: `tortoise/oauth.py` (`parse_resource`, `consent_preview`, `_default_team`, `issue_auth_code`, new `_selectable_teams` helper)
- Test: `tests/test_oauth_mcp.py`

**Step 1: Failing tests first** — add to `tests/test_oauth_mcp.py`:
- `parse_resource` boundary: `TEST_BASE` and `TEST_BASE + "/"` → bare MCP, no team; `TEST_BASE + "/v1/keys"` → 400 `invalid_resource`; `"https://evil.example/mcp"` → 400; `TEST_BASE + "/mcp/"` → bare MCP.
- `test_preview_multi_team_no_resource_returns_memberships` (2 active teams) → 200, `team_id is None`, `memberships` length 2, each row has a `team_resource_url` `resource`.
- `test_preview_multi_team_origin_root_echo_returns_memberships` (resource=`TEST_BASE`) → same as above (origin-root echo is treated as no-team-scope).
- `test_preview_single_team_origin_root_echo_binds_sole_team` → 200, `team_id == team-free-001`, no `memberships` key.
- `test_preview_excludes_suspended_teams` — split spec: (a) 2 memberships, 1 suspended → single-team auto-bind shape: `team_id == <active team>`, NO `memberships` key; (b) 3 memberships (2 active + 1 suspended) → `memberships` lists exactly the 2 active rows with scoped resources and the suspended team absent.
- `test_preview_declared_resource_suspended_team_403` (active membership on a suspended team, declared team resource) → 403 at preview.
- `test_consent_mint_refuses_suspended_team`: suspend the team AFTER a successful preview (mid-consent race), then POST the picked team's resource → 403 `invalid_grant`, no code issued.
- `test_exchange_guard_still_rejects_mint_then_suspend_race`: mint a code while the team is ACTIVE, then set `teams.suspended_at` BEFORE the code exchange → `POST /oauth/token` 403 `invalid_grant`, no tokens issued (pins the surviving exchange-time `_assert_team_usable` backstop, which the reworked consent test would otherwise orphan).
- `test_preview_all_teams_suspended_403`: 2 memberships, both teams suspended → preview 403 `invalid_grant` (0-active-after-exclusion branch).
- `test_preview_declared_bare_mcp_resource_keeps_resource_field`: single team, client declares the PRM value `{base}/mcp` (truthy) → preview keeps today's `resource: team_resource_url(base, team_id)` (byte-identical contract pin).
- `test_multi_team_default_requires_declaration` (existing, both teams active) stays 400.
- `test_suspended_team_rejects_code_exchange` (existing): update — the suspension now rejects at the CONSENT POST (code never minted), so rework the test to assert the consent 403 and keep the exchange-level assertion for the refresh path only.

**Step 2: Implement**
1. `parse_resource`: after the existing `base_mcp` equality (which already `rstrip("/")`s), map the exact origin root to the bare MCP resource:
```python
    base_root = base.rstrip("/")
    if resource in (base_root, base_root + "/"):
        return base_mcp, None
```
2. `_default_team` — count only non-suspended teams:
```python
def _default_team(cp, user_id: str) -> str:
    """The user's sole ACTIVE (non-suspended) team (D4 + R1). 0 usable teams
    → error; >1 usable teams → error telling the client to declare a
    team-scoped resource. Suspended memberships never count toward the
    default, so a 1-active + 1-suspended account binds the active team and
    never dead-ends on the multi-team 400."""
    active = [t for t in _selectable_teams(cp, user_id)]
    if len(active) == 1:
        return active[0]["team_id"]
    if not active:
        raise OAuthError(403, "invalid_grant",
                         "This account has no active team. Create a team "
                         "before connecting an MCP client.")
    raise OAuthError(400, "invalid_resource",
                     "This account belongs to multiple teams — the MCP client "
                     "must declare a team-scoped resource indicator "
                     f"({team_resource_url('<base>', '<team_id>')} form).")
```
3. `_selectable_teams`:
```python
def _selectable_teams(cp, user_id: str) -> list[dict]:
    """The user's ACTIVE memberships whose teams are not durably suspended —
    the sole source for default-team resolution AND the consent chooser."""
    from tortoise.supabase_control import user_memberships
    out = []
    for m in user_memberships(cp, user_id):
        rows = cp.query("teams", select=["name", "suspended_at"],
                        filters=[("id", "eq", m["team_id"])])
        if not rows or rows[0].get("suspended_at") is not None:
            continue
        out.append({"team_id": m["team_id"],
                    "team_name": rows[0].get("name") or m["team_id"]})
    # deterministic chooser order (user_memberships has no ORDER BY)
    return sorted(out, key=lambda t: t["team_id"])
```
4. `consent_preview` (replaces the current body — keeps single-team and declared-team shapes byte-identical, including the `resource` field conditional):
```python
def consent_preview(cp, user_id: str, base: str, resource: str | None) -> dict:
    """Consent-page team preview (D4 + R1 account-chooser).

    A client-declared team-scoped resource resolves to that team (membership
    AND suspension checked — a suspended team 403s here, never at exchange).
    A bare/omitted/origin-root-echoed resource resolves to the sole ACTIVE
    team or, for several, returns the selectable list for the page's chooser.
    Zero active teams keeps the 403 so an account with no usable team cannot
    mint a code.
    """
    _, team_id = parse_resource(base, resource)
    if team_id is not None:
        from tortoise.supabase_control import membership_for_user_team
        if membership_for_user_team(cp, user_id, team_id) is None:
            raise OAuthError(403, "invalid_resource",
                             "Not a member of the requested team.")
        _assert_team_usable(cp, team_id)   # suspended → 403 invalid_grant
        return {
            "team_id": team_id,
            "team_name": _team_name(cp, team_id),
            "resource": (team_resource_url(base, team_id) if resource
                         else mcp_resource_url(base)),
        }
    teams = _selectable_teams(cp, user_id)
    if len(teams) == 1:
        return {
            "team_id": teams[0]["team_id"],
            "team_name": teams[0]["team_name"],
            # byte-identical with today: a truthy declared resource (bare MCP
            # or origin echo) keeps the team-scoped resource field.
            "resource": (team_resource_url(base, teams[0]["team_id"])
                         if resource else mcp_resource_url(base)),
        }
    if len(teams) > 1:
        return {
            "team_id": None,
            "team_name": None,
            "resource": mcp_resource_url(base),
            "memberships": [
                {**t, "resource": team_resource_url(base, t["team_id"])}
                for t in teams
            ],
        }
    raise OAuthError(403, "invalid_grant",
                     "This account has no team. Create a team before "
                     "connecting an MCP client.")
```
`_selectable_teams` returns teams sorted deterministically by `team_id` (stable order for the chooser; the chooser never auto-binds — see Task 2).
5. `issue_auth_code` — assert the team is usable BEFORE inserting the code (suspension surfaces at consent, not at a later exchange):
```python
    team_id = _resolve_team(cp, user_id, base, resource)
    _assert_team_usable(cp, team_id)
```
(place after the existing `team_id = _resolve_team(...)` line, before the code insert).
6. Keep `_resolve_team` otherwise unchanged (its `_default_team` call now inherits suspension-aware resolution).

**Step 3:** run `tests/test_oauth_mcp.py` — all green.

---

### Task 2: Consent page — team picker + JS hardening

**Intent:** Let the user choose which team an OAuth grant binds when the client couldn't, with the page never dead-ending on a transient preview failure, a stale session, or a sign-in race.

**Acceptance (each has a pinned static-string test in Step 1):**
- Multi-membership preview → team `<select>` populated (options rebuilt from scratch on every populate — no duplicate rows on re-runs); the Authorize POST carries the selected team's scoped `resource`.
- **Multi-team pickers NEVER auto-bind.** With `memberships.length > 1`, Authorize stays disabled until the user explicitly picks a team (`change` event) — an untouched picker cannot authorize a wrong-org default. Single-team pages (and the sole-ACTIVE-team auto-bind) render exactly as today and authorize with the bound team (no picker).
- `#btn-auth` starts **`disabled` in the markup** and is enabled only after the preview resolves (single bound team) or an explicit selection exists; each `showConsent()` re-run disables it again until that run resolves; the handler early-returns when still disabled (double-click and pre-preview POST impossible).
- Preview **401 with a session present** → **refresh-first recovery**: `await supabaseClient.auth.refreshSession()` (the stored cookie still holds a valid refresh_token — the page runs `autoRefreshToken: false`, so only the ~1h access JWT is stale); on success re-run the guarded preview; on refresh failure show sign-in with "Your session expired — sign in again." **No `signOut()` on this path** — a global-scope signOut would revoke the shared parent-domain session server-side (logging the user out of the dashboard on every device). Any session clear uses local scope only.
- Preview **network/non-401 failure** → error banner + `#btn-retry-preview` that re-runs the preview (Authorize stays disabled).
- `onAuthStateChange` (`SIGNED_IN`/`INITIAL_SESSION`) auto-advances to the consent view, guarded by an in-flight flag that spans the async preview (no duplicate preview fetches / duplicate `<option>` population).

**Files:**
- Modify: `tortoise/oauth.py` (`_CONSENT_HTML` only; `consent_page_html` signature unchanged)
- Test: `tests/test_oauth_mcp.py`

**Step 1: Failing static-string tests** — render `consent_page_html(...)` and assert the embedded markup/script contains, per behavior:
- `test_consent_html_single_team_select_not_visible`: single/auto-bound team — the served markup keeps the select `display:none` and the `team-line` visible; the JS unhide + placeholder insertion is gated on `preview.memberships.length > 1`; Authorize enables on preview resolution without any picker interaction.
- `test_consent_html_team_picker_wiring`: `id="team-select"` present; the Authorize body expression `teamResource || PARAMS.resource || null` present; `body.resource` assignment reads the selected team resource.
- `test_consent_html_select_hidden_by_default`: the select's initial inline style is `display:none`, and only a `memberships.length > 1` branch unhides it.
- `test_consent_html_authorize_disabled_in_markup`: `btn-auth` renders with a `disabled` attribute; an enable statement exists that runs only after preview resolution / selection; the handler contains an early-return guard.
- `test_consent_html_picker_requires_explicit_selection`: the populate branch REBUILDS the select's options from scratch (clears existing children before appending) and does NOT set `teamResource` from the pre-selected option; Authorize is enabled only in the select's `change` handler (no auto-bind default).
- `test_consent_html_401_recovery`: the 401-with-session branch attempts `supabaseClient.auth.refreshSession()` BEFORE any re-auth path, contains NO `signOut(` call on the preview-401 path, and is capped (a `refreshAttempts` counter / sentinel — never an unbounded refresh→preview loop); the refresh-failure branch shows the sign-in view with the expired-session message.
- `test_consent_html_consent_post_401_recovery`: the Authorize POST-401 path refreshes once and re-POSTs before showing the expired-session view (no `signOut()`).
- `test_consent_html_picker_placeholder`: the select is populated with a leading disabled `value=""` placeholder option ("Choose a team…") so the first real selection always fires `change` and the picker visibly starts un-chosen.
- `test_consent_html_retry_and_inflight_guard`: `id="btn-retry-preview"` present; a `previewInFlight`-style flag is set before `fetchPreview` and cleared in a `finally`, with an early return when already in flight.
Behavior beyond these strings is explicitly NOT auto-verifiable (no jsdom layer exists for this page) — residual risk is owned by Task 5 (manual) + R3 observations, per the surface map.

**Step 2: Implement in `_CONSENT_HTML`**
1. Team row:
```html
<div class="row"><span class="k">Team</span>
  <span class="v" id="team-line">resolving…</span>
  <select id="team-select" style="display:none" aria-label="Team for this connection"></select>
</div>
```
2. `#btn-auth` markup gains `disabled` (enabled by JS post-preview). `showConsent()` becomes in-flight-guarded, disables `#btn-auth` at every re-entry, **and resets the module-level `teamResource = null` at every entry** (the ONLY writer of `teamResource` is the current run's picker `change` handler — a re-run can never carry a stale selection into the POST); it **returns a `'stale'` sentinel when a session exists but the preview 401s**, so recovery runs OUTSIDE the guarded body (see item 4). After the preview resolves: single/auto-bound team → today's team-line text and Authorize enabled; `memberships.length > 1` → CLEAR the select's existing options, add a leading disabled `value=""` placeholder ("Choose a team…"), then append the real options (value = `m.resource`, label = `team_name (team_id)`), unhide the select, hide `team-line`, set `resource-line` to picker copy; `teamResource` is set ONLY in the select's `change` handler (the placeholder guarantees the first real pick fires `change`), and Authorize enables only then — an untouched picker cannot authorize (no silent wrong-org bind).
3. Authorize handler: `body.resource = teamResource || PARAMS.resource || null`; early-return (with an error) if the button was never enabled; **a 401 POST response triggers one `refreshSession()` then a single re-POST that RE-READS the session (`await supabaseClient.auth.getSession()`) and uses the fresh `access_token` — only a second 401 shows the expired-session sign-in view** (no `signOut()`; the cookie is replaced by a fresh sign-in).
4. Stale-session recovery (single driver, capped): the caller of `showConsent()` (initial load, the `onAuthStateChange` handler, and the email sign-in path) receives the `'stale'` sentinel and performs AT MOST ONE `await supabaseClient.auth.refreshSession()` (the cookie still holds a valid refresh_token — the page runs `autoRefreshToken: false`, so only the ~1h access JWT is stale), then re-invokes `showConsent()` (the in-flight guard is already cleared). A second consecutive `'stale'` renders the expired-session sign-in view with "Your session expired — sign in again." (no `signOut()` anywhere on this path — a global-scope gotrue logout would revoke the shared parent-domain session server-side, logging the user out of the dashboard on every device). No session → `showSignin()` as today; non-401 preview failures → error banner + show `#btn-retry-preview` (re-runs the guarded `showConsent`), Authorize stays disabled.
5. After client creation:
```js
let previewInFlight = false
supabaseClient.auth.onAuthStateChange((event) => {
  if ((event === 'SIGNED_IN' || event === 'INITIAL_SESSION') && !previewInFlight) showConsent()
})
```
Keep all existing escaping/`_json_for_script`/nonce/CSP behavior untouched.

**Step 3:** `tests/test_oauth_mcp.py` green (existing consent-page tests unchanged).

---

### Task 3: `harnesses.js` — chatgpt vocabulary entries + shared workflows prompt

**Intent:** Add the 7th harness data so every `HARNESS_ORDER` consumer stays total, with byte-identical 6-harness content and zero copy drift between the Claude Web and ChatGPT skills-as-prompt payloads.

**Acceptance:** All exports accept `chatgpt`; the 6 existing entries' strings are unchanged; `HARNESS_INSTALL['claude-web']()` output is byte-identical to today; chatgpt copy embeds `CHATGPT_MCP_URL` (no slash), no key, no `tt_`, no `.../mcp/` slash form.

**Files:**
- Modify: `website/apps/dashboard/src/harnesses.js`

**Step 1: Constants** — add after `MCP_URL`:
```js
// #1701: ChatGPT's custom-connector URL — NO trailing slash. OpenAI's
// connector validates an '/mcp' suffix, and bare POST /mcp dispatches
// directly into the mounted MCP app (no 307). MCP_URL above keeps its
// slash for the 6 keyed harnesses (byte-identical copy).
const CHATGPT_MCP_URL = 'https://api.premiselabs.co/mcp'

// #1701: the skills-as-prompt body shared by Claude Web and ChatGPT (both
// have no local skills). ONE constant so the two user-facing prompts can
// never drift.
const WORKFLOWS_PROMPT =
  `You have Tortoise connected (the 'tortoise' MCP tools). Follow these workflows:\n\n1) Writing to the graph — Tortoise stores knowledge as points with edges: IMPL means 'supports', NAND means 'contradicts'. Mitigations reduce confidence (range 0.10–0.50). To change a point, supersede it and clean up its active edges rather than editing in place. Prefer structural claims over labels and always cite provenance.\n\n2) Decisions — to make a decision, first refine it, then research the options, the criteria that matter, and the findings/evidence, then wire IMPL/NAND edges from findings and criteria to options (mitigate an edge, range 0.10–0.50, when it's true but matters less), and rank the options by EP confidence.\n\n3) Research findings — when I share a research finding, ingest it as a point, check for existing related claims first, and surface connections to what we already know.`
```
**Step 2: Refactor `HARNESS_INSTALL['claude-web']`** so its `base` variable reads `const base = WORKFLOWS_PROMPT` (the session-filing gating paragraph is unchanged) — output byte-identical.
**Step 3: Vocabulary maps** — add `chatgpt`:
- `HARNESS_CAPTURE_SUPPORT`: `chatgpt: false`
- `HARNESS_NAMES`: `chatgpt: 'ChatGPT'`
- `HARNESS_SKILLLESS`: `['claude-desktop', 'claude-web', 'chatgpt']`
- `HARNESS_STEPS` `chatgpt` array:
  1. 'Enable Developer mode: chatgpt.com → Settings → Security and login → Developer mode (Plus/Pro/Business/Enterprise/Education).'
  2. 'Open chatgpt.com/plugins → the + button → create a Developer-mode app.'
  3. `{ label: 'MCP server URL', code: CHATGPT_MCP_URL, copy: CHATGPT_MCP_URL }`
  4. 'Choose OAuth — ChatGPT discovers Tortoise's authorization server (no API key needed).'
  5. 'Scan Tools — sign in to Tortoise when prompted and click Authorize.'
  6. 'The tortoise_* tools appear (Developer mode); write actions ask for confirmation in chat.'
  7. 'Paste the prompt below into a ChatGPT chat — it gives ChatGPT the Tortoise workflows.'
- `HARNESS_INTRO.chatgpt`: 'Start a new chat at chatgpt.com and paste the prompt below — that conversation becomes your connected agent. ChatGPT has no local skills — the prompt gives it the Tortoise workflows.'
- `HARNESS_INSTALL.chatgpt = () => WORKFLOWS_PROMPT`
- `HARNESS_COPY_LABEL.chatgpt = 'Copy prompt'`
- `HARNESS_CONTINUE_LABEL.chatgpt = "I've connected it — Continue →"`
- `HARNESS_CAPTURE_REASON.chatgpt = 'ChatGPT connects from its own cloud — session capture isn't available for it'`
- `HARNESS_ORDER`: append `'chatgpt'` (last).
- New `export const HARNESS_OAUTH = ['chatgpt']` with a comment: no-key/OAuth-only harnesses; the wizard connect step renders these key-less.
- `HARNESS_TEACH_HUMAN`: append `'chatgpt'`.
- `UNIVERSAL_COMMAND.chatgpt = () =>` a self-contained copy block: numbered connector steps + WORKFLOWS_PROMPT + checkpoint sentence ("After the tools appear, in the same ChatGPT chat paste the prompt above; confirm Tortoise answers and click \"I've connected it — Continue →\" in the dashboard."). This copy is R3-validated (asserts a user-facing chat check, not a server signal).
- Update the internal count comment to the 7-tab reality.

**Step 4:** `node --test src/harnesses.test.js` — Task 6's rewrite is the only allowed failure source.

---

### Task 4: `main.jsx` — live connect-step chatgpt branch + archived gate guard + org-naming

**Intent:** Render a key-less ChatGPT connect flow for wizard step 2 (self fork) that is correct for members AND owners, unreachable by the key-mint/universal-command fragments, names the onboarding org so the consent picker binds the RIGHT team, and is safe on the archived A0 rollback path.

**Acceptance:**
- Selecting the ChatGPT tab with no key, with a key from an earlier tab, as a member, or as an owner/admin all render the SAME key-less chatgpt flow.
- Copy prompt fires the sticky `wizardCopied === 'harness'` state and the copy beacon; Continue calls `wizardHarnessContinue`.
- The branch renders an org-naming line ("When Tortoise asks which team to use, choose **{shownOrgName}**") so a multi-team user picks the org they're onboarding.
- No chatgpt copy references the API key, the shown-once paragraph, or "run the command".
- 6-harness renders byte-identical; the stale "covers all 6 harnesses" comment near the keyed else-branch is updated.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx`

**Step 1: branch precedence + import** — add `HARNESS_OAUTH` to the named import from `./harnesses.js` (main.jsx L6). Then at the top of the self-fork `wizardStep === 2` content, above the `!harnessKey ? (...) : (...)` ternary (~L5677), insert the chatgpt branch as an unconditional sibling:
```jsx
{wizardHarness === 'chatgpt' ? (
  /* key-less ChatGPT flow — Step 2 */
) : !harnessKey ? (
  /* existing key-mint/paste gate — UNCHANGED */
) : (
  /* existing universal-command render — UNCHANGED */
)}
```
**Step 2: chatgpt content JSX** (inline; mirrors the archived wizard's step-list markup + live nav chrome):
- `<ol className="harness-steps">` from `HARNESS_STEPS('chatgpt', harnessKey)` using the existing `wizardCopyStep`/`copiedStep` per-step copy buttons.
- `HARNESS_INTRO.chatgpt` paragraph.
- Org-naming line when `shownOrgName` is available: "When Tortoise asks which team to connect, choose **{shownOrgName}**."
- `<pre className="snippet">` of `HARNESS_INSTALL.chatgpt()`.
- `wizard-nav` with Back (step 1) / **Copy prompt** → a chatgpt-local copy handler that AWAITS `navigator.clipboard.writeText(HARNESS_INSTALL.chatgpt())` and only on resolution sets the sticky `wizardCopied === 'harness'` state + fires the PATCH beacon (reusing `wizardCopy` semantics); on clipboard rejection it surfaces "Copy failed — select the prompt below and press ⌘/Ctrl-C" and does NOT enable Continue (the shared `wizardCopy` fire-and-forget stays untouched so the 6 keyed harnesses are byte-identical). **Continue rendered ONLY when `wizardCopied === 'harness'`** → `wizardHarnessContinue` (mirrors the claude-web gate: a checkpoint requires a successful copy action, keeping the done card honest) / Skip for now.
**Step 3: archived A0 wizard guard** — in the archived `!harnessKey` gate (~L5990), treat OAuth harnesses as key-satisfied:
```jsx
{(!harnessKey && !HARNESS_OAUTH.includes(wizardHarness)) ? ( /* existing no-key UI */ ) : ( /* existing copy path */ )}
```
**Step 4: comment updates** — refresh the main.jsx "the universal command covers all 6 harnesses" comment (~L5901) and the archived wizard block's analogous stale-count copy to the 7-tab reality.
**Step 5:** visual smoke via `npm run dev` — chatgpt tab key-less for member/owner/no-key/with-key states; 6 existing tabs byte-identical; MemorySources capture panel shows the chatgpt row with the disabled reason (no `undefined`).

---

### Task 5: Manual/UX verification checklist

Carried out during implementation (Task 2/4) and recorded in the PR body:
- Consent page: single-team byte-identical; **multi-team picker — an untouched picker does NOT authorize (Authorize stays disabled until an explicit selection; picking a team binds exactly that team)**; options render once (no duplicates); Authorize disabled pre-preview; retry on preview failure; stale-session recovery shows the sign-in view after one refresh attempt.
- Wizard chatgpt: member-no-key, owner-with-key, tab-switch-after-mint, skip/back nav; org-naming line rendered; **block the clipboard once and confirm Continue stays hidden until a successful copy**; 6 tabs unchanged.
- MemorySources: chatgpt row renders the reason (never `undefined`).

---

### Task 6: Tests + docs — harnesses.test.js rewrite, vocab subset test, beacon pin, e2e count, docs

**Intent:** Keep the vocabulary exactness invariants true for 7 tabs, subset the server cross-surface contract to the 6 capture-capable harnesses, pin the chatgpt beacon as inert, fix the e2e tab count, and update the OAuth doc.

**Acceptance:** All listed test files green; docs updated.

**Files:**
- Modify: `website/apps/dashboard/src/harnesses.test.js`, `tests/test_onboarding_endpoints.py`, `tests/test_onboarding_analytics_patch.py`, `tests/e2e/test_dashboard_onboarding.py`, `docs/oauth-mcp.md`

**Step 1: `harnesses.test.js`** — add `HARNESS_OAUTH` to the top-of-file import list from `./harnesses.js` (the new assertions reference it). Rewrite DE2E-5 vocabulary assertions: `HARNESS_ORDER.length === 7`; `SELF_INSTALL` exact `['claude','codex','cursor','pi']`; `TEACH_HUMAN` exact `['claude-desktop','claude-web','chatgpt']`; `HARNESS_OAUTH === ['chatgpt']`; disjointness (`SELF_INSTALL ∩ TEACH_HUMAN = ∅`, `HARNESS_OAUTH ∩ SELF_INSTALL = ∅`, `HARNESS_OAUTH ⊆ TEACH_HUMAN`); union == HARNESS_ORDER; `Object.keys(HARNESS_NAMES)` set == HARNESS_ORDER set. Keep the universal-command total loop. chatgpt copy assertions: includes `Developer mode`, `chatgpt.com/plugins`, `https://api.premiselabs.co/mcp` (exact), `OAuth`, a workflows-prompt marker; does NOT include `tt_`, the test key, `tortoise_health`, or `api.premiselabs.co/mcp/` (slash form). The teach-human `tortoise_health` regex applies to desktop/web only — chatgpt asserts its own in-chat verify sentence. `HARNESS_CAPTURE_SUPPORT.chatgpt === false` + reason present. Assert `HARNESS_INSTALL['claude-web']()` and `HARNESS_INSTALL.chatgpt()` share the identical workflows body (drift pin on `WORKFLOWS_PROMPT`).
**Step 2: `test_onboarding_endpoints.py`** — `test_cross_surface_harness_vocab_contract`: keep server exact-6 assertions; change the frontend assertion to subset + carve-out:
```python
    assert frontend >= set(_HARNESS_ANALYTICS_VALUES)
    # #1701: chatgpt is a WIZARD-ONLY harness — ChatGPT never files sessions
    # or fires copy-attribution (no local skills), so it is intentionally
    # absent from the server capture/analytics vocabulary.
    assert frontend - set(_HARNESS_ANALYTICS_VALUES) == {"chatgpt"}
```
**Step 3: `test_onboarding_analytics_patch.py`** — add `test_patch_chatgpt_harness_beacon_is_inert`: PATCH `{harness:'chatgpt', section:'config'}` → 200, no `artifact_copied` event, `harness`/`section` absent from the merged onboarding state (mirrors the existing vim-invalid case; documents chatgpt as intentionally beacon-less).
**Step 4: `tests/e2e/test_dashboard_onboarding.py`** — the `.harness-tab` count assertion (~L173): 6 → 7 and refresh the stale "HARNESS_ORDER is 6 …" comment to the 7-tab reality (Claude Code leg + paste-key flow otherwise unchanged).
**Step 5: `docs/oauth-mcp.md`** — update the token→team mapping row (multi-team now offers the consent chooser; suspended teams excluded; origin-root resource echoes accepted as the bare MCP resource) and add a short "Resource-less clients (ChatGPT)" note.

---

### Task 7: E2E script doc + dist rebuild

**Intent:** Deliver the recorded human E2E (indicator 2) and ship the built dashboard so the tab is live.

**Acceptance:** `docs/research/2026-09-10-1701-chatgpt-e2e-script.md` exists with the evidence template; dist rebuilt from the merged main.jsx.

**Files:**
- Create: `docs/research/2026-09-10-1701-chatgpt-e2e-script.md`
- Modify: `website/apps/dashboard/dist/**` (rebuild)

**Step 1:** Write the E2E doc — the numbered ChatGPT Developer-mode script + an evidence-template checklist with MANDATORY fields: plan tier + workspace type; exact connector URL form used; auth choice shown; tool-scan result (which `tortoise_*` tools appear); consent page inside the webview (session reuse vs sign-in; **team selected in the consent picker vs the org name shown in the wizard header** — fails the E2E on mismatch); a graph write round-trip question + confirmation it landed in the onboarding org's graph; refresh behaviour after ~24 h; redirects/errors observed; DCR errors under load. External risks to record: OpenAI plan entitlement for write tools; consent picker shows the team list exactly once (no duplicate options).
**Step 2:** Rebuild the dashboard (`npm run build` in `website/apps/dashboard`) and commit the new hashed bundle + `index.html`.

---

## Failure Modes (not covered by unit tests → observed in R3 / manual)
- Consent page inside ChatGPT's OAuth webview (CDN script, cookie jar, provider redirect) — R3 observation list.
- OpenAI plan entitlement / tool-scan heuristics — R3 records plan tier + scan result.
- DCR shared-egress per-IP limiter pressure at launch — ops note in the PR body.
- Verify-in-chat sentence accuracy — R3 validates before the copy is treated as true.
- Consent-page JS behavior beyond static strings (disable gate, retry, 401 recovery, picker selection) — Task 5 manual + R3.

## Code-notes (cycle-2/3 resolutions)
- Escaping: the chatgpt copy literals containing apostrophes (`Tortoise's`, `isn't`) MUST be written with escaped quotes or double-quoted JS strings — never raw single quotes inside a single-quoted literal (`harnesses.js` + the plan snippets are illustrative).
- `signOut()` is FORBIDDEN on the consent-page 401 paths (shared parent-domain session; global-scope gotrue logout revokes across devices) — refresh-first only, capped at ONE refresh attempt per stale cycle.
- Multi-team pickers never auto-bind (leading disabled placeholder forces an explicit `change`); single/auto-bound teams authorize directly.
- The wizard's chatgpt Continue gate mirrors claude-web (requires a successful copy — the chatgpt branch awaits the clipboard write).

## Verification Plan
1. `uv run pytest tests/test_oauth_mcp.py tests/test_onboarding_endpoints.py tests/test_onboarding_analytics_patch.py tests/test_hosted_auth.py tests/test_mcp_server_auth_modes.py -q` (docker lane, `tortoise_test_1701`) → green.
2. `node --test` in `website/apps/dashboard` (`src/*.test.js`) → green.
3. `npm run build` clean; dist committed.
4. Manual UX smoke (chatgpt key-less; 6 tabs unchanged; capture row disabled-with-reason).
5. E2E: `tests/e2e/test_dashboard_onboarding.py` updated (opt-in `RUN_DASHBOARD_E2E`).
6. R3 human E2E recorded against the PR (non-blocking).
