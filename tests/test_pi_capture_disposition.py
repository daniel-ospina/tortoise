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

Scope note: ``docs/`` is in ``ci_selection.NON_PYTHON_PREFIXES``, so a
docs-only PR deliberately skips the Python matrix. The record's code-adjacent
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
    pre-fix records from ``git show HEAD:…`` before these assertions were
    written: every one of them fails there and passes on the corrected text.
    """
    text = _text(path)
    text = re.sub(r"(?m)^\s*(?://|#)\s?", "", text)
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


def test_the_shim_is_recorded_as_target_state_not_current_state():
    """The shim is an end state; the repo today is two real files.

    The disposition test above requires the word "shim", which passes just as
    well when the record claims the shim as ACHIEVED. Measured 2026-10-17 both
    copies are real, CI-tested files in agent-infra, so a record that reads as
    done lets a lane skip the reduction (`daniel-ospina/agent-infra#1671`, still
    open) — the same "reads as settled, is not" shape as the dead
    ``tortoise/pi-extension/`` home.
    """
    readme = _text(SEAM_README)
    assert "TARGET" in readme, (
        "the record must mark the shim as TARGET state, not as current state")
    assert re.search(r"(no copy is a shim|not a shim yet)", readme), (
        "the record must state the measured current state: both copies are real "
        "files in agent-infra, so the shim is a target")
    assert "1671" in readme, (
        "the current-state note must name the owner that does the reduction")
