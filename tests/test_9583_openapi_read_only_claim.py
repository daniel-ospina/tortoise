"""#9583: the served OpenAPI description is a string a CUSTOMER reads.

`@app.get("/v1/packs")`'s docstring said *"#318: read-only pack introspection"*.
FastAPI serves a route docstring as the OpenAPI operation ``description``, so that
phrase shipped in ``/openapi.json`` to every generated client — while the route calls
``pack_state.get_tenant_packs``, whose ``ensure_tenant_packs`` self-heal MERGEs a
missing starter ``:PackInstall`` activation (one MERGE per starter namespace, skipped
when the graph's pack set was deliberately configured — #2728/#8648). A GET that can
write advertised itself as a pure read.

This is the REST half of the falsehood #9575 fixed on the MCP half: #9575's lint reads
``TOOL_REGISTRY``, and ``hosted_api.py`` is outside the surface-manifest gate entirely,
so nothing covered the served schema at all. This test widens that lint from the tool
registry to the **served OpenAPI operation descriptions of BOTH FastAPI apps this repo
serves**.

"Both" is load-bearing, not defensive: ``tortoise/hosted_api.py`` builds one app, and the
self-host daemon builds a SECOND (``tortoise/selfhost.py`` ``app``), mounting
``tortoise/selfhost_api.router`` (prefix ``/v1``) on it. ``hosted_api.py`` never
``include_router``s that one, so a gate that iterates ``hosted_api.app.routes`` alone
leaves every self-hosted GET unread — which is EXACTLY the #8076 shape recorded on the
sibling inventory gate (``tests/test_rest_surface_inventory.py``: its docstring claimed
both modules from the start while its parser covered one, so a new self-hosted route
expanded the public API undeclared and every check stayed green). Read the two apps by
their OBJECTS (``_served_apps``), so a route added to either is in scope without anyone
remembering this file.

Coverage boundary, stated because a promise is not an enumeration: the two apps are
what this test enumerates. A THIRD served app would be out of scope until it is added
to ``_served_apps`` — pinned by
``test_the_gate_reads_every_app_it_names``, so the boundary cannot silently narrow.

The predicate is a DERIVATION, not a ledger of offenders: a GET operation whose handler
names a declared read-through writer may not claim read-only. The declared set is
``tests/tool_surface_capabilities.READ_THROUGH_WRITE_METHODS`` / ``NON_SDK_READ_BINDINGS``
— the same authority #4122 uses — so a NEW GET that fronts one of those writers fails
here without anyone remembering this test exists, and there is no second ledger to fall
out of date. Reachability is read off the handler's CODE (the docstring is excluded: the
claim lives in the docstring, the reach lives in the body), via ``ast``, so a mention in
prose does not manufacture an offender.

Not gated here: GET operations that claim read-only and reach nothing declared. Five
serve such a claim today, all on ``hosted_api`` (`/v1/internal/{abuse/state,
activation/cohort,oauth/clients/count,retention/cohort}`,
`/v1/graphs/trash/{graph_id}/points`); deciding each is a pure read is a different (and
larger) question than this issue asked, so they are measured, not adjudicated.

Measured at this head, so the widening is a RECORDED measurement and not a guess:
``hosted_api`` 62 GET routes / 5 read-only claims / 0 offenders; ``selfhost`` 11 GET
routes / 0 claims / 0 offenders. Enumerating the second app therefore changed no verdict
— which is the honest reading: it closed a hole (the next self-hosted GET fronting a
declared writer would have passed silently), it did not catch a second bug.
"""
from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "tests") not in sys.path:  # the shared capability ledger
    sys.path.insert(0, str(_REPO_ROOT / "tests"))

# The read-only CLAIM, negation-aware (the shape #9575 uses for the registry lint):
# "so not read-only" is the OPPOSITE claim, and `tortoise_get_source_reliability`
# makes it in its description.  No attributive exemption — a lookahead would exempt
# the whole occurrence, and "safe for a read-only client" describes the TOOL.
# Deliberately strict about camelCase too: `readOnly` matches, because both the space
# and the hyphen are optional.  That is why the corrected `/v1/packs` description names
# no MCP hint field — a served REST description should carry neither form.
_CLAIM = re.compile(r"(?<!not )(?<!never )read[\s\-]?only", re.IGNORECASE)


def _claim_in(description: str) -> str | None:
    """The offending phrase in a description, or None when it makes no claim."""
    m = _CLAIM.search(description or "")
    return m.group(0) if m else None


def _declared_read_through_writers() -> set[str]:
    """The declared reads that WRITE — one authority, shared with #4122/#9575."""
    from tool_surface_capabilities import (
        NON_SDK_READ_BINDINGS,
        READ_THROUGH_WRITE_METHODS,
    )
    return set(READ_THROUGH_WRITE_METHODS) | set(NON_SDK_READ_BINDINGS)


def _names_reached(source: str, declared: set[str]) -> set[str]:
    """Declared writer names the handler's CODE (not its docstring) names."""
    fn = ast.parse(textwrap.dedent(source)).body[0]
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        fn.body = body[1:]  # drop the docstring — prose is not a call
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)  # `pack_state.get_tenant_packs` form
    return names & declared


def _violations(ops) -> list[str]:
    """Every (path, claim) where a GET reaching a declared writer claims read-only."""
    out = []
    for path, description, reached in ops:
        phrase = _claim_in(description)
        if phrase and reached:
            out.append(f"{path}: claims {phrase!r} while its handler reaches "
                       f"{sorted(reached)} — a declared read-through writer")
    return sorted(out)


def _served_apps():
    """Every FastAPI app this repo SERVES, as (name, app) — TWO of them, not one.

    The self-host daemon (``tortoise/selfhost.py``) is a second app: its ``/v1`` routes
    come from ``tortoise/selfhost_api.router`` and are NOT in ``hosted_api.app.routes``
    (no ``include_router`` links the two). Enumerating only ``hosted_api`` would make the
    stated scope wider than the examined one — the #8076 defect, on this gate.
    """
    from tortoise import hosted_api, selfhost

    return (("hosted_api", hosted_api.app), ("selfhost", selfhost.app))


def _walk_routes(routes):
    """Every request route in a FastAPI route tree, descending into INCLUDED routers.

    How ``include_router`` is represented depends on the FastAPI version: older ones
    copy the routes in FLAT, and the fastapi 0.141 / starlette 1.6 pair ``uv.lock``
    pins here appends ONE ``_IncludedRouter`` whose ``original_router.routes`` hold
    them. Walking only the top level therefore reads ZERO self-host ``/v1`` routes on
    the pinned version while reading all of them on a flat one — an
    environment-dependent hole, which is the one thing a scope gate must not have.

    ``Mount`` is deliberately NOT descended: ``/mcp`` is a separate ASGI app mounted
    on the daemon (its surface is #9575's registry lint), and its paths are not
    OpenAPI operations of this app.
    """
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None and hasattr(nested, "routes"):
            yield from _walk_routes(nested.routes)
            continue
        if getattr(route, "methods", None) and hasattr(route, "endpoint"):
            yield route


def _served_get_operations():
    """(app:path, served description, declared writers reached) for every GET route.

    Keys carry the APP NAME because the two apps serve overlapping paths (``/health``,
    ``/v1/context``): a bare path key would let one app's clean description hide the
    other's claim, and the lookup helpers below would read whichever landed last.
    """
    import inspect

    declared = _declared_read_through_writers()
    ops = []
    for app_name, app in _served_apps():
        schema = app.openapi()
        for route in _walk_routes(app.routes):
            if "GET" not in (getattr(route, "methods", None) or set()):
                continue
            served = (schema["paths"].get(route.path, {}).get("get") or {})
            ops.append((
                f"{app_name}:{route.path}",
                served.get("description") or "",
                _names_reached(inspect.getsource(route.endpoint), declared),
            ))
    return ops


# --- the predicate is not vacuous: a probe written to be caught, and one not -------

_SYNTHETIC_OFFENDER = '''
async def list_packs():
    """#318: read-only pack introspection — the tenant's ACTIVE packs."""
    from tortoise.pack_state import get_tenant_packs
    return get_tenant_packs(None)
'''

_SYNTHETIC_NEGATION = '''
async def get_source_reliability():
    """The source's counter-considerations, so not read-only in the strict sense."""
    from tortoise.pack_state import get_tenant_packs
    return get_tenant_packs(None)
'''

_SYNTHETIC_PURE_READ = '''
async def health():
    """Read-only liveness probe."""
    return {"ok": True}
'''


def _ops_from_sources(*sources: str):
    declared = _declared_read_through_writers()
    ops = []
    for source in sources:
        fn = ast.parse(textwrap.dedent(source)).body[0]
        doc = ast.get_docstring(fn) or ""
        ops.append((fn.name, doc, _names_reached(source, declared)))
    return ops


class TestServedOpenApiReadOnlyClaim:
    def test_a_get_reaching_a_declared_writer_never_claims_read_only(self):
        """The gate itself. Red on `main` before #9583: `/v1/packs` claimed it."""
        offenders = _violations(_served_get_operations())
        assert offenders == [], (
            "a served OpenAPI GET description claims read-only while the route can "
            "write — state the self-heal trigger instead of asserting a guarantee:\n  "
            + "\n  ".join(offenders))

    def test_the_gate_actually_examines_pack_introspection(self):
        """Non-vacuity: a refactor that moves the call must not green this by silence.

        The `/v1/packs` GET is the finding this gate exists for, so if the derivation
        stops seeing a declared writer reached from it, the gate has stopped covering
        its own subject — that is a gate failure, not a pass.
        """
        reached = {path: names for path, _desc, names in _served_get_operations()}
        assert "hosted_api:/v1/packs" in reached, "the packs introspection route moved or vanished"
        assert reached["hosted_api:/v1/packs"], (
            "no declared read-through writer is derivable from the /v1/packs handler, "
            "so the read-only-claim gate no longer covers it — re-point the derivation "
            f"(declared: {sorted(_declared_read_through_writers())})")

    def test_the_gate_reads_every_app_it_names(self):
        """Non-vacuity for the SCOPE: both apps must actually be enumerated.

        #8076 is the recorded shape of getting this wrong — a claim of both while the
        parser read one — so the boundary is asserted per app rather than described.
        Fails if ``_served_apps`` narrows back to ``hosted_api`` alone, which is a gate
        failure (a whole served surface unread) and not a pass.
        """
        paths = {path for path, _desc, _names in _served_get_operations()}
        assert "hosted_api:/v1/packs" in paths, sorted(paths)
        assert "selfhost:/v1/points" in paths, (
            "the self-host app's routes are not enumerated, so a self-hosted GET could "
            "advertise itself as read-only while writing and every check stayed green "
            "(#8076) — the gate reads one app only")

    def test_the_served_packs_description_states_the_self_heal(self):
        """Acceptance, positively: the description says the call may write.

        Asserting only the ABSENCE of "read-only" would pass a description that says
        nothing at all, leaving the customer-facing claim merely deleted rather than
        corrected.
        """
        served = dict(
            (path, desc) for path, desc, _n in _served_get_operations())
        description = served["hosted_api:/v1/packs"]
        assert "read-only" not in description.lower(), description
        assert "ensure_tenant_packs" in description or "self-heal" in description, (
            "the /v1/packs description must state the self-heal (a graph with no "
            f"PackInstall record gets its starter set re-seeded): {description!r}")

    def test_probe_an_offender_is_caught(self):
        caught = _violations(_ops_from_sources(_SYNTHETIC_OFFENDER))
        assert len(caught) == 1 and "read-only" in caught[0], caught

    def test_probe_a_negation_is_not_a_claim(self):
        assert _violations(_ops_from_sources(_SYNTHETIC_NEGATION)) == []

    def test_probe_a_pure_read_may_claim_read_only(self):
        assert _violations(_ops_from_sources(_SYNTHETIC_PURE_READ)) == []

    def test_the_d4_record_states_the_ensure_half(self):
        """Acceptance: D4 is a LOCKED decision list, so the correction is a record.

        Amending D4 in place would read as history being tidied; D4a states what D4's
        word promised and what the mechanism actually does, right below D4.
        """
        from tortoise import pack_state

        doc = pack_state.__doc__ or ""
        assert "D4  Read-only introspection surface" in doc, (
            "D4 must stay VERBATIM — the correction is recorded as D4a, not as an edit")
        d4a = doc.split("D4a", 1)
        assert len(d4a) == 2, "D4a (the #9583 record) is missing from pack_state's docstring"
        assert "ensure_tenant_packs" in d4a[1] or "ensure-then-READ" in d4a[1], (
            "D4a must record the ensure half: the read can MERGE a missing starter "
            ":PackInstall activation")
