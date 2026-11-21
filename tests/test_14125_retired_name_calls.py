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


#: Text-level gate applied BEFORE `ast.parse`. SOUND for both arms that consume the
#: parse: `receiver_is_sdk` can only answer True for a module that NAMES the SDK
#: somewhere (`_SDKISH` -> `^sdk|_sdk`, `TortoiseSDK`, a binding from
#: `TortoiseSDK(...)` / `_get_sdk()` / `_is_sdk_value`, or a stand-in class), and every
#: one of those spellings contains "sdk"; the probe census is confined to modules that
#: declare `FALKORDB_AVAILABLE`. Gating on the retired NAME alone is useless here —
#: `query`/`update`/`status`/`delete` are ordinary words, so 2,189 of 2,417 files matched
#: and parsing them all was ~50 s of this file's budget.
_PARSE_HINTS = ("sdk", "falkordb_available")


@functools.lru_cache(maxsize=1)
def _parsed_sources() -> tuple[tuple[str, str, ast.Module | None], ...]:
    """`(repo-relative path, source, AST)` for the tree, parsed ONCE.

    `tree is None` means the file was **prefiltered out** (`_PARSE_HINTS`) or did not
    parse. Both are kept in the tuple so a censusing arm still counts a file it did not
    read, instead of silently shrinking the population it grades — the fail-open shape
    this file exists to catch.
    """
    out: list[tuple[str, str, ast.Module | None]] = []
    for path in _tracked_python_files():
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(ROOT))
        lowered = src.lower()
        if not any(hint in lowered for hint in _PARSE_HINTS):
            out.append((rel, src, None))
            continue
        tree: ast.Module | None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(src)
        except SyntaxError:
            tree = None
        out.append((rel, src, tree))
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
        # ⚠️ A SOUND prefilter, not an optimisation guess: `receiver_is_sdk` can only
        # answer True for a module that NAMES the SDK somewhere — `_SDKISH` (^sdk|_sdk),
        # `TortoiseSDK`, a binding from `TortoiseSDK(...)` / `_get_sdk()` /
        # `_is_sdk_value`, or a stand-in class. Every one of those spellings contains
        # "sdk". Prefiling on the retired name alone is useless here precisely because
        # `query`/`update`/`status`/`delete` are ordinary words: 2,189 of 2,417 files
        # matched, and parsing them all was 50 s of this file's budget.
        # VALIDATED against the unfiltered walk on this tree: identical rows (2).
        if "sdk" not in src.lower():
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

#: The names a probe's re-raise arm must carry. `ImportError` is the IMPORT seam and
#: is not optional here: the import sits INSIDE the availability `try` in 19 of these
#: blocks, so a renamed or moved first-party module (`ModuleNotFoundError`, a
#: subclass) is the SAME silent SKIP through a different vector as a renamed SDK
#: method — measured on this tree, `git mv tortoise/projection.py tortoise/proj.py`
#: left all 19 skipping green while this guard stayed green with them (#14125
#: re-review). All four are programming errors. The broad arm stays, and narrowing is
#: not the fix: a genuinely absent server raises `redis.exceptions.ConnectionError`
#: (a `RedisError`, NOT an `OSError`), so `except OSError` would turn a real skip into
#: an error.
_RAISED = frozenset({"AttributeError", "NameError", "TypeError", "ImportError"})

#: Module-level names whose binding makes a module a PROBE module. `_AVAILABLE` is
#: here because two probes (`test_13302_ended_read`, `test_13557_13558_reachable_reads`)
#: publish the flag under that name, so a census keyed only on `FALKORDB_AVAILABLE`
#: skips two files it claims to grade.
_AVAILABILITY = frozenset({"FALKORDB_AVAILABLE", "_AVAILABLE"})


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"<bare>"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {
        getattr(n, "id", None) or getattr(n, "attr", None)
        for n in nodes
    } - {None}


def _bound_names(targets: list[ast.expr]) -> set[str]:
    """Every name an assignment target binds, destructuring `A, B = …`.

    `FALKORDB_AVAILABLE, _WORKING_URI = _probe_falkordb([...])` binds the flag through
    an `ast.Tuple` target. A check that reads only `ast.Name` targets cannot see that
    binding — which is precisely how four files this branch repaired stayed invisible
    to the census (`#14125` review F3b).
    """
    out: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            out.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            out |= _bound_names(target.elts)
        elif isinstance(target, ast.Starred):
            out |= _bound_names([target.value])
    return out


def _assigns_availability(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        targets = (
            sub.targets
            if isinstance(sub, ast.Assign)
            else [sub.target]
            if isinstance(sub, ast.AnnAssign)
            else []
        )
        if _bound_names(targets) & _AVAILABILITY:
            return True
    return False


def _cleanup_tries(root: ast.AST) -> set[int]:
    """`id()` of every `ast.Try` inside a `finally:` clause — cleanup, not a verdict."""
    out: set[int] = set()
    for sub in ast.walk(root):
        if isinstance(sub, ast.Try):
            for stmt in sub.finalbody:
                out |= {
                    id(inner) for inner in ast.walk(stmt) if isinstance(inner, ast.Try)
                }
    return out


def _broad_tries(node: ast.AST) -> list[ast.Try]:
    """Broad `except Exception` blocks under `node`, minus cleanup swallows.

    `try: proj.close() / except Exception: pass` lives in a `finally:` and is
    deliberately tolerant — a failed close cannot mean "storage unavailable", so
    demanding a re-raise there would be a false block. The PROBE is the block whose
    arm decides the module's result.
    """
    cleanup = _cleanup_tries(node)
    return [
        sub
        for sub in ast.walk(node)
        if isinstance(sub, ast.Try)
        and id(sub) not in cleanup
        and any("Exception" in _handler_names(h) for h in sub.handlers)
    ]


def _probe_tries(
    expr: ast.AST,
    helpers: dict[str, ast.AST],
    seen: set[str],
    depth: int = 0,
) -> list[ast.Try]:
    """Broad tries reachable from `expr`, following SAME-MODULE helper calls.

    One hop is enough for every probe in this tree; `depth` and `seen` are
    cycle/runaway guards, not a scope decision. Following the call is what makes the
    repair of `tests/test_indexes.py`, `test_graphcopy_boolean_index_3154.py`,
    `test_restore_seq_3902.py` and `test_search_engine_gaps.py` observable at all: in
    those the `try` lives in the helper, not in the module-level statement.
    """
    out = _broad_tries(expr)
    if depth >= 3:
        return out
    for sub in ast.walk(expr):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)):
            continue
        name = sub.func.id
        if name not in helpers or name in seen:
            continue
        seen.add(name)
        out += _probe_tries(helpers[name], helpers, seen, depth + 1)
    return out


def _probe_blocks_in(
    rel: str, src: str, tree: ast.Module | None
) -> list[tuple[str, int, bool]]:
    """The availability-probe `try` blocks of ONE module: `(path, line, armed?)`.

    A probe resolves its flag at IMPORT (a fixture runs too late), so the population
    is every module-level binding of an availability name, read in the TWO shapes the
    flag actually arrives in:

      * a module-level `try:` that binds it — the arms set `FALKORDB_AVAILABLE = False`;
      * a module-level binding whose VALUE is a same-module helper call —
        `_AVAILABLE, _WORKING_URI = _probe([...])` — with the `try` inside the helper.

    Grading only the first shape is what left 11 `_falkordb_available()` /
    `_probe(candidates)` modules ungraded while this gate reported the class closed
    (`#14125` review F3a), and `armed` is checked against the whole of `_RAISED` so a
    handler that carries three of the four names is not a re-raise arm.
    """
    if tree is None or not (rel.startswith("tests/") or rel.startswith("tools/")):
        return []
    if not any(name in src for name in ("FALKORDB_AVAILABLE", "_AVAILABLE")):
        return []
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    blocks: list[ast.Try] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Try) and _assigns_availability(stmt):
            blocks += _broad_tries(stmt)
            continue
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if stmt.value is not None and _bound_names(targets) & _AVAILABILITY:
            blocks += _probe_tries(stmt.value, helpers, set())
    rows: dict[tuple[str, int], bool] = {}
    for block in blocks:
        kinds = [_handler_names(h) for h in block.handlers]
        rows[(rel, block.handlers[0].lineno)] = any(_RAISED <= k for k in kinds)
    return [(path, line, armed) for (path, line), armed in sorted(rows.items())]


@functools.lru_cache(maxsize=1)
def _probe_blocks() -> tuple[tuple[str, int, bool], ...]:
    rows: list[tuple[str, int, bool]] = []
    for rel, src, tree in _parsed_sources():
        rows += _probe_blocks_in(rel, src, tree)
    return tuple(rows)


def test_every_availability_probe_re_raises_a_programming_error():
    """A rename must fail LOUDLY, not read as "storage unavailable".

    #13899's retirement hid for a whole wave behind exactly this arm: the probe's
    `except Exception` converted an `AttributeError` into `FALKORDB_AVAILABLE =
    False`, the family skipped locally, and CI — which refuses a skip — redded a
    shard with a message about live storage that named no door. Measured before
    this gate: ten modules in the same shape, plus 13 more in the assembly and
    `_probe` families; eleven more — the `_falkordb_available()` and
    `_probe(candidates)` helpers — arrived through the helper-assigned shape the
    census could not see until it followed the call (`#14125` review F3a/F3b).
    """
    unarmed = [(rel, lineno) for rel, lineno, armed in _probe_blocks() if not armed]
    if unarmed:
        rendered = "\n".join(f"  {rel}:{lineno}" for rel, lineno in unarmed)
        pytest.fail(
            "these availability probes catch `Exception` without re-raising "
            "`(AttributeError, NameError, TypeError, ImportError)` first, so a "
            "renamed or mistyped seam — or a renamed/moved module, whose import is "
            "inside the same `try` — reads as 'storage unavailable': a SKIP "
            "locally and a hard refusal in CI that names no door (#14114 class 2, "
            f"#14125):\n{rendered}\n\n"
            "Insert the re-raise arm immediately before the broad handler. Keep "
            "the broad arm: an absent server raises `redis.exceptions."
            "ConnectionError` (a `RedisError`, NOT an `OSError`), so narrowing to "
            "`OSError` would turn a real skip into an error."
        )


def test_the_census_follows_a_probe_helper():
    """The census must grade a probe whose `try` sits in a helper, and skip cleanup.

    Four files this branch armed bind the flag through a DESTRUCTURED target whose
    value is a same-module `_probe_falkordb(...)`, so their `try` is inside the
    helper. Measured at `b2ffb4fe9f`: deleting the arm in `tests/test_indexes.py`
    reddened NOTHING (`#14125` review F3b) — the repair was unobservable. This pins
    both halves of the widening on a synthetic module, so a later narrowing has to
    red a row instead of quietly dropping the population again.
    """

    def blocks(body: str) -> list[tuple[str, int, bool]]:
        src = textwrap.dedent(body)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(src)
        return _probe_blocks_in("tests/test_synthetic.py", src, tree)

    unarmed = blocks(
        """
        FALKORDB_AVAILABLE = False

        def _probe(candidates):
            for uri in candidates:
                if not uri:
                    continue
                proj = None
                try:
                    proj = build(uri)
                    proj.g.query("RETURN 1")
                    return True, uri
                except Exception:
                    continue
            return False, None

        FALKORDB_AVAILABLE, _WORKING_URI = _probe([])
        """
    )
    assert [armed for _, _, armed in unarmed] == [False], (
        "the census stopped following a destructured module-level binding into the "
        f"helper that holds the probe `try` — it now sees {unarmed}"
    )

    armed_rows = blocks(
        """
        FALKORDB_AVAILABLE = False

        def _probe(candidates):
            for uri in candidates:
                if not uri:
                    continue
                proj = None
                try:
                    proj = build(uri)
                    proj.g.query("RETURN 1")
                    return True, uri
                except (AttributeError, NameError, TypeError, ImportError):
                    raise
                except Exception:
                    continue
            return False, None

        FALKORDB_AVAILABLE, _WORKING_URI = _probe([])
        """
    )
    assert [armed for _, _, armed in armed_rows] == [True], (
        f"a fully armed helper probe is not read as armed — the census says {armed_rows}"
    )

    partial = blocks(
        """
        FALKORDB_AVAILABLE = False

        def _probe(candidates):
            for uri in candidates:
                proj = None
                try:
                    proj = build(uri)
                    proj.g.query("RETURN 1")
                    return True, uri
                except (AttributeError, NameError, TypeError):
                    raise
                except Exception:
                    continue
            return False, None

        FALKORDB_AVAILABLE, _WORKING_URI = _probe([])
        """
    )
    assert [armed for _, _, armed in partial] == [False], (
        "an arm that omits `ImportError` lets a renamed/moved module read as "
        f"'storage unavailable' — the census called it armed: {partial}"
    )

    with_cleanup = blocks(
        """
        FALKORDB_AVAILABLE = False

        def _probe(candidates):
            for uri in candidates:
                proj = None
                try:
                    proj = build(uri)
                    proj.g.query("RETURN 1")
                    return True, uri
                except (AttributeError, NameError, TypeError, ImportError):
                    raise
                except Exception:
                    continue
                finally:
                    if proj is not None:
                        try:
                            proj.close()
                        except Exception:
                            pass
            return False, None

        FALKORDB_AVAILABLE, _WORKING_URI = _probe([])
        """
    )
    assert [armed for _, _, armed in with_cleanup] == [True], (
        "a cleanup swallow inside a `finally:` is not a probe verdict and must not be "
        f"graded as one — the census reported {with_cleanup}"
    )


def test_the_probe_census_is_not_vacuous():
    """The census must find the probe blocks it claims to grade."""
    rows = _probe_blocks()
    total = len(rows)
    assert total >= 40, (
        f"the probe census found only {total} availability `try` blocks — it has "
        "stopped seeing the population it grades (43 across 42 files when the "
        "helper-assigned shape was added to the walk)"
    )
