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
    _HANDED.clear()
    for argument in args:
        _HANDED.append(argument)


_HANDED: list = []


def _oracle(body: str, extra: str = "") -> dict:
    """EXECUTE the writer with the gate answering STOP and report whether the vector lands.

    ⛔ THIS IS THE MEASUREMENT, NOT A SECOND OPINION. Every fail-open in this series was proved
    by running the writer; the same source the gate scans is compiled and called here, with
    `_derived_mirror_required` answering `False` (the arm a strip can actually race) and
    `_encode` returning the sentinel. `props` is a plain dict, so `props["embedding"] is
    VECTOR` answers "did this writer store the property on the stop arm" exactly.
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
        namespace["_upsert_object"](props, {"x": 1})
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
)


@pytest.mark.parametrize("case,arm", REBUILD_RESIDUAL_ROWS,
                         ids=[row[0] for row in REBUILD_RESIDUAL_ROWS])
def test_the_rebuild_residual_is_PINNED_as_a_known_fail_open(tmp_path, monkeypatch, case, arm):
    """Residual 3, measured: these arms certify AND hand the write channel the vector.

    ⛔ GREEN ON A KNOWN FAIL-OPEN IS THE POINT — the tool's docstring states the boundary, and
    this pin is what stops it changing silently. Closing it needs a mapping-VALUE analysis (every
    expression that can evaluate to a mapping, in every position); the positional attempt is
    recorded in the withdrawn-rule note in `_stop_arm_places_the_property`.
    """
    assert _oracle(arm)["lands"] is True, (
        f"`{case}` no longer stores the vector, so it documents nothing")
    gated, _why = _gate(tmp_path, monkeypatch, arm, f"rebuild_residual_{case.replace('-', '_')}")
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
