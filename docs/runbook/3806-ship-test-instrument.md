---
title: "Ship-test instrument — per-deploy onboarding walk (#3806)"
type: runbook
domain: operations
doc_status: live
created: 2026-09-18
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Ship-test instrument (#3806)

> **Done = merged. Shipped = deployed and observed.** A report, a clean review,
> a green local run, or an opened PR is not evidence. This instrument is what
> turns "shipped" into an **observation** with an artifact attached.

## What it is

The smallest thing that runs per deploy: a scripted clean-browser walk plus a
recorded observation. It is **not** a test framework, not a suite, and **not a
new CI gate** (the issue scopes it that way deliberately).

Three assertions — the third is the one that matters:

1. **A clean browser can reach signup** — the signup CTA is the element a
   top-of-stack click actually lands on (the `#3781` overlay class is invisible
   to every local test).
2. **The walk completes** — signup → wizard → the final screen renders.
3. **"Connected" appears ONLY when the server observed it.** The negative
   direction is the point: before any server-observed connection, no surface may
   claim one. A walk that only checks the positive path passes on a lying UI.

Evidence standard: it executes the **real browser path against the real
deployment** and asserts the **resolved** state a user would see. No `--mock`,
no proxy that silently substitutes a different backend.

## Run it (per deploy)

```bash
python tools/ship_test_onboarding.py \
  --base-url https://app.premiselabs.co \
  --auth-url https://tortoise.premiselabs.co \
  --api-url  https://api.premiselabs.co \
  --allow-prod --out review-artifacts/ship-test
```

* Exit `0` — every assertion passed. Exit `1` — a failure or an incomplete walk.
  Exit `2` — usage/refusal (a non-loopback target without `--allow-prod`).
* A loopback target (a local or self-hosted deployment) needs no `--allow-prod`.
* `--headed` to watch it; `--skip-agent-write` to run only the negative half.

The walk is stub-free. It signs up a fresh account (rate limit: 3/hr/IP),
walks the wizard, reads the server's own `/v1/onboarding/state`, makes a **real
MCP `tortoise_create_point` write** (the server's
`mcp_server.py::_maybe_onboarding_auto_complete` files `harness-connected` from
it), then reloads and reads Overview again. It never writes the onboarding
checkpoint itself — that edge is the server's observation, not the client's.

## The observation artifact

`<out>/observation.json` plus per-step screenshots. The record carries:

| Field | What it is |
| --- | --- |
| `started_at` | Timestamp (UTC) |
| `target` | The dashboard / auth / API origins observed |
| `deploy_sha` | **The deployed SHA** — the deployment's own revision, read from the public `GET {api}/v1/version` (`commit_sha`, baked into the release env at deploy time by `deploy-hosted.yml`: `TORTOISE_GIT_SHA=${GITHUB_SHA}`) |
| `bundle` | The content-addressed dashboard client asset served (a second, dashboard-side anchor) |
| `sha` | The instrument's own git revision (which revision of this tool produced the record) |
| `steps[]` | Per step: name, URL, resolved `ui` state, `observed`, `ok`, detail, screenshot |
| `assertions` | `front_door_reachable`, `walk_completed`, `no_claim_before_observation`, `shown_when_observed` |
| `verdict` | `passed` / `failed: …` / `incomplete: …` |

`verdict` is `passed` only when **both** directions are proven: nothing was
claimed before the server observed the write, the server *did* observe it
(`harness-connected` filed), and the screen *showed* it. A hidden connection is
`failed`; a positive read that never resolved is `incomplete` — never `passed`.

## The guard's own tests (what keeps the probe honest)

| Where | What | Count |
| --- | --- | --- |
| `tests/test_ship_test_onboarding.py` | Fast pure-Python: the classifier, the page-wide claim sweep, the DOM reader, the server-observation reader, the per-surface verdict seam, the verdict assembly, the CLI safety contract, and RED/GREEN mutation evidence | 68 |
| `tests/e2e/test_ship_test_onboarding.py` | Real-browser, opt-in (`RUN_DASHBOARD_E2E=1`): the three assertions against the deployment's own built bundle, the wire observation that the client issues no `harness-connected` write, and RED/GREEN evidence against a mutated COPY of the real bundle | 8 |

Both execute the instrument's **real decision code** — never a source-text scan.
The RED/GREEN property is the core requirement: a behaviour-identical reformat
must not move the verdict, and a UI that lies must go RED.

Guard self-check, no browser needed:

```bash
python tools/ship_test_onboarding.py --mutation-selfcheck
```

Browser guard tests:

```bash
cd website/apps/dashboard && npm run build      # the module serves this dist
RUN_DASHBOARD_E2E=1 TORTOISE_TEST_CARVE_OUT=1 \
  python -m pytest tests/e2e/test_ship_test_onboarding.py -v
```

## CI posture

**Not a gate.** The instrument itself is run per deploy (the command above) and
its output reviewed — it is not wired as a blocking check. The *guard tests*
that keep the probe honest run in the normal lanes: the fast module in the
pytest lane, the browser module in the existing `dashboard-e2e` job (which
already builds `dist/` and boots both previews, so the addition costs seconds
and adds no new job).

## Open gaps

* **The `_maybe_onboarding_auto_complete` premise is not pinned by a hermetic
  test here.** The instrument *assumes* a real MCP `tortoise_create_point` write
  makes the server file `harness-connected` (and, today, also
  `first-points-filed` + the fork's final step, and flips status complete). That
  is a `mcp_server.py` behaviour, covered by its own tests, not by this
  instrument; a regression there would make the positive direction
  "incomplete" rather than falsely pass, which is the safe failure. The premise
  is under active change — **#3784** (the auto-filed `decide-completed` false
  fact) and **#3670** (a REST-first build fork can never file
  `harness-connected`). The hermetic pin belongs with #3784's fix, not here.
* **The walk is heuristic.** It advances the wizard by clicking a set of known
  button labels; a wizard copy change makes it report `incomplete` (the safe
  failure) rather than a false PASS.
* **The two shipped derivations differ, and the guard must pick per surface.**
  The Overview accepts the server's wire-complete forms
  (`overview.js::overviewConnection`); the wizard is edge-only
  (`main.jsx::serverHarnessConnected`). The instrument judges each surface with
  its own vocabulary (see `connection_surface_kind`); a third derivation added
  later would need the same treatment.
