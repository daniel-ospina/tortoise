"""Boot-time regressions: credential leakage + function-scope import shadowing.

Two silent failures that shipped to hosted production and were found by reading
boot logs, not by any test:

  #2922 — `_lifespan` (tortoise/hosted_api.py) contained a function-local
          `import os`. A function-local import binds the name for the WHOLE
          function scope, so the earlier `os.environ.get(...)` read raised
          UnboundLocalError, aborting the entire watcher-start block on every
          hosted boot for ~31 days. The failure was swallowed into a
          `_logger.warning`. Nothing alarmed: `watcher.running = false` with a
          last poll of 2026-08-11, the sweep had not run since 2026-08-09, and
          `last_drill` was null — i.e. the monitor that would have reported the
          holdup was itself dead (#2790). The linter HAD flagged it (`F823`) and
          it had been silenced with `# noqa: F823`.

  #2923 — `entrypoint.sh` echoed the resolved `TORTOISE_DB_URI` on every boot.
          For FalkorDB Cloud that string carries the database password, so the
          credential was written to the platform log stream of every deploy.

These tests are static/AST/subprocess-level on purpose: no app import (which
needs a hosted environment), no database, and no network. They run in the normal
lane; nothing here needs the FalkorDB fixtures.

Also asserted: the redaction rule matches the canonical implementation in
`tortoise/__main__.py::_mask_uri_userinfo` (covered by
`tests/test_doctor.py::test_mask_uri_userinfo_*`). Two divergent maskers for the
same control is how the weak one survives review.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"
TORTOISE_PKG = REPO_ROOT / "tortoise"


# ─────────────────────────────────────────────────────────────────────────────
# #2923 — the boot log must never print a connection string
# ─────────────────────────────────────────────────────────────────────────────


def _extract_shell_function(name: str, source: str) -> str:
    """Lift a shell function out of entrypoint.sh by brace matching.

    Sourcing the whole entrypoint would execute it. A regex would truncate at the
    first line-starting `}` and silently exercise a partial function, so match
    braces and then verify the result is valid shell.
    """
    match = re.search(rf"^{re.escape(name)}\(\) \{{", source, re.M)
    assert match, f"entrypoint.sh no longer defines {name}()"

    depth = 0
    end: int | None = None
    for idx in range(match.start(), len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    assert end is not None, f"{name}() is unbalanced in entrypoint.sh"

    body = source[match.start() : end]
    check = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
    assert check.returncode == 0, f"extracted {name}() is not valid shell: {check.stderr}"
    return body


def _redactor_body() -> str:
    return _extract_shell_function("_redact_uri", ENTRYPOINT.read_text())


def _redact(uri: str) -> str:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n" + _redactor_body() + '\n_redact_uri "$1"',
            "bash",
            uri,
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"redactor failed: {proc.stderr}"
    return proc.stdout


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        # the ordinary shape
        (
            "rediss://tortoise:hunter2@r-example.host.cloud:50317",
            "rediss://:***@r-example.host.cloud:50317",
        ),
        # password contains '/' — RFC-invalid but accepted by redis-py/urlparse.
        # The boundary is the LAST '@', so a first-delimiter split leaks the tail.
        (
            "rediss://user:S3n/tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # password contains a literal '@'
        (
            "rediss://user:S3n@tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # both, plus a second '@'
        (
            "rediss://user:p/s@s1@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # already percent-encoded
        (
            "rediss://user:p%40ss%3Aword@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # user-only userinfo (no password at all)
        ("docker://user@host:6379/db", "docker://:***@host:6379/db"),
        # empty userinfo
        ("docker://:@127.0.0.1:59997/tenant-alpha", "docker://:***@127.0.0.1:59997/tenant-alpha"),
        # query/fragment survive verbatim, and an '@' after '?' must not swallow the host
        (
            "redis://:pw@db.example.com:6379/0?ssl=true",
            "redis://:***@db.example.com:6379/0?ssl=true",
        ),
        ("docker://user:p@ss@host:7687/g#frag", "docker://:***@host:7687/g#frag"),
        # Same class as '/': a delimiter inside the password must not end the mask.
        # The canonical Python helper leaks these (pre-existing there) because its
        # authority region stops at '?'/'#'; the entrypoint's single-bare-URI rule
        # masks to the last '@' anywhere, so it fails safe and masks MORE.
        (
            "rediss://user:S3n?tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        (
            "rediss://user:S3n#tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # Stray leading whitespace is a real shape (a copy-pasted secret) and must
        # not defeat the mask; the whitespace itself is preserved.
        (
            "  rediss://user:hunter2@host.cloud:1234",
            "  rediss://:***@host.cloud:1234",
        ),
        # A value with NO scheme cannot be masked reliably, so it fails closed
        # rather than being echoed: the app rejects a malformed URI, but the
        # password would already be in the log by then.
        ("user:S3n/tinel@host.cloud:1234", "<uri-redacted-unrecognised-shape>"),
        (":pw@host:6379/graph", "<uri-redacted-unrecognised-shape>"),
        # no userinfo → unchanged, so a target without credentials stays readable
        ("rediss://r-example.host.cloud:50317", "rediss://r-example.host.cloud:50317"),
        # embedded/dev target
        (
            "docker://:falkordb@localhost:6379/tortoise_test_matrix",
            "docker://:***@localhost:6379/tortoise_test_matrix",
        ),
    ],
)
def test_redactor_masks_userinfo_and_keeps_the_target(uri: str, expected: str):
    """Exact-output pin.

    Asserting only `password not in output` is satisfied by a *partial* leak: a
    naive `[^@/]*@` rule turns `user:S3n/tinel@host` into `:***@tinel@host`, which
    still hides most of the password while logging its tail.
    """
    assert _redact(uri) == expected


# The canonical Python implementation of the same control. Two maskers for one
# control is how the weaker one survives review — this pins them together.
_BARE_URI_CORPUS = [
    "rediss://tortoise:hunter2@r-example.host.cloud:50317",
    "rediss://user:S3n/tinel@host.cloud:1234",
    "rediss://user:S3n@tinel@host.cloud:1234",
    "rediss://user:p%40ss%3Aword@host.cloud:1234",
    "docker://user@host:6379/db",
    "docker://:@127.0.0.1:59997/tenant-alpha",
    "redis://:pw@db.example.com:6379/0?ssl=true",
    "docker://user:p@ss@host:7687/g#frag",
    "rediss://r-example.host.cloud:50317",
    "docker://:falkordb@localhost:6379/tortoise_test_matrix",
]


def test_shell_redactor_agrees_with_the_canonical_python_masker():
    """Parity with `tortoise/__main__.py::_mask_uri_userinfo`.

    The entrypoint and the Python error paths mask the same thing, so they must
    produce the same string. Without this, "mirrors the canonical implementation"
    is an unbacked claim: the first version of this helper emitted
    `scheme://***@host` while the canonical emits `scheme://:***@host` — it read
    as equivalent and was not.

    Scope: parity holds over the corpus below, which enumerates the bare-URI
    shapes the entrypoint can receive. Two deliberate, documented asymmetries:
    the canonical helper also masks every `scheme://` occurrence inside a longer
    message, and it stops its authority region at `?`/`#` (so it leaks a password
    containing those, a pre-existing gap this shell rule does not share).
    """
    from tortoise.__main__ import _mask_uri_userinfo

    for uri in _BARE_URI_CORPUS:
        assert _redact(uri) == _mask_uri_userinfo(uri), f"maskers disagree on {uri!r}"


def test_redactor_tolerates_an_empty_argument():
    """`set -u` is on in the entrypoint: an absent URI must not abort boot."""
    assert _redact("") == ""


def _extract_boot_db_block(source: str) -> str:
    """The real `if [ -n "$TORTOISE_DB_URI" ] … fi` block from entrypoint.sh."""
    lines = source.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith('if [ -n "${TORTOISE_DB_URI:-}" ]; then')
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize("secret", ["S3ntinelPw", "S3n/tinel", "S3n@tinel", "p/s@s1"])
@pytest.mark.parametrize("branch", ["explicit", "cloud"])
def test_boot_db_block_never_emits_the_password(secret: str, branch: str):
    """Execute the shipped boot block — black-box, not a line grep.

    This is the check that actually holds: it runs the real `echo` lines with a
    sentinel password and asserts the sentinel never reaches stdout. A
    line-scanning guard is bypassed by `printf`, an unbraced `$VAR`, an
    intermediate variable, or a line that prints the raw value *alongside* a
    redacted one.
    """
    source = ENTRYPOINT.read_text()
    script = "set -euo pipefail\n" + _redactor_body() + "\n" + _extract_boot_db_block(source)
    host = "r-example.host.cloud:50317"
    uri = f"rediss://Tortoise2:{secret}@{host}"
    var = "FALKORDB_CLOUD_URI" if branch == "cloud" else "TORTOISE_DB_URI"
    env = {"PATH": os.environ.get("PATH", ""), var: uri}  # deliberately excludes the other var

    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, f"boot block failed: {proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert secret not in combined, f"password reached the boot log: {combined!r}"
    assert host in combined, "the target host should stay diagnosable"


def test_entrypoint_prints_no_uri_through_a_raw_interpolation():
    """Secondary line-level guard, for URI prints outside the DB-target block.

    Known limitations (stated so the gate is not over-trusted): this is a static
    heuristic. It sees `echo`/`printf`/`logger` on a single line, so an unbraced
    `$VAR`, an intermediate variable, a heredoc, or a line-continuation can evade
    it. `test_boot_db_block_never_emits_the_password` is the black-box check that
    covers those for the block that actually prints the URI — this one is the
    cheap tripwire for anything added elsewhere.
    """
    uri_vars = re.compile(r"\$\{?(TORTOISE_DB_URI|FALKORDB_CLOUD_URI)")
    printers = re.compile(r"\b(echo|printf|logger)\b")
    offenders: list[str] = []

    for lineno, line in enumerate(ENTRYPOINT.read_text().splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue  # a comment cannot print
        if printers.search(line) and uri_vars.search(line) and "_redact_uri" not in line:
            offenders.append(f"entrypoint.sh:{lineno}: {line.strip()}")

    assert not offenders, (
        "connection strings carry the database password and must be printed "
        "through _redact_uri (#2923):\n" + "\n".join(offenders)
    )


# ─────────────────────────────────────────────────────────────────────────────
# #2922 — no function may locally import a name it already reads
# ─────────────────────────────────────────────────────────────────────────────
#
# Static analysis rather than importing tortoise.hosted_api: the module needs a
# hosted environment (TORTOISE_API_KEY, pepper, …) and the interesting failure is
# a scope-binding bug, which is visible in the AST.

NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _scope_nodes(container: ast.AST):
    """Nodes in a scope, without descending into nested scopes."""
    stack = list(container.body)  # type: ignore[attr-defined]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, NESTED_SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _imports_in(nodes, names: set[str] | None = None) -> list[tuple[int, str]]:
    """(lineno, bound name) for import statements among `nodes`.

    Covers `import x`, `import x as y`, `from p import x` and `from p import x as
    y`. What matters is the *bound name*: a local `from os import environ` shadows
    `environ` exactly as hard as a local `import environ` would. The comparison is
    against names bound at module scope either way — it does not shadow `os`.
    """
    found: list[tuple[int, str]] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            found.extend(
                (node.lineno, alias.asname or alias.name.split(".")[0]) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            found.extend((node.lineno, alias.asname or alias.name) for alias in node.names)
    relevant = [pair for pair in found if names is None or pair[1] in names]
    return relevant


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names bound at module scope.

    Walks the module scope rather than only `tree.body` direct children: a
    module-level `import os` inside `try:`/`if TYPE_CHECKING:` binds the same name
    (e.g. tortoise/session_continuity.py), and missing it would produce a false
    all-clear for the whole module.
    """
    return {name for _, name in _imports_in(_scope_nodes(tree))}


def _shadowing_offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    module_names = _module_level_names(tree)
    offenders: list[str] = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Earliest function-local import line per shadowed name. A function may
        # (redundantly) import the same module in several branches — harmless in
        # itself, because each read still follows an import on its own path.
        local_import_line: dict[str, int] = {}
        for lineno, name in _imports_in(_scope_nodes(fn), names=module_names):
            local_import_line[name] = min(local_import_line.get(name, lineno), lineno)
        if not local_import_line:
            continue

        first_read: dict[str, int] = {}
        for node in _scope_nodes(fn):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in local_import_line
            ):
                first_read[node.id] = min(first_read.get(node.id, node.lineno), node.lineno)

        # The genuine bug: the name is READ before ANY local import of it, so the
        # read hits the unbound local that the later import declares.
        #
        # Known limitations (line order is not control-flow order, and this is
        # deliberately not a linter reimplementation; ruff's F823 shares most of
        # these): a local import on a non-executing path (`if flag: import os`)
        # followed by a later unconditional read is missed; so is a read inside a
        # class body, lambda, or a scope outside tortoise/. A read in a decorator,
        # default argument, or annotation is attributed to the enclosing scope
        # rather than the function, so it is missed too. In the other direction, a
        # `global`/`nonlocal` declaration for the same name makes such a read
        # legal, and is not exempted here — a false positive, never a missed crash.
        # The value of this scan is covering the whole package uniformly and never
        # being silenceable by an inline suppression.
        for name, read_lineno in sorted(first_read.items()):
            if read_lineno < local_import_line[name]:
                offenders.append(
                    f"{path.name}:{local_import_line[name]}: {fn.name}() reads {name!r} at "
                    f"line {read_lineno}, before any function-local import of it — "
                    f"UnboundLocalError at runtime"
                )
    return offenders


def test_no_function_locally_imports_a_module_level_name_it_already_reads():
    """The #2922 bug class, scanned across the package.

    This is the guard that failed to exist: the bug was lint-flagged, silenced
    with `# noqa`, and then invisible for ~31 days.
    """
    offenders: list[str] = []
    for path in sorted(TORTOISE_PKG.rglob("*.py")):
        offenders.extend(_shadowing_offenders(path))

    assert not offenders, (
        "a function-local import shadows a module-level import read earlier in the "
        "same function — UnboundLocalError at runtime, and if the caller swallows "
        "exceptions it fails silently (#2922):\n" + "\n".join(offenders)
    )


def _lifespan_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse((TORTOISE_PKG / "hosted_api.py").read_text(), filename="hosted_api.py")
    found = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_lifespan"
        ),
        None,
    )
    assert found is not None, (
        "tortoise/hosted_api.py has no `_lifespan` function — if it was renamed, "
        "relocate this #2922 pin with it"
    )
    return found


def test_lifespan_body_has_no_redundant_local_module_imports():
    """Direct pin for #2922: the exact statement that killed the watcher.

    `os`, `asyncio` and `threading` are all imported at module scope in
    hosted_api.py, so a local import of any of them is redundant *and* is the
    footgun — it makes the name local for the whole function, so any earlier read
    becomes an UnboundLocalError. A correctly-placed conditional local import is
    not merely redundant here; it is a latent crash.
    """
    local = {name for _, name in _imports_in(_scope_nodes(_lifespan_fn()))}
    shadowing = sorted(local & {"os", "asyncio", "threading", "logging"})
    assert not shadowing, (
        f"hosted_api._lifespan re-imports {shadowing} locally; these are module-level "
        "imports and the local rebinding shadows them for the whole function (#2922)"
    )


def _boot_log_calls(fragment: str) -> list[ast.Call]:
    calls = []
    for node in ast.walk(_lifespan_fn()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and fragment in node.args[0].value
        ):
            calls.append(node)
    return calls


def test_watcher_start_failure_is_logged_at_error_with_traceback():
    """The loudness half of #2922.

    Reverting this handler to `_logger.warning(...)` (or dropping `exc_info`)
    previously passed every test — the fix could be undone by one keystroke and
    the dead-watcher blindness would return unremarked.
    """
    calls = _boot_log_calls("backup watcher could not start")
    assert len(calls) == 1, f"expected exactly one watcher-start failure log, found {len(calls)}"
    call = calls[0]

    assert isinstance(call.func, ast.Attribute) and call.func.attr == "error", (
        "_lifespan logs the watcher-start failure at "
        f"{getattr(call.func, 'attr', '?')!r}; it must be ERROR (a warning is what "
        "hid this for ~31 days)"
    )
    assert any(kw.arg == "exc_info" for kw in call.keywords), (
        "the watcher-start failure must carry exc_info — without a traceback a "
        "configuration error is indistinguishable from a transient one"
    )


def test_every_silent_watcher_non_start_path_states_its_reason():
    """#2922 review: not starting must never be silent for *any* reason.

    The UnboundLocalError was one silent path. The two remaining early-outs —
    `cfg is None` (fail-closed default: sweep off or config invalid) and
    `BACKUP_WATCHER_DISABLED=1` (test-only kill switch, unenforced in prod) —
    also left the watcher unset with no log line at all.
    """
    calls = _boot_log_calls("backup watcher not started")
    assert calls, "not starting the watcher must be stated on boot"
    assert all(isinstance(c.func, ast.Attribute) for c in calls), (
        "the non-start reasons must go through _logger so the log level is explicit"
    )
    assert any(c.func.attr == "warning" for c in calls), (
        "where a monitor is expected the non-start reason must be a warning, not merely debug"
    )

    constants = {
        node.value
        for node in ast.walk(_lifespan_fn())
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for fragment in ("config unavailable", "kill switch"):
        assert any(fragment in c for c in constants), (
            f"no reason text mentions {fragment!r} — an operator cannot tell WHY the "
            "watcher did not start"
        )


def test_watcher_non_start_reason_is_gated_on_being_hosted():
    """The level must depend on whether a monitor is expected here.

    Without this pin, reverting the gate (warning on every boot) or inverting it
    (warning only where it is NOT expected) both pass: the reason text and the
    existence of a warning call are asserted, but not *when* it fires. That
    matters because a warning on every healthy TestClient/embedded boot is how the
    real signal becomes noise.
    """
    lifespan = _lifespan_fn()

    # The marker is the hosted indicator, computed with the same truthiness test
    # the durability guard uses.
    assert any(
        isinstance(node, ast.Name) and node.id == "_watcher_expected" for node in ast.walk(lifespan)
    ), "no `_watcher_expected` marker is computed in _lifespan"
    assert any(
        isinstance(node, ast.Constant) and node.value == "FLY_APP_NAME"
        for node in ast.walk(lifespan)
    ), "`_watcher_expected` must derive from FLY_APP_NAME (the hosted marker)"

    gated = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.If)
        and any(
            isinstance(inner, ast.Name) and inner.id == "_watcher_expected"
            for inner in ast.walk(node.test)
        )
    ]
    assert gated, "the non-start reason is not gated on `_watcher_expected`"

    def _log_levels_in(statements) -> set[str]:
        levels: set[str] = set()
        for stmt in statements:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    levels.add(node.func.attr)
        return levels

    branch = gated[0]
    # Polarity, not just branch presence: an inverted gate
    # (`if not _watcher_expected:`) would take the debug path in production —
    # exactly the blindness #2922 removed — while still containing both levels.
    assert isinstance(branch.test, ast.Name) and branch.test.id == "_watcher_expected", (
        "the gate must be the positive `if _watcher_expected:` — an inverted or "
        "negated test would silence production"
    )
    assert "warning" in _log_levels_in(branch.body), (
        "the branch taken when a monitor IS expected must warn"
    )
    assert "debug" in _log_levels_in(branch.orelse), (
        "the branch taken when no monitor is expected must not warn"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The suppression channels must stay closed
# ─────────────────────────────────────────────────────────────────────────────
#
# The bug was not missed by tooling — it was silenced. Guard the silencing, not
# just the code, or the next instance is suppressed the same way.

F823_SUPPRESSION = re.compile(r"#\s*noqa[^\n]*\bF823\b", re.IGNORECASE)


def test_no_inline_f823_suppressions_remain():
    """F823 means "local variable referenced before assignment" — the exact crash."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
        for path in sorted(TORTOISE_PKG.rglob("*.py"))
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if F823_SUPPRESSION.search(line)
    ]
    assert not offenders, (
        "F823 is a real crash, not style. Move the import instead of suppressing:\n"
        + "\n".join(offenders)
    )


def test_ruff_config_does_not_ignore_f823():
    """An inline `# noqa` is not the only silencing channel.

    `pyproject.toml` can ignore the rule globally or per-file, which would
    re-blind every tool that depends on F823 — including the lint tripwire the
    original `# noqa` was hiding behind.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    lint = data.get("tool", {}).get("ruff", {}).get("lint", {})

    assert "F823" not in lint.get("ignore", []), "pyproject.toml globally ignores F823"
    for pattern, rules in lint.get("per-file-ignores", {}).items():
        assert "F823" not in rules, f"pyproject.toml per-file-ignores[{pattern!r}] ignores F823"
