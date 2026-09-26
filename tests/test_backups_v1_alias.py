# tests/test_backups_v1_alias.py — #4144.
#
# WHY THIS FILE EXISTS
# --------------------
# The dashboard reaches the hosted API only through the same-origin BFF proxy
# (`website/apps/dashboard/functions/api/v1/[[path]].ts`), which rebuilds the
# upstream URL as `${API_ORIGIN}/v1/${rest}` — it CANNOT produce a bare path. The
# public backups family was served only at `/backups`, so the dashboard's
# `loadBackups` asked for `https://app.premiselabs.co/api/backups`, a path with no
# Pages Function, and got a 404: the Backups card and the Graphs tab's per-graph
# "last backup" both read as empty while backups existed (#4144).
#
# WHAT IS PINNED
# --------------
# 1. EVERY public backups route — derived from the app, not from a list — has a
#    `/v1/…` alias with the same HTTP methods as its bare counterpart, and the bare
#    path still exists (the alias is ADDITIVE, so CLI/ops callers posting to
#    `api.premiselabs.co/backups` are unaffected). A hardcoded pair list would have
#    stayed green when a NEW public route was added without an alias — the exact
#    failure this file exists to prevent, one route later.
# 2. The alias resolves to the SAME handler function object. A second, copied
#    handler would satisfy a naive "the route exists" check while the two paths
#    silently drift apart; identity is the property that actually matters.
from __future__ import annotations

from fastapi.routing import APIRoute

from tortoise.hosted_api import app


def _public_backup_paths() -> list[str]:
    """The public backups family, DERIVED from the app: a bare path (so excluded are
    the `/v1/…` aliases themselves, which must not be required to have aliases of
    their own, and the internal `/v1/internal/backups/*` family)."""
    found = {
        r.path
        for r in app.routes
        if isinstance(r, APIRoute) and (r.path == "/backups" or r.path.startswith("/backups/"))
    }
    return sorted(found)


def _alias_for(bare: str) -> str:
    return f"/v1{bare}"


def _routes(path: str) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path == path]


def _methods(path: str) -> set[str]:
    return {m for r in _routes(path) for m in (r.methods or ())}


def test_the_derived_family_is_the_public_backups_family() -> None:
    """Non-vacuity of the derivation above: a scan that found nothing would make every
    test below pass over an empty set, and an internal path leaking in would demand an
    alias for something the proxy must never reach."""
    bare = _public_backup_paths()
    assert "/backups" in bare, bare
    assert "/backups/restore" in bare, bare
    assert not any(p.startswith("/v1") for p in bare), bare
    assert not any("/internal/" in p for p in bare), bare


def test_every_public_backup_route_has_a_v1_alias() -> None:
    bare_paths = _public_backup_paths()
    assert bare_paths, "the public backups family was not found — the scan, not the app, is broken"
    for bare in bare_paths:
        alias = _alias_for(bare)
        assert _routes(bare), (
            f"{bare} disappeared — the /v1 alias must be additive, never a move: "
            "existing callers (CLI, ops scripts) post to the bare path."
        )
        assert _routes(alias), (
            f"{alias} is missing. The BFF proxy can only build "
            "'${API_ORIGIN}/v1/…', so a backups route served ONLY at a bare path is "
            "unreachable from the dashboard — the exact #4144 failure."
        )
        # METHOD PARITY, not mere existence. A mutation check found this mattered:
        # deleting only `@app.get("/v1/backups")` left the POST alias registered, so an
        # existence-only assertion still passed while the dashboard's GET was broken
        # again — the test would have certified the very hole it exists to catch.
        assert _methods(alias) == _methods(bare), (
            f"{alias} exposes {sorted(_methods(alias))} but {bare} exposes "
            f"{sorted(_methods(bare))} — every method of the public family must be "
            "reachable through /v1, not just some of them (#4144)."
        )


def test_the_alias_is_the_same_handler_object_not_a_copy() -> None:
    for bare in _public_backup_paths():
        alias = _alias_for(bare)
        by_methods = {frozenset(r.methods or ()): r.endpoint for r in _routes(bare)}
        assert by_methods, f"{bare} has no APIRoute"
        for r in _routes(alias):
            methods = frozenset(r.methods or ())
            assert methods in by_methods, (
                f"{alias} exposes {sorted(methods)}, which {bare} does not — the alias "
                "must mirror the bare family's methods, not invent its own."
            )
            assert r.endpoint is by_methods[methods], (
                f"{alias} {sorted(methods)} resolves to a DIFFERENT function object than "
                f"{bare}. Two implementations of one endpoint cannot be kept in step; the "
                "alias must point at the same handler (#4144)."
            )


def test_v1_backups_covers_every_method_the_dashboard_family_needs() -> None:
    """GET is what the dashboard calls today; POST (create) and POST restore are the
    rest of the public family. Asserted explicitly so a future client call cannot
    discover that only the list alias was added."""
    methods = sorted({m for r in _routes("/v1/backups") for m in (r.methods or [])})
    assert "GET" in methods, methods
    assert "POST" in methods, methods
    restore_methods = sorted({m for r in _routes("/v1/backups/restore") for m in (r.methods or [])})
    assert "POST" in restore_methods, restore_methods


def _contract(route: APIRoute) -> tuple:
    """The parts of a route that decide whether the alias BEHAVES like its original.

    `route.dependencies` is EMPTY for this family: FastAPI resolves the auth
    dependency from the handler SIGNATURE into `route.dependant.dependencies`, which
    is where this has to read. That is not incidental — the two halves of the family
    are deliberately different (`get_current_org_session_ungated` for the list,
    `get_current_org_gated` for the create), so an alias that lost or swapped one
    would be a security change, not a routing detail.
    """
    return (
        route.status_code,
        route.response_model,
        tuple(getattr(d.call, "__name__", repr(d.call)) for d in route.dependant.dependencies),
        tuple(sorted((route.responses or {}).keys())),
        # Also observable on the wire, and also settable per-decorator: a stacked
        # decorator could ship `include_in_schema=False` (the alias vanishes from the
        # published API) or a different response_class without touching the handler.
        getattr(route.response_class, "__name__", repr(route.response_class)),
        route.include_in_schema,
    )


def test_the_alias_preserves_the_bare_route_contract() -> None:
    """Parity of status code, response model, auth dependencies and declared responses.

    Existence + method parity + handler identity are necessary but not sufficient: the
    same handler object can be registered with different decorator arguments, so an
    alias could answer 200 where the bare path answers 201, or (worse) be registered
    without the dependency the bare path carries.
    """
    for bare in _public_backup_paths():
        alias = _alias_for(bare)
        bare_by_methods = {frozenset(r.methods or ()): r for r in _routes(bare)}
        assert bare_by_methods, f"{bare} has no APIRoute"
        for route in _routes(alias):
            methods = frozenset(route.methods or ())
            assert methods in bare_by_methods, (
                f"{alias} exposes {sorted(methods)}, which {bare} does not"
            )
            assert _contract(route) == _contract(bare_by_methods[methods]), (
                f"{alias} {sorted(methods)} differs from {bare} in status code, response "
                f"model, auth dependencies or declared responses:\n"
                f"  alias: {_contract(route)}\n"
                f"  bare:  {_contract(bare_by_methods[methods])}\n"
                "Stacked decorators can drift; an alias that dropped the gated "
                "dependency would open an unauthenticated write path (#4144)."
            )


def test_the_contract_comparison_is_not_vacuous() -> None:
    """A contract check over empty tuples would pass for any pair of routes — the
    Python counterpart of the empty-needle glob hole found in the watchdog harness.
    Assert the family really does carry resolved auth dependencies, and that the gated
    member is still gated."""
    by_methods = {frozenset(r.methods or ()): _contract(r) for r in _routes("/backups")}
    assert by_methods, "/backups has no APIRoute"
    for methods, contract in by_methods.items():
        assert contract[2], (
            f"/backups {sorted(methods)} resolved to NO auth dependency, so the contract "
            "comparison above proves nothing about auth"
        )
    names = {name for contract in by_methods.values() for name in contract[2]}
    assert "get_current_org_gated" in names, (
        f"the create route no longer resolves the GATED dependency (found {sorted(names)}) "
        "— an unauthenticated write path"
    )
