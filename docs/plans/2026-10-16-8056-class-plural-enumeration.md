---
title: "Class-plural temporal enumeration (#8056) — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-16
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#8056, #2165, #2653, tests/_assembly_census.json"
---

<!-- research-path: docs/plans/2026-09-08-2165-connected-assembly.md + tests/_assembly_census.json + tortoise/assembly.py -->

# Class-plural temporal enumeration (#8056) — implementation plan

> **For Pi:** scoped + planned inline (the lane runs without a `task` tool; the
> parent owns the review gate). Pipeline level: **project** (complexity:standard,
> `domain:data`, team:epistemic-team). Align is INHERITED from #2165's epic
> decision — see "Align inheritance" below.

**Goal:** give the connected-assembly lane a DETERMINISTIC class-plural
enumeration shape: a question that asks for the **order of a CLASS of events
over a window** ("What is the order of the concerts and musical events I
attended in the past two months, starting from the earliest?") names NO
subjects, so every existing shape — all of which need resolvable Object
anchors — returns `None` and the question falls to the legacy lane. This adds
the missing selector: resolve the **window** from `question_date`, walk it
through the EXISTING window leg (`port.window_rows`), **select the class** by a
deterministic lexical filter, and **render the class chronologically**.

---

## 1. Measured problem (re-measured 2026-10-16, not trusted from the body)

The body's Open Question ("whether this shape needs #2652's LLM anchor+offset
extraction or can be resolved from `question_date` alone — resolve it before
planning") is answered by re-measurement: **`question_date` alone suffices.**
`in the past two months` is not the `ago` point-offset #2652 owns (that shape
resolves *`two months ago`* → an anchor ± a unit half-span); it is a bounded
RANGE `[question_date − 2 months, question_date]`, which is the same
deterministic window arithmetic `resolve_relative_anchor` already performs for
its offset-less forms (`window_lo = qd − N`, `window_hi = qd`). No model, no
anchor+offset pass, no dependency on #2652.

Re-measured over the committed 133-Q census (`tests/_assembly_census.json`),
`classify_question` returns `None` for the whole class-plural family at HEAD
(the body's claim HELD). The carve-out's "one row" is not the whole class — the
census filed the family across THREE classes, and **3 of its 6 rows carry a
bounded relative window**:

| qid | question | census class | window | in this lane? |
|---|---|---|---|---|
| `gpt4_d6585ce8` | What is the order of **the concerts and musical events** I attended **in the past two months**, starting from the earliest? | nary-ordering | `in the past two months` | ✅ (the carve-out's row) |
| `gpt4_e061b84f` | What is the order of **the three sports events** I participated in **during the past month**, from earliest to latest? | recency/current-state | `during the past month` (no count ⇒ 1) | ✅ |
| `gpt4_7f6b06db` | What is the order of **the three trips** I took **in the past three months**, from earliest to latest? | recency/current-state | `in the past three months` | ✅ |
| `gpt4_45189cb4` | What is the order of the sports events I watched **in January**? | other | CALENDAR month, no relative offset | ❌ different window family (see Boundaries) |
| `gpt4_7abb270c` | What is the order of the six museums I visited from earliest to latest? | recency/current-state | **none** — no window at all | ❌ |
| `gpt4_f420262c` | What is the order of airlines I flew with from earliest to latest **before today**? | recency/current-state | open-left (`before today`) — no bounded lookback | ❌ |

**Honest coverage target: 3/6 class-plural census rows fire** (0/6 at HEAD);
the other 3 stay non-firing with their reasons recorded (never absorbed).

Precision measurement beyond the census: the template was run over **every
question-shaped string literal in `tests/` + `tortoise/`** (ast string
constants, `8 ≤ len < 2500`, carrying both `?` and a space, unioned with the
133 committed census questions ⇒ **1,542** strings, re-measured after the
implementation landed; the count moves with the tree, so the RULE — not the
digit — is the reproducible part). **No question that fires a shape
at HEAD matches the new template** (0 conflicts of that kind), and the only
template matches that do NOT fire ``CLASS_PLURAL`` are the shape's own
deliberate R13 rejections — pinned as negatives (§5.4). The census run adds
the third firing row (`gpt4_e061b84f`).

### Align inheritance

The #2165 plan filed #2652–#2655 as recorded follow-ups with an explicit trigger
(census) and the constraint "the connected branch fires only on shapes with
MEASURED deterministic support". #8056 is filed from #2653's re-measurement as
the *carve-out* of a census row that synthesis re-read as a different feature;
it supplies that measurement + the deterministic mechanism for one more shape.
No recorded decision is contradicted.

**OVERRIDES:** the #2165 R13 **recency guard** (`_RE_RECENCY`: "bare `latest`
/ `the last one` is an ORDERING INSTRUCTION, not a time — reject") — overridden
*only* for the class-plural head, because there the ordering instruction is the
question's own request and the window comes from a separate, resolved clause;
the guard is otherwise untouched, so "Which did I buy first, my most recent
phone or my laptop?" / "List the events from most recent to oldest." / "What is
the order of the six museums I visited from earliest to latest?" (no window)
all still return `None`. Without the override the feature would serve one
arbitrary spelling of the same request and reject the other two measured rows.
Every other relative-offset / frequency / advice reject is preserved for the
shape (see §3).

### Boundaries (out of scope, recorded)

- Calendar-MONTH windows (`in January`) — a different window family: month
  resolution + as-of YEAR selection belongs to the calendar-anchor surface
  (#2655). `gpt4_45189cb4` stays non-firing.
- Open-left windows (`before today`) — no bounded lookback, so no window to
  enumerate. `gpt4_f420262c` stays non-firing.
- Window-less class-plural ordering (`from earliest to latest` with no window)
  — `gpt4_7abb270c` stays non-firing; this shape is *temporal* by definition.
- `or`-joined class lists (`concerts or musicals or events`) — the R13
  or-chain reject is preserved for the shape (a 2+ `or` chain is the N-ary
  shape's surface, not this one).
- No reader fragments, no qtype change, no MCP/SDK surface change.

## 2. Alternatives considered

| # | Approach | Outcome | Verdict |
|---|---|---|---|
| A | **Window leg + deterministic lexical class filter** inside the assembly lane (`resolve_class_window` → reuse `port.window_rows` → filter rows by the question's own class tokens → chronological render) | Zero-LLM, reuses the #2652 window walk + the R2 date ladder (ONE date source), selects the class explicitly as the issue asks, and fails CLOSED (no matching row ⇒ `fired=False`) so it can only help | **CHOSEN** |
| B | **Delegate the class selection to the reader** (fire the whole window, let the LLM filter by class) | Cheaper, but it does not "select the class" (the issue's own words), and it hands the reader a pool in which the class is a minority — on a busy window the class rows can be crowded out of the caps entirely | rejected |
| C | **Push the class filter into Cypher** (`toLower(p.content) CONTAINS $tok`) and filter BEFORE the fetch cap | Better cap behaviour, but it changes the `window_rows` port contract (Protocol + docker adapter + the dict-stub fakes in the pure lane) for a bound (`PER_ENTITY_FANOUT_CAP = 200`) no measurement hits; recorded as a residual instead | rejected (recorded residual) |
| D | **Entity-resolution of the class phrase** (feed "concerts and musical events" to the Task-3 resolver) | The resolver probes Object NAMES / aliases; a class is not an Object. A partial hit ("concerts" → some Object) would fabricate a subject and gate the shape on an entity that does not exist | rejected |
| E | LLM class extraction / LLM window parsing | Non-deterministic, contradicts the tier's constraint 6 ("NO LLM in this tier") and the census target ("deterministic") | rejected |

**Why A is the better outcome, not merely the easier one:** it introduces no
second date implementation (the walk and the R2 ladder are the #2652 ones), it
is as-of safe by construction (`window_hi = question_date`, never a future row),
it never silently degrades a question another shape owns (0 conflicts measured
over 1,542 question strings), and its failure mode is the safe one — a class no
row matches renders NOTHING and the legacy lane answers exactly as it does
today.

## 3. Implementation plan

1. **`tortoise/assembly.py` — classifier.**
   - `AssemblyShape.CLASS_PLURAL = "class-plural"`.
   - `_RE_CLASS_PLURAL`: `^(what|which) (is|was) the order of <CLASS> (I|we|…)
     <verb> (in|during|over|within) the (past|last) [<count>] <unit> <order>?`.
     Deliberately narrow: the ordering HEAD + the first-person RELATIVE CLAUSE +
     the explicit relative WINDOW. The count is optional (`the past month` ⇒
     1) and a plural unit without a count is NOT admitted (`the past months` is
     ambiguous — unmeasured). The `<CLASS>` span is TEMPERED at the first
     relative-clause boundary, so a repeated clause (`the concerts I attended
     and the plays I saw …`) ABSTAINS instead of absorbing the first clause's
     verb into the class vocabulary — a verb token (`attended`) would match
     almost every narrative row, a false include. The trailing ordering
     INSTRUCTION is captured and its direction read (`newest_first`), so a
     reverse-order request is rendered in the direction it asked for.
   - `classify_question`: the class-plural branch is evaluated AFTER the
     calendar-anchor branch and BEFORE the blanket R13 guard block (the
     placement IS the recency override — see the OVERRIDES note), with the rest
     of the R13 discipline re-applied explicitly inside the branch: a
     co-present `_RE_AGO_OTHER` surface, a promoted/bare relative offset, a
     frequency/count head, an advice frame, or a 2+ `or` chain still returns
     `None` — and so does any other co-present calendar day, read through the
     SHARED anchor finder rather than `_RE_AGO_OTHER`'s partial vocabulary
     (F12: a named holiday used to be claimed by the shape, dropping the day
     `main` resolved).
2. **`tortoise/assembly.py` — the pure resolver.**
   - `ClassWindow` (frozen): `class_phrase`, `tokens`, `unit`, `count`,
     `window_lo`, `window_hi`, `newest_first` (read from the question's own
     ordering instruction — `starting from the earliest` / `from earliest to
     latest` is chronological, `from latest to earliest` / `newest first` is
     newest-first; #8779 P1).
   - `resolve_class_window(question, question_date)` — deterministic, never
     raises (the `resolve_relative_anchor` contract): gated on
     `classify_question(question) is AssemblyShape.CLASS_PLURAL` (the two entry
     points can never disagree about which questions the shape owns), window
     arithmetic through the SAME `_sub_months` / `_sub_years` / `_timedelta`
     helpers, `None` for an absent/garbage `question_date` or an unparseable
     count.
   - `_class_tokens(span)` — the deterministic class vocabulary: strip the
     determiner + a leading count, tokenize, drop stopwords/number words, stem
     (plural `s`/`es`/`ies`, adjectival `al` so `musical` ≡ `music`), and drop
     the generic head nouns (`event`/`thing`/`activity`/… ) whenever a
     discriminative token exists — so `the three sports events` filters on
     `sport`, not on the near-universal `event`.
   - `_class_owns_row(row, tokens)` — the filter predicate: the row's own text
     (`content` + `search_keys`) tokenized/steamed by the SAME function carries
     at least one class token.
3. **`tortoise/assembly.py` — the walk.** `collect_class_slices(port, window,
   *, per_window_cap)`: the #2652 window walk (ONE batched `window_rows` fetch,
   the R2 ladder, the as-of window filter, undated rows excluded) narrowed by
   `_class_owns_row`; `admission` keeps the window census and adds
   `class_filtered_out`. `collect_window_slices`'s annotation is widened to a
   structural `Window` (its body is unchanged — `ClassWindow` and
   `RelativeAnchor` both satisfy it).
4. **`tortoise/assembly.py` — the render.** `_synthesize_class_hits(slices,
   window, question_date)`: ONE `CLASS ORDER (<phrase>): <n> dated events, …
   earliest first | newest first` header (the direction is the question's own
   request), then per row a position line (`<pos>. <date>`, where
   same-DAY rows SHARE a position — a tie is never rendered as a strict order,
   the module's existing tie discipline) followed by the row itself; the
   pure-walker derivation keys (`date`/`tier`) are stripped. Empty ⇒ `[]`.
5. **`tortoise/assembly.py` — the fired path.** `_assemble_class_plural(...)`
   mirrors `_assemble_relative_date`: resolver → class walk → render →
   `_decorate_real_rows` → the SHARED `_assemble_under_caps` tail; an
   unresolvable window, no question_date, or a class no window row matches all
   return `_empty_block()` (the #2165 P1-1 rule: never replace a working legacy
   pool with an empty block). `_assemble_connected` branches to it before
   `extract_subject_terms`; `extract_subject_terms` returns `[]` for the shape
   (the class is not an Object — the RELATIVE_DATE / AGO_RELATIVE precedent).
6. **`tortoise/assembly.py` — docstring.** The module header moves class-plural
   OUT of the never-fires list into the measured-shape list with the 3/6 figure
   and the three recorded non-firing rows.
7. **Tests** — `tests/test_assembly_pure.py` only (no new file ⇒ no
   `ci-surfaces.yml` change): the census coverage pin (3 fire / 3 recorded
   non-fires), `resolve_class_window` arithmetic (count absent ⇒ 1, digit and
   word counts, calendar month clamp, missing/garbage `question_date`, never
   raises, the classify gate), `_class_tokens` / `_class_owns_row`, the
   collector on the existing `_DictWindowPort` (class filter, undated
   exclusion, cap census), the render, and the hermetic
   `_assemble_connected` fired path (using the file's existing `_FakeAskSDK`
   harness) plus its empty-class fall-through. The census pins that quantified
   the class as "a different feature, correctly never fired" are RE-PINNED with
   the divergence documented, and the negative-set floor gains
   `_CLASS_PLURAL_COVERED` exactly as #2655's adjudication does.

## 4. Integration surface map / wiring check

| Surface | Test layer | Bug-pattern flag |
|---|---|---|
| `tortoise/assembly.py` classifier | pure unit + the committed 133-row census + the 1,542-string sweep | precision floor: no NEW false fire over the negatives (asserted) |
| `resolve_class_window` (pure) | pure unit | window arithmetic (month clamp, count absent, as-of), never-raises on garbage |
| `collect_class_slices` | dict-stub port unit + hermetic `_assemble_connected` | undated-row leak; cap accounting; class over/under-match |
| `_synthesize_class_hits` | pure unit | empty-class render; same-day tie; derivation-key leak |
| `_assemble_connected` dispatch | hermetic (no DB, no model) | fired path fires; empty class falls through (`fired=False`) |
| `collect_window_slices` annotation | unchanged body; #2652 tests must stay green | the AGO_RELATIVE path is behaviour-identical |
| `tests/_assembly_census.json` | committed artifact (read-only) | the census is the trigger record — never regenerated here |
| MCP tool registry / `TortoiseSDK` public surface | **not touched** | new shape is internal; no owner approval needed |

## 5. Acceptance criteria

1. `classify_question` returns `AssemblyShape.CLASS_PLURAL` for the 3
   window-bearing class-plural census rows, and `None` for the other 3 (pinned,
   with the reason per row).
2. **3/6** class-plural census rows fire (measured, pinned) — 0/6 at HEAD.
3. Zero new false fires: the negative-set floor's SET assertion still holds
   after the two `_CLASS_PLURAL_COVERED` rows are adjudicated out of the
   denominator (32 → 30 rows, denominator 29 → 27).
4. Every pinned never-fire example at HEAD still returns `None`, except the two
   class-plural rows this issue exists to reverse (deliberate, documented, and
   carrying the OVERRIDES note).
5. `resolve_class_window` never raises; `None` without `question_date`; the
   window never extends past `question_date`; `the past month` ⇒ a 1-month
   lookback; the calendar month subtraction clamps the day (03-31 − 1 month =
   02-28).
6. A window whose rows carry no class token ⇒ `fired=False` (legacy falls
   through byte-identically); the class-filtered block never renders a bare
   header.
7. The `flag OFF` legacy lane and every existing shape are untouched
   (`test_assembly_pure.py`'s existing 211 tests unchanged-green).
8. `ruff` clean on every touched file; `python tools/ci_selection.py
   --integrity` clean.

## Status — implementation (2026-10-16)

Implemented as planned. Measured results pinned in tests:

- census class-plural family: **3/6 fire** (was 0/6); the 3 non-fires stay
  non-firing with their window family recorded.
- precision sweep: **1,542** question strings scanned (tests + `tortoise/` +
  the committed census; the derivation rule is at §«Status», the count moves
  with the tree) ⇒ **no question that fires a shape at HEAD matches the new template**; the
  only template matches that are not `CLASS_PLURAL` are the shape's own
  deliberate R13 rejections (pinned negatives). The census run adds the third
  measured row.
- negative-set floor: `_CLASS_PLURAL_COVERED` removes the 2 adjudicated rows
  (32 → 30 in the boxed set, denominator 29 → 27); the fire SET assertion is
  unchanged and still green.
- `tests/test_assembly_pure.py`: **316 passed, 18 skipped** (334 collected) at
  the head of this branch; the head before the F12 round (`e7cd26792`)
  measured 312 passed, 18 skipped (330 collected). The pre-review head
  (`2e3106b4b`, as recorded there) measured 282 passed, 18 skipped;
  `origin/main` collects 229 (re-measured here). The tests added by the review
  fixes are the binary-ordering hijack, the direction read before the clause,
  the bare-`last` direction, the `last <unit>` window arm, the duration guard
  and the F12 pins; the +105 collected over `main` is the PR's total.
- `tests/test_assembly_sdk.py` (the ask-lane/SDK integration lane, embedded):
  **33 passed** — the #2652 window path and the flag-off legacy goldens are
  unaffected.
- `tests/test_evidence_assembly.py` + `tests/test_assembly_fixtures.py`: 33
  passed. `tests/test_2350_validity_precision.py` +
  `tests/longmem_eval/test_assembly_arm.py`: 41 passed.
- `ruff` clean on both touched files; `python tools/ci_selection.py
  --integrity` clean (no new test file, so no `ci-surfaces.yml` change).

### Addendum — one defect found and fixed during implementation

The first draft of the template used `(?P<cls>.+?)\s+`, which is the exact
ambiguity the module had already removed from `_RE_OR_CHAIN` (both `\s+` and
the lazy `.+?` match whitespace): measured **53 ms at len 1853** and **26 s at
len 25853** for `'the order of the ' + 'a'*100 + ' '*n + 'i'*100 + ' '*n +
'took …'` — quadratic. Inside `MAX_ASK_QUESTION_CHARS` (2000) the cost is tens
of milliseconds, so this is not a live hang, but `run_ask_lane` has no timeout
and the module's standing rule is that a super-linear classifier head is an
availability defect (#8097). Fixed with the same remedy: a SINGLE `\s` (the
lazy `.+?` covers extra separators) and a greedy tail, measured linear at
20k/50k chars. `test_class_plural_template_is_not_super_linear_on_space_runs`
pins it with a deliberately out-of-bound probe (20k), because no in-bound
probe can separate linear from quadratic.

## 6. Residuals (recorded, not hidden)

- **`main`-routed constructs that now land on `None` (safe direction).** The
  class-plural branch is entered before the rest of `classify_question`, so
  `"… in the past two months, most recently?"` (main: `AGO_RELATIVE`) and
  `"… in the past two months last Saturday?"` (main: `RELATIVE_DATE`) are
  rejected by the branch's own guards. **Measured, not assumed — a third form
  was claimed unaffected and is not (#8779 F17):** a NAMED-HOLIDAY
  class-plural question (`"… in the past two months on Christmas?"`; also
  `on Halloween`, `on Valentine's day`) is exempted from the day-lookup
  surface so this branch can decide it, and the branch then ABSTAINS on
  `_named_fixed_date_anchor` — so the question reaches NEITHER shape and
  `classify_question` returns `None` where `main` returned
  `relative-date-lookup`. Neither fires `CLASS_PLURAL`, and legacy answers —
  the safe direction — but this is a routing change, not "still rejected".
  Handing the question back to the day-lookup surface once the body declines
  is the follow-up fix, and both the classifier comment and the earlier
  version of this bullet that claimed the lookup was kept are corrected here.
  **F18 widened it:** `_RE_AGO_OTHER` held only SOME weekday forms and
  `_named_fixed_date_anchor` only the FIXED_DATE family, so `this past
  weekend`, `past weekend`, `the past weekend`, `this weekend`, `this past
  Saturday` and `past Saturday` slipped BOTH guards and FIRED the shape over
  the two-month window — a main-firing construct landing on a *different
  firing shape*, which §6's first version did not cover. The shared
  calendar-anchor finder now owns the whole table, so these join the `None`
  family above instead of answering a two-month enumeration for a
  named-day question.

- **The class filter runs after the fetch cap.** `port.window_rows` fetches
  `cap` points + `cap` events (cap = `PER_ENTITY_FANOUT_CAP` = 200) newest-first
  per its own FETCH-priority contract, and the class filter then runs in Python.
  A window that saturates the cap can therefore truncate class rows. Pushing
  the filter into the Cypher predicate (alternative C) is the fix if this shape
  reaches traffic where that binds.
- **Lexical class selection.** A row whose text shares no token with the
  question's class phrase is not selected (recall loss, never a false include
  beyond the token match). The fail-closed fall-through keeps this from
  *degrading* the answer — legacy simply answers. The stemmer is deliberately
  three rules (`ies→y`, trailing `s`, a BOUNDED adjective→noun alias such as
  `musical`≡`music`); a class/row spelled in forms it does not reconcile
  (`box` vs `boxes`) loses recall, not precision. The bare adjectival `-al`
  strip this originally used is **removed** (#8779 F16) because it was NOT
  recall-only: it turned `formal` into `form` and enumerated "filled out the
  FORM for the insurance" as a *formal event* — a false include, which this
  design does not accept. A pair outside the alias list costs recall only.
- **Two more false-include classes were closed by measurement** (each was a
  word-class error, not a recall trade): a reduced relative COORDINATES, so
  the post-modifier run is stripped as a RUN — trimming a single token
  promoted the next participle to "the discriminator" and enumerated
  "organized my desk this morning" as an event of the class (F22); and the
  plural `s` strip no longer crosses a word whose singular is a DIFFERENT
  lexeme, so `news` no longer becomes `new` and enumerates "ordered a new
  couch" as a news item (F23). The stemmer's "loses recall, not precision"
  claim holds again for the pairs measured.
- **Three more false-include classes were closed by measurement** (each was a
  word-class error, not a recall trade): a PLURAL `-ing`/`-ed` word is a noun,
  so "the morning MEETINGS at the office" no longer loses its head to the
  reduced-relative rule (F19); an INFINITIVE complement names no class, so
  "the things TO DO" no longer promotes the verb `do` (F21); and a class name
  never keeps the preposition an interposed possessive left dangling (F20).
- **The docker-lane end-to-end pin is not added** (no docker in this lane). The
  hermetic `_assemble_connected` tests drive the same orchestration, and
  `tests/test_assembly_sdk.py` passes unchanged, but the docker/SDK golden for
  the new shape is a recorded follow-up for the lane that has a container.
- **`mypy` was not run** (not installed in this worktree's canonical env; the
  repo's `pyproject.toml` config is a baseline-with-disabled-codes pass). The
  new code is annotated and the constructs that could red a strict pass
  (`arg-type` for the shared walk, `union-attr` for the window fields) are in
  the disabled baseline; the CI `mypy tortoise/` leg is the check of record.
- The 133-Q sentence-level re-measure cannot be re-run here (the LongMemEval
  corpus is not committed); the coverage claim is measured on the committed
  census + the committed question-string sweep.
