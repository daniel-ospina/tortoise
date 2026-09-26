---
title: "Plan — #4620 Pi capture seam executable verification"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-22
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Plan — #4620: installed-artifact executable verification + accurate disclosure

Scope: `docs/scoping/2026-09-22-4620-pi-capture-verification.md`
Issue: `daniel-ospina/tortoise#4620` · Objective: `#1714` · Tree: `b3334b560`
Tier: **standard** (`complexity:standard`) · Domain: **Complicated** (established patterns)
Research path: **none** — zero third-party dependencies (Node stdlib + a type-only import), all
patterns in-repo. `writing-plans` Step B (Perplexity gate) skipped under its own rule; Step A consumed
the scoping artifact. `test-design` integration-surface map: the only new boundary is
Python-pytest → `node` subprocess over a temp `HOME` (Task 1); Tasks 2–5 are a string change and
prose, so no further layers.

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Installed-check shape | A **minimal node probe** (`.mjs`) that imports the installed seam, registers on a mock `pi`, fires `session_shutdown`, asserts the receipt — **not** a copy of the 51-test suite | The 51 behaviors are already asserted at the source path; re-running them adds time, not coverage. The installed path's marginal claim is *the file loads in isolation, its handlers register, and it produces a receipt* |
| Named mutation the check catches | Path-dependent runtime module resolution | Verified RED: adding `import { X } from "./helper.ts"` leaves the source-located suite green (51/51, sibling present) while the installed single-file load raises `ERR_MODULE_NOT_FOUND`; the seam is fail-open, so such a regression would silently file nothing. No existing test catches it (byte-equality + a grep for `agent-infra` only) |
| Non-vacuity | A **second test** mutates the installed artifact and asserts the probe FAILS | Makes non-vacuity mechanical, not a claim |
| Probe stdout contract | The probe prints one sentinel line `PROBE_JSON:<json>`; Python extracts it by regex | The seam **logs to stdout** on the fired path (`tortoise-capture.ts:1316`), so `json.loads(stdout)` would fail on every green run |
| Probe transport | Write the probe into the temp `HOME`; pass the seam + spool paths via **env**; import via `pathToFileURL`; run with `cwd=tmp_home` | Avoids interpolating a `C:\…` path into a JS literal (invalid `\U`), works on Windows, keeps the seam specifier absolute |
| Node TypeScript support | Pass **no** `--experimental-strip-types` flag: the floor tracks the version where stripping became DEFAULT-ON, **Node ≥ 22.18** | Type stripping is default only at Node ≥ 22.18 (nodejs.org "Type stripping is enabled by default" at v22.18.0), and `--experimental-strip-types` survives on ≥ 22.18 only as an **undocumented alias** — Node's documented form is to run with no flag. So the invocation is bare `node --test <file>.ts` and `_node_supports_ts` accepts ≥ 22.18; the old 22.6 floor was the latent false-red, because on 22.6–22.17 that same bare invocation cannot load the typeless module at all |
| Credential isolation | Reuse `_scrubbed_env(tmp_home)` + an explicit temp `spoolDir` | Same #3721 discipline the existing suite uses. `_scrubbed_env` does **not** strip `TORTOISE_CAPTURE_SPOOL_DIR`, so passing `spoolDir` explicitly is load-bearing — never omit it |
| `session_verify` copy | Reword `UNVERIFIABLE_REASON["pi"]` + the `HEADLESS_FIRABLE` comment + the module-docstring sentence; keep the word **"extension"** | The absolute claim must go; the replacement must be **scoped** ("not firable **by this command**"), never another absolute. The installed artifact *is* fired headlessly by Task 1, so the copy must say so to avoid under-claiming |
| Manual residual actor | Define the role by capability **and** give the report's full path | "release operator" is undefined in both repos; "B1 lane" alone is unreadable from the README; the report lives outside the repo |
| Done-state home | Append a dated amendment to objective 1's plan + add a Pi row to `#1714`'s Verification Checklist + a `#1714` comment | The issue requires the limitation "stated in objective 1's done-state"; that done-state is the issue's own Verification Checklist (no Pi row today) and the plan's Integration-Surface row (`:43`, "Pi 2xx leg observed") |

## Task 1 — installed-seam fired check (+ anti-vacuity)

**Intent:** Close the headlessly-closable gap: prove the artifact *as `install_capture` writes it*
loads in isolation, registers its handlers, and produces a capture receipt (issue outcome (1) at
installed fidelity).
**Acceptance:** `tests/test_pi_capture_hooks.py::test_installed_seam_loads_and_fires` passes;
`test_installed_seam_probe_fails_when_the_artifact_is_not_self_contained` passes (proves non-vacuity);
both FAIL under CI when `node` is absent or below the default-on floor (22.18) (they skip only in a local run, where the
source-level pins above still ran); every lane that executes this file provisions Node 22.

**Files:**
- Modify: `tests/test_pi_capture_hooks.py`

**Steps (TDD):**
1. Add `import json` to the file's import block. Pass **no**
   `NODE_TS_FLAG`: the behavioural-suite invocation stays `[node, "--test", str(EXTENSION_TEST)]` and the
   floor moves to the default-on boundary (`_node_supports_ts`, Node ≥ 22.18 — see the Node TypeScript
   support row above); correct the file docstring's "Node >= 22.6" note to "Node >= 22.18 … BY DEFAULT".
2. Add the probe SOURCE as a module constant (`_PROBE_SOURCE` as shipped) plus a
   `_run_installed_probe(tmp_home, installed, node)` that writes it into the temp HOME and runs it.
   No parameter carries a path — paths travel by ENV:
   ```js
   import { pathToFileURL } from "node:url";
   const mod = await import(pathToFileURL(process.env.PROBE_SEAM).href);
   const handlers = {};
   const pi = { on(e, f) { handlers[e] = f; } };
   const calls = [];
   const fetchImpl = async (url, init) => {
     calls.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
     return { ok: true, status: 200, json: async () => ({}) };
   };
   mod.default(pi, {
     fetchImpl,
     env: { TORTOISE_API_KEY: "tt_test", TORTOISE_API_URL: "https://h" },
     configPath: "/nonexistent/tortoise-config.json",
     spoolDir: process.env.PROBE_SPOOL,
   });
   const ctx = { sessionManager: {
     getEntries: () => [
       { type: "message", message: { role: "user", content: [{ type: "text", text: "installed seam probe" }] } },
       { type: "message", message: { role: "assistant", content: [{ type: "text", text: "receipt" }] } },
     ],
     getSessionId: () => "sess-installed-1",
     getSessionFile: () => "/tmp/sessions/2026-01-01_abc.jsonl",
   }, model: { provider: "deepseek", id: "deepseek-v4-flash" } };
   await handlers.session_shutdown({ reason: "quit" }, ctx);
   await new Promise((r) => setImmediate(r));
   console.log("PROBE_JSON:" + JSON.stringify({ handlers: Object.keys(handlers), calls }));
   ```
   The `{type:"message", message:{…}}` entry shape matches `PI_ENTRIES` (a bare `{type:"user"}` yields
   zero turns and a confusing `0 !== 1`). `session_shutdown` fires synchronously; the `setImmediate`
   awaits its `.then`.
3. Add `test_installed_seam_loads_and_fires`:
   - `with tempfile.TemporaryDirectory() as tmp_home:`; `home = Path(tmp_home)`;
     `result = install_capture("pi", home=home)`; assert `result.ok`, then
     `installed = capture_install.pi_home(home) / capture_install.PI_EXTENSION_NAME`; assert
     `installed.is_file()` — never re-type the install path (#4620 review).
   - `spool = home / "spool"`; the probe file is written into the temp HOME by
     `_run_installed_probe`, which also runs it and returns the completed process.
   - `proc = subprocess.run([node, "probe.mjs"], cwd=tmp_home, capture_output=True,
     text=True, timeout=120, env={**_scrubbed_env(tmp_home), "PROBE_SEAM": str(installed),
     "PROBE_SPOOL": str(spool)})`.
   - `m = re.search(r"^PROBE_JSON:(.*)$", proc.stdout, re.M)`; `assert m, proc.stdout`;
     `payload = json.loads(m.group(1))`; `calls = payload["calls"]`; `assert len(calls) == 1, proc.stdout`;
     `body = calls[0]["body"]`.
   - Assert: `{"session_start","session_shutdown"} <= set(payload["handlers"])`;
     `calls[0]["url"]` matches `/v1/sessions$`; `body["harness"] == "pi"`;
     `body["session_id"] == "sess-installed-1"`; `body["conversation"] == [{user…},{assistant…}]` (the two fixed turns).
4. Add `test_installed_seam_probe_fails_when_the_artifact_is_not_self_contained` (after step 3 exists):
   same install, then append `\nimport { __x } from "./helper.ts";\n` to `installed` (no `helper.ts`
   beside it), run the probe, assert `proc.returncode != 0` and `"ERR_MODULE_NOT_FOUND"` /
   `"helper.ts"` in `proc.stdout + proc.stderr`.
5. Guard (both tests): `node = _require_node()` — absent / `not _node_supports_ts(node)` →
   `pytest.skip` locally, `pytest.fail` under CI (pinned by
   `test_require_node_fails_closed_when_node_is_absent` and
   `test_require_node_fails_closed_on_a_pre_strip_types_node`).

**Integration surface:** pytest → `node` subprocess over a temp `HOME`; no network (fetch injected), no
ambient credential (`_scrubbed_env`), no real spool (explicit `spoolDir`).

## Task 2 — accurate `session_verify` disclosure

**Intent:** Remove the absolute over-claim that restates the stale premise; replace it with a **scoped**
statement that neither over- nor under-claims.
**Acceptance:** `test_pi_is_honestly_unverifiable` passes with the new assertions. The pin's exact
scope — the text it scans and how it joins wrapped comment lines — is defined by the shipped test in
`tests/test_session_verify.py`, which is the source of truth; it is deliberately not restated here.

**Files:**
- Modify: `tortoise/session_verify.py` (`UNVERIFIABLE_REASON["pi"]`, the `HEADLESS_FIRABLE` comment, the module-docstring sentence)
- Modify: `tests/test_session_verify.py` (`test_pi_is_honestly_unverifiable`)
**Steps (TDD):**
1. **Red:** in `test_pi_is_honestly_unverifiable`, bind
   `detail = report["links"]["installed"]["detail"]` (keeping the existing `"extension" in detail`)
   and add the new assertions — including the extension of the pin to the module-side Pi ruling. The
   shipped test in `tests/test_session_verify.py` is the definition of that assertion set; it is not
   restated here. RED against the current string.
2. Reword `UNVERIFIABLE_REASON["pi"]` to state the scoped truth. The shipped text is the source of
   truth — see `tortoise/session_verify.py` → `UNVERIFIABLE_REASON["pi"]`. Constraints it must
   satisfy: it is PLAIN TEXT (printed verbatim into a report line), so it carries **no markdown
   emphasis**; it keeps `"extension"`; the seam path in it is DERIVED from
   `capture_install.pi_home` / `PI_EXTENSION_NAME` rather than written out.
   **Attribute correctly:** the TS suite covers the *source* seam; the *installed* artifact is covered
   by the Python file's node probe — never claim the TS suite covers the installed copy.
   **Never** the bare absolute "cannot be executed/fired headlessly" or "no headless trigger" —
   `pi -p` is non-interactive and Task 1 fires the installed artifact headlessly.
3. Add one clarifying sentence to the `HEADLESS_FIRABLE` comment block and the module-docstring
   "HONEST DISCLOSURE" paragraph, the same distinction.
4. Re-run `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_session_verify.py -k pi_is_honestly_unverifiable -q` → green.

## Task 3 — recorded decision in the seam README

**Intent:** Land the recorded decision in the artifact a lane actually reads.
**Acceptance:** `tortoise/pi-hooks/README.md` gains a **Verification** section naming (a) what is
executably verified, (b) what is manual-only, (c) the exact procedure **with its precondition**,
(d) who runs it, (e) the `#3713`/`#4661`/`#4710`/`#4680` pointers, (f) that `#3971` has merged.

**Files:**
- Modify: `tortoise/pi-hooks/README.md`

**Steps:** add the section:
- **Executable:** `node --experimental-strip-types --test tortoise/pi-hooks/tortoise-capture.test.ts`
  (the extension's full hermetic suite, which fires the real handlers); `tests/test_pi_capture_hooks.py`
  (source pins + the installed-artifact fired check + its anti-vacuity mutation test). `tests/test_pi_capture_hooks.py` is
  **registered under `core` (and `onboarding`); a `tortoise/pi-hooks/` change selects `core` via the
  `tortoise/` fallback**. The installed artifact as `install_capture` writes it is loaded and fired by
  that check (into a temp `HOME`, not the operator's live file).
- **Update the run command at `tortoise/pi-hooks/README.md:13`** to
  `node --experimental-strip-types --test tortoise/pi-hooks/tortoise-capture.test.ts` (or note
  "Node ≥ 22.18, else pass the flag"), so the README does not carry two conflicting instructions.
- **Manual-only:** that a real `pi` process loads the installed extension against the live API.
  **Canonical: `tortoise/pi-hooks/README.md` § Verification** — procedure, actor, pass condition,
  UNMEASURABLE rule and blocker list live there, and are not restated here. Until `#4661`/`#4675`
  clear the live leg yields **no verdict**, so objective 1's done-state must not read as verified.
  `#3713` is launch-blocking for the Pi *claim* only; its sequencing blocker `#3971` merged as
  `73acefddf`. Automating the probe is `#4710`; the version-contract route is `#4680`.

## Task 4 — docs index

**Intent:** Register the new docs per the filing protocol.
**Acceptance:** `docs/00_index.md` gains one row naming
`docs/scoping/2026-09-22-4620-pi-capture-verification.md` **and**
`docs/plans/2026-09-22-4620-pi-capture-verification.md`.
**Files:** Modify `docs/00_index.md`.

## Task 5 — state the limitation in objective 1's done-state

**Intent:** Satisfy issue outcome (2)'s "stated in objective 1's done-state" clause.
**Acceptance:**
- `docs/plans/2026-08-25-1714-memory-capture-onboarding.md`: **append** a dated
  `## #4620 amendment (2026-09-22)` section (do **not** rewrite the reviewed text), and **append** a
  one-line pointer *immediately after* the table containing `:43` and *after* item 4 at `:316`,
  **leaving both lines byte-identical** (`:43` is the Integration-Surface row
  `| Claude Code hooks + Pi extension | … | hook smoke; Pi 2xx leg observed |` — the actual stale
  over-claim; `:248` is a Task-14 `- Test:` bullet, not the verification row). The amendment states
  that the seam logic + installed artifact ARE executably verified (citing `tortoise-capture.test.ts`
  and `tests/test_pi_capture_hooks.py`), that the real-`pi`-process leg is **manual-only**, and
  **points to `tortoise/pi-hooks/README.md` § Verification** for the procedure, actor, pass condition
  and blockers — it does not restate them (a restated procedure drifts from its home).
- A **Pi/session-capture row is added to `#1714`'s `### Verification Checklist`** (its stated
  done-state; it has no such row today), naming the executable check and the manual residual.
- A decision comment is posted on `#1714` that (a) cross-links `#4620`, and (b) **names the report
  path** `~/.pi/agent/state/lane-reports/B1-LIVE-FOUR-HARNESS-2026-09-22.md` — so the report path the
  objective names is retrievable from `#1714` itself.

**Files:**
- Modify: `docs/plans/2026-08-25-1714-memory-capture-onboarding.md`
- Artifact: body edit + comment on `#1714`

## Execution order

Order: **1 → 2 → 3 → 4 → 5** (Task 1 is the critical path; Tasks 2–5 are disjoint and cite it).

## Verification

1. `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_pi_capture_hooks.py tests/test_session_verify.py -v`
   (carve-out lane — both are DB-free).
2. `node --experimental-strip-types --test tortoise/pi-hooks/tortoise-capture.test.ts` → 51/51.
3. Task 1's anti-vacuity test passes (the probe REDs on a non-self-contained installed copy).
4. `printf '%s\n' tests/test_pi_capture_hooks.py tortoise/session_verify.py tortoise/pi-hooks/README.md | python3 tools/ci_selection.py --changed-files -`
   → assert `test_pi_capture_hooks.py` and `test_session_verify.py` are in the emitted `test_files`.
5. `bash scripts/check-pipeline-compliance.sh` (pre-commit docs/version gate; `scripts` → `$AGENT_INFRA_PATH/scripts`).
6. Node gate, both directions: `CI=true` with a stub `node` reporting v20.11.0 → the two
   installed-artifact checks FAIL by name (7 passed / 2 failed / 1 skipped); real Node ≥ 22.18 with
   `CI=true` → 10 passed, 0 skipped.

## Reviewers

Plan review: 2 reviewers per `proportional-gates` §Review Cycles (Low-Medium → cap 3). Exit:
**capped** after 4 cycles — one over that cap. The cycle-4 finding (the `retrievable` pass condition
was under-defined) is incorporated above, so no finding is carried open; the exit is recorded as
capped, not clean, because the loop ran past its bound (`plan-review` §Exit & Signature).

<!-- plan-review: cycles=4, status=capped, verdict=incorporation-over-cap, version=2.3.0 -->

