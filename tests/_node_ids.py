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

    ⛔ The count is of HOLDERS (matched rows), never of truthy ids (#9876 review
    P2). An id-less legacy/hosted stub holder (``MERGE (o:Object {name:$n})``
    mints those) contributes a row whose value is ``None``; filtering it out
    would make one id-carrying plus one id-less live carrier — the mixed D2
    shape — pass as "one entity", which is the same fail-open this PR removed
    at ``resolve_or_mint``, ``resolve_fold_name`` and
    ``_refuse_ambiguous_rename`` (see ``entity_identity.live_name_holder_ids``:
    "filtering the ``None`` out would silently pick one").
    """
    g = getattr(source, "g", None)
    if g is None:
        proj = getattr(source, "_get_proj", None)
        g = proj().g if callable(proj) else source.projection.g
    rows = g.query(
        f"MATCH (n:{label} {{{prop}:$name}}) RETURN n.id",
        params={"name": name}).result_set
    holder_ids = [r[0] for r in rows]
    assert len(holder_ids) == 1, (
        f"expected exactly one {label} carrying {prop}={name!r}, found "
        f"{len(holder_ids)} holder(s) {holder_ids}")
    assert holder_ids[0], (
        f"the single {label} carrying {prop}={name!r} has no id — an id-less "
        f"legacy/hosted stub holder is not addressable, so there is no id to "
        f"return {holder_ids}")
    return holder_ids[0]


def node_ids(source, label: str, name: str, *, prop: str = "name") -> list:
    """Every ``id`` held by ``label`` nodes whose ``prop`` equals ``name``."""
    g = getattr(source, "g", None)
    if g is None:
        proj = getattr(source, "_get_proj", None)
        g = proj().g if callable(proj) else source.projection.g
    return [r[0] for r in g.query(
        f"MATCH (n:{label} {{{prop}:$name}}) RETURN n.id",
        params={"name": name}).result_set]
