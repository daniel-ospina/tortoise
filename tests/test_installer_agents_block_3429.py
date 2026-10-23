"""#3429 — a provisioned repo's AGENTS.md block must be upgradeable, and say so.

THE DEFECT
----------
`install-tortoise-skills.sh` writes a marker-delimited Tortoise block into the
repo-root ``AGENTS.md`` of a project it provisions. The markers made a re-run
IDEMPOTENT — but nothing made a STALE block DETECTABLE. So a repo provisioned
before the block's text changed kept re-reading the old text in EVERY later
session, and there was no way for it or its user to tell: the installer was the
only writer, it rewrote the block silently, and the block itself carried no
version. Fixing what the installer WRITES (that is #3232) does not repair
repos that already carry the old block — mutating a behavioural contract inside
someone else's repo is a migration, and this file is its acceptance test.

THE FIX THIS PINS
-----------------
1. The emitted block carries a version line INSIDE the marker window
   (``<!-- tortoise:block vN -->``), so the ordinary marker-delimited replace
   refreshes it and a stale block is greppable.
2. The installer READS the version it finds and says what it did — created,
   upgraded (``v1 -> v2`` or ``unversioned -> v2``), or already current. A
   silent rewrite is the failure this replaces.
3. Re-running over an old block does not duplicate it and does not touch
   content outside the markers.

Every test drives the REAL installer end-to-end — a temporary copy with ONLY
its ``SKILLS_BASE`` URL substituted for a local ``file://`` fixture — and
asserts the bytes on disk plus the installer's own stdout, never source text.

Mutations that turn these RED (each verified RED before merge, restored byte-exact):
  * delete the version line from the emitted block        -> every version assertion
    fails (nothing to grep, no upgrade branch);
  * blank the ``prev_version`` read                       -> the migration and
    reinstall assertions fail (unversioned / already-current both misreport);
  * make the "already current" branch unreachable         -> the reinstall test fails;
  * edit the block's TEXT without bumping the version      -> the prose pin fails
    (the only thing that makes a stale ``AGENTS_BLOCK_VERSION`` visible);
  * decide the pre-state with an unanchored match          -> the malformed tests
    fail: the anchored split finds no pair and the block is duplicated;
  * make ``near()`` return 1 for any mention               -> the prose test fails
    (precision: quoting the marker is documentation, not a block);
  * make ``_emit_fail`` RETURN instead of exit             -> the unreadable-file
    test fails: ``cmd || helper`` is an errexit-exempt list, so a returning
    helper lets the next statement run and the run reports SUCCESS at exit 0
    over an untouched file;
  * drop the mangled-marker detection                      -> the internal-spacing
    test fails (that marker appended a second block);
  * strip ALL CRs when comparing (instead of CRLF only)    -> the CR-inside-a-line
    test fails (a real content change read as "already current");
  * restore the process-substitution comparison            -> the failing-``sed``
    test fails: two failed substitutions yield two EMPTY streams, ``cmp`` calls
    them EQUAL, and a stale block is reported "already current" at exit 0;
  * count markers inside a fenced code block               -> the fenced-example
    test fails (a project that DOCUMENTS the markers was refused, exit 3, and the
    pre-PR installer handled that very file);
  * make the non-numeric version fall through to ``unversioned`` -> that test
    fails, and the shell then prints ``integer expression expected`` and reports
    "same version, updated text";
  * make the unparsed-state arm a no-op                    -> the awk-crash test
    fails: nothing is written and the run claims ``upgraded`` at exit 0;
  * stage the block through a predictable ``$target.tmp``  -> the planted-symlink
    test fails (the write follows the link, clobbers an unrelated file, and turns
    ``AGENTS.md`` into a symlink to it — #4327's threat model);
  * locate the block with a FENCE-BLIND scan in the split -> the column-zero-fence
    test fails: the fenced EXAMPLE is overwritten and the real block's markers are
    deleted, while the report says "refreshed" (cycle 4 shipped the fence skip in
    the counter only, so the decision said "paired" and the writers edited the
    example);
  * accept only a bare fence (no indent, no language tag) -> the ```md test fails;
  * drop the unclosed-fence fallback                      -> the unclosed-fence test
    fails: ``state=none`` means APPEND, so one more copy is added per run, forever;
  * use an interval quantifier in the fence classifier        -> the mawk test
    fails: on a host whose awk is mawk the readable AGENTS.md is refused at exit 3
    — Ubuntu 24.04's mawk PANICS at REcompile time (so the state pass dies and the
    file is called unreadable), Debian 12's has no interval support at all (so
    ``{3,}`` is LITERAL and no fence matches → the MALFORMED remedy on a file with
    a fenced example AND a block, and a silent in-place overwrite of the fenced
    example at exit 0 on a file whose only markers are the example). #9274,
    invisible on this host's BSD awk and on CI's gawk;
  * neutralise BOTH the ``tr`` guard and the non-empty check -> the failing-``tr``
    test fails: ``tr`` dying empties both stripped markers, which silently disables
    the mangled detector, and a file whose two markers are BOTH spacing-mangled
    then reads as "no markers" and gets a duplicate block. (Either half alone is
    enough, so the pair is defence in depth — the test pins the pair.)

Cycle 5 shipped the fence skip in the counter only, and the writers were blind: a
documented example was deleted and a duplicate block was written outside any
marker. The fix is one map — the marker LINE NUMBERS — read by the version
extraction, the window and the split. Cycle 6 then measured the fallback
recounting the WHOLE file — a CLOSED fence's example was treated as the block,
replaced, and reported as an upgrade:

  * recount the whole file in the unclosed-fence fallback -> the closed-fence test
    fails (the example inside the fence is rewritten and reported as an upgrade);
  * strip the BOM with the locale-dependent escape, under a UTF-8 locale -> the
    BOM test fails: ``\357`` is the CHARACTER U+00EF there, so the BOM survived
    and the documented "BOM-prefixed" cause could never print;
  * refuse when ANY near/mangled line exists beside one clean pair -> the
    indented-example test fails: CommonMark's other code-block form was called a
    duplicate and its remedy deleted it;
  * drop the scratch-dir message                                -> the mktemp test
    fails: the user got no cause at all;
  * drop the pair re-assertion in the split                     -> the
    changes-mid-rewrite test fails: a file that moves between the read and the
    split is cut at the stale line numbers, silently.

That last one is why the split re-checks itself: the map is computed from an
earlier read, and AGENTS.md is exactly the file another process may write here.

Cycle 7 measured the FENCE MODEL itself. It was a parity toggle on three
backticks, which is not CommonMark's rule:

  * treat only backticks as a fence                        -> the tilde test fails:
    an example in a `~~~` fence was DELETED and the run reported "upgraded" over a
    block that never existed;
  * toggle on parity instead of matching the opening run     -> the nested-fence
    test fails: a 4-backtick fence around a 3-backtick example closed at the wrong
    line, so one marker was counted and the other skipped — a false MALFORMED with
    a remedy about a file that has no block;
  * drop the up-to-3-space indent tolerance                -> the indented-fence
    test fails;
  * report a fence-hidden ambiguity as "an older installer ran twice" -> the
    unterminated-fence test fails, because that remedy ("delete from the FIRST
    start marker through the LAST end marker") deletes the user's REAL block;
  * drop `LC_ALL=C` from the state pass                    -> the locale test fails
    (pinned at the source: unimplementable on this host's BSD awk, and gawk under
    a UTF-8 locale is where it bites).

Cycle 8 measured the CLOSING predicate and the silent path:

  * let a closing fence carry trailing text                -> the language-tagged
    test fails: a three-tick fence containing a three-tick language-tagged line
    closed early, so the rest of a documentation example was counted and a false
    "ran twice" was printed — whose remedy deletes the real block AND the docs;
  * make the still-open fence silent for paired/none       -> the not-silent test
    fails: the block is written INSIDE the open fence, where it renders as code
    and is never read, with no warning at all.

Cycle 2 also measured a version token in prose outside the block (read as
"current") and an older installer overwriting a newer block (called an
"upgrade"); both are tests here. Cycle 3 measured the false-success path
end-to-end: an unreadable ``AGENTS.md`` returned 0 and printed an "upgrade" the
pre-PR installer would have failed closed on. Cycle 4 measured three more: the
line-ending comparison failing OPEN on a broken ``sed``, a fenced EXAMPLE of the
markers hard-refusing the install (a regression against the pre-PR installer),
and a documented ``not a number`` report branch with no test behind it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
             / "install-tortoise-skills.sh")

#: The 3 reusable capabilities the installer ships (#4365). Onboarding is
#: delivered as INSTRUCTIONS, never installed as a skill.
SKILLS = ("how-to-use-tortoise", "tortoise-decide", "tortoise-file-finding")

START = "<!-- tortoise:start -->"
END = "<!-- tortoise:end -->"
FOREIGN_BEFORE = "## House rules\n\n- Foreign line ABOVE the block, never ours.\n"
FOREIGN_AFTER = "\n## More house rules\n\n- Foreign line BELOW the block.\n"

#: What a repo provisioned BEFORE #3429 carries: the same markers, the old
#: ordering text, and NO version line.
LEGACY_BLOCK = f"""{START}
# Tortoise — agent memory (this project is connected to Tortoise Cloud)
- When the user says "Set up Tortoise", follow the onboarding instructions at
  https://example.invalid/tortoise-onboarding/SKILL.md. Write the MCP config,
  restart the client so the new session reads it, then verify with
  tortoise_health before reporting done.
{END}
"""


def _payload(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: fixture payload for {name}\n"
        "domain: capability\n"
        "type: Workflow\n"
        "status: live\n"
        "updated: 2026-10-18\n"
        "allowed-tools: read write bash grep\n"
        "---\n"
        f"\n# {name}\n\nFixture body for {name}.\n"
    )


def _fixture_tree(tmp_path: Path) -> str:
    base = tmp_path / "served-skills"
    for name in SKILLS:
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(_payload(name), encoding="utf-8")
    return f"file://{base}"


def _patched_installer(tmp_path: Path, base_uri: str) -> Path:
    """The REAL installer with only its download base swapped."""
    src = INSTALLER.read_text(encoding="utf-8")
    patched, n = re.subn(r'^SKILLS_BASE=.*$', f'SKILLS_BASE="{base_uri}"', src,
                         count=1, flags=re.M)
    assert n == 1, "installer SKILLS_BASE anchor not found — update this test"
    out = tmp_path / "installer.sh"
    out.write_text(patched, encoding="utf-8")
    return out


def _run(installer: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    # The installer must not need a terminal; keep it offline and quiet.
    env.pop("TORTOISE_API_KEY", None)
    return subprocess.run(
        ["bash", str(installer), "--harness", "codex"],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300,
    )


def _agents_report(stdout: str) -> str:
    """The AGENTS.md line only — the skills lines print "(already current)" too,
    which is a different claim about a different artifact."""
    lines = [ln for ln in stdout.splitlines() if "AGENTS.md" in ln]
    assert lines, f"installer printed no AGENTS.md line:\n{stdout}"
    return "\n".join(lines)


def _versioned(text: str) -> list[int]:
    return [int(v) for v in re.findall(r"tortoise:block v(\d+)", text)]


def test_fresh_install_writes_a_versioned_block(tmp_path):
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    done = _run(installer, home, project)
    assert done.returncode == 0, f"installer failed:\n{done.stdout}\n{done.stderr}"

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    versions = _versioned(agents)
    assert len(versions) == 1, f"expected exactly one version line, got {agents!r}"
    assert START in agents and END in agents
    # The version must sit INSIDE the marker window, or the replace below
    # leaves it stale the first time the block is refreshed.
    assert agents.index(START) < agents.index("tortoise:block v") < agents.index(END)
    assert "created" in _agents_report(done.stdout), (
        f"a fresh install must say it CREATED the block:\n{done.stdout}")


def test_installer_upgrades_a_legacy_block_without_duplicating_or_clobbering(tmp_path):
    """The migration itself: an unversioned block from before this change."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        FOREIGN_BEFORE + LEGACY_BLOCK + FOREIGN_AFTER, encoding="utf-8")

    done = _run(installer, home, project)
    assert done.returncode == 0, f"installer failed:\n{done.stdout}\n{done.stderr}"

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(START) == 1, f"the block was duplicated:\n{agents}"
    assert agents.count(END) == 1
    versions = _versioned(agents)
    assert len(versions) == 1, f"expected one version line, got {versions}\n{agents}"
    # The old text is GONE — that is the point of the migration.
    assert "restart the client so the new session reads it, then verify with" not in agents, (
        "the legacy ordering survived the refresh")
    # Foreign content is untouched, in order, on both sides.
    assert agents.startswith(FOREIGN_BEFORE), agents[:200]
    assert agents.endswith(FOREIGN_AFTER), agents[-200:]
    assert "unversioned" in _agents_report(done.stdout), (
        f"the upgrade must be REPORTED, not silent:\n{done.stdout}")


def test_installer_upgrades_a_versioned_block_and_names_both_versions(tmp_path):
    """v1 → v2 when the block already carried a version line."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        FOREIGN_BEFORE
        + LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v1 -->")
        + FOREIGN_AFTER,
        encoding="utf-8",
    )

    done = _run(installer, home, project)
    assert done.returncode == 0, f"installer failed:\n{done.stdout}\n{done.stderr}"

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("tortoise:block v") == 1, (
        f"the old version line must be REPLACED, not kept alongside the new:\n{agents}")
    upgraded = re.search(r"upgraded v(\d+) → v(\d+)", done.stdout)
    assert upgraded, f"expected a named upgrade in stdout:\n{done.stdout}"
    assert upgraded.group(1) == "1" and upgraded.group(2) != "1"


def test_reinstall_reports_already_current_and_changes_nothing(tmp_path):
    """A re-run is the documented upgrade path, so it must be a no-op when
    there is nothing to upgrade — and it must say so instead of claiming work."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    first = _run(installer, home, project)
    assert first.returncode == 0, first.stdout + first.stderr
    before = (project / "AGENTS.md").read_text(encoding="utf-8")

    second = _run(installer, home, project)
    assert second.returncode == 0, second.stdout + second.stderr
    after = (project / "AGENTS.md").read_text(encoding="utf-8")

    assert after == before, "a second run must not change the block"
    report = _agents_report(second.stdout)
    assert "already current" in report, (
        f"a re-run over a current block must say so:\n{report}")
    assert "created" not in report and "upgraded" not in report, report


# ── The report must be derived from what was WRITTEN, not from the version ──
#
# Cycle 1 found the message came from the version token alone: "already current"
# was printed while the body was being rewritten (drifted text, same version),
# and "upgraded"/"created" was printed while a malformed marker pair made the
# write duplicate the block and keep the stale one. Each test below is a
# reachable pre-state that produced a false report.


def _install(tmp_path: Path, agents: str | None):
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    if agents is not None:
        (project / "AGENTS.md").write_text(agents, encoding="utf-8")
    done = _run(installer, home, project)
    return done, (project / "AGENTS.md").read_text(encoding="utf-8")


def _drift_installer(tmp_path: Path) -> Path:
    """A copy whose block TEXT differs while the VERSION is unchanged — the
    hand-discipline failure the version line cannot see."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    src = installer.read_text(encoding="utf-8")
    drifted = src.replace(
        "- First-time MCP calls may prompt for approval",
        "- First-time MCP calls may prompt for approval (DRIFTED SENTENCE)",
        1,
    )
    assert drifted != src, "drift anchor not found — update this test"
    installer.write_text(drifted, encoding="utf-8")
    return installer


def test_same_version_different_text_is_not_already_current(tmp_path):
    """A version line is evidence of ORDER, not proof the text matches."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    assert _run(installer, home, project).returncode == 0
    drifted = _drift_installer(tmp_path)
    done = _run(drifted, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, (
        f"the body WAS rewritten, so 'already current' is false:\n{report}")
    assert "updated text" in report, (
        f"a same-version text change must be reported as a refresh:\n{report}")
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "DRIFTED SENTENCE" in agents
    assert _versioned(agents) == [_current_version()]


def test_malformed_markers_are_refused_not_silently_duplicated(tmp_path):
    """A stray end marker made the anchored awk split swallow the tail: the
    report said 'upgraded' while user content was deleted and the stale block
    survived. The installer must refuse and say so."""
    agents_in = (
        FOREIGN_BEFORE
        + END                      # stray end BEFORE any start
        + "\nTAIL THAT MUST SURVIVE\n"
        + LEGACY_BLOCK.replace("OLD", "OLD")
        + FOREIGN_AFTER
    )
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode != 0, (
        f"a malformed marker pair must fail the run, not report success:\n{done.stdout}")
    assert "MALFORMED" in done.stderr, done.stderr
    assert agents == agents_in, (
        f"the file must be left byte-identical when it is refused:\n{agents}")
    assert "TAIL THAT MUST SURVIVE" in agents


def test_start_marker_without_end_is_refused(tmp_path):
    done, agents = _install(tmp_path, LEGACY_BLOCK.replace(END + "\n", ""))

    assert done.returncode == 3, done.stdout
    assert "MALFORMED" in done.stderr
    assert agents.count(START) == 1, f"the block was duplicated:\n{agents}"
    assert agents == LEGACY_BLOCK.replace(END + "\n", "")


def test_version_token_outside_the_window_does_not_mean_current(tmp_path):
    """A repo that DOCUMENTS the marker in prose would otherwise read as
    versioned while carrying an unversioned, stale block."""
    agents_in = "We use a `tortoise:block v2` marker here.\n\n" + LEGACY_BLOCK
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, (
        f"the token was OUTSIDE the block, so nothing was current:\n{report}")
    assert "unversioned" in report, report
    assert "restart the client so the new session reads it, then verify with" not in agents


def test_an_older_installer_says_so_instead_of_calling_it_an_upgrade(tmp_path):
    """v3 block + v2 installer (pinned/cached/rolled-back copy)."""
    newer = _current_version() + 1
    agents_in = LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v{newer} -->")
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "upgraded" not in report, (
        f"writing v2 over v3 is a DOWNGRADE, not an upgrade:\n{report}")
    assert "OLDER than the block" in report, report
    assert _versioned(agents) == [_current_version()]


#: sha256 of the block's PROSE with the version line removed. The version's only
#: job is ORDER (v1 → v2); the decision compares bytes. But editing the text
#: without a bump still silently changes what every provisioned repo re-reads in
#: every future session, and no other test can see it — the installer would
#: report "refreshed (same version, updated text)" and mean it. So the text is
#: pinned: edit it → update this pin AND bump AGENTS_BLOCK_VERSION, in one commit.
#: The version line is excluded so a bump alone does not red this pin.
BLOCK_PROSE_SHA256 = "d32104d5819bed5a77e6925379d27e05533c952bf0ecde6370fe9730c6067eea"


def _current_version() -> int:
    """Read AGENTS_BLOCK_VERSION from the installer — a legitimate bump must not
    red this suite for the wrong reason (cycle 2: the downgrade fixture was
    hard-coded to v3, so bumping to v3 made it a v3-onto-v3 "downgrade")."""
    m = re.search(r'^AGENTS_BLOCK_VERSION="(\d+)"', INSTALLER.read_text(encoding="utf-8"), re.M)
    assert m, "AGENTS_BLOCK_VERSION anchor not found — update this test"
    return int(m.group(1))


def _block_prose() -> str:
    src = INSTALLER.read_text(encoding="utf-8")
    head = "  printf '%s\\n' \\\n"
    start = src.index(head) + len(head)
    body = src[start:src.index('${me}" > "$block"')]
    assert body.startswith('"${ms}'), body[:80]
    lines = body.split("\n")[1:]          # drop the `"${ms}` line
    # Drop the VERSION line by POSITION — a substring filter also dropped any
    # later line that merely mentioned the marker, so adding such a line shipped
    # a text change past the pin with no bump (cycle 2, mutation d2).
    # In the SOURCE the line still holds the shell expansion, so match the shape.
    assert lines[0].startswith("<!-- tortoise:block v") and lines[0].endswith(" -->"), lines[:2]
    return "\n".join(lines[1:])


def test_block_text_edit_requires_a_version_bump():
    import hashlib

    got = hashlib.sha256(_block_prose().encode("utf-8")).hexdigest()
    assert got == BLOCK_PROSE_SHA256, (
        "the AGENTS.md block's TEXT changed. That text is re-read by every agent "
        "in every provisioned repo, so the change needs a VERSION BUMP to be "
        "reachable there — the installer only upgrades what it can compare.\n"
        f"  new sha256: {got}\n"
        "  -> bump AGENTS_BLOCK_VERSION, update BLOCK_PROSE_SHA256, and check the "
        "message examples in docs/quickstart-cloud.md + both served SKILL copies.")


# ── Pre-states that had no test, and were wrong because of it ────────────────


def test_indented_markers_are_refused_not_duplicated(tmp_path):
    """The mildest edit — a formatter indenting the marker lines — made the
    anchored detector see NO marker, so `none` APPENDED a second block: the stale
    copy stayed first and every later run compared the appended pair to itself
    and said "already current". A near-anchor marker is a MALFORMED marker."""
    legacy = LEGACY_BLOCK.replace(START, "  " + START).replace(END, "  " + END)
    done, agents = _install(tmp_path, FOREIGN_BEFORE + legacy)

    assert done.returncode == 3, (
        f"a refusal exits 3 (skills installed, block not upgraded), got {done.returncode}:\n{done.stdout}")
    assert "MALFORMED" in done.stderr, done.stderr
    assert agents.count("tortoise:start") == 1, f"the block was duplicated:\n{agents}"


def test_trailing_space_markers_are_refused_not_duplicated(tmp_path):
    legacy = LEGACY_BLOCK.replace(START, START + " ").replace(END, END + " ")
    done, agents = _install(tmp_path, legacy)

    assert done.returncode == 3, (
        f"a refusal exits 3 (skills installed, block not upgraded), got {done.returncode}:\n{done.stdout}")
    assert "MALFORMED" in done.stderr, done.stderr
    assert agents.count("tortoise:start") == 1, f"the block was duplicated:\n{agents}"


def test_an_existing_agents_file_without_markers_is_appended_once(tmp_path):
    """The common case: the project already HAS an AGENTS.md with its own rules."""
    done, agents = _install(tmp_path, FOREIGN_BEFORE)

    assert done.returncode == 0, done.stdout + done.stderr
    assert agents.startswith(FOREIGN_BEFORE)
    assert agents.count(START) == 1 and agents.count(END) == 1
    assert "created" in _agents_report(done.stdout)


def test_a_pure_line_ending_difference_does_not_claim_a_text_change(tmp_path):
    """CRLF file: a pure EOL difference must not be reported as an updated text
    (and must not be rewritten into mixed endings)."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert _run(installer, home, project).returncode == 0

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    (project / "AGENTS.md").write_bytes(agents.replace("\n", "\r\n").encode())
    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "updated text" not in report, (
        f"only the line endings differed, nothing was rewritten:\n{report}")
    assert "already current" in report, report


def test_a_numeric_version_that_does_not_compare_never_reads_as_current(tmp_path):
    """An out-of-range version made `-lt`/`-gt` error and fall through to the
    refresh branch: a DOWNGRADE announced as a same-version text update."""
    huge = "9" * 25
    agents_in = LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v{huge} -->")
    done, _agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, report
    assert "refreshed (v" not in report and "updated text" not in report, (
        f"a downgrade must never be reported as a text refresh:\n{report}")
    assert ("too large to compare" in report or "unreadable" in report), report


# ── Cycle 3: fail-closed under a `||` wrapper, near() precision, mangled markers


def test_an_unreadable_agents_file_fails_instead_of_reporting_success(tmp_path):
    """`emit … || { exit 3; }` means bash does NOT apply errexit inside the
    function, so an internal read failure used to be reported as a SUCCESSFUL
    upgrade over an untouched file — the #3429 harm, at exit 0."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text(LEGACY_BLOCK, encoding="utf-8")
    agents.chmod(0o000)

    try:
        done = _run(installer, home, project)
    finally:
        agents.chmod(0o600)

    if done.returncode == 0:
        # Running as root makes the file readable anyway; nothing to assert.
        import os as _os
        if _os.geteuid() == 0:
            return
    assert done.returncode != 0, (
        f"an unreadable AGENTS.md must fail, not report success:\n{done.stdout}")
    assert "AGENTS.md" in (done.stderr + done.stdout)
    # No success line at all: the failure is reported on stderr.
    assert "✓ AGENTS.md" not in done.stdout, done.stdout


def test_prose_mentioning_the_marker_is_not_a_marker(tmp_path):
    """near() must be PRECISE: quoting the marker in prose is documentation, not
    a block, and must neither refuse nor be treated as the block."""
    agents_in = (
        "- Our block delimiters are <!-- tortoise:start --> and <!-- tortoise:end -->.\n"
        "- Foreign rule that must survive.\n"
    )
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    assert agents.startswith(agents_in), agents[:200]
    # The prose keeps its mentions; what must appear exactly once is the block,
    # i.e. markers on their OWN line.
    own_line = [ln for ln in agents.splitlines() if ln in (START, END)]
    assert own_line == [START, END], agents
    assert "created" in _agents_report(done.stdout)


def test_a_marker_with_different_internal_spacing_is_refused(tmp_path):
    """`<!--  tortoise:start  -->` is a BROKEN marker, not prose: left as
    `none` it appends and leaves the stale block first."""
    legacy = LEGACY_BLOCK.replace(START, "<!--  tortoise:start  -->")
    done, agents = _install(tmp_path, FOREIGN_BEFORE + legacy)

    assert done.returncode == 3, f"must refuse, not append:\n{done.stdout}"
    assert "MALFORMED" in done.stderr, done.stderr
    assert "internal spacing" in done.stderr, done.stderr
    assert agents.count("tortoise:start") == 1, agents


def test_the_refusal_names_the_marker_counts(tmp_path):
    """The docs say it prints how many and which markers — pin the shape."""
    done, _agents = _install(tmp_path, LEGACY_BLOCK + "\n" + LEGACY_BLOCK)

    assert done.returncode == 3
    assert "start markers: 2" in done.stderr, done.stderr
    assert "end markers: 2" in done.stderr, done.stderr
    assert "ran twice" in done.stderr, done.stderr


def test_a_cr_inside_a_line_is_a_real_difference(tmp_path):
    """Only a CR immediately before the newline is an EOL difference. A CR in the
    MIDDLE of a line is content, so the block must not read as current."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert _run(installer, home, project).returncode == 0

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    altered = agents.replace("agent memory", "agent\r memory", 1)
    assert altered != agents
    (project / "AGENTS.md").write_text(altered, encoding="utf-8")
    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, (
        f"a CR inside a line is content, not an EOL difference:\n{report}")
    assert "updated text" in report, report


# ── Cycle 4: fail-open comparison, fenced doc examples, non-numeric versions,
#    an unparsed state, and a planted staging symlink


#: #9274 — a stand-in for mawk, which IS `awk` on a stock Debian 12 / Ubuntu 24.04.
#: Both released builds break the obvious spelling of the fence classifier, in
#: different ways:
#:   * 1.3.4 20240123 (Ubuntu 24.04) PANICS at REcompile time on
#:     ``/^ {0,3}(`{3,}|~{3,})/`` — "values still on machine stack", exit 100
#:     (measured in ``ubuntu:24.04``; the trigger is the leading zero-minimum
#:     interval followed by an open-ended one — ``/^ {0,3}a+/`` and ``/x{3,}/``
#:     compile fine);
#:   * 1.3.4 20200120 (Debian 12) predates interval support entirely
#:     (``-W re-interval`` is a "vacuous option"), so ``{3,}`` is a LITERAL and no
#:     fence ever matches (measured: ``/^a{3,}/`` misses "aaa"). The state pass
#:     then SUCCEEDS: a fenced example PLUS a block reads as malformed (a false
#:     MALFORMED), while a file whose ONLY markers are the fenced example reads as
#:     paired — the example is overwritten in place and the run says "✓ upgraded"
#:     at exit 0 (measured in debian:12-slim).
#: The shim models the PANIC for the whole interval-quantifier class rather than
#: re-implementing mawk's state-stack bug: on the Debian build every interval is
#: already wrong, so "a classifier that needs none" is the only spelling that is
#: right on both — and over-refusing here fails in the safe direction (a red test,
#: not a user's refused ``AGENTS.md``). Anything without an interval goes to the
#: HOST awk, so the rest of the installer still runs for real. ``@SHIM_CALLED@`` is
#: touched on every pass through the shim so a test can prove the INSTALLER really
#: consulted it — an installer that later resolves ``gawk`` by absolute path would
#: otherwise leave this test green while modelling nothing.
MAWK_SHIM = """#!/bin/sh
prog=
for a in "$@"; do prog="$prog
$a"; done
: > '@SHIM_CALLED@'
if printf '%s' "$prog" | grep -qE '\\{[0-9]+(,[0-9]*)?\\}'; then
  echo "REcompile() - panic:  values still on machine stack for <interval expression>" >&2
  exit 100
fi
exec @REAL_AWK@ "$@"
"""


def _install_with_stub(tmp_path: Path, stub: str, body: str, agents: str | None):
    """Run the REAL installer with one stubbed command first on PATH."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / stub).write_text(body, encoding="utf-8")
    (bindir / stub).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    if agents is not None:
        (project / "AGENTS.md").write_text(agents, encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    done = subprocess.run(
        ["bash", str(installer), "--harness", "codex"],
        cwd=str(project), env=env, capture_output=True, text=True, timeout=300)
    return done, (project / "AGENTS.md")


def test_a_failing_sed_cannot_read_as_already_current(tmp_path):
    """Process substitution that fails yields two EMPTY streams, so `cmp` says
    "equal" and a stale block is reported as current. The normalisation is done
    into real files and each step is checked."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert _run(installer, home, project).returncode == 0
    target = project / "AGENTS.md"
    # Drifted text AND CRLF, so the exact `cmp` misses and the normalisation path
    # is the only thing that can decide — which is what the stub breaks.
    target.write_bytes(
        target.read_text(encoding="utf-8")
        .replace("agent memory", "agent  memory").replace("\n", "\r\n").encode())

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sed").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bindir / "sed").chmod(0o755)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    done = subprocess.run(["bash", str(installer), "--harness", "codex"],
                          cwd=str(project), env=env, capture_output=True, text=True, timeout=300)

    assert done.returncode != 0, f"a failed normalisation must not report success:\n{done.stdout}"
    # Scope to the AGENTS line: the SKILLS lines legitimately say "(already current)".
    assert "✓ AGENTS.md" not in done.stdout, done.stdout


@pytest.mark.parametrize("hardlinked", [False, True], ids=["single-link", "hardlinked"])
def test_a_fenced_documentation_example_does_not_refuse_the_install(tmp_path, hardlinked):
    """A FENCE is an example, not content. Refused before: a repo that documents
    the markers in a fence was told its markers were malformed — and the remedy
    said to delete the example. A file with ONLY a fenced example has no block
    at all, so it must be created, not refused.

    `hardlinked` puts this file on the IN-PLACE write arms (`ln AGENTS.md
    CLAUDE.md`), which is where the mid-write concurrency check lives. That check
    used to `grep` the file for the marker TEXT — fence-blind and line-blind — so
    the very example this test is about made it refuse, permanently, naming a
    concurrent install as the cause and advising a re-run that re-matched the
    user's own documentation every time (measured rc 3 on both runs). The check
    now asks whether the target CHANGED (`cmp` against the pre-state copy), which
    cannot false-positive on documentation.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = "## How this file is structured\n\n```\n  " + START + "\n  ...\n  " + END + "\n```\n"
    agents = project / "AGENTS.md"
    agents.write_text("# House rules\n\n" + example, encoding="utf-8")
    alias = None
    if hardlinked:
        alias = project / "CLAUDE.md"
        alias.hardlink_to(agents)

    done = _run(installer, home, project)

    assert done.returncode == 0, (
        f"a fenced EXAMPLE of the markers is documentation, not a broken block:\n{done.stderr}")
    text = agents.read_text(encoding="utf-8")
    assert text.count(START) == 2, "expected the example plus one real block"
    assert text.count(END) == 2, "the created block must be closed exactly once"
    assert example in text, "the example was eaten"
    assert "✓ AGENTS.md" in done.stdout, done.stdout

    # Second run: current, and the example is still not a second block.
    again = _run(installer, home, project)
    assert again.returncode == 0, again.stderr
    assert "already current" in _agents_report(again.stdout), again.stdout
    assert agents.read_text(encoding="utf-8") == text
    if alias is not None:
        assert agents.stat().st_ino == alias.stat().st_ino, (
            "the install must not split a hardlinked alias")
        assert alias.read_text(encoding="utf-8") == text, (
            "the alias must hold the same bytes — a rename splits the inode")


def test_a_non_numeric_version_is_reported_as_such(tmp_path):
    """`v2-rc1` used to fall through to `unversioned` — a false statement about
    the file — and with the guard removed both comparisons ERROR and fall
    through to `refreshed (same version, updated text)`."""
    agents_in = LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v2-rc1 -->")
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "not a number this installer can compare" in report, report
    assert "unversioned" not in report, report
    assert "updated text" not in report, report
    # The report says so AND the file was really upgraded (not just claimed).
    assert "<!-- tortoise:block v2-rc1 -->" not in agents
    assert "<!-- tortoise:block v2 -->" in agents
    assert "integer expression expected" not in (done.stdout + done.stderr), (
        "a shell comparison must not be fed a non-number")


def test_an_unparsed_state_fails_instead_of_claiming_an_upgrade(tmp_path):
    """If the state awk prints nothing (crash), nothing must be written and no
    claim may be made — the earlier failure mode was `upgraded` at exit 0."""
    done, agents = _install_with_stub(tmp_path, "awk", "#!/bin/sh\nexit 0\n", LEGACY_BLOCK)

    assert done.returncode != 0, f"an unparsed state must fail:\n{done.stdout}"
    assert "✓ AGENTS.md" not in done.stdout, done.stdout
    if agents.exists():
        assert agents.read_text(encoding="utf-8") == LEGACY_BLOCK


def test_the_fence_classifier_installs_on_a_host_whose_awk_is_mawk(tmp_path):
    """#9274 — the block must install where ``awk`` is mawk, not refuse a readable
    file. On Debian/Ubuntu (mawk is the distro default) the fence classifier's
    interval quantifiers break the install in two DIFFERENT ways. Ubuntu 24.04's
    mawk panics at REcompile time, so the state pass dies and the run says
    ``✗ AGENTS.md — could not read AGENTS.md (unreadable, or not a regular file)``.
    Debian 12's mawk has no interval support at all, so ``{3,}`` is literal and no
    fence ever matches: a fenced example PLUS a block then reads as malformed
    (``the Tortoise block markers are MALFORMED … delete from the FIRST start
    marker`` — advice that deletes the user's documentation), while a file whose
    ONLY markers are the example reads as paired and the example is overwritten in
    place at exit 0, reported as ``✓ upgraded`` (measured in debian:12-slim). All
    of it over files that were perfectly readable. Unobservable on BSD awk (this
    host) and on CI's gawk, which is why it shipped.

    The shim models the FIRST class (the panic). It refusing the whole interval
    class is the safe direction: the SECOND class has no correct intervals to
    model, and its symptom is not a panic at all."""
    real_awk = shutil.which("awk")
    assert real_awk, "no awk on PATH — cannot model a mawk host"
    probe_called = tmp_path / "probe-awk-called"
    probe_shim = (MAWK_SHIM.replace("@REAL_AWK@", real_awk)
                  .replace("@SHIM_CALLED@", str(probe_called)))

    # The shim itself is pinned: a shim that stopped modelling the panic would
    # make the assertion below vacuous (the installer would just pass).
    probe_dir = tmp_path / "probe-bin"
    probe_dir.mkdir()
    (probe_dir / "awk").write_text(probe_shim, encoding="utf-8")
    (probe_dir / "awk").chmod(0o755)
    refused = subprocess.run(
        [str(probe_dir / "awk"), '/^ {0,3}(`{3,}|~{3,})/'],
        capture_output=True, text=True, timeout=60)
    assert refused.returncode == 100, (
        f"the shim must refuse what mawk 1.3.4 refuses: rc={refused.returncode}\n"
        f"{refused.stdout}{refused.stderr}")
    assert "REcompile() - panic" in refused.stderr, refused.stderr
    assert probe_called.exists(), "the shim did not even run in the probe"

    # A fenced EXAMPLE of the markers plus the real (legacy) block: the fence
    # scanner is exercised on the way to the upgrade, exactly as on a mawk host.
    agents_in = ("# House rules\n\n"
                 f"```\n{START}\n...\n{END}\n```\n\n" + LEGACY_BLOCK)
    installer_called = tmp_path / "installer-awk-called"
    done, agents = _install_with_stub(
        tmp_path, "awk",
        MAWK_SHIM.replace("@REAL_AWK@", real_awk)
        .replace("@SHIM_CALLED@", str(installer_called)),
        agents_in)

    # Liveness, so this cannot pass while proving nothing: an installer that later
    # resolves ``gawk``/``/usr/bin/awk`` by absolute path (issue #9274's option 2)
    # would bypass the shim entirely, and the mawk regression would be unmodelled.
    assert installer_called.exists(), (
        "the installer never consulted the shim — this test proved nothing")
    assert done.returncode == 0, (
        "a mawk host must install the block, not refuse a readable file:\n"
        f"{done.stdout}{done.stderr}")
    assert "could not read" not in done.stderr, done.stderr
    text = agents.read_text(encoding="utf-8")
    assert text.count(START) == 2, f"the documented fenced EXAMPLE must survive:\n{text}"
    assert text.count(END) == 2, f"the example plus ONE closed block:\n{text}"
    assert "unversioned" in _agents_report(done.stdout), done.stdout


def test_a_planted_staging_symlink_cannot_redirect_the_write(tmp_path):
    """#4327's threat model, applied to the block writer: a predictable
    `AGENTS.md.tmp` allowed a planted symlink to clobber an arbitrary file."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    victim = project / "victim.txt"
    victim.write_text("ORIGINAL — must not change\n", encoding="utf-8")
    (project / "AGENTS.md").write_text(LEGACY_BLOCK, encoding="utf-8")
    (project / "AGENTS.md.tmp").symlink_to(victim)

    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    assert victim.read_text(encoding="utf-8") == "ORIGINAL — must not change\n", (
        "the write followed a planted symlink and clobbered an unrelated file")
    assert not (project / "AGENTS.md").is_symlink(), "AGENTS.md became a symlink"
    assert (project / "AGENTS.md").read_text(encoding="utf-8").count(START) == 1


def test_a_column_zero_fenced_example_is_not_the_block(tmp_path):
    """The canonical form (```` ``` ```` at column 0) is the one that used to be
    MISTAKEN for the block: the state counted with fences while the version read,
    window extraction and split were fence-blind, so a documented example was
    deleted and a duplicate block was written OUTSIDE any marker."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"```\n{START}\n...\n{END}\n```\n"
    body = "# House rules\n\n" + example
    agents = project / "AGENTS.md"
    agents.write_text(body, encoding="utf-8")
    assert _run(installer, home, project).returncode == 0
    installed = agents.read_text(encoding="utf-8")
    assert example in installed, "the documented example was deleted"

    # Now perturb the block's TEXT so a rewrite (not "already current") happens,
    # and re-run: the example must still be exactly where it was.
    drifted = installed.replace("safe to allow.", "safe to allow (drifted).")
    agents.write_text(drifted, encoding="utf-8")
    done = _run(installer, home, project)

    assert done.returncode == 0, done.stderr
    text = agents.read_text(encoding="utf-8")
    assert text.count(START) == 2, f"expected the example + one block:\n{text}"
    assert text.count(END) == 2
    assert example in text, "the rewrite ate the documented example"
    assert "safe to allow (drifted)." not in text, "the stale text survived the rewrite"
    assert "already current" not in _agents_report(done.stdout), "a rewrite happened"
    # The LAST pair is the real one: everything after the closing marker is intact.
    assert text.rstrip().endswith(END)


def test_a_language_tagged_fence_example_is_not_the_block(tmp_path):
    """```` ```md ```` is the form quickstart uses — the toggle must accept a
    language tag, not only a bare fence."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"```md\n{START}\n...\n{END}\n```\n"
    agents = project / "AGENTS.md"
    agents.write_text(example, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, f"a language-tagged EXAMPLE was treated as a block:\n{done.stderr}"
    text = agents.read_text(encoding="utf-8")
    assert text.count(START) == 2, text
    assert example in text, "the example was eaten"


def test_an_unclosed_fence_does_not_append_a_second_block(tmp_path):
    """An unterminated fence hides every later marker; "no markers" means
    APPEND, which added one more copy per run forever. The fallback is the
    fence-blind pass — the pre-#3429 behaviour, which replaced the real block."""
    agents_in = "# House\n\n```bash\nnpm run build\n" + LEGACY_BLOCK
    done, text = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    assert text.count(START) == 1, f"a second block was appended:\n{text}"
    assert text.count(END) == 1
    assert "npm run build" in text, "content outside the markers was lost"

    # And it must stay at one copy on the next run.
    installer = _patched_installer(tmp_path, _fixture_tree(tmp_path))
    again = _run(installer, tmp_path / "home", tmp_path / "project")
    assert again.returncode == 0, again.stderr
    assert (tmp_path / "project" / "AGENTS.md").read_text(encoding="utf-8").count(START) == 1


def test_a_failing_tr_fails_closed(tmp_path):
    """`tr` failing empties both whitespace-stripped markers, which silently
    disables the mangled detector and reverts the duplicate-block path. The guard
    was `command not found` because it ran BEFORE `_emit_fail` was defined."""
    mangled = ("<!--  tortoise:start  -->\nstale body\n"
               + END.replace("<!-- ", "<!--  ").replace(" -->", "  -->") + "\n")
    done, agents = _install_with_stub(tmp_path, "tr", "#!/bin/sh\nexit 1\n", mangled)

    assert done.returncode != 0, f"a broken tr must fail closed:\n{done.stdout}"
    assert "✓ AGENTS.md" not in done.stdout, done.stdout
    assert "_emit_fail: command not found" not in done.stderr, (
        "the guard called a function that was not defined yet")
    if agents.exists():
        assert agents.read_text(encoding="utf-8") == mangled


def test_a_fenced_verbatim_copy_of_the_block_does_not_mask_a_stale_one(tmp_path):
    """If the window is located fence-blind, a repo that pastes the CURRENT block
    into a documentation fence is told "already current" while its real block
    stays stale: the comparison input is a proxy, not the artifact."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    assert _run(installer, home, project).returncode == 0
    current = agents.read_text(encoding="utf-8")
    example = f"```\n{current}```\n"

    # A STALE (unversioned, old-text) block, plus a fence holding a byte-identical
    # copy of the CURRENT one.
    agents.write_text("# Docs\n\n" + example + "\n" + LEGACY_BLOCK, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, done.stderr
    text = agents.read_text(encoding="utf-8")
    assert "already current" not in _agents_report(done.stdout), (
        "a fenced copy is documentation — it must not satisfy the comparison")
    assert example in text, "the fenced copy was rewritten"
    assert text.count("<!-- tortoise:block v") == 2, "expected the fenced copy + the real block"
    assert "setuptortoise.invalid" not in text  # sanity: fixture text is gone
    # The STALE text is uniquely the old sentence; the new block does not contain it.
    assert "follow the onboarding instructions at\n  https://example.invalid" not in text, (
        "the stale block was left in place")
    assert text.rstrip().endswith(END)


def test_a_closed_fence_is_not_reconsidered_by_the_unclosed_fence_fallback(tmp_path):
    """The fallback recounts when a fence never closes — but it must reconsider
    only THE LINES THAT FENCE PASS SKIPPED. Recounting the whole file treated a
    CLOSED fence's documented example as the block: it was overwritten inside the
    fence, reported as an "upgrade" of a block that never existed."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"```\n{START}\n...\n{END}\n```\n"
    agents = project / "AGENTS.md"
    # A CLOSED example, an UNCLOSED fence far below it, and no install yet.
    agents.write_text("Add this by hand if you like:\n" + example + "\n```bash\nnpm run build\n",
                      encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, done.stderr
    text = agents.read_text(encoding="utf-8")
    assert example in text, "the closed fenced example was rewritten by the fallback"
    assert text.count("tortoise:block v") == 1, "expected exactly the block it created"


def test_an_indented_example_beside_a_real_block_is_preserved(tmp_path):
    """A 4-space-indented marker pair is CommonMark's other code block. With ONE
    clean pair present it is documentation, not a duplicate: refusing it printed
    "an older installer ran twice" and the remedy deleted the example."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"    {START}\n    ...\n    {END}\n"
    agents = project / "AGENTS.md"
    agents.write_text("# Docs\n\n" + example + "\n" + LEGACY_BLOCK, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, (
        f"an indented EXAMPLE beside one clean pair is documentation:\n{done.stderr}")
    text = agents.read_text(encoding="utf-8")
    assert example in text, "the indented example was eaten"
    assert text.rstrip().endswith(END)
    assert "https://example.invalid" not in text, "the real block was not upgraded"


def test_a_bom_prefixed_marker_is_diagnosed_as_such(tmp_path):
    """`gsub(/\\357\\273\\277/)` is locale-dependent: under UTF-8 those escapes are
    CHARACTERS, so the BOM survived, near() never matched, and the documented
    "BOM-prefixed" cause could never print."""
    bom = b"\xef\xbb\xbf"
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_bytes(bom + START.encode() + b"\nstale\n" + END.encode() + b"\n")

    done = _run(installer, home, project)

    assert done.returncode == 3, done.stdout + done.stderr
    assert "MALFORMED" in done.stderr, done.stderr
    assert "BOM-prefixed" in done.stderr, (
        f"the BOM cause is documented but was not diagnosed:\n{done.stderr}")


def test_a_scratch_dir_failure_reports_a_cause(tmp_path):
    """`tmpdir=$(mktemp -d) || return 1` gave the user no cause at all."""
    # Fail ONLY for `mktemp -d` (the block writer's scratch dir); the same
    # command stages the skill payloads, and a blanket failure would test that
    # path instead of this one.
    stub = "#!/bin/sh\ncase \"$1\" in -d) exit 1 ;; esac\nexec /usr/bin/mktemp \"$@\"\n"
    done, _ = _install_with_stub(tmp_path, "mktemp", stub, LEGACY_BLOCK)

    assert done.returncode == 3, done.stdout + done.stderr
    assert "scratch directory" in done.stderr, (
        f"a failure with no cause is not a diagnosis:\n{done.stderr}")


def test_a_pre_state_copy_failure_names_a_reachable_cause(tmp_path):
    """Review P3: the pre-state copy (the bytes the concurrency check compares
    against) reported a failure as "unreadable, or not a regular file" — and
    "not a regular file" is already excluded by the `[ -f "$target" ]` guard the
    copy sits under, so the one reachable cause besides readability (a full or
    unwritable scratch directory) went unnamed."""
    stub = ("#!/bin/sh\n"
            "case \"$*\" in *pre-state-agents.md*) exit 1 ;; esac\n"
            "exec /bin/cp \"$@\"\n")
    done, agents = _install_with_stub(tmp_path, "cp", stub, LEGACY_BLOCK)

    assert done.returncode == 3, done.stdout + done.stderr
    assert "pre-write copy" in done.stderr, (
        f"the failure must name what it was doing:\n{done.stderr}")
    assert "not a regular file" not in done.stderr, (
        "the message must not name a cause the surrounding [ -f ] guard "
        "excludes:\n" + done.stderr)
    assert agents.read_text(encoding="utf-8") == LEGACY_BLOCK, (
        "a failed pre-state copy must write nothing")


def test_a_file_that_changes_mid_rewrite_is_not_half_written(tmp_path):
    """The marker line numbers are computed from an earlier read and the file is
    re-opened three times afterwards. In this fleet AGENTS.md is exactly the kind
    of file another process writes, and a stale line number makes the split cut
    the WRONG lines while every command still exits 0. The split re-asserts the
    pair it is cutting; a `cmp` stub moves the end marker to prove it."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text(LEGACY_BLOCK, encoding="utf-8")
    before = agents.read_text(encoding="utf-8")

    # `cmp` runs BETWEEN the state read and the split. Make it a hostile writer.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "cmp").write_text(
        "#!/bin/sh\nperl -pi -e 's/tortoise:end/tortoise:ENDED/' AGENTS.md\nexit 1\n", encoding="utf-8")
    (bindir / "cmp").chmod(0o755)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    done = subprocess.run(["bash", str(installer), "--harness", "codex"],
                          cwd=str(project), env=env, capture_output=True, text=True, timeout=300)

    assert done.returncode == 3, f"a moved marker must fail closed:\n{done.stdout}"
    assert "moved while it was being rewritten" in done.stderr, done.stderr
    assert "✓ AGENTS.md" not in done.stdout, done.stdout
    # The block was NOT replaced: only the stubbed mutation is present.
    assert "tortoise:block v" not in agents.read_text(encoding="utf-8")
    assert before.replace("tortoise:end", "tortoise:ENDED") == agents.read_text(encoding="utf-8")


def test_a_tilde_fence_example_is_an_example(tmp_path):
    """`~~~` is a CommonMark code fence. A three-backtick parity toggle did not
    know that, so an example in a tilde fence was DELETED and reported as an
    "upgrade" of a block that never existed."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"~~~markdown\n{START}\nan EXAMPLE the user wrote\n{END}\n~~~\n"
    agents = project / "AGENTS.md"
    agents.write_text(example, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, done.stderr
    text = agents.read_text(encoding="utf-8")
    assert "an EXAMPLE the user wrote" in text, "the user's example was deleted"
    assert example in text, "the tilde-fenced example was modified"
    assert "upgraded" not in _agents_report(done.stdout), "there was no block to upgrade"


def test_a_nested_fence_example_is_an_example(tmp_path):
    """A 4-backtick fence wrapping a 3-backtick example: parity closed at the
    wrong line, so one marker was counted and the other skipped — a false
    MALFORMED refusal, with a remedy about a file that has no block."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"````markdown\n{START}\n```\n{END}\n```\n````\n"
    agents = project / "AGENTS.md"
    agents.write_text(example, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, f"a nested fence example was refused:\n{done.stderr}"
    text = agents.read_text(encoding="utf-8")
    assert example in text, "the nested example was modified"
    assert text.count(START) == 2, "expected the example plus the created block"


def test_an_indented_fence_example_is_an_example(tmp_path):
    """CommonMark allows up to 3 spaces before the fence; the toggle's `[ \\t]*`
    is what makes that work, and only the language tag half was pinned."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = f"   ```md\n{START}\n   ...\n{END}\n   ```\n"
    agents = project / "AGENTS.md"
    agents.write_text(example, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, f"an indented fence was not a fence:\n{done.stderr}"
    text = agents.read_text(encoding="utf-8")
    assert example in text, "the indented-fence example was modified"


def test_an_unterminated_fence_is_diagnosed_as_a_fence(tmp_path):
    """The fallback produced these counts, so "an older installer ran twice" is
    not what happened — and its remedy ("delete from the FIRST start marker
    through the LAST end marker") deletes the user's REAL block."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    second = f"```md\n{START}\nan example\n{END}\n"
    agents.write_text(LEGACY_BLOCK + "\n" + second, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 3, done.stdout + done.stderr
    assert "UNTERMINATED" in done.stderr, done.stderr
    assert "ran twice" not in done.stderr, "the cause is a fence, not a double run"
    assert "Delete from the FIRST start marker" not in done.stderr, (
        "that remedy deletes the user's real block")
    assert agents.read_text(encoding="utf-8") == LEGACY_BLOCK + "\n" + second, (
        "a refusal must leave the file byte-identical")


def test_the_classifier_runs_under_the_C_locale():
    """`LC_ALL=C` is what makes the byte-level BOM strip work on gawk under a
    UTF-8 locale (`sprintf("%c", 239)` there yields the 2-byte encoding of
    U+00EF). Not reproducible on this host's BSD awk, which is why the guard is
    pinned at the source rather than through behaviour."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'state="$(LC_ALL=C awk' in text, (
        "the state classifier must run under the C locale for the byte BOM strip")


def test_a_language_tagged_line_inside_a_fence_does_not_close_it(tmp_path):
    """A closing fence may carry nothing but spaces/tabs after the run; trailing
    text is legal only on the OPENING fence. Treating a ```` ```bash ```` line
    INSIDE a fence as a close ended the example early, counted the rest of it, and
    produced a false "ran twice" whose remedy deletes the real block AND the docs."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    # A THREE-tick fence containing a THREE-tick language-tagged line: only the
    # trailing-text rule keeps the fence open here.
    example = ("```markdown\n```bash\n" + START + "\nnpm run build\n" + END + "\n```\n")
    agents = project / "AGENTS.md"
    agents.write_text(example, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, (
        f"a language-tagged line inside a fence closed the fence:\n{done.stderr}")
    assert "ran twice" not in done.stderr, done.stderr
    text = agents.read_text(encoding="utf-8")
    assert example in text, "the documentation example was modified"
    assert text.count(START) == 2, "expected the example plus the created block"


def test_a_block_replaced_BELOW_an_unterminated_fence_still_warns(tmp_path):
    """Review P1 on my own P3 fix: silencing the warning was wrong in this state.

    A clean pair that sits BELOW an unterminated fence IS replaced inside the
    fence — it renders as code and is never read — so the warning is TRUE there.
    The narrow condition (`state_kind = none`) suppressed it; the condition is
    now "the block's region lies after the fence opened", which covers an append
    at EOF and a pair below the fence, and excludes a pair above it.
    """
    agents = (f"# House\n\n```bash\nnpm run build\n\n{START}\nold block\n{END}\n")
    done, text = _install(tmp_path, agents)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "inside it and read as code" in done.stderr, (
        "a block BELOW an unclosed fence is written inside it and must warn:\n"
        + done.stderr)
    assert "old block" not in text, "the stale block is still replaced in place"


def test_an_unterminated_fence_does_not_warn_when_the_block_is_replaced_in_place(
        tmp_path):
    """Review P3: the warning claimed a fate the block does not have.

    With a CLEAN pair ABOVE an unclosed fence the block is rewritten where it
    already sits — it is not "written inside" the fence. Warning anyway told the
    user their (perfectly placed) block was unreadable, which is the same class
    of false statement as a docs claim, one layer down.
    """
    agents = f"# House\n\n{START}\nold block\n{END}\n\n```bash\nnpm run build\n"
    done, text = _install(tmp_path, agents)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "inside it and read as code" not in done.stderr, (
        "a block replaced IN PLACE above the fence is not written inside it:\n"
        + done.stderr)
    assert START in text
    assert "old block" not in text, "the stale block must be replaced"


def test_a_read_only_agents_md_can_still_be_upgraded_and_keeps_its_mode(
        tmp_path):
    """Review P3: `cp -p` copied the target's mode onto the staged file.

    A 0444 AGENTS.md therefore could not be upgraded although the directory and
    the `mv` were writable, and the failure named no cause. The upgrade must
    work AND the read-only mode must survive it — loosening a user's file mode
    silently is its own defect.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    target.write_text(f"# House\n\n{START}\nold\n{END}\n", encoding="utf-8")
    target.chmod(0o444)

    done = _run(installer, home, project)
    text = target.read_text(encoding="utf-8")

    assert done.returncode == 0, (
        "a read-only AGENTS.md in a writable directory must be upgradeable:\n"
        + done.stdout + done.stderr)
    assert "old\n" not in text, "the stale block must be replaced"
    assert (target.stat().st_mode & 0o777) == 0o444, (
        "the upgrade must not loosen the user's file mode: got "
        f"{oct(target.stat().st_mode & 0o777)}")


def test_a_block_written_inside_an_unterminated_fence_is_not_silent(tmp_path):
    """The write matches the pre-#3429 installer, so it is not refused — but a
    block inside an open fence renders as code and is never read, so it must warn."""
    done, text = _install(tmp_path, "# House\n\n```bash\nnpm run build\n")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "UNTERMINATED code fence" in done.stderr, (
        f"a block written inside an open fence must not be silent:\n{done.stderr}")
    assert text.count(START) == 1, "the block IS written (unchanged behaviour)"


def test_a_pair_that_straddles_an_unterminated_fence_is_refused(tmp_path):
    """Review P2: the block's END marker below the fence line puts the fence
    INSIDE the replace window, so rewriting the block deletes the fence line and
    promotes whatever it was quoting to live instructions.

    Measured against the condition that warned here: a build snippet inside the
    fence vanished and the trailing line became prose, while the run reported
    "✓ upgraded". Destroying a user's code block while reporting success cannot be
    fixed by warning, so this case is refused and nothing is written.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    original = f"# House\n\n{START}\nold\n\n```bash\nnpm run build\n{END}\nAFTER\n"
    target.write_text(original, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 3, (
        "a straddling pair must be refused, not rewritten:\n"
        + done.stdout + done.stderr)
    assert "STRADDLE" in done.stderr, done.stderr
    assert target.read_text(encoding="utf-8") == original, (
        "nothing may be written when the block's window contains the fence line")
    assert not list(project.glob(".tortoise-agents.*")), (
        "no staged file may be left behind — the refusal must not stage at all")


def test_a_read_only_agents_md_with_no_block_still_gets_one(tmp_path):
    """Review P2: the append path wrote with `>>` straight onto the user's file,
    so a 0444 AGENTS.md got bash's "Permission denied" and no named cause, while
    the replace path handled read-only files. Both paths must behave the same."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    target.write_text("# House\n", encoding="utf-8")
    target.chmod(0o444)

    done = _run(installer, home, project)
    text = target.read_text(encoding="utf-8")

    assert done.returncode == 0, (
        "a read-only AGENTS.md with no block must still get one:\n"
        + done.stdout + done.stderr)
    assert text.count(START) == 1, "the block must be appended"
    assert text.startswith("# House\n"), "the user's content must be preserved"
    assert (target.stat().st_mode & 0o777) == 0o444, (
        f"the read-only mode must survive: got {oct(target.stat().st_mode & 0o777)}")


@pytest.mark.parametrize("existing_block", [True, False], ids=["replace", "append"])
def test_an_unreadable_mode_refuses_rather_than_loosening_permissions(
        tmp_path, existing_block):
    """Review P3: the mode restore was `chmod … 2>/dev/null || true`, so a failed
    `stat` silently loosened a 0444 file to the umask default and still reported
    success. Fail closed instead: the user's mode is not ours to drop."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    # BOTH write paths must fail closed: with a block it takes the replace path,
    # without one it takes the append path, and each reads the mode itself.
    original = (f"# House\n\n{START}\nold\n{END}\n" if existing_block
                else "# House\n")
    target.write_text(original, encoding="utf-8")
    target.chmod(0o444)
    # A `stat` that always fails: both the GNU and the BSD form must be defeated.
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "stat"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)

    env_path = f"{shim}:{os.environ.get('PATH', '')}"
    done = subprocess.run(
        ["bash", str(installer), "--harness", "codex"],
        cwd=str(project), env={**os.environ, "HOME": str(home), "PATH": env_path},
        capture_output=True, text=True, timeout=180)

    assert done.returncode == 3, (
        "an unreadable mode must refuse rather than rewrite with looser "
        f"permissions:\n{done.stdout}{done.stderr}")
    assert "could not read the mode" in done.stderr, done.stderr
    assert target.read_text(encoding="utf-8") == original, "nothing may be written"
    assert (target.stat().st_mode & 0o777) == 0o444, "the mode must be untouched"


@pytest.mark.parametrize("existing_block", [True, False], ids=["replace", "append"])
@pytest.mark.parametrize("read_only", [False, True], ids=["rw", "ro"])
def test_a_hardlinked_alias_sees_the_block(tmp_path, existing_block, read_only):
    """Review P2/P1: BOTH write paths went from in-place writing to `cp` + `mv`,
    which REPLACES the inode. A hardlinked alias of AGENTS.md — `ln AGENTS.md
    CLAUDE.md` is a common way to dual-name agent files — kept the pre-run bytes
    and silently diverged, while the report said the block was created/upgraded.

    Measured on the replace path: inode split, the alias still held the stale v1
    block, and the run reported "✓ upgraded". Measured on the append path: the
    alias had no block at all.

    The body carries a UNIQUE line and the assertion is EXACT: an earlier version
    of this test passed against an arm that appended the whole staged copy (i.e.
    duplicated the user's file) because it counted markers instead of comparing
    content.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    body = "# House\nUNIQUE-LINE-ONE\n"
    original = f"{body}\n{START}\nold block\n{END}\nTAIL\n" if existing_block else body
    target.write_text(original, encoding="utf-8")
    if read_only:
        # The in-place arms chmod the target and must put the mode back; without a
        # 0444 case that restore is a no-op and cannot be observed (review: deleting
        # it left every test green while a read-only file came back 0644).
        target.chmod(0o444)
    alias = project / "CLAUDE.md"
    alias.hardlink_to(target)

    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    text = target.read_text(encoding="utf-8")
    assert target.stat().st_ino == alias.stat().st_ino, (
        "the install must not split a hardlinked alias")
    assert alias.read_text(encoding="utf-8") == text, "the alias must hold the same bytes"
    assert text.count("UNIQUE-LINE-ONE") == 1, (
        "the user's content must appear exactly once — appending the whole staged "
        f"copy duplicates it:\n{text[:400]}")
    assert text.count("# House") == 1, "the original body must not be duplicated"
    assert text.count("tortoise:block v") == 1, "the alias must see the upgraded block"
    assert "old block" not in text, "the stale block must be replaced on both paths"
    assert text.count(START) == 1 and text.count(END) == 1, "exactly one block"
    if existing_block:
        assert text.endswith("TAIL\n"), "content after the block must survive"
    if read_only:
        assert (target.stat().st_mode & 0o777) == 0o444, (
            "an in-place write must restore the read-only mode: got "
            f"{oct(target.stat().st_mode & 0o777)}")


def test_a_failed_in_place_rewrite_restores_the_original(tmp_path):
    """Review P2: the in-place arm truncates the ONLY copy, and `_emit_fail` wipes
    the tmpdir — so a failed write destroyed the user's file. Measured with a `cat`
    shim writing 4 bytes and exiting 1: the file came back as "part", with the
    original text, the block and the trailer all gone, while the message only said
    "re-run after checking the file".

    The arm now keeps a copy first and restores it, so this asserts the EXACT
    original bytes are back AND that the failure says so — a rollback that silently
    "worked" would leave the user unable to tell what happened.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    original = f"# House\nUNIQUE-ONE\n\n{START}\nold block\n{END}\nTAIL\n"
    target.write_text(original, encoding="utf-8")
    target.chmod(0o444)
    alias = project / "CLAUDE.md"
    alias.hardlink_to(target)
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "cat"
    # The in-place rewrite is the ONE-argument `cat` whose argument is the staged
    # temp file; by the time it runs, the shell has already truncated the target.
    # Fail there and nowhere else, so the rollback (which cats the BACKUP file) is
    # not intercepted.
    fake.write_text(
        '#!/bin/sh\n'
        'if [ $# -eq 1 ]; then\n'
        '  case "$1" in\n'
        '    *.tortoise-agents.*) printf "PARTIAL" ; exit 1 ;;\n'
        '  esac\n'
        'fi\n'
        'exec /bin/cat "$@"\n',
        encoding="utf-8")
    fake.chmod(0o755)

    done = subprocess.run(
        ["bash", str(installer), "--harness", "codex"], cwd=str(project),
        env={**os.environ, "HOME": str(home), "PATH": f"{shim}:{os.environ['PATH']}"},
        capture_output=True, text=True, timeout=180)

    assert done.returncode == 3, (
        "the injected write failure must be reported:\n" + done.stdout + done.stderr)
    assert "RESTORED" in done.stderr, (
        "the failure must say the original was restored:\n" + done.stderr)
    # The message is not evidence: the first version printed RESTORED and then
    # deleted the very backup it named. Require the bytes below AND that no
    # "could NOT be restored" line appears.
    assert "could NOT be restored" not in done.stderr, done.stderr
    assert target.read_text(encoding="utf-8") == original, (
        "the original bytes must be back, verbatim")
    assert (target.stat().st_mode & 0o777) == 0o444, (
        f"the read-only mode must be restored: {oct(target.stat().st_mode & 0o777)}")
    assert target.stat().st_ino == alias.stat().st_ino, "the alias must not be split"


def test_a_signal_is_reported_as_a_failure_not_a_success(tmp_path):
    """Review P1: the trap restored the bytes and RETURNED, so execution fell
    through into the success report. Measured: rc 0 and a "✓ … upgraded" line over
    an untouched file — a silent lie.

    A TERM during the in-place write must exit non-zero and leave the file alone.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    original = f"# House\nUNIQUE-ONE\n\n{START}\nold block\n{END}\nTAIL\n"
    target.write_text(original, encoding="utf-8")
    # 0444 matters here: it is what makes the in-place window OBSERVABLE (the arm
    # chmods it writable, and that transition is the signal to send TERM). Without
    # it the test killed the installer during skill installation and proved nothing
    # (observed rc=-15, empty output, original bytes untouched).
    target.chmod(0o444)
    alias = project / "CLAUDE.md"
    alias.hardlink_to(target)
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "cat"
    # Slow ONLY the in-place rewrite, so the signal lands inside the window.
    fake.write_text(
        '#!/bin/sh\n'
        'if [ $# -eq 1 ]; then\n'
        '  case "$1" in\n'
        '    *.tortoise-agents.*) sleep 4 ;;\n'
        '  esac\n'
        'fi\n'
        'exec /bin/cat "$@"\n',
        encoding="utf-8")
    fake.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", str(installer), "--harness", "codex"], cwd=str(project),
        env={**os.environ, "HOME": str(home), "PATH": f"{shim}:{os.environ['PATH']}"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Wait for the WINDOW rather than guessing a duration: the mode only changes
    # once the in-place arm has made the file writable, which is the state the
    # signal must recover from. A fixed sleep raced the skill installation and
    # sometimes landed before the window (observed: mode 644 left behind because
    # the signal arrived while the trap was not yet installed).
    in_window = False
    for _ in range(120):
        if proc.poll() is not None:
            break
        staged = list(project.glob(".tortoise-agents.*"))
        if (target.stat().st_mode & 0o777) != 0o444 and staged:
            in_window = True
            break
        time.sleep(0.25)
    assert in_window, "the in-place window never opened; the test proves nothing"
    proc.terminate()
    out, err = proc.communicate(timeout=120)

    assert proc.returncode != 0, (
        "an interrupted write must not exit 0:\n" + out + err)
    # The skills above legitimately print ✓, so the check is on the AGENTS.md
    # claim specifically — that is the line that lied.
    assert "AGENTS.md" not in out or "upgraded" not in out, (
        "an interrupted write must not report the AGENTS.md block as upgraded:\n" + out)
    assert target.read_text(encoding="utf-8") == original, (
        "the original bytes must be restored")
    assert (target.stat().st_mode & 0o777) == 0o444, (
        "the mode must be restored: got "
        f"{oct(target.stat().st_mode & 0o777)}\nrc={proc.returncode}\n{out}\n{err}")
    assert not list(project.glob(".tortoise-agents.*")), (
        "a verified restore must not leave its staging file behind — an untracked "
        "`.tortoise-agents.*` is one `git add -A` from being committed")


def test_a_failed_restore_keeps_the_backup_and_does_not_claim_success(tmp_path):
    """Review P1: the restore's status was swallowed with `2>/dev/null`, the
    message said RESTORED unconditionally, and `_emit_fail` then deleted the tmpdir
    holding the only intact copy. Measured: a 1-byte target, the backup gone, and a
    claim that it had been restored.

    When the restore cannot be verified the installer must say so and LEAVE the
    intact copy, naming where it is.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    original = f"# House\nUNIQUE-ONE\n\n{START}\nold block\n{END}\nTAIL\n"
    target.write_text(original, encoding="utf-8")
    alias = project / "CLAUDE.md"
    alias.hardlink_to(target)
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "cat"
    # Fail BOTH the in-place write and the restore of the backup: the same I/O
    # fault, which is what makes an unverified restore dangerous.
    fake.write_text(
        '#!/bin/sh\n'
        'if [ $# -eq 1 ]; then\n'
        '  case "$1" in\n'
        '    *.tortoise-agents.*|*orig-agents.md)\n'
        '      case "$1" in *orig-agents.md) printf "X" ; exit 1 ;; esac\n'
        '      printf "PARTIAL" ; exit 1 ;;\n'
        '  esac\n'
        'fi\n'
        'exec /bin/cat "$@"\n',
        encoding="utf-8")
    fake.chmod(0o755)

    done = subprocess.run(
        ["bash", str(installer), "--harness", "codex"], cwd=str(project),
        env={**os.environ, "HOME": str(home), "PATH": f"{shim}:{os.environ['PATH']}"},
        capture_output=True, text=True, timeout=180)

    assert done.returncode == 3, done.stdout + done.stderr
    assert "could NOT be restored" in done.stderr, (
        "an unverified restore must not be reported as a success:\n" + done.stderr)
    assert "RESTORED" not in done.stderr.replace("could NOT be restored", ""), (
        "the message must not also claim the restore succeeded:\n" + done.stderr)
    # The backup lives in the installer's OWN mktemp dir, so it is found by
    # reading the path out of the message rather than by guessing a location.
    match = re.search(r"An intact copy is kept at: (\S+)", done.stderr)
    assert match, "the message must name where the copy is:\n" + done.stderr
    backup = Path(match.group(1))
    assert backup.exists(), f"the named copy must exist: {backup}"
    assert backup.read_text(encoding="utf-8") == original, (
        "the surviving copy must hold the original bytes")


def test_a_signal_whose_restore_fails_still_reports_and_keeps_the_backup(tmp_path):
    """Review P1: a trap handler is NOT errexit-exempt, so a failing restore inside
    `on_signal` killed the shell at that line — rc 1, EMPTY stderr, the mode left
    loosened from 0444 and the backup stranded in an unnamed mktemp dir (measured).

    The signal path must report like the ordinary failure path: say the original
    could NOT be restored, name the surviving copy, restore the mode, exit 143.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    original = f"# House\nUNIQUE-ONE\n\n{START}\nold block\n{END}\nTAIL\n"
    target.write_text(original, encoding="utf-8")
    target.chmod(0o444)
    alias = project / "CLAUDE.md"
    alias.hardlink_to(target)
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "cat"
    # Slow the in-place write (so the signal lands inside the window) AND fail the
    # restore of the backup, which is the combination that killed the shell.
    fake.write_text(
        '#!/bin/sh\n'
        'if [ $# -eq 1 ]; then\n'
        '  case "$1" in\n'
        '    *.tortoise-agents.*) sleep 4 ; exit 1 ;;\n'
        '    *orig-agents.md) printf "X" ; exit 1 ;;\n'
        '  esac\n'
        'fi\n'
        'exec /bin/cat "$@"\n',
        encoding="utf-8")
    fake.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", str(installer), "--harness", "codex"], cwd=str(project),
        env={**os.environ, "HOME": str(home), "PATH": f"{shim}:{os.environ['PATH']}"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    in_window = False
    for _ in range(120):
        if proc.poll() is not None:
            break
        if (target.stat().st_mode & 0o777) != 0o444 and list(project.glob(".tortoise-agents.*")):
            in_window = True
            break
        time.sleep(0.25)
    assert in_window, "the in-place window never opened; the test proves nothing"
    proc.terminate()
    out, err = proc.communicate(timeout=180)

    assert proc.returncode == 143, (
        f"an interrupted write must exit 143, not {proc.returncode}:\n{out}\n{err}")
    assert "interrupted while writing it in place" in err, (
        "the signal path must say what happened:\n" + err)
    assert "could NOT be restored" in err, (
        "a failed restore must not be silent:\n" + err)
    # The staged copy is not the recovery artifact — $backup is, and it is named
    # below — and it is the one leftover that lands in the USER's repo (the same
    # reason the verified-restore branch removes it).
    assert not list(project.glob(".tortoise-agents.*")), (
        "the staged copy must not be left in the project as an untracked file "
        f"one `git add -A` from being committed: {list(project.glob('.tortoise-agents.*'))}")
    assert (target.stat().st_mode & 0o777) == 0o444, (
        "the mode must be restored even when the bytes cannot be: got "
        f"{oct(target.stat().st_mode & 0o777)}")
    match = re.search(r"An intact copy is kept at: (\S+)", err)
    assert match, "the surviving copy must be named:\n" + err
    assert Path(match.group(1)).exists(), "the named copy must exist"
    assert "AGENTS.md" not in out or "upgraded" not in out, (
        "an interrupted write must not report the AGENTS.md block as upgraded:\n" + out)


def test_the_append_path_refuses_when_a_concurrent_run_wrote_the_block(tmp_path):
    """Review P2: renaming made the last writer win, but appending IN PLACE adds a
    SECOND block pair — measured: two parallel runs on a hardlinked, block-less
    file, both printing success, leaving `starts=2 ends=2` and a file the next run
    refuses as MALFORMED. A block that appears between the pre-state read and the
    append must therefore abort instead of duplicating.

    The state is injected deterministically: the shim writes a block into the
    target the moment the staged copy is read back, i.e. inside the window.
    """
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "AGENTS.md"
    original = "# House\nUNIQUE-ONE\n"
    target.write_text(original, encoding="utf-8")
    alias = project / "CLAUDE.md"
    alias.hardlink_to(target)
    shim = tmp_path / "shim"
    shim.mkdir()
    fake = shim / "cat"
    # When the append arm cats the block, first plant a competing block in the
    # target — the exact effect of a parallel run winning the race.
    fake.write_text(
        '#!/bin/sh\n'
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        '    */block)\n'
        '      printf "\\n\\n%s\\nother run\\n%s\\n" "<!-- tortoise:start -->" "<!-- tortoise:end -->" >> "$AGENTS_RACE_TARGET"\n'
        '      ;;\n'
        '  esac\n'
        'done\n'
        'exec /bin/cat "$@"\n',
        encoding="utf-8")
    fake.chmod(0o755)

    done = subprocess.run(
        ["bash", str(installer), "--harness", "codex"], cwd=str(project),
        env={**os.environ, "HOME": str(home), "PATH": f"{shim}:{os.environ['PATH']}",
             "AGENTS_RACE_TARGET": str(target)},
        capture_output=True, text=True, timeout=180)

    text = target.read_text(encoding="utf-8")
    assert done.returncode == 3, (
        "a block appearing mid-write must abort, not duplicate:\n" + done.stdout + done.stderr)
    assert "changed while this one was running" in done.stderr, done.stderr
    assert text.count(START) == 1 and text.count(END) == 1, (
        f"exactly one block must remain, got {text.count(START)}/{text.count(END)}:\n{text[:300]}")
    assert "UNIQUE-ONE" in text, "the user's content must survive"
