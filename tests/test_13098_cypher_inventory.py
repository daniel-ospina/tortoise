"""#13098 — the Cypher census and its differential harness, pinned.

WHAT THIS SUITE IS FOR
======================
`docs/architecture/CYPHER-INVENTORY.src.md` publishes counts — the tracked SOURCE,
whose 25 pins `--check` grades; `CYPHER-INVENTORY.md` beside it is `--render`'s
gitignored output — and a published count without a gate is a number that rots the
first time a statement is added. Two
things are therefore pinned here:

1. **The census reconciles and the doc matches it** — `tools/cypher_inventory.py
   --check` re-runs the enumeration and compares every pinned count; drift is a
   red, not a footnote. ⚠️ Those doc-vs-tree arms are `@requires_doc_pin`: they
   do NOT gate a merge (#2148/B1) and run at full strength in the path-filtered,
   non-required `cypher-diff` job — see the block comment below the imports for
   the measured reason (a ten-PR re-cut chain from one product move).
2. **The classifier's precedence is the thing that decides a verdict**, so it is
   tested directly rather than through the doc: a statement that needs a walk and
   also matches "plain" must be `traversal`; a fulltext QUERY must not be
   swallowed by the DDL arm (the first version of that regex ate every
   `CALL db.idx.*` and hid the whole class); a Postgres `CREATE TABLE` must not be
   counted as Cypher.

⛔ AND THE HARNESS MUST BE ABLE TO REFUSE. A differential harness whose only
observable outcome is "empty diff" is the vacuous-pin failure this epic has hit
repeatedly (#11859). `tools/cypher_diff_harness.py` therefore has an exit code
for "could not run" (2), and these tests pin that it is used: no lanes, an
inapplicable mutation, and a corpus that lacks a declared edge all refuse rather
than report agreement.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import cypher_diff_harness as harness  # noqa: E402
from tools import cypher_inventory as census  # noqa: E402

# --------------------------------------------------------------------------- #
# THE DOCUMENT IS RENDERED, NOT COMMITTED (#13098)
# --------------------------------------------------------------------------- #
# `docs/architecture/CYPHER-INVENTORY.src.md` is the hand-authored SOURCE — the
# prose, the 25 pins — and the only TRACKED file. `docs/architecture/
# CYPHER-INVENTORY.md` is `--render`'s OUTPUT: the source plus one `file:line`
# row per statement site, appended from the live tree, and it is GITIGNORED.
#
# ⛔ WHY. The appendix's rows carry a line number each, so any product commit
# that moves a statement restamps every later row. While that output was
# COMMITTED, every such PR had to carry its own regenerated copy —
# measured 2026-11-24 at `939e37f664`: 8 of 11 open PRs held an edited copy, it
# was in 6 of 6 then-conflicting PRs, and 3 of the last 20 commits on `main`
# regenerated it. A committed generated artifact plus N open branches is a
# guaranteed conflict on every merge round.
#
# ⛔ WHAT IS PRESERVED, EXACTLY. The pin/prose arms grade the SOURCE; the appendix
# arms grade a freshly RENDERED document — the same prose, and the same rows a
# committed copy carried — and `--check` still reads the same 25 pins. A census
# that disagrees with the tree still reds (proved on this change: one added
# statement site reds `--check` and the published-decomposition arm). What went
# away is only the obligation to COMMIT a regenerated copy.
DOC_SRC = ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.src.md"


def _source_text() -> str:
    """The tracked SOURCE: prose + the 25 pins, and NOT one census row."""
    return DOC_SRC.read_text(encoding="utf-8")


def _document_text() -> str:
    """The DOCUMENT: the source + THIS run's appendix, rendered from the tree."""
    return census.render(DOC_SRC, census.census(("tortoise",)))

# --------------------------------------------------------------------------- #
# WHERE THE DOC-VS-TREE PIN RUNS (#2148/B1)
# --------------------------------------------------------------------------- #
# The arms below marked `@requires_doc_pin` grade the census against the live tree,
# and they split by WHAT they read (#13098): SIX read the tracked SOURCE — three the
# 25 pins (`--check`, the drift arm, the `files_scanned` arm), three its published
# prose (the decomposition, the narrative pin, the anchored-arm coverage) — and
# THREE involve a render (the two appendix arms render to `tmp_path` through a
# subprocess; the table-shape arm renders in-process). ⛔ NOT "the committed
# document": after the split there is none, and the two SOURCE halves are not
# interchangeable — `check()` reads only the HTML pins, so the prose arms are the
# only grading §0/§1's prose tables get. Any product commit can move those counts,
# so in the
# pre-merge matrix (where this file is `core`-registered and rides the REQUIRED
# `python-ci-gate`) each of them redded PRs that touched neither the census tool
# nor its doc — #14296 reconciled NINE open PRs / 115 files over exactly this and
# still failed, and #13879 → #14088 → #14087 → #14064 → #13992 → #13887 → #13755 →
# #13750 → #13746 → #13589 is a ten-PR chain from one product move.
#
# The pin is therefore DECOUPLED from the merge gate and kept at full strength:
# `TORTOISE_CYPHER_DOC_PIN=1` is set ONLY by `.github/workflows/cypher-diff.yml`,
# which is not a required context AND is already path-filtered to exactly the
# diffs that can move a count (`tortoise/**`, `supabase/migrations/**`,
# `tools/cypher_inventory.py`, `tools/cypher_diff_harness.py`,
# `docs/architecture/CYPHER-INVENTORY.src.md`, this file). Nothing was deleted: the
# arms, the tool and `cypher_inventory.py --check` all still run there, and a
# genuinely wrong census still reds a job — just not somebody else's merge.
#
# The classifier/harness arms below stay UNGATED: they pin behaviour (precedence,
# diff semantics, the refusal exit codes), not a number that rots with the tree.
_DOC_PIN_ENV = "TORTOISE_CYPHER_DOC_PIN"
requires_doc_pin = pytest.mark.skipif(
    os.environ.get(_DOC_PIN_ENV) != "1",
    reason=(
        f"doc-vs-tree census pin: DECOUPLED from the required merge gate by "
        f"#2148/B1 — a count that moves with any product commit must not block an "
        f"unrelated PR. It runs at full strength in the path-filtered `cypher-diff` "
        f"job, which sets {_DOC_PIN_ENV}=1 (see the block comment above)."
    ),
)


# --------------------------------------------------------------------------- #
# the census
# --------------------------------------------------------------------------- #
@requires_doc_pin
def test_the_doc_counts_match_the_census():
    """The pinned counts in CYPHER-INVENTORY.src.md are the census's own numbers."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cypher_inventory.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"census drift:\n{proc.stdout}\n{proc.stderr}"


@requires_doc_pin
def test_the_doc_appendix_is_the_tools_own_output(tmp_path):
    """Appendix A is DERIVED from the tree, so the doc must carry the tool's output.

    ⛔ THIS ARM EXISTS BECAUSE THE PINS DO NOT REACH THE APPENDIX, and the gap is
    not theoretical — it cost this document the same defect twice in ONE PR
    (`#13223`). The 25 pins are SCALARS (`sites`, `shape_plain`, …): `--check`
    prints "25 pinned counts match" while §Appendix A's ~1,309 `file:line` rows
    name lines that no longer exist, because a later commit moved code inside a
    file that already carried sites. The counts do not move (an insertion is not a
    new statement) so EVERY gate in this suite stays green over a table whose
    whole purpose is to be a resolvable index.

    Measured twice on `#13223`: 644 of 1,309 rows wrong after `#13116`
    (`hosted_api.py` +131, `sdk.py` net +72), and 6 more after `#13214` moved
    `tortoise/metering.py` by +7 and `tortoise/postgres_bytes.py` by +4 — while
    `--check` was RC=0 both times.

    Why a byte comparison rather than sampling: the appendix is `--markdown`'s
    output verbatim (`tools/cypher_inventory.py`, one row per site, sorted by
    `(path, line)`), so any difference at all is a row the tree no longer
    supports. Sampling cannot say that — a sample of 12 passed on the run that
    still carried the 6 stale rows.
    """
    marker = "## Appendix A — every statement site, by shape\n"
    # ⛔ RENDERED IN A SEPARATE PROCESS FROM THIS ARM'S OWN CENSUS CALL, on
    # purpose: the teeth here are that the FILE `--render` writes carries every
    # row, in order, exactly once — a truncated, duplicated or mis-ordered write
    # reds even though `markdown_table` itself is fine.
    out = tmp_path / "CYPHER-INVENTORY.md"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cypher_inventory.py"),
         "--render", str(out)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"--render refused:\n{proc.stdout}\n{proc.stderr}"
    doc = out.read_text(encoding="utf-8")
    assert marker in doc, (
        "the rendered document no longer carries Appendix A's heading — this arm\n"
        "reads the table from that heading, so re-point it rather than deleting it")
    # ⛔ AND THE SOURCE CARRIES NO ROW ITSELF. A row pasted back into the tracked
    # file is the conflict this split removed, returning silently.
    assert not census.CENSUS_ROW.search(_source_text()), (
        f"{DOC_SRC.relative_to(ROOT)} carries a census row — the appendix is "
        "GENERATED: delete the rows and let `--render` append them")
    appendix = doc[doc.index(marker) + len(marker) + 1:]
    result = census.census(("tortoise/",))
    expected = census.markdown_table(result["sites"]) + "\n"
    if appendix == expected:
        return
    head_rows = appendix.splitlines()
    tool_rows = expected.splitlines()
    stale = [(i, a, b) for i, (a, b) in enumerate(zip(head_rows, tool_rows, strict=False))
             if a != b]
    shown = "\n".join(
        f"  row {i}: doc  {a[:96]}\n          tool {b[:96]}" for i, a, b in stale[:10])
    if len(stale) > 10:
        shown += f"\n  … and {len(stale) - 10} more differing row(s)"
    if not shown:
        # No COMMON row differs, so the difference is in length (a truncation or a
        # tail insertion) or in trailing bytes only. ⛔ These three cases are stated
        # SEPARATELY: an earlier cut printed "same length, differs only in trailing
        # bytes" unconditionally, which contradicted the row counts printed directly
        # above it on a truncation (measured: doc=1310 tool=1313 printed that line).
        if len(head_rows) != len(tool_rows):
            shown = (f"  no shared row differs — the appendix is the wrong LENGTH "
                     f"(doc={len(head_rows)}, tool={len(tool_rows)} rows)")
        else:
            shown = "  same row count and every shared row matches — differs only in trailing bytes"
    raise AssertionError(
        "the rendered Appendix A is not the census tool's own output — it is\n"
        "DERIVED from the tree, so the RENDERER (or the source it appends to) is\n"
        "what is wrong; no row is ever edited by hand:\n"
        "    python tools/cypher_inventory.py --render\n"
        "A row pasted back into `CYPHER-INVENTORY.src.md` is REFUSED — exit 2 by\n"
        "`--render`, exit 1 (naming the rows) by `--check` — the rows belong to\n"
        "the generator.\n"
        f"rows: doc={len(head_rows)} tool={len(tool_rows)}; "
        f"{len(stale)} row(s) differ.\n" + shown)


@requires_doc_pin
def test_the_doc_appendix_row_count_is_the_census_site_count(tmp_path):
    """The appendix's row count is §0's `sites` pin — one row per classified site.

    A cheap second angle on the same artifact: a truncated or duplicated appendix
    still reds here, and the message names the number the document itself publishes.

    ⛔ AND IT GRADES THE FILE `--render` WRITES, not an in-process re-render. The
    first cut of this split read `_document_text()`, which assembles the table from
    `markdown_table()` — one row per site BY CONSTRUCTION — so the arm could not
    fail however broken the renderer was (the vacuity `#11859` names, caught in
    review of this change rather than by it).
    """
    marker = "## Appendix A — every statement site, by shape\n"
    out = tmp_path / "CYPHER-INVENTORY.md"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cypher_inventory.py"),
         "--render", str(out)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"--render refused:\n{proc.stdout}\n{proc.stderr}"
    doc = out.read_text(encoding="utf-8")
    appendix = doc[doc.index(marker) + len(marker) + 1:]
    rows = [ln for ln in appendix.splitlines() if ln.startswith("| `")]
    result = census.census(("tortoise/",))
    assert len(rows) == len(result["sites"]), (
        f"the rendered Appendix A carries {len(rows)} site rows, the census "
        f"classifies {len(result['sites'])} — re-render it with "
        "`python tools/cypher_inventory.py --render` rather than hand-editing")


def test_the_renderer_refuses_a_source_that_carries_the_appendix(tmp_path, capsys):
    """The split's guard: the appendix cannot be pasted back into the source.

    ⛔ WITHOUT THIS THE CONFLICT ENGINE COMES BACK SILENTLY. The obvious way to
    "refresh the census" is to copy `--markdown` into the tracked file — that is
    what the tracked document WAS for eight PRs' worth of merges. `--render`
    therefore REFUSES a source that already carries a census row rather than
    appending a second copy, and `--check` refuses it too: a committed appendix
    is the defect this split removed, and it must fail loudly, not render twice.
    """
    source = _source_text()
    assert not census.CENSUS_ROW.search(source), (
        "the shipped source carries a census row — the appendix is generated")
    rowed = tmp_path / "rowed.src.md"
    rowed.write_text(
        source
        + "| `tortoise/sdk.py:1` | `f` | plain | read | candidate:SQL-shaped | `RETURN 1` |\n",
        encoding="utf-8",
    )
    # ⛔ NO CENSUS HERE, AND THAT IS THE POINT: both refusals return BEFORE they
    # touch the result (verified — `render(rowed, None)` raises 2 and `check(rowed,
    # None)` returns 1), so measuring the tree here would add a full walk (~8 s) to
    # the REQUIRED `python-ci-gate` for a value nothing reads. Passing `None` is
    # deliberate: if a future edit makes either path consult the census, this arm
    # TypeErrors HERE rather than quietly re-adding the cost.
    result = None
    with pytest.raises(SystemExit) as refused:
        census.render(rowed, result)
    # 2 is this tool's "could not run" — never 1, which is the DRIFT verdict the
    # `--check` arm above reserves (`SystemExit(<str>)` would have been 1).
    assert refused.value.code == 2, (
        f"the refusal must exit 2 (could not run), got {refused.value.code!r}")
    assert "carries 1 census row" in capsys.readouterr().err, (
        "the refusal must name the row it found on stderr")
    assert census.check(rowed, result) == 1, (
        "`--check` must refuse a SOURCE that carries the appendix (exit 1), not "
        "grade its pins over a committed copy of the rows")


def test_the_renderer_refuses_the_all_surface_combination(tmp_path):
    """`--render --all-surface` would write a document that contradicts its own pins.

    The source's 25 pins are PRODUCT-surface counts, and `--render` appends one row
    per site under them. The all-surface census is many times wider in rows as well as
    in files, so the pair produced a
    document whose §0 `sites` pin — a product-surface count — sat above an appendix
    several times longer while `--render` printed success — a self-contradictory
    artifact, which is the "false pass in prose" class this file exists to refuse.
    The recorded measurement of the unguarded run — a historical observation, not a
    live count: `render: wrote 10327 lines` when it was taken.

    ⛔ A LOUD REFUSAL, NOT A SILENT SCOPE-NARROWING: a lane that asked for the wide
    census and quietly got the product one would believe it had rendered the wide
    table.
    """
    out = tmp_path / "wide.md"
    for verb in ("--render", "--check"):
        argv = [sys.executable, str(ROOT / "tools" / "cypher_inventory.py"),
                verb] + ([str(out)] if verb == "--render" else []) + ["--all-surface"]
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
        # ⛔ `--check --all-surface` IS THE SAME CONTRADICTION ONE VERB OVER: the
        # pins are product counts, so the wide census can never satisfy them —
        # measured before this guard, it exited 1 with 27 DRIFT keys, a guaranteed
        # false RED reached through the documented verb.
        assert proc.returncode == 2, (
            f"`{verb} --all-surface` must exit 2 (could not run), got "
            f"{proc.returncode}")
        assert "--all-surface" in proc.stderr, (
            f"the refusal must name the flag that contradicts `{verb}`; stderr was:\n"
            + proc.stderr)
        assert not out.exists(), (
            "the refusal must write NOTHING — a half-written wide document is the "
            "artifact the refusal exists to prevent")


def test_an_enumeration_failure_is_could_not_run_never_drift(monkeypatch, tmp_path):
    """`--render`/`--check` answer 2 when the census cannot enumerate the tree.

    `surface_files()` runs `git ls-files … check=True`, so a box with no `git` or no
    index raises — and an uncaught exception exits the interpreter with **1**, the
    code this tool reserves for the DRIFT verdict. `--render` has no drift verdict at
    all, so a lane that could not enumerate would read "the census drifted" and re-cut
    25 pins that were correct; `--check` would report drift about a box that could not
    answer. The probe wrapper closes that for `git ls-files --error-unmatch`; this arm
    closes it for the enumeration, which runs on EVERY path (measured: before the
    guard, `PATH` without `git` gave `FileNotFoundError` and exit 1).

    Both verbs are graded, and the target must not be written — the failure precedes
    the render.
    """
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("No such file or directory: 'git'")

    out = tmp_path / "never.md"
    monkeypatch.setattr(census, "census", boom)
    for argv in (["--render", str(out)], ["--check"], [], ["--markdown"], ["--json"]):
        rc = census.main(argv)
        assert rc == 2, (
            f"`{' '.join(argv) or '(bare census)'}` must exit 2 (could not run), got {rc} — "
            "1 is this tool's DRIFT verdict and would misreport an unreadable tree")
    assert not out.exists(), "the failure precedes the write, so nothing is rendered"
    monkeypatch.undo()

    # ⛔ AND `--json` ENUMERATES TWICE: `rg_totals()` re-reads the index, so a PARTIAL
    # failure (first walk fine, second refused) escaped the first guard and exited 1 —
    # measured with a `git` shim that delegates the first `ls-files` and exits 128 on
    # the second (#13098 review round 12, P2).
    def boom_totals(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "ls-files"])

    monkeypatch.setattr(census, "rg_totals", boom_totals)
    for argv in (["--json"], []):
        assert census.main(argv) == 2, (
            f"`{' '.join(argv) or '(bare census)'}` re-enumerates for `rg_totals` "
            f"(directly, or through `report()`); that second call must answer 2 too")


def test_check_reports_an_unreadable_document_as_could_not_run(tmp_path):
    """A doc that exists but cannot be READ is 2 (could not run), never 1 (drift).

    Same class as the enumeration wrap, in the same verb: an uncaught
    `PermissionError`/`EIO` would exit 1, which this tool reserves for "the pins
    drifted", so a lane would re-cut 25 correct pins over a file it never read.
    """
    doc = tmp_path / "src.md"
    doc.write_text("<!-- cypher-inventory:sites = 1327 -->\n", encoding="utf-8")
    doc.chmod(0o000)
    try:
        rc = census.check(doc, {"sites": []})
    finally:
        doc.chmod(0o600)
    assert rc == 2, f"an unreadable document must answer 2 (could not run), got {rc}"

    # ⛔ AND NOT JUST AN `OSError`: `UnicodeDecodeError` is a ValueError, so it slipped
    # past an `except OSError` in this very guard and exited 1 (measured on a latin-1
    # byte). Same verb, same reserve of the DRIFT code.
    latin1 = tmp_path / "latin1.md"
    latin1.write_bytes("<!-- cypher-inventory:sites = 1327 -->\n".encode("utf-8") + b"\xff\xfe\n")
    assert census.check(latin1, {"sites": []}) == 2, (
        "a document that cannot be DECODED must answer 2 (could not run), not 1 (drift)")


def test_an_unresolvable_render_target_is_could_not_run(tmp_path):
    """A destination that cannot be RESOLVED answers 2, never 1.

    `Path.resolve()` raises `RuntimeError("Symlink loop …")` on ELOOP — a
    `RuntimeError`, not an `OSError` — and the resolve happens before every guard, so
    the traceback escaped and the interpreter exited 1 (this tool's DRIFT code) from
    a verb with no drift verdict. Measured with two symlinks pointing at each other.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    rc = census.main(["--render", str(a / "deep.md")])
    assert rc == 2, (
        f"an unresolvable render target must answer 2 (could not run), got {rc}")


def test_the_renderer_reports_an_unreadable_source_as_could_not_run(tmp_path):
    """`--render` on a source it cannot read answers 2, never 1.

    `--render` has no drift verdict, so an uncaught `PermissionError` (chmod 000) or
    `IsADirectoryError` (a path that exists and is a directory) exits 1 — "the pins
    drifted" — and §7 tells every lane to run this verb. The twin of
    `check()`'s guard, graded here because the two are separate reads.
    """
    unreadable = tmp_path / "src.md"
    unreadable.write_text("<!-- cypher-inventory:sites = 1327 -->\n", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        with pytest.raises(SystemExit) as refused:
            census.render(unreadable, {"sites": []})
        assert refused.value.code == 2, (
            f"an unreadable SOURCE must refuse with 2 (could not run), got "
            f"{refused.value.code!r}")
    finally:
        unreadable.chmod(0o600)

    with pytest.raises(SystemExit) as refused_dir:
        census.render(tmp_path, {"sites": []})   # exists, and is a directory
    assert refused_dir.value.code == 2, (
        f"a directory as the source must refuse with 2, got {refused_dir.value.code!r}")


def test_the_renderer_refuses_to_write_over_the_source(capsys):
    """`--render <the source>` would corrupt the tracked file AND lock the tool.

    `render()`'s own refusal guards the SOURCE'S CONTENT, never the DESTINATION, so
    pointing `--render` at `CYPHER-INVENTORY.src.md` read the clean source, passed
    that refusal, and wrote source + the appendix rows over the tracked file. That is
    self-locking: `--check` then exits 1 (it carries rows) and `--render` exits 2 (it
    refuses a rowed source), so the tool can neither grade nor repair the tree it
    just wrote. §7 tells every lane to run this verb, so a mistyped path is the
    plausible route to the conflict this split exists to remove.

    In-process and cheap: the guard sits BEFORE the census, so this never walks the
    tree; the source is compared byte-for-byte to prove nothing was written.
    """
    before = DOC_SRC.read_bytes()
    rc = census.main(["--render", DOC_SRC.relative_to(ROOT).as_posix()])
    assert rc == 2, f"`--render <source>` must exit 2, got {rc}"
    assert DOC_SRC.read_bytes() == before, (
        "the tracked SOURCE was rewritten — the guard must refuse before writing")
    # ⛔ ASSERT THE BRANCH, NOT JUST THE EXIT CODE (#13098 round 3): the source is
    # ALSO tracked, so `git ls-files` refuses it too — an exit-2-only assertion
    # therefore stays green if the `target == DOC_SRC` branch is DELETED, and that
    # branch is the one that still holds where `git ls-files` cannot answer at all:
    # an UNTRACKED source, or a tree with no index (exit 128). ⛔ NOT the
    # modify/delete case — measured in a scratch repo, an index with a `UD` path
    # makes `git ls-files --error-unmatch` print the path at both stages and exit 0,
    # so THAT state is caught by the tracked-path branch above.
    assert "refusing to render over the SOURCE" in capsys.readouterr().err, (
        "refused, but by the TRACKED-PATH branch rather than the SOURCE branch — "
        "the source-specific guard is then pinned by nothing")


def test_the_renderer_refuses_to_write_over_a_tracked_path(monkeypatch):
    """…and over ANY tracked path, not only the source.

    ⛔ THE TRACKED-PATH BRANCH IS PINNED WITHOUT RISKING A REAL FILE: the tool's
    `git ls-files` probe is the thing under test, so it is the thing replaced — a
    fake that reports "tracked" for a path that does not exist. If the guard is
    present, nothing is written and the target stays absent; if it regressed, this
    reds on the assertion below rather than after a 370 KB document has replaced a
    real tracked file.
    """
    target = ROOT / "docs" / "architecture" / "refuse-me.md"
    assert not target.exists(), "a real file sits at the probe path — pick another"
    real_run = census.subprocess.run

    def fake_run(cmd, **kwargs):
        if list(cmd[:2]) == ["git", "ls-files"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(census.subprocess, "run", fake_run)
    rc = census.main(["--render", target.relative_to(ROOT).as_posix()])
    assert rc == 2, f"`--render <tracked path>` must exit 2, got {rc}"
    assert not target.exists(), (
        "the guard must refuse before writing — a rendered document over a tracked "
        "path is the artifact this split exists to prevent")


def test_the_renderer_reports_a_write_failure_as_could_not_run(tmp_path, monkeypatch, capsys):
    """A write that cannot happen exits 2 (could not run), never 1 (DRIFT).

    ⛔ `--render` HAS NO DRIFT VERDICT AT ALL, so ANY exit 1 from it is a
    misclassification by construction: 1 is what `--check` reserves for "the pins
    drifted". The write path this split adds was unwrapped, so an OSError
    (`IsADirectoryError`, a read-only mount, ENOSPC) terminated the interpreter with
    status 1 — and §7 tells every lane to run `--render`, so a lane that happened to
    render into a path it cannot write read "the census drifted" and re-cut 25 pins
    that were correct (#13098 review, P2).

    The failure is reached without a tree walk: `census()` is replaced, because the
    subject here is the WRITE's status, not the enumeration. The parent is a regular
    FILE, so `mkdir(parents=True, exist_ok=True)` answers `NotADirectoryError`.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    out = blocker / "CYPHER-INVENTORY.md"
    monkeypatch.setattr(census, "census", lambda *a, **k: {"sites": []})
    rc = census.main(["--render", str(out)])
    assert rc == 2, (
        f"a write failure must exit 2 (could not run), got {rc} — 1 is the DRIFT "
        "verdict this verb never reports")
    err = capsys.readouterr().err
    assert "could not write" in err, f"the refusal must say what failed; stderr:\n{err}"
    assert str(out) in err, f"the refusal must name the path; stderr:\n{err}"


def test_the_renderer_reports_a_probe_failure_as_could_not_run(monkeypatch, capsys):
    """…and neither does the `git ls-files` probe that guards a tracked target.

    An absent `git` binary raises `FileNotFoundError` out of `subprocess.run`; left
    uncaught it exits 1, which is indistinguishable from "the census drifted" — the
    same misclassification as the write path, one call over (#13098 review, P2).
    Refused BEFORE the census runs, so this arm costs no tree walk.
    """
    target = ROOT / "docs" / "architecture" / "probe-failure-me.md"
    assert not target.exists(), "a real file sits at the probe path — pick another"
    real_run = census.subprocess.run

    def fake_run(cmd, **kwargs):
        if list(cmd[:2]) == ["git", "ls-files"]:
            raise FileNotFoundError(2, "No such file or directory", "git")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(census.subprocess, "run", fake_run)
    rc = census.main(["--render", target.relative_to(ROOT).as_posix()])
    assert rc == 2, (
        f"a failed tracking probe must exit 2 (could not run), got {rc} — 1 is the "
        "DRIFT verdict this verb never reports")
    err = capsys.readouterr().err
    assert "could not probe" in err, f"the refusal must say what failed; stderr:\n{err}"
    assert not target.exists(), "the refusal must happen before anything is written"


def test_a_hand_authored_prose_row_is_not_read_as_a_census_row(tmp_path, capsys):
    r"""The guard matches the GENERATED row, not any table starting with `file:line`.

    ⛔ THE BROAD PATTERN REFUSED CORRECT PROSE AND PRESCRIBED DELETING IT. A two-cell
    prose row (`| `file:line` | what it is |`) matched `^\| `file:line` \|` exactly as
    a six-cell generated row did, so a source that put a site address in a table's
    first column was refused by `--render` (exit 2, "Delete the rows") and read by
    `--check` as DRIFT (exit 1) — on a source whose 25 pins were all correct — and
    `cypher-diff.yml` reads that 1 as "the doc's numbers are wrong" (#13098 review,
    P2). Narrowing cannot blind the guard: the pattern still matches every row the
    generator emits, graded here against `markdown_table`'s OWN output so a column
    change reds instead of silently detaching the guard.
    """
    prose_row = "| `tortoise/sdk.py:1234` | the only place the ontology label is written |\n"
    assert not census.CENSUS_ROW.search(prose_row), (
        "a two-cell hand-authored row is not a census row — the appendix has SIX cells")
    generated = census.markdown_table([
        census.Site("tortoise/sdk.py", 1234, "f", "query", "plain", "read", "RETURN 1"),
        # `unresolved` is the one row whose cells are not read/write — the shape and
        # role of a statement the AST could not resolve. It must match too.
        census.Site("tortoise/sdk.py", 1235, "g", "query", "unresolved", "unresolved",
                    "<unresolved>"),
    ])
    assert census.CENSUS_ROW.search(generated), (
        "the guard no longer matches the generator's own rows — a pasted appendix "
        "would now pass; re-cut CENSUS_ROW with `markdown_table`")
    assert len(census.CENSUS_ROW.findall(generated)) == 2, (
        "every generated data row must match, not only the first")
    # …and both consumers must now treat a prose-carrying source as CLEAN. The pins
    # below are exactly what an empty census computes, so 0 means "no row refused and
    # no pin drifted" — the pre-fix code answered 1 here, for the prose row.
    src = tmp_path / "prose.src.md"
    pins = "".join(
        f"<!-- cypher-inventory:{key} = 0 -->\n"
        for key in ("sites", "files", "files_scanned", "unresolved", "candidates",
                    "non_cypher_door_calls", "ast_door_calls", "partial"))
    src.write_text(pins + "\n" + prose_row, encoding="utf-8")
    empty = {"sites": [], "files_with_sites": 0, "files_scanned": 0,
             "non_cypher_door_calls": 0, "partial": 0}
    assert census.check(src, empty) == 0, (
        "`--check` refused a source whose only 'row' is hand-authored prose — the "
        "guard must match the GENERATED shape")
    assert "census row" not in capsys.readouterr().err
    rendered = census.render(src, {"sites": []})
    assert prose_row in rendered, (
        "`--render` must append the appendix to a prose-carrying source unchanged")


NARRATIVE_PATTERN = r"([\d,]+) of ([\d,]+) product statements \(([\d.]+) %\)"

# ⛔ EVERY `_exactly_one` ARM `_grade_doc_text` MAKES, one row each, as
# `(arm, what, bounds, pattern)`. The patterns are the ARM'S OWN constants (defined here, used
# by `_grade_doc_text` below), so this table cannot drift from the arms it claims to cover:
# renaming or re-cutting a pattern re-points the row with it, and deleting a row cannot leave a
# stale literal behind. `what` is the `what=` string the arm passes to `_exactly_one`, which lets
# the assertion name the arm that fired rather than accepting any refusal.
#
# ⛔ "ARM" IS THE UNIT OF THIS TABLE, NOT "LOOKUP" — `_grade_doc_text` also makes TWO
# `re.search` calls (#13458` review F1): the §0/§1 bounds lookups. Those are not arms and are
# not graded here: their slice is a SUPERSET of the region an arm reads, so an in-section decoy
# is still refused by the arm that reads it. They are COUNTED instead, below.
#
# ⛔ THIS TABLE IS THE UNIQUENESS COVERAGE CLAIM (`#13458` review P2-3). Before it existed,
# the mutant proof duplicated only NARRATIVE_PATTERN, so arm (b) was the only arm whose
# revert-to-`re.search` any test could redden, while two docstrings said "any arm". One row
# per arm makes that claim executable. A deleted row, a
# DUPLICATED row and a row whose `what=` matches nothing are all reds
# (`test_every_graded_lookup_has_a_coverage_row`); a DUPLICATED row alone would not be, which is
# why that test asserts multiplicity and arm-id uniqueness on top of set equality.
#
# A row whose pattern no longer matches its section fails loudly ("no line in the scoped
# section matches") rather than silently proving nothing, so a re-cut that moves a row
# cannot leave a dead case behind.
SECTION0_BOUNDS = r"^## 0\..*?^## 1\."
SECTION1_BOUNDS = r"^## 1\..*?^## 2\."
DECOMPOSITION_PATTERN = (
    r"^= ([\d,]+) +classified Cypher statements \+ ([\d,]+) non-Cypher door calls$")
TABLE_NON_CYPHER_PATTERN = (
    r"^\| non-Cypher door calls \(excluded from every shape total\) \| (\d+) \|$")
TABLE_AST_PATTERN = r"^\| \*\*AST door calls\*\* = census \+ non-Cypher \| \*\*(\d+)\*\* \|$"
TABLE_SITES_PATTERN = r"^\| \*\*statement sites \(this census\)\*\* \| \*\*(\d+)\*\* \|$"
TABLE_CANDIDATES_PATTERN = (
    r"^\| \*\*`plain` READS — the migration candidates\*\* \| \*\*(\d+)\*\* \|$")
PRODUCT_SURFACE_PATTERN = (
    r"^\| product surface \(this census\) \| \*\*(\d+)\*\* \| \*\*(\d+)\*\* \|")

_GRADED_LOOKUPS: tuple[tuple[str, str, str, str], ...] = (
    ("a", "§1's decomposition line (publishes §0's operands)", SECTION1_BOUNDS,
     DECOMPOSITION_PATTERN),
    ("a2-non-cypher", "§0's `non-Cypher door calls` table row", SECTION0_BOUNDS,
     TABLE_NON_CYPHER_PATTERN),
    ("a2-ast", "§0's `AST door calls` table row", SECTION0_BOUNDS, TABLE_AST_PATTERN),
    ("a2-sites", "§0's `statement sites` table row", SECTION0_BOUNDS, TABLE_SITES_PATTERN),
    ("a2-candidates", "§0's `plain` READS table row", SECTION0_BOUNDS,
     TABLE_CANDIDATES_PATTERN),
    ("b-narrative", "§0's 'N of M product statements (P %)' line", SECTION0_BOUNDS,
     NARRATIVE_PATTERN),
    ("c-product-surface", "§1's product-surface table row", SECTION1_BOUNDS,
     PRODUCT_SURFACE_PATTERN),
)

# The ONLY bare `re.search` lookups `_grade_doc_text` is allowed to make: the §0 and §1 region
# bounds. They are not arms — a slice is a SUPERSET of the region its arms read, so an
# in-section decoy is still refused by the arm that reads it — and they are counted by
# `test_every_graded_lookup_has_a_coverage_row` so a third one cannot arrive unremarked
# (`#13458` review F1).
_UNGRADED_BOUNDS_LOOKUPS = 2


def _exactly_one(pattern: str, text: str, *, what: str, flags: int = re.MULTILINE) -> tuple[str, ...]:
    """The ONE occurrence of `pattern` in `text`; zero or MANY is a red, never first-match.

    ⛔ `re.search` takes the FIRST match, so a SECOND occurrence of a pinned
    publication — a decoy planted ABOVE the real one **inside the same section** —
    re-points the arm at the decoy while the real line rots green. Slicing to a
    section closes the CROSS-section decoy; this closes the IN-section one, which is
    the same mechanism one level in (`#13424` review P2-1). Uniqueness rather than
    first-match is the property a pin actually needs: "one anchored expected value"
    only holds if the pattern can match once.

    ⛔ THE ANCHORED ARMS ARE GUARDED TOO, deliberately. The review called that case
    "much weaker" because a decoy there must reproduce an entire row label — true, and
    a duplicated whole row IS itself a doc defect, but the consequence is identical: a
    correct duplicate above a rotted real row is a GREEN pin over a doc that disagrees
    with the census, which is the false PASS this whole test exists to refuse. One
    helper, every arm, no arm left as the hole the next decoy walks through.

    ⛔ AND THAT IS PROVEN ARM BY ARM, not asserted (`#13458` review P2-3): `_GRADED_LOOKUPS`
    enumerates every `_exactly_one` ARM below and `test_every_anchored_arm_refuses_a_duplicate`
    drives THIS helper through `_grade_doc_text` once per row, so each named arm has its own red
    when reverted to `re.search`. Adding an ARM without a table row is the gap that review found;
    the two lists must stay the same length. (The two §0/§1 BOUNDS `re.search` calls are not
    arms and have no rows — their slice is a superset, and the coverage test counts them
    separately so a third bare `re.search` still reds.)
    """
    found = re.findall(pattern, text, flags)
    assert found, f"{what} moved or changed shape; re-cut this pin with it"
    assert len(found) == 1, (
        f"{what} now appears {len(found)} times; this pin can only grade ONE — "
        "remove the duplicate, or re-cut this pin with them")
    match = found[0]
    # `findall` returns a bare string for a one-group pattern and a tuple for several;
    # callers unpack uniformly, so normalise here rather than at four call sites.
    return match if isinstance(match, tuple) else (match,)


def _grade_doc_text(doc_text: str, *, sites: int, non_cypher: int, candidates: int,
                    files_with_sites: int) -> None:
    """Grade EVERY publication of §0's operand pair in `doc_text`, or red.

    ⛔ EXTRACTED FROM THE TEST ON PURPOSE (`#13424` review P2-1): a guard nothing can be
    pointed at is an assertion nobody has shown to bite. TWO tests drive THIS function
    against a doctored document, and they partition the arms below between them:
    `test_the_narrative_pin_refuses_an_in_section_decoy` reproduces the false PASS end to
    end for arm (b), and `test_every_anchored_arm_refuses_a_duplicate` walks
    `_GRADED_LOOKUPS` — one case per lookup — so reverting ANY arm to a first-match
    `re.search` reds the row named after THAT arm instead of quietly re-opening the false
    PASS. ⛔ Every `_exactly_one` ARM below appears in `_GRADED_LOOKUPS`; a new arm that is not
    listed there is a coverage gap, and adding one without a row is the overclaim this was
    caught making (`#13458` review P2-3). The function's TWO other regex lookups are the §0/§1
    bounds `re.search` calls, which are deliberately NOT arms — their slice is a superset of
    the region an arm reads, so an in-section decoy is still refused by that arm. They are
    counted by the coverage test so a THIRD bare `re.search` cannot slip in unremarked.

    The reasoning below is the test's, moved with the arms it explains.
    """
    # ⛔ REGION BOUNDS, established ONCE for every arm below.
    #
    # Each of the four publications this test grades lives in a SPECIFIC section, and a
    # whole-document `re.search` takes the FIRST match ANYWHERE in the file. So a decoy —
    # say a correct `= 1314 … + 258` planted above §0, or any other prose quoting the
    # phrase — silently re-points the arm at the decoy while the real publication rots
    # green. That is a false PASS, the exact failure mode every guard in this test exists
    # to refuse (`#13424` review P2-F1).
    #
    # Slicing first makes the SHAPE GUARD do its job: if a section stops publishing its
    # pair, the arm REDS instead of matching some other section. Arm (b) was scoped this
    # way from the start; leaving (a), (a2) and (c) unscoped was an inconsistency, not a
    # decision, and it was measurably exploitable.
    #
    # ⛔ AND A SLICE IS ONLY HALF THE GUARD: it stops ANOTHER section re-pointing an arm,
    # not a SECOND occurrence INSIDE this one. Every arm below therefore goes through
    # `_exactly_one`, which refuses a duplicate instead of taking the first match
    # (`#13424` review P2-1; `test_the_narrative_pin_refuses_an_in_section_decoy` is the
    # executable proof that the refusal is load-bearing).
    section0 = re.search(SECTION0_BOUNDS, doc_text, re.MULTILINE | re.DOTALL)
    assert section0, "§0's section bounds moved (no `## 0.` … `## 1.`); re-cut this pin with them"
    section1 = re.search(SECTION1_BOUNDS, doc_text, re.MULTILINE | re.DOTALL)
    assert section1, "§1's section bounds moved (no `## 1.` … `## 2.`); re-cut this pin with them"

    # (a) §1's decomposition line — §0's operand pair, published in §1's block:
    # `= N classified Cypher statements + M non-Cypher door calls`
    #
    # ⛔ It sits in §1's block, not §0 — §0 publishes the same pair in its table and its
    # narrative, and this line is the third publication, one screen further down. Scoped
    # to §1 for the decoy reason above. The MESSAGES below name §1 too, deliberately: a
    # re-cutter whose arm fires is told where the line actually lives, so they do not
    # hunt for it in §0 and "fix" a publication this arm does not read (`#13424` R2).
    doc_sites_raw, doc_non_cypher_raw = _exactly_one(
        DECOMPOSITION_PATTERN,
        section1.group(0),
        what="§1's decomposition line (publishes §0's operands)")
    doc_sites = int(doc_sites_raw.replace(",", ""))
    doc_non_cypher = int(doc_non_cypher_raw.replace(",", ""))
    assert (doc_sites, doc_non_cypher) == (sites, non_cypher), (
        "§1's decomposition states §0's deltas, and it disagrees with the census: "
        f"doc={doc_sites}+{doc_non_cypher} measured={sites}+{non_cypher}")

    # (a2) §0's count TABLE publishes the same pair one screen above the block, and
    # it is the arm nothing else grades: on the `#13249` re-cut at `ffe10dd198` the
    # table and the block both read `260 / 1571` while the pins said `258 / 1569`
    # and `--check` printed `25 pinned counts match` — a whole merge shipped a table
    # contradicting the pins, the block's own `1,622 − 53` arithmetic AND §7.
    # Scoped to §0 (`#13424` review P2-F1): the table is the FIRST publication in the
    # document, so a decoy above it would re-point these four rows.
    (table_non_cypher,) = _exactly_one(
        TABLE_NON_CYPHER_PATTERN,
        section0.group(0), what="§0's `non-Cypher door calls` table row")
    (table_ast,) = _exactly_one(
        TABLE_AST_PATTERN,
        section0.group(0), what="§0's `AST door calls` table row")
    # The two OPERAND rows, not just the two derived ones: `non-Cypher` and `AST` are
    # both COMPUTED from the pair below, so a re-cut that moved only the operands would
    # red (a)/(b) and this arm alike — but the reverse is what shipped at `ffe10dd198`:
    # the table published its own operands, and nothing here read them. Pinning the
    # operands adds NO new failure surface: (a) and (b) already require a re-cut to move
    # `sites` and `candidates` in the block and the narrative, so a re-cutter is already
    # obliged to touch these rows. It closes the remaining way THIS table can go stale
    # while every other publication is right — the exact class the arm exists for.
    (table_sites,) = _exactly_one(
        TABLE_SITES_PATTERN,
        section0.group(0), what="§0's `statement sites` table row")
    (table_candidates,) = _exactly_one(
        TABLE_CANDIDATES_PATTERN,
        section0.group(0), what="§0's `plain` READS table row")
    assert int(table_sites) == sites, (
        f"§0's table says {table_sites} statement sites, the census measures {sites}")
    assert int(table_candidates) == candidates, (
        f"§0's table says {table_candidates} `plain` migration candidates, the census "
        f"measures {candidates}")
    assert int(table_non_cypher) == non_cypher, (
        f"§0's table says {table_non_cypher} non-Cypher door calls, the census "
        f"measures {non_cypher}")
    assert int(table_ast) == sites + non_cypher, (
        f"§0's table says {table_ast} AST door calls, {sites} + {non_cypher} is "
        f"{sites + non_cypher}")

    # (b) §0's narrative: `N of M product statements (P %)` — N is the candidate
    # count (plain READS), M is the statement sites, and P is DERIVED from them.
    #
    # ⛔ SCOPED TO §0's OWN TEXT, for the same decoy reason every arm above is now
    # scoped (`#13249` review, `#13424` P2-F1). The pattern is deliberately loose about
    # the line's PREFIX (a legitimate reword must not red) but not about WHERE: the
    # shared `section0` slice above makes the shape guard do its job, so if §0 stops
    # publishing the pair the arm REDS rather than matching some other section.
    #
    # ⛔ AND IT DEMANDS UNIQUENESS, not a first match — this arm's pattern is the one
    # that is deliberately loose about the line's prefix, so a second restatement of the
    # pair inside §0 is the plausible way in (a re-cutter adding a summary sentence above
    # the finding line). Uniqueness makes that a red instead of a re-pointed arm
    # (`#13424` review P2-1).
    doc_candidates_raw, doc_statement_sites_raw, doc_percent_raw = _exactly_one(
        NARRATIVE_PATTERN, section0.group(0),
        what="§0's 'N of M product statements (P %)' line")
    doc_candidates = int(doc_candidates_raw.replace(",", ""))
    doc_statement_sites = int(doc_statement_sites_raw.replace(",", ""))
    doc_percent = float(doc_percent_raw)
    assert (doc_candidates, doc_statement_sites) == (candidates, sites), (
        "§0's narrative count disagrees with the census: "
        f"doc={doc_candidates} of {doc_statement_sites} measured={candidates} of {sites}")
    assert doc_percent == round(candidates / sites * 100, 1), (
        f"§0's percentage is {doc_percent}, but {candidates}/{sites} is "
        f"{round(candidates / sites * 100, 1)}")

    # (c) §1's comparison table: the product-surface row, and the files carrying a site.
    row_sites, row_files = _exactly_one(
        PRODUCT_SURFACE_PATTERN,
        section1.group(0), what="§1's product-surface table row")
    assert (int(row_sites), int(row_files)) == (sites, files_with_sites), (
        "§1's comparison table disagrees with the census: "
        f"doc={row_sites} sites / {row_files} files, "
        f"measured={sites} / {files_with_sites}")


@requires_doc_pin
def test_the_published_decomposition_names_the_measured_operands():
    """§0/§1 reconcile ARITHMETICALLY over operands, and the operands are the claim.

    ⛔ The failure this refuses is the exact shape `test_the_census_reconciles_
    arithmetically` was written against, one layer up: at `b4c6ce8294` — the head
    `3ca7c6a4f0` re-cut — §0 read `= 1310 classified Cypher statements + 257
    non-Cypher door calls` while the census measured `1311` statement sites and
    `256` non-Cypher door calls. Both terms were off by ONE in OPPOSITE directions,
    so the line's own total still closed at 1,567 and every pin stayed green
    (`#13249` review round 13 F1, round 14). Nothing could see it: `check()` reads
    only the HTML pins, and the reconciliation test recomputes from MEASURED
    values, never from this prose.

    The narrative in §0 and §1's PRODUCT-surface row publish the same two
    quantities, so they are pinned by the same test — they were wrong at the same
    commit and for the same reason (the pins were re-cut, the prose was not). A
    re-cut that moves any of these numbers must move this line with it.

    ⛔ THIS TEST WAS DELETED, TWICE, BY CONFLICT RESOLUTIONS on the branch that
    landed this doc — at `243dbdbe6b` and again at `fa48ba6d70` — and the squash
    merge of `#13249` (`63aa947381`) carried the absence into `main`, with a fully
    green suite. That is the whole reason it is being re-added rather than merely
    kept: the class it grades is the class that shipped. Measured on `main` at
    `9cebd72ad7`: §1 read "the verb-at-end-of-line scan alone finds `175`" against
    `176`, and §7 quoted a `ten-prose/three-Cypher` split its own §1 contradicted —
    with `--check` printing `25 pinned counts match` over both (`#13368` R2-R4).

    ⛔ WHAT IT DOES NOT GRADE, stated rather than implied (`#13249` review,
    handover item 2): it pins §1's decomposition line (§0's operand pair, published
    in §1's block), §0's narrative
    pair and §1's PRODUCT-surface row, plus FOUR rows of §0's count TABLE —
    `statement sites`, `` `plain` READS``, `non-Cypher door calls` and `AST door calls`.
    It does NOT pin the shape-split rows under them (`860 (579 read, 281 write)`,
    `adjacency` / `ep` / `traversal` / `fulltext` / `ddl` / `unresolved`) nor §0's
    scanned-file count: those are INDEPENDENT census quantities, NOT derived from the
    four that are graded, so any of them CAN go stale here and stay green. (The files
    carrying a site ARE pinned, by (c).) That is a declared trade-off, not a property:
    pinning nine more scalars buys a wider re-cut obligation, and this test chooses not
    to pay it. Measured on a `/tmp` copy (`#13424` review P2-F2): changing `traversal`
    from `3` to `4`, and `860 (579 read, 281 write)` to `860 (579 read, 999 write)`,
    each leaves this test GREEN while §0 publishes a row whose own split does not add
    up. Only their SUM is constrained by `sites`. Do not read the absence of these
    assertions as a guarantee that they are right. It does
    NOT pin the `--all-surface` row that
    sits beneath that row in §1's mode table: that population includes `tests/`, so
    a pin on it would red an unrelated PR for a number this document does not rest
    on, and NOT pinning it is a recorded decision (`#13098` review P2) — do not
    "fix" this by adding one. The consequence is that the mode row CAN go stale
    while every assertion here stays green; it did, repeatedly. A re-cutter must
    re-derive it separately with `python tools/cypher_inventory.py --all-surface
    --json` (`sites` / `files_with_sites`); see §7's "third place the same cut can
    leave stale".
    """
    doc_text = _source_text()
    result = census.census(("tortoise",))
    sites = len(result["sites"])
    non_cypher = result["non_cypher_door_calls"]
    candidates = sum(1 for s in result["sites"] if s.verdict.startswith("candidate"))

    # ⛔ THE ARMS LIVE IN `_grade_doc_text`, so the identical code can be driven against
    # the in-section decoy mutant (`test_the_narrative_pin_refuses_an_in_section_decoy`);
    # that function's comments carry the region-bounds and per-arm reasoning.
    _grade_doc_text(doc_text, sites=sites, non_cypher=non_cypher,
                    candidates=candidates, files_with_sites=result["files_with_sites"])

@requires_doc_pin
def test_the_narrative_pin_refuses_an_in_section_decoy():
    """A SECOND occurrence INSIDE §0 must red the arm, not re-point it (`#13424` P2-1).

    Slicing arm (b) to §0's own text closed the CROSS-section decoy. It did not close
    the same mechanism one level in: the arm's pattern is prefix-loose by design (a
    legitimate reword must not red), so a `> 579 of 1314 product statements (44.1 %)`
    restatement planted ABOVE the finding line — plausible in §0, which is where the
    numbers are first published — was what `re.search` bound to, while the real line
    rotted to `580 of 1314`. Measured on the pre-fix arm: **GREEN** over a §0 that
    disagrees with the census.

    This test is the non-vacuity proof for the fix: it builds exactly that mutant from
    the live doc and asserts BOTH halves — that a first-match search binds the decoy
    (the false PASS being refused), and that `_grade_doc_text` — the REAL grading path,
    baseline-GREEN on the unmutated doc — refuses it. Either half alone is won't-do:
    the first is what makes this test FAIL on the pre-fix arm, the second is what makes it
    fail if arm (b) — the one this mutant duplicates — is ever reverted to first-match.
    ⛔ IT PROVES ARM (b) ONLY, and says so: the other six lookups have their own rows in
    `test_every_anchored_arm_refuses_a_duplicate`, which is where their proof lives
    (`#13458` review P2-3: this docstring used to claim "an arm", i.e. any arm).
    """
    doc_text = _source_text()
    result = census.census(("tortoise",))
    sites = len(result["sites"])
    candidates = sum(1 for s in result["sites"] if s.verdict.startswith("candidate"))
    percent = round(candidates / sites * 100, 1)

    section0 = re.search(r"^## 0\..*?^## 1\.", doc_text, re.MULTILINE | re.DOTALL)
    assert section0, "§0's section bounds moved (no `## 0.` … `## 1.`); re-cut this pin with them"
    # The mutant is the WHOLE document with one bullet added inside §0 — it has to be, or
    # `_grade_doc_text` (which grades §1 too) would red on the missing section rather than
    # on the defect this test names, i.e. the proof would be of the wrong thing.
    lines = doc_text.splitlines(keepends=True)
    real = next(i for i, line in enumerate(lines) if re.search(NARRATIVE_PATTERN, line))
    assert section0.start() <= len("".join(lines[:real])) < section0.end(), (
        "the narrative line is no longer inside §0, so this decoy is not the in-section one")

    # The decoy states the CORRECT pair, so a first-match search is satisfied by it...
    decoy = f"> **The count, restated:** {candidates} of {sites} product statements ({percent} %)\n"
    # ...and the REAL line rots to a different count, which is the doc defect the pin exists
    # to catch. Only the real line moves; the decoy is a plain addition above it.
    rotted = re.sub(r"\b[\d,]+ of [\d,]+", f"{candidates + 1} of {sites}", lines[real], count=1)
    assert rotted != lines[real], "the fixture's rot did not take; the decoy proof would be vacuous"
    mutant = "".join([*lines[:real], decoy, rotted, *lines[real + 1:]])

    # 1. THE FALSE PASS, reproduced: the pre-fix arm binds the decoy and grades GREEN over a
    #    §0 whose own finding line now disagrees with the census.
    first = re.search(NARRATIVE_PATTERN, mutant)
    assert first, "the mutant stopped publishing the pair at all — the proof is not the one claimed"
    assert (int(first.group(1)), int(first.group(2)), float(first.group(3))) == (
        candidates, sites, percent), (
        "the decoy is not what a first-match search binds to, so this test would not have "
        "failed on the pre-fix arm")

    # 2. AND THE REAL GRADING PATH REFUSES IT — not a helper in isolation: the same
    #    `_grade_doc_text` the pin above runs is handed the mutant, so reverting arm (b)
    #    to a first-match `re.search` reds THIS test. The OTHER six lookups are not this
    #    mutant's business (it duplicates only the narrative line) — each has its own
    #    duplicate, and its own case, in `test_every_anchored_arm_refuses_a_duplicate`.
    #    ⛔ Do not read "the arms share one helper" as "this test covers them": that is
    #    precisely the overclaim `#13458` review P2-3 returned.
    kwargs = dict(sites=sites, non_cypher=result["non_cypher_door_calls"],
                  candidates=candidates, files_with_sites=result["files_with_sites"])
    _grade_doc_text(doc_text, **kwargs)  # baseline GREEN, so step 3's red is the mutant's
    with pytest.raises(AssertionError) as refused:
        _grade_doc_text(mutant, **kwargs)
    message = str(refused.value)
    assert "now appears 2 times" in message, message
    assert "can only grade ONE" in message, message


@pytest.fixture(scope="module")
def measured_census() -> dict[str, int]:
    """The census, measured ONCE — the arm-coverage test grades one mutant per
    `_GRADED_LOOKUPS` row against ONE doc.

    `census()` is a full tree walk (measured ~6 s at `#13458`), so measuring it per case
    would add ~40 s to prove what one measurement already settles.
    """
    result = census.census(("tortoise",))
    return {
        "sites": len(result["sites"]),
        "non_cypher": result["non_cypher_door_calls"],
        "candidates": sum(1 for s in result["sites"] if s.verdict.startswith("candidate")),
        "files_with_sites": result["files_with_sites"],
    }


def test_every_graded_lookup_has_a_coverage_row():
    """`_GRADED_LOOKUPS` and the `_exactly_one` arms of `_grade_doc_text` are the SAME set.

    Read from the function's own source rather than counted, so the pairing is mechanical: an
    arm added without a row is a coverage gap named here, and a row left behind for an arm
    that no longer exists is a DEAD case that silently proves nothing. Both directions matter
    — before this existed the mutant proof covered arm (b) only while two docstrings said
    "any arm", which is the overclaim the review returned (`#13458` P2-3).

    ⛔ SET EQUALITY ALONE WAS NOT ENOUGH (`#13458` review F1/F2), and both gaps were executed
    before they were closed:

    * the `what=` scan can only see a DOUBLE-QUOTED literal, so an arm added as
      `what=f"…"`, `what='…'` or a bare `re.search` was invisible to it while this function's
      own docstring promised it would be caught. The call COUNTS below close that: arms by
      `_exactly_one(`, and the two bounds lookups by `re.search(` against a named constant.
    * a DUPLICATED row keeps set equality (`set` dedupes), so nothing redded. Multiplicity and
      arm-id uniqueness are asserted explicitly.
    """
    source = inspect.getsource(_grade_doc_text)
    armed = set(re.findall(r'what="([^"]+)"', source))
    listed = {row[1] for row in _GRADED_LOOKUPS}
    assert listed == armed, (
        "every arm in `_grade_doc_text` needs exactly one row in `_GRADED_LOOKUPS`, and every\n"
        "row needs a live arm — otherwise the uniqueness claim has a hole nobody can see:\n"
        f"  arms with NO proof row: {sorted(armed - listed)}\n"
        f"  proof rows with NO arm: {sorted(listed - armed)}")
    assert len(_GRADED_LOOKUPS) == len(listed), (
        f"`_GRADED_LOOKUPS` carries {len(_GRADED_LOOKUPS)} rows but only {len(listed)} distinct\n"
        "arms: a DUPLICATED row proves nothing twice and is invisible to the set comparison")
    assert len({row[0] for row in _GRADED_LOOKUPS}) == len(_GRADED_LOOKUPS), (
        "two rows share an arm id, so one of their cases reports under the other's name")
    # Both counts are whitespace-tolerant on purpose: a formatter's `_exactly_one (` or
    # `re.search (` is the same call, and a `str.count` of the tight spelling would miss it
    # (`#13458` review notes). The pair must stay symmetric — a tolerant arm count next to a
    # tight bounds count is the asymmetry that let a spaced third `re.search` pass.
    arm_calls = len(re.findall(r"_exactly_one\s*\(", source))
    assert arm_calls == len(_GRADED_LOOKUPS), (
        f"`_grade_doc_text` makes {arm_calls} `_exactly_one` calls but the\n"
        f"table lists {len(_GRADED_LOOKUPS)} arms. The `what=` scan above only sees a\n"
        "double-quoted literal, so an arm added with `what=f\"…\"` or `what='…'` would pass it\n"
        "silently — add a row for the arm, and this count keeps the two in step")
    assert len(re.findall(r"re\.search\s*\(", source)) == _UNGRADED_BOUNDS_LOOKUPS, (
        f"`_grade_doc_text` makes {len(re.findall(r're\.search\s*\(', source))} bare "
        "`re.search` lookups; only\n"
        f"the {_UNGRADED_BOUNDS_LOOKUPS} section bounds are allowed to be ungraded. A new one\n"
        "is either an ARM (use `_exactly_one` and add a `_GRADED_LOOKUPS` row) or it needs its\n"
        "own justification written down where the bounds are established")


@requires_doc_pin
@pytest.mark.parametrize("arm, what, bounds, pattern", _GRADED_LOOKUPS,
                         ids=[row[0] for row in _GRADED_LOOKUPS])
def test_every_anchored_arm_refuses_a_duplicate(arm, what, bounds, pattern, measured_census):
    """EACH ARM's uniqueness guard reds on its own duplicate (`#13458` review P2-3).

    The sibling mutant test proves arm (b) end to end, including the false PASS a first-match
    search reproduces. That is one arm. `_grade_doc_text` makes one `_exactly_one` arm per
    `_GRADED_LOOKUPS` row, and a guard no test can redden is exactly the "quietly re-opened
    false PASS" the arms exist to refuse — so this test drives the REAL grading path once per
    row of `_GRADED_LOOKUPS`:

      1. locate the line this arm binds, INSIDE the section it is scoped to;
      2. duplicate that line ABOVE itself — a CORRECT restatement, which is the decoy shape
         (a first-match search stays satisfied by the copy while the real line rots);
      3. assert the unmutated doc grades GREEN, so the red below is the mutant's;
      4. assert the refusal names THIS arm (`"<what> now appears 2 times"`), not some earlier
         assertion that happens to fire first.

    ⛔ Step 4 is what makes this non-vacuous per arm: revert JUST that arm to `re.search` and
    only that arm's case reds, so the failure names the arm it belongs to.
    """
    doc_text = _source_text()
    section = re.search(bounds, doc_text, re.MULTILINE | re.DOTALL)
    assert section, f"arm ({arm}): its section bounds moved; re-cut this pin with them"
    section_text = section.group(0)
    hit = re.search(pattern, section_text, re.MULTILINE)
    assert hit, (
        f"arm ({arm}): no line in the scoped section matches this arm's pattern — the case\n"
        f"would prove nothing about arm ({arm}); pattern={pattern!r}")
    line_start = section_text.rfind("\n", 0, hit.start()) + 1
    line_end = section_text.find("\n", hit.end())
    assert line_end != -1, (
        f"arm ({arm}): the matched line carries no terminator; re-cut this case")
    real_line = section_text[line_start:line_end + 1]
    mutant_section = section_text[:line_start] + real_line + section_text[line_start:]
    mutant = doc_text[:section.start()] + mutant_section + doc_text[section.end():]

    # Baseline GREEN on the unmutated doc, so step 4's red is the MUTANT's, not the fixture's
    # (the same anti-vacuity property the sibling test states).
    _grade_doc_text(doc_text, **measured_census)
    with pytest.raises(AssertionError) as refused:
        _grade_doc_text(mutant, **measured_census)
    message = str(refused.value)
    assert f"{what} now appears 2 times" in message, (
        f"arm ({arm}) has a uniqueness guard no test can redden: duplicating its own line did\n"
        f"not red THAT arm. The refusal raised was:\n{message}")


def test_the_census_reconciles_arithmetically():
    """regex occurrences − non-call occurrences = AST door calls = classified + non-Cypher.

    This is the assertion the #13098 review earned: the first version of the tool
    printed a residual that ABSORBED a 52-statement loss, so the arithmetic closed
    over a wrong number. The reconciliation is only worth printing if it is an
    equality between measured values.
    """
    result = census.census(("tortoise",))
    totals = census.rg_totals(("tortoise",))
    ast_door_calls = len(result["sites"]) + result["non_cypher_door_calls"]
    assert totals["lines_door_calls"] - totals["door_occurrences_without_a_call"] == ast_door_calls
    # ⛔ This second assertion was an IDENTITY and could never fail: both buckets
    # come from the SAME regex, so `regex − non-call` equals the regex's own
    # partition total by construction, and subtracting the AST count from it is
    # the assertion above rearranged. Its comment claimed a line regex and an AST
    # walk were "measured independently", which the arithmetic denies
    # (`#13098` review P2). What is asserted now is a PER-LINE cross-check of the
    # line regex against the AST's own door nodes — strictly stronger than the
    # sum, because a balanced pair of opposite errors (one line where the regex
    # sees two occurrences and the AST holds one node, another off in the other
    # direction) leaves the totals closing perfectly and is caught only here.
    # Non-vacuity MEASURED, and stated for exactly what it shows: putting a
    # docstring `.query(` on a line that already carries a real door call takes
    # this counter 0 -> 1. That mutant ALSO reddens the assertion above (the
    # occurrence leaves `door_occurrences_without_a_call`, so `regex − non-call`
    # grows by one) — so what it proves is that this counter can be non-zero, NOT
    # that it is the only arm that sees a divergence. What only this counter sees
    # is the BALANCED case: two lines that are wrong in opposite directions leave
    # every total above closing perfectly, and no assertion on those totals can
    # ever notice (`#13098` review P3 caught this comment claiming more than the
    # mutant showed).
    assert totals["lines_where_regex_and_ast_disagree"] == 0, (
        "the door regex and the AST disagree on how many door calls ONE LINE carries — the "
        "totals can still close over that, which is exactly why this arm exists: "
        f"{totals}"
    )


@requires_doc_pin
def test_the_census_pin_can_report_drift(tmp_path):
    """The pin's FAILURE path is asserted, not just its happy path.

    `test_the_doc_counts_match_the_census` only asserts `returncode == 0`, so
    making `check()` permanently unable to redden (its drift guard replaced by
    `if False:`) left this suite green — the vacuous-pin class the module's own
    docstring cites (`#11859`), and the `#13098` review's F1. Each arm below is a
    way the pin is supposed to fail.
    """
    result = census.census(("tortoise",))
    doc_text = _source_text()
    assert census.check(DOC_SRC, result) == 0

    def write(text: str):
        path = tmp_path / "doc.md"
        path.write_text(text, encoding="utf-8")
        return path

    # (a) a pin one off — DRIFT
    drifted = write(doc_text.replace("<!-- cypher-inventory:sites = ", "<!-- cypher-inventory:sites = 1", 1))
    assert census.check(drifted, result) == 1
    # (b) a pin removed — MISSING
    missing_line = next(line for line in doc_text.splitlines()
                        if line.startswith("<!-- cypher-inventory:candidates ="))
    assert census.check(write(doc_text.replace(missing_line + "\n", "")), result) == 1
    # (c) a pin the tool no longer computes — STALE
    assert census.check(write(doc_text + "\n<!-- cypher-inventory:shape_retired = 1 -->\n"),
                        result) == 1
    # (d) no doc at all — could not run, which is 2 and never 0
    assert census.check(tmp_path / "absent.md", result) == 2


def test_a_lambda_body_is_part_of_its_enclosing_function():
    """`lambda: sdk.query("MATCH …")` is a statement site, not an invisible one.

    The review found 52 of these (23 in `hosted_api.py`, 29 in `sdk.py`), dropped
    because the walker stopped at `ast.Lambda` and the residual line printed them
    as "comments".
    """
    import ast

    source = (
        "def f(sdk):\n"
        "    return asyncio.to_thread(lambda: sdk.query(\"MATCH (n:Point) RETURN n.id\"))\n"
    )
    tree = ast.parse(source)
    func = tree.body[0]
    calls = [n for n in census._own_nodes(func) if isinstance(n, ast.Call)]
    assert any(census._is_door(c) == "query" for c in calls), (
        "a door call inside a lambda must be visible to the census"
    )


@pytest.mark.parametrize(
    "statement,shape",
    [
        # precedence: a walk that also matches "plain" is a traversal
        ("MATCH path = (a)-[:IMPL|NAND*1..2]-(b) RETURN count(path)", "traversal"),
        ("MATCH p = shortestPath((a:Point)-[*]-(b:Point)) RETURN p", "traversal"),
        # adjacency: an edge pattern with no variable length
        ("MATCH (op:Point)-[:IMPL]->(t) RETURN t.id", "adjacency"),
        # adjacency: the BRACKETLESS spellings. They have no brackets for the first
        # arm to see, so they fell through every shape arm to `plain`/`read` →
        # `candidate:SQL-shaped`, the one direction the verdict flips (`#13098`
        # review P3). There IS one live site in this form —
        # `tortoise/domain_validators.py:240`'s `AND NOT (n)--()`, which is a real
        # adjacency predicate — so this is not purely prophylactic.
        ("MATCH (a:Point {id:$a})-->(b:Point) RETURN b.id", "adjacency"),
        ("MATCH (a:Point {id:$a})<--(b:Point) RETURN a.id", "adjacency"),
        ("MATCH (a:Point {id:$a})--(b:Point) RETURN a.id", "adjacency"),
        # a fulltext QUERY must not be eaten by the DDL arm
        ("CALL db.idx.fulltext.queryNodes('Point', $q) YIELD node, score", "fulltext"),
        ("CALL db.idx.fulltext.createNodeIndex('Point', ['content'])", "ddl"),
        # EP state
        ("MATCH (n:Point) WHERE n.ep_alpha > 0.5 RETURN n.id", "ep"),
        # plain: single node, no edge, no ep
        ("MATCH (n:Point {id:$id}) RETURN n.content", "plain"),
        ("MATCH (t:Team) RETURN t.id, t.name", "plain"),
    ],
)
def test_shape_precedence(statement, shape):
    assert census.classify(statement)[0] == shape


def test_a_sql_comment_is_not_a_bracketless_edge():
    """The bracketless arm is CONTEXT-ANCHORED, and this is why.

    `--` is also SQL's comment introducer, so a blanket `--` arm reports
    `SELECT * FROM public.point -- note` as an `adjacency` statement. In
    `enumerate_file` that is currently harmless because `SQL_VETO` runs first —
    but a classification that is correct only because of a caller's ordering is
    a latent false candidate, which is the class this fix removes. So an
    arrowhead (`-->`, `<--`) counts anywhere, and a bare `--` counts only BETWEEN
    two node patterns, which is where no SQL comment sits.
    """
    for sql in (
        "SELECT id FROM public.point -- the draft filter",
        "INSERT INTO public.point (id) VALUES ($1) -- note",
        "UPDATE public.point SET content = $1 WHERE id = $2 -- note",
    ):
        assert census.classify(sql)[0] != "adjacency", sql
    # …while the Cypher bare `--` between node patterns does.
    assert census.classify(
        "MATCH (n:Point {status:'draft'}) WHERE NOT (n)--() RETURN n.id")[0] == "adjacency"
    # The widening must not touch the SQL veto: `--` is NOT a node pattern, so a
    # SQL statement carrying a comment is still vetoed as non-Cypher.
    assert not census.NODE_PATTERN.search("SELECT id FROM public.point -- note")


def test_the_one_live_bracketless_site_is_an_adjacency_predicate():
    """`tortoise/domain_validators.py:240` — the site the widening actually moves.

    It left `candidate:SQL-shaped` for `measure:across shapes`, which is the
    correct direction: `AND NOT (n)--()` asks the graph a question about EDGES, so
    it is not a statement an SQL row could answer. Pinned by file:line so a later
    edit that removes the predicate also removes this expectation.
    """
    source = (census.ROOT / "tortoise/domain_validators.py").read_text()
    assert "AND NOT (n)--()" in source, (
        "the live bracketless site moved or was deleted; re-cut the census pins "
        "and this test together (§7 of the doc)"
    )


def test_a_postgres_statement_is_not_cypher():
    """`CREATE TABLE` through a shared `execute` door is not a Cypher statement."""
    assert not census.CYPHER_MARK.search("CREATE TABLE IF NOT EXISTS public.point (id text)")
    assert census.CYPHER_MARK.search("CREATE (n:Point {id:'x'})")


@pytest.mark.parametrize(
    "statement,shape",
    [
        # the write-clause shapes the first `WRITE_INLINE` arm could not see. Each
        # left role=`read` on a statement that mutates the graph, so a `plain`
        # one got verdict `candidate:SQL-shaped` — the one direction the verdict
        # flips, and therefore an inflated headline candidate count
        # (`#13098` review P2, measured on Appendix A's own output).
        ("MATCH (n:Point {id:$id}) SET n += $props RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) SET n = $props RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) SET n:Object RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) SET n.version = 1 RETURN count(n)", "plain"),
        # the SET target folded out of the literal: nothing follows the keyword
        ("MATCH (n:Point {id:$id}) SET {} RETURN n.id", "plain"),
        ("MATCH (n:Point {id:$id}) SET ??? RETURN count(n)", "plain"),
        # the verb is inline, so the `^`-anchored `WRITE_VERB` arm cannot see it
        ("MATCH (a:Point {id:$a}), (b:Point {id:$b}) MERGE (a)-[:CORR]->(b)", "adjacency"),
        ("MATCH (s:Point {id:$sid}), (t) WHERE ID(t) = $tid CREATE (s)-[:CONTAINS]->(t)",
         "adjacency"),
        ("MATCH ({} {id:$id}) REMOVE {}.embedding", "plain"),
        # a BACKTICKED target containing a space. The SET arm was widened for this
        # spelling and the DELETE/REMOVE/DROP arm was not, so a legal Cypher write
        # still classified role=`read` → `candidate:SQL-shaped` (`#13098` review P3).
        ("MATCH (n:Point {id:$id}) SET `my node`.`a b` = 1 RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) REMOVE `my node`.`a b` RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) DELETE `my node` RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) DROP `my node` RETURN count(n)", "plain"),
    ],
)
def test_an_inline_write_clause_is_a_write_not_a_candidate(statement, shape):
    """A statement carrying a write clause gets role `write`, never `candidate`.

    Role is half the verdict (`VERDICTS[(shape, role)]`), so a write read as a
    read is a migration candidate for a statement that mutates the graph — or, on
    an adjacency shape, a `measure`. No test asserted `role` at all before this
    one, which is why the defect was unguarded (`#13098` review P2).
    """
    got_shape, role = census.classify(statement)
    assert got_shape == shape, f"premise: {statement!r} classifies by shape as {got_shape!r}"
    assert role == "write", f"{statement!r} is a write clause, classified role={role!r}"
    assert not census.VERDICTS[(shape, role)].startswith(("candidate", "measure"))


def test_a_call_whose_procedure_name_the_text_cannot_prove_is_not_a_candidate():
    """A `CALL` naming no provable procedure must not be published as a READ.

    ⛔ The live defect (`#13098` review P1): `tortoise/projection/__init__.py` loops
    over `("db.idx.fulltext.drop", "db.idx.fulltext.dropIndex")` and calls
    `CALL {drop_proc}('Point')`. The resolver folds a loop variable to `{}`, so the
    NAME left the text — and a fulltext-index DROP was classified `plain`/`read`,
    which the verdict table maps to `candidate:SQL-shaped`: two graph WRITES inside
    the headline migration-candidate count. The un-folded spellings are the
    premise-check for this test: they classify `ddl`, so the loss was the folding,
    not the DDL arm.
    """
    for statement in ("CALL db.idx.fulltext.drop('Point')",
                      "CALL db.idx.fulltext.dropIndex('Event')"):
        assert census.classify(statement) == ("ddl", "ddl"), statement
    for statement in ("CALL {}('Point')",
                      "CALL ???('Point')",
                      # outside the engine's own namespace: read-vs-write is the
                      # plugin's business and is not visible in the statement
                      "CALL apoc.create.node(['X'], {}) YIELD node RETURN node"):
        assert census.classify(statement) == ("unresolved", "unresolved"), statement
    # …and the `db.*` procedures that ARE provable keep their own shapes: a blanket
    # "any CALL is unknown" would swallow every fulltext/vector READ.
    assert census.classify("CALL db.idx.fulltext.queryNodes('Point', 'x') YIELD node RETURN node") \
        == ("fulltext", "read")


def test_a_read_clause_is_still_a_read():
    """The other direction: the fix must not make every statement a write.

    A guard that fires on everything is as useless as one that fires on nothing,
    and it would deflate the candidate count instead of inflating it.
    """
    for statement in (
        "MATCH (n:Point {id:$id}) RETURN n.content",
        "MATCH (n:Point) WHERE n.createdAt IS NULL RETURN count(n)",
        "MATCH (m:Membership {user_id:$uid, role:'owner'}) RETURN m.role",
    ):
        assert census.classify(statement)[1] == "read", statement


# --------------------------------------------------------------------------- #
# the harness
# --------------------------------------------------------------------------- #
def test_the_harness_refuses_without_both_stores(capsys):
    """No lanes is exit 2 — a diff without two stores compares nothing to nothing."""
    assert harness.main(["--candidate", "point-by-id"]) == 2
    assert "could not run" in capsys.readouterr().err


def test_an_inapplicable_mutation_is_refused_not_applied():
    """`null-order` on a statement with no ORDER BY must refuse, never no-op.

    Review finding: the mutation used to return the SQL unchanged, so the run
    printed "diff EMPTY" and exited 0 while reporting `mutation: null-order` —
    a green run that proved nothing.
    """
    candidate = harness.CANDIDATES["point-by-id"]
    with pytest.raises(KeyError):
        harness._mutated(candidate, "null-order")


def test_every_declared_mutation_actually_changes_the_sql():
    """A mutation that changes nothing cannot prove the harness can fail."""
    for name, candidate in harness.CANDIDATES.items():
        for mutation in candidate.mutations:
            mutated = harness._mutated(candidate, mutation)
            assert mutated != candidate.sql, f"{name}/{mutation} changed nothing"


def test_the_diff_reports_a_dropped_row():
    """Row count is compared before values, so a filter change cannot hide in a zip."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate,
        [("p-1", "c", None)], ["id", "content", "point_kind"],
        [], ["id", "content", "point_kind"],
        ["p-1"],
    )
    assert result["equal"] is False
    assert any(d["kind"] == "row-count" for d in result["differences"])


def test_the_diff_separates_null_from_empty_string_and_zero():
    """Three-way null handling: '' and 0 are NOT 'the same as null'."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate,
        [("p-1", None, 0)], ["id", "content", "point_kind"],
        [("p-1", "", False)], ["id", "content", "point_kind"],
        ["p-1"],
    )
    assert result["equal"] is False
    kinds = {d["kind"] for d in result["differences"]}
    assert "family" in kinds


def test_the_diff_reports_order_for_an_ordered_statement():
    """An ordered statement compared as a sequence: the NULLS-LAST defect class."""
    candidate = harness.CANDIDATES["point-listing"]
    # the SAME rows, reversed — the null-placement defect exactly
    rows_cypher = [("a", "x", None), ("b", "y", "2026-01-01")]
    rows_sql = [("b", "y", "2026-01-01"), ("a", "x", None)]
    result = harness.diff(
        candidate, rows_cypher, ["id", "content", "created_at"],
        rows_sql, ["id", "content", "created_at"],
        ["a", "b"],
    )
    assert result["equal"] is False
    assert any(d["kind"] == "order" for d in result["differences"])


def test_the_order_pass_uses_the_value_pass_equality_for_numbers():
    """A numeric column must not report `order` on two forms that agree.

    ⛔ The order pass compared `_canon` (a json serialisation) while the value
    pass compared `_norm`, and those disagree on `1` vs `1.0`: `public.point`
    carries `confidence` as `double precision`, so the record answers
    `float`/`Decimal` where FalkorDB answers the `int` the property was written
    with. Measured before the fix: identical rows in identical order reported
    `['order']` (`#13098` review P2) — so an ordered EP-shaped candidate could
    never return an empty diff. Both directions are pinned here: the numeric
    difference is NOT an order difference, and a real reorder still is.
    """
    candidate = harness.CANDIDATES["point-listing"]
    cols = ["id", "content", "created_at"]
    # same rows, same order; the record's numbers arrive as Decimal/float
    result = harness.diff(
        candidate, [("a", "x", 1), ("b", "y", 2)], cols,
        [("a", "x", Decimal("1")), ("b", "y", Decimal("2.0"))], cols,
        ["a", "b"],
    )
    assert result["equal"] is True, result["differences"]
    # …and the same values REORDERED is still the null-placement class
    reversed_rows = harness.diff(
        candidate, [("a", "x", 1), ("b", "y", 2)], cols,
        [("b", "y", Decimal("2.0")), ("a", "x", Decimal("1"))], cols,
        ["a", "b"],
    )
    assert reversed_rows["equal"] is False
    assert [d["kind"] for d in reversed_rows["differences"]] == ["order"]


def test_the_multiset_pass_pairs_rows_with_the_equality_it_compares_with():
    """A multiset that is equal must not report `value` differences.

    ⛔ The pass sorted by `_canon` (a json string) and compared by `_norm`: `1`
    serialises `"1"` and `1.0` as `"1.0"`, and `]` > `,` > `.`, so the two sides
    sorted into OPPOSITE orders and the positional pairing compared different rows
    — two `value` differences on multisets that are equal (`#13098` review P3).
    Reachable from the real stores for the same reason the order pass was: the
    record's numeric columns arrive as `float`/`Decimal`.
    """
    candidate = harness.Candidate(name="numeric", cypher="", sql="",
                                 columns=("id", "confidence"), ordered=False)
    cols = ["id", "confidence"]
    equal = harness.diff(candidate, [("a", 1), ("a", 10)], cols,
                        [("a", 1.0), ("a", 10.0)], cols, ["a"])
    assert equal["equal"] is True, equal["differences"]
    # …and a REAL value difference in the same shape is still reported
    different = harness.diff(candidate, [("a", 1), ("a", 10)], cols,
                            [("a", 1.0), ("a", 11.0)], cols, ["a"])
    assert different["equal"] is False
    assert [(d["kind"], d["cypher"], d["sql"]) for d in different["differences"]] == \
        [("value", 10, 11.0)]


def test_the_multiset_sort_key_agrees_with_the_equality_for_collections_too():
    """The sort key must be a total order that agrees with `_norm_row` — for lists
    and dicts, not only scalars.

    ⛔ `_norm` recurses into collections but a json rendering of its result does
    not, so `[1]` and `[1.0]` stayed different keys and the false `value`
    difference this file's `_sort_key` closes for a scalar column was still
    reproducible for a collection column (`#13098` review P3). The census lists
    plenty of `collect(…)`/`properties(…)` reads, so this is the shape a future
    candidate would hit.
    """
    candidate = harness.Candidate(name="collections", cypher="", sql="",
                                 columns=("id", "tags"), ordered=False)
    cols = ["id", "tags"]
    for cypher_rows, sql_rows in (
        ([("a", [1]), ("a", [10])], [("a", [1.0]), ("a", [10.0])]),
        ([("a", {"k": 1})], [("a", {"k": 1.0})]),
        ([("a", [1, [2]])], [("a", [1.0, [2.0]])]),
    ):
        result = harness.diff(candidate, cypher_rows, cols, sql_rows, cols, ["a"])
        assert result["equal"] is True, (cypher_rows, sql_rows, result["differences"])
    # …and the collection pass still reports a REAL difference
    different = harness.diff(candidate, [("a", [1])], cols, [("a", [2])], cols, ["a"])
    assert different["equal"] is False
    assert [d["kind"] for d in different["differences"]] == ["value"]


def test_a_type_family_inside_a_collection_is_still_a_difference():
    """`bool` vs `int` nested in a list/map/set must redden — it is a FAMILY change.

    ⛔ `_family` answered only for the TOP-LEVEL value, so two lists compared as
    `list` vs `list` and Python's own `[True] == [1]` then called them equal: the
    header's rule ("a type family change — int vs str, bool vs int — is a
    DIFFERENCE") was silently not applied inside a collection, which is a false
    PASS — the worst class this file can produce (`#13098` review P3).
    """
    candidate = harness.Candidate(name="families", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    pairs = (([True], [1]), ({"k": True}, {"k": 1}), ([1], ["1"]), ({True}, {1}),
             # ⛔ a SWAP between two positions: the SET of leaf families is identical
             # on both sides, so a set-based check skipped the gate and Python's own
             # `True == 1` then certified the row — a false PASS (`#13098` review P2)
             ([[True], [1]], [[1], [True]]),
             ({"flag": True, "count": 1}, {"flag": 1, "count": True}),
             # …and a null nested under a key is still three-way
             ({"k": None}, {"k": ""}))
    for cypher_val, sql_val in pairs:
        result = harness.diff(candidate, [("a", cypher_val)], cols,
                             [("a", sql_val)], cols, ["a"])
        assert result["equal"] is False, (cypher_val, sql_val, result["differences"])
        assert [d["kind"] for d in result["differences"]] == ["family"], result["differences"]
    # …and a numeric spelling change inside a collection is NOT a difference
    numeric = harness.diff(candidate, [("a", [1])], cols, [("a", [1.0])], cols, ["a"])
    assert numeric["equal"] is True, numeric["differences"]


def test_a_shape_change_is_a_VALUE_difference_not_a_family_one():
    """Structure is not a family: arity, key names and emptiness must read `value`.

    ⛔ The first cut of the recursive check carried ARITY and KEY NAMES in the
    compared signature, so a list that merely grew an element was relabelled
    `kind: family` — and the `continue` then skipped the value comparison that used
    to name it. That is a diagnostic regression: `kind` is what the CLI prints and
    what §5 of the census doc documents (`#13098` review P3).
    """
    candidate = harness.Candidate(name="shape", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    pairs = (([1], [1, 2]), ([1, 2], [1, 2, 3]), ({"a": 1}, {"a": 1, "b": 2}),
             ({"a": 1}, {"b": 1}), ([], [1]), ({}, {"a": 1}), ([1], []),
             # ⛔ ONE LEVEL DOWN too: `_skeleton`'s entry arm dropped the value
             # subtree, so every map below the top level had one skeleton and a pure
             # structure change was labelled `family` (`#13098` review P3)
             ({"a": [1]}, {"a": [1, 2]}), ({"a": {"x": 1}}, {"a": {"x": 1, "y": 2}}),
             ({"a": {"x": 1}}, {"a": {"y": 1}}), ({"a": {}}, {"a": {"x": 1}}),
             ({"a": {"x": 1}}, {"a": [1]}))
    for cypher_val, sql_val in pairs:
        result = harness.diff(candidate, [("a", cypher_val)], cols,
                             [("a", sql_val)], cols, ["a"])
        assert result["equal"] is False, (cypher_val, sql_val)
        assert [d["kind"] for d in result["differences"]] == ["value"], \
            (cypher_val, sql_val, result["differences"])


def test_the_same_nan_on_both_sides_is_not_a_difference():
    """A NaN must compare EQUAL to itself, at any depth.

    Python says `nan != nan`, so the same NaN on both sides reddened forever on a
    column that can legitimately hold one — and nested it depended on whether the
    driver handed back one object or two, i.e. on allocation rather than on data
    (`#13098` review P3).
    """
    candidate = harness.Candidate(name="nan", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    nan = float("nan")
    for cypher_val, sql_val in ((nan, float("nan")), ([nan], [float("nan")]),
                                ([nan], [nan]), ({"k": nan}, {"k": float("nan")})):
        result = harness.diff(candidate, [("a", cypher_val)], cols,
                             [("a", sql_val)], cols, ["a"])
        assert result["equal"] is True, (cypher_val, sql_val, result["differences"])
    # …but a NaN is NOT equal to a number, to null, or to a STRING that spells it.
    # ⛔ `"NaN"` is the case that matters: canonicalising a NaN to that token put an
    # ordinary string into the number's slot, so a row holding a real `"NaN"`
    # cancelled a NaN in another position (`#13098` review P2).
    for other in (0, 1, None, "nan", "NaN", "NAN"):
        result = harness.diff(candidate, [("a", nan)], cols, [("a", other)], cols, ["a"])
        assert result["equal"] is False, other
    swapped = harness.diff(candidate, [("a", [nan, "NaN"])], cols,
                          [("a", ["NaN", nan])], cols, ["a"])
    assert swapped["equal"] is False, swapped["differences"]
    # ⛔ and the FINGERPRINT must still tell them apart: a str token put the string
    # into the number's slot, so two different rows fingerprinted identically
    nan_fp = harness.diff(candidate, [("a", nan)], cols, [("a", nan)], cols, ["a"])
    str_fp = harness.diff(candidate, [("a", "NaN")], cols, [("a", "NaN")], cols, ["a"])
    assert nan_fp["fingerprint"] != str_fp["fingerprint"], "a NaN and the string 'NaN' render alike"


def test_a_numeric_spelling_change_is_one_number_but_a_rounded_one_is_not():
    """`Decimal`/`float`/`int` compare NUMERICALLY — and EXACTLY.

    ⛔ `_norm` converted a `Decimal` to `float`, which ROUNDS: two distinct numerics
    beyond the float mantissa became the same number and a real difference read
    EQUAL — a false PASS in the value pass (`#13098` review P3).
    """
    from decimal import Decimal

    candidate = harness.Candidate(name="numeric", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    for left, right in ((Decimal("1"), 1), (Decimal("1.0"), 1), (Decimal("1"), 1.0),
                        (Decimal("NaN"), float("nan"))):
        result = harness.diff(candidate, [("a", left)], cols, [("a", right)], cols, ["a"])
        assert result["equal"] is True, (left, right, result["differences"])
    # …and the two values `float()` folds together are NOT one number
    for left, right in ((Decimal("9007199254740993"), Decimal("9007199254740992")),
                        (Decimal("9007199254740993"), 9007199254740992)):
        result = harness.diff(candidate, [("a", left)], cols, [("a", right)], cols, ["a"])
        assert result["equal"] is False, (left, right)


def test_a_bytes_like_compares_and_renders_by_content_not_by_address():
    """`str(memoryview(b"x"))` is an ADDRESS, so rendering it reported a false `value`.

    psycopg2 returns `memoryview` for `bytea`, so this is the common bytea path, and
    the fingerprint of one row changed per object (`#13098` review P3).
    """
    candidate = harness.Candidate(name="bytes", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    same = harness.diff(candidate, [("a", memoryview(b"x"))], cols,
                       [("a", memoryview(b"x"))], cols, ["a"])
    assert same["equal"] is True, same["differences"]
    # the two driver spellings of one `bytea` are one family
    across = harness.diff(candidate, [("a", b"x")], cols, [("a", memoryview(b"x"))], cols, ["a"])
    assert across["equal"] is True, across["differences"]
    # …and different content is a difference, not a representation one
    differ = harness.diff(candidate, [("a", b"x")], cols, [("a", b"y")], cols, ["a"])
    assert differ["equal"] is False and [d["kind"] for d in differ["differences"]] == ["value"]
    # ⛔ TWO rows in BOTH orders: rendering the ADDRESS sorted the same data into two
    # different orders, so the multiset pass could pair the wrong rows and invent a
    # `value` difference on identical data.
    for rows in ([("a", memoryview(b"x")), ("b", memoryview(b"y"))],
                 [("b", memoryview(b"y")), ("a", memoryview(b"x"))]):
        same_rows = harness.diff(candidate, rows, cols, rows, cols, ["a"])
        assert same_rows["equal"] is True, same_rows["differences"]
    # ⛔ the FINGERPRINT is a function of the data, not of the object: at the parent
    # commit these two measurements of the identical row differed (`#13098` review P3)
    one = harness.diff(candidate, [("a", memoryview(b"x"))], cols,
                       [("a", memoryview(b"x"))], cols, ["a"])
    two = harness.diff(candidate, [("a", memoryview(b"x"))], cols,
                       [("a", memoryview(b"x"))], cols, ["a"])
    assert one["fingerprint"] == two["fingerprint"], "the fingerprint follows object identity"
    other = harness.diff(candidate, [("a", memoryview(b"y"))], cols,
                        [("a", memoryview(b"y"))], cols, ["a"])
    assert one["fingerprint"] != other["fingerprint"]


def test_a_non_string_dict_key_does_not_take_the_diff_down():
    """A map KEY of any type must not raise after every comparison has succeeded.

    `json.dumps` accepts only str/int/float/bool/None keys — a bare `_norm` dump
    raised there — and `_norm`'s own `sorted(value.items())` raised for a map mixing
    key TYPES. Either one is a `TypeError` out of `diff()` instead of a verdict
    (`#13098` review P3). Reachability is nil today (psycopg2's `jsonb` and
    FalkorDB's maps both yield str keys), which is why it needs a pin.
    """
    from decimal import Decimal

    candidate = harness.Candidate(name="keys", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    rows = [("a", {Decimal("1"): "x"}),
            ("a", {Decimal("1"): "x", "a": "y"}),   # two key TYPES
            ("a", {1: "x", "1": "y"})]              # two keys, one rendering
    for row in rows:
        result = harness.diff(candidate, [row], cols, [row], cols, ["a"])
        assert result["equal"] is True, (row, result["differences"])
        assert result["fingerprint"], "the fingerprint is computed after the sort — it must survive"
    # ⛔ and the fingerprint must still tell two such maps APART: a bare `str(k)`
    # collapsed `1` and `"1"` into one entry and dropped the other's value, so two
    # different rows fingerprinted identically (`#13098` review P3).
    left = harness.diff(candidate, [("a", {1: "x", "1": "y"})], cols,
                       [("a", {1: "x", "1": "y"})], cols, ["a"])
    right = harness.diff(candidate, [("a", {1: "x", "1": "z"})], cols,
                        [("a", {1: "x", "1": "z"})], cols, ["a"])
    assert left["fingerprint"] != right["fingerprint"], "a map key was dropped from the rendering"
    # ⛔ and a NON-JSON leaf (`date`, `bytes`) must not collide with the string that
    # spells it — the same identity loss, one line below the NaN arm
    import datetime

    for value, spelling in ((datetime.date(2020, 1, 1), "2020-01-01"), (b"x", "x")):
        left = harness.diff(candidate, [("a", value)], cols, [("a", value)], cols, ["a"])
        right = harness.diff(candidate, [("a", spelling)], cols, [("a", spelling)], cols, ["a"])
        assert left["fingerprint"] != right["fingerprint"], (value, spelling)

def test_a_projection_that_drifts_on_both_sides_still_reddens_against_the_declaration():
    """`Candidate.columns` is a CONTRACT, not a comment (`#13098` review P3).

    The cross-engine checks alone cannot see this: both stores dropping the same
    column agrees with itself, so only the declared list can catch it.
    """
    candidate = harness.CANDIDATES["point-by-id"]  # declares id/content/point_kind
    result = harness.diff(
        candidate, [("a", "x")], ["id", "content"],
        [("a", "x")], ["id", "content"],
        ["a"],
    )
    assert result["equal"] is False
    shape = [d for d in result["differences"] if d["kind"] == "shape"]
    assert shape, result["differences"]
    assert shape[0]["detail"] == "declared column list"
    assert shape[0]["declared"] == ["id", "content", "point_kind"]


def test_an_unordered_statement_is_compared_as_a_multiset():
    """Row order is not part of an unordered statement's answer."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate, [("a", "x", None), ("b", "y", None)], ["id", "content", "point_kind"],
        [("b", "y", None), ("a", "x", None)], ["id", "content", "point_kind"],
        ["a", "b"],
    )
    assert result["equal"] is True, result["differences"]


def test_a_dropped_column_is_a_shape_difference():
    """Projection drift is not a value difference and must not be absorbed."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate, [("a", "x")], ["id", "content"],
        [("a", "x", None)], ["id", "content", "point_kind"],
        ["a"],
    )
    assert result["equal"] is False
    assert any(d["kind"] == "shape" for d in result["differences"])


def test_the_fingerprint_is_stable_across_runs():
    candidate = harness.CANDIDATES["point-by-id"]
    args = ([("a", "x", None)], ["id", "content", "point_kind"],
            [("a", "x", None)], ["id", "content", "point_kind"], ["a"])
    first = harness.diff(candidate, *args)["fingerprint"]
    second = harness.diff(candidate, *args)["fingerprint"]
    assert first == second


def test_a_credential_never_reaches_the_output():
    """The secret-hygiene rule the record modules apply, applied here."""
    redacted = harness._redact("postgresql://postgres:s3cret@127.0.0.1:15499/db")
    assert "s3cret" not in redacted
    assert "postgres:***@" in redacted


def test_a_password_containing_an_at_sign_is_redacted_whole():
    """`@` is legal unencoded in a password, and the authority ends at the LAST one.

    The pre-fix `_redact` split on the FIRST `@`, so
    `postgresql://postgres:p@ssw0rd@host/db` printed `ssw0rd` — the tail of a
    working credential, in CI logs, on every run (`#13098` review). libpq and
    redis-py both split userinfo at the last `@`, so this is reachable with a
    real generated DSN.
    """
    leaked = harness._redact("postgresql://postgres:p@ssw0rd@127.0.0.1:15499/db")
    assert "ssw0rd" not in leaked, f"the password tail reached the output: {leaked}"
    assert "p@ssw0rd" not in leaked
    assert leaked == "postgresql://postgres:***@127.0.0.1:15499/db"


def test_the_benchmark_reports_no_verdict_and_still_checks_the_corpus(monkeypatch):
    """`--bench` is a measurement: it must not exit 0, and it must still refuse a
    corpus that lacks a declared edge.

    Exit 0 is this tool's word for "the two forms are the same answer". `--bench`
    diffed nothing and used to return 0 — before the corpus-edge check, too — so
    a caller keying on 0 read a timing run as agreement (`#13098` review).
    """
    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 6})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 6})
    monkeypatch.setattr(harness, "run_cypher",
                        lambda *_a, **_k: ([("p-null-content",), ("p-retired",), ("p-null-created",)], ["id"]))
    monkeypatch.setattr(harness, "run_sql",
                        lambda *_a, **_k: ([("p-null-content",), ("p-retired",), ("p-null-created",)], ["id"]))
    monkeypatch.setattr(harness, "bench", lambda *_a, **_k: {"candidate": "point-listing"})
    argv = ["--graph-uri", "docker://:p@127.0.0.1:1",
            "--record-dsn", "postgresql://u:p@127.0.0.1/db",
            "--bench", "10"]

    # (a) the corpus edge is present -> the bench runs, and it does NOT report equal
    assert harness.main([*argv, "--candidate", "point-listing"]) == harness.EXIT_NO_VERDICT
    assert harness.EXIT_NO_VERDICT != 0

    # (b) the corpus edge is ABSENT -> refused with 2, and `bench` never ran
    monkeypatch.setattr(harness, "run_cypher", lambda *_a, **_k: ([], ["id"]))
    monkeypatch.setattr(harness, "run_sql", lambda *_a, **_k: ([], ["id"]))
    ran = []
    monkeypatch.setattr(harness, "bench", lambda *_a, **_k: ran.append(1) or {})
    assert harness.main([*argv, "--candidate", "point-listing"]) == 2
    assert ran == [], "a corpus missing a declared edge must be refused BEFORE it is measured"


def test_an_unexpected_failure_is_could_not_run_not_a_difference(monkeypatch, capsys):
    """Exit **1** means "the diff is NON-EMPTY" — so a crash must not be able to reach it.

    `bench()` was called outside every inner `try` in `main()`, and `main()` had no
    outer boundary, so any exception there escaped as the interpreter's own exit 1:
    this tool's word for a FINDING. That is not cosmetic — the CI mismatch step
    (`cypher-diff.yml`) passes only on `rc == 1`, because 1 is the evidence that the
    harness CAN fail, so a crash satisfied the assertion the job exists for
    (`#13098` review P2).

    Arm (a) is the real reproducer, not a synthetic raise: `--bench 10 --bench-iters 0`
    reaches `statistics.median([])` inside `bench()` and raises `StatisticsError`.
    Arms (b)/(c) keep the boundary honest: it is not exception-type-specific, and it
    does not swallow a genuine verdict.
    """
    import statistics

    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 6})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 6})
    ids = [("p-null-content",), ("p-retired",), ("p-null-created",)]
    monkeypatch.setattr(harness, "run_cypher", lambda *_a, **_k: (ids, ["id"]))
    monkeypatch.setattr(harness, "run_sql", lambda *_a, **_k: (ids, ["id"]))
    argv = ["--graph-uri", "docker://:p@127.0.0.1:1",
            "--record-dsn", "postgresql://u:p@127.0.0.1/db"]
    bench_argv = [*argv, "--bench", "10", "--bench-iters", "0"]

    # (a) the real defect: median([]) inside bench() — must be 2 (could not run), never 1
    def median_of_nothing(*_a, **_k):
        return statistics.median([])

    monkeypatch.setattr(harness, "bench", median_of_nothing)
    assert harness.main([*bench_argv, "--candidate", "point-listing"]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err, err
    assert "StatisticsError" in err, err
    assert 2 != 1, "the whole finding: a crash must not share the code that means 'different'"

    # (b) the boundary is not exception-type-specific
    def boom(*_a, **_k):
        raise RuntimeError("the driver changed its mind")

    monkeypatch.setattr(harness, "bench", boom)
    assert harness.main([*bench_argv, "--candidate", "point-listing"]) == 2
    assert "RuntimeError" in capsys.readouterr().err

    # (c) …and it does NOT absorb a verdict: a real difference is still 1
    monkeypatch.setattr(harness, "diff", lambda *_a, **_k: {
        "equal": False, "differences": [{"kind": "row-count"}],
        "columns": {"cypher": ["id"], "sql": ["id"]}, "fingerprint": "f"})
    assert harness.main([*argv, "--candidate", "point-listing", "--json"]) == 1


def test_a_listing_is_not_a_verdict(capsys):
    """`--list` prints candidates and compares nothing, so it is not exit 0 either."""
    assert harness.main(["--list"]) == harness.EXIT_NO_VERDICT
    assert "point-by-id" in capsys.readouterr().out


def test_every_declared_corpus_edge_exists_in_the_shared_corpus():
    """A `requires` name that no corpus row provides would refuse EVERY run."""
    ids = {row["id"] for row in harness.CORPUS}
    for name, candidate in harness.CANDIDATES.items():
        for edge in candidate.requires:
            assert edge in ids, f"{name} requires {edge!r}, which no corpus row provides"


def test_the_empty_result_is_compared_by_a_candidate_not_merely_declared(monkeypatch):
    """The EMPTY result must be an edge the harness actually asks both stores for.

    The docstring used to declare an empty-tenant edge that no candidate
    queried, so a port that turned an empty answer into an error or `None`
    would not have been caught (`#13098` review F2).
    """
    candidate = harness.CANDIDATES["point-by-absent-id"]
    # no `requires` entry can assert an ABSENCE, so the candidate's honesty rests
    # on the id being absent from every fixture — including a future corpus edit
    assert candidate.requires == ()
    assert candidate.params == {"id": harness.ABSENT_ID}
    assert harness.ABSENT_ID not in {row["id"] for row in harness.CORPUS}
    # zero rows on both sides, same columns: that IS the same answer
    result = harness.diff(candidate, [], ["id", "content", "point_kind"],
                          [], ["id", "content", "point_kind"], [])
    assert result["equal"] is True and result["row_count"] == {"cypher": 0, "sql": 0}
    # ⛔ an empty answer whose projection is MISSING is not the same answer — the
    # shape a port returning `None`/a bare empty instead of no-rows would have
    assert harness.diff(candidate, [], [], [],
                        ["id", "content", "point_kind"], [])["equal"] is False
    # and the CLI path reaches that verdict end to end: empty stores -> exit 0
    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 0})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 0})
    monkeypatch.setattr(harness, "run_cypher",
                        lambda *_a, **_k: ([], ["id", "content", "point_kind"]))
    monkeypatch.setattr(harness, "run_sql",
                        lambda *_a, **_k: ([], ["id", "content", "point_kind"]))
    assert harness.main(["--graph-uri", "docker://:p@127.0.0.1:1",
                         "--record-dsn", "postgresql://u:p@127.0.0.1/db",
                         "--candidate", "point-by-absent-id"]) == 0


def test_a_missing_psql_is_could_not_run_not_a_difference(monkeypatch, capsys):
    """A setup failure must never be reported as `1` = "the two forms DIFFER".

    `bootstrap_record` called `subprocess.run(["psql", …])` outside any `try`, so
    a box without the client raised `FileNotFoundError`, escaped `main()`, and
    the interpreter exited **1** — this tool's code for a FINDING. The harness
    would report its own missing dependency as a porting defect (`#13098`
    review).
    """
    def _missing(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory", "psql")

    monkeypatch.setattr(harness.subprocess, "run", _missing)
    assert harness.bootstrap_record("postgresql://u:p@127.0.0.1/db") == 2
    assert "could not run" in capsys.readouterr().err


def test_bootstrap_refuses_when_the_record_schema_never_materialises(monkeypatch, capsys):
    """`public.point` absent after the migrations is 2, never 0.

    The function used to `return 0` unconditionally, so `bootstrapped 0/85
    migrations` reported success and the caller's `rc != 0` guard was dead code.
    A partial apply IS tolerated (the record builds on the control-plane schema),
    but only while the schema the corpus needs exists.
    """
    class _Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    monkeypatch.setattr(harness.subprocess, "run", lambda *_a, **_k: _Proc())
    assert harness.bootstrap_record("postgresql://u:p@127.0.0.1/db") == 2
    assert "no public.point" in capsys.readouterr().err


def test_bootstrap_tolerates_a_partial_apply_that_does_build_the_record(monkeypatch):
    """A file that fails does not stop the record from existing — and that is 0."""
    class _Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    calls = {"n": 0}

    def _run(argv, **_k):
        calls["n"] += 1
        if "-tAc" in argv:                       # the to_regclass probe
            return _Proc(0, "public.point\n")
        if "-f" in argv and "0001_user_teams" in argv[-1]:
            return _Proc(1, "", "psql:…: ERROR: schema \"auth\" does not exist")
        return _Proc()

    monkeypatch.setattr(harness.subprocess, "run", _run)
    assert harness.bootstrap_record("postgresql://u:p@127.0.0.1/db") == 0
    assert calls["n"] > 1


@requires_doc_pin
def test_the_files_scanned_count_is_pinned_too(tmp_path):
    """§0 publishes "295 files scanned", which `files` (the files WITH a site) is not.

    `check()` compared only the keys it computed, so the doc's scanned count could
    drift by any amount while `--check` stayed green (`#13098` review P3).

    ⛔ `@requires_doc_pin` (#2148/B1), and the NON-VACUITY assert is what makes
    that safe. This arm's mutation is a `str.replace` of the pin AT THE MEASURED
    VALUE, so once the committed pin is stale the replace is a NO-OP and
    `check()` answers 1 — and prints `DRIFT files_scanned` — for the STALE PIN
    ITSELF, so every assertion below passes while the drift path this arm exists
    for was never reached. Measured on the pre-fix arm: `files_scanned`
    simulated `+3` gave `rc=1` and `DRIFT files_scanned: doc=308 tool=311`, i.e.
    GREEN under drift. The mutation is therefore asserted to have CHANGED the
    document before it is graded.
    """
    result = census.census(("tortoise",))
    doc_text = _source_text()
    assert "<!-- cypher-inventory:files_scanned = " in doc_text
    path = tmp_path / "doc.md"
    path.write_text(doc_text.replace(
        f"<!-- cypher-inventory:files_scanned = {result['files_scanned']} -->",
        f"<!-- cypher-inventory:files_scanned = {result['files_scanned'] + 1} -->", 1),
        encoding="utf-8")
    assert path.read_text(encoding="utf-8") != doc_text, (
        "the mutation did not change the document, so `check()` below would be "
        "grading the STALE PIN rather than the mutated one — this arm's own "
        "failure class, one level in")
    assert census.check(path, result) == 1
    # ⛔ AND it is reported as a COUNT MISMATCH, not as a pin the tool stopped
    # computing: dropping the key from `check()`'s `actual` still reddens through
    # the STALE arm, so `== 1` alone cannot tell the two apart (measured — that
    # mutant survived this assertion).
    import contextlib
    import io

    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        census.check(path, result)
    assert "DRIFT files_scanned" in captured.getvalue(), captured.getvalue()


def test_a_corpus_row_that_lost_its_edge_property_is_refused(monkeypatch, capsys):
    """Presence is not the pin: the NULL that makes `p-null-created` an edge case is.

    A seeding change that keeps the row in both stores but gives it a
    `created_at` disarms the `null-order` mutation — the run still exits 1, for
    the wrong reason — while the presence pin stays green (`#13098` review P3).
    """
    ids = [row["id"] for row in harness.CORPUS]
    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 6})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 6})

    def _run(statement, *_a, **_k):
        # every presence question is answered YES; every PROPERTY question is
        # answered NO — exactly the shape a stripped property produces
        if "WHERE n." in statement or " IS NULL" in statement or " IS NOT NULL" in statement:
            return [], ["id"]
        return [(i,) for i in ids], ["id"]

    monkeypatch.setattr(harness, "run_cypher", lambda _u, _g, cypher, params: _run(cypher))
    monkeypatch.setattr(harness, "run_sql", lambda _d, sql, params: _run(sql))
    rc = harness.main(["--graph-uri", "docker://:p@127.0.0.1:1",
                       "--record-dsn", "postgresql://u:p@127.0.0.1/db",
                       "--candidate", "point-listing"])
    assert rc == 2
    assert "PROPERTY" in capsys.readouterr().err


class _FakeGraph:
    """A graph that reports its labels and its Point ids — nothing else is read."""

    def __init__(self, labels, ids):
        self.labels, self.ids = labels, ids

    def query(self, statement):
        from types import SimpleNamespace

        if "UNWIND labels" in statement:
            return SimpleNamespace(result_set=[(label,) for label in self.labels])
        return SimpleNamespace(result_set=[(i,) for i in self.ids])


def test_a_graph_this_harness_did_not_build_is_refused_not_wiped():
    """The seed DETACH DELETEs every `:Point` in its target, so the target is checked.

    Two refusals, both raising (which `main` turns into exit 2): a node label the
    harness never writes, and a Point id it never wrote — the shapes a mistyped
    `--graph-name`, or a target inherited from `TORTOISE_DB_URI`, would produce
    (`#13098` review P2-3).
    """
    with pytest.raises(RuntimeError, match="Operator"):
        harness._refuse_a_foreign_graph(_FakeGraph({"Point", "Operator"}, []), "real-graph")
    with pytest.raises(RuntimeError, match="never wrote"):
        harness._refuse_a_foreign_graph(_FakeGraph({"Point"}, ["a-real-point-id"]), "real-graph")
    # its own corpus graph, and its own bench padding, is the one shape it accepts
    harness._refuse_a_foreign_graph(
        _FakeGraph({"Point"}, [row["id"] for row in harness.CORPUS] + ["p-pad-00001"]),
        "cypher-diff",
    )


@pytest.mark.parametrize("knob", [None, "none", "0"],
                         ids=["unset", "opt-out-none", "opt-out-zero"])
def test_the_seed_refuses_a_foreign_graph_before_it_deletes(monkeypatch, knob):
    """The guard is WIRED into `seed_graph`, not merely available.

    ⛔ Calling `_refuse_a_foreign_graph` directly (the test above) stays green if
    the call site is deleted — measured: replacing it with `pass` survived. This
    drives `seed_graph` through a stand-in `falkordb` so a missing call REDDENS.

    ⛔ The stand-in `from_url` takes `**kwargs` because the construction now
    carries the product's read bound (#13211 governs this fix; #13140 is the
    defect's origin) — and it ASSERTS the bound rather than accepting it
    blindly: a stub that swallowed `**kwargs` would leave the seed's socket
    policy unpinned here, and `socket_timeout` absent from the received kwargs
    reds this test (the shape the seed had before the fix).

    ⛔ IT ASSERTS THE BOUND WAS *PASSED*, NOT THE VALUE IT RESOLVED TO (#13212
    review P2-1). The first cut asserted `seen["socket_timeout"] is not None`,
    which is the RESOLVED value: `_bounded_server_socket_kwargs()` returns
    `socket_timeout=None` for every spelling of the DOCUMENTED opt-out
    (`TORTOISE_DB_SOCKET_TIMEOUT` = `none`/`off`/`false`/`0`/any value ≤ 0, see
    `tortoise/projection/__init__.py`), a configuration the sibling suite pins
    as CORRECT — so an operator shell that exported the opt-out reddened this
    row on a tree that obeyed #3065, accusing the seed of a defect it did not
    have. The presence of the kwarg is the thing this pin is about; its VALUE is
    the operator's business, asserted below only for the two documented shapes.
    """
    from types import SimpleNamespace

    # The operator's own shell must not decide this row's verdict — the repo's
    # established shape for that (`tests/test_cache_eviction_census.py`, and
    # `_graph_viz_connection_module` in `test_bounded_raw_clients_3065.py`).
    monkeypatch.delenv("TORTOISE_DB_SOCKET_TIMEOUT", raising=False)
    monkeypatch.delenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", raising=False)
    if knob is not None:
        monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", knob)

    client = SimpleNamespace(select_graph=lambda name: _FakeGraph({"Point", "Operator"}, []))
    seen: dict = {}

    def _from_url(url, **kwargs):
        seen.update(kwargs)
        return client

    monkeypatch.setitem(sys.modules, "falkordb",
                        SimpleNamespace(FalkorDB=SimpleNamespace(from_url=_from_url)))
    with pytest.raises(RuntimeError, match="Operator"):
        harness.seed_graph("docker://:p@127.0.0.1:1", "real-graph")
    assert "socket_timeout" in seen, (
        "the seed's client was constructed with NO read bound (#3065/#8118): "
        f"from_url received {sorted(seen)}")
    if knob is None:
        assert seen["socket_timeout"] > 0, (
            "the seed's client received `socket_timeout=None` with no opt-out "
            f"exported (#3065/#8118): from_url received {seen}")
    else:
        assert seen["socket_timeout"] is None, (
            f"TORTOISE_DB_SOCKET_TIMEOUT={knob} is the documented unbounded "
            "opt-out and the resolver's answer must reach the seed too — this "
            f"arm exists so the pin above cannot accuse correct code: {seen}")


@pytest.mark.parametrize("axis", [None, "--graph-uri", "--record-dsn"],
                         ids=["both-omitted", "graph-uri", "record-dsn"])
def test_the_store_targets_are_named_never_inherited_from_the_environment(monkeypatch, capsys, axis):
    """⛔ No ambient `TORTOISE_DB_URI` / `TORTOISE_RECORD_DSN` default — PER AXIS.

    Both targets defaulted to the PRODUCT's own stores while the seed DELETEs the
    target graph's `:Point` rows and rewrites its tenant's `public.point` rows, so
    a bare invocation in a developer shell could wipe the store that shell pointed
    at (`#13098` review P2-3). They are command-line only now — including when the
    environment DOES name a store.

    ⛔ WHY THIS IS PARAMETRISED AND NOT A SINGLE `main([...])` CALL. The guard it
    exercises is `if not args.graph_uri or not args.record_dsn`, so with BOTH
    arguments omitted it fires on whichever axis is still empty — and a regression
    that restores the OTHER axis from the environment is then invisible to the
    assertion (`#13098` review F13; measured: reintroducing the ambient
    `--graph-uri` default left this test GREEN, because the empty `--record-dsn`
    triggered the refusal first). Each axis is therefore tested with the OTHER
    target supplied EXPLICITLY, so the only thing that can stop the seed is the
    axis under test. The `both-omitted` case is kept because it is the guard's own
    contract and it costs nothing.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://postgres:x@127.0.0.1:5432/db")
    # ⛔ the refusal must happen BEFORE any store is touched: restoring EITHER
    # ambient default is invisible to an exit-code-only assertion (the seed fails
    # on an unreachable lane and returns 2 as well — measured, that mutant
    # survived), so the stubs below fail loudly instead.
    monkeypatch.setattr(harness, "seed_graph",
                        lambda *_a, **_k: pytest.fail("must not seed from an ambient target"))
    monkeypatch.setattr(harness, "seed_record",
                        lambda *_a, **_k: pytest.fail("must not seed from an ambient target"))
    # The axis NOT under test is supplied explicitly, so the sibling arm of the
    # `or` cannot answer for it.
    other = {
        "--graph-uri": ["--record-dsn", "postgresql://u:p@127.0.0.1:5432/db"],
        "--record-dsn": ["--graph-uri", "docker://:p@127.0.0.1:1"],
    }.get(axis, [])
    assert harness.main(["--candidate", "point-by-id", *other]) == 2
    assert "could not run" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the installing step must PROVE the client RUNS (PR #13314 review)
# --------------------------------------------------------------------------- #
# ⛔ WHY THIS SUITE PINS A WORKFLOW AND NOT THE HARNESS. The harness already
# refuses correctly when `psql` is missing (2 = "could not run"), and
# `test_a_missing_psql_is_could_not_run_not_a_difference` pins that. The gap was
# one level up: the CI step that INSTALLS the client claimed to prove the binary
# exists, and proved only that the name resolves. Measured on run 44158572832 at
# `aff4eaef81`: `apt-get install postgresql-client` exited 0 having unpacked just
# the metapackage, and the very next step died with
# `FileNotFoundError: [Errno 2] … 'psql'`.
#
# Mutation attribution, as the review asked: `git grep -ln "cypher-diff"` over
# tests/ found nothing asserting this step, so every one of these properties was
# unpinned — deleting the refusal, the probe or the `$GITHUB_PATH` repair left
# the suite GREEN. They are pinned here because the fail-closed branch IS the
# change, and a one-line edit that removes it would otherwise be invisible.

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "cypher-diff.yml"


def test_the_doc_pin_env_has_a_producer_and_that_producer_RUNS_this_file():
    """#2148/B1: the `@requires_doc_pin` skip is default-OFF, so its producer is a GATE.

    The doc-vs-tree arms above skip unless `TORTOISE_CYPHER_DOC_PIN=1`, and the ONE
    place that sets it is a step of `cypher-diff.yml` — a workflow this module does
    not control. Delete, rename or detach that `env:` entry and every gated arm — each
    single one plus each parametrized duplicate-arm case, counted from
    `_GRADED_LOOKUPS` rather than typed here — goes back to SKIPPING in
    every job in the repo, with no red anywhere to notice: a pin that appears to
    protect the census and enforces nothing, which is the INERT-ENFORCER class the
    decoupling was allowed to create only because this pin closes it.

    Pinned HERE, beside the arms that depend on it, for the same reason the
    `psql`-install step below is pinned rather than trusted: that file has already
    shipped a step whose removal would have left the suite green.
    """
    steps = yaml.safe_load(WORKFLOW_PATH.read_text())["jobs"]["differential"]["steps"]
    runners = [s for s in steps
               if "pytest" in (s.get("run") or "")
               and "tests/test_13098_cypher_inventory.py" in (s.get("run") or "")]
    assert len(runners) == 1, (
        "expected EXACTLY ONE `cypher-diff` step to run this file; got "
        f"{[(s.get('name'), s.get('run')) for s in runners]} — with none, the "
        "doc-vs-tree arms skip in every job and nothing reds")
    env = runners[0].get("env") or {}
    assert env.get(_DOC_PIN_ENV) == "1", (
        f"the step that runs this file does not set {_DOC_PIN_ENV}=1 (its env is "
        f"{env!r}) — every `@requires_doc_pin` arm would skip silently, so the "
        "census document would be graded by NOTHING")
    # ⛔ AND THE SESSION GATE MUST BE SATISFIED, or the step errors before a single
    # arm runs: `tests/conftest.py::_p4_uri_required` refuses a URI-less session
    # unless `TORTOISE_DB_URI` or `TORTOISE_TEST_CARVE_OUT` is set, and this job
    # exports no `TORTOISE_DB_URI` (its stores reach the harness through
    # `--graph-uri`/`--record-dsn`). Measured at this head: with the doc-pin env set
    # and this flag absent the file reports `90 errors, 0 skipped` — every case,
    # including this pin. So both halves of "the step RUNS" are asserted, and a
    # future edit that drops either one reds HERE rather than in a job nobody
    # watches.
    assert env.get("TORTOISE_DB_URI") or env.get("TORTOISE_TEST_CARVE_OUT") == "1", (
        "the step that runs this file satisfies neither half of the session URI gate "
        f"(`TORTOISE_DB_URI` or `TORTOISE_TEST_CARVE_OUT=1`; its env is {env!r}), so "
        "pytest errors in session setup and no census arm runs at all")
    # …and the arms it produces for are still here. A structural count, not a
    # reading of the file's content: the decorator is applied at 8 arms + 1
    # parametrized one (one case per `_GRADED_LOOKUPS` row) — 8 since #13098 split the
    # table-shape arm, whose rendered half now renders a document — and an arm leaving
    # this module is a diff a reviewer sees; what is invisible is the ENV disappearing,
    # which is above.
    #
    # ⛔ COUNTED AS DECORATORS, never as the raw token: `str.count("@requires_doc_pin")`
    # is 18 at this head — 9 decorator lines PLUS 9 prose/format mentions: FIVE in
    # four docstrings (one of them names it twice), TWO in block comments (this one
    # included), ONE in an f-string and ONE in an assertion literal. ⛔ Re-measure
    # rather than trusting these numbers: `#13098`'s own review rounds added mentions
    # faster than the sentence describing them was re-read. Measured, not assumed: under the previous `>= 8` form, deleting SEVEN
    # of the eight gated arms left the count at `1 decorator + 7 prose = 8` and this
    # row stayed GREEN, so the arm it claims to protect ("the arms this pin exists for
    # are gone") did not fire until seven eighths of them were gone. The AST cannot
    # see prose; `== 9` rather than `>= 9` means a TENTH gated arm is a red a reviewer
    # reads instead of slack that a silently-deleted arm can absorb.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    decorated = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and any(
            isinstance(dec.func if isinstance(dec, ast.Call) else dec, ast.Name)
            and (dec.func if isinstance(dec, ast.Call) else dec).id == "requires_doc_pin"
            for dec in node.decorator_list
        )
    ]
    assert len(decorated) == 9, (
        "expected EXACTLY 9 `@requires_doc_pin` arms (8 single + 1 parametrized); got "
        f"{len(decorated)}: {decorated} — the arms this pin exists for are gone; "
        "delete this pin with them, or restore them")
    # AND THE UNGATED HALF. The count above sees only DECORATED arms; the arms that
    # carry #13098's own split invariants are deliberately UNGATED (they cost no tree
    # walk and must ride the required gate), so deleting one would leave no red
    # anywhere — the invisible-deletion class this pin exists for, one set over.
    # Pinned by NAME, because each name states the invariant it protects: the document
    # is untracked, the appendix cannot be pasted back into the source, the render
    # target cannot be the source or any other tracked path, a rendered document
    # cannot contradict its own pins, a run that cannot read/probe/write answers 2
    # rather than the DRIFT code 1, and the row guard matches the GENERATOR's shape
    # rather than any table whose first cell is a `file:line`.
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    split_arms = {
        "test_the_rendered_document_is_not_tracked",
        "test_the_renderer_refuses_a_source_that_carries_the_appendix",
        "test_the_renderer_refuses_to_write_over_the_source",
        "test_the_renderer_refuses_to_write_over_a_tracked_path",
        "test_the_renderer_refuses_the_all_surface_combination",
        "test_the_renderer_reports_a_probe_failure_as_could_not_run",
        "test_the_renderer_reports_a_write_failure_as_could_not_run",
        "test_a_hand_authored_prose_row_is_not_read_as_a_census_row",
    }
    missing = sorted(split_arms - defined)
    assert not missing, (
        f"the #13098 split's own invariant arms are GONE: {missing} — they are "
        "ungated on purpose (so they ride the required gate) and NOTHING else "
        "asserts what they carry; restore them")


def _psql_install_run() -> str:
    """The `run:` block of the step that installs the client — the artifact under test."""
    doc = yaml.safe_load(WORKFLOW_PATH.read_text())
    steps = doc["jobs"]["differential"]["steps"]
    match = [s for s in steps if "install postgresql-client" in (s.get("name") or "")]
    assert len(match) == 1, f"expected exactly one client-install step, got {len(match)}"
    return match[0]["run"]


def _run_install_step(tmp_path, psql_body: str | None, versioned_psql: str | None = None):
    """Execute that step hermetically: `sudo`/`dpkg`/`ls` are stubs, the client is the variable.

    `psql_body=None` puts NO `psql` on PATH, and PATH carries **nothing from the host**
    but the shell plumbing the step itself invokes. That hermeticity is load-bearing:
    with `PATH=f"{bin_dir}:/usr/bin:/bin"` — the previous spelling — `command -v psql`
    searched `/usr/bin`, so on any box carrying a real client the MISSING-client arm
    resolved the HOST's binary and never reached the refusal it exists to pin (measured
    on this host before the fix: `psql: /usr/bin/psql (psql (PostgreSQL) 16.15 …)`,
    exit 0, `$GITHUB_PATH`=/usr/bin — #13314 review, P1). Nothing here touches the
    network or a database — the step's own shell is the code under test.

    `versioned_psql=<script>` installs that client at `<tmp>/vendor/bin/psql` and makes
    `ls` report `<tmp>/vendor/bin` as the `/usr/lib/postgresql/*/bin` hit, so the
    step's FALLBACK arm is reachable without consulting the host either. It defaults to
    `None`, where `ls` exits 1 and nothing resolves. That arm pins the fallback
    RESOLUTION and the probe/reach-`$GITHUB_PATH` behaviour, not the `sort -V` version
    SELECTION — `sort` is stubbed to a pass-through for exactly that reason.

    ⛔ `ls` IS A STUB, NOT A HOST SYMLINK, AND THAT IS A MEASUREMENT. This host carries
    `/usr/lib/postgresql/16/bin/psql`, so a real `ls` on a hermetic PATH would let the
    step's fallback resolve a working client, the refusal would never be reached, and
    the MISSING-client arm would be host-dependent again — the very defect being fixed.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("sudo", "dpkg"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    # `ls` NEVER sees the host's `/usr/lib/postgresql`: the arms under test are "nothing
    # resolves", "the client on PATH is the one we supplied" and (via `versioned_psql`)
    # "only a versioned client outside PATH resolves".
    ls_stub = bin_dir / "ls"
    if versioned_psql is None:
        ls_stub.write_text("#!/bin/sh\nexit 1\n")
    else:
        vendor_bin = tmp_path / "vendor" / "bin"
        vendor_bin.mkdir(parents=True)
        fake = vendor_bin / "psql"
        fake.write_text(versioned_psql)
        fake.chmod(0o755)
        ls_stub.write_text(f"#!/bin/sh\necho '{vendor_bin}'\n")
    ls_stub.chmod(0o755)
    # Shell plumbing, not the subject under test: `bash` (which `subprocess` must resolve
    # THROUGH this PATH, since `env` is passed), and the utilities the step's own lines
    # invoke. Omitting them would turn this into an assertion about which coreutils a
    # runner ships instead of about the step's logic — so a host missing one is a LOUD
    # failure here, not a silently wrong verdict three assertions later.
    for name in ("bash", "dirname", "tail"):
        real = shutil.which(name)
        assert real is not None, f"this suite needs `{name}` on the host to drive the step"
        (bin_dir / name).symlink_to(real)
    # ⛔ `sort` IS STUBBED, NOT SYMLINKED. The step resolves the fallback with `sort -V`,
    # and `-V` is a GNU coreutils extension — symlinking the host's `sort` would make this
    # arm's verdict depend on the host being GNU (measured: a non-GNU `sort` reds it, and
    # it reds with a message that blames the STEP, because the pipeline's failure is
    # swallowed by the step's own `|| true`). The version SELECTION is not what this arm
    # claims to pin; one candidate line makes `sort -V | tail -1` a pass-through, so a
    # pass-through stub is the honest stand-in (#13314 review, P2). It must IGNORE argv:
    # the step passes `-V`, and `exec cat` inherits it and dies on it.
    sort_stub = bin_dir / "sort"
    sort_stub.write_text(
        "#!/bin/sh\n"
        "# pass-through: ignore argv (the step passes -V) and echo stdin unchanged\n"
        'while IFS= read -r line; do printf \'%s\\n\' "$line"; done\n'
    )
    sort_stub.chmod(0o755)
    if psql_body is not None:
        fake = bin_dir / "psql"
        fake.write_text(psql_body)
        fake.chmod(0o755)

    github_path = tmp_path / "github_path"
    github_path.write_text("")
    script = tmp_path / "step.sh"
    script.write_text(_psql_install_run())

    env = {
        **os.environ,
        # ⛔ `bin_dir` ALONE, with no `/usr/bin` suffix. That suffix was the whole defect:
        # the host's `psql` lives there and `command -v psql` searches PATH, so a
        # missing-client arm silently inherited a working client.
        "PATH": str(bin_dir),
        "GITHUB_PATH": str(github_path),
    }
    return (
        subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env),
        github_path,
    )


# `postgresql-client-common`'s pg_wrapper: present, executable, and UNABLE to run —
# exactly the state the apt evidence points at (`command -v` and `-x` both pass).
BROKEN_PSQL = (
    "#!/bin/sh\n"
    'echo "Error: You must install at least one postgresql-client-<version> package" >&2\n'
    "exit 1\n"
)
WORKING_PSQL = '#!/bin/sh\necho "psql (PostgreSQL) 16.1 (Ubuntu 16.1-1)"\n'


def test_a_resolvable_but_broken_psql_fails_the_install_step(tmp_path):
    """The step's claim is that the client RUNS; a resolvable-but-broken one must red it.

    Pre-fix the probe was `echo "psql: $psql_bin ($("$psql_bin" --version))"`: the
    substitution's status is discarded (the simple command's status is `echo`'s, so
    `set -e` never fires), and the step exited **0** printing `psql: … ()`. The
    harness then reported the same exit 2 one step later, misattributed to the
    DSN/migrations — the outcome the step exists to prevent.
    """
    proc, github_path = _run_install_step(tmp_path, BROKEN_PSQL)

    assert proc.returncode == 1, (
        "a client that resolves but cannot run must FAIL the step, not pass it:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "but cannot run" in proc.stdout
    assert "You must install at least one postgresql-client" in proc.stdout, (
        "the refusal must quote the client's own error — that is the remedy text"
    )
    # The pre-fix signature: an empty `()` where the version should be.
    assert "()" not in proc.stdout
    # And it must NOT have repaired PATH for the later steps on a failed probe.
    assert github_path.read_text() == ""


def test_a_missing_client_fails_with_the_word_psql_in_the_remedy(tmp_path):
    """The MISSING-client arm: the refusal must still print the word `psql`.

    This is the other half of the review's finding 1, and it is the arm a static
    backtick sweep cannot reach: the sweep proves no line DOUBLE-quotes a backtick,
    while this proves what the operator actually reads on the only path that reaches
    the line. Pre-fix the message was `echo "::error::… no `psql` binary resolves …"`,
    so bash ran `psql` (absent here), printed `command not found` into the middle of
    the diagnostic, and the remedy read `no  binary resolves` — losing the one word a
    human repairing the runner needs. Measured on that spelling: stdout
    `::error::postgresql-client is installed but no  binary resolves …` plus two
    `bash: line N: psql: command not found` lines.
    """
    proc, github_path = _run_install_step(tmp_path, None)
    combined = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        f"no resolvable `psql` must red the step; stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert "no `psql` binary resolves" in proc.stdout, (
        "the `::error::` message must keep its backticks LITERAL — double-quoted they "
        f"command-substitute the word away; stdout={proc.stdout}"
    )
    assert "command not found" not in combined, (
        "bash's own error leaked into the diagnostic, which is the signature of a "
        f"command-substituted `::error::` message; stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert github_path.read_text() == "", (
        "nothing resolved, so nothing may be appended to $GITHUB_PATH"
    )


def test_a_working_client_still_passes_and_repairs_path(tmp_path):
    """The mirror arm: the fix must not over-block, and the repair must still happen.

    `$GITHUB_PATH` is what makes the versioned client visible to the two harness
    steps, so it is as load-bearing as the refusal — deleting it was invisible too.
    """
    proc, github_path = _run_install_step(tmp_path, WORKING_PSQL)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "psql (PostgreSQL) 16.1" in proc.stdout
    assert github_path.read_text().strip().endswith("bin"), (
        "the client's directory must be appended to $GITHUB_PATH for the harness steps"
    )


def test_a_versioned_client_outside_path_is_resolved_by_the_fallback(tmp_path):
    """The `/usr/lib/postgresql/*/bin` fallback arm — the reason `ls` is stubbed at all.

    Its sibling tests shadow the fallback (so no host client can satisfy them), which
    would leave this branch unpinned: deleting the `ls -d … | sort -V | tail -1`
    resolution would red nothing. Here the fallback is the ONLY client that exists, so
    the step must find it, probe it, and put its directory on `$GITHUB_PATH` — the
    trimmed-image case the step's comment describes.
    """
    proc, github_path = _run_install_step(tmp_path, None, versioned_psql=WORKING_PSQL)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "psql (PostgreSQL) 16.1" in proc.stdout, (
        "the fallback client must be probed, not merely found: "
        f"stdout={proc.stdout} stderr={proc.stderr}"
    )
    repaired = github_path.read_text().strip()
    assert repaired.endswith("vendor/bin"), (
        "the RESOLVED client's directory — not /usr/bin — must reach $GITHUB_PATH: "
        f"got {repaired!r}"
    )


def test_the_version_probe_is_a_statement_a_failing_step_can_observe():
    """Structural pin, independent of the exact spelling the behavioural tests exercise.

    A probe nested in `$( … )` inside `echo` cannot fail the step no matter what it
    prints, which is why this asserts POSITION: the `--version` invocation must sit
    on an `if` line (a condition `set -e` observes), and that arm must `exit 1`.
    """
    run = _psql_install_run()
    probe_lines = [ln.strip() for ln in run.splitlines()
                   if "--version" in ln and not ln.strip().startswith("#")]
    assert probe_lines, "the step must actually invoke `--version` — the probe is the point"
    for line in probe_lines:
        assert line.startswith("if "), (
            f"the probe must be a statement position (`if …`), not a discarded "
            f"command substitution: {line!r}"
        )
    first_probe = run.index(probe_lines[0])
    assert "exit 1" in run[first_probe:], "the failed-probe arm must exit non-zero"


def test_no_error_line_command_substitutes_a_backticked_word():
    """`echo "::error::… `psql` …"` executes `psql` and drops the word from the message.

    Repo-wide sweep over every workflow, not just this step: the hazard is the
    shape (a backtick inside a double-quoted `echo`), and the only path that
    reaches the line is the one where the word does not resolve — so bash's own
    "command not found" lands mid-diagnostic and the remedy text reads
    "no  binary resolves" (#13314 review P2).
    """
    offenders = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        doc = yaml.safe_load(wf.read_text())
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                run = step.get("run") or ""
                for lineno, line in enumerate(run.splitlines(), 1):
                    if "echo \"" not in line or "::error::" not in line:
                        continue
                    # A BACKSLASH-ESCAPED backtick is literal prose (`ci-timing.yml` uses
                    # that spelling throughout), so strike those before looking for a live
                    # one — otherwise this guard reds on correct code.
                    if "`" in line.replace("\\`", ""):
                        offenders.append(f"{wf.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "a double-quoted echo with a backtick COMMAND-SUBSTITUTES it; single-quote the "
        "message instead:\n" + "\n".join(offenders)
    )


def _table_shape_offenders(text: str) -> list[str]:
    """A table row is ONE physical line — a hard wrap inside one reds the `docs` gate.

    ⛔ THIS IS NOT HYPOTHETICAL (`#14038`). A lane re-cut one prose cell of §1's
    `--all-surface` row — the longest row in the document — and the editor wrapped
    it across three physical lines. `docs` is a REQUIRED context, and the pinned
    linter reported SIX findings on that single row, all of them the same defect:

    ```
    CYPHER-INVENTORY.md:69:5170 error MD055 leading_only        (Missing trailing pipe)
    CYPHER-INVENTORY.md:70:1    error MD055 no_leading_or_trailing
    CYPHER-INVENTORY.md:70:102  error MD056 Expected: 4; Actual: 1
    CYPHER-INVENTORY.md:71:1    error MD055 trailing_only
    CYPHER-INVENTORY.md:71:2377 error MD056 Expected: 4; Actual: 1
    ```

    ⚠️ `tools/cypher_inventory.py --check` stayed GREEN throughout, and it had to:
    the pins are scalars and a line break is not a statement. The appendix arm
    above does not see it either — it compares ROWS, and every row was intact. So
    the gate that READ the break was the linter, in CI, on the required check —
    after the push. This arm is the same two rules the linter applies
    (`MD055` `leading_and_trailing` and `MD056` column count, both enabled by
    `.markdownlint-cli2.jsonc`'s `default: true`), narrowed to the rows it can
    decide without the linter: a line that STARTS with `|` must also END with one
    (a line that starts with `|` is unambiguously a row), and each row of a table
    must carry the column count its own separator row declares.

    Measured non-vacuous: on the pre-fix blob (`a642d54b8678`) this fails at line
    69 with `no-trailing` AND `colcount` (4 pipes where the separator declares 5).

    ⛔ WHY ONE CHECKER AND TWO ARMS (#13098 review). After the split, the PROSE
    tables live in the TRACKED source (what `docs` lints) and the appendix lives in
    a gitignored render that `docs` never sees. One arm reading `_document_text()`
    therefore paid a FULL TREE WALK (`_document_text()` calls `census()`) inside the
    REQUIRED `python-ci-gate` to grade rows no required check can red, and it added
    ~22 s to every PR — the cost this file's own `@requires_doc_pin` block exists to
    keep out of the merge gate. So: the source half is UNGATED (cheap, and it is the
    half `docs` reads) and the rendered half is `@requires_doc_pin`.
    """
    lines = text.split("\n")

    def pipes(line: str) -> int:
        # `\|` renders a literal pipe and does not delimit a cell — the doc uses
        # that spelling inside an inline `git ls-tree … | grep` command.
        return re.sub(r"\\\|", "", line).count("|")

    offenders: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        # A table is a maximal run of rows; the line after it is prose again.
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            if not lines[j].rstrip().endswith("|"):
                offenders.append(
                    f":{j + 1}: row starts with '|' but does not end with one "
                    f"(MD055 leading_and_trailing) — {lines[j].strip()[-70:]!r}"
                )
            j += 1
        # The separator row (`|---|---|`) declares how many cells every row has.
        sep = i + 1
        if sep < j and set(lines[sep].strip()) <= set("|-: "):
            declared = pipes(lines[sep])
            for k in range(i, j):
                if pipes(lines[k]) != declared:
                    offenders.append(
                        f":{k + 1}: {pipes(lines[k]) - 1} cells, separator declares "
                        f"{declared - 1} (MD056) — {lines[k].strip()[:70]!r}"
                    )
        i = j

    return offenders


def test_the_source_tables_are_not_broken_across_physical_lines():
    """The PROSE tables — the half a REQUIRED check actually reads.

    Ungated on purpose: this is the text `docs` lints (`CYPHER-INVENTORY.src.md` is
    the tracked file), it costs one `read_text`, and a wrapped cell here is exactly
    the `#14038` break that redded a required context.
    """
    offenders = _table_shape_offenders(_source_text())
    assert not offenders, (
        "a markdown table row in the tracked SOURCE is broken across physical "
        "lines, which reds the REQUIRED `docs` check as MD055/MD056. A row is one "
        "line — re-join it rather than re-wrapping it:\n" + "\n".join(offenders)
    )


@requires_doc_pin
def test_the_rendered_tables_are_not_broken_across_physical_lines():
    """The whole RENDERED document — prose plus Appendix A's generated rows.

    ⛔ PINNED, not ungated: `_document_text()` runs a full census, and the appendix
    it grades lives in a gitignored file that no required check ever sees — so it
    belongs where the census already runs (`cypher-diff`, which sets
    `TORTOISE_CYPHER_DOC_PIN=1`), not in the merge gate. An offender inside Appendix
    A is a `markdown_table`/`_digest` bug rather than a row to re-join, because
    every generated row is one line by construction.
    """
    offenders = _table_shape_offenders(_document_text())
    assert not offenders, (
        "a markdown table row in the RENDERED document is broken across physical "
        "lines (MD055/MD056). ⛔ Inside Appendix A this is a `markdown_table` bug, "
        "never a row to re-join — fix the generator:\n" + "\n".join(offenders)
    )


def test_the_rendered_document_is_not_tracked():
    """The split's OTHER half: `CYPHER-INVENTORY.md` must stay OUT of the index.

    ⛔ WITHOUT THIS THE CONFLICT RETURNS BY THE RESOLUTION PATH, NOT THE EDIT PATH.
    Every open PR that still holds a copy meets this change as a `CONFLICT
    (modify/delete)`, and the standard "keep our version" resolution RE-STAGES the
    file — `.gitignore` governs UNTRACKED paths, so it does not refuse a path that
    already has an index stage. Nothing else would notice: `--check` reads the
    source, the appendix arms render into `tmp_path`, and `docs` lints a re-added
    copy and finds nothing wrong with it. The path is derived from `census.DOC_OUT`
    so the assertion cannot drift from the constant `--render` writes.
    """
    rel = census.DOC_OUT.relative_to(ROOT).as_posix()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert tracked.returncode != 0, (
        f"{rel} is TRACKED again — it is `--render`'s output, not a source (#13098).\n"
        f"    git rm --cached {rel}\n"
        "A modify/delete conflict resolved by keeping the incoming copy re-stages "
        "it, and no other arm in this suite reads the tracked path."
    )
    ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT)
    assert ignored.returncode == 0, (
        f"{rel} is not gitignored — `--render`'s output must never be stageable; "
        "re-add the path to `.gitignore`'s #13098 block"
    )
