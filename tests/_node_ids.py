"""Resolve a named entity's ID from the graph — the #3590 test helper.

Before #3590 an Object/Subject id was a FUNCTION of its name
(``obj-<sha26(name)>``), so a test could predict it:
``_entity_name_id("Object", name)``. Identity is now an OPAQUE id minted once at
creation — there is nothing to predict — so a test that needs the id READS it
from the graph, which is also a stronger assertion: it pins the id the write
actually produced, not a second implementation of the same derivation.

Deliberately NOT a minter: a test must never mint an id. If a test needs an
entity to exist, it creates it through the SDK (which journals the registration)
and then resolves the id here.
"""

from __future__ import annotations


def node_id(source, label: str, name: str, *, prop: str = "name") -> str:
    """The single ``id`` of the ``label`` node whose ``prop`` equals ``name``.

    ``source`` may be a ``FalkorProjection`` (anything with ``.g``), a
    ``TortoiseSDK`` (resolved through ``_get_proj()``), or a raw graph handle.

    Raises ``AssertionError`` for zero holders and for two or more: a test that
    needs "the" entity of a name is asserting single-holder coexistence, and a
    silent pick would hide exactly the duplicate-carrier bug these tests exist
    to catch.
    """
    g = getattr(source, "g", None)
    if g is None:
        proj = getattr(source, "_get_proj", None)
        g = proj().g if callable(proj) else source.projection.g
    rows = g.query(
        f"MATCH (n:{label} {{{prop}:$name}}) RETURN n.id",
        params={"name": name}).result_set
    ids = [r[0] for r in rows if r[0]]
    assert len(ids) == 1, (
        f"expected exactly one {label} carrying {prop}={name!r}, found "
        f"{len(ids)} id-carrying holder(s) {ids} — {len(rows)} row(s) total")
    return ids[0]


def node_ids(source, label: str, name: str, *, prop: str = "name") -> list:
    """Every ``id`` held by ``label`` nodes whose ``prop`` equals ``name``."""
    g = getattr(source, "g", None)
    if g is None:
        proj = getattr(source, "_get_proj", None)
        g = proj().g if callable(proj) else source.projection.g
    return [r[0] for r in g.query(
        f"MATCH (n:{label} {{{prop}:$name}}) RETURN n.id",
        params={"name": name}).result_set]
