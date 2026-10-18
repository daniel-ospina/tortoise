# Capstone verification report — epic #1976 (agent-driven onboarding)

**Status: PASS for the epic's own surfaces, with partial coverage named per *capstone* indicator and six findings (D1 a load-bound endpoint, D2 a flake, D3 a proven environment artifact, D4 an out-of-scope layer, D5 an observation, D6 a filed coverage gap #8998)** · **Commit verified on:** `180fe6fd3` (plus `fbc1d96d5` for the first batch — see lane conditions) · **Date:** 2026-10-18 · **Capstone:** #2008 · **Epic:** #1976 · **Surface map:** #1992

**Mode:** docker lane on a throwaway private FalkorDB (`tools/test_lane.py`, #5084) per batch, plus a **real browser walk** of the dashboard (Playwright/chromium against a local Pages preview built from the same commit, with the bound D1 database). No mocks were substituted for the layer under test: the integration layer talks to a real graph, and the browser layer runs the shipped wizard in a real Chromium against the shipped `dist`.

## Scope

The epic's 12 high-level E2E scenarios (`03-scope.md` §4) and the 17 integration surfaces from the test-design map (#1992), each verified through the layer the map assigns it — integration (docker lane), contract, skill lint, hosted-e2e (browser), or manual.

This epic's deploy surface is **web dashboard + hosted API + served agent skill**, so "walk it" means: for UI surfaces, drive the real wizard in a browser; for graph/API surfaces, exercise the real endpoints against a real graph; for the served skill, execute the installer and check the bytes it writes. Evidence is the command, its result, and — for the UI — a screenshot.

## Verdict summary

| # | High-level E2E (scope §4) | Surfaces (verbatim from `04-test-design.md`, the map #1992 declares canonical) | Evidence | Result |
|---|---|---|---|---|
| 1 | One-sitting first-run (north star) | 1, 3, 4, 5, 7, 8, 17 | `test_dashboard_onboarding.py` (40 passed) + state/seed suites | ✅ PASS (browser walk, local preview) |
| 2 | Overview calm | 9, 16 | browser walk + `test_onboarding_w4_settings.py` | ✅ PASS — 3 elements, zero toggles, Settings present in the 6-button nav |
| 3 | Org-name capture | 3, 16 | `test_one_free_org_entitlement.py`, `test_free_team_entitlement.py` + wizard | ✅ PASS |
| 4 | Ontology-precise seed | 7, 15 | `test_onboarding_seed.py`, `test_onboarding_seed_endpoint.py` | ⚠️ **PARTIAL** — 53 of 54 pass; one test has never passed under load (D1). Its graph-state assertions do hold on a quiet host |
| 5 | Agent install across harnesses | 5, 6 | `test_installer_agents_block_3429.py`, `test_served_skills.py`, `test_served_skill_byte_parity.py`, `test_installer_preserves_foreign_skill_content.py` | ⚠️ PASS at the automated layer — the six-harness manual walk (surface 5's manual leg) was not run |
| 6 | Graph-held state resumption | 1, 2, 10 | `test_onboarding_state*.py` (6 files) | ✅ PASS |
| 7 | Invite fusion (most failure-prone) | 12 | `test_invite_fusion_http.py`, `test_invites_http.py`, `test_invites_email_http.py` | ✅ PASS |
| 8 | Atomic new-user accept | 1, 12 | same as E2E-7 (atomic path assertions) | ✅ PASS |
| 9 | Builder catalog | 13 | `test_capabilities_endpoint.py`, `test_capability_catalog.py` | ✅ PASS |
| 10 | Self-hosted onboarding | 15 | `test_onboarding_selfhost_w12.py` | ⚠️ PASS at the automated layer — a hand-driven self-hosted install (surface 15's manual leg) was not run |
| 11 | Capture disclosure (self-use) | 11 | `test_onboarding_w6_capture_disclosure.py`, `test_capture_disclosure_template.py` | ⚠️ PASS with one flake (deviation D2) |
| 12 | Cross-W journey (W5-owned) | 1-17 (all) | `test_dashboard_onboarding.py` full-journey specs + `test_decide.py` | ✅ PASS (browser walk) |

E2E-1 and E2E-12 are **walked in a browser**; the other ten are verified through the suites their surface map assigns them (integration, contract, skill lint, unit). "Walked" is not claimed for a scenario driven only by pytest.

**Surfaces: 17 of 17 exercised at their assigned *automated* layer; 16 green; 1 that has never passed under load; 3 whose manual leg was not fully run.** Stated precisely, because "verified" is a claim about a sha and about a pass:

- **Which sha:** batches **A and B** ran at the anchor commit `fbc1d96d5` — surfaces **1, 2, 7, 9, 10, 11, 14, 15, 16**; batches **C, D and the browser walk E** ran at `180fe6fd3` — surfaces **3, 4, 5, 6, 8, 12, 13, 17**. (Surface 4's evidence is the fork-card specs inside `test_dashboard_onboarding.py`, i.e. the browser walk, so it is a `180fe6fd3` surface.) The two heads differ only by main's own merges in between, none touching onboarding code, and the per-batch file lists in lane conditions let any batch be re-derived at either head. The **browser walk — the evidence for the epic's own UI claims — is all at `180fe6fd3`**.
- **Which pass:** **surface 7 (seed) has never passed under load** — one of its 54 tests returns a 504 wait-budget error (D1, 5/5 reproductions, green in 9.08 s on a quiet host). Surface 11 (capture disclosure) failed one test in a batch and passed alone (D2). **Surface 16** also failed one test in batch B — `test_installer_dist_is_a_build_artifact_not_a_committed_mirror` (D3) — and passed on a re-run at `180fe6fd3` (1 passed in 12.36 s) after the stale build output was replaced by a fresh one.
- **Which layer was not reached:** **three** surfaces carry a **manual** leg in the test-design map that this run did not fully execute — 5 (install across six harnesses), 15 (a hand-driven self-hosted install), and 16 (the manual copy sweep, which the desktop capture only *partly* covers). Their automated layers are green — see the rows.

Two things are **outside** the 17 and are not counted in that number, each named with its evidence: the **deployed-host** auth/identity journey (deviation D4 — those five specs expect the production auth origin, the local build redirects to its own, and this lane has no production session), and the **W11 funnel read** (indicator 5 / A0 gate data — hosted capture returns 402, so the events of the sessions that would populate the funnel are never posted). Surface 14 is the telemetry *machinery*, and that is verified; the funnel *read* is a different artifact and is not.

The `Surfaces` column is the map's own list of surfaces each scenario **exercises**, copied verbatim from
`04-test-design.md` — not a list of what this run's evidence happened to touch. The two differ, and the difference is
deliberate: E2E-12 is mapped to "1–17 (all)", while the browser walk that evidences it drives surfaces 3, 4, 9, 10, 16
and 17 directly and the rest through their assigned suites. Reading the column as coverage rather than traceability is
the mistake it invites, so it is labelled.

## Per-surface evidence

Commands below ran from `.worktrees/capstone-2008-1976` (clean checkout of the verified commit) unless marked, with
`L="$(.venv/bin/python tools/test_lane.py uri)"; TORTOISE_DB_URI=<lane> .venv/bin/python -m pytest …`.

| Surface | Assigned layer | Suite(s) | Result |
|---|---|---|---|
| 1 OnboardingState graph node | Integration (docker) | `test_onboarding_state.py`, `test_onboarding_state_cas.py`, `_split_init/_split_checkpoint/_split_patch/_split_projection` | ✅ 6 files green in a 201-test batch (1 unrelated failure, D1) |
| 2 Legacy store migration | Integration + regression | same split suite (migration/projection) | ✅ PASS |
| 3 Org-create transaction | Integration + hosted-e2e | `test_one_free_org_entitlement.py`, `test_free_team_entitlement.py`, wizard org-create specs | ✅ 170-test batch green |
| 4 Fork card | hosted-e2e + unit | fork specs inside `test_dashboard_onboarding.py` | ✅ PASS |
| 5 Universal setup command | Contract + integration + **manual (6 harnesses)** | installer suites + `test_served_skills.py` | ✅ contract + integration PASS · ⚠️ **manual 6-harness leg not executed** (the installer's per-harness arms are asserted; nobody sat in front of six harnesses) |
| 6 Agent install skill | Skill lint + integration | `test_served_skill_byte_parity.py`, `test_served_skills.py`, `test_installer_agents_block_3429.py` (42) | ✅ PASS — both SKILL copies byte-identical |
| 7 Seed — two Subjects | Integration + hosted-e2e + unit | `test_onboarding_seed.py`, `test_onboarding_seed_endpoint.py` | ⚠️ 53 passed, 1 failed (54 tests): `test_email_derived_person_requires_confirmation_no_writes` → 504 wait budget (D1) |
| 8 Decide nudge | Integration + walk-through | `test_decide.py` | ✅ PASS |
| 9 Overview calm | hosted-e2e + ux-verification | `test_onboarding_w4_settings.py` + screenshot | ✅ PASS |
| 10 Settings tab (owner) | hosted-e2e + integration | `test_onboarding_w4_settings.py` + screenshot (Settings **button in the 6-button nav**: Overview · API Keys · Graphs · Members · Billing · Settings; the source comment's "7th tab (R2-11)" is a design ordinal, not the rendered count) | ✅ PASS |
| 11 Capture disclosure | Integration + hosted-e2e | `test_onboarding_w6_capture_disclosure.py`, `test_capture_disclosure_template.py` | ⚠️ 1 flake, passes alone (D2) |
| 12 Invite-accept fusion | Integration + contract + hosted-e2e | `test_invite_fusion_http.py`, `test_invites_http.py`, `test_invites_email_http.py` | ✅ 123-test batch green |
| 13 Builder catalog | Unit + integration + contract | `test_capabilities_endpoint.py`, `test_capability_catalog.py` | ✅ PASS |
| 14 Onboarding telemetry | Integration + regression | `test_onboarding_telemetry_w11.py` | ✅ PASS |
| 15 Self-hosted onboarding | Integration + **manual** | `test_onboarding_selfhost_w12.py` | ✅ integration PASS · ⚠️ **manual leg not executed** (a real self-hosted install was not driven by hand this run) |
| 16 Wizard shrink + copy sweep | hosted-e2e + manual | `test_onboarding_variants.py` + screenshot ("Your Organization is live") | ⚠️ PASS — 1 test in this file (the dist guard, D3) failed at `fbc1d96d5` and passed on re-run at `180fe6fd3`; **proven** a stale-build artifact, not a code defect. Manual leg **partly** covered: the capture shows the copy sweep, not every shrink variant |
| 17 Cross-W full journey | hosted-e2e + walk-through | `test_dashboard_onboarding.py` — **40 passed** | ✅ PASS |

**Totals, stated so the arithmetic can be checked:** **654 in-scope passed** (200 + 121 + 123 + 170 + 40) **plus 22 out-of-scope in batch F = 676** across the batches below (200 + 121 + 123 + 170 + 40 + 22), and **1 skipped**, which is also batch F's. Failures split by scope: **3 in-scope failures** — batch A's 1 (D1) and batch B's 2 (D2, D3), each named and reproduced — plus **batch F's 40 failures, which are D4 and outside the epic's 17 surfaces**, listed separately rather than netted into the in-scope count. Batch F also contributes the single skip, so it is counted for passes and skips but not folded into the in-scope failure tally.

```text
A  200 passed, 1 failed   state ×6 + seed ×2                      (D1)
B  121 passed, 2 failed   w4_settings, variants, w6_capture,
                          capture_disclosure_template, w11,
                          selfhost_w12                            (D2, D3)
C  123 passed             invites ×3, capabilities, catalog, dist-guard
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

What the walk exercised, per spec: the 5-step wizard, the fork card **once per org** (asserted as the only checkpoint
for that org), connect step for owner/admin vs member, paste refusal paths (revoked/disabled/bootstrap rows never
advance), keyless Claude Desktop/Web paths, the done screen's incomplete-setup variant, and the mobile viewports
(320/360/390/414) with no horizontal overflow in the fork key row.

One item from the test-design map's anti-behavior column is **not** exercised by the walk and is called out as D6
below: "fork triggers billing/upgrade demand". The suite's org projection hardcodes `subscription_status: "active"`,
so no test varies the plan to prove the card is reachable without one — the property is a design decision
(`00-align-and-scope.md` §116, `04-test-design.md` S4), not a verified one.

**Screenshots** (captured from the same preview, `/tmp/capstone-shots/`):

- `onboarding-desktop.png` (1440×900) and `onboarding-mobile.png` (390×844) — the continuation card on Overview:
  "Continue setting up <org>", tabs `Overview · API Keys · Graphs · Members · Billing · Settings`, a `Setup` button,
  a `Free tier` badge, and the copy sweep visible in the body ("Your **Organization** is live"). No feature toggles
  on Overview — the calm surface holds.
- `dashboard-desktop.png` (1440×900) and `dashboard-mobile.png` (390×844) — the **degraded** path: with a session but no
  reachable BFF, the dashboard renders "Could not load your organizations — try again" with a contact link. That is the
  honest failure the surface map's S9 row requires (never a blank page, never a false checklist), and the mobile capture
  confirms it does not overflow at 390 px.

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

**UI surfaces exercised but not captured:** surface 4 (fork card — asserted by `test_fork_card_is_shown_once_per_org`
and its companions), surface 11 (capture disclosure — asserted), surface 3 (org-create, driven by the wizard's
org-create specs in the same walk) and surface 17 (the full journey, driven by the 40 walked specs). All four were
exercised in the browser; none has an image. Surfaces 1, 2, 5–8, 12–15 are not UI surfaces in this deploy, so a
screenshot is not the assigned evidence for them.

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
| **D1** | `test_onboarding_seed_endpoint.py::TestSeedEndpoint::test_email_derived_person_requires_confirmation_no_writes` returns **504 "the server's wait budget for this request was exceeded"** under load — **5/5** reproductions; the same test passes in **9.08 s** on a quiet host | `--lf` ×5, each run 132–136 s, load average 26→45; body: `assert 504 == 200` | **A product-latency symptom until measured otherwise, tracked on #9008.** What was measured is the *test's* wall time (132–136 s vs 9.08 s), not the endpoint's own response latency under load — the request never returned a response, the server gave up on a budget. So the classification the data supports is: *under fleet load this endpoint exceeds its own wait budget*. Whether that budget is too small or the endpoint is too slow is the open question on #9008, and the next measurement is the endpoint's server-side latency under load (the test evidence cannot distinguish the two) |
| **D2** | `test_onboarding_w6_capture_disclosure.py::TestDeleteDuringCapture::test_no_orphaned_receipt_under_concurrent_delete` failed in the 6-file batch and **passed alone** (133 s) | batch B + isolated re-run | Load-sensitive; re-run green. Not written off — D1 is the same mechanism |
| **D3** | **PROVEN, single variable.** `test_onboarding_variants.py::test_installer_dist_is_a_build_artifact_not_a_committed_mirror` compares a built `dist/` copy against `public/` **when `dist/` exists**. In the hub checkout: `dist/install-tortoise-skills.sh` is dated **28 Sep**, `public/` is dated **18 Oct** (it gained the `#4643` "WHICH REPO AUTHORS WHAT" header), so they disagree and the test reds. In a checkout built from the same lineage, `dist/` == `public/` byte-for-byte and the test passes | `ls -lT` on both files (28 Sep vs 18 Oct); `diff dist public` shows exactly the missing `#4643` header; `cmp -s` in the fresh worktree → identical; the same test re-run in that worktree → **1 passed in 12.36 s** | **Environment artifact, proven not a main defect** — one variable (the age of an ignored build output), measured on both sides. It is also a genuine trap worth knowing: **any lane that built the dashboard once will red this test the next time `public/` changes on main**, with a message that blames "a BUILT dist copy" rather than naming the remedy (`rerun the dashboard build` — which is the remedy, and it is in the assertion text) |
| **D4** | (batch F) `tests/e2e/{test_welcome_page,test_ship_test_onboarding,test_dashboard_gate,test_dashboard_identity,test_session_login_flow}.py` → 22 passed, 40 failed, 1 skipped | first failure: expected `https://tortoise.premiselabs.co/auth`, actual `http://127.0.0.1:8790/auth` | These expect the **deployed** auth origin; the local build redirects to its own origin. They are auth/identity/gate surfaces, **outside #1976's 17** — their hosted layer is the deployed app, which this lane cannot drive (no prod session). Recorded as unverified here rather than claimed |
| **D5** | The dashboard's degraded state says "Could not load your organizations — try again" without naming what failed | `dashboard-desktop.png` | Observation, not a defect and **not filed** as an issue: the copy is actionable and honest, and the surface map asks for a degraded state, not a diagnosis. Recorded here only |
| **D6** | The anti-behavior **"fork triggers billing/upgrade demand"** (`04-test-design.md` S4) has **no covering test** — filed as **#8998**: `test_dashboard_onboarding.py` hardcodes `subscription_status: "active"` and never varies the plan, so the "never a billing gate" property is asserted in the design docs but not proven by any suite | `rg -n "billing\|subscription_status" tests/e2e/test_dashboard_onboarding.py` → only the `active` mock at `:211` | **Gap, not a regression.** The card's own copy and flow contain no billing reference, so nothing suggests the behavior is broken — but a future change could introduce one and every current test would stay green. This is the single concrete test-coverage gap this capstone found |

## Residuals carried forward (not this capstone's to close)

- **D4** (the deployed-host auth/identity journey) has **no owner and no automated home**: CI's `dashboard-e2e` leg runs a
  local wrangler preview, so it cannot exercise the production auth origin either. Two ways to close it, both out of this
  lane's reach — (a) run those five specs once from a machine holding a production session, or (b) make their auth origin
  configurable and point it at a preview deployment that carries the same redirect config. Recorded here so it is not
  mistaken for covered by the green 40/40 above.
- **#1992** (the surface map itself) is still open — its artifact is delivered in the issue; closing it is bookkeeping.
- **D1 is tracked on #9008** (filed by this capstone). It was **not** left on #8000: that issue is **closed**
  (2026-10-16) and is about a *different* test (`test_capture_loop_responsiveness`), so the recurrence comment posted
  there on 2026-10-18 landed on a dead thread two days after closure. #9008 names the measurement that classifies it
  (server-side latency under the same concurrency, distinguishing "budget too small" from "endpoint too slow").
- **D6** (no test varies the subscription plan, so "never a billing gate" is unproven) is **filed as #8998**, with the
  test to write and the mutation that must redden it. Not written here: this capstone's job is to verify, not to extend
  the suites.
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

**PASS for the epic's own surfaces, with the coverage stated exactly.** 17 of 17 surfaces were exercised at their
assigned **automated** layer and **16 are green**; the one that is not — surface 7, the seed endpoint — has never passed under load
(D1). Three surfaces carry a **manual leg this run did not fully execute** (5, 15 and 16; all green at their automated layers), and
the batches covering surfaces 1, 2, 7, 9–11 and 14–16 ran at the anchor commit `fbc1d96d5`, while the browser walk and
the remaining suites (surfaces 3–6, 8, 12, 13, 17) ran at `180fe6fd3` — see the sha grouping above. All 12 high-level E2E scenarios are covered with
evidence; **2 of them (`E2E-1`, `E2E-12`) were walked in a real browser** (40/40), the other ten through their assigned
suites. The two UI claims the epic rests on — a calm Overview and a Settings home — are confirmed on screen as well as
in assertions.

Of the three in-scope failures: **D2** was green only on re-run (a load-sensitive flake), **D1** never went green under
load (5/5 reproductions — it passes on a quiet host, which is why the classification is unresolved and tracked on #9008),
and **D3** was **proven** an environment artifact with a single-variable control (a 28 Sep build output against
an 18 Oct source; the same test passes in a checkout built from the same lineage). One *layer* — the deployed
auth/identity journey, D4 — was not driven at all because it needs a production session. The W11 funnel read
(indicator 5) is unmeasurable while hosted capture returns 402.

**The capstone's (#2008) five verification indicators as a whole** — these are *not* epic #1976's own O/I/T indicators,
which are a different set and are mapped below:

| #2008 verification indicator | Status |
|---|---|
| 1 — every high-level E2E walked with evidence | **met with a caveat**: 12/12 have evidence, but only **2/12 were walked in a browser** (E2E-1, E2E-12); the other ten were verified through their assigned suites. The target's word is "walked", so the caveat is stated rather than rounded up |
| 2 — every integration surface through its assigned layer | **met with caveats**: 17/17 exercised at the automated layer, 16 green, surface 7 never green under load (D1), surfaces 5, 15 and 16 have an unexecuted or partly-covered manual leg |
| 3 — logs + DB state at each step | **partial**: graph state is asserted at suite granularity (Subject/label counts, the Overview digest's `_point_count`), not captured as a per-step transcript |
| 4 — screenshots per surface | **partial**: 4 captures covering surfaces 9, 10, 16 and the degraded state; the fork card (4) and capture disclosure (11) are asserted, not captured |
| 5 — A0 gate data (friction + W11 funnel) | **partial**: friction observations delivered; the funnel read is blocked by the hosted-capture 402 |

**Epic #1976's own indicators, for the reader holding the epic** (different numbering, all covered): (1) the one-sitting
first-run — E2E-1 + E2E-12; (2) Overview calm — surface 9 + `test_onboarding_w4_settings.py` + the capture;
(3) org-name captured as an explicit step — E2E-3 + surface 3; (4) self-vs-build fork asked as a deployment question —
surface 4 + `04-test-design.md` S4; (5) the seed teaches Object-vs-Statement with the user's own data — E2E-4 + surface 7.
None of the five is claimed as fully met *as a capstone verification indicator*, and none of the epic's own five is
uncovered.

**What this report does not do:** it does not mark the epic complete, it does not claim all five indicators are met, and it
does not close D6 (filed as #8998). Completion needs the owner's reading of D1 (is a
504 under load a product promise or a test contract?) and of the funnel gate — neither of which is a lane decision.
