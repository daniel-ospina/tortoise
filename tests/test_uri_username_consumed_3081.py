"""#3081 — the URI username must be CONSUMED, not merely decoded.

`parse_uri_userinfo` (tortoise/config.py, #3039) is the single percent-decoding
rule for URI credentials. Its contract is that BOTH fields come back. Six
`graph-scripts` helpers bound the username to a discarded ``_username`` and
returned only the password, so a named-user URI
(``redis://user:pw@host``) reached ``FalkorDB(...)`` with no username and
authenticated as the DEFAULT user — a misleading ``AuthenticationError`` /
``NOPERM``.

The pre-existing AST guard from PR #3047 proves no helper reads a RAW
(undecoded) ``parsed.password``. It cannot see a *dropped* field, which is why
the defect survived: decoding correctly and then discarding the result passes
that guard. The class-level invariant this file pins is therefore about the
CONSUMER, not the decoder:

    every FalkorDB(...) construction that is handed a password must also be
    handed the username.

Both halves are checked, because either alone is insufficient: returning the
username from the parser does nothing if the constructor ignores it, and
passing ``cfg["username"]`` is a ``KeyError`` if the parser never produced it.

`graph-scripts/rdb_snapshot_restore.py` is the reference implementation — it was
already correct when #3081 was filed (its ``parse_uri`` returns ``username`` and
its ``_redis_cli`` consumes it as ``--user``), and its tests in
``tests/test_wiring_phase01.py`` already assert the named-user case. It is
included here so the invariant covers all six helpers rather than the five that
happened to be broken.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import ClassVar

import pytest

REPO = Path(__file__).resolve().parent.parent
GS = REPO / "graph-scripts"

# The six helpers #3081 names, with the function that parses the URI.
PARSERS = [
    ("audit_graph.py", "_parse_uri"),
    ("audit_graph_deep.py", "_parse_uri"),
    ("context_removal_audit.py", "_parse_uri"),
    ("parity_sample.py", "_parse_uri"),
    ("pre_migration_snapshot.py", "_parse_uri"),
    ("rdb_snapshot_restore.py", "parse_uri"),
]


def _load(module_file: str):
    """Import a graph-scripts module without executing its __main__ block.

    ``graph-scripts/`` is put on ``sys.path`` first: when these modules are run
    directly that directory is ``sys.path[0]``, so sibling imports such as
    ``from connectivity_gate import ...`` (rdb_snapshot_restore.py) resolve.
    Imported from a test they do not, so the test supplies what the script's
    own run context supplies.
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


def _bare_uri(module_file: str) -> str:
    """A URI shaped like the one each helper defaults to (docker://, port 16379)."""
    mod = _load(module_file)
    fn = getattr(mod, next(p[1] for p in PARSERS if p[0] == module_file))
    # Probe with the anonymous form to learn the default port, then build the
    # named-user form against it. Keeps this test independent of each helper's
    # hard-coded default (16379 vs 6379).
    base = fn("docker://:pw@localhost:16379/tortoise")
    return f"redis://alice:pw@localhost:{base['port']}/tortoise"


@pytest.mark.parametrize("module_file,fn_name", PARSERS)
def test_named_user_uri_surfaces_the_username(module_file, fn_name):
    """The parser must RETURN the username — dropping it is the defect."""
    fn = getattr(_load(module_file), fn_name)
    cfg = fn(_bare_uri(module_file))
    assert cfg["username"] == "alice", (
        f"{module_file}::{fn_name} dropped the decoded username; a named-user "
        f"URI authenticates as the default user (#3081)"
    )


@pytest.mark.parametrize("module_file,fn_name", PARSERS)
def test_anonymous_uri_yields_empty_username(module_file, fn_name):
    """`docker://:pw@host` carries an EMPTY username -> "" (client default)."""
    fn = getattr(_load(module_file), fn_name)
    cfg = fn(_bare_uri(module_file).replace("alice", ""))
    assert cfg["username"] == ""


def _falkordb_names(tree: ast.AST) -> set[str]:
    """Names bound to ``falkordb.FalkorDB`` by this module's imports.

    ``context_removal_audit.py`` and ``parity_sample.py`` do
    ``from falkordb import FalkorDB as _FalkorDB`` and call ``_FalkorDB(...)``.
    Matching the literal spelling ``FalkorDB`` therefore skipped exactly those
    two modules — the guard could not fail on them, so a future deletion of
    ``username=`` there would have been a green-CI return of #3081. Resolve the
    name from the import instead of guessing it from the call site.
    """
    names = {"FalkorDB"}  # a bare `FalkorDB(...)` needs no import to be matched
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("falkordb"):
            for alias in node.names:
                if alias.name == "FalkorDB":
                    names.add(alias.asname or alias.name)
    return names


def _password_only_constructors(tree: ast.AST) -> list[int]:
    """Line numbers of FalkorDB(...) calls handed a password but no username."""
    names = _falkordb_names(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called not in names:
            continue
        kws = {k.arg for k in node.keywords}
        if "password" in kws and "username" not in kws:
            offenders.append(node.lineno)
    return offenders


def _unwrap_or_none(value: ast.expr) -> ast.expr:
    """``X or None`` -> ``X``; anything else unchanged."""
    if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
        left = value.values[0]
        if len(value.values) == 2 and _is_none(value.values[1]):
            return left
    return value


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_username_derived(value: ast.expr) -> bool:
    """True if ``value`` is the parsed URI username rather than a literal.

    Accepts the two shapes this codebase actually uses:

        username=<mapping>["username"] or None   # the decoded URI field
        username=username or None                # the helper's own parameter

    The point is PROVENANCE, not presence: the first version of this guard
    only checked that a ``username=`` keyword existed, so ``username=None`` —
    which discards the decoded username and reintroduces #3081 exactly —
    satisfied it.
    """
    value = _unwrap_or_none(value)
    if isinstance(value, ast.Subscript):
        sl = value.slice
        if isinstance(sl, ast.Constant) and sl.value == "username":
            return True
    if isinstance(value, ast.Name):
        return "username" in value.id
    return False


def _unprovenanced_usernames(tree: ast.AST) -> list[int]:
    """FalkorDB(...) calls whose ``username=`` value is not the URI username."""
    names = _falkordb_names(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called not in names:
            continue
        for kw in node.keywords:
            if kw.arg == "username" and not _is_username_derived(kw.value):
                offenders.append(node.lineno)
    return offenders


# Helpers that take credentials and build a client. Their own `username`
# parameter DEFAULTS to `""`, so dropping the keyword at a call site is SILENT:
# the client authenticates as the default user and no constructor-level check
# can see it (the constructor still reads `username=username or None`).
CREDENTIAL_HELPERS = ("trigger_bgsave", "check_rdb")


def _call_sites_missing_username(tree: ast.AST) -> list[int]:
    """Calls to a credential-taking helper with no username-derived argument.

    This sits one level ABOVE the constructor check. A behavioural test that
    calls the helper directly with ``username="alice"`` exercises the helper's
    plumbing but not the call site that reads it out of the parsed config — so
    deleting the keyword at the call site leaves such a test green. Observed:
    both ``pre_migration_snapshot.py`` call sites dropped the keyword and the
    suite stayed at 33 passed. This is the check that closes it.
    """
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if called not in CREDENTIAL_HELPERS:
            continue
        # These helpers forward credentials; `host, port, password` are the
        # first three positional parameters, and `username` follows by keyword.
        if len(node.args) < 3:
            continue
        kws = {k.arg: k.value for k in node.keywords}
        if "username" not in kws or not _is_username_derived(kws["username"]):
            offenders.append(node.lineno)
    return offenders


@pytest.mark.parametrize("module_file", [p[0] for p in PARSERS])
def test_every_password_construction_also_passes_a_username(module_file):
    """The consumer half. A dropped field is invisible to the #3047 AST guard.

    Walks every ``FalkorDB(...)`` call in the module — including the aliased
    ``_FalkorDB(...)`` spelling two of these modules use — and requires that any
    call supplying ``password=`` also supplies ``username=``. This is the check
    that would have caught the original five: each decoded the username (so the
    #3047 guard was satisfied) and then built the client without it.
    """
    tree = ast.parse((GS / module_file).read_text(encoding="utf-8"))
    offenders = _password_only_constructors(tree)
    assert not offenders, (
        f"{module_file}: FalkorDB(...) at line(s) {offenders} is handed a "
        f"password but no username — a named-user URI would authenticate as the "
        f"default user (#3081). Add `username=<cfg>[\"username\"] or None`."
    )


def test_the_ast_guard_can_fail_on_both_spellings():
    """A guard that cannot fail is not a guard.

    This canary drives the REAL matcher (``_password_only_constructors``), not a
    copy of its logic, because a copy would stay green while the matcher was
    broken. It covers both the literal and the aliased spelling — the alias case
    is the one that silently passed for two of the six modules when this guard
    first landed.
    """
    literal = (
        "def f(cfg):\n"
        "    return FalkorDB(host=cfg['h'], password=cfg['p'] or None)\n"
    )
    aliased = (
        "from falkordb import FalkorDB as _FalkorDB\n"
        "\n"
        "def f(cfg):\n"
        "    return _FalkorDB(host=cfg['h'], password=cfg['p'] or None)\n"
    )
    compliant = (
        "from falkordb import FalkorDB as _FalkorDB\n"
        "\n"
        "def f(cfg):\n"
        "    return _FalkorDB(host=cfg['h'], username=cfg['u'] or None,\n"
        "                     password=cfg['p'] or None)\n"
    )
    assert _password_only_constructors(ast.parse(literal)) == [2]
    assert _password_only_constructors(ast.parse(aliased)) == [4], (
        "the matcher missed the ALIASED constructor (_FalkorDB) — the hole that "
        "left context_removal_audit.py and parity_sample.py unguarded"
    )
    assert _password_only_constructors(ast.parse(compliant)) == []


def test_real_modules_use_an_aliased_constructor():
    """Anti-vacuity: the alias path above must actually be exercised by a real
    module, otherwise this suite's alias coverage is theoretical."""
    aliased = [
        m
        for m, _ in PARSERS
        if "_FalkorDB" in _falkordb_names(
            ast.parse((GS / m).read_text(encoding="utf-8"))
        )
    ]
    assert aliased, (
        "no module in PARSERS uses an aliased FalkorDB constructor — if that "
        "changes, drop this test rather than let it pass vacuously"
    )


@pytest.mark.parametrize("module_file", [p[0] for p in PARSERS])
def test_the_username_value_is_the_uri_username_not_a_literal(module_file):
    """Provenance, not presence.

    ``username=None`` satisfies a presence-only check while discarding the
    decoded username and reintroducing #3081 — so assert the value is derived
    from the parsed URI (or the helper's own parameter), never a literal.
    """
    tree = ast.parse((GS / module_file).read_text(encoding="utf-8"))
    offenders = _unprovenanced_usernames(tree)
    assert not offenders, (
        f"{module_file}: FalkorDB(...) at line(s) {offenders} passes a username "
        f"that is not the parsed URI username (e.g. a literal or None) — that "
        f"discards the decoded credential and reintroduces #3081."
    )


def test_the_provenance_check_can_fail():
    """The provenance check must reject the shapes that reintroduce #3081."""
    good = (
        "def f(cfg):\n"
        "    return FalkorDB(host=cfg['h'], username=cfg['username'] or None,\n"
        "                     password=cfg['p'] or None)\n"
    )
    from_param = (
        "def f(host, password, username):\n"
        "    return FalkorDB(host=host, username=username or None,\n"
        "                     password=password or None)\n"
    )
    none_literal = (
        "def f(cfg):\n"
        "    return FalkorDB(host=cfg['h'], username=None,\n"
        "                     password=cfg['p'] or None)\n"
    )
    hardcoded = (
        "def f(cfg):\n"
        "    return FalkorDB(host=cfg['h'], username='admin',\n"
        "                     password=cfg['p'] or None)\n"
    )
    assert _unprovenanced_usernames(ast.parse(good)) == []
    assert _unprovenanced_usernames(ast.parse(from_param)) == []
    assert _unprovenanced_usernames(ast.parse(none_literal)) == [2], (
        "username=None discards the decoded username and must be rejected"
    )
    assert _unprovenanced_usernames(ast.parse(hardcoded)) == [2]


def test_call_sites_forward_the_username_from_the_config():
    """The call sites that read the username out of the parsed URI.

    One level above the constructor: `trigger_bgsave`/`check_rdb` default
    `username` to `""`, so a dropped keyword silently authenticates as the
    default user while every constructor-level check stays green.
    """
    offenders = _call_sites_missing_username(
        ast.parse((GS / "pre_migration_snapshot.py").read_text(encoding="utf-8"))
    )
    assert not offenders, (
        f"pre_migration_snapshot.py: helper call(s) at line(s) {offenders} drop "
        f"the username — the helper defaults it to \"\" and the client would "
        f"authenticate as the default user (#3081)."
    )


def test_the_call_site_check_can_fail():
    """This check must reject exactly the deletion round 2 found surviving."""
    dropped = (
        "def f(cfg):\n"
        "    return trigger_bgsave(cfg['host'], cfg['port'], cfg['password'])\n"
    )
    kept = (
        "def f(cfg):\n"
        "    return trigger_bgsave(cfg['host'], cfg['port'], cfg['password'],\n"
        "                          username=cfg['username'])\n"
    )
    literal = (
        "def f(cfg):\n"
        "    return check_rdb(cfg['host'], cfg['port'], cfg['password'],\n"
        "                     username='admin')\n"
    )
    assert _call_sites_missing_username(ast.parse(dropped)) == [2]
    assert _call_sites_missing_username(ast.parse(kept)) == []
    assert _call_sites_missing_username(ast.parse(literal)) == [2]


def test_the_reference_file_stays_in_the_invariant():
    """`rdb_snapshot_restore.py` was already correct, so it needed no change —
    but it must stay in PARSERS, or the invariant would silently cover only the
    five files that happened to be broken and stop pinning the reference.
    """
    assert len(PARSERS) == 6  # five fix sites + the already-correct reference
    assert ("rdb_snapshot_restore.py", "parse_uri") in PARSERS


class _RecordingFalkorDB:
    """Stand-in for FalkorDB that records the kwargs it was constructed with."""

    calls: ClassVar[list] = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)
        self.connection = _ExplodingConnection()


class _ExplodingConnection:
    def execute_command(self, *a, **k):
        raise RuntimeError("stop after recording the constructor call")


def test_pre_migration_helpers_forward_the_username(monkeypatch):
    """BEHAVIOURAL: the plumbing that carries the username to the client.

    ``pre_migration_snapshot``'s two helpers take ``username`` as a parameter
    defaulting to ``""``. Omitting it at the call site is therefore SILENT: the
    client authenticates as the default user. An AST check on the constructor
    cannot see that (the constructor still reads ``username=username or None``)
    and neither can the parser test, so a one-line deletion at either call site
    would have left the whole suite green. This drives the real call path.
    """
    import falkordb

    mod = _load("pre_migration_snapshot.py")
    for helper in (mod.trigger_bgsave, mod.check_rdb):
        _RecordingFalkorDB.calls = []
        monkeypatch.setattr(falkordb, "FalkorDB", _RecordingFalkorDB, raising=False)
        helper("example.invalid", 16379, "pw", username="alice")
        assert _RecordingFalkorDB.calls, f"{helper.__name__} never built a client"
        got = _RecordingFalkorDB.calls[-1]
        assert got.get("username") == "alice", (
            f"{helper.__name__} did not forward the username — the client would "
            f"authenticate as the default user (#3081). Got {got!r}"
        )
        assert got.get("password") == "pw", (
            f"{helper.__name__} lost the password: {got!r}"
        )


def test_pre_migration_helpers_send_no_username_for_the_anonymous_form(monkeypatch):
    """The ``docker://:pw@host`` form must send ``None``, not ``""``."""
    import falkordb

    mod = _load("pre_migration_snapshot.py")
    _RecordingFalkorDB.calls = []
    monkeypatch.setattr(falkordb, "FalkorDB", _RecordingFalkorDB, raising=False)
    mod.trigger_bgsave("example.invalid", 16379, "pw")
    assert _RecordingFalkorDB.calls[-1].get("username") is None
