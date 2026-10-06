---
title: "Scope — #4620 Pi capture seam executable verification"
type: decisions
domain: capability
doc_status: draft
created: 2026-09-22
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Scope — #4620: the Pi capture seam's executable verification

Issue: `daniel-ospina/tortoise#4620` (`complexity:standard`, `team:epistemic-team`)
Objective: `#1714` (objective 1, `complexity:complex`)
Tree: `b3334b560` (= `origin/main`); measured 2026-09-22.

## Gate 0 — A/B classification

**Not machinery → routes normally.** The A/B test classifies *pipeline machinery* only; the Pi
capture seam is product code (`tortoise/pi-hooks/`). This issue is product-capability verification
(does the objective's claim hold, and is it falsifiable?), not agent-infra gate/marker/process
machinery, so it routes through `task-workflow-standard`.

## Phase 1 — problem-diverge (candidate framings)

| # | Framing | Verdict |
|---|---|---|
| 1 | **As filed** — *"there is no headless trigger, and nothing loads the extension's seam in a test"* | **The second clause is FALSE; the first is TRUE.** The seam *is* loaded and fired in a test (see 2). What remains true is that nothing loads it *as installed*, and no real `pi` process is driven. |
| 2 | **Stale premise** — the seam IS executably verified: `tortoise/pi-hooks/tortoise-capture.test.ts` loads the module, registers it on a mock `pi`, fires `session_start`/`session_shutdown` with a session payload, and asserts the POST receipt; CI runs it via `tests/test_pi_capture_hooks.py` | **TRUE** (measured; the suite landed `7ea8874fb` on 2026-09-17, five days *before* the issue was filed) |
| 3 | **Owner's narrowing** — (2) is met for the seam's *logic*; the residual is the *wiring* half: a real `pi` process loading the *installed* extension | TRUE, but **not CI-able** and read-back-blocked (`#4661`) |
| 4 | **Installed-artifact gap** — every check exercises the seam at its **source** path (`tortoise/pi-hooks/tortoise-capture.ts`); **nothing executes the artifact as installed** (`~/.pi/agent/extensions/tortoise-capture.ts`). The "self-contained on purpose" claim is source-inspected (a grep for `agent-infra`) and installs are byte-equality-asserted, but the installed file is never *loaded and fired* | **TRUE — headlessly closable** |
| 5 | **Read path** — a live end-to-end capture cannot be scored | Blocked by `#4661` (504s) — context, not the work |

## Phase 2 — problem-converge (confirmed problem)

> **The issue's premise is half-stale — its test-coverage clause is false, its wiring clause is true.
> The real, still-open, headlessly-closable gap is framing (4): the executable verification never
> exercises the artifact at its **installed** location, and the shipped disclosure
> (`tortoise/session_verify.py::UNVERIFIABLE_REASON["pi"]`) overstates the wiring loss as "cannot be
> executed headlessly" — an absolute claim the seam's own suite falsifies.**

The live-wiring residual (framing 3) is genuine but not CI-able and is read-back-blocked by `#4661`;
it is recorded (below), never faked.

### Evidence (measured 2026-09-22, tree `b3334b560`)

1. **The seam suite passes, hermetically.** `node --test tortoise/pi-hooks/tortoise-capture.test.ts`
   → `# tests 51 / # pass 51 / # fail 0`, in a scrubbed `HOME` with no capture credential.
2. **It fires the real handlers.** `tortoise-capture.test.ts:49-50` imports `./tortoise-capture.ts`
   (the real module); `:305-314` fires `session_start` and asserts the probe POST; `:354-375` fires
   `session_shutdown` and asserts `url ≈ /v1/sessions`, `harness="pi"`, `session_id`, `source`,
   `model`, `conversation` — via an injected `fetch`.
3. **CI-enforced.** `tests/test_ci_selection.py::test_pi_hooks_change_selects_the_capture_guard`
   proves a `tortoise/pi-hooks/` change selects `core` and includes `test_pi_capture_hooks.py`;
   `config/ci-surfaces.yml:951` (core) and `:1104` (onboarding) register it; a real CI run
   (`docs/evidence/3770-…/ci-run-35588858762-test-b-pytest.log:1682-1687`) shows the whole file
   PASSED including `test_extension_behavioral_suite`.
4. **The installed artifact loads and fires** — the runnable attempt proving framing (4) is
   constructible: installed the seam into a temp `HOME`, copied the installed file alone into a bare
   dir, and ran a node probe that imported **the installed file**, registered on a mock `pi`, and
   fired `session_shutdown` → POST `/v1/sessions` with
   `{harness:"pi", session_id, source, conversation, machine_id, model}`, exit 0.
5. **No existing test loads the installed artifact.** `tests/test_capture_install.py:784-790`
   installs `pi` and asserts `dst.read_bytes() == _PI` (byte equality) but never loads it;
   `tests/test_capture_spool.py` imports the **source** path only.
6. **The shipped disclosure is over-broad.** `tortoise/session_verify.py:143-146`: *"Pi's capture
   seam is a TypeScript extension loaded in-process by Pi (…); it is not a script and **cannot be
   executed headlessly**."* Contrast the `HEADLESS_FIRABLE["pi"]` comment (`:120-128`), whose own
   docstring scopes the ruling correctly to *"not a script **this command** may execute"*.
7. **Blockers (not worked around).** `#4661` — the probe's read-back query 504s (`GET /v1/search`
   504 on 7/7 probes); note the read path is **query-dependent** (the B1 report measured
   `q=session` → 200 with real points), so the honest statement is "this probe's read-back refuses",
   not "reads are dead". Write cap `9981/10000` for period `2026-09` (HTTP 402). Neither is a finding
   and neither is re-litigated here.

### Root cause

A **coverage gap between "source artifact verified" and "installed artifact exercised"**, plus
shipped copy that describes the seam as *un-executable* rather than *un-launchable by this verifier*.
Both keep the stale premise ("no executable verification") alive.

## Adversarial Threat Surface

**(not adversarial)** — test coverage + operator-facing copy. No gate, no fail-open enforcement, no
attacker-defeatable path, and no adversarial `OVERRIDES:` ruling. (The one deliberate departure this
scope does carry is recorded in § Known residual of this scope's own test.)

## Phase 3 — codebase explorer / wiring check

| Surface | Change | CI selection (measured via `tools/ci_selection.py`) |
|---|---|---|
| `tortoise/pi-hooks/tortoise-capture.ts` | none | — |
| `tests/test_pi_capture_hooks.py` | + installed-seam fired check | `core` (pinned; `onboarding` too via `ci-surfaces.yml`) |
| `tortoise/session_verify.py` | reason / docstring / comment wording | **`core`** |
| `tortoise/pi-hooks/README.md` | + Verification / manual-residual section | `core` (via the `tortoise/` fallback) |
| `docs/scoping/…` + `docs/00_index.md` | new + index row | docs-only (validator warn-only) |

No new test file is added, so no `ci-surfaces.yml` registration is needed.

## Phase 4 — solution-diverge (candidate approaches)

| # | Approach | Tradeoffs |
|---|---|---|
| **A** | **Test-only** — add the installed-artifact fired check + a decision record | Closes the headless gap; leaves the over-broad shipped copy intact |
| **B** | **A + copy fix** — also reword `UNVERIFIABLE_REASON["pi"]` (+ the `HEADLESS_FIRABLE` comment and module docstring) to distinguish "this verifier cannot launch the harness's registration" from "the seam cannot be executed at all" | Closes the gap **and** removes the over-claim from product copy; small, pinned by a test |
| **C** | Wire the probe into `session verify --harness pi` (`pi --no-extensions -e <installed-seam>.ts -p …`) | **Deferred to `#4710`, not rejected as worthless.** It is the owner's own argument (2026-09-22 note 2: the manual procedure is the only stale-install detector). It is out of scope *here*: it adds a live `pi`+LLM dependency with a spend bound and a degrade path to `session verify`, and the Pi-claim is independently blocked by `#3713`'s de-duplication. Filed as `#4710`. |
| **D** | Record-only (issue option 2) — no code; declare pi manual-only | **Rejected** — *underclaims*: the seam IS executably verified, so declaring it manual-only would be as false as the original premise, in the opposite direction |

## Phase 5 — solution-converge (chosen: B)

**Chosen because it produces the better outcome, not the smaller diff:** the installed artifact is
executably verified (the last headlessly-closable gap), the shipped disclosure becomes accurate (the
premise is fixed where it lives), and the genuinely-manual residual is recorded with an exact
procedure for objective 1's done-state. C is a real improvement but a separate, larger change
(`#4710`); doing it here would mix a live-dependency change into a test-coverage issue.

### Deliverables

1. **`tests/test_pi_capture_hooks.py` — `test_installed_seam_loads_and_fires`.** Install `pi` into a
   temp `HOME` via `capture_install.install_capture("pi", home=…)`; run a node probe that imports
   the **installed** `<home>/.pi/agent/extensions/tortoise-capture.ts` with a mock `pi` and injected
   `fetch`, fires `session_shutdown` with a session payload, and asserts the receipt (`/v1/sessions`,
   `harness="pi"`, `session_id`, `conversation`). This is issue outcome (1) at installed fidelity.
   **Named mutation (the gap no existing test catches): path-dependent runtime module resolution** —
   adding `import { X } from "./helper.ts"` to the seam leaves the *source-located* suite green
   (51/51, the sibling resolves) while the *installed* single-file copy raises
   `ERR_MODULE_NOT_FOUND` (verified RED). `tests/test_capture_install.py` covers only bytes; the
   `agent-infra` grep in `test_extension_has_no_agent_infra_dependency` covers only that one name.
   Because the seam is fail-open, such an import would silently file nothing. When Node cannot strip
   TypeScript the check FAILS under CI and skips only locally — it does **not** inherit the older
   behavioral suite's silent-skip contract, whose deferral still governs that suite alone (see
   § Known residual of this scope's own test).
2. **`tortoise/session_verify.py`** — reword `UNVERIFIABLE_REASON["pi"]`, the `HEADLESS_FIRABLE`
   comment block (`:120-128`), and the module-docstring sentence so they say what is true: the
   seam's *handler logic* is exercised by the seam's own hermetic suite
   (`tortoise/pi-hooks/tortoise-capture.test.ts`, run by `tests/test_pi_capture_hooks.py`), while the
   *install leg* — a real `pi` process loading the installed extension — is not firable by this
   command. Keep the word "extension" (the existing pin at
   `tests/test_session_verify.py::test_pi_is_honestly_unverifiable` asserts it, and nothing asserts
   the rest of the reason text, so no existing test needs changing).
3. **`tortoise/pi-hooks/README.md`** — a **Verification** section: what is executably verified, what
   is **manual-only**, the exact manual procedure, who runs it, and the `#3713`/`#4661` caveats.
   This is the recorded decision for objective 1's (`#1714`) done-state.
4. **`tests/test_session_verify.py`** — a positive pin that the pi reason names the *suite*
   (`tortoise-capture.test.ts`) and the manual residual, so the accurate wording cannot regress to
   the over-broad claim.
5. **`docs/00_index.md`** — register this scope doc.

### Recorded decision (issue option 2, for the residual only)

> **Residual (manual-only):** that a real `pi` process loads the installed extension and invokes
> `turn_end`/`session_shutdown`. The procedure and its pass condition are canonical in
> `tortoise/pi-hooks/README.md` § Verification, and are deliberately not restated here. The live leg
> is blocked by `#4661` (read path) and `#4675` (receipt terminality), so objective 1 must not read as
> verified for Pi until they clear. Not CI-able (needs a live harness + an LLM call). Automating this
> probe is `#4710`.

## `#3713` — recorded call (issue requirement)

**Not touched, and not "a follow-up" in the dismissive sense: it is launch-blocking for the Pi
claim.** The recorded owner decision on `#3713` (comment `2026-09-22T17:20:18Z`) rules:

| Path | Verdict |
|---|---|
| Brand-new user, clean machine | **not blocked** |
| "Upgraded existing install" | **BLOCKED** (guard is one-shot; `pi-bootstrap` re-materializes the colliding directory) |
| The beta claim *"Pi capture works"* | **BLOCKED** — a false negative in the honesty contract, and the Pi evidence base is itself the colliding population |

The sequencing blocker that comment named — *"must land after #3971 merges"* — is **cleared**:
`#3971` merged as `73acefddf` (2026-09-22T17:21:23Z), an ancestor of this tree. The fix #3713 needs
is the owner-ruled **de-duplication to one producer** (give the product seam the local/self-host leg,
then delete the agent-infra producer), which is a different, larger piece of work with its own
prerequisite. **This issue's completion does not unblock the Pi claim**; it closes the
executable-verification gap and states the claim's true status.

## Rejected / out of scope

- **`#4710`** — wire the *behavioral* probe into `session verify` (fire the installed artifact). The
  owner's note-2 argument; real, separate, filed.
- **`#4680`** (2026-09-22T17:56Z) — the **version-contract route to the same residual**: give the pi
  seam a `tortoise-hook-version` marker + an `_EXPECTED_INSTALL_CONTRACT` entry so a stale install is
  reported **STALE** (hermetic, no LLM), versus `#4710`'s live behavioral probe. **`#4680` and
  `#4710` overlap and neither cross-referenced the other** — the duplicate-ownership shape AGENTS.md
  warns about. They must be coordinated, not raced: `#4680` is the cheap structural detector; `#4710`
  is the behavioral confirmation. Cross-links posted on both issues; whichever lane picks either up
  owns reconciling them.
- **`#2552` / operator gold corpus** — owned elsewhere.
- **`#4661`** — already filed; the probe's read-back refusal is a reported blocker, never weakened
  around.

## Known residual of this scope's own test

`_node_supports_ts` **skips** (green) when Node < 22.6, and `tools/skip-guard.py` does not guard
Node-availability skips — so on such a runner the **pre-existing** behavioral suite is a silent
no-op. That is inherited from the older test, not introduced here, and its skip contract is
deliberately left alone (changing it is its own change).

The two installed-artifact checks this scope ADDS do **not** share that residual: they fail closed
under CI (`pytest.fail`, not `pytest.skip`, when `os.environ["CI"]` is set and Node is unusable),
and the lanes that run them provision Node 22 (`actions/setup-node@v4`), so the requirement is owned
by the lane rather than inherited from the runner image. Both directions are pinned by tests in
`tests/test_pi_capture_hooks.py`.

**OVERRIDES:** the node guard's skip-in-every-case default (the deferral recorded above) — the two
installed-artifact checks this scope adds fail closed under CI, because a fail-open guard on the only
executable proof that the seam works as installed is precisely the defect this scope exists to close.
The deferral still governs the pre-existing behavioral suite, whose skip contract is untouched.
