"""#3540 ruling B — the consolidated source-management operation.

The ruling's conditions are behavioural, so they are pinned behaviourally:

- the assistant **may request, never self-grant** — an unauthorised or
  not-connected request is refused, and the refusal names the step that is
  actually missing rather than failing opaquely;
- **credentials never pass through the model** — asserted by INTROSPECTION on
  the tool's signature, because a parameter added later is exactly how a token
  would arrive;
- the change is **attributable** — the log line is emitted for refusals too;
- `list` reports **every** source's state and its default in one call, and
  `agent_sessions` reports its real default (`on`, #1927) rather than a
  deny-by-default story the product does not implement.

`github_documents` is covered here because it is the gap the ruling closes: it
had no tool at all, and it must now be settable through the SHARED operation
without a per-source tool appearing.
"""

from __future__ import annotations

import inspect
import json
import time

import pytest

import tortoise.mcp_server as m
from tortoise import hosted_api as ha


@pytest.fixture
def ctx(monkeypatch):
    """Org context + stubbed control-plane seams; nothing touches a real plane."""
    m._current_org_id.set("org_test")
    calls: dict = {"writes": [], "cleared": False, "logs": [], "jobs": [],
                   "events": []}

    state = {"github_connected": True, "session_recording": True,
             "issues_enabled": True, "docs_enabled": True}
    monkeypatch.setattr(m, "_onboarding_state", lambda: dict(state))
    # `**kw` keeps the harness tolerant of the pre-fix `connection_level=`
    # keyword, so a regression shows up as a SEMANTIC assertion failure (the
    # write / the projection) rather than a TypeError from this lambda.
    monkeypatch.setattr(
        m, "_log_source_change",
        lambda org, src, act, out, scope="source", **kw:
            (calls["logs"].append((org, src, act, out, scope)),
             calls["events"].append(("log", out)))[0],
    )

    def _update(org_id, _echo=True, **fields):
        calls["writes"].append(fields)
        calls["events"].append(("write", fields))
        state.update(fields)
        return dict(state)

    monkeypatch.setattr(ha, "_update_onboarding_state", _update)
    monkeypatch.setattr(ha, "_github_credentials", lambda org_id: (None, None))
    monkeypatch.setattr(ha, "_clear_github_credentials",
                        lambda org_id: calls.update(cleared=True))
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda org_id, kind: calls["jobs"].append(kind)
                        or (f"job-{kind}", False))
    calls["state"] = state
    yield calls
    m._current_org_id.set(None)


# ── the signature: no credential can arrive through the model ───────────────
def test_the_tool_takes_no_credential_parameter():
    """Ruling B: "Credentials never pass through the model".

    Introspection, not a call — the property is about the CONTRACT. A token,
    code, or secret parameter appearing here is the failure, whatever the body
    does with it.
    """
    params = set(inspect.signature(m.tortoise_manage_sources).parameters)
    assert params == {"action", "source"}


# ── list ────────────────────────────────────────────────────────────────────
def test_list_covers_all_three_sources_with_their_defaults(ctx):
    out = m.tortoise_manage_sources("list")

    assert set(out["sources"]) == {"github_issues", "github_documents",
                                   "agent_sessions"}
    assert out["sources"]["agent_sessions"]["default"] == "on"
    # The GitHub flags are ON by default in the PRODUCT (`issues_enabled: True`
    # / `docs_enabled: True` in both default dicts, #1924), so a `default: "off"`
    # here would sit next to `enabled: true` in the same payload.
    assert out["sources"]["github_issues"]["default"] == "on"
    assert out["sources"]["github_documents"]["default"] == "on"
    # The raw projection rides along — the tool must not hide state behind its
    # own interpretation of it.
    assert "onboarding" in out
    # The two GitHub sources must expose the SAME key set: a caller iterating
    # `sources` must not have to know which one carries the index state. Both
    # tools are off the TARGET list (`removed` there means "not a target", never
    # "not served") and both are STILL SERVED today: `tortoise_onboarding_state`
    # returns the raw projection, a second reader of this state, while
    # `tortoise_onboarding_github_status` is served but reads no index state.
    issues = set(out["sources"]["github_issues"])
    documents = set(out["sources"]["github_documents"])
    assert issues == documents, (issues ^ documents)
    assert {"indexed", "indexed_at"} <= issues, issues


def test_list_reports_the_issues_index_from_the_issues_key(ctx):
    """A key set proves nothing about the VALUES: both sources share one helper,
    so reading the wrong state key (`github_docs_indexed` for the issues source)
    used to pass — the keys still matched."""
    ctx["state"]["github_indexed"] = True
    ctx["state"]["github_indexed_at"] = "2026-10-17T00:00:00+00:00"
    ctx["state"]["github_docs_indexed"] = False
    ctx["state"]["github_docs_indexed_at"] = None

    out = m.tortoise_manage_sources("list")

    assert out["sources"]["github_issues"]["indexed"] is True
    assert (out["sources"]["github_issues"]["indexed_at"]
            == "2026-10-17T00:00:00+00:00")
    assert out["sources"]["github_documents"]["indexed"] is False
    assert out["sources"]["github_documents"]["indexed_at"] is None


def test_list_projects_each_github_source_from_its_own_intent_flag(ctx):
    """#1924: the CONNECTION is shared, the enable INTENT is per source.

    Projecting `enabled` from `github_connected` for both sources would show a
    source as on after the team switched it off (and vice versa).
    """
    ctx["state"]["docs_enabled"] = False
    out = m.tortoise_manage_sources("list")

    assert out["sources"]["github_issues"]["enabled"] is True
    assert out["sources"]["github_documents"]["enabled"] is False, (
        "docs_enabled=False was ignored — `enabled` is being read off the "
        "shared connection instead of the per-source intent flag (#1924)")
    assert out["sources"]["github_documents"]["connection_connected"] is True, (
        "the connection must be reported separately, never conflated "
        "with the source's enable intent")
    assert out["sources"]["github_documents"]["scope"] == "source"


def test_list_reflects_the_session_recording_off_switch(ctx):
    ctx["state"]["session_recording"] = False

    assert m.tortoise_manage_sources("list")["sources"]["agent_sessions"][
        "enabled"] is False


# ── refusal, not self-grant ─────────────────────────────────────────────────
def test_enabling_a_github_source_without_a_connection_is_refused(ctx):
    out = m.tortoise_manage_sources("enable", "github_documents")

    assert "error" in out
    assert "tortoise_onboarding_github_connect" in out["error"]
    assert ctx["writes"] == [], "a refusal must not write state"
    assert ("org_test", "github_documents", "enable",
            "refused_not_connected", "source") in ctx["logs"], (
        "a refusal must be attributable too, not only the successes")


def test_an_unknown_action_is_refused(ctx):
    out = m.tortoise_manage_sources("toggle", "agent_sessions")
    assert "error" in out and "list|enable|disable" in out["error"]
    assert ctx["writes"] == []


def test_an_unknown_source_is_refused_and_the_roster_is_named(ctx):
    out = m.tortoise_manage_sources("enable", "ftp_shares")
    assert "error" in out
    assert "github_documents" in out["error"], "the refusal names valid sources"
    assert ctx["writes"] == []


# ── agent sessions: the #1927 default is honoured, not reversed ────────────
def test_disabling_agent_sessions_writes_the_off_switch(ctx):
    out = m.tortoise_manage_sources("disable", "agent_sessions")

    assert out["status"] == "disabled"
    assert ctx["writes"] == [{"session_recording": False,
                              "capture_revised": True}]


def test_enabling_agent_sessions_writes_even_when_the_default_is_on(ctx):
    """The default being ON is not the same fact as a write having landed.

    `session_recording` defaults ON, so a fresh install reads `enabled: true`
    without anything having been written; `capture_revised` is the marker that a
    recording-state write has happened (both directions write it — it is NOT a
    consent record, and none should be invented). Short-circuiting on `enabled`
    alone made the SKILL's yes-branch ("both write session_recording +
    capture_revised") a silent no-op on exactly the install it is written for.
    """
    ctx["state"].pop("capture_revised", None)      # no write on record yet
    assert ctx["state"]["session_recording"] is True

    out = m.tortoise_manage_sources("enable", "agent_sessions")

    assert out["status"] == "enabled", out
    assert ctx["writes"] == [{"session_recording": True,
                              "capture_revised": True}], ctx["writes"]


def test_enabling_agent_sessions_reports_already_on_without_writing(ctx):
    """Ruling B says deny-by-default; #1927 says this source is default ON.

    The ruling does not reverse a recorded default, so `enable` here reports the
    truth and changes nothing — it does not flip the product's behaviour. The
    state already records a recording write (`capture_revised`), so there is
    nothing left to land: `capture_revised` is the backward-compat COMPANION KEY
    that marks a recording-state write as having happened, NOT a consent record
    — no disclosure acceptance is stored, or should be invented, for it.
    """
    ctx["state"]["capture_revised"] = True
    out = m.tortoise_manage_sources("enable", "agent_sessions")

    assert out["status"] == "already_on"
    assert out["default"] == "on"
    assert "#1927" in out["note"]
    assert ctx["writes"] == [], "no write when there is nothing to change"


# ── GitHub disable is PER-SOURCE, never a disconnect (#1924) ───────────────
#
# The pre-fix behaviour cleared the stored credential and wrote
# `github_connected=False`, i.e. a full disconnect that also killed the sibling
# source and forced a fresh OAuth round-trip just to hide one source — exactly
# what #1924 removed from the dashboard's off-toggle. These tests pin the
# restored invariant from the TOOL's side, so the tool cannot re-introduce it.
def test_disabling_github_issues_writes_only_its_intent_flag(ctx):
    out = m.tortoise_manage_sources("disable", "github_issues")

    assert out["status"] == "disabled"
    assert out["scope"] == "source"
    assert ctx["writes"] == [{"issues_enabled": False}], (
        "disable wrote more than the source's own enable intent: "
        f"{ctx['writes']}")
    assert ctx["state"]["github_connected"] is True, (
        "disabling one source disconnected GitHub (#1924 regression)")
    assert ctx["state"]["docs_enabled"] is True, (
        "disabling github_issues switched the sibling source off too")
    assert ctx["cleared"] is False, (
        "the stored credential was cleared — the connection was torn down")
    # The OUTCOME of THIS branch, not just of the recording branch: mutating the
    # github_issues success log to `error_flag_write` used to leave all 31 tests green.
    assert any(e[2] == "disable" and e[3] == "ok" for e in ctx["logs"]), (
        ctx["logs"])


def test_disabling_github_documents_writes_its_own_flag(ctx):
    """The flag must depend on WHICH source was named, not on a shared path."""
    m.tortoise_manage_sources("disable", "github_documents")

    assert ctx["writes"] == [{"docs_enabled": False}]
    assert ctx["state"]["issues_enabled"] is True
    assert ctx["state"]["github_connected"] is True


def test_disable_names_the_enforcement_gap_instead_of_claiming_custody(ctx):
    """The flag is intent-only today: the payload must SAY so.

    Claiming a source is off while nothing server-side honours the flag would
    make the tool lie about what it did — the same class of failure as the
    connection-level teardown, one step quieter.
    """
    out = m.tortoise_manage_sources("disable", "github_documents")

    assert out["enforcement"] == "intent-only"
    assert "INTENT ONLY" in out["note"]
    assert "not cancelled" in out["note"], (
        "in-flight work must be named, not implied")
    assert out["connection_untouched"] is True
    assert out["sibling_untouched"] == "github_issues"


def test_disabling_an_already_off_source_writes_nothing(ctx):
    ctx["state"]["issues_enabled"] = False

    out = m.tortoise_manage_sources("disable", "github_issues")

    assert out["status"] == "already_off"
    assert ctx["writes"] == [], "an idempotent disable must not write"


def test_enable_dispatches_to_that_sources_own_index_job(ctx, monkeypatch):
    """`enable` still replaces `tortoise_onboarding_github_index`.

    The retired name points callers here (#3883), so this call must start the
    source's index job — and the RIGHT one: docs for github_documents, github
    for github_issues.
    """
    monkeypatch.setattr(ha, "_github_credentials",
                        lambda org_id: ("enc-token", "acme"))
    ctx["state"]["docs_enabled"] = False

    docs = m.tortoise_manage_sources("enable", "github_documents")
    issues = m.tortoise_manage_sources("enable", "github_issues")

    assert ctx["jobs"] == ["docs", "github"]
    assert docs["job_id"] == "job-docs" and issues["job_id"] == "job-github"
    assert {"docs_enabled": True} in ctx["writes"], (
        "enable must restore the source's enable intent, not only start a job")
    assert "enc-token" not in str(docs) and "enc-token" not in str(issues), (
        "the stored credential leaked into a tool response")


# ── the retired names still answer (#3883) ─────────────────────────────────
def test_the_retired_switches_point_at_this_call():
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY
    by_name = {t.name: t for t in RETIRED_TOOL_REGISTRY}

    for name, arg in (("tortoise_onboarding_github_index",
                       'source="github_issues"'),
                      ("tortoise_onboarding_session_recording",
                       'source="agent_sessions"')):
        pointer = by_name[name].retired_use_instead
        assert "tortoise_manage_sources" in pointer
        assert arg in pointer


# ── defaults are the product's, not this tool's invention ──────────────────
def test_defaults_match_the_products_own_default_state(ctx):
    """`list` must not report a default the product does not implement.

    Derived from `DEFAULT_ONBOARDING_STATE` rather than restated, so the two
    cannot drift apart again.
    """
    from tortoise.hosted_api import DEFAULT_ONBOARDING_STATE as defaults

    out = m.tortoise_manage_sources("list")
    for source, spec in m._SOURCE_ROSTER.items():
        expected = "on" if defaults.get(spec["enable_flag"], True) else "off"
        assert out["sources"][source]["default"] == expected, source


# ── the self-host projection NESTS the operational keys ────────────────────
def test_selfhost_nested_operational_flags_are_honoured(ctx, monkeypatch):
    """`read_selfhost_state` returns `operational: {...}`, not a flat dict.

    Reading only the top level reported `agent_sessions.enabled: true` for a
    stored `operational.session_recording: false`, and made `enable` answer
    `already_on` while writing nothing — stranding the pinned re-enable path
    ("re-sets the setting regardless of capture_revised").
    """
    nested = {"operational": {"session_recording": False,
                               "github_connected": True}}
    monkeypatch.setattr(m, "_onboarding_state", lambda: dict(nested))

    listed = m.tortoise_manage_sources("list")
    assert listed["sources"]["agent_sessions"]["enabled"] is False

    out = m.tortoise_manage_sources("enable", "agent_sessions")
    assert out["status"] == "enabled", "a stored OFF flag must not read as on"
    assert ctx["writes"] == [{"session_recording": True,
                              "capture_revised": True}]


# ── a NEW job must be spawned by the source's OWN runner ──────────────────
def test_enable_spawns_the_matching_runner_when_the_job_is_new(ctx, monkeypatch):
    """Dispatched from a WORKER THREAD, which is how FastMCP runs a sync tool.

    On such a thread `asyncio.get_event_loop()` returns a loop that is not
    running, so `create_task` scheduled nothing while the tool answered
    `enabled` and left a minted job claim blocking the org for 30 minutes.
    Calling this on an asyncio loop (as the previous version of this test did)
    hid that entirely.
    """
    import time

    import anyio

    ran: list[tuple[str, str]] = []

    async def _docs(job_id, org_id, gh_org, repo):
        ran.append(("docs", job_id))

    async def _issues(job_id, org_id, gh_org, repo):
        ran.append(("issues", job_id))

    monkeypatch.setattr(ha, "_github_credentials",
                        lambda org_id: ("enc-token", "acme"))
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda org_id, kind: (f"job-{kind}", True))
    monkeypatch.setattr(ha, "_run_docs_indexing", _docs)
    monkeypatch.setattr(ha, "_run_indexing", _issues)

    result: dict = {}

    # anyio (not a bare thread) is FastMCP's own dispatch: it propagates the
    # contextvars that carry the org, while the tool body still runs on a
    # worker thread with NO running loop — the exact production context.
    async def _go():
        return await anyio.to_thread.run_sync(
            lambda: m.tortoise_manage_sources("enable", "github_documents"))

    result.update(anyio.run(_go))
    assert "error" not in result, result
    assert result["state"] == "started"

    for _ in range(100):
        if ran:
            break
        time.sleep(0.05)
    assert ran == [("docs", result["job_id"])], (
        "github_documents must spawn the DOCS runner, from a worker thread")


# ── honest states: unknown is not "on", and no org is not success ────────
def test_unreadable_state_is_not_reported_as_on(ctx, monkeypatch):
    monkeypatch.setattr(m, "_onboarding_state",
                        lambda: {"operational": "unavailable"})

    listed = m.tortoise_manage_sources("list")
    assert listed["sources"]["agent_sessions"]["enabled"] is None
    # The sentinel must not leak into the boolean fields either: a plain
    # `object()` is truthy, so `bool(_state_get(...))` reported a graph-down
    # team as connected AND indexed.
    assert listed["sources"]["github_issues"]["connection_connected"] is None
    assert listed["sources"]["github_documents"]["indexed"] is None
    assert listed["sources"]["github_documents"]["indexed_at"] is None
    # The sentinel is a raw `object()`: handing it to the caller makes the
    # tool result unserialisable, so `list` fails instead of answering.
    json.dumps(listed)

    out = m.tortoise_manage_sources("enable", "agent_sessions")
    assert "could not be read" in out["error"]
    assert ctx["writes"] == [], "an unknown state must not be written over"


def _stub_heal(monkeypatch, healed):
    """Stub the #1845 heal both REST twins run BEFORE they walk (#3540).

    `_heal_github_org` is `async` and hits `GET /user`; the tool must await it
    from a sync handler, so the seam is the coroutine itself.
    """
    seen: list[tuple] = []

    async def _heal(org_id, encrypted, stored_org):
        seen.append((org_id, encrypted, stored_org))
        return healed(stored_org) if callable(healed) else healed

    monkeypatch.setattr(ha, "_heal_github_org", _heal)
    return seen


def test_enable_refuses_when_the_stored_credential_has_no_org(ctx, monkeypatch):
    """With the heal unable to derive a login, `None` would reach the walk as
    `f"{org}/{repo}"`, so a success answer would be a lie."""
    _stub_heal(monkeypatch, lambda stored: stored)
    monkeypatch.setattr(ha, "_github_credentials", lambda org_id: ("enc", None))

    out = m.tortoise_manage_sources("enable", "github_issues")

    assert "GitHub org unknown" in out["error"]
    assert ctx["writes"] == []
    assert ctx["jobs"] == []


def test_enable_heals_a_missing_stored_org_instead_of_demanding_a_reconnect(
        ctx, monkeypatch):
    """#1845: both REST twins heal the stored org, and the heal's own docstring
    says a reconnect is NOT required — so the consolidated call must not refuse
    a state the call it replaces (and REST) handles.

    Mutation: delete the heal (the pre-fix body read `_github_credentials`
    straight into the refusal) and this reds with "GitHub org unknown"."""
    ran: list[str | None] = []

    async def _issues(job_id, org_id, org, repos):
        ran.append(org)

    seen = _stub_heal(monkeypatch, "acme")
    monkeypatch.setattr(ha, "_github_credentials", lambda org_id: ("enc", None))
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda org_id, kind: (f"job-{kind}", True))
    monkeypatch.setattr(ha, "_run_indexing", _issues)

    out = m.tortoise_manage_sources("enable", "github_issues")

    assert "error" not in out, out
    assert seen == [("org_test", "enc", None)], (
        "the heal was skipped — the stored org was used raw")
    for _ in range(100):
        if ran:
            break
        time.sleep(0.05)
    assert ran == ["acme"], (
        f"the walk was not given the HEALED login: {ran!r}")


def test_enable_heals_the_pre_1845_uuid_org_before_walking(ctx, monkeypatch):
    """The pre-#1845 connect stored the internal org_id UUID as `github_org`.

    A truthiness guard passes that value straight into the walk, where every
    `f"{org}/{repo}"` 404s while the tool answers `enabled`; the heal derives
    the real login. Mutation: drop the heal and the walk receives the UUID."""
    ran: list[str | None] = []

    async def _issues(job_id, org_id, org, repos):
        ran.append(org)

    _stub_heal(monkeypatch, "acme")
    monkeypatch.setattr(ha, "_github_credentials",
                        lambda org_id: ("enc", org_id))
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda org_id, kind: (f"job-{kind}", True))
    monkeypatch.setattr(ha, "_run_indexing", _issues)

    out = m.tortoise_manage_sources("enable", "github_issues")

    assert "error" not in out, out
    for _ in range(100):
        if ran:
            break
        time.sleep(0.05)
    assert ran == ["acme"], (
        "the walk was handed the stored org instead of the healed login: "
        f"{ran!r}")


def test_enable_refuses_when_the_stored_org_is_the_internal_id(ctx, monkeypatch):
    """If the heal cannot repair the UUID, fail CLOSED rather than walk it.

    `_heal_github_org` returns the stored value when its `GET /user` fails, so
    a healed-but-still-a-UUID result must not be treated as a login.
    """
    _stub_heal(monkeypatch, lambda stored: stored)
    monkeypatch.setattr(ha, "_github_credentials",
                        lambda org_id: ("enc", org_id))

    out = m.tortoise_manage_sources("enable", "github_issues")

    assert "GitHub org unknown" in out["error"]
    assert ctx["jobs"] == []


def test_enable_refuses_when_the_heal_raises(ctx, monkeypatch):
    """A raising heal is best-effort by contract — refuse, do not walk."""

    async def _boom(org_id, encrypted, stored_org):
        raise RuntimeError("GET /user failed")

    monkeypatch.setattr(ha, "_heal_github_org", _boom)
    monkeypatch.setattr(ha, "_github_credentials", lambda org_id: ("enc", None))

    out = m.tortoise_manage_sources("enable", "github_issues")

    assert "GitHub org unknown" in out["error"]
    assert ctx["jobs"] == []


def test_a_failed_spawn_does_not_leave_the_claim_behind(ctx, monkeypatch):
    """A minted claim blocks the org's next start for 30 minutes."""
    monkeypatch.setattr(ha, "_github_credentials", lambda org_id: ("enc", "acme"))
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda org_id, kind: ("job-x", True))
    monkeypatch.setattr(m, "_spawn_index_runner", lambda *a, **k: False)
    ha._INDEX_JOBS["job-x"] = {"status": "started", "org_id": "org_test"}
    ha._INDEX_JOB_OWNERS["job-x"] = "owner"
    try:
        out = m.tortoise_manage_sources("enable", "github_issues")
        assert "could not be started" in out["error"]
        assert "job-x" not in ha._INDEX_JOBS
        assert "job-x" not in ha._INDEX_JOB_OWNERS
    finally:
        ha._INDEX_JOBS.pop("job-x", None)
        ha._INDEX_JOB_OWNERS.pop("job-x", None)


def test_a_failed_flag_write_says_the_walk_started(ctx, monkeypatch):
    """The walk is running but the source still reads OFF — say both."""
    monkeypatch.setattr(ha, "_github_credentials", lambda org_id: ("enc", "acme"))
    monkeypatch.setattr(ha, "_start_index_job",
                        lambda org_id, kind: ("job-f", True))
    monkeypatch.setattr(m, "_spawn_index_runner", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(ha, "_update_onboarding_state", _boom)
    ctx["state"]["issues_enabled"] = False

    out = m.tortoise_manage_sources("enable", "github_issues")

    assert "could not be written" in out["error"]
    assert out["state"] == "started", "the walk DID start; do not imply it did not"


def test_a_failed_disable_write_is_an_error_payload_not_an_exception(ctx,
                                                                  monkeypatch):
    """Every declared status carries the same fields — and no traceback.

    The enable and agent-sessions writes were guarded first; this one escaped
    as a raw exception, so a caller branching on `enforcement` got a
    traceback instead of the documented payload.
    """

    def _boom(*a, **k):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(ha, "_update_onboarding_state", _boom)

    out = m.tortoise_manage_sources("disable", "github_issues")

    assert "could not be written" in out["error"]
    assert "error_flag_write" in [entry[3] for entry in ctx["logs"]]


def test_every_enable_flag_is_a_registered_state_key():
    """A mistyped flag is silently dropped by the router (fail-closed) while
    the tool still answers `disabled` — so pin the key to the registry."""
    from tortoise.hosted_api import _ALLOWED_STATE_KEYS

    for source, spec in m._SOURCE_ROSTER.items():
        assert spec["enable_flag"] in _ALLOWED_STATE_KEYS, source


# ── refusals and writes are attributable ──────────────────────────────────
def test_refusals_are_logged_too(ctx):
    m.tortoise_manage_sources("nonsense", "github_issues")
    m.tortoise_manage_sources("enable", "not_a_source")

    outcomes = [entry[3] for entry in ctx["logs"]]
    assert "refused_action" in outcomes
    assert "refused_source" in outcomes


@pytest.mark.parametrize(
    "source, initial, action, expected",
    [
        ("github_issues", True, "disable", "disabled"),
        ("github_issues", False, "disable", "already_off"),
        ("github_issues", False, "enable", "enabled"),
        # `enable` on an already-on GitHub source RE-INDEXES, so it answers
        # `enabled` — only agent_sessions has an `already_on` short-circuit.
        ("github_issues", True, "enable", "enabled"),
        ("agent_sessions", True, "disable", "disabled"),
        ("agent_sessions", False, "disable", "already_off"),
        ("agent_sessions", False, "enable", "enabled"),
        ("agent_sessions", True, "enable", "already_on"),  # write on record
    ],
)
def test_every_state_change_payload_carries_the_same_fields(
        ctx, monkeypatch, source, initial, action, expected):
    """`_source_payload`'s contract: ONE key set, for every documented status.

    The fields used to be assembled per branch, so `default` appeared on only
    some responses and `enable_flag` could be deleted from the shared builder
    with the whole suite still green. A caller branching on `enforcement` must
    not meet a KeyError on one of the documented `status` values.
    """
    if source == "github_issues":
        ctx["state"]["issues_enabled"] = initial
    else:
        ctx["state"]["session_recording"] = initial
    if source == "agent_sessions" and action == "enable" and initial:
        # `already_on` needs BOTH the flag and a write on record.
        ctx["state"]["capture_revised"] = True
    if source == "github_issues" and action == "enable":
        # Without a stored credential the tool REFUSES (an `{"error": ...}`
        # payload with no `status`) — a different contract, tested elsewhere.
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: ("enc-token", "acme"))

    out = m.tortoise_manage_sources(action, source)

    assert out.get("status") == expected, out
    assert set(out) >= {"status", "enforcement", "default", "enable_flag"}, out


def test_a_write_logs_the_attempt_before_the_write(ctx):
    """A crash inside the write must still leave the attempt on the record.

    Asserted against the WRITE's position in the event stream, not merely
    against the order of two log lines: moving the "attempt" line after the
    write used to leave every test green.
    """
    m.tortoise_manage_sources("disable", "agent_sessions")

    kinds = [kind for kind, _payload in ctx["events"]]
    assert "write" in kinds, ctx["events"]
    first_write = kinds.index("write")
    assert ("log", "attempt") in ctx["events"][:first_write], ctx["events"]
    # The OUTCOME half matters too: a mutation that logs `error_flag_write` on
    # the success path used to leave the suite green.
    assert any(e[2] == "disable" and e[3] == "ok" for e in ctx["logs"]), (
        ctx["logs"])


def test_disabling_an_already_off_session_writes_nothing(ctx):
    ctx["state"]["session_recording"] = False

    out = m.tortoise_manage_sources("disable", "agent_sessions")

    assert out["status"] == "already_off"
    assert ctx["writes"] == [], "an idempotent disable must not write"
