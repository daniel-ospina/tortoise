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
* **``git bisect run`` leaves HEAD detached at the first bad commit** and the
  doctrine never returned to the batch branch, so the documented "drop the
  culprit" committed the revert onto a detached HEAD (the batch kept the
  culprit) and the final verdict measured a tree nobody lands;
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
  batch's URI lane *skips* rather than reproduces;
* the carve-out file count was quoted from memory and was stale.

WHAT IS PINNED, precisely. The **command fences** are pinned as commands: a
command edit that would not run reds this file, and a wording edit that keeps the
commands honest does not. Three things are additionally pinned as **substrings**
because they are the document's contract rather than its wording — the
``OVERRIDES:`` line, the verdict markers, and the "never green on a partial lane"
rule — plus the two marker strings (``--no-ff``, ``export TORTOISE_DB_URI=``) that
must sit in the *fence* they belong to, not merely somewhere in the prose, and
the leg names (``test-d14-hosted-api``, ``test-record-postgres``) whose omission
would make the reproduced/not-reproduced list read as a complete partition.

Non-vacuity, **measured** by pointing this module's ``DOC`` at each pre-fix
revision rather than asserted: **11 of 13 red** at ``a17576c43``, **8 of 13** at
``f3c6c803b``, and **4 of 13** at ``a8be548762`` — the four there being exactly
the findings that head's review raised (the landing pin's ``--jq -r``, the
baseline's missing ``-m track_b`` half, the missing clean-tree precondition, and
the two gate test legs the URI lane skips). The parser-sanity check is green on
every revision by design: it guards against a re-pointed or deleted document, not
against a bad command. The contract-sentences check is green from ``f3c6c803b``
on, which is when those sentences landed.

Static, no network / DB / graph — but the MODULE still loads `tests/conftest.py`, which fails the
session closed when no lane is exported (`_p4_uri_required` → `_assert_p4_uri_required`). Running
this file with a bare `pytest tests/test_batch_integration_doctrine.py` therefore reports 13 ERRORS
that have nothing to do with the doctrine. Name a lane:

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_batch_integration_doctrine.py -q

(CI is unaffected: the `core`/`tier1` surface runs in the `test` job, which exports
`TORTOISE_DB_URI`.)
"""

from __future__ import annotations

import re
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

#: A full-suite invocation: ``pytest tests``, ``pytest tests/``, ``pytest ./tests/``
#: — optionally followed by flags or a redirect. ``pytest <some file>`` (the
#: bisect/blame commands) deliberately does NOT match, so it is not required to
#: carry a full-suite marker filter.
_FULL_SUITE = re.compile(r"\bpytest\s+(?:\./)?tests/?\s*(?:$|[-\s>])")

#: The landing-time base check: compare ``origin/main`` to the pinned sha.
_PINNED_MAIN_ASSERTION = re.compile(
    r'\[\s*"\$\(git\s+rev-parse\s+origin/main\)"\s*=\s*"\$PINNED"\s*\]'
)

#: Commands that move HEAD onto a ref — what Phase 1 must do before its suite run.
_MOVES_HEAD = re.compile(r"\bgit\s+(?:switch|checkout)\b")


def _code(line: str) -> str:
    """A fenced line's COMMAND portion — the inline comment stripped first.

    ``_fences()`` drops lines that BEGIN with ``#``, but a trailing ``# ...``
    survives, and a spelling check run against the raw line is therefore
    satisfiable by an annotation rather than by the command
    (``git switch -c … || exit 1   # use switch -C next time`` passed).
    """
    return line.rstrip().split("#", 1)[0].rstrip()


def _halts(code: str) -> bool:
    """Does this command stop the fence on failure?

    A pin create is only SAFE because of its trailing halt: on a dirty lane
    ``git switch -C`` refuses (rc=1), and an unhalted fence then runs the merges
    or the suite with HEAD still on the operator's branch — the P1 the fence's
    own comment says ``-C`` prevents. `|| true` is the negation of that, so it
    is refused even though it "contains" a halt-looking token.
    """
    if "|| true" in code:
        return False
    return re.search(r"\|\|\s*(exit\s+[1-9]|\{[^}]*exit\s+[1-9])", code) is not None


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


#: The test target a marker-scoped run names (``tests/test_ingest_safety.py``).
_TARGET_FILE = re.compile(r"\b(tests/[\w./-]+\.py)\b")


def _text() -> str:
    assert DOC.is_file(), f"{DOC.relative_to(ROOT)} is missing — re-point this guard"
    return DOC.read_text(encoding="utf-8")


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
    return bool(_FULL_SUITE.search(cmd)) and TRACK_B_FILTER not in cmd


def _full_suite_fences() -> list[list[str]]:
    """The fences that carry a full-suite run, in document order."""
    return [fence for fence in _fences() if any(_FULL_SUITE.search(c) for c in fence)]


def _fence_index(predicate) -> int:
    """Index of the first fence satisfying ``predicate``; -1 when there is none."""
    for i, fence in enumerate(_fences()):
        if predicate(fence):
            return i
    return -1


def test_this_guard_parses_the_document() -> None:
    """A parser that finds nothing would make every check below vacuous."""
    commands = _fenced_commands()
    assert len(commands) >= 10, f"parsed only {len(commands)} fenced commands — re-point this guard"
    assert _fences(), "no fenced code block parsed — re-point this guard"
    assert any(_FULL_SUITE.search(cmd) for cmd in commands)


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
    full_suite = [cmd for cmd in _fenced_commands() if _FULL_SUITE.search(cmd)]
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


def test_the_lane_is_selected_and_exported_before_the_first_suite_run() -> None:
    """#10399 review P1-4 / ISSUE 3: the suite is URI-required, so the lane comes first.

    ``tests/conftest.py::_p4_uri_required`` fails the session closed when
    ``TORTOISE_DB_URI`` is unset and the carve-out is not opted in, so a
    documented baseline that precedes the lane selection cannot run at all.

    Resolved against the FENCED COMMANDS, not raw lines: a prose mention of
    ``TORTOISE_DB_URI`` (the doc explains the gate in words, earlier) must not
    satisfy this, or deleting the whole export fence would leave it green.
    """
    commands = _fenced_commands()
    exports = [i for i, cmd in enumerate(commands) if "export TORTOISE_DB_URI=" in cmd]
    suites = [i for i, cmd in enumerate(commands) if _FULL_SUITE.search(cmd)]
    assert exports, (
        "no fenced command exports TORTOISE_DB_URI= — the documented suite would "
        "die at session start (tests/_embedded.py::_assert_p4_uri_required)"
    )
    assert suites, "the doctrine no longer documents a full-suite run — re-point this guard"
    assert min(exports) < min(suites), (
        "the lane is exported only at fenced command "
        f"{min(exports) + 1} but the first full-suite command is at "
        f"{min(suites) + 1}: the baseline would die at session start "
        "(tests/_embedded.py::_assert_p4_uri_required)"
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
    first = _fence_index(lambda fence: any(_FULL_SUITE.search(c) for c in fence))
    assert first >= 0, "no baseline fence found — re-point this guard"
    fence = fences[first]
    moves = [i for i, c in enumerate(fence) if _MOVES_HEAD.search(c) and '"$PINNED"' in c]
    assert moves, (
        "the baseline fence must CHECK OUT the pinned base (`git switch -c … "
        '"$PINNED"` or `git switch --detach "$PINNED"`) before running the '
        "suite; `git branch` creates the ref and leaves HEAD in the operator's "
        "own tree, so the baseline would be measured on the wrong tree"
    )
    run = [i for i, c in enumerate(fence) if _FULL_SUITE.search(c)]
    assert min(moves) < min(run), (
        "the baseline fence checks out the pinned base only AFTER the suite run"
    )


def test_bisect_returns_to_the_batch_branch_before_the_revert() -> None:
    """#10399 review ISSUE 3 (at ``f3c6c803b``) / P1-3: ``bisect run`` leaves HEAD detached.

    ``git bisect run`` ends detached at the first bad commit, so the documented
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
        "`git bisect run` leaves HEAD DETACHED at the first bad commit, so the "
        "revert below never lands on the batch branch"
    )
    assert min(bisects) < min(returns) < min(reverts), (
        "the return to the batch branch must sit BETWEEN the bisect "
        f"(command {min(bisects) + 1}) and the revert (command "
        f"{min(reverts) + 1}); found it at {min(returns) + 1}"
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


def test_the_baseline_refuses_a_dirty_tree() -> None:
    """the ``e715d81b2`` P3: ``git switch -c`` carries uncommitted and untracked files.

    ``git switch -c <branch> <commit>`` re-applies local modifications to the new
    tree (it refuses only where they differ between the two branches) and always
    carries untracked files — so in this fleet's normal state (a lane worktree with
    unreviewed changes) the fence headed "run the suite on ``origin/main`` ALONE"
    measures the lane. The clean-tree precondition is the other half of the fix.
    """
    fences = _fences()
    first = _fence_index(lambda fence: any(_FULL_SUITE.search(c) for c in fence))
    assert first >= 0, "no baseline fence found — re-point this guard"
    baseline = fences[first]
    # A MENTION is not a refusal: `[ -z "$(git status --porcelain)" ]` on its own,
    # `|| true`, and an `echo "[ -z ... ]"` all "contain" the substring, and this
    # test's whole point is that the fence STOPS on a dirty tree.
    dirty_ok = [
        c for c in baseline
        if "git status --porcelain" in c and "exit 1" in c and "|| true" not in c
    ]
    assert dirty_ok, (
        "the baseline fence must REFUSE a dirty tree in the same command "
        "(`[ -z \"$(git status --porcelain)\" ] || { …; exit 1; }`): `git switch -C` "
        "carries uncommitted and untracked files into the baseline tree, so the "
        "run would measure the lane"
    )

    # ...and the same precondition on EVERY fence that runs the full suite: the
    # Phase 3 run is the one that authorizes BATCH-GREEN, so a tree dirtied during
    # phases 2-3 must stop it too.
    for fence in fences:
        if not any(_FULL_SUITE.search(c) for c in fence):
            continue
        assert any(
            "git status --porcelain" in c and "exit 1" in c and "|| true" not in c
            for c in fence
        ), (
            "every fence that runs the full suite must refuse a dirty tree first; "
            f"this one does not: {fence[:3]}"
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
    run = [i for i, c in enumerate(baseline) if _FULL_SUITE.search(c)]
    assert run, "the baseline fence no longer runs the suite — re-point this guard"
    before = [c for c in baseline[: min(run)] if _MOVES_HEAD.search(_code(c))]
    assert all('"$PINNED"' in _code(c) and _re_runnable(_code(c)) for c in before), (
        "every HEAD-moving command before the baseline suite must switch to "
        f"$PINNED; these do not: {before}"
    )
    assert moves, "the baseline fence no longer switches to the pinned base — re-point this guard"
    assert all(_halts(_code(c)) for c in moves), (
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
    assert all(_re_runnable(_code(c)) for c in flat), (
        "rebuilding the batch must reset the branch to the pin; a one-shot create "
        f"leaves the following --no-ff merge on the wrong branch: {flat}"
    )

    # ...and the batch branch must still be the one checked out when the FIRST
    # merge runs. A HEAD-moving line carrying no `$PINNED` is invisible to the
    # checks above: `git switch main` inserted after the pin create leaves every
    # merge landing on the operator's local `main` while integration/batch-1
    # stays at the pin — the same silent wrong-tree the baseline fence's position
    # check closes on the other fence.
    fence = [f for f in batch if any("batch-1" in _code(c) for c in f)][0]
    merges = [i for i, c in enumerate(fence) if re.search(r"\bgit\s+merge\b", c)]
    assert merges, "the batch fence no longer merges anything — re-point this guard"
    before_merge = [c for c in fence[: min(merges)] if _MOVES_HEAD.search(_code(c))]
    assert all(
        '"$PINNED"' in _code(c) and _re_runnable(_code(c)) and _halts(_code(c))
        for c in before_merge
    ), (
        "every HEAD-moving command before the batch's first merge must be the pin "
        f"create; these move HEAD off the batch branch: {before_merge}"
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
    defines = [
        cmd for cmd in landing[0] if re.match(r'(?:export\s+)?PINNED=', cmd)
    ]
    assert defines, (
        "the landing fence uses $PINNED without defining it: Phase 1 set it in a "
        "DIFFERENT shell, so here it is empty and the assertion reports "
        "`main moved:  -> <sha>` against an unmoved main. Materialise it from the "
        "batch PR body (Phase 1 records it there)"
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
    assert any("--jq" in cmd or "--template" in cmd for cmd in defines), (
        "the landing pin must read the batch PR body through `--jq '.body'` or "
        "`--template '{{.body}}'` — that is the only mechanical enforcement of "
        "the tested-tree==landed-tree invariant"
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
    """
    text = _text()
    for needle in (
        "test-d14-hosted-api",
        "test-record-postgres",
        "embedded_only",
        "TEST_RECORD_DB_URI",
    ):
        assert needle in text, (
            f"the doctrine must name {needle!r} where it lists the legs a pytest run "
            "does not reproduce: both are python-ci-gate members the batch's URI "
            "lane skips, so omitting them makes the list read as a complete partition"
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
