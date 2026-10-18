# Capstone verification report — epic #1976 (agent-driven onboarding)

**Status: PASS for the epic's own surfaces, with five named deviations (four defects, one observation)** · **Commit verified on:** `180fe6fd3` (plus `fbc1d96d5` for the first batch — see lane conditions) · **Date:** 2026-10-18 · **Capstone:** #2008 · **Epic:** #1976 · **Surface map:** #1992

**Mode:** docker lane on a throwaway private FalkorDB (`tools/test_lane.py`, #5084) per batch, plus a **real browser walk** of the dashboard (Playwright/chromium against a local Pages preview built from the same commit, with the bound D1 database). No mocks were substituted for the layer under test: the integration layer talks to a real graph, and the browser layer runs the shipped wizard in a real Chromium against the shipped `dist`.

## Scope

The epic's 12 high-level E2E scenarios (`03-scope.md` §4) and the 17 integration surfaces from the test-design map (#1992), each verified through the layer the map assigns it — integration (docker lane), contract, skill lint, hosted-e2e (browser), or manual.

This epic's deploy surface is **web dashboard + hosted API + served agent skill**, so "walk it" means: for UI surfaces, drive the real wizard in a browser; for graph/API surfaces, exercise the real endpoints against a real graph; for the served skill, execute the installer and check the bytes it writes. Evidence is the command, its result, and — for the UI — a screenshot.

## Verdict summary

| # | High-level E2E (scope §4) | Surfaces (#1992) | Evidence | Result |
|---|---|---|---|---|
| 1 | One-sitting first-run (north star) | S1, S3, S7, S8, S17 | `test_dashboard_onboarding.py` (40 passed) + state/seed suites | ✅ PASS (browser walk, local preview) |
| 2 | Overview calm | S9 | browser walk + `test_onboarding_w4_settings.py` | ✅ PASS — 3 elements, zero toggles, 7th tab present |
| 3 | Org-name capture | S3 | `test_one_free_org_entitlement.py`, `test_free_team_entitlement.py` + wizard | ✅ PASS |
| 4 | Ontology-precise seed | S7, S8 | `test_onboarding_seed.py`, `test_onboarding_seed_endpoint.py` | ⚠️ PASS with a load-sensitive 504 (deviation D1) |
| 5 | Agent install across harnesses | S5, S6 | `test_installer_agents_block_3429.py`, `test_served_skills.py`, `test_served_skill_byte_parity.py`, `test_installer_preserves_foreign_skill_content.py` | ✅ PASS |
| 6 | Graph-held state resumption | S1, S2 | `test_onboarding_state*.py` (6 files) | ✅ PASS |
| 7 | Invite fusion (most failure-prone) | S12 | `test_invite_fusion_http.py`, `test_invites_http.py`, `test_invites_email_http.py` | ✅ PASS |
| 8 | Atomic new-user accept | S12 | same as E2E-7 (atomic path assertions) | ✅ PASS |
| 9 | Builder catalog | S13 | `test_capabilities_endpoint.py`, `test_capability_catalog.py` | ✅ PASS |
| 10 | Self-hosted onboarding | S15 | `test_onboarding_selfhost_w12.py` | ✅ PASS |
| 11 | Capture disclosure (self-use) | S11 | `test_onboarding_w6_capture_disclosure.py`, `test_capture_disclosure_template.py` | ⚠️ PASS with one flake (deviation D2) |
| 12 | Cross-W journey (W5-owned) | S17 | `test_dashboard_onboarding.py` full-journey specs + `test_decide.py` | ✅ PASS (browser walk) |

**Surfaces: 17 of 17 verified at their assigned layer in this run** (per-surface table below), **two of them with a load-sensitivity caveat**: surface 7 (seed) carries D1 — one of its 54 tests hits the 504 wait budget under load, and the rest pass; surface 11 (capture disclosure) carries D2 — one test failed in a batch and passed alone. Both caveats are in the rows, not just here.

Two things are **outside** the 17 and are not counted in that number, each named with its evidence: the **deployed-host** auth/identity journey (deviation D4 — those five specs expect the production auth origin, the local build redirects to its own, and this lane has no production session), and the **W11 funnel read** (indicator 5 / A0 gate data — hosted capture returns 402, so the events of the sessions that would populate the funnel are never posted). Surface 14 is the telemetry *machinery*, and that is verified; the funnel *read* is a different artifact and is not.

## Per-surface evidence

Commands below ran from `.worktrees/capstone-2008-1976` (clean checkout of the verified commit) unless marked, with
`L="$(.venv/bin/python tools/test_lane.py uri)"; TORTOISE_DB_URI=<lane> .venv/bin/python -m pytest …`.

| Surface | Assigned layer | Suite(s) | Result |
|---|---|---|---|
| 1 OnboardingState graph node | Integration (docker) | `test_onboarding_state.py`, `test_onboarding_state_cas.py`, `_split_init/_split_checkpoint/_split_patch/_split_projection` | ✅ 6 files green in a 201-test batch (1 unrelated failure, D1) |
| 2 Legacy store migration | Integration + regression | same split suite (migration/projection) | ✅ PASS |
| 3 Org-create transaction | Integration + hosted-e2e | `test_one_free_org_entitlement.py`, `test_free_team_entitlement.py`, wizard org-create specs | ✅ 170-test batch green |
| 4 Fork card | hosted-e2e + unit | fork specs inside `test_dashboard_onboarding.py` | ✅ PASS |
| 5 Universal setup command | Contract + integration + manual | installer suites + `test_served_skills.py` | ✅ PASS |
| 6 Agent install skill | Skill lint + integration | `test_served_skill_byte_parity.py`, `test_served_skills.py`, `test_installer_agents_block_3429.py` (42) | ✅ PASS — both SKILL copies byte-identical |
| 7 Seed — two Subjects | Integration + hosted-e2e + unit | `test_onboarding_seed.py`, `test_onboarding_seed_endpoint.py` | ⚠️ 53 passed, 1 failed (54 tests): `test_email_derived_person_requires_confirmation_no_writes` → 504 wait budget (D1) |
| 8 Decide nudge | Integration + walk-through | `test_decide.py` | ✅ PASS |
| 9 Overview calm | hosted-e2e + ux-verification | `test_onboarding_w4_settings.py` + screenshot | ✅ PASS |
| 10 Settings tab (owner) | hosted-e2e + integration | `test_onboarding_w4_settings.py` + screenshot (7th tab) | ✅ PASS |
| 11 Capture disclosure | Integration + hosted-e2e | `test_onboarding_w6_capture_disclosure.py`, `test_capture_disclosure_template.py` | ⚠️ 1 flake, passes alone (D2) |
| 12 Invite-accept fusion | Integration + contract + hosted-e2e | `test_invite_fusion_http.py`, `test_invites_http.py`, `test_invites_email_http.py` | ✅ 123-test batch green |
| 13 Builder catalog | Unit + integration + contract | `test_capabilities_endpoint.py`, `test_capability_catalog.py` | ✅ PASS |
| 14 Onboarding telemetry | Integration + regression | `test_onboarding_telemetry_w11.py` | ✅ PASS |
| 15 Self-hosted onboarding | Integration + manual | `test_onboarding_selfhost_w12.py` | ✅ PASS |
| 16 Wizard shrink + copy sweep | hosted-e2e + manual | `test_onboarding_variants.py` + screenshot ("Your Organization is live") | ✅ PASS |
| 17 Cross-W full journey | hosted-e2e + walk-through | `test_dashboard_onboarding.py` — **40 passed** | ✅ PASS |

**Totals, stated so the arithmetic can be checked:** **676 passed** across the batches below (200 + 121 + 123 + 170 + 40 + 22) and **1 skipped** (from batch F). Failures split by scope: **3 in-scope failures** — batch A's 1 (D1) and batch B's 2 (D2, D3), each named and reproduced — plus **batch F's 40 failures, which are D4 and outside the epic's 17 surfaces**, listed separately rather than netted into the in-scope count. Batch F also contributes the single skip, so it is counted for passes and skips but not folded into the in-scope failure tally.

```
A  200 passed, 1 failed   state ×6 + seed ×2                      (D1)
B  121 passed, 2 failed   w4_settings, variants, w6_capture,
                          capture_disclosure_template, w11,
                          selfhost_w12                            (D2, D3)
C  123 passed             invites ×3, capabilities, catalog, dist-guard
D  170 passed             entitlements, decide, installer ×2, served skills ×2
E   40 passed             dashboard onboarding e2e (browser)
F   22 passed, 40 failed  welcome / ship-test / gate / identity / login  (D4)
```

## The browser walk (E2E-1, E2E-12, surfaces 9, 10, 16)

Built from the verified commit and served the way the suite's own docstring prescribes:

```bash
cd website/apps/dashboard && npm ci && npm run build
npx wrangler@4 pages dev dist --port 8790 --inspector-port 9301 --d1 SESSIONS   # dashboard + D1 pages function
npx wrangler@4 pages dev .    --port 8788 --inspector-port 9302                 # site root (/auth)
RUN_DASHBOARD_E2E=1 pytest tests/e2e/test_dashboard_onboarding.py -q  →  40 passed
```

What the walk exercised, per spec: the 5-step wizard, fork card (once per org, never a billing gate), connect step for
owner/admin vs member, paste refusal paths (revoked/disabled/bootstrap rows never advance), keyless Claude
Desktop/Web paths, the done screen's incomplete-setup variant, and the mobile viewports (360/390/414) with no
horizontal overflow.

**Screenshots** (captured from the same preview, `/tmp/capstone-shots/`):

- `onboarding-desktop.png` (1440×900) and `onboarding-mobile.png` (390×844) — the continuation card on Overview:
  "Continue setting up <org>", tabs `Overview · API Keys · Graphs · Members · Billing · Settings`, a `Setup` button,
  a `Free tier` badge, and the copy sweep visible in the body ("Your **Organization** is live"). No feature toggles
  on Overview — the calm surface holds.
- `dashboard-desktop.png` — the **degraded** path: with a session but no reachable BFF, the dashboard renders
  "Could not load your organizations — try again" with a contact link. That is the honest failure the surface map's
  S9 row requires (never a blank page, never a false checklist).

## A0 gate data (indicator 5) — NOT captured, and the reason is a hard external limit

This is a separate artifact from surface 14. Surface 14 verifies that the events **fire, dedupe and reach the write
path**; this section is about **reading the resulting funnel**, which needs the hosted environment.

The indicator asks for walk-through friction observations **and a W11 funnel read** (`onboarding_seed_complete`,
`onboarding_decide_complete`, deduped). The event machinery is verified (surface 14, `test_onboarding_telemetry_w11.py`);
the **funnel read is not obtainable from this machine**, because hosted capture is over quota:

```
[tortoise-capture] Hosted capture FAILED (HTTP 402 — {"code":"quota_exceeded",
 "message":"Team points limit reached: 25000 in use + 138 estimated for this capture
 exceeds 25000. Upgrade your plan. ...","used":25000,"limit":25000})
```

Every session on this box, including the ones run for this capstone, therefore fails to post its events — so a
"first real cohort" funnel read would read zero for a reason that has nothing to do with the epic. Recording that as
the state of the gate rather than reporting a number the environment cannot produce.

**Friction observations** (from the walk itself, the part that is obtainable): zero "what do I do now" moments in the
40 walked specs — each step has a next action, the connect step states whether a key is needed ("Claude Desktop and
Claude Web connect without a key"), and the resumed-setup card names both available actions. The one friction the walk
surfaced is not in the product: the degraded card says "try again" without saying what failed (see D5).

## Deviations (all measured; none accepted silently)

| # | Finding | Evidence | Disposition |
|---|---|---|---|
| **D1** | `test_onboarding_seed_endpoint.py::TestSeedEndpoint::test_email_derived_person_requires_confirmation_no_writes` fails with **504 "wait budget exceeded"** under load — **5/5** reproductions, while the same test passed in 9.08 s on a quiet host | `--lf` ×5, 132–136 s each; load average 26→45 | **Recurrence recorded on #8000** (the same wait-budget class, different endpoint). Product or test contract is the open question there: the assertion cannot mean both "the seed is fast enough" and "the host is not busy" |
| **D2** | `test_onboarding_w6_capture_disclosure.py::TestDeleteDuringCapture::test_no_orphaned_receipt_under_concurrent_delete` failed in the 6-file batch and **passed alone** (133 s) | batch B + isolated re-run | Load-sensitive; re-run green. Not written off — D1 is the same mechanism |
| **D3** | (batch B) `test_onboarding_variants.py::test_installer_dist_is_a_build_artifact_not_a_committed_mirror` failed **only in the hub checkout** (which carries a stale ignored `dist/` from a local build); the same file passes in batch C, which runs from the clean capstone worktree at `180fe6fd3` | hub vs clean worktree, two different commits (D3 is a local artefact) | Environment artifact, not a main defect: `dist/` is gitignored and untracked, and the test's own name says dist is a build artifact. It nevertheless catches a real trap — a stale build will red this test for any lane that built once |
| **D4** | (batch F) `tests/e2e/{test_welcome_page,test_ship_test_onboarding,test_dashboard_gate,test_dashboard_identity,test_session_login_flow}.py` → 22 passed, 40 failed, 1 skipped | first failure: expected `https://tortoise.premiselabs.co/auth`, actual `http://127.0.0.1:8790/auth` | These expect the **deployed** auth origin; the local build redirects to its own origin. They are auth/identity/gate surfaces, **outside #1976's 17** — their hosted layer is the deployed app, which this lane cannot drive (no prod session). Recorded as unverified here rather than claimed |
| **D5** | The dashboard's degraded state says "Could not load your organizations — try again" without naming what failed | `dashboard-desktop.png` | Observation, not a defect: the copy is actionable and honest. Filed as an observation only — the surface map asks for a degraded state, not a diagnosis |

## Residuals carried forward (not this capstone's to close)

- **#1992** (the surface map itself) is still open — its artifact is delivered in the issue; closing it is bookkeeping.
- **#8000** carries the D1 recurrence; the 504-class decision (budget vs assertion) has no owner.
- **Hosted capture 402** blocks the W11 funnel read for **every** lane, not just this one — it is the reason indicator 5
  is unmeasurable here, and it will be the reason the next capstone cannot measure it either.

## Lane conditions (measurement caveats)

- The fleet ran other lanes throughout (load average 26–45 on 10 CPUs, 12-lane ceiling). Every wall time in this
  report is an upper bound, and D1/D2 are the two places where that bound crossed an assertion.
- Batches **A and B** ran from the **hub** checkout at `fbc1d96d5`; batches **C and D** ran from the clean capstone
  worktree at `180fe6fd3`, and the browser walk (E) and the peripheral e2e (F) ran there too. The two heads differ only
  by main's own merges in between (none touching onboarding code), and the file lists are recorded per batch so any
  batch can be re-derived at either head. D3 is the one finding whose *provenance* is that difference: it fails where a
  stale ignored `dist/` exists (the hub) and passes where it does not (the clean worktree).
- The browser walk used a **local Pages preview** (`wrangler pages dev`, D1 bound) — the same surface CI's
  `dashboard-e2e` leg drives, not the deployed host. D4 is where that distinction bites.

## Reproduction recipe (the setup cost that is not obvious)

1. `cd website/apps/dashboard && npm ci && npm run build` — the dist must be rebuilt; a stale ignored `dist/` reds D3.
2. Two `wrangler pages dev` instances **need distinct `--inspector-port`s** or the second dies with
   `Address already in use (127.0.0.1:9229)`; without `--d1 SESSIONS` the suite fails with
   `no D1 database sqlite appeared …`.
3. Do **not** pass `--persist-to` for the dashboard: the suite looks for
   `<dashboard>/.wrangler/state/v3/d1/**/*.sqlite`, so the preview must persist under the app directory.
4. A stray `/tmp/queue.py` shadows the stdlib `queue` module and makes any `sys.path.insert(0, ".")`-style script in
   `/tmp` fail with an unrelated `FileNotFoundError` — put ad-hoc scripts in their own directory.

## Verdict

**PASS for the epic's own surfaces.** All 17 integration surfaces are verified through their assigned layer at
`180fe6fd3` — two with a load-sensitivity caveat (surfaces 7 and 11) — all 12 high-level E2E
scenarios are walked with evidence, the browser walk of the real wizard is green
(40/40), and the two UI claims the epic rests on — a calm Overview and a Settings home — are confirmed on screen as
well as in assertions.

Of the three in-scope failures: **D2** was green only on re-run (a load-sensitive flake), **D1** never went green under
load (5/5 reproductions — it passes on a quiet host, which is exactly why the assertion is ambiguous and the finding is
recorded on #8000), and **D3** was an environment artifact (a stale ignored `dist/`) that passes in a clean checkout.
One *layer* — the deployed auth/identity journey, D4 — was not driven at all because it needs a production session. The W11 funnel read (indicator 5) is unmeasurable while hosted
capture returns 402.

**What this report does not do:** it does not mark the epic complete. Completion needs the owner's reading of D1 (is a
504 under load a product promise or a test contract?) and of the funnel gate — neither of which is a lane decision.
