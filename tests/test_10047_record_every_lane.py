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
    # #4051: the org-limit gate's SDK (`graph_name=_data_graph_name(org)` or
    # `_org_sdk_for_credential(org)`). Listed here SO THAT its call sites count
    # as sites — the site walker's `unconditional` set is `_SDK_OPENERS +
    # _INSTALLING_OPENERS`, so an opener declared only in `_UNBOUND_OPENERS`
    # would be judged as a handle with its own construction uncounted.
    "_gate_sdk_for",
})
#: The openers that install a record tenant on the SDK they return.
#:
#: `_data_sdk_with_projection` (#10602) is `_data_sdk` PLUS the projection
#: attach: it opens through `_data_sdk` (`sdk = _data_sdk(org)`), and `_data_sdk`
#: binds this request's record tenant before returning
#: (`_bind_record_tenant(sdk, org)`), so it installs before returning exactly
#: like its two neighbours. It arrived on `origin/main` AFTER this branch's base
#: and the classification leg below went RED on the MERGE tree while staying
#: green on the branch — the leg doing precisely its job (#10723 review F1).
_INSTALLING_OPENERS = frozenset({"_data_sdk", "_data_sdk_offloaded",
                                 "_data_sdk_with_projection"})
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
    # #4051: the org-limit gate's own SDK — `_make_sdk(graph_name=…)` for a
    # graph-bound key, `_org_sdk_for_credential(org)` for an org-wide one, and
    # `None` for a registry-scoped or unresolved binding. It installs nothing,
    # so it is the UNBOUND class: today it is only ever COUNTED through (so
    # this scan reports 0 writes through it), and a future write through it
    # must not be looked past. Also landed on `origin/main` after this branch's
    # base (#10723 review F1).
    # ⚠️ This member is ALSO in `_SDK_OPENERS`, and that is not redundant:
    # `_org_scoped_sdk_sites` builds its site set from `_SDK_OPENERS` +
    # `_INSTALLING_OPENERS`, so a member here alone would be judged as a HANDLE
    # while its own call site was never counted as a construction site.
    "_gate_sdk_for",
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
    # #4051: a PASS-THROUGH, not an opener — it returns the `sdk` it was GIVEN,
    # or `sdk_factory()`'s return, and constructs nothing itself. Same class as
    # `mine_corpus_with_sdk`: the handle it hands back was classified at its own
    # construction site (`_gate_sdk_for`, above). Also on `origin/main` only
    # (#10723 review F1).
    "_resolve_count_sdk",
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

    A STRING-literal namespace is the registry lane (`namespace="registry"` and
    the other literal spellings PROD resolves), never an org's graph; every
    other spelling (`org_id`, `org["org_id"]`, `namespace`) is org-scoped, and
    `graph_name=` is by definition a tenant graph (registry graphs are opened by
    namespace).

    `namespace=None` is COUNTED, deliberately (#10046 review F3). It reads like
    the probe/anchor surface, and two shipped constructions do pass it
    (`hosted_api._probe_sdk`, `_data_plane_db`), but a `namespace=None` handle
    writes to the DEFAULT graph and carries no tenant either — so the
    fail-closed answer is "org-scoped too". `_handle_state` reaches this same
    predicate, so answering "not an org graph" here would ALSO answer `None`
    ("not an SDK handle at all") THERE and make a write through such a handle
    invisible, which is the one direction this scan must never take. The census
    therefore counts the two probe constructions among the package's org-scoped
    sites (the measured breakdown lives in `_org_scoped_sdk_sites`, dated to the
    tree it was measured on), and both halves
    are pinned by `test_a_literal_namespace_is_not_an_org_scoped_site`.

    An ABSENT keyword is the same construction (#10723 review F2):
    `hosted_api._make_sdk` is `def _make_sdk(*, namespace: str | None = None,
    graph_name: str | None = None)`, so `_make_sdk()` IS `namespace=None` at
    runtime — the org's DEFAULT graph with no tenant. It was the one spelling
    that reached the OPPOSITE answer on BOTH halves (not a site, and
    `_handle_state` → `None` → the write looked past), because
    `kwargs.get("namespace")` is Python `None` for an omitted keyword just as it
    is for an explicit `None`. `_make_sdk(**opts)` folds in here too (`**`
    has no `arg`), which is the same fail-closed direction. No site count moves:
    the shipped package has no bare or `**`-spread `_make_sdk` call.
    """
    kwargs = {k.arg: k.value for k in call.keywords}
    if "namespace" not in kwargs and "graph_name" not in kwargs:
        # `_make_sdk()` / `_make_sdk(**opts)`: namespace=None at runtime.
        return True
    namespace = kwargs.get("namespace")
    if "graph_name" in kwargs:
        return True
    return namespace is not None and not (
        isinstance(namespace, ast.Constant) and isinstance(namespace.value, str))


def _operand_calls(value) -> list:
    """The call expressions a value is made of, flattening `or`/`if else`/`await`.

    `await` is unwrapped for the same reason `or` and `if else` are (#10723
    review F1): it is a TRANSPARENT wrapper around the call, so leaving it on
    made `_handle_state` answer `None` — "not an SDK handle at all" — for
    `sdk = await _data_sdk_offloaded(org)`. That is a fail-OPEN on an awaited
    `_UNBOUND_OPENERS` member (the write looks past the handle and the scan is
    GREEN, the false PASS this file exists to prevent) and a FALSE POSITIVE on
    the documented fix when the rebind is spelled with the async twin. `await`
    is not a hypothetical spelling here: `hosted_api.py` assigns
    `sdk = await _data_sdk_offloaded(org)` at 23 sites.
    """
    if isinstance(value, ast.Await):
        return _operand_calls(value.value)
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


def _assigned_handles(node) -> list[tuple[str, ast.expr]]:
    """Every `(handle key, value)` a statement or binding introduces.

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
    indirect shape like a lambda or a factory, which are declared out. EVERY item
    is returned (#10046 review round 7 P2): a `with _lock() as lk,
    _make_sdk(...) as sdk:` used to stop at the first binding, so the SDK in the
    second item was never a handle and the write inside was silent.
    """
    if isinstance(node, ast.With):
        pairs: list[tuple[str, ast.expr]] = []
        for item in node.items:
            target = item.optional_vars
            if isinstance(target, ast.Name):
                pairs.append((target.id, item.context_expr))
            elif isinstance(target, ast.Attribute):
                pairs.append((ast.unparse(target), item.context_expr))
        return pairs
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return [(target.id, node.value)]
        if isinstance(target, ast.Attribute):
            return [(ast.unparse(target), node.value)]
    if (isinstance(node, ast.AnnAssign)
            and isinstance(node.target, (ast.Name, ast.Attribute))
            and node.value is not None):
        target = node.target
        return [((target.id if isinstance(target, ast.Name)
                  else ast.unparse(target)), node.value)]
    return []


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
                for key, events in _sdk_handles(node).items():
                    if "." not in key:
                        continue
                    # The state the method LEAVES BEHIND — the fold is ordered
                    # within a method, so its own rebind is honoured here too.
                    merged[key] = merged.get(key, True) and bool(
                        _handle_state_at(events, _END_OF_LANE))
        if merged:
            by_class[cls.name] = merged
    return by_class


#: A sort key past every statement in a lane: asks a handle's events for the
#: state at the END of the function (the class-attribute union needs the state a
#: method LEAVES BEHIND, not the state at a particular call).
_END_OF_LANE = (1 << 30, 1 << 30)


def _parent_map(node) -> dict[int, object]:
    """`{id(child): parent}` for one lane, so a statement's nesting is readable.

    `ast.walk` hands out nodes without their parents, and the fold below needs
    to know whether a definition sits in the lane's straight line or inside a
    branch that may be skipped (#10046 review F1/F2).
    """
    parents: dict[int, object] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _binds_as_parameter(scope, key: str) -> bool:
    """Is `key` a PARAMETER of `scope` — i.e. handed in, not resolved here?

    The distinction `state_at` rests on (#10723 review P1). A handle spelled in
    a callee's signature is a value the caller supplied; its state cannot be
    read from the callee, which is the declared indirection `_dream_worker`
    needs and the reason events are grouped per scope at all. A handle the
    callee merely CLOSES OVER is not that: it is a local of an enclosing
    function, statically resolvable one AST hop out.
    """
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    args = scope.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                args.vararg, args.kwarg):
        if arg is not None and arg.arg == key:
            return True
    return False


def _binds_locally(scope, key: str) -> bool:
    """Does `scope` BIND `key` itself — by any statement, resolvable or not?

    PEP 227's escape clause, which the outward walk must respect (#10723 review
    round 3 P2, shape ii): *"names bound in B are visible in A unless A contains
    a name binding that hides the binding in B"*. A nested `def` that assigns
    `sdk = _safe_handle()` holds a DIFFERENT variable from the enclosing lane's
    `sdk`, so folding the enclosing definition onto its write is a false
    positive — it tells the reader to install a tenant on a handle the write
    never touches.

    `_binds_as_parameter` covers the handed-in half; this covers the
    re-bound half. Only the BINDINGS that make the name local are tested, not
    resolvability: `_handle_state` answers `None` for `_safe_handle()`, so an
    unresolvable rebind records no event and the outward walk would otherwise
    see "no local event" and resolve the enclosing name. A name that is not
    bound here at all (the genuine closure capture) still resolves outward.

    ⚠️ A target that is NOT an identifier is a USE of the name, never a binding
    of it (#10723 review at `bb2dde007`, P2 fail-open). CPython's execution
    model lists what binds a name as *"targets that are **identifiers** if
    occurring in an assignment … after `as` in a `with` statement"*, so
    `sdk.timeout = 5`, `seen[sdk] = 1` and `sdk.calls += 1` all only *read*
    `sdk` — they cannot make it local. Walking the target and accepting any
    `ast.Name` it contains read those as bindings, so a nested writer that
    merely set an attribute on the enclosing UNBOUND handle was answered
    `shadowed` → `inherited` (`None` = silent), while deleting the attribute
    line flagged it. Measured at `bb2dde007`: `_flagged` returned `[]` for the
    attribute and subscript spellings and `['_lane:5 writes through the unbound
    SDK `sdk`']` for the control. The `Store` requirement below is the fix; the
    genuine shadows (`sdk = …`, `with … as sdk:`) are `Store` and stay silent.

    ⚠️ The stop reads the bindings of THIS BLOCK only — `_nodes_of_one_scope`
    stops at a nested `def` AND at a nested `class`, because a class body is a
    block of its own (a `class C: sdk = …` binds `C.sdk`, never the writing
    function's `sdk`). Descending into it made this answer True and silenced a
    write through the enclosing UNBOUND handle that `origin/main` flags — a
    false PASS, the direction this file must never fail in.

    ⚠️ Treating the shadow as authoritative for the WHOLE scope is Python's own
    rule, not a simplification: a binding anywhere in a function makes the name
    LOCAL for the whole function, so a read of it BEFORE the binding raises
    `UnboundLocalError` at runtime — it can never be a silently-unbound third
    path, which is why this stop cannot introduce a fail-open.
    """
    for sub in _nodes_of_one_scope(scope):
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, (ast.AnnAssign, ast.AugAssign, ast.For,
                             ast.AsyncFor)):
            targets = [sub.target]
        elif isinstance(sub, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in sub.items
                       if item.optional_vars is not None]
        for target in targets:
            for node in ast.walk(target):
                # `Store` only: `ast.walk` also reaches the `Load` names inside
                # an attribute or subscript target, which are uses (see the
                # docstring). A tuple/list target carries `Store` on each of its
                # `Name`s, so unpacking still binds.
                if (isinstance(node, ast.Name) and node.id == key
                        and isinstance(node.ctx, ast.Store)):
                    return True
    return False


def _nodes_of_one_scope(scope) -> list:
    """Every node of `scope` WITHOUT descending into a nested code BLOCK.

    A nested `def`'s bindings are its OWN locals: descending into them would read
    a sibling function's `sdk` as this function's, which is the fail-open the
    bounded walk exists to avoid.

    A nested `class` body is a block of its own for exactly the same reason, and
    it is the #10723 review F1 regression: the CPython execution model defines a
    block as "a module, a function body, and a class definition", and "if a name
    is bound in a block, it is a local variable of that block" — so `class C:
    sdk = …` binds `C.sdk` and says NOTHING about the enclosing function, which
    keeps resolving its own `sdk`. Reading it as a binding of the writing
    function made `_binds_locally` answer True, took the `shadowed` branch, and
    answered `inherited` (`None` = silent) for a write through the lane's UNBOUND
    handle that `origin/main` flags. A false PASS, which this file's contract
    forbids even where a false positive is tolerated.

    A nested class is skipped even when `scope` IS a class: a class body's own
    bindings are read when the scan judges that class's block (the write's
    `_enclosing_scope` is the class itself then), never through a class nested
    inside it.
    """
    inner_blocks: tuple = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    if not isinstance(scope, ast.ClassDef):
        inner_blocks += (ast.ClassDef,)
    found: list = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, inner_blocks):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _position_of(node) -> tuple[int, int]:
    """A node's `(lineno, col_offset)` — its ORDER inside its own scope.

    Used to ask what an enclosing function's handle looked like AT the moment
    the nested `def` was created (#10723 review round 2 P2). Folding that
    enclosing scope to its END instead was wrong: an install **after** the nested
    def then erased the earlier unbound definition and covered a write it never
    reached — the same "an install arms only the writes after it" rule this file
    already pins in the flat spelling. And starting the nested fold from
    `inherited` rather than from this state let an install inside the nested body
    arm a write that had already run.

    ⚠️ The def-position alone is NOT the whole answer (#10723 review round 3 P2):
    a closure executes at CALL time, so a handle opened AFTER its `def` is the one
    the closure actually sees. `def _w(): sdk.create_point(...)` followed by
    `sdk = _make_sdk(namespace=org_id)` is ordinary ordering, and folding only to
    the def position went silently green on it — a regression against
    `origin/main` and against round 1. `_AFTER_SCOPE` is how the enclosing
    scope's END is read, so an unbound definition on EITHER side of the def wins.
    """
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


# Read an enclosing scope to its END rather than to a position — the second half
# of the worst-wins rule above. Only an UNBOUND answer at the end changes the
# result, so this can over-report a cross-scope install and can never hide one.
_AFTER_SCOPE = (float("inf"), float("inf"))


def _enclosing_scope(node, parents, lane) -> object:
    """The innermost `def`/`class` around `node` inside `lane` (else `lane`).

    Events are grouped by the function that EXECUTES them: a nested `def` is its
    own scope, not the parent's straight line (see `_handle_events_by_scope`).
    """
    child = node
    parent = parents.get(id(child))
    while parent is not None and parent is not lane:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            return parent
        child, parent = parent, parents.get(id(parent))
    return lane


def _is_conditional(node, parents, scope) -> bool:
    """Does `node` sit inside a branch of `scope` that may NOT execute?

    Conditional = under an `if`/`for`/`while`, an `except` handler, a `match`
    arm, or a `try`'s `orelse`. Straight-line for this purpose = the function's
    own body, a `try`/`with` body, and a `finally` — those run on every path that
    reaches a later write. The walk stops at `scope`, so a nested `def` does not
    make the code INSIDE it conditional; `_enclosing_scope` already gave it a
    scope of its own.

    `match`/`case` is here for the same reason `if` is (#10723 review P2): a
    `case` body runs only when that pattern matches, so a rebind inside one is a
    branch, not a kill. Leaving it out read such a rebind as UNCONDITIONAL — the
    shape `_handle_state_at` treats as "the earlier definition is dead" — and
    measured a false GREEN: `sdk = _make_sdk(namespace=org_id)` followed by
    `match …: case "a": sdk = _data_sdk(_org(org_id))` then a write answered
    `[]`, where the `if` spelling of the same code correctly flags it (the arm
    may not run, so the unbound handle is still live). The package has 0
    `match` statements today, so this is latent — and it is the fail-OPEN
    direction, which is why it is modelled rather than declared out.
    """
    child = node
    parent = parents.get(id(child))
    while parent is not None and parent is not scope:
        if isinstance(parent, (ast.If, ast.For, ast.AsyncFor, ast.While,
                               ast.ExceptHandler, ast.Match, ast.match_case)):
            return True
        if isinstance(parent, ast.Try) and any(
                child is statement for statement in parent.orelse):
            return True
        child, parent = parent, parents.get(id(parent))
    return False


def _handle_state_at(events, position, initial=None) -> bool | None:
    """The handle's state AT `position`, folded from its ordered events.

    This is the ORDERED half of the container (#10046 review F1, F2). A write is
    judged by the definitions that REACH it, not by the whole lane:

    - an UNCONDITIONAL definition KILLS the earlier one, so a rebind to a bound
      opener before the write clears the handle (`_rebind_local`), and
    - an INSTALL arms only the writes AFTER it, so a lane whose install sits in
      a `finally` no longer retroactively covers the write in its `try` — the
      `no_tenant` DECLINE this file exists to prevent.

    A CONDITIONAL definition is worst-wins, because the branch may not run: that
    is the shape `if not sdk: sdk = _data_sdk(org)` protects (an unbound operand
    keeps winning), and treating it as a kill would invert it.
    `initial` is the state a class INHERITS before its own first event.
    """
    state = initial
    for event_position, event_state, conditional in events:
        if event_position > position:
            break
        if event_state is False:
            state = False
        elif conditional:
            state = state if state is not None else True
        else:
            state = True
    return state


def _handle_events_by_scope(node, parents) -> dict[int, dict[str, list[tuple[tuple[int, int], bool, bool]]]]:
    """`{scope id: {SDK handle: [(position, tenant installed?, conditional?)]}}`.

    Events are grouped by the function that EXECUTES them — `node` itself and
    every `def` nested in it — because a position is only an execution order
    WITHIN one function, and a handle is only the same variable within one.
    `_dream_worker` is the shipped shape that needs it: `_open_sdk` opens and
    arms the SDK, `_drain` writes it, and the write's handle is handed in by a
    callback factory (a declared out-of-scan indirection) rather than bound in
    the writing function — judging that write lane-wide said "bound" on the
    strength of a definition in a different function (#10046 review F1/F2).
    """
    handles: dict[int, dict[str, list[tuple[tuple[int, int], bool, bool]]]] = {}

    def record(owner, key: str, state: bool) -> None:
        scope = _enclosing_scope(owner, parents, node)
        handles.setdefault(id(scope), {}).setdefault(key, []).append((
            (getattr(owner, "lineno", 0), getattr(owner, "col_offset", 0)),
            state, _is_conditional(owner, parents, scope)))

    for sub in ast.walk(node):
        for name, value in _assigned_handles(sub):
            if name is None:
                continue
            state = _handle_state(value)
            if state is None:
                continue
            # WORST-WINS across assignments (#10046 review round 6 P2) is kept
            # for a CONDITIONAL assignment only, because the branch may not run:
            # `sdk = _make_sdk(org)` followed by `if not sdk: sdk =
            # _data_sdk(org)` must not answer bound while an unbound path is
            # still live — inverting the "an unbound operand wins" rule
            # `_handle_state` already applies WITHIN one value. An
            # UNCONDITIONAL rebind KILLS the earlier definition instead, and an
            # INSTALL arms only the writes after it: a lane that already bound
            # its handle must not be reported unbound, and an install in a
            # `finally` must not retroactively cover the write in its `try`
            # (#10046 review F1/F2).
            record(sub, name, state)
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        called = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
        if called not in _TENANT_INSTALLERS:
            continue
        # Key on the SAME spelling `_assigned_handles` produces, or an install on
        # an SDK held on `self` cannot mark it bound (#10046 review round 6 P1):
        # `_bind_default_graph_record_tenant(self._sdk, org)` has an ATTRIBUTE in
        # `args[0]`, and the method form has one as `sub.func.value` — reading
        # only `ast.Name` silently marked `org` bound instead and left the handle
        # False, so a developer who followed this test's own failure message
        # ("Install one at the construction site: `_bind_default_graph_record_
        # tenant(sdk, org_id)`") stayed RED.
        #
        # KEYWORD arguments count too (#10046 review round 7 P2): `…(sdk=sdk,
        # org_id=org_id)` installs the tenant just as much as the positional
        # spelling, and reading only `sub.args` reported that lane RED — the same
        # false-positive family as the line above, one spelling over.
        for arg in (*sub.args, *(kw.value for kw in sub.keywords)):
            if isinstance(arg, (ast.Name, ast.Attribute)):
                key = arg.id if isinstance(arg, ast.Name) else ast.unparse(arg)
                record(sub, key, True)
        if isinstance(sub.func, ast.Attribute) and isinstance(
                sub.func.value, (ast.Name, ast.Attribute)):
            receiver = sub.func.value
            record(sub, receiver.id if isinstance(receiver, ast.Name)
                   else ast.unparse(receiver), True)
    for by_key in handles.values():
        for events in by_key.values():
            events.sort(key=lambda event: event[0])
    return handles


def _sdk_handles(node) -> dict[str, list[tuple[tuple[int, int], bool, bool]]]:
    """`{SDK handle: [ordered events]}` for ONE function body (`node`).

    Identity, not name presence (#10380 review P2.2): the answer belongs to the
    HANDLE, so an install on one handle says nothing about another in the same
    function. An install is found in any of its three spellings —
    `_bind_default_graph_record_tenant(sdk, org_id)`, `_bind_record_tenant(sdk,
    org)`, or the method form `sdk._bind_record_tenant(...)` — and arms THAT
    handle FROM THAT POSITION ON (#10046 review F1: the map is ordered events
    rather than one lane-wide boolean, so an install cannot reach backwards).
    Callers that judge WRITES use `_handle_events_by_scope`, which also has the
    nested functions.
    """
    return _handle_events_by_scope(node, _parent_map(node)).get(id(node), {})


def _org_scoped_sdk_sites(tree) -> dict[str, int]:
    """`{lane: count}` for every NON-AMBIENT org-scoped SDK construction site.

    ⚠️ "Non-ambient" is load-bearing, and this docstring said "org-scoped"
    without it until #10723's review (P3) — the count and the claim were not the
    same thing. `unconditional` is `(_SDK_OPENERS - {"_make_sdk"}) |
    _INSTALLING_OPENERS`, so a member of `_AMBIENT_OPENERS` is NOT counted:
    `_get_org_sdk` is the package's second org-scoped opener and it is excluded
    here even though this same file's `_AMBIENT_OPENERS` comment states it "DO
    return a real org-scoped SDK a lane writes through". The exclusion is
    correct — its tenant is the request's AMBIENT scope, which no AST read can
    observe, so there is nothing here to measure — but the CONSEQUENCE is that
    `_ORG_SCOPED_MODULES` never lists `mcp_server.py` (109 `_get_org_sdk` call
    sites, the package's largest record-write surface) or `mcp_auth.py` (its
    definition). Read this as "how many sites can this scan actually judge?",
    never as a census of every org-scoped construction in the package.

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
    registry lanes alone. Corrected, the package has 88 org-scoped sites across
    THREE modules, measured on the tree #10723 merges into (`origin/main`
    `e4ee966f4`, 264 modules): `hosted_api.py` 85 (two of which are the
    `namespace=None` probe constructions the predicate COUNTS deliberately),
    `quota.py` 1 and `supabase_control.py` 2 (the module-qualified spelling).
    ⚠️ The number is DATED on purpose. Two earlier revisions of this line said
    "81 across two modules" and then "83 across three": each was true of the
    predicate on the day it was written and false at the next merge, because
    `origin/main` keeps adding openers (#10602's `_data_sdk_with_projection`,
    #4051's `_gate_sdk_for` — both counted here by DECLARATION, which is why the
    vocabulary is what moves this number). A bare number that outlives its tree
    is the claim-vs-code defect this file has been sent back for four times
    (#10046 review F3, #10723 review F3), so quote the snapshot or quote the
    breakdown, never a bare figure.
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
    # Handles are read PER SCOPE — the lane's own body plus each nested `def` — so
    # a definition is never allowed to arm a write in a different function, and
    # a position is only used as an order within the function it belongs to.
    parents_by_lane = {name: _parent_map(node)
                       for name, node in functions.items()}
    scopes_by_lane = {name: _handle_events_by_scope(node, parents_by_lane[name])
                      for name, node in functions.items()}
    # An SDK a CLASS owns (`self._own_sdk`) is opened in one method and written in
    # another, so the per-lane map cannot see it. The class's merged state is what
    # a lane INHERITS before its own first event: a method that only WRITES the
    # attribute is judged by what `__init__` left behind (a worker class holding
    # its handle on `self` was green — #10046 review round 5 P1), while a method
    # that BINDS the attribute itself is judged by its own binding at the write.
    # The old `and`-merge re-imposed `__init__`'s unbound state over the writer's
    # own correct binding, telling a developer to install a tenant the lane
    # already had (#10046 review F2b — the guard rejecting its own documented
    # fix, the same false-positive family as rounds 6-7).
    class_attrs = _class_attribute_handles(tree)
    # The CALLEE's parameter list maps a positional argument to the parameter
    # the helper writes through — not the caller's.
    callee_params = {name: [a.arg for a in
                            (*node.args.posonlyargs, *node.args.args)]
                     for name, node in functions.items()}
    handed = _writers_over_a_parameter(tree)
    found: list[str] = []
    for name, node in functions.items():
        parents = parents_by_lane[name]
        scopes = scopes_by_lane[name]
        owner = name.split(".", 1)[0]
        inherited = class_attrs.get(owner, {}) if "." in name else {}

        def state_at(key: str, call, scopes=scopes, parents=parents, lane=node,
                     inherited=inherited) -> bool | None:
            """The state of `key` AT this call — the ordered answer (#10046 F1/F2).

            The loop variables are bound as DEFAULTS on purpose: the callables are
            used within this iteration only, and binding them keeps the closure
            from reading a later lane's maps if that ever changes (ruff B023).

            When the writing scope has NO event for `key`, two different shapes
            reach here and they must not be answered the same way (#10723 review
            P1). A PARAMETER is handed in — the state is not knowable here, so
            `inherited` answers and the walk stops. A CLOSURE CAPTURE of an
            enclosing function's local IS resolvable, and answering `inherited`
            for it was a silent fail-open: a nested `def` writing through the
            enclosing lane's unbound `sdk` was RED on `origin/main` and green
            after the scoped fold, with neither direction pinned.
            """
            scope = _enclosing_scope(call, parents, lane)
            # The state a nested scope STARTS from, composed over the WHOLE
            # ancestor chain (#10723 review rounds 1, 2 and 3 — each round found
            # one half of this rule):
            #   · a PARAMETER, here or in any enclosing function, is a value the
            #     caller supplied, so its state is not knowable — `inherited`
            #     answers and the walk stops;
            #   · otherwise each ancestor contributes WORST-WINS: an unbound
            #     answer at the nested def's position OR at that scope's END wins
            #     (a closure runs at call time, so a handle opened after the def
            #     is the one it sees), and a bound answer is taken only from the
            #     def position;
            #   · the walk does NOT stop at the first ancestor that has an event,
            #     because a too-late event in a middle scope would otherwise
            #     reset the answer instead of inheriting the outer binding.
            initial = inherited.get(key)
            # A nested scope that BINDS the name itself holds a DIFFERENT
            # variable, so no enclosing definition reaches its write (PEP 227's
            # "unless A contains a name binding that hides the binding in B").
            # `_handle_state` answers `None` for an unresolvable value, so such a
            # rebind records no event and the walk below would otherwise resolve
            # the enclosing name — reporting a lane that shadows its handle with
            # a SAFE one as unbound (#10723 review round 3 P2, shape ii).
            # Answering `inherited` (unknown) is the honest state, not "bound".
            shadowed = scope is not lane and _binds_locally(scope, key)
            anchor = scope
            while not shadowed and anchor is not lane:
                if _binds_as_parameter(anchor, key):
                    initial = inherited.get(key)
                    break
                enclosing = _enclosing_scope(anchor, parents, lane)
                if _binds_as_parameter(enclosing, key):
                    initial = inherited.get(key)
                    break
                enclosing_events = scopes.get(id(enclosing), {}).get(key)
                if enclosing_events is not None:
                    at_def = _handle_state_at(
                        enclosing_events, _position_of(anchor), initial)
                    at_end = _handle_state_at(
                        enclosing_events, _AFTER_SCOPE, initial)
                    if at_def is False or at_end is False:
                        initial = False
                    elif at_def is not None:
                        initial = at_def
                anchor = enclosing
            own_events = scopes.get(id(scope), {}).get(key)
            if own_events is None:
                return initial
            return _handle_state_at(
                own_events, (call.lineno, call.col_offset), initial)

        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if (isinstance(call.func, ast.Attribute)
                    and call.func.attr in _RECORD_EMITTING_VERBS):
                receiver = call.func.value
                if (isinstance(receiver, ast.Name)
                        and state_at(receiver.id, call) is False):
                    found.append(f"{name}:{call.lineno} writes through the "
                                 f"unbound SDK `{receiver.id}` "
                                 f"(`{receiver.id}.{call.func.attr}(…)`)")
                elif (isinstance(receiver, ast.Attribute)
                        and state_at(ast.unparse(receiver), call) is False):
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
                if (isinstance(arg, ast.Name)
                        and state_at(arg.id, call) is False):
                    found.append(f"{name}:{call.lineno} hands the unbound SDK "
                                 f"`{arg.id}` to `{callee}`, which writes "
                                 f"records through it")
            for keyword in call.keywords:
                if keyword.arg not in handed[callee]:
                    continue
                if (isinstance(keyword.value, ast.Name)
                        and state_at(keyword.value.id, call) is False):
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
    # `sites` maps LANE → its count, so the floor is asserted on the SITES, the
    # same quantity the package-wide leg uses. `len(sites)` is the number of
    # lanes holding at least one site (65 at `bb2dde007` against 85 sites), so
    # the old message reported a lane count as a site count and the floor would
    # not move while lanes kept one site each (#10723 review P3).
    assert sum(sites.values()) >= 30, (
        "the scan found only "
        f"{sum(sites.values())} org-scoped SDK construction sites across "
        f"{len(sites)} lanes — the AST criteria no "
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

    Measured on the tree this leg was last verified against (`origin/main`
    `e4ee966f4`, 264 modules): 88 org-scoped construction sites across THREE
    modules, and 0 unbound record writes, so widening the scan
    cannot red on the code as it stands. Its whole job is the module that does
    not exist yet. (Two earlier revisions reported different counts, each fixed
    at review: "149 entries / 175 sites" was inflated by a doubled predicate —
    `_make_sdk` is a member of `_SDK_OPENERS`, so the membership test matched
    EVERY `_make_sdk(...)` call and 94 of the 175 were registry/base literals;
    then "81 sites / two modules" still missed `supabase_control.py`, whose two
    lanes open through the MODULE-QUALIFIED spelling `_ha._make_sdk(...)` that a
    bare-name reader cannot see. Both are fixed in `_org_scoped_sdk_sites` /
    `_handle_state`. A third revision said "83" until `origin/main` added #10602's
    `_data_sdk_with_projection` and #4051's `_gate_sdk_for`; the count follows the
    declared vocabulary, so it moves when main adds an opener — see the dated
    note in `_org_scoped_sdk_sites`.)

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
    reached through a lambda or local factory. ORDER is modelled, but only
    WITHIN one function (`_handle_events_by_scope`): a definition reaches a
    write positionally, so an install arms only the writes after it and an
    unconditional rebind kills the earlier definition. A write in a DIFFERENT
    function is judged by that function's own definitions when the handle is
    handed in as a PARAMETER, and by the ENCLOSING function's definitions when
    it is a CLOSURE CAPTURE — resolved at the nested `def`'s position, and again
    at the enclosing scope's end, because a closure runs at CALL time (PEP 227:
    names bound in the enclosing function are visible in the nested one *unless
    the nested one binds the name itself*, which `_binds_locally` honours). The
    trade that buys is one CONSERVATIVE false positive: an install positioned
    between the nested `def` and its CALL is not seen (the fold anchors at the
    def's position), so such a lane is flagged although the install does run
    first — kept because the alternative re-opens the `finally`-install
    fail-open, and it is pinned as the asserted answer rather than left silent.
    The shipped shape that costs is a callback FACTORY, `_run_dream_on_pool(_drain,
    _open_sdk)` in `hosted_api.py`: `_open_sdk` opens and arms the handle,
    `_drain` writes it, and the write's handle is therefore unknown rather than
    assumed bound (the local-factory indirection above, one spelling over).
    Also OUT, and listed so `0 unbound`
    is not read as covering them: an SDK handed to a writing helper as an INLINE
    call in either position (`_write(_org_sdk(org))` / `_write(sdk=_org_sdk(org))`
    — the bound-name forms of both ARE caught); an SDK held on a MODULE-LEVEL
    global and written from a function; a passing alias (`s2 = sdk`); a walrus
    (`if (sdk := _make_sdk(...))`); `setattr(self, "_sdk", ...)`; tuple unpack; a
    handle held in a DICT/LIST and written through a SUBSCRIPT; a class-body
    opener; an install performed in a THIRD method (the class-attribute map merges
    worst-wins, so an install there cannot arm a handle another method opened —
    install in the writer, or in the opener); a CLASS nested inside a FUNCTION
    that holds its handle on `self` (the scan's `_class_attribute_handles` walk
    reads `tree.body`, i.e. the package's module-level classes; the nested class
    is still judged as its own scope when one function both opens and writes);
    and a SUBCLASS writing through a
    handle its base class's `__init__` opened (the class map is keyed per class).
    What IS scanned: methods, an SDK held on `self` (opened in one method, written
    in another, and armed by a positional, keyword or method-form install), the
    ORDER of a handle's definitions within a function (an install arms only the
    writes after it; an unconditional rebind kills the earlier one; a conditional
    one is worst-wins), `with … as sdk` (EVERY item of a multi-item `with`), a
    CLOSURE CAPTURE of an enclosing function's handle (with a nested local that
    SHADOWS the name answered as its own, per PEP 227), an `await`ed opener in
    either direction (`sdk = await _data_sdk_offloaded(org)` is the package's
    ordinary async spelling — 23 sites in `hosted_api.py` — and leaving the
    `ast.Await` wrapper on made it a non-handle, i.e. a fail-open on the unbound
    direction and a false positive on the bound one), and module-qualified
    dispatch (`_ha._make_sdk(...)`).
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

    # The MODULE axis, which a global site floor cannot see: THREE modules
    # contribute sites and `hosted_api.py` holds 85 of the 88 (measured on
    # `origin/main` `e4ee966f4`; the ratio, not the figure, is what matters
    # here), so criteria drift
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
    # matched nothing is not a pass. 60 against a measured 88.
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

    # Non-vacuity for the METHOD half, the analogue of `len(shaped) >= 20`
    # above (#10723 review P3). Without it, a class walk or a name shape that
    # silently stops matching makes `unclassified_methods` empty and this leg
    # passes while certifying nothing — and `_NOT_A_LANE_OPENER_METHODS` is the
    # one vocabulary the `vanished` check deliberately excludes.
    assert shaped_methods, (
        "no SDK-shaped METHODS were found in the package — the class-scoped "
        "walk or `_SDK_NAME_RE` has stopped matching, so "
        "`_NOT_A_LANE_OPENER_METHODS` certifies nothing")
    vanished_methods = sorted(_NOT_A_LANE_OPENER_METHODS - shaped_methods)
    assert vanished_methods == [], (
        "these declared opener METHODS no longer exist as class-scoped "
        "definitions, so the classification is stale and its exemptions are "
        "unchecked:" + _joined(vanished_methods))

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
    `_module_functions`, or the attribute half of `_assigned_handles`.
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


def test_an_install_in_any_spelling_arms_the_handle_it_names():
    """Pins round-7: a KEYWORD install arms the handle, and every `with` item binds.

    Both were false NEGATIVES of the same family one spelling over from the
    round-6 P1. A developer who follows this test's own failure message
    (`_bind_default_graph_record_tenant(sdk, org_id)`) but writes it with keyword
    arguments must not stay RED — and a `with … as lk, _make_sdk(...) as sdk:`
    must not hide the SDK in the second item.
    """
    keyword_local = '''
def _local_kw(org_id: str) -> None:
    sdk = _make_sdk(namespace=org_id)
    _bind_default_graph_record_tenant(sdk=sdk, org_id=org_id)
    sdk.create_point("Subject", {})
'''
    assert _flagged(keyword_local) == [], (
        "a KEYWORD-argument install installs the tenant; reporting it RED tells "
        "the reader to add an install it already has")

    keyword_self = '''
class _KwWorker:
    def __init__(self, org_id: str) -> None:
        self._sdk = _make_sdk(namespace=org_id)
        _bind_default_graph_record_tenant(sdk=self._sdk, org_id=org_id)

    def run(self) -> None:
        self._sdk.create_point("Subject", {})
'''
    assert _flagged(keyword_self) == [], (
        "the same, on a handle the class owns")

    multi_with = '''
def _run_multi(org_id: str) -> None:
    with _lock() as lk, _make_sdk(namespace=org_id) as sdk:
        sdk.create_point("Subject", {})
'''
    assert _flagged(multi_with), (
        "EVERY item of a multi-item `with` binds a handle — stopping at the first "
        "one let a lock-plus-SDK `with` hide the SDK entirely")


def test_an_install_arms_the_writes_THAT_FOLLOW_it_and_no_earlier_ones():
    """#10046 review F1 (fail-open): install ORDERING, not lane-wide presence.

    `_sdk_handles` used to fold ONE lane-wide boolean, so an install ANYWHERE in
    the lane armed every write in it. A lane that installs in its `finally`
    therefore read GREEN while the write in its `try` ran with no tenant — the
    `no_tenant` DECLINE this leg's own failure message exists to prevent ("the
    lane looks healthy and files nothing"). Reaching-definition semantics: an
    install arms the writes AFTER it.

    Mutation this reddens on: reverting the arm to `handles[key] = True` over a
    separate pass (or dropping the position from the event) — `late` goes silent
    again.
    """
    late = '''
def _late_bind(org_id: str) -> None:
    sdk = _make_sdk(namespace=org_id)
    try:
        sdk.create_point("Subject", {})
    finally:
        _bind_default_graph_record_tenant(sdk, org_id)
        sdk.close()
'''
    assert _flagged(late), (
        "an install in `finally` must NOT retroactively arm the write in `try` — "
        "that write runs with no tenant, and an armed deployment refuses it")

    early = '''
def _early_bind(org_id: str) -> None:
    sdk = _make_sdk(namespace=org_id)
    _bind_default_graph_record_tenant(sdk, org_id)
    sdk.create_point("Subject", {})
'''
    assert _flagged(early) == [], (
        "the control: the SAME lane with the install before the write is clean, "
        "so the flag above is ordering rather than a broken harness")


def test_a_match_ARM_is_a_branch_like_an_if_body():
    """#10723 review P2: a `case` body may not run, so its rebind is not a kill.

    `_is_conditional` listed `if`/`for`/`while`/`except` and `try`'s `orelse` but
    not `ast.Match`, so a rebind inside a `case` arm read as UNCONDITIONAL — the
    shape `_handle_state_at` treats as "the earlier definition is dead". The
    direction is worth naming because it is the DANGEROUS one: this test's first
    snippet measured `[]` (GREEN) before the fix, while its `if` twin was RED.
    A conditional rebind to a BOUND opener leaves an unbound handle live on the
    path where the pattern does not match, and only treating the arm as a branch
    says so.

    Mutation this reddens on: dropping `ast.Match`/`ast.match_case` from
    `_is_conditional`'s isinstance tuple.
    """
    match_unbound_live = '''
def _match_rebind(org, mode):
    sdk = _make_sdk(namespace=org["org_id"])
    match mode:
        case "bound":
            sdk = _data_sdk(org)
        case _:
            pass
    sdk.create_point("Subject", {})
'''
    assert _flagged(match_unbound_live), (
        "the arm may not run, so the unbound handle still reaches the write — "
        "reading the `case` body as unconditional killed it and went GREEN")

    match_all_bound = '''
def _match_bound(org, mode):
    sdk = _data_sdk(org)
    match mode:
        case "again":
            sdk = _data_sdk(org)
        case _:
            pass
    sdk.create_point("Subject", {})
'''
    assert _flagged(match_all_bound) == [], (
        "the control: an all-bound lane is clean whichever arm runs — making "
        "`case` a branch must not manufacture a false positive")


def test_a_rebind_to_a_BOUND_opener_before_the_write_clears_the_handle():
    """#10046 review F2 (false positive): the binding in force AT the write wins.

    Worst-wins across a lane's assignments is right for a CONDITIONAL rebind (the
    branch may not run) and wrong for an UNCONDITIONAL one: a lane that opens an
    SDK, REPLACES it with a bound one, then writes was reported unbound — telling
    the reader to "install one at the construction site" on a lane that already
    installs one. The class-attribute half had the same defect one merge further
    out: the class-wide `and` re-imposed `__init__`'s state over the WRITER's own
    binding.

    Mutation this reddens on: replacing the position-aware fold with the old
    `handles[name] = handles.get(name, True) and state` (both rebinds red), or
    restoring the `and`-merge in `_unbound_record_writes` (the class rebind
    reds).
    """
    local_rebind = '''
def _rebind_local(org):
    sdk = _make_sdk(namespace=org["org_id"])
    sdk = _data_sdk(org)
    sdk.create_point("Subject", {})
'''
    assert _flagged(local_rebind) == [], (
        "an unconditional rebind to a BOUND opener before the write clears it")

    class_rebind = '''
class _RebindWorker:
    def __init__(self, org_id: str) -> None:
        self._sdk = _make_sdk(namespace=org_id)

    def run(self, org) -> None:
        self._sdk = _data_sdk(org)
        self._sdk.create_point("Subject", {})
'''
    assert _flagged(class_rebind) == [], (
        "the same, on a handle the class OWNS: the WRITER's own binding must win "
        "over `__init__`'s — the round-5 `and`-merge reported this lane RED")

    conditional_rebind = '''
def _conditional_rebind(org):
    sdk = _make_sdk(namespace=org["org_id"])
    if not sdk:
        sdk = _data_sdk(org)
    sdk.create_point("Subject", {})
'''
    assert _flagged(conditional_rebind), (
        "the control the round-6 worst-wins rule protects: a CONDITIONAL rebind "
        "may not run, so the unbound definition still reaches the write")


def test_a_nested_def_writing_through_the_ENCLOSING_lane_handle_is_caught():
    """P1 (#10723 review): a CLOSURE CAPTURE is not a handed-in parameter.

    `_handle_events_by_scope` groups handle events by the function that
    EXECUTES them, which is right for the callback-factory shape — `_drain`
    receives the handle as a PARAMETER, so its state genuinely is not knowable
    here. A nested `def` writing through the ENCLOSING function's local is not
    that shape: the binding is statically resolvable, one AST hop out. The fold
    made that lane GREEN while it was RED on `origin/main`, and nothing pinned
    either direction — a silent fail-open in the guard that exists to catch
    exactly this.

    Mutation this reddens on: dropping the outward walk in `state_at` (the
    enclosing-scope lookup, or the `_binds_as_parameter` stop) — the closure
    capture goes green with an unbound definition sitting right above it.
    """
    closure = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.create_point("Subject", {})
    _write()
'''
    assert _flagged(closure), (
        "a nested `def` writing through the enclosing lane's unbound SDK must "
        "be flagged: `sdk` is a closure capture whose binding is statically "
        "resolvable, not a handed-in parameter")

    armed_above = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    _bind_default_graph_record_tenant(sdk, org_id)
    def _write():
        sdk.create_point("Subject", {})
    _write()
'''
    assert _flagged(armed_above) == [], (
        "the control: the enclosing lane ARMED the handle, so the closure "
        "capture is covered and must not be reported")

    # #10723 review round 2, P2: the anchor is the nested `def`'s own POSITION,
    # not the end of the enclosing function. An install that happens AFTER the
    # nested def is created does not cover a write the closure makes — the same
    # "an install arms only the writes after it" rule this file already pins in
    # the FLAT spelling. Folding the enclosing scope to its END erased the
    # unbound definition and turned this lane green.
    late_install = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.create_point("Subject", {})
    _write()
    _bind_default_graph_record_tenant(sdk, org_id)
'''
    assert _flagged(late_install), (
        "an install AFTER the nested `def` must not retroactively cover the "
        "closure's write — extracting a write into a local closure would "
        "otherwise turn a red lane green")

    # #10723 review round 2, P2: the nested body's OWN events must fold FROM the
    # captured state, not from `inherited`. Short-circuiting on "this scope has
    # an event" started the fold at `None`, so an install anywhere in the nested
    # body — even one that runs AFTER the write — armed it. That is the classic
    # #10447 shape (install in a `finally` covering the write in its `try`),
    # merely spelled inside a closure.
    install_after = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.create_point("Subject", {})
        _bind_default_graph_record_tenant(sdk, org_id)
    _write()
'''
    assert _flagged(install_after), (
        "an install AFTER the write, inside the same nested `def`, must not "
        "arm the write before it")

    install_in_finally = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        try:
            sdk.create_point("Subject", {})
        finally:
            _bind_default_graph_record_tenant(sdk, org_id)
    _write()
'''
    assert _flagged(install_in_finally), (
        "an install in a `finally` must not retroactively cover the write in "
        "its `try` — the nested spelling of the rule this file already pins")

    # #10723 review round 3, P2 — a REGRESSION round 2 introduced. A closure runs
    # at CALL time, so the def-position alone is not the answer: opening the
    # handle AFTER the `def` is ordinary ordering, and folding only to the def
    # position went green on it. Both directions of the def must be read.
    def_before_binding = '''
def _lane(org_id):
    def _write():
        sdk.create_point("Subject", {})
    sdk = _make_sdk(namespace=org_id)
    _write()
'''
    assert _flagged(def_before_binding), (
        "declaring the writer before opening its handle is ordinary ordering — "
        "the closure still sees the unbound handle at call time")

    rebound_unbound_after_the_def = '''
def _lane(org_id):
    sdk = _data_sdk(org)
    def _write():
        sdk.create_point("Subject", {})
    sdk = _make_sdk(namespace=org_id)
    _write()
'''
    assert _flagged(rebound_unbound_after_the_def), (
        "a rebind to an UNBOUND opener after the def wins: the write reaches "
        "the handle the closure actually sees at call time")

    # #10723 review round 3, P3 — the walk must not stop at the first ancestor
    # that has an event, or a too-late event in a MIDDLE scope resets the answer
    # instead of inheriting the outer binding.
    late_install_one_scope_out = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _mid():
        def _inner():
            sdk.create_point("Subject", {})
        _inner()
        _bind_default_graph_record_tenant(sdk, org_id)
    _mid()
'''
    assert _flagged(late_install_one_scope_out), (
        "an install after the inner write, in the MIDDLE scope, must not clear "
        "the outer unbound definition")

    # The CONSERVATIVE trade the fold buys, stated as a contract rather than
    # left to be discovered (#10723 review round 3, P2 shape i). The fold anchors
    # the nested scope at the `def`'s position, so an install positioned BETWEEN
    # the def and its CALL is not seen — the lane is flagged although the install
    # does run first. Kept, because seeing it would mean folding the enclosing
    # scope to its END, which re-opens the `finally`-install fail-open on the
    # line above. A false POSITIVE is the direction this file may err in; a
    # false PASS is not.
    install_between_def_and_call = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.create_point("Subject", {})
    _bind_default_graph_record_tenant(sdk, org_id)
    _write()
'''
    assert _flagged(install_between_def_and_call), (
        "the asserted CONSERVATIVE answer: an install between the nested def and "
        "its call is not modelled, so the lane reads unbound. If this ever goes "
        "silent, the fold has started reading the enclosing scope to its END — "
        "which is the fail-open `late_install_one_scope_out` pins against")


def test_a_nested_def_writing_through_a_PARAMETER_is_still_silent():
    """The declared indirection the scoped fold exists for, preserved.

    `_dream_worker`'s shape: a callback factory hands the handle IN, so the
    callee's parameter names a value this scan cannot resolve. Walking outward
    for a closure capture must stop at a parameter, or the fold's own fix is
    undone the moment the two shapes are confused.

    ⚠️ The snippet names the SAME handle in the enclosing lane on purpose
    (#10723 review round 2, P3). Without that, a neutralised `_binds_as_parameter`
    found no enclosing events either way and the whole file stayed green — the
    one precondition this fix adds was unpinned by the test claiming to pin it.

    Mutation this reddens on: neutralising `_binds_as_parameter` (return False)
    — the enclosing lane's unbound `_make_sdk` then answers for a handle that
    was passed in, and `_lane:5 writes through the unbound SDK `sdk`` appears.
    """
    parameter = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _drain(sdk):
        sdk.create_point("Subject", {})
    _drain(_open_sdk(org_id))
'''
    assert _flagged(parameter) == [], (
        "a helper writing through a PARAMETER is the declared indirection — "
        "the outward walk must stop there rather than resolving the "
        "enclosing lane's same-named binding")

    parameter_two_hops = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _outer(sdk):
        def _inner():
            sdk.create_point("Subject", {})
        _inner()
    _outer(_open_sdk(org_id))
'''
    assert _flagged(parameter_two_hops) == [], (
        "the stop must fire on a parameter of an ENCLOSING function, two hops "
        "out, not only on the writing scope's own")


def test_a_nested_def_that_SHADOWS_the_enclosing_handle_is_silent():
    """PEP 227's escape clause: a local binding HIDES the enclosing one.

    #10723 review round 3 (P2, shape ii). The outward walk resolves a closure
    capture against the enclosing function, which is right only while the nested
    scope does NOT bind the name itself: *"names bound in B are visible in A
    unless A contains a name binding that hides the binding in B"*. A nested
    `sdk = _safe_handle()` holds a DIFFERENT variable — but `_handle_state`
    answers `None` for an unresolvable opener, so the rebind records NO event and
    the walk saw "no local event" and resolved the enclosing unbound name,
    reporting a lane that had already replaced its handle with a safe one. That
    is the round-6/7 false-positive family (“the guard rejected its own
    documented fix”) one scope out.

    Mutation this reddens on: deleting the `_binds_locally` stop in `state_at`
    (the enclosing `_make_sdk` then answers for the shadowed write), or widening
    `_binds_locally` to descend into a nested `def`'s body (the `deeper` case
    below then stops the walk on a SIBLING's binding — a fail-open).
    """
    shadowed = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk = _safe_handle()
        sdk.create_point("Subject", {})
    _write()
'''
    assert _flagged(shadowed) == [], (
        "a nested local that REPLACES the handle is a different variable — the "
        "enclosing unbound definition does not reach its write")

    shadowed_by_a_with_binding = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        with _safe_handle() as sdk:
            sdk.create_point("Subject", {})
    _write()
'''
    assert _flagged(shadowed_by_a_with_binding) == [], (
        "a `with … as sdk` binds the name too, so it hides the outer one")

    deeper = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        def _unrelated():
            sdk = _safe_handle()
            return sdk
        sdk.create_point("Subject", {})
    _write()
'''
    assert _flagged(deeper), (
        "the shadowing stop reads THIS scope's bindings only — a nested `def`'s "
        "local must not silence the write one scope out, which is the "
        "fail-open a plain `ast.walk(scope)` would introduce")


def test_a_USE_of_the_handle_inside_the_writer_is_not_a_shadow():
    """Setting an ATTRIBUTE on the handle reads it — it does not bind the name.

    #10723 review at `bb2dde007`, P2 (fail-open, the direction this file must
    never fail in). `_binds_locally` walked the assignment TARGET and accepted
    any `ast.Name` it met, so the `Load` names inside an attribute or subscript
    target counted as bindings. CPython's execution model (§4.2.1, the primary
    source for this) says only *"targets that are identifiers"* bind a name, so
    `sdk.timeout = 5` is a USE of `sdk`. Reading it as a binding made
    `state_at` take the `shadowed` branch and answer `inherited` (`None` =
    silent) for a write through the enclosing lane's UNBOUND handle.

    Measured at `bb2dde007` with this file's own `_flagged`: the attribute case
    and the subscript case returned `[]`, the augmented form returned `[]`, and
    deleting the attribute line from the same snippet returned
    `['_lane:5 writes through the unbound SDK `sdk` …']` — the flag existed and
    the use-only line erased it. Post-fix all three red, so the pin fails on the
    pre-fix code.

    Mutation this reddens on: dropping `isinstance(node.ctx, ast.Store)` from
    `_binds_locally` (back to a plain `ast.walk` over the target).
    """
    control = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.create_point("Subject", {})
    _write()
'''
    attribute_use = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.timeout = 5
        sdk.create_point("Subject", {})
    _write()
'''
    subscript_use = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        seen[sdk] = 1
        sdk.create_point("Subject", {})
    _write()
'''
    augmented_attribute = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk.calls += 1
        sdk.create_point("Subject", {})
    _write()
'''
    control_flag = _flagged(control)
    assert control_flag, (
        "the write goes through the enclosing lane's UNBOUND handle — the "
        "control case, which must stay RED")

    def _shape(flags: list[str]) -> list[str]:
        """The flag with its line number dropped — the variants shift lines."""
        return [re.sub(r":\d+ ", ":<line> ", f) for f in flags]

    for label, source in (("attribute", attribute_use),
                          ("subscript", subscript_use),
                          ("augmented attribute", augmented_attribute)):
        assert _shape(_flagged(source)) == _shape(control_flag), (
            f"a {label} assignment on the handle is a USE — it does not bind "
            "the name, so it must not hide the enclosing unbound definition "
            "(#10723 review: a false PASS where the same snippet without that "
            "line is RED)")

    # The `Store` requirement must not cost a genuine binding: an unpacking
    # target carries `Store` on each of its names, so this one still shadows.
    unpacked_shadow = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        sdk, other = _safe_handle(), 1
        sdk.create_point("Subject", {})
    _write()
'''
    assert _flagged(unpacked_shadow) == [], (
        "an unpacking target BINDS each name it names, so the nested scope "
        "holds a different variable and the outer definition does not reach "
        "its write")


def test_a_nested_CLASS_BODY_binding_is_not_a_binding_of_the_writing_function():
    """A class body is its own BLOCK — `C.sdk` is not the writing function's `sdk`.

    #10723 review F1 (P2, false PASS, regression against `origin/main`).
    `_nodes_of_one_scope` stopped at a nested `def` but not at a nested `class`,
    so `_binds_locally` read a class-body assignment as a binding of the writing
    function. `state_at` then took the `shadowed` branch and answered
    `inherited` (`None` = silent) for a write through the lane's UNBOUND handle —
    a lane that `origin/main` flags. CPython's execution model is explicit: a
    block is "a module, a function body, and a class definition", and "if a name
    is bound in a block, it is a local variable of that block".

    Both directions are asserted so the pin is non-vacuous: renaming the class
    attribute to a different name must not change the answer (it never did), and
    the class-body spelling must agree with it.

    Mutation this reddens on: dropping the `ast.ClassDef` stop from
    `_nodes_of_one_scope` (the `class C: sdk = …` case goes silent again).
    """
    class_body_binds_the_handle = '''
def _lane(org_id):
    sdk = _make_sdk(namespace=org_id)
    def _write():
        class C:
            sdk = _safe_handle()
        sdk.create_point("Subject", {})
    _write()
'''
    class_body_binds_another_name = class_body_binds_the_handle.replace(
        "sdk = _safe_handle()", "other = _safe_handle()")

    assert _flagged(class_body_binds_another_name), (
        "the write goes through the ENCLOSING lane's unbound handle — the "
        "control case, which must stay RED")
    assert _flagged(class_body_binds_the_handle) == _flagged(
        class_body_binds_another_name), (
        "a class-body assignment binds `C.<name>`, not the writing function's "
        "name, so it must not silence the enclosing unbound handle (#10723 "
        "review F1 — a false PASS where `origin/main` was RED)")

    class_body_write_through_its_own_handle = '''
def _lane(org_id):
    class C:
        sdk = _make_sdk(namespace=org_id)
        sdk.create_point("Subject", {})
'''
    assert _flagged(class_body_write_through_its_own_handle), (
        "a class body's OWN bindings are still read when the write's scope IS "
        "that class — the stop only removes the ENCLOSING function's claim on "
        "them, so this unbound write must stay RED")


def test_a_literal_namespace_is_not_an_org_scoped_site():
    """Pins the round-3 predicate: `_make_sdk(namespace="registry")` is not ours.

    `_make_sdk` is a member of `_SDK_OPENERS`, so the membership test in
    `_org_scoped_sdk_sites` has to exclude it or EVERY `_make_sdk(...)` counts as
    an org-scoped site — 94 of 175 counted sites were registry/base literals, and
    the non-vacuity floor could be cleared by them alone. Without this pin the
    double-count can be reinstated by a "simplification" and nothing reds: the
    floor is a LOWER bound and the module inventory is a superset.

    The SECOND half pins `namespace=None`, which the predicate COUNTS (#10046
    review F3). The docstring used to list it with the registry literals while
    the code counted it; the count is the behaviour we want — a `namespace=None`
    handle writes to the default graph and carries no tenant either, and
    `_handle_state` reaches this same predicate, so excluding it would make a
    write through such a handle invisible — so the docstring was corrected and
    the behaviour pinned here instead of left to drift either way.

    The THIRD half pins the ABSENT keyword (#10723 review F2). `_make_sdk()` is
    `namespace=None` at runtime — `hosted_api._make_sdk`'s defaults — and it was
    the one spelling that answered the OPPOSITE way on BOTH halves: not a site,
    and `_handle_state` → `None` ("not an SDK handle at all"), so a write
    through it was looked straight past. The write half is asserted through
    `_flagged` because that is the leg the silence actually cost.
    """
    registry = 'def f():\n    return _make_sdk(namespace="registry")\n'
    assert _org_scoped_sdk_sites(ast.parse(registry)) == {}, (
        "a string-literal namespace is the registry/base lane, not an org graph")
    none_namespace = 'def f():\n    return _make_sdk(namespace=None)\n'
    assert _org_scoped_sdk_sites(ast.parse(none_namespace)) == {"f": 1}, (
        "`namespace=None` is COUNTED deliberately: it is the probe/anchor "
        "surface, but a handle opened that way writes to the DEFAULT graph with "
        "no tenant, and `_handle_state` must not answer `None` (not-a-handle) "
        "for it")
    absent_kwarg = 'def f():\n    return _make_sdk()\n'
    assert _org_scoped_sdk_sites(ast.parse(absent_kwarg)) == {"f": 1}, (
        "`_make_sdk()` with NO keyword is `namespace=None` at runtime "
        "(`hosted_api._make_sdk`'s defaults), so it must count exactly like the "
        "explicit `namespace=None` — otherwise the omission is the one spelling "
        "that answers `not an org graph` on both halves")
    absent_kwarg_write = (
        "def f(org_id):\n"
        "    sdk = _make_sdk()\n"
        "    sdk.create_point('Subject', {})\n")
    flagged = _flagged(absent_kwarg_write)
    assert flagged and "`sdk`" in flagged[0], (
        "a write through `_make_sdk()` is an UNBOUND write: the handle carries "
        "no tenant and `_handle_state` answering `None` for it made the write "
        "invisible — the false-PASS shape this file exists to remove; "
        f"got {flagged!r}")
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


def test_an_AWAITED_unbound_opener_is_flagged_and_an_awaited_rebind_is_clean():
    """#10723 review F1: `await` is a TRANSPARENT wrapper, not a non-handle.

    `_operand_calls` flattened `or`/`if else` but left `ast.Await` on, so
    `sdk = await _data_sdk_offloaded(org)` recorded NO event for `sdk`:
    `_handle_state` never ran a branch and answered `None` — "not an SDK handle
    at all" — and the write-detector then looked straight past it. Both
    directions of the round-6/7 defect came back at once, and only for the async
    spelling:

    - a fail-OPEN on an awaited `_UNBOUND_OPENERS` member (the write is GREEN
      while the handle carries no tenant — the false PASS this file exists to
      stop), and
    - a FALSE POSITIVE on the documented fix when the rebind is spelled with the
      async twin (`_data_sdk_offloaded` IS an `_INSTALLING_OPENERS` member).

    Not a hypothetical spelling: `hosted_api.py` assigns
    `sdk = await _data_sdk_offloaded(org)` at 23 sites, so the census's "0
    unbound" says nothing about those handles unless this is covered.

    Mutation this reddens on: dropping the `ast.Await` unwrap in
    `_operand_calls` — the unbound case goes silent and the rebind case reds.
    """
    awaited_unbound = '''
async def _lane(org_id):
    sdk = await _org_sdk(org_id)
    sdk.create_point("Subject", {})
'''
    assert _flagged(awaited_unbound), (
        "an AWAITED unbound opener returns the same tenantless SDK — answering "
        "'not a handle' for it is the fail-open the review found")

    awaited_rebind = '''
async def _lane(org):
    sdk = _make_sdk(namespace=org["org_id"])
    sdk = await _data_sdk_offloaded(org)
    sdk.create_point("Subject", {})
'''
    assert _flagged(awaited_rebind) == [], (
        "`_data_sdk_offloaded` binds before returning, awaited or not — the "
        "SYNC twin of this rebind is silent, and the spelling must not decide")

    awaited_conditional = '''
async def _lane(org):
    sdk = _make_sdk(namespace=org["org_id"])
    if not sdk:
        sdk = await _data_sdk_offloaded(org)
    sdk.create_point("Subject", {})
'''
    assert _flagged(awaited_conditional), (
        "the conditional case must keep its worst-wins answer through the "
        "unwrap too: the branch may not run")


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
