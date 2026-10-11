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
"""
from __future__ import annotations

import re
from pathlib import Path

from tortoise import capture_install, hook_install

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = (REPO_ROOT / "docs" / "plans"
        / "2026-08-25-1714-memory-capture-onboarding.md")
SEAM_README = REPO_ROOT / "tortoise" / "pi-hooks" / "README.md"

#: The two artifacts the record lives in — a lane reads one or the other.
RECORDS = (PLAN, SEAM_README)


def _text(path: Path) -> str:
    assert path.is_file(), f"the record is missing: {path}"
    return path.read_text(encoding="utf-8")


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
