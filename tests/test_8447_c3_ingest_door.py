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

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ingest import IngestOutcome, main

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
            assert outcome.returncode == 1
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
            IngestOutcome.REFUSED_NOT_A_DOCUMENT: 1,
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

    def test_cli_turns_the_outcome_into_the_process_status(self, monkeypatch):
        """The dereference itself — the ONE behaviour round 1's P0 was about.

        Without this, `def cli(): main()` passes the whole file: an outcome is
        returned, `pyproject` names `cli`, and `test_ingest_main_guard` only
        covers a DONE run — so every refusal would silently exit 0 again. (The
        defect this PR fixes was a wrong exit status no in-process test could
        see; the replacement guard must not repeat that blind spot for the
        refusal direction.)
        """
        import tortoise.ingest as ingest_mod
        for outcome, code in ((IngestOutcome.REFUSED_NOT_FOUND, 1),
                              (IngestOutcome.PARTIAL, 1),
                              (IngestOutcome.DONE, 0),
                              (IngestOutcome.SKIPPED_ALREADY_PROCESSED, 0)):
            monkeypatch.setattr(ingest_mod, "main",
                                lambda argv=None, _o=outcome: _o)
            with pytest.raises(SystemExit) as ei:
                ingest_mod.cli()
            assert ei.value.code == code, (outcome, ei.value.code)

    def test_the_installed_entry_point_exits_non_zero_on_a_refusal(self):
        """The PRE-EXISTING argument refusals still reach the shell non-zero.

        These three raise inside `main` (before any outcome exists), so they
        prove the entry point is wired, not the outcome mapping — that is
        `test_cli_turns_the_outcome_into_the_process_status` above.
        """
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
                "--out", str(tmp_path / "second.html"),
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        first, _ = _run([str(doc), *base])
        assert first is IngestOutcome.DONE, first

        # A SECOND, untouched --out path: the first run wrote `second.html`, so
        # existence after the skip means THE SKIP wrote it.
        # Same argv as the first run (that is what makes the idempotency
        # key match). The render is rewritten on every non-skip run, so an
        # unchanged mtime after the skip is proof the SKIP wrote nothing.
        render = tmp_path / "second.html"
        before = render.stat().st_mtime_ns
        second, out = _run([str(doc), *base])
        assert second is IngestOutcome.SKIPPED_ALREADY_PROCESSED, (
            f"the idempotency gate skipped but returned {second!r}")
        assert second.returncode == 0, "an idempotent re-run must stay green"
        assert "skip:" in out, out
        # The mutant: reinstate the fall-through and this reds. `points :` is
        # the completion summary — a run that wrote nothing must not print it.
        assert "points :" not in out, (
            "a SKIPPED ingest printed the completion summary, so its output is "
            "byte-indistinguishable from a completed one:\n" + out)
        # A skip writes nothing, the render included (documented in the §2
        # census row): a caller re-running to refresh --out gets the OLD file
        # and exit 0, so the `skip:` line is the only signal — pin both halves.
        assert render.stat().st_mtime_ns == before, (
            "a SKIPPED ingest rewrote the --out render, contradicting the row's "
            "'a skip writes nothing either'")

    def test_a_capture_metadata_run_on_a_non_document_is_refused(self, tmp_path):
        """Round 3's P1: --capture-metadata fell through to a success summary.

        Its ask is "emit the document Source + sessionCaptured Event", which is
        unperformable for content with no `##` sections. Measured before: exit 0,
        `points : 0 statements`, `--out` written, and ZERO `:Source` rows.
        """
        doc = tmp_path / "plain.txt"
        doc.write_text("just prose, no ## headers at all\n", encoding="utf-8")
        outcome, out = _run([
            str(doc), "--capture-metadata", "--db", _DB_URI,
            "--log", str(tmp_path / "e.jsonl"), "--out", str(tmp_path / "g.html"),
            "--point-model", "mock:cheap", "--relation-model", "mock:reason",
        ])
        assert outcome is IngestOutcome.REFUSED_NOT_A_DOCUMENT, (
            f"an unperformable --capture-metadata returned {outcome!r}; the "
            "caller reads that as a capture that happened")
        assert outcome.returncode == 1

        assert "refused" in out, out

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
        first, _ = _run([str(doc), *base])
        assert first is IngestOutcome.DONE, first
        after_first = _resolution_points()
        assert after_first == zero + 1, (
            "POSITIVE CONTROL FAILED: the first --resolution ingest minted "
            f"{after_first - zero} resolution Point(s), not 1 — this query "
            "cannot see the point, so the assertion below would be vacuous")

        outcome, _ = _run([str(doc), *base])
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
    def test_the_sweep_returns_partial_when_it_leaves_a_document_behind(
            self, tmp_path, monkeypatch):
        """Round 3's P2: `return IngestOutcome.PARTIAL` survived a `pass` mutant.

        Nothing asserted that the sweep RETURNS partial — only properties of the
        enum — so deleting the arm shipped green and the census's MARKS claim
        silently reverted to SILENT. This drives the real arm: park a document,
        strip its `##` sections, sweep.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        doc = corpus / "becomes-plain.md"
        doc.write_text(
            "---\ntitle: Sweep Note\n---\n\n## Section one\n\nSomething true.\n",
            encoding="utf-8")
        monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(corpus))
        base = ["--db", _DB_URI, "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "g.html"),
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        assert _run([str(doc), "--capture-metadata", *base])[0] is IngestOutcome.DONE
        # Now the content has no `##` sections: the sweep cannot extract it, so
        # the marker stays and the run is NOT a completed ask.
        doc.write_text("just prose now\n", encoding="utf-8")
        outcome, out = _run(["--upgrade-all", *base])
        assert "not a document" in out, out
        assert outcome is IngestOutcome.PARTIAL, (
            f"a sweep that left a document pending returned {outcome!r} "
            f"(exit {outcome.returncode}); a caller reads that as done:\n{out}")
        assert outcome.returncode == 1

        # The sweep is graph-GLOBAL: a Source left pending here would make every
        # LATER sweep in this file (and on this lane) read `unresolved` and
        # return PARTIAL. Drop what this test parked.
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(_DB_URI)
        try:
            proj.g.query("MATCH (s:Source {url: $id}) DETACH DELETE s",
                         params={"id": doc.name})
        finally:
            proj.close()


class TestTheSweepDoesNotRedOnItsOwnIdempotency:
    """The sweep's DECISION, driven through `_do_upgrade_all` with a stubbed population.

    Deliberately not a live sweep: `_discover_pending_documents` reads every
    pending `:Source` in the graph, so "the only bucket is `already claimed`" is
    really an assertion about whatever else this lane's graph holds — a
    false-red generator. That is also why the round-3 reviewer measured
    `return IngestOutcome.PARTIAL` surviving a `pass` mutant: no test drove the
    arm at all.
    """

    @staticmethod
    def _sweep(tmp_path, monkeypatch, rows, *, begin_skip):
        import typing

        import tortoise.ingest as ingest_mod

        class _Rowset:
            result_set: typing.ClassVar[list] = []

        class _G:
            def query(self, *_a, **_k):
                return _Rowset()

        class _Proj:
            g = _G()

        class _Result:
            skip = begin_skip
            reason = "already processed (same key + extractor_version)"
            run_id = "r1"

        class _API:
            agent_id = "mock:cheap@v"

            def begin_ingest(self, *_a, **_k):
                return _Result() if begin_skip else None

            def add_document(self, *_a, **_k):
                raise AssertionError("a skip must not write a document")

        monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
        monkeypatch.setattr(ingest_mod, "_discover_pending_documents",
                            lambda proj, **k: rows)
        args = argparse.Namespace(force=False, max_utterances=0, domain=None,
                                  include_unstamped=False,
                                  point_model="mock:cheap",
                                  relation_model="mock:reason")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            outcome = ingest_mod._do_upgrade_all(_Proj(), _API(), args)
        return outcome, buf.getvalue()

    def test_only_the_already_claimed_bucket_is_green(self, tmp_path, monkeypatch):
        doc = tmp_path / "sweep-note.md"
        doc.write_text("## Section one\n\nSomething true.\n", encoding="utf-8")
        outcome, out = self._sweep(tmp_path, monkeypatch,
                                   [(doc.name, str(doc), True, None)],
                                   begin_skip=True)
        assert outcome is IngestOutcome.DONE, (
            "a sweep whose ONLY skip is 'already claimed at this extractor "
            f"version' returned {outcome!r} (exit {outcome.returncode}) — a "
            "benign idempotent re-sweep must stay green")
        # The door's own output must not misstate the verdict it just produced.
        assert "exits NON-ZERO" not in out, (
            "a sweep that exited 0 printed the 'this sweep exits NON-ZERO' "
            "sentence:\n" + out)

    def test_a_not_document_candidate_is_partial(self, tmp_path, monkeypatch):
        """The `not_document` half of the predicate, in ISOLATION.

        ⚠️ The live sweep test cannot pin this: `--upgrade-all` is graph-GLOBAL,
        so a foreign pending Source decides `PARTIAL` for it and the
        `unresolved or not_document` -> `unresolved` mutant survives. This
        population has `unresolved == 0`, so `PARTIAL` can only come from
        `not_document`.
        """
        doc = tmp_path / "plain.md"
        doc.write_text("just prose, no ## headers\n", encoding="utf-8")
        outcome, out = self._sweep(
            tmp_path, monkeypatch,
            [(doc.name, str(doc), True, None)], begin_skip=False)
        assert "exits NON-ZERO" in out, out
        assert outcome is IngestOutcome.PARTIAL, (
            "a sweep whose only candidate has no ## sections returned "
            f"{outcome!r}; `not_document` is outstanding work")
        assert outcome.returncode == 1

    def test_an_unreadable_candidate_is_partial(self, tmp_path, monkeypatch):
        """The `unresolved` bucket — the arm a `pass` mutant used to survive."""
        outcome, _out = self._sweep(
            tmp_path, monkeypatch,
            [("gone.md", str(tmp_path / "gone.md"), True, None)], begin_skip=False)
        assert outcome is IngestOutcome.PARTIAL, (
            "a sweep that could not read its only candidate returned "
            f"{outcome!r} (exit {outcome.returncode}); a caller reads that as done")
        assert outcome.returncode == 1
