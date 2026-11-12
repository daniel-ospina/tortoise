"""
Issue #6902 — Audit: confirm no production code depends on BFS propagate_shock output.

This test file encodes the audit findings as assertions. It does NOT run against
a live FalkorDB instance — it enumerates candidate references with `grep` and
then decides what each one IS — code, a definition, or prose — from `tokenize`
(the code/prose split) and `ast` (which statements are prose).
Run with: python3 -m pytest tests/test_bfs_audit.py -v
"""

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ⛔ No module-level cache on purpose: a cache keyed by path alone answers from
# the FIRST read, so an arm that mutates a file and re-classifies in the same
# process would read a stale set and stay green over the mutation. Re-reading and
# re-parsing a handful of hit files is nothing beside that hazard.
def _code_lines_naming(path: Path, name: str) -> frozenset[int] | None:
    """The line numbers of ``path`` on which ``name`` appears as CODE.

    Why this exists (#11903). The classifier in
    :func:`test_no_propagate_shock_callers` exempted a ``tortoise/`` line from the
    production-code bucket only when it carried a ``#`` AND the word "replaces" —
    a heuristic keyed to the PROSE of one historical comment. Two docstring lines
    that DISCUSS the function (``propagation.py:70``/``:73``) matched neither, so
    a paragraph counted as a caller.

    ⛔ ONE RULE, and it enumerates no syntax. **A hit is code unless the name on
    that line is inside a comment or inside prose.** Two sources, unioned:

    1. every ``NAME`` token equal to the name — whatever its syntactic position.
       This is why there is no list of shapes to get wrong: a call, a callable
       passed as a value (``key=propagate_shock``, ``partial(...)``), a bare
       decorator ``@propagate_shock``, an ``import ... as`` alias, a reference
       anywhere, and the name on a later line of a multi-line expression are all
       ONE ``NAME`` token. Three successive drafts of this change enumerated AST
       node types instead and each enumeration silently missed a shape the old
       matcher had caught (first a callee, then a bare decorator, then an aliased
       import) — a token cannot be in the wrong position.
    2. every ``str`` **or** ``bytes`` literal whose VALUE contains the name,
       unless that string is prose — which covers the name resolved dynamically
       from a string (``getattr(m, "propagate_shock")``,
       ``globals()["propagate_shock"]``, ``eval("propagate_shock(1)")``, and
       ``getattr(m, b"propagate_shock".decode())``). The value is read from the
       AST, never from the literal's source text: implicit concatenation
       (``"propagate_" "shock"``), an escape (``"propagate_shoc\\x6b"``) and the
       same split across lines all fold to one ``ast.Constant`` whose value is the
       name, while no single TOKEN holds it. This over-includes a non-prose string
       that only talks about the function (a log line). Deliberate and declared:
       the pre-fix matcher counted those too, and the one thing this change must
       not do is hand back a quieter guard.

    A COMMENT token is prose by construction. A STRING token is prose when it is
    a statement in its own right — any ``Expr`` whose value is a string constant.
    That is PEP 257's docstring rule *and* its tolerated forms (an attribute
    docstring after an assignment, an additional docstring after the first, module
    prose after a ``from __future__`` import): a bare string statement evaluates
    nothing, so it is prose wherever it sits. A docstring is therefore identified
    by SHAPE, never by its wording — matching the wording is the defect class this
    file just removed.

    ``None`` means a file could not be read (any suffix), or a Python source
    file (``.py`` or ``.pyi`` — a stub declares identifiers too) could not be
    PARSED, and the caller reads it as "every line is code": a degraded read can
    only make the audit STRICTER.

    ⛔ A NON-Python suffix is NOT prose, and answering the empty set for one is a
    QUIETER guard than the matcher this replaced. ``tortoise/`` ships 16
    executable non-Python files today (7 ``.sh`` hooks, 6 ``.ts``, 3 ``.mjs``),
    and a hook embedding
    ``python3 -c 'from tortoise.projection.propagation import propagate_shock'``
    is a live dependency; the pre-fix matcher had no suffix notion at all, so it
    counted every such line. With no tokens to ask, the answer is therefore the
    LOUD one: on a non-Python file, a line that names it is code. Declared
    over-inclusion — a documentation mention under ``tortoise/`` reads as code
    too — in the same safe direction as the non-docstring-string residual above.
    """
    if path.suffix not in (".py", ".pyi"):
        try:
            # ⛔ BYTES, not `read_text`: text mode applies UNIVERSAL NEWLINES (a
            # lone `\r` becomes `\n`), so the file's own line breaks are gone
            # before any split can honour grep's numbering.
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        # No parser to ask, so the word boundary is the whole rule: it is a word
        # scan, never a syntax guess, and it errs LOUD on anything it cannot
        # place. `re.split` on the parser's own line endings keeps the returned
        # line numbers in the coordinate space `grep -rn` reports.
        word = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
        # Split on `\n` ALONE, because the caller's line number comes from
        # `grep -rn`, and grep counts only `\n`: on a file carrying a lone `\r`
        # (old-Mac line endings) any other split numbers the hit one line too
        # high, and the audit then reports the file as prose. The reference space
        # here is grep's, not a parser's.
        return frozenset(
            i
            for i, line in enumerate(text.split("\n"), start=1)
            if word.search(line)
        )
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, tokenize.TokenError,
            IndentationError):
        return None

    # ⛔ ONE COORDINATE SPACE, BY CONSTRUCTION — and NO conversion. Both boxes
    # compared below (the prose statement's, and the mentioning constant's) come
    # from `ast`, whose `col_offset`/`end_col_offset` are UTF-8 BYTE offsets
    # (CPython ASDL: "col_offset is the byte offset in the utf8 string the parser
    # uses"). Byte-vs-byte is unit-consistent and monotonic within a line, so
    # containment is decided correctly for non-ASCII content with no converter in
    # the middle. An earlier revision compared a TOKEN's character column against
    # an AST byte column and needed one; the string branch now reads the tree, so
    # that converter had become INERT (measured: removing it left every row green
    # and every verdict identical over 100+ non-ASCII shapes). It is deleted
    # rather than kept with a comment claiming work it no longer does — and the
    # three em-dash rows that pinned it are gone for the same reason: a row that
    # cannot fail is not a pin.
    prose_boxes: list[tuple[int, int, int, int]] = []

    def _box(node: ast.AST) -> tuple[int, int, int, int] | None:
        """The (top, left, bottom, right) box of an AST node, in AST columns."""
        right_line = getattr(node, "end_lineno", None) or node.lineno
        right_col = getattr(node, "end_col_offset", None)
        if right_col is None:
            return None
        return (node.lineno, node.col_offset, right_line, right_col)

    # Prose strings: any statement whose whole value is a string literal, as an
    # exact BOX rather than a line range.
    # ⛔ LINE-granularity is a fail-open: `"""Doc."""; x = getattr(m,
    # "propagate_shock")` puts a live string-resolved dependency on a line the
    # docstring owns, and a line test discards it — the audit then reports no
    # dependency while one exists. Containment is column-aware, so only a literal
    # lying INSIDE the prose statement's box is prose; the string after the
    # closing quotes is code.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            box = _box(node.value)
            if box is not None:
                prose_boxes.append(box)

    def _inside_prose(start: tuple[int, int], end: tuple[int, int]) -> bool:
        return any(
            (top, left) <= start and end <= (bottom, right)
            for top, left, bottom, right in prose_boxes
        )

    lines: set[int] = set()

    # ⛔ THE STRING BRANCH READS THE LITERAL'S **VALUE**, NEVER ITS SOURCE TEXT.
    # Matching the raw token text is a fail-open in shapes whose source spelling
    # differs from the value the runtime resolves: implicit concatenation
    # `getattr(m, "propagate_" "shock")` (tokenize emits TWO STRING tokens, neither
    # holding the name), an escape `"propagate_shoc\x6b"`, and the same
    # concatenation split ACROSS lines. The AST holds the folded value in all of
    # them — `ast.Constant.value == "propagate_shock"` — so the check is asked of
    # the value, and the box that decides prose-vs-code is the CONSTANT's box (the
    # whole folded literal in the multi-line case), not a token's.
    #
    # ⛔ AND IT COVERS `bytes`, because a bytes literal reaches the same runtime
    # name through `.decode()`: `getattr(m, b"propagate_shock".decode())`. A
    # `str`-only test drops that, and the pre-fix matcher CAUGHT it (the token's
    # source text holds the name) — so str-only would be a quieter guard than the
    # one this change replaced, in exactly the direction it forbids.
    #
    # An f-string's literal parts need no special case under this rule: a
    # `JoinedStr` holds them as `ast.Constant` nodes, so they are seen here (3.12's
    # FSTRING_MIDDLE token type is a tokenizer detail, not a fact about the tree).
    needle = name.encode("utf-8")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))):
            continue
        value = node.value
        if not (name in value if isinstance(value, str) else needle in value):
            continue
        box = _box(node)
        if box is None:
            continue
        # A prose statement's OWN value is inside its own box, so this single
        # containment test excludes it — no parent lookup, no second rule.
        if _inside_prose(box[:2], box[2:]):
            continue
        lines.update(range(box[0], box[2] + 1))

    # ⛔ THE NAME BRANCH READS TOKENS, not the tree: a token cannot be in the wrong
    # syntactic position, so a call, a callee passed as a value, a bare decorator,
    # an `import … as` alias and a reference on a later line of a multi-line
    # expression are all the same one answer.
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string == name:
            lines.add(tok.start[0])
    return frozenset(lines)


def _classify_tortoise_hit(path: Path, lineno: int) -> str:
    """Which bucket a `grep` hit inside ``tortoise/`` belongs in.

    Split out of :func:`test_no_propagate_shock_callers` so the CALLER's own arm
    is reachable by a test. The audit's consequence of a DEGRADED read lives
    here — ``_code_lines_naming`` returning ``None`` means this file could not
    be read, and a guard that cannot read a file must not call it clean, so
    ``None`` is routed to ``production_code``.

    ⛔ Pinned by :func:`test_an_unreadable_file_is_routed_to_production_code`, not
    only by the helper's own tests: reading ``None`` as prose leaves every
    helper-level row green while turning the audit's degraded-read arm into a
    fail-open.
    """
    call_lines = _code_lines_naming(path, "propagate_shock")
    if call_lines is None or lineno in call_lines:
        return "production_code"
    return "prose only"


def _grep(pattern: str, *dirs: str) -> list[str]:
    """Return non-empty lines from grep -rn in specified dirs (relative to ROOT)."""
    try:
        result = subprocess.run(
            ["grep", "-rn", pattern, *[str(ROOT / d) for d in dirs]],
            capture_output=True, text=True, timeout=15,
        )
        lines = result.stdout.strip().split("\n")
        return [l for l in lines if l.strip()]  # noqa: E741
    except subprocess.TimeoutExpired:
        return []


# ── 0. the call detector itself ──────────────────────────────────
#
# Pinned DIRECTLY, not only through `test_no_propagate_shock_callers`. The
# audit's real corpus contains no code mention of the name at all, so on the
# corpus alone every branch of `_code_lines_naming` except the "nothing here"
# path is unexecuted — which is how three fail-open shapes survived earlier
# drafts: a bare `@decorator` (not an `ast.Call`), a multi-line callee (the name
# is not on `node.lineno`), and a callable passed as a VALUE (`key=`, `partial`)
# which is not in callee position either.

def test_the_code_detector_sees_every_shape_it_claims(tmp_path):
    """Every shape that is a CODE dependency is detected; only prose is not."""
    # label -> (source, lines that MUST be in the code set). A subset assertion,
    # because a multi-line node records its whole range on purpose.
    cases: dict[str, tuple[str, set[int]]] = {
        # ── identifier in ANY position, which is what "depends on" means ──
        "plain_call": ("value = propagate_shock(1)\n", {1}),
        "attribute_call": ("value = mod.propagate_shock(1)\n", {1}),
        "reference_as_value_key": ("v = sorted([1], key=propagate_shock)\n", {1}),
        "reference_as_value_partial": (
            "from functools import partial\nv = partial(propagate_shock, 1)\n", {2}),
        "reference_bare_assignment": ("f = propagate_shock\n", {1}),
        "decorator": ("@propagate_shock\ndef f():\n    return 1\n", {1}),
        "decorator_attribute": ("@mod.propagate_shock\ndef f():\n    return 1\n", {1}),
        "decorator_called": ("@propagate_shock(1)\ndef f():\n    return 1\n", {1}),
        "decorator_on_a_class": ("@propagate_shock\nclass C:\n    pass\n", {1}),
        "nested_and_lambda": (
            "def f():\n    g = lambda v: propagate_shock(v)\n    return g\n", {2}),
        # ── an IMPORT alias: the only occurrence of the name is on the import
        # line, and three earlier drafts each missed a shape by
        # enumerating AST node types instead of reading tokens. ──
        "aliased_import": (
            "from mod import propagate_shock as shock\n", {1}),
        "aliased_import_then_used": (
            "from mod import propagate_shock as shock\ndef f():\n    return shock(1)\n", {1}),
        "import_as_the_name": ("import pkg as propagate_shock\n", {1}),
        "reexport_only": ("from mod import propagate_shock\n", {1}),
        # ── the name resolved from a STRING ──
        "string_getattr": ('value = getattr(m, "propagate_shock")(1)\n', {1}),
        "string_getattr_multiline": (
            'value = getattr(\n    m,\n    "propagate_shock",\n)(1)\n', {3}),
        "string_qualified_getattr": (
            'value = builtins.getattr(m, "propagate_shock")(1)\n', {1}),
        "string_subscript_dynamic": ('value = globals()["propagate_shock"](1)\n', {1}),
        "string_in_eval": ('value = eval("propagate_shock(1)")\n', {1}),
        # ⛔ The literal's SOURCE SPELLING is not its VALUE. These three resolve to
        # the name at runtime while NO single TOKEN holds it, so a token-text test
        # reads them as prose: implicit concatenation, an escape, and the same
        # concatenation split across lines (the AST Constant then spans both).
        "string_implicit_concat": (
            'value = getattr(m, "propagate_" "shock")(1)\n', {1}),
        "string_escape": ('value = getattr(m, "propagate_shoc\\x6b")(1)\n', {1}),
        "string_dunder_all_concat": ('__all__ = ["propagate_" "shock"]\n', {1}),
        # ⛔ `bytes`: the same runtime name through `.decode()`. A str-only value
        # test drops it and the pre-fix matcher caught it, so this row is the
        # difference between a guard that gets louder and one that gets quieter.
        "bytes_getattr": ('value = getattr(m, b"propagate_shock".decode())(1)\n', {1}),
        "bytes_implicit_concat": ('value = getattr(m, b"propagate_" b"shock".decode())\n', {1}),
        "string_implicit_concat_multiline": (
            'value = getattr(\n    m,\n    "propagate_"\n    "shock",\n)(1)\n', {3, 4}),
        # ── multi-line callee: the name is NOT on `node.lineno` ──
        "chained_multiline": (
            "value = (\n    _BASE\n    .strip()\n    .propagate_shock(1)\n)\n", {4}),
        # ── DELIBERATE over-inclusion: a non-docstring string that merely talks
        # about the function is read as code. The pre-fix matcher counted these
        # too, and the one thing this change must not do is hand back a quieter
        # guard; over-inclusion is the safe direction.
        "over_inclusion_plain_string": ('msg = "propagate_shock is deprecated"\n', {1}),
        "over_inclusion_fstring": ('msg = f"propagate_shock {1}"\n', {1}),
        # ── PROSE, excluded: comments are not in the AST at all, and docstrings
        # are identified by POSITION (first statement of a module/class/def),
        # never by their wording. ──
        "comment": ("# propagate_shock is deprecated\nvalue = 1\n", set()),
        "docstring": ('def f():\n    """propagate_shock is deprecated."""\n', set()),
        "docstring_multiline": (
            'def f():\n    """S2 VET \u2014 the pass\n\n    propagate_shock PERSISTS the\n    blend; it is never invoked.\n    """\n', set()),
        # ⛔ This row is the whole "a NON-ASCII docstring's prose mention is prose"
        # claim, and it is the ONLY one: two earlier rows
        # (`long_non_ascii_docstring_mention_on_closing_line`,
        # `docstring_non_ascii_mention_on_closing_line`) asserted they pinned the
        # byte/char drift "where the box's right edge is tested", but their mention
        # sits on an INTERIOR line, so containment compares (4, col) <= (5, …) and
        # the columns never decide — both were green under every unit, including
        # A docstring whose columns never decide is not a unit pin. This row stays
        # for the plain regression it names — a prose mention inside a non-ASCII
        # module/class docstring is prose — and the `_non_ascii` rows above are
        # gone for the same reason two earlier rows went: they could not fail.
        "class_docstring": ('class C:\n    """propagate_shock is deprecated."""\n', set()),
        "module_docstring": ('"""propagate_shock is deprecated."""\nvalue = 1\n', set()),
        # PEP 257's TOLERATED forms, which a "first statement of a def" rule misses:
        "attribute_docstring": (
            'class C:\n    x = 1\n    """attribute doc: propagate_shock is deprecated."""\n', set()),
        "additional_docstring": (
            'def f():\n    """first.\n\n    propagate_shock mentioned here.\n    """\n    """follow-up: propagate_shock is deprecated."""\n    return 1\n', set()),
        "module_prose_after_future": (
            'from __future__ import annotations\n\n"""propagate_shock is deprecated."""\n', set()),
        # The prose span's END, not just its start: with implicit concatenation
        # tokenize emits TWO STRING tokens, but the AST is ONE Constant whose
        # span is the pair — so the name sits on a line AFTER the span's start.
        # A live string-resolved dependency sharing a line with a docstring. A
        # line-granular prose test discards it; column containment does not.
        "docstring_then_string_same_line": (
            'def f():\n    """Doc."""; x = getattr(m, "propagate_shock")\n', {2}),
        "docstring_then_plain_string_same_line": (
            'def f():\n    """Doc."""; y = "propagate_shock"\n', {2}),
        # Columns are AST BYTE offsets on both sides of the containment test, by
        # construction, so non-ASCII content needs no separate unit row: an
        # ASCII-only row decides the same thing. (Two such rows were deleted here
        # after measuring that they could not fail — see the coordinate-space note
        # in `_code_lines_naming`.)
        # ⛔ A prose box that SPANS lines: the docstring starts on line 2 and ends
        # on line 3, and the live dependency shares line 3. Collapsing the box to
        # its start line reds this row (measured), while the single-line rows
        # above cannot see that regression. The em dashes are incidental content.
        "multiline_non_ascii_docstring_then_string_same_line": (
            'def f():\n    """\u2014 the pass\n    ' + "\u2014" * 18
            + '"""; x = getattr(m, "propagate_shock")\n', {3}),
        "implicit_concat_docstring": (
            'def f():\n    ("prose part one "\n     "propagate_shock mentioned here")\n', set()),
    }
    for label, (src, expected) in cases.items():
        path = tmp_path / f"{label}.py"
        path.write_text(src, encoding="utf-8")
        got = _code_lines_naming(path, "propagate_shock")
        assert got is not None, f"{label}: parsed source returned None"
        assert expected <= got, f"{label}: expected code lines {expected} \u2286 {sorted(got)}"
        if not expected:
            assert got == frozenset(), f"{label}: prose read as code \u2014 {sorted(got)}"


def test_a_non_python_file_is_read_as_code_not_as_prose(tmp_path):
    """A NON-Python file under `tortoise/` that names the helper is CODE.

    `tortoise/` ships executable non-Python files — `.sh` hooks, `.ts`/`.mjs`
    plugin sources — and a hook that shells out to
    ``python3 -c 'from tortoise.projection.propagation import propagate_shock'``
    is a live dependency. The pre-fix matcher counted every such line (it had no
    suffix notion), so a suffix gate answering the empty set would be a QUIETER
    guard than the one it replaced, for a file class that exists in the tree.
    With no tokens to ask, the answer is the loud one.

    Declared over-inclusion: a documentation mention under `tortoise/` also reads
    as code here. That is the safe direction — it can only red the audit, never
    green it.

    `.pyi` is a DIFFERENT case — a stub is Python source and is parsed as such —
    and is pinned by :func:`test_a_stub_file_is_read_as_python_not_as_prose`.
    """
    for suffix in (".md", ".sql", ".txt", ".yml", ".sh", ".ts", ".mjs", ".json"):
        path = tmp_path / f"notes{suffix}"
        path.write_text(
            "intro line\nThe deprecated helper propagate_shock is documented here.\n",
            encoding="utf-8")
        assert _code_lines_naming(path, "propagate_shock") == frozenset({2}), suffix

    # The shape that actually loses coverage if non-Python files are read as
    # prose: a shell hook embedding the import.
    hook = tmp_path / "session-end.sh"
    hook.write_text(
        "#!/bin/sh\npython3 -c '\n"
        "from tortoise.projection.propagation import propagate_shock\n'\n",
        encoding="utf-8")
    assert _code_lines_naming(hook, "propagate_shock") == frozenset({3})

    # A non-Python file that never names it answers the empty set.
    quiet = tmp_path / "quiet.md"
    quiet.write_text("nothing relevant here\n", encoding="utf-8")
    assert _code_lines_naming(quiet, "propagate_shock") == frozenset()

    # ...and an UNREADABLE non-Python file is a degraded read, not a clean one.
    assert _code_lines_naming(tmp_path / "absent.sh", "propagate_shock") is None

    # ⛔ LINE NUMBERS ARE GREP'S, not a parser's: `grep -rn` counts only `\n`, so a
    # file carrying a lone `\r` must be numbered the same way. Splitting on `\r`
    # too numbers the hit one line too high, the caller's grep line then misses it,
    # and a file that names the helper is reported as prose.
    cr = tmp_path / "mac.md"
    cr.write_bytes(b"prose line 1\rline2: propagate_shock is imported by the hook\r")
    assert _code_lines_naming(cr, "propagate_shock") == frozenset({1}), "grep numbers this line 1"

    # The word scan must not match a LONGER identifier that merely contains it.
    longer = tmp_path / "longer.md"
    longer.write_text("xpropagate_shocky and propagate_shocked\n", encoding="utf-8")
    assert _code_lines_naming(longer, "propagate_shock") == frozenset()


def test_a_stub_file_is_read_as_python_not_as_prose(tmp_path):
    """A `.pyi` stub is Python source: it declares and re-exports names, so a name
    inside one IS a code reference.

    Pinned because the suffix gate is the only thing that decides this, and a stub
    answering the empty set is read as "prose only" by the audit — so a module
    could start re-exporting the helper and the audit would stay GREEN. The typing
    spec and every static analyser (mypy, Pyright, ruff's `flake8-pyi`) parse
    stubs as Python source for exactly that reason.
    """
    stub = tmp_path / "mod.pyi"
    for src in (
        "def propagate_shock(m: object) -> object: ...\n",
        "from tortoise.projection.propagation import propagate_shock as _ps\n",
        "propagate_shock(1)\n",
    ):
        stub.write_text(src, encoding="utf-8")
        assert _code_lines_naming(stub, "propagate_shock") == frozenset({1}), src

    # An unparseable stub is a DEGRADED read (`None` -> "every line is code"),
    # never a prose verdict: a stub we cannot read is not a stub that is clean.
    stub.write_text("def f(:\n", encoding="utf-8")
    assert _code_lines_naming(stub, "propagate_shock") is None


def test_an_unreadable_file_is_routed_to_production_code(monkeypatch):
    """The CALLER's arm, not the helper's: a degraded read must fail LOUD.

    A helper-level test cannot see this. Reading `None` as prose leaves the
    helper's rows green while the audit silently passes over a file it could not
    read — so the routing is pinned here, by making the helper degrade.
    """
    this = sys.modules[__name__]
    monkeypatch.setattr(this, "_code_lines_naming", lambda _p, _n: None)
    assert _classify_tortoise_hit(Path("tortoise/unreadable.py"), 1) == "production_code"

    # ...and the other side of the same branch: a parsed file with no code hit.
    monkeypatch.setattr(this, "_code_lines_naming", lambda _p, _n: frozenset())
    assert _classify_tortoise_hit(Path("tortoise/parsed.py"), 1) == "prose only"

    # ...and a parsed file where THIS line carries the name.
    monkeypatch.setattr(this, "_code_lines_naming", lambda _p, _n: frozenset({7}))
    assert _classify_tortoise_hit(Path("tortoise/parsed.py"), 7) == "production_code"
    assert _classify_tortoise_hit(Path("tortoise/parsed.py"), 8) == "prose only"


def test_the_code_detector_is_fail_closed(tmp_path):
    """`None` (not an empty set) whenever a `.py` file cannot be read or parsed,
    so the caller routes the whole file to `production_code` rather than to the
    prose bucket."""
    missing = tmp_path / "absent.py"
    assert _code_lines_naming(missing, "propagate_shock") is None

    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n", encoding="utf-8")
    assert _code_lines_naming(broken, "propagate_shock") is None

    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe\x00propagate_shock")
    assert _code_lines_naming(binary, "propagate_shock") is None


# ── 1. propagate_shock callers ────────────────────────────────────

def test_no_propagate_shock_callers():
    """Assert: propagate_shock.calls only in deprecated definition + tests + scripts."""
    # All references to propagate_shock (the word)
    all_refs = _grep("propagate_shock", "tortoise/", "graph-scripts/", "validation/", "tests/")
    filtered = [
        l for l in all_refs  # noqa: E741
        if "__pycache__" not in l and "test_bfs_audit.py" not in l and "test_e2e_extraction_ep.py" not in l
    ]

    callers: dict[str, list[str]] = {
        "definition (deprecated)": [],
        "prose only": [],
        "test_files": [],
        "fixup_scripts (historical)": [],
        "production_code": [],
    }

    for line in filtered:
        path_part = line.split(":")[0]
        full_path = ROOT / path_part  # noqa: F841

        # #378: projection.py was split into the projection/ package — the
        # deprecated definition now lives in projection/propagation.py.
        if ("projection.py" in path_part or "projection/" in path_part) \
                and "def propagate_shock" in line:
            callers["definition (deprecated)"].append(line)
        elif "/tests/" in path_part:
            callers["test_files"].append(line)
        elif "graph-scripts/fix_" in path_part:
            callers["fixup_scripts (historical)"].append(line)
        elif "/graph-scripts/" in path_part:
            callers["test_files"].append(line)  # test_6707_shock.py
        elif "/tortoise/" in path_part:
            # Is this line CODE, or only prose about the function?
            bucket = _classify_tortoise_hit(ROOT / path_part, int(line.split(":", 2)[1]))
            callers[bucket].append(line)

    # The only "production" reference is a comment: no actual call
    assert len(callers["production_code"]) == 0, (
        f"PRODUCTION CODE REFERENCES propagate_shock:\n" + "\n".join(callers["production_code"])  # noqa: F541
    )
    assert len(callers["definition (deprecated)"]) == 1, (
        "Expected exactly 1 deprecated definition in projection.py"
    )

    print(f"  Definition: {len(callers['definition (deprecated)'])} (deprecated)")
    print(f"  Prose only: {len(callers['prose only'])}")
    print(f"  Test files: {len(callers['test_files'])}")
    print(f"  Fixup scripts: {len(callers['fixup_scripts (historical)'])}")
    print(f"  PRODUCTION: {len(callers['production_code'])}  ← key metric")
    print("  ✅ No production code calls propagate_shock")


# ── 2. n.confidence readers ───────────────────────────────────────

def test_no_confidence_readers():
    """Assert: only EP writes n.confidence; SDK reads it (field read, not BFS-dependent)."""
    all_refs = _grep("n\\.confidence", "tortoise/", "graph-scripts/", "validation/", "tests/")
    filtered = [l for l in all_refs if "__pycache__" not in l and "test_bfs_audit.py" not in l and "test_e2e_extraction_ep.py" not in l]  # noqa: E741

    writers: list[str] = []       # SET n.confidence = ...
    readers: list[str] = []       # RETURN n.confidence (read-only)
    bfs_internal: list[str] = []  # Inside projection/propagation.py propagate_shock path

    for line in filtered:
        path_part = line.split(":")[0]
        if "ep.py" in path_part and "SET" in line:
            writers.append(line)
        # Same stale-path fix as #645, applied to the second filter in this file:
        # the BFS confidence write lives in projection/propagation.py.
        elif "propagation.py" in path_part or "projection/" in path_part:
            bfs_internal.append(line)
        elif "sdk.py" in path_part and "RETURN" in line:
            readers.append(line)
        elif "validation/" in path_part:
            readers.append(line)  # validation/test_docker_ep.py — EP test, not BFS
        elif "/graph-scripts/" in path_part:  # noqa: SIM114
            readers.append(line)
        elif "/tests/" in path_part:
            readers.append(line)

    # sdk.py reads n.confidence as a data field — it reads whatever confidence
    # value is stored, regardless of whether EP or BFS set it. Not a BFS dependency.
    sdk_reads = [r for r in readers if "sdk.py" in r]
    print(f"  EP writers (ep.py):   {len(writers)}")
    print(f"  BFS internal (propagation.py/projection/): {len(bfs_internal)} (includes deprecated propagate_shock)")
    print(f"  SDK reads (sdk.py):   {len(sdk_reads)} — field retrieval, not BFS-dependent")
    print(f"  Tests/scripts:        {len(readers) - len(sdk_reads)}")

    # The SDK read is the only production consumer of n.confidence,
    # and it reads the field generically — EP writes it too.
    # No production code aside from sdk.py reads n.confidence.
    assert len(sdk_reads) <= 1, "SDK reads n.confidence at most once"
    assert len(writers) >= 1, "EP should write n.confidence"
    print("  ✅ No production code depends on BFS-set n.confidence specifically")


# ── 3. Stale doc references ────────────────────────────────────────

def test_stale_docs_updated():
    """Check docs/plans/ for stale propagate_shock references."""
    all_refs = _grep("propagate_shock", "../../docs/plans/")

    stale_files: dict[str, list[str]] = {}
    for line in all_refs:
        fname = line.split(":")[0]
        stale_files.setdefault(fname, []).append(line)

    print(f"  Files in docs/plans/ referencing propagate_shock: {len(stale_files)}")
    for fname, refs in stale_files.items():
        print(f"    {fname}: {len(refs)} references")

    # These are plan documents — they're historical records. The plan that
    # designed the epistemic extractor (6855) references propagate_shock as a
    # design decision, but the actual implementation in ingest.py uses EP
    # instead. The plan docs are not "stale" in the sense of being wrong —
    # they document design intent. However:
    if stale_files:
        print(f"  ⚠️  {len(stale_files)} plan doc(s) reference propagate_shock.")
        print("     These are design-phase documents; implementation diverged (uses EP).")
        print("     Not a blocking issue — plan docs are historical, not operational.")
        print("     Recommendation: add a note to 6855 plan doc noting EP replaced BFS.")

    # Not asserting 0 — plan docs are historical. Just reporting.
    assert True  # informational only


if __name__ == "__main__":
    # Standalone run
    test_no_propagate_shock_callers()
    test_no_confidence_readers()
    test_stale_docs_updated()
