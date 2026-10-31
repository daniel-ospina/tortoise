"""#10047 — the lanes that build their OWN SDK install the record's tenant.

The failure these tests prevent, measured before the change: every hosted lane
that opens ``_make_sdk(namespace=org_id)`` for itself carried no record tenant,
so an ARMED deployment refused every row they emitted ``no_tenant`` — counted on
``monitoring.record_write_failures`` (a DECLINE channel), invisible in
``/health``, and healed by nothing on these lanes because the rows they write
(seeded Subjects, indexed Objects/Events, ``EntityLinked``) are the ones a
capture never replays.

The two lanes that DID install one resolve the request's own credential
(``hosted_api._data_sdk`` → ``_bind_record_tenant``, and the dream drain it
enqueues). These five cannot: the indexing jobs keep only ``org_id`` across a
detached ``asyncio`` task, and the seed runners take ``org_id`` as a parameter.
They all write the org's DEFAULT graph BY CONSTRUCTION, and since #10025 that
graph has a stable ``public.graphs`` id (``default_graph_id_for``) — so the
tenant is nameable without a guess.

``_LaneStopped`` is a ``BaseException`` on purpose: the fake SDK raises it from
``_bind_record_tenant`` the instant the tenant is installed, so a lane test
stops AT the install and never reaches a network walk, a quota read or a second
write. Every lane's own ``except Exception`` wide-net must not swallow it — if
one starts to, the test fails loudly instead of silently passing with no bind.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise import hosted_api as ha
from tortoise import supabase_control

#: Shaped like a real ``public.graphs.id`` (``g_<16hex>``) and deliberately NOT
#: derivable from the org — a name-derived or constant tenant fails the
#: equality assertion below, which is the point of seeding a row at all.
DEFAULT_ROW_ID = "g_1004700abcde1234"
ORG = "org-10047-lanes"


class _LaneStopped(BaseException):
    """The lane reached the install. Nothing after it may run in a test."""


class _FakeCP:
    """The slice of the control-plane client ``default_graph_id_for`` reads."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = [dict(r) for r in rows]

    def query(self, table, method="GET", filters=None, select=None,
              json_body=None, order=None):
        rows = self.rows
        for col, op, val in (filters or []):
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
        return [dict(r) for r in rows] if method == "GET" else None


class _RecordingSDK:
    """Records the tenant the chokepoint installs, then stops the lane."""

    def __init__(self, sink: list[tuple[str, str]]) -> None:
        self._sink = sink

    def _bind_record_tenant(self, org_id: str, graph_id: str) -> None:
        self._sink.append((org_id, graph_id))
        raise _LaneStopped

    def close(self) -> None:  # the `finally` of every runner
        return None

    def __getattr__(self, name):  # anything else: the lane must not continue
        raise _LaneStopped


class _QuietSDK(_RecordingSDK):
    """Same recorder, but an install does not stop anything (helper tests)."""

    def _bind_record_tenant(self, org_id: str, graph_id: str) -> None:
        self._sink.append((org_id, graph_id))


class _Res:
    # ClassVar, not a plain mutable default: this empty result set is a
    # deliberately SHARED "no rows" placeholder — every `_Res` instance is
    # identical and nothing ever mutates it — so the shared state RUF012 warns
    # about cannot occur. Annotating it says so instead of moving it to
    # `__init__`, which would imply per-instance state that does not exist.
    result_set: ClassVar[list] = []


class _FakeProj:
    """An empty onboarding graph for the seed runner's pre-write probes."""

    def query(self, cypher, **params):
        return _Res()


@pytest.fixture
def default_row(monkeypatch):
    """HOSTED mode, one ACTIVE ``kind='default'`` row for ORG — nothing stubbed
    between the helper and ``default_graph_id_for``."""
    cp = _FakeCP([{"id": DEFAULT_ROW_ID, "org_id": ORG,
                   "kind": "default", "status": "active"}])
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(supabase_control, "get_control_plane", lambda: cp)
    return cp


@pytest.fixture
def bound(default_row, monkeypatch):
    """Every ``_make_sdk`` in the lane under test returns a recording fake."""
    sink: list[tuple[str, str]] = []
    monkeypatch.setattr(ha, "_make_sdk",
                        lambda **kwargs: _RecordingSDK(sink))
    return sink


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── The chokepoint itself ──────────────────────────────────────────────────

def test_the_helper_reads_the_row_and_installs_it_on_the_sdk(default_row):
    """The id is READ from the default row and handed to the one carrier.

    Mutation this reddens on: deleting ``_bind_record_tenant(...)`` from
    ``_bind_default_graph_record_tenant`` (nothing is installed) or replacing
    the resolved id with ``"default"`` / ``f"org_{org_id}"`` (the fake records
    the wrong pair).
    """
    sink: list[tuple[str, str]] = []
    ha._bind_default_graph_record_tenant(_QuietSDK(sink), ORG)
    assert sink == [(ORG, DEFAULT_ROW_ID)]


def test_an_org_with_no_default_row_installs_nothing(monkeypatch):
    """FAIL-CLOSED (#9192): no row ⇒ no bind ⇒ the write stays refused.

    ``write_live_record`` then counts ``no_tenant`` and writes nothing — the
    refusal direction is pinned by
    ``test_10025_graph_identity.py::test_an_unnameable_tenant_still_refuses``
    and ``test_9078_record_written_by_production_path.py::test_an_armed_capture_
    without_a_tenant_is_refused_and_counted``. What this pins is the LANE half:
    a lane that cannot name a tenant must not look like it installed one.
    """
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(supabase_control, "get_control_plane",
                        lambda: _FakeCP([]))
    sink: list[tuple[str, str]] = []
    ha._bind_default_graph_record_tenant(_QuietSDK(sink), ORG)
    assert sink == [], "an unnamed tenant must never be bound (never a guess)"


def test_the_selfhost_lane_never_reaches_the_control_plane(monkeypatch):
    """The self-host deployment has no control plane: do not call one.

    The probe MUST raise something the helper's own ``except Exception`` cannot
    swallow (``tortoise/hosted_api.py`` wraps ``get_control_plane()`` in that
    block), or this test cannot fail: a plain ``AssertionError`` would be
    caught, logged and turned into the same ``return`` a working guard takes,
    so deleting the guard would leave ``sink == []`` and the test GREEN.
    ``pytest.fail`` raises ``Failed``, which derives from ``BaseException`` —
    the same device ``_LaneStopped`` uses above — so a regression escapes to the
    caller and reds. Verified by mutation: with
    ``if not is_supabase_enabled(): return`` deleted, this test fails.
    """
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)

    def _boom():
        pytest.fail("the self-host lane must not read the control plane for a "
                    "record tenant")

    monkeypatch.setattr(supabase_control, "get_control_plane", _boom)
    sink: list[tuple[str, str]] = []
    ha._bind_default_graph_record_tenant(_QuietSDK(sink), ORG)
    assert sink == []


# ── One test per lane ─────────────────────────────────────────────────────
# Each reddens when its `_bind_default_graph_record_tenant(...)` line is
# removed: the fake SDK is never asked to install, it never raises, and the
# lane either runs on into a network/quota path or returns — either way the
# `pytest.raises(_LaneStopped)` has nothing to catch.

def test_the_onboarding_seed_runner_binds_the_default_graph_tenant(
        bound, monkeypatch):
    """`POST /v1/onboarding/seed` → `_run_onboarding_seed`: Subjects + edges."""
    monkeypatch.setattr(ha, "_graph_available", lambda org_id: True)
    monkeypatch.setattr(ha, "_org_proj", lambda org_id: _FakeProj())
    with pytest.raises(_LaneStopped):
        ha._run_onboarding_seed(ORG, org_name="Acme Corp",
                                person_name="Ann Example")
    assert bound == [(ORG, DEFAULT_ROW_ID)]


def test_the_starter_seed_runner_binds_the_default_graph_tenant(
        bound, monkeypatch):
    """`POST /internal/starter-seed` → `_run_starter_seed` (provisioning)."""
    monkeypatch.setattr(ha, "_graph_available", lambda org_id: True)
    with pytest.raises(_LaneStopped):
        ha._run_starter_seed(ORG, org_name="Acme Corp",
                             person_name="Ann Example")
    assert bound == [(ORG, DEFAULT_ROW_ID)]


def test_the_github_index_walk_binds_the_default_graph_tenant(
        bound, monkeypatch):
    """`POST /v1/index/github` → the detached `_run_indexing` task."""
    monkeypatch.setattr(ha, "_github_token_enc", lambda org_id: "enc-token")
    monkeypatch.setattr("tortoise.crypto.decrypt_token",
                        lambda enc: "plain-token")
    monkeypatch.setattr(ha, "_get_onboarding_state", lambda org_id: {})
    with pytest.raises(_LaneStopped):
        _run(ha._run_indexing("job-10047", ORG, "acme", None))
    assert bound == [(ORG, DEFAULT_ROW_ID)]


def test_the_docs_index_walk_binds_the_default_graph_tenant(
        bound, monkeypatch):
    """`POST /v1/index/docs` → the detached `_run_docs_indexing` task."""
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", "/tmp/10047-ingest")
    monkeypatch.setattr(ha, "_github_token_enc", lambda org_id: "enc-token")
    monkeypatch.setattr("tortoise.crypto.decrypt_token",
                        lambda enc: "plain-token")
    with pytest.raises(_LaneStopped):
        _run(ha._run_docs_indexing("job-10047-docs", ORG, "acme", None))
    assert bound == [(ORG, DEFAULT_ROW_ID)]


def test_the_index_relink_pass_binds_the_default_graph_tenant(
        bound, monkeypatch):
    """The index-completion relink — the pass that emits `EntityLinked`."""
    with pytest.raises(_LaneStopped):
        ha._relink_sessions_after_index(ORG)
    assert bound == [(ORG, DEFAULT_ROW_ID)]


# ── The recurrence guard: the LANE INVENTORY is derived from the source ────
# Every test above pins ONE lane, by name. That is the right shape for a
# behaviour pin and the wrong shape for the hazard: #10087's five bindings
# closed the five lanes that existed, and the NEXT lane a feature PR adds —
# a new background job, a new organically-grown route — would carry no tenant
# and redden NOTHING here, because no test above knows about it. The record's
# refusal is a DECLINE channel (`monitoring.record_write_failures`, invisible
# in `/health`'s `ok`), so that lane would write its graph and lose its rows
# silently, exactly as these five did before #10047.
#
# So this leg does not enumerate lanes by hand — it ASKS THE SOURCE. It walks
# `tortoise/hosted_api.py`, finds every function that builds an ORG-scoped SDK
# (`_make_sdk(namespace=<not a literal>)` / `_make_sdk(graph_name=…)`, i.e.
# anything that is not the registry lane), and for each one that can reach a
# RECORD-EMITTING SDK verb it requires a tenant install on the same path.
#
# WHAT IT CATCHES: a new lane that opens an org SDK and writes through the
# SDK's own doors (`create_point`, `_apply_and_journal`, `index_directory`, …)
# — the shape of every lane this issue fixed, and of the plausible next one.
# WHAT IT DOES NOT: a lane that writes through ANOTHER module's writer
# (`GitHubIndexer.index_repo`, `session_link.link_entity`) — reachability
# stops at this module's own functions, because following it across modules is
# a guess about which call sites write. Those lanes are covered by the
# per-lane behaviour pins above, and adding a sixth that delegates is when a
# sixth test belongs here.

#: SDK methods that append a journal record, therefore write the record. Read
#: from `tortoise/sdk.py` — every method whose body calls `_emit_event` /
#: `_apply_and_journal` / `_journal_entity_event` — rather than invented, and
#: `index_directory` is named because it writes through `_apply_and_journal`
#: per file. A NEW emitting verb added to the SDK is a new way to write records
#: and belongs here.
_RECORD_EMITTING_VERBS = frozenset({
    "_apply_and_journal", "_emit_event", "_journal_entity_event",
    "annotate_operator", "approve_merge", "assess_source", "checkpoint",
    "compute_confidence", "create_direct_edge", "create_object",
    "create_operator", "create_point", "create_source", "create_subject",
    "create_event", "delete_point", "index_directory", "ingest_corpus",
    "invalidate_point", "mitigate_operator", "mine_corpus", "promote_point",
    "record_calibration", "retract_point", "set_point_baseline",
    "supersede_point", "update_point", "_write_session_and_turns",
    "_write_capture_turns",
})

#: The carriers that install a record tenant on the SDK a lane writes through.
_TENANT_INSTALLERS = frozenset({
    "_bind_record_tenant",                # the credential-resolved chokepoint
    "_bind_default_graph_record_tenant",  # the org-DEFAULT lanes (#10047)
    "_bind_dream_record_tenant",          # the dream drain's own SDK
    "_data_sdk", "_data_sdk_offloaded",   # they bind before returning
})


def _called_names(node) -> set[str]:
    """Every function/method NAME called anywhere inside `node`."""
    return {name for name in (
        getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        for call in ast.walk(node) if isinstance(call, ast.Call)) if name}


def _org_scoped_sdk_sites(tree) -> dict[str, list[int]]:
    """`{function: [line, …]}` for every ORG-scoped `_make_sdk` construction.

    A string-literal namespace is the registry lane (`namespace="registry"`,
    `namespace=None` — the probe/anchor surfaces), never an org's graph; every
    other spelling (`org_id`, `org["org_id"]`, `namespace`) is org-scoped, and
    `graph_name=` is by definition a tenant graph (registry graphs are opened
    by namespace).
    """
    sites: dict[str, list[int]] = {}
    for node in tree.body:  # module-level lanes; nested helpers ride their parent
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "id", None) == "_make_sdk"):
                continue
            kwargs = {k.arg: k.value for k in call.keywords}
            namespace = kwargs.get("namespace")
            org_scoped = namespace is not None and not (
                isinstance(namespace, ast.Constant)
                and isinstance(namespace.value, str))
            if org_scoped or "graph_name" in kwargs:
                sites.setdefault(node.name, []).append(call.lineno)
    return sites


def _reaches_a_record_writer(name: str, calls: dict[str, set[str]],
                             seen: frozenset[str] = frozenset()) -> bool:
    """Does `name` — or a module-local function it calls — emit a record?"""
    if name in seen:
        return False
    seen = seen | {name}
    own = calls.get(name, set())
    if own & _RECORD_EMITTING_VERBS:
        return True
    return any(_reaches_a_record_writer(callee, calls, seen)
               for callee in own if callee in calls)


def test_no_hosted_lane_reaches_a_record_writer_without_a_tenant():
    """A NEW org-scoped lane that writes records must install a tenant.

    This is the leg that makes the class non-recurring: it is derived from
    `tortoise/hosted_api.py` itself, so a lane added tomorrow is scanned
    without anyone remembering to add a test. It reddens when such a lane is
    added with no `_bind_default_graph_record_tenant` /
    `_bind_record_tenant` / `_data_sdk` install on its path, and when an
    install is REMOVED (the five lanes above also have behaviour pins).

    Non-vacuity is asserted first, because a scan that matched nothing would
    pass while proving nothing — the rule `tools/record_wiring_check.py`
    states for its own legs: a signal that cannot be read is not a pass.
    """
    tree = ast.parse(Path(ha.__file__).read_text())
    calls = {node.name: _called_names(node)
             for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    sites = _org_scoped_sdk_sites(tree)
    assert sites, ("the scan found NO org-scoped SDK construction — the AST "
                   "criteria no longer match how lanes open their SDK")

    emitting = {name: lines for name, lines in sites.items()
                if _reaches_a_record_writer(name, calls)}
    unbound = sorted((name, lines) for name, lines in emitting.items()
                     if not calls[name] & _TENANT_INSTALLERS)
    assert unbound == [], (
        "these hosted lanes build an org-scoped SDK and can reach a record "
        "writer with NO record tenant installed, so an ARMED deployment "
        "refuses every row they emit `no_tenant` (a DECLINE counter, not a "
        "failure — the lane looks healthy and files nothing). Install one at "
        "the construction site: `_bind_default_graph_record_tenant(sdk, "
        "org_id)` for a lane that cannot see a request credential (#10047), "
        "or open the SDK through `_data_sdk`/`_data_sdk_offloaded` when it "
        f"can: {unbound}")

    # The five lanes #10087 wired, as the SOURCE sees them. A behaviour pin
    # above already reds if one stops installing; this keeps the inventory
    # honest when a runtime pin is edited rather than the code.
    for lane in ("_run_onboarding_seed", "_run_starter_seed", "_run_indexing",
                 "_relink_sessions_after_index", "_run_docs_indexing"):
        assert lane in sites, f"{lane} no longer builds an org-scoped SDK?"
        assert "_bind_default_graph_record_tenant" in calls[lane], (
            f"{lane} builds an org-scoped SDK and no longer installs the "
            "org's DEFAULT-graph record tenant (#10047)")
