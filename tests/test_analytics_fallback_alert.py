"""#3820 — a degraded analytics sink must be VISIBLE, not silent.

``hosted_api._track_analytics_event`` can deliver an event to Supabase, divert
it to the local JSONL fallback, or lose it entirely. Before #3820 **all four
exits returned bare ``None``**, so ``delivered`` and ``lost`` were
indistinguishable from outside the function — which is why #3677 was
discoverable only by a human reading a filesystem on the production machine
(2,464 events / 568,889 bytes on an ephemeral Fly rootfs).

Behavioural, not a source scan. Every test below EXECUTES the real handler over
a stubbed ``httpx.Client``, a ``tmp_path`` fallback file and a fake AlertStore,
and asserts on the RETURNED outcome, the recorded counter, and the calls the
fake store received. No ``inspect.getsource``, no ``read_text`` on source, no
string scan.

The outcome contract under test (D1) is a CLOSED vocabulary:

* ``"supabase"``     — delivered (2xx).
* ``"fallback"``     — configured but degraded; the event is on local disk.
* ``"unconfigured"`` — no sink by design (selfhost/dev). NEVER an alert: an
                       incident here would fire on every such process and the
                       real degradation would then be ignored.
* ``"dropped"``      — the event reached no sink at all (#3677's loss class).

Scope deviation, recorded deliberately (see T7): the #3820 scope doc's test
table asks T7 to assert ``detail["fallback"] >= 50`` after 50 degraded writes.
The incident is filed ONCE per episode, at the alert threshold, and the real
``AlertStore`` serialises the detail into the GitHub issue body at filing time
(``alert_store._body``) — so an incident filed on event 3 can never carry the
count from event 50. Asserting 50 would pin a value the production code cannot
honestly produce (only a fake store holding a live reference could show it).
T7 therefore asserts the count AT FILING plus the process aggregate, which is
what the signal actually carries.

Applied on the review pass (T13–T15 are new there):

* **P1-1/D5a** — the alert channel is NOT gated on ``BACKUP_SWEEP_ENABLED``.
  T14 drives a degradation with the sweep OFF and asserts a real issue is
  filed; T15 pins what is genuinely left (no alert CREDENTIALS) and that the
  counter still runs.
* **P1-2** — a HALF-configured env (``SUPABASE_URL`` without a key, or vice
  versa — #3677's own shape) is a ``fallback`` degradation with reason
  ``supabase_env_incomplete``, never ``unconfigured``. T13.
* **P2-1/D2** — the alert trigger is a STREAK of ``_ANALYTICS_FALLBACK_ALERT_
  AFTER`` consecutive degraded writes, not "the first fallback after any
  success" (which alerted on a single 5s timeout). Deferred deliberately and
  recorded: D5b's ABSENCE half (a sink that silently stops emitting) needs a
  heartbeat this sink does not emit — see the constants' comment.
* **P2-2** — the episode latch is set only on a dispatch that did NOT raise.
* **P2-3** — every non-dispatch leg (the outcome counter, and the detail build
  that takes the alert lock) now sits inside a never-raise guard. T16.
"""

import json
import logging
import os
from pathlib import Path
from typing import ClassVar

import pytest

# The production env shape from `fly secrets list -a tortoise-y4mjjq`: the two
# NAMES are what matter. Values are fixtures — PROD_URL is the public Supabase
# project URL documented in the repo's env examples; no secret was read out of
# production.
PROD_URL = "https://ybetwichurajbfswfeqa.supabase.co"
PROD_KEY = "prod-service-role-key"

FALLBACK_NAME = "analytics_fallback.jsonl"


class _Resp:
    """Minimal stand-in for ``httpx.Response`` (only ``status_code`` is read)."""

    def __init__(self, status: int):
        self.status_code = status


class _StubClient:
    """Stands in for ``httpx.Client``: records the POST, then replies.

    ``_track_analytics_event`` imports ``httpx`` lazily inside the function, so
    patching the ``httpx.Client`` attribute is what it resolves at call time.
    """

    status: ClassVar[int] = 201
    raises: ClassVar[BaseException | None] = None
    posts: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        type(self).posts.append((url, kwargs))
        if type(self).raises is not None:
            raise type(self).raises
        return _Resp(type(self).status)


class _FakeStore:
    """Records the incident lifecycle the write path drives."""

    def __init__(self, raises_on_open: bool = False):
        self.calls: list[tuple] = []
        self.raises_on_open = raises_on_open

    def open_incident(self, kind, org_id="", detail=None):
        self.calls.append(("open", kind, org_id, detail))
        if self.raises_on_open:
            raise RuntimeError("alert store down")
        return True

    def resolve_incident(self, kind, org_id=""):
        self.calls.append(("resolve", kind, org_id, None))
        return True

    @property
    def opens(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "open"]

    @property
    def resolves(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "resolve"]

    @property
    def log(self) -> list[str]:
        return [c[0] for c in self.calls]


# ── fixtures / helpers ──────────────────────────────────────────────────────


def _reset_write_path(monkeypatch) -> None:
    """Reset the MID-TEST-mutable write-path state (T8/T10 call it directly).

    The episode latch, the degraded streak and the once-per-process resolve
    probe are process globals that a single test mutates across phases; the
    outcome counter is a process-global Prometheus ``Counter`` whose label
    children must be cleared so a phase's counts mean exactly what that phase
    executed.

    The local JSONL sink path and the in-process ``_ANALYTICS_COUNTS`` dict are
    deliberately NOT touched here: they are owned suite-wide by
    ``tests/conftest.py::_analytics_alert_isolation``, and resetting them here
    would clobber that isolation (a ``None`` path resolves the REAL
    ``~/.tortoise/analytics_fallback.jsonl``). ``test_t19`` pins both.
    """
    import tortoise.hosted_api as ha
    import tortoise.monitoring as mon

    monkeypatch.setattr(ha, "_ANALYTICS_INCIDENT_OPEN", False)
    monkeypatch.setattr(ha, "_ANALYTICS_DEGRADED_STREAK", 0)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_PROBED", False)
    mon.ANALYTICS_OUTCOME_COUNT.clear()
    _StubClient.status = 201
    _StubClient.raises = None
    _StubClient.posts = []


@pytest.fixture(autouse=True)
def _fresh_write_path(monkeypatch):
    """Per-test isolation for the write path's process state."""
    _reset_write_path(monkeypatch)


def _prod_env(monkeypatch) -> None:
    """Exactly what production carries: URL + the canonical ROLE key."""
    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _no_env(monkeypatch) -> None:
    """No sink configured at all — the selfhost/dev/test shape."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _fallback_at(monkeypatch, tmp_path) -> Path:
    import tortoise.hosted_api as ha

    fallback = tmp_path / FALLBACK_NAME
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))
    return fallback


def _stub_http(monkeypatch, status: int = 201, raises=None):
    """Patch ``httpx.Client`` with the recording stub."""
    _StubClient.status = status
    _StubClient.raises = raises
    monkeypatch.setattr("httpx.Client", _StubClient)
    return _StubClient


def _store(monkeypatch, store=None) -> _FakeStore:
    """Install the fake (or ``None``) alert store via the #3820 seam.

    Tests patch ``_analytics_alert_store`` — never ``_alert_store_from`` — so
    no test can reach R2, GitHub or ``ops/`` on disk.
    """
    import tortoise.hosted_api as ha

    store = _FakeStore() if store is None else store
    monkeypatch.setattr(ha, "_analytics_alert_store", lambda: store)
    return store


def _fail_appends_to(monkeypatch, path) -> None:
    """Make ``open()`` raise for the fallback file only (T5/T11/T12)."""
    import builtins

    real_open = builtins.open
    target = str(path)

    def _open(file, *args, **kwargs):
        if str(file) == target:
            raise OSError("no space left on device")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)


def _outcomes() -> dict[str, int]:
    """The real, process-level outcome counter snapshot (#3820 item 10)."""
    import tortoise.monitoring as mon

    return mon.analytics_outcome_counts()


def _written(fallback: Path) -> dict:
    import json

    return json.loads(fallback.read_text(encoding="utf-8").strip())


# ── T1–T12 ──────────────────────────────────────────────────────────────────


def test_t1_healthy_write_returns_supabase(monkeypatch, tmp_path):
    """T1 — a 2xx write is DELIVERED, and nothing else happens.

    RED mutation: make the success exit return the default ``"fallback"``
    instead of ``"supabase"``.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)

    outcome = ha._track_analytics_event("org-1", "capture_cost", {"calls": 2})

    assert outcome == "supabase"
    assert len(_StubClient.posts) == 1, "exactly one event → one request"
    assert _StubClient.posts[0][0] == f"{PROD_URL}/rest/v1/analytics_events"
    assert not fallback.exists(), "a delivered write must not touch the disk"
    assert store.opens == [], "a delivered write must not FILE an incident"
    # cycle-4 P1-1: the FIRST delivered write of a process probes for a stale
    # open incident exactly once (the resolve is durable across a restart), so
    # the store is touched once — never per event, and never with an `open`.
    assert store.resolves == [
        ("resolve", ha._ANALYTICS_INCIDENT_KIND, "", None)]
    assert _outcomes() == {"supabase": 1}


@pytest.mark.parametrize("status", [301, 401, 500])
def test_t2_rejected_write_returns_fallback(monkeypatch, tmp_path, status):
    """T2 — a non-2xx does not raise, so an uninspected response silently
    discarded the event (the #3677 loss class, reachable whenever the key is
    revoked or INSERT-denied). The outcome must say ``fallback`` and the event
    must be on disk.

    RED mutation: widen the accepted band to ``resp.status_code < 400`` — a
    301, which ``httpx`` does not follow by default and which inserted no row,
    then reports ``"supabase"`` — or delete the status check entirely, which
    does the same for a 401/500.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=status)
    fallback = _fallback_at(monkeypatch, tmp_path)
    _store(monkeypatch)

    outcome = ha._track_analytics_event("org-2", "mcp_tool_call",
                                        {"tool_name": "t"})

    assert outcome == "fallback"
    assert fallback.exists(), "a rejected write must not discard the event"
    assert _written(fallback)["org_id"] == "org-2"
    assert _outcomes() == {"fallback": 1}


def test_t3_transport_failure_returns_fallback(monkeypatch, tmp_path):
    """T3 — a raising ``client.post`` (timeout, DNS, TLS) degrades to the JSONL
    with the ``fallback`` outcome.

    RED mutation: return early from the ``except`` branch WITHOUT writing the
    JSONL (``except Exception: return None``) — the event reaches no sink, is
    never counted, and the returned value is not the declared outcome. (The
    pre-review note claimed a missing outcome default leaks ``"supabase"``
    here; it does not — an empty ``except`` falls through to the terminal
    return, so that was a fictional mutation.)

    A second mutation reds here as well: drop the P2-1 streak (alert on the
    first ``fallback``) → the ``store.calls == []`` assertion fails, which is
    the pin that one 5s timeout must not open an issue.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)

    outcome = ha._track_analytics_event("org-3", "mcp_tool_call",
                                        {"tool_name": "y"})

    assert outcome == "fallback"
    assert fallback.exists()
    assert _written(fallback)["properties"] == {"tool_name": "y"}
    assert _outcomes() == {"fallback": 1}
    assert store.calls == [], (
        "one fallback with no success before it is below the D2 threshold — "
        "a single 5s timeout must not open an issue")


def test_t4_unconfigured_is_not_degradation(monkeypatch, tmp_path):
    """T4 — no sink configured (selfhost/dev/test) is NOT a degradation.

    The event still lands in the local JSONL — that *is* the sink — but no
    incident may be filed, or every such process alerts constantly and the real
    outage is drowned out.

    RED mutation: collapse the no-sink case into ``"fallback"`` (the issue
    body's 3-value form). The alert then fires here by construction.
    """
    from tortoise import hosted_api as ha

    _no_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)

    outcome = ha._track_analytics_event("org-4", "onboarding_complete",
                                        {"step": "done"})

    assert outcome == "unconfigured"
    assert not _StubClient.posts, "no key configured — nothing may be posted"
    assert fallback.exists(), "the JSONL is the intended sink here"
    assert _written(fallback)["event_name"] == "onboarding_complete"
    assert store.calls == [], (
        "an unconfigured sink is by design, not a degradation — filing here "
        "would make the alert a false-alarm generator")
    assert _outcomes() == {"unconfigured": 1}


def test_t5_jsonl_append_failure_is_dropped(monkeypatch, tmp_path):
    """T5 — the event reached NO sink: counted, and alerted with the reason.

    RED mutation: restore ``except Exception: pass`` on the append — no
    outcome, no count, no alert, which is exactly the silence #3677 was.

    D5 mutations (the reason this test carries a SENTINEL value): the incident
    detail must not carry event CONTENT. The pre-review assertion checked for
    the literal ``"mcp_tool_call"`` — but that is the EVENT NAME, not user
    content, so threading ``event["properties"]`` into the detail left it
    green. ``answer``/``questions``/``error_type`` are the user-derived keys in
    ``_ALLOWED_ANALYTICS_PROPS``, so a distinctive USER VALUE is the assertion
    that has teeth: the properties below carry ``SENTINEL_LEAK`` and it must
    not appear anywhere in the filed detail.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)
    _fail_appends_to(monkeypatch, fallback)

    outcome = ha._track_analytics_event(
        "org-5", "mcp_tool_call", {"answer": "SENTINEL_LEAK"})

    assert outcome == "dropped"
    assert _outcomes() == {"dropped": 1}
    assert len(store.opens) == 1, "an unrecoverable loss must alert at once"
    _, kind, org_id, detail = store.opens[0]
    assert kind == ha._ANALYTICS_INCIDENT_KIND
    assert org_id == ""
    assert detail["dropped"] >= 1, detail
    assert detail["reason"] == "fallback_append_failed", detail
    # D5: counts and reason codes only — never event content. The sentinel is a
    # USER-DERIVED value, which is the class D5 is about (the event NAME is not
    # user content, and asserting on it proved nothing).
    assert "SENTINEL_LEAK" not in str(detail), detail
    assert set(detail) == {"outcome", "reason", "fallback", "dropped",
                           "supabase", "unconfigured"}, detail
    assert PROD_KEY not in str(detail) and PROD_URL not in str(detail)


def test_t6_fallback_dir_unavailable_is_dropped(monkeypatch, tmp_path):
    """T6 — the fourth silent path: ``os.makedirs`` fails, so the event has
    nowhere to go. It must be counted as ``dropped`` and must not raise.

    RED mutation: the bare ``return`` at the makedirs failure (the pre-#3820
    code) returns the default ``"fallback"`` — or ``None`` — for a write that
    reached no sink and was never counted.
    """
    from tortoise import hosted_api as ha

    _no_env(monkeypatch)
    _fallback_at(monkeypatch, tmp_path)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", None)
    store = _store(monkeypatch)

    def _readonly_home(path, **kwargs):
        raise OSError("read-only HOME")

    monkeypatch.setattr(os, "makedirs", _readonly_home)

    # No pytest.raises: the assertion IS that this returns.
    outcome = ha._track_analytics_event("org-6", "onboarding_error",
                                        {"error_type": "capture_failed"})

    assert outcome == "dropped"
    assert _outcomes() == {"dropped": 1}
    assert len(store.opens) == 1
    assert store.opens[0][3]["reason"] == "fallback_dir_unavailable"


def test_t7_incident_is_never_per_event(monkeypatch, tmp_path):
    """T7 — 50 degraded writes produce ONE incident, subject-less.

    RED mutations:
      (i)  delete the ``_ANALYTICS_INCIDENT_OPEN`` latch → 50 ``open_incident``
           calls, i.e. an R2 PUT + a GitHub search + Telegram per event;
      (ii) pass ``org_id`` instead of ``""`` → the degradation fans out into
           one issue per tenant.

    Deviation from the scope doc recorded here: the doc's table asks for
    ``detail["fallback"] >= 50``. The incident is filed ONCE, at the alert
    threshold, and ``AlertStore._body`` serialises the detail into the GitHub
    issue body at that moment — so the filed detail carries the count AT
    FILING (3), and 50 is not a value production can produce. The process
    aggregate is asserted separately below, which is where the magnitude lives.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)

    for _ in range(50):
        assert ha._track_analytics_event("org-7", "mcp_tool_call",
                                         {"tool_name": "t"}) == "fallback"

    assert len(store.opens) == 1, (
        f"{len(store.opens)} open_incident calls for one degradation episode "
        "— the alert must be gated, not per event")
    _, kind, org_id, detail = store.opens[0]
    assert kind == "ANALYTICS_SINK_DEGRADED"
    assert org_id == "", "the sink is platform-level — a subject would fan out"
    assert detail["fallback"] >= ha._ANALYTICS_FALLBACK_ALERT_AFTER, detail
    assert ha._ANALYTICS_COUNTS["fallback"] == 50
    assert _outcomes() == {"fallback": 50}


def test_t8_success_resolves_so_the_next_loss_is_a_new_incident(
        monkeypatch, tmp_path):
    """T8 — a recovery resolves the incident, so the NEXT loss is a NEW one.

    The AlertStore resolves by delete: an incident left open absorbs every
    later degradation into a stale issue, which is the silent-loss class
    reintroduced through the alert channel.

    RED mutations:
      (i)  drop the resolve on success → the recurrence produces no new
           incident (the silent-loss class comes back);
      (ii) resolve unconditionally on every success → the healthy-only run
           below reds, and every healthy write pays an R2 download.

    Both episodes are 3 degraded writes because P2-1's trigger is a streak, so
    the count restarts at zero on the recovered write — one lone fallback after
    a recovery is below the alert threshold by design.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))

    # degraded × 3 → the streak arm opens the first incident.
    for _ in range(3):
        assert ha._track_analytics_event("org-8", "e") == "fallback"
    assert store.log == ["open"], store.log

    # healthy → the recovery resolves it, and resets the streak.
    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-8", "e") == "supabase"
    assert store.log == ["open", "resolve"], store.log

    # degraded again → a SECOND, distinct incident (not absorbed by the first),
    # but only once the streak is re-earned.
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    assert ha._track_analytics_event("org-8", "e") == "fallback"
    assert store.log == ["open", "resolve"], store.log
    for _ in range(2):
        assert ha._track_analytics_event("org-8", "e") == "fallback"
    assert store.log == ["open", "resolve", "open"], store.log
    assert len(store.resolves) == 1, (
        "resolve happens once, on the healthy write — with no incident open "
        "the healthy-only run below must not call the store at all")

    # A healthy-only run touches the store AT MOST ONCE — the once-per-process
    # startup resolve probe (cycle-4 P1-1) — never per event.
    _reset_write_path(monkeypatch)
    _stub_http(monkeypatch, status=201)
    _fallback_at(monkeypatch, tmp_path)
    healthy_store = _store(monkeypatch)
    for _ in range(5):
        assert ha._track_analytics_event("org-8", "e") == "supabase"
    assert healthy_store.opens == [], (
        "a delivered write must never file an incident")
    assert len(healthy_store.resolves) == 1, (
        "the startup probe runs ONCE per process — the rest of the healthy run "
        "must not call the store (resolve_incident begins with an R2 download)")


def test_t9_counted_even_when_the_alert_channel_does_not_exist(
        monkeypatch, tmp_path, caplog):
    """T9 — counting is not conditioned on the alert channel existing.

    A deployment with no alert credentials has no AlertStore. The counter and a
    WARNING are then the whole signal (the D6 residual, narrowed to "no alert
    credentials" by D5a) — but the write must still be COUNTED, because that is
    the machine-readable evidence.

    RED mutation: return/ascend early when the store is unavailable, before
    counting → ``_outcomes()`` is empty for a genuinely degraded write.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    monkeypatch.setattr(ha, "_analytics_alert_store", lambda: None)
    caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")

    outcomes = [ha._track_analytics_event("org-9", "mcp_tool_call",
                                          {"tool_name": "n"})
                for _ in range(3)]

    assert outcomes == ["fallback"] * 3, (
        "the event reached the disk every time — the channel's absence must "
        "not change the outcome")
    assert fallback.exists()
    assert _outcomes() == {"fallback": 3}, (
        "the count must not be skipped when no alert channel exists")
    assert any(r.levelno == logging.WARNING and r.name == "tortoise.hosted_api"
               for r in caplog.records), (
        "a degraded write with no channel must still emit a WARNING on the "
        "tortoise.hosted_api logger — behaviour, not the message's prose")


def test_t10_alert_sink_raising_does_not_escape(monkeypatch, tmp_path):
    """T10 — the never-raise contract holds over the NEW leg too.

    The alert dispatch is network I/O (R2 + GitHub + Telegram) reachable from
    the GitHub OAuth callback. A raising store must not escape; the write
    itself already succeeded onto the disk.

    RED mutations:
      (i)  remove the ``try/except`` around the alert dispatch → the RuntimeError
           escapes the handler (this test errors instead of returning);
      (ii) latch the episode BEFORE the dispatch succeeds (P2-2) → the raise
           silences the rest of the episode, so the 4th degraded write never
           retries and only ONE ``open_incident`` is attempted.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch, _FakeStore(raises_on_open=True))

    for _ in range(4):
        # No pytest.raises: the assertion IS that each call returns.
        assert ha._track_analytics_event("org-10", "mcp_tool_call",
                                         {"tool_name": "r"}) == "fallback"

    assert fallback.exists(), "the event still reached the disk"
    assert len(store.opens) == 2, (
        "the failed dispatch must not latch the episode: the streak stays "
        "past the threshold, so every later event retries "
        "(the store's own retry then gets its second call) — P2-2")


def test_t13_half_configured_env_is_a_degradation(monkeypatch, tmp_path):
    """T13 (P1-2) — HALF-configured is a degradation, never ``unconfigured``.

    ``SUPABASE_URL`` set with the key missing is #3677 ITSELF: the write path
    silently diverted to the ephemeral JSONL while the outcome vocabulary — and
    therefore the alert — called the situation "no sink by design". The signal
    must not be blind to the failure that created the issue.

    RED mutation: revert to ``configured = bool(url and key)`` as the sole
    discriminator (drop the ``misconfigured`` arm) → each write returns
    ``unconfigured`` and the alert is never reached, which is exactly what the
    scope's 3-value/2-way form did.
    """
    from tortoise import hosted_api as ha

    for shape in ("url_only", "key_only"):
        _reset_write_path(monkeypatch)
        _no_env(monkeypatch)
        if shape == "url_only":
            monkeypatch.setenv("SUPABASE_URL", PROD_URL)
        else:
            monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
        fallback = _fallback_at(monkeypatch, tmp_path)
        store = _store(monkeypatch)

        outcomes = [ha._track_analytics_event("org-13", "mcp_tool_call",
                                              {"tool_name": "half"})
                    for _ in range(3)]

        assert "unconfigured" not in outcomes, (shape, outcomes)
        assert outcomes == ["fallback"] * 3, (shape, outcomes)
        assert not _StubClient.posts, (
            f"{shape}: a half-configured env cannot post — no request may be "
            "attempted with a missing half")
        assert fallback.exists(), f"{shape}: the event is on local disk"
        assert _outcomes() == {"fallback": 3}, (shape, _outcomes())
        assert len(store.opens) == 1, (shape, store.log)
        assert store.opens[0][3]["reason"] == "supabase_env_incomplete", (
            shape, store.opens[0][3])


def test_t14_alert_channel_is_not_gated_on_the_backup_sweep(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T14 (P1-1 / D5a) — an incident is filed with backups DISABLED.

    ``BACKUP_SWEEP_ENABLED`` defaults to false, and the alert channel used to
    be built through ``_backup_config_safe()`` — which returns ``None``
    whenever the sweep is off. So on such a deployment NO incident was ever
    filed and the only signals were an unscraped counter and a log on an
    ephemeral Fly rootfs: #3677's loss class, re-created through the alert
    channel. D5a forbids that.

    The whole real leg runs here — ``_analytics_alert_store`` →
    ``load_alert_config`` → ``_alert_store_from`` → ``AlertStore`` — with only
    the object store (R2) and the two egress functions (GitHub, Telegram)
    replaced, so the assertion is a real FILED issue, not a mocked seam.

    RED mutation: build the channel from ``_backup_config_safe()`` alone
    (returning ``None`` when the sweep is disabled, the pre-fix code) → no
    incident is filed and ``filed`` stays empty.
    """
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.hosted_backup import MemoryStorage

    _prod_env(monkeypatch)
    # Backups are OFF — the default, and the pre-fix gate.
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    # …but the alert credentials exist, as they must for any channel at all.
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("BACKUP_ALERT_ASSIGNEE", "daniel-ospina")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "tg-chat")
    # The R2 seam is the only dependency replaced: no incident may PUT to prod.
    monkeypatch.setattr(ha, "_backup_storage", lambda: MemoryStorage())
    filed: list[tuple] = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: (
            filed.append((repo, pat, title, body, assignee)) or 4242))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    assert ha._backup_config_safe() is None, (
        "precondition: the sweep is disabled, which is the gate under test")
    assert ha._analytics_alert_store() is not None, (
        "D5a: the alert channel must NOT depend on the sweep switch")

    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    for _ in range(3):
        assert ha._track_analytics_event("org-14", "mcp_tool_call",
                                         {"tool_name": "t"}) == "fallback"
    assert fallback.exists()

    assert len(filed) == 1, (
        "backups disabled must not silence the analytics incident")
    repo, pat, title, body, assignee = filed[0]
    assert title.startswith("[DR] ANALYTICS_SINK_DEGRADED"), title
    assert repo == "daniel-ospina/tortoise" and pat == "pat-test-only"
    assert assignee == "daniel-ospina"
    # The detail is counts + reason codes: this is a plain transport fallback,
    # so it carries no reason code at all (never event content). Parse the
    # rendered detail rather than pinning ``json.dumps``' spelling — a
    # whitespace/serializer change must not red a semantic assertion.
    detail = json.loads(body.split("```", 2)[1])
    assert detail["reason"] == "", detail
    assert "org-14" not in body and "mcp_tool_call" not in body, body


def test_t15_without_alert_credentials_the_counter_is_the_residual(
        monkeypatch, tmp_path, caplog, real_analytics_alert_store):
    """T15 — with NO alert credentials the channel is ``None``, honestly.

    T14 proves the switch is no longer the gate; this pins what is genuinely
    left. Without ``DR_ISSUES_PAT`` there is no issue filer, so no channel can
    exist: the counter and the WARNING ARE the residual (D6, narrowed from "the
    sweep is off" to "no alert credentials"). What must NOT happen is a raise
    or a lost count.

    The R2 seam is replaced here even though nothing should reach it: without
    that, a missing ``R2_*`` env would make store construction fail for a
    SECOND reason and MASK the credential guard this test exists to pin (a
    mutation that drops the ``DR_ISSUES_PAT`` check then still returns
    ``None``, and the pin would be vacuous). The three egress callables are
    patched as T14 does as well — the guard under test is a credential check,
    not a docstring; if ``load_alert_config``'s ``DR_ISSUES_PAT`` guard (or the
    store's availability) is ever dropped, this test must not reach real R2,
    GitHub or Telegram.

    RED mutation: drop ``load_alert_config``'s ``if not github_issues_pat:
    return None`` guard → a config with an EMPTY PAT is built, the store is
    constructed (the R2 seam is usable here), and the first assertion below
    fails.
    """
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.hosted_backup import MemoryStorage

    _prod_env(monkeypatch)
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("DR_ISSUES_PAT", raising=False)
    monkeypatch.setattr(ha, "_backup_storage", lambda: MemoryStorage())
    filed: list[tuple] = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: (
            filed.append((repo, pat, title, body, assignee)) or 4242))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)
    caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")

    assert ha._backup_config_safe() is None
    assert ha._analytics_alert_store() is None, (
        "no PAT means no filer — the store cannot be built, and saying so is "
        "the honest residual rather than a silent half-channel")

    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    for _ in range(3):
        assert ha._track_analytics_event("org-15", "e") == "fallback"

    assert fallback.exists()
    assert _outcomes() == {"fallback": 3}
    assert filed == [], (
        "no alert credentials — no incident may be filed, and nothing may "
        "reach real egress")
    assert any(r.levelno == logging.WARNING and r.name == "tortoise.hosted_api"
               for r in caplog.records), (
        "the absent channel must still emit a WARNING on the "
        "tortoise.hosted_api logger — behaviour, not the message's prose")


def test_t16_a_raising_outcome_counter_does_not_escape(monkeypatch, tmp_path):
    """T16 (P2-3) — the counter leg sits inside its OWN never-raise guard.

    ``record_analytics_outcome`` used to be called bare from both note helpers,
    outside every guard: a raise from the metrics library would have escaped a
    function whose whole reason for existing is to never raise into the GitHub
    OAuth callback.

    RED mutation: inline ``record_analytics_outcome(outcome)`` back into the
    note helper (drop the ``_analytics_count_outcome`` wrapper) → the
    RuntimeError escapes instead of the outcome being returned.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    _store(monkeypatch)

    def _boom(outcome):
        raise RuntimeError("metrics library exploded")

    monkeypatch.setattr(ha, "record_analytics_outcome", _boom)

    # No pytest.raises: the assertion IS that each call returns.
    for _ in range(3):
        assert ha._track_analytics_event("org-16", "e") == "fallback"

    assert fallback.exists(), "the write itself still reached the disk"
    assert ha._ANALYTICS_COUNTS["fallback"] == 3, (
        "the write is still counted in-process even when the metrics leg "
        "fails — the two are independent")


def test_t17_a_raising_incident_detail_does_not_escape(monkeypatch,
                                                       tmp_path):
    """T17 (cycle-2 P2-2) — the incident DETAIL build sits inside the
    never-raise guard, mirroring T16's counter pin.

    ``_analytics_open_incident`` builds the detail (it takes the alert lock and
    reads the counters) before handing it to the store. A prior shape hoisted
    that build above the ``try``, where a raise would escape a function whose
    whole contract is never to raise — straight into the GitHub OAuth callback
    that drives the analytics write. T16 pins the COUNTER leg; this pins the
    DETAIL leg, so a refactor that hoists the build back out is caught.

    RED mutation: hoist ``detail = _analytics_incident_detail(...)`` back above
    the ``try`` in ``_analytics_open_incident`` -> the RuntimeError escapes and
    ``_track_analytics_event`` raises instead of returning ``"fallback"``.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)

    def _boom(outcome, reason):
        raise RuntimeError("detail build exploded")

    monkeypatch.setattr(ha, "_analytics_incident_detail", _boom)

    # No pytest.raises: the assertion IS that each call returns. Three calls
    # reach the alert threshold, so the detail leg is actually exercised.
    for _ in range(3):
        assert ha._track_analytics_event("org-17", "e") == "fallback"

    assert fallback.exists(), "the write itself still reached the disk"
    assert store.opens == [], (
        "the dispatch never completed — the detail build raised before it")
    assert ha._ANALYTICS_COUNTS["fallback"] == 3


def test_t18_the_suite_wide_isolation_hides_the_real_alert_store(monkeypatch):
    """T18 (cycle-2 P1) — the conftest autouse isolation really is installed.

    The alert leg's safety now rests on
    ``tests/conftest.py::_analytics_alert_isolation`` patching the seam for
    EVERY test, not on a per-file fixture. This test carries a FULL alert env
    (PAT + a usable in-memory object store) and proves the seam still resolves
    to ``None`` — i.e. the isolation covers this file too. Without it, three of
    the four write-path exits can build the REAL ``AlertStore``: on a machine
    holding production secrets that is a real ``[DR] ANALYTICS_SINK_DEGRADED``
    issue, a prod-R2 PUT and a Telegram push.

    RED mutation: delete the ``monkeypatch.setattr(ha, "_analytics_alert_store",
    lambda: None)`` line from ``tests/conftest.py::_analytics_alert_isolation``
    -> the real builder runs and the assertion fails.
    """
    from tortoise import hosted_api as ha
    from tortoise.hosted_backup import MemoryStorage

    monkeypatch.setenv("BACKUP_SWEEP_ENABLED", "0")
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    # The object store is the ONLY dependency replaced: with the seam patch in
    # place nothing below is reached; without it this is a store that would
    # file through the real `github_issue` path.
    monkeypatch.setattr(ha, "_backup_storage", lambda: MemoryStorage())

    assert ha._analytics_alert_store() is None, (
        "the autouse isolation must keep the real AlertStore out of every test")


def test_t19_the_suite_wide_isolation_covers_the_jsonl_sink_and_counts(
        monkeypatch, tmp_path):
    """T19 (cycle-3 P2-1/P2-2) — the autouse isolation covers the LOCAL JSONL
    sink and the in-process outcome counts, not only the alert store.

    T18 pins the alert seam; this pins the other two process globals the write
    path shares. It deliberately does NOT call ``_fallback_at``: it leaves
    ``_ANALYTICS_FALLBACK_PATH`` at whatever the suite-wide fixture installed
    and drives an ``unconfigured`` write (no Supabase env), so the local JSONL
    IS the sink. The event must land in THIS test's ``tmp_path`` — the real
    ``~/.tortoise/analytics_fallback.jsonl`` is the DR runbook's recovery source
    on the production box, and a suite run must never touch it. The counts must
    start at zero so no earlier test leaks into the incident detail this
    isolation exists to keep honest.

    RED mutation: delete the ``_ANALYTICS_FALLBACK_PATH`` (or the
    ``_ANALYTICS_COUNTS``) ``monkeypatch.setattr`` line from
    ``tests/conftest.py::_analytics_alert_isolation`` -> the sink resolves
    outside ``tmp_path`` (the first assertion fails) and/or the counts are
    non-zero (the second fails).
    """
    from tortoise import hosted_api as ha

    _no_env(monkeypatch)
    _store(monkeypatch)

    assert ha._ANALYTICS_FALLBACK_PATH == str(tmp_path / FALLBACK_NAME), (
        "the JSONL sink must be redirected out of the real HOME")
    assert ha._ANALYTICS_COUNTS == {o: 0 for o in ha._ANALYTICS_OUTCOMES}, (
        ha._ANALYTICS_COUNTS)

    assert ha._track_analytics_event("org-19", "e") == "unconfigured"

    assert (tmp_path / FALLBACK_NAME).exists(), (
        "the isolated sink is the one the write must have used")
    assert _written(tmp_path / FALLBACK_NAME)["org_id"] == "org-19"
    assert ha._ANALYTICS_COUNTS["unconfigured"] == 1


def test_t11_dropped_alerts_on_the_first_event(monkeypatch, tmp_path):
    """T11 — an unrecoverable loss alerts immediately, not at the threshold.

    ``dropped`` means no sink received the event, so there is nothing to wait
    for: the D2 threshold/transition gate is for ``fallback`` (the event is
    safe on disk), not for this.

    RED mutation: route ``dropped`` through the same threshold/transition gate
    as ``fallback`` → this single call files nothing.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)
    _fail_appends_to(monkeypatch, fallback)

    assert ha._track_analytics_event("org-11", "e") == "dropped"

    assert len(store.opens) == 1, (
        "a single dropped event must alert — the event is gone")


def test_t12_outcome_vocabulary_is_closed(monkeypatch, tmp_path):
    """T12 — every branch returns a member of the declared vocabulary, and the
    four branches observed equal that vocabulary exactly.

    RED mutation: any branch returning an ad-hoc string (e.g. ``"error"``) that
    is not in ``_ANALYTICS_OUTCOMES`` — the contract would then be open and
    callers could not branch on it.
    """
    from tortoise import hosted_api as ha

    seen: list[str] = []
    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    fallback = _fallback_at(monkeypatch, tmp_path)
    _store(monkeypatch)

    seen.append(ha._track_analytics_event("org-12", "e"))          # supabase
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    seen.append(ha._track_analytics_event("org-12", "e"))          # fallback
    _no_env(monkeypatch)
    seen.append(ha._track_analytics_event("org-12", "e"))          # unconfigured
    _prod_env(monkeypatch)
    _fail_appends_to(monkeypatch, fallback)
    seen.append(ha._track_analytics_event("org-12", "e"))          # dropped

    assert all(isinstance(o, str) for o in seen), seen
    assert all(o in ha._ANALYTICS_OUTCOMES for o in seen), seen
    assert set(seen) == set(ha._ANALYTICS_OUTCOMES), seen
    # …and the counter operates on that same closed vocabulary.
    assert set(_outcomes()) == set(ha._ANALYTICS_OUTCOMES), _outcomes()


# ── T20–T22 (cycle-4 review) ────────────────────────────────────────────────


def test_t20_recovery_resolve_is_durable_across_a_restart(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T20 (cycle-4 P1-1) — a recovered sink resolves even after a RESTART.

    ``_ANALYTICS_INCIDENT_OPEN`` is process-local, but the incident is durable
    (the AlertStore's dedup object outlives the process). So a process that
    degrades, files ``[DR] ANALYTICS_SINK_DEGRADED`` and then restarts (every
    ``fly deploy``) starts with the latch ``False`` while the issue is still
    open: a latch-only resolve never fires, and every later degradation is
    absorbed into the stale issue — the silent-loss class D4 exists to prevent,
    reintroduced through the alert channel.

    This runs the REAL store over a shared ``MemoryStorage`` — the SAME object
    across both simulated processes — and discards the process globals to
    simulate the restart, so the resolve has to be driven by the store's own
    durable state, not by a latch.

    RED mutation: drop the ``or not _ANALYTICS_RESOLVE_PROBED`` half of the
    resolve condition (the pre-fix latch-only gate) → the post-restart 2xx
    never resolves, ``closed`` stays empty, and the second episode is silently
    absorbed into the first issue.
    """
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.hosted_backup import MemoryStorage

    _prod_env(monkeypatch)
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("BACKUP_ALERT_ASSIGNEE", "daniel-ospina")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "tg-chat")

    storage = MemoryStorage()
    monkeypatch.setattr(ha, "_backup_storage", lambda: storage)
    filed: list[tuple] = []
    closed: list[tuple] = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: (
            filed.append((title, body)) or 4242))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue",
                        lambda *a, **k: closed.append(a))
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    fallback = _fallback_at(monkeypatch, tmp_path)

    # ── process A: 3 degraded writes file the incident ──
    for _ in range(3):
        assert ha._track_analytics_event("org-20", "e") == "fallback"
    assert fallback.exists()
    assert len(filed) == 1, filed
    assert filed[0][0].startswith("[DR] ANALYTICS_SINK_DEGRADED"), filed[0][0]

    # ── simulate a RESTART: every process-local global is discarded; the
    # durable store state (and its open issue) is not. ──
    monkeypatch.setattr(ha, "_ANALYTICS_INCIDENT_OPEN", False)
    monkeypatch.setattr(ha, "_ANALYTICS_DEGRADED_STREAK", 0)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_PROBED", False)

    # ── process B: the sink is healthy again. ONE 2xx write must resolve the
    # incident process A opened, or it stays open forever. ──
    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-20", "e") == "supabase"
    assert len(closed) == 1, (
        "the recovered sink must resolve the pre-restart incident — a "
        "latch-only resolve leaves it open and the next episode is absorbed")
    assert 4242 in closed[0], closed

    # ── and because the recovery was durable, a later episode is a NEW
    # incident (a second issue), not an absorption into the stale one. ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    for _ in range(3):
        assert ha._track_analytics_event("org-20", "e") == "fallback"
    assert len(filed) == 2, (
        f"a degradation after a recovered episode must file a NEW issue — "
        f"filed={filed!r}; one issue means the new episode was silently "
        f"absorbed into the stale open one")


def test_t21_a_raise_in_the_success_leg_cannot_fake_a_fallback(
        monkeypatch, tmp_path):
    """T21 (cycle-4 P2-1) — a 2xx write stays ``supabase`` even if the
    success-leg bookkeeping raises.

    The success ``return`` used to sit INSIDE the POST ``try``: a raise in
    ``_analytics_note_success`` was swallowed by the transport handler and fell
    through to the JSONL, so a DELIVERED event was written to disk a second
    time and reported as ``fallback`` — which can file an incident against a
    healthy sink. The guard must cover the network call only.

    RED mutation: put ``return _analytics_note_success()`` back inside the
    ``try`` around ``client.post`` → the injected raise is swallowed, the
    event is duplicated to the JSONL and the outcome is ``fallback``.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _store(monkeypatch)

    def _boom() -> str:
        raise RuntimeError("success bookkeeping exploded")

    monkeypatch.setattr(ha, "_analytics_note_success", _boom)

    # No pytest.raises: the never-raise contract still covers this leg.
    assert ha._track_analytics_event("org-21", "e") == "supabase"
    assert not fallback.exists(), (
        "a DELIVERED event must never be duplicated to the local JSONL")
    assert _outcomes() == {}, "a delivered write is not a degraded outcome"
    assert store.opens == [], "no incident may be filed for a healthy sink"


def test_t22_the_count_vocabulary_is_derived_from_the_declared_one(
        monkeypatch):
    """T22 (cycle-4 P2-2) — the counts and the incident detail are DERIVED
    from ``_ANALYTICS_OUTCOMES``, so adding an outcome cannot raise.

    ``_ANALYTICS_COUNTS`` and ``_analytics_incident_detail`` used to re-type the
    four members as literals. Adding a fifth member to the declared vocabulary
    then raised ``KeyError`` on the increment at ``_ANALYTICS_COUNTS[outcome]
    += 1`` — which sits inside ``with _ANALYTICS_ALERT_LOCK:`` with no ``try``,
    so the raise lands straight in the GitHub OAuth callback the never-raise
    contract protects.

    RED mutation: restore the hand-written four-key dict for
    ``_ANALYTICS_COUNTS`` and hand-written keys in ``_analytics_incident_detail``,
    then add a fifth member to ``_ANALYTICS_OUTCOMES`` — the derivations drift
    and the invariants below fail (or the detail build raises ``KeyError``).
    """
    from tortoise import hosted_api as ha

    vocab = set(ha._ANALYTICS_OUTCOMES)
    assert set(ha._ANALYTICS_COUNTS) == vocab, (
        "the counters must be derived from the declared vocabulary — a "
        "hand-typed dict drifts the moment an outcome is added")
    detail = ha._analytics_incident_detail("fallback", "r")
    assert set(detail) == {"outcome", "reason"} | vocab, (
        "the incident detail must carry a count for every declared outcome")
