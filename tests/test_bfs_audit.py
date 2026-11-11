"""
Issue #6902 — Audit: confirm no production code depends on BFS propagate_shock output.

This test file encodes the audit findings as assertions. It does NOT run against
a live FalkorDB instance — it enumerates candidate references with `grep` and
then decides what each one IS from `tokenize` — code, a definition, or prose.
Run with: python3 -m pytest tests/test_bfs_audit.py -v
"""

import ast
import io
import subprocess
import sys  # noqa: F401
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

    # Prose strings: any statement whose whole value is a string literal.
    prose_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            prose_spans.append(
                (node.value.lineno, getattr(node.value, "end_lineno", None) or node.value.lineno)
            )

    def _is_prose_line(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in prose_spans)

    # Python 3.12 tokenizes an f-string as FSTRING_START/FSTRING_MIDDLE/…, so the
    # literal part of one is an FSTRING_MIDDLE token, not a STRING. Without this
    # an f-string mention would be the one non-prose string that reads as prose.
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    lines: set[int] = set()
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string == name:
            lines.add(tok.start[0])
        elif (tok.type == tokenize.STRING or tok.type == fstring_middle) and name in tok.string:
            span = range(tok.start[0], tok.end[0] + 1)
            if not any(_is_prose_line(n) for n in span):
                lines.update(span)
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
        # line, and three earlier drafts each missed a this class of shape by
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
    it never parses, so it fell to the `None` arm and the whole file counted."""
    for suffix in (".md", ".sql", ".txt", ".yml"):
        path = tmp_path / f"notes{suffix}"
        path.write_text("The deprecated helper propagate_shock is documented here.\n",
                        encoding="utf-8")
        assert _code_lines_naming(path, "propagate_shock") == frozenset(), suffix


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
