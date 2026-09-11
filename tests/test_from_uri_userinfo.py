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
   ``graph-scripts/connectivity_gate.py`` is allowlisted).
"""

from __future__ import annotations

import ast
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


def _uri(password: str, user: str = "user", host: str = "db.example.com") -> str:
    """A docker:// URI whose userinfo is fully percent-escaped."""
    return (
        f"docker://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:6379/tortoise"
    )


def _assigned_names(targets) -> set[str]:
    """Every ``Name`` bound by an assignment target.

    Covers ``p = ...``, unpacking ``p, q = ...`` / ``[p] = ...`` and the
    nested forms. Missing an unpacking target would let a raw userinfo read
    slip past the source guard (see the guard meta-test).
    """
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.update(_assigned_names(target.elts))
    return names


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

    FalkorProjection.from_uri(_uri(password))
    assert captured["password"] == password


@pytest.mark.parametrize("user", HOSTILE_USERNAMES)
def test_from_uri_forwards_decoded_username(user, monkeypatch):
    captured = _capture_init(monkeypatch)
    from tortoise.projection import FalkorProjection

    FalkorProjection.from_uri(_uri("pw", user=user))
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
    _, raw = parse_uri_userinfo("docker://user:a+b@h:6379/g")
    assert raw == "a+b"
    # The escaped form of a literal '+' also decodes to '+'.
    _, escaped = parse_uri_userinfo(_uri("a+b"))
    assert escaped == "a+b"


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
    raw_uri = f"docker://user:{raw_password}@h:6379/g"
    FalkorProjection.from_uri(raw_uri)
    assert captured["password"] == raw_password
    # Same plaintext through the escaped-form builder.
    captured.clear()
    FalkorProjection.from_uri(_uri(raw_password))
    assert captured["password"] == raw_password


@pytest.mark.parametrize("raw_password", ["pa%ss", "pa%zz", "pa%2", "pa%g0"])
def test_invalid_percent_escape_is_preserved(raw_password, monkeypatch):
    """``unquote`` never raises and never rewrites a non-hex ``%`` sequence.

    A password whose literal text contains a stray ``%`` must survive
    unchanged — over-decoding is as wrong as under-decoding.
    """
    captured = _capture_init(monkeypatch)
    from tortoise.projection import FalkorProjection

    FalkorProjection.from_uri(f"docker://user:{raw_password}@h:6379/g")
    assert captured["password"] == raw_password
    # Its properly escaped form round-trips to the same plaintext.
    captured.clear()
    FalkorProjection.from_uri(_uri(raw_password))
    assert captured["password"] == raw_password


def test_empty_and_absent_userinfo_stay_none():
    from tortoise.config import parse_uri_userinfo

    # Anonymous user, real password (the canonical local docker form).
    assert parse_uri_userinfo("docker://:pw@h:6379/g") == (None, "pw")
    # No userinfo at all.
    assert parse_uri_userinfo("docker://h:6379/g") == (None, None)
    # Empty userinfo both sides.
    assert parse_uri_userinfo("docker://:@h:6379/g") == (None, None)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        # Whitespace-only credentials are still credentials — decode, do not
        # normalise to None (only truly absent/empty userinfo is None).
        ("docker://user:%20@h:6379/g", ("user", " ")),
        ("docker://%20:pw@h:6379/g", (" ", "pw")),
        ("docker://user: @h:6379/g", ("user", " ")),
        # No scheme → no userinfo; consumers reject the URI before this point
        # (from_uri validates the scheme, _admin_client the hostname).
        ("localhost:6379/g", (None, None)),
    ],
)
def test_boundary_userinfo_shapes(uri, expected):
    from tortoise.config import parse_uri_userinfo

    assert parse_uri_userinfo(uri) == expected


def test_double_encoded_percent_decodes_exactly_once():
    """Single decode, matching ``redis.from_url`` (``%2540`` → ``%40``)."""
    from tortoise.config import parse_uri_userinfo

    _, decoded = parse_uri_userinfo("docker://user:p%2540ss@h:6379/g")
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
    # walrus). Cross-function name reuse can only over-flag, never under-flag —
    # a found line is a prompt to look, not a proof.
    parsed_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_parse_call_value(node.value):
            parsed_names.update(_assigned_names(node.targets))
        elif (
            isinstance(node, ast.AnnAssign)
            and _is_parse_call_value(node.value)
            and isinstance(node.target, ast.Name)
        ):
            # `parsed: ParseResult = urlparse(uri)` — annotated local.
            parsed_names.add(node.target.id)
        elif (
            isinstance(node, ast.NamedExpr)
            and _is_parse_call_value(node.value)
            and isinstance(node.target, ast.Name)
        ):
            parsed_names.add(node.target.id)

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in _USERINFO_ATTRS:
            continue
        base = node.value
        is_raw = (isinstance(base, ast.Call) and _is_parse_call(base)) or (
            isinstance(base, ast.Name) and base.id in parsed_names
        )
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
    for dirname in _GUARDED_DIRS:
        for path in sorted((REPO_ROOT / dirname).rglob("*.py")):
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
