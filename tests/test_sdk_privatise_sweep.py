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


def _rev_present(rev: str) -> bool:
    """Is `rev` carried by THIS clone? The `test` job is `fetch-depth: 1` (no objects)."""
    try:
        done = subprocess.run(
            ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
            cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:  # no `git` on this host at all
        return False
    return done.returncode == 0


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


#: The PRs that legitimately OWN `RESIDUE_REWRITE_PARENT`: its squash subject reads
#: `fix(12943): read a tenant's cache graph, never derive it from the credential (#13337) (#14068)`,
#: so `#13337` is the PR the commit came from and `#14068` the squash that landed it.
RESIDUE_REWRITE_PARENT_PRS = frozenset({"#14068", "#13337"})

#: How close a PR number must sit to a named sha to BE an attribution of it. Beyond this the
#: block is talking about a different tree (it legitimately discusses `#14108`, `#14101`,
#: `#14078` and `#13994` itself within a paragraph), so distance is the boundary between an
#: attribution and a neighbouring statement — the same windowed shape the arms above use.
_ATTRIBUTION_WINDOW = 45


#: A past-tense / measurement qualifier: what turns "the ref IS `d523c9be8c`" into
#: "the ref WAS `d523c9be8c` when this was measured".
_PAST_OR_MEASURED = re.compile(
    r"(?i)\b(?:was|were|resolved|measured|at\s+the\s+time|when\s+measured)\b")

#: A PRESENT-TENSE binding of a sha to something: `it is `sha``, `the ref pins `sha`` …
#: ⛔ `resolves to` / `resolved to` are NOT in this set: they are the dated form, and the
#: arm below requires a qualifier to be ABSENT before it charges one.
_MOVING_REF_TARGET = re.compile(
    r"(?i)\b(?:is|pins?|names?|equals?|points?\s+at|resolves?\s+to)\s+`[0-9a-f]{7,40}`")


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


#: The `#14058` note's ORIGINAL, refuted reason for citing a pre-rebase commit by issue number.
_PREFIX_REASON_RE = re.compile(
    r"(?i)reachable from no ref|disappears on the next\s*`?gc`?|unreproducible address")


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



def test_a_tool_only_diff_selects_this_suite():
    """A diff to `tools/sdk_privatise.py` ALONE must select the arms that grade its pin.

    ⛔ WITHOUT THIS THE PIN'S OWN GUARDS SLEEP ON THE ONLY DIFF THAT CAN BREAK THEM. `tools/`
    is a flat `NON_PYTHON_PREFIXES` entry in `tools/ci_selection.py`, and this tool has no
    `SOURCE_PATTERNS` bridge, so the changed-file filter answers `surfaces: []` — and a PR that
    edits ONLY `SWEEP_RESIDUE` or its `#:` record then runs none of the arms below. Measured
    before the `CORE_ALSO` row: `--changed-files tools/sdk_privatise.py` → `surfaces: []`,
    `full: false`, 106 test files, THIS FILE ABSENT. `--check` (the required `surface-guard`
    step) is not a substitute: it compares the `(rows, digest)` pair and never reads the prose.
    So the first diff to notice a bad re-cut was whichever `core`-selecting PR came next — on
    rows its author never wrote.

    The `CORE_ALSO` row is what fixes it, and both of its failure directions are graded here:
    the row is removed (surfaces collapse), and the row is made DECORATIVE by adding this file
    to `tier1` (the `core` pool is skipped for tier-1 files, so the arm would pass while the
    suite still never ran).

    Runs the selector's own CLI rather than re-deriving its rules, so it grades the wiring the
    CI job actually uses, and it is object-free (no `fetch-depth` dependence).
    """
    import json
    import subprocess as _sp
    import sys as _sys

    sel = ROOT / "tools" / "ci_selection.py"
    done = _sp.run([_sys.executable, str(sel), "--changed-files", "tools/sdk_privatise.py"],
                   cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, f"the selector failed on a one-file diff: {done.stderr[-800:]}"
    got = json.loads(done.stdout)
    surfaces = got.get("surfaces") or []
    files = got.get("test_files") or []

    assert "core" in surfaces, (
        "a diff to tools/sdk_privatise.py selects no `core` surface — the `CORE_ALSO` row in "
        f"tools/ci_selection.py is gone or shadowed; measured surfaces={surfaces}, full={got.get('full')}")
    assert not got.get("full"), (
        "the diff escalated to the FULL matrix — that costs the whole suite for a one-file change")
    assert "test_sdk_privatise_sweep.py" in files, (
        "a diff to tools/sdk_privatise.py no longer selects its own pins: "
        f"{len(files)} test files, this one absent — the selection gap #14062 describes")

    # ⛔ NON-VACUITY, FROM THE OTHER SIDE: if this file joined `tier1`, the `core` pool is skipped
    # for it and the selects above would pass while the suite still never ran on its own diff.
    _assert_not_tier1(_load_manifest_for_test())


def _load_manifest_for_test():
    """The real manifest, through the selector's own loader — never a re-typed copy."""
    import importlib.util as _iu
    import sys as _sys

    path = ROOT / "tools" / "ci_selection.py"
    spec = _iu.spec_from_file_location("_ci_selection_for_tier1_guard", path)
    assert spec is not None and spec.loader is not None
    mod = _iu.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.load_manifest()


def _assert_not_tier1(manifest):
    """`core` must not be skipped for this file, and the guard must not lose its subject."""
    assert "tier1" in manifest, (
        "not a defect of this file: the manifest no longer carries 'tier1', so this non-vacuity "
        "guard has lost its subject and would pass for the wrong reason")
    tier1 = manifest["tier1"] or []
    # `tier1:` holds bare FILENAMES, not `tests/`-prefixed paths — asserting the prefixed form
    # would test nothing at all.
    assert "test_sdk_privatise_sweep.py" not in tier1, (
        "this file joined 'tier1' — a tier-1 file is SKIPPED for the `core` pool, so the "
        "`CORE_ALSO` row above is now decorative: the selection asserts pass while the suite "
        "still never runs on a tools/sdk_privatise.py-only diff")
