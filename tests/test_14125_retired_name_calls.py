"""#14125 — the two classes that let `#13899`'s retirement hide for a whole wave.

`#13899` retired 146 public `TortoiseSDK` names (69 renamed — the name fails and
names its replacement — and 77 removed, which name no replacement). The retirement
itself was correct and the SDK target is met (40 declared names resolve); what went
wrong is that **nothing asserted the retired names were gone from the trees that
call the SDK**. Two failure shapes were measured, and each is a *false PASS* rather
than a red:

  * **class 1 — a call to a retired door.** A test or a seed script calls a name
    that `__getattr__` refuses. In an ordinary test that is a loud
    `AttributeError`; inside a `python -c` seed, or behind a capability probe, it
    is not — the seed's subprocess simply exits 1 and *the test that depended on
    it* reports "storage unavailable" or a `CalledProcessError` with no retired
    name anywhere in the message (measured: `tests/test_openclaw_seam.py`
    `1 failed`, `tests/test_pi_volunteer_seam.py` `17 errors`, and
    `tests/test_7405_update_point_writes.py` `1 failed` of 98).

  * **class 2 — the probe that swallowed it.** Every module-level availability
    probe in this suite is `try: … capability call … except Exception: AVAILABLE
    = False`. That `except Exception` is what turned a rename into "no storage":
    the family SKIPS locally and, in CI where `TORTOISE_TEST_EXPECT_URI=1` refuses
    a skip, reds a shard with a message about live storage that names no door.

WHY A GATE AND NOT JUST THE REPAIR. The repair fixed 26 call sites and 23 probes.
That is a snapshot; the defect is a *property of the trees* ("no retired name is
called through the SDK"; "a probe does not read a programming error as an absent
server"), and a property needs a witness or the next rename reproduces it. Both
witnesses below are hermetic — AST only, no graph, no lane — and both answer from
the repo's own machinery rather than a second implementation of it:

  * the receiver typing is `tools/sdk_privatise.py`'s (`receiver_is_sdk` +
    `sdk_typing` + `sdk_standin_classes`) — the same walk `--check` uses to decide
    "is this receiver the SDK", so the gate and the sweep cannot disagree about
    `reg.query` (a FalkorDB `Graph`) or `props.update` (a dict);

  * the retired-name list is `tortoise/sdk_retired.py`'s, read as data.

⚠️ NOT EVERY MATCH IS A DEFECT, AND THE ALLOWANCES ARE THE POINT. Two references
survive on purpose: a test that ASSERTS a name is retired must spell the retired
name. They are listed in `_INTENTIONAL` with their reason and the gate checks each
one is STILL THERE — a stale allowance is itself a red, so the list cannot rot into
a blanket exemption.
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from tortoise.sdk_retired import REMOVED_SDK_METHODS, RETIRED_SDK_METHODS

ROOT = Path(__file__).resolve().parent.parent

RETIRED_NAMES = frozenset(RETIRED_SDK_METHODS) | frozenset(REMOVED_SDK_METHODS)

#: A reference to a retired name that is CORRECT as written, keyed by
#: `(repo-relative path, the expression the sweep renders)`. Each must still be
#: found: `test_the_intentional_allowances_still_exist` reds when one is repaired
#: or deleted, so this cannot silently become a blanket exemption, and a NEW shape
#: cannot join it without an edit that says why.
_INTENTIONAL: dict[tuple[str, str], str] = {
    (
        "tests/test_control_plane.py",
        'sdk [str "org_list"]',
    ): (
        "`assert not hasattr(sdk, \"org_list\")` — the regression guard that the "
        "unscoped tenant list STAYS removed (owner ruling #4282, 2026-10-25). The "
        "retired spelling is the subject of the assertion, not a call."
    ),
    (
        "tests/test_ingest.py",
        'TortoiseSDK [str "index_sessions"]',
    ): (
        "`assert not hasattr(TortoiseSDK, \"index_sessions\")` — the same shape: "
        "the test exists to prove the public name did not come back."
    ),
}

#: The attribute names a `getattr`-shaped reference is written through.
_GETATTR_FORMS = frozenset({"getattr", "setattr", "delattr", "hasattr"})


def _load_privatise():
    """`tools/sdk_privatise.py`, loaded as a module (`tools/` is a namespace here)."""
    spec = importlib.util.spec_from_file_location(
        "sdk_privatise_for_14125", ROOT / "tools" / "sdk_privatise.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves through sys.modules
    spec.loader.exec_module(module)
    return module


privatise = _load_privatise()


def _tracked_python_files() -> list[Path]:
    """Every tracked (and untracked-but-not-ignored) `.py`, exactly as the sweep sees it."""
    return privatise.py_files()


def _references(tree: ast.Module, in_sdk_file: bool) -> list[tuple[int, str]]:
    """Retired-name references in one module, with the expression that carries them.

    Three shapes, because a parenthesis search sees only the first:

      * an `Attribute` node — this also reaches `asyncio.to_thread(sdk.name, …)` and
        `self._fn = sdk.name`, which are references without a call;
      * a `getattr`/`hasattr`/`setattr` literal — `getattr(sdk, "query")`;
      * a **script-shaped string** (it carries a newline and an `import`), recursively
        parsed — this is the `python -c` seed, where the #14114 reds actually lived and
        the one shape a node walk over the *file* cannot see. Prose that merely
        mentions a name (`"sdk.org_create" not in clause`) is one line and no import,
        so it is not swept: a documentation assertion is not a call.
    """
    standins = privatise.sdk_standin_classes(tree, {})
    typing_ = privatise.sdk_typing(tree)
    ranges = privatise._class_ranges(tree)
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in RETIRED_NAMES:
            in_class = in_sdk_file and privatise._in_sdk_class(node.lineno, ranges)
            if privatise.receiver_is_sdk(
                node.value, typing_, standins, in_class, node.lineno
            ):
                found.append((node.lineno, f"{ast.unparse(node.value)}.{node.attr}"))
            continue

        if (
            isinstance(node, ast.Call)
            and privatise._call_name(node) in _GETATTR_FORMS
            and len(node.args) >= 2
        ):
            literal = node.args[1]
            if (
                isinstance(literal, ast.Constant)
                and isinstance(literal.value, str)
                and literal.value in RETIRED_NAMES
                and privatise.receiver_is_sdk(
                    node.args[0], typing_, standins, False, literal.lineno
                )
            ):
                found.append((
                    literal.lineno,
                    f'{ast.unparse(node.args[0])} [str "{literal.value}"]',
                ))
            continue

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "\n" in node.value
            and "import " in node.value
            and any(name in node.value for name in RETIRED_NAMES)
        ):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    inner = ast.parse(textwrap.dedent(node.value))
            except (SyntaxError, ValueError, UnicodeEncodeError):
                continue  # not a script — a heredoc fragment, a shell line, prose
            for lineno, expr in _references(inner, False):
                found.append((lineno, f"{expr}  [inside the script at line {node.lineno}]"))

    return found


@functools.lru_cache(maxsize=1)
def _sweep() -> tuple[tuple[str, int, str], ...]:
    rows: list[tuple[str, int, str]] = []
    for path in _tracked_python_files():
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(name in src for name in RETIRED_NAMES):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover — a broken file reds its own suite
            continue
        rel = str(path.relative_to(ROOT))
        in_sdk_file = path.resolve() == privatise.SDK_SRC.resolve()
        for lineno, expr in _references(tree, in_sdk_file):
            rows.append((rel, lineno, expr))
    return tuple(rows)


# ── class 1: no retired name is called through the SDK ────────────────────


def test_no_sdk_typed_reference_calls_a_retired_name():
    """Every reference to a retired name is either gone, or a declared allowance.

    A hit here is a caller of a door that raises. Fix the CALL to the approved name
    — never un-retire the name to make this gate green (#14125's contradiction
    test: the SDK target is met and the test is what is wrong).
    """
    offending = [
        (rel, lineno, expr)
        for rel, lineno, expr in _sweep()
        if (rel, expr) not in _INTENTIONAL
    ]
    if offending:
        rendered = "\n".join(f"  {rel}:{lineno}  {expr}" for rel, lineno, expr in offending)
        pytest.fail(
            "these reference a RETIRED `TortoiseSDK` name through an SDK-typed "
            "receiver, so they raise `AttributeError` with a migration hint at "
            "runtime (and, inside a `python -c` seed or behind a capability "
            "probe, they do it silently — #14114/#14125):\n"
            f"{rendered}\n\n"
            "Use the approved replacement (`tortoise/sdk_retired.py` names it) or, "
            "where the caller is a test reaching an internal seam, the private "
            "spelling. If the reference is deliberately ABOUT the retirement "
            "(e.g. `assert not hasattr(sdk, ...)`), add it to `_INTENTIONAL` with "
            "its reason — that is a deliberate entry, not a suppression."
        )


def test_the_intentional_allowances_still_exist():
    """Non-vacuity: a stale allowance is a red, so the list cannot rot.

    An allowance that no longer matches anything means the reference was repaired
    and the exception outlived it — which would let a FUTURE, identical shape
    through unremarked.
    """
    seen = {(rel, expr) for rel, _lineno, expr in _sweep()}
    stale = sorted(key for key in _INTENTIONAL if key not in seen)
    assert not stale, (
        "these allowances no longer match any reference — delete them (an "
        f"allowance that outlives its reference is a blanket exemption): {stale}"
    )


def test_the_sweep_is_not_vacuous():
    """The walk must actually see the SDK surface, or 'zero' means nothing.

    A receiver-typing bug that typed nothing would make the gate green over any
    tree. `TortoiseSDK` is recognised by name, and the retired list must still hold
    both shapes — 69 renames and 77 removals at #14125.
    """
    tree = ast.parse("sdk = make_sdk()\nsdk.query('MATCH (n) RETURN n')\n")
    assert privatise.sdk_typing(tree) is not None
    assert _references(tree, False) == [(
        2, "sdk.query"
    )], "the walk no longer flags the plain `sdk.<retired>(…)` shape"
    assert len(RETIRED_SDK_METHODS) >= 69, (
        "the rename map lost entries — a gate over it would then be answering "
        "about a smaller class than the one #13899 retired"
    )
    assert len(REMOVED_SDK_METHODS) >= 77


# ── class 2: a probe cannot read a programming error as "no storage" ──────

# The arm a probe must carry is the set of PROGRAMMING errors its `try` body can
# raise through a seam — the renames #13899 and #14125 are about. `ImportError`
# is in the set because the seam is usually an IMPORT: most probes name their
# module on the first line INSIDE the `try`, so a module that is moved, renamed
# or split raises `ModuleNotFoundError` there — and while that name was missing,
# the arm was present but unreachable for the very seam it wrapped, the block
# fell through to `FALKORDB_AVAILABLE = False`, and the family SKIPPED green
# over a broken tree.
_RAISED = frozenset({"AttributeError", "NameError", "TypeError", "ImportError"})

# The import-seam rule is scoped to the repo's OWN code. An own-code module that
# is moved is a defect and must fail loudly; a third-party module inside a probe
# may be an OPTIONAL extra (this repo ships `embeddings` / `parity` extras and a
# documented keyword-only install), and for those a SKIP is the correct outcome —
# demanding a re-raise there would turn a legitimate skip into a collection error
# and contradict the recorded rule that an absent capability skips.
_OWN_CODE_ROOTS = frozenset({"tortoise", "tests", "tools"})


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"<bare>"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {getattr(n, "id", None) or getattr(n, "attr", None) for n in nodes} - {None}


def _swallows_everything(handlers: list[ast.ExceptHandler]) -> bool:
    """Whether a handler here would catch a ANY exception (bare or `Exception`)."""
    return any(
        names == {"<bare>"} or "Exception" in names
        for names in (_handler_names(h) for h in handlers)
    )


def _covered(handlers: list[ast.ExceptHandler]) -> set[str]:
    """Names covered by the NARROW arms, unioned across handlers.

    The union, not the per-handler set: a split arm —
    `except (AttributeError, NameError, TypeError): raise` followed by
    `except ImportError: raise` — re-raises all four, so it satisfies the same
    property as one four-name tuple and must not be redded for its shape. The
    broad arm is never coverage: it is what swallows the rest.
    """
    out: set[str] = set()
    for handler in handlers:
        names = _handler_names(handler)
        if names == {"<bare>"} or "Exception" in names:
            continue
        out |= names
    return out


def _availability_targets(stmt: ast.stmt) -> list[ast.expr]:
    if isinstance(stmt, ast.Assign):
        return list(stmt.targets)
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target]
    return []


def _names_availability(targets: list[ast.expr]) -> bool:
    """Whether any target binds `FALKORDB_AVAILABLE`, including in a tuple target
    (`FALKORDB_AVAILABLE, _WORKING_URI = _probe([…])`)."""
    for target in targets:
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and node.id == "FALKORDB_AVAILABLE":
                return True
    return False


def _assigns_availability(node: ast.AST) -> bool:
    return any(
        _names_availability(_availability_targets(sub))
        for sub in ast.walk(node)
        if isinstance(sub, (ast.Assign, ast.AnnAssign))
    )


def _resolver_functions(tree: ast.Module) -> set[str]:
    """Module-level helpers the flag is RESOLVED by (`FALKORDB_AVAILABLE = _probe()`).

    Eight modules in this tree resolve availability that way instead of with a
    module-body `try`, so a census that only reads the module body grades nothing
    there — the exact blind spot that let these probes' missing arm survive.
    """
    names: set[str] = set()
    for stmt in tree.body:
        if not _names_availability(_availability_targets(stmt)):
            continue
        value = getattr(stmt, "value", None)
        if value is None:
            continue
        for node in ast.walk(value):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def _probe_roots(tree: ast.Module) -> list[ast.Try]:
    """The `try` blocks that DECIDE availability — two shapes in this tree.

      * a module-body `try` that assigns `FALKORDB_AVAILABLE`;
      * the probe `try` at the top of a module-level helper the flag is resolved
        by (`FALKORDB_AVAILABLE = _falkordb_available()`).

    Nested `try`s inside a probe body are not roots: a `try/_proj.close()/except
    Exception: pass` cleanup is not the availability decision. The import-seam
    check reaches into them separately, because a swallowed import there is the
    same defect one level down.
    """
    roots: list[ast.Try] = []
    resolvers = _resolver_functions(tree)
    for stmt in tree.body:
        if isinstance(stmt, ast.Try) and _assigns_availability(stmt):
            roots.append(stmt)
        elif (
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name in resolvers
        ):
            roots.extend(s for s in stmt.body if isinstance(s, ast.Try) and s.handlers)
    return roots


def _is_own_code_import(node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.ImportFrom):
        if node.level:  # a relative import is this package by construction
            return True
        root = (node.module or "").split(".")[0]
        return root in _OWN_CODE_ROOTS
    return any(alias.name.split(".")[0] in _OWN_CODE_ROOTS for alias in node.names)


def _imports_own_code(block: ast.Try) -> bool:
    """Whether the block body imports OWN code, without leaving the block.

    Descends through control flow (`if`/`with`/`for`/`while`, and their `else`
    arms) because an import behind a platform or version guard is still the
    seam. Stops at a NESTED `try` — that block is graded on its own pass, so an
    optional import already wrapped in its own `try` is not charged to its
    parent — and at `def`/`class`/`lambda`, whose bodies are not this probe.
    """
    stack: list[ast.stmt] = list(block.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _is_own_code_import(node):
                return True
            continue
        if isinstance(node, ast.Try):
            continue
        if isinstance(node, (ast.If, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor,
                             ast.While)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []) or [])
        elif isinstance(node, (ast.ExceptHandler,)):
            stack.extend(node.body)
    return False


def _probe_blocks_from_source(
    src: str,
) -> tuple[tuple[int, tuple[str, ...], bool, bool, bool], ...]:
    """Classify every probe `try` in one source text.

    One row per probe `try`: `(lineno, covered, swallows, own_code_import, is_root)`.
    Rows include nested `try` blocks inside a probe body (`is_root=False`) so the
    import seam is graded one level down, without charging a cleanup `try` to the
    availability decision.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(src)
    rows: list[tuple[int, tuple[str, ...], bool, bool, bool]] = []
    for root in _probe_roots(tree):
        rows.append(
            (
                root.lineno,
                tuple(sorted(_covered(root.handlers))),
                _swallows_everything(root.handlers),
                _imports_own_code(root),
                True,
            )
        )
        for block in ast.walk(root):
            if block is root or not isinstance(block, ast.Try) or not block.handlers:
                continue
            rows.append(
                (
                    block.lineno,
                    tuple(sorted(_covered(block.handlers))),
                    _swallows_everything(block.handlers),
                    _imports_own_code(block),
                    False,
                )
            )
    return tuple(rows)


@functools.lru_cache(maxsize=1)
def _probe_report() -> tuple[tuple[str, int, tuple[str, ...], bool, bool, bool], ...]:
    out: list[tuple[str, int, tuple[str, ...], bool, bool, bool]] = []
    for path in _tracked_python_files():
        rel = str(path.relative_to(ROOT))
        if not (rel.startswith("tests/") or rel.startswith("tools/")):
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "FALKORDB_AVAILABLE" not in src:
            continue
        try:
            rows = _probe_blocks_from_source(src)
        except SyntaxError:
            continue
        out.extend((rel, *row) for row in rows)
    return tuple(out)


def _unarmed_probe_blocks() -> tuple[tuple[str, int], ...]:
    """Probe roots that swallow a programming error instead of re-raising it."""
    return tuple(
        (rel, lineno)
        for rel, lineno, covered, swallows, _own, is_root in _probe_report()
        if is_root and swallows and not set(covered) >= _RAISED
    )


def _probe_blocks_with_an_uncovered_import_seam() -> tuple[tuple[str, int], ...]:
    """Blocks that SWALLOW an own-code import their arm cannot see.

    `swallows` is required: a `try: … finally: …` probe has no broad arm, so an
    `ImportError` at its import propagates and there is nothing to fix — redding
    it would be a false positive on a shape that is already loud.
    """
    return tuple(
        (rel, lineno)
        for rel, lineno, covered, swallows, own_import, _is_root in _probe_report()
        if swallows and own_import and "ImportError" not in covered
    )


def test_every_availability_probe_re_raises_a_programming_error():
    """A rename must fail LOUDLY, not read as "storage unavailable".

    #13899's retirement hid for a whole wave behind exactly this arm: the probe's
    `except Exception` converted an `AttributeError` into `FALKORDB_AVAILABLE =
    False`, the family skipped locally, and CI — which refuses a skip — redded a
    shard with a message about live storage that named no door. Measured before
    this gate: ten modules in the same shape, plus 13 more in the assembly and
    `_probe` families — and eight more that resolve the flag through a helper
    (`FALKORDB_AVAILABLE = _probe()`), which this census now reaches too.
    """
    unarmed = _unarmed_probe_blocks()
    if unarmed:
        rendered = "\n".join(f"  {rel}:{lineno}" for rel, lineno in unarmed)
        pytest.fail(
            "these availability probes catch `Exception` without re-raising "
            "`(AttributeError, NameError, TypeError, ImportError)` first, so a "
            "renamed or mistyped seam reads as 'storage unavailable' — a SKIP "
            "locally and a hard refusal in CI that names no door (#14114 class "
            f"2, #14125):\n{rendered}\n\n"
            "Insert the re-raise arm immediately before the broad handler (one "
            "tuple, or separate `except ImportError: raise` — the four names are "
            "required between them, not in one line). Keep the broad arm: an "
            "absent server raises `redis.exceptions.ConnectionError` (a "
            "`RedisError`, NOT an `OSError`), so narrowing to `OSError` would "
            "turn a real skip into an error."
        )


def test_a_probe_that_imports_inside_its_try_covers_the_import_seam():
    """The arm must cover the import seam, not just be present.

    The property the class-2 gate exists to guarantee is *"a rename must fail
    loudly, not read as 'storage unavailable'"* — and the rename that matters
    most in this tree is a MODULE rename. Most probes name their module on the
    first line INSIDE the `try`, so a moved/renamed/split module raises
    `ModuleNotFoundError` there; with an arm that omits it, the block has an arm
    that can never fire for the very seam it wraps, falls through to
    `FALKORDB_AVAILABLE = False`, and the family SKIPS green over a broken tree
    (measured: 20 such blocks, plus 8 helper-resolved probes with no arm at all).
    """
    blind = _probe_blocks_with_an_uncovered_import_seam()
    if blind:
        rendered = "\n".join(f"  {rel}:{lineno}" for rel, lineno in blind)
        pytest.fail(
            "these availability probes import OWN code INSIDE the `try` but do "
            "not name `ImportError` in their arm, so moving or renaming the "
            "imported module is swallowed as 'storage unavailable' (#14125 "
            f"class 2, the import seam):\n{rendered}\n\n"
            "Add `ImportError` to the arm, or hoist the import above the `try`. "
            "(A third-party import is out of scope here: for an optional extra, "
            "a skip is the correct outcome.)"
        )


def test_the_import_seam_census_sees_the_shapes_it_grades():
    """Self-test: the seam census flags a narrow arm and clears a fixed one.

    Both directions, so the gate cannot go quietly blind (every row green) or
    quietly red (a fixed block still reported).
    """
    def rows(src: str) -> tuple[tuple[int, tuple[str, ...], bool, bool, bool], ...]:
        return _probe_blocks_from_source(textwrap.dedent(src))

    narrow = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except (AttributeError, NameError, TypeError):
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert narrow == ((3, ("AttributeError", "NameError", "TypeError"), True, True, True),), (
        "the census no longer flags a three-name arm over an own-code import "
        f"inside the `try`: {narrow}"
    )

    fixed = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except (AttributeError, NameError, TypeError, ImportError):
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert fixed == (
        (3, ("AttributeError", "ImportError", "NameError", "TypeError"), True, True, True),
    ), f"the census must see `ImportError` as covered, or it reds a fixed block: {fixed}"

    # A SPLIT arm covers the same four names — union across handlers, not one tuple.
    split = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except (AttributeError, NameError, TypeError):
            raise
        except ImportError:
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert split == (
        (3, ("AttributeError", "ImportError", "NameError", "TypeError"), True, True, True),
    ), f"a split arm covers all four names and must not be redded for its shape: {split}"

    # A bare `except:` covers NOTHING and swallows everything — the worst shape.
    bare = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            FALKORDB_AVAILABLE = True
        except:  # noqa: E722
            FALKORDB_AVAILABLE = False
        """
    )
    assert bare == ((3, (), True, True, True),), (
        f"a bare `except:` must read as swallowing everything with no coverage: {bare}"
    )

    # A nested `try/finally` (no handlers) is not a crash and not a row.
    nested = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _sdk = _ProbeSDK()
            try:
                _sdk.answer()
            finally:
                _sdk.close()
            FALKORDB_AVAILABLE = True
        except (AttributeError, NameError, TypeError, ImportError):
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert len(nested) == 1 and nested[0][0] == 3, (
        f"a handler-less nested `try/finally` must not become a row of its own: {nested}"
    )

    # An OPTIONAL third-party import is out of scope: it must not be redded,
    # because for an optional extra a skip is the correct outcome.
    optional = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            import some_optional_dep
            some_optional_dep.probe()
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert optional == ((3, (), True, False, True),), (
        f"a third-party import must not be charged to the seam rule: {optional}"
    )
    assert "ImportError" not in optional[0][1], "no arm is claimed where there is none"

    # The tree must still hold probe rows and importing probes — otherwise the
    # census is grading nothing and every row above is theatre.
    report = _probe_report()
    roots = sum(1 for row in report if row[5])
    importing = sum(1 for row in report if row[4])
    assert roots >= 30, (
        f"the census found only {roots} probe roots — it has stopped seeing the "
        "population it grades (module-body probes PLUS the helper-resolved ones)"
    )
    assert importing >= 20, (
        f"only {importing} probes import own code inside their `try` — the seam "
        "census has stopped seeing the population it grades"
    )
