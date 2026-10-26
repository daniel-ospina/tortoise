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
    assert result.provisioned is False
    assert "refused" in result.detail.lower(), result.detail


def test_the_capability_probe_tells_denied_apart_from_absent(monkeypatch):
    """Ruling 1's mechanism: `_falkordb_present` is TRI-state. A server that
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
