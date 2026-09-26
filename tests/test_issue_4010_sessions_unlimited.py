"""#4010 — the flat 1000-session cap is removed: sessions are UNLIMITED for
every tier.

PROVENANCE (stated precisely, because the record matters). The flat 1000
max_sessions was an INHERITED CODE FALLBACK, never a ratified product cap,
and the reason is checkable rather than reconstructed: the plan that carried
it also designated its OWN canonical limits source, and that source has no
session field at all. `product/pricing.json` (canonical single source,
decision 1d: "product/pricing.json ... is canonical; pricing.md is
doc-generated from it") contains ZERO occurrences of "session" and no
sessions row in its tier table. What the plan recorded was KEEPING THE
EXISTING FALLBACK — as a fallback: `docs/epics/2026-08-07-tortoise-user-
journeys/05-plan.md:570` — "keep flat fallbacks (1000/1000) in v1 OR fold
into `ops_allowance` — decision: keep points/sessions flat in v1;
`ops_allowance` (write ops) is the billing metric", restated at `:598`
("points/sessions stay flat 1000/1000 in v1"). Keeping a fallback is not
ratifying the value, and `:18`'s "human gate #2 approved" reads in full "all
8 substeps, coherence CLEAN; human gate #2 approved 2026-08-07; decomposed
into #568-#578" — it approved the plan's coherence to decompose, not a
constant inside a tier table. The default pre-dates the #329 security commit
(`f6ca5ebdb`), whose scoping doc only instructed preserving the existing
resource in the shared helper (`docs/plans/scoping-329-problem.md:17` — a
refactor-safety instruction, not a cap ratification); it was the only org
limit flat across every tier and the only resource whose enforcement fell
back to a lenient constant when its key was missing. Because the session
count is monotonic (no retention window on user content), the dogfood org
crossed it and captured nothing for 43 consecutive days.

#4010 therefore REMOVES an unratified fallback that should never have been
enforcement (the 2026-09-19 correction on the issue withdraws the earlier
"recorded v1 decision / REOPEN" framing). It is **not** a reopen of a v1 cap
decision — nothing that RATIFIES a cap ever named it: no owner ruling, no
decision record, no `product/pricing.json` field. Approved docs do carry
1000 forward as a default (`git grep -n max_sessions -- '*.md'`); carrying a
default forward is the inheritance this docstring describes, not a
ratification. The P2-7 billing-metric choice (write-ops bills) is untouched.
Stale 1000-as-cost-bound framing elsewhere: #4052.

Removing it has two independent halves, and this module guards BOTH:

  * CODE — no flat constant, no lenient fallback, and a stored
    ``max_sessions`` is deliberately NOT honoured as a cap.
  * DATA — the stored rows are cleared, idempotently
    (``graph-scripts/clear_max_sessions_4010.py``).

The regression guard is the last class: it drives the REAL hosted capture
path (registry auth → resolve → gate) with a Team whose stored cap is 1000 and
1000 Sessions already present, and asserts the new session LANDS. It REDs if
either the cap constant returns or a stored value starts being honoured.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# graph-scripts/ is a hyphenated (namespace) dir — import the #4010 one-shot
# via path insert (AGENTS.md sibling-import convention; same as test_quota.py).
_GRAPH_SCRIPTS = str(Path(__file__).resolve().parent.parent / "graph-scripts")
if _GRAPH_SCRIPTS not in sys.path:
    sys.path.insert(0, _GRAPH_SCRIPTS)

# `test_guard` is reached as a MODULE attribute (not imported by name): a bare
# `test_guard` import would be COLLECTED by pytest as a test, since the helper's
# name matches the test pattern.
import clear_max_sessions_4010 as sweep  # noqa: E402
from clear_max_sessions_4010 import (  # noqa: E402
    _supabase_lane_refusal,
    clear_stored_max_sessions,
)

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.hosted_api import (  # noqa: E402,I001
    _org_limits_from_node,
    app,
    get_current_org,
)
from tortoise.quota import (  # noqa: E402
    _RESOURCE_LIMIT_KEYS,
    QuotaCheckError,
    enforce_org_limit,
    resolve_org_limits,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402
from tests._http_fixtures import patched_tortoise_sdk  # noqa: E402

#: The value the deleted constant used, and the value the dogfood org's rows
#: carried — reused as the stored cap the guard refuses to honour.
LEGACY_CAP = 1000


# ── CODE half 1: no constant, no lenient fallback ───────────────────────────


class TestNoSessionCapConstant:
    def test_quota_module_defines_no_session_cap_constant(self):
        """The constant is deleted, not renamed or relocated.

        Mutation this REDs: re-introduce `DEFAULT_MAX_SESSIONS = 1000` (any
        name would still fail the behavioural guards below; this one pins the
        literal deletion the issue asks for).
        """
        import tortoise.quota as quota
        assert not hasattr(quota, "DEFAULT_MAX_SESSIONS"), (
            "quota.DEFAULT_MAX_SESSIONS is back — the unapproved 1000-session "
            "cap must stay deleted (#4010)")

    def test_missing_max_sessions_key_is_fail_closed(self):
        """A MISSING key is fail-closed for every resource — sessions included.

        This is the #310 GAP-B rule sessions used to be the sole exception to.
        Mutation this REDs: restore the lenient fallback
        (`if resource == "sessions": limit = DEFAULT_MAX_SESSIONS`), which
        returns cleanly instead of raising.
        """
        limits = {
            "org_id": "team-4010-failclosed",
            "max_points": 10000,
            "max_api_keys": 2,
            "max_users": 1,
            "max_graphs": 1,
            # max_sessions deliberately ABSENT (not None — absent)
        }
        with pytest.raises(QuotaCheckError, match="max_sessions"):
            enforce_org_limit(limits, "sessions")

    def test_explicit_none_max_sessions_skips_enforcement(self):
        """An explicit None is UNLIMITED and skips — the shipped precedent
        (#683; pricing.json sets max_graphs_per_team null for pro/team)."""
        limits = {
            "org_id": "team-4010-unlimited",
            "max_points": 10000,
            "max_api_keys": 2,
            "max_users": 1,
            "max_graphs": 1,
            "max_sessions": None,
        }
        enforce_org_limit(limits, "sessions")  # must not raise


class TestResolvedLimitsContract:
    """The contract the fallback removal made load-bearing: EVERY value of
    `_RESOURCE_LIMIT_KEYS` must be PRESENT in a resolved limits dict. A present
    `None` means unlimited; a MISSING key is fail-closed (a 500 on a write).
    ~15 test doubles had to be fixed when the lenient fallback went away, so
    pin the shape at the resolvers rather than discovering it per-fixture.
    """

    def test_org_limits_from_node_carries_every_resource_key(self):
        limits = _org_limits_from_node({"id": "t-contract", "tier": "free"})
        missing = [k for k in _RESOURCE_LIMIT_KEYS.values() if k not in limits]
        assert not missing, (
            f"resolved limits dict is missing {missing} — a missing key is "
            "fail-closed in enforce_org_limit (#310 GAP-B / #4010)")

    def test_resolve_org_limits_carries_every_resource_key(
            self, tmp_path, monkeypatch):
        db_path = os.path.join(tmp_path, "contract4010.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        reg = TortoiseSDK(db_path, namespace="registry")
        try:
            team = reg.org_create(f"contract-{os.urandom(4).hex()}")
            limits = resolve_org_limits(team["id"])
            missing = [k for k in _RESOURCE_LIMIT_KEYS.values()
                       if k not in limits]
            assert not missing, f"resolve_org_limits is missing {missing}"
            # …and sessions is the resource with no cap at all.
            assert limits["max_sessions"] is None
        finally:
            reg.close()


# ── CODE half 2: a stored value is not honoured ─────────────────────────────


class TestStoredValueIsNotHonoured:
    def test_org_limits_from_node_ignores_stored_cap(self):
        """`_org_limits_from_node` (create_graph / invite_to_org lane) resolves
        sessions to None even when the node carries a finite stored value.

        Mutation this REDs: `max_sessions: ms if ms is not None else None`.
        """
        node = {"id": "t4010", "tier": "free", "max_sessions": LEGACY_CAP}
        assert _org_limits_from_node(node)["max_sessions"] is None

    def test_resolve_org_limits_ignores_stored_cap(self, tmp_path, monkeypatch):
        """The registry resolver ignores `t.max_sessions` — the trap the issue
        names (a stored 1000 surviving the constant's deletion).

        Mutation this REDs: honing the stored value in `resolve_org_limits`.
        """
        db_path = os.path.join(tmp_path, "stored4010.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        reg = TortoiseSDK(db_path, namespace="registry")
        try:
            team = reg.org_create(f"stored-{os.urandom(4).hex()}")
            tid = team["id"]
            reg._get_registry().query(
                "MATCH (t:Team {id:$id}) SET t.max_sessions = $ms",
                params={"id": tid, "ms": LEGACY_CAP},
            )
            assert reg.org_get(tid)["max_sessions"] == LEGACY_CAP  # stored...
            limits = resolve_org_limits(tid)
            assert limits["max_sessions"] is None, (  # ...and NOT honoured
                f"a stored max_sessions={LEGACY_CAP} was honoured as a cap — "
                "the #4010 trap is open")
        finally:
            reg.close()


class TestSweepSafetyGuards:
    """The one-shot sweep DELETES a property irreversibly, so its two guards
    are load-bearing: the #669 resurrection refusal (opening the registry
    namespace in Supabase mode auto-creates the graph the flip deleted) and
    the resolved-graph `--yes` gate."""

    def test_supabase_mode_refuses(self, monkeypatch):
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
        assert _supabase_lane_refusal() is not None

    def test_registry_mode_proceeds(self, monkeypatch):
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
        assert _supabase_lane_refusal() is None

    def test_undeterminable_mode_fails_closed(self, monkeypatch):
        """An import failure must REFUSE, not proceed: `tortoise.sdk` imports
        `supabase_control` lazily, so a broken import still lets the sweep
        reach the registry namespace and write."""
        import sys
        monkeypatch.setitem(sys.modules, "tortoise.supabase_control", None)
        refusal = _supabase_lane_refusal()
        assert refusal is not None and "could not be determined" in refusal

    def test_guard_requires_yes_on_the_resolved_graph(self):
        """The registry graph is never test-prefixed, so a real write must
        always pass `--yes` — the guard is fed the SDK-resolved name, not the
        URI path (a `tortoise_test_*` URI path used to auto-approve it)."""
        with pytest.raises(SystemExit):
            sweep.test_guard("registry_control_plane", yes=False)
        sweep.test_guard("registry_control_plane", yes=True)  # explicit consent
        sweep.test_guard("tortoise_test_4010")  # test-scoped, no consent


# ── DATA half: clearing the stored rows, idempotently ───────────────────────


class TestStoredValuesCleared:
    def test_clear_is_idempotent_and_reports_rows(self, tmp_path, monkeypatch):
        """The sweep NULLs stored caps; a second run is a no-op."""
        db_path = os.path.join(tmp_path, "clear4010.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        sdk = TortoiseSDK(db_path, namespace="registry")
        try:
            reg = sdk._get_registry()
            for i in range(2):
                team = sdk.org_create(f"clear-{i}-{os.urandom(4).hex()}")
                reg.query(
                    "MATCH (t:Team {id:$id}) SET t.max_sessions = $ms",
                    params={"id": team["id"], "ms": LEGACY_CAP},
                )
            # --dry-run reports the rows it would clear, without writing
            dry = clear_stored_max_sessions(reg, dry_run=True)
            assert dry["found"] == 2 and dry["cleared"] == 0
            # NON-VACUOUS: the dry run must NAME the rows (the operator's only
            # pre-flight before an irreversible property delete).
            assert len(dry["teams"]) == 2, dry["teams"]
            assert all(v == LEGACY_CAP for _, v in dry["teams"])
            # nothing written yet
            assert reg.query(
                "MATCH (t:Team) WHERE t.max_sessions IS NOT NULL "
                "RETURN count(t)").result_set[0][0] == 2
            # the sweep clears them and names each row
            sweep = clear_stored_max_sessions(reg)
            assert sweep["found"] == 2 and sweep["cleared"] == 2
            assert all(v == LEGACY_CAP for _, v in sweep["teams"])
            assert reg.query(
                "MATCH (t:Team) WHERE t.max_sessions IS NOT NULL "
                "RETURN count(t)").result_set[0][0] == 0
            # idempotent: a second run finds nothing
            again = clear_stored_max_sessions(reg)
            assert again["found"] == 0 and again["cleared"] == 0
        finally:
            sdk.close()


# ── EFFECT: the hosted capture path stores a session past the stored cap ────


@pytest.fixture
def hosted_org_past_cap(monkeypatch):
    """A real registry org: stored `max_sessions=1000`, 1000 Sessions present,
    and a real `tt_` key — so the capture exercises the REAL auth + resolver +
    gate chain rather than a stubbed limits dict."""
    # monkeypatch (not hand-rolled os.environ edits) so EVERY var — including
    # SUPABASE_URL and TORTOISE_DB_PATH — is restored on failure too.
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "hosted4010.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        with patched_tortoise_sdk(db_path):
            from fastapi.testclient import TestClient

            from tortoise.auth import hash_api_key
            reg = TortoiseSDK(db_path, namespace="registry")
            tenant = None
            try:
                team = reg.org_create(f"cap-{os.urandom(4).hex()}")
                tid = team["id"]
                # The registry auth lane resolves :APIKey nodes (lookup_hash
                # prefix scan) — mint one whose plaintext this test holds.
                key = "tt_4010" + uuid.uuid4().hex
                reg._get_registry().query(
                    "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, "
                    "key_prefix:$kp, created_by:$cb})",
                    params={"id": "key-4010", "tid": tid,
                            "kh": hash_api_key(key), "kp": key[:10],
                            "cb": "test"},
                )
                # The stored cap the issue's trap warns about.
                reg._get_registry().query(
                    "MATCH (t:Team {id:$id}) SET t.max_sessions = $ms",
                    params={"id": tid, "ms": LEGACY_CAP},
                )
                tenant = TortoiseSDK(db_path, namespace=tid)
                # 1000 Sessions already present — one round trip.
                tenant._get_proj().g.query(
                    "UNWIND range(1, 1000) AS i "
                    "CREATE (:Session {id: 'seed_' + toString(i)})")
                with TestClient(app) as client:
                    yield client, tid, key, tenant
            finally:
                # runs on failure too (a leaked projection keeps the embedded
                # store busy for the rest of the session).
                if tenant is not None:
                    tenant.close()
                reg.close()


class TestHostedCapturePastStoredCap:
    def test_capture_over_stored_cap_lands(self, hosted_org_past_cap):
        """EFFECT, not edit: the session that would previously have been
        refused is now STORED — 200, no 402, count 1000 → 1001.

        Mutation this REDs: honour a stored value as a cap in any resolver
        on this path (`int(ms) if ms is not None else None`) — the gate then
        sees count 1000 >= limit 1000 and returns 402.
        """
        client, _tid, key, tenant = hosted_org_past_cap
        assert _session_count(tenant) == LEGACY_CAP  # fixture is AT the old cap

        r = client.post(
            "/v1/sessions",
            json={
                "conversation": [{"role": "user", "content": "session 1001"}],
                "session_id": "past-cap-4010",
                "harness": "pi",
            },
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code != 402, (
            "the removed cap still refuses capture: "
            f"{r.status_code} {r.text}")
        assert r.status_code == 200, r.text
        # The session LANDED (not merely "no error").
        assert _session_count(tenant) == LEGACY_CAP + 1
        landed = tenant._get_proj().g.query(
            "MATCH (s:Session {id:'past-cap-4010'}) RETURN count(s)",
        ).result_set[0][0]
        assert landed == 1, "the capture returned 200 but stored no Session"

    def test_real_auth_lane_resolves_sessions_unlimited(self, hosted_org_past_cap):
        """The SAME chain the capture used — `get_current_org` resolved through
        the real registry key — yields `max_sessions is None`."""
        import asyncio
        from unittest.mock import MagicMock

        _client, _tid, key, _tenant = hosted_org_past_cap
        request = MagicMock()
        request.url.path = "/v1/sessions"
        request.headers = {"Authorization": f"Bearer {key}"}
        request.state = MagicMock()
        org = asyncio.run(get_current_org(request))
        assert org["max_sessions"] is None


def _session_count(tenant) -> int:
    return int(tenant._get_proj().g.query(
        "MATCH (s:Session) RETURN count(s)").result_set[0][0])
