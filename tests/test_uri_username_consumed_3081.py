"""#3081 — the decoded URI username must REACH THE CLIENT.

`parse_uri_userinfo` (tortoise/config.py, #3039) is the single percent-decoding
rule for URI credentials. Its contract is that BOTH fields come back. Five
`graph-scripts` helpers bound the username to a discarded ``_username`` and
returned only the password, so a named-user URI (``redis://user:pw@host``)
reached ``FalkorDB(...)`` with no username and authenticated as the DEFAULT
user — a misleading ``AuthenticationError`` / ``NOPERM``.

The pre-existing AST guard from PR #3047 proves no helper reads a RAW
(undecoded) ``parsed.password``. It cannot see a *dropped* field, which is why
the defect survived: decoding correctly and then discarding the result passes
it.

WHY THIS FILE IS BEHAVIOURAL, NOT STATIC
----------------------------------------
The invariant is a RUNTIME statement — did the username reach the client — and
an AST check can only approximate it by enumerating spellings. Four rounds of
review on this PR each found a spelling such a matcher missed (aliased
constructor, ``username=None``, a keyword-form call site, a positional
constructor argument) and three it wrongly rejected. Each missed spelling is a
green CI on a reintroduced #3081; each wrong rejection reds correct code.

So instead of pattern-matching source, this file **observes the client that is
actually built**, for every construction path each module has. The recording
constructor raises as soon as it has captured its arguments, so no database is
ever touched.

Two assertions are deliberately made against the PARSED CONFIG rather than a
literal: comparing to a fixed string would pass a call site that hardcodes that
string while the URI username is anything else — a reintroduced #3081 for every
user except the fixture's.

The production code carries the guarantee too: ``trigger_bgsave`` / ``check_rdb``
take ``username`` as a REQUIRED keyword-only parameter, so a call site that omits
it fails loudly with ``TypeError`` rather than silently authenticating as the
default user.

COVERAGE
--------
``rdb_snapshot_restore.py`` builds no ``FalkorDB`` client at all — it
authenticates through ``redis-cli --user`` — so it has no constructor to
observe here. Its ``--user`` consumption is pinned by
``tests/test_restore_container_recovery.py::test_named_user_uri_authenticates_end_to_end``
and ``::test_redis_cli_uses_env_not_argv``. It is included in the parser table
because #3081 listed it, and it was already correct.

Residual: a client built by INDIRECTION the counter cannot resolve is counted as
0 — ``_count_client_sites`` enumerates those. A newly added client in a module
OUTSIDE the parser table is caught by the directory scan in
``test_every_known_constructor_site_is_driven``, not left to escape silently.
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import sys
import types
import warnings
from pathlib import Path
from typing import ClassVar

import falkordb
import pytest

# Captured at import time, BEFORE any test can patch it. `_patched()` replaces
# `falkordb.FalkorDB`, so resolving the identity target lazily would make the
# counter's answer depend on whether a patch happened to be active (#7053).
REAL_FALKORDB = falkordb.FalkorDB

REPO = Path(__file__).resolve().parent.parent
GS = REPO / "graph-scripts"

# Every helper #3081 named, with the function that parses the URI.
PARSERS = [
    ("audit_graph.py", "_parse_uri"),
    ("audit_graph_deep.py", "_parse_uri"),
    ("context_removal_audit.py", "_parse_uri"),
    ("parity_sample.py", "_parse_uri"),
    ("pre_migration_snapshot.py", "_parse_uri"),
    ("rdb_snapshot_restore.py", "parse_uri"),
]

# Clients that build a `FalkorDB` but consume NO URI, so #3081 has nothing to
# observe in them. Listed explicitly: the directory scan below exists so a NEW
# site cannot enter unnoticed, and an allowlist entry records a decision where
# silence would hide one.
NON_URI_SITES = {"merge_endometriosis.py", "smoke_test.py"}

# Two distinct named users. Driving a second one is what stops a call site that
# hardcodes the first from passing.
NAMED = "redis://alice:pw@localhost:16379/tortoise"
NAMED2 = "redis://bob:pw@localhost:16379/tortoise"
ANON = "docker://:pw@localhost:16379/tortoise"

# The four modules that build their own client via a seam or a lazy accessor.
CLIENT_MODULES = [
    "audit_graph.py",
    "audit_graph_deep.py",
    "context_removal_audit.py",
    "parity_sample.py",
]


def _load(module_file: str):
    """Import a graph-scripts module without executing its __main__ block.

    ``graph-scripts/`` is put on ``sys.path`` first: run directly, that
    directory is ``sys.path[0]``, so sibling imports such as
    ``from connectivity_gate import ...`` resolve. Imported from a test they do
    not, so the test supplies what the script's own run context supplies.
    """
    path = GS / module_file
    name = "gs_" + path.stem
    if name in sys.modules:
        return sys.modules[name]
    if str(GS) not in sys.path:
        sys.path.insert(0, str(GS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Stop(Exception):
    """Raised by the recording constructor once it has captured its args."""


class _RecordingClient:
    """Records every construction, and which client is actually USED.

    ``__init__`` deliberately does NOT raise. Raising there would stop the
    module at its FIRST construction, so a second inline client that shadows the
    seam — built after it and used instead — would never be reached, and the
    regression it reintroduces would go unnoticed. ``select_graph`` is where use
    becomes observable, so that is what raises.
    """

    calls: ClassVar[list] = []  # kwargs of every client constructed
    used: ClassVar[list] = []  # kwargs of each client whose select_graph was called

    def __init__(self, *args, **kwargs):
        # `*args` for the same reason the file counts positional call sites: a
        # recorder that accepted only keywords would raise `TypeError` on one
        # instead of recording it, and the failure would read as a guard bug.
        type(self).calls.append(kwargs)
        self.args = args
        self.kwargs = kwargs
        self.connection = _NoopConnection()

    def select_graph(self, name):
        type(self).used.append(self.kwargs)
        raise _Stop


class _NoopConnection:
    """Enough for the credential helpers, which call `.connection.execute_command`."""

    def execute_command(self, *args, **kwargs):
        return ""


@contextlib.contextmanager
def _patched(mod):
    """Patch every name the module could resolve FalkorDB through.

    ``audit_graph*.py`` bind it at MODULE level (``from falkordb import
    FalkorDB``), while the others import it inside the function — and one of
    those imports it AS ``_FalkorDB``. Patching only ``falkordb`` would let a
    real socket be opened (or miss the spelling entirely), so every attribute the
    module actually holds is patched.
    """
    # Discover the bindings by IDENTITY, not from a fixed name list (#7053).
    # ANY attribute of the module — or of the `falkordb` package and the
    # submodule that DEFINES the class — that IS the `falkordb.FalkorDB` object
    # is a construction path, whatever it is called. The hard-coded
    # `(mod, "FalkorDB"), (mod, "_FalkorDB")` pair silently missed a
    # module-level alias such as `from falkordb import FalkorDB as _F`, leaving
    # a real socket open. Function-level imports need no per-name entry: they
    # resolve `falkordb.FalkorDB` (or `falkordb.falkordb.FalkorDB`) at call
    # time, so patching the OBJECT covers every alias they bind.
    target = REAL_FALKORDB
    owners = [falkordb, mod]
    submodule = getattr(falkordb, "falkordb", None)
    if not isinstance(submodule, types.ModuleType):
        # The attribute is absent until the submodule is imported, and a
        # function-level `from falkordb.falkordb import FalkorDB` would then
        # re-bind the REAL class — a real socket, in a file promising none.
        # Same fallback the counter uses, so the comment above stays true.
        try:
            submodule = importlib.import_module("falkordb.falkordb")
        except ImportError:
            submodule = None
    if isinstance(submodule, types.ModuleType):
        owners.append(submodule)
    saved = {}
    targets = [
        (owner, name, value)
        for owner in owners
        for name, value in list(vars(owner).items())
        if value is target
    ]
    for owner, attr, original in targets:
        saved[(id(owner), attr)] = (owner, attr, original)
        setattr(owner, attr, _RecordingClient)
    _RecordingClient.calls = []
    _RecordingClient.used = []
    try:
        yield _RecordingClient.calls
    finally:
        for owner, attr, original in saved.values():
            setattr(owner, attr, original)


def _drive_client_construction(module_file, uri):
    """Build the client through the module's OWN path and return its kwargs.

    ``audit_graph*.py`` construct lazily on first use from a module-level
    config; the two seam modules build theirs at the top of ``main``. Both are
    driven for real — the point is to observe the path production uses, not a
    helper called in isolation.
    """
    mod = _load(module_file)
    cfg = getattr(mod, next(p[1] for p in PARSERS if p[0] == module_file))(uri)
    saved_argv = sys.argv
    with _patched(mod) as calls:
        try:
            if module_file in ("audit_graph.py", "audit_graph_deep.py"):
                mod._cfg = cfg
                mod._CONN.clear()
                with contextlib.suppress(_Stop):
                    mod._connect("DB")
            else:
                sys.argv = [module_file, "--uri", uri]
                with contextlib.suppress(_Stop, SystemExit):
                    mod.main()
        finally:
            sys.argv = saved_argv
    assert calls, (
        f"{module_file} built no client on its own construction path — the "
        f"test is not exercising what it claims to"
    )
    # The client the module ACTUALLY USED, not merely the first one it built:
    # a second client that shadows the seam would otherwise pass unnoticed.
    used = _RecordingClient.used
    assert used, (
        f"{module_file} never used the client it built (no select_graph call) — "
        f"this test cannot tell which client is live"
    )
    return cfg, used[-1]


# ── the parser half ───────────────────────────────────────────────────────

@pytest.mark.parametrize("module_file,fn_name", PARSERS)
def test_named_user_uri_surfaces_the_username(module_file, fn_name):
    """The parser must RETURN the decoded username — dropping it is the defect."""
    cfg = getattr(_load(module_file), fn_name)(NAMED)
    assert cfg["username"] == "alice", (
        f"{module_file}::{fn_name} dropped the decoded username; a named-user "
        f"URI authenticates as the default user (#3081)"
    )


@pytest.mark.parametrize("module_file,fn_name", PARSERS)
def test_anonymous_uri_yields_empty_username(module_file, fn_name):
    """`docker://:pw@host` carries an EMPTY username -> "" (client default)."""
    cfg = getattr(_load(module_file), fn_name)(ANON)
    assert cfg["username"] == ""


@pytest.mark.parametrize("module_file,fn_name", PARSERS)
def test_credentials_are_percent_decoded_and_stay_together(module_file, fn_name):
    """#3039's decode rule still holds, and both fields survive it."""
    cfg = getattr(_load(module_file), fn_name)(
        "redis://al%40ice:p%40ss@localhost:16379/tortoise"
    )
    assert cfg["username"] == "al@ice"
    assert cfg["password"] == "p@ss"


# ── the consumer half — observe the client the module really builds ───────

@pytest.mark.parametrize("module_file", CLIENT_MODULES)
@pytest.mark.parametrize("uri,expected", [(NAMED, "alice"), (NAMED2, "bob")])
def test_the_client_gets_the_config_username(module_file, uri, expected):
    """The username must be the URI's — compared against the PARSED CONFIG.

    Driving a second named user is what makes this a real check: an assertion
    against a literal would pass a client built with that literal while the URI
    username is anything else.
    """
    cfg, kwargs = _drive_client_construction(module_file, uri)
    assert cfg["username"] == expected  # the fixture is what we think it is
    assert kwargs.get("username") == expected, (
        f"{module_file} built its client with username={kwargs.get('username')!r} "
        f"for a URI whose user is {expected!r} — the decoded credential was "
        f"dropped or replaced (#3081)"
    )
    assert kwargs.get("password") == "pw", (
        f"{module_file} lost the password: {kwargs.get('password')!r}"
    )


@pytest.mark.parametrize("module_file", CLIENT_MODULES)
def test_the_client_gets_none_for_the_anonymous_form(module_file):
    """`docker://:pw@host` must send None — not "" and not a stale value."""
    _, kwargs = _drive_client_construction(module_file, ANON)
    assert kwargs.get("username") is None, (
        f"{module_file} built its client with username={kwargs.get('username')!r} "
        f"for an anonymous URI"
    )


def _resolve_attr_chain(node, module_bindings: dict):
    """Resolve ``a.b.c`` through the REAL objects; None when a step is absent.

    Matching only the LAST component let ``fdb.other.FalkorDB`` count as a
    client, which reds correct code — the false-positive class the suffix
    matcher was deleted for (found in review on #7053).

    A step that is a SUBMODULE is looked up in ``sys.modules`` when the parent
    exposes no such attribute. pytest's assertion-rewriting import hook imports a
    submodule WITHOUT setting it on its parent package — measured on this box:
    ``hasattr(falkordb, "falkordb")`` is False inside a pytest run and True in a
    plain interpreter — so an attribute-only walk counts ``import
    falkordb.falkordb; falkordb.falkordb.FalkorDB(...)`` as 0 under pytest and 1
    outside it. That environment dependence is the same defect class the import
    walk was rewritten to remove.
    """
    attrs = []
    root = node
    while isinstance(root, ast.Attribute):
        attrs.append(root.attr)
        root = root.value
    if not isinstance(root, ast.Name) or root.id not in module_bindings:
        return None
    value = module_bindings[root.id]
    for attr in reversed(attrs):
        step = getattr(value, attr, None)
        if step is None and isinstance(value, types.ModuleType):
            step = sys.modules.get(f"{value.__name__}.{attr}")
        if step is None:
            return None
        value = step
    return value


def _names_the_class(node, local_names: set, module_bindings: dict) -> bool:
    """True when an EXPRESSION's value is the class — by name or by chain."""
    if isinstance(node, ast.Name):
        return node.id in local_names
    if isinstance(node, ast.Attribute):
        return _resolve_attr_chain(node, module_bindings) is REAL_FALKORDB
    return False


def _count_client_sites(source: str) -> int:
    """Count calls whose callee IS the ``falkordb.FalkorDB`` object.

    Every import in the source is imported FOR REAL and its binding compared
    with `is` against `falkordb.FalkorDB` — the callee is never matched by
    identifier spelling. ``endswith("FalkorDB")`` counted ``_FalkorDB`` but was
    blind to a novel alias (#7053); enumerating import spellings instead only
    moves the hole, and review found a new one per round (``from
    falkordb.falkordb import FalkorDB``, then ``from falkordb import
    falkordb``). Identity closes the class instead of re-spelling it.

    A module-level REBIND is followed too (``FalkorDB = falkordb.FalkorDB``),
    because the removed suffix counter counted that call site: dropping it here
    would be a coverage REGRESSION, not the widening this change claims to be.

    Residual, deliberately NOT counted: indirection that never names the object
    — ``getattr(falkordb, "FalkorDB")``, a factory returning the class, or a
    rebind whose right-hand side this walk cannot resolve. The suffix counter
    missed those too.

    An unresolvable import is skipped, but ONLY for `ImportError`: a blanket
    `except` would map a genuine failure onto a count of 0, which is the exact
    vacuity this guard exists to prevent.
    """
    with warnings.catch_warnings():
        # Parsing a file that carries an invalid escape sequence in a plain
        # string literal raises SyntaxWarning on 3.12+. That is a property of the
        # file being CHECKED, not a finding, and emitting it here would turn a
        # `-W error` run red for a script this guard only counts.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    local_names: set[str] = set()            # names bound directly to the class
    module_bindings: dict[str, object] = {}  # root name -> the object exporting it

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module:
                continue  # `from . import x` — no absolute name to resolve
            try:
                owner = importlib.import_module(node.module)
            except ImportError:
                continue
            exported = getattr(owner, "__all__", None)
            for alias in node.names:
                if alias.name == "*":
                    # Whether a star import binds `FalkorDB` is itself an
                    # identity question, so ask the module rather than guess.
                    if (exported is None or "FalkorDB" in exported) and (
                        getattr(owner, "FalkorDB", None) is REAL_FALKORDB
                    ):
                        local_names.add("FalkorDB")
                    continue
                value = getattr(owner, alias.name, None)
                if value is None:
                    # `from pkg import sub` also binds a SUBMODULE, which the
                    # import system supplies even though `getattr(pkg, sub)` is
                    # absent until something imports it. Resolving only by
                    # attribute made the count depend on whether an earlier
                    # import had happened to run — order-dependent, and wrong.
                    try:
                        value = importlib.import_module(f"{node.module}.{alias.name}")
                    except ImportError:
                        value = None
                if value is REAL_FALKORDB:
                    local_names.add(alias.asname or alias.name)
                elif getattr(value, "FalkorDB", None) is REAL_FALKORDB:
                    module_bindings[alias.asname or alias.name] = value
        elif isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    value = importlib.import_module(alias.name)
                except ImportError:
                    continue
                if getattr(value, "FalkorDB", None) is REAL_FALKORDB:
                    # `import a.b` binds `a`, so `a.b.FalkorDB` resolves through
                    # attributes; `import a.b as c` binds `a.b` itself.
                    if alias.asname:
                        module_bindings[alias.asname] = value
                    else:
                        top = alias.name.split(".")[0]
                        module_bindings[top] = importlib.import_module(top)

    # A module-level assignment binds the class just as an import does.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if _names_the_class(value, local_names, module_bindings):
            for target in targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)

    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            n += fn.id in local_names
        elif isinstance(fn, ast.Attribute):
            n += _resolve_attr_chain(fn, module_bindings) is REAL_FALKORDB
    return n


def test_a_novel_alias_does_not_escape_the_count():
    """#7053 — the anti-vacuity counter must not depend on the alias SPELLING.

    The previous counter matched `called.endswith("FalkorDB")`, so
    `from falkordb import FalkorDB as _F` plus `_F(...)` was invisible: the site
    was neither observed by the behavioural tests nor counted, so the coverage
    claim went silently stale and a reintroduced #3081 would ship GREEN. This is
    the issue's own repro, run against the counter instead of a live module.
    """
    canonical = "from falkordb import FalkorDB\nclient = FalkorDB(host='h')\n"
    aliased = "from falkordb import FalkorDB as _F\nclient = _F(host='h')\n"
    module_attr = "import falkordb as f\nclient = f.FalkorDB(host='h')\n"
    assert _count_client_sites(canonical) == 1
    assert _count_client_sites(aliased) == 1, (
        "a novel alias must still be counted — this is exactly the #7053 gap"
    )
    assert _count_client_sites(module_attr) == 1
    # Spellings the REMOVED suffix matcher already caught, each of which a
    # name-resolution rewrite can silently lose (caught in review on #7053):
    # the submodule that defines the class, a star import, and the dotted forms.
    submodule = (
        "from falkordb.falkordb import FalkorDB\nclient = FalkorDB(host='h')\n"
    )
    star = "from falkordb import *\nclient = FalkorDB(host='h')\n"
    submodule_binding = "from falkordb import falkordb\nclient = falkordb.FalkorDB(host='h')\n"
    dotted = "import falkordb.falkordb\nc = falkordb.falkordb.FalkorDB(host='h')\n"
    dotted_as = "import falkordb.falkordb as f2\nc = f2.FalkorDB(host='h')\n"
    assert _count_client_sites(submodule) == 1, (
        "`from falkordb.falkordb import ...` is the class's own definition site"
    )
    assert _count_client_sites(star) == 1
    assert _count_client_sites(submodule_binding) == 1, (
        "a submodule binding is an identity question, not a spelling one"
    )
    assert _count_client_sites(dotted) == 1
    assert _count_client_sites(dotted_as) == 1

    # A module-level REBIND. The suffix matcher counted this call site (it
    # matched the spelling, whatever the binding was), so failing to follow the
    # assignment here would be a coverage REGRESSION, not the widening this
    # change claims to be — caught in review on #7053.
    rebind = (
        "import falkordb\nFalkorDB = falkordb.FalkorDB\n"
        "client = FalkorDB(host='h')\n"
    )
    rebind_alias = (
        "from falkordb import FalkorDB as _F\nAlias = _F\nclient = Alias(host='h')\n"
    )
    assert _count_client_sites(rebind) == 1, (
        "the removed suffix counter counted this; losing it is a regression"
    )
    assert _count_client_sites(rebind_alias) == 1

    # Multiplicity, so that a count of 1 is never a coincidence of arithmetic.
    two_aliases = (
        "from falkordb import FalkorDB as A, FalkorDB as B\n"
        "client = A(host='h')\nclient2 = B(host='h')\n"
    )
    alias_thrice = "from falkordb import FalkorDB as _F\n" + "".join(
        f"c{index} = _F(host='h')\n" for index in range(3)
    )
    assert _count_client_sites(two_aliases) == 2
    assert _count_client_sites(alias_thrice) == 3

    # Zero cases: an import with no call, and no source at all.
    assert _count_client_sites("from falkordb import FalkorDB\n") == 0
    assert _count_client_sites("") == 0

    # The other direction: a same-named callable from another module is not a
    # FalkorDB client. The foreign module must be REAL and IMPORTABLE — with an
    # unimportable name the counter returns 0 because the IMPORT failed, so the
    # assertion would hold even if the identity check were deleted (caught in
    # review on #7053).
    foreign = types.ModuleType("_elsewhere_probe")

    class _ForeignFalkorDB:
        pass

    foreign.FalkorDB = _ForeignFalkorDB
    sys.modules["_elsewhere_probe"] = foreign
    try:
        other_module = (
            "from _elsewhere_probe import FalkorDB\nclient = FalkorDB(host='h')\n"
        )
        other_attr = (
            "import _elsewhere_probe\nc = _elsewhere_probe.FalkorDB(host='h')\n"
        )
        assert _count_client_sites(other_module) == 0, (
            "only falkordb's own object may be counted, never a same-named import"
        )
        assert _count_client_sites(other_attr) == 0
    finally:
        del sys.modules["_elsewhere_probe"]

    # ...and an attribute chain that merely ENDS in `FalkorDB` is not the class:
    # resolving only the root name inflated the count and red correct code.
    assert _count_client_sites("import falkordb as f\nc = f.other.FalkorDB(h=1)\n") == 0


def test_patched_reaches_a_module_level_alias():
    """#7053, the other half: discovery by IDENTITY, not a fixed name list.

    ``_patched`` previously patched a list — ``(falkordb, "FalkorDB")``,
    ``(mod, "FalkorDB")``, ``(mod, "_FalkorDB")`` — so a module-level
    ``from falkordb import FalkorDB as _F`` was left UNPATCHED and the module
    built a real client: a live socket, in a file that promises none.

    No module in the table uses a novel alias today, which is exactly why this
    is pinned synthetically — otherwise the identity rewrite is exercised by no
    test at all and could be reverted silently (caught in review on #7053).
    """
    probe = types.ModuleType("_alias_probe")
    probe._F = REAL_FALKORDB  # the shape `from falkordb import FalkorDB as _F`
    try:
        with _patched(probe) as _calls:
            assert probe._F is _RecordingClient, (
                "a module-level alias must be patched too, or the module opens "
                "a real socket"
            )
        assert probe._F is REAL_FALKORDB, "the patch must be restored on exit"

        # ...and on the exception path, not only the happy one.
        with pytest.raises(_Stop), _patched(probe):
            raise _Stop
        assert probe._F is REAL_FALKORDB, (
            "the patch must be restored when the body raises, or a leaked "
            "recorder corrupts every later test"
        )
    finally:
        sys.modules.pop("_alias_probe", None)


def test_every_known_constructor_site_is_driven():
    """Anti-vacuity for the coverage claim, not a spelling check.

    Counts the ``FalkorDB(...)`` construction sites in the six files of the
    parser table and asserts the EXACT number per file, each of which a test
    above drives. An earlier version computed this and then asserted only
    ``>= 1``, so it could not fail when a second client appeared — the claim in
    this docstring was itself unenforceable, which is the failure class this
    whole file exists to remove. Exact counts fail on any new site, including
    one added after the first ``select_graph`` on a driven path (which truncates
    the drive, but not the count). A deliberate new site therefore forces a
    reviewed update here.

    The scan covers the WHOLE directory, not just the table: iterating the table
    could never see a module absent from it, so a fresh client path could enter
    ``graph-scripts`` and escape both the drive and the count. That is not
    hypothetical — ``merge_endometriosis.py`` was already such a site when this
    was written (review on #7053). ``NON_URI_SITES`` names the exceptions
    explicitly, so the decision is recorded rather than silent.
    """
    discovered = {
        module_file: _count_client_sites((GS / module_file).read_text(encoding="utf-8"))
        for module_file, _ in PARSERS
    }

    # EXACT counts, not `>= 1`. With `>= 1` this test could not fail when a
    # second client appeared — it computed the count and then asserted nothing
    # about it — so its own docstring claim ("fails loudly rather than letting
    # the claim silently go stale") was untrue. Exact counts also close the
    # shadowing case: a second client added after the first `select_graph`
    # truncates the driven path invisibly, but it still CHANGES THE COUNT, and
    # that is what fails here.
    assert discovered == {
        "audit_graph.py": 1,
        "audit_graph_deep.py": 1,
        "context_removal_audit.py": 1,
        "parity_sample.py": 1,
        "pre_migration_snapshot.py": 2,
        "rdb_snapshot_restore.py": 0,
    }, (
        f"the set of client construction sites changed: {discovered}. Every site "
        f"listed here must be reached by a test above (the two "
        f"pre_migration_snapshot.py sites are its two credential helpers). If you "
        f"added a site deliberately, drive it in a test and update this count; "
        f"rdb_snapshot_restore.py is 0 because it authenticates through "
        f"`redis-cli --user` and is covered by test_restore_container_recovery.py."
    )

    # A module OUTSIDE the table must not carry a client site either. Iterating
    # the table alone could never see a module absent from it, so a new client
    # path could enter graph-scripts/ and escape both the drive and the count —
    # the "guard that cannot fail" class this file exists to remove.
    outside = {
        path.name
        for path in sorted(GS.glob("*.py"))
        if path.name not in dict(PARSERS)
        and path.name not in NON_URI_SITES
        and _count_client_sites(path.read_text(encoding="utf-8"))
    }
    assert outside == set(), (
        f"{sorted(outside)} construct a FalkorDB client but are not in the table "
        f"above, so no test drives them. Add the module to PARSERS (with a test "
        f"that drives it), or to NON_URI_SITES if it has no URI to consume."
    )


# ── the two credential helpers ────────────────────────────────────────────

def test_pre_migration_helpers_deliver_the_username():
    """The helpers whose dropped keyword was silent for three review rounds."""
    mod = _load("pre_migration_snapshot.py")
    for user in ("alice", "bob"):
        for helper in (mod.trigger_bgsave, mod.check_rdb):
            with _patched(mod) as calls, contextlib.suppress(_Stop):
                helper("example.invalid", 16379, "pw", username=user)
            assert calls, f"{helper.__name__} never built a client"
            got = calls[-1]
            assert got.get("username") == user, (
                f"{helper.__name__} did not forward the username — the client "
                f"would authenticate as the default user (#3081). Got {got!r}"
            )
            assert got.get("password") == "pw", f"{helper.__name__} lost the password"


def test_pre_migration_helpers_send_none_for_the_anonymous_form():
    """The ANONYMOUS form, and the `""` -> `None` conversion."""
    mod = _load("pre_migration_snapshot.py")
    for helper in (mod.trigger_bgsave, mod.check_rdb):
        with _patched(mod) as calls, contextlib.suppress(_Stop):
            helper("example.invalid", 16379, "pw", username="")
        assert calls, f"{helper.__name__} never built a client"
        assert calls[-1].get("username") is None, (
            f"{helper.__name__} must convert an empty username to None; got "
            f"{calls[-1].get('username')!r}"
        )


def test_pre_migration_helpers_require_the_username():
    """The root-cause fix: the silent default is gone.

    ``username`` used to default to ``""``, so a call site that omitted it
    authenticated as the DEFAULT user with no error — the failure this whole
    file exists to catch, and the reason a static AST guard was needed to police
    the call sites. As a required keyword-only parameter, every call site fails
    loudly instead, which covers every spelling at once: positional, keyword,
    ``*args``, ``**kwargs``, ``functools.partial``.
    """
    mod = _load("pre_migration_snapshot.py")
    for helper in (mod.trigger_bgsave, mod.check_rdb):
        with pytest.raises(TypeError, match="username"):
            helper("example.invalid", 16379, "pw")


def _drive_main_call_sites(argv, docker_ok=False):
    """Run the module's ``main()`` and record what the CALL SITES passed.

    Behavioural, and no AST: the helper call sites inside ``main`` are replaced
    by recorders, so what they were handed is OBSERVED rather than
    pattern-matched.
    """
    mod = _load("pre_migration_snapshot.py")
    captured = {}

    def _record(name, ok):
        def _rec(*args, **kwargs):
            captured[name] = {"args": args, "kwargs": kwargs}
            return {"ok": ok, "message": "recorded", "timestamp": "t", "dir": "d",
                    "dbfilename": "f", "dbsize": 0, "lastsave": 0, "lastsave_utc": "u"}
        return _rec

    replacements = {
        "trigger_bgsave": _record("trigger_bgsave", True),
        "check_rdb": _record("check_rdb", True),
        # Not-ok forces main() down the SDK-level fallback, which is the only
        # path that reaches the check_rdb call site.
        "check_rdb_via_docker": _record("check_rdb_via_docker", docker_ok),
    }
    saved = {}
    for name, fn in replacements.items():
        if hasattr(mod, name):
            saved[name] = getattr(mod, name)
            setattr(mod, name, fn)
    saved_argv = sys.argv
    sys.argv = ["pre_migration_snapshot.py", *argv]
    try:
        with contextlib.suppress(SystemExit):
            mod.main()
    finally:
        sys.argv = saved_argv
        for name, fn in saved.items():
            setattr(mod, name, fn)
    return mod, captured


@pytest.mark.parametrize("uri,expected", [(NAMED, "alice"), (NAMED2, "bob")])
def test_the_trigger_bgsave_call_site_passes_the_username(uri, expected):
    """The call site, OBSERVED — compared against the parsed config, not a literal."""
    mod, captured = _drive_main_call_sites(["--uri", uri, "--trigger-bgsave"])
    assert mod is not None  # the module is loaded as a side effect of driving main()
    assert "trigger_bgsave" in captured, (
        "main() never reached the trigger_bgsave call site — this test would "
        "otherwise pass without exercising anything"
    )
    assert captured["trigger_bgsave"]["kwargs"].get("username") == expected, (
        f"the trigger_bgsave call site handed "
        f"username={captured['trigger_bgsave']['kwargs'].get('username')!r}, "
        f"expected {expected!r} — dropping it authenticates as the default user"
    )


@pytest.mark.parametrize("uri,expected", [(NAMED, "alice"), (NAMED2, "bob")])
def test_the_check_rdb_call_site_passes_the_username(uri, expected):
    """Same, on the fallback path that reaches the second call site."""
    _, captured = _drive_main_call_sites(["--uri", uri], docker_ok=False)
    assert "check_rdb" in captured, "main() never reached the check_rdb fallback call site"
    assert captured["check_rdb"]["kwargs"].get("username") == expected, (
        f"the check_rdb call site handed "
        f"username={captured['check_rdb']['kwargs'].get('username')!r}, "
        f"expected {expected!r}"
    )


def test_the_call_site_recorders_can_fail():
    """Anti-vacuity: the recorder must actually see the call it claims to.

    The assertion is deliberately SPELLING-AGNOSTIC: pinning the positional
    arguments would red a semantically identical fully-keyword call site, the
    false-red class that justified deleting the AST matcher.
    """
    _, captured = _drive_main_call_sites(["--uri", NAMED, "--trigger-bgsave"])
    call = captured["trigger_bgsave"]
    seen = dict(zip(("host", "port"), call["args"], strict=False))
    seen.update(call["kwargs"])
    assert (seen.get("host"), seen.get("port")) == ("localhost", 16379), (
        f"the recorder did not capture the real call: {captured!r}"
    )
