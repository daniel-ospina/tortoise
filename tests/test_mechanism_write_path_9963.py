"""#9963 — the MECHANISM leg of the three-layer relation model (#7852) at the
`create_operator` write door.

The model is one triple (ONTOLOGY §8, #7852): **mechanism** (`IMPL` / `NAND` —
how it acts), **semantics** (what it means) and **direction** (which way it
runs). Before this file the door checked two of the three legs:

  * `label`/`semantics` MEMBERSHIP (`undeclared_relation`, #1934/#7551), and
  * the semantics → DIRECTION resolution (`direction_basis='semantics'`).

and never compared `op_type` to the mechanism the write's own meaning (or the
pack relation it names) DECLARES — even though the registry rows it already
reads carry `mechanism` (measured: `{'predicate': 'groundedIn', 'mechanism':
'IMPL', 'semantics': 'supports', …}`) and `SEMANTICS_MECHANISMS` states the
polarity-bearing meanings. So the one combination that INVERTS the logic —
"this evidence REFUTES that claim", spelled `op_type='IMPL'` — was accepted in
silence, EP raised the target's confidence, and the node recorded
`direction_basis: 'semantics'` (i.e. the declared meaning was honoured).

⛔ CONTRACT: the new warning is WARN-NOT-BLOCK. #1934's ruling (governance
D1/D2) is a recorded decision and this file pins it: every mismatched write
below must still be WRITTEN, with its `op_type` stored verbatim, and must
return a structured warning. A refusal here is a regression, not a fix.

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_mechanism_write_path_9963.py -q
"""
from __future__ import annotations

import logging

import pytest

from tortoise.pack_registry import (
    CORE_PREDICATES,
    SEMANTICS_MECHANISMS,
    SEMANTICS_VALUES,
    declared_mechanisms,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "mechanism.db"))
    yield s
    s.close()


def _two_points(sdk):
    return (
        sdk.create_point("statement", "the migration finished")["id"],
        sdk.create_point("statement", "the migration did not finish")["id"],
    )


def _mechanism_warnings(op: dict) -> list[dict]:
    return [w for w in op.get("warnings") or [] if w.get("code") == "mechanism_mismatch"]


def _stored_op_type(sdk, op_id: str) -> str:
    return sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.op_type", params={"id": op_id}
    ).result_set[0][0]


# ── the vocabulary invariant (no graph needed) ───────────────────────────

def test_mechanism_map_is_ontology_s8s_table():
    """The map IS ONTOLOGY §8's semantics table, mechanism column — the ruled
    table (#7852), not a derivation. Pinned verbatim so a vocabulary move
    cannot leave the map behind, and so the four `—` rows stay explicitly
    mechanism-free instead of silently acquiring one."""
    assert SEMANTICS_MECHANISMS == {
        "addresses": "IMPL",
        "causes": "IMPL",
        "dependsOn": "IMPL",
        "hasPart": "IMPL",
        "supports": "IMPL",
        "mutuallySupportive": "IMPL",
        "refutes": "NAND",
        "mutuallyExclusive": "NAND",
    }
    assert set(SEMANTICS_MECHANISMS) <= SEMANTICS_VALUES
    assert set(SEMANTICS_MECHANISMS.values()) <= CORE_PREDICATES
    # ONTOLOGY §8 gives these NO mechanism (`—`): structural/neutral meanings,
    # over which either mechanism may be spelled.
    assert SEMANTICS_VALUES - set(SEMANTICS_MECHANISMS) == {
        "related", "transacts", "ownedBy", "managedBy",
    }


# ── the named test from #9963 ─────────────────────────────────────────────

def test_semantics_refutes_on_impl_is_refused_or_warned(sdk):
    """The measured defect, verbatim: a write declaring `refutes` with the
    mechanism `IMPL` must not be silent (warn-not-block, per #1934).

    The assertion is MECHANISM-SPECIFIC on purpose: `op["warnings"]` alone is
    satisfied by any of the four producers on this path (the retired-word and
    out-of-vocabulary notices, `undeclared_relation`, or this one), so a bare
    truthiness check would keep passing if the mechanism leg disappeared."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="refutes", semantics="refutes")
    assert _mechanism_warnings(op), op.get("warnings")
    assert _mechanism_warnings(op)[0]["declared_mechanism"] == "NAND"


# ── the two stated contract points, pinned ───────────────────────────────

def test_part_whole_op_type_declares_no_mechanism(sdk):
    """A part/whole `op_type` is NEITHER mechanism, so there is nothing to
    compare and silence is the honest answer (ONTOLOGY §8 blesses
    `composedOf` + `hasPart`). The guard is `op_type in CORE_PREDICATES`; a
    tidy-up that drops or widens it would report `op_type 'composedOf'
    contradicts … 'IMPL'` on every such write, and before this test NOTHING
    failed — so the exclusion was a stated contract with zero coverage."""
    assert frozenset({"IMPL", "NAND"}) == CORE_PREDICATES
    a, b = _two_points(sdk)
    for op_type in ("composedOf", "decomposesInto", "contains", "wraps"):
        # `hasPart` and `supports` both declare IMPL: the loudest possible
        # declaration, so a leaked comparison would certainly fire.
        for semantics in ("hasPart", "supports"):
            op = sdk.create_operator(op_type, a, [b], semantics=semantics)
            assert not _mechanism_warnings(op), (op_type, semantics, op["warnings"])


def test_retired_word_is_compared_as_its_canonical_meaning(sdk):
    """`opposes` is RETIRED in favour of `mutuallyExclusive` (NAND), and a
    shipped pack still declares it — so the comparison must read the meaning the
    write will be RECORDED as, not the pre-canonical spelling. Compared
    pre-canonical, `opposes` is not in the map and the inversion goes silent
    again. The same word must behave identically whichever parameter carries it:
    `semantics='opposes'` warned from the first cut, `label='opposes'` did not,
    which is the same meaning disclosing differently by spelling."""
    a, b = _two_points(sdk)
    for kwargs in ({"semantics": "opposes"}, {"label": "opposes"}):
        op = sdk.create_operator("IMPL", a, [b], **kwargs)
        warns = _mechanism_warnings(op)
        assert warns, (kwargs, op.get("warnings"))
        assert warns[0]["declared_mechanism"] == "NAND", (kwargs, warns[0])
        # ... and the NAND spelling of the same meaning agrees, so the warning
        # above is about the meaning, not about the parameter.
        op_ok = sdk.create_operator("NAND", a, [b], **kwargs)
        assert not _mechanism_warnings(op_ok), (kwargs, op_ok.get("warnings"))


def test_pack_declaration_outranks_s8_neutrality(sdk):
    """A pack relation's own `mechanism` is authoritative for its PREDICATE even
    when its `semantics` is one ONTOLOGY §8 gives no mechanism: `marks`
    (investment-portfolio-manager) declares `mechanism: IMPL` over
    `semantics: related`, and §8's `—` column is about the MEANING, not about a
    pack's ruling on its own verb. The two rules are reconciled by the pack
    leg winning, and this test PINS that choice so it cannot drift silently in
    either direction (the alternative — skipping rows whose semantics is
    mechanism-free — would let a NAND `marks` edge through unwarned)."""
    assert "related" not in SEMANTICS_MECHANISMS  # §8 gives the meaning none
    assert declared_mechanisms(label="marks") == [
        ("IMPL", "the pack relation 'marks' (portfolio)")]
    a, b = _two_points(sdk)
    warns = _mechanism_warnings(sdk.create_operator("NAND", a, [b], label="marks"))
    assert warns, "the pack says IMPL, so a NAND `marks` is the same inversion"
    assert warns[0]["declared_mechanism"] == "IMPL"
    assert not _mechanism_warnings(sdk.create_operator("IMPL", a, [b], label="marks"))


# ── "NEVER raises": the fail-open path, exercised ────────────────────────

class _StubRegistry:
    """The `registry=` seam a second creator would use (#9963): the warn-not-
    block path must survive a registry that will not load or yields junk."""

    def __init__(self, rows=None, raises=None):
        self._rows = rows
        self._raises = raises

    def list_relations(self):
        if self._raises is not None:
            raise self._raises
        return self._rows


@pytest.mark.parametrize("registry", [
    _StubRegistry(raises=RuntimeError("the walk will not run")),
    _StubRegistry(rows=["not-a-row"]),                       # non-dict row
    _StubRegistry(rows=[{"predicate": "marks", "mechanism": ["IMPL"]}]),
    _StubRegistry(rows=[{"predicate": "marks", "mechanism": {"kind": "IMPL"}}]),
], ids=["walk-raises", "row-not-a-dict", "mechanism-list", "mechanism-dict"])
def test_declared_mechanisms_never_raises_on_a_broken_registry(registry):
    """The docstring promises this function NEVER raises, because it runs on the
    warn-not-block write path: an exception here aborts the caller's WRITE from
    a comparison that is documented never to refuse one. Only the load used to
    be guarded — the row walk and `mechanism in CORE_PREDICATES` (a frozenset
    membership test, which raises `TypeError` on an unhashable value) were
    OUTSIDE it. Each case here RAISES on the pre-fix code (AttributeError /
    TypeError), so this test is the non-vacuous proof of the guard."""
    assert declared_mechanisms(label="marks", registry=registry) == []


@pytest.mark.parametrize("registry", [
    _StubRegistry(rows=["not-a-row"]),
    _StubRegistry(rows=[{"predicate": ["x"], "mechanism": "IMPLISH"}]),
    # These REACH the row-mechanism key: the label must equal the row's
    # predicate for the walk to get that far, so a case whose predicate is a
    # plain string short-circuits at `predicate != label` and proves nothing
    # about that key being hashable.
    _StubRegistry(rows=[{"predicate": ["x"], "mechanism": ["IMPL"]}]),
    _StubRegistry(rows=[{"predicate": ["x"], "mechanism": None}]),
], ids=["row-not-a-dict", "row-mechanism-word", "row-mechanism-key",
        "row-mechanism-null"])
def test_an_unhashable_label_never_raises(registry):
    """The same NEVER-raises contract covers the ARGUMENTS (the docstring says so
    explicitly), and the warn-once KEYS are inside it: a list `label` raises
    `TypeError` the moment a key containing it is hashed — first out of the walk,
    then again out of the `except` that catches it, so it ESCAPES the function
    and aborts the caller's write. Keys therefore carry a GUARDED repr. Every
    case below reaches a key of its own and raised on the version that put the
    raw `label` in one."""
    assert declared_mechanisms(label=["x"], registry=registry) == []


def test_a_different_failure_class_on_the_walk_is_reported_too(caplog):
    """The load/walk keys carry the exception CLASS: `_WARN_ONCE` is process-
    lifetime, so keyed on the arm alone a later, different failure would be
    silenced by the first and the surviving diagnostic would name a cause that
    no longer applies."""
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(
            label="marks", registry=_StubRegistry(raises=RuntimeError("one"))) == []
        assert declared_mechanisms(
            label="marks", registry=_StubRegistry(raises=ValueError("two"))) == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("RuntimeError" in m for m in msgs), caplog.text
    assert any("ValueError" in m for m in msgs), caplog.text


class _UnhashableStr(str):
    """Legal Python: a `str` SUBCLASS with no usable `__hash__`. The signature
    admits it, so the NEVER-raises contract covers it — and it is why a key
    component is normalised to an exact `str`."""

    __hash__ = None


class _SubclassReprStr(str):
    """`repr()` returning a `str` SUBCLASS is legal; if that subclass is also
    unhashable, a key holding it raises. The key builder must return an EXACT
    `str`, not merely something `isinstance(..., str)`."""

    def __repr__(self):
        return _UnhashableStr("subclass repr")


class _BoomStr(str):
    """A `str` SUBCLASS whose `__str__` raises. The RETURN path builds `source`
    with f-strings, which call `str()` — so without a guard the caller's write
    aborts from the READ half, after the warn keys were made safe."""

    def __str__(self):
        raise RuntimeError("str boom")


def test_a_str_subclass_whose_str_raises_never_raises():
    """Both return-path f-strings (the pack-row `source` and the Layer-2
    `source`) and the semantics leg are exercised: `_safe_text` keeps a plain
    `str` byte-identical and falls back to the guarded repr otherwise."""
    got = declared_mechanisms(label=_BoomStr("mutuallyExclusive"), registry=None)
    assert got and got[0][0] == "NAND", got
    rows = [{"predicate": _BoomStr("marks"), "mechanism": "IMPL", "pack": "p"}]
    got2 = declared_mechanisms(label=_BoomStr("marks"),
                              registry=_StubRegistry(rows=rows))
    assert got2 and got2[0][0] == "IMPL", got2


def test_a_str_subclass_with_no_hash_never_raises(caplog):
    """Three argument shapes that are legal but hostile, each of which raised on
    some version of this walk: an unhashable `str` as the WORD (`_canonical`'s
    dict lookup), the same as the LABEL (a key component), and a label whose
    `repr()` returns an unhashable `str` subclass (the key component again). The
    lookup failure is LOGGED, not merely swallowed — an argument this walk
    cannot read declares nothing, which is a degradation like any other."""
    word = _UnhashableStr("marks")
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label=word, registry=_StubRegistry(rows=["junk"])) == []
        assert declared_mechanisms(label=word, registry=None) == []
        assert declared_mechanisms(
            label=_SubclassReprStr("marks"), registry=_StubRegistry(rows=["junk"])) == []
    assert any("an argument this lookup cannot read" in rec.getMessage()
               for rec in caplog.records), caplog.text


class _ExplodingRow(dict):
    """A row that is a `dict` (so the isinstance guard passes it) but whose
    `.get` raises — the shape that used to truncate the walk SILENTLY. `exc`
    keeps the row TYPE constant while varying the failure class, so the key's
    `type(exc).__name__` component can be isolated."""

    def __init__(self, exc=None):
        super().__init__()
        self._exc = exc or RuntimeError("the row exploded")

    def get(self, *args, **kwargs):
        raise self._exc


def test_two_differently_typed_bad_rows_under_one_label_both_speak(caplog):
    """The row-type key carries the ROW TYPE as well as the lookup: two rows of
    different kinds under one predicate are two degradations. Without
    `type(rel).__name__` in the key the second would be suppressed."""
    rows = [KeyError, "not-a-row"]                 # types: 'type' and 'str'
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label="marks", registry=_StubRegistry(rows=rows)) == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("not a mapping (type)" in m for m in msgs), caplog.text
    assert any("not a mapping (str)" in m for m in msgs), caplog.text


def test_two_failure_classes_on_one_row_arm_both_speak(caplog):
    """The row-`except` key carries the exception CLASS. Both rows here are the
    SAME type, so the two keys differ ONLY by the exception class: without
    `type(exc).__name__` in the key the second is suppressed."""
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label="marks", registry=_StubRegistry(
            rows=[_ExplodingRow(RuntimeError("one"))])) == []
        assert declared_mechanisms(label="marks", registry=_StubRegistry(
            rows=[_ExplodingRow(KeyError("two"))])) == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("RuntimeError" in m for m in msgs), caplog.text
    assert any("KeyError" in m for m in msgs), caplog.text


@pytest.mark.parametrize("registry,needle", [
    (_StubRegistry(raises=RuntimeError("the walk will not run")),
     "pack relations could not be read"),
    (_StubRegistry(rows=[_ExplodingRow()]),
     "unreadable pack relation row"),
    # The two arms BELOW are the P2 the 2026-10-25 review found at
    # `4b72a44e3`: the non-mapping row was a bare `continue` and an unusable
    # `mechanism` was discarded inside `_add` — neither logged — while the
    # docstring ALREADY claimed "every degradation is LOGGED … never silent".
    # A registry that yields non-mapping rows loses the entire pack-declared
    # half of the disclosure and left no instrument anywhere reporting it.
    (_StubRegistry(rows=["not-a-row"]),
     "not a mapping"),
    (_StubRegistry(rows=[{"predicate": "marks", "mechanism": ["IMPL"]}]),
     "cannot use"),
    (_StubRegistry(rows=[{"predicate": "marks", "mechanism": {"kind": "IMPL"}}]),
     "cannot use"),
    (_StubRegistry(rows=[{"predicate": "marks", "mechanism": "IMPLISH"}]),
     "cannot use"),
    # PRESENT and null is not the same as ABSENT: an explicit `mechanism: null`
    # cannot reach a shipped pack (validation rejects it at load), but the
    # `registry=` seam admits it and `list_relations` copies an authored null
    # through — so it is 'present but unusable', the arm the docstring logs.
    (_StubRegistry(rows=[{"predicate": "marks", "mechanism": None}]),
     "cannot use"),
], ids=["relations-raise", "row-raises", "row-not-a-dict", "mechanism-list",
        "mechanism-dict", "mechanism-unknown-word", "mechanism-null"])
def test_a_degraded_pack_walk_is_logged_not_swallowed(registry, needle, caplog):
    """Degrade AND SAY SO (the norm `domain_loader.py:103` states for exactly
    this situation): the pack half of the disclosure is the only thing that
    reports a pack-declared inversion, so a `list_relations()` that starts
    raising must not disable it permanently and invisibly. Both failure arms
    were a bare `except Exception: pass` — nothing logged, so no instrument
    anywhere could report the gap. Pre-fix this test finds NO warning record.

    The last four ids are the follow-up P2: the walk's OTHER two degradations
    (a row that is not a mapping, a row declaring a mechanism this walk cannot
    use) were silent too. "Logged" is a claim about the CODE, so every arm is
    exercised here rather than the two that happened to be raising."""
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label="marks", registry=registry) == []
    assert any(needle in rec.getMessage() for rec in caplog.records), caplog.text


def test_a_row_carrying_no_mechanism_is_not_a_degradation(caplog):
    """The ONE silent drop that is correct: an old-format row with no
    `mechanism` key (the four mechanism-free `project-management` relations)
    declares nothing, so nothing is lost and nothing is logged. This pins the
    new arm on KEY PRESENCE (`"mechanism" in rel`) rather than on `_usable`
    alone — keying it on unusability would fire on every old-format row of
    every shipped pack, i.e. exactly the noise that gets warnings switched off."""
    rows = [{"predicate": "marks", "semantics": "related"},          # no mechanism
            {"predicate": "marks", "mechanism": "IMPL", "pack": "portfolio"}]
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        got = declared_mechanisms(label="marks", registry=_StubRegistry(rows=rows))
    assert got == [("IMPL", "the pack relation 'marks' (portfolio)")], got
    assert not [r for r in caplog.records
                if "declared_mechanisms" in r.getMessage()], caplog.text


def test_two_distinct_unusable_mechanisms_are_BOTH_reported(caplog):
    """The warn-once KEY must carry the DECLARATION VALUE, not just the lookup.
    `_WARN_ONCE` is a module-level set never cleared in production, so a key
    without `repr(mechanism)` let the first bad word of a predicate suppress
    every later one for the life of the process — the silence this change
    exists to remove, one row later. Both calls below share the label, so the
    VALUE is the only thing that differs."""
    def _lookup(value):
        return declared_mechanisms(
            label="marks",
            registry=_StubRegistry(rows=[{"predicate": "marks",
                                          "mechanism": value}]))

    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert _lookup("IMPLISH") == []
        assert _lookup("NANDY") == []
        msgs = [r.getMessage() for r in caplog.records]
        assert any("IMPLISH" in m for m in msgs), caplog.text
        assert any("NANDY" in m for m in msgs), caplog.text
        # ... and an EXACT repeat is still deduped: warn-ONCE, not warn-always.
        before = len([r for r in caplog.records if "cannot use" in r.getMessage()])
        assert _lookup("NANDY") == []
        after = len([r for r in caplog.records if "cannot use" in r.getMessage()])
        assert after == before, caplog.text


def test_the_same_bad_word_under_a_second_label_is_reported_too(caplog):
    """The other half of the key: the same unusable word declared by a DIFFERENT
    predicate is a different degradation (the message names the predicate), so
    dropping `label` from the key must fail here."""
    rows = [{"predicate": "marks", "mechanism": "IMPLISH"},
            {"predicate": "groundedIn", "mechanism": "IMPLISH"}]
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label="marks", registry=_StubRegistry(rows=rows)) == []
        assert declared_mechanisms(label="groundedIn", registry=_StubRegistry(rows=rows)) == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("'marks'" in m and "IMPLISH" in m for m in msgs), caplog.text
    assert any("'groundedIn'" in m and "IMPLISH" in m for m in msgs), caplog.text


def test_a_bad_row_under_a_second_label_is_reported_too(caplog):
    """Same key-granularity defect on the non-mapping arm: the message names
    the LOOKUP, so the lookup has to be in the key as well."""
    rows = [KeyError, "not-a-row"]
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label="marks", registry=_StubRegistry(rows=rows)) == []
        assert declared_mechanisms(label="groundedIn", registry=_StubRegistry(rows=rows)) == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("looking up 'marks'" in m for m in msgs), caplog.text
    assert any("looking up 'groundedIn'" in m for m in msgs), caplog.text


def test_a_registry_that_would_not_load_is_reported(monkeypatch, caplog):
    """The LOAD arm is the one no other case here can reach: every other test
    supplies `registry=`, so `_get_registry()` is never called and a later lane
    could delete the load's logging while the suite stayed green. "Logged" is a
    claim about the code, so the arm gets its own case. The ids that drive the
    WALK's guard are named `walk-raises`/`relations-raise` for the same reason:
    a case called `load-raises` that raises from `list_relations()` names an arm
    it does not execute."""
    from tortoise import domain_loader

    def _boom(*_a, **_kw):
        raise RuntimeError("no registry for you")

    def _boom_other(*_a, **_kw):
        raise ValueError("a DIFFERENT load failure")

    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        monkeypatch.setattr(domain_loader, "_get_registry", _boom)
        assert declared_mechanisms(label="marks") == []
        # ... and the key carries the exception CLASS, so a LATER, different load
        # failure is not silenced by the first for the process lifetime.
        monkeypatch.setattr(domain_loader, "_get_registry", _boom_other)
        assert declared_mechanisms(label="marks") == []
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("would not load" in m and "RuntimeError" in m for m in msgs), caplog.text
    assert any("would not load" in m and "ValueError" in m for m in msgs), caplog.text


def test_an_unreadable_row_under_a_second_label_is_reported_too(caplog):
    """The per-row `except` arm's key carries the LOOKUP and the exception
    class: `_WARN_ONCE` is process-lifetime, so keying it on the row's type
    alone let the first unreadable row of a process suppress every later one
    under a DIFFERENT predicate — the same silence, on the arm that already
    logs."""
    rows = [_ExplodingRow()]
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        assert declared_mechanisms(label="marks", registry=_StubRegistry(rows=rows)) == []
        assert declared_mechanisms(label="groundedIn", registry=_StubRegistry(rows=rows)) == []
    needles = ["unreadable pack relation row for 'marks'",
               "unreadable pack relation row for 'groundedIn'"]
    for needle in needles:
        assert any(needle in rec.getMessage() for rec in caplog.records), (needle, caplog.text)


def test_an_unreadable_row_does_not_truncate_the_walk(caplog):
    """A row this walk cannot read must not discard the rows AFTER it: the
    caller renders the result as the mechanism the write declares, so a
    truncated walk reads as a complete one. One guard around the whole loop
    kept whatever had been read BEFORE the bad row and said nothing — the same
    disclosure silently losing a declaration. Per-row isolation reads on, and
    the skipped row is named in the log."""
    rows = [_ExplodingRow(),
            {"predicate": "marks", "mechanism": "IMPL", "pack": "portfolio"}]
    with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
        got = declared_mechanisms(label="marks", registry=_StubRegistry(rows=rows))
    assert got == [("IMPL", "the pack relation 'marks' (portfolio)")], got
    assert any("unreadable pack relation row" in rec.getMessage()
               for rec in caplog.records), caplog.text


def test_the_mechanism_leg_binds_its_own_emit_violation(sdk, monkeypatch):
    """The mechanism block must not DEPEND on an earlier block's import.
    `emit_violation` was bound only inside `if label:` / `if semantics is not
    None:`, so the block worked solely because `declared_mechanisms` returns
    `[]` when both are absent — a coupling no static checker sees. Its failure
    mode is a `NameError` raised BEFORE the node is written, i.e. a REFUSED
    write on the path this block documents as warn-not-block. Forcing a
    contradiction with neither argument supplied reproduces exactly that: on the
    pre-fix code this write raises `NameError` and nothing is written."""
    import tortoise.pack_registry as pack_registry

    monkeypatch.setattr(
        pack_registry, "declared_mechanisms",
        lambda **_: [("NAND", "a stub declaration (no label, no semantics)")],
    )
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b])          # no label, no semantics
    warns = _mechanism_warnings(op)
    assert warns, op.get("warnings")
    assert warns[0]["declared_mechanism"] == "NAND"
    assert _stored_op_type(sdk, op["id"]) == "IMPL"   # warn-not-block intact


def test_a_known_absent_registry_is_not_reloaded():
    """`registry=None` means "there is no registry" — a caller that has already
    ruled one out must not trigger a second `_get_registry()` load by saying so.
    An `object | None = None` signature cannot tell that apart from "not
    supplied", and the difference is observable: with the real registry loaded,
    `marks` declares IMPL."""
    assert declared_mechanisms(label="marks", registry=None) == []
    assert declared_mechanisms(label="marks")          # unset -> loads the real one


@pytest.mark.parametrize("kwargs", [
    {"label": ["refutes"]},
    {"semantics": {"refutes": 1}},
], ids=["label-list", "semantics-dict"])
def test_unhashable_argument_declares_nothing_instead_of_raising(kwargs):
    """The NEVER-raises contract covers the ARGUMENTS, not only the registry.
    `SEMANTICS_TRANSITION.get` is a dict lookup, so an unhashable `label` or
    `semantics` raised `TypeError` — out of a comparison documented never to
    refuse a write. Non-`str` is not a meaning, so it declares nothing.

    Non-vacuous: on the pre-fix code each case raises `TypeError: unhashable
    type` here. Unreachable from `create_operator` (it tests the same values
    against a set/dict first) — but the `registry=` seam exists for a second
    creator, and this is the arm that would carry the bug."""
    assert declared_mechanisms(registry=None, **kwargs) == []


def test_a_bad_argument_does_not_suppress_a_good_one():
    """Degrading on one argument must not discard the other: a malformed
    `semantics` beside a readable `label` still discloses what the label
    declares. The guard is per-argument, not a whole-function bail-out."""
    assert declared_mechanisms(
        label="refutes", semantics=["refutes"], registry=None,
    ) == [("NAND", "the label 'refutes' (a Layer-2 meaning)")]


# ── the message must not misdescribe the direction ───────────────────────

def test_message_describes_each_direction_correctly(sdk):
    """One hardcoded example only fits one of the two directions. The suite
    writes `NAND` + `supports` as well (see `test_mismatch_still_writes`), and
    the IMPL-worded sentence told that caller the opposite of what it had done.
    Each direction gets its own worked example, and each is asserted."""
    a, b = _two_points(sdk)
    impl_case = _mechanism_warnings(
        sdk.create_operator("IMPL", a, [b], semantics="refutes"))[0]["message"]
    nand_case = _mechanism_warnings(
        sdk.create_operator("NAND", a, [b], semantics="supports"))[0]["message"]
    # Assert the CLAIM each sentence makes about the WIRE's effect, and the
    # absence of the opposite claim. The earlier form asserted `"RAISES" not in
    # nand_case.replace("must RAISE it", "")` — a no-op in both halves: the NAND
    # sentence never spells `RAISES` (it says `must RAISE it`, and the replace
    # target therefore never occurs in the text the assertion can reject), so
    # that line could not fail for any implementation. Meanwhile both sentences
    # legitimately name the OTHER verb to describe what the MEANING demands
    # ("the meaning says it must LOWER it"), so the discriminating pair is the
    # claim phrase with its object (`RAISES the target's confidence`), which
    # appears only in the sentence for that direction.
    assert "IMPL wire" in impl_case, impl_case
    assert "RAISES the target's confidence" in impl_case, impl_case
    assert "LOWERS the target's confidence" not in impl_case, impl_case
    assert "NAND wire" in nand_case, nand_case
    assert "LOWERS the target's confidence" in nand_case, nand_case
    assert "RAISES the target's confidence" not in nand_case, nand_case


# ── the mechanism leg: meaning → mechanism ────────────────────────────────

def test_semantics_refutes_on_impl_names_the_declared_mechanism(sdk):
    """The warning is SPECIFIC: it names the mechanism the meaning declares,
    the mechanism the caller passed, and where the vocabulary lives — a bare
    "something is wrong" leaves the caller nothing to act on."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], semantics="refutes")
    warns = _mechanism_warnings(op)
    assert warns, op.get("warnings")
    message = warns[0]["message"]
    assert "'NAND'" in message, message          # what the meaning declares
    assert "'IMPL'" in message, message          # what the caller wrote
    assert "SEMANTICS_MECHANISMS" in message, message  # where the vocabulary lives
    assert "warn-not-block" in message, message


def test_label_spelling_is_compared_too(sdk):
    """The mirror spelling from the issue: the meaning rides the `label` while a
    DIFFERENT meaning arrives as `semantics`. Both are read, so the label's
    mechanism is compared even though the direction came from `semantics` — a
    contradictory `semantics` must not hide it."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="refutes", semantics="supports")
    assert _mechanism_warnings(op), op.get("warnings")


def test_label_alone_is_read_as_a_meaning(sdk):
    """The pre-#7852 spelling with NO `semantics` at all: the Layer-2 word rides
    the label and nothing else is declared. That is the case the sibling's
    docstring used to CLAIM while passing `semantics="supports"` — the arm was
    untested, and `refutes` is in the core label vocabulary, so the only
    declaration in play is the meaning itself."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="refutes")
    warns = _mechanism_warnings(op)
    assert warns, op.get("warnings")
    assert warns[0]["declared_mechanism"] == "NAND"


def test_pack_declared_relation_mechanism_is_compared(sdk):
    """A pack-declared predicate carries a declared `mechanism` — the datum the
    registry rows already hold. `competesWith` (product-strategy) declares NAND,
    so an IMPL write of it is the same logic inversion."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="competesWith")
    warns = _mechanism_warnings(op)
    assert warns, op.get("warnings")
    assert "NAND" in warns[0]["message"], warns[0]["message"]


def test_agreeing_mechanism_does_not_warn(sdk):
    """Discrimination: a write whose mechanism AGREES with its declared meaning
    (`supports` → IMPL, a pack relation whose declared mechanism is IMPL) stays
    silent. A warning on every write carries no information (#7551's lesson)."""
    a, b = _two_points(sdk)
    for kwargs in ({"semantics": "supports"}, {"label": "supports"},
                   {"label": "fixes"}, {"label": "causes"}):
        op = sdk.create_operator("IMPL", a, [b], **kwargs)
        assert not _mechanism_warnings(op), (kwargs, op.get("warnings"))


def test_neutral_meaning_does_not_pin_a_mechanism(sdk):
    """The FOUR meanings ONTOLOGY §8 gives no mechanism (`related`,
    `transacts`, `ownedBy`, `managedBy`) must never be reported: they are
    structural/neutral, so neither spelling contradicts them. Reporting one
    would invent an ontology ruling the model never made."""
    a, b = _two_points(sdk)
    for semantics in ("related", "transacts", "ownedBy", "managedBy"):
        assert semantics not in SEMANTICS_MECHANISMS, semantics
        for op_type in ("IMPL", "NAND"):
            op = sdk.create_operator(op_type, a, [b], semantics=semantics)
            assert not _mechanism_warnings(op), (op_type, semantics, op["warnings"])


def test_a_structural_meaning_still_declares_its_mechanism(sdk):
    """Discrimination against a polarity-only reading of the defect: ONTOLOGY
    §8 gives `hasPart`/`addresses`/`dependsOn` IMPL too, so a NAND write of one
    is the same inversion and must be reported. Without this, an implementation
    that only mapped `supports`/`refutes` would pass the tests above."""
    a, b = _two_points(sdk)
    for semantics in ("hasPart", "addresses", "dependsOn", "causes"):
        op = sdk.create_operator("NAND", a, [b], semantics=semantics)
        assert _mechanism_warnings(op), (semantics, op.get("warnings"))
        # ... and the agreeing direction stays silent.
        op2 = sdk.create_operator("IMPL", a, [b], semantics=semantics)
        assert not _mechanism_warnings(op2), (semantics, op2.get("warnings"))


# ── the #1934 contract: WARN-NOT-BLOCK ───────────────────────────────────

@pytest.mark.parametrize("op_type,declared", [("IMPL", "NAND"), ("NAND", "IMPL")])
def test_mismatch_still_writes(sdk, op_type, declared):
    """#1934 is a RECORDED DECISION: warn-not-block. The write must land with
    the caller's `op_type` stored VERBATIM — the warning is the disclosure, not
    a refusal (#9963 explicitly does not convert this into a hard refusal)."""
    a, b = _two_points(sdk)
    semantics = "refutes" if declared == "NAND" else "supports"
    op = sdk.create_operator(op_type, a, [b], semantics=semantics)
    assert op.get("id"), "warn-not-block: the write must proceed"
    assert _stored_op_type(sdk, op["id"]) == op_type
    assert _mechanism_warnings(op), op.get("warnings")
