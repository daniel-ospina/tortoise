"""Canonical serialization + deterministic ``batch_id`` derivation (epic #902 A4).

The ``batch_id`` is a deterministic, content-derived key over a bundle (§4.2):
identical bundle content ⇒ identical ``batch_id`` across crash-retry, so run-1
partial points and retry points share ONE batch_id and
``promote_batch(batch_id)`` covers the whole logical bundle. It is ULID-shaped
(26-char Crockford base32) and contains **no time component** — clock
independence holds by construction (CYCLE-21 pin): the derivation never reads
the clock, so retries hours/days later or under clock skew/NTP time-travel
preserve batch_id and exactly-once.

Canonical serialization (cycle-2/3/4/11 fix — determinism is load-bearing
across the MCP boundary; hashing Python dicts raw would split provenance on
key-order / float-format / list-order differences). The hash runs over the
canonical JSON of the RESOLVED bundle (refs expanded) with:

(a) keys sorted recursively at every mapping level,
(b) floats parsed then re-serialized via canonical repr (so ``0.30`` / ``0.3``
    / ``3e-1`` converge to one encoding),
(c) the connection list sorted by canonicalized (from, to, operator/relation,
    label, mitigation, reify) key tuple before hashing — connection order is
    not semantically load-bearing,
(d) UNICODE normalization — every string passes
    ``unicodedata.normalize("NFC", ...)`` before serialization,
(e) NUMERIC TYPE COLLAPSE — int/float equivalence by value (``1`` vs ``1.0``
    serialize identically),
(f) ITEM-LIST ORDER — every top-level item list (points, entities, sources)
    sorted by each item's canonical JSON before hashing.

REF EXPANSION PINNED (cycle-4 fix): "refs expanded" means every bundle-local
ref is replaced by the canonical JSON of the REFERENCED BUNDLE ITEM — NEVER
by an assigned id: ids do not exist at first stamp and crash-retry mints
different ULIDs for missed items, so id-based expansion would split batch_id
exactly where it must not. Ref-KEY names are pure syntax — renaming ref keys
without changing items leaves batch_id identical.

Ontology-evolution pin (CYCLE-25): batch_id is content-derived, not
kind-derived — the derivation is a pure function of the bundle text as
submitted and depends on no ontology lookup or kind-normalization table.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

# Crockford base32 alphabet (ULID spec — excludes I, L, O, U).
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Bundle sections whose item ORDER is not semantically load-bearing (f).
_ITEM_SECTIONS = ("points", "entities", "sources")

# Fields that may address earlier/later bundle items by local ref. The value
# (or each element of a list value, e.g. connection `to`) is replaced by the
# canonical JSON of the referenced item during ref expansion.
_REF_FIELDS = {
    "connections": ("from", "to"),
    "entities": ("authoredBy", "ownedBy", "managedBy",
                 "aboutSubject", "aboutObject", "aboutPoint", "aboutDocument"),
    "points": ("extractedFrom",),
}

# Connection sort-key tuple (c) — (from, to, operator/relation, label,
# mitigation, reify). `label`, `mitigation` AND `reify` join the key so the
# motivating tie shape (two same-pair reify connections differing ONLY in
# label) cannot tie under a shorter key and silently hash in input order.
_CONN_KEY_FIELDS = ("from", "to", "operator", "relation", "label",
                    "mitigation", "reify")


def _nfc(s: str) -> str:
    """(d) — NFC-normalize a string before serialization."""
    return unicodedata.normalize("NFC", s)


def _canonical_float(f: float) -> str:
    """(b)+(e) — canonical numeric encoding for a float value.

    ``0.30`` / ``0.3`` / ``3e-1`` all parse to the same float and re-serialize
    to one repr; integral floats collapse to their int encoding so ``1`` and
    ``1.0`` serialize identically (numeric type collapse).
    """
    if f.is_integer():
        return str(int(f))
    return repr(f)


def canonicalize(value: Any) -> Any:
    """Return a JSON-safe canonical transform of *value*.

    Dicts get NFC'd + recursively-canonicalized values (key ORDER is left to
    ``json.dumps(sort_keys=True)`` at serialization — keys sorted recursively
    at every mapping level, (a)); lists recurse preserving order (the
    load-bearing order sorts — connection list (c), item lists (f) — are
    applied at the bundle level before this); strings pass NFC (d); ALL
    numerics collapse to their canonical string encoding (b)/(e) so int 1 and
    float 1.0 serialize identically (``"1"``) and 0.30 / 0.3 / 3e-1 converge
    to one repr; bools/None pass through to json's own deterministic
    encodings.
    """
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if value is None:
        return value
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    if isinstance(value, dict):
        return {_nfc(str(k)): canonicalize(v) for k, v in value.items()}
    raise TypeError(
        f"canonicalize: unsupported type {type(value).__name__} "
        f"(value {value!r}) — bundles must be JSON-compatible"
    )


def canonical_json(value: Any) -> str:
    """Canonical JSON of *value*: recursively-sorted keys, compact separators.

    The same encoding used for the final bundle hash, the item-list sort keys
    and the connection-list sort key tuple — one deterministic byte form.
    """
    return json.dumps(
        canonicalize(value),
        sort_keys=True,           # (a) — sorted recursively at every level
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _crockford_ulid(digest: bytes) -> str:
    """Encode the first 16 digest bytes as a 26-char Crockford base32 ULID.

    ULID-shaped: 128 bits → 26 chars where the first char carries the top 3
    bits (so it is always 0-7) and the remaining 25 chars carry 5 bits each —
    matches ``sdk._CROCKFORD_ULID_RE`` (``^[0-7][0-9A-HJKMNP-TV-Z]{25}$``).
    """
    value = int.from_bytes(digest[:16], "big")
    chars = [_CROCKFORD32[(value >> 125) & 0x7]]  # first char: top 3 bits → 0-7
    chars += [
        _CROCKFORD32[(value >> (125 - 5 * i)) & 0x1F] for i in range(1, 26)
    ]
    return "".join(chars)


def _resolve_ref_field(value: Any, ref_items: dict[str, dict],
                       _depth: int = 0) -> Any:
    """Resolve bundle-local refs inside one field value.

    A ref key is replaced by the canonical JSON *string* of the referenced
    bundle item (never an assigned id — REF EXPANSION PINNED). List values
    resolve element-wise (connection ``to``). Non-ref values pass through
    unchanged (``refs.get(x, x)`` semantics — external ids/urls stay literal).
    """
    if _depth > 32:
        raise ValueError(
            "canonicalize: ref expansion exceeded depth 32 — cyclic bundle "
            "refs are not supported"
        )
    if isinstance(value, list):
        return [_resolve_ref_field(v, ref_items, _depth + 1) for v in value]
    if isinstance(value, str) and value in ref_items:
        return _item_canonical_json(ref_items[value], ref_items, _depth + 1)
    return value


def _item_canonical_json(item: dict, ref_items: dict[str, dict],
                         _depth: int = 0) -> str:
    """Canonical JSON of one bundle item, its own ref-valued fields resolved.

    *item* must already be stripped of its ``ref`` key (pure syntax). The
    item's canonical form is itself ref-expanded so a ref rename anywhere in
    the reference graph (item → item → item) leaves batch_id identical.
    """
    resolved = {
        k: _resolve_ref_field(v, ref_items, _depth)
        for k, v in item.items()
    }
    return canonical_json(resolved)


def resolve_refs(bundle: dict) -> dict:
    """Return a deep-ish copy of *bundle* with all bundle-local refs expanded.

    - every item's ``ref`` key is stripped (pure syntax — never hashed),
    - every ref-valued field (connection from/to, entity authoredBy/ownedBy/
      managedBy/about*, point extractedFrom) is replaced by the canonical JSON
      of the referenced bundle item.

    The copy is shallow at the section level but items/connections are new
    dicts — the caller's bundle is never mutated.
    """
    ref_items: dict[str, dict] = {}
    for section in _ITEM_SECTIONS:
        for item in bundle.get(section) or []:
            if isinstance(item, dict) and item.get("ref"):
                stripped = {k: v for k, v in item.items() if k != "ref"}
                ref_items[item["ref"]] = stripped

    resolved: dict[str, Any] = {}
    for section, items in bundle.items():
        if isinstance(items, list):
            resolved[section] = [
                {
                    k: _resolve_ref_field(v, ref_items)
                    for k, v in item.items()
                    if k != "ref"   # ref keys are pure syntax — never hashed
                }
                if isinstance(item, dict) else item
                for item in items
            ]
        else:
            resolved[section] = items
    return resolved


def canonical_bundle(bundle: dict) -> str:
    """Canonical JSON of the RESOLVED bundle (§4.2 (a)-(f), cycle-11 (h)).

    Order normalizations:
    - (f) top-level item lists (points/entities/sources) sorted by each
      item's canonical JSON — item order is not semantically load-bearing;
    - (c) the connection list sorted by the canonicalized (from, to,
      operator/relation, label, mitigation, reify) key tuple — connection
      order is not semantically load-bearing; identical duplicates dedup and
      conflicts are rejected in Phase 1, and label/mitigation/reify join the
      key so label-distinct same-pair reify connections sort deterministically
      regardless of input order (the (h) tie shape).
    """
    resolved = resolve_refs(bundle)

    for section in _ITEM_SECTIONS:
        items = resolved.get(section)
        if isinstance(items, list):
            canonical_items = [canonicalize(i) for i in items]
            resolved[section] = sorted(
                canonical_items,
                key=lambda c: json.dumps(
                    c, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True,
                ),
            )

    conns = resolved.get("connections")
    if isinstance(conns, list):

        def _conn_key(conn: Any) -> tuple[str, ...]:
            if not isinstance(conn, dict):
                return (canonical_json(conn),)
            # (c) — operator/relation: whichever of the two is present occupies
            # the third key slot; absent fields canonicalize to "null".
            key_parts: list[Any] = []
            for field in _CONN_KEY_FIELDS:
                if field == "operator" and "operator" not in conn:
                    key_parts.append(conn.get("relation"))
                elif field == "relation" and "relation" not in conn:
                    key_parts.append(conn.get("operator"))
                else:
                    key_parts.append(conn.get(field))
            return tuple(canonical_json(k) for k in key_parts)

        resolved["connections"] = sorted(
            [canonicalize(c) for c in conns], key=_conn_key
        )

    return canonical_json(resolved)


def derive_batch_id(bundle: dict) -> str:
    """Deterministic content-derived ``batch_id`` for *bundle* (§4.2).

    SHA-256 over the canonical JSON of the resolved bundle, encoded as a
    26-char Crockford base32 ULID. Pure function of the bundle text — no time
    component (CYCLE-21 clock-independence pin), no assigned ids, no ontology
    lookups (CYCLE-25 content-derived pin).
    """
    canonical = canonical_bundle(bundle)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return _crockford_ulid(digest)
