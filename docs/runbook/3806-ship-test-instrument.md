---
title: "Ship-test instrument — per-deploy onboarding walk (#3806)"
type: operations
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

* Exit `0` — every assertion passed. Exit `1` — the product was measured and
  found wanting (a failed assertion, or the server never observed the write).
  Exit `2` — usage/refusal (a non-loopback target without `--allow-prod`).
  **Exit `3` — the run could not exercise the product** (no signed-in session, a
  failed agent write, or an unreadable server projection).
* Exit `3` is the #4291 loud-failure guard: an instrument/infra fault says
  nothing about the product, and must never read as a product finding. The
  same split is on the record as `reason` (`instrument_error` vs
  `server_did_not_observe`), so a deploy job can branch without parsing prose.
  Each exit-3 cause is named in the verdict — e.g. `not_signed_in`,
  `agent_write_failed`, `projection_unreadable`.
* A loopback target (a local or self-hosted deployment) needs no `--allow-prod`.
* **Teardown is ON by default.** The run reaps the org it created (see below).
  `--keep-org` turns it OFF and leaves the org behind on purpose — use it only
  to inspect a FAILED run. **A teardown `failed`/`not_confirmed` is a cleanup
  fault, not a product failure**: it never changes the verdict or the exit code,
  and it is printed loudly on stderr (`RESIDUE — …`) so nobody has to infer it.
* `--headed` to watch it; `--skip-agent-write` to run only the negative half
  (which is recorded as `positive_not_attempted`, never as a server-side
  no-observation); `--agent-key tt_…` to supply the **agent write** credential
  (CLI-only, deliberately not env-settable) instead of minting one through the
  session. It does **not** carry the projection read.
* **The browser teardown is bounded at 30 s** (`TEARDOWN_BOUND_S`) and its outcome
  is recorded as `browser_teardown` — see *Browser teardown* below. It is cleanup:
  it never changes the verdict or the exit code.
* A degraded session store answers 503 on `/api/v1` while `/api/session` still
  answers 200, and a bad or graph-bound `--agent-key` fails the MCP write: both
  are instrument faults (exit 3), and both used to be graded as product
  findings. (A key for a DIFFERENT org is a separate, documented limitation —
  see Open gaps.)

### How the walk authenticates (#3501 / #4054)

The instrument holds **no session credential**. After the session seam, the
browser's
session is an opaque **HttpOnly `__Host-session`** cookie — unreadable from JS
by design, and host-only, so it can never be replayed at the API origin. The
walk therefore authenticates the way the app does: the **browser's own cookie
jar** (`ctx.request`) against the app origin's own `GET /api/session`, then
reads the server's truth through the same-origin `/api/v1` BFF proxy, which
mints the credential server-side. It does obtain an **agent** key (the
credential under test) for the MCP write — minted through that same proxy.

The server's truth is **always** read through the walked session, with no
key-based alternative: if `--agent-key` could carry the read, a key for org B
while the browser walks org A would let a lying org-A UI be judged against
org-B's projection and report `passed` — a false pass in the instrument's core
function. One identity, one read.

The retired `sb-*-auth-token` read (cookie or `localStorage`) is **gone**, and
a test fails if it returns: it is what made the positive direction
unexercisable while the run still reported a product-facing `incomplete`
(#4291). `GET /api/session`'s own 401/503 split is preserved — 401 is "not
signed in", 503 is "the session store is unreachable" — and either is an
instrument error (exit 3), never a product finding.

The walk is stub-free. It signs up a fresh account (rate limit: 3/hr/IP),
walks the wizard, reads the server's own onboarding projection through the
app origin's `/api/v1` BFF proxy, makes a **real
MCP `tortoise_create_point` write** (the server's
`mcp_server.py::_maybe_onboarding_auto_complete` files `harness-connected` from
it), then reloads and reads Overview again. It never writes the onboarding
checkpoint itself — that edge is the server's observation, not the client's.

**Teardown (#4319).** After the reads are done the run **reaps the org it
created**. The identity of "the org this run created" is proven
**differentially**, never by its name: the walked session's own org list
(`GET /api/v1/organizations`, through the same-origin BFF proxy) is read once
**before the wizard can create anything** and once at teardown, so this run's
orgs are exactly the set difference. That set must be exactly one org, this run
must have **actually attempted the org-create** (a set difference alone is not
an identity proof — an org that joined this session's list without this run
asking for one is not this run's to delete), and only then is its name compared
to the name this run wrote into the wizard. A name on
its own is not a proof of creation: `--org-name` and the provisioning lane's own
upsert can both put this run's name on an org this run did not create. The
delete goes to `DELETE /api/v1/organizations/{org_id}` through the **same**
origin's proxy as the org's owner, and `deleted` is recorded only after a
readable re-read shows the org gone from that same list (the list is derived
from active memberships, and the delete cascade removes them).

## Browser teardown — the bound, and what it leaves (#4907)

The instrument **owns** the Playwright driver's lifecycle (`sync_playwright().start()`
plus an explicit teardown) instead of inheriting it from a `with`. The closes are
unbounded in the API — `Browser.close()` is a timeout-less `send` — and cannot be
interrupted, so the bound comes from outside them: a watchdog thread signals the
run's **own** Playwright driver child, which is what releases a close blocked
against an unresponsive driver.

* **The bound is 30 seconds** (`TEARDOWN_BOUND_S = 30.0`, seconds). The ladder is
graceful first: `SIGTERM` at B/2 (15 s), then `SIGKILL` at 3B/4 (22.5 s), so the
run returns by B. A healthy run's teardown (~2.6 s measured) finishes well inside
the first rung, so a healthy run sends no signal and does not wait out the bound:
the graceful window is ~6x the healthy path, while a close that never returns is
still hard-bounded.
* **Only the run's own child is ever signalled.** The pid's identity — its parent
pid AND its start time, read together in a single `ps` call — is captured once,
as a direct child of the instrument's own process; that whole identity is
**re-read as one value immediately before every rung** (a bare pid is racy
against reuse, and a start time read separately can belong to the process that
reused the pid). With no child enumerated the watchdog signals nothing, and —
when the close then returns — records `driver_absent`, whose detail names whether
no candidate was found or an enumerated child's identity could not be read.
Only after a signal is the child reaped.
* **The bound holds even with nothing to signal (`abandoned`).** The ladder is
released by a *signal*, so if no driver child is enumerable there is nothing that
can release a close which never returns. After the final rung, with a close still
blocked, the run writes the record (`outcome: abandoned`, naming whether a child
was found) and **ends itself with its own exit code** rather than hang forever —
a cleanup fault may not move the verdict (#4319). The window is bounded either
way; what changes is that the process, not the driver, provides the bound.
* **The watchdog touches no Playwright object** — only the signal call and its own
record — so the sync API's thread-affinity rule holds.
* **The outcome is recorded, in a closed vocabulary.** `browser_teardown.outcome`
is one of `not_run` | `clean` | `close_error` | `watchdog_kill` |
`driver_absent` | `abandoned`, and `browser_teardown.closes` names each closer (`context`,
`browser`, `playwright`) with its `how` and any exception. A non-clean outcome
prints a `BROWSER TEARDOWN — …` line on stderr. **It never changes the verdict or
the exit code** — cleanup is not the product (#4319's rule, applied to the
browser).
* **The artifact is written twice, atomically, for a run whose walk settled into
the pre-teardown write.** A complete PRE-teardown document (outcome `not_run`) is
written before the teardown starts, and the authoritative one after it. A run
killed inside the ≤30 s window therefore still leaves a complete, parsable
artifact saying the teardown never finished — the `not_run` window is disclosed,
not discovered. That is a qualification, not a universal: a walk body that raises
before it settles writes no pre-teardown copy, and its exception propagates past
the authoritative write, so that run leaves no document at all.
* **Residue, not closed (#4928).** A run killed inside that window **while the
driver is unresponsive** still orphans the driver and its Chromium children: no
in-process code runs after the kill, and the frozen driver cannot read the stdin
EOF that would otherwise make it exit. This is the E10 leak from the issue's
scoping, and it is disclosed rather than papered over — a reaper that outlives the
run (an external supervisor, or a prune-on-next-run step) is a separate change,
deliberately not this one.

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
| `session` | How the run authenticated: `state` (one of `signed_in` / `not_signed_in` / `store_unavailable` / `unreachable`), `detail`, `mechanism` |
| `teardown` | The run's own cleanup outcome (#4319). `status` is one of `deleted` / `skipped_no_org` (nothing was created) / `not_reached` (no browser context, or the run exited before the cleanup baseline was read) / `kept_by_flag` (`--keep-org`) / `baseline_unavailable` / `not_listed` / `not_attempted` / `list_unreadable` / `ambiguous` / `name_mismatch` / `http_refused` / `not_confirmed` / `failed`. Every status except `deleted`/`skipped_no_org`/`not_reached` means a live org may remain and is warned on stderr. The keys carried depend on the status: `org_id` on `deleted`/`name_mismatch`/`http_refused`/`not_confirmed`; `http_status` + `upstream_status` on `list_unreadable`/`http_refused` (an upstream 429 arrives as a 503); `verify_status` + `verify_upstream_status` on `not_confirmed`; `created_ids` on `ambiguous`/`not_attempted`; `before_count`/`after_count` on `not_listed`; `grace_hours` + `hard_delete_after` on `deleted` |
| `browser_teardown` | The BROWSER teardown's outcome (#4907), distinct from the org reaper's. `outcome` is one of `not_run` / `clean` / `close_error` / `watchdog_kill` / `driver_absent` / `abandoned`; `closes[]` is `{name, how, detail}` per closer (`context`, `browser`, `playwright`); `detail` summarises a non-clean outcome. `not_run` is the PRE-teardown document's value — a run killed inside the ≤30 s window keeps it — and is never the value after a completed teardown. Any value other than `not_run`/`clean` is warned on stderr and never changes the verdict or the exit code. See *Browser teardown* above and **#4928** for the residue |
| `reason` | The failure CLASS — empty iff `verdict == "passed"`. `instrument_error` (exit 3, says nothing about the product) vs `server_did_not_observe` / `positive_not_shown` / `positive_not_attempted` / `walk_incomplete` / `walk_failed` (exit 1) |
| `verdict` | `passed` / `failed: …` / `incomplete: …` / `instrument-error: …` |

`verdict` is `passed` only when **both** directions are proven: nothing was
claimed before the server observed the write, the server *did* observe it
(`harness-connected` filed), and the screen *showed* it. A hidden connection is
`failed`; a positive read that never resolved is `incomplete` — never `passed`.
A run that could not exercise the product — no session, a failed agent write,
an unreadable server projection, no browser driver — is `instrument-error` with
`reason` `instrument_error`: it never claims `passed`, and never blames the
product (#4291). The reason defaults to `instrument_error` (fail-closed) and is
narrowed to a product class only where a path has proven it measured the
product.

## The guard's own tests (what keeps the probe honest)

| Where | What | Count |
| --- | --- | --- |
| `tests/test_ship_test_onboarding.py` | Fast pure-Python: the classifier, the page-wide claim sweep, the DOM reader, the server-observation reader, the MCP write-result reader (JSON **and** SSE framing, both tool-error shapes, notification frames), the per-surface verdict seam, the verdict assembly, the session seam (`/api/session` + BFF), the loud-failure guard (session, write, projection, driver) — plus **the real `run_walk` executed against a fake browser**, which pins the call site (which read it uses, with what credential, in what order) rather than grepping for it — **plus the teardown control set**: each threat class of the destructive surface (pre-existing org, foreign name, ambiguity, unreadable baseline, unreadable confirmation, ambiguous candidate, refused delete, residue-vs-clean, verdict conservation both ways, single-writer funnel, every `_finalize` exit executed and status-asserted) — **plus the bound's own set**: the wedge (context, browser, and `pw.stop()`), the raising closes, the healthy zero-signal run, the exact-pid/`driver_absent`/stale-start-time/reused-ppid cases, the ladder's rungs and its at-most-one-of-each, the exactly-one-entry count, the two no-browser paths, and the killed-inside-the-window document — plus the **hardening set**: a healthy run's margin over the measured teardown with no signal, the abandon path's printed summary, its write-conditional `observation →` line, its shared residue/browser warnings and its unconditional exit, a signal seam that raises, a walk that raises before it settles writing no document, the locale-dependent `%c` start time (four-token, six-token and space-padded renderings all agree with the re-check by construction), the refusal of a non-positive pid, the absolute `ps` path and its budgeted timeout, and the `ps`-**output** seam pins that keep the enumerator's correctness off the venue (a marked child winning over an earlier unmarked one, a marker-matching process that is not this process's child being skipped, a pid whose identity re-read names another parent being refused, and BOTH `ps` reads asking for unlimited width — a host truncates the last column, which is what cut the marker on the CI runner, #4956) — with the ONE live-process-table test retrying the real enumeration inside a short bound and re-reading the whole identity, so a venue whose `ps` renders the table differently cannot decide whether the instrument is correct — the symlink-refusing atomic write, and the scrubbed free text — every one reading `observation.json` **from disk** and asserting the recorded value — **plus the acceptance map**, which names for each of the nine criteria the test that proves it and pins the `_walk` exit count at 11, so a criterion cannot lose its covering test unnoticed | 216 |
| `tests/e2e/test_ship_test_onboarding.py` | Real-browser, opt-in (`RUN_DASHBOARD_E2E=1`): the three assertions against the deployment's own built bundle, the wire observation that the client issues no `harness-connected` write, and RED/GREEN evidence against a mutated COPY of the real bundle | 8 |

Both suites execute the instrument's **real decision code** (`judge`, the
verdict assembly, the classifier, the readers) — never a source-text scan of it.
The strongest pin in the fast lane is the **fake-browser `run_walk` suite**: it
executes the real walk end to end — no session, a failed write, an unreadable
projection at each of the three read sites (step 5, the poll, step 7), the happy
path, `--skip-agent-write`, an explicit `--agent-key`, and the teardown control
set — so the call site is
behaviourally fixed, not greped. The teardown control set is mutation-checked
(dropping the baseline set difference, or the create-attempted gate, turns it
RED), and so is the BOUND: a watchdog that never fires, fires at once, sends each
rung twice, collapses the rungs, signals a non-enumerated pid, skips the
identity re-check, signals with no child, or is omitted/moved out of the
`finally` — each reddens a named test. The ladder's ARITHMETIC is additionally
CI-provable on its own: `--mutation-selfcheck` exercises the real `_ladder` plus
five mutants of it. A few *structural* `inspect.getsource` / AST assertions remain for ORDERING
that the harness does not aim at (that the
session gate precedes the agent write, that the write-failure check precedes the
projection check), plus one completeness assertion over the walk's call sites.
That assertion parses the ASTs of `run_walk`, `_walk` and `_finalize` — not text —
so a behaviour-identical reformat or a renamed local cannot false-red it. It pins
the funnel invariant: the **authoritative** writer (`_finish`) is spelled exactly
once, in `run_walk`, AFTER the bounded teardown; the browser teardown is a
statement of the `finally` whose `try` body holds the `_walk` call; `_finalize`
is not called from `run_walk`; there is no bare
`getattr`/`globals`/`eval`/`exec`/`vars` call in any of the three; the run's single
`td` (built once by unpacking `_build_observation`) is handed to `_walk` and to
the teardown; each of `_walk`'s **11** `_finalize(` calls passes three positional
arguments whose third is `_walk`'s own `td` parameter; `_walk` spells neither
`_finish` nor `_write_observation`; and `_finalize` spells `_finish` never and
`_write_observation` exactly once (the pre-teardown write). It covers the cheap
forms only — a helper, an alias, a NON-Name binding (`import … as`,
`except … as`, `match … case _ as`) or an in-place field assignment gets past it —
so it is a
refactor guard, not a containment proof. What carries
the `not_reached`-reports-an-unreaped-org-as-clean class instead is the recorded
teardown STATUS — every `_finalize` exit is executed by at least one test, and
at least one of the tests reaching each exit asserts the recorded status,
including the five that no test reached until #4843 (the absent surface,
the screen that lies, no agent key, a signup CTA that is not hittable, and the
missing playwright driver). Two abort paths sit outside `run_walk`'s `try` and
write no artifact — an `--out` that cannot be created, and a driver that will not
start — and each has its own test now (#4875);
they complement the behavioural tests, they do not replace
them. The RED/GREEN property is the core
requirement: a behaviour-identical reformat must not move the verdict, and a UI
that lies must go RED.

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
* **`--agent-key` is for the walked org, and nothing verifies that.** The flag
  supplies the MCP write credential while the server's truth is always read
  through the walked session. A key belonging to a DIFFERENT org therefore makes
  the write land elsewhere and the walked session observes nothing, which is
  reported as `server_did_not_observe` (a product-class verdict) rather than an
  instrument fault — there is no key-scoped read to compare against, precisely
  because letting a key carry the projection read is the false pass that was
  fixed. Pass a key only for the org the walk signs up, or omit the flag and let
  the instrument mint one through the session.
* **Each run creates a production user + org, and the org is reaped by
default.** The run signs up a fresh disposable identity
(`ship-test-<ts>-<hex>@premiselabs.co`) and creates the org
`Ship Test <epoch>-<hex4>` (the random suffix makes a same-named pre-existing
org vanishingly unlikely for a default run; `--org-name` overrides the label
only, and is
validated against the product's own rule up front — exit 2 — because the server
rewrites a name it will not accept, which would make the created org silently
unreapable). Since **#4319** the run deletes that org
itself, as its owner, through the walked session's own BFF proxy — see
*Teardown* above. Three residual classes remain, and all three are explicit
rather than silent:
  * **`--keep-org`, or any run whose teardown did not confirm**
    (`baseline_unavailable` / `not_listed` / `not_attempted` / `list_unreadable` /
    `http_refused` / `not_confirmed` / …) leaves the org live. The observation
    records which, and stderr prints the `RESIDUE` warning. An unreadable org
    list at teardown **fails closed** (residue) rather than deleting on an
    unproven identity; an empty candidate set after a recorded create attempt is
    treated as suspect residue (`not_listed`).
  * **A stale baseline read is the one shape not detected.** The differential can
    only exclude an org it *saw*: if the baseline read returned a list that was
    already stale (omitting an org that was in fact there), and that org carried
    exactly this run's name, and this run's own create then failed, it would be
    the single candidate. The unique default name above makes the shape
    vanishingly unlikely, `--org-name` is validated, and ambiguity (two
    candidates) still refuses — but it is a race, not a proof, and it is stated
    rather than papered over.
  * **The crash window.** A run that dies after creating the org and before
    teardown leaves it behind, and no in-process code can reap it: the org
    belongs to a different (per-run) account and no credential for it survives.
    This is why teardown cannot be the whole answer to residue — but it bounds
    the residue to crashes instead of making it the norm.
* **Deletion is a SOFT delete, then a purge.** `DELETE /v1/organizations/{id}`
kills access immediately (API keys revoked, memberships removed, invitations
revoked) and stamps a grace window; the org's graph + control-plane rows are
hard-purged by the boot + hourly purge once that window elapses
(`TORTOISE_TEAM_DELETE_GRACE_HOURS`, recorded as `hard_delete_after`). The
disposable auth **account is not deleted** — the product deliberately does not
cascade an org delete into the account, and there is no auth-admin wiring to do
it. Backups are out of scope for this org: hosted backup eligibility is
`tier != 'free' AND backup_enabled`, and a ship-test org is created on a fresh
free account, so no backup pool is ever created for it (#4190 covers the
non-free case).
* **The delete budget is shared, and a refusal is diagnosable.** `team_delete` is
rate limited to 5/hour keyed on the client IP, and behind the BFF every
dashboard-originated delete presents the Worker's egress IP (the proxy strips
`cf-connecting-ip` / `x-forwarded-*`; the API trusts Fly's `Fly-Client-IP`), so
that budget is effectively shared across all dashboard deletes. An upstream 429
arrives at the proxy as a **503** — but the 503 body carries
`upstream_status: 429`, which the recorded `teardown.upstream_status` preserves,
so "rate limited" stays distinguishable from "store down".
* **The two shipped derivations differ, and the guard must pick per surface.**
  The Overview accepts the server's wire-complete forms
  (`overview.js::overviewConnection`); the wizard is edge-only
  (`main.jsx::serverHarnessConnected`). The instrument judges each surface with
  its own vocabulary (see `connection_surface_kind`); a third derivation added
  later would need the same treatment.
