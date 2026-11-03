"""#8906 — the REST points gate must count the graph the request WRITES.

The defect (P0-blocker, verified against the deployed build): every REST
points door called ``_check_org_limit(org, "points")``, whose
``enforce_org_limit(org, resource)`` took NO sdk, so ``_count_resource`` fell
to ``_make_sdk(namespace=org_id)`` — the org's DEFAULT graph ``org_{org_id}``
— while a graph-bound key writes the key's ``graph_namespace``
(``_data_graph_name`` → ``org_{org_id}_{gid}``). The two never agree for a
per-graph credential, by construction, so:

- a per-graph key with an EMPTY write graph is refused (402) because its
  org's default graph is full — the false refusal that froze every REST write
  once the counted graph reached its cap; and
- a per-graph key whose OWN graph is full is ADMITTED (the other half of the
  same divergence — the cap does not bite on the graph that grows).

The house rule this restores is `mcp_server.py`: *"Count on the SAME org SDK
the tool writes to (identical connection), so the count and the write can
never target different databases."*

Both halves are asserted here, so neither "make it admit" nor "make it refuse"
alone can turn the suite green:

- ``test_default_graph_full_write_graph_empty_is_ADMITTED`` fails RED under the
  old behaviour (402 from the wrong graph);
- ``test_write_graph_full_default_empty_is_REFUSED`` fails RED under the old
  behaviour (200 — the cap missed the graph that grows).

⚠️ The cap is the Team's stored ``max_points`` (the same field production
sets to 25000 from ``pricing.json`` solo.max_graph_nodes), held at 1–2 here so
one Point fills a graph. No limit value is changed and the graph's size is the
only axis under test.
"""
from __future__ import annotations

import contextlib
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise.auth import hash_api_key
from tortoise.quota import count_org_usage

#: A per-graph key with write access (``_require_scope`` needs graphs:write).
_WRITE_SCOPES = ["graphs:read", "graphs:write"]


class _Env:
    """One seeded registry + tenant graph pair on a shared temp embedded DB."""

    def __init__(self, reg, tid, write_graph, tc):
        self.reg = reg
        self.tid = tid
        self.write_graph = write_graph
        self.tc = tc

    # ── graph identities ────────────────────────────────────────────────
    @property
    def write_ns(self) -> str:
        return self.write_graph["namespace"]

    @property
    def default_ns(self) -> str:
        """The DEFAULT graph a graph-bound key must never be counted against."""
        return f"org_{self.tid}"

    # ── measures ───────────────────────────────────────────────────────
    def counted(self, graph_name: str) -> int:
        """Counted nodes in ONE named graph — the number the gate compares."""
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(graph_name=graph_name)
        try:
            return count_org_usage(self.tid, "points", sdk=sdk)
        finally:
            sdk.close()

    def seed(self, graph_name: str, name: str) -> None:
        """Write ONE counted Point into a named graph (direct, no HTTP)."""
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(graph_name=graph_name)
        try:
            sdk.create_point(name, content=f"{name} content")
        finally:
            sdk.close()

    def mint_key(self, *, graph_id: str | None = None,
                 scopes: list[str] | None = _WRITE_SCOPES) -> str:
        """A real APIKey row (the auth chain resolves the org dict from it)."""
        token = "tk_" + uuid.uuid4().hex
        self.reg._get_registry().query(
            "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:'8906', graph_id:$gid, "
            "scopes:$scopes, delegation_depth:$dd})",
            params={"id": f"k-{uuid.uuid4().hex[:8]}", "tid": self.tid,
                    "kh": hash_api_key(token), "kp": token[:10],
                    "gid": graph_id, "scopes": scopes or [], "dd": None},
        )
        return token

    def headers(self, *, graph_bound: bool = True) -> dict:
        token = self.mint_key(
            graph_id=self.write_graph["graph_id"] if graph_bound else None)
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(tmp_path):
    """Factory: ``env(max_points=N)`` seeds a team, its DEFAULT graph and one
    CUSTOM graph (the write graph a per-graph key binds).

    Mirrors ``tests/test_tenancy_spine.py``'s seeding (registry Spine + key
    rows) — the app-layer tenancy path is the real one, not an override, so
    the org dict under test carries the SAME ``graph_namespace`` production
    resolves.
    """
    import tortoise.hosted_api as ha_mod

    stack = contextlib.ExitStack()
    made: list[_Env] = []

    def _make(*, max_points: int) -> _Env:
        db_path = os.path.join(str(tmp_path), f"gate-{len(made)}.db")
        stack.enter_context(patched_tortoise_sdk(db_path))
        reg = ha_mod._make_sdk(namespace="registry")
        tid = f"gate8906-{len(made)}-{uuid.uuid4().hex[:6]}"
        # max_points is the SAME stored field get_current_org turns into the
        # org dict's `max_points` — the cap the gate compares `used` against.
        reg._get_registry().query(
            "CREATE (t:Team {id:$id, tier:'pro', max_graphs:5, "
            "max_api_keys:20, max_points:$mp, graph_name:$gn})",
            params={"id": tid, "mp": max_points, "gn": f"org_{tid}"},
        )
        reg._graph_create(tid, "default", kind="default")
        write_graph = reg._graph_create(tid, "write-8906", kind="custom")
        made.append(_Env(reg, tid, write_graph, TestClient(ha_mod.app)))
        return made[-1]

    try:
        yield _make
    finally:
        stack.close()


# ── the discriminating arms ─────────────────────────────────────────────────


def test_default_graph_full_write_graph_empty_is_ADMITTED(env):
    """RED under the old behaviour — the false refusal that froze REST writes.

    Cap 1; the DEFAULT graph holds 1 counted Point; the per-graph key's WRITE
    graph is empty. The old gate counted ``org_{tid}`` (1 >= 1) and answered
    402 for a graph it never writes. The fix counts the write graph (0 < 1)
    and admits the write.
    """
    e = env(max_points=1)
    e.seed(e.default_ns, "default-at-cap")
    assert e.counted(e.default_ns) == 1, "setup: default graph must be AT cap"
    assert e.counted(e.write_ns) == 0, "setup: the write graph must be empty"

    r = e.tc.post("/v1/points", json={"content": "8906 admitted"},
                  headers=e.headers())
    assert r.status_code in (200, 201), (
        "a graph-bound key was refused against the org DEFAULT graph it never "
        f"writes (#8906): {r.status_code} {r.text[:300]}")
    # The write went to the key's OWN graph, and the default graph is untouched
    # (so the admission is not a widen onto the counted graph).
    assert e.counted(e.write_ns) == 1, "the admitted write did not land"
    assert e.counted(e.default_ns) == 1, "the default graph was written"


def test_write_graph_full_default_empty_is_REFUSED(env):
    """RED under the old behaviour — the cap missed the graph that grows.

    Cap 1; the DEFAULT graph is empty and the key's WRITE graph already holds
    1 counted Point. The old gate counted ``org_{tid}`` (0 < 1) and admitted a
    write that takes the key's real graph over its cap. The fix must refuse it
    — the gate is not weakened, it is re-pointed.
    """
    e = env(max_points=1)
    e.seed(e.write_ns, "write-graph-at-cap")
    assert e.counted(e.write_ns) == 1, "setup: write graph must be AT cap"
    assert e.counted(e.default_ns) == 0, "setup: default graph must be empty"

    r = e.tc.post("/v1/points", json={"content": "8906 refused"},
                  headers=e.headers())
    assert r.status_code == 402, (
        "a graph-bound key's FULL OWN graph must still be refused — the "
        f"re-point must not weaken the gate (#8906): {r.status_code} "
        f"{r.text[:300]}")
    detail = r.json().get("detail")
    assert isinstance(detail, dict), detail
    assert detail.get("used") == 1, detail
    assert detail.get("limit") == 1, detail
    assert e.counted(e.write_ns) == 1, "a refused write must not land"


def test_objects_and_subjects_doors_count_the_write_graph(env):
    """The /v1/objects and /v1/subjects doors share the defect — both counted
    the default graph while ``sdk.create_object``/``create_subject`` write the
    key's graph, and both are ``resource="points"`` doors (#1911)."""
    e = env(max_points=2)
    e.seed(e.default_ns, "default-a")
    e.seed(e.default_ns, "default-b")
    assert e.counted(e.default_ns) == 2, "setup: default graph must be AT cap"
    assert e.counted(e.write_ns) == 0, "setup: the write graph must be empty"

    h = e.headers()
    for path, body in (("/v1/objects", {"name": "8906-object"}),
                       ("/v1/subjects", {"name": "8906-subject"})):
        r = e.tc.post(path, json=body, headers=h)
        assert r.status_code in (200, 201), (
            f"{path} refused a graph-bound key against the default graph "
            f"(#8906): {r.status_code} {r.text[:300]}")
    assert e.counted(e.write_ns) == 2, (
        "the two admitted writes must BOTH land in the key's own graph")
    assert e.counted(e.default_ns) == 2, "the default graph was written"


def test_commit_door_counts_the_write_graph(env):
    """``commit_session``'s gate runs inside ``_commit_sync`` — on a WORKER
    thread, far from the ``sdk`` the handler opened. It counted the default
    graph too (#8906)."""
    from tests.test_commit_endpoint import _finalize, _raw_payload

    e = env(max_points=1)
    e.seed(e.default_ns, "default-at-cap")
    assert e.counted(e.default_ns) == 1, "setup: default graph must be AT cap"
    assert e.counted(e.write_ns) == 0, "setup: the write graph must be empty"

    raw = _raw_payload(0, session_id="s8906", points=[], entities=[])
    r = e.tc.post("/v1/sessions/commit", json=_finalize(raw),
                  headers=e.headers())
    assert r.status_code == 200, (
        "the commit door refused a graph-bound key against the org DEFAULT "
        f"graph (#8906): {r.status_code} {r.text[:300]}")
    # The commit's own writes are all excluded from the points counter
    # (:CommitRecord/:Session/:Source are not counted labels), so the key's
    # graph stays empty — the admission is the gate's, not the counter's.
    assert e.counted(e.write_ns) == 0


def test_demo_door_cannot_diverge_because_it_rejects_graph_bound_keys(env):
    """``/v1/demo`` (the 5th points door) is DEFAULT-GRAPH ONLY by design:
    ``_reject_graph_bound_org_surface`` refuses a graph-bound key (403
    GRAPH_SCOPED_TEAM_SURFACE) before the gate, and ``_seed_demo_graph`` opens
    ``namespace=org_id``. Its count and its write are therefore the SAME
    graph, so it has no wrong-graph arm to fix — this pin is what keeps that
    true if the surface is ever opened to per-graph keys.
    """
    e = env(max_points=1)
    e.seed(e.default_ns, "default-at-cap")
    r = e.tc.post("/v1/demo", headers=e.headers())
    assert r.status_code == 403, r.text[:300]
    assert r.json()["detail"]["error_code"] == "GRAPH_SCOPED_TEAM_SURFACE"
