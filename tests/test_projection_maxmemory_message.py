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
