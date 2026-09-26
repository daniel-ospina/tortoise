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
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise.alert_store import ResolveOutcome

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

    def __init__(self, raises_on_open: bool = False,
                 dedup: bool = False, suppressed: bool = False,
                 resolve_outcome=None):
        self.calls: list[tuple] = []
        self.raises_on_open = raises_on_open
        self.dedup = dedup
        self.suppressed = suppressed
        # Which tri-state fact the store reports. ``None`` → RESOLVED (the
        # pre-collapse ``resolve_incident() is True``). Tests that need
        # ABSENT/SKIPPED_FRESH set it explicitly.
        self.resolve_outcome = resolve_outcome
        # The `before` bound each resolve was called with, one per call.
        self.resolve_befores: list = []
        self.resolve_raises: BaseException | None = None
        # The presence fact `incident_open` reports (cycle-9 P1). Default
        # ``True`` → an incident is on record.
        self._incident_open = True

    def open_incident(self, kind, org_id="", detail=None):
        self.calls.append(("open", kind, org_id, detail))
        if self.raises_on_open:
            raise RuntimeError("alert store down")
        # `False` is the real store's DEDUP hit (the object already existed) OR
        # a SUPPRESSED kind (no issue, no dedup object) — the two cases the
        # caller must not conflate (cycle-6 P2-1).
        return not (self.dedup or self.suppressed)

    def suppression_active(self, kind):
        return self.suppressed

    def incident_open(self, kind, org_id=""):
        # The real store's read-only presence check (#3820 cycle-9 P1). The
        # fake carries no object store, so the default is "on record" — which
        # preserves the pre-cycle-9 behaviour (keep the arm) for every existing
        # resolve test. A test that needs the raced-and-deleted shape installs
        # the REAL store instead.
        return self._incident_open

    def resolve_incident_state(self, kind, org_id="", *, before=None):
        self.calls.append(("resolve", kind, org_id, before))
        self.resolve_befores.append(before)
        if self.resolve_raises is not None:
            raise self.resolve_raises
        return self.resolve_outcome or ResolveOutcome.RESOLVED

    def resolve_incident(self, kind, org_id="", *, before=None):
        return (self.resolve_incident_state(kind, org_id, before=before)
                is ResolveOutcome.RESOLVED)

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

    The resolve state (``PENDING`` / ``NOT_BEFORE``) and the degraded streak
    are process globals that a single test mutates across phases; the outcome
    counter is a process-global Prometheus ``Counter`` whose label children
    must be cleared so a phase's counts mean exactly what that phase executed.
    The baseline is the PROCESS-START state — ``PENDING=True`` with no bound —
    so the first delivered write probes.

    The local JSONL sink path and the in-process ``_ANALYTICS_COUNTS`` dict are
    deliberately NOT touched here: they are owned suite-wide by
    ``tests/conftest.py::_analytics_alert_isolation``, and resetting them here
    would clobber that isolation (a ``None`` path resolves the REAL
    ``~/.tortoise/analytics_fallback.jsonl``). ``test_t19`` pins both.
    """
    import tortoise.hosted_api as ha
    import tortoise.monitoring as mon

    monkeypatch.setattr(ha, "_ANALYTICS_DEGRADED_STREAK", 0)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_PENDING", True)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_NOT_BEFORE", None)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_INFLIGHT", False)
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

    before_write = datetime.now(UTC)
    outcome = ha._track_analytics_event("org-1", "capture_cost", {"calls": 2})

    assert outcome == "supabase"
    assert len(_StubClient.posts) == 1, "exactly one event → one request"
    assert _StubClient.posts[0][0] == f"{PROD_URL}/rest/v1/analytics_events"
    assert not fallback.exists(), "a delivered write must not touch the disk"
    assert store.opens == [], "a delivered write must not FILE an incident"
    # cycle-4 P1-1: the FIRST delivered write of a process probes for a stale
    # open incident exactly once (the resolve is durable across a restart), so
    # the store is touched once — never per event, and never with an `open`.
    # cycle-5 P1-2: the probe passes a `before` bound (this process's first
    # delivered write) so it can only resolve an incident that predates it.
    assert len(store.resolves) == 1, store.resolves
    assert store.resolves[0][:3] == (
        "resolve", ha._ANALYTICS_INCIDENT_KIND, ""), store.resolves[0]
    # The bound is the instant this resolve DECIDED, captured once at the call
    # — not a once-per-process first-delivered stamp. An incident filed at or
    # after it is a fresh episode the store reports as SKIPPED_FRESH, so a
    # concurrent degradation is never swept away (T24/T28 pin the skip; here
    # it only has to BE a bound, and an aware one comparable to `filed_at`).
    #
    # RED mutation: drop the `before=` kwarg at the call site → the store
    # records a `None` bound and this reds on `resolve_befores[0] is not None`.
    assert store.resolve_befores[0] is not None, store.resolves
    assert store.resolve_befores[0].tzinfo is not None, store.resolve_befores
    assert before_write <= store.resolve_befores[0] <= datetime.now(UTC), (
        store.resolve_befores)
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
      (i)  drop the episode gate — stop arming
           ``_ANALYTICS_RESOLVE_NOT_BEFORE`` on a completed dispatch (or gate
           on nothing) → 50 ``open_incident`` calls, i.e. an R2 PUT + a GitHub
           search + Telegram per event;
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

    assert str(tmp_path / FALLBACK_NAME) == ha._ANALYTICS_FALLBACK_PATH, (
        "the JSONL sink must be redirected out of the real HOME")
    assert {o: 0 for o in ha._ANALYTICS_OUTCOMES} == ha._ANALYTICS_COUNTS, (
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

    The incident is durable (the AlertStore's dedup object outlives the
    process) but the resolve state is not. So a process that degrades, files
    ``[DR] ANALYTICS_SINK_DEGRADED`` and then restarts (every ``fly deploy``)
    starts with no memory of it: a latch-only resolve never fires, and every
    later degradation is absorbed into the stale issue — the silent-loss class
    D4 exists to prevent, reintroduced through the alert channel.
    ``_ANALYTICS_RESOLVE_PENDING`` is therefore ``True`` at process start, so
    the first delivered write probes the store's own durable state.

    This runs the REAL store over a shared ``MemoryStorage`` — the SAME object
    across both simulated processes — and discards the process globals to
    simulate the restart, so the resolve has to be driven by what the store
    reports, not by a latch.

    RED mutation: start the post-restart process with
    ``_ANALYTICS_RESOLVE_PENDING = False`` (the latch-only gate) → the
    post-restart 2xx never resolves, ``closed`` stays empty, and the second
    episode is silently absorbed into the first issue.
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
    # durable store state (and its open issue) is not. `PENDING=True` is the
    # process-start state — the probe that makes the resolve durable. ──
    monkeypatch.setattr(ha, "_ANALYTICS_DEGRADED_STREAK", 0)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_PENDING", True)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_NOT_BEFORE", None)

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


def test_t23_a_transient_resolve_failure_does_not_spend_the_probe(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T23 (cycle-5 P1-1; collapsed cycle-7) — a TRANSIENT resolve failure
    must not retire the resolve.

    ``resolve_incident_state`` begins with an R2 read that re-raises anything
    but ``KeyError``/``ValueError``. The resolve used to be marked done BEFORE
    it ran, so one transient read failure retired the machine's only shot: the
    process could never resolve again and every later episode was absorbed
    into the stale issue for its whole lifetime.

    This runs the REAL store over a shared ``MemoryStorage`` and makes the
    FIRST post-restart read fail once, so the failure is the real
    ``_read_json`` re-raise, not a mock. Process B starts in the UNKNOWN state
    (nothing is on record), so the failed attempt must arm NOTHING — arming
    the window would close the alert gate on an uncertainty (T30) — and the
    next delivered write simply retries and resolves the stale issue, freeing
    the following episode to file a NEW issue.

    RED mutation: clear PENDING when the resolve raises (the pre-fix
    retirement) → the retry never happens and this reds on
    ``_ANALYTICS_RESOLVE_PENDING is True``.
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

    # ── process A: 3 degraded writes file the incident ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    for _ in range(3):
        assert ha._track_analytics_event("org-23", "e") == "fallback"
    assert fallback.exists()
    assert len(filed) == 1, filed

    # ── simulate a RESTART: every process-local global is discarded ──
    monkeypatch.setattr(ha, "_ANALYTICS_DEGRADED_STREAK", 0)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_PENDING", True)
    monkeypatch.setattr(ha, "_ANALYTICS_RESOLVE_NOT_BEFORE", None)

    # The store's FIRST read fails once — a transient R2 error, re-raised by
    # ``_read_json`` exactly as production would.
    real_download = MemoryStorage.download
    calls = {"n": 0}

    def _flaky_download(self, key):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient R2 read failure")
        return real_download(self, key)

    monkeypatch.setattr(MemoryStorage, "download", _flaky_download)

    # ── process B: the first delivered write's resolve FAILS ──
    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-23", "e") == "supabase"
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "a transient resolve failure must NOT retire the resolve — the "
        "incident may still be open")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is None, (
        "nothing is on record, so a failed read must not close the alert "
        "gate — the next delivered write retries instead")
    assert closed == [], closed

    # ── the next delivered write RETRIES and resolves the stale issue ──
    before_retry = calls["n"]
    assert ha._track_analytics_event("org-23", "e") == "supabase"
    # Alias-aware resolve (#2844): the incident is platform-scoped, so BOTH
    # spellings of its sentinel (`_.json` and the driver's legacy
    # `global.json`) are read, and the SURVIVOR is then re-read for the
    # compare-and-delete — 3 reads here, vs main's single-key 1. Pinned as a
    # DELTA so the transient read that failed above is never folded into a
    # magic absolute. RED mutation: read only the canonical spelling
    # (`PLATFORM_ALIASES = ("_",)`) or drop the compare-and-delete re-read →
    # the delta is 2 and this reds.
    assert calls["n"] - before_retry == 3, calls
    assert len(closed) == 1, (
        "a transient failure must not retire the resolve: the retry must "
        "close the stale incident")
    assert 4242 in closed[0], closed
    assert ha._ANALYTICS_RESOLVE_PENDING is False, (
        "the store reported nothing open — the pending flag must clear")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is None

    # ── and a later episode is a NEW issue, not an absorption ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    for _ in range(3):
        assert ha._track_analytics_event("org-23", "e") == "fallback"
    assert len(filed) == 2, (
        "the retried resolve must free the next episode to file a NEW issue")


def test_t24_a_fresh_incident_survives_the_startup_probe(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T24 (cycle-5 P1-2) — the startup resolve must not close a FRESH
    incident.

    ``resolve_incident_state`` resolves whatever it reads unless a ``before``
    bound excludes it, so a fresh incident opened while the resolve was in
    flight was CLOSED: the issue disappeared while the following episode was
    absorbed — the silent-loss class through the alert channel. Every resolve
    now passes ``before`` (the instant it decided to resolve) and the store
    reports any incident whose ``filed_at`` is at or after it as
    ``SKIPPED_FRESH`` — untouched.

    The fresh episode is opened INSIDE the resolve, with a ``filed_at`` after
    the bound, and must survive: its dedup object is still present, no close
    was issued, and the sink stays PENDING so a later delivered write can
    still close it (T28 drives that half — the cycle-7 P1 pin).

    RED mutation: drop the ``before=`` argument at the call site (or ignore
    ``before`` in ``AlertStore.resolve_incident_state``) → the resolve runs
    unconditionally, the fresh incident is closed and its dedup object
    deleted, and the test reds on the ``storage.list("ops/alerts/")``
    assertion that names exactly that deletion. (The fixture derives its
    ``filed_at`` from the bound it is HANDED — ``before or now`` — so the
    call-site-drop mutation reaches the assertion instead of dying on a
    collateral ``TypeError`` from ``None + timedelta``.)
    """
    from datetime import datetime, timedelta, timezone

    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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

    store = ha._analytics_alert_store()
    assert store is not None

    real_resolve = AlertStore.resolve_incident_state

    def _fresh_then_resolve(self, kind, org_id="", *, before=None):
        # A FRESH episode opens while the probe's resolve is in flight; its
        # `filed_at` is deliberately later than the probe's `before` bound.
        # `before or now` keeps this fixture from dereferencing the bound
        # unconditionally: when the call site DROPS the kwarg (the docstring's
        # named mutation) `before` is None, and `None + timedelta` would raise
        # a collateral TypeError before the resolve ever runs — so the test
        # would red on an unrelated error instead of the assertion it names
        # (cycle-6 P2-2). With `before` absent, `filed_at` is simply now + 1s,
        # i.e. after the unbounded resolve's effective bound, and the probe
        # resolves the fresh incident — the behaviour the test must catch.
        bound = before or datetime.now(timezone.utc)  # noqa: UP017
        self.open_incident(kind, org_id, {"fresh": True})
        key = self._key(kind, org_id)
        state = json.loads(self._storage.download(key))
        state["filed_at"] = (bound + timedelta(seconds=1)).isoformat()
        self._storage.upload(key, json.dumps(state).encode())
        return real_resolve(self, kind, org_id, before=before)

    monkeypatch.setattr(AlertStore, "resolve_incident_state",
                        _fresh_then_resolve)

    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-24", "e") == "supabase"

    assert len(filed) == 1, filed
    assert storage.list("ops/alerts/"), (
        "the fresh incident's dedup object was DELETED by the startup resolve")
    assert closed == [], (
        "the resolve must not close an incident filed after the instant it "
        "decided to resolve")
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "SKIPPED_FRESH means an incident IS open — the sink must stay PENDING "
        "so a later delivered write can still close it (cycle-7 P1)")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "the skipped attempt must bound the next read to one per window")


def test_t25_the_log_distinguishes_filed_dedup_and_suppressed(
        monkeypatch, tmp_path, caplog, real_analytics_alert_store):
    """T25 (cycle-5 P1-3, extended cycle-6 P2-1) — the audit log tells the
    truth about WHY nothing was filed.

    ``AlertStore.open_incident`` returns ``False`` in TWO distinct cases: a
    DEDUP hit (the dedup object already existed — a restarted process
    adopting an open episode, or a concurrent filer) and a SUPPRESSED kind
    (``ops/suppression.json`` pauses it, so NO issue is filed and NO dedup
    object is ever created). The caller ignored the boolean and logged
    ``filed`` unconditionally, so the trail claimed a NEW issue on every
    dedup path; the cycle-5 fix then called both cases ``already open
    (dedup)``, so an operator who set a pause went hunting for an issue that
    does not exist. The caller now asks the store (``suppression_active``)
    instead of inferring a reason from an ambiguous boolean.

    RED mutations:
      (i)  log ``filed`` unconditionally (ignore the boolean) → the dedup run
           emits ``filed`` and the first assertion fails;
      (ii) restore the conflating ``"already open (dedup)"`` message for
           every ``False`` → the suppressed run below (real store, live
           pause) fails on the ``"suppressed (kind paused)"`` assertion.
    """
    from datetime import UTC, datetime, timedelta

    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.hosted_backup import MemoryStorage

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    _fallback_at(monkeypatch, tmp_path)
    _store(monkeypatch, _FakeStore(dedup=True))
    caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")

    for _ in range(ha._ANALYTICS_FALLBACK_ALERT_AFTER):
        assert ha._track_analytics_event("org-25", "e") == "fallback"

    dedup_msgs = [r.getMessage() for r in caplog.records
                  if r.levelno == logging.WARNING
                  and r.name == "tortoise.hosted_api"]
    assert any("already open (dedup)" in m for m in dedup_msgs), dedup_msgs
    assert not any("filed " in m for m in dedup_msgs), (
        "a dedup hit must NOT be logged as a new filing", dedup_msgs)

    # …and a genuine file still logs `filed` (so the fix did not just flip the
    # lie the other way).
    _reset_write_path(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    _fallback_at(monkeypatch, tmp_path)
    _store(monkeypatch, _FakeStore())
    caplog.clear()
    for _ in range(ha._ANALYTICS_FALLBACK_ALERT_AFTER):
        assert ha._track_analytics_event("org-25", "e") == "fallback"
    filed_msgs = [r.getMessage() for r in caplog.records
                  if r.levelno == logging.WARNING
                  and r.name == "tortoise.hosted_api"]
    assert any("filed ANALYTICS_SINK_DEGRADED" in m for m in filed_msgs), (
        filed_msgs)

    # ── a SUPPRESSED kind is NOT a dedup hit (cycle-6 P2-1) ──
    # Run the REAL store over a shared MemoryStorage with a LIVE pause in
    # `ops/suppression.json`, so the ``False`` comes from the store's own
    # suppression gate — not a fake asserting the branch under test. No issue
    # and no dedup object are created, which is exactly why reporting this as
    # "already open (dedup)" misleads the operator who set the pause.
    storage = MemoryStorage()
    monkeypatch.setattr(ha, "_backup_storage", lambda: storage)
    monkeypatch.setenv("BACKUP_SWEEP_ENABLED", "0")
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("BACKUP_ALERT_ASSIGNEE", "daniel-ospina")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "tg-chat")
    filed_issues: list[tuple] = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: (
            filed_issues.append((title, body)) or 9))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    storage.upload(
        "ops/suppression.json",
        json.dumps(
            {ha._ANALYTICS_INCIDENT_KIND: {"until": until}}).encode(),
        content_type="application/json")

    _reset_write_path(monkeypatch)
    _stub_http(monkeypatch, raises=RuntimeError("network down"))
    _fallback_at(monkeypatch, tmp_path)
    monkeypatch.setattr(ha, "_analytics_alert_store",
                        real_analytics_alert_store)
    caplog.clear()
    for _ in range(ha._ANALYTICS_FALLBACK_ALERT_AFTER):
        assert ha._track_analytics_event("org-25", "e") == "fallback"
    suppressed_msgs = [r.getMessage() for r in caplog.records
                       if r.levelno == logging.WARNING
                       and r.name == "tortoise.hosted_api"]
    assert any("suppressed (kind paused)" in m
               for m in suppressed_msgs), suppressed_msgs
    assert not any("filed " in m for m in suppressed_msgs), (
        "a suppressed kind files nothing — the trail must not claim it did",
        suppressed_msgs)
    assert not any("already open (dedup)" in m for m in suppressed_msgs), (
        "a paused kind leaves NO dedup object, so 'already open (dedup)' "
        "sends the operator to an issue that does not exist", suppressed_msgs)
    assert filed_issues == [], filed_issues
    assert storage.list("ops/alerts/") == [], (
        "a suppressed kind must leave no dedup object behind")


def test_t27_the_read_window_is_bounded_for_a_known_open_incident(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T27 (cycle-6 P1; re-expressed by the cycle-7 collapse) — the read
    bound, and no permanent retirement.

    A persistently unreadable store used to be handled by a bounded retry that
    RELEASED its budget by setting ``_ANALYTICS_RESOLVE_PROBED = True`` — a
    PERMANENT retirement (nothing reset it in production, and this function is
    the only production resolver for ``ANALYTICS_SINK_DEGRADED``). Once the
    budget was spent a RECOVERED store was never re-probed: the stale incident
    survived for the process's lifetime, the next degradation hit the store's
    dedup (no new issue, only ``already open``), and the episode was silently
    absorbed — the failure D4 exists to close.

    The collapsed state machine cannot express a retirement: while an incident
    is ON RECORD the SINGLE window (``_ANALYTICS_RESOLVE_NOT_BEFORE``) is
    re-armed by each inconclusive attempt and simply elapses. That gives the
    bound the old comment claimed but did not have — **at most one read per
    window** (the old code spent ``_ANALYTICS_RESOLVE_MAX_ATTEMPTS`` reads per
    window) — while PENDING stays set, so the resolve is delayed, never lost.

    The incident is FILED here rather than carried over a restart, because
    "an incident on record" is the state the window bounds; the process-start
    UNKNOWN state deliberately arms nothing (T23 pins its retry, T30 pins that
    it leaves the alert gate open).

    This runs the REAL store over a shared ``MemoryStorage`` so the failures
    are real ``_read_json`` re-raises, and drives ``time.monotonic`` with a
    controllable clock so the 300s window needs no sleep.

    RED mutations:
      (i)  clear ``PENDING`` on the failure (the retirement) → the recovered
           store is never re-probed and this reds on
           ``_ANALYTICS_RESOLVE_PENDING is True``;
      (ii) stop re-arming the window on an inconclusive attempt while an
           incident is on record → the next delivered write reads again and
           this reds on ``reads["n"] == 1``.
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
            filed.append((title, body)) or 4343))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue",
                        lambda *a, **k: closed.append(a))
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    # A controllable monotonic clock — installed BEFORE the dispatch, so the
    # window the dispatch arms is on the test's timeline.
    clock = {"t": 1000.0}
    monkeypatch.setattr(ha.time, "monotonic", lambda: clock["t"])

    # ── 3 degraded writes FILE the incident: it is now ON RECORD ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    for _ in range(3):
        assert ha._track_analytics_event("org-27", "e") == "fallback"
    assert fallback.exists()
    assert len(filed) == 1, filed
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "a completed dispatch leaves something to resolve")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "a completed dispatch puts the incident on record (the alert gate)")

    # The store's read is persistently broken, so every attempt FAILS until it
    # is flipped back. EVERY read is counted (broken or not) — a window that
    # fails to arm shows up as an extra read either way.
    real_download = MemoryStorage.download
    reads = {"n": 0, "broken": True}

    def _counting_download(self, key):
        reads["n"] += 1
        if reads["broken"]:
            raise RuntimeError("persistent R2 read failure")
        return real_download(self, key)

    monkeypatch.setattr(MemoryStorage, "download", _counting_download)

    # ── ONE delivered write attempts the resolve (due now) and FAILS ──
    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-27", "e") == "supabase"
    assert reads["n"] == 1, reads
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "an inconclusive attempt must keep the resolve pending — the incident "
        "may still be open, and retiring it is the cycle-6 P1")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "the failed attempt must re-arm the ONE window")
    assert closed == [], closed

    # ── the store RECOVERS; a write INSIDE the window must not read ──
    reads["broken"] = False
    assert ha._track_analytics_event("org-27", "e") == "supabase"
    assert reads["n"] == 1, (
        "at most ONE read per window: a delivered write inside it must not "
        "add a store read")
    assert closed == [], (
        "the window has not elapsed — the recovered store is not re-probed "
        "yet")

    # ── once the window elapses the RECOVERED store must be re-probed ──
    clock["t"] = 1000.0 + ha._ANALYTICS_RESOLVE_BACKOFF_S + 1.0
    assert ha._track_analytics_event("org-27", "e") == "supabase"
    assert len(closed) == 1, (
        "an inconclusive attempt must NOT retire the resolve: once the window "
        "elapses a RECOVERED store must still be re-probed and the stale "
        "incident resolved — otherwise it survives and the next degradation "
        "is silently absorbed (cycle-6 P1)")
    assert 4343 in closed[0], closed
    assert ha._ANALYTICS_RESOLVE_PENDING is False, (
        "the store reported nothing open — the pending flag must clear")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is None, (
        "a CLEAN resolve disarms the window")

    # ── and a later episode is a NEW issue, not an absorption ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    for _ in range(3):
        assert ha._track_analytics_event("org-27", "e") == "fallback"
    assert len(filed) == 2, (
        f"the re-armed resolve must free the next episode to file a NEW "
        f"issue — filed={filed!r}; one issue means the new episode was "
        f"silently absorbed into the stale open one")


def test_t28_a_skipped_fresh_incident_stays_resolvable(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T28 (cycle-7 P1) — a resolve that SKIPS a fresh incident must keep it
    pending, and the next episode must not be absorbed.

    Cycle 6 released its retry budget by clearing the in-process latch, and
    the cycle-5 probe cleared that same latch whenever its resolve COMPLETED —
    including when ``resolve_incident`` returned ``False`` because ``before``
    had made it skip a FRESH incident. So the fresh incident stayed open with
    nothing pending: no later delivered write ever resolved it, and the next
    degradation was deduped into it (no new issue, no notification). T24 pins
    the fresh incident's SURVIVAL; this pins that it is still RESOLVABLE,
    which is the half that actually kept the loss silent.

    Runs the REAL store over a shared ``MemoryStorage``, opens a fresh incident
    inside the first resolve (as T24 does), and then:

      * asserts the sink is still PENDING with the window armed;
      * a delivered write INSIDE the window must not spend another read;
      * once the window passes, a delivered write RESOLVES it — issue closed,
        dedup object deleted, state CLEAN;
      * a following degradation episode files a NEW issue (not absorbed).

    RED mutations:
      (i)  clear PENDING (and the window) on a ``SKIPPED_FRESH`` outcome — the
           pre-collapse shape → this reds on ``PENDING is True``;
      (ii) never let the retry become eligible (arm the window absurdly far
           ahead) → this reds on ``calls["n"] == 2``: the fresh incident is
           never resolved, so ``len(closed) == 1`` is never reached either.
    """
    from datetime import datetime, timedelta, timezone

    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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

    # A controllable monotonic clock: the skipped attempt arms the ONE window,
    # so the retry is a window away — drive it, never sleep for it.
    clock = {"t": 1000.0}
    monkeypatch.setattr(ha.time, "monotonic", lambda: clock["t"])

    store = ha._analytics_alert_store()
    assert store is not None

    real_state = AlertStore.resolve_incident_state
    calls = {"n": 0}

    def _fresh_then_resolve(self, kind, org_id="", *, before=None):
        calls["n"] += 1
        bound = before or datetime.now(timezone.utc)  # noqa: UP017
        if calls["n"] == 1:
            # The FRESH episode opens while the resolve is in flight; its
            # `filed_at` is placed AFTER the bound this attempt was handed, so
            # the store reports SKIPPED_FRESH.
            self.open_incident(kind, org_id, {"fresh": True})
            key = self._key(kind, org_id)
            state = json.loads(self._storage.download(key))
            state["filed_at"] = (bound + timedelta(seconds=1)).isoformat()
            self._storage.upload(key, json.dumps(state).encode())
        elif calls["n"] == 2:
            # By the retry the window has passed: the SAME incident now
            # PREDATES the attempt's bound, so it is the one to close. The
            # fixture models that passage of time without sleeping (the wall
            # clock has barely moved between the two calls).
            key = self._key(kind, org_id)
            state = json.loads(self._storage.download(key))
            state["filed_at"] = (bound - timedelta(seconds=1)).isoformat()
            self._storage.upload(key, json.dumps(state).encode())
        return real_state(self, kind, org_id, before=before)

    monkeypatch.setattr(AlertStore, "resolve_incident_state",
                        _fresh_then_resolve)

    # ── delivered write #1: the resolve SKIPS the fresh incident ──
    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-28", "e") == "supabase"
    assert calls["n"] == 1, calls
    assert len(filed) == 1, filed
    assert storage.list("ops/alerts/"), (
        "the fresh incident must survive the skipped resolve")
    assert closed == [], closed
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "SKIPPED_FRESH means an incident IS open — the sink must stay PENDING")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "the skipped attempt must arm the ONE window")

    # ── a delivered write INSIDE the window must not re-read … ──
    assert ha._track_analytics_event("org-28", "e") == "supabase"
    assert calls["n"] == 1, (
        "a write inside the window must not spend another store read")
    assert closed == [], closed

    # ── …and once it passes, the SAME fresh incident is resolved ──
    clock["t"] = 1000.0 + ha._ANALYTICS_RESOLVE_BACKOFF_S + 1.0
    assert ha._track_analytics_event("org-28", "e") == "supabase"
    assert calls["n"] == 2, calls
    assert len(closed) == 1, (
        "the fresh incident skipped by the first resolve must still be "
        "resolvable: a later delivered write has to close it (cycle-7 P1)")
    assert 4242 in closed[0], closed
    assert storage.list("ops/alerts/") == [], (
        "delete-to-resolve — the dedup object must be gone")
    assert ha._ANALYTICS_RESOLVE_PENDING is False, (
        "the store reported nothing open — the state must be CLEAN")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is None

    # ── a FOLLOWING episode is a NEW issue, not an absorption ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    for _ in range(3):
        assert ha._track_analytics_event("org-28", "e") == "fallback"
    assert len(filed) == 2, (
        f"a degradation after the skipped incident was resolved must file a "
        f"NEW issue — filed={filed!r}; one issue means the new episode was "
        f"silently absorbed")


@pytest.mark.parametrize(
    "fake_outcome, expect_pending, expect_armed",
    [
        (ResolveOutcome.RESOLVED, False, False),
        (ResolveOutcome.ABSENT, False, False),
        (ResolveOutcome.SKIPPED_FRESH, True, True),
    ],
)
def test_t29_each_resolve_outcome_drives_the_state(
        monkeypatch, tmp_path, fake_outcome, expect_pending, expect_armed):
    """T29 (cycle-7) — the store's tri-state FACT drives PENDING/NOT_BEFORE.

    The collapse's whole point: the sink must act on WHICH fact the store
    reports, never on a guess about what a ``False`` meant.

      * ``RESOLVED`` / ``ABSENT`` → the incident is not open → CLEAN
        (``PENDING=False``, window disarmed).
      * ``SKIPPED_FRESH`` → the incident IS open (it was filed after this
        attempt's bound) → stay PENDING and re-arm the window.

    RED mutations: (i) treat ``SKIPPED_FRESH`` like ``ABSENT`` (clear PENDING
    and the window) → the SKIPPED_FRESH row reds on ``PENDING is True``;
    (ii) leave PENDING set on ``ABSENT`` → the ABSENT row reds on
    ``PENDING is False``.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    _fallback_at(monkeypatch, tmp_path)
    _store(monkeypatch, _FakeStore(resolve_outcome=fake_outcome))

    assert ha._track_analytics_event("org-29", "e") == "supabase"

    assert ha._ANALYTICS_RESOLVE_PENDING is expect_pending, (
        fake_outcome, ha._ANALYTICS_RESOLVE_PENDING)
    assert (ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None) is expect_armed, (
        fake_outcome, ha._ANALYTICS_RESOLVE_NOT_BEFORE)


def test_t30_an_inconclusive_resolve_does_not_close_the_alert_gate(
        monkeypatch, tmp_path):
    """T30 (cycle-7) — a RAISING resolve from the process-start UNKNOWN state
    must not silence the next episode.

    NOT_BEFORE doubles as the alert gate (``NOT_BEFORE is not None`` ⇒ an
    incident is on record, ``_analytics_note_degradation``). From the UNKNOWN
    state nothing is on record, so a failed read proves NOTHING — arming the
    window here would close the gate on an uncertainty for a whole window, and
    with no delivered write to clear it, indefinitely. A real outage beginning
    right after a transient read failure would then be SILENT, which is the
    #3677 loss class re-created through the alert channel. The failed attempt
    therefore stays PENDING (not a retirement) and arms nothing (not a
    suppression), and the next delivered write retries (T23).

    RED mutation: arm ``NOT_BEFORE`` on the inconclusive attempt regardless of
    what is on record → the gate closes, the following degradation files
    nothing, and this reds on ``len(store.opens) == 1``.
    """
    from tortoise import hosted_api as ha

    _prod_env(monkeypatch)
    _stub_http(monkeypatch, status=201)
    fallback = _fallback_at(monkeypatch, tmp_path)
    store = _FakeStore()
    store.resolve_raises = RuntimeError("R2 read blew up")
    _store(monkeypatch, store)

    # No pytest.raises: the never-raise contract covers the resolve leg too.
    assert ha._track_analytics_event("org-30", "e") == "supabase"

    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "an inconclusive resolve must not retire the resolve")

    # ── a real episode immediately after the failure must still be filed ──
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    for _ in range(3):
        assert ha._track_analytics_event("org-30", "e") == "fallback"
    assert fallback.exists()
    assert len(store.opens) == 1, (
        "a transient resolve failure must not silence the next episode — "
        "the alert gate must not be armed by an uncertainty")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "the episode is now filed, so the incident is on record")


def test_t22_the_count_keys_match_the_declared_vocabulary(
        monkeypatch, real_analytics_counts):
    """T22 (cycle-4 P2-2; renamed cycle-5 P2-1) — the counter and detail KEYS
    match the declared vocabulary.

    This pins the INVARIANT (one key per declared outcome, no extras), which is
    what keeps the increment at ``_ANALYTICS_COUNTS[outcome] += 1`` from
    raising ``KeyError`` inside the never-raise GitHub OAuth callback. It is a
    KEY-SET pin, NOT a derivation pin: at a fixed vocabulary a hand-written
    four-key dict satisfies it. The derivation itself is pinned by T26, which
    extends the vocabulary at runtime. (Cycle-4 sanctioned the key-set form;
    cycle-5 P2-1 found the old NAME overclaimed what it tests, so it is renamed
    to what it actually pins.)

    It reads the CAPTURED production dict (``real_analytics_counts``), not
    ``ha._ANALYTICS_COUNTS``: the autouse isolation REPLACES the module
    attribute with a fresh derivation each test, which would mask a
    non-derived production dict and make this pin vacuous.

    RED mutation: add a key to the module-level ``_ANALYTICS_COUNTS`` that is
    not in ``_ANALYTICS_OUTCOMES`` (or drop one) → the key-set equality fails.
    """
    from tortoise import hosted_api as ha

    vocab = set(ha._ANALYTICS_OUTCOMES)
    assert set(real_analytics_counts) == vocab, (
        "the counters must carry exactly the declared vocabulary — a stray or "
        "missing key drifts the increment")
    detail = ha._analytics_incident_detail("fallback", "r")
    assert set(detail) == {"outcome", "reason"} | vocab, (
        "the incident detail must carry a count for every declared outcome")


def test_t26_the_incident_detail_rebuilds_with_an_extended_vocabulary(
        monkeypatch):
    """T26 (cycle-5 P2-1) — the incident detail is DERIVED from the declared
    vocabulary, not a hand-written key list.

    T22 only pins the key SET at the current four-outcome vocabulary, so it
    cannot tell a derived detail from a hand-written four-key one — the
    ``p2d_hardcode_only`` mutation passes it. This test extends the declared
    vocabulary at runtime (a fifth outcome, plus the counts a correctly-derived
    module would have rebuilt for it) and asserts the detail carries the new
    key. That makes the derivation observable at a vocabulary the hand-written
    form did not know about.

    RED mutation (``p2d_hardcode_only``): restore the hand-written
    ``{"outcome", "reason", "supabase", "fallback", "unconfigured",
    "dropped"}`` detail → it has no ``"fifth"`` key and this reds.
    """
    from tortoise import hosted_api as ha

    vocab = (*ha._ANALYTICS_OUTCOMES, "fifth")
    # A module that had DECLARED a fifth outcome would have rebuilt the counts
    # from it at import; emulate that rebuild so the detail has its counter.
    monkeypatch.setattr(ha, "_ANALYTICS_OUTCOMES", vocab)
    monkeypatch.setattr(ha, "_ANALYTICS_COUNTS", {o: 0 for o in vocab})

    detail = ha._analytics_incident_detail("fallback", "r")

    assert "fifth" in detail, (
        "the incident detail must be DERIVED from _ANALYTICS_OUTCOMES — a "
        "hand-written key list drops a newly declared outcome")
    assert detail["fifth"] == 0, detail
    assert set(detail) == {"outcome", "reason"} | set(vocab), detail


# ── T31–T33 (cycle-8 review) ──────────────────────────────────────────────────


def test_t31_concurrent_resolves_are_serialized(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T31 (cycle-8 P1) — the resolve is SERIALIZED across delivered writes.

    Deciding under ``_ANALYTICS_ALERT_LOCK`` and then calling the store with
    that lock RELEASED let N concurrent delivered writes all evaluate
    ``PENDING and (NOT_BEFORE is None or elapsed)`` against one UNSPENT window
    and all read the store. A reader that saw ``ABSENT`` because a fresh
    incident had not been filed yet could then land AFTER the ``SKIPPED_FRESH``
    completion armed the window and clear it — leaving an incident OPEN in the
    store with ``PENDING=False`` and the alert gate open, so delivered writes
    stop resolving it and the next episode is deduped into the stale incident
    (the D4/#3677 absorbed-episode class). It also falsifies the documented
    read bound ("at most once per window").

    Two threads drive the REAL store through a racing resolve: the first call
    is held inside the store until a second one arrives (bounded — a
    serialized resolve never admits a second, so it simply times out), reports
    ``SKIPPED_FRESH``; the second reports a SLOW ``ABSENT``, so a stale clear
    lands after the arm. The dedup object is real and stays on record.

    RED mutation: drop the resolve SERIALIZATION — the in-flight claim taken
    under ``_ANALYTICS_RESOLVE_LOCK`` — so both threads read the store. This
    reds on ``calls["n"] == 1`` (``calls == 2``) — exit criterion 1.

    It does NOT red on the ``_ANALYTICS_RESOLVE_PENDING is True`` /
    ``_ANALYTICS_RESOLVE_NOT_BEFORE is not None`` assertions, and an earlier
    version of this docstring claimed it did. That claim was wrong: thread 1
    completes ``SKIPPED_FRESH`` and arms the window BEFORE thread 2's slow
    ``ABSENT`` returns, so the ``armed_at_decision`` guard absorbs the stale
    read and both state assertions PASS under this mutation. Exit criterion 2
    is given teeth by T34, whose RED drops the ``armed_at_decision``
    comparison. Verified by running both mutations (see the cycle-9 evidence
    correction on #3820).
    """
    import threading
    import time

    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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
    monkeypatch.setattr(gi, "create_issue", lambda *a, **k: 4242)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    # A REAL dedup object on record — the incident the first resolve reports
    # as ``SKIPPED_FRESH``. Filed through the store, so the assertion "an
    # incident exists while PENDING must not be False" is about the store's
    # own object, not a stand-in.
    store = ha._analytics_alert_store()
    assert store is not None
    assert store.open_incident(
        ha._ANALYTICS_INCIDENT_KIND, "", {"x": 1}) is True
    assert storage.list("ops/alerts/"), "the incident must be on record"

    first_entered = threading.Event()
    second_entered = threading.Event()
    calls = {"n": 0}
    guard = threading.Lock()

    def _racing_resolve(self, kind, org_id="", *, before=None):
        with guard:
            calls["n"] += 1
            n = calls["n"]
        if n == 1:
            # Hold the first resolve INSIDE the store call until a second one
            # also gets there. Bounded: a serialized resolve never admits a
            # second, so it times out and proceeds.
            first_entered.set()
            second_entered.wait(timeout=1.5)
            return ResolveOutcome.SKIPPED_FRESH
        # The stale read: ``ABSENT`` lands AFTER the fresh arm was applied.
        second_entered.set()
        time.sleep(0.1)
        return ResolveOutcome.ABSENT

    monkeypatch.setattr(AlertStore, "resolve_incident_state", _racing_resolve)

    _stub_http(monkeypatch, status=201)
    errors: list = []

    def _delivered_write():
        try:
            assert ha._track_analytics_event("org-31", "e") == "supabase"
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    t1 = threading.Thread(target=_delivered_write)
    t2 = threading.Thread(target=_delivered_write)
    t1.start()
    assert first_entered.wait(timeout=5), "thread 1 never reached the store"
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert not t1.is_alive() and not t2.is_alive(), "a resolve thread wedged"
    assert not errors, errors

    assert storage.list("ops/alerts/"), (
        "the incident is still on record — SKIPPED_FRESH resolves nothing")
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "no PENDING=False while a dedup object exists: the store reported the "
        "incident IS open, so a stale ABSENT read must not clear it — "
        "otherwise delivered writes stop resolving an incident that is on "
        "record and the next episode is absorbed (D4; exit criterion 2)")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "the arm the SKIPPED_FRESH completion applied must survive the "
        "concurrent stale ABSENT — a disarmed window reopens the alert gate "
        "with an incident on record")
    assert calls["n"] == 1, (
        f"the resolve must be SERIALIZED: a second concurrent delivered write "
        f"must re-read the state under the lock and SKIP, not read the store "
        f"again — calls={calls['n']} (exit criterion 1)")


def test_t32_a_paused_kind_does_not_arm_the_alert_gate(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T32 (cycle-8 P2-1, exit criterion 3) — a paused kind arms NOTHING.

    ``open_incident`` returns ``False`` for a SUPPRESSED kind, creating no
    issue and NO dedup object; the caller nonetheless armed the gate
    (``PENDING=True, NOT_BEFORE=now``) on every non-raising path. With nothing
    on record, a pause LIFTED while the sink is still degraded could never be
    reopened: only a delivered write disarms the gate, and a writing sink is
    exactly what the outage forbids — so the ongoing outage would file nothing,
    indefinitely.

    Real store, live pause in ``ops/suppression.json``, three degraded writes
    (the alert threshold). The store must report the fact and the caller must
    not arm.

    RED mutation: ``return True`` on every non-raising path (the pre-cycle-8
    shape) → ``NOT_BEFORE`` is armed with no incident on record and this reds
    on ``NOT_BEFORE is None``.
    """
    from datetime import timedelta

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
    filed: list = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: filed.append((title, body)) or 7)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    storage.upload(
        "ops/suppression.json",
        json.dumps({ha._ANALYTICS_INCIDENT_KIND: {"until": until}}).encode(),
        content_type="application/json")

    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    fallback = _fallback_at(monkeypatch, tmp_path)
    for _ in range(ha._ANALYTICS_FALLBACK_ALERT_AFTER):
        assert ha._track_analytics_event("org-32", "e") == "fallback"

    assert fallback.exists()
    assert filed == [], filed
    assert storage.list("ops/alerts/") == [], (
        "a paused kind leaves NO dedup object — nothing is on record")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is None, (
        "the alert gate must NOT be armed when no incident is on record: a "
        "pause lifted mid-outage could then never be reopened, so the outage "
        "files nothing indefinitely (cycle-8 P2-1; exit criterion 3)")
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "nothing was resolved and nothing is known — the state stays UNKNOWN, "
        "not CLEAN")


def test_t33_the_open_reason_comes_from_one_suppression_read(
        monkeypatch, tmp_path, caplog, real_analytics_alert_store):
    """T33 (cycle-8 P2-2) — the reason reported is the store's own FACT.

    The pre-cycle-8 shape asked ``suppression_active`` a SECOND time, at a
    later instant, to explain the ``open_incident`` boolean. Both are reads of
    a TIME-DEPENDENT predicate: a pause withdrawn between them made the second
    read say "not suppressed", so the log reported ``already open (dedup)``
    for an incident that was never created — sending the operator who set the
    pause hunting for an issue that does not exist (cycle-6 P2-1 re-created in
    the opposite window). ``open_incident_state`` reads the predicate ONCE, so
    the reported reason is the decision's own fact.

    RED mutation: restore the second read (``open_incident`` + a later
    ``suppression_active``) → the withdrawn pause answers ``False`` at the
    second instant, the log says ``already open (dedup)``, and this reds on
    the ``suppressed (kind paused)`` assertion and on ``reads["n"] == 1``.
    """
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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
    monkeypatch.setattr(gi, "create_issue", lambda *a, **k: 8)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    reads = {"n": 0}

    def _paused_then_withdrawn(self, kind):
        reads["n"] += 1
        # Paused at the instant the call DECIDES; withdrawn a moment later —
        # the answer a SECOND read would give.
        return reads["n"] == 1

    monkeypatch.setattr(AlertStore, "_suppressed", _paused_then_withdrawn)

    caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    _fallback_at(monkeypatch, tmp_path)
    for _ in range(ha._ANALYTICS_FALLBACK_ALERT_AFTER):
        assert ha._track_analytics_event("org-33", "e") == "fallback"

    msgs = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "tortoise.hosted_api"]
    assert any("suppressed (kind paused)" in m for m in msgs), msgs
    assert not any("already open (dedup)" in m for m in msgs), (
        "the reason must be the FACT the call decided on — a second read of a "
        "time-dependent predicate reports a dedup hit for an incident that "
        "was never created", msgs)
    assert reads["n"] == 1, (
        f"the suppression predicate must be read ONCE per open attempt — "
        f"reads={reads['n']}; a second read can disagree with the decision")


def test_t34_an_arm_during_the_resolve_survives_the_stale_read(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T34 (cycle-8 P1, exit criterion 2) — a stale `ABSENT` cannot clear an
    arm a concurrent degradation set.

    `_ANALYTICS_RESOLVE_LOCK` serializes resolves against each other, but NOT
    against the open path: `_analytics_open_incident` does the R2 PUT + GitHub
    search + Telegram, and holding the resolve lock across that would block the
    analytics write path. So a degradation can file its incident and arm the
    gate (`PENDING=True, NOT_BEFORE=now`) while a resolve attempt is between its
    store read and its post-call update. The attempt's `ABSENT` is then STALE,
    and applying it leaves an incident OPEN in the store with `PENDING=False` —
    which no delivered write will ever resolve, so the next episode is deduped
    into it (D4). The update therefore applies the CLEAR only when the window is
    still the one the decision was taken against.

    The interleaving is injected INSIDE the real store call — the real
    `_analytics_note_degradation` path files the incident and arms, at the one
    instant the store call is in flight — so the test is deterministic rather
    than a thread race. The dedup object is the store's own.

    RED mutation: drop the `armed_at_decision` comparison (apply the clear
    unconditionally) → `PENDING` is cleared with the incident on record and
    this reds on `PENDING is True`.
    """
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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
    filed: list = []
    monkeypatch.setattr(gi, "create_issue", lambda *a, **k: filed.append(a) or 5151)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    calls = {"n": 0}

    def _degrade_while_resolving(self, kind, org_id="", *, before=None):
        calls["n"] += 1
        # The incident is FILED and the gate ARMED while this resolve attempt
        # is inside the store call: exactly what a concurrent degraded event
        # does. `_analytics_note_degradation` is the real arm path — it files
        # (the store dedups onto the object created just above it) and applies
        # `PENDING=True, NOT_BEFORE=now` under the alert lock.
        self.open_incident(kind, org_id, {"x": 1})
        ha._analytics_note_degradation("dropped", "filed-during-resolve")
        return ResolveOutcome.ABSENT

    monkeypatch.setattr(AlertStore, "resolve_incident_state",
                        _degrade_while_resolving)

    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-34", "e") == "supabase"
    assert calls["n"] == 1, calls

    assert storage.list("ops/alerts/"), "the incident is on record"
    assert filed, "the degradation filed an issue for it"
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "no PENDING=False while a dedup object exists: the arm a concurrent "
        "degradation applied while this resolve was in flight must survive "
        "the stale ABSENT (exit criterion 2)")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "the arm must survive — a disarmed window reopens the alert gate with "
        "an incident on record, so the next episode is absorbed and the "
        "delivered write never resolves it")


def test_t35_a_deduped_incident_deleted_by_its_own_resolve_clears_nothing(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T35 (cycle-9 P1) — the moved-window guard must not keep an arm with NO
    incident behind it.

    ``armed_at_decision`` stops a stale ``ABSENT`` clearing a concurrent
    degradation's arm (T34). But a degradation that DEDUPS onto the incumbent
    the resolve is about to close is armed and then its incident is DELETED —
    the process had ``NOT_BEFORE=None``, saw no known open incident, deduped
    (no new issue) and armed the gate. Keeping that arm on faith leaves
    ``PENDING=True`` with ``NOT_BEFORE`` set and NO dedup object on record: the
    alert gate is shut, so a still-degraded sink files nothing, indefinitely
    (the D4/#3677 absorbed-episode class this lane exists to close).

    Deterministic: the real store carries the incumbent, a real degradation is
    injected INSIDE the real resolve call (dedup + arm), and the real resolve
    then deletes the very object. With no incident on record the moved arm must
    NOT be kept — the state clears and a continued outage files a new incident.

    RED mutation: keep the arm unconditionally in the moved branch (the
    cycle-9 shape — skip ``incident_open``) → ``PENDING`` stays ``True`` and
    ``NOT_BEFORE`` stays set with nothing on record, so this reds on
    ``_ANALYTICS_RESOLVE_PENDING is False`` and the continued outage files
    nothing.

    SCOPE: this closes the ordering where the degradation arms BEFORE the
    resolve's post-call (the cycle-9 P1). A residual same-process ordering — a
    degradation that arms AFTER this clear, having deduped an object the
    resolve then deleted — is NOT closed by it; it is pre-existing (present in
    the cycle-8 shape at the same ~3% rate under a 16-thread mixed stress),
    self-heals on the next delivered write, and is tracked as **#3969**.
    """
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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
    filed: list = []
    monkeypatch.setattr(gi, "create_issue", lambda *a, **k: filed.append(a) or 5150)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    # The incumbent incident: filed through the real store BEFORE the resolve
    # decides, so its ``filed_at`` predates the resolve's ``before`` bound and
    # the real resolve may close it.
    store = ha._analytics_alert_store()
    assert store is not None
    assert store.open_incident(ha._ANALYTICS_INCIDENT_KIND, "", {"x": 1}) is True
    assert storage.list("ops/alerts/"), "the incumbent must be on record"
    filed_before = len(filed)

    real_resolve = AlertStore.resolve_incident_state

    def _dedup_then_resolve(self, kind, org_id="", *, before=None):
        # The concurrent degradation: it DEDUPS onto the incumbent (does NOT
        # file) and ARMS the gate, because the process's `NOT_BEFORE` is None
        # (process-start UNKNOWN). Then the real resolve closes and deletes the
        # very object it deduped onto.
        ha._analytics_note_degradation("dropped", "deduped-during-resolve")
        return real_resolve(self, kind, org_id, before=before)

    monkeypatch.setattr(AlertStore, "resolve_incident_state",
                        _dedup_then_resolve)

    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-35", "e") == "supabase"

    assert storage.list("ops/alerts/") == [], (
        "the resolve deleted the incident it was told to close")
    assert len(filed) == filed_before, (
        "the concurrent degradation DEDUPED onto the incumbent — it must not "
        f"file a second issue; filed={len(filed) - filed_before} new")
    assert ha._ANALYTICS_RESOLVE_PENDING is False, (
        "nothing is on record, so the state must CLEAR: keeping the moved arm "
        "leaves PENDING=True with no incident on record and the alert gate "
        "shut — a still-degraded sink then files nothing, indefinitely "
        "(cycle-9 P1; D4 absorbed-episode class)")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is None, (
        "NOT_BEFORE is the alert gate: with no incident on record it must not "
        "stay armed")

    # The outage CONTINUES. With the arm wrongly kept, `known_open` is True and
    # every later event is silent; only the clear lets the episode re-file.
    _stub_http(monkeypatch, raises=RuntimeError("supabase down"))
    _fallback_at(monkeypatch, tmp_path)
    for _ in range(ha._ANALYTICS_FALLBACK_ALERT_AFTER):
        assert ha._track_analytics_event("org-35", "e") == "fallback"
    assert len(filed) == filed_before + 1, (
        "the continued outage MUST file a new incident — the silent-outage "
        "window is exactly what this test closes")


def test_t36_a_hung_resolve_does_not_serialize_other_delivered_writes(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T36 (cycle-9 P2-2) — the resolve lock is NOT held across the store call.

    ``_ANALYTICS_RESOLVE_LOCK`` used to be held across
    ``resolve_incident_state``, whose R2 read botocore bounds only at its 60 s
    defaults (``R2Storage._s3()`` sets no explicit timeout). A single hung
    read therefore serialized every concurrent delivered write — the
    GitHub-OAuth-callback path — for up to a minute. The in-flight claim now
    bounds the lock hold to the claim itself (microseconds): the store call
    runs with NO lock held, and a second delivered write SKIPS rather than
    blocking.

    The store call is held inside a real resolve until the test releases it.
    A second delivered write must COMPLETE while the first is still blocked,
    and the store must have been called exactly once. Releasing afterwards must
    let the first thread finish — the no-deadlock re-confirmation.

    RED mutation: hold ``_ANALYTICS_RESOLVE_LOCK`` across the store call (the
    cycle-9 shape) → the second write blocks behind the hung read and this reds
    on ``not second.is_alive()``.
    """
    import threading

    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore, ResolveOutcome
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
    monkeypatch.setattr(gi, "create_issue", lambda *a, **k: 4242)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    guard = threading.Lock()

    def _hung_resolve(self, kind, org_id="", *, before=None):
        with guard:
            calls["n"] += 1
        entered.set()
        # Stands in for a ~60 s R2 read. Bounded so a broken run cannot wedge
        # the suite; the release below ends it immediately in the green path.
        release.wait(timeout=20)
        return ResolveOutcome.ABSENT

    monkeypatch.setattr(AlertStore, "resolve_incident_state", _hung_resolve)

    _stub_http(monkeypatch, status=201)
    errors: list = []

    def _delivered_write():
        try:
            assert ha._track_analytics_event("org-36", "e") == "supabase"
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    first = threading.Thread(target=_delivered_write)
    first.start()
    assert entered.wait(timeout=5), "thread 1 never reached the store call"

    second = threading.Thread(target=_delivered_write)
    second.start()
    second.join(timeout=10)
    assert not second.is_alive(), (
        "a delivered write blocked behind a hung resolve — the resolve lock "
        "is held across the store call, so one hung R2 read serializes every "
        "write on the OAuth path (cycle-9 P2-2)")
    assert calls["n"] == 1, (
        f"the second write must SKIP the in-flight resolve, not start a "
        f"second store read — calls={calls['n']}")

    release.set()
    first.join(timeout=20)
    assert not first.is_alive(), (
        "the resolve must complete once the read returns — a permanently "
        "wedged claim would be a deadlock regression")
    assert not errors, errors
    assert ha._ANALYTICS_RESOLVE_PENDING is False, (
        "the completed ABSENT, with no moving window, clears the state")


def test_t37_a_late_arm_after_the_presence_read_survives_the_clear(
        monkeypatch, tmp_path, real_analytics_alert_store):
    """T37 (cycle-11 P1) — the presence read is NOT atomic with the clear.

    T35's moved-window path asks the store, read-only, whether an incident is
    still on record before keeping the moved arm. That read is taken OFF every
    lock (the alert lock must not be held across store I/O) and the CLEAR is
    written under the lock afterwards — with nothing in between re-validating
    the state the read was taken against. The arm site's ``known_open``
    DECISION is taken under the alert lock, but its WRITE lands only after
    ``_analytics_open_incident`` returns (a GitHub search, a file, a Telegram
    push), so a degradation that decided ``should_alert`` while ``NOT_BEFORE``
    was ``None`` can FILE an object and arm AFTER the probe's read and BEFORE
    the clear. An unconditional clear then leaves that incident OPEN in the
    store with ``PENDING=False`` — no delivered write resolves it, and the
    next episode dedups into it (cycle-8 criterion 2, the D4/#3677
    absorbed-episode class).

    Deterministic: the moved arm is injected inside the real resolve (T35's
    dedup shape) and the late arm is injected INSIDE the real presence probe,
    after its read returns — the exact ordering, not a thread race.

    RED mutation: restore the unconditional clear (drop the ``moved_to``
    re-validation) → ``PENDING`` is cleared while the late-armed object is on
    record, so this reds on ``PENDING is True``.

    The final assertion is the same defect's second guard: with the arm
    wrongly cleared the resolve is not even eligible, so a later delivered
    write cannot drive the store and the open incident is stranded.

    SCOPE: this pins the wrong-clear ordering fixed in #3820; the durable
    epoch/generation fix for the whole class is #3969.
    """
    import time as _time

    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp
    from tortoise.alert_store import AlertStore
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
    filed: list = []
    monkeypatch.setattr(gi, "create_issue",
                        lambda *a, **k: filed.append(a) or 5152)
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(gi, "close_issue", lambda *a, **k: None)
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    # The incumbent: filed through the real store BEFORE the resolve decides,
    # so its ``filed_at`` predates the resolve's ``before`` bound and the real
    # resolve may close it.
    store = ha._analytics_alert_store()
    assert store is not None
    assert store.open_incident(
        ha._ANALYTICS_INCIDENT_KIND, "", {"x": 1}) is True
    assert storage.list("ops/alerts/"), "the incumbent must be on record"

    real_resolve = AlertStore.resolve_incident_state
    real_probe = AlertStore.incident_open
    calls = {"resolve": 0, "late_arm": 0}

    def _resolve_probe(self, kind, org_id="", *, before=None):
        calls["resolve"] += 1
        if calls["resolve"] == 1:
            # Moves the window DURING the store call (T35's shape), so the
            # post-call takes the moved branch and issues the presence read.
            ha._analytics_note_degradation("dropped", "deduped-during-resolve")
        return real_resolve(self, kind, org_id, before=before)

    def _late_arm_probe(self, kind, org_id=""):
        # The READ first — the incumbent the resolve deleted is gone, so the
        # moved arm has nothing behind it and the moved branch would clear.
        open_now = real_probe(self, kind, org_id)
        assert open_now is False, "the resolve deleted the incumbent"
        # …then the DEFERRED arm write lands HERE: a degradation whose
        # `should_alert` decision was taken while `NOT_BEFORE` was None runs
        # its PUT and its arm only after `_analytics_open_incident` returns —
        # after the read above and before the clear below. The arm site does
        # NOT re-check `NOT_BEFORE is None`, so it lands.
        assert ha._analytics_open_incident("dropped", "late-arm") is True
        with ha._ANALYTICS_ALERT_LOCK:
            ha._ANALYTICS_RESOLVE_PENDING = True
            ha._ANALYTICS_RESOLVE_NOT_BEFORE = _time.monotonic()
        calls["late_arm"] += 1
        return open_now

    monkeypatch.setattr(AlertStore, "resolve_incident_state", _resolve_probe)
    monkeypatch.setattr(AlertStore, "incident_open", _late_arm_probe)

    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-37", "e") == "supabase"
    assert calls["resolve"] == 1, calls
    assert calls["late_arm"] == 1, (
        "the raced presence read must have been taken")

    assert storage.list("ops/alerts/"), (
        "the late arm's dedup object is on record — the clear must not strand "
        "the incident it belongs to")
    assert ha._ANALYTICS_RESOLVE_PENDING is True, (
        "the late arm lands AFTER the presence read, so that read established "
        "nothing about it: the clear must be withheld, or the incident is "
        "left open in the store with PENDING=False and no delivered write "
        "ever resolves it (cycle-8 criterion 2)")
    assert ha._ANALYTICS_RESOLVE_NOT_BEFORE is not None, (
        "NOT_BEFORE is also the alert gate — clearing it reopens the gate with "
        "an incident on record, so the next episode is deduped into it")

    # …and it is STILL RESOLVABLE: a later delivered write must drive the
    # store again. With the arm wrongly cleared, PENDING is False, the
    # resolve is not even eligible, and the open incident is stranded.
    monkeypatch.setattr(AlertStore, "incident_open", real_probe)
    _stub_http(monkeypatch, status=201)
    assert ha._track_analytics_event("org-37", "e") == "supabase"
    assert calls["resolve"] == 2, (
        "the arm this test pins must keep the episode resolvable by a later "
        "delivered write — with the arm cleared the resolve is skipped and "
        f"the open incident is stranded (calls={calls['resolve']})")
