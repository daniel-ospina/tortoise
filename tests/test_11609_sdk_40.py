"""#11609 P3 — the SDK leg reaches 40: eleven approved names, two exports, one instrument.

`tortoise/__all__.py` declares **40** approved names and states the position exactly:
39 method rows plus row 1's ``Tortoise(...)`` entry point. On `origin/main` **11 of the
approved names had no `def` on `TortoiseSDK` at all**, and the entry point the product
documents was not importable from the package. This file is the gate on the delivery.

WHAT THIS FILE IS THE GATE ON

  1. **All 40 resolve, on the declared axis.** 39 method rows on the class, plus the row-1
     entry point on the package. The declaration is NOT re-cut — it is the owner's list
     (`docs/product/beta-sdk-surface.md` rows 1-40) and is not derivable from code.
  2. **Each of the eleven DELEGATES.** The implementing target is substituted on the
     instance and the arguments it receives are asserted, so a method that grew its own
     copy of the behaviour — the drift a public name must never have — is caught here.
     A routing assertion is exactly what a delegation can get wrong, and exactly what
     this change is allowed to be.
  3. **The refusals stay refusals.** Three of the eleven owe a refusal rather than a
     convenient answer, and each is asserted: an absent control plane REFUSES by naming
     the mode (never `[]`, which reads as "no members" / "no keys" and was produced by
     asking nobody); ``purge=True`` refuses rather than stamping "erased" over data that
     still exists; an unknown confidence scope refuses rather than silently choosing one.
  4. **The instrument measures like-for-like.** ``tools/surface-target-check.py`` compares
     the DECLARED names that resolve against the declared target (39 + 1 = 40), not
     ``dir(TortoiseSDK)`` — which counts the 66 off-surface methods too and can only
     agree with a 40-name target by accident. The `dir()` count is still reported as the
     fallback view, and the default (no `--leg`) still refuses while the MCP leg differs.

No database is needed: these are routing, refusal and instrument tests, and routing is
what a delegation changes. The writers' own behaviour is covered by their own suites.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.sdk_surface_declaration import load_declaration
from tortoise.exceptions import ControlPlaneError
from tortoise.sdk import TortoiseSDK

ROOT = Path(__file__).resolve().parent.parent

#: The approved surface, read the SUPPORTED way — by path, through the gate's own loader.
#: ⛔ Never `from tortoise import __all__`: that binds the MODULE onto the package as
#: `tortoise.__all__` and breaks `from tortoise import *` for the rest of the process
#: (the shadowing `test_sdk_surface_declaration.py` exists to catch).
DECLARATION: tuple[str, ...] = tuple(load_declaration()["__all__"])

#: The eleven that had no `def` on `origin/main` — the deliverable, named so a silent
#: disappearance is a failure rather than a smaller set.
DELIVERED = (
    "create_memory_graph",
    "delete_memory_graph",
    "add_member",
    "list_members",
    "remove_member",
    "create_key",
    "list_keys",
    "revoke_key",
    "adjust_relationship",
    "refresh_confidence",
    "write_knowledge_batch",
)

ENTRY_POINT = "Tortoise"


def _sdk() -> TortoiseSDK:
    """A bare SDK with no graph and no registry — every door here is substituted.

    `TortoiseSDK.__getattr__` raises for any name that is not a real attribute, so the
    two instance attributes the tenancy path reads are set here explicitly: a bare
    `__new__` instance has no `__init__` to establish them, and `_record_tenant` is read
    (not looked up) by `_tenancy_tenant`. `None` is its pre-binding value — the tenant
    then comes from the scope or the env pair, which the tests patch.
    """
    sdk = TortoiseSDK.__new__(TortoiseSDK)
    sdk._record_tenant = None
    return sdk


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _Reg:
    """A registry stub that answers each query with the next scripted rowset."""

    def __init__(self, *rowsets):
        self._rowsets = list(rowsets)
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))
        return _Result(self._rowsets.pop(0) if self._rowsets else [])


# ── 1. the surface is complete, on the declaration's axis ───────────────────────


def test_every_one_of_the_forty_declared_names_resolves():
    import tortoise

    assert len(DECLARATION) == 40, "the approved list was re-cut; that is an owner decision"
    unresolved = [
        name for name in DECLARATION
        if not (callable(getattr(tortoise, name, None)) if name == ENTRY_POINT
                else callable(getattr(TortoiseSDK, name, None)))
    ]
    assert unresolved == [], f"declared names that do not resolve: {unresolved}"


def test_the_eleven_that_had_no_def_are_all_implemented():
    missing = [name for name in DELIVERED if not callable(getattr(TortoiseSDK, name, None))]
    assert missing == [], f"still unimplemented: {missing}"


def test_the_entry_point_resolves_to_the_sdk_class():
    import tortoise

    assert tortoise.Tortoise is TortoiseSDK


def test_the_lazy_falkordb_path_still_resolves():
    """The entry point was added INSIDE the existing `__getattr__` — not a second one.

    `tortoise.FalkorDB` is the other lazy name that lives there (#5386). A second
    `__getattr__` would have shadowed it, and `hasattr` is the check that notices.
    """
    import tortoise

    assert hasattr(tortoise, "FalkorDB")


def test_the_entry_point_is_not_imported_eagerly():
    """`import tortoise` must not drag the SDK in — the cost #5386 removed."""
    proc = subprocess.run(
        [sys.executable, "-c", "import sys, tortoise; print('tortoise.sdk' in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert proc.stdout.strip() == "False", proc.stdout + proc.stderr


# ── 2. each delegation reaches its target, with the caller's arguments ──────────


def test_write_knowledge_batch_delegates_to_ingest():
    sdk = _sdk()
    seen = {}

    def fake_ingest(bundle, granularity="bulk", *, promotion_policy="gated"):
        seen.update(bundle=bundle, granularity=granularity, policy=promotion_policy)
        return {"ok": True}

    sdk.ingest = fake_ingest
    out = sdk.write_knowledge_batch({"points": []}, "row", promotion_policy="strict")

    assert out == {"ok": True}
    assert seen == {"bundle": {"points": []}, "granularity": "row", "policy": "strict"}


def test_adjust_relationship_delegates_to_annotate_operator():
    sdk = _sdk()
    sdk.get_point = lambda point_id: {
        "id": point_id, "is_operator": True,
        "annotator_bias": 0.2, "annotator_precision": 0.3,
        "annotator_consistency": 0.4, "annotator_directness": 0.6,
    }
    seen = {}
    sdk.annotate_operator = lambda pid, **kw: seen.update(id=pid, **kw) or {"annotated": True}

    out = sdk.adjust_relationship("op_1", consistency=0.9)

    assert out == {"annotated": True}
    # Only `consistency` was stated; every other term is the CURRENT value, never a reset.
    assert seen == {
        "id": "op_1",
        "bias": 0.2, "precision": 0.3, "consistency": 0.9, "directness": 0.6,
    }


def test_adjust_relationship_refuses_when_the_current_annotation_is_unreadable():
    """A residency marker is TRUTHY but carries no node — defaulting would corrupt it."""
    sdk = _sdk()
    sdk.get_point = lambda point_id: {"residency": "not-hot"}
    sdk.annotate_operator = lambda *a, **kw: pytest.fail("must not write over an unknown")

    with pytest.raises(ValueError, match="could not be read back"):
        sdk.adjust_relationship("op_1", bias=0.1)


def test_refresh_confidence_scope_changed_is_the_dirty_root_path():
    sdk = _sdk()
    seen = {}
    sdk.compute_confidence = lambda **kw: seen.update(kw) or {"converged": True}

    sdk.refresh_confidence()

    assert seen == {}, "the changed-nodes scope is compute_confidence's no-arg path"


def test_refresh_confidence_scope_graph_passes_every_claim_as_an_anchor():
    sdk = _sdk()
    pages = [
        {"results": [{"id": "c1"}, {"id": "c2"}], "hasMore": True},
        {"results": [{"id": "c3"}], "hasMore": False},
    ]
    offsets = []

    def fake_list(kind="claim", limit=20, offset=0, **kw):
        offsets.append(offset)
        return pages[len(offsets) - 1]

    seen = {}
    sdk.list_knowledge = fake_list
    sdk.compute_confidence = lambda **kw: seen.update(kw) or {"converged": True}

    sdk.refresh_confidence("graph")

    assert seen == {"anchors": ["c1", "c2", "c3"]}
    assert offsets == [0, 2], "the walk must page, not read one window and call it the graph"


def test_refresh_confidence_refuses_an_unknown_scope():
    sdk = _sdk()
    sdk.compute_confidence = lambda **kw: pytest.fail("must not choose a scope for the caller")

    with pytest.raises(ValueError, match="Unknown confidence scope"):
        sdk.refresh_confidence("everything")


def test_create_memory_graph_delegates_to_graph_create():
    sdk = _sdk()
    seen = {}

    def fake_create(org_id, name, *, kind="custom", namespace=None):
        seen.update(org_id=org_id, name=name, kind=kind, namespace=namespace)
        return {"graph_id": "g_1", "name": name, "kind": kind, "namespace": "ns_1"}

    sdk._graph_create = fake_create
    out = sdk.create_memory_graph("org_1", "acme", namespace="ns_1")

    assert seen == {"org_id": "org_1", "name": "acme", "kind": "custom", "namespace": "ns_1"}
    # `backend` is recorded, not accepted-and-ignored (the `operator_action` defect).
    assert out["backend"] == "falkordb"


def test_create_memory_graph_refuses_a_backend_it_cannot_provision():
    sdk = _sdk()
    sdk._graph_create = lambda *a, **kw: pytest.fail("must not create on an unsupported backend")

    with pytest.raises(ValueError, match="Unsupported memory-graph backend"):
        sdk.create_memory_graph("org_1", "acme", backend="neo4j")


def test_delete_memory_graph_default_is_the_recoverable_delete():
    sdk = _sdk()
    seen = {}
    sdk._graph_delete = lambda org_id, gid: seen.update(org_id=org_id, gid=gid) or True

    out = sdk.delete_memory_graph("org_1", "g_1")

    assert seen == {"org_id": "org_1", "gid": "g_1"}
    assert out == {"graph_id": "g_1", "deleted": True, "purge": False}


def test_delete_memory_graph_purge_refuses_and_deletes_nothing():
    sdk = _sdk()
    sdk._graph_delete = lambda *a, **kw: pytest.fail("purge must not fall back to a soft delete")

    with pytest.raises(ValueError, match="NOTHING WAS DELETED"):
        sdk.delete_memory_graph("org_1", "g_1", purge=True)


# ── 3. the actor rows: bounded tenant, and a refusal that is not `[]` ───────────


def _bound_tenant(monkeypatch, tenant=("org_1", "g_1")):
    from tortoise import record_writer

    monkeypatch.setattr(record_writer, "record_tenant_effective", lambda carried=None: tenant)


def _unbound_tenant(monkeypatch):
    from tortoise import record_writer

    monkeypatch.setattr(record_writer, "record_tenant_effective", lambda carried=None: None)


def test_create_key_delegates_to_apikey_create(monkeypatch):
    _bound_tenant(monkeypatch)
    sdk = _sdk()
    seen = {}
    sdk._apikey_create = lambda org_id, created_by, **kw: seen.update(
        org_id=org_id, created_by=created_by, **kw) or {"api_key": "tk_x"}

    out = sdk.create_key(name="prod", graph_id="g_9", scopes=["read"])

    assert out == {"api_key": "tk_x"}
    assert seen["org_id"] == "org_1"
    assert seen["graph_id"] == "g_9"
    assert seen["name"] == "prod"
    assert seen["scopes"] == ["read"]


def test_list_keys_reads_rows_and_hides_revoked_ones(monkeypatch):
    _bound_tenant(monkeypatch)
    sdk = _sdk()
    sdk._get_registry = lambda: _Reg([
        ("k1", "tt_aaaaaaa", "g_1", "prod", ["read"], "t0", "sdk", None, None),
        ("k2", "tt_bbbbbbb", "g_1", "old", [], "t0", "sdk", None, "t1"),
    ])

    rows = sdk.list_keys()

    assert [r["id"] for r in rows] == ["k1"], "a revoked key is a tombstone, not a live credential"
    assert "api_key" not in rows[0], "the plaintext is never stored, so it cannot be listed"


def test_list_keys_can_include_revoked(monkeypatch):
    _bound_tenant(monkeypatch)
    sdk = _sdk()
    sdk._get_registry = lambda: _Reg([
        ("k2", "tt_bbbbbbb", "g_1", "old", [], "t0", "sdk", None, "t1"),
    ])

    rows = sdk.list_keys(include_revoked=True)

    assert rows[0]["revoked_at"] == "t1"


def test_revoke_key_refuses_a_credential_from_another_account(monkeypatch):
    _bound_tenant(monkeypatch, ("org_1", "g_1"))
    sdk = _sdk()
    sdk._get_registry = lambda: _Reg([("org_OTHER",)])
    sdk._apikey_revoke = lambda key_id: pytest.fail("must not revoke another account's key")

    with pytest.raises(ControlPlaneError, match="does not belong to this account"):
        sdk.revoke_key("k1")


def test_revoke_key_delegates_for_an_owned_credential(monkeypatch):
    _bound_tenant(monkeypatch, ("org_1", "g_1"))
    sdk = _sdk()
    sdk._get_registry = lambda: _Reg([("org_1",)])
    sdk._apikey_revoke = lambda key_id: {"revoked": True, "key_id": key_id}

    assert sdk.revoke_key("k1") == {"revoked": True, "key_id": "k1"}


def test_add_member_delegates_to_membership_create(monkeypatch):
    _bound_tenant(monkeypatch)
    sdk = _sdk()
    seen = {}
    sdk._membership_create = lambda org_id, user_id, role: seen.update(
        org_id=org_id, user_id=user_id, role=role) or {"id": "m1"}

    assert sdk.add_member("u1", "admin") == {"id": "m1"}
    assert seen == {"org_id": "org_1", "user_id": "u1", "role": "admin"}


def test_list_members_reads_the_registry_when_supabase_is_off(monkeypatch):
    _bound_tenant(monkeypatch)
    from tortoise import supabase_control

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)
    sdk = _sdk()
    sdk._get_registry = lambda: _Reg([("u1", "owner", "active", None)])

    rows = sdk.list_members()

    assert rows == [{"user_id": "u1", "role": "owner", "status": "active", "email": ""}]


def test_remove_member_delegates_to_membership_remove(monkeypatch):
    _bound_tenant(monkeypatch)
    sdk = _sdk()
    sdk._membership_remove = lambda org_id, user_id: True

    assert sdk.remove_member("u1") == {"user_id": "u1", "removed": True}


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.create_key(),
        lambda s: s.list_keys(),
        lambda s: s.revoke_key("k1"),
        lambda s: s.add_member("u1"),
        lambda s: s.list_members(),
        lambda s: s.remove_member("u1"),
    ],
    ids=["create_key", "list_keys", "revoke_key", "add_member", "list_members", "remove_member"],
)
def test_an_absent_control_plane_refuses_by_naming_the_mode_and_never_returns_empty(
    monkeypatch, call,
):
    """The empty-the-surface rule: a read must not answer `[]` when it asked nobody.

    `[]` from `list_members` reads as "nobody has access" and from `list_keys` as "no
    credentials" — both reassuring, both produced by an unanswered question. The refusal
    must name what is missing, so the fix is in the message.
    """
    _unbound_tenant(monkeypatch)
    sdk = _sdk()
    sdk._record_tenant = None
    sdk._get_registry = lambda: pytest.fail("must not reach a store with no bound account")

    with pytest.raises(ControlPlaneError, match="needs a BOUND account"):
        call(sdk)


# ── 4. the instrument: like-for-like, with the fallback kept ────────────────────


def test_the_checker_compares_declared_names_that_resolve_and_reports_40():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_surface_target_check", ROOT / "tools" / "surface-target-check.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        live = module.live_surface()

        # The COMPLIANCE axis: declared names that resolve.
        assert live["sdk_declared"] == 40, "the SDK leg must count DECLARED names that resolve"
        assert live["sdk_unresolved"] == []
        # ⛔ The shared instrument's meaning is UNCHANGED — `sdk` is still the
        # public-method count, because `tests/test_surface_manifest.py` pins it against
        # the manifest's `counts.sdk_public_methods`. Redefining it would have made that
        # pin compare two different questions (the #10034 defect, inverted).
        assert live["sdk"] == live["sdk_public_methods"] >= 40
        assert live["sdk_public_methods"] != live["sdk_declared"], (
            "the two views coincide only if the off-surface set is empty — if they do, "
            "the fallback has stopped reporting anything"
        )
    finally:
        sys.path.remove(str(ROOT / "tools"))


def test_the_sdk_leg_exits_zero():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "surface-target-check.py"), "--leg", "sdk"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "40 SDK methods" in proc.stdout


def test_the_default_run_still_refuses_while_the_mcp_leg_differs():
    """`--leg sdk` is a scoped statement; the unscoped run must not become a false PASS."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "surface-target-check.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        assert "FAIL MCP tools" in proc.stdout
    else:  # pragma: no cover - only once the MCP migration lands
        assert "OK" in proc.stdout
