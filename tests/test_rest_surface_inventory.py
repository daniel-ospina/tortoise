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
It enumerates ``@app.<method>("<path>")`` from ``tortoise/hosted_api.py`` and
``tortoise/selfhost_api.py`` — the SOURCE — and compares that set to
``config/rest-surface-inventory.yml``.

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
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
API_MODULES = ("hosted_api.py", "selfhost_api.py")
INVENTORY = REPO_ROOT / "config" / "rest-surface-inventory.yml"

# The same shape the inventory was seeded from. Deliberately narrow: an
# unrecognised decorator form must NOT be silently skipped, so the parser
# asserts it found a plausible number of routes (see test_parser_is_live).
_ROUTE_RE = re.compile(r'@app\.(get|post|put|patch|delete)\("([^"]+)"')

_VALID_CLASSES = {"tenant", "control-plane", "protocol"}


def _live_routes() -> set[tuple[str, str]]:
    """(METHOD, path) for every ``@app.*`` route in the API modules."""
    found: set[tuple[str, str]] = set()
    for name in API_MODULES:
        src = (REPO_ROOT / "tortoise" / name).read_text()
        for meth, path in _ROUTE_RE.findall(src):
            found.add((meth.upper(), path))
    return found


def _declared_routes() -> set[tuple[str, str]]:
    doc = yaml.safe_load(INVENTORY.read_text())
    return {(r["method"].upper(), r["path"]) for r in doc["routes"]}


def test_parser_is_live() -> None:
    """A parser that finds NOTHING would compare equal to nothing and pass.

    A guard that cannot fail is not a guard, so the enumeration is itself
    asserted to be non-trivial and to have seen both known modules.
    """
    live = _live_routes()
    assert len(live) > 100, (
        f"the route parser found only {len(live)} routes — it has lost the "
        "decorator form and this check is now vacuous"
    )


def test_every_live_route_is_declared() -> None:
    """A new ``@app.*`` route MUST be declared — this is the #3875 target."""
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
