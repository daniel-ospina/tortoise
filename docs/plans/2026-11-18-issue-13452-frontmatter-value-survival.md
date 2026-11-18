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

**Status:** Draft (pre PLAN-VERIFY)
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

```
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
   **plain and contain no ` #`, they parse correctly, and they are OUT OF SCOPE — do not quote them.**
   The precedent is the quoting *style*, not the field.
2. Add `_values(fm)` -> `(key, kind, payload)` classifying each top-level value as `scalar`,
   `scalar_multiline`, `sequence` (a `key:` followed by `- item` lines, which may sit at column 0),
   or `other` (block scalar / flow collection / nested mapping). Add `_expectation(raw)` ->
   `(style, expected)`, computing the expected value **by de-quoting by hand**, never via
   `yaml.safe_load`.
3. In `test_research_doc_frontmatter_parses_as_yaml`, after the presence check, run the checks that
   together leave no escape — one check is provably not enough, and rounds 1 and 3 each demonstrated a
   distinct hole in the previous design:

   - **(A) the MECHANISM on the RAW text.** `_LOST_REF = re.compile(r"(?:^|\s)#\d")` — a `#` + digit
     at the start of the value or after whitespace, i.e. an **issue reference sitting where YAML reads
     a comment**. A match is a truncation by construction, whatever type the prefix resolves to, so it
     reaches the three shapes an `isinstance(value, str)` comparison provably misses: 
     `aboutObjects: #11395, #13082` → `{'aboutObjects': None}` (100 % lost), `issue: 3615 # refs` →
     `3615`, and a `- #11395` item → `None`. **Deliberately narrow:** a trailing `# a comment` on a
     plain value is legal YAML that loses no affiliation, and an arm that reds on it would be the
     over-block it exists to avoid (measured: `type: research  # a comment` stays green).
   - **(B) value survival** for the styles where YAML performs **no legal transform** — plain
     (expected == raw, minus any trailing comment) and simply-quoted (expected == the de-quoted body) —
     requiring `parsed[key] == expected`. A and B run **independently**; the `break` only ends the
     per-text loop, so on the unfixed tree (B)'s denominator is **470 with 8 mangled**, every one
     already flagged by (A). The 462/0 pair an earlier draft reported came from a short-circuiting
     variant that is **not** what ships.
   - **(C) quoting integrity** — the shape (B) cannot see and R3 names: an unescaped inner `"`, an
     unbalanced `'`, or a missing closing quote TERMINATES the scalar. Measured:
     `"STORAGE-ARCHITECTURE.md, "#11395" KEEP"` parses to `'STORAGE-ARCHITECTURE.md, '`. A legal
     escaped body (`\"`) is not a defect and is declined rather than reddened.
   - **(D) no silent omission** — `declared affiliation values == measured`, minus an explicit,
     deliberately-empty `UNMEASURED` set of `(path, key)` pairs. This is what makes "no file is
     exempted and there is no baseline" true rather than asserted, and it is the check that catches a
     style nobody taught the arm about: `aboutObjects: |`, `>`, `[...]`, `{...}` are classified
     `other`, reach no comparison, and therefore **fail (D)** with a message naming both remedies.
   - **Refused styles:** a multi-line plain value, and a sequence whose item is continued on a
     following indented line. Both are legal YAML folding, both swallow a continuation line that opens
     with `#` (measured: parses to `'A,'`), and neither can be verified without becoming a YAML
     implementation — so the arm refuses them and asks for quotes. Zero instances today.
4. **Residuals, stated in the code rather than omitted:** (i) `|`/`>`/`[`/`{` affiliation values are
   *deliberately* not compared — such text is preserved by construction — but they still FAIL (D), so
   adopting one requires writing an explicit `UNMEASURED` entry with a reason; (ii) sequence items are
   checked for a lost reference but not compared value-by-value; (iii) a character-level typo inside a
   quoted value is NOT a truncation and stays green — measured (M6), and claiming otherwise would be a
   false claim of coverage.
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

1. **Unfixed tree** (this test file run against `origin/main` content): `3 failed, 6 passed` — the
   research arm reds on **exactly the 8** `aboutObjects` values, each carrying BOTH the lost-reference
   message and the length pair (`90 affiliation values measured, 470 values compared`); the
   other 2 are the pre-existing absence reds.
2. **Fixed tree:** `2 failed, 7 passed` — and the 2 are the **pre-existing absence reds**
   (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`, `docs/evidence/13038-capstone/README.md`),
   unchanged, named, and deliberately not "fixed" here. `git diff --stat` = 9 files + 1 plan doc.
3. **Mutant matrix — 12/12 behave as designed** (script `/tmp/matrix_13452.sh`, tree restored
   byte-identical after each):

   | # | mutant | expected | observed |
   |---|---|---|---|
   | M1 | unquote the dossier's `aboutObjects` | RED (A)+(B) | RED — `len 141` → `len 57` |
   | M2 | `aboutObjects: #11395, #13082` (whole value a comment) | RED, **not** skipped as `None` | RED (A) |
   | M3 | inner unescaped `"` in a quoted value | RED (C) | RED — `is a quoted value YAML cannot keep whole` |
   | M4 | multi-line plain continuation opening with `#` | RED (refused) | RED |
   | M5 | `- #11395` sequence item | RED (A) on items | RED |
   | M6 | a typo inside a quoted value (not a truncation) | **GREEN — stated** | GREEN |
   | M7 | `aboutObjects: \|` (block scalar) | RED (D) | RED |
   | M8 | `type: research  # a comment` (deliberate comment) | GREEN — no over-block | GREEN |
   | M9 | `"Vector placement"  # short title` | GREEN | GREEN |
   | M10 | `"…, \"#11395\" KEEP"` (legal escape) | GREEN | GREEN |
   | M11 | sequence item continued on the next line | RED (refused) | RED |
   | M12 | `aboutObjects: [a, '#11395']` (flow) | RED (D) | RED |

4. `parse_frontmatter` on the dossier returns `aboutObjects` at its full raw length.
5. `tests/test_file_indexer.py` untouched. The 5 pins that actually OPEN these files were run on both
   trees: the 4 git-free ones give **identical** results (`3 failed, 182 passed` either side); the 5th
   (`test_entry_point_python_guard`) cannot be run against main content in a scratch tree because it
   shells out to `git ls-files`, so its main-side run is **not** claimed here. On this branch its
   contribution to the 7 is 4 named cases (`test_entry_point_guards_the_interpreter_before_its_imports`
   and `test_guard_refuses_an_old_interpreter_and_passes_the_floor`, for
   `tools/capstone_state_value.py` and `tools/paired_expansion_ab_2518.py`), all pre-existing; the whole
   sibling batch is `7 failed, 784 passed, 2 skipped` with the breakdown 4 + 2
   (`test_m3_fts_leg_recall_12298`) + 1 (`test_12184_shifted_cites`).

## 8. Review Scope Contract

**Class:** `code` (a CI test arm plus data fixes; not a gate/enforcement change, not adversarial
— declared explicitly in §3).

**Risk surface** — each with the check that would catch it:
- **R1** the assertion is **vacuously green** — it skips the blocks it means to measure, or reaches
  only value shapes that are already correct → caught by (A) being type-blind, by (C) for the
  quoted-mangle shape, and by **(D)**, which FAILS on any declared affiliation value that reached no
  check at all — the mechanism that closes `aboutObjects: |` / `[...]` / `{...}` (M7, M12). Mutants
  M1/M2/M3/M5/M7/M11/M12 red it.
- **R2** the assertion **over-blocks** — a legitimate YAML construct reported as truncation → measured,
  not asserted: on the unfixed tree (A) flags exactly the 8 known defects and nothing else; a
  deliberate trailing comment (M8), a quoted value with a trailing comment (M9) and a legal escaped
  body (M10) all stay **green**. The two styles that would genuinely over-block — multi-line folding
  and a continued sequence item — are **refused with a message naming the fix**, and have zero
  instances in this population.
- **R3** quoting **truncates** a value (unescaped inner `"`, unbalanced `'`, missing close) → caught by
  **(C)** on the raw text; M3 reds it, M10 (a legal escape) stays green. Scoped honestly: R3 covers
  truncation-at-a-quote, NOT a character-level typo — M6 stays green and is reported as such.
- **R4** **scope creep** (the 2 absence reds or the consumer get "fixed") → identical before/after
  failure sets + `git diff --stat` naming exactly 9 modified files + 1 new plan doc.

**Review budget disclosure.** PLAN-VERIFY spent its **3** cycles (the standard cap): round 1 found an
`isinstance(str)` fail-open that skipped a 100 %-lost value; round 2 found R3 unreachable, a false "no
block sequences in this population", and the unstated (A)→(B) ordering; round 3 found (D) inert for
block/flow styles, three over-blocks (deliberate comments, quoted+comment, legal escapes), and an
invisible sequence-item continuation. Every round-3 finding was verified against the primary source by
measurement and fixed, and the fix is validated by the 12-mutant matrix — but a 4th PLAN-VERIFY round
would exceed the cap, so the *fix to round 3's findings* is **not** covered by a 4th plan-verify
review. The independent check on the final code is the head-bound code review at the merge gate.

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
