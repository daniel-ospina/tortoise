---
title: "#2166 API Keys table — durable keys only (scope + verified plan)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-api-keys, tortoise-dashboard
created: 2026-09-02
---

# #2166 Scope — API Keys table: durable-only managed list, temporary keys in a separate section

> **Owner amendment (2026-09-02, applied post-review):** no separate
> "Temporary access keys" section. Auto-minted session credentials are **never
> rendered** on the API Keys page — not as rows, not as a zone. The render shows
> the durable managed set only (zones collapse: `swept`/`session` rows are
> excluded from the DOM). Revoked **durable** keys stay visible inline with
> `revoked_at`; copy carries no "temporary/permanent/session" vocabulary.
> Server endpoint unchanged (still unfiltered for CLI/selfhost consumers).
> See issue #2166 comment (issuecomment-5524398240).
>
> **SHIPPED-DESIGN SUPERSEDE (PR #2175, code-review P2):** the delivered
> implementation is deliberately simpler than §2's `normalizeKeys` VM /
> three-zone model: one pure `isManagedKey` predicate (durable-only) +
> a managed-keys filter at render. The `normalizeKeys` VM, `keyZone`,
> `managedStatus` (expired/in-use statuses), per-action `can*` helpers,
> session/swept tbodies and §4 AC1/AC2's "separate section" wording are
> **superseded by the amendment + this banner** — do not rebuild the VM.
> The held-durable "in use" disclosure ships as a visible dim note under the
> status cell (not a status token). Status vocabulary shipped: revoked /
> disabled / active. The scope's S5/S6 mixed-fixture e2e + CI job (AC5) did
> NOT ship in PR #2175 — tracked as follow-up issue #2178.


> Consolidated scoping (double-diamond, verified). Companion research: `docs/research/2026-09-02-api-keys-table-session-credentials.md` (commit both as S0 first-chore). Issue: #2166. Complexity: standard. Team: epistemic-team. Worktree: `feat/2166-keys-table-durable-only`.

## 0. Problem (confirmed, narrowed indicator-1 form)

The API Keys surface's managed-list model is incoherent: it renders rows of mixed nature — deliberate durable keys (`created_via` provisioned, legacy NULL), system-minted durable fallbacks (recovery), and auto-minted 24h temporary credentials (bootstrap) — and its status vocabulary misrepresents them: temporary credentials masquerade as manageable rows, the live in-use credential appears as an unexplained undeletable bare "active" row, disabled keys render "active", and expired/revoked rows persist with no explanation. **#2166 ships the client-model half** (ships before #2167 removes the login auto-mint): a coherent classification + rendering in which the managed list is durable-only by an explicit `created_via` predicate, temporary (bootstrap) credentials surface in a separate labeled non-actionable section, every row's status is truthful, and every blocked row carries a visible plain-language explanation.

### Three incoherence classes (all closed by this scope)
1. **Temporary-credential masquerade** — bootstrap rows look like manageable keys. → separate section (class-1 closed).
2. **Unexplained live durable key** — the row the browser/agents are using is bare "active" + undeletable. → "In use" status + replace-first explanation. Closure is for the LIVE key only; a **non-live system-minted recovery leftover still renders as an unexplained "active" actionable row this release** (deliberate-vs-fallback provenance needs server data → separate provenance issue; user-visible gap stated, not hidden).
3. **Status lies + clutter** — disabled renders active; expired-as-live never guarded; swept noise accumulates. → truthful statuses; swept rows behind a filter; durable revocations stay inline with dates.

### Falsification
Wrong if: production already behaves post-#2167; a deliberate user-facing session-key affordance exists; recovery is server-documented as deliberate-user keys; the narrowed explanation still fails a fresh-user e2e with a realistic mixed fixture; sequencing inversion (#2167/W2 first → section moot — S0/S6 checks).

## 1. Boundary (what #2166 is NOT)

- **Server unchanged** — GET /v1/team/keys returns the full row set (CLI/selfhost/keyless consumers; client needs full rows for section + filter + revokeKey re-mint resolution). Issue's optional exclude-session server param **declined** at scoping.
- **isActiveKey invariant unchanged** — live credential never revocable/togglable from UI while live (test 5 pin). Rotate-with-replacement, never delete-of-live (Stripe/GitHub precedent).
- **No state filter** — durable-only view is a render-level projection over full `keys` state (revokeKey re-mint resolution ~L3886-3930 and keyIdFromValue ~L4026-4036 iterate full state).
- **Out**: login/team-switch auto-mint removal → #2167; wizard copy (durable at connect) → W2 #1998 (PR #2161 OPEN); hard-delete/retention, provenance, DELETE role-guard parity, recovery-vs-deliberate, member-role visibility → separate issues (5+ filed).
- **Hard-blocked regions (no edits)**: wizard ~L3594-3700 and ~L4291-4404 (epic #1976 W1 MERGED/W2 PR #2161 OPEN); mintSessionKey ~L2116-2196; revokeKey re-mint ~L3899-3925; loadAll ~L3012-3046; toggle/rename ~L3824-3881.

## 2. Chosen solution — B′: normalizeKeys VM + three-zone declarative render (no useMemo)

sessionKey.js becomes ONE classification engine producing row view-models; main.jsx consumes declaratively. One pass per render. Chosen over: **A** (per-call-site decision fns — repeats the disconnected-ternary architecture that caused the incoherence; no structural link blocked↔explained), **C** (single-table zone-header — fails "clearly separate section" + lifecycle-column semantics + worse W2 removal seam; documented fallback if human gate rejects a new DOM section), **B-with-useMemo** (refs-reactivity footgun).

### Classification model (final)
- `keyZone(k)` → `'swept'` (created_via==='bootstrap' && revoked_at) | `'session'` (created_via==='bootstrap' && !revoked_at) | `'managed'` (everything else — provisioned/recovery/NULL, incl. durable revoked). No double-render by construction.
- `managedStatus(k, activeKey)` order: **revoked** (revoked_at) > **expired** (expires_at past — never live regardless of held) > **in-use** (delegates to unchanged `isActiveKey`) > **disabled** (enabled===false; enabled absent → enabled, registry parity) > **active**.
- `sessionExpiryText(expiresAt, nowMs)` — **clock injected** (memorySourcesStatus convention; deterministic node tests); returns `{expired}` only; formatting stays in main.jsx `fmtTime`.
- `normalizeKeys(keys, activeKey, {isManager})` → `{managed: VM[], session: VM[], swept: VM[], counts}`; VM = `{key, zone, status, statusLabel, reason, expiryText, canRename, canToggle, canRevoke, revokedAtText}`. reason produced by the same code as can* — copy lives in the module (memorySourcesStatus/captureStatus precedent), exported as frozen constants.
- can* (managed zone only, per-action):
  - `canRename = status ∉ {revoked, expired} && isManager` (rename KEPT on in-use rows — today's rename gate L5485 never checks isActiveKey; no regression).
  - `canToggle = !isActiveKey && status ∉ {revoked, expired} && isManager`.
  - `canRevoke = !isActiveKey && status !== 'revoked' && isManager` (never-revoke-absolute for ANY held key; expired non-held revocable as cleanup; disabled non-held revocable).
- **Status-semantic invariant** (unit test, manager ctx): every managed VM with `canToggle===false || canRevoke===false` AND status ∈ {in-use, expired} must carry reason === the module-pinned copy for that status. Revoked exempt (terminal — label + revocation date is the explanation). Placeholder/mislabeled copy = test failure. AC2 = test failure if violated.
- `isManager:false` (member): all can* false + banner; per-row reasons STILL render (status truth, not affordances).
- main.jsx: import normalizeKeys; delete wrapper ~L4019-4023; call once per render, NO useMemo (n ≤ ~10; refs-at-render matches current wrapper semantics); DOM consumes VM only. `isSessionKey` removed; "ephemeral" string (L5498) dies with the status-cell rewrite. Fixture/harness tokens must NOT embed banned user-facing substrings: use neutral `tt_live_recovery_key_abcdef0123456789` (prefix slice `tt_live_re`), and the harness mint must stay on the SILENT mount path (no createKey-style plaintext disclosure — `setNewKey` ~L3765/3990 renders into the `.new-key` code box L5453).

### Pinned user-facing copy (FINAL — plain language, no internal taxonomy)
| Element | Copy |
|---|---|
| Status labels | Active / In use / Disabled / Expired / Revoked |
| In-use reason | "This is the key this dashboard and your agents are using. It can't be revoked while it's live — create a new key, switch your agents to it, then revoke this one." |
| Expired reason (revoke-agnostic) | "This key is expired — it can no longer authenticate. Create a new key and update your agents." |
| Section heading | "Temporary access keys" |
| Section sub (body color 13-14px — NOT .dim; remediation copy, AA 4.5:1) | "Created automatically when you sign in — also used in setup commands until you create a permanent key. They expire on their own and can't be managed here; use + New key above for a permanent key." |
| Member banner | "Renaming, disabling, or revoking keys requires an owner or admin in this dashboard — you can still create keys for your own agents." (surface-scoped: server DELETE /v1/team/keys is team-scoped only today, no role gate on either lane — deferred parity issue owns the banner text once parity lands) |
| Swept group note | "Temporary keys are moved here when they expire or are rotated out — they can no longer be used. Kept so you can identify what stopped working." |
| Empty: all-zero | "No keys yet." (verbatim parity) |
| Empty: managed=0, session>0 | "No permanent keys yet. The temporary keys below expire on their own — create a permanent key above for agents and services." (copy branch; toggle still renders if swept>0) |
| Empty: managed=0, swept>0 (no session) | "No permanent keys yet." + toggle (vestigial "active" qualifier dropped — durable revoked/expired/disabled rows are inline in managed, so managed=0 means zero permanent keys exist) |
| Durable revoked row | "Revoked" + dim date (fmtTime(revoked_at)) |

UI copy rules: NO "durable"/"ephemeral"/"session credential" in user-facing strings (internal names only); "permanent key" ↔ "temporary keys"; user vocabulary chosen NOW (e2e fragments pin it — rewording after pins is the expensive direction).

### DOM (final)
- Existing `<table>` L5457: managed rows only — status cell renders truthful statusLabel with status-appropriate classes (disabled/expired muted, revoked red, live green — text labels, never color-only, WCAG 1.4.1); reason lines render **status-adjacent at BODY color 13-14px** (NOT .dim.small — 3.7:1 fails AA 4.5:1 at 12px; the reason IS the AC2 deliverable); actions cell from canToggle/canRevoke; rename from canRename. Add `scope="col"` to thead while rewriting. SECTION SUB also at body color (same AA ruling — it is remediation copy, not secondary framing; .dim reserved for genuinely secondary text: group notes, dates, banner). Wrap in `.keys-table-wrap{overflow-x:auto}` (created-dates `toLocaleString()` with seconds are unbreakable ~150px).
- Second `<tbody id="swept-rows">` **always in the DOM** with `hidden={!showRevoked}` (stable aria-controls target); zone label = plain `<td colSpan={5} className="dim small">` row (NOT `<th>` in tbody); swept rows render prefix + revoked/expired date.
- Toggle: ghost.small under the table's left edge, before the section, visible only when swept>0: "Show expired temporary keys (N)" ↔ "Hide expired temporary keys", aria-expanded + aria-controls="swept-rows". `setShowRevoked(false)` added to switchTeam's existing reset block (~L3252); logout covered by remount. Sticky cross-team preference NOT chosen.
- `<section className="session-section">` between table and BackupsCard (L5524), rendered only when vm.session non-empty: rows = prefix code + expiry line ("expires {fmtTime}" / "expired {fmtTime}" — never claims live). NO controls. `.session-section` CSS at index.css L141-147 is dead #714 code (margin-top + dim h3 only) — plan ADDITIVE container chrome from the .turn-item/.extracted-item family (surface + 1px border + radius + ~12px padding); heading at normal emphasis; update the L141-147 comment group.
- Member banner (!isOwnerAdmin) above table (Members-tab precedent L5589).

## 3. Implementation steps (TDD)

- **S0 pre-flight**: `gh pr view 2161` (W2 OPEN) + `gh issue view 2167` (#2167 OPEN → section MANDATORY; if #2167 shipped → session slice + section + bootstrap fixture rows optional, keep managed/status/revoked core) + `gh issue view 2167` RE-CHECKED at S6/merge (mid-flight merge demotes section). Baseline node --test 7/7. First-chore commit: research doc + THIS doc (+ optional docs/00_index.md row). Add "(declined at #2166 scoping — server endpoint unchanged)" one-liner to the research doc's architecture note.
- **S1 classification core rewrite (pure test-first, red→green)**: re-express T1-T7 (T2 SIGN FLIP: recovery+expires_at → managed/false under created_via!=='bootstrap' membership) + new: bootstrap-not-managed; NULL-legacy-durable-not-session-unless-active-explained; swept-bootstrap-under-filter-exactly-once; durable-revoked-inline; disabled-durable-status; expired-durable-never-live; held-durable-past-expiry-renders-expired-not-in-use; boundary now===expires_at.
- **S2 normalizeKeys + invariant (pure test-first)**: VMs derived ONLY via normalizeKeys(rawKeyRow) — hand-built VM fixtures prohibited. Invariant property test (manager: no in-use/expired managed VM without its pinned reason); member ctx all-can-false + reasons render; slice-exclusivity; in-use-durable-shows-explanation-and-no-actions. Export frozen REASONS/status constants.
- **S3 main.jsx VM-driven table (no new DOM)**: import swap, delete wrapper, VM once per render, row map over vm.managed (rename d.canRename L5485; status L5498 truthful; actions L5499-5519 d.canToggle/d.canRevoke; reason status-adjacent body-color). Update orphaned comment blocks L4014-4018 + L24-25. Empty-managed from slice counts. `npm run build` green.
- **S4 three-zone DOM**: session section (above rules), swept tbody + toggle (above rules), member banner, qualified empty states, .keys-table-wrap, index.css additive work. switchTeam reset.
- **S5 mixed e2e fixture — NEW module `tests/e2e/test_keys_table_mixed.py`** (own layered route handler; gate.py's 4 empty-keys tests untouched). Harness (scaffold: gate.py returning-segment L336-347 / test_bootstrap_cap_falls_back_to_recovery_mint L546-600; session_login_flow L112-130): mocked /v1/teams row with **role:'owner'** (no existing e2e mock supplies role → isOwnerAdmin false → vacuous otherwise; server parity: list_my_teams emits per-row role ~L6389); mint = bootstrap POST → 429 → recovery POST → 200 `tt_live_recovery_key_abcdef0123456789` (held key genuinely durable, created_via recovery — the faithful "dashboard's own durable credential" case; neutral token — never embed the banned user-facing substring "durable" in fixture plaintext); GET /v1/team/keys fixture rows (single team_id everywhere):
  1. provisioned, enabled → active, full actions — **positive control** (toggle aria-checked=true + trash visible)
  2. provisioned, disabled → Disabled, toggle aria-checked=false + rename/trash visible (supabase lane — comment: registry Cypher omits enabled)
  3. recovery, live prefix `tt_live_re` (slice(0,10) of held plaintext), enabled → In use + pinned reason, NO toggle/trash, rename visible
  4. recovery, non-live → active actionable (recovery-residual known limitation)
  5. NULL legacy → active actionable
  6. bootstrap, !revoked, future expiry → session section only (expiry-accurate)
  7. bootstrap, revoked AND expires_at PAST (sweep semantics; annotate: recovery-cap rotation can also revoke a not-yet-expired bootstrap — sweep is the dominant producer, not the only one) → swept bin ONLY (absent until toggle), prefix + date
  8. provisioned, revoked → managed INLINE Revoked + date (never in bin)
  9. provisioned, expires_at PAST (synthetic-so-far annotation — bootstrap is the only expires_at producer today) → Expired + copy, trash present, no toggle/rename
  10. bootstrap, expires_at past, !revoked → session section "expired {date}" only
  11. created_via absent (stale-cache shape), non-held → active (accepted limitation pinned in DOM)
  Assertions **zone-scoped by prefix**; EVERY fixture row carries a UNIQUE key_prefix (per-row prefix column in the fixture builder — any second row sharing `tt_live_re` would double-fire isActiveKey and demote the row-1 positive control; uniqueness is load-bearing); positive controls make live-row "no toggle/trash" satisfiable; body-level greps: no ephemeral/durable/session-credential user-facing strings. Optional variants: member role:'member' → banner + no actions + live-row reason still visible (member-scoped note: the in-use reason's owner imperative "revoke this one" is a dead end for members — banner adjacency above is load-bearing); keyless team → qualified empty.
- **S6 CI enforcement + regression + gate**: new CI job — spec explicitly (NO existing job does this; the welcome/legal e2e job ci.yml:357-398 is RUN_LEGAL_E2E on :8788 only — it builds no dist and runs no dashboard server): pip/playwright deps + **TWO wrangler boots** — `website/` on :8788 (auth) and `website/apps/dashboard` serving the committed `dist/` on :8790 (the mixed module inherits the gate/session_login_flow two-server harness and navigates via proxied prod-domains; without :8790 the module red-fails connection-refused) — then `RUN_DASHBOARD_E2E=1 pytest tests/e2e/test_keys_table_mixed.py -v`. NO npm build inside CI (committed-dist convention — a stale/missing dist commit fails the job red = tripwire). Run the job locally once before PR. Regression must NOT change: tests/test_hosted_api.py L905-949, test_cli_team_keys.py, test_session_key_http.py, test_auth_flip.py, existing 4 empty-keys e2e, onboarding/session_login_flow. Fallback if job flaky at execution: CI grep steps (no ephemeral/durable/session-credential user-facing; no `isSessionKey(` refs) + documented decision in PR — and taking the fallback REQUIRES an AC5 amendment (AC5 is categorical: the mixed e2e runs in CI via this job). npm run build → commit dist (12 tracked files — established convention). commit-workflow → PR.

## 4. Acceptance criteria (mapped + amended at Phase 7)
1. No temporary (bootstrap) credential appears in the MANAGED ROWS list (default view) — live bootstraps appear only in the separate non-actionable Temporary keys section. (Swept bootstrap rows live in the labeled second-tbody sub-zone behind the filter — a distinct bin, not a managed row, per AC3.)
2. Every managed row is either fully actionable with truthful status (active/disabled/revoked w/ date), or carries a visible plain-language explanation of the specific reason (in-use/expired pinned copy; member limits; revoked self-explained). [owner view; reason-invariant unit-enforced]
3. Swept temporary rows are behind the filter; durable revoked rows render inline truthfully with revocation date; nothing presents an expired credential as live.
4. sessionKey.js node --test suite green: re-expressed T1-T7 (T2 sign flip stated) + new named tests (S1/S2 list). Pure deterministic (clock injected).
5. Mixed durable+temporary+revoked e2e fixture renders the three-zone model — first mixed-table dashboard fixture — **and runs in CI** via the S6 job (two-server harness; taking the documented fallback requires an AC5 amendment). Owner-role fixture + unique per-row prefixes + positive controls + zone-scoped assertions.
6. A non-expert can explain every row — no internal-taxonomy jargon in user-facing strings (grep + e2e body assertions; "permanent/temporary" vocabulary).

## 5. Verification plan
node --test sessionKey.test.js green · docker-lane pytest hosted_api + cli_team_keys green (no server touch) · npm run dev clickthrough incl. 320-375px pass · RUN_DASHBOARD_E2E pytest (mixed module + gate + session_login_flow + onboarding) after dist rebuild · CI job green · grep no ephemeral/durable/session-credential in user-facing src + no isSessionKey( refs · dist rebuilt + committed.

## 6. Known limitations (state honestly in the PR + issue)
- Non-live system-minted recovery rows render indistinguishably from deliberate keys (active, actionable) — provenance needs server data → separate issue. User-visible gap this release.
- System-rotated durable rows (recovery-cap rotation) render "Revoked" + date WITHOUT rotation cause — the rotation banner discloses cause only in the rotating client; causeless inline revocation for other clients.
- Member banner says management "requires an owner or admin" while server DELETE is team-scoped (no role gate) — surface-scoped wording; DELETE role-guard parity is a separate issue that owns the banner text.
- Held-but-expired durable rows (synthetic today) render Expired with revoke-agnostic copy; trash hidden while held (never-revoke absolute) — future durable-expiry features must not pair this copy with a promised action.
- NULL/absent created_via treated durable (registry/selfhost legacy) — retired stale-cache fallback means an unheld stale bootstrap row could show as active; accepted, pinned by fixture row 11.
- Disabled+held state is UI-unreachable today (toggle hidden on live rows); if reached via API, status renders In use (expired > in-use > disabled ordering) with the in-use copy.
- Idle-tab expiry staleness (expiry text computed at render, no timer) — pre-existing app-wide pattern.
- Cross-team apiKey fallback in the wrapper expression inherits a previous team's key on a keyless new team — pre-existing semantics preserved.
- Key-login/anon modes safe by construction: session-gate redirect (~L4196-4228) prevents reaching the keys table; isActiveKey unchanged protects any live user key.

## 7. Files touched (complete)
sessionKey.js (rewrite; keep isActiveKey; remove isSessionKey) | sessionKey.test.js (re-express + new) | main.jsx (import L26; delete wrapper L4019-4023; rewrite keys render L5457-5523 VM-driven; session section; swept tbody + toggle; member banner; qualified empty; comment hygiene L24-25/L4014-4018; switchTeam reset; .keys-table-wrap) | index.css (additive; .session-section chrome; L141-147 comment) | tests/e2e/test_keys_table_mixed.py (NEW) | .github/workflows/ci.yml (NEW e2e job) | docs/research/2026-09-02-api-keys-table-session-credentials.md + docs/scoping/2026-09-02-2166-api-keys-table-scoping.md (S0 first-chore commit) | dist/ (rebuild + commit).
ZERO-CHANGE: server list_api_keys 4477-4559 · mintSessionKey 2116-2196 · revokeKey 3886-3930 + re-mint 3899-3925 · keyIdFromValue 4026-4036 · wizard ~3594-3700 + ~4291-4404 · loadAll 3012-3046 · toggle/rename 3824-3881 · CLI · keys STATE (L600) · gate.py existing tests.

## 8. Wiring
| Surface | Touch | Coverage |
|---|---|---|
| Data stores | none (no migration) | — |
| API | GET /v1/team/keys consumed unchanged (full rows) | contract tests stay green |
| Auth/roles | isOwnerAdmin → isManager ctx; member banner render-only | e2e owner + member variants |
| UI | keys render rewrite + section + bin + banner | unit + e2e + CI job |
| Build | dist rebuild + commit | established convention |
| Parallel work | #2167 OPEN (section mandatory; re-check at merge); W2 PR #2161 OPEN (wizard hard-blocked); #2083 siblings (#2111/2112/2114/2115 per-graph keys) — no mint/list semantics change here; #2154 OPEN (test CI churn — unrelated files) | checkout-guard + sequencing checks |
