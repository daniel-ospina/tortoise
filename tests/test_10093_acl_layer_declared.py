"""#10093 — on FalkorDB Cloud the ACL layer is DECLARED unavailable, not
discovered per mint (implements the #8617 owner decision, 2026-10-26).

No preset grants ``+ACL`` on FalkorDB Cloud and ``ACL`` is not in the vendor's
allowed-commands list, so every hosted graph mint used to fail with
``503 Per-graph ACL provisioning failed`` after rolling the graph back. The
decision: stop trying on the managed lane, RECORD the absence (three distinct
states in the reconcile report), keep the layer everywhere it works
(self-hosted / docker / test), and make ``TORTOISE_ACL_LAYER=strict`` restore
the pre-#10093 behaviour exactly — the setting is a seam, not a dead flag.

These tests run in the EMBEDDED lane (no FalkorDB required): the "Cloud" server
is a fake redis client with the MEASURED production shape — ``MODULE LIST``
answers with the graph module, every ``ACL`` command answers ``NOPERM``. The
registry/graph writes are the real embedded ones, so a mint here is a real
mint.
"""

from __future__ import annotations

import logging
import os
import uuid

import pytest
import redis

from tortoise import acl_graph_users as acl

_DENIAL = "NOPERM this user has no permissions to run the 'acl' command"


class CloudClient:
    """Fake FalkorDB Cloud admin connection (the measured refusal shape)."""

    def __init__(self, *, module_denied: bool = False,
                 module_present: bool = True) -> None:
        self.calls: list[tuple] = []
        self.module_denied = module_denied
        self.module_present = module_present

    def execute_command(self, *args, **kwargs):
        self.calls.append(args)
        verb = args[0] if args else ""
        verb = verb.decode() if isinstance(verb, bytes) else str(verb)
        if verb.upper() == "MODULE":
            if self.module_denied:
                raise redis.ResponseError(
                    "NOPERM this user has no permissions to run the "
                    "'module|list' command")
            return [[b"name", b"graph", b"ver", 42004]] \
                if self.module_present else [[b"name", b"other", b"ver", 1]]
        raise redis.ResponseError(_DENIAL)

    def acl_calls(self) -> list[tuple]:
        return [c for c in self.calls
                if str(c[0]).upper() == "ACL"]


@pytest.fixture
def cloud(monkeypatch):
    """Point the ACL module's admin connection at a Cloud-shaped fake."""
    client = CloudClient()
    monkeypatch.setattr(acl, "_admin_client", lambda: client)
    return client


def _layer_mode(mode: str | None) -> None:
    if mode is None:
        os.environ.pop("TORTOISE_ACL_LAYER", None)
    else:
        os.environ["TORTOISE_ACL_LAYER"] = mode


@pytest.fixture(autouse=True)
def _restore_layer_env():
    """#4883's autouse isolation covers TORTOISE_* — this is the explicit
    belt-and-braces for a test that sets the mode through a raw write, plus the
    per-process "once" notice register (a process-wide fact that would
    otherwise make these assertions order-dependent)."""
    import tortoise.hosted_api as ha

    before = os.environ.get("TORTOISE_ACL_LAYER")
    seen = set(ha._ACL_LAYER_NOTICE_SEEN)
    ha._ACL_LAYER_NOTICE_SEEN.clear()
    yield
    _layer_mode(before)
    ha._ACL_LAYER_NOTICE_SEEN.clear()
    ha._ACL_LAYER_NOTICE_SEEN.update(seen)


# ── Part 1: `tortoise/acl_graph_users.py` — the declared layer ────────────

class TestPresenceProbeIsTriState:
    """`_falkordb_present` answers WHICH absence it found, so a permission
    refusal stops being indistinguishable from a bare-redis server."""

    def test_present(self):
        assert acl._falkordb_present(CloudClient()) == "present"

    def test_bare_redis_is_absent(self):
        assert acl._falkordb_present(
            CloudClient(module_present=False)) == "absent"

    def test_refused_probe_is_denied_not_absent(self):
        """The Cloud shape: `MODULE LIST` itself refused → the credential is
        not permitted, which is NOT "this server has no graph module"."""
        assert acl._falkordb_present(
            CloudClient(module_denied=True)) == "denied"

    def test_unreachable_server_is_absent(self):
        class _Down:
            def execute_command(self, *a, **k):
                raise redis.ConnectionError("connection refused")

        assert acl._falkordb_present(_Down()) == "absent"


class TestSetuserDistinguishesDenial:
    """A permission refusal is `AclLayerDenied`; a recipe failure stays
    `AclLayerError` (today's "roll the mint back" meaning)."""

    def test_permission_refusal_is_denied(self):
        with pytest.raises(acl.AclLayerDenied) as exc:
            acl._setuser(CloudClient(), "tenant_g1", "org_t1_g1", "pw")
        assert "NOPERM" in str(exc.value)
        # Still an AclLayerError: an un-migrated handler takes today's path
        # instead of crashing on an unknown exception type.
        assert isinstance(exc.value, acl.AclLayerError)

    def test_recipe_error_is_not_denied(self):
        class _BadRecipe:
            def execute_command(self, *a, **k):
                raise redis.ResponseError("ERR wrong number of arguments")

        with pytest.raises(acl.AclLayerError) as exc:
            acl._setuser(_BadRecipe(), "tenant_g1", "org_t1_g1", "pw")
        assert not isinstance(exc.value, acl.AclLayerDenied)

    def test_connection_failure_stays_fail_soft(self):
        class _Down:
            def execute_command(self, *a, **k):
                raise redis.ConnectionError("connection refused")

        assert acl._setuser(_Down(), "tenant_g1", "org_t1_g1", "pw") is False


class TestLayerProvisionable:
    """The seam: three declared postures, default = today's behaviour."""

    def test_default_is_strict(self):
        _layer_mode(None)
        assert acl.layer_provisionable() == "strict"

    @pytest.mark.parametrize("mode", ["off", "best_effort", "strict"])
    def test_reads_the_declared_mode(self, mode):
        _layer_mode(mode)
        assert acl.layer_provisionable() == mode

    def test_unknown_value_fails_closed_to_strict(self, caplog):
        """A typo must never silently WEAKEN the layer: an unrecognised value
        is strict (attempt + roll back), never off."""
        _layer_mode("of")
        with caplog.at_level(logging.WARNING, logger="tortoise.acl"):
            assert acl.layer_provisionable() == "strict"
        assert any("TORTOISE_ACL_LAYER" in r.message for r in caplog.records)

    def test_is_case_and_space_tolerant(self):
        _layer_mode("  OFF ")
        assert acl.layer_provisionable() == "off"


class TestCreateAclUserDiscriminatedResult:
    """`create_acl_user` returns a discriminated result, never bare `None`."""

    def test_off_never_touches_the_server(self, cloud):
        _layer_mode("off")
        r = acl.create_acl_user(f"g-{uuid.uuid4().hex[:8]}",
                                f"t-{uuid.uuid4().hex[:8]}")
        assert r["status"] == "absent"
        assert "TORTOISE_ACL_LAYER=off" in r["reason"]
        # It did not even build a connection, let alone issue MODULE LIST/ACL.
        assert cloud.calls == []

    def test_denied_is_a_result_not_an_absence(self, cloud):
        _layer_mode("best_effort")
        r = acl.create_acl_user(f"g-{uuid.uuid4().hex[:8]}",
                                f"t-{uuid.uuid4().hex[:8]}")
        assert r["status"] == "denied"
        assert "NOPERM" in r["reason"]
        assert cloud.acl_calls()  # it DID try — that is the reported fact

    def test_absent_when_no_server_uri(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        r = acl.create_acl_user(f"g-{uuid.uuid4().hex[:8]}",
                                f"t-{uuid.uuid4().hex[:8]}")
        assert r["status"] == "absent"
        assert "URI" in r["reason"]

    def test_result_keeps_the_pre_10093_mapping_shape(self, cloud):
        """Compatibility pin: the credential keys are still readable as a
        mapping (`r["username"]`), which every existing caller does."""
        assert isinstance(acl.create_acl_user("g1", "t1"), dict)


# ── Part 2: `tortoise/hosted_api.py` — the mint consumes the result ───────

@pytest.fixture
def env():
    """A registry Team (pro tier, room for graphs) on the isolated store."""
    from tortoise.sdk import TortoiseSDK

    tid = f"team-10093-{uuid.uuid4().hex[:12]}"
    sdk = TortoiseSDK(namespace="registry")
    sdk._get_registry().query(
        "CREATE (t:Team {id:$id, tier:'pro', max_graphs:100, "
        "max_api_keys:20, backup_enabled:true})",
        params={"id": tid},
    )
    return {"tid": tid, "org": {"id": tid, "tier": "pro", "max_graphs": 100,
                                "max_api_keys": 20}}


def _mint(env, name: str):
    import tortoise.hosted_api as ha
    return ha._provision_graph(env["org"], name, ["graphs:read"], None)


def _graph_rows(tid: str, name: str) -> list:
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK(namespace="registry")._get_registry().query(
        "MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.id",
        params={"gid": _gid(tid, name), "tid": tid},
    ).result_set


def _gid(tid: str, name: str) -> str:
    """The id `_graph_create` derived for a (team, name) pair — read back from
    the registry rather than guessed (the mint returns it)."""
    from tortoise.sdk import TortoiseSDK
    rows = TortoiseSDK(namespace="registry")._get_registry().query(
        "MATCH (g:Graph {org_id:$tid, name:$n}) RETURN g.id",
        params={"tid": tid, "n": name},
    ).result_set
    return rows[0][0] if rows else "g_missing"


class TestHostedMintSurvivesTheDeclaredOffLayer:
    """Acceptance 1: on a Cloud-shaped lane the mint SUCCEEDS and the graph is
    usable; the refusal is recorded, never rolled back for."""

    def test_off_mint_succeeds_and_the_graph_is_usable(self, env, cloud):
        _layer_mode("off")
        result = _mint(env, "cloud-graph")
        assert result["graph"]["namespace"] == \
            f"org_{env['tid']}_{result['graph']['id']}"
        assert result["key_plaintext"].startswith("tk_")
        # The graph exists (not rolled back) …
        assert _graph_rows(env["tid"], "cloud-graph")
        # … and it is USABLE: a write to its namespace answers.
        from tortoise.sdk import TortoiseSDK
        ns = result["graph"]["namespace"]
        db = TortoiseSDK(namespace="registry", graph_name=ns)._get_proj().db
        db.select_graph(ns).query("CREATE (n:Probe {id:'p1'})")
        got = db.select_graph(ns).query(
            "MATCH (n:Probe {id:'p1'}) RETURN n.id").result_set
        assert got == [["p1"]]
        # The layer was never even attempted (the whole point of declaring it).
        assert cloud.calls == []

    def test_best_effort_mint_succeeds_and_logs_the_refusal_once(
            self, env, cloud, caplog):
        """Acceptance 1/2 under the discovered (not declared) posture: the
        attempt happens, the refusal is recorded ONCE PER PROCESS, and the mint
        still succeeds."""
        _layer_mode("best_effort")
        with caplog.at_level(logging.INFO, logger="tortoise.hosted_api"):
            assert _mint(env, "g-one")["key_plaintext"]
            assert _mint(env, "g-two")["key_plaintext"]
        notices = [r for r in caplog.records
                   if "acl layer denied" in r.getMessage().lower()]
        assert len(notices) == 1, [r.getMessage() for r in notices]
        assert cloud.acl_calls()  # it tried — the record is of a real attempt


class TestStrictRestoresTodaysRollback:
    """Acceptance 4: `strict` is today's behaviour, exactly — so the setting
    is a seam and not a dead flag."""

    def test_strict_denial_rolls_back_and_the_503_names_both_remedies(
            self, env, cloud):
        from fastapi import HTTPException

        _layer_mode("strict")
        with pytest.raises(HTTPException) as exc:
            _mint(env, "strict-graph")
        assert exc.value.status_code == 503
        detail = exc.value.detail
        assert "ACL" in detail
        # The rollback happened: no graph node, and no key left behind.
        assert _graph_rows(env["tid"], "strict-graph") == []
        # The old text told EVERY operator to set `requirepass` — a self-host
        # remedy that misdiagnoses Cloud (where no credential may create the
        # user at all). Both real remedies are named instead.
        assert "requirepass" not in detail.lower()
        assert "TORTOISE_ACL_LAYER" in detail

    def test_default_mode_is_strict(self, env, cloud):
        _layer_mode(None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _mint(env, "default-graph")
        assert exc.value.status_code == 503


class TestReconcileReportsThreeStates:
    """Acceptance 2: "not provisionable here", "refused", and "provisioned"
    are three reported numbers — never one conflated skip counter."""

    def test_the_three_states_are_counted_separately(self, env, monkeypatch):
        import tortoise.hosted_api as ha
        from tortoise.sdk import TortoiseSDK

        reg = TortoiseSDK(namespace="registry")._get_registry()
        for gid in ("g_absent", "g_denied", "g_ok"):
            reg.query(
                "CREATE (g:Graph {id:$gid, org_id:$tid, kind:'custom', "
                "namespace:$ns, status:'active'})",
                params={"gid": gid, "tid": env["tid"],
                        "ns": f"org_{env['tid']}_{gid}"},
            )
        by_graph = {
            "g_absent": {"status": "absent",
                         "reason": "TORTOISE_ACL_LAYER=off"},
            "g_denied": {"status": "denied", "reason": _DENIAL},
            "g_ok": {"status": "provisioned", "username": "tenant_g_ok"},
        }
        monkeypatch.setattr(acl, "create_acl_user",
                            lambda gid, org: by_graph[gid])
        monkeypatch.setattr(ha, "_backup_config_safe", lambda: {})

        report = ha._reconcile_acl_users_sync()
        res = report["results"][env["tid"]]
        assert res["provisioned"] == 1
        assert res["absent_by_design"] == 1
        assert res["denied"] == 1
        assert "acl_absent_skipped" not in res


class TestDropIsSkippedWhereTheLayerCannotExist:
    """A layer that cannot be created cannot need dropping: an `off` lane must
    not log a refusal on every graph delete."""

    def test_drop_issues_no_acl_command_when_off(self, cloud):
        _layer_mode("off")
        acl.drop_acl_user("g1")
        assert cloud.calls == []
