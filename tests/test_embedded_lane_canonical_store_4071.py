"""#4071: the embedded test lane must never resolve to the canonical store.

#4028 removed the SYMPTOM (test residue Points in the owner's real embedded
store ``~/.tortoise/tortoise.db``) and shipped ``tools/purge_test_residue.py``,
but left the WRITER path open — so each full local embedded run re-seeds the
residue and undoes the purge. Measured in #4071: a second copy of the real
store taken ~30 minutes after the first held **6 additional**
``guard-remove-test`` Points.

``tests/conftest.py`` closes it at the one choke point (``TORTOISE_DB_PATH``,
precedence 2 in ``resolve_db_path``), set at conftest IMPORT time rather than
in a session fixture — a fixture is structurally too late for a test module
that constructs a bare SDK in its MODULE BODY, which runs during collection.
These tests pin that the guard actually BITES, so a future conftest edit
cannot quietly reopen the writer path.

NO TEST IN THIS FILE SKIPS A LANE. Every test asserts something real in BOTH
the embedded (no URI) and the URI lanes. A bare ``return`` in one branch would
pass without checking anything — the invisible ambient-skip that
``tests/test_markers.py`` exists to prevent.

The lane predicate is the SDK's OWN rule, not ``is_db_uri``: ``sdk.py`` binds
``_db_uri`` (and leaves ``_db_path`` None) for *any non-empty*
``TORTOISE_DB_URI``, including a path-style URI, which never reaches
``resolve_db_path``. Using ``is_db_uri`` here would false-red a path-style-URI
session, so the two predicates must not drift apart.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.config import DEFAULT_DB_PATH, resolve_db_path

#: The directory prefix the conftest guard uses, so the redirect it installs
#: is identifiable from a test without re-deriving the temp path.
_GUARD_MARKER = "tortoise-embedded-lane-"

#: Captured at MODULE IMPORT time — i.e. during COLLECTION, after conftest has
#: been imported and BEFORE any session-scoped fixture runs. This is the
#: PLACEMENT pin. The guard's whole justification is that a fixture is
#: structurally too late for a module-body writer, so a guard that works only
#: once a fixture has run would satisfy every TEST-TIME assertion in this file
#: while leaving the writer path open — which is exactly what happened on the
#: first, fixture-based revision of `tests/conftest.py`.
_REDIRECT_AT_COLLECTION = os.environ.get("TORTOISE_DB_PATH") or ""


def _sdk_binds_an_embedded_path() -> bool:
    """True when the SDK will resolve an embedded path (its own rule):
    ``sdk.py`` takes the URI branch for ANY non-empty ``TORTOISE_DB_URI``."""
    return not os.environ.get("TORTOISE_DB_URI")


def test_bare_sdk_does_not_resolve_to_the_canonical_store():
    """The issue's literal ask: a bare ``TortoiseSDK()`` under the embedded
    test lane must NOT resolve to ``DEFAULT_DB_PATH``.

    This is the constructor the residue came from — ``TortoiseSDK()`` with no
    path argument — so it is the one that must be pinned, not a helper.
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    if _sdk_binds_an_embedded_path():
        assert sdk._db_path, "the embedded lane must resolve SOME db path"
        assert os.path.abspath(sdk._db_path) != os.path.abspath(
            DEFAULT_DB_PATH), (
            "a bare TortoiseSDK() resolved to the canonical store "
            f"{DEFAULT_DB_PATH!r} — #4071's writer path is open again")
        assert _GUARD_MARKER in sdk._db_path, (
            f"the embedded lane resolved outside the guard's redirect: "
            f"{sdk._db_path!r}")
    else:
        # URI lane: the URI is authoritative, so no embedded store opens.
        assert sdk._db_path is None, (
            "a non-empty TORTOISE_DB_URI must not ALSO bind an embedded path "
            f"(got {sdk._db_path!r})")
        assert sdk._db_uri, "the URI lane must bind the URI"


def test_resolve_db_path_is_redirected_in_the_embedded_lane():
    """The choke point itself, asserted independently of the SDK: with no URI
    set, the resolver must return the guard's redirect rather than the
    canonical default. In the URI lane the SDK never calls this, so the
    branch instead pins the guard's guarantee that it did not leak out of its
    lane."""
    resolved = os.path.abspath(resolve_db_path())
    if _sdk_binds_an_embedded_path():
        assert resolved != os.path.abspath(DEFAULT_DB_PATH), (
            "resolve_db_path() fell through to the canonical store under the "
            "embedded lane — the #4071 guard is not in effect")
        assert _GUARD_MARKER in resolved, (
            f"expected the guard's redirect, got {resolved!r}")
        assert os.path.basename(resolved) == "tortoise.db", (
            f"unexpected redirect shape: {resolved!r}")
    else:
        assert _GUARD_MARKER not in resolved, (
            "the embedded-lane redirect leaked into a URI lane: "
            f"{resolved!r} — the guard's no-op guarantee is broken")


def test_the_guard_is_a_session_wide_redirect_not_a_single_call_shim():
    """The guard must hold for the WHOLE session, at the resolved-env level —
    a per-call shim would leave a module that reads the path at import time
    (or at collection) still pointed at the canonical store. The URI-lane
    branch pins the same no-op from the environment side."""
    redirect = os.environ.get("TORTOISE_DB_PATH") or ""
    if _sdk_binds_an_embedded_path():
        assert _GUARD_MARKER in redirect, (
            "the embedded lane must carry a TORTOISE_DB_PATH redirect for the "
            "whole session (conftest's import-time #4071 guard)")
        assert os.path.abspath(resolve_db_path()) == os.path.abspath(redirect), (
            "resolve_db_path() disagrees with the session redirect")
    else:
        assert _GUARD_MARKER not in redirect, (
            "the guard set a TORTOISE_DB_PATH in a URI lane — it must be a "
            f"no-op there, got {redirect!r}")


def test_the_guard_is_in_force_before_collection_runs():
    """The PLACEMENT pin: the redirect must already be in force when this
    module's BODY runs during COLLECTION — not merely by the time a test runs.

    Every other assertion in this file reads ``TORTOISE_DB_PATH`` at TEST
    time, so a guard moved into a session-scoped fixture satisfies all of them
    while installing the redirect *after* collection — and a module that
    constructs a bare SDK in its body (two do:
    ``tests/test_issue94_annotate_ep_batch.py``,
    ``tests/test_topic_summarization.py``) then recreates the canonical store,
    which is the regression #4071 exists to prevent.

    Mutation-verified: with conftest's guard moved into a session ``autouse``
    fixture doing the identical work, the three tests above stay GREEN and this
    one fails (the snapshot is empty because the module body ran first).
    """
    if _sdk_binds_an_embedded_path():
        assert _GUARD_MARKER in _REDIRECT_AT_COLLECTION, (
            "the #4071 guard is NOT in force at COLLECTION time "
            f"(TORTOISE_DB_PATH={_REDIRECT_AT_COLLECTION!r}) — a session "
            "fixture is structurally too late for a module-body writer, so "
            "the canonical store will be recreated during collection")
    else:
        assert _GUARD_MARKER not in _REDIRECT_AT_COLLECTION, (
            "the embedded-lane guard set a TORTOISE_DB_PATH before collection "
            f"in a URI lane — it must be a no-op there, got "
            f"{_REDIRECT_AT_COLLECTION!r}")
