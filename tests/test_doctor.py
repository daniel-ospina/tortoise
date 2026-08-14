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
        """--db docker:// URI is parsed by from_uri (dead port proves it)."""
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:59999/tortoise"])
        out = capsys.readouterr().out

        assert rc == 1
        line = _health_line(out)
        assert "❌" in line
        assert "127.0.0.1:59999" in line  # from_uri parsed the URI host/port

    def test_doctor_db_plain_path_uses_embedded(self, clear_db_env, tmp_path, capsys):
        """--db accepts plain file paths → embedded constructor (help match)."""
        db_path = os.path.join(str(tmp_path), "via_db_flag.db")
        rc = _run_doctor(["--db", db_path])
        out = capsys.readouterr().out

        assert rc in (0, 1)
        line = _health_line(out)
        assert "0 Points" in line  # fresh embedded DB — not a URI connection error

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
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:59998/tortoise"])
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
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:notaport/tortoise"])
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
        rc = _run_doctor(["--db", "docker://:sekritpass@127.0.0.1:notaport/tortoise"])
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
        rc = _run_doctor(["--db", "docker://:p@ss@127.0.0.1:notaport/tortoise"])
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
        (docker/redis/rediss/bolt/etc) and never touches query/fragment
        delimiters — an '@' in a query value must not swallow the host."""
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
        rc = _run_doctor(["--db", "docker://:@127.0.0.1:59997/tenant-alpha"])
        out = capsys.readouterr().out

        assert rc == 1  # health check still fails against the dead port
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "tenant-alpha" in probe  # probe reports the URI path's graph
        # Every select_graph — probe AND Step 3's from_uri projection — used
        # the URI path's graph; none created a stray "tortoise" graph.
        assert set(selected) == {"tenant-alpha"}
        assert "tortoise" not in selected

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
    def test_no_flags_defaults_to_embedded(self, clear_db_env, capsys):
        """No flags, no env → shared default resolution (canonical embedded
        path), like init/index — NOT a local docker://localhost default
        (#720 conf 70). Graph health must report the embedded graph, never
        a docker connection failure."""
        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc in (0, 1)  # docker probe may fail without a live FalkorDB
        line = _health_line(out)
        assert "Points" in line
        assert "❌" not in line  # embedded resolution must not fail

    def test_no_flags_uses_env_uri(self, monkeypatch, clear_db_env, capsys):
        """TORTOISE_DB_URI env wins over embedded defaults."""
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:59999/tortoise")
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
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert rc in (0, 1)
        line = _health_line(out)
        assert "0 Points" in line


class TestDoctorSessionExtraction:
    """#1197: doctor surfaces the /v1/sessions LLM-provider gate (#822).

    Capture fails closed (503) when no provider key is configured — the beta
    testers' most-critical feature. Doctor must report the provider/model
    when configured, and FAIL in hosted mode (FLY_APP_NAME) when the key is
    missing or the test seam is left on, so ops catch it before testers do.
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
        """No key + not hosted → ⚠️ warning (capture fails closed; rc not
        driven by this check). Embedded DB so the only possible ❌ is mine."""
        monkeypatch, db_path = clean_llm_env
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "⚠️" in line
        assert "503" in line and "no LLM provider key" in line
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
        flagship beta feature cannot work; ops must not ship this."""
        monkeypatch, db_path = clean_llm_env
        monkeypatch.setenv("FLY_APP_NAME", "tortoise-api")
        rc = _run_doctor(["--path", db_path])
        out = capsys.readouterr().out

        line = self._extraction_line(out)
        assert "❌" in line
        assert "503" in line
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
        shape = next(l for l in out.splitlines() if "OpenRouter model" in l)
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
        assert not any("OpenRouter model" in l for l in out.splitlines())
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
        assert not any("OpenRouter model" in l for l in out.splitlines())
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
        assert not any("OpenRouter model" in l for l in out.splitlines())
        assert rc in (0, 1)


class TestOnboardDoctorCall:
    def test_bare_namespace_no_attribute_error(self, monkeypatch, clear_db_env, tmp_path, capsys):
        """#703 follow-up: _cmd_onboard calls _cmd_doctor(Namespace(cmd='doctor'))
        — both args.db AND args.path must be read via getattr."""
        monkeypatch.setenv("TORTOISE_DB_PATH", os.path.join(str(tmp_path), "onboard.db"))

        from tortoise.__main__ import _cmd_doctor
        rc = _cmd_doctor(argparse.Namespace(cmd="doctor"))
        out = capsys.readouterr().out

        assert rc in (0, 1)
        assert "'Namespace' object has no attribute" not in out
        line = _health_line(out)
        assert "0 Points" in line  # embedded check actually ran (not a misleading ❌)

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
