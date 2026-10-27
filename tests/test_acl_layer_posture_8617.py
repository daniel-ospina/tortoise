"""#8617 — the per-graph ACL layer is DECLARED, and its absence is RECORDED.

Owner decision (2026-10-26, issue #8617): **FalkorDB Cloud grants no credential
the app may hold `+ACL`** ("No preset grants `+ACL`"; `ACL` is not in the
allowed-commands list), so the hosted lane stops trying to create the per-graph
user, the graph mint proceeds, and the absence is *reported* rather than
inferred. Self-hosted/docker keep creating the user.

Why these pins live OUTSIDE `tests/test_acl_graph_users.py`: that file is
docker-lane and asserts the layer WORKING. The states pinned here are the ones a
healthy self-hosted server cannot produce (a credential refused `ACL SETUSER`,
a lane that declares the layer off), and a live FalkorDB Cloud call is out of
bounds for this change — the ruling's gaps section records that no live probe
was made and says not to fill that gap by guessing. So the refusal SHAPE comes
from redis-py's own denial text (`NOPERM … has no permissions …`), and the rest
is plain unit behaviour with no server.
"""

from __future__ import annotations

import logging

from tortoise import acl_graph_users as acl


class _Client:
    """Minimal admin-client stand-in: a `MODULE LIST` answer plus a refusal
    selector for the command under test. Not a harness — three call sites."""

    def __init__(self, *, module: list | None, refusal: str | None = None):
        self._module = module
        self._refusal = refusal
        self.seen: list[tuple] = []

    def execute_command(self, *cmd):
        self.seen.append(cmd)
        import redis

        if self._refusal and self._refusal in " ".join(str(c) for c in cmd):
            raise redis.exceptions.ResponseError(
                "NOPERM this user has no permissions to run the "
                f"'{'|'.join(str(c).lower() for c in cmd[:2])}' command")
        if cmd[0] == "MODULE":
            return self._module
        return []  # ACL GETUSER <absent user> → empty reply


def test_a_denied_credential_is_recorded_and_never_raised(monkeypatch):
    """Ruling 3 + 4: the Cloud credential IS refused, and that refusal is a
    distinct `denied` state — not an `AclLayerError` that would roll the mint
    back, and not a silent `None` either.

    Mutation behind this test: make `_setuser` raise the generic
    `AclLayerError` for a `NOPERM` refusal (the pre-#8617 behaviour) — this reds
    on `result.status is DENIED`, and the same mutation makes
    `_provision_graph` roll a hosted mint back, which is the production defect
    (#8617: every dashboard create 503'd).
    """
    # The type graph IS the guarantee: the rollback arms in hosted_api are
    # `isinstance(e, AclLayerError)`, so a refusal must not be one.
    assert not issubclass(acl.AclLayerDenied, acl.AclLayerError)

    client = _Client(module=[[b"name", b"graph", b"ver", 999]],
                     refusal="SETUSER")
    monkeypatch.setattr(acl, "_admin_client", lambda: client)
    monkeypatch.setattr(acl, "_stored_acl_password", lambda *a: None)
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")

    result = acl.create_acl_user("g_denied", "team_x")  # must NOT raise

    assert result.status is acl.AclStatus.DENIED, result
    # Round-4 review, proven by mutation: this stamp could be deleted with the
    # whole suite green, and the reconcile counts the STATUS only — so a
    # `denied` whose reason silently became "unknown" would send the operator
    # after a code defect instead of at the credential.
    assert result.reason == acl.REASON_SETUSER_DENIED, result.reason
    assert result.provisioned is False
    assert "refused" in result.detail.lower(), result.detail


def test_the_capability_probe_tells_denied_apart_from_absent(monkeypatch):
    """Ruling 1's mechanism: `_falkordb_present` is multi-state. A server that
    ANSWERED and refused (`denied`) is a fact about the CREDENTIAL; a server
    with no graph module (`absent`) is a fact about the SERVER. Conflating them
    is what made the pre-#8617 absence unreadable.

    Mutation behind this test: collapse the probe back to a bool (`return
    False` on any exception) — `denied` and `absent` become the same value and
    this reds on the first assertion.
    """
    denied = _Client(module=None, refusal="MODULE")
    bare = _Client(module=[[b"name", b"python", b"ver", 1]])
    loaded = _Client(module=[[b"name", b"graph", b"ver", 999]])

    assert acl._falkordb_present(denied) is acl.FalkorProbe.DENIED
    assert acl._falkordb_present(bare) is acl.FalkorProbe.ABSENT
    assert acl._falkordb_present(loaded) is acl.FalkorProbe.PRESENT

    # And the state reaches the caller: a refused probe is reported as
    # `denied`, not as "no module here".
    monkeypatch.setattr(acl, "_admin_client", lambda: _Client(
        module=None, refusal="MODULE"))
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")
    result = acl.create_acl_user("g_probe", "team_x")
    assert result.status is acl.AclStatus.DENIED, result


def test_an_unreachable_server_is_not_a_bare_redis(monkeypatch):
    """#10093: `absent` used to cover BOTH "this server has no graph module"
    (a boundary: nothing to protect) and "the server could not be reached"
    (an incident: something broke). An outage certified as a deployment fact
    is the silent-degradation class the decision exists to end.

    Mutation behind this test: map the non-refusal exception back to
    `FalkorProbe.ABSENT` — this reds on the first assertion.
    """
    import redis

    class _Down(_Client):
        def execute_command(self, *cmd):
            raise redis.exceptions.ConnectionError("connection reset by peer")

    down = _Down(module=None)
    bare = _Client(module=[[b"name", b"python", b"ver", 1]])
    assert acl._falkordb_present(down) is acl.FalkorProbe.UNREACHABLE
    assert acl._falkordb_present(bare) is acl.FalkorProbe.ABSENT

    monkeypatch.setattr(acl, "_admin_client", lambda: _Down(module=None))
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")
    result = acl.create_acl_user("g_down", "team_x")
    assert result.status is acl.AclStatus.ABSENT, result
    assert result.reason == acl.REASON_LAYER_DOWN, result.reason


def test_every_absence_names_its_own_reason(monkeypatch):
    """#10093: the reconcile report decomposes `absent` into kinds that imply
    DIFFERENT operator actions, so each producing path must stamp its own
    `reason` — a shared default would make the histogram meaningless."""
    # no_uri — no admin client at all
    monkeypatch.setattr(acl, "_admin_client", lambda: None)
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")
    assert acl.create_acl_user("g_a", "team_x").reason == acl.REASON_NO_URI

    # no_graph_module — a reachable server without the graph module
    monkeypatch.setattr(acl, "_admin_client", lambda: _Client(
        module=[[b"name", b"python", b"ver", 1]]))
    assert (acl.create_acl_user("g_b", "team_x").reason
            == acl.REASON_NO_GRAPH_MODULE)

    # probe_denied is a fact about the CREDENTIAL: it stays `denied`, and the
    # reason is the TOKEN — not the detail sentence. Round-1 review proved by
    # mutation that stamping prose here left every test green while
    # REASON_PROBE_DENIED became a constant nothing emits.
    monkeypatch.setattr(acl, "_admin_client", lambda: _Client(
        module=None, refusal="MODULE"))
    r = acl.create_acl_user("g_c", "team_x")
    assert r.status is acl.AclStatus.DENIED
    assert r.reason == acl.REASON_PROBE_DENIED, r.reason
    assert "credential" in r.detail, r.detail

    # declared_off — declared, not discovered
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "off")
    assert (acl.create_acl_user("g_d", "team_x").reason
            == acl.REASON_DECLARED_OFF)


def test_a_probe_state_with_no_mapping_is_a_fault_not_a_boundary(monkeypatch):
    """Round-2 review, proven by mutation: the fail-safe arm was unpinned —
    reverting it to `no_graph_module` (or repointing its detail at "bare redis")
    left the suite green.

    The arm exists because the NEXT probe state cannot be allowed to inherit
    "bare redis, nothing to protect": reporting an unclassifiable state as a
    deployment boundary is precisely the failure this whole change ends. A
    sentinel object stands in for that future state.
    """
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")
    monkeypatch.setattr(acl, "_admin_client", lambda: _Client(module=[]))
    monkeypatch.setattr(acl, "_falkordb_present",
                        lambda client: "some-state-added-later")

    r = acl.create_acl_user("g_future", "team_x")
    assert r.status is acl.AclStatus.ABSENT, r.status
    assert r.reason == acl.REASON_PROBE_UNREADABLE, r.reason
    assert "unrecognised" in r.detail, r.detail
    assert "bare redis" not in r.detail, r.detail


def test_the_absence_is_announced_once_per_REASON_not_once_per_status(
        monkeypatch, caplog):
    """#10093: the announcement was keyed by STATUS, so the first `absent` in a
    process silenced every later `absent` of a different kind — an operator
    read "no server URI" and never learned the server had started vanishing
    mid-op. Keyed by (status, reason), each distinct fact is stated once.

    Mutation behind this test: key the set on `status` alone — the second
    record disappears and this reds.
    """
    import tortoise.hosted_api as ha_mod

    monkeypatch.setattr(ha_mod, "_ACL_ABSENCE_ANNOUNCED", set())
    caplog.set_level(logging.INFO, logger=ha_mod._logger.name)

    ha_mod._announce_acl_absence_once(
        "absent", acl.REASON_NO_URI, "g_1", "no server URI")
    ha_mod._announce_acl_absence_once(
        "absent", acl.REASON_LAYER_DOWN, "g_2", "the server vanished")
    ha_mod._announce_acl_absence_once(
        "absent", acl.REASON_LAYER_DOWN, "g_3", "the server vanished")

    msgs = [r.getMessage() for r in caplog.records
            if "per-graph ACL layer absent" in r.getMessage()]
    assert len(msgs) == 2, msgs
    assert any("no server URI" in m for m in msgs)
    assert any("vanished" in m for m in msgs)


def test_a_connection_failure_during_setuser_is_a_layer_down_not_a_boundary(
        monkeypatch):
    """Round-4 review, proven by mutation: the `layer_down` stamp on the
    SETUSER path could be deleted with the suite green, and the reconcile would
    then bucket a mid-op outage as `unknown` — which both operator docs tell the
    reader to treat as a CODE DEFECT, i.e. the opposite direction.

    The server here ANSWERS the probe (so the layer is real) and then dies
    during the ACL write: exactly the transient fault that must never look like
    a deployment fact."""
    import redis

    class _DiesAtSetuser(_Client):
        def execute_command(self, *cmd):
            self.seen.append(cmd)
            if cmd[0] == "MODULE":
                return [[b"name", b"graph", b"ver", 60002]]
            if cmd[0] == "ACL" and len(cmd) > 1 and cmd[1] == "SETUSER":
                raise redis.exceptions.ConnectionError("connection reset mid-op")
            return []

    monkeypatch.setattr(acl, "_admin_client", lambda: _DiesAtSetuser(module=[]))
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")

    r = acl.create_acl_user("g_midop", "team_x")
    assert r.status is acl.AclStatus.ABSENT, r.status
    assert r.reason == acl.REASON_LAYER_DOWN, r.reason


def test_the_reason_vocabulary_is_distinct():
    """Round-5 note: nothing pinned that the seven reasons are DISTINCT, so a
    future edit could collapse two of them into one token — merging two
    different operator actions inside `absent_reasons` with the suite green.

    The whole point of the vocabulary is that each value implies a different
    action; two equal tokens destroy that silently."""
    values = [acl.REASON_DECLARED_OFF, acl.REASON_NO_URI,
              acl.REASON_NO_GRAPH_MODULE, acl.REASON_PROBE_DENIED,
              acl.REASON_SETUSER_DENIED, acl.REASON_LAYER_DOWN,
              acl.REASON_PROBE_UNREADABLE]
    assert len(set(values)) == len(values), values


def test_the_hook_forwards_the_reason_it_was_given(monkeypatch, caplog):
    """Round-1 review, proven by mutation: the announcement test drives
    `_announce_acl_absence_once` DIRECTLY, so replacing `result.reason` with
    `""` at the hook's call site (hosted_api) left the whole suite green — the
    production forwarding was pinned by nothing, and the status-only-keying
    defect had a silent path back in.

    Mutation behind this test: pass "" instead of `result.reason` in
    `_acl_user_create_hook` — the two records collapse into one and this reds.
    """
    import tortoise.hosted_api as ha_mod

    monkeypatch.setenv(acl.ACL_LAYER_ENV, "strict")
    monkeypatch.setattr(ha_mod, "_ACL_ABSENCE_ANNOUNCED", set())
    caplog.set_level(logging.INFO, logger=ha_mod._logger.name)

    seq = iter([
        acl.AclProvisionResult(status=acl.AclStatus.ABSENT,
                               reason=acl.REASON_NO_URI, detail="no server URI"),
        acl.AclProvisionResult(status=acl.AclStatus.ABSENT,
                               reason=acl.REASON_LAYER_DOWN,
                               detail="the server vanished"),
    ])
    monkeypatch.setattr(acl, "create_acl_user", lambda gid, org: next(seq))

    ha_mod._acl_user_create_hook("g_one", "team_x", strict=True)
    ha_mod._acl_user_create_hook("g_two", "team_x", strict=True)

    msgs = [r.getMessage() for r in caplog.records
            if "per-graph ACL layer absent" in r.getMessage()]
    assert len(msgs) == 2, msgs
    assert any("no server URI" in m for m in msgs)
    assert any("vanished" in m for m in msgs)


def test_the_reconcile_breaks_absent_down_by_reason(monkeypatch):
    """#10093 acceptance 2: the report must let an operator tell WHICH absence
    it is. `absent_reasons` sums to `absent` by construction, and a permission
    refusal is never counted as a design boundary."""
    import tortoise.hosted_api as ha_mod

    def _result(reason):
        return acl.AclProvisionResult(
            status=acl.AclStatus.ABSENT, reason=reason)

    sequence = iter([
        acl.AclProvisionResult(status=acl.AclStatus.PROVISIONED),
        acl.AclProvisionResult(status=acl.AclStatus.ABSENT_BY_DESIGN,
                               reason=acl.REASON_DECLARED_OFF),
        acl.AclProvisionResult(status=acl.AclStatus.DENIED,
                               reason=acl.REASON_SETUSER_DENIED),
        _result(acl.REASON_NO_URI),
        _result(acl.REASON_LAYER_DOWN),
        _result(acl.REASON_LAYER_DOWN),
    ])
    monkeypatch.setattr(acl, "create_acl_user", lambda gid, org: next(sequence))
    monkeypatch.setattr(ha_mod, "_backup_config_safe", lambda: {})
    monkeypatch.setattr(ha_mod, "_control_plane_source", lambda: object())
    import tortoise.backup_sweep as bs
    monkeypatch.setattr(bs, "_sweep_graph_list", lambda reg, org: [
        {"graph_id": f"g_{i}", "kind": "custom"} for i in range(6)])
    monkeypatch.setattr(bs, "enumerate_eligible_orgs", lambda reg: ["team_x"])

    res = ha_mod._reconcile_acl_users_sync()["results"]["team_x"]
    assert res["provisioned"] == 1
    assert res["absent_by_design"] == 1
    assert res["denied"] == 1
    assert res["absent"] == 3
    assert res["absent_reasons"] == {
        acl.REASON_NO_URI: 1, acl.REASON_LAYER_DOWN: 2}, res["absent_reasons"]
    assert sum(res["absent_reasons"].values()) == res["absent"]
    assert res["errors"] == []


def test_declared_off_makes_no_server_call(monkeypatch):
    """Ruling 1 + the brief's "declare, do not discover": with the layer
    declared off, `create_acl_user` answers `absent_by_design` BEFORE any
    server contact — so a hosted mint neither pays a failed round-trip nor
    depends on the credential's behaviour.

    Mutation behind this test: move the `layer_provisionable()` gate after
    `_admin_client()` (or drop it) — the sentinel below is called and this reds.
    """
    def _contacted():  # pragma: no cover - the assertion IS the red
        raise AssertionError(
            "the ACL layer was contacted while the deployment DECLARED it off")

    monkeypatch.setattr(acl, "_admin_client", _contacted)
    monkeypatch.setenv(acl.ACL_LAYER_ENV, "off")

    assert acl.layer_provisionable() is False
    result = acl.create_acl_user("g_off", "team_x")
    assert result.status is acl.AclStatus.ABSENT_BY_DESIGN, result
    # The reason names the setting, so an operator reading a log or the
    # reconcile report knows WHY there is no user.
    assert acl.ACL_LAYER_ENV in result.detail, result.detail


def test_strict_is_the_default_so_self_host_is_unchanged(monkeypatch):
    """Ruling 2: nothing about the self-hosted recipe changes. Unset (the
    self-hosted and docker-lane case) means `strict` — the layer IS attempted;
    only a DECLARED `off` disarms it. An unrecognised value must not silently
    disarm the layer either (a typo would otherwise reproduce the original
    silent-degradation defect)."""
    monkeypatch.delenv(acl.ACL_LAYER_ENV, raising=False)
    assert acl.layer_mode() == "strict"
    assert acl.layer_provisionable() is True

    monkeypatch.setenv(acl.ACL_LAYER_ENV, "of")  # typo
    assert acl.layer_mode() == "strict"
    assert acl.layer_provisionable() is True


def test_the_absence_says_so_once_per_process(monkeypatch, caplog):
    """The defect this issue is about: `_acl_user_create_hook` discarded
    `create_acl_user()`'s return, so a mint succeeded with NO per-graph ACL
    user and NO log line. The hook now consumes the result and announces the
    absence — once per process, not once per graph (a fleet of graphs must not
    drown the signal) and not never.

    Mutation behind this test: drop the `_announce_acl_absence_once` call from
    the hook (the pre-#8617 body) — zero records and this reds; call it
    unconditionally — two records and this reds.
    """
    import tortoise.hosted_api as ha_mod

    monkeypatch.setenv(acl.ACL_LAYER_ENV, "off")
    monkeypatch.setattr(ha_mod, "_ACL_ABSENCE_ANNOUNCED", set())
    caplog.set_level(logging.INFO, logger=ha_mod._logger.name)

    ha_mod._acl_user_create_hook("g_one", "team_x", strict=True)
    ha_mod._acl_user_create_hook("g_two", "team_x", strict=True)

    absent = [r for r in caplog.records
              if "per-graph ACL layer" in r.getMessage()
              and "absent_by_design" in r.getMessage()]
    assert len(absent) == 1, [r.getMessage() for r in caplog.records]
    assert absent[0].levelno == logging.WARNING, absent[0].levelname
    # The line names the graph that exposed it and the declared reason.
    assert "g_one" in absent[0].getMessage()
