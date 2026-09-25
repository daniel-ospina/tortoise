"""#3553 — onboarding-state compare-and-set (docker lane, REAL persistence).

The bug: the OPERATIONAL branch of ``_update_onboarding_state`` was a whole-dict
read-modify-write — ``_get_onboarding_state`` → mutate → ``_write_onboarding_state``.
Any key a concurrent writer committed between the READ and the WRITE is lost,
and the loss is user-visible: a dropped receipt flips the dashboard back to
``install-pending``/``waiting`` for a team that DID capture; a dropped cursor
regresses the walk.

These tests run against the REAL registry persistence path (the docker-lane
FalkorDB test matrix graph, ``TORTOISE_DB_URI``), never a stub — a stub has no
RMW to lose, which would make the claim a tautology. The negative control
executes the PRE-#3553 body (read → mutate → whole-dict write) UN-STUBBED on
the same real store and shows the same interleaving loses the other writer's
key.

Key-pair parametrization maps the issue's ``(pledges)``, ``(cursor, receipts)``
and ``(cursor, cursor)`` slots onto REGISTERED operational keys (``pledges`` is
not a state key):

  - ``pledges``       → ``install_probe_pi`` (one key, contested by both writers)
  - ``cursor+receipts``→ ``github_index_cursor`` + ``session_capture_receipt_claude``
  - ``cursor+cursor`` → ``github_index_cursor`` written by both writers
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY",
    "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=",
)

import contextlib
import threading
import uuid

import pytest

# docker-lane gate (epic #1647 P4): URI-less embedded legs cannot run these
# server-mode graph assertions — skip cleanly instead of failing.
from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip(
        "docker-lane onboarding-state CAS tests require TORTOISE_DB_URI "
        "(tier-2 embedded legs skip)",
        allow_module_level=True,
    )

from tortoise.hosted_api import (
    _STATE_VERSION_KEY,
    _cas_write_onboarding_state,
    _get_onboarding_state,
    _make_sdk,
    _read_onboarding_state_and_version,
    _update_onboarding_state,
    _write_onboarding_state,
)

CURSOR = "github_index_cursor"
RECEIPT = "session_capture_receipt_claude"
PROBE = "install_probe_pi"

# id, writer-A key, writer-B key. Same key ⇒ last-writer-wins on ONE key;
# distinct keys ⇒ BOTH must survive (the actual lost-update).
_KEY_CASES = [
    pytest.param(PROBE, PROBE, id="pledges-lone-key"),
    pytest.param(CURSOR, RECEIPT, id="cursor+receipts"),
    pytest.param(CURSOR, CURSOR, id="cursor+cursor"),
]
# The negative control only demonstrates a LOST KEY where the two writers touch
# DIFFERENT keys — a same-key race legitimately ends last-writer-wins.
_NEGATIVE_CASES = [
    pytest.param(PROBE, RECEIPT, id="pledges+receipts"),
    pytest.param(CURSOR, RECEIPT, id="cursor+receipts"),
]

_ROUNDS = 10


@pytest.fixture(autouse=True)
def _registry_lane(monkeypatch):
    """Pin the lane to the registry leg so exported Supabase creds cannot
    silently route these writes to a different backend."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)


def _provision(org_id: str) -> None:
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": org_id, "st": "{}"},
    )


@pytest.fixture
def org() -> str:
    """A real Team node (the registry state writer is a MATCH...SET — a silent
    no-op without the node)."""
    org_id = f"cas3553-{uuid.uuid4().hex[:10]}"
    _provision(org_id)
    yield org_id
    with contextlib.suppress(Exception):  # shared graph — best-effort cleanup
        _make_sdk(namespace="registry")._get_registry().query(
            "MATCH (t:Team {id:$id}) DETACH DELETE t", params={"id": org_id})


def _pre_fix_rmw(org_id: str, fields: dict) -> None:
    """The EXACT pre-#3553 OPERATIONAL branch body — real read, real whole-dict
    write, no guard. Deliberately NOT stubbed: this is the implementation whose
    lost update the CAS removes."""
    state = _get_onboarding_state(org_id)
    for k, v in fields.items():
        state[k] = v
    _write_onboarding_state(org_id, state)


def test_cas_version_advances_and_never_leaks(org):
    """The guard field exists, advances once per applied write, is stored as
    the Team node property, and is NEVER part of the state a reader sees."""
    assert "state_version" not in _get_onboarding_state(org)
    _update_onboarding_state(org, **{CURSOR: "c1"})
    _, v1 = _read_onboarding_state_and_version(org)
    assert v1 == 1, v1
    _update_onboarding_state(org, **{RECEIPT: "r1"})
    _, v2 = _read_onboarding_state_and_version(org)
    assert v2 == 2, v2

    stored = _get_onboarding_state(org)
    assert stored[CURSOR] == "c1"
    assert stored[RECEIPT] == "r1"
    assert "state_version" not in stored

    rows = _make_sdk(namespace="registry")._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.state_version",
        params={"id": org},
    ).result_set
    assert rows == [[2]], rows


def test_stale_guard_is_refused(org):
    """The guarded write itself: a stale expected version applies NOTHING; the
    current version applies exactly once."""
    _update_onboarding_state(org, **{CURSOR: "c1"})
    state, version = _read_onboarding_state_and_version(org)
    assert version == 1

    # A racing writer advances the epoch...
    assert _update_onboarding_state(org, **{RECEIPT: "r-racing"})

    # ...so a CAS at the stale version must be refused, and must not write.
    stale = dict(state)
    stale[CURSOR] = "c-stale"
    assert _cas_write_onboarding_state(org, stale, version) is False
    assert _get_onboarding_state(org)[CURSOR] == "c1"

    # A CAS at the current version applies and advances.
    fresh, current = _read_onboarding_state_and_version(org)
    fresh[CURSOR] = "c-fresh"
    assert _cas_write_onboarding_state(org, fresh, current) is True
    assert _get_onboarding_state(org)[CURSOR] == "c-fresh"
    _, advanced = _read_onboarding_state_and_version(org)
    assert advanced == current + 1


@pytest.mark.parametrize(("key_a", "key_b"), _KEY_CASES)
def test_concurrent_writers_lose_no_write(org, key_a, key_b):
    """REAL threads against the REAL store.

    Each writer performs one ``_update_onboarding_state`` per round, gated by a
    barrier so the read-modify-write windows overlap. Because every applied CAS
    write advances ``state_version`` by exactly one, the final version is an
    EXACT count of applied writes: ``2 * _ROUNDS`` means no writer's call was
    silently dropped. On top of that, distinct keys must both hold their last
    value; a contested single key must hold one of the two last values.
    """
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def writer(prefix: str, key: str | None) -> None:
        try:
            for i in range(_ROUNDS):
                barrier.wait(timeout=60)
                if key is not None:
                    _update_onboarding_state(org, **{key: f"{prefix}-{i}"})
        except BaseException as exc:  # surfaced after join
            failures.append(exc)

    threads = [
        threading.Thread(target=writer, args=("A", key_a)),
        threading.Thread(target=writer, args=("B", key_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not failures, failures
    assert all(not t.is_alive() for t in threads), "writer thread wedged"

    _, version = _read_onboarding_state_and_version(org)
    assert version == 2 * _ROUNDS, (
        f"{2 * _ROUNDS} writes were issued but only {version} applied — a "
        "write was lost")

    final = _get_onboarding_state(org)
    last_a = f"A-{_ROUNDS - 1}"
    last_b = f"B-{_ROUNDS - 1}"
    if key_a == key_b:
        assert final[key_a] in (last_a, last_b), (key_a, final[key_a])
    else:
        assert final[key_a] == last_a, (key_a, final[key_a])
        assert final[key_b] == last_b, (key_b, final[key_b])


def test_cas_retries_and_merges_under_forced_interleave(org, monkeypatch):
    """Deterministic scheduling: writer A's CAS loop reads the REAL state, then
    writer B commits a REAL write before A's guarded write runs. A's guard must
    mismatch, the loop must re-read (merging B's key) and re-apply only A's
    field — both keys survive.

    The hook injects a SCHEDULING point around the real read; the persistence
    path itself is not stubbed.
    """
    import tortoise.hosted_api as ha

    real_read = ha._read_onboarding_state_and_version
    reads = {"n": 0}

    def _racing_read(org_id: str):
        state, version = real_read(org_id)
        if reads["n"] == 0:
            reads["n"] += 1  # guard BEFORE B's write → no recursion
            _update_onboarding_state(org_id, **{RECEIPT: "r-from-B"})
        return state, version

    monkeypatch.setattr(ha, "_read_onboarding_state_and_version", _racing_read)
    _update_onboarding_state(org, **{CURSOR: "c-from-A"})

    assert reads["n"] >= 1
    final = _get_onboarding_state(org)
    assert final[CURSOR] == "c-from-A"
    assert final[RECEIPT] == "r-from-B", (
        "the CAS retry discarded the concurrent writer's receipt")
    # A's first attempt was refused, so the loop must have re-read.
    _, version = _read_onboarding_state_and_version(org)
    assert version == 2, version


@pytest.mark.parametrize(("key_a", "key_b"), _NEGATIVE_CASES)
def test_negative_control_pre_fix_rmw_loses_the_concurrent_key(
        org, key_a, key_b):
    """NEGATIVE CONTROL — the un-stubbed PRE-#3553 implementation.

    Deterministic interleave on the REAL store, with no CAS anywhere:
      A reads(key_a)  →  B commits(key_b)  →  A writes its STALE whole dict.
    The pre-fix body must lose B's key. (The guard-removing mutation: if this
    assertion ever stops holding, the CAS is no longer what makes
    ``test_concurrent_writers_lose_no_write`` green and that test proves
    nothing.)
    """
    a_state = _get_onboarding_state(org)
    a_state[key_a] = "A-stale"

    _pre_fix_rmw(org, {key_b: "B-concurrent"})   # B: full real RMW

    _write_onboarding_state(org, a_state)        # A: stale whole-dict write

    final = _get_onboarding_state(org)
    assert final[key_a] == "A-stale"
    assert final[key_b] != "B-concurrent", (
        "negative control FAILED to reproduce the pre-#3553 lost update — "
        f"{key_b!r} survived as {final[key_b]!r}; the concurrency test would "
        "pass without the CAS")


# ── the read-path materialization (node present, jsonb UNSET) ──────────────
# A node created without an `onboarding_state` property (the common SDK /
# provision creation shape) used to have its materialization done by a BARE
# whole-dict `_write_onboarding_state` on the READ path — which clobbered a
# concurrent CAS commit and left `state_version` lying about the content.

@pytest.fixture
def org_unset() -> str:
    """A real Team node with NO `onboarding_state` property."""
    org_id = f"cas3553-null-{uuid.uuid4().hex[:10]}"
    _make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id})", params={"id": org_id})
    yield org_id
    with contextlib.suppress(Exception):
        _make_sdk(namespace="registry")._get_registry().query(
            "MATCH (t:Team {id:$id}) DETACH DELETE t", params={"id": org_id})


def test_unset_property_materializes_through_the_guarded_cas(org_unset):
    """The read-path materialization advances the CAS epoch (it is a guarded
    write), and a later concurrent-style write survives a subsequent read."""
    state = _get_onboarding_state(org_unset)
    assert state[CURSOR] is None
    assert "state_version" not in state
    _, version = _read_onboarding_state_and_version(org_unset)
    assert version == 1, version  # materialization was a CAS write

    _update_onboarding_state(org_unset, **{RECEIPT: "r1"})
    assert _get_onboarding_state(org_unset)[RECEIPT] == "r1"
    _, version2 = _read_onboarding_state_and_version(org_unset)
    assert version2 == 2, version2


def test_read_path_materialization_never_clobbers_a_concurrent_write(
        org_unset, monkeypatch):
    """Deterministic interleave: a concurrent writer commits while the read
    path is about to materialize. The guarded materialization is REFUSED, the
    reader re-reads, and the concurrent receipt survives."""
    import tortoise.hosted_api as ha

    real_cas = ha._cas_write_onboarding_state
    fired = {"n": 0}

    def _racing_cas(org_id, state, expected):
        if expected == 0 and fired["n"] == 0:
            fired["n"] += 1  # guard BEFORE the concurrent write
            _update_onboarding_state(org_id, **{RECEIPT: "r-concurrent"})
        return real_cas(org_id, state, expected)

    monkeypatch.setattr(ha, "_cas_write_onboarding_state", _racing_cas)
    _get_onboarding_state(org_unset)  # read-path materialization
    assert fired["n"] == 1

    final = _get_onboarding_state(org_unset)
    assert final[RECEIPT] == "r-concurrent", final
    _, version = _read_onboarding_state_and_version(org_unset)
    assert version == 1, version  # only the concurrent writer applied


# ── the Supabase leg (the fly.toml production control plane) ───────────────
# The real PostgREST path is not reachable from the local lanes, so these
# cases run against a local double that models the ONLY PostgREST behaviour
# the CAS depends on: a PATCH applies IFF its version guard matches, and
# `return=representation` yields an EMPTY row list on a refused guard. The
# shared `tests/fake_control_plane.py` double ALSO resolves a `base->>key` path
# selector as of this change, so the behaviour is exercised there too (the W6
# onboarding lane drives a version 0→1→2 sequence through it); this local
# double is retained because these cases need to RECORD the exact filter list
# and drive a refused guard deterministically. The guard encoding this
# exercises is the first `->>` path filter in the repo.

class _FakeSupabaseControlPlane:
    """In-memory `organizations` table with a faithful jsonb-path guard."""

    def __init__(self, rows: dict | None = None):
        self.rows = rows if rows is not None else {}
        self.patches: list[dict] = []

    def query(self, table, *, select=None, filters=None, method="GET",
              json_body=None, **_kw):
        assert table == "organizations"
        rid = next((v for c, op, v in (filters or [])
                    if c == "id" and op == "eq"), None)
        row = self.rows.get(rid)
        if method == "GET":
            return [] if row is None else [{"id": rid, **row}]
        assert method == "PATCH", method
        self.patches.append({"id": rid, "filters": list(filters or []),
                             "body": dict(json_body or {})})
        if row is None:
            return []  # PATCH on a missing row — PostgREST returns no rows
        for col, op, val in (filters or []):
            if col == f"onboarding_state->>{_STATE_VERSION_KEY}":
                stored = (row.get("onboarding_state") or {}).get(
                    _STATE_VERSION_KEY)
                if op == "is" and val is None:
                    if stored is not None:
                        return []
                elif op == "eq":
                    if stored != int(val):
                        return []
                else:
                    raise AssertionError((col, op, val))
            elif not (col == "id" and op == "eq"):
                raise AssertionError(f"unmodelled filter {col!r}")
        row.update(json_body or {})
        return [{"id": rid}]


@pytest.fixture
def supa(monkeypatch):
    """Point the Supabase branch at the local double."""
    import tortoise.supabase_control as sc

    cp = _FakeSupabaseControlPlane(
        {"org-supa": {"onboarding_state": {}}})
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


def test_supabase_leg_guards_first_write_with_is_null_then_eq(supa):
    """First write (no stored version) guards on IS NULL; the next guards on
    the stored epoch, and both keys survive."""
    from tortoise.hosted_api import _write_jsonb_fields_cas

    _write_jsonb_fields_cas("org-supa", {CURSOR: "c1"})
    _write_jsonb_fields_cas("org-supa", {RECEIPT: "r1"})

    assert supa.patches[0]["filters"][-1] == (
        f"onboarding_state->>{_STATE_VERSION_KEY}", "is", None)
    assert supa.patches[1]["filters"][-1] == (
        f"onboarding_state->>{_STATE_VERSION_KEY}", "eq", "1")
    stored = supa.rows["org-supa"]["onboarding_state"]
    assert stored[CURSOR] == "c1"
    assert stored[RECEIPT] == "r1"
    assert stored[_STATE_VERSION_KEY] == 2


def test_supabase_leg_refuses_a_stale_guard(supa):
    from tortoise.hosted_api import _write_jsonb_fields_cas
    from tortoise.supabase_control import cas_update_onboarding_state

    _write_jsonb_fields_cas("org-supa", {CURSOR: "c1"})
    assert cas_update_onboarding_state(
        supa, "org-supa", {CURSOR: "stale"}, 0) is False
    assert supa.rows["org-supa"]["onboarding_state"][CURSOR] == "c1"


def test_supabase_leg_retries_and_merges_under_forced_interleave(
        supa, monkeypatch):
    import tortoise.hosted_api as ha

    real_read = ha._read_onboarding_state_and_version
    reads = {"n": 0}

    def _racing_read(org_id):
        state, version = real_read(org_id)
        if reads["n"] == 0:
            reads["n"] += 1
            # B — the concurrent writer, the same CAS loop (NOT
            # `_update_onboarding_state`: its FLOW projection would touch a
            # real graph).
            ha._write_jsonb_fields_cas(org_id, {RECEIPT: "r-from-B"})
        return state, version

    monkeypatch.setattr(ha, "_read_onboarding_state_and_version", _racing_read)
    ha._write_jsonb_fields_cas("org-supa", {CURSOR: "c-from-A"})

    stored = supa.rows["org-supa"]["onboarding_state"]
    assert stored[CURSOR] == "c-from-A"
    assert stored[RECEIPT] == "r-from-B"
    assert stored[_STATE_VERSION_KEY] == 2
