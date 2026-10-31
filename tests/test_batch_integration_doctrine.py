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
  batch's URI lane *skips* rather than reproduces — and the list named only four of
  the gate's **five** non-test members, omitting ``ci-introduced-failure`` (the #9984
  predicate, the rail's own automated version of Phase 1);
* **Phase 0 made the SHARED FalkorDB instance the default** under a ``docker ps``
  precondition that is always true in this fleet, demoting ``tools/test_lane.py`` to a
  fallback — inverting the repo's recorded #5084 rule for the very measurement the
  doctrine calls non-negotiable (peer lanes' graphs enter the batch id set and not the
  baseline's, so Phase 4 convicts an innocent PR);
* the carve-out file count was quoted from memory and was stale.

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
would make the reproduced/not-reproduced list read as a complete partition.

Non-vacuity, **measured** by pointing this module's ``DOC`` at each pre-fix
revision rather than asserted — 16 checks at this head: **13 of 16 red** at
``a17576c43``, **10 of 16** at ``f3c6c803b``, **7 of 16** at ``a8be548762``, **5 of 16**
at ``295d6d385``, **3 of 16** at ``32a2dc87d`` / ``ba7f83feb`` / ``64ae70fc5``, and
**2 of 16** at ``ed1c03b82`` (this round's pre-fix head) — and those last two are
exactly the findings that head's review raised: the
``ci-introduced-failure`` member missing from the leg list, and the shared FalkorDB
instance as the default lane. Each round's head is red on its own round's findings and
on nothing that a later round introduced, so no check reds on a revision that did not
raise it. Three checks the revision matrix alone cannot red are named rather than left
implied: the parser-sanity check and the contract sentences (green by design — they
guard against a re-pointed or deleted document, not against a bad command), and the
``#2985`` extras trap, which every revision here already fixed and which is pinned by
the ``uv sync`` mutants instead. The parser-sanity check is green on every revision by
design: it guards against a re-pointed or deleted document, not against a bad command.
The contract-sentences check is green from ``f3c6c803b`` on, which is when those
sentences landed.

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
blesses ``pin ; true || exit 1``, because ``true`` is a no-op. **111 such mutants
measured at this head: 0 survive** (109 measured at ``c50628450``, plus the two this
round added) — the two this round added are the omitted
``ci-introduced-failure`` member and a fence that executes the SHARED instance as
the lane, each killed by its own check, measured — and the honest document is
0 of 16 red.

Static, no network / DB / graph — but the MODULE still loads `tests/conftest.py`, which fails the
session closed when no lane is exported (`_p4_uri_required` → `_assert_p4_uri_required`). Running
this file with a bare `pytest tests/test_batch_integration_doctrine.py` therefore reports 16 ERRORS
that have nothing to do with the doctrine. Name a lane:

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_batch_integration_doctrine.py -q

(CI is unaffected: the `test` job runs `core`/`tier1` under the COMPUTED lane —
`TORTOISE_DB_URI` only on the full/push arm, `TORTOISE_TEST_CARVE_OUT=1` with no URI
on every PR run (`.github/workflows/python-ci.yml:1013-1034`, wired at `:1537-1542`) —
so this module must stay lane-agnostic: no DB, no URI, no graph. A docs-only PR is
`full=false`, i.e. this file's own load-bearing `tier1` run is URI-less.)
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

#: Commands that may share a line with a pin create without touching the tree it
#: checked out. Anything else must BE a re-runnable pin create (see
#: ``_ends_at_halt``) — an allowlist, because a hard reset moves the tree without
#: matching ``_MOVES_HEAD``.
#: ``cd`` is limited to the no-op forms: ``cd /other-worktree`` would relocate the
#: fence, and worktrees share an object DB, so the pin then succeeds in another repo.
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

#: The VERB, with no opinion about the target. Every population this module
#: quantifies over ("every fence that runs the full suite", "every fence that runs
#: both halves") is derived from THIS: keying them on the target instead let one
#: repointed token remove a fence from the population that must refuse a dirty tree.
_SUITE_CMD = re.compile(r"^uv\s+run\s+pytest\b(?!\s+<)")  # not the <placeholder> bisect leg

#: The two deliberate halves, as term MULTISETS. Order-free (a reordered `-m`
#: passes) but exact — `… and not slow` still *contains* the three exclusion terms
#: while silently deselecting a real marker.
_HALF1_TERMS = ("not", "track_b", "not", "live", "not", "integration")
_HALF2_TERMS = ("track_b",)


def _suite_argv_ok(cmd: str) -> bool:
    """Is this suite command `uv run pytest <target> [-q] [-m FILTER] [--junitxml=…]`?"""
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
    while i < len(rest):
        token = rest[i]
        if token == "-q":
            i += 1
        elif token == "-m" and i + 1 < len(rest):
            filters.append(rest[i + 1])
            i += 2
        elif token.startswith("--junitxml=") and token != "--junitxml=":
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
        return target in _SUITE_FILE_TARGETS
    if sorted(terms) != sorted(_HALF1_TERMS):
        return False
    return target in _SUITE_DIR_TARGETS


#: The only honest shape of the gate's poll line. Without this, `true && echo DONE`
#: reports a half-written log as a finished run.
_POLL = re.compile(
    r'kill\s+-0\s+"\$\(cat\s+/tmp/[\w.-]+\)"\s+2>/dev/null\s+&&\s+echo\s+RUNNING'
    r"\s+\|\|\s+echo\s+.+"
)

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
    return line.rstrip().split("#", 1)[0].rstrip()


def _halts(code: str) -> bool:
    """Does this command stop the fence on failure?

    A pin create is only SAFE because of its trailing halt: on a dirty lane
    ``git switch -C`` refuses (rc=1), and an unhalted fence then runs the merges
    or the suite with HEAD still on the operator's branch — the P1 the fence's
    own comment says ``-C`` prevents. `|| true` is the negation of that, so it
    is refused even though it "contains" a halt-looking token.
    """
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
    return re.search(r"\|\|\s*(exit\s+[1-9]|\{[^}]*exit\s+[1-9])", code) is not None


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


def _pin_subject(segment: str) -> bool:
    """Is this segment the pin create itself?"""
    return bool(_PIN_CREATE_FIRST.match(segment)) and _re_runnable(segment)


def _allows_pin_segment(segment: str) -> bool:
    """A segment that may join a pin create: the create itself, or a no-op."""
    return _re_runnable(segment) or bool(_SAFE_SEGMENT.match(segment))


def _allows_dirty_test(segment: str) -> bool:
    """A segment that may join the dirty-tree test: the test itself."""
    return bool(_DIRTY_TEST.match(segment))


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
        r"(?P<head>.*?)\|\|\s*(?:exit\s+[1-9]|\{[^}]*exit\s+[1-9][^}]*\})\s*",
        _unquoted(code),
    )
    if halt is None:
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
    polls = [cmd for cmd in _fenced_commands() if "kill -0" in cmd]
    assert polls, "the doctrine no longer documents a polled run — re-point this guard"
    bad_polls = [cmd for cmd in polls if not _POLL.fullmatch(_code(cmd))]
    assert not bad_polls, (
        "the poll line must BE `kill -0 \"$(cat /tmp/<run>.pid)\" 2>/dev/null && echo "
        f"RUNNING || echo ...`; anything else reads a half-written log as done: {bad_polls}"
    )
    # ...and the pid it reads must be the pid the SAME fence wrote: editing
    # `echo $! > /tmp/gate.pid` alone leaves `kill -0` failing on an absent file,
    # and the fence then prints DONE over a half-written log.
    for fence in _fences():
        written = {
            match.group(1)
            for cmd in fence
            if (match := re.search(r"echo\s+\$!\s*>\s*(\S+)", _code(cmd)))
        }
        read = {
            match.group(1)
            for cmd in fence
            for match in re.finditer(r'kill\s+-0\s+"\$\(cat\s+(\S+)\)"', _code(cmd))
        }
        if not read:
            continue
        assert written and read <= written, (
            "the poll reads a pid file this fence never wrote: editing either line "
            f"alone makes `kill -0` fail on an absent pid and report DONE. "
            f"writes={sorted(written)}, reads={sorted(read)}"
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
    extras = sorted(
        token for token in shlex.split(_code(syncs[0])) if token.startswith("--extra")
    )
    assert extras == ["--extra embeddings", "--extra parity"] or extras == [
        "--extra",
        "--extra",
        "embeddings",
        "parity",
    ] or sorted(shlex.split(_code(syncs[0]))[2:]) == [
        "--extra",
        "--extra",
        "embeddings",
        "parity",
    ], (
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
    # ...and every command that moves HEAD between the bisect and the revert must
    # land on the branch PHASE 2 CREATES — bound from its `-C <branch>`, not from a
    # `batch-` prefix. Repointing the documented `git switch integration/batch-1` to
    # `integration/base-check` (which Phase 1 is required to create, so it always
    # exists), to `main`, or to `-`, left this green while the revert committed
    # onto the wrong branch and the batch that landed still carried the culprit.
    started = re.search(r"git\s+bisect\s+start\s+(\S+)", _text())
    assert started, "no `git bisect start <branch>` found — re-point this guard"
    batch_branch = started.group(1)
    assert re.search(
        rf"git\s+switch\s+-C\s+{re.escape(batch_branch)}\s+\"?\$PINNED", _text()
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
    benign = re.compile(r"^(?:git\s+(?:log|bisect)\b|uv\s+run\s+pytest\b)")
    stray = [
        c
        for i, c in enumerate(commands)
        if min(bisects) < i < min(reverts)
        and not (_lands_on_branch(c, batch_branch) or benign.match(_code(c)))
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
    uncommitted = [
        cmd for cmd in reverts if re.search(r"(?<!\S)(?:-n|--no-commit|--squash)(?!\S)", cmd)
    ]
    assert not uncommitted, (
        "the revert must COMMIT (`git revert --no-edit -m 1 <merge sha>`): with "
        "`-n`/`--no-commit`/`--squash` the change never lands on the batch branch, "
        f"which then still contains the culprit: {uncommitted}"
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
        if re.fullmatch(r"git\s+fetch\s+origin(?:\s+main)?", _code(c))
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
    assert all(_ends_at_halt(_code(c)) for c in flat), (
        "the batch pin create must END at its halt; a suffix such as "
        f"`&& git switch -` leaves the merges on another branch: {flat}"
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
    fence = next(f for f in batch if any("batch-1" in _code(c) for c in f))
    merges = [i for i, c in enumerate(fence) if re.search(r"\bgit\s+merge\b", c)]
    assert merges, "the batch fence no longer merges anything — re-point this guard"
    before_merge = [c for c in fence[: min(merges)] if _MOVES_HEAD.search(_code(c))]
    assert all(
        '"$PINNED"' in _code(c)
        and _re_runnable(_code(c))
        and _halts(_code(c))
        and _ends_at_halt(_code(c))
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
        if re.fullmatch(r"git\s+fetch\s+origin(?:\s+main)?", _code(c))
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
    unhalted = [landing[0][i] for i in carriers if not _halts(_block(landing[0], i))]
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
        _halts(_block(landing[0], i)) for i in pin_guards
    ), (
        "a guard that is not backed by a halt is an annotation: an empty $PINNED "
        "must stop the fence here, not merely fail a later comparison"
    )
    assert pin_guards and any(
        re.match(r'\[\s*-n\s+"\$PINNED"\s*\]', _code(c)) for c in landing[0]
    ), (
        'the landing fence must guard the pin with `[ -n "$PINNED" ]`: a `-z` '
        "guard refuses every real sha, so the landing step can never pass"
    )
    assert all("--jq" in cmd or "--template" in cmd for cmd in defines), (
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
        # the two TEST legs the batch's URI lane skips rather than runs
        "test-d14-hosted-api",
        "test-record-postgres",
        "embedded_only",
        "TEST_RECORD_DB_URI",
        # …and the NON-TEST members, which no pytest run can reproduce at all.
        # `ci-introduced-failure` is the #9984 predicate: the rail's own automated
        # version of Phase 1, and the one member this list dropped (#10400 P2-1).
        "packs-compile",
        "manifest-integrity",
        "workflow-lint",
        "surface-guard",
        "ci-introduced-failure",
    ):
        assert needle in text, (
            f"the doctrine must name {needle!r} where it lists the legs a pytest run "
            "does not reproduce: every one is a `needs:` of the required "
            "`python-ci-gate`, so omitting it makes the list read as a complete "
            "partition while a gate member goes unmentioned"
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
