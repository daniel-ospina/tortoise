"""The batch-integration doctrine's commands must execute against THIS repo.

WHY THIS FILE EXISTS (#10399 review P1-1/P1-2/P1-4/P1-5, P2-1, P2-3, and the fixes
at ``f3c6c803b``: ISSUE 1-5 / P1-P3; and after ``e715d81b2``: P1-P3 plus the
``a8be548762`` P2)
------------------------------------------------------------------------------
``docs/ops/batch-integration.md`` is a *doctrine*: an operator follows it
top-to-bottom and executes its command blocks. Commands that failed as written,
or facts that were false against the tree they describe:

* the suite blocks carried no ``TORTOISE_DB_URI`` and no marker filter, so the
  documented baseline dies at session start (``tests/conftest.py``'s
  ``_p4_uri_required`` fixture calls ``pytest.fail``) — and a bare
  ``pytest tests/`` would additionally run ``live`` / ``integration`` tests that
  no CI lane runs;
* every merge in a batch is a merge commit by construction, so the documented
  ``git revert <sha>`` exits 128 (``is a merge but no -m option was given``) and
  drops nothing;
* **Phase 1 created the baseline branch with ``git branch`` and never checked it
  out**, so the "baseline on ``origin/main`` ALONE" ran in whatever tree the
  operator stood in — and that ledger is what every later verdict is diffed
  against;
* **``git bisect run`` leaves HEAD detached at the LAST PROBE it tested, not at the first bad
  commit** — the ref git's own manual names as the result is ``refs/bisect/bad`` — and the doctrine
  read the blame from ``HEAD``, so it named the merge *immediately before* the culprit (measured: 27
  of 40 swept shapes). The merge-ness assert passed on that sha, so ``git revert -m 1`` dropped an
  innocent PR while the culprit stayed in the tree. The doctrine also never returned to the batch
  branch, so the documented "drop the culprit" committed the revert onto a detached HEAD (the batch
  kept the culprit) and the final verdict measured a tree nobody lands;
* **``-m track_b`` was silently dropped from the gate** although CI's
  ``test-track-b`` job is a MUST-PASS ``needs:`` of the required
  ``python-ci-gate`` — so a batch could be green with a gate member unrun;
* the invariant ("``main`` only ever receives a tree that has already passed the
  full suite") was unenforceable, because nothing re-checked that ``main`` was
  still the pinned base at landing time — the property bors and GitHub's queue
  get for free by owning the merge, and the live ``.mergify.yml`` queue keeps
  moving ``main`` while a batch runs;
* the landing assertion read ``$PINNED``, which Phase 1 set in a *different*
  shell, so in the landing actor's shell it compared against the empty string;
* **the pin was read with ``gh pr view … --jq -r '.body'``**, and ``gh`` has no
  raw flag: ``-r`` becomes the *expression*, so the payload is never filtered,
  ``$PINNED`` is empty, and the landing step exits 1 on a body that carries the
  line — the doctrine's only mechanical check, impossible to pass;
* **the baseline ran one half of the suite while the gate ran two**, so a
  pre-existing ``track_b`` red was classified ``NEW`` and convicted an innocent
  PR — and the baseline could still measure a **dirty tree**, because
  ``git switch -c`` carries uncommitted and untracked files into the new tree;
* **the leg list was a partition claim that was false**: ``test-d14-hosted-api``
  (``-m embedded_only``, URI-less) and ``test-record-postgres``
  (``[postgres]`` + ``TEST_RECORD_DB_URI``) are ``python-ci-gate`` members the
  batch's URI lane *skips* rather than reproduces — and the list named only four of
  the gate's **six** non-test members, omitting ``ci-introduced-failure`` (the #9984
  predicate, the rail's own automated version of Phase 1) and then ``changes`` (the diff
  selector — non-deferrable, and the one leg whose failure makes every other leg skip);
* **the blame step assumed its bisect result was a merge**: ``git bisect`` walks *every*
  commit in ``origin/main..integration/batch-1`` (``--first-parent`` is opt-in), so in a
  ``--no-ff`` batch the "first bad commit" can be one of a PR's own commits — and
  ``git revert --no-edit -m 1`` on a **non-merge** IGNORES the mainline and exits ``0``
  (git-verified), reverting ONE COMMIT of the PR instead of the PR: the rest of it still
  lands unreviewed, and the re-blame rule cannot see it, because the subject becomes
  ``Revert "PR1 commit 2"``, which matches no ``batch: merge PR #<N>`` line. Reproduced
  in a scratch repo against this doctrine's own assembly shape (blame stopped at
  ``PR1 commit 2``; the revert of it exited 0);
* **the gate's poll reported ``DONE`` for a run that was KILLED**: ``kill -0`` fails just
  as fast for a process the kernel OOM-killed (measured on #10399: five ``python``
  processes in 23 minutes), a killed run writes no summary, and Phase 4 diffs failure
  *ids* — so a run that died carried no NEW ids and read as ``BATCH-GREEN``.
  ``UNPROVEN-FULL-SUITE`` was keyed only on "a subset ran", never on "the run never
  finished";
* **Phase 0 made the SHARED FalkorDB instance the default** under a ``docker ps``
  precondition that is always true in this fleet, demoting ``tools/test_lane.py`` to a
  fallback — inverting the repo's recorded #5084 rule for the very measurement the
  doctrine calls non-negotiable (peer lanes' graphs enter the batch id set and not the
  baseline's, so Phase 4 convicts an innocent PR);
* **the baseline was the one phase that neither CLEARED its report nor checked that the run
  COMPLETED**: ``pytest`` truncates a junit file only in ``pytest_sessionfinish``, so a baseline the
  kernel killed left the PREVIOUS batch's report at the fixed path and the ``-gt 0`` guard passed on
  that stale number — and the baseline id set and count ARE the ledger every later verdict is diffed
  against, so a stale baseline both hides ``main``'s own red and convicts an innocent PR;
* **the baseline total had no carrier**: ``$BASELINE_TOTAL`` was computed in Phase 1's shell and read
  in Phase 3's, which may be a LATER shell (the doctrine already carries the run's status and
  ``$PINNED`` in files for exactly that reason) — there ``[ "$BATCH_TOTAL" -ge "$BASELINE_TOTAL" ]``
  compares against the empty string, bash rejects it (rc=2), and the count halt reports a COMPLETE,
  green batch as SHORTER than the baseline;
* **the ``-m track_b`` half was instrumented in NEITHER phase** (the ``bc5d78b77`` P2, still open at
  ``a4028e8d7``): it is a MUST-PASS ``python-ci-gate`` member, and the doctrine ran it bare — no
  junit report, so a red ``track_b`` id sat in neither side of the Phase 4 diff and ``BATCH-GREEN``
  stayed reachable on a tree whose gate member was red; and no status capture, so a half the kernel
  killed (measured on #10399: five ``python`` processes in 23 minutes) read exactly like a finished
  one. Half 1 had both treatments; the half whose whole purpose is the gate member had neither;
* **every verdict-bearing artifact was a host-global ``/tmp`` path**: the run's status
  (``/tmp/batch.rc``), the gate run's id report, the baseline count and their four siblings were
  unnamespaced, while the doctrine's own premise is that peer lanes run the suite on this box during
  Phases 1 and 3 — so a peer **batch** finishing its run during this one's wrote the same two files:
  the poll read ``DONE``, ``$RC`` was the peer's status (honestly a *complete* run, just not this
  one), ``$BATCH_TOTAL`` was the peer's count (real and coherent, so the count halt passed), and
  Phase 4 diffed the peer's green ids to print ``BATCH-GREEN`` for a tree whose own run was still
  executing; the mirrored case convicted an innocent PR with the peer's ids. Every artifact now
  lives under ONE per-batch directory (``B=/tmp/batch-$BATCH``), derived from a REQUIRED ``BATCH``
  and re-derived — with an absent-directory halt — by every fence that writes into it;
* the carve-out file count was quoted from memory and was stale;
* **the lane was the ONE shell-local value with no carrier, no re-derivation and no halt.** Phase 0
  ``export``s it, and Phases 1 and 3 are usually LATER shells — the doctrine already re-derives ``$B``
  and carries the run's status and ``$PINNED`` in files for exactly that reason. The lane had none of
  those treatments, and its absence does not fail visibly: ``conftest.py``'s ``_p4_uri_required`` is
  ``scope="session", autouse=True``, so pytest re-raises it **per item** — every collected test
  ERRORs at setup and the process exits **1**, which is one of the two codes each fence's own
  COMPLETED predicate accepts (measured: ``rc=1`` with one ``<testcase>`` per item in the report).
  Both id sets then become all-error and identical, the Phase 4 diff is empty, and the doctrine prints
  ``BATCH-GREEN`` for a tree on which zero tests executed. Both suite fences now ASSERT the lane —
  present, or an explicit carve-out opt-in — and HALT on it, before they clear or write any artifact;
* **the "jobs that declare ``needs: changes``" count was a hand-written ordinal.** The sentence said
  nine where ``.github/workflows/python-ci.yml`` declared eight — and one of the eight
  (``packs-compile``) is the non-test member the same bullet names eight lines above, so the **test**
  legs that skip numbered seven. It sat four lines above the paragraph that forbids exactly this ("Do
  not trust a count of these members written here"), and the leg check pins member NAMES, not the
  ordinal, so nothing could red. The sentence now points at the measurement
  (``grep -c '^    needs: changes$' .github/workflows/python-ci.yml``) and states no number.
* **the reviewed-head fetch was refused on a rewritten PR head, silently, and the stale ref is what
  got merged.** The assembly fence's ``git fetch origin pull/<N>/head:refs/tmp/pr-<N>`` writes a
  **local** ref, and ``git switch -C`` above exists precisely because a REBUILD is the intended case —
  so the ref is REUSED, and when the PR head has been rewritten since the last run (amend / rebase /
  force-push: the ordinary answer to a review) the update is a **non-fast-forward** that git refuses
  without ``--force`` / a ``+`` refspec (measured in a scratch repo: ``! [rejected]
  refs/pull/1/head -> refs/tmp/pr-1 (non-fast-forward)``, rc=1, ref left at the PREVIOUS head). The
  fetch was also the one line of the fence with no halt, so the fence continued and the merge landed
  yesterday's head under a comment reading "exactly the reviewed head" — a clean ``--no-ff`` merge
  with a correct-looking ``batch: merge PR #<N>`` subject, Phase 2's own eligibility invariant
  (``headRefOid`` == the reviewed head) broken one phase before Landing can see it. The fetch now
  carries ``--force`` **and** a halt, and both are pinned.

* **the landing assertion ran NOWHERE on the merge path.** ``origin/main == $PINNED`` was called "the
  one mechanical enforcement of the invariant", but it is a bash block in a document: the batch PR is
  an ordinary PR to ``main``, so the moment it carries the required contexts it satisfies this repo's
  live queue (``.mergify.yml``: ``queue_conditions: base=main`` + ``-draft`` + the ``check-success``
  contexts, and ``merge_protections_settings: auto_merge_conditions: true``) and is squash-merged
  onto **whatever ``main`` is at that moment**, with the assertion never executed. The batch PR is now
  opened as a **DRAFT** and un-drafted only once the assertion passes — ``-draft`` is itself a queue
  condition, so a draft is never queued, which is what converts a step someone may remember into a
  property of the queue. Pinned, because a doctrine whose enforcement never runs is a doctrine whose
  guard is green about nothing.
* **a SKIP was invisible to BOTH completion detectors.** Phase 3's "did it actually run" machinery
  was a status predicate (rc ∈ {0,1}) and a junit test COUNT — and a skipped test is still
  COLLECTED: it is inside ``tests=``, it contributes no failure id, and rc is 0. The reachable bypass
  is a socket probe, not an unset variable: ``tests/test_projection.py``'s ``live_proj`` fixture skips
  when the FalkorDB probe fails, which it does with ``TORTOISE_DB_URI`` correctly exported and the
  server gone (the private container reaped, a Docker restart) — every live test skips and the
  verdict is ``BATCH-GREEN`` on a tree where the suite this document exists to gate never ran. This
  repo already fails closed on the class in CI (``tools/skip-guard.py``, #1382: "the historical
  silent-green masked the #1382 EP regression class for days"); the batch now records the BASELINE's
  skip count and reason multiset — cleared first, both carriers — and halts on any skip the baseline
  did not have. Baseline-diffed, never a whole-run gate, because the batch lane's ``embedded_only``
  skips are expected.
* **the blame grader convicted a commit on an INFRASTRUCTURE error.** ``git bisect run``'s protocol is
  0 = good, **125 = skip this probe**, anything else = bad, and the documented grader was
  ``… >/dev/null 2>&1 || exit 1`` — which grades pytest 2/3/4/5 (interrupted / usage / internal /
  misconfigured) and 128+N (SIGKILL/OOM — the class this doctrine measured five times in 23 minutes on
  #10399) as "this commit is the culprit", contradicting the document's own rule that only 0 and 1 are
  a COMPLETED run. **Measured** in a scratch repo against a five-commit ``--no-ff`` batch whose probe
  always exits 2: ``bisect run`` exited 0 and named the OLDEST probe — the batch's first merge — and the
  next documented step **drops** it, irreversibly within the batch. The grader now accepts exactly 0
  and 1 and returns 125 for everything else.
* **the merge line had no halt, and a no-op merge exits ZERO.** ``git merge --no-ff`` on a head that is
  already contained prints ``Already up to date``, exits 0 and creates **no merge commit** — so the
  documented ``git log --oneline -1`` was cosmetic, the manifest named a PR whose commits were not in
  the tree, and Phase 4's ``git revert -m 1`` had no mainline. The fetch directly above it got a halt
  for the adjacent failure (a REFUSAL exits non-zero); this is the other half, so the status alone
  cannot catch it and ``git rev-parse -q --verify HEAD^2`` is asserted after the halt.
* **two hand-written literals of LIVE config**: "``main`` keeps the **seven** required contexts" and
  ``batch_size: 4``. Both are READINGS of live config — the declaration home for ``main``'s required
  contexts, live-asserted by ``.github/scripts/check-required-set.py --live``, and ``.mergify.yml``,
  which itself says
  ``batch_size`` is a separate lever #5384 owns — the exact class the document forbids four pages
  earlier for the ``needs: changes`` set, and nothing pinned these. Both numbers are gone; both
  sections name the source. The **branch** name is stated as fixed (the LEDGER is per-batch, the branch
  is not), with a second concurrent batch declared out of scope.
* **the DRAFT was credited with a guarantee it does not buy.** The draft rule closed the ``4dc214c02``
  P1 — the landing assertion ran nowhere on the merge path — and then over-claimed: it called itself
  *"the guarantee bors and GitHub's queue get by owning the merge"*. Mechanically it is not, and
  ``.mergify.yml``'s own configuration contract says why in one line: ``queue_conditions`` gates
  **ENTRY** (against the PR head) and ``merge_conditions`` gates the **MERGE** (against the queue
  branch). ``-draft`` is an ENTRY condition, so un-drafting makes the PR *eligible* — it does not pin
  the base. ``merge_method: squash`` then applies the batch's diff to ``main`` **as it is at merge
  time**; the queue's only re-validation of that moved composition is ``check-success=python-ci-gate``
  (the TIERED selection, never the full suite); and the branch protection's ``strict`` is **false**
  (its declaration home's live assertion is ``.github/scripts/check-required-set.py --live``), so
  nothing forces the batch branch onto the moved base. ``.mergify.yml`` measures a full
  cycle at ~37 min against continuous merges, so the window is the whole cycle, and ``PINNED' +
  batch-diff`` can land with every check in the Landing section green. The document now states that
  residual window, names route 2 (the declared freeze) as the ONLY route that closes it, and — for the
  routes that leave it open — carries a **post-merge tree detector**: a squash onto ``PINNED``
  reproduces the tested tree exactly, so the landed commit's tree must equal the tree the suite
  MEASURED, and the detector compares ``rev-parse "$LANDED"^{tree}`` against the ``tested-tree: <sha>``
  line Phase 3 records (re-recorded by the post-revert re-check), halting on a mismatch or an absent
  carrier. It was originally ``git diff --quiet origin/integration/batch-1 "$LANDED"`` — a ref NO step
  in the doctrine pushes, which is absent on the landing actor's box and stale on the drop path
  (round 36, F1: a ref also records where the branch is *later*, not what was tested). Measured in a
  scratch remote, both directions: an honest landing (base == pin at merge time) → the trees match;
  the base moved by an unrelated merge before the landing → they differ, with the untested ``h.txt``
  in the diff — and the fence halts on exactly the second.
  ⛔ That bullet's declaration home is named by its LIVE ASSERTION in this file, never by its path:
  clause (viii)(b) of ``tools/mergify_config_guard.py`` treats a ``tests/`` file that spells the path as
  an authoritative READER of it, and this round's first cut spelled it — reddening the REQUIRED
  ``manifest-integrity`` job, exactly the defect ``dbd79ba07`` fixed one check over. Caught by the
  guard's own ``--static`` run, not by CI.
* **the two ``pytest tests/`` runs were a SUPERSET of the gate's population, and the doctrine credited
  the marker filter with a comparability it does not have.** ``python-ci.yml``'s deterministic lanes do
  not run ``tests/``: they run a **file list** from ``tools/ci_selection.py``, which skips the
  ``tests/e2e/`` prefix **by path** (#1349) — and the workflow says why, *"Without browsers they only
  ever ERROR here"*. So ``:308-311``'s claim (*"The filter is what makes the run comparable to CI at
  all"*) was false, and the difference is the worst possible one for a ledger built from failure IDs:
  no browsers → dozens of ``ERROR`` ids that no healthy batch can clear, so plain ``BATCH-GREEN`` is
  unreachable; browsers installed (this fleet installs them for ``app-test``) → those tests RUN and
  default to **live targets** (``tests/e2e/test_welcome_page.py`` points ``WELCOME_URL`` at the
  deployed site), so one flaky id is NEW and the prescribed remedy is to **drop an innocent PR**.
  Both runs now carry ``--ignore=tests/e2e``, the comparability sentence names the path selection
  beside the marker filter, and ``_suite_argv_ok`` — which refused the argv that fixes it — now ADMITS
  and REQUIRES that one selector on the ``tests/`` half.
* **``$BATCH`` was assigned, never EXPORTED — the one value the doctrine claimed "needs no carrier".**
  All three occurrences were plain assignments, and a plain assignment is not in the environment a new
  shell inherits (measured: ``BATCH=1 sh -c 'B=2; sh -c "echo ${B:-UNSET}"'`` → ``UNSET``). Phases 1
  and 3 are, by the doctrine's own premise, LATER shells — so in the normal case the name is empty
  there and Phase 1 halts at its own ``:?`` line before anything runs. Fail-closed, but the claim is
  exactly what the run does not do, and it is the same shell-local class the doc spends twenty lines
  on for ``TORTOISE_DB_URI``. Phase 0 now ``export``s it (a later plain re-assignment preserves the
  attribute), and ``_LEDGER_NAME`` tolerates the declarator prefix — anchoring it at ``^BATCH="`` made
  the honest export INEXPRESSIBLE, the guard-refuses-its-own-remedy shape ``_PIN_DEF`` already closes.
* **the run that ISSUES the verdict after a drop was PROSE.** *"Then re-run only the failed files,
  then one more full suite run — both halves"* was a sentence, so the one run whose result is read into
  the Phase 5 verdict row carried **none** of the detectors: no lane assertion, no tree assertion, no
  detached status with the ``rc ∈ {0,1}`` COMPLETED predicate, no ``tests=`` count against the
  baseline, no skip carriers. And its id source was ambiguous: Phase 4 says the id sets ARE
  ``$B/batch-junit.xml`` and ``$B/batch-trackb.xml``, but after a revert those files still hold the
  PRE-revert run — so the diff a reader is told to perform compares the baseline against the tree that
  still contained the culprit. It is now a fence that asserts clean-tree, tree and lane, launches
  detached with a bounded wait and a COMPLETED predicate, compares count and skips against the
  baseline, runs BOTH halves, and writes those same two reports after CLEARING them.
* **the half that carries the MUST-PASS gate member had no SKIP detector, and the two ``git fetch
  origin main`` lines that the ledger and the landing gate ride on had no halt.** ``-m track_b`` got a
  junit report and a COMPLETED predicate, and ``$B/batch-trackb.xml`` / ``$B/baseline-trackb.xml``
  were opened **only** as id sources in the Phase 4 diff — never for skips, which is the one axis both
  other detectors are blind to. It is reachable on an ordinary PR: ``tests/test_ingest_safety.py``
  decorates all four of its tests with ``TRACK_B_SKIP``, so a constituent that removes
  ``promote_source``/``promote_point`` or the A9 sentinel flips the whole half to SKIPPED on the batch
  tree while the baseline still runs it — rc 0, ``tests=`` untouched, no failure id, ``BATCH-GREEN``
  printed for a ``python-ci-gate`` member that never ran. Half 2 now records its own skip count and
  reason multiset in Phase 1 and halts on a NEW one in Phase 3 and in the post-revert re-check,
  exactly as half 1 does. The two bare fetches are the other half: with no ``set -e`` a REFUSED
  ``git fetch origin main`` leaves the remote-tracking ref at the last success — in the normal flow,
  the pinned base itself — so Phase 1 logs a baseline of a tree that is not current ``main`` (and
  convicts an innocent PR of main's own new red), and the Landing fence passes its pin check on a
  moved ``main`` and lets the batch land onto a composition no run has seen. Both now halt. And the
  tree assertion that the gate fence carries was pinned by PRESENCE only: pasting ``main`` in as the
  compared value — the gate run required to happen ON ``main`` — kept the branch name in the echo
  prose, kept the halt, and measured **0 red**; the check now reads the SUBJECT (the fence's own
  ``switch -C`` branch where it creates one, else a real non-``main`` ref). Finally, the section that
  promises it "never restates" ``.mergify.yml``'s ``batch_size`` restated it in prose fifteen lines
  later, which the YAML-only literal could not see.
* **the post-merge detector compared a BRANCH the doctrine never establishes, and three axes were
  pinned somewhere rather than in every fence that issues a verdict.** The residual-window detector
  diffed ``origin/integration/batch-1`` against the landed commit, and no step in the doctrine pushes
  that branch: on the landing actor's box the ref is ABSENT (the fetch dies on a refspec nobody
  wrote, behind a bare ``|| exit 1`` that shows only git's own fatal), and on the drop path it is
  STALE — Phase 4 commits the revert locally and nothing pushes again — so a PERFECT landing differs
  by exactly the dropped PR and is reported as a process failure, indistinguishable from the one real
  violation. Even a live ref is the WRONG OBJECT: it records where the branch is NOW, so one commit
  made after the run moves it while the tested tree stays behind, and the comparison certifies a
  landing no run has seen. The detector now compares ``$(git rev-parse "$LANDED"^{tree})`` against a
  ``tested-tree: <sha>`` line carried BY VALUE in the batch PR body — printed by Phase 3, re-printed
  by the post-revert re-check after the revert changes it, and HALTED on when absent. "State the push"
  was the cheaper remedy and was rejected: it cannot see the post-run-commit case. Two axes were
  pinned at the population's weakest member: the half-1 skip check ``continue``d past a
  ``batch-junit`` consumer with no comparison and let ANY one fence satisfy it, and the gate-status
  check concatenated every gate's lines — measured, deleting Phase 3's half-1 skip block, flipping its
  comparison to ``-ge``, and widening its ``$RC`` predicate to ``-ge 0`` each left the module GREEN
  (0 of 37) while a sibling fence's intact copy covered it. Both are per fence now. And the document
  contradicted itself about its one carrier: one paragraph said ``$BATCH`` "needs no carrier" while
  the fence that EXPORTS it said the claim "would be false in exactly the normal case" — the first is
  now the halt it actually has, and the guard refuses it.
* **the ``pinned-base:`` carrier had readers, a halt and NO producer.** Landing read the line out of
  the batch PR body and halted when it was absent, and ``:313`` told the operator to record it — but
  no fence ever PRINTED it: its `printf`s carried other values. Landing is a different
  shell/actor, so the value is not in scope when the body is written, and the only re-derivation the
  document left reachable is ``git rev-parse origin/main``: current ``main``, which turns the
  document's "only mechanical enforcement of the invariant" into ``[ "$(git rev-parse origin/main)"
  = "$(git rev-parse origin/main)" ]`` — always true, on a moved base, with every other check in that
  section green. Fail-OPEN, and invisible in both directions: 37 of 37 checks were green with no
  producer and 37 of 37 stayed green when one was added, because the guard pinned the READER hard (one
  definition, a PR-body read, a halting fetch before it) and pinned no WRITER. Phase 1 prints
  ``pinned-base:`` beside the assignment now, and every carrier the doctrine reads back out of the PR
  body must be produced by a ``printf`` in an EARLIER fence, from a variable that fence DERIVES.
* **the deterministic half's skip-REASON axis was pinned per FENCE, and half 1 shares its fence with
  half 2.** Phase 3 and the post-revert re-check each run BOTH halves, so each carries two
  ``comm -13`` diffs and two ``[ -s … ] && { … exit 1; }`` halts; ``any()`` over the fence let the
  ``track_b`` copy satisfy the half-1 demand, and the ``track_b`` filter on the sibling check meant
  only half 2's pair was ever verified. Measured at ``1c5fefdc4`` against the honest document:
  deleting Phase 3's half-1 ``comm -13`` line, deleting its halt, deleting both, deleting the
  post-revert half-1 halt, or flipping the diff to ``comm -23`` measured **0 of 37 red** each — so a
  skip SWAP at an equal count (one live-FalkorDB skip traded for a collection skip) stayed invisible
  in the fence the verdict is printed from, which is the #1382 class this doctrine closes for half 1.
  The axis is parameterised by the junit report a run WRITES now: each half owes its own reasons, its
  own diff against ITS baseline, and its own halt.

WHAT IS PINNED, precisely. The **command fences** are pinned as commands: a
command edit that would not run reds this file, and a wording edit that keeps the
commands honest does not. Three things are additionally pinned as **substrings**
because they are the document's contract rather than its wording — the
``OVERRIDES:`` line, the verdict markers, and the "never green on a partial lane"
rule — plus the two marker strings (``--no-ff``, and a lane SELECTION — the literal
``export TORTOISE_DB_URI=…`` or ``lane="$(… tools/test_lane.py uri)"`` + ``eval "$lane"``,
both executable, since a commented-out export selects nothing) that
must sit in the *fence* they belong to, not merely somewhere in the prose, and
the leg names (``test-d14-hosted-api``, ``test-record-postgres`` and the non-test
``packs-compile`` / ``manifest-integrity`` / ``workflow-lint`` / ``surface-guard`` /
``ci-introduced-failure``) whose omission
would make the reproduced/not-reproduced list read as a complete partition — pinned in the SENTENCE
that makes the claim, because every one of those names is mentioned a second time inside the same
bullet and a document-wide needle certified a mention while the list lost all five (round 39) —,
plus the FAILURE-ID LEDGER the verdict is a set difference over (the id rule — ``<classname>::<name>``
of every ``<testcase>`` carrying a ``<failure>`` or ``<error>`` CHILD — the four junit reports, the
three ``comm`` sets, and the fail-closed halt on an empty extraction from a report that says it
failed), plus the HALT-MESSAGE rule (no markdown backtick in a fence command: bash EXECUTES it), plus the lane
ASSERTION (``TORTOISE_DB_URI`` or an explicit carve-out, HALTING) in every fence that runs the suite
— a SELECTION in Phase 0's fence is a different shell's environment, so it cannot substitute for one,
plus the TREE every full-suite fence runs on (named, un-moved between the name and the run), the
BOUNDED WAIT between a detached launch and the first read of the status file it is still writing, the
assembly MERGE (halted, and followed by a halted ``HEAD^2`` merge-commit assertion — a no-op merge
exits 0), the baseline-diffed SKIP carriers (a skipped test is collected, so neither the status nor
the count can see it), the GRADED ``git bisect run`` (a non-``COMPLETED`` probe status is git's 125,
not a culprit), the DRAFT-until-asserted landing rule, the RESIDUAL-WINDOW detector that answers the
window a draft cannot close (the landed tree against the tested tree, halted), the PATH EXCLUSION that
makes a ``tests/`` run CI's population (``--ignore=tests/e2e``, required on that half and refused on the
single-file half), the EXPORTED ledger name (``export BATCH="${BATCH:?…}"``; a later plain
re-assignment preserves the attribute), the post-revert RE-CHECK (a fence after the revert, with every
detector, clearing the two reports Phase 4 diffs), and no hand-written
number for a live-config
property (the required contexts that ``check-required-set.py --live`` asserts, ``.mergify.yml``'s
``batch_size``) — and the TESTED TREE the landing is compared against, carried by VALUE in the batch
PR body (``tested-tree: <sha>``, printed by both fences that run the suite on the batch branch),
because a branch records where it is LATER while the invariant is about what was MEASURED, and the PRODUCER of every carrier the doctrine reads back out of the batch PR body (``pinned-base:`` from Phase 1, ``tested-tree:`` from Phase 3 and the post-revert re-check — a ``printf`` in an EARLIER fence, from a variable that fence DERIVES, so a hand-copied value cannot silently replace a derived one), and the skip-REASON axis per RUN rather than per fence (each half owes its own reasons, its own diff against ITS baseline, and its own halt).

Non-vacuity, **measured** at THIS head by pointing this module's ``DOC`` at each pre-fix
revision rather than asserted — **41 checks**, **0 red**. The per-revision literals in the next
two paragraphs are the ROUND-37 measurement (taken when the guard had 38 checks) and are left as
that round's record; the standing matrix is RE-MEASURED at 41 in *Round 39* below, which
supersedes every ``of 38``/``of 39`` here. The head the round-35 review was taken
at (``9f8e12192``) is **11 of 39**: the three findings of that review that red a CHECK there — the
``-m track_b`` half's missing SKIP axis, the two unhalted ``git fetch origin main`` lines the pin and
the landing gate ride on (one check, enumerated over fences) — plus the ``batch_size`` value restated
in prose, plus (round 36) the residual-window check, which now requires the tested tree to be
carried BY VALUE, plus the ``$BATCH`` "needs no carrier" claim, which the ledger-name check now
refuses. The tree assertion's unpinned SUBJECT is GREEN on that document by
construction (it asserts a non-``main`` ref), which is exactly why it is pinned by a MUTANT rather
than by the revision axis: pasting ``main`` in as the compared ref measured 0 of 37 red. The
P1+P2 repair head, ``c6d4c8278``, is **3 of 38** — the residual-window requirement (its detector
diffs a remote batch branch) AND that same ``needs no carrier`` refusal, both of which are round-36
findings about THAT document; the ``c7a772628`` / ``657a544a4`` head is
**10 of 38** (the checks that did not exist yet — the residual-window check, the ``a66387f9f``
round's CI-population, exported-ledger-name and fenced post-revert re-run checks, the pin-reading
fetch check, the tightened argv on ``test_every_full_suite_command_is_one_of_the_two_deliberate_halves``,
and this round's SKIP-axis and ``batch_size`` tightenings), **13 of 38** at ``4dc214c02`` (that
round's five — the skip carriers, the graded bisect, the halted merge + ``HEAD^2`` assert, the
draft-until-asserted landing rule, the live-config literals — plus the ones that did not exist then),
**16 of 38** at ``7fb722a9a`` (plus the gate fence's TREE assertion, the bounded wait before the
status read, and the reviewed-head fetch's force + halt), **18 of 38** at ``62c79b0b8`` (plus the lane
assertion and the hand-written ordinal — the two findings of the round that preceded it), **19 of 38**
at ``a6a71c1e5`` (the per-batch ledger, added there) and at ``a4028e8d7`` (that one plus the
``-m track_b`` instrumentation, which this round's SKIP-axis requirement now also reds), **21 of 38**
at ``a12792afe`` (those plus the baseline count's carrier and the baseline report's completion
check), **34 of 38** at ``a17576c43``, **31 of 38** at ``f3c6c803b``, **29 of 38** at
``a8be548762``, **27 of 38** at ``295d6d385``, **26 of 38** at ``32a2dc87d`` / ``ba7f83feb`` /
``64ae70fc5``, **25 of 38** at ``ed1c03b82`` and ``c50628450``, and **24 of 38** at ``a888c6980``
and ``aede4190f`` — where three are exactly that round's findings (the bisect attribution, the gate
run's completion, and the omitted ``changes`` member) and the fourth is the exit-status-persistence
requirement, which landed AFTER those revisions. That fourth is the rule rather than an exception,
and it is also what a TIGHTENED check does: a check added or widened later reds on every revision
older than the change, so a revision's count is "its own round's findings plus the checks that did not
exist (or did not yet demand the property) then". Every count above is re-measured at THIS head, not
carried forward — a carried-forward count drifts silently as checks land (six of them were stale by
exactly the check the status wrapper added), which is the same defect class as a hard-coded bare-run
ERROR count, and it is why the numbers are re-measured when a check is added or tightened rather than
inherited. The deltas against the previous recording are NOT uniform and are not claimed to be:
a revision whose fence already tripped the tree, fetch or skip requirement gains nothing from
tightening it, and a revision that predates a check gains all of it — which is why every number here
was re-measured at this head, revision by revision, rather than derived by adding a constant.
Four checks the revision matrix
alone cannot red are
named rather than left implied: the parser-sanity check and the contract sentences (green by
design — they guard against a re-pointed or deleted document, not against a bad command),
the header-count check (green on every revision by construction: it reads THIS module's
header, which a ``DOC`` re-point does not change), and the ``#2985`` extras trap, which every
revision here already fixed and which is pinned by the ``uv sync`` mutants instead. The
contract-sentences check is green from ``f3c6c803b`` on, which is when those sentences landed.

**A revision matrix is not the whole measure — MUTANTS are the other axis, and
they are measured too.** A doc revision only proves a check reds on the *mistakes
that were made*; it says nothing about the next one. So every check that pins a
command is also exercised against a mutated copy of the honest document, and the
mutant must red: a refusal that only ``echo``es the test, a halt inside a
subshell (``&`` / ``| cat``) or swallowed by ``|| true``, a suffix after the halt
(``|| exit 1 && git switch -``), a pin create followed by a ``;``-sequenced mover
that OWNS the halt (``; git switch - || exit 1``, ``; git switch main || exit 1``,
``; git reset --hard main || exit 1``), a second ``PINNED=`` assignment on the same
line or behind a declarator, a pin whose source falls back
(``… || git rev-parse origin/main``) or discards the body read
(``… >/dev/null; git rev-parse origin/main``), a fetch to a stale ref (``|| true``,
``--dry-run``, ``main:refs/remotes/…``), a wrapped or negated landing carrier,
an allowlisted no-op placed AFTER the subject so it owns the halt
(``; true || exit 1``, ``; :``, ``; echo ok``, ``; cd .``, ``; printf ''``,
``; git fetch origin main``), the subject behind a PIPE or a JOB SEPARATOR
(``| true || exit 1``, ``& true || exit 1`` — a pipeline's status is its LAST
command's and ``&`` does not wait, so a failed subject never reaches the halt),
and ``cd`` relocation (``cd /other-worktree``; worktrees share an object DB, so the
pin then succeeds in another repo) — the same set at the pin creates, the batch
fence and the dirty-tree refusals. Each one is green on
the *spelling* and wrong on the *behaviour*, which is the only class a substring
predicate cannot see. The invariant those last ones taught is **halt
REACHABILITY**: the halt must be reachable from the SUBJECT's failure, so every
separator between the last subject segment and the ``||`` is ``&&``, no separator
is a pipe, and the subject itself is in the chain — a per-segment allowlist alone
blesses ``pin ; true || exit 1``, because ``true`` is a no-op. **The ledger, by
provenance**: 109 mutants accumulated through ``c50628450``, 8 added in round 27 (its
own ledger), and **15 measured here, 15 killed** — drop ``--first-parent`` from the
bisect start, revert an independently-typed ``<merge sha>``, a merge-ness assert that
only PRINTS instead of halting, delete the ``wait``/``RC`` capture, widen the status
predicate to accept a signal kill (``-ne 0``), replace the count comparison with
``true`` and flip it so it cannot fail, delete the verdict row's "did not COMPLETE"
clause, delete ``changes`` from the leg bullet, a mover on the merge line that HALTS
only on failure (``… refs/tmp/pr-<N> && git switch main || exit 1`` — it dies only
because the assembly fence no longer exempts a line merely for carrying ``|| exit 1``:
a PRESENCE predicate, which the mover has already got past by the time the halt could
fire), and the four round-27 re-measurements
(poll / merge / after-merge-log / revert each with an appended mover) — plus the
guard-internal pair: restoring the quote-blind ``_code`` lets a ``&& git switch main``
suffix on the merge line pass GREEN, which is exactly what the quote-aware cut kills.
**8 more measured this round, 8 killed**, each red on the two checks it targets: delete the
carrier write, delete the consumer's ``cat`` bind, stop clearing the carrier, drop the
absent-carrier halt; stop clearing the baseline report, drop the ``BASELINE_RC=$?`` capture,
widen its predicate to accept ``137`` (SIGKILL/OOM), and make the predicate ``|| true``.
**7 more measured with the ``-m track_b`` instrumentation, 7 killed**, each red on
``test_the_track_b_half_is_completion_checked_like_the_deterministic_half`` and on nothing else:
delete the gate half's ``--junitxml``, delete the baseline half's ``--junitxml``, stop clearing
the gate half's report, move that clear to AFTER the run, drop the ``TRACK_B_RC=$?`` capture,
widen its predicate to accept ``137``, and make it ``|| true``.
**5 more measured with the per-batch ledger (`a6a71c1e5`'s P2), 5 killed**, each red on
``test_every_verdict_artifact_lives_in_a_per_batch_directory`` (the later-shell one on
``test_every_full_suite_command_is_one_of_the_two_deliberate_halves`` too): default the name
(``${BATCH:-1}``) instead of requiring it, hand-type the directory (``B="/tmp/batch-1"``) instead of
deriving it, drop the absent-directory halt, re-point ONE ``--junitxml`` back to a bare
``/tmp/batch-junit.xml`` (the peer-batch collision), and clear the ledger in a LATER shell.
**4 more measured with the lane assertion and the ordinal, 4 killed**, each red on ONE check and on
nothing else: replace a suite fence's lane halt with ``|| true``, delete the lane assertion from the
LAST suite fence, move that assertion AFTER the suite line (all three red
``test_the_lane_is_asserted_in_every_fence_that_runs_the_suite`` — the halting, the every-fence and
the before-the-run halves of it), and restore the pre-fix ``the nine jobs that declare
`needs: changes``` ordinal (red ``test_no_hand_counted_ordinal_for_the_needs_changes_jobs``).
**6 more measured with the reviewed-head fetch (`7fb722a9a`'s P1), 6 killed**, each red on
``test_the_batch_fence_rebuild_is_re_runnable`` and on nothing else: drop ``--force``, drop the halt,
``|| true`` for the halt, ``--force-with-lease`` (a different option), a ``| cat`` before the halt (a
pipeline's status is its last command's, so the fetch's failure never reaches it), and a
``&& git switch -`` suffix after it. Both HONEST alternative spellings are GREEN, measured — ``-f`` and
``origin +pull/<N>/head:…`` — so the check accepts the documented remedy rather than only the spelling
it was written against (the "the guard refuses its own remedy" shape this file has fixed three times).

**19 more measured with the ``4dc214c02`` round, 19 killed.** Each reds its own check, and the two
shapes that change a *separator* red the assembly one-command check as well (both are the same defect
one level up): delete the merge's halt; make it ``|| true``; append a mover after the ``HEAD^2`` assert;
delete that assert; leave it unhalted. Delete the baseline skip-count carrier; stop clearing it; drop
the absent-carrier halt; flip the comparison to ``-ge`` (which refuses a clean run and passes a dirty
one); delete the reason-multiset write; delete the reason diff; neuter the reason halt. Restore the
pre-fix ``|| exit 1`` bisect grader; keep the grading but drop ``exit 125``. Delete the draft
paragraph; name a draft rule and a ``queue_conditions`` entry in UNRELATED sentences (so the
mechanism is not identified — the check reads one sentence, not two words anywhere). Restore "the
seven required contexts"; restore ``batch_size: 4``; delete the concurrent-batch clause. The honest
document is **0 red**, and the review's own head ``4dc214c02`` is **13 of 38** — exactly
these five findings plus the checks that did not exist yet, and nothing else.

**The ``a66387f9f`` round** (three checks and the tightened suite argv) carries its own
measurements in its commit; the revision deltas above are where THIS file re-measures them at
this head, so the matrix cannot disagree with the population of checks again.

**13 more measured with the ``c7a772628`` round, 13 killed** — each red on
``test_the_residual_landing_window_is_stated_and_detected`` and on nothing else: delete the detector
fence; replace its diff halt with ``|| true``; drop the fetch's halt; read ``$LANDED`` AFTER the diff;
make the diff compare two constant files instead of the landed tree; delete the ENTRY/MERGE split
sentence; credit the draft with "the guarantee" unqualified; drop ``merge_method: squash``; drop the
tiered re-check; drop the ``strict`` **false** clause; promote the detector to *prevention*
("it does not prevent the landing" → "it prevents the landing"); make route 2 no longer the ONLY
route that closes the window; and point the diff at ``origin/main`` vs ``$PINNED`` instead of the two
trees. The detector's own semantics were reproduced in a scratch remote rather than asserted: an
honest landing (base == pin at merge time) → ``git diff --quiet`` ``rc=0``, and a base moved by an
unrelated merge before the landing → ``rc=1`` with the untested file in the diff — the direction the
fence halts on, and the direction it must not halt on.

**4 more measured this round, 4 killed** (each red on the check it targets and on nothing else, except
the first, which reds its own check plus the argv allowlist that now requires the exclusion): drop
``--ignore=tests/e2e`` from a ``tests/`` run (2 red); restore *"The filter is what makes the run
comparable to CI at all."* (1 red); make the Phase 0 ledger name a plain assignment again (1 red); and
delete the post-revert re-check fence, leaving the prose (1 red). The revision axis agrees: the head
the review sat at, ``484a4911d``, is **10 of 38** — the three new checks plus the argv check plus this
round's tightenings, which did not exist as requirements at that head — plus, since round 36, the
residual-window check's tested-tree requirement, which reds there because that head's detector diffs
a remote batch branch. (The earlier ``8`` was measured before that tightening, and before round 37's producer and per-run SKIP checks, which both red there.)

**Round 37 — two P2s, and the check count moved 37 → 38.** Both were producer/coverage gaps in
machinery that already had readers, so each fix is a small one plus the check that would have caught it.

*The ``pinned-base:`` carrier had no producer.* Landing read the line from the batch PR body, halted
when it was absent, and no fence ever printed it, so the only re-derivation left was
``git rev-parse origin/main`` — current ``main``, which makes the assertion compare ``main`` to
itself. Measured both ways: with no producer the guard was **37 of 37 green**, and adding one also
left it **37 of 37 green**, because the reader was pinned hard and no writer at all. Phase 1 now
prints the line beside the assignment, and the pair is pinned together — every ``<label>:`` the
doctrine reads back out of the PR body must be produced by a ``printf`` in an EARLIER fence, from a
variable that fence DERIVES. Non-vacuity: deleting the new ``printf 'pinned-base: %s\n'`` line reds
**1 of 38** — exactly ``test_every_carrier_line_the_doctrine_reads_back_is_printed_by_a_fence`` —
and the honest document is **0 red**.

*The deterministic half's skip-REASON axis was pinned per fence, not per run.* Phase 3 and the
post-revert re-check each run BOTH halves, so each carries two ``comm -13`` diffs and two ``-s``
halts; ``any()`` over the fence let the ``track_b`` copy satisfy the half-1 demand, and the
``track_b`` filter meant only half 2's pair was ever checked. The axis is parameterised by the junit
report a run WRITES now, so each half owes its own reasons, its own diff against ITS baseline, and
its own halt. Seven mutants measured against the honest document, each red on the strengthened check
and nothing else (the last also reds the half-2 parity check, which is the same defect seen from the
other side): delete Phase 3's half-1 ``comm -13 … > $B/new-skip-reasons``; delete its ``[ -s … ]``
halt; delete both; delete the post-revert half-1 halt; flip the diff to ``comm -23``; delete the
half-1 reasons producer; point half 1's diff at ``baseline-trackb.skipped-reasons``. Before the fix,
the first five measured **0 of 37 red** each.

The revision axis was re-measured at this head, not carried: ``9f8e12192`` **7 of 38** (the tightened
SKIP axis reds there too, because that document predates half 1's reason machinery), ``c7a772628`` /
``657a544a4`` **10 of 38**, ``4dc214c02`` **13 of 38**, ``7fb722a9a`` **16 of 38**, ``62c79b0b8**
**18 of 38**, ``a6a71c1e5`` / ``a4028e8d7`` **19 of 38**, ``a12792afe`` **21 of 38**,
``a17576c43`` **34 of 38**, ``f3c6c803b`` **31 of 38**, ``a8be548762`` **29 of 38**,
``295d6d385`` **27 of 38**, ``32a2dc87d`` / ``ba7f83feb`` / ``64ae70fc5`` **26 of 38**,
``ed1c03b82`` / ``c50628450`` **25 of 38**, ``a888c6980`` / ``aede4190f`` **24 of 38**, and
``c6d4c8278`` **3 of 38** — every one of them gains the producer check, because no pre-fix revision
printed ``pinned-base:``. Mutant figures quoted from EARLIER rounds were measured against the guard
as it then stood (37 checks) and are left as recorded; every revision count above was re-measured.

**10 more measured with the ``9f8e12192`` round, 10 killed** (each red on ONE check and on nothing
else): delete the gate fence's half-2 skip block; delete the baseline's half-2 skip recording; delete
the post-revert half-2 skip block; flip the half-2 count comparison to ``-ge`` (which refuses a clean
run and passes a dirty one); delete the half-2 reason diff — all five red
``test_the_track_b_half_is_completion_checked_like_the_deterministic_half``; unhalt the Phase-1
``git fetch origin main`` with ``|| true``, and the Landing fetch the same way (both red
``test_every_pin_reading_fence_halts_its_origin_main_fetch``, the fence-enumerated form this round's
sibling commit ``c6d4c8278`` introduced — the per-fence inline form was dropped in the merge rather
than kept twice); paste ``main`` in as the gate fence's asserted ref, and again in the post-revert
fence (both red ``test_every_full_suite_fence_names_the_tree_it_runs_on``); restore the prose "larger
than 4" (red ``test_no_hand_counted_literals_of_live_repo_properties``). The tree mutant is the one
that mattered most: the pre-fix check read only the PRESENCE of ``git rev-parse --abbrev-ref HEAD``,
and the branch name survived in the line's ``echo`` prose, so requiring the gate to run on ``main``
measured **0 of 37 red** — it certified exactly the failure the check was written for.

**13 more measured with the round-36 review (``c6d4c8278``), 13 killed, each red on exactly ONE
check.** The review's own claim that five mutants survived is re-measured rather than inherited: at
this head the half-2 pair (delete Phase 3's half-2 skip block; delete Phase 1's
``baseline-trackb.skipped`` write) ALREADY dies on
``test_the_track_b_half_is_completion_checked_like_the_deterministic_half`` — the per-fence
``_track_b_skip_axis_misses`` the merge kept from ``5f9efcfb2``/``f8db4dbdc`` closed it — and is
recorded, not re-fixed. What did survive, and is now killed: delete Phase 3's whole half-1 skip block,
and flip its comparison to ``-ge`` (both red
``test_a_skip_the_baseline_did_not_have_blocks_the_verdict``, which no longer ``continue``s past a
``batch-junit`` consumer with no comparison); degrade Phase 3's ``$RC`` predicate to ``-ge 0`` (red
``test_the_gate_run_must_complete_before_any_verdict``, now per fence instead of over the
concatenation of every gate's lines). Four more on the landing detector's new object: diff a remote
batch branch again, read the tested tree from the LIVE branch instead of the PR body, compare two
constants, and un-halt the detector's fetch — all red
``test_the_residual_landing_window_is_stated_and_detected`` — plus dropping the tested-tree capture
from the gate fence and from the post-revert re-check (red the same check, which now requires BOTH
full-suite-on-the-batch-branch fences to capture and print it). The ninth restores the
``needs no carrier`` claim and reds ``test_the_ledger_name_is_exported_not_merely_assigned``. Two of
the ``c7a772628`` round's 13 ("make the diff compare two constant files", "point the diff at
``origin/main`` vs ``$PINNED``") were written against the OLD spelling: the comparison is now
by value (``$LANDED_TREE`` vs ``$TESTED_TREE``), so they are re-measured in that shape above
(constants; and the read-from-the-live-branch mutant, which is the same fail-open the ref comparison
had).

**The round-36 ledger above was re-measured INDEPENDENTLY at the merged head, and its matrix was stale
by exactly one check.** A second lane implemented the same four findings and chose ``tested-tree`` by
VALUE independently (the merge kept that implementation and dropped the duplicate); its guards were
then re-run against a DIFFERENT mutant set — 12 measured, **12 killed**, each on exactly one check:
delete Phase 3's half-2 skip block and Phase 1's half-2 recording (both red the ``track_b`` check),
flip the half-1 comparison to ``-ge`` and delete its whole block (both red the half-1 skip check),
degrade the ``$RC`` predicate to ``-ge 0`` and delete the count comparison (both red the gate-status
check, per fence), delete the tested-tree capture from EITHER fence, read it from the LIVE branch,
compare two constants, and un-halt the detector's fetch (all red the residual-window check), and
restore the ``needs no carrier`` claim (red the ledger-name check). The same re-run found the header's
matrix understated ``9f8e12192`` (4 → **5**) and ``c6d4c8278`` (1 → **2**) by exactly the
``needs no carrier`` refusal this round added and did not fold in — the very class the round-35 review
nailed: a count recorded when a check landed, and never re-derived after the next one.

**The `7fb722a9a` review's P2 is recorded as MEASURED FALSE, not "fixed".** It reported that
``ci-introduced-failure`` and ``DEFERRABLE_LEGS`` "do not exist" and that the leg-table pointer is out
of range, measured against a ``python-ci.yml`` of **3791** lines. At this head that file is **4622**
lines and every clause of the doctrine holds: the job is declared at
``.github/workflows/python-ci.yml:4292`` (``if: always()``, ``actions: read``, ``GH_TOKEN``, and
"Reading the run's jobs and their step logs is the whole requirement"), it is in ``python-ci-gate``'s
``needs:`` (:4411) and in the gate's own ``LEGS_FILE`` table (:4496), ``DEFERRABLE_LEGS`` is declared
at :4435 (and ``changes`` is not in it), and the ``changes`` row the guard cites is :4484 (the
leg table's ``cat > "$LEGS_FILE" <<'LEGS'`` opener is :4483). Deleting those needles would have made
the partition claim false —
the defect the list was added to fix. A stale checkout, not a defect in the document.
**The surviving CLASS — and it is the reason #10434 exists.** Earlier rounds named a
single survivor; rounds 30-34 measured ~90 further mutants and the honest claim is
weaker than "one survivor": the guard decides SHELL CONTROL FLOW by pattern-matching
text, so any ONE-LINE edit that changes a subject's INPUTS rather than its wording
leaves a textually perfect halt bash-dead. Measured witness: inserting
``BASELINE_TOTAL=0`` between the fence's own ``[ "$BASELINE_TOTAL" -gt 0 ]`` guard and
the count halt makes the count halt dead (``bash`` prints CONTINUED) with the guard
**0 of 23 red**. The same treadmill continues one level up (``env BASELINE_TOTAL=0 …``,
``unset``, deleting the ``-gt 0`` line). No accumulation of spellings closes it; the
closure is to EXECUTE the fences under a stubbed PATH and assert the fence STOPS,
which is #10434. One instance of that class is still named explicitly below:
``git switch --detach -C <branch> "$PINNED"``, which git refuses outright (rc=128) and
whose ``|| exit 1`` halts — a predicate-tightness miss, not a broken invariant — and
the honest document is 0 of 23 red.

Two more classes belong to that list: an append-a-mover SUFFIX on any of
those lines — `git bisect reset <branch> && git switch main`, `git log --oneline -1
; git reset --hard main` — because naming the branch is not the same as HEAD ENDING
on it, so the return and the window's benign lines must BE one command (no separator
outside quoted spans). One survivor remains and is named rather than hidden:
`git switch --detach -C <branch> "$PINNED"`, which git refuses outright (rc=128) and
whose `|| exit 1` halts — a predicate-tightness miss, not a broken invariant. A
separator appended to the poll line, the merge line, the after-merge log line or the
revert line is the same class one fence over: every one of them must BE one command.

**5 more measured with the tree assertion and the bounded wait, 5 killed**, each red on ONE check
and on nothing else: insert `git switch main` between the tree assertion and the gate suite run
(the presence-only form lets that through — the guard reads the run, not the name), delete the tree
assertion, delete the bounded wait, replace the bounded loop
with a single `sleep 900` (unbounded in practice — the bound is what makes the wait finite), and
move the wait AFTER the poll it exists to protect. The first of those is the one a presence-only
check lets through, which is why the tree check refuses a MOVER between the name and the run rather
than merely requiring the name.

**The review at ``aa1cd92d4`` added two COMMAND defects and their guards, each measured red on the
pre-fix text.** (1) The blame sha was read from ``HEAD`` after ``git bisect run``, which leaves HEAD on
the LAST PROBE it tested — the ref that holds the answer is ``refs/bisect/bad``, reproduced here in a
scratch repo against this doctrine's own ``--no-ff`` shape (5 merges, culprit PR #4, ``HEAD`` =
``batch: merge PR #3``, and both are merges, so the merge-ness assert passed on the wrong one).
(2) The gate's status was taken with ``wait`` in a later shell — the poll's own requirement — where the
pid is not a child: measured ``bash: wait: pid N is not a child of this shell``, rc=127, so a
COMPLETED green run was declared ``UNPROVEN-FULL-SUITE``. The run's own shell now persists ``$?``.
Measured by mutating the honest document back to each pre-fix form: the ``HEAD`` capture reds 2 checks
and the ``wait`` form reds 1; a stale un-cleared status file, and a poll reading a file no one writes,
red 1 each; the honest document is 19 of 19 green.

Static, no network / DB / graph — but the MODULE still loads `tests/conftest.py`, which fails the
session closed when no lane is exported (`_p4_uri_required` → `_assert_p4_uri_required`). Running
this file with a bare `pytest tests/test_batch_integration_doctrine.py` therefore reports ONE
ERROR PER CHECK, and none of them has anything to do with the doctrine — `conftest.py` declares
that fixture `scope="session", autouse=True`, so pytest re-raises the session-fixture failure per
item rather than once. The count is deliberately NOT stated as a number (#10400 review P3): the
number is a function of how many checks this file has, so any literal is stale the moment a
check lands — this sentence said `16` while the module had 18, and a bare bump to 18 re-stales
the same way. `test_the_header_does_not_hard_code_the_bare_run_error_count` keeps this sentence
count-agnostic. Name a lane:

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_batch_integration_doctrine.py -q

(CI is unaffected: the `test` job runs `core`/`tier1` under the COMPUTED lane —
`TORTOISE_DB_URI` only on the full/push arm, `TORTOISE_TEST_CARVE_OUT=1` with no URI
on every PR run (`.github/workflows/python-ci.yml:1013-1034`, wired at `:1537-1542`) —
so this module must stay lane-agnostic: no DB, no URI, no graph. A docs-only PR is
`full=false`, i.e. this file's own load-bearing `tier1` run is URI-less.)

**Round 38 — four findings on one head, and the check count moved 38 → 39.** Every one was a place
where the doctrine was WEAKER than the thing it stands in for, so each fix is the doctrine asking the
thing itself instead of re-spelling it, plus the check that would have caught the re-spelling.

*The lane assertion was weaker than the gate it stands in for.* The three suite fences carried
``[ -n "${TORTOISE_DB_URI:-}" ] || [ -n "${TORTOISE_TEST_CARVE_OUT:-}" ]``, and
``tests/_embedded.py::_assert_p4_uri_required`` accepts neither half of that as written: it tests
``_uri_set_supported()`` (``is_db_uri`` over ``SUPPORTED_URI_SCHEMES`` — docker/redis/rediss) OR
``_carve_out_opted_in()`` (``is_truthy`` — 1/true/yes/on). So a set-but-unsupported ``bolt://`` URI
(this repo's own AGENTS.md documents it as a legal value) and ``TORTOISE_TEST_CARVE_OUT=0`` both PASSED
the halt and then ERRORed every collected item at setup, exiting 1 — the status COMPLETED accepts — and
the verdict was ``BATCH-GREEN`` for a tree on which nothing ran. Settled rather than read: ``bolt://``
+ ``=0`` → ``_uri_set_supported() = False``, ``_carve_out_opted_in() = False``, i.e. the fence passed
and the suite refused the session. All four suite fences now invoke the gate's own two predicates
(``uv run python -c '… from tests._embedded import …'``), so the scheme list and the truthy vocabulary
have ONE home and cannot drift from the gate.

*The two Phase-4 blame runs carried no lane assertion, and the grader graded a no-lane session
``bad``.* Phase 4 is the LATEST shell of all, and pytest gives "nothing ran" and "this commit is red"
the same status (1), so every probe read BAD and ``git bisect run`` convicted the OLDEST merge — the
doctrine's own measured failure, one paragraph above it — and the next step drops that PR. Both blame
fences now assert the lane and halt, and the probe captures its output and declines to convict on the
gate's own message (``exit 125``), which is git's documented protocol (``make || exit 125``). The
population was excluded by SPELLING, not judgement — ``_SUITE_CMD``'s ``(?!\\s+<)`` refuses the
placeholder form and ``git bisect run`` does not start with ``uv`` — so the new
``test_the_blame_probes_assert_the_lane_and_decline_to_convict_an_unrunnable_probe`` enumerates a
``_BLAME_PROBE`` population of its own, and pins the decline's needle against ``tests/_embedded.py``
so it cannot match a string no run can print.

*The bounded wait was SHORTER than the run it bounds.* ``240 × 15 s = 60 min``, described as the wedge
bound, against a command that runs the union of CI's pools in ONE process (no xdist, no leg
partition): ``test (a…i)``'s p50s serialise to ≈91–134 min, ``test-slow`` is 37.05 min of measured
test time, ``test-carve-out`` is 8.3–8.6 min × 3 shards ≈ 25 min — ≈153–196 min before collecting
41.7k tests. CI gets ~37 min of WALL clock out of that only by sharding across runners, and Phase 1
runs the byte-identical command with NO bound at all, so the same command cannot be expected to
finish in Phase 1 and not in Phase 3. The bound is 360 × 60 s = 6 h (≈1.8× the worst documented
composition) in both fences.

*The wait's expiry was read as the run's END, and it is not.* The run is detached and outlives the
shell, so re-running the fence ``rm -f``'d the status of a suite that was still EXECUTING, and the two
runs then shared this batch's ``$B`` — whichever finished first owned the ledger. That is the
per-batch-ledger collision the doctrine closes BETWEEN batches, one level down inside one batch, where
no directory namespacing can separate them. Both fences now refuse to restart while ``$B/batch.log``
was written in the last 10 minutes, and say to wait for the status (and to kill the suite first if it
really is wedged).

**Non-vacuity, measured against THIS guard, each red on ONE check and on nothing else:** restoring the
``-n`` disjunction at Phase 3 reds 1 of 39 (the lane check); deleting the Phase-4 manual fence's lane
assertion reds 1 of 39, and the bisect fence's reds 1 of 39 (the new blame check); restoring the
pre-fix grader (``>/dev/null 2>&1``, no decline) reds 1 of 39 (the new blame check);
``seq 1 1 … sleep 15``, ``seq 1 240 … sleep 15`` (the pre-fix 60 min) and ``seq 1 240 … sleep 1`` each
red 1 of 39 (the bounded-wait check); deleting both live-run refusals reds 1 of 39 (the same check).
Deleting the POST-REVERT fence's assertion reds 2 of 39 — its own check and the lane check — because
that fence is the one the verdict is read from. Two HONEST re-spellings of the bound stay green
(``120 × 300``, ``720 × 30``), so the check grades the arithmetic rather than the spelling.

**The revision axis, RE-MEASURED at 39** (not carried — the previous matrix was taken at 38):
``0a9ee9050`` (this round's review head) **4 of 39**; ``9f8e12192`` **11 of 39**; ``c6d4c8278``
**7 of 39**; ``c7a772628`` / ``657a544a4`` **13 of 39**; ``4dc214c02`` **16 of 39**; ``7fb722a9a``
**18 of 39**; ``62c79b0b8`` **19 of 39**; ``a6a71c1e5`` / ``a4028e8d7`` **20 of 39**; ``a12792afe``
**22 of 39**; ``a17576c43`` **35 of 39**; ``f3c6c803b`` **32 of 39**; ``a8be548762`` **30 of 39**;
``295d6d385`` **28 of 39**; ``32a2dc87d`` / ``ba7f83feb`` / ``64ae70fc5`` **27 of 39**;
``ed1c03b82`` / ``c50628450`` **26 of 39**; ``a888c6980`` / ``aede4190f`` **25 of 39**.
The deltas against round 37 are NOT uniform, which is the point of re-measuring: every pre-fix
revision gains the new blame check (+1), the bounded-wait check now also demands the live-run refusal
(+1 where its fence had the old bound), and the tightened ``_LANE_ASSERT`` reds the lane check — and,
where that revision's post-revert fence used the ``-n`` spelling, the post-revert check too (+2). A
revision already red on one of those gains nothing from it, so the observed deltas are +1..+4 and the
review head ``0a9ee9050`` reds on exactly the FOUR defects this round fixed (lane predicate, blame
probes, bound, live-run refusal) and on nothing else — which is the cleanest statement of the
round's scope this file can make.

**Round 39 — two P3s, one of them a defect in THIS GUARD, and the check count moved 39 → 41.** The
review found no defect in a doctrine COMMAND this round. Both findings were about whether the guard
and the doctrine hold the #10399 invariant they claim, and the second grew one more check from a
defect the first fix introduced and executing the new fence exposed.

*Finding 1 — the partition claim was pinned as a MENTION.* ``test_the_unreproduced_gate_legs_are_named``
asserted ``needle in text`` over the whole doctrine, and every one of the five non-test members also
appears a second time **inside the same bullet** (the paragraph about which of them declare
``needs:``), so the reviewer replaced the entire enumeration with prose and measured **39 passed** —
the list that exists to stop a gate member going unmentioned could lose all five silently. Measured
here on the pre-fix check before fixing it, at ``97f413e53``: same mutant, **0 red**. The needles are
now scoped by :func:`_sentence_with` (the sentence that carries ``non-test legs``) and
:func:`_bullet` (the two TEST legs), and a bullet scope alone was rejected with a measurement — that
paragraph is in the SAME bullet, so a bullet-scoped needle survives the same mutant.

*Finding 2 — the verdict's one CONVICTING input was prose.* Phase 4 classified
``BATCH-GREEN(BASELINE(<ids>))`` / ``BATCH-RED(<ids>)`` on a diff of failure ids, named the four
junit reports, and built none of the sets: ``grep -n '<failure' docs/ops/batch-integration.md``
returned nothing and the only ``comm`` lines in the section were the four skip-REASON diffs. So the
step that decides whether a PR is DROPPED was a spelling. Phase 4 now builds the ledger — one stated
rule, the four reports, three ``comm`` set operations into ``$B/new-failures`` /
``$B/baseline-still-failing`` / ``$B/cleared-failures``, and a fail-closed halt when a report whose
own ``failures=``/``errors=`` are non-zero yields NO id. The rule is the report SCHEMA's, not this
document's convenience: pytest prints the xsd it targets, ``<testcase>`` with ``name`` required and
``classname`` optional and children ``<skipped>?``/``<error>*``/``<failure>*``/``<system-out>*``/
``<system-err>*`` (<https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files>),
and ``classname`` is overridable per case with ``record_xml_attribute`` — so the child ELEMENT, never
the adjacent text, makes a case failing, and the id is read off the report rather than re-derived. It
is parsed as XML because the whole document lands on ONE line (measured: a real 6-test report has
``<testcase …/>``, ``<testcase …><failure …></failure></testcase>`` and every other case on line 2),
so the grep/awk pairing this could have been written as would have extracted NOTHING forever.
**The fence was then EXECUTED**, which is how the round's third defect was found: its own halt
messages carried markdown backticks, so ``echo "… every `track_b` id …"`` RAN ``track_b``
(measured: ``run.sh: line 7: track_b: command not found``) — ``bash -n`` sees a perfect command
substitution. That is now ``test_no_fence_command_executes_a_command_from_its_message``, a 41st check
(``$(…)`` stays legal: the tree assertion's message interpolates it on purpose, and a trailing ``#``
annotation is cut by ``_code`` so the honest re-spelling is green — measured).

*Two existing checks were scoped by MENTION and made an honest fence inexpressible.*
``test_the_baseline_report_is_cleared_and_its_status_checked`` and
``test_a_skip_the_baseline_did_not_have_blocks_the_verdict`` selected their fence population with
``any("baseline-junit" in c)`` / ``any("batch-junit" in c)``, so the new Phase-4 fence — which READS
those reports and runs nothing — was required to carry the baseline's clear, status capture and
0/1 predicate, and TWO suite runs of its own. Measured: the first cut of the ledger fence reddened
them with *"no baseline suite command writes the report"*, about a fence that runs nothing. Both are
now scoped by ROLE (a suite command that writes THAT report), which is the subject-vs-mention class
this module already closed for ``_COUNT_SUBJECT``/``_MERGE_SUBJECT``/``_LANDED_TREE_DIFF``; both
still kill their own mutants (see the ledger below).

**Non-vacuity, measured at THIS head (41 checks, 0 red), each red on ONE check unless stated.** The
reviewer's own mutant — the five-member enumeration replaced by prose — reds **1 of 41**
(``test_the_unreproduced_gate_legs_are_named``), and so does dropping one member, dropping the two
TEST legs, and dropping ``DEFERRABLE_LEGS``. On the ledger: deleting the ``comm -13`` line, flipping
it to ``comm -23``, deleting an absent-report halt, deleting the fail-closed block, deleting the
stated rule, rewriting its ``::`` separator, replacing the XML extraction with a ``grep``, and
deleting the ``baseline-junit.xml`` source each red **1 of 41**
(``test_the_failure_id_ledger_is_built_by_a_stated_rule``). Letting the post-revert re-check stop
rewriting ``$B/batch-trackb.xml`` reds **3 of 41** (that check, the ``track_b`` completion check and
the skip check — one run's instrument removed, seen from three sides). A backtick re-introduced into
a halt message reds **1 of 41** (the new message check); a backtick in a trailing annotation stays
GREEN, as does a reflowed enumeration.
**One survivor, and it is the documented CLASS, not this round's**: ``[ … ] || : || { … exit 1; }``
— defusing the fail-closed halt with an always-0 segment — leaves the text perfect and the halt
bash-dead, exactly like the ``BASELINE_TOTAL=0`` witness above. That is #10434's class (execute the
fences under a stubbed PATH), which no accumulation of spellings closes, and it is recorded rather
than hidden.

The fence's SEMANTICS were reproduced rather than asserted, on real pytest reports (the rule was run
end to end with the four files in a scratch ``$B``): a disjoint baseline → the batch's three ids
(``test_fail``, ``test_param[2]``, ``test_error``; the passing cases and the parametrized PASS are
excluded) land in ``new-failures``, main's own red in ``cleared-failures``; an identical baseline →
``new-failures`` EMPTY and all three in ``baseline-still-failing`` (the ``BASELINE(<ids>)`` row); a
report with ``failures="1"`` and no ``<failure>`` child → the fail-closed halt fires with
``UNPROVEN-FULL-SUITE(<lane>)``; a missing report → its own halt.

**The revision axis, RE-MEASURED at 41** (every count is round 38's **+1**, the new ledger check, and
nothing else — the scoped leg check and the message check are green on every pre-fix document, which
is exactly why finding 1 needed a MUTANT and not a revision): ``97f413e53`` (this round's review
head) **1 of 41** — the missing ledger alone; ``0a9ee9050`` **5**; ``9f8e12192`` **12**;
``c6d4c8278`` **8**; ``c7a772628`` / ``657a544a4`` **14**; ``4dc214c02`` **17**; ``7fb722a9a`` **19**;
``62c79b0b8`` **20**; ``a6a71c1e5`` / ``a4028e8d7`` **21**; ``a12792afe`` **23**; ``a17576c43`` **36**;
``f3c6c803b`` **33**; ``a8be548762`` **31**; ``295d6d385`` **29**; ``32a2dc87d`` / ``ba7f83feb`` /
``64ae70fc5`` **28**; ``ed1c03b82`` / ``c50628450`` **27**; ``a888c6980`` / ``aede4190f`` **26**.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "ops" / "batch-integration.md"

#: The three exclusions, required as *terms* rather than as one literal string —
#: a reordered ``-m`` must pass, a missing exclusion must not (the selector's
#: meaning, not its spelling).
EXCLUSION_TERMS = ("not track_b", "not live", "not integration")

#: The positive marker of the other half. CI runs it as ``test-track-b``, a
#: ``needs:`` of the required ``python-ci-gate``.
TRACK_B_FILTER = "-m track_b"

#: The landing-time base check: compare ``origin/main`` to the pinned sha.
_PINNED_MAIN_ASSERTION = re.compile(
    r'\[\s*"\$\(git\s+rev-parse\s+origin/main\)"\s*=\s*"\$PINNED"\s*\]'
)

#: A refresh of ``origin/main``, halting or not — the two are NOT interchangeable, and the pre-fix
#: locator could only see one of them: `re.fullmatch(r"git\s+fetch\s+origin(?:\s+main)?", …)`
#: matched the BUG (a bare fetch) and DROPPED THE FIX, so halting the fetch — the repair the review
#: asks for — red the very check that demanded a fetch. Split into shape and halt, so ordering can be
#: asserted on every fetch while the halt is asserted on its own.
_MAIN_FETCH = re.compile(r"^git\s+fetch\s+origin(?:\s+main)?(?=\s*(?:\|\||$))")
_MAIN_FETCH_SUBJECT = re.compile(r"^git\s+fetch\s+origin(?:\s+main)?$")


def _is_main_fetch(code: str) -> bool:
    """Is this command a refresh of ``origin/main`` (halting or not)?"""
    return bool(_MAIN_FETCH.match(_code(code)))


def _main_fetch_halts(code: str) -> bool:
    """Is this the ``origin/main`` refresh as ONE command that HALTS on failure?"""
    return _is_main_fetch(code) and _halts(_code(code), subject=_MAIN_FETCH_SUBJECT)

#: Commands that may share a line with a pin create without touching the tree it
#: checked out. Anything else must BE a re-runnable pin create (see
#: ``_ends_at_halt``) — an allowlist, because a hard reset moves the tree without
#: matching ``_MOVES_HEAD``.
#: ``cd`` is limited to the no-op forms: ``cd /other-worktree`` would relocate the
#: fence, and worktrees share an object DB, so the pin then succeeds in another repo.
#: The count halt's own subject: the run reported AT LEAST the baseline's tests.
_COUNT_SUBJECT = re.compile(
    r'\[\s+"\$BATCH_TOTAL"\s+-ge\s+"\$BASELINE_TOTAL"\s*\]'
)
#: The merge-ness assert's own subject — the ASSERTION only; `_MERGE_ASSERT` is the
#: whole line and ends in `||`, so it cannot be the pre-halt segment.
#: Same tolerance as `_MERGE_ASSERT` (`"?\$\{?BAD\}?"?`): a selector wider than its
#: own subject refuses honest `${BAD}`/`$BAD` lines and blames the halt.
_MERGE_SUBJECT = re.compile(
    r'\[\s+-n\s+"\$\(\s*git\s+rev-list\s+--no-walk\s+--merges\s+'
    r'"?\$\{?BAD\}?"?\s*\)"\s*\]'
)
#: The landing pin guard's own subject: `$PINNED` is non-empty.
_LANDING_PIN_GUARD = re.compile(r'\[\s+-n\s+"\$PINNED"\s*\]')

_SAFE_SEGMENT = re.compile(
    r"^(?:git\s+fetch\b|cd\s+(?:\.|-)(?=\s|$)|echo\b|printf\b|true\b|:)\s*"
)

#: The one definition prefix set — every declarator bash accepts. Used for the
#: baseline pin AND the landing pin, so `readonly PINNED=…` cannot slip past one of
#: them.
_PIN_DEF = re.compile(
    r"^(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)*|typeset\s+(?:-\w+\s+)*)?PINNED="
)

#: The argv a suite run may carry. `pytest tests/ --co` exits 0 having collected
#: nothing and `--lf` exits 0 having run one test, so an UNPINNED argv certifies a
#: green on a tree no run has seen — the document's one non-negotiable rule.
_SUITE_DIR_TARGETS = {"tests", "tests/", "./tests/"}
_SUITE_FILE_TARGETS = {"tests/test_ingest_safety.py"}

#: The ONE path exclusion a `tests/` run may (and must) carry. CI's deterministic lanes do not run
#: `tests/` — they run a FILE LIST, and `tools/ci_selection.py` skips the `tests/e2e/` prefix by path
#: (#1349) because those browser tests *"only ever ERROR here"* without browsers. So the marker
#: filter alone does NOT make a batch run comparable to CI: the POPULATION has to match too, and
#: `--ignore=tests/e2e` is the only argv that moves a bare `pytest tests/` toward that population.
#: `--co`/`--lf`/`-k`/`--deselect` move it away and stay refused.
_SUITE_DIR_EXCLUSIONS = ("tests/e2e",)

#: The VERB, with no opinion about the target. Every population this module
#: quantifies over ("every fence that runs the full suite", "every fence that runs
#: both halves") is derived from THIS: keying them on the target instead let one
#: repointed token remove a fence from the population that must refuse a dirty tree.
_SUITE_CMD = re.compile(r"^(?:\(\s*)?uv\s+run\s+pytest\b(?!\s+<)")  # not the <placeholder> bisect leg

#: ONE ledger-artifact path, as the doctrine spells it. Since the #10400 P2 (host-global ledger) every
#: verdict-bearing artifact lives under a per-batch directory, so a path is either that directory's own
#: `$B/<file>` (optionally braced/quoted) or a literal `/tmp[/<dir>]/<file>`. Pinning `/tmp/<file>` alone
#: made the honest fix INEXPRESSIBLE — measured: namespacing the launch and the poll alone reds two
#: checks — which is the same "the guard refuses its own remedy" shape this file has fixed three times.
_LEDGER = r'(?:\$\{?B\}?/[\w.-]+|/tmp/[\w./-]+)'

#: The status wrapper that makes a DETACHED run's exit status survive the shell boundary. `wait`
#: cannot: the poll happens in a LATER shell, where the pid is not a child (measured, bash 5.2:
#: `bash: wait: pid N is not a child of this shell`, rc=127) — a completed, green run then read as
#: UNPROVEN with a kill diagnosis printed. The run's OWN shell writes `$?` to a file instead.
_STATUS_WRAPPER = re.compile(
    r"^\(\s*(?P<suite>uv\s+run\s+pytest\b.*?)\s*;\s*echo\s+\$\?\s*>\s*\"?(?P<rc>"
    + _LEDGER
    + r')\"?\s*\)\s*&$'
)

#: The two deliberate halves, as term MULTISETS. Order-free (a reordered `-m`
#: passes) but exact — `… and not slow` still *contains* the three exclusion terms
#: while silently deselecting a real marker.
_HALF1_TERMS = ("not", "track_b", "not", "live", "not", "integration")
_HALF2_TERMS = ("track_b",)


def _suite_argv_ok(cmd: str) -> bool:
    """Is this suite command `uv run pytest <target> [-q] [-m FILTER] [--junitxml=…] [--ignore=tests/e2e]`?

    The path exclusion is allowed ONLY on the `tests/` half and is REQUIRED there: that half is the
    one a bare `pytest tests/` population inflates with `tests/e2e/`, which no CI lane runs.
    """
    wrapped = _STATUS_WRAPPER.match(cmd.strip())
    if wrapped is not None:
        # The detached form: the argv check must see through the status writer, or the gate command
        # reads as "unpinned argv" and the population silently loses its one real run.
        cmd = wrapped.group("suite")
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    # Redirections are removed from the WORD LIST, not from the tail: bash keeps
    # every other word in argv (`printf '<%s>' A B > out 2>&1 C` prints A B C), so
    # truncating at the first `>` let `… > /tmp/batch.log 2>&1 --co &` through as a
    # real `--co`.
    argv: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == ">":
            if i + 1 >= len(tokens):
                return False
            i += 2  # the redirect AND its target — neither is an argument
            continue
        if token in {"2>&1", "2>/dev/null"}:
            i += 1
            continue
        if token == "&":
            if i != len(tokens) - 1:
                return False
            i += 1
            continue
        argv.append(token)
        i += 1
    tokens = argv
    if tokens[:3] != ["uv", "run", "pytest"]:
        return False
    target = None
    filters: list[str] = []
    rest = tokens[3:]
    i = 0
    ignores: list[str] = []
    while i < len(rest):
        token = rest[i]
        if token == "-q":
            i += 1
        elif token == "-m" and i + 1 < len(rest):
            filters.append(rest[i + 1])
            i += 2
        elif token.startswith("--junitxml=") and token != "--junitxml=":
            i += 1
        elif token.startswith("--ignore=") and token != "--ignore=":
            # The ONE admitted selector — see `_SUITE_DIR_EXCLUSIONS`. It NARROWS the run toward
            # CI's population, which is the opposite of what `--co`/`--lf`/`-k` do.
            ignores.append(token.split("=", 1)[1])
            i += 1
        elif token in _SUITE_DIR_TARGETS | _SUITE_FILE_TARGETS and target is None:
            target = token
            i += 1
        else:
            return False
    if target is None or len(filters) != 1:
        # pytest honours the LAST -m, so a second one can deselect the whole run.
        return False
    terms = tuple(term for term in shlex.split(filters[0]) if term != "and")
    if sorted(terms) == sorted(_HALF2_TERMS):
        # One file, no directory prefix — an e2e exclusion here would be a no-op that reads as
        # diligence, so it is refused rather than ignored.
        return target in _SUITE_FILE_TARGETS and not ignores
    if sorted(terms) != sorted(_HALF1_TERMS):
        return False
    return target in _SUITE_DIR_TARGETS and sorted(ignores) == sorted(_SUITE_DIR_EXCLUSIONS)


#: The only honest shape of the gate's poll line: it must read the STATUS FILE the run's own shell
#: wrote. A `kill -0` probe answers "is the pid gone", which is the same answer for a finished run and
#: for an OOM-killed one; `true && echo DONE` answers nothing at all.
_RC_POLL = re.compile(
    r"^\[\s*-f\s*\"?"
    + _LEDGER
    + r"\"?\s*\]\s*&&\s*echo\s+(?:'[^']*'|\"[^\"]*\")\s*"
    r"\|\|\s*echo\s+(?:'[^']*'|\"[^\"]*\"|\S+)$"
)


def _status_head(var: str) -> re.Pattern[str]:
    """The COMPLETED-run predicate: only 0 and 1 are a finished pytest run.

    Named per variable so the same pin applies to the gate's `$RC` and to the baseline's
    `$BASELINE_RC` — one shape, two carriers, because a predicate that accepts 137
    (SIGKILL/OOM) or 2 (interrupted) certifies a KILLED run as COMPLETED.
    """
    return re.compile(
        rf'^\[\s*"\${var}"\s+-eq\s+0\s*\]\s*\|\|\s*\[\s*"\${var}"\s+-eq\s+1\s*\]\s*\|\|'
    )


#: The halt behind `_status_head`: the last thing on the line, and a `{…}` body may hold only
#: echo/printf segments before its final non-zero `exit` (so `{ (exit 1); }` — a CHILD exit — and a
#: decoy group inside a message are both refused).
_STATUS_TAIL = re.compile(
    r"^\s*(?:\{\s*(?:(?:echo|printf)\s+[^;{}()]*;\s*)*"
    r"exit\s+[1-9]\d*\s*;?\s*\}|exit\s+[1-9]\d*)\s*$"
)

#: The BASELINE total's carrier. `$BASELINE_TOTAL` is computed in Phase 1's fence and consumed in
#: Phase 3's, and Phase 3 may be a LATER shell (the same reason the run's status and `$PINNED` are
#: carried in files): there `[ "$BATCH_TOTAL" -ge "$BASELINE_TOTAL" ]` compares against the empty
#: string, bash rejects it (rc=2), and the count halt reports a COMPLETE, green run as SHORTER than
#: the baseline. The producer is a `printf … > /tmp/<name>` of `$BASELINE_TOTAL`; the consumer binds
#: `BASELINE_TOTAL` back with `cat`. A carrier that is neither written nor cleared is the same defect
#: as a status file the poll never wrote.
_CARRIER_WRITE = re.compile(
    r'^printf\s+\S+\s+"?\$BASELINE_TOTAL"?\s*>\s*"?(?P<path>' + _LEDGER + r')"?$'
)
_CARRIER_BIND = re.compile(
    r'^BASELINE_TOTAL="?\$\(\s*cat\s+"?(?P<path>' + _LEDGER + r')"?\s*\)"?$'
)
_CARRIER_TEST = re.compile(r'^\[\s*-s\s+"?(?P<path>' + _LEDGER + r')"?\s*\]')


#: Every path an `rm -f` in a fence names, with surrounding quotes STRIPPED. The doctrine quotes its
#: per-batch paths (`rm -f "$B/batch.rc"`), and a naive `.split()` leaves the quote on the token, so a
#: cleared-set comparison silently misses the file it is supposed to prove was cleared.
_CLEAR = re.compile(r"rm\s+-f\s+(.*)$")


def _cleared(codes: list[str]) -> set[str]:
    """The paths a fence's `rm -f` lines name (quotes stripped).

    ``\\``-CONTINUED lines are JOINED first. A fence may split one ``rm -f`` across two lines —
    ``rm -f a b \\`` / ``      c d`` — which bash reads as ONE command and a per-line scan reads as a
    clear that names only half its paths (measured on ``c6d4c8278``: the baseline's half-2 skip
    carriers were cleared exactly that way, and the skip axis reported them as never cleared — the
    guard refusing its own honest remedy, one spelling over).
    """
    joined: list[str] = []
    pending = ""
    for code in codes:
        if code.rstrip().endswith("\\"):
            pending += code.rstrip()[:-1] + " "
            continue
        joined.append(pending + code)
        pending = ""
    if pending:
        joined.append(pending)
    return {
        token.strip("\"'")
        for code in joined
        for match in _CLEAR.finditer(code)
        for token in match.group(1).split()
    }

#: The baseline fence's own report, and the two halves of the same rule the gate already applies:
#: clear it (pytest truncates a junit file only in `pytest_sessionfinish`, so a killed run leaves the
#: PREVIOUS report at the path) and refuse anything but a COMPLETED status.
#: The baseline fence's own report, identified by its BASENAME — so the guard accepts both the bare
#: `/tmp/baseline-junit.xml` and the per-batch `$B/baseline-junit.xml` the doctrine uses. Pinning the
#: whole `/tmp/...` path here is what made the per-batch fix inexpressible.
_BASELINE_JUNIT = "baseline-junit"
_BASELINE_RC_CAPTURE = re.compile(r'^(?:BASELINE_RC|RC)=\$\?$')
_BASELINE_RC = "BASELINE_RC"

#: The dirty-tree subject: the command whose failure must reach the halt.
_DIRTY_TEST = re.compile(r"^(?:\[\s*-z\s|test\s+-z\s)")

#: Commands that move HEAD onto a ref — what Phase 1 must do before its suite run.
_MOVES_HEAD = re.compile(r"\bgit\s+(?:switch|checkout)\b")

#: A lane SELECTION that actually selects, never merely a mention of one. Two
#: mechanisms, both executable: the literal ``export TORTOISE_DB_URI=…``, or
#: ``lane="$(… tools/test_lane.py uri)"`` followed by ``eval "$lane"`` (the form
#: Phase 0 uses since #5084 made a private container the default). ``_fences()``
#: drops whole-line comments and ``_code()`` strips trailing ones, so naming an
#: export in prose or in an annotation still cannot satisfy this — the property
#: the ``P1-4 / ISSUE 3`` fix was written for.
_LANE_LITERAL = "export TORTOISE_DB_URI="
_LANE_CAPTURE = re.compile(
    r'^(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)*|typeset\s+(?:-\w+\s+)*)?'
    r'(?P<var>\w+)="?\$\(\s*uv\s+run\s+python\s+tools/test_lane\.py\s+uri\s*\)"?'
    # the trailing halt is optional for MATCHING but the doc carries it: a failed
    # `uri` left the fence running with `$lane` empty (`eval ""` selects nothing).
    r'(?:\s*\|\|\s*(?:exit\s+[1-9]|\{[^}]*exit\s+[1-9][^}]*\}))?\s*$'
)
_LANE_EVAL = re.compile(r'^eval\s+"?\$\{?(?P<var>\w+)\}?"?$')

#: The batch lane's ledger must be PRIVATE by default (#5084): a shared instance is
#: contaminated by peer lanes while the batch runs, and the batch's id lists ARE
#: the ledger every verdict is diffed against.
_LANE_SHARED = "localhost:6379"

#: The private-container tool. Pinned in the FENCE, never in the prose.
_TEST_LANE = "tools/test_lane.py"


def _lane_exports(fence: list[str]) -> list[int]:
    """Indices of the commands in ``fence`` that EXPORT a lane (executable only)."""
    codes = [_code(c) for c in fence]
    found: list[int] = []
    for i, code in enumerate(codes):
        if code.startswith(_LANE_LITERAL):
            found.append(i)
            continue
        capture = _LANE_CAPTURE.match(code)
        if not capture:
            continue
        var = capture.group("var")
        if any(
            (match := _LANE_EVAL.match(codes[j])) and match.group("var") == var
            for j in range(i + 1, len(codes))
        ):
            found.append(i)
    return found


#: A single- or double-quoted span. Blanked (same length, so offsets survive)
#: before a halt is matched: `|| { echo "main moved — exit 1 and re-baseline" }`
#: returns `echo`'s 0 and exits nothing, but the raw text contains `exit 1`.
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def _unquoted(code: str) -> str:
    """``code`` with every quoted span blanked to the same length."""
    return _QUOTED.sub(lambda match: "-" * len(match.group(0)), code)


def _code(line: str) -> str:
    """A fenced line's COMMAND portion — the inline comment stripped first.

    ``_fences()`` drops lines that BEGIN with ``#``, but a trailing ``# ...``
    survives, and a spelling check run against the raw line is therefore
    satisfiable by an annotation rather than by the command
    (``git switch -c … || exit 1   # use switch -C next time`` passed).
    """
    stripped = line.rstrip()
    # Cut at the first `#` that is OUTSIDE a quoted span. A quote-blind cut truncated
    # the batch merge line at `-m "batch: merge PR ` — the `#<N>` inside the message —
    # and every suffix after it was invisible to every predicate built on `_code`.
    match = re.search(r"(?:^|\s)#", _unquoted(stripped))
    if match:
        stripped = stripped[: match.start()].rstrip()
    return stripped


#: A variable bound to the REF that holds the bisect result — `BAD="$(git rev-parse refs/bisect/bad)"`.
#: ⛔ NOT `git rev-parse HEAD`: `git bisect run` leaves HEAD on the LAST PROBE it tested (measured over
#: 3/5/7/8/13-merge `--no-ff` batches, 40 cases: HEAD diverged from the culprit in 27, always to the
#: merge immediately BEFORE it), and it is `refs/bisect/bad` that git's manual names as the result.
#: `git bisect reset` deletes the ref, so the capture must precede it. It is ONE command and it cannot
#: move HEAD, so the phase-4 window may carry it without loosening "benign lines must BE one command".
_READ_ONLY_CAPTURE = re.compile(
    r'^[A-Za-z_]\w*="\$\(\s*git\s+rev-parse\s+refs/bisect/bad\s*\)"$'
)

#: The merge-ness assertion on the blame sha. `git revert -m 1` on a NON-merge IGNORES the
#: mainline and exits 0, so a bisect result that is a commit inside a PR silently reverts ONE
#: COMMIT of that PR — the rest of it still lands, and the re-blame rule cannot see it because
#: the subject is `Revert "PR commit 2"`. This line is the mechanical check, and it must halt.
_MERGE_ASSERT = re.compile(
    r'^\[\s*-n\s+"\$\(\s*git\s+rev-list\s+--no-walk\s+--merges\s+"?\$\{?BAD\}?"?\s*\)"\s*\]\s*\|\|'
)


def _is_blame_binding(code: str) -> bool:
    """A READ-ONLY line binding/asserting the blame sha (the phase-4 window allows these)."""
    return bool(_READ_ONLY_CAPTURE.match(code) or _MERGE_ASSERT.match(code))


# A halt must be REACHABLE from the failing subject, which the text does not carry:
# `{ …; (exit 1); }` exits a CHILD (these fences have no `set -e`), and a decoy group
# before the real halt leaves it as dead code. So the halt is the LAST thing on the line
# and a `{…}` body holds only plain segments before its final non-zero `exit` — no
# `(`/`)`, `|`, `&`, nested braces, and nothing after the `exit`. Measured: this is the
# difference between `{ echo g; exit 1; }` (halts) and `{ echo g; (exit 1); }` (continues).
_REACHABLE_HALT = re.compile(r"\|\|\s*(?:(?P<grp>\{[^}]*\})|exit\s+[1-9]\d*)\s*$")
_GROUP_BODY = re.compile(r"\s*(?:[^;{}()|&]*;\s*)*exit\s+[1-9]\d*\s*;?\s*$")


def _reachable_code(code: str) -> str:
    """``code`` with quoted spans blanked — a `(` inside a MESSAGE is not a subshell."""
    return _unquoted(code)


def _halts(code: str, subject: object | None = None) -> bool:
    """Does this command stop the fence on failure?

    A pin create is only SAFE because of its trailing halt: on a dirty lane
    ``git switch -C`` refuses (rc=1), and an unhalted fence then runs the merges
    or the suite with HEAD still on the operator's branch — the P1 the fence's
    own comment says ``-C`` prevents. `|| true` is the negation of that, so it
    is refused even though it "contains" a halt-looking token.
    """
    # `raw` keeps the quoted SUBJECTS (`"$BATCH_TOTAL"`, `"$PINNED"`) that the call
    # site's subject predicate must still see; `code` is the blanked form used for every
    # structural test. Both are the same length, so the halt's index carries across.
    raw = code
    code = _unquoted(code)
    if "|| true" in code:
        return False
    # A halt in a SUBSHELL is not a halt: `… || { echo x; exit 1; } &` and
    # `… | cat` put the brace in a child, so `exit 1` ends the child and the fence
    # continues (bash-verified: `false || { echo g; exit 1; } & echo AFTER` prints
    # AFTER with rc=0). The guard would then certify a landing on a moved `main`,
    # or a baseline measured on the operator's lane.
    if re.search(r"&\s*$", code) or re.search(r"\}\s*\|\s*\S", code):
        return False
    match = _REACHABLE_HALT.search(_reachable_code(code))
    if match is None:
        return False
    group = match.group("grp")
    if group is not None and not _GROUP_BODY.fullmatch(group[1:-1]):
        return False
    # The segment immediately before the halt must not be ALWAYS-0: `subject || : ||
    # { …; exit 1; }` is accepted by the leftmost-`||` match above, yet bash never
    # reaches the group (verified: `false || : || { echo HALT; exit 1; }; echo
    # continued` prints `continued`, rc=0). `_ends_at_halt` already refuses this
    # shape, and the four sites whose only predicate is this function are the landing
    # base assertion — the document's ONLY mechanical enforcement of tested-tree ==
    # landed-tree — the `[ -n "$PINNED" ]` guard, the count halt and the merge-ness
    # assert. This subsumes the `|| true` special case above rather than sitting
    # beside it; `echo`/`printf`/`cd .`/`git fetch` are the same family.
    # A no-op between the SUBJECT and the halt leaves the halt dead (`|| : ||`,
    # `|| /bin/true ||`). Enumerating no-ops cannot hold — `/bin/true`, `ls`,
    # `git status --porcelain`, `[ 1 ]`, `wait` … are all the same defect one spelling
    # over, and each was measured to survive. So when the caller knows its own SUBJECT
    # shape (as `_ends_at_halt` already does), the segment before the halt must MATCH it;
    # the `_SAFE_SEGMENT` refusal then only has to be right about a segment that is
    # subject-shaped, and any segment that is not the subject at all is refused outright.
    # Slice the RAW text: `_unquoted` blanks the quoted subjects (`"$BATCH_TOTAL"`,
    # `"$PINNED"`) that the site predicates must still see. `_unquoted` preserves
    # length, so the halt's index in the blanked text is the same index here.
    # Split the BLANKED head and slice `raw` by the resulting indices: a quoted
    # `|`, `;` or `&&` inside a segment is not a separator, and splitting the raw text
    # turned the last segment into a fragment — which made the subject path NARROWER
    # than the no-subject fallback on an honest halting line (measured).
    blank_head = code[: match.start(0)].rstrip()
    raw_head = raw[: match.start(0)].rstrip()
    bounds = [m.start() for m in re.finditer(r";|&&|\|\||\||&", blank_head)]
    pieces: list[str] = []
    start = 0
    for stop in bounds:
        pieces.append(raw_head[start:stop])
        start = stop + (2 if blank_head[stop : stop + 2] in ("&&", "||") else 1)
    pieces.append(raw_head[start:])
    segments = [seg.strip() for seg in pieces if seg.strip()]
    if not segments:
        return True
    if subject is not None:
        # `_pin_subject` is a PREDICATE; `_MERGE_ASSERT` and friends are compiled
        # patterns. Accept both rather than duplicating the sites' subject shapes.
        if callable(subject) and not hasattr(subject, "fullmatch"):
            return bool(subject(segments[-1]))
        return bool(subject.fullmatch(segments[-1]))
    return not _SAFE_SEGMENT.match(segments[-1])


def _block(fence: list[str], i: int) -> str:
    """The COMMAND at ``fence[i]``, joined across a multi-line ``|| { … }``.

    ``_fences()`` keeps one entry per line, so a halt written as

        [ "$(git rev-parse origin/main)" = "$PINNED" ] || {
          echo "main moved …"; exit 1
        }

    reaches ``_halts()`` as a line that merely ENDS in ``{`` — the ``exit 1``
    that makes it a halt sits two entries later and was invisible. The doc's
    landing fence uses exactly this form, so the checks that read it must see the
    block, not the first line.
    """
    code = _code(fence[i])
    if not code.rstrip().endswith("{"):
        return code
    parts = [code]
    for following in fence[i + 1 : i + 6]:
        piece = _code(following)
        parts.append(piece)
        if piece.rstrip().endswith("}"):
            break
    return " ".join(parts)


def _is_refusal(fence: list[str], i: int) -> bool:
    """Is entry ``i`` a command that REFUSES — shape AND halt, not two substrings?

    A line carrying ``git status --porcelain`` and ``exit 1`` satisfied the
    previous check while only PRINTING them
    (``echo '[ -z "$(git status --porcelain)" ] || exit 1'``), so the shape is
    pinned: the command must BE the test, not a message about it.
    """
    code = _code(fence[i])
    # `!`, `true`, `:`, `echo` — each one makes the command something other than
    # the refusal. `!` matters most: `! [ -z "$(…)" ] || exit 1` SUCCEEDS on a
    # dirty tree and refuses a clean one, i.e. it certifies the inversion.
    if code.startswith(("!", "echo ", "printf ", ":", "true")):
        return False
    if "git status --porcelain" in code and not re.match(
        r"^(?:\[\s*-z\s|test\s+-z\s)", code
    ):
        return False
    # The INVOCATION is pinned, not just the verb: `git status --porcelain -uno`
    # prints nothing on a tree whose only dirt is untracked files, and
    # `git switch -C` carries those into the pinned tree — so the fence measures
    # the lane under a heading promising `origin/main` ALONE.
    for match in re.finditer(r"git\s+status\b[^)\"']*", code):
        if not re.fullmatch(r"git\s+status\s+--porcelain", match.group(0).strip()):
            return False
    return _ends_at_halt(
        _block(fence, i), allow=_allows_dirty_test, subject=_allows_dirty_test
    )


#: `--detach` / `-d`: HEAD ends on a COMMIT, not a branch. The document itself
#: endorses that spelling for the Phase-1 pin create, which is exactly how an editor
#: "harmonising" the Phase-4 return writes it — and then the revert commits detached.
_DETACHES = re.compile(r"(?<!\S)(?:--detach(?:=\S*)?|-d)(?!\S)")
_FORCE_CREATE = re.compile(r"(?<!\S)(?:-C|-B|-c|-b|--force-create)(?!\S)")


def _lands_on_branch(code: str, branch: str) -> bool:
    """Does HEAD end ON ``branch`` — a branch at its tip, not detached, not reset?

    ``git switch --detach <branch>`` names the branch and moves HEAD off it;
    ``git switch -C <branch>`` with no start point RESETS the branch to whatever the
    bisect probe left checked out. Both keep every name-based check green, and both
    make the revert commit somewhere other than the branch that lands.
    """
    code = _code(code)
    code = re.sub(r"\|\|\s*(?:exit\s+[1-9]|\{[^}]*\})\s*$", "", code).strip()
    # ONE command: `git bisect reset <branch> && git switch main` (or
    # `… ; git reset --hard main`) names the branch and leaves HEAD somewhere else,
    # and only the separator shows it. A `;`-appended segment is an in-line attack on
    # the same line — the same discipline `_ends_at_halt` applies to the pin create.
    if re.search(r"[;&|]", _unquoted(code)):
        return False
    if _DETACHES.search(code) or _FORCE_CREATE.search(code):
        return False
    if code.startswith("git bisect reset"):
        rest = code[len("git bisect reset") :].split()
        return not rest or rest[0] == branch
    return (
        re.fullmatch(rf"git\s+(?:switch|checkout)\s+{re.escape(branch)}\s*", code)
        is not None
    )


def _re_runnable(code: str) -> bool:
    """Is this move command idempotent — safe on a RE-baseline / RE-build?

    Two things are checked, and the second is the one that matters: the FLAGS
    must be a combination git accepts, and the ARGUMENT SHAPE must put the pin
    where git reads a start point. Flag presence alone is not enough —
    ``git switch -C "$PINNED"`` has the create flag, exits 0, and never moves
    HEAD (it creates a branch *named* the sha at the current HEAD), which would
    leave the suite measuring the lane under a heading that promises
    ``origin/main`` alone.
    """
    sub = re.search(r"\bgit\s+(switch|checkout)\b", code)
    if sub is None:
        return False
    if sub.group(1) == "switch":
        create = re.search(r"switch\s+(-C|--force-create)(=\S*|\s+\S+)", code)
    else:
        create = re.search(r"checkout\s+(-B)(\s+\S+)", code)
    # exact token, so `--detach=1` (rc=129, "option `detach' takes no value")
    # is not mistaken for the flag
    detaches = re.search(r"(?<!\S)--detach(?!\S)", code) is not None
    if create and detaches:
        return False  # fatal: '--detach' cannot be used with '-b/-B/--orphan'
    if detaches:
        return True
    if create is None:
        return False
    branch = create.group(2).lstrip("=").strip().strip('"').strip("'")
    if branch == "$PINNED":
        # pin as the BRANCH NAME -> creates from HEAD, rc=0, HEAD never moves
        return False
    rest = code[create.end():].split()
    return bool(rest) and rest[0].strip('"').strip("'") == "$PINNED"  # START POINT, first token


#: A pin create must be the COMMAND, not a word inside another command's
#: arguments: `/bin/echo 'git switch -C integration/base-check' "$PINNED" || exit 1`
#: runs `echo`, never pins, and (before this) was selected as the subject.
_PIN_CREATE_FIRST = re.compile(r"^(?:command\s+|builtin\s+|exec\s+)?git\s+(?:switch|checkout)\b")

#: The `--force` FLAG as its own token (``-f`` is the same option, and refusing it would make
#: the honest fix inexpressible in the short spelling). ``--force-with-lease`` is a different
#: option and must not be mistaken for it (``--force-x`` is not ``--force``).
_FORCE_FLAG = re.compile(r"(?<!\S)(?:--force|-f)(?![-\w])")


def _pin_subject(segment: str) -> bool:
    """Is this segment the pin create itself?"""
    return bool(_PIN_CREATE_FIRST.match(segment)) and _re_runnable(segment)


def _allows_pin_segment(segment: str) -> bool:
    """A segment that may join a pin create: the create itself, or a no-op."""
    return _re_runnable(segment) or bool(_SAFE_SEGMENT.match(segment))


def _allows_dirty_test(segment: str) -> bool:
    """A segment that may join the dirty-tree test: the test itself."""
    return bool(_DIRTY_TEST.match(segment))


#: The reviewed-head fetch of the assembly fence (`git fetch … pull/<N>/head:refs/tmp/pr-<N>`).
#: ⛔ The ref it writes is LOCAL and it is REUSED by every later run, so a PR head that was
#: REWRITTEN since the last one — amend, rebase, force-push, the ordinary answer to a review —
#: makes that update a NON-fast-forward, which git REFUSES without `--force` / a leading `+`:
#: measured in a scratch repo with the exact command shape, `! [rejected] refs/pull/1/head ->
#: refs/tmp/pr-1 (non-fast-forward)`, rc=1, and the ref left at the PREVIOUS head. The fetch was
#: also the one line of the fence that carried no halt, so the fence continued and the merge
#: landed yesterday's head under a comment reading "exactly the reviewed head" — a clean
#: `--no-ff` merge with a correct-looking `batch: merge PR #<N>` subject, Phase 2's own
#: eligibility invariant (`headRefOid` == the reviewed head) broken one phase before Landing can
#: see it, and Phase 3 certifying a tree built from heads nobody reviewed.
_PR_HEAD_FETCH = re.compile(r"\bgit\s+fetch\b[^|&;]*\bpull/")

#: The whole COMMAND of the reviewed-head fetch: ONE command (no separator before the halt),
#: a refspec naming a pull head, then a HALT at the end of the line.
_REVIEWED_HEAD_FETCH = re.compile(
    r"git\s+fetch\s+(?P<flags>[^|&;]*?)\s*(?P<refspec>\S*pull/\S*)"
    r"\s*\|\|\s*(?:\{[^}]*\}|exit\s+[1-9]\d*)\s*"
)


def _reviewed_head_fetch(code: str) -> bool:
    """Is this the reviewed-head fetch, ONE command, FORCED and HALTING on refusal?

    Three properties, and the last two are the ones a spelling check cannot see:
    ``--force`` (or a ``+``-prefixed refspec) is the documented override for a
    non-rewindable local ref, and the halt is what stops a REFUSED fetch instead of
    merging the stale ref it left behind. Both were missing at ``7fb722a9a`` while the
    line's own comment claimed "exactly the reviewed head".
    """
    code = _code(code)
    shape = _REVIEWED_HEAD_FETCH.fullmatch(_unquoted(code))
    if shape is None:
        return False
    forced = _FORCE_FLAG.search(shape.group("flags")) is not None or shape.group(
        "refspec"
    ).startswith("+")
    return forced and _halts(code, subject=_is_pr_head_fetch_segment)


def _is_pr_head_fetch_segment(segment: str) -> bool:
    """Is this segment the reviewed-head fetch itself (the halt's subject)?"""
    return bool(_PR_HEAD_FETCH.search(segment))


#: The batch MERGE line. `git merge --no-ff --no-edit -m "batch: merge PR #<N>" <ref>` on a head that
#: is ALREADY contained prints `Already up to date` and exits **0** with no merge commit — so the
#: `git log --oneline -1` after it is cosmetic, the manifest names a PR whose commits are not in the
#: tree, and Phase 4's `git revert -m 1` has no mainline to revert. The fetch line directly above it
#: got a halt for exactly this reason (a REFUSAL exits non-zero, a NO-OP exits zero — two different
#: failures), and the merge line was the one left bare. Asserted with `HEAD^2`, because a no-op
#: `Already up to date` merge exits 0 and the status alone cannot see it.
_BATCH_MERGE = re.compile(r"^git\s+merge\s+--no-ff\b")
_BATCH_MERGE_SUBJECT = re.compile(r"git\s+merge\s+--no-ff\b.*")
_MERGE_COMMIT_ASSERT = re.compile(r"^git\s+rev-parse\s+-q\s+--verify\s+HEAD\^2\b")
_MERGE_COMMIT_SUBJECT = re.compile(
    r"git\s+rev-parse\s+-q\s+--verify\s+HEAD\^2(?:\s*>\s*/dev/null)?\s*"
)


def _is_halted_batch_merge(code: str) -> bool:
    """Is this the assembly MERGE, as ONE command that HALTS?"""
    code = _code(code)
    return bool(_BATCH_MERGE.match(code)) and _halts(code, subject=_BATCH_MERGE_SUBJECT)


def _is_merge_commit_assert(code: str) -> bool:
    """Is this the `HEAD^2` merge-commit assertion, as ONE command that HALTS?"""
    code = _code(code)
    return bool(_MERGE_COMMIT_ASSERT.match(code)) and _halts(
        code, subject=_MERGE_COMMIT_SUBJECT
    )


def _ends_at_halt(code: str, allow=_allows_pin_segment, subject=_pin_subject) -> bool:
    """Is the halt REACHABLE from the subject's failure — every separator `&&`?

    Two things go wrong without this, and both were measured:

    * ``pin ; true || exit 1`` — the halt belongs to ``true``, which never fails,
      so the fence runs the whole suite and exits **0**. ``pin | true || exit 1``
      and ``pin & true || exit 1`` are the same failure one separator over: a
      pipeline's status is its LAST command's, and ``&`` does not wait at all, so
      a FAILED pin never reaches the halt (HEAD still moves — the fork writes it —
      which makes the race worse, not better). Every word there (``true``, ``:``,
      ``echo``, ``cd``, ``printf``, ``git fetch``) is in ``_SAFE_SEGMENT``, so an
      allowlist over SEGMENTS alone blesses it.
    * ``A || exit 1 && C`` parses as ``(A || exit 1) && C``, so a suffix steals the
      halt rather than being stopped by it.

    So the SEPARATORS are kept: no pipeline at all, and every separator between the
    last allowlisted subject and the halt must be ``&&`` — the only separator that
    short-circuits a failure into the ``||``. ``;``/``&&`` BEFORE the subject stays
    legal: their failure is harmless, since the subject is still halted.
    """
    halt = re.fullmatch(
        r"(?P<head>.*?)\|\|\s*(?P<halt>\{[^}]*\}|exit\s+[1-9]\d*)\s*",
        _reachable_code(code),
    )
    if halt is None:
        return False
    # The group body must be plain segments then the exit — `(exit 1)` exits a CHILD,
    # and anything after the `exit` cannot be reached.
    if halt.group("halt").startswith("{") and not _GROUP_BODY.fullmatch(
        halt.group("halt")[1:-1]
    ):
        return False
    # Slice the head from the ORIGINAL text: the subject predicates need to see
    # `"$PINNED"`, which the blanking removed.
    parts = re.split(r"(;|&&|\|\||\||&)", code[: halt.end("head")])
    segments = [piece.strip() for piece in parts[0::2]]
    separators = parts[1::2]
    if "|" in separators or "&" in separators:
        # `a | b` yields b's status; `a &` does not even wait, so neither can
        # deliver the subject's failure to the halt. The bare `&` HAS to be in the
        # split alphabet: left inside the segment, `_re_runnable` blesses
        # `pin & true` as a pin create one character away from the accepted
        # `pin && true`.
        return False
    subjects = [
        i
        for i, segment in enumerate(segments)
        if segment and subject(segment) and not _SAFE_SEGMENT.match(segment)
    ]
    if not subjects:
        return False
    if any(sep != "&&" for sep in separators[max(subjects) :]):
        return False
    return all(allow(segment) for segment in segments if segment)


#: The test target a marker-scoped run names (``tests/test_ingest_safety.py``).
_TARGET_FILE = re.compile(r"\b(tests/[\w./-]+\.py)\b")


def _text() -> str:
    assert DOC.is_file(), f"{DOC.relative_to(ROOT)} is missing — re-point this guard"
    return DOC.read_text(encoding="utf-8")


def _sentences_with(phrase: str) -> list[str]:
    """Every whitespace-normalised document SENTENCE that carries ``phrase``.

    Round 39, and the reason a *bullet* scope is not enough: the five non-test leg names appear
    **twice inside the same bullet** — once in the enumeration a reader takes as the partition, and
    once in the paragraph about which of them declare ``needs:``. Measured at ``97f413e53``:
    replacing the whole enumeration with prose left a bullet-scoped needle green, because the second
    mention is in the same bullet. The unit a partition claim is MADE in is the sentence, so that is
    the unit the needle is scoped to.

    Normalising whitespace first is what makes a reflow harmless (the clause spans eight source
    lines); the split is on a period followed by whitespace, which is markdown's own prose
    convention and does not fire inside a code span in this document (paths and versions carry no
    ``'. '``).
    """
    normalised = re.sub(r"\s+", " ", _text())
    return [
        sentence
        for sentence in re.split(r"(?<=\.)\s+", normalised)
        if phrase.lower() in sentence.lower()
    ]


def _sentence_with(phrase: str) -> str:
    """The FIRST sentence carrying ``phrase`` (see :func:`_sentences_with` for the scoping rule)."""
    found = _sentences_with(phrase)
    if not found:
        raise AssertionError(
            f"the doctrine no longer says {phrase!r} — re-point this guard (a scoped needle whose "
            "subject is gone fails silently otherwise)"
        )
    return found[0]


def _bullet(lead: str) -> str:
    """The top-level markdown BULLET whose first line starts with ``lead`` — nothing after it."""
    lines = _text().splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(lead)), -1)
    if start < 0:
        raise AssertionError(f"the doctrine no longer carries the bullet {lead!r} — re-point this guard")
    end = start + 1
    while end < len(lines) and lines[end].strip() and not lines[end].startswith(("- ", "#")):
        end += 1
    return "\n".join(lines[start:end])


def _fences() -> list[list[str]]:
    """Every fenced code block, as its list of non-blank, non-comment lines."""
    fences: list[list[str]] = []
    current: list[str] = []
    in_fence = False
    for line in _text().splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                fences.append(current)
                current = []
            in_fence = not in_fence
            continue
        if in_fence and stripped and not stripped.startswith("#"):
            current.append(stripped)
    return [fence for fence in fences if fence]


def _fenced_commands() -> list[str]:
    """Every non-blank, non-comment line inside a fenced code block, flattened."""
    return [cmd for fence in _fences() for cmd in fence]


def _deterministic_half(cmd: str) -> bool:
    """A full-suite run of the deterministic half (``-m 'not track_b …'``)."""
    return bool(_SUITE_CMD.match(cmd)) and TRACK_B_FILTER not in cmd


def _full_suite_fences() -> list[list[str]]:
    """The fences that carry a full-suite run, in document order."""
    return [fence for fence in _fences() if any(_SUITE_CMD.match(c) for c in fence)]


def _fence_index(predicate) -> int:
    """Index of the first fence satisfying ``predicate``; -1 when there is none."""
    for i, fence in enumerate(_fences()):
        if predicate(fence):
            return i
    return -1


#: A stated NUMBER of bare-run ERRORs. One ERROR lands per CHECK (`conftest.py`'s
#: `_p4_uri_required` is `scope="session", autouse=True`, so pytest re-raises the failed session
#: fixture per item), so a literal is stale the moment a check lands: the header said 16 with 18
#: checks present (#10400 review P3), and a bare bump to 18 re-stales the same way.
_ERROR_COUNT_CLAIM = re.compile(r"\b\d+\s+ERRORS?\b")


def test_the_header_does_not_hard_code_the_bare_run_error_count() -> None:
    """#10400 review P3: the header's own claim about this file must not be a NUMBER.

    A URI-less run errors against every item, so the number of ERRORs is a function of how
    many checks this file has, not a fact about the doctrine. Measured at this head: a bare
    ``pytest tests/test_batch_integration_doctrine.py`` reports one ``ERROR at setup`` per
    check, while the header stated a smaller literal — a claim with no mechanism behind it,
    in a file whose whole subject is claims that must not go stale. The header states the
    RULE now (one ERROR per check) and this check refuses any total, so the next check added
    cannot re-stale it.

    Non-vacuity, measured: restoring the pre-fix ``reports 16 ERRORS`` sentence reds this
    check and nothing else; the honest header is green.
    """
    # `__doc__` inside a function is THIS function's docstring, not the module's — the module
    # header is only reachable through the module namespace.
    header = globals().get("__doc__") or ""
    claim = _ERROR_COUNT_CLAIM.search(header)
    assert claim is None, (
        f"the module header hard-codes a bare-run ERROR count ({claim.group(0)!r}): one ERROR "
        "lands per check, so any total re-stales on the next check added — state it per-check"
    )


def test_this_guard_parses_the_document() -> None:
    """A parser that finds nothing would make every check below vacuous."""
    commands = _fenced_commands()
    assert len(commands) >= 10, f"parsed only {len(commands)} fenced commands — re-point this guard"
    assert _fences(), "no fenced code block parsed — re-point this guard"
    assert any(_SUITE_CMD.match(cmd) for cmd in commands)


def test_every_full_suite_command_is_one_of_the_two_deliberate_halves() -> None:
    """#10399 review ISSUE 1 / P1-5: the documented suite is the DETERMINISTIC suite.

    Bare ``pytest tests/`` runs the ``live`` and ``integration`` markers, which
    need live credentials and production access; no CI lane runs them. And a
    ``-m 'not track_b …'`` run is only HALF the deterministic suite: CI runs
    ``test-track-b`` with ``-m track_b`` as a MUST-PASS ``needs:`` of the
    required ``python-ci-gate``, so a batch that never runs it can be green with
    a gate member unrun. Every full-suite command must therefore be one half or
    the other, never a bare run and never a silently partial one.
    """
    full_suite = [cmd for cmd in _fenced_commands() if _SUITE_CMD.match(cmd)]
    # The ARGV of BOTH halves — the directory form would not match `test_ingest_safety.py`
    # target, so checking `full_suite` alone left `-m track_b --co` free: it collects
    # and runs nothing, and the gate then certifies BATCH-GREEN with the MUST-PASS
    # `test-track-b` leg unrun. An appended `--co`, `--lf`, `-k`, `--ignore=`,
    # `--deselect`, `-x` or `--maxfail=1` exits 0 on a tree no run has seen.
    halves = full_suite + [cmd for cmd in _fenced_commands() if TRACK_B_FILTER in cmd]
    unpinned_argv = [cmd for cmd in halves if not _suite_argv_ok(cmd)]
    assert not unpinned_argv, (
        "a suite command carries arguments outside the documented argv — "
        "`uv run pytest <target> [-q] [-m FILTER] [--junitxml=…]`: a selector such "
        f"as --co/--lf/-k/--ignore/--deselect runs nothing and still exits 0: "
        f"{unpinned_argv}"
    )
    # A detached run's status cannot come from `wait`, and the poll cannot come from a pid probe.
    # `wait` only reaps THIS shell's children and the poll runs in a LATER shell (measured, bash 5.2:
    # `bash: wait: pid N is not a child of this shell`, rc=127), while a pid going away says nothing
    # about COMPLETION — it fails just as fast for an OOM kill. The run's own shell must persist `$?`,
    # and the poll must read that file.
    launches = [cmd for cmd in _fenced_commands() if _STATUS_WRAPPER.match(_code(cmd))]
    assert launches, (
        "no detached suite run persists its exit status: launch it as "
        "`( uv run pytest …; echo $? > /tmp/<run>.rc ) &` — `wait` cannot return a pid that is not "
        "this shell's child (rc=127), so a status taken that way declares a completed run unproven"
    )
    polls = [cmd for cmd in _fenced_commands() if _RC_POLL.match(_code(cmd))]
    assert polls, (
        "the doctrine no longer POLLS the run's status file — re-point this guard (`[ -f "
        "/tmp/<run>.rc ] && echo 'DONE …' || echo RUNNING`)"
    )
    # ...and the file the poll reads must be the one the SAME fence wrote AND cleared. A leftover
    # status file from an earlier run makes the poll report DONE instantly and hands the verdict the
    # PREVIOUS run's status.
    for fence in _fences():
        codes = [_code(cmd) for cmd in fence]
        written = {m.group("rc") for c in codes if (m := _STATUS_WRAPPER.match(c))}
        if not written:
            continue
        read = {
            m.group(1).strip("\"'")
            for c in codes
            for m in re.finditer(r"\[\s*-f\s+\"?(" + _LEDGER + r")\"?\s*\]", c)
        }
        cleared = _cleared(codes)
        assert read and read <= written, (
            "the poll reads a status file this fence never writes: editing either line alone leaves "
            f"the poll on an absent file and reports DONE. writes={sorted(written)}, reads={sorted(read)}"
        )
        assert written <= cleared, (
            "a stale status file from an earlier run must be cleared before the launch (`rm -f`): "
            "otherwise the poll reports DONE at once and Phase 4 diffs the PREVIOUS run's status. "
            f"writes={sorted(written)}, cleared={sorted(cleared)}"
        )
    assert full_suite, "the doctrine no longer documents a full-suite run — re-point this guard"

    bare = [cmd for cmd in full_suite if "-m" not in cmd]
    assert not bare, (
        "a full-suite command in the doctrine carries no `-m` filter, so it runs "
        f"the live/integration markers no CI lane runs: {bare}"
    )

    # The two deliberate halves are told apart by which one they select: the
    # ``-m track_b`` half may target one file (CI's job runs
    # ``tests/test_ingest_safety.py -m track_b``), so it is matched on ANY command.
    deterministic = [cmd for cmd in full_suite if TRACK_B_FILTER not in cmd]
    track_b = [cmd for cmd in _fenced_commands() if TRACK_B_FILTER in cmd]

    missing = [
        cmd for cmd in deterministic if not all(term in cmd for term in EXCLUSION_TERMS)
    ]
    assert not missing, (
        "every deterministic full-suite command must exclude all of "
        f"{EXCLUSION_TERMS}; these do not: {missing}"
    )

    assert track_b, (
        "the doctrine documents no `-m track_b` run: `python-ci.yml`'s "
        "`test-track-b` job runs that half as a MUST-PASS need of the required "
        "`python-ci-gate`, so a batch that skips it leaves a gate member unrun"
    )
    for cmd in track_b:
        assert _TARGET_FILE.search(cmd), (
            f"the `-m track_b` command names no test target: {cmd!r}"
        )
        target = ROOT / _TARGET_FILE.search(cmd).group(1)
        assert target.is_file(), (
            f"the doctrine runs `-m track_b` against {target.relative_to(ROOT)!s}, "
            "which does not exist — CI's test-track-b job runs "
            "tests/test_ingest_safety.py, so a rename leaves the documented gate "
            "pointing at nothing"
        )


def test_the_track_b_gate_leg_is_run_in_the_gate_phase() -> None:
    """#10399 review ISSUE 1 / the ``e715d81b2`` P2: BOTH halves, in EVERY suite phase.

    A `-m track_b` mention anywhere would satisfy a naive substring check, so this
    requires it inside a fence that ALSO carries the deterministic half. And it
    requires that of **every** full-suite fence that runs the deterministic half —
    not just one: half 2 was added to the Phase 3 gate and not to the Phase 1
    baseline, which left the baseline set structurally incapable of containing a
    pre-existing `track_b` failure, so Phase 4 classified it NEW and convicted an
    innocent PR.
    """
    fences = _full_suite_fences()
    assert fences, "the doctrine no longer documents a full-suite run — re-point this guard"
    paired = [
        fence
        for fence in fences
        if any(TRACK_B_FILTER in c for c in fence)
        and any(_deterministic_half(c) and all(term in c for term in EXCLUSION_TERMS) for c in fence)
    ]
    assert paired, (
        "no fenced block runs the `-m track_b` half alongside the deterministic "
        "half; the gate phase must run BOTH (test-track-b is a python-ci-gate need)"
    )
    unpaired = [
        [c for c in fence if _deterministic_half(c)]
        for fence in fences
        if any(_deterministic_half(c) for c in fence)
        and not any(TRACK_B_FILTER in c for c in fence)
    ]
    assert not unpaired, (
        "a suite phase runs only the deterministic half and never `-m track_b`: "
        "a pre-existing track_b red would then be missing from that phase's id set "
        f"(Phase 4 calls it NEW and convicts an innocent PR); offending fences: {unpaired}"
    )


def test_the_lane_sync_is_the_one_command_that_keeps_the_extras() -> None:
    """#2985: plain `uv sync` is a KEYWORD-ONLY product, silently.

    ``uv sync --extra embeddings`` alone drops ``parity`` (and vice versa) — the
    document names this trap two paragraphs down and then leaves its own command
    unpinned, so a "full suite" that authorises ``BATCH-GREEN`` can be a partial run
    with the dense-retrieval leg degraded to keyword-only.
    """
    syncs = [cmd for cmd in _fenced_commands() if re.match(r"uv\s+sync\b", _code(cmd))]
    assert len(syncs) == 1, (
        f"the doctrine must document exactly one `uv sync`: {syncs}"
    )
    extras = sorted(shlex.split(_code(syncs[0]))[2:])
    assert extras == ["--extra", "--extra", "embeddings", "parity"], (
        "the lane install must name BOTH extras in one command: `uv sync --extra "
        "embeddings --extra parity`. A plain or single-extra sync silently removes "
        f"the other, and the run then measures a keyword-only product: {syncs}"
    )


def test_the_lane_is_selected_and_exported_before_the_first_suite_run() -> None:
    """#10399 review P1-4 / ISSUE 3: the suite is URI-required, so the lane comes first.

    ``tests/conftest.py::_p4_uri_required`` fails the session closed when
    ``TORTOISE_DB_URI`` is unset and the carve-out is not opted in, so a
    documented baseline that precedes the lane selection cannot run at all.

    Resolved against the FENCED COMMANDS, not raw lines: a prose mention of
    ``TORTOISE_DB_URI`` (the doc explains the gate in words, earlier) must not
    satisfy this, or deleting the whole export fence would leave it green.

    Since #10400's P2-2 the selection is filled in two ways: the literal
    ``export TORTOISE_DB_URI=…`` (the shared lane) **or** the private-container
    form ``lane="$(… tools/test_lane.py uri)"`` + ``eval "$lane"``. Both must be
    EXECUTABLE — a commented-out export selects nothing, so it cannot satisfy this.
    """
    fences = _fences()
    exports = [i for i, fence in enumerate(fences) if _lane_exports(fence)]
    suites = [
        i
        for i, fence in enumerate(fences)
        if any(_SUITE_CMD.match(_code(cmd)) for cmd in fence)
    ]
    assert exports, (
        "no fenced command exports a lane — neither `export TORTOISE_DB_URI=` nor "
        '`lane="$(… tools/test_lane.py uri)"` + `eval "$lane"`. The documented '
        "suite would die at session start "
        "(tests/_embedded.py::_assert_p4_uri_required)"
    )
    assert suites, "the doctrine no longer documents a full-suite run — re-point this guard"
    assert min(exports) < min(suites), (
        f"the lane is selected only in fence {min(exports) + 1} but the first "
        f"full-suite fence is {min(suites) + 1}: the baseline would die at session "
        "start (tests/_embedded.py::_assert_p4_uri_required)"
    )


def test_the_batch_lane_defaults_to_a_private_container() -> None:
    """#10400 review P2-2: a batch IS the multi-lane case, so its lane must be private.

    Phase 0 made the SHARED FalkorDB instance the default (`docker ps` works — which in
    this fleet is always true), with `tools/test_lane.py` demoted to a fallback gated on
    the shared instance being "contended or absent". That inverts the repo's own recorded
    #5084 rule for exactly the measurement the doctrine calls non-negotiable: peer lanes'
    graphs land in the shared instance during Phase 1/3, so a failure caused only by that
    interference enters the batch id set and not the baseline's, Phase 4 classifies it
    `NEW`, and an innocent PR is convicted.
    """
    fences = _fences()
    selections = [i for i, fence in enumerate(fences) if _lane_exports(fence)]
    assert selections, "no fence selects a lane — re-point this guard"
    lane_fence = fences[selections[0]]
    codes = [_code(c) for c in lane_fence]
    assert any(_TEST_LANE in c and " uri" in c for c in codes), (
        "the lane fence must select the PRIVATE container with "
        f"`tools/test_lane.py uri` as an executable command: {lane_fence}"
    )
    shared = [c for c in codes if _LANE_SHARED in c]
    assert not shared, (
        "the lane fence must not execute the SHARED instance as the default — its "
        "graphs are not a ledger while peer lanes run (#5084): "
        f"{shared}"
    )
    text = _text()
    assert "#5084" in text, (
        "the doctrine must name the #5084 rule it follows here: the private lane is "
        "the recorded repo rule for more than one live lane, and an unmarked departure "
        "from it is invisible to the operator who reads it next"
    )
    assert "no other lane is live" in text, (
        "the shared instance is admissible only under an explicit precondition; the "
        "doctrine must state it rather than leave `docker ps` to imply it"
    )


#: The lane ASSERTION a suite fence must carry before it runs.
#: ⛔ It is the GATE's predicate, not "is the variable set". `tests/_embedded.py::_assert_p4_uri_required`
#: accepts a SUPPORTED `TORTOISE_DB_URI` (`_uri_set_supported` — `is_db_uri`, `SUPPORTED_URI_SCHEMES`)
#: OR a TRUTHY `TORTOISE_TEST_CARVE_OUT` (`_carve_out_opted_in` — `is_truthy`). The pre-fix assertion was
#: `[ -n "${TORTOISE_DB_URI:-}" ] || [ -n "${TORTOISE_TEST_CARVE_OUT:-}" ]`, which satisfies neither
#: half of that for the two values the helpers were written to refuse: a set-but-unsupported `bolt://`
#: URI (this repo's own AGENTS.md documents it as a legal value) and `TORTOISE_TEST_CARVE_OUT=0`. Both
#: pass a `-n` test, both make the session fixture ERROR every collected item, and both exit 1 — the
#: status the COMPLETED predicate accepts. So the fence ASKS the gate's two predicates instead of
#: re-spelling the scheme list and the truthy vocabulary, which cannot drift when either grows.
#: The halt is checked separately (`_halts`): an invocation that only prints asserts nothing.
_LANE_ASSERT = re.compile(
    r"^(?:uv\s+run\s+)?python3?\s+-c\s+'"
    r"(?=[^']*\btests\._embedded\b)"
    r"(?=[^']*\b_uri_set_supported\b)"
    r"(?=[^']*\b_carve_out_opted_in\b)"
    r"(?=[^']*\bsys\.exit\b)[^']*'"
)


def test_the_lane_is_asserted_in_every_fence_that_runs_the_suite() -> None:
    """#10400 review P1: the lane was the ONE shell-local value with no carrier and no halt.

    Phase 0's `export` reaches only the shell that ran it, and Phases 1 and 3 are usually LATER shells
    — the doctrine already re-derives `$B` and carries the run's status and `$PINNED` in files for
    exactly that reason. The lane had neither treatment. Its absence is not visible in the status:
    `conftest.py`'s `_p4_uri_required` is `scope="session", autouse=True`, so pytest re-raises it
    **per item** — every collected test ERRORs at setup and the process exits **1**, which is one of
    the two codes the fence's own COMPLETED predicate accepts. Measured at this head
    (`env -u TORTOISE_DB_URI -u TORTOISE_TEST_CARVE_OUT pytest tests/test_batch_integration_doctrine.py
    --junitxml=…`): **rc=1**, one `<testcase>` per check in the report. Both id sets then become
    all-error and identical, Phase 4 diffs them to no NEW ids, and the doctrine prints
    ``BATCH-GREEN`` for a tree on which zero tests executed.

    So every fence that runs the suite must ASSERT the lane — present, or an explicit carve-out —
    and HALT on it, before it clears or writes any artifact. Checking only fence ORDER (what
    ``test_the_lane_is_selected_and_exported_before_the_first_suite_run`` does) cannot see this: the
    export is in Phase 0's fence, and both suite fences are later shells.

    Non-vacuity, measured: at the pre-fix head (``62c79b0b8``) neither suite fence contains the
    assertion, so this check reds on both and on nothing else; the honest head is green.
    """
    fences = _full_suite_fences()
    assert len(fences) >= 2, (
        "expected at least the Phase 1 and Phase 3 suite fences, parsed "
        f"{len(fences)} — re-point this guard"
    )
    for number, fence in enumerate(fences, start=1):
        codes = [_code(c) for c in fence]
        asserts = [i for i, code in enumerate(codes) if _LANE_ASSERT.match(code)]
        assert asserts, (
            f"suite fence {number} runs the full suite without asserting a lane. Phase 0's export is "
            "shell-local, and a run with no lane ERRORs every collected test at setup and exits 1 — "
            "which this fence's own COMPLETED predicate ACCEPTS — so both id sets become all-error "
            "and the Phase 4 diff is empty: BATCH-GREEN on a tree where nothing ran. Assert it IN "
            "the fence, the way `$B` is re-derived there, and with the GATE's predicate: "
            "`uv run python -c '… from tests._embedded import _uri_set_supported, "
            "_carve_out_opted_in …'` || { … exit 1; }"
        )
        assert any(_halts(_block(fence, i)) for i in asserts), (
            f"the lane assertion in suite fence {number} does not HALT: a bare `[ -n … ]`, `|| true` "
            "or a `{ … exit 1; }` the shell never reaches all leave an unexported run certifying "
            "every item as a COMPLETED run"
        )
        first_suite = min(i for i, code in enumerate(codes) if _SUITE_CMD.match(code))
        assert min(asserts) < first_suite, (
            f"the lane is asserted only AFTER the first suite run in fence {number}, so the run it "
            "guards is exactly the one that certifies itself"
        )
        writes = [i for i, code in enumerate(codes) if "$B/" in code]
        if writes:
            assert min(asserts) < min(writes), (
                f"the lane assertion sits after fence {number}'s first write into the ledger: the "
                "artifacts are already being produced for a run that cannot mean anything"
            )


#: The batched tree Phase 2 creates. Phase 2's own fence is
#: `git switch -C integration/batch-1 "$PINNED" || exit 1`, so the name is the doctrine's, not this
#: guard's — `test_every_full_suite_fence_names_the_tree_it_runs_on` only requires that a fence
#: ESTABLISHES its tree that way or ASSERTS it, never that it spells this particular branch.
_TREE_SWITCH = re.compile(
    r"^git\s+switch\s+-C\s+(?P<branch>\S+).*\|\|\s*exit\s+[1-9]\d*$"
)

#: The SUBJECT of a tree assertion: the ref ``HEAD`` is compared to. At ``9f8e12192`` the guard read
#: only the PRESENCE of ``git rev-parse --abbrev-ref HEAD``, so pasting ``main`` in as the compared
#: value — ``[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { … exit 1; }``, i.e. requiring the
#: gate run to happen on ``main`` — left the module GREEN check-for-check: the branch name still appeared in
#: the line's ``echo`` prose, and the halt was intact. That reproduces the exact failure this check's
#: own docstring says it was written for (a gate run on ``main`` certifying the batch), which is the
#: subject-vs-mention shape the branch has already fixed three times (``_COUNT_SUBJECT``,
#: ``_MERGE_SUBJECT``, ``_LANDED_TREE_DIFF``).
_TREE_ASSERT_REF = re.compile(
    r'^\[\s*"\$\(git\s+rev-parse\s+--abbrev-ref\s+HEAD\)"\s*=\s*(?P<ref>"?[^\s\]"]+"?)\s*\]'
)

#: Refs a full-suite fence may NEVER assert: a gate run on `main` (or a detached/short spelling of it)
#: certifies a tree nobody batches and nobody lands.
_NON_BATCH_REFS = {"main", "master", "HEAD", "-", "$PINNED", "origin/main"}

#: The smallest wait the document may use, in SECONDS: 6 h.
#: ⛔ The pre-fix bound was **240 × 15 s = 60 min**, and the run it bounded cannot finish in it. The
#: command (`uv run pytest tests/ -m 'not track_b and not live and not integration' --ignore=tests/e2e`)
#: has no xdist and no leg partition, so it runs the UNION of CI's pools in one process — by this
#: repo's own measurements (`docs/ops/ci-wall-clock.md`, `config/ci-surfaces.yml`): `test (a…i)` p50s
#: serialise to ≈91–134 min, `test-slow` is 37.05 min of measured test time, `test-carve-out` is
#: 8.3–8.6 min × 3 shards ≈ 25 min — ≈153–196 min, before collecting 41,726 tests. CI gets ~37 min of
#: WALL clock out of that only by sharding across runners. So a bound under the run is not a wedge
#: detector, it is a false halt on every honest run — and its recovery path (re-run the fence) clears
#: the status of a suite that is still EXECUTING, because the run is detached and outlives the shell.
_MIN_WAIT_SECONDS = 6 * 60 * 60

#: The only honest shape of "wait for the detached run": a BOUNDED loop on a literal count that
#: sleeps between reads. Unbounded (`while [ ! -f … ]`) holds a wedged lane forever; unbounded-free
#: (`sleep 900` once) guesses. A literal bound makes the wait provably finite — and `count × sleep`
#: must clear `_MIN_WAIT_SECONDS`, or the bound is shorter than the run it bounds.
_BOUNDED_WAIT = re.compile(
    r"^for\b.*\$\(\s*seq\s+1\s+(?P<count>[0-9]+)\s*\).*\bdo\b.*\bsleep\s+(?P<sleep>[0-9]+)"
)

#: A refusal to re-run over a run that is STILL EXECUTING: the wait's expiry is not the run's end.
_LIVE_RUN_REFUSAL = re.compile(r"find\s+\"?\$\{?B\}?/[\w.-]+\"?\s+-newermt")


def test_every_full_suite_fence_names_the_tree_it_runs_on() -> None:
    """#10400 review P2: the GATE fence refused a dirty tree but never said WHICH tree.

    Every other phase halts on its tree — Phase 1 and Phase 2 through
    `git switch -C <branch> "$PINNED" || exit 1`, Phase 4 through `git switch integration/batch-1 ||
    exit 1` — because the branch those phases leave HEAD on is not carried into the next shell. Phase
    3 is the run the verdict is ABOUT, and it checked only `git status --porcelain`. So a `git switch`
    in any intervening shell (or a lane branch left checked out) made the gate measure a tree that is
    not the batch, and nothing said so: measured, inserting `git switch main` immediately before the
    Phase 3 suite run left the whole guard GREEN.

    A fence satisfies this either by CREATING its tree (`switch -C … || exit`, which is how Phase 1
    and Phase 2 establish theirs) or by ASSERTING it (`git rev-parse --abbrev-ref HEAD`), and an
    assertion must HALT — an `echo` of the wrong branch measures the wrong tree just as silently.
    """
    fences = _full_suite_fences()
    assert len(fences) >= 2, (
        "expected at least the Phase 1 and Phase 3 suite fences, parsed "
        f"{len(fences)} — re-point this guard"
    )
    for number, fence in enumerate(fences, start=1):
        codes = [_code(c) for c in fence]
        establishes = [c for c in codes if _TREE_SWITCH.match(c)]
        asserts = [c for c in codes if "git rev-parse --abbrev-ref HEAD" in c]
        assert establishes or asserts, (
            f"full-suite fence {number} never names the tree it runs on. A clean tree is not the "
            "BATCH's tree: `git switch` in a later shell (or a lane branch left checked out) makes "
            "this run measure something else, and the verdict is about the tree it actually read. "
            'Create the tree (`git switch -C <branch> "$PINNED" || exit 1`) or assert it "\n            "(`[ "$(git rev-parse --abbrev-ref HEAD)" = <branch> ] || { … exit 1; }`) in THIS fence'
        )
        if not establishes:
            assert any(_halts(c) for c in asserts), (
                f"the tree assertion in full-suite fence {number} does not HALT: a bare compare, "
                "`|| true` or an `echo` leaves a gate run on another branch certifying the batch"
            )
        # …and it must come before the suite line it guards, or the run it certifies is the one
        # that moved the tree.
        first_suite = min(i for i, code in enumerate(codes) if _SUITE_CMD.match(code))
        asserted_at = [i for i, code in enumerate(codes) if _TREE_SWITCH.match(code)] + [
            i for i, code in enumerate(codes) if "git rev-parse --abbrev-ref HEAD" in code
        ]
        assert min(asserted_at) < first_suite, (
            f"fence {number} names its tree only AFTER the first suite run, so the run it guards is "
            "exactly the one that may have moved it"
        )
        # …and nothing may MOVE the tree between the name and the run it names. Presence alone is
        # not enough: `[ … = integration/batch-1 ] || exit 1` followed by `git switch main` and the
        # suite satisfies every line above while measuring `main` (measured: that exact insertion
        # left the presence-only form GREEN).
        movers = [
            i
            for i, code in enumerate(codes)
            if min(asserted_at) < i < first_suite
            and re.match(r"^git\s+(?:switch|checkout|reset|rebase|restore)\b", code)
        ]
        assert not movers, (
            f"fence {number} CHANGES the tree between naming it and running the suite on it "
            f"(line {movers[0] + 1}: {codes[movers[0]]!r}): the run certifies that tree, not the one "
            "the branch named"
        )
        # ⛔ ...and the SUBJECT of that assertion, not merely its presence. A fence that only ASSERTS
        # its tree never says which ref HEAD must BE; `= main` keeps the branch name in the message,
        # satisfies every line above, and makes the gate run on `main` certify the batch — measured at
        # `9f8e12192`: 0 red. So the compared ref must be the branch THIS fence's own
        # `switch -C` created when it creates one, and otherwise a real non-`main` ref.
        created = {m.group("branch") for c in codes if (m := _TREE_SWITCH.match(c))}
        for code in asserts:
            subject = _TREE_ASSERT_REF.match(code)
            assert subject, (
                f"fence {number}'s tree assertion does not compare HEAD to a ref this guard can "
                "read, so nothing stops the compared value from being `main` — the shape that "
                f"measured GREEN while certifying a gate run on main: {code}"
            )
            ref = subject.group("ref").strip('"')
            if created:
                assert ref in created, (
                    f"fence {number} asserts HEAD is {ref!r} but the tree it CREATES in the same "
                    f"fence is {sorted(created)!r}: the run would measure a tree this fence never "
                    "established"
                )
            else:
                assert ref not in _NON_BATCH_REFS, (
                    f"fence {number} asserts HEAD is {ref!r}: a full-suite fence that pins `main` "
                    "(or a spelling of it) certifies the batch from a tree nobody batches and "
                    "nobody lands — name the BATCH branch (e.g. integration/batch-1)"
                )


def test_a_fence_that_launches_the_suite_waits_before_it_reads_the_status() -> None:
    """#10400 review P2: Phase 3's fence could not be executed at all.

    The launch, a ONE-SHOT read of the status file and the "did it COMPLETE" halt sat in the same
    fenced block, in that order. Executed top-to-bottom — which is how the document presents every
    fence — the read lands before the detached run can possibly have written `$B/batch.rc`, the block
    prints `RUNNING`, and the halt two lines later reports the run as "never launched in this shell"
    about a run it launched one line earlier. Measured with a 2-second stand-in suite: `RUNNING`, then
    the halt, `rc=1`, while the run's own shell wrote `0` three seconds later. So the gate phase could
    not be run as written, and half 2 — the `track_b` MUST-PASS gate member — was never reached.

    The fix is a BOUNDED wait between the launch and the read, not a blocking call: the suite stays
    detached (`&`) and so outlives the shell, and the bound keeps a wedged run from holding the lane.
    """
    waited = 0
    for number, fence in enumerate(_fences(), start=1):
        codes = [_code(c) for c in fence]
        launches = [i for i, c in enumerate(codes) if _STATUS_WRAPPER.match(c)]
        if not launches:
            continue
        for poll in (i for i, c in enumerate(codes) if _RC_POLL.match(c)):
            waits = [
                w
                for i in range(launches[0], poll)
                if (w := _BOUNDED_WAIT.match(codes[i]))
            ]
            assert waits, (
                f"fence {number} launches a detached suite run and then reads its status file with "
                "no bounded wait in between, so the fence cannot execute: it prints RUNNING and the "
                "halt below it reports a run it just launched as never launched. Put a bounded wait "
                "(`for _ in $(seq 1 360); do [ -f $B/<run>.rc ] && break; sleep 60; done`) between "
                "the launch and the poll"
            )
            # ...and the bound must OUTLAST the run, not merely be bounded. The pre-fix `240 × 15 s`
            # is 60 min against a command whose own documented composition is ≈153–196 min, so the
            # fence halted EVERY honest run and then its re-run cleared the status of a suite that
            # was still executing. Presence alone cannot see that (measured: `1 × 15` and `240 × 1`
            # both left the guard green), so the ARITHMETIC is graded.
            seconds = max(int(w.group("count")) * int(w.group("sleep")) for w in waits)
            assert seconds >= _MIN_WAIT_SECONDS, (
                f"fence {number}'s bounded wait is {seconds} s, under the {_MIN_WAIT_SECONDS} s this "
                "command's own measurements say one uninterrupted run of it costs (≈153–196 min of "
                "test time, unsharded, before collecting 41.7k tests). A bound shorter than the run "
                "halts honest gates instead of wedges, and re-running the fence then `rm -f`s the "
                "status of a suite that is still EXECUTING"
            )
            # ...and the expiry must not be READ as "the run is over": the run is detached and
            # outlives this shell, so the fence must refuse to restart over a live one (a re-run
            # clears `$B/<run>.rc` of a suite that is still executing, and both runs then share this
            # batch's ledger).
            assert any(
                _LIVE_RUN_REFUSAL.search(codes[i]) and _halts(_block(fence, i))
                for i in range(launches[0], len(codes))
            ), (
                f"fence {number} treats its bounded wait's EXPIRY as the run's END: the suite is "
                "detached and outlives the shell, so re-running hits the `rm -f` above for a suite "
                "that is still EXECUTING — whichever finishes first owns the ledger, which is the "
                "per-batch-ledger collision one level down, inside one batch. Refuse to restart when "
                "`$B/<run>.log` was written recently, and say to wait for the status instead"
            )
            waited += 1
    assert waited, (
        "no fence launches a detached suite run and polls its status — re-point this guard"
    )


#: The per-batch ledger directory. `BATCH` is REQUIRED (`:?` halts on an empty one — an empty name
#: addresses a bare /tmp path every batch on the box writes), and `$B` is DERIVED from it, so every
#: fence and every later shell reproduces the same directory without carrying anything.
#: The declarator prefix is part of the shape on purpose: `BATCH` IS a carrier (Phases 1 and 3 are
#: usually later shells), so Phase 0 has to `export` it — and anchoring at `^BATCH="` made the honest
#: `export BATCH="…"` INEXPRESSIBLE, the "the guard refuses its own remedy" shape this file has
#: fixed three times. A later plain re-assignment preserves the export attribute.
_LEDGER_NAME = re.compile(
    r'^(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)*|typeset\s+(?:-\w+\s+)*)?'
    r'BATCH="\$\{BATCH:\?[^}]*\}"$'
)
_LEDGER_DIR = re.compile(r'^B="/tmp/batch-\$\{?BATCH\}?"$')
_LEDGER_CREATE = re.compile(r'^mkdir\s+-p\s+"?\$\{?B\}?"?\s*\|\|\s*exit\s+[1-9]\d*$')
_LEDGER_PRESENT = re.compile(
    r'^\[\s*-d\s+"?\$\{?B\}?"?\s*\]\s*\|\|\s*\{[^{}]*exit\s+[1-9]\d*\s*;?\s*\}$'
)

#: A HOST `/tmp` path: the `tmp` must start the token, so a git ref (`refs/tmp/pr-<N>`) is not one.
_HOST_TMP = re.compile(r'(?:^|[\s"\'=>(])/tmp/')


def test_every_verdict_artifact_lives_in_a_per_batch_directory() -> None:
    """the ``a6a71c1e5`` P2: the ledger was host-global, so a PEER batch decided this batch's verdict.

    All seven artifacts the verdict is built from were literal, unnamespaced ``/tmp`` paths —
    ``/tmp/batch.rc`` (the status), ``/tmp/batch-junit.xml`` (the ids), ``/tmp/baseline.total`` (the
    count) and their four siblings. The doctrine's own premise (#5084, above) is that peer lanes run
    the suite on this box while Phase 1 and Phase 3 run, so a peer batch finishing DURING this one's
    run writes the same two files:

    1. the poll reads ``DONE`` (the file exists) and ``$RC`` is the peer's status — honestly a
       COMPLETE run, just not this one;
    2. ``$BATCH_TOTAL`` is the peer's count, a real and coherent number, so the count halt — the last
       mechanical "did it finish" detector — passes;
    3. Phase 4 diffs the peer's green ids against this baseline and prints ``BATCH-GREEN`` for a tree
       whose own run is still executing. The mirrored case convicts an innocent PR with its ids.

    ``rm -f`` closes only the already-finished direction; the finishing-during-my-run direction is
    untouched, and the window is the whole duration of the run, not a race of milliseconds. The repo
    has measured the class: ``python-ci.yml`` records a fully GREEN leg that still concluded
    ``failure`` because a sibling leg's ``> /tmp/pytest.log`` truncated the file between this leg's
    ``tail`` and its attribution read (run 40808264676), and #10028's fix refuses the shortcut
    outright (``base="${RUNNER_TEMP:?…}"``). So the ledger follows the container into per-batch
    isolation: ONE directory, defined once, REQUIRED by name, re-derived by every fence that writes
    into it, with an absent directory a HALT — never a write at ``$B``'s path with ``$B`` empty.
    """
    codes = _fenced_commands()
    names = [c for c in codes if _LEDGER_NAME.match(_code(c))]
    dirs = [c for c in codes if _LEDGER_DIR.match(_code(c))]
    assert names, (
        "the doctrine defines no per-batch ledger NAME: every verdict artifact must live under one "
        'per-batch directory addressed by a required name — `BATCH="${BATCH:?…}"` (an unset one '
        "silently addresses the bare /tmp path every batch on this box writes)"
    )
    assert dirs, (
        'no `B="/tmp/batch-$BATCH"` derivation: the directory must be DERIVABLE in every fence (and '
        "in every later shell), not carried from Phase 0's"
    )
    assert len(set(dirs)) == 1, (
        f"the ledger directory is derived more than one way: {sorted(set(dirs))}"
    )

    # 1a. Every fence that derives the directory derives it from a REQUIRED name. A DEFAULT is not a
    #     weaker version of the rule, it is the defect: `${BATCH:-1}` silently picks another batch's
    #     directory the moment the name is forgotten.
    defining = [
        fence
        for fence in _fences()
        if any(_LEDGER_NAME.match(_code(c)) or _LEDGER_DIR.match(_code(c)) for c in fence)
    ]
    assert defining, "no fence defines the ledger directory — re-point this guard"
    for fence in defining:
        fence_codes = [_code(c) for c in fence]
        assert any(_LEDGER_NAME.match(c) for c in fence_codes), (
            'a ledger directory is derived without a REQUIRED name (`BATCH="${BATCH:?…}"`): a '
            "default (`${BATCH:-1}`, or a bare `BATCH=1`) falls back to another batch's ledger "
            f"when the name is forgotten: {fence_codes[:3]}"
        )
        assert any(_LEDGER_DIR.match(c) for c in fence_codes), (
            f"a fence requires the ledger name but never derives `$B` from it: {fence_codes[:3]}"
        )

    # 1. NO verdict-bearing artifact may be a bare host-global /tmp path. The only literal `/tmp/`
    #    left in the fences is the directory ITSELF (`B="/tmp/batch-$BATCH"`). A git REF
    #    (`refs/tmp/pr-<N>`) is not a host path, so the `/tmp/` must START the token.
    bare = [
        c
        for c in codes
        if _HOST_TMP.search(c) and not _LEDGER_DIR.match(_code(c))
    ]
    assert not bare, (
        "a verdict-bearing artifact is written to a host-global /tmp path: a peer batch on this box "
        "writes the same file, so the poll reads ITS status, the count reads ITS report and Phase 4 "
        "diffs ITS ids — `BATCH-GREEN` printed for a tree whose own run is still executing. Put "
        f"every artifact under `$B`: {bare}"
    )

    # 2. Every fence that writes into the ledger HALTS on an absent directory: Phase 1 and Phase 3
    #    may be LATER shells than Phase 0's, where `$B` is empty.
    users = [fence for fence in _fences() if any("$B/" in _code(c) for c in fence)]
    assert users, "no fence uses the per-batch ledger directory — re-point this guard"
    for fence in users:
        fence_codes = [_code(c) for c in fence]
        derive = [
            i
            for i, c in enumerate(fence_codes)
            if _LEDGER_NAME.match(c) or _LEDGER_DIR.match(c)
        ]
        assert len(derive) >= 2, (
            "a fence writes into the ledger without re-deriving it: in a later shell `$B` is empty, "
            f"so its `rm -f`/write lands at the host root: {fence_codes[:3]}"
        )
        guards = [
            i
            for i, c in enumerate(fence_codes)
            if _LEDGER_CREATE.match(c) or _LEDGER_PRESENT.match(c)
        ]
        assert guards, (
            'a fence writes into the ledger without asserting the directory exists (`mkdir -p "$B"` '
            'in Phase 0, `[ -d "$B" ] || { … exit 1; }` in every later phase): an absent one must '
            "HALT, never write at `$B`'s path with `$B` empty"
        )
        first_use = min(i for i, c in enumerate(fence_codes) if "$B/" in c)
        assert max(derive) < first_use and min(guards) < first_use, (
            "the ledger directory is derived or checked AFTER the first write into it, so the write "
            "goes to whatever `$B` happened to be"
        )


def test_phase_1_checks_out_the_pinned_base_before_the_baseline_suite() -> None:
    """#10399 review ISSUE 2: ``git branch`` creates a ref and does NOT move HEAD.

    The baseline fence is headed "run the suite on ``origin/main`` ALONE", and
    its id list is the ledger every later verdict is diffed against. Created with
    ``git branch`` from a lane worktree, the suite runs on the LANE — polluting
    the ledger in both directions (main's own red is hidden, an innocent PR is
    convicted). The fence must move HEAD onto ``"$PINNED"`` before the run.
    """
    fences = _fences()
    first = _fence_index(lambda fence: any(_SUITE_CMD.match(c) for c in fence))
    assert first >= 0, "no baseline fence found — re-point this guard"
    fence = fences[first]
    moves = [i for i, c in enumerate(fence) if _MOVES_HEAD.search(c) and '"$PINNED"' in c]
    assert moves, (
        "the baseline fence must CHECK OUT the pinned base (`git switch -c … "
        '"$PINNED"` or `git switch --detach "$PINNED"`) before running the '
        "suite; `git branch` creates the ref and leaves HEAD in the operator's "
        "own tree, so the baseline would be measured on the wrong tree"
    )
    run = [i for i, c in enumerate(fence) if _SUITE_CMD.match(c)]
    assert min(moves) < min(run), (
        "the baseline fence checks out the pinned base only AFTER the suite run"
    )


def test_bisect_returns_to_the_batch_branch_before_the_revert() -> None:
    """#10399 review ISSUE 3 (at ``f3c6c803b``) / P1-3: ``bisect run`` leaves HEAD detached.

    ``git bisect run`` ends detached (at the last probe it tested), so the documented
    ``git revert -m 1`` commits onto a detached HEAD: ``integration/batch-1``
    keeps the culprit the doctrine says to drop, and the final full run measures
    a tree nobody lands. A ``git bisect reset`` (or an explicit ``git switch``
    back to the batch branch — the walk-the-merges route detaches too) must come
    between the bisect and the revert.
    """
    commands = _fenced_commands()
    bisects = [i for i, c in enumerate(commands) if c.startswith("git bisect start")]
    reverts = [i for i, c in enumerate(commands) if "git revert" in c]
    assert bisects, "the doctrine no longer documents a bisect — re-point this guard"
    assert reverts, "the doctrine no longer documents dropping a culprit — re-point this guard"
    returns = [
        i
        for i, c in enumerate(commands)
        if c.startswith("git bisect reset") or re.match(r"git switch integration/batch-", c)
    ]
    assert returns, (
        "no `git bisect reset` (or `git switch integration/batch-…`) anywhere: "
        "`git bisect run` leaves HEAD DETACHED (at the last probe it tested), so the "
        "revert below never lands on the batch branch"
    )
    assert min(bisects) < min(returns) < min(reverts), (
        "the return to the batch branch must sit BETWEEN the bisect "
        f"(command {min(bisects) + 1}) and the revert (command "
        f"{min(reverts) + 1}); found it at {min(returns) + 1}"
    )
    # ...and every command that moves HEAD between the bisect and the revert must
    # land on the branch PHASE 2 CREATES — bound from its `-C <branch>`, not from a
    # `batch-` prefix. Repointing the documented `git switch integration/batch-1` to
    # `integration/base-check` (which Phase 1 is required to create, so it always
    # exists), to `main`, or to `-`, left this green while the revert committed
    # onto the wrong branch and the batch that landed still carried the culprit.
    started = re.search(r"git\s+bisect\s+start\s+(?:--?[\w-]+\s+)*(\S+)", _text())
    assert started, "no `git bisect start <branch>` found — re-point this guard"
    batch_branch = started.group(1)
    assert re.search(
        rf"git\s+(?:switch\s+-C|checkout\s+-B)\s+{re.escape(batch_branch)}\s+\"?\$PINNED",
        _text(),
    ), (
        f"the bisected branch ({batch_branch}) is not the one Phase 2 creates: the "
        "revert would then land on a branch the gate never built"
    )
    window = [
        (i, c)
        for i, c in enumerate(commands)
        if min(bisects) < i < min(reverts)
        and (_MOVES_HEAD.search(_code(c)) or _code(c).startswith("git bisect reset"))
    ]
    # ...and the whole window is an ALLOWLIST, because `_MOVES_HEAD` names only
    # switch/checkout: `git reset --hard main` as the "return" never entered the
    # window, the `git bisect reset` above it satisfied the ordering assert, and the
    # revert committed on `main`. Benign inspection and suite runs are allowed; every
    # other command between the bisect and the revert must land HEAD ON the branch.
    # ...and a benign line must BE one command too: the old prefix match let
    # `git log --oneline -1 ; git reset --hard main` through as "benign".
    benign = re.compile(r"^(?:git\s+(?:log|bisect)\b|uv\s+run\s+pytest\b)")
    stray = [
        c
        for i, c in enumerate(commands)
        if min(bisects) < i < min(reverts)
        and not (
            _lands_on_branch(c, batch_branch)
            or _is_blame_binding(_code(c))
            # ...and the LANE assertion, which this window must admit or the doctrine's own remedy
            # is inexpressible: the blame probe runs the suite, so its fence owes the lane halt the
            # other suite fences carry — and `benign` cannot see it, because it invokes python, not
            # pytest. It moves nothing (it reads two env predicates) and it HALTS, which is checked
            # here rather than assumed.
            or (_LANE_ASSERT.match(_code(c)) and _halts(_code(c)))
            or (
                benign.match(_code(c))
                # ...checked on the UNQUOTED text, so `git bisect run sh -c '… || exit 1'`
                # keeps its separator inside the quotes where it belongs.
                and not re.search(r"[;&|]", _unquoted(_code(c)))
            )
        )
    ]
    assert not stray, (
        "the only commands between the bisect and the revert may be benign "
        "inspection, a suite run, or a return that lands HEAD ON "
        f"{batch_branch}: {stray}"
    )
    wrong = [c for _, c in window if not _lands_on_branch(c, batch_branch)]
    assert not wrong, (
        "every HEAD-moving command between the bisect and the revert must land on "
        f"the branch Phase 2 creates ({batch_branch}); these do not, so the revert "
        f"commits onto another branch and the batch still carries the culprit: {wrong}"
    )
    assert window, "no return to the batch branch found — re-point this guard"
    assert _lands_on_branch(window[-1][1], batch_branch) and (
        _code(window[-1][1]).startswith("git bisect reset") or _halts(_code(window[-1][1]))
    ), (
        "the LAST command that moves HEAD before the revert must stay on the batch "
        f"branch without being able to fail open: {window[-1][1]}"
    )


def test_reverting_a_batch_culprit_passes_a_mainline() -> None:
    """#10399 review P1-2: every batch merge is a merge commit by construction.

    ``git revert <merge sha>`` without ``-m`` exits 128 and drops nothing, so
    the one step the doctrine calls non-negotiable silently does not happen.
    """
    reverts = [cmd for cmd in _fenced_commands() if "git revert" in cmd]
    assert reverts, "the doctrine no longer documents dropping a culprit — re-point this guard"
    missing = [cmd for cmd in reverts if "-m 1" not in cmd]
    assert not missing, (
        "a batch merge is a merge commit, so its revert needs a mainline "
        f"(-m 1) or git exits 128; these do not: {missing}"
    )
    # ...and it must actually COMMIT. `-n`/`--no-commit`/`--squash` apply the
    # reverted changes to the worktree and the INDEX without committing, so the
    # batch branch keeps the culprit while the "one more full suite run" — which
    # measures the worktree — certifies a tree the batch does not contain.
    chained = [cmd for cmd in reverts if re.search(r"[;&|]", _unquoted(_code(cmd)))]
    assert not chained, (
        "the revert must be ONE command: a suffix such as `; git switch main` moves "
        "HEAD after the revert commits, and the doctrine's next step — 'one more full "
        f"suite run' — then measures a tree that is not the batch: {chained}"
    )
    uncommitted = [
        cmd for cmd in reverts if re.search(r"(?<!\S)(?:-n|--no-commit|--squash)(?!\S)", cmd)
    ]
    assert not uncommitted, (
        "the revert must COMMIT (`git revert --no-edit -m 1 <merge sha>`): with "
        "`-n`/`--no-commit`/`--squash` the change never lands on the batch branch, "
        f"which then still contains the culprit: {uncommitted}"
    )


def test_the_blame_sha_is_bound_to_a_batch_merge() -> None:
    """the ``a888c6980`` P2: a bisect result can be a commit INSIDE a PR, and the drop is silent.

    ``git bisect`` walks **every** commit in ``origin/main..integration/batch-1`` —
    ``--first-parent`` is opt-in — so in a ``--no-ff`` batch the "exact commit" it reports can be one
    of a PR's own commits. ``git revert --no-edit -m 1 <a non-merge>`` then IGNORES the mainline and
    exits **0** (git-verified), reverting ONE COMMIT of that PR instead of the PR: the rest of it
    still lands unreviewed, and the re-blame rule ("the dropped PR is the one with a matching revert
    line") cannot fire, because the subject becomes ``Revert "PR1 commit 2"``, which matches no
    ``batch: merge PR #<N>`` line. Reproduced in a scratch repo against this doctrine's own shape:
    plain bisect blamed ``PR1 commit 2`` (not a merge) and the revert of it exited 0.

    Both halves are pinned: the bisect is scoped to the batch's merge chain, and the sha handed to
    ``git revert`` is the one whose merge-ness was asserted.
    """
    text = _text()
    assert re.search(r"git\s+bisect\s+start\s+--first-parent\b", text), (
        "`git bisect start` must carry `--first-parent`: without it the blame can stop on a commit "
        "INSIDE a PR, which has no merge to attribute and none to revert"
    )
    enumerations = [
        cmd
        for cmd in _fenced_commands()
        if cmd.startswith("git log")
        and "origin/main..integration/batch-" in cmd
    ]
    assert enumerations, "the doctrine no longer enumerates the batch's merges — re-point this guard"
    for cmd in enumerations:
        assert "--first-parent" in cmd and "--merges" in cmd, (
            "the 'merges ATTEMPTED' enumeration must be "
            "`git log --first-parent --merges …`: the bare form also lists the commits inside each "
            f"PR, so a reader takes a PR-internal sha for a merge: {cmd}"
        )
    reverts = [cmd for cmd in _fenced_commands() if "git revert" in cmd]
    assert reverts, "the doctrine no longer documents dropping a culprit — re-point this guard"
    for cmd in reverts:
        assert '"$BAD"' in cmd or "${BAD}" in cmd, (
            "the revert must target the sha whose merge-ness was ASSERTED (`$BAD`), not an "
            f"independently-typed `<merge sha>`: {cmd}"
        )
    captures = [
        (i, _code(c))
        for i, c in enumerate(_fenced_commands())
        if _READ_ONLY_CAPTURE.match(_code(c))
    ]
    assert captures, (
        "the blame sha is never captured before the return — `BAD=\"$(git rev-parse "
        "refs/bisect/bad)\"` must sit BEFORE `git bisect reset`: `bisect run` leaves HEAD on the LAST "
        "PROBE it tested, not on the culprit, and the reset deletes the ref that holds the answer"
    )
    for fence in _fences():
        codes = [_code(c) for c in fence]
        if not any("git revert" in c for c in codes):
            continue
        asserts = [c for c in codes if _MERGE_ASSERT.match(c)]
        assert asserts, (
            "the fence that drops the culprit carries no merge-ness assertion: ``git revert -m 1`` "
            "on a non-merge exits 0 and reverts one COMMIT of the PR"
        )
        for cmd in asserts:
            assert _halts(cmd, subject=_MERGE_SUBJECT), (
                "the merge-ness assertion must HALT the fence: a failed non-merge check that only "
                f"prints still lets the silent single-commit revert run: {cmd}"
            )
    capture_at = [i for i, c in enumerate(_fenced_commands()) if _READ_ONLY_CAPTURE.match(_code(c))]
    reset_at = [
        i for i, c in enumerate(_fenced_commands()) if _code(c).startswith("git bisect reset")
    ]
    assert reset_at and min(capture_at) < min(reset_at), (
        "the blame sha must be captured BEFORE `git bisect reset`, which deletes `refs/bisect/bad` — "
        "a capture after it records the batch tip, and the revert then drops the wrong thing"
    )


def test_the_gate_run_must_complete_before_any_verdict() -> None:
    """the ``c50628450`` P2: a poll that only says ``DONE`` is not a PASS — a killed run reads as BATCH-GREEN.

    Any liveness probe prints ``DONE`` the moment the process is gone, which is equally true of a run
    the kernel OOM-killed (measured on #10399: five ``python`` processes killed in 23 minutes). A killed
    run writes no summary, so it carries no failure ids; Phase 4 diffs ids, so it certifies `BATCH-GREEN`
    for a tree no run ever finished — and `UNPROVEN-FULL-SUITE` was keyed only on "a subset ran".

    The status therefore has to come from the run's own shell: ``wait`` cannot reach it (the poll is a
    later shell, rc=127), so the launch persists ``$?`` and this test reads that file.
    """
    gates = [
        fence
        for fence in _fences()
        if any("--junitxml=" in _code(c) and "batch-junit" in _code(c) for c in fence)
    ]
    assert gates, "the gate run no longer writes a junit report — re-point this guard"
    # ⛔ PER FENCE, and this is a finding, not tidiness: `codes` used to be the CONCATENATION of every
    # gate's lines, so each assertion below was satisfied by ANY one of them. Measured at the pre-fix
    # head, degrading Phase 3's own predicate to `[ "$RC" -ge 0 ]` — accept any status, including the
    # 137 that means SIGKILL/OOM — left the module GREEN check-for-check (0 of 37), because the
    # post-revert re-check's intact copy satisfied it. That is the c50628450 fail-open the check
    # exists to prevent, restored in the fence the verdict is printed from. Every rule below is owed
    # by EVERY fence that runs the gate and writes the report Phase 4 diffs.
    for fence in gates:
        codes = [_code(c) for c in fence]
        # The status must come from the file the run's OWN shell wrote. `wait` cannot supply it: the
        # poll happens in a LATER shell, where the pid is not a child (measured, bash 5.2: `bash:
        # wait: pid N is not a child of this shell`, rc=127) — so `wait` reported a COMPLETED, green
        # run as unproven.
        rc_files = [m.group("rc") for c in codes if (m := _STATUS_WRAPPER.match(c))]
        assert rc_files, (
            "the gate fence must launch the run in its own shell and persist the status "
            "(`( uv run pytest …; echo $? > /tmp/batch.rc ) &`): `wait` on a pid from a previous "
            "shell returns 127, not the run's status"
        )
        assert any(
            re.fullmatch(rf'RC="\$\(\s*cat\s+"?{re.escape(rc)}"?\s*\)"', c)
            for c in codes
            for rc in rc_files
        ), (
            'the gate fence must read the status the run wrote (`RC="$(cat /tmp/batch.rc)"`): '
            "without a status a killed run is indistinguishable from a finished one (status files: "
            f"{rc_files})"
        )
        status_checks = [c for c in codes if "$RC" in c and "-eq" in c]
        assert status_checks, (
            "no exit-status predicate on `$RC`: pytest exits 0 (passed) or 1 (tests failed) on a "
            "COMPLETED run, and 2/3/4/5 — or 128+N — when it was interrupted, misconfigured or KILLED"
        )
        # NOT a presence-conjunction. `-eq 0` and `-eq 1` merely APPEARING somewhere let an
        # added accepted code through: measured at the previous head,
        #   [ "$RC" -eq 0 ] || [ "$RC" -eq 1 ] || [ "$RC" -eq 137 ] || { echo bad; exit 1; }
        # was GREEN. 137 is the token that line's own comment names (SIGKILL/OOM), so a KILLED
        # run read as COMPLETED — and the count line does not catch it, because it reads the
        # fixed path /tmp/batch-junit.xml, which pytest truncates only in pytest_sessionfinish
        # and which the fence never clears: a killed run leaves the PREVIOUS report intact, so
        # Phase 4 would diff stale failure ids. The predicate must have exactly this shape.
        # The HEAD is matched on the RAW line (quoting `"$RC"` in `_unquoted` would blank the
        # very subject being compared, and was measured to red the honest document); the HALT
        # is matched on the QUOTE-BLANKED line, because a message that only SAYS `exit 1` is
        # `echo`'s 0, not a halt. Without that split, `[ "…" ] || [ "…" ] || { echo "must
        # exit 1"; }` passed — the canonical trap `_QUOTED`'s docstring names, and the exact
        # "refusal that only echoes" class this module's ledger claims to kill.
        # The halt must be REACHABLE from the subject's failure, and the text does not carry
        # reachability: `{ (exit 1); }` exits a CHILD (these fences have no `set -e`), and
        # `{ :; } || { …; exit 1; }` leaves the real halt as dead code — both were measured
        # GREEN, and both leave the fence running past RC=137 into the STALE
        # /tmp/batch-junit.xml (pytest truncates it only in pytest_sessionfinish), so a killed
        # run could still certify BATCH-GREEN for a tree no run has seen. Pin the TAIL: the
        # halt is the LAST thing on the line, and a `{…}` body may hold only echo/printf
        # segments before its final non-zero `exit`.
        _STATUS_RC = _status_head("RC")
        bad_status = [
            c
            for c in status_checks
            if not (
                (head := _STATUS_RC.match(_code(c)))
                and _STATUS_TAIL.match(_unquoted(_code(c))[head.end() :])
            )
        ]
        assert not bad_status, (
            "the status predicate must accept EXACTLY 0 and 1 — any additional accepted code "
            "(137=SIGKILL/OOM, 2=interrupted) certifies a KILLED run as COMPLETED: "
            f"{bad_status}"
        )
        # The SUBJECT must be the comparison, not a message about it: pinning `$BATCH_TOTAL`
        # anywhere in the line was satisfied by the echo INSIDE the halt, so replacing the
        # comparison with `true` left the check green (measured — that mutant survived).
        counts = [c for c in codes if re.match(r'^\[\s+-n\s+"\$BATCH_TOTAL"', c)]
        assert counts, (
            "the gate fence must compare the run's own test COUNT against the baseline's: a killed "
            "run reports fewer (or no) tests, and that is the detector a status code alone can miss"
        )
        for cmd in counts:
            assert '-ge "$BASELINE_TOTAL"' in cmd, (
                f"the count must be compared to the BASELINE total, not merely mentioned: {cmd}"
            )
            assert _halts(cmd, subject=_COUNT_SUBJECT), (
                f"a short count must HALT the fence, or the verdict proceeds on a killed run: {cmd}"
            )
    verdict_rows = [line for line in _text().splitlines() if line.startswith("| `UNPROVEN-FULL-SUITE")]
    assert verdict_rows, "the verdict table no longer carries an UNPROVEN-FULL-SUITE row"
    assert any("did not COMPLETE" in row for row in verdict_rows), (
        "the verdict table's UNPROVEN-FULL-SUITE row must carry the 'did not COMPLETE' clause — a "
        "killed or interrupted run has no failure set to diff and is never BATCH-GREEN "
        f"(row: {verdict_rows})"
    )


def test_the_baseline_count_carries_across_shells() -> None:
    """the ``a12792afe`` P2-1: ``$BASELINE_TOTAL`` was the one value with no carrier.

    Phase 1 computed it with a plain assignment and Phase 3 compared against it — but Phase 3 may run
    in a LATER shell, which the doctrine itself relies on two sections below (the run's status is a
    file, and ``$PINNED`` is re-materialised from the PR body because *landing is a different
    actor*). In that later shell the comparison degrades to ``[ "12345" -ge "" ]``, bash rejects it
    (``integer expression expected``, rc=2), and the ``||`` fires — with the wrong diagnosis
    ("SHORTER than the baseline") on a COMPLETE, green batch. The count halt is the only mechanical
    detector left for a run that finished but reported too few tests, so it has to be readable.

    Producer and consumer are bound here, and the carrier must be cleared before it is written — a
    previous batch's total surviving a killed baseline is the same defect as a stale status file.
    """
    fences = _fences()
    writes = [
        (fence, match.group("path"))
        for fence in fences
        for c in fence
        if (match := _CARRIER_WRITE.match(_code(c)))
    ]
    assert writes, (
        "the baseline total is never written to a file, so it cannot cross a shell boundary: Phase 3 "
        "may be a LATER shell, where `[ \"$BATCH_TOTAL\" -ge \"$BASELINE_TOTAL\" ]` compares against "
        "the empty string (bash rc=2) and the halt reports a complete, green batch as SHORTER than "
        'the baseline. Carry it: `printf \'%s\\n\' "$BASELINE_TOTAL" > /tmp/baseline.total`'
    )
    paths = {path for _fence, path in writes}
    assert len(paths) == 1, f"the baseline total is carried through more than one file: {sorted(paths)}"
    carrier = paths.pop()

    for fence, path in writes:
        assert path == carrier
        codes = [_code(c) for c in fence]
        assert any(
            c.startswith("BASELINE_TOTAL=") and "grep" in c for c in codes
        ), (
            "the carried value must be the BASELINE's own total, computed from its junit report in "
            "the same fence — a carrier written from anywhere else measures a different run"
        )
        cleared = _cleared(codes)
        assert carrier in cleared, (
            f"{carrier} must be CLEARED before the baseline run (`rm -f … {carrier}`): a previous "
            "batch's total survives a killed baseline and Phase 3 then compares against it"
        )
        writes_at = [i for i, c in enumerate(codes) if _CARRIER_WRITE.match(c)]
        report_at = [i for i, c in enumerate(codes) if _BASELINE_JUNIT in c]
        assert report_at and min(writes_at) > min(report_at), (
            "the carrier must be written AFTER the baseline report it is derived from — writing it "
            "first carries the count of a run that has not happened"
        )

    # The consumer: every fence that carries the count halt must BIND the total from that file, and
    # must HALT on an absent carrier rather than degrade to an empty comparison.
    consumers = [
        fence
        for fence in fences
        if any(re.match(r'^\[\s+-n\s+"\$BATCH_TOTAL"', _code(c)) for c in fence)
    ]
    assert consumers, "the gate no longer compares the run's test count — re-point this guard"
    for fence in consumers:
        codes = [_code(c) for c in fence]
        binds = [
            (i, match.group("path"))
            for i, c in enumerate(codes)
            if (match := _CARRIER_BIND.match(c))
        ]
        assert binds, (
            f"the gate fence compares against `$BASELINE_TOTAL` without binding it from {carrier}: "
            'in a later shell that is an empty string (bash rc=2), reported as a SHORT run. Bind it: '
            f'`BASELINE_TOTAL="$(cat {carrier})"`'
        )
        assert {path for _i, path in binds} == {carrier}, (
            f"the gate binds the baseline total from {sorted({p for _i, p in binds})}, but Phase 1 "
            f"wrote {carrier}: the comparison then reads a file nobody produced"
        )
        halt_at = [i for i, c in enumerate(codes) if re.match(r'^\[\s+-n\s+"\$BATCH_TOTAL"', c)]
        assert min(i for i, _p in binds) < min(halt_at), (
            "the count halt runs BEFORE the total is bound from the carrier — it then compares "
            "against whatever `$BASELINE_TOTAL` happened to be, or nothing at all"
        )
        tested = [
            i
            for i, c in enumerate(codes)
            if (match := _CARRIER_TEST.match(c)) and match.group("path") == carrier and _halts(c)
        ]
        assert tested and min(tested) < min(halt_at), (
            f"an ABSENT {carrier} must HALT the gate fence (`[ -s {carrier} ] || {{ …; exit 1; }}`) "
            "and must do so before the count halt: without it the comparison runs against an empty "
            "string, which bash rejects — a fail-closed read is right, a silent one is not"
        )


def test_the_baseline_report_is_cleared_and_its_status_checked() -> None:
    """the ``a12792afe`` P2-2: Phase 1 neither cleared its report nor checked the run COMPLETED.

    Phase 3 does both, with the reason written down: `rm -f /tmp/batch.rc /tmp/batch-junit.xml` —
    "a stale status/report from an earlier run must not decide this one" — and `rc ∈ {0, 1}` because
    "a killed run prints no summary". Phase 1, the phase whose numbers ARE the ledger, did neither:
    `pytest` truncates a junit file only in `pytest_sessionfinish`, so a baseline the kernel killed
    (measured on #10399: five ``python`` processes in 23 minutes) leaves the PREVIOUS batch's report
    at the fixed path, and the `-gt 0` guard passes happily on that stale number. A stale baseline
    both hides `main`'s own red and convicts an innocent PR — the two outcomes this phase exists to
    prevent.
    """
    # ⛔ Scoped by ROLE, never by mention. A fence that merely NAMES the report — Phase 4's id ledger
    # reads it, a sibling's `rm -f` lists it — neither runs the baseline nor owes the baseline's
    # detectors. Mention-scoping is the subject-vs-mention class this module has already closed for
    # `_COUNT_SUBJECT`, `_MERGE_SUBJECT` and `_LANDED_TREE_DIFF`, and here it made an honest fence
    # INEXPRESSIBLE: adding Phase 4's failure-id ledger (round 39) reddened this check with "no
    # baseline suite command writes the report", about a fence that runs nothing at all.
    fences = [
        fence
        for fence in _fences()
        if any(_SUITE_CMD.match(_code(c)) and _BASELINE_JUNIT in _code(c) for c in fence)
    ]
    assert fences, "the baseline run no longer writes a junit report — re-point this guard"
    head = _status_head(_BASELINE_RC)
    for fence in fences:
        codes = [_code(c) for c in fence]
        runs = [i for i, c in enumerate(codes) if _SUITE_CMD.match(c) and _BASELINE_JUNIT in c]
        assert runs, "no baseline suite command writes the report — re-point this guard"
        cleared = _cleared(codes)
        assert any(token.endswith("baseline-junit.xml") for token in cleared), (
            "the baseline must CLEAR its junit report before the run "
            "(`rm -f \"$B/baseline-junit.xml\"`): "
            "pytest truncates it only in `pytest_sessionfinish`, so a baseline the kernel killed "
            "leaves the PREVIOUS batch's report there and the count reads it as THIS baseline"
        )
        clear_at = [i for i, c in enumerate(codes) if re.search(r"rm\s+-f\s+.*baseline-junit", c)]
        assert min(clear_at) < min(runs), (
            "the report is cleared AFTER the suite line, so it guards nothing"
        )
        captures = [i for i, c in enumerate(codes) if _BASELINE_RC_CAPTURE.match(c)]
        assert captures, (
            "the baseline must capture the run's exit status (`BASELINE_RC=$?` on the line after the "
            "suite): `pytest` exits 0/1 on a COMPLETED run and 2/3/4/5 — or 128+N — when it was "
            "interrupted, misconfigured or KILLED, and a killed run reports no summary, so nothing "
            "else distinguishes it"
        )
        assert min(captures) > min(runs), (
            "the exit-status capture sits BEFORE the suite run, so it captures something else"
        )
        preds = [c for c in codes if f'"${_BASELINE_RC}"' in c and "-eq" in c]
        assert preds, (
            f"no exit-status predicate on `${_BASELINE_RC}`: a status that is captured and never "
            "checked leaves a KILLED baseline reading as a finished one"
        )
        bad = [
            c
            for c in preds
            if not (
                (matched := head.match(c))
                and _STATUS_TAIL.match(_unquoted(c)[matched.end() :])
            )
        ]
        assert not bad, (
            "the baseline status predicate must accept EXACTLY 0 and 1 — any additional accepted "
            "code (137=SIGKILL/OOM, 2=interrupted) certifies a KILLED baseline as COMPLETED: "
            f"{bad}"
        )


#: A `-m track_b` run's OWN status capture, whatever the doctrine calls it. Named separately from
#: ``_BASELINE_RC_CAPTURE`` because this rule is per-FENCE: half 2 sits in the same fence as half 1,
#: so the variable it captures has to be the one the 0/1 predicate carries.
_TRACK_B_RC_CAPTURE = re.compile(r"^(?P<var>[A-Z][A-Z0-9_]*_RC)=\$\?$")


#: Half 2's SKIP axis — the THIRD detector, in the two shapes the doctrine spells for half 1.
#: At ``9f8e12192`` half 2 had a report and a status predicate and **no skip detector at all**:
#: ``$B/batch-trackb.xml`` and ``$B/baseline-trackb.xml`` were written and opened only as id sources
#: in the Phase 4 diff. That is reachable on an ordinary PR — ``tests/test_ingest_safety.py``
#: decorates all four of its tests with ``TRACK_B_SKIP``, which fires when ``promote_source``/
#: ``promote_point`` or the A9 sentinel is gone, so a constituent PR removing either flips the whole
#: MUST-PASS half to SKIPPED on the batch tree while the baseline still runs it: rc is 0 (pytest has
#: no all-skip exit code), ``tests=`` is untouched, a skip carries no failure id, and ``BATCH-GREEN``
#: is printed for a tree on which a ``python-ci-gate`` member never ran. Measured: that tree left
#: this module GREEN check-for-check. CI fails closed on exactly this class
#: (``.github/workflows/python-ci.yml``'s skip-fail guard, ``tools/skip-guard.py``).
_TRACK_B_SKIP_COUNT_SRC = re.compile(
    r"grep\s+-o\s+'skipped=\"\[0-9\]\*\"'\s+\"?(?P<path>" + _LEDGER + r')"?'
)
_TRACK_B_SKIP_REASONS_SRC = re.compile(
    r"grep\s+-o\s+'<skipped[^']*'\s+\"?(?P<path>"
    + _LEDGER
    + r')\"?.*\bsort\s*>\s*"?(?P<out>'
    + _LEDGER
    + r')"?$'
)
_TRACK_B_SKIP_WRITE = re.compile(
    r'^printf\s+\S+\s+"?(?P<var>\$[A-Z][A-Z0-9_]*)"?\s*>\s*"?(?P<path>' + _LEDGER + r')"?$'
)
_TRACK_B_SKIP_BIND = re.compile(
    r'^(?P<var>BASELINE_[A-Z0-9_]*)="?\$\(\s*cat\s+"?(?P<path>' + _LEDGER + r')"?\s*\)"?$'
)
#: The half-2 count comparison's own SUBJECT — the general form of ``_SKIP_COUNT_SUBJECT``, which is
#: pinned to half 1's variable names. Both halves' names must stay distinguishable, or the two skip
#: axes collapse into one and the report half 2 writes is once again opened by nothing.
_TRACK_B_SKIP_COUNT_SUBJECT = re.compile(
    r'\[\s*"(?P<batch>\$BATCH_[A-Z0-9_]*SKIPPED)"\s+-le\s+"(?P<base>\$BASELINE_[A-Z0-9_]*SKIPPED)"\s*\]'
)


def _track_b_skip_axis_misses(
    codes: list[str], run_at: int, report: str
) -> list[tuple[str, str]]:
    """Is THIS half-2 run's SKIP axis instrumented the way half 1's is?

    Returns ``[(what, why)]`` — empty means yes. The junit path the run writes decides which half
    of the axis the fence owes: a ``baseline-trackb`` report is RECORDED (count + reason multiset,
    cleared first), a ``batch-trackb`` report is DIFFED against that record (absent-carrier halt,
    ``-le`` count halt, ``comm -13`` reason diff with its own non-empty halt).
    """
    misses: list[tuple[str, str]] = []
    counting = [
        (m.group("path"), c)
        for c in codes
        if (m := _TRACK_B_SKIP_COUNT_SRC.search(c)) and m.group("path") == report
    ]
    recording = report.endswith("baseline-trackb.xml")
    if not counting:
        return [
            (
                "count",
                f"no skip count is read from {report}: a skipped `track_b` test is still COLLECTED, "
                "carries no failure id and exits 0, so the status predicate and the id diff are "
                "both blind to it",
            )
        ]
    var = re.match(r'^(?P<var>[A-Z][A-Z0-9_]*)="?\$\(', counting[0][1])
    var_name = var.group("var") if var else ""
    written = [
        m.group("path")
        for c in codes
        if (m := _TRACK_B_SKIP_WRITE.match(c))
        and var_name
        and m.group("var") == f"${var_name}"
    ]
    reasons = [
        (m.group("path"), m.group("out"))
        for c in codes
        if (m := _TRACK_B_SKIP_REASONS_SRC.search(c)) and m.group("path") == report
    ]
    if recording:
        if not written:
            misses.append(
                (
                    "record",
                    f"{report}'s skip count is computed and never written to a carrier, so no later "
                    "phase can diff it",
                )
            )
        if not reasons:
            misses.append(
                (
                    "reasons",
                    f"{report}'s skip REASONS are not recorded: an equal COUNT can be a SWAP (one "
                    "`test_ingest_safety` skip traded for another), and only the multiset sees it",
                )
            )
        cleared = _cleared(codes)
        for path in written + [out for _p, out in reasons]:
            if path not in cleared:
                misses.append(
                    (
                        "clear",
                        f"{path} is never cleared: pytest truncates a junit file only in "
                        "`pytest_sessionfinish`, so a killed baseline keeps the PREVIOUS run's "
                        "skips at that path and Phase 3 diffs against them",
                    )
                )
                continue
            clear_at = [
                i for i, c in enumerate(codes) if re.search(rf"rm\s+-f\s+.*{re.escape(path)}", c)
            ]
            if clear_at and min(clear_at) > run_at:
                misses.append(("clear", f"{path} is cleared AFTER the half-2 run, so it guards nothing"))
        return misses
    # …and the GATE side is the baseline-DIFF, asserted in this fence and AFTER the run it judges.
    binds = [
        (m.group("var"), m.group("path"))
        for c in codes
        if (m := _TRACK_B_SKIP_BIND.match(c))
    ]
    base_var = next((v for v, path in binds if path.endswith("trackb.skipped")), "")
    if not base_var:
        misses.append(
            (
                "bind",
                f"the gate fence never BINDS the baseline's half-2 skip count from a carrier: "
                f"without it the comparison runs against an empty string — binds={binds}",
            )
        )
    absent = [
        i
        for i, c in enumerate(codes)
        if (m := _CARRIER_TEST.match(c)) and m.group("path").endswith("trackb.skipped")
    ]
    if not absent or not all(_halts(codes[i], subject=lambda s: bool(_CARRIER_TEST.match(s))) for i in absent):
        misses.append(
            (
                "absent-carrier halt",
                "an ABSENT baseline half-2 skip carrier must HALT the gate fence: `-le` on an empty "
                "value is a silent pass",
            )
        )
    elif base_var and min(absent) > min(
        i for i, c in enumerate(codes) if base_var in c
    ):
        misses.append(("absent-carrier halt", "the absent-carrier halt must precede the bind it protects"))
    subjects = [
        c
        for c in codes
        if base_var
        and "TRACK_B" in c
        and (m := _TRACK_B_SKIP_COUNT_SUBJECT.search(c))
        and m.group("base") == f"${base_var}"
    ]
    if not subjects:
        misses.append(
            (
                "count halt",
                f"no `[ \"$BATCH_…\" -le \"${base_var or 'BASELINE_…'}\" ]` comparison of half 2's "
                "skip count against the baseline's",
            )
        )
    elif not all(_halts(c) for c in subjects):
        misses.append(("count halt", f"the half-2 skip comparison does not halt: {subjects}"))
    if not any(
        _SKIP_REASON_DIFF.match(c) and "trackb" in c for c in codes
    ):
        misses.append(
            (
                "reason diff",
                "half 2's skip REASONS are never `comm -13`-diffed against the baseline's, so an "
                "equal count that SWAPPED a skip is invisible",
            )
        )
    if not any(
        _SKIP_REASON_HALT.match(c) and "trackb" in c for c in codes
    ):
        misses.append(
            (
                "reason halt",
                "no halt on a NEW half-2 skip reason: a test that must run did not, and no failure "
                "id records it",
            )
        )
    if absent:
        # Every half-2 skip detector must sit AFTER the half-2 run it judges, or it reads the
        # PREVIOUS run's report — the same later-shell hazard the report clear exists for.
        after = [
            i
            for i, c in enumerate(codes)
            if (
                _SKIP_REASON_HALT.match(c)
                or _SKIP_REASON_DIFF.match(c)
                or _TRACK_B_SKIP_COUNT_SUBJECT.search(c)
            )
            and "trackb" in c
        ]
        if any(i < run_at for i in after):
            misses.append(
                ("order", "a half-2 skip detector sits BEFORE the half-2 run it judges, so it reads the previous run")
            )
    return misses


def test_the_track_b_half_is_completion_checked_like_the_deterministic_half() -> None:
    """the ``bc5d78b77`` P2 — still open at ``a4028e8d7``: half 2 is instrumented in NEITHER phase.

    ``-m track_b`` is a MUST-PASS ``needs:`` of the required ``python-ci-gate``, and the doctrine
    says so itself — then runs it bare. Two fail-open paths follow, both mechanical:

    1. **Its ids reach neither side of the Phase 4 diff.** Half 2 writes no junit report, so a
       composition-introduced ``track_b`` red is in the baseline id set and the batch id set alike
       — absent from both — and ``BATCH-GREEN`` stays reachable on a tree whose gate member is red.
    2. **A killed half 2 is indistinguishable from a finished one.** ``kill -0``-style liveness and
       a bare foreground run both succeed for a process the kernel SIGKILLed (measured on #10399:
       five ``python`` processes in 23 minutes), and no status is captured, so nothing separates
       them.

    Half 1 got both treatments (``test_the_gate_run_must_complete_before_any_verdict``,
    ``test_the_baseline_report_is_cleared_and_its_status_checked``); the half whose whole purpose is
    the gate member did not. Pinned here for every fence that runs it: a junit artifact, cleared
    before the run, a status captured after it, and a predicate that accepts EXACTLY 0 and 1.

    **…and the third detector, which the ``9f8e12192`` review found still missing (P1).** Half 2's
    report was written and opened only as an id source, so half 2 had NO skip check at all — and a
    SKIP is invisible to both detectors above: it is collected (``tests=`` unchanged), it adds no
    failure id (the Phase 4 diff is empty) and pytest exits 0. ``tests/test_ingest_safety.py``
    decorates all four of its tests with ``TRACK_B_SKIP``, so a constituent PR that removes
    ``promote_source``/``promote_point`` or the A9 sentinel flips the whole MUST-PASS half to
    SKIPPED while the baseline still runs it, and ``BATCH-GREEN`` is printed for a tree on which a
    ``python-ci-gate`` member never ran. Measured at that head: GREEN check-for-check. So every fence that
    runs half 2 also owes the skip axis, in the same two shapes half 1 spells — the BASELINE recording
    (count + reason multiset, cleared first) and the gate's baseline-DIFF (absent-carrier halt, ``-le``
    count halt, ``comm -13`` reason diff with its own halt).
    """
    fences = [
        fence
        for fence in _fences()
        if any(_SUITE_CMD.match(_code(c)) and TRACK_B_FILTER in _code(c) for c in fence)
    ]
    assert fences, "the doctrine no longer runs the `-m track_b` half — re-point this guard"
    for fence in fences:
        codes = [_code(c) for c in fence]
        runs = [i for i, c in enumerate(codes) if _SUITE_CMD.match(c) and TRACK_B_FILTER in c]
        for run_at in runs:
            report = re.search(r'--junitxml="?(' + _LEDGER + r')"?', codes[run_at])
            assert report, (
                "the `-m track_b` run writes no junit report: its failure ids then reach NEITHER "
                "side of the Phase 4 diff, so a red MUST-PASS gate member cannot be classified "
                f"BASELINE or NEW and the verdict reads green: {codes[run_at]}"
            )
            path = report.group(1)
            cleared = _cleared(codes)
            assert path in cleared, (
                f"the `-m track_b` report ({path}) is never cleared: pytest truncates a junit file "
                "only in `pytest_sessionfinish`, so a killed half 2 leaves the PREVIOUS run's report "
                "at that path and it is read as this run's ids"
            )
            clear_at = [
                i for i, c in enumerate(codes) if re.search(rf"rm\s+-f\s+.*{re.escape(path)}", c)
            ]
            assert min(clear_at) < run_at, (
                f"the `-m track_b` report ({path}) is cleared AFTER the run, so it guards nothing"
            )
            after = [
                i
                for i, c in enumerate(codes)
                if i > run_at and _TRACK_B_RC_CAPTURE.match(c)
            ]
            assert after, (
                "the `-m track_b` run captures no exit status: pytest exits 0/1 on a COMPLETED run "
                "and 2/3/4/5 — or 128+N — when it was interrupted or KILLED, so without a capture a "
                "half the kernel killed is indistinguishable from a finished one"
            )
            var = _TRACK_B_RC_CAPTURE.match(codes[min(after)]).group("var")  # type: ignore[union-attr]
            preds = [c for c in codes if f'"${var}"' in c and "-eq" in c]
            assert preds, (
                f"no exit-status predicate on `${var}`: a `track_b` status that is captured and "
                "never checked leaves a KILLED half 2 reading as a finished one"
            )
            head = _status_head(var)
            bad = [
                c
                for c in preds
                if not (
                    (matched := head.match(c))
                    and _STATUS_TAIL.match(_unquoted(c)[matched.end() :])
                )
            ]
            assert not bad, (
                "the `track_b` status predicate must accept EXACTLY 0 and 1 — any additional "
                "accepted code (137=SIGKILL/OOM, 2=interrupted) certifies a KILLED half 2 as "
                f"COMPLETED: {bad}"
            )
            misses = _track_b_skip_axis_misses(codes, run_at, path)
            assert not misses, (
                "half 2's SKIP axis is not instrumented the way half 1's is: "
                + "; ".join(f"[{what}] {why}" for what, why in misses)
            )


def test_the_baseline_refuses_a_dirty_tree() -> None:
    """the ``e715d81b2`` P3: ``git switch -c`` carries uncommitted and untracked files.

    ``git switch -c <branch> <commit>`` re-applies local modifications to the new
    tree (it refuses only where they differ between the two branches) and always
    carries untracked files — so in this fleet's normal state (a lane worktree with
    unreviewed changes) the fence headed "run the suite on ``origin/main`` ALONE"
    measures the lane. The clean-tree precondition is the other half of the fix.
    """
    fences = _fences()
    first = _fence_index(lambda fence: any(_SUITE_CMD.match(c) for c in fence))
    assert first >= 0, "no baseline fence found — re-point this guard"
    baseline = fences[first]

    # A STALE base is as bad as a dirty tree: this fence's `PINNED` is the ledger
    # every later verdict is diffed against. `--dry-run`, `|| true` and a stale
    # refspec all leave `origin/main` unmoved, so `PINNED` is the OLD main and the
    # baseline is measured on a tree that is not current `origin/main`.
    fetches = [
        i
        for i, c in enumerate(baseline)
        if _is_main_fetch(c)
    ]
    pins = [
        i
        for i, c in enumerate(baseline)
        if _PIN_DEF.match(re.split(r";|&&|\|\|", _code(c))[0].strip())
    ]
    # ...and the SOURCE of the pin, not just its name: `git rev-parse HEAD` is one
    # argument away and makes `$PINNED` the LANE's tip, so the baseline measures the
    # lane under a heading promising `origin/main` ALONE. The ledger every later
    # verdict is diffed against is this one line.
    pinned_main = [
        _code(baseline[i])
        for i in pins
        if re.fullmatch(
            r"(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)*|typeset\s+(?:-\w+\s+)*)?"
            r'PINNED="?\$\(git\s+rev-parse\s+origin/main\)"?',
            _code(baseline[i]),
        )
    ]
    assert len(pins) == 1 and pinned_main, (
        "the baseline pin must BE `PINNED=\"$(git rev-parse origin/main)\"`: any "
        "other source (`git rev-parse HEAD`, `main`, `origin/main~1`) names a tree "
        f"that is not origin/main while every other check here stays green: "
        f"{[baseline[i] for i in pins]}"
    )
    assert fetches and pins and max(fetches) < min(pins), (
        "the baseline must fetch origin (a plain `git fetch origin [main]`) BEFORE "
        "it pins $PINNED: a stale remote-tracking ref makes the pin the OLD main, "
        f"and the baseline — the ledger — is then measured on the wrong base. "
        f"fetches={[baseline[i] for i in fetches]}, pins={[baseline[i] for i in pins]}"
    )
    # A MENTION is not a refusal — `[ -z "$(git status --porcelain)" ]` alone,
    # `|| true`, and an `echo '[ -z … ] || exit 1'` all "contain" the tokens — and
    # a refusal AFTER the suite refuses nothing. Both are pinned: the command must
    # BE the test with a non-zero halt, and it must precede the first suite line.
    for fence in fences:
        run = [i for i, c in enumerate(fence) if _SUITE_CMD.match(c)]
        if not run:
            continue
        dirty = [i for i, c in enumerate(fence) if "git status --porcelain" in _code(c)]
        assert dirty, (
            "every fence that runs the full suite must refuse a dirty tree: "
            f"`git switch -C` carries uncommitted and untracked files into the tree "
            f"under test, so the run measures the lane. This one does not: {fence[:3]}"
        )
        refused = [i for i in dirty if _is_refusal(fence, i) and i < min(run)]
        assert refused, (
            "the dirty-tree precondition must be an EXECUTED refusal (`[ -z \"$(git "
            "status --porcelain)\" ] || { …; exit 1; }`) positioned BEFORE the suite "
            f"— this fence only mentions it, halts nothing, or checks it too late: "
            f"{[fence[i] for i in dirty]}"
        )

    # The idempotency half (final-head review P2). A RE-baseline is the normal
    # case — Landing route 1 re-runs phases 1 and 3 — and a one-shot create
    # refuses an existing branch name, after which a fence without `set -e`
    # keeps going and the pytest lines measure whatever tree HEAD is on. Any
    # re-runnable spelling satisfies this; the one-shot form does not.
    moves = [c for c in baseline if _MOVES_HEAD.search(_code(c)) and "$PINNED" in _code(c)]

    # ...and HEAD must still be on the pin when the suite STARTS. A reorder of
    # this fence's own trailing `git switch -` (or any stray `git switch main`)
    # between the pin switch and the first pytest line satisfies every check
    # above while the suite measures the operator's lane — verbatim the P1 this
    # file exists to prevent.
    run = [i for i, c in enumerate(baseline) if _SUITE_CMD.match(c)]
    assert run, "the baseline fence no longer runs the suite — re-point this guard"
    before = [c for c in baseline[: min(run)] if _MOVES_HEAD.search(_code(c))]
    assert all('"$PINNED"' in _code(c) and _re_runnable(_code(c)) for c in before), (
        "every HEAD-moving command before the baseline suite must switch to "
        f"$PINNED; these do not: {before}"
    )
    assert moves, "the baseline fence no longer switches to the pinned base — re-point this guard"
    assert all(_ends_at_halt(_code(c)) for c in moves), (
        "a pin create must END at its halt: `… || exit 1 && git switch -` parses "
        "as `(A || exit 1) && …`, so the halt never fires and the fence leaves the "
        f"pinned tree before the suite: {moves}"
    )
    assert all(_halts(_code(c), subject=_pin_subject) for c in moves), (
        "a pin create must HALT on failure (`|| exit 1`): unhalted, a refused "
        f"switch leaves the fence running on the operator's branch: {moves}"
    )
    assert all(_re_runnable(_code(c)) for c in moves), (
        "a re-baseline must be re-runnable: a one-shot create dies with 'a branch "
        "named ... already exists', and the fence then measures the wrong tree; "
        f"these moves are one-shot: {moves}"
    )


def test_the_batch_fence_rebuild_is_re_runnable() -> None:
    """P1 (final-head review): the batch fence had the same defect, worse blast radius.

    On a rebuild a one-shot create of the batch branch fails, and the following
    ``--no-ff`` merge then lands a foreign PR head into whatever branch the
    operator is standing on. ``-C``/``-B``/``--detach`` reset to the pin, which
    is the intent of a rebuild.

    The ``7fb722a9a`` P1 (the same rebuild class, one line down): the reviewed-head
    fetch REUSES ``refs/tmp/pr-<N>``, so a rewritten PR head makes the update a
    non-fast-forward that git refuses without ``--force``/``+``, and the fence merged
    the stale ref because that line carried no halt.
    """
    fences = _fences()
    batch = [
        fence for fence in fences
        if any(
            "batch-1" in _code(c) and _MOVES_HEAD.search(_code(c)) and "$PINNED" in _code(c)
            for c in fence
        )
    ]
    assert batch, "the batch-assembly fence no longer creates batch-1 from the pin — re-point this guard"
    flat = [
        c for fence in batch for c in fence
        if _MOVES_HEAD.search(_code(c)) and "$PINNED" in _code(c)
    ]
    assert flat, (
        "no re-runnable-spelling check target — a trailing comment nominated the "
        "fence, and all([]) would pass vacuously"
    )
    assert all(_ends_at_halt(_code(c)) for c in flat), (
        "the batch pin create must END at its halt; a suffix such as "
        f"`&& git switch -` leaves the merges on another branch: {flat}"
    )
    assert all(_re_runnable(_code(c)) for c in flat), (
        "rebuilding the batch must reset the branch to the pin; a one-shot create "
        f"leaves the following --no-ff merge on the wrong branch: {flat}"
    )

    # P1 (the ``7fb722a9a`` review): the reviewed-head fetch is REUSED across rebuilds, so a
    # head that was rewritten since the last run makes the update to `refs/tmp/pr-<N>` a
    # non-fast-forward, which git REFUSES — silently, because the fetch is one command with no
    # separator and its `|| exit 1` was absent. The fence then merged the PREVIOUS head under a
    # comment reading "exactly the reviewed head". Both halves are pinned: the OVERRIDE and the
    # halt, on the refspec that names a PR head.
    fetches = [c for fence in batch for c in fence if _is_pr_head_fetch_segment(_code(c))]
    assert fetches, (
        "the batch fence no longer fetches a reviewed PR head (`git fetch … pull/<N>/head:…`) "
        "— re-point this guard"
    )
    for command in fetches:
        assert _reviewed_head_fetch(command), (
            "the reviewed-head fetch must be ONE command, FORCE the update (`--force` or a "
            "`+pull/<N>/head:` refspec — git refuses a non-fast-forward update to the reused "
            "local ref) and HALT on refusal: without the halt a refused fetch leaves the ref "
            "at the PREVIOUS head and the merge below lands it silently. got: "
            f"{command}"
        )

    # EVERY line of the assembly fence must be ONE command (or end at a halt): a
    # suffix on the merge line — `git merge … refs/tmp/pr-<N> && git switch main` —
    # puts the second and every later merge on the operator's local `main`, so the
    # gate measures a tree the branch does not contain. The merge line also carries a
    # `#` inside its message, which a quote-blind `_code` used to hide.
    for fence in batch:
        for command in fence:
            code = _code(command)
            # NOT `or _halts(code)`: that is a PRESENCE predicate, so a mover
            # followed by a legitimate `|| exit 1` would be exempt — the mover has
            # already run by the time the halt fires, which is the round-26 P1 one
            # `|| exit 1` away from its own fix. The reviewed-head fetch, the merge and
            # its `HEAD^2` assert are the exemptions, and none is a presence predicate:
            # each pins the whole shape (one command that halts).
            if (
                _ends_at_halt(code)
                or _reviewed_head_fetch(code)
                or _is_halted_batch_merge(code)
                or _is_merge_commit_assert(code)
            ):
                continue
            assert not re.search(r"[;&|]", _unquoted(code)), (
                "every line of the batch-assembly fence must be one command: a "
                f"separator here lands later merges off the batch branch: {command}"
            )

    # ...and the batch branch must still be the one checked out when the FIRST
    # merge runs. A HEAD-moving line carrying no `$PINNED` is invisible to the
    # checks above: `git switch main` inserted after the pin create leaves every
    # merge landing on the operator's local `main` while integration/batch-1
    # stays at the pin — the same silent wrong-tree the baseline fence's position
    # check closes on the other fence.
    fence = next(f for f in batch if any("batch-1" in _code(c) for c in f))
    merges = [i for i, c in enumerate(fence) if re.search(r"\bgit\s+merge\b", c)]
    assert merges, "the batch fence no longer merges anything — re-point this guard"
    before_merge = [c for c in fence[: min(merges)] if _MOVES_HEAD.search(_code(c))]
    assert all(
        '"$PINNED"' in _code(c)
        and _re_runnable(_code(c))
        and _halts(_code(c), subject=_pin_subject)
        and _ends_at_halt(_code(c))
        for c in before_merge
    ), (
        "every HEAD-moving command before the batch's first merge must be the pin "
        f"create; these move HEAD off the batch branch: {before_merge}"
    )


def test_every_pin_reading_fence_halts_its_origin_main_fetch() -> None:
    """the ``9f8e12192`` P2: a bare ``git fetch origin main`` makes the pin read a STALE ref.

    ``git rev-parse origin/main`` reads the LOCAL remote-tracking ref. A ``git fetch`` that fails —
    network, auth, DNS — exits non-zero and leaves that ref at the **last successful fetch**, which
    in the normal flow is the base the batch was pinned to. Without a halt the fence continues, so

    * in Phase 1 the baseline records a tree that is not current ``main``: ``main``'s own new red is
      absent from the baseline id set, Phase 4 classifies it ``NEW``, and an innocent PR is dropped;
    * in Landing the ``[ "$(git rev-parse origin/main)" = "$PINNED" ]`` assertion **PASSES against a
      moved ``main``**, the batch is un-drafted, and the queue squash-merges an untested composition
      onto it — the untested-tree landing this whole section exists to prevent, certified by its own
      fence with every other line green.

    ``AGENTS.md`` names this exact failure ("a stale sha yields a false green"). The document already
    halts the two fetches it repaired (the reviewed PR head, the post-merge detector); these two were
    left bare, and the pre-fix locators could not even see the repair — a ``re.fullmatch`` on the bare
    command matched the bug and dropped the fix.

    Enumerated over FENCES rather than over the two known sites, so a third pin-reading fence cannot
    arrive without its fetch halted.
    """
    pin_readers = [
        fence
        for fence in _fences()
        if any(_PINNED_MAIN_ASSERTION.search(_code(c)) for c in fence)
        or any(
            "PINNED=" in _code(c) and "origin/main" in _code(c) for c in fence
        )
    ]
    assert len(pin_readers) >= 2, (
        "expected at least the Phase 1 baseline fence and the Landing fence to read origin/main for a "
        f"pin, parsed {len(pin_readers)} — re-point this guard"
    )
    for number, fence in enumerate(pin_readers, start=1):
        codes = [_code(c) for c in fence]
        fetches = [i for i, c in enumerate(codes) if _is_main_fetch(c)]
        assert fetches, (
            f"pin-reading fence {number} reads `origin/main` with no `git fetch origin main`: the "
            "value is the LOCAL remote-tracking ref, so a moved `main` compares equal to the pin"
        )
        unhalted = [fence[i] for i in fetches if not _main_fetch_halts(fence[i])]
        assert not unhalted, (
            f"pin-reading fence {number} has an UNHALTED `git fetch origin main`: a failed fetch "
            "leaves the ref at the last successful one, so the pin/assertion reads a stale base and "
            "passes — the false-green `AGENTS.md` names. Halt it: "
            '`git fetch origin main || { echo "…"; exit 1; }`: ' + repr(unhalted)
        )


def test_landing_asserts_main_is_still_the_pinned_base() -> None:
    """#10399 review P1-3 / P2-1: the tested tree must be the landed tree.

    bors fast-forwards ``main`` to the staging branch it tested and GitHub's
    queue validates against "the latest version of the target branch" — both get
    the property by OWNING the merge. Here ``.mergify.yml`` keeps merging to
    ``main`` while a batch runs, so the base must be re-checked mechanically at
    landing time, or the invariant is a sentence rather than a property.

    And the check must be SELF-CONTAINED: ``$PINNED`` is set in Phase 1's shell,
    while landing "is a separate, human-routed step" in a different shell — so
    the landing fence has to materialise ``PINNED`` itself, from the batch PR
    body Phase 1 wrote it to.
    """
    fences = _fences()
    landing = [fence for fence in fences if _PINNED_MAIN_ASSERTION.search("\n".join(fence))]
    assert landing, (
        "the doctrine must carry the landing-time base assertion "
        '`[ "$(git rev-parse origin/main)" = "$PINNED" ]` as an executable '
        "command; without it a batch can land a tree no run has seen"
    )
    # Count ASSIGNMENTS, not physical lines: `PINNED="$(gh pr view …)"; PINNED="$
    # (git rev-parse origin/main)"` is one line with two assignments, and the
    # second one makes the assertion compare `main` to itself. The prefix set is
    # every declarator bash accepts, since each one misses a narrower anchor.
    defines = [
        segment.strip()
        for cmd in landing[0]
        for segment in re.split(r";|&&|\|\|", _code(cmd))
        if _PIN_DEF.match(segment.strip())
    ]
    # EXACTLY ONE definition, and it must READ THE PR BODY: `any()` let a second
    # definition rewrite the pin — appending `PINNED="$(git rev-parse origin/main)"`
    # makes the assertion compare `main` to itself (always true) and makes the
    # `[ -n "$PINNED" ]` fail-closed guard inert; `${PINNED:-$(git rev-parse
    # origin/main)}` turns that fail-closed path fail-open.
    assert len(defines) == 1, (
        "the landing fence must define $PINNED exactly once, from the batch PR "
        f"body; these definitions rewrite it: {defines}"
    )
    assert not re.search(r";|&&|\|\|", defines[0]) and re.fullmatch(
        r"(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)*|typeset\s+(?:-\w+\s+)*)?"
        r"PINNED=\"\$\(gh pr view .+ --json body "
        r"(?:--jq '\.body'|--template '\{\{\.body\}\}')\s*\|\s*"
        r"sed -n 's/\^pinned-base: //p'\)\"",
        defines[0],
    ), (
        "the landing pin must BE the PR-body read, in one substitution — a "
        "fallback (`… || git rev-parse origin/main`) or a discarded read "
        "(`… >/dev/null; git rev-parse origin/main`) makes $PINNED the CURRENT "
        f"main, so the assertion compares main to itself: {defines[0]}"
    )
    assert "gh pr view" in defines[0], (
        "the landing pin must be read from the batch PR body Phase 1 recorded it "
        f"to — a value taken from the local repo tests `main` against itself: {defines[0]}"
    )
    # ...and the polarity: `[ -z "$PINNED" ]` refuses every real sha, so landing
    # becomes impossible — the same class as the `--jq -r` defect that made the
    # check unpassable.
    empty_guards = [
        c for c in landing[0] if re.match(r'\[\s*-z\s+"\$PINNED"\s*\]', _code(c))
    ]
    assert not empty_guards, (
        'the landing fence must not guard the pin with `[ -z "$PINNED" ]`: a `-z` '
        f"guard refuses every real sha, so the landing step can never pass: {empty_guards}"
    )
    # gh has no raw (`-r`) flag: `--jq -r '.body'` makes `-r` the EXPRESSION and
    # `.body` a second positional argument, so the payload is never filtered and
    # `$PINNED` comes out empty — on gh 2.102.0 it fails outright with
    # "accepts at most 1 arg(s), received 2". The landing step must be runnable.
    confused = [cmd for cmd in defines if "--jq -r" in cmd or "--jq=-r" in cmd]
    assert not confused, (
        "the landing pin is read with `--jq -r`, which gh does not have (that is "
        "jq's flag): `-r` is taken as the expression and `.body` as a second "
        f"argument, so the body is never extracted and $PINNED is empty: {confused}"
    )
    # The assertion the doctrine calls "the only mechanical enforcement of the
    # tested-tree==landed-tree invariant" must STOP the fence, not merely return
    # 1: the landing fence has no `set -e`, so an unhalted `[ … ]` is discarded
    # and the operator merges onto a moved `main` — the document's headline
    # failure, certified green by its own guard.
    # An anchored ALLOWLIST on the carrier, not a blacklist of prefixes. A
    # blacklist is keyed on how the line STARTS, so any prefix it does not name
    # carries the assertion while inverting it and is invisible here — measured:
    # `if [ … = "$PINNED" ]; then … exit 1; fi` refuses an UNMOVED main and lands
    # a MOVED one (the document's headline failure), and `bash -c '…'`,
    # `{ ! [ … ]; }`, `command [ … ]` and `sh -c '…'` all do the same, all green.
    # A STALE remote-tracking ref compares equal to itself: without a fetch, a
    # `main` that moved since the last one satisfies the assertion, which then
    # certifies a tree no run has seen.
    # WHOLE-COMMAND allowlist: `git fetch origin main || true`, `--dry-run`, and
    # `origin main:refs/remotes/origin/stale` all start with `git fetch` and
    # contain `origin` while leaving the ref STALE — which then compares equal to
    # the pin, certifying a tree no run has seen.
    fetches = [
        i
        for i, c in enumerate(landing[0])
        if _is_main_fetch(c)
    ]
    assert fetches, (
        "the landing fence must fetch origin before comparing: `$(git rev-parse "
        "origin/main)` reads the LOCAL remote-tracking ref, so without a fetch a "
        "moved main compares equal and the assertion passes"
    )
    carriers = [
        i for i, c in enumerate(landing[0]) if _PINNED_MAIN_ASSERTION.search(_code(c))
    ]
    assert carriers and min(fetches) < min(carriers), (
        "the fetch must precede the base assertion; these assertions read a ref "
        "no fetch in this fence has refreshed"
    )
    wrapped = [
        landing[0][i]
        for i in carriers
        if not re.match(
            r'^\[\s*"\$\(git\s+rev-parse\s+origin/main\)"\s*=\s*"\$PINNED"\s*\]\s*\|\|',
            _code(landing[0][i]),
        )
    ]
    assert not wrapped, (
        "the landing assertion must BE the guard (`[ … = \"$PINNED\" ] || { …; "
        "exit 1; }`) — not wrapped (`if [ … ]`, `bash -c '…'`, `{ … }`, "
        "`command …`) and not negated: a wrapper carries the assertion while "
        f"inverting or unreaching it: {wrapped}"
    )
    assert carriers, (
        "the landing fence carries no executable base assertion — re-point this guard"
    )
    unhalted = [
        landing[0][i]
        for i in carriers
        if not _halts(_block(landing[0], i), subject=_PINNED_MAIN_ASSERTION)
    ]
    assert not unhalted, (
        "every landing guard must halt on failure (`|| { …; exit 1; }` — `|| true` "
        f"is not a halt): these return 1 and are discarded: {unhalted}"
    )
    # ...and the pin guard's polarity: `[ -n "$PINNED" ]` refuses an EMPTY pin.
    # `[ -z "$PINNED" ]` refuses every non-empty one, so landing becomes
    # impossible — the same class as the `--jq -r` defect that made the check
    # unpassable.
    pin_guards = [
        i
        for i, c in enumerate(landing[0])
        if re.match(r'\[\s*-n\s+"\$PINNED"\s*\]', _code(c))
    ]
    assert pin_guards and all(
        _halts(_block(landing[0], i), subject=_LANDING_PIN_GUARD) for i in pin_guards
    ), (
        "a guard that is not backed by a halt is an annotation: an empty $PINNED "
        "must stop the fence here, not merely fail a later comparison"
    )
    assert all("--jq" in cmd or "--template" in cmd for cmd in defines), (
        "the landing pin must read the batch PR body through `--jq '.body'` or "
        "`--template '{{.body}}'` — that is the only mechanical enforcement of "
        "the tested-tree==landed-tree invariant"
    )


#: A carrier PRODUCER: the `printf` that emits a `<label>: <value>` line the batch PR body must
#: carry. The printed value must be a VARIABLE — a printed literal is a hand-copied value with a
#: production step in front of it.
_CARRIER_PRINT = re.compile(
    r"^printf\s+'(?P<label>[a-z][a-z-]*): %s\\n'\s+\"\$(?P<var>[A-Z][A-Z0-9_]*)\"$"
)
#: A carrier CONSUMER: the `sed` that lifts `<label>: ` back out of the batch PR body.
_CARRIER_READ = re.compile(r"sed\s+-n\s+'s/\^(?P<label>[a-z][a-z-]*): //p'")


def test_every_carrier_line_the_doctrine_reads_back_is_printed_by_a_fence() -> None:
    """the ``1c5fefdc4`` P2-1: the pin's carrier had readers and a halt — and NO producer.

    ``:313`` told the operator to *"Record `$PINNED` too — in the batch PR body, as a
    `pinned-base: <sha>` line"*, and Landing halts when the line is absent. **No fence ever PRINTED
    it**: `grep -n '^printf' docs/ops/batch-integration.md` matched no `printf` of the pin. Landing
    is explicitly a different shell/actor, so the value is not in scope when the body is written —
    and the only re-derivation the document leaves reachable is
    ``git rev-parse origin/main``, which at body-writing time is CURRENT ``main``. Feed that in and
    the landing assertion becomes ``[ "$(git rev-parse origin/main)" = "$(git rev-parse
    origin/main)" ]``: always true, on a moved base, with every other check in the Landing section
    green — the doctrine's headline failure passing its own "only mechanical enforcement of the
    invariant". It is fail-OPEN, too: ``$PINNED`` is non-empty, so the ``[ -n "$PINNED" ]`` guard is
    satisfied.

    And the guard could not see it from either side: 37 of 37 checks were green with no producer, and
    37 of 37 stayed green when one was added — the guard pinned the READER hard (exactly one
    definition, a PR-body read, a halting fetch before it, a halting assertion) and pinned no WRITER
    at all. So the pair is pinned together, in the direction the failure ran: **every ``<label>: ``
    the doctrine reads back out of the batch PR body must be PRODUCED by a ``printf`` in an EARLIER
    fence, from a variable that fence DERIVES.** ``tested-tree:`` (Phase 3, re-printed by the
    post-revert re-check) and ``pinned-base:`` (Phase 1) are the two, and a hand-copied value cannot
    silently replace either.
    """
    fences = _fences()
    reads: dict[str, int] = {}
    for i, fence in enumerate(fences):
        for command in fence:
            match = _CARRIER_READ.search(_code(command))
            if match:
                reads.setdefault(match.group("label"), i)
    assert reads, (
        "no fence reads a `<label>: <value>` line back out of the batch PR body — re-point this guard"
    )
    assert set(reads) == {"pinned-base", "tested-tree"}, (
        "the carriers read back out of the batch PR body changed: this guard pins the pair the "
        f"landing assertion and its post-merge detector rely on — {sorted(reads)}"
    )
    for label, read_at in sorted(reads.items()):
        producers = [
            (i, match)
            for i, fence in enumerate(fences[:read_at])
            for command in fence
            if (match := _CARRIER_PRINT.match(_code(command))) and match.group("label") == label
        ]
        assert producers, (
            f"nothing PRINTS `{label}:` before the fence that reads it back. A carrier with no "
            "producer is a hand-copied value, and the only re-derivation left to the operator is the "
            "one that makes the check vacuous (`git rev-parse origin/main` IS current main, so "
            "Landing's assertion compares main to itself). Print it where the value is derived: "
            f"`printf '{label}: %s\\n' \"$VALUE\"`"
        )
        for i, match in producers:
            var = match.group("var")
            derived = [
                _code(c)
                for c in fences[i]
                if re.match(rf"^(?:export\s+|readonly\s+)?{var}=", _code(c))
            ]
            assert derived and any("$(" in c for c in derived), (
                f"the `{label}:` producer prints ${var}, but the same fence never DERIVES it (no "
                f"`{var}=\"$(…)\"`): a printed literal is a hand-copied value one step later — "
                f"derived={derived}"
            )


def test_the_manifest_claim_survives_the_revert() -> None:
    """#10399 review ISSUE 5: after a revert the log is "merges ATTEMPTED".

    Read as the landed manifest, ``git log origin/main..integration/batch-1``
    advertises a PR whose content the revert removed, and a re-run of the Phase 4
    bisect is walked straight back to the already-dropped merge.
    """
    text = _text()
    assert "merges attempted" in text.lower(), (
        "the doctrine must say the batch log records *merges attempted* — after "
        "the reverts it prescribes, `git log origin/main..integration/batch-1` "
        "also names dropped PRs, so reading it as the landed manifest re-blames "
        "a culprit that was already removed"
    )


def test_the_contract_sentences_are_pinned() -> None:
    """#10399 review P3: the document's load-bearing claims, not its wording.

    The guard's header claims it keeps the doctrine honest. These four are the
    claims a wording edit could invert while every command stayed executable, so
    they are pinned as substrings: the ``OVERRIDES:`` line (the ruling this doc
    records), the four verdict markers, the never-green-on-a-partial-lane rule,
    and the ``--no-ff`` assembly form inside the assembly fence.
    """
    text = _text()
    assert "ci-main.yml's post-merge full suite" in text and "OVERRIDES" in text, (
        "the doctrine must carry the same `OVERRIDES:` line as decision issue "
        "#10399 — it is what makes the ruling visible to an adopter"
    )
    # The verdict rows are pinned as TABLE ROWS, not as loose substrings: the
    # markers also appear in the surrounding prose (`UNPROVEN-FULL-SUITE(...)` is
    # quoted in the carve-out paragraph), so a substring check would survive
    # deleting a row from the table.
    table = "\n".join(line for line in text.splitlines() if line.startswith("|"))
    for marker in (
        "BATCH-GREEN",
        "BATCH-GREEN(BASELINE(",
        "BATCH-RED(",
        "UNPROVEN-FULL-SUITE(",
    ):
        assert marker in table, f"the verdict table no longer defines {marker!r}"
    assert "never green on a partial lane" in text, (
        "the rule that a subset is never a green is the one that stops this "
        "process manufacturing the red main it exists to prevent"
    )
    assert "only ever receives a tree that has already passed the full suite" in text, (
        "the invariant sentence is the whole document — every phase is in "
        "service of it"
    )

    assembly = _fence_index(lambda fence: any("git merge" in c for c in fence))
    assert assembly >= 0, "the doctrine no longer documents assembling a batch — re-point this guard"
    assert any("--no-ff" in c for c in _fences()[assembly]), (
        "the assembly fence must keep `--no-ff` on every merge: a fast-forward "
        "merge is not a merge commit, so the documented `git revert -m 1` "
        "would then have no mainline to revert against"
    )


def test_the_unreproduced_gate_legs_are_named() -> None:
    """the ``a8be548762`` P2: the reproduced/not-reproduced list is a partition claim.

    ``test-d14-hosted-api`` runs ``-m embedded_only`` URI-less and
    ``test-record-postgres`` arms ``[postgres]`` + ``TEST_RECORD_DB_URI``; both are
    ``needs:`` of the required ``python-ci-gate``, and the batch's URI lane SKIPS
    both rather than reproducing them. A list of the legs a pytest run does not
    reproduce that omits them reads as a complete partition while two gate test
    legs never execute — so ``BATCH-GREEN`` becomes achievable on a tree they never
    ran.

    ⛔ **Scoped to the claim, not to the document.** This check was ``assert needle in text``
    over the whole doctrine, and it was measured GREEN with every one of the five non-test members
    deleted from the enumeration (#10400 round-39 review, Finding 1): each name also appears in the
    paragraph two sentences later about which of them declare ``needs:``, so the needle certified a
    MENTION while the partition claim lost its members. A bullet scope is not enough either — that
    paragraph is inside the same bullet. The five members are therefore pinned in the SENTENCE that
    carries the phrase ``non-test legs``, and the two test legs in the bullet that carries
    ``Two test legs``; both scopes are measured against the mutant below.
    """
    non_test_claim = _sentence_with("non-test legs")
    for needle in (
        # The NON-TEST members, which no pytest run can reproduce at all.
        # `ci-introduced-failure` is the #9984 predicate: the rail's own automated
        # version of Phase 1, and the one member this list dropped (#10400 P2-1).
        "packs-compile",
        "manifest-integrity",
        "workflow-lint",
        "surface-guard",
        "ci-introduced-failure",
    ):
        assert needle in non_test_claim, (
            f"the NON-TEST partition claim must name {needle!r} in the sentence that makes it: "
            "every one is a `needs:` of the required `python-ci-gate`, so omitting it makes the "
            "list read as a complete partition while a gate member goes unmentioned. A mention "
            "elsewhere in the section is not the claim — that is how all five went missing with "
            "this check green"
        )

    # `changes` is the diff SELECTOR: a non-deferrable row of the gate's own leg
    # table (`.github/workflows/python-ci.yml:4484` — the row itself; `:4483` is the
    # `cat > "$LEGS_FILE" <<'LEGS'` opener — not in `DEFERRABLE_LEGS`), and the one leg
    # whose failure makes every other leg skip — the selection a batch's pytest run cannot
    # exercise (#10400 P3). It is argued in its own sentences, so it is pinned in the bullet.
    non_test_bullet = _bullet("- **Non-test legs**")
    for needle in (
        "`changes`",
        "DEFERRABLE_LEGS",
        # …and the authority for the SET is the leg table, never a count written here:
        # a hand-maintained ordinal is how the last two members went missing.
        "LEGS_FILE",
    ):
        assert needle in non_test_bullet, (
            f"the non-test-leg bullet must name {needle!r} where it argues the diff selector: "
            "`changes` is a gate member with no pytest counterpart, and its set is read from the "
            "leg table rather than counted here"
        )

    test_legs = _bullet("- **Two test legs")
    for needle in (
        # the two TEST legs the batch's URI lane skips rather than runs
        "test-d14-hosted-api",
        "test-record-postgres",
        "embedded_only",
        "TEST_RECORD_DB_URI",
    ):
        assert needle in test_legs, (
            f"the reproduced/not-reproduced list must name {needle!r} in the bullet that lists the "
            "TEST legs the batch's lane SKIPS rather than runs: omitting one makes "
            "`BATCH-GREEN` achievable on a tree where a `python-ci-gate` test leg never ran"
        )


#: A stated NUMBER of the jobs that declare `needs: changes`. The count is a property of
#: `.github/workflows/python-ci.yml`, not of the doctrine, so a literal re-stales on the next job
#: added — measured: the sentence said nine and the workflow declared eight, and one of those eight
#: (`packs-compile`) is a NON-test leg, so the test legs that skip were seven.
_NEEDS_CHANGES_ORDINAL = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"jobs\b[^\n]{0,40}?needs:\s*changes",
    re.IGNORECASE,
)


def test_no_hand_counted_ordinal_for_the_needs_changes_jobs() -> None:
    """#10400 review P3: the sentence wrote a count that was wrong at the head it described.

    ``:288-289`` said "the **nine** jobs that declare ``needs: changes``" while
    ``.github/workflows/python-ci.yml`` declared **eight** — and one of the eight
    (``packs-compile``) is the non-test member the same bullet names eight lines above, so the
    **test** legs that skip numbered seven. The sentence sits four lines above the paragraph that
    forbids exactly this ("Do not trust a count of these members written here — the authority is the
    leg table"), and ``test_the_unreproduced_gate_legs_are_named`` pins the member NAMES but not the
    ordinal, so it could not red. This is the same stale-hand-count class the module header names for
    the bare-run ERROR literal, closed the same cheap way: the doctrine points at the measurement
    (``grep -c '^    needs: changes$' .github/workflows/python-ci.yml``) and states no number.

    Non-vacuity, measured: restoring the pre-fix ``the nine jobs that declare `needs: changes``` sentence
    reds this check and nothing else; the honest head is green.
    """
    text = _text()
    claim = _NEEDS_CHANGES_ORDINAL.search(text)
    assert claim is None, (
        "the doctrine states a hand-written number of jobs declaring `needs: changes` "
        f"({claim.group(0)!r}): the count belongs to .github/workflows/python-ci.yml, so any literal "
        "re-stales the moment a job is added — point at the measurement "
        "(`grep -c '^    needs: changes$' .github/workflows/python-ci.yml`) and state no number"
    )
    assert "needs: changes" in text, (
        "the doctrine no longer names the `needs: changes` set at all — re-point this guard"
    )


def test_the_live_queue_and_the_carve_out_registry_are_named() -> None:
    """#10399 review P2-1 / P2-3: name the machinery instead of paraphrasing it.

    ``.mergify.yml`` is a LIVE merge queue in this repo (``merge_conditions:
    check-success=python-ci-gate``, ``batch_size: 4``) that already validates
    composition, so a doctrine claiming per-PR CI is the only gate
    mis-describes the repo it governs. And the carve-out population is a registry
    (``config/ci-surfaces.yml`` -> ``carve_out:``), so a quoted count of it goes
    stale on the next reconciliation — name the registry.
    """
    text = _text()
    for needle in (".mergify.yml", "python-ci-gate", "config/ci-surfaces.yml", "carve_out"):
        assert needle in text, (
            f"the doctrine must name {needle!r} — it is the authoritative source "
            "for a claim the document makes"
        )


def test_the_batch_merge_halts_on_a_no_op_and_asserts_a_merge_commit() -> None:
    """the ``4dc214c02`` P2: ``git merge --no-ff`` on an already-contained head is a silent no-op.

    Git prints ``Already up to date`` and exits **0** without creating a merge commit, so the
    documented ``git log --oneline -1`` after the merge is cosmetic, the manifest names a PR whose
    commits are not in the tree, and Phase 4's ``git revert -m 1`` has no mainline to revert. The
    reviewed-head fetch one line above got a halt for the adjacent failure (a REFUSAL exits
    non-zero); this is the other half — a NO-OP exits zero — so the status is not enough on its own
    and the ``HEAD^2`` assertion is what catches it.
    """
    fences = _fences()
    merges = [
        (fence, i)
        for fence in fences
        for i, c in enumerate(fence)
        if _BATCH_MERGE.match(_code(c))
    ]
    assert merges, "the assembly fence no longer merges a PR head — re-point this guard"
    for fence, i in merges:
        command = fence[i]
        assert _is_halted_batch_merge(command), (
            "the batch merge must HALT (`… refs/tmp/pr-<N> || { echo …; exit 1; }`): `git merge "
            "--no-ff` on an already-contained head prints 'Already up to date' and exits 0, so an "
            f"unhalted line records a PR the batch never merged: {command}"
        )
        later = [_code(c) for c in fence[i + 1 :]]
        assert any(_is_merge_commit_assert(c) for c in later), (
            "the merge must be followed by a halted `git rev-parse -q --verify HEAD^2` assertion: "
            "a no-op merge exits 0 and creates no merge commit, so the STATUS cannot see it, and "
            "Phase 4's `git revert -m 1` would then have no mainline"
        )
        assert any(c.startswith("git log --oneline -1") for c in later), (
            "`git log --oneline -1` must still follow every merge: the batch's record of merges "
            "attempted is read from it, and a merge nobody looked at is a merge nobody can blame"
        )


#: Phase 1's skip carriers: the COUNT (a number Phase 3 compares) and the reason MULTISET (which
#: catches a swap an equal count cannot). Both are baseline-diffed, never absolute.
_SKIP_COUNT_WRITE = re.compile(
    r'^printf\s+\S+\s+"?\$SKIPPED_TOTAL"?\s*>\s*"?(?P<path>' + _LEDGER + r')"?$'
)
_SKIP_REASONS_WRITE = re.compile(
    r"^grep\s+-o\s+'<skipped[^']*'.*\bsort\s*>\s*\"?(?P<path>" + _LEDGER + r")\"?$"
)
_SKIP_BIND = re.compile(
    r'^BASELINE_SKIPPED="?\$\(\s*cat\s+"?(?P<path>' + _LEDGER + r')"?\s*\)"?$'
)
_SKIP_COUNT_SUBJECT = re.compile(
    r'\[\s*"\$BATCH_SKIPPED"\s+-le\s+"\$BASELINE_SKIPPED"\s*\]'
)
_SKIP_COUNT_NONEMPTY = re.compile(r'\[\s*-n\s+"\$BATCH_SKIPPED"\s*\]')


#: The reason-diff halt. It is NOT a `||` halt (it must fire only when the diff is NON-empty), so
#: it is pinned as its own shape: `[ -s <file> ] && { … exit 1; }`.
_SKIP_REASON_HALT = re.compile(
    r'^\[\s*-s\s+"?(?P<path>' + _LEDGER + r')"?\s*\]\s*&&\s*\{[^{}]*\bexit\s+[1-9]\d*\s*;?\s*\}$'
)
_SKIP_REASON_DIFF = re.compile(
    r'^comm\s+-13\s+"?[^"\s]*skipped-reasons"?\s+"?[^"\s]*skipped-reasons"?\s*>\s*'
    r'"?(?P<path>' + _LEDGER + r')"?$'
)


#: The reason axis is pinned per RUN, not per FENCE. A fence that runs both halves (Phase 3, and the
#: post-revert re-check) carries TWO `comm -13` diffs and TWO `[ -s … ] && { … exit 1; }` halts, one
#: per half, so an `any()` over the fence let the `track_b` copy stand in for the deterministic one.
#: Measured at ``1c5fefdc4`` against the honest document: deleting Phase 3's half-1
#: `comm -13 … > $B/new-skip-reasons` line, deleting its halt, deleting both, or flipping the diff to
#: `comm -23` measured **0 of 37 red** each — so a skip SWAP with an equal count (one live-FalkorDB
#: skip traded for a collection skip) stayed invisible in the fence the verdict is printed from, which
#: is the #1382 class the doctrine closes at ``:539``. The junit report a run WRITES decides which
#: half of the axis that run owes.
_JUNIT_REPORT = re.compile(r'--junitxml="?(?P<path>' + _LEDGER + r')"?')

_SKIP_REASONS_FROM_RUN = _TRACK_B_SKIP_REASONS_SRC
_SKIP_REASON_RUN_DIFF = re.compile(
    r'^comm\s+-13\s+"?(?P<base>' + _LEDGER + r')"?\s+"?(?P<batch>' + _LEDGER + r')"?\s*>\s*'
    r'"?(?P<out>' + _LEDGER + r')"?$'
)


def _skip_reason_axis_misses(codes: list[str], run_at: int, report: str) -> list[tuple[str, str]]:
    """Is THIS run's skip-REASON axis instrumented? ``[(what, why)]`` — empty means yes.

    Per RUN, never per fence: the junit report a run WRITES decides which half of the axis the fence
    owes. Half 1 diffs against ``baseline.skipped-reasons``, half 2 (``…trackb``) against
    ``baseline-trackb.skipped-reasons`` — the two halves have their own populations, so a half that
    diffs against the other half's baseline, or whose diff/halt belongs to its sibling run, leaves a
    swapped skip in THIS run invisible.
    """
    misses: list[tuple[str, str]] = []
    half_two = "trackb" in report
    reasons = [
        (m.group("out"), i)
        for i, c in enumerate(codes)
        if (m := _SKIP_REASONS_FROM_RUN.search(c)) and m.group("path") == report
    ]
    if not reasons:
        return [
            (
                "reasons",
                f"no skip REASONS are recorded from {report} in this fence: an equal COUNT can be a "
                "SWAP (one live-FalkorDB skip traded for a collection skip), and only the multiset "
                "sees it",
            )
        ]
    if any(i < run_at for _out, i in reasons):
        misses.append(("reasons", f"{report}'s reasons are recorded BEFORE the run that writes it"))
    sources = {out for out, _i in reasons}
    diffs = [
        (m.group("base"), m.group("out"), i)
        for i, c in enumerate(codes)
        if (m := _SKIP_REASON_RUN_DIFF.match(c)) and m.group("batch") in sources
    ]
    if not diffs:
        misses.append(
            (
                "reason diff",
                f"no `comm -13 <baseline> <batch>` diff of the reasons recorded from {report}: the "
                "count comparison cannot see a SWAP, so a test that had to run and did not is "
                "invisible in this run",
            )
        )
        return misses
    for base, out, at in diffs:
        if ("trackb" in base) != half_two:
            misses.append(
                (
                    "reason diff",
                    f"{report}'s reasons are diffed against the OTHER half's baseline ({base}): the "
                    "two halves have their own populations, so this diff is not about this run",
                )
            )
        if at < run_at:
            misses.append(("reason diff", f"{report}'s reason diff sits BEFORE the run it judges"))
        halts = [
            i
            for i, c in enumerate(codes)
            if (m := _SKIP_REASON_HALT.match(c)) and m.group("path") == out
        ]
        if not halts:
            misses.append(
                (
                    "reason halt",
                    f"the reason diff of {report} ({base} -> {out}) does not HALT: a NEW skip "
                    "reason is a test that must run and did not, and no failure id records it",
                )
            )
        elif any(i < run_at for i in halts):
            misses.append(("reason halt", f"{report}'s reason halt sits BEFORE the run it judges"))
    return misses


def test_a_skip_the_baseline_did_not_have_blocks_the_verdict() -> None:
    """the ``4dc214c02`` P1: a SKIP is invisible to both completion detectors.

    Phase 3's "did it actually run" machinery was a status predicate (rc ∈ {0,1}) and a junit test
    COUNT. A skipped test is still COLLECTED: it is inside `tests=`, it contributes no failure id,
    so rc is 0, the count is unchanged and the Phase 4 id diff is empty. The reachable bypass is a
    socket probe, not an unset variable — ``tests/test_projection.py``'s ``live_proj`` fixture skips
    when the FalkorDB probe fails, which it does with ``TORTOISE_DB_URI`` correctly exported and the
    server gone — so every live test skips and the verdict is ``BATCH-GREEN`` on a tree where the
    suite this document exists to gate never ran.

    This repo already fails closed on exactly that class in CI (``tools/skip-guard.py``, #1382:
    "the historical silent-green masked the #1382 EP regression class for days"). The batch records
    the BASELINE's skips and halts on any skip the baseline did not have — baseline-diffed, never a
    whole-run gate, because the batch lane's ``embedded_only`` skips are expected.
    """
    codes = _fenced_commands()
    writes = [
        (m.group("path"), c) for c in codes if (m := _SKIP_COUNT_WRITE.match(_code(c)))
    ]
    assert writes, (
        "the baseline's SKIP COUNT is never recorded, so Phase 3 cannot diff it: a skipped test is "
        "collected, contributes no failure id, and rc is 0 — a FalkorDB outage mid-run then reads "
        'as BATCH-GREEN. Record it: `printf \'%s\\n\' "$SKIPPED_TOTAL" > "$B/baseline.skipped"`'
    )
    paths = {path for path, _c in writes}
    assert len(paths) == 1, f"the skip count is carried through more than one file: {sorted(paths)}"
    carrier = paths.pop()

    producers = [
        fence
        for fence in _fences()
        if any(_SKIP_COUNT_WRITE.match(_code(c)) for c in fence)
    ]
    assert len(producers) == 1, (
        "the skip count must be produced in exactly ONE fence (Phase 1's), or Phase 3 compares two "
        f"different runs: {len(producers)}"
    )
    fence = producers[0]
    fence_codes = [_code(c) for c in fence]
    assert any("baseline-junit" in c and 'skipped="' in c for c in fence_codes), (
        "the recorded skip count must be READ FROM THE BASELINE's junit report — a count from "
        "anywhere else measures a different run"
    )
    assert carrier in _cleared(fence_codes), (
        f"{carrier} must be CLEARED before the baseline run: a previous batch's skip count survives "
        "a killed baseline and Phase 3 then diffs against it"
    )
    reason_writes = {
        m.group("path") for c in fence_codes if (m := _SKIP_REASONS_WRITE.match(c))
    }
    assert reason_writes, (
        "the baseline's skip REASONS are not recorded: an equal COUNT can be a SWAP (one "
        "live-FalkorDB skip traded for a collection skip), and only the reason multiset sees it"
    )

    # ⛔ Scoped by ROLE (a suite run that writes THIS report), not by mention: Phase 4's id ledger
    # READS `$B/batch-junit.xml` and issues no verdict on its skips, and mention-scoping made that
    # honest fence inexpressible (round 39 — it demanded two suite runs of a fence that runs none).
    consumers = [
        fence
        for fence in _fences()
        if any(
            _SUITE_CMD.match(_code(c)) and "batch-junit" in _code(c) for c in fence
        )
    ]
    assert consumers, "the gate fence no longer writes a junit report — re-point this guard"
    gated = False
    for fence in consumers:
        fence_codes = [_code(c) for c in fence]
        binds = [m.group("path") for c in fence_codes if (m := _SKIP_BIND.match(c))]
        # ⛔ PER FENCE, not "somewhere". This loop used to `continue` on a fence with no skip
        # comparison and let `assert gated` be satisfied by ANY one fence, so the axis was pinned
        # at the population's weakest member: deleting Phase 3's whole half-1 skip block — the
        # primary gate fence, the one the verdict is printed from — measured **0 red of 37**, and so
        # did flipping its comparison to `-ge`, while the post-revert re-check's surviving copy
        # satisfied `gated`. That is verbatim the fail-open the skip axis exists to prevent (a
        # FalkorDB outage mid-run reads `BATCH-GREEN`), surviving in the fence that matters most.
        counts = [c for c in fence_codes if _SKIP_COUNT_SUBJECT.search(c)]
        assert counts, (
            "this fence writes a `batch-junit` report and issues a verdict from it, but never "
            "compares its SKIPS against the baseline's: a skipped test is COLLECTED, adds no failure "
            "id and exits 0, so neither the status predicate nor the count can see it (a FalkorDB "
            "outage mid-run skips the whole live suite and the verdict still reads BATCH-GREEN). "
            'The comparison must be `[ "$BATCH_SKIPPED" -le "$BASELINE_SKIPPED" ]`, halted — a '
            "sibling fence's copy does not cover this run"
        )
        gated = True
        assert binds == [carrier], (
            f"the gate fence compares a skip count without binding the baseline's from {carrier}: "
            f'binds={binds}'
        )
        absent = [
            i
            for i, c in enumerate(fence_codes)
            if _CARRIER_TEST.match(c) and _CARRIER_TEST.match(c).group("path") == carrier  # type: ignore[union-attr]
        ]
        assert absent and _halts(fence[absent[0]], subject=lambda seg: bool(_CARRIER_TEST.match(seg))), (
            f"an ABSENT {carrier} must HALT the gate fence: without it the comparison runs against "
            "an empty string and `-le` on an empty value is a silent pass"
        )
        assert min(absent) < fence_codes.index(counts[0]), (
            "the absent-carrier halt must precede the comparison it protects"
        )
        assert all(_halts(c, subject=_SKIP_COUNT_SUBJECT) for c in counts), (
            "the skip comparison must HALT on its own failure and compare the batch's count as "
            "`-le` the baseline's (a flipped `-ge` refuses a clean run and passes a dirty one): "
            f"{counts}"
        )
        # ⛔ PER RUN, not "somewhere in this fence". This is the sibling of the `continue` defect
        # above, one axis over: a fence that runs both halves (Phase 3, and the post-revert
        # re-check) carries TWO reason diffs and TWO halts — one for `batch-junit.xml` and one for
        # `batch-trackb.xml` — so `any()` over the fence let the `track_b` copy stand in for the
        # deterministic one. Measured at `1c5fefdc4` against the otherwise honest document: deleting
        # Phase 3's half-1 `comm -13 …` line, deleting its `-s` halt, deleting both, or flipping the
        # diff to `comm -23` measured **0 of 37 red** each — a skip SWAP at an equal count is
        # exactly what the count comparison cannot see, so half 1's reason machinery has to be
        # pinned to half 1's OWN run. The junit report a run writes decides which half it owes.
        runs = [
            (i, m.group("path"))
            for i, c in enumerate(fence_codes)
            if (m := _JUNIT_REPORT.search(c))
        ]
        assert len(runs) == 2, (
            "a verdict-bearing gate fence must run BOTH halves, and each run must write its own "
            f"junit report: found {[path for _i, path in runs]}"
        )
        for run_at, report in runs:
            misses = _skip_reason_axis_misses(fence_codes, run_at, report)
            assert not misses, (
                f"the skip-REASON axis is not instrumented for THIS run ({report}): "
                + "; ".join(f"{what} — {why}" for what, why in misses)
                + ". Each half owes its OWN `grep … <report> … sort > <reasons>`, its OWN "
                "`comm -13` against that half's baseline, and its OWN `[ -s … ] && { … exit 1; }` "
                "halt — a sibling run's copy does not cover this one"
            )
    assert gated, (
        "the gate fence never compares skips against the baseline: a run whose live-FalkorDB tests "
        "all SKIP exits 0 with an unchanged test count, so it is indistinguishable from a full run"
    )
    verdict_rows = [
        line for line in _text().splitlines() if line.startswith("| `UNPROVEN-FULL-SUITE")
    ]
    assert verdict_rows and any(
        re.search(r"SKIP", row) for row in verdict_rows
    ), (
        "the verdict table's UNPROVEN-FULL-SUITE row must name the SKIPPED case: a verdict computed "
        f"from id sets cannot see a skip, so the row is where an operator learns it is not green "
        f"(row: {verdict_rows})"
    )


#: The four junit reports Phase 4's id ledger is built from, by BASENAME under the per-batch
#: directory (each fence re-derives `$B`). Two are written by Phase 1 (the baseline) and two by Phase 3
#: **and** by the post-revert re-check — which is why the ledger must be REBUILT after a drop.
_JUNIT_REPORTS = ("batch-junit", "batch-trackb", "baseline-junit", "baseline-trackb")

#: flag → carrier for the three set operations the verdict rows read. `comm`'s columns are 1 = unique
#: to file1, 2 = unique to file2, 3 = common (GNU coreutils manual, `comm invocation`: "Column one
#: contains lines unique to file1, column two lines unique to file2, and column three lines common to
#: both files"), so `-13` = batch-only = NEW (convicts a PR), `-12` = common = `BASELINE(<ids>)`
#: (exempts), `-23` = baseline-only = on `main` and gone here (neither convicts nor exempts).
_COMM_SETS = (
    ("-13", "new-failures"),
    ("-12", "baseline-still-failing"),
    ("-23", "cleared-failures"),
)


def _failure_ledger_fence() -> list[str] | None:
    """The Phase-4 fence that builds the failure-id ledger (all four sources + the NEW set)."""
    for fence in _fences():
        codes = [_code(c) for c in fence]
        if all(any(f"$B/{name}.xml" in c for c in codes) for name in _JUNIT_REPORTS) and any(
            "comm -13" in c and "$B/new-failures" in c for c in codes
        ):
            return fence
    return None


def test_the_failure_id_ledger_is_built_by_a_stated_rule() -> None:
    """#10400 round-39 review, Finding 2 (P3): the one convicting input was PROSE.

    Phase 4 decided ``BATCH-GREEN`` / ``BATCH-GREEN(BASELINE(<ids>))`` / ``BATCH-RED(<ids>)`` on a
    diff of failure **ids**, named the four junit reports those ids come from, and never built the
    sets: `grep -n '<failure' docs/ops/batch-integration.md` found nothing, and the only `comm` lines
    in the section were the four *skip-reason* diffs. So the single step that decides whether a PR is
    dropped turned on how each operator spelled an id — two operators, two ledgers — while every
    other axis in the verdict (lane, tree, COMPLETED status, test count, skips by count and by
    reason) is a fence with a halt.

    This pins the fence's four load-bearing pieces, each of which was measured as a surviving mutant
    (see the module header's round-39 ledger): the RULE (a `<testcase>` that carries a `<failure>` or
    `<error>` CHILD, identified by `classname` + `name` — the report schema's own definition, and
    `classname` is overridable per case with `record_xml_attribute`), the four SOURCE paths with an
    absent-report halt each, the three `comm` set operations with their carriers, and the fail-closed
    halt that refuses an EMPTY extraction from a report that says it failed (an undefined id is never
    green — an empty set diffs empty in both directions).
    """
    ledger = _failure_ledger_fence()
    assert ledger is not None, (
        "the doctrine builds no failure-id ledger: Phase 4's verdict is a set difference over failure "
        "ids, so the sets have to be files under `$B` (the same shape as the baseline count, the "
        "status and the tested tree) and not something each operator spells for themselves"
    )
    codes = [_code(c) for c in ledger]

    # 1. THE RULE, stated where an operator reads it. Scoped to the sentence that DEFINES the id
    #    (round 39's own finding 1, applied to this check): the four tokens also appear in the
    #    surrounding prose, so a document-wide `in text` would certify a mention instead of the rule.
    rule = [s for s in _sentences_with("failure id =") if "testcase" in s]
    assert rule, (
        "the doctrine must STATE the failure-id rule where the ledger is built (`failure id = "
        "\"<classname>::<name>\" of every <testcase> that carries a <failure> or <error> CHILD`): "
        "both sides of the diff have to spell an id the same way, and the junit id is not "
        "self-evident — pytest identifies a case by `classname` + `name`, and `classname` is "
        "overridable per case with `record_xml_attribute` "
        "(<https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files>)"
    )
    assert any(
        all(token in sentence for token in ("classname", "::", "<failure>", "<error>"))
        for sentence in rule
    ), f"the stated id rule must name the SEPARATOR and the two child elements: {rule}"

    # 2. The extraction IMPLEMENTS that rule: a parser over the XML tree, keyed on the child ELEMENT.
    body = "\n".join(codes)
    for token in ("testcase", "classname", "'failure'", "'error'"):
        assert token in body, (
            f"the ledger's extraction never tests {token!r}: a case is failing because it CARRIES a "
            "`<failure>`/`<error>` child, never because a line or the adjacent text said so. pytest "
            "writes the whole document on ONE line (measured), so no grep/awk pairing of adjacent "
            "lines can work at all"
        )
    assert any(c.startswith("uv run python - ") for c in codes), (
        "the ids must be extracted by a parser, not by `grep`: the whole report is one line, so a "
        "line-oriented rule extracts nothing and the diff is empty in both directions"
    )

    # 3. The four SOURCES, each with an absent-report HALT: an absent report means that half never
    #    ran, and "no ids" must never read as "no failures".
    for name in _JUNIT_REPORTS:
        halts = [
            c
            for c in codes
            if c.startswith(f'[ -s "$B/{name}.xml" ]') and "exit 1" in c
        ]
        assert halts, (
            f"the ledger must READ `$B/{name}.xml` AND halt when it is absent: an empty id set diffs "
            "empty in both directions, so a half that never ran reads as a half that passed"
        )

    # 4. The three set operations, each into its own carrier — the verdict rows read THESE files.
    for flags, carrier in _COMM_SETS:
        assert any(
            f"comm {flags} " in c and f'"$B/{carrier}"' in c for c in codes
        ), (
            f"the ledger must write `$B/{carrier}` with `comm {flags}`: `comm`'s columns are 1 = "
            "unique to the baseline, 2 = unique to the batch, 3 = common — so `-13` is the NEW set "
            "that convicts a PR, `-12` is the `BASELINE(<ids>)` set that exempts it, and `-23` is "
            "main's failings this batch does not have"
        )

    # 5. FAIL CLOSED: a report whose own `failures=`/`errors=` attributes are non-zero while the
    #    extraction is empty means the rule and the producer drifted apart.
    reported = [c for c in codes if "failures=" in c and "errors=" in c]
    assert reported, (
        "the extraction must be checked against the report's OWN counts (`failures=` + `errors=`): "
        "an extraction that yields nothing from a report that says it failed would diff empty in "
        "both directions and print `BATCH-GREEN` over a red tree"
    )
    assert any(
        '[ -s "$B/$side.failures" ]' in c or "$side.failures" in c
        for c in codes
        if "exit 1" in c and "UNPROVEN-FULL-SUITE" in c
    ), (
        "the empty-extraction case must HALT as `UNPROVEN-FULL-SUITE(<lane>)`: an id the rule cannot "
        "read is undefined, not absent"
    )

    # 6. The sources are the reports the run fences WRITE, and the two batch ones are written TWICE
    #    (Phase 3 and the post-revert re-check) — the property that makes "rebuild the ledger after a
    #    drop" true rather than advisory.
    writes = {
        name: [c for c in _fenced_commands() if f'--junitxml="$B/{name}.xml"' in c]
        for name in _JUNIT_REPORTS
    }
    for name, found in writes.items():
        assert found, (
            f"no run writes `$B/{name}.xml`, so the ledger reads a report nothing produces: "
            "`--junitxml` is what puts a run's ids in the diff at all"
        )
    for name in ("batch-junit", "batch-trackb"):
        assert len(writes[name]) >= 2, (
            f"`$B/{name}.xml` is written by {len(writes[name])} fence(s): Phase 3 and the post-revert "
            "re-check must EACH write (and clear) it, or the diff after a drop compares the baseline "
            "against the tree that still contained the culprit"
        )


def test_no_fence_command_executes_a_command_from_its_message() -> None:
    """#10400 round-39: a halt MESSAGE must not run a command — measured, in this round's own fence.

    The doctrine's failure path is ``{ echo "…"; exit 1; }``, so its messages are part of what an
    operator reads when the process stops. Double quotes do not stop command substitution and a
    markdown backtick pair inside the message does not either: the round-39 ledger's
    ``echo "… without it every `track_b` id …"`` **ran ``track_b`` as a command** (measured by
    executing the fence: ``run.sh: line 7: track_b: command not found``). ``bash -n`` cannot see it —
    it is a syntactically perfect command substitution — and no other check in this module executes
    a fence's text, so the defect shipped green and was caught only by running the document.

    ``$(…)`` is deliberately allowed: the tree assertion's own message interpolates
    ``$(git rev-parse --abbrev-ref HEAD)`` on purpose. A backtick never has a legitimate use inside
    a fenced shell block here (the doctrine spells every substitution as ``$(…)``), so any occurrence
    in a line's COMMAND portion — never in a ``#`` annotation, which ``_fences()`` drops when it is
    the whole line and ``_code()`` cuts when it trails one — is the accident.
    """
    offenders = [c for c in _fenced_commands() if "`" in _code(c)]
    assert not offenders, (
        "a fence command carries a markdown backtick, which bash executes as a command substitution: "
        "a message is part of the failure path this doctrine is read for, and a message that runs a "
        "command is a halt that can do something other than halt (measured: `track_b: command not "
        f"found` from a halt message). Spell substitutions as $(…) and drop the backticks: {offenders}"
    )


#: ``git bisect run``'s grading: 0 = good, 125 = skip this probe, anything else = bad. The pre-fix
#: spelling was `uv run pytest … >/dev/null 2>&1 || exit 1`, which collapses every other status
#: (pytest 2/3/4/5, 128+N for SIGKILL/OOM) into "this commit is the culprit".
_BISECT_RUN = re.compile(r"^git\s+bisect\s+run\b")
_BISECT_COLLAPSED = re.compile(r"\|\|\s*exit\s+1")


def test_the_bisect_grader_does_not_convict_a_commit_on_an_infra_error() -> None:
    """the ``4dc214c02`` P2: the bisect grader treated EVERY non-zero status as "bad".

    Git's protocol for ``bisect run`` is 0 = good, **125 = skip this probe**, anything else = bad. A
    bare ``|| exit 1`` therefore grades pytest rc 2/3/4/5 (interrupted / usage / internal /
    misconfigured) and 128+N (SIGKILL/OOM — the class this document measured five times in 23
    minutes on #10399) as "this commit is the culprit". Measured in a scratch repo against a
    five-commit ``--no-ff`` batch whose probe always exits 2: ``bisect run`` printed "first bad
    commit", exited 0, and named the OLDEST probe — the first merge in the batch. The next
    documented step DROPS that PR, irreversibly within the batch, over an infrastructure error.

    So the grader must accept only the two statuses the document already calls COMPLETED, and hand
    everything else back to git as its own skip code.
    """
    runs = [c for c in _fenced_commands() if _BISECT_RUN.match(_code(c))]
    assert runs, "the doctrine no longer documents `git bisect run` — re-point this guard"
    for command in runs:
        code = _code(command)
        assert "exit 125" in code, (
            "the bisect grader must hand an infrastructure error back to git as its skip code "
            "(`exit 125`): without it a persistent rc=2 convicts the batch's FIRST merge as the "
            f"culprit and the doctrine then drops an innocent PR: {code}"
        )
        assert '[ "$rc" -eq 0 ] && exit 0' in code and '[ "$rc" -eq 1 ] && exit 1' in code, (
            "the grader must accept EXACTLY the two COMPLETED statuses (0 and 1) — the same rule "
            f"the gate and baseline predicates carry: {code}"
        )
        assert not _BISECT_COLLAPSED.search(code), (
            "the grader still collapses every non-zero status into `bad` (`|| exit 1`): a killed or "
            f"misconfigured probe then convicts an innocent commit: {code}"
        )


#: The BLAME probes — the runs whose ONLY job is to convict one merge, and the two the lane check
#: could not see. `_SUITE_CMD` excludes both by SPELLING, not by judgement: the manual probe's target
#: is a `<…>` placeholder (the `(?!\s+<)` lookahead refuses it, correctly, as "not a full-suite run")
#: and `git bisect run …` does not start with `uv` at all. So the lane assertion was required in every
#: fence EXCEPT the two whose verdict a dropped PR is read from — measured: rewriting the placeholder to
#: a real file (`uv run pytest tests/test_projection.py -q`) reds 5 of 38, i.e. the exclusion really is
#: spelling rather than scope.
_BLAME_PROBE = re.compile(r"^(?:uv\s+run\s+pytest\s+<|git\s+bisect\s+run\b)")

#: pytest's session-start refusal, verbatim from `tests/_embedded.py::_assert_p4_uri_required`. It is
#: the ONE string that separates "the lane is gone, nothing ran" from "this commit is red": both exit
#: 1, and pytest's documented codes have no value that tells them apart.
_LANE_ERROR = "default pytest requires TORTOISE_DB_URI"


def test_the_blame_probes_assert_the_lane_and_decline_to_convict_an_unrunnable_probe() -> None:
    """the ``0a9ee9050`` P2: the two runs a PR is DROPPED from carried no lane assertion.

    Phase 4 is the LATEST shell of all — Phases 1 and 3 state the lane, Landing is "a different
    actor" — and `_p4_uri_required` is session-scoped and autouse, so with no usable lane EVERY
    collected item ERRORs at setup and pytest exits **1**. The grader accepts exactly that status as
    "this commit is bad", so `git bisect run` names the OLDEST probe (the doctrine's own measurement,
    in the paragraph above it) and `git revert -m 1` drops the batch's FIRST merge, irreversibly
    within the batch — the innocent-PR drop the whole phase exists to prevent. The manual route is no
    better: a wall of `ERROR at setup` reads as "this merge is the culprit".

    So both blame runs must assert the lane (the GATE's predicate, `_LANE_ASSERT`) and HALT in their
    own fence, and the grader must hand a probe that could not RUN the suite back to git as its skip
    code — git's own documented protocol (`make || exit 125   # this skips broken builds`). The lane
    assertion is a start-of-fence fact; a probe runs minutes later against a checked-out tree, so the
    container can be reaped in between and the IN-PROBE decline is what covers that window.

    Non-vacuity, measured at the pre-fix head: neither blame fence carries the assertion and the
    grader has no decline, so this check reds on its first assert and on nothing else.
    """
    fences = [
        fence for fence in _fences() if any(_BLAME_PROBE.match(_code(c)) for c in fence)
    ]
    assert len(fences) >= 2, (
        "expected the manual blame probe and the `git bisect run` fence, parsed "
        f"{len(fences)} — re-point this guard"
    )
    for number, fence in enumerate(fences, start=1):
        codes = [_code(c) for c in fence]
        probes = [i for i, c in enumerate(codes) if _BLAME_PROBE.match(c)]
        asserts = [i for i, c in enumerate(codes) if _LANE_ASSERT.match(c)]
        assert asserts, (
            f"blame fence {number} runs a probe without asserting a lane: `_p4_uri_required` is "
            "session-scoped and autouse, so a run with no usable lane ERRORs every item at setup and "
            "exits 1 — the grader's own ACCEPTED \"bad\" status — and `bisect run` then convicts the "
            "OLDEST merge in the batch, whose PR the next step DROPS. Assert the GATE's predicate in "
            "this fence, as Phases 1 and 3 do"
        )
        assert any(_halts(_block(fence, i)) for i in asserts), (
            f"the lane assertion in blame fence {number} does not HALT — a bare check leaves every "
            "probe grading an all-setup-error session as a real red"
        )
        assert min(asserts) < min(probes), (
            f"blame fence {number} asserts the lane only AFTER a probe, so the probe it guards is "
            "exactly the one that convicts"
        )
    # ...and the decline must still be a decline: pin the needle against the module it comes from, or
    # the `case` below matches a string no pytest run can ever print.
    embedded = (ROOT / "tests" / "_embedded.py").read_text(encoding="utf-8")
    assert _LANE_ERROR in embedded, (
        f"the grader declines to convict on {_LANE_ERROR!r}, which no longer exists in "
        "tests/_embedded.py — the needle drifted and the decline can never fire"
    )
    graders = [c for c in _fenced_commands() if _BISECT_RUN.match(_code(c))]
    assert graders, "the doctrine no longer documents `git bisect run` — re-point this guard"
    for command in graders:
        code = _code(command)
        assert _LANE_ERROR in code and "exit 125" in code, (
            "the bisect grader does not decline to convict an all-setup-error probe: pytest gives "
            "\"nothing ran\" and \"this commit is red\" the SAME status (1), so without the lane-error "
            "escape every probe grades BAD and the OLDEST merge is convicted. Capture the probe's "
            f"output and `case` on {_LANE_ERROR!r} → `exit 125`: {code}"
        )


def test_the_batch_pr_is_a_draft_until_the_landing_assertion_passes() -> None:
    """the ``4dc214c02`` P1: the landing assertion runs NOWHERE on the merge path.

    The doctrine called the ``origin/main == $PINNED`` fence "the one mechanical enforcement of the
    invariant", but it is a bash block in a document: the batch PR is an ordinary PR to ``main``, so
    the moment it carries its required contexts it satisfies this repo's live queue —
    ``.mergify.yml``'s ``queue_rules`` declare ``queue_conditions: base=main`` + ``-draft`` + the
    ``check-success`` contexts, and ``merge_protections_settings: auto_merge_conditions: true`` — and
    is squash-merged onto whatever ``main`` is at that moment, with the fence never run.

    The lever is ``-draft``, which is itself a queue condition: a draft is never queued, so it can
    never be merged. Holding the batch PR as a draft until the assertion passes is what makes
    tested-tree == landed-tree a property of the QUEUE rather than a step someone may remember.
    """

    text = _text()
    landing = text[text.index("### Landing") : text.index("## Prior art")]
    assert re.search(r"batch PR is opened as a\s+\**draft", landing, re.IGNORECASE), (
        "the Landing section must say the batch PR is opened as a DRAFT: the base assertion is a "
        "bash block nothing invokes, and the queue auto-merges any non-draft PR that carries its "
        "required contexts"
    )
    # The MECHANISM, in one sentence: `queue_conditions` and `-draft` together. A draft rule whose
    # reason is only that drafts are polite is not enforcement — this repo's queue honours `-draft`
    # (`.mergify.yml`'s `queue_conditions`), and that is the whole lever.
    mechanism = [
        sentence
        for sentence in re.split(r"(?<=\.)\s", landing)
        if "queue_conditions" in sentence and "-draft" in sentence
    ]
    assert mechanism, (
        "the draft rule must name its MECHANISM in one sentence — `-draft` as a `.mergify.yml` "
        "`queue_conditions` entry: a draft that no queue condition honours is not enforcement, and "
        "a mention of each word in unrelated sentences is not the mechanism"
    )
    assert ".mergify.yml" in landing, (
        "the draft rule must name `.mergify.yml`, the live queue that honours `-draft`"
    )
    assert re.search(r"un-draft", landing, re.IGNORECASE), (
        "the Landing section must say WHEN to un-draft (only after the pin still matches, or with "
        "route 2's freeze declared): otherwise the draft is never released and the batch cannot land"
    )
    aftermath = text[text.index("## Aftermath") : text.index("### Landing")]
    assert re.search(r"draft", aftermath, re.IGNORECASE), (
        "the Aftermath section (where the batch PR is described) must carry the draft rule too: it "
        "is the list an operator reads while opening the PR"
    )


#: The post-merge detector's SUBJECT: the landed commit's TREE, against the tree the run MEASURED —
#: carried BY VALUE in the batch PR body as `tested-tree: <sha>` (Phase 3 prints it; Phase 4 re-prints
#: it after a revert). A squash-merge onto the pin reproduces the tested tree exactly, so a mismatch
#: here means the base moved inside the queue's cycle — or the batch branch moved after the run — and
#: `main` now holds a composition no full-suite run has seen.
#: ⛔ NOT a branch, and not a remote-tracking ref: `integration/batch-1` is a LOCAL branch that no step
#: in the doctrine pushes, so `origin/integration/batch-1` is either ABSENT (the landing actor is
#: usually in a fresh clone, where the fetch dies on a refspec nobody wrote, and a bare `|| exit 1`
#: hides that behind git's own `fatal: couldn't find remote ref`) or STALE — the ordinary case on the
#: drop path, where Phase 4 commits the revert locally and nothing pushes again, so a PERFECT landing
#: differs by exactly the dropped PR and is reported as a process failure. And even a live ref is the
#: WRONG OBJECT: it records where the branch is NOW, so one commit made after the run moves it while
#: the tested tree stays behind, and the comparison certifies a landing no run has seen. The property
#: the doctrine states is `landed tree == TESTED tree`, so the object is the tested tree's value.
_LANDED_TREE_DIFF = re.compile(r'^\[\s*"\$LANDED_TREE"\s*=\s*"\$TESTED_TREE"\s*\]')

#: The landed commit's TREE, captured from the landed sha in the same fence — one command, halted, so
#: a failed `rev-parse` stops the detector instead of comparing two empty strings.
_LANDED_TREE_READ = re.compile(
    r'^LANDED_TREE="\$\(\s*git\s+rev-parse\s+"\$LANDED"\^\{tree\}\s*\)"\s*\|\|\s*exit\s+1$'
)

#: The tested tree, READ from the batch PR body — the same carrier discipline as `$PINNED`, because
#: landing is a different shell and usually a different actor.
_TESTED_TREE_READ = re.compile(
    r'^TESTED_TREE="\$\(\s*gh\s+pr\s+view\b.*--jq\s+\'\.body\'.*\|\s*sed\s+-n\s+'
    r"'s/\^tested-tree: //p'\s*\)\"$"
)

#: The tested tree, CAPTURED at the run from the batch branch's TREE and PRINTED as the exact line the
#: batch PR body must carry. Without the capture, Landing's comparison has a value nobody records; the
#: print is what makes the carrier mechanical instead of remembered (`sed -n 's/^tested-tree: //p'`
#: reads precisely this shape).
_TESTED_TREE_CAPTURE = re.compile(
    r'^TESTED_TREE="\$\(\s*git\s+rev-parse\s+"integration/batch-1\^\{tree\}"\s*\)"\s*\|\|\s*exit\s+1$'
)
_TESTED_TREE_PRINT = re.compile(
    r"^printf\s+'tested-tree: %s\\n'\s+\"\$TESTED_TREE\"$"
)

#: The absent-carrier halt's SUBJECT: `[ -n "$TESTED_TREE" ]`. An empty value compares the landed
#: tree to the empty string — a mismatch on a perfect landing, or a silent pass if the halt is
#: dropped, and either way the carrier stops being a gate.
_TESTED_TREE_NONEMPTY = re.compile(r'\[\s*-n\s+"\$TESTED_TREE"\s*\]')

#: The landed sha, READ in the detector's own fence (nothing is carried between shells).
_LANDED_SHA_READ = re.compile(r'^LANDED="\$\(\s*git\s+rev-parse\s+origin/main\s*\)"$')


def test_the_residual_landing_window_is_stated_and_detected() -> None:
    """the ``c7a772628`` P2: the draft gates queue ENTRY, and its guarantee stops there.

    The P1 of the ``4dc214c02`` round was that the landing assertion ran NOWHERE on the merge path,
    and the fix was to hold the batch PR as a draft. That fix then over-claimed: it called the draft
    "the guarantee bors and GitHub's queue get by owning the merge". Mechanically it is not.
    ``.mergify.yml``'s own configuration contract splits the two condition lists —
    ``queue_conditions`` gates ENTRY against the PR head, ``merge_conditions`` gates the MERGE against
    the queue branch — and ``-draft`` is an ENTRY condition. Un-drafting therefore makes the PR
    eligible; it does not pin the base. ``merge_method: squash`` then applies the batch's diff to
    ``main`` as it is at merge time, the queue's only re-validation of the moved composition is
    ``check-success=python-ci-gate`` (the TIERED selection, never the full suite), and the branch
    protection's ``strict`` is false, so nothing forces the batch branch onto the moved base. ``.mergify.yml`` measures a full cycle at ~37 min against continuous merges, so the window
    is the whole cycle: ``PINNED' + batch-diff`` can land with every check in the Landing section
    green.

    Two things are pinned, because either one alone is a claim with no mechanism behind it:

    1. the document STATES the entry/merge split and the residual window (one sentence naming both
       condition lists — two words in unrelated sentences is not the mechanism), and
    2. it carries an EXECUTABLE post-merge detector: the landed commit's tree against the tested
       batch tree, with the landed sha read in the same fence, a fetch before it, and a HALT on the
       diff — route 2 (the freeze) being the only route that closes the window rather than detecting
       a violation of it.
    """
    text = _text()
    landing = text[text.index("### Landing") : text.index("## Prior art")]
    # 1. ENTRY vs MERGE, in ONE sentence naming BOTH lists. The draft rule's own check already
    # requires a `queue_conditions` + `-draft` sentence; this one requires the other half, so an
    # editor cannot keep the draft and silently drop the fact that it gates entry only.
    split = [
        sentence
        for sentence in re.split(r"(?<=\.)\s", landing)
        if "queue_conditions" in sentence
        and "merge_conditions" in sentence
        and "ENTRY" in sentence
        and "MERGE" in sentence
    ]
    assert split, (
        "the Landing section must state the queue's OWN split in one sentence — `queue_conditions` "
        "gates ENTRY (the PR head) and `merge_conditions` gates the MERGE (the queue branch): that "
        "is why un-drafting cannot pin the base, and a mention of each list in unrelated sentences "
        "is not the mechanism"
    )
    assert re.search(r"residual window", landing, re.IGNORECASE), (
        "the Landing section must name the residual window the draft leaves open"
    )
    # The three facts that MAKE the window real, each read from live config rather than asserted:
    # `merge_method: squash` (the base is whatever main is at merge time), the queue's only
    # re-check (`python-ci-gate`, the tiered selection), and the branch protection's `strict: false`
    # (nothing requires the batch branch onto the moved base). ⛔ The declaration home is named by its
    # LIVE ASSERTION, never by its path: clause (viii)(b) of `tools/mergify_config_guard.py` treats any
    # file under `tests/` that spells that path as an authoritative READER of it (measured: spelling it
    # here reds the REQUIRED `manifest-integrity` job — the same defect `dbd79ba07` fixed one check
    # over). This check reads the DOCTRINE, not the config.
    for needle, why in (
        ("merge_method: squash", "the merge applies the batch's diff to main AS IT IS at merge time"),
        (
            "check-success=python-ci-gate",
            "the queue's only re-validation of the moved composition is the TIERED selection",
        ),
    ):
        assert needle in landing, (
            f"the residual-window paragraph must name {needle!r}: {why}, which is the window"
        )
    strict = [
        sentence
        for sentence in re.split(r"(?<=\.)\s", landing)
        if "strict" in sentence and "branch protection" in sentence
    ]
    assert strict, (
        "the residual-window paragraph must state that the branch protection's `strict` is false — "
        "without it the moved base would be refused and the window would not exist. Do not name the "
        "declaration home's path here: spell its live assertion instead (`manifest-integrity` reads it)"
    )
    assert re.search(r"only route that", landing), (
        "the Landing section must say route 2 (the declared freeze) is the ONLY route that closes the "
        "window: routes 1 and a plain pin-checked landing leave it open and therefore owe the detector"
    )
    # ⛔ POLARITY on the ROLE: the comparison runs after the landing, so it cannot prevent it. A sentence
    # that calls it a detector must ALSO say it does not prevent the landing — otherwise the next editor
    # promotes it to prevention and the document claims a guarantee no command in it provides (the
    # class the ``7fb722a9a`` round fixed for the tree assertion, one window over).
    role = [
        sentence
        for sentence in re.split(r"(?<=\.)\s", landing)
        if re.search(r"detector", sentence, re.IGNORECASE)
        and re.search(
            r"does not prevent|never prevents|cannot prevent|is not prevention|not a prevention",
            sentence,
            re.IGNORECASE,
        )
    ]
    assert role, (
        "the post-merge tree comparison is a DETECTOR (this document's own gate/detector split), and the "
        "sentence that names it must also say it does not PREVENT the landing — a bark block run after the "
        "merge cannot prevent anything, and the wording must not read as though it does"
    )
    # ⛔ POLARITY on the comparative claim: the pre-fix sentence said the draft borrows "the guarantee
    # bors and GitHub's queue get by owning the merge". That is the over-claim this check exists to
    # refuse, so any sentence mentioning it must QUALIFY it — a document may compare itself to the
    # prior art, it may not credit the draft with what the prior art gets from owning the merge.
    owning = [
        sentence
        for sentence in re.split(r"(?<=\.)\s", landing)
        if "owning the merge" in sentence
    ]
    assert owning, (
        "the Landing section no longer compares the draft rule to the prior art it is shorter than — "
        "re-point this guard if the comparison moved, but do not drop it: it is what an adopter reads "
        "instead of assuming the draft is the bors guarantee"
    )
    unqualified = [
        sentence
        for sentence in owning
        if not re.search(r"less than|rather than|cannot|never", sentence, re.IGNORECASE)
    ]
    assert not unqualified, (
        "the draft rule is credited with 'the guarantee bors and GitHub's queue get by owning the "
        "merge' without qualification — that guarantee comes from owning the merge, and a draft is an "
        f"ENTRY condition: {unqualified}"
    )
    # 2. The DETECTOR, as an executable fence. A claim with no command is the defect the ``4dc214c02``
    # round fixed for the pin; the same defect one window over would be a paragraph.
    detector = [
        fence for fence in _fences() if any(_LANDED_TREE_DIFF.search(_code(c)) for c in fence)
    ]
    assert detector, (
        "the Landing section must carry the post-merge detector as a command: "
        '`[ "$(git rev-parse "$LANDED^{tree}")" = "$TESTED_TREE" ]` — a landed-tree comparison '
        "nothing executes is the assertion-runs-nowhere defect with one more window"
    )
    assert len(detector) == 1, (
        f"the detector must be ONE fence, not {len(detector)}: a second copy is where the halt gets "
        "dropped from the one an operator reads"
    )
    fence = detector[0]
    reads = [i for i, c in enumerate(fence) if _LANDED_SHA_READ.fullmatch(_code(c))]
    diffs = [i for i, c in enumerate(fence) if _LANDED_TREE_DIFF.search(_code(c))]
    assert len(reads) == 1, (
        "the detector fence must read the landed sha exactly once, as "
        f'`LANDED="$(git rev-parse origin/main)"`: {[fence[i] for i in reads]}'
    )
    assert min(reads) < min(diffs), (
        "the landed sha must be read BEFORE it is compared: a detector that compares an empty or "
        "stale `$LANDED` certifies every landing, including a moved base"
    )
    # ⛔ The COMPARED object is a VALUE, read from the batch PR body — not a branch, and not a
    # remote-tracking ref. `integration/batch-1` is a LOCAL branch and nothing in the doctrine pushes
    # it, so `origin/integration/batch-1` is either ABSENT on the landing actor's box (the fetch then
    # dies on a refspec nobody wrote, behind a bare `|| exit 1` that shows only git's own fatal) or
    # STALE on the drop path (Phase 4 commits the revert locally and nothing pushes again), where a
    # PERFECT landing differs by exactly the dropped PR and is reported as a process failure — the one
    # real violation this detector exists to catch, indistinguishable from it. And even a live ref is
    # the WRONG OBJECT: it records where the branch is NOW, so a commit made after the run moves it
    # and the detector certifies a landing no run has seen. Measured at the pre-fix head: re-pointing
    # the comparison at the tested LOCAL branch red the check (1 of 37), and adding the missing push
    # left it green — the guard-refuses-its-own-remedy shape, one spelling over.
    assert not re.search(r"origin/integration/batch-1", "\n".join(_fenced_commands())), (
        "the doctrine compares the landing against `origin/integration/batch-1`, a ref nothing in it "
        "ever pushes: on a fresh clone it does not exist (and the fetch that reads it dies on a "
        "refspec nobody wrote), and on the drop path it is stale by exactly the dropped PR, so a "
        "PERFECT landing is reported as a process failure. Carry the tested tree BY VALUE instead"
    )
    tested_reads = [i for i, c in enumerate(fence) if _TESTED_TREE_READ.match(_code(c))]
    assert len(tested_reads) == 1, (
        "the detector must read the tested tree from the batch PR body — the same carrier discipline "
        f"as `$PINNED`, since landing is a different shell and usually a different actor: {fence}"
    )
    carrier_halts = [
        i
        for i, c in enumerate(fence)
        if re.match(r'^\[\s*-n\s+"\$TESTED_TREE"\s*\]', _code(c))
    ]
    assert carrier_halts and all(
        _halts(_block(fence, i), subject=_TESTED_TREE_NONEMPTY) for i in carrier_halts
    ), (
        "an ABSENT `tested-tree:` line must HALT the detector: without it `[ … = \"\" ]` compares "
        "the landed tree to nothing, which either fails on a perfect landing (a stale/absent carrier "
        "read as a violation) or — with the halt dropped — certifies it"
    )
    assert min(tested_reads) < min(carrier_halts) < min(diffs), (
        "the tested tree must be read, then refused when absent, BEFORE it is compared: the halt "
        "protects the comparison it precedes"
    )
    tree_reads = [i for i, c in enumerate(fence) if _LANDED_TREE_READ.match(_code(c))]
    assert tree_reads and min(tree_reads) < min(diffs), (
        "the detector must capture the landed commit's TREE (`LANDED_TREE=\"$(git rev-parse "
        "\"$LANDED\"^{tree})\" || exit 1`) before comparing it: a failed `rev-parse` that the fence "
        "keeps going from compares two empty strings and certifies the landing"
    )
    # A STALE remote-tracking ref compares equal to itself — the class this branch already fixed on
    # the pin assertion — so the fetch must precede the read, and the fetch must HALT: an unhalted
    # `git fetch` leaves `origin/main` at whatever the last successful fetch left, and the detector
    # then judges a landing against a commit that is not current `main`.
    fetches = [i for i, c in enumerate(fence) if _is_main_fetch(c)]
    assert fetches and min(fetches) < min(reads), (
        "the detector fence must `git fetch origin main` before reading anything: without it the "
        "comparison reads a local remote-tracking ref, which a moved `main` leaves stale"
    )
    halted_fetch = [fence[i] for i in fetches if not _main_fetch_halts(fence[i])]
    assert not halted_fetch, (
        "the detector's fetch must halt on failure (`… || { …; exit 1; }`): an unhalted fetch leaves "
        f"`origin/main` stale and the detector then judges the wrong landing: {halted_fetch}"
    )
    unhalted = [
        fence[i] for i in diffs if not _halts(_block(fence, i), subject=_LANDED_TREE_DIFF)
    ]
    assert not unhalted, (
        "the detector's comparison must STOP the fence on a mismatch (`… || { …; exit 1; }`): a "
        "comparison whose failure is discarded reports the invariant violated as success: "
        f"{unhalted}"
    )
    # ...and the value it compares must EXIST: every fence that runs the full suite ON THE BATCH
    # BRANCH captures the branch's tree and prints the exact `tested-tree:` line the PR body needs.
    # A detector whose carrier no fence produces is a halt on every landing (fail-closed, but the
    # doctrine would never land), and the re-check is where the tree CHANGES (the revert), so both
    # sites owe it — the same per-fence discipline this file already applies to the skip carriers.
    batch_fences = [
        f
        for f in _fences()
        if any(_SUITE_CMD.match(_code(c)) and TRACK_B_FILTER not in _code(c) for c in f)
        and any(
            match.group("ref").strip('"') == "integration/batch-1"
            for c in f
            if (match := _TREE_ASSERT_REF.match(_code(c)))
        )
    ]
    assert len(batch_fences) >= 2, (
        "the gate run and the post-revert re-check both run the full suite on `integration/batch-1` "
        f"— re-point this guard if the fences moved: {len(batch_fences)}"
    )
    for fence in batch_fences:
        codes = [_code(c) for c in fence]
        captures = [c for c in codes if _TESTED_TREE_CAPTURE.match(c)]
        assert captures, (
            "a fence that runs the full suite on the batch branch must CAPTURE the tested tree "
            '(`TESTED_TREE="$(git rev-parse "integration/batch-1^{tree}")" || exit 1`): the tree '
            "the verdict is about is the tree Landing compares the landing against, and a branch name "
            f"cannot carry it (see the detector): {fence[-3:]}"
        )
        prints = [c for c in codes if _TESTED_TREE_PRINT.match(c)]
        assert prints, (
            "the captured tested tree must be PRINTED as the exact line the batch PR body carries "
            "(`printf 'tested-tree: %s\\n' \"$TESTED_TREE\"`): a value recorded by nobody leaves the "
            "detector comparing against an absent carrier on every landing"
        )
    # The detector compares the LANDED commit to the TESTED tree, never to the pin: comparing
    # `origin/main` to `$PINNED` after the merge is the pre-merge assertion run late, and a squash
    # commit is never equal to the base it was squashed onto.
    assert not re.search(r'\[\s*"\$\(git\s+rev-parse\s+origin/main\)"\s*=\s*"\$PINNED"', "\n".join(fence)), (
        "the post-merge detector must compare TREES (the landed commit to the batch branch), never "
        "repeat the pre-merge `origin/main == $PINNED` assertion: after a squash that comparison is "
        "false on a perfect landing"
    )


#: A stated number of the required contexts, and a stated `batch_size` value — in the YAML spelling
#: AND in prose. Both are properties of live config (the declaration home that
#: `.github/scripts/check-required-set.py --live` asserts, and `.mergify.yml`) rather than of the
#: doctrine — the class the doctrine itself forbids for the `needs: changes` set ("that is a count to
#: READ FROM THE WORKFLOW, never a literal written here"). Measured: the landing section said "the
#: seven required contexts" (right today, stale on the next context added), the queue section quoted
#: `batch_size: 4`, and — at `9f8e12192` — the same section restated the value fifteen lines after
#: promising it never does ("larger than 4"), which the YAML-only spelling could not see: 0 red.
_CONTEXT_ORDINAL = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:required\s+|check-success\s+)?contexts\b",
    re.IGNORECASE,
)
_BATCH_SIZE_LITERAL = re.compile(
    r"batch_size\s*:\s*[\d{]" r"|\b(?:larger|bigger|greater|more)\s+than\s+\d+\b"
)

def test_no_hand_counted_literals_of_live_repo_properties() -> None:
    """the ``4dc214c02`` P3: the two literals that re-stale on a change this document does not own.

    ``:636`` said "``main`` keeps the **seven** required contexts", and ``:61`` quoted
    ``batch_size: 4``. Both are READINGS of live config — the declaration home for `main`'s required
    contexts, whose live comparison is ``.github/scripts/check-required-set.py --live``, and
    ``.mergify.yml``,
    which states that ``batch_size`` is a separate lever PR #5384 owns and may become
    ``{min: 1, max: N}``. The guard pinned member NAMES and the ``needs: changes`` ordinal but not
    these, so nothing could red. Same class, closed the same way: name the source, state no number.

    Non-vacuity: restoring either literal reds this check and nothing else.
    """
    text = _text()
    context_claim = _CONTEXT_ORDINAL.search(text)
    assert context_claim is None, (
        "the doctrine states a hand-written number of required contexts "
        f"({context_claim.group(0)!r}): the count belongs to the declaration home, whose live "
        "assertion is `.github/scripts/check-required-set.py --live`, so any literal re-stales the "
        "moment a context is added — name that assertion, state no number"
    )
    size_claim = _BATCH_SIZE_LITERAL.search(text)
    assert size_claim is None, (
        "the doctrine quotes a `batch_size` VALUE "
        f"({size_claim.group(0)!r}): `.mergify.yml` says batch_size is a separate lever (#5384 owns "
        "raising it, possibly to `{min: 1, max: N}`), so a literal here goes stale on a change this "
        "document does not control — name `.mergify.yml`, state no number"
    )
    # ⛔ The declaration home is named by its LIVE ASSERTION, never by its path: clause (viii)(b) of
    # `tools/mergify_config_guard.py` treats any file under `tests/` that spells that path as an
    # authoritative READER of it (measured: spelling it here reds the REQUIRED `manifest-integrity`
    # job). This check reads the DOCTRINE, not the config — so it names the guard that does read it.
    for needle in (
        "check-required-set.py --live",
        ".mergify.yml",
    ):
        assert needle in text, (
            f"the doctrine must name {needle!r} as the authority where it makes a claim about live "
            "config, since it no longer restates the value"
        )
    assert re.search(r"CONCURRENT batch is out of scope", text), (
        "the batch BRANCH name is fixed (`integration/batch-1`) while the LEDGER is per-batch, so "
        "the doctrine must say a second concurrent batch is out of scope (its own clone/worktree and "
        "a branch named after `$BATCH`) — otherwise the per-batch ledger reads as if two batches "
        "could share one checkout"
    )


#: The claim the doctrine made about comparability, verbatim. It is FALSE while the population
#: differs, and a false sentence is what an operator reasons from — so it is refused by shape.
_FALSE_COMPARABILITY_CLAIM = re.compile(
    r"The filter\s+is what makes the run comparable to CI at all", re.S
)

#: CI's own path exclusion, as the argv spells it.
_SUITE_E2E_ARGV = "--ignore=tests/e2e"


def test_the_documented_suite_is_the_population_ci_runs() -> None:
    """#10400 review P1: the marker filter is not the whole of "comparable to CI".

    `:216` and `:463` ran `pytest tests/` with the marker filter and `:308-311` claimed *"The marker
    filter is what makes the run comparable to CI at all."* It is not: CI's deterministic lanes run a
    **file list** (`tools/ci_selection.py`), and that selector drops the `tests/e2e/` prefix **by
    path** (#1349) — `python-ci.yml` says why in as many words: those browser tests *"only ever
    ERROR here"* without browsers. So `tests/` is a SUPERSET of what CI runs, and the difference is
    the one directory with the worst failure modes for a ledger built from failure IDs:

    * no browsers → every `tests/e2e/` module ERRORs at import, so a healthy batch can never print
      plain `BATCH-GREEN`, only `BATCH-GREEN(BASELINE(<dozens of e2e ids>))`;
    * browsers installed (this fleet installs them for `app-test`) → those tests RUN, and they
      default to LIVE targets (`tests/e2e/test_welcome_page.py` points `WELCOME_URL` at the deployed
      site), so one network-dependent id that is not in the baseline is NEW, convicts an innocent
      PR, and the doctrine's own remedy is to drop it.

    Both halves are pinned: every `tests/` run carries CI's exclusion, and the comparability claim
    names the path selection instead of crediting the marker filter alone.

    Non-vacuity, measured at the pre-fix head: dropping `--ignore=tests/e2e` from either command reds
    this check (and `_suite_argv_ok` independently), and restoring the sentence *"The filter is what
    makes the run comparable to CI at all."* reds it on its own.
    """
    dir_halves = [
        cmd
        for cmd in _fenced_commands()
        if _deterministic_half(cmd)
        and any(token in _SUITE_DIR_TARGETS for token in shlex.split(_code(cmd)))
    ]
    assert len(dir_halves) >= 2, (
        "expected at least the baseline and gate deterministic halves, parsed "
        f"{len(dir_halves)} — re-point this guard"
    )
    missing = [cmd for cmd in dir_halves if _SUITE_E2E_ARGV not in cmd]
    assert not missing, (
        "a `pytest tests/` run does not carry CI's own path exclusion: CI's deterministic lanes run a "
        "FILE LIST and `tools/ci_selection.py` skips the `tests/e2e/` prefix by path (#1349), so this "
        "run's population is a superset of the gate's. Without browsers every e2e module ERRORs (so "
        "the batch can never print plain BATCH-GREEN), and with browsers they RUN against live "
        f"targets and one flaky id convicts an innocent PR: {missing}"
    )
    text = _text()
    assert _FALSE_COMPARABILITY_CLAIM.search(text) is None, (
        "the doctrine still credits the MARKER FILTER alone with making the run comparable to CI: the "
        "population has to match too. Name the path selection (`tests/e2e/` excluded by "
        "`tools/ci_selection.py`, #1349) beside the filter"
    )
    for needle in ("tests/e2e", "tools/ci_selection.py", "#1349"):
        assert needle in text, (
            f"the comparability paragraph must name {needle!r}: it is the second half of what makes "
            "a batch run CI's population, and an unstated selector reads as decoration"
        )


def test_the_ledger_name_is_exported_not_merely_assigned() -> None:
    """#10400 review P2: `$BATCH` was the one carrier the doctrine claimed "needs no carrier".

    All three occurrences were PLAIN assignments (`BATCH="${BATCH:?…}"`), and a plain assignment is
    not in the environment a new shell inherits (measured: `BATCH=1 sh -c 'B=2; sh -c "echo ${B:-UNSET}"'`
    → `UNSET`). The doctrine's own premise is that Phases 1 and 3 are LATER shells, so in the normal
    case the name is empty there and Phase 1 halts at its own `:?` line before anything runs — a
    fail-closed halt, but the claim *"without carrying anything"* is exactly what the run does not do.
    It is the same shell-local class the doctrine spends twenty lines on for `TORTOISE_DB_URI`.

    So Phase 0 — the one fence that can export it — must, and the doc must say that a later plain
    re-assignment PRESERVES the export attribute (otherwise the three fences below look like they
    undo it).
    """
    exported = [
        cmd
        for cmd in _fenced_commands()
        if _LEDGER_NAME.match(_code(cmd)) and re.match(r"^(?:export|readonly|declare|typeset)\b", _code(cmd))
    ]
    assert exported, (
        "the per-batch ledger NAME is only ever ASSIGNED, never EXPORTED: a plain assignment does not "
        "cross a shell boundary, and Phases 1 and 3 are usually later shells — there the name is "
        f"empty and Phase 1 halts at its own `:?` line. Export it in Phase 0: {exported}"
    )
    # …and it must be the DEFINING fence (the one that creates the directory), not some later
    # re-derivation: an export after the mkdir is an export after the operator already needed it.
    defining = [
        fence
        for fence in _fences()
        if any(_LEDGER_CREATE.match(_code(c)) for c in fence)
    ]
    assert defining, "no fence creates the ledger directory — re-point this guard"
    for fence in defining:
        codes = [_code(c) for c in fence]
        assert any(
            _LEDGER_NAME.match(c) and re.match(r"^(?:export|readonly|declare|typeset)\b", c)
            for c in codes
        ), (
            "the fence that CREATES the ledger directory does not export the name it requires: the "
            "operator is told to set a name nothing carries to the phases that use it"
        )
    assert "PRESERVES" in _text() and "export attribute" in _text(), (
        "the doctrine must say that a later PLAIN re-assignment preserves the export attribute — "
        "otherwise the three `BATCH=\"${BATCH:?…}\"` re-derivations read as undoing the export"
    )
    # ⛔ ...and the document must not tell a reader BOTH things. This paragraph used to say `$BATCH`
    # "is the one value that needs no carrier, because `$B` is a DERIVATION of it", while the Phase 0
    # fence that actually exports it says the opposite in as many words ("the 'needs no carrier' claim
    # would be false in exactly the normal case"). A reader who arrives at the first one in a later
    # shell stops looking: the next fence they run halts on `BATCH="${BATCH:?…}"`. Fail-closed, so an
    # operator round trip rather than a wrong verdict — which is why it is a P3 and not more — but it
    # is the same contradiction-with-itself class the doctrine refuses elsewhere, and the only carrier
    # sentence in the document has to be true. The claim is now the HALT, and both halves are pinned.
    text = _text()
    assert "needs no carrier" not in text, (
        "the doctrine still claims some value \"needs no carrier\": `$BATCH` is EXPORTED in Phase 0 "
        "precisely because it does need one, and every later fence re-derives it with `:?` and HALTS "
        "when the shell did not inherit it — so the claim tells a reader in a later shell to skip "
        "the one thing that would have told them"
    )
    assert re.search(r"inherit the name HALTS", text), (
        "the doctrine must say what `$BATCH`'s treatment actually is — Phase 0 exports it, and a shell "
        "that did NOT inherit the name HALTS at its own `:?` rather than writing into another batch's "
        "ledger — otherwise the two halves of the carrier story read as each other's opposite"
    )


#: The revert line itself, as the blame section spells it (`-m 1` is pinned by its own check).
_REVERT_COMMAND = re.compile(r"^git\s+revert\b")


def test_the_post_revert_re_run_is_a_fenced_run_with_every_detector() -> None:
    """#10400 review P2: the run that ISSUES the verdict after a drop carried none of the detectors.

    `:604` said *"Then re-run only the failed files, then one more full suite run — both halves."*
    That is prose. Every detector this doctrine insists on lives in a fence and is enumerated by the
    guard's `_SUITE_CMD` population, so the one run whose result is read into the Phase 5 verdict row
    had: no lane assertion, no clean-tree/tree assertion, no detached status with the
    `rc ∈ {0,1}` COMPLETED predicate, no `tests=` count against the baseline, and no skip carriers.
    That is verbatim the fail-open this branch already fixed for the baseline (`a12792afe`) and for
    the `track_b` half (`a6a71c1e5`), one run further on.

    The id source was ambiguous too: Phase 4 tells the operator the id sets ARE `$B/batch-junit.xml`
    and `$B/batch-trackb.xml`, but after a revert those files still hold the PRE-revert run — so the
    diff a reader is told to perform compares the baseline against the tree that still contained the
    culprit. The re-check therefore writes those same paths, CLEARED first.

    Non-vacuity, measured at the pre-fix head: no full-suite fence exists after the revert, so this
    check reds on its first assert and on nothing else.
    """
    fences = _fences()
    revert_at = _fence_index(lambda fence: any(_REVERT_COMMAND.match(_code(c)) for c in fence))
    assert revert_at >= 0, "the doctrine no longer documents the culprit revert — re-point this guard"
    after = [
        (i, fence)
        for i, fence in enumerate(fences)
        if i > revert_at and any(_SUITE_CMD.match(_code(c)) for c in fence)
    ]
    assert after, (
        "the doctrine drops the culprit and then describes the re-run in PROSE: the run whose result "
        "is read into the verdict row has no fence, so it has none of the detectors this doctrine "
        "requires of every other suite run (lane, tree, COMPLETED status, count, skips). Give it its "
        "own fenced both-halves commands"
    )
    _index, fence = after[0]
    codes = [_code(c) for c in fence]
    runs = [i for i, c in enumerate(codes) if _SUITE_CMD.match(c)]
    assert any(TRACK_B_FILTER in codes[i] for i in runs), (
        "the post-revert re-check runs only the deterministic half every full-suite fence must run "
        "BOTH — a green that excludes `-m track_b` is not a green"
    )
    assert any(_LANE_ASSERT.match(c) for c in codes), (
        "the post-revert re-check does not assert a lane: an unexported run ERRORs every item and "
        "exits 1, which its own COMPLETED predicate accepts — so the re-check certifies a tree on "
        "which nothing ran, and it is the run the VERDICT is read from"
    )
    assert any("git rev-parse --abbrev-ref HEAD" in c for c in codes), (
        "the post-revert re-check never names the tree it runs on: the revert commits on "
        "integration/batch-1, and a later shell can be anywhere — a run on another branch measures a "
        "tree nobody drops from and nobody lands"
    )
    assert any(_STATUS_WRAPPER.match(c) for c in codes), (
        "the post-revert re-check takes no durable exit status: a killed run prints no summary and "
        "carries no failure ids, so it reads as BATCH-GREEN"
    )
    assert any(re.match(r'^\[\s+-n\s+"\$BATCH_TOTAL"', c) for c in codes), (
        "the post-revert re-check does not compare its test COUNT against the baseline's"
    )
    assert any(_SKIP_COUNT_SUBJECT.search(c) for c in codes), (
        "the post-revert re-check does not diff SKIPS against the baseline: a skip carries no failure "
        "id, so neither the status nor the count can see the live suite silently not running"
    )
    # …and both report paths the Phase 4 diff reads are CLEARED before the recheck runs, or the diff
    # compares the baseline against the tree that still contained the culprit.
    cleared = _cleared(codes)
    assert {"batch-junit.xml", "batch-trackb.xml"} <= {p.rsplit("/", 1)[-1] for p in cleared}, (
        "the post-revert re-check does not clear the two reports Phase 4 diffs: `pytest` truncates a "
        "junit file only in `pytest_sessionfinish`, so the PRE-revert report survives the recheck and "
        f"the verdict is computed from the tree that still contained the culprit: {sorted(cleared)}"
    )
