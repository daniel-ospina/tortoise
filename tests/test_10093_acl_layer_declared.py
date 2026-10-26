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
        """A REFUSED SETUSER is `denied` — the layer exists and this credential
        may not provision it (the Cloud shape: MODULE LIST answers, ACL does not)."""
        _layer_mode("best_effort")
        r = acl.create_acl_user(f"g-{uuid.uuid4().hex[:8]}",
                                f"t-{uuid.uuid4().hex[:8]}")
        assert r["status"] == "denied"
        assert r["reason_kind"] == "setuser_denied"
        assert "NOPERM" in r["reason"]
        assert cloud.acl_calls()  # it DID try — that is the reported fact

    def test_a_refused_probe_is_absent_and_NOT_fatal_in_strict(
            self, monkeypatch):
        """A credential that cannot even run `MODULE LIST` is "not provisionable
        here", not a failed provisioning attempt — and it must NOT fail a mint
        under `strict`, which is precisely today's behaviour (#10093 review)."""
        _layer_mode("strict")
        client = CloudClient(module_denied=True)
        monkeypatch.setattr(acl, "_admin_client", lambda: client)
        r = acl.create_acl_user("g1", "t1")
        assert r["status"] == "absent"
        assert r["reason_kind"] == "probe_denied"
        assert client.acl_calls() == []  # it never reached SETUSER

    def test_absent_when_no_server_uri(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        r = acl.create_acl_user(f"g-{uuid.uuid4().hex[:8]}",
                                f"t-{uuid.uuid4().hex[:8]}")
        assert r["status"] == "absent"
        assert r["reason_kind"] == "no_uri"
        assert "URI" in r["reason"]

    def test_absent_when_declared_off_carries_its_own_kind(self):
        """`absent` is not one thing: the report needs to tell "declared off"
        from "snapped mid-op" (a transient fault) — hence `reason_kind`."""
        _layer_mode("off")
        assert acl.create_acl_user("g1", "t1")["reason_kind"] == \
            "declared_off"

    def test_result_keeps_the_pre_10093_mapping_shape(self, monkeypatch):
        """Compatibility pin: a PROVISIONED result still carries the credential
        keys as a mapping (`r["username"]` / `r["password"]` / `r["graph"]`),
        which every pre-#10093 caller reads."""

        class _Accepts:
            def __init__(self):
                self.calls = []

            def execute_command(self, *args, **kwargs):
                self.calls.append(args)
                verb = str(args[0]).upper()
                if verb == "MODULE":
                    return [[b"name", b"graph"]]
                if verb == "ACL" and str(args[1]).upper() == "GETUSER":
                    return None  # no such user yet
                return True  # SETUSER / SAVE succeed

        monkeypatch.setattr(acl, "_admin_client", lambda: _Accepts())
        r = acl.create_acl_user("g1", "t1")
        assert r["status"] == "provisioned"
        assert r["username"] == "tenant_g1"
        assert r["graph"] == "org_t1_g1"
        assert r["password"] and len(r["password"]) == 48
        assert isinstance(r, dict)


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


class TestNonStrictPosturesNeverFailTheMint:
    """The two arms that make `off`/`best_effort` non-failing — a future edit
    that re-raises in either one would re-break the production lane."""

    def test_off_logs_one_notice_per_process(self, env, caplog):
        _layer_mode("off")
        with caplog.at_level(logging.INFO, logger="tortoise.hosted_api"):
            assert _mint(env, "off-one")["key_plaintext"]
            assert _mint(env, "off-two")["key_plaintext"]
        notices = [r for r in caplog.records
                   if "DECLARED off" in r.getMessage()]
        assert len(notices) == 1, [r.getMessage() for r in notices]

    def test_best_effort_survives_a_recipe_error(self, env, monkeypatch,
                                                 caplog):
        """A REACHABLE server refusing the recipe (the open-`nopass` default,
        say) is `AclLayerError`, not a denial: under `best_effort` it is logged
        once and the mint still succeeds — the graph exists and has its key."""
        from tortoise.acl_graph_users import AclLayerError
        _layer_mode("best_effort")

        def _boom(graph_id, org_id):
            raise AclLayerError("default ACL user is open (nopass)")

        monkeypatch.setattr(acl, "create_acl_user", _boom)
        with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
            result = _mint(env, "recipe-error-graph")
        assert result["key_plaintext"].startswith("tk_")
        assert _graph_rows(env["tid"], "recipe-error-graph")
        assert any("ACL user create failed" in r.getMessage()
                   for r in caplog.records)


class TestAProbeRefusalNeverFailsAMintEvenUnderStrict:
    """The consequence half of the round-1 P2 fix: mapping the probe refusal to
    `absent` is only half of it — a MINT must still succeed. Pinned at the mint
    because a mutation that re-raises on an `absent` result under `strict`
    restores the original outage and no other test reds (#10093 review)."""

    def test_strict_mint_succeeds_when_the_probe_is_refused(
            self, env, monkeypatch):
        _layer_mode("strict")
        client = CloudClient(module_denied=True)
        monkeypatch.setattr(acl, "_admin_client", lambda: client)
        result = _mint(env, "probe-denied-graph")
        assert result["key_plaintext"].startswith("tk_")
        assert _graph_rows(env["tid"], "probe-denied-graph")  # not rolled back
        assert [c for c in client.calls if str(c[0]).upper() == "ACL"] == []


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
            "g_absent": {"status": "absent", "reason_kind": "declared_off",
                         "reason": "TORTOISE_ACL_LAYER=off"},
            "g_denied": {"status": "denied", "reason_kind": "setuser_denied",
                         "reason": _DENIAL},
            "g_ok": {"status": "provisioned", "username": "tenant_g_ok"},
        }
        monkeypatch.setattr(acl, "create_acl_user",
                            lambda gid, org: by_graph[gid])
        monkeypatch.setattr(ha, "_backup_config_safe", lambda: {})

        report = ha._reconcile_acl_users_sync()
        res = report["results"][env["tid"]]
        assert res["provisioned"] == 1
        assert res["absent_by_design"] == 1
        assert res["absent_reasons"] == {"declared_off": 1}
        assert res["denied"] == 1
        assert res["layer_down"] == 0
        assert "acl_absent_skipped" not in res

    def test_an_unrecognised_status_is_an_error_never_a_success(
            self, env, monkeypatch):
        """#10093 review: a fourth status must never be counted as `provisioned`
        — that is absence reported as success, the one thing the issue forbids."""
        import tortoise.hosted_api as ha
        from tortoise.sdk import TortoiseSDK

        TortoiseSDK(namespace="registry")._get_registry().query(
            "CREATE (g:Graph {id:'g_odd', org_id:$tid, kind:'custom', "
            "namespace:$ns, status:'active'})",
            params={"gid": "g_odd", "tid": env["tid"],
                    "ns": f"org_{env['tid']}_g_odd"},
        )
        monkeypatch.setattr(acl, "create_acl_user",
                            lambda gid, org: {"status": "something-new"})
        monkeypatch.setattr(ha, "_backup_config_safe", lambda: {})

        res = ha._reconcile_acl_users_sync()["results"][env["tid"]]
        assert res["provisioned"] == 0
        assert res["errors"], res

    def test_a_server_that_vanished_is_not_certified_as_by_design(
            self, env, monkeypatch):
        """#10093 review: a TRANSIENT fault must not be reported as a deliberate
        boundary — the DR verification step reads these counters."""
        import tortoise.hosted_api as ha
        from tortoise.sdk import TortoiseSDK

        TortoiseSDK(namespace="registry")._get_registry().query(
            "CREATE (g:Graph {id:'g_down', org_id:$tid, kind:'custom', "
            "namespace:$ns, status:'active'})",
            params={"gid": "g_down", "tid": env["tid"],
                    "ns": f"org_{env['tid']}_g_down"},
        )
        monkeypatch.setattr(
            acl, "create_acl_user",
            lambda gid, org: {"status": "absent", "reason_kind": "layer_down",
                              "reason": "the server went away mid-provisioning"})
        monkeypatch.setattr(ha, "_backup_config_safe", lambda: {})

        res = ha._reconcile_acl_users_sync()["results"][env["tid"]]
        assert res["layer_down"] == 1
        assert res["absent_by_design"] == 0


class TestDropStillRevokesWhereTheLayerIsDeclaredOff:
    """#10093 review: a layer that cannot be CREATED now may still hold users
    created under an earlier posture, so the delete path still attempts the
    revocation — what the declared-off posture buys is log quiet (one warning
    per process), never a skipped credential."""

    def test_drop_attempts_the_revocation_once_logged(self, cloud, caplog):
        _layer_mode("off")
        with caplog.at_level(logging.DEBUG, logger="tortoise.acl"):
            acl.drop_acl_user("g1")
            acl.drop_acl_user("g2")
            acl.drop_acl_user("g3")
        # Every delete issues the command (best-effort, never 500s) …
        assert len([c for c in cloud.calls if str(c[0]).upper() == "ACL"]) == 3
        # … but the refusal is warned ONCE per process, not once per delete.
        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING
                    and "ACL DELUSER failed" in r.getMessage()]
        assert len(warnings) == 1
