---
title: "Frontmatter value survival — #13452 implementation plan"
type: engineering
domain: knowledge-graph
doc_status: draft
created: 2026-11-18
subjects.team: epistemic-team
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/13452#issuecomment-6967218550 -->
<!-- plan-review: cycles=3, status=capped, version=2.3.0 -->

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

On `main` **before `#13476` landed** — `035ed183fc`, which is `a5f1e7db46^` — measured through that
same consumer across `docs/research/**/*.md`:  (⛔ NOT `c0463806db`, the head an earlier revision of
this paragraph cited: `git merge-base --is-ancestor a5f1e7db46 c0463806db` exits 0, so that tree
already CARRIES the fix and measures **0** truncated values, not 8 — the same defect this section
exists to record, caught by review round 3.)

```text
103 files · 68 frontmatter blocks · 8 truncated values · all 8 are `aboutObjects`
  the truncated count matches the issue's 8 EXACTLY, derived independently;
  the block count is the issue's 67 plus one file that landed since (drift, not
  disagreement — see §7's before/after count)
```

The count is tied to that head on purpose: `#13476` is now ON `main`, so the same probe over
the same glob at this head reports **0** truncated values (`broken` count re-measured through
`_values` + `_quoted_style` at `ae275460df`: `files: 103 blocks: 68 broken(truncated): 0`).
Stating `8` as a present-tense `main` measurement would be the exact defect this section
exists to prevent — a number that no longer reproduces against the tree it names.

`test_research_doc_frontmatter_parses_as_yaml`'s presence check (`REQUIRED_KEYS` membership) cannot
see any of them — named rather than line-numbered, because this PR's own insertions above it moved
the line: `missing = [key for key in REQUIRED_KEYS if key not in parsed]` asks whether four keys are
**present**, and `REQUIRED_KEYS` does not even contain the two fields that carry the affiliation
to other entities. The number it used to carry was correct when written and rotted TWICE under it —
`:643` where the comprehension sat at `34df19504d`, `659` at `10a3d694`, `666` from `fcf5d9cd72` on
(measured per head). ⛔ No guard can catch that: `test_docs_citation_refs.py`'s `_PATH_REF` is
`(docs/[A-Za-z0-9_./-]+\.md):(\d+)`, so a `tests/….py:NNN` pointer is never resolved and never
reds (the widening is #11573, open). A name is therefore the one form that cannot go stale — the
rule §9's discrimination paragraph already states for this test file, applied here as well.

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

**Re-derivation note (PLAN-VERIFY round 1, P3; citation corrected in review):** the deferred
`config/ci-surfaces.yml` rationale is the `test_doc_affiliation_frontmatter.py: 0.1  # #8738 review
P2: six frontmatter-block reads + one directory glob; …` line in that file's timing-cost region —
`grep -n 'test_doc_affiliation_frontmatter.py: 0.1' config/ci-surfaces.yml` finds it. Measured at head
(`b0940c7a4ffc`) that is line **`:13167`**; it is cited by its KEY, not by a bare line number,
because `ci_timing.py --refresh-durations` rewrites the region and the number moves with every
refresh. An earlier correction of this note said `:13139`, which is itself off by 28 lines (that line
is `test_pack_shipping.py`), and the issue body's `:13061` is a different module's cost note
(`test_invites_email_http.py`) — the citation drifted at every hand, which is the reason for the key.

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
| **5 pins that actually OPEN the 8 files** — `test_entity_attachment_brief_claims.py`, `test_12184_shifted_cites.py`, `test_m3_fts_leg_recall_12298.py`, `test_candidate_recall_11901.py`, and `test_entry_point_python_guard.py`'s docs scan (`test_capacity_page.py`, `test_name_entropy_12294.py` and `tools/edge_census.py` only NAME them in prose and open nothing — corrected after round 2) | those tests | the **4 git-free** pins run on both trees with identical results; the 5th (`test_entry_point_python_guard.py`) cannot run against `main` content and is **NOT** claimed identical — see §7 item 5. Re-measured at `ae275460df`, the 4 give `4 failed, 186 passed` (190 collected), so the earlier `3 failed, 182 passed` / `7 failed, 784 passed` figures are superseded: the latter was a SINGLE-tree whole-batch count, never an "either side" comparison, and quoting it as one contradicted §7 item 5 |
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
| `tests/test_doc_affiliation_frontmatter.py` (`test_research_doc_frontmatter_parses_as_yaml`) | the arm itself | `config/ci-surfaces.yml`: the `core:` row at `:3728` + the `tier1:` row carrying the `#8738 review P2` marker at **`:10690`** (`:10678` before the `origin/main` merge) | dual-registered, so it runs for a docs-only diff AND for the full suite. ⛔ The two SURFACE labels here were inverted until review round 4: the line NUMBERS had been corrected while the labels carried forward from the pre-correction citations. Verified by enclosing key, not by eye — `:3728` sits inside the `core:` sub-block (which ends where the next `ep:` key begins) and `:10690` sits inside the top-level `tier1:` list (which ends where `slow_files:` begins), which is the direction the line's own comment states: *"a docs-only diff runs `tier1` only; dual-registered with `core`"*. ⛔ **`:10678` → `:10690` is the `origin/main` merge's own doing** (`#13486` inserted 12 lines above it in this file; `grep -n -- '- test_doc_affiliation_frontmatter.py' config/ci-surfaces.yml` re-derives it), which is why the row now also names the marker — a bare line number here had already been "corrected" twice and rot is the reason. ⛔ **The BOUNDS of that check were transcribed NUMBERS until review round 7** — 2632 ≤ 3728 < the `ep:` key, and 10656 ≤ 10690 < `slow_files:` — and the same `+12` insertion moved every key below it: `ep:` 8976 → **8988**, `tier1:` 10656 → **10668**, `slow_files:` 11248 → **11260**. So the row repaired the number it was about and left the three that certified it stale, in the one clause whose whole point is "not by eye". It now names the KEYS it compares instead of transcribing them; re-derive all four with `grep -n -E '^(  )?[a-z0-9_]+:' config/ci-surfaces.yml`. |
| `docs/research/**/*.md` (103 files, of which **68** carry a leading block — re-measured at this head) | data | the arm's `docs/research/**/*.md` glob | the population the assertion iterates |
| `scripts/check-doc-affiliation.cjs` | commit-time advisory | not CI-wired | **not touched**; deferred per §2 alternative E |

## 7. Verification plan — measured (PROVE evidence)

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run --frozen python -m pytest tests/test_doc_affiliation_frontmatter.py -q`

1. **Unfixed tree** (this test file against `origin/main` content): `3 failed, 6 passed` — this figure
   was measured by the earlier round on the tree it then held, and it is **recorded as a historical
   claim, not re-verified here**: the nearest reproducible measurements DISAGREE with it, and both are
   named so a reader can see the gap rather than inherit a number — `268737ae6cd9` (this branch's
   first commit) in its OWN tree measures `2 failed, 8 passed` (10 collected), and the pre-fix file
   against `c0463806db` (main's then-parent) measures `2 failed, 7 passed` (9 collected). What
   reproduces at every head is the SUBSTANCE below. The research
   arm reds on **exactly the 8** `aboutObjects` values, each carrying the **RAW-vs-parsed length pair**
   (`len 82 → 48`, `81 → 67`, `93 → 73`, `119 → 75`, `107 → 92`, `155 → 46`, `115 → 87`, `141 → 57`);
   the other 2 failures are the pre-existing absence reds. **(B) fires 8 times — counted from the
   failure output, not asserted.**
2. **Fixed tree:** `2 failed, 16 passed` at the head that adds the differential pin — `18 collected`,
   the 2 failures being the ones named below; the file carries exactly **8** tests this PR added
   (`comm` on the two files' `^def test_` lines against `origin/main`: 16 vs main's 8), and each count
   is tied to the head that produced it, because tests are appended to this file every round
   (`15 passed` at `38df4e93a4`, before the differential pin):
   `8 passed` on `main` (`f6793959a2`), `9 passed` at the rebased `36700a89b7` (the extra pass is
   this branch's OWN first pin, `test_a_single_quoted_escape_before_a_hash_is_measured_not_broken` —
   `comm` on the two `^def test_` lists against `origin/main` names exactly that one test, and
   `#13485`'s `test_a_wholly_commented_value_gets_ONE_TRUE_diagnosis` is already inside the `8`, so
   it cannot be the extra pass), `10 passed` once the `''`-parity pin
   landed, `11 passed` once the `"`-backslash pin landed, `12 passed` once the (C)-ordering pin
   landed, `13 passed` once the never-closing/`malformed` pin landed in `b0940c7a4f`,
   `14 passed` once the verdict-split remedy pin landed, `15 passed` once the
   unlexable-key fail-open pin landed in `38df4e93a4`, and `16 passed` once the differential
   refusal pin landed. The 2 are the
   pre-existing absence reds
   (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`, and the evidence bundles
   `docs/evidence/13038-capstone/README.md` **+** `docs/evidence/13436-write-path-costs/README.md` —
   the second bundle landed on `main` in `#13436` AFTER this plan was written, so the red now names
   two bundles), unchanged, named, and deliberately not "fixed" here. The baseline was measured on
   `origin/main` at `f6793959a2` too (`2 failed, 8 passed`) — same two tests, same paths.
   `git diff --stat` vs `main` = **2 files** (this plan doc + the test file) — the `9 files + 1 plan
   doc` this line used to carry was #13476's tree, measured at THAT head, and was false here.
   ⛔ **SUPERSEDED for the absence reds by the `origin/main` merge (`130e20fdaf`, two commits after
   `f6793959a2`).** That merge brought in `#13471` and `#13486`; the frontmatter that clears these
   reds is **`#13486`'s** — `git show --numstat 0186df593423 -- <those 3 docs>` = 11 + 11 + 11 = 33
   lines. `#13471` touched only `docs/evidence/13038-capstone/README.md`, and its 7 lines are a prose
   `⚠️` note in that file's BODY, no frontmatter (`git show --numstat 130e20fdaf -- <those 3 docs>` =
   7/0, one file). So the `40 insertions` the whole merge contributes are **33 + 7**, which is exactly
   why the total was right while a bare `#13471`/`#13486` attribution is not — corrected after review
   round 6 caught it naming `#13471` for frontmatter it never added. At the merge head the same
   command measures **`18 passed, 0 failed`** (measured, not inferred). Every
   `2 failed, N passed` figure in this doc stays true for the head it names; what no longer holds is
   the PRESENT TENSE — "the 2 ARE the pre-existing absence reds".
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
   **including its `---` fences** degrades to `{}` — and NOT because of a YAML error, since the
   consumer never reaches yaml: `_FM_RE.match("  ---\n  type: research…")` is `False`, so
   `parse_frontmatter` sees no block at all and returns `{}` before that import is called (measured;
   `yaml.safe_load` on the INNER, unindented block alone returns all 10 keys). The earlier
   "`mapping values are not allowed here`" attribution named a call the consumer does not make — the
   same "measured a different construction" error this item exists to record. That variant reds one
   arm earlier. An earlier revision of this
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
   trees: the 4 git-free ones re-measure **identical** results at this head — `4 failed, 186 passed`
   (190 collected). The 5th
   (`test_entry_point_python_guard`) shells out to `git ls-files`, so it cannot run against main content
   in a scratch tree and is **not** claimed identical here; on this branch its contribution is 4 named
   pre-existing cases. The figures this item used to carry (`3 failed, 182 passed` either side; a
   "whole sibling batch" of `7 failed, 784 passed, 2 skipped`) are SUPERSEDED: the second was a
   single-TREE count, never an "either side" comparison, and quoting it as one contradicted this item.

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
  escaped body (M10) and an indented comment line after a quoted value (M17) all stay **GREEN**. For
  the shapes this PR fixes — the `''` escape, the never-closing scalar, the (C) ordering — the
  message names a remedy that actually clears the red, and each is pinned by a test that proves it.
  ⚠️ That is NOT true of one shape the arm still over-blocks: an affiliation SEQUENCE followed by an
  indented comment line (`aboutObjects:` / `- a, b` / `# note`) reds as `MULTI-LINE` with *"quote the
  whole value instead"*, yet `parse_frontmatter` reads it INTACT (`['a, b']`) and quoting the items
  reds with the identical message — so the printed remedy cannot clear it. It is PRE-EXISTING
  (`main` produces the same text) and is recorded in §9 among the remedy residuals rather than
  claimed clean here.
- **R3** quoting **truncates** a value (unescaped inner `"`, unbalanced `'`, missing close) → caught by
  **(C)**, which runs **before** the escape decline — the reverse order let an escaped body with an
  unescaped quote through green (M16 now reds). Scoped honestly: truncation-at-a-quote, NOT a
  character typo (M6 green, stated).
- **R4** **scope creep** (the 2 absence reds or the consumer get "fixed") → identical before/after
  failure sets + `git diff --stat` vs `main` = **2 files** (this plan doc + the test file), with the
  plan doc MODIFIED rather than new — it already exists on `origin/main`. The `9 files + 1 plan doc`
  figure this line carried was #13476's tree, measured at THAT head, and is false here (§7 item 2
  records the same correction against the sibling claim).

**Review budget disclosure.** PLAN-VERIFY spent its **3** cycles (the standard cap); round 1 found an
`isinstance(str)` fail-open that skipped a 100 %-lost value, round 2 found R3 unreachable and a false
population claim, round 3 found (D) inert for block/flow plus three over-blocks — all verified by
measurement, fixed, and covered by the matrix. Because the cap is spent, the *diff* then went to the
**head-bound code review**, which found two more real defects — **(B) tautological** and **(C)
unreachable behind the escape branch** — both verified by measurement, fixed, and now covered by M1 and
M16 / M18. The attestation for #13476 is recorded at its FINAL head (`9f25dc79c9`) — a sha that is
**not an object in this clone and cannot be obtained**: no ref here reaches it
(`git rev-list --all | grep -c '^9f25dc79c9'` = **0**), `git cat-file -t 9f25dc79c9` and
`git show 9f25dc79c9` both fail (`Not a valid object name` / `unknown revision`), `git fetch origin
9f25dc79c95d` answers `couldn't find remote ref`, and `git merge-base --is-ancestor 9f25dc79c9
origin/main; echo $?` prints **128**, not `1`. A sha that is present only because some command
fetched it is not durable — git drops unreachable objects when it repacks, and this one is gone
(measured at `9f12429a52`; `git cat-file --batch-check` reports `missing`). #13476's merged state on
`main` is the squash **`a5f1e7db46`** (`git cat-file -t` → `commit`), which IS obtainable, so that
is the pointer to cite; `9f25dc79c9` is kept beside it only because the record is bound to that head.
⛔ Two rounds wrote this line differently and each was wrong in ONE direction: it said *"not an
object in this clone, so a reader here cannot open it"* (true, and it was "corrected" away), then
*"present in this clone, so `git show 9f25dc79c9` opens it"* until an independent re-measure — a
`cat-file` reading that reproduced when it was taken and not afterwards. The form that cannot rot is
the squash on `main`), and that record
is `not-clean` with two findings — the escape one fixed in #13484, and the counter residuals now
listed in §9. #13484's own attestation is minted at its own final head, after this section.

**Out of scope — must NOT be raised** (note, file, do not chase):

- the 2 pre-existing absence reds (`docs/architecture/STORAGE-PICTURE-2026-11-17.md`, and the
  evidence bundles `docs/evidence/13038-capstone/README.md` +
  `docs/evidence/13436-write-path-costs/README.md`, the latter landed by `#13436` after this plan) —
  separate defect, explicitly excluded by the issue. **Fixed upstream while this PR was in review**
  — the frontmatter is `#13486`'s, landed alongside `#13471` and integrated by the `origin/main`
  merge at `130e20fdaf` (§7 item 2 carries the `--numstat` split; `#13471`'s 7 lines there are a
  prose note, no frontmatter) — so the red is gone at the merge head; the exclusion still holds —
  this PR did not fix it, `main` did;
- `config/ci-surfaces.yml` timing-cost rationale for `test_doc_affiliation_frontmatter.py` (cited by
  its key: line `:13167` at head `b0940c7a4ffc`, moved by `ci_timing.py --refresh-durations`) —
  rationale drift (category B, no behavioural consequence);
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
escape* remedy). `test_a_single_quoted_escape_mid_body_under_a_trailing_comment_is_measured`
now pins the parity half: with the skip deleted it reds and the sibling stays green
(`-k single_quoted` → `1 failed, 1 passed`), so each half names its own falsifier.
⛔ An earlier version of this paragraph ended that sentence "…, with green CI" — FALSIFIED by
measurement, and corrected rather than dropped: with the skip deleted the whole file is
`5 failed, 13 passed` at `dad7aea959`, i.e. **three** pins red (this one,
`…_never_closing_quoted_scalar_…`, and the class pin `test_no_GREEN_quoted_verdict_survives_a_parser_refusal`)
plus the 2 pre-existing absence rows. So the parity skip was never un-pinned in the sense that
mattered: a simplification of `_closing_quote` cannot land green.

**Fixed at this head — the `"` backslash skip had no pin (round-4 review).**

`"a\"" # c` is legal YAML — `parse_frontmatter` returns `a"` — and the TRUE close sits
immediately before the space-then-hash that introduces the comment (for that raw the escaped quote
is at index 3, the true close at 4, and the `" #"` at indices 4-6). A close found WITHOUT skipping backslash
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
mutant reds this test **and** `test_a_never_closing_quoted_scalar_is_malformed_not_escaped`
(re-measured at `1c956076`: `4 failed, 12 passed` — the 2 pre-existing absence reds plus those 2),
because that later pin also asserts `_closing_quote('"a\\"') == -1`, which is exactly what the
backslash skip produces. So this test is **not** the sole custodian of the branch — an earlier
revision said "this test alone", and the fifth pin falsified it — and no file-wide count is
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
values the consumer TRUNCATES change what the arm reports — and only ONE of them escapes
measurement. `"a, "#1" KEEP"` (YAML reads `'a, '`) goes `('broken', None)` →
`('double-quoted', 'a, "#1" KEEP')`, so the scalar branch still compares it and still reds, with
*does not SURVIVE the parse* (`len 12 → 3`, the BODY's length — the raw is 14) where it used to say TERMINATED. `"a\tb\" "#1" KEEP"`
(YAML reads `'a\tb" '`) goes `('broken', None)` → `('escaped', None)`, and `escaped` declines the
comparison entirely: THAT is the value escaping measurement — the #13452 defect this guard exists to
prevent — behind green CI, and it is the only branch of this helper whose loss is a fail-open rather
than a mis-worded remedy; the two pins above are the other class. `test_an_unescaped_quote_is_broken_even_beside_a_legal_escape` now
pins both shapes (unit-level on `_quoted_style`, which is where the ordering decides). At the head
this was measured (`23cfffc260`) it red alone under that mutant — `3 failed, 12 passed`, the 2
pre-existing reds plus this one. It no longer does: at `6cea462fd4` the mutant reds this test,
`test_a_malformed_value_names_a_true_remedy` **and**
`test_no_GREEN_quoted_verdict_survives_a_parser_refusal` (`5 failed, 13 passed` of 18), because those
later pins assert a verdict the ordering decides. The exclusivity claim is dropped, not re-dated —
it is now one of three pins, which is the stronger fact.

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
finding, and neither is an escape: in `test_research_doc_frontmatter_parses_as_yaml` the
`assert measured` / `assert compared` pair runs BEFORE `assert not failures`, so a tree the guard
samples sparsely reds on *"measured no affiliation value at all — re-point this guard"* — advice
that points at the guard instead of at the value; and `_values`' SCALAR branch `continue`s on
`escaped` before it reaches `compared += 1`, so a tree whose affiliated values are all escaped
leaves the counter legitimately empty and fires the same message, as does the SEQUENCE branch's
same `assert measured` / `compared` pair. (Cited BY NAME, not by line — this document's own rule
below. Every appended test shifts those numbers, and a line citation here has gone stale in each
of the three revisions that carried one, which is the mechanism rather than bad luck.) Both are
false blocks of an author's tree, not fail-opens — nothing escapes measurement, they fail closed
and name a remedy — and both live in the guard, not in the escape branch this PR fixes. Recorded
here because a reader meets the message before the reason; fixing the ordering is a separate change
to #13476's guard.

**Recorded, not fixed — one legal shape is newly REFUSED (a narrow over-block this PR introduces).**
Found by the fresh-context review at `7d4514f39e` and re-measured here. `aboutObjects: "\" #"# c`
(raw `"\" #"# c`) is loadable YAML — `yaml.safe_load` returns `" #`, and `parse_frontmatter` agrees
— but the arm now reports `('malformed', None)` where the pre-fix helper reported
`('escaped', None)`. The cause is the fix's own predicate, working as designed on one half and
meeting a lenient consumer on the other: the space at index 3 and the `#` at index 4 both sit
INSIDE the scalar, so
`_split_comment` correctly refuses to split there (the text before it does not end at the parity
close, which is index 5), and with no space-separated `#` after that close nothing is stripped, so
the raw text reaches `_quoted_style` with a trailing `# c` and `value.endswith(q)` fails. Note the
arm is the SPEC-CORRECT side here (YAML 1.2.2 §6.6: *"Comments must be separated from other tokens
by white space characters"*, so `"…"# c` is not a comment), and PyYAML is the lenient one — but
PyYAML is the consumer this arm exists to model, so for that consumer it is a false block. It
cannot ship a defect — it fails CLOSED, the value is REPORTED rather than silently skipped — and it
is the same class as the (C) truncation case this PR deliberately keeps red. ⚠️ The VERDICT is still
refused, and clearing that is still out of this issue's scope for the reason below — but the REMEDY
it printed was itself the class this PR removes: the arm said *"the value is TERMINATED there —
remove the inner quote"* when `yaml.safe_load` reads the value whole, so a reader was told to delete
a legal inner quote. That half needed no lexer change and is now FIXED: `_unkeepable_quoted` splits
the diagnosis by VERDICT, so `malformed` names the shapes that actually clear it (one space before
the `#`; the whole value on one line inside its quotes; a block scalar) while only `broken` — where
YAML really does stop at an inner quote — keeps the deletion advice, and
`test_a_malformed_value_names_a_true_remedy` pins both halves. Clearing the VERDICT means
teaching the arm to accept a non-space-separated trailing comment after a close — a lexer widening,
out of this issue's scope, and unchanged by the remedy fix.
Measured scale, so it is not read as larger than it is. ⚠️ The differential below was run by the
round that found this, over a **generator it did not commit**, so the figure is a historical
measurement: an independent re-run at `4be2f9a959` over 81,243 YAML-parseable generated raws found
**0** cases where the one declining branch (`escaped`) hid a truncation and **2** pre-GREEN →
post-RED rows, both in this declared family — the same direction, on a different alphabet, so the
counts are not comparable and only the direction is corroborated: differential over **15,548 YAML-parseable
generated raws**, exactly **1** where the arm was green before and is red now; and **0** where the
new predicate splits at a space-preceded `#` the old one refused — i.e. no fail-open was introduced on the path
this PR touches.

**Recorded, not fixed — #13476's `470 values compared` figure.** At this head the same probe
prints **90 measured / 94 compared** at `4be2f9a959` (reproduced with an instrumented copy); the
counter now counts affiliation values only. The
substance — 8 of 8 flagged, every length pair exact — reproduces unchanged. That body is NOT
edited: it carries the review attestation the gate reads, and an unrelated edit wipes that proof
(the rule is in `AGENTS.md`). The corrected figure is recorded here instead.

**Recorded, not fixed — THIS PR's OWN body carries two stale present-tense figures.** Both were
measured at `ae275460df93` by the fresh-context review and re-measured at `1c956076` (the last head
this note covers; every later round moves both numbers again):

- *"contains exactly one commit"* — false at this head: `git log --oneline origin/main..HEAD | wc -l`
  = **27** at `1c956076`, **28** once that round's docstring commit was added, **30** at
  `38df4e93a4`, and **32** at `c32ff051b4` (each re-measured; every round adds at least one, which is
  the point). The sentence was
  true when the body was written (the PR was rebased onto `main` as a
  single commit); the review rounds then appended the rest. A merger reading "exactly one commit"
  against a 30-commit branch is being told something false about the diff's SHAPE, which is why this
  is recorded rather than left implicit in §7's moving-count rationale.
- *"`pytest …` → **8 passed, 2 failed**"* — stale for the same reason §7 item 2 gives: tests are
  appended to this file every round. At `ae275460df93` the same command reported **13 passed, 2
  failed** (`15 collected`), at `1c956076` **14 passed, 2 failed** (`16 collected`), and at
  `38df4e93a4` **15 passed, 2 failed** (`17 collected`; re-measured), and **16 passed, 2 failed**
  (`18 collected`) once the differential refusal pin landed, the
  2 being the pre-existing absence reds named above.

The body is **not** edited — it carries the review attestation the gate reads, and editing it wipes
that proof (`AGENTS.md`; the same rule that left #13476's `470` figure in place above).

**Recorded, not fixed — a comment with NO space after the close is a PRE-EXISTING false block.**
Found by the fresh-context review at `23cfffc260` and re-measured there. `aboutObjects: "x"# note`
is loadable YAML — `yaml.safe_load` returns `"x"`, and `parse_frontmatter` agrees — but the arm
reports `('malformed', None)`. The verdict is the PRE-EXISTING part (see the next paragraph); the
REMEDY is not: this round's `_unkeepable_quoted` prints *"put one space before the `#`"* for it, so
the *"remove the inner quote"* advice the review recorded here is gone, pinned by
`test_a_malformed_value_names_a_true_remedy`. `'x'# note` behaves the same.
This is NOT a regression and NOT a new refusal: `main`'s helper (`f6793959a2`) reaches `malformed`
here too, via `not value.endswith(q)` — `_split_comment` only ever splits at a space-preceded `#`,
so the raw keeps its trailing comment and never ends in the quote char. The fix neither causes it
nor clears it, and it is recorded rather than chased for the reason the sibling entry above gives:
clearing it means teaching the arm to accept a non-space-separated trailing comment after a close,
which is the same lexer widening and the same out-of-scope boundary. It fails CLOSED — the value is
reported, never silently skipped — so it cannot ship a defect. It is worth naming because a reader
meets it the first time anyone writes `"…"#comment`, and because it is one member of a family
whose VERDICT is the same while the gate is shut — the family's printed remedy, which this round's
verdict split corrects, was the other half of the complaint.

**The differential pin, and the one shape it deliberately stops short of.** Every quoting defect this
branch fixed was found ONE SHAPE AT A TIME, and each round the NEXT shape was what red. So this round
adds a CLASS pin: `test_no_GREEN_quoted_verdict_survives_a_parser_refusal` sweeps every string over
the alphabet *(single quote, double quote, backslash, `#`, space, `a`)* up to five characters —
3,110 quoted values — with `yaml.safe_load`
as the oracle, and asserts the two directions in which a bad value gets THROUGH: a shape the parser
REFUSES must not come back with a COMPARING verdict, and a comparing verdict's body must equal what
the parser read. Measured: 277 shapes reach a comparing verdict, **0** refusals reported green,
**0** body mismatches. Non-vacuity: the same sweep driven through `main`'s `_split_comment` +
`_quoted_style` finds **1** — `'''''` (five quotes), which YAML refuses as unterminated and `main`
reports as `single-quoted` with body `''` — and mutating this head's wholeness test back to main's
`not value.endswith(q)` reds the new pin (`4 failed, 14 passed`, restored after the run). ⛔ The
pairing is named exactly: `_closing_quote` does NOT exist on `main` (it is introduced by this
branch), so the replay is of main's SCALAR VERDICT path — `_split_comment` + `_quoted_style` —
which is where the compared verdict is decided.

It stops at length 5 because at 6 the sweep finds FIVE PRE-EXISTING discrepancies this change
neither causes nor clears — and they are NOT all one kind, so naming only the harmless one would
mis-describe the bound. All five reproduce IDENTICALLY through `origin/main`'s `_split_comment` +
`_quoted_style` (measured shape by shape) — that pairing, not `_closing_quote`, which does not
exist on `main` — so the exclusion is about ownership, not severity.

- FOUR are the pin's FIRST and most severe direction — **refused-but-green**, a comparing verdict
  on input a real parser REJECTS — all of the `''''X'` form: `''''"'`, `''''\'`, `'''' '`,
  `''''a'`. None is an end-to-end fail-open: `parse_frontmatter` returns `{}` for every one, so the
  PARSE arm refuses the file before a comparing verdict is consumed (measured at `f43f4971`).
- ONE is a mis-slice of the harmless direction — `''''#'` is loadable YAML (`'`) and the arm's body
  is `''#`, because `body = value[1:-1]` is never sliced at the close the parity scan already
  located. False red, so it fails CLOSED. Recorded, not fixed — and the reason recorded here is
  CORRECTED, because the one an earlier revision gave was false: the one-line fix
  (`body = value[1:close]`) is not blocked by `'a'x'` going GREEN, since
  `parse_frontmatter("aboutObjects: 'a'x'")` returns `{}` (measured: the same PARSE-arm refusal this
  document relies on above for the four `''''X'` rows), so no comparing verdict is consumed
  end-to-end. For THAT shape (`'a'x'`) alone, what the fix would leave is a HELPER-LEVEL comparing
  verdict on parser-refused input — the latent class those four rows already carry — not a fail-open.
  ⛔ That does NOT generalise, and an earlier revision of this bullet stated it as though it did: on
  the canonical truncation shape `"a, "#1" KEEP"`, which YAML ACCEPTS (reading `'a, '`), the slice
  moves `('broken', None)` → `('double-quoted', 'a, ')` — a COMPARING verdict whose body equals what
  PyYAML reads — so the PARSE arm never refuses it and the arm goes GREEN end-to-end (measured at
  `6cea462fd4`: driving `test_research_doc_frontmatter_parses_as_yaml` over a tmp tree holding that
  value, HEAD reds and the slice PASSES). On that shape the slice IS an end-to-end fail-open.
- ⛔ It is NOT free either, and the review's own measurement covers only two of the three directions.
  Applied to this head, the slice turns the class pin's FIRST direction from **0 to 122**
  refused-but-green shapes at bound 5 (comparing verdicts `277 → 422`) and reds
  `test_no_GREEN_quoted_verdict_survives_a_parser_refusal`,
  `test_a_malformed_value_names_a_true_remedy` **and**
  `test_an_unescaped_quote_is_broken_even_beside_a_legal_escape` — the last because the YAML-accepted
  `"a, "#1" KEEP"` comes back `('double-quoted', 'a, ')` and PASSES the arm end-to-end; at bound 6
  it is `1430 → 2527`. The mechanism is the
  same one that makes the mis-slice harmless: `'' '` closes at index 1, so `value[1:close]` is the
  empty string and the text AFTER the close is silently dropped instead of refused
  (`('broken', None)` here). So the fix does clear `''''#'` (`''#` → `'`, the correct body) and adds
  0 wrong bodies on YAML-ACCEPTED input — both of which the review measured — but it must land WITH
  the wholeness test that refuses a close the text continues past. "Not blocked" is the wrong
  reading: as a one-liner it reds this PR's own pin, which is why it did not ride along in a prose
  round.
- ⚠️ The slice fix and the LEXER widening above are INDEPENDENT: the slice fix does not need the
  widening, and the widening's own out-of-scope note is unchanged by the correction above. What the
  slice needs is the WHOLENESS change, which is a third thing.

The length bound in the pin names the whole residual rather than leaving it to be rediscovered.

**Recorded, not fixed (the VERDICT) — a MULTI-LINE QUOTED scalar is refused; the remedy it prints is no longer mis-worded.**
Found by the second fresh-context review at `ae275460df` and re-measured there. A quoted scalar may
legally continue onto the next line:

```text
aboutObjects: "a
  b"
```

`parse_frontmatter` reads that whole as `'a b'`, but `_values` hands `_quoted_style` only the first
line (`_values(['title: x','aboutObjects: "a','  b"'])` → `('aboutObjects','scalar','"a')`), which
returns `('malformed', None)` — so the arm fails the file, and since this round prints the
one-line/block-scalar remedy rather than *"the value is TERMINATED there — remove the inner quote"*
about a value YAML keeps intact (`test_a_malformed_value_names_a_true_remedy`). This is
PRE-EXISTING: `main`'s
helper reaches `malformed` here identically, and the fix neither causes nor clears it. It also
falsifies the code comments that say a quoted value is closed on its line
(`tests/test_doc_affiliation_frontmatter.py:169` and `:210`) — those are corrected to the narrower
true statement (an indented **comment** line after a closed quoted value is not a continuation).
Not fixed here for the same scoping reason as its siblings, and it is the one member of this family
whose honest remedy is already modelled by the arm: `_values` has a `scalar_multiline` verdict for
PLAIN folding, and clearing this means teaching it to emit that for a quoted value with no close on
its line — a lexer change, out of this issue's scope. It fails CLOSED, so it cannot ship a defect.

**Fixed at this head — the two docstrings that contradicted their own measurements.** Found by a
fresh-context review at `ae275460df93` and still live at `1c956076`; both are PURE prose (no helper
behaviour changes), so the tests they describe are untouched. `test_a_never_closing_quoted_scalar`
claimed *"Nothing else in this file reds"*, falsified by the whole pre-fix-helper replay reddening
TWO of this PR's pins; `test_a_double_quoted_backslash_escape…` claimed deleting the backslash skip
*"leaves the rest of this file GREEN"*, and twelve lines above it the same docstring measures the
sibling reddening alongside. Each now names the mutant and the current figure, re-measured one
mutant at a time with the tree restored between runs:
`backslash skip deleted` → `4 failed, 12 passed` at `1c956076` / `4 failed, 13 passed` at
`38df4e93a4` (re-measured; the pass count moved because a 17th test was appended — the same two
pins are the two extra failures either way); `whole pre-fix helper replayed` → `4 failed, 12
passed`, measured at `1c956076` and not re-run at the later head. The counts moved `11 → 12 → 13`
because the verdict-split and unlexable-key rounds appended a 14th and a 17th test; a stale count in
the very paragraph that tells the reader not to trust stale counts is the failure mode §7 item 2
exists for.

**Recorded, not fixed — an affiliation SEQUENCE followed by an indented COMMENT line is a PRE-EXISTING false block whose printed remedy cannot clear it.** Found by the fourth fresh-context review and re-measured here. `aboutObjects:` / `- a, b` / `# note` (the comment on its own indented line, after the items) reds as `MULTI-LINE` with *"…a continuation line opening with `#` is silently swallowed; quote the whole value instead"*, yet `parse_frontmatter` reads the block **INTACT** (`['a, b']`) — and quoting the items (`- "a, b"`) reds with the IDENTICAL message, so the remedy the arm prints is one that cannot clear the red. `_values`' own docstring defines `sequence_multiline` as *"a sequence whose item is continued on a following indented line"*; a trailing comment is not a continuation, so the verdict is mis-classified as well as mis-worded. PRE-EXISTING — `main`'s arm produces the same text for the same file — so this PR neither causes nor clears it, and it is recorded here rather than fixed: clearing it means teaching the sequence scanner to skip a comment-only trailing line instead of marking the item `continued`, which is the same lexer-widening boundary as its siblings. Fails CLOSED, so it cannot ship a defect; §8 R2 used to claim this class was absent and now names it.

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

- the new test still **discriminates**: replaying the pre-fix close-POSITION test
  (`raw[:i].rstrip().endswith(q)`) into a scratch copy of the head while leaving the
  parity-based wholeness check in place reds it (`- single-quoted` / `+ malformed`):
  the truncated `'the ''KEEP''` now fails `_closing_quote`, so it lands on the
  no-close branch instead of on `broken`. `+ broken` is what a WHOLE-helper replay
  produces (old split **and** old `endswith`-only wholeness), which is the replay the
  test's own docstring describes — measured both ways at this head, so the two
  verdicts are not interchangeable. The failing assertion is
  `assert style == "single-quoted"` in
  `test_a_single_quoted_escape_before_a_hash_is_measured_not_broken` — cite it by
  name, not by line: the line number moves whenever a test is appended to this
  file, which is why the one an earlier revision carried is not repeated here;
- over-block controls unchanged (`trailing-comment` / `plain-quoted` / `escaped-body`), and (C)'s
  truncation case still `broken`;
- `main` at `f6793959a2` measures `2 failed, 8 passed` on this file; this head (`38df4e93a4`)
  measures `2 failed, 15 passed` — the same 2 pre-existing absence reds, the same paths, plus
  `#13485`'s test and this PR's own **seven** pins (`''` parity, mid-body parity, `"` backslash, the
  (C) ordering, the never-closing `malformed` pin, the verdict-split remedy pin, and the
  unlexable-key fail-open pin);
- `test_docs_lint_baseline` + `test_docs_lint_gate` + `test_docs_citation_refs` → **106 passed**.

One correction this rebase forced: the pre-existing absence red on the evidence bundles now names
TWO bundles, because `#13436` landed `docs/evidence/13436-write-path-costs/README.md` without
frontmatter after this plan was written. §7 and the out-of-scope list above record both; the defect
is `main`'s, not this PR's.

**The `origin/main` merge at `130e20fdaf` then removed that red entirely** — the frontmatter is
**`#13486`'s** (33 lines across the three paths); `#13471`'s 7 lines there are a prose note in one
file's body, no frontmatter (`git show --numstat` on each commit, named in §7 item 2) — so the file
measures **`18 passed`** at the merge head and §7 item 2 carries the measurement. Recorded here
because this section's last word on the subject was "the defect is `main`'s" — true when written,
resolved upstream since, and a reader at the merge head must not go hunting for a red that is gone.

⛔ **One correction to that same merge commit's MESSAGE (`9f12429a528`), which cannot be amended
because it is already pushed.** Its PENULTIMATE paragraph says *"`config/ci-surfaces.yml` is
unchanged …
already dual-registered at `:3728` (`core`) and `:10678` (`tier1`)"*, and both halves are wrong at the
head it certifies: the merge itself brought `#13486`'s **12 lines** into that file
(`git diff --stat 65523b28f3a0..9f12429a528 -- config/ci-surfaces.yml`), and that insertion moved the
`tier1` row **`10678` → `10690`** (so `:10678` is now `test_12295_fold_blocking_measurement.py`). What
the message MEANT — this lane added no registration — is true and is what the row records; the fix is
to stop citing the row by a bare line number, which §6 now does
(`grep -n -- '- test_doc_affiliation_frontmatter.py' config/ci-surfaces.yml` re-derives it). Recorded
here rather than silently left, because a commit message is the one artifact on this branch no later
commit can mend.
