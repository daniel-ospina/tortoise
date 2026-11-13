"""#12407 — the `.env` the daemon reads must never be silently ignored by the CLI verbs.

The documented configure step is `cp .env.example .env` (`.env.example` line 1,
`AGENTS.md` §Environment). The daemon (`tortoise serve` / the MCP server) reads
that file at import; the CLI verbs do not. So on the documented setup
`tortoise doctor` grades the CANONICAL EMBEDDED FILE while the daemon serves the
FalkorDB the user just configured — and prints `Graph: health ✅` about it.

These tests pin the fix direction #12407 recommends: **do not load it, make the
divergence VISIBLE**. Two properties, and they are the ones that make the false
✅ impossible:

1. `tortoise config show` answers "which graph am I on" for BOTH entry points —
   this CLI and the daemon — and names the divergence when they differ.
2. `tortoise doctor` prints one row saying the same, so a `Graph: health ✅`
   beside it is no longer a verdict about a store the daemon does not use.

Three more properties protect the disclosure itself: a `.env` value is a
credential and must never be printed; a diagnostic must not mutate the
environment it reports on; and the report must use the SAME resolver and the
SAME `.env` parser as the two entry points (a second implementation is how a
reporter starts lying — the lesson `_cmd_config`'s own graph-store block carries).
"""
from __future__ import annotations

import json
import os

import pytest

import tortoise.__main__ as main
import tortoise.dotenv_file as dotenv_file

# The REAL cwd scan, bound at import time. The session fixture replaces the
# module ATTRIBUTE (so a developer's repo-root `.env` cannot change any test's
# output), and this module-level binding is what lets one case exercise the real
# path comparison instead of the stub. A fixture runs after collection, so the
# object bound here is the product's own function.
from tortoise.__main__ import _dotenv_unread_candidates as _real_unread_candidates

DB_ENV_VARS = (
    "TORTOISE_DB_URI",
    "TORTOISE_DB_PATH",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "FALKORDB_PASSWORD",
)

DOCKER_URI = "docker://:falkordb@localhost:6379/tortoise"
MASKED_URI = "docker://:***@localhost:6379/tortoise"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Never read the developer's real `.env`, stores, or environ.

    The loader path is derived from the INSTALLED package's own file, so without
    this patch a populated repo-root `.env` (the very state under test) would
    leak into every other test in the suite.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in (*DB_ENV_VARS, "TORTOISE_API_KEY", "TORTOISE_API_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    env_path = tmp_path / "repo" / ".env"
    monkeypatch.setattr(dotenv_file, "dotenv_path", lambda: str(env_path))
    # ⛔ `_load_dotenv` writes `os.environ` DIRECTLY, and the session-level
    # `_isolate_process_env` only restores TORTOISE_*/SUPABASE_*/PACK_STATE_* —
    # so a provider key a fixture `.env` fills (`DEEPSEEK_API_KEY=…`, an arbitrary
    # `A`/`B`/`c`) would survive the test and flip later `doctor` provider rows,
    # which is exactly what `test_doctor.py::_keyless_llm_env` exists to prevent
    # (#12428 review). Restore EVERY key added or changed while this test ran.
    before = dict(os.environ)
    # ⛔ The same for `dotenv_file.APPLIED` / `LOADED_PATHS`: they record what this
    # process took from a `.env`, and the attribution reads them against the
    # CURRENT environment — so a value an earlier test applied under the same key
    # would make this test's environment look like the file's. `APPLIED` is
    # path-scoped now, and this reset closes the remaining window (same tmp file
    # name, same value). `monkeypatch.setattr` restores the ORIGINAL objects.
    monkeypatch.setattr(dotenv_file, "APPLIED", {}, raising=False)
    monkeypatch.setattr(dotenv_file, "LOADED_PATHS", set(), raising=False)
    yield env_path
    for key in list(os.environ):
        if key not in before:
            del os.environ[key]
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


def _write_env(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _run(argv=("config", "show")):
    return main.main(list(argv))


def _show_text(capsys, argv=("config", "show")):
    assert _run(argv) == 0
    return capsys.readouterr().out


def _show_json(capsys):
    out = _show_text(capsys, ("config", "show", "--json"))
    return json.loads(out)


# ── the defective state: `.env` selects the daemon's store, the CLI ignores it ──


def test_config_show_names_the_env_file_it_does_not_read(capsys, _isolated):
    """The measured defect: `.env` says docker://, `config show` says default."""
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")

    out = _show_text(capsys)

    assert str(_isolated) in out, (
        "`config show` did not name the .env file the daemon reads; the user "
        "cannot tell the file was even considered"
    )
    assert "does NOT read it" in out, out
    assert "TORTOISE_DB_URI" in out, "the key the file sets was not named"
    # The daemon's store, masked, and the fact it is a DIFFERENT store.
    assert MASKED_URI in out, out
    # NOT the bare word "DIFFERENT": the harness block below this one already says
    # "a DIFFERENT store", so that assertion passes on an unrelated line and the
    # divergence row is pinned by nothing (measured: with `differs` hardcoded
    # False the test stayed GREEN — attribution harness, code-review Step 8).
    assert "DIFFERENT graph than the target above" in out, out


def test_config_show_json_carries_the_daemon_target(capsys, _isolated):
    """A machine consumer must get both targets, not just the CLI's."""
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")

    payload = _show_json(capsys)
    env_file = payload["env_file"]

    assert env_file["state"] == "present"
    assert env_file["path"] == str(_isolated)
    assert env_file["read_by_this_command"] is False
    assert env_file["keys"] == ["TORTOISE_DB_URI"]
    assert env_file["daemon_target"] == MASKED_URI
    assert env_file["differs"] is True, (
        "the two entry points resolve different stores; the payload said they agree"
    )
    # The CLI's own target is in the same document, so one read answers both.
    assert payload["graph_store"]["target"] != env_file["daemon_target"]


def test_doctor_reports_the_env_divergence(capsys, _isolated):
    """`doctor` graded the embedded file while the daemon served FalkorDB."""
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")

    assert _run(("doctor",)) == 0
    out = capsys.readouterr().out

    assert str(_isolated) in out, out
    assert MASKED_URI in out, (
        "doctor did not name the store the daemon actually serves"
    )
    assert "does NOT read it" in out, out


# ── the divergence must be reported, never invented ───────────────────────────


def test_doctor_is_silent_when_no_env_file_exists(capsys, _isolated):
    """No `.env` → nothing is being ignored → no row (no new noise)."""
    assert _run(("doctor",)) == 0
    out = capsys.readouterr().out

    assert ".env" not in out.replace(".env.example", ""), out


def test_absent_env_file_is_reported_as_absent_in_json(capsys, _isolated):
    payload = _show_json(capsys)

    assert payload["env_file"]["state"] == "absent"
    assert payload["env_file"]["differs"] is False
    assert payload["env_file"]["daemon_target"] is None


def test_env_key_already_in_the_environment_cannot_change_the_target(
        capsys, _isolated, monkeypatch):
    """`_load_dotenv` fills ABSENT keys only — so the file changes nothing here.

    Reporting a divergence for a key the environment already wins would send a
    user hunting a difference that cannot exist.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", DOCKER_URI)
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")

    payload = _show_json(capsys)
    env_file = payload["env_file"]

    assert env_file["differs"] is False
    assert env_file["graph_keys"] == [], (
        "a key the environment already sets was reported as taking effect"
    )
    assert env_file["keys"] == ["TORTOISE_DB_URI"], (
        "the file's own keys must still be disclosed — the user needs to know "
        "the file also sets it"
    )


def test_env_file_setting_no_graph_key_is_reported_without_a_divergence(
        capsys, _isolated):
    """A `.env` that only carries provider keys is still not read — say so."""
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    payload = _show_json(capsys)
    env_file = payload["env_file"]

    assert env_file["state"] == "present"
    assert env_file["keys"] == ["DEEPSEEK_API_KEY"]
    assert env_file["graph_keys"] == []
    assert env_file["differs"] is False


def test_a_password_in_the_env_file_never_reaches_the_report(capsys, _isolated):
    """`.env` values are credentials; the report names KEYS, never values."""
    secret = "s3cr3t-pw-xyzzy"
    uri = f"docker://user:{secret}@db.example:6379/tortoise"
    _write_env(_isolated, f"TORTOISE_DB_URI={uri}\n")

    out = _show_text(capsys)
    assert secret not in out, (
        "the .env value was printed — a diagnostic must not leak the credential "
        "it reads"
    )
    # …and the same for the machine-readable half.
    payload = _show_json(capsys)
    assert secret not in json.dumps(payload)


def test_reporting_does_not_mutate_the_environment(capsys, _isolated):
    """The daemon-target computation overlays `.env`; it must not KEEP it.

    A diagnostic that exports what it reports would change the very resolution
    the NEXT verb in the same shell performs.
    """
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")
    before = dict(os.environ)

    _show_text(capsys)

    assert "TORTOISE_DB_URI" not in os.environ
    assert dict(os.environ) == before, (
        "`config show` left the environment changed: "
        f"{set(os.environ) ^ set(before)}"
    )


# ── one parser, one resolver ──────────────────────────────────────────────────


def test_the_shared_parser_matches_what_the_daemon_applies(tmp_path, monkeypatch):
    """The disclosure parses `.env` the SAME way the daemon does.

    Pin the parse rules the daemon's loader implements (quoted values literal,
    unquoted values lose an inline comment, `export ` prefix, bare keys
    skipped), so a second parser cannot drift from the one that decides what
    the daemon really uses.
    """
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "export EXPORTED=one\n"
        "PLAIN=two\n"
        'QUOTED="three # not a comment"\n'
        "INLINE=four # stripped\n"
        "NOEQUALS\n"
        "EMPTY=\n"
    )

    parsed = dotenv_file.parse_dotenv(str(env))

    assert parsed == {
        "EXPORTED": "one",
        "PLAIN": "two",
        "QUOTED": "three # not a comment",
        "INLINE": "four",
        "EMPTY": "",
    }


def test_the_daemon_loader_and_the_parser_agree(tmp_path, monkeypatch):
    """`_load_dotenv` applies exactly what `parse_dotenv` returns."""
    from tortoise.mcp_server import _load_dotenv

    env = tmp_path / ".env"
    env.write_text('A=1\nB="two words"\nc=x # tail\n')
    for key in ("A", "B", "c"):
        monkeypatch.delenv(key, raising=False)

    expected = dotenv_file.parse_dotenv(str(env))
    _load_dotenv(str(env))

    assert expected == {"A": "1", "B": "two words", "c": "x"}
    for key, value in expected.items():
        assert os.environ[key] == value


def test_the_parser_refuses_a_non_regular_file(tmp_path):
    """A FIFO at the `.env` path must not hang the verb that reads it.

    `read_text()` on a FIFO blocks in `open()` with no writer — the hang would
    be in a diagnostic the user ran TO find out what is wrong. The parser
    therefore reads regular files only.
    """
    fifo = tmp_path / ".env"
    os.mkfifo(fifo)

    assert dotenv_file.parse_dotenv(str(fifo)) == {}


# ── the verdict split: informational, warning, and hard failure ───────────────


def test_a_same_target_env_file_is_informational_not_a_warning(capsys, _isolated,
                                                              monkeypatch, tmp_path):
    """The docs tell a user to create this file — do not warn on every setup.

    `doctor`'s own alert-fatigue rule (#3615): a row that fires ⚠️ on the
    documented configuration teaches the reader to ignore warnings. The file is
    still DISCLOSED, because "this command does not read it" is the part a user
    cannot see.

    The shape must be one where the daemon and the graded store AGREE: an
    environment that already sets the same URI (so the file's fill is inert).
    A `.env` with only a provider key is NOT that shape — the daemon refuses to
    start when neither TORTOISE_DB_URI nor TORTOISE_DB_PATH is set, which is its
    own (warned) state.
    """
    # DB_PATH in the ENVIRONMENT (not the file): both ladders then resolve it, so
    # the file's provider key is inert for the graph target and the row is info.
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "same.db"))
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    # rc is not asserted: `doctor`'s fail count on a box includes unrelated
    # checks (the issue itself records a local pi-hook staleness fail), so the
    # ROW is what this pins.
    _run(("doctor",))
    out = capsys.readouterr().out

    assert "ℹ️ Config: .env" in out, out
    assert "⚠️ Config: .env" not in out, out
    assert "does NOT read it" in out, out


def test_doctor_fails_when_the_env_file_breaks_the_daemons_target(capsys, _isolated):
    """A `.env` whose DAEMON key cannot resolve is the ❌ arm.

    `TORTOISE_DB_URI=relative/path.db` makes `resolve_db_path` REFUSE (a relative
    path is never permitted), so the daemon cannot open anything — a refusal no
    retry fixes, while the rows below are green about a store it will never see.

    ⛔ NOT `FALKORDB_PORT=notaport`: the DAEMON never reads the legacy trio (only
    the CLI does), so that value cannot break it. Reporting it as a daemon
    failure was the round-1 review finding (#12428) — the key list this reports
    on is the daemon's, imported from `config.DAEMON_DB_KEYS`.
    """
    _write_env(_isolated, "TORTOISE_DB_URI=relative/path.db\n")

    assert _run(("doctor",)) == 1
    out = capsys.readouterr().out

    assert "❌ Config: .env" in out, out
    assert "unresolvable" in out, out
    assert "relative/path.db" in out, "the offending value was not named"


def test_a_legacy_trio_only_env_file_is_the_daemons_REFUSAL(capsys, _isolated):
    """`.env` with only `FALKORDB_*`: the daemon reads NONE of it and refuses.

    Measured before the fix (PR #12428 review round 1): the row claimed "the
    daemon serves docker://…" — the CLI's trio target, attributed to a daemon
    that never reads the trio and exits 1 in this state.
    """
    _write_env(_isolated, "FALKORDB_HOST=localhost\nFALKORDB_PORT=6379\n")

    assert _run(("doctor",)) in (0, 1)
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)
    assert "REFUSES to start" in row, row

    payload = _show_json(capsys)
    env_file = payload["env_file"]
    assert env_file["daemon_rung"] == "refuses"
    assert env_file["daemon_target"] is None
    assert env_file["graph_keys"] == []      # the trio is NOT a daemon key
    assert "docker://" not in json.dumps(env_file)


def test_a_scheme_less_env_uri_WINS_over_the_environments_db_path(capsys, _isolated,
                                                                monkeypatch):
    """The daemon's ladder differs from the CLI's on this rung — say so.

    Measured before the fix: env `TORTOISE_DB_PATH=/a.db` + `.env`
    `TORTOISE_DB_URI=/b.db` was reported as "the same graph target" — while the
    daemon opens `/b.db` (URI wins) and the CLI resolves `/a.db` (DB_PATH wins).
    A missed divergence closed with a reassurance is the false PASS #12407 is
    about.
    """
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/cli-side.db")
    _write_env(_isolated, "TORTOISE_DB_URI=/tmp/daemon-side.db\n")

    payload = _show_json(capsys)
    env_file = payload["env_file"]

    assert env_file["differs"] is True, json.dumps(env_file, indent=2)
    assert env_file["daemon_target"] == "/tmp/daemon-side.db"
    assert env_file["cli_target"] == "/tmp/cli-side.db"

    assert _run(("doctor",)) in (0, 1)
    out = capsys.readouterr().out
    assert "/tmp/daemon-side.db" in out and "DIFFERENT store" in out, out


def test_doctor_reports_an_unreadable_env_file(capsys, _isolated):
    """"Cannot tell" is never a pass: the file exists and was not read."""
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n").chmod(0o000)
    if os.access(_isolated, os.R_OK):
        # Root ignores mode 000, so the precondition cannot be created — the
        # product is right and this host cannot express the case (containers and
        # some CI images run as root). Skipping is honest; a red here would
        # blame the code for the uid.
        pytest.skip("running as a uid that ignores mode 000 — cannot create the state")

    assert _run(("doctor",)) == 0
    out = capsys.readouterr().out

    assert "⚠️ Config: .env" in out, out
    assert "cannot be read" in out, out


def test_config_show_json_carries_a_daemon_error(capsys, _isolated):
    """`--json` must carry the failure, not imply the daemon resolved fine."""
    _write_env(_isolated, "TORTOISE_DB_URI=relative/path.db\n")

    payload = _show_json(capsys)
    env_file = payload["env_file"]

    assert "relative/path.db" in env_file["daemon_error"]
    assert env_file["daemon_target"] is None
    assert env_file["differs"] is False


# ── the file no reader reads (the documented step, in the wrong directory) ────


def test_a_cwd_env_file_no_entry_point_reads_is_reported(capsys, _isolated, tmp_path,
                                                         monkeypatch):
    """`cp .env.example .env` in a PROJECT dir: the daemon reads the package root.

    Nothing reads the cwd file — not the CLI (no verb loads a `.env`) and not the
    daemon (it resolves from its own package location). Reporting it is the same
    silently-ignored class one level out, and it is the shape a pip install
    produces for a user who followed the instructions.
    """
    # The suite pins the scan away from the machine (conftest), so hand it this
    # one file explicitly — the DISCOVERY itself is pinned separately, below.
    _write_env(tmp_path / ".env", f"TORTOISE_DB_URI={DOCKER_URI}\n")
    monkeypatch.setattr(main, "_dotenv_unread_candidates",
                        lambda daemon_path: [str(tmp_path / ".env")])

    payload = _show_json(capsys)
    unread = payload["env_file"]["unread_env_files"]

    assert [entry["path"] for entry in unread] == [str(tmp_path / ".env")]
    assert payload["env_file"]["state"] == "absent"  # the daemon's own path
    assert "TORTOISE_DB_URI" in unread[0]["graph_keys"]

    assert _run(("doctor",)) == 0
    out = capsys.readouterr().out
    assert "NO Tortoise entry point" in out, out


def test_the_unread_scan_ignores_the_daemons_own_file(tmp_path, monkeypatch):
    """The cwd scan must not report the daemon's file as "read by nobody".

    Pinned separately from the reporting above because the suite replaces the
    scan wholesale (a developer's repo-root `.env` must not change test output);
    this is the only case that exercises the real path comparison.
    """
    _write_env(tmp_path / ".env", f"TORTOISE_DB_URI={DOCKER_URI}\n")
    monkeypatch.chdir(tmp_path)
    elsewhere = str(tmp_path / "other" / ".env")
    own = str(tmp_path / ".env")

    assert _real_unread_candidates(elsewhere) == [own]
    assert _real_unread_candidates(own) == []
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    assert _real_unread_candidates(elsewhere) == []


def test_the_daemon_dotenv_is_not_reported_as_unread_when_cwd_is_the_repo(
        capsys, _isolated, monkeypatch):
    """cwd == the daemon's root is the documented case, not a second file."""
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")
    monkeypatch.chdir(_isolated.parent)

    payload = _show_json(capsys)

    assert payload["env_file"]["unread_env_files"] == []


def test_a_null_byte_in_the_env_file_is_reported_not_raised(capsys, _isolated):
    """A diagnostic must not traceback on the condition it diagnoses.

    `os.environ` refuses a value carrying a NUL byte, and the parser decodes a
    binary file with `errors="replace"` — so a NUL can survive parsing and blow
    up the environment overlay that computes the daemon's target. It is reported
    as an unloadable file (the daemon cannot load it either), never as a crash.
    """
    _isolated.parent.mkdir(parents=True, exist_ok=True)
    _isolated.write_bytes(b"TORTOISE_DB_URI=docker://:pw@h:6379/t\x00ortoise\n")

    payload = _show_json(capsys)
    env_file = payload["env_file"]

    assert env_file["daemon_target"] is None
    assert "cannot be loaded" in env_file["daemon_error"]
    # …and the environment it inspects is untouched afterwards.
    assert "TORTOISE_DB_URI" not in os.environ


# ── review round 1: the three findings, each pinned in the direction it failed ──


def test_a_control_byte_in_the_env_file_cannot_rewrite_the_terminal(
        capsys, _isolated, monkeypatch, tmp_path):
    """A `.env` is attacker-shippable text; its KEY NAMES reach a terminal.

    `.gitignore` excludes the file, but `git add -f`, a tarball or a zip still
    ships it. Before the fix, a key carrying an ANSI clear-screen repainted
    `doctor`'s output — i.e. the very rows this change adds could be erased or
    forged. `_printable` is the escape everything else in this file uses.
    """
    # Two shapes, because the two doctor arms print DIFFERENT fields: a
    # control byte in a key NAME reaches the "present, same target" row's key
    # list, and one inside the URI reaches the divergent row's target. (The
    # divergent row's `graph_keys` are the ladder's own fixed names, so a key
    # name cannot reach it — hence two cases rather than one.)
    # The "present, same target" arm is the one that prints arbitrary KEY NAMES;
    # an environment DB_PATH makes both ladders agree so that arm is reached.
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "same.db"))
    _write_env(_isolated, "\x1b[2J\x1b[HFAKEKEY=1\n")
    _run(("doctor",))
    out = capsys.readouterr().out
    assert "Config: .env" in out, out
    assert "\x1b" not in out, (
        "a control byte from a .env KEY NAME reached doctor's output raw"
    )
    assert "\\x1b" in out, "the escaping is not visible either — check _printable"

    _write_env(_isolated, "TORTOISE_DB_URI=docker://:pw@h:6379/t\x1b[2Jortoise\n")
    assert _run(("doctor",)) in (0, 1)
    out = capsys.readouterr().out
    assert "\x1b" not in out, (
        "a control byte in the .env TARGET reached doctor's output raw"
    )

    payload = _show_json(capsys)
    assert "\x1b" not in json.dumps(payload)


def test_doctor_compares_the_store_it_grades_not_the_environments(
        capsys, _isolated, tmp_path):
    """`doctor --db <path>` grades ITS target; the row must say that store.

    Measured before the fix: with `.env` selecting FalkorDB and
    `doctor --db /tmp/other.db`, the row claimed "the rows below grade that
    DIFFERENT store" while they graded `/tmp/other.db` — a reassurance about a
    store the run was not about to open.
    """
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")
    graded = tmp_path / "graded.db"

    assert _run(("doctor", "--db", str(graded))) in (0, 1)
    out = capsys.readouterr().out

    row = next(line for line in out.splitlines() if "Config: .env" in line)
    assert str(graded) in row, (
        f"the row does not name the store the rows below grade: {row}"
    )
    assert MASKED_URI in row, row


def test_config_show_does_not_claim_the_environment_sets_an_effective_key(
        capsys, _isolated):
    """A `.env` graph key that TAKES EFFECT must not be described as shadowed.

    `graph_keys` is the set absent from the environment — the keys that DO take
    effect. Keying the "the environment already sets them" sentence on it
    inverted the claim, and the empty case then said "no graph key in this
    file" about a file whose key the environment shadows.
    """
    shadowed = "TORTOISE_DB_PATH=/tmp/from-the-file.db"
    _write_env(_isolated, shadowed + "\n")

    out = _show_text(capsys)
    assert "already sets" not in out, out
    assert "no graph key in this file" not in out, out


def test_config_show_names_the_shadowed_key_as_the_reason(capsys, _isolated,
                                                          monkeypatch):
    """The shadowed case names the environment as the reason, not the file."""
    monkeypatch.setenv("TORTOISE_DB_URI", DOCKER_URI)
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")

    out = _show_text(capsys)
    assert "already sets TORTOISE_DB_URI" in out, out
    assert "cannot change the target here" in out, out


def test_the_already_applied_state_is_recorded_not_inferred(capsys, _isolated):
    """`read_by_this_command` reports what HAPPENED, not what was imported.

    The suite imports `tortoise.mcp_server` (its loader is tested elsewhere), and
    under pytest that import's `.env` load is SKIPPED — so "the module is in
    `sys.modules`" is not evidence the file was applied. This pins the recorded
    fact: false until the loader actually runs for this path, true after.
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")
    payload = _show_json(capsys)
    assert payload["env_file"]["read_by_this_command"] is False, (
        "importing the daemon module is not applying the file — under pytest its "
        "load is skipped, so this must stay False"
    )

    _load_dotenv(str(_isolated))
    payload = _show_json(capsys)
    assert payload["env_file"]["read_by_this_command"] is True
    # …and the prose stops claiming it does not read it.
    assert _run(("config", "show")) == 0
    out = capsys.readouterr().out
    assert "this process already applied that file at import" in out, out


def test_a_control_byte_in_a_broken_env_value_cannot_rewrite_the_terminal(
        capsys, _isolated):
    """The ❌ arm prints a RESOLVER MESSAGE, which can quote the `.env` value.

    `resolve_db_path` refuses a relative target by embedding it in the message
    (`RELATIVE_PATH_ERROR`), and `_scrub` removes credentials from that text but
    not control bytes — so the ❌ row is a second path from a shipped `.env` to
    a terminal, after the keys.
    """
    _write_env(_isolated, "TORTOISE_DB_URI=relative/\x1b[2J\x1b[Hpwned.db\n")

    _run(("doctor",))
    out = capsys.readouterr().out

    assert "❌ Config: .env" in out, out
    assert "\x1b" not in out, "a control byte from a .env VALUE reached doctor raw"
    assert "\\x1b" in out, out


# ── review round 2: the credential that leaked through the resolver's message ──

# Each is REFUSED by `resolve_db_path` (so the resolver quotes it) AND carries a
# credential, which is the combination that leaked.
_CREDENTIAL_URIS = (
    "alice:s3cr3t-PASSW0RD@dbhost.internal/tortoise.db",   # userinfo, scheme-less
    "./x.db?password=QUERY-SECRET-9",                      # query credential (#5109)
    "token@host/SECRET-TOKEN.db",                          # colon-less userinfo (#2987)
)


@pytest.mark.parametrize("uri", _CREDENTIAL_URIS)
def test_a_credential_in_a_refused_env_value_never_reaches_any_surface(
        capsys, _isolated, uri):
    """`RELATIVE_PATH_ERROR` embeds the value — on all THREE surfaces.

    `resolve_db_path` refuses a scheme-less relative target and quotes it, so the
    `.env` value reaches a printed message. Round 1 introduced a raw `{exc}`
    interpolation there (a REGRESSION: the parent commit scrubbed), and the
    suite could not see it because its only credential case was a well-formed
    `docker://` URI, which never enters the error arm (#12428 review round 2).
    """
    secret = ("QUERY-SECRET-9" if "password=" in uri
              else uri.split("@")[0].lstrip("./").split(":")[-1]
              if uri.startswith("alice:")
              else "SECRET-TOKEN")
    _write_env(_isolated, f"TORTOISE_DB_URI={uri}\n")

    # 1. prose
    out = _show_text(capsys)
    assert secret not in out, f"leaked in `config show` prose: {out}"
    # 2. --json
    payload = _show_json(capsys)
    assert secret not in json.dumps(payload), "leaked in `config show --json`"
    assert payload["env_file"]["daemon_error"], "the refusal was not reported at all"
    # 3. doctor
    _run(("doctor",))
    out = capsys.readouterr().out
    assert secret not in out, f"leaked in `doctor`: {out}"


def test_the_environment_value_is_redacted_too(capsys, _isolated, monkeypatch):
    """The ENV value leaks through the same arm when the `.env` sets any other key.

    The `.env`-free path was already protected; the path where a `.env` exists
    was not — which inverts the threat model.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "alice:ENV-SECRET-PW@h/x.db")
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    out = _show_text(capsys)
    assert "ENV-SECRET-PW" not in out, out


def test_a_plain_relative_path_stays_legible_in_the_refusal(capsys, _isolated):
    """Redaction must cut the CREDENTIAL, not the diagnosis.

    `_scrub`-the-whole-value would replace `relative/path.db` with a marker,
    destroying the one thing the message exists to say — so the two credential
    shapes are cut and a plain path is kept.
    """
    _write_env(_isolated, "TORTOISE_DB_URI=relative/path.db\n")

    payload = _show_json(capsys)
    assert "relative/path.db" in payload["env_file"]["daemon_error"]


def test_config_show_does_not_reassure_when_the_daemon_refuses(capsys, _isolated):
    """The refusal must not be followed by "the target above is the daemon's too".

    Measured: `FALKORDB_*`-only `.env` printed the refusal line and then "no
    graph key in this file, so the target above is the daemon's too" — two
    adjacent sentences contradicting each other (#12428 review round 2).
    """
    _write_env(_isolated, "FALKORDB_HOST=localhost\nFALKORDB_PORT=6379\n")

    out = _show_text(capsys)
    assert "REFUSES to" in out, out
    assert "the daemon's too" not in out, out
    assert "the same graph target as above" not in out, out

    # …and the same for an EMPTY ladder value, which otherwise reads as effective.
    _write_env(_isolated, "TORTOISE_DB_URI=\n")
    out = _show_text(capsys)
    assert "REFUSES to" in out, out
    assert "the same graph target as above" not in out, out


def test_the_daemons_refusal_is_still_the_one_the_user_sees(monkeypatch):
    """`_get_sdk` must stay constructible on the refusal rung.

    `main()` calls `_get_sdk()` BEFORE its own "set TORTOISE_DB_URI … in the
    environment or .env" refusal, so handing `TortoiseSDK(db_path=None)` for that
    rung made the SDK's own production guard raise `RuntimeError` first and
    replace the message with a traceback (measured on a `FLY_APP_NAME` host,
    #12428 review round 2). Both paths exit non-zero, so nothing false passed —
    but the refusal a user READS is the product's.
    """
    import tortoise.mcp_server as mcp_mod

    for key in ("TORTOISE_DB_URI", "TORTOISE_DB_PATH", "TORTOISE_ALLOW_EMBEDDED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "tortoise-test")
    monkeypatch.setattr(mcp_mod, "_sdk", None, raising=False)
    monkeypatch.setattr(mcp_mod, "sdk", None, raising=False)

    sdk = mcp_mod._get_sdk()          # must NOT raise
    assert sdk is not None


@pytest.mark.parametrize("line", [
    # A quoted python-dotenv value keeps its inner padding (the quotes go), while
    # `resolve_db_path` STRIPS what it quotes back — so the printed form differs
    # from the file's bytes by whitespace alone, and an exact-substring redactor
    # matches nothing. Measured leak under round 2's own fix, closed in round 3.
    # A CREDENTIAL-SHAPED value, on the rung the resolver STRIPS: the quoted form
    # differs from the printed form by whitespace alone, and a bare relative path
    # (which is not a secret) is deliberately left legible — see
    # `test_a_plain_relative_path_stays_legible_in_the_refusal`.
    'TORTOISE_DB_PATH="   alice:PADDED-SECRET-PW@host/x.db   "',
    'TORTOISE_DB_URI=" alice:PADDED-SECRET-PW@host/x.db "',
    "TORTOISE_DB_URI=alice:PADDED-SECRET-PW@host/x.db\t",
])
def test_a_padded_credential_is_redacted_too(capsys, _isolated, line):
    """"It redacts the value" must hold for the value the RESOLVER quotes."""
    _write_env(_isolated, line + "\n")

    out = _show_text(capsys)
    assert "PADDED-SECRET-PW" not in out, f"leaked in prose: {out}"
    payload = _show_json(capsys)
    assert "PADDED-SECRET-PW" not in json.dumps(payload), "leaked in --json"
    _run(("doctor",))
    out = capsys.readouterr().out
    assert "PADDED-SECRET-PW" not in out, f"leaked in doctor: {out}"


# ── review round 3 ───────────────────────────────────────────────────────────


def test_doctor_reports_the_import_that_applies_the_file_mid_run(capsys, _isolated):
    """`doctor` imports the daemon module (§5.5) — which APPLIES the `.env`.

    So the row must not be computed before that import: measured before the fix,
    `doctor` printed "this command does NOT read it" and then a
    `Session extraction` verdict that existed only because it HAD read it
    (`#12428` review round 3). The row now discloses the mid-run application
    instead of denying it.
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")
    # Apply it the way `doctor`'s own §5.5 import does — deterministically, so
    # the case does not depend on what else a given box happens to import.
    _load_dotenv(str(_isolated))

    _run(("doctor",))
    out = capsys.readouterr().out

    assert "Config: .env" in out, out
    assert "this run imported the daemon module" in out, (
        "the process applied the .env and the row did not disclose it"
    )


def test_a_duplicate_key_keeps_the_first_value_like_the_old_loader(tmp_path):
    """Duplicate keys: FIRST wins — the pre-change daemon behaviour.

    The old loader wrote a key only when absent, so a second line for the same
    key was skipped. A dict comprehension silently flipped that to last-wins,
    which for `TORTOISE_DB_URI` changes the DAEMON's graph for anyone who
    appended a line instead of editing the first — a split-graph change with no
    migration note (#12428 review round 3).
    """
    env = tmp_path / ".env"
    env.write_text("TORTOISE_DB_URI=/first.db\nTORTOISE_DB_URI=/second.db\n")

    assert dotenv_file.parse_dotenv(str(env))["TORTOISE_DB_URI"] == "/first.db"


def test_config_show_does_not_reassure_when_the_cli_target_does_not_resolve(
        capsys, _isolated, monkeypatch):
    """The mirror of the daemon-refuses arm: the CLI resolved NOTHING.

    Measured: env `FALKORDB_PORT=notaport` + a `.env` selecting a store printed
    "configured but unusable" and then "the same graph target as above … the two
    ladders agree", where the CLI had no target at all.
    """
    monkeypatch.setenv("FALKORDB_PORT", "notaport")
    _write_env(_isolated, "TORTOISE_DB_PATH=/tmp/daemon-side.db\n")

    out = _show_text(capsys)

    # The prose wraps, so assert the fragments rather than one line.
    assert "own target did not resolve" in out, out
    assert "cannot be compared" in out, out
    assert "the same graph target as above" not in out, out


def test_doctor_does_not_name_a_store_it_never_graded(capsys, _isolated, tmp_path):
    """`--db <unresolvable>` ⇒ the row must not claim the rows below grade a store.

    Measured before the fix: the row named the ENVIRONMENT's store as "the rows
    below grade" while the rows graded nothing (`#12428` review round 3).
    """
    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")

    _run(("doctor", "--db", "relative/x.db"))
    out = capsys.readouterr().out

    row = next(line for line in out.splitlines() if "Config: .env" in line)
    assert "grade NO store" in row, row
    assert "the rows below grade" not in row.replace("rows below grade NO store", ""), row


def test_doctor_does_not_reassure_when_the_ENVIRONMENT_target_fails(
        capsys, _isolated, monkeypatch):
    """The twin of the `config show` case, on the doctor surface.

    `FALKORDB_PORT=notaport` makes the run's own resolution fail with no `--db`
    involved, so the row must say the rows grade NO store — measured before the
    fix: `ℹ️ … it selects the same graph target the rows below grade
    ((unresolved))` while the graph rows reported the failure (#12428 round 4).
    """
    monkeypatch.setenv("FALKORDB_PORT", "notaport")
    _write_env(_isolated, "TORTOISE_DB_PATH=/tmp/daemon-side.db\n")

    _run(("doctor",))
    out = capsys.readouterr().out

    row = next(line for line in out.splitlines() if "Config: .env" in line)
    assert "grade NO store" in row, row
    assert "same graph target" not in row, row
    assert "(unresolved)" not in row, row


def test_the_sets_clause_describes_the_FILE_not_what_is_left_to_fill(
        capsys, _isolated):
    """"what the file sets" must survive the fill being emptied mid-run.

    A mid-run application (`doctor`'s own import, or any earlier `_load_dotenv`)
    makes every key "already set", so `graph_keys` — the keys that would STILL be
    filled — goes empty while the file plainly sets one. Reading that as "sets no
    graph key" misstated the file's contents on a full dev install
    (#12428 round 4, NB1).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated, f"TORTOISE_DB_URI={DOCKER_URI}\n")
    _load_dotenv(str(_isolated))          # as any earlier import would

    out = _show_text(capsys)
    assert "sets TORTOISE_DB_URI" in out, out
    assert "no graph key" not in out, out


def test_config_show_does_not_deny_reading_a_file_it_just_applied(capsys, _isolated):
    """The applied state must not say both "does NOT read it" and "already applied".

    Measured before the fix, with a `.env` setting BOTH ladder keys (so the two
    ladders still rank them differently): one block printed "this command does
    NOT read it", then "this process already applied that file at import", then
    reported a DIFFERENT graph. The later lines agree with each other; the
    HEADER was the one still denying the read (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated,
               "TORTOISE_DB_URI=/tmp/daemon-side.db\n"
               "TORTOISE_DB_PATH=/tmp/cli-side.db\n")
    _load_dotenv(str(_isolated))          # what `doctor`'s §5.5 import does

    out = _show_text(capsys)

    assert "THIS PROCESS applied it at import" in out, out
    assert "does NOT read it" not in out, (
        "the header denied reading a file this process had just applied"
    )
    assert "already applied that file at import" in out, out
    # …and the divergence it must still report is reported.
    assert "/tmp/daemon-side.db" in out and "/tmp/cli-side.db" in out, out


def test_doctor_row_does_not_deny_a_read_it_just_performed(capsys, _isolated):
    """The applied+divergent `doctor` row is one sentence, so it must not split.

    Measured before the fix: the row read "this command does NOT read it, the
    daemon does … — ⚠️ this run imported the daemon module, which applies the
    file at import, so its values ARE in this process now (the graph verdict was
    resolved before that)". That is a denial and a confession in one line, and
    the parenthetical is FALSE when the file was applied before the run: the
    graded target came FROM the file's values (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated,
               "TORTOISE_DB_URI=/tmp/daemon-side.db\n"
               "TORTOISE_DB_PATH=/tmp/cli-side.db\n")
    _load_dotenv(str(_isolated))

    _run(("doctor",))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "does NOT read it" not in row, row
    assert "applied it at import as well" in row, row
    assert "the two LADDERS" in row, row
    assert "resolved before that" not in row, row


def test_config_show_does_not_credit_an_inert_file_key(capsys, _isolated, monkeypatch):
    """A key the daemon never ran must not be named as the reason they agree.

    Measured before the fix: env `TORTOISE_DB_URI` (which WINS the daemon's
    ladder) plus a `.env` `TORTOISE_DB_PATH` printed "the file's
    TORTOISE_DB_PATH resolves to it for the daemon too" — the file's key resolves
    to nothing, and naming it is the "reason that cannot explain the outcome"
    class round 1 removed (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", DOCKER_URI)
    _write_env(_isolated, "TORTOISE_DB_PATH=/tmp/never-used.db\n")

    out = _show_text(capsys)

    assert "resolves to it for the daemon too" not in out, out
    assert "the ENVIRONMENT's" in out, out
    assert "TORTOISE_DB_URI decides it" in out, out
    # The file's inert key is still DISCLOSED BY NAME (never its value).
    assert "TORTOISE_DB_PATH" in out, "the file's inert key was not named at all"


def test_doctor_error_arm_does_not_deny_an_applied_read(capsys, _isolated, monkeypatch):
    """The ❌ arm of the row: applied ⇒ the rows below DO show that error.

    Measured before the fix: `.env` applied, daemon target unresolvable, and the
    row still said "this command does not read it, so the rows below do not show
    that error" — while the rows below resolved from the same values and showed
    exactly that error (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/a.db")
    _write_env(_isolated, "TORTOISE_DB_URI=relative/path.db\n")
    _load_dotenv(str(_isolated))

    _run(("doctor",))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "❌ Config: .env" in row, row
    assert "do not show that error" not in row, row
    # ⛔ The row now makes NO claim about the rows below at all. Every version of
    # that tail was false in some reachable state — the graded store resolved fine
    # here (`TORTOISE_DB_PATH` outranks the file's broken URI on the CLI's ladder),
    # so "the rows below fail the same way" was false, and "graded the store
    # above" was false in the arm where it resolved normally (#12428 review).
    assert "fail the same way" not in row, row
    assert "graded the store above" not in row, row
    assert "resolved from the same values" not in row, row
    assert "the value it rejects is the one this `.env` supplied" in row, row


def test_doctor_unresolved_arm_does_not_deny_an_applied_read(capsys, _isolated,
                                                             monkeypatch):
    """The "rows grade NO store" arm: same conditional clause.

    Measured before the fix: applied state + an unresolvable OWN target, and the
    row said "is not read by this command (the daemon reads it)" (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    monkeypatch.setenv("FALKORDB_PORT", "notaport")
    _write_env(_isolated, "TORTOISE_DB_PATH=/tmp/daemon-side.db\n")
    _load_dotenv(str(_isolated))

    _run(("doctor",))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "is not read by this command" not in row, row
    assert "applied it at import as well" in row, row
    assert "grade NO store" in row, row


def test_config_show_names_the_LADDERS_as_the_cause_when_it_applied_the_file(
        capsys, _isolated):
    """The applied+divergent `config show` block must not blame a file read.

    Measured before the fix: the header correctly said "and THIS PROCESS applied
    it at import", and three lines later the block said "the daemon loads that
    file at import, so it serves a DIFFERENT graph … this command resolved from
    the environment only" — a cause that cannot operate, since both read the same
    environment (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated,
               "TORTOISE_DB_URI=/tmp/daemon-side.db\n"
               "TORTOISE_DB_PATH=/tmp/cli-side.db\n")
    _load_dotenv(str(_isolated))

    out = _show_text(capsys)

    assert "they differ only through the two LADDERS ranking" in out, out
    assert "resolved from the environment only" not in out, out


def test_two_at_paths_are_not_described_as_credential_only(capsys, _isolated,
                                                           monkeypatch):
    """Two `@`-bearing PATHS mask alike; that is not "differ only in credentials".

    Measured before the fix: env `TORTOISE_DB_PATH=/srv/alice@corp/cli.db` and a
    `.env` URI `/home/bob@corp/daemon.db` both mask to the same sentinel, and the
    row said "the two targets differ only in masked credential bytes" — i.e. "same
    store, different login" about two different databases, a false reassurance
    about STORE IDENTITY (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_PATH", "/srv/alice@corp/cli.db")
    _write_env(_isolated, "TORTOISE_DB_URI=/home/bob@corp/daemon.db\n")

    out = _show_text(capsys)

    assert "differ only in masked credential bytes" not in out, out
    assert "the difference cannot be shown here" in out, out


def test_doctor_info_row_does_not_credit_a_file_key_that_cannot_act(
        capsys, _isolated, monkeypatch):
    """Provider-only `.env` + an environment store: the file selected nothing.

    Measured before the fix: `sets DEEPSEEK_API_KEY … it selects the same graph
    target the rows below grade (/tmp/env-only.db)` — the file has no graph key
    at all (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/env-only.db")
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    _run(("doctor",))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "it selects the same graph target" not in row, row
    assert "the environment already sets TORTOISE_DB_PATH" in row, row


def test_doctor_info_row_says_when_the_file_has_no_graph_key(
        capsys, _isolated, monkeypatch):
    """The `embedded-allowed` rung: no ladder key anywhere, so say exactly that."""
    monkeypatch.setenv("TORTOISE_ALLOW_EMBEDDED", "1")
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    _run(("doctor",))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "it selects the same graph target" not in row, row
    assert "no graph key is in this file" in row, row


def test_doctor_does_not_blame_the_ladders_for_an_explicit_target(capsys, _isolated):
    """`--path` grades its OWN target, so the difference is not ladder ranking.

    Measured before the fix: applied `.env` + `doctor --path …/graded.db` printed
    "both read the SAME environment now, so the difference comes from the two
    LADDERS ranking its keys differently" — the difference was the flag
    (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated, "TORTOISE_DB_URI=/tmp/daemon-side.db\n")
    _load_dotenv(str(_isolated))
    graded = str(_isolated.parent / "graded.db")

    _run(("doctor", "--path", graded))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "two LADDERS" not in row, row
    assert "graded its OWN explicit target" in row, row


def test_a_broken_ENVIRONMENT_value_is_not_blamed_on_the_file(capsys, _isolated,
                                                             monkeypatch):
    """The file is healthy; the ENVIRONMENT's URI is what does not resolve.

    Measured before the fix: env `TORTOISE_DB_URI=relative/env.db` (invalid) with
    a valid `.env` `TORTOISE_DB_PATH` printed "— this `.env` cannot be loaded at
    all" and then "the target above does not show that error", while the target
    above showed exactly that error: two false statements about a healthy file
    (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "relative/env.db")
    _write_env(_isolated, "TORTOISE_DB_PATH=/tmp/file-path.db\n")

    out = _show_text(capsys)

    assert "cannot be loaded at all" not in out, out
    assert "does not show that error" not in out, out
    assert "the ENVIRONMENT's own value does not resolve" in out, out


_LEAK_PROBE = "ZZ_DOTENV_LEAK_PROBE"


def test_a_dotenv_applied_by_a_test_leaks_no_environment(capsys, _isolated):
    """`_load_dotenv` writes `os.environ` directly, so the fixture must undo it.

    Paired with the test below (same file order): without the restore the key
    survives and flips later `doctor` provider rows — the reason
    `test_doctor.py::_keyless_llm_env` exists (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated, f"{_LEAK_PROBE}=1\n")
    _load_dotenv(str(_isolated))
    assert os.environ[_LEAK_PROBE] == "1"


def test_the_previous_test_leaked_nothing_into_the_environment(_isolated):
    """The other half of the pair — runs immediately after the leaking test."""
    assert _LEAK_PROBE not in os.environ, (
        "a `.env` applied by one test leaked its key into the next one"
    )


def test_doctor_blames_the_pre_application_target_not_the_ladders():
    """The DOCUMENTED setup: resolution happens BEFORE the mid-run import fills.

    Measured before the fix, on the real CLI: no `TORTOISE_DB_URI` in the shell
    and a `.env` that sets one. Step 1 resolved the embedded default, §5.5's
    import then applied the file, and the row said "both read the SAME
    environment now, so the difference comes from the two LADDERS ranking its
    keys differently" — while both ladders resolved the file's target. It named
    a ladder bug that does not exist, and never said "re-run" (#12428 review).

    Pinned as a RENDERER test on purpose: inside pytest the daemon loader's own
    guard skips the load (`_load_dotenv` is inert under the suite), so the
    mid-run-application state cannot be produced end-to-end here — the branch
    is the artifact, and this is its input.
    """
    from tortoise.__main__ import _dotenv_rows

    payload = {
        "path": "/repo/.env",
        "missing": False,
        "differs": True,
        "read_by_this_command": True,
        "applied_when_graded": False,
        "graded_explicit": False,
        "graded_unresolved": False,
        "cli_error": None,
        "daemon_rung": "uri",
        "daemon_target": "/tmp/from-file.db",
        "cli_target": "/tmp/embedded-default.db",
        "difference_is_masked": None,
        "keys": ["TORTOISE_DB_URI"],
        "file_graph_keys": ["TORTOISE_DB_URI"],
        "graph_keys": [],
        "shadowed_graph_keys": [],
        "keys_already_set_in_the_environment": [],
        "unread_env_files": [],
    }
    rows = _dotenv_rows(payload)
    row = " ".join(str(detail) for _check, _status, detail in rows)

    assert "two LADDERS ranking its keys differently" not in row, row
    assert "resolved the store above BEFORE that import applied the file" in row, row
    assert "re-running grades the same store" in row, row
    assert "set -a; . ./.env; set +a" in row, row


def test_a_provider_only_file_is_not_credited_with_the_difference(capsys, _isolated,
                                                                 monkeypatch):
    """No graph key in the file means the file cannot cause the divergence.

    Measured before the fix: a `.env` holding only `DEEPSEEK_API_KEY`, with the
    ENVIRONMENT setting both `TORTOISE_DB_URI` and `TORTOISE_DB_PATH`, printed
    "the daemon loads that file at import, so it serves a DIFFERENT graph … this
    command resolved from the environment only" — the file was irrelevant
    (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "/tmp/daemon-side.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/cli-side.db")
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    out = _show_text(capsys)

    assert "this file sets no graph key, so it does not cause this" in out, out
    assert "the daemon loads that file at import" not in out, out


def test_an_applied_file_is_credited_with_the_key_it_filled(capsys, _isolated):
    """Applied + shadowed: the environment carries the key BECAUSE of the file.

    Measured before the fix: `config show` printed "the environment already sets
    TORTOISE_DB_URI … the file cannot change the target here" in the same breath
    as "its values ARE in this process now" — the target above WAS the file's
    value (#12428 review).
    """
    from tortoise.mcp_server import _load_dotenv

    _write_env(_isolated, "TORTOISE_DB_URI=/tmp/from-file.db\n")
    _load_dotenv(str(_isolated))
    assert os.environ["TORTOISE_DB_URI"] == "/tmp/from-file.db"

    out = _show_text(capsys)

    assert "applied this file, so the environment now sets TORTOISE_DB_URI" in out, out
    assert "the file cannot change the target here" not in out, out


def test_the_only_reader_claim_is_scoped_to_the_graph_target():
    """"Exactly one entry point read that file" is false without the scope.

    `tortoise/s2_decision_seam.py` reads the repo-root `.env` for a provider
    credential with its own parser, so the absolute claim was falsified by this
    repo's own code (#12428 review). The scope is the graph target, and the seam
    is named.
    """
    from tortoise import dotenv_file

    doc = " ".join((dotenv_file.__doc__ or "").split())
    assert "For the GRAPH TARGET, exactly one entry point read that file" in doc, doc
    assert "s2_decision_seam" in doc, doc


def _rows_text(payload):
    from tortoise.__main__ import _dotenv_rows

    base = {
        "path": "/repo/.env",
        "missing": False,
        "differs": True,
        "read_by_this_command": True,
        "applied_when_graded": False,
        "graded_explicit": False,
        "graded_unresolved": False,
        "cli_error": None,
        "daemon_rung": "uri",
        "daemon_target": "/tmp/from-file.db",
        "cli_target": "/tmp/embedded-default.db",
        "difference_is_masked": None,
        "keys": ["TORTOISE_DB_URI"],
        "file_graph_keys": ["TORTOISE_DB_URI"],
        "graph_keys": [],
        "shadowed_graph_keys": [],
        "keys_already_set_in_the_environment": [],
        "unread_env_files": [],
    }
    base.update(payload)
    rows = _dotenv_rows(base)
    return " ".join(str(detail) for _check, _status, detail in rows)


def test_the_pre_application_arm_promises_no_rerun_that_cannot_happen():
    """Every run resolves at the same Step 1, BEFORE the import fills the file.

    Measured on two consecutive real `doctor` runs: both graded the pre-fill
    embedded default, and both said "a re-run grades the daemon's — the two
    ladders agree on this box". A re-run reproduces the store above, because
    resolution precedes the import in every run — so the promise sent the user
    back to a command that cannot answer, which is the one thing a diagnostic
    must not do (#12428 review).
    """
    row = _rows_text({})

    assert "a re-run grades the daemon's" not in row, row
    assert "two ladders agree on this box" not in row, row
    assert "re-running grades the same store" in row, row
    # …and the remedy is one that actually changes the resolution.
    assert "set -a; . ./.env; set +a" in row, row
    assert "point `--db`/`--path` at its target above" in row, row


def test_the_unresolved_arm_does_not_credit_a_provider_only_file(capsys, _isolated):
    """A file with no graph key cannot "set the daemon's store".

    Measured before the fix: a provider-only `.env` plus `doctor --db relative/x`
    printed "…sets the daemon's store … the daemon selects no store either —
    `tortoise serve` REFUSES to start", a row contradicting itself (#12428
    review).
    """
    _write_env(_isolated, "DEEPSEEK_API_KEY=sk-not-a-real-key\n")

    _run(("doctor", "--db", "relative/probe.db"))
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "Config: .env" in line)

    assert "sets the daemon's store" not in row, row
    assert "sets no graph key" in row, row


def test_a_blank_environment_value_does_not_decide_the_daemon_target(capsys, _isolated,
                                                                    monkeypatch):
    """An exported `=""` is PRESENT but the daemon's ladder skips it.

    Measured: `TORTOISE_DB_URI=""` + `.env` `TORTOISE_DB_PATH=/x.db` printed
    "the ENVIRONMENT's TORTOISE_DB_URI decides it for the daemon, so the file's
    TORTOISE_DB_PATH cannot change the target" — while the daemon's target WAS
    that DB_PATH (`daemon_db_target` reads `if uri:`). `dotenv_file`'s own
    docstring makes "explicitly set — even to the empty string" a real state, so
    presence alone is the wrong test for "this key decides" (#12428 review).
    """
    from tortoise.config import DEFAULT_DB_PATH

    monkeypatch.setenv("TORTOISE_DB_URI", "")
    _write_env(_isolated, f"TORTOISE_DB_PATH={DEFAULT_DB_PATH}\n")

    out = _show_text(capsys)

    assert "decides it for the daemon" not in out, out
    assert "cannot change the target" not in out, out
    assert "the file's TORTOISE_DB_PATH" in out, out


def test_a_broken_target_from_the_FILE_is_not_called_an_environment_error(
        _isolated, monkeypatch):
    """The pre-existence probe must not read the file's own applied value.

    Reproduced on the real CLI (the documented setup, empty shell, `.env` =
    `TORTOISE_DB_URI=relative/x.db`): `doctor` imports the daemon module
    mid-run, so the file is already applied when `_dotenv_divergence` runs — the
    probe then saw the file's own broken value, concluded the ENVIRONMENT was
    broken, and printed "this `.env` is disclosed, but the DAEMON's target is
    unresolvable from the environment itself … this file did not cause it and
    the rows below fail the same way". Three false claims: the file caused it,
    and the rows below graded a store normally (#12428 review).
    """
    from tortoise import dotenv_file
    from tortoise.__main__ import _dotenv_divergence, _dotenv_rows

    path = _write_env(_isolated, "TORTOISE_DB_URI=relative/x.db\n")
    # The applied state: the key IS in this process, recorded as loaded — and the
    # RECORD says this process took that value from the FILE (#12428 review).
    monkeypatch.setenv("TORTOISE_DB_URI", "relative/x.db")
    monkeypatch.setattr(dotenv_file, "LOADED_PATHS", {str(path)}, raising=False)
    monkeypatch.setattr(
        dotenv_file, "APPLIED",
        {"TORTOISE_DB_URI": (str(path.resolve()), "relative/x.db")},
        raising=False)

    payload = _dotenv_divergence(graded_target="/tmp/graded-by-the-rows.db")
    assert payload["read_by_this_command"] is True, payload
    assert payload.get("daemon_error_source") == "file", payload
    assert "supplied" in str(payload.get("daemon_error")), payload

    row = " ".join(str(detail) for _c, _s, detail in _dotenv_rows(payload))
    assert "this file did not cause it" not in row, row
    assert "the rows below fail the same way" not in row, row


def test_a_client_error_never_quotes_the_password_prefix():
    """A parser that cuts at `#` quotes only the credential's PREFIX.

    `docker://:Abc#rest@h:6379/g` makes the port `Abc`, so the client's own
    message says "Port could not be cast to integer value as 'Abc'" — the head of
    the password. Scrubbing with the full target alone missed it (measured,
    #12428 review): `_scrub` derives `Abc#rest` from the value, not `Abc`.
    """
    from tortoise.__main__ import _scrub_uri_error

    cases = [
        ("docker://:Abc#rest@h:6379/g", "Abc"),      # empty user part, `#` cut
        ("docker://u:S3cretPw?x@h/g", "S3cretPw"),   # USER part, `?` cut
        ("docker://u:S3cretPw#x@h/g", "S3cretPw"),   # USER part, `#` cut
        ("docker://u:p%2Fw?x@h/g", "p/w"),           # percent-encoded, decoded echo
    ]
    for uri, leak in cases:
        message = f"bad port in URI '{uri}': Port could not be cast to integer value as '{leak}'"
        scrubbed = _scrub_uri_error(message, uri)
        assert leak not in scrubbed, (uri, scrubbed)


def test_doctor_health_row_never_echoes_a_password_head(capsys, _isolated, monkeypatch):
    """BOTH `Graph:` rows must agree about a credential — the second one leaked it.

    Measured (#12428 review): with a `.env` value EXPORTED into the shell — the
    remedy this PR prints (`set -a; . ./.env; set +a`) — for a password carrying
    a `#`, `doctor` printed the FalkorDB row redacted and then
    "Graph: health: Port could not be cast to integer value as 'Sup3rSecret'":
    the password's head, from the outer exception arm, one row below a row that
    had already hidden it.
    """
    uri = "docker://:Sup3rSecret#x@localhost:6379/g"
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    _write_env(_isolated, f"TORTOISE_DB_URI={uri}\n")

    _run(("doctor",))
    out = capsys.readouterr().out

    assert "Sup3rSecret" not in out, "a `.env` credential reached the doctor output"
    health = [line for line in out.splitlines() if "Graph: health" in line]
    assert health, out
    assert "<redacted>" in health[0], health[0]


def test_a_value_recorded_from_ANOTHER_file_is_not_credited_to_this_one(
        capsys, _isolated, monkeypatch):
    """`APPLIED` is a record of what THIS process applied, scoped to the file reported.

    Measured (#12428 review): the record was value-only and process-global, so a
    value an EARLIER test (or an earlier `.env` in the same process) applied was
    read as this file's — `config show` then credited the file with a value the
    USER's environment supplied, in the state where running alone it correctly
    says "this file did not supply it".
    """
    from tortoise import dotenv_file

    other = _isolated.parent / "other.env"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("TORTOISE_DB_URI=relative/x.db\n")
    monkeypatch.setattr(
        dotenv_file, "APPLIED",
        {"TORTOISE_DB_URI": (str(other.resolve()), "relative/x.db")},
        raising=False)
    monkeypatch.setenv("TORTOISE_DB_URI", "relative/x.db")
    _write_env(_isolated, "TORTOISE_DB_URI=relative/x.db\n")

    out = _show_text(capsys)

    assert "this `.env` supplied" not in out, out
    assert "file did not supply it" in out, out


def test_a_file_that_was_never_applied_does_not_claim_it_was():
    """The non-applied arm must not say "this process applied that file".

    Measured on the documented setup with a file-broken URI and an empty shell:
    the row printed "this process applied that file at import as well, so the
    rows below resolved from the same values and graded the store above" — the
    file was applied only INSIDE the diagnostic's own overlay, and the rows
    graded the canonical embedded default (#12428 review).
    """
    row = _rows_text({"read_by_this_command": False,
                      "differs": False,
                      "cli_target": "/tmp/graded-by-the-rows.db",
                      "daemon_target": None,
                      "daemon_error": "ValueError: Relative DB path 'relative/x.db' rejected."})
    # Neither claim about the rows below survives: the row states WHO supplied the
    # rejected value and nothing it cannot verify (#12428 review).
    assert "applied that file at import as well" not in row, row
    assert "rows below" not in row, row


def test_a_file_applied_after_grading_does_not_blame_the_rows():
    """Applied mid-run, but AFTER the store above was resolved: the rows are fine.

    Same shape as the documented setup, except the file was applied during the
    run. "the rows below resolved from the same values" is false there — they
    resolved BEFORE the import (#12428 review).
    """
    row = _rows_text({"read_by_this_command": True,
                      "applied_when_graded": False,
                      "daemon_target": None,
                      "daemon_error": "ValueError: Relative DB path 'relative/x.db' rejected."})
    assert "AFTER resolving the store above" not in row, row
    assert "resolved from the same values" not in row, row
    assert "rows below" not in row, row


def test_a_shadowed_shell_value_is_still_the_environment_s(_isolated, monkeypatch):
    """The probe removes a key only when the FILE's value is the one in effect.

    Popping by NAME also removed a shell value the file merely shadows, so the
    probe's answer described an environment that does not exist and `config show`
    blamed the file for an error the environment owned (#12428 review).
    """
    from tortoise import dotenv_file
    from tortoise.__main__ import _dotenv_divergence

    _write_env(_isolated, "TORTOISE_DB_URI=/tmp/ok.db\n")
    monkeypatch.setenv("TORTOISE_DB_URI", "relative/broken.db")   # the shell's own
    monkeypatch.setattr(dotenv_file, "LOADED_PATHS", set(), raising=False)
    monkeypatch.setattr(dotenv_file, "APPLIED", {}, raising=False)

    payload = _dotenv_divergence(graded_target="/tmp/graded.db")

    # The shell value is broken and the file's is not: the ENVIRONMENT owns it.
    assert payload.get("daemon_error_source") == "env", payload


def test_a_shell_value_identical_to_the_files_is_not_credited_to_it(
        capsys, _isolated, monkeypatch):
    """The shell exporting the file's OWN broken value does not make it the file's.

    Measured (#12428 review): env `TORTOISE_DB_URI=relative/x.db` and a `.env`
    declaring the same value printed "the value is the one this `.env` supplied"
    — the file supplied nothing (`pending_assignments` was empty, the key was
    already present) — and then "the target above does not show that error"
    three lines under a `Graph store` row showing that exact error.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "relative/x.db")
    _write_env(_isolated, "TORTOISE_DB_URI=relative/x.db\n")

    out = _show_text(capsys)

    assert "this `.env` supplied" not in out, out
    assert "does not RESOLVE with this file" not in out, out
    assert "file did not supply it" in out, out
    # The target above DID fail, so the row must say so rather than deny it.
    assert "does not show that error" not in out, out
    assert "see `Graph store` above" in out, out


def test_two_broken_values_are_reported_as_both(capsys, _isolated, monkeypatch):
    """The probe popps the FILE's values, so its failure is a DIFFERENT key.

    Measured (#12428 review): env `TORTOISE_DB_PATH=relative/y.db` with `.env`
    `TORTOISE_DB_URI=relative/x.db` printed the FILE's error and then claimed
    "the ENVIRONMENT's own value does not resolve — this file did not cause it":
    both are broken, and the file is one of the causes.
    """
    monkeypatch.setenv("TORTOISE_DB_PATH", "relative/y.db")
    _write_env(_isolated, "TORTOISE_DB_URI=relative/x.db\n")

    out = _show_text(capsys)

    assert "ENVIRONMENT's own value does not resolve either" in out, out
    assert "this file did not cause it" not in out, out
    assert "does not RESOLVE with this file" in out, out


def test_a_described_value_does_not_keep_a_delimited_credential():
    """`head + "…"` kept a credential that sat before the `#`/`?`.

    Measured on a `.env` holding `TORTOISE_DB_URI=bolt://uu:PwABCD#x@h/db`:
    `config show` printed `Relative DB path 'bolt://uu:PwABCD…' rejected` — the
    password's head, in the value the message exists to name (#12428 review).
    """
    from tortoise.__main__ import _redact_db_value

    value = "bolt://uu:PwABCD#x@h/db"
    out = _redact_db_value(f"Relative DB path '{value}' rejected", value)

    assert "PwABCD" not in out, out
    assert "bolt://uu:" in out, out


def test_the_graph_health_row_keeps_a_path_and_cuts_a_credential():
    """Two pins at once: a plain path stays legible, a credential fragment does not.

    `Graph: health` quotes the resolver's own message, so it must use the
    fragment redactor — `_scrub` would redact `relative.db` (two pinned
    `test_doctor` tests enforce the other half) (#12428 review).
    """
    from tortoise.__main__ import _redact_credential_fragments

    plain = "Relative DB path 'relative.db' rejected"
    assert _redact_credential_fragments(plain, "relative.db") == plain

    leaked = "Port could not be cast to integer value as 'PwABCD'"
    assert "PwABCD" not in _redact_credential_fragments(
        leaked, "docker://:PwABCD#x@h:6379/g")


def test_a_differing_shell_value_is_not_credited_to_the_file():
    """A shell value that DIFFERS is not "the one it selects".

    Measured: `.env` `/tmp/from-file.db` with the shell exporting
    `/tmp/from-shell.db` printed "the environment now sets TORTOISE_DB_URI — the
    target the rows below grade (/tmp/from-shell.db) is the one it selects". The
    file never took effect (#12428 review).
    """
    row = _rows_text({"read_by_this_command": True,
                      "shadowed_graph_keys": ["TORTOISE_DB_URI"],
                      "keys_in_effect": [],
                      "file_graph_keys": ["TORTOISE_DB_URI"],
                      "graph_keys": [],
                      "differs": False,
                      "cli_target": "/tmp/from-shell.db",
                      "daemon_target": "/tmp/from-shell.db"})
    assert "is the one it selects" not in row, row
    assert "the environment already sets TORTOISE_DB_URI" in row, row


def test_a_blank_exported_key_is_named_as_the_blocker(capsys, _isolated, monkeypatch):
    """The file sets a graph key and a BLANK export blocks it — say which.

    Measured: `.env` = `TORTOISE_DB_URI=docker://…` with `TORTOISE_DB_URI=""`
    exported printed "neither TORTOISE_DB_URI nor TORTOISE_DB_PATH is set" — the
    file sets one, and the blocker (the daemon's ladder skips a blank value) was
    named nowhere (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    _write_env(_isolated, "TORTOISE_DB_URI=/tmp/from-file.db\n")

    out = _show_text(capsys)

    assert "is exported EMPTY" in out, out
    assert "neither TORTOISE_DB_URI nor" not in out, out


def test_the_applied_note_does_not_claim_values_that_did_not_survive():
    """A loader that ran over a shadowing shell wrote nothing that is in effect.

    `keys_in_effect` is empty when the shell's value differs, and the row then
    said "its values ARE in this process now" — about values that are not
    (#12428 review).
    """
    row = _rows_text({"read_by_this_command": True,
                      "keys_in_effect": [],
                      "shadowed_graph_keys": ["TORTOISE_DB_URI"],
                      "file_graph_keys": ["TORTOISE_DB_URI"],
                      "graph_keys": [],
                      "differs": False,
                      "cli_target": "/tmp/from-shell.db",
                      "daemon_target": "/tmp/from-shell.db"})

    assert "its values ARE in this process now" not in row, row
    assert "no key it wrote survived in this process" in row, row


def test_both_graph_health_arms_scrub_their_exception_text():
    """No `Graph: health` arm may render an exception raw.

    The SECOND arm (the one the SDK/client reaches) printed a client exception
    verbatim — measured with `TORTOISE_DB_URI=docker://:PwABCD#x@h:6379/g`:
    `❌ Graph: health: Port could not be cast to integer value as 'PwABCD'`. The
    sibling probe arms were already scrubbed; this one was missed (#12428
    review). Pinned structurally because forcing that block to raise would need
    the SDK's projection teardown mocked; the behavioural half — that the
    composition removes the fragment and keeps a plain path — is pinned by the
    two tests above.
    """
    import inspect

    from tortoise import __main__ as m

    flat = " ".join(inspect.getsource(m._cmd_doctor).split())

    assert 'results.append(("Graph: health", "❌", str(e)[:60]))' not in flat, flat
    assert flat.count('results.append(("Graph: health", "❌", _mask_uri_userinfo(') == 2, flat


def test_a_plain_path_survives_the_graph_health_arm():
    """The arm must cut the credential WITHOUT wiping a path (or `:memory:`).

    Composing `_scrub_uri_error` there turned `Relative DB path 'relative.db'
    rejected` into `Relative DB path '<redacted>' rejected` — `_scrub` treats the
    whole target as secret, the hazard the resolver arm above documents and two
    `test_doctor` tests enforce (#12428 review).
    """
    from tortoise.__main__ import _mask_uri_userinfo, _redact_credential_fragments

    # `relative.db` is the shape two `test_doctor` tests assert on; `:memory:` is
    # NOT included: `_mask_uri_userinfo` mangles it to `:<redacted>` (a
    # pre-existing behaviour of that masker, unrelated to this arm).
    target, message = "relative.db", "Relative DB path 'relative.db' rejected"
    out = _mask_uri_userinfo(_redact_credential_fragments(message, target))
    assert message in out, out


def test_config_show_keeps_a_rejected_path_legible(capsys, _isolated, monkeypatch):
    """`config show`'s OWN refusal arm must cut the credential, not the path.

    Measured with no `.env` and `TORTOISE_DB_URI=relative.db`:
    `⛔ configured but unusable: ValueError: Relative DB path '<redacted>'
    rejected` — and `--json` carried the same. `relative.db` appears nowhere else
    in that command, so the one sentence that names the rejected value no longer
    names it, while `doctor` printed it correctly (#12428 review).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "relative.db")

    payload = _show_json(capsys)

    error = payload["graph_store"]["error"]
    assert "relative.db" in error, error
    assert "<redacted>" not in error, error


def test_config_show_cuts_a_credential_in_the_rejected_path(capsys, _isolated,
                                                            monkeypatch):
    """The other half: a credential-shaped rejected value is still cut."""
    monkeypatch.setenv("TORTOISE_DB_URI", "alice:Pw1@h/d.db")

    payload = _show_json(capsys)

    error = payload["graph_store"]["error"]
    assert "Pw1" not in error, error
    assert "<redacted: userinfo-shaped>" in error, error
