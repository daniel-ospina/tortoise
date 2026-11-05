"""#8906 — the REST points gate must count the graph the request WRITES.

The defect (P0-blocker, verified against the deployed build): every REST
points door called ``_check_org_limit(org, "points")``, whose
``enforce_org_limit(org, resource)`` took NO sdk, so ``_count_resource`` fell
to ``_make_sdk(namespace=org_id)`` — the org's DEFAULT graph ``org_{org_id}``
— while a CUSTOM-graph-bound key writes the key's ``graph_namespace``
(``_data_graph_name`` → ``org_{org_id}_{gid}``). For that credential class the
two graphs differ BY CONSTRUCTION (the mint matrix refuses a
``kind='default'`` binding, so a custom binding's namespace is never
``org_{org_id}``; the bound-DEFAULT caveat lives in the ``sdk:`` seam doc of
``quota.enforce_org_limit``, not here), so:

- a custom-graph-bound key with an EMPTY write graph is refused (402) because
  its org's default graph is full — the false refusal that froze every REST
  write once the counted graph reached its cap; and
- such a key whose OWN graph is full is ADMITTED (the other half of the same
  divergence — the cap does not bite on the graph that grows).

⚠️ NOT every per-graph credential diverged, and which ones did follows the org's
STORAGE KEY rather than the lane — the one authoritative statement (and the
reason it is not restated here) lives in ``quota.enforce_org_limit``'s ``sdk:``
doc.

The house rule this restores is `mcp_server.py`: *"Count on the SAME org SDK
the tool writes to (identical connection), so the count and the write can
never target different databases."*

Both directives are asserted for every door, so neither "make it admit" nor
"make it refuse" alone can turn the suite green:

- ADMIT arms (RED under the old behaviour — 402 from the wrong graph):
  ``/v1/points``, ``/v1/objects``+``/v1/subjects``, ``/v1/sessions/commit``;
- REFUSE arms (RED under the old behaviour the OTHER way — 200, the cap
  missing the graph that grows): ``/v1/points``, ``/v1/objects``+``/v1/subjects``,
  ``/v1/sessions/commit``;
- the UNCHANGED branch (must pass BEFORE and after — this is what the fix must
  not break): an org-wide key's gate still follows ``org_{org_id}`` in both
  directions;
- ``/v1/demo`` (default-graph-only, so it has no wrong-graph arm): its gate is
  pinned behaviourally on the default graph, and its graph-bound refusal 403 is
  pinned as the structural reason it can never need one.

⚠️ The cap is the Team's stored ``max_points`` (the same field production
sets to 25000 from ``pricing.json`` solo.max_graph_nodes), held at 1–2 here so
one Point fills a graph. No limit value is changed and the graph's size is the
only axis under test.

⚠️ ``#10637`` (folded from the #4051 lane) landed the SAME defect's other half
on ``main`` while this branch was open: ``quota.enforce_org_limit`` now takes a
lazy ``sdk_factory=`` and ``_check_org_limit``'s fallback is
``_gate_sdk_for(org, resource)``, which mirrors the OPENER's call forms instead
of the org default. So the behavioural arms below no longer FALSIFY ``main`` —
they pin the outcome (#8906 stays closed if that fallback is ever weakened) and
they still falsify the pre-#10637 gate, which is what the falsification table in
the PR body measured.

⚠️ What is and is NOT pinned, stated exactly (six doors thread ``sdk=``:
``/v1/points``, ``/v1/objects``, ``/v1/subjects``, ``/v1/sessions/commit``,
``/v1/demo`` and the capture ``/v1/sessions`` gate): **behaviour** is pinned at
all six by the arms below, but **one-handle identity is pinned structurally at
two** — ``/v1/demo`` and the capture ``sessions`` gate. The OTHER four
``resource="points"`` doors (``/v1/points``, ``/v1/objects``, ``/v1/subjects``,
``/v1/sessions/commit``; ``/v1/demo`` is the fifth and IS pinned) are NOT pinned
for identity: deleting ``sdk=sdk``
there now leaves this suite green, because ``#10637``'s fallback resolves the
same graph for those credentials (that is the double-handle cost, not a wrong
count). Identity is pinned where behaviour CANNOT see it at all — the demo
door splits only if the default resolution moves between two constructions, and
the capture gate is vacuous by ``#4010`` so it never counts. Add a structural
arm beside the behavioural one if a points door ever stops opening a single
handle.
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

#: The transport wait bound, pinned OUT of the `/v1/sessions` capture arm so a
#: latency budget cannot decide an assertion about WHICH sdk handle the gate
#: receives (#10296/#10289: `assert 504 in (200, 201)` on a self-hosted shard).
#: Named rather than inlined so the assertion below can state its relationship to
#: the product's own bound: it must exceed it by a WIDE factor, because the arm
#: also carries a full offline extraction, and the factor is what makes this a
#: "pinned out" value rather than a barely-wider one.
_WAIT_BOUND_PINNED_OUT_S = 300.0
_WAIT_BOUND_PINNED_OUT_FACTOR = 10.0


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

    def seed(self, graph_name: str, label: str) -> None:
        """Write ONE counted Point into a named graph (direct, no HTTP).

        ``create_point``'s first positional arg is the KIND, not a name — the
        label goes into ``content`` (a label in the kind slot only warns
        ``Unrecognized pointKind`` and would break if the SDK ever refuses).
        """
        import tortoise.hosted_api as ha_mod
        sdk = ha_mod._make_sdk(graph_name=graph_name)
        try:
            sdk.create_point("statement", content=f"{label} content")
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
        # Single-use: `patched_tortoise_sdk` re-pins TORTOISE_DB_PATH on ENTER
        # and closes the previous anchors, so a SECOND `env()` in one test would
        # leave the first env measuring the second env's database — a green arm
        # that proves nothing. Fail loudly instead.
        assert not made, (
            "env() is single-use per test: a second call re-pins "
            "TORTOISE_DB_PATH and the first env's counted()/seed() would read "
            "the second DB. Build one env and drive both arms on it.")
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
    assert e.counted(e.default_ns) == 1, (
        "the default graph must be UNTOUCHED — it held 1 before the request, "
        "so a count above 1 means the write widened onto the graph the old "
        "gate counted")


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
    the default graph while ``sdk.create_object``/``sdk.create_subject`` write
    the key's graph, and both are ``resource="points"`` doors (#1911)."""
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
    assert e.counted(e.default_ns) == 2, (
        "the default graph must be UNTOUCHED — it held 2 before the requests, "
        "so a count above 2 means a write widened onto it")


def test_objects_and_subjects_doors_REFUSE_a_full_write_graph(env):
    """The reverse arm for the same two doors — without it a must-admit-only
    pin would also pass with the gate deleted (#8906 "do not weaken")."""
    e = env(max_points=2)
    e.seed(e.write_ns, "write-a")
    e.seed(e.write_ns, "write-b")
    assert e.counted(e.write_ns) == 2, "setup: write graph must be AT cap"
    assert e.counted(e.default_ns) == 0, "setup: default graph must be empty"

    h = e.headers()
    for path, body in (("/v1/objects", {"name": "8906-object2"}),
                       ("/v1/subjects", {"name": "8906-subject2"})):
        r = e.tc.post(path, json=body, headers=h)
        assert r.status_code == 402, (
            f"{path} admitted a write to a FULL key graph (#8906 gate "
            f"weakened): {r.status_code} {r.text[:300]}")
    assert e.counted(e.write_ns) == 2, "a refused write must not land"


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


def test_commit_door_REFUSES_a_full_write_graph(env):
    """The reverse arm at the commit door (#8906 "do not weaken")."""
    from tests.test_commit_endpoint import _finalize, _raw_payload

    e = env(max_points=1)
    e.seed(e.write_ns, "write-at-cap")
    assert e.counted(e.write_ns) == 1, "setup: write graph must be AT cap"
    assert e.counted(e.default_ns) == 0, "setup: default graph must be empty"

    raw = _raw_payload(0, session_id="s8906b", points=[], entities=[])
    r = e.tc.post("/v1/sessions/commit", json=_finalize(raw),
                  headers=e.headers())
    assert r.status_code == 402, (
        "the commit door admitted a commit whose OWN graph is full (#8906 gate "
        f"weakened): {r.status_code} {r.text[:300]}")


def test_org_wide_key_is_refused_at_the_default_graph_cap(env):
    """The branch this fix must NOT change: an org-wide key (``graph_id``
    None) writes ``org_{tid}``, so its gate must count ``org_{tid}``."""
    e = env(max_points=1)
    e.seed(e.default_ns, "default-at-cap")
    assert e.counted(e.default_ns) == 1
    r = e.tc.post("/v1/points", json={"content": "org-wide at cap"},
                  headers=e.headers(graph_bound=False))
    assert r.status_code == 402, (
        "an org-wide key must still be refused at the DEFAULT graph's cap "
        f"(#8906): {r.status_code} {r.text[:300]}")
    assert r.json()["detail"].get("used") == 1, r.text[:300]


def test_org_wide_key_is_admitted_while_a_custom_graph_is_full(env):
    """...and it must NOT be refused by a CUSTOM graph's count: the gate
    follows the credential's own graph in both directions."""
    e = env(max_points=1)
    e.seed(e.write_ns, "custom-graph-full")
    assert e.counted(e.write_ns) == 1
    assert e.counted(e.default_ns) == 0
    r = e.tc.post("/v1/points", json={"content": "org-wide ok"},
                  headers=e.headers(graph_bound=False))
    assert r.status_code in (200, 201), (
        "an org-wide key was refused by a CUSTOM graph's count — the gate must "
        f"follow the credential's own graph (#8906): {r.status_code} "
        f"{r.text[:300]}")
    assert e.counted(e.default_ns) == 1, "the org-wide write must land"
    assert e.counted(e.write_ns) == 1, "the custom graph must be untouched"


def test_demo_door_gates_on_the_default_graph_and_rejects_bound_keys(env):
    """``/v1/demo`` (the 5th points door) is DEFAULT-GRAPH ONLY by design.

    Three pins:
    - an ORG-WIDE key is gated on the default graph (refused at its cap), so
      the door's count target is asserted behaviourally, not just described;
    - a GRAPH-BOUND key is refused 403 GRAPH_SCOPED_TEAM_SURFACE before the
      gate — which is what keeps this door from ever needing a wrong-graph fix;
    - **structurally**, the gate and the seeder are handed ONE handle (#8906
      review P2). Behaviour alone cannot discriminate here: an org-wide
      credential's fallback and `_org_sdk` resolve the same graph, so reverting
      the threading would still go green. The identity assertion is the only
      pin that fails when the handle is split again.
    """
    e = env(max_points=1)
    e.seed(e.default_ns, "default-at-cap")
    assert e.counted(e.default_ns) == 1
    r = e.tc.post("/v1/demo", headers=e.headers(graph_bound=False))
    assert r.status_code == 402, (
        "the demo seed must be gated on the DEFAULT graph it writes (#8906): "
        f"{r.status_code} {r.text[:300]}")
    assert r.json()["detail"].get("used") == 1, r.text[:300]

    r2 = e.tc.post("/v1/demo", headers=e.headers())
    assert r2.status_code == 403, r2.text[:300]
    assert r2.json()["detail"]["error_code"] == "GRAPH_SCOPED_TEAM_SURFACE"



def test_demo_door_hands_the_gate_and_the_seeder_ONE_handle(env, monkeypatch):
    """The structural pin the behavioural demo arm cannot provide (#8906 P2).

    An org-wide credential's fallback SDK and `_seed_demo_sync`'s `_org_sdk` both
    resolve the default graph, so reverting `_check_org_limit(..., sdk=sdk)` at
    that door keeps every behavioural assertion green — the fix would be
    unproven. This arm asserts the property the change exists to buy: the gate
    and the seeder receive the SAME object, and it is not None.
    """
    import tortoise.hosted_api as ha_mod

    e = env(max_points=100)
    seen: dict[str, object] = {}

    real_gate = ha_mod._check_org_limit

    def _spy_gate(org, resource, *, slot_credit=0, sdk=None):
        seen["gate"] = sdk
        return real_gate(org, resource, slot_credit=slot_credit, sdk=sdk)

    def _spy_seed(org_id, sdk=None):
        seen["seed"] = sdk
        return {"status": "stub"}

    monkeypatch.setattr(ha_mod, "_check_org_limit", _spy_gate)
    monkeypatch.setattr(ha_mod, "_seed_demo_graph", _spy_seed)

    r = e.tc.post("/v1/demo", headers=e.headers(graph_bound=False))
    assert r.status_code in (200, 201), r.text[:300]
    assert seen.get("gate") is not None, (
        "the demo door's gate ran without an sdk — it counted the org DEFAULT "
        "graph instead of the graph it writes (#8906)")
    assert seen["gate"] is seen.get("seed"), (
        "the gate and the seeder were handed DIFFERENT SDK handles at /v1/demo "
        "— the count and the write can then split when the default resolution "
        "moves between them (#8906)")


def test_capture_sessions_gate_is_handed_the_capture_handle(env, monkeypatch):
    """The 6th converted gate — `/v1/sessions`' `sessions` resource — is
    VACUOUS today, so only a structural arm can pin it (#8906 review, F2).

    Every resolver emits ``max_sessions: None`` since #4010, and
    ``enforce_org_limit`` returns at the explicit-``None`` branch BEFORE it
    counts, so no behavioural assertion can distinguish ``sdk=sdk`` from no
    argument at this site: reverting that one keyword leaves the whole suite
    green while the door silently re-arms #8906 the day a finite session cap
    ships. This arm asserts the property the change buys — the gate receives the
    SAME handle the capture lane writes through, and it is not ``None``.
    """
    import tortoise.hosted_api as ha_mod

    # ⛔ The transport wait bound is pinned OUT of this arm, deliberately.
    # `/v1/sessions` is a BOUNDED route under the #3834 ruling (10 s of a
    # user's patience), and on these self-hosted shards that budget decides
    # the assertion: measured 2026-11-05 on run 42076633842, this test redded
    # with `assert 504 in (200, 201)` — the handler was still working when the
    # middleware refused legibly, on a host ~7-23x slower per test than a
    # GitHub-hosted one (#10296/#10289). The SUBJECT here is WHICH sdk handle
    # the gate receives, not how fast the capture lane finishes, so a latency
    # invariant must not be able to decide it. The bound itself is still
    # pinned where it belongs — `test_transport_wait_bound.py` asserts
    # `_TRANSPORT_WAIT_BOUND_S == 10.0` and drives the refusal at 0.05 s — so
    # widening it for THIS test weakens nothing: a gate called without
    # `sdk=sdk` still fails the two assertions below.
    assert ha_mod._mcp_auth._TRANSPORT_WAIT_BOUND_S * _WAIT_BOUND_PINNED_OUT_FACTOR <= _WAIT_BOUND_PINNED_OUT_S, (
        f"the pinned-out bound ({_WAIT_BOUND_PINNED_OUT_S}s) must clear the "
        f"product's own bound by at least {_WAIT_BOUND_PINNED_OUT_FACTOR}x — "
        f"it is now {ha_mod._mcp_auth._TRANSPORT_WAIT_BOUND_S}s, so the pin has "
        f"drifted back into the range where a slow shard reds an assertion "
        f"about WHICH handle the gate receives (#10296/#10289)")
    monkeypatch.setattr(ha_mod._mcp_auth, "_TRANSPORT_WAIT_BOUND_S",
                        _WAIT_BOUND_PINNED_OUT_S)

    e = env(max_points=100)
    seen: dict[str, object] = {}

    real_probe = ha_mod._capture_session_probe_off_loop
    real_gate = ha_mod._check_org_limit

    async def _spy_probe(org, session_id):
        out = await real_probe(org, session_id)
        seen["probe"] = out[0]
        return out

    def _spy_gate(org, resource, *, slot_credit=0, sdk=None):
        if resource == "sessions":
            seen["gate"] = sdk
        return real_gate(org, resource, slot_credit=slot_credit, sdk=sdk)

    monkeypatch.setattr(ha_mod, "_capture_session_probe_off_loop", _spy_probe)
    monkeypatch.setattr(ha_mod, "_check_org_limit", _spy_gate)
    # The offline extractor seam — this arm is about WHICH handle the gate gets,
    # not about extraction, so no provider key or network is involved.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")

    r = e.tc.post(
        "/v1/sessions",
        json={"session_id": "s-8906-gate",
              "conversation": [
                  {"role": "user",
                   "content": "The points gate must count the graph this write "
                              "lands in, not the org default."},
                  {"role": "assistant",
                   "content": "Agreed. Count on the same handle the write uses."},
              ]},
        headers=e.headers())
    assert r.status_code in (200, 201), r.text[:400]
    assert seen.get("gate") is not None, (
        "the capture door's `sessions` gate ran without an sdk — an omitted one "
        "counts the org DEFAULT graph, so a finite session cap would compare "
        "the cap against a graph this key never writes (#8906)")
    assert seen["gate"] is seen.get("probe"), (
        "the `sessions` gate was handed a DIFFERENT SDK handle than the capture "
        "lane writes through — the count and the write can then target "
        "different graphs (#8906)")
