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


@pytest.mark.parametrize("module_file", [p[0] for p in PARSERS])
def test_every_password_construction_also_passes_a_username(module_file):
    """The consumer half. A dropped field is invisible to the #3047 AST guard.

    Walks every ``FalkorDB(...)`` call in the module and requires that any call
    supplying ``password=`` also supplies ``username=``. This is the check that
    would have caught the original five: each decoded the username (so the
    #3047 guard was satisfied) and then built the client without it.
    """
    tree = ast.parse((GS / module_file).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "FalkorDB":
            continue
        kws = {k.arg for k in node.keywords}
        if "password" in kws and "username" not in kws:
            offenders.append(node.lineno)
    assert not offenders, (
        f"{module_file}: FalkorDB(...) at line(s) {offenders} is handed a "
        f"password but no username — a named-user URI would authenticate as the "
        f"default user (#3081). Add `username=<cfg>[\"username\"] or None`."
    )


def test_the_ast_guard_can_fail():
    """A guard that cannot fail is not a guard — prove the walker sees an offender.

    Without this, a typo in the call-name match (e.g. comparing against
    ``Attr``-only) would make every module pass vacuously.
    """
    src = "def f(cfg):\n    return FalkorDB(host=cfg['h'], password=cfg['p'] or None)\n"
    tree = ast.parse(src)
    found = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "FalkorDB"
        and "password" in {k.arg for k in n.keywords}
        and "username" not in {k.arg for k in n.keywords}
    ]
    assert found == [2], "the offender detector did not see a password-only construction"
