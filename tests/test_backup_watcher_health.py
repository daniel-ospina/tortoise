"""#2877: a dead backup watcher must be visible to /health, not just a boot log.

#2851/#2922 fixed the ``UnboundLocalError`` that stopped the backup watcher from
ever starting. The *class* of failure survived: ``_lifespan`` wraps the watcher
start in a log-and-continue ``except``, so the hosted durability monitor could
be completely dead while ``/health`` reported ``{"status": "ok"}``.

These tests are deliberately DB-free and app-boot-free: ``tortoise.hosted_api``
is imported with the pepper env set and the DB probe is stubbed, so they run in
the normal lane. Nothing here touches FalkorDB, R2, or the network.

Structure:
  * (a) functional + structural guards that the watcher boot path cannot
        silently shadow ``os`` / drop the failure marker again (#2851).
  * (b) ``/health`` reports ``degraded`` for a failed (never-started) or
        stopped (dead-thread) watcher, and stays ``ok`` when the sweep is
        legitimately disabled (#2877 target).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
from pathlib import Path

import pytest

# Auth module import requires the pepper; set it before importing the app.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.hosted_api as hosted_api

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSTED_API_SRC = REPO_ROOT / "tortoise" / "hosted_api.py"


# ── helpers ──────────────────────────────────────────────────────────────────


class _Thread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _FakeWatcher:
    def __init__(self, alive: bool) -> None:
        self._thread = _Thread(alive)


@pytest.fixture(autouse=True)
def _restore_watcher_state():
    """Never leak the module globals this suite mutates into another test."""
    prev_watcher = hosted_api._WATCHER
    prev_error = hosted_api._WATCHER_START_ERROR
    prev_expected = hosted_api._WATCHER_EXPECTED
    yield
    hosted_api._WATCHER = prev_watcher
    hosted_api._WATCHER_START_ERROR = prev_error
    hosted_api._WATCHER_EXPECTED = prev_expected


def _stub_db_ok(monkeypatch) -> dict:
    """Make ``/health``'s DB verdict a clean ``ok``.

    This tree is post-#2850: ``health()`` reads ``_HEALTH_PROBE.snapshot()``, one
    in-memory value from a background refresher, so patching the module-level
    ``_probe_db`` alone is not enough — it is reached only from
    ``HealthProbe._run``, and a cold probe reports
    ``{"ok": False, "error": "probe has not produced a result"}``, failing every
    ``ok``-state assertion for the wrong reason. The snapshot is stubbed, and
    ``_probe_db`` as well so the helper also holds for a handler that awaits it
    directly.

    Returns the verdict dict for callers that assert on ``body["db"]``.
    """
    verdict = {"ok": True, "latency_ms": 0.1, "error": None}
    monkeypatch.setattr(hosted_api, "_probe_db", lambda: dict(verdict))
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:  # main-only seam (#2850)
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(verdict))
    return verdict


def _health_with_db_ok(monkeypatch) -> dict:
    _stub_db_ok(monkeypatch)
    return asyncio.run(hosted_api.health())


def _lifespan_node() -> ast.AsyncFunctionDef:
    tree = ast.parse(HOSTED_API_SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan":
            return node
    raise AssertionError("tortoise/hosted_api.py has no `_lifespan` — relocate this pin")


# ── (a) the watcher boot path cannot regress into the #2851 shadow ───────────


def test_lifespan_does_not_bind_os_as_a_local():
    """#2851 functional guard: `os` must stay a module global in ``_lifespan``.

    A function-local ``import os`` (or ``os = ...``) binds ``os`` for the WHOLE
    function body, so the earlier ``os.environ.get(...)`` read raises
    ``UnboundLocalError`` — the exact crash that aborted the watcher-start block
    on every hosted boot for ~31 days.

    Two tables are checked, not one, because where the binding lands depends on
    whether anything nested captures it: a plain local lands in ``co_varnames``,
    and CPython promotes it to a **cell** (so it appears only in
    ``co_cellvars``) when a nested function inside ``_lifespan`` also reads the
    name. On the current tree ``co_varnames`` is the half that bites — no nested
    def in ``_lifespan`` reads ``os`` (``_sweep_events`` lives at module scope),
    and re-introducing ``import os`` puts ``os`` in ``co_varnames`` (verified by
    compiling the mutated function: ``co_cellvars`` stays clean). The
    ``co_cellvars`` half is kept against a future nested reader of ``os``.
    """
    code = inspect.unwrap(hosted_api._lifespan).__code__
    for shadowed in ("os", "asyncio", "threading", "logging"):
        assert (
            shadowed not in code.co_varnames and shadowed not in code.co_cellvars
        ), (
            f"`{shadowed}` is bound locally inside _lifespan — a function-local "
            "import/assignment shadows the module global for the whole function "
            "and re-introduces the #2851 UnboundLocalError"
        )


def test_watcher_start_failure_sets_the_health_marker():
    """#2877 structural guard: loudness alone does not reach /health.

    The start-failure ``except`` must record ``_WATCHER_START_ERROR`` — without
    it a failed watcher is indistinguishable from the legitimate disabled state
    and /health keeps reporting `ok` (the reported bug).

    The assignment must be located in an ``except`` handler AND carry a
    non-``None`` value. An unqualified "is the name assigned anywhere in
    ``_lifespan``" search is satisfied by the per-instance ``= None`` reset at
    the top of the function, so it stayed green with the failure-site
    assignment deleted (verified).
    """
    marker_in_handler = [
        node
        for handler in ast.walk(_lifespan_node())
        if isinstance(handler, ast.ExceptHandler)
        for node in ast.walk(handler)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id == "_WATCHER_START_ERROR"
        and not (isinstance(node.value, ast.Constant) and node.value.value is None)
    ]
    assert marker_in_handler, (
        "the start-failure `except` in _lifespan must assign "
        "`_WATCHER_START_ERROR` a non-None value — the `= None` reset at the "
        "top of the function does not count, or /health cannot tell 'wanted "
        "but dead' from 'disabled' (#2877)"
    )


# ── (b) /health surfaces a dead/absent watcher ───────────────────────────────


def test_health_ok_when_watcher_running(monkeypatch):
    hosted_api._WATCHER = _FakeWatcher(alive=True)
    hosted_api._WATCHER_START_ERROR = None
    hosted_api._WATCHER_EXPECTED = False

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "ok"
    assert body["backup_watcher"] == {
        "state": "running",
        "ok": True,
        "error": None,
        "expected": False,
    }


def test_health_degraded_when_watcher_never_started(monkeypatch):
    """The #2877 regression: DB fine, backups dead → not `ok`."""
    hosted_api._WATCHER = None
    hosted_api._WATCHER_START_ERROR = (
        "cannot access local variable 'os' where it is not associated with a value"
    )

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "degraded", (
        "a failed backup watcher must degrade /health — silently-absent backups "
        "were exactly the failure #2877 reported"
    )
    assert body["db"]["ok"] is True  # the DB is not the problem
    assert body["backup_watcher"]["state"] == "failed"
    assert body["backup_watcher"]["ok"] is False
    assert "os" in body["backup_watcher"]["error"]


def test_health_degraded_when_watcher_thread_died(monkeypatch):
    hosted_api._WATCHER = _FakeWatcher(alive=False)
    hosted_api._WATCHER_START_ERROR = None

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "degraded"
    assert body["backup_watcher"]["state"] == "stopped"
    assert body["backup_watcher"]["ok"] is False


def test_health_stays_ok_when_watcher_is_legitimately_disabled(monkeypatch):
    """Fail-closed default (no config) / kill switch must NOT degrade /health.

    The #2877 target pins this: false-positive degraded on every
    TestClient/embedded/self-host boot would train operators to ignore the
    signal, which is how the original blindness returns.
    """
    hosted_api._WATCHER = None
    hosted_api._WATCHER_START_ERROR = None
    hosted_api._WATCHER_EXPECTED = False

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "ok"
    assert body["backup_watcher"] == {
        "state": "disabled",
        "ok": True,
        "error": None,
        "expected": False,
    }


def test_health_never_raises_on_watcher_failure(monkeypatch):
    """Liveness must answer, not 5xx — a dead durability monitor is not a
    process death (#338 / the /health contract)."""
    _stub_db_ok(monkeypatch)

    class _ExplodingWatcher:
        @property
        def _thread(self):
            raise RuntimeError("watcher metadata unreadable")

    hosted_api._WATCHER = _ExplodingWatcher()

    body = asyncio.run(hosted_api.health())

    assert body["status"] == "degraded"
    assert body["backup_watcher"]["state"] == "unknown"
    assert body["backup_watcher"]["ok"] is False


# ── (c) #4498 `expected` — visible without degrading ────────────────────────


def test_health_expected_and_absent_is_visible_but_stays_ok(monkeypatch):
    """Hosted boot that dropped/lost its ``BACKUP_*`` config: /health must SHOW
    that a watcher was expected, yet still answer ``ok``.

    This is the whole point of #4498 option 2 (additive visibility, no owner
    decision): ``expected`` True on a ``disabled`` block with ``ok`` True, and
    the end-to-end body still ``status: "ok"``. Without the marker an operator
    cannot tell "expected and absent" from "intentionally off" without reading
    the boot log — the blindness #2851/#2870/#2922 produced for ~31 days.
    """
    hosted_api._WATCHER = None
    hosted_api._WATCHER_START_ERROR = None
    hosted_api._WATCHER_EXPECTED = True

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "ok"  # visibility, NOT degradation (#4498 option 2)
    assert body["backup_watcher"]["state"] == "disabled"
    assert body["backup_watcher"]["ok"] is True
    assert body["backup_watcher"]["expected"] is True


def test_health_non_hosted_default_reports_expected_false(monkeypatch):
    """The non-hosted/TestClient default: same ``disabled`` block, but
    ``expected`` False — so the two cases are distinguishable (#4498).

    This pins /health's *rendering* for a non-hosted boot: with the marker
    False the body carries ``expected: False`` and still ``status: "ok"``. It
    assigns the marker by hand and never runs a lifespan, so it does NOT pin the
    *publication* half — that a non-hosted ``_lifespan`` never publishes True.
    The publication is pinned structurally, for both the hosted and the
    non-hosted case, by
    ``test_watcher_expected_publish_happens_outside_conditionals_and_in_module_scope``
    below.
    """
    hosted_api._WATCHER = None
    hosted_api._WATCHER_START_ERROR = None
    hosted_api._WATCHER_EXPECTED = False

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "ok"
    assert body["backup_watcher"]["state"] == "disabled"
    assert body["backup_watcher"]["ok"] is True
    assert body["backup_watcher"]["expected"] is False


def test_watcher_expected_publish_happens_outside_conditionals_and_in_module_scope():
    """#4498 structural guard: the FIELD is worthless without the BOOT PLUMB.

    ``/health`` reads the module marker ``_WATCHER_EXPECTED``, and the only
    thing that sets it on a real boot is ``_WATCHER_EXPECTED = _watcher_expected``
    inside ``_lifespan``. The tests above assign the marker by hand, so they
    exercise the handler and never the plumb that feeds it — deleting that one
    line left this suite green. This is the analog of the
    ``_WATCHER_START_ERROR`` pin above, and it carries these conditions:

      * the assignment must EXIST in ``_lifespan`` (an unqualified search would
        otherwise be satisfied by the module default);
      * its VALUE must be the boot value ``_watcher_expected`` — a constant, a
        call or any other expression hard-codes one of the two cases while the
        published field still looks populated;
      * it must be REACHED and make a skip LOUD. Three mechanical rules are
        enforced, and no more:

        (1) NO ENUMERATED SKIPPING CONSTRUCT strictly between the publish and
            ``_lifespan``'s own body — conditional branches and handlers
            (``if`` / ``except``), ``match`` arms, loops that may iterate zero
            times (``while`` / ``for`` / ``async for``), nested ``def`` /
            ``async def`` / ``lambda`` / ``class`` scopes (an uncalled ``def``
            publishes nothing), and any ``with`` / ``async with``. The ONLY
            ``with`` exemption is the framework's own lifespan frame: a
            SINGLE-item ``with`` / ``async with`` that is a DIRECT statement of
            ``_lifespan``'s body AND whose ONLY context expression is a
            ``Call`` on an ``Attribute`` named ``lifespan`` — the real code's
            ``async with mcp_http_app.lifespan(mcp_http_app):``. THAT frame is
            the composition the whole body runs inside, so it cannot silently
            skip the publish: its body runs exactly when ``_lifespan`` runs,
            and failing to enter it propagates out of the boot. "Direct
            statement" alone is NOT safety — a direct
            ``with contextlib.suppress(...)`` is also a direct statement, and
            its ``__exit__`` swallows the very exception that would have made
            the skip loud — so every other ``with``, direct or nested, is a
            barrier. Requiring a SINGLE item matters independently: stacking a
            ``lifespan`` item beside any other context manager
            (``with suppress(...), lifespan(...):``) leaves the suppressing
            sibling's ``__exit__`` free to swallow the skip, so a multi-item
            ``with`` is never exempt however its items are ordered. The
            exemption matches the call SHAPE; see the residual.
            In particular the ``if _watcher_expected:``/``else`` branch only
            logs, so a publish there reaches only one of the two cases and a
            non-hosted re-entry keeps a prior boot's ``True``, corrupting
            #4498's distinction.
        (2) EVERY ``try`` / ``try*`` (``TryStar``) ANCESTOR must make a skipped
            publish LOUD. A ``try/finally`` (no handlers) qualifies only when
            its ``finally`` cannot discard the pending exception: a ``return``
            in ``finalbody`` (outside a nested scope) leaves the frame and drops
            the in-flight exception, so such a ``try/finally`` is not loud.
            ``break``/``continue`` are counted even when they target a loop
            INSIDE the ``finally`` — a deliberate, fail-closed
            over-approximation that can only red a safe ``finally``, never green
            a swallowing one. A ``try`` with handlers qualifies
            only if at least one handler makes the skip loud as a DIRECT
            statement of its body: an assignment to ``_WATCHER_START_ERROR``
            (so /health reports ``failed``, degraded) or a ``raise`` of ANY
            form. Both are gated on the ``finally``: a ``raise`` counts only
            while the ``finally`` cannot discard it (``finally: return``
            overrides the re-raise at runtime), and the marker assignment
            counts only while the ``finally`` does not itself write
            ``_WATCHER_START_ERROR`` (a clearing ``finally`` undoes it). A
            handler that only logs or ``pass``es swallows the skip, which is
            exactly #4498's original blindness. This is checked for
            EVERY such ancestor, not just the nearest, because an outer
            swallowing ``try`` can skip an inner marker-setting one entirely.
            The rule is per-``try``, not per-handler: a ``try`` with one
            marker-setting handler and one swallowing handler still passes
            rule (2).
        (3) NO PRECEDING TERMINATOR in the publish's OWN enclosing statement
            list: a ``return`` / ``raise`` / ``continue`` / ``break`` sibling
            that precedes the publish leaves the block before it runs.

        NOT modelled (the residual — named, not implied):
          * a ``try`` with one marker-setting handler AND one swallowing
            handler — the loudness rule is per-``try``, not per-handler, so
            the swallowing handler can still take the skip;
          * general reachability / whole-function control flow — a path that
            bypasses the enclosing block without any of the enumerated
            constructs (e.g. an ``if cfg is None: return`` before the plumb);
          * a swallowing ``__exit__`` reached through a ``lifespan``-named
            attribute that does not actually propagate — the exemption trusts
            the call shape and cannot see the object's real behaviour;
          * dynamic indirection (``getattr`` / ``exec``), and a handler whose
            exception class cannot actually occur.
        Those rules are syntactic and deliberately local; they pin the shapes
        that have actually regressed or been proposed (#2877, #4498; mutations
        f/n/c2b/β), not ``_lifespan``'s control-flow graph.
      * ``_WATCHER_EXPECTED`` must be named in a ``global`` statement of
        ``_lifespan``, or the assignment binds a local and the module marker
        never moves.

    Failure it prevents, in the reviewer's words: a later refactor drops or
    moves that line; the suite stays green; a hosted deploy that loses its
    ``BACKUP_*`` config again answers
    ``{"state":"disabled","ok":true,"expected":false}`` — indistinguishable
    from "deliberately off", i.e. #4498's blindness silently returns.
    """
    lifespan = _lifespan_node()

    declared_global = {
        name
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Global)
        for name in node.names
    }
    assert "_WATCHER_EXPECTED" in declared_global, (
        "`_WATCHER_EXPECTED` is not declared `global` in _lifespan — the boot "
        "assignment then binds a function local and /health keeps reading the "
        "module default False (#4498)"
    )

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(lifespan):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def _ancestors(node: ast.AST) -> list[ast.AST]:
        # Strictly BETWEEN the node and `_lifespan`'s own body: `_lifespan` is
        # the module scope this pin requires, so it is not itself a barrier.
        chain: list[ast.AST] = []
        current = parents.get(node)
        while current is not None and current is not lifespan:
            chain.append(current)
            current = parents.get(current)
        return chain

    publishes = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "_WATCHER_EXPECTED"
    ]
    assert publishes, (
        "_lifespan must publish `_WATCHER_EXPECTED = _watcher_expected` on boot "
        "— without it /health's `expected` field only ever renders the module "
        "default (#4498)"
    )

    sourced = [
        node
        for node in publishes
        if isinstance(node.value, ast.Name) and node.value.id == "_watcher_expected"
    ]
    assert sourced, (
        "the `_WATCHER_EXPECTED` publish must take its VALUE from the boot "
        "value `_watcher_expected` — a constant or any other expression "
        "hard-codes one of the two cases while the plumb still looks intact, "
        "so #4498's distinction is silently lost"
    )

    # Constructs that can leave the publish un-run or defer it: conditionals,
    # loops that may iterate zero times, `match` arms, nested function/class
    # scopes (an uncalled `def` publishes nothing), and any `with`/`async with`.
    # The ONE exemption is the framework's own lifespan frame — see
    # `_is_framework_lifespan_frame`: a SINGLE-item direct `with`/`async with`
    # whose ONLY context expression is a call on an attribute named `lifespan`.
    # That frame cannot silently skip its body (the whole function runs inside
    # it, and failing to enter it aborts the boot). "Direct statement" is NOT by
    # itself safety: `with contextlib.suppress(...)` is direct too and its
    # `__exit__` swallows the exception — so it stays a barrier (round-5 β).
    # Nor is a stacked item safe: `with suppress(...), lifespan(...):` still has
    # the suppressing sibling's `__exit__`, so `len(node.items) == 1` is
    # required (round-6 P3-1).
    barriers = (
        ast.If,
        ast.ExceptHandler,
        ast.While,
        ast.For,
        ast.AsyncFor,
        ast.Match,
        ast.With,
        ast.AsyncWith,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Lambda,
        ast.ClassDef,
    )

    def _is_framework_lifespan_frame(node: ast.AST) -> bool:
        # The framework's own lifespan wrapper — the ONE exempt `with`.
        # Shape-matched (never by file name, line number or the specific
        # app object): a SINGLE-item `with`/`async with` that is a DIRECT
        # statement of `_lifespan`'s body AND whose ONLY context expression is
        # a `Call` on an `Attribute` named `lifespan` (Starlette/FastMCP
        # `...lifespan(app)`). Only THAT frame is safe: its body runs whenever
        # `_lifespan` runs, and failing to enter it propagates and aborts the
        # boot. A suppressing / stacking / custom `with` — `contextlib.suppress`,
        # an `ExitStack`, a hand-rolled context manager — can swallow an
        # exception raised in its body and is NOT exempt. Fail closed: renaming
        # the framework call reddens the pin and names itself.
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            return False
        if node not in lifespan.body:
            return False
        if len(node.items) != 1:
            # A stacked sibling item beside the lifespan call keeps its own
            # `__exit__` (`with suppress(...), lifespan(...):`), so only a
            # SOLE-item `with` is exempt.
            return False
        (item,) = node.items
        return (
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "lifespan"
        )

    def _barrier(node: ast.AST) -> ast.AST | None:
        for anc in _ancestors(node):
            if _is_framework_lifespan_frame(anc):
                continue
            if isinstance(anc, barriers):
                return anc
        return None

    unbarriered = [node for node in sourced if _barrier(node) is None]
    assert unbarriered, (
        "the `_WATCHER_EXPECTED` publish must run in `_lifespan`'s own body, "
        "outside every construct that can skip or defer it — a conditional "
        "branch, an `except` handler, a loop that may never iterate, a `match` "
        "arm, a nested `def`/`class`, or any `with` that is not a single-item "
        "`with` whose only context is the framework's own `lifespan(...)` call "
        "— otherwise the assignment either never "
        "runs or reaches only some of the two cases, and a boot can serve a "
        "stale or hard-coded `expected` and #4498's distinction silently returns"
    )

    # Rule (2) of the docstring: `Try`/`TryStar` are deliberately NOT in
    # `barriers` above — the real plumb sits inside the boot block's `try:`
    # whose `except` sets `_WATCHER_START_ERROR`, which is precisely what turns
    # "the publish was skipped" into /health `failed` (degraded). So a `try`
    # ancestor is acceptable only if it cannot swallow the skip. A `try` with
    # no handlers (`try/finally`) cannot swallow the skip UNLESS its `finally`
    # leaves the frame — a `return` in `finalbody` discards the pending
    # exception, so that shape is not loud. `break`/`continue` are counted even
    # when they target a loop INSIDE the `finally`: over-approximating only
    # reds a safe `finally`, never greens a swallowing one. A
    # `try` with handlers can swallow, unless at least one handler makes it loud
    # as a DIRECT statement of its body: an assignment to
    # `_WATCHER_START_ERROR`, or a `raise`. Both are gated on the `finally`: the
    # raise counts only while it cannot discard it (`finally: return` overrides
    # the re-raise), and the marker assignment only while the `finally` does not
    # itself write the marker (a clearing `finally` undoes it). Direct statement
    # only, so `except Exception: if False: _WATCHER_START_ERROR = ...` cannot
    # look loud while setting nothing.
    def _finalbody_leaves_frame(node: ast.Try | ast.TryStar) -> bool:
        # Nested `def`/`async def`/`lambda`/`class` bodies are excluded: a
        # `return` in there binds to that inner frame and cannot leave THIS
        # one. `break`/`continue` are over-approximated (a `break` targeting a
        # loop INSIDE the `finally` does not leave the frame but is still
        # counted) — that only ever reds a safe `finally`, never greens a
        # swallowing one.
        nested: set[int] = set()
        for inner in ast.walk(node):
            if isinstance(
                inner,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            ):
                nested.update(id(sub) for sub in ast.walk(inner))
        return any(
            isinstance(sub, (ast.Return, ast.Break, ast.Continue))
            and id(sub) not in nested
            for stmt in node.finalbody
            for sub in ast.walk(stmt)
        )

    def _is_loud_try(node: ast.Try | ast.TryStar) -> bool:
        leaves = _finalbody_leaves_frame(node)
        if not node.handlers:
            # try/finally: loud only if the `finally` cannot discard the
            # in-flight exception. `ast.walk` (not just the top-level
            # statements) because a `return` nested in an `if` inside the
            # `finally` still leaves the frame and drops the pending exception
            # — the skipped publish then goes silent, which is #4498 all over
            # again.
            return not leaves
        # A `finally` that itself writes `_WATCHER_START_ERROR` undoes a
        # handler's marker assignment — the symmetric counterpart to `leaves`
        # gating the raise. Fail-closed at any depth: a `finally` writing the
        # marker is not something a legitimate boot path does.
        finally_writes_marker = any(
            isinstance(sub, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_WATCHER_START_ERROR"
                for target in sub.targets
            )
            for stmt in node.finalbody
            for sub in ast.walk(stmt)
        )
        for handler in node.handlers:
            for stmt in handler.body:
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(target, ast.Name)
                    and target.id == "_WATCHER_START_ERROR"
                    for target in stmt.targets
                ):
                    return not finally_writes_marker
                # A re-raise is loud only while the `finally` cannot discard
                # it: `finally: return` overrides it, so the skip goes silent.
                if isinstance(stmt, ast.Raise) and not leaves:
                    return True
        return False

    def _try_ancestors(node: ast.AST) -> list[ast.Try | ast.TryStar]:
        return [
            anc
            for anc in _ancestors(node)
            if isinstance(anc, (ast.Try, ast.TryStar))
        ]

    # EVERY `try` ancestor must be loud, not just the nearest: a farther
    # swallowing `try` can skip an inner marker-setting one entirely.
    loud = [
        node
        for node in unbarriered
        if all(_is_loud_try(try_node) for try_node in _try_ancestors(node))
    ]
    assert loud, (
        "every `try`/`try*` around the `_WATCHER_EXPECTED` publish must make a "
        "skipped publish LOUD — a handler that DIRECTLY sets "
        "`_WATCHER_START_ERROR` (so /health reports `failed`) or DIRECTLY "
        "`raise`s (any form: bare, `raise e`, `raise SomeError(...)`); a "
        "handler that only logs or `pass`es swallows the skip, and the publish "
        "is then skipped while /health still answers `disabled` + `ok` with a "
        "stale `expected` — #4498's blindness. A `try/finally` is fine only "
        "when its `finally` does not `return`/`break`/`continue` (which would "
        "discard the pending exception). Checked for EVERY `try` ancestor, not "
        "just the nearest"
    )

    # Rule (3) of the docstring: no statement that unconditionally leaves the
    # publish's OWN enclosing block may precede it. This closes the
    # placement-after-`return` shape without attempting general reachability —
    # only sibling ordering within the one enclosing statement list is scanned.
    def _preceding_terminator(node: ast.AST) -> ast.AST | None:
        parent = parents.get(node)
        if parent is None:
            return None
        for _field, value in ast.iter_fields(parent):
            if not isinstance(value, list) or node not in value:
                continue
            for stmt in value[: value.index(node)]:
                if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                    return stmt
        return None

    executed = [node for node in loud if _preceding_terminator(node) is None]
    assert executed, (
        "the `_WATCHER_EXPECTED` publish must be REACHED: it cannot follow a "
        "`return`/`raise`/`continue`/`break` in its own enclosing statement "
        "list, because that statement leaves the block before the publish runs "
        "and the marker keeps a prior boot's value (#4498). Only sibling "
        "ordering within that one body is checked; whole-function reachability "
        "is not"
    )
