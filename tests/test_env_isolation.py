# tests/test_env_isolation.py
"""#4883: the suite must not leak process-shared routing env vars between tests.

pytest runs ONE process, so a plain `os.environ[...] = ...` in any earlier test (no
monkeypatch, no restore) is visible to every test after it. The leak class is real and
present in the files listed on #4883; `tests/test_uri_env_mutations_declared.py` (#2084)
guards it for `TORTOISE_DB_URI` only, so it survived for every other routing key.

SCOPE — this file pins the LEAK CLASS, not the flake #4883 was opened for. That flake
(`test_pack_state.py::TestBackfillScript::test_apply_writes_to_introspection_read_target`)
is redislite replaying a `.settings` registry whose recorded socket is gone — #4879, fixed
in #4892 — and the victim passes `db_path` explicitly, so per-test env restoration cannot
influence it. Passing this file is not evidence that flake is fixed.

The repair under test is `_isolate_process_env` in `tests/conftest.py` (autouse, function
scope), which restores `TORTOISE_*`/`SUPABASE_*`/`PACK_STATE_*` after every test.

These two tests are deliberately ORDER-DEPENDENT — that IS the property under test. The
first LEAKS on purpose (no monkeypatch, no restore, exactly the anti-pattern); the second
asserts the leak did not survive. Without the conftest fixture the second test fails, so
this file is the regression pin for the class, not for the one instance.
"""
from __future__ import annotations

import os

_LEAKED_KEY = "TORTOISE_CONTROL_PLANE_LEAK_PROBE"
_LEAKED_VALUE = "leaked-by-test_a"


def test_a_a_plain_os_environ_write_leaks_without_the_guard():
    """The polluter: a raw write with no monkeypatch and no restore.

    This is not a hypothetical — it is the mechanism measured in #4883, and the
    pattern still present in every file listed on that issue.
    """
    os.environ[_LEAKED_KEY] = _LEAKED_VALUE
    assert os.environ[_LEAKED_KEY] == _LEAKED_VALUE


def test_b_the_leak_did_not_survive_into_the_next_test():
    """The pin: after the polluter, the key must be gone.

    Red without `_isolate_process_env`; green with it. A suite that fails this is
    one where any test can silently change the starting state of every test after
    it — which is the class #4883 reports, and the reason main was ~60% red on one
    test while that test passed in isolation.
    """
    assert _LEAKED_KEY not in os.environ, (
        f"{_LEAKED_KEY} leaked from the previous test "
        f"({os.environ.get(_LEAKED_KEY)!r}) — tests must not share process env state (#4883)"
    )
