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
        """The door inventory for the self-host surface.

        #8497 existed because ONE door silently omitted ``event_log_path=``. A
        behavioural test on ``_sdk()`` cannot see a construction added beside
        it, so the inventory guard is what catches the NEXT one. Scope, stated
        honestly: it matches the two node kinds the codebase uses
        (``TortoiseSDK(...)`` and ``module.TortoiseSDK(...)``); an aliased import
        (`from tortoise.sdk import TortoiseSDK as T`) would escape it — the
        #7951 door-inventory pattern's declared escape surface.
        """
        import ast
        import inspect

        from tortoise import selfhost_api

        tree = ast.parse(inspect.getsource(selfhost_api))

        def _is_sdk_call(node: ast.AST) -> bool:
            if not isinstance(node, ast.Call):
                return False
            func = node.func
            if isinstance(func, ast.Name):
                return func.id == "TortoiseSDK"
            if isinstance(func, ast.Attribute):
                return func.attr == "TortoiseSDK"
            return False

        calls = [node for node in ast.walk(tree) if _is_sdk_call(node)]
        assert calls, "inventory guard lost its subject — no TortoiseSDK construction found"
        unarmed = [
            node.lineno for node in calls
            if not any(kw.arg == "event_log_path" for kw in node.keywords)
        ]
        assert not unarmed, (
            f"TortoiseSDK construction(s) at line(s) {unarmed} omit "
            "event_log_path — that door writes an UNRECORDED graph, which is "
            "the #8497 defect (a partial journal looks like a record)")
