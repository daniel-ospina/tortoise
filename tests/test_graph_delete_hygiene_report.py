"""#8746: the graph-delete refusal record is written AND surfaced.

`tests/_live_utils.py::drop_test_graph` deletes a per-run FalkorDB graph at
teardown through `tortoise.graph_delete_guard.safe_graph_delete`, behind a
fail-closed name gate. It is log-and-continue BY POLICY (a teardown must not red
a suite), and the session sweep remains the backstop.

That policy has a capture problem the repo has already measured: a PASSING
test's `logger.warning` is DISCARDED by pytest capture (#7735 / #1103 — verified
0 occurrences under this CI's own flags). Relying on the log alone would make a
systematic refusal invisible, and the per-run graph leak #8746 removes would
return unobserved — the very defect #8746's own test docstring names ("the leak
stayed invisible because the only evidence consulted was the suite's own
bookkeeping").

So the tolerated refusals/failures are ACCUMULATED and handed to
`tests/_tmpdir_hygiene.py::write_tolerated_cleanup_report` as an EXTRA SECTION of
the `tempdir-hygiene-end.json` artifact. That file — not a new one — because the
suite-session jobs ALREADY dump it (pinned by
`tests/test_tmpdir_hygiene.py::test_suite_jobs_surface_the_tolerated_leak`): a
second artifact nothing reads is not an observability channel, and adding a dump
step is a workflow change (#8746 review r3 P2).

This file pins all three halves: the accumulation, the artifact section (write
and merge), and the WIRING that connects them at session end.

HERMETIC — no DB, no lane, no fixture.
"""
from __future__ import annotations

import ast
import contextlib
import json
from pathlib import Path

from tests import _live_utils
from tests import _tmpdir_hygiene as _hygiene
from tests._tmpdir_hygiene import write_tolerated_cleanup_report

REPO = Path(__file__).resolve().parent.parent
_SECTION = "drop_test_graph_events"
_ARTIFACT = "tempdir-hygiene-end.json"


@contextlib.contextmanager
def _isolated_tolerated_leaks(seed=None):
    """Snapshot → clear (→ seed) → restore the SESSION-GLOBAL #7735 record.

    `tests/_tmpdir_hygiene._TOLERATED_CLEANUP_LEAKS` is a session global that
    nothing resets per test, and `tests/conftest.py` patches
    `TemporaryDirectory.cleanup` at IMPORT — so an ENOTEMPTY tolerated by ANY
    earlier test in the same session is still in the list when this file runs,
    and `write_tolerated_cleanup_report` then returns a path purely because of
    it. Two tests below assert on a CLEAN record, so without this guard they
    false-red for a reason with no relation to #8746 (the order-independence
    `tests/test_tmpdir_hygiene.py::test_write_tolerated_cleanup_report_is_silent_when_nothing_leaked`
    already pins for the same writer).

    `seed` lets a test SEED a known tolerated leak — so the merge test asserts
    the tempdir key's real CONTENT, not that an empty list stayed empty.
    """
    saved = list(_hygiene._TOLERATED_CLEANUP_LEAKS)
    _hygiene._TOLERATED_CLEANUP_LEAKS[:] = list(seed or [])
    try:
        yield
    finally:
        _hygiene._TOLERATED_CLEANUP_LEAKS[:] = saved


class _FakeProj:
    """A projection stand-in: the name is all the gate reads."""

    def __init__(self, name) -> None:
        self.graph_name = name
        self.db = None


def test_a_tolerated_refusal_reaches_the_shared_artifact(tmp_path):
    """The refusal is WRITTEN, not only logged (the whole point of the channel)."""
    snapshot = _live_utils.graph_delete_events()
    try:
        probe = "team_review_probe_8746"
        _live_utils.drop_test_graph(_FakeProj(probe))
        recorded = [e for e in _live_utils.graph_delete_events()
                    if e["name"] == probe]
        assert recorded and recorded[-1]["event"] == "refused", recorded
        path = write_tolerated_cleanup_report(
            log_dir=str(tmp_path), extra={_SECTION: _live_utils.graph_delete_events()})
        assert path is not None, "no artifact written for a tolerated refusal"
        assert path.endswith(_ARTIFACT), path
        with open(path) as fh:
            written = json.load(fh)
        entries = written[_SECTION]
        assert any(e["name"] == probe and e["event"] == "refused"
                   for e in entries), entries
    finally:
        # The probe must not fork into the real session artifact (the #7735
        # `_discard_tolerated_leaks` precedent).
        _live_utils._restore_graph_delete_events(snapshot)


def test_a_clean_session_writes_no_graph_delete_section(tmp_path):
    """No tolerated event → no section, so a run cannot report a fabricated leak.

    Asserted in BOTH shapes the writer has, so the property named is the one
    exercised and not something stronger that only holds on a pristine process:

    (a) nothing tolerated at all → no artifact, hence no section;
    (b) an UNRELATED tolerated tempdir leak (#7735's session global) → the
        artifact IS written for it, and the graph-delete section is still
        absent. That is the docstring's invariant, and it is the arm the old
        `is None` assertion could not express: `extra={_SECTION: []}` is
        filtered to `{}`, so `is None` was really a claim about the OTHER
        session global being empty (#8746 review r3 P2).
    """
    snapshot = _live_utils.graph_delete_events()
    try:
        _live_utils._restore_graph_delete_events([])
        with _isolated_tolerated_leaks():
            assert write_tolerated_cleanup_report(
                log_dir=str(tmp_path), extra={_SECTION: []}) is None
            assert not (tmp_path / _ARTIFACT).exists()

        leaky = tmp_path / "unrelated-leak"
        leaky.mkdir()
        unrelated = "/tmp/tortoise_8746_unrelated_leak"
        with _isolated_tolerated_leaks([{"path": unrelated, "errno": 39,
                                         "reason": "Directory not empty"}]):
            path = write_tolerated_cleanup_report(
                log_dir=str(leaky), extra={_SECTION: []})
        assert path is not None, (
            "an unrelated tolerated leak must still produce the artifact")
        written = json.loads(Path(path).read_text())
        assert _SECTION not in written, (
            f"a clean graph-delete record invented a {_SECTION!r} section: "
            f"{written.get(_SECTION)!r}")
        assert [e["path"] for e in written["tolerated_cleanup_leaks"]] == [
            unrelated], written["tolerated_cleanup_leaks"]
    finally:
        _live_utils._restore_graph_delete_events(snapshot)


def test_the_section_merges_and_cannot_truncate_a_prior_record(tmp_path):
    """A peer/second session must not blank entries it does not own (#1103 shape).

    The section is merged BY NAME into whatever is already in the artifact. A
    plain truncate would lose a peer's record wholesale — and then a real leak
    reads as "never happened".
    """
    prior = tmp_path / _ARTIFACT
    prior.write_text(json.dumps({_SECTION: [
        {"name": "test_peer_owned", "event": "failed", "reason": "peer"},
    ]}))
    snapshot = _live_utils.graph_delete_events()
    # SEED a known tolerated leak rather than relying on the session global
    # being empty: the assertion below is about the tempdir key KEEPING its
    # content while the new section merges in, which an empty list cannot show
    # (#8746 review r3 P2).
    tolerated = "/tmp/tortoise_8746_tolerated_probe"
    try:
        _live_utils._restore_graph_delete_events([])
        with _isolated_tolerated_leaks([{"path": tolerated, "errno": 39,
                                         "reason": "Directory not empty"}]):
            _live_utils._record_delete_event(
                "test_this_session", "refused", "probe")
            write_tolerated_cleanup_report(
                log_dir=str(tmp_path),
                extra={_SECTION: _live_utils.graph_delete_events()})
        written = json.loads(prior.read_text())
        names = {e["name"] for e in written[_SECTION]}
        assert names == {"test_peer_owned", "test_this_session"}, names
        # The tempdir key kept its own record and was not truncated by the
        # new section (the #1103 shape this test exists for).
        assert [e["path"] for e in written["tolerated_cleanup_leaks"]] == [
            tolerated], written["tolerated_cleanup_leaks"]
    finally:
        _live_utils._restore_graph_delete_events(snapshot)


def test_a_session_with_no_events_cannot_erase_a_prior_section(tmp_path):
    """The merge is TWO-WAY: an EMPTY current section must not delete a peer's.

    `extra={_SECTION: []}` is filtered to `{}` (nothing to say this session),
    and `extra` used to be the ONLY source of emitted section keys — so a
    session that recorded zero graph-delete events rewrote the artifact
    WITHOUT the section a peer had written, silently erasing a real refusal
    record. The forward direction (this session's events merge into a peer's)
    is `test_the_section_merges_and_cannot_truncate_a_prior_record`; this pins
    the reverse, which nothing red'ed on before (#8746 review r6 P2).
    """
    artifact = tmp_path / _ARTIFACT
    peer = {_SECTION: [
        {"name": "test_peer_owned", "event": "failed", "reason": "peer"},
    ]}
    artifact.write_text(json.dumps(peer))
    unrelated = "/tmp/tortoise_8746_peer_erase_probe"
    with _isolated_tolerated_leaks([{"path": unrelated, "errno": 39,
                                     "reason": "Directory not empty"}]):
        # (a) the finalizer's own shape on a clean session: the section is
        #     supplied but EMPTY, so it is filtered out before the merge.
        write_tolerated_cleanup_report(
            log_dir=str(tmp_path), extra={_SECTION: []})
        written = json.loads(artifact.read_text())
    assert [e["name"] for e in written.get(_SECTION, [])] == ["test_peer_owned"], (
        "a session with no graph-delete events ERASED the prior section: "
        f"{written!r}")
    assert [e["path"] for e in written["tolerated_cleanup_leaks"]] == [
        unrelated], written["tolerated_cleanup_leaks"]

    # (b) `extra=None` (no section kwarg at all) must carry the peer section
    #     over too, not drop it.
    with _isolated_tolerated_leaks([{"path": unrelated, "errno": 39,
                                     "reason": "Directory not empty"}]):
        write_tolerated_cleanup_report(log_dir=str(tmp_path))
        written = json.loads(artifact.read_text())
    assert [e["name"] for e in written.get(_SECTION, [])] == ["test_peer_owned"], (
        f"extra=None dropped the prior section: {written!r}")


def test_the_session_finalizer_feeds_the_section_into_the_artifact():
    """The WIRING is pinned, because an unwired writer is the r3 P2 defect.

    `tests/conftest.py::_redislite_hygiene` must hand `graph_delete_events()` to
    `write_tolerated_cleanup_report(extra=…)` — and it must be THAT finalizer:
    `_server_graph_hygiene` is URI-gated, while `live_proj` skips on a TCP probe
    and so also runs on the URI-less lane, where a refusal would otherwise be
    recorded in memory only.

    Asserted on the call's own arguments (an AST walk), not on a substring: a
    mention in a comment must not satisfy it.
    """
    tree = ast.parse((REPO / "tests" / "conftest.py").read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", ""))
        == "write_tolerated_cleanup_report"
        and any(kw.arg == "extra" for kw in node.keywords)
    ]
    assert len(calls) == 1, (
        "expected exactly one `write_tolerated_cleanup_report(extra=…)` call in "
        f"tests/conftest.py, found {len(calls)} — an unwired section is silent")
    extra = next(kw for kw in calls[0].keywords if kw.arg == "extra")
    assert isinstance(extra.value, ast.Dict), ast.dump(extra.value)[:200]
    keys = [k.value for k in extra.value.keys if isinstance(k, ast.Constant)]
    assert _SECTION in keys, (
        f"the session finalizer no longer feeds {_SECTION} into the artifact; "
        f"keys={keys} — the tolerated graph-delete record would be "
        "memory-only (#8746 review r3 P2)")
