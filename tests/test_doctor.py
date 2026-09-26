"""Tests for `tortoise doctor` (#703 follow-up: --path/--db + shared resolution).

Covers the four review findings:
  - P1 bug: _cmd_doctor read args.path unguarded → AttributeError when called
    from _cmd_onboard with a bare Namespace(cmd="doctor").
  - P1 test-coverage: no tests exercised _cmd_doctor (onboard mocked it).
  - P2: --db now routes URI schemes through from_uri and plain file paths
    through the embedded constructor (help text matches behavior).
  - P2: no-flag doctor follows the shared CLI resolution — env URI >
    FALKORDB_* > TORTOISE_DB_PATH > canonical embedded default (same as
    init/index; #720 conf 70 — no local docker://localhost default).

Runnable with: uv run python -m pytest tests/test_doctor.py -v
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

_DB_ENV_VARS = (
    "TORTOISE_DB_URI",
    "TORTOISE_DB_PATH",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "FALKORDB_PASSWORD",
)


@pytest.fixture
def clear_db_env(monkeypatch):
    """No DB-related env vars — deterministic resolution tests."""
    for k in _DB_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _run_doctor(argv: list[str]) -> int:
    """Invoke `tortoise doctor <argv>` (main returns int)."""
    from tortoise.__main__ import main
    return main(["doctor", *argv])


def _pi_seam_name() -> str:
    """Pi's installed artifact basename, DERIVED from the contract registry so
    a rename cannot leave a test writing a file nothing reads."""
    from tortoise.hook_install import ARTIFACT_CONTRACTS
    return ARTIFACT_CONTRACTS["pi"].install_name


def _session_verify_accepts_pi_harness() -> bool:
    """The pi-aware read-only query is one that ACCEPTS `--harness pi`.

    Doctor's collision hint names a replacement command, and the obvious
    `tortoise hooks status` is layout-keyed — it exits 1 with "unknown harness
    'pi'", so naming it would swap one refusal for another.  This asserts the
    command actually named accepts the harness.  Its exit code may still be
    non-zero for a missing config, which is not a refusal of the REQUEST.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "session", "verify",
         "--harness", "pi"],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    return "unknown harness" not in (proc.stdout + proc.stderr)


def _seed_db(db_path: str, content: str, attempts: int = 3) -> None:
    """Boot an embedded DB at db_path and write one point.

    Bounded-retried (#720 review: redislite can transiently fail to start on
    a crowded shared TMPDIR — unix-socket ENOENT); the seed forces the
    projection up so the DB file exists before doctor probes it.
    """
    import time
    for _i in range(attempts):
        try:
            sdk = TortoiseSDK(db_path=db_path)
            sdk.create_point(kind="observation", content=content)
            sdk.close()
            return
        except Exception:
            if _i == attempts - 1:
                raise
            time.sleep(1)


def _health_line(out: str) -> str:
    """Extract the 'Graph: health' results line."""
    return next(line for line in out.splitlines() if "Graph: health" in line)


class TestDoctorPath:
    def test_doctor_path_resolves_and_runs(self, clear_db_env, tmp_path, capsys):
        """--path <file> resolves to that embedded DB and runs."""
        db_path = os.path.join(str(tmp_path), "doctor.db")
        sdk = TortoiseSDK(db_path=db_path)
        sdk.create_point(kind="decision", content="Doctor smoke check")
        sdk.close()

        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        assert rc in (0, 1)  # docker section may warn without a live FalkorDB
        line = _health_line(out)
        assert "✅" in line and "1 Points" in line

    def test_doctor_db_uri_routes_through_from_uri(self, clear_db_env, capsys):
        """--db docker:// URI is parsed by from_uri (dead port proves it).

        The graph path is TEST-PREFIXED: doctor Step 3 goes through
        ``from_uri``, which journals its resolved graph name in a test
        session, and the session-end sweep drops every journaled graph
        except the env-URI default — a shared path (``/tortoise``) would
        let this test delete the dev/compose graph (#7795).
        """
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:59999/test_doctor"])
        out = capsys.readouterr().out

        assert rc == 1
        line = _health_line(out)
        assert "❌" in line
        assert "127.0.0.1:59999" in line  # from_uri parsed the URI host/port

    def test_doctor_db_plain_path_uses_embedded(self, clear_db_env, tmp_path, capsys):
        """--db accepts plain file paths → embedded constructor (help match)."""
        db_path = os.path.join(str(tmp_path), "via_db_flag.db")
        # Seed an initialized DB — doctor must not create one as a side effect
        # of a diagnostic (#2204); the health row then proves --db resolved
        # to THIS embedded graph, not a URI connection error.
        _seed_db(db_path, "doctor --db flag seed")
        rc = _run_doctor(["--db", db_path])
        out = capsys.readouterr().out

        assert rc in (0, 1)
        line = _health_line(out)
        assert "Points" in line  # embedded DB reached via --db flag
        assert "❌" not in line  # health at the seeded target must pass

    def test_doctor_bad_relative_path_clean_error(self, clear_db_env, capsys):
        """Relative --path → clean error, no traceback."""
        rc = _run_doctor(["--path", "relative.db"])
        out = capsys.readouterr().out

        assert rc == 1
        line = _health_line(out)
        assert "❌" in line
        assert "Relative DB path 'relative.db' rejected" in line
        assert "Traceback" not in out

    def test_doctor_db_uri_probe_uses_resolved_target(self, clear_db_env, capsys):
        """#720 conf 78: the Step 2 Docker probe must probe the RESOLVED
        --db target's host/port — never a hardcoded localhost:16379. Both
        the probe line and the health line must report the same target."""
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:59998/test_doctor"])
        out = capsys.readouterr().out

        assert rc == 1
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "127.0.0.1:59998" in probe  # probe targeted the --db host/port
        assert "localhost:16379" not in probe  # not the old hardcoded default
        assert "127.0.0.1:59998" in _health_line(out)  # same target as health

    def test_doctor_db_uri_non_numeric_port_clean_error(self, clear_db_env, capsys):
        """#720 P2 conf 75: a non-numeric port in --db/TORTOISE_DB_URI must
        surface as a clean ❌ check + rc 1 — never an uncaught ValueError
        traceback (parsed.port now lives inside the guarded try)."""
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:notaport/test_doctor"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "Traceback" not in out
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "❌" in probe
        assert "bad port" in probe  # actionable message, not a raw ValueError

    def test_doctor_db_uri_password_never_in_error_output(self, clear_db_env, capsys):
        """#720 P2 conf 78: a malformed URI carrying a password must not
        print the credential — the 'bad port' error redacts the userinfo
        (docker://:***@) while keeping host/port for debuggability."""
        rc = _run_doctor(["--db", "docker://:sekritpass@127.0.0.1:notaport/test_doctor"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "sekritpass" not in out  # credential never reaches stdout
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "bad port" in probe
        assert "docker://:***@127.0.0.1:notaport" in probe  # masked, target intact

    def test_doctor_db_uri_password_with_at_sign_never_leaks(self, clear_db_env, capsys):
        """#720 conf 65: a password containing a raw @ must not leak —
        urlparse splits userinfo at the LAST @, so the mask must consume
        everything up to the host separator (docker://:p@ss@host must not
        print the ':ss@' tail)."""
        rc = _run_doctor(["--db", "docker://:p@ss@127.0.0.1:notaport/test_doctor"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "p@ss" not in out  # full credential (incl. @) never reaches stdout
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "bad port" in probe
        assert "docker://:***@127.0.0.1:notaport" in probe  # masked, target intact

    def test_doctor_malformed_ipv6_uri_clean_error(self, clear_db_env, capsys):
        """#720 P2 conf 95: a malformed authority (dangling '[' → urlparse
        raises ValueError: Invalid IPv6 URL) must surface as a clean ❌ +
        rc 1 — never an uncaught traceback. urlparse + hostname extraction
        live INSIDE the guarded try; the error line masks userinfo so a
        credential in --db never reaches the terminal."""
        rc = _run_doctor(["--db", "docker://:pw@[abc"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "Traceback" not in out  # never a raw ValueError traceback
        assert "pw" not in out  # credential never reaches stdout
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "❌" in probe
        assert "bad URI" in probe  # actionable message, not a raw ValueError
        assert "docker://:***@[abc" in probe  # masked, target intact

    def test_doctor_unsupported_scheme_uri_masks_credentials(self, clear_db_env, capsys):
        """#720 P2 conf 95: an unsupported-scheme URI (bolt://, mongodb://,
        …) is not a DB URI, so it falls through is_db_uri → resolve_db_path →
        RELATIVE_PATH_ERROR, which embeds the RAW URI. The resolution-error
        line must mask the userinfo — the password must never reach stdout."""
        rc = _run_doctor(["--db", "bolt://user:sup3rsekrit@host:7687/g"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "sup3rsekrit" not in out  # credential never reaches stdout
        assert "Traceback" not in out
        line = _health_line(out)
        assert "❌" in line
        assert "Relative DB path" in line  # still the actionable message
        assert "bolt://:***@host:7687/g" in line  # masked, target intact

    def test_mask_uri_userinfo_masks_at_containing_password(self):
        """Unit-level: mask consumes up to the LAST @ (host boundary),
        never the first — @ inside a password stays hidden."""
        from tortoise.__main__ import _mask_uri_userinfo

        assert _mask_uri_userinfo("docker://:p@ss@127.0.0.1:7687/tortoise") == \
            "docker://:***@127.0.0.1:7687/tortoise"
        assert _mask_uri_userinfo("docker://user:p@ss@host:7687/g") == \
            "docker://:***@host:7687/g"
        # no userinfo → unchanged (plain target stays visible for debuggability)
        assert _mask_uri_userinfo("docker://127.0.0.1:7687/tortoise") == \
            "docker://127.0.0.1:7687/tortoise"

    def test_mask_uri_userinfo_slash_in_password(self):
        """#720 P2 conf 68: '/' INSIDE userinfo is RFC-invalid but accepted
        by urlparse/redis-py — the mask must still consume the full
        credential up to the LAST @ (the host boundary), never split at
        the first '/' (which would leak the password tail)."""
        from tortoise.__main__ import _mask_uri_userinfo

        assert _mask_uri_userinfo("docker://user:p/ss@host:notaport/g") == \
            "docker://:***@host:notaport/g"
        # slash + multiple @ combined: everything up to the host is hidden
        assert _mask_uri_userinfo("docker://user:p/ss@h1@host:7687/g") == \
            "docker://:***@host:7687/g"
        # user-only userinfo (no colon) is masked too
        assert _mask_uri_userinfo("docker://user@host:6379/db") == \
            "docker://:***@host:6379/db"
        # empty userinfo (docker://:@host) is still a userinfo
        assert _mask_uri_userinfo("docker://:@127.0.0.1:59997/tenant-alpha") == \
            "docker://:***@127.0.0.1:59997/tenant-alpha"

    def test_mask_uri_userinfo_all_schemes_and_delimiters(self):
        """#720 P2 conf 68: the mask applies to every scheme:// pattern
        (docker/redis/rediss/bolt/etc) and never touches a query/fragment
        delimiter that genuinely starts one — an '@' in a query value does
        not swallow the host UNLESS that would risk masking less (see
        #2983 fail-closed cases below)."""
        from tortoise.__main__ import _mask_uri_userinfo

        assert _mask_uri_userinfo("bolt://user:sup3rsekrit@host:7687/g") == \
            "bolt://:***@host:7687/g"
        assert _mask_uri_userinfo("redis://:hunter2@db.example.com:6379/tortoise") == \
            "redis://:***@db.example.com:6379/tortoise"
        assert _mask_uri_userinfo("rediss://:pw@db.example.com:6380/0") == \
            "rediss://:***@db.example.com:6380/0"
        # query/fragment survive the mask verbatim
        assert _mask_uri_userinfo("redis://:pw@db.example.com:6379/0?ssl=true") == \
            "redis://:***@db.example.com:6379/0?ssl=true"
        assert _mask_uri_userinfo("docker://user:p@ss@host:7687/g#frag") == \
            "docker://:***@host:7687/g#frag"

    def test_mask_uri_userinfo_plain_paths_and_malformed_unchanged(self):
        """#720 P2 conf 68: plain paths (no scheme://) pass through
        unchanged; a malformed authority (urlsplit raises, e.g. unmatched
        '[') must never propagate out of an error handler — mask best-effort."""
        from tortoise.__main__ import _mask_uri_userinfo

        assert _mask_uri_userinfo("tortoise.db") == "tortoise.db"
        assert _mask_uri_userinfo("/abs/path/tortoise.db") == "/abs/path/tortoise.db"
        assert _mask_uri_userinfo("~/.tortoise/tortoise.db") == "~/.tortoise/tortoise.db"
        assert _mask_uri_userinfo("C:\\foo\\tortoise.db") == "C:\\foo\\tortoise.db"
        # urlsplit raises on unmatched '[' — the mask still hides the
        # credential instead of leaking it (and never raises in a handler)
        assert _mask_uri_userinfo("docker://user:pw@[abc") == "docker://:***@[abc"

    def test_mask_uri_userinfo_delimiter_inside_password_fails_closed(self):
        """#2983: a literal '?'/'#' inside a password (RFC-invalid — it
        should be %3F/%23 — but copy-pasteable) must not truncate the
        authority region before the '@' and re-emit the credential.
        When an '@' follows the earliest '?'/'#', that delimiter is itself
        inside the userinfo, so the mask consumes to the LAST '@' of the
        full authority, mirroring entrypoint.sh::_redact_uri."""
        from tortoise.__main__ import _mask_uri_userinfo

        # Both repro shapes from the issue re-emitted the password verbatim.
        assert _mask_uri_userinfo("rediss://user:S3n?tinel@host.cloud:1234") == \
            "rediss://:***@host.cloud:1234"
        assert _mask_uri_userinfo("rediss://user:S3n#tinel@host.cloud:1234") == \
            "rediss://:***@host.cloud:1234"
        # An '@' BEFORE the delimiter is password material too: the old code
        # stopped at the '?' and leaked the '?word@host' tail.
        assert _mask_uri_userinfo("rediss://user:p@ss?word@host:1234") == \
            "rediss://:***@host:1234"
        assert _mask_uri_userinfo("rediss://user:p#ss#word@host:1234") == \
            "rediss://:***@host:1234"
        # both delimiters, plus an extra '@' inside the credential
        assert _mask_uri_userinfo("rediss://user:p?ss#w@rd@host:1234/g") == \
            "rediss://:***@host:1234/g"
        # trailing delimiter at the end of the password
        assert _mask_uri_userinfo("rediss://user:S3n?@host:1234") == \
            "rediss://:***@host:1234"
        # '://' inside the password must not register as a second URI: the
        # old next-scheme boundary truncated before the '@' and leaked the
        # credential prefix (found by the #2983 verifier).
        assert _mask_uri_userinfo("rediss://user:p://w@host:1234") == \
            "rediss://:***@host:1234"
        assert _mask_uri_userinfo("rediss://user:S3n://tinel@host.cloud:1234/db") == \
            "rediss://:***@host.cloud:1234/db"
        assert _mask_uri_userinfo("rediss://user:S3n?tinel://w@host.cloud:1234/db") == \
            "rediss://:***@host.cloud:1234/db"
        # '@' AND '://' inside the password together: the '@' must not make
        # the inner '://' look like a second URI whose scheme is the leaked
        # password tail.
        assert _mask_uri_userinfo("rediss://user:S3n@tinel://w@host.cloud:1234/db") == \
            "rediss://:***@host.cloud:1234/db"
        assert _mask_uri_userinfo("rediss://user:S3n?x@tinel://@host.cloud:1234/db") == \
            "rediss://:***@host.cloud:1234/db"
        # embedded in prose — RELATIVE_PATH_ERROR carries the raw URI
        prose = ("Relative DB path 'rediss://user:S3n?tinel@host:1234/g' "
                 "rejected. Use (1) the canonical path")
        assert _mask_uri_userinfo(prose) == \
            ("Relative DB path 'rediss://:***@host:1234/g' rejected. "
             "Use (1) the canonical path")
        # A delimiter with NO '@' after it still starts a real
        # query/fragment and stays byte-identical (well-formed path).
        assert _mask_uri_userinfo("redis://:pw@db.example.com:6379/0?ssl=true") == \
            "redis://:***@db.example.com:6379/0?ssl=true"
        # Fail-closed over-reach, documented deliberately: an '@' in a
        # GENUINE query is syntactically indistinguishable from a '?' in a
        # password, so the mask consumes to the last '@' (masks more,
        # never leaks; diagnosability loss only).
        assert _mask_uri_userinfo("redis://:pw@host:6379/0?u=a@b") == \
            "redis://:***@b"
        # Same reason, a second URI whose predecessor has no userinfo is
        # merged into one masked line rather than risking a split that
        # leaves half a credential visible.
        assert _mask_uri_userinfo(
            "rediss://host1:1/db and rediss://u:p@host2:2/db") == \
            "rediss://:***@host2:2/db"

    def test_mask_uri_userinfo_fuzz_never_emits_password_material(self):
        """#2983: exhaustive fuzz over passwords containing '?'/'#'/'@'/'/'.

        The marker pair 'S3n'/'tinel' is asserted separately from the whole
        password so the check stays honest for degenerate one-character
        passwords (a lone '@' is unavoidably present as the mask separator).
        """
        from tortoise.__main__ import _mask_uri_userinfo

        specials = ["?", "#", "@", "/", ":", "=", "&", "%40", "%3F", "%23",
                    "://", "://w", "a://"]
        passwords: list[str] = []
        for a in specials:
            passwords.append(f"S3n{a}tinel")
            passwords.append(f"S3n{a}{a}tinel")
            for b in specials:
                passwords.append(f"S3n{a}tinel{b}")
                passwords.append(f"S3n{a}{b}tinel")
        passwords += [
            "?S3ntinel", "#S3ntinel", "@S3ntinel", "/S3ntinel",
            "S3ntinel?", "S3ntinel#", "S3ntinel@", "S3ntinel/",
            "??S3n", "##S3n", "@@S3n", "//S3n", "S3n", "S3n?", "S3n#",
        ]
        for pw in passwords:
            for uri in (
                f"rediss://user:{pw}@host.cloud:1234/db",
                f"docker://:{pw}@127.0.0.1:7687/tortoise",
                f"bolt://user:{pw}@[::1]:7687/g",
            ):
                masked = _mask_uri_userinfo(uri)
                assert "S3n" not in masked, f"marker leaked: {uri!r} -> {masked!r}"
                assert "tinel" not in masked, f"marker leaked: {uri!r} -> {masked!r}"
                assert pw not in masked, f"password leaked: {uri!r} -> {masked!r}"

    def test_doctor_db_uri_probe_uses_uri_graph_name(self, clear_db_env, monkeypatch, capsys):
        """#720 P2 conf 62: the Step 2 probe must select the graph from the
        URI path — the same derivation from_uri uses in Step 3 — never a
        hardcoded "tortoise". A non-default graph name in the URI must be
        probed, so a remote server gets no stray "tortoise" graph created."""
        import falkordb as _falkordb

        selected: list[str] = []

        class _FakeGraph:
            def query(self, q):
                return None

        class _FakeFalkorDB:
            def __init__(self, *a, **k):
                pass

            def select_graph(self, name):
                selected.append(name)
                return _FakeGraph()

        monkeypatch.setattr(_falkordb, "FalkorDB", _FakeFalkorDB)
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:59997/test_doctor_tenant"])
        out = capsys.readouterr().out

        assert rc == 1  # health check still fails against the dead port
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "test_doctor_tenant" in probe  # probe reports the URI path's graph
        # Every select_graph — probe AND Step 3's from_uri projection — used
        # the URI path's graph; none created a stray "tortoise" graph.
        assert set(selected) == {"test_doctor_tenant"}
        assert "tortoise" not in selected

    def test_doctor_db_uri_probe_uses_decoded_credentials(
            self, clear_db_env, monkeypatch, capsys):
        """#3039: the Step 2 probe must percent-DECODE URI userinfo — urlparse
        does not, so a raw read forwards a literal %XX and the probe reports a
        false auth failure. Pin the (username, password) it hands FalkorDB."""
        import falkordb as _falkordb

        calls: list[dict] = []

        class _FakeGraph:
            def query(self, q):
                return None

        class _FakeFalkorDB:
            def __init__(self, *a, **k):
                calls.append(
                    {key: k.get(key) for key in ("username", "password")}
                )

            def select_graph(self, name):
                return _FakeGraph()

        monkeypatch.setattr(_falkordb, "FalkorDB", _FakeFalkorDB)
        # ad%6Din -> admin ; p%40ss -> p@ss
        rc = _run_doctor([
            "--db", "docker://ad%6Din:p%40ss@127.0.0.1:59997/test_doctor_tenant"])
        capsys.readouterr()

        assert rc == 1  # dead port — both probe and Step 3 still construct
        # Step 2 (the probe under test) is followed by Step 3's from_uri
        # construction, so a single mutable dict would be overwritten by the
        # later, already-decoded call. Assert on EVERY construction:
        # reverting the probe to raw `parsed.username` must red this test.
        assert calls, "doctor constructed no FalkorDB client"
        assert all(
            c == {"username": "admin", "password": "p@ss"} for c in calls
        ), calls

    def test_doctor_embedded_target_skips_docker_probe(self, clear_db_env, tmp_path, capsys):
        """#720 conf 78: embedded target → probe reports embedded mode
        instead of attempting a fake localhost:16379 connection."""
        db_path = os.path.join(str(tmp_path), "embedded_probe.db")
        rc = _run_doctor(["--db", db_path])
        out = capsys.readouterr().out

        assert rc in (0, 1)
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "embedded mode" in probe
        assert "localhost:16379" not in probe


class TestDoctorDefaultResolution:
    def test_no_flags_defaults_to_embedded(self, monkeypatch, clear_db_env, tmp_path, capsys):
        """No flags, no env → shared default resolution (canonical embedded
        path), like init/index — NOT a local docker://localhost default
        (#720 conf 70). Graph health must report the embedded graph, never
        a docker connection failure. Hermetic (#2204): the canonical default
        (~/.tortoise/tortoise.db) is redirected to a seeded tmp DB — the
        test must never depend on the runner's real ~/.tortoise existing."""
        from tortoise import config as _config
        canonical = os.path.join(str(tmp_path), ".tortoise", "tortoise.db")
        monkeypatch.setattr(_config, "DEFAULT_DB_PATH", canonical)
        # Seed an initialized embedded DB at the canonical default (fix A
        # creates the data dir on open; the write forces the projection up).
        _seed_db(canonical, "doctor no-flags seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc in (0, 1)  # docker section may warn without a live FalkorDB
        line = _health_line(out)
        assert "Points" in line
        assert "❌" not in line  # embedded resolution must not fail

    def test_no_flags_uses_env_uri(self, monkeypatch, clear_db_env, capsys):
        """TORTOISE_DB_URI env wins over embedded defaults."""
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:59999/test_doctor")
        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc == 1
        line = _health_line(out)
        assert "❌" in line and "127.0.0.1:59999" in line

    def test_no_flags_uses_falkordb_env(self, monkeypatch, clear_db_env, capsys):
        """FALKORDB_* env → Docker URI (FALKORDB_* > TORTOISE_DB_PATH)."""
        monkeypatch.setenv("FALKORDB_HOST", "127.0.0.1")
        monkeypatch.setenv("FALKORDB_PORT", "59999")
        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc == 1
        line = _health_line(out)
        assert "❌" in line and "127.0.0.1:59999" in line

    def test_no_flags_uses_tortoise_db_path(self, monkeypatch, clear_db_env, tmp_path, capsys):
        """TORTOISE_DB_PATH env → embedded at that path."""
        db_path = os.path.join(str(tmp_path), "env.db")
        # Seed an initialized DB at the env target — doctor must not CREATE a
        # DB as a side effect of a diagnostic (#2204); the health row then
        # proves the env var won resolution by reporting THIS graph.
        _seed_db(db_path, "doctor env-path seed")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc in (0, 1)
        line = _health_line(out)
        assert "Points" in line
        assert "❌" not in line  # embedded health at the env target must pass
        assert db_path in out  # the probe row names the resolved env target


class TestDoctorPreInit:
    """#2204: doctor on an UNINITIALIZED environment must print a clean,
    readable first-run status instead of starting the embedded redis server,
    which would emit a raw "*** FATAL CONFIG FILE ERROR" (redislite writes
    `dir <missing-dir>` into its config) plus a redis-server subprocess
    traceback. The probe is SKIPPED — doctor never creates state or spawns a
    server on a machine the user has not set up.

    Verdict split (review #2204): a missing DEFAULT target is the expected
    fresh-machine first-run state → ⚠️ + rc 0 ("doctor passes pre-init"); a
    missing EXPLICITLY CONFIGURED target (--db/--path/TORTOISE_DB_PATH
    pointing somewhere else) keeps a loud ❌ + rc 1 naming the path — a
    typo'd target must read as a config error, not as a healthy first run.
    """

    def test_embedded_path_missing_dir_reports_not_set_up(self, clear_db_env, tmp_path, capsys):
        """--path into a NONEXISTENT directory tree → readable 'not set up
        yet' line naming the configured target + rc 1 (config error), no
        FATAL CONFIG noise, no traceback, and NO directory created (probe
        skipped — doctor has no side effects)."""
        db_path = os.path.join(str(tmp_path), "no-such-dir", "graph", "tortoise.db")

        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        assert rc == 1
        assert "FATAL CONFIG" not in out
        assert "Traceback" not in out
        assert "redis-server" not in out
        line = _health_line(out)
        assert "❌" in line  # configured-but-missing target is a config error
        assert "not set up yet" in line
        assert "tortoise init" in line
        assert db_path in line  # names the misconfigured target
        # probe skipped → the missing dir tree was NOT created
        assert not os.path.exists(os.path.dirname(db_path))

    def test_a_legacy_collision_does_not_recommend_a_refusing_command(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """The hint's own invariant: never name a command that REFUSES.

        `install_capture` refuses when the legacy extension is already disabled
        at `.tortoise-capture.disabled` (it will not overwrite the previous
        backup).  The detector cannot report that collision — its legacy blind
        spot is #3713 — so in that state the only finding is `stale-artifact`,
        no manual-fix kind is present, and the hint used to print
        `tortoise install pi`: a command that then refuses.

        Mutation: drop the `_legacy_collision` arm in `_cmd_doctor` — this REDs
        on the assertion that the refusing command is absent.
        """
        from tortoise import capture_install as _ci
        from tortoise import config as _config
        monkeypatch.setattr(
            _config, "DEFAULT_DB_PATH",
            os.path.join(str(tmp_path), ".tortoise", "tortoise.db"))
        monkeypatch.setenv("HOME", str(tmp_path))

        res = _ci.install_capture("pi", home=tmp_path)
        assert res.ok, res.error
        root = _ci.pi_home(tmp_path)
        installed = root / _pi_seam_name()
        installed.write_text("// tortoise-hook-version: 0\n// body\n",
                             encoding="utf-8")
        (root / _ci.LEGACY_PI_DIRNAME).mkdir()      # the legacy directory
        (root / _ci.PI_DISABLED_DIRNAME).mkdir()    # its backup name, taken

        _run_doctor([])
        out = capsys.readouterr().out

        assert "Capture hooks" in out, out
        assert "tortoise install pi" not in out, (
            "the hint recommends a command that refuses in this state")
        assert _ci.PI_DISABLED_DIRNAME in out, out
        # A replacement command must itself accept `--harness pi`: the obvious
        # `tortoise hooks status` is layout-keyed and exits 1 there.
        assert "hooks status --harness pi" not in out, out
        assert _session_verify_accepts_pi_harness()

    def test_a_symlinked_legacy_entry_is_not_treated_as_a_collision(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """The guard must mirror the installer's own refusal condition.

        `_install_pi` unlinking a SYMLINKED legacy entry never reaches its
        refusal — only a real legacy DIRECTORY whose backup name is taken does.
        A guard keyed on `.exists()` fires on the symlink case too, so doctor
        withholds the command that actually works.

        Mutation: change `.is_dir() and not .is_symlink()` back to `.exists()` —
        this REDs, because the working `tortoise install pi` disappears.
        """
        from tortoise import capture_install as _ci
        from tortoise import config as _config
        monkeypatch.setattr(
            _config, "DEFAULT_DB_PATH",
            os.path.join(str(tmp_path), ".tortoise", "tortoise.db"))
        monkeypatch.setenv("HOME", str(tmp_path))

        assert _ci.install_capture("pi", home=tmp_path).ok
        root = _ci.pi_home(tmp_path)
        (root / _pi_seam_name()).write_text("// tortoise-hook-version: 0\n",
                                            encoding="utf-8")
        target = tmp_path / "checkout"
        target.mkdir()
        (root / _ci.LEGACY_PI_DIRNAME).symlink_to(
            target, target_is_directory=True)
        (root / _ci.PI_DISABLED_DIRNAME).mkdir()

        _run_doctor([])
        out = capsys.readouterr().out

        assert "Capture hooks" in out, out
        assert "move one aside" not in out, (
            "a symlinked legacy entry is repaired by the installer, so the "
            "working command must still be offered")

    def test_a_memory_error_is_not_reported_as_an_unavailable_check(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """Resource exhaustion must not be laundered into "check unavailable".

        `MemoryError` is an `Exception`, so the per-harness
        `except MemoryError: raise` is re-caught by the handler around the
        whole block — a simulated exhaustion used to print
        `check unavailable: simulated exhaustion`, abort the harness loop, and
        leave rc at 0, contradicting the comment that says exhaustion is not a
        refusal.

        Mutation: remove the outer `except MemoryError: raise` — this REDs,
        because doctor then prints the laundered warning instead of raising.
        """
        from tortoise import capture_install as _ci
        from tortoise import config as _config
        from tortoise import hook_install as _hi
        monkeypatch.setattr(
            _config, "DEFAULT_DB_PATH",
            os.path.join(str(tmp_path), ".tortoise", "tortoise.db"))
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _ci.install_capture("pi", home=tmp_path).ok

        def _boom(*_a, **_k):
            raise MemoryError("simulated exhaustion")

        monkeypatch.setattr(_hi, "detect_artifact_install", _boom)
        with pytest.raises(MemoryError):
            _run_doctor([])

    def test_no_flags_fresh_machine_reports_not_set_up(self, monkeypatch, clear_db_env, tmp_path, capsys):
        """The canonical first-run scenario: no flags, no env, no ~/.tortoise
        → doctor reports 'not set up yet — run tortoise init' (rc 0) instead
        of the raw embedded-redis FATAL CONFIG error.

        HOME is isolated because "fresh machine" includes the HOME-scoped
        capture seams: since #4680 doctor grades the installed Pi extension's
        GENERATION, so an ambient stale seam under the developer's real HOME
        would legitimately FAIL this rc-0 assertion.
        """
        from tortoise import config as _config
        canonical = os.path.join(str(tmp_path), ".tortoise", "tortoise.db")
        monkeypatch.setattr(_config, "DEFAULT_DB_PATH", canonical)
        monkeypatch.setenv("HOME", str(tmp_path))

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc == 0
        assert "FATAL CONFIG" not in out
        assert "Traceback" not in out
        line = _health_line(out)
        assert "⚠️" in line
        assert "not set up yet" in line
        assert canonical in line  # names the missing default so the hint is actionable
        assert "❌" not in line
        assert not (tmp_path / ".tortoise").exists()  # no dir created by the probe


class TestDoctorImportHygiene:
    """#2204 O/I/T (3) regression: importing tortoise modules on a clean env
    must NOT emit dev-mode pepper / API-key warnings or fastmcp "Component
    already exists" / "no handler — skipped" noise. Runs in a SUBPROCESS
    (fresh interpreter) so module caching from earlier tests cannot mask a
    regression, with the pepper/key env scrubbed so the dev fallback path is
    exercised.
    """

    _SCRUB_ENV = (
        *_DB_ENV_VARS,
        "TORTOISE_SECRET_PEPPER", "TORTOISE_API_KEY", "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
        "TORTOISE_TEST_MODE", "TORTOISE_FAST_ATEXIT", "RATE_LIMIT_DISABLED",
        "TORTOISE_TEST_SESSION", "TORTOISE_SESSION_LLM_MOCK",
    )

    def _run_py(self, code: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        for k in self._SCRUB_ENV:
            env.pop(k, None)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = root
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=180, env=env, cwd=root,
        )

    def test_import_tortoise_auth_emits_no_warning(self):
        """Importing tortoise.auth (pepper + key unset) is silent — the
        dev-mode pepper warning moved from import time to first use."""
        p = self._run_py("import tortoise.auth")
        assert p.returncode == 0, p.stderr
        assert p.stderr == "", p.stderr
        assert "dev-mode pepper" not in p.stderr

    def test_auth_dev_pepper_warns_once_at_first_use(self):
        """The dev-pepper signal still fires — once per process, on first
        hashing — never per call."""
        p = self._run_py(
            "import tortoise.auth as a; from tortoise.auth import hash_api_key; "
            "hash_api_key('k1'); hash_api_key('k2'); hash_api_key('k3')"
        )
        assert p.returncode == 0, p.stderr
        assert p.stderr.count("dev-mode pepper") == 1, p.stderr

    def test_import_mcp_server_emits_no_fastmcp_noise(self):
        """Importing tortoise.mcp_server emits no "Component already exists"
        (duplicate registration) and no "registry entries have no handler"
        lines — the pre-#2204 import noise."""
        p = self._run_py("import tortoise.mcp_server")
        assert p.returncode == 0, p.stderr
        assert "Component already exists" not in p.stderr
        assert "no handler" not in p.stderr

    def test_session_capture_registered_on_mcp_instance(self):
        """tortoise_session_capture — a registry tool whose handler existed
        but was never registered while register_all ran mid-module — must now
        actually reach the MCP server (#2204; the #993 entrypoint regression
        only asserts >=70 tools + the onboarding set, so it would not catch a
        silent absence)."""
        p = self._run_py(
            "import tortoise.mcp_server as m; "
            "comps = m.mcp._local_provider._components; "
            "names = [getattr(c, 'name', '') for c in comps.values()]; "
            "assert 'tortoise_session_capture' in names, names; "
            "print('OK')"
        )
        assert p.returncode == 0, p.stderr
        assert "OK" in p.stdout


class TestDoctorSessionExtraction:
    """#1197: doctor surfaces the /v1/sessions LLM-provider state (#822).

    Captures are STORED but the LLM extraction is skipped when no provider key
    is configured (#3892) — extraction is the beta testers' most-critical
    feature. Doctor must report the provider/model when configured, and FAIL
    in hosted mode (FLY_APP_NAME) when the key is missing or the test seam is
    left on, so ops catch it before testers do.
    """

    _LLM_ENV = (
        "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
        "GEMINI_API_KEY", "TORTOISE_SESSION_LLM_MOCK",
        "TORTOISE_SESSION_LLM_MODEL", "FLY_APP_NAME",
    )

    @pytest.fixture
    def clean_llm_env(self, monkeypatch, clear_db_env, tmp_path):
        for k in self._LLM_ENV:
            monkeypatch.delenv(k, raising=False)
        db_path = os.path.join(str(tmp_path), "doctor_llm.db")
        return monkeypatch, db_path

    def _extraction_line(self, out: str) -> str:
        return next(line for line in out.splitlines() if "Session extraction" in line)

    def test_no_provider_local_warns(self, clean_llm_env, capsys):
        """No key + not hosted → ⚠️ warning (captures are stored, extraction
        skipped; rc not driven by this check). Embedded DB so the only
        possible ❌ is mine."""
        monkeypatch, db_path = clean_llm_env  # noqa: RUF059
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "⚠️" in line
        assert "STORED" in line and "no LLM provider key" in line
        assert rc in (0, 1)

    def test_provider_key_reports_provider(self, clean_llm_env, capsys):
        """A configured provider key → ✅ with the resolved provider + model."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-1197")
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MODEL", "openrouter:deepseek/deepseek-chat")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "✅" in line
        assert "openrouter" in line
        assert "deepseek/deepseek-chat" in line
        assert rc in (0, 1)

    def test_mock_seam_local_reports_test_mode(self, clean_llm_env, capsys):
        """TORTOISE_SESSION_LLM_MOCK=1 locally → ⚠️ test seam (offline)."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "⚠️" in line
        assert "test" in line.lower()
        assert rc in (0, 1)

    def test_hosted_no_provider_fails(self, clean_llm_env, capsys):
        """Hosted mode (FLY_APP_NAME) + no provider key → ❌ + rc 1 — the
        flagship extraction feature cannot work; ops must not ship this. The
        copy is truthful: captures are STORED, extraction is skipped."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("FLY_APP_NAME", "tortoise-api")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "❌" in line
        assert "STORED" in line and "skipped" in line
        assert rc == 1

    def test_hosted_mock_seam_fails(self, clean_llm_env, capsys):
        """Hosted + TORTOISE_SESSION_LLM_MOCK=1 → ❌ (captures would write
        offline MockModel points; the seam must never ship to prod)."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("FLY_APP_NAME", "tortoise-api")
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "❌" in line
        assert "REMOVE" in line
        assert rc == 1


    def test_provider_model_mismatch_not_ok(self, clean_llm_env, capsys):
        """Key set + TORTOISE_SESSION_LLM_MODEL naming a DIFFERENT provider
        → never ✅: sdk._build_session_llm_extractor raises ValueError
        (capture would 500) — the doctor must surface the misconfig
        (❌ hosted / ⚠️ local) instead of reporting healthy."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-1197")
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MODEL", "deepseek:deepseek-chat")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "✅" not in line
        assert "misconfig" in line
        assert rc in (0, 1)

    def test_openrouter_bare_model_shape_warns(self, clean_llm_env, capsys):
        """PR #1220 review P2 c65: an openrouter spec WITHOUT <family>/<model>
        (openrouter:deepseek-chat) builds an extractor fine (✅ Session
        extraction) but the route 404s at capture time — the doctor must add
        a ⚠️ shape warning, never fail the run (config itself is valid)."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-1197")
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MODEL", "openrouter:deepseek-chat")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "✅" in line  # config is valid — extractor builds
        assert "deepseek-chat" in line
        shape = next(l for l in out.splitlines() if "OpenRouter model" in l)  # noqa: E741
        assert "⚠️" in shape
        assert "<family>/<model>" in shape
        assert "404" in shape  # actionable: the failure mode is a route 404
        assert rc in (0, 1)

    def test_openrouter_family_model_no_warning(self, clean_llm_env, capsys):
        """A well-formed openrouter spec (openrouter:deepseek/deepseek-chat)
        → no shape warning at all."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-1197")
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MODEL", "openrouter:deepseek/deepseek-chat")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "✅" in line
        assert "deepseek/deepseek-chat" in line
        assert not any("OpenRouter model" in l for l in out.splitlines())  # noqa: E741
        assert rc in (0, 1)

    def test_non_openrouter_provider_never_shape_warns(self, clean_llm_env, capsys):
        """Shape warning is openrouter-ONLY — deepseek/openai/gemini models
        are bare ids (deepseek-chat, gpt-4o-mini, gemini-2.0-flash) and must
        never trip it."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-1197")
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MODEL", "deepseek:deepseek-chat")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "✅" in line
        assert "deepseek-chat" in line
        assert not any("OpenRouter model" in l for l in out.splitlines())  # noqa: E741
        assert rc in (0, 1)

    def test_openrouter_default_model_no_warning(self, clean_llm_env, capsys):
        """Unset TORTOISE_SESSION_LLM_MODEL with openrouter key → default
        deepseek/deepseek-chat (well-formed) → no shape warning."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-1197")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "✅" in line
        assert "deepseek/deepseek-chat" in line
        assert not any("OpenRouter model" in l for l in out.splitlines())  # noqa: E741
        assert rc in (0, 1)


class TestOnboardDoctorCall:
    def test_bare_namespace_no_attribute_error(self, monkeypatch, clear_db_env, tmp_path, capsys):
        """#703 follow-up: _cmd_onboard calls _cmd_doctor(Namespace(cmd='doctor'))
        — both args.db AND args.path must be read via getattr."""
        monkeypatch.setenv("TORTOISE_DB_PATH", os.path.join(str(tmp_path), "onboard.db"))
        # Seed an initialized DB (doctor must not create one as a side effect
        # of a diagnostic — #2204); the health row must then report THIS graph.
        _seed_db(os.path.join(str(tmp_path), "onboard.db"), "doctor onboard seed")

        from tortoise.__main__ import _cmd_doctor
        rc = _cmd_doctor(argparse.Namespace(cmd="doctor"))
        out = capsys.readouterr().out

        assert rc in (0, 1)
        assert "'Namespace' object has no attribute" not in out
        line = _health_line(out)
        assert "Points" in line  # embedded check actually ran (not a misleading ❌)

    def test_full_onboard_reaches_doctor_without_crash(self, monkeypatch, clear_db_env, tmp_path, capsys):
        """End-to-end onboard → doctor Step 5 completes (init falls back to
        embedded via TORTOISE_DB_PATH; doctor reuses the same resolution).
        Runs from a non-git dir so Step 3 skips repo indexing."""
        monkeypatch.setenv("TORTOISE_DB_PATH", os.path.join(str(tmp_path), "onboard_full.db"))
        monkeypatch.chdir(tmp_path)

        from tortoise.__main__ import _cmd_onboard
        rc = _cmd_onboard(argparse.Namespace(cmd="onboard", path=None))
        out = capsys.readouterr().out

        assert rc == 0
        assert "Step 5/5: Health check" in out
        assert "'Namespace' object has no attribute" not in out


class TestDoctorPiSeamFreshness:
    """#4680: `doctor` must grade the Pi seam's GENERATION, not its presence.

    Pi is the one wizard-offered harness with no `HarnessLayout`, so step 6's
    "Pi (extension found)" used to be the only Pi row — a stale or
    markerless seam exited 0 with no freshness row at all (the review-P1
    finding). Step 7 now drives both seam classes through
    `contract_version_for` / `detect_artifact_install`.

    `--path relative.db` pins an invalid DB target so the graph checks fail
    fast and no embedded server is started; steps 6/7 still run.
    """

    @staticmethod
    def _seam(home, text: str):
        root = home / ".pi" / "agent" / "extensions"
        root.mkdir(parents=True, exist_ok=True)
        path = root / _pi_seam_name()
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _pi_row(out: str) -> str:
        return next(line for line in out.splitlines() if "Capture hooks (pi)" in line)

    def test_doctor_fails_a_stale_pi_seam(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """A markerless (pre-contract) Pi seam is a FAIL row naming the repair.

        Mutation: drop "pi" from step 7's loop — the green "Pi (extension
        found)" row stands alone and this REDs (no ❌ row, rc 0 for this seam).
        """
        home = tmp_path / "home"
        from tortoise import hook_install
        shipped = hook_install.ARTIFACT_CONTRACTS["pi"].source.read_text(
            encoding="utf-8")
        # The body is the SHIPPED seam with its marker stripped, so it is ours
        # by signature but declares no generation — the pre-contract shape.
        self._seam(home, "\n".join(
            line for line in shipped.splitlines()
            if not line.startswith("// tortoise-hook-version:")) + "\n")
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "❌" in row, row
        assert "unversioned-artifact" in row, row
        assert "tortoise install pi" in row, row

    def test_doctor_passes_a_current_pi_seam(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """The shipped bytes installed verbatim are "current" — doctor must not
        nag a healthy Pi install (the failure mode that would train users to
        ignore the row)."""
        from tortoise import hook_install
        home = tmp_path / "home"
        self._seam(home, hook_install.ARTIFACT_CONTRACTS["pi"].source
                   .read_text(encoding="utf-8"))
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "✅" in row, row
        assert "install current" in row, row

    def test_doctor_recommends_the_installer_for_a_repairable_pi_seam(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """A stale-but-repairable Pi seam names `tortoise install pi`, the
        command that actually fixes it (the counterpart to the foreign case
        below — without this the hint could be silent for everything)."""
        home = tmp_path / "home"
        self._seam(home, "// tortoise-hook-version: 0\n// body\n")
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "❌" in row, row
        assert "run `tortoise install pi` to repair" in row, row
        assert "needs a manual fix" not in row, row

    def test_doctor_never_recommends_a_pi_repair_that_would_refuse(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """#4680 review: `tortoise install pi` REFUSES a foreign artifact (it
        will not clobber a file it cannot claim), so doctor must print the
        finding's manual instruction instead of the hint that says to run it
        unconditionally.  The detail is allowed to name the command as the
        step AFTER moving the file aside — that is the installer's own
        prescribed path.

        Mutation: drop the `is_manual_fix` gate (always append "run `tortoise
        install <harness>` to repair") — this REDs on a foreign artifact.
        """
        home = tmp_path / "home"
        self._seam(home, "// some other product extension\nexport default 1;\n")
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "❌" in row, row
        assert "foreign-artifact" in row, row
        assert "needs a manual fix" in row, row
        assert "run `tortoise install pi` to repair" not in row, (
            "recommending a command that refuses is worse than no hint")

    def test_doctor_never_recommends_the_installer_for_a_symlinked_root(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """A symlinked install ROOT makes `tortoise install pi` refuse (it will
        not write through a symlink), so doctor must not recommend it.

        Mutation: compute the manual set over BLOCKING findings only — the root
        note is non-blocking, so the hint flips to "run `tortoise install pi`"
        and this REDs.
        """
        home = tmp_path / "home"
        real = home / "checkout-extensions"
        real.mkdir(parents=True)
        (home / ".pi" / "agent").mkdir(parents=True)
        root = home / ".pi" / "agent" / "extensions"
        root.symlink_to(real)
        (real / "tortoise-capture.ts").write_text(
            "// tortoise-hook-version: 0\n// tortoise session\n", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "❌" in row, row
        assert "symlinked-install" in row, row
        assert "needs a manual fix" in row, row
        assert "run `tortoise install pi`" not in row, (
            "the installer refuses a symlinked install root, so recommending "
            "it is wrong")

    def test_doctor_never_recommends_the_installer_for_an_out_of_home_symlink(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """A leaf symlink whose target escapes $HOME is refused by the
        installer; the refusal is expressed in the NON-blocking symlink note,
        so the hint must consult all findings.  (Peer of the root case above.)
        """
        home = tmp_path / "home"
        self._seam(home, "// tortoise-hook-version: 0\n// tortoise session\n")
        outside = tmp_path / "outside.ts"
        outside.write_text(
            "// tortoise-hook-version: 0\n// tortoise session\n", encoding="utf-8")
        installed = (home / ".pi" / "agent" / "extensions"
                     / _pi_seam_name())
        installed.unlink()
        installed.symlink_to(outside)
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "❌" in row, row
        assert "needs a manual fix" in row, row
        assert "run `tortoise install pi`" not in row, row
