"""#8447 C3 — the INGEST door's refusal must be visible, never success-shaped.

#8447's refusal clause (``docs/architecture/BOUNDED-OPERATION-CONTRACT.md`` §1a):

    A refused operation must either refuse visibly (an error, or a reply field
    that says so) or succeed. It must never return a value indistinguishable
    from success — ``None``, ``[]``, or a ``200`` with an empty body — while
    recording the failure somewhere the caller does not read.

The four doors C3 covers are *record write*, *session read*, *capture*
and *ingest* (the enumeration lives in `BOUNDED-OPERATION-CONTRACT.md` §6, which
this PR adds; §1a itself names no doors). The first two are pinned elsewhere; the **ingest** door is
``tortoise-ingest`` / ``python -m tortoise.ingest`` (``tortoise/ingest.py``),
whose only machine-readable
output is the process exit status. Before this file, ``main`` returned ``None``
on every arm — so ``tortoise-ingest --upgrade <not-a-document>`` printed a
refusal and exited **0**, and an idempotency skip fell through to the
completion summary as if the run had ingested something.

Six properties are pinned here, and the classes below are the ONLY ones — a
name in this paragraph that does not resolve is worse than no paragraph, because
the next lane auditing #8447 greps it in `BOUNDED-OPERATION-CONTRACT.md` §2,
finds nothing, and has to decide whether the pin was deleted:

* ``TestTheExitContractIsOneMapping`` — the mapping itself: a refusal is `1`,
  an already-satisfied ask is `0`, `PARTIAL` is `1`, every enum member has a
  DECIDED status, and `cli()` dereferences `.returncode` so the installed
  entry point cannot hand `sys.exit` a `StrEnum`.
* ``TestARefusalIsNotSuccess`` — the mutant: revert any refusal to a
  success-shaped return and the arm reds.
* The opposite failure — collapsing an already-satisfied ask into an error
  would break every idempotent re-run — is pinned by
  ``TestTheExitContractIsOneMapping::test_an_already_satisfied_ask_exits_zero``
  (the skip members are `0`) and by ``TestTheSkipArmWritesNothing`` (a skip
  emits no completion summary and writes no ``--resolution`` Point for work it
  never did). A test that only pinned "refusals are non-zero" would pass on a
  fix that made every run red.
* ``TestTheSweepReportsOutstandingWork`` and
  ``TestTheSweepDoesNotRedOnItsOwnIdempotency`` — the ``--upgrade-all`` sweep:
  leftover work is `PARTIAL`, an empty sweep and the `already claimed` bucket
  are `0`, and an unreadable or not-a-Document candidate is `PARTIAL`.
* ``TestAPartialExtractionIsNotDone`` — an extraction that failed sections is
  not a completed ingest, on all three arms (the sweep, ``--upgrade``, the
  full ingest).

One further class, ``TestThisFilesEvidencePointersCannotGoStale``, is a
file-level guard rather than a seventh door property: it pins that no docstring
here cites a ``file:line`` coordinate, because a coordinate re-stales on the
next rebase and an unfollowable evidence pointer invites the reader to delete
the assertion it justified.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The census pin's own ``file:line`` predicate — IMPORTED, not re-spelled. The
#: guard below asks the same question the §2 census pin asks ("does this text
#: point a reader at a coordinate?"), and two predicates for one question drift
#: in the direction that matters: a citation one catches and the other misses is
#: RED in the census and GREEN here. Safe to import: that module touches no DB at
#: import time (stdlib + ``tortoise.truncation``).
from tests.test_8447_truncation_marker import _LINE_CITATION
from tortoise.ingest import IngestOutcome, main

# The DB is only needed by the arms that reach a projection. This file is a
# DOCKER-lane member (the `core` surface in `config/ci-surfaces.yml`, NOT a
# `carve_out:` entry), and its live arms carry the same
# `skipif(not _DB_URI, ...)` shape as `tests/test_ingest.py::_live_db`. On a
# URI-less lane those arms skip VISIBLY; the enum/exit-contract arms below need
# no DB and run everywhere, because they are the ones that pin the clause.
_DB_URI = os.environ.get("TORTOISE_DB_URI", "")


def _graph_uri(test_graph: str) -> str:
    """The lane's backend, on a PER-TEST ``test_*`` graph path.

    The env URI's graph path is shared by every test file in the CI job
    (``tortoise_test_matrix``), and this file cannot use it for two measured
    reasons: ``--upgrade-all`` reads EVERY pending ``:Source`` in the graph, so
    the sweep's verdict would be a statement about whatever else the lane has
    parked (the round-3 review saw exactly that class when the `PARTIAL` arm
    read as unreachable); and one arm ends with
    ``MATCH (s:Source {url: $id}) DETACH DELETE s`` where ``$id`` is a SOURCE
    BASENAME (``ingest.py`` sets ``source_id = args.transcript.name``), so on the
    shared path it could delete a peer file's ``Source {url: "note.md"}`` —
    the cross-file clobber `tests/test_derived_names.py`'s census exists to stop.

    Same shape as `tests/test_ingest.py::_live_uri` (epic #1647 T7): swap only
    the graph path, keep the backend. `from_uri` journals every minted graph in
    TEST_MODE, so the session sweep drops these per-run graphs.
    """
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(_DB_URI)
    return urlunsplit((parts.scheme, parts.netloc, f"/{test_graph}", "", ""))


def _fresh_graph(stem: str) -> str:
    """A per-test ``test_8447_<stem>_<hex>`` URI — never the shared lane graph."""
    return _graph_uri(f"test_8447_{stem}_{os.urandom(4).hex()}")


def _run(argv: list[str]) -> tuple[object, str]:
    """Call ``main`` and return (returned outcome, captured stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        outcome = main(argv)
    return outcome, buf.getvalue()


@pytest.fixture(autouse=True)
def _no_embedder(force_sparse_tfidf):
    """No arm in this file loads the embedding model — and it is ONE seam.

    This file pins the DOOR's exit contract (which outcome for which input, and
    what a refusal must not leave behind). No arm pins the dense leg, so an
    unmocked full ingest here paid for a model load nothing asserted on: the
    DOCUMENT UPSERT constructs the real embedder (`add_document` -> projection
    apply -> `entities.encode_for_row` -> `EmbeddingModel.get()`), whose
    huggingface.co HEAD request the hermetic egress guard blocks and retries five
    times (#4387).

    It is the FIRST ingest in a PROCESS that pays: `EmbeddingModel`'s failed-load
    negative cache (`_FAIL_COOLDOWN_S`, 60 s) means a later arm reuses the
    failure, which is why the slow arm read as "one arm costs two minutes" and
    why a stub on that one arm did not help — measured, the 123 s simply moved to
    the next arm that needed the model. The same shape is recorded for a session
    cost in `config/ci-surfaces.yml`'s `test_9362_sweep_mirror_order.py` row.

    This fixture REQUESTS `tests/conftest.py::force_sparse_tfidf` — the repo's
    documented "no embedder" pin (#2573/#2772) — instead of re-spelling its
    lambda, for the reason this file's citation guard gives about predicates: two
    spellings of one seam drift, and the drift is invisible. `get() -> None` is
    also the codebase's own keyword-only degrade path, so this pins an existing
    behaviour rather than inventing one. A file that DID pin the dense leg must
    not take this fixture.
    """
    return None


# --------------------------------------------------------------------------
# The exit contract itself — no DB, no model, runs on every lane.
# --------------------------------------------------------------------------

class TestTheExitContractIsOneMapping:
    def test_a_refusal_exits_non_zero(self):
        for outcome in (IngestOutcome.REFUSED_NOT_FOUND,):
            assert outcome.refused is True, outcome
            # ⛔ ONE assertion, with the message. Round 10 found a bare
            # `assert outcome.returncode == 1` duplicated directly above this
            # one: it fired first, so the diagnostic message written for
            # exactly this failure was unreachable — an assertion that could
            # not fail. The mutant is still killed; the DIAGNOSIS was dead.
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

        ⛔ Each arm asserts the guard's OWN message, not only the SIGN of the
        status. `--db` is `required=True` on `tortoise/ingest.py::main`'s own
        parser, and `argparse.ArgumentParser.parse_args` runs BEFORE both mode
        guards (the `--include-unstamped` and `--upgrade` `SystemExit`s that
        follow it), so an arm that omits `--db` is refused by ARGPARSE with
        exit 2 and its `!= 0` assertion holds without either guard being
        reached — MEASURED before this was fixed:

            $ python -c 'from tortoise.ingest import cli; cli()' --include-unstamped
            error: the following arguments are required: --db   → exit 2

        Both arms were therefore byte-identically green on the PRE-FIX tree
        (`from tortoise.ingest import cli` raised ImportError, also non-zero)
        and stayed green under the mutant that deletes both guards — they
        pinned nothing. Passing `--db` makes the GUARD what fires, and the
        message assertion makes the arm red if the guard is deleted and some
        LATER `SystemExit` takes its place.
        """
        import subprocess
        root = Path(__file__).resolve().parent.parent
        # Never connected: with `--db` present the mode guard raises before any
        # projection is opened, so this string only has to be parseable.
        db = "docker://:f@localhost:6379/x"
        # These are the module's PRE-EXISTING argument refusals (they already
        # raised SystemExit before this change); the arms pinned here are the
        # ones this change owns — a refusal must be non-zero, never 0.
        for argv, want, message in (
            (["--help"], 0, "usage:"),
            # `--db` present → the GUARD fires, not the parser.
            (["--include-unstamped", "--db", db],
             "nonzero", "--include-unstamped requires --upgrade-all"),
            (["--upgrade", "--db", db],
             "nonzero", "--upgrade requires a transcript file to upgrade"),
        ):
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from tortoise.ingest import cli; cli()", *argv],
                cwd=root, capture_output=True, text=True, timeout=120)
            seen = proc.stdout + proc.stderr
            ok = (proc.returncode == want) if want != "nonzero" \
                else (proc.returncode != 0)
            assert ok, (
                f"{argv} -> exit {proc.returncode}, wanted {want}\n"
                f"stdout={proc.stdout[-500:]}\nstderr={proc.stderr[-500:]}")
            assert message in seen, (
                f"{argv} exited non-zero but NOT because of the guard this arm "
                f"names: {message!r} is absent. Something else refused first "
                "(argparse's required-argument error is the shape to suspect), "
                "so the arm does not falsify the guard:\n"
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
            "--upgrade", str(doc), "--db", _fresh_graph("refused"),
            "--log", str(tmp_path / "e.jsonl"), "--out", str(tmp_path / "g.html"),
            "--point-model", "mock:cheap", "--relation-model", "mock:reason",
        ])
        assert "not a Document" in out, out
        assert outcome is IngestOutcome.REFUSED_NOT_FOUND, (
            f"the door refused but returned {outcome!r}; the caller reads that "
            "as the ask having been performed")
        assert outcome.returncode == 1
        # The refusal must not leave a COMPLETION artifact behind either: a
        # caller that ignores `$?` reads the render, and an `--out` written by a
        # run that did nothing is the same success-shaped lie one layer down.
        # (Round-6 review flagged a claim that these two assertions were added
        # when the tree did not have them; round 8 measured the OTHER error —
        # they are present but NON-DISCRIMINATING on this arm: `--upgrade` has
        # been terminal on main since #133 (`main` returns before the completion
        # block), so both pass against the UNFIXED module. They are kept as
        # regression pins, NOT as mutant-killers. The arm where they DO
        # discriminate is `test_a_capture_metadata_run_on_a_non_document_is_refused`
        # below, which fell through to the summary until this PR.)
        assert not (tmp_path / "g.html").exists(), (
            "a REFUSED --upgrade wrote the --out render, so the refusal is "
            "success-shaped on disk as well as absent from the exit status")
        # The summary line is the OTHER half of the same tail (`render` + `points :`
        # are written together). Same caveat as the assertion above: pinned as a
        # regression, not as a kill of the un-fixed code.
        assert "points :" not in out, (
            "a REFUSED --upgrade printed the completion summary, so its stdout "
            "is byte-indistinguishable from a completed run's:\n" + out)



# --------------------------------------------------------------------------
# The skip arm must not produce a completion artifact.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _DB_URI, reason="needs TORTOISE_DB_URI (docker lane)")
class TestTheSkipArmWritesNothing:
    def test_a_second_ingest_skips_and_does_not_print_a_completion_summary(
            self, tmp_path, monkeypatch, force_sparse_tfidf):
        # ⛔ THE COST THIS ARM CARRIED WAS NOT S7 — it was the DOCUMENT UPSERT
        # (round 12 measured the stack: `add_document` → projection apply →
        # `entities.encode_for_row` → `EmbeddingModel.get()`), which constructs
        # the real embedding model on the `core` surface. `force_sparse_tfidf`
        # pins that seam to None, which is the documented way to keep an arm
        # from depending on the bge model cache (#2573/#2772). The S7 stub stays
        # as cheap insurance: this arm passes `semantic_extract` through, so a
        # future default flip would otherwise re-introduce a second real load.
        monkeypatch.setattr("tortoise.ingest._s7_document_entities",
                            lambda *_a, **_k: {})
        doc = tmp_path / "note.md"
        doc.write_text(
            "---\ntitle: Repro Note\n---\n\n## Section one\n\nSomething true.\n",
            encoding="utf-8")
        base = ["--db", _fresh_graph("skip"), "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "second.html"),
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        first, _ = _run([str(doc), *base])
        assert first is IngestOutcome.DONE, first

        # The ``--out`` path BOTH runs write: its existence attributes nothing to
        # the skip — only its mtime, asserted below, can.
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

    def test_an_already_extracted_document_answers_the_named_skip(
            self, tmp_path):
        """Round 8 P3: `SKIPPED_ALREADY_EXTRACTED`'s `return` was undriven.

        `tests/test_ingest.py::test_upgrade_on_already_extracted_is_noop` and
        `tests/test_5140_pending_extraction.py` assert the *printed* line
        (``doc already extracted, skipped``), which survives deleting the
        `return` — the run then falls through to step 2 and re-extracts, calling
        `begin_ingest` and superseding the Source's Points for work nobody asked
        for. The exit status would also silently become `DONE`/`PARTIAL`.
        Pinning the OUTCOME reds on that deletion.
        """
        doc = tmp_path / "already.md"
        doc.write_text(
            "---\ntitle: X\n---\n\n## User\nhello\n", encoding="utf-8")
        uri = _fresh_graph("already_extracted")
        # Seed the document as ALREADY extracted: D10's signal is
        # needs_extraction=false. `source_id` is the file BASENAME (ingest.py
        # sets `source_id = args.transcript.name`), so `url`/`id` carry the name.
        # The variable is named `uri` on purpose: it is the shape
        # `tests/test_derived_names.py` declares for this file, and a per-test
        # `test_8447_*` graph is what keeps the seed off the shared lane path.
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(uri)
        try:
            proj.g.query(
                "CREATE (s:Source {url:$id, id:$id, title:'X', "
                "documentKind:'document', needs_extraction:false, sourcePath:$sp})",
                params={"id": doc.name, "sp": str(doc)},
            )
        finally:
            proj.close()

        outcome, out = _run([
            str(doc), "--upgrade", "--db", uri,
            "--log", str(tmp_path / "e.jsonl"),
            "--out", str(tmp_path / "g.html"),
            "--point-model", "mock:cheap", "--relation-model", "mock:reason",
        ])
        assert "doc already extracted, skipped" in out, out
        assert outcome is IngestOutcome.SKIPPED_ALREADY_EXTRACTED, (
            f"an already-extracted --upgrade returned {outcome!r} while printing "
            "the skip line; the `return` that names this outcome is gone, so the "
            "run fell through and re-extracted a document nobody asked it to "
            "touch")
        assert outcome.returncode == 0, (
            "a skip is the ask's own answer, so it must stay green")

    def test_a_capture_metadata_run_on_a_non_document_is_refused(self, tmp_path):
        """Round 3's P1: --capture-metadata fell through to a success summary.

        Its ask is "emit the document Source + sessionCaptured Event", which is
        unperformable for content with no `##` sections. Measured before: exit 0,
        `points : 0 statements`, `--out` written, and ZERO `:Source` rows.
        """
        doc = tmp_path / "plain.txt"
        doc.write_text("just prose, no ## headers at all\n", encoding="utf-8")
        outcome, out = _run([
            str(doc), "--capture-metadata", "--db", _fresh_graph("capture"),
            "--log", str(tmp_path / "e.jsonl"), "--out", str(tmp_path / "g.html"),
            "--point-model", "mock:cheap", "--relation-model", "mock:reason",
        ])
        assert outcome is IngestOutcome.REFUSED_NOT_A_DOCUMENT, (
            f"an unperformable --capture-metadata returned {outcome!r}; the "
            "caller reads that as a capture that happened")
        assert outcome.returncode == 1

        assert "refused" in out, out
        # Same artifact half as the `--upgrade` arm above: the pre-fix run wrote
        # `points : 0 statements` AND the render, so both are pinned absent.
        assert "points :" not in out, (
            "a REFUSED --capture-metadata printed the completion summary:\n" + out)
        assert not (tmp_path / "g.html").exists(), (
            "a REFUSED --capture-metadata wrote the --out render")

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
        uri = _fresh_graph("resolution")
        base = ["--db", uri, "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "g.html"), "--resolution",
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

        def _resolution_points() -> int:
            from tortoise.projection import FalkorProjection
            proj = FalkorProjection.from_uri(uri)
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

# ⛔ The two enum-only assertions that used to open
# `TestTheSweepReportsOutstandingWork` (`PARTIAL.returncode == 1`,
# `SKIPPED_NOTHING_PENDING.returncode == 0`) are DELETED, not relocated (review
# round 11, P3): they never drove the sweep, so under this class name they read
# as the sweep's pins while the real ones are the live arm below and
# `TestTheSweepDoesNotRedOnItsOwnIdempotency` — and every outcome's exit status
# is already table-pinned by `TestTheExitContractIsOneMapping`.

class TestTheSweepReportsOutstandingWork:
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
        # A per-test graph is what makes the sweep's verdict a statement about
        # THIS corpus: `--upgrade-all` discovers every pending Source in the
        # graph, so on the shared lane path a peer's leftovers would decide it.
        uri = _fresh_graph("sweep")
        base = ["--db", uri, "--log", str(tmp_path / "e.jsonl"),
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

        # The sweep is graph-GLOBAL: a Source left pending would make every
        # LATER sweep on THIS graph read `unresolved`. The graph is per-test
        # (`_fresh_graph`), so the delete can only reach this test's own node —
        # and the session journal drops the graph itself.
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(uri)
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
                if begin_skip:
                    raise AssertionError("a skip must not write a document")
                return None

        monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
        monkeypatch.setattr(ingest_mod, "_discover_pending_documents",
                            lambda proj, **k: rows)
        # `semantic_extract` / `capture_metadata` are read by the S7 gate
        # (`_s7_document_entities`) on any arm that reaches EXTRACTION; the
        # skip / not-document arms never get there, so they were absent before.
        args = argparse.Namespace(force=False, max_utterances=0, domain=None,
                                  include_unstamped=False,
                                  point_model="mock:cheap",
                                  relation_model="mock:reason",
                                  semantic_extract=False,
                                  capture_metadata=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            outcome = ingest_mod._do_upgrade_all(_Proj(), _API(), args)
        return outcome, buf.getvalue()

    def test_an_empty_population_is_skipped_nothing_pending(
            self, tmp_path, monkeypatch):
        """The `SKIPPED_NOTHING_PENDING` return, DRIVEN (round-2 review, 2nd half).

        Nothing pinned this arm: the enum-only assertions that used to open
        `TestTheSweepReportsOutstandingWork` (deleted in round 11 — they asserted
        `IngestOutcome.SKIPPED_NOTHING_PENDING.returncode == 0`, a property of
        the ENUM) never drove the sweep. Delete the `return IngestOutcome.
        SKIPPED_NOTHING_PENDING` above the loop and the sweep falls through to
        the `DONE` tail while every enum assertion stays green.
        """
        outcome, out = self._sweep(tmp_path, monkeypatch, [], begin_skip=False)
        assert "no document Sources are awaiting extraction" in out, out
        assert outcome is IngestOutcome.SKIPPED_NOTHING_PENDING, (
            f"an empty sweep returned {outcome!r} (exit "
            f"{outcome.returncode}); 'nothing to do' is the ask's own answer and "
            "must be distinguishable from both a completed run and a refusal")

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


# --------------------------------------------------------------------------
# A partial extraction is not a completed ask — on all THREE arms (§1a).
# --------------------------------------------------------------------------

_PARTIAL_STATS = {"points": 1, "operators": 0, "sections": 2,
                  "point_ids": ["p1"], "failed_sections": ["Section two"]}


def _stub_partial_extractor(monkeypatch) -> None:
    """Make the document stage report ONE failed section.

    ⚠️ Why a stub rather than a fixture: `extract_from_document` returns a
    non-empty `failed_sections` ONLY under `skip_on_failure=True`
    (`tortoise/extractor.py::_run_document` re-raises otherwise), and `ingest`
    never passes it — so no real document can produce this state today. The
    outcome is still wired to it (review P2, both rounds: the door printed
    `warning: N sections failed extraction` and returned `DONE` → exit 0), and
    a comment claiming a behaviour nothing can falsify is exactly what this file
    exists to prevent. The stub is the falsifier.

    ⚠️ It must patch BOTH bindings. `main`'s full-ingest arm calls the
    MODULE-LEVEL name (`tortoise/ingest.py`: `from .extractor import …
    extract_from_document`), while `_do_upgrade` / `_do_upgrade_all` re-import it
    inside the function body — so a patch on `tortoise.extractor` alone is INERT
    for the full-ingest arm. MEASURED before this was fixed: that arm ran the
    real extractor, printed `extracted 0 Points, 0 operators from 0 sections`,
    and reded on `"failed extraction" not in out` — the stub was asserting
    nothing there, which is the vacuous-control shape this class exists for.
    """
    def _partial(*_a, **_k):
        return dict(_PARTIAL_STATS)

    monkeypatch.setattr("tortoise.extractor.extract_from_document", _partial)
    monkeypatch.setattr("tortoise.ingest.extract_from_document", _partial)


class TestAPartialExtractionIsNotDone:
    def test_the_sweep_does_not_report_a_partial_upgrade_as_complete(
            self, tmp_path, monkeypatch):
        """The `--upgrade-all` arm, through the stubbed population.

        Pre-fix this returns `DONE` → exit 0 with the `warning:` line as the only
        record — and the revisit marker cleared, so no later sweep retries the
        document and nothing else can ever report the shortfall.

        ⛔ The report must also sit OUTSIDE `if skipped:`: a partially-extracted
        document IS `upgraded`, so `skipped` stays 0 and a predicate riding that
        block would be the same wrong-condition shape round 5 fixed for the
        verdict sentence.
        """
        doc = tmp_path / "sweep-partial.md"
        doc.write_text("## Section one\n\nSomething true.\n", encoding="utf-8")
        _stub_partial_extractor(monkeypatch)
        outcome, out = TestTheSweepDoesNotRedOnItsOwnIdempotency._sweep(
            tmp_path, monkeypatch, [(doc.name, str(doc), True, None)],
            begin_skip=False)
        assert "failed extraction" in out, out
        assert outcome is IngestOutcome.PARTIAL, (
            "a sweep that upgraded a document only PARTIALLY returned "
            f"{outcome!r} (exit {outcome.returncode}); its warning went to "
            f"stdout and its revisit marker was cleared:\n{out}")
        assert outcome.returncode == 1
        # The verdict sentence must be emitted where it is TRUE (round 5 pinned
        # the both-ways direction for the other buckets).
        assert "exits NON-ZERO" in out, out

    def test_the_verdict_sentence_is_printed_once_for_two_outstanding_kinds(
            self, tmp_path, monkeypatch):
        """Round 8 P3: a partial upgrade PLUS an unreadable candidate printed the
        door's verdict TWICE.

        The partial block and the still-pending block each carried their own
        "this sweep exits NON-ZERO" sentence, and this population reaches both
        (the partial document IS `upgraded`, so it lands in the first block; the
        missing file lands in `skipped`/`unresolved` in the second). A door that
        states its verdict twice reads as two separate problems, and the second
        reading is wrong. Pinned as a COUNT so re-introducing a second emission
        site reds regardless of wording.
        """
        doc = tmp_path / "sweep-partial-and-unreadable.md"
        doc.write_text("## Section one\n\nSomething true.\n", encoding="utf-8")
        _stub_partial_extractor(monkeypatch)
        outcome, out = TestTheSweepDoesNotRedOnItsOwnIdempotency._sweep(
            tmp_path, monkeypatch,
            [(doc.name, str(doc), True, None),
             ("gone.md", str(tmp_path / "gone.md"), True, None)],
            begin_skip=False)
        assert outcome is IngestOutcome.PARTIAL, out
        assert "failed extraction" in out, out   # the partial half is present
        assert "unreadable" in out, out           # the pending half is present
        assert out.count("this sweep exits NON-ZERO") == 1, (
            "the sweep stated its verdict "
            f"{out.count('this sweep exits NON-ZERO')} times for TWO outstanding "
            "kinds; it must be ONE line, emitted from one place:\n" + out)

    @pytest.mark.skipif(not _DB_URI, reason="needs TORTOISE_DB_URI (docker lane)")
    def test_an_upgrade_that_extracted_only_some_sections_is_partial(
            self, tmp_path, monkeypatch):
        """The `--upgrade` arm: `_do_upgrade` returned `DONE` after the warning."""
        doc = tmp_path / "partial.md"
        doc.write_text("---\ntitle: Partial\n---\n\n## Section one\n\n"
                       "Something true.\n", encoding="utf-8")
        base = ["--db", _fresh_graph("upgrade_partial"),
                "--log", str(tmp_path / "e.jsonl"),
                "--out", str(tmp_path / "g.html"),
                "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
        assert _run([str(doc), "--capture-metadata", *base])[0] is IngestOutcome.DONE

        _stub_partial_extractor(monkeypatch)
        outcome, out = _run(["--upgrade", str(doc), *base])
        assert "failed extraction" in out, out
        assert outcome is IngestOutcome.PARTIAL, (
            "an --upgrade that extracted only some sections returned "
            f"{outcome!r} (exit {outcome.returncode}); the shortfall exists only "
            f"in its stdout:\n{out}")
        assert outcome.returncode == 1

    @pytest.mark.skipif(not _DB_URI, reason="needs TORTOISE_DB_URI (docker lane)")
    def test_a_full_ingest_that_extracted_only_some_sections_is_partial(
            self, tmp_path, monkeypatch):
        """The full-ingest arm — and `PARTIAL` is NOT a refusal.

        The completion artifacts ARE written (most of the ask was performed);
        the exit status is what carries the shortfall. Asserting that keeps a
        future "make PARTIAL exit before the artifacts" change from silently
        becoming a regression in the other direction.
        """
        doc = tmp_path / "full.md"
        doc.write_text("---\ntitle: Full\n---\n\n## Section one\n\nSomething "
                       "true.\n", encoding="utf-8")
        _stub_partial_extractor(monkeypatch)
        # The S7 stage is stubbed too: it builds the REAL embedding model, which
        # on a hermetic lane is a blocked https://huggingface.co request (#4387)
        # retried five times per file before failing open. This arm's subject is
        # the exit contract, not entity extraction, so the network dependency is
        # removed rather than tolerated.
        monkeypatch.setattr("tortoise.ingest._s7_document_entities",
                            lambda *_a, **_k: {})
        outcome, out = _run([
            str(doc), "--db", _fresh_graph("full_partial"),
            "--log", str(tmp_path / "e.jsonl"), "--out", str(tmp_path / "g.html"),
            "--point-model", "mock:cheap", "--relation-model", "mock:reason",
        ])
        assert "failed extraction" in out, out
        assert outcome is IngestOutcome.PARTIAL, (
            "a full ingest that extracted only some sections returned "
            f"{outcome!r} (exit {outcome.returncode}):\n{out}")
        assert outcome.returncode == 1
        assert "points :" in out, out
        assert (tmp_path / "g.html").exists()


#: A ``file:line`` coordinate in prose. The filename-anchored half is the census
#: pin's OWN predicate, imported rather than re-spelled: two spellings for one
#: question drift, and this file's first version proved it — it matched only the
#: contiguous ``ingest.py:<n>`` form, so the split spelling
#: (``config/ci-surfaces.yml` (`:<n>`)``) and a ranged coordinate walked through a
#: guard that claimed to ban both. The
#: second alternative is the bare backticked continuation (``:`<n>` ``), which
#: the census never needed because its cells always name the file. That half is
#: SIX digits wide on purpose: the first version stopped at four, and this file's
#: own subject is a `durations` row past line 10000, which is five digits — so
#: the natural short form (a bare ``(`:<n>`)`` after the filename was named a
#: sentence earlier) walked through a guard that claims to ban it.
#: The THIRD alternative closes the census half's extension blind spot: the
#: imported predicate reaches only `.py`/`.yml`/`.yaml`, so a docstring citing
#: `BOUNDED-OPERATION-CONTRACT.md:120` or `pyproject.toml:60` — the two shapes
#: this file's prose actually points at — walked through a guard whose positive
#: control only ever exercised `.py`/`.yml` spellings. It matches a dotted token
#: whose extension is one a SOURCE or DOC file carries, which is the claim it can
#: keep: an extension outside that list and a host's TLD
#: (``api.example.com:8080``, ``https://…:443``) are both outside the list on
#: purpose, and the lookbehind refuses a match that starts mid-URL.
#: Deliberately NOT matching a host:port whose TLD is OUTSIDE the extension
#: list (``localhost:6379``, ``api.example.com:8080``). ``foo.sh:8080`` and
#: ``x.md:443`` DO match, because ``.sh``/``.md`` are also source/doc extensions
#: and the list is the whole rule — a false positive here reds a docstring and
#: fails a test, never a gate.
_COORDINATE = re.compile(
    rf"{_LINE_CITATION.pattern}"
    r"|(?<![\w/:.])(?i:[\w./-]+)\.(?i:md|markdown|rst|toml|json|ya?ml|cfg|ini|txt|sh|bash"
    r"|ts|tsx|js|jsx|sql|pyx|parquet)[\s`'\"]*[(\[]?[\s`'\"]*:\d[\w-]*"
    r"|`:\d{1,6}(?:-\d{1,6})?`")


def _coordinate_citations(text: str) -> list[str]:
    """Every ``file:line`` coordinate in `text`, in source order."""
    return [m.group(0) for m in _COORDINATE.finditer(text)]


def _docstrings_by_symbol(source: str | None = None) -> list[tuple[str, str]]:
    """Every docstring in THIS module, as ``(qualified symbol, text)`` pairs.

    Keyed by the QUALIFIED path (``Class.method``), never by the bare name, and
    returned as a LIST rather than a dict. Reason: the first version keyed by
    `node.name` through `setdefault`, so the moment a second class re-used a
    method name one of the two docstrings was silently never examined — a green
    meaning "the first symbol of each name is clean". A list cannot collapse
    two same-named symbols into one entry.

    `source` exists so the collision behaviour can be pinned against a synthetic
    module (a real same-name collision would have to be added to this file).
    """
    tree = ast.parse(
        source if source is not None
        else Path(__file__).read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        pairs.append(("<module>", module_doc))

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                doc = ast.get_docstring(child)
                if doc:
                    pairs.append((name, doc))
                walk(child, name)
            else:
                walk(child, prefix)

    walk(tree, "")
    return pairs


class TestThisFilesEvidencePointersCannotGoStale:
    """No docstring here may point a reader at a ``file:line`` coordinate.

    WHY THIS IS A PIN AND NOT TIDINESS. Four line numbers in
    `test_the_installed_entry_point_exits_non_zero_on_a_refusal`'s docstring are
    the *evidence* for why its arms assert the guard's message and not only the
    sign of the status. A rebase inserted ten lines above `main`; none of the
    four was re-measured, and every one of them came to point at prose — `main`'s
    own docstring and three lines of `--include-unstamped` help text. The tidy
    response to an evidence pointer that cannot be followed is to delete the
    assertions it justifies, which reinstates precisely the vacuous arm that
    docstring documents. A symbol (`tortoise/ingest.py::main`, `parse_args`, and
    the two guards' own messages) survives the next rebase; a line number does
    not. This is the rule that ``BOUNDED-OPERATION-CONTRACT.md`` §2's census
    already adopted when it moved its evidence cells to ``module::name``.

    The guard applies to its own docstring, so the pre-fix text it must catch
    lives in a string literal below, never in prose here.
    """

    def test_no_docstring_in_this_file_cites_a_file_line_coordinate(self):
        offenders = {
            name: _coordinate_citations(doc)
            for name, doc in _docstrings_by_symbol()
            if _coordinate_citations(doc)
        }
        assert not offenders, (
            "these docstrings cite a `file:line` coordinate instead of a symbol; "
            "a coordinate re-stales on the next rebase, so cite "
            f"`module::name` (or the guard's own message) instead: {offenders}")

    def test_the_guard_reds_on_the_text_that_went_stale(self):
        """Non-vacuity: the exact pre-fix sentences are what the predicate catches."""
        pre_fix = (
            "status. `--db` is `required=True` (`ingest.py:1034`) and "
            "`parse_args`\n        (`:1099`) runs BEFORE both mode guards "
            "(`:1104`, `:1108`), so an arm")
        assert _coordinate_citations(pre_fix) == [
            "ingest.py:1034", "`:1099`", "`:1104`", "`:1108`"], (
            "the predicate must catch the pre-fix citations with the same "
            "spelling they were written in, or this guard is green on the very "
            "text it exists to ban")

    def test_the_guard_sees_the_split_ranged_and_non_py_spellings_too(self):
        """Positive control for the forms the FIRST version of this guard missed.

        It matched only the contiguous single-suffix run, so the split form the
        repo actually writes (a bare path, then the coordinate in its own code
        span), a ranged coordinate, and the non-`.py` extensions the imported
        half cannot reach all walked through a guard that claims no docstring
        here cites a coordinate — that half reaches `.py`/`.yml`/`.yaml` only.
        The spellings
        themselves live in the loop below, never in prose, because the guard
        applies to this docstring too — which is the point. This file's subject
        is a YAML row, so the missable spelling is the likely one.
        """
        for spelling in (
            "`tortoise/ingest.py` (`:1044-1049`)",
            "`config/ci-surfaces.yml` (`:121-143`)",
            "config/ci-surfaces.yml:10481",
            "`sdk.py` (`:34655`)",
            # The bare continuation, five digits — the short form a reader writes
            # once the filename is a sentence behind, and the width the first
            # version capped at four.
            "the durations row (`:10481`)",
            "(`:10481-10490`)",
            # The extensions the imported half cannot reach, which this file's
            # own prose points at: a `.md` contract doc and `pyproject.toml`.
            "`docs/architecture/BOUNDED-OPERATION-CONTRACT.md:120`",
            "pyproject.toml:60",
            "config/x.json:12",
        ):
            assert _coordinate_citations(spelling), (
                f"the guard is blind to {spelling!r} — a coordinate the census "
                "pin catches, so this file could re-stale under a green check")

    def test_one_spelling_two_enforcement_points(self):
        """The census pin owns the filename-anchored spelling; this guard reuses it.

        Two predicates for one question drift, and the drift is invisible in the
        worst direction: a citation matching one and not the other is RED in the
        census and GREEN here. The filename-anchored half is imported, so this
        asserts the composition did not quietly narrow it.
        """
        for spelling in (
            "`config/surface-manifest.yml` (`:121-143`)",
            "hosted_api.py:10209",
            "`tortoise/ingest.py` (`:1044-1049`)",
        ):
            assert _LINE_CITATION.findall(spelling), spelling
            assert _coordinate_citations(spelling), spelling
        for spelling in (
            "`tortoise/ingest.py::main`",
            "docker://:f@localhost:6379/x",
            "measured 2026-11-05, 143.9 s",
            "`config/ci-surfaces.yml` → the durations row",
        ):
            assert not _LINE_CITATION.findall(spelling), spelling
            assert not _coordinate_citations(spelling), spelling

    def test_a_second_symbol_with_the_same_name_is_still_examined(self):
        """Collision non-vacuity: a bare-name key would exempt the second symbol.

        The first version keyed docstrings by `node.name` via `setdefault`, so a
        method name reused in a second class resolved to ONE key and whichever
        docstring came second was never examined. Two classes, one shared method
        name, the stale cite in the SECOND — the guard must still find it.
        """
        source = (
            'class A:\n'
            '    """clean"""\n'
            '    def same(self):\n'
            '        """no coordinate here"""\n'
            'class B:\n'
            '    """clean"""\n'
            '    def same(self):\n'
            '        """cites `ingest.py:1034` here"""\n')
        pairs = _docstrings_by_symbol(source)
        symbols = [name for name, _ in pairs]
        assert "A.same" in symbols and "B.same" in symbols, symbols
        offenders = {
            name: _coordinate_citations(doc)
            for name, doc in pairs
            if _coordinate_citations(doc)
        }
        assert offenders == {"B.same": ["ingest.py:1034"]}, (
            "a same-named symbol in a second class escaped the guard: "
            f"{offenders}")

    def test_the_guard_does_not_red_on_a_host_port_or_a_symbol(self):
        """The other direction: a URL port and a ``module::name`` cite stay green.

        The DOTTED host:port is here because the third alternative made it the
        live risk: an extension-agnostic arm would read ``api.example.com:8080``
        as a citation. What prevents it is the EXTENSION LIST — not the shape —
        so this pins only the TLDs that list excludes: ``.sh`` and ``.md`` ARE
        ccTLDs (``.ts``/``.js`` are not, but both are in the extension list) and
        all four DO match as citations, which is accepted because a false
        positive here reds a docstring, never a gate.
        """
        assert _coordinate_citations("docker://:f@localhost:6379/x") == []
        assert _coordinate_citations(
            "the endpoint api.example.com:8080 is live") == []
        assert _coordinate_citations(
            "https://api.premiselabs.co:443/v1/x") == []
        assert _coordinate_citations(
            "`tortoise/ingest.py::main` and "
            "`argparse.ArgumentParser.parse_args`") == []
