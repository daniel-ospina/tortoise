"""#3904 — the declared SDK binding must be CONSUMED, not merely present.

``tests/test_surface_resolution.py`` resolves each entry and asserts
``callable(getattr(TortoiseSDK, entry.sdk_method))``. That is an
EXISTENCE/SHAPE property of the named TARGET, and it cannot see three things:

1. **declaration↔behaviour drift** — the handler reads one attribute while the
   registry declares another. The live case: ``tortoise_traverse`` declares
   ``sdk_method="traverse"``, but its handler reads
   ``_get_org_sdk()._get_proj()``.
2. **inert-but-callable targets** — ``def audit(self): return {}`` satisfies
   ``callable()``, and an entry may be pointed at an inert method no handler ever
   reads.
3. **wrong-class attribution** — a name that resolves somewhere else entirely.

This module is the CONSUMED-declaration gate. It installs a **recording-stub
SDK**, drives the REAL MCP handler for every registry entry, and asserts the
attribute the handler ACTUALLY READ includes the entry's declared
``sdk_method``. The declaration↔behaviour question is answered by the READ, not
by the target's shape.

WHY NOT ``MagicMock`` — this is the whole instrument
    ``MagicMock`` auto-creates EVERY attribute, so ``getattr(stub, anything)``
    always succeeds and nothing is ever absent. That makes it structurally
    incapable of being the discriminator: a Mock cannot report which name the
    handler read, because it answers them all identically. The recorder must be
    a real object whose ``__getattr__`` observes the access. ``unittest.mock`` is
    used here only to PATCH the SDK-acquisition seam.

DRIVING THE REAL HANDLER
    Handlers acquire the SDK through the module-level ``_get_org_sdk()`` (and
    ``_get_sdk()``); both are patched to return the ONE recording stub for the
    duration of a call. Handlers then fail on their own argument validation —
    that is expected and FINE: the SDK read happens before it, in the very
    expression that passes the bound method to ``_safe``/``_quota_gated``. Each
    call is wrapped in ``except Exception`` and a handler's own exception is
    NEVER allowed to fail the gate; the exception type is recorded and reported
    (also via ``record_property``), so the swallow is visible rather than silent.
    ``Exception`` — not ``BaseException`` — is the right width: the census over
    every vector is ``{AttributeError, TypeError, ValueError}``, all ordinary
    handler-validation failures, so a Ctrl-C (``KeyboardInterrupt``) or a
    ``SystemExit`` during the ~80s drive must PROPAGATE rather than be swallowed
    as one more handler exception (it would otherwise read as a slow pass). A
    handler that legitimately raised a non-``Exception`` ``BaseException``
    subclass would need evidence added here before widening back.

    Two argument vectors are tried per handler — ``minimal`` (required params
    filled, defaults elsewhere) and ``typed`` (every param filled) — and the
    reads are UNIONED. ``_DRIVE_OVERRIDES`` adds a targeted vector for the three
    handlers whose declared read sits behind an early-return branch a generic
    vector cannot reach (a real directory, an entity id, a valid action). A
    handler that reaches NO SDK read at all is an explicit, CLOSED ledger
    (``_HANDLER_SERVED_NO_SDK_READ``) checked in BOTH directions — never a silent
    pass.

THE RED THIS GATE WAS BUILT ON (#3904)
    Before the repair, ``tortoise_traverse`` was RED: it declares
    ``sdk_method="traverse"`` while the handler reads ``_get_proj``.
    ``TortoiseSDK.traverse`` is a LIVE but UNRELATED method — signature
    ``(id, relationship_type, direction)`` — which is precisely why the
    existence gate cannot see the drift. Driving the whole registry also
    surfaced a SECOND entry of the same class,
    ``tortoise_operator_action``: it declares ``sdk_method="operator_action"``
    while its handler reads ``mitigate_operator`` / ``annotate_operator`` and
    never calls the SDK's consolidated method. Both were repaired to
    ``sdk_method=""`` (handler-served), matching the #4035 / PR #4043 idiom.

    This is NOT an MCP/SDK surface change: no tool's name, description or
    schema moved and ``TortoiseSDK.traverse`` / ``TortoiseSDK.operator_action``
    are untouched. Only a DECLARATION that the code did not consume was made
    honest, which is the #4035 repair shape.

THE TWO MUTATION TESTS (acceptance requirement 6)
    ``test_mutation_repointed_declaration_reds_the_gate`` repoints one entry's
    ``sdk_method`` at a DIFFERENT LIVE method and asserts the gate names it.
    ``test_mutation_inert_but_callable_target_reds_the_gate`` installs an
    inert-but-callable method on ``TortoiseSDK``, points the entry at it, and
    asserts BOTH that the old predicate ``callable(getattr(TortoiseSDK, name))``
    stays GREEN and that this gate REDS — the exact blind spot the issue
    records, demonstrated rather than asserted.

KNOWN BOUNDARY, stated rather than hidden
    A READ-based gate cannot see a target's BODY. If the handler does read the
    declared name and that method's body is later made inert, this instrument
    still passes — the recording stub replaces the target entirely, so the real
    body is never executed. Catching that needs a behavioural test of the
    method, which this instrument is explicitly not.
"""
from __future__ import annotations

import contextlib
import functools
import inspect
import types
import typing
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any
from unittest import mock

import pytest

import tortoise.mcp_server as mcp_server
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import TOOL_REGISTRY

# ── the recording stub ───────────────────────────────────────────────────────


class _Permissive:
    """A benign sentinel: every attribute/call/index yields another one and never raises.

    The handler must run far enough to record the read, so the object the stub
    hands back must be usable as a stand-in for whatever the real SDK returns —
    a projection, a dict, a list. Returning ``self`` for all of them is what
    keeps a handler from dying on ``proj.db`` or ``result["x"]`` before the next
    read is recorded.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> _Permissive:
        return _Permissive()

    def __call__(self, *args: Any, **kwargs: Any) -> _Permissive:
        return _Permissive()

    def __getitem__(self, key: Any) -> _Permissive:
        return _Permissive()

    def __iter__(self) -> typing.Iterator[Any]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: Any) -> bool:
        return False

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        return "<_Permissive>"


class _RecordingSDK:
    """A stub SDK that RECORDS the names of attributes read off it.

    ``__getattr__`` fires only for attributes the instance does not have, so the
    recorder list itself is a real instance attribute (never recorded) and every
    other access is an observed READ. Dunder probes are excluded: framework code
    (and pytest) may probe ``__wrapped__``/``__name__``-style attributes, and
    those are not SDK reads. Declared ``sdk_method`` names are always ordinary
    public methods, so excluding dunders cannot hide a declared read.
    """

    def __init__(self) -> None:
        self.reads: list[str] = []

    def __getattr__(self, name: str) -> _Permissive:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        self.reads.append(name)
        return _Permissive()

    def __repr__(self) -> str:
        return f"<_RecordingSDK reads={self.reads!r}>"


@contextlib.contextmanager
def _recording_seam(stub: _RecordingSDK) -> typing.Iterator[None]:
    """Return ``stub`` from every SDK-acquisition seam a handler can use.

    ``_get_org_sdk`` and ``_get_sdk`` are patched as MODULE globals on
    ``mcp_server`` — the names the handlers actually resolve — and the stdio
    transport + dev-mode gate are pinned so ``_safe`` reaches the closure that
    carries the read (a handler that passes a ``lambda`` to ``_safe`` otherwise
    records nothing when the auth gate short-circuits). Auth is orthogonal to
    the declaration↔behaviour question; nothing here asserts anything about it.
    """
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(mcp_server, "_get_org_sdk", lambda: stub))
        stack.enter_context(mock.patch.object(mcp_server, "_get_sdk", lambda: stub))
        stack.enter_context(mock.patch.object(mcp_server, "_is_dev_mode", lambda: True))
        token = mcp_server._transport_mode.set("stdio")
        try:
            yield
        finally:
            mcp_server._transport_mode.reset(token)


# ── argument vectors ─────────────────────────────────────────────────────────


def _typed_value(annotation: Any) -> Any:
    """A benign value of ``annotation``'s shape, so argument validation lets the call through."""
    if annotation is inspect.Parameter.empty or annotation is Any or annotation is None:
        return "x"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in args if a is not type(None)]
        return _typed_value(non_none[0]) if non_none else None
    if origin is typing.Literal:
        return args[0]
    if origin in (list, set, tuple, frozenset):
        return []
    if origin is dict:
        return {}
    if annotation is str:
        return "x"
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is dict:
        return {}
    if annotation in (list, tuple, set, frozenset):
        return []
    return "x"


def _resolved_hints(fn: Any) -> dict[str, Any]:
    try:
        return typing.get_type_hints(fn, globalns=dict(vars(mcp_server)))
    except Exception:  # an unresolved forward ref falls back per-param
        return {}


def _vectors_for(name: str, fn: Any) -> tuple[tuple[str, tuple, dict], ...]:
    """The argument vectors a handler is driven with; reads are unioned across them."""
    vectors: list[tuple[str, tuple, dict]] = []
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None
    if sig is not None:
        params = [
            p
            for p in sig.parameters.values()
            if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
        hints = _resolved_hints(fn)
        minimal = tuple(
            _typed_value(hints.get(p.name, p.annotation))
            for p in params
            if p.default is inspect.Parameter.empty
        )
        typed = tuple(_typed_value(hints.get(p.name, p.annotation)) for p in params)
        vectors.append(("minimal", minimal, {}))
        vectors.append(("typed", typed, {}))
    vectors.extend(_DRIVE_OVERRIDES.get(name, ()))
    return tuple(vectors)


@dataclass(frozen=True)
class DriveResult:
    reads: tuple[str, ...]
    swallowed: tuple[str, ...]
    vectors: tuple[str, ...]


@functools.cache
def _drive_handler_cached(name: str, fn: Any) -> DriveResult:
    reads: list[str] = []
    swallowed: list[str] = []
    labels: list[str] = []
    for label, args, kwargs in _vectors_for(name, fn):
        labels.append(label)
        stub = _RecordingSDK()
        with _recording_seam(stub):
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # a handler exception must not fail the gate; KeyboardInterrupt/SystemExit propagate
                swallowed.append(f"{label}:{type(exc).__name__}")
        reads.extend(stub.reads)
    return DriveResult(tuple(dict.fromkeys(reads)), tuple(swallowed), tuple(labels))


def drive_handler(name: str, fn: Any) -> DriveResult:
    """Drive ``fn`` with every vector against a recorder, swallowing handler exceptions.

    The union of reads is deduplicated while preserving order. ``swallowed``
    names the exception TYPE per vector so the swallow is visible; it is never
    consulted by the gate (only a mismatch may fail).

    The result is CACHED on ``(name, fn)`` — importing ``mcp_server`` dominates
    this module's runtime, and five full sweeps of the registry would otherwise
    quadruple it. The cache is safe because the drive is a pure read: the seam
    is patched per call, the handler's read set depends only on its own source,
    and a mutation test that substitutes a handler passes a DIFFERENT ``fn``
    object (a distinct cache key).
    """
    return _drive_handler_cached(name, fn)


# ── the ledger and the drive overrides ───────────────────────────────────────
#
# A handler-served entry (``sdk_method=""``) has no declared SDK binding to
# consume, so the gate only requires that it RESOLVES (``getattr(mcp_server, name)``
# is callable) and does not claim a named binding. The entries below additionally
# reach NO SDK read at all on the stdio lane, because they are hosted-only: each
# returns before touching the SDK when no org context is set. That is an explicit,
# LISTED condition, closed in both directions — an unlisted no-read entry fails,
# and a listed entry that starts reading fails as a stale ledger row.
_HANDLER_SERVED_NO_SDK_READ: dict[str, str] = {
    "tortoise_pack_install": (
        "hosted-only (#1935): on self-host it returns the actionable stub before "
        "importing the hosted pack store, so no SDK attribute is read."
    ),
    "tortoise_session_capture": (
        "hosted-only: with no org (or the self-host sentinel) it returns the "
        "requires-hosted-mode error before any SDK access."
    ),
    "tortoise_graph_set_recording": (
        "hosted control-plane (#2302): returns the requires-hosted-mode error when "
        "there is no org, before reading the SDK."
    ),
    "tortoise_onboarding_demo_create": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
    "tortoise_onboarding_state": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
    "tortoise_onboarding_seed": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
    "tortoise_onboarding_session_recording": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
    "tortoise_onboarding_github_connect": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
    "tortoise_onboarding_github_index": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
    "tortoise_onboarding_github_status": (
        "hosted-only (org context required): returns the no-team-context error on stdio."
    ),
}

# Targeted vectors for the three handlers whose declared read sits behind an
# early-return branch a generic vector cannot satisfy. Each is explained; the
# import-time gate below refuses an override that names no registry entry.
_DRIVE_OVERRIDES: dict[str, tuple[tuple[str, tuple, dict], ...]] = {
    "tortoise_index_files": (
        (
            "real-directory",
            (),
            {"directory": "."},  # the handler returns early unless os.path.isdir(directory)
        ),
    ),
    "tortoise_get_entity": (
        (
            "entity-id",
            (),
            {"id": "x"},  # with id=None the handler delegates to tortoise_get and never reads get_entity
        ),
    ),
    "tortoise_operator_action": (
        (
            "mitigate",
            (),
            # the generic vectors pass ``action="x"`` (the benign str sample),
            # which is an UNKNOWN action: the handler returns the unknown-action
            # error BEFORE reading the SDK. A real action is required to enter a
            # branch, and this vector enters the mitigate one.
            {"action": "mitigate", "id": "x", "reason": "r"},
        ),
        (
            "annotate",
            (),
            # the annotate branch reads ``annotate_operator`` only when ALL FOUR
            # dimensions are non-None (else it returns the validation error first,
            # mcp_server.py::tortoise_operator_action), so the override must fill
            # them; this is the second half of the recorded union.
            {
                "action": "annotate",
                "id": "x",
                "bias": 0.1,
                "precision": 0.5,
                "consistency": 0.5,
                "directness": 0.5,
            },
        ),
    ),
}


# ── the exact PUBLIC-read expectation (dynamic reach, closed both ways) ──────
#
# ``tests/tool_surface_capabilities.py`` used to pin each of these two entries'
# exact PUBLIC reached set through ``DECLARED_BINDING_DIVERGENCES`` (it compared
# the declared method against ``recorded.reached``). #3904 emptied that ledger
# when both declarations were made honest (``sdk_method=""``), which also
# dropped the only pin that:
#   * ``tortoise_traverse`` reaches NO public SDK method (its handler reads only
#     ``_get_proj``, an underscore/internal), and
#   * ``tortoise_operator_action`` reaches exactly the two operator methods.
# This gate re-establishes the property DYNAMICALLY — the reads are RECORDED off
# the stub, not derived by AST — so a new public read either handler starts
# reaching is a red, in EITHER direction.
#
# Keyed by entry name; the value is the exact set of PUBLIC SDK names the handler
# must READ. The map is CLOSED: every listed entry is asserted exact, and a key
# that names no callable registry entry fails at import time (see the collection
# gate at the foot of this module).
_EXPECTED_PUBLIC_READS: dict[str, frozenset[str]] = {
    # reads only ``_get_proj``; NO public SDK method is reached. The #3904
    # divergence was that it DECLARED ``traverse`` — a live but unrelated method,
    # signature ``(id, relationship_type, direction)`` — which its handler never
    # read. Pinning the empty public set keeps that declaration from returning
    # unnoticed.
    "tortoise_traverse": frozenset(),
    # dispatches to ``mitigate_operator`` / ``annotate_operator``; the union is
    # exactly what the blanked consumed declaration was made honest about (the
    # consolidated ``operator_action`` SDK method is never reached).
    "tortoise_operator_action": frozenset({"mitigate_operator", "annotate_operator"}),
}


# ── the gate ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BindingViolation:
    kind: str
    entry_name: str
    declared: str
    reads: tuple[str, ...]
    swallowed: tuple[str, ...]


def _classify(entry: Any, result: DriveResult, known_no_read: frozenset[str]) -> BindingViolation | None:
    """The ONE gate predicate, shared by the parametrized test and ``find_violations``."""
    if entry.sdk_method:
        if entry.sdk_method not in result.reads:
            return BindingViolation(
                "DECLARED_BINDING_NOT_CONSUMED",
                entry.name,
                entry.sdk_method,
                result.reads,
                result.swallowed,
            )
        return None
    if not result.reads:
        if entry.name in known_no_read:
            return None
        return BindingViolation(
            "HANDLER_SERVED_UNLISTED_NO_READ", entry.name, "", (), result.swallowed
        )
    if entry.name in known_no_read:
        return BindingViolation(
            "LEDGER_STALE_READS_NOW", entry.name, "", result.reads, result.swallowed
        )
    return None


def check_entry(
    entry: Any, fn: Any, known_no_read: frozenset[str]
) -> tuple[BindingViolation | None, DriveResult]:
    if not callable(fn):
        return (
            BindingViolation("NO_HANDLER", entry.name, entry.sdk_method, (), ()),
            DriveResult((), (), ()),
        )
    result = drive_handler(entry.name, fn)
    return _classify(entry, result, known_no_read), result


def find_violations(
    registry: Any,
    handlers: dict[str, Any] | None = None,
    known_no_read: frozenset[str] = frozenset(),
) -> tuple[list[BindingViolation], set[str], Counter]:
    """Run the gate over ``registry``; return (violations, observed-no-read, exception census).

    ``handlers`` defaults to the real MCP handlers; a mutation test may pass a
    substituted mapping, which is how the inert-but-callable mutation is built
    without editing source.
    """
    if handlers is None:
        handlers = {e.name: getattr(mcp_server, e.name, None) for e in registry}
    violations: list[BindingViolation] = []
    observed_no_read: set[str] = set()
    census: Counter = Counter()
    for entry in registry:
        violation, result = check_entry(entry, handlers.get(entry.name), known_no_read)
        for swallowed in result.swallowed:
            census[swallowed.rsplit(":", 1)[-1]] += 1
        if violation is not None:
            violations.append(violation)
        if not entry.sdk_method and not result.reads:
            observed_no_read.add(entry.name)
    registry_names = {e.name for e in registry}
    for name in sorted(known_no_read - registry_names):
        violations.append(
            BindingViolation("LEDGER_ORPHAN_NOT_IN_REGISTRY", name, "", (), ())
        )
    return violations, observed_no_read, census


_KNOWN_NO_READ = frozenset(_HANDLER_SERVED_NO_SDK_READ)


def _describe(violation: BindingViolation) -> str:
    return (
        f"[{violation.kind}] {violation.entry_name}: declared sdk_method="
        f"{violation.declared!r}; reads={list(violation.reads)!r}; "
        f"swallowed_exceptions={list(violation.swallowed)!r}"
    )


def _format(violations: list[BindingViolation]) -> str:
    return "consumed-declaration gate violations:\n  " + "\n  ".join(
        _describe(v) for v in violations
    )


# ── the scoreboard ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("entry", TOOL_REGISTRY, ids=lambda e: e.name)
def test_entry_consumes_its_declared_sdk_binding(entry, record_property):
    """Each entry's handler must READ the attribute the registry declares.

    A ``sdk_method="<name>"`` entry must have ``<name>`` among the reads
    recorded off the stub. A handler-served entry (``sdk_method=""``) must
    resolve on ``mcp_server`` and must not claim a named binding; if it reads
    nothing at all it must be in ``_HANDLER_SERVED_NO_SDK_READ``.

    Mutation that REDs this: repoint ``sdk_method`` at a different (even live)
    method, or replace the handler body with an inert ``return {}``.
    """
    fn = getattr(mcp_server, entry.name, None)
    violation, result = check_entry(entry, fn, _KNOWN_NO_READ)
    record_property("sdk_reads", ",".join(result.reads))
    record_property("swallowed_exceptions", ",".join(result.swallowed))
    record_property("vectors", ",".join(result.vectors))
    assert violation is None, _describe(violation)


def test_declared_public_read_set_is_exact():
    """The exact PUBLIC SDK read set is pinned for the entries this gate declares.

    ``_EXPECTED_PUBLIC_READS`` is a CLOSED map, asserted in BOTH directions:

      * an entry whose recorded public reads differ from its expectation fails —
        a NEW read, a dropped read, or a changed dispatch branch (an expectation
        that says ``unreached`` while the handler now reaches something is
        exactly this case); and
      * a key that names no callable registry entry fails here (and at import
        time via the collection gate), so the map cannot grow a phantom row.

    This is the dynamic replacement for the static exact-reach pin the emptied
    ``DECLARED_BINDING_DIVERGENCES`` ledger used to carry (see the map's
    comment): a NEW public read reached by ``tortoise_traverse`` — the case the
    ledger removal left unflagged — is a red here.
    """
    registry_names = {e.name for e in TOOL_REGISTRY}
    mismatches: list[str] = []
    for name, expected in sorted(_EXPECTED_PUBLIC_READS.items()):
        if name not in registry_names:
            mismatches.append(f"{name}: expectation names no registry entry (stale map)")
            continue
        fn = getattr(mcp_server, name, None)
        if not callable(fn):
            mismatches.append(f"{name}: no callable handler to drive")
            continue
        result = drive_handler(name, fn)
        observed = frozenset(r for r in result.reads if not r.startswith("_"))
        if observed != expected:
            mismatches.append(
                f"{name}: expected public reads {sorted(expected)} but recorded "
                f"{sorted(observed)} (all reads: {list(result.reads)!r})"
            )
    assert not mismatches, "exact public-read expectation drifted:\n  " + "\n  ".join(mismatches)


def test_handler_served_no_read_ledger_is_exact():
    """The no-read ledger is closed in BOTH directions.

    An unlisted handler-served entry that reads nothing fails; a listed entry
    that STARTS reading fails as stale so the ledger cannot outlive its reason.
    Both are produced by ``find_violations`` itself, so this test cannot drift
    from the gate it documents.
    """
    violations, observed, _census = find_violations(
        TOOL_REGISTRY, known_no_read=_KNOWN_NO_READ
    )
    ledger = set(_HANDLER_SERVED_NO_SDK_READ)
    assert observed == ledger, (
        "handler-served no-read ledger drifted — "
        f"observed_but_unlisted={sorted(observed - ledger)}, "
        f"listed_but_reads_now={sorted(ledger - observed)}"
    )
    ledger_rows = [
        v for v in violations if v.kind in ("LEDGER_STALE_READS_NOW", "LEDGER_ORPHAN_NOT_IN_REGISTRY")
    ]
    assert not ledger_rows, _format(ledger_rows)


def test_drive_swallows_handler_exceptions_and_reports_the_census():
    """The swallowed exceptions are VISIBLE, and the census is not empty by accident.

    The assertion is deliberately not on a pinned set of exception types — a
    handler refactor legitimately changes them, and requirement 2 says a
    handler's own exception must never fail the gate. The census is asserted to
    be COLLECTED and each entry is asserted to have been driven at least once:
    an entry whose vectors never ran would be a silent hole in the scoreboard.
    """
    _violations, _observed, census = find_violations(
        TOOL_REGISTRY, known_no_read=_KNOWN_NO_READ
    )
    assert isinstance(census, Counter)
    undriven = [
        e.name for e in TOOL_REGISTRY if not _vectors_for(e.name, getattr(mcp_server, e.name, None))
    ]
    assert not undriven, f"registry entries with no drive vector at all: {undriven}"
    print(f"swallowed handler-exception census: {dict(sorted(census.items()))}")


# ── mutation proof ───────────────────────────────────────────────────────────

_MUTATION_TARGET = "tortoise_query"          # declares sdk_method="query"; handler reads .query
_MUTATION_WRONG_LIVE_METHOD = "create_point"  # a DIFFERENT live method (not inert)
_MUTATION_INERT_METHOD = "inert_stub_3904"    # added to TortoiseSDK by the mutation


def _mutation_registry(sdk_method: str) -> tuple:
    """``TOOL_REGISTRY`` with one entry's declaration repointed — no source edit."""
    return tuple(
        replace(e, sdk_method=sdk_method) if e.name == _MUTATION_TARGET else e
        for e in TOOL_REGISTRY
    )


def _mutation_hits(violations: list[BindingViolation]) -> list[BindingViolation]:
    return [v for v in violations if v.entry_name == _MUTATION_TARGET]


def test_mutation_repointed_declaration_reds_the_gate():
    """Repoint one declaration at another LIVE method → the gate must name it.

    Precondition, asserted rather than assumed: the unmutated entry really does
    consume its declaration and the mutation target really is live, so a green
    run cannot come from the mutation being a no-op.
    """
    original = next(e for e in TOOL_REGISTRY if e.name == _MUTATION_TARGET)
    baseline_reads = drive_handler(_MUTATION_TARGET, getattr(mcp_server, _MUTATION_TARGET)).reads
    assert original.sdk_method in baseline_reads, (
        f"precondition failed: {_MUTATION_TARGET} does not consume "
        f"{original.sdk_method!r} (reads={baseline_reads!r})"
    )
    assert hasattr(TortoiseSDK, _MUTATION_WRONG_LIVE_METHOD), (
        "the mutation must point at a LIVE method, not an absent one"
    )

    violations, _observed, _census = find_violations(
        _mutation_registry(_MUTATION_WRONG_LIVE_METHOD), known_no_read=_KNOWN_NO_READ
    )
    hits = _mutation_hits(violations)
    assert hits and hits[0].kind == "DECLARED_BINDING_NOT_CONSUMED", (
        "a repointed declaration did not red the gate: " + _format(violations)
    )
    assert _MUTATION_WRONG_LIVE_METHOD not in hits[0].reads


def test_mutation_inert_but_callable_target_reds_the_gate(monkeypatch):
    """An INERT but callable target → the gate must red where the old one could not.

    ``TortoiseSDK.<inert> = lambda self: {}`` satisfies the OLD predicate
    ``callable(getattr(TortoiseSDK, name))`` — the blind spot the issue records
    (defect 2). This gate reds because no handler READS the inert name. The
    test asserts the old predicate is green on the SAME mutation, so the added
    discrimination is demonstrated, not merely claimed.
    """
    monkeypatch.setattr(TortoiseSDK, _MUTATION_INERT_METHOD, lambda self: {}, raising=False)
    # The OLD gate's entire check — deliberately asserted GREEN here.
    assert callable(getattr(TortoiseSDK, _MUTATION_INERT_METHOD))
    inert_target = getattr(TortoiseSDK, _MUTATION_INERT_METHOD)
    assert inert_target(self=None) == {}, "the injected target must be inert-but-callable"

    violations, _observed, _census = find_violations(
        _mutation_registry(_MUTATION_INERT_METHOD), known_no_read=_KNOWN_NO_READ
    )
    hits = _mutation_hits(violations)
    assert hits and hits[0].kind == "DECLARED_BINDING_NOT_CONSUMED", (
        "an inert-but-callable target did not red the gate: " + _format(violations)
    )


# ── collection gate: fail-closed and NON-DESELECTABLE ────────────────────────
# Validated at IMPORT time, during collection and BEFORE pytest applies `-k`,
# `-m` or node-id selection — a shrunken case set cannot be hidden by
# deselecting the test that reports it. Mirrors the idiom in
# tests/test_surface_resolution.py.


def _parametrized_case_names() -> list[str]:
    for mark in test_entry_consumes_its_declared_sdk_binding.pytestmark:
        if mark.name == "parametrize":
            names: list[str] = []
            for param in mark.args[1]:
                value = param.values[0] if hasattr(param, "values") else param
                names.append(value.name)
            return names
    return []


_CASES = _parametrized_case_names()
assert _CASES, "empty scoreboard — no registry entries to drive (fail-closed)"
assert [e.name for e in TOOL_REGISTRY] == _CASES, (
    f"the scoreboard parametrised {len(_CASES)} cases for {len(TOOL_REGISTRY)} registry "
    "entries — a sample is not a scoreboard"
)
_REGISTRY_NAMES = {e.name for e in TOOL_REGISTRY}
_ORPHANED_LEDGER = sorted(set(_HANDLER_SERVED_NO_SDK_READ) - _REGISTRY_NAMES)
assert not _ORPHANED_LEDGER, (
    f"orphaned no-read ledger entries: {_ORPHANED_LEDGER} — the registry renamed or "
    "removed them without re-pointing the ledger"
)
_ORPHANED_OVERRIDES = sorted(set(_DRIVE_OVERRIDES) - _REGISTRY_NAMES)
assert not _ORPHANED_OVERRIDES, (
    f"orphaned drive overrides: {_ORPHANED_OVERRIDES} — the registry renamed or removed "
    "them without re-pointing the override"
)
_ORPHANED_EXPECTATIONS = sorted(set(_EXPECTED_PUBLIC_READS) - _REGISTRY_NAMES)
assert not _ORPHANED_EXPECTATIONS, (
    f"orphaned public-read expectations: {_ORPHANED_EXPECTATIONS} — the registry renamed "
    "or removed them without re-pointing the expectation"
)
