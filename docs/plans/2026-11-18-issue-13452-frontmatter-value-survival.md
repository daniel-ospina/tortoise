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
| `tests/test_doc_affiliation_frontmatter.py:492` | the arm itself | `config/ci-surfaces.yml:3728` (`tier1`) + `:10678` (`core`) | dual-registered, so it runs for a docs-only diff AND for the full suite |
| `docs/research/**/*.md` (68 blocks) | data | the arm's glob `:557` | the population the assertion iterates |
| `scripts/check-doc-affiliation.cjs` | commit-time advisory | not CI-wired | **not touched**; deferred per §2 alternative E |

## 7. Verification plan — measured (PROVE evidence)

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run --frozen python -m pytest tests/test_doc_affiliation_frontmatter.py -q`

1. **Unfixed tree** (this test file against `origin/main` content): `3 failed, 6 passed` — measured at
   `268737ae6cd9` (this branch's first commit) against `main`'s then-content, and **not reproducible
   from this head**: the file has since grown to 14 tests and `main`'s docs have moved. The research
   arm reds on **exactly the 8** `aboutObjects` values, each carrying the **RAW-vs-parsed length pair**
   (`len 82 → 48`, `81 → 67`, `93 → 73`, `119 → 75`, `107 → 92`, `155 → 46`, `115 → 87`, `141 → 57`);
   the other 2 failures are the pre-existing absence reds. **(B) fires 8 times — counted from the
   failure output, not asserted.**
2. **Fixed tree:** `2 failed, 12 passed` **at this head** — the same 2 failures throughout, and each
   count is tied to the head that produced it, because tests are appended to this file every round:
   `8 passed` pre-rebase, `9 passed` at the rebased `36700a89b7` (the extra pass is `#13485`'s own new
   test, which main added under ours), `10 passed` once the `''`-parity pin landed, `11 passed` once
   the `"`-backslash pin landed, and `12 passed` once the (C)-ordering pin landed. The 2 are the
   pre-existing absence reds
   (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`, and the evidence bundles
   `docs/evidence/13038-capstone/README.md` **+** `docs/evidence/13436-write-path-costs/README.md` —
   the second bundle landed on `main` in `#13436` AFTER this plan was written, so the red now names
   two bundles), unchanged, named, and deliberately not "fixed" here. The baseline was measured on
   `origin/main` at `f6793959a2` too (`2 failed, 8 passed`) — same two tests, same paths.
   `git diff --stat` vs `main` = **2 files** (this plan doc + the test file) — the `9 files + 1 plan
   doc` this line used to carry was #13476's tree, measured at THAT head, and was false here.
3. **Mutant matrix — 22/22 behave as designed** (M1–M20 by `/tmp/matrix_13452.sh`; M13–M18 were
   added after code review, and M21/M22 were measured separately in `e488d2adcc`'s scratch run —
   one mutation at a time, tree restored byte-identical between each):

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
   | M21 | `aboutObjects : <unquoted value>` (one space before the colon) | RED (D) | RED |
   | M22 | `"aboutObjects": <unquoted value>` (quoted key) | RED (D) | RED |

   M21/M22 are the FAIL-OPEN a second review found, and they are the reason check (D)'s `declared`
   set is now derived from the **parsed** block as well as from `_values`. `_KV` lexes one spelling
   (key at column 0, colon immediately after it); YAML also accepts a space before the colon and a
   quoted key, and both spellings keep the key, truncate the value at a space-preceded `#`, and
   match `_KV` not at
   all — so `declared` and `measured` were BOTH empty for such a file and the round-1 tree reported
   all seven arms green over a 57-of-141-character value. Measured, one mutation at a time, tree
   restored between: round-1 tree GREEN on both, this head RED on both.

   The **third** spelling — a frontmatter block whose keys are uniformly indented off column 0 —
   behaves differently from the other two, and getting that wrong is instructive: YAML accepts an
   indented ROOT node, so the block PARSES (measured: every key of `dossier.md`'s block indented one
   space still returns all **10** keys from `parse_frontmatter`, `aboutObjects` already truncated to
   `'… §17.1/§17.2,'`). It therefore reaches (D) — it is NOT caught by the parse arm — and it is
   closed by the same parsed-derived `declared` line as the other two. Only a block indented
   **including its `---` fences** degrades to `{}`: `yaml.safe_load` rejects it with `mapping values
   are not allowed here`, so THAT variant reds one arm earlier. An earlier revision of this
   paragraph claimed the parse arm caught both; it was wrong, and the error was mine — the mutation
   I measured had indented the closing fence too, so it was mixed indentation (a parse error) and not
   the uniform-indent case at all. Round 4 caught it and the record is corrected here.

   **Round 3 — the diagnostics, not the checks.** A third fresh review found three messages that
   gave the WRONG remedy or a false diagnosis. None let a defect through; each would have sent a
   reader to the wrong place, which is the failure mode this whole issue is about. All three were
   verified by measurement before applying:

   - (D) said **"Quote the value"** for a pair it can reach only because the KEY is unlexable, and
     quoting cannot clear that. Measured: `aboutObjects : "a, #11395 KEEP, #13220"` parses INTACT
     and still reds, so the message now names the respelling (the remedy that actually works).
   - The **sequence-item** branch still said "a sequence item has no legal transform, so this is a
     truncation" for `- 13452` -> int, the exact false diagnosis retired for scalars 20 lines
     above; one file gave two contradictory diagnoses for one mechanism. Now shared.
   - The new scalar wording **overcorrected**: `aboutObjects: 13452 #11395 reopen, #13220` parses
     to int `13452`, and 22 author-written characters were terminated at the space-preceded `#` —
     this issue's defect — while the message said "not a truncation". Both non-string members are
     now one helper that decides from the RAW text: a space-preceded `#` in it means the tail was
     cut, no such character means implicit typing lost nothing.

   **Round 4 — the record, and the third cause.** A fourth fresh review found no fail-open either,
   and three defects in what round 3 WROTE. All three measured before applying:

   - The **falsification above was itself false** — corrected there, and the reason is the general
     one: a mutation that is not the construction under test produces a confident measurement of a
     different thing.
   - `tests/test_doc_affiliation_frontmatter.py`'s own docstring said a block that degrades to `{}`
     is red by **(A)/(B)**. It cannot be: the `if not parsed: … continue` above the loop means the
     PARSE arm reds it and (A)/(B) are never reached. The sentence contradicted the control flow
     three lines below it.
   - (D)'s remedy listed **two** of the three key spellings that reach it, and for the missing one —
     an indented block — the prescribed edit did not clear the red: dedenting the named key alone
     produces mixed indentation, a parse error with a less useful message. Each spelling now gets
     its own remedy, decided from the raw block: respell the key, unquote the key, or dedent the
     whole block. Measured on all three, value fully quoted so only the key differs — every one
     reds, and each names the edit that actually clears it.

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
M16 / M18. The attestation for #13476 is recorded at its FINAL head (`9f25dc79c9`), and that record
is `not-clean` with two findings — the escape one fixed in #13484, and the counter residuals now
listed in §9. #13484's own attestation is minted at its own final head, after this section.

**Out of scope — must NOT be raised** (note, file, do not chase):

- the 2 pre-existing absence reds (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`, and the
  evidence bundles `docs/evidence/13038-capstone/README.md` +
  `docs/evidence/13436-write-path-costs/README.md`, the latter landed by `#13436` after this plan) —
  separate defect, explicitly excluded by the issue;
- `config/ci-surfaces.yml:13139` rationale drift (category B, no behavioural consequence);
- other directories' unquoted-plain-scalar hazard (unmeasured here — stated as a gap);
- the 7 sibling `aboutSubjects` in these files that are plain-but-parse-correctly (quoting them is
  not this task and would inflate the diff);
- `parse_frontmatter`'s tolerant-degrade design (a recorded invariant, not a defect).

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared
surface.

## 9. Review-round residuals — each closed, fixed, or recorded with its reason

Head-bound review rounds ran past the recorded attestation (`5fc01193de` → `e488d2adcc`
→ `7963c4a851` → `9f25dc79c9`; this branch's own commit messages number the reviews of those
heads 1, 3, 4 and 5). The first of them is the one that matters: reviewing the attested
head `5fc01193de`, a fresh-context reviewer found a **FAIL-OPEN in check (D)** — the arm whose
entire job is "no affiliation value escapes measurement" — not a message defect; it is closed in
`e488d2adcc` and listed below. The later rounds found no further fail-open, but the last of them —
the round-5 review of `9f25dc79c9` — found a **false block**: a wrong VERDICT, not only a message.
Legal YAML `'the ''KEEP'' #11395 ruling'` was read as an unbalanced quote and check (C) failed it;
that one is fixed in `268737ae6cd9` — rebased onto this branch as `4bc4d177a850`, its first commit —
and pinned below. Where a finding
was applied it was measured first, and where it was falsified the record was corrected in place
rather than restated. What is left is listed here so the next reader inherits the measurement
instead of repeating it.

**Already fixed in `e488d2adcc` — a FAIL-OPEN in check (D).** Round 1 reviewed the attested head
`5fc01193de` and found that (D) could be starved of BOTH sides: `declared` came only from
`_values`, whose `_KV` lexer accepts one spelling (key at column 0, colon immediately after it),
while YAML also accepts `aboutObjects :` and `"aboutObjects":`. For either spelling `declared`
and `measured` were both empty, nothing was compared, and all seven arms reported GREEN over a
57-of-141-character value — the exact #13452 defect the guard exists to close, reachable with a
one-keystroke typo. `declared` is now derived from the PARSED block as well (`M21`/`M22` in §8's
matrix). It is recorded here because this section's job is to hand on the measurement, and a
fail-open the arms survived is the one residual a reader must not have to rediscover.

**Fixed at this head — a single-quoted `''` escape read as an unbalanced quote.**

`'the ''KEEP'' #11395 ruling'` is legal, intact YAML: `parse_frontmatter` returns
`the 'KEEP' #11395 ruling`. But the `''` escape leaves a quote immediately before the space that
introduces the comment, so a close located by `raw[:i].rstrip().endswith(q)` fired at the wrong
`#` and check (C) reported *an unbalanced quote … remove the inner quote* — a remedy that asks the
author to delete a legal escape. `_split_comment` now locates the close by PARITY
(`_closing_quote`), so only a `#` that follows the true closing quote is a comment. Measured: the
value is now `('single-quoted', ...)`, whose text is `the 'KEEP' #11395 ruling`, and the consumer
agrees; the (C) mutant `"STORAGE-ARCHITECTURE.md, "#11901" KEEP"` has no space-then-hash after its
close and STILL reds `broken`, so the truncation check is unchanged. Pinned by
`test_a_single_quoted_escape_before_a_hash_is_measured_not_broken`, which fails on the pre-fix
helper (`('broken', None)`) — verified by re-applying the old body.

The fix has TWO halves, and the first pin only covers one of them (found in the next review).
That value's `''` sits immediately before the space that introduces the comment, so the
close-POSITION test alone refuses the wrong split and `_closing_quote`'s `''` skip never decides
anything — measured: deleting the skip
left that test GREEN, while a legal mid-body escape under a trailing comment, `'it''s' # comment`,
regressed from `('single-quoted', "it's")` to `('malformed', None)` (the same *delete a legal
escape* remedy), with green CI. `test_a_single_quoted_escape_mid_body_under_a_trailing_comment_is_measured`
now pins the parity half: with the skip deleted it reds and the sibling stays green
(`-k single_quoted` → `1 failed, 1 passed`), so each half names its own falsifier.

**Fixed at this head — the `"` backslash skip had no pin (round-4 review).**

`"a\"" # c` is legal YAML — `parse_frontmatter` returns `a"` — and the TRUE close sits
immediately before the space-then-hash that introduces the comment (for that raw the escaped quote
is at index 3, the true close at 4, the `" #"` at 5). A close found WITHOUT skipping backslash
escapes lands on the escaped quote at index 3 instead, so NO space-then-hash satisfies the split predicate:
nothing is stripped, the raw text reaches `_quoted_style` still ending in `# c`, and
`value.endswith(q)` fails — the arm reports `('malformed', None)`, the same
*remove the inner quote* class of false remedy this PR removes. The mechanism is stated this
precisely because it is NOT a mis-landed split: no split fires at all. (This paragraph carried the
*landed on the escaped quote ⇒ the split took everything after it* wording until it was measured;
the sibling parity test's docstring was corrected for the same wording first, and this copy
survived that fix — recorded because the wrong mechanism is the one a reader would re-derive.)
This is the one arm the PRE-fix
helper already got RIGHT (it never scanned for a close: it cut at the space-then-hash whose left
side ended in the quote char, which this shape satisfies), so the backslash skip is old behaviour carried by a
NEW branch — and nothing else in the file reds when it goes: measured with the skip deleted at
`f426ea53fa` (no pin in the file yet) the file stayed at `2 failed, 10 passed` — the same two
pre-existing absence reds, nothing else noticing — while this shape regressed. Pinned now by
`test_a_double_quoted_backslash_escape_before_a_comment_is_measured`; with the pin in place the same
mutant reds this test alone (`-k double_quoted_backslash`), so the file's
total under that mutant is the 2 pre-existing absence reds **plus** this one — no file-wide count is
printed here, because it moves every time another test is appended (an earlier revision quoted
`1 failed, 12 deselected` for that filter, which went stale the same way). Same finding shape as the parity
half above, and the same reason it is worth its own test: `_closing_quote` added branches, and a
branch nothing asserts is a branch a later simplification is free to drop.

**Fixed at this head — the (C) ordering had no committed pin, and it is the ONE fail-open here.**

§8's M16 measures that an unescaped inner `"` is reported `broken` even when the body also carries a
legal `\`, i.e. that `_quoted_style` tests `_has_unescaped_dq` BEFORE the escape branch. That order
was pinned only by `/tmp/matrix_13452.sh` — a scratch script that no longer exists — never by a
committed assertion. Measured with `if _has_unescaped_dq(body):` mutated to `if False:`: the whole
file stays GREEN (`2 failed, 11 passed` at `3b8de63b8a`, the same two pre-existing reds) while two
values the consumer TRUNCATES stop being reported — `"a, "#1" KEEP"` (YAML reads `'a, '`) goes
`('broken', None)` → `('double-quoted', …)`, and `"a\tb\" "#1" KEEP"` (YAML reads `'a\tb" '`) goes
`('broken', None)` → `('escaped', None)`, where `escaped` declines the comparison entirely. That is a
value escaping measurement — the #13452 defect this guard exists to prevent — behind green CI, and it
is the only branch of this helper whose loss is a fail-open rather than a mis-worded remedy; the two
pins above are the other class. `test_an_unescaped_quote_is_broken_even_beside_a_legal_escape` now
pins both shapes (unit-level on `_quoted_style`, which is where the ordering decides) and reds alone
under that mutant (`3 failed, 11 passed`; the 2 pre-existing reds plus this one).

**Already fixed — the sequence branch's false diagnosis.** `- 13452` (int) once reported "a
sequence item has no legal transform, so this is a truncation" while the scalar branch said
"implicit typing". Both non-string members now share `_type_mismatch`, which decides from the RAW
text: measured, `- 13452` → *"YAML implicit typing, nothing was lost"*, and
`13452 #11395 reopen, #13220` → *"a space-preceded `#` ended it at character 5 of 27, so the tail
is GONE"*.

**Recorded, not fixed — a plain value followed by an indented comment line is refused as
MULTI-LINE.** YAML reads `X` + `\n  # note` intact, so this is a false block. It is a category-B
over-block that cannot ship a defect (it fails closed and names the remedy), and the arm cannot
distinguish an indented `#` continuation from a comment without becoming a YAML implementation —
the refusal is declared in `_values`' docstring for that reason. Fixing it would require folding
support, which is out of this issue's scope.

**Recorded, not fixed — the guard's sanity counters can red on an empty population.** Two sites in
`tests/test_doc_affiliation_frontmatter.py` report the same wrong diagnosis as round 5's escape
finding, and neither is an escape: `:828-829` assert `measured` and `compared` BEFORE
`assert not failures` (`:830`), so a tree the guard samples sparsely reds on *"measured no
affiliation value at all — re-point this guard"* — advice that points at the guard instead of at
the value; and `:733` skips `escaped` before `compared += 1` (`:743`), so a tree whose affiliated
values are all escaped leaves the counter legitimately empty and fires the same message. (Line
numbers are as of this head; the rebase onto `f6793959a2` moved them — `#13485` inserted ~109 lines
above the arm — and an earlier revision of this section still cited the pre-rebase `:751-752`,
`:753`, `:658`, `:666`, which after the rebase land on unrelated code. The sequence branch carries
the same pair at `:699` / `:703`.) Both are
false blocks of an author's tree, not fail-opens — nothing escapes measurement, they fail closed
and name a remedy — and both live in the guard, not in the escape branch this PR fixes. Recorded
here because a reader meets the message before the reason; fixing the ordering is a separate change
to #13476's guard.

**Recorded, not fixed — one legal shape is newly REFUSED (a narrow over-block this PR introduces).**
Found by the fresh-context review at `7d4514f39e` and re-measured here. `aboutObjects: "\" #"# c`
(raw `"\" #"# c`) is loadable YAML — `yaml.safe_load` returns `" #`, and `parse_frontmatter` agrees
— but the arm now reports `('malformed', None)` where the pre-fix helper reported
`('escaped', None)`. The cause is the fix's own predicate, working as designed on one half and
meeting a lenient consumer on the other: the space-preceded `#` at index 3 sits INSIDE the scalar, so
`_split_comment` correctly refuses to split there (the text before it does not end at the parity
close, which is index 5), and with no space-separated `#` after that close nothing is stripped, so
the raw text reaches `_quoted_style` with a trailing `# c` and `value.endswith(q)` fails. Note the
arm is the SPEC-CORRECT side here (YAML 1.2.2 §6.6: *"Comments must be separated from other tokens
by white space characters"*, so `"…"# c` is not a comment), and PyYAML is the lenient one — but
PyYAML is the consumer this arm exists to model, so for that consumer it is a false block. It
cannot ship a defect (it fails closed and names a remedy), and it is the same class as the (C)
truncation case this PR deliberately keeps red, so clearing it means teaching the arm to accept a
non-space-separated trailing comment after a close — a lexer widening, out of this issue's scope.
Measured scale, so it is not read as larger than it is: differential over **15,548 YAML-parseable
generated raws**, exactly **1** where the arm was green before and is red now; and **0** where the
new predicate splits at a space-preceded `#` the old one refused — i.e. no fail-open was introduced on the path
this PR touches.

**Recorded, not fixed — #13476's `470 values compared` figure.** At this head the same probe
prints **90 measured / 94 compared**; the counter now counts affiliation values only. The
substance — 8 of 8 flagged, every length pair exact — reproduces unchanged. That body is NOT
edited: it carries the review attestation the gate reads, and an unrelated edit wipes that proof
(the rule is in `AGENTS.md`). The corrected figure is recorded here instead.

## 10. Rebase onto `main` — the conflict, and why the resolution is additive

This PR carried a POSITIVE conflict with `main` (`mergeStateStatus: DIRTY`), so the rail refused it
whatever the checks said. `main` had moved to `f6793959a2` and `#13485` — the post-merge repair of
`#13476`, landed AFTER this branch was cut — rewrote the SAME arm and appended its own new test
(`test_a_wholly_commented_value_gets_ONE_TRUE_diagnosis`).

A second lane reached the same rebase concurrently and force-pushed its resolution
(`7d4514f39e`, 06:31:17Z) while this one was verifying its own; the two agreed on
every line but one, so the branch continues from THAT head and this run adds only
what was missing instead of force-pushing a parallel history. The one divergence was
formatting: the sibling resolution left a single blank line between the two top-level
tests (E302 — legal, not in the enforced `select`, and `ruff format --check` is
already unclean on `main` for unrelated comprehensions, so it reds no check), restored
here.

The conflict was **purely additive**: both sides appended a distinct test at the end of
`tests/test_doc_affiliation_frontmatter.py`, with no overlapping lines and no semantic overlap —
`#13485` fixed what the arm *says* about a wholly-commented value (diagnosis text, item vs key
nullity), this PR fixes *where the closing quote is found* (`_closing_quote`, parity). The bound was
one region, and it was resolved by keeping BOTH sides: no line of either was dropped, edited, or
reordered.

Everything else rebased without a hand-touch, including the `_closing_quote` helper and the truncated
`"…#11901" KEEP` case that must stay RED.

Measured at the post-rebase head:

- the new test still **discriminates**: replaying the true pre-fix `_split_comment`
  (`raw[:i].rstrip().endswith(q)`) into a scratch copy of the head reds it
  (`- single-quoted` / `+ broken`) — the failing assertion is
  `assert style == "single-quoted"` in
  `test_a_single_quoted_escape_before_a_hash_is_measured_not_broken` (`:981` at this
  head; it was `:974` when the mutant was run, and it moves whenever a test is
  appended to this file, so cite it by name, not by line);
- over-block controls unchanged (`trailing-comment` / `plain-quoted` / `escaped-body`), and (C)'s
  truncation case still `broken`;
- `main` at `f6793959a2` measures `2 failed, 8 passed` on this file; this head measures
  `2 failed, 12 passed` — the same 2 pre-existing absence reds, the same paths, plus `#13485`'s test
  and this PR's own three pins (`''` parity, `"` backslash, and the (C) ordering);
- `test_docs_lint_baseline` + `test_docs_lint_gate` + `test_docs_citation_refs` → **106 passed**.

One correction this rebase forced: the pre-existing absence red on the evidence bundles now names
TWO bundles, because `#13436` landed `docs/evidence/13436-write-path-costs/README.md` without
frontmatter after this plan was written. §7 and the out-of-scope list above record both; the defect
is `main`'s, not this PR's.
