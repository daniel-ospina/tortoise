# Sealed ask-lane corpus (issue #9097, epic #2281 Q3 exit bar)

The **sealed** corpus behind the ask lane's published exit measurement. It is
the promotion of the #9095 instrument (`tools/ask_spotcheck.py --distilled`),
which could *print* a number, into the repo's pre-registered sealed-benchmark
discipline, so a number can be *published*.

Runner: `tools/longmem_eval/measure_ask_distilled.py`.

## Layout

```text
tests/eval/ask/
  generate_corpus.py   # the AUTHORED corpus spec + deterministic renderer
  corpus.py            # paths, corpus hash, manifest verification, sealed loaders
  fixtures/<case>.json        # RUNNER-visible: ingest inputs + the question ONLY
  gold/<case>.gold.json       # SEALED — the grading key (answer + judge template)
  _manifest.json              # sha256 of every fixture AND gold file
  baselines/main.json         # first-run-pending: NO preset quality bar
  preregistration.json        # the protocol, committed BEFORE the first run
  test_ask_corpus.py          # corpus contract tests
```

## Two classes, two different bars

| Class | Shape | Graded by |
|---|---|---|
| `knowledge-update` | a planted old→new supersession (3 cases) | the D8 gate: no superseded claim rendered LIVE, every planted claim OBSERVED. The current-state RATE is **reported, never thresholded** (D8: the first published run sets the benchmark-side X set). |
| `verbatim` | a fact stated verbatim, no supersession (3 cases) | the **paired** within-run comparison: a case the `raw` arm commits and the `distilled` arm loses is a FAIL, not a trade. No number is needed — the comparison is internal to one run. |

The verbatim class exists because the exit bar's "no regression on
verbatim/raw" half was **unmeasurable** without it: the #9095 corpus is
knowledge-update only, so there was no case to regress and no paired
comparison to lose one. Adding these controls is not a competing benchmark —
the KU corpus identity is #9095's, promoted here unchanged.

## Invariant that makes the comparison mean something

Every distilled claim's text appears **verbatim** in a turn of its own session
(enforced in `generate_corpus._issues` and pinned by
`test_ask_corpus.py::test_every_distilled_claim_is_verbatim_in_its_own_session`).
The two arms therefore hold the *same fact* and differ in **form** — narrative
turn vs distilled claim — so a raw-vs-distilled difference measures the
distillation, never a corpus difference.

## The pre-registration — what was fixed, and before what (#11460)

`preregistration.json` is the **authoritative** record; this section is the
readable copy of it, and it is committed **before** any arm runs. A
pre-registration is citable later as *"this was decided before the number
existed"*, so everything below is a field of that file — not prose beside it.
**What that does and does not mean:** the bar (`evaluate_exit_bar`) *reads*
`corpus`, `reader`, `judge`, `seed`, `arms` and `gate_arm`; the target, metric,
population, pass line and falsifier below state **what that bar is** and are
citable, but they are not separately re-checked — do not read a field here as an
enforced gate unless it is one of those six.

### Judge — re-registered to the owner-ruled DeepSeek judge

| | value |
|---|---|
| judge | `deepseek:deepseek-v4-pro` (provider **named** — see below) |
| pin | `unpinned:deepseek:deepseek-v4-pro` (an honest label — see below) |
| rubric | `longmemeval-official`, hash `82d07b0de05daa48` (**unchanged**: only *who* grades moved, not *what* they grade with) |
| credential | the judge credential the run resolved, **by env var name only** — `judge_key_env` in the receipt/report, never its value and never a fragment of it. ⚠️ The name is the provider's standard env var; whether it holds a key dedicated to this eval or a shared one is an **operational** fact the receipt cannot establish, and it is not claimed. |

> **OVERRIDES:** the LongMemEval official judge (`gpt-4o-2024-08-06`, the
> paper's judge, and what this record pinned until 2026-11-07) — the owner
> ruled DeepSeek as our eval judge (`#2281` comment 6590088403, 2026-11-02:
> *"D1-JUDGE is SETTLED: use DeepSeek as the eval judge"*), so the record
> names DeepSeek and the pin travels as `unpinned:<spec>`: no number graded
> under this record may claim official-judge comparability.

Why this had to move: the decision rule makes any run whose resolved judge
differs from the record **`inconclusive: config_mismatch`**. Frozen against
the official spec, a DeepSeek-graded run was inconclusive *by construction*,
however green it was. The scorer now **builds** the judge from the same resolved
spec it records (`resolve_judge_spec`), so a run with no `--judge` is graded by
the judge the record names — and the clause is **not** weakened: a DeepSeek
judge against the *old* record still returns `config_mismatch` (pinned by
`test_the_official_judge_still_cannot_grade_this_pre_registration`).

The provider is part of the spec (`deepseek:…`, not the OpenRouter-namespaced
`deepseek/…`) so the **record** — not the box's ambient key set — decides the
endpoint, the model id and the credential name; otherwise `_PROVIDER_PRIORITY`
tries `openrouter` first and a shared routing key could grade the eval while the
report printed that name.

### Target, metric, population, bar, falsifier

- **Target.** The ask lane commits the **current** state on the
  `knowledge-update` class, and does **not regress** on the `verbatim` class,
  when the product's own reader answers over a distilled +
  planted-supersession corpus.
- **Metric.** `superseded_live@<gate_arm>` (per planted superseded claim:
  `seen` / `rendered_live` / `flagged`, attributed by the validity window) and
  `verbatim_no_regression` (per verbatim case: `raw.committed` vs
  `distilled.committed`, plus the list of cases the gate arm lost).
  `current_state@<arm>` — the knowledge-update current-state **rate** — is
  **reported, never thresholded** (D8). The judge's semantic grade is **not a
  thresholded or comparable metric** (see the two-number split below): it is
  printed as the reported rate and read as the paired within-run comparison,
  and no pass line or comparability claim is derived from it.
- **Population and denominator.** Every case `corpus.cases` names, in **both**
  arms inside **one** invocation; every class `corpus.classes` names. The
  denominators are read from the corpus itself (`n_cases`, `seen`/`n`,
  `verbatim.n`) and never assumed: a zero denominator is `inconclusive`, not a
  pass.
- **Exclusion rule — there is none.** A case is never dropped to make the bar
  clean: a run that misses a pre-registered case, class or arm is
  `inconclusive` (a subset run is not this measurement), and a case that failed
  to run in either arm is `inconclusive` too. The only legitimate way to change
  the population is to **re-register** — a new commit of
  `preregistration.json` — *before* a run exists.
- **Pass/fail line.** PASS iff the corpus verifies, the resolved config
  matches the record, every pre-registered case ran in both arms, **the vacuity
  floors did not fire** (the corpus plants a superseded claim, carries a
  verbatim case, and the raw arm committed one — without them the clauses below
  would hold over a measurement never made, which is `inconclusive`, never a
  pass), `rendered_live == 0`, `seen == n`, and
  `verbatim_no_regression.regressions == []`. No other number is compared
  against any threshold.
- **What would falsify it.** Any superseded claim **rendered live** on the gate
  arm; any planted claim **not observed**; any verbatim case committed on the
  `raw` arm and **lost** by the distilled arm (a KU win with a verbatim loss is
  a FAIL, not a trade); a corpus with no planted supersession, no verbatim case,
  or a raw arm that committed none (the clauses would hold **vacuously**); a
  corpus whose manifest or hash does not verify; a run whose judge, reader,
  pin, rubric hash, seed flags, arm set or gate arm differ from this record.
- **Not claimed.** No rate is claimed as a *thresholded* benchmark result and no
  absolute or cross-benchmark accuracy is claimed. The published claim is the two
  mechanical clauses; the D8 rate is **printed as a reported number** (§3 of the
  report) with no pass threshold attached, and the judge's reading is never
  presented as an absolute.

### Per-category rows (#11460 row surface; mechanism #2516)

The record pre-registers the **row surface**, so a headline can never merge the
classes away: one row for `knowledge-update` (the D8 gate + the reported rate)
and one for `verbatim` (the paired comparison). The run report renders §2b *Per-category readings* from the same per-case rows
the bar reads, and each class is read against the clauses that **apply** to it —
`knowledge-update` (the D8 gate + the reported rate) and `verbatim` (the paired
comparison) — **with no per-class threshold**, exactly as there is none for the
headline. A class this bar has no clause for is reported *not graded*, and on an
`inconclusive` run every class reads `inconclusive` rather than a class green
under an inconclusive headline. The **type-stratified gate MECHANISM** is
[#2516](https://github.com/daniel-ospina/tortoise/issues/2516)'s: it is declared
as a dependency, **not** implemented here.

### Two-number split (D14 / #5085 shape) — stated as it actually holds

The **judge-free half is the authoritative half**: corpus verification
(manifest + hashes) and the D8 supersession gate (`seen` / `rendered_live` /
`flagged`, attributed by the validity window) need no judge and cannot be
influenced by one.

The **judge-labelled half** is `committed` — whether the reader's answer matched
the sealed gold — which feeds the knowledge-update **rate** (reported, never
thresholded) and the paired verbatim/raw comparison. Both arms are graded by the
**same** judge inside **one** run, so a *systematic* judge bias cancels across
the arms; a *permissive* judge does not, which is why **no threshold and no
comparability claim are published** for that rate — it is printed as a
*reported* number in §3, with no pass line attached.

⚠️ The write-path harness keeps its judged arm entirely out of `metrics`
(`tests/eval/write_path/runner.py`). **This benchmark cannot** — its `committed`
IS the judge's grade — so the record states the equivalent guarantee instead of
borrowing a stronger claim: no **threshold** and no **comparability claim** are
published for that rate — it IS printed as a *reported* number in §3 — and the
judge-labelled half is only ever a paired comparison between two arms of one
run. The judge-free half
is **not** a makeshift substitute for a skipped judge; it is the bar's
authoritative half, and the judge-labelled half is labelled as such.

### Re-registering

```bash
uv run python -m tools.longmem_eval.measure_ask_distilled preregister \
    --out tests/eval/ask/preregistration.json
```

Byte-deterministic (sorted keys, fixed indent, no timestamps), so re-running it
over an unchanged corpus and judge is a no-op — a record that changed on a
re-write would be a bar a later lane could "refresh" without a commit to point
at. `preregister` refuses to write over a corpus that does not verify, and the
publish step refuses a run whose recorded pre-registration is not an ancestor of
the run commit, so the ordering is a property of history rather than a promise.

## Sealed-key discipline

The answer key's only on-disk home is `gold/`. The **run** step is handed
`corpus.load_fixtures`, which globs `fixtures/*.json` only and refuses any
fixture carrying a grading key (`answer` / `gold` / `grading` / `expected`) **at
any depth** (`find_grading_keys` — a top-level check is walked past by a nested
key); the **score** step is the one that opens `gold/`. Blindness is a property
of the API, not a promise — `tests/test_measure_ask_distilled.py::
test_the_runner_cannot_read_the_sealed_gold` proves it with a sentinel.

**What "sealed" does not mean.** It is structural, not cryptographic: for a
knowledge-update case the answer is one of the planted claims anyway, and the
full `corpus_hash` digests `gold/`, so it would act as a weak oracle. That is
why the **run** record carries `fixtures_hash` (fixtures only) and the **score**
step computes the full `corpus_hash` — the bar binds the run to the corpus
through `fixtures_hash` and the corpus to the protocol through `corpus_hash`.

A **gold-only** edit changes the tree-wide `corpus_hash`, which voids the
pre-registration: a run over the re-keyed corpus reports `inconclusive`
(`hash_mismatch`), never a silent pass.

## Regeneration protocol

⛔ **Generator-owned — never hand-edit `fixtures/` or `gold/`.** Add or change a
case in `generate_corpus._CASES` and re-run (the #7947 lesson: a hand-added
section in a sealed gold reddened 18 tests).

```bash
uv run python -m tests.eval.ask.generate_corpus             # idempotent write
uv run python -m tests.eval.ask.generate_corpus --check     # exit 1 on drift
uv run python -m tests.eval.ask.generate_corpus --validate  # committed-dir validation
```

Re-running is byte-deterministic (sorted keys, fixed indent, no timestamps).
Changing the corpus **intentionally** means re-writing the pre-registration
(`measure_ask_distilled preregister`) in its own commit — the pre-registration
that fixed the bar is required to precede the run it governs.

## Publishing is bound to the commit the report records

The run report's central claim is that it is *reproducible from the recorded
sha alone*, and that the protocol was committed **before** the run. Both are
properties of git history, so `publish` asks the repository instead of
asserting them (`measure_ask_distilled.verify_commit_binding`):

1. `--commit` must **resolve** to a commit in the repository — a sha that
   resolves to nothing is refused, never printed;
2. the corpus **at that sha** must re-hash to the published `corpus_hash`, and
   the pre-registration blob at that sha must be byte-identical to the
   published one (so `git checkout <sha>` reproduces *this* corpus and *this*
   protocol);
3. the commit that last touched the pre-registration at or before the run
   commit must be a **proper ancestor** of it (`git merge-base
   --is-ancestor`), and its committer instant must not be later — compared on
   `%ct` epoch seconds, which are recorded in the binding beside the `%cI`
   strings. (`%cI` renders each commit in its own UTC offset, so those strings
   do not sort chronologically across an offset change; the epochs do.) A
   clone whose shallow boundary is that commit reports **UNPROVABLE** and
   refuses, as does one whose commit times cannot be read — and so does one
   whose shallow file exists but cannot be **read**: an unreadable boundary is
   an unknown boundary, and assuming it is absent would silently pass the very
   check it disengages.

The receipt `publish` writes is then re-checkable **without a checkout**
(`validate_ask_receipt`): it must carry the binding at all, its own run commit
and full 40-hex pre-registration commit, the corpus hash the binding re-hashed
at that sha, the pre-registration's sha256 matching `resolved_config`'s, both
commit epochs ordered the right way, and — because `ordering: proven` is a claim
that rests on a proof — the proof sentence itself. Removing a field a
cross-check compares against invalidates the receipt; it never skips the check.

The verified binding is written into the receipt (`commit_binding`) and
rendered into §1/§6 of the run report, so a reviewer can re-check it from the
report alone. It adds no clause to the pre-registration's decision rule: it is
a publishing precondition, not a scoring change.

### The other half: the run records the tree it ran from

The binding above makes the named sha vouch for the **corpus** and the
**protocol** at that sha. It says nothing about the tree the **numbers** came
from, so a run made at tree A could be published under a commit B whose corpus
and protocol are byte-identical — any `main` merge between the run and the
publish has exactly that shape — and `git checkout B` would re-run different
code while every corpus check passed. So `run_sealed()` records its own
`code_identity`: the resolved `HEAD` commit and whether the tree was clean.
That record is carried through `score` into the receipt, and `publish` refuses
when it is missing, when it is not a full 40-hex sha, when it names a
**different** commit than the report is about to name, or when the tree had
**uncommitted paths** (a sha reproduces *sha plus a patch nobody recorded*,
which is not the recorded sha alone). A `git status` that fails is recorded as
not-provably-clean, never as clean: the check must not fail open on an
unreadable index. Both rows — the commit and the tree state — are rendered in
§1/§6 beside the binding.

The record is taken **twice**, before the first case and after the last one,
and that is a fifth refusal: a measurement takes minutes, so a commit that
lands while the run is in flight means the numbers came from **no single
tree** — the start and end records differ, and `publish` refuses the run rather
than publishing numbers under either sha. The same applies in reverse to a
path the run itself dirties. Untracked files count as dirty, and the untracked
mode is pinned (`--untracked-files=normal`) rather than read from the
operator's git config, so a repository configured not to report untracked
files cannot make a dirty tree look clean.

`git status` is **not the only oracle**, because it can be told to say
"clean" in two ways that have nothing to do with the tree: an index flag
(`git update-index --assume-unchanged` / `--skip-worktree`) makes git skip a
modified file's content, and an ignore rule (`.gitignore`, `.git/info/exclude`,
a global `core.excludesFile`) hides an untracked, importable module. Both are
recorded defeats of a status/diff check elsewhere in this package
(`guard_measured_revision.py`), so the record also puts the question to git
with no filter in it, over the **executed surface** — `tortoise/` (the SDK
under measurement), `tools/` (the measurement tooling) and `tests/eval/ask/`
(the corpus loader that tooling imports, so it EXECUTES on every run): the
untracked set is enumerated **without** `--exclude-standard`, and every tracked
file's index tag is read — `H` is ordinary, a lowercase tag is
assume-unchanged, `S` is skip-worktree, and anything else is refused. Each such
path is recorded as an uncommitted path, so publish refuses the run. A path the
surface scan finds that `git status` **already reported** is not recorded
twice (byte-caches included): one file is one uncommitted path, and the "an
ignore rule hides it" wording is used only for a path status did not list. A
failed `git ls-files` is recorded as not-provably-clean, exactly as a failed
`git status` is.

The surface is not hand-curated: `tests/test_measure_ask_distilled.py::
test_the_recorded_surface_COVERS_every_module_the_run_imports` imports the
runner in a fresh interpreter and fails, naming the file, if anything it loads
sits outside the list. That is how the corpus loader came to be in it — without
it, an `--assume-unchanged` patch to that loader published under a clean record.

Only the editor/VCS suffixes (`.orig`, `.rej`, `.swp`, `.swo`, `.DS_Store`) are
excused from the untracked scan, taken from the guard's own predicate
(`guard_measured_revision._is_noise`) so the two cannot drift. **Byte-caches
(`.pyc`/`.pyo`) are REFUSED**, which is the guard's default too (#3712): they
are executable code this record does not verify, a forged one's header matches
its source by construction, and CPython prefers a matching `.pyc` over the
`.py` beside it — so a tree carrying one is not a tree the recorded sha
reproduces, however clean `git status` looks. Tracked/committed byte-caches are
refused on the same ground. The byte-code-free requirement that makes a run
publishable is **this command's own**, not an inherited property:
`run_protocol` launches `tools.longmem_eval.run` and `full_context` with `-B`,
not this module. A bare interpreter launch writes `.pyc` files under the
surface on import — before any identity is taken — so `run` **refuses to
start** when `sys.dont_write_bytecode` is False, naming the removal command
and the invocation that works. Two halves, both checked: the interpreter
flag, and the tree itself — a byte-code-free launch over a tree that already
carries caches removes none, so it would measure and then be refused at
publish. The refusal is not advisory: `-B` stops caches being written but not
one being read, so a run is publishable only once the surface carries no
byte-caches.

## Why this is a separate tree from `tests/eval/write_path/`

Mechanical, not stylistic: the write-path corpus's own integrity gate derives
its file set from *its* `FIXTURE_GLOB`/`GOLD_GLOB` and requires `not extra`, so
an ask fixture dropped into that tree fails the write-path benchmark and changes
its single tree-wide hash (invalidating its committed baselines). Two
benchmarks, two trees, two hashes.
