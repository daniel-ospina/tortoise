"""#3039 — percent-DECODE URI userinfo through the single shared rule.

``FalkorProjection.from_uri`` parsed the connection URI with
``urllib.parse.urlparse`` and fed ``parsed.username`` / ``parsed.password``
straight to ``FalkorDB(...)``. ``urlparse`` does NOT percent-decode userinfo;
``redis.from_url`` DOES. A password needing percent-encoding (the correct way
to carry ``@``/``:``/``/``/``?``/``#``/``%`` in a URI) therefore reached the
client as a literal ``%XX`` and auth failed with the misleading message
``invalid username-password pair or user is disabled`` — proven in production
2026-09-11 (raw len 17 vs decoded 15, ``'%' in raw``).

The rule now lives in exactly one place, ``tortoise.config.parse_uri_userinfo``.
These tests pin:

1. **Round trip** — every hostile character survives decode, on the two
   production consumers (``from_uri`` → FalkorDB, ``_admin_client`` → redis).
2. **Negative controls** — ``+`` is a literal plus (``unquote``, not
   ``unquote_plus``); an *unencoded* clean password and a percent-escaped one
   parse to the same plaintext; absent/empty userinfo stays ``None``.
3. **Source guard** — a future refactor cannot reintroduce a raw
   ``urlparse(...).username``/``.password`` read anywhere under ``tortoise/``
   or ``graph-scripts/`` (the display-only
   ``graph-scripts/connectivity_gate.py`` is allowlisted), plus the two
   test-infra modules this fix converted. Bindings covered: plain/annotated
   assignment, attribute targets, unpacking (positional), walrus, ``for`` and
   ``with`` targets. It is a prompt to look, not a proof — index surgery and
   cross-function aliasing remain out of reach.
4. **Helper decode** — each of the six converted ``graph-scripts`` parsers is
   exercised for real (the guard cannot see a plumbing regression).
"""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path
from urllib.parse import quote

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Characters that must be percent-encoded in URI userinfo (RFC 3986
# sub-delims + '#'/'?'/'%' which would otherwise terminate or corrupt the
# authority). `+` is legal unencoded in userinfo, so it is not in the
# must-encode class — it is still exercised below via its escaped form
# (`%2B`), and raw `+` is pinned by test_plus_is_a_literal_plus_not_a_space.
HOSTILE_PASSWORDS = [
    "p@ss",
    "pa:ss",
    "pa/ss",
    "pa#ss",
    "pa?ss",
    "pa%ss",
    "pa+ss",
    "pa ss",
    "p@:/?#%+ ",  # every hostile character at once, ending in a space
]

HOSTILE_USERNAMES = ["user@corp", "u:ser", "u/1", "u+1", "u%1"]

# Test-prefixed graph path + explicit graph_name on every ``from_uri`` call.
# ``from_uri`` journals its graph name in a test session; the session-end
# sweep DROPs every journaled non-default graph, so a shared path
# (``/tortoise``) would let a test run delete the dev/compose graph
# (#7795). Explicit ``graph_name=`` also satisfies the cycle-5 P1-6 census
# in ``test_derived_names.py`` without a blanket route-table exemption.
TEST_GRAPH = "test_from_uri_userinfo"


def _uri(password: str, user: str = "user", host: str = "db.example.com") -> str:
    """A docker:// URI whose userinfo is fully percent-escaped.

    The path is TEST-PREFIXED: ``from_uri`` journals the resolved graph name
    in a test session, and the session-end sweep DROPs every journaled graph
    except the env-URI default — a shared path like ``/tortoise`` would put
    the dev/compose graph in that drop set (the #7795 data-loss class).
    """
    return (
        f"docker://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:6379/{TEST_GRAPH}"
    )


def _qualified(node) -> str | None:
    """Dotted name for a Name/Attribute chain (``parsed`` / ``self.parsed``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _qualified(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _target_names(target) -> set[str]:
    """Every binding one assignment target introduces.

    Covers plain names, attribute targets (``self.parsed``), unpacking
    (``p, q`` / ``[p]``) and starred targets (``a, *rest``). An attribute
    target is a realistic refactor shape that a Name-only walk would miss.
    """
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names |= _target_names(elt)
        return names
    qualified = _qualified(target)
    return {qualified} if qualified else set()


def _bindings_from_assign(value, targets) -> set[str]:
    """Bindings that receive a parse result from ``targets = value``.

    Positional when BOTH sides unpack (``p, q = urlparse(u), None``): only the
    target paired with the parse result is bound. Tainting every target
    whenever the tuple *contains* a parse call false-positives on an unrelated
    sibling object — ``creds, parsed = load_creds(), urlparse(u)`` would
    wrongly flag ``creds.username``.
    """
    if (
        len(targets) == 1
        and isinstance(targets[0], (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
    ):
        names: set[str] = set()
        for target, val in zip(targets[0].elts, value.elts, strict=False):
            # A starred target absorbs the remainder, so the parse result can
            # land in it wherever it sits.
            if isinstance(target, ast.Starred) or _is_parse_call_value(val):
                names |= _target_names(target)
        return names
    if _is_parse_call_value(value):
        names = set()
        for target in targets:
            names |= _target_names(target)
        return names
    return set()


def _capture_init(monkeypatch) -> dict:
    """Capture the kwargs ``from_uri`` forwards to ``FalkorProjection.__init__``."""
    captured: dict = {}
    from tortoise.projection import FalkorProjection

    monkeypatch.setattr(
        FalkorProjection, "__init__",
        lambda self, *args, **kwargs: captured.update(kwargs),
    )
    return captured


# ── 1. Round trip — helper ────────────────────────────────────────────────

@pytest.mark.parametrize("password", HOSTILE_PASSWORDS)
def test_helper_round_trips_hostile_passwords(password):
    from tortoise.config import parse_uri_userinfo

    user, decoded = parse_uri_userinfo(_uri(password))
    assert user == "user"
    assert decoded == password


@pytest.mark.parametrize("user", HOSTILE_USERNAMES)
def test_helper_round_trips_hostile_usernames(user):
    from tortoise.config import parse_uri_userinfo

    decoded_user, password = parse_uri_userinfo(_uri("pw", user=user))
    assert decoded_user == user
    assert password == "pw"


# ── 1b. Round trip — production consumers ────────────────────────────────

@pytest.mark.parametrize("password", HOSTILE_PASSWORDS)
def test_from_uri_forwards_decoded_password(password, monkeypatch):
    """The pre-#3039 code forwarded the raw ``%XX`` form here."""
    captured = _capture_init(monkeypatch)
    from tortoise.projection import FalkorProjection

    FalkorProjection.from_uri(_uri(password), graph_name=TEST_GRAPH)
    assert captured["password"] == password


@pytest.mark.parametrize("user", HOSTILE_USERNAMES)
def test_from_uri_forwards_decoded_username(user, monkeypatch):
    captured = _capture_init(monkeypatch)
    from tortoise.projection import FalkorProjection

    FalkorProjection.from_uri(_uri("pw", user=user), graph_name=TEST_GRAPH)
    assert captured["username"] == user


def test_acl_admin_client_decodes_userinfo(monkeypatch):
    """``acl_graph_users._admin_client`` feeds redis.Redis — same rule.

    ``redis.Redis(...)`` does NOT unquote (only ``redis.from_url`` does), so
    this path had the identical latent auth failure.
    """
    import redis

    from tortoise import acl_graph_users as acl

    captured: dict = {}
    monkeypatch.setenv("TORTOISE_DB_URI", _uri("p@ss", user="admin"))
    monkeypatch.setattr(
        redis, "Redis", lambda **kw: (captured.update(kw), object())[1]
    )
    assert acl._admin_client() is not None
    assert captured["username"] == "admin"
    assert captured["password"] == "p@ss"


# ── 2. Negative controls ─────────────────────────────────────────────────

def test_plus_is_a_literal_plus_not_a_space(monkeypatch):
    """``unquote_plus`` would yield ``a b`` — userinfo is not a query string."""
    from tortoise.config import parse_uri_userinfo

    # A raw '+' in the URI stays '+'.
    _, raw = parse_uri_userinfo(f"docker://user:a+b@h:6379/{TEST_GRAPH}")
    assert raw == "a+b"
    # The escaped form of a literal '+' also decodes to '+'.
    _, escaped = parse_uri_userinfo(_uri("a+b"))
    assert escaped == "a+b"


def test_redirect_seam_decodes_userinfo(monkeypatch):
    """The test-session redirect inside ``FalkorProjection.__init__`` is the
    other client-feeding consumer #3039 converted (it reads the env URI, not
    the argument). ``_capture_init`` stubs ``__init__``, so the ``from_uri``
    round-trip tests cannot reach it — this constructs for real and captures
    the redirect's ``FalkorDB(...)`` kwargs.
    """
    import falkordb

    calls: list[dict] = []

    class _FakeFalkorDB:
        def __init__(self, *args, **kwargs):
            calls.append({k: kwargs.get(k) for k in ("username", "password")})

        def select_graph(self, name):
            raise RuntimeError("unit test: no graph")

    monkeypatch.setattr(falkordb, "FalkorDB", _FakeFalkorDB)
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv(
        "TORTOISE_DB_URI", f"docker://:p%40ss@localhost:6379/{TEST_GRAPH}"
    )

    from tortoise.projection import FalkorProjection

    # The fake refuses every graph operation; only the construction kwargs matter.
    with contextlib.suppress(Exception):
        FalkorProjection("/tmp/seam-3039-userinfo.db")

    assert calls, "the redirect seam constructed no FalkorDB client"
    assert calls[0]["password"] == "p@ss", calls


@pytest.mark.parametrize(
    "raw_password",
    ["plain", "p4ssw0rd", "with.dot-dash_under", "tilde~pass", "abcXYZ123"],
)
def test_unencoded_clean_password_parses_unchanged(raw_password, monkeypatch):
    """An unencoded password with no hostile char must pass through verbatim.

    The negative control the issue asks for: the unencoded and (no-op)
    percent-escaped forms must AGREE, not merely both 'work'.
    """
    captured = _capture_init(monkeypatch)
    from tortoise.projection import FalkorProjection

    assert quote(raw_password, safe="") == raw_password, "fixture must be clean"
    raw_uri = f"docker://user:{raw_password}@h:6379/{TEST_GRAPH}"
    FalkorProjection.from_uri(raw_uri, graph_name=TEST_GRAPH)
    assert captured["password"] == raw_password
    # Same plaintext through the escaped-form builder.
    captured.clear()
    FalkorProjection.from_uri(_uri(raw_password), graph_name=TEST_GRAPH)
    assert captured["password"] == raw_password


@pytest.mark.parametrize("raw_password", ["pa%ss", "pa%zz", "pa%2", "pa%g0"])
def test_invalid_percent_escape_is_preserved(raw_password, monkeypatch):
    """``unquote`` never raises and never rewrites a non-hex ``%`` sequence.

    A password whose literal text contains a stray ``%`` must survive
    unchanged — over-decoding is as wrong as under-decoding.
    """
    captured = _capture_init(monkeypatch)
    from tortoise.projection import FalkorProjection

    FalkorProjection.from_uri(
        f"docker://user:{raw_password}@h:6379/{TEST_GRAPH}",
        graph_name=TEST_GRAPH,
    )
    assert captured["password"] == raw_password
    # Its properly escaped form round-trips to the same plaintext.
    captured.clear()
    FalkorProjection.from_uri(_uri(raw_password), graph_name=TEST_GRAPH)
    assert captured["password"] == raw_password


def test_empty_and_absent_userinfo_stay_none():
    from tortoise.config import parse_uri_userinfo

    # Anonymous user, real password (the canonical local docker form).
    assert parse_uri_userinfo(f"docker://:pw@h:6379/{TEST_GRAPH}") == (None, "pw")
    # No userinfo at all.
    assert parse_uri_userinfo(f"docker://h:6379/{TEST_GRAPH}") == (None, None)
    # Empty userinfo both sides.
    assert parse_uri_userinfo(f"docker://:@h:6379/{TEST_GRAPH}") == (None, None)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        # Whitespace-only credentials are still credentials — decode, do not
        # normalise to None (only truly absent/empty userinfo is None).
        ("docker://user:%20@h:6379/test_g", ("user", " ")),
        ("docker://%20:pw@h:6379/test_g", (" ", "pw")),
        ("docker://user: @h:6379/test_g", ("user", " ")),
        # No scheme → no userinfo; consumers reject the URI before this point
        # (from_uri validates the scheme, _admin_client the hostname).
        ("localhost:6379/test_g", (None, None)),
    ],
)
def test_boundary_userinfo_shapes(uri, expected):
    from tortoise.config import parse_uri_userinfo

    assert parse_uri_userinfo(uri) == expected


def test_double_encoded_percent_decodes_exactly_once():
    """Single decode, matching ``redis.from_url`` (``%2540`` → ``%40``)."""
    from tortoise.config import parse_uri_userinfo

    _, decoded = parse_uri_userinfo(f"docker://user:p%2540ss@h:6379/{TEST_GRAPH}")
    assert decoded == "p%40ss"


# ── 3. Source guard ──────────────────────────────────────────────────────

_PARSE_CALLS = {"urlparse", "urlsplit"}
_USERINFO_ATTRS = {"username", "password"}

# Directories whose modules feed parsed credentials to a DB/redis client.
# ``tortoise/`` is the runtime package; ``graph-scripts/`` contains the repo
# utility scripts this fix also converted (their ``_parse_uri`` helpers feed
# ``FalkorDB(..., password=...)``).
_GUARDED_DIRS = ("tortoise", "graph-scripts")

# Modules allowed to read raw userinfo:
#   * ``tortoise/config.py`` — it IS the single decoding rule (#3039).
#   * ``graph-scripts/connectivity_gate.py`` — ``redact_uri`` masks a
#     ``urlsplit`` result for display and never feeds a client.
_ALLOWED = {"tortoise/config.py", "graph-scripts/connectivity_gate.py"}

# ``tests/`` is excluded wholesale — tests legitimately probe raw ``urlparse``
# semantics (``test_sdk_props_coercion.py``) and the directory is an order of
# magnitude larger than the runtime roots. These two test-infra modules were
# converted by #3039, so they are scanned explicitly instead of being left
# with no protection at all.
_GUARDED_FILES = (
    "tests/_embedded.py",
    "tests/test_pre_migration_safety.py",
)


def _is_parse_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in _PARSE_CALLS
    if isinstance(func, ast.Attribute):  # urllib.parse.urlparse(...)
        return func.attr in _PARSE_CALLS
    return False


def _raw_userinfo_reads(path: Path, rel: str) -> list[str]:
    """Line-level findings for raw userinfo reads on a urlparse/urlsplit result."""
    import warnings

    with warnings.catch_warnings():
        # Some scanned scripts carry pre-existing invalid-escape literals
        # (SyntaxWarning only) — the guard cares about attribute reads, not
        # escape hygiene, and must not pollute the test output.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(), filename=str(path))
    # Names bound from a parse call anywhere in the module (Assign, AnnAssign,
    # walrus, for/with targets, attribute targets). Cross-function name reuse
    # can only over-flag, never under-flag — a found line is a prompt to look,
    # not a proof.
    parsed_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            parsed_names.update(_bindings_from_assign(node.value, node.targets))
            continue
        # Every other binding shape reduces to (expression, single target):
        # annotated local, walrus, `for` target, `with ... as` target.
        bindings: list[tuple] = []
        if isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            # `parsed: ParseResult = urlparse(uri)` / `(p := urlparse(uri))`.
            bindings.append((node.value, node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bindings.append((node.iter, node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            bindings.extend(
                (item.context_expr, item.optional_vars)
                for item in node.items
                if item.optional_vars is not None
            )
        for expr, target in bindings:
            if _is_parse_call_value(expr):
                parsed_names.update(_target_names(target))

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in _USERINFO_ATTRS:
            continue
        base = node.value
        if isinstance(base, ast.NamedExpr):
            # `(p := urlparse(uri)).password` — the walrus is the base.
            is_raw = _is_parse_call_value(base.value)
        elif isinstance(base, ast.Call) and _is_parse_call(base):
            is_raw = True
        else:
            key = _qualified(base)
            is_raw = key is not None and key in parsed_names
        if is_raw:
            hits.append(f"  {rel}:{node.lineno}: raw .{node.attr} read")
    return hits


def _is_parse_call_value(value) -> bool:
    """True when ``value`` is — or unpacking-contains — a parse call.

    ``p, q = urlparse(uri), None`` binds a parse result through a tuple
    value, so a bare ``isinstance(value, ast.Call)`` check would miss it.
    """
    if isinstance(value, ast.Call) and _is_parse_call(value):
        return True
    if isinstance(value, (ast.Tuple, ast.List)):
        return any(_is_parse_call_value(elt) for elt in value.elts)
    return False


def test_no_raw_urlparse_userinfo_read_in_guarded_dirs():
    """Guard: only ``tortoise.config.parse_uri_userinfo`` may read userinfo.

    Scans every module under ``_GUARDED_DIRS`` that can feed a parsed
    credential to a DB/redis client: the ``tortoise/`` runtime package and the
    ``graph-scripts/`` helpers converted by this fix. ``tests/`` is excluded —
    tests legitimately probe raw ``urlparse`` semantics
    (``test_sdk_props_coercion``). ``graph-scripts/connectivity_gate.py`` is
    allowlisted because ``redact_uri`` masks a ``urlsplit`` result for display
    and never feeds a client. The bug class guarded is a credential reaching a
    client undecoded.
    """
    violations: list[str] = []
    guarded = [
        path
        for dirname in _GUARDED_DIRS
        for path in sorted((REPO_ROOT / dirname).rglob("*.py"))
    ] + [REPO_ROOT / rel for rel in _GUARDED_FILES]
    for path in guarded:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        violations.extend(_raw_userinfo_reads(path, rel))

    assert not violations, (
        "raw urlparse userinfo read(s) found — route them through "
        "tortoise.config.parse_uri_userinfo (#3039):\n" + "\n".join(violations)
    )


def test_guard_detects_the_pre_fix_pattern(tmp_path):
    """Meta-test: the guard is not vacuous — it flags the historical shape."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from urllib.parse import urlparse\n"
        "parsed = urlparse(uri)\n"
        "password = parsed.password or None\n"
        "username = urlparse(uri).username\n"
    )
    hits = _raw_userinfo_reads(sample, "sample.py")
    assert any("password" in h for h in hits)
    assert any("username" in h for h in hits)

    # Annotated local (`parsed: ParseResult = urlparse(uri)`) is the same bug
    # shape and must not slip through the name-collection pass.
    annotated = tmp_path / "annotated.py"
    annotated.write_text(
        "from urllib.parse import urlparse\n"
        "parsed: object = urlparse(uri)\n"
        "password = parsed.password\n"
    )
    hits = _raw_userinfo_reads(annotated, "annotated.py")
    assert any("password" in h for h in hits)

    # The allowlisted display-only shape (urlsplit → mask) is NOT a client feed,
    # but the guard is name-based: prove it still flags a urlsplit read in a
    # non-allowlisted module (fail-closed, not fail-open).
    urlsplit_sample = tmp_path / "urlsplit_sample.py"
    urlsplit_sample.write_text(
        "from urllib.parse import urlsplit\n"
        "parts = urlsplit(uri)\n"
        "user = parts.username\n"
    )
    hits = _raw_userinfo_reads(urlsplit_sample, "urlsplit_sample.py")
    assert any("username" in h for h in hits)

    # Walrus binding (`if (p := urlparse(uri)):`) has its own collection
    # branch — pin it so the branch cannot be deleted without a red.
    walrus_sample = tmp_path / "walrus_sample.py"
    walrus_sample.write_text(
        "from urllib.parse import urlparse\n"
        "if (p := urlparse(uri)):\n"
        "    password = p.password\n"
    )
    hits = _raw_userinfo_reads(walrus_sample, "walrus_sample.py")
    assert any("password" in h for h in hits)

    # Unpacking target (`p, q = urlparse(uri), None`) must contribute its
    # Names too — a tuple target is a realistic refactor shape.
    tuple_sample = tmp_path / "tuple_sample.py"
    tuple_sample.write_text(
        "from urllib.parse import urlparse\n"
        "p, q = urlparse(uri), None\n"
        "password = p.password\n"
    )
    hits = _raw_userinfo_reads(tuple_sample, "tuple_sample.py")
    assert any("password" in h for h in hits)

    # Pairing is POSITIONAL: in `q, p = None, urlparse(uri)` only `p` holds the
    # parse result. A blanket "tuple value taints every target" rule would
    # false-positive on `q` — an unrelated sibling object.
    paired = tmp_path / "paired.py"
    paired.write_text(
        "from urllib.parse import urlparse\n"
        "q, p = None, urlparse(uri)\n"
        "q.password\n"
        "p.password\n"
    )
    hits = _raw_userinfo_reads(paired, "paired.py")
    assert [h for h in hits if ":3:" in h] == [], hits
    assert any(":4:" in h for h in hits)

    # Attribute target (`self.parsed = urlparse(uri)`) — a Name-only walk
    # misses the qualified binding, and this is the realistic refactor shape.
    attr_sample = tmp_path / "attr_sample.py"
    attr_sample.write_text(
        "from urllib.parse import urlparse\n"
        "self.parsed = urlparse(uri)\n"
        "password = self.parsed.password\n"
    )
    hits = _raw_userinfo_reads(attr_sample, "attr_sample.py")
    assert any("password" in h for h in hits)

    # for/with and starred targets are binding shapes too.
    for_sample = tmp_path / "for_sample.py"
    for_sample.write_text(
        "from urllib.parse import urlparse\n"
        "for p in (urlparse(uri),):\n"
        "    password = p.password\n"
        "with urlparse(uri) as w:\n"
        "    user = w.username\n"
    )
    hits = _raw_userinfo_reads(for_sample, "for_sample.py")
    assert any("password" in h for h in hits)
    assert any("username" in h for h in hits)

    starred = tmp_path / "starred.py"
    starred.write_text(
        "from urllib.parse import urlparse\n"
        "first, *rest = 1, urlparse(uri)\n"
        "password = rest.password\n"
    )
    hits = _raw_userinfo_reads(starred, "starred.py")
    assert any("password" in h for h in hits)

    # Inline walrus base: `(p := urlparse(uri)).password`.
    walrus_base = tmp_path / "walrus_base.py"
    walrus_base.write_text(
        "from urllib.parse import urlparse\n"
        "if (p := urlparse(uri)).password:\n"
        "    pass\n"
    )
    hits = _raw_userinfo_reads(walrus_base, "walrus_base.py")
    assert any("password" in h for h in hits)


# ── 4. graph-scripts helper decode ───────────────────────────────────────

_GRAPH_SCRIPT_HELPERS = (
    "audit_graph",
    "audit_graph_deep",
    "context_removal_audit",
    "parity_sample",
    "pre_migration_snapshot",
    "rdb_snapshot_restore",
)


@pytest.mark.parametrize("module_name", _GRAPH_SCRIPT_HELPERS)
def test_graph_script_helpers_decode_credentials(module_name):
    """The six graph-scripts URI parsers feed ``FalkorDB(..., password=...)``.

    They read ``parsed.password`` before #3039; the AST guard proves no raw
    read remains but cannot catch a plumbing regression (dropping the key,
    swapping fields, reading a different source), so each helper is exercised
    for real through its own module.
    """
    import importlib.util
    import sys

    scripts_dir = REPO_ROOT / "graph-scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            f"_gs_{module_name}", scripts_dir / f"{module_name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts_dir))

    parse = getattr(module, "_parse_uri", None) or module.parse_uri
    cfg = parse(f"docker://:p%40ss@localhost:6379/{TEST_GRAPH}")
    assert cfg["password"] == "p@ss", f"{module_name} did not decode userinfo"
    assert cfg["host"] == "localhost"
    assert cfg["graph"] == TEST_GRAPH
