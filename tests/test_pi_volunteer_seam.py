"""#2119 — the Pi per-turn READ seam (epic #2080 end-state platform).

Pi is the one harness in the seam family whose hook surface is an EXTENSION
rather than a shell command, and until this issue it had only the capture half
(`tortoise/pi-hooks/tortoise-capture.ts`, #3575). The read half is
`tortoise/pi-hooks/tortoise-volunteer.ts`: it registers Pi's per-turn
`before_agent_start` hook and returns the documented
`{message: {customType, content, display}}` result carrying the shared reflex's
why-context block.

The re-measurement this issue turns on, since the matrix row that said the seam
was unreachable is what kept it unbuilt:

* the installed pi-coding-agent documents `before_agent_start` as "fired after
  user submits prompt, before agent loop — can inject a message and/or modify
  the system prompt" (`docs/extensions.md`), typed in
  `dist/core/extensions/types.d.ts` as `BeforeAgentStartUserEvent`
  (`messageType: "user"` + `prompt`) → `BeforeAgentStartEventResult`
  (`message` / `systemPrompt`);
* `docs/research/2026-09-01-gbrain-learnings/platform-seams.md` claimed the
  opposite ("no per-turn prompt hook in the extension API today"). The row is
  corrected by this change; this file is the evidence it was wrong.

What is graded here, in order of how hard it is to fake:

1. the INSTALL — `install_capture("pi")` writes BOTH extension files, byte for
   byte from the shipped sources, idempotently, and refuses a foreign file at
   the read seam's path (never a silent replace of another extension);
2. the DRIFT verdict — an installed read seam that is stale reads as
   `stale-artifact`, while its ABSENCE is not a finding at all (a capture-only
   install never opted into injection, so grading absence would false-block a
   healthy machine);
3. `--uninstall` — removes the READ half only, and says what it left behind;
4. THE REAL SEAM END TO END — the INSTALLED extension, loaded by node through
   Pi's own handler contract, against the REAL `tortoise volunteer` reflex over
   a SEEDED embedded graph: the injected message carries the seeded claim's
   label. This is the anti-gaming grade the issue asks for ("grade the real
   seam, not a sim");
5. the extension's own `node --test` suite.

Self-contained embedded lane: `TORTOISE_DB_URI` is cleared and the graph is a
per-test temp db.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "tortoise" / "pi-hooks"
CAPTURE_EXTENSION = HOOKS / "tortoise-capture.ts"
VOLUNTEER_EXTENSION = HOOKS / "tortoise-volunteer.ts"
VOLUNTEER_TEST = HOOKS / "tortoise-volunteer.test.ts"

_PY = sys.executable

#: The same seeded graph `tests/test_platform_seams.py` uses: the reflex only
#: surfaces EP-MEASURED points (a neutral Beta(1,1) prior sits below the 0.7
#: gate), so the evidence→claim IMPL edge and the posteriors are wired
#: directly. Copied deliberately rather than imported — a shared fixture would
#: make one file's seeding change silently re-baseline the other's assertions.
_SEED = """
import os
from tortoise.sdk import TortoiseSDK
sdk = TortoiseSDK(db_path=os.environ["TORTOISE_DB_PATH"])
proj = sdk._get_proj()
ev = sdk.create_point(
    "evidence",
    "CI deploys on main merge via GitHub Actions [supporting record]")
claim = sdk.create_point(
    "statement",
    "The CI pipeline deploys via GitHub Actions on main merge.")
sdk.create_operator("IMPL", ev["id"], [claim["id"]])
for pid, a, b in ((claim["id"], 12.0, 1.0), (ev["id"], 12.0, 1.0)):
    m = round(a / (a + b), 4)
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
        "n.posterior_alpha=$a, n.posterior_beta=$b",
        params={"id": pid, "a": a, "b": b, "c": m})
sdk.close()
"""

_SEEDED_CLAIM = "CI pipeline deploys via GitHub Actions"


# ── env plumbing ─────────────────────────────────────────────────────────


def _base_env(home: Path) -> dict:
    # ⛔ The seam's OWN switches are BLANKED here (#11584 review).
    # `{**os.environ}` copies them into the dict `_run_driver` hands to node,
    # where the installed artifact reads them — so an operator or CI runner with
    # `TORTOISE_VOLUNTEER_DISPLAY=1` exported (a switch the seam documents)
    # REDS the W4 "silent by default" row: a verdict about the HOST, not the
    # artifact, and indistinguishable from a real regression. Measured at the
    # head that introduced the seam:
    #
    #   $ TORTOISE_VOLUNTEER_DISPLAY=1 pytest tests/test_pi_volunteer_seam.py \
    #         -q -k installed_extension_injects_the_real_reflex_block
    #   E  AssertionError: silent by default (W4 posture)
    #   E  assert True is False
    #
    # ⛔ THE MEASURED BLAST RADIUS IS EXACTLY ONE SWITCH. Every clause below is
    # a measurement, and a clause that cannot be measured does not belong here:
    # this paragraph is evidence other lanes read, so an unmeasured claim about
    # itself is a defect in its own right.
    # `TORTOISE_VOLUNTEER_DISABLE`
    # and `TORTOISE_COMMAND` were already clean BEFORE this fix — the fixture's
    # own `monkeypatch.delenv` loop below removes them from `os.environ` during
    # setup, and `_run_driver` builds its env at ROW time, so they never reached
    # the subprocess; measured at the parent head they pass. `TORTOISE_SRC_DIR`,
    # `TORTOISE_VOLUNTEER_DISPLAY` and `TORTOISE_VOLUNTEER_DEBUG` were neither
    # deleted nor blanked, so all three DID reach the subprocess — and only
    # `TORTOISE_VOLUNTEER_DISPLAY` changes the assertion (the other two measured
    # inert: `TORTOISE_SRC_DIR` reaches the seam but `pathLookup` wins the
    # candidate order first). Three switches leaked, one reds a row.
    #
    # They are blanked anyway because the fixture's `delenv` list is a
    # hand-maintained enumeration that does not name them: extending it would
    # leave the next switch one forgotten edit from the same false red, whereas
    # blanking here holds for every switch the seam reads. This is the same root
    # the sibling `hermetic()` helper names verbatim in
    # `tortoise-volunteer.test.ts` ("a Pi user who followed the SHIPPED README
    # reds the silent-default row"), closed one layer over. Empty is falsy for
    # both `truthy()` and the `||`/`??` chains, so the SHIPPED default stays
    # the path under test rather than being skipped.
    #
    # `VIRTUAL_ENV` is deliberately NOT blanked: `_run_driver` supplies the
    # interpreter on `PATH`, and `VIRTUAL_ENV` is a legitimate candidate in the
    # seam's module-dir resolution. Measured inert here (`PATH`'s hit wins the
    # candidate order), but blanking it would test a different path than a real
    # install takes.
    return {
        **os.environ,
        "TORTOISE_DB_PATH": str(home.parent / "seams.db"),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        "HOME": str(home),
        # The reflex resolves its identity from the user-global credentials
        # file; a real hosted key on the runner would make these local-graph
        # assertions vacuous. Nothing here is ever transmitted.
        "TORTOISE_API_KEY": "",
        "TORTOISE_API_URL": "",
        # The seam's own switches — see this function's header.
        "TORTOISE_VOLUNTEER_DISPLAY": "",
        "TORTOISE_VOLUNTEER_DEBUG": "",
        "TORTOISE_VOLUNTEER_DISABLE": "",
        "TORTOISE_COMMAND": "",
        "TORTOISE_SRC_DIR": "",
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME with a seeded embedded graph beside it."""
    h = tmp_path / "home"
    h.mkdir()
    for key in ("TORTOISE_API_KEY", "TORTOISE_API_URL",
                "TORTOISE_VOLUNTEER_DISABLE", "TORTOISE_COMMAND"):
        monkeypatch.delenv(key, raising=False)
    # AFTER the `delenv` loop on purpose. Ordering hygiene, not a measured bug:
    # every `env` this fixture builds feeds the GRAPH-SEED subprocess below, and
    # the seed script reads none of these keys — so nothing here could have
    # red a row either way (measured; do not claim otherwise). It is kept so the
    # seed subprocess env cannot carry an operator switch if that script ever
    # starts reading one.
    env = _base_env(h)
    # `PATH.home()` is what the INSTALLER and the `--uninstall` verb resolve
    # their root from, so the fixture must move it too or `_uninstall_pi_volunteer`
    # would act on the developer's real `~/.pi`.
    monkeypatch.setenv("HOME", str(h))
    subprocess.run([_PY, "-c", _SEED], env=env, check=True,
                   capture_output=True, timeout=180)
    return h


def _install(home: Path):
    from tortoise.capture_install import install_capture

    return install_capture("pi", home=home)


def _ext_dir(home: Path) -> Path:
    return home / ".pi" / "agent" / "extensions"


# ── 1. the install ───────────────────────────────────────────────────────


def test_install_writes_both_artifacts_byte_for_byte(home):
    result = _install(home)
    assert result.ok, result.error
    assert result.changed is True
    installed = _ext_dir(home)
    assert (installed / CAPTURE_EXTENSION.name).read_bytes() == CAPTURE_EXTENSION.read_bytes()
    assert (installed / VOLUNTEER_EXTENSION.name).read_bytes() == VOLUNTEER_EXTENSION.read_bytes()
    # The read seam's action is reported SEPARATELY: an install that silently
    # gained an injection seam would be an exposure change nobody asked for.
    assert any(VOLUNTEER_EXTENSION.name in action for action in result.actions)
    assert any("read seam" in action for action in result.actions)

    # Idempotent: a re-run (the upgrade path) writes nothing and says so.
    again = _install(home)
    assert again.ok and again.changed is False
    assert again.actions == ()


def test_a_dry_run_writes_neither_artifact(home):
    from tortoise.capture_install import install_capture

    result = install_capture("pi", home=home, dry_run=True)
    assert result.ok, result.error
    assert not _ext_dir(home).exists()


def test_a_foreign_file_at_the_read_seam_is_refused_not_replaced(home):
    installed = _ext_dir(home)
    installed.mkdir(parents=True)
    foreign = installed / VOLUNTEER_EXTENSION.name
    foreign.write_text("// somebody else's extension\nexport default () => {};\n")

    result = _install(home)
    assert result.ok is False
    assert VOLUNTEER_EXTENSION.name in (result.error or "")
    assert foreign.read_text().startswith("// somebody else's extension"), (
        "a file this installer cannot prove it wrote must never be replaced")
    # …and the CAPTURE half did land first (reported, not hidden) — the error
    # names the read seam without pretending the whole install was a no-op.
    assert (installed / CAPTURE_EXTENSION.name).is_file()


def test_a_stale_read_seam_is_reported_and_its_absence_is_not(home):
    from tortoise import hook_install

    result = _install(home)
    assert result.ok, result.error
    root = _ext_dir(home)
    assert hook_install.detect_artifact_install(root, "pi") == []

    # Absence is NOT drift: a capture-only install never opted into injection.
    (root / VOLUNTEER_EXTENSION.name).unlink()
    assert hook_install.detect_artifact_install(root, "pi") == []

    # Present but OLD is the real defect — a read seam that injects with older
    # logic is indistinguishable from an empty graph at the agent turn.
    stale = root / VOLUNTEER_EXTENSION.name
    stale.write_text("// tortoise-hook-version: 0\n// old read seam\n")
    findings = hook_install.detect_artifact_install(root, "pi")
    kinds = {f.kind for f in findings}
    assert "stale-artifact" in kinds
    detail = " ".join(f.detail for f in findings)
    assert VOLUNTEER_EXTENSION.name in detail
    assert "tortoise install pi" in detail

    # …and a present-but-edited copy (same marker, different bytes) is named too.
    stale.write_text(VOLUNTEER_EXTENSION.read_text() + "\n// local edit\n")
    findings = hook_install.detect_artifact_install(root, "pi")
    assert "modified-artifact" in {f.kind for f in findings}

    # ⛔ A markerless file that is NOT ours must read as FOREIGN, not as an old
    # copy of ours (review finding 4). The distinction is load-bearing: the
    # installer and `--uninstall` both REFUSE a file they cannot prove they
    # wrote, so "reinstall with `tortoise install pi`" — the `unversioned`
    # wording — is advice that fails. `foreign-artifact` is also in
    # `MANUAL_FIX_KINDS`, so callers tell the user to move it aside instead.
    stale.write_text("// somebody else's extension\nexport default () => {};\n")
    findings = hook_install.detect_artifact_install(root, "pi")
    assert "foreign-artifact" in {f.kind for f in findings}
    foreign = next(f for f in findings if f.kind == "foreign-artifact")
    assert "move it aside" in foreign.detail
    assert hook_install.is_manual_fix("foreign-artifact")

    # …while a markerless copy that IS ours is the repairable class.
    shipped = VOLUNTEER_EXTENSION.read_text()
    # The marker is DERIVED from the shipped artifact, never spelled: after a
    # generation bump (#11662 moved the pair to 19) a hard-coded ``18`` strips
    # nothing, the copy keeps its marker, and this row turns into a
    # `modified-artifact` assertion that can pass for the wrong reason.
    marker = ("// tortoise-hook-version: "
              f"{hook_install.read_hook_version(VOLUNTEER_EXTENSION)}\n")
    assert marker in shipped, marker
    stale.write_text(shipped.replace(marker, "", 1))
    findings = hook_install.detect_artifact_install(root, "pi")
    assert "unversioned-artifact" in {f.kind for f in findings}


def test_an_unreadable_read_seam_is_not_readable_not_foreign(home):
    """#11584 review, round 2: the two structural checks the optional half
    dropped are the primary's, and neither needs ownership evidence.

    An UNREADABLE seam used to fall through to the markerless branch, where
    `read_hook_version` and `_looks_like_our_script` both swallow the `OSError`,
    so a good Tortoise seam that merely lost its read bit graded
    `foreign-artifact` — whose advice is "move it aside" — instead of the
    primary's `not-readable` ("chmod it"). That is advice the user acts on.
    """
    from tortoise import hook_install

    # #11584 review: `0o000` is not unreadable to uid 0 — `access(2)` on a
    # privileged process bypasses the mode bits, so the detector's own
    # `os.access(installed, os.R_OK)` gate answers True, the file grades clean,
    # and this row reds on a HEALTHY tree. The same guard (twelve files, the
    # sibling `test_capture_install.py` rows included) is the repo's precedent.
    if os.geteuid() == 0:  # pragma: no cover - CI runs unprivileged
        pytest.skip("root ignores file mode bits, so the read would succeed")

    result = _install(home)
    assert result.ok, result.error
    root = _ext_dir(home)
    seam = root / VOLUNTEER_EXTENSION.name
    seam.chmod(0o000)
    try:
        findings = hook_install.detect_artifact_install(root, "pi")
        kinds = {f.kind for f in findings}
        assert "not-readable" in kinds, (
            f"an unreadable read seam must grade `not-readable`, got {sorted(kinds)}")
        assert "foreign-artifact" not in kinds, (
            "an unreadable file is not evidence that it is somebody else's")
        detail = " ".join(f.detail for f in findings)
        assert "move it aside" not in detail
        assert "chmod" in detail
    finally:
        seam.chmod(0o644)


def test_a_symlinked_read_seam_is_named_not_silent(home, tmp_path):
    """The installer REFUSES a symlink that escapes the install home
    (`_symlink_escape`), so a grader that reports CLEAN for one tells the user
    to run a command that refuses — the #5351 "advice the installer
    contradicts" class the primary artifact's own comment spells out."""
    from tortoise import hook_install

    result = _install(home)
    assert result.ok, result.error
    root = _ext_dir(home)
    seam = root / VOLUNTEER_EXTENSION.name
    outside = tmp_path / "copy-of-the-seam.ts"
    outside.write_bytes(seam.read_bytes())
    seam.unlink()
    seam.symlink_to(outside)

    findings = hook_install.detect_artifact_install(root, "pi")
    kinds = {f.kind for f in findings}
    assert "symlinked-artifact" in kinds, (
        f"a symlinked read seam must be NAMED, got {sorted(kinds)} (silence here is "
        "a false CLEAN over a file `install`/`upgrade` refuses)")
    link = next(f for f in findings if f.kind == "symlinked-artifact")
    assert link.blocking is False, "a symlink is a note, not a block"

    # A BROKEN symlink is its own shape and gets the primary's remedy wording,
    # not `not-a-regular-file` ("is not a regular file") — wrong advice.
    seam.unlink()
    seam.symlink_to(tmp_path / "gone.ts")
    findings = hook_install.detect_artifact_install(root, "pi")
    broken = [f for f in findings if f.kind == "symlinked-artifact"]
    assert broken, f"a broken symlink must be named, got {[f.kind for f in findings]}"
    assert "broken symlink" in broken[0].detail
    assert "not-a-regular-file" not in {f.kind for f in findings}


def test_the_two_artifacts_declare_one_contract_number():
    """`contract_version_for("pi")` answers only when BOTH shipped artifacts
    agree — the layout half's "one contract, one number" invariant (#4680)."""
    from tortoise import hook_install

    contract = hook_install.ARTIFACT_CONTRACTS["pi"]
    assert contract.volunteer_source == VOLUNTEER_EXTENSION
    shipped = hook_install.contract_version_for("pi")
    assert shipped is not None, (
        "the Pi seam's two shipped artifacts must declare the same generation")
    assert hook_install.read_hook_version(CAPTURE_EXTENSION) == shipped
    assert hook_install.read_hook_version(VOLUNTEER_EXTENSION) == shipped


def test_uninstall_removes_the_read_half_only(home, capsys):
    from tortoise.__main__ import _uninstall_pi_volunteer

    assert _install(home).ok
    rc = _uninstall_pi_volunteer(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert not (_ext_dir(home) / VOLUNTEER_EXTENSION.name).exists()
    assert (_ext_dir(home) / CAPTURE_EXTENSION.name).is_file(), (
        "`--uninstall` is scoped to the READ seam — capture is never removed")
    assert "removed" in out and "capture" in out

    # A second --uninstall is a clean no-op.
    assert _uninstall_pi_volunteer(None) == 0
    assert "not installed" in capsys.readouterr().out


def test_a_repair_never_turns_on_the_read_seam(home):
    """The repair verb REFRESHES the seam; it never INTRODUCES it (#2119 W4).

    ``hooks status`` tells an operator to run ``hooks upgrade`` to repair a
    capture install, and that command routes through ``install_capture`` — the
    same entry point ``tortoise install pi`` uses.  Before
    ``introduce_read_seam=False`` existed, a user who had explicitly run
    ``install pi --uninstall`` got the per-turn read seam back on the next
    repair: silently, because the command's own output never said the exposure
    changed.  That is a bypass of the recorded opt-in ("installing the
    extension IS the opt-in"), and on a machine with no ``tortoise`` on PATH
    but a configured key it POSTs the prompt window to the hosted endpoint.

    So both halves are pinned here: a repair restores the CAPTURE artifact, and
    it does not resurrect the READ seam in a tree that opted out.
    """
    from types import SimpleNamespace

    from tortoise.__main__ import _upgrade_artifact_seam

    assert _install(home).ok
    capture = _ext_dir(home) / CAPTURE_EXTENSION.name
    read_seam = _ext_dir(home) / VOLUNTEER_EXTENSION.name
    read_seam.unlink()                      # the user opted OUT
    assert capture.is_file() and not read_seam.exists()

    capture.unlink()                        # …and capture now needs repair
    rc = _upgrade_artifact_seam(
        SimpleNamespace(harness="pi", dry_run=False), _ext_dir(home), home)

    assert rc == 0
    assert capture.is_file(), "the repair must restore the capture artifact"
    assert not read_seam.exists(), (
        "the repair verb reintroduced the per-turn read seam in a tree where "
        "the user had explicitly opted out — it must refresh, never introduce")


@pytest.mark.parametrize("shape", ["directory", "fifo", "dangling-symlink"])
def test_a_repair_never_reports_success_over_a_broken_seam_shape(home, shape):
    """A NON-REGULAR path at the seam must refuse loudly, never read "current".

    The repair gate asks "is there anything at this path?", and the first cut of
    it used `is_file()` — so a directory, a FIFO or a symlink whose target was
    moved made the gate report ABSENT, the artifact installer never ran, and
    `hooks upgrade` printed "already current — nothing to do" and exited **0**
    while `detect_artifact_install` still returned a blocking finding for that
    same path (`not-a-regular-file` for a directory/FIFO, `symlinked-artifact`
    for a dangling link). A silent false SUCCESS over a
    blocking finding, in a command whose whole contract is "fail loudly"
    (`capture_install.py`: "Refusals are the product").

    The gate is `os.path.lexists`, so every shape reaches the guards that
    classify it: a refusal with a non-zero exit, and the finding still reported.
    """
    from types import SimpleNamespace

    from tortoise import hook_install
    from tortoise.__main__ import _upgrade_artifact_seam

    assert _install(home).ok
    root = _ext_dir(home)
    seam = root / VOLUNTEER_EXTENSION.name
    seam.unlink()                                    # the user opted OUT
    if shape == "directory":
        seam.mkdir()
    elif shape == "fifo":
        os.mkfifo(seam)
    else:
        os.symlink(str(home.parent / "moved-away" / "src.ts"), seam)

    blocking_before = [f.kind for f in
                       hook_install.detect_artifact_install(root, "pi") if f.blocking]
    # The KIND is shape-specific — the read seam's grader now mirrors the primary
    # artifact's vocabulary (round-2 review), so a DANGLING SYMLINK is
    # `symlinked-artifact` ("is a broken symlink — remove or re-point it") rather
    # than the vaguer `not-a-regular-file` the primary never emits for that
    # shape. Both are BLOCKING, which is the only property this row's
    # precondition needs (and the earlier wording pinned the wrong kind until the
    # round-2 fix moved it, which is what made this row fail — correctly).
    expected_kind = ("symlinked-artifact" if shape == "dangling-symlink"
                     else "not-a-regular-file")
    assert expected_kind in blocking_before, (
        f"precondition: a {shape} at the seam must be a blocking finding for "
        f"`hooks status`, got {blocking_before}")

    rc = _upgrade_artifact_seam(
        SimpleNamespace(harness="pi", dry_run=False), root, home)

    assert rc != 0, (
        "the repair reported SUCCESS over a shape `hooks status` calls blocking — "
        "the user is told to run this very command and then told nothing is wrong")
    blocking_after = [f.kind for f in
                      hook_install.detect_artifact_install(root, "pi") if f.blocking]
    assert blocking_after == blocking_before, (
        "a refused repair must leave the finding in place rather than claim a fix")


def test_a_repair_still_refreshes_an_installed_read_seam(home):
    """…but drift in an OPTED-IN seam is exactly what the repair is for.

    The guard added above must not make a stale read seam unreachable: refusing
    to introduce is a scope limit, not an exemption from drift repair.
    """
    from types import SimpleNamespace

    from tortoise.__main__ import _upgrade_artifact_seam

    assert _install(home).ok
    read_seam = _ext_dir(home) / VOLUNTEER_EXTENSION.name
    shipped = read_seam.read_bytes()
    read_seam.write_bytes(shipped + b"\n// stale\n")

    rc = _upgrade_artifact_seam(
        SimpleNamespace(harness="pi", dry_run=False), _ext_dir(home), home)

    assert rc == 0
    assert read_seam.read_bytes() == shipped, (
        "an installed (opted-in) seam must be refreshed to the shipped bytes")


def test_uninstall_honours_dry_run(home, capsys):
    """⛔ `--dry-run` is documented as "without writing", and the delete is the
    strongest write this verb performs — there is no undo, the installed file is
    a COPY, not a link (review finding 2: the first cut turned a branch that
    printed a sentence into an unconditional `unlink()`)."""
    from types import SimpleNamespace

    from tortoise.__main__ import _uninstall_pi_volunteer

    assert _install(home).ok
    rc = _uninstall_pi_volunteer(SimpleNamespace(dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] would remove" in out, out
    assert (_ext_dir(home) / VOLUNTEER_EXTENSION.name).is_file(), (
        "a dry run must not delete the seam")
    assert (_ext_dir(home) / CAPTURE_EXTENSION.name).is_file()


def test_uninstall_preserves_an_edit_it_cannot_prove_is_one(tmp_path, monkeypatch,
                                                           capsys):
    """An UNREADABLE shipped source is not evidence of equality (#11584 review
    finding 1) — the locally-edited seam must still be preserved.

    ``_pi_volunteer_local_edit_backup`` read both sides inside one ``try`` and
    returned ``None`` on ``OSError``; the caller reads ``None`` as "nothing to
    preserve" and goes straight to ``unlink()``. So on a partially-extracted
    wheel (or a downgrade) — precisely the shape
    ``_pi_volunteer_is_provably_ours`` documents and deliberately accepts — a
    locally edited seam was deleted with **no backup, no message, rc 0**: the
    round-9 unrecoverable-delete hazard re-opened through the source side.

    Hermetic: the seam lives under a temp ``HOME``, so an invocation can never
    delete the runner's own ``~/.pi``. No graph seed is needed, so this row does
    not pay for one.

    Mutation: put both reads back in one ``try`` (or return ``None`` from the
    source-side ``OSError``) — the ``.bak`` assertions RED and the edit is lost.
    """
    from dataclasses import replace

    from tortoise import capture_install, hook_install
    from tortoise.__main__ import _uninstall_pi_volunteer

    # ⛔ IMPORT `capture_install` BEFORE PATCHING THE CONTRACT, and assert the
    # premise. The verb derives the installed NAME from
    # `capture_install.PI_VOLUNTEER_EXTENSION_NAME`, which is computed at that
    # module's import time off the contract; a first import AFTER this patch
    # would derive the name from the patched contract, point the verb at a
    # missing path, take the "nothing to remove" branch and let this row pass
    # without exercising the backup at all (measured while proving the
    # mutation).
    assert VOLUNTEER_EXTENSION.name == capture_install.PI_VOLUNTEER_EXTENSION_NAME

    fake_home = tmp_path / "home"
    ext_dir = fake_home / ".pi" / "agent" / "extensions"
    ext_dir.mkdir(parents=True)
    seam = ext_dir / VOLUNTEER_EXTENSION.name
    edited = (f"// tortoise-hook-version: "
              f"{hook_install.read_hook_version(VOLUNTEER_EXTENSION)}\n"
              "// my local tweak\n")
    seam.write_text(edited, encoding="utf-8")

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))  # Path.home() on Windows
    # The shipped source is gone: a partial wheel / a downgrade.
    contracts = dict(hook_install.ARTIFACT_CONTRACTS)
    contracts["pi"] = replace(contracts["pi"],
                              volunteer_source=tmp_path / "no-such-source.ts")
    monkeypatch.setattr(hook_install, "ARTIFACT_CONTRACTS", contracts)

    rc = _uninstall_pi_volunteer(None)
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    assert not seam.exists(), "the verb still completes: the seam is removed"
    backup = seam.with_suffix(seam.suffix + ".bak")
    assert backup.is_file(), (
        "an edit that cannot be PROVEN unedited must be preserved before the "
        "delete — unrecoverable otherwise:\n" + captured.out)
    assert backup.read_text(encoding="utf-8") == edited, (
        "the backup must hold the bytes that were on disk")
    assert "backed up" in captured.out, captured.out
    assert "could not be read" in captured.out, (
        "the sentence must say WHY a copy was kept — the seam could not prove "
        "the bytes were the shipped ones:\n" + captured.out)


def test_the_read_seam_export_names_the_file_the_contract_installs():
    """The Pi read seam's name is declared TWICE — and the pair is pinned
    (#11584 review finding 2).

    ``hook_install.PI_VOLUNTEER_EXTENSION_NAME`` is the source of truth every
    Python consumer derives from (``capture_install`` re-derives it off
    ``ARTIFACT_CONTRACTS["pi"].volunteer_source``);
    ``tortoise-volunteer.ts``'s ``EXTENSION_NAME`` is an INDEPENDENT literal
    naming the source file the contract points at. No Python code can read a TS
    export, so the TS docstring's old claim ("hook_install derives it") was
    false in both directions — and nothing coupled the two values, which is the
    #4680 class the rest of this seam polices.

    Mutation: change the TS literal to any other name — this REDs, naming both
    values, instead of ``install pi`` copying a path that is not there.
    """
    from tortoise.hook_install import ARTIFACT_CONTRACTS, PI_VOLUNTEER_EXTENSION_NAME

    exported = _ts_string_constant("EXTENSION_NAME")
    assert exported == PI_VOLUNTEER_EXTENSION_NAME, (
        "the read seam's TS export and its contract declaration disagree: "
        f"TS says {exported!r}, hook_install declares "
        f"{PI_VOLUNTEER_EXTENSION_NAME!r}")
    assert ARTIFACT_CONTRACTS["pi"].volunteer_source.name == exported, (
        "the contract must point at the file the seam's own export names — "
        "otherwise the installer copies a path that does not exist")


def _ts_string_constant(name: str) -> str:
    """Read a plain string constant out of the shipped read seam.

    A mirror that is not read is not a pin: the value is parsed from the TS the
    installer ships, never restated here (the same reason
    ``tests/test_openclaw_seam.py::_plugin_int_constant`` exists for the
    OpenClaw seam).
    """
    src = VOLUNTEER_EXTENSION.read_text(encoding="utf-8")
    match = re.search(
        rf'^export const {re.escape(name)}\s*=\s*"([^"\\\n]*)";$', src, re.M)
    assert match, f"{name} is not a plain string export in {VOLUNTEER_EXTENSION}"
    return match.group(1)


# ── 2. the real seam, end to end ─────────────────────────────────────────


_DRIVER = r"""
import { pathToFileURL } from "node:url";

const seam = await import(pathToFileURL(process.env.SEAM_PATH).href);
const handlers = {};
const pi = { on: (event, fn) => { handlers[event] = fn; } };
seam.default(pi, { env: process.env, breadcrumbDir: process.env.CRUMB_DIR });

const ctx = { sessionManager: { getSessionId: () => process.env.SESSION_ID } };
const first = await handlers.before_agent_start(
  { messageType: "user", prompt: "How is CI deploy triggered?" }, ctx);
const second = await handlers.before_agent_start(
  { messageType: "user", prompt: "How is CI deploy triggered?" }, ctx);
const custom = await handlers.before_agent_start(
  { messageType: "custom", message: { content: "x" } }, ctx);
process.stdout.write(JSON.stringify({
  events: Object.keys(handlers),
  first: first ?? null,
  second: second ?? null,
  custom: custom ?? null,
}));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI — the real seam cannot be graded "
                        "without it (provision actions/setup-node)") 
        pytest.skip("node not available — the install-side pins still ran")
    return node


def _node_supports_ts(node: str) -> bool:
    try:
        out = subprocess.run([node, "--version"], capture_output=True,
                             text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.match(r"v(\d+)\.(\d+)", out)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return (major, minor) >= (22, 18) or (major, minor) >= (23, 0)


def _run_driver(home: Path, tmp_path: Path, *, display: bool) -> dict:
    node = _node()
    if not _node_supports_ts(node):
        if os.environ.get("CI"):
            pytest.fail("node >= 22.18 is required to load the .ts extension")
        pytest.skip("node < 22.18 does not enable TypeScript type stripping")

    installed = _ext_dir(home) / VOLUNTEER_EXTENSION.name
    assert installed.is_file(), "the read seam must be installed before it is graded"

    if display:
        (home / ".pi" / "agent" / "tortoise-config.json").write_text(
            json.dumps({"volunteer": {"display": True}}))
    # ⛔ Node takes a `.ts` file's module format from the NEAREST parent
    # `package.json`, and the fixture cannot own what sits above its temp dir: a
    # host (or CI runner) carrying `/tmp/package.json` with
    # `"type": "commonjs"` settles the installed seam as CommonJS and it dies
    # with `SyntaxError: Cannot use import statement outside a module` — a
    # verdict about the host, not the artifact. The temp HOME pins the type,
    # exactly as `tests/test_pi_capture_hooks.py::_run_installed_probe` does
    # (#11124). Written at the HOME root, NOT beside the seam: the install
    # directory must stay sibling-free.
    (home / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    crumbs = tmp_path / "crumbs"
    driver = tmp_path / "driver.mjs"
    driver.write_text(_DRIVER)
    # ⛔ NOT `.resolve()`: `.venv/bin/python` is a SYMLINK to the system
    # interpreter, so resolving it yields `/usr/bin` — a PATH with no
    # `tortoise` on it, which makes the seam record an inert install and
    # inject nothing, i.e. the test would pass its own bug as a verdict.
    venv_bin = str(Path(_PY).parent)
    env = {
        **_base_env(home),
        "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}",
        "SEAM_PATH": str(installed),
        "SESSION_ID": "pi-seam-e2e",
        "CRUMB_DIR": str(crumbs),
    }
    proc = subprocess.run([node, str(driver)], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return json.loads(proc.stdout)


_HOSTED_DRIVER = r"""
import { pathToFileURL } from "node:url";

const seam = await import(pathToFileURL(process.env.SEAM_PATH).href);
const handlers = {};
const pi = { on: (event, fn) => { handlers[event] = fn; } };
// PATH is empty and no `tortoise` command is configured, so the seam has NO CLI
// candidate and must take the HOSTED transport — the leg this row grades.
seam.default(pi, {
  env: { PATH: "" },
  home: process.env.SEAM_HOME,
  configPath: process.env.SEAM_CONFIG,
  breadcrumbDir: process.env.CRUMB_DIR,
});

const ctx = { sessionManager: { getSessionId: () => "sess-hosted" } };
// TWO turns with DIFFERENT prompts: an identical prompt would be stopped by the
// digest, and turn 2 is the one that has to carry turn 1's block.
const first = await handlers.before_agent_start(
  { messageType: "user", prompt: "first hosted turn" }, ctx);
const second = await handlers.before_agent_start(
  { messageType: "user", prompt: "second hosted turn" }, ctx);
process.stdout.write(JSON.stringify({
  first: first ?? null,
  second: second ?? null,
}));
"""


def test_the_hosted_body_is_the_contract_the_server_validates(home, tmp_path):
    """#2120/#11584: the seam's HOSTED body must validate against the SERVER's
    own model — not against a hand-copy.

    The sibling seam shipped exactly this defect once: `runHosted` posted
    `{prompt}`, `POST /v1/context` requires `window: [{role, content}]`, so EVERY
    hosted turn was a 422 — and the fail-open catch turned that into "injected
    nothing" with no signal anywhere. That is why
    `tests/test_openclaw_seam.py::test_hosted_body_is_the_contract_the_server_validates`
    exists: a hand-copy of the shape re-encodes the bug it is supposed to catch.

    The node row `CONTINUITY: the HOSTED transport sends prior_context too`
    compares the emitted body to a LITERAL written from the same reading of
    `VolunteerContextRequest` the implementation used, so it cannot see a
    contract drift. This row validates the captured body with the server's own
    Pydantic model — the same standard as the sibling, applied to this seam.
    """
    from tests.test_platform_seams import _RecordingStub, _stub_server
    from tortoise.hosted_api import VolunteerContextRequest

    node = _node()
    if not _node_supports_ts(node):
        if os.environ.get("CI"):
            pytest.fail("node >= 22.18 is required to load the .ts extension")
        pytest.skip("node < 22.18 does not enable TypeScript type stripping")

    assert _install(home).ok
    installed = _ext_dir(home) / VOLUNTEER_EXTENSION.name
    assert installed.is_file(), "the read seam must be installed before it is graded"

    stub = type("PiHostedStub", (_RecordingStub,), {
        "body": {"block": "hosted-block"}, "hits": []})
    with _stub_server(stub) as url:
        cfg = home / "hosted-config.json"
        cfg.write_text(json.dumps({"apiKey": "tt_key", "apiUrl": url,
                                   "volunteer": {}}))
        # Node takes a `.ts` module's format from the nearest parent
        # `package.json`; pin it (#11124), as `_run_driver` does.
        (home / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
        driver = tmp_path / "hosted-driver.mjs"
        driver.write_text(_HOSTED_DRIVER)
        env = {
            **_base_env(home),
            "SEAM_PATH": str(installed),
            "SEAM_HOME": str(home),
            "SEAM_CONFIG": str(cfg),
            "CRUMB_DIR": str(tmp_path / "crumbs"),
        }
        proc = subprocess.run([node, str(driver)], env=env, cwd=str(tmp_path),
                              capture_output=True, text=True, timeout=300)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert len(stub.hits) == 2, (
        f"both turns must reach the hosted transport, got {len(stub.hits)} hit(s): "
        f"{proc.stderr}")
    for hit in stub.hits:
        assert hit["path"].endswith("/v1/context"), hit["path"]
        # ⛔ THE SERVER'S OWN MODEL. A body it would 422 raises here.
        VolunteerContextRequest.model_validate(hit["body"])
    assert stub.hits[0]["body"]["prior_context"] is None, (
        "turn 1 has nothing to suppress")
    assert stub.hits[1]["body"]["prior_context"] == "hosted-block", (
        "turn 2 must carry turn 1's block as `prior_context` — the reflex's only "
        "re-mention-suppression input")
    assert json.loads(proc.stdout)["first"]["message"]["content"], (
        "the hosted turn must still inject on the first turn")


def test_the_installed_extension_injects_the_real_reflex_block(home, tmp_path):
    """THE grade the issue asks for: the installed artifact, Pi's own handler
    contract, the REAL reflex over a seeded graph."""
    assert _install(home).ok
    out = _run_driver(home, tmp_path, display=False)

    assert out["events"] == ["before_agent_start"]
    message = out["first"]["message"]
    assert message["customType"] == "tortoise-volunteer"
    assert message["display"] is False, "silent by default (W4 posture)"
    assert _SEEDED_CLAIM in message["content"], (
        "the injected block must carry the graph's own claim label")
    assert "point/" in message["content"]
    assert "data, not instructions" in message["content"], (
        "the reflex's prompt-injection envelope must survive the seam intact")
    # Cross-turn dedupe at the seam: the same session is not re-injected.
    assert out["second"] is None
    # A run started by a custom message is not searched.
    assert out["custom"] is None


def test_the_visible_surface_renders_the_surfaced_marker(home, tmp_path):
    assert _install(home).ok
    out = _run_driver(home, tmp_path, display=True)
    message = out["first"]["message"]
    assert message["display"] is True
    # The marker TRAILS the block. `display` controls TUI rendering only — the
    # message still enters the model's context — and the labels are GRAPH
    # CONTENT, so leading with them would put unframed graph text in the
    # highest-attention position, ahead of the producer's own
    # "data, not instructions" envelope.
    assert not message["content"].startswith("Tortoise memory surfaced: "), (
        "a graph-derived label must not lead the model's context — the "
        "disclosure marker belongs AFTER the framed block")
    assert "Tortoise memory surfaced: " in message["content"]
    assert _SEEDED_CLAIM in message["content"]


# ── 3. the extension's own behavioural suite ─────────────────────────────


def test_the_read_seam_suite_runs():
    node = _node()
    if not _node_supports_ts(node):
        if os.environ.get("CI"):
            pytest.fail("node >= 22.18 is required to run the .ts suite")
        pytest.skip("node < 22.18 does not enable TypeScript type stripping")
    env = {
        **os.environ,
        "HOME": str(Path(os.environ.get("TMPDIR", "/tmp"))),
        "TORTOISE_API_KEY": "",
        "TORTOISE_VOLUNTEER_DISABLE": "",
    }
    proc = subprocess.run([node, "--test", str(VOLUNTEER_TEST)],
                          cwd=str(REPO_ROOT), env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
