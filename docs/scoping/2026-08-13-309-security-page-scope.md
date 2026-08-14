---
title: "Security page for tortoise.premiselabs.co — /security (Scope Phase 5: Solution Converge + Plan Draft)"
type: decisions
issue: "#309"
date: 2026-08-13
status: scoping-phase5
revision: 1 — initial
domain: capability
doc_status: draft
subjects.team: organisation-design-team
created: 2026-08-13
---

# Scope (Phase 5 convergence) — /security page (#309)

**Issue:** daniel-ospina/tortoise#309 (RE-SCOPED 2026-08-13: DPA portion DONE; remaining = /security page only)
**Inputs:** issue body · re-scope note · Phase 2 confirmed problem (confidence 90) · Phase 4 diverge output (approaches A/B/C) · research note 2026-08-13 (SOC 2 roadmap phrasing, 5 sources) · repo verification of every claim anchor (crypto.py, auth.py, fly.toml, audit_events.py, hosted_api.py, test_legal_pages.py, python-ci.yml, deploy-pages.yml, ci.yml, test_website_static.py)
**Method:** solution-converge (Phase 5) — pick approach on outcome quality, edge cases, failure-mode coverage, extensibility; NOT convenience. Produces CONVERGENCE + PLAN DRAFT.

---

## 0. Decision: CHOSEN APPROACH = C-core + selective A (hybrid)

**Core (enforcement):** Approach C — `website/security.json` as the single source of truth with **executable claim specs** (`check: {must_contain, must_not_contain}` per claim), a stdlib renderer (`website/security_render.py`) that generates `website/security.html`, committed-HTML↔JSON byte-parity enforced in CI and re-verified at deploy, and SOC 2 rendered from a status enum so it is **structurally incapable of overclaiming**.

**Hybrid addition (cheap, safe served-content membership):** `/security` ALSO joins `LEGAL_PAGES` in `tests/e2e/test_legal_pages.py`. Verified this session: `LEGAL_PAGES` membership adds EXACTLY TWO gates — `test_revision_history_present` (line 943) and `test_effective_date_format_present_once` (line 1118). The present-tense guard is **hardcoded to `/privacy`+`/tos`** (line 1095-ish iteration list) — membership does NOT auto-apply it. The "guard landmine" from Approach A's risk list is real but only triggers if security sentences enter `PINNED_CANONICAL` (which feeds `EXPECTED_GUARD_MATCHES`); this plan explicitly keeps security copy OUT of `PINNED_CANONICAL`. So the hybrid buys two real served-content gates at zero new risk.

**Why not pure A / pure B:** see §1 Rejected Alternatives.

### Why C-core wins on the binding constraint (claim accuracy over time)

The issue's re-scope says: *"the page must not overclaim — verify actual product state before listing claims"*. The failure mode that matters is **stale-pin-is-green**: code changes (crypto.py switches to AES-256-GCM, auth.py bumps iterations), the page keeps the old claim, and every test stays green because the pinned strings are unchanged. That is the exact failure the mandate forbids us to accept.

| Failure mode | A (legal clone, pins) | B (markdown + grep parity) | C (executable claim specs) |
|---|---|---|---|
| Code↔claim divergence | Human checklist; pins rot silently (stale pins are green) | Grep patterns live in a SEPARATE file from claim text → can drift independently; phrase-proximity, not execution | Claim text + its verification live in the SAME JSON object — schema requires a non-empty check per claim; drift is structurally impossible |
| SOC 2 overclaim | Pinned regex on served text (page-only, no code anchor) | Page-text absence regexes (weaker) | Rendered from `soc2.status` enum — the page literally cannot say "certified" unless the enum says it; plus negation-safe e2e |
| Credential per-surface (runtime vs dashboard) | Human discipline | Prose grep | Structured `credential_scoping` block with own must/must_not — runtime block forbids "passwords" |
| Vacuously-green tests | Possible (weak pins) | Empty grep = green | Schema forbids empty `must_contain`; a claim without a check fails validation |
| Extensibility (new sections: data residency, vulnerability handling) | New pins + new reviewer discipline per section | New greps in a growing separate file | One new JSON claim object with its check — the executor generalizes with zero test rewrite |

### Edge cases C handles that A/B do not

1. **Comment-reword false positives** (B's known risk): C uses the same phrase-matching mechanics but the plan mandates **implementation-anchor-first check design** — anchor on code symbols/constants (`pbkdf2_hmac`, `100_000`, `Fernet`, `TORTOISE_ENCRYPTION_KEY`, `INSERT INTO audit_events`) rather than prose; docstring prose is an anchor only where the docstring IS the contract (crypto.py's "AES-128-CBC + HMAC-SHA256" is the code's own statement — the page claims exactly what the code's header claims, so they cannot disagree).
2. **Absence claims** (TLS version pinning): fly.toml `must_not_contain: tls_options` verifies the page's scoped "TLS 1.2 and 1.3 per the Fly edge" claim stays honest — if someone later pins `tls_options.versions`, the claim check fails and forces re-scoping.
3. **A claim whose source moves** (crypto.py → crypto_utils.py): the check's `source` path list is part of the claim spec — the test fails loudly (file missing) instead of silently passing.
4. **Served page ≠ data**: byte-parity test (CI) + render-and-verify at deploy (deploy-pages staging step) — a stale committed HTML fails the deploy, not just CI.

### Repo precedent (not a new mini-contract)

`tests/test_website_static.py` already implements "data-object single source + parity" in production: `product/pricing.json` ↔ product.html `PRICING` mirror, stdlib-only, zero network, running in python-ci half-b. C's renderer is strictly stronger (no hand-maintained mirror at all — the HTML is generated). The check-spec vocabulary is new, but the *pattern* (data object + parity) is established repo practice.

---

## 1. Rejected alternatives

### Approach A — Legal-Family Clone (hand-authored HTML, full pin discipline)
**Why not:** The code↔claim link is human-maintained pins in an opt-in e2e suite. Stale pins are green — the exact failure the binding constraint forbids. The present-tense-guard interplay is a real landmine (only if security sentences enter `PINNED_CANONICAL`, but that is precisely how A would "extend" the pin machinery). Prose citations rot. Its ONE genuinely strong property — full legal-family served-content membership — is captured by the hybrid (LEGAL_PAGES membership is cheap and safe; the guard machinery is NOT auto-applied by membership, verified).
**When A WOULD have been better:** if the code surfaces were unknowable/unverifiable and the only truthful thing to check was served content against owner-approved copy (pure marketing claims with no code anchor). Or if the team refused any tooling. Neither holds here — every claim has a real code anchor, and the repo already runs the pricing.json parity pattern.

### Approach B — Canonical Markdown + Build Step + Repo-Local Code-Parity Tests
**Why not:** Better authoring UX, but the verification patterns live in a *separate* test file from the claim text — they can drift independently (the "two-copy problem" moves up a level instead of disappearing). Phrase-anchored greps false-positive on comments and cannot express absence/SOC-2 semantics well. The deployed HTML + build output need a determinism test anyway (B carries C's parity burden without C's structural coupling). SOC 2 coverage is weaker (page-text regexes only).
**When B WOULD have been better:** if the page were long-form prose (500+ words/section, heavy markdown formatting, tables everywhere) where JSON string escaping becomes genuinely painful and reviewers are humans reading markdown diffs. This page is 5 short technical sections + a status table — the authoring friction of JSON is modest.

---

## 2. Problem statement

Ship a static `/security` page on the tortoise.premiselabs.co host (served by the same Cloudflare Pages project; linked from the legal footer of product.html and the 4 footer pages) documenting the hosted service's encryption, key management, audit, and compliance posture — under a binding claim-accuracy constraint: every claim must be code-verifiable against the current repo, credential handling must be attributed per surface (Tortoise runtime vs Supabase dashboard), and SOC 2 must be stated as roadmap-only, never certification.

## 3. Proposed solution (concrete)

**Single source of truth:** `website/security.json` — `{meta, intro, sections: [{id, title, claims: [{text, source: [paths], check: {must_contain: [], must_not_contain: []}}]}], credential_scoping: {runtime: {text, must_not_contain}, dashboard: {text, must_contain}}, soc2: {status: "roadmap", milestones: [], control_areas: []}, revision_history: [...]}`.

**Renderer:** `website/security_render.py` (stdlib only, deterministic — no timestamps; all dates come from the JSON). Reuses the dpa.html template elements (below). Renders `website/security.html`, which is COMMITTED.

**Enforcement (three layers):**
1. **Repo-local executable checks** — `tests/test_website_security.py` (stdlib, zero network, fast): schema validation (every claim MUST have non-empty `must_contain`; `soc2.status` ∈ enum; sources must exist), then **executes each claim's check against the actual source files**, then byte-parity committed-HTML vs in-memory render, plus tag-balance and the negation-safe SOC 2/credential-scoping structural checks on the committed HTML.
2. **Served-content e2e** — `tests/e2e/test_legal_pages.py` extensions (all opt-in via `RUN_LEGAL_E2E=1`, run pre-merge in ci.yml `legal-e2e` against a local wrangler preview AND post-deploy in deploy-pages.yml `verify-legal` with `ALLOW_PROD=1`).
3. **Deploy-time render verification** — deploy-pages.yml staging step re-renders from JSON and byte-compares against the committed HTML; mismatch fails the deploy.

## 4. Implementation plan

### Step 1 — `website/security.json` (canonical data; author the claims with the exact wording below)

Claim wording directions (all five sections) — every claim includes the scoping language that prevents overclaiming:

1. **TLS in transit** — "All traffic to the hosted API is encrypted in transit. TLS 1.2 and TLS 1.3 are the supported protocol versions, terminated at the Fly.io edge." Scoped: NO "TLS 1.3 only", NO "perfect forward secrecy" (not verified), NO "HSTS" (not configured), no claim about the Cloudflare↔Fly leg. Check: fly.toml must_contain `handlers = ["tls", "http"]`; must_not_contain `tls_options` (absence of version pinning is what makes the 1.2+1.3 claim the Fly-edge default, honest).
2. **Encryption at rest** — "OAuth access tokens for connected integrations (e.g., GitHub) are encrypted at rest using Fernet, which combines AES-128-CBC encryption with HMAC-SHA256 authentication. The encryption key is provided via the TORTOISE_ENCRYPTION_KEY secret and is never stored in the database; a missing key fails closed." Scoped: tokens ONLY — NO "all data at rest", NO "the database is encrypted" (FalkorDB graph data is not claimed encrypted), NO "AES-256". Check: crypto.py must_contain `Fernet`, `TORTOISE_ENCRYPTION_KEY`, `AES-128-CBC`, `HMAC-SHA256`; hosted_api.py must_contain `github_token_enc`.
3. **API key hashing** — "API keys are never stored in plaintext. The runtime stores them as PBKDF2-HMAC-SHA256 hashes with a per-key random salt (100,000 iterations) and a server-side pepper; the control plane stores only a deterministic SHA-256 lookup digest (peppered)." Scoped: NO "reversible", NO unverifiable marketing ("not even we can recover" — the hash is one-way by construction; say "one-way" only). Check: auth.py must_contain `pbkdf2_hmac`, `"sha256"`, `100_000`, `token_bytes(32)`, `TORTOISE_SECRET_PEPPER`, `lookup_hash`.
4. **Audit logging** — "Control-plane events — tenant registration, API key creation and issuance, account-deletion requests, and authentication failures — are written to an append-only audit log with actor, operation, resource, IP address, and timestamp. Events are written to Postgres first, with an automatic JSONL fallback and replay-on-recovery, so events are not lost during database outages." Scoped: control-plane ONLY — NO "all API requests are logged"; NO retention-period claim (audit_events has no retention policy in code). Check: audit_events.py must_contain `INSERT INTO audit_events`, `audit_fallback.jsonl`, `ON CONFLICT`, `_replay_fallback`; hosted_api.py must_contain `api_key_create`, `api_key_mint`, `tenant_register`, `auth_failure:`.
5. **SOC 2 roadmap** — "Tortoise is not SOC 2 certified and does not claim compliance. We maintain a SOC 2 readiness roadmap across the control areas enterprise buyers typically review — access control, change management, vendor management, training, and incident response. Milestones: scoping → readiness assessment → external audit. No target audit date has been announced." Scoped per research note 2026-08-13: milestone-based, no hard date (a dated roadmap that slips is worse than no date), explicitly non-certified, no fabricated auditor. Check: structural — rendered from `soc2.status` enum; e2e + repo-local must_not_contain `certified`, `type i`, `type ii`, `attestation`, `\b20(2[5-9]|3[0-9])\b` audit-date patterns.

6. **Credential per-surface attribution** (scope §2 requirement) — a dedicated section, wording direction: "Where credentials live: the Tortoise runtime uses API keys (stored as one-way hashes) and OAuth tokens (encrypted at rest) — it has no password system. Dashboard accounts (app.premiselabs.co) use passwords handled by Supabase Auth with salted hashing, as described in the Privacy Policy §7." `credential_scoping.runtime.must_not_contain: ["passwords"]` (the runtime block must never attribute passwords to the runtime — this is what prevents deepening the privacy/dpa §7 confusion); `credential_scoping.dashboard.must_contain: ["Supabase", "salted hashing"]`.

**Intro scope sentence** (prevents overclaiming coverage): "This page describes the hosted Tortoise service at tortoise.premiselabs.co." — pinned by a repo-local check so the page never claims to cover self-hosted builds.

**meta:** last_updated, effective_date (yyyy-mm-dd), version. **revision_history:** one changelog entry with "Initial publication" (required by the LEGAL_PAGES revision-history gate).

### Step 2 — `website/security_render.py` (stdlib renderer, dpa.html template elements to reuse)
Self-contained inline CSS + dark theme (`--bg:#060b14` palette, serif body, mono meta-labels, ~720px max-width) · topbar (← Back to product + `tortoise.premiselabs.co` host) · `.doc-type` label ("Security") · h1 + lede · `.meta` table (Last updated / Effective date / Version) · callout (relationship to Privacy Policy §7 + DPA §7 — the per-surface note) · h2 sections · tables (subprocessor-style status table for SOC 2 milestones; revision-history table) · footer (Premise Labs · Tortoise · Back to product). Deterministic output — byte-stable across runs.

### Step 3 — `website/security.html` (generated, committed)
Run the renderer; commit the output. This is what Pages serves. (`website/**` trigger already covers it — no workflow change needed for deploy.)

### Step 4 — `tests/test_website_security.py` (NEW, repo-local, stdlib, zero network)
1. Schema validation of security.json (structure, non-empty must_contain per claim, soc2.status enum, sources exist on disk).
2. **Execute every claim check** against the actual source files (normalized, case-insensitive substring must_contain; must_not_contain).
3. Render parity: invoke the renderer in-memory/subprocess → byte-compare with committed security.html.
4. Tag-balance over committed security.html (mirror the e2e `_BalanceParser` pattern).
5. SOC 2 structural: committed HTML contains "roadmap" + "not certified"; must_not_contain overclaim regexes (from §4 step 1).
6. Credential-scoping structural: runtime block forbids "passwords"; dashboard block present with Supabase + salted hashing.
7. Scope-sentence present.

### Step 5 — `.github/workflows/python-ci.yml` (CRITICAL wiring)
The `test` job uses EXPLICIT half a/b allowlists (verified this session — `test_website_static` is listed in half b). **Add `test_website_security` to the half-b files list.** Without this, the executable claim checks never run in CI. (`uv-lock-check` dev-group collection will auto-collect it — stdlib only, no new deps.)

### Step 6 — `tests/e2e/test_legal_pages.py` (tuple extensions + new served-content test)
- `LEGAL_PAGES += ("/security",)` → auto-covers revision-history presence + exactly-once effective date (the two LEGAL_PAGES-iterating tests).
- `FOOTER_LINK_HREFS += ("/security",)` → the 5 footer pages must link it.
- `CRAWL_PAGES += ("/security",)` → final-200 + external-link crawl (page must contain NO external hrefs — internal relative links only, so nothing new joins the rate-limited external crawl).
- NEW `test_security_soc2_roadmap_negation_safe` (unconditional, mirrors the no-training guard pattern): GET /security → 200; contains "roadmap" + "not certified"; must_not_contain overclaim regexes; credential-scoping block present (both surfaces); the 5 section headings present.
- Add `"/security"` to the `test_mobile_render_no_horizontal_scroll` parametrize set (dpa-template is responsive at 480px).

### Step 7 — Footer links on all 5 pages
`product.html` (`nav.legal-footer` — add `<a href="/security">Security</a>`), `welcome.html` (footer), `signup.html` (`.footer` div), `signin.html` (`.footer` div), `self-hosted.html` (footer line). No middleware change needed — non-root paths pass through on both hosts (verified `_middleware.ts`).

### Step 8 — `.github/workflows/deploy-pages.yml` staging step
Add a step before `wrangler pages deploy`: `python3 website/security_render.py --check` (or render-to-temp + byte-compare with committed security.html); mismatch → fail the deploy. Trigger paths: NO change (artifacts live under `website/**`). The existing `verify-legal` post-deploy job picks up the extended e2e suite automatically.

### Step 9 — this document + deferred issue
`docs/scoping/2026-08-13-309-security-page-scope.md` (this file). File the deferred privacy/dpa wording issue (below).

## 5. Testing strategy

| Layer | What | Where it runs |
|---|---|---|
| Executable claim checks (schema + per-claim must/must_not + render parity + SOC 2 + credential scoping) | `tests/test_website_security.py` | python-ci `test` half-b (pre-merge, PR + main) |
| E2E route/footer/crawl/mobile/SOC 2 negation | `tests/e2e/test_legal_pages.py` extensions | ci.yml `legal-e2e` (pre-merge, local wrangler preview, `RUN_LEGAL_E2E=1`); deploy-pages `verify-legal` (post-deploy, `ALLOW_PROD=1`); local opt-in `RUN_LEGAL_E2E=1` |
| Deploy-time HTML↔JSON parity | render-and-verify staging step | deploy-pages (push to main) |

The SOC 2 negation-safe check is the highest-risk claim (page-only, no code anchor) — it gets BOTH the repo-local structural check (committed HTML) AND the served-content e2e check (served bytes), because served content is the only thing an enterprise buyer reads.

## 6. Verification plan (E2E-7-D Security Baseline — documentation portion)

The implementer proves the indicators:
1. **Route renders**: locally `cd website && npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1` + `RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 python -m pytest tests/e2e/test_legal_pages.py -k security -v` (pre-merge proof); ci.yml `legal-e2e` job green; post-deploy `verify-legal` green on https://premiselabs.co/security + https://tortoise.premiselabs.co/security (ALLOW_PROD=1).
2. **Footer link present**: `test_footer_legal_links_on_all_site_pages` (unconditional — all 4 FOOTER_PAGES) + `test_tortoise_host_footer_half` (gated) + `_footer_links_present` in the crawl tests — all green after the 5 footer edits.
3. **No broken links**: `test_crawl_all_pages_final_200` (includes /security) + external crawl (security page ships zero external hrefs).
4. **Claims accurate (the binding constraint)**: `python -m pytest tests/test_website_security.py -v` green — every claim executed against the current repo state; render parity byte-identical.
5. **SOC 2 never certified**: negation-safe checks green in both layers.
Runtime-security portion of E2E-7-D (auth, key storage) lives in `tests/test_hosted_auth.py` security tests — unchanged; this issue covers the documentation portion only.

## 7. Acceptance criteria

1. `/security` serves 200 on both premiselabs.co and tortoise.premiselabs.co (cross-host pass-through), rendering the dpa.html-style template.
2. The page contains all 5 sections (TLS in transit, encryption at rest, API key hashing, audit logging, SOC 2 roadmap) + the credential per-surface section + the scope sentence.
3. Every claim in security.json executes its check against the current source files and passes (`tests/test_website_security.py` green in CI half-b).
4. SOC 2 section says "not certified"/"roadmap" and the served page contains NO overclaim pattern (`certified`, `type i/ii`, `attestation`, fabricated audit date) — both repo-local and e2e checks green.
5. The runtime credential block does not attribute passwords to the runtime; the dashboard block attributes passwords to Supabase.
6. `website/security.html` is byte-identical to `security_render.py`'s output (CI + deploy-time verify).
7. `/security` is in LEGAL_PAGES, FOOTER_LINK_HREFS, CRAWL_PAGES, and the mobile-render set; all 5 footer pages link it; revision-history + exactly-once effective-date gates green.
8. No external hrefs on /security (external crawl unchanged in surface).
9. Deferred issue filed for the privacy.html §7 / dpa.html §7 "passwords" wording.

## 8. Runtime prerequisites

- **None** for serving: /security is a static asset on the existing Pages project; middleware passes non-root paths through unchanged (no middleware edit).
- **python-ci.yml**: add `test_website_security` to the half-b allowlist (mandatory — explicit-allowlist job).
- **deploy-pages.yml**: add the render-and-verify staging step; NO trigger-path change (`website/**` covers all three artifacts).
- **No new dependencies**: renderer is stdlib; tests are stdlib; e2e reuses existing playwright harness.
- **No secrets, no API changes, no auth changes, no DB changes.**

## 9. Deferred / separate issues

1. **privacy.html §7 / dpa.html §7 "passwords" wording (file separately — RECOMMENDED):** both pages say "Passwords are stored using salted hashing." as a generic security measure. This is accurate ONLY for the Supabase-backed dashboard accounts, not the Tortoise runtime (no password system). The /security page's per-surface attribution makes the ambiguity visible, so the legacy wording should be fixed: e.g. "Passwords for dashboard accounts are stored using salted hashing." Scope: 2-line edit + no e2e change (verified — no e2e test pins the salted-hashing line). File as its own low-complexity issue so #309's merge doesn't expand its diff.
2. **No other deferred items.** Future sections (data residency, vulnerability handling, incident-response commitments) plug into security.json as new claim objects — no test rewrite (the executor generalizes).

---

## 10. Wiring touch points (Phase 6 hard-gate inputs)

| Surface | Touch | Change |
|---|---|---|
| **Static assets** | `website/security.json` (NEW — canonical data) · `website/security_render.py` (NEW — renderer) · `website/security.html` (NEW — generated, committed) | Add |
| **UI / footers** | `website/product.html` (nav.legal-footer) · `website/welcome.html` · `website/signup.html` (.footer) · `website/signin.html` (.footer) · `website/self-hosted.html` | Add `<a href="/security">Security</a>` × 5 |
| **Edge / middleware** | `website/functions/_middleware.ts` | **NO change** (non-root pass-through verified) |
| **Repo-local tests** | `tests/test_website_security.py` (NEW) | Add |
| **CI (python-ci.yml)** | `test` job half-b files allowlist | **Add `test_website_security` (CRITICAL — explicit allowlist, otherwise checks never run)** |
| **CI (ci.yml)** | `legal-e2e` job | NO change (picks up tuple extensions automatically) |
| **Deploy (deploy-pages.yml)** | staging step (render+verify) | Add step; trigger paths unchanged |
| **E2E suite** | `tests/e2e/test_legal_pages.py` — LEGAL_PAGES / FOOTER_LINK_HREFS / CRAWL_PAGES / mobile set + new SOC 2 negation test | Edit tuples + add test |
| **Data stores / APIs / auth / secrets / external services** | None | NO change (static page; claims reference existing code, no runtime wiring) |
| **Docs** | `docs/scoping/2026-08-13-309-security-page-scope.md` (this file) | Add |
| **Deferred** | privacy.html §7 + dpa.html §7 wording | Separate issue (file at merge time) |
