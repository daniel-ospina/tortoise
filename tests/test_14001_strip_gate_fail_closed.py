"""`#14001` — the strip gate's stop arm must be FAIL-CLOSED, and must prove it.

The defect these pins close, in one sentence: `tools/strip_graph_entity_vectors.py`'s write
gate asked *"is there a store I RECOGNISE?"*, so every shape it did not model answered "no
store" — and the writer was certified write-stopped while its stop arm placed the vector. Six
issues in the same tool, days apart (`#13864`, `#13910`, `#13915`, `#13920`, `#13936`, and the
`#13806` population they consolidated into `#14001`), each added one row to the recogniser and
each opened the next hole.

`#14001`'s DEFENCE 2 inverts the polarity: `gated=True` only for an arm the gate can PROVE
places nothing, and everything else — including syntax it has never seen — REFUSES. These
tests pin the inversion at the two places a scanner can be fooled:

* the verdict, through the tool's own `_writer_consults_gate` (what `REFUSE_WRITE_STILL_EMITS`
  actually reads), and
* the RUNTIME ORACLE — the same writer source, EXECUTED with the gate answering STOP and a
  sentinel vector, so "the vector lands" is a measurement rather than an argument. Every
  regression row asserts the ONE direction that matters: an oracle that lands may never be
certified stopped. (The reverse — oracle clean, gate refusing — is a permitted over-refusal and
is pinned per row in `test_13629_entity_vector_retire.py`'s `over_refused` tuple.)

⛔ HERMETIC: no graph, no store, no DSN, no model call, no network. The writer sources are
synthetic modules written to `tmp_path` (the idiom `test_13629_entity_vector_retire.py`
established), and the oracle `exec`s the same text in this process.
"""
from __future__ import annotations

import ast
import contextlib
import re
import sys
import types

import pytest

import tools.strip_graph_entity_vectors as strip

# ── harness ─────────────────────────────────────────────────────────────────


#: The sentinel the oracle's `_encode` returns. Identity — not equality — is the test: a
#: writer that stores it leaves the OBJECT in `props`, whatever its shape.
VECTOR = object()


def _writer_module(tmp_path, monkeypatch, body: str, name: str, extra: str = "") -> str:
    """Register a synthetic writer module (the shape `test_13629` uses) and return its name."""
    path = tmp_path / f"{name}.py"
    path.write_text("def _upsert_object(props, ev" + extra + "):\n" + body)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    strip._writer_sources().pop(name, None)          # a fresh AST read per case
    return name


def _gate(tmp_path, monkeypatch, body: str, name: str,
          extra: str = "") -> tuple[bool, str]:
    """`(gated, why)` from the tool's own gate — the boolean `REFUSE_WRITE_STILL_EMITS` reads."""
    module = _writer_module(tmp_path, monkeypatch, body, name, extra)
    return strip._writer_consults_gate(module, "_upsert_object", ("object",))


def _record_write(*args, **kwargs) -> None:
    """The write channel: records what it is HANDED, so a rebuild-then-write is measurable.

    ⛔ A GATE CAN BE FOOLED BY A MAPPING THE ARM BUILDS AND HANDS ON (`#14001`, round-8
    review): `params = {**props, k: vec}; _write(params)` leaves `props` untouched, so an oracle
    that only inspects `props` cannot see the store. The handed arguments are part of the
    measurement.
    """
    # ⛔ DO NOT CLEAR HERE (`#14001`, measured by the cycle-4 review): `_oracle` resets per run, and
    # clearing on entry meant a handoff-then-`_write(props)` arm recorded only the LAST call — the
    # false "documents nothing" that would get a real row deleted on a false premise. PINNED by
    # `REBUILD_RESIDUAL_ROWS["rebuilt-mapping-handoff-then-rehandoff"]`, whose stop arm calls this
    # channel twice; restoring `_HANDED.clear()` here reds that row and nothing else (the rebase
    # review measured that nothing else reached it, so the row is what makes the change non-inert).
    _HANDED.extend(args)
    # ⛔ A KEYWORD HANDOFF IS THE SAME HANDOFF (`#14001`, measured by the review of this branch):
    # `_write(props={**props, k: vec})` certifies and lands, and an oracle that recorded only
    # `args` reported `lands=False` — the direction that turns a pin vacuous.
    _HANDED.extend(kwargs.values())


_HANDED: list = []


def _oracle(body: str, extra: str = "", call_kwargs: dict | None = None) -> dict:
    """EXECUTE the writer with the gate answering STOP and report whether the vector lands.

    ⛔ THIS IS THE MEASUREMENT, NOT A SECOND OPINION. Every fail-open in this series was proved
    by running the writer; the same source the gate scans is compiled and called here, with
    `_derived_mirror_required` answering `False` (the arm a strip can actually race) and
    `_encode` returning the sentinel. `props` is a plain dict, so `props["embedding"] is
    VECTOR` answers "did this writer store the property on the stop arm" exactly.

    ⛔ `call_kwargs` IS PART OF THE WRITER'S CONTRACT, not a harness convenience: a stop arm that
    forwards a CALLER-SUPPLIED mapping to the write channel (`_write({**props, **extra})`) places
    the vector exactly when that mapping carries it, so an oracle that only ever passes `None`
    cannot see the store — the caller argument is the measurement.
    """
    source = "def _upsert_object(props, ev" + extra + "):\n" + body
    namespace: dict = {
        "key": "embedding",
        "_derived_mirror_required": lambda label: False,
        "mirror_required": lambda label: False,
        "_encode": lambda ev: VECTOR,
        "_write": _record_write,
        "_f": lambda: True,
        "_m": lambda: "model",
        "_keys": lambda: ["embedding"],
        "_flag": lambda ev: True,
        # the adversarial manager: it hands `k` the PROPERTY, which is exactly why a
        # `with … as` target must poison the binding rather than be ignored
        "_ctx": lambda: contextlib.nullcontext("embedding"),
        "ChainMap": __import__("collections").ChainMap,
        "_Skip": type("_Skip", (Exception,), {}),
    }
    exec(compile(source, "<oracle>", "exec"), namespace)
    props: dict = {}
    _HANDED.clear()
    with contextlib.suppress(Exception):   # a raising writer placed what it placed
        namespace["_upsert_object"](props, {"x": 1}, **(call_kwargs or {}))
    handed = any(isinstance(item, dict) and item.get("embedding") is VECTOR
                 for item in _HANDED)
    return {"lands": props.get("embedding") is VECTOR or handed, "props": props}


def _stage_one_sees_a_store(body: str) -> bool:
    """Does the OLD question — the store vocabulary alone — recognise a store in this arm?

    `_stores_embedding(..., anywhere=True)` is stage 1 of the verdict: the recogniser, before
    `#14001`'s allow-list. Reading it directly is how these tests stay non-vacuous — a row
    whose store stage 1 ALREADY sees is refused by both polarities and proves nothing about
    the inversion.
    """
    tree = ast.parse("def _upsert_object(props, ev):\n" + body)
    function = tree.body[0]
    scope = function
    return any(strip._stores_embedding(statement, anywhere=True, scope=scope)
               for statement in function.body)


# ── the red test that matters: a shape the gate has never seen ───────────────


#: ⛔ INVENTED FOR THIS TEST, and deliberately unlike every row in the issue's table: the key is
#: BUILT at runtime (an f-string over two halves, so no `"embedding"` literal appears in the
#: source), and the store goes through the FUNCTIONAL `dict.__ior__` form with a `dict([…])`
#: pairs builder. No recogniser in the tool models any of those three things.
UNSEEN_SHAPE = """
    k = f"{'embed'}{'ding'}"
    if not _derived_mirror_required("object"):
        props.__ior__(dict([(k, _encode(ev))]))
        return None
    _write(props)
"""


def test_a_shape_the_gate_has_never_seen_REFUSES(tmp_path, monkeypatch):
    """⛔ THE ACCEPTANCE TEST OF `#14001`, and the one that was RED on the pre-fix tree.

    Pre-fix this writer was **CERTIFIED**: `_stores_embedding` sees no literal `"embedding"`
    key (the key is an f-string), no `|=`/`update` operand it can read, and no assignment it
    recognises — so the stop arm answered "no store" and the gate lifted
    `REFUSE_WRITE_STILL_EMITS` on a writer that places the vector. The two assertions below
    are the whole inversion, in order:

    1. stage 1 (the recogniser) does NOT see this store — that is the vacuity check: if it
       did, this row would be refused by the old code too and prove nothing;
    2. the gate REFUSES it, because the closed vocabulary cannot PROVE the key is not the
       property.
    """
    assert _stage_one_sees_a_store(UNSEEN_SHAPE) is False, (
        "this row no longer tests the inversion: the stage-1 recogniser already refuses it")
    gated, why = _gate(tmp_path, monkeypatch, UNSEEN_SHAPE, "unseen_14001")
    assert gated is False, f"an invented, unmodelled key shape was CERTIFIED: {why}"
    assert "closed vocabulary" in why or "unproven" in why, (
        f"the refusal must name the unmatched condition, not just say no: {why}")
    assert _oracle(UNSEEN_SHAPE)["lands"] is True, (
        "non-vacuity of the row itself: the writer must really store the vector")


#: Further spellings the tool models NOWHERE, one per family. Each is refused for the same
#: reason as `UNSEEN_SHAPE` — none of them is in the closed vocabulary.
UNRECOGNISED_KEY_SHAPES = (
    # a key built by `str.join` over a closed set (not a literal, not a Name, not a method)
    """
    k = "".join(["embed", "ding"])
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
""",
    # a key from a name bound OUTSIDE the writer (a module-level constant, a parameter): the
    # binding walk has no value for it, so the key is unproven. (The attribute family is pinned
    # by `test_an_unmodelled_expression_is_UNPROVEN_rather_than_clean`.)
    """
    if not _derived_mirror_required("object"):
        props[SENTINEL_KEY] = _encode(ev)
        return None
    _write(props)
""",
    # a key from `%`-formatting
    """
    k = "%s%s" % ("embed", "ding")
    if not _derived_mirror_required("object"):
        props.setdefault(k, _encode(ev))
        return None
    _write(props)
""",
)


def test_unrecognised_key_shapes_are_refused_not_certified(tmp_path, monkeypatch):
    """The default is REFUSE — measured across three unmodelled families, not one."""
    for index, body in enumerate(UNRECOGNISED_KEY_SHAPES):
        assert _stage_one_sees_a_store(body) is False, (
            f"row {index} is already refused by the recogniser, so it pins nothing")
        gated, why = _gate(tmp_path, monkeypatch, body, f"unrecognised_{index}_14001")
        assert gated is False, f"unrecognised shape #{index} CERTIFIED a stop arm: {why}"


# ── the seven measured shapes, as regression rows: gate AND oracle, one direction ─


#: ⛔ THE ISSUE'S OWN TABLE, one row per family, each spelled as the writer the issue measured.
#: `(case, arm)` — the arm is the stop-arm body; the mirror arm is added below. The gate must
#: REFUSE every one, and the oracle is the authority on whether it had to: a row whose oracle
#: already says "no store" would pass on a gate that certified it, which is why the test
#: asserts the ORACLE first: a row whose writer lands may never be certified stopped.
REGRESSION_ROWS = (
    # `#13864` — a key EXPRESSION that folds to the property. Three foldings, one channel
    # each; the store channel is a plain subscript.
    ("13864-ifexp", """
    k = ("embedding" if _f() else "y")
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    ("13864-or", """
    k = ("embedding" or "y")
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    ("13864-walrus", """
    k = ("embedding" if _f() else "y")
    if not _derived_mirror_required("object"):
        props.update({k: _encode(ev)})
        return None
    _write(props)
"""),
    # `#13910` — a dict comprehension whose CLAUSE LITERAL holds a const-indexed element.
    ("13910-const-index", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in {kk: 1 for kk in [["embedding"][0]]}.items()}
        return None
    _write(props)
"""),
    # `#13915` — the pairs source wrapped in `reversed(...)`.
    ("13915-reversed", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in reversed(list({"embedding": 1}.items()))}
        return None
    _write(props)
"""),
    # `#13920` — the UNBOUND `.keys()` view as a keys source, and a `.values()` view used as
    # a key source.
    ("13920-unbound-keys", """
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(dict.keys({"embedding": 1}), _encode(ev))
        return None
    _write(props)
"""),
    ("13920-values-as-keys", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"x": "embedding"}.values()}
        return None
    _write(props)
"""),
    # `#13936` — the index-keyed comprehension over a pair-with-extras whose property is at
    # index 1, and the `sorted(...)` wrapper the issue's second row needed.
    ("13936-index-1", """
    if not _derived_mirror_required("object"):
        props |= {p[1]: p[2] for p in [(1, "embedding", _encode(ev))]}
        return None
    _write(props)
"""),
    ("13936-sorted", """
    if not _derived_mirror_required("object"):
        props |= {p[1]: p[2] for p in sorted([(1, "embedding", _encode(ev))]) if p}
        return None
    _write(props)
"""),
)


@pytest.mark.parametrize("case,arm", REGRESSION_ROWS, ids=[row[0] for row in REGRESSION_ROWS])
def test_every_measured_shape_is_refused_and_agrees_with_the_runtime_oracle(
        tmp_path, monkeypatch, case, arm):
    """⛔ GATE AND ORACLE MUST AGREE WHERE IT MATTERS, and the ORACLE decides.

    The direction that matters is the one `REFUSE_WRITE_STILL_EMITS` protects: if the executed
    writer leaves the vector in `props`, the gate may NOT answer `gated=True`. `#14001`'s
    doctrine is that a false refusal costs a refactor and a false acceptance deletes the only
    readable copy of a fingerprint, so the permitted over-refusal (oracle clean, gate refusing)
    is not asserted here — it is pinned, per row, in the sibling file's `over_refused` tuple.
    """
    measured = _oracle(arm)
    gated, why = _gate(tmp_path, monkeypatch, arm, f"regress_{case.replace('-', '_')}_14001")
    # ⛔ ONE DIRECTION IS THE CONTRACT (`#14001`, corrected by the round-3 review): if the
    # executed writer leaves the vector in `props`, the gate may NOT answer `gated=True`. The
    # reverse — the oracle is clean and the gate still refuses — is a permitted over-refusal,
    # and `test_the_regression_table_is_not_vacuous` owns "every row here really stores", so an
    # equality assertion would be dead in its second arm (the earlier version was exactly that).
    assert not (gated and measured["lands"]), (
        f"`{case}`: the writer PLACES the vector ({measured['props']!r}) and the gate "
        f"CERTIFIED it — {why}")


def test_the_regression_table_is_not_vacuous(tmp_path):
    """Every regression row must really place the vector — otherwise it pins nothing.

    ⛔ THIS IS THE GUARD AGAINST A GREEN SUITE THAT ASSERTS NOTHING: a row whose writer does
    not store (a mis-paired fixture, a `props` that is never built) would satisfy
    "gate REFUSES" while proving nothing about the fail-open it exists to close.
    """
    not_storing = [case for case, arm in REGRESSION_ROWS if not _oracle(arm)["lands"]]
    assert not_storing == [], (
        f"these regression rows do not actually store the vector, so they are vacuous: "
        f"{not_storing}")


#: ⛔ THE SHAPES THAT DEFEATED EARLIER CUTS OF THIS CLOSURE, kept as permanent regression rows.
#: Every one was found by a review round of this branch and measured CERTIFIED-and-storing
#: there; none is in the issue's table, which is exactly the point — the inversion exists for
#: the spellings nobody has written yet.
DEFEATED_RESOLVER_ROWS = (
    # a name REBOUND outside a plain assignment: the binding walk answered with the stale,
    # safe set while the runtime name held the property
    ("rebound-augassign", """
    k = "embed"
    k += "ding"
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    ("rebound-for-target", """
    k = "x"
    for k in ["embedding"]:
        pass
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    # a `zip` whose VALUE column is constant: at the ELEMENT seam the key is still column 0
    ("zip-constant-value-column", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k, _v in zip(["embedding"], [1])}
        return None
    _write(props)
"""),
    # a conditional source with ONE unreadable branch: the recogniser DROPS it, the proof
    # polarity must not
    ("conditional-source", """
    d = {"embedding": 1}
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in (["a"] if not _f() else d)}
        return None
    _write(props)
"""),
    # a `|` merge with one unreadable operand, and the `{**d}` inside it
    ("bitor-and-dict-unpack", """
    d = {"embedding": _encode(ev)}
    if not _derived_mirror_required("object"):
        props |= ({**d} | {})
        return None
    _write(props)
"""),
    # a DYNAMICALLY resolved callee: `getattr(props, "update")(…)`
    ("getattr-dispatch", """
    if not _derived_mirror_required("object"):
        getattr(props, "update")({"embedding": _encode(ev)})
        return None
    _write(props)
"""),
    # a `match` CAPTURE pattern is an assignment the binding walk must poison
    ("match-capture", """
    k = "x"
    match {"kk": "embedding"}:
        case {"kk": k}:
            pass
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    # a rebinding in a NESTED scope (`nonlocal`) — invisible to a walk that prunes one
    ("nonlocal-rebinding", """
    k = "x"
    def _bg():
        nonlocal k
        k = "embedding"
    _bg()
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    # a PARAMETER later shadowed by a plain assign: the assigned value has not run yet
    ("parameter-shadowed", """
    if not _derived_mirror_required("object"):
        props[key] = _encode(ev)
        return None
    key = "y"
    _write(props)
"""),
    # definition-time execution inside a pruned nested scope: a default argument
    ("default-argument-store", """
    if not _derived_mirror_required("object"):
        def _bg(_x=props.__setitem__("embedding", _encode(ev))):
            pass
        return None
    _write(props)
"""),
    # …a decorator
    ("decorator-store", """
    if not _derived_mirror_required("object"):
        @props.__setitem__("embedding", _encode(ev))
        def _bg():
            pass
        return None
    _write(props)
"""),
    # …and a class body
    ("class-body-store", """
    if not _derived_mirror_required("object"):
        class _C:
            marker = props.__setitem__("embedding", _encode(ev))
        return None
    _write(props)
"""),
    # TWO gate asks: the refused stop arm must not be out-voted by a clean one
    ("second-guard-outvotes-first", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in _keys()}
        return None
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
        return None
    _write(props)
"""),
    # a store in the TEST expression itself — it runs on BOTH arms, so no arm's proof covers it
    ("store-in-the-guard-test", """
    if not _derived_mirror_required("object") and props.update({"embedding": _encode(ev)}):
        return None
    _write(props)
"""),
    ("store-in-the-guard-test-walrus", """
    if not _derived_mirror_required("object") and (
            _p := props.__setitem__("embedding", _encode(ev))):
        return None
    _write(props)
"""),
    # the two binding spellings the tool's docstring claims have a row: a WALRUS rebinding…
    ("rebound-walrus", """
    k = "x"
    if (k := "embedding"):
        pass
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    # …and a `with … as` target
    ("rebound-with-target", """
    k = "x"
    with _ctx() as k:
        pass
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    # …and a `:=` inside a nested `def`'s DEFAULT, which runs at definition time
    ("rebound-walrus-in-nested-default", """
    k = "x"
    def _bg(_d=(k := "embedding")):
        pass
    if not _derived_mirror_required("object"):
        props[k] = _encode(ev)
        return None
    _write(props)
"""),
    # a constructor whose operand set is PARTIALLY readable: the recogniser DROPS the operand
    # it cannot read, so the proof would be built from the remainder
    ("partial-operand-dict-kwargs", """
    d = {"embedding": _encode(ev)}
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"x": 1}, **d)}
        return None
    _write(props)
"""),
    ("partial-operand-dict-positional", """
    if not _derived_mirror_required("object"):
        props.update(dict(zip(_keys(), [_encode(ev)]), x=1))
        return None
    _write(props)
"""),
    # a MULTI-OPERAND "transparent" call: ChainMap iterates EVERY operand's keys, so
    # resolving the first one proves nothing about the property in the second
    ("chainmap-second-operand", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in ChainMap({"x": 1}, {"embedding": 2})}
        return None
    _write(props)
"""),
    ("chainmap-via-fromkeys", """
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys(ChainMap({"x": 1}, {"embedding": 2}), _encode(ev))
        return None
    _write(props)
"""),
    # a MASKED ask (`not gate(...) is True`) discarded, and a second guard out-voting it
    ("masked-ask-discarded", """
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
    if not _derived_mirror_required("object") is True:
        props["embedding"] = _encode(ev)
    _write(props)
"""),
    ("masked-ask-before-clean-guard", """
    if not _derived_mirror_required("object") is True:
        props["embedding"] = _encode(ev)
        return None
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
        return None
    _write(props)
"""),
    # a `{**…}` WRAPPER over a half-read mapping: the pairs list carries keys=None, which a
    # `is None` guard reads as a proof — the wrapper, not the callee, was the leak
    ("wrapper-over-half-read-mapping", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {**dict(zip(_keys(), [1]))}}
        return None
    _write(props)
"""),
    ("wrapper-over-fromkeys", """
    if not _derived_mirror_required("object"):
        props |= dict.fromkeys({**dict(zip(_keys(), [1]))}, _encode(ev))
        return None
    _write(props)
"""),
    ("wrapper-over-constructor-kwargs", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in dict({"x": 1}, **dict(zip(_keys(), [1])))}
        return None
    _write(props)
"""),
    ("wrapper-with-literal-key", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {**dict(zip(_keys(), [1])), "y": 2}}
        return None
    _write(props)
"""),
    # ⛔ THE CANONICAL SHAPE OF THE DYNAMIC-NAMESPACE DEFECT, in the WRITER'S OWN SCOPE (`#14001`,
    # round-5 review — the spelling `_dynamic_namespace_write`'s own docstring names): a subscript
    # write to the module namespace with a CONSTANT key. No key-shape rule can see it (the key is a
    # constant, so the stop arm's own `props[gk]` reads as a non-property store). What makes the row
    # CATCH anything is the opposite of what this comment first said: `gk` IS an ordinary assignment
    # target (`gk = "x"`), so the static value map knows `props[gk]` is the non-property `"x"` and
    # CERTIFIES the writer, and the own-scope dynamic-namespace arm then poisons the scope's `global`
    # declarations (`poisoned |= _scope_declarations`). ⛔ AT THIS HEAD THE MENTION RULE IS WHAT
    # REFUSES THIS ROW, not that arm (`#14001`, measured by the review of this branch and
    # re-measured here): `globals` is named, so `_namespace_mentioned` short-circuits the scope to
    # `{name: None …}` and the own-scope arms are never read. Disarming the arm alone therefore
    # reds NOTHING — measured, 93 passed — which is why the blanket `poisoned |=
    # _scope_declarations` beside the mention was deleted as inert (a mutant removing it reds no
    # row either). The arm is a FALLBACK, and its own discriminating power is only measurable with
    # the mention RETURN disabled (`return {name: None for name in assigned}` → `pass`). THE
    # MEASURED RECIPE, at 56 rows in the table below and 93 tests in this file:
    #   mention return → `pass`:                       19 failed, 74 passed
    #   … plus this arm → `pass`:                      22 failed, 71 passed
    #   ⇒ this arm's OWN contribution is exactly THREE rows — `globals-subscript-rebinding`,
    #     `globals-annassign-own-scope`, `globals-annassign-inside-tuple-target`.
    #   … plus the `_dynamic_namespace_mutation` arm → `pass` instead: 21 failed ⇒ that arm's own
    #     are `globals-mutator-rebinding` and `namespace-unbound-setitem`. Disjoint sets, so the
    #     two arms are not interchangeable and no bare failure COUNT identifies either.
    # ⛔ THE PREVIOUS RECIPE HERE WAS FALSE and this comment had already sent one reviewer at the
    # wrong arm: it claimed disarming the own-scope arm reds `globals-subscript-rebinding` "and
    # only it" ("1 failed, 58 passed of the file's 59 tests"). Measured at this head: 0 failed, and
    # the mention rule is why. Re-measure when a row joins; never transcribe a count from a review.
    # ⛔ `gk = "x"` IS STILL LOAD-BEARING for the ARM's own measurement: with the mention return
    # disabled, deleting it leaves the stop arm merely UNPROVEN (not certifying), so the arm's
    # three rows stop reding. The two rows below pin the MUTATOR and the ONE-SCOPE-IN spellings;
    # without this row the plain form the defect was MEASURED on would have no regression pin.
    ("globals-subscript-rebinding", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        globals()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # the MUTATOR spelling of the dynamic-namespace write (residual 4's other half) …
    ("globals-mutator-rebinding", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        globals().update({"gk": "embedding"})
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # …and the same write ONE SCOPE IN, inside a class body that runs at definition time
    ("globals-subscript-in-class-body", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        class _C:
            globals()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # a LAMBDA in the arm: its body is spelled here and runs when the consumer runs
    ("lambda-body-in-arm", """
    if not _derived_mirror_required("object"):
        list(map(lambda _x: props.update({"embedding": _encode(ev)}), [1]))
        return None
    _write(props)
"""),
    ("immediately-invoked-lambda", """
    if not _derived_mirror_required("object"):
        (lambda k: props.__setitem__(k, _encode(ev)))("embedding")
        return None
    _write(props)
"""),
    # ⛔ THE ANNOTATED SPELLING OF THE DYNAMIC-NAMESPACE WRITE (`#14001`, measured by the
    # review of `#14050` at its merged head): `AnnAssign` fell to the generic assignment arm
    # and its `Subscript` target poisoned nothing. All three are ONE write.
    ("globals-annassign-own-scope", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        globals()["gk"]: str = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("globals-annassign-in-class-body", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        class _C:
            globals()["gk"]: str = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("globals-annassign-inside-tuple-target", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        globals()["gk"], _z = "embedding", 1
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # ⛔ AND EVERY OTHER BINDING FORM (`#14001`, measured by the review of the `AnnAssign`
    # repair): `AugAssign`, a `for` target and a comprehension target all reach `_poison` with a
    # `Subscript` that poisoned nothing, so each CERTIFIED while storing.
    ("globals-augassign-own-scope", """
    global gk
    gk = ""
    if not _derived_mirror_required("object"):
        globals()["gk"] += "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("globals-for-target-own-scope", """
    global gk
    gk = ""
    if not _derived_mirror_required("object"):
        for globals()["gk"] in ("embedding",):
            pass
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("globals-for-target-in-class-body", """
    global gk
    gk = ""
    if not _derived_mirror_required("object"):
        class _C:
            for globals()["gk"] in ("embedding",):
                pass
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("globals-comprehension-target", """
    global gk
    gk = ""
    if not _derived_mirror_required("object"):
        [None for globals()["gk"] in ("embedding",)]
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # ⛔ THE RECEIVER IS AN AXIS LIKE THE BINDING FORM (`#14001`, measured by the review of this
    # branch): every arm above required a CALL named `globals`/`locals`/`vars`, so a NAME bound
    # to the namespace carried the same write past all of them. Measured CERTIFIED at the head
    # that closed the binding-form axis; these rows are the receivers axis.
    ("namespace-alias-subscript", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns = globals()
        ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-alias-chain", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns = globals()
        nn = ns
        nn["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-alias-mutator", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns = globals()
        ns.update({"gk": "embedding"})
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-alias-ior", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns = globals()
        ns |= {"gk": "embedding"}
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-alias-tuple-target", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns, _z = globals(), 1
        ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-alias-in-class-body", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        class _C:
            ns = globals()
            ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # ⛔ AND THE SPELLINGS THAT DEFEATED THE FIRST RECEIVER MODEL (`#14001`, measured by the
    # cycle-3 review): the tool now refuses on the MENTION of a dynamic namespace, so none of
    # these can evade it by holding the namespace in a shape nobody modelled.
    ("namespace-starred-tuple-target", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns, *_r = globals(), 1, 2
        ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-conditional-value", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns = globals() if _f() else {}
        ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-box-indirection", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        ns = globals()
        box = (ns,)
        box[0]["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-for-target", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        for ns in (globals(),):
            ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-match-capture", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        match globals():
            case ns:
                ns["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    # ⛔ THE IMPORT AND STRING-KEY SPELLINGS (`#14001`, measured by the cycle-4 review): they name
    # a dynamic namespace without producing a `Name` node, so `ast.alias` and a string-keyed
    # `__dict__`/`__builtins__` subscript had to be inspected too.
    ("namespace-from-import-alias", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        from builtins import globals as _g
        _g()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-builtins-dict-key", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        import builtins
        builtins.__dict__["globals"]()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-dunder-builtins", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        __builtins__["globals"]()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-function-alias", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        g = globals
        g()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("namespace-unbound-setitem", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        dict.__setitem__(globals(), "gk", "embedding")
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
)


@pytest.mark.parametrize("case,arm", DEFEATED_RESOLVER_ROWS,
                         ids=[row[0] for row in DEFEATED_RESOLVER_ROWS])
def test_the_resolver_bypasses_are_closed(tmp_path, monkeypatch, case, arm):
    """Each row defeated an earlier cut of this change; the gate must refuse and the oracle
    must agree that it had to."""
    extra = ", key=\"embedding\"" if case == "parameter-shadowed" else ""
    measured = _oracle(arm, extra)
    gated, why = _gate(tmp_path, monkeypatch, arm, f"defeat_{case.replace('-', '_')}_14001", extra)
    assert measured["lands"] is True, (
        f"`{case}` no longer stores the vector, so it pins nothing")
    assert gated is False, f"`{case}` CERTIFIED a stop arm that lands the vector — {why}"


#: ⛔ THE DISCLOSED RESIDUAL, PINNED AS A RESIDUAL — not as a success and not as a bug to fix
#: blindly. A store in the statements the STOP RUN FALLS THROUGH TO, after a guard whose own arm
#: is clean. The repair was written, measured and WITHDRAWN in this PR: it refuses five real
#: writers (`_upsert_document`, `restore_graph`, `_event_raw_write_door`, `_ingest_corpus`,
#: `session_indexer::main`) whose trailing code legitimately carries the encode-guard's
#: placeholder or an unreadable mapping. Pinning the CURRENT verdict means a later fix reds here
#: and has to argue the trade first — an inert enforcer is the worse error.
FALLTHROUGH_RESIDUAL = """
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
    props.update({"embedding": _encode(ev)})
    _write(props)
"""


def test_the_fallthrough_residual_is_PINNED_as_a_known_fail_open(tmp_path, monkeypatch):
    """`#14001`'s residual 2, measured: this arm certifies and the writer stores.

    ⛔ A GREEN ASSERTION ON A KNOWN FAIL-OPEN IS THE POINT: the gate's own documentation states
    the residual, and this test is what makes the state impossible to change silently. Fixing it
    is a value-flow change (which local holds the placeholder on the stop path) and must argue
    against the five real writers an over-broad fall-through proof refuses.
    """
    assert _oracle(FALLTHROUGH_RESIDUAL)["lands"] is True, (
        "the residual row no longer stores the vector, so it documents nothing")
    gated, _why = _gate(tmp_path, monkeypatch, FALLTHROUGH_RESIDUAL, "fallthrough_residual_14001")
    assert gated is True, (
        "the fall-through residual is CLOSED — good news, but it must not change silently: "
        "delete this pin, move the row into DEFEATED_RESOLVER_ROWS, and check the five real "
        "writers named in the tool's docstring still certify")


#: ⛔ RESIDUAL 3 — A MAPPING THE ARM BUILDS AND HANDS ON, pinned as a known fail-open. A rebuild
#: SITE was written and WITHDRAWN (round-9 review): the positional version certified every WRAPPED
#: spelling while refusing two honest fixtures, so the boundary is disclosed rather than half-held.
REBUILD_RESIDUAL_ROWS = (
    # ⛔ THE TWO SPELLINGS THE WITHDRAWN RULE WAS WRITTEN FOR (`#14001`, measured by the
    # review of `#14050` at its merged head): withdrawing the rule deleted these rows, so
    # nothing pinned the central spelling residual 3's own prose describes — a future rule
    # could pass every remaining pin while these stayed open.
    ("rebuilt-mapping-inline", """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        _write({**props, k: _encode(ev)})
        return None
    _write(props)
"""),
    ("rebuilt-mapping-bitor", """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        _write(props | {k: _encode(ev)})
        return None
    _write(props)
"""),
    # ⛔ THE ROW THAT PINS `_record_write`'s NO-CLEAR (`#14001`, measured by the rebase review
    # of this branch, F5): the STOP arm calls the write channel TWICE — first with the mapping
    # that carries the vector, then with bare `props`. Clearing `_HANDED` on entry to
    # `_record_write` would leave only the LAST call recorded, `handed` would be `False`, and
    # this row would read `lands is False` — a real handoff deleted on a false premise. With the
    # no-clear the first call survives and the row measures the store. Mutant: restore
    # `_HANDED.clear()` at `_record_write`'s entry → this row is the ONLY one that reds.
    ("rebuilt-mapping-handoff-then-rehandoff", """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        _write({**props, k: _encode(ev)})
        _write(props)
        return None
    _write(props)
"""),
    ("handed-on", """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        params = {**props, k: _encode(ev)}
        _write(params)
        return None
    _write(props)
"""),
    ("wrapped-in-ifexp", """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        _write({**props, k: _encode(ev)} if _f() else props)
        return None
    _write(props)
"""),
    ("wrapped-in-copy", """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        params = ({**props, k: _encode(ev)}).copy()
        _write(params)
        return None
    _write(props)
"""),
    # ⛔ THE CALLER-SUPPLIED MAPPING IS PART OF THE SAME FAMILY (`#14001`, measured by the
    # review of this branch): `_write({**props, **extra})` hands the write channel whatever the
    # caller passed, so it stores whenever `extra` carries the property. `_oracle` takes the
    # caller argument so the row can be measured rather than assumed. ⛔ The 13629 fixture that
    # used to call this an "honest control" was DELETED from that file's `accepted` tuple and is
    # in NEITHER of its tuples — it is not an `over_refused` row either, because the gate
    # CERTIFIES this shape rather than refusing it. It is pinned HERE, as a known fail-open;
    # `tests/test_13629_entity_vector_retire.py` carries the pointer in its `over_refused`
    # preamble ("neither an honest control nor an over-refusal").
    ("rebuilt-mapping-extra-unpack", """
    if not _derived_mirror_required("object"):
        _write({**props, **extra})
        return None
    _write(props)
"""),
    ("rebuilt-mapping-extra-bitor", """
    if not _derived_mirror_required("object"):
        _write(props | extra)
        return None
    _write(props)
"""),
)

#: The caller argument each row's writer needs — `**extra` is a LANDER only when the caller's
#: mapping carries the property, so the pin has to supply one (a `None`/absent caller argument
#: would make the row's oracle clean and the pin vacuous).
REBUILD_CALLER_KWARGS = {
    "rebuilt-mapping-extra-unpack": {"extra": {"embedding": VECTOR}},
    "rebuilt-mapping-extra-bitor": {"extra": {"embedding": VECTOR}},
}


@pytest.mark.parametrize("case,arm", REBUILD_RESIDUAL_ROWS,
                         ids=[row[0] for row in REBUILD_RESIDUAL_ROWS])
def test_the_rebuild_residual_is_PINNED_as_a_known_fail_open(tmp_path, monkeypatch, case, arm):
    """Residual 3, measured: these arms certify AND hand the write channel the vector.

    ⛔ GREEN ON A KNOWN FAIL-OPEN IS THE POINT — the tool's docstring states the boundary, and
    this pin is what stops it changing silently. Closing it needs a mapping-VALUE analysis (every
    expression that can evaluate to a mapping, in every position); the positional attempt is
    recorded in the withdrawn-rule note in `_stop_arm_places_the_property`.
    """
    extra = ", extra=None" if case in REBUILD_CALLER_KWARGS else ""
    caller = REBUILD_CALLER_KWARGS.get(case)
    assert _oracle(arm, extra, caller)["lands"] is True, (
        f"`{case}` no longer stores the vector, so it documents nothing")
    gated, _why = _gate(tmp_path, monkeypatch, arm,
                        f"rebuild_residual_{case.replace('-', '_')}", extra)
    assert gated is True, (
        f"`{case}` is CLOSED — good news, but it must not change silently: delete this pin and "
        "move the row into DEFEATED_RESOLVER_ROWS")


def test_the_helper_call_residual_is_PINNED_as_a_known_fail_open(tmp_path, monkeypatch):
    """Residual 1's reproducible witness: a store reached through `map(…, props.update)`.

    ⛔ PINNED, not celebrated: the callee the gate would have to read is `map`'s own, and the
    store is a method reference handed as a value. The tool's docstring names this row, so the
    state cannot change silently — a later fix reds here and must move the row into
    `DEFEATED_RESOLVER_ROWS`.
    """
    body = """
    if not _derived_mirror_required("object"):
        list(map(props.update, [{"embedding": _encode(ev)}]))
        return None
    _write(props)
"""
    assert _oracle(body)["lands"] is True, (
        "the residual row no longer stores the vector, so it documents nothing")
    gated, _why = _gate(tmp_path, monkeypatch, body, "helper_residual_14001")
    assert gated is True, (
        "the helper-call residual is CLOSED — good news, but it must not change silently: "
        "delete this pin and move the row into DEFEATED_RESOLVER_ROWS")


#: ⛔ RESIDUAL 1's REAL WITNESS — an `exec` STRING the gate cannot read, storing through the
#: MAPPING OBJECT (`#14001`, measured by the review of this branch and re-measured here for the
#: pre-fix tool: `gated=True` on BOTH `origin/main` and this head, so it is a pre-existing pin and
#: not a diff regression). ⛔ NOT the bare `exec('k = …')` spelling: a NAME write from `exec`
#: reaches no fast local on any supported version — 3.12 keeps it in the un-flushed `locals()`
#: cache and discards it on the next refresh (measured: `lands=False`), and 3.13's PEP 667 gives
#: each `exec` an independent snapshot so writes are "consistently ignored" (§Impact on exec() and
#: eval()). ⛔ And NOT the `global`-statement variant (`exec('global k; k = "embedding"')`): it
#: lands, but the gate REFUSES it (`gated=False`, measured on `origin/main` and here) because the
#: rebound name resolves to no proven binding — so it is a refusal, not a fail-open.
EXEC_STRING_RESIDUAL_ROWS = (
    ("exec-string-subscript", """
    if not _derived_mirror_required("object"):
        exec('props["embedding"] = _encode(ev)')
        return None
    _write(props)
"""),
    ("exec-string-update", """
    if not _derived_mirror_required("object"):
        exec('props.update({"embedding": _encode(ev)})')
        return None
    _write(props)
"""),
)


@pytest.mark.parametrize("case,arm", EXEC_STRING_RESIDUAL_ROWS,
                         ids=[row[0] for row in EXEC_STRING_RESIDUAL_ROWS])
def test_the_exec_string_residual_is_PINNED_as_a_known_fail_open(tmp_path, monkeypatch, case, arm):
    """Residual 1's surviving witness: an exec'd string mutates `props`, and the gate certifies.

    ⛔ PINNED, not celebrated — and PINNED AS A RESIDUAL, so the state cannot change silently: a
    later fix that reads exec'd source, or refuses the `exec` callee, reds here and must move the
    row into `DEFEATED_RESOLVER_ROWS`. The `gated is True` half is the pin that varies when the
    boundary moves; the `lands is True` half is the measurement that makes the pin meaningful (a
    row that certifies something that stores nothing documents nothing — the direction that turns
    a pin vacuous).
    """
    assert _oracle(arm)["lands"] is True, (
        "the exec-string residual no longer stores the vector, so it documents nothing")
    gated, _why = _gate(tmp_path, monkeypatch, arm, f"exec_string_residual_14001_{case}")
    assert gated is True, (
        "the exec-string residual is CLOSED — good news, but it must not change silently: "
        "delete this pin and move the row into DEFEATED_RESOLVER_ROWS")


def test_the_two_populations_the_issue_excludes_are_not_claimed_here():
    """`#13806` is not a shape, and `#13796` closed before this change — say so where it lives.

    ⛔ THE SCOPE IS PART OF THE ARTEFACT (`#14001`'s body says both things). `#13806` measured
    that writers never CONSULT the gate at all, so no amount of scanner quality can open the
    strip for them; that population is closed by ROUTING the writers through a gate
    (`#13826`), not by this polarity. `#13796` (the gate was unsatisfiable from a fresh
    process) was closed by its own fix and is not a shape either. Pinning the exclusion keeps
    a later reader from counting these rows as "covered" by the inversion.
    """
    doc = strip.__doc__ or ""
    assert "#13826" in doc and "#13806" in doc, (
        "the tool's own documentation must name the wiring issue as the reason the caller "
        "population is not closed by this gate")
    assert "defence 2" in doc.lower(), (
        "the tool must state WHICH of the four defences it implements, so a reader cannot "
        "mistake a scanner for an enforcement point")


# ── the closed vocabulary: allow-list, not recogniser ───────────────────────


def test_an_unmodelled_expression_is_UNPROVEN_rather_than_clean():
    """⛔ THE POLARITY, AT ITS SMALLEST: the closure's default answer is `None` (refuse).

    Every admitted form resolves to the set of strings it can be; everything else — an
    f-string, `%`-formatting, an attribute, a call, a name bound outside the writer — answers
    `None`. Asserting the DEFAULT rather than a list of shapes is the point: a new spelling
    cannot certify itself by being unusual.
    """
    bindings = {"literal": frozenset({"embedding"}), "other": frozenset({"x"})}

    def values(source):
        return strip._stop_arm_key_values(ast.parse(source, mode="eval").body, bindings)

    assert values("'embedding'") == frozenset({"embedding"})
    assert values("literal") == frozenset({"embedding"})     # a bound Name resolves
    assert values("other") == frozenset({"x"})
    assert values("('embedding' if flag else 'x')") == frozenset({"embedding", "x"})
    assert values("1") == frozenset()                        # a non-string constant is not it
    # …and the refusing default, one family per line
    for unmodelled in ("f\"{'embedding'}\"", "'%s' % 'embedding'", "unbound_name",
                       "obj.key", "build()", "[x for x in ['embedding']][0]"):
        assert values(unmodelled) is None, (
            f"`{unmodelled}` resolved to something, but no arm of the closed vocabulary "
            "models it — the default must be UNPROVEN")


def test_the_stop_arm_verdict_refuses_an_unprovable_key_and_names_it():
    """The refusal carries the SHAPE (`#14001` acceptance 3), not just a boolean."""
    tree = ast.parse(
        'def _upsert_object(props, ev):\n'
        '    if not _derived_mirror_required("object"):\n'
        '        props[f"{k}"] = _encode(ev)\n'
        '        return None\n'
        '    _write(props)\n')
    function = tree.body[0]
    ifs = [node for node in ast.walk(function) if isinstance(node, ast.If)]
    guard = ifs[0]
    places, why = strip._stop_arm_places_the_property(guard.body, function)
    assert places is True, "an f-string key in a stop arm was not refused"
    assert "closed vocabulary" in why, f"the refusal must name the unmatched condition: {why}"
    # ⛔ AND THE RENDERED KEY, not just the word "key" (`#14001`, round-7 review): every refusal
    # from `_keys_reason` contains "key (…)", so `"key" in why` was always true and pinned
    # nothing.
    assert "f'{k}'" in why or 'f"{k}"' in why, f"the refusal must quote the shape: {why}"


#: The honest write-stops, and each must stay CERTIFIED — otherwise the inversion is an inert
#: enforcer that no correct writer could satisfy. The first is exactly the shape `#13617`
#: ships (an encode guard with no `else`).
HONEST_WRITE_STOPS = (
    ("encode-guard", """
    embedding = None
    if _derived_mirror_required("object"):
        embedding = _encode(ev)
    _write(ev, embedding)
"""),
    ("pop-write-stop", """
    if not _derived_mirror_required("object"):
        props.pop("embedding", None)
        return None
    _write({"SET n += $props": props})
"""),
    ("copy-and-pop", """
    _extra = ev
    if not _derived_mirror_required("object"):
        _extra = dict(ev)
        _extra.pop("embedding", None)
    _write(_extra)
"""),
    ("other-key-comprehension", """
    if not _derived_mirror_required("object"):
        props |= {k: _encode(ev) for k in {"embedding_model": 1}}
        return None
    _write(props)
"""),
    ("constant-value", """
    if not _derived_mirror_required("object"):
        props["embedding"] = None
        props |= [("embedding", None)]
        return None
    _write(props)
"""),
)


@pytest.mark.parametrize("case,arm", HONEST_WRITE_STOPS,
                         ids=[row[0] for row in HONEST_WRITE_STOPS])
def test_an_honest_write_stop_still_certifies(tmp_path, monkeypatch, case, arm):
    """⛔ THE INERT-ENFORCER GUARD. A gate that refuses everything is not fail-closed, it is
    broken: `#13617`'s encode guard and the dict writer's `pop` are the shapes the production
    folds use, and both must still answer `gated=True` — with the oracle agreeing that they
    place nothing."""
    assert _oracle(arm)["lands"] is False, (
        f"`{case}` is not an honest control: it places the vector, so it belongs in the "
        "regression rows")
    gated, why = _gate(tmp_path, monkeypatch, arm, f"honest_{case.replace('-', '_')}_14001")
    assert gated is True, f"an honest write-stop was refused: `{case}` — {why}"


def test_a_recognised_store_is_still_refused_by_the_recogniser():
    """Stage 1 is unchanged: the inversion only ADDS refusals, it never widens acceptance.

    ⛔ A FIX MAY ADD REFUSALS, NEVER ACCEPTANCES. The obvious literals must stay refused — if
    any of these certified, the allow-list would be buying its precision by weakening the
    recogniser it composes with.
    """
    for body in ("""
    if not _derived_mirror_required("object"):
        props["embedding"] = _encode(ev)
        return None
    _write(props)
""", """
    if not _derived_mirror_required("object"):
        props |= {"embedding": _encode(ev)}
        return None
    _write(props)
"""):
        assert _stage_one_sees_a_store(body) is True, (
            "stage 1 no longer recognises a literal store — the composition has changed")


def test_the_oracle_records_a_KEYWORD_handoff_to_the_write_channel():
    """⛔ THE MEASUREMENT MUST SEE BOTH CALL SPELLINGS (`#14001`, measured by the cycle-3 review).

    `_write(props={**props, k: vec})` certifies at HEAD and lands — the store is in the mapping the
    channel RECEIVES — but an oracle that recorded only `args` reported `lands=False`, which is the
    direction that turns a pin vacuous: a future row written on the keyword spelling would claim
    "this no longer stores the vector, so it documents nothing".
    """
    body = """
    k = "embed" + "ding"
    if not _derived_mirror_required("object"):
        _write(props={**props, k: _encode(ev)})
        return None
    _write(props)
"""
    assert _oracle(body)["lands"] is True, (
        "the oracle cannot see a keyword handoff, so every `_write(...)` pin is half-measured")


#: ⛔ THE ONLY MODULES WHOSE IMPORT MAY LEGITIMATELY FAIL HERE (`#14001`, measured by the review of
#: `79673ca8`). `tortoise.sdk`'s `import` pulls third-party packages a DEV venv may not carry
#: (measured: `ModuleNotFoundError: prometheus_client`), so its keys are the one population the pin
#: below may skip. EVERY other skip is silent shrinkage of the population the over-refusal claim
#: rests on — and a shrunk population still satisfied a bare `examined > 0` (measured: 14 of 24),
#: which is the whole reason this set is DECLARED rather than inferred.
#: ⛔ AN EXACT MODULE, NOT A PREFIX (`#14099` review of `7c6ad128`). `startswith` would let a future
#: `tortoise.sdk_v2` inherit the exemption without anyone declaring it, so the match is on the key's
#: module component alone.
_UNREADABLE_EXEMPT_MODULES = ("tortoise.sdk",)

#: The ORIGIN token CPython puts in an `ImportError` message (`cannot import name 'x' from
#: '<this>' (<path>)`). Read on its own, because the parenthesised tail is a FILESYSTEM PATH.
_IMPORT_ORIGIN_TOKEN = re.compile(r"from '([^']+)'")


def _is_absent_third_party_distribution(cause: BaseException | None) -> bool:
    """Did this import failure come from a MISSING THIRD PARTY, or from THIS repo?

    ⛔ ONLY THE FIRST MAY EXCUSE A SKIP (`#14099` F1). `ModuleNotFoundError` is a SUBCLASS of
    `ImportError`, so grading the class alone accepts a strictly larger set than the pre-fix test
    did — and the extra members include the one failure a source-reading pin exists to notice: a
    renamed symbol or a circular import INSIDE `tortoise`, which raises
    `ImportError: cannot import name 'x' from 'tortoise.retrieval'` and would otherwise drop all
    ten `tortoise.sdk` writers out of the population while the pin read as fully measured.

    The ORIGIN is graded, measured at this head (`.name` settles every REAL shape):

      absent third party       `ModuleNotFoundError`  `.name == 'prometheus_client'`
      partially installed dep  `ImportError`          `.name == 'prometheus_client'`
      repo-side rename         `ImportError`          `.name == 'tortoise.retrieval'`
      hand-raised, no `name=`  either class           `.name is None` -> the `from '<module>'`
                                                      TOKEN decides, never the tail

    ⛔ THE RENDERED TAIL NEVER DECIDES (`#14099` F1, cycle 5). CPython renders a partially
    installed dependency as `cannot import name 'x' from '<mod>' (<the module's __file__>)`, so
    the parenthesised tail carries a FILESYSTEM PATH — and this repo's venv (and CI's checkout)
    both live under a path containing `tortoise`. Grading the rendered message therefore reds for
    the exact environment this exemption exists to excuse: measured, a REAL `ImportError` for a
    partially installed `prometheus_client` read as repo-side while `.name` said otherwise.
    `.name` settles every real shape; the message is read only for its origin TOKEN.

    Both signals fail CLOSED: an origin that is neither named nor recognisably third-party is not
    the environment, so it keeps the skip out. That last clause is PINNED — a cause carrying no
    origin at all must classify `stray` — because reverting it was measured green until the arm
    in `test_the_over_refusal_population_is_itself_pinned` existed.
    """
    if not isinstance(cause, ImportError):
        return False
    name = getattr(cause, "name", None) or ""
    if not name:
        # No `.name` — the only other origin signal is the `from '<module>'` token. Read THAT and
        # nothing else, so a filesystem path in the parenthesised tail cannot decide the verdict.
        match = _IMPORT_ORIGIN_TOKEN.search(str(cause))
        name = match.group(1) if match else ""
    if not name:
        return False            # unrecognised origin -> not the environment (fail closed)
    return not (name == "tortoise" or name.startswith("tortoise."))


def _writer_register_population() -> dict[str, list[str]]:
    """Classify every entity-writer register key by what the pin below could READ of it.

    ⛔ EXTRACTED SO IT CAN BE TESTED (`#14099` review of `310ddeeb7c2`). While this walk lived
    inline inside its own pin, the assertion that closes the population shrink had NO negative
    control: deleting it left the file green, so a later refactor could reopen the shrink
    silently and nothing would say so. The buckets are the contract:

      examined    — read, and its `def` walked for a dynamic-namespace mention;
      unreadable  — in a DECLARED module, skipped because its import failed for a missing dep;
      stray       — skipped for any OTHER reason: the population shrank;
      missing     — the register names a `def` the module does not contain (a renamed writer);
      offenders   — read, and it mentions a dynamic namespace (what the pin exists to catch).

    ⛔ THE CAUSE IS GRADED ONLY FOR THE DECLARED MODULE, AND ONLY WHEN ITS ORIGIN IS A MISSING
    THIRD PARTY. Outside the declaration a skip is `stray` whatever the cause, because no reason
    makes a writer's gate disappear quietly. Inside it `_is_absent_third_party_distribution`
    decides: a missing `prometheus_client` is the environment, while a REPO-SIDE
    `ImportError: cannot import name … from 'tortoise.…'` still reds — that is the one break a
    source-reading pin exists to notice, and grading the class alone let it through. A cause that
    names NO origin at all (a bare `raise ImportError("…")` inside the declared module's chain)
    is `stray` too: unrecognised is not the environment.
    """
    import ast

    from tortoise import embed_writer_register as register

    population: dict[str, list[str]] = {
        "examined": [], "unreadable": [], "stray": [], "missing": [], "offenders": []}
    for key in sorted(register.EMBED_WRITER_CLASSES):
        module_name, _, symbol = key.partition("::")
        try:
            source = strip._module_source(module_name)
        except strip._UnreadableFold as exc:
            exempt = (module_name in _UNREADABLE_EXEMPT_MODULES
                      and _is_absent_third_party_distribution(exc.__cause__))
            population["unreadable" if exempt else "stray"].append(key)
            continue
        bodies = strip._function_bodies(source)
        if symbol not in bodies:
            population["missing"].append(key)   # a renamed writer, not an environment fact
            continue
        population["examined"].append(key)
        if any(strip._is_dynamic_namespace_reference(node) for node in ast.walk(bodies[symbol][0])):
            population["offenders"].append(key)
    return population


def test_no_declared_entity_writer_mentions_a_dynamic_namespace():
    """The blanket dynamic-namespace rule costs ZERO refusals on the real population — PINNED.

    ⛔ `_string_bindings` refuses every declared name of a writer that mentions `globals`/
    `locals`/`vars` at all (`_is_dynamic_namespace_reference`). That over-refusal is only
    acceptable while no real writer does it: the day one does, this pin reds, and the fix has to
    argue the trade instead of silently losing a writer's gate. This is the same non-vacuity
    discipline the residual pins use, pointed at the over-refusal side.

    ⛔ THE POPULATION IS PART OF THE CLAIM. "No declared writer mentions one" is only true of the
    writers this walk could READ, so a skip that is not the declared environment-optional module
    reds here (`stray`) and a key the module no longer defines reds too (`missing`). The walk
    itself is `_writer_register_population`, and it is pinned by
    `test_the_over_refusal_population_is_itself_pinned`.
    """
    from tortoise import embed_writer_register as register

    population = _writer_register_population()
    stray, missing = population["stray"], population["missing"]
    assert stray == [], (
        "a writer outside the declared environment-optional modules could not be read, so it "
        f"left this pin's population for a reason that is not the environment: {stray} "
        f"(examined {len(population['examined'])} of {len(register.EMBED_WRITER_CLASSES)})")
    assert missing == [], (
        "the register names a writer whose `def` the module does not contain — the writer was "
        f"renamed or the key is stale, so it left this pin's population: {missing}")
    assert population["examined"], (
        "NO writer could be read, so this pin asserts nothing — the register or the source "
        "loader changed shape and the over-refusal cost it guards is unmeasured")
    assert population["offenders"] == [], (
        "a declared entity writer now mentions a dynamic namespace, so the blanket rule refuses "
        "it: argue the trade (is the writer's stop arm provable another way?) before either "
        f"narrowing the rule or accepting the refusal: {population['offenders']}")


def test_the_over_refusal_population_is_itself_pinned(monkeypatch):
    """⛔ THE GUARD AGAINST A SILENT SHRINK IS ITSELF GUARDED (`#14099` reviews of 7c6ad128
    and 310ddeeb).

    Measured before this test existed: deleting `stray` left the file green while a real writer
    module folded out of the population, and widening `_UNREADABLE_EXEMPT_MODULES` to any other
    module did the same. Both are the "a pin reports green while it measures less" failure, so
    each bucket is driven here directly, one cause at a time, through a loader that refuses ONE
    module and serves the rest.
    """
    from tortoise import embed_writer_register as register

    assert _UNREADABLE_EXEMPT_MODULES == ("tortoise.sdk",), (
        "the set of modules allowed to leave this pin's population changed. That is a "
        "DECLARATION, not a detail: appending a name buys silence for a whole module's writers, "
        f"so it has to be argued in review — declared: {_UNREADABLE_EXEMPT_MODULES}")
    keys = sorted(register.EMBED_WRITER_CLASSES)
    assert keys, "the register is empty, so this control proves nothing"
    # ⛔ TOLERATE THE ENVIRONMENT THIS PIN EXISTS FOR (`#14099` review of `102d5e4e` / `540c29ec`).
    # Reading every module up front with no handling made this control ERROR in exactly the case its
    # exemption is for (measured: only `prometheus_client` absent → `tortoise.sdk` folds, the PIN
    # PASSES and this test died with an uncaught `_UnreadableFold`, then with `KeyError` once the
    # fake indexed `sources`). A module this venv cannot read is simply not cached.
    # ⛔ …AND THE ENVIRONMENT'S OWN REFUSAL IS RE-RAISED VERBATIM, NEVER FABRICATED (`#14099` F2,
    # review of `ed6ff2ab`). `env_source` used to re-raise `ModuleNotFoundError(name)` —
    # POSITIONALLY, so `.name` is `None` (CPython's `name`/`path` are keyword-only) and the message
    # carries no `from '<module>'` token — which the origin gate correctly REFUSES. The declared
    # module's keys then landed in `stray`, so the `missing`-bucket arm below red-ed through the
    # STRAY assertion instead of the one it exists to drive, in the one venv this exemption exists
    # for (measured: with `prometheus_client` blocked, that arm failed while the pin it guards
    # passed). The real cause the loader folded — `ModuleNotFoundError('prometheus_client')`, whose
    # `.name` is set — carries the origin, so keep it and re-raise THAT.
    sources: dict[str, str] = {}
    env_failure: dict[str, BaseException] = {}
    for key in keys:
        module_name = key.partition("::")[0]
        if module_name in sources or module_name in env_failure:
            continue
        try:
            sources[module_name] = strip._module_source(module_name)
        except strip._UnreadableFold as exc:
            # `_UnreadableFold` is always raised `from exc`, so the cause is the environment's own
            # fact; the fallback stays FAIL-CLOSED (no origin → `stray`), never an excuse.
            env_failure[module_name] = exc.__cause__ or ModuleNotFoundError(module_name)
    outsider = next(k for k in keys if k.partition("::")[0] not in _UNREADABLE_EXEMPT_MODULES)
    declared = next((k for k in keys if k.partition("::")[0] in _UNREADABLE_EXEMPT_MODULES), None)

    def env_source(name: str) -> str:
        # ⛔ THE ENVIRONMENT'S OWN REFUSAL MUST OUTLIVE THE FAKE, ORIGIN INCLUDED. A module this venv
        # cannot read is absent from `sources`; indexing it died with `KeyError: 'tortoise.sdk'` in
        # exactly the environment the exemption exists for, and fabricating a cause from the
        # module's own NAME classified that refusal as repo-side. The declaration's own arms cover
        # the declared module, so every other fake re-raises the environment's refusal instead.
        if name in sources:
            return sources[name]
        raise strip._UnreadableFold(f"{name} unreadable") from env_failure.get(
            name, ModuleNotFoundError(name))

    def folding(module_name: str, cause: BaseException):
        def fake(name: str) -> str:
            if name == module_name:
                raise strip._UnreadableFold(f"{name} unreadable") from cause
            return env_source(name)
        return fake

    # baseline: the real loader, nothing shrunk, nothing refused.
    base = _writer_register_population()
    assert base["stray"] == [] and base["missing"] == [] and base["examined"]

    # a writer OUTSIDE the declared modules cannot leave the population — and the CAUSE cannot
    # excuse it: an absent dependency is the environment only where the declaration says so.
    module = outsider.partition("::")[0]
    monkeypatch.setattr(strip, "_module_source", folding(module, ModuleNotFoundError("absent")))
    assert outsider in _writer_register_population()["stray"], (
        f"`{module}` folded out of the population with no red — the shrink is back")
    monkeypatch.setattr(strip, "_module_source", folding(module, ValueError("loader changed")))
    assert outsider in _writer_register_population()["stray"]

    # ⛔ AN ABSENT DECLARED KEY IS A FINDING, NOT A SKIPPED ARM (`#14099` review of `102d5e4e`):
    # behind `if declared is not None:` the four exemption arms silently stopped running, so this
    # control proved NOTHING about `_UNREADABLE_EXEMPT_MODULES` and still reported green.
    assert declared is not None, (
        "no register key lives on a declared environment-optional module, so the exemption's "
        "arms cannot run and this control would prove nothing about it")
    declared_module = declared.partition("::")[0]
    # the declared module MAY skip — for a missing dependency, in the shape CPython REALLY raises
    # for a PARTIALLY INSTALLED one (`#14099` F1, cycle 5): `.name` is set, and the rendered
    # message's parenthesised tail is the module's `__file__`. This repo's venv and the CI
    # checkout both live under a path CONTAINING `tortoise`, so grading the rendered text calls
    # the environment a repo-side break and reds this pin for the one fact it exists to excuse.
    installed_under_a_tortoise_path = (
        "cannot import name 'Counter' from 'prometheus_client' "
        "(/home/runner/work/tortoise/tortoise/.venv/lib/python3.12/site-packages/"
        "prometheus_client/__init__.py)")
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module, ImportError(installed_under_a_tortoise_path,
                                             name="prometheus_client")))
    skipped = _writer_register_population()
    assert declared in skipped["unreadable"] and skipped["stray"] == [], (
        "a partially installed optional dependency carries its FILE PATH in the rendered "
        "message, and that path contains `tortoise`; grading that text instead of the origin "
        f"reds this pin for the environment it exists to excuse: {skipped['stray']}")
    # …and when `.name` is absent, the origin TOKEN (`from '<module>'`) decides — NOT the tail
    # that carries the path.
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module, ImportError(installed_under_a_tortoise_path)))
    skipped = _writer_register_population()
    assert declared in skipped["unreadable"] and skipped["stray"] == [], (
        "the origin token `from 'prometheus_client'` decides, and the `tortoise` in the "
        f"parenthesised path must not: {skipped['stray']}")
    # …but a NON-import cause inside it is a loader change, not the environment.
    monkeypatch.setattr(
        strip, "_module_source", folding(declared_module, ValueError("loader changed")))
    assert declared in _writer_register_population()["stray"], (
        f"`{declared_module}`'s loader changed shape and the skip was blamed on the "
        "environment")

    # ⛔ …AND NOR MAY A REPO-SIDE IMPORT BREAK INSIDE IT (`#14099` F1). This is the measured
    # shape of a renamed symbol in the module's chain: `ImportError` (NOT a
    # `ModuleNotFoundError`) whose `.name` is the `tortoise` module that lost the symbol.
    # Grading the class alone let this read as "the environment" and dropped all ten writers.
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module, ImportError(
            "cannot import name 'is_turn_echo_row' from 'tortoise.retrieval'",
            name="tortoise.retrieval")))
    assert declared in _writer_register_population()["stray"], (
        f"`{declared_module}` failed on a REPO-SIDE import and the skip was blamed on the "
        "environment — ten writers left the population silently")
    # …and the same origin with no `name=`, where only the message carries it.
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module,
                ImportError("cannot import name 'is_turn_echo_row' from 'tortoise.retrieval'")))
    assert declared in _writer_register_population()["stray"], (
        "a hand-raised repo-side ImportError carries its origin in the MESSAGE, and the "
        "message was not read")
    # ⛔ …AND `.name` ALONE MUST DECIDE, WITH NO ORIGIN IN THE MESSAGE (`#14099` review of
    # `540c29ec`, "not attributable" #1). Every arm above restates the origin in its message, so
    # DELETING the `.name` gate left them all green — the docstring's claim that ".name settles
    # every REAL shape" was enforced by nothing. These two shapes carry no `from '<module>'`
    # token at all, so only `.name` can decide them, in both directions.
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module, ImportError("symbol vanished", name="tortoise.retrieval")))
    assert declared in _writer_register_population()["stray"], (
        "a repo-side break whose MESSAGE names no module was excused — only `.name` can decide "
        "that shape, and it said `tortoise.retrieval`")
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module, ImportError("symbol vanished", name="prometheus_client")))
    skipped = _writer_register_population()
    assert declared in skipped["unreadable"] and skipped["stray"] == [], (
        "a third-party `.name` is enough on its own — a message naming no module must not turn "
        f"the environment into a repo-side break: {skipped['stray']}")

    # ⛔ …AND A CAUSE THAT NAMES NO ORIGIN AT ALL MUST FAIL **CLOSED** (`#14099` F1, cycle 5).
    # `111a461c` narrowed the helper to "`.name`, else the `from '<module>'` token, else REFUSE",
    # and the last clause was enforced by NOTHING: reverting `if not name: return False` to
    # `return True` (the pre-`111a461c` reading, where a bare hand-raised cause inside the
    # declared module read as the environment) left this whole file GREEN. That is the fail-OPEN
    # direction of the population pin — a cause no origin signal names drops the ten
    # `tortoise.sdk` writers out of the measured set on the strength of a guess. Measured mutant,
    # and the reason this arm exists: with the rule, `stray`; without it, `unreadable`.
    monkeypatch.setattr(
        strip, "_module_source",
        folding(declared_module, ImportError("symbol vanished")))
    assert declared in _writer_register_population()["stray"], (
        "a cause naming NO origin module — no `.name`, no `from '<module>'` token — was excused "
        "as the environment; an unrecognised origin has to fail CLOSED, or the exemption "
        "silently swallows the population shrink it exists to bound")

    # a renamed writer is `missing`, never silently dropped.
    blanked = outsider.partition("::")[0]
    monkeypatch.setattr(
        strip, "_module_source", lambda name: "" if name == blanked else env_source(name))
    assert outsider in _writer_register_population()["missing"]

    # ⛔ …AND THE PIN MUST RED FOR *THAT* BUCKET TOO (`#14099` F3, review of `00a5804`). The arm
    # above proves the WALK files a renamed writer under `missing`; it says nothing about the PIN
    # reporting one. Measured at `00a5804`: deleting the pin's `assert missing == []` left this
    # whole file GREEN (94 passed) — the `stray` bucket had the red below, `missing` had nothing,
    # so a renamed writer could silently leave the population the declaration exists to bound.
    #
    # The population is SYNTHETIC here, deliberately: an arm driven by the real walker is
    # environment-coupled — under a partial install (a folded `tortoise.sdk`) the declared keys
    # land in `stray` and the pin reds on THAT assertion instead, which would attribute this arm
    # to the wrong guard (measured: the real-walker version of this arm failed under the F2
    # simulation). `env_source` above keeps the environment's REAL cause, so the pin still
    # classifies a partial install correctly; this arm simply does not depend on that either way.
    with monkeypatch.context() as mp:
        mp.setattr(
            sys.modules[__name__],
            "_writer_register_population",
            lambda: {
                "examined": [outsider],
                "unreadable": [],
                "stray": [],
                "missing": [outsider],
                "offenders": [],
            },
        )
        with pytest.raises(AssertionError) as red_missing:
            test_no_declared_entity_writer_mentions_a_dynamic_namespace()
    assert outsider in str(red_missing.value) and "register names a writer" in str(
        red_missing.value), (
        f"the pin does not red — or reds without naming the key — for a register writer whose "
        f"`def` the module no longer contains: {red_missing.value}")

    # ⛔ THE PIN ITSELF MUST RED (`#14099` F2). The arms above prove the WALK classifies; they do
    # NOT prove the PIN reports. Measured: deleting the pin's `assert stray == []` left this whole
    # file green, because every other row reads the helper rather than the assertion — so the guard
    # could be removed and nothing would say so. Each red below names the key AND the population it
    # measured, which is what a reader needs to tell a working guard from a deleted one.
    monkeypatch.setattr(strip, "_module_source", folding(module, ModuleNotFoundError("absent")))
    with pytest.raises(AssertionError) as red:
        test_no_declared_entity_writer_mentions_a_dynamic_namespace()
    assert outsider in str(red.value), (
        f"the pin reds without naming the key that left its population: {red.value}")
    assert f"of {len(keys)}" in str(red.value), (
        f"the pin reds without naming the population it measured: {red.value}")

    monkeypatch.setattr(strip, "_module_source", folding(module, ValueError("loader changed")))
    with pytest.raises(AssertionError, match="outside the declared environment-optional modules"):
        test_no_declared_entity_writer_mentions_a_dynamic_namespace()

    # ⛔ THE EXEMPTION IS AN EXACT MODULE, NOT A PREFIX (`#14099` review of `310ddeeb`, whose fix was
    # itself unpinned: reverting it to `key.startswith(...)` left this file green because no
    # `tortoise.sdk_v2` key exists in the register today). A synthetic key makes the rule
    # measurable — a module that merely STARTS with the declared name must not inherit it.
    neighbour = f"{declared_module}_v2::_emit"
    monkeypatch.setattr(register, "EMBED_WRITER_CLASSES", frozenset({neighbour}))
    # ⛔ THE CAUSE MUST BE ONE THE ORIGIN GATE RECOGNISES, or this arm measures nothing: the
    # exemption is `declared module AND absent-third-party origin`, so a cause that fails the
    # origin half lands in `stray` under BOTH the exact and the prefix comparison. A real absent
    # dependency keeps the row discriminating between the two.
    monkeypatch.setattr(
        strip, "_module_source",
        folding(neighbour.partition("::")[0],
                ModuleNotFoundError("No module named 'prometheus_client'",
                                    name="prometheus_client")))
    assert _writer_register_population()["stray"] == [neighbour], (
        "a module that merely starts with the declared name inherited its exemption")


#: ⛔ RESIDUAL — A NAME the mention rule cannot READ (`#14001`, measured by the cycle-4 review).
#: The rule matches a `globals`/`locals`/`vars` NAME, attribute, import alias or string key. A name
#: COMPOSED at runtime (`builtins.__dict__["glob"+"als"]`) or reached by FRAME INTROSPECTION
#: (`sys._getframe(0).f_globals`) names nothing the scanner can see. Pinned, not fixed: chasing
#: string composition is the unbounded recogniser this issue exists to stop.
DYNAMIC_NAMESPACE_NAME_COMPOSITION = (
    ("composed-string-key", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        import builtins
        _g = builtins.__dict__["glob" + "als"]
        _g()["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
    ("frame-introspection", """
    global gk
    gk = "x"
    if not _derived_mirror_required("object"):
        import sys
        sys._getframe(0).f_globals["gk"] = "embedding"
        props[gk] = _encode(ev)
        return None
    _write(props)
"""),
)


@pytest.mark.parametrize("case,arm", DYNAMIC_NAMESPACE_NAME_COMPOSITION,
                         ids=[row[0] for row in DYNAMIC_NAMESPACE_NAME_COMPOSITION])
def test_the_composed_name_residual_is_PINNED_as_a_known_fail_open(tmp_path, monkeypatch, case, arm):
    """Residual 5, measured: a COMPOSED namespace name certifies while storing.

    ⛔ GREEN ON A KNOWN FAIL-OPEN IS THE POINT. The tool's docstring states the boundary, and this
    pin is what stops it changing silently: closing it reds here and the row moves into
    `DEFEATED_RESOLVER_ROWS`.
    """
    assert _oracle(arm)["lands"] is True, (
        f"`{case}` no longer stores the vector, so it documents nothing")
    gated, _why = _gate(tmp_path, monkeypatch, arm, f"composed_residual_{case.replace('-', '_')}")
    assert gated is True, (
        f"`{case}` is CLOSED — good news, but it must not change silently: delete this pin and "
        "move the row into DEFEATED_RESOLVER_ROWS")
