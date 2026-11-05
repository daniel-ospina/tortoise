"""#8447 C3 — the INGEST door's refusal must be visible, never success-shaped.

#8447's refusal clause (``docs/architecture/BOUNDED-OPERATION-CONTRACT.md`` §1a):

    A refused operation must either refuse visibly (an error, or a reply field
    that says so) or succeed. It must never return a value indistinguishable
    from success — ``None``, ``[]``, or a ``200`` with an empty body — while
    recording the failure somewhere the caller does not read.

The four doors that clause names are *record write*, *session read*, *capture*
and *ingest*. The first two are pinned elsewhere; the **ingest** door is
``tortoise ingest`` (``tortoise/ingest.py``), whose only machine-readable
output is the process exit status. Before this file, ``main`` returned ``None``
on every arm — so ``tortoise-ingest --upgrade <not-a-document>`` printed a
refusal and exited **0**, and an idempotency skip fell through to the
completion summary as if the run had ingested something.

Three properties are pinned here, one class each:

* ``TestARefusalIsNotSuccess`` — the mutant: revert any refusal to a
  success-shaped return and the arm reds.
* ``TestASkipIsNotARefusal`` — the opposite failure: collapsing an already-
  satisfied ask into an error would break every idempotent re-run, so the skip
  arms assert ``0`` explicitly. A test that only pinned "refusals are non-zero"
  would pass on a fix that made every run red.
* ``TestTheSkipArmWritesNothing`` — a skip must not emit a completion summary
  or a ``--resolution`` Point for work it never did.
"""
from __future__ import annotations

import io
import os
import sys
import contextlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ingest import IngestOutcome, main  # noqa: E402

# The DB is only needed by the arms that reach a projection. On a URI-less
# (carve-out) CI lane those arms skip; the enum/exit-contract arms below run
# everywhere, because they are the ones that pin the clause.
_DB_URI = os.environ.get("TORTOISE_DB_URI", "")


def _run(argv: list[str]) -> tuple[object, str]:
    """Call ``main`` and return (returned outcome, captured stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        outcome = main(argv)
    return outcome, buf.getvalue()


# --------------------------------------------------------------------------
# The exit contract itself — no DB, no model, runs on every lane.
# --------------------------------------------------------------------------

class TestTheExitContractIsOneMapping:
    def test_a_refusal_exits_non_zero(self):
        for outcome in (IngestOutcome.REFUSED_NOT_FOUND,):
            assert outcome.refused is True, outcome
            assert outcome.returncode == 1, (
                f"{outcome} is a REFUSAL but maps to exit {outcome.returncode}; "
                "a caller cannot tell it from a completed ingest")

    def test_an_already_satisfied_ask_exits_zero(self):
        """The opposite arm. A skip is the ask's own answer, not a failure.

        Without this, a "fix" that returned 1 for every non-DONE outcome would
        turn every idempotent re-run in a pipeline red and still pass a
        refusals-only test.
        """
        for outcome in (IngestOutcome.SKIPPED_ALREADY_PROCESSED,
                        IngestOutcome.SKIPPED_ALREADY_EXTRACTED,
                        IngestOutcome.SKIPPED_NOTHING_PENDING,
                        IngestOutcome.DONE):
            assert outcome.refused is False, outcome
            assert outcome.returncode == 0, (
                f"{outcome} is not a refusal but maps to exit "
                f"{outcome.returncode}; an idempotent re-run must stay green")

    def test_a_partial_run_is_not_reported_as_complete(self):
        assert IngestOutcome.PARTIAL.returncode == 1

    def test_every_outcome_has_a_decided_exit_status(self):
        """An explicit table, not `isinstance` — a NEW member must red this.

        The previous version asserted the two properties were total, which they
        are by construction (`return 1 if … else 0`), so it stayed green when
        an arm was added without anyone deciding what the caller should see.
        """
        expected = {
            IngestOutcome.DONE: 0,
            IngestOutcome.SKIPPED_ALREADY_PROCESSED: 0,
            IngestOutcome.SKIPPED_ALREADY_EXTRACTED: 0,
            IngestOutcome.SKIPPED_NOTHING_PENDING: 0,
            IngestOutcome.REFUSED_NOT_FOUND: 1,
            IngestOutcome.PARTIAL: 1,
        }
        assert set(expected) == set(IngestOutcome), (
            "the enum gained or lost a member and nobody decided its exit "
            f"status: {sorted(set(IngestOutcome) ^ set(expected))}")
        for outcome, code in expected.items():
            assert outcome.returncode == code, (outcome, code)

    def test_the_console_script_target_never_passes_the_enum_to_sys_exit(self):
        """`sys.exit(<StrEnum>)` prints the member and exits **1** for ALL of them.

        The generated console wrapper is `sys.exit(<entry>())`, and
        `pyproject.toml` pointed `tortoise-ingest` at `main` — so a DONE ingest
        exited 1 with `done` on stderr, and DONE became indistinguishable from
        REFUSED at the only machine-readable channel. `cli()` is the fix; this
        pins the wiring, because the failure is invisible to any test that
        calls `main` directly.
        """
        import tomllib
        root = Path(__file__).resolve().parent.parent
        scripts = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["scripts"]
        assert scripts["tortoise-ingest"] == "tortoise.ingest:cli", (
            "the console script must point at `cli`, which dereferences "
            "`.returncode`; pointing it at `main` hands sys.exit a StrEnum")

    def test_the_installed_entry_point_exits_with_the_outcomes_code(self):
        """Proven end to end on the REAL entry point, not on `main`."""
        import subprocess
        root = Path(__file__).resolve().parent.parent
        # These are the module's PRE-EXISTING argument refusals (they already
        # raised SystemExit before this change); the arms pinned here are the
        # ones this change owns — a refusal must be non-zero, never 0.
        for argv, want in (
            (["--help"], 0),
            (["--include-unstamped"], "nonzero"),   # needs --upgrade-all
            (["--upgrade"], "nonzero"),             # needs a transcript
        ):
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from tortoise.ingest import cli; cli()", *argv],
                cwd=root, capture_output=True, text=True, timeout=120)
            ok = (proc.returncode == want) if want != "nonzero" \
                else (proc.returncode != 0)
            assert ok, (
                f"{argv} -> exit {proc.returncode}, wanted {want}\n"
                f"stdout={proc.stdout[-500:]}\nstderr={proc.stderr[-500:]}")


# --------------------------------------------------------------------------
# The refusal, end to end, against a real graph.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _DB_URI, reason="needs TORTOISE_DB_URI (docker lane)")
class TestARefusalIsNotSuccess:
    def test_upgrading_a_source_that_is_not_a_document_is_refused(
            self, tmp_path):
        """The reproduced defect: 'not a Document' used to print and exit 0."""
        doc = tmp_path / "plain.txt"
        doc.write_text("not a document — no ## sections, no Source row\n",
                       encoding="utf-8")
        outcome, out = _run([
            "--upgrade", str(doc), "--db", _DB_URI,
            "--log", str(tmp_path / "e.jsonl"), "--out", str(tmp_path / "g.html"),
            "--point-model", "mock:cheap", "--relation-model", "mock:reason",
        ])
        assert "not a Document" in out, out
        assert outcome is IngestOutcome.REFUSED_NOT_FOUND, (
            f"the door refused but returned {outcome!r}; the caller reads that "
            "as the ask having been performed")
        assert outcome.returncode == 1


# --------------------------------------------------------------------------
# The skip arm must not produce a completion artifact.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _DB_URI, reason="needs TORTOISE_DB_URI (docker lane)")
class TestTheSkipArmWritesNothing:
    def test_a_second_ingest_skips_and_does_not_print_a_completion_summary(
            self, tmp_path):
        doc = tmp_path / "note.md"
        doc.write_text(
            "---\ntitle: Repro Note\n---\n\n## Section one\n\nSomething true.\n",
            encoding="utf-8")
        base = ["--db", _DB_URI, "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "g.html"),
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        first, _ = _run([str(doc)] + base)
        assert first is IngestOutcome.DONE, first

        second, out = _run([str(doc)] + base)
        assert second is IngestOutcome.SKIPPED_ALREADY_PROCESSED, (
            f"the idempotency gate skipped but returned {second!r}")
        assert second.returncode == 0, "an idempotent re-run must stay green"
        assert "skip:" in out, out
        # The mutant: reinstate the fall-through and this reds. `points :` is
        # the completion summary — a run that wrote nothing must not print it.
        assert "points :" not in out, (
            "a SKIPPED ingest printed the completion summary, so its output is "
            "byte-indistinguishable from a completed one:\n" + out)

    def test_a_skipped_ingest_does_not_write_a_resolution_point(self, tmp_path):
        """`--resolution` after a skip wrote a Point for work that never ran.

        ⚠️ The point's content is ``f"Resolved: {source_id}"`` where
        ``source_id`` is ``args.transcript.name`` — the **basename**. An earlier
        version of this test matched on the absolute tmp path and therefore
        counted 0 before AND after, asserting nothing. The positive control
        below (the first run raises the count by exactly 1) is what keeps that
        vacuity from coming back silently.
        """
        doc = tmp_path / "note.md"
        doc.write_text(
            "---\ntitle: Repro Note 2\n---\n\n## Section one\n\nSomething true.\n",
            encoding="utf-8")
        needle = f"Resolved: {doc.name}"
        base = ["--db", _DB_URI, "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "g.html"), "--resolution",
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        def _resolution_points() -> int:
            from tortoise.projection import FalkorProjection
            proj = FalkorProjection.from_uri(_DB_URI)
            try:
                return proj.g.query(
                    "MATCH (p:Point) WHERE p.pointKind = 'resolution-event' "
                    "AND p.content CONTAINS $s RETURN count(p)",
                    params={"s": needle}).result_set[0][0]
            finally:
                proj.close()

        zero = _resolution_points()
        first, _ = _run([str(doc)] + base)
        assert first is IngestOutcome.DONE, first
        after_first = _resolution_points()
        assert after_first == zero + 1, (
            "POSITIVE CONTROL FAILED: the first --resolution ingest minted "
            f"{after_first - zero} resolution Point(s), not 1 — this query "
            "cannot see the point, so the assertion below would be vacuous")

        outcome, _ = _run([str(doc)] + base)
        assert outcome is IngestOutcome.SKIPPED_ALREADY_PROCESSED, outcome
        assert _resolution_points() == after_first, (
            "a SKIPPED ingest minted a resolution Point for work it never "
            "performed")


# --------------------------------------------------------------------------
# The sweep: outstanding work is not a completed ask.
# --------------------------------------------------------------------------

class TestTheSweepReportsOutstandingWork:
    def test_leftover_pending_work_is_partial_not_done(self):
        assert IngestOutcome.PARTIAL.returncode == 1
        assert IngestOutcome.PARTIAL is not IngestOutcome.DONE

    def test_an_empty_sweep_is_not_a_failure(self):
        """An empty sweep is the ask's own answer, not a refusal."""
        assert IngestOutcome.SKIPPED_NOTHING_PENDING.returncode == 0


@pytest.mark.skipif(not _DB_URI, reason="needs TORTOISE_DB_URI (docker lane)")
class TestTheSweepDoesNotRedOnItsOwnIdempotency:
    def test_a_second_sweep_with_only_the_already_claimed_bucket_is_green(
            self, tmp_path, monkeypatch):
        """The benign bucket must not make a steady-state corpus red forever.

        `already_claimed` is the sweep's twin of `SKIPPED_ALREADY_PROCESSED`:
        the content is already in the graph at this extractor version. Counting
        it as outstanding would exit 1 on every re-sweep of an unchanged
        corpus — noise that trains operators to stop reading the exit status,
        which destroys the marker. `not_document` and `unresolved` keep their
        pending marker and DO count.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        doc = corpus / "sweep-note.md"
        doc.write_text(
            "---\ntitle: Sweep Note\n---\n\n## Section one\n\nSomething true.\n",
            encoding="utf-8")
        monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(corpus))

        base = ["--db", _DB_URI, "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "g.html"),
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        # Park the document with its pending marker without extracting it.
        meta = [str(doc), "--capture-metadata"] + base
        assert _run(meta)[0] is IngestOutcome.DONE

        first, out1 = _run(["--upgrade-all"] + base)
        assert "1 upgraded" in out1, out1
        assert first is IngestOutcome.DONE, (
            f"a sweep that upgraded its only candidate returned {first!r}")

        # Re-park the SAME content, then sweep again: the only bucket is
        # `already claimed`, which must stay GREEN.
        assert _run(meta)[0] is IngestOutcome.DONE
        third, out3 = _run(["--upgrade-all"] + base)
        assert "1 already claimed" in out3, out3
        assert third is IngestOutcome.DONE, (
            "a sweep whose ONLY skip is 'already claimed at this extractor "
            f"version' returned {third!r} (exit {third.returncode}) — a benign "
            "idempotent re-sweep must stay green:\n" + out3[-600:])
