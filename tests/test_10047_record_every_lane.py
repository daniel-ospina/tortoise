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
from tortoise import sdk as _sdk_module
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


# ── The recurrence guard: the WRITE SITES are derived from the source ──────
# Every test above pins ONE lane, by name. That is the right shape for a
# behaviour pin and the wrong shape for the hazard: #10087's five bindings
# closed the five lanes that existed, and the NEXT lane a feature PR adds —
# a new background job, a new organically-grown route — would carry no tenant
# and redden NOTHING here, because no test above knows about it. The record's
# refusal is a DECLINE channel (`monitoring.record_write_failures`, invisible
# in `/health`'s `ok`), so that lane would write its graph and lose its rows
# silently, exactly as these five did before #10047.
#
# So this leg does not enumerate lanes, and does not enumerate VERBS, by hand.
# Both are ASKED OF THE SOURCE:
#
#   * the verbs come from `tortoise/sdk.py` — every function/method whose body
#     reaches `write_live_record` / `_apply_and_journal` / `_emit_event` /
#     `_journal_entity_event`, transitively through the module's own call
#     graph (#10380 review P1: the previous hand-picked list missed the SDK's
#     own aliases — `create_entity`, `supersede`, `batch_create_points`,
#     `index_file` — so a lane using them was invisible);
#   * the lanes come from `tortoise/hosted_api.py` — every place an ORG-scoped
#     SDK is opened, through ANY of the construction helpers a lane uses
#     (`_make_sdk(namespace=<not a literal>)` / `_make_sdk(graph_name=…)` /
#     `_open_org_graph_sdk(...)`, and the `_data_sdk` pair that bind on the way
#     out) — not only the `_make_sdk` spelling (#10380 review P2.1).
#
# The check is on the OBJECT, not on the name: a lane may not WRITE records
# through an SDK handle that no install covers. `_data_sdk(org)` in one local
# and a second, unbound `_make_sdk(namespace=org_id)` in another does not
# satisfy it — the writing handle is the one that must be bound (#10380 review
# P2.2), which is what "installed a tenant" has always meant here.
#
# WHAT IT CATCHES: a lane that opens an org SDK (by any construction helper)
# and writes through that SDK's own doors — the shape of `_run_docs_indexing`,
# the one lane of the five whose writes go straight through an SDK verb.
# WHAT IT DOES NOT: a lane that DELEGATES its writes to another module's writer
# (`GitHubIndexer.index_repo`, `indexer.backfill_legacy_closed`,
# `seed_onboarding_anchors`, `session_link.link_entity`) — four of the five
# #10087 lanes have exactly that shape (`_run_onboarding_seed` makes no SDK-door
# call at all; `_run_starter_seed` adds `sdk._get_proj`; `_run_indexing` and
# `_relink_sessions_after_index` call another module's writer). Following a
# hand-off into another module is a guess about which of ITS call sites write,
# and a guess in a guard reads as coverage it does not have. Those lanes are
# covered by the per-lane behaviour pins above; adding a sixth delegating lane
# is when a sixth behaviour pin belongs here. This leg is the SDK-door half,
# and it says so.

#: The helpers that OPEN an SDK a hosted lane may write through.
_SDK_OPENERS = frozenset({"_make_sdk", "_open_org_graph_sdk"})
#: The openers that install a record tenant on the SDK they return.
_INSTALLING_OPENERS = frozenset({"_data_sdk", "_data_sdk_offloaded"})
#: The carriers that install a record tenant on an SDK that already exists.
_TENANT_INSTALLERS = frozenset({
    "_bind_record_tenant",                # the credential-resolved chokepoint
    "_bind_default_graph_record_tenant",  # the org-DEFAULT lanes (#10047)
    "_bind_dream_record_tenant",          # the dream drain's own SDK
}) | _INSTALLING_OPENERS

#: The seeds the verb derivation starts from: a record row is appended HERE and
#: nowhere else, so "reaches one of these" is the definition of a record write.
_RECORD_WRITE_SEEDS = frozenset({
    "write_live_record", "_apply_and_journal", "_emit_event",
    "_journal_entity_event",
})

#: The non-vacuity floor for that derivation. A derived set is the right shape
#: (a new emitting verb is covered without editing this file) but it has one
#: failure mode: a refactor that moves the record append behind a differently
#: named carrier makes the derivation match LESS while still passing. ArchUnit
#: ships the same rule for the same reason — "Fail Rules on Empty Should", a
#: rule that matches nothing is not a pass — so the set is derived AND a pinned
#: core is asserted INSIDE it. The four aliases are the ones the #10380 review
#: measured as uncaught under the old hand-picked list.
_PINNED_CORE_VERBS = frozenset({
    # the seeds themselves, plus the module-level carriers in `sdk.py`
    "write_live_record", "_apply_and_journal", "_emit_event",
    "_journal_entity_event", "_write_session_and_turns", "_write_capture_turns",
    # public doors a lane plausibly writes through …
    "create_point", "create_object", "create_subject", "create_operator",
    "create_direct_edge", "index_directory", "ingest_corpus", "update_point",
    "supersede_point", "promote_point", "retract_point", "invalidate_point",
    "delete_point", "mitigate_operator", "annotate_operator", "checkpoint",
    "record_calibration", "set_point_baseline", "compute_confidence",
    # … and the aliases the hand-picked list missed (#10380 review P1)
    "batch_create_points", "create_entity", "index_file", "supersede",
})


def _called_names(node) -> set[str]:
    """Every function/method NAME called anywhere inside `node`."""
    return {name for name in (
        getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        for call in ast.walk(node) if isinstance(call, ast.Call)) if name}


def _module_functions(tree) -> dict:
    """Every module-level lane/helper, by name (a nested def rides its parent)."""
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _derived_record_emitting_verbs() -> frozenset[str]:
    """Every `sdk.py` name whose body can reach a record-write seed.

    DERIVED, never listed: the module's own call graph is walked to a fixpoint,
    so an alias (`supersede` → `supersede_point`), a private carrier
    (`_doc_write`) or a verb added tomorrow is covered without touching this
    file. Keyed by NAME because that is the granularity `ast` gives without
    type inference, which can only ever over-approximate — the safe direction
    for a guard that refuses a write site.
    """
    tree = ast.parse(Path(_sdk_module.__file__).read_text())
    calls: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls.setdefault(node.name, set()).update(_called_names(node))
    reaches = set(_RECORD_WRITE_SEEDS)
    grew = True
    while grew:
        grew = False
        for name, own in calls.items():
            if name not in reaches and own & reaches:
                reaches.add(name)
                grew = True
    return frozenset(reaches | _RECORD_WRITE_SEEDS)


_RECORD_EMITTING_VERBS = _derived_record_emitting_verbs()


def _namespace_is_org_scoped(call) -> bool:
    """`_make_sdk(namespace=<not a string literal>)` or `graph_name=…`.

    A string-literal namespace is the registry lane (`namespace="registry"`,
    `namespace=None` — the probe/anchor surfaces), never an org's graph; every
    other spelling (`org_id`, `org["org_id"]`, `namespace`) is org-scoped, and
    `graph_name=` is by definition a tenant graph (registry graphs are opened
    by namespace).
    """
    kwargs = {k.arg: k.value for k in call.keywords}
    namespace = kwargs.get("namespace")
    if "graph_name" in kwargs:
        return True
    return namespace is not None and not (
        isinstance(namespace, ast.Constant) and isinstance(namespace.value, str))


def _operand_calls(value) -> list:
    """The call expressions a value is made of, flattening `or`/`if else`."""
    if isinstance(value, ast.BoolOp):
        return [expr for operand in value.values
                for expr in _operand_calls(operand)]
    if isinstance(value, ast.IfExp):
        return _operand_calls(value.body) + _operand_calls(value.orelse)
    return [value]


def _handle_state(value) -> bool | None:
    """Is this assigned value an SDK handle, and is a tenant installed on it?

    `True` = a bound handle (`_data_sdk`/`_data_sdk_offloaded` bind before
    returning, a `_bind_*` call returns one), `False` = an org-scoped SDK opened
    with NO tenant, `None` = not an SDK handle at all (`_get_onboarding_projection`
    returns a dict, `_org_proj` a projection — neither is a record carrier).
    """
    state = None
    for expr in _operand_calls(value):
        if not isinstance(expr, ast.Call):
            continue
        name = getattr(expr.func, "id", None)
        if name in _INSTALLING_OPENERS:
            state = True
        elif name == "_open_org_graph_sdk":
            # The org's DEFAULT graph, opened by name — never bound.
            state = False
        elif name == "_make_sdk" and _namespace_is_org_scoped(expr):
            state = False
        elif name in _TENANT_INSTALLERS and expr.args:
            state = True
        if state is False:
            break  # an unbound operand wins: the write could ride it
    return state


def _assigned_handle(node):
    """`(local name, value)` for `x = …` / `x: T = …` — else `(None, None)`."""
    if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)):
        return node.targets[0].id, node.value
    if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.value is not None):
        return node.target.id, node.value
    return None, None


def _sdk_handles(node) -> dict[str, bool]:
    """`{local SDK handle: tenant installed?}` for one lane.

    Identity, not name presence (#10380 review P2.2): the answer belongs to the
    HANDLE, so an install on one handle says nothing about another in the same
    function. An install is found in any of its three spellings —
    `_bind_default_graph_record_tenant(sdk, org_id)`, `_bind_record_tenant(sdk,
    org)`, or the method form `sdk._bind_record_tenant(...)` — and marks THAT
    handle, then and for the rest of the lane.
    """
    handles: dict[str, bool] = {}
    for sub in ast.walk(node):
        name, value = _assigned_handle(sub)
        if name is None:
            continue
        state = _handle_state(value)
        if state is not None:
            handles[name] = state
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        called = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
        if called not in _TENANT_INSTALLERS:
            continue
        if sub.args and isinstance(sub.args[0], ast.Name):
            handles[sub.args[0].id] = True
        if isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name):
            handles[sub.func.value.id] = True
    return handles


def _org_scoped_sdk_sites(tree) -> dict[str, int]:
    """`{lane: count}` for every place a lane opens an ORG-SCOPED SDK.

    Any construction helper, not the `_make_sdk` spelling only (#10380 review
    P2.1): `_open_org_graph_sdk(org_id)` is the canonical way to open the org's
    DEFAULT graph by name, and a lane using it was invisible to a scan that
    looked for `_make_sdk` alone. The `_data_sdk` pair counts too — it binds on
    the way out, so it is clean by construction, but counting it is what makes
    "how many lanes open an org SDK?" a measurement instead of a guess.
    """
    sites: dict[str, int] = {}
    for name, node in _module_functions(tree).items():
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            called = getattr(call.func, "id", None)
            if ((called == "_make_sdk" and _namespace_is_org_scoped(call))
                    or called in _SDK_OPENERS | _INSTALLING_OPENERS):
                sites[name] = sites.get(name, 0) + 1
    return sites


def _writers_over_a_parameter(tree) -> dict[str, set[str]]:
    """`{helper: {parameter names it writes records through}}`.

    A lane may hand its SDK to a module-local helper instead of writing inline
    (`_seed(_sdk, …)`), so the handle check alone would miss it: the WRITE is
    spelled in the helper, the UNBOUND handle is at the call. Both ends are
    needed, and both are in this module.
    """
    writers: dict[str, set[str]] = {}
    for name, node in _module_functions(tree).items():
        params = {a.arg for a in
                  (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in _RECORD_EMITTING_VERBS
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in params):
                writers.setdefault(name, set()).add(call.func.value.id)
    return writers


def _unbound_record_writes(tree) -> list[str]:
    """Every call site that writes a record through an SDK handle no install covers."""
    functions = _module_functions(tree)
    handles_by_lane = {name: _sdk_handles(node)
                       for name, node in functions.items()}
    # The CALLEE's parameter list maps a positional argument to the parameter
    # the helper writes through — not the caller's.
    callee_params = {name: [a.arg for a in
                            (*node.args.posonlyargs, *node.args.args)]
                     for name, node in functions.items()}
    handed = _writers_over_a_parameter(tree)
    found: list[str] = []
    for name, node in _module_functions(tree).items():
        handles = handles_by_lane[name]
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if (isinstance(call.func, ast.Attribute)
                    and call.func.attr in _RECORD_EMITTING_VERBS):
                receiver = call.func.value
                if isinstance(receiver, ast.Name) and handles.get(receiver.id) is False:
                    found.append(f"{name}:{call.lineno} writes through the "
                                 f"unbound SDK `{receiver.id}` "
                                 f"(`{receiver.id}.{call.func.attr}(…)`)")
                elif isinstance(receiver, ast.Call) and _handle_state(receiver) is False:
                    found.append(f"{name}:{call.lineno} writes through an "
                                 f"inline unbound SDK "
                                 f"(`….{call.func.attr}(…)`)")
            callee = getattr(call.func, "id", None)
            if callee not in handed:
                continue
            # Only the arguments that land on a parameter the helper WRITES
            # through; a helper that merely closes the handle is not a write.
            positional = callee_params.get(callee, [])
            for index, arg in enumerate(call.args):
                param = positional[index] if index < len(positional) else None
                if param not in handed[callee]:
                    continue
                if isinstance(arg, ast.Name) and handles.get(arg.id) is False:
                    found.append(f"{name}:{call.lineno} hands the unbound SDK "
                                 f"`{arg.id}` to `{callee}`, which writes "
                                 f"records through it")
            for keyword in call.keywords:
                if keyword.arg not in handed[callee]:
                    continue
                if (isinstance(keyword.value, ast.Name)
                        and handles.get(keyword.value.id) is False):
                    found.append(f"{name}:{call.lineno} hands the unbound SDK "
                                 f"`{keyword.value.id}` to `{callee}`, which "
                                 "writes records through it")
    return found


def test_the_verb_derivation_still_answers():
    """Non-vacuity of the DERIVATION: an empty match is not a pass.

    Mutation this reddens on: any change that stops `_derived_record_emitting_verbs`
    reaching the SDK's own record doors (a refactor that renames a seed, a
    source read that silently parses nothing) — the pinned core would fall out
    of the set while every lane stayed "clean".
    """
    missing = sorted(_PINNED_CORE_VERBS - _RECORD_EMITTING_VERBS)
    assert missing == [], (
        "the derived record-emitting verb set no longer reaches these, so the "
        f"scan below cannot see them: {missing}")


def test_no_hosted_lane_writes_records_through_an_unbound_sdk():
    """A NEW org-scoped lane that writes records must install a tenant.

    This is the leg that makes the class non-recurring: the verbs AND the lanes
    are read from the source, so a lane added tomorrow is scanned without
    anyone remembering to add a test. It reddens when such a lane writes through
    an unbound handle, and when an install is REMOVED from one of the lanes that
    writes through an SDK door.

    Non-vacuity is asserted first, because a scan that matched nothing would
    pass while proving nothing — the rule `tools/record_wiring_check.py` states
    for its own legs: a signal that cannot be read is not a pass.
    """
    tree = ast.parse(Path(ha.__file__).read_text())
    sites = _org_scoped_sdk_sites(tree)
    assert len(sites) >= 30, (
        "the scan found only "
        f"{len(sites)} org-scoped SDK construction sites — the AST criteria no "
        "longer match how lanes open their SDK")
    unbound = _unbound_record_writes(tree)
    assert unbound == [], (
        "these hosted lanes write records through an SDK handle with NO record "
        "tenant installed, so an ARMED deployment refuses every row they emit "
        "`no_tenant` (a DECLINE counter, not a failure — the lane looks healthy "
        "and files nothing). Install one at the construction site: "
        "`_bind_default_graph_record_tenant(sdk, org_id)` for a lane that "
        "cannot see a request credential (#10047), or open the SDK through "
        "`_data_sdk`/`_data_sdk_offloaded` when it can:\n  " + "\n  ".join(unbound))

    # The lanes #10087 wired, as the SOURCE sees them. A behaviour pin above
    # already reds if one stops installing; this keeps the inventory honest
    # when a runtime pin is edited rather than the code.
    for lane in ("_run_onboarding_seed", "_run_starter_seed", "_run_docs_indexing",
                 "_relink_sessions_after_index", "_run_indexing"):
        assert lane in sites, f"{lane} no longer opens an org-scoped SDK?"
        assert "_bind_default_graph_record_tenant" in _called_names(
            _module_functions(tree)[lane]), (
            f"{lane} opens an org-scoped SDK and no longer installs the "
            "org's DEFAULT-graph record tenant (#10047)")


# ── The guard's own pins, on synthetic lanes ──────────────────────────────
# The four findings the #10380 review measured were all FALSE NEGATIVES: a lane
# shaped like X stayed GREEN. Each one is reproduced below as a source snippet
# and must be flagged, so the guard cannot regress to catching only the shape
# that happened to exist when it was written. Every case was GREEN before this
# round — the pre-fix scan matched `_make_sdk` spellings only, decided on the
# presence of an installer NAME anywhere in the lane, and took its verbs from a
# hand-picked list.

def _flagged(source: str) -> list[str]:
    """Run the guard's write-site scan over a synthetic hosted module."""
    return _unbound_record_writes(ast.parse(source))


@pytest.mark.parametrize("verb", ["batch_create_points", "create_entity",
                                  "supersede", "index_file", "create_point"])
def test_a_lane_using_an_sdk_alias_is_caught(verb):
    """P1 (#10380): the verbs are DERIVED, so an alias is not a blind spot.

    Mutation this reddens on: replacing `_RECORD_EMITTING_VERBS` with the old
    hand-picked frozenset — `batch_create_points`, `create_entity`, `supersede`
    and `index_file` are all absent from it, so four of these five cases go
    silent while still reporting a clean scan.
    """
    assert verb in _RECORD_EMITTING_VERBS, (
        f"{verb} reaches a record append in tortoise/sdk.py, so it must be a "
        "derived verb")
    source = f'''
def _run_new_feature_job(org_id: str, repos) -> None:
    sdk = _make_sdk(namespace=org_id)
    try:
        sdk.{verb}(repos[0])
    finally:
        sdk.close()
'''
    assert _flagged(source), (
        f"a lane writing through `sdk.{verb}()` on an unbound org SDK must be "
        "flagged")


def test_a_lane_opening_through_open_org_graph_sdk_is_caught():
    """P2.1 (#10380): site detection follows EVERY construction helper.

    `_open_org_graph_sdk` is the canonical way to open the org's DEFAULT graph
    by name, so a lane using it was outside a scan that looked for `_make_sdk`
    alone — it was not even a site, so nothing about it was checked.
    """
    source = '''
def _run_new_job(org_id: str) -> None:
    sdk = _open_org_graph_sdk(org_id)
    if sdk is None:
        return
    try:
        sdk.create_point("Subject", {})
    finally:
        sdk.close()
'''
    flagged = _flagged(source)
    assert flagged and "_run_new_job" in "".join(flagged)


def test_an_install_on_another_handle_does_not_cover_the_writing_one():
    """P2.2 (#10380): the install is per HANDLE, not per name-in-the-body.

    A lane that resolves its credential SDK with `_data_sdk(org)` — which
    binds — and then opens a SECOND, unbound `_make_sdk(namespace=org_id)` and
    writes through that one was GREEN while a tenantless write went through.
    """
    source = '''
def _run_two_sdk_job(org: dict) -> None:
    data = _data_sdk(org)
    other = _make_sdk(namespace=org["org_id"])
    try:
        other.create_point("Subject", {})
    finally:
        other.close()
        data.close()
'''
    flagged = _flagged(source)
    assert flagged, ("the bound `data` handle must not launder the unbound "
                     "`other` handle")
    assert "other" in "".join(flagged)


def test_a_lane_that_installs_on_the_writing_handle_is_clean():
    """The control: the same lane, with the install on the handle it writes
    through, is silent — so the legs above are not flagging any write at all.
    """
    source = '''
def _run_bound_job(org_id: str) -> None:
    sdk = _make_sdk(namespace=org_id)
    _bind_default_graph_record_tenant(sdk, org_id)
    try:
        sdk.create_point("Subject", {})
    finally:
        sdk.close()
'''
    assert _flagged(source) == []


def test_a_lane_that_hands_an_unbound_sdk_to_a_writing_helper_is_caught():
    """A lane may write through a helper instead of inline; the handle is
    still the thing that has to be bound, so the CALL is checked."""
    source = '''
def _seed_the_graph(sdk, org_id: str) -> None:
    sdk.create_subject("Person", "Ann")

def _run_seed_lane(org_id: str) -> None:
    sdk = _make_sdk(namespace=org_id)
    try:
        _seed_the_graph(sdk, org_id)
    finally:
        sdk.close()
'''
    flagged = _flagged(source)
    assert flagged and "_run_seed_lane" in "".join(flagged)


def test_an_inline_unbound_construction_that_writes_is_caught():
    """`_make_sdk(namespace=org_id).create_point(…)` binds no handle to check,
    so the receiver itself is inspected."""
    source = '''
def _run_inline_job(org_id: str) -> None:
    _make_sdk(namespace=org_id).create_point("Subject", {})
'''
    assert _flagged(source), "an inline unbound write must be flagged"
