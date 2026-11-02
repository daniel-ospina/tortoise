"""Hermetic tests for `tools/substrate_map_check.py` (#5086).

The substrate map (`docs/architecture/SUBSTRATE-REBUILD-MAP.md`) replaced an
issue-only artifact that went stale **three times**, each time silently. This
file pins the guard that makes the machine-checkable half of it falsifiable.

No network, no DB, no gh: the tracker half of the map is deliberately NOT
guarded (see the tool's docstring), so nothing here depends on it.

EVERY TEST STATES, IN ITS DOCSTRING, (a) THE EXACT VALUE/STATE THAT MAKES IT
FAIL and (b) WHY THAT STATE IS REACHABLE. A guard whose negative case cannot be
reached is a test that cannot fail — it proves the code ran, not that the tree
agrees with the document. The negative controls below therefore mutate a COPY of
the real document / a tmp index / a tmp source tree, rather than asserting only
that the shipped tree is clean.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an
unregistered test file).
"""
from __future__ import annotations

import ast
import contextlib
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import substrate_map_check as smc  # noqa: E402

DOC = REPO / "docs" / "architecture" / "SUBSTRATE-REBUILD-MAP.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


#: §5's `:Document` measurement, as the document states it: LINES across FILES.
#: The `files` half is optional in the PATTERN so a document that states only a
#: line count is compared on that number (rather than reported as "the sentence is
#: gone") — its absence is then the failure, not an unparsed match.
_LABEL_CLAIM = re.compile(
    r"occurs on \*\*(\d+)\*\*\s+lines(?:\s+across\s+\*\*(\d+)\*\*\s+files)?"
)

_COUNT_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight")

#: The population §5's claim is stated in: every **tracked** file under
#: `tortoise/`, any suffix. Derived from the index rather than from an extension
#: filter so the check measures the same set the sentence's `git grep` does —
#: `rglob("*.py")` omitted the 28 tracked non-`.py` files under `tortoise/`
#: (`SKILL.md`, `config/*.json|*.yaml`, `pi-hooks/*.ts`, …), so a `:Document`
#: added to one of them raised the true figure while the check still saw 55 and
#: passed: a latent false CLEAN on the number the no-regex-check decision rests
#: on (#5086 review P3, cycle 4).
_TRACKED_DIR = "tortoise"

#: Bound on the index read. `git ls-files` is local (it reads the index, it never
#: fetches), so anything slower means the checkout is broken — a state error.
_INDEX_TIMEOUT_S = 60


def _tracked_files_under(rel: str) -> list[Path]:
    """Every tracked path under `rel`, in index order.

    Not a failure-tolerant read: if git cannot answer, that is a STATE ERROR
    (exit 2), never a quietly narrower population — the exact way a guard and
    the claim it checks end up agreeing because both looked at less.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--", rel],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=_INDEX_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise smc._state_error(f"cannot enumerate tracked files under {rel}/ ({exc})") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        why = detail[0] if detail else "git gave no detail"
        raise smc._state_error(f"cannot enumerate tracked files under {rel}/: {why}")
    return [REPO / p for p in proc.stdout.split("\0") if p]


def _label_hits() -> tuple[int, int, int]:
    """`(lines, files, narrow)` for `:Document` under `tortoise/`.

    Over EVERY tracked file, any suffix — the population §5's sentence is stated
    in (`git grep` over `tortoise/`), not the `.py`-only subset the first cut
    walked. Counting is still done directly rather than through `git grep`, so a
    broken `grep` cannot make the claim and its check agree by both failing;
    the *file list* comes from the index, and a git that cannot supply it is a
    state error rather than an empty population.
    """
    lines = files = narrow = 0
    for path in _tracked_files_under(_TRACKED_DIR):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # NOT a silent skip: a tracked non-text file under `tortoise/` may
            # carry the literal bytes `:Document`, so excluding it would narrow
            # the population the figure is stated in — the false-CLEAN this
            # function was fixed to close. There are zero such files at this
            # head, so this is a bound that cannot fire today; if one appears,
            # the honest answer is a decision about it, not a quiet subtraction.
            raise smc._state_error(
                f"{path.relative_to(REPO)} is a tracked file under {_TRACKED_DIR}/ "
                f"that is not UTF-8 text, so the `:Document` prose count cannot "
                f"include it — decide whether to count or explicitly exclude it "
                f"({exc})"
            ) from exc
        except OSError as exc:
            # A tracked file that cannot be READ is a state error, exactly as in
            # `_tracked_files_under`: dropping it would shrink the population and
            # red the §5 claim against a correct document with a confusing
            # "the tree has fewer lines" message.
            raise smc._state_error(
                f"cannot read {path.relative_to(REPO)}, a tracked file under "
                f"{_TRACKED_DIR}/ ({exc})"
            ) from exc
        hits = [line for line in text.splitlines() if ":Document" in line]
        if not hits:
            continue
        files += 1
        lines += len(hits)
        narrow += sum(1 for line in hits if re.search(r"\(\w*:Document", line))
    return lines, files, narrow


#: The UNPATCHED `Path.read_text`, captured at import. Every recorder below passes
#: through THIS rather than through `Path.read_text` as it happens to be at that
#: moment: `test_a_read_from_another_thread_is_not_attributed_to_label_hits` needs
#: two recorders in one test, and a pass-through captured inside the second one
#: would be the FIRST recorder — two spies chained, each appending to its own list.
_REAL_READ_TEXT = Path.read_text


def _label_hits_reads(
    monkeypatch,
    *,
    scope_to_thread: bool = True,
    during=None,
) -> list[Path]:
    """Every path `_label_hits()` read via `Path.read_text`, in read order.

    `scope_to_thread` records ONLY reads made on the calling thread, and that is
    load-bearing rather than tidy. `monkeypatch.setattr(Path, "read_text", …)`
    patches an attribute on a class, so the patch is GLOBAL TO THE PROCESS until
    teardown (pytest's own documented behaviour: the attribute is replaced, not a
    per-thread view of it) — every other thread that reads a file inside the
    window is recorded as if the function under test had read it.

    Measured on CI rather than argued: `test (a)` of run 41319892722 (job
    124892295398) reddened `test_the_label_population_is_every_tracked_file_
    not_just_py` with `Extra items in the left set: PosixPath('/proc/2839/stat')`.
    `_label_hits` never reads `/proc`; `tortoise/embedded_reaper._pid_alive`
    does (`embedded_reaper.py:991`) from the sweep's worker thread, and the
    unscoped recorder attributed that read to the population control. A false
    RED — the exact mirror of the false CLEAN that control was written to close.

    `during` runs after the patch is installed and before `_label_hits()`, so a
    caller can stage a concurrent read deterministically instead of hoping for
    one (`test_a_read_from_another_thread_is_not_attributed_to_label_hits`).
    """
    read: list[Path] = []
    mine = threading.get_ident()

    def _spy(self, *args, **kwargs):
        if not scope_to_thread or threading.get_ident() == mine:
            read.append(self)
        return _REAL_READ_TEXT(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _spy)
    if during is not None:
        during()
    _label_hits()
    return read


def _population_drift(read: list[Path]) -> list[Path]:
    """Every path in `read` that is not a tracked file under `tortoise/`.

    Sorted, so a failure names the offender instead of leaving the reader to
    reconstruct it from two truncated set reprs — the shape the CI failure at
    `6a938c002` arrived in (`Extra items in the left set: PosixPath('/proc/…')`,
    with neither side of the comparison visible).
    """
    tracked = set(_tracked_files_under(_TRACKED_DIR))
    return sorted({p for p in read if p not in tracked})


def _checkout_history_is_complete() -> bool:
    """Whether THIS clone can walk its WHOLE history.

    Asked of git directly rather than via `smc._is_shallow_clone()`, so a bug in
    the tool's helper cannot make the oracle agree with it. This is the fact the
    tool corroborates before it may read status **1** as drift: in a shallow clone
    the graft boundary cuts the ancestry walk, so a commit that IS an ancestor in
    the complete history answers with the same status an unrelated tree does
    (#10406, the depth-1 `test` job). Anything other than a positive "not shallow"
    is False — a clone whose completeness cannot be established cannot support an
    expectation of CLEAN either.
    """
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=smc.ANCESTRY_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "false"


def _ancestry_is_verifiable_here() -> bool:
    """Whether THIS clone can ADJUDICATE the `measured_at_commit` claim.

    Probed with its own `git` call on purpose — NOT via `smc._is_shallow_clone()`,
    so a bug in the helper cannot make the test agree with it. `actions/checkout`
    fetches one commit by default, so in the CI `test` job this is False and the
    ancestry claim is unprovable there; the REQUIRED `docs` job (fetch-depth 0)
    is where it is proved.

    ⛔ **Status 1 needs the same corroboration the tool applies.** Three of the
    four answers are arm-deciding on their own: `0` is proved ancestry, an absent
    object (128) and a timeout are unverified in ANY clone. Status `1` is the one
    that depends on the checkout — in a shallow clone a TRUNCATED walk returns it
    for a true claim (#10406), so it only means drift where the history is
    complete. Without this mirror the oracle would expect `CLEAN` from a run that
    correctly said `UNVERIFIED` and fail in both branches.

    ⛔ **Bounded with the tool's own bound** (`smc.ANCESTRY_TIMEOUT_S`). Unbounded,
    this oracle ran the exact probe the tool deliberately caps: on a partial clone
    whose absent object costs ~24 s of lazy fetch it could finish and answer
    "verifiable" AFTER the tool had already given up at 10 s, so the two
    disagreed about which arm to expect and both integration tests failed in
    both branches (#5086 review P2, cycle 6). A probe that cannot answer within
    the tool's own bound is not verifiable HERE — the tool will say the same.
    """
    sha = smc._FRONTMATTER_SHA.search(_doc()).group(1)
    try:
        probe = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=smc.ANCESTRY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False
    if probe.returncode == 0:
        return True
    if probe.returncode == 1:
        # "Not an ancestor" — or a graft-truncated walk. Only a complete history
        # tells the two apart, which is exactly what the tool corroborates.
        return _checkout_history_is_complete()
    return False


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    """Commit the staged tree and return the new sha (hermetic identity).

    `commit.gpgsign=false` is pinned alongside the identity: without it a
    developer box with signing enabled globally fails the commit with
    `gpg failed to sign the data`, which would red the ancestry controls below
    for a non-bug (the hazard `test_ci_timing.py` keeps a dedicated case for).
    """
    done = subprocess.run(
        [
            "git", "-c", "user.name=substrate-map", "-c", "user.email=t@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", message,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return _git(repo, "rev-parse", "HEAD")


def _deepened_shallow_clone(tmp_path: Path) -> tuple[Path, str]:
    """A depth-1 PR-merge-ref clone that ANOTHER ref has since deepened.

    The state the `test` job's clone is in by the time this guard runs, built here
    with real git rather than described: `actions/checkout` fetches the merge ref
    at `--depth=1`, then another test in the SAME shard fetches `origin main` into
    the same object store (`tests/test_9397_about_edge_properties.py::_ensure_commit`
    does exactly that, by design — it is what makes that leg run in CI). Main's
    history therefore arrives while HEAD stays the shallow boundary.

    Returns `(clone, sha)` where `sha` is an ancestor of HEAD in the full history
    and is PRESENT in the object store, yet unreachable through the boundary.
    """
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", ".")
    _git(src, "symbolic-ref", "HEAD", "refs/heads/main")
    (src / "f").write_text("1", encoding="utf-8")
    _git(src, "add", "f")
    reachable = _commit(src, "A")
    (src / "f").write_text("2", encoding="utf-8")
    _git(src, "add", "f")
    base = _commit(src, "M0")
    _git(src, "checkout", "-q", "-b", "feature")
    (src / "g").write_text("3", encoding="utf-8")
    _git(src, "add", "g")
    _commit(src, "X")
    _git(src, "checkout", "-q", "main")
    (src / "f").write_text("4", encoding="utf-8")
    _git(src, "add", "f")
    _commit(src, "M1")
    _git(src, "checkout", "-q", "-b", "pr", base)
    (src / "h").write_text("5", encoding="utf-8")
    _git(src, "add", "h")
    _commit(src, "X2")
    _git(
        src, "-c", "user.name=substrate-map", "-c", "user.email=t@example.invalid",
        "-c", "commit.gpgsign=false",
        "merge", "-q", "--no-ff", "-m", "P", "main",
    )
    clone = tmp_path / "clone"
    _git(
        tmp_path, "clone", "-q", "--depth=1", "--branch", "pr",
        f"file://{src}", str(clone),
    )
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    # The deepen: main's history enters the object store; HEAD's boundary stays.
    _git(clone, "fetch", "-q", "--depth=200", "origin", "main")
    _git(clone, "cat-file", "-e", f"{reachable}^{{commit}}")
    return clone, reachable


def test_the_shipped_map_is_clean() -> None:
    """Fails if the document and the tree disagree about any guarded claim.

    Reachable today: `measure()` reads the real document, the real
    `docs/00_index.md`, the real `tortoise/` tree and `git merge-base`. Dropping
    any one of them (e.g. unregistering the doc from the index) reds this.

    ⚠️ The ancestry claim is the ONE exception and it is stated, not silently
    skipped: a `fetch-depth: 1` checkout (the CI `test` job's) cannot walk the
    history, so the question is unanswerable THERE — the object may be ABSENT
    (`git` exits 128) or PRESENT but graft-unreachable under the shallow graft
    (`git` exits **1** about a real ancestor, the status that otherwise means
    drift, so it is NOT drift there — #10356). The offline half is asserted
    FIRST — in a shallow clone a real offline drift still reds this — and only
    then is the ancestry half handed to the job that has the
    history.

    The unverified arm accepts EVERY wording the tool uses for "this checkout
    cannot answer": the absent-object status arm, the shallow status-1 arm (a
    grafted graph answers "not an ancestor" about a real ancestor — #10356), the
    status-1 arm where the completeness probe itself could not answer
    (`_is_shallow_clone()` -> `None`, #10420), and the bounded-probe timeout arm.
    All four are one verdict, so they carry one token — before that they did not,
    and this test FAILED (rather than skipped) whenever the bound tripped (#5086
    review P2, cycle 6).
    """
    result = smc.measure()
    assert result["problems"] == [], result["problems"]
    if not _ancestry_is_verifiable_here():
        assert result["verdict"] == "UNVERIFIED", result
        assert len(result["unverified"]) == 1, result["unverified"]
        assert "cannot be verified here" in result["unverified"][0], result["unverified"]
        pytest.skip(
            "this checkout cannot adjudicate the ancestry claim (`measured_at_commit` "
            "is absent, or present but graft-unreachable in a shallow clone), so the "
            "offline half is all this job proves; the ancestry claim is proved by "
            "the REQUIRED `docs` job, which checks out at fetch-depth: 0 and runs "
            "`tools/substrate_map_check.py --check` unconditionally"
        )
    assert result["verdict"] == "CLEAN", result["problems"]


def test_a_lane_family_that_does_not_resolve_is_drift() -> None:
    """Fails if a §3/§4 path is not checked for existence.

    Reachable: the mutated copy names `tortoise/does_not_exist.py` inside the
    lane table — a family a lane row could plausibly declare and a rename could
    plausibly break.
    """
    doc = _doc().replace("`tortoise/fanout.py`", "`tortoise/does_not_exist.py`", 1)
    problems = smc.check_lane_families(doc)
    assert any("tortoise/does_not_exist.py" in p for p in problems), problems


def test_a_family_outside_the_five_prefixes_is_checked_not_skipped() -> None:
    """Fails if a lane row outside `tortoise|tools|config|tests|docs/` is skipped.

    Reachable: `battery/`, `graph-scripts/`, `integrations/`, `operations/`,
    `packs/`, `supabase/` and `validation/` all exist in this repo, and the map
    is the artifact the review history shows GROWING (L8 was added by this very
    PR) — so a lane row naming a family there is a plausible next edit. Under
    the old five-prefix `_BACKTICK_PATH` such a path never entered the match,
    never reached the existence test, and never entered `seen`, so the guard
    reported CLEAN over a family nobody checked (#5086 review P3, cycle 7).
    """
    doc = _doc()
    anchor = "`tortoise/fanout.py`"
    in_tables = smc._section(doc, "## 3.") + smc._section(doc, "## 4.")
    assert anchor in in_tables, "the §3 anchor is gone — this control measures nothing"

    resolved = doc.replace(anchor, "`battery/runner`", 1)
    assert smc.check_lane_families(resolved) == [], (
        "an EXISTING family under a non-allowlisted prefix must resolve, not "
        "vanish from the population"
    )

    missing = doc.replace(anchor, "`battery/does_not_exist.py`", 1)
    problems = smc.check_lane_families(missing)
    assert any("battery/does_not_exist.py" in p for p in problems), problems


def test_a_lane_row_with_a_prose_only_family_is_reported() -> None:
    """Fails if a §3 lane row can declare its family in prose and go unmeasured.

    Reachable: L6's shipped cell reads `tortoise/consistency.py` ·
    `tortoise/backup.py` · *"the provenance read paths"* and L7's used to read
    *"the three compile seams + pack templates"* — a lane row whose family cell
    carries no backticked path is invisible to `_BACKTICK_PATH`, and the
    document-wide `seen` floor is satisfied by the OTHER rows, so the guard
    reported CLEAN over a family nobody checked (the same narrower-population
    class, one door along). The two-state control is what makes this
    discriminating: a row that keeps a path, or that declares itself prose with
    the marker, must stay clean (#5086 review P3, cycle 8).
    """
    doc = _doc()
    #: The WHOLE cell, not one path in it: a row that keeps any backticked path
    #: is still measured, so substituting a single path would leave this control
    #: proving nothing (the first draft did exactly that and stayed green).
    anchor = (
        "`tortoise/assembly.py` · `tortoise/subgraph.py` · "
        "`tortoise/fanout.py` · `tortoise/search_engine.py`"
    )
    assert anchor in smc._section(doc, "## 3."), (
        "the L4 family cell is gone — this control would substitute nothing the "
        "row checker reads"
    )

    problems = smc.check_lane_families(doc.replace(anchor, "the retrieval seams", 1))
    assert any("declares no checkable file family" in p for p in problems), problems

    declared = smc.check_lane_families(
        doc.replace(anchor, f"the retrieval seams {smc.PROSE_FAMILY_MARKER}", 1)
    )
    assert declared == [], (
        "a prose family that SAYS it is prose must be accepted — the marker is "
        "the document's admission, not a reason to red it"
    )

    named = smc.check_lane_families(doc.replace(anchor, "`tortoise/api.py`", 1))
    assert named == [], named

    #: A backticked token the existence loop SKIPS is not a measured family: a
    #: glob is a rule about a directory and a `.md`/`.yml` is a registry citation,
    #: so a row whose cell holds only those is still measuring nothing. Without
    #: this arm the per-row floor would be satisfied by a token that never enters
    #: the population — the same gap one door along.
    glob_only = smc.check_lane_families(doc.replace(anchor, "`tests/*.py`", 1))
    assert any("declares no checkable file family" in p for p in glob_only), glob_only

    registry_only = smc.check_lane_families(
        doc.replace(anchor, "`config/ci-surfaces.yml`", 1)
    )
    assert any(
        "declares no checkable file family" in p for p in registry_only
    ), registry_only

    #: The row's IDENTIFIER is a population too: a row that renders the same but
    #: is not literally `| **L` must still be measured, or its family is skipped
    #: silently — the same narrower-population class one door along. Both variants
    #: below returned `[]` before the identifier was normalised (measured against
    #: this file's previous head), so each is a reachable control.
    for variant in ("| L4 retrieval-traversal", "| **l4** retrieval-traversal"):
        reformatted = doc.replace(anchor, "the retrieval seams", 1).replace(
            "| **L4** retrieval-traversal", variant, 1
        )
        assert variant in reformatted, f"the L4 row did not take the {variant!r} form"
        found = smc.check_lane_families(reformatted)
        assert any("declares no checkable file family" in p for p in found), (
            f"a lane row written {variant!r} must still be measured; got {found}"
        )


def test_an_unreadable_measurement_input_is_exit_2_not_the_drift_code(monkeypatch) -> None:
    """Fails if an unreadable input escapes as a traceback (exit 1 = DRIFTED).

    Reachable: a symbol file removed, or one blocked by permissions. The module
    contract says exit 2 covers "a measurement input … unreadable/ABSENT here",
    but an unguarded `read_text` escapes `measure()` as a bare `OSError`, so
    `main()` never returns and CPython exits **1** — the code reserved for a
    document that drifted. An operator reading the exit code is then told the map
    is wrong when the checkout could not answer (#5086 review P3, cycle 9).
    """
    target = REPO / smc.INTERFACE_SYMBOLS[0][1]
    real_read_text = Path.read_text

    def _deny(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)
    with pytest.raises(SystemExit) as excinfo:
        smc.check_interface_symbols(_doc())
    assert excinfo.value.code == 2, (
        f"an unreadable measurement input is a state error (2), never the drift "
        f"code; got {excinfo.value.code!r}"
    )


def test_an_unparseable_leaf_module_is_exit_2_not_the_drift_code(tmp_path: Path) -> None:
    """Fails if `ast.parse` throwing `SyntaxError` becomes the drift code.

    Reachable: a leaf module edited into a syntax error. The document is then
    correct and the TREE is unreadable, so the answer is the state error (2), not
    "the document and the tree disagree" (1) — the same wrong-branch failure the
    exit-code contract exists to prevent (#5086 review P3, cycle 9).
    """
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n    pass\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        smc._module_level_internal_imports(broken)
    assert excinfo.value.code == 2, f"got {excinfo.value.code!r}"


def test_a_lane_row_that_stops_looking_like_a_LANE_row_is_still_measured() -> None:
    """Fails if the §3 row population is a ROW-SHAPE allowlist (`| **L`).

    Reachable: the checker used to enumerate rows by a `| **L` text prefix, so
    the SAME prose-family row it was added to catch went unseen the moment its
    id was written unbolded (`| L4 retrieval-traversal |`) — `check_lane_families`
    returned `[]` while the family was measured by nothing. Measured at the
    pre-fix head: the first arm below returns `[]`, i.e. CLEAN over an
    unmeasured lane. A lane table is a table; the population is its body rows,
    not the ones whose first cell happens to start with a bold `L` — and not the
    ones whose first cell starts with `L<digit>` either, which is the same
    allowlist one door along (the `| **Retrieval traversal**` arm below).
    (#5086 review P3, cycle 9).

    The two-state control keeps it discriminating: dropping the bold from the id
    ALONE must stay clean, so the arm proves the row is still PARSED (rather than
    reported for its own sake).
    """
    doc = _doc()
    anchor = "| **L4** retrieval-traversal |"
    family = (
        "`tortoise/assembly.py` · `tortoise/subgraph.py` · "
        "`tortoise/fanout.py` · `tortoise/search_engine.py`"
    )
    assert anchor in smc._section(doc, "## 3."), (
        "the L4 row is gone — this control substitutes nothing the row checker reads"
    )
    assert family in smc._section(doc, "## 3."), (
        "the L4 family cell is gone — the prose arm below would prove nothing"
    )

    unbolded = doc.replace(anchor, "| L4 retrieval-traversal |", 1)
    problems = smc.check_lane_families(unbolded.replace(family, "the retrieval seams", 1))
    assert any("declares no checkable file family" in p for p in problems), (
        f"an unbolded lane row with a prose family is a CLEAN over nothing: {problems}"
    )

    #: The shape test one door further along: normalising the IDENTIFIER (`L\d`)
    #: only moves the allowlist — a row in §3's lane table whose first cell does
    #: not start with `L<digit>` is still a lane row, and is STILL measured by
    #: nothing under a shape rule (measured: that version returns `[]`).
    renamed = doc.replace(anchor, "| **Retrieval traversal** |", 1)
    renamed_problems = smc.check_lane_families(
        renamed.replace(family, "the retrieval seams", 1)
    )
    assert any("declares no checkable file family" in p for p in renamed_problems), (
        "a lane-table row whose id is not `L<n>` must not escape measurement: "
        f"{renamed_problems}"
    )
    assert smc.check_lane_families(renamed) == [], (
        "a renamed row whose family still resolves is still MEASURED, not drift"
    )

    assert smc.check_lane_families(unbolded) == [], (
        "the unbolded row must still be MEASURED — its family still resolves, so "
        "dropping the bold alone is not drift"
    )

    #: A table whose header/separator cannot be found cannot be enumerated at
    #: all; that must be REPORTED, never read as an empty population.
    broken = doc.replace("|---|---|---|---|---|", "| not a separator |", 1)
    assert any("cannot be enumerated" in p for p in smc.check_lane_families(broken)), (
        "an unenumerable lane table must be a stated problem, not silence"
    )


def test_the_lane_table_is_actually_enumerated() -> None:
    """Fails if §3's families stop being parsed, leaving only §4's checked.

    Reachable: §3 originally wrote its families WITHOUT the `tortoise/` prefix
    (`fanout.py`), so the checker silently saw zero of that table's paths and
    this guard would have gone green over an entirely unchecked lane table. The
    floor below is the measured count at the tree of THIS head (22 distinct cited
    paths, 20 of them code — re-derived after L7's family was named, #5086 review
    P3, cycle 8), so a table-shape change reds here rather than passing quietly.
    """
    doc = _doc()
    section = smc._section(doc, "## 3.") + smc._section(doc, "## 4.")
    cited = {raw.split(":")[0].rstrip("/") for raw in smc._BACKTICK_PATH.findall(section)}
    code = {
        p
        for p in cited
        if not p.endswith((".md", ".yml")) and "*" not in p and (REPO / p).exists()
    }
    assert len(code) >= 20, sorted(code)
    assert "tortoise/fanout.py" in code
    #: `tools/` is a bare directory cell with an EMPTY tail. It pins one half of
    #: the widening and only that half: the OLD pattern's tail quantifier was
    #: `+`, so `` `tools/` `` matched neither the old nor a prefix-narrowed
    #: pattern — the empty-tail case was simply never matched. (The cycle-7
    #: review said exactly that, so the first version of this comment had it
    #: backwards; #5086 review P3, cycle 8.) The PREFIX half is pinned by
    #: `test_a_family_outside_the_five_prefixes_is_checked_not_skipped`.
    assert "tools" in code


def test_a_glob_is_not_a_family_path() -> None:
    """Fails if the checker treats `tests/*.py` (a rule) as a required file.

    Reachable: the hotspot section legitimately says `tests/*.py`, and an
    existence check over a glob would report a permanent false drift — a guard
    that reds on a correct document is the failure mode this asserts against.

    ⚠️ The substitution is only meaningful if its anchor is IN a section
    `check_lane_families` reads: a `== []` over a substitution that landed
    nowhere passes silently. So the anchor is asserted first (#5086 review P1
    cycle 1, closing the one vacuity the reviewer flagged).
    """
    doc = _doc()
    substituted = doc.replace("`config/ci-surfaces.yml`", "`tests/*.py`", 1)
    assert substituted != doc, "the §4 anchor `config/ci-surfaces.yml` is gone"
    in_tables = smc._section(doc, "## 3.") + smc._section(doc, "## 4.")
    assert "`config/ci-surfaces.yml`" in in_tables, (
        "the anchor must sit inside §3/§4, or this control substitutes nothing "
        "the checker reads and its `== []` proves nothing"
    )
    assert smc.check_lane_families(substituted) == []


def test_an_interface_symbol_missing_from_its_file_is_drift(
    monkeypatch,
) -> None:
    """Fails if §5's symbol placement is trusted rather than verified.

    Reachable: the retargeted pair below is a real file (`tortoise/fanout.py`)
    that genuinely does not declare `stub_key` — exactly what a move/rename in
    `projection/edges.py` would produce.
    """
    monkeypatch.setattr(smc, "INTERFACE_SYMBOLS", (("def stub_key", "tortoise/fanout.py"),))
    problems = smc.check_interface_symbols(_doc())
    assert any("stub_key" in p and "tortoise/fanout.py" in p for p in problems), problems


def test_an_interface_symbol_dropped_from_the_document_is_drift() -> None:
    """Fails if the doc can stop making a claim while the guard keeps checking it.

    Reachable: deleting the symbol's name from the document is a one-line edit
    that leaves the interface map claiming nothing while the tool still reports
    CLEAN — i.e. the guard silently widening past what anyone reads.
    """
    doc = _doc().replace("compile_value_brief", "compile_value_summary")
    problems = smc.check_interface_symbols(doc)
    assert any("compile_value_brief" in p and "dropped" in p for p in problems), problems


def test_the_d10_retirement_mechanism_is_checked(monkeypatch) -> None:
    """Fails if the retired-key allowlist / relabel migration stops being measured.

    Reachable: `migrate_kinds.py` does not declare `build_master_list`, so
    retargeting the pair is a real file that genuinely lacks the symbol — exactly
    what deleting the migration would leave behind. The `:Document` label itself
    is deliberately NOT regex-checked (55 prose lines across 17 files at this
    head — pinned by `test_the_document_label_prose_count_is_the_LINE_count`), so
    the retirement is guarded through these two symbols instead.
    """
    monkeypatch.setattr(
        smc,
        "INTERFACE_SYMBOLS",
        (("def relabel_legacy_documents", "tortoise/migrate_kinds.py"),),
    )
    assert smc.check_interface_symbols(_doc()) == []

    monkeypatch.setattr(
        smc,
        "INTERFACE_SYMBOLS",
        (("def relabel_legacy_documents", "tortoise/extractor_v2.py"),),
    )
    problems = smc.check_interface_symbols(_doc())
    assert any("relabel_legacy_documents" in p for p in problems), problems


def test_an_unregistered_document_is_drift(monkeypatch, tmp_path: Path) -> None:
    """Fails if docs registration is assumed rather than checked.

    Reachable: an unindexed doc is unfindable from `docs/00_index.md`, which is
    how the map would return to existing only where someone remembers it.
    """
    index = tmp_path / "00_index.md"
    index.write_text("| Area | Path |\n| --- | --- |\n", encoding="utf-8")
    monkeypatch.setattr(smc, "INDEX", index)
    problems = smc.check_registered(_doc())
    assert any("not registered" in p for p in problems), problems


def test_an_absent_object_is_a_state_error_not_document_drift(monkeypatch) -> None:
    """Fails if an object this clone does NOT have is scored as document drift.

    Reachable, and MEASURED in CI before this fix: `actions/checkout` fetches one
    commit by default, so the `test` job's clone does not carry the
    `measured_at_commit` object and `git merge-base --is-ancestor` exits **128**
    (reproduced in a real depth-1 clone of this repo: `fatal: Not a valid commit
    name`, exit 128). Scoring that as `DRIFTED` redded three tests in the shard AND
    told the reader "the document and the tree disagree — do not delete the row to
    make this green", the wrong instruction for a shallow clone: nothing is wrong
    with the document. It must read as a STATE ERROR (exit 2) — never a pass, and
    never a drift. The probe is stubbed here on purpose: the real 128 path costs
    ~24 s on this box's partial clone (git attempts a lazy fetch).
    """
    doc = _doc().replace(
        "measured_at_commit: 4f68a2aa73be4572c3ea88367981ca874ca0e54b",
        "measured_at_commit: 0123456789abcdef0123456789abcdef01234567",
        1,
    )
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 128, "", "fatal: Not a valid commit name"
        ),
    )
    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: True)
    problems, unverified = smc.check_measured_at_commit(doc)
    assert problems == [], problems
    assert len(unverified) == 1, unverified
    assert "cannot be verified here" in unverified[0]
    # The report must name the CAUSE, so the reader is not sent to the document.
    assert "SHALLOW" in unverified[0]
    assert "not a valid commit" in unverified[0].lower()


def test_a_sha_that_is_not_an_ancestor_is_drift(monkeypatch) -> None:
    """Fails if a genuinely unrelated tree is reported as merely unverified.

    Reachable: exit **1** is `git merge-base --is-ancestor`'s "no, not an
    ancestor" — a re-cut against a branch, a rebase that dropped the commit, or a
    sha from another repository. That IS a disagreement between the document and
    the tree, so it must never collapse into the state-error arm.

    The completeness stub states the premise the status alone cannot carry: this
    answer only means drift in a clone that CAN walk its whole history (see
    `test_a_shallow_checkout_cannot_call_an_ancestor_drift` for the other half).
    """
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""),
    )
    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: False)
    problems, unverified = smc.check_measured_at_commit(_doc())
    assert any("not an ancestor" in p for p in problems), problems
    assert unverified == [], unverified


def test_a_shallow_checkout_cannot_call_an_ancestor_drift(monkeypatch) -> None:
    """Fails if a SHALLOW clone's status-1 answer is scored as document drift.

    Reachable, and MEASURED in CI: the `test` job checks out at the default
    `fetch-depth: 1`, and a shallow graft truncates the graph at its boundary —
    so a commit that IS an ancestor answers status **1** as soon as its object
    is present (a partial/deeper fetch, a pack that carried it), with no error
    text. Reproduced in a real depth-1 clone: absent object → 128; the same
    object fetched explicitly → status 1 about a commit that is its own
    ancestor. Before this arm the tool printed "measured_at_commit … is not an
    ancestor of HEAD" — DRIFT, i.e. "the document and the tree disagree, do not
    delete the row" — about a correct document, and redded the CI `test` job
    (run 41167511516). A shallow checkout cannot adjudicate ancestry whatever
    status it returns, so this is the same state error the 128 arm is.

    ⛔ This control landed on `main` independently as #10356 and #10406 keeps it
    — same name, every assertion — rather than retiring it for the sibling it
    adds. On #10406 (`test (e)` in the Python CI run 41183423096, 2026-10-31,
    head `d0cd6c38c`): the depth-1 `test` job reported `measured_at_commit
    4f68a2aa7… is not an ancestor of HEAD` and reded two required tests, while
    the `docs` job of the sibling `CI` run 41183423210 — the same check at
    `fetch-depth: 0`, on the same head — passed, because the merge commit IS a
    descendant of that commit.

    Reproduced in a real depth-1 clone of this repo: writing the object into the
    clone's object DB makes `cat-file -t` succeed and `--is-ancestor` still exit
    1 with `rev-parse --is-shallow-repository` answering `true`.

    ⛔ Non-vacuous: on the pre-fix tool this test FAILS — the drift problem comes
    back and `unverified` is empty. The fix corroborates status 1 with the
    repository's own completeness and defers the claim to the `docs` job.

    The unresolvable arm (`_is_shallow_clone()` → None: git answered the ancestry
    probe but not this one) is asserted too, and it is the arm this control did
    NOT carry before #10406: it cannot certify drift either, and it must not
    claim the checkout IS shallow. Only a positive `False` may accuse the
    document.
    """
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""),
    )
    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: True)
    problems, unverified = smc.check_measured_at_commit(_doc())
    assert problems == [], (
        f"a shallow clone cannot prove a non-ancestor, so this is not drift: "
        f"got problems={problems}")
    assert len(unverified) == 1, unverified
    assert "cannot be verified here" in unverified[0], unverified[0]
    assert "SHALLOW" in unverified[0], unverified[0]
    assert "not an ancestor" in unverified[0], unverified[0]
    assert "is not an ancestor" not in unverified[0], unverified

    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: None)
    problems, unverified = smc.check_measured_at_commit(_doc())
    assert problems == [], problems
    assert len(unverified) == 1, unverified
    assert "cannot be verified here" in unverified[0]
    assert "SHALLOW" not in unverified[0], unverified


def test_an_unreadable_completeness_answer_cannot_certify_drift(monkeypatch) -> None:
    """Fails if an exit-0 payload this code cannot READ is scored as "complete".

    `_is_shallow_clone()` is a tri-state — `True` (this checkout is shallow),
    `False` (it can walk its whole history), `None` (git could not say) — and only
    a positive `False` may promote git's status 1 to DRIFT. The pre-#10406 shape,
    `return proc.stdout.strip() == "true"`, collapses every UNRECOGNISED exit-0
    payload onto `False`, i.e. onto the strongest claim the probe can make ("this
    clone can walk its whole history"). A status-1 ancestry answer then reds the
    REQUIRED `docs` job with "the document and the tree disagree — do not delete
    the row to make this green" about a document that is right: the exact defect
    this module exists to stop, reached through the one input the docstring
    already reserved `None` for.

    git prints exactly `true` or `false` today, so this arm is unreachable on the
    versions measured — the #10406 reviewer recorded it as noted-not-a-finding.
    It is closed anyway because the fallthrough is INVERTED (an unanswerable
    probe is the one path that may accuse a correct document), which is the same
    asymmetry `test_a_shallow_checkout_cannot_call_an_ancestor_drift` guards.

    ⛔ Non-vacuous: on the pre-fix tool BOTH halves fail — the first loop gets
    `False` for the unrecognised payloads, and the end-to-end arm gets a drift
    problem instead of the state error.
    """
    # The two words git actually prints keep their meaning (positive controls).
    for payload, expected in (("true\n", True), ("false\n", False)):
        monkeypatch.setattr(
            smc.subprocess,
            "run",
            lambda *a, _p=payload, **k: subprocess.CompletedProcess(a[0], 0, _p, ""),
        )
        assert smc._is_shallow_clone() is expected, payload

    # Anything else is "git could not say" — never "the history is whole".
    for payload in ("yes\n", "TRUE\n", "\n", "fatal: not a git repository\n"):
        monkeypatch.setattr(
            smc.subprocess,
            "run",
            lambda *a, _p=payload, **k: subprocess.CompletedProcess(a[0], 0, _p, ""),
        )
        assert smc._is_shallow_clone() is None, payload

    # End to end, through the LIVE `_is_shallow_clone` (not a stub of it): the
    # ancestry probe answers status 1 and the completeness probe answers an
    # unreadable exit-0 word, which must land in the state error, not in drift.
    def _unreadable_completeness(argv, **_kwargs):
        if "--is-shallow-repository" in argv:
            return subprocess.CompletedProcess(argv, 0, "yes\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(smc.subprocess, "run", _unreadable_completeness)
    problems, unverified = smc.check_measured_at_commit(_doc())
    assert problems == [], (
        f"an unanswerable completeness probe cannot certify drift: {problems}")
    assert len(unverified) == 1, unverified
    assert "cannot be verified here" in unverified[0], unverified[0]
    # It must not claim the checkout IS shallow either — the honest cause is that
    # git did not answer, and the two causes read differently on purpose.
    assert "SHALLOW" not in unverified[0], unverified


def test_status_1_for_a_PRESENT_yet_grafted_commit_is_the_real_git_verdict(
    monkeypatch, tmp_path: Path
) -> None:
    """Fails if the arm above rests on a git behaviour that does not happen.

    The stub above asserts the tool's DECISION for status 1 in a shallow clone;
    this asserts its PREMISE with real git, which is what makes that decision
    reachable rather than hypothetical — and what makes it non-vacuous if git's
    own behaviour changes: a dead arm would otherwise keep passing its stub.

    Measured on the runner's git (2.56.0) and on 2.43.0: with the object ABSENT
    `--is-ancestor` exits **128**; after the deepening fetch it is PRESENT
    (`cat-file -e` succeeds) and exits **1** — the same status a genuinely
    unrelated tree produces — while `rev-parse --is-shallow-repository` still
    answers `true`. So in that state the tool must report the STATE arm: the
    document is not wrong, the history is cut.
    """
    clone, sha = _deepened_shallow_clone(tmp_path)
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
        cwd=clone,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 1, (
        f"this control exists for the state where git answers 'not an ancestor' "
        f"about a PRESENT commit; got rc={probe.returncode} ({probe.stderr.strip()})"
    )
    monkeypatch.setattr(smc, "ROOT", clone)
    problems, unverified = smc.check_measured_at_commit(
        f"---\nmeasured_at_commit: {sha}\n---\n"
    )
    assert problems == [], (
        f"a truncated history cannot prove non-ancestry — the document is not "
        f"wrong; got problems={problems}"
    )
    assert len(unverified) == 1, unverified
    assert "cannot be verified here" in unverified[0], unverified
    assert "SHALLOW" in unverified[0], unverified
    assert "not an ancestor" in unverified[0], unverified


def test_an_unbounded_ancestry_probe_cannot_hang_the_gate(monkeypatch) -> None:
    """Fails if a slow/hanging git probe is allowed to read as clean — or as drift.

    Reachable: measured on this box's partial clone, an absent sha makes
    `git merge-base --is-ancestor` spend ~24 s attempting a lazy fetch. Without
    the bound the gate blocks; with the bound as a PASS it would silently certify
    a tree nobody verified — so the timeout must not be clean. It must not be
    DRIFT either: the claim was never adjudicated, and "the document and the tree
    disagree" is a false instruction about a correct document on a slow clone.
    The split keys on git's own status (`--is-ancestor` errors with "a non-zero
    status that is not 1"), so the timeout joins the status-128 arm below:
    unverified, exactly one entry, exit 2 (#5086 review P2, cycle 4).
    """
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=smc.ANCESTRY_TIMEOUT_S)

    monkeypatch.setattr(smc.subprocess, "run", _boom)
    problems, unverified = smc.check_measured_at_commit(_doc())
    assert problems == [], (
        f"a timeout is not drift — the document is not wrong, the clone cannot "
        f"answer; got problems={problems}"
    )
    assert len(unverified) == 1, unverified
    assert "could not be verified within" in unverified[0], unverified


def test_the_unverified_arm_carries_one_token_whichever_probe_produced_it(
    monkeypatch, capsys
) -> None:
    """Fails if the timeout arm's report is worded differently from the status arm's.

    Reachable on a PARTIAL clone: the 10 s bound trips (measured ~24 s of lazy
    fetch on this box), so the timeout arm — not the status-128 arm — produces
    the `UNVERIFIED` report. Both arms mean the same thing ("this checkout cannot
    answer the claim"), and every caller keys on the token the 128 arm uses, so
    the timeout arm must carry it too. Before the fix it said only "could not be
    verified within", so `test_the_shipped_map_is_clean` and `test_cli_exit_codes`
    FAILED — not skipped — in that state, and the CLI's stdout could not be
    matched to the verdict it printed (#5086 review P2, cycle 6).
    """
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=smc.ANCESTRY_TIMEOUT_S)

    monkeypatch.setattr(smc.subprocess, "run", _boom)
    code = smc.main(["substrate_map_check.py", "--check"])
    printed = capsys.readouterr().out
    assert code == 2, printed
    assert "UNVERIFIED" in printed, printed
    assert "cannot be verified here" in printed, printed
    assert "could not be verified within" in printed, printed
    # The drift instruction must NOT be printed: nothing is wrong with the document.
    assert "The document and the tree disagree" not in printed, printed


def test_the_test_oracle_cannot_outlive_the_tools_own_bound(monkeypatch) -> None:
    """Fails if the tests' own probe is unbounded, so it can out-vote the tool.

    Reachable on a partial clone whose absent object costs ~24 s: unbounded, this
    oracle finished and answered "verifiable" AFTER the bounded tool had given up
    at 10 s — so the tests expected `CLEAN`/exit 0 from a run that correctly said
    `UNVERIFIED`/exit 2 and failed in both branches. The oracle must give up where
    the tool does (#5086 review P2, cycle 6).
    """
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=smc.ANCESTRY_TIMEOUT_S)

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _ancestry_is_verifiable_here() is False


def test_a_missing_measured_at_commit_is_drift() -> None:
    """Fails if the provenance field is optional.

    Reachable: a re-cut that forgets the frontmatter field would otherwise read
    as clean while naming no tree at all (`tools/finding_provenance.py`'s rule).
    """
    doc = "\n".join(
        line for line in _doc().splitlines() if not line.startswith("measured_at_commit:")
    )
    assert smc.check_measured_at_commit(doc) == (
        ["frontmatter carries no `measured_at_commit: <sha>`"],
        [],
    )


def test_a_constant_the_document_places_must_be_DECLARED_not_mentioned(
    monkeypatch, tmp_path: Path
) -> None:
    """Fails if "still declared" is a bare substring match over the file.

    Reachable on the SHIPPED tree: `_DOC_RETIRED_KEYS` is declared once and
    mentioned on six further lines of `projection/entities.py` (a docstring, two
    comments, three `self.` readers — `_SOURCE_HANDLED` has seven). So blanking
    the D10 retirement ALLOWLIST itself — the mechanism §5 of the document and the
    tool docstring both say this guard holds, on the one claim the doc compensates
    for by dropping the `:Document` regex — still returned `[]`: a false CLEAN.
    The mutation below is exactly that state: the declaration line replaced, every
    mention kept.
    """
    monkeypatch.setattr(
        smc,
        "INTERFACE_SYMBOLS",
        (("_DOC_RETIRED_KEYS", "tortoise/projection/entities.py"),),
    )
    # Positive control first: the shipped declaration satisfies the check, so the
    # failure below cannot come from the pair simply never resolving.
    assert smc.check_interface_symbols(_doc()) == []

    lines = (REPO / "tortoise/projection/entities.py").read_text(encoding="utf-8").splitlines(
        keepends=True
    )
    decl = next(
        i for i, line in enumerate(lines) if line.lstrip().startswith("_DOC_RETIRED_KEYS:")
    )
    lines[decl] = "    # D10 allowlist declaration REMOVED by this test\n"
    mutated = "".join(lines)
    assert "_DOC_RETIRED_KEYS" in mutated, "the mentions must survive the mutation"

    (tmp_path / "tortoise" / "projection").mkdir(parents=True)
    (tmp_path / "tortoise" / "projection" / "entities.py").write_text(
        mutated, encoding="utf-8"
    )
    monkeypatch.setattr(smc, "ROOT", tmp_path)
    problems = smc.check_interface_symbols(_doc())
    assert any("_DOC_RETIRED_KEYS declaration missing" in p for p in problems), problems


def test_a_state_error_exits_2_not_the_drift_code(monkeypatch, tmp_path: Path) -> None:
    """Fails if "the map is GONE" reports the exit code that means "the map DRIFTED".

    Reachable: `raise SystemExit("<message>")` prints the message and exits **1**
    — the DRIFTED code — so the documented tri-state (0 clean · 1 drifted · 2 a
    state error) was implemented for USAGE only, and an operator separating the
    two by exit code re-measured the document instead of fixing the checkout. Both
    state paths are asserted here: the absent document in-process, and the whole
    CLI end-to-end (a copy of the tool with nothing beside it — the same state a
    `mv` of the document produces).
    """
    missing = tmp_path / "docs" / "architecture" / "SUBSTRATE-REBUILD-MAP.md"
    monkeypatch.setattr(smc, "ROOT", tmp_path)
    monkeypatch.setattr(smc, "DOC", missing)
    with pytest.raises(SystemExit) as excinfo:
        smc.measure()
    assert excinfo.value.code == 2, excinfo.value.code

    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    tool = tool_dir / "substrate_map_check.py"
    tool.write_text(Path(smc.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    run = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "state error" in run.stderr, run.stderr


def test_an_unverified_ancestry_probe_is_a_state_error_exit(monkeypatch) -> None:
    """Fails if UNVERIFIED becomes a pass (exit 0) or a drift (exit 1).

    Reachable in CI by construction: the `test` job's checkout is shallow, so
    `measure()` returns UNVERIFIED there. That must exit **2** — the documented
    "an input is unreadable/absent, a state error, NOT a pass" arm — so a caller
    that only checks for 0 cannot mistake it for a clean map.
    """
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, "", "fatal: Not a valid commit name"),
    )
    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: True)
    result = smc.measure()
    assert result["verdict"] == "UNVERIFIED", result
    assert result["problems"] == [], result["problems"]
    assert smc.main(["substrate_map_check.py", "--check"]) == 2


def test_the_json_manifest_names_what_it_does_not_check() -> None:
    """Fails if the tool ever implies it guards the tracker half.

    Reachable: issue state needs the network and the doc records a dated
    measurement instead. A guard that claimed it and silently could not would be
    the false PASS the document exists to prevent.
    """
    result = smc.measure()
    assert any("issue_state" in entry for entry in result["not_checked"]), result
    assert "lane_and_hotspot_file_families" in result["checked"]
    # The unverified channel is part of the manifest: a caller must be able to see
    # WHICH claim a state error stopped, instead of inferring it from the exit code.
    assert "unverified" in result, result


def test_the_label_population_is_every_tracked_file_not_just_py(monkeypatch) -> None:
    """Fails if the `:Document` count is measured over a NARROWER set than §5 claims.

    Reachable — and it WAS the shipped state: `_label_hits()` walked
    `(REPO / "tortoise").rglob("*.py")` while §5's sentence states its figure in
    `git grep`'s population (all tracked files under `tortoise/`). The two sets
    differ by **28** tracked non-`.py` files (`tortoise/onboarding/SKILL.md`,
    `tortoise/config/*.json|*.yaml`, `tortoise/pi-hooks/*.ts`, …), so a
    `:Document` occurrence added to one of them raises the true figure while a
    `.py`-only walk still returns 55 — the assertion then compares 55 against a
    document that says 55 and PASSES, leaving the stale number in place. That is
    the false-CLEAN class this file exists to close.

    The control asserts on the POPULATION `_label_hits` ITSELF reads — recorded
    by spying on `Path.read_text` — not on the helper it happens to call. That
    distinction is load-bearing: the first cut of this fix asserted only that
    `_tracked_files_under()` returns a non-`.py` file, which stayed green with
    `_label_hits`'s own loop reverted to `rglob("*.py")` — the two populations
    are numerically identical today (all 55 hits are in `.py` files), so the
    count matches either way and the latent false CLEAN survives behind a green
    suite. A control on the helper is not a control on the count (#5086 review
    P2, cycle 5).

    The recorder is THREAD-SCOPED (`_label_hits_reads`) so a concurrent read by an
    unrelated worker thread cannot be mistaken for one of `_label_hits`'s — that
    false RED reddened CI, and `test_a_read_from_another_thread_is_not_attributed_
    to_label_hits` pins both halves of it.
    """
    read = _label_hits_reads(monkeypatch)

    assert read, "`_label_hits` read no files at all"
    non_py = [p for p in read if p.suffix != ".py"]
    assert non_py, (
        "`_label_hits` read only `.py` files, so a `:Document` minted in a "
        "tracked SKILL.md / config *.yaml / *.ts under tortoise/ raises the true "
        "figure while this check keeps reporting the old number and PASSES — "
        "exactly the latent false CLEAN the index-derived population closed"
    )
    drifted = _population_drift(read)
    assert not drifted, (
        "`_label_hits` read a file that is not tracked under tortoise/ — the "
        "population drifted off the one §5's `git grep` sentence is stated in: "
        + ", ".join(str(p) for p in drifted[:5])
    )


def test_a_read_from_another_thread_is_not_attributed_to_label_hits(monkeypatch) -> None:
    """Fails if a read made by ANOTHER thread lands in `_label_hits`'s population.

    Reachable, and measured on CI: `test (a)` of run 41319892722 (job
    124892295398) reddened `test_the_label_population_is_every_tracked_file_
    not_just_py` with `Extra items in the left set: PosixPath('/proc/2839/stat')`.
    `_label_hits` never reads `/proc`; `tortoise/embedded_reaper._pid_alive` does
    (`embedded_reaper.py:991`), on the sweep's worker thread — and because
    `Path.read_text` is patched on the CLASS, every thread that reads a file
    inside the window is recorded. The control then compared a foreign path
    against the tracked population and failed: a false RED, the mirror of the
    false CLEAN it exists to close, reddening the required `test` job with no
    defect behind it.

    Both halves are asserted, so this cannot regress in either direction: the
    UNscoped recorder DOES see the other thread's read (the race is reproduced
    deterministically through `during`, not assumed), and the scoped one — the
    one the population control uses — does not.
    """
    #: A path `_label_hits()` provably never reads: not under `tortoise/`, and it
    #: need not exist — the recorder appends BEFORE the read, exactly as it did
    #: for `/proc/<pid>/stat`, so an `OSError` here is the steadiest case rather
    #: than a weak one.
    foreign = REPO / ".git" / "a-path-another-thread-read.txt"

    def _another_thread_reads() -> None:
        def _probe() -> None:
            with contextlib.suppress(OSError):
                foreign.read_text(encoding="utf-8")

        thread = threading.Thread(target=_probe, daemon=True)
        thread.start()
        thread.join(timeout=_INDEX_TIMEOUT_S)
        assert not thread.is_alive(), (
            "the probe thread did not finish inside the patched window, so this "
            "test would prove nothing about what a concurrent read is attributed to"
        )

    unscoped = _label_hits_reads(
        monkeypatch, scope_to_thread=False, during=_another_thread_reads
    )
    assert foreign in unscoped, (
        "the unscoped recorder did not see the other thread's read, so the "
        "contamination this test guards against was not reproduced — the "
        "scoped assertion below would then be vacuous"
    )

    scoped = _label_hits_reads(monkeypatch, during=_another_thread_reads)
    assert scoped, "`_label_hits` read no files at all on the calling thread"
    assert foreign not in scoped, (
        "a read made on ANOTHER thread was attributed to `_label_hits`, which "
        "reds the population control on a foreign path and reports a defect "
        "that is not there (CI: `/proc/2839/stat` from the reaper's sweep thread)"
    )
    assert set(scoped) <= set(_tracked_files_under(_TRACKED_DIR)), (
        "the scoped population drifted off the tracked set under tortoise/ — "
        "the population §5's `git grep` sentence is stated in"
    )


def test_a_same_thread_read_outside_the_index_is_still_drift(monkeypatch) -> None:
    """Non-vacuity for the thread scoping, which is the half main's control cannot see.

    Scoping the recording to one thread is a way to make the population control
    unfalsifiable — record nothing, assert nothing — and
    `test_a_read_from_another_thread_is_not_attributed_to_label_hits` does not
    exclude it: it asserts the scoped list is NON-EMPTY and that everything in it
    is tracked, both of which stay true of a recorder that drops every path
    outside the index. A read of a tracked file is not drift, so a recorder that
    only ever records the tracked population passes that test while reporting
    CLEAN for a `_label_hits` that had wandered off it.

    This is the missing direction: a read of a path outside the index made on the
    SAME thread is still recorded and still reported as drift, so the guard keeps
    its discriminating power over exactly the reads `_label_hits` itself performs.
    """
    stray = REPO / "tests" / "test_substrate_map.py"

    def _a_stray_read() -> None:
        stray.read_text(encoding="utf-8")

    drifted = _population_drift(_label_hits_reads(monkeypatch, during=_a_stray_read))
    assert drifted == [stray], drifted


def test_an_index_git_that_cannot_answer_is_a_state_error_not_an_empty_population(
    monkeypatch,
) -> None:
    """Fails if a broken git silently narrows the population to nothing.

    Reachable: exit 128 (outside a work tree, a corrupt index, a permissions
    fault). An empty population would make every `:Document` figure compare 0
    against 0 and report CLEAN, which is the fail-open this whole module is
    written against — so the read must raise the state error (exit 2) instead.
    """
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, "", "fatal: not a git repository"),
    )
    with pytest.raises(SystemExit) as excinfo:
        _tracked_files_under(_TRACKED_DIR)
    assert excinfo.value.code == 2, (
        f"a git that cannot enumerate the population is a state error (2); got "
        f"{excinfo.value.code!r}"
    )


def test_a_leaf_row_that_imports_internally_is_drift() -> None:
    """Fails if §5 can call a module a `*leaf*` while it imports `tortoise/` code.

    Reachable, and it WAS the shipped state (#5086 review cycle 5, P2 — the half
    of finding 3 the inbound-count fix left behind): the interface map's `depends
    on` column said `*leaf*` for `vet_gate.py` while that file opens with a
    module-level `from . import value_gate` (`vet_gate.py:149`). Every other row
    lists the modules it depends on, so the `*leaf*` cells are the only ones
    asserting EMPTINESS — and emptiness is what a lane plans a rebase on.

    Three things are pinned, and each fails on a state that was real or is one
    edit away: (a) the shipped document has no such row; (b) restoring the
    pre-fix cell (`*leaf*` beside `vet_gate.py`) reds with that module named — on
    the pre-fix document this IS the shipped text, so the check reds there; (c)
    a document whose `*leaf*` cells have all been renamed reports that it is
    measuring nothing, rather than passing over an empty set.
    """
    doc = _doc()
    assert smc.check_leaf_rows(doc) == [], smc.check_leaf_rows(doc)

    drifted = doc.replace("| `vet_gate.py` | `value_gate` |", "| `vet_gate.py` | *leaf* |")
    assert drifted != doc, (
        "the substitution anchor is gone — the `vet_gate.py` row no longer reads "
        "`value_gate` in its `depends on` cell, so update this control's anchor "
        "(and re-check §5) rather than deleting the control"
    )
    problems = smc.check_leaf_rows(drifted)
    assert any("vet_gate.py" in p and "value_gate" in p for p in problems), (
        f"§5 calling `vet_gate.py` a *leaf* must be drift, naming the import it "
        f"hides; got: {problems}"
    )

    emptied = doc.replace("| *leaf* |", "| nothing |")
    assert emptied != doc, "§5 has no `*leaf*` cell to rename — see (a)"
    problems = smc.check_leaf_rows(emptied)
    assert any("no `*leaf*` depends-on row" in p for p in problems), (
        "a document with no `*leaf*` row must report that the check is measuring "
        f"nothing, not pass over an empty set; got: {problems}"
    )


def test_a_module_scope_try_wrapped_internal_import_is_not_a_leaf(tmp_path: Path) -> None:
    """Fails if "module level" means "a direct child of the module body".

    Reachable: `try: from .security import redact_error / except ImportError: ...`
    at module scope RUNS at import time and creates exactly the cycle the `*leaf*`
    cell denies, but it is an `ast.Try` at `body`, so a walk over `tree.body`'s
    direct `Import`/`ImportFrom` children never saw it and reported the module as
    a leaf — a false CLEAN on the property the map calls load-bearing
    ("emptiness is what a lane plans a rebase on"). The same holds for a
    module-scope `if` that is not the `TYPE_CHECKING` guard, and for `with`.

    The negative half is as load-bearing as the positive one: a
    `TYPE_CHECKING` block and an in-function import must stay exempt, because
    neither runs at import time — that is how `source_identity` and `quota`
    deliberately break their cycles, and the shipped tree is CLEAN on that
    reading (#5086 review P3, cycle 6).
    """
    module = tmp_path / "wrapped.py"
    module.write_text(
        "from __future__ import annotations\n"
        "try:\n"
        "    from . import security\n"
        "except ImportError:\n"
        "    security = None\n"
        "if True:\n"
        "    from . import value_gate\n"
        "if TYPE_CHECKING:\n"
        "    from . import entities\n"
        "if typing.TYPE_CHECKING:\n"
        "    from . import edges\n"
        "def lazy():\n"
        "    from . import redact\n",
        encoding="utf-8",
    )
    imports = smc._module_level_internal_imports(module)
    assert imports == [".security", ".value_gate"], imports

    clean = tmp_path / "clean.py"
    clean.write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "if TYPE_CHECKING:\n"
        "    from . import entities\n"
        "def lazy():\n"
        "    from tortoise import projection\n",
        encoding="utf-8",
    )
    assert smc._module_level_internal_imports(clean) == []


def test_a_class_body_loop_body_or_type_checking_else_import_is_not_a_leaf(
    tmp_path: Path,
) -> None:
    """Fails if "module level" stops at the block types cycle 7 added.

    Reachable: every construct below is a BLOCK the language reference §4.1
    defines as "a piece of Python program text that is executed as a unit" — a
    `class` body, a `for`/`while` body, a `match` case body, and the `else:` arm
    of an `if TYPE_CHECKING:` guard (which runs precisely because the guard's
    body does not). Each executes at import time and each returned `[]` before
    the fix, so the control is reachable rather than vacuous: a module-scope
    `class C: from . import x` creates exactly the cycle the `*leaf*` cell
    denies, and the guard reported the file as a leaf.

    The negative half is as load-bearing: `def` bodies and the `TYPE_CHECKING`
    body stay exempt — that is how `source_identity` and `quota` deliberately
    break their cycles, and the shipped tree is CLEAN on that reading (#5086
    review P3, cycle 7).
    """
    module = tmp_path / "blocks.py"
    module.write_text(
        "from __future__ import annotations\n"
        "class C:\n"
        "    from . import from_class_body\n"
        "for _i in range(1):\n"
        "    from . import from_for_body\n"
        "while False:\n"
        "    from . import from_while_body\n"
        "match 1:\n"
        "    case 1:\n"
        "        from . import from_match_case\n"
        "if TYPE_CHECKING:\n"
        "    from . import exempt_from_guard_body\n"
        "else:\n"
        "    from . import from_guard_else\n"
        "def lazy():\n"
        "    from . import exempt_from_function\n",
        encoding="utf-8",
    )
    imports = smc._module_level_internal_imports(module)
    assert imports == [
        ".from_class_body",
        ".from_for_body",
        ".from_guard_else",
        ".from_match_case",
        ".from_while_body",
    ], imports

    clean = tmp_path / "clean_blocks.py"
    clean.write_text(
        "from __future__ import annotations\n"
        "if TYPE_CHECKING:\n"
        "    from . import entities\n"
        "class C:\n"
        "    def method(self):\n"
        "        from tortoise import projection\n"
        "for _i in range(1):\n"
        "    def inner():\n"
        "        from . import redact\n",
        encoding="utf-8",
    )
    assert smc._module_level_internal_imports(clean) == []


def test_an_unreadable_tracked_file_is_a_state_error_not_a_narrower_count(
    monkeypatch,
) -> None:
    """Fails if a tracked file that cannot be READ silently shrinks the count.

    Reachable: a tracked file deleted from the working tree, or one blocked by
    permissions. Dropping it narrows the population, so §5's figure is then
    compared against a smaller tree — a *false red* ("the tree has 34 lines")
    against a correct document, with the cause invisible. A measurement input
    that cannot be read is the state error this tool already defines for the
    ancestry probe, so it must exit 2 instead (#5086 review P3, cycle 5).
    """
    target = _tracked_files_under(_TRACKED_DIR)[0]
    real_read_text = Path.read_text

    def _deny(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)
    with pytest.raises(SystemExit) as excinfo:
        _label_hits()
    assert excinfo.value.code == 2, (
        f"an unreadable measurement input is a state error (2), never a quietly "
        f"narrower population; got {excinfo.value.code!r}"
    )


def test_a_tracked_non_text_file_under_tortoise_is_a_stated_state_error(
    monkeypatch,
) -> None:
    """Fails if a non-text tracked file is silently EXCLUDED from the population.

    Reachable: a `.pyc` or any binary checked in under `tortoise/`. It may carry
    the literal bytes `:Document`, so excluding it narrows the population the
    figure is stated in — the same false-CLEAN class as the `.py`-only walk,
    arriving by a different door. There are zero such files at this head (all
    287 tracked paths under `tortoise/` decode as UTF-8), so the arm cannot fire
    today; it exists so that adding one is a decision, not a silent subtraction.
    """
    target = _tracked_files_under(_TRACKED_DIR)[0]
    real_read_text = Path.read_text

    def _binary(self, *args, **kwargs):
        if self == target:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _binary)
    with pytest.raises(SystemExit) as excinfo:
        _label_hits()
    assert excinfo.value.code == 2, (
        f"a non-text tracked file must be a stated state error (2), not a silent "
        f"exclusion; got {excinfo.value.code!r}"
    )


def test_the_shipped_guard_refuses_with_the_STATE_ERROR_code_not_drift() -> None:
    """Fails if the #5128 refusal exits 1 — this tool's DRIFT code.

    Reachable: the Apple CLT `python3` (3.9.6) that #5128 was filed from. With
    `raise SystemExit(<message>)` CPython exits **1**, so `--check` on an old
    interpreter reports *the document and the tree disagree* about a document it
    never read — a wrong instruction, and the collision is invisible because the
    stderr text is still attributable. An old interpreter is the same class as a
    missing object: this checkout cannot answer the question, so it is 2.
    """
    source = (REPO / "tools" / "substrate_map_check.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and "version_info" in ast.unparse(node.test)
    ]
    assert len(guards) == 1, f"expected exactly one interpreter guard, found {len(guards)}"
    guard = guards[0]
    exits = [
        node
        for node in ast.walk(guard)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
    ]
    assert len(exits) == 1, f"the guard must raise exactly once, found {len(exits)}"
    code = exits[0].exc.args[0]
    assert isinstance(code, ast.Constant) and code.value == 2, (
        "the interpreter guard must exit 2 (state error) — exiting 1 collides "
        "with this tool's `drifted` code; got "
        f"{ast.unparse(exits[0].exc) if exits else 'nothing'}"
    )


def test_the_document_label_prose_count_is_the_LINE_count() -> None:
    """Fails if §5's `:Document` figure is a FILE count wearing a line's name.

    Reachable — and it WAS the shipped state: §5 read *":Document occurs on **17**
    lines under `tortoise/` at `4f68a2aa7`"*, but 17 is `git grep -l`'s FILE count;
    `git grep -n` (`git grep -c` too — "the number of matching lines", per the
    git-grep manual) counts **55** LINES at exactly that commit, which is also 55
    at this head. The number is load-bearing: it is the evidence §5 records for
    the decision NOT to regex-check the label, so it is the number a reader
    re-examining that decision quotes — and it was wrong by 3x.

    §5's second number is pinned with it: the narrow `\\(\\w*:Document` pattern
    still matching exactly **two** RST literals is what makes *"a guard would have
    needed a hand-maintained allowlist"* a measurement rather than an assertion.
    """
    doc = _doc()
    match = _LABEL_CLAIM.search(doc)
    assert match, (
        "§5's `:Document` measurement sentence is gone or was reworded — re-measure "
        "it rather than dropping it; the no-regex-check decision rests on it"
    )
    claimed_lines = int(match.group(1))
    claimed_files = int(match.group(2)) if match.group(2) else None
    lines, files, narrow = _label_hits()
    assert claimed_files is not None, (
        f"§5 states {claimed_lines} lines but no file count — the tree has {lines} "
        f"lines across {files} files; state BOTH, because confusing the two is the "
        "defect this pins"
    )
    assert (claimed_lines, claimed_files) == (lines, files), (
        f"§5 claims {claimed_lines} lines across {claimed_files} files; the tree has "
        f"{lines} lines across {files} files — re-measure the claim (§5)"
    )
    assert narrow == 2, (
        f"the narrow `\\(\\w*:Document` pattern now matches {narrow} lines, not the 2 "
        "RST literals §5 records — the exclusion argument needs re-measuring"
    )


def test_the_summary_line_agrees_with_the_named_holes_section() -> None:
    """Fails if §1's headline count contradicts §8's, as it did when shipped.

    Reachable — and it WAS the shipped state: §1 said the map "describes … four
    named holes", then **one clause later** "no live hole of the two it names".
    The count and the phrase naming the count disagreed inside a sentence pair,
    while §8 (*"The issue body names two holes"*), D5 (*"The hole count went
    2 → 1"*) and the source (#5086's body: WS-B and WS-D are the two workstreams
    no epic references) all say **two**. §1 is the section whose premise is
    *"Everything below that carries a state token was read, not remembered"*, so
    a self-contradicting number there is the one place it cannot be tolerated.

    The check is a CONSISTENCY one (the two sections must agree) rather than a
    pin on `two`: a re-cut at a new `measured_at_commit` may legitimately change
    the count, and when it does both sections must move together.
    """
    doc = _doc()
    summary = re.search(r"(\w+)\s+named holes", smc._section(doc, "## 1."))
    named = re.search(r"names (\w+) holes", smc._section(doc, "## 8."))
    assert summary and named, (summary, named)
    for word in (summary.group(1), named.group(1)):
        assert word in _COUNT_WORDS, f"unparsed hole count {word!r} — the wording moved"
    assert summary.group(1) == named.group(1), (
        f"§1 says {summary.group(1)!r} named holes; §8 says the body names "
        f"{named.group(1)!r} — the two must agree (§8/D5 are the measurement)"
    )


def test_cli_exit_codes() -> None:
    """0 clean · 1 drifted · 2 usage/state — and no arm collapses into another.

    Reachable: the clean arm on the shipped tree, the usage arm on an unknown
    flag, and (in a `fetch-depth: 1` checkout, which is what CI gives the `test`
    job, or on a partial clone whose probe trips the bound) the state arm with 2
    in place of 0. Asserting `0` unconditionally here is what redded this file in
    CI: the clone cannot answer the ancestry question. The drift arm is covered
    by the negative controls above.

    `_ancestry_is_verifiable_here()` is bounded with the tool's own bound, so the
    expectation and the tool cannot disagree about which arm this is; the
    unverified assertion accepts either unverified wording because all carry the
    same token (#5086 review P2, cycle 6; the shallow status-1 arm joined them in
    #10356).
    """
    tool = REPO / "tools" / "substrate_map_check.py"
    clean = subprocess.run(
        [sys.executable, str(tool), "--check"], capture_output=True, text=True
    )
    expected = 0 if _ancestry_is_verifiable_here() else 2
    assert clean.returncode == expected, clean.stdout + clean.stderr
    if expected == 2:
        assert "UNVERIFIED" in clean.stdout, clean.stdout
        assert "cannot be verified here" in clean.stdout, clean.stdout
    usage = subprocess.run(
        [sys.executable, str(tool), "--nonsense"], capture_output=True, text=True
    )
    assert usage.returncode == 2, usage.stdout + usage.stderr

    manifest = subprocess.run(
        [sys.executable, str(tool), "--json"], capture_output=True, text=True
    )
    assert manifest.returncode == expected, manifest.stdout + manifest.stderr
    payload = json.loads(manifest.stdout)
    assert payload["verdict"] == ("CLEAN" if expected == 0 else "UNVERIFIED"), payload
    assert payload["document"] == "docs/architecture/SUBSTRATE-REBUILD-MAP.md"
