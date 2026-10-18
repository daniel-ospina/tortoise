"""Pack-declared per-kind VALUE FIELDS (#2818) — the #2782 declaration seam.

A pack manifest's ``ontology.kindDefs.<kind>`` is a **closed** key set
(``pack_registry.VALID_KINDDEF_KEYS``): an unrecognised key is a hard
validation error, so a declaration surface is a thing we add deliberately,
never a props bag that grows by accident. This module is the ONE home of the
value-field declaration's vocabulary and shape rules — the validator
(``pack_registry._validate``), the brief compiler
(``value_extractor.compile_value_brief``) and the prompt renderers
(``extractor_v2._render_master*``) all read it from here, so the three cannot
drift.

**What a value field is.** ``#2782`` declares the money value on the Point
lane; a pack has to be able to say *which kinds carry which value properties*
— *"a ``fundingTranche`` carries ``committedAmountMinor`` as a **money**
value"*. Without a declaration surface that fact can only be hardcoded in
engine code, which is the hardcoded-map anti-pattern ``#2729`` exists to
remove.

**The vocabulary is CLOSED, and that is the point.** ``valueKind`` is WS-C's
value-shape discriminator (``docs/plans/2026-09-10-ws-c-numeric-normalization``),
never a storage type: ``money`` is a shape over ``amountMinor`` +
``currency`` + ``minorUnitExponent``, not a scalar. An unknown ``valueKind``,
an unknown entry key, a namespaced name, or a non-list ``values`` is a
**validation error with a precise message** — a malformed declaration must
never degrade into an opaque dropped pack or a ``TypeError`` on the way to
the prompt.

**Property names are BARE** (the owner ruling on WS-B OD2: ``committedAmountMinor``,
never ``venture:committedAmountMinor``). Kinds are namespaced by the pack;
property names are not, so a colon in a declared name is refused here rather
than silently declared and then unwritable on every read/index path.

**What this module deliberately does NOT declare (yet):** the value shape's
companion props (``currency``, ``minorUnitExponent``, ``basisPoints``, …) and
the membership rule that a ``money`` declaration requires them. Those are
WS-C's core ``POINT_PROPS`` register (WS-A D1 / WS-C D12), which does not
exist in code yet; a pack *binds* them there rather than retyping them. The
write path for a declared value field is ``#2782``. Declaring the shape is
the seam this module ships; rendering it into the classification index text is
explicitly NOT done (it would rotate the npz cache key — see
``value_extractor.compile_kind_index_spec``).

**Relationship to the WS-B design**
(``docs/plans/2026-09-10-ws-b-pack-property-declaration.md``, D2). That design
generalizes the per-kind declaration into a ``props`` register that also
carries registrations and core-prop BINDINGS, and its step 1 is blocked on
WS-A's ``POINT_PROPS`` (``tortoise/projection/contract.py``), which is not in
code. ``values`` is the narrow #2818 subset the issue asks for — a value field
is a property with a ``valueKind`` — and it is deliberately NOT the wider
``props`` schema, because shipping that schema without the core register would
mean either an unvalidated bag or a fork of the key. When the ``props``
register lands, it supersedes this key (one home at a time); this module's
vocabulary and its precise messages move with it rather than being
re-implemented beside it.
"""
from __future__ import annotations

import re

#: A declared value field's name is a BARE property identifier: it is rendered
#: raw into the extraction prompt, and it becomes a node property / index key
#: downstream, so anything outside this shape (a comma, a newline, a bracket)
#: is refused at install instead of being pasted into the prompt.
_BARE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

#: WS-C's value-SHAPE discriminator (``valueKind``) — the only values
#: ``valueKind`` may take. A shape, not a storage type: ``money`` is a
#: composite over bearer + companion props. Keep this the single copy; a new
#: shape lands here and every consumer follows it.
VALUE_KINDS = frozenset({
    "money",      # amountMinor + currency + minorUnitExponent
    "percent",    # basisPoints
    "multiple",   # ratioNumerator + ratioDenominator
    "duration",   # durationMinor + durationUnit
    "quantity",   # quantityMinor + quantityExponent + unit
})

#: Keys a single value-field declaration may carry. Unknown keys are errors —
#: the same closed-vocabulary rule ``VALID_KINDDEF_KEYS`` applies one level up.
VALUE_FIELD_KEYS = frozenset({"name", "valueKind", "required", "cardinality"})

#: A PROPERTY cardinality, not the relation cardinality
#: (``pack_registry.VALID_CARDINALITIES`` is ``one_to_one``/…): how many
#: values of this shape one node of the kind carries.
VALUE_CARDINALITIES = frozenset({"one", "many"})

_ALLOWED_FIELD_KEYS = ", ".join(sorted(VALUE_FIELD_KEYS))
_ALLOWED_VALUE_KINDS = ", ".join(sorted(VALUE_KINDS))
_ALLOWED_CARDINALITIES = ", ".join(sorted(VALUE_CARDINALITIES))


def is_bare_value_field_name(name: object) -> bool:
    """True when ``name`` is a bare property identifier (``committedAmountMinor``)."""
    return isinstance(name, str) and bool(_BARE_NAME_RE.match(name))


def validate_value_fields(kind: str, spec: dict) -> list[str]:
    """Shape-check ``kindDefs.<kind>.values``; return ``[]`` or error strings.

    Errors are prefixed ``kindDefs '<kind>': values[i] …`` and each one names
    the offending declaration, so ``tortoise pack validate`` (which reuses
    ``PackRegistry._validate``) tells a pack author exactly what to fix.
    Never raises: the validator's contract is to RETURN its error list, and a
    malformed section is the case it exists for.
    """
    values = spec.get("values")
    if not isinstance(values, list):
        return [
            f"kindDefs '{kind}': 'values' must be a list of value-field "
            f"declarations (got {type(values).__name__})"
        ]
    errors: list[str] = []
    seen: dict[str, int] = {}
    for i, entry in enumerate(values):
        where = f"kindDefs '{kind}': values[{i}]"
        if not isinstance(entry, dict):
            errors.append(
                f"{where} must be a map (got {type(entry).__name__})"
            )
            continue
        for key in entry:
            if key not in VALUE_FIELD_KEYS:
                errors.append(
                    f"{where}: unknown key '{key}' (allowed: "
                    f"{_ALLOWED_FIELD_KEYS})"
                )
        name = entry.get("name")
        if name is None:
            errors.append(
                f"{where}: 'name' is required — the BARE property name this "
                f"value field carries"
            )
            name = None
        elif not isinstance(name, str) or not name.strip():
            errors.append(
                f"{where}: 'name' must be a non-empty string (got {name!r})"
            )
            name = None
        else:
            # NOT stripped: the name must be bare AS WRITTEN. A leading or
            # trailing space is a declaration error like any other whitespace
            # (and the docs say so), not something to silently normalize into a
            # different property name.
            if ":" in name:
                errors.append(
                    f"{where}: value field name '{name}' must be BARE — "
                    f"property names are not namespaced (WS-B OD2 ruled "
                    f"bare names; kinds carry the namespace, properties do "
                    f"not)"
                )
            elif not is_bare_value_field_name(name):
                # The name is rendered raw into the S2/S4 prompt and becomes a
                # node property downstream, so whitespace/punctuation is a
                # declaration error, not prompt material.
                errors.append(
                    f"{where}: value field name {name!r} must be a bare "
                    f"property identifier (letters, digits and underscores, "
                    f"not starting with a digit)"
                )
            elif name in seen:
                errors.append(
                    f"{where}: duplicate value field '{name}' (already "
                    f"declared at values[{seen[name]}])"
                )
            else:
                seen[name] = i
        value_kind = entry.get("valueKind")
        if name is not None and value_kind is None:
            errors.append(
                f"{where} '{name}': 'valueKind' is required (one of "
                f"{_ALLOWED_VALUE_KINDS})"
            )
        elif value_kind is not None and not isinstance(value_kind, str):
            # TYPE CHECK BEFORE SET MEMBERSHIP: a YAML list/map here is
            # unhashable and `in frozenset` would RAISE a TypeError out of the
            # validator (whose contract is to RETURN its errors) and surface as
            # an opaque dropped pack — the exact failure class this validation
            # exists to prevent.
            errors.append(
                f"{where}{f' {name!r}' if name is not None else ''}: "
                f"'valueKind' must be a string, got "
                f"{type(value_kind).__name__} {value_kind!r}"
            )
        elif value_kind is not None and value_kind not in VALUE_KINDS:
            errors.append(
                f"{where}{f' {name!r}' if name is not None else ''}: unknown "
                f"valueKind {value_kind!r} (allowed: {_ALLOWED_VALUE_KINDS})"
            )
        if "required" in entry and not isinstance(entry["required"], bool):
            errors.append(
                f"{where}{f' {name!r}' if name is not None else ''}: "
                f"'required' must be a boolean (got {entry['required']!r})"
            )
        if "cardinality" in entry and (
                not isinstance(entry["cardinality"], str)
                or entry["cardinality"] not in VALUE_CARDINALITIES):
            errors.append(
                f"{where}{f' {name!r}' if name is not None else ''}: "
                f"'cardinality' must be one of {_ALLOWED_CARDINALITIES} "
                f"(got {entry['cardinality']!r})"
            )
    return errors


def declared_value_fields(spec: object) -> list[dict]:
    """A kindDef spec's ``values``, NORMALIZED — or ``[]`` when it declares none.

    This is the compiler's reader, and it normalizes **without trusting** the
    validator: ``compile_value_brief`` reads raw manifest YAML (the filesystem
    catalog and the hosted tenant overlay alike) and must degrade a malformed
    section to absent rather than raising into the prompt path — the same
    contract ``value_extractor._extraction_hints`` follows. ``required``
    defaults to ``False`` and ``cardinality`` to ``"one"`` so the render is
    stable; entries that are not a map, or that lack a usable ``name`` /
    ``valueKind``, are skipped (the validator is what REPORTS them).

    The returned list preserves declaration order — it is prompt-visible, and
    a pack author's order is the order the model should see. A name containing
    a colon is SKIPPED here as well as refused by the validator (#2818/OD2:
    property names are bare): the compile reads raw YAML, including a tenant
    manifest that may never have passed the validator, so it must not be the
    path that puts a namespaced property into the prompt.
    """
    if not isinstance(spec, dict):
        return []
    raw = spec.get("values")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value_kind = entry.get("valueKind")
        if not isinstance(name, str) or not name.strip():
            continue
        # `isinstance(str)` BEFORE the set membership: a YAML list/dict here is
        # unhashable (`TypeError`) and the compiler must degrade, not raise.
        if not isinstance(value_kind, str) or value_kind not in VALUE_KINDS:
            continue
        # The compiler mirrors the validator's bare-identifier rule, on the
        # name AS WRITTEN (no strip): a name with a colon or edge whitespace
        # passes nothing to the prompt, and neither does one that is not a bare
        # identifier (the field name is rendered raw).
        if not is_bare_value_field_name(name) or name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "valueKind": value_kind,
            "required": entry.get("required") is True,
            "cardinality": ("many" if entry.get("cardinality") == "many"
                            else "one"),
        })
    return out


def value_field_annotations(value: dict) -> str:
    """``"money, required, many"``-style constraint text for ONE declaration.

    Shared by every renderer (the master render and the S2/S4 rule block) so
    the prompt spells a declaration the same way everywhere.
    """
    parts = [str(value.get("valueKind", ""))]
    if value.get("required") is True:
        parts.append("required")
    if value.get("cardinality") == "many":
        parts.append("many")
    name = str(value.get("name", ""))
    return f"{name}({', '.join(p for p in parts if p)})"
