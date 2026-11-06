"""`tortoise config show` — offline credential + coordinate reporting (#10655).

The verb exists because NOTHING reported which of the four credential stores won,
what org/graph the winner claims, or that the Pi harness reads a different file
from the CLI. These tests pin the three properties that make it worth having:
it names the WINNER (following the resolver, never re-deriving it), it never
prints a secret, and it answers on a box where the network does not.

They also pin the states that MADE someone run the verb — a corrupt store, a
store the resolver refuses, no credential at all — because a diagnostic that
crashes on the condition it diagnoses is the defect, not the fix.
"""
from __future__ import annotations

import json

import pytest

import tortoise.__main__ as main

GLOBAL_KEY = "tt_globalkey0000000000000000000000"
HARNESS_KEY = "tt_harnesskey000000000000000000000"
SHARED_KEY = "tt_sharedkey0000000000000000000000"
GLOBAL_FINGERPRINT = "147540280030"          # sha256(GLOBAL_KEY)[:12], computed once
HARNESS_FINGERPRINT = "17a0349d261d"
SHARED_FINGERPRINT = "ad13765e183f"

GLOBAL = json.dumps({
    "api_key": GLOBAL_KEY,
    "api_url": "https://api.premiselabs.co",
    "org_id": "00e8e36129794bcdb2c992ff47",
    "org_name": "agent-00e8e3",
    "signup_token": "st_" + "a" * 64,
})

HARNESS = json.dumps({
    "apiKey": HARNESS_KEY,
    "apiUrl": "https://api.premiselabs.co",
    "cloud": True,
    "autoCapture": True,
})


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Never read the developer's real stores; never resolve a stray ./.tortoise."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("TORTOISE_API_KEY", "TORTOISE_API_URL", "TORTOISE_DB_URI",
                "TORTOISE_DB_PATH", "FALKORDB_HOST", "FALKORDB_PORT",
                "FALKORDB_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


def _global(tmp_path):
    return _write(tmp_path / ".tortoise" / "credentials.json", GLOBAL)


def _harness(tmp_path, text=HARNESS):
    return _write(tmp_path / ".pi" / "agent" / "tortoise-config.json", text)


def _run(argv=("config", "show")):
    return main.main(list(argv))


def _bounded(fn, seconds=20):
    """Run `fn` on a daemon thread and FAIL if it has not returned in `seconds`.

    A credential read that BLOCKS (`read_text()` on a FIFO/socket blocks in
    `open()` and never raises — no `except` fires) would otherwise hang the
    whole suite: CI reports a job timeout instead of the defect, and the test
    that names the hazard cannot say it regressed. `join(seconds)` turns the
    hang into an assertion (#10655 review F1).
    """
    import threading

    box: dict[str, object] = {}

    def _target():
        try:
            box["value"] = fn()
        except BaseException as exc:          # re-raised on the main thread
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise AssertionError(
            f"the call did not return within {seconds}s — a store read blocked "
            "on a non-regular file (`read_text()` on a FIFO blocks in `open()` "
            "and never raises, so no guard fires)")
    if "error" in box:
        raise box["error"]                   # type: ignore[misc]
    return box["value"]


def _run_bounded(argv, seconds=20):
    return _bounded(lambda: main.main(list(argv)), seconds)


def payload_of(cap):
    return json.loads(cap.out)


def _rows(out):
    """The ladder rows of the human report → [(label, state, marker)]."""
    return [ln for ln in out.splitlines() if ln.startswith("  ") and "←" in ln]


# ── the winner is named, and it is the resolver's answer ──────────────────


def test_global_store_is_reported_as_the_source(capsys, tmp_path):
    _global(tmp_path)
    assert _run() == 0
    out = capsys.readouterr().out
    assert str(tmp_path / ".tortoise" / "credentials.json") in out
    assert "←  USED" in out
    assert "00e8e36129794bcdb2c992ff47" in out          # org_id — the coordinate
    assert "agent-00e8e3" in out                         # org_name


def test_exactly_one_row_is_marked_used(capsys, tmp_path):
    """The marker must be discriminating: a report that marks everything (or
    nothing) used is a report nobody can act on."""
    _global(tmp_path)
    _harness(tmp_path)
    assert _run() == 0
    used = [ln for ln in _rows(capsys.readouterr().out) if "USED" in ln]
    assert len(used) == 1
    assert str(tmp_path / ".tortoise" / "credentials.json") in used[0]


def test_env_wins_and_the_ladder_says_so(monkeypatch, capsys, tmp_path):
    """An env key beats both files. The report must follow that, and must not
    claim coordinates it does not have — an env key carries none."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey0000000000000000000000000")
    assert _run() == 0
    out = capsys.readouterr().out
    env_row = next(ln for ln in out.splitlines() if "TORTOISE_API_KEY (env)" in ln)
    assert "←  USED" in env_row
    assert "agent-00e8e3" not in out                     # the file's org is NOT used
    assert "org_id        (absent)" in out


def test_cwd_store_is_named_when_it_shadows_the_global_one(monkeypatch, capsys, tmp_path):
    """`init --api-key` writes <cwd>/.tortoise, which OUTRANKS the global store.
    Making that shadow visible is the whole point of the report."""
    _global(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _write(repo / ".tortoise", json.dumps({
        "api_key": "tt_cwdkey000000000000000000000000",
        "api_url": "https://api.premiselabs.co",
    }))
    assert _run() == 0
    out = capsys.readouterr().out
    cwd_row = next(ln for ln in out.splitlines() if str(repo / ".tortoise") in ln)
    assert "←  USED" in cwd_row


def test_a_cwd_store_without_org_id_says_where_to_get_one(
        monkeypatch, capsys, tmp_path):
    """`init --api-key` writes ONLY api_key/api_url, so the coordinate section —
    the reason this verb exists — would be silently blank on the documented
    onboarding path. Ask the question that path leaves open."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / ".tortoise", json.dumps({
        "api_key": "tt_cwdkey000000000000000000000000",
        "api_url": "https://api.premiselabs.co",
    }))
    monkeypatch.chdir(repo)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "org_id        (absent)" in out
    assert "tortoise team info" in out                   # the next command is named


# ── secrets never reach the output ────────────────────────────────────────


def test_neither_the_key_nor_the_recovery_token_is_printed(capsys, tmp_path):
    _global(tmp_path)
    _harness(tmp_path)
    assert _run() == 0
    cap = capsys.readouterr()
    both = cap.out + cap.err                              # stderr counts: this verb writes to it
    assert GLOBAL_KEY not in both
    assert "globalkey0000" not in both                    # the body never appears
    assert HARNESS_KEY not in both
    assert "harnesskey000" not in both
    assert "st_" + "a" * 64 not in both                   # the recovery token never appears
    assert "signup_token present" in cap.out


def test_json_output_carries_a_pinned_fingerprint_not_the_key(capsys, tmp_path):
    """The digest is pinned LITERALLY: an oracle computed by the same function
    under test would pass even if it hashed the wrong material."""
    _global(tmp_path)
    assert _run(("config", "show", "--json")) == 0
    out = capsys.readouterr().out
    cred = json.loads(out)["credential"]
    assert cred["org_id"] == "00e8e36129794bcdb2c992ff47"
    assert cred["api_key_fingerprint"] == GLOBAL_FINGERPRINT
    assert cred["api_url"] == "https://api.premiselabs.co"     # pinned, not just "masked"
    # negative guards: both key-condition fields must be ABSENT for a clean key —
    # an implementation that set them unconditionally would pass every other test
    assert "api_key_unsendable" not in cred
    assert "api_key_whitespace" not in cred
    assert "globalkey0000" not in out


def test_the_db_uri_password_is_masked(monkeypatch, capsys, tmp_path):
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:sup3rs3cret@localhost:16379/tortoise")
    assert _run(("config", "show", "--json")) == 0
    cap = capsys.readouterr()
    assert "sup3rs3cret" not in json.dumps(payload_of(cap))
    assert "16379" in json.dumps(payload_of(cap))
    assert "sup3rs3cret" not in cap.out + cap.err


def test_a_url_with_userinfo_is_masked(capsys, tmp_path):
    """A credential in a URL is a credential: the DB URI is masked two branches
    up from here, so api_url must be too."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY,
        "api_url": "https://user:hunter2@api.premiselabs.co",
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    assert _run() == 0
    cap = capsys.readouterr()
    assert "hunter2" not in cap.out + cap.err


# ── the split brain is stated, not hidden ────────────────────────────────


def test_harness_store_is_reported_side_by_side_and_differing_keys_are_called_out(
        capsys, tmp_path):
    _global(tmp_path)
    _harness(tmp_path)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "tortoise-config.json" in out
    assert "a DIFFERENT key from the CLI" in out
    assert HARNESS_FINGERPRINT in out
    assert GLOBAL_FINGERPRINT in out


def test_the_same_key_in_both_stores_is_reported_as_one_identity(capsys, tmp_path):
    """The negative half of the split-brain verdict. A comparison hardwired to
    'different' would pass every other test in this file."""
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiKey": SHARED_KEY,
                                  "apiUrl": "https://api.premiselabs.co"}))
    assert _run() == 0
    out = capsys.readouterr().out
    assert "a DIFFERENT key" in out              # different keys …
    _write(tmp_path / ".pi" / "agent" / "tortoise-config.json",
           json.dumps({"apiKey": GLOBAL_KEY, "apiUrl": "https://api.premiselabs.co"}))
    assert _run() == 0
    out = capsys.readouterr().out
    assert "same identity as the CLI" in out     # … and the same key is reported as one
    assert "a DIFFERENT key" not in out
    assert SHARED_FINGERPRINT not in out


def test_an_env_key_makes_the_harness_the_same_identity_despite_a_stale_file(
        monkeypatch, capsys, tmp_path):
    """The harness resolves `apiKey = envKey || fileKey`. Comparing the two FILE
    keys would invent a split brain in the commonest fleet state: a stale
    tortoise-config.json beside a live env key."""
    _global(tmp_path)
    _harness(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["harness"]["source"] == "env:TORTOISE_API_KEY"
    assert payload["identity_agrees"] is True
    assert payload["credential"]["api_key_fingerprint"] == GLOBAL_FINGERPRINT


def test_no_cli_identity_is_not_reported_as_a_mismatch(capsys, tmp_path):
    """Nothing to compare must not read as 'two identities'."""
    _harness(tmp_path)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "the CLI has no identity" in out
    assert "a DIFFERENT key" not in out


def test_a_missing_harness_store_is_reported_as_absent(capsys, tmp_path):
    _global(tmp_path)
    assert _run() == 0
    assert "state         absent" in capsys.readouterr().out


# ── the states that made a user run the verb must not crash it ────────────


def test_no_credential_at_all_still_answers_with_the_next_command(capsys, tmp_path):
    assert _run() == 0
    out = capsys.readouterr().out
    assert "Identity: none resolved." in out
    assert "tortoise signup" in out


def test_a_corrupt_store_is_REFUSED_reported_and_non_zero(capsys, tmp_path):
    """`_ConfigError` is the same failure every other verb turns into exit 1 via
    `_read_config`. A diagnostic verb may be informative and non-zero, but a
    script must not read success off a store no command can open."""
    (tmp_path / ".tortoise").mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text("{not json")
    assert _run(("config", "show", "--json")) == 1
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    # `_cmd_fail`'s documented machine contract: status/error/message, and the
    # message ALSO on stderr in both modes.
    assert payload["status"] == "error"
    assert payload["error"] == "no_config"
    assert payload["config_state"] == "refused"
    assert payload["message"]
    assert payload["message"] in cap.err
    assert payload["credential"]["blocked_by"]


def test_the_corrupt_store_is_marked_BLOCKS_never_USED(capsys, tmp_path):
    """A `←  USED` mark beside "Identity: none resolved" is a self-contradicting
    report; the refused path must never be presented as the winner."""
    (tmp_path / ".tortoise").mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text("{not json")
    assert _run() == 1
    out = capsys.readouterr().out
    assert "←  USED" not in out
    assert "←  BLOCKS" in out
    assert "REFUSED" in out


def test_a_refusing_store_that_does_NOT_block_is_not_marked_BLOCKS(
        monkeypatch, capsys, tmp_path):
    """An env key short-circuits the resolver, so a corrupt `./.tortoise` beside
    it blocks nothing. Marking it BLOCKS would assert a failure the very next
    line disproves."""
    (tmp_path / ".tortoise").mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text("{not json")
    monkeypatch.setenv("TORTOISE_API_KEY", SHARED_KEY)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "←  BLOCKS" not in out
    assert "←  USED" in out
    assert "corrupt" in out                     # the state is still reported


def test_an_unreadable_store_is_reported_not_a_traceback(capsys, tmp_path):
    """A mode-000 store raises PermissionError out of `Path.is_dir()` — NOT a
    `_ConfigError`. The diagnostic verb must survive the state it diagnoses."""
    d = tmp_path / ".tortoise"
    d.mkdir()
    (d / "credentials.json").write_text(GLOBAL)
    d.chmod(0o000)
    try:
        assert _run(("config", "show", "--json")) == 1
        cap = capsys.readouterr()
        payload = json.loads(cap.out)
        assert payload["status"] == "error"
        assert payload["credential"]["blocked_by"]
        assert "PermissionError" in payload["message"] + payload["credential"].get(
            "blocked_reason", "")
        # WHICH store: HOME == cwd here, so a hardcoded cwd blame coincides with
        # the truth — the assertion is what keeps that honest.
        assert payload["credential"]["blocked_by"] == str(d / "credentials.json")
    finally:
        d.chmod(0o700)


def test_an_unreadable_GLOBAL_store_is_not_blamed_on_the_cwd(
        monkeypatch, capsys, tmp_path):
    """The offender must be derived from evidence. Hardcoding the cwd marked an
    ABSENT row BLOCKS and told the reader to delete a file that does not exist."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "credentials.json").write_text(GLOBAL)
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(repo)
    (home / ".tortoise").chmod(0o000)
    try:
        assert _run(("config", "show", "--json")) == 1
        cap = capsys.readouterr()
        payload = json.loads(cap.out)
        assert payload["credential"]["blocked_by"] == str(
            home / ".tortoise" / "credentials.json")
        blocked_rows = [r for r in payload["credential"]["ladder"] if r["blocks"]]
        assert len(blocked_rows) == 1
        assert blocked_rows[0]["state"] == "unreadable"
        assert _run() == 1
        human = capsys.readouterr().out
        assert "←  BLOCKS" in human
        assert "←  USED" not in human
        # the cwd row is absent: it must never carry the marker
        cwd_row = next(r for r in payload["credential"]["ladder"]
                       if r["source"] == str(repo / ".tortoise"))
        assert cwd_row["state"] == "absent"
        assert cwd_row["blocks"] is False
        assert "←  BLOCKS" not in cap.out
    finally:
        (home / ".tortoise").chmod(0o700)


def test_a_deleted_cwd_does_not_traceback(monkeypatch, capsys, tmp_path):
    """`Path.cwd()` raises FileNotFoundError — before the resolver even runs."""
    import os

    doomed = tmp_path / "doomed"
    doomed.mkdir()
    _global(tmp_path)
    monkeypatch.chdir(doomed)
    os.rmdir(doomed)
    assert _run(("config", "show", "--json")) == 1
    payload = json.loads(capsys.readouterr().out)
    # DEGRADED, not "refused": nothing was refused, the resolver could not run.
    assert payload["status"] == "degraded"
    assert "resolution_error" in payload
    assert "blocked_by" not in payload["credential"]
    assert "error" not in payload
    # and no store is marked, because no store is to blame
    assert [r for r in payload["credential"]["ladder"] if r["blocks"]] == []


def test_an_unresolvable_home_does_not_traceback(monkeypatch, capsys, tmp_path):
    """`Path.home()` raises RuntimeError for `HOME=~`/`~/x` — the same raise-set
    the deleted-cwd sibling is guarded for. The resolver calls it too."""
    monkeypatch.setenv("HOME", "~/x")
    rc = _run(("config", "show", "--json"))
    cap = capsys.readouterr()
    payload = json.loads(cap.out)
    assert rc == 1
    assert payload["status"] == "degraded"
    assert "resolution_error" in payload


def test_an_unusable_harness_store_is_named(monkeypatch, capsys, tmp_path):
    """The harness block must say which unusable shape it found, not fall silent."""
    _global(tmp_path)
    hp = tmp_path / ".pi" / "agent" / "tortoise-config.json"
    hp.parent.mkdir(parents=True)
    hp.mkdir()                                   # a DIRECTORY at the file path
    assert _run() == 0
    out = capsys.readouterr().out
    assert "directory sits at the harness config path" in out


def test_an_invalid_utf8_store_is_corrupt_not_a_traceback(capsys, tmp_path):
    """The resolver reads with `read_text()`, so invalid UTF-8 raises ValueError —
    a shape the JSON-only parametrisation does not cover."""
    (tmp_path / ".tortoise").mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_bytes(b"\xff\xfe{not json")
    assert _run(("config", "show", "--json")) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["credential"]["blocked_by"]
    # the label carries different advice (fix permissions vs delete a bad file)
    assert [r["state"] for r in payload["credential"]["ladder"] if r["blocks"]] == ["corrupt"]


def test_a_fifo_at_the_candidate_does_not_hang_or_read_it(
        monkeypatch, capsys, tmp_path):
    """`read_text()` on a FIFO BLOCKS forever; the resolver skips it (`is_file()`
    is False), so the report must say so instead of reading.

    The exported KEY is what makes this test capable of failing (#10655 review
    F1): the recovery-token read sits inside `if api_key:`, so with NO credential
    anywhere the pre-fix code never reached the unguarded read and this test
    passed on the very defect its name claims. With a key resolved, the resolver
    skips the FIFO, the global store wins, and the token read runs — which used
    to block the verb forever (bounded, so a regression FAILS here).
    """
    import os
    try:
        os.mkfifo(tmp_path / ".tortoise")
    except (AttributeError, OSError):
        pytest.skip("mkfifo unavailable on this platform")
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    assert _run_bounded(("config", "show", "--json")) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    cwd_row = payload["credential"]["ladder"][1]
    assert cwd_row["state"] == "not a regular file"
    # …and the recovery-token read the key unblocked skipped it too, instead of
    # blocking on it (the FIFO holds no token, so "absent" is the honest answer).
    assert payload["credential"]["recovery_token"] == "absent"


def test_no_store_reader_blocks_on_a_non_regular_candidate(tmp_path, monkeypatch):
    """One gate, three readers: `signup`, `recover` and `config show` each read a
    store candidate directly, and a FIFO at `<cwd>/.tortoise` hung every one of
    them. Pinned per reader because a gate remembered at two of three call sites
    is the same defect on the third (#10655 review F1)."""
    import os
    try:
        os.mkfifo(tmp_path / ".tortoise")
    except (AttributeError, OSError):
        pytest.skip("mkfifo unavailable on this platform")
    for fn in (main._read_stored_signup_token_with_source,
               main._read_stored_config_api_url,
               main._token_only_config_path):
        result = _bounded(fn)
        assert result in ((None, None), None), (
            f"{fn.__name__} reported {result!r} for a FIFO — it must SKIP a "
            "non-regular candidate (the resolver's own `is_file()` predicate)")


def test_a_broken_symlink_store_is_unreadable_not_absent(capsys, tmp_path):
    """A path that EXISTS and breaks resolution must not read as nonexistent."""
    target = tmp_path / "gone"
    (tmp_path / ".tortoise").symlink_to(target)
    # The resolver SKIPS it (`is_file()` is False), so the run is not refused —
    # but the state label must not claim the path does not exist, and a SKIPPED
    # path must never be presented as the refuser.
    assert _run() == 0
    out = capsys.readouterr().out
    assert "broken symlink" in out
    assert "BLOCKS" not in out


@pytest.mark.parametrize("body, expectation", [
    (json.dumps({"api_key": ""}), "empty-key"),
    (json.dumps({"signup_token": "st_" + "b" * 64}), "no-key"),
    (json.dumps({"api_key": None}), "no-key"),
    (json.dumps({}), "no-key"),
    (json.dumps({"api_key": 12345}), "invalid-key"),
    ("[1, 2, 3]", "corrupt"),
    ("{not json", "corrupt"),
])
def test_a_store_state_mirrors_the_resolvers_own_predicate(
        monkeypatch, tmp_path, body, expectation):
    """`empty-key` FALLS THROUGH to the next candidate; `invalid-key`/`corrupt`
    make the resolver raise. Collapsing them would tell a user "add a key here"
    about a file that already breaks every command."""
    path = tmp_path / "store"
    path.write_text(body)
    only = tmp_path / "only"
    only.mkdir()
    (only / ".tortoise").write_text(body)     # the resolver's cwd candidate
    state, _cfg = main._describe_store(path)
    assert state == expectation
    # …and the claim is coupled to the resolver, not just to itself: a state the
    # resolver REFUSES must actually raise, and `empty-key` must fall through.
    monkeypatch.chdir(only)
    if expectation == "empty-key":
        assert main._resolve_config_path()[2] is None          # skipped, not refused
    else:
        with pytest.raises(main._ConfigError):
            main._resolve_config_path()


def test_a_legacy_store_with_no_api_key_is_refused_not_called_empty(capsys, tmp_path):
    """#1756's token-only store, end to end: the report must show the refusal."""
    (tmp_path / ".tortoise").mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text(
        json.dumps({"signup_token": "st_" + "b" * 64}))
    assert _run() == 1
    out = capsys.readouterr().out
    assert "no-key" in out
    assert "REFUSED" in out


def test_a_directory_at_the_cwd_candidate_is_reported_as_skipped(capsys, tmp_path):
    """The resolver SKIPS a directory (D5). Calling an existing directory
    'absent' sends someone hunting for a file they are looking straight at."""
    (tmp_path / ".tortoise").mkdir()                     # a DIRECTORY, not a file
    _global(tmp_path)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "skipped (directory)" in out


def test_a_truncated_org_id_is_flagged_rather_than_passed_off_as_a_coordinate(
        capsys, tmp_path):
    """The measured state on the box that filed #10655: org_id `t4`, org_name `o`.
    A 2-char org id is a truncated write — say so, do not print it as truth."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": "tt_x" + "0" * 32,
        "api_url": "https://api.premiselabs.co",
        "org_id": "t4",
        "org_name": "o",
    }))
    assert _run() == 0
    assert "looks truncated" in capsys.readouterr().out


def test_a_well_formed_org_id_is_not_flagged(capsys, tmp_path):
    """Negative guard: the warning must be discriminating, not always-on."""
    _global(tmp_path)
    assert _run() == 0
    assert "looks truncated" not in capsys.readouterr().out


def test_bare_config_with_no_subcommand_prints_the_report(capsys, tmp_path):
    """A discovery verb must not be a dead end for the short form."""
    _global(tmp_path)
    assert _run(("config",)) == 0
    assert "Resolution order" in capsys.readouterr().out


# ── the record verbs' naming, derived from the resolved org ──────────────


def test_the_cache_graph_conventions_are_printed_for_the_resolved_org(capsys, tmp_path):
    _global(tmp_path)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "org_00e8e36129794bcdb2c992ff47" in out
    assert "org_00e8e36129794bcdb2c992ff47_<graph_id>" in out


@pytest.mark.parametrize("uri, kind, warned", [
    ("docker://:pw@localhost:16379/tortoise", "database URI", False),
    ("/var/lib/tortoise/graph.db", "embedded file path", False),
    ("/tmp/me@work/db.fdb", "embedded file path", False),  # a path, not userinfo
    # the RESOLVER decides: a RELATIVE path and a non-db scheme are both refused
    ("./local.db", "configured-unusable", False),
    ("bolt://localhost:7687", "configured-unusable", False),
])
def test_the_graph_store_classifies_the_target_instead_of_crying_wolf(
        monkeypatch, capsys, tmp_path, uri, kind, warned):
    """A bare path is a SUPPORTED embedded target (`doctor --db` accepts one), so
    warning on it is a false alarm — and one that was never rendered to a human."""
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    if kind == "configured-unusable":
        assert store["status"] == "configured but unusable"
        assert "target" not in store
        return
    assert store.get("target_kind") == kind
    assert bool(store.get("warning")) is warned
    if kind == "embedded file path":
        # a filesystem path has no userinfo, so the fail-closed `@` masker must
        # not redact the target the report exists to name
        assert store["target"] == uri


# ── `team info`'s refusals become remedies (#10655) ──────────────────────

GRAPH_SCOPED_BODY = json.dumps({"detail": {
    "error_code": "GRAPH_SCOPED_TEAM_SURFACE",
    "message": "Graph-scoped keys cannot access team overview."}})


def test_a_403_from_team_info_is_explained_end_to_end(monkeypatch, capsys, tmp_path):
    """Pinned through `team info` itself, not by calling the helper — the WIRING
    is what changed, and a helper test alone cannot see it."""
    import urllib.error

    _global(tmp_path)

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 403, "Forbidden", {}, 
            __import__("io").BytesIO(GRAPH_SCOPED_BODY.encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert "bound to ONE graph" in err
    assert "tortoise config show" in err
    assert "deliberate" in err          # the refusal is NOT presented as a bug
    assert "GRAPH_SCOPED_TEAM_SURFACE" not in err   # no raw internal code


def test_an_invalid_key_remedy_names_the_recovery_paths(monkeypatch, capsys, tmp_path):
    import urllib.error

    _global(tmp_path)

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 401, "Unauthorized", {},
            __import__("io").BytesIO(json.dumps({"detail": "Invalid API key"}).encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert "not valid" in err
    assert "tortoise recover" in err
    assert "tortoise config show" in err


def test_an_unknown_failure_keeps_its_body(monkeypatch, capsys):
    """A reporter that swallows an unrecognised error is worse than a raw one."""
    main._explain_org_info_failure(503, "upstream unavailable", "https://api.example")
    err = capsys.readouterr().err
    assert "API error (503)" in err
    assert "upstream unavailable" in err


def test_a_short_secret_discloses_no_characters():
    """At 12 chars the tail form printed half the secret; `serve --auth static`
    keys are arbitrary strings."""
    assert main._mask_secret_tail("abcdefghijkl") == "(present)"
    assert main._mask_secret_tail("tt_short") == "(present)"
    masked = main._mask_secret_tail("tt_" + "a" * 32)
    assert masked.startswith("tt_") and masked.endswith("aaaa")


def test_team_info_does_not_print_url_userinfo(monkeypatch, capsys, tmp_path):
    import urllib.error

    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY,
        "api_url": "https://user:hunter2@api.premiselabs.co",
    }))

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 401, "Unauthorized", {},
            __import__("io").BytesIO(b'{"detail": "Invalid API key"}'))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    cap = capsys.readouterr()
    assert "hunter2" not in cap.out + cap.err


def test_a_non_utf8_error_body_does_not_crash_the_handler(monkeypatch, capsys, tmp_path):
    """A captive portal answering 4xx with binary raises UnicodeDecodeError from
    `e.read().decode()` — INSIDE the handler that exists to explain the 4xx. This
    drives the real decode site (`HTTPError.fp`), not an already-decoded str."""
    import io
    import urllib.error

    _global(tmp_path)

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 500, "Server Error", {},
            io.BytesIO(b"\xff\xfe binary"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    assert "API error (500)" in capsys.readouterr().err


@pytest.mark.parametrize("body", [
    "[{\"msg\": \"upstream pool exhausted\"}]",   # a JSON ARRAY (crashed pre-review)
    '"a bare string"',
    "null",
    "{\"detail\": [1, 2]}",
    "{\"detail\": {\"error_code\": [\"a\"]}}",   # unhashable: crashed pre-review
    "{\"detail\": {\"error_code\": {\"a\": 1}}}",
    "",
    "{not json",
])
def test_a_non_object_error_body_never_crashes_the_report(capsys, body):
    """The body is server/proxy-controlled: an AttributeError here would replace
    a diagnostic with a traceback on the very path the verb exists to explain."""
    main._explain_org_info_failure(500, body, "https://api.example")
    assert "API error (500)" in capsys.readouterr().err


def test_a_200_whose_body_is_not_an_object_is_reported_not_crashed(monkeypatch, capsys, tmp_path):
    import io
    import urllib.error  # noqa: F401  (parity with the sibling tests)

    _global(tmp_path)

    def _fake_urlopen(_req, timeout=None):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _Resp(b'["not", "an", "object"]')

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    assert "expected a JSON object" in capsys.readouterr().err


def test_a_suspended_403_explains_itself_with_the_existing_parser(capsys):
    """`_suspended_info` is the module's own 403 parser; the remedy table must
    use it rather than printing the raw envelope for the one refusal that has a
    ready-made explanation."""
    main._explain_org_info_failure(
        403,
        json.dumps({"detail": {"code": "SUSPENDED",
                              "message": "This organization has been suspended.",
                              "appeal_url": "https://premiselabs.co/appeal"}}),
        "https://api.example",
    )
    err = capsys.readouterr().err
    assert "suspended" in err.lower()
    assert "https://premiselabs.co/appeal" in err
    assert "API error (403)" not in err


# ── the cold-start retry (#10655): a 10s CLI timeout is below a wake ─────


def test_timeout_reason_is_detected_and_a_refusal_is_not():
    assert main._is_timeout_reason(TimeoutError("timed out"))
    assert main._is_timeout_reason(OSError("The read operation timed out"))
    assert not main._is_timeout_reason(ConnectionRefusedError("refused"))
    assert not main._is_timeout_reason(None)


def test_a_timeout_retries_once_at_a_longer_bound_and_succeeds(monkeypatch, capsys, tmp_path):
    """A cold hosted app can outlast the CLI's 10s bound. The retry must (a)
    actually happen, (b) use a LONGER bound, and (c) report the team rather than
    an 'unreachable' verdict for a service that was only waking."""
    import io
    import urllib.error

    _global(tmp_path)
    bounds: list[float] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(_req, timeout=None):
        bounds.append(timeout)
        if len(bounds) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _Resp(json.dumps({"org_id": "00e8e36129794bcdb2c992ff47",
                                 "tier": "free", "point_count": 3,
                                 "max_users": 1, "max_graphs": 1}).encode())

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 0
    out, err = capsys.readouterr()
    assert bounds == [10, 30]
    assert "may be starting up" in err
    assert "00e8e36129794bcdb2c992ff47" in out


def test_a_retry_that_lands_on_a_403_still_explains_the_refusal(monkeypatch, capsys, tmp_path):
    """The cold-start retry can land on a DELIBERATE refusal (the app woke and
    the key is graph-scoped). Swallowing it as 'still no answer' would bypass the
    remedy on exactly the path this change exists to fix."""
    import io
    import urllib.error

    _global(tmp_path)
    calls: list[float] = []

    def _fake_urlopen(_req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 403, "Forbidden", {},
            io.BytesIO(GRAPH_SCOPED_BODY.encode()))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert calls == [10, 30]
    assert "bound to ONE graph" in err
    assert "still no answer" not in err


def test_a_BODY_READ_timeout_still_earns_the_cold_start_retry(monkeypatch, capsys, tmp_path):
    """The cold-app shape is headers-then-no-body: `resp.read()` raises a bare
    TimeoutError, which is an OSError and NOT a URLError. Keying the retry on
    URLError alone misses the exact case the retry exists for."""
    import io

    _global(tmp_path)
    bounds: list[float] = []

    class _SlowBody(io.BytesIO):
        def read(self, *a):
            raise TimeoutError("timed out")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return _SlowBody()

        def __exit__(self, *a):
            return False

    class _Ok(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(_req, timeout=None):
        bounds.append(timeout)
        if len(bounds) == 1:
            return _Resp()
        return _Ok(json.dumps({"org_id": "00e8e36129794bcdb2c992ff47",
                               "tier": "free", "point_count": 1,
                               "max_users": 1, "max_graphs": 1}).encode())

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 0
    out, err = capsys.readouterr()
    assert bounds == [10, 30]
    assert "may be starting up" in err
    assert "00e8e36129794bcdb2c992ff47" in out


def test_a_set_but_blank_env_key_is_not_reported_as_not_set(monkeypatch, capsys, tmp_path):
    """The resolver IGNORES a blank env key — but the user set it, so reporting
    'not set' sends them looking at the wrong place."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", "   ")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    env_row = payload["credential"]["ladder"][0]
    assert env_row["state"] == "set but blank (ignored)"
    assert env_row["used"] is False
    assert payload["credential"]["source"].endswith("credentials.json")


def test_an_untrusted_store_value_cannot_forge_a_report_line(monkeypatch, capsys, tmp_path):
    """`./.tortoise` is attacker-controllable by this file's own reckoning. A
    newline in org_name would print a whole fake ladder row claiming another
    store is the winner."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY,
        "api_url": "https://api.premiselabs.co",
        "org_id": "00e8e36129794bcdb2c992ff47",
        "org_name": "evil\n  /tmp/other/credentials.json  ok  ←  USED",
    }))
    assert _run() == 0
    out = capsys.readouterr().out
    # one line, escaped — never a second row
    assert "evil\\n" in out
    assert [ln for ln in out.splitlines() if "/tmp/other/credentials.json" in ln] == [
        next(ln for ln in out.splitlines() if "evil" in ln)]


def test_a_set_but_empty_db_uri_resolves_to_the_default(monkeypatch, capsys, tmp_path):
    """An EMPTY string is treated as unset by the resolver (only whitespace is
    rejected as a relative path) — so the report must say "default", not
    "unusable", and must not claim a store it did not resolve."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["source"] == "default"
    assert store["note_default"]
    assert "status" not in store


def test_a_whitespace_only_db_uri_is_unset_everywhere_not_half_set(
        monkeypatch, capsys, tmp_path):
    """`.strip()` for the note but truthiness for the target produced three
    mutually contradictory facts in one payload."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", "   ")
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["status"] == "configured but unusable"
    assert store["source"] == "TORTOISE_DB_URI (file path)"   # SET, and refused
    assert "target" not in store
    assert "target_kind" not in store
    assert _run() == 0
    assert "configured but unusable" in capsys.readouterr().out


def test_a_key_with_surrounding_whitespace_is_reported_not_called_healthy(
        monkeypatch, capsys, tmp_path):
    """The CLI sends the env key VERBATIM while the Pi hook trims it — so a key
    copied with a trailing newline authenticates from one surface and not the
    other, and a bare fingerprint comparison cannot say so."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY + "\n")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "whitespace" in payload["credential"]["api_key_whitespace"]
    # …and the masked key must not smuggle the newline into the REPORT LINE: the
    # tail is drawn from the credential, so the printer has to escape it.
    assert _run() == 0
    out = capsys.readouterr().out
    key_lines = [ln for ln in out.splitlines() if "(sha256:" in ln and "api_key" in ln]
    # The CLI's own line is the first `api_key` line; the harness block prints
    # its own (it inherits the env key) further down.
    assert "\\n" in key_lines[0]           # escaped, on one line


def test_the_harness_verdict_trims_like_the_hook_does(capsys, tmp_path):
    """`resolveConfig` applies `.trim()` to BOTH keys. Comparing untrimmed keys
    published two different fingerprints and then called them one identity."""
    _global(tmp_path)                                    # CLI key: GLOBAL_KEY
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY + "\n",
                                  "apiUrl": "https://api.premiselabs.co"}))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    # the FILE key carries a trailing newline; trimmed, it IS the CLI's key
    assert payload["identity_agrees"] is True
    assert payload["harness"]["api_key_fingerprint"] == \
        payload["credential"]["api_key_fingerprint"] == GLOBAL_FINGERPRINT


def test_an_env_key_beside_a_stale_file_publishes_the_effective_identity(
        monkeypatch, capsys, tmp_path):
    """The published fingerprint must be the one the hook USES, or the report
    prints two digests beside the words "same identity"."""
    _global(tmp_path)
    _harness(tmp_path)                                   # file key: HARNESS_KEY
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_agrees"] is True
    assert payload["harness"]["api_key_fingerprint"] == \
        payload["credential"]["api_key_fingerprint"]
    # the stale file key is still reported, under its own name
    assert payload["harness"]["file_api_key_fingerprint"] == HARNESS_FINGERPRINT


def test_a_non_utf8_key_does_not_crash_and_is_called_unsendable(monkeypatch, capsys, tmp_path):
    """`os.environ` decodes with surrogateescape, so a key with a non-UTF-8 byte
    is a str with lone surrogates and a strict `.encode()` raises — on exactly the
    state (an unusable key) that makes someone run this verb."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_\udcff\udcfeBADKEY")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    # labelled, not crashed — and this key is short, so no digest is published
    assert "cannot be sent in a request header" in payload["credential"][
        "api_key_unsendable"]
    assert "api_key_fingerprint" not in payload["credential"]


def test_a_hostile_org_id_cannot_forge_a_cache_graph_line(capsys, tmp_path):
    """`org_id` is store-supplied and is interpolated into THREE printed lines."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY,
        "api_url": "https://api.premiselabs.co",
        "org_id": "00e8e3\n  PWNED  ok  \u2190  USED",
    }))
    assert _run() == 0
    out = capsys.readouterr().out
    assert not any(ln.strip().startswith("PWNED") for ln in out.splitlines())
    assert "org_'00e8e3\n" in out or "org_'00e8e3" in out     # quoted, not bare


def test_a_control_character_in_a_path_cannot_forge_a_ladder_rung(
        monkeypatch, capsys, tmp_path):
    """A directory name containing a newline could otherwise print a whole fake
    rung claiming another store is the winner (`_rows()` would count it)."""
    evil = tmp_path / "x\n  PWNED  ok  \u2190  USED"
    evil.mkdir()
    _global(tmp_path)
    monkeypatch.chdir(evil)
    assert _run() in (0, 1)
    out = capsys.readouterr().out
    # Unescaped, the injected newline splits the cwd label into a SECOND line that
    # both begins with the injected text and ends with its own USED marker — a
    # whole fake rung claiming another store is the winner.
    assert len([ln for ln in out.splitlines() if ln.rstrip().endswith("←  USED")]) == 1
    assert not any(ln.strip().startswith("PWNED") for ln in out.splitlines())


def test_a_symlink_to_the_global_store_marks_exactly_one_row_used(
        monkeypatch, capsys, tmp_path):
    """"first match wins" must produce ONE marker even when both candidates
    resolve to the same file (a real sharing trick)."""
    g = _global(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    (repo / ".tortoise").symlink_to(g)
    assert _run() == 0
    out = capsys.readouterr().out
    assert len([ln for ln in _rows(out) if "USED" in ln]) == 1
    assert "same file as the winner above" in out


def test_config_json_before_the_subcommand_still_emits_json(capsys, tmp_path):
    """argparse copies the subparser's defaults onto the parent namespace, so a
    plain store_true on `show` clobbered `config --json show` into printing prose."""
    _global(tmp_path)
    assert _run(("config", "--json", "show")) == 0
    assert json.loads(capsys.readouterr().out)["credential"]["org_id"]


def test_the_harness_api_url_follows_the_harness_own_co_source_rule(
        monkeypatch, capsys, tmp_path):
    """`resolveConfig` uses `envKey ? TORTOISE_API_URL : fileUrl`. Reporting the
    file's apiUrl beside an env-sourced identity names a host the hook would
    never contact."""
    _global(tmp_path)
    _harness(tmp_path)                      # file apiUrl = the default host
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("TORTOISE_API_URL", "https://env-wins.example")
    assert _run(("config", "show", "--json")) == 0
    harness = json.loads(capsys.readouterr().out)["harness"]
    assert harness["source"] == "env:TORTOISE_API_KEY"
    assert harness["api_url"] == "https://env-wins.example"


def test_bare_config_json_is_not_a_usage_error(capsys, tmp_path):
    """The short form is an affordance; an affordance that only works in one
    output mode is a trap."""
    _global(tmp_path)
    assert _run(("config", "--json")) == 0
    assert json.loads(capsys.readouterr().out)["credential"]["org_id"]


def test_team_info_refuses_an_unsendable_key_without_echoing_it(monkeypatch, capsys):
    """`http.client` raises `ValueError("Invalid header value b'Bearer <KEY>'")`
    — the refusal itself prints the credential. Refuse before building the
    request, so the message can be ours."""
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_SECRETKEY0123456789\n")
    monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co")
    assert main.main(["team", "info"]) == 1
    cap = capsys.readouterr()
    assert "SECRETKEY0123456789" not in cap.out + cap.err
    assert "cannot be sent in a request header" in cap.err


def test_an_invalid_api_url_is_caught_not_tracebacked(monkeypatch, capsys, tmp_path):
    """`http.client.InvalidURL` derives from HTTPException — neither OSError nor
    ValueError — so it escaped as a TRACEBACK whose text embeds the api_url
    (including a password in its userinfo)."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY,
        "api_url": "https://user:hunter2@api.premiselabs.co",
    }))
    rc = main.main(["team", "info"])                 # real urlopen, no mock
    assert rc == 1
    cap = capsys.readouterr()
    both = cap.out + cap.err
    assert "hunter2" not in both
    assert "Traceback" not in both
    assert "Invalid response from API" in cap.err


def test_an_echoing_error_body_does_not_print_the_key(monkeypatch, capsys, tmp_path):
    """A proxy that echoes the request echoes the `Authorization` header. The
    body is the reply to a request that carried the key."""
    import io
    import urllib.error

    _global(tmp_path)

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 502, "Bad Gateway", {},
            io.BytesIO(json.dumps({"echo": {"Authorization": f"Bearer {GLOBAL_KEY}"}}
                                  ).encode()))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    cap = capsys.readouterr()
    assert GLOBAL_KEY not in cap.out + cap.err
    assert "<redacted>" in cap.err


def test_a_degraded_resolution_puts_its_reason_on_stderr(monkeypatch, capsys, tmp_path):
    """`degraded` is also exit 1 — a human in text mode must get the reason, not
    a pointer to a note that does not exist."""
    _global(tmp_path)
    monkeypatch.setenv("HOME", "~/x")
    assert _run() == 1
    cap = capsys.readouterr()
    assert "could not run" in cap.err


def test_the_cwd_candidate_discloses_the_transmitting_surfaces_exclusion(
        monkeypatch, capsys, tmp_path):
    """`_resolve_transmit_config` deliberately skips `./.tortoise` for the verbs
    that SEND. The report must say so or "which credential won" is wrong there."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _write(repo / ".tortoise", json.dumps({"api_key": GLOBAL_KEY,
                                          "api_url": "https://api.premiselabs.co"}))
    assert _run() == 0
    out = capsys.readouterr().out
    assert "transmitting verbs" in out
    assert "global store only" not in out


def test_a_matching_harness_file_is_not_called_stale(monkeypatch, capsys, tmp_path):
    """Env key + the SAME key on disk is the commonest fleet state; calling that
    file "stale / NOT in use" invites deleting the only key some sessions have."""
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY,
                                  "apiUrl": "https://api.premiselabs.co"}))
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "same key as above" in out
    assert "stale file" not in out


@pytest.mark.parametrize("bad", [["https://x.example"], {"u": 1}])
def test_team_info_survives_a_non_string_api_url(monkeypatch, capsys, tmp_path, bad):
    """`_read_config` validates `api_key` only; `_mask_uri_userinfo` calls
    `.split()`. Driven through `team info` — the path that crashed."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": bad,
    }))
    assert main.main(["team", "info"]) == 1
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err


def test_an_http_error_whose_body_read_stalls_does_not_escape(monkeypatch, capsys, tmp_path):
    """A proxy that sends 502 headers then stalls: the body READ raises a bare
    TimeoutError out of the handler that exists to explain the 502."""
    import io
    import urllib.error

    _global(tmp_path)

    class _StalledBody(io.BytesIO):
        def read(self, *a):
            raise TimeoutError("timed out")

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 502, "Bad Gateway", {},
            _StalledBody())

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "API error (502)" in err


def test_an_incomplete_error_body_does_not_escape(monkeypatch, capsys, tmp_path):
    """`IncompleteRead` (a proxy closing before the declared Content-Length) is an
    HTTPException, NOT an OSError — the same misbehaving-proxy case, reached a
    different way than a stall."""
    import http.client
    import io
    import urllib.error

    _global(tmp_path)

    class _Truncated(io.BytesIO):
        def read(self, *a):
            raise http.client.IncompleteRead(b"partial", 500)

    def _fake_urlopen(_req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 502, "Bad Gateway", {},
            _Truncated())

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "API error (502)" in err


def test_a_short_key_does_not_publish_its_digest(capsys, tmp_path):
    """48 bits over a SHORT secret is brute-forceable, and this line is what a
    transcript keeps — the same floor the tail uses must apply to the digest."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": "tt_short1", "api_url": "https://api.premiselabs.co",
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    cred = payload["credential"]
    assert "api_key_fingerprint" not in cred
    assert "brute-forceable" in cred["api_key_fingerprint_withheld"]


def test_a_short_HARNESS_key_does_not_publish_its_digest(capsys, tmp_path):
    """The floor must hold on BOTH sides: publishing the harness key's digest
    beside the CLI's withheld notice contradicts the same payload."""
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiKey": "tt_short", "apiUrl": "https://x.example"}))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "api_key_fingerprint" not in payload["harness"]
    assert "file_api_key_fingerprint" not in payload["harness"]
    assert "api_key_fingerprint_withheld" in payload["harness"]
    assert main._key_fingerprint("tt_short") not in json.dumps(payload)
    # …and TEXT mode must survive the same state (it read the missing key raw)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "digest withheld" in out
    assert main._key_fingerprint("tt_short") not in out


def test_an_env_key_beside_a_SHORT_file_key_survives_text_mode(monkeypatch, capsys, tmp_path):
    """`file_api_key_fingerprint` is absent when the file key is withheld; a raw
    subscript crashed the ONLY verb whose contract is to survive what it reports."""
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiKey": "tt_short", "apiUrl": "https://x.example"}))
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "stale file" in out            # a different (short) file key: stale, not "same"


def test_two_different_short_keys_are_not_called_the_same_key(monkeypatch, capsys, tmp_path):
    """Two withheld digests are both absent — comparing them (`None == None`) would
    report a distinct on-disk key as a duplicate, the keep-or-delete decision this
    line exists to inform."""
    # an env key so the "on disk / stale file" branch is reached at all, and a
    # DIFFERENT short key on disk (both digests withheld)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_shortA")
    _harness(tmp_path, json.dumps({"apiKey": "tt_shortB", "apiUrl": "https://x.example"}))
    assert _run() == 0
    out = capsys.readouterr().out
    assert "same key as above" not in out
    assert "stale file" in out


def test_the_scrubber_covers_query_values(monkeypatch, capsys, tmp_path):
    """A proxy may echo only the query PART of the URL, so each value (raw and
    percent-decoded) must be a scrub fragment of its own."""
    text = ('{"path": "/v1?api_key=SUPERSECRET",'
            ' "enc": "/v1?api_key=p%40ss"}')
    scrubbed = main._scrub(text, "https://h.example/v1?api_key=SUPERSECRET",
                           "https://h.example/v1?api_key=p%40ss")
    assert "SUPERSECRET" not in scrubbed
    assert "p@ss" not in scrubbed
    assert "p%40ss" not in scrubbed


@pytest.mark.parametrize("url", ["https://h/?", "https://h/#", "https://h?",
                                  "https://h#"])
def test_an_empty_query_does_not_shred_the_diagnostic(url):
    """A URL ending in `?`/`#` yields an EMPTY fragment, and `replace("", marker)`
    interleaves the marker between every character — losing the remedy, because
    the body is scrubbed before it is parsed."""
    assert main._scrub("API error 500: disk full", url) == "API error 500: disk full"


def test_a_trailing_question_mark_api_url_still_gets_the_403_remedy(capsys):
    main._explain_org_info_failure(
        403, json.dumps({"detail": {"error_code": "GRAPH_SCOPED_TEAM_SURFACE"}}),
        "https://api.premiselabs.co/?")
    err = capsys.readouterr().err
    assert "bound to ONE graph" in err
    assert "<redacted>" not in err


def test_the_remedy_does_not_promise_coordinates_this_machine_may_not_have():
    """`config show` says "the resolved store carries no org_id" for exactly the
    two key shapes the remedy prescribed, so the remedy must not claim otherwise."""
    text = main._ORG_INFO_REMEDIES["GRAPH_SCOPED_TEAM_SURFACE"]
    assert "already on this machine" not in text
    assert "reports what THIS machine knows" in text


def test_a_space_padded_key_is_not_called_broken(monkeypatch, capsys, tmp_path):
    """h11 strips leading/trailing OWS, so a space-padded key normally still
    authenticates — only CR/LF makes it unsendable."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY + " ")
    assert _run() == 0
    out = capsys.readouterr().out
    assert "cannot authenticate" not in out
    assert "usually still works" in out


def test_a_short_query_value_does_not_shred_the_diagnostic():
    """A bare fragment of 1-2 chars matches inside ordinary prose: `?v=1` turned
    "API error 401" into "API error 40<redacted>". Scrub the pair instead."""
    body = "rate limited, retry later"
    assert main._scrub(body, "https://h/v1?env=a") == body
    assert "API error 40<redacted>" not in main._scrub("API error 401 unauthorised",
                                                       "https://h/?v=1")


def test_the_harness_key_is_also_checked_for_sendability(monkeypatch, capsys, tmp_path):
    """Reporting a non-latin-1 harness key as healthy while the CLI key gets a
    ⛔ contradicts itself in one payload — and the arrow claimed "the harness's
    works"."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_\u2192ARROWKEY0123456789\n")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "api_key_unsendable" in payload["harness"]
    assert _run() == 0
    out = capsys.readouterr().out
    assert "the harness's copy cannot be sent either" in out


def test_the_same_key_on_a_different_host_is_not_called_one_identity(
        capsys, tmp_path):
    """`identity_agrees` compares key material only; a shared key pointed at two
    hosts means a refusal on one really does say nothing about the other."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": "https://selfhost.example",
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY,
                                  "apiUrl": "https://api.premiselabs.co"}))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_relation"] == "same-key-different-host"
    assert payload["identity_host_agrees"] is False
    assert _run() == 0
    assert "DIFFERENT endpoint" in capsys.readouterr().out


@pytest.mark.parametrize("cli_url, hook_url, agree", [
    ("https://api.premiselabs.co/", "https://api.premiselabs.co", True),
    ("https://API.premiselabs.co", "https://api.premiselabs.co", True),
    ("https://api.premiselabs.co:443", "https://api.premiselabs.co", True),
    ("https://api.premiselabs.co", "https://other.example", False),
    # the SAME malformed value on both sides is UNKNOWN, never "different"
    ("api.premiselabs.co", "api.premiselabs.co", None),
    ("https://api.premiselabs.co", "api.premiselabs.co", None),
    # plaintext vs TLS is NOT the same endpoint (it decides whether a transport
    # failure carries across surfaces)
    ("http://h.example:8443", "https://h.example:8443", False),
])
def test_endpoint_comparison_is_normalised_like_the_hook(
        capsys, tmp_path, cli_url, hook_url, agree):
    """The hook strips trailing slashes and compares hosts, not literal URLs;
    comparing whole URLs reported `https://h/` vs `https://h` as a DIFFERENT
    host — a false statement about the user's own configuration."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": cli_url,
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY, "apiUrl": hook_url}))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_host_agrees"] is agree
    if agree is None:
        assert payload["identity_relation"] == "same-key-host-unknown"
    else:
        assert payload["identity_relation"] == ("same" if agree
                                                else "same-key-different-host")


@pytest.mark.parametrize("bad_url", [
    "https://localhost:abc", "https://h:99999", "https://h:-1", "https://a:b:c",
])
def test_a_malformed_port_does_not_crash_the_endpoint_comparison(capsys, tmp_path, bad_url):
    """`urlsplit` is lazy: `.port` re-parses the authority and raises ValueError
    for a non-numeric/out-of-range port — hostile-store reachable."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": bad_url,
    }))
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY,
                                  "apiUrl": "https://api.premiselabs.co"}))
    assert _run(("config", "show", "--json")) in (0, 1)
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err


def test_no_cli_identity_makes_no_host_claim(capsys, tmp_path):
    """`identity_host_agrees: false` about a comparison that never happened
    contradicts the same report's text mode ("nothing to compare")."""
    _harness(tmp_path)
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_agrees"] is None
    assert payload["identity_host_agrees"] is None


def test_a_whitespace_only_api_url_env_is_disclosed(monkeypatch, capsys, tmp_path):
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("TORTOISE_API_URL", "   ")
    assert _run(("config", "show", "--json")) == 0
    cred = json.loads(capsys.readouterr().out)["credential"]
    assert "blank" in cred["api_url_note"]


def test_a_shell_metacharacter_is_quoted_in_the_CACHE_GRAPH_line_too(
        capsys, tmp_path):
    """`--cache-graph` takes no derived default, so this line is the only place the
    name is printed — and it is pasted. Quoting `--org` and not this token left the
    worse hole open."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": "https://api.premiselabs.co",
        "org_id": "$(touch${IFS}/tmp/PWNED_BY_CONFIG_SHOW)",
    }))
    assert _run(("config", "show", "--json")) == 0
    convention = json.loads(capsys.readouterr().out)["graph_store"][
        "cache_graph_convention"]
    import shlex
    # the first shell token must be the LITERAL name, quotes stripped
    token = shlex.split(convention.split(" (org SDK)")[0])[0]
    assert token == "org_$(touch${IFS}/tmp/PWNED_BY_CONFIG_SHOW)"


def test_the_chmod_remedy_is_quoted(capsys, tmp_path):
    """It is a line the user is told to PASTE, and the path is store-derived — a
    directory name can legally contain `;`."""
    evil_dir = tmp_path / "x; touch MARKER"
    evil_dir.mkdir()
    target = evil_dir / ".tortoise"        # the resolver's CWD candidate IS this file
    target.write_text(GLOBAL)
    # mode-000 FILE: `is_file()` passes and `read_text()` fails inside the
    # resolver's try, so it arrives as `_ConfigError` (deterministic on every
    # platform — a mode-000 PARENT is swallowed by pathlib's `is_file()` in 3.12)
    target.chmod(0o000)
    try:
        import os
        os.chdir(evil_dir)
        assert _run() == 1
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if "chmod 600" in ln)
        inside = line.split("chmod 600 ", 1)[1].rstrip("`).")
        import shlex
        assert shlex.split(inside) == [str(target)]
    finally:
        target.chmod(0o600)
        del target


def test_a_present_but_unusable_org_field_is_not_called_absent(capsys, tmp_path):
    """Reporting "(absent)" plus the `init --api-key` hint was a false claim twice
    over: the field IS there, and that hint does not describe such a store."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": "https://api.premiselabs.co",
        "org_id": 12345,
    }))
    assert _run(("config", "show", "--json")) == 0
    cred = json.loads(capsys.readouterr().out)["credential"]
    assert "org_field_warning" in cred
    assert "int" in cred["org_field_warning"]
    assert "org_id_hint" not in cred          # the hint would be a false explanation
    assert _run() == 0
    out = capsys.readouterr().out
    assert "not a NON-EMPTY string" in out


def test_the_chmod_remedy_is_display_escaped_too(monkeypatch, capsys, tmp_path):
    """`shlex.quote` is shell-safe but passes control characters through raw, so a
    newline in the path forged a report line."""
    evil = tmp_path / "x\n  FAKE  ok  \u2190  USED"
    evil.mkdir()
    target = evil / ".tortoise"
    target.write_text(GLOBAL)
    target.chmod(0o000)
    try:
        monkeypatch.chdir(evil)
        assert _run() == 1
        out = capsys.readouterr().out
        assert not any(ln.strip().startswith("FAKE") for ln in out.splitlines())
    finally:
        target.chmod(0o600)


def test_a_blank_org_field_is_absent_not_mistyped(capsys, tmp_path):
    """`signup`/`recover` copy the server's value straight through, so `""` is
    reachable — and it IS a string. Calling it "not a string" also suppressed the
    hint that is exactly right for it."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": "https://api.premiselabs.co",
        "org_id": "", "org_name": "  ",
    }))
    assert _run(("config", "show", "--json")) == 0
    cred = json.loads(capsys.readouterr().out)["credential"]
    assert "org_field_warning" not in cred
    assert "org_id_hint" in cred              # the accurate explanation, restored


def test_the_global_store_refusing_makes_the_transmitting_verbs_fail_too(
        monkeypatch, capsys, tmp_path):
    """Those verbs resolve `global_only=True`; asserting "unaffected" without
    looking told the reader to rely on upload while it was broken."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".tortoise").mkdir(parents=True)
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(repo)
    (repo / ".tortoise").write_text("{not json")
    (home / ".tortoise" / "credentials.json").write_text("{not json")
    assert _run() == 1
    out = capsys.readouterr().out
    assert "FAIL TOO" in out
    assert "are unaffected" not in out


def test_an_absent_global_store_makes_the_transmitting_verbs_identity_less(
        monkeypatch, capsys, tmp_path):
    """Only `ok` resolves. For absent/empty/directory the verbs have NO identity
    and file nothing (`session drain` exits 0 with "nothing to file"), so claiming
    "unaffected" told the reader upload was fine while it was dead."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)                       # HOME has no store at all
    (repo / ".tortoise").write_text("{not json")
    assert _run() == 1
    out = capsys.readouterr().out
    assert "are unaffected" not in out
    assert "do not resolve either" in out


def test_the_same_malformed_api_url_on_both_sides_is_not_called_different(
        monkeypatch, capsys, tmp_path):
    """A missing scheme is the normal shape of a mistyped TORTOISE_API_URL, and the
    report printed two identical values then asserted they differ."""
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("TORTOISE_API_URL", "api.premiselabs.co")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_host_agrees"] is None
    assert payload["identity_relation"] == "same-key-host-unknown"
    assert _run() == 0
    out = capsys.readouterr().out
    assert "DIFFERENT endpoint" not in out
    assert "cannot be made" in out


def test_a_transport_drop_is_reported_as_transport_not_as_an_invalid_response(
        monkeypatch, capsys, tmp_path):
    """`RemoteDisconnected` is both an OSError and an HTTPException. It used to be
    caught by the HTTPException branch and reported as "Invalid response from API",
    i.e. blamed on the SERVER's body — the diagnosis must be a transport failure.
    (The retry stays gated on a TIMEOUT: a reset is a different failure, reported
    plainly instead of retried.)"""
    import http.client
    import io

    _global(tmp_path)
    calls: list[float] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(_req, timeout=None):
        calls.append(timeout)
        raise http.client.RemoteDisconnected("Remote end closed connection")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    rc = main.main(["team", "info"])
    _out, err = capsys.readouterr()
    assert calls == [10]                            # not a timeout: no retry
    assert rc == 1
    assert "Cannot reach API at" in err             # a transport failure …
    assert "Invalid response from API" not in err   # … not the server's body
    assert "Remote end closed connection" in err


def test_a_retry_side_transport_drop_is_reported_as_transport(
        monkeypatch, capsys, tmp_path):
    """The SAME order invariant as the first attempt, on the RETRY (#10655
    review F2): `http.client.RemoteDisconnected` is BOTH a ConnectionResetError
    (OSError) and an HTTPException, and Python takes the FIRST matching clause.
    With the retry's arms in the pre-fix order the drop was printed as "Invalid
    response from API" — the server's BODY blamed for a dropped connection, on
    exactly the cold-app path this retry exists for.

    No other test in this file drives a retry-side transport drop: the first
    attempt's sibling (`..._is_reported_as_transport_not_as_an_invalid_response`)
    asserts `calls == [10]`, i.e. no retry happens at all."""
    import http.client
    import urllib.error

    _global(tmp_path)
    bounds: list[float] = []

    def _fake_urlopen(_req, timeout=None):
        bounds.append(timeout)
        if len(bounds) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        raise http.client.RemoteDisconnected("Remote end closed connection")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    _out, err = capsys.readouterr()
    assert bounds == [10, 30]                        # the retry DID happen
    assert "still no answer" in err                  # a transport failure …
    assert "Remote end closed connection" in err
    assert "Invalid response from API" not in err    # … not the server's body


def test_the_db_uri_log_line_does_not_print_a_query_credential(
        monkeypatch, capsys, caplog, tmp_path):
    """`resolve_db_path` logs the value; a `?password=…` in a path-shaped
    TORTOISE_DB_URI reached the LOG in clear, one line above the redacted field.

    Asserted on `caplog.text`, NOT only on the streams (#10655 review F3):
    `tortoise/config.py` logs through a plain propagating
    `logging.getLogger(__name__)`, pytest attaches its own handler to the ROOT
    logger for the call phase, and the record therefore never reaches the
    `logging.lastResort` stderr fallback — so a `capsys`-only assertion passes on
    a `config.py` whose `?`/`#` cut is reverted (the payload side it does see is
    masked by `_cmd_config` itself). Measured: reverted, the stream assertions
    stay green and these log assertions go red.
    """
    _global(tmp_path)
    monkeypatch.setenv(
        "TORTOISE_DB_URI", "/tmp/x.db?password=topsecret&api_key=tt_leak")
    with caplog.at_level("WARNING"):
        assert _run(("config", "show", "--json")) == 0
    cap = capsys.readouterr()
    assert "topsecret" not in cap.out + cap.err
    assert "tt_leak" not in cap.out + cap.err
    # The log record is where the pre-fix leak happened. The `file path` half
    # keeps the secret assertions from passing vacuously on an empty capture.
    assert "file path" in caplog.text
    assert "topsecret" not in caplog.text
    assert "tt_leak" not in caplog.text


def test_a_resolved_path_with_a_colon_is_still_a_path(monkeypatch, capsys, tmp_path):
    """`_resolve_db_target` only returns absolute paths here, so a colon is legal —
    calling it "userinfo-shaped" was a false statement about a working store."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "a:b.db"))
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["target_kind"] == "embedded file path"
    assert store["target"].endswith("a:b.db")


def test_the_whitespace_relation_carries_the_endpoint_verdict(
        monkeypatch, capsys, tmp_path):
    """The `same-modulo-whitespace` branch used to drop the different-endpoint
    clause its sibling carries."""
    _global(tmp_path)                                    # api_url https://api.premiselabs.co
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY + " ")
    monkeypatch.setenv("TORTOISE_API_URL", "")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    # `TORTOISE_API_URL=""` is not a parseable endpoint, so the verdict is UNKNOWN,
    # not "different" — the same distinction this commit makes everywhere else.
    assert payload["identity_host_agrees"] is None
    assert payload["identity_relation"].endswith("host-unknown")
    assert _run() == 0
    out = capsys.readouterr().out
    assert "cannot be made" in out
    assert "DIFFERENT endpoint" not in out


def test_an_unreadable_store_is_not_offered_for_deletion(capsys, tmp_path):
    """The file is INTACT and may hold the signup_token — a full credential and the
    only way back into the org. "repair or delete it" made deletion the
    actionable-sounding option."""
    d = tmp_path / ".tortoise"
    d.mkdir()
    (d / "credentials.json").write_text(GLOBAL)
    d.chmod(0o000)
    try:
        assert _run() == 1
        out = capsys.readouterr().out
        assert "Do NOT delete it" in out
        assert "chmod 600" in out
        assert "repair or delete it" not in out
    finally:
        d.chmod(0o700)


def test_the_refusal_does_not_claim_every_hosted_command_fails(
        monkeypatch, capsys, tmp_path):
    """With a corrupt `./.tortoise` and a healthy global store the transmitting
    verbs still work — and the row note right above says so."""
    _global(tmp_path)                                  # HOME/tmp_path: the global store
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    (repo / ".tortoise").write_text("{not json")
    assert _run() == 1
    out = capsys.readouterr().out
    assert "Every hosted command will fail" not in out
    assert "transmitting verbs" in out


def test_a_shell_metacharacter_in_org_id_is_quoted_in_the_record_scope(
        monkeypatch, capsys, tmp_path):
    """`./.tortoise` is attacker-controllable, the line is presented as the
    arguments to paste into `record-*`, and the quickstart tells users to run this
    verb first — an unquoted `$(…)` would RUN."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY,
        "api_url": "https://api.premiselabs.co",
        "org_id": "00e8e3; touch /tmp/PWNED_FILE #",
    }))
    assert _run(("config", "show", "--json")) == 0
    scope = json.loads(capsys.readouterr().out)["graph_store"]["record_scope"]
    # quoted, so the pasted line passes ONE argument and runs nothing
    import shlex
    assert shlex.split(scope)[1] == "00e8e3; touch /tmp/PWNED_FILE #"
    assert scope.startswith("--org '")


def test_the_graph_target_follows_the_resolver_precedence(monkeypatch, capsys, tmp_path):
    """The CLI resolves via `_resolve_db_target`: URI > FALKORDB_* > DB_PATH >
    default. Reporting only TORTOISE_DB_URI named a store the CLI would not use —
    and the shipped selfhost image sets TORTOISE_DB_PATH with no URI."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_PATH", "/data/tortoise.db")
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["source"] == "TORTOISE_DB_PATH"
    assert "tortoise.db" in store["target"]


def test_the_recovery_token_is_found_where_recover_reads_it(monkeypatch, capsys, tmp_path):
    """`recover` scans both stores regardless of which one supplied the KEY; asking
    the winner alone reported "absent" for a token recover finds and uses."""
    _global(tmp_path)                                   # holds the st_ token
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)  # the KEY comes from env
    assert _run(("config", "show", "--json")) == 0
    cred = json.loads(capsys.readouterr().out)["credential"]
    assert cred["recovery_token"] == "present"
    assert cred["recovery_token_source"].endswith("credentials.json")
    assert "recovery_token_note" in cred                # the #1752 divergence


def test_an_env_key_with_an_unresolvable_home_does_not_traceback(monkeypatch, capsys, tmp_path):
    """The TOKEN reader calls `Path.home()` itself, and the guard is bypassed on
    the env-key path because the resolver returns before touching home."""
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("HOME", "~/x")
    assert _run(("config", "show", "--json")) in (0, 1)
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err


@pytest.mark.parametrize("env, source", [
    ({"TORTOISE_DB_PATH": "/data/tortoise.db"}, "TORTOISE_DB_PATH"),
    ({"FALKORDB_HOST": "localhost"}, "FALKORDB_* trio"),
    ({}, "default"),
])
def test_the_graph_source_follows_the_resolvers_precedence(
        monkeypatch, capsys, tmp_path, env, source):
    _global(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["source"] == source
    assert store["target"]                     # the RESOLVED target, always


def test_a_legacy_trio_notice_does_not_corrupt_json(monkeypatch, capsys, tmp_path):
    """`_resolve_db_target` PRINTS its trio notice; unredirected it landed in front
    of the JSON payload and made `--json` unparseable for a script."""
    _global(tmp_path)
    monkeypatch.setenv("FALKORDB_HOST", "localhost")
    assert _run(("config", "show", "--json")) == 0
    cap = capsys.readouterr()
    json.loads(cap.out)                        # must be pure JSON
    assert "legacy" in cap.err                 # the notice went to stderr


@pytest.mark.parametrize("env, value", [
    ({"TORTOISE_DB_URI": "relative/x.db"}, "relative"),
    ({"TORTOISE_DB_URI": "   "}, "blank"),
    ({"TORTOISE_DB_PATH": "relative/p.db"}, "relative"),
])
def test_a_configured_but_unusable_store_is_reported_as_such(
        monkeypatch, capsys, tmp_path, env, value):
    """Falling through to the default note claimed the variable was unset and sent
    the user to `doctor` for a store the CLI REFUSES."""
    _global(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["status"] == "configured but unusable"
    assert store["error"]
    assert "target" not in store
    # …and the mode a HUMAN reads must say so, not "nothing is configured"
    assert _run() == 0
    out = capsys.readouterr().out
    assert "configured but unusable" in out
    assert "no TORTOISE_DB_URI" not in out


@pytest.mark.parametrize("uri, source", [
    ("/tmp/x/some.db", "TORTOISE_DB_URI (file path)"),      # backward-compat branch
    ("postgresql://h:5432/db", "TORTOISE_DB_URI (file path)"),
])
def test_a_scheme_less_db_uri_is_not_called_the_default(
        monkeypatch, capsys, tmp_path, uri, source):
    """`resolve_db_path` treats a scheme-less TORTOISE_DB_URI as an embedded file
    path — so labelling it `default` said "nothing is configured" about a target
    the user set."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    assert _run(("config", "show", "--json")) == 0
    store = json.loads(capsys.readouterr().out)["graph_store"]
    assert store["source"] == source
    assert "note_default" not in store


def test_a_query_string_does_not_fake_an_endpoint_mismatch(capsys, tmp_path):
    """The published api_url is truncated at `?`; comparing the MASKED form made
    two identical endpoints read as different."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": "https://api.example/v1?tenant=acme",
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY,
                                  "apiUrl": "https://api.example/v1?tenant=acme"}))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_host_agrees"] is True
    assert payload["identity_relation"] == "same"


def test_a_wrong_typed_harness_key_is_not_the_keyless_shape(monkeypatch, capsys, tmp_path):
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiKey": 12345, "apiUrl": "https://x.example"}))
    assert _run() == 0
    out = capsys.readouterr().out
    assert "unusable harness store (invalid-key)" in out
    assert "no api-key in this file" not in out


def test_the_harness_no_key_state_is_not_called_invalid(monkeypatch, capsys, tmp_path):
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiUrl": "https://x.example"}))
    assert _run(("config", "show", "--json")) == 0
    harness = json.loads(capsys.readouterr().out)["harness"]
    assert harness["state"] == "no-key"        # absent/None, not a wrong type
    assert _run() == 0
    out = capsys.readouterr().out
    assert "unusable harness store" not in out


def test_a_different_PATH_is_not_the_same_endpoint(capsys, tmp_path):
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": "https://api.example/v1",
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    _harness(tmp_path, json.dumps({"apiKey": GLOBAL_KEY,
                                  "apiUrl": "https://api.example/v2"}))
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_host_agrees"] is False
    assert _run() == 0
    assert "DIFFERENT endpoint" in capsys.readouterr().out


def test_a_keyless_harness_file_is_not_called_unusable(monkeypatch, capsys, tmp_path):
    """`resolveConfig` reads a keyless harness file happily — it supplies apiUrl
    and settings while the env supplies the key. Telling the user to delete it
    would break their settings."""
    _global(tmp_path)
    _harness(tmp_path, json.dumps({"apiUrl": "https://x.example", "cloud": True}))
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    assert _run() == 0
    out = capsys.readouterr().out
    assert "unusable harness store" not in out
    assert "no api-key in this file" in out


def test_an_empty_api_url_does_not_claim_both_surfaces_fail(
        monkeypatch, capsys, tmp_path):
    """The harness falls back to the default host when TORTOISE_API_URL is blank,
    so it still works — asserting a joint failure contradicted the same report."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("TORTOISE_API_URL", "")
    assert _run() == 0
    out = capsys.readouterr().out
    assert "BOTH fail the same way" not in out
    assert "Only one side is affected" in out


def test_the_same_unparseable_value_on_both_sides_says_so(
        monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("TORTOISE_API_URL", "api.premiselabs.co")
    assert _run() == 0
    out = capsys.readouterr().out
    assert "BOTH fail the same way" in out


def test_an_empty_api_url_env_is_disclosed(monkeypatch, capsys, tmp_path):
    """`TORTOISE_API_URL=""` makes the CLI send requests to an empty base URL —
    a state the user set deliberately and the report must name."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY)
    monkeypatch.setenv("TORTOISE_API_URL", "")
    assert _run(("config", "show", "--json")) == 0
    cred = json.loads(capsys.readouterr().out)["credential"]
    assert cred["api_url"] == ""
    assert "blank" in cred["api_url_note"]


@pytest.mark.parametrize("uri", [
    "postgresql://h:5432/db?password=hunter2",
    "postgresql://h:5432/db#password=hunter2",
    "https://api.example/v1?api_key=hunter2",
    "/tmp/me@work/db.fdb?password=hunter2",        # bare path, query credential
    "file:.db?password=hunter2",
    "dockeruser:987654321",                        # a NUMERIC password is still one
    "user:0",
])
def test_a_query_string_credential_is_not_printed(monkeypatch, capsys, tmp_path, uri):
    """`_mask_uri_userinfo` judges only the authority; this verb is a NEW printing
    surface for the DSN, and `?password=` is as much a credential as `user:pw@`."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    assert _run(("config", "show", "--json")) == 0
    cap = capsys.readouterr()
    assert "hunter2" not in cap.out + cap.err


@pytest.mark.parametrize("api_url, body", [
    ("https://api.example/v1?api_key=hunter2", json.dumps({"detail": "Invalid API key"})),
    ("http://127.0.0.1:9/v1?api_key=hunter2", None),
])
def test_team_info_masks_a_query_credential_too(monkeypatch, capsys, tmp_path,
                                                api_url, body):
    """`_mask_uri_userinfo` judges only the authority, so the query/fragment class
    closed in `config show` must be closed here as well."""
    import io
    import urllib.error

    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": api_url,
    }))
    if body is not None:
        def _fake_urlopen(_req, timeout=None):
            raise urllib.error.HTTPError(
                api_url, 401, "Unauthorized", {}, io.BytesIO(body.encode()))
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    cap = capsys.readouterr()
    assert "hunter2" not in cap.out + cap.err


def test_the_scrubber_covers_the_percent_decoded_userinfo():
    """A client (or redactor) may echo the DECODED form: `redis.from_url` applies
    `unquote`, so `p%40ss%3Aword` reaches the wire as `p@ss:word`."""
    text = '{"echo":"docker://user:p%40ss%3Aword@host/db","decoded":"user:p@ss:word"}'
    scrubbed = main._scrub(text, "docker://user:p%40ss%3Aword@host:6379/db")
    assert "p%40ss%3Aword" not in scrubbed
    assert "p@ss:word" not in scrubbed


def test_a_verbose_refusal_still_gets_its_remedy(capsys):
    """Truncating before parsing made a 4 KB 403 lose the suspension message and
    its appeal URL — the one refusal this change exists to explain."""
    body = json.dumps({"detail": {"code": "SUSPENDED", "message": "Suspended.",
                                 "appeal_url": "https://premiselabs.co/appeal"},
                       "filler": "x" * 4000})
    main._explain_org_info_failure(403, body, "https://api.example")
    err = capsys.readouterr().err
    assert "suspended" in err.lower()
    assert "https://premiselabs.co/appeal" in err


def test_a_deeply_nested_response_body_does_not_traceback(monkeypatch, capsys, tmp_path):
    import io

    _global(tmp_path)

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(_req, timeout=None):
        return _Resp(b"[" * 12000 + b"]" * 12000)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_a_deeply_nested_store_is_corrupt_not_a_recursion_crash(capsys, tmp_path):
    """`json.loads` raises RecursionError on a pathologically nested store — which
    is neither ValueError nor TypeError, and would escape the "reported, never
    raised" contract."""
    (tmp_path / ".tortoise").mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text("[" * 200000)
    assert _run(("config", "show", "--json")) == 1
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err
    payload = json.loads(cap.out)
    # REFUSED like every other corrupt store — not "degraded, environment"
    assert payload["status"] == "error"
    assert payload["config_state"] == "refused"
    assert [r["state"] for r in payload["credential"]["ladder"] if r["blocks"]] == ["corrupt"]


def test_a_scheme_less_user_password_dsn_is_not_printed(capsys, tmp_path):
    """The #2987 shape: a copy-paste that dropped the `@host` tail."""
    _global(tmp_path)
    import os
    os.environ["TORTOISE_DB_URI"] = "dockeruser:S3cretPw"
    try:
        assert _run(("config", "show", "--json")) == 0
        cap = capsys.readouterr()
        assert "S3cretPw" not in cap.out + cap.err
        store = json.loads(cap.out)["graph_store"]
        assert "embedded file path" not in store.get("target_kind", "")
    finally:
        del os.environ["TORTOISE_DB_URI"]


def test_an_ignored_api_url_env_is_disclosed(monkeypatch, capsys, tmp_path):
    """#2369 D1.1 ignores TORTOISE_API_URL for a FILE-sourced key — the report's
    own rule is that an ignored env var is named, not silently beaten."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_URL", "https://env-would-say.example")
    assert _run(("config", "show", "--json")) == 0
    cred = json.loads(capsys.readouterr().out)["credential"]
    assert cred["api_url"] == "https://api.premiselabs.co"      # the file won
    assert "IGNORED" in cred["api_url_note"]


def test_a_non_latin1_key_is_also_unsendable(capsys, tmp_path):
    """Header values are latin-1, not UTF-8: a `→` key passes a UTF-8 check and
    fails every request."""
    assert not main._is_sendable_key("tt_\u2192ARROWKEY0123456789")
    assert main._is_sendable_key("tt_" + "a" * 32)


def test_whitespace_only_difference_is_not_called_two_identities(monkeypatch, capsys, tmp_path):
    """The same key differing only by the whitespace the report already flags is
    NOT "two identities" — the harness refusal does say something about the CLI."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_API_KEY", GLOBAL_KEY + "\n")
    assert _run(("config", "show", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_relation"] == "same-modulo-whitespace"
    # one credential (the payload must not say "agrees: false" while the human
    # line says "the SAME key"); the caveat rides on the relation + the ⚠️ line
    assert payload["identity_agrees"] is True
    assert "api_key_whitespace" in payload["credential"]
    assert _run() == 0
    out = capsys.readouterr().out
    assert "modulo surrounding whitespace" in out
    assert "a DIFFERENT key" not in out


def test_the_graph_scoped_remedy_is_applied_only_to_a_403(monkeypatch, capsys):
    """The table explains the C5 403 (a decision). A 500 or a SUSPENDED body that
    happens to carry the same error_code must not be explained away by it."""
    main._explain_org_info_failure(500, GRAPH_SCOPED_BODY, "https://api.example")
    err = capsys.readouterr().err
    assert "bound to ONE graph" not in err
    assert "API error (500)" in err

    main._explain_org_info_failure(
        403,
        json.dumps({"detail": {"code": "SUSPENDED",
                              "message": "Suspended.",
                              "error_code": "GRAPH_SCOPED_TEAM_SURFACE"}}),
        "https://api.example",
    )
    err = capsys.readouterr().err
    assert "bound to ONE graph" not in err
    assert "suspended" in err.lower()


def test_a_non_string_api_url_does_not_crash_the_report(capsys, tmp_path):
    """`_resolve_config_path` validates `api_key` but NOT `api_url`, so a
    hand-edited store can carry a number — and the masker calls `.split()`."""
    _write(tmp_path / ".tortoise" / "credentials.json", json.dumps({
        "api_key": GLOBAL_KEY, "api_url": 12345,
        "org_id": "00e8e36129794bcdb2c992ff47",
    }))
    assert _run(("config", "show", "--json")) == 0
    assert json.loads(capsys.readouterr().out)["credential"]["api_url"] == "12345"


@pytest.mark.parametrize("uri, redacted", [
    ("/tmp/me@work/db.fdb", False),                         # a path, no userinfo
    ("docker://:pw@localhost:16379/tortoise", True),
    ("docker://user:pw@cache.example:6379/0", True),
])
def test_the_graph_target_keeps_the_maskers_fail_closed_rule(
        monkeypatch, capsys, tmp_path, uri, redacted):
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    assert _run(("config", "show", "--json")) == 0
    cap = capsys.readouterr()
    store = json.loads(cap.out)["graph_store"]
    if redacted:
        assert "pw@" not in store.get("target", "")
        assert "pw@" not in cap.out + cap.err
    else:
        assert store["target"] == uri


def test_a_scheme_dropped_dsn_is_refused_without_echoing_it(monkeypatch, capsys, tmp_path):
    """The #2987 shape: a copy-paste that dropped the scheme. The RESOLVER rejects
    it as a relative path, so the report refuses it — and must not echo it."""
    _global(tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker:user:S3cretPw@cache.example:6379/0")
    assert _run(("config", "show", "--json")) == 0
    cap = capsys.readouterr()
    store = json.loads(cap.out)["graph_store"]
    assert store["status"] == "configured but unusable"
    assert "S3cretPw" not in cap.out + cap.err


def test_a_suspended_403_without_an_appeal_url_still_explains_itself(capsys):
    main._explain_org_info_failure(
        403, json.dumps({"detail": {"code": "SUSPENDED",
                                    "message": "This organization has been suspended."}}),
        "https://api.example")
    err = capsys.readouterr().err
    assert "suspended" in err.lower()
    assert "Appeal:" not in err


def test_a_non_string_appeal_url_prints_no_blank_appeal_line(capsys):
    main._explain_org_info_failure(
        403, json.dumps({"detail": {"code": "SUSPENDED", "message": "Suspended.",
                                    "appeal_url": {"u": 1}}}),
        "https://api.example")
    err = capsys.readouterr().err
    assert "Suspended." in err
    assert "Appeal:" not in err


def test_a_suspended_message_with_a_newline_cannot_forge_a_stderr_line(capsys):
    main._explain_org_info_failure(
        403, json.dumps({"detail": {"code": "SUSPENDED",
                                    "message": "Suspended.\n  FORGED: nothing"}}),
        "https://api.example")
    err = capsys.readouterr().err
    assert not any(ln.strip().startswith("FORGED") for ln in err.splitlines())


def test_a_retry_landing_on_a_non_json_200_says_invalid_response(
        monkeypatch, capsys, tmp_path):
    """A proxy/captive-portal body on the second attempt is not "still no
    answer" — folding it into the transport branch sends the reader to the
    health endpoint for a proxy problem."""
    import io
    import urllib.error

    _global(tmp_path)
    calls: list[float] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(_req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _Resp(b"<html>captive portal</html>")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert "Invalid response from API" in err
    assert "still no answer" not in err


def test_a_retry_landing_on_a_500_keeps_the_body(monkeypatch, capsys, tmp_path):
    import io
    import urllib.error

    _global(tmp_path)
    calls: list[float] = []

    def _fake_urlopen(_req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        raise urllib.error.HTTPError(
            "https://api.premiselabs.co/v1/team", 500, "Server Error", {},
            io.BytesIO(b'[{"msg": "pool exhausted"}]'))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert calls == [10, 30]
    assert "API error (500)" in err
    assert "pool exhausted" in err


def test_a_retry_landing_on_a_200_array_is_reported(monkeypatch, capsys, tmp_path):
    import io
    import urllib.error

    _global(tmp_path)
    calls: list[float] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(_req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _Resp(b"[]")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    assert "expected a JSON object" in capsys.readouterr().err


def test_a_non_timeout_network_failure_does_not_retry(monkeypatch, capsys, tmp_path):
    """Negative guard: only a TIMEOUT earns a retry. A refusal is an answer."""
    import urllib.error

    _global(tmp_path)
    calls: list[float] = []

    def _fake_urlopen(_req, timeout=None):
        calls.append(timeout)
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert main.main(["team", "info"]) == 1
    err = capsys.readouterr().err
    assert calls == [10]
    assert "may be starting up" not in err
    # the reason must still be ON the line — escaping it to an empty string
    # deleted the diagnosis from the commonest failure there is
    assert "refused" in err
