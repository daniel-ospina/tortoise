"""#3821 — an unregistered event/prop/key is never dropped SILENTLY.

The defect: allowlist filters (analytics props, the onboarding-state router,
the PATCH front door, the writer's FLOW strip, the beacon enum check) all had
a membership test with no ``else``. A key that failed the test vanished — no
error, no counter, no log — so a dropped signal was indistinguishable from an
event that never fired, and the debugging direction was inverted: you hunt a
product bug while the product is fine and the INSTRUMENT ate the event.

The fix is one choke point, :func:`tortoise.hosted_api._report_unregistered`:
never forwards (PII intact), always counts, emits a bounded WARN, and raises
only in strict mode (env read at call time).

The load-bearing test here is
``test_every_emitted_prop_key_is_allowlisted`` — a structural AST pass over
every emit site. It REDs on unmodified ``origin/main`` because the Stripe
webhook emits ``plan``/``tier``, which were never registered (the live,
~5-week silent loss since ``c928b0316``).
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise import hosted_api as ha

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOSTED_API = _REPO_ROOT / "tortoise" / "hosted_api.py"
_MCP_SERVER = _REPO_ROOT / "tortoise" / "mcp_server.py"

_TELEMETRY_FUNCS = ("_track_analytics_event", "_track_onboarding_event")


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_telemetry_state(monkeypatch):
    """Strict off + an empty counter/dedup set per test.

    The counter is process-global by design (monotonic in production), so a
    test must reset it — that is a test-hygiene concern, not a code smell.
    """
    monkeypatch.delenv(ha._TELEMETRY_STRICT_ENV, raising=False)
    ha._TELEMETRY_DROP_COUNTS.clear()
    ha._TELEMETRY_DROP_REPORTED.clear()
    yield
    ha._TELEMETRY_DROP_COUNTS.clear()
    ha._TELEMETRY_DROP_REPORTED.clear()


@pytest.fixture
def state_seams(monkeypatch):
    """Route the registry legs of `_update_onboarding_state` to a dict."""
    monkeypatch.setattr(ha, "_get_onboarding_state", lambda org_id: {})
    monkeypatch.setattr(ha, "_get_onboarding_projection", lambda org_id: {})
    written: dict = {}

    def fake_write(org_id, state):
        written["org_id"] = org_id
        written["state"] = dict(state)

    monkeypatch.setattr(ha, "_write_onboarding_state", fake_write)
    return written


@pytest.fixture
def patch_client(tmp_path, monkeypatch):
    """TestClient for PATCH /v1/onboarding/state with auth + seams stubbed.

    Mirrors the seam in tests/test_onboarding_analytics_patch.py: the state
    writer and email reader are monkeypatched, and the analytics fallback is
    redirected to a tmp JSONL so an emitted event is observable.
    """
    from fastapi.testclient import TestClient

    team = {"org_id": "test-team-3821", "tier": "free", "key_id": "k1"}
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics.jsonl"))
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ha, "_update_onboarding_state",
                        lambda org_id, **fields:
                        dict(ha.DEFAULT_ONBOARDING_STATE))
    monkeypatch.setattr(ha, "_org_email", lambda org_id: None)
    ha.app.dependency_overrides[ha.get_current_org] = lambda: team
    with TestClient(ha.app) as c:
        c._jsonl = tmp_path / "analytics.jsonl"
        yield c
    ha.app.dependency_overrides.clear()


def _emit(tmp_path, monkeypatch, event_name, props):
    """Emit through the REAL writer with Supabase unset → JSONL fallback."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    fallback = tmp_path / "analytics.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))
    outcome = ha._track_analytics_event("team-1", event_name, props)
    rows = []
    if fallback.exists():
        rows = [json.loads(line) for line in fallback.read_text().splitlines()
                if line.strip()]
    return outcome, rows


def _warnings(caplog):
    return [r for r in caplog.records
            if "unregistered telemetry" in r.getMessage()]


# ── the choke point (mechanism) ─────────────────────────────────────────────

def test_unregistered_analytics_prop_is_counted_not_dropped(
        tmp_path, monkeypatch, caplog):
    """The row is still written, the field is absent, the drop is COUNTED and
    reported once — acceptance is on the value the row receives and the
    counter that moves, never on a spelling in a file."""
    with caplog.at_level(logging.WARNING):
        _outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost",
                               {"cost_usd": 0.5, "aha": True})
    assert rows, "analytics must never block the capture — the row still lands"
    rec = rows[-1]
    assert "aha" not in rec["properties"]           # never forwarded
    assert rec["properties"]["cost_usd"] == 0.5     # registered field survives
    assert ha._TELEMETRY_DROP_COUNTS[
        ("analytics_props", "capture_cost", ("aha",))] == 1
    warns = _warnings(caplog)
    assert len(warns) == 1
    assert "aha" in warns[0].getMessage()


def test_unregistered_prop_is_never_forwarded_pii_guarantee(
        tmp_path, monkeypatch):
    """The anti-regression for the PII guarantee: reporting must not start
    forwarding unknown keys."""
    _outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost",
                           {"cost_usd": 0.1, "secret_key": "leak"})
    assert rows
    assert set(rows[-1]["properties"]) <= ha._ALLOWED_ANALYTICS_PROPS
    assert "secret_key" not in rows[-1]["properties"]


def test_prop_drop_counter_is_zero_for_a_fully_registered_event(
        tmp_path, monkeypatch):
    """The legitimate form: a registered event leaves the drop counter at 0."""
    _outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost",
                           {"cost_usd": 0.1, "session_id": "s1"})
    assert rows
    assert not any(k[0] == "analytics_props" for k in ha._TELEMETRY_DROP_COUNTS)


def test_drop_report_is_deduplicated(tmp_path, monkeypatch, caplog):
    """OTel's "at most once per record" bound: three identical drops produce
    ONE warning and a counter of 3."""
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            _emit(tmp_path, monkeypatch, "capture_cost", {"aha": True})
    assert len(_warnings(caplog)) == 1
    assert ha._TELEMETRY_DROP_COUNTS[
        ("analytics_props", "capture_cost", ("aha",))] == 3


def test_drop_report_does_not_recurse(tmp_path, monkeypatch):
    """The reporter reports via log + counter only — it never emits a row, so
    it cannot eat its own report."""
    _outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost", {"aha": True})
    assert [r["event_name"] for r in rows] == ["capture_cost"]
    assert {k[0] for k in ha._TELEMETRY_DROP_COUNTS} == {"analytics_props"}


def test_strict_mode_does_not_break_the_capture_path(tmp_path, monkeypatch):
    """Flag unset → the drop is reported and the call returns normally (the
    capture path cannot 500). Flag set → the SAME call raises, and the
    exception punches through `_track_onboarding_event`'s bare except."""
    outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost", {"aha": True})
    assert rows
    assert outcome in ha._ANALYTICS_OUTCOMES

    monkeypatch.setenv(ha._TELEMETRY_STRICT_ENV, "1")
    with pytest.raises(ha.UnregisteredTelemetryKey):
        ha._track_analytics_event("team-1", "capture_cost", {"aha": True})
    # #3498: _track_onboarding_event is async (its emit is offloaded off the
    # event loop), so the strict-mode exception is observed by awaiting it.
    with pytest.raises(ha.UnregisteredTelemetryKey):
        asyncio.run(ha._track_onboarding_event({"org_id": "team-1"}, "cap", aha=True))


def test_non_dict_properties_is_tolerated(tmp_path, monkeypatch):
    """A non-dict properties value must not make the reporter raise or count
    (it is normalized to None before the drop is computed)."""
    _outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost", ["aha"])
    assert rows
    assert not any(k[0] == "analytics_props" for k in ha._TELEMETRY_DROP_COUNTS)


def test_concurrent_drops_count_exactly_and_warn_once(monkeypatch, caplog):
    """The lock makes 'always counted' and 'reported at most once' hold under
    the threaded emit sites. The counter is widened so a lost lock actually
    loses increments (a plain Counter's read-modify-write is too narrow to
    interleave reliably)."""
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor

    class _SlowCounter(Counter):
        def __setitem__(self, key, value):
            time.sleep(0.0005)
            super().__setitem__(key, value)

    monkeypatch.setattr(ha, "_TELEMETRY_DROP_COUNTS", _SlowCounter())
    n = 16
    with caplog.at_level(logging.WARNING), \
            ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda _: ha._report_unregistered(
                "concurrency", "same", {"k"}),
            range(n)))
    assert ha._TELEMETRY_DROP_COUNTS[
        ("concurrency", "same", ("k",))] == n
    assert len(_warnings(caplog)) == 1


def test_drop_state_is_bounded_for_distinct_client_keys():
    """An authenticated client sending unique unknown field names cannot grow
    the process-global drop state without bound (the PATCH front door keys it
    on request-body fields)."""
    for i in range(ha._TELEMETRY_DROP_MAX_MARKERS + 25):
        ha._report_unregistered("bounded", "distinct", {f"k{i}"})
    assert len(ha._TELEMETRY_DROP_COUNTS) <= ha._TELEMETRY_DROP_MAX_MARKERS + 1
    per_site = ha._TELEMETRY_DROP_REPORTED[("bounded", "distinct")]
    assert len(per_site) <= ha._TELEMETRY_DROP_MAX_PER_SITE


def test_drop_fingerprint_bounds_a_single_key_name_length(caplog):
    """The COUNT cap alone is not enough: the PATCH front door keys the drop on
    request-body FIELD NAMES, so one field name of unbounded length would
    otherwise become one unbounded retained fingerprint entry AND log line."""
    huge = "A" * 200_000
    with caplog.at_level(logging.WARNING):
        ha._report_unregistered("bounded_len", "subject", {huge})
    counter_key = next(k for k in ha._TELEMETRY_DROP_COUNTS
                       if k[0] == "bounded_len")
    (rendered,) = counter_key[2]
    assert len(rendered) <= ha._TELEMETRY_DROP_MAX_KEY_LEN + 32
    assert rendered.startswith("A")
    # The warning carries that same bounded fingerprint, so the log line is
    # bounded by the same cap.
    assert all(len(rec.getMessage()) < 10_000 for rec in _warnings(caplog))
    # The cap must not perturb a normal short key — existing assertions and the
    # billing plan/tier fingerprint rely on the un-truncated form.
    assert ha._telemetry_drop_fingerprint({"plan", "tier"}) == ("plan", "tier")


def test_drop_site_label_is_bounded_too(caplog):
    """The site label (``where``/``subject``) is a code literal at every current
    call site, but the boundedness contract must not DEPEND on that: a future
    request-derived label must not grow the counter, the per-site dict, or the
    log line without bound."""
    huge = "W" * 200_000
    with caplog.at_level(logging.WARNING):
        ha._report_unregistered(huge, huge, {"k"})
    counter_key = next(k for k in ha._TELEMETRY_DROP_COUNTS if "k" in k[2])
    assert len(counter_key[0]) <= ha._TELEMETRY_DROP_MAX_KEY_LEN + 32
    assert len(counter_key[1]) <= ha._TELEMETRY_DROP_MAX_KEY_LEN + 32
    assert all(len(rec.getMessage()) < 10_000 for rec in _warnings(caplog))


def test_drop_state_is_bounded_across_distinct_sites(caplog):
    """The reported dict is bounded in its SITE dimension too — a future emit
    site misusing a caller-derived `subject` cannot grow it without bound — and
    the bound must not be bought by silencing a folded site."""
    for i in range(ha._TELEMETRY_DROP_MAX_SITES + 10):
        ha._report_unregistered(f"site{i}", "subject", {f"k{i}"})
    assert len(ha._TELEMETRY_DROP_REPORTED) <= ha._TELEMETRY_DROP_MAX_SITES + 1
    total = sum(len(v) for v in ha._TELEMETRY_DROP_REPORTED.values())
    assert total <= ((ha._TELEMETRY_DROP_MAX_SITES + 1)
                     * ha._TELEMETRY_DROP_MAX_PER_SITE)
    # The core #3821 guarantee still holds past the cap: a post-cap site that
    # folds into the overflow sentinel is still COUNTED and still WARNS.
    assert ha._TELEMETRY_DROP_SITE_OVERFLOW in ha._TELEMETRY_DROP_REPORTED
    with caplog.at_level(logging.WARNING):
        ha._report_unregistered("post_cap_site", "subject", {"fresh_key"})
    assert ha._TELEMETRY_DROP_COUNTS[
        ("post_cap_site", "subject", ("fresh_key",))] == 1
    assert any("fresh_key" in rec.getMessage() for rec in _warnings(caplog))


def test_one_site_cannot_silence_another_sites_warning(caplog):
    """A client flooding the PATCH front door with distinct unknown field
    names must not exhaust the warning budget for unrelated sites. With a
    single global dedup set this REDs: after the global cap is reached no
    other site ever warns again."""
    for i in range(ha._TELEMETRY_DROP_MAX_MARKERS + 10):
        ha._report_unregistered(
            "onboarding_state_patch", "unknown_field", {f"bad{i}"})
    with caplog.at_level(logging.WARNING):
        ha._report_unregistered("analytics_props", "capture_cost", {"aha"})
    assert any("aha" in rec.getMessage() for rec in _warnings(caplog))


# ── S1: the analytics prop filter / the shipped billing loss ────────────────

def test_billing_emit_carries_plan_and_tier(tmp_path, monkeypatch):
    """The billing emit passes `plan`/`tier`; they must survive the filter.
    Before #3821 they were dropped — the live loss since 2026-08-09."""
    _outcome, rows = _emit(tmp_path, monkeypatch, "invoice_paid",
                           {"plan": "pro", "tier": "pro", "status": "active"})
    assert rows
    assert rows[-1]["properties"] == {
        "plan": "pro", "tier": "pro", "status": "active"}
    assert not any(k[0] == "analytics_props" for k in ha._TELEMETRY_DROP_COUNTS)


def test_capture_cost_row_is_complete_when_all_keys_are_registered(
        tmp_path, monkeypatch):
    """#3359's requirement, enforced: every measured capture_cost field
    survives the PII filter (or the measurement is silently lost)."""
    meta = {"stats": {"llm": {
        "calls": 1, "retries": 0, "prompt_tokens": 10,
        "completion_tokens": 2, "cost_usd": 0.001,
        "calls_without_cost": 0, "calls_without_usage": 0,
        "deadline_aborts": 0, "by_stage": {}}}}
    props = ha._capture_cost_props("sess-1", meta)
    assert props is not None
    assert set(props) <= ha._ALLOWED_ANALYTICS_PROPS
    _outcome, rows = _emit(tmp_path, monkeypatch, "capture_cost", props)
    assert rows
    assert not any(k[0] == "analytics_props" for k in ha._TELEMETRY_DROP_COUNTS)


# ── S2/S5: the onboarding-state router and the writer backstop ──────────────

def test_unregistered_state_key_is_reported_at_the_router(state_seams):
    """A registered key persists; an unregistered one is reported (counter +
    WARN naming it) and is NOT persisted."""
    ha._update_onboarding_state("org-1", prompt_pasted=True, bogus_key=1)
    assert state_seams["state"].get("prompt_pasted") is True
    assert "bogus_key" not in state_seams["state"]
    assert ha._TELEMETRY_DROP_COUNTS[
        ("onboarding_state", "unknown_key", ("bogus_key",))] == 1


def test_registered_state_key_writes_through_with_zero_skips(state_seams):
    """The legitimate form — a registered key writes through, no skip counted."""
    ha._update_onboarding_state("org-1", prompt_pasted=True)
    assert state_seams["state"].get("prompt_pasted") is True
    assert not ha._TELEMETRY_DROP_COUNTS


def test_flow_scalar_at_router_is_reported_as_flow_rejection_not_unknown(
        state_seams):
    """A scalar FLOW key is reported with its OWN reason, distinct from a
    typo'd operational key — and still never reaches jsonb."""
    ha._update_onboarding_state("org-1", fork="self")
    assert ("onboarding_state", "flow_scalar_rejected_at_router", ("fork",)) \
        in ha._TELEMETRY_DROP_COUNTS
    assert ("onboarding_state", "unknown_key", ("fork",)) \
        not in ha._TELEMETRY_DROP_COUNTS
    assert "fork" not in state_seams.get("state", {})


def test_write_onboarding_state_flow_strip_is_reported(monkeypatch, caplog):
    """The belt-and-braces FLOW strip still strips (jsonb NEVER holds FLOW
    state) AND now reports the strip — its last chance to be noticed."""
    import tortoise.supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
    persisted: dict = {}

    class _FakeReg:
        def query(self, *args, **kwargs):
            persisted.update(kwargs.get("params") or {})
            return self

    class _FakeSdk:
        def _get_registry(self):
            return _FakeReg()

    monkeypatch.setattr(ha, "_make_sdk", lambda namespace=None: _FakeSdk())
    with caplog.at_level(logging.WARNING):
        ha._write_onboarding_state("org-1",
                                   {"prompt_pasted": True, "fork": "self"})
    # The strip itself is unchanged: FLOW keys never reach the persisted jsonb.
    persisted_state = json.loads(persisted["state"])
    assert "fork" not in persisted_state
    assert persisted_state.get("prompt_pasted") is True
    assert ha._TELEMETRY_DROP_COUNTS[
        ("onboarding_state", "flow_keys_stripped_at_write", ("fork",))] == 1
    assert any("fork" in r.getMessage() for r in caplog.records)


# ── S3/S4: the PATCH front door and the beacon enum skip ────────────────────

def test_unknown_onboarding_patch_field_is_reported_not_refused(patch_client):
    """The front door drops unknown PATCH fields (pydantic extra='ignore')
    before the router runs. It must stay a 200 — no unconditional refusal —
    but the drop must be counted."""
    r = patch_client.patch("/v1/onboarding/state", json={"bogus_key": 1})
    assert r.status_code == 200
    assert ("onboarding_state_patch", "unknown_field", ("bogus_key",)) \
        in ha._TELEMETRY_DROP_COUNTS


def test_strict_unknown_patch_field_is_a_validation_error(
        patch_client, monkeypatch):
    """Strict mode on the front door surfaces as a 422 — the ValueError
    subclass is load-bearing (not an opaque 500)."""
    monkeypatch.setenv(ha._TELEMETRY_STRICT_ENV, "1")
    r = patch_client.patch("/v1/onboarding/state", json={"bogus_key": 1})
    assert r.status_code == 422


def test_artifact_copied_invalid_enum_is_reported(patch_client, caplog):
    """An enum-invalid beacon still emits no event and returns 200, but the
    rejected value is now reported instead of vanishing."""
    with caplog.at_level(logging.WARNING):
        r = patch_client.patch("/v1/onboarding/state",
                               json={"harness": "vim", "section": "config"})
    assert r.status_code == 200
    assert ("artifact_copied", "invalid_enum", ("harness",)) \
        in ha._TELEMETRY_DROP_COUNTS
    assert any("harness" in rec.getMessage() for rec in caplog.records)
    # The event still does NOT fire — no artifact_copied row was written.
    assert not patch_client._jsonl.exists()


def test_strict_invalid_beacon_raises_loudly(patch_client, monkeypatch):
    """Strict mode is a dev/test opt-in; an enum-invalid beacon raises from the
    endpoint body (→ 500 in production wiring) rather than being swallowed.
    This documents the one strict-mode exception the beacon comment scopes."""
    monkeypatch.setenv(ha._TELEMETRY_STRICT_ENV, "1")
    with pytest.raises(ha.UnregisteredTelemetryKey):
        patch_client.patch("/v1/onboarding/state",
                           json={"harness": "vim", "section": "config"})


# ── S6 + the structural gate: every emitted prop key must be registered ─────

class _Call:
    __slots__ = ("event", "func", "keys", "lineno", "path")

    def __init__(self, path, lineno, func, event, keys):
        self.path = path
        self.lineno = lineno
        self.func = func
        self.event = event
        self.keys = keys

    def __repr__(self):  # pragma: no cover - diagnostics only
        return (f"_Call({self.path.name}:{self.lineno} {self.func} "
                f"event={self.event!r} keys={self.keys!r})")


def _literal_dict_keys(node):
    if not isinstance(node, ast.Dict):
        return None
    keys = set()
    for key in node.keys:
        if key is None or not isinstance(key, ast.Constant) \
                or not isinstance(key.value, str):
            return None
        keys.add(key.value)
    return keys


def _resolve_keys(node, scopes):
    """Prop keys for a call argument, or None if unresolvable.

    Handles a dict literal, a bare name resolved to its unique literal dict
    assignment in an enclosing scope (needed for mcp_server.py, which passes
    `props`), and `props or None` (the wrapper's passthrough).
    """
    if node is None:
        return None
    if isinstance(node, ast.Dict):
        return _literal_dict_keys(node)
    if isinstance(node, ast.Name):
        for scope in reversed(scopes):
            if node.id in scope:
                return scope[node.id]
        return None
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            keys = _resolve_keys(value, scopes)
            if keys is not None:
                return keys
        return None
    return None


def _walk_without_nested(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_without_nested(child)


def _callee_name(func_node):
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def _function_return_dicts(tree):
    """{func_name: keys} for functions whose top-level return is a dict literal.

    Needed for the capture_cost emit site, which passes the result of
    ``_capture_cost_props(...)`` — a CALL, not a literal — so the walk must
    resolve the helper's own field set instead of silently skipping it.
    """
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
                keys = _literal_dict_keys(stmt.value)
                if keys is not None:
                    out[node.name] = keys
    return out


def _dict_assignments(func_node, helper_returns):
    out: dict[str, set[str]] = {}
    for stmt in _walk_without_nested(func_node):
        target = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) \
                and isinstance(stmt.target, ast.Name) \
                and stmt.value is not None:
            target, value = stmt.target.id, stmt.value
        if target is None or value is None:
            continue
        keys = _literal_dict_keys(value)
        if keys is None and isinstance(value, ast.Call):
            keys = helper_returns.get(_callee_name(value.func))
        if keys is not None:
            out[target] = keys
    return out


class _Collector(ast.NodeVisitor):
    def __init__(self, path, helper_returns):
        self.path = path
        self.helper_returns = helper_returns
        self.calls: list[_Call] = []
        self.scopes: list[dict[str, set[str]]] = []
        self.func_stack: list[str] = []
        self.func_nodes: list[ast.AST] = []

    def visit_FunctionDef(self, node):
        self.scopes.append(_dict_assignments(node, self.helper_returns))
        self.func_stack.append(node.name)
        self.func_nodes.append(node)
        self.generic_visit(node)
        self.func_nodes.pop()
        self.func_stack.pop()
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _record(self, node, func, event_arg, props_arg):
        event = None
        if isinstance(event_arg, ast.Constant) \
                and isinstance(event_arg.value, str):
            event = event_arg.value
        self.calls.append(_Call(self.path, node.lineno, func, event,
                                _resolve_keys(props_arg, self.scopes)))

    #: The entry point's parameters, in signature order.
    _ENTRY_POINT_PARAMS = ("org_id", "event_name", "properties")

    def _is_entry_point_passthrough(self, node) -> bool:
        """True only for the UNTOUCHED forward inside ``_emit_analytics_off_loop``.

        Two conditions, both required (#4015 review):

        1. The call sits in that function and passes the caller's
           ``org_id``/``event_name``/``properties`` through **by name** —
           positionally or by keyword, with no extra positional argument, no
           keyword outside those three, and no ``**`` expansion. Anything else
           (a literal dict, a call, an expression, a sneaked-in extra keyword)
           is not the pass-through and gets RECORDED, where the count pin
           catches it.
        2. The helper does nothing else with those names — see
           ``_entry_point_forwards_parameters_untouched``. Without it, a
           one-line injection such as
           ``properties = {**(properties or {}), "unregistered_key": 1}`` or
           ``properties.update({"unregistered_key": 1})`` keeps the call
           looking verbatim while the injected key reaches the sink from every
           routed site.
        """
        if not self.func_stack \
                or self.func_stack[-1] != "_emit_analytics_off_loop":
            return False
        if not self._entry_point_forwards_parameters_untouched():
            return False
        params = self._ENTRY_POINT_PARAMS
        if len(node.args) > len(params):
            return False
        values: dict[str, ast.expr] = {}
        for i, arg in enumerate(node.args):
            values[params[i]] = arg
        for kw in node.keywords:
            if kw.arg is None or kw.arg not in params:
                return False
            values[kw.arg] = kw.value
        if set(values) != set(params):
            return False
        return all(
            isinstance(values[p], ast.Name) and values[p].id == p
            for p in params)

    def _entry_point_forwards_parameters_untouched(self) -> bool:
        """True only when the helper does nothing with its own parameters.

        Reference counting is deliberately strict: ``org_id``/``event_name``/
        ``properties`` must appear EXACTLY ONCE each in the helper body — the
        forward itself. That single rule closes every write/mutation shape at
        once instead of enumerating them:

        * a re-assignment (``properties = {...}``, ``properties, _ = ...``),
        * an in-place mutation (``properties.update(...)``),
        * a subscript target (``properties["k"] = 1``),
        * a ``for``/``with``/``except``/comprehension target,
        * any additional use at all (a log line, a second call),

        each adds a reference and turns the skip OFF, so the call is RECORDED
        and the count pin fails on it. An enumeration of assignment TARGETS (the
        previous shape) let an in-place mutation through, because
        ``properties.update(...)`` is a plain expression statement whose target
        is a ``Subscript``/``Call``, not a ``Name``.
        """
        names = frozenset(self._ENTRY_POINT_PARAMS)
        seen: dict[str, int] = {}
        for stmt in ast.walk(self.func_nodes[-1]):
            if isinstance(stmt, ast.Name) and stmt.id in names:
                seen[stmt.id] = seen.get(stmt.id, 0) + 1
        return all(seen.get(param) == 1 for param in names)

    def visit_Call(self, node):
        callee = _callee_name(node.func)
        # ``_emit_analytics_off_loop`` (#4015) is the off-loop entry point whose
        # signature mirrors ``_track_analytics_event``'s first three positional
        # parameters, so the SAME resolver walks its emit sites — otherwise
        # rerouting a site through the seam would silence this allowlist gate
        # for that site's props (the review the count pin exists to force).
        if callee == "_track_analytics_event" \
                and self._is_entry_point_passthrough(node):
            # The entry point's own pass-through call (#4015): it forwards the
            # callers' ``properties`` VERBATIM, so the calls TO it (recorded
            # below) carry the real props — counting this one too would
            # double-count and leave the inventory with a phantom unresolved
            # site. Skip it; count the call sites.
            #
            # The skip is deliberately narrow (see
            # ``_is_entry_point_passthrough``): it fires only for the UNTOUCHED
            # forward. Any other call written into the helper — an injected
            # dict passed AS the argument, or a rebound ``properties`` — is
            # RECORDED, so the count pin below fails on it.
            self.generic_visit(node)
            return
        if callee in ("_track_analytics_event", "_emit_analytics_off_loop"):
            event_arg = node.args[1] if len(node.args) >= 2 else None
            props_arg = node.args[2] if len(node.args) >= 3 else None
            for kw in node.keywords:
                if kw.arg == "properties":
                    props_arg = kw.value
                elif kw.arg == "event_name":
                    event_arg = kw.value
            self._record(node, callee, event_arg, props_arg)
        elif callee == "_track_onboarding_event":
            keys = set()
            resolved = True
            for kw in node.keywords:
                if kw.arg is None:
                    resolved = False
                else:
                    keys.add(kw.arg)
            self.calls.append(_Call(self.path, node.lineno, callee, None,
                                    keys if resolved else None))
        elif node.args and _callee_name(node.args[0]) == "_track_analytics_event":
            # Partial application: `asyncio.to_thread(_track_analytics_event,
            # org, event, props)` — the capture_cost emit site.
            event_arg = node.args[2] if len(node.args) >= 3 else None
            props_arg = node.args[3] if len(node.args) >= 4 else None
            self._record(node, "_track_analytics_event(to_thread)",
                         event_arg, props_arg)
        self.generic_visit(node)


def _collect(path):
    tree = ast.parse(path.read_text())
    collector = _Collector(path, _function_return_dicts(tree))
    collector.visit(tree)
    return collector.calls


def test_every_emitted_prop_key_is_allowlisted():
    """The structural fix — the #3675 guard.

    Walks the AST of both emitter modules, resolves each emit site's prop
    keys (dict literal, or a name bound to a literal dict in an enclosing
    scope), and asserts they are a subset of ``_ALLOWED_ANALYTICS_PROPS``.

    This test REDs on unmodified ``origin/main``: the billing webhook emits
    ``plan`` and ``tier``, neither registered. It is the gate that would have
    caught the live Aug-2026 loss at CI time, and that catches #3675's new
    props before they ship.
    """
    calls = _collect(_HOSTED_API) + _collect(_MCP_SERVER)
    # Pin the TOTAL emit-site count: a NEW site whose props expression cannot
    # be resolved would otherwise be filtered out by `if c.keys` and pass the
    # subset check. Any addition must update this inventory (and register its
    # props), which is exactly the review the gate exists to force.
    # #4015: routing the five analytics sites through ``_emit_analytics_off_loop``
    # does NOT change the count — the collector resolves that helper's args
    # exactly like the direct calls it replaced. main's #3773 added a sixth
    # emitter, hence 12 here (11 before it).
    assert len(calls) == 12, (
        f"emit-site inventory changed — {len(calls)} calls found: {calls}")
    resolved = [c for c in calls if c.keys]
    assert len(resolved) >= 10, (
        "the AST walk resolved suspiciously few emit sites — the walk is "
        f"broken, not the code: {calls}")

    all_keys = set().union(*(c.keys for c in resolved))
    # The billing site's event name is a variable, so pin the walk to it by
    # its keys: if `plan`/`tier` are not inspected, the walk has a hole.
    assert {"plan", "tier"} <= all_keys
    # Pin the two sites whose props are NOT a bare literal dict, so a future
    # refactor that makes either unresolvable FAILS here instead of silently
    # dropping it from the guard.
    assert any(c.func == "_track_analytics_event(to_thread)" and c.keys
               for c in calls), "capture_cost emit site did not resolve"
    assert any(c.path.name == "mcp_server.py" and c.keys
               for c in calls), "mcp_tool_call emit site did not resolve"

    violations = {}
    for call in resolved:
        bad = call.keys - ha._ALLOWED_ANALYTICS_PROPS
        if bad:
            violations[f"{call.path.name}:{call.lineno} {call.func}"] = sorted(bad)
    assert violations == {}, (
        "emit site(s) pass prop keys missing from _ALLOWED_ANALYTICS_PROPS — "
        f"they would be dropped silently: {violations}")
