---
title: "Scope — Issue #309: Hosted Security Page (post re-scope)"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Scope — Issue #309: Hosted Security Page (post re-scope)

**Issue:** daniel-ospina/tortoise#309 "Hosted: Security Page + DPA Template" (RE-SCOPED 2026-08-13 — DPA portion DONE)
**Date:** 2026-08-13
**Tier:** micro (upheld — no skill-domain/shared-code override paths: `skills/`, `src/lib/`, `src/hooks/`, `src/services/`, `supabase/migrations/`, `src/types/`, `operations/pi-config/` all absent from the touched surface; touched paths are `website/`, `tests/`, `.github/workflows/`)
**Skill:** issue-scoping v5.1.0 — full double diamond (Micro: 1 sub-agent per phase + single full-diamond-verify gate)
**Scope doc location:** `docs/scoping/2026-08-13-309-security-page-scoping.md`

---

## Confirmed Problem

> Ship a static `/security` page documenting the hosted Tortoise service's encryption, key management, and compliance posture — served on the **tortoise.premiselabs.co** host (the only legal footer lives there: `product.html` `.legal-footer` linking `/privacy /license /dpa`; `website/index.html` on premiselabs.co has **no footer**) and linked from that footer — under three binding constraints:

1. **Claim accuracy per section is code-verifiable.** Fernet **AES-128-CBC + HMAC-SHA256** encrypts **OAuth tokens only** (`tortoise/crypto.py:3`, key from `TORTOISE_ENCRYPTION_KEY` Fly secret, fails closed; `github_token_enc` column REVOKEd from anon/authenticated `hosted_api.py:4827`); hosted API TLS terminates at the **Fly.io edge**, protocol versions **TLS 1.2 + 1.3** (`fly.toml:16-19` — no `tls_options.versions` pinning; Fly proxy supports TLSv1.2+1.3 only; see TLS evidence note below); **PBKDF2-HMAC-SHA256** per-key 32-byte-salt API-key hashing + peppered SHA-256 lookup digest (`tortoise/auth.py:83-137`); **three-tier** Postgres/JSONL/replay **audit logging** for control-plane events (`hosted_api.py:501`, `audit_events.py`, `TORTOISE_AUDIT_DSN`); **SOC 2 = roadmap only, never certification** (no SOC 2 program exists in-repo).
2. **Credentials attributed per surface.** privacy.html §7 and dpa.html §7 "passwords… salted hashing" describe the **Supabase-backed dashboard** (app.premiselabs.co account passwords), NOT the Tortoise runtime — which has **no password system** (API keys + OAuth tokens only). The security page must attribute credentials per surface (runtime vs dashboard) and must not copy the legal pages' "passwords" phrasing into the runtime context.
3. **Tests must have real coverage.** `tests/e2e/test_legal_pages.py` tuples (`LEGAL_PAGES`, `FOOTER_LINK_HREFS`, `CRAWL_PAGES`) are enumerated — shipping /security without extending them yields zero coverage and a vacuously-green "no broken links". `FOOTER_LINK_HREFS` is checked against `FOOTER_PAGES` (welcome/signup/signin/self-hosted), so **all 5 footer pages** (product + those 4) must link `/security`.

**TLS evidence (recorded 2026-08-13):** `dig api.premiselabs.co` → `66.241.124.70`; `whois 66.241.124.70` → NetName **FLYIO**, OrgName **Fly.io, Inc.** (Fly-direct A record — NOT Cloudflare-proxied; contrast `tortoise.premiselabs.co` → `172.67.142.216`/`104.21.54.216` = Cloudflare). Fly docs: proxy supports TLSv1.2 + TLSv1.3 only, TLS terminated at the edge. Page copy scoped to "hosted API traffic (api.premiselabs.co)".

**Why this framing:** the original issue framed this as page-building; the binding constraints make it a **claim-integrity deliverable** — the page's whole value is believability, every indicator section is a verifiable claim, and the two most dangerous failure modes (stale-pin-is-green overclaims; cross-doc contradiction) are mechanically prevented by the chosen solution.

---

## Verification Gates

### full-diamond-verify (Micro — 1 verifier, all 4 phases)
- **Cycle 1:** Verifier returned **no P0**; 1×P1 (TLS "terminated at the Fly.io edge" not repo-verifiable), 1×P2 (SOC 2 research note not persisted), 3×P3, 2×P4. All four phases rated strong (diverge genuine, converge evidence-based + challenged original framing, solution approaches architecturally distinct, hybrid justified).
- **Controller action:** P1 resolved with live DNS/whois evidence (above); P2 → committed `docs/research/2026-08-13-309-soc2-roadmap-notes.md`; P3/P4 incorporated (unconditional product.html footer assertion; crypto anchor on `Fernet(` import/usage; deploy-parity kept as CI-bypass defense-in-depth with comment; mobile justification dropped; e2e docstring comment updates).
- **Cycle 2:** Verifier returned **no P0**; 2×P1 — (1) TLS evidence not yet present in the plan artifact (draft doc — final doc now embeds it), (2) **SOC 2 negation spec internally unsatisfiable**: `must_not_contain "certified"` collides with required "not SOC 2 certified" copy, and a page-wide audit-date regex collides with the mandatory `Effective date: YYYY-MM-DD` line.
- **Controller action (cycle 2):** Fixed both — negation vocabulary switched to positive overclaim phrases + audit-date regex **scoped to the SOC 2 section text only** (which naturally contains no dates — milestone-based, no target date). Re-dispatch issued against the final doc.
- **Cycle 3:** Verifier found 1×P1 residual — the negative-lookbehind `(?<!not )certified` (applied in cycle 2) is still **internally unsatisfiable**: it matches "soc 2 **certified**" because it only blocks direct `not ` adjacency (verifier executed it against the e2e `_clean()` normalization: `tortoise is **not soc 2 certified**.` → match at `certified`). All other fixes confirmed clean — TLS evidence live-verified (dig/whois FLYIO), P2 research note persisted, P3/P4 incorporated, LEGAL_PAGES membership safe.
- **Controller action (cycle 3):** Fixed — negation check switched to **strip-then-scan** (assert "not soc 2 certified" present → `re.sub("not soc 2 certified", "", body)` → assert "certified" absent in the remainder), verified satisfiable + discriminating in Python against the exact `_clean()` normalization pipeline (also catches "we are certified" / "certified by…" while the required negation passes). Research note updated.
- **Cycle 4:** ✅ **NO ISSUES FOUND — gate passes.** Strip-then-scan re-executed against the verbatim `_clean()` (`test_legal_pages.py:216`): "not soc 2 certified" survives contiguously (markdown asterisks flank, don't break), overclaim discrimination holds ("we are soc 2 certified", "soc 2 type i", "certified by…", "attestation", "audited" all fail correctly), and the dpa template contributes zero "certified" occurrences so the full-body remainder assertion is satisfiable. "audit" (noun, roadmap milestones) does not collide with "audited". All prior fixes re-spot-checked clean. Full-diamond-verify at convergence after 4 cycles — no further re-dispatch needed.

---

## Plan

### Problem statement
Ship a static `/security` page on tortoise.premiselabs.co (linked from the legal footer on all 5 footer pages) documenting encryption, key management, audit, and compliance posture, under a binding claim-accuracy constraint: every claim code-verifiable, credentials attributed per surface (Tortoise runtime vs Supabase dashboard), SOC 2 roadmap-only.

### Proposed solution — Hybrid: C-core + selective A
`website/security.json` (single source of truth: per-claim **executable check specs** `check: {must_contain, must_not_contain}` + `source`/`notes`) → `website/security_render.py` (stdlib, deterministic, dpa.html template) → committed `website/security.html`. Enforcement in three layers:
1. **Repo-local `tests/test_website_security.py`** (python-ci half-b): JSON schema (non-empty `must_contain` mandatory), **executes every claim's check spec against the actual source files**, byte-parity (renderer output == committed HTML), tag balance, SOC 2 + credential structural checks, unconditional product.html footer assertion.
2. **E2E suite extensions** (`tests/e2e/test_legal_pages.py`): `LEGAL_PAGES` + `FOOTER_LINK_HREFS` + `CRAWL_PAGES` += `/security` (inherits revision-history + exactly-once effective-date gates — both satisfied by the dpa template), mobile-render set += `/security`, one new unconditional `test_security_soc2_roadmap_negation_safe`.
3. **Deploy-time render-and-verify** (deploy-pages.yml staging step: re-render from JSON, byte-compare vs committed HTML, mismatch fails deploy — intentional CI-bypass defense-in-depth for a compliance surface).

**Why hybrid over pure approaches:** the binding constraint is claim accuracy; only C's executable claim specs structurally prevent stale-pin-is-green (claim text and its verification live in the same JSON object; schema forbids a claim without a check; SOC 2 rendered from a `status` enum). A's one genuinely strong property — legal-family served-content membership — is added because it is provably cheap: `LEGAL_PAGES` is iterated by exactly 2 tests (revision-history `:943`, effective-date `:1118`), both satisfied by the dpa template; the present-tense guard is hardcoded to `/privacy`+`/tos` via `PINNED_CANONICAL` values (zero security keys) — membership does **not** auto-apply it. Security copy stays OUT of `PINNED_CANONICAL`.

### Implementation plan (ordered steps)

1. **`website/security.json`** — claims with wording direction (all copy scoped to the hosted service; scope sentence: "This page describes the hosted Tortoise service (tortoise.premiselabs.co / api.premiselabs.co). Self-hosted deployments are not covered."):
   - **TLS in transit:** "TLS 1.2 and TLS 1.3 are the supported protocol versions for hosted API traffic, terminated at the Fly.io edge." Spec: fly.toml must_contain `handlers = ["tls", "http"]` (443) + must_not_contain `tls_options` (absence-of-pinning honesty). `source/notes`: dig/whois evidence (66.241.124.70 = FLYIO, findings-date 2026-08-13) + fly.io/docs/networking/tls. No HSTS/PFS claims (unverified surface).
   - **Encryption at rest:** "OAuth tokens (e.g., GitHub) are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256). The key comes from `TORTOISE_ENCRYPTION_KEY`, is never stored in the database, and the system fails closed if it is missing. Encrypted token columns are revoked from anonymous and authenticated roles." Spec: crypto.py must_contain `Fernet(` (import/usage — **primary anchor**, breaks on cipher swap) + `TORTOISE_ENCRYPTION_KEY`; docstring anchors `AES-128-CBC`/`HMAC-SHA256` **secondary** (drift-catch); hosted_api.py must_contain `github_token_enc`. Explicitly NOT "all data at rest is encrypted" (FalkorDB graph data is not Tortoise-encrypted).
   - **API key hashing:** "API keys are stored only as one-way hashes — PBKDF2-HMAC-SHA256 with a per-key 32-byte random salt and 100,000 iterations, plus a server-side pepper. A separate peppered SHA-256 digest enables constant-time lookup; the plaintext key exists only at creation time." Spec: auth.py must_contain `pbkdf2_hmac`, `"sha256"`, `100_000`, `token_bytes(32)`, `TORTOISE_SECRET_PEPPER`, `lookup_hash`. No "reversible storage" language.
   - **Audit logging:** "Control-plane operations — account registration, API key creation and issuance, deletion requests, and authentication failures — are logged with actor, operation, resource, IP, and timestamp. Events are written to Postgres first, with a local JSONL fallback and automatic replay on recovery." Spec: audit_events.py must_contain `INSERT INTO audit_events`, `audit_fallback.jsonl`, `ON CONFLICT`, `_replay_fallback`; hosted_api.py must_contain `tenant_register`, `api_key_create`, `api_key_mint`, `auth_failure`. NOT "all requests logged"; no retention-period claim.
   - **SOC 2 roadmap:** "Tortoise is **not SOC 2 certified**. Compliance is a roadmap item: we are building toward a readiness posture across access control, change management, vendor management, training, and incident response — scoping → readiness → audit." Rendered from `soc2.status` enum ("roadmap"). Spec: must_not_contain positive overclaim phrases `["we are soc 2 certified", "soc 2 type i", "soc 2 type ii", "attestation", "audited"]`; the negation-safe layer (repo-local AND e2e) uses **strip-then-scan** — assert `"not soc 2 certified"` present in the normalized body, `re.sub("not soc 2 certified", "", body)` → assert `"certified"` absent in the remainder (satisfiable — verified in Python against the e2e `_clean()` normalization; catches "we are certified" / "certified by…"). Do NOT use bare-substring or lookbehind regexes on "certified" (both are unsatisfiable against the required copy). **No target date, no auditor name** (research note: dated roadmap that slips is worse than none). Audit-date regex `\b20(2[5-9]|3[0-9])\b` **scoped to the SOC 2 section text only** (naturally date-free) — never page-wide, so it cannot collide with the mandatory `Effective date: YYYY-MM-DD` (revision-history/effective-date gates).
   - **Credential scoping (per-surface):** runtime block: "The Tortoise runtime stores **API keys and OAuth tokens** — it has **no password system**." (must_not_contain `passwords` in the runtime block); dashboard block: "Account passwords for the dashboard (app.premiselabs.co) are stored using salted hashing by Supabase Auth." (must_contain `Supabase`, `salted hashing`). This resolves the privacy/dpa §7 cross-doc tension by attribution, not contradiction.
2. **`website/security_render.py`** — stdlib, deterministic, dpa.html template reuse: `--bg:#060b14`/`--accent:#06b6d4` palette, serif body + mono meta-labels, ~720px wrap, topbar ("← Back to product" + host), `.doc-type`/h1/lede, `.meta` table (last updated / effective date / version), callout, h2 sections + status table, revision-history table ("initial publication" entry), footer. **Zero external hrefs** (external crawl surface unchanged; footer link to premiselabs.co excluded by `_PROJECT_OWNED_HOSTS`).
3. **`website/security.html`** — generated, committed.
4. **`tests/test_website_security.py`** (new, repo-local stdlib, test_website_static.py pattern):
   - JSON schema: every claim has non-empty `must_contain` + a `source`; `soc2.status` ∈ {roadmap}; `credential_scoping` block has its own must/must_not.
   - **Execute every claim's check spec** against the actual source files (crypto.py, auth.py, fly.toml, audit_events.py, hosted_api.py).
   - Byte-parity: rerun renderer → byte-diff vs committed `website/security.html`.
   - Tag balance (stdlib HTMLParser), SOC 2 structural check, credential per-surface check, scope sentence present.
   - **Unconditional product.html footer assertion** (raw read, no network): `website/product.html` `nav.footer-links` contains `href="/security"` — mirrors the e2e `_footer_links_present` but is never DNS/TORTISE_HOST-gated (the gated e2e tortoise-host half alone would ship green on stale DNS).
5. **`.github/workflows/python-ci.yml`** — add `test_website_security` to the **half-b explicit allowlist** (line ~159; `test_website_static` precedent) — without this the new suite never runs in CI.
6. **`tests/e2e/test_legal_pages.py`** — `LEGAL_PAGES`, `FOOTER_LINK_HREFS`, `CRAWL_PAGES` += `/security`; mobile-render param set += `/security` (empirically verified by the test — no "responsive at 480px" assumption); new unconditional `test_security_soc2_roadmap_negation_safe` (200; "not soc 2 certified" present; overclaim phrases absent via **strip-then-scan** — strip the exact phrase, assert `certified` absent in remainder; both credential surfaces present; 5 section headings); update stale docstring comments ("the four legal hrefs" / "all four ship" → the new set).
7. **Footer links ×5:** `product.html` (`nav.footer-links`), `welcome.html`, `signup.html`, `signin.html`, `self-hosted.html` — add `<a href="/security">Security</a>` next to the legal links.
8. **`.github/workflows/deploy-pages.yml`** — staging step: re-render from JSON + byte-compare vs committed HTML (mismatch fails deploy; comment documents this as a CI-bypass guard). **Trigger paths unchanged** (`website/**` covers security.json/security_render.py/security.html).
9. **Persistence:** this scope doc + `docs/research/2026-08-13-309-soc2-roadmap-notes.md` + deferred-issue filing (below).

### Testing strategy
- **Pre-merge, repo-local (python-ci half-b):** `test_website_security.py` — executable claim specs, byte-parity, tag balance, SOC 2/credential structure, product.html footer link.
- **Pre-merge, e2e (ci.yml legal-e2e vs local wrangler preview; opt-in `RUN_LEGAL_E2E=1`):** tuple extensions pick up /security automatically; new SOC 2 negation-safe test; revision-history + effective-date gates green for /security (LEGAL_PAGES membership).
- **Post-deploy (deploy-pages.yml `verify-legal` job, `ALLOW_PROD=1`):** served bytes verified against production — the only thing a buyer reads.
- **Deploy-time:** render-and-byte-compare staging step (CI-bypass defense).

### Verification plan (mapped to E2E-7-D Security Baseline, documentation portion)
| Indicator (issue O/I/T) | How proven |
|---|---|
| /security route renders with the 5 sections | `wrangler pages dev` + repo-local suite; e2e 200 check; post-deploy verify-legal |
| Footer link present | unconditional repo-local product.html assertion + e2e footer checks (5 pages) |
| No broken links | e2e crawl (`CRAWL_PAGES` incl. /security, final-200) |
| Claims accurate (no overclaim) | executable claim specs vs source files + SOC 2 negation-safe (both layers) |
| SOC 2 roadmap-only | rendered from enum + strip-then-scan negation check + scoped audit-date regex |
| Runtime never attributes passwords | credential-scoping must_not_contain `passwords` in runtime block |

### Runtime prerequisites
- None for serving (no middleware edit — non-root pass-through verified in `functions/_middleware.ts`; route serves on both hosts, canonical tortoise.premiselabs.co).
- Mandatory: python-ci half-b allowlist entry; deploy-pages staging step; e2e tuple edits.
- No new third-party deps, no secrets, no API/DB/auth changes.

### Deferred / separate issues
- **File separately:** privacy.html §7 + dpa.html §7 "passwords… salted hashing" → precision edit attributing account passwords to the dashboard (Supabase Auth). 2-line doc edit; nothing pins that line in e2e (verified) — filing separately keeps #309's diff small.

### Acceptance criteria
1. `/security` returns 200 on both hosts (tortoise canonical), dpa-template styling.
2. Page renders the 5 sections (TLS, encryption at rest, API key hashing, audit logging, SOC 2 roadmap) + credential-scoping block + scope sentence.
3. Every claim's check spec executes green in CI half-b against the actual source files.
4. SOC 2 section states "not certified" + roadmap; zero overclaim patterns in served bytes (both layers).
5. Runtime credential block contains no `passwords` attribution; dashboard block names Supabase salted hashing.
6. Committed `security.html` is byte-identical to the renderer output (CI parity + deploy staging step).
7. E2E tuples extended; all 5 footer pages link /security; revision-history + effective-date gates green for /security.
8. Page ships zero external hrefs.
9. Deferred issue (privacy/dpa §7 wording) filed and linked.

---

## Clarifications
*(No clarifying questions needed — issue-scoping Phase 0.5 clarifying-questions invocation skipped: tier = micro, per clarifying-questions skill skip conditions. All open questions were resolved by research or evidence.)*

## External Research (Phase 1.5 artifact)

### Axis Research
> **Trigger assessment:** axes low (Architecture=low, UX=low, Ontology=low); no third-party deps; no novel pattern — in-repo precedents: `website/dpa.html` (static legal-page template), `tortoise/onboarding/stage_variants.py` + `tests/test_website_static.py` (canonical-source → rendered artifact + repo-local parity tests), `tests/e2e/test_legal_pages.py` (legal-page e2e machinery). External research fired on ONE demonstrated gap: SOC 2 roadmap phrasing (highest-risk claim, claim-accuracy binding constraint).

- **SOC 2 roadmap communication** (1 query, findings-date 2026-08-13, 4 sources — promise.legal / soc2auditors.org / lorikeetsecurity.com / zipsec.com): roadmap language must be separated from certification language; milestone-based roadmap (scoping → readiness → audit) with control-area milestones beats a hard date that can slip; minimum viable SOC 2 scope = access control, change management, vendor management, training, incident response; enterprise buyers expect references to information security/access control/acceptable use/incident response/change management policies. Persisted: `docs/research/2026-08-13-309-soc2-roadmap-notes.md`.
- **TLS version claim** (diverge-phase external check, findings-date 2026-08-13): Fly proxy supports TLSv1.2 + TLSv1.3 only (fly.io/docs/networking/tls); `fly.toml` pins no `tls_options.versions` → claim as "TLS 1.2 + 1.3 supported", never "TLS 1.3 only".

### Integration Docs
- **No new third-party dependencies introduced.** Renderer = stdlib only. All external surfaces (Fly, Cloudflare Pages, Supabase Auth) are existing, already-deployed infrastructure referenced only descriptively by the page.

## Rejected Alternatives
- **A — Legal-Family Clone (hand-authored HTML, full pins, reviewer checklist):** the code↔claim link is human-maintained pins in an opt-in e2e suite; stale pins are green — the exact failure the binding constraint forbids. The present-tense-guard interplay is a real landmine if security copy enters `PINNED_CANONICAL`. *Would have been better if* claims had no code anchors (pure marketing copy) or the team refused all tooling.
- **B — Canonical Markdown + Build + Grep-Parity:** best authoring UX, but grep patterns live in a separate file from the claim text → they drift independently (the two-copy problem moves up a level), and absence claims (TLS pinning) and SOC 2 status check weakly. *Would have been better if* the page were long-form prose (500+ words/section) where JSON escaping hurts and markdown diffs matter — this page is 5 short technical sections.
- **Pure C (no LEGAL_PAGES membership):** would avoid the revision-history/effective-date ceremony but give up two served-content gates that are provably free (verified: 2 iterations, dpa template satisfies both, guard not auto-applied). *Rejected in favor of hybrid* — membership is the quality-over-convenience choice.
- **Trust-center expansion (pentest reports, named security contact, subprocessors, VSQ bank):** explicitly out of scope — the issue is "complexity: low, static page"; these are named non-goals so readers aren't misled into assuming them.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| `website/security.json` | Static asset (new, single source of truth) | Plan Step 1 | ✅ |
| `website/security_render.py` | Static asset (new, stdlib generator) | Plan Step 2 | ✅ |
| `website/security.html` | Static asset (new, generated + committed) | Plan Step 3 | ✅ |
| `product.html` footer (`nav.footer-links`) | UI component | Step 7 + unconditional repo-local assertion (Step 4) | ✅ |
| `welcome.html`, `signup.html`, `signin.html`, `self-hosted.html` footers | UI component (FOOTER_PAGES) | Step 7 + e2e `_footer_links_present` | ✅ |
| `functions/_middleware.ts` | Edge/middleware | **No change required** (non-root pass-through verified) | ✅ |
| `tests/test_website_security.py` | Repo-local test suite (new) | Step 4 | ✅ |
| `.github/workflows/python-ci.yml` half-b allowlist | CI | Step 5 (mandatory — explicit allowlist) | ✅ |
| `.github/workflows/ci.yml` legal-e2e | CI | No change (auto-picks tuple edits) | ✅ |
| `.github/workflows/deploy-pages.yml` | Deploy | Step 8 (staging render+verify; triggers unchanged) | ✅ |
| `tests/e2e/test_legal_pages.py` | E2E suite | Step 6 (3 tuples + mobile set + 1 new test + docstring) | ✅ |
| Data stores / APIs / auth / secrets / external services | — | **None** (static documentation page) | ✅ |
| Cloudflare zone min-TLS setting | External (unowned) | Documented out-of-scope; page copy avoids HSTS/min-version claims | ✅ |
| Deferred: privacy.html §7 + dpa.html §7 wording | Docs | Filed separately (not absorbed) | ✅ |

**HARD-GATE: all touch points covered — no unresolved gaps.**

## Review Cycle Log

```
### full-diamond-verify — Cycle 1
- Verifier: P0=0, P1=1 (TLS topology not repo-verifiable), P2=1 (SOC 2 research note not persisted), P3=3, P4=2
- Controller action: Fixed P1 (DNS/whois evidence recorded, copy host-scoped), fixed P2 (committed research note),
  incorporated P3/P4 (product.html unconditional assertion; crypto primary anchors; deploy-parity comment; mobile
  justification dropped; e2e docstring updates)
- Re-dispatching...

### full-diamond-verify — Cycle 2
- Verifier: P0=0, P1=2 — P1-a: TLS evidence absent from plan artifact (draft doc, now embedded in final),
  P1-b: SOC 2 negation spec unsatisfiable (must_not_contain "certified" collides with required "not certified";
  page-wide audit-date regex collides with mandatory effective date)
- Controller action: Fixed P1-a (evidence embedded in Confirmed Problem + claim spec source/notes),
  Fixed P1-b (positive-phrase overclaim vocabulary + negative-lookbehind regex + audit-date regex scoped to
  SOC 2 section only), incorporated P2 (research-note path referenced), P3 (product.html assertion added to Step 4),
  P4 (mobile parenthetical dropped; docstring step added)
- Re-dispatching against the final scope doc...

### full-diamond-verify — Cycle 3
- Verifier: P0=0, P1=1 — residual P1: negative-lookbehind `(?<!not )certified` still unsatisfiable
  (matches "soc 2 certified" — only direct `not ` adjacency blocked; executed against `_clean()` normalization).
  TLS P1 confirmed FIXED (live dig/whois), P2/P3/P4 confirmed incorporated, LEGAL_PAGES membership re-verified safe.
- Controller action: Fixed — strip-then-scan negation check (assert "not soc 2 certified" present; strip it;
  assert "certified" absent in remainder), verified satisfiable + discriminating in Python; research note updated
- Re-dispatching...

### full-diamond-verify — Cycle 4
- Verifier: ✅ **NO ISSUES FOUND — gate passes.** Strip-then-scan verified satisfiable + discriminating against the verbatim `_clean()` normalization; dpa template contributes zero "certified" occurrences; no new issues introduced. Full-diamond-verify converged after 4 cycles.
```

## Complexity

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | low | Static asset + stdlib generator; no runtime changes |
| UX | low | Static informational page, dpa template; no interaction |
| Ontology | low | No new entities/statuses; page only describes existing state |
| Testing | medium | 3 new enforcement layers + e2e tuple extensions + CI allowlist — the real effort of this issue |
| Content / claim accuracy | medium | 5 claim sections must be code-accurate; per-surface credential attribution; SOC 2 roadmap phrasing |
| Security | low | Documentation only; no security controls changed (page describes existing ones) |
| Operations | low | deploy-pages staging step + python-ci allowlist entry; no runtime ops change |

**Overall tier: micro** (upheld). The issue's complexity claim of "low" holds for authoring; the binding constraint (claim accuracy) and its test wiring are where the real effort concentrates — acknowledged in the plan, not inflated.
