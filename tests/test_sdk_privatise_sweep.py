"""#13899 — the privatisation sweep's verifier must be able to say NO.

`tools/sdk_privatise.py` took 66 off-surface `TortoiseSDK` methods off the public surface
and rewrote every reference it could prove. Two review findings were about the VERIFIER,
not the sweep, and both are the fail-open shape a sweep cannot afford:

  * **F3 — the vacuous check.** `--check` derived the rename map from
    `git merge-base HEAD origin/main`. On this branch that resolves to a commit whose
    class still holds the 66 public names, so the map was populated and the check looked
    meaningful. The moment this branch MERGES, the merge-base is a commit whose class
    holds the 39 approved names, the map is `{}`, and `--check` prints
    `typed references still pointing at a public name: 0` and exits 0 — over any tree at
    all, swept or not. A verifier whose answer cannot vary is not a verifier.
  * **F2 — the file-wide typing.** `sdk_bound_names` built a PER-FILE set of SDK-typed
    names. `tortoise/hosted_api.py` binds `reg` from `_registry_sdk()` (a real
    `TortoiseSDK`) in one function and from `sdk._get_registry()` (a FalkorDB `Graph`) in
    ~78 others, so the file-wide set typed every `reg.` in the file and 80 graph-handle
    calls were rewritten `reg.query(` → `reg._query(` — the control plane now depends on
    a third-party PRIVATE method. That is exactly the "ordinary word another object also
    answers to" sabotage the tool's own docstring warns about, and the tool performed it.

Every test below asks a question the tool answered WRONGLY at the review head, so each is
a mutation the fix has to keep red. Nothing here runs the tree-wide walk: the pins are
read from the map and from the typing, and the two synthetic modules carry the shape.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_tool():
    """Load `tools/sdk_privatise.py` as a module (`tools/` is a namespace package here)."""
    spec = importlib.util.spec_from_file_location(
        "sdk_privatise_under_test", ROOT / "tools" / "sdk_privatise.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


privatise = _load_tool()


def _module(tmp_path: Path, src: str) -> Path:
    path = tmp_path / "subject.py"
    path.write_text(src, encoding="utf-8")
    return path


def _base_is_readable(rev: str) -> bool:
    """Is `rev`'s `tortoise/sdk.py` readable in THIS clone?

    ⛔ The `test` job checks out at the default `fetch-depth: 1` (no `with:` block on
    `actions/checkout`, and no `unshallow` anywhere under `.github/`), so a historical
    `SWEEP_BASE` is ABSENT by construction there and `_sdk_source()` exits the session
    (`SystemExit`, not a skip) — which reds the very job that runs this file. The arm
    below therefore asks the question first and skips on ABSENCE, the same idiom as
    `tests/test_7949_capstone_measure.py` (`git show <sha>:…` → `pytest.skip`) and
    `tests/test_11739_quote_population_probe.py` (which names `fetch-depth: 1` for it).
    The real measurement is carried at depth 0 by the `surface-guard` job's
    `python3 tools/sdk_privatise.py --check` step, which resolves `SWEEP_BASE` through
    `git show` — so a shallow clone reports UNCHECKED here, never a pass.
    """
    try:
        shown = subprocess.run(
            ["git", "show", f"{rev}:tortoise/sdk.py"],
            cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:  # no `git` on this host at all
        return False
    return shown.returncode == 0


# ── F3: the pin, and the refusal that makes it a verifier ───────────────────────


def test_the_pinned_base_still_derives_the_whole_batch():
    """The map is derived from `SWEEP_BASE`, so it cannot drift with `origin/main`.

    A grep-able count, not a vibe: 66 off-surface methods is the batch, and it is what the
    PR body, `tortoise/sdk_retired.py` and the manifest all claim.
    """
    if not _base_is_readable(privatise.SWEEP_BASE):
        pytest.skip(
            f"{privatise.SWEEP_BASE[:12]} is not carried by this clone — a `fetch-depth: 1` "
            "checkout lacks it by construction, so the map is UNCHECKED here, not empty. "
            "The depth-0 `surface-guard` step runs `tools/sdk_privatise.py --check`, which "
            "resolves the same pinned base; re-arm locally with "
            f"`git fetch origin {privatise.SWEEP_BASE}`.")
    entries = privatise.rename_map(rev=privatise.SWEEP_BASE)
    assert [e.name for e in entries], "the pinned base derived an EMPTY map — `--check` is vacuous"
    assert len(entries) == privatise.SWEEP_NAMES
    assert privatise.SWEEP_NAMES == 66, (
        "SWEEP_NAMES is the non-vacuity floor; changing it is a deliberate re-pin, not drift")
    assert len({e.private for e in entries}) == len(entries), "two names share a private spelling"


def test_reading_the_approved_set_does_not_shadow_the_package_star_import():
    """⛔ THE IMPORT-SHAPE ARM: the declaration is read BY PATH, never as `tortoise.__all__`.

    A dotted import sets the `__all__` ATTRIBUTE on the `tortoise` package, and from then on
    `from tortoise import *` raises `TypeError: 'module' object is not iterable` for the rest of
    the process. MEASURED, not asserted from memory: with the dotted import restored, the three
    files in this family in one pytest process give
    `test_loading_the_declaration_by_path_does_not_shadow_star_import` RED whenever THIS file is
    collected first (`1 failed, 48 passed`) and green when it is not — an order-dependent red
    that lives in the process's module state, so the arm belongs beside the reader that caused
    it. It is deliberately a SEPARATE arm, not a tail on the pinned-base arm above: that one
    SKIPS on a shallow checkout, and an assertion parked there would skip with it.
    """
    import tortoise

    assert "__all__" not in vars(tortoise), "the package already carries a shadowing attribute"
    approved = privatise.approved_methods()
    assert approved, "the reader returned nothing, so this proves nothing"
    assert "__all__" not in vars(tortoise), (
        "reading the declaration left `__all__` on the `tortoise` package — a dotted import is "
        "back, and `from tortoise import *` now raises TypeError for the rest of the process")


def test_check_refuses_a_base_whose_map_is_empty(monkeypatch, capsys):
    """⛔ THE F3 REGRESSION ARM: an empty map is a RED (exit 2), never a passing zero.

    `HEAD` is the swept tree, so `off_surface_names` there is empty — the exact state
    `--check` used to read as "0 references left" after this branch merges. Before the fix
    this returned 0 (after a two-minute tree walk); the assertion is on the exit code, so
    it stays honest either way.
    """
    monkeypatch.setattr(
        sys, "argv", ["sdk_privatise.py", "--check", "--base", "HEAD"]
    )
    assert privatise.rename_map(rev="HEAD") == [], (
        "premise: the swept class holds no off-surface name, so a HEAD-derived map is empty")
    assert privatise.main() == 2, "an empty rename map must refuse, not report a vacuous 0"
    assert "VACUOUS CHECK" in capsys.readouterr().err


def test_the_residue_pin_is_a_reading_of_the_whole_set():
    """F6: the residue is pinned as (rows, digest), so a NEW untyped shape reds the check.

    The digest has to be a function of the SET, not of a count: two different shapes with
    the same number of rows are the case a count cannot see.
    """
    rows, digest = privatise.SWEEP_RESIDUE
    assert rows > 0, "an unpinned (0, …) residue means the drift arm is a no-op"
    assert re.fullmatch(r"[0-9a-f]{16}", digest), digest
    assert privatise.residue_digest(Counter({"x.query": 1})) != privatise.residue_digest(
        Counter({"x.update": 1})
    ), "the digest must distinguish shapes, or the pin only counts"
    assert privatise.residue_digest(Counter({"x.query": 2})) != privatise.residue_digest(
        Counter({"x.query": 1})
    ), "the digest must carry the counts, or a new site is invisible"


# ── F2: typing is per SCOPE; ambiguity is not rewritten ─────────────────────────


def test_a_typed_receiver_reference_is_still_detected(tmp_path):
    """Non-vacuity of the detection arm itself: the sweep must SEE a proved reference."""
    src = (
        "def make():\n"
        "    sdk = TortoiseSDK()\n"
        "    return sdk.create_point('statement', 'a claim')\n"
    )
    edits = privatise.edits_for(_module(tmp_path, src), {"create_point": "_create_point"})
    assert [repl for *_span, repl in edits.attrs] == ["._create_point"]
    assert not edits.residue


def test_the_typing_follows_the_scope_not_the_file(tmp_path):
    """⛔ THE F2 REGRESSION ARM: `reg` is an SDK in one function and a graph handle in the next.

    This is `tortoise/hosted_api.py` in miniature, and it is the shape that produced
    `reg._query(` at 80 sites. A per-FILE typing rewrites BOTH calls; the fix rewrites only
    the one whose own function binds `reg` from an SDK, and reports the other as residue.
    """
    src = (
        "def registry_handle():\n"
        "    reg = _registry_sdk()\n"
        "    return reg.query('k')\n"
        "\n"
        "def graph_handle():\n"
        "    reg = sdk._get_registry()\n"
        "    return reg.query('MATCH (n) RETURN n')\n"
    )
    edits = privatise.edits_for(_module(tmp_path, src), {"query": "_query"})
    rewritten = [line for line, *_rest in edits.attrs]
    assert rewritten == [3], (
        "only the function that binds `reg` from an SDK may be rewritten; a file-wide "
        f"typing rewrote {rewritten} and that is F2")
    assert edits.residue["reg.query"] == 1, (
        "the untyped graph-handle call must be RESIDUE (reported, never guessed at)")


def test_a_name_bound_both_ways_in_one_scope_is_not_rewritten(tmp_path):
    """Ambiguity inside ONE scope resolves to "do not touch it", not to the first match."""
    src = (
        "def handle(flag):\n"
        "    reg = _registry_sdk()\n"
        "    if flag:\n"
        "        reg = sdk._get_registry()\n"
        "    return reg.query('MATCH (n) RETURN n')\n"
    )
    edits = privatise.edits_for(_module(tmp_path, src), {"query": "_query"})
    assert edits.attrs == [], "a name bound from an SDK AND a graph handle is ambiguous"
    assert edits.residue["reg.query"] == 1


# ── the residue block's own FIGURES: a published address must reproduce ─────────
#
# The `#:` block above `SWEEP_RESIDUE` is the only place this tool publishes ADDRESSES
# (`83e94826a8a`, `d523c9be8c`, …) and FIGURES (a character count, which commit deleted a
# line, which trees still carry it). A review round found four of them that did not
# reproduce against the command printed beside them — the defect class the block polices
# in its own words, and one no other suite can see: the block is comments, so nothing
# executable depends on it. These arms ARE the falsifier, one per finding.

RESIDUE_ABBREV = "83e94826a8a"          # the literal the block prints and calls an abbreviation
RESIDUE_DELETING_COMMIT = "aee14c5ceb"  # measured: the interval commit that drops `seen.update`
RESIDUE_INTERVAL_END = "d5ecef0d72"     # measured: that commit's parent, the last carrier
RESIDUE_PR_HEAD = "d523c9be8c"          # `refs/pull/14108/head`
RESIDUE_REWRITE_PARENT = "2fe01fc775e"  # the capture rewrite's parent (`#14068`'s squash)

# The interval's TWO published counts, as the block prints them. They live here so the PROSE arm
# can grade the WORDS against the same numbers the PREMISES arm measures against the repository —
# before this, `SIX`→`SEVEN` and `EIGHT`→`NINE` both left the suite GREEN (review, round 12), which
# is the defect class the block polices in its own words: the numeral IS the substance of the
# finding-2 fix ("six of the eight still carry the line"), not a decoration around it.
RESIDUE_INTERVAL_COMMITS = 8            # `git rev-list --reverse 2fe01fc775e..d523c9be8c` → 8
RESIDUE_CARRIER_COUNT = 6               # interval trees still holding `seen.update(` → 6
# Word forms, so a change to either constant can never leave the prose silently mismatched.
RESIDUE_COUNT_WORDS = {6: "SIX", 7: "SEVEN", 8: "EIGHT", 9: "NINE"}

# The re-cut pair and the TREE that derives it (review, round 13, P2). The pair is `--repin`'s
# output on `1b82cc7063` — the capture rewrite — and NOT "the pair THIS head pins": this head
# pins `(11844, "70d6cfdcd8b769e4")`, so the relative form asserted a pair this head does not
# carry, the very class the block fixes one sentence above for `e022fc935b`.
RESIDUE_PAIR_REV = "1b82cc7063"         # the rewrite's own tree, which DERIVES the pair
RESIDUE_PAIR = '(11838, "de5664dc98549d56")'


def _residue_block() -> str:
    """The whole contiguous `#:` prose run above `SWEEP_RESIDUE` — NOT one paragraph.

    ⛔ ITS EXTENT IS THE ENTIRE RUN (1677 lines at this head, measured through THIS function's own
    walk — and GRADED, see `test_the_extent_figure_is_the_walks_own_count`: a prose edit that
    moves the run REDS naming both numbers instead of quietly publishing a stale figure, which is
    what the sentence this one replaces asked a reader to do by hand. `#14086` left one blank
    line inside the run and it silently shrank to 22 until the #14188 repair; PR #14258's own two
    prose commits grew it to 1066 while this sentence still said 985, which was true at
    `04afeb811d` and 1010 at `main` `70fbd1a27dd5` (review of #14258, P3), and this re-cut's
    restoration of the branch's dropped blocks took the merged run to 1079, a peer's prose to 1084,
    dropping the duplicated `#:` separator that restoration had left to 1083, the measured erratum
    rework below to 1087, and the merge with `origin/main` `10e51693` — which KEPT both sides'
    blocks and added the re-measure note — to 1122; THIS re-cut merges `origin/main` `0467ba32`
    (base `70fbd1a2`) and then a PEER's own copy of that same re-cut, KEEPING both sides' blocks at
    each merge for the same reason: the peer's copy read 1185 and then 1196 as its prose grew
    while this lane's read 1197, and the per-line difference between the two LIVES is exactly what
    a merged run is — so the figure above is RE-MEASURED here rather than inherited from either
    side, and the next reflow repeats that), the fold of `origin/main` `414761ae` (which brought
    its own `#14282` re-cut blocks AND re-cut `SWEEP_RESIDUE` on its own tree) kept both sides'
    blocks again and took the run to 1583, the `#:` block recording that fold in
    `tools/sdk_privatise.py` took it to 1594, and RESTORING the one `main` block the three-way
    merge had dropped WITHOUT a conflict marker (`THE BASE FIGURE IS THE BASE **TREE's**
    MEASUREMENT`) took it to 1607 — that block names `1b0db0489408`'s own pair, so its absence
    orphaned that `OWN_PINS` entry and the orphan arm redded; THIS re-cut merges `origin/main`
    `7244b4d1a9` (seven files, both sides' `#:` blocks kept and this merge's own record added to
    `tools/sdk_privatise.py`), which took the run to 1615; the figure is RE-MEASURED by this
    function's walk on the RESOLVED file, never inherited, and 1615 is the figure above,
    AND THE ARMS GRADE ALL OF IT
    (review, round 17, P3: this docstring said "the block immediately above `SWEEP_RESIDUE`",
    which implied a paragraph). The walk climbs from the pin while each line is `#:`-prefixed,
    and the residue section's prose is contiguous all the way up to its opening sentence — so
    every NEGATIVE arm below has that whole surface as its grading area, and an honest edit to
    a sentence elsewhere in it can red one. That is why the arms are windowed around the
    sentence they police (see `_ATTRIBUTION_WINDOW`); it is not a licence to shorten the walk,
    because the sections the arms read live hundreds of lines apart.

    `test_the_residue_block_is_a_real_block_and_publishes_its_addresses` anchors the extent, so
    a reflow that breaks the run with a blank line SCREAMS here instead of silently shrinking
    every arm's grading surface.
    """
    lines = (ROOT / "tools" / "sdk_privatise.py").read_text(encoding="utf-8").splitlines()
    # ⛔ EXACTLY ONE ASSIGNMENT, ASSERTED HERE (#14038 review, P1). The block above states the rule
    # in its own words — "THIS RECORD CARRIES NO ASSIGNMENT, deliberately … ONE assignment, at the
    # end of the records, cannot be shadowed by a second re-cut" — and a merge that folds two
    # sides each carrying a record also carries BOTH assignments. Python binds the LAST one, so
    # the file still imports the right pin, while this walk anchored on the FIRST and every arm
    # below graded only the prose above that first pin, silently losing the newest records. The
    # extent pin in `test_the_residue_block_is_a_real_block_and_publishes_its_addresses` cannot
    # see this: its anchors all sit ABOVE the first pin, so the truncation is invisible to it.
    # Anchoring on the single assignment makes a re-kept duplicate a RED here instead.
    pins = [i for i, line in enumerate(lines) if line.startswith("SWEEP_RESIDUE = (")]
    assert len(pins) == 1, (
        f"tools/sdk_privatise.py carries {len(pins)} `SWEEP_RESIDUE = (` assignments, at lines "
        f"{[n + 1 for n in pins]}; the block requires exactly ONE, at the end of the records. "
        "Delete the dead assignment(s) and keep their `#:` prose — otherwise this walk grades "
        "only the prose above the FIRST pin and every arm below is grading less than it claims")
    pin = pins[0]
    start = pin
    while start and lines[start - 1].lstrip().startswith("#:"):
        start -= 1
    return "\n".join(lines[start:pin])


def _rev_present(rev: str) -> bool:
    """Is `rev` carried by THIS clone? The `test` job is `fetch-depth: 1` (no objects)."""
    try:
        done = subprocess.run(
            ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
            cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:  # no `git` on this host at all
        return False
    return done.returncode == 0


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)} failed: {done.stderr.strip()}"
    return done.stdout


def _ref_sha(ref: str) -> str | None:
    """The sha `ref` resolves to in THIS clone, or None when it is not fetched here.

    `refs/pull/*` is not in a normal clone's refspec, so its absence is the ORDINARY
    case — the address is still graded through the constant the block prints, and only
    the drift arm is UNCHECKED. No network, and ⛔ NOT because of the egress guard:
    that guard patches only the two CONCRETE httpx transports (`tests/conftest.py`,
    "SCOPE: the httpx transports only"), so a `git` subprocess is not intercepted by it
    — an earlier draft of this docstring claimed it would be, which is a mechanism that
    does not exist (review, round 12, P3). The reason is the ordinary one: a test that
    needs the network is not a test here, and an unpinned remote read would make this
    arm's verdict a function of GitHub's uptime rather than of the tree.
    """
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", ref], cwd=ROOT, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


#: Where each addressed rev is FETCHABLE from — the refspec a skip's re-arm line prints.
#:
#: ⛔ A RE-ARM THAT CANNOT RUN IS NOT A RE-ARM (review, round 12, P2): the skip printed
#: `git fetch origin <the missing revs>`, and every rev this block addresses is an
#: ELEVEN-character abbreviation — which git cannot `want` (`fatal: couldn't find remote
#: ref 83e94826a8a`, and this block's own prose says so in its "11-character ABBREVIATION"
#: sentence). ⛔ NAMED BY ITS TEXT AND NOT BY A LINE NUMBER: an address rots on the next
#: insertion above it, and the block's own rule forbids one — it publishes a `grep` over
#: `tests/` returning EMPTY as the evidence that nothing cites it by line, so a line address
#: here would falsify that sentence in the very commit that quotes it.
#: So the printed remedy contradicted the sentence it exists to explain, and an operator
#: who followed it would believe the guard was re-armed while it stayed UNCHECKED — in a
#: PR whose thesis is falsifiability. A REFSPEC pulls the objects (`_rev_present` reads
#: the object DB, not a ref), and `refs/pull/14108/head` carries every interval commit
#: while `main` carries the rest.
#:
#: ⛔ AND SOURCE-ONLY IS STILL INERT FOR THE REF (review, round 13, P3). `git fetch origin
#: refs/pull/14108/head` records the objects in `FETCH_HEAD` and updates NO local ref —
#: git-fetch(1), "Refspec": a refspec with no destination "intentionally updates no local
#: ref". Verified against the manual (git-scm.com/docs/git-fetch, "./<repository> refspec"):
#: the documented way to get a local ref for a pull request is a DESTINATION refspec
#: (`refs/pull/1/head:refs/remotes/origin/pr/1`). So the source-only re-arm armed
#: `_rev_present` — which reads the OBJECT DB — while `_ref_sha("refs/pull/14108/head")"
#: ` stayed None, and the ADDRESS/drift assertion in
#: `test_the_history_clause_premises_hold_in_this_clone` — the only arm that re-measures the
#: moving pointer — stayed silently UNCHECKED *while the skip message told the operator the
#: block's addresses were now checked*. That is the round-12 inert-re-arm finding one level
#: down, so the spec below is the form that creates exactly the ref `_ref_sha` reads:
#: `test_the_rearm_refspec_creates_the_ref_it_promises` is the offline proof.
_PR_HEAD_REFSPEC = "+refs/pull/14108/head:refs/pull/14108/head"
_ARM_REFSPECS: dict[str, str] = {
    RESIDUE_ABBREV: _PR_HEAD_REFSPEC,
    RESIDUE_REWRITE_PARENT: _PR_HEAD_REFSPEC,
    RESIDUE_DELETING_COMMIT: _PR_HEAD_REFSPEC,
    RESIDUE_INTERVAL_END: _PR_HEAD_REFSPEC,
    RESIDUE_PR_HEAD: _PR_HEAD_REFSPEC,
    "origin/main": "+main:refs/remotes/origin/main",
    # ⛔ THE RECUT BLOCK'S OWN ADDRESSES (#14282 review, P3). Three revs the block publishes an
    # OWN PIN for, so `_requires(...)` can name them — and an unmapped rev RAISES instead of
    # printing an inert command, which is the round-12/13 defect this map exists to prevent.
    # GitHub serves a commit reachable from ANY ADVERTISED REF, by FULL SHA — `main`'s ancestry, or
    # `refs/pull/<n>/head` (`efe5605e976a` is served via `refs/pull/14270/head`: the rebase left it
    # no longer an ancestor of `main`, so an ancestry-only rule does NOT explain that entry) — and a
    # DESTINATION is required (round 13), so each maps to `+<sha>:refs/pi/<abbrev>`.
    # ⛔ NOT `refs/remotes/origin/<abbrev>` (#14282 review, P3): `git fetch --prune origin` DELETES a
    # remote-tracking ref the remote does not advertise — measured in a scratch clone, the destination
    # was removed with ` - [deleted] (none) -> origin/<abbrev>` — so a routine prune silently disarmed
    # the re-arm this map prints. `refs/pi/*` is what every sibling sha want here uses, and `--prune`
    # does not touch it.
    # Measured on this repo: each fetch created its destination ref at the named sha.
    "5e83c2a2f6": "+5e83c2a2f697d5879dd47d1204a2be88d6aff616:refs/pi/5e83c2a2f6",
    "10e51693f1": "+10e51693f1668e98fe62f3ee1293668a1f2f21a8:refs/pi/10e51693f1",
    "efe5605e976a": "+efe5605e976af24ea45cc66d2015861d30d3a39c:refs/pi/efe5605e976a",
    #
    # ⛔ AND #14258's TWO, FROM THE OTHER SIDE OF THIS MERGE. They are kept alongside the
    # three above rather than replacing them: all five are an `OWN_PINS` key on the MERGED
    # tree, and `test_every_addressed_rev_names_a_fetchable_refspec` requires an address for
    # every one — so this is the union of two independently-correct maps, not a choice. The
    # shape guards below grade every entry in the map identically, by deriving the population
    # from the map rather than restating its size — a size typed in prose here goes stale on the
    # next entry added, and nothing grades it (#14282 review cycle 2, P3).
    # ⛔ PR #14258's `OWN_PINS` GREW BY TWO AND THE MAP DID NOT FOLLOW, so
    # `test_the_own_pin_map_matches_each_revs_own_tree`'s `_requires(*sorted(OWN_PINS))` raised
    # `KeyError` instead of SKIPPING in the sharded `test` job — which checks out at
    # `fetch-depth: 1` (`python-ci.yml`'s bare `actions/checkout@v4` has no `with:`), i.e. the
    # exact shape this two-arm design exists to survive (see the round-16 note above: an arm that
    # cannot run there is not a guard). An ERROR, not a skip, so it reds the job.
    # The two entries are NOT the same kind of address, which is why one carries a flag:
    #   * `0351fe856942` IS `refs/pull/14160/head`'s tip, so a depth-1 fetch of that ref arms it —
    #     the same convention `RESIDUE_PR_HEAD` already uses for `refs/pull/14108/head`.
    #   * `10e51693f166` is #14160's SQUASH and lives 8 commits back in `main`'s history, so NO
    #     depth-1 refspec can arm it. The obvious remedy — `--deepen=N +refs/heads/main:…` — WORKS
    #     ONLY WHILE `main` HAS MOVED FEWER THAN N COMMITS since this was written: `--deepen` reaches
    #     `N+1` from the tip, `main` moves ~62 commits/day here (`git rev-list --count --since='7 days
    #     ago' origin/main` → 435), so a deepen would make this message inert again in ~3 days with
    #     NOTHING reddening (the guard grades syntax and destination, never reachability). So it
    #     names the commit by FULL SHA: git can `want` a 40-character sha, GitHub answers it
    #     (measured from a `fetch-depth: 1` clone — `git fetch origin
    #     "+10e51693f1668e98fe62f3ee1293668a1f2f21a8:refs/pi/10e51693f166"` → `* [new ref]`, exit 0,
    #     `cat-file -e` then succeeds), and a sha-want does not expire and fetches one commit's trees
    #     rather than 200 commits of `main`.
    # ⛔ AND BECAUSE IT PINS BY SHA, ITS *SOURCE* IS GRADED AS A FULL SHA (review of PR #14258, P2).
    # A refspec SOURCE that is hex is a new shape here — every other entry sources a REF
    # (`refs/pull/14108/head`, `main`). An abbreviated source would print the exact inert remedy this
    # map exists to forbid (`git fetch origin "+10e51693f166:refs/pi/x"` → `fatal: couldn't find
    # remote ref`, rc=128) while the suite stayed GREEN, because the arm's
    # `fullmatch(r"[0-9a-f]{7,39}")` runs on the WHOLE entry and a refspec contains `+` and `:`, so it
    # can never match hex-only at ANY length. Hex length therefore does no work there; the arm now
    # checks it where it does — on the SOURCE.
    "0351fe856942": "+refs/pull/14160/head:refs/pull/14160/head",
    "10e51693f166": "+10e51693f1668e98fe62f3ee1293668a1f2f21a8:refs/pi/10e51693f166",
    # ⛔ FULL-SHA WANTS, because neither rev has an ADVERTISED ref: `27cd81d55ebd` sits in no ref at
    # all and `8889f1140105`'s only ref is a local branch (`refs/pull/14086/head` contains neither),
    # so `+<40-hex>:refs/pi/<rev>` is the only address that resolves — naming a branch would be the
    # lie this map exists to forbid. ⛔ DURABILITY, not fetchability, is the caveat: an unadvertised
    # sha is subject to the server's GC, so a re-arm can stop resolving; the pairs stay graded.
    "8889f1140105": "+8889f1140105677add2c7c4dd60d0c8c5080ea26:refs/pi/8889f1140105",
    "27cd81d55ebd": "+27cd81d55ebd42948c27051d821df970bbc0ba2f:refs/pi/27cd81d55ebd",
    # `1b0db0489408` is an ANCESTOR of `origin/main` (measured: `git merge-base --is-ancestor
    # 1b0db0489408 origin/main` exits 0), so an advertised ref DOES carry it — but the full-sha
    # want is kept for the same reason as its neighbours above: it does not depend on how deep the
    # clone checked out, and the FORM is what this map grades.
    "1b0db0489408": "+1b0db0489408e1e817688f83cef0d7c1ee33be5d:refs/pi/1b0db0489408",
    "2c0d66e2ec": "+2c0d66e2ec07d673b17e31b76dc24fbdd57f8e14:refs/pi/2c0d66e2ec",
    "e74cf1d13d": "+e74cf1d13da76ba7176c73ebacd96cef843378f5:refs/pi/e74cf1d13d",
    # ⛔ `2fe01fc775e` is NOT listed here: it is `RESIDUE_REWRITE_PARENT`, already mapped at the top of
    # this map to `_PR_HEAD_REFSPEC`. A second literal entry bound the SAME key, so Python kept the last
    # one and the constant's row became dead data — and the printed remedy silently changed from a
    # refspec that creates `refs/pull/14108/head` to a sha want that creates only `refs/pi/2fe01fc775e`
    # (#14282 review, P2). One key, one row.
    "e0accb8fe6": "+e0accb8fe6362942a0c847c7daa1829e272296a9:refs/pi/e0accb8fe6",
    "72c104bcbb": "+72c104bcbb1cbcef901476a535ed35262d167e02:refs/pi/72c104bcbb",
    # `8889f1140105` is a MERGE on a deleted feature branch, so no depth-1 REF arms it — but a sha
    # want does, measured from a clone holding neither object: `git fetch --dry-run --depth=1
    # https://github.com/daniel-ospina/tortoise.git 8889f1140105677add2c7c4dd60d0c8c5080ea26` →
    # `* branch … -> FETCH_HEAD`, while the same command with an invented sha prints
    # `fatal: remote error: upload-pack: not our ref` (the negative control that makes the first
    # line evidence rather than a coincidence). This map grades the FORM of the remedy; the
    # want-ability behind it is what that pair of commands measures.
}


def _rearm_command(missing) -> str:
    """The `git fetch` a skip prints: REFSPECS only — an abbreviation is not a `want`.

    ⛔ `_ARM_REFSPECS[rev]` RAISES on an unmapped rev rather than falling back to the rev
    itself, because the fallback is exactly the inert command this replaces: a new
    constant added without a fetchable address would otherwise re-introduce the defect
    silently. `test_every_addressed_rev_names_a_fetchable_refspec` is the non-vacuity
    arm for the map.
    """
    return f"git fetch origin {' '.join(sorted({_ARM_REFSPECS[rev] for rev in missing}))}"


def _requires(*revs: str) -> None:
    missing = [rev for rev in revs if not _rev_present(rev)]
    if missing:
        pytest.skip(
            f"{', '.join(missing)} absent from this clone — a `fetch-depth: 1` checkout "
            "lacks them by construction, so the block's addresses are UNCHECKED here, not "
            f"wrong. Re-arm with `{_rearm_command(missing)}`.")


def _quoted(block: str, start: int) -> bool:
    """Is the text at `start` introduced as a QUOTED earlier draft that was called wrong?

    The block's shape for that is `<NOT|never|falsified|wrong> "<the draft's words>"`.
    """
    before = block[max(0, start - 100):start]
    # The draft's words may open with an article (`NOT "the re-indented …"`), so a short run of
    # word chars is allowed between the open quote and the occurrence.
    return bool(re.search(r"(?:NOT|never|falsif\w*|wrong)[\s#:]*\"[\s#:\w]{0,12}$", before,
                          re.IGNORECASE))


def _refuted(text: str, start: int) -> bool:
    """Does the block REFUTE the claim at `start` — quoted as a falsified draft, or negated?

    The block's own shape for a corrected sentence is to QUOTE the earlier draft and call it
    wrong (`not "the pair THIS head pins"`), or to negate it in place (`a pair this head does
    NOT carry`, `…, and it was wrong`). Both are honest prose; neither is the claim. Everything
    else is charged.
    """
    if _quoted(text, start):
        return True
    return bool(re.search(r"(?i)\b(?:not|never|no longer|does not|did not|wrong|false|falsified)\b",
                          text[start:start + 70]))


def test_the_residue_block_is_a_real_block_and_publishes_its_addresses():
    """Non-vacuity for the arms below: the block exists and carries its addresses.

    It also grades the addresses' OWNERSHIP: a revision the block names must be attributed to
    the PR that produced it, never to a neighbouring one — the defect a round-12 review found
    in ``(`2fe01fc775e`, the `#13337` merge, `#13994`)``, where the same block calls that tree
    `#14068` seven other times, so one address was given two answers.
    """
    block = _residue_block()
    assert block.count("\n") > 100, "the residue block is missing or truncated"
    # ⛔ THE EXTENT IS PINNED, NOT ASSUMED (review, round 17, P3). `_residue_block()` walks up
    # while lines are `#:`-prefixed, so a single blank line inserted mid-run TRUNCATES it — and
    # every negative arm below would then pass over a surface that no longer contains the
    # paragraph it exists to police. One anchor per depth of the run, each a phrase no arm
    # rewrites: truncation above any of them reds here, where the cause is named.
    for anchor in ("REFUSED to guess at",               # the run's opening sentence
                   "RE-PINNED (11776,",                  # the re-pin history, ~10 lines in
                   "OLDEST of the EIGHT commits"):       # the interval, ~300 lines in
        assert anchor in block, (
            f"`{anchor}` is a section of the contiguous `#:` run above `SWEEP_RESIDUE`; its "
            "absence means a non-`#:` line broke the run and `_residue_block()` now returns a "
            "SHORTER surface — every negative arm below is grading less than it claims")
    for literal in (RESIDUE_ABBREV, RESIDUE_PR_HEAD, RESIDUE_REWRITE_PARENT):
        assert literal in block, f"{literal} is one of the block's published addresses"
    # ⛔ NON-VACUITY IN BOTH DIRECTIONS: the tree is still attributed to its own squash, so
    # deleting the sentence is not the fix, AND no neighbouring PR may sit beside it.
    assert _attributions(block), (
        f"non-vacuity: `{RESIDUE_REWRITE_PARENT}` must still be attributed to `#14068`, the "
        "squash that landed it — dropping the attribution is not the fix")
    bad = _misattributions(block)
    assert not bad, (
        f"`{RESIDUE_REWRITE_PARENT}` is attributed to {', '.join(sorted(set(bad)))}; its squash "
        f"subject reads `(#13337) (#14068)`, so only `#14068`/`#13337` own it and `#13994` is a "
        "later, unrelated commit — an address that resolves to a different tree is exactly the "
        "defect this block exists to remove")
    # The arm's own falsifier, on the PRE-FIX and fixed texts (this is what makes it
    # non-vacuous without a git object, so it also runs in the sharded `test` job).
    assert _misattributions("(`2fe01fc775e`, the `#13337` merge, `#13994`)"), (
        "the PRE-FIX parenthetical must be refused — this assertion is why the fix is "
        "falsifiable rather than a comment")
    assert not _misattributions("(`2fe01fc775e`, the `#13337` merge, `#14068`)"), (
        "the corrected parenthetical must pass, or the arm reds an honest block")
    assert not _misattributions("the red `test (a)` reported on `#13994`'s shard `a`"), (
        "`#13994` is a legitimate statement elsewhere in the block; forbidding the NUMBER "
        "instead of the ATTRIBUTION would red it")


def test_the_extent_figure_is_the_walks_own_count():
    """The line count `_residue_block()` publishes must be what its OWN walk returns.

    ⛔ THE FIGURE WAS ADVICE, NOT A GATE (#14282 review cycle 3, P3), AND IT WAS CARRIED WRONG
    REPEATEDLY BECAUSE OF IT. An instruction to re-derive is not a check, and a figure read off a
    sentence is exactly the class this PR exists to repair — so the sentence is graded here, and
    a prose edit that changes the extent REDS, naming both numbers, instead of quietly publishing
    a stale one.

    ⛔ NO COUNT OF THE READINGS IS RESTATED HERE, OR ANYWHERE (#14282 review, P3). An earlier
    cut of this docstring carried its OWN count of how often the extent figure had been re-derived,
    and it disagreed with the count `_residue_block`'s docstring carried — two numbers for one
    population, both authored by this PR, so a reader auditing the count had to re-derive it
    instead of trusting either. Those numbers survive only as the history of that disagreement,
    which is why this header says a count is not restated rather than that none is mentioned:
    neither docstring publishes a total now. The readings are listed in one place
    (`_residue_block`'s docstring, beside the figures they name) and their count is deliberately
    left unasserted — publishing it here would be the same stale-restatement defect this gate
    exists to close, one level up. That is why this arm asserts the FIGURE and not the history.

    Scoped to THIS sentence's figure: the docstring legitimately quotes other counts (`shrank to
    22`, `round 17`), and a looser match would grade those against the walk and red an honest edit.
    """
    m = re.search(r"ITS EXTENT IS THE ENTIRE RUN \((\d+) lines", _residue_block.__doc__ or "")
    assert m, (
        "the docstring must still publish the extent figure — deleting the sentence is not the "
        "fix for a wrong one")
    published = int(m.group(1))
    actual = len(_residue_block().splitlines())
    assert published == actual, (
        f"the block publishes its extent as {published} lines; its own walk returns {actual} — a "
        "prose edit changed the run, so re-derive the figure instead of carrying it forward")


#: The PRs that legitimately OWN `RESIDUE_REWRITE_PARENT`: its squash subject reads
#: `fix(12943): read a tenant's cache graph, never derive it from the credential (#13337) (#14068)`,
#: so `#13337` is the PR the commit came from and `#14068` the squash that landed it.
RESIDUE_REWRITE_PARENT_PRS = frozenset({"#14068", "#13337"})

#: How close a PR number must sit to a named sha to BE an attribution of it. Beyond this the
#: block is talking about a different tree (it legitimately discusses `#14108`, `#14101`,
#: `#14078` and `#13994` itself within a paragraph), so distance is the boundary between an
#: attribution and a neighbouring statement — the same windowed shape the arms above use.
_ATTRIBUTION_WINDOW = 45


def _attributions(block: str) -> list[str]:
    """PR numbers stated within `_ATTRIBUTION_WINDOW` characters of `RESIDUE_REWRITE_PARENT`."""
    flow = re.sub(r"\n#:\s?", " ", block)
    found: list[str] = []
    for occ in re.finditer(re.escape(RESIDUE_REWRITE_PARENT), flow):
        for pr in re.finditer(r"#[0-9]{4,6}", flow):
            if abs(pr.start() - occ.end()) <= _ATTRIBUTION_WINDOW:
                found.append(pr.group(0))
    return found


def _misattributions(block: str) -> list[str]:
    """Attributions of `RESIDUE_REWRITE_PARENT` to a PR that did not produce it.

    ⛔ AN ADDRESS THAT RESOLVES TO A DIFFERENT TREE (review, round 12, P2): the parenthetical
    read ``(`2fe01fc775e`, the `#13337` merge, `#13994`)``. Measured,
    `git log -1 --format='%s' 2fe01fc775e` is
    `fix(12943): … (#13337) (#14068)`, and `#13994` is `e0accb8fe6` — ~4h later, and not an
    ancestor of that merge tree at all. So the block gave one address two answers and the
    parenthetical gave the wrong one.

    ⛔ SHAPE, NOT PHRASE, and not a ban on the NUMBER: `#13994` is used correctly elsewhere in
    the block (`the red `test (a)` reported on #13994's shard (a)`), which the arm must accept.
    """
    return [pr for pr in _attributions(block) if pr not in RESIDUE_REWRITE_PARENT_PRS]


def _abbreviation_length_claims(block: str) -> list[tuple[str, int]]:
    """`(printed figure, offset)` for every figure stated as THIS abbreviation's length.

    ⛔ KEYED ON THE OBJECT, NOT ON ONE PHRASE (review, round 12, P3). The arm used to
    require the literal words `<N>-character ABBREVIATION`, so an honest rewording —
    `11-char abbreviation`, or the literal placed AFTER the figure — turned it RED while
    the claim said exactly what the repository measures. That is the over-specified
    assertion this file complains about elsewhere, and the standard shape of the fix is
    to assert the CONTRACT (a figure, and the object it describes) instead of the prose
    that happens to express it.

    The contract, as graded: a length figure whose ±80-character window carries
    `RESIDUE_ABBREV` — either side of it, so word order is free — and which is NOT a
    claim about a different object of the same shape. The block also says a FULL
    `40-character sha` IS served; that figure is about a sha, not about the
    abbreviation, so it is excluded by the noun that follows it rather than by distance
    (distance would either admit it or make an honest `11-character sha-prefix` red).
    """
    claims: list[tuple[str, int]] = []
    for figure in re.finditer(r"(\d+)[-\s]?char(?:acter)?s?\b", block, re.IGNORECASE):
        window = block[max(0, figure.start() - 80):figure.end() + 80]
        if RESIDUE_ABBREV not in window:
            continue
        if re.match(r"\s*sha\b", block[figure.end():figure.end() + 24], re.IGNORECASE):
            continue  # a length claim about a SHA, not about the abbreviation literal
        claims.append((figure.group(1), figure.start()))
    return claims


def test_the_abbreviation_length_claim_reproduces():
    """Finding 1: `<N>-character` must equal the length of the literal it describes.

    The block printed `13-character` while `83e94826a8a` — the literal in the same
    sentence — is ELEVEN characters: a figure that does not reproduce against the object
    it describes, which is the defect the block names in its own words.
    """
    block = _residue_block()
    # ⛔ GRADED ON EVERY OCCURRENCE, NOT THE FIRST (review, round 9): `re.search` graded one match,
    # so a second, FALSE figure appended anywhere in the block left this arm green — the same
    # one-occurrence blindness the negative arms below already avoid by using `finditer`.
    claims = _abbreviation_length_claims(block)
    assert claims, "non-vacuity: the block states the abbreviation's length"
    for printed, offset in claims:
        assert int(printed) == len(RESIDUE_ABBREV), (
            f"the block says {printed} characters at offset {offset}; `{RESIDUE_ABBREV}` "
            f"is {len(RESIDUE_ABBREV)}")


def test_the_abbreviation_arm_does_not_red_an_honest_rewording():
    """Review round 12 (P3): the figure's arm must grade the CLAIM, not its wording.

    Each string below states the SAME true figure in a different honest form; every one of
    them must be accepted, and the two dishonest ones must still be refused. Without this
    arm, a phrase-keyed check can be re-narrowed by a later edit with nothing to notice.
    """
    accepted = [
        "`git fetch origin 83e94826a8a` fails on the 11-character ABBREVIATION, NOT on the sha.",
        "`git fetch origin 83e94826a8a` fails on the 11-char abbreviation.",
        "the abbreviation `83e94826a8a` is 11 characters long, measured.",
        "it is 11 characters: `83e94826a8a` is not a `want`.",
    ]
    refused = [
        "`git fetch origin 83e94826a8a` fails on the 13-character ABBREVIATION.",
        "the abbreviation `83e94826a8a` is 7 characters long.",
    ]
    for text in accepted:
        claims = _abbreviation_length_claims(text)
        assert claims, f"non-vacuity: this form states the figure — {text!r}"
        assert all(int(printed) == len(RESIDUE_ABBREV) for printed, _ in claims), text
    for text in refused:
        claims = _abbreviation_length_claims(text)
        assert claims, f"non-vacuity: the figure is found even when it is wrong — {text!r}"
        assert any(int(printed) != len(RESIDUE_ABBREV) for printed, _ in claims), text


def test_whole_tree_movement_is_only_ever_stated_as_a_conditional():
    """Round 11, P2: the clause attributed a whole-tree movement that is measurably ZERO.

    The block said the scoped pair proved the prose population did not move "so the whole-tree
    movement IS entirely this block's own words". Measured at both revs the clause names:

        git grep -hoE '\\bclient\\.delete\\b' <rev> -- '*.py'                        -> 48 / 48
        git grep -hoE '\\bclient\\.delete\\b' <rev> -- '*.py' ':!…privatise.py'       -> 45 / 45

    so there is no whole-tree movement to attribute — the derived claim outlived the figure it
    was derived from, which is this block's own defect class. The scoped pair measures the prose
    EXCLUSION, never the whole-tree total, so whatever that total does is not what the sentence
    may assert; the honest form is the CONDITIONAL, and the conditional is what is graded here.

    ⛔ WHAT THIS ARM CANNOT DO: it matches the COPULA, not the claim, so a rewording that asserts
    a movement without `is`/`was`/`remains` beside the word `movement` slips past it. That limit
    is deliberate — every phrase-keyed alternative tried in this file turned an honest rewording
    RED — and it is why the arm also requires the conditional to be PRESENT: deleting the clause
    is not a fix, and a green-by-absence arm would be the vacuous shape this file rejects.
    """
    block = _residue_block()
    # `_residue_block()` keeps the `#:` prefixes, so the clause wraps across lines here; the
    # copula test needs the prose flowing, or `whole-tree\nmovement` never matches as one phrase.
    flow = re.sub(r"\n#:\s?", " ", block)
    for claim in re.finditer(r"(?is)whole-tree\s+(?:grep\s+)?movement\s+(is|was|remains)\b", flow):
        assert _quoted(flow, claim.start()), (
            "the whole-tree movement may only be stated as a CONDITIONAL (`any whole-tree "
            "movement WOULD be …`): the scoped pair measures the exclusion, not the total, and "
            "asserting a movement here is what round 11 falsified — "
            f"{flow[claim.start():claim.start() + 90]!r}")
    assert re.search(r"(?is)whole-tree\s+movement\s+would\b", flow), (
        "the clause must state the conditional (any whole-tree movement WOULD be this block's "
        "own words); deleting the sentence is not the fix")


def test_the_seen_update_deletion_premises_hold_in_this_clone():
    """The REPO-STATE half of finding 2 — the premises the prose arm below is graded against.

    ⛔ THIS IS A SEPARATE TEST FROM THE PROSE ARM ON PURPOSE (review, round 10, P2). It used
    to be the other half of `test_the_seen_update_deletion_is_attributed_to_the_commit_that_
    made_it`, behind one `_requires(...)` at the top — so on a `fetch-depth: 1` checkout the
    SKIP took the prose assertions with it, and the block could be edited back to crediting
    `d523c9be8c` with the deletion while the sharded `test` job stayed GREEN. A guard that a
    gating job cannot run is not a guard; the prose arm now runs in CI and this one verifies
    the facts it is graded against wherever the objects are present.

    ⛔ IT ASSERTS THE INTERVAL THE BLOCK DESCRIBES, so it reds if `refs/pull/14108/head` gains
    a commit or a history is rewritten — deliberately: that is the premise changing, and the
    block's own figures then have to be re-derived. It is not a prose guard and cannot
    falsify prose; the prose arm below it does that, and runs everywhere.
    """
    _requires(RESIDUE_REWRITE_PARENT, RESIDUE_DELETING_COMMIT, RESIDUE_INTERVAL_END,
              RESIDUE_PR_HEAD)
    revs = _git("rev-list", "--reverse", f"{RESIDUE_REWRITE_PARENT}..{RESIDUE_PR_HEAD}").split()
    assert len(revs) == RESIDUE_INTERVAL_COMMITS, (
        f"premise: `83e94826a8a` is the OLDEST of the "
        f"{RESIDUE_COUNT_WORDS[RESIDUE_INTERVAL_COMMITS]} interval commits")
    assert revs[0].startswith(RESIDUE_ABBREV), "premise: the interval opens on that commit"

    def carries(rev: str) -> bool:
        shown = subprocess.run(
            ["git", "show", f"{rev}:tests/test_13337_cache_graph_declaration.py"],
            cwd=ROOT, capture_output=True, text=True)
        assert shown.returncode == 0, f"{rev} lost the file the claim is about"
        return "seen.update(" in shown.stdout

    carriers = [rev for rev in revs if carries(rev)]
    droppers = [rev for rev in revs if not carries(rev)]
    assert [rev[:10] for rev in droppers] == [RESIDUE_DELETING_COMMIT, RESIDUE_PR_HEAD], (
        "premise: the deletion lands at aee14c5ceb, and d523c9be8c is one commit after it")
    assert len(carriers) == RESIDUE_CARRIER_COUNT, (
        f"premise: {RESIDUE_COUNT_WORDS[RESIDUE_CARRIER_COUNT]} of the eight trees carry the "
        "line, not one")
    assert carriers[-1].startswith(RESIDUE_INTERVAL_END), (
        "premise: the last carrier is the deleting commit's parent")


def test_the_seen_update_deletion_is_attributed_to_the_commit_that_made_it():
    """Finding 2: WHICH commit deleted the line, and over which span the claim holds.

    The block credited the interval's HEAD (`d523c9be8c`) with the deletion, and said the
    re-indent was true "ONLY on `83e94826a8a`". Measured: the deleting commit is
    `aee14c5ceb`, and SIX of the eight interval commits still carry the line — so the
    claim is interval-scoped, not tree-unique.

    ⛔ EVERY ASSERTION HERE IS GRADED ON THE BLOCK'S OWN TEXT, so it needs no git object and
    runs in the sharded `test` job's `fetch-depth: 1` checkout. The facts it is graded against
    are the module-level `RESIDUE_*` constants, and
    `test_the_seen_update_deletion_premises_hold_in_this_clone` verifies those against the
    repository wherever the objects exist.
    """
    block = _residue_block()

    # ⛔ THE GAP MAY NOT INVERT, RELATIVISE OR DELEGATE THE ATTRIBUTION (review, rounds 6-8):
    # a 60-char gap alone accepted `is one commit before the one that deletes …`, `does not
    # delete …` and `is one commit before the commit that deletes …` — every one credits the
    # wrong tree while naming ours, so the verb must be OURS and immediate.
    assert re.search(
        rf"`{RESIDUE_DELETING_COMMIT}`"
        rf"(?![^.]{{0,60}}(?i:\b(?:not|never|no longer|before|after)\b))"
        rf"(?![^.]{{0,60}}(?i:\bthe (?:one|commit|tree|rev)\b))"
        rf"[^.]{{0,60}}(?i:\bdelet(e|es|ed)\b) the `seen\.update` line",
        block), (
        "the DELETION must be BOUND to the commit that made it — naming `aee14c5ceb` somewhere "
        "in the block is not the attribution the finding asked for")
    # ⛔ AND THE INTERVAL SPAN MUST STILL BE PUBLISHED (review, round 8): the too-narrow ban is
    # a pure negative, so deleting the sentence that states the span left it green — the
    # "deleting the sentence must not pass" defect this file rejects elsewhere.
    assert re.search(rf"`{RESIDUE_ABBREV}`[^.]{{0,80}}`{RESIDUE_INTERVAL_END}`", block), (
        "the claim is interval-scoped, so the span must be named: `83e94826a8a` to "
        "`d5ecef0d72`")
    # ⛔ AND THE TWO COUNT WORDS ARE PART OF THAT CLAIM (review, round 12 — P2). The span
    # assertion above checks only that the two SHAs are named, and the premises arm checks 8 and 6
    # against the repository — but NOTHING compared the words the sentence actually PUBLISHES, so
    # `the SIX commits`→`SEVEN` and `OLDEST of the EIGHT`→`NINE` both left this file GREEN while
    # the sentence it exists to protect went false. Graded on the block's TEXT, so it needs no git
    # object and runs in the sharded `test` job's `fetch-depth: 1` checkout.
    assert re.search(
        rf"OLDEST of the {RESIDUE_COUNT_WORDS[RESIDUE_INTERVAL_COMMITS]} commits", block), (
        f"the block must publish the interval size as "
        f"{RESIDUE_COUNT_WORDS[RESIDUE_INTERVAL_COMMITS]!r}, matching the "
        f"{RESIDUE_INTERVAL_COMMITS} commits the premises arm measures — a count word that does "
        f"not match its measurement is the defect this block exists to remove")
    assert re.search(
        rf"holds for the {RESIDUE_COUNT_WORDS[RESIDUE_CARRIER_COUNT]} commits from", block), (
        f"the block must publish the carrier count as "
        f"{RESIDUE_COUNT_WORDS[RESIDUE_CARRIER_COUNT]!r}, matching the "
        f"{RESIDUE_CARRIER_COUNT} carriers the premises arm measures — `SIX of the eight still "
        f"carry the line` IS the finding-2 fix, so the numeral is load-bearing")
    # ⛔ CASE-INSENSITIVE, THE NOUN "deletion" MUST NOT COUNT, AND A SENTENCE SPLIT OR A LONGER
    # RUN MUST NOT HIDE IT (review, rounds 6-8): the block honestly says the interval head
    # `inherits that deletion` — only a VERB credits it — and both `… of the eight. It deletes
    # the seen.update line.` and a 76-char credit sentence slid past a short fixed gap. So the
    # negative runs on a period-flattened copy with a wider reach, EXCEPT when another revision
    # is named in between (then the verb is that revision's, which is the honest attribution).
    flat = re.sub(r"\.\s", " ", block)
    for head_ref in re.finditer(rf"`{RESIDUE_PR_HEAD}`", flat):
        tail = flat[head_ref.end():head_ref.end() + 200]
        verb = re.search(r"(?i:\bdelet(e|es|ed)\b)", tail)
        if verb is None:
            continue
        assert re.search(r"`[0-9a-f]{7,40}`", tail[:verb.start()]), (
            "`d523c9be8c` is the interval HEAD and only inherits the deletion; a delete verb "
            "near it with no other revision named in between credits it with doing it")
    # ⛔ AND NO **OTHER** REVISION MAY CARRY THE DELETING VERB (review, round 11 — P2): the loop
    # above sweeps ONE revision, the interval head, so appending
    #   `⛔ Also, `4b7e9eb06f` DELETES the `seen.update` line in the same rewrite.`
    # left this arm GREEN — and `4b7e9eb06f` is one of the SIX CARRIERS (it still holds the line),
    # so the appended sentence is false, and the same hole admitted a credit to `d5ecef0d72`.
    # The invariant is rev-agnostic and needs no git object: the revision bound to "deletes the
    # `seen.update` line" is the DELETING commit, and only that one.
    for claim in re.finditer(r"`([0-9a-f]{7,40})`", flat):
        rev = claim.group(1)
        if RESIDUE_DELETING_COMMIT.startswith(rev) or rev.startswith(RESIDUE_DELETING_COMMIT):
            continue
        tail = flat[claim.end():claim.end() + 200]
        verb = re.search(r"(?i:\bdelet(e|es|ed)\b)[^`]{0,40}the `seen\.update` line", tail)
        if verb is None:
            continue
        # A revision named BETWEEN this one and the verb owns the verb — that is the honest
        # attribution, and it is the same escape the loop above applies.
        if re.search(r"`[0-9a-f]{7,40}`", tail[:verb.start()]):
            continue
        raise AssertionError(
            f"`{rev}` is bound to deleting the `seen.update` line, and that verb belongs to "
            f"`{RESIDUE_DELETING_COMMIT}` alone: `{rev}` is not the deleting commit, so crediting "
            "it with the deletion is false (the six other interval trees still HOLD the line)")
    # ⛔ THE SHAPE, NOT THE PHRASE (review, rounds 7-8): `ONLY on …` has paraphrases — `solely
    # on`, `ONLY for … is it true`, and a bare ` … alone` — and a phrase lookup let each
    # reinstate the too-narrow claim. The escape is the block's own honest form, which says the
    # narrow reading is TOO NARROW right after naming it.
    for too_narrow in re.finditer(
            rf"(?i:only|solely|just|exclusively)\s+(?:on|of|for|at|across)?\s*`{RESIDUE_ABBREV}`"
            rf"|`{RESIDUE_ABBREV}`\s+alone", block):
        assert _quoted(block, too_narrow.start()) or re.search(
            r"too narrow", block[too_narrow.end():too_narrow.end() + 80]), (
            "the re-indent holds across the six commits up to the deleting commit's parent, "
            "not for one sha")


def test_the_history_clause_premises_hold_in_this_clone():
    """The REPO-STATE half of finding 3 — the premises the prose arm below is graded against.

    ⛔ SEPARATE FROM THE PROSE ARM ON PURPOSE (review, round 10, P2). It used to sit inside
    `test_the_history_clause_is_only_quoted_as_falsified`, behind one `_requires(...)`, so a
    `fetch-depth: 1` checkout SKIPPED the prose assertions too — and "outside every history a
    reader browses" could be reinstated with the sharded `test` job green.

    It grades `83e94826a8a`'s ancestry of the tree the block names as
    `refs/pull/14108/head` (`d523c9be8c`), and a PR head ref survives a merge, so "outside
    every history a reader browses" was false as an assertion. What is measured is narrower:
    no remote BRANCH contains it, and `main` does not.

    ⛔ TWO DIFFERENT MEASUREMENTS, NAMED SEPARATELY (review cycle 1): the ancestry below is
    against the CONSTANT `d523c9be8c`, so its message must not claim the REF was measured —
    and where this clone does carry `refs/pull/14108/head`, its target is graded against the
    constant, because the block's own sentence says a PR head ref "tracks the branch's
    CURRENT head": the moment #14108 gains a commit the address moves and every interval
    claim written against it goes stale without a red.

    ⛔ IT READS THE CLONE'S REMOTE-TRACKING REFS, so a clone whose `refs/remotes/origin/*` are
    stale (a deleted branch never pruned) reds the `branch -r --contains` arm until
    `git fetch origin --prune`. That is the premise being stale, not the block being wrong —
    which is why it lives here and not in the prose arm, which runs everywhere.
    """
    _requires(RESIDUE_ABBREV, RESIDUE_PR_HEAD, "origin/main")
    head_ref = _ref_sha("refs/pull/14108/head")
    if head_ref is not None:  # fetched here: grade the ADDRESS, not only the ancestry
        assert head_ref.startswith(RESIDUE_PR_HEAD), (
            f"`refs/pull/14108/head` resolves to {head_ref[:10]}, not `{RESIDUE_PR_HEAD}` — the "
            "block's ADDRESS has MOVED, so its '`2fe01fc775e..d523c9be8c`' interval and every "
            "claim written against that head must be re-measured before this arm may pass")
    inside = subprocess.run(
        ["git", "merge-base", "--is-ancestor", RESIDUE_ABBREV, RESIDUE_PR_HEAD],
        cwd=ROOT, capture_output=True, text=True)
    assert inside.returncode == 0, (
        f"premise: the commit IS an ancestor of `{RESIDUE_PR_HEAD}`, the tree the block names "
        "as `refs/pull/14108/head` (this clone does not carry that ref, so only the constant "
        "is graded here)")
    on_main = subprocess.run(
        ["git", "merge-base", "--is-ancestor", RESIDUE_ABBREV, "origin/main"],
        cwd=ROOT, capture_output=True, text=True)
    assert on_main.returncode == 1, (
        "premise: the commit is NOT on `origin/main` (rc 1 = not an ancestor; anything else "
        "means the measurement did not run)")
    assert _git("branch", "-r", "--contains", RESIDUE_ABBREV).strip() == "", (
        "premise: no remote BRANCH contains it — which is the only part of the block's "
        "narrow claim that is about branches",)


def test_the_history_clause_is_only_quoted_as_falsified():
    """Finding 3: the commit IS inside a history a reader browses.

    `83e94826a8a` is an ancestor of `refs/pull/14108/head`, and a PR head ref survives a
    merge, so "outside every history a reader browses" was false as an assertion. What is
    measured is narrower: no remote BRANCH contains it, and `main` does not. The arm accepts
    either honest form — the narrow claim stated outright, or the false one quoted as false —
    and refuses the unqualified version, which is where the finding landed.

    ⛔ THE CLAUSE IS MATCHED BY SHAPE, NOT BY ONE SENTENCE (review, rounds 5-8): the block has
    already been reworded twice under this arm ("a reader browses" → "the repo PUBLISHES"), and
    a phrase-keyed lookup turned each honest rewording into a red while letting a dishonest
    one through. What must not vary is *a claim about every history* with no measured
    qualifier in it.

    ⛔ EVERY ASSERTION HERE IS GRADED ON THE BLOCK'S OWN TEXT, so it runs in the sharded `test`
    job's `fetch-depth: 1` checkout. `test_the_history_clause_premises_hold_in_this_clone`
    verifies the facts it is graded against wherever the objects exist.
    """
    block = _residue_block()

    # ⛔ EVERY OCCURRENCE IS GRADED (review, round 9): a single `re.search` let the exact false
    # clause be appended a second time while the first, honest one was the only thing graded.
    clauses = list(re.finditer(r"outside every[^.]{0,80}history", block))
    assert clauses, "non-vacuity: the block still says where such a commit sits"
    for clampse in clauses:
        # ⛔ TWO HONEST SHAPES, AND NO THIRD (review, rounds 5-7). Either the clause itself carries a
        # MEASURED qualifier (`BRANCH`), or it is a QUOTED earlier draft and the narrow fact that
        # replaced it sits beside it. Keyed on the CLAUSE AS WRITTEN, not on one exact phrase: an
        # earlier version looked up the exact phrase, so swapping the qualifier for an unmeasured one
        # (`outside every TAG history …`) made the phrase absent, which it read as "quoted", and the
        # nearby narrow sentence carried it — an unmeasured claim went green.
        if not re.search(r"BRANCH|branch", clampse.group(0)):
            assert _quoted(block, clampse.start()), (
                "unquoted clause with no measured qualifier: the commit sits outside every BRANCH "
                "history and not on `main` — `refs/pull/14108/head` IS a history a reader browses")
            near = block[max(0, clampse.start() - 400):clampse.end() + 400]
            assert re.search(r"no (?:remote )?branch", near, re.IGNORECASE), (
                "a quoted earlier draft is acceptable only BESIDE the narrow fact that replaced it")


def _already_counted_anchors(block: str) -> list[int]:
    """Offsets of every place the block says the row is ALREADY counted elsewhere.

    ⛔ SHAPE, NOT PHRASE (review, round 12, P3): the lookup was the literal `already count`,
    so `counted already`, `counts it already` or `is already accounted for by the bullet`
    red the pointer arm below while the pointer was intact — the same false-block shape the
    abbreviation arm was fixed for in the same round. Either word order is accepted; the
    arm still grades where the pointer LANDS (row and file in its forward window), which is
    the part that carries the meaning.
    """
    return [m.start() for m in re.finditer(
        r"already[^.\n]{0,40}count|count\w*[^.\n]{0,40}already", block, re.IGNORECASE)]


def test_the_reindent_reference_points_at_the_bullet_that_counts_it():
    """Finding 4: "the re-indented `seen.update` above" has no referent above it.

    The only re-indents described above that sentence are `graph.query`'s. The row it means
    is counted in the `tests/test_13337_cache_graph_declaration.py` +2 bullet, so the
    sentence must POINT AT that bullet: the "already counted" claim, the bullet's
    `seen.update` 1, and the bullet's file have to meet in one sentence. A test that only
    forbade the dangling phrase would pass if the sentence were deleted, leaving the row
    uncounted by name.
    """
    block = _residue_block()
    # ⛔ `(?!\d)`: the block's own totals carry `seen.update` 101/102, and a bare substring
    # test let `seen.update` 101 satisfy this arm — and satisfied its non-vacuity check on the
    # PRE-FIX head, which is the shape of a gate that has never been able to go red.
    row = re.compile(r"`seen\.update` 1(?!\d)")
    assert row.search(block), "non-vacuity: the +2 bullet counts the row"
    # ⛔ A WINDOW FORWARD FROM THE CLAIM, not a proximity window around the phrase: the
    # `already counted` paraphrase further down the block sits next to the test file's name (as
    # a path in a `git show <rev>:tests/…` command) but never beside `seen.update` 1, so it
    # cannot satisfy this arm, and deleting the pointer sentence cannot pass it.
    anchors = _already_counted_anchors(block)
    assert anchors, "non-vacuity: the block states the row is counted elsewhere"
    pointer = [
        anchor for anchor in anchors
        if row.search(block[anchor:anchor + 320])
        and "tests/test_13337_cache_graph_declaration.py" in block[anchor:anchor + 320]
    ]
    assert pointer, (
        "the sentence must POINT AT the bullet that counts the `seen.update` row, naming "
        "`tests/test_13337_cache_graph_declaration.py` and its `seen.update` 1 — pointing at a "
        "re-indent 'above' resolves to `graph.query`'s, and pointing nowhere resolves to nothing")
    # ⛔ THE SHAPE, NOT THE PHRASE (review, round 7): `the re-indented `seen.update` above` has
    # paraphrases (`… row above`, `… line above`, `a re-indent of `seen.update` above`), and an
    # exact-phrase ban let them all through. The pointer sentence below survives both, so the
    # shape is what has to be banned — unless it is quoted as the earlier draft it was.
    for dangling in re.finditer(
            r"re-?indent\w*[^.\"]{0,40}`seen\.update`[^.\"]{0,20}\babove", block, re.IGNORECASE):
        assert _quoted(block, dangling.start()), (
            "no `seen.update` re-indent is described above that sentence — point at the bullet "
            "that counts the row instead of pointing 'above'")


def test_the_pointer_arm_does_not_red_an_honest_rewording():
    """Review round 12 (P3): the pointer may be phrased either way round, and must be found."""
    row = "`seen.update` 1 in the `+2` against `tests/test_13337_cache_graph_declaration.py`"
    for text in (
        f"the row this block already counts above, {row}",
        f"this row is counted already: {row}",
        f"the bullet counts it already — {row}",
    ):
        anchors = _already_counted_anchors(text)
        assert anchors, f"non-vacuity: this form states the pointer — {text!r}"
        assert any("`seen.update` 1" in text[a:a + 320]
                   and "tests/test_13337_cache_graph_declaration.py" in text[a:a + 320]
                   for a in anchors), text


#: A past-tense / measurement qualifier: what turns "the ref IS `d523c9be8c`" into
#: "the ref WAS `d523c9be8c` when this was measured".
_PAST_OR_MEASURED = re.compile(
    r"(?i)\b(?:was|were|resolved|measured|at\s+the\s+time|when\s+measured)\b")

#: A PRESENT-TENSE binding of a sha to something: `it is `sha``, `the ref pins `sha`` …
#: ⛔ `resolves to` / `resolved to` are NOT in this set: they are the dated form, and the
#: arm below requires a qualifier to be ABSENT before it charges one.
_MOVING_REF_TARGET = re.compile(
    r"(?i)\b(?:is|pins?|names?|equals?|points?\s+at|resolves?\s+to)\s+`[0-9a-f]{7,40}`")


def _moving_pointer_violations(block: str) -> list[str]:
    """Sentences that bind a sha to the PR head ref's CURRENT target in the PRESENT tense.

    ⛔ SAME RELATIVITY CLASS THE BLOCK FIXES FOR `main` (review, round 12, P3): the block
    scopes `e022fc935b` to "`origin/main` when this was written" and re-derives every figure
    it states about `main`, but its own `#14108` sentence said the ref "tracks the branch's
    CURRENT head, `d523c9be8c`" — a present-tense address for a pointer that moves on the
    next push to #14108, and the only arm that re-measures it runs solely where
    `refs/pull/14108/head` was fetched by hand (never in CI). A sha bound to a moving ref in
    the present tense rots into a false statement with nothing red.

    ⛔ TWO SHAPES, BECAUSE THE FIRST ONE WAS A PHRASE AND NOT A CONTRACT (review, round 13,
    P3). Keyed only on the literal `CURRENT head`, the arm could fire **nowhere** in the real
    block any more — that wording is gone — so a PARAPHRASE that drops the dating without
    restoring the word ("… the ref tracks the branch's head, and it is `d523c9be8c`") stayed
    GREEN, which made the "second direction" unreachable on the block it exists to grade.
    The contract graded now is the BINDING, not the vocabulary: (1) an appositive beside
    `CURRENT head`, and (2) a present-tense copula binding a sha inside a ref-tracking clause
    with no past/measured qualifier. Word order and surrounding words stay free, so an honest
    rewrite ("it resolved to `…` when these figures were measured") is accepted.
    """
    flow = re.sub(r"\n#:\s?", " ", block)
    violations: list[str] = []
    for claim in re.finditer(r"(?i)current\s+head|refs/pull/14108/head", flow):
        window = flow[claim.end():claim.end() + 120]
        if not re.search(r"`[0-9a-f]{7,40}`", window):
            continue  # no sha bound here at all — nothing to relativise
        if _PAST_OR_MEASURED.search(window):
            continue
        violations.append(flow[claim.start():claim.end() + 40])
    # ⛔ THE PARAPHRASE SHAPE: the DATING is the thing that must not vary, so a present-tense
    # copula inside a ref-tracking clause is charged unless a past/measured qualifier precedes
    # the sha. `resolved to` is the dated form and is excluded by `_PAST_OR_MEASURED` rather
    # than by a phrase list, so an honest rewording cannot be red.
    for clause in re.finditer(r"(?i)(?:tracks?|resolves?\s+to)\b[^.]{0,80}\bhead\b", flow):
        window = flow[clause.start():clause.start() + 240]
        for bound in _MOVING_REF_TARGET.finditer(window):
            before = window[max(0, bound.start() - 50):bound.start()]
            # The qualifier may also FOLLOW the sha (`… it is `d523c9be8c` as measured`), so the
            # forward window is graded too — bounded at the sentence end, or a `measured` two
            # sentences later would launder an undated binding.
            after = re.split(r"\.", window[bound.end():bound.end() + 60], maxsplit=1)[0]
            if _PAST_OR_MEASURED.search(before) or _PAST_OR_MEASURED.search(after):
                continue
            violations.append(window[max(0, bound.start() - 40):bound.end()])
    return violations


def test_the_pr_head_refs_target_is_a_measurement_never_a_present_tense_address():
    """Review round 12 (P3): `refs/pull/14108/head` moves; a sha beside it must be dated.

    ⛔ NON-VACUITY IN BOTH DIRECTIONS. Deleting the sentence is not the fix (the ref's relation
    to a branch head is WHY the block names a sha at all), so the first assert requires that
    relation to survive — ⛔ **EITHER TENSE** (review, round 17, P2): `#14108` has since MERGED
    and its head branch is deleted, so `refs/pull/14108/head` is now FROZEN at `d523c9be8c` and
    tracks nothing. Requiring the PRESENT tense forced the stale wording to stay and made the
    truthful correction a RED, which is the stale-on-arrival class this block polices. The
    second assert still refuses only the unqualified present-tense binding.
    """
    block = _residue_block()
    flow = re.sub(r"\n#:\s?", " ", block)
    assert re.search(r"(?i)\btrack(?:s|ed)\b", flow) and re.search(r"(?i)\bhead", flow), (
        "non-vacuity: the block still relates the PR head ref to a branch head, in either tense "
        "— that is why it names a sha rather than the issue number")
    violations = _moving_pointer_violations(block)
    assert not violations, (
        "a sha bound to a MOVING ref must be dated, not asserted in the present tense: "
        f"{violations[0]!r} — say it `resolved to` the sha `when these figures were measured`, "
        "the way the block already scopes `origin/main`")


def test_the_moving_pointer_arm_accepts_dated_forms():
    """The other half: dated forms pass, the present-tense binding does not.

    Written because a negative-only arm can be satisfied by deleting the sentence, and
    because a phrase-keyed ban would red an honest rewrite — the two false-block shapes
    this file already rejects elsewhere.
    """
    dated = [
        "`#14108` resolves to `refs/pull/14108/head`, which tracks the branch's CURRENT head; "
        "it resolved to `d523c9be8c` when these figures were measured.",
        "`#14108` tracks the branch's CURRENT head — `d523c9be8c` as measured — so name the sha.",
        "`#14108` tracks the branch's head, and the sha it pointed at was `d523c9be8c`.",
        # ⛔ THE PARAPHRASE DIRECTION (review, round 13, P3): the date is carried by a
        # present-tense sentence with an explicit `measured` qualifier, not by the word
        # `CURRENT`. Before round 13 this shape was unreachable — the arm keyed on a literal
        # that no longer occurs on the block.
        "`#14108` tracks the branch's head, and it is `d523c9be8c` as measured.",
        "the ref tracks the branch's head; it is `d523c9be8c`, measured on the day this was written.",
    ]
    for text in dated:
        assert not _moving_pointer_violations(text), text
    undated = [
        # the verbatim pre-fix sentence
        "`#14108` resolves to a ref that tracks the branch's CURRENT head, "
        "`d523c9be8c`, and `83e94826a8a` is the OLDEST of the eight.",
        # ⛔ and the PARAPHRASE that drops the dating without restoring the word `CURRENT`, which
        # was GREEN before round 13: same claim, no `CURRENT head` for a phrase-keyed arm to find.
        "`#14108` resolves to a ref that tracks the branch's head, and it is `d523c9be8c`.",
    ]
    for text in undated:
        assert _moving_pointer_violations(text), (
            f"a sha bound to a MOVING ref in the present tense must be refused — this arm is what "
            f"makes the fix falsifiable: {text!r}")


#: ⛔ THE ONE ISSUE NUMBER IN THIS BLOCK THAT CANNOT ADDRESS A TREE (review, round 13, P2).
#: `#14108` resolves to `refs/pull/14108/head`, which TRACKS a branch head — so a tree cited by
#: that number is a moving address, and the block's own sentence is what says so. Other numbers
#: in the block (`#14068 tree`, `#14067`) name MERGED pull requests and are stable, which is why
#: this is keyed on the OBJECT and not on the shape `#<n> tree`: a shape-keyed arm red the honest
#: `on the #14068 tree`, which is the false-block this file rejects everywhere else.
_PR_REF_AS_TREE = re.compile(
    r"(?:#14108|refs/pull/14108/head)`?[\s,]*"
    r"(?:(?:PRE-MERGE|PRE-REWRITE|PRE-FIX|pre-merge|pre-rewrite|pre-fix)\s+)?(?:tree|tip)\b")

#: A relative pin address: "the pair THIS head pins" / "this commit's `origin/main`" — a claim
#: that is true only for the tree it was written on, in a block that has already been re-cut under
#: it twice (PR body §4, review round 13, P2).
#:
#: ⛔ KEYED ON THE SHAPE, NOT ON ONE PHRASE (review, round 17, P3). Clause 1 was the whole of the
#: round-13 arm and it matched the LITERAL `THIS head`, so the same defect spelled `THIS COMMIT
#: pins` / `this tree pins` / `the head pins` stayed GREEN — narrower than the "SHAPE, NOT PHRASE"
#: contract this arm's own docstring claims. An address a rebase falsifies is a POINTER defect, so
#: what is charged is a relative head **as the subject of a pin verb** (clause 2): the verb is what
#: makes it a BINDING rather than a measurement, and that distinction is load-bearing — the block
#: legitimately says the pair was `--repin`-printed or measured `on THIS tree`, which is honest and
#: must not red. Which is also why clause 1 keeps `head` and does NOT widen to `tree`: `THIS tree`
#: is an honest measurement qualifier, and widening it there false-blocked
#: "the pin is the pair `--repin` printed on THIS tree".
#: ⛔ A POSSESSIVE RELATIVE TREE AS THE SUBJECT OF A PRINT VERB (PR #14209 review). The `#14164`
#: paragraph said "the SAME pair this branch's tree prints". Clause 2 cannot see it — the noun
#: sits behind `branch's`, and the verb is `prints` rather than `pins` — so that sentence graded
#: nothing until a reader caught it, and `(11843, "6aed5368ffa10513")` read as current on a tree
#: that prints `(11857, …)`. The pair-bound arm added with the same fix reads the pair out of the
#: SHAPE, which needs a quoted pair in the sentence; this clause closes the shape itself.
#: ⛔ PRESENT TENSE ON PURPOSE: `this branch's tree printed at `72c104bcbb`` is a MEASUREMENT with
#: its rev named and must not red, while the unqualified present tense is the binding that the
#: next re-pin falsifies — the same measurement-vs-binding line clause 1 already draws.
#: ⛔ THE REVERSED ORDER — CLAUSE 1's OWN BLIND SPOT (#14282 fix lane, P3-class, measured).
#: Clause 1 puts the `pair`/`pin` noun BEFORE the relative head, so the sentence `At THIS head the
#: committed pair is `11861`` graded NOTHING and shipped GREEN on the merged head — where the
#: committed pair is `11864` and `--check` exits 0 against it, so the sentence asserted a pair the
#: head it ships on does not carry, the exact defect clause 1 exists to charge. The passive
#: "committed pair"/"committed pin" is a BINDING (it states what the head commits), never the
#: honest measurement qualifier `--check` exits 0 `on THIS tree` / `on the SAME pair` — which is
#: why this clause keys on that noun instead of on every `pair` within reach of a relative head:
#: that widening would charge `main`'s stale-marked `on the SAME pair` sentence, whose refutation
#: sits in the paragraph BELOW it and so outside `_refuted`'s forward window.
_RELATIVE_PIN_ADDRESS = re.compile(
    r"(?i)\b(?:pair|pin)\b[^.\"]{0,40}\b(?:THIS|this)\s+head\b"
    r"|\b(?:THIS|this|the)\s+(?:current\s+)?(?:head|commit|tree|revision|rev)\s+pins?\b"
    r"|\b(?:THIS|this)\s+(?:branch|commit|PR|pr)'?s\s+(?:tree|tip)\s+(?:pins?|prints?)\b"
    r"|\bthis\s+commit'?s\s+`?origin/main`?"
    r"|\b(?:THIS|this|the)\s+(?:current\s+)?(?:head|commit|revision|rev)\b[^.\"]{0,40}"
    r"\bcommitted\s+(?:pair|pin)\b")

#: The rewrite's PARENT claim: `83e94826a8a` is the pre-rebase DRAFT of the commit that landed as
#: `1b82cc7063`; the rewrite's PARENT — the tree every row above is measured against — is
#: `2fe01fc775e`. Presenting the draft as the parent is the misattribution round 8 removed
#: (PR body §1, review round 13, P2).
_PARENT_CLAIM = re.compile(
    r"(?i)\b(?:parent|pre-rewrite\s+tree|pre-rebase\s+tree)\b[^.]{0,40}`83e94826a8a`"
    r"|`83e94826a8a`[^.]{0,40}\b(?:is|was)\s+the\s+parent\b"
    r"|\bthis\s+commit'?s\s+PRE-REWRITE\s+tree")


#: A PAIR bound to the tree the READER is standing on: "the pair below is `(11840, …)`" / "the
#: SAME pair this branch's tree prints". Both are the #14174 class in this block's own words — "an
#: address that resolves to a different tree" — and both shipped GREEN on every arm until PR
#: #14209's review F2: the pair below IS `SWEEP_RESIDUE`, and those two sentences claimed 11840 and
#: 11843 after the merges with `main` had re-measured it to 11857.
#:
#: ⛔ KEYED ON THE PAIR, NOT ON THE PHRASE — the discipline this file already applies to the
#: abbreviation and pointer arms. The block legitimately writes "the pair below was RE-DERIVED …
#: on THIS merge tree", and legitimately quotes ANOTHER tree's pair beside it (`main` at
#: `fbcd24760b` "for ITS tree"), so banning the words would false-block honest measurements. What
#: is charged is a QUOTED PAIR bound to a relative subject in the PRESENT tense — the form that
#: rots the moment the tree moves — and only where the pair is NOT the one the file carries.
_PAIR_BELOW_CLAIM = re.compile(
    r'(?i)(?:the\s+)?pair\s+below\s+is\b[^.]{0,90}?\(\d{4,5},\s*["\'][0-9a-f]{16}["\']\)'
    r'|\(\d{4,5},\s*["\'][0-9a-f]{16}["\']\)[^.]{0,90}?\bthis\s+branch\'?s\s+tree\s+prints?\b'
    r'|\bthis\s+branch\'?s\s+tree\s+prints?\b[^.]{0,90}?\(\d{4,5},\s*["\'][0-9a-f]{16}["\']\)')
_QUOTED_PAIR_RE = re.compile(r'\((\d{4,5}),\s*["\']([0-9a-f]{16})["\']\)')


def _pr_ref_as_tree_violations(flow: str) -> list[str]:
    """A TREE addressed by the moving PR ref's issue number (review, round 13, P2)."""
    return [flow[m.start():m.end()] for m in _PR_REF_AS_TREE.finditer(flow)
            if not _refuted(flow, m.start())]


def _relative_pin_addresses(flow: str) -> list[str]:
    """A pin pair or an `origin/main` bound to a RELATIVE head (review, round 13, P2)."""
    return [flow[m.start():m.end()] for m in _RELATIVE_PIN_ADDRESS.finditer(flow)
            if not _refuted(flow, m.start())]


def _parent_claims(flow: str) -> list[str]:
    """The pre-rebase DRAFT presented as the capture rewrite's PARENT (review, round 13, P2)."""
    return [flow[m.start():m.end()] for m in _PARENT_CLAIM.finditer(flow)
            if not _refuted(flow, m.start())]


def _pairs_bound_to_a_relative_tree(flow: str) -> list[tuple[str, tuple[int, str]]]:
    """`(sentence, pair)` for each pair bound to "the pair below" / "this branch's tree".

    `_PAIR_BELOW_CLAIM` finds the SHAPE; this reads the pair out of it so the caller can compare
    it against the pin the file actually carries. A match with no parseable pair is still a
    violation — it is the shape, not the digits, that rots.
    """
    out: list[tuple[str, tuple[int, str]]] = []
    for m in _PAIR_BELOW_CLAIM.finditer(flow):
        if _refuted(flow, m.start()):
            continue
        found = _QUOTED_PAIR_RE.search(m.group(0))
        out.append((m.group(0), (int(found.group(1)), found.group(2)) if found else (0, "")))
    return out


#: Direction 1 of the `#14068` arm: a DIFF verb and the tree it diffs against, both inside one
#: window, with the tree named by SHA.
#:
#: ⛔ NOT PHRASE-KEYED (review, round 17, P3). The arm required the literal
#: "`git diff` … between `A` and `B`", so an honest rewording of the SAME claim —
#: "diffing `A` against `B`" — was a RED. That is the over-specified-shape defect this file
#: removes from `_abbreviation_length_claims` and `_already_counted_anchors`, and the wrong
#: BINDING it exists to catch is already refused by direction 2 (`_PR_REF_AS_TREE`). What is
#: graded here is the presence of both shas beside the diff command, not the vocabulary.
_DIFF_VERB = re.compile(r"(?i)\bdiff\w*")


def _diff_names_tree_by_sha(flow: str) -> bool:
    """True when a diff command and BOTH shas share one window — word order and verbs free."""
    for occ in re.finditer(re.escape(RESIDUE_REWRITE_PARENT), flow):
        window = flow[max(0, occ.start() - 160):occ.end() + 160]
        if RESIDUE_ABBREV in window and _DIFF_VERB.search(window):
            return True
    return False


#: The `#14058` note's ORIGINAL, refuted reason for citing a pre-rebase commit by issue number.
_PREFIX_REASON_RE = re.compile(
    r"(?i)reachable from no ref|disappears on the next\s*`?gc`?|unreproducible address")


def _install_pre_fix(flow: str, anchor: str, replacement: str, sentence: str) -> str:
    """Install a PRE-FIX wording into the block WITHOUT asserting on the honest prose.

    ⛔ WHY THIS EXISTS (review, round 17, P3). The round-13 arms proved non-vacuity with
    `assert <anchor> in flow, "anchor for the mutation is gone"`, so an HONEST rewording
    elsewhere in the block red the arm while the claim it guards was untouched — measured:
    replacing `and `83e94826a8a` over` with the equally-true `and against `83e94826a8a`` failed
    `test_the_14068_attribution_names_its_tree_by_sha_never_by_the_pr_ref`. That is the
    false-block shape this file rejects for the abbreviation and pointer arms, and an arm that
    reds on honest prose trains the next lane to edit the GUARD instead of the claim.

    So the anchor is BEST-EFFORT: the restoration is made IN PLACE where the wording still
    matches, and where it does not the pre-fix SENTENCE is INSTALLED beside it. Either way the
    mutant carries the defect, so the detector is still seen to fire.

    ⛔ DELETION is the other route out of a negative-only arm, and it is not opened here: every
    caller runs its DIRECTION-1 asserts BEFORE the mutation, so the honest facts (the sha in the
    diff clause, the pair against its rev, the parent) must be present for the arm to reach this
    point at all.
    """
    return flow.replace(anchor, replacement) if anchor in flow else f"{flow} {sentence}"


def test_the_14058_notes_reason_is_the_measured_one_never_the_refuted_one():
    """§3 of this PR: the `#14058` note's stated REASON, graded — it was ungraded before.

    ⛔ THIS IS THE ARM THE PR BODY CLAIMED EXISTED AND DID NOT (review, round 17, P2, measured):
    restoring the pre-fix reason — "a pre-rebase commit is reachable from no ref and disappears
    on the next `gc`" — left every other arm green, because the three arms the body named grade
    three OTHER sentences (the "outside every branch history" clause, the whole-tree conditional,
    the PR-head dating), and the premise arms read no block text at all. Deleting the corrected
    narrow fact was likewise invisible. Both are graded here, object-free, so this runs in the
    sharded `test` job.

    Two honest shapes, as elsewhere in this file: the refuted reason may be QUOTED as refuted
    (that is how the block records it), and the measured narrow fact must be present.
    """
    block = _residue_block()
    flat = re.sub(r"\n#:\s?", " ", block)
    # ⛔ NON-VACUITY — the CORRECTED fact must survive: deleting it is not a fix for a wrong reason.
    assert re.search(r"(?i)no\s+remote\s+BRANCH|no\s+REMOTE\s+branch", flat), (
        "non-vacuity: the note still publishes the MEASURED narrow fact (`no remote BRANCH`) — "
        "deleting it leaves the reason unstated, which is not the same as it being right")
    assert re.search(r"(?i)not on\s+`main`", flat), (
        "non-vacuity: and the other half of that measurement (`not on `main``)")
    # ⛔ AND THE REFUTED SHAPE MAY NOT COME BACK UNREFUTED.
    for hit in _PREFIX_REASON_RE.finditer(flat):
        before = flat[max(0, hit.start() - 220):hit.start()]
        assert re.search(r"(?i)false|refut\w*|wrong|NOT |never ", before), (
            "the `#14058` note's ORIGINAL reason is honest only as a QUOTED, refuted draft — "
            "`83e94826a8a` IS an ancestor of `refs/pull/14108/head`, so `git gc` does not prune "
            f"it: {flat[max(0, hit.start() - 60):hit.end() + 60]!r}")


def test_the_14068_attribution_names_its_tree_by_sha_never_by_the_pr_ref():
    """PR body §2 — the `#14068` attribution, UNGRADED before round 13 (P2).

    The block attributed the move past the 274 `graph.query` rows to "the `#14108` PRE-MERGE
    tree". `#14108` resolves to `refs/pull/14108/head`, which tracks a branch head, so that
    citation does not name a tree at all — and measured, the tree it had to name is the one
    `2fe01fc775e` and `83e94826a8a` share.

    ⛔ NON-VACUITY IS IN THIS TEST, ON THE REAL BLOCK: the reviewer's proof that this site was
    unguarded was that restoring the pre-fix text left all 14 arms green. The in-memory mutant
    below is that restoration, so the arm can be seen to FIRE on it — a green that survives the
    mutation is exactly what the finding charged.
    """
    flow = re.sub(r"\n#:\s?", " ", _residue_block())
    # ⛔ DIRECTION 1 — the tree is named by SHA, in the same window as the diff command it
    # qualifies, and the attribution names the PR that landed it by number as well. The arm is
    # NOT phrase-keyed (review, round 17, P3): see `_diff_names_tree_by_sha`.
    assert _diff_names_tree_by_sha(flow), (
        f"the paragraph must name the tree it diffs against by SHA (`{RESIDUE_ABBREV}`), because "
        "the issue number cannot name it")
    assert re.search(r"`#14068`'s \(`" + RESIDUE_REWRITE_PARENT + r"`\)", flow), (
        f"the move past 274 must be attributed to `#14068`'s tree with its sha "
        f"(`{RESIDUE_REWRITE_PARENT}`), not to a lane and not to a PR head ref")
    # ⛔ DIRECTION 2 — the pre-fix citation is refused. `#14108` may be discussed (it IS the
    # ref the block explains at length); it may not ADDRESS a tree or a tip.
    assert not _pr_ref_as_tree_violations(flow), (
        "`#14108` resolves to `refs/pull/14108/head`, a ref that TRACKS a branch head — it cannot "
        f"address a tree: {_pr_ref_as_tree_violations(flow)[0]!r}. Name the tree by its sha.")
    # ⛔ DIRECTION 1 DOES NOT RED AN HONEST REWORDING — the SAME claim as `git diff` between X
    # and Y, in another wording, must stay green (review, round 17, P3: this arm required the
    # literal phrase). The anchor is BEST-EFFORT, like the mutant below and for the same reason
    # (round 17, P3): an honest rewording of the block must not red the arm.
    anchor = f"`git diff` between `{RESIDUE_REWRITE_PARENT}` and `{RESIDUE_ABBREV}`"
    assert _diff_names_tree_by_sha(_install_pre_fix(
        flow, anchor, f"diffing `{RESIDUE_REWRITE_PARENT}` against `{RESIDUE_ABBREV}`",
        f"diffing `{RESIDUE_REWRITE_PARENT}` against `{RESIDUE_ABBREV}`")), (
        "`diffing `X` against `Y`` is the SAME claim as `git diff` between `X` and `Y`; redding "
        "it is the over-specified shape the review removed")
    # ⛔ THE MUTATION, ON THE REAL BLOCK: restore the citation the finding is about. The anchor is
    # BEST-EFFORT (review, round 17, P3) — an honest rewording must not red this arm.
    pre_fix = f"and `{RESIDUE_ABBREV}` over"
    mutant = _install_pre_fix(
        flow, pre_fix, "and the `#14108` PRE-MERGE tree over",
        f"the move is measured on the `#14108` PRE-MERGE tree and `{RESIDUE_ABBREV}` over it")
    assert _pr_ref_as_tree_violations(mutant), (
        "the PRE-FIX citation must be refused — restoring `the `#14108` PRE-MERGE tree` left "
        "every arm green before this one existed")


def test_the_recut_pair_is_bound_to_its_rev_never_to_a_relative_head():
    """PR body §4 — the `e022fc935b` scoping and the "pair THIS head pins" contradiction (P2).

    Two halves of one correction: `main`'s tree at a RELATIVE "this commit's `origin/main`" and
    the pair that tree derives stated as "the pair THIS head pins". This head pins
    `(11844, "70d6cfdcd8b769e4")`, so the relative form asserted a pair this head does not carry.
    """
    flow = re.sub(r"\n#:\s?", " ", _residue_block())
    # ⛔ DIRECTION 1 — the pair is bound to the rev that DERIVES it, and `origin/main` is scoped.
    assert re.search(re.escape(RESIDUE_PAIR) + r"[^.]{0,80}`" + RESIDUE_PAIR_REV + r"`", flow), (
        f"the {RESIDUE_PAIR} pair must be stated against `{RESIDUE_PAIR_REV}`, the tree that "
        "derives it — a pair stated against a relative head is what the finding is about")
    assert re.search(r"`origin/main` this was written against", flow), (
        "`origin/main` is a moving ref: the sentence must scope it to when it was read")
    # ⛔ DIRECTION 2 — no unrefuted relative binding.
    assert not _relative_pin_addresses(flow), (
        "a pin pair / `origin/main` bound to a RELATIVE head must be scoped to the rev it names: "
        f"{_relative_pin_addresses(flow)[0]!r}")
    # ⛔ THE MUTATIONS, ON THE REAL BLOCK — each half, restored. The anchors are BEST-EFFORT
    # (review, round 17, P3), so an honest rewording of the surrounding prose cannot red the arm.
    scoped = f"re-cut to `{RESIDUE_PAIR}` on `{RESIDUE_PAIR_REV}`'s tree"
    assert _relative_pin_addresses(_install_pre_fix(
        flow, scoped, f"re-cut to the pair THIS head pins, `{RESIDUE_PAIR}`",
        f"re-cut to the pair THIS head pins, `{RESIDUE_PAIR}`, on `{RESIDUE_PAIR_REV}`'s tree")), (
        "`the pair THIS head pins` must be refused: this head pins (11844, …)")
    when_written = "(the `origin/main` this was written against)"
    if when_written in flow and "`main`'s tree at `e022fc935b` " in flow:
        relative = flow.replace(when_written, "").replace(
            "`main`'s tree at `e022fc935b` ", "this commit's `origin/main` ")
    else:
        relative = f"{flow} this commit's `origin/main`"
    assert _relative_pin_addresses(relative), (
        "`this commit's `origin/main`` must be refused: a rebase falsifies it")
    # ⛔ THE SPELLING THE ROUND-13 ARM MISSED (review, round 17, P3). Measured before this fix:
    # appending this clause left this arm GREEN, because clause 1 keyed on the literal `THIS head`
    # only. Same defect, different noun — so the arm must key on the SHAPE.
    assert _relative_pin_addresses(
        f"{flow} and the pair THIS COMMIT pins, `{RESIDUE_ABBREV}` over"), (
        "a relative pin spelled `THIS COMMIT pins` is the same defect as `THIS head pins` — the "
        "arm must refuse the SHAPE, not one spelling of it")
    # ⛔ THE REVERSED ORDER, ADDED WITH THE MERGE-INTRODUCED FALSEHOOD IT MISSED (#14282 fix
    # lane, P3-class). Clause 1 sees the noun only BEFORE the head, so `At THIS head the committed
    # pair is `11861`` left this arm GREEN on a head whose committed pair is `11864`. Non-vacuity
    # is the mutation itself: reinstall the shipped wording and the arm must bite.
    assert _relative_pin_addresses(
        f"{flow} At THIS head the committed pair is `11861`, and `--check` exits 0 against it"), (
        "`At THIS head the committed pair is <pair>` must be refused: it binds the pin to the tree "
        "the reader is standing on, which is how a merge re-cut falsified it silently")


def test_a_pair_bound_to_the_pair_below_is_the_pins_own_pair():
    """PR #14209 review F2 — two sentences bound a STALE pair to a relative address.

    `The pair below is --repin's output on THIS tree at 1b0db04894 ((11840, …))` and
    `(11843, …), the SAME pair this branch's tree prints` are both relative addresses, and after
    the merge with `main` the tree printed `(11857, …)` — so both were FALSE on the tree that
    would have shipped them. Neither was graded: they carry no `pins` verb for
    `_RELATIVE_PIN_ADDRESS` and no `#14108` for `_PR_REF_AS_TREE`, which is why they survived a
    clean review and needed a reader to catch them.

    Object-free: it compares against `privatise.SWEEP_RESIDUE`, so it runs in every job.
    """
    flow = re.sub(r"\n#:\s?", " ", _residue_block())
    own = tuple(privatise.SWEEP_RESIDUE)
    stale = [b for b in _pairs_bound_to_a_relative_tree(flow) if b[1] != own]
    assert not stale, (
        "a pair bound to `the pair below` / `this branch's tree` must BE the pin this file "
        f"carries, {own} — name the rev that derived it instead: {stale[0]!r}")
    # ⛔ NON-VACUITY, ON THE REAL BLOCK: the two pre-fix sentences, installed verbatim. Both were
    # GREEN on every arm before this one existed.
    pre_fix = [
        'The pair below is `--repin`\'s output on THIS tree at `1b0db04894` '
        '(`(11840, "b7eb08ff3acdcce4")`), measured, never carried forward by arithmetic.',
        '`(11843, "6aed5368ffa10513")`, the SAME pair this branch\'s tree prints, '
        'which is why it is the branch that is red rather than the change.',
    ]
    for sentence in pre_fix:
        mutant = _pairs_bound_to_a_relative_tree(f"{flow} {sentence}")
        assert [p for _, p in mutant if p != own], (
            "the PRE-FIX sentence must be refused — nothing graded it before this arm existed: "
            f"{sentence!r}")
    # ⛔ AND THE HONEST FORMS STAY GREEN, so the arm cannot be satisfied by deleting prose: a
    # past-tense measurement, another tree's pair quoted beside it, and a pair scoped to its rev.
    honest = [
        'the pair below was RE-DERIVED with `--repin` on THIS merge tree',
        '`main` at `fbcd24760b` printed `(11831, "b14af0d2db12529b")` for ITS tree',
        'the SAME pair this branch\'s tree carried at `72c104bcbb`',
    ]
    for sentence in honest:
        assert not _pairs_bound_to_a_relative_tree(sentence), (
            f"an honest, rev-scoped measurement must NOT red this arm: {sentence!r}")


def test_the_capture_rewrites_parent_is_the_rev_the_block_names():
    """PR body §1 — the rewrite's PARENT (review, round 13, P2).

    `83e94826a8a` is the pre-rebase DRAFT of the commit that landed as `1b82cc7063`; the
    rewrite's PARENT — the tree the row counts above are measured against — is
    `2fe01fc775e`, the same rev that pins `(11839, "0a7e60fa05aa31b9")`. The pre-fix sentence
    presented the draft as the parent, which is the misattribution round 8 removed.
    """
    flow = re.sub(r"\n#:\s?", " ", _residue_block())
    assert re.search(r"(?i)capture rewrite'?s own PARENT,\s*`" + RESIDUE_REWRITE_PARENT + r"`",
                     flow), (
        f"the block must name the capture rewrite's PARENT as `{RESIDUE_REWRITE_PARENT}`")
    assert not _parent_claims(flow), (
        "the pre-rebase DRAFT (`83e94826a8a`) is not the rewrite's parent — `2fe01fc775e` is: "
        f"{_parent_claims(flow)[0]!r}")
    parent_anchor = "on the capture rewrite's own PARENT,"
    assert _parent_claims(_install_pre_fix(
        flow, parent_anchor,
        "on this commit's PRE-REWRITE tree (`83e94826a8a`), which is the PARENT,",
        f"this sentence names this commit's PRE-REWRITE tree (`{RESIDUE_ABBREV}`) as the parent")), (
        "the PRE-FIX wording must be refused: the draft is not the parent, and `this commit's` "
        "is a relative address in a block that has been re-cut under it")


def test_the_relative_address_arms_accept_honest_rewordings():
    """The other half of the three arms above: an honest rewrite must not red.

    Written because a phrase-keyed ban turns a REWORDING into a false block (this file's
    round-12 lesson on the abbreviation and pointer arms), and because a negative-only arm can
    be satisfied by DELETING the sentence — each arm above also asserts the fact that replaced
    the defect, so deletion is red.
    """
    honest = [
        "the move is `#14068`'s (`2fe01fc775e`), and `#14108` resolves to a tree that is not it",
        "`#14108` resolves to `refs/pull/14108/head`, so it resolves to a DIFFERENT tree",
        "on the #14068 tree, which is the MERGE tree `surface-guard` grades",
        f"re-cut to {RESIDUE_PAIR} on `{RESIDUE_PAIR_REV}`'s tree, measured",
        "`main`'s tree at `e022fc935b` re-cut to the pair that tree derives",
        "`--repin` on THIS tree prints the same pair the first merge derived",
        "it left the sentence asserting a pair this head does not carry",
        f"on the capture rewrite's own PARENT, `{RESIDUE_REWRITE_PARENT}`",
        "that tip's parent is `2fe01fc775e`, the very rev just named",
    ]
    for text in honest:
        assert not _pr_ref_as_tree_violations(text), text
        assert not _relative_pin_addresses(text), text
        assert not _parent_claims(text), text
    # ⛔ AND THE QUOTED PRE-FIX DRAFTS THE BLOCK ITSELF KEEPS are honest prose, because the block
    # refutes them — refusing them would red the block for documenting its own corrections.
    refuted = [
        'it names the TREE and not "the pair THIS head pins", which is what it said',
        "an earlier draft said the pair read `origin/main`, and this commit's `origin/main` "
        "was then wrong",
    ]
    for text in refuted:
        assert not _relative_pin_addresses(text), text


def test_the_relative_pin_arm_keys_on_the_shape_not_on_one_phrase():
    """Review round 17 (P3): the round-13 arm matched the literal `THIS head` only.

    So the SAME relative-pin defect spelled `THIS COMMIT pins` / `this tree pins` / `the head
    pins` stayed GREEN — narrower than the "SHAPE, NOT PHRASE" contract the arm's own docstring
    claims. Measured before the fix: appending "and the pair THIS COMMIT pins, `83e94826a8a`
    over" left `test_the_recut_pair_is_bound_to_its_rev_never_to_a_relative_head` passing.

    Non-vacuity is in the REFUSED list: each entry is a relative head as the SUBJECT of a pin
    verb, i.e. the defect itself, differing only in spelling. The HONEST list is the other half —
    the block legitimately records that the pair was printed or measured `on THIS tree`, and the
    fix must not turn that measurement into a false block.
    """
    refused = [
        f're-cut to the pair THIS head pins, `{RESIDUE_PAIR}`',
        f'and the pair THIS COMMIT pins, `{RESIDUE_ABBREV}` over',
        f'this tree pins `{RESIDUE_PAIR}`',
        "the head pins the pair, so the figure cannot drift",
        "re-cut to the pair THIS revision pins",
        "this commit's `origin/main`",
        "the SAME pair this branch's tree prints",
        "and this PR's tree pins the pair, so the figure cannot drift",
    ]
    for text in refused:
        assert _relative_pin_addresses(text), (
            "a relative head used as the SUBJECT of a pin verb is the defect the round-13 arm "
            f"missed in this spelling: {text!r}")
    honest = [
        "`--repin` on THIS tree prints the same pair the first merge derived",
        "the pasted pair is `--repin`'s output measured on THIS tree, never arithmetic",
        "the pin is the pair `--repin` printed on THIS tree",
        "only THIS pin is this tree's",
        f'`{RESIDUE_PAIR}` is what THIS tree derives',
        "a pair this head does not carry",
        f"on `{RESIDUE_PAIR_REV}`'s tree, which derives it",
        "the SAME pair this branch's tree carried at `72c104bcbb`",
        "the same pair this PR's tree printed at `1b0db04894`",
    ]
    for text in honest:
        assert not _relative_pin_addresses(text), (
            "an honest MEASUREMENT on a relative tree is not a relative BINDING — widening the "
            f"arm must not red it: {text!r} -> {_relative_pin_addresses(text)!r}")


def test_the_mutation_anchors_do_not_red_an_honest_rewording(monkeypatch):
    """Review round 17 (P3): the three arms above anchored their mutation with
    `assert <anchor> in flow, "anchor for the mutation is gone"`, so an HONEST rewording
    elsewhere in the block red them — the false-block shape this file rejects for the
    abbreviation and pointer arms.

    Measured pre-fix: replacing `and `83e94826a8a` over` with the equally-true
    `and `83e94826a8a` across` failed
    `test_the_14068_attribution_names_its_tree_by_sha_never_by_the_pr_ref` with
    "anchor for the mutation is gone".

    The block below is the real one with three honest rewordings applied — one for each of the
    three anchors — and every DIRECTION-1 assert still holds (`over` → `across` keeps "`git diff`
    between `A` and `B`", which editing the `and` would not), so all three arms must stay GREEN.
    On the pre-fix code this test fails at the first one.
    """
    pristine = re.sub(r"\n#:\s?", " ", _residue_block())
    honest_block = (
        pristine
        .replace(f"and `{RESIDUE_ABBREV}` over", f"and `{RESIDUE_ABBREV}` across")
        .replace(f"re-cut to `{RESIDUE_PAIR}` on `{RESIDUE_PAIR_REV}`'s tree",
                 f"re-cut to `{RESIDUE_PAIR}` on `{RESIDUE_PAIR_REV}`'s own tree")
        .replace("on the capture rewrite's own PARENT,",
                 "on the capture rewrite's OWN PARENT,")
    )
    assert honest_block != pristine, "the rewordings did not apply — this arm would be vacuous"
    # ⛔ Every pre-fix anchor must be GONE, or the arm would prove nothing about the anchors.
    # (arm2's anchor spans a `#:` line break in the RAW block, which is why the rewording is
    # applied to the normalised flow — the same text the arms themselves search.)
    for gone in (f"and `{RESIDUE_ABBREV}` over",
                 f"re-cut to `{RESIDUE_PAIR}` on `{RESIDUE_PAIR_REV}`'s tree",
                 "on the capture rewrite's own PARENT,"):
        assert gone not in honest_block, f"anchor not reworded: {gone!r}"
    monkeypatch.setattr(sys.modules[__name__], "_residue_block", lambda: honest_block)
    # Each arm re-reads `_residue_block()` and must not fail on a drifted anchor.
    test_the_14068_attribution_names_its_tree_by_sha_never_by_the_pr_ref()
    test_the_recut_pair_is_bound_to_its_rev_never_to_a_relative_head()
    test_the_capture_rewrites_parent_is_the_rev_the_block_names()
    # ⛔ And the arms still BITE on the reworded block: the installer must install the defect
    # where the anchor is gone, not silently skip the mutation.
    flow = re.sub(r"\n#:\s?", " ", honest_block)
    assert _pr_ref_as_tree_violations(_install_pre_fix(
        flow, f"and `{RESIDUE_ABBREV}` over", "x",
        f"the move is on the `#14108` PRE-MERGE tree and `{RESIDUE_ABBREV}` over it")), (
        "the fallback path must still install a REFUTABLE defect, or the arm goes vacuous "
        "the moment its anchor is reworded")


def _arm_refspecs_literal_keys() -> list[str]:
    """Every KEY EXPRESSION written in the `_ARM_REFSPECS` dict literal, in source order.

    Read from THIS file's own source rather than from the imported mapping, because the defect it
    grades is invisible in the imported mapping: a duplicate key does not raise, it is silently
    collapsed by Python, so `_ARM_REFSPECS` reports only the survivor. The literal is the only place
    the shadowed row still exists (#14282 review cycle 12, P3).
    """
    body = Path(__file__).read_text().split("_ARM_REFSPECS: dict[str, str] = {", 1)[1].splitlines()
    keys: list[str] = []
    for line in body:
        if line == "}":
            break
        matched = re.match(r'^    ("[^"]+"|[A-Z_]+):', line)
        if matched:
            keys.append(matched.group(1))
    return keys


def test_every_addressed_rev_names_a_fetchable_refspec():
    """Review round 12 (P2): every rev a skip names must be FETCHABLE, or the skip is a lie.

    `_rearm_command` indexes `_ARM_REFSPECS` directly, so an unmapped rev raises instead of
    printing an inert `git fetch origin <abbreviation>`. This arm keeps the map honest for
    the revs a skip can name today.

    ⛔ EVERY ENTRY IS GRADED, NOT A HARDCODED SIX (#14282 review, P3 residual). This arm used
    to iterate a literal tuple of the revs then in the map, so the three addresses the re-cut
    paragraph publishes (`5e83c2a2f6`, `10e51693f1`, `efe5605e976a`) were added to the map and
    graded by NOTHING: an ABBREVIATION or a SOURCE-ONLY refspec on any of them passed silently,
    which is exactly the round-12/round-13 inert remedy one level down. The first assertion still
    requires the revs a skip can name today, so deleting an entry is caught; the loop grades the
    SHAPE of every entry the map holds, so folding a new rev in cannot buy an ungraded line.

    ⛔ PRESENCE AND NON-VACUITY ARE ONE PREDICATE (#14282 review, P3). These used to be a
    per-rev `rev in _ARM_REFSPECS` loop followed by `assert _ARM_REFSPECS, "non-vacuity: …"`.
    Once the loop has proven six keys the map is provably non-empty, so nothing could ever make
    THAT line the one that reds: it was an assertion whose value cannot vary — the round's
    own rule 2, a catch not attributable to the guard it claims to prove. A superset assert
    carries both facts and stays reachable: delete any key and it names the rev that went
    missing, empty the map and that same line reds naming every rev in `expected` — the fixed
    tuple UNION `OWN_PINS`. ⛔ ITS SIZE IS DERIVED, NEVER TYPED (#14282 review cycle 2, P3, the
    same class the map's own note repairs): this sentence published a figure — "the six", then
    "ELEVEN" — and each went stale the moment `OWN_PINS` gained a key, because no arm grades the
    size restated in THIS sentence. ⛔ SCOPED TO THIS SENTENCE, DELIBERATELY (#14282 review, P3):
    an earlier cut of this clause said "no arm grades a size restated in prose", which is FALSE of
    the tree and its wrong outcome is deletion of a live arm — `tools/sdk_privatise.py`'s `SIX`/`EIGHT`
    are graded through this file's `RESIDUE_INTERVAL_COMMITS`/`RESIDUE_CARRIER_COUNT` constants (they
    are defined HERE, not in the tool), by the arms here, because the numerals there are load-bearing. The rule is about a
    figure published HERE without a grader, never about prose generally. A reader who needs the
    population's size reads `expected`.

    ⛔ AND THAT UNION IS THE MERGE ITSELF (#14282 merged with #14258), WHICH IS WHY THE FIGURE
    CHANGED FROM A FIXED ONE TO A DERIVED ONE. #14258 independently added two `OWN_PINS` keys,
    `10e51693f166` and `0351fe856942`, and its own reason for iterating `OWN_PINS` here is
    the same one:
    a fixed tuple leaves a newly-added key ungraded, and the failure surfaces not as a red
    but as a `KeyError` at SKIP time in `test_the_own_pin_map_matches_each_revs_own_tree` on
    a `fetch-depth: 1` checkout — an ERROR where a SKIP was designed. Both spellings and
    both key sets are kept, because a union that drops one side re-opens the hole it closed.

    ⛔ THE REQUIRED SET INCLUDED ONLY THE FIXED TUPLE, SO A RE-CUT ADDRESS'S DELETION WAS STILL
    UNGRADED (#14282 review cycle 2, P3). The three addresses the re-cut paragraph publishes were
    in the map and their SHAPE was graded below, but they were absent from `expected`, so deleting
    one left this arm GREEN — the loss surfacing at best as a `KeyError` out of `_rearm_command` on
    the skip path, never as an assertion. `expected` is now that tuple UNION every `OWN_PINS` key,
    because a rev the block publishes an OWN PIN for is exactly a rev a skip may name.

    ⛔ AND AN ABBREVIATED SOURCE IS THE SAME HOLE ONE SHAPE OVER (#14282 review, P3). The
    wrong-source guard AT THAT TIME matched `[0-9a-f]{7,40}`, which admitted a 7-to-39-char prefix,
    and `startswith(rev)` is TRUE for the key's OWN abbreviation — so
    `+5e83c2a2f6:refs/remotes/origin/5e83c2a2f6` satisfied EVERY assertion in this arm while
    `git fetch --dry-run origin "+5e83c2a2f6:…"` returns
    `fatal: couldn't find remote ref 5e83c2a2f6`. A source that IS a sha must therefore be the
    full 40 hex, never in between. ⛔ THAT SENTENCE IS PAST TENSE ON PURPOSE (#14282 review
    cycle 4, P3): it records the predicate that WAS here — this docstring said "matches
    `[0-9a-f]{7,40}`" in the present tense, a few lines above a comment saying the predicate is
    NOT that, so a lane reconciling the two in the wrong direction would re-open this hole. ⛔ NO
    LINE OFFSET IS TYPED (#14282 review cycle 2, P3): this read "33 lines above", which is
    ungraded and rots on the next insertion — see this file's own rule that an address is named by
    its TEXT and never by a line number. The
    guard now decides "is a sha" by a CASE-INSENSITIVE HEX PREFIX with a floor of 4 (git's
    minimum abbreviation) and then requires the full 40 LOWERCASE hex — see the comment above
    `if re.match(` below, which is the one place the rule is stated. The negative control drives
    this very arm over each inert shape so the hole cannot quietly reopen.
    """
    expected = {RESIDUE_ABBREV, RESIDUE_REWRITE_PARENT, RESIDUE_DELETING_COMMIT,
                RESIDUE_INTERVAL_END, RESIDUE_PR_HEAD, "origin/main"} | set(OWN_PINS)
    missing = sorted(expected - set(_ARM_REFSPECS))
    assert not missing, (
        f"{missing} have no fetchable address — a skip naming one is inert, and an empty map "
        "is not a vacuous pass")
    for rev, refspec in _ARM_REFSPECS.items():
        assert not re.fullmatch(r"[0-9a-f]{7,39}", refspec), (
            f"{rev} is mapped to an ABBREVIATION, which git cannot `want`")
        source, _, destination = refspec.lstrip("+").partition(":")
        assert destination, (
            f"{rev} is mapped to a SOURCE-ONLY refspec: git records it in `FETCH_HEAD` and "
            "creates no local ref, so a skip printing it re-arms the object DB and leaves the "
            "ADDRESS assertion UNCHECKED — the round-13 inert remedy")
        # ⛔ A WRONG SOURCE SHA UNDER A CORRECT KEY (#14282 review cycle 2, P3 residual). Grading
        # only the DESTINATION lets `+<another-sha>:refs/remotes/origin/<rev>` pass, so the map
        # would fetch rev B under rev A's address and every `_ref_sha` premise reading that ref
        # would be a premise about the WRONG TREE — the same "address that resolves to a different
        # tree" defect this block exists to catch, one level down in the re-arm path. The source is
        # graded ONLY where it IS a sha: the older entries address `refs/pull/14108/head`
        # (a live pointer rather than an abbreviation of the key — measured: `83e94826a8a`,
        # `2fe01fc775e`, `aee14c5ceb`, `d5ecef0d72`, `d523c9be8c`), `origin/main` addresses a
        # branch name, and `0351fe856942` (added by #14258) addresses `refs/pull/14160/head` — so
        # every entry whose source is a ref NAME is excused; the sha sources are graded by both
        # assertions below. ⛔ THESE COUNTS ARE NOT TYPED (#14282 review cycle 2, P3): the split
        # was published as "FIVE … SEVEN of … ELEVEN … the remaining FOUR" and nothing graded it —
        # mutating that split's numerals left the whole suite green — so the population is named by
        # TEXT and its SIZE comes from the map, which is the only form that survives an entry
        # being added.
        # ⛔ THE PREDICATE IS A HEX PREFIX, CASE-INSENSITIVE AND UNBOUNDED — NOT
        # `[0-9a-f]{7,40}` (#14282 review cycle 3, P3). The bounded-lowercase band graded only
        # what it admitted, so every sha-shaped source OUTSIDE it fell into the ref-NAME
        # exemption and was graded by NEITHER assertion — measured GREEN on four shapes, of which
        # TWO `git fetch` REFUSES (`rc=128`, `fatal: couldn't find remote ref`): the 41-hex source
        # and `<40 hex>g`; and TWO it RESOLVES (`rc=0`) — the UPPERCASE form of THIS key's own
        # commit (to the RIGHT tree, so refusing it is a deliberate over-block) and the UPPERCASE
        # form of a DIFFERENT commit, which re-arms the WRONG tree under this key's address, i.e.
        # verbatim the defect the block above exists to close.
        # ⛔ THE SPLIT IS 2 REFUSED + 2 RESOLVED, AND IT WAS MEASURED TWICE (#14282 fix lane, P3).
        # Three earlier drafts of this sentence got it wrong in three different ways — "refuses
        # them" over a list whose last item RESOLVES; then "THREE refused + the FOURTH resolves",
        # which counted a 10-char UPPERCASE ABBREVIATION (`5E83C2A2F6`, refused) that is NOT one of
        # the four shapes, instead of the 40-char uppercase source that IS and that RESOLVES. Each
        # draft invited a reader to run `git fetch --dry-run` and watch the sentence fail. The
        # enumeration in the negative control below is the authoritative one; this paragraph now
        # agrees with it. What IS graded — by that control — is that all four shapes RAISE.
        # A source whose LEADING HEX RUN is 4+ characters is a sha attempt, so it is graded; a
        # source with no such prefix (`main`, `origin/main`, `refs/pull/<n>/head`) is a ref NAME
        # and is exempt by construction. ⛔ THE FLOOR IS 4, NOT 7, AND IT IS SAID HERE BECAUSE THE
        # PROSE USED TO CLAIM MORE THAN THE CODE DID (#14282 review cycle 4, P3): the previous
        # predicate said `{7,}` while the sentence above it said "merely STARTS with a hex run",
        # so a 1-6-hex source was graded by NEITHER assertion — measured GREEN for `+5e83c2:` and
        # `+5e83:` while `git fetch` refuses both (`fatal: couldn't find remote ref`). 4 is git's
        # own minimum abbreviation, so every abbreviation git could be handed is now graded.
        # A future ref name that genuinely begins with 4+ hex characters would be flagged —
        # accepted deliberately: a refused re-arm is cheaper than an inert one.
        if re.match(r"[0-9a-fA-F]{4,}", source):
            assert re.fullmatch(r"[0-9a-f]{40}", source), (
                f"{rev} is mapped to SOURCE {source!r}, which is not the full 40-hex lowercase "
                "sha git can `want` (an abbreviation, a case-mangled sha, or an over-long hex "
                "run) — a full 40-hex sha or a ref NAME, never in between")
            assert source.startswith(rev), (
                f"{rev} is mapped to a refspec whose SOURCE is {source[:12]} — a DIFFERENT "
                "commit, so a skip re-arming off it fetches the wrong tree")
    assert _ARM_REFSPECS[RESIDUE_PR_HEAD].lstrip("+").partition(":")[2] == "refs/pull/14108/head", (
        "the destination must be the ref `_ref_sha` reads, or the fix re-arms a different ref")
    assert _ARM_REFSPECS["origin/main"].lstrip("+").partition(":")[2] == "refs/remotes/origin/main"
    # ⛔ TWO EDITS ON THIS MAP WERE UNGRADED (#14282 review cycle 12, P3 both). Reverting either one
    # to its pre-review text left all 35 other tests green, so the map could silently lose the very
    # property the commit that fixed it argued for. Both are graded here, once.
    #
    # (1) THE DESTINATION NAMESPACE. `git fetch --prune origin` DELETES a remote-tracking ref the
    # remote does not advertise, so a `refs/remotes/origin/<abbrev>` destination arms a ref the very
    # next routine prune removes — an inert re-arm that reads as a live one. `origin/main` is the one
    # legitimate `refs/remotes/` row: `main` IS advertised, so prune leaves it alone.
    assert not [
        (rev, dest) for rev, dest in _ARM_REFSPECS.items()
        if re.fullmatch(r"\+[0-9a-f]{40}", dest.partition(":")[0]) and ":refs/remotes/" in dest
    ], (
        "a sha-want row arms a `refs/remotes/origin/…` destination — `git fetch --prune origin` "
        "removes it, so the printed re-arm is inert. Use `refs/pi/<abbrev>`, which prune does not touch")
    #
    # (2) ONE KEY, ONE ROW. A literal key that spells an abbreviation ALREADY bound by one of the
    # constant keys (`RESIDUE_REWRITE_PARENT` is `2fe01fc775e`) does not raise — Python keeps the LAST
    # duplicate — so the constant's row becomes dead data and the printed remedy silently changes.
    # The literal-key count is the mechanical tell: every key expression in the dict literal must
    # survive into the mapping, so a duplicate (two literals, or a literal shadowing a constant) makes
    # the two numbers disagree.
    assert len(_ARM_REFSPECS) == len([
        line for line in _arm_refspecs_literal_keys()
    ]), (
        "`_ARM_REFSPECS` states a key more than once — a duplicate literal (or a literal spelling an "
        "abbreviation a constant key already binds) shadows an earlier row, so the map's printed "
        "remedy is not the one it looks like it states")
    assert _ARM_REFSPECS[RESIDUE_REWRITE_PARENT] == _PR_HEAD_REFSPEC, (
        "`RESIDUE_REWRITE_PARENT` no longer maps to `_PR_HEAD_REFSPEC` — a later duplicate key "
        "shadowed the row that creates `refs/pull/14108/head`")


def test_the_refspec_shape_guards_red_every_inert_shape_and_pass_every_live_one(monkeypatch):
    """The negative control for the shape guards in
    `test_every_addressed_rev_names_a_fetchable_refspec` (#14282 review, P3).

    A guard whose negative case cannot be reached proves the code ran, not that the map is
    FETCHABLE, so this drives the REAL arm over mutated copies of `_ARM_REFSPECS` held in memory:
    every inert shape must red and every live shape must pass. The abbreviation case is the one
    that satisfied every assertion in the arm while `git fetch` refused it, which is why it is
    pinned here rather than only described in the docstring.
    """
    original = dict(_ARM_REFSPECS)

    def arm_with(refspec: str) -> None:
        mutated = {**original, "5e83c2a2f6": refspec}
        monkeypatch.setattr(sys.modules[__name__], "_ARM_REFSPECS", mutated)
        test_every_addressed_rev_names_a_fetchable_refspec()

    for refspec in (
        # ⛔ THIS CASE'S DESTINATION CHANGED (#14282 review cycle 12, P3). It was
        # `…:refs/remotes/origin/5e83c2a2f6` and listed as LIVE — but `git fetch --prune origin`
        # removes a remote-tracking ref the remote does not advertise, so that destination was the
        # INERT shape wearing the live case's clothes, and the arm accepted it. A sha want now must
        # arm `refs/pi/…`; the `refs/remotes/` form is graded as inert below.
        "+5e83c2a2f697d5879dd47d1204a2be88d6aff616:refs/pi/5e83c2a2f6",            # full sha
        "+main:refs/remotes/origin/main",                                            # ref NAME
        "+refs/pull/14108/head:refs/pull/14108/head",                                # ref NAME
    ):
        arm_with(refspec)  # must not raise

    for refspec in (
        "+5e83c2a2f6:refs/remotes/origin/5e83c2a2f6",  # ABBREVIATED SOURCE + destination: the hole
        "5e83c2a2f6",                                  # bare abbreviation
        "+5e83c2a2f6",                                 # source-only abbreviation
        "+5e83c2a2f697d5879dd47d1204a2be88d6aff616",    # source-only full sha: creates no local ref
        "+10e51693f1668e98fe62f3ee1293668a1f2f21a8:refs/remotes/origin/5e83c2a2f6",  # wrong sha
        # ⛔ THE FOUR SHAPES A BOUNDED-LOWERCASE PREDICATE LET THROUGH (#14282 review cycle 3,
        # P3). Each was GREEN before this fix, and the guard refuses all four because a sha
        # source must be the full 40 LOWERCASE hex. What `git fetch --dry-run` does with them is
        # NOT uniform, re-measured on this repo (#14282 fix lane, P3 — the sentence that stood
        # here said "THREE are refused; the FOURTH resolves", and that count was false):
        #   * 41 hex and `<40 hex>g` are REFUSED — `rc=128`, `fatal: couldn't find remote ref`;
        #   * the UPPERCASE form of THIS key's own commit RESOLVES — `rc=0`, and to the RIGHT
        #     tree, so refusing it is a deliberate over-block: lowercase is this map's convention
        #     and the case-insensitivity it relies on is git's leniency, not a contract to lean on;
        #   * the UPPERCASE form of a DIFFERENT commit RESOLVES — `rc=0` — and re-arms the WRONG
        #     tree under this key's address, which is the one that matters.
        # ⛔ THE ORDINAL IS MEASURED, NOT GRADED (#14282 fix lane, P3). Cycle 4 caught this
        # comment saying "the third one" for a fact belonging to the fourth and fixed the ordinal —
        # but no assertion here can grade WHICH shape a remote resolves (that needs a live fetch),
        # and the claim that it was "graded here rather than described" was itself ungradeable. It
        # is a measurement, cited as one; what IS graded is that all four shapes raise.
        "+5E83C2A2F697D5879DD47D1204A2BE88D6AFF616:refs/remotes/origin/5e83c2a2f6",  # CASE
        "+5e83c2a2f697d5879dd47d1204a2be88d6aff6166:refs/remotes/origin/5e83c2a2f6",  # 41 hex
        "+5e83c2a2f697d5879dd47d1204a2be88d6aff616g:refs/remotes/origin/5e83c2a2f6",  # hex+g
        "+10E51693F1668E98FE62F3EE1293668A1F2F21A8:refs/remotes/origin/5e83c2a2f6",  # CASE+wrong
        # ⛔ AND THE FLOOR ITSELF (#14282 review cycle 4, P3). The predicate's floor is 4 — git's
        # own minimum abbreviation — not 7, so a source truncated BELOW git's default display
        # width is graded too. Both were GREEN under `{7,}` while `git fetch` refused them.
        "+5e83c2:refs/remotes/origin/5e83c2a2f6",      # 6 hex
        "+5e83:refs/remotes/origin/5e83c2a2f6",        # 4 hex: git's minimum abbreviation
        # ⛔ AND THE PRUNE-DELETABLE DESTINATION (#14282 review cycle 12, P3). A FULL sha sources
        # this one, so every abbreviation guard passes it — it was the LIVE case above until the
        # namespace became graded. `git fetch --prune origin` deletes the ref it creates, so the
        # re-arm it prints is inert, which is the failure the map's own comment argues against.
        "+5e83c2a2f697d5879dd47d1204a2be88d6aff616:refs/remotes/origin/5e83c2a2f6",
    ):
        with pytest.raises(AssertionError):
            arm_with(refspec)


def test_the_skip_instruction_is_a_command_git_can_actually_run(monkeypatch):
    """Review round 12 (P2): the printed re-arm used to be `git fetch origin 83e94826a8a …`.

    Measured: `fatal: couldn't find remote ref 83e94826a8a` — git cannot `want` an
    abbreviation, and the block says so itself in its "11-character ABBREVIATION" sentence. An
    operator
    who followed the old line would believe the two repo-state arms were re-armed while they
    stayed UNCHECKED, in a PR whose thesis is falsifiability.
    """
    monkeypatch.setattr(sys.modules[__name__], "_rev_present", lambda rev: False)
    with pytest.raises(BaseException) as caught:  # `pytest.skip` raises `Skipped`, not `Exception`
        _requires(RESIDUE_ABBREV, RESIDUE_PR_HEAD, "origin/main")
    message = str(caught.value)
    assert "git fetch" in message, message
    command = re.search(r"git fetch origin [^`\n]+", message)
    assert command, f"the skip must print the command that re-arms it: {message!r}"
    tokens = command.group(0).split()[3:]
    assert tokens, command.group(0)
    for token in tokens:
        assert not re.fullmatch(r"[0-9a-f]{7,39}", token), (
            f"`git fetch` cannot `want` {token!r}: it is an abbreviation, and the skip must "
            "name a REFSPEC (`refs/pull/14108/head` / `main`) instead")
        # ⛔ AND IT MUST NAME A DESTINATION (review, round 13, P3): source-only fetches store
        # into `FETCH_HEAD` and create NO local ref, so `_ref_sha` — and therefore the ADDRESS
        # assertion, the only re-measurement of the moving pointer — stays unchecked. The
        # printed line must be the form that actually arms what the message promises.
        _, _, destination = token.lstrip("+").partition(":")
        assert destination, (
            f"the printed remedy `{token}` is SOURCE-ONLY: git stores it in `FETCH_HEAD` and "
            "creates no local ref, so the ADDRESS assertion stays UNCHECKED while the skip says "
            "the block's addresses are checked — print a `source:destination` refspec")
    assert any(token.lstrip("+").partition(":")[2] == "refs/pull/14108/head" for token in tokens), (
        "the interval commits are carried by `refs/pull/14108/head`, and the destination must be "
        f"the ref `_ref_sha` reads: {command.group(0)}")


def test_the_rearm_refspec_creates_the_ref_it_promises(tmp_path):
    """Review round 13 (P3): a SOURCE-ONLY re-arm leaves the ADDRESS assertion unchecked.

    Measured on this box and stated in git-fetch(1) ("Refspec"): `git fetch origin
    refs/pull/14108/head` — no destination — records the objects in `FETCH_HEAD` and creates
    NO local ref. So the round-12 remedy armed `_rev_present` (object DB) while
    `_ref_sha("refs/pull/14108/head")` stayed `None`, and the only arm that re-measures the
    moving pointer stayed silently UNCHECKED *while the skip message said the block's addresses
    were now checked* — the same inert-remedy class one level down.

    ⛔ HERMETIC, OFFLINE, AND IT PROVES THE MECHANISM RATHER THAN THE STRING: a bare "origin"
    carrying `refs/pull/9/head` is fetched into two clones, once in each form. The source-only
    fetch leaves the ref absent; the destination fetch creates it at the pushed sha. If git ever
    changes that, the premise assertion below says so instead of the arm silently rotting.
    """

    def run(*args: str, cwd: Path) -> str:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        assert done.returncode == 0, f"git {' '.join(args)} failed: {done.stderr.strip()}"
        return done.stdout

    try:
        run("--version", cwd=tmp_path)
    except (FileNotFoundError, AssertionError):  # no `git` on this host at all
        pytest.skip("no `git` on this host — the refspec mechanism cannot be measured here")

    origin = tmp_path / "origin.git"
    origin.mkdir()
    run("init", "--bare", "-q", ".", cwd=origin)
    seed = tmp_path / "seed"
    seed.mkdir()
    run("init", "-q", ".", cwd=seed)
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        run("config", key, value, cwd=seed)
    (seed / "f.txt").write_text("x\n", encoding="utf-8")
    run("add", "f.txt", cwd=seed)
    run("commit", "-qm", "seed", cwd=seed)
    sha = run("rev-parse", "HEAD", cwd=seed).strip()
    run("push", "-q", str(origin), "HEAD:refs/pull/9/head", cwd=seed)

    def fetch_into(name: str, refspec: str) -> str | None:
        clone = tmp_path / name
        clone.mkdir()
        run("init", "-q", ".", cwd=clone)
        run("remote", "add", "origin", str(origin), cwd=clone)
        run("fetch", "-q", "origin", refspec, cwd=clone)
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "refs/pull/9/head"],
            cwd=clone, capture_output=True, text=True)
        return verify.stdout.strip() or None

    assert fetch_into("source-only", "refs/pull/9/head") is None, (
        "premise: git-fetch(1) — a source-only refspec updates NO local ref. If this ever "
        "creates one, the destination requirement is unnecessary and this arm should be revisited")
    assert fetch_into("with-destination", "refs/pull/9/head:refs/pull/9/head") == sha, (
        "a DESTINATION refspec must create exactly the ref `_ref_sha` reads, at the pushed sha")
    printed = _ARM_REFSPECS[RESIDUE_PR_HEAD]
    assert printed.lstrip("+").partition(":")[2] == "refs/pull/14108/head", printed
    assert ":" in printed.lstrip("+"), (
        f"the printed remedy `{printed}` is the SOURCE-ONLY form measured above as inert")


# ── a rev's OWN published PIN is a pair, and the pair must be that rev's ────────
#
# ⛔ THE DEFECT THIS GRADES (review, round 14, P2): the `#14068` attribution published
# `d523c9be8c`'s OWN pin as `(11839, "0a7e60fa05aa31b9")` — the PARENT's pair — while that rev
# commits `(11838, "de5664dc98549d56")`, and `git rev-parse d523c9be8c^{tree}
# 1b82cc7063^{tree}` returns ONE sha. So the same paragraph said `d523c9be8c` "inherits that
# deletion" (the capture rewrite's own row set) AND that it carries the parent's figures — a
# published pair belonging to another tree, which is the class this block polices everywhere
# else. Nothing graded a published PAIR: `test_the_residue_pin_is_a_reading_of_the_whole_set`
# grades the SHAPE of `SWEEP_RESIDUE`, so mutating a true pair left the suite green.
#
# No `--repin` here: `--repin` is ~70 s per rev and re-derives a number, while the claim is
# about what a REV CARRIES. `git show <rev>:tools/sdk_privatise.py` reads that rev's own
# committed pin — deterministic and offline.
#
# ⛔ TWO ARMS, BECAUSE ONE ARM COULD NOT RUN IN CI (review, round 16, P2). This used to be a
# single arm that `git show`ed each rev behind `_requires(...)`, so it SKIPPED in every job that
# runs this file — the sharded `test` job checks out at `fetch-depth: 1`, and the one depth-0 job
# runs `--check`, not pytest. Measured: restoring the pre-fix pair left the suite UNCHANGED
# (19 passed / 4 skipped), so the round-14 correction was gated by nothing in the merge path.
# A guard a gating job cannot run is not a guard — the same class round 10 fixed for the two
# prose arms. So the block is graded against `OWN_PINS`, an object-FREE map, and the map is
# graded against each rev's committed pin by the premise arm that follows it.
_OWN_PIN_CLAIM_RE = re.compile(
    r"(?i:(?:Its[\s#:]+own[\s#:]+pin[\s#:]+is|commits))[\s#:]*`\((\d+),[\s#:]*[\"']([0-9a-f]{16})[\"']\)`")
#: ⛔ THE PHRASE IS `(?i:…)` BECAUSE THE BLOCK WRITES IT IN CAPITALS WHERE IT EMPHASISES (#14282
#: review cycle 6, P1, measured). This block's own convention is to set the operative verb in caps
#: — `origin/main` `1b0db0489408` **COMMITS** `(11843, "6aed5368ffa10513")` — and a case-sensitive
#: pattern silently skipped that clause while BOTH new guards passed: parity saw 8 clauses and 8
#: claims (the clause was invisible to the loose pattern too, because it carries the same phrase),
#: and the orphan arm saw no missing key because the rev was never in `OWN_PINS` either. So a wrong
#: pair typed into that clause would have passed everything. Scoped to the PHRASE, deliberately:
#: the digest class stays `[0-9a-f]{16}` lowercase, so a capitalised HEX DIGEST is still a RED —
#: the uppercase-digest mutant reds the claims-vs-clauses parity arm (one clause with no claim).
#: GRADED, not restated: `test_the_scoped_case_fold_still_reds_a_capitalised_digest` asserts the
#: loose clause pattern counts one MORE clause than the claim pattern parses, which is what fires
#: that arm. This sentence used to publish the mutant's two observed counts, measured at cycle 6's
#: head; cycle 7 added two clauses and made the figure FALSE without touching the sentence — the
#: same drift the extent figure is graded against (#14282), so no count is typed here at all.
#: How many clauses the pattern must match is NOT typed: it is counted off the block below.
#: ⛔ THE WRAP IS GRADED HERE BECAUSE A RE-WRAP SILENTLY DISARMS THIS ARM (#14282 review cycle 4,
#: P1, measured). The pattern was `Its own pin[\s#:]+is` — it tolerated a wrap between `pin` and
#: `is` but NOT between `Its` and `own`, so re-wrapping one sentence put `Its` at the end of a line
#: and the clause stopped matching. `10e51693f1`'s published pair then passed a mutant that set it
#: to `(99999, "aaaaaaaaaaaaaaaa")`: the whole suite stayed green, which is a FALSE PASS — the
#: exact failure this file exists to catch, introduced by a prose edit. The pattern now breaks at
#: ANY of those word boundaries, AND the parity assertion below makes a silent drop impossible
#: however the sentence is wrapped next.
_OWN_PIN_CLAUSE_TEXT_RE = re.compile(
    r"(?i:(?:Its[\s#:]+own[\s#:]+pin[\s#:]+is|commits))[\s#:]*`\(")
#: ⛔ THE PARITY PARTNER IS DELIBERATELY LOOSER THAN `_OWN_PIN_CLAIM_RE` IN EXACTLY ONE DIMENSION: it
#: stops after the OPENING BACKTICKED PAREN, so it does not grade the pair's CONTENTS. It therefore
#: catches a failure the matcher's own tolerances cannot — a PAIR that was wrapped or malformed (the
#: digest split across lines, or `[\"']` lost) still reads as a clause to the loose form and not to
#: the strict one. A bare `commits` in prose does NOT count, because the loose form requires the
#: opening paren: `commits` appears as an ordinary word many times in this block, in several
#: positions relative to a pair, and an earlier cut of this assertion counted every one of them and
#: reded an honest block with `6 == 18` — the arm grading the WRONG POPULATION, which is the same
#: defect class as the wrap it was written to catch, one level up. ⛔ AND NO POPULATION COUNT IS
#: PUBLISHED HERE (#14282 review, P3): this sentence published one and a fresh reader measured it
#: wrong at the same head. Publishing the correction would be the same defect with a newer number,
#: and nothing in this file grades a number written in prose — measured: adding one more such
#: claim would leave this sentence wrong with every arm still green, the same shape as the
#: orphaned `OWN_PINS` entry above. So the shape is described and left uncounted, and the
#: assertion below measures it directly instead of trusting a number written here.
#: ⛔ AND IT USES `[\s#:]+` BETWEEN
#: THE PHRASE'S OWN WORDS, not `\s+`: this block's line breaks are `\n#: `, so the `#:` is INSIDE the
#: gap and `\s+` alone matched one claim fewer than the matcher did — measured, and that asymmetry
#: is why the word-level separator must match the matcher's while only the pair body is relaxed.
_BACKTICKED_REV_RE = re.compile(r"`([0-9a-f]{7,40})`")

#: rev -> the `(rows, digest)` pair the block publishes AS that rev's own pin.
#: ⛔ THE OBJECT-FREE HALF: a dict lookup needs no git object, so
#: `test_a_revs_published_own_pin_is_the_pin_that_rev_commits` runs in every job. A rev the block
#: publishes but THIS MAP lacks REDS — folding a new rev in is what keeps the map honest, rather
#: than letting an ungraded sentence through. `test_the_own_pin_map_matches_each_revs_own_tree`
#: re-derives every entry from the repository wherever the objects exist.
OWN_PINS: dict[str, tuple[int, str]] = {
    "d523c9be8c": (11838, "de5664dc98549d56"),
    # ⛔ THE RECUT PARAGRAPH'S MEASUREMENT ADDRESSES (#14282 review, P3). It publishes each rev's
    # own COMMITTED pair — deliberately NOT the pair that rev's `--repin` prints, which is the
    # re-derivation and would claim another tree's figures. Recording them here is what makes the
    # addresses REACHABLE: the object-free arm above reds on a rev this map lacks, and the premise
    # arm below re-derives every entry from the repository. The mutant the review named — naming
    # `94d75667dc`, which COMMITS (11857, "ed40203dbbf32b87") and whose own `--repin` prints
    # (11860, '57e0960ca6f20f5a'), ONE row short of the re-cut pair, so it is never a correct
    # address — therefore reds instead of passing as prose.
    "5e83c2a2f6": (11858, "a1552662bf07a10b"),
    "10e51693f1": (11858, "a1552662bf07a10b"),
    "efe5605e976a": (11858, "a1552662bf07a10b"),
    # ⛔ AND #14258's TWO, UNCHANGED BY THE MERGE. Both sides reached the same pair from
    # the same +3, and all five of the added keys commit `(11858, "a1552662bf07a10b")`, so the
    # union is a fact about the tree rather than a reconciliation. Dropping either side would
    # leave a published `commits (N, "digest")` claim — or this branch's three `Its own pin is`
    # claims — with no map entry, which is what
    # `test_a_revs_published_own_pin_is_the_pin_that_rev_commits` reds on.
    # ⛔ THE `commits` SPELLING IS GRADED TOO (review of PR #14258, P3). The regex above used to key
    # on `Its own pin is` alone, so the re-pin block's `10e51693f166` (#14160) `commits
    # (11858, "a1552662bf07a10b")` — the same class of claim, written in a different honest form —
    # was ungraded: a rev published with another tree's pair would have passed in that spelling.
    # `0351fe856942` is #14160's PRE-squash tip (`refs/pull/14160/head`) and `10e51693f166` the
    # squash that landed it; both commit the same pair, measured with `git show
    # <rev>:tools/sdk_privatise.py | grep '^SWEEP_RESIDUE'`. ⛔ `--repin` at the SQUASH tree prints
    # `(11861, "f0647020531c078c")` — that is the drift this re-pin repairs, so it is `--repin` at
    # `0351fe856942`, NOT at the squash, that reproduces the pair; an earlier cut of this comment
    # said "either tree" (review round 2, P2) and the cut that replaced it said "alone", which the
    # NEXT sentence falsifies — `c5e84ad180f2` reads the same pair too, which is the whole point of
    # excluding it by PROVENANCE rather than by a measurement difference (review round 3, P2). ⛔ AND NOT `c5e84ad180f2`, where
    # `origin/fix/14125-census-p1s` stands today: it is a DESCENDANT of `0351fe856942`, pushed
    # after the squash, and its content never landed.
    "10e51693f166": (11858, "a1552662bf07a10b"),
    "0351fe856942": (11858, "a1552662bf07a10b"),
    # ⛔ THE CAPS-SPELLING CLAUSE, WHICH BOTH GUARDS WERE BLIND TO (#14282 review cycle 6, P1). The
    # block writes `origin/main` `1b0db0489408` **COMMITS** `(11843, "6aed5368ffa10513")` — in
    # capitals, its own emphasis convention. Every pattern here keyed on a lowercase `commits`, so
    # that clause was invisible to the matcher AND to the parity partner (which looks for the same
    # phrase), and this map lacked the key, so the orphan arm had nothing to report either: three
    # guards, one silent hole. The phrase is `(?i:…)` now, and this entry is what makes the clause
    # gradeable. Pair verified the way every other entry is
    # (`git show 1b0db0489408:tools/sdk_privatise.py | grep '^SWEEP_RESIDUE'`).
    "1b0db0489408": (11843, "6aed5368ffa10513"),
    # ⛔ TWO MORE CLAUSES THE GUARDS COULD NOT SEE (#14282 review cycle 7, P2), both of the same
    # shape the CAPS entry above documents: the pair is published, the rev is named, and NEITHER the
    # pattern NOR the orphan arm reaches it — the pattern because the sentence puts the pair first
    # ("the `(N, …)` PAIR is committed at `<rev>`"), the orphan arm because the rev was never in this
    # map, so there was no key to orphan. MEASURED: corrupting either pair left 35 tests green.
    # Both sentences now use the graded spelling (`<rev>` COMMITS `(N, "digest")`), and these entries
    # are what the orphan arm then enforces. Pairs verified the way every other entry is
    # (`git show <rev>:tools/sdk_privatise.py | grep '^SWEEP_RESIDUE'`).
    "2c0d66e2ec": (11835, "891187f5a4636487"),
    "e74cf1d13d": (11857, "ed40203dbbf32b87"),
    # ⛔ THREE MORE, SAME DOORWAY (#14282 review cycle 8, P2). Each published a rev's own pin in a
    # spelling the register does not grade — `which pins`, `pinned`, `still satisfies its own pin` —
    # and each rev was absent from this map, so the orphan arm had no key to report. MEASURED, on the
    # head before this commit: falsifying any of the three pairs left 35 tests green. All three are
    # now written in the graded register (`<rev>` COMMITS `(N, "digest")`) and recorded here.
    "2fe01fc775e": (11839, "0a7e60fa05aa31b9"),
    "e0accb8fe6": (11844, "70d6cfdcd8b769e4"),
    "72c104bcbb": (11843, "6aed5368ffa10513"),
    # ⛔ THE RUN-5 ILLUSTRATION'S TWO REVS (review of PR 14183, P2). The `#:` block publishes the
    # merge `8889f1140105` as committing `(11835, "891187f5a4636487")` while its own tree measures
    # `11843`, and `27cd81d55ebd` as re-pinning to that measured pair. Both pairs are true
    # (`git show <rev>:tools/sdk_privatise.py | grep '^SWEEP_RESIDUE'`), so BOTH revs belong here:
    # a merge of `origin/main` had dropped them from the graded spelling, which left the pairs
    # ungraded and this arm's coverage quietly narrower than the block claims. ⛔ BOTH LANES added
    # `8889f1140105` here independently while merging, so ONE entry is kept: a duplicate literal
    # key in this dict is SILENT (last wins) and would make the pair look double graded when it is
    # graded once. Measured: `(11835, "891187f5a4636487")` and `(11843, "6aed5368ffa10513")`.
    "8889f1140105": (11835, "891187f5a4636487"),
    "27cd81d55ebd": (11843, "6aed5368ffa10513"),
}


def _own_pin_claims(block: str) -> list[tuple[str, int, str]]:
    """`(rev, rows, digest)` for each "Its own pin is …" clause, with the rev it names.

    The rev is the NEAREST backticked hex token BEFORE the claim, because that is how the
    block writes it — the sentence it corrects reads "… `d523c9be8c`, the LAST of the eight,
    inherits that deletion. … Its own pin is …". Two SPELLINGS are graded, `Its own pin is` and
    `commits` (PR #14258's review, P3: the second was ungraded, so the same defect written as
    "`<rev>` commits `(N, "digest")`" passed). No claim returns an empty list; the caller
    asserts non-vacuity so deleting the sentence is not a way to pass.
    """
    claims = []
    for claim in _OWN_PIN_CLAIM_RE.finditer(block):
        revs = _BACKTICKED_REV_RE.findall(block[:claim.start()])
        if revs:
            claims.append((revs[-1], int(claim.group(1)), claim.group(2)))
    return claims


def test_the_scoped_case_fold_still_reds_a_capitalised_digest():
    """`(?i:…)` is scoped to the PHRASE, so a capitalised HEX DIGEST stays a RED.

    ⛔ WHY THIS IS AN ASSERTION AND NOT A SENTENCE. The comment above `_OWN_PIN_CLAIM_RE` used to
    publish this mutant's observed counts, and the commit that added the next two clauses made
    that figure FALSE without touching the sentence (#14282 review cycle 7: the same mutant then
    counted two more on each side). A typed observation of a mutant goes stale exactly like the
    extent figure does — this file's own subject — so the CLAIM is graded instead of restated: the
    loose clause pattern must count one clause MORE than the claim pattern parses, which is what
    reds the parity arm rather than letting a capitalised digest pass ungraded. Widening the digest
    class to `[0-9a-fA-F]` — the reason `(?i:…)` is scoped and not global — makes the two counts
    AGREE and this arm red.
    """
    block = _residue_block()
    claim = _OWN_PIN_CLAIM_RE.search(block)
    assert claim, "non-vacuity: the block still carries a graded claim to mutate"
    mutated = block[:claim.start(2)] + claim.group(2).upper() + block[claim.end(2):]
    assert mutated != block, "non-vacuity: the digest class is already case-insensitive"
    assert len(_OWN_PIN_CLAUSE_TEXT_RE.findall(mutated)) > len(_own_pin_claims(mutated)), (
        "a CAPITALISED digest must still be RED: the loose clause pattern counts the clause, the "
        "pair-grading pattern does not, so the parity arm fires. These two agreeing means the "
        "digest class was widened (or the phrase widened to a global `(?i)`) and a capitalised "
        "digest now passes ungraded.")


def test_a_revs_published_own_pin_is_the_pin_that_rev_commits():
    """A `(rows, digest)` published AS a rev's own pin must be that rev's committed pair.

    ⛔ OBJECT-FREE, so it runs in every job (review, round 16, P2). It grades the block against
    `OWN_PINS`; `test_the_own_pin_map_matches_each_revs_own_tree` grades that map against the
    repository. The split is the whole point: the first arm is the guard the merge path
    executes, the second is what stops the constant from drifting into a wrong pair.
    """
    block = _residue_block()
    claims = _own_pin_claims(block)
    assert claims, (
        "non-vacuity: the block still publishes at least one rev's OWN pin — deleting the "
        "sentence is not the fix for a wrong pair")
    #: ⛔ COUNT PARITY, so a RE-WRAP cannot silently disarm this arm (#14282 review cycle 4, P1).
    #: Every textual `Its own pin is` / `commits` clause in the block must yield a claim; a wrap
    #: that broke the match already caused one false pass once, and this makes the next such edit
    #: RED instead of green without anybody having to notice the regex.
    published_clauses = len(_OWN_PIN_CLAUSE_TEXT_RE.findall(block))
    assert len(claims) == published_clauses, (
        f"the block publishes {published_clauses} \"Its own pin is\"/\"commits\" clause(s) but "
        f"only {len(claims)} parsed as a graded claim — a WRAP (or a spelling) has disarmed this "
        "arm, and every pair in the unparsed clause(s) is passing ungraded. Re-wrap so the "
        "phrase is contiguous, or widen `_OWN_PIN_CLAIM_RE`")
    # ⛔ THE `commits` SPELLING MUST BE FOUND, not merely tolerated (#14258 review, P3 and the
    # mutation-attribution rule). Widening `_OWN_PIN_CLAIM_RE` ADDED an assertion surface — two
    # block clauses that were ungraded became graded — and without this arm REVERTING the
    # widening left the suite GREEN (measured: the pre-widening regex finds only `d523c9be8c`,
    # which is in `OWN_PINS`, so every assertion below passes over a surface that no longer
    # grades the clause it was widened for). A covered row that nothing holds covered is the
    # shape this file rejects, so the coverage itself is asserted here.
    assert {"10e51693f166", "0351fe856942"} <= {rev for rev, _rows, _digest in claims}, (
        "the `commits (N, \"digest\")` spelling must be GRADED: `_OWN_PIN_CLAIM_RE` has to match "
        "it, or a rev published with another tree's pair passes in that spelling — which is the "
        "defect the widening exists for. Findings found: "
        f"{sorted(rev for rev, _r, _d in claims)}")
    # ⛔ AND EVERY `OWN_PINS` KEY MUST BE REACHED BY SOME CLAUSE (#14282 review cycle 5, P3). The
    # loop below grades each CLAIM against the map, so a key no clause attributes to is graded by
    # NOTHING — and `_own_pin_claims` attributes a clause to the nearest backticked hex token
    # BEFORE it, so a parenthetical naming another rev between a rev and its clause silently
    # re-attributes the clause and orphans the first rev's entry. MEASURED: with `... 705 s BEFORE
    # `5e83c2a2f6`` written between `10e51693f1` and its own clause, DELETING the `10e51693f1` map
    # key left every arm GREEN while deleting `5e83c2a2f6` reded. This assertion is the one that
    # names the dropped key.
    orphans = sorted(set(OWN_PINS) - {rev for rev, _rows, _digest in claims})
    assert not orphans, (
        f"`OWN_PINS` carr{'ies' if len(orphans) == 1 else 'y'} {orphans} but NO clause in the "
        "block attributes a published pair to it — the entry is graded by nothing, so a wrong "
        "pair recorded under it would pass. Either the block dropped that rev's clause, or an "
        "edit put ANOTHER backticked rev between it and its clause (`_own_pin_claims` attributes "
        "to the nearest preceding token). Fix the block, not this map")
    for rev, rows, digest in claims:
        assert rev in OWN_PINS, (
            f"the block publishes `{rev}`'s own pin, but `{rev}` is not in `OWN_PINS`, so "
            "nothing grades it: record the pair that rev commits in the map (and let "
            "`test_the_own_pin_map_matches_each_revs_own_tree` verify it against the tree)")
        assert (rows, digest) == OWN_PINS[rev], (
            f"the block publishes `{rev}`'s own pin as ({rows}, {digest!r}); `OWN_PINS` records "
            f"{OWN_PINS[rev]!r} — a rev published with ANOTHER tree's pair is the "
            "`address that resolves to a different tree` defect this block exists to catch")


def test_the_own_pin_map_matches_each_revs_own_tree():
    """PREMISE: every `OWN_PINS` entry is what that rev actually commits.

    Skips (with the re-arm refspec) where the revs are absent — it is the object-dependent half,
    never the guard. If it reds, the constant above has gone stale and the object-free arm is
    grading against a wrong pair.
    """
    _requires(*sorted(OWN_PINS))
    for rev, (rows, digest) in sorted(OWN_PINS.items()):
        own = re.search(r'^SWEEP_RESIDUE = \((\d+), "([0-9a-f]{16})"\)',
                        _git("show", f"{rev}:tools/sdk_privatise.py"), re.MULTILINE)
        assert own, f"`{rev}` carries no readable `SWEEP_RESIDUE`, so its entry cannot be graded"
        assert (rows, digest) == (int(own.group(1)), own.group(2)), (
            f"`OWN_PINS[{rev!r}]` records {(rows, digest)!r}; that rev commits "
            f"({own.group(1)}, {own.group(2)!r}) — the map has gone stale, so the object-free "
            "arm is grading against a wrong constant")


def test_the_parent_pair_attribution_is_only_ever_quoted_as_falsified():
    """The specific false shape that landed: `the same pair as the parent`.

    ⛔ OBJECT-FREE, so it runs in the sharded `test` job too (the premise arm above skips on a
    `fetch-depth: 1` checkout). The sentence is honest prose only as a REFUTED draft; charging
    it is what stops the next edit from reinstating the pair without an object to notice.
    """
    block = _residue_block()
    hits = list(re.finditer(r"same pair as the parent", block, re.IGNORECASE))
    assert hits, "non-vacuity: the block still records the falsified clause it replaced"
    for hit in hits:
        assert _refuted(block, hit.start()), (
            "an UNREFUTED `same pair as the parent` claim is the round-14 defect: the pair one "
            "rev commits is not its parent's, so the claim is only honest where the block itself "
            "calls it wrong")
