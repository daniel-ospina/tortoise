---
title: "Capstone verification report — epic #1976 (agent-driven onboarding)"
type: synthesis
domain: capability
doc_status: final
subjects.team: epistemic-team
created: 2026-10-18
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Capstone verification report — epic #1976 (agent-driven onboarding)

**Status: NOT VERIFIED AS A WHOLE — 15 of the 17 surfaces are green, 2 are not (7 and 12), and 6 carry an assigned leg not fully passed or executed (seven legs — see below); with every *capstone* indicator graded partial rather than rounded up, the proud-test story review graded partial, and six findings (D1 an unresolved load-bound 504, filed as #9001, D2 a flake, D3 a proven environment artifact, D4 five e2e modules, whose 40 failures are **not individually attributable** from the batch record (see D4), while surface 12's hosted-e2e leg is measured failing in its own run (one cause: D4's redirect), the five D4 modules carrying three different causes — 3 CI-stale, 1 CI-wired/un-derived, 1 unrenderable from this Pages root — D5 an observation, D6 a filed coverage gap #8998)** · **Commit verified on:** `180fe6fd3` (batches A and B ran at the anchor `fbc1d96d5` — see lane conditions) · **Date:** 2026-10-18 · **Capstone:** #2008 · **Epic:** #1976 · **Surface map:** #1992

**Mode:** docker lane on a throwaway private FalkorDB (`tools/test_lane.py`, #5084) per batch, plus a **real browser walk** of the dashboard (Playwright/chromium against a local Pages preview built from the same commit, with the Cloudflare D1 database bound). No mocks were substituted for the layer under test: the integration layer talks to a real graph, and the browser layer runs the shipped wizard in a real Chromium against the shipped `dist`.

## Scope

The epic's 12 high-level E2E scenarios (`03-scope.md` §4) and the 17 integration surfaces from the test-design map (#1992), each verified through the layer the map assigns it — integration (docker lane), contract, skill lint, hosted-e2e (browser), or manual.

This epic's deploy surface is **web dashboard + hosted API + served agent skill**, so "walk it" means: for UI surfaces, drive the real wizard in a browser; for graph/API surfaces, exercise the real endpoints against a real graph; for the served skill, execute the installer and check the bytes it writes. Evidence is the command, its result, and — for the UI — a screenshot.

## Verdict summary

| # | High-level E2E (scope §4) | Surfaces (verbatim from `04-test-design.md`, the map #1992 declares canonical) | Evidence | Result |
|---|---|---|---|---|
| 1 | One-sitting first-run (north star) | 1, 3, 4, 5, 7, 8, 17 | `test_dashboard_onboarding.py` (40 passed) + state/seed suites | ⚠️ PASS at the browser walk (40/40); the rows it maps carry legs not fully run — surface 7's seed (53/54, D1) and the hosted-e2e of 7, the walk-through of 8 and the manual leg of 5 |
| 2 | Overview calm | 9, 16 | browser walk + `website/apps/dashboard/src/overviewSettings.test.js` (8/8) + `test_onboarding_w4_settings.py` (server leg) | ✅ PASS — 3 elements, zero toggles, Settings present in the rendered nav |
| 3 | Org-name capture | 3, 16 | `test_one_free_org_entitlement.py`, `test_free_team_entitlement.py` + wizard | ✅ PASS |
| 4 | Ontology-precise seed | 7, 15 | `test_onboarding_seed.py`, `test_onboarding_seed_endpoint.py` | ⚠️ **PARTIAL** — 53 of 54 pass; one test has never passed under load (D1). Its graph-state assertions do hold on a quiet host |
| 5 | Agent install across harnesses | 5, 6 | `test_installer_agents_block_3429.py`, `test_served_skills.py`, `test_served_skill_byte_parity.py`, `test_installer_preserves_foreign_skill_content.py` | ⚠️ PASS at the automated layer — the six-harness manual walk (surface 5's manual leg) was not run |
| 6 | Graph-held state resumption | 1, 2, 9, 10 | `test_onboarding_state*.py` (6 files) for the graph state; surfaces 9/10's render is E2E-2's browser walk + `overviewSettings.test.js` (8/8, row 2) | ✅ PASS at the automated layer — the graph-state suites for 1/2, E2E-2's render evidence for 9/10; no browser leg was re-run for this row |
| 7 | Invite fusion (most failure-prone) | 12 | `test_invite_fusion_http.py`, `test_invites_http.py`, `test_invites_email_http.py` | ⚠️ PASS at the integration + contract layers; surface 12's **hosted-e2e** leg fails (see the overlap paragraph and the surface table) |
| 8 | Atomic new-user accept | 1, 12 | same as E2E-7 (atomic path assertions) | ⚠️ PASS at the integration + contract layers; the same surface 12 hosted-e2e leg applies |
| 9 | Builder catalog | 13 | `test_capabilities_endpoint.py`, `test_capability_catalog.py` | ✅ PASS |
| 10 | Self-hosted onboarding | 15 | `test_onboarding_selfhost_w12.py` | ⚠️ PASS at the automated layer — a hand-driven self-hosted install (surface 15's manual leg) was not run |
| 11 | Capture disclosure (self-use) | 11 | `test_onboarding_w6_capture_disclosure.py`, `test_capture_disclosure_template.py` | ⚠️ PASS with one flake (deviation D2) |
| 12 | Cross-W journey (W5-owned) | 1-17 (all) | `test_dashboard_onboarding.py` full-journey specs + `test_decide.py` | ⚠️ PASS at the browser walk (40/40); it maps every surface, so it inherits all seven legs not fully run — surface 12's hosted-e2e and 7's seed + hosted-e2e, the walk-through of 8, the manual legs of 5 and 15, 16's partly-covered sweep |

E2E-1 and E2E-12 are **walked in a browser**; the other ten are verified through the suites their surface map assigns them (integration, contract, skill lint, unit). "Walked" is not claimed for a scenario driven only by pytest.

The evidence behind E2E-2 is split, and the two halves are not interchangeable: the **render** claims — exactly 3 elements against a closed whitelist, zero feature toggles, and the Settings nav button with its handler — are asserted in `website/apps/dashboard/src/overviewSettings.test.js`, a source-scan suite over `main.jsx` whose negative guards each carry a non-vacuity check. `tests/test_onboarding_w4_settings.py` is the **server** leg of the same contract (5 tests: the `PATCH /v1/onboarding/state` toggle round-trip and the graph-node exclusion) and cannot observe a render. Run of the render suite, at this head: `cd website/apps/dashboard && node --test src/overviewSettings.test.js` → **8 passed**.

**Surfaces: 17 of 17 exercised at an automated layer assigned to them — though not every assigned automated leg ran (surface 7's hosted-e2e never ran, surface 12's failed) — 15 green; 2 not green — 7 (a test that has never passed under load) and 12 (its hosted-e2e leg was attempted and fails, for D4's reason) — and 6 with an assigned leg not fully passed or executed — **seven legs** in all (three automated: two attempted and failing, one never run; one walk-through, not run; three manual: two not executed, one partly covered).** Stated precisely, because "verified" is a claim about a sha and about a pass:

- **Which sha:** batches **A and B** ran at the anchor commit `fbc1d96d5` — surfaces **1, 2, 7, 11, 14, 15, 16**; batches **C, D and the browser walk E** ran at `180fe6fd3` — surfaces **3, 4, 5, 6, 8, 12, 13, 17**. Three surfaces need stating rather than grouping: **surface 9's evidence is entirely at `180fe6fd3`** (the render suite plus the capture — batch B's `w4_settings` run cannot see a render), so it belongs to the second group; **surface 10 is split** — its server leg (`test_onboarding_w4_settings.py`) is batch B at `fbc1d96d5`, its render leg (`overviewSettings.test.js`) and the capture are at `180fe6fd3`. **Surface 16 is split too**: its suite (`test_onboarding_variants.py`) is batch B at the anchor, while its copy-sweep capture and the D3 re-run are at `180fe6fd3`. (Surface 4's evidence is the fork-card specs inside `test_dashboard_onboarding.py`, i.e. the browser walk, so it is a `180fe6fd3` surface.) Three commits separate the two heads, and **their file lists are enumerated here so the grouping can be checked** — but the check they support is narrower than "the two heads are equivalent", and this report does not use it as a licence for that reading: `180fe6fd3` touches only `tests/test_onboarding_endpoints.py` (+26 lines, the embedder pin); `a89120529` touches `config/rest-surface-inventory.yml` + `tests/test_rest_surface_inventory.py`; `9c2137470` touches 18 files — 5 under `tortoise/` (`sdk`, `time_aware`, `extractor_v2`, `ask_lane`, `env_truthy`), 5 tests (`test_time_aware_2520`, `test_time_aware_sdk_2520`, `test_ask_retrieval_budget`, `test_extractor_v2`, `test_env_truthy`), 5 `tools/` scripts (the retrieval/eval benches and probes), plus `.env.example`, one `docs/` plan doc and one fixture under `tests/fixtures/`. **None of those 18 files is in batches A/B's file lists** (state ×6, seed ×2, w4_settings, variants, w6_capture_disclosure, capture_disclosure_template, w11, selfhost_w12). That is a **test-file** disjointness check, and it cannot answer the question a cross-head reading actually turns on: *did the production code those suites exercise change?* It did — `9c2137470` rewrites `tortoise/env_truthy.py` (the `env_flag` resolver) and `tortoise/sdk.py` (+340 lines, the valid-time rerank in the read path), and both are statically on the anchor-head suites' path: `tests/test_onboarding_seed_endpoint.py:34` imports `_make_sdk, app` from `tortoise.hosted_api`; `tortoise/hosted_api.py:94` imports `env_flag, is_truthy` from `tortoise.env_truthy` and gates signup/onboarding flags through them; and `_make_sdk` constructs the `tortoise.sdk` whose read path that commit alters. **So batches A/B's green is measured at `fbc1d96d5` and is not asserted at `180fe6fd3`** — the eight anchor-head rows (surfaces 1, 2, 7, 11, 14, 15, 16 and surface 10's server leg) carry that limit, and indicator 2 below is graded on it. Re-running batches A/B (the same **14 files, 324 executions** — batch A's 201 over 8 files, batch B's 123 over 6; the sum is the batch table's own rows, not a second estimate) in a clean worktree at the verified head is the one measurement that would remove it. The per-batch file lists in lane conditions let any batch be re-derived at either head. The **browser walk — the evidence for the epic's own UI claims — is all at `180fe6fd3`**.
- **Which pass:** **surface 7 (seed) has never passed under load** — one of its 54 tests returns a 504 wait-budget error (D1, 5/5 reproductions, green in 9.08 s on a quiet host). Surface 11 (capture disclosure) failed one test in a batch and passed alone (D2). **Surface 16** also failed one test in batch B — `test_installer_dist_is_a_build_artifact_not_a_committed_mirror` (D3) — and passed on a re-run at `180fe6fd3` (1 passed in 12.36 s) after the stale build output was replaced by a fresh one.
- **Which layer was not fully reached — or not passed:** **six** surfaces have an assigned leg this run did not fully pass or execute —
  **seven legs** between them: **three automated** (two attempted and failing, one never run), **one walk-through** (not run) and **three manual** (two not executed, one partly covered). The automated ones come first, because a failing automated leg is evidence and a missing one is not:
  - **hosted-e2e (automated) — 12.** The leg is not missing; it is failing. `tests/e2e/test_dashboard_identity.py::test_pending_invites_accept_lands_on_team`
    was run against the local Pages preview at `180fe6fd3` and returned `1 failed in 59.97 s`, blocked on the `/auth`
    navigation — the same deployed-origin redirect as D4. Surface 12's integration and contract legs are green (the invites ×3 suites in
    batch C, out of that batch's 123); its browser leg is not, so surface 12 is the one automated **hosted-e2e** assignment that was
    attempted and did not pass — surface 7's attempted-and-failed automated leg is
    D1, above.
  - **hosted-e2e (automated) — 7.** Surface 7's assigned layer is "Integration (docker lane) + hosted-e2e + unit
    (ontology)", and the hosted-e2e half was never run: the only spec that drives a seed journey end to end is
    `tests/e2e/hosted/test_14_onboarding_journey.py` (its E2E-12 journey goes signup → org → fork → connect → seed →
    decide), it is **HTTP-only** — the `api` fixture, no browser; its own docstring reads "THIS leg is HTTP-only" — and
    it appears in no batch here. Surface 7's integration and unit legs are green on a quiet host (D1) and its hosted-e2e
    leg has no evidence at all — which is a different statement from "it failed". Surface 7 has no browser leg at all.
  - **walk-through — 8.** `test_decide.py` covers the integration leg; the browser walk only clicks "Not sure yet —
    decide later" (`test_dashboard_onboarding.py:1616`, `:1657`), so it never drives options→criteria→findings→IMPL/NAND→EP.
  - **manual — 5, 15 and 16.** 5 (install across six harnesses) and 15 (a hand-driven self-hosted install) were not
    executed; 16 (the manual copy sweep) is **partly** covered — the desktop capture shows the copy sweep, not every
    shrink variant. Partly covered is not the same state as not executed, so 16 is named separately rather than folded
    in with 5 and 15.

Two things sit **outside the 17 surfaces' own gates, apart from the single overlap stated next** — batch F's five e2e modules and the W11 funnel read — and neither is counted as a surface pass. Batch F nonetheless overlaps the surface set **in at least the one place stated next** — and because its per-spec results were not preserved, a further overlap among its passes or its failures cannot be excluded:
**`tests/e2e/test_dashboard_identity.py::test_pending_invites_accept_lands_on_team`
is surface 12's assigned hosted-e2e leg, measured failing in its own targeted single-spec run.** It was also
collected inside batch F (one of `test_dashboard_identity.py`'s 22 collected cases), but whether its batch-F execution is
among the 40 failures was not preserved (D4), so the two measurements are stated side by side rather than identified. It is counted **once** — as surface
12's not-green (which is why the green count is 15, not 16) — and its cause is D4's deployed-origin redirect, not a
defect in the invite-accept flow. Each item, with its evidence: five e2e modules whose failures are **not** one cause (deviation D4 — three of them are recorded by CI itself as stale against the #3501 BFF-session seam and the #4054 auth-origin move and are deliberately unwired; one is a CI-run spec; one cannot render from this Pages root — per-spec mechanism in the D4 row), and the **W11 funnel read** (indicator 5 / A0 gate data — hosted capture returns 402, so the events of the sessions that would populate the funnel are never posted). Surface 14 is the telemetry *machinery*, and that is verified; the funnel *read* is a different artifact and is not.

The `Surfaces` column is the map's own list of surfaces each scenario **exercises**, copied verbatim from
`04-test-design.md` — not a list of what this run's evidence happened to touch. The two differ, and the difference is
deliberate: E2E-12 is mapped to "1–17 (all)", while the browser walk that evidences it drives surfaces 3, 4, 9, 10, 16
and 17 directly and the rest through their assigned suites. Reading the column as coverage rather than traceability is
the mistake it invites, so it is labelled.

## Per-surface evidence

Commands below ran at `180fe6fd3` from `.worktrees/capstone-2008-1976` (clean checkout of the verified commit),
**except** the batch **A/B** rows — surfaces 1, 2, 7, 11, 14, 15, surface 16's suite, and surface 10's server leg
(`test_onboarding_w4_settings.py`) — which ran from the **hub**
checkout at the anchor `fbc1d96d5` (see "Which sha" above and Lane conditions below; the two heads' intervening file
lists are enumerated there, and they do **not** establish that the production code those suites import is unchanged —
`9c2137470` changes `tortoise/sdk.py` and `tortoise/env_truthy.py`, both on that path). Those rows are the report's
weakest evidence: an anchor commit, in a shared hub tree whose state at measurement time was not recorded. Every **pytest** command below uses
`L="$(.venv/bin/python tools/test_lane.py uri)"; TORTOISE_DB_URI=<lane> .venv/bin/python -m pytest …` (the render-suite rows are Node, not pytest, and take no lane URI).

| Surface | Assigned layer | Suite(s) | Result |
|---|---|---|---|
| 1 OnboardingState graph node | Integration (docker) | `test_onboarding_state.py`, `test_onboarding_state_cas.py`, `_split_init/_split_checkpoint/_split_patch/_split_projection` | ✅ 6 files green in a 201-test batch (1 unrelated failure, D1) |
| 2 Legacy store migration | Integration + regression | same split suite (migration/projection) | ✅ PASS |
| 3 Org-create transaction | Integration + hosted-e2e | `test_one_free_org_entitlement.py`, `test_free_team_entitlement.py`, wizard org-create specs | ✅ 170-test batch green |
| 4 Fork card | hosted-e2e + unit | fork specs inside `test_dashboard_onboarding.py` | ✅ PASS |
| 5 Universal setup command | Contract + integration + **manual (6 harnesses)** | installer suites + `test_served_skills.py` | ✅ contract + integration PASS · ⚠️ **manual 6-harness leg not executed** (the installer's per-harness arms are asserted; nobody sat in front of six harnesses) |
| 6 Agent install skill | Skill lint + integration | `test_served_skill_byte_parity.py`, `test_served_skills.py`, `test_installer_agents_block_3429.py` (42) | ✅ PASS — the parity suite pins the **deploy guard** (the served copy is compared to the canonical by the workflow step it asserts), **not** local equality; local equality of `tortoise/onboarding/SKILL.md` and `website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md` was checked by hand at this head (`cmp -s` → identical, 62,486 bytes each) and is not asserted by any suite |
| 7 Seed — two Subjects | Integration + hosted-e2e + unit | `test_onboarding_seed.py`, `test_onboarding_seed_endpoint.py` | ⚠️ 53 passed, 1 failed (54 tests): `test_email_derived_person_requires_confirmation_no_writes` → 504 wait budget (D1) · ⚠️ **hosted-e2e leg never run** (its only candidate spec, `tests/e2e/hosted/test_14_onboarding_journey.py`, is HTTP-only and appears in no batch — see the leg list) |
| 8 Decide nudge | Integration + **walk-through** | `test_decide.py` | ✅ integration PASS · ⚠️ **walk-through not executed** — the browser walk only dismisses the nudge |
| 9 Overview calm | hosted-e2e + ux-verification | `website/apps/dashboard/src/overviewSettings.test.js` (8/8) + screenshot | ✅ PASS |
| 10 Settings tab (owner) | hosted-e2e + integration | `website/apps/dashboard/src/overviewSettings.test.js` (the Settings nav button and its handler; the SettingsTab's four homes) + `test_onboarding_w4_settings.py` (server leg) + screenshot | ✅ PASS — Settings is the **6th of 6 rendered nav buttons** (`main.jsx:9602-9613`: Overview · API Keys · Graphs · Members · Billing · Settings); the routable-tab list `KNOWN_TABS` has **7** entries because `profile` is a tab (`main.jsx:1045`) reached from the account menu (`:9785`) and by deep link, **not** from the nav row. The source comment's "7th tab (R2-11)" is a design ordinal, not the rendered count |
| 11 Capture disclosure | Integration + hosted-e2e | `test_onboarding_w6_capture_disclosure.py`, `test_capture_disclosure_template.py` | ⚠️ 1 flake, passes alone (D2) |
| 12 Invite-accept fusion | Integration + contract + **hosted-e2e** | `test_invite_fusion_http.py`, `test_invites_http.py`, `test_invites_email_http.py`; hosted leg = `test_dashboard_identity.py::test_pending_invites_accept_lands_on_team` | ✅ integration + contract green (batch C, 123 tests; that batch also covers surface 13) · ⚠️ **hosted-e2e leg exists and FAILS**: `1 failed in 59.97 s`, blocked on `/auth` navigation (D4's mechanism) |
| 13 Builder catalog | Unit + integration + contract | `test_capabilities_endpoint.py`, `test_capability_catalog.py` | ✅ PASS |
| 14 Onboarding telemetry | Integration + regression | `test_onboarding_telemetry_w11.py` | ✅ PASS |
| 15 Self-hosted onboarding | Integration + **manual** | `test_onboarding_selfhost_w12.py` | ✅ integration PASS · ⚠️ **manual leg not executed** (a real self-hosted install was not driven by hand this run) |
| 16 Wizard shrink + copy sweep | hosted-e2e + manual | `test_onboarding_variants.py` + screenshot ("Your Organization is live") | ⚠️ PASS — 1 test in this file (the dist guard, D3) failed at `fbc1d96d5` and passed on re-run at `180fe6fd3`; **proven** a stale-build artifact, not a code defect. Manual leg **partly** covered: the capture shows the copy sweep, not every shrink variant — so surface 16's evidence spans both heads, the suite at `fbc1d96d5` and the capture plus the D3 re-run at `180fe6fd3` |
| 17 Cross-W full journey | hosted-e2e + walk-through | `test_dashboard_onboarding.py` — **40 passed** | ✅ PASS |

**Totals, stated so the arithmetic can be checked:** **654 passed in batches A–E** (200 + 121 + 123 + 170 + 40) **plus 22 passed in batch F = 676** (batch F's surface attribution was not preserved — at least one of those modules carries an in-scope pin, see D4), plus the **8 render-suite passes** (`node --test src/overviewSettings.test.js`) that are surface 9's evidence at `180fe6fd3` and sit outside every batch — so **662 in-scope passes are attributable** (654 + 8), plus 22 batch-F passes whose surface attribution was not preserved, **684 passes in total** — and **1 skipped**, which is also batch F's. Failures, stated so that nothing is collapsed into a number it does not support: **3 inside batches A–E** — batch A's 1 (D1) and batch B's 2 (D2, D3), each named and reproduced — plus **batch F's 40 failures**, whose per-spec attribution was not preserved — **none of the 40 is individually identified as in-scope or out of it** from the batch record (the surface-4 pin's result was not captured either), so no split of them can be stated. Separately, **one in-scope failure was measured in its own targeted single-spec run**: `test_pending_invites_accept_lands_on_team` — **surface 12's assigned hosted-e2e leg**, counted **once**, under surface 12, and **not added to the 43** below because that run is a separate measurement from the batched ones. So the in-scope failure set is **four identified** (D1, D2, D3, surface 12's leg), with a fifth not excluded. All batched failures: 3 + 40 = 43, and batch F also contributes the single skip.

```text
A  200 passed, 1 failed   state ×6 + seed ×2                      (D1)
B  121 passed, 2 failed   w4_settings, variants, w6_capture,
                          capture_disclosure_template, w11,
                          selfhost_w12                            (D2, D3)
C  123 passed             invites ×3, capabilities, catalog
D  170 passed             entitlements, decide, installer ×2, served skills ×2
E   40 passed             dashboard onboarding e2e (browser)
F   22 passed, 40 failed, 1 skipped   welcome / ship-test / gate / identity / login  (D4)
```

## The browser walk (E2E-1, E2E-12; surfaces 3, 4, 9, 10, 16, 17)

Built from the verified commit and served the way the suite's own docstring prescribes:

```bash
cd website/apps/dashboard && npm ci && npm run build
npx wrangler@4 pages dev dist --port 8790 --inspector-port 9301 --d1 SESSIONS   # dashboard + D1 pages function
npx wrangler@4 pages dev .    --port 8788 --inspector-port 9302                 # site root (/auth)
RUN_DASHBOARD_E2E=1 pytest tests/e2e/test_dashboard_onboarding.py -q  →  40 passed
```

What the walk exercised, per spec: the 5-step wizard, the fork card as **the only checkpoint the dashboard writes for
that org** (`cap["checkpoint"] == [{"fork": "build"}]`, `test_dashboard_onboarding.py:1314` — no suite drives a
second org, so "asked once per org" is asserted as *one* walk's single checkpoint, not as a re-ask), the resume door
landing on the card (`:1367`), connect step for owner/admin vs member, paste refusal paths (revoked/disabled/bootstrap rows never
advance), keyless Claude Desktop/Web paths, the done screen's incomplete-setup variant, and the mobile viewports
(320/360/390/414) with no horizontal overflow in the fork key row.

One item from the test-design map's anti-behavior column is **not** exercised by the walk and is called out as D6
below: "fork triggers billing/upgrade demand". The suite's org projection hardcodes `subscription_status: "active"`,
so no test varies the plan to prove the card is reachable without one — the property is a design decision
(`00-align-and-scope.md` §116, `04-test-design.md` S4), not a verified one.

**Screenshots** — four captures, taken from the same preview. They were written to `/tmp/capstone-shots/` on the
measuring host and were **not committed to this repo and not attached to #2008**, so a reader of this artifact cannot
inspect them; that is a limit of this report, recorded rather than glossed over. The claims they support are also
pinned in code (cited per capture), which is what a later lane can re-derive:

- `onboarding-desktop.png` (1440×900) and `onboarding-mobile.png` (390×844) — the continuation card on Overview:
  "Continue setting up <org>", tabs `Overview · API Keys · Graphs · Members · Billing · Settings`, a `Setup` button,
  a `Free tier` badge, and the copy sweep visible in the body ("Your **Organization** is live"). No feature toggles
  on Overview — the calm surface holds.
- `dashboard-desktop.png` (1440×900) and `dashboard-mobile.png` (390×844) — the **degraded** path: with a session but no
  reachable BFF, the dashboard renders "Could not load your organizations — try again" with a contact link. That is the
  honest failure the surface map's S9 row requires (never a blank page, never a false checklist), and the mobile capture
  confirms it does not overflow at 390 px.

That is **four** captures in total, matching the count in the indicator-4 table below (an earlier revision of this
list named three while the table named four — the fourth was `dashboard-mobile.png`).

## Indicator 3 (logs + DB state at each step) — asserted in the suites, NOT captured as a per-step log

Stated plainly: **this capstone did not produce a step-by-step log/DB-state transcript.** What exists is per-suite
graph-state assertion, which is the same evidence at a coarser grain:

- `tests/test_onboarding_seed_endpoint.py` asserts actual graph contents after a seed — `MATCH (s:Subject) RETURN count(s)`
  and `MATCH (o:<label> {name: 'Acme'}) RETURN count(o)` (`:247`, `:251`) — and the no-writes arm asserts that a refused
  confirmation leaves the graph untouched (`:79`).
- `tests/test_onboarding_seed_endpoint.py` also asserts the Overview digest reads through to the graph —
  `_point_count(org_id)` → `MATCH (p:Point) RETURN count(p)` (`:436`–`:479`, "Points — the digest counts 0, never the
  old 13"). That is a DB-state assertion on the surface a user actually looks at.
- `tests/test_onboarding_state.py` / `_cas.py` exercise state reads and writes against the real graph (the docker lane
  is a real FalkorDB, not a mock), so a write that did not land fails the suite.

What was **not** done: capturing the sequence of graph writes step-by-step as the wizard advanced, with a log line per
step. That is the indicator as written, and the honest status is "partially met — asserted at suite granularity, not
sequenced". Recorded here rather than counted as satisfied.

## Indicator 4 (screenshots per surface) — 4 captures, 3 UI surfaces plus 1 degraded state

The indicator asks for a screenshot per surface. **4 images exist against 17 surfaces**, so this is a partial
satisfaction, and the ratio is stated rather than implied:

| capture | surface | what it shows |
|---|---|---|
| `onboarding-desktop.png` (1440×900) | 9 Overview, 10 Settings tab **label**, 16 copy sweep | the continuation card, the 6-button nav with Settings, the `Free tier` badge, "Your Organization is live". The capture shows the Settings *entry point*, not that surface's content |
| `onboarding-mobile.png` (390×844) | 9, 10, 16 | the same surfaces at the mobile breakpoint, no horizontal overflow |
| `dashboard-desktop.png` (1440×900) | S9 degraded state | "Could not load your organizations — try again" with a contact link |
| `dashboard-mobile.png` (390×844) | S9 degraded state | the same at the mobile breakpoint |

**UI surfaces exercised but not captured:** surface 4 (fork card) — asserted by
`test_first_timer_wizard_build_fork_records_no_catalog_presented` (`test_dashboard_onboarding.py:1314`:
`cap["checkpoint"] == [{"fork": "build"}]`, the fork is the only checkpoint the dashboard writes) and
`test_resume_door_lands_on_the_fork_card_instead_of_org_create` (`:1367`), with the set-once write pinned in
`test_dashboard_gate.py:405-408` (`fork_payloads == [{"fork": "self"}]`) and the first-org arm in
`tests/e2e/hosted/test_14_onboarding_journey.py:65` (`st["fork"] is None`); surface 11 (capture disclosure —
asserted); surface 3 (org-create, driven by the wizard's org-create specs in the same walk); and surface 17 (the
full journey, driven by the 40 walked specs). All four were exercised in the browser; none has an image.
Surfaces 1, 2, 5–8, 12–15 are not UI surfaces in this deploy, so a screenshot is not the assigned evidence for them.

## A0 gate data (indicator 5) — NOT captured, and the reason is a hard external limit

This is a separate artifact from surface 14. Surface 14 verifies that the events **fire, dedupe and reach the write
path**; this section is about **reading the resulting funnel**, which needs the hosted environment.

The indicator asks for walk-through friction observations **and a W11 funnel read** (`onboarding_seed_complete`,
`onboarding_decide_complete`, deduped). The event machinery is verified (surface 14, `test_onboarding_telemetry_w11.py`);
the **funnel read is not obtainable from this machine**, because hosted capture is over quota:

```text
[tortoise-capture] Hosted capture FAILED (HTTP 402 — {"code":"quota_exceeded",
 "message":"Team points limit reached: 25000 in use + 138 estimated for this capture
 exceeds 25000. Upgrade your plan. ...","used":25000,"limit":25000})
```

Every session on this box, including the ones run for this capstone, therefore fails to post its events — so a
"first real cohort" funnel read would read zero for a reason that has nothing to do with the epic. Recording that as
the state of the gate rather than reporting a number the environment cannot produce.

**Friction observations** (from the walk itself, the part that is obtainable): zero "what do I do now" moments in the
40 walked specs — each step has a next action, the connect step states whether a key is needed ("Claude Desktop and
Claude Web connect without a key"), and the resumed-setup card names both available actions. The one friction the walk surfaced **is** in the product, and is minor: the degraded card says "try again" without
naming what failed (D5 below — an observation, not a defect, because the copy is still actionable and honest).

## Deviations (all measured; none accepted silently)

| # | Finding | Evidence | Disposition |
|---|---|---|---|
| **D1** | `test_onboarding_seed_endpoint.py::TestSeedEndpoint::test_email_derived_person_requires_confirmation_no_writes` returns **504 "the server's wait budget for this request was exceeded"** under load — **5/5** reproductions; the same test passes in **9.08 s** on a quiet host | `--lf` ×5, each run 132–136 s, load average 26→45; body: `assert 504 == 200` | **An unresolved load-bound 504 — the budget and the endpoint cannot be distinguished by this evidence; tracked on #9001.** What was measured is the *test's* wall time (132–136 s vs 9.08 s), not the endpoint's own response latency under load — the request never returned a response, the server gave up on a budget. So the classification the data supports is: *under fleet load this endpoint exceeds its own wait budget*. Whether that budget is too small or the endpoint is too slow is the open question on #9001, and the next measurement is the endpoint's server-side latency under load (the test evidence cannot distinguish the two) |
| **D2** | `test_onboarding_w6_capture_disclosure.py::TestDeleteDuringCapture::test_no_orphaned_receipt_under_concurrent_delete` failed in the 6-file batch and **passed alone** (133 s) | batch B + isolated re-run | Load-sensitive; re-run green. Not written off — D1 is the same mechanism |
| **D3** | **PROVEN, single variable.** `test_onboarding_variants.py::test_installer_dist_is_a_build_artifact_not_a_committed_mirror` compares a built `dist/` copy against `public/` **when `dist/` exists**. In the hub checkout: `dist/install-tortoise-skills.sh` is dated **28 Sep**, `public/` is dated **18 Oct** (it gained the `#4643` "WHICH REPO AUTHORS WHAT" header), so they disagree and the test reds. In a checkout built from the same lineage, `dist/` == `public/` byte-for-byte and the test passes | `ls -lT` on both files (28 Sep vs 18 Oct); `diff dist public` shows exactly the missing `#4643` header; `cmp -s` in the fresh worktree → identical; the same test re-run in that worktree → **1 passed in 12.36 s** | **Environment artifact, proven not a main defect** — one variable (the age of an ignored build output), measured on both sides. It is also a genuine trap worth knowing: **any lane that built the dashboard once will red this test the next time `public/` changes on main**, with a message that blames "a BUILT dist copy" rather than naming the remedy (`rerun the dashboard build` — which is the remedy, and it is in the assertion text) |
| **D4** | (batch F) `tests/e2e/{test_welcome_page,test_ship_test_onboarding,test_dashboard_gate,test_dashboard_identity,test_session_login_flow}.py` → 22 passed, 40 failed, **1 skipped** over the **63 collected cases** (`--collect-only` per module: welcome 11, ship-test 8, gate 19, identity 22, login 3 — the same 63 the pass/fail/skip tally accounts for) | first failure: expected `https://tortoise.premiselabs.co/auth`, actual `http://127.0.0.1:8790/auth` | **Not one cause, and not "this lane had no prod session".** Per module: (a) `test_welcome_page.py` **cannot render here** — its own docstring records that `/welcome` is not served by this Pages root (#8826, #4054) and lives in the dashboard Pages project, so no `wrangler pages dev` preview of `website/` can produce it; (b) `test_dashboard_identity.py` (**21 of its 22 collected specs fail in CI**), `test_dashboard_gate.py` (**15 of 19**) and `test_session_login_flow.py` (**3 of 3**) are recorded **by CI itself** as *stale against the #3501 BFF-session seam and the #4054 auth-origin move* — those ratios are CI's own job, a different run from this lane's batch F, so they are not summed with the 40 below, which is why that job deliberately does not wire them (`.github/workflows/ci.yml:2104-2120`, evidence on #4221) — their failure here is that known stale class, not a missing session; the first failure's expected/actual pair is exactly what a `__AUTH_BASE_URL` seam that did not take effect looks like, and `test_dashboard_gate.py:116-141` shows the prod origin is an *asserted redirect target served from the local :8788 preview* (#2744), never a required login; (c) `test_ship_test_onboarding.py` **is wired into CI's `dashboard-e2e` leg** (`.github/workflows/ci.yml:2121-2129`), i.e. it is run against the same two-origin local harness this lane built, so it is CI-runnable by construction rather than needing the deployed host. **Not recorded, and not claimed:** the lane captured the aggregate only, so which of the 40 failures belongs to which module was not preserved; (c) is therefore recorded as *un-derived* rather than explained. The one in-scope failure **identified** is `test_dashboard_identity.py::test_pending_invites_accept_lands_on_team` (surface 12's hosted-e2e leg, measured in a targeted single-spec run — whether it was among the 40 was not preserved; see the overlap paragraph). The modules themselves are not all outside the epic: `test_dashboard_gate.py:405-408` carries surface 4's fork set-once **pin** (cited at Indicator 4; whether that spec was among batch F's 22 passes was not preserved), and `test_ship_test_onboarding.py` walks the wizard against the fork checkpoint. Most of the modules' assertions are on auth/identity/gate surfaces the epic does not change — but that is the scope argument, and for (a)–(c) it is the *only* one |
| **D5** | The dashboard's degraded state says "Could not load your organizations — try again" without naming what failed | `dashboard-desktop.png` | Observation, not a defect and **not filed** as an issue: the copy is actionable and honest, and the surface map asks for a degraded state, not a diagnosis. Recorded here only |
| **D6** | The anti-behavior **"fork triggers billing/upgrade demand"** (`04-test-design.md` S4) has **no covering test** — filed as **#8998**: `test_dashboard_onboarding.py` hardcodes `subscription_status: "active"` and never varies the plan, so the "never a billing gate" property is asserted in the design docs but not proven by any suite | `rg -n "billing\|subscription_status" tests/e2e/test_dashboard_onboarding.py` → only the `active` mock at `:211` | **Gap, not a regression.** The card's own copy and flow contain no billing reference, so nothing suggests the behavior is broken — but a future change could introduce one and every current test would stay green. This is the single concrete test-coverage gap this capstone found |

## Proud test (#2008's sixth element) — PARTIAL, with each criterion graded

Issue **#2008** carries a sixth obligation beside the five indicators, added by the epic's Verify stage
(`08-verify.md:69`, `:107`): review the **recorded journey as a coherent product story** and show it without apologising
for any part. Its criteria, graded here:

1. **No orphaned screens** — **PASS within the walked journey.** The walked story is create Organization → fork card →
   connect (owner/admin and member arms) → done, and every step hands off to the next: the resume door lands on the
   fork card rather than back at org-create (`test_dashboard_onboarding.py:1367`), the connect step names whether a key
   is needed, and the done screen's incomplete-setup variant names the remaining action. No step is reachable-and-stuck.
2. **Consistent terminology** — **PASS, and machine-checked.** `overviewSettings.test.js`'s copy-sweep test fails on
   `workspace` anywhere in the Overview or Settings copy and requires `Organization`; it passes (8/8). The wizard
   headings use "Organization" and "your agent" throughout. This criterion is the one a later lane can re-run rather
   than trust.
3. **No step that reads as an internal artifact** — **PASS on the walked journey, with one named exception.** No
   screen names a workstream, a surface number, a graph node, or a JSONB key. The exception is D5: the degraded card
   says "Could not load your organizations — try again" without naming what failed — the one line a founder would have
   to explain rather than show. It is recorded as an observation because the copy is honest and actionable;
   #2008's failure rule (file a P0/P1 UX fix) applies to an *offending* screen, and whether this one crosses that bar
   is the owner's reading, not this lane's.

**Verdict: PARTIAL — not passed, not failed.** Criteria 1–3 hold on the two walked journeys, while the story review
covers 2 of 12 journeys and no capture survives for a second reader to re-derive it (the `/tmp` limit above). The
proud test is therefore **not claimed as passed**, which is why this report does not treat #2008 as complete.

## Residuals carried forward (not this capstone's to close)

- **D4** (five e2e modules; each module's mechanism is the one the D4 row assigns it) has **no
  single owner**. None of it is closed by "get a production session": `test_welcome_page` cannot render from this Pages
  root at all (#8826/#4054) and belongs in the dashboard Pages project's own suite; `test_dashboard_identity`,
  `test_dashboard_gate` and `test_session_login_flow` are **stale** against the #3501 BFF-session seam and the #4054
  auth-origin move, and CI deliberately does not wire them — their real home is the update tracked on **#4221**, not a
  session; `test_ship_test_onboarding` **is** run by CI's `dashboard-e2e` leg, and its per-module result in this lane was
  not captured, so there is nothing to chase until someone re-runs that module in the harness. Recorded here so D4 is not
  mistaken for covered by the green 40/40 above.
- **#1992** (the surface map itself) is still open — its artifact is delivered in the issue; closing it is bookkeeping.
- **D1 is tracked on #9001** (filed by this capstone; #9008 was the same finding filed minutes later and was
  closed as its duplicate). It was **not** left on #8000: that issue is **closed**
  (2026-10-16) and is about a *different* test (`test_capture_loop_responsiveness`), so the recurrence comment posted
  there on 2026-10-18 landed on a dead thread two days after closure. #9001 names the measurement that classifies it
  (server-side latency under the same concurrency, distinguishing "budget too small" from "endpoint too slow").
- **D6** (no test varies the subscription plan, so "never a billing gate" is unproven) is **filed as #8998**, with the
  test to write and the mutation that must redden it. Not written here: this capstone's job is to verify, not to extend
  the suites.
- **The hosted-capture 402 is tracked on #9011.** It blocks the W11 funnel read for **every** lane, not just this one — it is the reason indicator 5
  is unmeasurable here, and it will be the reason the next capstone cannot measure it either.

## Lane conditions (measurement caveats)

- The fleet ran other lanes throughout (load average 26–45 on 10 CPUs, 12-lane ceiling). Every wall time in this
  report is an upper bound, and D1/D2 are the two places where that bound crossed an assertion.
- Batches **A and B** ran from the **hub** checkout at `fbc1d96d5`; batches **C and D** ran from the clean capstone
  worktree at `180fe6fd3`, and the browser walk (E) and the peripheral e2e (F) ran there too. The intervening commits
  and their file lists are enumerated in the "Which sha" bullet above, and the file lists are recorded per batch so any
  batch can be re-derived at either head. D3 is the one finding whose *provenance* is that difference: it fails where a
  stale ignored `dist/` exists (the hub) and passes where it does not (the clean worktree).
- **The hub checkout's tree state at measurement time was not recorded**, so batches A and B cannot be shown to have
  measured `fbc1d96d5` itself: no `git status --porcelain` was captured before them, and the hub is the shared main
  worktree other lanes were working in throughout this run. Those **324** executions (batch A's 201 over 8 files + batch B's 123 over 6) therefore measured `fbc1d96d5` **plus
  whatever tracked modifications and untracked files the hub carried at the time** — a state this report cannot
  reconstruct. That is not a hypothetical difference: D3 is precisely a stale *ignored* `dist/` in that tree. Batches
  C, D, E and F ran in a clean worktree, so this caveat is A/B's alone. Together with the cross-head limit in "Which
  sha" — `9c2137470` changes `tortoise/sdk.py` and `tortoise/env_truthy.py` on the anchor-head suites' import path — the
  eight anchor-head surface rows are the weakest evidence in this report, and one clean-worktree re-run of batches A/B
  at `180fe6fd3` would settle both at once.
- The browser walk used a **local Pages preview** (`wrangler pages dev`, with Cloudflare D1 bound) — the same surface CI's
  `dashboard-e2e` leg drives, not the deployed host. D4 is where that distinction bites, and the D4 row states which
  module is a CI-run spec and which are recorded stale.

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

**NOT VERIFIED AS A WHOLE, with the coverage stated exactly.** 17 of 17 surfaces were exercised at an automated layer
**assigned to them** — though not every assigned automated leg ran (surface 7's hosted-e2e never ran,
surface 12's failed) — and **15 are green**; the two that are not are **surface 7**, the seed endpoint, which has never
passed under load (D1), and **surface 12**, whose hosted-e2e leg was attempted and fails for D4's reason — recorded as a
failing leg, not as a claim that the surface is defective. **Six** surfaces have an assigned leg this run did not fully pass or execute — **seven legs**: surface 7's **integration** leg (attempted, one test never passes under load — D1) and its **hosted-e2e** leg (never run), surface 12's **hosted-e2e** leg (not green — see above), surface 8's **walk-through** (not run), and the **manual** legs of 5 and 15 (not executed) and 16 (partly covered).
The batches covering surfaces 1, 2, 7, 11, **10's server leg**, 14–16 ran at the anchor commit `fbc1d96d5`, while the browser walk and
the remaining suites (surfaces 3–6, 8, 9, 12, 13, 17 — plus surface 10's render leg and surface 16's capture) ran at `180fe6fd3` — see the sha
grouping above. Those anchor-head rows are **anchor-measured, not asserted at the verified head**: the intervening
`9c2137470` changes `tortoise/sdk.py` and `tortoise/env_truthy.py`, both on the path those suites import through
`tortoise/hosted_api.py`, and the hub tree they ran in was never shown clean. All 12 high-level E2E scenarios are covered with
evidence; **2 of them (`E2E-1`, `E2E-12`) were walked in a real browser** (40/40), the other ten through their assigned
suites. The two UI claims the epic rests on — a calm Overview and a Settings home — are confirmed on screen as well as
in assertions.

Of the three failures inside batches A–E: **D2** was green only on re-run (a load-sensitive flake), **D1** never went green under
load (5/5 reproductions — it passes on a quiet host, which is why the classification is unresolved and tracked on #9001),
and **D3** was **proven** an environment artifact with a single-variable control (a 28 Sep build output against
an 18 Oct source; the same test passes in a checkout built from the same lineage). One *layer* — five e2e modules
testing deployed auth/identity/gate surfaces (D4) — was not driven: three of them are recorded stale by CI and
deliberately unwired, one is a CI-run spec whose local failures were not re-derived per module, and one cannot render
from this Pages root. The W11 funnel read
(indicator 5) is unmeasurable while hosted capture returns 402.

**The capstone's (#2008) five verification indicators, plus the proud test, as a whole** — these five are *not* epic #1976's own O/I/T indicators,
which are a different set and are mapped below:

| #2008 verification indicator | Status |
|---|---|
| 1 — every high-level E2E walked with evidence | **partial**: **2 of 12 walked in a browser** (E2E-1, E2E-12); the other ten are verified through their assigned suites. The indicator's verb is "walked", and this report's own rule is that "walked" is not claimed for a pytest-driven scenario — so "covered" is not substituted for it. Re-walking the remaining ten is what would move it to met |
| 2 — every integration surface through its assigned layer | **partial**: 17/17 exercised at the **automated** layer, but **15 green** — surface 7 has never passed under load (D1) and its **hosted-e2e** leg never ran, surface 12's **hosted-e2e** leg was attempted and fails for D4's reason, surface 8's **walk-through** was not run, and surfaces 5, 15 and 16 each carry an **unexecuted or partly-covered manual leg** that their map row assigns. So 17/17 were not verified through *their assigned* layer. **And eight of the rows are measured at the anchor `fbc1d96d5`, not at the verified head**: the intervening `9c2137470` changes `tortoise/sdk.py` and `tortoise/env_truthy.py`, which those suites import through `tortoise/hosted_api.py`, and the hub tree they ran in was never shown clean — so their green is anchor-measured and is not asserted at `180fe6fd3` (see "Which sha" and Lane conditions) |
| 3 — logs + DB state at each step | **partial**: graph state is asserted at suite granularity (Subject/label counts, the Overview digest's `_point_count`), not captured as a per-step transcript |
| 4 — screenshots per surface | **partial**: 4 captures covering surfaces 9, 10, 16 and the degraded state; the fork card (4) and capture disclosure (11) are asserted, not captured |
| 5 — A0 gate data (friction + W11 funnel) | **partial**: friction observations delivered; the funnel read is blocked by the hosted-capture 402 |
| 6 — the **proud test** (added to #2008 by the epic's Verify stage) | **partial**: see the proud-test section above — criteria 1 and 3 pass on the two walked journeys, criterion 2 passes and is machine-checked, but the story was reviewed over 2 of 12 journeys and no capture survives for a second reader |

**Epic #1976's own indicators, for the reader holding the epic** (different numbering, all covered): (1) the one-sitting
first-run — E2E-1 + E2E-12; (2) Overview calm — surface 9 + `website/apps/dashboard/src/overviewSettings.test.js` (8/8) + the capture;
(3) org-name captured as an explicit step — E2E-3 + surface 3; (4) self-vs-build fork asked as a deployment question —
surface 4 + `04-test-design.md` S4; (5) the seed teaches Object-vs-Statement with the user's own data — E2E-4 + surface 7.
None of the five is claimed as fully met *as a capstone verification indicator*, the sixth element (the proud test) is
graded partial rather than passed, and none of the epic's own five is
uncovered.

**What this report does not do:** it does not mark the epic complete, it does not claim all five indicators are met, it
does not claim the proud test as passed, and it does not close D6 (filed as #8998) or D1 (filed as #9001). Completion
needs the owner's reading of D1 (is a 504 under load a product promise or a test contract?), of D5 (does the degraded
card's unnamed cause clear the proud test's failure rule?), and of the funnel gate — none of which is a lane decision.
