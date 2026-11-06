"""Self-host daemon tests (#338 T1.4).

ASGI-level (TestClient, no socket) for /health, /health/ready, MCP handshake
+ grep gate: selfhost module must not import hosted/supabase machinery.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest


def _client_for_env(monkeypatch, tmp_path, **env):
    """Build a TestClient over the selfhost app with the given env.

    conftest.py sets only TORTOISE_SECRET_PEPPER (no URI); clear
    TORTOISE_DB_URI for embedded-mode tests so selfhost's URI check falls
    through to TORTOISE_DB_PATH.
    """
    from starlette.testclient import TestClient

    from tortoise import selfhost

    # Re-point env BEFORE importing selfhost (reads env at import time).
    if "TORTOISE_DB_URI" not in env:
        monkeypatch.setenv("TORTOISE_DB_URI", "")  # force embedded mode
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if "TORTOISE_DB_PATH" not in env:
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "selfhost.db"))

    # #3396 at FILE scope: `monitoring._probe_worker()` is a process-global,
    # ONE-SLOT worker that survives this module's reload (and is NOT reachable
    # from `selfhost`'s own caches), and a probe abandoned by an EARLIER test in
    # the same xdist worker keeps that slot occupied until its own socket call
    # returns (`monitoring.py`: "holds the single shared ``_probe_worker`` slot
    # while it does"; a thread cannot be cancelled, only abandoned).
    #
    # Measured, not hypothetical — CI job 121608127319 at 96f6d91a1:
    # ``TestHealth::test_health_liveness`` never became healthy in 40 s and
    # ``test_health_ready_embedded`` returned 503, both with the snapshot
    # ``{'ok': False, 'observed': False, 'error': 'probe sdk acquisition did not
    # start within 2.0s'}`` — the never-STARTED spelling, i.e. every refresh was
    # SUBMITTED and never picked up. It is order-dependent, not a product
    # defect: the whole file passes in a fresh process (11.8 s). Dropping the
    # worker here gives each test a slot it does not have to share with an
    # abandoned probe, which is the same remedy ``test_hosted_api.py``'s autouse
    # fixture applies for the same class (#7555).
    from tortoise import monitoring as mon

    mon._reset_probe_worker()

    import importlib

    importlib.reload(selfhost)
    return TestClient(selfhost.app)


#: How many times ``_wait_for_probe`` may RE-ARM a provably STRANDED probe
#: slot. Bounded on purpose: the remedy must never turn a genuinely wedged
#: product into a green test, so a slot that strands on every cycle still
#: exhausts the wait and fails — pinned by
#: ``test_the_strand_remedy_is_bounded``.
_PROBE_STRAND_REARMS = 2


def _stranded_slot_error(selfhost_mod) -> str | None:
    """The coordinator's last verdict, IF it proves the slot was STRANDED.

    Returns the error string only for the never-STARTED acquisition spelling
    (``monitoring._PROBE_ACQUISITION_QUEUED_MSG``), else ``None``.

    ⚠️ ``did not start`` is NOT ``was slow``. ``monitoring._acquire_on_probe_worker``
    sets its ``started`` event as the WORKER picks the submission up, so this
    spelling is emitted only when the item sat in the queue for its whole
    budget — no measurement of the DB was attempted at all. That is what makes
    the verdict safe to discard: a MEASURED failure (a dead DB, a black-holed
    socket, an acquisition that raised) carries a different spelling, is never
    re-armed past, and still fails the test.
    """
    from tortoise import monitoring as mon

    error = selfhost_mod._HEALTH_PROBE.snapshot().get("error") or ""
    if error.startswith(mon._PROBE_ACQUISITION_QUEUED_MSG):
        return str(error)
    return None


def _rearm_stranded_probe(selfhost_mod) -> None:
    """Give the next refresh a FREE slot after a provably stranded one.

    ``monitoring._reset_probe_worker()`` is the documented escape hatch for
    exactly this state ("when a probe thread is presumed wedged past any
    realistic socket timeout"), and it is already what ``_client_for_env``
    applies at FILE scope (#3396). Re-arming at TEST scope covers the strand a
    test's own daemon creates mid-run, which no pre-test reset can reach.

    The abandoned thread is not killed — it cannot be (CPython #87185) — it is
    left behind a fresh slot, which is the whole point: the strand stops
    costing the NEXT probe its measurement.
    """
    import tortoise.monitoring as mon

    mon._reset_probe_worker()
    selfhost_mod._HEALTH_PROBE.reset()


#: How long either health wait may block before failing. NOT a product bound —
#: it is the harness tolerance for a COLD embedded FalkorDB on a LOADED runner.
#:
#: The old 40 s was derived when the worst case was thought to be "setup 20 s +
#: refresh 10 s". The real worst case is longer, because an overrun ABANDONS its
#: worker rather than ending it (see ``_rearm_stranded_probe``), so recovery can
#: cost a whole extra cold start: measured 2026-11-05 on a box at load 21.7,
#: ``tests/test_selfhost.py`` took 81 s wall and the 40 s wait expired, while the
#: SAME test passed in 6.6 s seconds later. CI job 126950495861 is the same
#: shape at 4609 tests of concurrent load.
#:
#: It weakens nothing: the wait still returns only on a MEASURED ``ok=True``
#: verdict (or, for readiness, a real 200), and a product that never becomes
#: healthy still fails — it just fails after a budget a loaded box can meet.
_HEALTH_VERDICT_WAIT_S = 90.0

#: How long ``test_the_health_wait_releases_a_starved_probe_slot``'s holder keeps
#: the ONE process-lifetime probe slot occupied. It MUST OUTLAST the wait's own
#: budget, or that test stops proving what it is named for.
#:
#: The test's subject is the wait's RE-ARM of a stranded slot
#: (``_rearm_stranded_probe``, #3396). A hold SHORTER than the budget un-starves
#: itself: the holder times out, its worker drains, the next refresh MEASURES on
#: the freed slot, and the assertion passes with the remedy never called. That is
#: exactly what the 30 s hold against this 90 s default did, measured on PR #11023:
#: GREEN as-is and GREEN with ``_rearm_stranded_probe`` monkeypatched to a no-op.
#: With the hold above the budget the no-op run FAILS at the deadline, so the test
#: discriminates again.
#:
#: ⚠️ The OBSTACLE is what grows here, never the deadline: shrinking
#: ``_HEALTH_VERDICT_WAIT_S`` back below the documented harness tolerance is what
#: red-ed a required check on a HEALTHY daemon (CI run 42186465222, job
#: 127800485941, ``test (d)``), and is not an option.
_STARVED_SLOT_HOLD_S = _HEALTH_VERDICT_WAIT_S + 5.0


def _wait_for_ready(tc, timeout: float = _HEALTH_VERDICT_WAIT_S):
    """Block until ``/health/ready`` answers 200, or fail naming its own 503.

    The FIRST readiness read on a COLD daemon pays the embedded FalkorDB cold
    start INSIDE the endpoint's own DB-leg allowance
    (``selfhost._ready_probe_inner_bound_s`` = 5.5 s at the shipped defaults).
    Measured 0.305 s on an idle box — ~18x headroom, which a loaded CI runner
    does not leave: CI job 126950495861 answered this read 503 while the same
    daemon answered 200 in 0.3 s both locally and at ``origin/main``. Asserting
    200 on ONE cold attempt therefore asserts an environment property, not a
    product one, so the cold start is established here as the PRECONDITION.

    ⚠️ What this deliberately does NOT weaken: the endpoint's BOUNDED answer is
    pinned precisely in ``tests/test_selfhost_health_probe_executor.py`` — the
    ``_READY_PROBE_TIMEOUT_S``/``_ready_probe_inner_bound_s`` derivation, the
    parked-worker false-503 pins, and
    ``test_readiness_fan_in_does_not_produce_a_false_503`` — which is where a
    boundedness regression belongs. THIS test's subject is "a healthy embedded
    DB reports ready", and it still fails on a PERSISTENT not-ready (see
    ``test_the_readiness_wait_still_fails_when_the_daemon_never_gets_ready``).
    """
    import time

    deadline = time.monotonic() + timeout
    last = None
    while True:
        last = tc.get("/health/ready")
        if last.status_code == 200:
            return last
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"/health/ready never answered 200 within {timeout}s — last "
                f"answer {last.status_code} {last.text!r} (a healthy embedded "
                "DB must report ready; a bounded not-ready answer is the "
                "endpoint's own contract, pinned in "
                "tests/test_selfhost_health_probe_executor.py)"
            )
        time.sleep(0.1)


def _wait_for_probe(selfhost_mod, timeout: float = _HEALTH_VERDICT_WAIT_S,
                   *, require_ok: bool = True):
    """Block until the background liveness refresher's verdict has SETTLED.

    #2988: ``/health`` reads an in-memory snapshot, so a fresh process
    truthfully reports "probe has not produced a result" (→ degraded) until the
    refresher's first probe lands. Waiting for that is deterministic; sleeping
    a fixed amount is not.

    #7520: for a test that needs a HEALTHY daemon, mere ARRIVAL is not enough.
    The first probe after a cold start pays the O(graph) projection cold start
    and the refresh cycle is ``max(health_probe_interval(), probe_duration)``
    (documented worst case ~20 s + 10 s), so the refresher legitimately reads
    ``ok=False`` for the early cycles — and asserting ``/health == "ok"`` on a
    merely-arrived verdict asserts a precondition the test never established.
    By DEFAULT we therefore wait for the ``/health`` read path's own ``ok=True``
    verdict (``snapshot()`` — the same in-memory view ``/health`` serves). Pass
    ``require_ok=False`` when the test DELIBERATELY drives a failing probe and
    only needs the verdict to land.

    AT TEST SCOPE (#3396): a strand this test's OWN daemon creates is not
    reachable by ``_client_for_env``'s pre-test reset. A probe phase that
    overruns its enforced deadline is ABANDONED, never cancelled (CPython
    #87185), and the abandoned thread keeps the single process-lifetime slot
    until its own call returns (``monitoring`` guarantee (c)) — while it does,
    EVERY later acquisition queues and the daemon reads
    ``probe sdk acquisition did not start within 2.0s`` on every refresh. CI
    job 126950495861 measured exactly that for the full 40 s. A verdict with
    that spelling carries NO measurement of the DB (the acquisition phase sets
    its ``started`` event as the worker picks the submission up), so it is the
    one verdict we may discard: see ``_stranded_slot_error``.

    On expiry the failure names the probe's OWN state instead of surfacing as a
    bare ``assert 'degraded' == 'ok'``, which misreads as a product divergence.
    """
    import time

    deadline = time.monotonic() + timeout
    rearms = 0
    while True:
        info = selfhost_mod._HEALTH_PROBE.info()
        if info.get("result_age_s") is not None:
            if not require_ok:
                return
            if selfhost_mod._HEALTH_PROBE.snapshot().get("ok") is True:
                return
            # A provably STRANDED slot: re-arm so the next cycle MEASURES
            # instead of queueing. Bounded — a persistent wedge still fails.
            if (rearms < _PROBE_STRAND_REARMS
                    and _stranded_slot_error(selfhost_mod) is not None):
                rearms += 1
                _rearm_stranded_probe(selfhost_mod)
                info = selfhost_mod._HEALTH_PROBE.info()
        if time.monotonic() >= deadline:
            if not require_ok:
                raise AssertionError(
                    "the selfhost liveness refresher never produced a verdict "
                    f"within {timeout}s — probe info {info!r}"
                )
            raise AssertionError(
                "the selfhost daemon never became healthy within "
                f"{timeout}s — snapshot "
                f"{selfhost_mod._HEALTH_PROBE.snapshot()!r}, probe info {info!r}, "
                f"strand re-arms used {rearms}/{_PROBE_STRAND_REARMS}"
            )
        time.sleep(0.02)


def test_embedded_banner_stderr(monkeypatch, tmp_path, capsys):
    """#942: the selfhost daemon prints the loud SINGLE-WRITER / EVAL-ONLY
    banner to stderr when started in embedded mode (no TORTOISE_DB_URI)."""
    tc = _client_for_env(monkeypatch, tmp_path)
    with tc:
        pass
    err = capsys.readouterr().err
    assert "SINGLE-WRITER" in err and "EVAL ONLY" in err


def test_uri_mode_no_banner(monkeypatch, tmp_path, capsys):
    """#942 negative pin: no banner when TORTOISE_DB_URI is set."""
    tc = _client_for_env(
        monkeypatch, tmp_path,
        TORTOISE_DB_URI="docker://:falkordb@localhost:6379/tortoise",
    )
    with tc:
        pass
    err = capsys.readouterr().err
    assert "SINGLE-WRITER" not in err


class TestOffBoxBackupSignal:
    """#8543 — the off-box backup target is a setup-path decision, never silent.

    Owner decision 2026-10-13 (parity with hosted): a self-host deployment must
    DECLARE an off-box target or EXPLICITLY DECLINE one; ABSENT must stay
    distinguishable from DECLINED. These tests pin the tristate, the message
    polarity, and the boot emission. #8544 tightened what "declare" MEANS: the
    target is the COMPLETE set (R2 access + bucket + the archive encryption
    key), because a partial target cannot produce a restorable archive — and it
    added the DRIVER, whose own behaviour is pinned in
    ``tests/test_selfhost_backup.py``.
    """

    def test_unset_is_absent_and_says_single_local_copy(self):
        from tortoise import selfhost

        assert selfhost.off_box_backup_state({}) == selfhost.OFF_BOX_ABSENT
        msg = selfhost.off_box_backup_signal(selfhost.OFF_BOX_ABSENT)
        low = msg.lower()
        assert "single local copy" in low
        assert "total loss" in low
        # The decline knob is offered, so silence is actionable rather than a
        # dead end.
        assert selfhost._OFF_BOX_DECLINE_ENV in msg

    def test_messages_never_assert_a_store_mechanism(self):
        """#8552 review P1: the resolver reads only the BACKUP env vars, so it
        cannot observe the store — and the mechanism is INVERTED between the two
        self-host paths (embedded: AOF OFF by default per
        ``projection._embedded_aof_enabled``, RDB the mechanism; compose: AOF
        `everysec` on). A message that names the mechanism is therefore false on
        one path — the #101 incident shape (a deployment believed durable on a
        mechanism it did not have). Non-vacuous: the pre-fix ABSENT text said
        "(AOF on the named volume; RDB is off)".
        """
        from tortoise import selfhost

        absent = selfhost.off_box_backup_signal(selfhost.OFF_BOX_ABSENT)
        declined = selfhost.off_box_backup_signal(selfhost.OFF_BOX_DECLINED)
        for msg in (absent, declined):
            low = msg.lower()
            assert "rdb" not in low, msg
            assert "aof" not in low, msg
            # The local-copy claim is SCOPED to the paths where it holds, so a
            # remote store's own off-box durability is not denied.
            assert "compose and embedded" in low, msg

    def test_declined_flag_is_explicit_not_the_default(self):
        from tortoise import selfhost

        env = {selfhost._OFF_BOX_DECLINE_ENV: "1"}
        assert selfhost.off_box_backup_state(env) == selfhost.OFF_BOX_DECLINED
        # A falsy spelling is NOT a decline — only an explicit truthy is.
        assert selfhost.off_box_backup_state(
            {selfhost._OFF_BOX_DECLINE_ENV: "0"}) == selfhost.OFF_BOX_ABSENT
        msg = selfhost.off_box_backup_signal(selfhost.OFF_BOX_DECLINED)
        assert "explicitly declined" in msg.lower()
        # Declining does not license a mechanism claim: the message states the
        # residual (no off-box copy) without naming AOF/RDB (#8552 review).
        assert "no off-box copy" in msg.lower()

    def test_only_a_complete_target_reports_configured(self):
        """#8544: a PARTIAL target is ABSENT, not "declared".

        ``R2Storage`` refuses a partial R2 config and ``encrypt_backup``
        fail-closes without ``TORTOISE_BACKUP_KEY``, so a deployment holding
        some of the target cannot produce a restorable archive. Reporting it
        "declared" is the exact defect #8544 names, one layer down. Each var
        alone (the pre-#8544 rule made ANY of them "configured") now reports
        ABSENT; only the full set reports CONFIGURED.
        """
        from tortoise import selfhost

        each = {n: "x" for n in selfhost._OFF_BOX_TARGET_ENVS}
        for var in selfhost._OFF_BOX_TARGET_ENVS:
            assert selfhost.off_box_backup_state({var: "x"}) == \
                selfhost.OFF_BOX_ABSENT, var
            # Blank is UNSET, not declared — even with the rest present.
            partial = dict(each, **{var: "  "})
            assert selfhost.off_box_backup_state(partial) == \
                selfhost.OFF_BOX_ABSENT, var
            assert var in selfhost.off_box_backup_missing(partial), var
        assert selfhost.off_box_backup_state(dict(each)) == \
            selfhost.OFF_BOX_CONFIGURED
        assert selfhost.off_box_backup_missing(dict(each)) == []

    def test_hosted_driver_switch_is_not_a_selfhost_target(self):
        """#8544: ``BACKUP_SWEEP_ENABLED`` is the HOSTED app's switch.

        Nothing in the self-host lane ever consumed it, so a self-host deploy
        flipping it was reported "declared" while nothing could run — a false
        PASS on the surface that tells an operator whether they are backed up.
        The self-host driver is armed by the complete target set alone.
        """
        from tortoise import selfhost

        assert selfhost.off_box_backup_state(
            {"BACKUP_SWEEP_ENABLED": "true"}) == selfhost.OFF_BOX_ABSENT

    def test_absent_message_names_the_missing_target_vars(self):
        """A partial config must be ACTIONABLE: the boot line names exactly
        what is missing rather than reporting a bare "no target"."""
        from tortoise import selfhost

        partial = {"R2_BUCKET": "b", "TORTOISE_BACKUP_KEY": "k"}
        missing = selfhost.off_box_backup_missing(partial)
        assert missing == ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                           "R2_SECRET_ACCESS_KEY"]
        msg = selfhost.off_box_backup_signal(
            selfhost.OFF_BOX_ABSENT, missing=missing)
        for name in missing:
            assert name in msg, name

    def test_declared_target_wins_over_a_decline_flag(self):
        """Both set is a config error; the target the deploy is configured with
        is the one to report, so the signal cannot claim "declined" over a live
        target configuration."""
        from tortoise import selfhost

        env = {n: "x" for n in selfhost._OFF_BOX_TARGET_ENVS}
        env[selfhost._OFF_BOX_DECLINE_ENV] = "1"
        assert selfhost.off_box_backup_state(env) == selfhost.OFF_BOX_CONFIGURED

    def test_configured_message_never_claims_a_copy_exists(self):
        """#8544: the signal cannot observe the driver's runs, so a declared
        target must not read as an off-box copy — it says what the driver does
        and that a copy is a RUN that reported one."""
        from tortoise import selfhost

        msg = selfhost.off_box_backup_signal(selfhost.OFF_BOX_CONFIGURED)
        low = msg.lower()
        assert "declared" in low
        assert "not a copy" in low
        assert "driver" in low

    def _reload_with_no_backup_env(self, monkeypatch):
        """Reload selfhost with EVERY input of ``off_box_backup_state`` scrubbed.

        Derived from the resolver's OWN registries (``_OFF_BOX_TARGET_ENVS`` +
        the decline flag) rather than a hand list, so a new target var cannot
        silently re-introduce the ambient-shell premise (#8552 review;
        #3695/#5477 doctrine). ``BACKUP_SWEEP_ENABLED`` is scrubbed too: it is
        no longer an input (#8544 — it is the hosted app's switch), and leaving
        it would let a stale premise pass by accident.
        """
        import importlib

        from tortoise import selfhost

        for var in (*selfhost._OFF_BOX_TARGET_ENVS, "BACKUP_SWEEP_ENABLED"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(selfhost._OFF_BOX_DECLINE_ENV, raising=False)
        importlib.reload(selfhost)

    @pytest.mark.parametrize("var", [
        "R2_BUCKET", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "TORTOISE_BACKUP_KEY", "BACKUP_SWEEP_ENABLED",
    ])
    def test_boot_signal_premise_ignores_ambient_target_env(
            self, monkeypatch, capsys, var):
        """#8552 review P2, extended by #8544: the boot-emission test asserts
        "nothing is declared", so it must scrub ALL inputs the resolver reads —
        an ambient ``R2_ACCOUNT_ID`` (a real exported var on this box's deploy
        and backup lanes) otherwise flips the resolver to CONFIGURED and the
        assertion fails on a clean tree. Non-vacuous by construction: pre-fix
        the reload cleared only ``R2_BUCKET`` + the decline flag.
        """
        monkeypatch.setenv(var, "ambient-value")
        self._reload_with_no_backup_env(monkeypatch)
        err = capsys.readouterr().err
        assert "SINGLE LOCAL COPY" in err
        assert "target DECLARED" not in err

    def test_boot_signal_is_printed_and_logged(self, monkeypatch, capsys):
        """The signal fires at daemon boot (module load) — the deploy ladder
        ``docker compose up`` shows it, so the omission cannot pass unnoticed.
        """
        self._reload_with_no_backup_env(monkeypatch)
        err = capsys.readouterr().err
        assert "off-box backup" in err
        assert "SINGLE LOCAL COPY" in err

    def test_setup_surfaces_record_the_decision(self):
        """Deliverable 1 of #8543: the decision is a STEP of the self-host setup
        path, so the shipped compose template and the quickstart both name it.
        #8544 adds the requirement that the declared target is COMPLETE — the
        compose template must therefore carry the archive key too, or following
        it verbatim yields an archive-less "declared" deployment.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        compose = (root / "docker-compose.yml").read_text()
        assert "TORTOISE_OFFBOX_BACKUP_DECLINED" in compose
        assert "R2_BUCKET" in compose
        assert "TORTOISE_BACKUP_KEY" in compose
        quickstart = (root / "docs" / "quickstart-selfhosted.md").read_text()
        assert "TORTOISE_OFFBOX_BACKUP_DECLINED" in quickstart
        assert "durability-posture.md" in quickstart
        assert "TORTOISE_BACKUP_KEY" in quickstart


class TestHealth:
    def test_health_liveness(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        from tortoise import selfhost

        with tc:
            _wait_for_probe(selfhost)
            r = tc.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            # #1384 deep check: the DB verdict rides along on liveness (now from
            # the coordinator's in-memory snapshot, #2988).
            assert body["db"]["ok"] is True
            assert isinstance(body["db"]["latency_ms"], (int, float))

    def test_health_read_path_is_in_memory_and_refresher_carries_the_allowance(
            self, monkeypatch, tmp_path):
        """#2988/#3243: the read path and the allowance must stay separated.

        The READ path must do NO I/O (that is what keeps the gate fast under
        saturation), while the projection cold-start allowance lives on the
        background refresher — where a large graph's slow cold start costs no
        request anything. Threading the allowance into the read path is what
        #3243 explicitly forbids; leaving it out of the refresher is the false
        degrade the same issue reports.
        """
        # A long refresh period keeps the background loop from probing again
        # inside the measured request window ("15" is the clamp ceiling).
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k",
                             TORTOISE_HEALTH_PROBE_INTERVAL="15")
        import tortoise.monitoring as mon
        from tortoise import selfhost

        seen = {"calls": 0, "setup_timeout": None}

        def _spy_probe_db(sdk=None, setup_timeout=None, *, acquire=None):
            seen["calls"] += 1
            seen["setup_timeout"] = setup_timeout
            seen["acquire"] = acquire
            return {"ok": True, "latency_ms": 0.1, "error": None}

        monkeypatch.setattr(mon, "probe_db", _spy_probe_db)
        with tc:
            _wait_for_probe(selfhost)
            calls_after_refresh = seen["calls"]
            assert calls_after_refresh >= 1, "the refresher never probed"
            assert seen["setup_timeout"] == mon.probe_setup_timeout(), (
                "the refresher did not pass the #3243 cold-start allowance — a "
                f"reachable cold graph would read degraded {seen}"
            )
            assert seen["acquire"] is not None, (
                "#3446: the refresher must hand its SDK lookup to probe_db as "
                "acquire= so the phase is bounded — passing an already-acquired "
                "sdk leaves it unbounded on this coordinator's thread"
            )
            r = tc.get("/health")
            assert r.status_code == 200
            assert seen["calls"] == calls_after_refresh, (
                "the /health read path ran the DB probe — it must read in-memory "
                "state only (#2988)"
            )

    def test_health_degraded_when_db_down(self, monkeypatch, tmp_path):
        """#1384: a stopped FalkorDB flips /health to degraded — 200, never
        500 or a crashed handler."""
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        import tortoise.monitoring as mon
        from tortoise import selfhost

        def _boom_probe(sdk=None, setup_timeout=None, *, acquire=None):
            raise ConnectionError("NXDOMAIN")

        # probe_db is imported lazily from tortoise.monitoring inside the
        # refresher's probe — patch it there, not on the selfhost module.
        monkeypatch.setattr(mon, "probe_db", _boom_probe)
        with tc:
            # Deliberately failing probe: wait only for the verdict to land.
            _wait_for_probe(selfhost, require_ok=False)
            r = tc.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "degraded"
            assert body["db"]["ok"] is False
            assert "NXDOMAIN" in body["db"]["error"]

    def test_health_ready_embedded(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        with tc:
            # Cold start is this test's PRECONDITION, not its subject — see
            # ``_wait_for_ready`` (CI job 126950495861 read 503 here under load
            # while the same daemon answered 200 in 0.3 s locally and on main).
            r = _wait_for_ready(tc)
            assert r.status_code == 200
            assert r.json()["status"] == "ready"

    def test_a_wedged_probe_worker_from_an_earlier_test_is_not_inherited(
            self, monkeypatch, tmp_path):
        """#3396 at FILE scope: the shared probe slot must not be inherited.

        A probe abandoned by a previous test in this xdist worker holds the
        process-global one-slot ``monitoring._probe_worker()`` until its own
        socket call returns, so a daemon built afterwards reports
        ``probe sdk acquisition did not start within 2.0s`` for every refresh
        and never becomes healthy (CI job 121608127319). This pins the remedy:
        ``_client_for_env`` drops the occupied worker, so the health verdict
        this test's daemon reads is its OWN probe's, not an ambient one.

        Deterministic by construction — it wedges the slot itself instead of
        relying on whatever an earlier file happened to leave behind.
        """
        import threading

        import tortoise.monitoring as mon
        from tortoise import selfhost

        started, release = threading.Event(), threading.Event()

        def _block():
            started.set()
            release.wait(60)

        occupied = mon._probe_worker()
        occupied.submit(_block)
        assert started.wait(30), "the shared probe worker never ran the blocker"
        try:
            tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
            with tc:
                assert mon._probe_worker() is not occupied, (
                    "the occupied probe worker was inherited — a probe "
                    "submitted here would never start (#3396)")
                _wait_for_probe(selfhost)
        finally:
            release.set()

    def test_a_strand_created_by_this_daemons_own_probe_is_re_armed(
            self, monkeypatch, tmp_path):
        """#3396 at TEST scope: a strand created AFTER the client is built.

        ``_client_for_env`` reset cannot reach this one: the slot is occupied by
        the time this daemon's refresher first probes, so every acquisition
        queues and the daemon reports ``probe sdk acquisition did not start
        within 2.0s`` with no measurement of the DB at all. The wait must
        RE-ARM the stranded slot (fresh worker) instead of waiting out the
        deadline, and then assert on the real verdict.

        Deterministic: the slot is wedged here, not inherited from whatever an
        earlier file left behind.
        """
        import threading

        import tortoise.monitoring as mon
        from tortoise import selfhost

        # A short refresh period keeps the recovery inside the test's own wait;
        # it does not affect what is being asserted (a free slot is measured).
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k",
                             TORTOISE_HEALTH_PROBE_INTERVAL="0.5")

        started, release = threading.Event(), threading.Event()

        def _block():
            started.set()
            # ⛔ THE HOLD OUTLASTS THE WAIT BUDGET, the same two-part treatment
            # `_STARVED_SLOT_HOLD_S` gives the sibling leg: a hold SHORTER than
            # the budget un-starves itself and the leg passes with the remedy
            # deleted. The OBSTACLE grows here; the documented tolerance does
            # not shrink. (A 60 s hold against a 15 s ad-hoc budget was that
            # shape inverted — the budget, not the hold, was the margin, and
            # 15 s sits 10 s BELOW the tolerance this module documents for a
            # loaded runner. Measured: the recovery is ~2–4 s when the remedy
            # works, so this costs nothing on the green path.)
            release.wait(_STARVED_SLOT_HOLD_S)

        occupied = mon._probe_worker()
        occupied.submit(_block)
        assert started.wait(30), "the shared probe worker never ran the blocker"
        try:
            with tc:
                # Pre-fix this exhausts (the strand never clears) and FAILS; with
                # the re-arm the verdict lands in ~2-4 s. The DEFAULT budget, not
                # an ad-hoc literal — see `_HEALTH_VERDICT_WAIT_S`.
                _wait_for_probe(selfhost)
                body = tc.get("/health").json()
                assert body["status"] == "ok"
                assert body["db"]["ok"] is True
        finally:
            release.set()

    def test_the_strand_remedy_is_bounded(self, monkeypatch, tmp_path):
        """The re-arm must never make a PERSISTENT strand look healthy.

        Simulates a slot that strands on EVERY cycle — the probe itself reports
        the never-STARTED spelling and never a measurement — so the wait must
        give up after ``_PROBE_STRAND_REARMS`` re-arms and FAIL. What this pins
        is that re-arming is BOUNDED: it is a remedy for a transient strand,
        never a retry loop that could turn a wedged product green.
        """
        import sys

        import tortoise.monitoring as mon
        from tortoise import selfhost

        def _always_queued(sdk=None, setup_timeout=None, *, acquire=None):
            return {"ok": False, "observed": False, "latency_ms": 0.0,
                    "error": f"{mon._PROBE_ACQUISITION_QUEUED_MSG}2.0s"}

        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k",
                             TORTOISE_HEALTH_PROBE_INTERVAL="0.5")
        monkeypatch.setattr(mon, "probe_db", _always_queued)
        calls = {"rearms": 0}
        this_module = sys.modules[__name__]

        def _counting_rearm(selfhost_mod):
            calls["rearms"] += 1
            selfhost_mod._HEALTH_PROBE.reset()

        monkeypatch.setattr(this_module, "_rearm_stranded_probe", _counting_rearm)
        with tc, pytest.raises(AssertionError) as excinfo:
            _wait_for_probe(selfhost, timeout=3.0)
        assert calls["rearms"] == _PROBE_STRAND_REARMS, (
            "the strand remedy is not bounded — it re-armed "
            f"{calls['rearms']} times; a persistent strand must fail the test")
        assert "strand re-arms used" in str(excinfo.value), (
            f"the failure must name the re-arm budget: {excinfo.value}")

    def test_the_readiness_wait_waits_out_a_transient_not_ready(
            self, monkeypatch, tmp_path):
        """A transient cold-start 503 must be waited out, not asserted on.

        Non-vacuous by construction: the first read really does answer 503 (the
        leg is made to time out once) — a single-read assertion, which is what
        the test did before, would fail on exactly this response.
        """
        from tortoise import selfhost

        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        real = selfhost._run_bounded
        state = {"calls": 0}

        def _flaky(fn, allowance_s):
            state["calls"] += 1
            if state["calls"] == 1:
                raise TimeoutError("probe db leg overran its allowance")
            return real(fn, allowance_s)

        monkeypatch.setattr(selfhost, "_run_bounded", _flaky)
        with tc:
            first = tc.get("/health/ready")
            assert first.status_code == 503, (
                "the transient fault did not take effect — this test cannot "
                f"prove the wait retries: {first.status_code} {first.text!r}")
            # The retry is immediate; what the budget pays for is the cold DB
            # behind it, so it is the file's tolerance — not a 15 s literal a
            # cold start can outrun on a loaded runner (#10790).
            r = _wait_for_ready(tc)
            assert r.status_code == 200
            assert r.json()["status"] == "ready"

    def test_the_readiness_wait_still_fails_when_the_daemon_never_gets_ready(
            self, monkeypatch, tmp_path):
        """A PERSISTENT not-ready must still fail, naming the endpoint's 503."""
        from tortoise import selfhost

        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")

        def _always_overruns(fn, allowance_s):
            raise TimeoutError("probe db leg overran its allowance")

        monkeypatch.setattr(selfhost, "_run_bounded", _always_overruns)
        with tc, pytest.raises(AssertionError) as excinfo:
            _wait_for_ready(tc, timeout=0.5)
        message = str(excinfo.value)
        assert "never answered 200" in message and "503" in message, (
            f"the failure must name the endpoint's own answer: {message}")


class TestMCPHandshake:
    def test_initialize_handshake_no_auth_mode(self, monkeypatch, tmp_path):
        # auth_mode="none" (no key) — initialize passes through to MCP server
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "selfhost-test", "version": "0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            assert r.status_code in (200, 202)

    def test_static_mode_requires_key(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="s3cret")
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            assert r.status_code == 401
            r2 = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json",
                         "Authorization": "Bearer s3cret"},
            )
            assert r2.status_code in (200, 202)


class TestGrepGate:
    def test_no_hosted_or_supabase_machinery(self):
        """selfhost must not import hosted_api / supabase / TeamResolution."""
        import ast
        from pathlib import Path

        src = Path("tortoise/selfhost.py").read_text()
        tree = ast.parse(src)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
            elif isinstance(node, ast.Import):
                for n in node.names:
                    imports.add(n.name.split(".")[0])
        banned = {"hosted_api", "supabase", "mcp_auth"}
        assert not (imports & banned), f"banned imports: {imports & banned}"


class TestToolsList:
    def test_tools_list_returns_tools(self, monkeypatch, tmp_path):
        """tools/list works directly (stateless_http) and returns tools (T1.4)."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                      "params": {}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            assert r.status_code in (200, 202)
            assert "result" in r.text or "tools" in r.text


def _parse_sse_json(r):
    """Parse an MCP response body that may be SSE-framed."""
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                import json
                return json.loads(line[len("data: "):])
        return None
    return r.json()


def _tool_result(body):
    """Extract a tool-call result dict from a JSON-RPC body — FastMCP returns
    dict-shaped results inline OR wrapped in content[].text JSON depending on
    the call path; both are handled here."""
    result = body.get("result", {}) if body else {}
    if isinstance(result, dict) and "content" in result:
        # The PAYLOAD is `content[0]`; every other block is appended LAST by the
        # dispatch seam (the #3883 retirement warning, #10084's graph
        # disclosure). Joining all blocks turned a WRITE into `{"text": …}`
        # instead of failing loudly once a disclosure block appeared.
        blocks = [c for c in result["content"] if isinstance(c, dict)]
        text = blocks[0].get("text", "") if blocks else ""
        if text:
            import json
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text}
    return result


def _mcp_post(tc, payload):
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    r = tc.post("/mcp", json=payload, headers=headers)
    return r, _parse_sse_json(r)


def _mcp_post_auth(tc, key, payload):
    """_mcp_post with a static-mode Bearer key (auth_mode="static")."""
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    r = tc.post("/mcp", json=payload, headers=headers)
    return r, _parse_sse_json(r)


def _is_wait_bound_refusal(body) -> bool:
    """Is this JSON-RPC body the #3834 transport-wait-bound REFUSAL?

    The refusal is a caller-visible "this call did not start in time" answer,
    not a tool verdict: the server says so structurally
    (``structuredContent.error.code == -32009``) and flags the payload with
    ``_meta.tortoise_wait_bound``. Any OTHER error shape (a tool's own
    fail-closed dict, a protocol error) is NOT a refusal and must never be
    retried here — retrying it would mask the very answer under test.
    """
    result = (body or {}).get("result") or {}
    meta = result.get("_meta") or {}
    if meta.get("tortoise_wait_bound"):
        return True
    err = (result.get("structuredContent") or {}).get("error") or {}
    return err.get("code") == -32009


def _mcp_tool_call_after_cold_start(tc, key, payload, *, attempts: int = 2):
    """One ``tools/call``, tolerating the transport wait bound ONCE on cold start.

    #3834's transport bound refuses a ``tools/call`` whose dispatch exceeds 10 s
    with the legible ``-32009``/``tortoise_wait_bound`` shape. A FRESH embedded
    graph pays a cold SDK construction + ``_ensure_indexes()`` + the selfhost
    onboarding node inside the tool call, which on the self-hosted ARM pool
    (measured 4.46x a hosted runner per the python-ci leg's own note) can exceed
    that 10 s — measured in run 41964347676 / job 127067312539, where
    ``test_tortoise_health_degraded_matches_http_health`` got the refusal
    instead of its answer while the SAME probe was already stubbed to fail.
    That is a cold-start property of the harness, not a health verdict, so the
    call is retried once and the assertions run on the warm answer.

    Nothing is masked: only the refusal shape is retried, and a refusal that
    survives the retry is returned to the caller like any other body, so a
    genuinely over-bounded tool call still reds its assertion.
    """
    r = body = None
    for _attempt in range(max(1, attempts)):
        r, body = _mcp_post_auth(tc, key, payload)
        if not _is_wait_bound_refusal(body):
            return r, body
    return r, body


def _wait_bound_refusal_body():
    """The #3834 transport-wait-bound refusal, byte-shaped like the server's."""
    return {"jsonrpc": "2.0", "id": 1, "result": {
        "_meta": {"tortoise_wait_bound": True},
        "content": [{"type": "text", "text": "The server's wait budget for this "
                                             "request was exceeded"}],
        "structuredContent": {"error": {
            "code": -32009,
            "message": "wait budget exceeded",
            "data": {"retry_after": 10},
        }},
        "isError": True,
    }}


def _verdict_body(payload: dict):
    import json
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [
        {"type": "text", "text": json.dumps(payload)}]}}


def test_the_cold_start_tolerance_retries_the_refusal_and_never_a_verdict(
        monkeypatch):
    """The wait-bound tolerance is bounded and shape-specific (#10726 `test (b)`).

    Both directions are pinned, because a tolerance that retried ANY body would
    silently turn a wrong verdict into a right-looking one: the refusal shape is
    retried once (and the warm answer is what the assertions see), while a real
    tool verdict is returned on the FIRST call.
    """
    import sys

    mod = sys.modules[__name__]
    calls = []

    def _refusing(tc, key, payload):
        calls.append(payload)
        if len(calls) == 1:
            return object(), _wait_bound_refusal_body()
        return object(), _verdict_body({"status": "degraded"})

    monkeypatch.setattr(mod, "_mcp_post_auth", _refusing)
    _r, body = mod._mcp_tool_call_after_cold_start(
        None, "k", {"params": {"name": "tortoise_health"}})
    assert len(calls) == 2, "the refusal must be retried exactly once"
    assert mod._tool_result(body)["status"] == "degraded"

    calls.clear()

    def _answering(tc, key, payload):
        calls.append(payload)
        return object(), _verdict_body({"status": "degraded"})

    monkeypatch.setattr(mod, "_mcp_post_auth", _answering)
    _r, body = mod._mcp_tool_call_after_cold_start(
        None, "k", {"params": {"name": "tortoise_health"}})
    assert len(calls) == 1, "a real verdict must NOT be retried"
    assert mod._tool_result(body)["status"] == "degraded"


def test_the_health_wait_releases_a_starved_probe_slot(monkeypatch, tmp_path):
    """A slot-starved probe is `unknown`, so the wait RECOVERS instead of expiring.

    #3683 decision `2026-09-30-3683-health-probe-honesty`: a probe that never
    reached the DB is not a verdict. Measured failure this pins — CI run
    41964347676, job 127067312539 (`test (b)`), the required gate on #10726:
    every refresh for 40 s reported ``{'ok': False, 'observed': False, 'error':
    'probe sdk acquisition did not start within 2.0s'}``, so the pre-fix wait
    could only ever raise while the daemon was healthy throughout.

    The slot is held here exactly as a wedged/overrunning probe holds it, and
    the ONE refresh period is shortened so the recovery is a couple of seconds
    rather than the shipped 15 s cadence.

    ⛔ **The hold OUTLIVES the wait's budget, and the re-arm is COUNTED** — both
    are load-bearing, and both were added because the leg had stopped pinning
    the property it is named for (#11023 review, measured). At a 30 s hold
    against the module's 90 s tolerance the holder gave up ~60 s BEFORE the
    deadline: the slot un-starved itself, a later cycle MEASURED, and the final
    `ok is True` passed with `_rearm_stranded_probe` never running — so a
    deleted remedy left this leg green. Holding past the deadline removes that
    escape, and the counter makes the requirement explicit rather than inferred
    from timing: with the slot held the ONLY path to a measured `ok=True` is a
    re-arm (`_stranded_slot_error` classifies every queued refresh as
    discardable). The `finally: release.set()` ends the abandoned holder, which
    is never joined.
    """
    import sys
    import threading

    from tortoise import monitoring as mon

    monkeypatch.setenv("TORTOISE_HEALTH_PROBE_INTERVAL", "1")
    held = threading.Event()
    release = threading.Event()

    def _hold_the_one_shared_slot():
        held.set()
        # abandoned, never joined: the wait must not block on it. It outlasts
        # the wait's own budget on purpose — see `_STARVED_SLOT_HOLD_S`, and
        # `test_the_starved_slot_hold_outlasts_the_wait_budget` pins the coupling.
        release.wait(_STARVED_SLOT_HOLD_S)

    #: ⛔ The re-arm is COUNTED. What that catches is a deleted CALL SITE — and
    #: nothing more, which is what this comment used to over-claim. A NEUTERED
    #: BODY (`_rearm_stranded_probe` monkeypatched to a no-op) still increments,
    #: because the only thing that distinguishes a real re-arm from a no-op is
    #: its EFFECT — the next refresh MEASURING — and that is the `snapshot()["ok"]
    #: is True` assertion below. With the hold past the budget the no-op body
    #: fails at the deadline instead, and the `except` below re-raises that
    #: failure with the count in it, so the diagnosis is never lost.
    #: Same idiom as `test_the_strand_remedy_is_bounded`.
    calls = {"rearms": 0}
    real_rearm = _rearm_stranded_probe

    def _counting_rearm(selfhost_mod):
        # ⛔ AFTER the delegation, not before: a remedy that RAISES was never a
        # re-arm, and counting it first reported one that did not happen.
        real_rearm(selfhost_mod)
        calls["rearms"] += 1

    monkeypatch.setattr(sys.modules[__name__], "_rearm_stranded_probe",
                        _counting_rearm)

    tc = _client_for_env(monkeypatch, tmp_path)
    from tortoise import selfhost

    mon._probe_worker().submit(_hold_the_one_shared_slot)
    assert held.wait(5.0), "the slot holder never started"
    try:
        with tc:
            # ⚠️ NOT the ad-hoc `25.0` this call carried: the wait bound is the
            # module's documented HARNESS tolerance for a loaded runner, which
            # is what `_HEALTH_VERDICT_WAIT_S` (90 s, raised from 40 s after a
            # loaded box expired it on a HEALTHY daemon) exists for. 25 s sat
            # BELOW that documented tolerance and red-ed a required check on a
            # healthy daemon — CI run 42186465222, job 127800485941, `test (d)`:
            # the failure reported `strand re-arms used 2/2` with the snapshot
            # still `{'ok': False, … 'probe in flight (0.0s)'}`, i.e. a probe was
            # MEASURING at the deadline. Two queued cycles (2 × the 2.0 s
            # acquisition budget) plus ONE cold embedded-FalkorDB start is
            # already ~26 s before the runner's own load is counted.
            #
            # ⛔ The budget is NOT the only half of this test's discrimination: a
            # slot held for LESS than the budget un-starves itself, so the wait
            # never has to re-arm and this passes with the remedy deleted — it did,
            # at the 30 s hold this test used to install (PR #11023 review). The
            # hold (`_STARVED_SLOT_HOLD_S`) therefore exceeds this budget, and
            # `test_the_starved_slot_hold_outlasts_the_wait_budget` pins that
            # coupling so it cannot silently drift back. The re-arm COUNT below
            # is the direct form of the same requirement.
            #
            # The remedy's BOUNDEDNESS is pinned where that property belongs —
            # `test_the_strand_remedy_is_bounded`, on its own 3.0 s bound.
            #
            # ⛔ THE DEADLINE IS WHERE A BODY-DELETED RUN FAILS, so the count is
            # re-raised THROUGH it: at `_STARVED_SLOT_HOLD_S` (95 s) the deadline
            # fires first (90 s), and the bare `assert calls["rearms"] >= 1`
            # after this block was therefore UNREACHABLE on exactly the run it was
            # written to diagnose (measured: failed in 94.55 s, inside the wait).
            try:
                _wait_for_probe(selfhost)
            except AssertionError as expired:
                raise AssertionError(
                    f"{expired}\n⛔ the wait expired with re-arms used "
                    f"{calls['rearms']} — with the slot held past the deadline the "
                    f"ONLY path to a measured `ok=True` is `_rearm_stranded_probe`; "
                    f"0 means the remedy was never called, >0 means it was called "
                    f"and did not free the slot (a neutered body)") from expired
    finally:
        release.set()
    assert calls["rearms"] >= 1, (
        "the wait recovered WITHOUT re-arming the stranded slot — with the slot "
        "held past the deadline the only way to a measured `ok=True` is "
        "`_rearm_stranded_probe`, so this leg would pass even if the remedy "
        "were deleted, which is exactly the vacuity #11023's review found")
    assert selfhost._HEALTH_PROBE.snapshot()["ok"] is True


def test_the_starved_slot_hold_outlasts_the_wait_budget():
    """Non-vacuity coupling for `test_the_health_wait_releases_a_starved_probe_slot`.

    That test proves the wait RE-ARMS a stranded slot. It can only do that while
    the slot it installs is STILL HELD at the moment the wait would otherwise
    expire: with `hold < budget` the holder returns first, the next refresh
    MEASURES on the freed slot, and the assertion passes with
    `_rearm_stranded_probe` never called.

    Measured on PR #11023 (fresh-context review): at the 30 s hold this test
    used to install against the 90 s default, the assertion was GREEN as-is AND
    GREEN with the remedy monkeypatched to a no-op — green without its subject.
    This pin holds the coupling, so an edit that re-creates that vacuity goes RED
    here instead of merging silently green.

    ⛔ THREE mutations can break the coupling, and the first cut of this pin saw
    only the third (review at `10fc020353`):
      1. **a LITERAL hold** — `_STARVED_SLOT_HOLD_S = 95.0` stops tracking the
         budget, so a later budget raise silently re-creates the vacuity. Caught
         by requiring the assignment to DERIVE from `_HEALTH_VERDICT_WAIT_S`.
      2. **an explicit `timeout=` at the CALL SITE** — the wait would then be
         bounded by that literal and not by the budget the hold is sized
         against. Caught by requiring the call in that test to take the default.
      3. **raising the budget without the hold** — the ratio test below. ⚠️ This
         is the one that CANNOT happen while the hold is derived (raising the
         budget raises the hold with it), which is why 1 and 2 are the load-
         bearing halves and this is the backstop.
    """
    import ast
    import inspect
    import sys

    assert _STARVED_SLOT_HOLD_S > _HEALTH_VERDICT_WAIT_S, (
        f"the starved-slot hold ({_STARVED_SLOT_HOLD_S}s) must outlast the health "
        f"wait budget ({_HEALTH_VERDICT_WAIT_S}s) — a shorter hold un-starves "
        "itself, and test_the_health_wait_releases_a_starved_probe_slot then "
        "passes with the re-arm remedy deleted (vacuous, not green)"
    )
    #: 1 — the hold is DERIVED, not a literal. A literal is the mutation that
    #: survives every runtime assertion until a later edit moves the budget.
    hold_assignment = None
    for node in ast.parse(inspect.getsource(sys.modules[__name__])).body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "_STARVED_SLOT_HOLD_S"
                        for t in node.targets)):
            hold_assignment = node
    assert hold_assignment is not None, (
        "`_STARVED_SLOT_HOLD_S` is no longer a module-level assignment, so the "
        "derivation this pin reads cannot be found")
    assigned_names = {n.id for n in ast.walk(hold_assignment.value)
                      if isinstance(n, ast.Name)}
    assert "_HEALTH_VERDICT_WAIT_S" in assigned_names, (
        f"`_STARVED_SLOT_HOLD_S` no longer DERIVES from "
        f"`_HEALTH_VERDICT_WAIT_S` (it assigns {ast.unparse(hold_assignment.value)}) "
        f"— a literal hold stops tracking the budget, so the next budget raise "
        f"silently re-creates the vacuity this pin exists to prevent")
    #: 2 — the COUPLE, not the default: that test must take the DEFAULT budget,
    #: so an explicit `timeout=` at its call site is the mutation to refuse.
    holder_source = inspect.getsource(
        test_the_health_wait_releases_a_starved_probe_slot)
    wait_calls = [n for n in ast.walk(ast.parse(holder_source))
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "_wait_for_probe"]
    assert len(wait_calls) == 1, (
        f"expected ONE `_wait_for_probe` call in the starved-slot test, found "
        f"{len(wait_calls)} — re-derive this pin before trusting it")
    assert not wait_calls[0].keywords, (
        "the starved-slot test passes its own budget at the call site "
        f"({ast.unparse(wait_calls[0])}) — the hold is sized against "
        "`_HEALTH_VERDICT_WAIT_S`, so the DEFAULT is the bound, not a copy of it")
    #: 3 — the backstop ratio, plus the default the hold is sized against.
    default = inspect.signature(_wait_for_probe).parameters["timeout"].default
    assert default == _HEALTH_VERDICT_WAIT_S, (
        f"_wait_for_probe's default timeout is {default}, not the "
        f"{_HEALTH_VERDICT_WAIT_S}s budget the hold is sized against — re-derive "
        "_STARVED_SLOT_HOLD_S, or pass the budget explicitly in that test"
    )


class TestHealthTruthMCP:
    """#2202 — tortoise_health reports the same truth as the daemon's /health.

    The onboarding lie: the MCP tool used to probe monitoring's module-global
    SDK handle, which the HTTP daemon never registers (only the stdio
    entrypoint does) — so tortoise_health reported degraded /
    no_sdk_registered / graph_size 0 while GET /health (fresh SDK probe of the
    SAME graph) returned ok. These tests run the REAL selfhost daemon surface
    (auth_mode=static): /health and the MCP tortoise_health tool must agree,
    and mere tool listing must not drag hosted machinery in.
    """

    def test_tortoise_health_ok_matches_http_health(self, monkeypatch, tmp_path):
        """Healthy daemon: tools/call tortoise_health == ok, exactly like
        GET /health — no no_sdk_registered, graph probed."""
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        from tortoise import selfhost

        with tc:
            _wait_for_probe(selfhost)
            health = tc.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            r, body = _mcp_tool_call_after_cold_start(tc, "k", {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "tortoise_health", "arguments": {}},
            })
            assert r.status_code == 200, r.text
            result = _tool_result(body)
            assert result.get("status") == "ok", body
            assert result.get("db", {}).get("ok") is True
            assert result.get("falkordb") == "connected"
            assert "no_sdk_registered" not in str(result)
            assert isinstance(result.get("graph_size"), int)

    def test_tortoise_health_degraded_matches_http_health(self, monkeypatch,
                                                          tmp_path):
        """#2202 pin: degraded is reserved for a REAL probe failure — a dead
        DB flips BOTH /health and the MCP tool to degraded together (same
        probe)."""
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        import tortoise.monitoring as mon
        from tortoise import selfhost

        def _boom_probe(sdk=None, setup_timeout=None, *, acquire=None):
            # probe_db's contract is never-raise: a dead DB is a FAILED probe
            # result, not an exception. Return the degraded shape both /health
            # and metrics() turn into status="degraded". (#3143 widened the
            # signature with optional budget args, #3446 added keyword-only
            # ``acquire=`` — the stub must accept ALL of them. This stub serves
            # BOTH callers: selfhost._probe_db passes ``acquire=``, while
            # metrics() passes an already-acquired ``sdk`` positionally. A
            # signature mismatch raises a TypeError that HealthProbe._run
            # SWALLOWS, so the test below also asserts the error MESSAGE.)
            return {"ok": False, "latency_ms": 0.0, "error": "NXDOMAIN"}

        # probe_db is imported lazily from tortoise.monitoring inside both
        # /health (selfhost.py) and metrics() (the MCP tool) — patch it there.
        monkeypatch.setattr(mon, "probe_db", _boom_probe)
        with tc:
            # Deliberately failing probe: wait only for the verdict to land.
            _wait_for_probe(selfhost, require_ok=False)
            r = tc.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "degraded"
            assert "NXDOMAIN" in r.json()["db"]["error"], (
                "the stub's own error must survive: a swallowed TypeError would "
                "also produce 'degraded', so assert the MESSAGE (#3446)"
            )

            r, body = _mcp_tool_call_after_cold_start(tc, "k", {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "tortoise_health", "arguments": {}},
            })
            assert r.status_code == 200, r.text
            result = _tool_result(body)
            assert result.get("status") == "degraded", body
            assert result.get("db", {}).get("ok") is False

    def test_tools_list_creates_no_stray_tmp_db(self, monkeypatch, tmp_path):
        """#2202 indicator 2: tools/list on the daemon must not open a stray
        embedded DB in $TMPDIR (the 21KB tortoise.db side effect observed
        during mere tool listing). The daemon's own graph lives at
        TORTOISE_DB_PATH (tmp_path here) — nothing may mint a second store in
        the system tempdir. (The hosted_api no-import guarantee is pinned
        unit-level in test_onboarding_gate_short_circuits_on_selfhost — a
        sys.modules diff here would be vacuous once an earlier file in the
        same pytest process imported hosted_api.)"""
        # #3752: discovery is scoped to the private per-session temp root
        # (`scan_root()` refuses the shared system temp dir), never the host
        # tree — a stray-db assertion must not depend on ambient host state.
        from tests._tmpdir_hygiene import scan_root
        tmp = scan_root()
        stray_before = {f for f in os.listdir(tmp) if f.startswith("tortoise.db")}

        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        with tc:
            r, body = _mcp_post_auth(tc, "k", {
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
                "params": {},
            })
            assert r.status_code == 200, r.text
            names = [t["name"] for t in body["result"]["tools"]]
            # #4282: the orientation container is `tortoise_graph_overview`;
            # #3883: `tortoise_health` — and now `tortoise_overview` itself — are
            # RETIRED, callable and warned, never listed.
            assert "tortoise_graph_overview" in names
            assert "tortoise_overview" not in names
            assert "tortoise_health" not in names

        stray_after = {f for f in os.listdir(tmp) if f.startswith("tortoise.db")}
        assert stray_after == stray_before, (
            f"tools/list opened a stray embedded DB in $TMPDIR: "
            f"{stray_after - stray_before}")

    def test_onboarding_gate_short_circuits_on_selfhost_before_hosted_api(
            self, monkeypatch):
        """#2202 indicator 2: the tools/list onboarding-completion gate
        (_team_onboarding_complete) must short-circuit on the SELFHOST team
        id BEFORE importing tortoise.hosted_api — the control-plane read that
        dragged hosted machinery / an embedded registry DB into a mere tool
        listing. Pin the guard ORDER: hosted_api imports are BLOCKED here, and
        the gate still returns False (fail-open: onboarding tools stay
        listed)."""
        import asyncio
        import builtins

        from tortoise import mcp_server as _ms
        from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "tortoise.hosted_api":
                raise AssertionError(
                    "tools/list gate must not import hosted_api on the "
                    "selfhost daemon")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        token = _current_org_id.set(SELFHOST_ORG_ID)
        try:
            # #2924: the gate is ``async`` (its hosted read is offloaded), so it
            # must be awaited — the SELFHOST early return still happens BEFORE
            # any ``tortoise.hosted_api`` import, which is what this pins.
            assert asyncio.run(_ms._org_onboarding_complete()) is False
        finally:
            _current_org_id.reset(token)


class TestOriginProtection:
    def test_origin_bearing_request_ok_for_allowed_origin(self, monkeypatch, tmp_path):
        """allowed_origins pin: Origin http://localhost:8000 passes (T1.2)."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json",
                         "Origin": "http://localhost:8000"},
            )
            assert r.status_code in (200, 202)

    def test_disallowed_origin_rejected(self, monkeypatch, tmp_path):
        """Origin outside the allowlist → 403 (host_origin_protection)."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json",
                         "Origin": "https://evil.example"},
            )
            assert r.status_code == 403



class TestStartupGuard:
    def test_refuses_none_mode_on_non_loopback(self, monkeypatch, tmp_path):
        """Fail-closed (code-review P1): no key + non-loopback bind → SystemExit."""
        import importlib

        monkeypatch.setenv("TORTOISE_DB_URI", "")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "guard.db"))
        monkeypatch.setenv("TORTOISE_API_KEY", "")
        monkeypatch.setenv("TORTOISE_HOST", "0.0.0.0")

        with pytest.raises(SystemExit) as exc:
            importlib.reload(__import__("tortoise.selfhost", fromlist=["app"]))
        assert "REFUSING TO START" in str(exc.value)

    def test_allows_static_mode_on_non_loopback(self, monkeypatch, tmp_path):
        """Key set → auth_mode=static → non-loopback bind is fine."""
        import importlib

        monkeypatch.setenv("TORTOISE_DB_URI", "")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "guard2.db"))
        monkeypatch.setenv("TORTOISE_API_KEY", "k")
        monkeypatch.setenv("TORTOISE_HOST", "0.0.0.0")
        importlib.reload(__import__("tortoise.selfhost", fromlist=["app"]))
        # No exception = guard passed (static mode ok on non-loopback)


class TestSubprocessSmoke:
    def test_python_m_selfhost_real_http(self, tmp_path):
        """T1.4 subprocess smoke: `python -m tortoise.selfhost` on an
        ephemeral port serves /health + MCP initialize over real HTTP."""
        import socket  # noqa: I001
        import subprocess
        import sys
        import time
        import urllib.request
        import urllib.error

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        env = dict(os.environ)
        env["TORTOISE_DB_URI"] = ""
        env["TORTOISE_DB_PATH"] = str(tmp_path / "smoke.db")
        env["TORTOISE_PORT"] = str(port)
        env["TORTOISE_HOST"] = "127.0.0.1"
        env["TORTOISE_API_KEY"] = "smoke-key"
        env["PYTHONPATH"] = os.getcwd()

        proc = subprocess.Popen(
            [sys.executable, "-m", "tortoise.selfhost"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Wait for /health (up to 30s)
            healthy = False
            for _ in range(60):
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                        if r.status == 200:
                            healthy = True
                            break
                except (urllib.error.URLError, ConnectionError, OSError):
                    time.sleep(0.5)
            assert healthy, "selfhost did not become healthy in 30s"

            # Real MCP initialize with the static key (follow 307 redirects
            # — fastmcp streamable-http may redirect POST /mcp to /mcp/).
            import json as _json

            def _post_with_redirect(url, data, headers, redirects=5):
                while redirects > 0:
                    try:
                        req = urllib.request.Request(url, data=data, headers=headers)
                        return urllib.request.urlopen(req, timeout=10)
                    except urllib.error.HTTPError as e:
                        if e.code in (307, 308) and e.headers.get("Location"):
                            url = e.headers["Location"]
                            redirects -= 1
                            continue
                        raise
                raise RuntimeError("too many redirects")

            body = _json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26",
                           "capabilities": {},
                           "clientInfo": {"name": "smoke", "version": "0"}},
            }).encode()
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer smoke-key",
            }
            with _post_with_redirect(f"http://127.0.0.1:{port}/mcp", body, headers) as r:
                assert r.status in (200, 202), f"initialize status {r.status}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ─────────────────────────────────────────────────────────────────────────
# #2179 — selfhost_api._sdk() keepalive check-then-create race
# ─────────────────────────────────────────────────────────────────────────
# The #1475 per-path keepalive anchor (mirror of hosted_api._FALLBACK_
# KEEPALIVE) had the SAME unlocked check-then-create shape hosted_api did
# before #2172: two threads calling _sdk() concurrently on a cold path both
# see an empty dict, both open the same embedded db_path, and the
# setdefault loser is dropped UNCLOSED (daemon socket unlinked mid-request →
# ConnectionError / silent empty-graph reads). All-async today, but
# TestClient/portal threads and any to_thread pool make it a REAL thread
# race exactly as the hosted lane became. _SELFHOST_LOCK serializes the
# miss/stale path (re-check + evict + create + insert) so exactly ONE
# anchor per path is created. Mirrors test_hosted_api.py's #2172 probe
# (keep in sync): a counting __init__ parks ~300ms to widen the pre-fix
# window, then asserts 1 anchor + N fresh SDKs, no closed-aware orphan,
# and a sentinel written via the anchor reads back through every returned
# SDK (decoupled-server guard). The third iteration (round 2, 0-based)
# pre-seeds a PATH-DRIFTED anchor (bound to seed.db, stored under the
# round key) so the in-lock stale evict + recreate branch is exercised
# under contention too.
def test_sdk_concurrent_first_calls_single_anchor(monkeypatch):
    import threading
    import time
    import uuid

    import tortoise.selfhost_api as sha
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setenv("TORTOISE_DB_URI", "")  # force embedded mode
    ns = f"selfhost-race-{uuid.uuid4().hex}"
    _orig_init = TortoiseSDK.__init__

    def _make_counter(target_db: str):
        made: list = []

        def _counting_init(self, db_path=None, *, namespace=None, **kwargs):
            if db_path == target_db:
                made.append(self)
                time.sleep(0.3)  # widen the pre-fix race window (docstring)
            _orig_init(self, db_path, namespace=namespace, **kwargs)

        return made, _counting_init

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        try:
            for _round in range(3):
                if _round == 2:
                    # Seed an anchor bound to a DIFFERENT path and pre-store
                    # it under this round's key — the path-drift stale case
                    # (_anchor_usable compares the anchor's INTERNAL path to
                    # the requested db_path, independent of the dict key).
                    seed_db = os.path.join(td, "seed.db")
                    monkeypatch.setenv("TORTOISE_DB_PATH", seed_db)
                    sha._sdk()  # creates + stores a connected seed anchor
                    stale = sha._SELFHOST_KEEPALIVE.pop(seed_db, None)
                    assert stale is not None, "seed anchor not stored"
                    db = os.path.join(td, f"round-{_round}.db")
                    monkeypatch.setenv("TORTOISE_DB_PATH", db)
                    sha._SELFHOST_KEEPALIVE[db] = stale  # pre-store: both threads see it
                else:
                    db = os.path.join(td, f"round-{_round}.db")
                    monkeypatch.setenv("TORTOISE_DB_PATH", db)
                    stale = None
                constructed, counting_init = _make_counter(db)
                monkeypatch.setattr(TortoiseSDK, "__init__", counting_init)
                results: list = []
                errors: list = []
                barrier = threading.Barrier(2)

                def _go(_barrier=barrier, _results=results, _errors=errors):
                    try:
                        _barrier.wait(timeout=10)
                        _results.append(sha._sdk())
                    except Exception as e:
                        _errors.append(e)

                threads = [threading.Thread(target=_go, daemon=True)
                           for _ in range(2)]
                for t in threads:
                    t.start()
                try:
                    for t in threads:
                        t.join(timeout=60)
                    for t in threads:
                        assert not t.is_alive(), (
                            f"round {_round}: thread hung — the #2179 "
                            f"regression would deadlock here")
                    assert not errors, f"round {_round}: thread raised: {errors!r}"
                    anchors = [v for k, v in sha._SELFHOST_KEEPALIVE.items()
                               if k == db]
                    assert anchors, (
                        f"round {_round}: no anchor stored for {db!r}")
                    anchor = anchors[0]
                    assert anchor._proj is not None, "anchor not connected"
                    assert sha._anchor_usable(anchor, db) is True
                    # Lower bound (not equality): a legitimate env-fault
                    # recovery (the lock winner's eager connect fails, the
                    # loser's in-lock re-check evicts it and recreates) adds
                    # a second, CLOSED anchor construction on correct code.
                    assert len(constructed) >= 1 + len(results), (
                        f"round {_round}: expected >= 1 anchor + 1 per "
                        f"returned fresh SDK = {1 + len(results)} "
                        f"constructions, got {len(constructed)} — an anchor "
                        f"or fresh SDK was never built")
                    # Fresh-per-request contract: returned SDKs are NOT the
                    # shared anchor (a regression returning it would share
                    # mutable session state across requests — the #493 class).
                    assert all(s is not anchor for s in results), (
                        f"round {_round}: a returned SDK IS the shared anchor")
                    assert len({id(s) for s in results}) == 2, (
                        f"round {_round}: returned SDKs are not distinct")
                    returned = {id(s) for s in results}
                    # Closed-aware orphan check: every construction must be
                    # the stored anchor, a returned SDK, or a CLOSED evicted
                    # candidate (the env-fault recovery path closes what it
                    # evicts). Pre-fix the loser was dropped OPEN — unclosed
                    # and unaccounted — which is exactly the leak.
                    unclosed_orphans = [
                        inst for inst in constructed
                        if not getattr(inst, "_t_closed", False)
                        and inst is not anchor
                        and id(inst) not in returned]
                    assert not unclosed_orphans, (
                        f"round {_round}: {len(unclosed_orphans)} UNCLOSED "
                        f"construction(s) neither stored nor returned — a "
                        f"dropped-unclosed SDK (the #2179 race) leaks here")
                    # Data-plane probe: a node written through the anchor's
                    # graph must be readable via every returned SDK — proves
                    # they attach to the anchor's data-bearing daemon.
                    sentinel = f"race-{ns}-{_round}"
                    anchor._get_proj().g.query(
                        "CREATE (n:RaceSentinel {rk: $rk})", {"rk": sentinel})
                    for sdk in results:
                        seen = sdk._get_proj().g.query(
                            "MATCH (n:RaceSentinel {rk: $rk}) RETURN count(n)",
                            {"rk": sentinel}).result_set[0][0]
                        assert seen == 1, (
                            f"round {_round}: returned SDK missed the "
                            f"anchor's write — decoupled server")
                    if _round == 2:
                        assert anchor is not stale, "stale anchor re-served"
                        assert stale is not None and stale._t_closed is True, (
                            "stale anchor evicted but never closed — the "
                            "dropped-unclosed daemon leak #2179 prevents")
                        assert stale._proj is None
                finally:
                    # Deterministic round teardown (close-then-pop, #1950):
                    # shut every daemon we started. Under FIXED code nothing
                    # was dropped (the orphans assert proved it).
                    for sdk in results:
                        try:  # noqa: SIM105
                            sdk.close()
                        except Exception:
                            pass
                    leftover = sha._SELFHOST_KEEPALIVE.pop(db, None)
                    if leftover is not None:
                        try:  # noqa: SIM105
                            leftover.close()
                        except Exception:
                            pass
        finally:
            for k in list(sha._SELFHOST_KEEPALIVE):
                leftover = sha._SELFHOST_KEEPALIVE.pop(k, None)
                if leftover is not None:
                    try:  # noqa: SIM105
                        leftover.close()
                    except Exception:
                        pass
