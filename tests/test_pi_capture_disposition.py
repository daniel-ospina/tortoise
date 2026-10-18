"""#3512 — the Pi capture mechanism's recorded home, pinned to the tree.

The disposition of the Pi capture mechanism is recorded in two artifacts other
lanes read and act on:

- ``docs/plans/2026-08-25-1714-memory-capture-onboarding.md`` — the epic #1714
  plan, §2 / §4 / §7 / §8, where the home and the agent-infra deliverable are
  routed;
- ``tortoise/pi-hooks/README.md`` — the canonical statement of this seam's
  install and migration status.

**The defect these tests hold (measured 2026-10-11).** The plan named
``tortoise/pi-extension/`` as the product-owned home while the artifact that
actually ships lives at ``tortoise/pi-hooks/`` — ``pi-extension/`` never
existed. A lane following the record would have scaffolded a second tree beside
the shipped one, and a lane reading issue #3512 as "not started" had a
coordinated reason to: its own goal text named a path with nothing in it. A
record that names a path is a claim about the tree, and a claim about the tree
is checkable, so it is checked here instead of trusted.

Three of these assertions are also what keeps the *ownership split* from
silently reverting: the consolidation of agent-infra's two recorder bodies is
#3515's, and the cross-repo reduction (with its PR ordering) is
``daniel-ospina/agent-infra#1671``. If the record stops naming either, the next
lane re-derives the cycle the #3512 scoping comment spent a section resolving.

Scope note: most ``docs/`` edits skip the Python matrix (``docs/`` is in
``ci_selection.NON_PYTHON_PREFIXES``), but this record's plan doc is itself a
``CORE_ALSO`` claim entry (the #3554 carve-out in ``tools/ci_selection.py``), so a
plan-only edit selects ``core`` and runs this file. The record's code-adjacent
half — ``tortoise/pi-hooks/README.md`` — matches no ``SOURCE_PATTERNS`` entry
and therefore selects ``core`` through the ``tortoise/`` fallback, which is why
this file is registered there.

**The second defect these tests hold (re-measured 2026-10-17).** The record's
account of the *second* producer went stale under it: agent-infra
``7e77470e4`` (2026-10-16, #803/#848) moved ``reflect-hook.ts`` onto a shared
``extensions/shared/capture-gate.ts`` that requires an explicit ``cloud: true``
in the operator config *and* a key, so the "ungated producer" the record (and
the shipped artifact's header) named no longer exists — and the recount matters
beyond prose: ``capture-gate.ts`` is a **second** shared module both recorders
import, so the move inventory that named only ``capture-attribution.ts`` would
have a reducer half-move the pair and leave both recorders importing a module
that is no longer beside them. A record is only worth pinning if it is the
record a lane can act on, so the stale claims are pinned out and the complete
inventory is pinned in.

**The same defect was then found in the PR's own note (2026-10-17).** The new
current-state note said agent-infra "still **CI-tests**" both recorders while
the table it points at ("table above") records that
``tortoise-capture/index.test.ts`` is executed by **no** workflow, and the
"complete inventory" omitted the two shared modules' **own suites** — which that
same table's CI row says are executed by the ``extensions/shared/*.test.ts``
glob. Both are pinned here, and the note's assertions are scoped to the NOTE
block rather than to the whole file: a whole-file substring check cannot tell
"the note says X" from "X appears somewhere", and the pre-fix README already
contained both ``1671`` and the per-recorder CI row.
"""
from __future__ import annotations

import re
from pathlib import Path

from tortoise import capture_install, hook_install

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = (REPO_ROOT / "docs" / "plans"
        / "2026-08-25-1714-memory-capture-onboarding.md")
SEAM_README = REPO_ROOT / "tortoise" / "pi-hooks" / "README.md"

#: The shipped extension — the record's own claims about it live in its header.
SEAM_ARTIFACT = REPO_ROOT / "tortoise" / "pi-hooks" / "tortoise-capture.ts"

#: The two artifacts the record lives in — a lane reads one or the other.
RECORDS = (PLAN, SEAM_README)


def _text(path: Path) -> str:
    assert path.is_file(), f"the record is missing: {path}"
    return path.read_text(encoding="utf-8")


def _prose(path: Path) -> str:
    """Record text with line markers and whitespace collapsed into one stream.

    The stale claims these tests pin out are **line-wrapped** in the README and
    split by ``//`` comment markers in the extension's header, so a raw
    substring check over either record is vacuous — verified against the
    pre-fix records from ``git show origin/main:…`` before these assertions were
    written: every assertion that pins a **new** fact fails there and passes on
    the corrected text. The qualifier is load-bearing — two clauses of the
    inventory guard (``capture-attribution.ts`` is named, and the cross-repo
    owner is present) hold on the pre-fix record too, because they pin facts
    that were already true; the **new** facts this file adds are
    ``capture-gate.ts``, "both shared modules", "TARGET" and "no copy is a
    shim", and those are the ones that red on the pre-fix text.
    """
    text = _text(path)
    text = re.sub(r"(?m)^\s*(?://|#)\s?", "", text)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text)


#: How the README's measured current-state blockquote opens. It is a blockquote,
#: so it is delimited by ``>`` — which is what makes a NOTE-scoped assertion
#: possible: a whole-file substring check cannot tell "the note says X" from "X
#: appears somewhere in the file", and a note can lose its content while the
#: file still contains every word of it elsewhere (measured: the pre-#3512
#: README already contained ``1671`` and the CI row).
_NOTE_PREFIX = "> **Current state:"


def _note_block(path: Path) -> str:
    """The README's current-state blockquote alone, marker- and whitespace-collapsed."""
    lines = _text(path).splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.startswith(_NOTE_PREFIX)), None)
    assert start is not None, (
        f"{path.name} has no current-state note — the measured TARGET-state "
        "blockquote is where a lane reads whether the reduction is done")
    block: list[str] = []
    for line in lines[start:]:
        if not line.startswith(">"):
            break
        block.append(line)
    text = " ".join(block)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text)


def test_the_recorded_home_is_the_artifact_the_installer_installs():
    """The record must name the home the INSTALLER resolves, not a lookalike.

    The artifact path is taken from the code that installs it
    (``hook_install.ARTIFACT_CONTRACTS['pi'].source``), never re-typed here: a
    test that re-typed it would agree with a wrong record as easily as with a
    right one.
    """
    source = hook_install.ARTIFACT_CONTRACTS["pi"].source
    assert source.is_file(), f"the shipped Pi seam is missing: {source}"
    relpath = source.relative_to(REPO_ROOT)
    assert relpath == Path("tortoise/pi-hooks/tortoise-capture.ts"), relpath
    # The HOME (the directory), which is what the record claims — the file name
    # itself is already pinned by tests/test_pi_capture_hooks.py.
    home = relpath.parent.as_posix() + "/"
    for record in RECORDS:
        assert home in _text(record), (
            f"{record.relative_to(REPO_ROOT)} does not name the recorded home "
            f"{home!r} — the artifact the installer writes lives there")


def test_no_record_names_a_product_home_that_does_not_exist():
    """Any ``tortoise/pi-<name>/`` a record names must be a real directory.

    This is the assertion that would have caught ``tortoise/pi-extension/``
    (see the module docstring). A historical mention is written without the
    repo prefix (``pi-extension/``) on purpose: the rule is that a path with a
    repo prefix in it is a claim about this tree.
    """
    named = set()
    for record in RECORDS:
        named.update(re.findall(r"\btortoise/pi-[a-z0-9-]+/", _text(record)))
    assert named, "no Pi home is named anywhere — the record is unreadable"
    missing = sorted(p for p in named if not (REPO_ROOT / p.rstrip("/")).is_dir())
    assert not missing, (
        f"the record names a product home that does not exist: {missing} — "
        "a lane following it would scaffold a second tree beside the shipped one")


def test_the_disposition_names_both_measured_producers():
    """The record must name BOTH legacy producers, not only the guarded one.

    The installer's guard covers the legacy ``tortoise-capture/`` **directory**
    (``capture_install.LEGACY_PI_DIRNAME``). The second producer is a top-level
    ``reflect-hook.ts`` — measured to be a real file on
    ``agent-infra@origin/main``, not the symlink #3512's body once claimed —
    which the guard does not touch. Naming only the guarded one is how the
    "one recorder, one home" claim stayed readable while two producers were
    live on a farm machine.
    """
    readme = _text(SEAM_README)
    assert capture_install.LEGACY_PI_DIRNAME in readme
    assert "reflect-hook.ts" in readme, (
        "the record must name the second producer, the one the install guard "
        "does NOT disable")
    assert "shim" in readme, (
        "the record must state the disposition: the agent-infra copy is kept "
        "as a shim, not deleted yet")


def test_the_cross_repo_owner_is_named():
    """The reduction lives in ANOTHER repo, so the record must route it.

    Without the issue number, the deliverable has no owner a lane can act on —
    the state this issue sat in while two lanes read it as blocked. The pattern
    is the repo-qualified form, so a bare ``#NNNN`` (this repo's numbering)
    cannot satisfy it.
    """
    pattern = re.compile(r"daniel-ospina/agent-infra#\d+")
    for record in RECORDS:
        assert pattern.search(_text(record)), (
            f"{record.relative_to(REPO_ROOT)} does not name the cross-repo "
            "owner as daniel-ospina/agent-infra#NNNN")


def test_the_consolidation_is_owned_by_3515_not_here():
    """#3512 owns the placement + the agent-infra shim; #3515 owns the merge.

    The two issues named each other's work as their own prerequisite (#3512
    "the two recorders consolidated into one", #3515 "Depends on #3512 (the
    recorder is consolidated ... first)"), which is a cycle: the consolidation
    was the prerequisite of the issue that contains it. #3512's scoping comment
    resolves it — the hybrid defines the single recorder's shape, so the merge
    is #3515's. This pins the resolution where a lane looks for it.
    """
    plan = _text(PLAN)
    assert "#3515" in plan, "the plan must name the consolidation's owner"
    # The routing sentence must survive: it is the resolution, not a label.
    assert re.search(r"consolidat\w*[^.]{0,200}#3515", plan, re.IGNORECASE), (
        "the plan no longer routes the recorder consolidation to #3515")


def test_the_second_producers_gate_is_recorded_as_it_now_stands():
    """The record must not describe the second producer's gate as it was pre-#803.

    Re-measured on ``agent-infra@origin/main`` 2026-10-17: both recorders import
    ``shared/capture-gate.js``, and the egress opt-in is read from the operator
    FILE only (``cloud = file.cloud === true``) — a key alone uploads nothing.
    That gate landed in agent-infra ``7e77470e4`` (2026-10-16, #803/#848).

    The record said the opposite ("credential presence alone", "the other
    ungated producer"), which is the exact failure this issue exists to stop:
    a lane acts on a recorded claim instead of re-measuring, and here it would
    have re-derived a divergence that is already closed at source. The stale
    phrasings are pinned out of both the README *and* the shipped artifact's
    header — and the gate change is required to carry its commit, so "it is
    gated now" can never be asserted without evidence a lane can check.
    """
    for record in (SEAM_README, SEAM_ARTIFACT):
        text = _prose(record)
        rel = record.relative_to(REPO_ROOT)
        assert "capture-gate" in text, (
            f"{rel} does not name the shared egress gate both agent-infra "
            "recorders now use (extensions/shared/capture-gate.ts, #803)")
        for stale in ("credential presence alone", "the other ungated producer"):
            assert stale not in text, (
                f"{rel} still describes the pre-#803 gate ({stale!r}): "
                "reflect-hook requires an explicit `cloud: true` since "
                "agent-infra 7e77470e4 (2026-10-16), so the divergence this "
                "issue's evidence cited is closed at source")
    assert "7e77470e4" in _text(SEAM_README), (
        "a gate change recorded without the commit that made it is not "
        "evidence — name agent-infra 7e77470e4 (2026-10-16, #803)")


def test_the_move_inventory_names_every_shared_capture_module():
    """BOTH shared capture modules move — moving one breaks BOTH recorders.

    ``capture-attribution.ts`` was the pair's only shared module until #803
    added ``capture-gate.ts`` (agent-infra ``7e77470e4``, 2026-10-16) and both
    recorders began importing it. The record named only the first, so a reducer
    following it would move ``capture-attribution`` and leave ``capture-gate``
    behind — leaving both recorders importing a module that is no longer beside
    them, i.e. capture installed but unwired. The record must therefore state the
    pair as **two** modules, not one: "both shared modules" is pinned in, so the
    inventory cannot silently shrink back to a single entry.
    """
    readme = _text(SEAM_README)
    for required in ("capture-attribution.ts", "capture-gate.ts"):
        assert required in readme, (
            f"the move inventory does not name {required} — the two recorders "
            "import BOTH shared modules (measured 2026-10-17)")
    assert re.search(r"both shared modules", _prose(SEAM_README), re.IGNORECASE), (
        "the record must state that the pair shares TWO modules: a single-module "
        "inventory is what half-moves them (#803 added capture-gate.ts)")


def test_the_move_inventory_names_the_shared_suites_too():
    """The inventory is "complete", so it must include the modules' own tests.

    Measured on ``agent-infra@origin/main`` 2026-10-17:
    ``extensions/shared/capture-gate.test.ts`` (20,873 B) and
    ``extensions/shared/capture-attribution.test.ts`` (18,073 B) both import the
    module they sit beside, and agent-infra's ``ci-main.yml`` runs whatever the
    ``extensions/shared/*.test.ts`` glob matches. A reducer following a
    "complete inventory" that named only the two ``.ts`` modules would move them
    and leave both suites importing a file that is no longer there — agent-infra
    CI red, the test-half twin of the "installed but unwired" window the PR
    ordering exists to prevent. The recorders' own suites are a different move
    (they belong with the recorder bodies, #3515), so the record must say which
    is which.
    """
    readme = _text(SEAM_README)
    prose = _prose(SEAM_README)
    for suite in ("capture-gate.test.ts", "capture-attribution.test.ts"):
        assert suite in readme, (
            f"the move inventory omits {suite} — the two shared modules' own "
            "suites are CI-executed by the same glob, so a modules-only move "
            "leaves agent-infra CI red on a missing import")
    assert re.search(r"recorder'?s own suite", prose, re.IGNORECASE), (
        "the record must say the RECORDERS' suites are a separate move (with "
        "their bodies, #3515) — otherwise a reducer takes all four suites with "
        "the shared pair and splits each recorder from its own tests")


def test_the_shim_is_recorded_as_target_state_not_current_state():
    """The shim is an end state; the repo today is two real files.

    The disposition test above requires the word "shim", which passes just as
    well when the record claims the shim as ACHIEVED. Measured 2026-10-17 both
    copies are real files in agent-infra, so a record that reads as done lets a
    lane skip the reduction (`daniel-ospina/agent-infra#1671`, still open) — the
    same "reads as settled, is not" shape as the dead
    ``tortoise/pi-extension/`` home.

    Every assertion here is scoped to the NOTE, not to the file: the pre-#3512
    README already contained ``1671`` (the cross-repo deliverable paragraph) and
    the per-recorder CI row, so a whole-file check would stay green while the
    note lost its content — measured against ``git show origin/main:…``.
    """
    note = _note_block(SEAM_README)
    assert "TARGET" in note, (
        "the note must mark the shim as TARGET state, not as current state")
    assert re.search(r"(no copy is a shim|not a shim yet)", note), (
        "the note must state the measured current state: both copies are real "
        "files in agent-infra, so the shim is a target")
    assert "1671" in note, (
        "the current-state note must name the owner that does the reduction")


def test_the_current_state_note_does_not_claim_ci_coverage_it_denies():
    """A note that tidies away its own table is the defect this PR corrects.

    The note used to say "both recorders are real files that agent-infra still
    **CI-tests**" while the table four lines above it — cited by the note as
    "(table above)" — records that ``tortoise-capture/index.test.ts`` is
    executed by **no** agent-infra workflow (the extension glob is
    ``extensions/*/test*.mjs``, which does not match a ``.ts`` file). That is
    the same class as the stale gate claim: a lane that trusts the prose instead
    of the measurement. "agent-infra still CI-tests both recorders" is also the
    argument for keeping both alive, which makes the clause load-bearing for the
    disposition the note defends.

    Mutation: restore the claim "both recorders are real files that agent-infra
    still CI-tests" — this REDs on the denier, and a note that drops the
    per-recorder split REDs on both the ``index.test.ts`` and the "no agent-infra
    workflow" clauses.
    """
    note = _note_block(SEAM_README)
    assert "still CI-tests" not in note, (
        "the note claims agent-infra still CI-tests BOTH recorders — the table "
        "it points at says `tortoise-capture/index.test.ts` runs in no workflow")
    assert "tortoise-capture/index.test.ts" in note, (
        "the note must carry the measured gap by name, not a rounded claim")
    assert re.search(r"no agent-infra workflow", note), (
        "the note must state that the second recorder's suite runs in NO "
        "agent-infra workflow — that gap is part of what #1671 must fix")
    assert "reflect-hook.test.ts" in note, (
        "the split is only honest if the CI-covered recorder is named too")
