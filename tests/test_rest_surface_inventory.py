"""#3875 — the tenant REST surface must be DECLARED, and the declaration is checked.

WHY THIS EXISTS
---------------
#3863's objective is that the MCP tools and the SDK cannot be expanded without
explicit human approval. The REST surface is a **third** surface that objective
never reached: ``tortoise/hosted_api.py`` imports neither ``TOOL_REGISTRY`` nor
``HTTP_ALLOWED``, so a new ``@app.*`` route expands the product's public API
while every #3863 check stays green.

That is not hypothetical. Measured while implementing this check: the issue's own
context counted **125** route decorators; at implementation there were **132**.
**Seven routes were added between the issue being filed and being picked up, and
nothing failed.**

WHAT THIS CHECK DOES
--------------------
It enumerates the route decorators of ``tortoise/hosted_api.py`` and
``tortoise/selfhost_api.py`` — the SOURCE — and compares that set to
``config/rest-surface-inventory.yml``. The decorator form is PER MODULE (see
``_ApiModule``): the two modules attach to different FastAPI objects, so one
global ``@app.`` regex reads one of them and finds nothing in the other. That
is #8076 — this docstring claimed both modules from the start while the parser
covered only ``hosted_api.py``, so a new self-hosted route could expand the
public API undeclared and every check stayed green.

Because it reads the source rather than a second hand-maintained list, the check
cannot drift from the code. Only the declarations are hand-maintained, which is
the least machinery that makes the behaviour trustworthy.

It fails closed in BOTH directions:

* a live route absent from the inventory  → a new endpoint expanded the public API
  undeclared (the failure this guards); and
* an inventory entry with no live route   → a removed endpoint left a stale
  declaration, which is how the mirror starts lying in the other direction.

A one-way check would let one of those two rot silently. That is the same
"a wrong query and an absent defect are indistinguishable" shape that a
directional check always produces, so both directions are asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY = REPO_ROOT / "config" / "rest-surface-inventory.yml"

# Deliberately narrow: an unrecognised decorator form must NOT be silently
# skipped, so the parser asserts every known module contributed routes (see
# test_parser_is_live).
_METHODS = ("get", "post", "put", "patch", "delete")
_ROUTER_PREFIX_RE = re.compile(r'APIRouter\(\s*prefix\s*=\s*"([^"]*)"')

_VALID_CLASSES = {"tenant", "control-plane", "protocol"}


@dataclass(frozen=True)
class _ApiModule:
    """One API module and the decorator form ITS routes are written in.

    The decorator is per-module, not global (#8076): ``hosted_api.py`` attaches
    to the FastAPI app and writes full paths (``@app.get("/v1/…")``), while
    ``selfhost_api.py`` attaches to an ``APIRouter(prefix=…)`` and writes paths
    relative to it (``@router.post("/points")``)."""

    name: str
    #: the FastAPI object the module's routes attach to — ``app`` or ``router``
    decorator: str
    #: True when paths are relative to an ``APIRouter(prefix=…)`` in the module
    router_prefix: bool = False

    def prefix(self, src: str) -> str:
        """The path prefix applied to this module's routes."""
        if not self.router_prefix:
            return ""
        found = _ROUTER_PREFIX_RE.search(src)
        assert found, (
            f"{self.name} no longer declares APIRouter(prefix=...) at module "
            "scope — its route paths are relative to that prefix, so the "
            "enumeration would compare the wrong paths and this guard would "
            "pass on a lie"
        )
        return found.group(1)

    def parse(self, src: str) -> set[tuple[str, str]]:
        """(METHOD, path) for every route THIS module declares in ``src``."""
        prefix = self.prefix(src)
        pattern = re.compile(rf'@{self.decorator}\.({"|".join(_METHODS)})\(\s*"([^"]+)"')
        return {(meth.upper(), prefix + path) for meth, path in pattern.findall(src)}


MODULES: tuple[_ApiModule, ...] = (
    _ApiModule("hosted_api.py", "app"),
    _ApiModule("selfhost_api.py", "router", router_prefix=True),
)
_MODULES_BY_NAME = {module.name: module for module in MODULES}


def _module_source(name: str) -> str:
    return (REPO_ROOT / "tortoise" / name).read_text()


def _live_routes_by_module() -> dict[str, set[tuple[str, str]]]:
    """(METHOD, path) per module — the per-module view exists so that a module
    the parser cannot read is visible instead of absorbed into one big set."""
    return {module.name: module.parse(_module_source(module.name)) for module in MODULES}


def _live_routes() -> set[tuple[str, str]]:
    """(METHOD, path) for every route in every API module, in real path form."""
    found: set[tuple[str, str]] = set()
    for routes in _live_routes_by_module().values():
        found |= routes
    return found


def _declared_routes() -> set[tuple[str, str]]:
    doc = yaml.safe_load(INVENTORY.read_text())
    return {(r["method"].upper(), r["path"]) for r in doc["routes"]}


def test_parser_is_live() -> None:
    """A parser that finds NOTHING would compare equal to nothing and pass.

    A guard that cannot fail is not a guard, so the enumeration is asserted to
    be non-trivial AND to have seen EVERY known module. The ``> 100`` floor
    alone is not enough: ``hosted_api.py`` clears it on its own, which is
    exactly how a module with zero enumerated routes stayed green (#8076).
    """
    by_module = _live_routes_by_module()
    blind = sorted(name for name, routes in by_module.items() if not routes)
    assert not blind, (
        f"the route parser found ZERO routes in {blind} — that module's routes "
        "are invisible to this guard, so a new endpoint in it expands the "
        "public API while every check stays green (#8076). Decorator forms in "
        f"use: {[(m.name, '@' + m.decorator + '.') for m in MODULES]}"
    )
    live = _live_routes()
    assert len(live) > 100, (
        f"the route parser found only {len(live)} routes — it has lost the "
        "decorator form and this check is now vacuous"
    )


def test_parser_enumerates_both_decorator_forms() -> None:
    """The per-module rule, asserted on SYNTHETIC source.

    Kept off the live files on purpose: every self-hosted route currently has a
    same-shaped twin in ``hosted_api.py``, so a live-only assertion would pass
    even with the selfhost half blind — the exact state #8076 found.
    """
    hosted = _ApiModule("hosted_api.py", "app")
    selfhost = _ApiModule("selfhost_api.py", "router", router_prefix=True)
    hosted_src = '@app.get("/v1/widgets")\ndef handler(): ...\n'
    selfhost_src = (
        'router = APIRouter(prefix="/v1")\n\n\n'
        '@router.post("/widgets")\ndef handler(): ...\n\n\n'
        '@router.get("/widgets/{widget_id}")\ndef handler(): ...\n'
    )
    assert hosted.parse(hosted_src) == {("GET", "/v1/widgets")}
    assert selfhost.parse(selfhost_src) == {
        ("POST", "/v1/widgets"),
        ("GET", "/v1/widgets/{widget_id}"),
    }
    # The app form applied to the router module finds NOTHING — which is the
    # #8076 bug stated as an assertion, and why the decorator is per-module.
    assert hosted.parse(selfhost_src) == set()
    # The prefix is read from the source: change it and the enumerated paths
    # follow, so the guard cannot silently compare a stale prefix.
    assert selfhost.parse(selfhost_src.replace('prefix="/v1"', 'prefix="/v2"')) == {
        ("POST", "/v2/widgets"),
        ("GET", "/v2/widgets/{widget_id}"),
    }


def test_a_new_selfhost_route_reaches_the_declaration_check() -> None:
    """#8076 indicator 1 — an added ``@router.*`` route must red the live check.

    The mutation is IN MEMORY, not on disk: a test that rewrites the source it
    is testing races every other test in the shard. The parse is the same code
    path the live check uses, so a route seen here is a route
    ``test_every_live_route_is_declared`` can see.
    """
    module = _MODULES_BY_NAME["selfhost_api.py"]
    src = _module_source(module.name)
    baseline = module.parse(src)
    assert baseline, "the selfhost module declared no routes for this test to extend"
    mutated = src + '\n\n@router.get("/undeclared-thing")\ndef _added(): ...\n'
    newly_seen = module.parse(mutated) - baseline
    assert newly_seen == {("GET", "/v1/undeclared-thing")}, (
        "the parser did not pick up an added @router route — a new self-hosted "
        f"endpoint would expand the public API undeclared (#8076): {newly_seen}"
    )
    assert newly_seen - _declared_routes(), (
        "the added route must be UNDECLARED, otherwise this test proves nothing "
        "about the declaration check"
    )


def test_a_removed_selfhost_route_is_visible_to_the_parser() -> None:
    """#8076 indicator 2 — a selfhost route vanishing from the source is SEEN.

    What #8076 broke was enumeration, not the comparison: the parser found zero
    selfhost routes, so both directions were blind for that module. Renaming a
    route here (the same observable state as deleting it) must therefore make
    the route disappear from the enumerated set.

    Scope note, measured: every selfhost route currently has a same-shaped twin
    in ``hosted_api.py`` and the inventory carries no module attribution, so
    removing ONE of a twin pair reads as a stale declaration for NEITHER
    direction. The declaration comparison itself (both directions) is asserted
    by ``test_every_live_route_is_declared`` / ``test_every_declared_route_is_live``
    — the honest unit for this module is that its routes are enumerated at all.
    """
    module = _MODULES_BY_NAME["selfhost_api.py"]
    src = _module_source(module.name)
    prefix = module.prefix(src)
    live = sorted(module.parse(src))
    assert live, "the selfhost module declared no routes for this test to remove"
    method, path = live[0]
    assert path.startswith(prefix), f"{path} does not carry the router prefix {prefix!r}"
    decorator = f'@router.{method.lower()}("{path[len(prefix) :]}"'
    assert decorator in src, (
        f"could not find {decorator!r} in {module.name} — this test's source "
        "mutation no longer matches the module's decorator formatting"
    )
    mutated = src.replace(decorator, f'@router.{method.lower()}("/moved-away"', 1)
    gone = module.parse(src) - module.parse(mutated)
    assert gone == {(method, path)}, (
        f"removing {method} {path} from {module.name} was not visible to the "
        f"parser — the guard cannot see that module drift (#8076): {gone}"
    )


def test_every_live_route_is_declared() -> None:
    """A new route in EITHER module MUST be declared — the #3875 target."""
    undeclared = sorted(_live_routes() - _declared_routes())
    assert not undeclared, (
        "these REST routes are live but NOT declared in "
        "config/rest-surface-inventory.yml — a new endpoint expanded the "
        "product's public API without declaration (#3875):\n  "
        + "\n  ".join(f"{m} {p}" for m, p in undeclared)
    )


def test_every_declared_route_is_live() -> None:
    """A declaration with no live route is a stale mirror — the other direction."""
    stale = sorted(_declared_routes() - _live_routes())
    assert not stale, (
        "these routes are declared in config/rest-surface-inventory.yml but are "
        "NOT live — the declaration has gone stale (a removed endpoint left it "
        "behind):\n  " + "\n  ".join(f"{m} {p}" for m, p in stale)
    )


def test_every_entry_carries_a_known_class() -> None:
    """Each route states WHY it is (or is not) part of the approved tenant surface."""
    doc = yaml.safe_load(INVENTORY.read_text())
    bad = [
        f"{r['method']} {r['path']} -> {r.get('class')!r}"
        for r in doc["routes"]
        if r.get("class") not in _VALID_CLASSES
    ]
    assert not bad, (
        "every inventory entry must carry class in "
        f"{sorted(_VALID_CLASSES)}:\n  " + "\n  ".join(bad)
    )


@pytest.mark.parametrize("cls", ["tenant", "control-plane", "protocol"])
def test_class_partitions_the_surface(cls: str) -> None:
    """The three classes must together account for EVERY live route (0 unaccounted)."""
    doc = yaml.safe_load(INVENTORY.read_text())
    counted = {(r["method"].upper(), r["path"]) for r in doc["routes"] if r["class"] == cls}
    assert counted, f"class {cls!r} is empty — the inventory has lost a bucket"
    assert counted <= _live_routes(), (
        f"class {cls!r} declares routes that are not live: {sorted(counted - _live_routes())}"
    )
    # The partition target from the issue: 0 unaccounted across all classes.
    all_classes = {r["class"] for r in doc["routes"]}
    assert all_classes == _VALID_CLASSES, (
        f"the inventory must partition into exactly {sorted(_VALID_CLASSES)}; "
        f"it has {sorted(all_classes)}"
    )
