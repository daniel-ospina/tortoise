"""#1708 Task 1 acceptance — command-level smoke tests.

1. Global-config smoke: commands resolve `~/.tortoise/credentials.json` when
   there is no cwd `.tortoise` (create-point / context / session / team all
   issue their request with the global key).
2. Env-only smoke: TORTOISE_API_KEY set + no files → hosted commands resolve
   the env key (the config=None crash guard: config.get(...) must never see
   None).
3. D1b regression: env alone must NOT flip `tortoise context` to hosted mode;
   a global config still does (include_env=False).
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.__main__ import main

GLOBAL_CFG = {
    "api_key": "tt_global", "api_url": "https://api.premiselabs.co",
    "org_id": "team-g", "org_name": "Global", "device_id": "anon-g",
}


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path, _capture_consent_default_on):
    """#1708 D9: never read the developer's real ~/.tortoise credentials."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    # #3615/#4276: capture needs explicit consent — a stray ambient opt-in must
    # not leak into (or out of) these tests. The suite-wide default
    # (`tests/conftest.py`) now GRANTS it, so this file opts back out; the
    # dependency makes that opt-out order AFTER the grant. The positive
    # `test_session_capture_from_global` below re-opts-in explicitly.
    monkeypatch.delenv("TORTOISE_CAPTURE", raising=False)
    monkeypatch.chdir(tmp_path)


def _seed_global(tmp_path):
    d = tmp_path / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    f = d / "credentials.json"
    f.write_text(json.dumps(GLOBAL_CFG))
    f.chmod(0o600)


def _ok(body: dict) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


class TestGlobalConfigCommands:
    """Command-level acceptance: create-point/context/session/team work from a
    global config (no cwd .tortoise)."""

    def test_team_info_from_global(self, tmp_path, monkeypatch, capsys):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"org_id": "team-g", "tier": "free", "point_count": 0})) as urlopen:
            rc = main(["team", "info"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/team"
        assert req.headers["Authorization"] == "Bearer tt_global"
        assert "Team:       team-g" in capsys.readouterr().out

    def test_create_point_from_global(self, tmp_path, monkeypatch):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"id": "p1", "kind": "statement"})) as urlopen:
            rc = main(["create-point", "hello world", "--kind", "statement"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/points"
        assert req.headers["Authorization"] == "Bearer tt_global"

    def test_session_capture_from_global(self, tmp_path, monkeypatch):
        _seed_global(tmp_path)
        # #3615: the credential alone is not consent — ask for capture.
        monkeypatch.setenv("TORTOISE_CAPTURE", "1")
        transcript = tmp_path / "conv.txt"
        transcript.write_text("User: hello\nAssistant: hi there\n")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"session_id": "s1"})) as urlopen:
            rc = main(["session", "capture", "--file", str(transcript)])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/sessions"
        assert req.headers["Authorization"] == "Bearer tt_global"

    def test_context_hosted_from_global(self, tmp_path, monkeypatch, capsys):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"diary_entries": [], "recent_points": [
                    {"content": "remembered", "pointKind": "observation"}],
                 "recent_events": []})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/context")
                   for call in urlopen.call_args_list)
        assert urlopen.call_args.args[0].headers["Authorization"] == "Bearer tt_global"
        assert "remembered" in capsys.readouterr().out

    def test_context_hosted_digest_header_names_hosted_source(self, tmp_path, monkeypatch, capsys):
        """#2209: with a stored global key the digest header must say the
        hosted memory is answering — not silently swap local → hosted."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"diary_entries": [], "recent_points": [
                    {"content": "remembered", "pointKind": "observation"}],
                 "recent_events": []})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert urlopen.called  # served by the hosted API
        out = capsys.readouterr().out
        assert "# Tortoise memory (from previous sessions) — hosted (api.premiselabs.co)" in out
        assert "local" not in out

    def test_context_hosted_empty_notice_names_hosted_source(self, tmp_path, monkeypatch, capsys):
        """#2209: even an empty hosted digest must name the hosted source — a
        local-first user signing up for cloud sees the switch immediately."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"no_prior_sessions": True})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert urlopen.called
        out = capsys.readouterr().out
        assert "no prior sessions" in out
        assert "Source: hosted (api.premiselabs.co)" in out
        assert "local" not in out

    def test_context_hosted_header_uses_configured_api_url(self, tmp_path, monkeypatch, capsys):
        """#2209: a self-hosted api_url in the stored config is what the header
        names — never a hardcoded Premise host."""
        _seed_global(tmp_path)
        cfg_path = tmp_path / ".tortoise" / "credentials.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["api_url"] = "https://tortoise.example.com"
        cfg_path.write_text(json.dumps(cfg))
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"diary_entries": [], "recent_points": [
                    {"content": "remembered", "pointKind": "observation"}],
                 "recent_events": []})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert urlopen.call_args.args[0].full_url.startswith("https://tortoise.example.com/v1/context")
        out = capsys.readouterr().out
        assert "# Tortoise memory (from previous sessions) — hosted (tortoise.example.com)" in out


class TestCreatePointRetrySafety:
    """#14424 — `create-point` must not leave a first-run user in a dead-end.

    MEASURED on the deployed build (2026-11-23, commit `e74cf1d13da7`): four
    attempts of this verb each returned `504` and **all four committed**, so the
    org held 4 identical Points. The server's refusal says *"wait for the
    advertised delay before retrying, and retry only if repeating the operation
    is safe"* — and the verb made both halves unactionable: it discarded the
    `Retry-After` header that IS the advertised delay, and nothing said how a
    repeat could be made safe. See `docs/evidence/14424-onboarding/`.

    Mutation check: drop the `_create_point_retry_hint` print from
    `_cmd_create_point` and the two refusal tests RED; drop the `dedup` payload
    arm and `test_dedup_flag...` REDs.
    """

    @staticmethod
    def _refusal(code: int, headers: dict[str, str] | None = None):
        import io as _io
        from email.message import Message
        from urllib.error import HTTPError

        msg = Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        body = json.dumps({"detail": "the server's wait budget was exceeded"}).encode()
        return HTTPError("https://api.premiselabs.co/v1/points", code,
                         "Gateway Timeout", msg, _io.BytesIO(body))

    def test_dedup_flag_sends_the_content_hash_fold(self, tmp_path):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"id": "p1"})) as urlopen:
            rc = main(["create-point", "hello world", "--dedup"])
        assert rc == 0
        payload = json.loads(urlopen.call_args.args[0].data.decode())
        assert payload["dedup"] is True
        assert payload["content"] == "hello world"

    def test_omitting_the_flag_keeps_the_existing_payload(self, tmp_path):
        """No behaviour change for the default path — `dedup` stays server-side
        default (False) unless the caller opts in."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"id": "p1"})) as urlopen:
            assert main(["create-point", "hello world"]) == 0
        assert "dedup" not in json.loads(urlopen.call_args.args[0].data.decode())

    def test_a_successful_create_names_where_to_search(self, tmp_path, capsys):
        """D5: the verb has no search sibling, so the success message must name
        the door that does — otherwise the memory is written and never found."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok({"id": "p1"})):
            assert main(["create-point", "hello world"]) == 0
        out = capsys.readouterr().out
        assert "Created point: p1" in out
        assert "/v1/search?q=" in out
        assert "tortoise_search" in out
        # ⛔ THE NAMED DOOR MUST BE RUNNABLE, or it is the same dead end it exists
        # to close. `/v1/search` is gated (`hosted_api.py:15765`
        # `Depends(get_current_org_gated)` -> 401 "Missing Authorization header"
        # at `hosted_api.py:9859`), so a hint that omits the header sends a
        # first-run user straight into a refusal. Mutation: drop the header
        # clause and this REDs.
        assert "Authorization: Bearer" in out
        # ⛔ AND IT MUST NAME WHERE THE KEY ACTUALLY IS. `signup` writes
        # `~/.tortoise/credentials.json` and never sets `TORTOISE_API_KEY`
        # (measured: no `environ[...]` in `_cmd_signup`), so a hint naming only
        # the env var hands the documented first-run user an empty Bearer and a
        # 401 — a fresh refusal inside the hint that exists to remove one.
        # Mutation: narrow the hint back to the env var and this REDs.
        assert "credentials.json" in out
        # ...and the printed URL must survive a paste. An unquoted `<` is a shell
        # REDIRECT, so a `<a word from it>` placeholder fails before any request
        # is made. Mutation: restore an angle-bracket placeholder and this REDs.
        assert "<" not in out

    def test_the_success_hint_masks_the_url_user_as_well_as_the_password(
            self, tmp_path, capsys):
        """STRICTLY STRONGER than `test_the_success_hint_never_prints_a_url_credential`.

        That test (added by `f7d769fb66e6`) asserts only `"…password" not in out`,
        i.e. it is satisfied by masking the PASSWORD while leaving the USERNAME in
        the transcript. Measured: masking only the password (`:pw@` → `:***@`, user
        kept) leaves that test GREEN and reds this one (at "svc:").

        This pin therefore covers the two things it does not:
          * the USER half of the userinfo is masked too; and
          * the mask is not OVER-broad — the host and path must survive, because a
            "fix" that redacts the URL into uselessness breaks the instruction the
            hint exists to carry (mutant: `***/v1/search?q=WORD` reds this).

        ⛔ CORRECTION, recorded rather than re-worded: an earlier version of this
        docstring claimed the success path had *no* equivalent witness and cited a
        `173 passed` measurement. Both were false at this head — that figure was
        taken over `45beda518b42..65d4028a3d`, BEFORE `f7d769fb66e6` added the
        password-class test, and was never re-taken. The witness existed; what was
        missing was the USERNAME half and the not-over-broad half, which is all
        this test claims.

        Why it still matters: `api_url` is store-supplied and
        `_resolve_config_path` passes it through verbatim, while `./.tortoise` is
        repo-controllable — so userinfo in that value reaches a transcript users
        paste into issues (`_mask_display_target`'s own docstring, `:3765`).
        """
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        store = d / "credentials.json"
        store.write_text(json.dumps({
            **GLOBAL_CFG,
            "api_url": "https://svc:s3cr3t@api.premiselabs.co",
        }))
        store.chmod(0o600)

        with mock.patch("urllib.request.urlopen", return_value=_ok({"id": "p1"})):
            assert main(["create-point", "hello world"]) == 0
        out = capsys.readouterr().out

        # The credential must not reach the transcript...
        assert "s3cr3t" not in out
        assert "svc:" not in out
        # ...the userinfo is masked, and the host stays usable so the instruction
        # is still runnable rather than redacted into uselessness.
        assert "https://:***@api.premiselabs.co/v1/search" in out

    def test_the_success_hint_does_not_let_a_url_forge_a_line(self, tmp_path, capsys):
        """`_printable` is the line-forgery guard on the same interpolation.

        A store `api_url` containing a newline would otherwise emit a second,
        attacker-authored line into the transcript — the class the doctor row pins
        as `test_control_bytes_in_the_hosted_row_cannot_forge_a_graph_row`.

        Mutation: drop `_printable(...)` at the hint and this REDs (observed).
        """
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        store = d / "credentials.json"
        store.write_text(json.dumps({
            **GLOBAL_CFG,
            "api_url": "https://a.example.test\n  FORGED-LINE",
        }))
        store.chmod(0o600)

        with mock.patch("urllib.request.urlopen", return_value=_ok({"id": "p1"})):
            assert main(["create-point", "hello world"]) == 0
        out = capsys.readouterr().out

        # No physical transcript line may start with the forged payload.
        assert not any(ln.strip().startswith("FORGED-LINE") for ln in out.splitlines())

    def test_a_trailing_slash_api_url_does_not_produce_a_double_slash(
            self, tmp_path, capsys):
        """D5: `_resolve_config_path` returns `TORTOISE_API_URL` VERBATIM, so a
        user-set value ending in `/` used to yield `…//v1/points` and a
        `…//v1/search` in the success hint -> 404 from a message the verb
        promises is runnable.

        Mutation: drop the `rstrip("/")` in `_cmd_create_point` and both
        assertions RED."""
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        f = d / "credentials.json"
        f.write_text(json.dumps({**GLOBAL_CFG, "api_url": "https://api.premiselabs.co/"}))
        f.chmod(0o600)
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok({"id": "p1"})) as _url:
            assert main(["create-point", "hello world"]) == 0
        # the REQUEST went to the normalized base, not `…//v1/points`
        sent = _url.call_args[0][0].full_url
        assert "//v1/" not in sent.replace("https://", "")
        assert sent.rstrip().endswith("/v1/points")
        # and the hint a user copies did not inherit the extra slash either
        out = capsys.readouterr().out
        assert "//v1/search" not in out

    def test_the_success_hint_never_prints_a_url_credential(self, tmp_path, capsys):
        """The success line prints the target URL, so it must MASK userinfo.

        `doctor` output and CLI transcripts are what users paste into issues, and
        a store-supplied `api_url` may carry basic-auth credentials.

        Mutation: replace `_printable(_mask_display_target(api_url or ''))` with
        `api_url or ''` and this REDs — measured, the raw form printed
        `https://svc:sup3rsecretpw@api.premiselabs.co/v1/search?q=WORD`.
        """
        _seed_global(tmp_path)
        store = tmp_path / ".tortoise" / "credentials.json"
        store.write_text(json.dumps({
            **GLOBAL_CFG,
            "api_url": "https://svc:sup3rsecretpw@api.premiselabs.co",
        }))
        store.chmod(0o600)
        with mock.patch("urllib.request.urlopen", return_value=_ok({"id": "p1"})):
            assert main(["create-point", "hello world"]) == 0
        out = capsys.readouterr().out
        assert "sup3rsecretpw" not in out
        assert "/v1/search?q=" in out

    def test_wait_bound_refusal_names_the_delay_and_the_safe_retry(
            self, tmp_path, capsys):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(504, {"Retry-After": "10"})):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        # the advertised delay is the HEADER the refusal points at
        assert "Retry-After: 10" in err
        # what changed: the failure does not mean nothing was written
        assert "may have" in err and "completed on the server" in err
        # the next action, and the cost of skipping it
        assert "--dedup" in err
        assert "duplicate" in err

    def test_a_504_retried_with_dedup_is_told_it_is_safe(self, tmp_path, capsys):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(504, {"Retry-After": "10"})):
            rc = main(["create-point", "hello world", "--dedup"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "Re-running is safe" in err
        # the duplicate warning belongs to the arm that did NOT opt in
        assert "duplicate Point" not in err

    def test_a_non_wait_bound_error_gets_no_retry_prose(self, tmp_path, capsys):
        """A 422 is the server rejecting the body: retrying it is not the next
        action, so the hint must stay quiet rather than advise a re-send."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(422)):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "--dedup" not in err
        assert "Retry-After" not in err
        assert "re-sending" not in err

    def test_a_retry_after_http_date_keeps_its_own_units(self, tmp_path, capsys):
        """RFC 9110: `Retry-After = HTTP-date / delay-seconds`. The hint used to
        append a bare `" s"`, so a proxy emitting an HTTP-date rendered as
        `Retry-After: Wed, 21 Oct 2015 07:28:00 GMT s` — a nonsense delay a
        first-run user cannot act on (#14424 review)."""
        _seed_global(tmp_path)
        when = "Wed, 21 Oct 2015 07:28:00 GMT"
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(504, {"Retry-After": when})):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        assert when in err, "the header value must be echoed as supplied"
        assert f"{when} s" not in err, (
            "a seconds unit was appended to an HTTP-date — the load-bearing gap")

    def test_a_refusal_with_no_retry_after_names_the_header_it_lacks(
            self, tmp_path, capsys):
        """COVERAGE PIN, not a mutation witness — measured, so nobody reads it as
        one: this assertion **passes on the pre-fix code**, because the
        header-less wording predates this change. It is here because the #14424
        review named this arm as *untested* and an untested branch is one an
        edit can silently break.

        What it protects: a `503` from a proxy during a rolling deploy carries no
        `Retry-After`, so "the server advertised N seconds" would be fabricated;
        and, being a `503` rather than a wait-bound `504`, it must not inherit
        the "may have completed" claim. The mutation that reds it is reverting
        the `if retry_after:` branch to the header-only arm."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(503)):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "no Retry-After" in err
        assert "advertised Retry-After:" not in err, (
            "invented a delay the response did not carry")
        assert "does NOT mean nothing was written" not in err, (
            "the 504 completion claim must not leak onto the 503 arm")

    def test_a_refusal_without_retry_after_names_a_concrete_wait(
            self, tmp_path, capsys):
        """#14424 review P2-3: naming the missing header is not enough.

        The measured 504 body (docs/evidence/14424-onboarding/README.md §1.1)
        says only *"wait for the advertised delay before retrying"* — and "the
        advertised delay" IS the header. So with the header absent, the pre-fix
        fallback ("wait the delay its body names") sent the reader to the body
        for a number the body does not contain: the same dead-end this hint
        exists to close (AX rule #10183 — a surface must not refuse where the
        next action is knowable). The fallback must name a concrete wait; 10 s
        is what the deployed server advertises when it does send the header.

        Mutation that reds it: restore the pre-fix fallback text.
        """
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(504)):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "no Retry-After" in err
        # actionable: a stated wait the user can act on
        assert "10 s" in err, "the fallback must name a concrete delay"
        assert "its body names" not in err, (
            "the body names no number — pointing at it yields no wait")
        # and the 504 arm still says what changed about the write
        assert "may have" in err and "completed on the server" in err

    def test_an_unreachable_api_does_not_claim_nothing_was_written(
            self, tmp_path, capsys):
        """#14424 review: the `URLError` arm asserted "Nothing was sent, so
        nothing was written". That is not a fact this error can establish —
        `urlopen` follows redirects, so a 3xx answering this POST is already a
        server-processed write and a failure on the SECOND hop arrives here;
        and RFC 9110 only permits a POST retry when the client can determine the
        original was never applied. The neighbouring `HTTPError` arm says "may
        have landed" for the same reason; the two must not carry opposite
        epistemics. Consequence of the old wording: "nothing was written" tells
        the user to re-run WITHOUT `--dedup`, and the retry folds nothing."""
        from urllib.error import URLError

        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=URLError("Connection refused")):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "Connection refused" in err, "the transport reason must survive"
        assert "Nothing was sent" not in err, (
            "the unprovable claim this test exists to remove")
        assert "does not prove it failed to land" in err
        assert "--dedup" in err, (
            "the retry override must be named, or the user re-runs without it")

    def test_a_rate_limit_refusal_does_not_claim_a_duplicate_risk(
            self, tmp_path, capsys):
        """The arms are NOT interchangeable: a 429 is an admission refusal — the
        write never ran — so it must not inherit the 504 duplicate warning, which
        would tell a caller to reach for a flag that changes nothing here."""
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._refusal(429, {"Retry-After": "30"})):
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "Retry-After: 30" in err
        assert "re-sending after that delay is safe" in err
        assert "duplicate" not in err
        assert "may have" not in err

    def test_a_read_timeout_is_a_hint_not_a_traceback(self, tmp_path, capsys):
        """The write landed and the RESPONSE stalled — modelled as such.

        ⛔ The stall is raised from `read()`, NOT from `urlopen`: a raise from
        `urlopen` models a SEND-phase failure, which `do_open` already converts
        into `URLError` (`except OSError as err: raise URLError(err)`). The
        documented failure is the one AFTER that — the request is fully sent and
        `resp.read()` raises — which skips the `HTTPError` and `URLError` arms
        entirely. Faking it from `urlopen` would pass this test while never
        executing the path its own name claims.

        Mutation: delete the `except (TimeoutError, OSError)` arm and this REDs
        (the exception escapes `main`) — observed.
        """
        class _StalledResponse:
            """Headers arrived, the body never did."""

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                raise TimeoutError("read timed out")

        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        return_value=_StalledResponse()) as urlopen:
            rc = main(["create-point", "hello world"])
        err = capsys.readouterr().err

        # The request really was dispatched before the stall — that is the whole
        # point of the arm's claim, so assert it rather than assume it.
        assert urlopen.call_count == 1
        assert rc == 1
        # what changed: the request had left the box before the stall
        assert "MAY have landed" in err
        # and the reconciliation path, not a raw stack trace
        assert "/v1/points" in err or "--dedup" in err
        assert "Traceback" not in err


class TestConfigErrorCommandHandling:
    """D6 per-site _ConfigError handling: a corrupt/unreadable global config
    must never traceback — context falls back to local mode, create-point and
    session fail cleanly with rc 1 ("Invalid config at {path}")."""

    @staticmethod
    def _seed_corrupt_global(tmp_path):
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_text("{not json")

    def test_context_corrupt_global_falls_back_to_local(self, tmp_path, monkeypatch, capsys):
        self._seed_corrupt_global(tmp_path)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "local.db"))
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert not urlopen.called, "corrupt config must fall back to the local SDK path"
        cap = capsys.readouterr()  # readouterr DRAINS the buffer — capture ONCE
        assert "corrupt or unreadable" in cap.err
        assert "no prior sessions" in cap.out

    def test_create_point_corrupt_global_fails_closed(self, tmp_path, monkeypatch, capsys):
        self._seed_corrupt_global(tmp_path)
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["create-point", "hello world"])
        assert rc == 1
        assert not urlopen.called
        assert "Invalid config at" in capsys.readouterr().err

    def test_session_capture_corrupt_global_fails_closed(self, tmp_path, monkeypatch, capsys):
        self._seed_corrupt_global(tmp_path)
        transcript = tmp_path / "conv.txt"
        transcript.write_text("User: hello\nAssistant: hi\n")
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["session", "capture", "--file", str(transcript)])
        assert rc == 1
        assert not urlopen.called
        assert "Invalid config at" in capsys.readouterr().err


class TestEnvOnlyResolution:
    """TORTOISE_API_KEY set + no files → hosted commands resolve the env key
    (the config=None crash guard: config.get(...) must never see None)."""

    def test_team_keys_list_env_only(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"keys": [{"id": "k1", "key_prefix": "tt_env"}]})) as urlopen:
            rc = main(["team", "keys", "list"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tt_envkey"
        assert req.full_url == "https://api.premiselabs.co/v1/team/keys"

    def test_team_keys_list_json_env_only(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"keys": [{"id": "k1", "key_prefix": "tt_env"}]})) as urlopen:
            rc = main(["team", "keys", "list", "--json"])
        assert rc == 0
        assert urlopen.call_args.args[0].headers["Authorization"] == "Bearer tt_envkey"

    def test_team_keys_create_json_env_only(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"id": "kid1", "key": "tt_newkey", "key_prefix": "tt_new"})) as urlopen:
            rc = main(["team", "keys", "create", "--json"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tt_envkey"
        assert req.get_method() == "POST"


class TestContextD1bRegression:
    """D1b: env alone must NOT flip `tortoise context` to hosted mode; a
    global config still does (include_env=False)."""

    def test_context_env_only_stays_local(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "local.db"))
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert not urlopen.called, "env alone must keep context on the local SDK path"
        assert "no prior sessions" in capsys.readouterr().out

    def test_context_global_config_still_hosted_with_env_key_set(self, tmp_path, monkeypatch):
        _seed_global(tmp_path)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"diary_entries": [], "recent_points": [
                    {"content": "hosted digest", "pointKind": "observation"}],
                 "recent_events": []})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tt_global"
