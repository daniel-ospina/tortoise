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
import copy
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


def _unaliased_sdk_class(tree: ast.Module) -> ast.Module:
    """Rewrite `from tortoise.sdk import TortoiseSDK as X` so `X` types as the SDK.

    `privatise.sdk_typing` types a receiver from the LITERAL class name, so an
    ALIASED import was a hole in both this gate and the sweep: measured —
    `from tortoise.sdk import TortoiseSDK as _ProbeSDK` + `_ProbeSDK().create_point(…)`
    yielded `[]` while the un-aliased body yielded `[(_p.create_point)]`. That is a
    false PASS, and the alias is not exotic: the gate's own self-test and
    `tests/test_identifier_tokens_8933.py`'s probe both spell the SDK that way.

    Only the use sites are rewritten (the import keeps its `asname`), and the
    rewrite is on a deep copy so no other assertion in this file sees a mutated
    tree.
    """
    aliases = {
        alias.asname
        for node in tree.body  # module level only: a FUNCTION-LOCAL alias can
        if isinstance(node, ast.ImportFrom)  # shadow an unrelated name elsewhere
        and (node.module or "").split(".")[0] == "tortoise"
        for alias in node.names
        if alias.name == "TortoiseSDK" and alias.asname
    }
    # A module-scope alias can still be SHADOWED by a local binding of the same
    # name (`tests/test_dod_5090_probe.py` has an alias `module` and unrelated
    # `for module in …` loop targets). Rewriting those Loads would type a loop
    # variable as the SDK and manufacture a false red, so an id that is RE-BOUND
    # inside any function/comprehension is left alone.
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        # Every STORE context in that scope — an `=` target, a `for`/`with …
        # as` target, a comprehension target — rebinds the name locally.
        rebound |= {
            sub.id
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)
        }
    aliases -= rebound
    if not aliases:
        return tree
    tree = copy.deepcopy(tree)
    for node in ast.walk(tree):
        # LOAD sites only. A `Store` context is a BINDING of that name, not a use
        # of the SDK: rewriting `for module in …` would type a loop variable as
        # the SDK and manufacture a false red (`tests/test_dod_5090_probe.py`
        # holds exactly that pair — an alias `module` at a function scope and
        # unrelated `for module in …` targets elsewhere).
        if (
            isinstance(node, ast.Name)
            and node.id in aliases
            and isinstance(node.ctx, ast.Load)
        ):
            node.id = "TortoiseSDK"
    return tree


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
    tree = _unaliased_sdk_class(tree)
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

    # ⛔ THE THIRD ARM OF CLASS 1 (`review of d2d783ca`, P2). The docstring above
    # names the script-shaped seed as "where the #14114 reds actually lived", and
    # that arm had no pin: deleting the `ast.Constant` + recursive-parse branch
    # left every test in this file green. Built from a JOIN so this file does not
    # flag itself (a single literal holding a newline and an `import` IS a
    # script-shaped string — that is the arm).
    seed = chr(10).join(
        [
            "import os",
            "from tortoise.sdk import TortoiseSDK",
            "sdk = TortoiseSDK()",
            "sdk." + "create_point('statement', 'x')",
        ]
    )
    assert _references(ast.parse("SEED = " + repr(seed) + chr(10)), False) == [
        (4, "sdk.create_point  [inside the script at line 1]")
    ], (
        "the `python -c` seed shape is no longer swept — it is the arm #14114's "
        "reds lived in, and a node walk over the FILE cannot see it"
    )

    # ⛔ THE ALIAS CASE (`review of 03fc244, P1`). `sdk_typing` types a receiver
    # from the LITERAL class name, so `from tortoise.sdk import TortoiseSDK as _P`
    # was a hole in both this gate and `tools/sdk_privatise.py`: the aliased body
    # yielded `[]` where the plain one yielded the call. Replacing
    # `_unaliased_sdk_class` with the identity reddens exactly these two.
    def _script(*lines: str) -> ast.Module:
        """Parse a synthetic module WITHOUT writing a script-shaped literal.

        A single string holding both a newline and an `import` is exactly what
        this gate's class-1 sweep parses as a `python -c` seed, so a witness
        written that way flags ITSELF. Line lists keep the sweep's own file clean.
        """
        return ast.parse("".join(line + chr(10) for line in lines))

    aliased = _script(
        "from tortoise.sdk import TortoiseSDK as _P",
        "_p = _P()",
        "_p.create_point(content='x')",
    )
    assert _references(aliased, False) == [(3, "_p.create_point")], (
        "an ALIASED `TortoiseSDK` import must type as the SDK — otherwise a "
        "retired door is reachable through it and this gate reads green"
    )
    direct = _script(
        "from tortoise.sdk import TortoiseSDK",
        "TortoiseSDK().create_point(content='x')",
    )
    assert _references(direct, False) == [(2, "TortoiseSDK().create_point")], (
        "a bare construction must be typed as the SDK"
    )
    # …and a STORE-context binding of the alias name is not a use of the SDK: a
    # function-scope alias can shadow an unrelated loop variable, and rewriting
    # `for module in …` would manufacture a false red
    # (`tests/test_dod_5090_probe.py` holds that pair).
    shadowed = _script(
        "from tortoise.sdk import TortoiseSDK as module",
        "def f(items):",
        "    for module in items:",
        "        module.query('MATCH (n) RETURN n')",
    )
    assert _references(shadowed, False) == [], (
        "a loop variable that shadows a module-scope alias is NOT the SDK — "
        "rewriting store contexts manufactures a false red"
    )


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

#: The subclass spellings that mean the SAME event as an `_RAISED` member, normalised on
#: the way in (charging) and on the way out (coverage accounting).
#: `ModuleNotFoundError` is how a moved/renamed module actually raises — it is
#: `ImportError`'s canonical spelling at the seam this file exists for — and
#: `UnboundLocalError` is `NameError`'s.
#:
#: ⛔ WHAT THE MAP FIXES IS THE CHARGE SIDE, and the honest statement of the other side is
#: that it cannot be fixed here: `_required_names` holds only the concrete parents and the
#: seam gate tests the literal `"ImportError"`, so a credited subclass spelling satisfies
#: neither — a block that re-raises ONLY `ModuleNotFoundError` is still red. That is
#: deliberate, because the arm naming the subclass does not re-raise the plain `ImportError`
#: that a renamed NAME raises inside an own-code import. The map's live effects are the
#: charge and the masking (`_masked_by`), where a parent arm really does mask a child one
#: (review of #14287, P2-2).
_RAISED_ALIAS = {
    "ModuleNotFoundError": "ImportError",
    "ZipImportError": "ImportError",
    "UnboundLocalError": "NameError",
}
_RAISED_ALL = _RAISED | set(_RAISED_ALIAS)

#: What counts as a BROAD arm. `BaseException` belongs here: it swallows the
#: same programming errors `Exception` does (and more), so a census that asked
#: only for the word `Exception` read `except BaseException: AVAILABLE = False`
#: as non-swallowing and passed the exact defect green (review of 03fc244, P2).
_BROAD = frozenset({"Exception", "BaseException"})

# The import-seam rule is scoped to imports that are IMPORT-TIME-SAFE WITHOUT
# EXTRAS — in practice the repo's own code, `tortoise.*` / `tests.*` / `tools.*` /
# `battery.*` / `benchmarks.*` (`_OWN_CODE_ROOTS`, and the last two are first-party
# too: both carry `__init__.py`, both import with no extras, and
# `battery/runner/setup.py` defines our own `query` wrapper — leaving them out made
# `import battery.runner.setup as torch; torch.query(…)` read EXEMPT over our seam).
# An own-code module that is moved is a defect and must fail loudly; a module
# behind an OPTIONAL extra must keep its skip, and "own code" does NOT imply that:
# `tortoise/embeddings.py` does `import numpy as np` at module scope and numpy ships
# only in the `embeddings` extra, which is why
# `tests/test_rebuild_graph_only_derived_parity.py` wraps `from tortoise.embeddings`
# in a broad `try` with a deliberate fallback. Demanding a re-raise there would turn
# a legitimate skip into a collection error and contradict the recorded rule that
# an absent capability skips. (That block DOES import own code inside its `try`
# and is still not graded today — but for a different reason: it binds no
# availability decision and sits in no skip-gated helper, so `_probe_roots`
# never reaches it. A future widening that grades ANY module-body `try` that
# imports own code WOULD charge it, and that is the sentence to re-read before
# widening.)
_OWN_CODE_ROOTS = frozenset({"tortoise", "tests", "tools", "battery", "benchmarks"})


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"<bare>"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {getattr(n, "id", None) or getattr(n, "attr", None) for n in nodes} - {None}


def _swallows_everything(handlers: list[ast.ExceptHandler]) -> bool:
    """Whether some handler here CATCHES a programming error and DROPS it.

    The arms that count are a bare arm, an `Exception`/`BaseException` arm, and — since
    review cycle 7 of #14160 — a NARROW arm naming one of `_RAISED`, because narrowing
    `except Exception:` to the four names and KEEPING the drop is the substitution a lane
    is most likely to make when this gate reds, and it swallows the rename exactly as
    before. `ImportError` is INCLUDED: excluding it (cycle 8 of #14160) opened a hole in
    BOTH gates at once, because the seam gate is conditioned on this predicate — with
    `swallows=False`, `except ImportError: FLAG = False` around `from tortoise.sdk import …`
    read green in `_unarmed_probe_blocks` AND in
    `_probe_blocks_with_an_uncovered_import_seam`, i.e. the exact module-rename seam this
    file exists to catch (measured, cycle 9 of #14160). The legitimate skip it was
    protecting is the optional-extra import — `from tortoise.embeddings import …` raises
    `ImportError` when numpy is absent — and this predicate cannot tell the two apart: the
    ARM is identical, only the imported MODULE differs, so the decision belongs to a gate
    that knows the import. Until one carries it, that shape is a DECLARED RESIDUAL below.

    ⛔ DECLARED RESIDUAL (issue #14125): an OWN-CODE import that genuinely needs an
    optional extra — `from tortoise.embeddings import …`, where numpy ships only in the
    `embeddings` extra — is legitimately dropped by `except ImportError:`, and this
    predicate charges it. There is no allowance for it: the EXTRA-GATED shape has 0 GRADED
    instances, and a module-keyed allowance would be machinery for a population that does
    not exist.

    ⛔ AND THERE ARE UNGRADED INSTANCES OF THE FAMILY, so do NOT quote a number from
    memory: three successive drafts of this paragraph carried a wrong count, a wrong scope
    word and a citation from the wrong tree (review of #14160, cycles 12-14). The family is
    "an own-code import dropped by an `ImportError`-family arm"; instances of it exist in
    `tests/` and `tools/`, none of them is a probe root, and that is why the census is
    silent about them. Re-derive the population from the census's own inputs before adding
    an allowance: a block for which `_imports_own_code` holds, whose `ImportError`-NAMING
    ARM does not re-raise (`not _reraises(that_handler)`) —
    NOT `_swallows_everything`, which also charges broad-`Exception` drops and therefore
    matches far more, including graded roots. A future widening that admits own-code `try`s
    outside the probe roots meets this rule first.

    Whether the handler re-raises is part of the question, not a separate one:
    the property is *"the programming error never reaches the caller"*, and a
    broad arm that re-raises never swallows anything. Charging it would be a
    false red on the CORRECT shape — `except Exception: _proj.close(); raise`
    (clean up, then propagate) is a probe that already fails loudly, and
    `tests/e2e/hosted/conftest.py` is exactly that shape.
    """
    #: ONE reachability notion for BOTH predicates (review of #14287, cycles 1-2). An arm
    #: that Python can never reach is not a drop: `except ImportError: raise` followed by an
    #: (unreachable) `except ModuleNotFoundError: FLAG = False` propagates everything, and
    #: the order-blind `any()` charged it — a FALSE RED the delta introduced, on the exact
    #: ordering a lane gets by following this gate's own repair message above an existing
    #: precise skip.
    caught_earlier: set[str] = set()
    for handler in handlers:
        names = _handler_names(handler)
        if names == {"<bare>"} or names & _BROAD:
            # nothing below a broad/bare arm can run: it catches whatever is left
            return not _reraises(handler)
        reachable = {name for name in names if not _masked_by(name, caught_earlier)}
        if reachable & (_BROAD | _RAISED_ALL) and not _reraises(handler):
            return True
        caught_earlier |= names
    return False


def _masked_by(name: str, caught_earlier: set[str]) -> bool:
    """Whether an EARLIER arm already catches every instance `name` would catch.

    Exact spelling, or a PARENT of it: an earlier `except ImportError:` catches every
    `ModuleNotFoundError`, so a later `except ModuleNotFoundError: raise` is unreachable —
    but the converse is NOT true, and that asymmetry is why this is a containment test and
    not a canonical-set membership test (review of #14160, cycle 12). A later plain
    `except ImportError:` after an `except ModuleNotFoundError:` arm is perfectly reachable
    for a plain `ImportError`, and must still be credited.
    """
    return name in caught_earlier or _RAISED_ALIAS.get(name) in caught_earlier


def _covered(handlers: list[ast.ExceptHandler]) -> set[str]:
    """Names covered by the NARROW arms, unioned across handlers.

    The union, not the per-handler set: a split arm —
    `except (AttributeError, NameError, TypeError): raise` followed by
    `except ImportError: raise` — re-raises all four, so it satisfies the same
    property as one four-name tuple and must not be redded for its shape. The
    broad arm is never coverage: it is what swallows the rest.

    A handler only COUNTS when its LAST statement re-raises its own caught exception —
    a bare `raise`, `raise exc`, or a chained `raise X from exc` (see `_reraises`) — and
    never when it merely NAMES a programming error in the clause: `except ImportError:`
    `return False` names the seam and then swallows it, and counting the name would let a
    probe pass this gate with exactly the behaviour it exists to forbid.

    ⛔ ORDER IS PART OF THE PROPERTY (review F2, and it was still open at
    `9b7ac6ee8d`). Python tests `except` clauses in source order and runs only
    the first match, so a narrow arm placed AFTER the broad one is UNREACHABLE:
    `except Exception: …` then `except ImportError: raise` reads as armed to a
    set-membership check, while behaving exactly like the pre-fix code. Coverage
    is therefore what the narrow arms BEFORE the first broad arm can still catch.

    ⛔ AND ORDER APPLIES AMONG THE NARROW ARMS TOO, which is the half this walked past
    (review of #14160, cycle 10, P1-1). Python runs the FIRST matching arm, so an EARLIER
    narrow arm that DROPS a name has already handled it; a LATER re-raising arm naming the
    same name is unreachable for it and credits nothing. Crediting it defeats exactly the
    substitution `_swallows_everything` charges, and the shape is one the gate's own repair
    message produces — "Insert the re-raise arm immediately before the broad handler" — on a
    block that already carries an `except ImportError:` skip: the charged ImportError drop
    stayed green in BOTH gates. Measured, and the population is unchanged (70/58/0).

    ⛔ THE HIERARCHY IS COMPARED, THE SPELLING IS CREDITED — and the two are NOT the same
    set (review of #14160, cycle 11, both P1s). Accounting is done on the CANONICAL name, so
    an earlier `except ImportError:` also masks a later `except ModuleNotFoundError:` (it
    catches every subclass, so that later arm is unreachable for the name). Credit is
    returned as the RAW spelling, because an arm that narrows to `ModuleNotFoundError` does
    NOT re-raise a plain `ImportError` — crediting the parent for it told the seam gate that
    any ImportError propagates while a reachable arm below could still swallow one. So:
    charge and account by FAMILY, credit by SPELLING, and never normalise the result.

    ⛔ AND A FAMILY AN EARLIER ARM *DROPPED* CANNOT BE CREDITED EITHER — masking is one-way
    (parent masks child) but DROPPING is not: `except ModuleNotFoundError: FLAG = False`
    followed by `except (AttributeError, NameError, TypeError, ImportError): raise` left the
    move/rename swallowed by arm 1 while arm 2 credited `ImportError`, which is the ONE name
    both gates test — green in both over #14125 class 2 verbatim. Measured: 278 of 30,930
    enumerated arm sequences leaked, all this single root (review of #14287, P1-1). So the
    credit set is also family-closed against what has already been DROPPED.
    """
    out: set[str] = set()
    caught_earlier: set[str] = set()
    dropped_family: set[str] = set()
    for handler in handlers:
        names = _handler_names(handler)
        if names == {"<bare>"} or names & _BROAD:
            break  # nothing below a broad arm can ever run
        reachable = {name for name in names if not _masked_by(name, caught_earlier)}
        if _reraises(handler):
            #: per-NAME, not per-arm: an arm that names four errors credits only the ones
            #: whose family no earlier arm already caught or DROPPED.
            out |= {
                name
                for name in reachable
                if _RAISED_ALIAS.get(name, name) not in dropped_family
            }
        else:
            dropped_family |= {_RAISED_ALIAS.get(name, name) for name in reachable}
        caught_earlier |= names
    return out


def _reraises(handler: ast.ExceptHandler) -> bool:
    """Whether the handler ENDS by re-raising (nothing is dropped).

    The last statement is what decides it, so the correct cleanup shape —
    `except (AttributeError, …): _proj.close(); raise` — counts. A coverage rule
    demanding "exactly one bare `raise`" redded that shape as soon as the block
    became a root (review of `ee5457f`, P3). The shape is exercised by the
    `cleanup_raise` self-test below rather than by a live probe: the one probe
    that had it now propagates with a bare `raise`, which is stricter.

    ⛔ DECLARED RESIDUAL (issue #14125): this predicate sees the HANDLER only, so a `Try`
    whose `finalbody` ends in `return`/`break`/`continue` discards the raise it credits —
    `try: … except Exception: raise finally: return ok` reads as propagating while the
    probe still converts the failure into "capability unavailable", and because
    `_probe_blocks_with_an_uncovered_import_seam` is conditioned on `swallows`, both gates
    skip it. The seam gate's rationale — a HANDLER-LESS `try/finally` probe has no broad arm,
    so its import error propagates — describes a different shape: this one carries a broad
    arm that re-raises, and it is skipped only because that re-raise reads as propagating. It needs the owning `ast.Try`, not the handlers; the shape has
    0 instances in `tests/`+`tools/` (no probe there has `finally:` + `return`), so the
    machinery is deferred and this paragraph is the record. Re-read it before adding the
    first live instance.

    A trailing bare `raise` keeps the anti-`except ImportError: return False`
    property: the clause is only coverage if it actually propagates.
    """
    if not handler.body:
        return False
    _last = handler.body[-1]
    if isinstance(_last, ast.Raise) and _last.exc is not None:
        # `raise exc` re-raises THE SAME object, so nothing is dropped and the clause is
        # coverage — `_reraises` reads it that way too, and a lane following
        # this gate's own repair instruction ("insert the re-raise arm") in that idiomatic
        # spelling was redded for having fixed it (review of #14160, cycle 6, P2-6).
        _caught = _handler_names(handler) | ({handler.name} if handler.name else set())
        if isinstance(_last.exc, ast.Name) and _last.exc.id in _caught:
            return True
        # PEP 3134's CHAINED form is the same propagation with a nicer message, and the
        # SWALLOW side must read it the same way `_covered` does (the consumer here is
        # `_covered`; it counts the chained form because THIS predicate does, not the
        # other way round) — reading the two halves differently
        # made ONE handler simultaneously "no coverage" and "re-raises, drops nothing", so a
        # block written per this gate's own repair instruction read UNARMED (review of
        # #14160, cycle 7, P2-2). `raise X from None` discards the cause and is not coverage.
        return isinstance(_last.cause, ast.Name) and _last.cause.id in _caught
    return bool(handler.body) and (
        isinstance(handler.body[-1], ast.Raise) and handler.body[-1].exc is None
    )


def _availability_targets(stmt: ast.stmt) -> list[ast.expr]:
    if isinstance(stmt, ast.Assign):
        return list(stmt.targets)
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target]
    return []


#: The module-level flag names this tree resolves a capability into. A second
#: SPELLING is not a second defect: the flag is the availability DECISION either
#: way, and two `_probe(…)` families resolve `_AVAILABLE, _WORKING_URI = _probe(…)`
#: — so a census keyed on one spelling graded NOTHING in the other, which is how
#: those two kept the swallowing shape through two repair rounds (review F3).
#:
#: A fixed list is what went blind the third time: `test_falkordb_compat.py`
#: resolves `_LIVE_AVAILABLE` and was graded by nothing, so the class survived a
#: round that claimed to close it. The rule is the SUFFIX — any flag that ends in
#: `AVAILABLE` is an availability decision, whatever it is prefixed with.
_AVAILABILITY_FLAGS = frozenset({"FALKORDB_AVAILABLE", "_AVAILABLE"})
_AVAILABILITY_SUFFIX = "AVAILABLE"
_UNAVAILABLE_SUFFIX = "UNAVAILABLE"


def _is_availability_flag(name: str) -> bool:
    """Whether `name` is an availability FLAG.

    `*_UNAVAILABLE` is not one: `X_UNAVAILABLE = _probe()` and
    `EMBEDDER_UNAVAILABLE_REASONS` are the opposite decision, and
    `"UNAVAILABLE".endswith("AVAILABLE")` is True — so the suffix rule has to
    exclude the negation explicitly or it grades a "cannot use it" flag as a
    "can use it" flag.
    """
    if name.endswith(_UNAVAILABLE_SUFFIX):
        return False
    return name in _AVAILABILITY_FLAGS or name.endswith(_AVAILABILITY_SUFFIX)


def _names_availability(targets: list[ast.expr]) -> bool:
    """Whether any target binds an availability flag, including in a tuple target
    (`FALKORDB_AVAILABLE, _WORKING_URI = _probe([…])`)."""
    for target in targets:
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and _is_availability_flag(node.id):
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


def _skipif_conditions(tree: ast.Module) -> list[ast.expr]:
    """Every `skipif`/`skip` condition expression in the module.

    This is the SECOND place availability is resolved at import time, and it is
    not a flag at all: `@pytest.mark.skipif(not _falkordb_available(), …)` calls
    the probe from a decorator, and `LIVE = _falkordb_available()` feeds one from
    a bare name. Both decide a SKIP at collection, so both are the class-2
    decision — and a census keyed only on an assignment graded NEITHER, which is
    how three more probes kept the swallowing shape (measured: two
    `_falkordb_available()` helpers and one `LIVE =` family).
    """
    out: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name in {"skipif", "skip"}:
            out.append(node.args[0])
    return out


def _skip_condition_names(tree: ast.Module) -> set[str]:
    """Every NAME a skip condition reads — whatever the flag is spelled.

    `skipif(not LIVE)` and `skipif(not _FTS_ESCAPE_LIVE)` are the same decision as
    `skipif(not FALKORDB_AVAILABLE)`; a census keyed on the spelling graded the
    first two as nothing (review of 9be4923 / b2ef7b0, P1).
    """
    out: set[str] = set()
    for condition in _skipif_conditions(tree):
        out |= {node.id for node in ast.walk(condition) if isinstance(node, ast.Name)}
    return out


def _skip_guard_call_names(tree: ast.Module) -> set[str]:
    """Names CALLED in an `if`/`assert` guard whose branch SKIPS.

    `uri = _live_target()` … `if uri is None: pytest.skip(...)` is a probe whose
    sentinel decides the skip — a shape neither a flag nor a `skipif` condition
    reaches (review of 76abdca, P2: `tests/test_9226_dump_paging_v6.py`).
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.Assert)):
            continue
        body = getattr(node, "body", []) or []
        orelse = getattr(node, "orelse", []) or []
        skips = any(
            isinstance(sub, ast.Call)
            and getattr(sub.func, "attr", None) in {"skip", "skipif", "xfail"}
            for stmt in list(body) + list(orelse)
            for sub in ast.walk(stmt)
        )
        if not skips:
            continue
        for sub in ast.walk(node.test):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if name:
                    out.add(name)
    # …and the same guard one hop away: `uri = _live_target()` … `if uri is
    # None: pytest.skip(…)`. The sentinel is the decision, and the helper that
    # produced it is the probe.
    guarded: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        skips = any(
            isinstance(sub, ast.Call)
            and getattr(sub.func, "attr", None) in {"skip", "skipif", "xfail"}
            for stmt in list(node.body) + list(node.orelse)
            for sub in ast.walk(stmt)
        )
        if skips:
            guarded |= {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
    for stmt in ast.walk(tree):
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = [
            n.id
            for target in _availability_targets(stmt)
            for n in ast.walk(target)
            if isinstance(n, ast.Name)
        ]
        if not set(targets) & guarded:
            continue
        value = getattr(stmt, "value", None)
        if value is None:
            continue
        for node in ast.walk(value):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                out.add(node.func.id)
    return out


def _skip_gating_functions(tree: ast.Module) -> set[str]:
    """Module-level helpers whose value decides a SKIP at import/collection.

    Three shapes, all seen in this tree: called directly in the condition
    (`skipif(not _falkordb_available())`), assigned to a name the condition reads
    (`LIVE = _falkordb_available()` … `skipif(not LIVE)`), or called in an
    `if`/`assert` guard whose branch skips (`if _live_target() is None: skip`).
    """
    names: set[str] = set(_skip_guard_call_names(tree))
    referenced: set[str] = set()
    for condition in _skipif_conditions(tree):
        for node in ast.walk(condition):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
    for stmt in tree.body:
        targets = [
            node.id
            for target in _availability_targets(stmt)
            for node in ast.walk(target)
            if isinstance(node, ast.Name)
        ]
        if not set(targets) & referenced:
            continue
        value = getattr(stmt, "value", None)
        if value is None:
            continue
        for node in ast.walk(value):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


_SKIP_CALLS = frozenset({"skip", "skipif", "xfail"})


def _own_scope_nodes(fn: ast.AST) -> list[ast.AST]:
    """Every node in THIS function's own scope, not its nested definitions'.

    A nested `def`/`lambda` binds its own names, so a nested parameter or local
    merely sharing a sentinel's name must not decide the OUTER probe's skip — and
    that nested def is visited by its own turn of the `ast.walk(tree)` loop, so
    nothing is lost by stopping here (review of `cd75ec7144`, P2).
    """
    out: list[ast.AST] = []
    stack: list[ast.AST] = [fn]
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)
    return out


def _skip_call_read_names(fn: ast.AST) -> set[str]:
    """Names that DECIDE a skip inside this function.

    `_skip_condition_names` answers the module-DECORATOR half (`skipif(not LIVE)`
    above a `def`), and `_skip_gating_functions` answers the "module-level helper
    that produces the flag" half. Neither reaches a probe inside a `def test_…`
    body that decides its own skip.

    ⛔ TWO SHAPES, AND THE GUARD IS THE ONE THAT MATTERS (review of `cd75ec7144`,
    P1). The first version of this helper read only the skip call's ARGUMENTS, so a
    sentinel carried by a GUARD —

        if not dropped:
            pytest.skip("engine has no Point fulltext drop procedure")

    — contributed NOTHING, while the decision plainly reads `dropped`. Keying on
    arguments alone closed the residual only for skips whose message INTERPOLATES
    the sentinel, and FIVE live probes of exactly this class stayed ungraded
    (`tests/test_indexes.py:1422`, `tests/test_11682_quote_indexed.py:438`, the
    drop-procedure probe in
    `test_a_pre_existing_array_quote_is_flattened_by_the_sweep` in that same file,
    `tests/test_session_semantic_retrieval.py:298`, and
    `tests/test_extractor_reliability.py:898` — the last admitted because its handler
    ASSIGNS its guard's sentinel). The drop-procedure probe is named by
    its enclosing test rather than by a line, because it was cited as `:651` and
    the four re-raise lines `#14129` added moved its `try` — a bare `file:line` is a
    stale-by-design pointer, and a third number would rot the same way (review of
    this PR, non-blocking observation). The guard half is the
    reading `_skip_guard_call_names` already applies at module level; this is the
    same rule scoped to one function.

    Scope-aware on purpose (`_own_scope_nodes`): a name in a NESTED function is
    that scope's decision, not this one's.

    ⛔ RESIDUAL (review P2) — BOTH HALVES match the ATTRIBUTE spelling only
    (`pytest.skip`, `pytest.xfail`). A `from pytest import skip` re-import makes the
    call an `ast.Name`, so a `try` whose skip uses the bare form reads `set()` here
    and is not admitted. `_skip_guard_call_names` carries the SAME `.attr`-only
    limit, and it is the helper this one now mirrors. Declared rather than widened
    here, and MEASURED before it was claimed: admitting the bare form at EVERY
    detection site — `_skip_call_read_names`, and both skip detections of
    `_skip_guard_call_names` — moves the census by ZERO on `72c104bcbb`: 70 rows /
    62 roots / 58 files unchanged, no new rows, `UNARMED == ()`, import-seam reds
    `()`, `_UNGRADABLE == []`. So the widening owes a WITNESS ROW (a separate
    spelling needs its own assertion — nothing here grades
    `from pytest import skip`) and NO population re-pin. The zero is the point: a
    widening that moves no row cannot be witnessed by the census, so a later lane
    must not read the unchanged pin as evidence the bare form is already covered.
    """
    out: set[str] = set()
    for node in _own_scope_nodes(fn):
        if isinstance(node, (ast.If, ast.Assert)):
            body = list(getattr(node, "body", []) or [])
            orelse = list(getattr(node, "orelse", []) or [])
            skips = any(
                isinstance(sub, ast.Call)
                and getattr(sub.func, "attr", None) in _SKIP_CALLS
                for stmt in body + orelse
                for sub in ast.walk(stmt)
            )
            if skips:
                out |= {
                    sub.id for sub in ast.walk(node.test) if isinstance(sub, ast.Name)
                }
            continue
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) not in _SKIP_CALLS:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            out |= {sub.id for sub in ast.walk(arg) if isinstance(sub, ast.Name)}
    return out


def _control_flow_tries(body: list[ast.stmt]) -> list[ast.Try]:
    """The `try` blocks in a helper body, descending through control flow.

    A probe's guard is not always a DIRECT child of its helper: the two
    `_AVAILABLE, _WORKING_URI = _probe([…])` families put it inside
    `for uri in candidates:`, so reading only `stmt.body` graded nothing there
    and their missing arm was invisible to the census (review F3(b)).

    Stops at a NESTED `try` THAT HAS HANDLERS: a `try: _proj.close() / except
    Exception: pass` cleanup is not the availability decision, and it is graded
    on its own pass where it is a root's descendant.

    It does NOT stop at a handler-less `try/finally`, because that is a pure
    WRAPPER, not a decision — and a probe inside one was invisible for exactly
    that reason: `tests/test_integration_search.py:45` puts its availability
    probe under `try: … finally: restore TORTOISE_DB_URI`, so stopping at the
    wrapper returned NO row for a module-level probe that
    `_skip_condition_names(tree)` already bound (`FALKORDB_AVAILABLE`), i.e. by
    the census's own rule it IS the decision. Same hole as the `if _DOCKER_URI:`
    one one level out (a control-flow node it failed to descend), and the same
    consequence: a de-armed probe behind it reads GREEN (review of 3ce7ffdd867).
    """
    out: list[ast.Try] = []
    stack: list[ast.stmt] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Try):
            if node.handlers:
                out.append(node)
            else:
                # `try/finally`: descend the GUARDED region (and no further).
                # `finalbody` is the restoration arm, never the probe.
                stack.extend(node.body)
            continue
        if isinstance(node, (ast.If, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor,
                             ast.While)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []) or [])
        elif isinstance(node, ast.ExceptHandler):
            stack.extend(node.body)
    return out


def _bound_names(node: ast.AST) -> set[str]:
    """Every NAME this block BINDS, in its body, handlers or `finally`."""
    out: set[str] = set()
    for sub in ast.walk(node):
        targets = (
            sub.targets
            if isinstance(sub, ast.Assign)
            else [sub.target]
            if isinstance(sub, ast.AnnAssign)
            else []
        )
        for target in targets:
            out |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    return out


def _own_imported_names(nodes: list[ast.stmt]) -> set[str]:
    """Names bound by OWN-code imports anywhere in these statements."""
    out: set[str] = set()
    for stmt in nodes:
        for node in ast.walk(stmt):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and _is_own_code_import(node):
                out |= {(alias.asname or alias.name.split(".")[0]) for alias in node.names}
    return out


def _non_own_imported_names(nodes: list[ast.stmt]) -> set[str]:
    """Names bound by imports that are NOT own code — third-party or stdlib.

    The mirror of ``_own_imported_names``, and ONE OF THE TWO INPUTS to the evidence
    that keeps ``_CAPABILITY_CALLS``' generic attribute (``query``) from grading an
    OPTIONAL EXTRA as this repository's seam: `import some_optional_dep` …
    `some_optional_dep.query(…)` binds its receiver HERE, so the attribute call is
    a third-party client's verb, not a door a rename in this repo can move. It is an
    INPUT and not the evidence, because this set answers "which names did a non-own
    import bind ANYWHERE", which is a NAME and not a BINDING:
    ``_third_party_receivers`` subtracts every re-bound name from it, and THAT is the
    set ``_reaches_capability_seam`` is given. A receiver bound any other way — a
    PARAMETER (`proj.g.query`), or a client built at module scope
    (`PROJ = _make_proj()`) — is in neither set and stays graded.
    """
    out: set[str] = set()
    for stmt in nodes:
        for node in ast.walk(stmt):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not _is_own_code_import(node):
                out |= {(alias.asname or alias.name.split(".")[0]) for alias in node.names}
    return out


def _third_party_receivers(tree: ast.Module) -> set[str]:
    """Third-party import names that still DENOTE that import where a probe uses them.

    ``_non_own_imported_names`` answers "which names did a non-own import bind
    ANYWHERE in this file", and the narrowing used that answer directly. That is a
    NAME, not a BINDING: ``import falkordb`` at the top of the module and
    ``falkordb = proj`` inside the probe leaves the name holding OUR client, while the
    exemption still skipped the ``.query`` call on it. Measured: that shape was graded
    at ``994fb1d52f`` and UNGRADED from ``61fc96cd`` on — the narrowing's own
    regression (two fresh reviewers, same finding; control unchanged on both heads).

    So an imported name that is re-bound where the probe can SEE it is not evidence of
    the import at a call site and buys no exemption:

    ⛔ "RE-BOUND" IS PYTHON'S OWN LIST OF BINDING CONSTRUCTS (Language Reference §4.2.1),
    NOT A SUBSET OF IT: the ``ast.Name`` Store contexts (assignment, walrus, ``for``,
    ``with … as``, and ``type X = …``), the ``ast.Name`` **Del** context (``del name``,
    which §4.2.1 counts as bound), FORMAL PARAMETERS and TYPE PARAMETERS of a function
    that HOLDS a probe, ``def`` / ``class`` names, ``except`` / ``except*`` ``as``
    targets, and the structural-pattern captures ``MatchAs`` / ``MatchStar`` /
    ``MatchMapping.rest``. Only the Store/Del contexts are ``ast.Name``; the rest are
    ``ast.arg`` or plain ``str`` fields, so a walk that read only ``ast.Name`` missed
    every other family.

    ⛔ ONE §4.2.1 CONSTRUCT IS DELIBERATELY NOT COLLECTED, and this is the exception the
    sentence above has to carry or it is literally false (review of this PR, P3): an
    IMPORT STATEMENT binds (``import x``, ``import x as y``, ``from p as q``), yet a walk
    that subtracted it would make ``import torch`` cancel the ``torch`` exemption — the
    over-block ``889affc6`` reverted, because an import's right-hand side is always an
    import and re-importing the name is not a rebind away from it (see
    ``_function_local_bindings``, and row ``4j-sexdecies``, which reds if alias
    collection comes back).

    ⛔ TWO OF THEM BIND IN THEIR OWN SCOPE, so WHICH WALK collects them is decided by
    scope, not node kind: a comprehension's iteration target binds in the comprehension
    and a type parameter binds in the generic's own body — both are unreachable from a
    flat module-wide walk, and a "complete the list of node kinds" fix still gets them
    wrong (see ``_collect_binding``, ``_module_scope_bindings``, ``_type_param_names``).

    ⛔ AND THE SCOPE IS AS LOAD-BEARING AS THE LIST (review P2): a binding only cancels
    the exemption when it can REACH the probe. Subtracting every binding construct
    anywhere made an unrelated ``def _helper(torch)`` parameter — or a ``for torch in
    range(3)`` in some other function — cancel the exemption for a legitimate
    module-level ``torch.query(…)`` probe, turning it into a graded ROOT: a false red of
    exactly the class ``4k-``/``4l-`` forbid. Measured on that scope-blind version
    ``((6, (), True, False, True),)``, scoped ``()``. An import binds through its ALIAS,
    so ``import x`` alone never removes ``x``, and the parameter / passed-in-client
    witnesses are unaffected because those receiver names are not in the import set.

    ⛔ THE SCOPE APPLIES TO THE ``str``-FIELD FAMILIES TOO (review P2): collecting the
    capture families with ``ast.walk(tree)`` exempted nothing less than the scope-blind
    version did for Store targets — an ``except Exception as torch:`` in an UNRELATED
    helper cancelled the exemption for a legitimate module-level ``torch.query(…)``
    probe, the same false red. Every family is now collected by a SCOPED walk, so the
    direction claim is uniform: a term can only be subtracted where it can reach the
    probe. The scopes are the module
    (``_module_scope_bindings``) and, per probe, the FUNCTION chain that lexically
    encloses it (``_enclosing_bindings`` → ``_function_scope_bindings``: parameters,
    type parameters and locals, up the closure). Per probe, not per module, because one
    module-wide set holds a neighbouring function's locals too and was measured to red
    its legitimate third-party probe (review of #14160, P2).

    ⛔ REACH IS RESOLVED AT THE CALL NODE, SO THERE IS NO SEPARATE "the block rebinds it"
    TERM — and that is a REFUTATION of two earlier judgements on this branch, both of
    which measured a real symptom and drew the wrong conclusion from it:

      * a ``_rebinds_within(block, name)`` clause was added because a rebind inside a
        ``def`` NESTED in the block was exempt. True — but the fix was at the wrong
        granularity: ``ast.walk(block)`` also sees every NESTED scope, so
        ``except Exception as torch`` inside a nested ``def`` cancelled the exemption for
        a call at BLOCK level that it cannot reach (§4.2.2) — the ``4k-``/``4l-`` false
        red, and one case a REGRESSION against ``origin/main``.
      * it was then deleted as "DEAD, unwitnessed" because no row red. Also wrong: the
        term was load-bearing for the nested-``def`` shape, so the deletion re-opened it
        (measured ``()`` exempt).
      * so it was restored with a witness — and BOTH judgements were still at the wrong
        granularity, which is what this tree now fixes.

    The single rule is: a name is exempt only where it still denotes the import **at the
    call site** (§4.2.2 — a binding reaches every use in the SAME block). ``_reaches_
    capability_seam`` resolves ``_enclosing_bindings(node, parents)`` from the CALL NODE,
    so a nested scope contributes exactly to the calls inside it and to nothing else.
    That one rule closes the fail-open shape AND both false reds, and it is why no
    "the block rebinds it" term exists to be patched.
    """
    bound = _non_own_imported_names(tree.body)
    if not bound:
        return set()
    #: MODULE scope only. A binding inside a FUNCTION cannot be expressed here — it
    #: reaches a probe in that function and, through the closure chain, in any nested
    #: one, and NOWHERE else — so it is subtracted per PROBE by `_enclosing_bindings`
    #: at each `_reaches_capability_seam` call site. Returning one module-wide set that
    #: had already folded every probe-holding function's locals in was measured to red a
    #: legitimate third-party probe in a neighbouring function (review of #14160, P2).
    return bound - (_module_scope_bindings(tree) | _global_scope_bindings(tree))


def _global_scope_bindings(tree: ast.Module) -> set[str]:
    """Names a `global` declaration binds into MODULE scope (Language Reference §4.2.2).

    *"If the `global` statement occurs within a block, all uses of the names specified in
    the statement refer to the bindings of those names in the top-level namespace … The
    `global` statement has the same scope as a name binding operation in the same
    block."* So `global torch; torch = _make_client()` inside ANY function rebinds the
    MODULE-level name and reaches a module-level probe — while `_module_scope_bindings`
    never descends a function body and `_enclosing_bindings` holds only the call's own
    chain, so no other walk sees it (review of #14160, P2).

    ⛔ THREE GRANULARITIES, AND ONLY THE LAST IS RIGHT. (1) A TREE-WIDE `declared & bound`
    let an inert `global torch` borrow ANY binding of that name anywhere — another
    function's loop target, an `except … as`, a plain LOCAL that never touches module
    scope. False red. (2) A scope-keyed walk fixed that but charged the declaring function
    with COMPREHENSION and CLASS-BODY bindings, which live in their own scopes (false red
    again), while missing the families that DO bind in the declaring block — a nested
    `def`/`class` NAME and a header walrus, which are `str` fields and annotations rather
    than `ast.Name` stores. Fail-open. (3) So the pair is resolved PER FUNCTION, through
    the walks that already know one scope's own bindings: `_global_declarations` (the
    `global` names declared in that function's own scope, never a nested one's) ∩
    `_function_local_bindings` (that function's own bindings — comprehension targets and
    nested bodies excluded, a nested `def`'s NAME and header expressions included). A
    nested function's `global` is its own declaration and gets its own turn.
    """
    out: set[str] = set()
    for scope in ast.walk(tree):
        #: A CLASS BODY CAN DECLARE `global` TOO, and CPython honours it:
        #: `class C: global torch; torch = _make_client()` rebinds the MODULE name
        #: (measured). It was invisible
        #: here because only FUNCTION nodes were visited, so a module-level probe stayed
        #: EXEMPT over OUR client — the same fail-open family as the function case, one
        #: node kind over (final review of #14160, P2; 0 live instances in the tree).
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        declared = _global_declarations(scope)
        if declared:
            out |= declared & _function_local_bindings(scope)
    return out


def _global_declarations(func: ast.AST) -> set[str]:
    """`global` names declared in THIS function's own scope — not a nested def's.

    A nested function's `global` is that function's own declaration and is resolved on its
    own turn; folding it in here would let an inert `global x` in one scope borrow a
    binding from another, which is the false red `_global_scope_bindings` avoids.
    """
    out: set[str] = set()
    stack: list[ast.AST] = list(getattr(func, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Global):
            out.update(node.names)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _module_scope_bindings(tree: ast.Module) -> set[str]:
    """Names bound at MODULE scope — never descending into a function or class body.

    `if`/`for`/`while`/`try`/`with` at module level stay module scope and ARE
    descended; a `def`/`class` NAME binds here while its BODY is another scope, and a
    `lambda` body is another scope entirely.

    ⛔ THE HEADER OF A `def`/`class` EVALUATES HERE, so it is walked before the body is
    left behind: a decorator, a base, a class keyword and a function DEFAULT all evaluate
    in the ENCLOSING scope, and one carrying a walrus binds its target there —
    `@_reg(torch := _make_client())` rebinds the module's `torch`. Not descending was the
    same silent drop the body skip avoids (review of #14160, P2). `_function_local_bindings`
    already did this for a nested def and class; the module half did not, and that
    asymmetry was the hole.
    """
    out: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)  # the name binds HERE; the body is a different scope
            stack.extend(node.decorator_list)
            stack.extend(_header_expressions(node))
            continue
        if isinstance(node, ast.Lambda):
            # the BODY is another scope; the DEFAULT expressions evaluate HERE
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.AnnAssign) and node.value is None:
            # ⛔ A BARE ANNOTATION AT MODULE SCOPE BINDS NOTHING. `torch: object` only
            # records an annotation — it is not a binding operation, so an earlier
            # `import torch` still denotes itself afterwards (and with NO earlier binding
            # the read is a `NameError`, measured) — but its TARGET is an `ast.Name` with
            # `Store` context, so the generic walk counted it and a legitimate third-party
            # probe was graded: a 4k-/4l- false red
            # (review of #14160, cycle 5). The annotation is still walked, because a walrus
            # in it IS evaluated here. In a FUNCTION the same line DOES make the name local
            # (`UnboundLocalError`, measured), so `_function_local_bindings` keeps counting
            # it — the rule is scope-dependent, not spelling-dependent.
            stack.append(node.annotation)
            continue
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # A comprehension has its OWN scope in Python 3, so its ITERATION TARGETS do
            # not bind in the enclosing one: `_ = [torch for torch in range(3)]` leaves
            # an outer `torch` intact (verified). Descending it here made that line
            # cancel the exemption for a module-level `torch.query` probe — a false red
            # (review of #14160, P1). A WALRUS inside it is the exception PEP 572 names:
            # `:=` binds in the CONTAINING scope, so `_ = [(torch := c) for _ in r]`
            # DOES rebind. Taking the targets and not the walrus was a silent drop
            # (review P2).
            out |= _walrus_targets(node)
            continue
        _collect_binding(node, out)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _collect_binding(node: ast.AST, out: set[str]) -> None:
    """Add the name this ONE node binds, for every binding family §4.2.1 lists.

    Two of those families are plain ``str`` fields and unreachable from an
    ``ast.Name`` walk: ``ExceptHandler.name`` (``except E as name`` / ``except*``) and
    the structural-pattern captures ``MatchAs.name`` / ``MatchStar.name`` /
    ``MatchMapping.rest``. Both walks that collect bindings — module scope and a
    probe-holding function — call this, so neither can silently miss a family the other
    has.

    ⛔ ``del`` IS A BINDING CONSTRUCT, AND NOT A ``Store`` ONE (§4.2.1: "a target
    occurring in a ``del`` statement is also considered bound"). Its targets are
    ``ast.Name`` with ``Del`` context, so a walk that read ``Store`` ALONE left
    ``del some_optional_dep`` buying the exemption for a ``.query`` that can no longer
    reach the import — the silent drop this narrowing exists to forbid, one context
    flag over (measured: ``()`` exempt before, graded after).

    ⛔ TWO §4.2.1 FAMILIES ARE NOT COLLECTED BY THIS HELPER, and neither is an
    omission: where they bind is decided by SCOPE, so each is collected where its own
    scope's USE is resolved, not by a flat walk. A COMPREHENSION's iteration target
    binds in the comprehension's own scope, so BOTH the module walk and the
    probe-holding-function walk refuse it, and `_enclosing_bindings` collects it at the
    CALL NODE — the only granularity that gets both directions right: a call INSIDE such
    a comprehension is graded (`((4, (), True, False, True),)` for
    `[torch.query(…) for torch in clients]` over a non-own import), while a call AFTER
    it stays exempt (`()`). An earlier version of this sentence credited
    ``_module_scope_bindings`` with the collection, and NO walk collects it there — that
    walk SKIPS the target and keeps only the walrus, which binds in the CONTAINING scope
    (review of this PR, P2). A TYPE PARAMETER LIST — ``def f[T](…)``,
    ``class C[T]``, ``type X[T] = …`` — binds in the GENERIC's scope and IS visible in
    its body (``f.__code__.co_freevars == ('T',)``, measured on 3.12.3), so it is
    collected per function by ``_type_param_names``. A flat node-kind walk cannot
    express either place a scope owns, which is why "complete the list of node kinds" is
    the wrong fix.
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        out.add(node.id)
    elif isinstance(node, ast.ExceptHandler) and node.name is not None:
        out.add(node.name)
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
        # `case name:` / `case [*name]:` (`None` for the wildcards `_` / `[*_]`)
        out.add(node.name)
    elif isinstance(node, ast.MatchMapping) and node.rest is not None:
        # `case {**rest}:`
        out.add(node.rest)




def _type_param_names(node: ast.AST) -> set[str]:
    """Names a PEP 695 TYPE PARAMETER LIST binds — `def f[T](…)`, `class C[T]`, `type X[T]`.

    ⛔ THE BODY CAN SEE THEM, AND THAT WAS MEASURED, NOT ASSUMED. An earlier version of
    this block EXCLUDED the family from the walks on the belief that a type parameter
    lives only in an annotation scope and so cannot shadow an import. False on 3.12.3:
    with `T = 'module-T'` at module level, `def f[T](x): return T` returns a `TypeVar`,
    and `f.__code__.co_freevars == ('T',)` with `co_names == ()` — the body reads the
    TYPE PARAMETER as a free variable of the annotation scope, and a generic class body
    does the same. (The probe that produced the false belief PRINTED the return value,
    whose repr is the type parameter's own name `T`, so the TypeVar looked like the
    module's string.) So a `def _probe[torch](…)` body calling `torch.query(…)` is NOT
    calling the import, and the exemption must not survive it.

    Scoping: a type parameter reaches the generic's own body and its nested functions'
    (via the closure chain) — exactly the scope `_function_scope_bindings` describes —
    and nothing outside it, which is why this is collected per function and never into
    the module-wide set.
    """
    out: set[str] = set()
    for param in getattr(node, "type_params", ()) or ():
        name = getattr(param, "name", None)
        if isinstance(name, str):
            out.add(name)
    return out


def _function_scope_bindings(fn: ast.AST) -> set[str]:
    """Every name ONE function scope binds: its parameters, type parameters and locals.

    This is the unit of REACH. A probe inside `fn` (or inside a function nested in `fn`,
    through the closure) cannot trust a name this set holds, so the third-party
    exemption is cancelled for it there — and ONLY there.
    """
    out = _type_param_names(fn)
    args = getattr(fn, "args", None)
    if args is not None:
        out |= {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        out |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    out |= _function_local_bindings(fn)
    return out


def _enclosing_bindings(block: ast.AST, parents: dict[int, ast.AST]) -> set[str]:
    """Bindings in every scope that lexically encloses `block` — the closure chain.

    A local of an OUTER function reaches a probe in a nested one, so the chain is the
    reach, not the innermost `def` alone. Module scope is not here: it is subtracted
    once, module-wide, by `_third_party_receivers`.

    ⛔ A CLASS BODY IS TWO RULES, NOT ONE (§4.2.2): "the scope of names defined in a
    class block is limited to the class block" — so a class-body binding reaches a use
    DIRECTLY in that block (including a `try` in it) and does NOT reach a method body.
    Only the second half used to be implemented, so `class C: torch = proj` …
    `torch.query(…)` inside the class body read EXEMPT over OUR client — a fail-open
    the call-node refactor introduced (`3b7f3e57d426` graded it; measured `()` at the
    head of #14160 when this was fixed, `((5, (), True, False, True),)` after).
    `crossed_callable` is what separates the halves: a `def`/`lambda` between the call
    and the class means the class-body names cannot reach it, which is why the two
    METHOD controls stay `()`.

    ⛔ A COMPREHENSION IS A BARRIER FOR THE CLASS HALF ONLY, and the asymmetry is
    Python's, not a convenience: a comprehension (or genexp) in a CLASS BODY cannot see
    the class-body names — `class C: torch = object(); rows = [torch for _ in …]` raises
    `NameError` (measured on this interpreter) — while a comprehension inside a FUNCTION
    DOES see that function's locals (`rows = [torch for _ in range(1)]` with `torch` a
    function local works). So `crossed_comprehension` suppresses only the class-body
    contribution and never a function scope's (final review of #14160, P2: the first
    version graded the class-body shape, a false red of the `4k-`/`4l-` class).
    """
    out: set[str] = set()
    crossed_callable = False
    crossed_comprehension = False
    #: Every node between the call and the current ancestor, the call itself included. A
    #: generator's ITERABLE is evaluated one scope out, so its own target is not in scope
    #: there — and that is decided by WHERE the call sits inside the comprehension, which
    #: only this path knows.
    seen: set[int] = {id(block)}
    previous = block
    node = parents.get(id(block))
    while node is not None:
        seen.add(id(node))
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ) and _is_header_child(node, previous):
            # ⛔ A CALL IN THE HEADER IS NOT IN THE DEFINITION'S SCOPE. Decorators, bases,
            # keywords, defaults and annotations all evaluate in the ENCLOSING scope — the
            # same direction `_header_expressions` implements for the WALRUS — so charging
            # a header call with the definition's OWN bindings is a false red: measured,
            # `try: def _inner(x=torch.query(…)): torch = _make_client()` and
            # `try: class C(torch.query(…)): torch = _make_client()` were both graded over
            # a legitimate probe. The node is skipped and the ascent continues outward.
            # ⛔ EXCEPT ITS TYPE PARAMETERS, WHICH REACH ONLY THE PARTS OF THE HEADER THAT
            # LIVE IN THE TYPE-PARAMETER SCOPE. PEP 695 gives a header THREE scopes, and
            # only one of them can see the list's own names — measured on 3.12.3, and the
            # list is tagged where a member is newer than that interpreter:
            #   * TYPE-PARAMETER scope: the `bound` of the list itself (and its PEP 696
            #     `default_value`, which is a 3.13 SyntaxError here, so it is routed — never
            #     measured — by the whole `type_params` child), a
            #     parameter/return ANNOTATION, a class BASE and a class KEYWORD. Here a
            #     type parameter named after the import shadows it — `def f[T, S: T]()`
            #     gives `f.__type_params__[1].__bound__ is T`, and in
            #     `def _inner[torch: object, T: torch.query("…")]()` the call resolves to
            #     the TypeVar. (⛔ NOT AT DEFINITION TIME: bounds are LAZY — the `def`,
            #     `class` and `type` spellings all `exec` fine, and the `AttributeError`
            #     arrives when the bound is EVALUATED.)
            #   * ENCLOSING scope: DECORATORS (function and class) and a function's
            #     DEFAULTS — `@deco(W) def f[W: object]()` and `def f[W: object](x=deco(W))`
            #     both see the MODULE `W`, not the type parameter.
            # Contributing the type parameters to the whole header therefore graded a
            # legitimate probe over the import — a regression against `origin/main` on the
            # decorator and default shapes (review of this PR, P1). The contribution is
            # scoped to the children that actually evaluate there.
            if not _header_child_is_enclosing_scope(node, seen):
                out |= _type_param_names(node)
            previous = node
            node = parents.get(id(node))
            continue
        #: A `lambda` is a function scope too: its parameters are invisible to every
        #: other walk (they are `ast.arg`, and the module walk skips a lambda body by
        #: construction), so a call inside one must see them here or the exemption
        #: survives over OUR client (`4j-duodecies`).
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            out |= _function_scope_bindings(node)
            crossed_callable = True
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # ⛔ A COMPREHENSION SCOPE REACHES A CALL INSIDE IT. Its iteration targets bind
            # in the comprehension and nowhere else — which is why
            # `_module_scope_bindings` and `_function_local_bindings` both refuse them,
            # right for a call AFTER the comprehension and WRONG for a call inside one. So
            # `[torch.query(…) for torch in clients]` kept the exemption over OUR client
            # while main's scope-blind sweep graded it: a fail-open regression against
            # `origin/main` (review of #14160, cycle 4).
            for generator in node.generators:
                # ⛔ BUT NOT ITS OWN ITERABLE, which is evaluated one scope OUT: the first in
                # the ENCLOSING scope, a later one in the comprehension scope where the
                # earlier targets are bound — never with its own target bound. Measured:
                # `[None for clients in clients.query()]` resolves `clients` from the
                # enclosing scope (with a `C.query()` returning `[1,2,3]` the result is
                # `[None, None, None]`), and an iterable that is unbound is a `NameError`.
                # So a call inside `gen.iter` must NOT see `gen.target`; `elt`, the `ifs`
                # and a LATER generator's iterable do see the earlier targets.
                # Subtracting all of them graded a call in the outermost iterable — a
                # 4k-/4l- false red (review of #14160, cycle 5).
                if id(generator.iter) in seen:
                    break
                out |= {
                    sub.id for sub in ast.walk(generator.target) if isinstance(sub, ast.Name)
                }
            # ⛔ AND THE CLASS HALF IS THE OPPOSITE: a comprehension in a CLASS BODY cannot
            # read the class-body names — `class C: torch = object(); rows = [torch for
            # _ in …]` raises `NameError` (measured on this interpreter), while the same
            # comprehension inside a FUNCTION does see that function's locals. So the flag
            # set here suppresses ONLY the class-body contribution below, never a function
            # scope's (final review of #14160, P2).
            crossed_comprehension = True
        elif isinstance(node, getattr(ast, "TypeAlias", ())):
            # ⛔ `type Y[torch] = torch.query(…)` — THE TYPE PARAMETER IS BOUND IN THE VALUE.
            # Measured on 3.12.3: with `torch` a module-level import, the alias's
            # `__value__` raises `AttributeError: 'typing.TypeVar' object has no attribute
            # 'query'`, so the receiver resolved to the TypeVar and not the import. The
            # docstring named this spelling while only the def/class halves were collected,
            # so the alias form was a fail-open hole (review of #14160, cycle 6, P2-4).
            out |= _type_param_names(node)
        elif isinstance(node, ast.ClassDef):
            # ⛔ A GENERIC CLASS'S TYPE PARAMETER REACHES ITS METHODS. PEP 695: a class type
            # parameter "is valid within the class body and inner scopes contained
            # therein" — measured on 3.12.3, `class C[T]: def m(self): return T` gives
            # `C.m.__code__.co_freevars == ('T',)`. `_type_param_names` was reachable only
            # through `_function_scope_bindings`, so a `ClassDef` never entered the shadow
            # set and `class C[torch]: def probe(self): torch.query(…)` kept the
            # exemption — the silent drop, one node kind over (review of #14160, P1).
            out |= _type_param_names(node)
            if not crossed_callable and not crossed_comprehension:
                # the OTHER half: names the class body binds reach a use in that same
                # block. A nested `def`/`class` contributes its NAME but not its body,
                # which is exactly the descent `_function_local_bindings` implements.
                out |= _function_local_bindings(node)
            crossed_callable = True
        previous = node
        node = parents.get(id(node))
    return out


def _function_local_bindings(func: ast.AST) -> set[str]:
    """Every name a FUNCTION's own body binds — never descending into a NESTED scope.

    A nested `def`/`class` binds its NAME in this function's scope, but its BODY is
    another scope, and a `lambda` body (or a comprehension's iteration target) is
    another scope entirely: a local there cannot shadow the name this function's probe
    reads, so descending into it cancels the exemption for a legitimate third-party
    probe — the same false red the module-scope walk avoids, one level in. Measured on
    the descending version `((6, (), True, False, True),)`; here `()`.

    ⛔ A WALRUS INSIDE A COMPREHENSION IS THE EXCEPTION (PEP 572): `:=` in a
    comprehension binds in the CONTAINING scope, so `_ = [(torch := c) for _ in r]`
    DOES rebind this function's `torch` while the iteration target `_` does not. Skipping
    the comprehension wholesale missed the walrus — a silent drop (review of #14160, P2).
    """
    out: set[str] = set()
    body = getattr(func, "body", [])
    #: A `lambda`'s body is a single EXPRESSION, not a statement list (`4j-duodecies`).
    stack: list[ast.AST] = [body] if isinstance(body, ast.AST) else list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)  # the name binds HERE; the body is a different scope
            # decorators, bases, keywords, defaults AND ANNOTATIONS evaluate in THIS scope
            stack.extend(node.decorator_list)
            stack.extend(_header_expressions(node))
            continue
        if isinstance(node, ast.Lambda):
            # the BODY is another scope; the DEFAULTS evaluate in this one
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            out |= _walrus_targets(node)
            continue
        #: ⛔ AN IMPORT STATEMENT IS NEVER A SHADOWING BINDING — not `import x`, not
        #: `import somedep as x`, not `from m import n as x`. The RIGHT-HAND SIDE OF AN
        #: IMPORT IS ALWAYS AN IMPORT, so after it the name still denotes an imported
        #: module/object, which is the exemption's premise — and `_non_own_imported_names`
        #: is NAME-keyed, so it holds the alias itself and the receiver is still that
        #: optional extra. The tempting test ("the alias binds a DIFFERENT name, so the
        #: name left the import set") is a FALSE RED of the `4k-`/`4l-` class: measured,
        #: `import falkordb as fdb` … `fdb.query(…)` in a helper went `()` at
        #: `3b7f3e57d426` to `((6, (), True, False, True),)` under it (a fresh reviewer
        #: measured the same direction on an earlier attempt and refused it). The OWN-CODE
        #: half needs no import rule at all: `import tortoise.sdk as torch` is graded by the
        #: own-import arm (`_touches_own_code`), measured graded before any of this. Rows
        #: `4j-quattuordecies` pin every direction.
        _collect_binding(node, out)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _header_expressions(node: ast.AST) -> list[ast.AST]:
    """The expressions in a `def`/`class` HEADER, which evaluate in the ENCLOSING scope.

    A decorator, a base, a class keyword, a default and an ANNOTATION all run before the
    new scope exists, so a walrus in any of them binds in the scope that ENCLOSES the
    definition. A header carrying no `:=` contributes nothing, which matters in the other
    direction too: a header with no walrus must NOT cancel the exemption (control row).

    ANNOTATIONS are the family only a careful reading of §4.2.1 finds: they are part of
    the header and evaluate eagerly in the enclosing scope (§4.2.4's lazy evaluation is a
    later, version-specific exception, not the rule), so `def _h(x: (torch := object()))`
    rebinds the module's `torch` — measured: `exec("def h(x: (W := 5)): pass")` leaves
    module `W == 5` (review of #14160, P2). A CLASS-BODY annotation is deliberately not
    collected: a class body is its own scope, not the enclosing one.
    """
    out: list[ast.AST] = []
    args = getattr(node, "args", None)
    if args is not None:
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if arg is not None and arg.annotation is not None:
                out.append(arg.annotation)
        out.extend(args.defaults)
        out.extend(d for d in args.kw_defaults if d is not None)
        returns = getattr(node, "returns", None)
        if returns is not None:
            out.append(returns)
    out.extend(getattr(node, "bases", ()) or ())
    out.extend(keyword.value for keyword in getattr(node, "keywords", ()) or ())
    # NOT a type parameter's bound / default_value: a named expression there is a
    # COMPILE-TIME SyntaxError ("named expression cannot be used within a TypeVar bound"),
    # so no module that imports can hold one — `ast.parse` accepting it would only let a
    # grader measure a shape that cannot exist (review of #14160, cycle 4).
    return out


def _is_header_child(node: ast.AST, child: ast.AST) -> bool:
    """Whether `child` sits in `node`'s HEADER rather than its body.

    A header expression evaluates in the ENCLOSING scope, so a call there is not resolved
    by the definition's own bindings — the two directions of one §4.2.1/§4.2.4 rule.
    """
    #: A default's expression reaches the `def` through an `ast.arguments` node and a
    #: class keyword through an `ast.keyword`, so the direct-child identity test alone
    #: misses the two largest families — measured, the default-header case stayed graded
    #: through a `previous` that was the `arguments` node.
    #: ⛔ AND NO `ast.arg` TERM. `ast.arg` used to sit in this tuple as a third member and
    #: it can never fire: an `ast.arg`'s only parent is `ast.arguments`, which is not a
    #: `def`/`class`/`lambda`, and this function is called for those three node kinds only
    #: — so it cannot receive an `ast.arg` as `child`. Measured, not inferred: an
    #: instrumented copy that logged every `ast.arg` arrival ran the ENTIRE graded corpus
    #: (this file's rows plus the 58-file probe census) with ZERO hits. A term that can
    #: never fire is not coverage, it is a CLAIM of coverage — the same defect this file
    #: filed against `_is_probe_container`, whose docstring said the self-test used it.
    #: Removing it changes no row, and that is exactly the point: no row could ever pin it.
    if isinstance(child, (ast.arguments, ast.keyword)):
        return True
    if any(expression is child for expression in getattr(node, "decorator_list", ()) or ()):
        return True
    #: A TYPE PARAMETER's `bound` / `default_value` is NOT in the class body's scope
    #: (PEP 695): `class C[T: torch.query(…)]: torch = …` defines fine, so that call
    #: resolved the MODULE import, not the class body — yet it was charged with the
    #: generic's own bindings. Measured on this interpreter rather than inferred from the
    #: prose: for `class C[T: torch]: torch = "CLASS-BODY-torch"` over a module-level
    #: `torch`, `C.__type_params__[0].__bound__` IS the enclosing `torch`, not the class
    #: body's — the bound never sees the body it is part of.
    #: `_header_expressions` excludes these fields for a different reason (a `:=` in a
    #: bound is a COMPILE-TIME SyntaxError, so it can bind nothing), and inheriting that
    #: exclusion into the REACH direction graded a legitimate probe (review of this PR,
    #: P3). The witness is the `"a type-parameter bound"` row of `4j-tervicesimus`: `()`
    #: at this head, and graded the moment this test is removed (revert it on a copy and
    #: the row comes back as exactly one entry — the probe's own `try`). ⛔ The row's LINE
    #: NUMERAL is deliberately not restated here: the first element of a row is the PROBE
    #: `try`'s line, so it moves with the template and the live value is already in the
    #: assertion message — a numeral written into a growing file is stale by construction,
    #: which is the defect this file has now falsified five times (see the
    #: block-granularity note, `4j-vicesimus-bis`).
    #: ⛔ AND THE SCOPE IS THE TYPE-PARAMETER ONE, NOT THE ENCLOSING ONE — which matters
    #: because the list's OWN names are in scope in a bound: measured, `def f[T, S: T]()`
    #: gives `f.__type_params__[1].__bound__ is T`, and in
    #: `def _inner[torch: object, T: torch.query("…")]()` the call resolves to the
    #: TypeVar. ⛔ AND THAT IS OBSERVED BY EVALUATING THE BOUND, NOT AT DEFINITION: PEP 695
    #: bounds are LAZY — `exec` of that shape succeeds for the `def`, the `class` and the
    #: `type` spelling (measured), and the `AttributeError` appears when `__bound__` is
    #: forced. So skipping the definition
    #: wholesale (what fixes the legitimate case) turned that silent drop into an
    #: exemption; `_enclosing_bindings` contributes `_type_param_names(node)` on the
    #: header-skip path instead, and the `def`/`class` spellings are pinned by their own
    #: rows (review of this PR, P2).
    #: The ascent reaches the definition with the TYPE-PARAMETER node as the child, not
    #: the bound expression itself, so the test is on that node's identity.
    if any(parameter is child for parameter in getattr(node, "type_params", ()) or ()):
        return True
    return any(expression is child for expression in _header_expressions(node))


def _header_child_is_enclosing_scope(node: ast.AST, seen: set[int]) -> bool:
    """Whether the header child the ascent reached evaluates in the ENCLOSING scope.

    A header has THREE scopes under PEP 695, and only two of them matter here: a
    DECORATOR (function or class) and a function's DEFAULTS are evaluated in the scope
    that HOLDS the definition, where the generic's type parameters are NOT bound —
    measured on 3.12.3, `@deco(W) def f[W: object]()` and `def f[W: object](x=deco(W))`
    both see the module `W`. A parameter/return ANNOTATION, a class BASE, a class KEYWORD
    and the type-parameter list itself are evaluated in the TYPE-PARAMETER scope, where
    they ARE bound. So the type-parameter names belong to the second group only.

    `seen` holds every node between the call and this definition (the call included), so
    a call nested anywhere inside a decorator or a default is recognised by identity of
    an ancestor — not by the direct child alone.
    """
    decorators = getattr(node, "decorator_list", ()) or ()
    arguments = getattr(node, "args", None)
    defaults: list[ast.AST] = []
    if arguments is not None:
        #: The DEFAULTS only — not the `ast.arguments` node itself, which also holds the
        #: `ast.arg` nodes and therefore the ANNOTATIONS, and those evaluate in the
        #: TYPE-PARAMETER scope (`def f[W: object](x: W)` sees the TypeVar).
        defaults.extend(arguments.defaults)
        defaults.extend(d for d in arguments.kw_defaults if d is not None)
    enclosing = {id(child) for child in (*decorators, *defaults)}
    return bool(seen & enclosing)


def _walrus_targets(node: ast.AST) -> set[str]:
    """The names `:=` binds OUTSIDE a comprehension — its own scope's targets excluded.

    PEP 572: an assignment expression inside a comprehension binds its target in the
    CONTAINING scope (that is the whole point of the walrus there), while the
    comprehension's `for` targets bind in the comprehension's own scope. So this walks
    the comprehension for `NamedExpr` targets ONLY, and adds nothing else.
    """
    return {
        sub.target.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name)
    }


def _receiver_root_name(node: ast.expr) -> str | None:
    """The NAME at the root of a receiver chain — `PROJ.g.query` -> `PROJ`.

    `None` when the chain bottoms out in something that binds no name (a literal,
    a subscript of a comprehension, …): an unnameable receiver cannot be a
    third-party import, so it is never excluded from grading.
    """
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break
    return node.id if isinstance(node, ast.Name) else None


def _touches_own_code(block: ast.AST, own_names: set[str]) -> bool:
    """Whether the block USES a name bound from own code.

    The call form of the same seam: a probe that hoisted its import above the
    guard (`from tortoise.sdk import TortoiseSDK` … `try: TortoiseSDK()`) still
    reads a moved/renamed door through the guard, so its arm must stay graded —
    whether or not the import sits inside the `try`.
    """
    return any(
        isinstance(node, ast.Name) and node.id in own_names for node in ast.walk(block)
    )


#: Calls that reach the capability seam on a client the block did not import
#: itself — `proj.g.query(…)` on a PARAMETER, `FalkorProjection.from_uri(…)`.
#: Named from the class-2 statement ("…reaches an own-code capability seam
#: (`_get_proj`, `.g.query`, `TortoiseSDK`, `FalkorProjection.from_uri`, an
#: own-code import)"), not from a flag — the seam is the API a rename breaks, so it
#: does not go stale when a module re-spells its flag.
_CAPABILITY_CALLS = frozenset({"from_uri", "_get_proj", "query", "graph_overview"})
_CAPABILITY_NAMES = frozenset({"TortoiseSDK", "FalkorProjection"})


def _reaches_capability_seam(
    block: ast.AST, third_party: frozenset[str] | set[str], parents: dict[int, ast.AST]
) -> bool:
    """Whether the block CALLS the capability seam, however the client arrived.

    `_imports_own_code` and `_touches_own_code` both need the own-code name to be
    reachable from the block, so a probe that takes the client as a PARAMETER —
    `def _probe_set_order(proj)` … `proj.g.query(…)` — satisfied neither and was
    graded by NOTHING, even though `.g.query` is one of the seams class 2 names by
    hand. That probe's sentinel becomes a SKIP whose reason names no door, so the
    whole live proof skipped green over a renamed seam (`tests/
    test_8870_version_anchor_write_order.py`, measured).

    The three own-code gates are ORed, never replaced: a moved module, a moved
    door on an imported client, and a moved method on a passed-in client are the
    same programming error, and each one is invisible to the other two tests.
    Measured blast radius of adding it at this head: exactly TWO blocks tree-wide,
    both genuine skip-deciding probes (the other one already carries its arm).

    ⛔ THE ATTRIBUTE FORM IS NARROWED, THE ARM IS NOT DROPPED. `query` and
    `graph_overview` are OUR verbs, and they are also ordinary words a third-party
    client answers to, so an ungated arm graded `import some_optional_dep` …
    `some_optional_dep.query(…)` as a root and turned a legitimate skip into a
    collection error (review of 4478e90782d, P2). Removing the arm instead was
    worse in the other direction: a module-body guard whose client is built at
    module scope (`PROJ = _make_proj()` … `try: PROJ.g.query(…)`) is then graded by
    NOTHING, and a swallowed rename reads as "storage unavailable" again — the
    class-2 false PASS this file exists to catch (review of 90a0e9e8b20c, finding
    3). So the receiver decides, on EVIDENCE: a receiver whose root name a NON-own
    import binds is that optional extra and is skipped — UNLESS a binding for that name
    can REACH the call site (§4.2.2), in which case it no longer denotes the import
    there. ⛔ TWO DIFFERENT SETS IMPLEMENT THAT, and an earlier version of this paragraph
    said "re-bound anywhere in the module", which is false in BOTH directions (review of
    this PR, P2): module scope comes from `_third_party_receivers`
    (`_module_scope_bindings` + `_global_scope_bindings`), and the probe's OWN enclosing
    scopes come per probe from `_enclosing_bindings`. Measured with this file's own
    `_probe_blocks_from_source`: a parameter of a NEIGHBOURING function does NOT cancel
    the exemption (`()` exempt), a parameter of the probe's own function DOES
    (`((4, (), True, False, True),)`), and a module-scope rebind does. Every other
    receiver — a parameter (`def _probe(proj)` … `proj.g.query`), a client assigned at
    module scope, `FalkorProjection.from_uri(…)` — is still graded. `receiver_is_sdk`
    (tools/sdk_privatise.py) was considered for this and rejected: it answers from
    annotations and stand-ins, so it cannot see the `PROJ = _make_proj()` shape the
    finding names, and gating on it would break the pinned parameter-client witness
    (`tests/test_8870_version_anchor_write_order.py:138`).

    ⛔ `third_party` IS A REQUIRED PARAMETER, deliberately. It once defaulted to
    `frozenset()`, which IS the UNGATED behaviour this narrowing exists to remove:
    every call site passes it today, so a fifth one that forgot would have silently
    re-opened the `some_optional_dep.query` false red with nothing red on any test. A
    missing argument now fails loudly at the call site instead (review P2).

    ⛔ `parents` IS HOW THE CALL'S OWN REACH IS RESOLVED, AND IT IS REQUIRED. `third_party` is
    module-scope only, so the bindings that can reach a probe from its ENCLOSING
    FUNCTIONS — parameters, type parameters, locals, up the closure chain — arrive here
    per probe (`_enclosing_bindings`). Folding them into one module-wide set was
    measured to grade a legitimate third-party probe in a NEIGHBOURING function as a
    ROOT — the `4k-`/`4l-` false red, one granularity up (review of #14160, P2).
    """
    for node in ast.walk(block):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _CAPABILITY_CALLS:
            root = _receiver_root_name(func.value)
            # EXEMPT ONLY WHEN THE NAME STILL DENOTES THE IMPORT **AT THIS CALL SITE**.
            # Reach is resolved FROM THE CALL NODE, not from the block: §4.2.2 says a
            # binding reaches every use in the SAME block, and a nested `def`/`class`/
            # `lambda` is a different block whose bindings do NOT reach a call outside it.
            # Resolving at the block instead was wrong in BOTH directions — it missed a
            # rebind inside a `def` nested in the block (fail-open) and it cancelled the
            # exemption for a nested scope's binding that cannot reach a block-level call
            # (false red, one of them a regression against `origin/main`). This ONE rule
            # replaces the separate "the block rebinds it" term, which is why that term
            # is gone rather than patched (review of #14160, P2).
            if root in third_party and root not in _enclosing_bindings(node, parents):
                continue
            return True
        if isinstance(func, ast.Name) and func.id in _CAPABILITY_NAMES:
            return True
    return False


def _probe_roots(tree: ast.Module) -> list[ast.Try]:
    """The `try` blocks that DECIDE availability — FIVE admission rules.

      * a module-body `try` that assigns an `*AVAILABLE` flag — admitted by the SAME
        three ORed arms as the skip-read rule below (own import inside the `try`,
        own-code contact, or the narrowed capability call), NOT by own-code contact
        alone;
      * a module-body `try` that binds a name the module's own `skipif`/`skip`
        condition READS — `LIVE`, `_FTS_ESCAPE_LIVE`, `_LIVE_OK` are the same
        decision as `FALKORDB_AVAILABLE` even though none ends in `AVAILABLE`;
      * the probe `try` inside a module-level helper the flag is RESOLVED by
        (`FALKORDB_AVAILABLE = _falkordb_available()`), reached through the
        helper's control flow — the guard may sit inside a `for`;
      * the probe `try` inside a helper a SKIP decision calls — directly from a
        `skipif`, or through a sentinel (`uri = _live_target()` … `if uri is None:
        pytest.skip(…)`), where no flag exists at all;
      * the probe `try` in ANY OTHER function whose `skip`/`skipif`/`xfail`
        decision READS a name that `try` BINDS — the test-body widening below
        (`tests/test_index_surfacing.py:269`), which admits a probe that decides its
        own skip with no flag, no `skipif` condition and no helper entry point.

    The contract above is the code's, not an aspiration: a docstring that said
    "two shapes" while the function admitted four told any lane auditing this
    gate from its own contract that half its population was ungraded (review of
    3ce7ffdd867, P2). Measured per-branch root counts at that head, for the four
    rules that then existed: 26 / 5 / 15 / 5. The fifth rule is the `# ── ANY OTHER
    function whose probe feeds its own SKIP decision` pass at the end of this
    function; its blast radius is stated there with its measurement.

    Both module-body rules walk module-level CONTROL FLOW (`if`/`for`/`with`, and
    through a handler-less `try/finally` — a pure wrapper, see
    `_control_flow_tries`).

    ⛔ DECLARED RESIDUAL — a probe inside a `def test_…` BODY is graded by NOTHING.
    The population is module-body `try`s plus `try`s inside module-LEVEL helpers that
    the flag is resolved by, or that a skip decision calls; a test function is in
    neither set, even when it is the same shape (`except Exception` → sentinel →
    `pytest.skip`). Live instance at this writing: `tests/test_index_surfacing.py:269`
    — a broad `except Exception` inside a `for` in a test body, capturing into
    `unsupported` and feeding `pytest.skip(...)`; measured `_reaches_capability_seam`
    TRUE, `_assigns_availability` FALSE, and absent from `_probe_report()` while the
    census reports `UNARMED == ()`. It is DECLARED here rather than silently left out,
    and the widening is named: admit any function containing a `skip`/`skipif`/`xfail`
    call that ALSO binds a name the call reads, reusing the sentinel set
    `_skip_guard_call_names` already computes.

    ✅ THAT DECISION IS NOW MADE AND THE WIDENING IS APPLIED (review P1, closed).
    It was declared because grading the named block "demands knowing which errors
    must re-raise — a design call, not a mechanical edit". The design answer is the
    one every other probe in this census already uses: re-raise
    `(AttributeError, NameError, TypeError)` ahead of the broad arm and LEAVE THE
    BROAD ARM. That is safe for exactly this block because an engine that cannot
    hold a fulltext index answers with a `redis.exceptions.ResponseError` — a
    `RedisError`, not a `TypeError`, so the legitimate skip is untouched while a
    moved door or a changed call shape now fails loudly instead of being recorded
    as "this engine has no FTS" (see `tests/test_index_surfacing.py:269`).
    Measured blast radius of the widening, and the two baselines are DIFFERENT, so
    both are stated (an earlier version of this paragraph mixed them and named the
    wrong set — review of `2046456825`, P1, which is exactly the "silent drop is a
    fail" class this census forbids):

      * vs the commit that introduced it (`b41afcdd0c`): 5 new roots in 4 files —
        `tests/test_indexes.py:1422`, `tests/test_11682_quote_indexed.py:438` and
        `:656`, `tests/test_extractor_reliability.py:898`, and
        `tests/test_session_semantic_retrieval.py:298`;
      * vs `origin/main` at `2c0d66e2ec` (the commit `#14166` merged the widening
        onto — see the population note below for why that commit, and not today's
        `origin/main`): 6 new roots in 5 files — the five above plus
        `tests/test_index_surfacing.py:269`, which the ARGUMENT-ONLY first attempt had
        already graded and which the guard-half fix therefore did not re-add.

    Population: the tree the widening was measured AGAINST — `2c0d66e2ec`, which is
    `origin/main` as it stood immediately before `#14166` merged the widening — reads
    64 rows / 56 roots / 54 files; with the widening it is **70 / 62 / 58**, and
    `UNARMED` stays `()` because every previously-unarmed block is ARMED in the same
    change. ⛔ THE BASELINE IS THAT COMMIT, NOT TODAY'S `origin/main`: the widening
    itself landed in `#14166` (`dc92b0eecc`), so today's `origin/main` already
    measures 70 / 62 / 58 — the same as this (comment-only) tree, both read with
    `_probe_report()` — and an arrow written as "`origin/main` 64 → 70" is not
    reproducible from its own label. Measured, not argued: 64/56/54 on `2c0d66e2ec`
    with that tree's own census, 70/62/58 here and on `origin/main` (`69908d006b`)
    (review of this PR, execution leg). One of the five is not charged
    (`test_extractor_reliability.py:898` already re-raises, `swallows=False`); it is
    graded because its `pytest.xfail` guard reads names the `try` binds.
    ⛔ AND IT IS A MEASUREMENT, NOT A PIN. The self-test pins PRESENCE, never an exact
    total: `test_the_import_seam_census_sees_the_shapes_it_grades` asserts only LOWER
    BOUNDS on the population (`roots >= 30`, `importing >= 20`) plus the presence of two
    named files, so the row/root/file numbers above are measurements and a later lane
    that moves the tree owes no re-cut of a literal. (This paragraph said "Population pin
    moves …" until a reviewer measured that no such pin exists; the correction first
    landed as bare `:2191`/`:2195` line citations, which are stale by design and pointed
    at unrelated lines by the head that carried them — the bounds are named by symbol here
    instead. Final review of #14160, P2; execution re-measured at the resolved head.)

    ⛔ THE SENTINEL IS USUALLY CARRIED BY A GUARD, NOT BY THE MESSAGE (review of
    `cd75ec7144`, P1). The first version of this widening keyed only on the skip
    call's ARGUMENTS, so `if not dropped: pytest.skip("engine has no drop
    procedure")` — where the decision plainly reads `dropped` — contributed
    NOTHING and the residual was closed for interpolated messages only. It keys on
    the `if`/`assert` GUARD now (`_skip_call_read_names`), which is also what
    surfaced the four unarmed blocks above; against the argument-only rule the blast
    radius grew BY the 5 named above, i.e. from the 1 `test_index_surfacing.py:269`
    it already graded to **6** new roots in total vs the pre-widening census. (This
    sentence said "to 5" until a reviewer of `2046456825` measured it — 1 + 5 = 6, and
    a total that contradicts the bullet list two paragraphs up is the same
    baseline-mixing this section exists to stop.)

    ⛔ DECLARED RESIDUAL — THE SKIP CALLED INSIDE THE BROAD HANDLER is graded by
    NOTHING. The fifth rule admits a `try` only when the skip decision READS a name
    that `try` BINDS (`_bound_names(block) & _skip_call_read_names(fn)`). With the
    guard-keying above that reading covers the sentinel in the MESSAGE
    (`unsupported = e` … `pytest.skip(f"…{unsupported}")`) and the sentinel in the
    GUARD (`if not dropped: pytest.skip("…")`). It does not cover the third spelling
    of the same decision: the skip called IN the broad handler, which binds NOTHING
    (or binds only `exc` — `_bound_names` counts `Assign`/`AnnAssign` targets, not
    `ast.ExceptHandler.name`) while the skip reads a reason LITERAL. Two gates then
    reject it: an empty `skip_read` drops the whole FUNCTION at `if not skip_read:
    continue`, and a disjoint one fails the `&` above.

    Measured on the real tree at this head, in-process, by adding exactly that
    admission (a `try` whose handler is `_swallows_everything` and whose handler body
    calls a `skip`/`skipif`/`xfail` verb) to the fifth pass: THREE new rows in TWO
    new files — 70 rows / 62 roots / 58 files → **73 / 65 / 60** — and all three come
    back `covered=()`, i.e. UNARMED, so the rule cannot land without arming each:

      * `tests/test_domain_validators.py:465` (`graph_sdk`) — `imports_own_code=True`
        (`from tests._embedded import has_falkor`), so an own-code RENAME becomes
        "embedded FalkorDBLite unavailable". OWES `ImportError` on top of the usual
        arm (`_required_names(own_import=True)`); the three-name arm alone would red
        it on BOTH the UNARMED and the import-seam gate, since the broad arm is kept.
      * `tests/test_8870_version_anchor_write_order.py:202` (`_live_proj`) — reaches
        the capability seam (`FalkorProjection.from_uri`, `proj.g.query`); binds
        `{'proj'}`, the skip reads `{'exc','type','_live_utils','reason'}`.
      * `tests/longmem_eval/test_variant_containment_judge.py:106` — binds
        `instances`, the skip reads `{'ROWS'}`; the call shape it probes is own code.

    DECLARED here rather than silently left out, per this block's own contract.
    NOT LANDED because it re-pins the census AND arms three files outside this PR's
    declared `files[]`; it owes its own witness row and a negative control whose
    handler does NOT skip.

    Nested `try`s inside a probe body are not roots: a `try/_proj.close()/except
    Exception: pass` cleanup is not the availability decision. The import-seam
    check reaches into them separately, because a swallowed import there is the
    same defect one level down.
    """
    roots: list[ast.Try] = []
    resolvers = _resolver_functions(tree)
    skip_gated = _skip_gating_functions(tree)
    skip_referenced = _skip_condition_names(tree)
    module_own = _own_imported_names(tree.body)
    #: Names this module imported from a NON-own package (`import torch`, `import
    #: some_optional_dep`), MINUS any re-bound at MODULE scope: a name that no longer
    #: denotes the import where a probe uses it is not evidence that the `.query` is a
    #: third-party verb (`_third_party_receivers`). A capability ATTRIBUTE called on one
    #: of them is that package's verb, not our seam — see `_reaches_capability_seam`.
    #: Bindings inside a FUNCTION are NOT folded in here; they reach a probe only from
    #: its own ancestor chain, so they are computed per CALL by `_enclosing_bindings`.
    third_party = _third_party_receivers(tree)

    #: Every parent edge, once, so `_enclosing_bindings` can resolve a call's enclosing
    #: scopes. Reach is per PROBE: one module-wide set of shadowed names cannot express
    #: "rebound inside `_b` but not inside `_a`", and folding them together graded `_a`'s
    #: legitimate third-party probe as a ROOT — the `4k-`/`4l-` false red (review P2).
    _parents: dict[int, ast.AST] = {}
    for _parent in ast.walk(tree):
        for _child in ast.iter_child_nodes(_parent):
            _parents[id(_child)] = _parent

    # MODULE-LEVEL control flow, not just `tree.body`. `tests/test_assembly_pure.py:2615`
    # puts its probe inside `if _DOCKER_URI:`, so a walk over the direct children
    # returned NO row for that file while `_skip_condition_names(tree)` already held
    # `_FALKORDB_UP` — a live unarmed probe, 19 docker legs skipped behind it, and
    # the census reporting `UNARMED = ()` (review of 3a363eb1a3a). The helper
    # branches below already descended control flow; the module level did not, and
    # that asymmetry was the hole.
    for stmt in _control_flow_tries(tree.body):
        if _assigns_availability(stmt) and (
            _imports_own_code(stmt)
            or _touches_own_code(stmt, module_own)
            or _reaches_capability_seam(stmt, third_party, _parents)
        ):
            # The own-code gate applies to THIS branch too. Without it the rule
            # redded `TORCH_AVAILABLE = False / try: import torch …`, i.e. a probe
            # whose only seam is an OPTIONAL third-party extra — precisely the
            # false red the import-seam rule and the self-test refuse (review of
            # 03fc244, P2).
            #
            # The capability arm is ORed here, NARROWED rather than dropped: it is
            # the ONLY arm that grades a module-body guard whose client was built
            # above the `try` (`PROJ = _make_proj()` … `PROJ.g.query(…)`), which a
            # removal left graded by nothing (review of 90a0e9e8b20c, finding 3).
            # The third-party false red it used to admit is excluded by
            # `third_party`, so `import some_optional_dep; some_optional_dep.query`
            # is still left alone — that negative shape has its own witness, and
            # re-adding an UNGATED arm reddens it (review of 90a0e9e8b20c, finding
            # 1).
            roots.append(stmt)
        elif (_bound_names(stmt) & skip_referenced) and (
            _imports_own_code(stmt)
            or _touches_own_code(stmt, module_own)
            or _reaches_capability_seam(stmt, third_party, _parents)
        ):
            # A module-body probe whose flag is spelled something other than
            # `*AVAILABLE` — `LIVE`, `_FTS_ESCAPE_LIVE`, `_FALKORDB_UP` — is NOT an
            # availability flag by name, but it IS the decision: a `skipif` reads it.
            # The same own-code test (and the same narrowed capability arm) as the
            # helper branches applies, so a probe whose only import is an optional
            # third-party one is still left alone.
            roots.append(stmt)
    for stmt in tree.body:
        if (
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name in resolvers
        ):
            # MODULE-level own imports belong in this set: `_touches_own_code`
            # promises that a probe reading a door imported ABOVE the guard is
            # still the seam, and reusing a module-scope import is the common
            # spelling — grading only the helper's own imports left that shape
            # ungraded with the census green (review of 3ce7ffdd867, P2).
            own_names = _own_imported_names(stmt.body) | module_own
            roots.extend(
                block
                for block in _control_flow_tries(stmt.body)
                if (
                    _imports_own_code(block)
                    or _touches_own_code(block, own_names)
                    # The parameter-client arm, narrowed by `third_party` instead
                    # of gated on own-code reachability: gating it WOULD break the
                    # pinned witness (a probe reaching the seam on a PASSED-IN
                    # client is a root even with no own import in the block —
                    # `tests/test_8870_version_anchor_write_order.py:138`), and
                    # leaving it ungated graded a third-party client's `.query` as
                    # this repository's seam (review of 90a0e9e8b20c, findings 1+3).
                    or _reaches_capability_seam(block, third_party, _parents)
                )
            )
        elif (
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name in skip_gated
        ):
            # Only a probe that TOUCHES own code is graded — it imports own code
            # inside the `try`, or it calls a name it imported from own code
            # above it. That is the seam this class exists for (a module or door
            # that is moved must fail loudly). A THIRD-PARTY import in the same
            # `try` — `import redislite.falkordb_client` in the embedded
            # harnesses, `sentence_transformers` in the model probes — is an
            # optional extra, and demanding a re-raise for it would turn a
            # legitimate skip into a collection error, contradicting the rule
            # that an absent capability skips.
            own_names = _own_imported_names(stmt.body) | module_own
            roots.extend(
                block
                for block in _control_flow_tries(stmt.body)
                if (
                    _imports_own_code(block)
                    or _touches_own_code(block, own_names)
                    # Same arm, same narrowing as the resolver branch above: the
                    # passed-in client stays graded, a third-party client's
                    # generic `.query` does not (review of 90a0e9e8b20c, 1+3).
                    or _reaches_capability_seam(block, third_party, _parents)
                )
            )
    # ── ANY OTHER function whose probe feeds its own SKIP decision (review P1) ──
    #
    # The two passes above are module-level by construction: one reads `tree.body`
    # for helpers a SKIP DECISION calls, the other `tree.body` for the resolvers a
    # flag is assigned from. A probe inside a `def test_…` body that computes its
    # own `skip` sentinel is in neither — the previously DECLARED residual, whose
    # live instance was `tests/test_index_surfacing.py:269`. The admission rule and
    # the narrowing are the ones the skip-gated helper branch already uses; only the
    # set of functions searched changes. Blocks graded above are skipped by identity
    # so no root is counted twice.
    graded = {id(block) for block in roots}
    module_level = {
        id(stmt)
        for stmt in tree.body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Skip by IDENTITY, not by name: a NESTED def that merely shares a name
        # with a module-level resolver/skip-gated helper is a different function
        # with its own probes, and skipping it by name dropped them silently
        # (review of `cd75ec7144`, P2).
        if id(fn) in module_level and (fn.name in resolvers or fn.name in skip_gated):
            continue  # already searched above, from its module-level entry
        skip_read = _skip_call_read_names(fn)
        if not skip_read:
            continue
        own_names = _own_imported_names(fn.body) | module_own
        for block in _control_flow_tries(fn.body):
            if id(block) in graded:
                continue
            if not (_bound_names(block) & skip_read):
                continue
            if (
                _imports_own_code(block)
                or _touches_own_code(block, own_names)
                or _reaches_capability_seam(block, third_party, _parents)
            ):
                graded.add(id(block))
                roots.append(block)
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


#: Files the probe census could not read or parse. A file it cannot GRADE must
#: never be a silent drop: `except SyntaxError: continue` made sixteen files
#: vanish from the population while the census stayed green (measured while
#: fixing round-3 review F1), which is the "a silent drop is a fail" class this
#: gate exists to catch.
_UNGRADABLE: list[str] = []


#: Text markers that keep a file from being dropped UNPARSED. It must be a
#: SUPERSET of every verb the two skip-guard sets accept (`skip`, `skipif`,
#: `xfail`) plus `importorskip` — which the guard sets do NOT model (no helper
#: of ours is called, so no row can be built from it) but which still keeps a
#: file from being dropped unparsed — plus the flag spellings: a marker list narrower than
#: the guard sets is a SILENT DROP — the walk would grade the block if it ever saw
#: it, and the file is discarded before parsing and recorded as nothing (review of
#: `ee5457f`, P2: `if URI is None: pytest.xfail(…)` was that hole). The self-test
#: pins the superset, so the two lists cannot drift apart again.
_PREFILTER_MARKERS = (
    "AVAILABLE",
    "skipif",
    "skip(",
    "pytest.skip",
    "xfail",
    "importorskip",
)


@functools.lru_cache(maxsize=1)
def _probe_report() -> tuple[tuple[str, int, tuple[str, ...], bool, bool, bool], ...]:
    out: list[tuple[str, int, tuple[str, ...], bool, bool, bool]] = []
    del _UNGRADABLE[:]
    for path in _tracked_python_files():
        rel = str(path.relative_to(ROOT))
        if not (rel.startswith("tests/") or rel.startswith("tools/")):
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            _UNGRADABLE.append(rel)
            continue
        # A cheap text prefilter, not the rule: `AVAILABLE` is a substring of
        # every `*AVAILABLE` flag spelling, `skipif` admits the
        # decorator-resolved probes that have NO flag, and the `skip(`/`pytest.
        # skip` markers admit the SENTINEL probes (`uri = _live_target()` …
        # `if uri is None: pytest.skip(...)`) whose file need not mention a flag
        # or a `skipif` at all — that omission is why
        # `tests/test_9226_dump_paging_v6.py` was never parsed (review of
        # 76abdca, P2).
        if not any(marker in src for marker in _PREFILTER_MARKERS):
            continue
        try:
            rows = _probe_blocks_from_source(src)
        except SyntaxError:
            _UNGRADABLE.append(rel)
            continue
        out.extend((rel, *row) for row in rows)
    return tuple(out)


def test_a_file_the_census_cannot_grade_is_a_red_not_a_silent_drop():
    """A file the census cannot read or parse is UNGRADED — which must be a red.

    `except SyntaxError: continue` drops the whole file from the population, so
    the gate reads GREEN over code it never looked at. Not hypothetical, and not
    about somebody else's tree: while fixing review F1 a malformed comment was
    written into sixteen files, every one of them vanished from the census, and
    `UNARMED` went EMPTY — which is indistinguishable from "every probe is
    armed". A dropped file is a fail, not an absence.
    """
    _probe_report()
    assert not _UNGRADABLE, (
        "the probe census could not read or parse these files, so it graded "
        f"NOTHING in them: {_UNGRADABLE}"
    )


def _required_names(own_import: bool) -> set[str]:
    """The programming-error names a graded block's arm must cover.

    `AttributeError`/`NameError`/`TypeError` always: a renamed door, a renamed
    local, a changed call shape. `ImportError` only when the block imports OWN
    code INSIDE the `try` — that is the module-rename seam (the #14125 F2
    finding). A block that cannot import anything inside itself cannot raise it
    from its own body, and demanding it there would force an `ImportError`
    re-raise on a probe whose only import is an optional third-party extra —
    turning a legitimate skip into a collection error.
    """
    return _RAISED if own_import else _RAISED - {"ImportError"}


def _unarmed_from_rows(
    rows: tuple[tuple[str, int, tuple[str, ...], bool, bool, bool], ...],
) -> tuple[tuple[str, int], ...]:
    """The UNARMED gate's verdict, over any row population.

    Split out of `_unarmed_probe_blocks` so the gate VERDICT itself can be witnessed from
    synthetic source (`_probe_blocks_from_source` rows) and not only from the real tree's
    census. Every self-test in this file used to exercise a PREDICATE and never a gate
    verdict, which is why a shape BOTH gates passed could have no red row anywhere
    (review of PR #14287, F1's second half).
    """
    return tuple(
        (rel, lineno)
        for rel, lineno, covered, swallows, own_import, is_root in rows
        if is_root and swallows and not set(covered) >= _required_names(own_import)
    )


def _unarmed_probe_blocks() -> tuple[tuple[str, int], ...]:
    """Probe roots that swallow a programming error instead of re-raising it."""
    return _unarmed_from_rows(_probe_report())


def _seam_from_rows(
    rows: tuple[tuple[str, int, tuple[str, ...], bool, bool, bool], ...],
) -> tuple[tuple[str, int], ...]:
    """The IMPORT-SEAM gate's verdict, over any row population.

    Split out for the same reason as `_unarmed_from_rows`: a gate verdict needs a witness
    that does not depend on the real tree happening to contain the shape.
    """
    return tuple(
        (rel, lineno)
        for rel, lineno, covered, swallows, own_import, _is_root in rows
        if swallows and own_import and "ImportError" not in covered
    )


def _probe_blocks_with_an_uncovered_import_seam() -> tuple[tuple[str, int], ...]:
    """Blocks that SWALLOW an own-code import their arm cannot see.

    `swallows` is required: a `try: … finally: …` probe has no broad arm, so an
    `ImportError` at its import propagates and there is nothing to fix — redding
    it would be a false positive on a shape that is already loud.
    """
    return _seam_from_rows(_probe_report())


def test_every_availability_probe_re_raises_a_programming_error():
    """A rename must fail LOUDLY, not read as "storage unavailable".

    #13899's retirement hid for a whole wave behind exactly this arm: the probe's
    `except Exception` converted an `AttributeError` into `FALKORDB_AVAILABLE =
    False`, the family skipped locally, and CI — which refuses a skip — redded a
    shard with a message about live storage that named no door. Measured before
    this gate: ten modules in the same shape, plus 13 more in the assembly and
    `_probe` families — and eight more that resolve the flag through a helper
    (`FALKORDB_AVAILABLE = _probe()`), which this census reaches too.

    ⛔ Three repair rounds in a row reported this class closed while probes kept
    the shape, because the census knew ONE resolution at a time: first only a
    module-body `try` (missing the helper-resolved families), then only the
    `FALKORDB_AVAILABLE` spelling (missing `_AVAILABLE`), then only an assigned
    flag at all (missing `_LIVE_AVAILABLE` and the three probes a module-level
    `skipif` calls directly). The walk now answers from the DECISION — a flag
    whose name ends in `AVAILABLE`, or a helper whose value a `skipif` reads —
    so a fourth spelling or a fourth resolution shape cannot go quietly blind.
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
            "a skip is the correct outcome.) ⛔ NEITHER REPAIR FITS AN EXTRA-GATED OWN "
            "MODULE (`tortoise.embeddings`, numpy only in the `embeddings` extra) — see "
            "the DECLARED RESIDUAL on `_swallows_everything`: re-raising turns the "
            "legitimate skip into a collection error, and hoisting makes the module "
            "unimportable when the extra is absent."
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

    # ⛔ F1 (review of PR #14287): THE RENAME IS DROPPED AT THE FIRST ARM, and the later
    #     four-name re-raise credited `ImportError` anyway — producing a row BYTE-IDENTICAL
    #     to the `fixed` row above, so the census could not tell "armed" from "rename
    #     swallowed". Both gates passed, because both are conjunctions with coverage. This
    #     is the shape a lane reaches by obeying the gates' own repair message on a block
    #     that already carries an `except ModuleNotFoundError: pass` skip, so it is the
    #     fail-open this whole file exists to catch (category A: a false PASS).
    rename_dropped = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except ModuleNotFoundError:
            pass
        except (AttributeError, NameError, TypeError, ImportError):
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert rename_dropped == (
        (3, ("AttributeError", "NameError", "TypeError"), True, True, True),
    ), (
        "a `ModuleNotFoundError: pass` arm DROPS the module rename, so the later four-name "
        "re-raise must NOT credit `ImportError` back into coverage — that credit made this "
        f"row identical to the armed `fixed` row: {rename_dropped}"
    )

    # ⛔ …and the GATE VERDICTS themselves are witnesses, not just the predicate: both gates
    #     must RED this block and stay green on the armed one. Without this pair the whole
    #     class had no red row anywhere, because every other self-test exercises `_covered`
    #     / `_swallows_everything` directly and never a gate verdict (review of PR #14287,
    #     F1's second half — the fix that closed the leak added predicate rows only).
    _witness = "tests/_14287_witness.py"
    assert _unarmed_from_rows(tuple((_witness, *row) for row in rename_dropped)) == (
        (_witness, 3),
    ), (
        "the UNARMED gate must report a probe that drops the module rename at its first "
        "arm, even though a later arm re-raises the four names"
    )
    assert _seam_from_rows(tuple((_witness, *row) for row in rename_dropped)) == (
        (_witness, 3),
    ), (
        "the IMPORT-SEAM gate must report an own-code import whose rename is dropped at "
        "the first arm — `ImportError` in `covered` is not the same question"
    )
    assert _unarmed_from_rows(tuple((_witness, *row) for row in fixed)) == (), (
        f"the armed control must stay GREEN in the unarmed gate: {fixed}"
    )
    assert _seam_from_rows(tuple((_witness, *row) for row in fixed)) == (), (
        f"the armed control must stay GREEN in the import-seam gate: {fixed}"
    )

    # …and the same leak in the OTHER alias family: a dropped `UnboundLocalError` must not be
    # credited back as `NameError` by a later arm (a renamed LOCAL, not a moved module). The
    # arm still names `ImportError`, so the seam gate stays green and the UNARMED gate is the
    # one that must catch it.
    _local_dropped = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except UnboundLocalError:
            pass
        except (AttributeError, ImportError, NameError, TypeError):
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert _local_dropped == (
        (3, ("AttributeError", "ImportError", "TypeError"), True, True, True),
    ), (
        "an `UnboundLocalError: pass` arm drops `NameError`'s family, so the later `NameError` "
        f"re-raise must not credit it back: {_local_dropped}"
    )
    assert _unarmed_from_rows(tuple((_witness, *row) for row in _local_dropped)) == (
        (_witness, 3),
    ), "the UNARMED gate must report a dropped `UnboundLocalError` recredited as `NameError`"
    assert _seam_from_rows(tuple((_witness, *row) for row in _local_dropped)) == (), (
        "the import seam IS covered here, so the seam gate stays green — the local family is "
        "the unarmed gate's question"
    )

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

    # `except BaseException` swallows every programming error, so it must read as
    # a BROAD arm. Without this pin the member can be dropped from `_BROAD` and
    # nothing reddens (`review of d2d783ca`, P2) — measured: with `_BROAD` back to
    # `{Exception}` this block classifies `swallows=False` and is not graded.
    base_exc = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK
            TortoiseSDK()
            FALKORDB_AVAILABLE = True
        except BaseException:
            FALKORDB_AVAILABLE = False
        """
    )
    assert base_exc == ((3, (), True, True, True),), (
        f"`except BaseException:` must read as a BROAD, swallowing arm: {base_exc}"
    )

    # ⛔ The prefilter must be a SUPERSET of the verbs the guard sets accept, or a
    # file the walk WOULD grade is dropped before parsing and recorded as nothing
    # — the "a silent drop is a fail" class, which nothing else here can see
    # (review of `ee5457f`, P2: a sentinel whose skip is `pytest.xfail` was that
    # hole). Both lists live in this file, so the pin is a set comparison.
    # ⛔ A SUBSTRING test per verb was vacuous: `"skip" in "skipif"` is True, so
    # deleting BOTH `skip(` and `pytest.skip` left this green while the sentinel
    # family (`tests/test_9226_dump_paging_v6.py`) lost its only markers. Each
    # guard verb needs a marker that carries it ALONE.
    for verb, needles in (
        ("skip", ("skip(", "pytest.skip")),
        ("skipif", ("skipif",)),
        ("xfail", ("xfail",)),
        ("importorskip", ("importorskip",)),
    ):
        assert any(needle in _PREFILTER_MARKERS for needle in needles), (
            f"the prefilter lost `{verb}` (needs one of {needles}) — a file whose "
            "only skip verb is that one is dropped UNPARSED and recorded as "
            "nothing: a silent drop"
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
    assert optional == (), (
        "a probe whose ONLY import is an optional third-party extra is not graded "
        "at all — for an optional capability a SKIP is the correct outcome, and "
        f"demanding an arm there would turn a skip into a collection error: {optional}"
    )

    # A CLEANUP-THEN-RAISE arm is coverage: it ends by re-raising, so nothing is
    # dropped. `tests/test_9226_dump_paging_v6.py:517` ships exactly this shape;
    # a coverage rule demanding "exactly one bare `raise`" would red it the
    # moment the block became a root (review of `ee5457f`, P3).
    cleanup_raise = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK
            TortoiseSDK()
            FALKORDB_AVAILABLE = True
        except (AttributeError, NameError, TypeError, ImportError):
            clean()
            raise
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert cleanup_raise == (
        (3, ("AttributeError", "ImportError", "NameError", "TypeError"), True, True, True),
    ), (
        "an arm that cleans up and THEN re-raises covers the programming errors — "
        f"nothing is dropped: {cleanup_raise}"
    )

    # A probe guard that is NOT a direct child of its helper — inside a `for`
    # over candidate URIs — and resolved through the `_AVAILABLE` spelling. Two
    # families in this tree are exactly this shape, and reading only the
    # helper's direct children graded NOTHING there (review F3(b)).
    looped = rows(
        """
        _AVAILABLE = False
        def _probe(candidates):
            for uri in candidates:
                try:
                    from tortoise.projection import FalkorProjection
                    return True, uri
                except Exception:
                    continue
            return False, None
        _AVAILABLE, _WORKING_URI = _probe(["docker://x"])
        """
    )
    assert looped == ((5, (), True, True, True),), (
        "a probe guard inside a `for` in a `_AVAILABLE`-resolved helper must be "
        f"graded as a ROOT, not skipped: {looped}"
    )

    # ⛔ F2 — ORDER is part of the property, and it was still open at
    # `9b7ac6ee8d`: Python matches handlers in source order, so a narrow arm
    # BELOW the broad one is UNREACHABLE and the block behaves exactly like the
    # pre-fix code while a set-membership check reads it as ARMED.
    broad_first = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        except (AttributeError, NameError, TypeError, ImportError):
            raise
        """
    )
    assert broad_first == ((3, (), True, True, True),), (
        "an inert arm (the narrow handler placed AFTER the broad one) is "
        f"UNREACHABLE and must read as NO coverage: {broad_first}"
    )

    # ...and a broad arm that RE-RAISES swallows nothing, so it is not the
    # class-2 shape and must not be redded. `except Exception: cleanup(); raise`
    # is a probe that already fails loudly (`tests/e2e/hosted/conftest.py`).
    reraised = rows(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK as _ProbeSDK
            _ProbeSDK()
            FALKORDB_AVAILABLE = True
        except Exception:
            _cleanup()
            raise
        """
    )
    assert reraised == ((3, (), False, True, True),), (
        "a broad arm that re-raises does not SWALLOW: `swallows` must be False, "
        f"or the gate reds a block that is already loud: {reraised}"
    )

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

    # And the two `_AVAILABLE`-spelled families must be IN the population: they
    # were the ones the `FALKORDB_AVAILABLE`-only census could not see, so
    # narrowing the flag set back would silently stop grading the fix that
    # landed in them (review F3(a)/(b)). Pinned by presence, not by a count.
    graded = {row[0] for row in report}
    for rel in (
        "tests/test_13302_ended_read.py",
        "tests/test_13557_13558_reachable_reads.py",
    ):
        assert rel in graded, (
            f"{rel} is absent from the probe census — its `_AVAILABLE, … = "
            "_probe([…])` flag spelling has fallen out of `_AVAILABILITY_FLAGS`, "
            "so the arm in it is unobservable"
        )


def test_the_census_reaches_the_skip_gated_probes_and_every_flag_spelling():
    """Self-test: the third blind spot, and the rule that closes it.

    Two rounds of this repair reported the class closed while probes kept the
    swallowing shape, because the census only knew ONE way a probe is resolved:
    a module-level `try` assigning `FALKORDB_AVAILABLE`, plus a helper that
    assignment calls. Measured at the head before this widening: 9 more probes
    were outside it — one resolving `_LIVE_AVAILABLE` (a third flag SPELLING,
    so a fixed name set graded nothing), three resolved by a `skipif` that calls
    the probe directly, and five `_has_embedded` helpers that import own code
    inside the guard. "A silent drop is a fail" (#14125), so both directions are
    pinned here: what the census must now grade, and what it must NOT.
    """
    def roots(src: str) -> tuple[tuple[int, tuple[str, ...], bool, bool, bool], ...]:
        return _probe_blocks_from_source(textwrap.dedent(src))

    # 1. A third flag SPELLING is the same decision — `_LIVE_AVAILABLE` in
    #    `tests/test_falkordb_compat.py` was graded by nothing under a fixed set.
    respelled = roots(
        """
        from tortoise.projection import FalkorProjection as _FP
        _LIVE_AVAILABLE = False
        try:
            _probe = _FP.from_uri("redis://x")
            _probe.g.query("RETURN 1")
            _LIVE_AVAILABLE = True
        except Exception:
            _LIVE_AVAILABLE = False
        """
    )
    assert respelled == ((4, (), True, False, True),), (
        "a `*_AVAILABLE` flag spelling must be graded like `FALKORDB_AVAILABLE`: "
        f"{respelled}"
    )
    for name in ("FALKORDB_AVAILABLE", "_AVAILABLE", "_LIVE_AVAILABLE", "AVAILABLE"):
        assert _is_availability_flag(name), f"{name} must read as an availability flag"
    assert not _is_availability_flag("EMBEDDER_UNAVAILABLE_REASONS")
    # `"UNAVAILABLE".endswith("AVAILABLE")` is True — the negation is the
    # OPPOSITE decision (`PROBE_UNAVAILABLE = _probe()` in `tools/cmux_dispatch.py`
    # is the "cannot use it" flag) and must not be graded as an availability flag.
    assert not _is_availability_flag("PROBE_UNAVAILABLE")
    negation = roots(
        """
        PROBE_UNAVAILABLE = _probe_unavailable()

        def _probe_unavailable():
            try:
                from tortoise.sdk import TortoiseSDK
                return TortoiseSDK()
            except Exception:
                return False
        """
    )
    assert negation == (), (
        f"a `*_UNAVAILABLE` flag is not an availability decision: {negation}"
    )

    # 2. A helper CALLED from a module-level `skipif` decides a skip at
    #    collection — same class, no flag anywhere.
    called = roots(
        """
        import pytest

        def _live():
            try:
                from tortoise.sdk import TortoiseSDK
                TortoiseSDK()
                return True
            except Exception:
                return False

        @pytest.mark.skipif(not _live(), reason="live")
        def test_live():
            pass
        """
    )
    assert called == ((5, (), True, True, True),), (
        f"a skipif-called probe with an own-code import must be graded: {called}"
    )

    # 3. …and a helper ASSIGNED to a name the condition reads (`LIVE = _live()`).
    assigned = roots(
        """
        import pytest

        def _live():
            try:
                from tortoise.sdk import TortoiseSDK
                return True
            except Exception:
                return False

        LIVE = _live()

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert assigned == ((5, (), True, True, True),), (
        f"a skipif-referenced `LIVE = _probe()` resolution must be graded: {assigned}"
    )

    # 3b. …including a TUPLE target (`LIVE, URI = _probe()`), which a target walk
    #     that only reads `ast.Name` nodes misses.
    tupled = roots(
        """
        import pytest

        def _live():
            try:
                from tortoise.sdk import TortoiseSDK
                return True, "redis://x"
            except Exception:
                return False, None

        LIVE, URI = _live()

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert tupled == ((5, (), True, True, True),), (
        f"a tuple-target `LIVE, URI = _probe()` resolution must be graded: {tupled}"
    )

    # 3c. A hoisted-import probe whose guarded body CALLS the own-code door is
    #     still the seam — `tests/eval/retrieval/test_1348.py` builds a
    #     `TortoiseSDK` inside its guard — and it needs the three door errors,
    #     not `ImportError` (nothing is imported inside its own `try`).
    hoisted_call = roots(
        """
        import pytest

        def _has_embedded():
            from tortoise.sdk import TortoiseSDK
            try:
                TortoiseSDK().close()
                return True
            except (AttributeError, NameError, TypeError):
                raise
            except Exception:
                return False

        @pytest.mark.skipif(not _has_embedded(), reason="embedded")
        def test_embedded():
            pass
        """
    )
    assert hoisted_call == ((6, ("AttributeError", "NameError", "TypeError"), True, False, True),), (
        f"a hoisted-import probe that CALLS the door must be graded: {hoisted_call}"
    )
    assert _required_names(False) == _RAISED - {"ImportError"}
    assert _required_names(True) == _RAISED

    # 3d. …and its arm must RE-RAISE: naming the exceptions in the clause is not
    #     the property, or `except (…): FLAG = False` would satisfy the gate with
    #     exactly the swallow it exists to forbid.
    swallowed = roots(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.sdk import TortoiseSDK
            TortoiseSDK()
            FALKORDB_AVAILABLE = True
        except (AttributeError, NameError, TypeError, ImportError):
            FALKORDB_AVAILABLE = False
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert swallowed == ((3, (), True, True, True),), (
        "a narrow arm that does NOT re-raise is not coverage — it names the "
        f"programming error and swallows it: {swallowed}"
    )

    # 4. An OPTIONAL third-party capability is still out of scope: a skip is the
    #    correct outcome for an absent extra, so it must NOT be redded.
    optional = roots(
        """
        import pytest

        def _model_cached():
            try:
                import sentence_transformers
                sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
                return True
            except Exception:
                return False

        @pytest.mark.skipif(not _model_cached(), reason="model")
        def test_model():
            pass
        """
    )
    assert optional == (), (
        "a third-party-only capability probe must not be graded — demanding a "
        f"re-raise there turns a legitimate skip into an error: {optional}"
    )

    # 4b. The repaired `_has_embedded` shape: own code HOISTED above the guard,
    #     the optional third-party extra left inside it. Grading this would
    #     demand an `ImportError` re-raise for `redislite`, turning a legitimate
    #     embedded skip into a collection error — so the census must leave it be.
    hoisted = roots(
        """
        import pytest

        def _has_embedded():
            from tortoise.projection import FalkorProjection  # noqa: F401
            try:
                import redislite.falkordb_client  # noqa: F401
                return True
            except Exception:
                return False

        @pytest.mark.skipif(not _has_embedded(), reason="embedded")
        def test_embedded():
            pass
        """
    )
    assert hoisted == (), (
        "the hoisted-own-import shape must not be graded — its only import in "
        f"the `try` is the optional extra: {hoisted}"
    )

    # 3e. A helper that takes the CLIENT as a PARAMETER reaches the seam through
    #     `proj.g.query(…)`, and neither own-code test can see it: nothing is
    #     imported in the block and no own-code name is reachable from it. Its
    #     sentinel becomes a SKIP, so the swallow skips the proof green over a
    #     renamed seam (measured: `tests/test_8870_version_anchor_write_order.py`,
    #     where `SET_ORDER_UNMEASURED` -> `ORDER_UNMEASURED_SKIP_REASON`).
    passed_client = roots(
        """
        def _probe(proj) -> str:
            try:
                proj.g.query("RETURN 1")
            except Exception:
                return "unmeasured"
            return "measured"

        def _skip_reason(semantics):
            return None if semantics == "measured" else "unmeasured"

        _reason = _skip_reason(_probe(None))
        if _reason is not None:
            pytest.skip(_reason)
        """
    )
    assert passed_client == ((3, (), True, False, True),), (
        "a probe reaching the seam on a PARAMETER client is a ROOT even with no "
        f"own-code import or name in the block: {passed_client}"
    )

    # 4c. SHAPE (b): a MODULE-BODY flag read by a skip condition, whatever it is
    #     called. The skipif widening was consulted only for `def` statements, so
    #     `LIVE` (not `*AVAILABLE`) was rejected by the spelling rule and never
    #     considered here — four live probes were graded by nothing (review of
    #     76abdca, P1). Deleting this branch reddens exactly this assertion.
    skip_read = roots(
        """
        import pytest

        LIVE = False
        try:
            from tortoise.sdk import TortoiseSDK
            TortoiseSDK()
            LIVE = True
        except Exception:
            LIVE = False

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert skip_read == ((5, (), True, True, True),), (
        "a module-body flag READ BY A SKIP CONDITION is the availability decision "
        f"whatever it is named — grading it only under `*AVAILABLE` misses it: {skip_read}"
    )

    # 4d. SHAPE (e): a SENTINEL helper — `URI = _live_target()` … `if URI is None:
    #     pytest.skip(…)`. Neither a flag nor a `skipif` condition reaches it, and
    #     the file need not mention either word (that is why
    #     `tests/test_9226_dump_paging_v6.py` was never parsed). Deleting
    #     `_skip_guard_call_names` reddens exactly this assertion.
    sentinel = roots(
        """
        import pytest

        def _live_target():
            try:
                from tortoise.sdk import TortoiseSDK
                TortoiseSDK()
                return "redis://x"
            except Exception:
                return None

        URI = _live_target()
        if URI is None:
            pytest.skip("no live")

        def test_live():
            pass
        """
    )
    assert sentinel == ((5, (), True, True, True),), (
        "a helper whose SENTINEL decides a skip is the availability decision — "
        f"neither a flag nor a `skipif` reaches it: {sentinel}"
    )

    # 4c-bis. A module-body guard NESTED in module-level control flow. The helper
    #     branches always descended control flow, the module level did not — so
    #     `tests/test_assembly_pure.py:2615`'s probe inside `if _DOCKER_URI:` got
    #     NO row at all and the census read GREEN over an unarmed probe with 19
    #     tests behind it (review of 3a363eb1a3a). The UN-nested source below is
    #     the control: it was already graded, so the pair isolates module-level
    #     control-flow descent as the thing that was missing.
    flat_guard = roots(
        """
        import pytest

        _DOCKER_URI = "redis://x"
        _FALKORDB_UP = False
        try:
            import tortoise.sdk as _sdkmod
            _sdkmod.TortoiseSDK().close()
            _FALKORDB_UP = True
        except Exception:
            _FALKORDB_UP = False

        @pytest.mark.skipif(not (_DOCKER_URI and _FALKORDB_UP), reason="docker")
        def test_docker():
            pass
        """
    )
    assert flat_guard == ((6, (), True, True, True),), (
        "the module-body guard outside control flow is the CONTROL: it must be "
        f"graded exactly as before: {flat_guard}"
    )
    nested_guard = roots(
        """
        import pytest

        _DOCKER_URI = "redis://x"
        _FALKORDB_UP = False
        if _DOCKER_URI:
            try:
                import tortoise.sdk as _sdkmod
                _sdkmod.TortoiseSDK().close()
                _FALKORDB_UP = True
            except Exception:
                _FALKORDB_UP = False

        @pytest.mark.skipif(not (_DOCKER_URI and _FALKORDB_UP), reason="docker")
        def test_docker():
            pass
        """
    )
    assert nested_guard == ((7, (), True, True, True),), (
        "a guard inside a module-level `if` must be a ROOT too — the flag is read "
        f"by a `skipif`, so it is the decision whatever the nesting: {nested_guard}"
    )

    # 4e. The same descent, one wrapper out: a probe under a HANDLER-LESS
    #     `try/finally`. Not a cleanup try (it has no `except`), so it is a pure
    #     wrapper and the guard inside it is the decision — but the walk stopped
    #     at ANY `try`, so `tests/test_integration_search.py:45` (a module-level
    #     probe binding `FALKORDB_AVAILABLE`, which `skipif` reads) produced no
    #     row at all. The `flat_guard` above is the control: its probe is the
    #     same three lines without the wrapper, so the pair isolates the WRAPPER.
    wrapped_guard = roots(
        """
        import pytest

        _FALKORDB_UP = False
        try:
            try:
                import tortoise.sdk as _sdkmod
                _sdkmod.TortoiseSDK().close()
                _FALKORDB_UP = True
            except Exception:
                _FALKORDB_UP = False
        finally:
            pass

        @pytest.mark.skipif(not _FALKORDB_UP, reason="docker")
        def test_docker():
            pass
        """
    )
    assert wrapped_guard == ((6, (), True, True, True),), (
        "a guard under a handler-less `try/finally` is a pure WRAPPER, not a "
        f"cleanup: the probe inside it is still the decision: {wrapped_guard}"
    )

    # 4g. A helper that reuses a MODULE-LEVEL own import is still the seam: the
    #     door is read through the guard, which is exactly what `_touches_own_code`
    #     promises. Grading only the helper's OWN imports left this shape ungraded
    #     with the census green, and `from tortoise.sdk import TortoiseSDK` at
    #     module scope is the COMMON spelling (review of 3ce7ffdd867, P2).
    module_scope_door = roots(
        """
        from tests._live_utils import docker_uri

        def _probe():
            try:
                docker_uri("tortoise_test_x")
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe()
        """
    )
    assert module_scope_door == ((5, (), True, False, True),), (
        "a helper reading an own-code name imported at MODULE scope must be "
        f"graded — the import is above the guard, not inside it: {module_scope_door}"
    )

    # 4h. The SAME rule on the OTHER helper branch (skip-gated, not resolver): a
    #     `skipif`-called helper that calls an own-code name imported at module
    #     scope. The resolver witness above cannot stand in for this one — at the
    #     head that added the module set, the resolver branch gained ZERO rows while
    #     BOTH new roots landed here (`tests/test_cross_lens.py:400`,
    #     `tests/test_wipe_server.py:32`), so reverting only this branch was a
    #     silent drop with all eight tests green (review of 0cc63c8de4da, P1).
    module_scope_door_skipif = roots(
        """
        import pytest
        from tests._live_utils import docker_uri

        def _live():
            try:
                docker_uri("tortoise_test_x")
                return True
            except Exception:
                return False

        @pytest.mark.skipif(not _live(), reason="live")
        def test_live():
            pass
        """
    )
    assert module_scope_door_skipif == ((6, (), True, False, True),), (
        "a SKIPIF-called helper reading a module-scope own-code name must be "
        f"graded too, or its branch's half of the rule is unwitnessed: "
        f"{module_scope_door_skipif}"
    )

    # 4i/4j/4k. ONE isolating witness per admission arm, because a rule whose arm
    #     can be deleted with all tests green is the silent-drop class this gate
    #     exists to forbid — the review of 4478e90782d measured that each of these
    #     three could be blanked with the population unchanged.
    #
    #     The FIRST-CONTACT arms below are `_touches_own_code`, so the IMPORT half of
    #     BOTH module-body filters needs its own rows (4i-bis, 4i-ter) — one row cannot
    #     witness two conditions, because each guard reaches only one of them — and the shape the
    #     removed capability OR used to admit is RESTORED by rule rather than pinned as a
    #     gap (4m states the closure; 4k+ is its positive witness) — all three from the
    #     review of 90a0e9e8b20c (P2), the second row from the review of 994fb1d52f (P2).
    #
    # 4i — the module-body `*AVAILABLE` branch's OWN-CODE contact: a module-scope
    #      own import is the only seam, and the flag is an availability flag, so
    #      only this arm can admit it.
    module_body_own_contact = roots(
        """
        from tests._live_utils import docker_uri

        FALKORDB_AVAILABLE = False
        try:
            docker_uri("tortoise_test_x")
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert module_body_own_contact == ((5, (), True, False, True),), (
        "a module-body availability guard whose only seam is a MODULE-scope own "
        f"import must be graded by the own-code contact arm: {module_body_own_contact}"
    )
    # 4j — the module-body SKIP-READ branch: the same contact, but the flag is not
    #      spelled `*AVAILABLE` — it is a name a `skipif` reads.
    module_body_skip_read = roots(
        """
        import pytest
        from tests._live_utils import docker_uri

        LIVE = False
        try:
            docker_uri("tortoise_test_x")
            LIVE = True
        except Exception:
            LIVE = False

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert module_body_skip_read == ((6, (), True, False, True),), (
        "a module-body guard binding a name a `skipif` READS must be graded even "
        f"without an `*AVAILABLE` spelling: {module_body_skip_read}"
    )
    # 4k — the RESOLVER branch's capability-seam arm: the client is passed in, so
    #      neither an import nor a bound own name is reachable — only the call
    #      shape (`client.g.query`) is.
    resolver_capability_seam = roots(
        """
        def _probe(client):
            try:
                client.g.query("RETURN 1")
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe(None)
        """
    )
    assert resolver_capability_seam == ((3, (), True, False, True),), (
        "a resolver helper whose only seam is the CAPABILITY CALL on a passed-in "
        f"client must be graded: {resolver_capability_seam}"
    )

    # 4i-/4j-. The own-code contact on the two module-body branches is a FILTER, so
    #     its isolating witness is the NEGATIVE one: a module-body availability
    #     guard whose only seam is an optional THIRD-PARTY extra must NOT be graded.
    #     Deleting either contact arm admits these and reddens exactly these rows
    #     (measured after the review of 4478e90782d: both deletions were green
    #     before these two rows existed).
    module_body_third_party = roots(
        """
        TORCH_AVAILABLE = False
        try:
            import torch
            torch.zeros(1)
            TORCH_AVAILABLE = True
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    assert module_body_third_party == (), (
        "a module-body `*AVAILABLE` guard whose only seam is an optional THIRD-PARTY "
        "extra must not be graded — demanding a re-raise there turns a legitimate "
        f"skip into a collection error: {module_body_third_party}"
    )
    module_body_third_party_skip_read = roots(
        """
        import pytest

        LIVE = False
        try:
            import torch
            torch.zeros(1)
            LIVE = True
        except Exception:
            LIVE = False

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert module_body_third_party_skip_read == (), (
        "the same filter must apply to the SKIP-READ branch — a third-party-only "
        f"guard is not the own-code seam: {module_body_third_party_skip_read}"
    )

    # 4i-bis. The IMPORT half of the same two module-body filters. Rows 4i/4j are
    #     admitted by `_touches_own_code` — they CALL a name imported at module scope —
    #     so neither of them exercises `_imports_own_code`, and blanking it from either
    #     condition was a silent drop with every row above green (review of
    #     90a0e9e8b20c, P2). This guard imports own code INSIDE the `try` and binds no
    #     name from it, so no other arm can admit it: measured `((3, (), True, True,
    #     True),)`, which is exactly the import arm.
    module_body_own_import_unused = roots(
        """
        FALKORDB_AVAILABLE = False
        try:
            from tortoise.projection import FalkorProjection  # noqa: F401
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert module_body_own_import_unused == ((3, (), True, True, True),), (
        "a module-body guard that IMPORTS own code inside the `try` must be graded by "
        "the import arm even when it never binds a name from it, or `_imports_own_code` "
        f"can be deleted with every other row green: {module_body_own_import_unused}"
    )
    # 4i-ter. The SAME import arm on the SKIP-READ filter. 4i-bis cannot stand in for
    #     it: a guard that assigns `*AVAILABLE` never reaches the SKIP-READ condition,
    #     so the SKIP-READ filter's `_imports_own_code` term was deletable with the
    #     entire file green (measured by blanking each call site in turn — the
    #     `*AVAILABLE` filter's term blanked reds 4i-bis, the SKIP-READ filter's term
    #     blanked reds this row, and before it existed that deletion was silent;
    #     review of 994fb1d52f, P2). Line numbers are deliberately NOT cited here:
    #     they move whenever this function is reflowed, and a stale cite reads to the
    #     next lane as proof the claim is false (review of 291f27963787, P2 — an
    #     earlier text did cite `:895`/`:914`, which stopped being the two call sites
    #     the moment `61fc96cdd8aa` reflowed them). Measured `((5, (), True, True, True),)`; the import
    #     INSIDE the `try` binds no `Name`, and there is no capability call, so no other
    #     arm admits it.
    module_body_own_import_unused_skip_read = roots(
        """
        import pytest

        LIVE = False
        try:
            from tortoise.projection import FalkorProjection  # noqa: F401
            LIVE = True
        except Exception:
            LIVE = False

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert module_body_own_import_unused_skip_read == ((5, (), True, True, True),), (
        "the SKIP-READ filter's import arm needs its OWN witness — the `*AVAILABLE` "
        "row above cannot reach that condition, so its `_imports_own_code` term stayed "
        f"deletable with every row green: {module_body_own_import_unused_skip_read}"
    )

    # 4j-bis. The SKIP-READ filter's CAPABILITY arm — the one term 4i-ter cannot reach.
    #     `_imports_own_code` and `_touches_own_code` are both False here (the client is
    #     built at module scope, nothing own is imported, nothing own is touched), so the
    #     narrowed capability arm is the ONLY admitter. Deleting it left all eight tests
    #     green with ZERO rows changed on the real tree — measured independently, twice
    #     (two fresh reviewers, same finding), so it was a silent drop of exactly the
    #     class this gate exists to forbid. Measured `((6, (), True, False, True),)` at
    #     this head, `()` with the term removed.
    module_body_skip_read_capability = roots(
        """
        import pytest

        PROJ = _make_proj()
        LIVE = False
        try:
            PROJ.g.query("RETURN 1")
            LIVE = True
        except Exception:
            LIVE = False

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert module_body_skip_read_capability == ((6, (), True, False, True),), (
        "the SKIP-READ filter's narrowed CAPABILITY arm has no other witness — its "
        "sibling terms are covered by 4i/4j/4i-ter, so without this row that arm can be "
        f"deleted with the whole file green: {module_body_skip_read_capability}"
    )

    # 4j-ter. A third-party NAME that is RE-BOUND to our client is not an exemption.
    #     The narrowing keyed on a name a non-own import bound ANYWHERE in the file, so
    #     `falkordb = proj` inside the probe still bought the exemption while the name
    #     held OUR client — graded at `994fb1d52f`, ungraded from `61fc96cd` (the
    #     narrowing's regression, measured on both heads with the no-import control
    #     unchanged). `_third_party_receivers` now subtracts re-bound names. Measured
    #     `((5, (), True, False, True),)`.
    third_party_name_rebound_to_our_client = roots(
        """
        import falkordb

        def _probe(proj):
            try:
                falkordb = proj
                falkordb.g.query("RETURN 1")
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe(None)
        """
    )
    assert third_party_name_rebound_to_our_client == ((5, (), True, False, True),), (
        "a name bound by a non-own import and then RE-BOUND to our own client is not "
        "evidence of a third-party verb at the call site — exempting it re-opens the "
        f"silent drop the narrowing opened: {third_party_name_rebound_to_our_client}"
    )

    # 4j-quater. "RE-BOUND" IS EVERY PYTHON BINDING CONSTRUCT, not only the ones
    #     `ast.Name` spells. `_third_party_receivers` subtracted names bound by a Store
    #     target and by formal parameters, so a module that re-bound the third-party
    #     name with `def` / `class` / `except … as` / a `match` capture still bought the
    #     exemption for a `.query` on OUR client — the same silent drop 4j-ter pins, one
    #     construct family over (review P2; the same walk over tests/+tools/ found 0
    #     live instances in 1,828 files, so this is a LATENT hole the docstring already
    #     claimed was closed, not a live drop).
    #     ⛔ ONE ROW PER FAMILY, because 4j-ter pins the Store branch ONLY: a mutation
    #     that reverts a single family must red ITS row and leave the rest green. The
    #     trailing control is the other direction — without it these rows would also
    #     pass if the exemption had been dropped outright (which 4k-/4l- forbid).
    _rebind_prelude = "import falkordb\n\n"
    _rebind_guard = textwrap.dedent(
        """
        FALKORDB_AVAILABLE = False
        try:
            falkordb.g.query("RETURN 1")
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert roots(_rebind_prelude + _rebind_guard) == (), (
        "the CONTROL: a non-own import name that is NOT re-bound keeps the third-party "
        "exemption — without this row the per-family rows below would also pass on "
        "code that had simply dropped the exemption"
    )
    for _family, _rebinding in {
        # a function definition binds its NAME (a `str` field, not an `ast.Name`)
        "def": "def falkordb():\n    pass",
        # `del name` binds too (§4.2.1) — an `ast.Name` with `Del`, not `Store`, context
        "del": "del falkordb",
        # …and so does a class definition
        "class": "class falkordb:\n    pass",
        # `except E as name` / `except* E as name` bind `ExceptHandler.name`
        "except-as": (
            'try:\n    raise RuntimeError("boom")\nexcept Exception as falkordb:\n    pass'
        ),
        # structural-pattern captures bind `MatchAs` / `MatchStar` / `MatchMapping.rest`
        "match-capture": "match None:\n    case falkordb:\n        pass",
        "match-star": "match (1,):\n    case [*falkordb]:\n        pass",
        "match-rest": "match {}:\n    case {**falkordb}:\n        pass",
    }.items():
        _rebound_row = roots(_rebind_prelude + _rebinding + "\n\n" + _rebind_guard)
        assert len(_rebound_row) == 1 and _rebound_row[0][1:] == ((), True, False, True), (
            f"a `{_family}` re-binding of a non-own import name must NOT buy the "
            "third-party exemption: after it the name no longer denotes the import "
            "where the probe calls `.query` on OUR client, so leaving it exempt is the "
            f"silent drop 4j-ter pins, one construct family over: {_rebound_row}"
        )

    # 4j-quinquies. THE SCOPING IS ITSELF WITNESSED. `_enclosing_bindings` exists so a
    #     binding that cannot REACH the probe does not cancel the exemption — and a
    #     REVERTED (scope-blind) implementation left all eight tests green, i.e. the fix
    #     for an unwitnessed rule was itself unwitnessed (review of #14160, P1). These are
    #     the negative controls: the same probe, with the shadowing binding somewhere that
    #     cannot reach it, must stay EXEMPT (`()`), or a legitimate optional-extra guard is
    #     graded as a ROOT (the false red 4k-/4l- forbid).
    _scope_control_prelude = "import torch\n\n"
    _scope_control_guard = textwrap.dedent(
        """
        TORCH_AVAILABLE = False
        try:
            torch.query("SELECT 1")
            TORCH_AVAILABLE = True
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    for _label, _unrelated in {
        "a parameter of an unrelated helper": "def _helper(torch):\n    return torch",
        # a helper that DOES hold a handler-bearing `try` used to contribute ALL its
        # parameters, so an import-named one cancelled the exemption below. `try: int(x)`
        # decides nothing about a probe — and `_enclosing_bindings` cannot make this
        # mistake, because it resolves the probe's own ancestor chain instead of asking
        # "does this function look like it holds a probe".
        "a parameter of an unrelated helper that holds a try": (
            "def _helper(torch, x):\n"
            "    try:\n"
            "        int(x)\n"
            "    except ValueError:\n"
            "        pass"
        ),
        "an unrelated loop target": "def _helper():\n    for torch in range(3):\n        pass",
        # a comprehension has its OWN scope in Python 3, so its TARGET does not bind here
        "a module-level comprehension target": "_ = [torch for torch in range(3)]",
        # ⛔ A TYPE PARAMETER BINDS IN THE GENERIC'S SCOPE — it reaches that function's
        # BODY (and its nested defs'), and nothing else. `f.__code__.co_freevars ==
        # ('T',)` on 3.12.3: an earlier row here claimed the body does NOT see it and
        # made this a control; that was FALSE, and the positive witness is `4j-decies`
        # below. This row is the scope half: an unrelated generic's `[torch]` cannot
        # reach a MODULE-level probe, so it cancels nothing here.
        "a type parameter list on an unrelated generic": (
            "def _generic[torch](x):\n"
            "    return torch"
        ),
        # the `str`-field families are SCOPED too: an `except … as` in an unrelated
        # helper used to be collected tree-wide and cancelled this exemption (review P2)
        "a capture in an unrelated function": (
            "def _helper():\n"
            "    try:\n"
            '        raise RuntimeError("boom")\n'
            "    except Exception as torch:\n"
            "        pass"
        ),
        # ⛔ REACH IS PER PROBE, NOT PER MODULE (review of #14160, P2). A local of
        # `_other` reaches `_other`'s probe and no further, so it must not cancel the
        # exemption for THIS module-level probe. Folding every probe-holding function's
        # locals into one module-wide set did exactly that — a false red of the same
        # class, one granularity up.
        "a local of ANOTHER probe-holding function": (
            "def _other():\n"
            "    torch = _make_client()\n"
            "    try:\n"
            "        torch.query('SELECT 1')\n"
            "        return True\n"
            "    except Exception:\n"
            "        return False"
        ),
    }.items():
        _scope_row = roots(_scope_control_prelude + _unrelated + "\n\n" + _scope_control_guard)
        assert _scope_row == (), (
            f"{_label} cannot reach this probe, so it must NOT cancel the third-party "
            "exemption — a scope-blind subtraction grades a legitimate optional-extra "
            f"guard as a ROOT, which is the false red 4k-/4l- forbid: {_scope_row}"
        )

    # 4j-octies (cont.) — the module-scope binding families that WALRUS writes into the
    #     ENCLOSING scope. PEP 572 is explicit that `:=` in a comprehension binds in the
    #     containing scope, and a decorator / base / keyword / default expression is
    #     evaluated in the containing scope, so each of these rebinds the module's `torch`
    #     and must cancel the exemption. Every one of them was missed: the comprehension
    #     was skipped wholesale (correct for its TARGETS, wrong for its walrus), and the
    #     module-scope walk `continue`d at `def`/`class` without descending the headers
    #     (`_function_local_bindings` already did, which is why only the module half was a
    #     drop). One row per spelling, so a partial fix reds only the family it missed.
    for _family, _walrus in {
        "comprehension": "_ = [(torch := _make_client()) for _ in range(1)]",
        "decorator": "@_reg(torch := _make_client())\ndef _f():\n    pass",
        "class base": "class C(torch := _make_client()):\n    pass",
        "class keyword": "class C(metaclass=(torch := _make_client())):\n    pass",
        "def default": "def _h(x=(torch := _make_client())):\n    return x",
        "def kw default": "def _h(*, x=(torch := _make_client())):\n    return x",
        # an ANNOTATION is part of the header, evaluated eagerly in the enclosing scope
        "def annotation": "def _h(x: (torch := object())):\n    return x",
        # a lambda BODY is another scope; its DEFAULTS evaluate in the enclosing one
        "lambda default": "_f = lambda x=(torch := _make_client()): x",
    }.items():
        _walrus_row = roots(
            _scope_control_prelude + _walrus + "\n\n" + _scope_control_guard
        )
        assert _walrus_row != (), (
            f"a walrus in a module-level {_family} binds in the CONTAINING scope, so it "
            "rebinds the non-own import and the probe must be graded — leaving it exempt "
            f"is a silent drop (review of #14160, P2): {_walrus_row}"
        )

    # 4j-decies. THE OTHER DIRECTION FOR TYPE PARAMETERS: a generic that HOLDS the probe
    #     DOES shadow the import inside its own body, so its probe must be graded. This is
    #     the row that reds if `_type_param_names` is dropped — and the row whose absence
    #     let a FALSE claim survive a review cycle: a previous version declared the family
    #     "deliberately excluded because the body cannot see it", which `f.__code__
    #     .co_freevars == ('T',)` disproves on 3.12.3. The mechanism of the error is worth
    #     keeping: the probe PRINTED the TypeVar, whose repr is its own name, so the
    #     shadowed read looked exactly like the module-level string.
    _generic_holding_the_probe = textwrap.dedent(
        """
        import torch

        def _probe[torch]():
            try:
                torch.query("SELECT 1")
                return True
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    _generic_row = roots(_generic_holding_the_probe)
    assert _generic_row == ((5, (), True, False, True),), (
        "a TYPE PARAMETER naming the non-own import shadows it inside the generic's own "
        "body (co_freevars, measured), so this probe must be graded — the exemption here "
        f"is a silent drop of the class this file exists to catch: {_generic_row}"
    )

    # 4j-undecies. A `global` RE-BINDING reaches a MODULE-level probe (Language Reference
    #     §4.2.2), and it was invisible to EVERY walk: the module walk never descends a
    #     function body, and the closure chain only holds the probe's own ancestors. Measured
    #     exempt before this row, with the block dropped out of the population entirely — so
    #     `UNARMED == ()` read green over it (review of #14160, P2). The control is the other
    #     direction: a `global` DECLARATION with no binding anywhere is inert and must keep
    #     the exemption, or a legitimate optional-extra probe is graded as a ROOT (4k-/4l-).
    _global_rebind_guard = textwrap.dedent(
        """
        import torch

        def _helper():
            global torch
            torch = _make_client()
            return torch

        TORCH_AVAILABLE = False
        try:
            torch.query("SELECT 1")
            TORCH_AVAILABLE = True
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    _global_rebind_row = roots(_global_rebind_guard)
    assert len(_global_rebind_row) == 1 and _global_rebind_row[0][1:] == ((), True, False, True), (
        "a `global` re-binding puts the name in MODULE scope, so it can reach a "
        "module-level probe and must NOT buy the third-party exemption — missing it "
        "leaves a swallowed rename reading as 'capability unavailable': "
        f"{_global_rebind_row}"
    )
    _bare_global_control = roots(
        textwrap.dedent(
            """
            import torch

            def _helper():
                global torch
                return torch

            TORCH_AVAILABLE = False
            try:
                torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert _bare_global_control == (), (
        "a `global` DECLARATION is not a binding: with no assignment anywhere the name "
        "still denotes the import and the guard must stay exempt — counting the bare "
        f"declaration grades a legitimate third-party probe: {_bare_global_control}"
    )

    # 4j-quattuordecies. THE CLOSURE ASCENT — the mechanism "reach is per probe" rests on
    #     — gets its own witness. `_enclosing_bindings` walks the WHOLE ancestor chain, not
    #     just the innermost `def`, because an outer function's local genuinely reaches a
    #     probe in a nested one (closure). Nothing pinned that before: breaking the ascent
    #     to innermost-only left the entire self-test green (measured, review of #14160,
    #     P2), while `_function_scope_bindings`' own docstring calls itself "the unit of
    #     REACH" — the invitation to make exactly that simplification. The 5th admission
    #     rule's shape (a probe deciding its own skip) is used because a method/closure
    #     probe that decides nothing is not a row at all.
    _outer_local_reaches_nested_probe = textwrap.dedent(
        """
        import pytest
        import torch

        def _outer():
            torch = _make_client()
            def _inner():
                try:
                    _e = torch.query("SELECT 1")
                except Exception:
                    _e = None
                if _e is None:
                    pytest.skip("engine has no capability")
                return _e
            return _inner()

        TORCH_AVAILABLE = _outer()
        """
    )
    _ascent_row = roots(_outer_local_reaches_nested_probe)
    assert _ascent_row == ((8, (), True, False, True),), (
        "an OUTER function's local reaches a probe in a NESTED function through the "
        "closure, so it rebinds the non-own import there and the probe must be graded — "
        f"innermost-def-only reach is a silent drop: {_ascent_row}"
    )

    # 4j-quindecies. THE SAME RULE FOR A GENERIC CLASS, a different node kind that the
    #     function-only walk missed: `class C[torch]` never entered the shadow set, so its
    #     method's `torch.query(…)` kept the exemption — the `4j-decies` silent drop, one
    #     node kind over (review of #14160, P1). PEP 695: a class type parameter "is valid
    #     within the class body and inner scopes contained therein"; measured
    #     `C.m.__code__.co_freevars == ('T',)`.
    #     ⛔ THE TWO CONTROLS ARE THE OTHER HALF, and they are why the fix adds ONLY type
    #     parameters: a CLASS-BODY assignment does not reach a method (§4.2.2 — the class
    #     block's names "do not extend to the code blocks of methods"), so collecting it
    #     would grade a legitimate probe.
    _admitted_method_probe = textwrap.dedent(
        """
        import pytest
        import torch

        class C{generic}:
            {class_body}def probe(self):
                try:
                    _e = torch.query("SELECT 1")
                except Exception:
                    _e = None
                if _e is None:
                    pytest.skip("engine has no capability")

        def _run():
            C().probe()
        """
    )
    _generic_class_row = roots(
        _admitted_method_probe.format(generic="[torch]", class_body="")
    )
    assert _generic_class_row == ((7, (), True, False, True),), (
        "a GENERIC CLASS's type parameter reaches its methods' bodies, so a probe calling "
        "`.query` on that name is not calling the import and must be graded — exempting "
        f"it is the 4j-decies silent drop, one node kind over: {_generic_class_row}"
    )
    for _label, _class_body in (
        ("a plain class", ""),
        ("a class-body assignment", "torch = _make_client()\n    "),
    ):
        _class_control = roots(_admitted_method_probe.format(generic="", class_body=_class_body))
        assert _class_control == (), (
            f"{_label} does not rebind the module's import for a method's probe (§4.2.2: a "
            "class block's names do not extend to the code blocks of methods), so the "
            f"exemption must survive — grading it is the 4k-/4l- false red: {_class_control}"
        )

    # 4j-duodecies. A FORMAL PARAMETER inside a `lambda` in the probe BLOCK is a binding
    #     only the CALL-NODE reach can see (`ast.arg` is not an `ast.Name`, and the module
    #     walk skips a `lambda` body by construction), so `(lambda torch:
    #     torch.query(…))(...)` ran `.query` on OUR client and stayed exempt. It is graded
    #     because `_enclosing_bindings` treats a `lambda` as a function scope (review of
    #     #14160, P2).
    _lambda_param_in_block = textwrap.dedent(
        """
        import torch

        TORCH_AVAILABLE = False
        try:
            TORCH_AVAILABLE = (lambda torch: bool(torch.query("SELECT 1")))(_make_client())
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    _lambda_param_row = roots(_lambda_param_in_block)
    assert len(_lambda_param_row) == 1 and _lambda_param_row[0][1:] == ((), True, False, True), (
        "a lambda parameter named after the non-own import rebinds it inside the block "
        "itself, so the `.query` runs on OUR client and must be graded: "
        f"{_lambda_param_row}"
    )

    # 4j-terdecies. THE CALL-NODE REACH'S OWN WITNESS. The call sits inside a `def` NESTED
    #     in the block, so its enclosing chain is THAT def, not the module — which is why
    #     the rebind reaches the call even though `_module_scope_bindings` never descends a
    #     nested `def`'s BODY. Resolving reach from the BLOCK instead (the shape this
    #     branch carried through two wrong judgements) measures exempt here.
    _rebind_inside_nested_def = textwrap.dedent(
        """
        import falkordb

        FALKORDB_AVAILABLE = False
        try:
            def _run():
                falkordb = _make_client()
                return falkordb.g.query("RETURN 1")

            FALKORDB_AVAILABLE = _run()
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    _rebind_nested_row = roots(_rebind_inside_nested_def)
    assert len(_rebind_nested_row) == 1 and _rebind_nested_row[0][1:] == (
        (),
        True,
        False,
        True,
    ), (
        "a re-binding inside a `def` nested in the probe BLOCK can reach the probe, and "
        "resolving reach from the BLOCK rather than from the CALL leaves the block "
        f"exempt over OUR client: {_rebind_nested_row}"
    )
    # ⛔ THE CONTROL IS THE OTHER DIRECTION OF THE SAME RULE: the nested `def` rebinds the
    #     IMPORT NAME, and the call stays at BLOCK level, where that local CANNOT reach it
    #     (§4.2.2 — a nested `def` is a different block). Block-granularity resolution
    #     graded this legitimate third-party probe as a ROOT, i.e. turned a skip into a
    #     collection error — the 4k-/4l- false red.
    #     ⚠️ THIS INSTANCE IS NOT MAIN-VS-BRANCH: `origin/main` grades it too — its
    #     `_third_party_receivers` subtracts `ast.Name`-Store rebinds with an UNSCOPED
    #     `ast.walk(tree)`, which sees the nested assignment, so main measures
    #     `((5, (), True, False, True),)` here while this tree measures `()` (measured with
    #     each tree's own `_probe_blocks_from_source`). A row's comment must not send the
    #     next reader to test it against main and be contradicted: this row pins the
    #     DIRECTION, and the family where main genuinely differs is the `str`-field one
    #     above (`except … as falkordb` in a nested `def`, call at block level: main `()`,
    #     block-granularity grades it, this tree `()`).
    #     ⛔ MEASURED WITHOUT A COUNT, ON PURPOSE. Putting the block-granularity subtraction
    #     back (the `ast.walk(block)` mechanism the deleted `_rebinds_within` was) reds this
    #     control AND other rows that pin a BLOCK-SCOPED exemption — INCLUDING the CLASS-BODY
    #     family (`_class_body_method_control`, `_class_body_comprehension_control`, and
    #     the `4j-sesvicesimus` nested-class-body row), both iterable rows of
    #     `4j-vicesimus-bis` (`outermost iterable`, `later generator iterable`), and the
    #     header/definition-scope rows. The list is ILLUSTRATIVE, not exhaustive: the
    #     mutation reds whichever row pins a shape the unscoped walk mis-resolves, and the
    #     row set GROWS, which is why this is NAMED BY FAMILY and NOT counted — an explicit
    #     "two" for
    #     the class-body family was already wrong WHEN WRITTEN: the `4j-sesvicesimus`
    #     nested-class-body row was present at that head (a sibling had added it in an
    #     earlier commit, `69a92755b21e`), so it was short on arrival rather than outgrown.
    #     ⛔ NO TOTAL IS RESTATED HERE, and that is the correction, not an omission. This
    #     sentence carried one four times ("one", "three", "four", "five") and each was
    #     falsified by the next commit that added a row: the flip set GROWS with the file,
    #     so any figure in it is stale by construction (review of this PR, P3, four times
    #     over). The method that produces the right figure each time: re-apply the mutant
    #     to a COPY, convert every `assert` into a recording call (an inline
    #     `assert roots(…)` is invisible to a count taken under `-O`, which is how the
    #     first wrong figure was produced), and diff base against mutant over every
    #     classification the self-test makes. That is why the
    #     fail-open row above cannot stand alone (review of #14160, P2: without this the
    #     fix could be reverted with all eight tests green).
    _nested_def_control = roots(
        textwrap.dedent(
            """
            import falkordb

            FALKORDB_AVAILABLE = False
            try:
                def _run():
                    falkordb = _make_client()
                    return falkordb

                falkordb.g.query("RETURN 1")
                FALKORDB_AVAILABLE = True
            except Exception:
                FALKORDB_AVAILABLE = False
            """
        )
    )
    assert _nested_def_control == (), (
        "the CONTROL: a nested `def`'s LOCAL cannot reach a call at BLOCK level, so the "
        "block must stay exempt — grading it is the 4k-/4l- false red this row pins (a "
        "false red `origin/main` happens to share on this instance; the main-vs-branch "
        "family is the `str`-field one): "
        f"{_nested_def_control}"
    )

    # 4j-quattuordecies-bis. THE REMAINING UNWITNESSED RULES ON THE REACH PATH, each of which
    #     survives its own deletion with the whole file green (measured, review of #14160,
    #     cycle 3). Every one is correct today; none was pinned — the unwitnessed-arm defect
    #     class this gate has filed as a P1 twice — and `_function_scope_bindings` was
    #     rewritten from a whole-tree `ast.walk` to the scoped form during this repair, so a
    #     silent regression there is invisible without these rows.
    #
    #     ⛔ THE `_function_local_bindings` HALF IS PINNED HERE TOO, and it was the hole a
    #     later review named twice (Finding 2 of the `298ead5261` review; P2-4 of the
    #     `e29bc97887` review). Each term is load-bearing and each was deletable with the
    #     whole file green — measured on the parent head, one mutant per term, shape →
    #     head/mutant:
    #
    #       def/class NAME add   `def _probe(): def torch(): pass`  graded → `()` (FAIL-OPEN
    #                            over OUR client: the probe's `torch` is no longer the import)
    #       decorator_list       `@_reg(torch := …)` on a nested def    graded → `()`
    #       _header_expressions  `def _inner(x=(torch := …))` nested   graded → `()`
    #
    #     The module-scope twin of the header family is `4j-octies`; that row does NOT reach
    #     these terms, because a probe inside a function is resolved by the function walk and
    #     `_module_scope_bindings` never descends a body. Hence one row per spelling below,
    #     in the scope this walk owns.
    for _label, _source in {
        # formal parameters of the function that HOLDS the probe
        "a positional parameter of the probe-holding function": (
            "import torch\n"
            "def _probe(torch):\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe(None)\n"
        ),
        "a positional-only parameter of the probe-holding function": (
            "import torch\n"
            "def _probe(torch, /):\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe(None)\n"
        ),
        "a keyword-only parameter of the probe-holding function": (
            "import torch\n"
            "def _probe(*, torch):\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe(torch=None)\n"
        ),
        "a kwarg parameter of the probe-holding function": (
            "import torch\n"
            "def _probe(**torch):\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
        "a vararg parameter of the probe-holding function": (
            "import torch\n"
            "def _probe(*torch):\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe(None)\n"
        ),
        # a `returns` annotation evaluates in the enclosing scope, like any argument one
        "a walrus in a `returns` annotation": (
            "import torch\n"
            "def _h() -> (torch := object()):\n"
            "    return None\n"
            "TORCH_AVAILABLE = False\n"
            "try:\n"
            '    torch.query("SELECT 1")\n'
            "    TORCH_AVAILABLE = True\n"
            "except Exception:\n"
            "    TORCH_AVAILABLE = False\n"
        ),
        # a lambda DEFAULT inside the probe-holding function evaluates in THAT scope
        "a walrus in a lambda default inside the probe-holding function": (
            "import torch\n"
            "def _probe():\n"
            "    _f = lambda x=(torch := _make_client()): x\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
        # …and a walrus in a COMPREHENSION there binds that function's scope (PEP 572)
        "a walrus in a comprehension inside the probe-holding function": (
            "import torch\n"
            "def _probe():\n"
            "    _ = [(torch := c) for c in _clients()]\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
        # ⛔ A NESTED FUNCTION'S **NAME** BINDS IN THE PROBE'S OWN SCOPE (Finding 2 of the
        # `298ead5261` review). The BODY is another scope and must not be descended; the
        # NAME is this scope's, so `def torch(): …` before the `try` re-points `torch` and
        # the `.query` is no longer the optional extra. Deleting `out.add(node.name)` left
        # the whole file green while this shape silently read EXEMPT — a FAIL-OPEN over our
        # own client, which is the direction that matters. A `class` is a separate spelling
        # of the same branch, so it gets its own row: a partial fix must red exactly one.
        "a nested `def` name shadowing the import inside the probe-holding function": (
            "import torch\n"
            "def _probe():\n"
            "    def torch():\n"
            "        pass\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
        "a nested `class` name shadowing the import inside the probe-holding function": (
            "import torch\n"
            "def _probe():\n"
            "    class torch:\n"
            "        pass\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
        # …and the nested definition's HEADER evaluates in the probe's scope, so a walrus
        # there rebinds it. Decorators are collected from `decorator_list` and everything
        # else from `_header_expressions` — two terms, two rows, so deleting one reds only
        # its own (P2-4 of the `e29bc97887` review).
        "a walrus in a nested `def`'s decorator inside the probe-holding function": (
            "import torch\n"
            "def _probe():\n"
            "    @_reg(torch := _make_client())\n"
            "    def _inner():\n"
            "        pass\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
        "a walrus in a nested `def`'s default inside the probe-holding function": (
            "import torch\n"
            "def _probe():\n"
            "    def _inner(x=(torch := _make_client())):\n"
            "        return x\n"
            "    try:\n"
            '        torch.query("SELECT 1")\n'
            "        return True\n"
            "    except Exception:\n"
            "        return False\n"
            "TORCH_AVAILABLE = _probe()\n"
        ),
    }.items():
        _reach_row = roots(_source)
        assert _reach_row != (), (
            f"{_label} reaches the block and rebinds the non-own import, so the probe must "
            "be graded — nothing else in the file pins this rule, and a silent revert is "
            f"the swallowed-rename class: {_reach_row}"
        )

    # 4j-quattuordecies-tris. THE SAME CLASS OF GAP, ON THE RULES THE LAST TWO COMMITS ADDED:
    #     each of these three survives its own deletion with the whole file green, and two of
    #     the three then regress BELOW `origin/main` (measured by execution on a mutated copy
    #     of this head, review of #14160):
    #       * the COMPREHENSION-target contribution in `_enclosing_bindings` — with it deleted,
    #         `[torch.query(…) for torch in clients]` reads EXEMPT where `origin/main` grades
    #         it, so the revert is a silent drop AND a regression;
    #       * `_global_scope_bindings`' per-FUNCTION resolution — a `global` in a NESTED def
    #         rebinds the module name (`def _inner(): global torch; torch = …`), and a
    #         `tree.body`-only loop loses it;
    #       * `_global_declarations`' non-descent into nested scopes — the control below, which
    #         reds if an INERT nested `global` starts borrowing the outer function's local.
    #     A row per term, because the comment block above claims this path is pinned.
    _comprehension_target_row = roots(
        textwrap.dedent(
            """
            import torch

            def _probe(clients):
                try:
                    return [torch.query("SELECT 1") for torch in clients]
                except Exception:
                    return False

            TORCH_AVAILABLE = _probe([])
            """
        )
    )
    assert (
        len(_comprehension_target_row) == 1
        and _comprehension_target_row[0][1:] == ((), True, False, True)
    ), (
        "a COMPREHENSION's iteration target binds in the comprehension and nowhere else, "
        "so a call INSIDE it runs on the target — not on the import — and must be graded "
        "(§4.2.2; `origin/main` grades it too, so exempting it is a regression as well as "
        f"a silent drop): {_comprehension_target_row}"
    )
    _nested_global_row = roots(
        textwrap.dedent(
            """
            import torch

            def _outer():
                def _inner():
                    global torch
                    torch = _make_client()
                    return torch
                return _inner()

            TORCH_AVAILABLE = False
            try:
                torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert (
        len(_nested_global_row) == 1 and _nested_global_row[0][1:] == ((), True, False, True)
    ), (
        "a `global` declared and bound inside a NESTED function still rebinds the MODULE "
        "name (§4.2.2), so it reaches a module-level probe and the guard must be graded — "
        "resolving declarations from module-level `def`s alone loses this and `origin/main` "
        f"grades it: {_nested_global_row}"
    )
    _inert_nested_global_control = roots(
        textwrap.dedent(
            """
            import torch

            def _outer():
                def _inner():
                    global torch
                    return torch
                torch = _make_client()
                return _inner()

            TORCH_AVAILABLE = False
            try:
                torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert _inert_nested_global_control == (), (
        "an INERT nested `global` must not borrow the OUTER function's local: that local "
        "is a different scope and cannot reach a module-level probe, so `torch` still "
        "denotes the import and grading it is the `4k-`/`4l-` false red — which is what a "
        "`_global_declarations` that descends into nested scopes produces: "
        f"{_inert_nested_global_control}"
    )

    # 4j-sexies. The OTHER direction: a LOCAL of the function that HOLDS the probe CAN
    #     reach it, so it must be graded. `_function_scope_bindings` supplies it from the
    #     probe's own ancestor chain; before that, a local bound before the block was
    #     subtracted by neither the module set nor a block-local term, so the exemption
    #     survived over OUR client (review of #14160, P1-2).
    _local_shadowing_the_import = textwrap.dedent(
        """
        import torch

        def _probe():
            torch = _make_client()
            try:
                torch.query("SELECT 1")
                return True
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    assert roots(_local_shadowing_the_import) != (), (
        "a LOCAL of the probe-holding function can reach the block, so it rebinds the "
        "non-own import and the guard must be graded — exempting it leaves a swallowed "
        f"rename reading as 'capability unavailable': {roots(_local_shadowing_the_import)}"
    )

    # 4j-septies. The line between 4j-sexies and 4j-quinquies: a local of a NESTED
    #     function cannot reach the outer function's probe, so it must NOT cancel the
    #     exemption. `_function_local_bindings` claims non-descent and implements it; the
    #     version before it walked the whole function with `ast.walk`, which descended —
    #     the same false red as the unrelated helper, one level in (review of #14160 on the
    #     over-correction).
    _nested_scope_control = textwrap.dedent(
        """
        import torch

        def _probe():
            def _inner():
                torch = _make_client()
                return torch

            try:
                torch.query("SELECT 1")
                return True
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    assert roots(_nested_scope_control) == (), (
        "a NESTED function's local is a different scope and cannot shadow the name the "
        "outer function's probe reads, so it must not cancel the third-party exemption "
        "— descending into the nested body grades a legitimate optional-extra guard as "
        f"a ROOT: {roots(_nested_scope_control)}"
    )

    # 4j-septies-bis. THE OTHER DIRECTION OF THE COMPREHENSION TERM, in the FUNCTION walk.
    #     `4j-quinquies`' "a module-level comprehension target" pins the MODULE half only —
    #     a probe inside a function is resolved by `_function_local_bindings`, which is a
    #     different walk with its own comprehension branch. Deleting THAT branch left the
    #     whole file green while this shape flipped to GRADED (measured on the parent head:
    #     `()` at head, `((6, (), True, False, True),)` with the branch removed), i.e. the
    #     fix for an unwitnessed rule was itself unwitnessed — the P1 class twice filed.
    #     Finding 2 of the `298ead5261` review named this direction explicitly.
    _fn_comprehension_target_control = textwrap.dedent(
        """
        import torch

        def _probe():
            _ = [torch for torch in range(3)]

            try:
                torch.query("SELECT 1")
                return True
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    assert roots(_fn_comprehension_target_control) == (), (
        "a comprehension has its own scope in Python 3, so a FUNCTION-level iteration "
        "target cannot shadow the name the enclosing function's probe reads — descending "
        "it cancels the exemption for a legitimate optional-extra guard and grades a "
        f"non-probe as a ROOT: {roots(_fn_comprehension_target_control)}"
    )

    # 4j-sexdecies. AN IMPORT STATEMENT NEVER CANCELS THE EXEMPTION — NOT EVEN AN
    #     ALIASED ONE, and not a `from … import … as`. The RIGHT-HAND SIDE OF AN IMPORT
    #     IS ALWAYS AN IMPORT, so the name still denotes an imported object after it,
    #     which is this exemption's premise; `_non_own_imported_names` is NAME-keyed, so
    #     it holds the alias itself. The tempting rule ("the alias binds a DIFFERENT
    #     name, so the name left the import set") is a FALSE RED of the `4k-`/`4l-`
    #     class — measured on the parent head: `import falkordb as fdb` … `fdb.query(…)`
    #     is `()` without it and `((6, (), True, False, True),)` with it. TWO fresh
    #     reviewers reached the same conclusion from opposite directions, so the rule
    #     needs a NEGATIVE row, not a default. The OWN-CODE half is the line it must not
    #     cross, and it needs no import rule at all: the own-import arm grades it.
    _aliased_third_party_import_in_function = textwrap.dedent(
        """
        import torch

        def _probe():
            import json as torch
            try:
                torch.query("SELECT 1")
                return True
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    assert roots(_aliased_third_party_import_in_function) == (), (
        "a local ALIASED import of a non-own module still binds the name to an "
        "IMPORTED object, so the receiver is that optional extra and keeps the "
        "exemption — collecting the alias grades a legitimate skip as a ROOT (the "
        f"false red 4k-/4l- forbid): {roots(_aliased_third_party_import_in_function)}"
    )
    _aliased_third_party_import_in_block = textwrap.dedent(
        """
        import torch

        TORCH_AVAILABLE = False
        try:
            import json as torch
            torch.query("SELECT 1")
            TORCH_AVAILABLE = True
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    assert roots(_aliased_third_party_import_in_block) == (), (
        "the same alias INSIDE the probe block — a row per scope, because the block "
        "and the enclosing function are resolved by different walks and a fix that "
        f"only exempts one leaves the other red: {roots(_aliased_third_party_import_in_block)}"
    )
    _from_import_alias = textwrap.dedent(
        """
        from falkordb import FalkorDB as F

        def _probe():
            from falkordb import FalkorDB as Client
            try:
                Client.query("RETURN 1")
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe()
        """
    )
    assert roots(_from_import_alias) == (), (
        "`from pkg import Class as Name` is the SAME family — the alias binds a name "
        "to a non-own package's object, so it keeps the exemption in a local scope "
        f"exactly as `import pkg as name` does: {roots(_from_import_alias)}"
    )
    _aliased_own_import = textwrap.dedent(
        """
        import torch

        def _probe():
            import tortoise.sdk as torch
            try:
                torch.query("SELECT 1")
                return True
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    _aliased_own_row = roots(_aliased_own_import)
    assert len(_aliased_own_row) == 1 and _aliased_own_row[0][1:] == ((), True, False, True), (
        "the OTHER direction, and it needs no import rule: an OWN-code alias is graded "
        "by the own-import arm, so the rows above cannot be satisfied by widening the "
        f"exemption — it was also graded at the parent head: {_aliased_own_row}"
    )

    # 4j-octodecies. THE OWN-CODE ROOTS ARE A DECLARED SET, and two repo packages were
    #     missing from it: `battery` and `benchmarks` are first-party code (both carry
    #     `__init__.py`, both import in CI with no extras, `battery/runner/setup.py:334`
    #     defines our own `query` wrapper), so `import battery.runner.setup as torch` …
    #     `torch.query(…)` is OUR seam and must be graded. Under the three-name root set
    #     it read EXEMPT — a fail-open the import-rule removal exposed, because the
    #     own-import arm is what grades this family (final review of #14160, P2).
    for _own_pkg in ("battery.runner.setup", "benchmarks.synthetic_corpus"):
        _repo_alias_row = roots(
            textwrap.dedent(
                f"""
                import falkordb as torch

                def _probe():
                    import {_own_pkg} as torch
                    try:
                        torch.query("SELECT 1")
                        return True
                    except Exception:
                        return False

                TORCH_AVAILABLE = _probe()
                """
            )
        )
        assert len(_repo_alias_row) == 1 and _repo_alias_row[0][1:] == ((), True, False, True), (
            f"`{_own_pkg}` is REPO-OWNED code, so the alias rebinds the receiver to OUR "
            "seam and the guard must be graded — leaving it exempt is a silent drop "
            "over an own-code rename, the class this census exists to catch: "
            f"{_repo_alias_row}"
        )

    # 4j-novodecies. A CLASS BODY IS TWO RULES (§4.2.2): its names reach a use DIRECTLY in
    #     that block, and do NOT reach a method body. Only the second half was
    #     implemented, so a class-body rebind and a call in that same body read EXEMPT
    #     over OUR client — a fail-open the call-node refactor introduced (`3b7f3e57d426`
    #     graded it; measured `()` at the head of #14160 before this, `((5, (), True,
    #     False, True),)` after) (final review of #14160, P2). The METHOD control is the
    #     other half and must stay `()`.
    _class_body_rebind_graded = textwrap.dedent(
        """
        import falkordb as torch

        def _probe(proj):
            try:
                class C:
                    torch = proj
                    torch.query("RETURN 1")
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe(None)
        """
    )
    _class_body_row = roots(_class_body_rebind_graded)
    assert len(_class_body_row) == 1 and _class_body_row[0][1:] == ((), True, False, True), (
        "a binding in a class body reaches a use in that SAME block (§4.2.2: a class "
        "block's names are limited to that block), so the `.query` runs on the "
        f"rebound client and must be graded: {_class_body_row}"
    )
    _class_body_method_control = textwrap.dedent(
        """
        import falkordb as torch

        def _probe(proj):
            try:
                class C:
                    torch = proj
                    def m(self):
                        return torch.query("RETURN 1")
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe(None)
        """
    )
    assert roots(_class_body_method_control) == (), (
        "the CONTROL: a class-body name does NOT reach a METHOD body, so the same shape "
        "is not evidence the method's receiver left the import — grading it is the "
        f"`4k-`/`4l-` false red: {roots(_class_body_method_control)}"
    )
    _class_body_comprehension_control = textwrap.dedent(
        """
        import falkordb as torch

        def _probe():
            try:
                class C:
                    torch = object()
                    rows = [torch.query("RETURN 1") for _ in range(1)]
                return True
            except Exception:
                return False

        FALKORDB_AVAILABLE = _probe()
        """
    )
    assert roots(_class_body_comprehension_control) == (), (
        "the THIRD scope, and the one the first version of this rule got wrong: a "
        "COMPREHENSION in a class body cannot read the class-body names — CPython "
        "raises NameError (`class C: torch = object(); rows = [torch for _ in …]`), so "
        "the receiver is still the module import and grading it is a false red. A "
        "comprehension inside a FUNCTION keeps seeing that function's locals, which is "
        "why the barrier gates only the class-body half: "
        f"{roots(_class_body_comprehension_control)}"
    )
    _function_local_through_comprehension = textwrap.dedent(
        """
        import falkordb as torch

        FALKORDB_AVAILABLE = False
        try:
            def _outer():
                torch = _make_client()
                class C:
                    rows = [torch.query("RETURN 1") for _ in range(1)]
                return True
            FALKORDB_AVAILABLE = _outer()
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    _through_row = roots(_function_local_through_comprehension)
    assert len(_through_row) == 1 and _through_row[0][1:] == ((), True, False, True), (
        "the OTHER half of the asymmetry: a comprehension in a class body DOES see the "
        "enclosing FUNCTION's locals (measured), so a `torch` bound by `_outer` still "
        "reaches the call and the guard must be graded — a barrier that suppressed "
        f"function scopes too would re-open the silent drop: {_through_row}"
    )

    # 4j-vicesimus. THE COMPREHENSION TARGET IS ITSELF A WALK, and the
    #     row for it was missing: the comprehension branch adds the iteration targets (a
    #     call INSIDE `[torch.query(…) for torch in clients]` runs on the target, not the
    #     import), and deleting those five lines left the whole file green while restoring
    #     a silent drop that `origin/main` graded (final review of #14160, P2). The class
    #     half of the same branch is the opposite direction and is pinned just above.
    _comprehension_target_rebind = textwrap.dedent(
        """
        import torch

        def _probe():
            clients = _clients()
            try:
                rows = [torch.query("SELECT 1") for torch in clients]
                return bool(rows)
            except Exception:
                return False

        TORCH_AVAILABLE = _probe()
        """
    )
    _comp_target_row = roots(_comprehension_target_rebind)
    assert len(_comp_target_row) == 1 and _comp_target_row[0][1:] == ((), True, False, True), (
        "the comprehension's ITERATION TARGET binds inside the comprehension, so a call "
        "inside it runs on the target rather than on the import — exempting it leaves a "
        "swallowed rename reading as 'capability unavailable': "
        f"{_comp_target_row}"
    )

    # 4j-vicesimus-bis. THE OTHER DIRECTION OF THE SAME TARGET, and it is where the
    #     subtraction was too WIDE: a generator's ITERABLE is evaluated one scope out, so a
    #     call there reads the import, not the target — measured,
    #     `[None for clients in clients.query()]` resolves `clients` from the enclosing
    #     scope (an unbound iterable is a `NameError`, not a `TypeError`).
    #     Subtracting every target unconditionally graded this shape: a 4k-/4l- false red
    #     that would refuse a legitimate optional-extra probe (review of #14160, cycle 5).
    for _label, _body in (
        ("outermost iterable", "len([None for torch in torch.query('SELECT 1')])"),
        (
            "later generator iterable",
            "len([None for _x in [1] for torch in torch.query('SELECT 1')])",
        ),
    ):
        _outermost_row = roots(
            textwrap.dedent(
                f"""
                import torch

                TORCH_AVAILABLE = False
                try:
                    _r = {_body}
                    TORCH_AVAILABLE = True
                except Exception:
                    TORCH_AVAILABLE = False
                """
            )
        )
        assert _outermost_row == (), (
            "a comprehension's own target is NOT bound in its iterable, so the call there "
            f"still runs on the import and must stay EXEMPT ({_label}): {_outermost_row}"
        )
    # …and the paired positive, so the row above cannot be satisfied by deleting the
    # comprehension branch: `elt` and the `ifs` DO see the target.
    for _label, _body in (
        ("elt", "[torch.query('SELECT 1') for torch in _clients()]"),
        ("ifs", "[None for torch in _clients() if torch.query('SELECT 1')]"),
    ):
        _inside_row = roots(
            textwrap.dedent(
                f"""
                import torch

                TORCH_AVAILABLE = False
                try:
                    _r = {_body}
                    TORCH_AVAILABLE = True
                except Exception:
                    TORCH_AVAILABLE = False
                """
            )
        )
        assert len(_inside_row) == 1, (
            f"a call in the comprehension's {_label} DOES run on the bound target, so it "
            f"must be graded — the control for the outermost-iterable row: {_inside_row}"
        )

    # 4j-tervicesimus. A CALL IN THE HEADER IS NOT IN THE DEFINITION'S SCOPE. Every header
    #     expression — decorator, base, keyword, default, annotation — evaluates BEFORE the
    #     new scope exists (§4.2.1), which the walrus direction already pins
    #     (`_header_expressions`); the REACH direction charged the header call with the
    #     definition's own bindings, so a legitimate probe was graded. Measured on the two
    #     spellings a reviewer found: `try: def _inner(x=torch.query(…)): torch =
    #     _make_client()` and the same with a class BASE both read GRADED at the parent head
    #     (review of #14160, cycle 6, P2-5). One row per header family, because a partial fix
    #     must red exactly the family it missed, plus the body control that stays graded.
    _header_reach_probe = (
        "import torch\n"
        "try:\n"
        "    {header}\n"
        "    TORCH_AVAILABLE = True\n"
        "except Exception:\n"
        "    TORCH_AVAILABLE = False\n"
    )
    for _label, _header in {
        "a default": 'def _inner(x=torch.query("SELECT 1")):\n        torch = _make()',
        "an annotation": 'def _inner(x: torch.query("SELECT 1")):\n        torch = _make()',
        "a decorator": '@torch.query("SELECT 1")\n    def _inner():\n        torch = _make()',
        "a class base": 'class _Inner(torch.query("SELECT 1")):\n        torch = _make()',
        "a class keyword": (
            'class _Inner(metaclass=torch.query("SELECT 1")):\n        torch = _make()'
        ),
        #: A `lambda`'s DEFAULT evaluates in the enclosing scope like any other, and its
        #: PARAMETER named after the import is what made the miss visible: the lambda's own
        #: scope was charged to a call that cannot be in it (measured `()` → graded before
        #: the fix). `ast.Lambda` was simply absent from the header test's caller tuple.
        "a lambda default": '(lambda torch=torch.query("SELECT 1"): torch)',
        #: …and a PEP 695 type parameter BOUND, the second family `_header_expressions`'
        #: deliberate exclusion was inherited into.
        "a type-parameter bound": (
            'def _inner[T: torch.query("SELECT 1")]():\n        torch = _make()'
        ),
    }.items():
        _header_row = roots(_header_reach_probe.format(header=_header))
        assert _header_row == (), (
            f"{_label.capitalize()} does not evaluate in the definition's BODY scope, so "
            f"the call there still runs on the import and the probe must stay EXEMPT: "
            f"{_header_row}"
        )
    # ⛔ …BUT A TYPE PARAMETER'S OWN LIST IS IN SCOPE IN ITS BOUND, so the exemption above
    #     must NOT be bought by a type parameter named after the import. PEP 695 puts the
    #     bound in the TYPE-PARAMETER scope: measured, `def f[T, S: T]()` gives
    #     `f.__type_params__[1].__bound__ is T`, and this shape resolves the name to the
    #     TypeVar — observed by EVALUATING the bound, not at definition: PEP 695 bounds are
    #     LAZY, and `exec` of the def/class/type spellings all succeed (measured), the
    #     `AttributeError` arriving on `__bound__`. Skipping the
    #     definition wholesale read it EXEMPT, i.e. a silent drop the widening opened
    #     (review of this PR, P2); `_enclosing_bindings` contributes the list's names now.
    for _label, _generic in {
        "a def": 'def _inner[torch: object, T: torch.query("SELECT 1")]():\n        X = 1',
        "a class": 'class _Inner[torch: object, T: torch.query("SELECT 1")]:\n        X = 1',
    }.items():
        _shadowed_bound_row = roots(_header_reach_probe.format(header=_generic))
        assert len(_shadowed_bound_row) == 1 and _shadowed_bound_row[0][1:] == (
            (),
            True,
            False,
            True,
        ), (
            f"a type parameter named after the import is IN SCOPE in {_label} generic's "
            "own bound (PEP 695), so the `.query` runs on the TypeVar and the probe must "
            f"be graded — exempting it is a silent drop: {_shadowed_bound_row}"
        )
    # ⛔ THE OTHER DIRECTION OF THE SAME SPLIT, and the reason the contribution is SCOPED.
    #     A parameter/return ANNOTATION evaluates in the TYPE-PARAMETER scope, where a type
    #     parameter named after the import shadows it; a DECORATOR or a DEFAULT evaluates in
    #     the ENCLOSING scope, where the type parameters are NOT bound — measured on 3.12.3,
    #     `@deco(W) def f[W: object]()` and `def f[W: object](x=deco(W))` both see the MODULE
    #     `W`. Contributing the list's names to the WHOLE header graded the last two shapes
    #     over a legitimate probe, i.e. a false red and a regression against `origin/main`
    #     (review of this PR, P1). Four rows: two that must stay exempt, two that must be
    #     graded, so a partial revert reds the half it broke.
    for _label, _generic_header, _must_be_graded in (
        (
            "a parameter annotation on a generic",
            'def _inner[torch: object](x: torch.query("SELECT 1")):\n        X = 1',
            True,
        ),
        (
            "a return annotation on a generic",
            'def _inner[torch: object]() -> torch.query("SELECT 1"):\n        X = 1',
            True,
        ),
        (
            "a decorator on a generic",
            '@torch.query("SELECT 1")\n    def _inner[torch: object]():\n        X = 1',
            False,
        ),
        (
            "a default on a generic",
            'def _inner[torch: object](x=torch.query("SELECT 1")):\n        X = 1',
            False,
        ),
    ):
        _split_row = roots(_header_reach_probe.format(header=_generic_header))
        if _must_be_graded:
            assert len(_split_row) == 1 and _split_row[0][1:] == ((), True, False, True), (
                f"{_label} evaluates in the TYPE-PARAMETER scope, where the list's own "
                "names ARE bound, so the `.query` runs on the TypeVar and the probe must be "
                f"graded — exempting it is a silent drop: {_split_row}"
            )
        else:
            assert _split_row == (), (
                f"{_label} evaluates in the ENCLOSING scope, where the generic's type "
                "parameters are NOT bound, so the call still runs on the import and the "
                f"probe must stay EXEMPT — grading it is the 4k-/4l- false red: {_split_row}"
            )
    _body_after_header = roots(
        _header_reach_probe.format(
            header=(
                "def _inner(x=_unused()):\n"
                "        torch = _make()\n"
                '        torch.query("SELECT 1")'
            )
        )
    )
    assert len(_body_after_header) == 1, (
        "the control: a call in the BODY is resolved by the definition's own bindings, so "
        "it must stay graded — a header rule that skipped the body too is a silent drop: "
        f"{_body_after_header}"
    )

    # 4j-pentavicesimus. A TYPE PARAMETER IS NOT IN SCOPE IN A DEFAULT OR A DECORATOR —
    #     those evaluate BEFORE the type-parameter scope exists, so a call there resolves the
    #     MODULE import, while an ANNOTATION, a class BASE and a type-parameter BOUND all
    #     evaluate inside that scope and read the `TypeVar` (measured: the first two return
    #     the module object, the last three raise `AttributeError: 'typing.TypeVar' object
    #     has no attribute 'query'`). Contributing the parameters at every header position
    #     graded a legitimate probe in the two eager ones (review of #14160, cycle 9, P2-1);
    #     the three lazy positions are the controls and must stay graded.
    _generic_header_probe = (
        "import torch\n\nTORCH_AVAILABLE = False\ntry:\n    {header}\n"
        "    TORCH_AVAILABLE = True\nexcept Exception:\n    TORCH_AVAILABLE = False\n"
    )
    for _label, _header, _want in (
        (
            "a default",
            'def _f[torch](x=torch.query("SELECT 1")):\n        pass',
            False,
        ),
        (
            "a decorator",
            '@_reg(torch.query("SELECT 1"))\n    def _f[torch]():\n        pass',
            False,
        ),
        (
            "an annotation",
            'def _f[torch](x: torch.query("SELECT 1")):\n        pass',
            True,
        ),
        (
            "a class base",
            'class _C[torch](torch.query("SELECT 1")):\n        pass',
            True,
        ),
        #: A class KEYWORD is evaluated in the same (type-parameter) scope as a base, and
        #: this row is what makes that a claim rather than a docstring line: reverting the
        #: `keywords` half of `_header_child_is_enclosing_scope` alone stays GREEN without
        #: it (measured — a latent fail-open over our own client, review of this PR).
        (
            "a class keyword",
            'class _C[torch](metaclass=torch.query("SELECT 1")):\n        pass',
            True,
        ),
        (
            "a type-parameter bound",
            'def _f[torch: torch.query("SELECT 1")]():\n        pass',
            True,
        ),
    ):
        _generic_row = roots(_generic_header_probe.format(header=_header))
        _expected = "GRADED (the TypeVar is in scope)" if _want else "EXEMPT (the module import is)"
        assert (len(_generic_row) == 1) == _want, (
            f"a generic's {_label} must be {_expected}: {_generic_row}"
        )

    # 4j-svicesimus. ORDER AMONG THE NARROW ARMS — an EARLIER dropping arm has already
    #     handled the name, so a LATER re-raising arm naming it is unreachable and credits
    #     nothing. `_covered` unioned every re-raising narrow arm, so the charged
    #     `except ImportError:` drop came back green in BOTH gates whenever the gate's own
    #     repair message was followed ("Insert the re-raise arm immediately before the broad
    #     handler") on a block that already carried the skip (review of #14160, cycle 10,
    #     P1-1). The control is the same two arms in the FIXED order: a re-raising arm ABOVE
    #     an unreachable dropping arm drops nothing and does credit.
    _unreachable_credit = [
        node
        for node in ast.walk(
            ast.parse(
                textwrap.dedent(
                    """
                    try:
                        _probe = _make()
                    except ImportError:
                        pass
                    except (AttributeError, NameError, TypeError, ImportError):
                        raise
                    except Exception:
                        _probe = None
                    """
                )
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert "ImportError" not in _covered(_unreachable_credit), (
        "the FIRST matching arm handles `ImportError`, so the later re-raising arm is "
        "unreachable for it and must not credit it — crediting it is how a charged "
        "ImportError drop reads armed again"
    )
    _reachable_credit = [
        node
        for node in ast.walk(
            ast.parse(
                textwrap.dedent(
                    """
                    try:
                        _probe = _make()
                    except (AttributeError, NameError, TypeError, ImportError):
                        raise
                    except ImportError:
                        pass
                    except Exception:
                        _probe = None
                    """
                )
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert "ImportError" in _covered(_reachable_credit), (
        "the control: the same two arms in the other order — the re-raising arm runs "
        "first, drops nothing, and DOES credit `ImportError`"
    )

    # 4j-octovicesimus-bis. THE SUBCLASS SPELLINGS are the same event: `ModuleNotFoundError`
    #     is how a moved module actually raises at the seam this file exists for, and
    #     `UnboundLocalError` is `NameError`'s. Neither `_RAISED` (charging) nor the
    #     coverage credit recognised them, so `except ModuleNotFoundError:` alone was GREEN
    #     in both gates when it dropped and RED when it re-raised correctly — the exact
    #     "redded for having fixed it" defect fixed for `raise exc` (review of #14160,
    #     cycle 10, P1-2).
    _subclass_drop = [
        node
        for node in ast.walk(
            ast.parse(
                "try:\n    _probe = _make()\nexcept ModuleNotFoundError:\n    _probe = None\n"
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert _swallows_everything(_subclass_drop) is True, (
        "a `ModuleNotFoundError` arm that DROPS the error swallows the module rename just as "
        "an `ImportError` arm does"
    )
    #: A DEAD DROP CANNOT SWALLOW: `except ImportError: raise` above an (unreachable)
    #: `except ModuleNotFoundError: <drop>` propagates everything, and the order-blind
    #: `any()` charged it — the exact ordering a lane gets by following this gate's repair
    #: message above an existing precise skip (review of #14287, P2-1). `origin/main` left the
    #: block ungraded, so charging it was a NEW false red.
    _dead_drop = [
        node
        for node in ast.walk(
            ast.parse(
                "try:\n    _probe = _make()\nexcept ImportError:\n    raise\n"
                "except ModuleNotFoundError:\n    _probe = None\n"
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert _swallows_everything(_dead_drop) is False, (
        "an arm below a re-raising arm that already catches its whole family can never run, "
        "so it drops nothing"
    )
    #: ⛔ THE EXACT-SPELLING HALF OF `_masked_by`, on the CHARGE side — no row pinned it
    #: (review of PR #14287, F3). `_dead_drop` above only pins the FAMILY term: with the
    #: exact term deleted, `_RAISED_ALIAS.get("ModuleNotFoundError") == "ImportError"` is
    #: still in `caught_earlier`, so its unreachable drop stays uncharged either way. A
    #: SAME-SPELLING duplicate is the shape the exact term alone covers, and with it gone
    #: the second arm reads reachable and this predicate flips to a FALSE CHARGE on a block
    #: that propagates everything. The masking-table row labelled "same spelling twice"
    #: cannot see this: it reaches `set()` through `dropped_family` under both versions.
    _exact_dead_drop = [
        node
        for node in ast.walk(
            ast.parse(
                "try:\n    _probe = _make()\nexcept ImportError:\n    raise\n"
                "except ImportError:\n    _probe = None\n"
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert _swallows_everything(_exact_dead_drop) is False, (
        "an arm that repeats an earlier arm's EXACT spelling can never run, so it drops "
        "nothing — charging it is a false red on a block that propagates everything"
    )

    #: `ImportError`'s OTHER 3.12 subclass, and the map listed it with no witness at all
    #: (review of #14160, cycle 12, P1-1): deleting the entry was green while re-opening
    #: BOTH fail-open directions — the drop stopped being charged, and a
    #: `except ZipImportError: raise` arm below an `except ImportError:` arm started
    #: crediting a name the earlier arm had already swallowed.
    _zip_drop = [
        node
        for node in ast.walk(
            ast.parse("try:\n    _probe = _make()\nexcept ZipImportError:\n    _probe = None\n")
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert _swallows_everything(_zip_drop) is True, (
        "a `ZipImportError` arm that DROPS swallows a zip-import seam exactly as "
        "`ImportError` and `ModuleNotFoundError` do"
    )
    _subclass_credit = [
        node
        for node in ast.walk(
            ast.parse(
                "try:\n    _probe = _make()\nexcept (AttributeError, ModuleNotFoundError, "
                "NameError, TypeError):\n    raise\n"
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert _covered(_subclass_credit) == set(_handler_names(_subclass_credit[0])), (
        "an arm that narrows to `ModuleNotFoundError` credits THAT SPELLING and nothing "
        "more: it does not re-raise a plain `ImportError`, so crediting the parent would "
        "tell the seam gate that any ImportError propagates while a reachable arm below "
        f"could still swallow one: {_covered(_subclass_credit)}"
    )
    #: ⛔ WHAT THIS ROW DOES *NOT* PIN (review of PR #14287, F2). It is GREEN on the code
    #: before the alias map existed — `_covered` was `out |= names` for a lone re-raising
    #: arm, which is already `set(_handler_names(...))` — so it cannot witness the credit
    #: half of the subclass work, and this comment used to present it as if it did. What it
    #: DOES pin, and must keep pinning, is that credit is never CANONICALISED on output: the
    #: mutation `out |= {_RAISED_ALIAS.get(n, n) …}` reds this row and nothing else. The
    #: alias map's live effects are the CHARGE side, the masking account, and the
    #: family-close; the GATE-level witness for the family-close lives in
    #: `test_the_import_seam_census_sees_the_shapes_it_grades` (`rename_dropped`), because a
    #: predicate row cannot see a gate verdict.
    #: …and it IS accounted as `ImportError` for ORDERING, so an earlier plain
    #: `except ImportError:` masks it — that arm catches every subclass, which is how a
    #: later `except ModuleNotFoundError: raise` becomes unreachable (review of #14160,
    #: cycle 11, P1-1). The mirror pair does the same for `NameError` / `UnboundLocalError`.
    for _parent, _child in (
        ("ImportError", "ModuleNotFoundError"),
        ("ImportError", "ZipImportError"),
        ("NameError", "UnboundLocalError"),
    ):
        _alias_order = [
            node
            for node in ast.walk(
                ast.parse(
                    "try:\n    _probe = _make()\n"
                    f"except {_parent}:\n    pass\n"
                    f"except {_child}:\n    raise\n"
                    "except Exception:\n    _probe = None\n"
                )
            )
            if isinstance(node, ast.ExceptHandler)
        ]
        assert _covered(_alias_order) == set(), (
            f"an earlier `except {_parent}:` catches every {_child} too, so the later "
            f"`except {_child}: raise` is unreachable and credits nothing: "
            f"{_covered(_alias_order)}"
        )

    #: ⛔ `_covered`'s MASKING FILTER itself, which no row could red (review of PR #14287,
    #: F4). Every other masking row reaches its expectation through `dropped_family`
    #: (an earlier arm that DROPS), so deleting `reachable = {name for name in names if
    #: not _masked_by(...)}` left them all green. The ONLY shape whose answer changes is
    #: parent-then-child with BOTH arms re-raising: nothing is dropped, so `dropped_family`
    #: is empty and the earlier parent arm's containment is the whole guard. It cannot
    #: change a gate verdict (a child spelling satisfies neither `_required_names` nor the
    #: seam check) — it is pinned because the docstring presents it as load-bearing, and a
    #: term presented as load-bearing with no row that reds when it goes tells the next
    #: reader the guard is covered when it is not.
    _parent_then_child_raise = [
        node
        for node in ast.walk(
            ast.parse(
                "try:\n    _probe = _make()\nexcept ImportError:\n    raise\n"
                "except ModuleNotFoundError:\n    raise\n"
            )
        )
        if isinstance(node, ast.ExceptHandler)
    ]
    assert _covered(_parent_then_child_raise) == {"ImportError"}, (
        "the earlier `except ImportError:` catches every ModuleNotFoundError, so the later "
        "re-raising `except ModuleNotFoundError:` is unreachable and credits nothing — "
        f"credit is {_covered(_parent_then_child_raise)}, expected {{'ImportError'}}"
    )

    # 4j-septenvicesimus-bis. THE MASKING IS CONTAINMENT, NOT EQUALITY, and the direction
    #     matters both ways: an earlier PARENT arm masks a later CHILD arm (Python catches
    #     every subclass), but an earlier CHILD arm does NOT mask a later PARENT one — a
    #     plain `ImportError` still reaches an `except ImportError:` arm placed after an
    #     `except ModuleNotFoundError:` arm. Canonicalising both sides into one set got the
    #     second direction wrong and cost a legitimate arm its credit, which is a false red
    #     on a correct repair (review of #14160, cycles 11-12).
    for _label, _arms, _expect in (
        (
            "parent then child",
            "except ImportError:\n    pass\nexcept ModuleNotFoundError:\n    raise\n",
            set(),
        ),
        (
            #: ⛔ THE CONVERSE ABOVE, which the block comment claims and NO ROW exercised
            #: (review of #14287, F1a — the ASYMMETRY term of `_masked_by` survived
            #: replacement with every other row green; measured: the symmetric mutant PASSED
            #: this whole test before this row existed). An earlier CHILD re-raise does not
            #: mask a later PARENT re-raise: a plain `ImportError` still reaches the
            #: four-name arm, so every one of the five spellings is credited. Symmetric
            #: membership would drop `ImportError` from that arm's reach and take its credit
            #: with it — the false RED on a correct repair (a probe that re-raises the module
            #: rename first and then the four programming errors propagates everything, and
            #: the gates' own repair message produces this shape) that the containment test
            #: exists to prevent.
            "child raised, then parent raised",
            "except ModuleNotFoundError:\n    raise\n"
            "except (AttributeError, NameError, TypeError, ImportError):\n    raise\n",
            {"AttributeError", "ImportError", "ModuleNotFoundError", "NameError", "TypeError"},
        ),
        (
            #: a dropped child family is exactly what the seam gate looks for, so a later
            #: parent re-raise cannot credit it (review of #14287, P1-1; this row previously
            #: expected `{"ImportError"}`, which LOCKED the leak in).
            "child dropped, then parent raise",
            "except ModuleNotFoundError:\n    _p = None\nexcept ImportError:\n    raise\n",
            set(),
        ),
        (
            #: A DROPPED child family blocks a later PARENT arm's credit for it — masking is
            #: one-way, dropping is not (review of #14287, P1-1: 278 of 30,930 enumerated
            #: arm sequences leaked through this single root).
            "child dropped, then four-name raise",
            "except ModuleNotFoundError:\n    _p = None\nexcept (AttributeError, NameError, "
            "TypeError, ImportError):\n    raise\n",
            {"AttributeError", "NameError", "TypeError"},
        ),
        (
            #: …and a mixed tuple credits only the names the earlier arm neither caught nor
            #: dropped — the "per-NAME, not per-arm" rule, which nothing else pinned
            #: (review of #14287, P2-5). `ImportError` is DROPPED by arm 1 and `TypeError` is
            #: untouched, so a per-ARM credit would take both and a per-NAME credit takes one.
            "child dropped, then a mixed tuple",
            "except ModuleNotFoundError:\n    _p = None\nexcept (ImportError, TypeError):\n    raise\n",
            {"TypeError"},
        ),
        (
            #: ⛔ WHAT THIS ROW DOES *NOT* PIN (review of PR #14287, F3). It asserts `set()`,
            #: and it reaches it through `dropped_family`: arm 1 drops the family, so arm 2's
            #: credit is withheld at the family-close whether or not `_masked_by`'s
            #: EXACT-SPELLING term exists. Deleting that term leaves this row green. The term
            #: is pinned on the CHARGE side, by `_exact_dead_drop` above (same-spelling
            #: duplicate below a re-raise). This comment previously claimed the row pinned the
            #: exact term; it does not, and neither does `_unreachable_credit`.
            "same spelling twice",
            "except ModuleNotFoundError:\n    pass\nexcept ModuleNotFoundError:\n    raise\n",
            set(),
        ),
    ):
        _masking = [
            node
            for node in ast.walk(
                ast.parse("try:\n    _probe = _make()\n" + _arms + "except Exception:\n    _p = None\n")
            )
            if isinstance(node, ast.ExceptHandler)
        ]
        assert _covered(_masking) == _expect, (
            f"masking runs from a PARENT arm to a later CHILD arm and not back ({_label}): "
            f"{_covered(_masking)} != {_expect}"
        )

    # 4j-quadrovicesimus. `type Y[torch] = torch.query(…)` — THE TYPE PARAMETER IS BOUND IN
    #     THE ALIAS VALUE. Measured on 3.12.3: `__value__` raises `AttributeError:
    #     'typing.TypeVar' object has no attribute 'query'`, i.e. the receiver resolved to
    #     the TypeVar and not the module import. The docstring named `type X[T]` as a covered
    #     spelling while nothing passed an `ast.TypeAlias` to `_type_param_names`, so the
    #     alias form was a fail-open hole and the block was not even admitted (review of
    #     #14160, cycle 6, P2-4).
    _type_alias_probe = roots(
        textwrap.dedent(
            """
            import torch

            TORCH_AVAILABLE = False
            try:
                type _Y[torch] = torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert len(_type_alias_probe) == 1, (
        "a `type` alias's type parameter is in scope for its VALUE, so the call there runs "
        f"on the TypeVar and must be graded: {_type_alias_probe}"
    )

    # 4j-quinvicesimus. `raise exc` IS COVERAGE. `_reraises` read only a BARE `raise`, so a
    #     lane that followed this gate's own repair instruction ("insert the re-raise arm")
    #     in that idiomatic spelling was redded for having fixed it (review of #14160, cycle
    #     6, P2-6). The handler is parsed directly, because the coverage rule is a predicate
    #     over the handler and a fixture block would test the admission machinery instead.
    for _label, _statement, _want in (
        ("raise exc", "raise exc", True),
        ("raise an unrelated name", "raise _other", False),
        ("swallow", "_probe = None", False),
        ("bare raise", "raise", True),
        #: the CHAINED form is coverage — `4j-octovicesimus`; a suppressed cause is not,
        #: because it discards the caught error it names.
        ("raise X from None", "raise RuntimeError() from None", False),
    ):
        _handler = next(
            node
            for node in ast.walk(
                ast.parse(
                    "try:\n    _probe = _make()\n"
                    f"except (AttributeError, NameError) as exc:\n    {_statement}\n"
                    "except Exception:\n    _probe = None\n"
                )
            )
            if isinstance(node, ast.ExceptHandler)
        )
        assert _reraises(_handler) is _want, (
            f"`{_statement}` in a narrow arm must be coverage={_want} ({_label}) — a "
            "`raise exc` re-raises the caught object and drops nothing"
        )

    # 4j-sesvicesimus. A NESTED CLASS BODY IS A SEPARATE SCOPE, in BOTH directions. The
    #     `crossed_callable` flag's CLASS half was unpinned: the two METHOD controls pass via
    #     the `FunctionDef` branch's flag, so deleting the assignment at the end of the class
    #     branch left the file green (review of #14160, cycle 6, P2-2). Measured flip:
    #     `class Outer: torch = object(); class Inner: rows = torch.query(…)` → head `()`,
    #     mutant graded. §4.2.2: a class block's names do not extend to a nested class body.
    _nested_class_body = roots(
        textwrap.dedent(
            """
            import torch

            TORCH_AVAILABLE = False
            try:
                class _Outer:
                    torch = object()

                    class _Inner:
                        _rows = torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert _nested_class_body == (), (
        "an OUTER class body's binding cannot reach an INNER class body, so the call there "
        f"still runs on the import and must stay EXEMPT: {_nested_class_body}"
    )

    # 4j-septenvicesimus. A NESTED SCOPE'S `global` IS ITS OWN DECLARATION, and `_global_
    #     declarations`' skip of nested blocks was deletable green (review of #14160, cycle 6,
    #     P2-3). Without the skip an INERT `global torch` inside a nested class borrows the
    #     ENCLOSING function's local `torch`, and a legitimate probe at module scope is graded
    #     — the false red the docstring names. Measured flip: head `()`, mutant graded.
    _nested_scope_global = roots(
        textwrap.dedent(
            """
            import torch

            def _outer():
                torch = _make_client()

                class _Inner:
                    global torch

                return _Inner

            TORCH_AVAILABLE = False
            try:
                _e = torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert _nested_scope_global == (), (
        "an inert `global torch` in a NESTED class must not borrow the enclosing function's "
        f"local of that name — the module import still reaches the probe: {_nested_scope_global}"
    )

    # 4j-nonvicesimus. A NARROW arm THAT DROPS the programming errors swallows them exactly
    #     as a broad arm does. `_swallows_everything` asked only for a bare / `Exception` /
    #     `BaseException` arm, so the substitution a lane is most likely to make when this
    #     gate reds — narrow `except Exception:` to the four names and KEEP the drop — read
    #     as neither covered nor swallowing, i.e. invisible to BOTH gates while the rename
    #     stayed swallowed (review of #14160, cycle 7, P2-3; pre-existing at `origin/main`,
    #     LATENT: the tree holds 0 instances, so no population change). The handler list is
    #     parsed directly because the predicate is over handlers; a fixture block would test
    #     the admission machinery instead.
    for _label, _handlers_source, _want in (
        (
            "a narrow arm that DROPS the error",
            "except (AttributeError, NameError, TypeError, ImportError):\n    FLAG = False\n",
            True,
        ),
        (
            "a narrow arm that RE-RAISES",
            "except (AttributeError, NameError, TypeError, ImportError):\n    raise\n",
            False,
        ),
        #: `ImportError` IS charged, and this row is the P1 of cycle 9: excluding it left
        #: `except ImportError: FLAG = False` around `from tortoise.sdk import …` green in
        #: BOTH gates, because the seam gate is conditioned on this predicate. The
        #: legitimate optional-extra skip it was protecting is a DECLARED RESIDUAL on
        #: `_swallows_everything` (0 instances in the tree; module-keyed allowance would be
        #: machinery for a population that does not exist).
        ("an ImportError-only arm that drops", "except ImportError:\n    FLAG = False\n", True),
        #: …but a handler that raises on ONE path and drops on the default path DOES
        #: swallow, and reading coverage from the walking predicate made it green in both
        #: gates (review of #14160, cycle 8, P2-2).
        (
            "a conditional re-raise with a dropping default",
            "except Exception:\n    if _STRICT:\n        raise\n    FLAG = False\n",
            True,
        ),
        ("a bare arm", "except:\n    FLAG = False\n", True),
        ("a broad arm", "except Exception:\n    FLAG = False\n", True),
        ("a broad arm that re-raises", "except Exception:\n    raise\n", False),
        ("an unrelated narrow arm", "except KeyError:\n    FLAG = False\n", False),
    ):
        _handlers = [
            node
            for node in ast.walk(ast.parse("try:\n    _probe = _make()\n" + _handlers_source))
            if isinstance(node, ast.ExceptHandler)
        ]
        assert _swallows_everything(_handlers) is _want, (
            f"{_label} must read as swallowing={_want} — a NARROW arm that catches a "
            "programming error and drops it swallows it exactly as a broad arm does, and an "
            "arm that re-raises drops nothing"
        )

    # 4j-octovicesimus. A CHAINED re-raise — `raise RuntimeError(…) from exc` — propagates the
    #     caught error just as `raise exc` does (PEP 3134), and the DROP side has to read it
    #     the same way `_covered` does — `_covered` counts it because this predicate does,
    #     so the dependency runs one way only. The PREDECESSOR predicate read just a bare
    #     `raise` or `raise exc`, so ONE handler was
    #     simultaneously "no coverage" and "re-raises, drops nothing", and a block written per
    #     this gate's own repair instruction read UNARMED (review of #14160, cycle 7, P2-2).
    #     `raise X from None` is the control: it discards the cause, so it is not coverage.
    for _label, _statement, _want in (
        ("raise X from exc", "raise RuntimeError('boom') from exc", True),
        ("raise X from None", "raise RuntimeError('boom') from None", False),
        ("raise the caught name", "raise exc", True),
    ):
        _chained = next(
            node
            for node in ast.walk(
                ast.parse(
                    "try:\n    _probe = _make()\n"
                    f"except (AttributeError, NameError) as exc:\n    {_statement}\n"
                    "except Exception:\n    _probe = None\n"
                )
            )
            if isinstance(node, ast.ExceptHandler)
        )
        assert _reraises(_chained) is _want, (
            f"`{_statement}` must be coverage={_want} ({_label}) — a chained re-raise "
            "propagates the caught error, a suppressed cause does not"
        )

    # 4j-duovicesimus. A MODULE-LEVEL BARE ANNOTATION BINDS NOTHING. `torch: object` only
    #     records an annotation — it is not a binding operation, so an earlier
    #     `import torch` still denotes itself afterwards (with NO earlier binding the read
    #     is a `NameError`, measured) —
    #     but its TARGET is an `ast.Name` with `Store` context, so the generic module walk
    #     counted it and a legitimate third-party probe read GRADED: a 4k-/4l- false red
    #     (review of #14160, cycle 5). The annotation is still walked, because a walrus in
    #     it IS evaluated at module scope; the control below pins that half, so a fix that
    #     simply skipped the whole node would leave that walrus drop green.
    _bare_annotation = textwrap.dedent(
        """
        import torch

        torch: object

        TORCH_AVAILABLE = False
        try:
            torch.query("SELECT 1")
            TORCH_AVAILABLE = True
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    _bare_row = roots(_bare_annotation)
    assert _bare_row == (), (
        "a module-level bare annotation binds no value, so the import still reaches the "
        f"probe and it must stay EXEMPT: {_bare_row}"
    )
    _annotation_walrus = roots(
        textwrap.dedent(
            """
            import torch

            _annotation: (torch := _make_client())

            TORCH_AVAILABLE = False
            try:
                torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert len(_annotation_walrus) == 1, (
        "the control: the ANNOTATION expression is evaluated at module scope, so a walrus "
        "in it binds `torch` and the probe must be graded — skipping the whole `AnnAssign` "
        f"node instead of its target is a silent drop: {_annotation_walrus}"
    )
    #: …and in a FUNCTION the same bare annotation DOES make the name local
    #: (`UnboundLocalError`, measured), which is why the rule lives in the MODULE walk and
    #: not in `_collect_binding`.
    _function_bare_annotation = roots(
        textwrap.dedent(
            """
            import pytest
            import torch

            def _probe():
                torch: object
                TORCH_AVAILABLE = False
                try:
                    _e = torch.query("SELECT 1")
                    TORCH_AVAILABLE = True
                except Exception:
                    TORCH_AVAILABLE = False
                return TORCH_AVAILABLE

            pytestmark = pytest.mark.skipif(not _probe(), reason="engine")
            """
        )
    )
    assert len(_function_bare_annotation) == 1, (
        "a bare annotation in a FUNCTION makes the name local (`UnboundLocalError`), so a "
        "call there cannot run on the module import — the scope-dependent half of the "
        f"rule, and the reason a single-token fix cannot serve both: "
        f"{_function_bare_annotation}"
    )

    # 4j-unvicesimus. A `global` DECLARED IN A CLASS BODY is the same module-scope
    #     rebinding the function case pins (`4j-undecies`), and CPython honours it
    #     (measured: the module name becomes the assigned object). `_global_scope_bindings`
    #     visited only FunctionDef/AsyncFunctionDef, so this shape stayed EXEMPT over OUR
    #     client — the same fail-open family, one node kind over (final review of #14160,
    #     P2; 0 live instances in the tree, so it is LATENT and needs the row, not a
    #     population change).
    _class_body_global = textwrap.dedent(
        """
        import torch

        class C:
            global torch
            torch = _make_client()

        TORCH_AVAILABLE = False
        try:
            torch.query("SELECT 1")
            TORCH_AVAILABLE = True
        except Exception:
            TORCH_AVAILABLE = False
        """
    )
    _class_global_row = roots(_class_body_global)
    assert len(_class_global_row) == 1 and _class_global_row[0][1:] == ((), True, False, True), (
        "a `global` declaration in a CLASS BODY binds the MODULE name, so a module-level "
        "probe after it runs on OUR client and must be graded — missing it is the same "
        f"silent drop the function case pins: {_class_global_row}"
    )
    _class_body_global_control = roots(
        textwrap.dedent(
            """
            import torch

            class C:
                torch = object()

            TORCH_AVAILABLE = False
            try:
                torch.query("SELECT 1")
                TORCH_AVAILABLE = True
            except Exception:
                TORCH_AVAILABLE = False
            """
        )
    )
    assert _class_body_global_control == (), (
        "the CONTROL: WITHOUT `global`, a class-body assignment is the class body's own "
        "name and does NOT touch the module's, so the module-level probe stays exempt "
        f"(grading it is the `4k-`/`4l-` false red): {_class_body_global_control}"
    )

    # 4m. THE GAP THIS SLOT PINNED AS ACCEPTED IS CLOSED BY RULE (review of
    #     90a0e9e8b20c, P2), so the `()` pin that stood here is DELETED — its own text
    #     instructed exactly that if the coverage was restored. A module-body guard
    #     whose client is built at MODULE scope (`PROJ = _make_proj()`) and then queried
    #     reaches no import and no module-level own name, so after the capability OR was
    #     removed NO admission rule saw it: a swallowed rename there reads as "storage
    #     unavailable" again, the class-2 false PASS this file exists to catch (measured
    #     at the parent: `_assigns_availability` True, `_imports_own_code` False,
    #     `_touches_own_code` False, `_reaches_capability_seam` True — a root before
    #     `90a0e9e8b20c`, `()` after).
    #
    #     The repair is the one `privatise.receiver_is_sdk` could NOT make: it answers
    #     from annotations and stand-ins, so it cannot see `PROJ = _make_proj()`, and
    #     gating the helper branches on it breaks the pinned passed-in-client rule
    #     (`tests/test_8870_version_anchor_write_order.py:138`). Instead the arm is back
    #     in every branch and NARROWED: `_reaches_capability_seam(block, third_party, _parents)`
    #     skips an ATTRIBUTE call only when the receiver's ROOT name is bound by a
    #     NON-own import AND no binding for it REACHES the call site — module scope
    #     (`_third_party_receivers`) or the probe's own enclosing scopes
    #     (`_enclosing_bindings`), NOT "anywhere in the module" (a rebind in a
    #     neighbouring function cannot reach it: measured `()` exempt — review of this
    #     PR, P2) — the `some_optional_dep.query` false red — and
    #     grades everything else. `4k+` below is the POSITIVE witness
    #     for this exact document; deleting the arm again reds it, which is the
    #     guarantee the `()` pin could never give.

    # 4k-/4l-. `_reaches_capability_seam` matches the GENERIC attribute `query`, so it
    #     must not admit a third-party-only probe: at the module body, nor in either
    #     helper branch. That holds for a receiver whose ROOT NAME an import bound and
    #     that no binding REACHES — not for every third-party-looking receiver:
    #     `CLIENT = make_client(); CLIENT.query(…)` IS graded (the name is not an
    #     import), and so is a collection handle from
    #     `client.get_or_create_collection(...)`; this comment used to deny that (review
    #     P2, corrected — the exemption is a BINDING, not a shape). The two rows below
    #     are the NEGATIVE half of the narrowing
    #     (`third_party`): re-adding an UNGATED arm anywhere reddens them, and dropping
    #     the arm outright reddens the positive rows 4k+ that follow (review of
    #     90a0e9e8b20c, findings 1+3).
    module_body_third_party_query = roots(
        """
        FALKORDB_AVAILABLE = False
        try:
            import some_optional_dep
            some_optional_dep.query("MATCH (n) RETURN n")
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert module_body_third_party_query == (), (
        "a module-body guard whose only seam is a THIRD-PARTY client's `.query` "
        f"must not be graded — the attribute name is generic: {module_body_third_party_query}"
    )
    skip_gated_third_party_query = roots(
        """
        import pytest

        def _probe():
            try:
                import some_optional_dep
                some_optional_dep.query("MATCH (n) RETURN n")
                return True
            except Exception:
                return False

        LIVE = _probe()

        @pytest.mark.skipif(not LIVE, reason="live")
        def test_live():
            pass
        """
    )
    assert skip_gated_third_party_query == (), (
        "the same rule applies in a SKIP-GATED helper — a third-party client's "
        f"`.query` is not this repository's seam: {skip_gated_third_party_query}"
    )

    # 4k+. The arm is NARROWED, not dropped, so its isolating POSITIVE witness must
    #     exist too: a module-body guard whose client is built at module scope has no
    #     import and no module-level own name to reach, so only the capability arm
    #     admits it. This is the shape the removal graded by NOTHING — a swallowed
    #     rename there reads as "storage unavailable" again, the class-2 false PASS
    #     this file exists to catch (review of 90a0e9e8b20c, finding 3).
    module_body_client_bound_above = roots(
        """
        PROJ = _make_proj()

        FALKORDB_AVAILABLE = False
        try:
            PROJ.g.query("RETURN 1")
            FALKORDB_AVAILABLE = True
        except Exception:
            FALKORDB_AVAILABLE = False
        """
    )
    assert module_body_client_bound_above == ((5, (), True, False, True),), (
        "a module-body guard whose client was BUILT above the `try` must still be "
        "graded — `PROJ` is neither an import nor a module-level own name, so only "
        f"the capability arm can admit it: {module_body_client_bound_above}"
    )

    # 4p. The last admission arm to get a witness: a probe inside a `def test_…`
    #     BODY that decides its own skip. It was the census's DECLARED RESIDUAL
    #     until the widening below was applied (review P1); its live instance is
    #     `tests/test_index_surfacing.py:269`, where a broad `except Exception`
    #     captures into `unsupported` and feeds `pytest.skip(…)`, so a moved door
    #     there read as "this engine cannot hold a fulltext index".
    test_body_probe_feeding_skip = roots(
        """
        def test_a_engine_probe():
            unsupported = None
            for probe in ("one", "two"):
                try:
                    g.query(probe)
                    unsupported = None
                    break
                except Exception as e:
                    unsupported = e
            if unsupported is not None:
                pytest.skip(f"unsupported: {unsupported}")
        """
    )
    assert test_body_probe_feeding_skip == ((5, (), True, False, True),), (
        "a probe inside a TEST BODY that feeds its own `pytest.skip` sentinel is "
        "the same class-2 decision as a module-level one, and it must be graded: "
        f"{test_body_probe_feeding_skip}"
    )
    # ...but the arm is conditional on the SKIP BINDING, not a blanket grading of
    # every `try` in every function. ⚠️ THIS ROW DOES NOT WITNESS THE BINDING GATE,
    # even though an earlier comment here said it did (review of `2046456825`, P2):
    # the block below has no `skip`/`xfail` call AT ALL, so it leaves one gate
    # earlier (`if not skip_read: continue`), and deleting the binding gate leaves
    # this row green. What holds that gate is the CENSUS — measured: under the
    # deletion the population goes 70 → 78 rows, `UNARMED` becomes six blocks, and
    # `test_every_availability_probe_re_raises_a_programming_error` plus
    # `test_a_probe_that_imports_inside_its_try_covers_the_import_seam` red. This row
    # is the NEGATIVE for "a `try` with no skip decision is not the decision", which
    # is a different (and still load-bearing) claim.
    test_body_probe_without_skip = roots(
        """
        def test_b_unrelated():
            note = None
            try:
                g.query("one")
            except Exception as e:
                note = e
            assert note is None
        """
    )
    assert test_body_probe_without_skip == (), (
        "a `try` in a test body that decides NO skip is not the availability "
        f"decision and must not be graded: {test_body_probe_without_skip}"
    )

    # 4q. The GUARD half of that widening, which is the half that actually exists in
    #     this tree. `_skip_call_read_names` first read only the skip call's
    #     ARGUMENTS, so a sentinel carried by the `if` that guards the skip —
    #     `if not dropped: pytest.skip("engine has no drop procedure")` — contributed
    #     nothing and the residual stayed open for FIVE live probes (review of
    #     `cd75ec7144`, P1) — measured by removing ONLY the guard half: 70/62/58 →
    #     65/57/55, the five lost being `tests/test_indexes.py:1422`,
    #     `tests/test_11682_quote_indexed.py:438` and `:656`,
    #     `tests/test_session_semantic_retrieval.py:298`, and
    #     `tests/test_extractor_reliability.py:898`. That last one is graded because
    #     its handler ASSIGNS its guard's sentinel, and it owes NO arm for a
    #     DIFFERENT reason — it already RE-RAISES (`swallows=False`), so the census
    #     charges it nothing; grading alone does not excuse an arm. This row is the
    #     sentinel-in-the-guard shape, a bare
    #     string message that interpolates NOTHING, so only the guard reading can
    #     admit it.
    test_body_guard_carried_sentinel = roots(
        """
        def test_a_engine_probe():
            dropped = False
            for proc in ("one", "two"):
                try:
                    g.query(f"CALL {proc}('Point')")
                    dropped = True
                    break
                except Exception:
                    continue
            if not dropped:
                pytest.skip("engine has no drop procedure")
        """
    )
    assert test_body_guard_carried_sentinel == ((5, (), True, False, True),), (
        "a skip whose MESSAGE interpolates nothing is still decided by the guard "
        "that carries the sentinel — keying on the call's arguments alone left this "
        f"live shape ungraded: {test_body_guard_carried_sentinel}"
    )
    # ...and the guard must actually SKIP: the identical block whose guard asserts
    # instead is not a skip decision, so it stays ungraded.
    test_body_guard_without_skip = roots(
        """
        def test_b_unrelated():
            dropped = False
            for proc in ("one", "two"):
                try:
                    g.query(f"CALL {proc}('Point')")
                    dropped = True
                    break
                except Exception:
                    continue
            assert dropped
        """
    )
    assert test_body_guard_without_skip == (), (
        "a guard that does not SKIP is not the availability decision: "
        f"{test_body_guard_without_skip}"
    )

    # 5. The real tree must carry all four shapes, or every assertion above is a
    #    statement about a sample rather than about the code it is meant to guard.
    graded = {row[0] for row in _probe_report()}
    for rel in (
        "tests/test_falkordb_compat.py",
        "tests/test_evidence_assembly_wiring_2683.py",
        "tests/test_paired_expansion_ab_2518.py",
        "tests/test_time_aware_run_2520.py",
        # the skip-read spellings (shape b) …
        "tests/test_12360_underscore_or_terms.py",
        "tests/test_search_engine.py",
        "tests/test_identifier_tokens_8933.py",
        "tests/test_6151_annotation_band.py",
        # … and the sentinel helper (shape e)
        "tests/test_9226_dump_paging_v6.py",
        # … and the module-body guard nested in control flow, pinned by presence:
        # it was the ONE probe in tests/+tools/ that the direct-children walk left
        # unarmed and unreported, so its absence from the report is the signal that
        # the walk has gone blind again.
        "tests/test_assembly_pure.py",
        # … and the same probe one wrapper out, under a handler-less `try/finally`
        # (a pure wrapper the walk must descend). Its absence is the signal that
        # the walk has gone back to stopping at every `try`.
        "tests/test_integration_search.py",
    ):
        assert rel in graded, (
            f"{rel} is absent from the probe census — a shape it grades has "
            "been dropped from the walk (flag suffix, skipif-call, or the "
            "`LIVE = _probe()` resolution), so its arm is unobservable"
        )
