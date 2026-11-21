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

#: `sdk_standin_classes` calls `approved_methods()` once per module, and that function
#: EXECS `tortoise/__all__.py` to read the declaration — ~2,400 module executions over
#: this tree, which was the whole cost of this file (~103 s, of which ~25 s is the
#: session conftest every file in the pool pays). Memoised HERE rather than in the tool:
#: the answer is a constant of the tree, but `tools/sdk_privatise.py` is a shipped
#: verifier and an optimisation to it belongs in its own reviewed change.
privatise.approved_methods = functools.lru_cache(maxsize=1)(privatise.approved_methods)


def _tracked_python_files() -> list[Path]:
    """Every tracked (and untracked-but-not-ignored) `.py`, exactly as the sweep sees it."""
    return privatise.py_files()


@functools.lru_cache(maxsize=1)
def _parsed_sources() -> tuple[tuple[str, str, ast.Module | None], ...]:
    """`(repo-relative path, source, AST)` for the tree, parsed ONCE.

    The gate has four arms that each need a walk, and parsing ~2,400 files four
    times was ~40 s of its ~100 s budget. `None` means the file did not parse —
    it is kept so the census still counts a file it could not read, rather than
    silently shrinking the population it grades (`#14125` review: a censusing
    arm that skips what it cannot parse is the fail-open shape it exists to
    catch).
    """
    out: list[tuple[str, str, ast.Module | None]] = []
    for path in _tracked_python_files():
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tree: ast.Module | None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(src)
        except SyntaxError:
            tree = None
        out.append((str(path.relative_to(ROOT)), src, tree))
    return tuple(out)


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
    for rel, src, tree in _parsed_sources():
        if tree is None:  # pragma: no cover — a broken file reds its own suite
            continue
        if not any(name in src for name in RETIRED_NAMES):
            continue
        in_sdk_file = Path(rel).resolve() == privatise.SDK_SRC.resolve()
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

_RAISED = frozenset({"AttributeError", "NameError", "TypeError"})


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"<bare>"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {
        getattr(n, "id", None) or getattr(n, "attr", None)
        for n in nodes
    } - {None}


def _assigns_availability(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        targets = (
            sub.targets
            if isinstance(sub, ast.Assign)
            else [sub.target]
            if isinstance(sub, ast.AnnAssign)
            else []
        )
        if any(
            isinstance(t, ast.Name) and t.id == "FALKORDB_AVAILABLE" for t in targets
        ):
            return True
    return False


@functools.lru_cache(maxsize=1)
def _probe_blocks() -> tuple[tuple[str, int, bool], ...]:
    """Every module-scope availability-probe `try` block: `(path, line, armed?)`.

    The scope is deliberately module level: a probe resolves `FALKORDB_AVAILABLE`
    at IMPORT (a fixture runs too late), so the block that sets it is the one that
    decides whether a rename becomes a skip. `armed` means a
    `(AttributeError, NameError, TypeError)` re-raise arm precedes the broad one.
    """
    out: list[tuple[str, int, bool]] = []
    for rel, src, tree in _parsed_sources():
        if tree is None or not (rel.startswith("tests/") or rel.startswith("tools/")):
            continue
        if "FALKORDB_AVAILABLE" not in src:
            continue
        for top in tree.body:
            if not isinstance(top, ast.Try) or not _assigns_availability(top):
                continue
            for block in ast.walk(top):
                if not isinstance(block, ast.Try):
                    continue
                kinds = [_handler_names(h) for h in block.handlers]
                if not any("Exception" in k for k in kinds):
                    continue
                armed = any(k & _RAISED for k in kinds)
                out.append((rel, block.handlers[0].lineno, armed))
    return tuple(out)


def test_every_availability_probe_re_raises_a_programming_error():
    """A rename must fail LOUDLY, not read as "storage unavailable".

    #13899's retirement hid for a whole wave behind exactly this arm: the probe's
    `except Exception` converted an `AttributeError` into `FALKORDB_AVAILABLE =
    False`, the family skipped locally, and CI — which refuses a skip — redded a
    shard with a message about live storage that named no door. Measured before
    this gate: ten modules in the same shape, plus 13 more in the assembly and
    `_probe` families.
    """
    unarmed = [(rel, lineno) for rel, lineno, armed in _probe_blocks() if not armed]
    if unarmed:
        rendered = "\n".join(f"  {rel}:{lineno}" for rel, lineno in unarmed)
        pytest.fail(
            "these module-level availability probes catch `Exception` without "
            "re-raising `(AttributeError, NameError, TypeError)` first, so a "
            "renamed or mistyped seam reads as 'storage unavailable' — a SKIP "
            "locally and a hard refusal in CI that names no door (#14114 class 2, "
            f"#14125):\n{rendered}\n\n"
            "Insert the re-raise arm immediately before the broad handler. Keep "
            "the broad arm: an absent server raises `redis.exceptions."
            "ConnectionError` (a `RedisError`, NOT an `OSError`), so narrowing to "
            "`OSError` would turn a real skip into an error."
        )


def test_the_probe_census_is_not_vacuous():
    """The census must find the probe blocks it claims to grade."""
    total = len(_probe_blocks())
    assert total >= 20, (
        f"the probe census found only {total} module-level availability `try` "
        "blocks — it has stopped seeing the population it grades (this box "
        "measured 26+ when the gate landed)"
    )
