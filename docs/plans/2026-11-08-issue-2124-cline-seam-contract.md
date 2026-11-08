<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/2124#issuecomment-6727648328 -->
<!-- plan-review: cycles=0, status=not-run-lane-dispatches-no-verifier; the harness merge chain (merge-one.sh) supplies the one fresh-context review round at the pushed head -->

# #2124 — repair the shipped Cline seam: real input shape, real output shape, real install path

**Issue:** #2124 (epic #2080) · **Branch:** `feat/2124-tm` · **Complexity:** standard (as labelled)

## Confirmed problem

Issue #2124's indicator 2 is *"each adapter is verified against its documented seam (primary-source)"*,
and
the 2026-10-18 re-measurement recorded it **OPEN for Cline** — the shipped seam was *"graded by OUR
tests only; delivery unverified."* Re-verified **against Cline's source** (the research comment), that
indicator does not merely fail: the seam cannot inject on any Cline surface. Three independent defects,
each reproduced locally:

| # | Defect | Evidence |
|---|---|---|
| F1 | Output shape is an **object**; Cline requires a flat **string** | `hooks.proto:37-41` `string context_modification = 1;`; `hook-factory.ts:74-84` rejects non-string with *"Invalid hook output: 'contextModification' must be a string"* |
| F2 | Cline's nested `{"userPromptSubmit":{"prompt":…}}` is not extracted, so the reflex is queried with the raw JSON envelope | `hooks.proto:12`; `hook-factory.ts` serialises `jsonObj.userPromptSubmit` |
| F3 | `install cline` writes `<dir>/.cline/hooks/…`; the **VS Code extension** reads only `<dir>/.clinerules/hooks/…` | `apps/vscode/src/core/storage/disk.ts` `hooksDir: ".clinerules/hooks"` → `getWorkspaceHooksDirs()` → `getAllHooksDirs()` → `hook-factory.ts` |

Reproduction (real script, stubbed reflex that echoes the prompt it received), before any change:

```console
$ printf '%s' '{"prompt":"How is CI deploy triggered?"}' | volunteer-turn.sh cline
{"contextModification": {"context": "REFLEX-SAW-PROMPT=[How is CI deploy triggered?]"}}   # F1: object
$ printf '%s' '{"userPromptSubmit":{"prompt":"How is CI deploy triggered?","attachments":[]}}' | volunteer-turn.sh cline
{"contextModification": {"context": "REFLEX-SAW-PROMPT=[{\"userPromptSubmit\": … }]"}}     # F2: raw envelope
```

Falsification check: if Cline's source accepted an object `contextModification`, or the extension
discovered `<ws>/.cline/hooks`, F1/F3 would be wrong. Both are refuted by the cited lines, F1 also by
Cline's own e2e fixture and by six independent web results. **Confidence: high.**

## Alternatives considered

1. **Fix the seam in place** (chosen) — correct the two contract shapes and move the install target.
   Lowest blast radius, repairs a shipped deliverable, contradicts no recorded decision.
2. **Delete the Cline seam and re-file it as demand-gated.** Rejected: the issue's decision gates
   *building new adapters*; removing a shipped `install cline` would regress users for a defect that is
   four lines of contract. It also does not close indicator 2 — it abandons it.
3. **Accept both output shapes** (`contextModification` as string *or* object). Rejected: Cline's
   validator is the authority and it accepts one shape; emitting a shape its validator rejects is the
   bug, not a compatibility surface to preserve. Nothing else consumes our narrow form.
4. **Write both `.clinerules/hooks/` and `.cline/hooks/`.** Rejected: the SDK/CLI loader reads *both*,
   so two hook files double-inject there. One canonical path plus a migration of the legacy file.

## Complexity + domains

`complexity:standard` (as labelled). Domains: code (a hook script + installer), tests, docs
(the platform-seams matrix correction log). Not gate/enforcement code — `(not adversarial)`.

## Wiring

| Site | What it does | Change |
|---|---|---|
| `tortoise/claude-hooks/volunteer-turn.sh:342-354` prompt extraction | JSON→`PROMPT`; falls back to raw stdin | also read `userPromptSubmit.prompt` |
| `tortoise/claude-hooks/volunteer-turn.sh:555-565` `cline)` output arm | builds `{"contextModification":{"context":…}}` | emit `{"cancel":false,"contextModification":"<str>"}` |
| `tortoise/__main__.py:6001-6003` target selection | `root/".cline"/"hooks"/"UserPromptSubmit"` | `root/".clinerules"/"hooks"/"UserPromptSubmit"` + legacy path for uninstall/migration |
| `tortoise/__main__.py:5919-5921` `install --list` row | prints the path | print the new path |
| `tortoise/__main__.py:6137-6179` cline install/uninstall branch | ownership check, atomic write, uninstall | migrate away our legacy `.cline/hooks` file; uninstall both |
| `tests/test_platform_seams.py:284-320, 540-548, 526-538, 730-770, 932-946` | install/path/uninstall/ownership | retarget to `.clinerules/hooks`; add legacy-migration case |
| `tests/test_platform_seams.py:683-688` cline contract | feeds `{"prompt":…}`, asserts object | feed Cline's real nested stdin, assert flat string |
| `docs/research/2026-09-01-gbrain-learnings/platform-seams.md` matrix + correction log | records the Cline row + correction log | correct row; record this pass's F1/F2/F3 with locators |

Consumers of the *output* are Cline itself and `tests/test_platform_seams.py`; consumers of the
*install path* are the installer, the ownership/uninstall logic, and the tests. Nothing imports the
hook script as a module. No MCP tool or public SDK method is touched.

## Implementation steps

1. **Hook script, input half.** Extract `userPromptSubmit.prompt` when the top-level keys are absent.
   Test: `test_volunteer_turn_hook_cline_contract` fed Cline's nested stdin must show the reflex
   receiving the *question*, not the envelope.
2. **Hook script, output half.** Emit the flat string. Test: same, asserting `isinstance(str)`.
3. **Installer path.** Retarget to `.clinerules/hooks/UserPromptSubmit`; keep the ownership marker,
   symlink-escape guard and atomic 0755 write (they are path-agnostic, inherited not re-implemented).
   Test: install writes the new path; a foreign hook there is still refused.
4. **Legacy migration.** On install, remove our own `.cline/hooks/UserPromptSubmit` (ownership-checked
   via the existing `_cline_marker_owns`); on uninstall, remove it too. A *foreign* file at either path
   is never deleted. Test: migration removes ours and leaves a foreign one.
5. **Docs.** Correct the `#2124 Cline` matrix row and add a correction-log entry for this pass.
6. **Suites.** `.venv/bin/python -m pytest tests/test_platform_seams.py` plus the installer suites that
   reference the hook.

### Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 The reflex receives the envelope instead of the question for a Cline turn → caught by the cline
  contract test fed Cline's **real** stdin shape and asserting the reflex input.
- R2 The emitted JSON is rejected by Cline's validator → caught by asserting
  `isinstance(out["contextModification"], str)` (the exact predicate `hook-factory.ts:75` applies).
- R3 `install cline` writes where no Cline surface reads → caught by asserting the install target is
  `<dir>/.clinerules/hooks/UserPromptSubmit`.
- R4 Migration deletes a user's own hook → caught by the foreign-hook refusal test at the legacy path.
- R5 Uninstall leaves our file behind at either path → caught by uninstall assertions on both paths.
- R6 A regression in the other harness arms (`claude|codex|devin` share one arm) → caught by their
  unchanged contract tests in the same file.

**Threat classes:** (not adversarial) — not gate/enforcement code.

**Out of scope — must NOT be raised** (note, file, do not chase):

- #8962 (`hooks status`/`hooks upgrade` report cline/devin as "unknown harness") — a separate open issue.
- The demand-gated tail (Devin/OpenCode/Gemini session parsers, `install gemini`, OpenCode plugin) —
  held by #2124's own recorded demand gate.
- Cline's stale `.clinerules/hooks` README vs its `.cline` config dir — documentation drift in a
  third-party repo, recorded in the correction log, not fixed here.
- The global hook directory (`~/Documents/Cline/Hooks` for the extension vs `~/.cline/hooks` for the
  SDK) — `tortoise install` writes project-scope registrations only.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.

## Rounds

**Rounds:** 0 at PLAN-VERIFY (this lane dispatches no verifier — it has no `task` tool; the harness
merge chain's fresh-context reviewer is the one round). Class: `code`.
