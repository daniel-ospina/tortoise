"""#2981 — a full-but-healthy DB is not a corrupt DB.

`_auto_health_recover` collapses every probe failure into ONE message that
advises `python -m tortoise rebuild`. When the server has simply reached
`maxmemory` (policy `noeviction`), the graph is INTACT and that advice points
at destroying healthy data.

These tests **execute the real handler** and assert on what it RAISES — the
value the operator actually receives — not on the presence of a string in the
source. Both directions are pinned, because a fix that only removes the
rebuild advice is as wrong as the bug:

* full-but-healthy  -> the error NAMES used_memory/maxmemory and does NOT
                       advise rebuild (the write-refusal branch);
* genuinely corrupt -> the rebuild advice STILL appears (the branch NARROWS
                       the remedy, it does not remove it).

Revert-verified: delete the `_write_refusal_message` branch in
`_auto_health_recover` and `test_full_but_healthy_is_not_reported_as_corrupt`
goes RED (the rebuild advice reappears); make `_write_refusal_message` return
a message for every exception and
`test_genuine_corruption_still_advises_rebuild` goes RED.
"""

from __future__ import annotations

import pytest

from tortoise.projection import FalkorProjection

# The server's own wording when `maxmemory` is reached with `noeviction`.
_MAXMEMORY_ERROR = (
    "OOM command not allowed when used memory > 'maxmemory'."
)
# An unrelated failure — the class that must STILL reach the rebuild advice.
_CORRUPTION_ERROR = "Invalid graph operation on empty key"


def _projection(*, probe_error: BaseException | None, embedded: bool = False):
    """A projection whose probe fails for a chosen reason.

    Built with ``object.__new__`` so the REAL ``_auto_health_recover`` runs
    without a live server: the handler is the unit under test, not the DB.
    """
    proj = object.__new__(FalkorProjection)
    proj._probe_error = probe_error
    proj._is_embedded = embedded
    proj._path = "/tmp/does-not-exist-2981"
    proj._probe_ok = lambda: False  # type: ignore[method-assign]
    return proj


def test_full_but_healthy_is_not_reported_as_corrupt(monkeypatch):
    """A maxmemory write-refusal names the numbers and must NOT say rebuild."""
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    # used 512 MiB of a 512 MiB ceiling — the wedge the issue measured.
    proj._memory_pressure = lambda: (512 * 1024 * 1024, 512 * 1024 * 1024)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    msg = str(err.value)
    # 1. It names the memory numbers — the operator sees WHY.
    assert "used_memory" in msg, msg
    assert "maxmemory" in msg, msg
    assert "512.0 MiB" in msg, msg
    # 2. It does NOT ADVISE the rebuild — the assertion is on the PRESCRIBED
    #    COMMAND, not on the word "rebuild": the correct message contains
    #    "do NOT rebuild", so a substring check on the bare word would fail
    #    on a correct message and pass on a wrong one that merely drops the
    #    word while still pointing at the rebuild path.
    assert "python -m tortoise rebuild" not in msg, (
        "a full-but-healthy graph must never be sent to the rebuild path: " + msg
    )
    # 3. It says the data is intact, forbids the rebuild, and names the remedy.
    assert "INTACT" in msg, msg
    assert "do NOT rebuild" in msg, msg
    assert "GRAPH.DELETE" in msg, msg


def test_memory_pressure_unreadable_still_avoids_rebuild(monkeypatch):
    """An unreadable INFO must not demote the failure back to 'corrupt'."""
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_MAXMEMORY_ERROR))
    proj._memory_pressure = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    msg = str(err.value)
    assert "maxmemory" in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg


def test_genuine_corruption_still_advises_rebuild(monkeypatch):
    """THE OTHER DIRECTION — an unrelated failure keeps the rebuild advice.

    Without this test the fix could be 'delete the message', which would be a
    worse bug than the one being fixed.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(_CORRUPTION_ERROR))
    proj._memory_pressure = lambda: (1, 512 * 1024 * 1024)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    assert "python -m tortoise rebuild" in str(err.value), (
        "corruption must still point at the rebuild path: " + str(err.value)
    )


def test_corruption_in_server_mode_still_advises_rebuild(monkeypatch):
    """Production/server mode: the rebuild advice is unchanged for corruption."""
    monkeypatch.setenv("FLY_APP_NAME", "tortoise-test")
    proj = _projection(probe_error=RuntimeError(_CORRUPTION_ERROR))

    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()

    msg = str(err.value)
    assert "server/production mode" in msg, msg
    assert "python -m tortoise rebuild" in msg, msg


@pytest.mark.parametrize("cause,needle", [
    (_MAXMEMORY_ERROR, "maxmemory"),
    ("LOADING Redis is loading the dataset in memory", "loading"),
    ("GRAPH.COPY failed, could not fork", "fork"),
])
def test_each_backend_cause_names_itself(cause, needle):
    """#3634 — each backend cause reports ITS OWN reason, not a neighbour's.

    A maxmemory refusal, a still-hydrating `LOADING` reply and a module-fork
    refusal are three different problems with three different remedies.
    Falling through to the rebuild advice (or borrowing the maxmemory
    message) would send an operator to destroy healthy data for a transient
    cause.

    No `FLY_APP_NAME` dance: all three causes branch before the
    `is_prod or not self._is_embedded` gate, so which message is raised here
    does not depend on the env var. (The FORK lane text is keyed on
    `self._is_embedded` alone — see `_backend_failure_message`.)
    """
    proj = _projection(probe_error=RuntimeError(cause))
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    assert needle in str(err.value).lower()
    assert "python -m tortoise rebuild" not in str(err.value)


@pytest.mark.parametrize("cause", [
    "GRAPH.COPY failed, could not fork",   # FalkorDB's reply (cmd_copy.c)
    "Can't fork for module: File exists",  # redis module.c, errno 17 (EEXIST)
])
def test_documented_fork_wordings_route_to_the_fork_remedy(cause):
    """P2-1 — the canonical classifier recognises the engine's REAL wordings.

    The parallel table shipped in 0b9b78eb8 matched `fork failed`/`can't fork`
    but MISSED `could not fork` — FalkorDB's own reply, and the exact string in
    `tests/test_fork_slot_wedge_3845.py` — so a real fork refusal fell through
    to the rebuild advice. Routing through `fork_slot.is_fork_refusal` closes
    that gap; this test fails if that routing is removed.

    `embedded=True` pins the embedded-lane variant, whose action names the
    slot cure. The server-lane variant is pinned separately (below).
    """
    proj = _projection(probe_error=RuntimeError(cause))
    msg = proj._backend_failure_message(RuntimeError(cause), embedded=True)
    assert msg is not None, cause
    assert "recover_fork_slot" in msg, msg


def test_fork_remedy_names_the_slot_cure_and_not_memory(monkeypatch):
    """P2-4 — the fork remedy must not repeat the misattribution it fixes.

    errno 17 is EEXIST: a hung `redis-module-fork` child holds Redis's single
    module-fork slot (fork_slot.py:23-30) — NOT memory/process pressure. The
    remedy shipped in 0b9b78eb8 told the operator to relieve a memory limit,
    which cannot clear a slot held by a hung child, and omitted the documented
    cure. Pin both halves: the cure is named, the memory misattribution is not.

    Pinned on the EMBEDDED lane (`_is_embedded=True`, no FLY_APP_NAME) — the
    only lane where `recover_fork_slot` applies.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=True,
    )
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    msg = str(err.value)
    assert "EEXIST" in msg, msg
    assert "recover_fork_slot" in msg, msg
    assert "redis-module-fork" in msg, msg
    assert "not corrupt" in msg, msg
    # The forbidden misattribution: no memory/process remedy may be prescribed.
    assert "raise --maxmemory" not in msg, msg
    assert "memory/process limit" not in msg, msg
    assert "add memory" not in msg.lower(), msg


def test_fork_remedy_names_both_causes_and_the_discriminator(monkeypatch):
    """P2 — one refusal, TWO mechanically distinct causes; do not assert one.

    `GRAPH.COPY failed, could not fork` is emitted both when a background
    RDB/AOF child holds the slot (transient, healthy) and when a previous
    module fork never exited (#3845). A remedy that asserts only the hang is
    false in the save case — and its prescribed `recover_fork_slot` is a NO-OP
    there (it is socket-scoped to `redis-module-fork`, so it returns
    `recovered=False`). Pin both causes and the discriminating action.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=True,
    )
    msg = str(proj._backend_failure_message(
        RuntimeError("GRAPH.COPY failed, could not fork"), embedded=True))
    # (a) BOTH causes are named — the message must not claim a single cause.
    assert "TWO" in msg, msg
    assert "background RDB save" in msg or "background\nRDB save" in msg, msg
    assert "hung" in msg and "redis-module-fork" in msg, msg
    # The old single-cause assertion, verbatim — must be gone.
    assert "a hung, un-reaped `redis-module-fork` child still holds that slot" not in msg, msg
    # (b) The discriminating action: when no hung child is found, the slot is
    #     held by an in-flight save — wait, don't reap.
    assert "if no hung" in msg, msg
    assert "wait for it to finish and retry" in msg, msg
    # The EEXIST / EAGAIN distinction is retained.
    assert "EAGAIN" in msg, msg


def test_fork_remedy_on_server_lane_does_not_prescribe_the_embedded_cure(monkeypatch):
    """P2 — `_auto_health_recover` emits the FORK remedy BEFORE the
    `is_prod or not self._is_embedded` gate, so a remote/docker operator must
    not be handed an embedded-only call. There `socket_path_of(db)` is None and
    `recover_fork_slot` returns 'not an embedded unix-socket server'.
    """
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=False,  # remote/docker FalkorDB
    )
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    msg = str(err.value)
    # (c) the embedded-only cure is NOT prescribed on this lane...
    assert "recover_fork_slot" not in msg, msg
    # ...the server-mode action IS...
    assert "server" in msg.lower(), msg
    assert "restart" in msg.lower(), msg
    # ...and the two-cause analysis + discriminator still apply on every lane.
    assert "TWO" in msg, msg
    assert "wait for it to finish and retry" in msg, msg
    assert "EEXIST" in msg, msg
    assert "not corrupt" in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg


@pytest.mark.parametrize("is_prod,embedded,lane_label,has_cure", [
    (False, False, "[server lane", False),
    (False, True, "[embedded lane]", True),
    (True, False, "[server lane", False),
    (True, True, "[embedded lane]", True),
])
def test_fork_remedy_lane_follows_the_embedded_axis_only(
    monkeypatch, is_prod, embedded, lane_label, has_cure
):
    """P2 — the lane remedy is selected on `self._is_embedded` ALONE.

    The flag previously read `_is_embedded and not is_prod`, conflating two
    independent axes. That handed a prod+embedded handle the server text
    ("this is not an embedded unix-socket server") even though its socket
    path WAS present and the slot cure was available. Pin all four
    `is_prod` × `_is_embedded` products: only the embedded axis moves the
    lane, and no variant may assert a socket property it cannot know.
    """
    if is_prod:
        monkeypatch.setenv("FLY_APP_NAME", "tortoise-test")
    else:
        monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(
        probe_error=RuntimeError("GRAPH.COPY failed, could not fork"),
        embedded=embedded,
    )
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    msg = str(err.value)
    assert lane_label in msg, msg
    assert ("recover_fork_slot" in msg) is has_cure, msg
    assert "this is not an embedded unix-socket server" not in msg, msg
    assert "python -m tortoise rebuild" not in msg, msg


def test_bare_fork_stem_is_not_a_refusal():
    """P2-2 — a bare `fork` stem is not evidence of a module-fork refusal.

    The removed parallel table matched `fork failed`/`can't fork`/`cannot
    fork`; the canonical classifier requires one of the engine's actual
    module-fork wordings. Unrelated text mentioning a fork can no longer be
    routed to the fork remedy.
    """
    proj = _projection(probe_error=None)
    assert proj._backend_failure_message(
        RuntimeError("disk fork failed during compaction")) is None


def test_write_refusal_only_matches_the_server_wording():
    """The classifier is signature-scoped, not a blanket catch-all."""
    proj = _projection(probe_error=None)
    proj._memory_pressure = lambda: (1, 2)  # type: ignore[method-assign]

    # The real server wordings are recognised...
    for text in (
        _MAXMEMORY_ERROR,
        "OOM command not allowed when used memory > 'maxmemory'",
        "Out of memory",
    ):
        assert proj._write_refusal_message(RuntimeError(text)) is not None, text

    # ...and unrelated failures are NOT (so they fall through to rebuild).
    for text in (_CORRUPTION_ERROR, "connection refused", "WRONGTYPE"):
        assert proj._write_refusal_message(RuntimeError(text)) is None, text

    # A missing exception is not a write refusal.
    assert proj._write_refusal_message(None) is None
