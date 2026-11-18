---
title: "Frontmatter value survival — #13452 implementation plan"
type: engineering
domain: knowledge-graph
doc_status: draft
created: 2026-11-18
subjects.team: epistemic-team
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/13452#issuecomment-6967218550 -->
<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->

# Frontmatter value survival — #13452

> **For Pi:** `executing-plans`, one task. Both halves are small; the mutant proof is the
> load-bearing evidence, not the diff size.

**Status:** Implemented — PLAN-VERIFY closed at 3 cycles (the standard cap, budget disclosed in §8);
PROVE evidence is measured in §7.
**Complexity:** Standard (tier=Standard)
**Goal:** quotes restored on 8 `aboutObjects` values, and the `docs/research/` frontmatter arm
asserts **value survival** rather than key presence, so the class cannot silently re-land.

---

## 1. Confirmed problem

`tortoise.file_indexer.parse_frontmatter` is tolerant by design: on a `ScannerError` it returns
`{}`. That tolerance is deliberate and NOT in scope. The defect is upstream of it — a frontmatter
value written as an **unquoted plain scalar** containing `, #11395 …` is silently **terminated at
the space-preceded `#`**, because YAML admits `#` inside a plain scalar only with a non-space
lookbehind (`ns-plain-char(c)`, YAML 1.2.2 §7.3.3). The parse succeeds, the key survives, and the
value is short.

On `main` today, measured through that same consumer across `docs/research/**/*.md`:

```text
103 files · 68 frontmatter blocks · 8 truncated values · all 8 are `aboutObjects`
  the truncated count matches the issue's 8 EXACTLY, derived independently;
  the block count is the issue's 67 plus one file that landed since (drift, not
  disagreement — see §7's before/after count)
```

`tests/test_doc_affiliation_frontmatter.py:254` cannot see any of them:
`missing = [key for key in REQUIRED_KEYS if key not in parsed]` asks whether four keys are
**present**, and `REQUIRED_KEYS` does not even contain the two fields that carry the affiliation
to other entities.

**Falsification check:** if a value-survival assertion does not redden when one `aboutObjects`
value is reverted from quoted to unquoted plain, the design is wrong and the arm is a rubber stamp
— see §5.
**Confidence:** 95 (mechanism cited normatively, population measured, defect reproduced 8/8).

## 2. Alternatives considered

| # | Approach | Why rejected / chosen |
|---|---|---|
| **A** | **Quote the 8 values + add a style-aware value-survival assertion to the existing `docs/research` arm** | **CHOSEN.** Fixes the data and the guard in one change; the guard is already CI-wired (`config/ci-surfaces.yml` ×2, `tier1` + `core`); the mutant is single-target; no consumer semantics change. |
| B | Quote the 8 values only | Rejected: it is exactly what #13432 did — `aboutSubjects` was quoted in a commit whose message claimed the values were fixed, one line above an identical unquoted field. Data-only fixes re-land. |
| C | Change `parse_frontmatter` to re-attach dropped text / use a comment-preserving loader | Rejected: it changes parse semantics for **every** document type (`docs_miner`, `sdk`, `ingest`), diverges from YAML, would silently accept genuinely malformed blocks, and reverses a recorded design invariant (`parse_frontmatter` degrades to `{}` — `frontmatter_validator.py` docstring pins it). Also strictly worse: the correct fix is at the source of the value, not at the consumer. |
| D | Extend `tortoise/frontmatter_validator.py` (the existing quality gate) to detect truncation | Rejected: it is `warn-only` **and** gated OFF behind `TORTOISE_VALIDATE_FRONTMATTER`, so it can never redden CI — the exact false-PASS shape this issue is about. Adding a check there would add a second, non-enforcing instrument. |
| E | Add a lint rule to `scripts/check-doc-affiliation.cjs` | Deferred, not rejected: that script is **not wired into CI** (the arm's own docstring says so), so it is a second advisory instrument, not the enforcement. Filed as a follow-up if it recurs; A already closes the class on the CI path. |

**Re-derivation note (PLAN-VERIFY round 1, P3):** the deferred `config/ci-surfaces.yml` rationale
lives at **`:13139`**, not `:13061` (line 13061 is a different module's cost note — the issue body's
citation is wrong and this plan inherited it; corrected here).

## 3. Complexity + domains

- **Tier:** Standard (Config `standard`, per the issue).
- **Domains:** data (8 one-line data fixes) + verification (one assertion extension).
- **No** UX, ontology, schema, migration, dependency or config-flag change.
- **Adversarial domain: NO.** The assertion is not gate/enforcement code an attacker could defeat
  to make something fail open in production; it is a CI test arm whose failure mode is being
  **too weak**, which the mutant proof covers. Declared explicitly so this is not an undeclared gap.

## 4. Integration Surface Map

| Surface | Consumer | Test layer that proves it |
|---|---|---|
| the 8 `docs/research` blocks | `parse_frontmatter` → index pipeline | the pytest arm (this change) |
| **5 pins that actually OPEN the 8 files** — `test_entity_attachment_brief_claims.py`, `test_12184_shifted_cites.py`, `test_m3_fts_leg_recall_12298.py`, `test_candidate_recall_11901.py`, and `test_entry_point_python_guard.py`'s docs scan (`test_capacity_page.py`, `test_name_entropy_12294.py` and `tools/edge_census.py` only NAME them in prose and open nothing — corrected after round 2) | those tests | all 5 run on both trees; failure sets **identical** (`7 failed, 784 passed` either side), so unaffected is a diff of two runs, not an assertion |
| `tests/test_doc_affiliation_frontmatter.py` | CI `tier1` + `core` (dual-registered) | itself |
| `config/ci-surfaces.yml` (2 registrations) | CI lane selection | unchanged; the file is not edited |
| `tortoise/file_indexer.py` | `docs_miner`, `sdk`, `ingest` | **unchanged** — `tests/test_file_indexer.py` must stay green |

## 5. Implementation steps

### Task 1: restore the 8 values and make the arm measure survival

**Intent:** close the class, not the instance — the guard must be one that would have caught the
merged dossier, so the mutant has to redden it.
**Acceptance:** `0` truncated values across the population the arm globs; unquoting one
`aboutObjects` reddens exactly this arm; `tests/test_file_indexer.py` unchanged and green.
**Files:**

- Modify: `docs/research/2026-09-24-2730-entity-attachment/research-brief.md`
- Modify: `docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md`
- Modify: `docs/research/2026-09-25-4503-edge-accounting/measurement.md`
- Modify: `docs/research/2026-11-12-11901-candidate-recall/research-brief.md`
- Modify: `docs/research/2026-11-12-12186-search-inventory/round-3-verification.md`
- Modify: `docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`
- Modify: `docs/research/2026-11-17-13108-dense-leg/research-brief.md`
- Modify: `docs/research/2026-11-18-13420-vector-placement/dossier.md`
- Modify: `tests/test_doc_affiliation_frontmatter.py`
- Create: this plan doc

**Steps:**

1. Wrap each of the 8 `aboutObjects` values in one pair of double quotes. No other character of any
   value changes (verified by comparing the parsed value to the pre-fix raw text, not by eye).
   **Precisely:** the dossier's `aboutSubjects` is already quoted; the other seven `aboutSubjects` are
   **plain and contain no space-preceded `#`, they parse correctly, and they are OUT OF SCOPE — do
   not quote them.**
   The precedent is the quoting *style*, not the field.
2. Add `_values(fm)` -> `(key, kind, payload)` classifying each top-level value as `scalar`,
   `scalar_multiline`, `sequence`, `sequence_multiline` (a `key:` followed by `- item` lines, which may
   sit at column 0), or `other` (block scalar / flow collection / nested mapping). Add `_quoted_style(raw)` ->
   `(style, body)` — `plain` / `double-quoted` / `single-quoted` / `broken` / `malformed` /
   `escaped` — de-quoting by hand, never via `yaml.safe_load`, plus `_split_comment(raw)` (which only
   splits a QUOTED value, so a quoted value may carry a trailing comment) and `_has_unescaped_dq`.
3. In `test_research_doc_frontmatter_parses_as_yaml`, after the presence check, measure the
   affiliation VALUE four ways — one check is provably not enough, and review found a real hole in
   each earlier design:

   - **(A) the whole value is a comment.** A value whose text BEGINS with `#` resolves to `None` and
     100 % of it is gone. **Unambiguous** — a key with a real value never becomes `None` because of a
     comment (`null`/`~` carry no `#`) — so no digit needs to appear. Checked for **every** key and for
     sequence items, which is what reaches `aboutObjects: #11395, #13082` → `None`, `aboutObjects: # TODO
     decide later` → `None`, and a `- #11395` item → `None`. An `isinstance(value, str)` comparison
     misses all three.
   - **(B) SURVIVAL, for the affiliation keys.** The parsed value must equal the **RAW** text (plain
     style) or the de-quoted body (quoted style). Plain is compared against RAW **because** that is what
     makes a shortened value fail: an earlier draft stripped the comment during comparison, which made
     the check tautological and let it claim coverage it did not have (found in code review; measured:
     it flagged 0 of the 8). It now flags **8 of 8** on the unfixed tree, `len 82 → 48 … 141 → 57`.
   - **(C) quoting integrity.** An unescaped inner `"`, an unbalanced `'`, or a missing close
     TERMINATES the scalar (`"a, "#1" KEEP"` → `'a, '`). The unescaped-quote test runs **before** the
     escape branch: an escaped body can still carry an unescaped quote, and the reverse order let that
     truncation through green (found in code review).
   - **(D) no silent omission.** `declared affiliation values == measured`, minus a deliberately empty
     `UNMEASURED` set. `aboutObjects: |`, `>`, `[...]`, `{...}` reach no comparison, so they **FAIL**
     with both remedies named. `_KV` also accepts a **whitespace-only** value — a key followed only by
     spaces, e.g. `aboutObjects:` and then nothing — so it cannot slip past enumeration either (that
     was a one-keystroke bypass of (D)).
   - **(E) sequences, item by item.** A `sequence` affiliation value was previously counted as
     "measured" while only (A) reached it — so a mid-item truncation
     (`- tortoise-hosted-platform, #4333 census, #5380`) and a quoted item cut at an inner quote were
     both GREEN, in a shape the population actually carries (3 affiliation sequences). Measured, then
     fixed: each item now gets the quoting check and a value comparison, and a length mismatch is a
     failure. The counter now reads every affiliation value as *compared*, not just counted.
   - **Refused styles:** a multi-line **plain** value, and a sequence item continued on a following
     line — both legal folding, both swallow a `#`-opening continuation, neither verifiable without
     becoming a YAML implementation. A **quoted** value is CLOSED, so an indented comment line after it
     is not a continuation; getting that distinction backwards was measured in both directions (a
     false red on a legal block, and a truncated continuation passing green).
4. **Residuals, stated in the code rather than omitted:** (i) `|`/`>`/`[`/`{` affiliation values are
   deliberately not compared — such text is preserved by construction — but they still FAIL (D), so
   adopting one requires an explicit `UNMEASURED` entry with a reason; (ii) a character-level typo
   inside a quoted value is NOT a truncation
   and stays green — measured (M6), and claiming otherwise would be a false claim of coverage;
   (iii) a deliberate trailing comment on an **affiliation** value will red (M18) — fail-closed, and the
   remedy (quote the value) genuinely clears it, unlike the shapes review found where it did not.
5. Update the module docstring (a new check 6) and the arm's docstring to say that **survival** is
   measured and by which checks, keeping #13432's non-vacuity note intact.

**Do not:** touch `parse_frontmatter`, `frontmatter_validator.py`, `config/ci-surfaces.yml`
rationale drift, or the 2 pre-existing absence reds.

## 6. Wiring

| Touch point | Type | Covered by | What it does there |
|---|---|---|---|
| `tortoise.file_indexer.parse_frontmatter` | python consumer | `tests/test_file_indexer.py` (unchanged) | the read path this fix depends on; **not modified** |
| `tortoise/docs_miner.py:352` | importer | `tests/test_file_indexer.py` | consumes the same parse; unaffected by quoting data |
| `tests/test_doc_affiliation_frontmatter.py:220` | the arm itself | `config/ci-surfaces.yml:3719` (`tier1`) + `:10652` (`core`) | dual-registered, so it runs for a docs-only diff AND for the full suite |
| `docs/research/**/*.md` (68 blocks) | data | the arm's glob `:241` | the population the assertion iterates |
| `scripts/check-doc-affiliation.cjs` | commit-time advisory | not CI-wired | **not touched**; deferred per §2 alternative E |

## 7. Verification plan — measured (PROVE evidence)

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run --frozen python -m pytest tests/test_doc_affiliation_frontmatter.py -q`

1. **Unfixed tree** (this test file against `origin/main` content): `3 failed, 6 passed` — the research
   arm reds on **exactly the 8** `aboutObjects` values, each carrying the **RAW-vs-parsed length pair**
   (`len 82 → 48`, `81 → 67`, `93 → 73`, `119 → 75`, `107 → 92`, `155 → 46`, `115 → 87`, `141 → 57`);
   the other 2 failures are the pre-existing absence reds. **(B) fires 8 times — counted from the
   failure output, not asserted.**
2. **Fixed tree:** `2 failed, 7 passed` — the 2 are the pre-existing absence reds
   (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`, `docs/evidence/13038-capstone/README.md`),
   unchanged, named, and deliberately not "fixed" here. `git diff --stat` vs `main` = 9 files + 1 plan doc.
3. **Mutant matrix — 20/20 behave as designed** (`/tmp/matrix_13452.sh`, tree restored byte-identical
   after each; M13–M18 were added after code review):

   | # | mutant | expected | observed |
   |---|---|---|---|
   | M1 | unquote the dossier's `aboutObjects` | RED (B) | RED — `len 141` → `len 57` |
   | M2 | `aboutObjects: #11395, #13082` (whole value a comment) | RED, **not** skipped as `None` | RED (A) |
   | M3 | inner unescaped `"` in a quoted value | RED (C) | RED |
   | M4 | multi-line plain continuation opening with `#` | RED (refused) | RED |
   | M5 | `- #11395` sequence item | RED (A) on items | RED |
   | M6 | a typo inside a quoted value (not a truncation) | **GREEN — stated** | GREEN |
   | M7 | `aboutObjects: \|` (block scalar) | RED (D) | RED |
   | M8 | `type: research  # a comment` (deliberate) | GREEN | GREEN |
   | M9 | `"Vector placement"  # short title` | GREEN | GREEN |
   | M10 | `"…, \"#11395\" KEEP"` (legal escape) | GREEN | GREEN |
   | M11 | sequence item continued on the next line | RED (refused) | RED |
   | M12 | `aboutObjects: [a, '#11395']` (flow) | RED (D) | RED |
   | M13 | `aboutObjects: # TODO decide later` (no digits) | RED (A) | RED |
   | M14 | `"Vector placement"  # see #13452` (affiliation, trailing comment) | GREEN | GREEN |
   | M15 | `aboutObjects:` + trailing spaces only | RED (D) | RED |
   | M16 | escaped body **and** an unescaped inner quote | RED (C) | RED |
   | M17 | quoted value + indented comment line | GREEN — not a fold | GREEN |
   | M18 | `aboutObjects: A, # section 2` (affiliation tail is a comment) | RED (B), fail-closed | RED |
   | M19 | sequence item truncated mid-text (`, #4333 census, #5380`) | RED (E) | RED |
   | M20 | quoted sequence item cut at an inner quote | RED (E) | RED |

   (An earlier draft also listed "an extra item appended" as a mutant; that is a legitimate edit, not a
   truncation, and it correctly stays green — the expectation was wrong, not the arm.)

4. `parse_frontmatter` on the dossier returns `aboutObjects` at its full raw length.
5. `tests/test_file_indexer.py` untouched. The pins that actually OPEN these files were run on both
   trees: the 4 git-free ones give **identical** results (`3 failed, 182 passed` either side). The 5th
   (`test_entry_point_python_guard`) shells out to `git ls-files`, so it cannot run against main content
   in a scratch tree and is **not** claimed identical here; on this branch its contribution is 4 named
   pre-existing cases. Whole sibling batch: `7 failed, 784 passed, 2 skipped` (4 + 2 + 1 pre-existing).

## 8. Review Scope Contract

**Class:** `code` (a CI test arm plus data fixes; not a gate/enforcement change, not adversarial
— declared explicitly in §3).

**Risk surface** — each with the check that would catch it:

- **R1** the assertion is **vacuously green** — it skips what it means to measure, or reaches only
  shapes that are already correct → (A) is type-blind and unambiguous, (C) covers the quoted mangle,
  and **(D)** FAILS on any declared affiliation value that reached no check. Mutants
  M1/M2/M3/M5/M7/M11/M12/M13/M15/M16/M18/M19/M20 red it — including the shape review found last: a `sequence` value counted
  as measured while no value-comparing check touched it. This is the risk that actually bit: review proved
  **(B) was tautological** in an earlier draft (0 of 8 flagged) because the comparison stripped the
  comment first; it now compares against RAW and flags 8 of 8.
- **R2** the assertion **over-blocks** — a legal YAML construct reported as truncation → measured, not
  asserted: a deliberate trailing comment (M8), a quoted value with a trailing comment (M9), a legal
  escaped body (M10) and an indented comment line after a quoted value (M17) all stay **GREEN**. The
  shapes that do fail closed are refused with a message naming a remedy that actually clears them.
- **R3** quoting **truncates** a value (unescaped inner `"`, unbalanced `'`, missing close) → caught by
  **(C)**, which runs **before** the escape decline — the reverse order let an escaped body with an
  unescaped quote through green (M16 now reds). Scoped honestly: truncation-at-a-quote, NOT a
  character typo (M6 green, stated).
- **R4** **scope creep** (the 2 absence reds or the consumer get "fixed") → identical before/after
  failure sets + `git diff --stat` naming exactly 9 modified files + 1 new plan doc.

**Review budget disclosure.** PLAN-VERIFY spent its **3** cycles (the standard cap); round 1 found an
`isinstance(str)` fail-open that skipped a 100 %-lost value, round 2 found R3 unreachable and a false
population claim, round 3 found (D) inert for block/flow plus three over-blocks — all verified by
measurement, fixed, and covered by the matrix. Because the cap is spent, the *diff* then went to the
**head-bound code review**, which found two more real defects — **(B) tautological** and **(C)
unreachable behind the escape branch** — both verified by measurement, fixed, and now covered by M1 and
M16 / M18. The attestation for this PR is recorded at the FINAL head, after those fixes.

**Out of scope — must NOT be raised** (note, file, do not chase):

- the 2 pre-existing absence reds (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`,
  `docs/evidence/13038-capstone/README.md`) — separate defect, explicitly excluded by the issue;
- `config/ci-surfaces.yml:13139` rationale drift (category B, no behavioural consequence);
- other directories' unquoted-plain-scalar hazard (unmeasured here — stated as a gap);
- the 7 sibling `aboutSubjects` in these files that are plain-but-parse-correctly (quoting them is
  not this task and would inflate the diff);
- `parse_frontmatter`'s tolerant-degrade design (a recorded invariant, not a defect).

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared
surface.
