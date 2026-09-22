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
| Node TypeScript support | Pass **`--experimental-strip-types`** to every `node` invocation in the file | Type stripping is default only at Node ≥ 22.18 (`_node_supports_ts` accepts ≥ 22.6 — a pre-existing latent false-red on 22.6–22.17, nodejs.org "Type stripping is enabled by default" at v22.18.0). The flag exists from 22.6 and is accepted on ≥ 22.18, so passing it makes the ≥ 22.6 guard *true* and fixes the existing test too |
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
both skip (not fail) only when `node` is absent or is < 22.6.

**Files:**
- Modify: `tests/test_pi_capture_hooks.py`

**Steps (TDD):**
1. Add `import json` to the file's import block. Add a module constant
   `NODE_TS_FLAG = "--experimental-strip-types"` and pass it in both this task's probe invocation and
   the existing `test_extension_behavioral_suite` invocation (`[node, NODE_TS_FLAG, "--test", str(EXTENSION_TEST)]`);
   correct the file docstring's "Node >= 22.6" note to say the flag is passed so 22.6+ works.
2. Add `_installed_probe() -> str` (no parameter — paths travel by env) returning the probe source:
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
     `install_capture("pi", home=home)` (import from `tortoise.capture_install`);
     `installed = home / ".pi" / "agent" / "extensions" / "tortoise-capture.ts"`; assert `installed.is_file()`.
   - `spool = home / "spool"`; `probe = home / "probe.mjs"`; `probe.write_text(_installed_probe())`.
   - `proc = subprocess.run([node, NODE_TS_FLAG, "probe.mjs"], cwd=tmp_home, capture_output=True,
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
5. Guard (both tests): `node = shutil.which("node")`; absent → `pytest.skip`; `not _node_supports_ts(node)`
   → `pytest.skip`.

**Integration surface:** pytest → `node` subprocess over a temp `HOME`; no network (fetch injected), no
ambient credential (`_scrubbed_env`), no real spool (explicit `spoolDir`).

## Task 2 — accurate `session_verify` disclosure

**Intent:** Remove the absolute over-claim that restates the stale premise; replace it with a **scoped**
statement that neither over- nor under-claims.
**Acceptance:** `test_pi_is_honestly_unverifiable` passes with the new assertions; the pin fails if the
reason stops naming the suite / the residual, or if **either** absolute sentence returns.

**Files:**
- Modify: `tortoise/session_verify.py` (`UNVERIFIABLE_REASON["pi"]`, the `HEADLESS_FIRABLE` comment, the module-docstring sentence)
- Modify: `tests/test_session_verify.py` (`test_pi_is_honestly_unverifiable`)

**Steps (TDD):**
1. **Red:** in `test_pi_is_honestly_unverifiable` (its final assert already uses
   `report["links"]["installed"]["detail"]` — bind `detail = report["links"]["installed"]["detail"]`
   and keep the existing `"extension" in detail`), add:
   ```python
   assert "tortoise-capture.test.ts" in detail
   assert "tests/test_pi_capture_hooks.py" in detail
   assert "installed" in detail
   assert "manual-only" in detail
   assert "not firable by this command" in detail
   assert "cannot be executed headlessly" not in detail
   assert "cannot be fired headlessly" not in detail
   assert "no headless trigger" not in detail
   ```
   RED against the current string.
2. Reword `UNVERIFIABLE_REASON["pi"]` to (keeping `"extension"`; the string is PLAIN TEXT — no
   markdown emphasis, it is printed verbatim into a report line):
   > Pi's capture seam is a TypeScript extension loaded in-process by Pi
   > (`~/.pi/agent/extensions/tortoise-capture.ts`), not a command this verifier can execute; the
   > install leg is therefore not firable by this command. The seam's handler logic is exercised
   > hermetically by `tortoise/pi-hooks/tortoise-capture.test.ts` (run by
   > `tests/test_pi_capture_hooks.py`), and the installed artifact is loaded and fired by that
   > file's node probe (into a temp HOME); the residual, a real `pi` process loading the installed
   > extension against the live API, is manual-only.
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
  (51 hermetic tests, fires the real handlers); `tests/test_pi_capture_hooks.py` (source pins + the
  installed-artifact fired check + its anti-vacuity mutation test). `tests/test_pi_capture_hooks.py` is
  **registered under `core` (and `onboarding`); a `tortoise/pi-hooks/` change selects `core` via the
  `tortoise/` fallback**. The installed artifact as `install_capture` writes it is loaded and fired by
  that check (into a temp `HOME`, not the operator's live file).
- **Update the run command at `tortoise/pi-hooks/README.md:13`** to
  `node --experimental-strip-types --test tortoise/pi-hooks/tortoise-capture.test.ts` (or note
  "Node ≥ 22.18, else pass the flag"), so the README does not carry two conflicting instructions.
- **Manual-only:** that a real `pi` process loads the installed extension against the live API.
  Procedure: with `TORTOISE_API_KEY` set and the org's Agent-sessions toggle on (else the server 409s
  and no receipt prints), run
  `pi --no-extensions -e ~/.pi/agent/extensions/tortoise-capture.ts -p "<trivial prompt>"` on a
  non-dogfood install. `--no-extensions` makes the probe single-producer.
  **Pass condition = `retrievable` (owner ruling, B1 report): read the SPECIFIC CAPTURED CONTENT
  back.** `GET /v1/sessions/{id}` returns the session's turns + extracted points (and/or
  `GET /v1/search?q=…` returns its points) — that is the test. A row in `GET /v1/sessions` alone is
  **necessary, not sufficient** (it proves `captured` — the write landed — never `retrievable`); it is
  *supporting evidence*, exactly like the
  `[tortoise-capture] captured session(s) → <apiUrl> (filed N≥1)` line (a 2xx is implied, not printed).
  **A read-back 504 is `UNMEASURABLE` — never PASS, never FAIL** (the read path is query-dependent,
  `#4661`). **No receipt line while the session IS present ⇒ the `#4675` post-commit-504 false
  negative, NOT a seam failure — never FAIL, and never PASS on the list row alone; attempt the
  content read-back, else `UNMEASURABLE`.**
  Run by a maintainer with a live `pi` install and a capture credential — at 2026-09-22 the **B1
  lane**, owner of objective-1's exit evidence
  (`~/.pi/agent/state/lane-reports/B1-LIVE-FOUR-HARNESS-2026-09-22.md`, cited by `#4620` and named in
  the `#1714` comment Task 5 posts).
- **Blockers:** `#4661` (read path — a read-back 504 is UNMEASURABLE); **`#4675`** (a post-commit 504
  is indistinguishable from a pre-commit one, so the client records no receipt for a session that did
  land — the client's *terminality* rule, resolved by a confirming read once `#4661` permits it);
  `#3713` —
  launch-blocking for the Pi claim per its recorded decision (2026-09-22T17:20Z), **not** for a
  clean-machine install, and its sequencing blocker `#3971` has merged as `73acefddf`. Until `#4661`
  and `#4675` are resolved the live leg can yield **no verdict** (UNMEASURABLE), so objective 1's
  done-state must not read as verified. Automating the probe is `#4710`; the version-contract route
  is `#4680`.

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
  over-claim; `:248` is a Task-14 `- Test:` bullet, not the verification row). The amendment states:
  the seam logic + installed artifact ARE executably verified (cite `tortoise-capture.test.ts` and
  `tests/test_pi_capture_hooks.py`); the real-`pi`-process leg is **manual-only**, its pass condition
  is **`retrievable` — the specific captured content read back** (`GET /v1/sessions/{id}` turns +
  points / `GET /v1/search?q=…`; a list row is not sufficient), and a read-back 504 is UNMEASURABLE
  (procedure + actor + `#3713`/`#4661`/`#4675`), citing `tortoise/pi-hooks/README.md`.
- A **Pi/session-capture row is added to `#1714`'s `### Verification Checklist`** (its stated
  done-state; it has no such row today), naming the executable check and the manual residual.
- A decision comment is posted on `#1714` that (a) cross-links `#4620`, and (b) **names the report
  path** `~/.pi/agent/state/lane-reports/B1-LIVE-FOUR-HARNESS-2026-09-22.md` — making Task 3's "cited
  by `#4620` and named in `#1714`" provenance true.

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

## Reviewers

Plan review: per `proportional-gates` §Review Cycles, standard → Low-Medium → **2 reviewers**, cap 3
cycles. Cycle-1 findings (P1 done-state placement; P2 stdout contract, one-sided pin, verification
commands, manual-residual actor; P3 receipt quote, `#3971` fact, mutation expressibility, path
injection, ctx shape), cycle-2 findings (P1 scoped-not-absolute replacement copy, second negative
token; P2 `#4680`/`#1714` placement and the miscited objective-1 row, Node strip-types bound; P3
actor resolvability, append-don't-rewrite, done-state location; P4 snippet bindings), and cycle-3
findings (**P1** the manual procedure measured a receipt `#4675` suppresses and omitted the
`retrievable` pass condition; P2 the installed-artifact check mis-attributed to the TS suite, false
`#1714` provenance; P4 README run-command conflict) are all incorporated above.
