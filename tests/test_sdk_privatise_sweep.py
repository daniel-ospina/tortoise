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


def _residue_block() -> str:
    """The `#:` comment block immediately above `SWEEP_RESIDUE`, as text."""
    lines = (ROOT / "tools" / "sdk_privatise.py").read_text(encoding="utf-8").splitlines()
    pin = next(i for i, line in enumerate(lines) if line.startswith("SWEEP_RESIDUE = ("))
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
#: ref 83e94826a8a`, and this block's own prose says so at `tools/sdk_privatise.py:378`).
#: So the printed remedy contradicted the sentence it exists to explain, and an operator
#: who followed it would believe the guard was re-armed while it stayed UNCHECKED — in a
#: PR whose thesis is falsifiability. A REFSPEC pulls the objects (`_rev_present` reads
#: the object DB, not a ref), and `refs/pull/14108/head` carries every interval commit
#: while `main` carries the rest.
_ARM_REFSPECS: dict[str, str] = {
    RESIDUE_ABBREV: "refs/pull/14108/head",
    RESIDUE_REWRITE_PARENT: "refs/pull/14108/head",
    RESIDUE_DELETING_COMMIT: "refs/pull/14108/head",
    RESIDUE_INTERVAL_END: "refs/pull/14108/head",
    RESIDUE_PR_HEAD: "refs/pull/14108/head",
    "origin/main": "main",
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


def test_the_residue_block_is_a_real_block_and_publishes_its_addresses():
    """Non-vacuity for the arms below: the block exists and carries its addresses."""
    block = _residue_block()
    assert block.count("\n") > 100, "the residue block is missing or truncated"
    for literal in (RESIDUE_ABBREV, RESIDUE_PR_HEAD, RESIDUE_REWRITE_PARENT):
        assert literal in block, f"{literal} is one of the block's published addresses"


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


def _moving_pointer_violations(block: str) -> list[str]:
    """Sentences that bind a sha to the PR head ref's CURRENT target in the PRESENT tense.

    ⛔ SAME RELATIVITY CLASS THE BLOCK FIXES FOR `main` (review, round 12, P3): the block
    scopes `e022fc935b` to "`origin/main` when this was written" and re-derives every figure
    it states about `main`, but its own `#14108` sentence said the ref "tracks the branch's
    CURRENT head, `d523c9be8c`" — a present-tense address for a pointer that moves on the
    next push to #14108, and the only arm that re-measures it runs solely where
    `refs/pull/14108/head` was fetched by hand (never in CI). A sha bound to a moving ref in
    the present tense rots into a false statement with nothing red.

    The shape graded: a sha within 40 characters after `CURRENT head` with no qualifier that
    marks it as a measurement. Word order and the surrounding words are free, so an honest
    rewrite (`… as measured`, `… it resolved to `d523c9be8c` when these figures were
    measured`) is accepted; what is refused is the unqualified present-tense binding.
    """
    flow = re.sub(r"\n#:\s?", " ", block)
    violations: list[str] = []
    for claim in re.finditer(r"(?i)current\s+head", flow):
        window = flow[claim.end():claim.end() + 40]
        if not re.search(r"`[0-9a-f]{7,40}`", window):
            continue  # no sha bound here at all — nothing to relativise
        if _PAST_OR_MEASURED.search(window):
            continue
        violations.append(flow[claim.start():claim.end() + 40])
    return violations


def test_the_pr_head_refs_target_is_a_measurement_never_a_present_tense_address():
    """Review round 12 (P3): `refs/pull/14108/head` moves; a sha beside it must be dated.

    ⛔ NON-VACUITY IN BOTH DIRECTIONS. Deleting the sentence is not the fix (a PR head ref
    tracking a branch's head is WHY the block names a sha at all), so the first assert
    requires the tracking claim to survive; the second refuses only the unqualified
    present-tense binding. `test_the_moving_pointer_arm_accepts_dated_forms` pins the other
    side: an honest rewrite must not red.
    """
    block = _residue_block()
    flow = re.sub(r"\n#:\s?", " ", block)
    assert re.search(r"(?i)tracks\b[^.]{0,80}\bhead", flow), (
        "non-vacuity: the block still says the PR head ref tracks a branch head — that is why "
        "it names a sha rather than the issue number")
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
    ]
    for text in dated:
        assert not _moving_pointer_violations(text), text
    undated = ("`#14108` resolves to a ref that tracks the branch's CURRENT head, "
               "`d523c9be8c`, and `83e94826a8a` is the OLDEST of the eight.")
    assert _moving_pointer_violations(undated), (
        "the PRE-FIX sentence must be refused — this arm is what makes the fix falsifiable")


def test_every_addressed_rev_names_a_fetchable_refspec():
    """Review round 12 (P2): every rev a skip names must be FETCHABLE, or the skip is a lie.

    `_rearm_command` indexes `_ARM_REFSPECS` directly, so an unmapped rev raises instead of
    printing an inert `git fetch origin <abbreviation>`. This arm keeps the map honest for
    the revs a skip can name today.
    """
    for rev in (RESIDUE_ABBREV, RESIDUE_REWRITE_PARENT, RESIDUE_DELETING_COMMIT,
                RESIDUE_INTERVAL_END, RESIDUE_PR_HEAD, "origin/main"):
        assert rev in _ARM_REFSPECS, f"{rev} has no fetchable address — a skip naming it is inert"
        assert not re.fullmatch(r"[0-9a-f]{7,39}", _ARM_REFSPECS[rev]), (
            f"{rev} is mapped to an ABBREVIATION, which git cannot `want`")


def test_the_skip_instruction_is_a_command_git_can_actually_run(monkeypatch):
    """Review round 12 (P2): the printed re-arm used to be `git fetch origin 83e94826a8a …`.

    Measured: `fatal: couldn't find remote ref 83e94826a8a` — git cannot `want` an
    abbreviation, and the block says so itself at `tools/sdk_privatise.py:378`. An operator
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
    assert "refs/pull/14108/head" in tokens, (
        f"the interval commits are carried by `refs/pull/14108/head`: {command.group(0)}")
