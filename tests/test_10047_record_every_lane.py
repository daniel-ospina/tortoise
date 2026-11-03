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
import functools
import re
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
_SDK_OPENERS = frozenset({
    "_make_sdk", "_open_org_graph_sdk",
    # #10180: the ONE default-graph resolver every org-wide lane now opens
    # through (and its I/O-free credential-shaped twin). A scan that did not
    # learn these names would silently stop counting those lanes — exactly the
    # drift this inventory exists to catch.
    "_org_sdk", "_org_sdk_for_credential", "_org_sdk_from_name",
})
#: The openers that install a record tenant on the SDK they return.
_INSTALLING_OPENERS = frozenset({"_data_sdk", "_data_sdk_offloaded"})
#: The openers that return an SDK with NO record tenant installed on it. Every
#: one of these is a real SDK handle, so a write through it without a later
#: `_TENANT_INSTALLERS` call is exactly the unbound write this file exists to
#: catch — `None` from `_handle_state` would mean "not an SDK handle at all"
#: and let the write past.
#:
#: `_open_org_graph_sdk` was the #10380 P2.1 finding; the three `_org_sdk*`
#: spellings are #10180's, and they are the ones the codebase now opens through
#: BY DESIGN (`_org_sdk`'s own docstring: "the ONE way an org-wide lane opens
#: it"). They all end in `_make_sdk(...)` and install nothing, so before this
#: entry every new lane could convert to the canonical opener, be counted as a
#: site, and write records through an unbound handle with the scan silent —
#: the false-PASS shape, on the spelling the fleet is migrating TO.
_UNBOUND_OPENERS = frozenset({
    "_open_org_graph_sdk",        # the org's DEFAULT graph, opened by name
    "_org_sdk",                   # #10180: the one default-graph resolver
    "_org_sdk_from_name",         #   …its already-resolved-name twin (no I/O)
    "_org_sdk_for_credential",    #   …and its credential-shaped twin
})
#: The carriers that install a record tenant on an SDK that already exists.
_TENANT_INSTALLERS = frozenset({
    "_bind_record_tenant",                # the credential-resolved chokepoint
    "_bind_default_graph_record_tenant",  # the org-DEFAULT lanes (#10047)
    "_bind_dream_record_tenant",          # the dream drain's own SDK
}) | _INSTALLING_OPENERS

#: SDK-returning names whose record tenant this static scan CANNOT model, split
#: from the vocabularies above so that "not modelled" is declared rather than
#: silent. `_handle_state` answers `None` ("not an SDK handle at all") for any
#: name it does not recognise, and `_unbound_record_writes` then looks straight
#: past the write — the false-PASS shape this file has now remediated three
#: times (#10380 P2.1 `_open_org_graph_sdk`, #10180's `_org_sdk*` spellings, and
#: this one). The sets below are the third remediation: the classification is
#: PINNED by `test_every_sdk_shaped_name_in_the_package_is_classified`, so the
#: next opener must be classified deliberately instead of arriving as a silent
#: `None`.
#:
#: `_AMBIENT_OPENERS` DO return a real org-scoped SDK a lane writes through, and
#: their tenant is the REQUEST's ambient scope (`OrgResolutionMiddleware` →
#: `_record_tenant_scope`) — installed around the whole request rather than on
#: the handle, which no amount of AST reading can see. `_get_org_sdk` is the
#: package's second org-scoped opener and carries `tortoise/mcp_server.py`'s
#: entire record-write surface, so leaving it out of every set would have made
#: this file claim a completeness it does not have. The limit is real and is
#: stated rather than papered over: a lane that opens `_get_org_sdk()` OUTSIDE a
#: request has no tenant and this scan cannot tell.
_AMBIENT_OPENERS = frozenset({
    "_get_org_sdk",               # request-scoped; tenant = the ambient scope
})
#: SDK-shaped names that are NOT org-scoped lane construction sites: the
#: registry/base/probe accessors, their caches, and the plain helpers that take
#: an SDK rather than return one. They are not "bound" and not "unbound" — they
#: are simply not the thing this scan judges, and naming them keeps the
#: completeness leg from having to special-case them. Every member is asserted to
#: still EXIST, so this cannot rot into a fiction.
_NOT_A_LANE_OPENER = frozenset({
    # accessors that return an SDK for a NON-org-scoped graph
    "_sdk", "_get_sdk", "_default_sdk", "_get_base_sdk",
    "_probe_sdk", "_acquire_probe_sdk", "_registry_probe_sdk",
    "_reset_registry_probe_sdk", "_registry_sdk", "_reg_sdk",
    "_close_cached_sdk",
    # helpers that take a handle (or a backend) instead of opening a lane
    "mine_corpus_with_sdk", "_retire_probe_sdk_locked", "_sdk_matches_env",
    "_sdk_usable", "_sdk_backend_is_embedded", "sdk_reflex",
    "_probe_sdk_key", "_probe_sdk_reset",
})

#: The same, for CLASS-SCOPED opener methods. A method is scoped to its class, so
#: it is classified here rather than in the module vocabulary — but it must be
#: classified, because a method that RETURNS an SDK is a handle source, and when
#: its name is in no vocabulary `_handle_state` answers `None` for the call and
#: the write through the returned handle is skipped (#10046 review round 5 P2).
_NOT_A_LANE_OPENER_METHODS = frozenset({
    # `_ha._make_sdk(namespace="registry")` — the registry lane, not an org graph.
    "OrgResolutionMiddleware._get_registry_sdk",
})
#: An SDK-SHAPED name — `…_sdk`, `_sdk…`, `sdk_…`. Broader than
#: `endswith("_sdk")`, which silently excluded `_sdk_matches_env`-style helpers
#: AND the real openers `_org_sdk_from_name` / `_data_sdk_offloaded` (their
#: `_sdk_` is interior), so a RENAME could have retired a declared opener into a
#: silent exemption without reddening anything.
_SDK_NAME_RE = re.compile(r"(^|_)sdk(_|$)")

#: Every name this file claims to know. A name outside it reaches `_handle_state`
#: as `None`, which is the silent exemption the completeness leg exists to stop.
_DECLARED_SDK_NAMES = (frozenset(_SDK_OPENERS) | frozenset(_INSTALLING_OPENERS)
                       | frozenset(_UNBOUND_OPENERS)
                       | frozenset(_TENANT_INSTALLERS)
                       | _AMBIENT_OPENERS | _NOT_A_LANE_OPENER)

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
    """Every lane/helper this file scans, by name.

    A NESTED def rides its parent — its calls are already inside the parent's
    `ast.walk`, so listing it separately would double-count. A CLASS METHOD does
    not ride anything: `tree.body` never saw it, so a `Worker.run()` that opens
    an org-scoped SDK and writes records was neither a site nor a write. That
    was an entire blind axis (#10046 review P2) — the package has 564 classes,
    and a worker/indexer class is the most natural shape for the next lane.
    Keyed `Class.method` so a method cannot collide with a module-level name.

    Measured when methods were added: 0 method-level org-scoped opener calls in
    the package, so this widens the scan WITHOUT reddening anything — it only
    closes the shape that does not exist yet.
    """
    found = {node.name: node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(f"{cls.name}.{node.name}", node)
    return found


@functools.cache
def _tree_at(path: str, mtime_ns: int, size: int):
    """A parsed module, cached on the file's OWN identity.

    Keyed on `(mtime, size)` rather than the path alone, deliberately: a rewrite
    within one process invalidates instead of serving a stale tree. What makes
    that sufficient is that each pytest RUN is a fresh process — the mutation
    pins in this file use `_flagged(source)` on a synthetic string and never
    touch the filesystem, so a probe added or removed between runs is always
    re-parsed. The failure this guards against is a cache that turns a real red
    into a false green, which a stale tree would do silently.
    """
    return ast.parse(Path(path).read_text())


def _tree_of(path: Path):
    """`ast.parse` of *path*, cached while the file is unchanged."""
    stat = path.stat()
    return _tree_at(str(path), stat.st_mtime_ns, stat.st_size)


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

    `_UNBOUND_OPENERS` is the list that decides `False`, and it is the list that
    must stay complete: an opener that returns a real SDK and matches NEITHER
    branch answers `None`, i.e. "not a handle", and the write-detector below
    then looks straight past it (`handles.get(name) is False` never holds).
    """
    state = None
    for expr in _operand_calls(value):
        if not isinstance(expr, ast.Call):
            continue
        # `id or attr`: the MODULE-QUALIFIED spelling is ordinary and already in
        # the shipped package (`_ha._make_sdk(namespace=org_id)` in
        # `supabase_control.py`, twice). Reading the bare name only answered
        # `None` for it — "not an SDK handle at all" — so the write went past
        # (#10046 review round 4 P1). `_sdk_handles` already used `id or attr`
        # for the installer half; this is the opener half catching up.
        name = (getattr(expr.func, "id", None)
                or getattr(expr.func, "attr", None))
        if name in _INSTALLING_OPENERS:
            state = True
        elif name in _UNBOUND_OPENERS:
            # A real SDK handle, with no tenant installed on it.
            state = False
        elif name == "_make_sdk" and _namespace_is_org_scoped(expr):
            state = False
        elif name in _TENANT_INSTALLERS and expr.args:
            state = True
        if state is False:
            break  # an unbound operand wins: the write could ride it
    return state


def _assigned_handle(node):
    """`(handle key, value)` for `x = …`, `self.x = …`, `x: T = …`, `with … as x`.

    The key is a bare name for a local and the DOTTED spelling for an attribute
    (`self._own_sdk`), because an SDK a class OWNS is a handle too (#10046
    review round 5 P1): reading only `ast.Name` targets meant `__init__`'s
    `self._own_sdk = _make_sdk(namespace=org_id)` was never a handle, so a
    `run()` writing `self._own_sdk.create_point(...)` was never a write. That is
    the package's dominant class idiom — `dream.py`, `onboarding/selfhost.py`,
    `hosted_api._OrgSeedSurface`, `mcp_auth` all hold their SDK on `self`.

    `with … as sdk` is here too (#10046 review round 6 P1): `TortoiseSDK`
    implements `__enter__`/`__exit__` (`sdk.py`), so the context-manager form is
    a SUPPORTED way to hold it and the plainest Python spelling for one — not an
    indirect shape like a lambda or a factory, which are declared out.
    """
    if isinstance(node, ast.With):
        for item in node.items:
            target = item.optional_vars
            if isinstance(target, ast.Name):
                return target.id, item.context_expr
            if isinstance(target, ast.Attribute):
                return ast.unparse(target), item.context_expr
        return None, None
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id, node.value
        if isinstance(target, ast.Attribute):
            return ast.unparse(target), node.value
    if (isinstance(node, ast.AnnAssign)
            and isinstance(node.target, (ast.Name, ast.Attribute))
            and node.value is not None):
        target = node.target
        return (target.id if isinstance(target, ast.Name)
                else ast.unparse(target)), node.value
    return None, None


def _class_attribute_handles(tree) -> dict[str, dict[str, bool]]:
    """`{class: {dotted attribute: tenant installed?}}` for the whole class.

    An SDK a class OWNS is opened in one method and written in another, so the
    per-lane map cannot see it: `__init__` opens, `run` writes, and each was
    judged alone. Unioning the ATTRIBUTE half across a class's methods is what
    lets the write in `run` find the handle `__init__` set (#10046 review round 5
    P1). Only attribute keys are unioned — a local of one method says nothing
    about another — and only handles whose state is KNOWN, so an attribute
    assigned from a parameter (`self._sdk = sdk`) stays out rather than being
    guessed at.
    """
    by_class: dict[str, dict[str, bool]] = {}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        merged: dict[str, bool] = {}
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for key, state in _sdk_handles(node).items():
                    if "." in key:
                        merged[key] = merged.get(key, True) and state
        if merged:
            by_class[cls.name] = merged
    return by_class


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
            # WORST-WINS across assignments (#10046 review round 6 P2): keeping the
            # last one lets `sdk = _make_sdk(org)` followed by
            # `if not sdk: sdk = _data_sdk(org)` answer bound while an unbound
            # path is still live — inverting the "an unbound operand wins" rule
            # `_handle_state` already applies WITHIN one value. An explicit
            # install below still arms the handle, which is the sequence the
            # failure message tells a developer to write.
            handles[name] = handles.get(name, True) and state
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        called = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
        if called not in _TENANT_INSTALLERS:
            continue
        # Key on the SAME spelling `_assigned_handle` produces, or an install on
        # an SDK held on `self` cannot mark it bound (#10046 review round 6 P1):
        # `_bind_default_graph_record_tenant(self._sdk, org)` has an ATTRIBUTE in
        # `args[0]`, and the method form has one as `sub.func.value` — reading
        # only `ast.Name` silently marked `org` bound instead and left the handle
        # False, so a developer who followed this test's own failure message
        # ("Install one at the construction site: `_bind_default_graph_record_
        # tenant(sdk, org_id)`") stayed RED.
        for arg in sub.args:
            if isinstance(arg, (ast.Name, ast.Attribute)):
                key = arg.id if isinstance(arg, ast.Name) else ast.unparse(arg)
                handles[key] = True
        if isinstance(sub.func, ast.Attribute) and isinstance(
                sub.func.value, (ast.Name, ast.Attribute)):
            receiver = sub.func.value
            handles[receiver.id if isinstance(receiver, ast.Name)
                    else ast.unparse(receiver)] = True
    return handles


def _org_scoped_sdk_sites(tree) -> dict[str, int]:
    """`{lane: count}` for every place a lane opens an ORG-SCOPED SDK.

    Any construction helper, not the `_make_sdk` spelling only (#10380 review
    P2.1): `_open_org_graph_sdk(org_id)` is the canonical way to open the org's
    DEFAULT graph by name, and a lane using it was invisible to a scan that
    looked for `_make_sdk` alone. The `_data_sdk` pair counts too — it binds on
    the way out, so it is clean by construction, but counting it is what makes
    "how many lanes open an org SDK?" a measurement instead of a guess.

    `_make_sdk` is in `_SDK_OPENERS`, so the membership test below MUST exclude
    it or the namespace predicate above is dead code: every `_make_sdk(...)`
    would count as an "org-scoped site" — including the registry/base lanes that
    pass a string-literal namespace. Measured at #10046 review round 3: 94 of
    175 counted "sites" were literal-namespace constructions, i.e. 54% of the
    count was not org-scoped at all, which let the non-vacuity floor stand on
    registry lanes alone. Corrected, the package has 81 org-scoped sites across
    two modules.
    """
    sites: dict[str, int] = {}
    unconditional = (_SDK_OPENERS - {"_make_sdk"}) | _INSTALLING_OPENERS
    for name, node in _module_functions(tree).items():
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            called = (getattr(call.func, "id", None)
                      or getattr(call.func, "attr", None))
            if ((called == "_make_sdk" and _namespace_is_org_scoped(call))
                    or called in unconditional):
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
    # An SDK a CLASS owns (`self._own_sdk`) is opened in one method and written in
    # another, so the per-lane map cannot see it. Union the attribute half across
    # each class's methods into the lanes that belong to it (#10046 review round 5
    # P1: a worker class holding its handle on `self` was green).
    class_attrs = _class_attribute_handles(tree)
    for name in handles_by_lane:
        owner = name.split(".", 1)[0]
        if "." in name and owner in class_attrs:
            for key, state in class_attrs[owner].items():
                handles_by_lane[name][key] = (
                    handles_by_lane[name].get(key, True) and state)
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
                elif (isinstance(receiver, ast.Attribute)
                        and handles.get(ast.unparse(receiver)) is False):
                    dotted = ast.unparse(receiver)
                    found.append(f"{name}:{call.lineno} writes through the "
                                 f"unbound SDK `{dotted}` "
                                 f"(`{dotted}.{call.func.attr}(…)`)")
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


#: The shipped package, derived from the module the hosted lanes live in — so
#: the walk follows the INSTALL rather than a hand-written path.
_PACKAGE_ROOT = Path(ha.__file__).resolve().parent

#: The modules measured to contribute an ORG-SCOPED SDK construction site. The
#: package-wide leg asserts this list is still a SUBSET of what it finds, which is
#: the only per-module drift signal available: every per-module rule built from
#: the scan's own predicates is tautological, so the inventory has to be the
#: external reference. A legitimate new org-scoped lane may ADD a module without
#: touching this; a module that stops matching reds.
_ORG_SCOPED_MODULES = frozenset({"hosted_api.py", "quota.py",
                                "supabase_control.py"})


def _joined(items: list[str]) -> str:
    """The multi-line assertion body: one item per line, indented under it."""
    return "\n  " + "\n  ".join(items)


def test_no_lane_ANYWHERE_in_the_package_writes_records_through_an_unbound_sdk():
    """#10046: the class is PACKAGE-wide, not `hosted_api.py`-wide.

    Why this leg exists. The leg above parses ONE module (`ha.__file__`), which
    is where every hosted lane happened to live when #10047 landed. Nothing
    keeps a record-writing lane there: a new `hosted_worker.py`, the backup
    sweep, or any future module can open `_make_sdk(namespace=org_id)` (or one
    of the `_org_sdk*` openers) and emit rows no install covers — and the guard
    stays GREEN while an ARMED deployment refuses every one `no_tenant`. That
    is a DECLINE counter, not a failure: the lane reads healthy and files
    nothing, which is exactly the silent-invisible class #10046 was filed for
    ("the gap is invisible until such a tenant tries to write").

    Measured when this leg was added: 264 modules, 83 org-scoped construction
    sites across THREE modules, and 0 unbound record writes, so widening the scan
    cannot red on the code as it stands. Its whole job is the module that does
    not exist yet. (Two earlier revisions reported different counts, each fixed
    at review: "149 entries / 175 sites" was inflated by a doubled predicate —
    `_make_sdk` is a member of `_SDK_OPENERS`, so the membership test matched
    EVERY `_make_sdk(...)` call and 94 of the 175 were registry/base literals;
    then "81 sites / two modules" still missed `supabase_control.py`, whose two
    lanes open through the MODULE-QUALIFIED spelling `_ha._make_sdk(...)` that a
    bare-name reader cannot see. Both are fixed in `_org_scoped_sdk_sites` /
    `_handle_state`.)

    WHAT THIS SCAN CANNOT SEE, stated rather than implied: it judges a write by
    the handle's OPENER, and only openers in the declared vocabularies are
    recognised (`_unbound_record_writes` looks past a handle `_handle_state`
    answers `None` for). So "0 unbound" is exactly "0 writes through a DECLARED
    unbound opener" — the vocabulary's completeness is what makes the claim
    mean anything, and it is pinned by
    `test_every_sdk_shaped_name_in_the_package_is_classified`. A lane opening
    `_get_org_sdk()` outside a request is bound by nothing and is the declared
    blind spot (`_AMBIENT_OPENERS`). Two indirections are likewise OUT of the
    scan and are named so they are not mistaken for coverage: an SDK handed to a
    writing helper as a KEYWORD argument (`_write(sdk=_org_sdk(org))`) and one
    reached through a lambda or local factory. Also OUT, and listed so `0 unbound`
    is not read as covering them: an SDK handed to a writing helper as an INLINE
    call in either position (`_write(_org_sdk(org))` / `_write(sdk=_org_sdk(org))`
    — the bound-name forms of both ARE caught); an SDK held on a MODULE-LEVEL
    global and written from a function; a passing alias (`s2 = sdk`); a walrus
    (`if (sdk := _make_sdk(...))`); `setattr(self, "_sdk", ...)`; tuple unpack;
    and a SUBCLASS writing through a handle its base class's `__init__` opened
    (the class map is keyed per class). What IS scanned: methods, an SDK held on
    `self` (opened in one method, written in another), `with … as sdk`, and
    module-qualified dispatch (`_ha._make_sdk(...)`).
    """
    modules = sorted(_PACKAGE_ROOT.rglob("*.py"))
    assert len(modules) >= 200, (
        f"the package walk found only {len(modules)} modules under "
        f"{_PACKAGE_ROOT} — the walk no longer sees the shipped package, so a "
        "clean result would prove nothing")

    sites: dict[str, int] = {}
    unbound: list[str] = []
    unparsed: list[str] = []
    contributing: set[str] = set()
    for path in modules:
        rel = path.relative_to(_PACKAGE_ROOT)
        try:
            tree = _tree_of(path)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            # CANNOT-ANSWER is not a pass: an unreadable module could hold the
            # very lane this scan exists to catch.
            unparsed.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        opened_here = _org_scoped_sdk_sites(tree)
        for lane, count in opened_here.items():
            sites[f"{rel}:{lane}"] = count
        if opened_here:
            contributing.add(str(rel))
        unbound.extend(f"{rel}: {site}"
                       for site in _unbound_record_writes(tree))

    assert unparsed == [], (
        "the package walk could not parse these modules, so it cannot answer "
        "whether they install a record tenant:" + _joined(unparsed))

    # The MODULE axis, which a global site floor cannot see: only two modules
    # contribute sites and `hosted_api.py` holds 80 of the 81, so criteria drift
    # that stopped matching anywhere else would still clear the floor below with
    # the one module it already covered. A SUPERSET assertion (not equality) is
    # the honest form — a legitimate new org-scoped lane may ADD a module freely,
    # while a module that stops matching reds.
    missing = sorted(_ORG_SCOPED_MODULES - contributing)
    assert missing == [], (
        "these modules used to contribute an ORG-SCOPED SDK construction site and "
        "now contribute none, so the scan's AST criteria have stopped matching "
        "in them and `unbound == []` says nothing about their lanes. Either the "
        "criteria need updating, or the module genuinely lost its last lane — "
        "in which case update `_ORG_SCOPED_MODULES` deliberately:" + _joined(missing))

    # Non-vacuity, the same rule the single-module leg states: a scan that
    # matched nothing is not a pass. 60 against a measured 83.
    assert sum(sites.values()) >= 60, (
        f"the package scan found only {sum(sites.values())} org-scoped SDK "
        "construction sites — the AST criteria no longer match how lanes open "
        "their SDK, so `unbound == []` below certifies nothing")

    assert unbound == [], (
        "these lanes OUTSIDE hosted_api.py write records through an SDK handle "
        "with NO record tenant installed, so an ARMED deployment refuses every "
        "row they emit `no_tenant` (a DECLINE counter — the lane looks healthy "
        "and files nothing). Install one at the construction site: "
        "`_bind_default_graph_record_tenant(sdk, org_id)` for a lane that "
        "cannot see a request credential (#10047), or open the SDK through "
        "`_data_sdk`/`_data_sdk_offloaded` when it can:" + _joined(unbound))


def test_every_sdk_shaped_name_in_the_package_is_classified():
    """The `0 unbound` claim is only as strong as the OPENER vocabulary.

    `_handle_state` answers `None` — "not an SDK handle at all" — for a name it
    does not recognise, and `_unbound_record_writes` then looks straight past
    the write. An opener that returns a real org-scoped SDK, is in no declared
    set, and is used by a lane WITHOUT an install is therefore GREEN: the exact
    recurrence the package-wide leg above exists to catch, arriving through the
    one axis that leg cannot see.

    Measured when this leg was added: 27 SDK-SHAPED module-level definitions in
    the package (the `_sdk` token anywhere, not just a `_sdk` suffix — the suffix
    test alone excluded the real openers `_org_sdk_from_name` and
    `_data_sdk_offloaded`), 7 of them already declared, 20 undeclared — including
    `_get_org_sdk`, which carries `tortoise/mcp_server.py`'s entire record-write
    surface (109 call sites) and whose tenant is the request's AMBIENT scope.

    Mutation this reddens on: deleting `_get_org_sdk` from `_AMBIENT_OPENERS`
    (it returns to being silently exempt), or adding an SDK-shaped helper to any
    module without classifying it.
    """
    modules = sorted(_PACKAGE_ROOT.rglob("*.py"))
    shaped: set[str] = set()
    shaped_methods: set[str] = set()
    for path in modules:
        try:
            tree = _tree_of(path)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue  # the leg above fails LOUD on these; do not double-report
        # MODULE-LEVEL names go in the package vocabulary.
        shaped.update(
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _SDK_NAME_RE.search(node.name))
        # CLASS-SCOPED opener methods are a SEPARATE vocabulary, not an
        # afterthought. This leg used to skip them with the justification "its
        # writes are covered by the leg above, which now walks methods too" —
        # which was FALSE: the write is walked, but its handle came from a name
        # no vocabulary knew, so `_handle_state` answered `None` and the write
        # was skipped (#10046 review round 5 P2). A handle source must be
        # classified whichever scope it lives in.
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for node in cls.body:
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and _SDK_NAME_RE.search(node.name)):
                    shaped_methods.add(f"{cls.name}.{node.name}")

    assert len(shaped) >= 20, (
        f"only {len(shaped)} SDK-shaped definitions found in the package — the "
        "name shape no longer describes how openers are spelled, so the "
        "classification below certifies nothing: " + _joined(sorted(shaped)))

    unclassified = sorted(shaped - _DECLARED_SDK_NAMES)
    assert unclassified == [], (
        "these SDK-shaped names are in NO declared vocabulary, so "
        "`_handle_state` answers `None` for them and `_unbound_record_writes` "
        "looks past every write that goes through one. Classify each one — "
        "`_UNBOUND_OPENERS` (returns an SDK with no tenant: the scan judges it), "
        "`_INSTALLING_OPENERS` (installs before returning), "
        "`_AMBIENT_OPENERS` (tenant is the request's ambient scope, unmodellable "
        "here) or `_NOT_A_LANE_OPENER` (not an org-scoped lane construction "
        "site):" + _joined(unclassified))

    # CLASS-SCOPED opener methods, classified separately: a method returning an
    # SDK is a handle source too, and when it is in no vocabulary `_handle_state`
    # answers `None` for it and the write through its return value is skipped.
    unclassified_methods = sorted(shaped_methods - _NOT_A_LANE_OPENER_METHODS)
    assert unclassified_methods == [], (
        "these SDK-shaped METHODS are in no declared vocabulary, so a write "
        "through the handle one returns is invisible to the scan. Classify each "
        "in `_NOT_A_LANE_OPENER_METHODS` (with the reason) or, if it opens an "
        "org graph, in `_UNBOUND_OPENERS`:" + _joined(unclassified_methods))

    # The declaration cannot rot into a lie — checked over every module-level
    # vocabulary whose members are MODULE-LEVEL FUNCTIONS. The suffix-era
    # version checked only
    # `_AMBIENT_OPENERS | _NOT_A_LANE_OPENER`, so renaming `_org_sdk_from_name`
    # would have left the stale entry standing while the new name reached
    # `_handle_state` as `None` (a silent exemption, and the very false PASS this
    # file exists to stop). `_SDK_OPENERS` is included for the same reason — and
    # for an opener spelled WITHOUT an `_sdk` token (`_open_org_graph`), which
    # `unclassified` above cannot see at all.
    # `_TENANT_INSTALLERS` is EXCLUDED deliberately: it mixes module-level
    # functions with `TortoiseSDK` METHOD names (`_bind_record_tenant` is called
    # as `sdk._bind_record_tenant`), so a `tree.body` lookup would report every
    # method as vanished.
    vanished = sorted(
        name for name in (_SDK_OPENERS | _UNBOUND_OPENERS | _INSTALLING_OPENERS
                          | _AMBIENT_OPENERS | _NOT_A_LANE_OPENER)
        if name not in shaped)
    assert vanished == [], (
        "these declared SDK names no longer exist as module-level definitions, "
        "so the classification is stale and its exemptions are unchecked:"
        + _joined(vanished))


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


def test_a_lane_opening_through_a_MODULE_QUALIFIED_opener_is_caught():
    """#10046 review round 4 P1: `_ha._make_sdk(namespace=org_id)`.

    The module-qualified spelling is ordinary, not exotic — `supabase_control.py`
    uses it TWICE today — and a reader that only takes `node.func.id` answers
    `None` ("not an SDK handle at all") for an `ast.Attribute`, so BOTH the site
    count and the write detector looked past it. That made the package-wide leg
    green on the exact recurrence it exists to catch, and it made the census
    wrong (`supabase_control.py` was invisible).

    Mutation this reddens on: dropping `or getattr(…, "attr", None)` from
    `_handle_state` or `_org_scoped_sdk_sites` (the file's own `_sdk_handles`
    already read `id or attr` for the installer half).
    """
    source = '''
from tortoise import hosted_api as _ha

def _run_qualified_lane(org_id: str) -> None:
    sdk = _ha._make_sdk(namespace=org_id)
    try:
        sdk.create_point("Subject", {})
    finally:
        sdk.close()
'''
    assert _flagged(source), (
        "a lane writing through an SDK opened as `_ha._make_sdk(...)` must be "
        "flagged — an attribute callee is not 'not an SDK handle'")
    assert _org_scoped_sdk_sites(ast.parse(source)), (
        "a module-qualified org-scoped opener must also be COUNTED as a site, "
        "or the census and the module inventory under-report")


def test_a_CLASS_METHOD_writing_through_an_unbound_sdk_is_caught():
    """Pins the method walk, and the handle a class OWNS on `self`.

    Round 2 added class methods to the scan and round 5 added the handle a class
    keeps on `self` (opened in `__init__`, written in `run`) — the package's
    dominant class idiom. Neither had a pin, so either could be deleted silently:
    the shipped package has 0 method-level org-scoped sites and every one of its
    `self.*sdk` attributes comes from a parameter, so nothing exercised them.

    Mutation this reddens on: dropping the `for cls in tree.body` walk from
    `_module_functions`, or the attribute half of `_assigned_handle`.
    """
    local = '''
class _MethodLane:
    def run(self, org_id: str) -> None:
        sdk = _make_sdk(namespace=org_id)
        sdk.create_point("Subject", {})
'''
    assert _flagged(local), (
        "a write inside a CLASS METHOD through an unbound SDK must be flagged — "
        "this class is MODULE-LEVEL on purpose: nested inside a function the "
        "parent's `ast.walk` would reach it and the assertion would pass even "
        "with the method walk deleted")

    bind_then_write = '''
class _BoundWorker:
    def __init__(self, org_id: str) -> None:
        self._sdk = _make_sdk(namespace=org_id)
        _bind_default_graph_record_tenant(self._sdk, org_id)

    def run(self) -> None:
        self._sdk.create_point("Subject", {})
'''
    assert _flagged(bind_then_write) == [], (
        "the BOUND direction: an SDK held on `self` that the class then arms — "
        "exactly what this test's own failure message tells a developer to do — "
        "must be silent. Without this half the guard rejects the documented fix "
        "whenever the handle lives on `self` (#10046 review round 6 P1).")

    with_form = '''
def _run_with_lane(org_id: str) -> None:
    with _make_sdk(namespace=org_id) as sdk:
        sdk.create_point("Subject", {})
'''
    assert _flagged(with_form), (
        "`with _make_sdk(...) as sdk:` is a SUPPORTED way to hold this SDK "
        "(`TortoiseSDK.__enter__/__exit__`), so a write inside it must be "
        "flagged like any other")

    owned = '''
class _OwnWorker:
    def __init__(self, org_id: str) -> None:
        self._own_sdk = _make_sdk(namespace=org_id)

    def run(self) -> None:
        self._own_sdk.create_point("Subject", {})
'''
    assert _flagged(owned), (
        "an SDK the class OWNS (`self._own_sdk`, opened in `__init__`, written in "
        "`run`) must be flagged — a worker class holds its handle on `self`")

    control = '''
class _ParamWorker:
    def __init__(self, sdk) -> None:
        self._sdk = sdk

    def run(self) -> None:
        self._sdk.create_point("Subject", {})
'''
    assert _flagged(control) == [], (
        "the control: a handle that came from a PARAMETER has an unknown state "
        "and must NOT be flagged — guessing is the mis-filing this guard stops")


def test_a_literal_namespace_is_not_an_org_scoped_site():
    """Pins the round-3 predicate: `_make_sdk(namespace="registry")` is not ours.

    `_make_sdk` is a member of `_SDK_OPENERS`, so the membership test in
    `_org_scoped_sdk_sites` has to exclude it or EVERY `_make_sdk(...)` counts as
    an org-scoped site — 94 of 175 counted sites were registry/base literals, and
    the non-vacuity floor could be cleared by them alone. Without this pin the
    double-count can be reinstated by a "simplification" and nothing reds: the
    floor is a LOWER bound and the module inventory is a superset.
    """
    registry = 'def f():\n    return _make_sdk(namespace="registry")\n'
    assert _org_scoped_sdk_sites(ast.parse(registry)) == {}, (
        "a string-literal namespace is the registry/base lane, not an org graph")
    org = 'def f(org_id):\n    return _make_sdk(namespace=org_id)\n'
    assert _org_scoped_sdk_sites(ast.parse(org)), (
        "the control: a non-literal namespace IS an org-scoped site")


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


@pytest.mark.parametrize("opener", sorted(_UNBOUND_OPENERS))
def test_every_unbound_opener_is_flagged_on_the_handle_it_returns(opener):
    """#10180: the canonical opener must not be a blind spot in this scan.

    `_org_sdk` is what the whole fleet is migrating TO, and it installs no
    tenant (it returns `_make_sdk(...)`; the org's default namespace is not a
    record tenant). `_handle_state` used to answer `None` for all three
    `_org_sdk*` spellings — "not an SDK handle at all" — so `handles.get(name)
    is False` never held and a lane could write through one with the scan
    silent. That is the false PASS this test exists to prevent, and it is
    parametrised over the SET so a fourth opener added there cannot arrive
    uncaught.

    Mutation this reddens on: dropping any `_UNBOUND_OPENERS` member from
    `_handle_state` (or restoring the `== "_open_org_graph_sdk"` spelling).
    """
    source = f'''
def _run_new_lane(org_id: str) -> None:
    sdk = {opener}(org_id)
    try:
        sdk.create_point("Subject", {{}})
    finally:
        sdk.close()
'''
    flagged = _flagged(source)
    assert flagged and "_run_new_lane" in "".join(flagged), (
        f"a lane writing records through an unbound `{opener}()` handle must be "
        "flagged — the scan cannot treat the default-graph resolver as a "
        "non-handle")


def test_an_org_sdk_lane_that_binds_is_clean():
    """The control for the legs above: the SAME opener, bound, is silent.

    Without this, the parametrised cases above could be flagging any `_org_sdk`
    write at all — which would red every converted lane in the file and say
    nothing about the tenant.
    """
    source = '''
def _run_bound_org_lane(org_id: str) -> None:
    sdk = _org_sdk(org_id)
    _bind_default_graph_record_tenant(sdk, org_id)
    try:
        sdk.create_point("Subject", {})
    finally:
        sdk.close()
'''
    assert _flagged(source) == []


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
