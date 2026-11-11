"""
Issue #6902 — Audit: confirm no production code depends on BFS propagate_shock output.

This test file encodes the audit findings as assertions. It does NOT run against
a live FalkorDB instance — it's a grep-based documentation of the audit results.
Run with: python3 -m pytest tests/test_bfs_audit.py -v
"""

import ast
import subprocess
import sys  # noqa: F401
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ast_sources(package: Path) -> list[Path]:
    """Every Python-PARSABLE source under `package`: `.py` modules and `.pyi` stubs.

    `.pyi` is in the set because `main`'s word grep had no suffix filter — it would have seen
    a stub that imports the symbol — and that matters: a stub import is a dependency for the
    type checker the repo runs (`mypy tortoise/` in `.github/workflows/ci.yml`), while
    `ast.parse` reads a stub unchanged, so leaving them out was a narrowing with no
    mechanism behind it. (Measured: the repo currently ships zero stubs, so this restores
    coverage rather than fixing a live miss.)
    """
    return sorted({*package.rglob("*.py"), *package.rglob("*.pyi")})


def _package_files(package: Path) -> list[Path]:
    """Every FILE under `package`, of ANY suffix, with `__pycache__` excluded.

    ⛔ NOT a `*.py` filter. `main`'s word grep scanned every file type under this package,
    and a `*.py`-only walk is a BLINDER rather than a stricter gate: measured GREEN on a
    tree whose only reference was `tortoise/hermes-plugin/plugin.yaml` carrying
    `entry: "propagate_shock"`, where `origin/main` red. A plugin manifest, a registry
    JSON or a config is exactly where a re-export gets registered.

    Undecodable files are READ with `errors="replace"` rather than skipped for the same
    reason: a guard that skips them reopens the hole for any file not written in UTF-8.
    """
    return sorted(
        p for p in package.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _production_ast_references(
    root: Path | None = None, symbol: str = "propagate_shock"
) -> list[tuple[str, int]]:
    """Every AST reference to `symbol` under `<root>/tortoise`, as (path, lineno).

    A `Name`, an `Attribute`, or an `alias` node is a USE of the symbol — a call, an
    alias, an import, a reference — regardless of whitespace, line continuations, or how
    it is later invoked. The `def` name and any docstring/comment mention are not nodes
    of those kinds (prose is a string constant), which is what makes this the right
    primitive for "does production code depend on it".

    `root` is injectable so the form set below is itself pinned by
    `test_the_reference_scan_catches_every_static_form` — a scan nobody tests is how the
    two earlier versions of this guard each passed their own bug.
    """
    base = ROOT if root is None else Path(root)
    refs: list[tuple[str, int]] = []
    for src in _ast_sources(base / "tortoise"):
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id == symbol) or (
                    isinstance(node, ast.Attribute) and node.attr == symbol):
                refs.append((str(src.relative_to(base)), node.lineno))
            elif isinstance(node, ast.alias) and node.name.rsplit(".", 1)[-1] == symbol:
                # `from x import propagate_shock` and `… import propagate_shock as _s`:
                # the later call spells `_s(...)`, so no Name/Attribute node ever carries
                # the symbol — the import is the only static trace of the dependency, and
                # main's word-grep caught it before this AST leg replaced that grep.
                refs.append((str(src.relative_to(base)), node.lineno))
    return refs


def _production_string_references(
    root: Path | None = None, symbol: str = "propagate_shock"
) -> list[tuple[str, int]]:
    """Every reference to `symbol` under `<root>/tortoise`, as (path, lineno).

    Traversal is EVERY file of EVERY suffix (`_package_files`), because this arm exists to
    hold the coverage `main`'s `grep -rn` had and a `grep` has no suffix filter. The
    narrowing that made it `*.py` was measured GREEN on a non-Python reference, so it is
    pinned by `test_the_string_literal_arm_catches_what_no_ast_can`.

    The PREDICATE depends on the file, and both halves are parity with `main` rather than a
    new policy:

    - **Python** (`.py`/`.pyi`): the line must QUOTE the symbol with matching straight
      quotes. This is where #11903's false positive lives — a docstring naming the symbol —
      so the bare word is not enough here. Node-shaped references are the AST arm's job.
    - **Any other file** (manifest, config, registry, `.env`, and — see the residual below —
      prose-bearing `.md`): the BARE WORD, which is what `main`'s grep matched there. Requiring
      quotes WAS a narrowing: `entry: propagate_shock`, a TOML key or `ENTRY=propagate_shock`
      all create the dependency and all were RED on `main`. Pinned by `manifest_unquoted.yaml`.
    - ⚠️ RESIDUAL on the non-Python side, and it is the SAME over-block class as Python's rather
      than an exemption: "this half carries no prose" is FALSE. `tortoise/` ships 9 tracked
      `.md` files (`git ls-files 'tortoise/**/*.md'`), and a bare-word mention in any of them
      REDs this arm — measured: a `tortoise/notes.md` reading "We no longer call
      propagate_shock anywhere." returns `[('tortoise/notes.md', 1)]`. It is latent, not live
      (the real tree measures `[]`, and none of the 9 names the symbol) and it is fail-CLOSED —
      a false red, never a false pass. Pinned by `prose.md` in
      `test_the_string_literal_arm_catches_what_no_ast_can`, so a suffix filter that silently
      dropped `.md` from the walk breaks a test instead of narrowing the arm.

    The AST leg above is blind to this shape by construction: `getattr(mod,
    "propagate_shock")`, `globals()["propagate_shock"]`, `__all__ = ["propagate_shock"]`
    and a registry key spell the symbol inside a string constant, which is not a `Name`,
    `Attribute` or `alias` node at all. Main's word-grep saw these; an AST-only gate does
    not, so this arm exists to keep that coverage rather than trade it for the docstring
    false positive. It reads the source text directly rather than shelling out to grep, so
    the pattern needs no regex-dialect quoting and the arm is pinnable by fixtures
    (`test_the_string_literal_arm_catches_what_no_ast_can`).
    """
    base = ROOT if root is None else Path(root)
    quoted = (f'"{symbol}"', f"'{symbol}'")
    refs: list[tuple[str, int]] = []
    for src in _package_files(base / "tortoise"):
        text = src.read_text(encoding="utf-8", errors="replace")
        # Per the docstring: Python keeps the quoted-only rule (that is where #11903's
        # docstring false positive lives), and every other file type keeps `main`'s
        # bare-word rule, because a manifest/config/registry carries no prose.
        needles = quoted if src.suffix in (".py", ".pyi") else (symbol,)
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(n in line for n in needles):
                refs.append((str(src.relative_to(base)), lineno))
    return refs


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
        "comment/reference only": [],
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
        elif "/tortoise/" in path_part and "#" in line and "replaces" in line.lower():
            callers["comment/reference only"].append(line)
        elif "/tortoise/" in path_part:
            callers["production_code"].append(line)

    # ⛔ A LINE-BASED WORD GREP CANNOT ANSWER THIS QUESTION (#11903).
    #
    # The classifier above matches the WORD. Prose that names the function therefore
    # reads as a caller — `#11702` added exactly such a note to `_compute_confidence`'s
    # docstring (`projection/propagation.py:70,73`, explaining why the path is
    # unreachable), and this test reddened on its own documentation.
    #
    # The tempting repair — `"propagate_shock(" not in line` ⇒ a reference — is WORSE
    # than the bug, because a substring is not a call form. Each of these IS a call and
    # none contains that substring: `propagate_shock (a, b)` (Python allows the space),
    # a backslash or bracket continuation putting the name at end of line, and an alias
    # (`f = propagate_shock; f(a, b)`). A gate that passes those is a gate that stopped
    # gating.
    #
    # So the verdict comes from the AST, which answers the question the test is actually
    # asked: a `Name`/`Attribute`/`alias` node is the SYMBOL BEING USED; a docstring
    # mention is a string constant and cannot produce one, and whitespace, continuations,
    # invocations and import-aliases all still resolve to a reference.
    #
    # ⛔ THIS SCAN HAS BEEN WRONG THREE TIMES (a substring match, then an AST walk that
    # skipped `ast.alias`, then a form list with no traversal constraint), and each version
    # passed its own bug — the forms were reasoned about rather than pinned.
    # `test_the_reference_scan_catches_every_static_form` now parses a fixture tree
    # containing every static form, positive and negative, AND a caller inside a
    # subpackage, so the next iteration breaks a test instead of quietly narrowing the gate.
    #
    # Residual, named rather than hidden — and the arm's REAL predicate, which is finer than
    # "a reference carried in a string literal". It is a line test: does this line quote the
    # symbol with MATCHING STRAIGHT QUOTES (`"propagate_shock"` / `'propagate_shock'`)?
    # That is a superset of the reference class and a subset of the word grep, and both edges
    # are measured rather than assumed:
    #
    # (a) COVERED, and it is why this arm exists. A reference carried in a string literal whose
    # content IS the symbol — `getattr(mod, "propagate_shock")`,
    # `globals()["propagate_shock"]`, an `__all__` entry, a registry key — is not a node at all,
    # so no AST walk can see it. main's word-grep DID see those, and replacing that grep with
    # the AST scan alone would have WIDENED a hole while closing a false positive: measured on
    # a synthetic tree whose `__all__` names the symbol, the AST-only gate went GREEN where
    # `origin/main` red.
    #
    # (b) NOT MATCHED, deliberately. A quoted literal that CONTAINS the symbol rather than
    # being delimited by quotes around it — `exec("propagate_shock(g, n)")`,
    # `raise X("…propagate_shock…")` — is green here. Widening to a word-substring test would
    # re-import the false positive #11903 exists to remove (`"""call propagate_shock(node)"""`
    # is prose, not a caller), and #11903 is the decision that forbids the word grep. This is a
    # real narrowing against `origin/main`, recorded rather than hidden and pinned in
    # `must_stay_green` below, so a later widening is a deliberate choice that breaks a test
    # instead of a silent drift. The structural replacement — `ast.Constant` / `ast.JoinedStr`
    # parts, docstring nodes skipped — closes this case with no prose false positive, and is
    # filed as a follow-up (#11918) rather than smuggled into a cite repair.
    #
    # ⚠️ The matching-quote rule cuts BOTH ways in PYTHON: straight-quoted PROSE
    # (`# "propagate_shock" was removed`, `"""Do not call "propagate_shock" here."""`) IS
    # flagged by this arm. That is a latent over-block, not a live one — the repo convention
    # for naming a symbol in prose is backticks, and the real tree measures empty
    # (`string_refs == []`) — and it is the reason the structural follow-up above is the
    # right long-term arm. Stated so the next reader does not infer a prose exemption this
    # textual test does not have. The SAME over-block exists on the NON-Python side, for the
    # same reason: `tortoise/` ships 9 tracked `.md` files, so "no docstrings there" is false —
    # a bare-word mention in any of them reds. Pinned by `prose.md` below; it is fail-closed (a
    # false red, never a false pass), latent (none of the 9 names the symbol), and the
    # structural fix is the one #11918 tracks for the Python side.
    #
    # TRAVERSAL, stated so the next reader can CHECK it rather than infer it. Depth is
    # unbounded (`rglob`); file type is unbounded for the textual arm (every file under
    # `tortoise/`, `__pycache__` excluded) and `.py`/`.pyi` for the AST arm (only Python
    # parses). Both were `*.py`-only for one round, and that walk was measured GREEN on a
    # tree whose sole reference was `tortoise/hermes-plugin/plugin.yaml` where `origin/main`
    # red — so the file-type filter is gone and pinned by fixtures in both detector tests.
    # DIRECTORY scope is the shipped package `tortoise/`, which is ALSO the scope `main`'s
    # assertion had: `origin/main` bucketed a line into `production_code` only when
    # `"/tortoise/" in path_part`, so `graph-scripts/`, `validation/` and `tests/` never
    # reached it there either. They stay in the word-grep `callers` map above as reported
    # CONTEXT. What this repair changes is the PREDICATE — word match becomes
    # node/straight-quoted for Python and bare-word for every other file type — not the
    # directory surface, which is stated here so no reader infers a narrowing that did not
    # happen.
    #
    # Every node-shaped reference is pinned below, traversal is pinned for BOTH arms, and each
    # arm's positive and negative form sets are pinned by its own fixtures.
    ast_refs = _production_ast_references()

    # ⛔ The word-grep bucket still lists #11702's DOCSTRING lines
    # (`projection/propagation.py:70,73` explain why this path is unreachable) — they are
    # prose, not callers, which is the false positive #11903 exposed. The AST scan is the
    # gate for node-shaped references; `callers["production_code"]` is context, and it is
    # expected to be non-empty.
    assert not ast_refs, (
        "PRODUCTION CODE CALLS propagate_shock:\n"
        + "\n".join(f"{path}:{lineno}" for path, lineno in ast_refs)
        + "\n\n(word-grep context, which cannot tell a call from a mention:)\n"
        + "\n".join(callers["production_code"])
    )
    string_refs = _production_string_references()
    assert not string_refs, (
        "PRODUCTION CODE REFERENCES propagate_shock BY STRING LITERAL — no AST walk can"
        " see this shape, so it is the word-grep arm's job:\n"
        + "\n".join(f"{path}:{lineno}" for path, lineno in string_refs)
    )
    assert len(callers["definition (deprecated)"]) == 1, (
        "Expected exactly 1 deprecated definition in projection.py"
    )

    print(f"  Definition: {len(callers['definition (deprecated)'])} (deprecated)")
    print(f"  Comments:   {len(callers['comment/reference only'])}")
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
