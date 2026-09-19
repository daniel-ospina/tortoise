"""#3677 — the analytics path must RESOLVE to the real store under the
environment production actually defines.

Behavioural, not a source scan.

The production Fly app (``fly secrets list -a tortoise-y4mjjq``) provides
``SUPABASE_URL`` and ``SUPABASE_SERVICE_ROLE_KEY`` — and **never** the legacy
``SUPABASE_SERVICE_KEY``. Each test below therefore imports the REAL handler,
EXECUTES it with that exact env shape, and asserts on:

(a) the RESOLVED destination — full-string equality of the URL actually hit
    after interpolation/joins, so a change to the host, the prefix, the path
    or the endpoint reds regardless of what the source string looks like; and
(b) the value the destination RECEIVES — the POSTed body deep-equal to the
    event the handler built, and the credential actually presented on the wire.

Why not a scan: a scan for the string ``"SUPABASE_SERVICE_ROLE_KEY"`` reports
on a *spelling*. The defect (#3677) was that the handler RESOLVED to
``~/.tortoise/analytics_fallback.jsonl`` — a path on an ephemeral Fly VM — so
every event was silently discarded. 2,464 events were found in exactly that
file on the production machine, and ``analytics_events`` had none of them.

Both halves of lane B7's measurement chain are covered: the WRITE
(``hosted_api._track_analytics_event``) and the READ
(``tools/capture_cost_report.fetch_rows``), which is the leg that turns stored
rows into the per-session cost evidence.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

# The production env shape from `fly secrets list -a tortoise-y4mjjq`: the two
# NAMES are what matters. The values here are test fixtures — PROD_URL matches
# the public Supabase project URL documented in the repo's env examples, and no
# secret value was ever read out of production.
PROD_URL = "https://ybetwichurajbfswfeqa.supabase.co"
PROD_KEY = "prod-service-role-key"
LEGACY_KEY = "legacy-service-key"

# Exactly the keys `_track_analytics_event` builds — a body with an extra or
# missing key is a different shape and must not pass.
_EVENT_KEYS = {"org_id", "event_name", "properties", "created_at"}


class _Resp:
    status_code = 201

    def __init__(self, payload=None):
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _CapturingClient:
    """Stands in for ``httpx.Client``; records what the handler actually sent.

    The handler imports ``httpx`` lazily inside the function, so patching the
    attribute on the module object is what it will resolve at call time.
    """

    posts: ClassVar[list] = []
    gets: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        type(self).posts.append((url, kwargs))
        return _Resp()

    def get(self, url, **kwargs):
        type(self).gets.append((url, kwargs))
        return _Resp(payload=[])


def _capture(monkeypatch):
    _CapturingClient.posts = []
    _CapturingClient.gets = []
    _CapturingClient.instances = []
    monkeypatch.setattr("httpx.Client", _CapturingClient)
    return _CapturingClient


def _prod_env(monkeypatch):
    """Exactly what production carries: URL + the canonical ROLE key."""
    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _fallback_at(monkeypatch, tmp_path):
    import tortoise.hosted_api as ha

    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))
    return fallback


# #3820 (cycle-2 P1): the alert-leg isolation this file used to carry inline
# (`_never_reach_a_real_alert_channel`) is now the shared autouse fixture
# `tests/conftest.py::_analytics_alert_isolation`, so it covers every test in
# the suite — not just this file.


# ── WRITE path: hosted_api._track_analytics_event ──────────────────────────


def test_write_path_resolves_to_the_analytics_store_under_production_env(
        monkeypatch, tmp_path):
    """The regression test for #3677.

    Production's env shape must resolve the request to the analytics store.
    On unmutated ``origin/main`` this fails: the handler reads only the legacy
    name, finds nothing, and appends to the ephemeral JSONL fallback instead.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    client = _capture(monkeypatch)
    fallback = _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event(
        "org-1", "capture_cost",
        {"session_id": "s-1", "calls": 2, "not_registered": "must be dropped"})

    assert client.posts, (
        "the analytics write path did not resolve to the analytics store under "
        "production's env shape (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY)")
    assert len(client.posts) == 1, "exactly one event must produce one request"

    url, kwargs = client.posts[0]

    # (a) the RESOLVED destination — read after every join/interpolation.
    assert url == f"{PROD_URL}/rest/v1/analytics_events", url

    # The credential actually presented, not the one in the source.
    assert kwargs["headers"]["apikey"] == PROD_KEY
    assert kwargs["headers"]["Authorization"] == f"Bearer {PROD_KEY}"

    # (b) the value the destination receives.
    body = kwargs["json"]
    assert set(body) == _EVENT_KEYS, body
    assert body["org_id"] == "org-1"
    assert body["event_name"] == "capture_cost"
    assert body["properties"] == {"session_id": "s-1", "calls": 2}
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None

    # Nothing was diverted to the ephemeral disk.
    assert not fallback.exists(), (
        "the event was ALSO written to the ephemeral JSONL fallback — the "
        "production machine's rootfs, which is discarded on replacement")


def test_write_path_accepts_the_legacy_name_as_well(monkeypatch, tmp_path):
    """The selfhost / legacy env shape must keep working — the fix widens the
    accepted names, it does not swap one hardcoded name for another."""
    from tortoise import hosted_api as ha

    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", LEGACY_KEY)
    client = _capture(monkeypatch)
    fallback = _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-2", "mcp_tool_call", {"tool_name": "x"})

    url, kwargs = client.posts[0]
    assert url == f"{PROD_URL}/rest/v1/analytics_events"
    assert kwargs["headers"]["Authorization"] == f"Bearer {LEGACY_KEY}"
    assert kwargs["json"]["properties"] == {"tool_name": "x"}
    assert not fallback.exists()


def test_write_path_falls_back_only_when_truly_unconfigured(monkeypatch,
                                                            tmp_path):
    """The fallback is a legitimate branch — it must stay reachable when no
    service key of either name exists (selfhost with no Supabase at all).
    Pinned so a fix cannot silently delete the offline path.
    """
    from tortoise import hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    client = _capture(monkeypatch)
    fallback = _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-3", "onboarding_complete", {"step": "done"})

    assert not client.posts, "no key configured — nothing should be posted"
    assert fallback.exists()
    written = json.loads(fallback.read_text(encoding="utf-8").strip())
    assert set(written) == _EVENT_KEYS, written
    assert written["org_id"] == "org-3"
    assert written["event_name"] == "onboarding_complete"
    assert written["properties"] == {"step": "done"}
    assert datetime.fromisoformat(written["created_at"]).tzinfo is not None


def test_write_path_needs_a_key_not_just_a_url(monkeypatch, tmp_path):
    """A URL WITHOUT a key must not be treated as configured: `apikey: ""` /
    `Bearer ` would be sent, and a 2xx would `return` without ever reaching the
    JSONL — the #3677 loss class. The key half of `if url and key` is
    load-bearing.

    Mutation that reds this test: ``if url and key:`` -> ``if url:``.

    #3820 (P1-2): this shape is a HALF-configured sink, so it is now classified
    as a ``fallback`` DEGRADATION with reason ``supabase_env_incomplete`` —
    never ``unconfigured``, which would have made the alert blind to exactly
    this case. The alert assertion for that classification lives in
    ``tests/test_analytics_fallback_alert.py::test_t13_half_configured_env_is_a_
    degradation``.
    """
    from tortoise import hosted_api as ha

    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    client = _capture(monkeypatch)
    fallback = _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-14", "mcp_tool_call", {"tool_name": "nk"})

    assert not client.posts, "no key — nothing may be posted"
    assert fallback.exists(), "the event was dropped instead of falling back"
    written = json.loads(fallback.read_text(encoding="utf-8").strip())
    assert written["org_id"] == "org-14"


def test_write_path_never_raises_when_the_fallback_dir_cannot_be_created(
        monkeypatch):
    """The function's central contract is *never raises* — the onboarding flow
    and the GitHub OAuth callback call it with no guard of their own.

    Directory creation used to sit OUTSIDE the best-effort block, so a
    read-only HOME raised straight out of it. Mutation that reds this test:
    hoist ``os.makedirs`` back out of its ``try``.
    """
    import os as _os

    from tortoise import hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", None)

    def _readonly_home(path, **kwargs):
        raise OSError("read-only HOME")

    monkeypatch.setattr(_os, "makedirs", _readonly_home)

    # No pytest.raises: the assertion IS that this returns.
    ha._track_analytics_event("org-4", "onboarding_error",
                              {"error_type": "capture_failed"})


def test_write_path_falls_back_to_jsonl_when_the_post_fails(monkeypatch,
                                                            tmp_path):
    """A transport failure must not drop the event silently on the floor: the
    handler still must not raise, and the event must reach the local JSONL.

    Mutation that reds this test: delete the ``except Exception: pass  # fall
    through to JSONL`` guard around the POST.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    fallback = _fallback_at(monkeypatch, tmp_path)

    class _BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr("httpx.Client", _BoomClient)

    ha._track_analytics_event("org-5", "mcp_tool_call", {"tool_name": "y"})

    assert fallback.exists(), "the event was dropped instead of falling back"
    written = json.loads(fallback.read_text(encoding="utf-8").strip())
    assert written["org_id"] == "org-5"
    assert written["properties"] == {"tool_name": "y"}


def test_write_path_never_raises_on_malformed_properties(monkeypatch, tmp_path):
    """The contract is never-raise, so a non-dict ``properties`` must be
    dropped, not raise ``AttributeError`` from ``.items()``.

    Mutation that reds this test: delete the ``isinstance`` guard.
    """
    from tortoise import hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    fallback = _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-7", "mcp_tool_call", ["not", "a", "dict"])

    written = json.loads(fallback.read_text(encoding="utf-8").strip())
    assert written["properties"] == {}


def test_fallback_path_resolves_to_the_documented_default(monkeypatch,
                                                          tmp_path):
    """#3677's evidence — and the recovery instructions built from it — are
    keyed to an EXACT path: ``~/.tortoise/analytics_fallback.jsonl`` on the
    machine that ran the writer. Pin the resolved default so it cannot drift
    silently, and prove it is really resolved from HOME rather than a literal.

    Mutation that reds this test: rename the file (e.g.
    ``analytics_fallback_v2.jsonl``) or move the ``.tortoise`` directory.
    """
    from tortoise import hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", None)
    monkeypatch.setenv("HOME", str(tmp_path))

    ha._track_analytics_event("org-6", "mcp_tool_call", {"tool_name": "z"})

    resolved = tmp_path / ".tortoise" / "analytics_fallback.jsonl"
    assert str(resolved) == ha._ANALYTICS_FALLBACK_PATH, (
        ha._ANALYTICS_FALLBACK_PATH)
    assert resolved.exists()


@pytest.mark.parametrize("status", [301, 401, 500])
def test_write_path_falls_back_to_jsonl_when_the_post_is_rejected(monkeypatch,
                                                                 tmp_path,
                                                                 status):
    """Any non-2xx from PostgREST does NOT raise, so an uninspected response
    silently discarded the event — the #3677 loss class, reachable whenever the
    key is revoked or INSERT-denied (a 401 raises nothing), and for a 3xx
    because ``httpx`` does not follow redirects by default: the redirect is
    treated as delivered while no row was inserted. It must reach the local
    JSONL instead of vanishing.

    Mutation that reds this test: widen the accepted band, e.g.
    ``200 <= resp.status_code < 300`` -> ``resp.status_code < 400``, or delete
    the status check (return unconditionally after the POST).
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    fallback = _fallback_at(monkeypatch, tmp_path)

    class _RejectingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            return type("R", (), {"status_code": status})()

    monkeypatch.setattr("httpx.Client", _RejectingClient)

    ha._track_analytics_event("org-9", "mcp_tool_call", {"tool_name": "r"})

    assert fallback.exists(), "a rejected write must not discard the event"
    written = json.loads(fallback.read_text(encoding="utf-8").strip())
    assert written["org_id"] == "org-9"
    assert written["properties"] == {"tool_name": "r"}


def test_write_path_prefers_the_canonical_key_over_a_stale_legacy_one(
        monkeypatch, tmp_path):
    """Both names present: the CANONICAL key must be the one presented. A stale
    legacy value left over in the environment must not win — it would 401 and
    (before the status check) lose the event.

    Mutation that reds this test: swap the order of
    ``supabase_control._SERVICE_KEY_ENV``.
    """
    from tortoise import hosted_api as ha

    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", LEGACY_KEY)
    client = _capture(monkeypatch)
    _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-10", "mcp_tool_call", {"tool_name": "p"})

    _url, kwargs = client.posts[0]
    assert kwargs["headers"]["Authorization"] == f"Bearer {PROD_KEY}"


def test_write_path_bounds_the_post_timeout(monkeypatch, tmp_path):
    """A timeout short enough to expire in production makes EVERY write fall
    through to the ephemeral JSONL — #3677's symptom by another route. Pin a
    bounded (not exact) timeout so tuning stays possible but a degenerate value
    cannot ship.

    Mutation that reds this test: ``timeout=5`` -> ``timeout=0.000001``, or
    dropping the ``timeout`` kwarg entirely (=> no bound at all).
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    client = _capture(monkeypatch)
    _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-8", "mcp_tool_call", {"tool_name": "t"})

    timeout = client.instances[0].init_kwargs.get("timeout")
    # The value sent must equal the module's bound, and that bound must be sane
    # — so tuning the constant tunes the call and a drifted value reds. (A
    # value-identical literal would pass this assertion; only a differing one
    # is caught.)
    assert timeout == ha._ANALYTICS_POST_TIMEOUT_S, (
        f"call site drifted from the module constant: {timeout!r}")
    assert isinstance(timeout, (int, float)), (
        "the analytics POST must carry an explicit timeout")
    assert 1 <= timeout <= 30, f"degenerate POST timeout: {timeout!r}"


def test_fallback_appends_rather_than_truncating(monkeypatch, tmp_path):
    """On the machine that ran the writer, this JSONL is the ONLY copy of the
    events: #3677's recovery procedure reads 2,464 of them out of exactly this
    file. A truncating open would destroy every earlier event on the next
    write — the same loss the fix exists to stop.

    Mutation that reds this test: ``open(path, "a")`` -> ``open(path, "w")``.
    """
    from tortoise import hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    fallback = _fallback_at(monkeypatch, tmp_path)

    ha._track_analytics_event("org-11", "mcp_tool_call", {"tool_name": "a1"})
    ha._track_analytics_event("org-12", "mcp_tool_call", {"tool_name": "a2"})

    lines = [ln for ln in fallback.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert len(lines) == 2, f"expected 2 appended events, found {len(lines)}"
    first, second = (json.loads(ln) for ln in lines)
    assert first["properties"] == {"tool_name": "a1"}
    assert second["properties"] == {"tool_name": "a2"}


def test_write_path_never_raises_when_the_jsonl_append_fails(monkeypatch,
                                                            tmp_path):
    """The LAST leg of the never-raise contract is the JSONL append itself: a
    full disk or a read-only mount must not propagate out of the handler into
    the GitHub OAuth callback.

    Mutation that reds this test: delete the ``except Exception: pass`` around
    the final ``open(...)``/``write``.
    """
    import builtins

    from tortoise import hosted_api as ha

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    fallback = _fallback_at(monkeypatch, tmp_path)

    real_open = builtins.open

    def _no_disk(file, *args, **kwargs):
        if str(file) == str(fallback):
            raise OSError("no space left on device")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _no_disk)

    # No pytest.raises: the assertion IS that this returns.
    ha._track_analytics_event("org-13", "mcp_tool_call", {"tool_name": "f"})


# ── READ path: tools/capture_cost_report.fetch_rows ────────────────────────


def test_read_path_resolves_to_the_analytics_store_under_production_env(
        monkeypatch):
    """The cost report's live pull is the leg that produces lane B7's
    per-session cost evidence. Under production's env shape it must reach the
    store rather than refuse to run."""
    from tools import capture_cost_report as report

    _prod_env(monkeypatch)
    client = _capture(monkeypatch)

    report.fetch_rows(1)

    assert client.gets, (
        "the cost report refused to pull under production's env shape — "
        "no request was made")
    url, kwargs = client.gets[0]
    assert url == f"{PROD_URL}/rest/v1/analytics_events", url
    assert kwargs["headers"]["apikey"] == PROD_KEY
    assert kwargs["headers"]["Authorization"] == f"Bearer {PROD_KEY}"
    assert kwargs["params"]["event_name"] == "eq.capture_cost"
    assert kwargs["params"]["select"] == "org_id,properties,created_at"
    # The window must actually DERIVE from `days`. Mutation that reds these:
    # delete the `created_at` filter (the pull then ignores `days` and returns
    # the whole table) or drop the ordering.
    stamp = kwargs["params"]["created_at"]
    assert stamp.startswith("gte."), stamp
    age = datetime.now(UTC) - datetime.fromisoformat(stamp[4:])
    assert timedelta(days=1) - timedelta(minutes=5) <= age <= (
        timedelta(days=1) + timedelta(minutes=5)), age
    assert kwargs["params"]["order"] == "created_at.desc"


def test_read_path_accepts_the_legacy_name_as_well(monkeypatch):
    from tools import capture_cost_report as report

    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", LEGACY_KEY)
    client = _capture(monkeypatch)

    report.fetch_rows(1)

    url, kwargs = client.gets[0]
    assert url == f"{PROD_URL}/rest/v1/analytics_events"
    assert kwargs["headers"]["Authorization"] == f"Bearer {LEGACY_KEY}"


def test_read_path_prefers_the_canonical_key_and_bounds_its_timeout(
        monkeypatch):
    """The reader must present the canonical key when both names are set (a
    stale legacy value must not win) and must bound its pull timeout — an
    unbounded or degenerate pull is how a report silently reads an empty
    window.

    Mutation that reds this test: swap ``_SERVICE_KEY_ENV`` order, or drop the
    timeout passed to ``httpx.Client``.
    """
    from tools import capture_cost_report as report

    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", LEGACY_KEY)
    client = _capture(monkeypatch)

    report.fetch_rows(1)

    url, kwargs = client.gets[0]
    assert url == f"{PROD_URL}/rest/v1/analytics_events"
    assert kwargs["headers"]["Authorization"] == f"Bearer {PROD_KEY}"
    timeout = client.instances[0].init_kwargs.get("timeout")
    assert isinstance(timeout, (int, float)) and 1 <= timeout <= 60, timeout


def test_read_path_window_tracks_the_days_argument(monkeypatch):
    """The window must be DERIVED from ``days``, not a constant — a fixed
    window silently reports the wrong period while looking healthy.

    Mutation that reds this test: ``_iso_days_ago(days)`` ->
    ``_iso_days_ago(1)``.
    """
    from tools import capture_cost_report as report

    _prod_env(monkeypatch)
    client = _capture(monkeypatch)

    report.fetch_rows(1)
    report.fetch_rows(7)

    def _age(kwargs):
        return datetime.now(UTC) - datetime.fromisoformat(
            kwargs["params"]["created_at"][4:])

    one = _age(client.gets[0][1])
    seven = _age(client.gets[1][1])
    assert timedelta(days=7) - timedelta(minutes=5) <= seven <= (
        timedelta(days=7) + timedelta(minutes=5)), seven
    assert seven - one > timedelta(days=6) - timedelta(seconds=1), (one, seven)


def test_read_path_still_refuses_when_truly_unconfigured(monkeypatch):
    """The offline paths (--jsonl/--json) exist because this refusal must be
    loud, not a silent empty report."""
    from tools import capture_cost_report as report

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    with pytest.raises(ValueError, match="must both be set"):
        report.fetch_rows(1)


def test_read_path_refuses_a_url_without_a_key(monkeypatch):
    """A URL without a key is not configured: PostgREST would reject the pull,
    and silently reporting an empty window is worse than refusing.

    Mutation that reds this test: ``if not url or not key:`` -> ``if not url:``.
    """
    from tools import capture_cost_report as report

    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    with pytest.raises(ValueError, match="must both be set"):
        report.fetch_rows(1)
