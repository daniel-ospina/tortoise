"""``metering.get_capture_usage`` — the READER the extractor dimension lacked (#4051).

⚠️ The reader is ONE function over TWO landed contracts. #4051 landed it first (columns +
``capture_cost_usd``, zero-view degradation, never raises); the ``#9567`` D11/D12 branch defined a
SECOND ``def get_capture_usage`` at the same insertion point (``measured`` /
``unmeasured_reason``). Python does not error on a duplicate ``def`` — the last one wins silently —
so the merge reconciled them into one body: every key #4051's landed tests assert, plus the presence
fact the allowance gate cannot work without. Both halves are pinned below.

WHY THIS FILE EXISTS. ``record_capture_usage`` has written ``capture_tokens_in`` /
``capture_tokens_out`` since #5045, and the owner ruling behind them (2026-09-26) is that the
customer-visible extraction allowance is denominated in **tokens**. The columns had no reader:
``tortoise/cost_dimensions.py`` declared ``get_current_usage`` as the
``capture_extraction`` READER, and that function returns write-op fields only — so "tokens per
team per period" was written and unreadable from code, the ``#4493`` failure mode where a
recorded dimension with no reader reads as a measurement that does not exist.

These tests are deliberately DB-free: the reader is a pure mapping over one ledger row, so the
window and the registry are stubbed and the assertions are about the CONTRACT (keys, zeros,
degradation), not about FalkorDB.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tortoise import cost_dimensions, metering
from tortoise.cost_dimensions import READER, resolve

_KEYS = ("capture_calls", "capture_tokens_in", "capture_tokens_out",
         "capture_cost_usd")


def _period():
    return SimpleNamespace(label="2026-11", start_iso="2026-11-01T00:00:00+00:00",
                           end_iso="2026-12-01T00:00:00+00:00")


def _fake_sdk(result_set):
    """A registry whose Cypher read returns *result_set*."""
    reg = SimpleNamespace(query=lambda *a, **k: SimpleNamespace(result_set=result_set))
    return SimpleNamespace(_get_registry=lambda: reg)


# ── the declaration is now TRUE ──────────────────────────────────────────────


def test_the_dimension_declares_this_function_as_its_reader():
    row = cost_dimensions.by_name("capture_extraction")
    readers = [b for b in row.bindings_of(READER)]
    assert readers, "capture_extraction declares no reader at all"
    names = {b.attr for b in readers}
    assert "get_capture_usage" in names, (
        f"capture_extraction must declare get_capture_usage as its reader, got {names}"
    )
    # and every declared binding must actually resolve on the live module
    for b in readers:
        assert resolve(b) is not None


def test_the_old_binding_could_not_have_been_a_reader():
    """The former binding named a function that returns write-op fields only.

    Pinned so a future edit cannot re-point the reader at it silently: if
    ``get_current_usage`` ever grows the capture keys this assertion is the
    thing that should be revisited deliberately, not by drift.
    """
    import inspect

    source = inspect.getsource(metering.get_current_usage)
    for key in _KEYS:
        assert key not in source, (
            f"get_current_usage now mentions {key!r} — re-check which function is the "
            "capture reader before re-pointing the binding"
        )


# The modules that DEFINE the reader, plus ``cost_dimensions.py`` — which names it
# only as a declared ``Binding(..., READER)`` (a string, never a call).
_ALLOWED = {"metering.py", "supabase_control.py", "cost_dimensions.py"}

# ⚠️ §1b SUPERSEDED (#9567 D11/D12): the reader is no longer measurement-only. An
# allowance cannot be enforced against a reader nothing may call, so the allowance
# gate is now its ONE consumer — and that is asserted EXACTLY (below), not exempted
# by name. An exemption for ``allowance_gate.py`` would let a SECOND call site (a
# report, a dashboard, a tool) appear without reddening this pin; an exact equality
# also reds if the gate stops reading it, which would be a fail-OPEN regression.
_ENFORCEMENT_CONSUMERS = {"allowance_gate.py"}


def _scan_capture_call_sites(root):
    """Return ``(offending relative paths, every relative path actually read)``.

    A helper rather than an inline loop so the scan itself can be exercised
    against a fixture tree: a scan that is only ever run against the real tree
    is green whether or not it can still detect anything.
    """
    offenders, scanned = [], []
    for path in sorted(root.rglob("*.py")):
        if path.name in _ALLOWED:
            continue
        rel = str(path.relative_to(root))
        scanned.append(rel)
        if "get_capture_usage" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(rel)
    return offenders, scanned


def test_the_gate_is_the_readers_only_consumer():
    """§1b's scope is NARROWED, not dropped: the gate, and nothing else (#9567).

    ``#4051`` §1b recorded this reader as measurement-only, with zero call sites.
    The ``#9567`` change arms the ruled token dimension and has no other meter to
    read, so the gate became a consumer — a scope change recorded in
    ``docs/architecture/CONSUMPTION-MODEL.md`` §6 (with the reopen on ``#4051``),
    not a silent one. The pin is kept, and made STRICTER than an exemption: the
    offender list must be EXACTLY ``allowance_gate.py``. A blanket exemption for
    that module would stay green when a report, dashboard or tool added a second
    call site; an exact equality also reds if the gate stops reading the meter,
    which is the fail-OPEN direction (a configured dimension with no reading).

    Scans EVERY module under ``tortoise/`` except the modules that DEFINE the
    reader. A call site in ``hosted_api`` would put it on another request path;
    one in ``tool_registry`` / ``sdk`` / ``hosted_mcp`` would add surface. All
    three are the #4051 exclusions, so the pin covers the whole tree rather than
    the one obvious file (a review finding: a one-file pin stays green while the
    reader is wired in elsewhere).

    The FLOOR below is load-bearing, not decoration. ``rglob`` over a wrong
    root — or a moved package, or a rename — yields nothing, and then
    ``offenders == []`` is GREEN while the pin inspected no file at all: a
    guard that checks nothing reads exactly like a guard that found nothing.
    ``hosted_api.py`` is asserted BY NAME for the same reason: it is the one
    module whose call site would silently make this a request-path change, so
    the scan must be proven to have covered it.
    """
    from pathlib import Path

    root = Path(metering.__file__).parent
    offenders, scanned = _scan_capture_call_sites(root)
    assert len(scanned) > 100, (
        f"the scope scan read only {len(scanned)} module(s) under {root} — a scan "
        "that inspected nothing cannot certify that nothing calls the reader"
    )
    assert "hosted_api.py" in scanned, (
        "the scope scan did not read tortoise/hosted_api.py, the module whose call "
        f"site would put the reader on a request path; read: {scanned[:5]}…"
    )
    assert set(offenders) == _ENFORCEMENT_CONSUMERS, (
        "get_capture_usage must be read by the allowance gate and NOTHING else "
        f"(#9567 narrows #4051 §1b); it is referenced in {offenders}"
    )


def test_the_scope_scan_flags_a_wired_call_site(tmp_path):
    """Non-vacuity: the scan can still DETECT — a real call site reddens it.

    Run against a fixture tree rather than the repo, so the assertion is about
    the scanner's discriminating power and not about today's tree happening to
    be clean.
    """
    (tmp_path / "hosted_api.py").write_text(
        "def f(org):\n    return metering.get_capture_usage(org)\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("X = 1\n", encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert offenders == ["hosted_api.py"]
    assert scanned == ["clean.py", "hosted_api.py"]


def test_the_scope_scan_exempts_only_the_defining_modules(tmp_path):
    """The exemption set is exact: the defining modules are skipped, and ONLY them."""
    for name in _ALLOWED:
        (tmp_path / name).write_text(
            "def get_capture_usage(org):\n    return {}\n", encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert (offenders, scanned) == ([], [])


def test_an_unreadable_root_is_caught_by_the_floor_not_read_as_a_pass(tmp_path):
    """A wrong root produces ``[]`` offenders — which is why the floor exists.

    Pinned so the floor cannot be dropped as redundant: without it, this empty
    directory is indistinguishable from a clean tree (#3400/#4348 shape, applied
    to a scan instead of a manifest row).
    """
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert (offenders, scanned) == ([], [])
    assert not len(scanned) > 100, "the floor must reject a root that yielded no files"


# ── the supabase (production) substrate ─────────────────────────────────────


def test_supabase_read_maps_the_capture_columns():
    """The production ledger post-#669 must be covered, not just the registry.

    A review finding: every other test here forces ``_supabase_mode`` False, so a
    wrong column or filter in the control-plane read would return no row, be
    swallowed by the reader's degradation, and serve the ZERO view for every
    tenant on the production ledger — permanently, with a green suite. That is
    the silent-zero class the capture-tokens migration's own header warns about.
    """
    import tortoise.supabase_control as sc
    from tests.fake_control_plane import FakeControlPlane

    start = "2026-11-01T00:00:00+00:00"
    fake = FakeControlPlane()
    fake.tables.setdefault("metering_records", []).append({
        "org_id": "org_x",
        "period_start": start,
        "capture_calls": 4,
        "capture_tokens_in": 1400,
        "capture_tokens_out": 320,
        "capture_cost_usd": 0.0075,
    })
    got = sc.metering_get_capture_usage(fake, "org_x", start)
    assert got == {"measured": True, "unmeasured_reason": None,
                   "capture_calls": 4, "capture_tokens_in": 1400,
                   "capture_tokens_out": 320, "capture_cost_usd": 0.0075}


def test_supabase_read_is_zero_for_a_different_window_or_org():
    """The filters are load-bearing: the row is keyed on (org, period_start)."""
    import tortoise.supabase_control as sc
    from tests.fake_control_plane import FakeControlPlane

    start = "2026-11-01T00:00:00+00:00"
    fake = FakeControlPlane()
    fake.tables.setdefault("metering_records", []).append({
        "org_id": "org_x", "period_start": start, "capture_calls": 4,
        "capture_tokens_in": 1400, "capture_tokens_out": 320,
        "capture_cost_usd": 0.0075,
    })
    zeros = {"measured": False, "unmeasured_reason": "absent",
             "capture_calls": 0, "capture_tokens_in": 0,
             "capture_tokens_out": 0, "capture_cost_usd": 0.0}
    assert sc.metering_get_capture_usage(fake, "org_other", start) == zeros
    assert sc.metering_get_capture_usage(
        fake, "org_x", "2026-10-01T00:00:00+00:00") == zeros


def test_supabase_branch_passes_the_window_start_to_the_control_plane(monkeypatch):
    """The three-line branch that picks the production substrate must carry
    ``period.start_iso``, not the derived month label (#3825's bug class).

    Every other test here forces ``_supabase_mode`` False, so without this the
    branch is unexecuted: a later edit passing ``period.label`` would be caught
    by the ``except`` and every tenant on the production ledger would read the
    zero view with a green suite.
    """
    seen = {}

    def _reader(cp, org_id, period_start):
        seen["args"] = (org_id, period_start)
        return {"measured": True, "unmeasured_reason": None,
                "capture_calls": 7, "capture_tokens_in": 900,
                "capture_tokens_out": 100, "capture_cost_usd": 0.002}

    import tortoise.supabase_control as sc

    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "metering_get_capture_usage", _reader)

    view = metering.get_capture_usage("org_x")
    assert view["capture_calls"] == 7 and view["capture_tokens_in"] == 900
    assert seen["args"] == ("org_x", "2026-11-01T00:00:00+00:00"), (
        "the supabase branch must pass the WINDOW START, not the month label")


# ── the reader's contract ───────────────────────────────────────────────────


def test_no_org_is_the_zero_view_and_never_raises():
    view = metering.get_capture_usage("")
    assert all(view[k] == 0 for k in _KEYS)
    assert view["period_start"] is None and view["period_end"] is None


def test_unresolvable_window_degrades_to_zeros(monkeypatch):
    def _boom(_org_id):
        raise RuntimeError("anchor unavailable")

    monkeypatch.setattr(metering, "_current_period", _boom)
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)
    # #923/#3825: an unresolvable window renders the zero view with a
    # placeholder label — never a month KEY that would read as a real period.
    assert view["period_start"] is None
    # …and it is a FAULT, not an absence: flattening the two would let a broken
    # clock read as "nothing spent" and disarm the allowance (fail OPEN).
    assert view["measured"] is False
    assert view["unmeasured_reason"] == "period_error"


def test_registry_row_maps_to_the_capture_keys(monkeypatch):
    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk",
                        lambda: _fake_sdk([[3, 1234, 567, 0.0042]]))
    view = metering.get_capture_usage("org_x")
    assert view["capture_calls"] == 3
    assert view["capture_tokens_in"] == 1234
    assert view["capture_tokens_out"] == 567
    assert view["capture_cost_usd"] == pytest.approx(0.0042)
    # ⚠️ the PRESENCE half (#4051 + #9567 reconciled on one function): a row IS a
    # reading even when every counter in it is zero, and the gate may rely on it.
    assert view["measured"] is True
    assert view["unmeasured_reason"] is None
    assert view["capture_tokens_total"] == 1801
    assert view["period"] == "2026-11"
    assert view["period_start"] == "2026-11-01T00:00:00+00:00"


def test_no_row_is_zeros_not_an_error(monkeypatch):
    """The MERGE creates the row on the first write — an empty read is not a fault."""
    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk", lambda: _fake_sdk([]))
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)
    assert view["period"] == "2026-11"
    # "nobody has captured yet" — a named ABSENCE the gate treats as a zero for a
    # cumulative flow, never as a measurement.
    assert view["measured"] is False
    assert view["unmeasured_reason"] == "absent"


def test_null_columns_read_as_zero(monkeypatch):
    """A ledger row that never carried tokens must not raise or return None."""
    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk", lambda: _fake_sdk([[None, None, None, None]]))
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)


def test_a_failed_read_degrades_and_never_raises(monkeypatch):
    """A control-plane blip must never 500 a usage read (#923)."""

    def _boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk", _boom)
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)
    # the zero view is kept (#923) AND the fault is NAMED — the gate refuses on
    # this reason rather than reading a broken meter as zero usage.
    assert view["measured"] is False
    assert view["unmeasured_reason"] == "read_error"
