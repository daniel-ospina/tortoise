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
    2. every string literal CONTAINING the name, unless that string is prose —
       which covers the name resolved dynamically from a string
       (``getattr(m, "propagate_shock")``, ``globals()["propagate_shock"]``,
       ``eval("propagate_shock(1)")``). This over-includes a non-prose string
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

    ``None`` means a Python source file (``.py`` or ``.pyi`` — a stub declares
    identifiers too) could not be read or parsed, and the caller reads it as
    "every line is code": a degraded read can only make the audit STRICTER.
    Any OTHER suffix under ``tortoise/`` answers the empty set rather than failing
    to parse — prose in a ``.md`` or ``.sql`` file is prose, and reading it as a
    caller is the false positive this change removes. (It is not that such a file
    can hold no identifiers; it is that we do not read it as Python.)
    """
    if path.suffix not in (".py", ".pyi"):
        return frozenset()
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, tokenize.TokenError,
            IndentationError):
        return None

    # ⛔ ONE COORDINATE SPACE. `ast` reports `col_offset`/`end_col_offset` as
    # UTF-8 BYTE offsets (CPython ASDL: "col_offset is the byte offset in the utf8
    # string the parser uses"), while `tokenize.generate_tokens` over a `str`
    # reports CHARACTER columns. Comparing the two directly makes the prose box's
    # right edge drift right by one column per extra UTF-8 byte, so a docstring
    # holding enough non-ASCII swallows a literal that really lies after its
    # closing quotes — a live `getattr(m, "...")` on the terminator line read as
    # prose, i.e. the same fail-open the box exists to close. The bytes are
    # converted to characters here, at the only place that reads AST columns.
    # `by.splitlines()` is avoided deliberately: it also splits on `\v`, `\f`,
    # `\x85`, `\u2028`/`\u2029`, which the parser does NOT treat as line ends, so
    # indices could shift. Split on exactly the parser's own line endings.
    src_lines = re.split(r"\r\n|\r|\n", src)

    def _char_col(lineno: int, byte_col: int) -> int | None:
        """The CHARACTER column of a UTF-8 ``byte_col`` on ``lineno``.

        AST offsets always land on a character boundary; ``errors="ignore"`` only
        keeps the conversion total (it can never drop a whole character).

        ``None`` means the line is outside the parser's own line model, so the
        column cannot be placed at all. A box that cannot be placed is DROPPED by
        the caller, never built in the byte unit: dropping an exemption makes the
        prose read as CODE (the loud direction), while returning ``byte_col`` here
        would restore the exact fail-open this conversion removes.
        """
        if not 1 <= lineno <= len(src_lines):
            return None
        return len(src_lines[lineno - 1].encode("utf-8")[:byte_col].decode("utf-8", "ignore"))

    # Prose strings: any statement whose whole value is a string literal. Kept as
    # an exact (line, column) BOX, not a line range.
    # ⛔ LINE-granularity is a fail-open: `"""Doc."""; x = getattr(m,
    # "propagate_shock")` puts a live string-resolved dependency on a line the
    # docstring owns, and a line test discards it — the audit then reports no
    # dependency while one exists. Containment is therefore column-aware, so only
    # a literal lying INSIDE the prose statement's box is prose; the string on the
    # terminator line is code.
    prose_boxes: list[tuple[int, int, int, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            value = node.value
            right_line = getattr(value, "end_lineno", None) or value.lineno
            right_col = getattr(value, "end_col_offset", None)
            left = _char_col(value.lineno, value.col_offset)
            right = _char_col(right_line, right_col) if right_col is not None else 0
            if left is None or right is None:
                continue
            prose_boxes.append((value.lineno, left, right_line, right))

    def _inside_prose(start: tuple[int, int], end: tuple[int, int]) -> bool:
        return any(
            (top, left) <= start and end <= (bottom, right)
            for top, left, bottom, right in prose_boxes
        )

    # Python 3.12 tokenizes an f-string as FSTRING_START/FSTRING_MIDDLE/…, so the
    # literal part of one is an FSTRING_MIDDLE token, not a STRING. Without this
    # an f-string mention would be the one non-prose string that reads as prose.
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    lines: set[int] = set()
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string == name:
            lines.add(tok.start[0])
        elif (
            (tok.type == tokenize.STRING or tok.type == fstring_middle)
            and name in tok.string
            and not _inside_prose(tok.start, tok.end)
        ):
            lines.update(range(tok.start[0], tok.end[0] + 1))
    return frozenset(lines)


def _classify_tortoise_hit(path: Path, lineno: int) -> str:
    """Which bucket a `grep` hit inside ``tortoise/`` belongs in.

    Split out of :func:`test_no_propagate_shock_callers` so the CALLER's own arm
    is reachable by a test. The audit's consequence of a DEGRADED read lives
    here — ``_code_lines_naming`` returning ``None`` means "this file could not
    be read'', and a guard that cannot read a file must not call it clean, so
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
        # the pre-fix mutant. A row that cannot fail is not a pin, so they were
        # dropped and this one stays for the plain regression it names. The unit
        # itself is pinned by the `_non_ascii` rows below.
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
        # ⛔ The SAME two shapes with a NON-ASCII docstring. `ast` columns are
        # UTF-8 bytes and `tokenize` columns are characters, so an ASCII-only row
        # cannot see an off-by-N-bytes comparison: the prose box's right edge
        # drifts right by one column per extra byte and swallows the literal that
        # follows the closing quotes. 18 em dashes = 54 UTF-8 bytes but 18
        # characters, i.e. a 36-column drift on one line — the pre-fix box ends at
        # column 64 against a true end of 28, so the literal at column 45 is
        # inside it. First fail-open measured at 17 em dashes (34 columns of
        # drift); every row here is above that wedge.
        "docstring_then_string_same_line_non_ascii": (
            'def f():\n    """' + "\u2014" * 18 + '"""; x = getattr(m, "propagate_shock")\n', {2}),
        "docstring_then_plain_string_same_line_non_ascii": (
            'def f():\n    """' + "\u2014" * 18 + '"""; y = "propagate_shock"\n', {2}),
        # ⛔ …and the same fail-open on a MULTI-line docstring, whose box is the one
        # a single-line-only conversion never exercises: a regression that
        # converted the columns only when the span's start and end share a line
        # passes EVERY single-line row above and still reads this live dependency
        # as prose (measured: `[]` against the correct `{3}`).
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


def test_the_code_detector_ignores_prose_outside_python_files(tmp_path):
    """A `.md`/`.sql` file under `tortoise/` cannot carry a Python identifier, so
    its mention is prose. Reading it as a caller was a declared-shape violation:
    it never parses, so it fell to the `None` arm and the whole file counted.

    `.pyi` is the OPPOSITE case — a stub is Python source — and is pinned by
    :func:`test_a_stub_file_is_read_as_python_not_as_prose`. Do not add it here.
    """
    for suffix in (".md", ".sql", ".txt", ".yml"):
        path = tmp_path / f"notes{suffix}"
        path.write_text("The deprecated helper propagate_shock is documented here.\n",
                        encoding="utf-8")
        assert _code_lines_naming(path, "propagate_shock") == frozenset(), suffix


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
    print(f"  word-grep 'production' context: {len(callers['production_code'])} "
          f"(docstring prose — NOT the gate; the AST scan and the string arm are, and they"
          f" found {len(ast_refs)} node-shaped and {len(string_refs)} quoted)")
    print("  ✅ No production code calls propagate_shock (AST scan + string-literal arm)")


def test_the_reference_scan_catches_every_static_form(tmp_path):
    """The scan above is itself pinned — both of its earlier versions passed their own bug.

    `propagate_shock` must be reported for EVERY static form a dependency can take, and
    must NOT be reported for prose or for lookalike names. This is a test of the detector,
    not of the tree, so it builds its own fixtures: the previous two failures were reasoned
    about in prose and never exercised.
    """
    (tmp_path / "tortoise").mkdir()

    must_be_caught = {
        "call.py": "propagate_shock(graph, n)\n",
        "space.py": "propagate_shock (graph, n)\n",                       # space before (
        "continuation.py": "propagate_shock \\\n    (graph, n)\n",         # backslash
        "name_at_eol.py": "x = (\n    propagate_shock\n    (graph, n)\n)\n",  # name at EOL
        "assign_alias.py": "f = propagate_shock\nf(graph, n)\n",          # bound, then called
        "attribute.py": "self.propagate_shock(graph, n)\n",
        "import_plain.py": "from tortoise.projection.propagation import propagate_shock\n",
        "import_aliased.py": "from tortoise.projection.propagation import propagate_shock as _s\n_s(x)\n",
        "import_dotted.py": "import tortoise.projection.propagate_shock\n",
        # A stub is Python-parsable and `main`'s word grep saw it; excluding `.pyi` from the
        # walk was a narrowing with no mechanism behind it, so it is pinned here.
        "import_in_stub.pyi": "from tortoise.projection.propagation import propagate_shock\n",
    }
    must_stay_green = {
        "definition.py": "def propagate_shock(self, graph, n):\n    return 1\n",
        "docstring.py": 'def f():\n    """`propagate_shock` is never invoked."""\n    return 1\n',
        "comment.py": "# propagate_shock is never invoked\nx = 1\n",
        "unrelated.py": "propagate_shock_v2(graph, n)\n",
    }
    for name, body in {**must_be_caught, **must_stay_green}.items():
        (tmp_path / "tortoise" / name).write_text(body, encoding="utf-8")

    # ⛔ TRAVERSAL IS PART OF THE CONTRACT. The scan's question is "does `tortoise/**`
    # depend on this", and the one place it would be called from is a subpackage — the
    # deprecated definition lives in `projection/`. A flat-only fixture tree passes
    # unchanged if someone swaps `rglob` for `glob`, and the gate would then print a clean
    # bill of health while blind to every subpackage. So the tree is nested on purpose.
    nested = tmp_path / "tortoise" / "projection"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "nested_call.py").write_text("propagate_shock(graph, n)\n", encoding="utf-8")
    refs = _production_ast_references(root=tmp_path)
    found = {Path(p).name for p, _ in refs}

    assert any(p.endswith("projection/nested_call.py") for p, _ in refs), (
        "the reference scan does not descend into subpackages, so a caller in"
        " `tortoise/projection/` — where the deprecated definition lives — would read as no"
        f" dependency at all. Paths seen: {sorted({p for p, _ in refs})}"
    )

    missed = sorted(set(must_be_caught) - found)
    assert not missed, (
        "the reference scan MISSES a static form, so a real caller would read as no"
        f" dependency at all: {missed}. Every one of these names the symbol:"
        + "".join(f"\n  {n}: {must_be_caught[n]!r}" for n in missed)
    )
    false_positives = sorted(found & set(must_stay_green))
    assert not false_positives, (
        "the reference scan flags prose or a lookalike as a dependency — this is the"
        f" #11903 failure (a docstring mention reddened the gate): {false_positives}"
    )


def test_the_string_literal_arm_catches_what_no_ast_can(tmp_path):
    """The string-literal arm is pinned, for the same reason the AST leg is.

    `__all__ = ["propagate_shock"]` is the shape a re-export takes, and it is invisible to
    every AST walk. Measured before this arm existed: the AST-only gate went GREEN on a
    tree containing it while `origin/main`'s word-grep red. A gate that traded a false
    positive for a false NEGATIVE is not an improvement, so the coverage is restored as an
    assertion — and this test is what stops the next rewrite from dropping it silently.

    ⛔ TRAVERSAL IS PART OF THIS ARM'S CONTRACT TOO, and for a sharper reason than the AST
    leg's: `tortoise/projection/` is exactly where a re-export of the deprecated function
    would be registered, so a `rglob`→`glob` swap here would blind the arm precisely where
    the event it guards against is most likely. Measured: without the nested fixture such a
    swap passed this test unchanged.
    """
    (tmp_path / "tortoise").mkdir()

    must_be_caught = {
        "all_export.py": '__all__ = ["propagate_shock"]\n',
        "getattr_string.py": 'getattr(mod, "propagate_shock")(g, n)\n',
        "registry_key.py": '_HOOKS = {"shock": "propagate_shock"}\n',
        "single_quoted.py": "HOOK = 'propagate_shock'\n",
        # Non-Python files keep `main`'s BARE-WORD rule: a manifest/config/registry carries no
        # docstring, and requiring quotes would miss `entry: propagate_shock` — which
        # `origin/main` red on. (A prose-bearing `.md` IS walked and DOES red — see `prose.md`
        # below; that over-block is fail-closed and declared, not exempted.)
        "manifest_unquoted.yaml": "entry: propagate_shock\n",
    }
    must_stay_green = {
        "backticked_prose.py": 'def f():\n    """`propagate_shock` is never invoked."""\n    return 1\n',
        "lookalike.py": 'NAME = "propagate_shock_v2"\n',
        # The other side of the split: PYTHON keeps the quoted-only rule, so a docstring
        # naming the symbol in bare form stays green — that mention is #11903's false
        # positive, and widening Python to a bare word is the wrong fix for it.
        "bare_prose.py": 'def f():\n    """propagate_shock is never invoked."""\n    return 1\n',
        # Residual (b), pinned deliberately: a quoted literal that CONTAINS the symbol is not
        # matched, because a word-substring match would re-import the #11903 false positive.
        # Pinning it green makes any future widening a decision that breaks this test.
        "embedded_literal.py": 'exec("propagate_shock(g, n)")\n',
    }
    for name, body in {**must_be_caught, **must_stay_green}.items():
        (tmp_path / "tortoise" / name).write_text(body, encoding="utf-8")

    # The nested fixture, and the assert that reads it: a flat-only tree passes unchanged
    # under `glob`, which is how the AST leg's own traversal pin was found necessary.
    nested = tmp_path / "tortoise" / "projection"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "all_export_nested.py").write_text(
        '__all__ = ["propagate_shock"]\n', encoding="utf-8"
    )

    # ⛔ TRAVERSAL IS ALSO FILE TYPE. A reference does not have to live in a Python file:
    # a plugin manifest or a registry JSON is where a re-export actually gets registered,
    # and `main`'s word grep had no suffix filter. Measured at the head that walked only
    # `*.py`: a tree whose sole reference was `tortoise/hermes-plugin/plugin.yaml` returned
    # `[]` while `origin/main` red — a false PASS, this file's core failure class.
    plugin_dir = tmp_path / "tortoise" / "hermes-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text('entry: "propagate_shock"\n', encoding="utf-8")

    # ⛔ The non-Python half is NOT a prose exemption — it is the same over-block, and this
    # fixture pins that the bare-word rule really reaches `.md`. `tortoise/` ships 9 tracked
    # `.md` files, so a prose mention there reds this arm; that is fail-CLOSED (a false red,
    # never a false pass), which is why it is declared and pinned rather than filtered away.
    (tmp_path / "tortoise" / "prose.md").write_text(
        "We no longer call propagate_shock anywhere.\n", encoding="utf-8"
    )

    refs = _production_string_references(root=tmp_path)
    found = {Path(p).name for p, _ in refs}

    assert any(p.endswith("projection/all_export_nested.py") for p, _ in refs), (
        "the string-literal arm does not descend into subpackages, so a re-export registered"
        " in `tortoise/projection/` — where the deprecated definition lives — would read as no"
        f" dependency at all. Paths seen: {sorted({p for p, _ in refs})}"
    )
    assert any(p.endswith("prose.md") for p, _ in refs), (
        "the bare-word rule stopped reaching non-Python PROSE, so a `.md` mention inside"
        " `tortoise/` would read as no dependency — the parity `main`'s suffix-free grep had."
        f" Paths seen: {sorted({p for p, _ in refs})}"
    )
    assert any(p.endswith("hermes-plugin/plugin.yaml") for p, _ in refs), (
        "the string-literal arm does not read non-Python files, so a reference carried in a"
        " manifest/config/registry reads as no dependency at all — the coverage `main`'s word"
        f" grep provided. Paths seen: {sorted({p for p, _ in refs})}"
    )

    missed = sorted(set(must_be_caught) - found)
    assert not missed, (
        "the string-literal arm MISSES a quoted reference, so the AST leg is blind to it"
        f" and nothing else looks: {missed}"
    )
    false_positives = sorted(found & set(must_stay_green))
    assert not false_positives, (
        "the string-literal arm flags prose or a lookalike — that is the same false"
        f" positive the AST leg was introduced to remove: {false_positives}"
    )


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
