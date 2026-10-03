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
The invariant is about RUNTIME behaviour — did the username reach the client —
and an AST check can only approximate it by enumerating spellings. Four rounds
of review on this PR each found a spelling such a matcher missed (aliased
constructor, ``username=None``, a keyword-form call site, a positional
constructor argument) and three it wrongly rejected. Each false negative is a
green CI on a reintroduced #3081; each false positive reds correct code.

So this file observes the actual call instead, and the production code carries
the guarantee: ``trigger_bgsave`` / ``check_rdb`` take ``username`` as a
REQUIRED keyword-only parameter, so a call site that omits it fails loudly with
``TypeError`` rather than silently authenticating as the default user.

COVERAGE
--------
``rdb_snapshot_restore.py`` builds no ``FalkorDB`` client at all — it
authenticates through ``redis-cli --user`` — so it has no constructor to
observe here. Its ``--user`` consumption is pinned by
``tests/test_restore_container_recovery.py::test_named_user_uri_authenticates_end_to_end``
and ``::test_redis_cli_uses_env_not_argv``. It is included in the parser table
because #3081 listed it, and it was already correct.
"""
from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path
from typing import ClassVar

import pytest

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

NAMED = "redis://alice:pw@localhost:16379/tortoise"
ANON = "docker://:pw@localhost:16379/tortoise"


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


class _Recording:
    """Stand-in for FalkorDB that records the kwargs it was constructed with."""

    calls: ClassVar[list] = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)
        self.connection = _NoopConnection()

    def select_graph(self, name):
        return self


class _NoopConnection:
    def execute_command(self, *a, **k):
        return ""  # let the helpers run past the constructor and stop naturally


# ── the parser half ───────────────────────────────────────────────────────
# Behavioural already: it calls the real parser and reads the real dict.

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


# ── the consumer half — observe the REAL call ─────────────────────────────

def _cfg_for(module_file: str, uri: str) -> dict:
    fn = next(p[1] for p in PARSERS if p[0] == module_file)
    return getattr(_load(module_file), fn)(uri)


def _patched(mod):
    """Patch every name the module could resolve FalkorDB through.

    ``audit_graph*.py`` bind it at MODULE level (``from falkordb import
    FalkorDB``), so patching ``falkordb.FalkorDB`` alone does not reach them and
    the test would open a REAL socket. The other three import it inside the
    function, so ``falkordb`` is the one that matters there. Patch both.
    """
    import falkordb

    @contextlib.contextmanager
    def cm():
        originals = {"falkordb": falkordb.FalkorDB}
        falkordb.FalkorDB = _Recording
        if hasattr(mod, "FalkorDB"):
            originals["module"] = mod.FalkorDB
            mod.FalkorDB = _Recording
        try:
            yield
        finally:
            falkordb.FalkorDB = originals["falkordb"]
            if "module" in originals:
                mod.FalkorDB = originals["module"]

    return cm()


def _assert_delivered(module_file, uri, why):
    """Construct through the module's own path; assert the username arrived."""
    cfg = _cfg_for(module_file, uri)
    mod = _load(module_file)
    expected = cfg["username"] or None

    _Recording.calls = []
    with _patched(mod):
        if module_file in ("audit_graph.py", "audit_graph_deep.py"):
            # These build lazily on first use, from a module-level config.
            mod._cfg = cfg
            mod._CONN.clear()
            mod._connect("DB")
        elif hasattr(mod, "_build_client"):
            mod._build_client(cfg)
        else:  # pragma: no cover - guards against a silent skip
            raise AssertionError(f"no construction path exercised for {module_file}")

    assert _Recording.calls, f"{why}: {module_file} never built a client"
    got = _Recording.calls[-1]
    assert got.get("username") == expected, (
        f"{why}: {module_file} built the client with username="
        f"{got.get('username')!r}, expected {expected!r} — a named-user URI "
        f"would authenticate as the default user (#3081)"
    )
    assert got.get("password") == (cfg["password"] or None), (
        f"{why}: {module_file} lost the password: {got.get('password')!r}"
    )


@pytest.mark.parametrize(
    "module_file",
    ["context_removal_audit.py", "parity_sample.py"],
)
def test_the_seam_is_actually_used_by_main(module_file):
    """The last hop: `main()` must go THROUGH the seam, not around it.

    Observing `_build_client` in isolation proves the seam is correct but not
    that anything calls it. Replacing `db = _build_client(cfg)` with an inline
    `_FalkorDB(...)` that drops the username restores #3081 on the real entry
    path while the seam — and its test — stay correct and green. The deleted AST
    matcher was exhaustive over constructor calls and *would* have caught that,
    so this hop is the price of deleting it.

    The seam call sits before `main()`'s ``try``, so the recorder raises to stop
    the run as soon as it has captured what it was handed.
    """
    mod = _load(module_file)
    captured = {}

    class _Stop(Exception):
        pass

    def _recorder(cfg, *args, **kwargs):
        captured["cfg"] = cfg
        raise _Stop

    saved = mod._build_client
    saved_argv = sys.argv
    mod._build_client = _recorder
    sys.argv = [module_file, "--uri", NAMED]
    try:
        with contextlib.suppress(_Stop, SystemExit):
            mod.main()
    finally:
        mod._build_client = saved
        sys.argv = saved_argv

    assert "cfg" in captured, (
        f"{module_file}.main() never called _build_client — it must build its "
        f"client through the seam, or this file's construction path is not "
        f"observed by any test (#3081)."
    )
    got = captured["cfg"].get("username")
    assert got == "alice", (
        f"{module_file}.main() handed the seam username={got!r}, expected "
        f"'alice' — a named-user URI would authenticate as the default user."
    )


@pytest.mark.parametrize(
    "module_file",
    [
        "audit_graph.py",
        "audit_graph_deep.py",
        "context_removal_audit.py",
        "parity_sample.py",
    ],
)
def test_named_user_reaches_the_constructor(module_file):
    """The seam each file actually uses to build its client."""
    _assert_delivered(module_file, NAMED, "named-user URI")


@pytest.mark.parametrize(
    "module_file",
    [
        "audit_graph.py",
        "audit_graph_deep.py",
        "context_removal_audit.py",
        "parity_sample.py",
    ],
)
def test_anonymous_uri_sends_none_not_empty(module_file):
    """`docker://:pw@host` must send None, which is what redis-py expects."""
    _assert_delivered(module_file, ANON, "anonymous URI")


def test_pre_migration_helpers_deliver_the_username():
    """The two helpers whose dropped keyword was silent for three review rounds."""
    mod = _load("pre_migration_snapshot.py")
    for helper in (mod.trigger_bgsave, mod.check_rdb):
        _Recording.calls = []
        with _patched(mod):
            helper("example.invalid", 16379, "pw", username="alice")
        assert _Recording.calls, f"{helper.__name__} never built a client"
        got = _Recording.calls[-1]
        assert got.get("username") == "alice", (
            f"{helper.__name__} did not forward the username — the client would "
            f"authenticate as the default user (#3081). Got {got!r}"
        )
        assert got.get("password") == "pw", f"{helper.__name__} lost the password"


def test_pre_migration_helpers_send_none_for_the_anonymous_form():
    """The ANONYMOUS form, and the `""` -> `None` conversion.

    `docker://:pw@host` carries no username, so the client must be handed
    ``None`` rather than ``""``. (They happen to be equivalent in redis-py —
    ``UsernamePasswordCredentialProvider`` normalises a falsy username — but
    the conversion is the contract, and without this test a hardcoded or
    unreduced username in either helper goes unnoticed.)
    """
    mod = _load("pre_migration_snapshot.py")
    for helper in (mod.trigger_bgsave, mod.check_rdb):
        _Recording.calls = []
        with _patched(mod):
            helper("example.invalid", 16379, "pw", username="")
        assert _Recording.calls, f"{helper.__name__} never built a client"
        assert _Recording.calls[-1].get("username") is None, (
            f"{helper.__name__} must convert an empty username to None; got "
            f"{_Recording.calls[-1].get('username')!r}"
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


def _drive_main_and_capture(module_file, argv, docker_ok=False):
    """Run the module's ``main()`` and record what the CALL SITES passed.

    Behavioural, and no AST: the helper call sites inside ``main`` are replaced
    by recorders, so what they were handed is OBSERVED rather than
    pattern-matched. A static matcher for this was deleted after four review
    rounds each found a spelling it missed (literal name, ``None``, keyword-form
    call, positional constructor); watching the call cannot miss one.
    """
    mod = _load(module_file)
    captured = {}

    def _result(ok):
        return {"ok": ok, "message": "recorded", "timestamp": "t", "dir": "d",
                "dbfilename": "f", "dbsize": 0, "lastsave": 0, "lastsave_utc": "u"}

    def _record(name, ok):
        def _rec(*args, **kwargs):
            captured[name] = {"args": args, "kwargs": kwargs}
            return _result(ok)
        return _rec

    saved = {}
    replacements = {
        "trigger_bgsave": _record("trigger_bgsave", True),
        "check_rdb": _record("check_rdb", True),
        # Returning not-ok here forces main() down the SDK-level fallback, which
        # is the only path that reaches the check_rdb call site.
        "check_rdb_via_docker": _record("check_rdb_via_docker", docker_ok),
    }
    for name, fn in replacements.items():
        if hasattr(mod, name):
            saved[name] = getattr(mod, name)
            setattr(mod, name, fn)
    saved_argv = sys.argv
    sys.argv = argv
    try:
        with contextlib.suppress(SystemExit):
            mod.main()
    finally:
        sys.argv = saved_argv
        for name, fn in saved.items():
            setattr(mod, name, fn)
    return captured


def test_the_trigger_bgsave_call_site_passes_the_username():
    """The call site round 3's AST matcher pronounced safe, now OBSERVED."""
    captured = _drive_main_and_capture(
        "pre_migration_snapshot.py",
        ["pre_migration_snapshot.py", "--uri", NAMED, "--trigger-bgsave"],
    )
    assert "trigger_bgsave" in captured, (
        "main() never reached the trigger_bgsave call site — this test would "
        "otherwise pass without exercising anything"
    )
    got = captured["trigger_bgsave"]["kwargs"].get("username")
    assert got == "alice", (
        f"the trigger_bgsave call site handed username={got!r}, expected 'alice' "
        f"— dropping it authenticates as the default user (#3081)"
    )


def test_the_check_rdb_call_site_passes_the_username():
    """Same, on the fallback path that reaches the second call site."""
    captured = _drive_main_and_capture(
        "pre_migration_snapshot.py",
        ["pre_migration_snapshot.py", "--uri", NAMED],
        docker_ok=False,
    )
    assert "check_rdb" in captured, (
        "main() never reached the check_rdb fallback call site"
    )
    got = captured["check_rdb"]["kwargs"].get("username")
    assert got == "alice", (
        f"the check_rdb call site handed username={got!r}, expected 'alice' "
        f"— dropping it authenticates as the default user (#3081)"
    )


def test_the_call_site_recorders_can_fail():
    """Anti-vacuity: the recorder must actually see the call it claims to.

    Without this, a typo in the patched name would leave both call-site tests
    green while observing nothing at all.

    The assertion is deliberately SPELLING-AGNOSTIC. Pinning `args[:2]` would
    red a semantically identical fully-keyword call site — the same false-red
    class that justified deleting the AST matcher in the first place.
    """
    captured = _drive_main_and_capture(
        "pre_migration_snapshot.py",
        ["pre_migration_snapshot.py", "--uri", NAMED, "--trigger-bgsave"],
    )
    call = captured["trigger_bgsave"]
    seen = dict(zip(("host", "port"), call["args"], strict=False))
    seen.update(call["kwargs"])
    assert (seen.get("host"), seen.get("port")) == ("localhost", 16379), (
        f"the recorder did not capture the real call: {captured!r}"
    )
