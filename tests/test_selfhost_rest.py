"""Self-host REST surface tests (#525).

Covers /v1/points CRUD, /v1/search, /v1/dream + static-key auth on the
self-host daemon's REST router (auth_mode none + static).
"""
from __future__ import annotations

import logging
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest


def _client_for_env(monkeypatch, tmp_path, **env):
    from starlette.testclient import TestClient

    if "TORTOISE_DB_URI" not in env:
        monkeypatch.setenv("TORTOISE_DB_URI", "")  # force embedded
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "rest.db"))

    import importlib

    from tortoise import selfhost
    from tortoise import selfhost_api as _sha

    # #1512: the anchor dict is module-level and survives importlib.reload
    # (reload only re-executes selfhost, not its cached selfhost_api import) —
    # clear it so each test's fresh TORTOISE_DB_PATH gets its own anchor and
    # prior tests' pinned servers are released at session end (not leaked).
    _sha._SELFHOST_KEEPALIVE.clear()
    importlib.reload(selfhost)
    return TestClient(selfhost.app)


class TestPointsCRUD:
    def test_create_and_list(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/points", json={"content": "rest test point", "kind": "statement"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["id"]
            assert body["content"] == "rest test point"

            r2 = tc.get("/v1/points")
            assert r2.status_code == 200
            ids = [p["id"] for p in r2.json()]
            assert body["id"] in ids

    def test_get_by_id(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            created = tc.post("/v1/points", json={"content": "by-id", "kind": "statement"}).json()
            r = tc.get(f"/v1/points/{created['id']}")
            assert r.status_code == 200
            assert r.json()["id"] == created["id"]

    def test_missing_id_404(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.get("/v1/points/nonexistent-id")
            assert r.status_code == 404

    def test_invalid_kind_400(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/points", json={"content": "x", "kind": "not-a-real-kind"})
            assert r.status_code == 422  # pydantic validator rejects

    def _persisted_props(self, pid: str) -> dict:
        """Read the stored node props for `pid`.

        The selfhost REST read surface (list + by-id) is deliberately
        narrow (``PointResponse`` = id/content/kind/created_at), so it cannot
        show arbitrary props; read them straight from the graph the route
        wrote to (same TORTOISE_DB_PATH).
        """
        from tortoise import selfhost_api as _sha

        sdk = _sha._sdk()
        try:
            return dict(sdk.get_point(pid) or {})
        finally:
            sdk.close()

    def test_confidence_and_author_persist(self, monkeypatch, tmp_path):
        """#4032: same silent-drop class as hosted_api — `confidence` /
        `authoredBy` must be stored, never dropped while the write reports ok."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={
                    "content": "selfhost confidence round trip",
                    "kind": "hypothesis",
                    "confidence": 0.8,
                    "authoredBy": "research-skill",
                },
            )
            assert r.status_code == 200, r.text
            props = self._persisted_props(r.json()["id"])
            assert props.get("confidence") == 0.8, props
            assert props.get("authoredBy") == "research-skill", props

    def test_confidence_zero_persists_not_dropped(self, monkeypatch, tmp_path):
        # 0.0 is FALSY — a truthiness guard at the boundary would drop it.
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={
                    "content": "selfhost zero confidence",
                    "kind": "hypothesis",
                    "confidence": 0.0,
                },
            )
            assert r.status_code == 200, r.text
            props = self._persisted_props(r.json()["id"])
            assert props.get("confidence") == 0.0, props

    def test_out_of_range_confidence_is_rejected_not_dropped(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={"content": "selfhost bad confidence", "kind": "statement", "confidence": 5},
            )
            assert r.status_code == 422, r.text

    def test_credibility_calibrates_the_point(self, monkeypatch, tmp_path):
        """#1088: the self-host REST write surface can AUTHOR a baseline —
        the parity of the hosted_api.CreatePointRequest fix. Without it a
        self-host client writes a LIVE point the fail-closed calibration
        gate then makes `/v1/dream` refuse, with no way to make it EP-able."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={"content": "selfhost credibility round trip",
                      "kind": "statement", "credibility": "gold"},
            )
            assert r.status_code == 200, r.text
            props = self._persisted_props(r.json()["id"])
            assert props.get("baseline_set") is True, props
            assert props.get("baseline_source") == "set-by-author", props

    def test_credibility_on_a_dedup_hit_calibrates_the_point(self, monkeypatch, tmp_path):
        """#7980: a dedup hit is the DEFAULT path on this surface
        (``CreatePointRequest.dedup`` defaults True, unlike the hosted
        mirror), so a client re-writing identical content got a 200 back on a
        point that could never be made EP-able — ``/v1/dream`` then refused
        the graph (the fail-closed #344/#1157 gate).

        Mutations that red this: dropping the baseline application in
        ``create_point``'s dedup branch (the reported bug), and turning the
        self-host ``dedup`` default off (the request below never names it).
        """
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            body = {"content": "selfhost dedup-hit credibility",
                    "kind": "statement"}
            first = tc.post("/v1/points", json=body)
            assert first.status_code == 200, first.text
            pid = first.json()["id"]
            assert not self._persisted_props(pid).get("baseline_set")
            second = tc.post("/v1/points", json={**body, "credibility": "gold"})
            assert second.status_code == 200, second.text
            assert second.json()["id"] == pid, "must dedup, not duplicate"
            props = self._persisted_props(pid)
            assert props.get("baseline_set") is True, props
            assert props.get("baseline_source") == "set-by-author", props
            # #398 (owner ruling 2026-10-12): ONE weak evidence body, kept as the
            # `credibility` label — not the word's ladder prior (gold ⇒ 10.0).
            # This passed before only because the dedup-hit path carried the
            # defect (the word's ladder prior, no label).
            from tortoise.source_credibility import AUTHOR_ASSERTION_WEAK_PC
            assert props.get("credibility") == "T0", props
            assert props.get("ep_alpha") == 1.0 + AUTHOR_ASSERTION_WEAK_PC, props

    def test_bad_credibility_is_rejected_not_500(self, monkeypatch, tmp_path):
        # A ladder word the store cannot resolve is a client error — 4xx, not
        # the route's catch-all 500.
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={"content": "selfhost bad credibility",
                      "kind": "statement", "credibility": "platinum"},
            )
            assert r.status_code == 422, r.text


class TestSearch:
    def test_search_finds_point(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            tc.post("/v1/points", json={"content": "unique-searchable-phrase-alpha", "kind": "decision"})
            r = tc.get("/v1/search", params={"q": "unique-searchable-phrase-alpha"})
            assert r.status_code == 200
            hits = [p for p in r.json() if "unique-searchable-phrase-alpha" in p["content"]]
            assert hits
            # FTS result shape (point_kind) must map to the response kind field
            assert hits[0]["kind"] == "decision"


class TestStaticAuth:
    def test_no_key_401(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="rest-secret")
        with tc:
            r = tc.post("/v1/points", json={"content": "x", "kind": "statement"})
            assert r.status_code == 401

    def test_wrong_key_401(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="rest-secret")
        with tc:
            r = tc.post("/v1/points", json={"content": "x", "kind": "statement"},
                        headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401

    def test_correct_key_200(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="rest-secret")
        with tc:
            r = tc.post("/v1/points", json={"content": "authed", "kind": "statement"},
                        headers={"Authorization": "Bearer rest-secret"})
            assert r.status_code == 200, r.text


class TestSelfHostJournal8497:
    """#8497 — the self-host deployment must have a RECORD, not only a cache.

    The defect (re-measured on the head): `config._resolve_event_log_path`
    returns `None` unless `TORTOISE_EVENT_LOG_BASE_DIR` is set, nothing in the
    self-host deployment set it, and **`selfhost_api._sdk` built every SDK with
    no `event_log_path`** — so even setting the variable armed the MCP door
    (`mcp_auth._get_org_sdk` already threads it) and left `/v1/*` writing an
    UNRECORDED graph: `_journal_backed` False, §17.1 inert, `rebuild` with
    nothing to replay. A partial journal looks like a record and is not one.

    What each case pins (the value that makes it fail, and why it is reachable):

    * ``test_default_arms_a_real_journal_when_the_data_root_is_a_mount`` — FAILS
      on the pre-fix head with ``AttributeError`` (no arming seam existed at
      all). Reachable: a mounted data root is the shape `Dockerfile.selfhost` +
      `docker-compose.yml` produce.
    * ``test_explicit_base_dir_wins_over_the_default`` — FAILS while the default
      overrides an operator's own value.
    * ``test_unwritable_default_refuses_loudly_and_arms_nothing`` — FAILS while
      an unwritable default is silent (the state must be a loud refusal, never a
      quiet durability-shaped claim). Reachable without root: a FILE where the
      journal dir should be makes ``makedirs`` raise for any uid.
    * ``test_no_persistent_mount_arms_nothing`` — FAILS while existence alone
      (rather than a mount) gates the default; the image creates `/data` at
      BUILD time, so an existence gate would arm an ephemeral journal that dies
      with the container. Reachable: dev/CI/test lanes have no mount.
    * ``test_rest_door_carries_the_journal_and_arms_the_guard`` — FAILS while
      `_sdk()` omits ``event_log_path`` (the pre-fix state: a real REST write
      lands no record and a direct fold folds instead of raising).
    * ``test_every_selfhost_sdk_construction_carries_the_journal`` — the
      inventory guard: the #8497 defect WAS one door silently omitting the
      argument, so a new unarmed door must red.
    * ``test_the_explicit_probe_runs_once_per_value_not_per_request`` — FAILS
      while the explicit (shipped-compose) branch re-probes on every call.
    * ``test_persistent_dir_sees_a_same_filesystem_bind_mount`` /
      ``test_an_existing_non_mount_root_says_it_armed_nothing`` — FAIL while the
      gate is ``ismount`` alone and the declined default stays silent.
    """

    def _mounted_data_root(self, tmp_path, monkeypatch, *, mounted=True) -> str:
        """A data root whose ``_persistent_dir`` answer the test controls."""
        from tortoise import config

        root = tmp_path / "data"
        root.mkdir()
        monkeypatch.setattr(config, "_persistent_dir", lambda _p: mounted)
        return str(root)

    def test_persistent_dir_is_a_mount_not_a_directory(self, tmp_path):
        """The gate itself — pinned on the REAL function, no monkeypatch.

        Every arming case above fakes ``_persistent_dir``, so without this pin a
        regression of its body from ``os.path.ismount`` to ``os.path.isdir``
        leaves them all green while T5 returns: the self-host image creates
        ``/data`` at BUILD time, so an existence gate arms a journal on the
        container's ephemeral layer — a record that dies with the container and
        is indistinguishable from a real one.
        """
        from tortoise import config

        assert config._persistent_dir("/") is True, (
            "`/` is a mount point on every POSIX platform — if this is False the "
            "persistence gate is not asking about mounts at all")
        assert config._persistent_dir(str(tmp_path)) is False, (
            "a plain directory on the container's writable layer must NOT count "
            "as persistent — an existence gate is the #8497 T5 defect")

    def test_mountinfo_parser_reads_the_mount_point_field(self):
        """The mount-table probe's parser (the bind-mount half of the gate).

        ``os.path.ismount`` alone misses a same-filesystem bind mount, so the
        gate consults ``/proc/self/mountinfo``; this pins the field and the
        escaped-character decoding so a malformed parse cannot silently
        disarm that second probe.
        """
        from tortoise import config

        line = "36 35 0:32 / /data\\040volume rw,relatime - ext4 /dev/sda1 rw"
        assert config._parse_mountinfo(line) == frozenset({"/data volume"})
        # Hostile/absent input degrades to "no mounts", never to a false mount.
        assert config._parse_mountinfo("") == frozenset()
        assert config._parse_mountinfo("garbage line") == frozenset()

    def test_persistent_dir_sees_a_same_filesystem_bind_mount(
            self, tmp_path, monkeypatch):
        """A bind mount on the SAME filesystem is persistent but not detected
        by ``ismount`` — the gate must still arm on it.

        Reproduced shape (Linux/CPython 3.12): ``mount --bind /srv/tortoise
        /data`` has ``st_dev`` equal to its parent's and a different inode, so
        ``os.path.ismount`` returns False even though the mount table names it.
        The operator gets a durable /data and would otherwise be silently
        journal-less — the #8526 review P2/P3-2 miss.
        """
        from tortoise import config

        root = tmp_path / "bind-mounted"
        root.mkdir()
        monkeypatch.setattr(config.os.path, "ismount", lambda _p: False)
        monkeypatch.setattr(
            config, "_mount_points",
            lambda: frozenset({os.path.realpath(str(root))}))

        assert config._persistent_dir(str(root)) is True, (
            "a same-filesystem bind mount is persistent, but `ismount` reads it "
            "as ephemeral — the mount table must be consulted too")

    def test_default_arms_a_real_journal_when_the_data_root_is_a_mount(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = self._mounted_data_root(tmp_path, monkeypatch)

        from tortoise import config

        base = config.ensure_selfhost_event_log(data_root=root)
        assert base == os.path.join(root, "events"), base
        assert os.path.isdir(base)
        assert os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] == base
        # The ONE derivation now resolves a real path for the self-host org
        # graph — the property `rebuild` and §17.1 both depend on.
        assert config._resolve_event_log_path(
            namespace="selfhost", graph_name=None) == os.path.join(
                base, "org_selfhost", "events.jsonl")

    def test_explicit_base_dir_wins_over_the_default(self, tmp_path, monkeypatch):
        explicit = tmp_path / "operator-chosen"
        monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(explicit))
        root = self._mounted_data_root(tmp_path, monkeypatch)

        from tortoise import config

        assert config.ensure_selfhost_event_log(data_root=root) == str(explicit)
        # The operator's path is authoritative: the default was never created.
        assert not (tmp_path / "data" / "events").exists()

    def test_the_explicit_probe_runs_once_per_value_not_per_request(
            self, monkeypatch, tmp_path):
        """The memo must cover the EXPLICIT branch — the SHIPPED path.

        ``docker-compose.yml`` sets ``TORTOISE_EVENT_LOG_BASE_DIR``, so every
        ``/v1/*`` request, every self-host MCP tool call and every readiness
        probe reaches the explicit branch. Before the fix the memo was only
        consulted while the env was UNSET, so each of those paid a real
        ``makedirs`` + ``mkstemp`` + ``unlink`` on the journal volume — the cost
        ``_SELFHOST_JOURNAL_ARMED``'s docstring says the memo exists to avoid
        (#8526 review P2).
        """
        from tortoise import config

        explicit = str(tmp_path / "operator-chosen")
        monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", explicit)
        probes: list[str] = []
        real = config._probe_journal_dir

        def _spy(base: str) -> bool:
            probes.append(base)
            return real(base)

        monkeypatch.setattr(config, "_probe_journal_dir", _spy)

        assert [config.ensure_selfhost_event_log() for _ in range(3)] == [
            explicit] * 3
        assert probes == [explicit], (
            "the explicit branch re-probed the journal dir on every call — the "
            "memo is dead on the shipped compose deployment (#8526)")

        # ...and a CHANGED value IS probed: the memo is keyed by value, not by
        # "have we probed anything yet".
        other = str(tmp_path / "second-choice")
        monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", other)
        assert config.ensure_selfhost_event_log() == other
        assert probes == [explicit, other]

    def test_the_default_arm_probe_runs_once_per_root_not_per_request(
            self, monkeypatch, tmp_path):
        """The DEFAULT arm hands its value to the explicit-probe memo.

        Arming writes ``TORTOISE_EVENT_LOG_BASE_DIR`` itself, so from the
        SECOND call onward the function takes the EXPLICIT branch with the value
        it just wrote. If the memo does not learn that value, the per-request
        caller re-probes the very directory the arm already probed — the default
        deployment keeps paying the ``makedirs`` + ``mkstemp`` + ``unlink`` the
        #8526 review P2 measured ("default-arm call + 4 repeats -> 5 probes").
        """
        from tortoise import config

        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = self._mounted_data_root(tmp_path, monkeypatch)
        probes: list[str] = []
        real = config._probe_journal_dir

        def _spy(base: str) -> bool:
            probes.append(base)
            return real(base)

        monkeypatch.setattr(config, "_probe_journal_dir", _spy)
        armed = config.ensure_selfhost_event_log(data_root=root)
        assert armed == os.path.join(root, "events"), armed
        assert [config.ensure_selfhost_event_log(data_root=root)
                for _ in range(3)] == [armed] * 3
        assert probes == [armed], (
            "the default arm re-probed the directory it had itself armed — the "
            "explicit-probe memo did not learn the value the arm wrote "
            "(#8526 review P2)")

    def test_a_cached_arm_re_establishes_the_env_the_derivation_reads(
            self, monkeypatch, tmp_path):
        """A memo hit must not say "armed" while the derivation reads ``None``.

        The default arm writes ``TORTOISE_EVENT_LOG_BASE_DIR`` itself, and the
        memo is consulted only while the var is UNSET — so a caller that clears
        the var after arming (a test's monkeypatch teardown, an ops harness) got
        the cached base back while ``_resolve_event_log_path()`` — the ONE
        derivation every door reads — returned ``None``. This function said a
        journal was armed and the SDK built from it was journal-less, silently
        (#8526 review P3).
        """
        from tortoise import config

        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = self._mounted_data_root(tmp_path, monkeypatch)
        armed = config.ensure_selfhost_event_log(data_root=root)
        assert armed == os.path.join(root, "events"), armed

        # The var is cleared out from under the memo (teardown/harness shape).
        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        assert config.ensure_selfhost_event_log(data_root=root) == armed
        assert os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] == armed, (
            "a memo hit returned the armed base without re-establishing the "
            "env `_resolve_event_log_path` reads — the two disagree, so the "
            "door builds a journal-less SDK behind an 'armed' claim (#8526 P3)")
        assert config._resolve_event_log_path(
            namespace="selfhost", graph_name=None) == os.path.join(
                armed, "org_selfhost", "events.jsonl")

    def test_unwritable_default_refuses_loudly_and_arms_nothing(
            self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = self._mounted_data_root(tmp_path, monkeypatch)
        # A FILE where the journal directory must go: ``makedirs`` raises
        # ``FileExistsError`` for any uid (no chmod/root dependency).
        (tmp_path / "data" / "events").write_text("not a directory")

        from tortoise import config

        with caplog.at_level(logging.ERROR):
            assert config.ensure_selfhost_event_log(data_root=root) is None
        # Honest refusal: nothing is armed, so nothing can pretend to record.
        assert "TORTOISE_EVENT_LOG_BASE_DIR" not in os.environ
        assert config._resolve_event_log_path(
            namespace="selfhost", graph_name=None) is None
        assert any("journal unavailable" in r.message for r in caplog.records), (
            "an unwritable self-host journal must be LOUD — a silent degrade "
            "is the durability claim this issue closes (#8497)")

    def test_no_persistent_mount_arms_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = self._mounted_data_root(tmp_path, monkeypatch, mounted=False)

        from tortoise import config

        assert config.ensure_selfhost_event_log(data_root=root) is None
        assert "TORTOISE_EVENT_LOG_BASE_DIR" not in os.environ
        # No filesystem side effect at all on a non-persistent root.
        assert not (tmp_path / "data" / "events").exists()

    def test_an_existing_non_mount_root_says_it_armed_nothing(
            self, tmp_path, monkeypatch, caplog):
        """A root that EXISTS but is not a mount must not be silent.

        `Dockerfile.selfhost` bakes ``/data`` at build time, so a bare
        ``docker run`` and a same-filesystem bind mount both land on this
        branch. Silence there is indistinguishable from a correctly
        journal-less dev lane — the "deployment is journal-less and nobody
        knows" state #8497 closes (#8526 review P2/P3-2).
        """
        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = tmp_path / "data"
        root.mkdir()

        from tortoise import config

        monkeypatch.setattr(config, "_persistent_dir", lambda _p: False)
        with caplog.at_level(logging.WARNING):
            assert config.ensure_selfhost_event_log(data_root=str(root)) is None

        assert any("no default armed" in r.message for r in caplog.records), (
            "a root that exists but is not a persistent mount armed nothing "
            "silently — the operator cannot tell it apart from a dev lane")

    def test_rest_door_carries_the_journal_and_arms_the_guard(
            self, monkeypatch, tmp_path):
        events = tmp_path / "events"
        tc = _client_for_env(
            monkeypatch, tmp_path, TORTOISE_EVENT_LOG_BASE_DIR=str(events))
        with tc:
            r = tc.post("/v1/points",
                        json={"content": "journaled via REST", "kind": "statement"})
            assert r.status_code == 200, r.text

            from tortoise import selfhost_api as _sha

            sdk = _sha._sdk()
            try:
                expected = os.path.join(str(events), "org_selfhost", "events.jsonl")
                assert sdk._event_log_path == expected, sdk._event_log_path
                assert sdk._get_event_log() is not None
                proj = sdk._get_proj()
                assert proj._journal_backed is True, (
                    "the REST door built a journal-less SDK — every write on "
                    "/v1/* lands no record and §17.1 is inert (#8497)")
                from tortoise.projection import DirectCacheWriteRefused

                with pytest.raises(DirectCacheWriteRefused):
                    proj.apply({"type": "PointAdded", "point": {"id": "x"}})
            finally:
                sdk.close()

            # The record is REAL, not just configured: the POST above wrote it.
            journal = events / "org_selfhost" / "events.jsonl"
            assert journal.exists(), sorted(p.name for p in events.iterdir())
            assert "PointAdded" in journal.read_text(encoding="utf-8")

    def test_mcp_selfhost_lane_arms_the_journal(self, tmp_path, monkeypatch):
        """The MCP door must arm the default itself.

        `tortoise serve --http` is a documented self-host daemon that serves
        `org_selfhost` through this door and never imports `tortoise.selfhost`
        (the module that arms the docker daemon) — so without arming here that
        daemon resolved a `None` journal and wrote an UNRECORDED graph, the same
        #8497 defect on a third surface.
        """
        from types import SimpleNamespace

        monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
        root = self._mounted_data_root(tmp_path, monkeypatch)

        from tortoise import config, mcp_auth

        # The deployment default is resolved from this module global.
        monkeypatch.setattr(config, "_SELFHOST_DATA_ROOT", root)
        monkeypatch.setattr(mcp_auth, "TortoiseSDK",
                            lambda **kwargs: SimpleNamespace(**kwargs))
        import tortoise.onboarding.selfhost as _osh

        monkeypatch.setattr(_osh, "ensure_selfhost_onboarding_once",
                            lambda *a, **k: None)

        token = mcp_auth._current_org_id.set(mcp_auth.SELFHOST_ORG_ID)
        try:
            sdk = mcp_auth._get_org_sdk()
        finally:
            mcp_auth._current_org_id.reset(token)

        expected_base = os.path.join(root, "events")
        assert os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] == expected_base
        assert sdk.event_log_path == os.path.join(
            expected_base, "org_selfhost", "events.jsonl"), sdk.event_log_path

    def test_every_selfhost_sdk_construction_carries_the_journal(self):
        """The door inventory for the WHOLE self-host surface.

        #8497 existed because ONE door silently omitted ``event_log_path=``. A
        behavioural test on ``_sdk()`` cannot see a construction added beside
        it, so the inventory guard is what catches the NEXT one. It spans every
        module that builds a self-host SDK — ``selfhost_api`` (REST),
        ``selfhost`` (daemon start + read-only probes) and ``mcp_auth`` (the MCP
        door) — because a scan of ``selfhost_api`` alone leaves an omission in
        the other two green (#8526 review P2/P3-1). Scope, stated honestly: it
        matches the two node kinds the codebase uses (``TortoiseSDK(...)`` and
        ``module.TortoiseSDK(...)``); an aliased import (`from tortoise.sdk
        import TortoiseSDK as T`) would escape it — the #7951 door-inventory
        pattern's declared escape surface.
        """
        import inspect

        from tortoise import mcp_auth, selfhost, selfhost_api

        armed: list[int] = []
        unarmed: dict[str, list[int]] = {}
        for mod in (selfhost_api, selfhost, mcp_auth):
            mod_armed, mod_unarmed = _journal_inventory(inspect.getsource(mod))
            armed += mod_armed
            if mod_unarmed:
                unarmed[mod.__name__] = mod_unarmed
        assert armed, (
            "inventory guard lost its subject — no TortoiseSDK construction "
            "found in the self-host surface")
        assert not unarmed, (
            f"TortoiseSDK construction(s) omit event_log_path at {unarmed} — "
            "that door writes an UNRECORDED graph, which is the #8497 defect "
            "(a partial journal looks like a record)")

    def test_inventory_reds_on_a_none_valued_journal_argument(self):
        """``event_log_path=None`` is an UNARMED door, not a keyword to count.

        The guard's first shape matched only the keyword's PRESENCE, so
        ``TortoiseSDK(..., event_log_path=None)`` — precisely
        the unarmed-door class the guard exists for — passed it (#8526 review
        P2).
        """
        assert _journal_inventory(
            'TortoiseSDK(namespace="test_probe")')[1] == [1]
        assert _journal_inventory(
            'TortoiseSDK(namespace="test_probe", event_log_path=None)')[1] == [1]
        assert _journal_inventory(
            'TortoiseSDK(graph_name=ns,\n'
            '           event_log_path=_journal_path_for(ns))')[0] == [1]


def _journal_inventory(source: str) -> tuple[list[int], list[int]]:
    """(armed, unarmed) line numbers of every ``TortoiseSDK(...)`` in ``source``.

    Unarmed means the call has NO ``event_log_path`` keyword, or its value is
    the literal ``None`` — the keyword's presence alone is not the signal.
    """
    import ast

    def _is_sdk_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "TortoiseSDK"
        if isinstance(func, ast.Attribute):
            return func.attr == "TortoiseSDK"
        return False

    def _carries_journal(node: ast.Call) -> bool:
        for kw in node.keywords:
            if kw.arg != "event_log_path":
                continue
            if isinstance(kw.value, ast.Constant):
                return kw.value.value is not None
            return True
        return False

    armed: list[int] = []
    unarmed: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not _is_sdk_call(node):
            continue
        (armed if _carries_journal(node) else unarmed).append(node.lineno)
    return armed, unarmed
