"""``metering.get_capture_usage`` — the READER the extractor dimension lacked (#4051).

⚠️ The reader is ONE function over TWO landed contracts. #4051 landed it first (columns +
``capture_cost_usd``, zero-view degradation, never raises); the ``#9567`` D11/D12 branch defined a
SECOND ``def get_capture_usage`` at the same insertion point (``measured`` /
``unmeasured_reason``). Python does not error on a duplicate ``def`` — the last one wins silently —
so the merge reconciled them into one body: every key #4051's landed tests assert, plus the presence
fact the allowance gate cannot work without. Both halves are pinned below.

WHY THIS FILE EXISTS. ``record_capture_usage`` has written ``capture_tokens_in`` /
``capture_tokens_out`` since #5045, and the owner ruling behind them (2026-09-26) is that the
customer-visible extraction allowance is denominated in **tokens**. The columns had no reader:
``tortoise/cost_dimensions.py`` declared ``get_current_usage`` as the
``capture_extraction`` READER, and that function returns write-op fields only — so "tokens per
team per period" was written and unreadable from code, the ``#4493`` failure mode where a
recorded dimension with no reader reads as a measurement that does not exist.

These tests are deliberately DB-free: the reader is a pure mapping over one ledger row, so the
window and the registry are stubbed and the assertions are about the CONTRACT (keys, zeros,
degradation), not about FalkorDB.
"""
from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from tortoise import cost_dimensions, metering
from tortoise.cost_dimensions import READER, resolve

_KEYS = ("capture_calls", "capture_tokens_in", "capture_tokens_out",
         "capture_cost_usd")


def _period():
    return SimpleNamespace(label="2026-11", start_iso="2026-11-01T00:00:00+00:00",
                           end_iso="2026-12-01T00:00:00+00:00")


def _fake_sdk(result_set):
    """A registry whose Cypher read returns *result_set*."""
    reg = SimpleNamespace(query=lambda *a, **k: SimpleNamespace(result_set=result_set))
    return SimpleNamespace(_get_registry=lambda: reg)


# ── the declaration is now TRUE ──────────────────────────────────────────────


def test_the_dimension_declares_this_function_as_its_reader():
    row = cost_dimensions.by_name("capture_extraction")
    readers = [b for b in row.bindings_of(READER)]
    assert readers, "capture_extraction declares no reader at all"
    names = {b.attr for b in readers}
    assert "get_capture_usage" in names, (
        f"capture_extraction must declare get_capture_usage as its reader, got {names}"
    )
    # and every declared binding must actually resolve on the live module
    for b in readers:
        assert resolve(b) is not None


def test_the_old_binding_could_not_have_been_a_reader():
    """The former binding named a function that returns write-op fields only.

    Pinned so a future edit cannot re-point the reader at it silently: if
    ``get_current_usage`` ever grows the capture keys this assertion is the
    thing that should be revisited deliberately, not by drift.
    """
    import inspect

    source = inspect.getsource(metering.get_current_usage)
    for key in _KEYS:
        assert key not in source, (
            f"get_current_usage now mentions {key!r} — re-check which function is the "
            "capture reader before re-pointing the binding"
        )


# The modules that DEFINE the reader, plus ``cost_dimensions.py`` — which names it
# only as a declared ``Binding(..., READER)`` (a string, never a call).
_ALLOWED = {"metering.py", "supabase_control.py", "cost_dimensions.py"}

# ⚠️ §1b SUPERSEDED (#9567 D11/D12): the reader is no longer measurement-only. An
# allowance cannot be enforced against a reader nothing may call, so the allowance
# gate is now its ONE consumer — and that is asserted EXACTLY (below), not exempted
# by name. An exemption for ``allowance_gate.py`` would let a SECOND call site (a
# report, a dashboard, a tool) appear without reddening this pin; an exact equality
# also reds if the gate stops reading it, which would be a fail-OPEN regression.
_ENFORCEMENT_CONSUMERS = {"allowance_gate.py"}


def _names_the_reader(path):
    """True iff ``path`` REFERENCES ``get_capture_usage`` in CODE.

    ⛔ PARSED, never grepped. A substring match also counts the COMMENT that
    documents the reader: ``hosted_api.py`` names
    ``metering.get_capture_usage`` in the comment explaining its own capture
    door, and a text scan reads that prose as a second consumer — so the pin
    reds on the very file that explains the relationship it enforces (the
    ``#10675`` class: a pin that reads a file as TEXT also matches the prose
    about it). Comments never reach the AST; a docstring does, and is excluded
    here EXPLICITLY by an id-set rather than "by construction".

    The detection surface is every **direct syntactic** reference in the scanned
    package — an attribute, a bare name, an import alias (including a re-export
    with no use site at all), or the reader's name as a string literal ANYWHERE in
    the module whose value IS the name or a dotted path ENDING in it (a docstring
    excepted). Those last two arms are here because a first version of
    this helper matched only ``ast.Attribute``/``ast.Name`` and therefore read
    ``from … import get_capture_usage as gcu`` + ``gcu(o)`` as clean — a
    false-negative the old substring scan did not have, and house style in this
    repo (``allowance_gate.py:196`` aliases its own import). The string arm is
    not limited to ``getattr``/``setattr``: a name-keyed registration is the same
    reference, and this repo carries one at ``cost_dimensions.py:534``
    (``Binding("tortoise.metering", "get_capture_usage", READER)``) — the file
    exempted above for exactly that reason — so a scanner that recognised only
    the two builtins would let a SECOND such registration land without reddening
    the pin below (#10962 review P2). A string Constant is matched ANYWHERE in the
    module, not only inside a call: a dict-literal registration
    (``{"get_capture_usage": READER}``) and a dotted path held in a constant
    (``"tortoise.metering.get_capture_usage"``) are the same reference, and an arm
    scoped to ``Call``/``Subscript`` read both as CLEAN while the text scan this
    replaced caught them (#10962 review, P2 — a narrowing of the detection
    surface, not a strengthening of it). The value must BE the name (``==``) or be
    the dotted ``metering.get_capture_usage`` path: a bare ``endswith`` matched
    prose that merely ENDS in the name — an error message
    (``"… could not be resolved: metering.get_capture_usage"``) and a
    ``"%s.get_capture_usage"`` log placeholder — which is a false RED on the
    required scan, the mirror of the fail-open the arm was widened to close, so
    the suffix arm also requires the ``metering`` segment. Prose that mentions or
    ends in the path any other way stays invisible; a docstring is excluded
    explicitly and a comment never reaches the AST at all, which are what this
    helper exists to exclude. (A name built at
    RUNTIME, e.g. ``getattr(m, some_variable)`` or ``eval``, is out of scope:
    no static pin can resolve it, and demanding it is demanding an interpreter.
    Declared residual: a dotted path through an ALIAS of the module
    (``"m.get_capture_usage"`` where ``m`` is ``tortoise.metering``) is not
    matched, because the segment check names ``metering``.)

    It cannot see TRANSITIVE wiring: a door that runs the gate inherits the
    reader without naming it (the declared residual on ``#10949``). "No offender"
    therefore means "no direct reference in this package", never "no consumer".
    """
    try:
        # ⛔ ``read_bytes``, not ``read_text``: decoding first makes two kinds of
        # VALID module unparsable — a UTF-8 BOM (``read_text`` leaves a ``\ufeff``
        # the tokenizer rejects) and a non-UTF-8 file with a coding cookie. Both
        # compile and import fine, so raising on them would be a false red that
        # MISREPORTS itself as "could not be parsed". ``ast.parse`` on bytes
        # honours the BOM and the cookie itself, and still raises ``SyntaxError``
        # for a genuinely broken file.
        tree = ast.parse(path.read_bytes())
    except SyntaxError as exc:
        # ⛔ NOT a silent clean: the file is in ``scanned``, so returning False
        # would satisfy the >100 floor while this module was never examined —
        # a guard that checked nothing reading exactly like one that found
        # nothing (the failure the floor's own docstring exists to prevent).
        raise AssertionError(
            f"{path} could not be parsed, so the scope scan never examined it — "
            "an UNREADABLE module must not read as a CLEAN one"
        ) from exc
    # Docstring constants are PROSE and are collected up front, because the string
    # arm below matches a Constant ANYWHERE in the module (see it for why). The one
    # shape it must still refuse is a docstring that happens to BE the dotted path.
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "get_capture_usage":
            return True
        if isinstance(node, ast.Name) and node.id == "get_capture_usage":
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if "get_capture_usage" in (alias.name, alias.asname):
                    return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and (
                node.value == "get_capture_usage"
                or (
                    node.value.endswith(".get_capture_usage")
                    and node.value.rsplit(".", 2)[-2] == "metering"
                )
            )
        ):
            # ⛔ A string Constant ANYWHERE in the module, not only as a call
            # argument or a subscript key. A name-keyed registration is a reference
            # whatever spelling it takes — ``Binding("tortoise.metering",
            # "get_capture_usage", READER)`` (``cost_dimensions.py``),
            # ``{"get_capture_usage": READER}``, or a dotted path held in a
            # constant. Scoping this arm to ``Call``/``Subscript`` read the last two
            # as CLEAN while the text scan it replaced caught them — a narrowing,
            # not the strengthening the docstring claimed (#10962 review P2).
            # ``endswith`` covers the dotted path, and it is CONSTRAINED to the
            # ``metering`` segment: a bare ``endswith`` also matched an error MESSAGE
            # (``"… could not be resolved: metering.get_capture_usage"``) and a
            # ``"%s.get_capture_usage"`` placeholder, both of which are prose and
            # would FALSE-RED the required scan. ``id(node) not in docstrings``
            # keeps a docstring that IS the path from reading as a consumer.
            return True
    return False


def _scan_capture_call_sites(root):
    """Return ``(offending relative paths, every relative path actually read)``.

    A helper rather than an inline loop so the scan itself can be exercised
    against a fixture tree: a scan that is only ever run against the real tree
    is green whether or not it can still detect anything.
    """
    offenders, scanned = [], []
    for path in sorted(root.rglob("*.py")):
        if path.name in _ALLOWED:
            continue
        rel = str(path.relative_to(root))
        scanned.append(rel)
        if _names_the_reader(path):
            offenders.append(rel)
    return offenders, scanned


def test_the_gate_is_the_readers_only_consumer():
    """§1b's scope is NARROWED, not dropped: the gate, and nothing else (#9567).

    ``#4051`` §1b recorded this reader as measurement-only, with zero call sites.
    The ``#9567`` change arms the ruled token dimension and has no other meter to
    read, so the gate became a consumer — a scope change recorded in
    ``docs/architecture/CONSUMPTION-MODEL.md`` §6 (with the reopen on ``#4051``),
    not a silent one. The pin is kept, and made STRICTER than an exemption: the
    offender list must be EXACTLY ``allowance_gate.py``. A blanket exemption for
    that module would stay green when a report, dashboard or tool added a second
    call site; an exact equality also reds if the gate stops reading the meter,
    which is the fail-OPEN direction (a configured dimension with no reading).

    Scans EVERY module under ``tortoise/`` except the modules that DEFINE the
    reader. A call site in ``hosted_api`` would put it on another request path;
    one in ``tool_registry`` / ``sdk`` / ``hosted_mcp`` would add surface. All
    three are the #4051 exclusions, so the pin covers the whole tree rather than
    the one obvious file (a review finding: a one-file pin stays green while the
    reader is wired in elsewhere).

    The FLOOR below is load-bearing, not decoration. ``rglob`` over a wrong
    root — or a moved package, or a rename — yields nothing, and then
    ``offenders == []`` is GREEN while the pin inspected no file at all: a
    guard that checks nothing reads exactly like a guard that found nothing.
    ``hosted_api.py`` is asserted BY NAME for the same reason: it is the one
    module whose call site would silently make this a request-path change, so
    the scan must be proven to have covered it.
    """
    from pathlib import Path

    root = Path(metering.__file__).parent
    offenders, scanned = _scan_capture_call_sites(root)
    assert len(scanned) > 100, (
        f"the scope scan read only {len(scanned)} module(s) under {root} — a scan "
        "that inspected nothing cannot certify that nothing calls the reader"
    )
    assert "hosted_api.py" in scanned, (
        "the scope scan did not read tortoise/hosted_api.py, the module whose call "
        f"site would put the reader on a request path; read: {scanned[:5]}…"
    )
    assert set(offenders) == _ENFORCEMENT_CONSUMERS, (
        "get_capture_usage must be read by the allowance gate and NOTHING else "
        f"(#9567 narrows #4051 §1b); it is referenced in {offenders}"
    )


def test_the_scope_scan_flags_a_wired_call_site(tmp_path):
    """Non-vacuity: the scan can still DETECT — a real call site reddens it.

    Run against a fixture tree rather than the repo, so the assertion is about
    the scanner's discriminating power and not about today's tree happening to
    be clean.
    """
    (tmp_path / "hosted_api.py").write_text(
        "def f(org):\n    return metering.get_capture_usage(org)\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("X = 1\n", encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert offenders == ["hosted_api.py"]
    assert scanned == ["clean.py", "hosted_api.py"]


def test_a_comment_naming_the_reader_is_not_a_consumer(tmp_path):
    """The false-POSITIVE direction: prose about the reader is not a consumer.

    ``hosted_api.py`` documents its capture door as *"its meter is
    `metering.get_capture_usage`"*. A substring scan reads that comment as a
    second consumer and reds the tree on the file that explains the
    relationship (the ``#10675`` class). Pinned so the scanner cannot regress
    to text matching: a module mentioning the reader ONLY in a comment and a
    docstring must stay OFF the offender list, while a bare name reference in
    code must still be caught.
    """
    (tmp_path / "documents_it.py").write_text(
        '"""Why the door is metered: see get_capture_usage."""\n'
        "# and its meter is `metering.get_capture_usage`,\n"
        "X = 1  # get_capture_usage\n",
        encoding="utf-8")
    (tmp_path / "calls_it.py").write_text(
        "from tortoise.metering import get_capture_usage\n"
        "Y = get_capture_usage\n",
        encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert scanned == ["calls_it.py", "documents_it.py"]
    assert offenders == ["calls_it.py"], (
        "prose naming the reader must not read as a consumer; a bare name "
        "reference in code must still be one"
    )


def test_the_scan_catches_a_renamed_or_dynamically_named_consumer(tmp_path):
    """The false-NEGATIVE direction: renaming the reader must not hide a consumer.

    A first version of ``_names_the_reader`` matched only ``ast.Attribute`` /
    ``ast.Name``, and so read all of these as CLEAN — a narrowing the substring
    scan it replaced did not have (found in review). Aliasing an import is house
    style here (``allowance_gate.py:196``), so a second consumer written that way
    would have cleared the pin silently. ``setattr`` is paired with ``getattr``
    because both take the name as a literal argument, and the cost of missing
    one is the same fail-open — each gets its own fixture, because an arm that
    is never exercised can be deleted without reddening anything. The
    star-import case is here for the ``ast.Name`` arm specifically: every other
    fixture reaches the reader through an ``Import``/``Attribute`` node, so
    without it that arm could be replaced by ``if False:`` and the file would
    stay green (found in review).
    """
    (tmp_path / "aliased.py").write_text(
        "from tortoise.metering import get_capture_usage as gcu\nX = gcu(1)\n",
        encoding="utf-8")
    (tmp_path / "reexported.py").write_text(
        "from tortoise.metering import get_capture_usage  # noqa: F401\n",
        encoding="utf-8")
    (tmp_path / "dynamically.py").write_text(
        'X = getattr(metering, "get_capture_usage")(1)\n', encoding="utf-8")
    (tmp_path / "dynamically_set.py").write_text(
        'setattr(metering, "get_capture_usage", None)\n', encoding="utf-8")
    (tmp_path / "starimported.py").write_text(
        "from tortoise.metering import *\nX = get_capture_usage\n", encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert scanned == [
        "aliased.py", "dynamically.py", "dynamically_set.py", "reexported.py",
        "starimported.py",
    ], f"every module must have been examined; read: {scanned}"
    assert sorted(offenders) == [
        "aliased.py", "dynamically.py", "dynamically_set.py", "reexported.py",
        "starimported.py",
    ], (
        "an aliased import, a bare re-export, getattr/setattr by literal name, and a "
        f"star-imported bare name are all consumers; only these were found: {offenders}"
    )


def test_the_scan_catches_the_reader_named_as_a_string_in_ANY_call(tmp_path):
    """The gap the ``getattr``/``setattr``-only arm left open (#10962 review P2).

    A consumer can name the reader as a string literal of a call the scanner does
    not know: this repo does exactly that at ``tortoise/cost_dimensions.py:534``
    — ``Binding("tortoise.metering", "get_capture_usage", READER)`` — and the
    ``_ALLOWED`` exemption for that file exists *because* a name-in-a-call counts
    as a reference. The pre-merge text scan (main's ``_text_without_comments``)
    flagged it; an arm that recognised only ``getattr``/``setattr`` read it as
    CLEAN, so a SECOND ``Binding``-style registration (or a ``registry[...]``
    lookup) would have landed without reddening the "the gate, and NOTHING else"
    pin — the fail-open direction the pin exists to prevent.

    Prose stays invisible because the value must BE the name or END WITH it: a
    log message that merely mentions the dotted path is not a consumer, and
    neither is a docstring. The arm is not limited to call arguments — the other
    two spellings have their own leg below
    (``test_the_scan_catches_the_reader_named_as_a_string_OUTSIDE_a_call``).
    """
    (tmp_path / "registered.py").write_text(
        'from tortoise.cost_dimensions import Binding, READER\n'
        'X = Binding("tortoise.metering", "get_capture_usage", READER)\n',
        encoding="utf-8")
    (tmp_path / "registered_as_keyword.py").write_text(
        'X = register(name="get_capture_usage")\n', encoding="utf-8")
    (tmp_path / "subscripted.py").write_text(
        'X = REGISTRY["get_capture_usage"]\n', encoding="utf-8")
    (tmp_path / "logs_it.py").write_text(
        'logger.info("metering.get_capture_usage is the reader")  # prose, not a call site\n',
        encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert scanned == [
        "logs_it.py", "registered.py", "registered_as_keyword.py", "subscripted.py",
    ]
    assert sorted(offenders) == [
        "registered.py", "registered_as_keyword.py", "subscripted.py",
    ], (
        "the reader named as a string literal of ANY call (a ``Binding`` registration, "
        "a keyword argument) or looked up as a subscript key is a consumer; only "
        f"these were found: {offenders}"
    )


def test_the_scan_catches_the_reader_named_as_a_string_OUTSIDE_a_call(tmp_path):
    """The shapes a ``Call``/``Subscript``-scoped arm read as CLEAN (#10962 review P2).

    The sibling leg above pins the call shapes. These two spellings reach the
    same reader and were caught by main's pre-merge text scan, so an AST arm
    scoped to ``Call``/``Subscript`` NARROWED what it replaced rather than
    strengthening it: a dict-literal registration
    (``{"get_capture_usage": READER}``) is the ``Binding``-style registration in
    another spelling, and a dotted path held in a constant is what an
    ``importlib``/plugin loader is handed. Each gets its own fixture, because an
    arm that is never exercised can be deleted without reddening anything.

    ⛔ The remaining three fixtures are the false-POSITIVE half, and they are
    pinned HERE rather than left implicit: once the arm matches any string
    Constant, a docstring whose entire body IS the dotted path, an error MESSAGE
    that ends in the path, and a log FORMAT placeholder that ends in it must all
    stay off the offender list. The last two red on an unconstrained
    ``endswith`` (measured) — a false RED on the required scan.
    """
    (tmp_path / "dict_registered.py").write_text(
        'from tortoise.cost_dimensions import READER\n'
        'REGISTRY = {"get_capture_usage": READER}\n',
        encoding="utf-8")
    (tmp_path / "dotted_path.py").write_text(
        'PATH = "tortoise.metering.get_capture_usage"\n',
        encoding="utf-8")
    (tmp_path / "docstring_is_the_path.py").write_text(
        '"""tortoise.metering.get_capture_usage"""\nX = 1\n',
        encoding="utf-8")
    (tmp_path / "error_message.py").write_text(
        'raise RuntimeError('
        '"meter could not be resolved: metering.get_capture_usage")\n',
        encoding="utf-8")
    (tmp_path / "format_placeholder.py").write_text(
        'logger.info("%s.get_capture_usage", mod)\n', encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert scanned == [
        "dict_registered.py", "docstring_is_the_path.py", "dotted_path.py",
        "error_message.py", "format_placeholder.py",
    ]
    assert sorted(offenders) == ["dict_registered.py", "dotted_path.py"], (
        "a dict-literal registration and a dotted path in a constant are both "
        "consumers, while a docstring that IS the path, an error message ending "
        "in it and a ``%s`` format placeholder ending in it are prose; found: "
        f"{offenders}"
    )


def test_an_unparsable_module_reds_instead_of_reading_as_clean(tmp_path):
    """Unreadable must never equal clean — the floor cannot catch this.

    An unparsable file is still appended to ``scanned``, so it satisfies the
    ``>100`` floor while never being examined. Returning False there would make
    a guard that checked nothing indistinguishable from one that found nothing.
    """
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    try:
        _scan_capture_call_sites(tmp_path)
    except AssertionError as exc:
        assert "UNREADABLE" in str(exc)
    else:  # pragma: no cover - the assertion below is the failure
        raise AssertionError("an unparsable module was read as CLEAN")


def test_the_scope_scan_still_flags_a_dynamic_reference_in_a_string(tmp_path):
    """The AST must not become an identifier-only scan.

    Main's pre-merge ``_text_without_comments`` repair (``71832318b``, landed
    with #10935) stripped COMMENT tokens and kept STRING tokens in scope, and
    this leg is the half of that repair worth keeping: a reference assembled as
    a literal string (``getattr(metering, "get_capture_usage")``) is still a
    wiring of the reader. The comment-stripping half is subsumed rather than
    dropped — ``test_a_comment_naming_the_reader_is_not_a_consumer`` above
    excludes a comment AND a docstring AND still catches a real code reference,
    which is strictly stronger than a comment strip alone. Kept as a distinct
    fixture shape because the string-in-a-call arm is the one an
    identifier-only scanner silently loses.
    """
    (tmp_path / "tool_registry.py").write_text(
        'READER = getattr(metering, "get_capture_usage")\n', encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert offenders == ["tool_registry.py"]
    assert scanned == ["tool_registry.py"]


def test_the_scope_scan_exempts_only_the_defining_modules(tmp_path):
    """The exemption set is exact: the defining modules are skipped, and ONLY them."""
    for name in _ALLOWED:
        (tmp_path / name).write_text(
            "def get_capture_usage(org):\n    return {}\n", encoding="utf-8")
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert (offenders, scanned) == ([], [])


def test_an_unreadable_root_is_caught_by_the_floor_not_read_as_a_pass(tmp_path):
    """A wrong root produces ``[]`` offenders — which is why the floor exists.

    Pinned so the floor cannot be dropped as redundant: without it, this empty
    directory is indistinguishable from a clean tree (#3400/#4348 shape, applied
    to a scan instead of a manifest row).
    """
    offenders, scanned = _scan_capture_call_sites(tmp_path)
    assert (offenders, scanned) == ([], [])
    assert not len(scanned) > 100, "the floor must reject a root that yielded no files"


# ── the supabase (production) substrate ─────────────────────────────────────


def test_supabase_read_maps_the_capture_columns():
    """The production ledger post-#669 must be covered, not just the registry.

    A review finding: every other test here forces ``_supabase_mode`` False, so a
    wrong column or filter in the control-plane read would return no row, be
    swallowed by the reader's degradation, and serve the ZERO view for every
    tenant on the production ledger — permanently, with a green suite. That is
    the silent-zero class the capture-tokens migration's own header warns about.
    """
    import tortoise.supabase_control as sc
    from tests.fake_control_plane import FakeControlPlane

    start = "2026-11-01T00:00:00+00:00"
    fake = FakeControlPlane()
    fake.tables.setdefault("metering_records", []).append({
        "org_id": "org_x",
        "period_start": start,
        "capture_calls": 4,
        "capture_tokens_in": 1400,
        "capture_tokens_out": 320,
        "capture_cost_usd": 0.0075,
    })
    got = sc.metering_get_capture_usage(fake, "org_x", start)
    assert got == {"measured": True, "unmeasured_reason": None,
                   "capture_calls": 4, "capture_tokens_in": 1400,
                   "capture_tokens_out": 320, "capture_cost_usd": 0.0075}


def test_supabase_read_is_zero_for_a_different_window_or_org():
    """The filters are load-bearing: the row is keyed on (org, period_start)."""
    import tortoise.supabase_control as sc
    from tests.fake_control_plane import FakeControlPlane

    start = "2026-11-01T00:00:00+00:00"
    fake = FakeControlPlane()
    fake.tables.setdefault("metering_records", []).append({
        "org_id": "org_x", "period_start": start, "capture_calls": 4,
        "capture_tokens_in": 1400, "capture_tokens_out": 320,
        "capture_cost_usd": 0.0075,
    })
    zeros = {"measured": False, "unmeasured_reason": "absent",
             "capture_calls": 0, "capture_tokens_in": 0,
             "capture_tokens_out": 0, "capture_cost_usd": 0.0}
    assert sc.metering_get_capture_usage(fake, "org_other", start) == zeros
    assert sc.metering_get_capture_usage(
        fake, "org_x", "2026-10-01T00:00:00+00:00") == zeros


def test_supabase_branch_passes_the_window_start_to_the_control_plane(monkeypatch):
    """The three-line branch that picks the production substrate must carry
    ``period.start_iso``, not the derived month label (#3825's bug class).

    Every other test here forces ``_supabase_mode`` False, so without this the
    branch is unexecuted: a later edit passing ``period.label`` would be caught
    by the ``except`` and every tenant on the production ledger would read the
    zero view with a green suite.
    """
    seen = {}

    def _reader(cp, org_id, period_start):
        seen["args"] = (org_id, period_start)
        return {"measured": True, "unmeasured_reason": None,
                "capture_calls": 7, "capture_tokens_in": 900,
                "capture_tokens_out": 100, "capture_cost_usd": 0.002}

    import tortoise.supabase_control as sc

    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "metering_get_capture_usage", _reader)

    view = metering.get_capture_usage("org_x")
    assert view["capture_calls"] == 7 and view["capture_tokens_in"] == 900
    assert seen["args"] == ("org_x", "2026-11-01T00:00:00+00:00"), (
        "the supabase branch must pass the WINDOW START, not the month label")


# ── the reader's contract ───────────────────────────────────────────────────


def test_no_org_is_the_zero_view_and_never_raises():
    view = metering.get_capture_usage("")
    assert all(view[k] == 0 for k in _KEYS)
    assert view["period_start"] is None and view["period_end"] is None


def test_unresolvable_window_degrades_to_zeros(monkeypatch):
    def _boom(_org_id):
        raise RuntimeError("anchor unavailable")

    monkeypatch.setattr(metering, "_current_period", _boom)
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)
    # #923/#3825: an unresolvable window renders the zero view with a
    # placeholder label — never a month KEY that would read as a real period.
    assert view["period_start"] is None
    # …and it is a FAULT, not an absence: flattening the two would let a broken
    # clock read as "nothing spent" and disarm the allowance (fail OPEN).
    assert view["measured"] is False
    assert view["unmeasured_reason"] == "period_error"


def test_registry_row_maps_to_the_capture_keys(monkeypatch):
    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk",
                        lambda: _fake_sdk([[3, 1234, 567, 0.0042]]))
    view = metering.get_capture_usage("org_x")
    assert view["capture_calls"] == 3
    assert view["capture_tokens_in"] == 1234
    assert view["capture_tokens_out"] == 567
    assert view["capture_cost_usd"] == pytest.approx(0.0042)
    # ⚠️ the PRESENCE half (#4051 + #9567 reconciled on one function): a row IS a
    # reading even when every counter in it is zero, and the gate may rely on it.
    assert view["measured"] is True
    assert view["unmeasured_reason"] is None
    assert view["capture_tokens_total"] == 1801
    assert view["period"] == "2026-11"
    assert view["period_start"] == "2026-11-01T00:00:00+00:00"


def test_no_row_is_zeros_not_an_error(monkeypatch):
    """The MERGE creates the row on the first write — an empty read is not a fault."""
    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk", lambda: _fake_sdk([]))
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)
    assert view["period"] == "2026-11"
    # "nobody has captured yet" — a named ABSENCE the gate treats as a zero for a
    # cumulative flow, never as a measurement.
    assert view["measured"] is False
    assert view["unmeasured_reason"] == "absent"


def test_null_columns_read_as_zero(monkeypatch):
    """A ledger row that never carried tokens must not raise or return None."""
    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk", lambda: _fake_sdk([[None, None, None, None]]))
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)


def test_a_failed_read_degrades_and_never_raises(monkeypatch):
    """A control-plane blip must never 500 a usage read (#923)."""

    def _boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr(metering, "_current_period", lambda _org: _period())
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False)
    monkeypatch.setattr(metering, "_reg_sdk", _boom)
    view = metering.get_capture_usage("org_x")
    assert all(view[k] == 0 for k in _KEYS)
    # the zero view is kept (#923) AND the fault is NAMED — the gate refuses on
    # this reason rather than reading a broken meter as zero usage.
    assert view["measured"] is False
    assert view["unmeasured_reason"] == "read_error"
