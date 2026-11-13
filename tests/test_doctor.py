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
import http.client
import io
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

    Doctor's collision hint names a replacement command, and in that state the
    installer-shaped commands are exactly the ones a user must not be sent to:
    `tortoise install pi` refuses on the collision, and so does
    `hooks upgrade --harness pi` (it calls the same installer).  This asserts
    the command actually named accepts the harness.  Its exit code may still
    be non-zero for a missing config, which is not a refusal of the REQUEST.
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


def _row(out: str, label: str) -> str | None:
    """The results line for `label` (None when the row was not printed)."""
    return next((ln for ln in out.splitlines() if f" {label}:" in ln), None)


@pytest.fixture(autouse=True)
def _hermetic_hosted_identity(monkeypatch, tmp_path):
    """#11912: `doctor` probes the hosted identity when one is configured, so
    this module must never inherit the OPERATOR's identity.

    BOTH halves of the ladder have to be closed, not just the cheap one:
    `_resolve_config_path` is env → `<cwd>/.tortoise` →
    `~/.tortoise/credentials.json`, and `<cwd>/.tortoise` is exactly what
    `tortoise init --api-key` writes into a checkout (gitignored, so it is a
    normal untracked artifact). Isolating only `$HOME` left the HIGHER-precedence
    candidate live: on a developer box that had run `init --api-key` here, every
    test in this module — the keyless assertion below included — resolved that
    credential and dialled whatever `api_url` named (fresh review of this change,
    measured). So the fixture chdirs into a fresh empty directory as well; tests
    that need a particular cwd set their own afterwards.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    # ⛔ AND A HARD GUARD, because scrubbing the ambient identity is not enough:
    # any test in this module that puts a credential anywhere (a fixture setting
    # TORTOISE_API_KEY, a seeded store) makes `doctor` dial for real, and a fake
    # key aimed at the production default sent live requests to
    # api.premiselabs.co from five spool tests (fresh review round 2, measured).
    # A suite must never depend on a third party's availability. Tests that WANT
    # the probe stub it with `_stub_hosted`, which overrides this; anything else
    # fails loudly instead of reaching the network — `pytest.fail` raises
    # `Failed`, which derives from BaseException, so `doctor`'s own
    # `except Exception` cannot swallow it into a quiet ⚠️ row.
    import urllib.request

    def _no_network(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        pytest.fail(f"unstubbed network call from tests/test_doctor.py: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    return home


class _FakeResponse:
    """The minimal `urlopen(...)` context-manager response shape doctor uses.

    Backed by a real `io.BytesIO` so it behaves like a file: it HONOURS `n` and
    returns `b""` at EOF. A duck-typed double that returned its whole body on
    every call made the (chunked) body reader append the same bytes until the cap
    — and made an `HTTPError`'s `fp` wrapper raise on GC (fresh review rounds 10
    and 15).
    """

    def __init__(self, status: int, body: bytes = b""):
        self.status = status
        self._buf = io.BytesIO(body if isinstance(body, (bytes, bytearray)) else b"")
        self._body = body

    def read(self, n: int = -1):
        return self._buf.read(n)

    def read1(self, n: int = -1):
        return self._buf.read(n)

    def getcode(self):
        return self.status

    @property
    def length(self):
        """Remaining DECLARED length, like `http.client.HTTPResponse.length`.

        Without it `_read_capped`'s "a completed body is not a cut body" branch
        (`getattr(resp, "length", None) == 0`) is unreachable from the suite, and
        reverting that fix stayed green (fresh review round 18).
        """
        remaining = self._buf.getbuffer().nbytes - self._buf.tell()
        return remaining

    def close(self):
        self._buf.close()

    @property
    def closed(self):
        return self._buf.closed

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()
        return False


def _stub_hosted(monkeypatch, handler):
    """Replace `urllib.request.urlopen` for the duration of a test.

    `_doctor_hosted_rows` imports it at CALL time, so patching the attribute on
    `urllib.request` is what the production path resolves. Returns the list of
    `(url, timeout)` pairs actually requested, so a test can assert on the
    requests that were NOT made — the property this feature is most likely to
    regress (a diagnostic that dials a keyless box).
    """
    import urllib.request

    calls: list[tuple[str, int | None]] = []

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        calls.append((url, timeout))
        return handler(url, req)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _write_credential(home, api_key: str, api_url: str | None = None) -> None:
    """Seed the global credential store the resolver reads (mode-0600)."""
    import json

    store = home / ".tortoise"
    store.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": api_key, "org_id": "org-1", "org_name": "agent-1"}
    if api_url:
        payload["api_url"] = api_url
    path = store / "credentials.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


#: A hosted-shaped key that is NOT a credential — built by concatenation so the
#: literal can never be mistaken for (or scanned as) a live token. Used to prove
#: `doctor` prints a mask and never the value.
_FAKE_HOSTED_KEY = "t" + "t_" + "FAKEKEY0000000000000000000000"
_FAKE_ENV_KEY = "t" + "t_" + "ENVKEY00000000000000000000000"


#: The shipped probe bounds, as LITERALS on purpose. Reading them back from the
#: module under test makes the assertion compare a constant with itself, which
#: is green for ANY value — a retry narrowed to 3s, i.e. narrower than both first
#: attempts, reintroduces the cold-start misgrade and passed (fresh review round
#: 11). These numbers are the contract; changing them must red this test.
_EXPECTED_HEALTH_TIMEOUT_S = 5
_EXPECTED_TEAM_TIMEOUT_S = 10
_EXPECTED_TEAM_RETRY_TIMEOUT_S = 30


def _assert_retry_widens(attempts: list[int], first: int, retry: int) -> None:
    assert attempts == [first, retry], attempts
    assert retry > first, (
        f"the retry bound ({retry}s) must EXCEED the first attempt ({first}s), or "
        "the retry re-introduces the cold-start failure it exists to prevent")


class TestDoctorReadCappedIsAWallClock:
    """The body read promises a WALL CLOCK, and only a real socket can test it.

    Every other drip test in this file models the drip at the `read1` boundary —
    one slow CALL at a time — which is the shape the between-calls deadline
    check already handles. The LIVE shape is different: on a CHUNKED body one
    `read1` loops inside `http.client` for the chunk-size line
    (`_read_next_chunk_size` → `fp.readline`), so the drip happens INSIDE a
    single call, where no Python-level check can run. Measured before the
    watchdog: one `read1(4096)` held for 12.01 s against a 0.5 s per-recv bound
    (x24), and it never returned at all while the drip continued — a proxy or
    CDN normalises a reply to chunked, so this is the ordinary path, not a
    corner. `_doctor_read_capped` therefore `shutdown()`s the socket at the
    deadline.
    """

    def _drip_chunk_size_server(self, drip_gap: float, bytes_to_drip: int,
                                status: int = 200, chunked: bool = True):
        """A chunked response whose CHUNK-SIZE LINE arrives one byte at a time.

        No newline is ever sent, so the chunk-size `readline` cannot complete —
        the exact shape that outlives a per-`recv` socket bound. Returns
        `(port, listening_socket)`; the caller closes it.

        `status` exists because the SAME shape reaches the reader through two
        different objects: a 2xx arrives as an `HTTPResponse` and a 4xx/5xx as an
        `HTTPError` whose `fp` is that `HTTPResponse` — one hop deeper, which is
        where a one-hop socket lookup silently disarmed the watchdog.
        """
        import socket
        import threading
        import time

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def serve() -> None:
            try:
                conn, _ = srv.accept()
            except OSError:      # listening socket closed by the test
                return
            try:
                conn.recv(65536)
                reason = {200: b"OK", 500: b"Internal Server Error"}[status]
                # ⛔ TWO shapes share this server: `chunked` (a chunk-size line
                # that never completes, so `read1` spins inside `readline`) and
                # close-delimited (no `Content-Length`, no `Transfer-Encoding`,
                # where the same interrupt makes `read1` return `b""` instead of
                # raising). Both are drips inside ONE call; only the second can be
                # mistaken for the peer's own end of body.
                framing = b"Transfer-Encoding: chunked\r\n" if chunked else b""
                conn.sendall(b"HTTP/1.1 " + str(status).encode() + b" " +
                             reason + b"\r\n" + framing +
                             b"Content-Type: application/json\r\n\r\n")
                for _ in range(bytes_to_drip):
                    conn.sendall(b"a")
                    time.sleep(drip_gap)
            except OSError:      # the watchdog shut the socket down
                pass
            finally:
                conn.close()

        threading.Thread(target=serve, daemon=True).start()
        return srv.getsockname()[1], srv

    def test_a_chunked_body_dripping_inside_one_read1_stops_at_the_deadline(
            self):
        """Without the watchdog this test does not fail fast — it HANGS.

        200 bytes at 0.05 s each is ~10 s of headroom against a 0.5 s bound: a
        reader that only checks the deadline between calls returns 0 times, so
        the wall clock assertion is what fails. Verified by mutation (watchdog
        disarmed): elapsed 10.1 s > the 6 s ceiling below.

        ⛔ A real `http.client.HTTPResponse`, not `urlopen`: this module's
        autouse fixture replaces `urlopen` with a fail-loudly stub (a suite must
        never dial a third party), and the shape under test is the transport's,
        not urllib's.
        """
        import http.client
        import time

        from tortoise import __main__ as cli

        bound = 0.5
        port, srv = self._drip_chunk_size_server(drip_gap=0.05,
                                                 bytes_to_drip=200)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=bound)
        try:
            conn.request("GET", "/v1/team")
            resp = conn.getresponse()
            started = time.monotonic()
            outcome = "returned"
            cut = False
            try:
                body, cut = cli._doctor_read_capped(
                    resp, cli._DOCTOR_BODY_CAP, started + bound)
                outcome = f"returned {len(body)} bytes cut={cut}"
            except (OSError, http.client.HTTPException) as exc:
                # An interrupted read is a READ ERROR, which the callers already
                # report as an unread body — never as a complete one.
                outcome = f"raised {type(exc).__name__}"
            elapsed = time.monotonic() - started
        finally:
            conn.close()
            srv.close()

        assert elapsed < 6.0, (
            f"one read1 against a {bound} s deadline took {elapsed:.2f} s "
            f"({outcome}) — the drip is inside a single call, so a "
            "between-calls deadline check cannot bound it")
        assert outcome.startswith("raised") or cut, (
            f"the interrupted body was reported COMPLETE ({outcome})")

    def test_a_body_the_watchdog_ended_is_never_reported_complete(self):
        """The DETERMINISTIC form of the same defect: EOF after OUR interrupt.

        The close-delimited test below exercises the shape, but on the shipped
        code the loop's deadline check usually wins the race and reports `cut`
        anyway, so it cannot isolate the flag. This one does: the deadline is 20 ms
        out, the watchdog fires while `read1` is still blocked (100 ms), and
        `read1` answers the shutdown the way a close-delimited body does — with
        `b""`, no exception, and `length is None`. Grading that as "the stream
        declared its end" is the false red: it reported `cut=False` for a body
        THIS reader truncated (measured at `fb2926ef9e`).
        """
        import time

        from tortoise import __main__ as cli

        class _Sock:
            def shutdown(self, how):        # the watchdog's only call
                pass

        class _Raw:
            _sock = _Sock()

        class _Fp:
            raw = _Raw()

        class _CloseDelimited:
            fp = _Fp()
            length = None                   # no declared length, either shape

            def read1(self, n: int = -1):
                time.sleep(0.1)             # the 20 ms watchdog fires in here
                return b""                  # what a shutdown looks like here

        body, cut = cli._doctor_read_capped(_CloseDelimited(), 65536,
                                           time.monotonic() + 0.02)
        assert body == b"", body
        assert cut is True, (
            "a body the watchdog ended was reported COMPLETE — the interrupt is "
            "being read as the peer's own end of body")

    def test_a_close_delimited_body_is_not_read_as_complete(self):
        """The interrupt must be OBSERVABLE, not inferred from the body shape.

        A close-delimited reply (no `Content-Length`, no `Transfer-Encoding`)
        declares no length — `HTTPResponse.length` is None, exactly as it is for a
        chunked body — but `read1` does NOT raise when the socket is shut down; it
        returns `b""`. Grading that as "the stream declared its end" reported
        `cut=False` for a body THIS reader had truncated, and a healthy
        close-delimited `/v1/team` reply then reached the no-identity arm as
        `❌ … Check your TORTOISE_API_URL` with exit 1 (measured at `fb2926ef9e`:
        360 bytes, `cut=False`, 0.50 s as shipped; `cut=True` with the watchdog
        disarmed — the interrupt itself caused the false red). The flag is what
        separates "the peer ended the body" from "we ended it".
        """
        import http.client
        import time

        from tortoise import __main__ as cli

        bound = 0.5
        port, srv = self._drip_chunk_size_server(drip_gap=0.05,
                                                 bytes_to_drip=200,
                                                 chunked=False)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=bound)
        try:
            conn.request("GET", "/v1/team")
            resp = conn.getresponse()
            assert resp.length is None, "a close-delimited body declares no length"
            started = time.monotonic()
            outcome = "returned"
            cut = False
            try:
                body, cut = cli._doctor_read_capped(
                    resp, cli._DOCTOR_BODY_CAP, started + bound)
                outcome = f"returned {len(body)} bytes cut={cut}"
            except (OSError, http.client.HTTPException) as exc:
                outcome = f"raised {type(exc).__name__}"
            elapsed = time.monotonic() - started
        finally:
            conn.close()
            srv.close()

        assert elapsed < 6.0, (
            f"one close-delimited read against a {bound} s deadline took "
            f"{elapsed:.2f} s ({outcome}) — the watchdog did not interrupt it")
        assert outcome.startswith("raised") or cut is True, (
            f"a body THIS reader truncated was reported COMPLETE ({outcome}) — "
            "a `b''` from an interrupt is not the peer's end of body")

    def test_an_http_error_body_is_bounded_the_same_way(self):
        """The THIRD call site reads an `HTTPError`, not an `HTTPResponse`.

        `urllib.error.HTTPError.__init__` sets `self.fp = fp` and that `fp` IS
        the `HTTPResponse`, so `getattr(resp.fp, "raw", None)` is None and a
        one-hop socket lookup never arms the watchdog here — measured on this
        shape: 10.05 s against a 0.5 s deadline, and no return at all while the
        drip continued. A 4xx/5xx whose body is server-chosen is exactly what the
        captive-portal/proxy case produces, so this site cannot be the unarmed
        one.

        The `HTTPError` is constructed around a REAL `HTTPResponse` rather than
        built by `urlopen`: this module's autouse fixture replaces `urlopen` with
        a fail-loudly stub, and the object graph (not urllib's dispatch) is what
        the socket walk has to get right.
        """
        import http.client
        import time
        import urllib.error

        from tortoise import __main__ as cli

        bound = 0.5
        port, srv = self._drip_chunk_size_server(drip_gap=0.05,
                                                 bytes_to_drip=200, status=500)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=bound)
        try:
            conn.request("GET", "/v1/team")
            raw = conn.getresponse()
            error = urllib.error.HTTPError(
                f"http://127.0.0.1:{port}/v1/team", raw.status, raw.reason,
                raw.headers, raw)
            assert isinstance(error.fp, http.client.HTTPResponse), (
                "the shape this test exists for: HTTPError.fp IS the response")
            started = time.monotonic()
            outcome = "returned"
            cut = False
            try:
                body, cut = cli._doctor_read_capped(
                    error, cli._DOCTOR_BODY_CAP, started + bound)
                outcome = f"returned {len(body)} bytes cut={cut}"
            except (OSError, http.client.HTTPException) as exc:
                outcome = f"raised {type(exc).__name__}"
            elapsed = time.monotonic() - started
        finally:
            conn.close()
            srv.close()

        assert elapsed < 6.0, (
            f"one HTTPError body read against a {bound} s deadline took "
            f"{elapsed:.2f} s ({outcome}) — the socket walk missed the "
            "HTTPError → HTTPResponse hop, so no watchdog was armed")
        assert outcome.startswith("raised") or cut, (
            f"the interrupted body was reported COMPLETE ({outcome})")


class TestDoctorHostedCredential:
    """#11912: `doctor` must be able to see the hosted half of the product.

    The defect this pins: with a REVOKED key in `~/.tortoise/credentials.json`,
    `doctor` printed `5 pass, 5 warn, 0 fail` and exited 0 while every hosted
    command answered `401 Invalid API key` in the same minute. The tool that is
    supposed to tell a user what is wrong certified a setup that could not make
    a single hosted call.
    """

    def test_no_credential_makes_no_request_and_says_so(
            self, clear_db_env, tmp_path, capsys, monkeypatch):
        """A keyless box: no `Hosted` row at all, zero network calls.

        This is the self-host install, and it is also #11912's target "0 network
        calls when no credential is configured" — a diagnostic that dials a host
        nobody configured is a probe against a system the user never named.
        """
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"no request may be made on a keyless box — dialled {url}"))
        db_path = str(tmp_path / "keyless.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor keyless seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert calls == []
        assert _row(out, "Hosted") is None, out[-1500:]
        cred = _row(out, "Credentials")
        assert cred is not None and "none configured" in cred, out[-1500:]
        assert "ℹ️" in cred
        assert "❌ Hosted" not in out  # no hosted failure row was invented

    def test_invalid_key_is_a_fail_not_a_pass(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """THE regression: a revoked key must not read `0 fail`.

        Before this block existed the same invocation printed
        `5 pass, 5 warn, 0 fail` and exited 0.
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, 401, "Unauthorized",
                            {"Content-Type": "application/json"},
                            __import__("io").BytesIO(
                                b'{"detail":"Invalid API key"}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "revoked.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor revoked seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "❌" in hosted, hosted
        assert "401" in hosted, hosted
        # The remedy the served refusal itself names — a verdict without a next
        # step is the raw-error-envelope pattern `team info` already fixed.
        assert "tortoise signup" in hosted and "tortoise recover" in hosted, hosted
        assert "config show" in hosted, hosted
        assert rc == 1, "a broken hosted credential must exit non-zero"
        # The secret itself must never reach the transcript.
        assert _FAKE_HOSTED_KEY not in out

    def test_valid_key_is_green_with_latency_and_identity(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A working identity is reported as working: latency, tier, org."""
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-9","tier":"anon"}')))
        db_path = str(tmp_path / "valid.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor valid seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "✅" in hosted, hosted
        assert "/v1/team accepted" in hosted, hosted
        # A MEASURED latency, not a bare OK: `… /health 200 in 0.00s; …`.
        import re as _re
        assert _re.search(r"/health 200 in \d+\.\d+s", hosted), hosted
        assert "org-9" in hosted and "tier anon" in hosted, hosted
        # #11912 names api_url and the org among the Credentials block's
        # indicators; both come from the STORE, so they are only reported if that
        # row carries them (fresh review round 8 — deleting them left the suite
        # green, and no other assertion looks at the store's own org).
        cred = _row(out, "Credentials")
        assert cred is not None, out[-1200:]
        assert "api_url https://api.premiselabs.co" in cred, cred
        assert "org agent-1" in cred, cred
        # ⛔ The row's PAYLOAD was entirely unpinned (fresh review round 15,
        # mutation-tested): the mask, the fingerprint, and the store path could
        # each be deleted — or the short-key withholding dropped in favour of
        # `_key_fingerprint` — with every test in the module green. The mask and
        # the fingerprint are the row's whole answer to "who am I", and the
        # withholding is a deliberate #5109 property (a 48-bit digest over a
        # short secret is brute-forceable offline), so all three are pinned.
        import re as _re2
        assert _re2.search(r"\(fingerprint [0-9a-f]{12}\)", cred), cred
        assert f"tt_…{_FAKE_HOSTED_KEY[-4:]}" in cred, cred
        # Which STORE won — the question `config show` exists to answer.
        assert str(_hermetic_hosted_identity / ".tortoise" / "credentials.json") in cred, cred
        # The row must be in the PASS column: downgrading it to ⚠️ would make
        # every healthy hosted box print "Some warnings" and nothing reds
        # (fresh review round 12 — no test asserted this icon).
        assert "✅" in cred, cred
        assert "pass," in out and "0 fail" in out, out[-800:]
        assert "❌ Hosted" not in out, out[-1200:]
        assert _FAKE_HOSTED_KEY not in out
        # The provenance note is CONDITIONAL on the winner being the checkout-local
        # store: made unconditional (`_cwd_provenance = True`) it would tell every
        # global-store and env user that their credential is a repo-local file
        # they do not have — a false statement about which store won, with the
        # suite still green (fresh review round 15). The positive direction is
        # pinned by the `./.tortoise` test.
        assert "checkout-local" not in hosted, hosted
        assert "#3660" not in hosted, hosted

    def test_a_short_key_withholds_its_fingerprint(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A short secret gets NO fingerprint — the deliberate #5109 withholding.

        `_publishable_fingerprint` withholds the digest below
        `_MIN_PUBLISHABLE_KEY_LEN`, because a 48-bit digest over a short secret is
        brute-forceable offline — a property this PR's own body calls out. Swapping
        it for `_key_fingerprint` published one for every key and nothing reded
        (fresh review round 15). The row must also not print half a secret: below
        the mask floor the only output is `(present)`.
        """
        short_secret = "short-secret"
        from tortoise.__main__ import _key_fingerprint
        _write_credential(_hermetic_hosted_identity, short_secret)
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-s"}')))
        db_path = str(tmp_path / "short-key.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor short key seed")

        _run_doctor([])
        out = capsys.readouterr().out

        cred = _row(out, "Credentials")
        assert cred is not None, out[-1500:]
        assert "fingerprint" not in cred, cred
        assert "(present)" in cred, cred
        assert short_secret not in out, "a short secret was printed"
        assert _key_fingerprint(short_secret) not in out, "the withheld digest was published"

    def test_offline_makes_no_request_and_says_not_probed(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`--offline` is the supported answer for a proxy / air-gapped runner.

        The row says "not probed" so "untested" can never be read as "fine".
        """
        import json as _json
        from pathlib import Path as _Path

        (_Path.cwd() / ".tortoise").write_text(_json.dumps(
            {"api_key": _FAKE_HOSTED_KEY, "api_url": "https://cwd.example.test"}))
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"--offline must make no request — dialled {url}"))
        db_path = str(tmp_path / "offline.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor offline seed")

        _run_doctor(["--offline"])
        out = capsys.readouterr().out

        assert calls == []
        hosted = _row(out, "Hosted")
        assert hosted is not None and "not probed" in hosted, out[-1500:]
        assert "--offline" in hosted
        # The docs say BOTH rows disclose a checkout-local credential; the offline
        # arm was the one that did not (fresh review round 10).
        assert "checkout-local" in hosted, hosted
        # The credential row is still resolved and printed — the offline answer
        # is "what would I send", which needs no network.
        assert _row(out, "Credentials") is not None

    def test_an_absent_offline_attribute_still_dials(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """An args object with NO `offline` means DIAL — pinned at the consumer.

        `_cmd_doctor` reads the flag with `getattr(args, "offline", False)` so
        that a bare `Namespace(cmd="doctor")` (#703) keeps the hosted probe ON.
        That default was pinned only through `_cmd_onboard`: flipping it to
        `True` at the `_doctor_hosted_rows(...)` call left the whole suite green
        (fresh review round 10), and it is exactly the false-clean #11912 exists
        to remove — such a caller would print "not probed" instead of dialling.
        This drives `_cmd_doctor` directly, so the default itself is asserted.
        """
        import argparse as _argparse

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        calls = _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-9"}')))
        db_path = str(tmp_path / "bare-ns.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor bare namespace seed")

        from tortoise.__main__ import _cmd_doctor
        _cmd_doctor(_argparse.Namespace(cmd="doctor"))
        out = capsys.readouterr().out

        # The probe RAN: an absent `offline` must not be read as "skip".
        assert calls, "a bare Namespace must still dial the hosted probe"
        assert any("/health" in url for url, _t in calls), calls
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "not probed" not in hosted, hosted
        assert "✅" in hosted or "❌" in hosted, hosted

    def test_a_spent_budget_stops_the_probe_instead_of_hanging(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A spent budget is ⚠️, not a hang — and it bounds leg STARTS.

        ⛔ What this pins, precisely: a leg the budget cannot fit is NOT BEGUN.
        It does NOT pin an in-flight read (a server dripping bytes just under the
        per-recv bound can still outlive the budget inside one leg) — that
        residual is filed as tortoise#12398 and named in the constant's comment,
        not claimed fixed. The row must say UNPROVEN — ⚠️, never `❌` + exit 1,
        which would blame the credential for a slow link (fresh review round 13).
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"no leg may be started past the budget — dialled {url}"))
        monkeypatch.setattr(
            __import__("tortoise.__main__", fromlist=["x"]), "_DOCTOR_TOTAL_BUDGET_S", 0.0)
        db_path = str(tmp_path / "budget.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor budget seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert calls == []
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "⚠️" in hosted, hosted
        assert "budget" in hosted and "UNPROVEN" in hosted, hosted
        assert "❌ Hosted" not in out, out[-1200:]
        assert rc == 0, "a slow link is not a failing credential"

    def test_the_report_names_the_bound_actually_used(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A truncated retry must be reported as truncated, not as the constant.

        The retry rows print the wait that was ATTEMPTED. When the total budget
        truncates a leg (`min(own, left)`), printing the configured 30 s reports
        a wait that never happened — an over-claim inside the verdict report, the
        surface a user reads to decide whether to re-run (fresh review round 14).
        Here the budget is 6 s. The stub raises without consuming its bound, so
        the retry's bound is the whole remaining budget (6 s) — and the row must
        say 6, never the configured 30. (A first leg that really spent its 5 s
        would leave ~1 s; the property under test is the same either way: the row
        names the bound it applied, not the constant.)
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            raise TimeoutError("timed out")

        _stub_hosted(monkeypatch, handler)
        import tortoise.__main__ as _m
        monkeypatch.setattr(_m, "_DOCTOR_TOTAL_BUDGET_S", 6.0)
        db_path = str(tmp_path / "used-bound.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor used bound seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "none in 6s on retry" in hosted or "none in 6.0s on retry" in hosted, hosted
        assert "none in 30s on retry" not in hosted, hosted

    def test_an_unreadable_health_body_is_never_green(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A `/health` body that could not be READ is not "no status" — it is UNREAD.

        The read is wrapped in `except (OSError, ValueError, TypeError,
        RecursionError, HTTPException)` and set `health_status = None`, which is
        the same value as "the body carried no `status` field". That collapse left
        `_health_notes` empty, so a service that sent 200 headers and then stalled
        (a saturated app, a streaming proxy) printed `✅ … /health 200` and
        `All checks passing!` with its own verdict never read — the
        reachable-but-unhealthy-reads-green class this note exists to remove
        (fresh review round 13). The transport error is recorded, scrubbed and
        escaped like every other server byte.
        """
        password = "hunter2pass"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test")

        class _StalledBody(_FakeResponse):
            def read(self, *_a, **_k):
                raise TimeoutError(
                    "the read timed out for https://svc:" + password + "@x/health")

            # `_read_capped` prefers `read1`; the stall must cover both entry
            # points or the fake silently answers "empty body" instead.
            def read1(self, *a, **k):
                return self.read(*a, **k)

        _stub_hosted(monkeypatch, lambda url, req: (
            _StalledBody(200) if url.endswith("/health")
            else _FakeResponse(200, b'{"org_id":"org-9","tier":"anon"}')))
        db_path = str(tmp_path / "unread.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor unread body seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "✅" not in hosted, hosted
        assert "⚠️" in hosted, hosted
        assert "could not be read" in hosted, hosted
        # The unread verdict suppresses the all-clear line.
        assert "All checks passing" not in out
        # Transport bytes are scrubbed and escaped like every other server value.
        assert password not in out

    def test_a_crashing_hosted_block_cannot_forge_a_row(
            self, clear_db_env, tmp_path, capsys, monkeypatch):
        """The crash arm's exception text is escaped, not interpolated raw.

        This is the one `except Exception` arm in the block, and it prints
        `str(e)[:80]` into a verdict row. It was the only interpolation in the
        diff that bypassed the file's own `_printable` rule (fresh review round
        12): a newline in an unexpected exception's message would fabricate a
        whole `✅ Graph: …` row in the report whose honesty is the point of
        #11912. The row still has to carry the diagnosis — the fix escapes, it
        does not delete.
        """
        db_path = str(tmp_path / "crash.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor crash seed")

        from tortoise import __main__ as m

        def boom(results, offline):
            raise RuntimeError("kaboom\n  ✅ Graph: health: 999 Points, forged")

        monkeypatch.setattr(m, "_doctor_hosted_rows", boom)
        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "❌" in hosted, hosted
        assert "kaboom" in hosted and "RuntimeError" in hosted, hosted
        # The newline is ESCAPED: no forged report line appears.
        assert "\n  ✅ Graph: health: 999 Points, forged" not in out
        assert "\\n" in hosted, hosted
        # A hosted verdict that could not be produced is never a passing one.
        assert rc == 1

    def test_graph_scoped_403_is_information_not_a_failure(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A graph-scoped key is refused BY DESIGN — it must not be called broken.

        `team info` already draws this line (`_ORG_INFO_REMEDIES`); a `❌` here
        would tell a correctly-scoped user to re-mint a working key.
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, 403, "Forbidden",
                            {"Content-Type": "application/json"},
                            __import__("io").BytesIO(
                                b'{"detail":{"error_code":"GRAPH_SCOPED_TEAM_SURFACE"}}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "scoped.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor scoped seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "❌" not in hosted, hosted
        # ⛔ The ICON is asserted, not just the absence of ❌: `_row()` returns the
        # first match and `✅` counts in the PASS column, so pinning only "no ❌"
        # let this refusal be flipped green with the suite still passing (fresh
        # review round 13).`
        assert "ℹ️" in hosted, hosted
        assert "✅" not in hosted, hosted
        assert "GRAPH_SCOPED_TEAM_SURFACE" in hosted, hosted
        assert "by design" in hosted or "deliberate" in hosted, hosted
        # A deliberate scope refusal must not contribute a FAIL to the report.
        assert "❌ Hosted" not in out, out[-1200:]

    def test_degraded_health_is_a_warning_not_a_pass(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A 200 whose body says `degraded` is not "the product works".

        The live hosted `/health` has returned `{"status":"degraded"}` while
        `/v1/team` answered 200 — the key works and the service does not. Neither
        a ✅ (which would hide it) nor a ❌ (which the user cannot fix) is honest.
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"degraded"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-9"}')))
        db_path = str(tmp_path / "degraded.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor degraded seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "⚠️" in hosted, hosted
        assert "degraded" in hosted, hosted
        assert "/v1/team accepted" in hosted, hosted

    def test_an_env_key_is_told_about_its_own_url_override(
            self, clear_db_env, tmp_path, capsys, monkeypatch):
        """The OTHER half of the source-aware remedy (fresh review round 19).

        `TORTOISE_API_URL` is the knob for an ENV-sourced key, so that row must
        keep naming it: making the remedy store-only would leave the env user
        without the one override they have.
        """
        monkeypatch.setenv("TORTOISE_API_KEY", _FAKE_ENV_KEY)
        monkeypatch.setenv("TORTOISE_API_URL", "http://127.0.0.1:1")

        def handler(url, req):
            raise ConnectionRefusedError("[Errno 111] Connection refused")

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "env-unreachable.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor env unreachable seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert "env:TORTOISE_API_KEY" in (_row(out, "Credentials") or ""), out[-1500:]
        hosted = _row(out, "Hosted")
        assert hosted is not None and "UNGRADED" in hosted, out[-1500:]
        assert "TORTOISE_API_URL" in hosted, hosted

    def test_env_key_names_its_source(self, clear_db_env, tmp_path, capsys,
                                     monkeypatch):
        """The env key is the highest-precedence candidate and is NAMED as such.

        "which store won" is the question `config show` exists to answer; a
        row that just says "a key is configured" leaves a store-shadowed user
        chasing the wrong file.
        """
        monkeypatch.setenv("TORTOISE_API_KEY", _FAKE_ENV_KEY)
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-2"}')))
        db_path = str(tmp_path / "env-key.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor env key seed")

        _run_doctor([])
        out = capsys.readouterr().out

        cred = _row(out, "Credentials")
        assert cred is not None and "env:TORTOISE_API_KEY" in cred, out[-1500:]
        assert _FAKE_ENV_KEY not in out

    def test_an_env_only_selfhost_static_key_is_never_transmitted(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`TORTOISE_API_KEY` alone is NOT a hosted identity — never dial with it.

        The SAME variable is the documented shared secret for a self-hosted
        `serve --http --auth static` (`.env.example:149-150`,
        `docs/quickstart-selfhosted.md:65`), and the resolver aims any env key at
        `TORTOISE_API_URL` or the production default. So an env-only static
        secret was sent to api.premiselabs.co, 401'd there, and read a HEALTHY
        self-host box as `❌ Hosted … the stored API key at this host is not
        valid` + exit 1 — #11912's false PASS mirrored into a false FAIL, with a
        `signup`/`recover` remedy that is wrong for a user with no hosted
        account. Found independently by two fresh reviewers (round 15); #1708
        §D1b is the recorded decision for exactly this shape.
        """
        static_secret = "local-static-key-for-my-own-serve"
        monkeypatch.setenv("TORTOISE_API_KEY", static_secret)
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"a self-host static key must never be transmitted — dialled {url}"))
        db_path = str(tmp_path / "static-auth.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor static auth seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert calls == [], "a local auth secret left the box"
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "not probed" in hosted, hosted
        assert "no request was made" in hosted, hosted
        # The remedy must name the way out, not `signup` for a self-hoster.
        assert "TORTOISE_API_URL" in hosted, hosted
        assert "signup" in hosted, hosted  # …IF it is a hosted key
        # ⚠️, never ❌: a correct self-host install is not a broken credential.
        assert "⚠️" in hosted and "❌ Hosted" not in out, out[-1200:]
        assert rc == 0, "a self-host static key must not fail the report"
        # The other half of #11912 still holds: the row names what WOULD be sent.
        cred = _row(out, "Credentials")
        assert cred is not None and "env:TORTOISE_API_KEY" in cred, out[-1500:]
        assert static_secret not in out, "the secret reached the transcript"

    def test_an_env_only_hosted_shaped_key_is_still_probed(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A `tt_`/`tk_` env key IS a hosted identity — the gate must not go quiet.

        The gate above is only safe if it is pinned in BOTH directions: without
        this, a fix for the self-host case could silence the
        `TORTOISE_API_KEY=<hosted key>` path, which `.env.example` calls the
        CANONICAL name for the hosted credential — and a revoked one would then
        read green, which is #11912 itself.
        """
        monkeypatch.setenv("TORTOISE_API_KEY", _FAKE_ENV_KEY)
        calls = _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-7"}')))
        db_path = str(tmp_path / "env-hosted.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor env hosted seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert [u for u, _t in calls if u.endswith("/health")], calls
        hosted = _row(out, "Hosted")
        assert hosted is not None and "✅" in hosted, out[-1500:]
        assert "/v1/team accepted" in hosted, hosted
        assert "not probed" not in hosted, hosted

    def test_an_env_key_with_an_explicit_api_url_is_probed(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """Naming the endpoint IS the hosted signal, even for a non-`tt_` key.

        A self-hoster pointing the CLI at their own `serve --http` box sets both
        variables; that is an explicit hosted identity (the resolver's own
        co-source rule keeps the env key with the env URL), so the probe must
        run against the endpoint the user named.
        """
        monkeypatch.setenv("TORTOISE_API_KEY", "local-static-key-for-my-own-serve")
        monkeypatch.setenv("TORTOISE_API_URL", "https://self.example.test")
        calls = _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else
            _FakeResponse(200, b'{"org_id":"org-8"}')))
        db_path = str(tmp_path / "env-explicit.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor env explicit seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert calls and calls[0][0] == "https://self.example.test/health", calls
        hosted = _row(out, "Hosted")
        assert hosted is not None and "✅" in hosted, out[-1500:]

    def test_a_read_error_is_scrubbed_before_it_is_truncated(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The display bound must land AFTER the scrub, never before it.

        A 200-char cut that slices through a userinfo password leaves a fragment
        `_scrub` can no longer match — measured at `e19d160c27` by swapping
        `_scrub(text)[:200]` for `_scrub(text[:200])`: the row printed
        `SUPERSECRE` (and `SUPERSECRETPASSWORD0` for a 1-char shift) while the
        whole module stayed green. The correct order and the leaking order are
        indistinguishable to every other test, which is why this one exists.
        `#5109` is the rule this file applies at every interpolation site.
        """
        password = "SUPERSECRETPASSWORD"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test/")
        # The rendered text is `f"{type(exc).__name__}: {exc}"` around a reason
        # that itself starts with `OSError: ` — 18 chars of prefix the padding
        # has to account for, or the cut lands one byte into the password and a
        # 1-char fragment slips past every assertion (measured: that is exactly
        # how the first version of this test passed under the leaking mutation).
        # Sized so the password STARTS at index 190: the 200-char bound then
        # retains its first 10 chars.
        reason = f"OSError: {'x' * (190 - 18 - 13)}https://svc:{password}@api.example.test/health"

        class _StallingBody(_FakeResponse):
            """Headers arrive, then the body read raises the URL-bearing error."""

            def __init__(self):
                super().__init__(200, b"")

            def read1(self, n: int = -1):
                raise OSError(reason)

            def read(self, n: int = -1):
                raise OSError(reason)

        def handler(url, req):
            if url.endswith("/health"):
                return _StallingBody()
            return _FakeResponse(200, b'{"org_id":"org-t"}')

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "scrub-order.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor scrub order seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert password not in out, "the api_url password reached the transcript"
        # No PREFIX of it either — a fragment is enough to brute-force the rest.
        for cut_len in (3, 6, 10, 14):
            assert password[:cut_len] not in out, (
                f"a {cut_len}-char fragment of the password survived: {password[:cut_len]!r}")
        assert "SUPERSECRE" not in out, out[-1200:]
        # …and the row still carries a usable diagnosis, not a scrubbed-to-nothing
        # fragment (scrubbing the WHOLE text keeps the head of the message).
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]

    def test_an_env_oauth_token_is_refused_with_its_own_remedy(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """An `oat_` token is MCP-only — this probe asks REST, so do not send it.

        The tree accepts a THIRD Bearer family: `oat_` OAuth 2.1 access tokens,
        "hosted-only, introspected … at the MCP boundary"
        (`tortoise/mcp_auth.py:1251-1258`), while REST `/v1/*` "stays
        key/JWT-only — `oat_` tokens are MCP-only"
        (`docs/adr/ADR-010-auth-planes-session-agent-key.md:62-64`). Probing one
        here is a guaranteed 401 BY DESIGN, and the generic env-key remedy
        ("set `TORTOISE_API_URL`") would then produce `❌ … re-mint it` for a
        working OAuth setup — a false FAIL for a correct configuration, the same
        defect class as the gate itself (fresh review at `e19d160c27`).
        """
        monkeypatch.setenv("TORTOISE_API_KEY", "oat_" + "a1b2c3d4e5f6" * 3)
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"an MCP-only OAuth token must not be sent to the REST probe — {url}"))
        db_path = str(tmp_path / "oat.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor oat seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert calls == [], "an MCP-only token was sent to a REST endpoint"
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "not probed" in hosted, hosted
        assert "OAuth" in hosted and "MCP" in hosted, hosted
        # The generic remedy would be a FALSE FAIL for this family: the REST
        # endpoint answers 401 to `oat_` by design, so it must not be offered.
        assert "set `TORTOISE_API_URL`" not in hosted, hosted
        assert "⚠️" in hosted and "❌ Hosted" not in out, out[-1200:]
        assert rc == 0

    def test_the_hosted_prefixes_match_the_auth_contract(self):
        """`_HOSTED_KEY_PREFIXES` must stay the ONE prefix contract.

        `tortoise/auth.py` owns the definition (`API_KEY_PREFIXES`) and
        `__main__` cannot import it — that module RAISES at import time when
        `TORTOISE_API_KEY` is set without `TORTOISE_SECRET_PEPPER`, which is the
        self-host box the gate protects. So the duplicate is pinned here instead:
        a new prefix added to the mint (`key create`) must not silently leave
        `doctor` refusing to probe those keys.
        """
        from tortoise import auth as _auth
        from tortoise.__main__ import _HOSTED_KEY_PREFIXES

        assert _HOSTED_KEY_PREFIXES == _auth.API_KEY_PREFIXES

    def test_trailing_slash_in_api_url_does_not_fake_a_broken_key(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A store/proxy endpoint ending in `/` must not read as a bad credential.

        `f"{api_url}/v1/team"` on `https://host/` asks for `//v1/team`, which the
        server answers 404 — the offline reporter's own failure mode, and the
        reason every other hosted caller in this file rstrips the slash. Fresh
        review of this change reproduced it before the fix.
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url="https://api.example.test/")
        seen: list[str] = []

        def handler(url, req):
            seen.append(url)
            return (_FakeResponse(200, b'{"status":"ok"}')
                    if url.endswith("/health") else
                    _FakeResponse(200, b'{"org_id":"org-3"}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "slash.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor slash seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert "//health" not in " ".join(seen) and "//v1/team" not in " ".join(seen), seen
        hosted = _row(out, "Hosted")
        assert hosted is not None and "✅" in hosted, out[-1500:]
        assert "accepted" in hosted, hosted

    def test_transport_failure_scrubs_a_url_password(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """An unreachable endpoint must not echo the api_url's PASSWORD.

        `http.client` interpolates the URL into its own exception text, so a
        userinfo-bearing endpoint — realistic behind a proxy, which is exactly
        the setup `--offline` advertises — would print the secret. Fixed in the
        same review round; `_scrub` is the file's one rule for this (#5109).
        """
        password = "hunter2pass"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test/")

        def handler(url, req):
            raise ValueError(f"nonnumeric port: '{password}@api.example.test'")

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "scrub.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor scrub seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None and "⚠️" in hosted, out[-1500:]
        assert password not in out, "the api_url password reached the transcript"
        # Unreachable is UNGRADED, not a broken key (round 12): a refused socket
        # asked nothing of the credential, and the same event on the authenticated
        # leg is ⚠️ too.
        assert "UNGRADED" in hosted, hosted
        # ⛔ The remedy names the knob that CAN move this endpoint (fresh review
        # round 19). This credential is STORE-sourced, and `_resolve_config_path`
        # takes a file-sourced key's URL from the same file or the built-in
        # default (#2369 D1.1) — so naming `TORTOISE_API_URL` here aimed the user
        # at a knob that cannot change the result. Measured under the exact
        # regression (remedy made unconditional for both sources): 197 passed.
        assert "TORTOISE_API_URL" not in hosted, hosted
        assert "config show" in hosted, hosted
        # EXACTLY ONE `Hosted` row (fresh review at `77c1f364b6`): `_row()` returns
        # the FIRST match, so without this line deleting the `return` that
        # follows this arm leaves the mutation SILENT — this `except` block's
        # non-timeout guard sits ABOVE the timeout-retry code, so the fall-through
        # does not reach the team leg: it re-enters the retry, asks the endpoint a
        # second time after a definitive refusal (a non-retryable `ValueError`
        # reported as "no answer in 5s, and none in 30s on retry"), and prints a
        # SECOND, contradictory `⚠️ Hosted` row. Measured under that exact
        # mutation: `9 pass, 5 warn, 0 fail` — rc UNCHANGED at 0 — so the row
        # count is the only signal there is, not an exit code (fresh review at
        # `2e8bc5b132` corrected an earlier claim that this reds with a `❌` row
        # and exit 1; both are wrong, and the assertion below still reds).
        assert len([ln for ln in out.splitlines() if " Hosted:" in ln]) == 1, out[-1500:]
        assert rc == 0

    def test_health_5xx_with_a_working_key_is_a_warning(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A reachable-but-broken `/health` must not render `✅ … All checks passing!`.

        The pass arm keyed on `/v1/team` alone, so a `/health` 500 with a 200 on
        `/v1/team` printed a full green report (fresh review of this change).
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            if url.endswith("/health"):
                raise HTTPError(url, 500, "Server Error", {}, None)
            return _FakeResponse(200, b'{"org_id":"org-4"}')

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "health5xx.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor health5xx seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "⚠️" in hosted, hosted
        assert "/health returned 500" in hosted, hosted
        assert "/v1/team accepted" in hosted, hosted

    def test_cwd_store_is_isolated_from_the_checkout(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The fixture must move the `<cwd>/.tortoise` candidate out of the checkout.

        `tortoise init --api-key` writes `./.tortoise` into the CURRENT DIRECTORY
        (docs/quickstart-cloud.md §3) and it outranks the home store, so a
        checkout that ever ran it would hand this whole module a live credential —
        and the keyless test below would dial it (fresh review of this change
        measured exactly that).
        """
        import json as _json
        from pathlib import Path as _Path

        from tortoise.__main__ import _resolve_config_path  # the ladder under test

        repo_root = _Path(__file__).resolve().parent.parent
        # (i) the fixture moved us OUT of the checkout, so a `./.tortoise` sitting
        #     there (gitignored, a normal untracked artifact) can never win.
        assert _Path.cwd().resolve() != repo_root.resolve()
        assert not (_Path.cwd() / ".tortoise").exists()
        # (ii) the candidate is still LIVE at the fixture's cwd — the guarantee is
        #      "isolated", never "disabled" — so this test would catch a fixture
        #      that neutered the ladder instead of moving it.
        (_Path.cwd() / ".tortoise").write_text(
            _json.dumps({"api_key": _FAKE_ENV_KEY, "api_url": "https://cwd.example.test"}))
        _path, _cfg, key, url = _resolve_config_path()
        assert key == _FAKE_ENV_KEY
        assert url == "https://cwd.example.test"

    def test_health_timeout_is_retried_before_reporting_a_broken_endpoint(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A cold-starting app must not read as a broken endpoint (exit 1).

        The hosted app wakes slowly (>10s measured — the reason `team info`
        retries), and `/health` is bounded at 5s, so a first attempt that times
        out on a HEALTHY box used to print `cannot reach …` and exit 1 with a
        remedy aimed at the user's config. Fresh review of this change measured
        exactly that; the leg is now retried once at the wider bound.
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        seen: list[str] = []

        def handler(url, req):
            seen.append(url)
            if url.endswith("/health") and len([u for u in seen if u.endswith("/health")]) == 1:
                raise TimeoutError("timed out")
            return (_FakeResponse(200, b'{"status":"ok"}')
                    if url.endswith("/health") else
                    _FakeResponse(200, b'{"org_id":"org-5"}'))

        calls = _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "cold.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cold start seed")

        _run_doctor([])
        out = capsys.readouterr().out

        # The BOUNDS, not just the count: a retry at the same 5 s would still
        # "retry" while re-introducing the cold-start failure this exists for
        # (fresh review round 5 — `Request` has no `.timeout`, so the old
        # assertion recorded None and pinned nothing).
        health_calls = [(u, t) for u, t in calls if u.endswith("/health")]
        _assert_retry_widens([t for _u, t in health_calls],
                             _EXPECTED_HEALTH_TIMEOUT_S,
                             _EXPECTED_TEAM_RETRY_TIMEOUT_S)
        hosted = _row(out, "Hosted")
        assert hosted is not None and "✅" in hosted, out[-1500:]
        assert "first attempt timed out" in hosted, hosted
        assert "❌ Hosted" not in out, out[-1200:]

    def test_retry_failure_scrubs_a_url_password(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The RETRY row's reason is a THIRD scrub site, and it must be pinned.

        The first `/health` attempt is pinned by
        `test_transport_failure_scrubs_a_url_password` and the `/v1/team` leg by
        round 7 — but the retry row interpolates `str(retry_reason)` through
        `_scrub` as well, because `http.client`/a proxy embeds the request URL in
        its own message and a userinfo `api_url` embeds the PASSWORD in that URL.
        Reduced to a plain `str(...)` the suite stayed green, while a proxy
        endpoint — exactly the `--offline` audience — printed the secret in the
        very row that masks that URL two fields earlier (#5109; fresh review at
        `77c1f364b6`).
        """
        password = "retrypw9x"
        endpoint = f"https://svc:{password}@api.example.test/"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY, api_url=endpoint)

        def handler(url, req):
            # BOTH `/health` attempts fail as a TIMEOUT (only a timeout is
            # retried), carrying the userinfo URL the way a real client would.
            raise TimeoutError(f"timed out reading {url}")

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "retryscrub.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor retry scrub seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "on retry" in hosted, hosted
        assert password not in out, "the api_url password reached the retry row"
        # Unreachable is UNGRADED, not a broken key: nothing was asked of it.
        assert "UNGRADED" in hosted, hosted
        # …and EXACTLY ONE `Hosted` row: the `return` after this retry arm is a
        # surviving mutation too (fresh review at `2e8bc5b132` measured `100
        # passed` with it deleted). Losing it prints a SECOND row — the
        # authenticated leg's — against an endpoint whose `/health` never
        # answered, i.e. it sends the `Bearer` key to a host that just failed
        # reachability and spends an extra leg of the probe budget (#5109's
        # transmission rule, and the budget pin). `_row()` takes the FIRST
        # match, so only a COUNT can catch it.
        assert len([ln for ln in out.splitlines() if " Hosted:" in ln]) == 1, out[-1500:]
        assert rc == 0

    def test_a_refused_retry_is_not_reported_as_a_wait(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A retry exists because the FIRST attempt timed out — that says nothing
        about why the retry failed, and the row must not assume it timed out too.

        The row printed "and none in 30s on retry" for an instant
        `ECONNREFUSED`, asserting a wait that never happened and sending the
        reader after a slow network when the endpoint is refusing in
        microseconds (fresh review round 16; round 14 fixed which NUMBER the row
        prints, not this claim).
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url="https://api.example.test")
        seen_health: list[str] = []

        from urllib.error import URLError

        def handler(url, req):
            if url.endswith("/health"):
                seen_health.append(url)
                if len(seen_health) == 1:
                    raise TimeoutError("timed out")
                # ⛔ The WRAPPED shape `urlopen` actually raises on the wire: a
                # refusal arrives as `URLError(ConnectionRefusedError(...))`, and
                # `isinstance(URLError(...), ConnectionRefusedError)` is False —
                # so the bucket is only right because of the caller's `.reason`
                # unwrap. Injecting the bare exception let that unwrap be deleted
                # with the suite green (fresh review round 18).
                raise URLError(ConnectionRefusedError(
                    "[Errno 111] Connection refused"))
            return _FakeResponse(200, b'{"org_id":"org-r"}')

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "refused-retry.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor refused retry seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert len(seen_health) == 2, "the retry must still be attempted"
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "refused on retry" in hosted, hosted
        assert "and none in" not in hosted, hosted
        assert "Connection refused" in hosted, hosted
        # The FIRST bound, not the retry's: naming the 30 s retry bound here
        # would report a wait the reader cannot map to an attempt (round 14).
        assert "timed out at 5s" in hosted, hosted
        assert rc == 0, out[-800:]

    def test_a_refused_team_retry_is_not_reported_as_a_wait(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The `/v1/team` retry leg carries the same claim — pin it separately.

        Both legs route through `_retry_wait_claim`, but only the `/health` leg
        had a test: reverting the authenticated leg to the unconditional wait
        left the whole suite green (fresh review round 17 measured `110 passed`),
        so a garbled sentence or a wrong bound in it was invisible to CI.
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url="https://api.example.test")
        seen_team: list[str] = []

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            seen_team.append(url)
            if len(seen_team) == 1:
                raise TimeoutError("timed out")
            from urllib.error import URLError
            raise URLError(ConnectionRefusedError(
                "[Errno 111] Connection refused"))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "team-refused-retry.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor team refused retry seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert len(seen_team) == 2, "the retry must still be attempted"
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "refused on retry" in hosted, hosted
        # The FIRST bound is the one that timed out; naming the 30 s retry bound
        # here would assert a wait the reader cannot map to an attempt.
        assert "timed out at 10s" in hosted, hosted
        assert "and none in" not in hosted, hosted
        assert rc == 0, out[-800:]

    @pytest.mark.parametrize("leg,first_bound", [("health", "5s"), ("team", "10s")])
    def test_a_non_refusal_retry_failure_is_not_called_a_refusal(
            self, leg, first_bound, clear_db_env, tmp_path, capsys, monkeypatch,
            _hermetic_hosted_identity):
        """A DNS/TLS/closed-connection failure is not the endpoint REFUSING.

        Splitting the world into "timeout" and "refused" replaced one
        unmeasured claim with another: a `gaierror`, an expired certificate, a
        `RemoteDisconnected` and a `ValueError` all rendered "refused on retry",
        and the row's remedy then blames the endpoint/proxy while the real cause
        needs a different fix (fresh review round 17). Only
        `ConnectionRefusedError` is a refusal.

        Parametrised over BOTH legs, and each leg asserts the FIRST bound in the
        sentence: the `failed=` strings were the other unpinned half — a row
        claiming "timed out at 30s" shipped with the suite green (round 18).
        """
        import socket

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url="https://api.example.test")
        seen: list[str] = []

        def handler(url, req):
            is_health = url.endswith("/health")
            if (leg == "health") != is_health:
                return (_FakeResponse(200, b'{"status":"ok"}') if is_health
                        else _FakeResponse(200, b'{"org_id":"org-g"}'))
            seen.append(url)
            if len(seen) == 1:
                raise TimeoutError("timed out")
            raise socket.gaierror(-2, "Name or service not known")

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / f"dns-retry-{leg}.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, f"doctor dns retry {leg} seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert len(seen) == 2, f"the {leg} retry must still be attempted"
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "the retry failed" in hosted, hosted
        assert "refused on retry" not in hosted, hosted
        assert "Name or service not known" in hosted, hosted
        assert f"timed out at {first_bound}" in hosted, hosted
        assert rc == 0, out[-800:]

    def test_an_empty_reason_names_the_exception_type(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`RemoteDisconnected` stringifies EMPTY — the row must still say what.

        A peer that accepts and closes without a response raises
        `http.client.RemoteDisconnected` whose `str()` is `''` (verified against
        the real `urllib`), so the row rendered `cannot reach … — .` on the first
        attempt and `… timed out at 5s ().` on a retry: a diagnostic carrying no
        diagnosis, on a common proxy/restart shape (fresh review round 18).
        """
        import http.client

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url="https://api.example.test")
        seen_health: list[str] = []

        def handler(url, req):
            if url.endswith("/health"):
                seen_health.append(url)
                if len(seen_health) == 1:
                    raise TimeoutError("timed out")
                raise http.client.RemoteDisconnected()
            return _FakeResponse(200, b'{"org_id":"org-e"}')


        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "empty-reason.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor empty reason seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "RemoteDisconnected" in hosted, hosted
        assert "()" not in hosted, hosted

    def test_control_bytes_in_api_url_cannot_forge_a_row(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`api_url` comes from a store; a newline in it must not fabricate rows.

        `<cwd>/.tortoise` is attacker-controllable by this module's own reckoning,
        and `api_url` is interpolated into every hosted row — a newline printed a
        fake `✅ Graph: health: 999 Points` line inside `doctor`'s report (fresh
        review of this change measured it).
        """
        forged = "https://api.example.test\n  ✅ Graph: health: 999 Points, forged"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY, api_url=forged)
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else _FakeResponse(200, b'{"org_id":"org-6"}')))
        db_path = str(tmp_path / "forge.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor forge seed")

        _run_doctor([])
        out = capsys.readouterr().out

        # `_printable` renders the injection as a literal backslash-n, so the
        # forged text stays INLINE in the Hosted row and never becomes its own
        # results line. `Graph: health` is printed exactly once by doctor, so a
        # second such results row is the forgery succeeding.
        import re as _re
        # Rows only: `<icon> <label>: <detail>`. Matched on the LABEL so the
        # escaped text sitting inline in the Hosted/Credentials detail (which
        # also names "Graph: health:") is not counted as a row of its own.
        forged_rows = [ln for ln in out.splitlines()
                       if _re.match(r"^\s+\S+\s+Graph: health:", ln)]
        assert len(forged_rows) == 1, out[-1500:]
        assert "\\n" in out, "the newline should be escaped, not executed"

    def test_suspended_org_keeps_its_appeal_path(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A 403 SUSPENDED must route through `_suspended_info`, not the generic arm.

        Both a suspension and a graph-scope refusal ride a 403; the module's own
        rule is that SUSPENDED wins, because an appeal URL is the only actionable
        fact. The generic arm truncated the body at 200 chars, so a realistic
        suspension (a sentence of message before the URL) had its appeal URL cut
        in half — the one fact the operator cannot reconstruct (fresh review of
        this change measured it).
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        appeal = "https://app.premiselabs.co/appeal?org=abc123"
        long_message = ("This organization has been suspended due to unusual "
                        "activity on the account, and an operator must review it. ") * 3
        payload = ('{"detail": {"code": "SUSPENDED", "message": "'
                   + long_message.strip() + '", "appeal_url": "'
                   + appeal + '"}}').encode()
        assert payload.index(appeal.encode()) > 200, (
            "the fixture must place the appeal URL PAST the truncation boundary, "
            "or the pre-fix code would print it by luck")

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, 403, "Forbidden", {}, __import__("io").BytesIO(payload))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "suspended.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor suspended seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "suspended" in hosted.lower(), hosted
        assert appeal in hosted, hosted
        # A suspension is a FAIL: ℹ️ would count in neither column, so the report
        # would read `0 fail` / exit 0 for the one refusal whose remedy is a human
        # appeal (fresh review round 8 — both were pinned by nothing).
        assert "❌" in hosted, hosted
        assert rc == 1

    def test_a_refused_credential_store_is_a_fail_not_a_quiet_warning(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A CORRUPT store makes every command fail, so `doctor` must not exit 0.

        Without this arm the block's own `except Exception` renders a
        `⚠️ Hosted: check unavailable: …` and returns — a warning does not fail
        the report, so the pre-#11912 defect (green on a box where nothing hosted
        works) comes straight back. Fresh review round 2 named this branch as
        deletable with the suite green.
        """
        store = _hermetic_hosted_identity / ".tortoise"
        store.mkdir(parents=True, exist_ok=True)
        (store / "credentials.json").write_text("{ this is not json")
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"a refused store must not be dialled — requested {url}"))
        db_path = str(tmp_path / "refused.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor refused store seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert calls == []
        cred = _row(out, "Credentials")
        assert cred is not None and "❌" in cred, out[-1500:]
        assert "refused" in cred and "config show" in cred, cred
        assert rc == 1

    def test_an_unsendable_key_is_refused_before_http_client_echoes_it(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A CR/LF key must be refused by US, not by `http.client`.

        `http.client` raises `ValueError("Invalid header value b'Bearer <THE
        KEY>'")` — the refusal itself echoes the credential into the report.
        """
        bad_key = _FAKE_HOSTED_KEY + "\r\nX-Injected: 1"
        _write_credential(_hermetic_hosted_identity, bad_key)
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"an unsendable key must not be dialled — requested {url}"))
        db_path = str(tmp_path / "unsendable.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor unsendable seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        assert calls == []
        cred = _row(out, "Credentials")
        assert cred is not None and "❌" in cred, out[-1500:]
        assert "cannot be sent in a request header" in cred, cred
        assert rc == 1

    def test_team_leg_timeout_is_retried_then_reported_unproven(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The authenticated leg's cold-start retry, and its exhausted case.

        The retry is the leg the block's comment says cold start lands on, so it
        needs its own pin: a single attempt that times out must NOT read as a
        refusal, and two must report the credential as UNPROVEN.
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise TimeoutError("timed out")

        all_calls = _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "team-timeout.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor team timeout seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        team_calls = [(u, t) for u, t in all_calls if u.endswith("/v1/team")]
        assert len(team_calls) == 2, team_calls  # one attempt + one bounded retry
        _assert_retry_widens([t for _u, t in team_calls],
                             _EXPECTED_TEAM_TIMEOUT_S,
                             _EXPECTED_TEAM_RETRY_TIMEOUT_S)
        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "no answer in 10s" in hosted and "on retry" in hosted, hosted
        # `/health` ANSWERED, so an exhausted authenticated leg is a service
        # blip: the key is unproven, not wrong — ⚠️, never `❌` (round 7).
        assert "⚠️" in hosted and "❌" not in hosted, hosted
        assert rc == 0

    def test_a_2xx_without_an_identity_is_not_an_accepted_key(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A captive portal answers 200 too; only a READ identity proves the key.

        `TORTOISE_API_URL` aimed at a proxy that returns 200 for everything (or
        at the wrong service) used to render `✅ … accepted` and exit 0 — a green
        report on a box whose hosted commands all fail (fresh review round 2).
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        for body in (b"", b"<html>Please sign in to the network</html>"):
            def handler(url, req, _body=body):
                if url.endswith("/health"):
                    return _FakeResponse(200, b'{"status":"ok"}')
                return _FakeResponse(200, _body)

            _stub_hosted(monkeypatch, handler)
            db_path = str(tmp_path / f"soft200-{len(body)}.db")
            monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
            _seed_db(db_path, "doctor soft200 seed")
            capsys.readouterr()

            rc = _run_doctor([])
            out = capsys.readouterr().out

            hosted = _row(out, "Hosted")
            assert hosted is not None and "❌" in hosted, (body, out[-1200:])
            assert "no team identity" in hosted, hosted
            assert rc == 1

    def test_offline_never_prints_all_checks_passing(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`--offline` on a configured box must not end `✅ All checks passing!`.

        The offline row is a WARNING for exactly this reason: `_cmd_doctor` counts
        ℹ️ in neither column, so a green summary printed beside "hosted validity
        was NOT tested" is #11912's target reachable through the new flag (fresh
        review round 3).
        """
        import json as _json
        from pathlib import Path as _Path

        (_Path.cwd() / ".tortoise").write_text(_json.dumps(
            {"api_key": _FAKE_HOSTED_KEY, "api_url": "https://cwd.example.test"}))
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"--offline must make no request — dialled {url}"))
        db_path = str(tmp_path / "offline-summary.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor offline summary seed")

        _run_doctor(["--offline"])
        out = capsys.readouterr().out

        assert calls == []
        hosted = _row(out, "Hosted")
        assert hosted is not None and "⚠️" in hosted, out[-1200:]
        assert "All checks passing" not in out, out[-1200:]

    def test_a_checkout_local_credential_is_probed_and_its_provenance_disclosed(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`./.tortoise` IS dialled — and the row says the credential is repo-local.

        `tortoise init --api-key` writes `./.tortoise` and the hosted quickstart
        tells the user to run the CLI from that directory, so skipping it left the
        DOCUMENTED hosted layout with a warning and exit 0 on a revoked key — the
        pre-#11912 defect back on the most common path (fresh review round 4). The
        beacon/provenance concern is answered by disclosure instead: the verdict is
        kept, and the reader is told which file supplied the identity.
        """
        import json as _json
        from pathlib import Path as _Path

        (_Path.cwd() / ".tortoise").write_text(_json.dumps(
            {"api_key": _FAKE_HOSTED_KEY, "api_url": "https://repo.example.test"}))
        seen: list[str] = []

        def handler(url, req):
            seen.append(url)
            return (_FakeResponse(200, b'{"status":"ok"}')
                    if url.endswith("/health") else
                    _FakeResponse(200, b'{"org_id":"org-cwd"}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "cwd-store.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cwd store seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert seen, "a checkout-local credential is the one this shell would use"
        hosted = _row(out, "Hosted")
        assert hosted is not None and "✅" in hosted, out[-1200:]
        assert "checkout-local" in hosted, hosted
        assert "accepted" in hosted, hosted
        assert "❌ Hosted" not in out, out[-1200:]

    def test_the_checkout_local_note_appears_exactly_once_on_every_row(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The provenance note rides EVERY row naming the target, and only once.

        Two regressions this pins at once (fresh review round 16): the
        authenticated-leg transport row appended it on top of `_combine`'s own
        append (a 636-char row carrying the disclosure TWICE), and the `/health`
        refusal row disclosed nothing at all — while the function's comment and
        `docs/quickstart-cloud.md` both promise the `Hosted` row names the file.
        Neither shape was pinned: no test counted occurrences.
        """
        import json as _json
        from pathlib import Path as _Path

        (_Path.cwd() / ".tortoise").write_text(_json.dumps(
            {"api_key": _FAKE_HOSTED_KEY, "api_url": "https://repo.example.test"}))

        # (a) `/health` refuses → the reachability refusal row.
        def refuse(url, req):
            raise ConnectionRefusedError("[Errno 111] Connection refused")

        _stub_hosted(monkeypatch, refuse)
        db_path = str(tmp_path / "note-refusal.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor note refusal seed")
        _run_doctor([])
        out = capsys.readouterr().out
        hosted = _row(out, "Hosted")
        assert hosted is not None and "⚠️" in hosted, out[-1200:]
        assert hosted.count("checkout-local") == 1, hosted

        # (b) `/health` answers, the authenticated leg resets → the OTHER row.
        def reset(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise ConnectionResetError("[Errno 104] Connection reset by peer")

        _stub_hosted(monkeypatch, reset)
        _run_doctor([])
        out2 = capsys.readouterr().out
        hosted2 = _row(out2, "Hosted")
        assert hosted2 is not None and "⚠️" in hosted2, out2[-1200:]
        assert hosted2.count("checkout-local") == 1, hosted2

    def test_a_checkout_local_credential_still_fails_when_the_key_is_revoked(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The documented layout must still red on a revoked key (`./.tortoise`)."""
        import json as _json
        from pathlib import Path as _Path
        from urllib.error import HTTPError

        (_Path.cwd() / ".tortoise").write_text(_json.dumps(
            {"api_key": _FAKE_HOSTED_KEY, "api_url": "https://repo.example.test"}))

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, 401, "Unauthorized", {},
                            __import__("io").BytesIO(b'{"detail":"Invalid API key"}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "cwd-revoked.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cwd revoked seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None and "❌" in hosted, out[-1200:]
        assert "401" in hosted, hosted
        assert rc == 1

    def test_a_transient_service_refusal_is_not_a_broken_credential(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`503 graph_unavailable` is a SERVICE refusal, not a bad key.

        `/v1/team` runs a usage count, so it answers 503 on a saturated pool and
        429 under rate limiting — the same transients `_cmd_init` retries. A `❌`
        (with a remedy aimed at the user's config) reds `doctor`, and anything
        gating on its exit code, during a capacity blip (fresh review round 4).
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, 503, "Service Unavailable", {},
                            __import__("io").BytesIO(
                                b'{"detail":{"error_code":"graph_unavailable"}}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "transient.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor transient seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "⚠️" in hosted and "❌" not in hosted, hosted
        assert "503" in hosted and "UNPROVEN" in hosted, hosted
        # A service-side refusal must not contribute a FAIL either.
        assert "❌ Hosted" not in out, out[-1200:]

    @pytest.mark.parametrize("status", [429, 408])
    def test_a_rate_limit_is_the_same_service_refusal_as_a_5xx(
            self, status, clear_db_env, tmp_path, capsys, monkeypatch,
            _hermetic_hosted_identity):
        """`429`/`408` are SERVICE refusals, not a bad key (#11912 review).

        The arm reads `team_code in (408, 429) or 500 <= team_code < 600`, and
        only the 5xx half was pinned: the rate-limit half — the one the arm's
        own comment names first, and the one `/v1/team`'s usage count actually
        produces — could be deleted with the suite green. A `429` rendered as a
        `❌` would red `doctor`, and anything gating on its exit code, for a
        healthy key that was merely throttled.
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, status, "refused", {},
                            __import__("io").BytesIO(
                                b'{"detail":{"error_code":"rate_limited"}}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / f"transient-{status}.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor transient seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "⚠️" in hosted and "❌" not in hosted, hosted
        assert str(status) in hosted and "UNPROVEN" in hosted, hosted
        assert "❌ Hosted" not in out, out[-1200:]
        assert rc == 0, "a throttled key must not fail the report"

    def test_reply_bodies_are_read_with_a_bound(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A proxy streaming a huge body must not be buffered whole.

        `resp.read()` then `len(...)` bounds the TERMINAL, not the process — on a
        memory-tight box that is how `doctor` (the verb `onboard` ends with) gets
        OOM-killed. The cap has to be applied AT the read.
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        caps_seen: list[int | None] = []

        class _BoundedStream(io.BytesIO):
            """A real file-like body that RECORDS the size of each read.

            `io.BytesIO` on purpose: `HTTPError` wraps its `fp` in
            `tempfile._TemporaryFileWrapper`, whose finalizer calls `close()` —
            a duck-typed object without it raises an unraisable AttributeError on
            every run (fresh review round 10), which is also why this test was
            the only thing blocking `-W error::PytestUnraisableExceptionWarning`.
            """

            def __init__(self, data: bytes, sink: list):
                super().__init__(data)
                self._sink = sink
                self.served = 0

            @property
            def length(self):
                """Remaining DECLARED length, like `http.client.HTTPResponse`.

                A real `HTTPError` delegates `length` to its `fp`, so a fake
                without it cannot express the cut shapes production sees.
                """
                return self.getbuffer().nbytes - self.tell()

            def read(self, n=-1):
                self._sink.append(None if n is None or n < 0 else n)
                data = super().read(n)
                self.served += len(data)
                return data

            def read1(self, n=-1):
                # The error-body leg reaches this stream through
                # `HTTPError.__getattr__ → fp`, so a recorder on `read` alone saw
                # NOTHING of the leg the cap is asserted on (fresh review round
                # 18).
                self._sink.append(None if n is None or n < 0 else n)
                data = super().read1(n)
                self.served += len(data)
                return data

        class _BoundedResponse(_FakeResponse):
            def read(self, n=None):
                # RECORD, never raise: a raise here happens INSIDE production
                # code, where `_cmd_doctor`'s own `except Exception` turns it into
                # a ⚠️ "check unavailable" row and the assertion is swallowed —
                # removing the cap stayed green through that path (fresh review
                # round 6). Asserting on the recorded values afterwards is the
                # only shape that can fail.
                caps_seen.append(None if n is None or n < 0 else n)
                return super().read(-1 if n is None else n)

            def read1(self, n=None):
                # `_read_capped` prefers `read1`; record through it as well.
                return self.read(n)

        # A body LARGER than the cap on the ERROR leg: the cap has to CUT it, not
        # merely bound each call. `all(0 < n <= 65536)` passed for a 1-byte chunk
        # loop, a cap raised to 10 MB and a cap lowered to 1 alike (fresh review
        # round 18).
        error_stream = _BoundedStream(b"x" * (65536 + 4096), caps_seen)

        def handler(url, req):
            if url.endswith("/health"):
                return _BoundedResponse(200, b'{"status":"ok"}')
            # The ERROR-body read is capped too (fresh review round 8): a proxy
            # that answers 4xx with a large page is the exact case the cap exists
            # for, and it was unpinned.
            from urllib.error import HTTPError
            raise HTTPError(url, 400, "Bad Request", {}, error_stream)

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "bounded.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor bounded read seed")

        _run_doctor([])
        out = capsys.readouterr().out

        # Every read must be BOUNDED (a positive int, at most one chunk): the
        # reader walks the body in chunks so a dripping proxy cannot hold a
        # started leg open, which is why an exact call-size list is the wrong
        # assertion. 4096 is the chunk size; asserting against the CAP alone let a
        # 1-byte-per-call loop pass.
        assert caps_seen, caps_seen
        assert all(isinstance(n, int) and 0 < n <= 4096 for n in caps_seen), (
            "an unbounded read() reached the wire on some leg — the body cap is "
            f"not applied: {sorted(set(caps_seen))[:8]}")
        # …and the CAP itself is exact and pinned to its documented value: a body
        # larger than it is cut AT it. Nothing else in the tree names 65536, so
        # without this a cap of 10 MB reads as green.
        assert error_stream.served == 65536, error_stream.served
        hosted = _row(out, "Hosted")
        # ⛔ This 400 body is 69,632 bytes — OVER the cap — so the fragment is
        # NOT evidence: the row must say CUT/UNPROVEN rather than grade the
        # refusal from 64 KiB of it (fresh review round 21 corrected my own
        # comment here, which called the same body "small and complete").
        assert hosted is not None, out[-1200:]
        assert "CUT" in hosted, hosted
        assert "❌" not in hosted, hosted

    def test_an_unreadable_store_directory_is_a_refusal_not_an_environment_note(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A store behind an unreadable PARENT dir makes every command fail.

        `Path.is_file()` sits outside the resolver's own try, so `~/.tortoise` at
        mode 000 (what a `sudo tortoise init` leaves behind) escapes as a bare
        OSError rather than `_ConfigError`. Rendering that as "the resolver could
        not run" + ⚠️ let `doctor` exit 0 on exactly the box #11912 is about —
        `_cmd_config` already attributes it from `exc.filename` (fresh review
        round 5).
        """
        import os as _os
        import stat as _stat

        store = _hermetic_hosted_identity / ".tortoise"
        store.mkdir(parents=True, exist_ok=True)
        (store / "credentials.json").write_text('{"api_key": "x"}')
        calls = _stub_hosted(monkeypatch, lambda url, req: pytest.fail(
            f"an unreadable store must not be dialled — requested {url}"))
        db_path = str(tmp_path / "unreadable-store.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor unreadable store seed")
        store.chmod(0o000)
        try:
            if _os.access(store, _os.R_OK):  # root ignores mode bits
                pytest.skip("running as root")
            rc = _run_doctor([])
        finally:
            store.chmod(_stat.S_IRWXU)
        out = capsys.readouterr().out

        assert calls == []
        cred = _row(out, "Credentials")
        assert cred is not None and "❌" in cred, out[-1200:]
        assert "unreadable at" in cred, cred
        assert rc == 1

    def test_a_graph_scoped_key_still_surfaces_a_degraded_health(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`/health`'s own verdict must survive a graph-scoped 403.

        That arm returned `ℹ️` before the health verdict was consulted, so a
        `degraded` service rendered as information — counted in neither column —
        and an otherwise-clean box printed `✅ All checks passing!` during a real
        outage. A graph-scoped key is documented and supported (fresh review
        round 5).
        """
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"degraded"}')
            raise HTTPError(url, 403, "Forbidden", {},
                            __import__("io").BytesIO(
                                b'{"detail":{"error_code":"GRAPH_SCOPED_TEAM_SURFACE"}}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "scoped-degraded.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor scoped degraded seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "⚠️" in hosted, hosted
        assert "degraded" in hosted, hosted
        assert "All checks passing" not in out, out[-1200:]

    def test_a_crash_in_the_hosted_block_fails_the_report(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A hosted verdict that could not be produced is NOT a passing verdict.

        `_cmd_doctor` counts only ❌ toward its exit code, so the block's own
        `except Exception` must not degrade to ⚠️: that would print
        `N pass, 0 fail` / `All checks passing!` and exit 0 on a box whose
        credential is broken — #11912's target reachable through any bug in the
        new code (fresh review round 7 — the arm was unpinned, and reverting it to
        ⚠️ left every test green).
        """
        from tortoise import __main__ as _m

        def _boom(_results, offline=False):
            raise RuntimeError("deliberate: the hosted block failed")

        monkeypatch.setattr(_m, "_doctor_hosted_rows", _boom)
        db_path = str(tmp_path / "crash.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor crash seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None and "❌" in hosted, out[-1200:]
        assert "UNGRADED" in hosted, hosted
        assert "All checks passing" not in out, out[-1200:]
        assert rc == 1

    def test_the_team_legs_transport_error_is_scrubbed_too(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The AUTHENTICATED leg's exception text must not print the api_url password.

        The other scrub test makes every request fail, so `/health` fails first and
        the team-leg scrub is never reached — dropping `_scrub` from that line kept
        every test green (fresh review round 7), while a userinfo `api_url` printed
        `nonnumeric port: '<password>@host'` into the transcript (#5109).
        """
        password = "hunter2pass"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test/")

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise ValueError(f"nonnumeric port: '{password}@api.example.test'")

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "team-scrub.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor team scrub seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert password not in out, "the api_url password reached the transcript"
        # The endpoint ANSWERED /health, so a reset on the authenticated leg is a
        # service blip: unproven, not a broken key (round 7). ⛔ And the row must
        # NOT claim the key is fine: only the authenticated read can prove a key,
        # so a proxy that serves anonymous GETs and rewrites `Authorization` is
        # unauthored-for by "the endpoint answered" (fresh review round 13).
        assert "⚠️" in hosted, hosted
        assert "could not complete" in hosted, hosted
        assert "UNPROVEN" in hosted and "not verified" in hosted, hosted
        assert "rather than your key" not in hosted, hosted

    def test_a_refusal_body_that_echoes_the_request_is_scrubbed(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A proxy echoing the REQUEST back must not print the live key.

        `_scrub(team_body, api_url, api_key)` is the guard, and the 503/generic
        arms print `body[:400]`/`body[:2000]` of it — the exact "a proxy may echo
        the request (headers included)" case `_scrub`'s own docstring names. It
        could be deleted with the whole suite green (fresh review round 9).
        """
        from urllib.error import HTTPError

        password = "hunter2pass"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test")

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            echo = ('{"detail":"blocked",'
                    f'"request":"GET {url} Authorization: Bearer {_FAKE_HOSTED_KEY}"}}')
            raise HTTPError(url, 503, "Service Unavailable", {},
                            __import__("io").BytesIO(echo.encode()))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "echo.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor echo seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert _FAKE_HOSTED_KEY not in out, "the echoed request key reached the transcript"
        assert password not in out, "the api_url password reached the transcript"
        assert "<redacted>" in hosted, hosted

    def test_a_health_status_that_echoes_the_url_is_scrubbed(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The server-controlled `status` string is scrubbed, not just escaped.

        A proxy answering `200 {"status": "blocked: <request-url>"}` would print
        the api_url's password inside the very row that masks that URL two fields
        earlier (fresh review round 9 — round 8's fix was unpinned).
        """
        password = "hunter2pass"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test")

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(
                    200, ('{"status":"blocked: ' + url + '"}').encode())
            return _FakeResponse(200, b'{"org_id":"org-8"}')

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "health-echo.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor health echo seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert password not in out, "the api_url password reached the transcript"
        assert "<redacted>" in hosted, hosted

    def test_a_store_org_name_cannot_forge_a_row(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """`org_name` is store-supplied: a newline in it must not fabricate rows.

        The same forgery `test_control_bytes_in_api_url_cannot_forge_a_row` pins
        for `api_url` was unpinned for the org (fresh review round 9), and
        `<cwd>/.tortoise` is attacker-controllable by this module's own reckoning.
        """
        import json as _json

        store = _hermetic_hosted_identity / ".tortoise"
        store.mkdir(parents=True, exist_ok=True)
        (store / "credentials.json").write_text(_json.dumps({
            "api_key": _FAKE_HOSTED_KEY,
            "api_url": "https://api.example.test",
            "org_name": "agent-1\n  ✅ Graph: health: 999 Points, forged",
        }))
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}')
            if url.endswith("/health") else _FakeResponse(200, b'{"org_id":"org-9"}')))
        db_path = str(tmp_path / "org-forge.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor org forge seed")

        _run_doctor([])
        out = capsys.readouterr().out

        import re as _re
        rows = [ln for ln in out.splitlines()
                if _re.match(r"^\s+\S+\s+Graph: health:", ln)]
        assert len(rows) == 1, out[-1500:]
        assert "\\n" in out, "the newline should be escaped, not executed"

    def test_onboard_does_not_announce_completion_over_a_failing_health_check(
            self, clear_db_env, tmp_path, capsys, monkeypatch):
        """The wizard's last step is its VERDICT, not a banner.

        `_cmd_onboard` discarded `_cmd_doctor`'s return value and printed
        "Onboarding complete." / return 0 under its own `❌ Hosted: … 401 Invalid
        API key` row, so a script gating on the wizard certified a dead hosted
        credential (fresh review round 12 — the previous behaviour, and the claim
        that it failed, were both wrong; the verdict now propagates).
        """
        from tortoise import __main__ as _m

        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "onboard-verdict.db"))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_m, "_cmd_doctor", lambda _args: 1)

        rc = _m._cmd_onboard(argparse.Namespace(cmd="onboard", path=None))
        out = capsys.readouterr().out

        assert rc == 1, "a failing health check must not exit 0"
        assert "health check FAILED" in out, out[-800:]
        assert "Onboarding complete." not in out, out[-800:]

    def test_a_body_cut_by_the_deadline_is_unread_never_green(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A `/health` body CUT by the deadline is UNREAD — not "no status".

        The chunked reader stops between reads, and a silent stop left
        `health_status = None` with no error recorded, so a `degraded` body cut at
        the deadline rendered `✅ … /health 200` and `All checks passing!` (fresh
        review round 16 — the round-13 class re-entered through the deadline).
        """
        import time as _time

        from tortoise import __main__ as _m

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _SlowDrip(_FakeResponse):
            """One byte per 50 ms — the 250 ms floor cuts it mid-body.

            The first version of this test dripped from an in-memory buffer with
            NO delay, completed in microseconds, and never reached the cut arm
            (fresh review round 17).
            """

            def read(self, n=None):
                _time.sleep(0.05)
                return self._buf.read(1)

            def read1(self, n=None):
                return self.read(n)

        monkeypatch.setattr(_m, "_DOCTOR_HEALTH_TIMEOUT_S", 0.001)  # → 0.25 floor
        _stub_hosted(monkeypatch, lambda url, req: (
            _SlowDrip(200, b'{"status":"degraded"}') if url.endswith("/health")
            else _FakeResponse(200, b'{"org_id":"org-9"}')))
        db_path = str(tmp_path / "cut-health.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cut health seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        # ⛔ "CUT" SPECIFICALLY: the `or "could not be read"` alternative was
        # satisfied by the PRE-EXISTING JSON-decode arm (a drip-cut body fails
        # `_json.loads` whether or not the cut block exists), so deleting the
        # round-16 block left the suite green.
        assert "CUT" in hosted, hosted
        assert "✅" not in hosted, hosted
        assert "All checks passing" not in out, out[-1200:]

    def test_a_short_body_is_not_read_as_a_captive_portal(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A body that CLOSES EARLY is truncated, not "no team identity".

        EOF is not proof of completeness: a declared `Content-Length` that never
        arrives (a proxy cutting the stream, a reset after the headers) made the
        reader report a COMPLETE body, so a healthy key fell into the
        "answered 200 but with no team identity … a captive portal also returns
        200" arm — `❌` + exit 1 for a flaky TRANSPORT, with a remedy blaming the
        user's own endpoint (fresh review of the delta).
        """

        class _ShortBody(_FakeResponse):
            """Declares 60 bytes, delivers 11, then closes."""

            def __init__(self):
                super().__init__(200, b'{"org_id":')

            @property
            def length(self):
                # The shape `http.client` exposes: bytes still to come.
                return max(0, 60 - self._buf.tell())

            def read1(self, n: int = -1):
                chunk = self._buf.read(n)
                return chunk

            def read(self, n: int = -1):
                return self._buf.read(n)

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}') if url.endswith("/health")
            else _ShortBody()))
        db_path = str(tmp_path / "short-body.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor short body seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "no team identity" not in hosted, hosted
        assert "captive portal" not in hosted, hosted
        assert "CUT" in hosted or "UNPROVEN" in hosted or "UNREAD" in hosted, hosted
        assert "❌ Hosted" not in out, out[-1200:]
        assert rc == 0, "a truncated transport is not a broken credential"

    def test_a_complete_body_at_the_deadline_boundary_is_not_reported_as_cut(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A COMPLETED body is not "CUT" — the round-17 check, pinned.

        The deadline is consulted before the EOF probe, so a body whose last chunk
        arrives at the boundary (or a fixed-length body fully read) would be
        reported CUT and downgrade a healthy `✅` to a "we stopped early" `⚠️`.
        `HTTPResponse.length == 0` is what distinguishes it; deleting that check
        left the whole module green because no test double carried `length`.
        """

        import time as _time

        from tortoise import __main__ as _m

        class _CompleteAtTheBoundary(_FakeResponse):
            """The WHOLE body arrives late, in one read, with `length == 0`.

            That is the shape the round-17 check exists for: the loop's next
            deadline test fires AFTER every declared byte has arrived, so the
            deadline has passed while the body is COMPLETE. `length == 0` is what
            says so; without it the row would be downgraded to "stopped early".
            """

            @property
            def length(self):
                return 0

            def read1(self, n: int = -1):
                _time.sleep(0.30)  # past the 0.25 s floor
                return self._buf.read(n)

            def read(self, n: int = -1):
                return self.read1(n)

        monkeypatch.setattr(_m, "_DOCTOR_HEALTH_TIMEOUT_S", 0.001)  # → 0.25 floor
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        _stub_hosted(monkeypatch, lambda url, req: (
            _CompleteAtTheBoundary(200, b'{"status":"degraded"}')
            if url.endswith("/health")
            else _FakeResponse(200, b'{"org_id":"org-c"}')))
        db_path = str(tmp_path / "exact-body.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor exact body seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "CUT" not in hosted, hosted
        assert "UNREAD" not in hosted, hosted
        # The body's OWN verdict survives — proof it was read, not assumed.
        assert "degraded" in hosted, hosted
        assert "/v1/team accepted" in hosted, hosted

    def test_an_oat_token_in_the_env_with_an_explicit_url_is_not_a_broken_key(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """The `oat_` 401 must not read as a revoked key — on EVERY route to it.

        The env-only gate refuses to probe an `oat_` identity, but an explicit
        `TORTOISE_API_URL` (or a store file) skips that gate and probes, and REST
        `/v1/*` answers 401 to an OAuth token BY DESIGN (ADR-010: `oat_` tokens
        are MCP-only). That read `❌ … Re-mint it: tortoise signup (new team)` +
        exit 1 for a working setup — the exact false FAIL the gate exists to
        prevent, one environment variable away (fresh review of the delta).
        """
        from urllib.error import HTTPError

        monkeypatch.setenv("TORTOISE_API_KEY", "oat_" + "b2c3d4e5f6a7" * 3)
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.example.test")

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(url, 401, "Invalid API key", {}, None)

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "oat-401.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor oat 401 seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1500:]
        assert "401" in hosted, hosted
        assert "OAuth" in hosted and "MCP" in hosted, hosted
        # The remedies that would be WRONG for this family.
        assert "tortoise signup (new team)" not in hosted, hosted
        assert "❌ Hosted" not in out, out[-1200:]
        assert rc == 0, "an MCP-only token is not a failed REST credential"

    def test_a_v1_team_reply_cut_by_the_deadline_is_unproven_not_a_broken_key(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A 2xx whose body was CUT is not a captive portal — it is unproven.

        Before the flag existed, a cut 2xx reached the no-identity arm and printed
        `❌ … Check your TORTOISE_API_URL` with a fragment of the user's own body
        as "evidence" — a healthy setup graded red (fresh review round 16).
        """
        import time as _time

        from tortoise import __main__ as _m

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _SlowTeam(_FakeResponse):
            """One byte per 50 ms, so the TEAM leg's deadline cuts it mid-body.

            `/health` must COMPLETE here: a spent budget at leg one would test the
            budget row instead of the cut arm (fresh review round 17 caught
            exactly that in the first version).
            """

            def read(self, n=None):
                _time.sleep(0.05)
                return self._buf.read(1)

            def read1(self, n=None):
                return self.read(n)

        monkeypatch.setattr(_m, "_DOCTOR_TEAM_TIMEOUT_S", 0.001)  # → 0.25 floor
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}') if url.endswith("/health")
            else _SlowTeam(200, b'{"org_id":"org-9","tier":"anon"}')))
        db_path = str(tmp_path / "cut-team.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cut team seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "CUT" in hosted, hosted
        assert "❌" not in hosted, hosted
        assert "TORTOISE_API_URL" not in hosted, hosted

    def test_a_budget_stop_still_reports_an_established_health_verdict(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A budget stop must CARRY a `/health` verdict already established.

        Otherwise the report withholds a known `degraded`/5xx and only says
        "re-run" — the round-15 fix, which round 17 measured as unpinned
        (`carried = ""` left the suite green).
        """
        import time as _time

        from tortoise import __main__ as _m

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _SlowHealth(_FakeResponse):
            """Completes the body in ~0.2 s, leaving < 0.25 s for the next leg."""

            def read(self, n=None):
                _time.sleep(0.02)
                return self._buf.read(4)

            def read1(self, n=None):
                return self.read(n)

        monkeypatch.setattr(_m, "_DOCTOR_TOTAL_BUDGET_S", 0.30)
        _stub_hosted(monkeypatch, lambda url, req: (
            _SlowHealth(200, b'{"status":"degraded"}') if url.endswith("/health")
            else pytest.fail("the team leg must not start on a spent budget")))
        db_path = str(tmp_path / "budget-carried.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor budget carried seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "budget" in hosted, hosted
        # Under load the health read can itself be cut; the mutation this pins
        # (`carried = ""`) still reds because a completed health read then
        # contributes no note at all.
        assert "degraded" in hosted or "could not be read" in hosted, hosted
        assert "✅" not in hosted, hosted

    def test_a_cut_transport_message_still_scrubs_the_password(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """Scrub BEFORE truncate: a cut inside the password must not leak a fragment.

        The read-error text is bounded for display, but a bound applied FIRST
        turns the password into a fragment `_scrub` can no longer match — all but
        one character of it printed (fresh review round 15). Round 17 measured the
        ordering as unpinned because the only such test used a short message.
        """

        password = "hunter2pass"
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY,
                          api_url=f"https://svc:{password}@api.example.test")

        class _Stalled(_FakeResponse):
            def read(self, n=None):
                # The password sits PAST the 200-char display bound, so
                # truncate-then-scrub would print its tail.
                # ⛔ The password STRADDLES the 200-char display bound: with
                # truncate-before-scrub the cut leaves `hunter2p`, a fragment
                # `_scrub` can no longer match (fresh review round 18 — the first
                # version put the password entirely PAST the bound and pinned
                # nothing).
                # `ValueError: ` (12) + 170 x's + " https://svc:" (14) = 196, so
                # the 200-char display bound cuts INSIDE the password: with
                # truncate-before-scrub the output keeps `hunt`, a fragment
                # `_scrub` can no longer match (fresh review round 19 — 190 x's
                # pushed the URL entirely past the bound and pinned nothing).
                raise ValueError("x" * 170 + f" https://svc:{password}@api.example.test")

            def read1(self, n=None):
                return self.read(n)

        _stub_hosted(monkeypatch, lambda url, req: (
            _Stalled(200) if url.endswith("/health")
            else _FakeResponse(200, b'{"org_id":"org-9"}')))
        db_path = str(tmp_path / "cut-scrub.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cut scrub seed")

        _run_doctor([])
        out = capsys.readouterr().out

        assert password not in out, "the api_url password reached the transcript"

    def test_a_completed_body_is_not_reported_as_cut(self):
        """Direct unit test of the reader: complete ⇒ not cut, mid-body ⇒ cut.

        The previous version drove `doctor` with a zero budget, so the leg never
        STARTED and the branch was never reached — mutating it to
        `complete = False` stayed green (fresh review round 19). The reader is a
        module-level function now, so the discrimination is direct.
        """
        import time as _time

        from tortoise.__main__ import _doctor_read_capped

        class _WithLength(_FakeResponse):
            """A response that reports its remaining DECLARED length."""

            @property
            def length(self):
                return self._buf.getbuffer().nbytes - self._buf.tell()

        # Complete: the whole body is read, then the deadline is already spent.
        body = b'{"status":"ok"}'
        resp = _WithLength(200, body)
        data, cut = _doctor_read_capped(resp, 65536, _time.monotonic() + 5)
        assert data == body
        assert cut is False, "a fully-read body must never be reported as cut"

        # Now the SAME response with the deadline already past: the loop head
        # fires, and `length == 0` still says complete.
        resp2 = _WithLength(200, b"")
        _doctor_read_capped(resp2, 65536, _time.monotonic() + 5)
        data2, cut2 = _doctor_read_capped(resp2, 65536, _time.monotonic() - 1)
        assert data2 == b""
        assert cut2 is False, "an empty body at EOF is complete, not cut"

        # Mid-body: a declared length remains, so the stop IS a cut.
        resp3 = _WithLength(200, b"0123456789")
        resp3.read(3)
        _data3, cut3 = _doctor_read_capped(resp3, 65536, _time.monotonic() - 1)
        assert cut3 is True, "a body with bytes still declared must be a cut"

        # ── NO declared length (chunked / close-delimited) ────────────────
        # `None` carries no information at the cap, so one probe byte decides.
        class _Chunked(_FakeResponse):
            length = None  # a chunked body declares nothing

            def read(self, n=None):
                return self._buf.read(n)

            def read1(self, n=None):
                return self.read(n)

        # (a) body EXACTLY at the cap → complete, NOT cut.
        exact = _Chunked(200, b"y" * 4096)
        _data_a, cut_a = _doctor_read_capped(exact, 4096, _time.monotonic() + 5)
        assert cut_a is False, "a chunked body that ends exactly at the cap is complete"

        # (b) body OVER the cap → cut.
        over = _Chunked(200, b"y" * (4096 + 10))
        _data_b, cut_b = _doctor_read_capped(over, 4096, _time.monotonic() + 5)
        assert cut_b is True, "a chunked body over the cap must be a cut"

        # (c) the probe is ONE byte — its wait and allocation must not scale with
        # the cap (fresh review round 23).
        probe_sizes: list[int] = []

        class _Recording(_Chunked):
            def read(self, n=None):
                probe_sizes.append(n)
                return super().read(n)

        _doctor_read_capped(_Recording(200, b"y" * (4096 + 10)), 4096,
                            _time.monotonic() + 5)
        assert probe_sizes[-1] == 1, probe_sizes

        # (d) a SPENT deadline at the cap is CUT, never "complete".
        spent = _Chunked(200, b"y" * 4096)
        _data_d, cut_d = _doctor_read_capped(spent, 4096, _time.monotonic() - 1)
        assert cut_d is True, "an unverifiable body at a spent deadline is a cut"

        # (e) a probe that RAISES PROPAGATES (a read error is its own verdict),
        # it is not laundered into "cut".
        class _RaisingProbe(_Chunked):
            def read(self, n=None):
                raise http.client.IncompleteRead(b"y", 10)

        with pytest.raises(http.client.IncompleteRead):
            _doctor_read_capped(_RaisingProbe(200, b"y" * (4096 + 10)), 4096,
                                _time.monotonic() + 5)

    def test_a_cut_403_body_is_unproven_not_a_red_refusal(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A cut 403 must not be graded from a fragment.

        Losing the body loses `error_code`, so a cut graph-scoped 403 fell through
        to `❌ /v1/team → API error 403: <fragment>` — a documented, correctly
        scoped key reading RED (fresh review round 18).
        """
        import time as _time

        from tortoise import __main__ as _m

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _Slow403(_FakeResponse):
            def read(self, n=None):
                _time.sleep(0.05)
                return self._buf.read(1)

            def read1(self, n=None):
                return self.read(n)

        monkeypatch.setattr(_m, "_DOCTOR_TEAM_TIMEOUT_S", 0.001)  # → 0.25 floor
        body = b'{"detail":{"error_code":"GRAPH_SCOPED_TEAM_SURFACE"}}'
        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}') if url.endswith("/health")
            else _Slow403(403, body)))
        db_path = str(tmp_path / "cut-403.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cut 403 seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "CUT" in hosted, hosted
        assert "❌" not in hosted, hosted

    def test_a_raised_read_on_a_refusal_body_is_unproven_not_red(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A read that RAISES is an UNREAD body — the raise path is a cut too.

        `_doctor_read_capped` propagates read errors on purpose, and on a CHUNKED
        reply a mid-stream drop raises `http.client.IncompleteRead` (a
        fixed-length body returns `b""` and takes the reader's own cut exit,
        which is pinned by `test_an_early_close_with_bytes_outstanding_is_a_cut_body`).
        The `HTTPError` arm swallowed that raise into `team_body = ""` and left
        `team_cut` False, so a documented `403 GRAPH_SCOPED_TEAM_SURFACE` whose
        body a proxy cut mid-chunk read `❌ … API error 403: (empty body)` +
        exit 1 — the exact false red the cut flag exists to remove, surviving on
        the one path that never set the flag (fresh review round 19).

        Reachable in practice, not theoretical: the reader arms a socket watchdog
        at the deadline, and its interrupt surfaces as exactly this raise.
        """
        import http.client as _http_client
        from urllib.error import HTTPError

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _IncompleteBody(io.BytesIO):
            """A chunked refusal body whose stream drops mid-chunk."""

            def read1(self, n=-1):
                raise _http_client.IncompleteRead(b'{"detail":', 50)

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            raise HTTPError(
                url, 403, "Forbidden", {},
                _IncompleteBody(b'{"detail":{"error_code":"GRAPH_SCOPED_TEAM_SURFACE"}}'))

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "incomplete-403.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor incomplete 403 seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "❌" not in hosted, hosted
        assert "CUT" in hosted and "UNPROVEN" in hosted, hosted
        assert rc == 0, "an unread refusal body is not a failing credential"

    def test_a_cut_body_is_reported_even_when_the_prefix_parses(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A cut must be reported even when the bytes read so far are VALID JSON.

        The other cut tests drip a body that is cut into INVALID JSON, so the
        `JSONDecodeError` arm satisfies them and they stay green if the
        cut-report block is deleted. This one delivers a COMPLETE, valid
        `{"status":"ok"}` and then keeps the stream open (trailing whitespace
        keeps the JSON parseable, and the fake declares bytes still owed), so the
        cut FLAG is the only thing that can downgrade the ✅ (fresh review round
        18).

        The drip is bounded (five bytes, then EOF) so a regression that disarms
        the deadline finishes in ~0.6 s and fails on the verdict instead of
        hanging.
        """
        import time as _time

        from tortoise import __main__ as _m

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _TrailingSpaceDrip(_FakeResponse):
            """Whole body, then one space per 120 ms, then EOF."""

            _drips = 0

            @property
            def length(self):
                # Bytes still OWED: the declared body is bigger than what arrived,
                # which is #12398's dripping shape. Without this the round-18
                # `length == 0` branch correctly reads the stop as completion.
                return max(0, 5 - self._drips)

            def read1(self, n=None):
                body = super().read(n)
                if body:
                    return body
                if self._drips >= 5:
                    return b""
                self._drips += 1
                _time.sleep(0.12)
                return b" "

        # 0.26 s is above `_leg_budget`'s 0.25 s floor, so this is the bound the
        # leg actually uses — and the drip crosses it.
        monkeypatch.setattr(_m, "_DOCTOR_HEALTH_TIMEOUT_S", 0.26)
        _stub_hosted(monkeypatch, lambda url, req: (
            _TrailingSpaceDrip(200, b'{"status":"ok"}') if url.endswith("/health")
            else _FakeResponse(200, b'{"org_id":"org-9"}')))
        db_path = str(tmp_path / "cut-valid.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor cut valid seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "CUT" in hosted, hosted
        assert "✅" not in hosted, hosted
        assert "All checks passing" not in out, out[-1200:]

    def test_a_body_without_read1_is_read_one_byte_per_call(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A response with no `read1` must not swallow the wall-clock bound.

        `HTTPResponse.read(n)` blocks until `n` bytes or EOF, so a chunk-sized
        fallback (`getattr(resp, "read1", None) or resp.read`) cannot reach the
        deadline until a whole chunk has arrived — the unbounded wait the chunked
        reader exists to remove. Real urllib responses expose `read1`, so this is
        latent rather than live; it is pinned because the fallback is one edit
        away from being the whole mechanism (fresh review round 18).

        The fake ACCEPTS one byte per call and refuses anything wider with an
        `AssertionError`, which `doctor`'s own `except Exception` renders as a ❌
        "could not run" row — so a regression fails the verdict assertion loudly
        instead of being swallowed into a pass.
        """
        import time as _time

        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        requested: list[int] = []

        class _NoRead1(_FakeResponse):
            """Only `read(n)`; a read wider than one byte would block."""

            read1 = None

            def read(self, n=-1):
                requested.append(n)
                assert n == 1, (
                    f"read({n}) on a body without read1 blocks past the deadline "
                    "— the fallback must read one byte per call")
                _time.sleep(0.002)
                return super().read(1)

        _stub_hosted(monkeypatch, lambda url, req: (
            _NoRead1(200, b'{"status":"ok"}') if url.endswith("/health")
            else _NoRead1(200, b'{"org_id":"org-9","tier":"anon"}')))
        db_path = str(tmp_path / "noread1.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor no read1 seed")

        rc = _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "✅" in hosted, hosted
        assert "could not run" not in hosted, hosted
        assert requested and set(requested) == {1}, sorted(set(requested))[:5]
        assert rc == 0, hosted

    def test_an_early_close_with_bytes_outstanding_is_a_cut_body(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """EOF is not automatically a COMPLETE body.

        `HTTPResponse.read1` returns `b""` on a premature close instead of raising
        `IncompleteRead` (measured: `Content-Length: 200`, connection closed after
        10 bytes, `resp.length == 190`, no exception), so a truncated reply used
        to report `cut=False` and reach the ❌ captive-portal arm — the same false
        red the cut flag removed, on a different trigger. `resp.length`
        distinguishes them: non-zero means bytes are still owed (fresh review round
        18).
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)

        class _ShortResponse(_FakeResponse):
            """Serves 10 bytes of a body whose `Content-Length` promised 200."""

            length = 190

        _stub_hosted(monkeypatch, lambda url, req: (
            _FakeResponse(200, b'{"status":"ok"}') if url.endswith("/health")
            else _ShortResponse(200, b'{"org_id":')))
        db_path = str(tmp_path / "short-body.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor short body seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "❌" not in hosted, hosted
        assert "CUT" in hosted, hosted
        assert "no team identity" not in hosted, hosted

    def test_a_v1_team_body_over_the_cap_is_unproven_not_a_broken_setup(
            self, clear_db_env, tmp_path, capsys, monkeypatch, _hermetic_hosted_identity):
        """A >64 KiB 2xx body is a TRUNCATION, not a captive portal.

        The cap needs no slow network, so it was the easiest way to make a healthy
        setup read `❌ … no team identity … Check your TORTOISE_API_URL`: the
        fragment was parsed, found to carry no identity, and blamed on the user
        (fresh review round 20).
        """
        _write_credential(_hermetic_hosted_identity, _FAKE_HOSTED_KEY)
        big = b'{"org_id":"org-9","pad":"' + b"x" * 70000 + b'"}'

        def handler(url, req):
            if url.endswith("/health"):
                return _FakeResponse(200, b'{"status":"ok"}')
            return _FakeResponse(200, big)

        _stub_hosted(monkeypatch, handler)
        db_path = str(tmp_path / "over-cap.db")
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        _seed_db(db_path, "doctor over cap seed")

        _run_doctor([])
        out = capsys.readouterr().out

        hosted = _row(out, "Hosted")
        assert hosted is not None, out[-1200:]
        assert "❌" not in hosted, hosted
        assert "TORTOISE_API_URL" not in hosted, hosted
        assert "CUT" in hosted, hosted


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
        print the credential. #2987 review: the masked URI itself is no longer
        echoed when its tail is credential-shaped (`127.0.0.1:notaport` — a
        non-numeric port is indistinguishable from `host:pw`), so the line now
        fails closed; the port value is still named by the exception text, so
        the diagnostic survives.

        Class B: (1) `docker://:sekritpass@127.0.0.1:notaport` — before the
        #2987 fix the tail after the last '@' was emitted verbatim, which is
        the same region the no-'@' branch already refuses to print; (2)
        reachable: `doctor --db <malformed URI>` prints `_mask_uri_userinfo`
        of the target, and --db is operator-supplied.
        """
        rc = _run_doctor(["--db", "docker://:sekritpass@127.0.0.1:notaport/test_doctor"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "sekritpass" not in out  # credential never reaches stdout
        probe = next(line for line in out.splitlines() if "Graph: FalkorDB" in line)
        assert "bad port" in probe
        assert "<uri-redacted-unrecognised-shape>" in probe  # fail closed
        assert "notaport" in probe  # the bad port is still named by the reason

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
        assert "<uri-redacted-unrecognised-shape>" in probe  # fail closed (#2987)
        assert "notaport" in probe  # the bad port is still named by the reason

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
        assert "<uri-redacted-unrecognised-shape>" in probe  # fail closed (#2987)

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
            "<uri-redacted-unrecognised-shape>"
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
        # credential instead of leaking it (and never raises in a handler).
        # #2987 review: `[abc` is not a recognised-safe target, so the whole
        # value fails closed rather than echoing the tail.
        assert _mask_uri_userinfo("docker://user:pw@[abc") == \
            "<uri-redacted-unrecognised-shape>"

    def test_mask_uri_userinfo_post_at_remainder_fails_closed(self):
        """#2987 review: the text after the LAST '@' must not be echoed when it
        is credential-shaped, and a later `scheme://` must not ride through.

        Class B: (1) `rediss://u:pw@host:S3nPw` and
        `rediss://u:pw@h:1 rediss://:S3nPw` print the password before the fix
        — the post-'@' remainder was appended verbatim (`i = len(line)`) and
        never judged, while the SAME region with no '@' fails closed one branch
        up; (2) reachable: `_mask_uri_userinfo` is the masker every CLI error
        path uses, and the value is an operator-supplied `TORTOISE_DB_URI`.

        ANTI-VACUOUS: a well-formed masked URI must keep its target — otherwise
        this rule would fail closed on every normal target.
        """
        from tortoise.__main__ import _mask_uri_userinfo

        # credential-shaped post-'@' tail: same treatment as the no-'@' form
        assert _mask_uri_userinfo("rediss://u:pw@host:S3nPw") == \
            "<uri-redacted-unrecognised-shape>"
        assert _mask_uri_userinfo("rediss://:pw@:S3nPw:6379") == \
            "<uri-redacted-unrecognised-shape>"
        # a SECOND URI after a masked one, on the same line
        assert _mask_uri_userinfo("rediss://u:pw@h:1 rediss://:S3nPw") == \
            "<uri-redacted-unrecognised-shape>"
        assert _mask_uri_userinfo("rediss://u:pw@h:1 rediss://user:S3nPw") == \
            "<uri-redacted-unrecognised-shape>"
        # ANTI-VACUOUS: a recognised-safe target still prints, masked, intact
        assert _mask_uri_userinfo("bolt://user:pw@host:7687/g") == \
            "bolt://:***@host:7687/g"
        assert _mask_uri_userinfo("rediss://u:pw@[::1]:6379/db") == \
            "rediss://:***@[::1]:6379/db"
        assert _mask_uri_userinfo("docker://:pw@host:7687/g") == \
            "docker://:***@host:7687/g"

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

    def test_mask_uri_userinfo_scheme_with_password_and_no_at_fails_closed(self):
        """#2987: a value that lost its '@host' tail still carries a password.

        Class B: (1) `rediss://:T4ilPw` and `rediss://user:T4ilPw` make this
        fail — with no '@' the last-'@' boundary finds nothing and the value
        passed through verbatim, so the password was printed; (2) reachable:
        the CLI prints `_mask_uri_userinfo(target)` / `_mask_uri_userinfo(str(e))`
        for a bad target (doctor, init), and an operator-supplied target may be
        any bytes.
        """
        from tortoise.__main__ import _mask_uri_userinfo

        # the empty-user form: `rediss://:pw@host` with the '@host' dropped
        assert _mask_uri_userinfo("rediss://:T4ilPw") == \
            "<uri-redacted-unrecognised-shape>"
        # a non-empty user whose 'password' is not a port (ports are numeric)
        assert _mask_uri_userinfo("rediss://user:T4ilPw") == \
            "<uri-redacted-unrecognised-shape>"
        # the SAME class hidden inside an error message: only the URI is
        # replaced, so the prose stays diagnosable
        assert _mask_uri_userinfo("Relative DB path 'rediss://:T4ilPw' rejected") == \
            "Relative DB path 'rediss://<uri-redacted-unrecognised-shape>' rejected"
        # ANTI-VACUOUS: a password-less host:port (numeric after the last ':')
        # is not credential-shaped and must keep printing unchanged — otherwise
        # this rule would fail closed on every normal target.
        assert _mask_uri_userinfo("rediss://r-example.host.cloud:50317") == \
            "rediss://r-example.host.cloud:50317"
        assert _mask_uri_userinfo("docker://127.0.0.1:7687/tortoise") == \
            "docker://127.0.0.1:7687/tortoise"

    def test_mask_uri_userinfo_malformed_scheme_and_schemeless_fail_closed(self):
        """#2987 cycle-2: an invalid/empty scheme is "no scheme", and a
        scheme-less continuation line fails closed on any '@'.

        Class B: (1) `1://user:T4ilPw` and `rediss://user:\npw@host` make this
        fail — the first was walked past as an invalid scheme and echoed, the
        second's `pw@host` continuation was echoed because only a colon-LED
        scheme-less line was checked; (2) both are reachable through
        `TORTOISE_DB_URI` / the CLI error paths, which print through this helper.
        """
        from tortoise.__main__ import _mask_uri_userinfo

        for value in (
            "1://user:T4ilPw",
            "://user:T4ilPw",
            "://:T4ilPw",
            "+://user:T4ilPw",
            "rediss://user:\nS3ntinelpw@host",
            "rediss://u:pw@h:1\nuser:T4ilPw@host",
            "  rediss://:T4ilPw",
            "rediss://[::1]:6379:S3n",
            "rediss://[::1]:6abc",
            # an '@' BEFORE the scheme / a non-ASCII scheme or port
            "T4ilPw@rediss://host:6379",
            "user:T4ilPw@rediss://:S3ntinel",
            "user:T4ilPw@rediss://user2:T4ilPw@host:6379",
            "user:T4ilPw@1://host:6379",
            "r\u00e9diss://user:T4ilPw",
            "rediss://[::1]:\u0660",
        ):
            out = _mask_uri_userinfo(value)
            assert "T4ilPw" not in out and "S3n" not in out, \
                f"leaked {value!r} -> {out!r}"

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
        # Neither of the installer-shaped repairs may be named: in this state
        # `tortoise install pi` AND `hooks upgrade --harness pi` both call the
        # same installer and both refuse.
        assert "hooks upgrade" not in out, (
            "the hint recommends an upgrade that refuses in this state")
        assert _ci.PI_DISABLED_DIRNAME in out, out
        # The replacement the hint names must itself accept `--harness pi`.
        # It does NOT pin WHICH read-only surface is named: `hooks status
        # --harness pi` also accepts the harness since #5351, so the only
        # invariant left here is that the hint is not a refusal.
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


#: Env names that decide doctor's session-extraction verdict but are NOT
#: provider-routing inputs: the offline mock seam (#3892's keyless path) and the
#: hosted-mode flag that escalates the line from ⚠️ to ❌. The provider KEYS are
#: DERIVED — see `_keyless_llm_env`.
_DOCTOR_ENV_EXTRAS = ("TORTOISE_SESSION_LLM_MOCK", "FLY_APP_NAME")


def _keyless_llm_env(monkeypatch) -> tuple[str, ...]:
    """Scrub every env name that decides doctor's session-extraction verdict.

    ⚠️ The provider KEYS are DERIVED from the registries the capture gate itself
    reads (`tests/_provider_env.pin_provider_routing_env`), never a literal list
    — the #3695 / #5477 class. The four-key hand-list this replaces never gained
    `VENICE_API_KEY`, and that omission was inert until #8133 registered `venice`
    in `ingest._PROVIDERS` and appended it to `sdk._SESSION_LLM_PROVIDER_PRIORITY`
    — after which an ambient `VENICE_API_KEY` made `_llm_provider_available()`
    true and `_session_llm_provider()` resolve `"venice"`, so doctor printed
    `✅ Session extraction: LLM provider configured (venice, model
    deepseek-v4-flash)` where these tests assert the keyless ⚠️ / ❌ line. Green
    in CI (the runner has no such key), red on any box carrying the eval/managed
    key set. Deriving is what makes it durable: a fifth provider registration
    cannot re-open it.

    Callable as a module-level helper on purpose — it is registered in
    `tests/test_session_extraction_modes.py::_KEYLESS_CAPTURE_HELPERS`, so the
    scrub is proven observable from outside the fixture.
    """
    from tests._provider_env import provider_routing_env_names

    names = (*provider_routing_env_names(), *_DOCTOR_ENV_EXTRAS)
    for name in names:
        monkeypatch.delenv(name, raising=False)
    return names


class TestDoctorSessionExtraction:
    """#1197: doctor surfaces the /v1/sessions LLM-provider state (#822).

    Captures are STORED but the LLM extraction is skipped when no provider key
    is configured (#3892) — extraction is the beta testers' most-critical
    feature. Doctor must report the provider/model when configured, and FAIL
    in hosted mode (FLY_APP_NAME) when the key is missing or the test seam is
    left on, so ops catch it before testers do.
    """

    @pytest.fixture
    def clean_llm_env(self, monkeypatch, clear_db_env, tmp_path):
        _keyless_llm_env(monkeypatch)
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
        """#703 follow-up: `_cmd_doctor` must tolerate a caller that passes ONLY
        `Namespace(cmd='doctor')` — `_cmd_onboard` did exactly that before it
        forwarded the graph target (#720) and `--offline` (#11912), and the
        tolerance is the contract, not an accident of who calls it today. Both
        args.db AND args.path AND args.offline are read via getattr."""
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
        # The withholding covers the finding's OWN detail too, whose phrasing
        # is "reinstall with `tortoise install pi`" — a needle on the HINT's
        # "run `...`" wording passes straight over it, which is how this
        # refusal survived the first pass (#5351 round 3).
        assert "reinstall with" not in row, (
            "the detail embeds the same refusing command as the hint")

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
        assert "reinstall with" not in row, (
            "a manual kind with no legacy collision must withhold the detail's "
            "unconditional `reinstall with `tortoise install pi`` too")

    def test_doctor_names_the_collision_beside_a_manual_kind(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """A MANUAL step does not clear the legacy collision, so naming only the
        manual obstacle promises a repair that still refuses.

        `foreign-artifact` is BOTH the first blocking finding and a manual kind
        (it is in `MANUAL_FIX_KINDS`), so the manual arm selects the hint and
        the `elif` dropped the collision from the row entirely.  The row must
        therefore name BOTH obstacles; and because the manual kind's detail is
        a CONDITIONAL two-step whose first step is the instruction the user
        needs, the detail stays — the collision sentence ahead of it is the
        counter-signal for its second step.  (Withholding it was tried and
        reverted: it deletes "move it aside", and `session verify` refuses
        before printing any finding when no API key resolves.)

        Mutation: restore the `elif` (drop the collision append) — the
        collision name disappears and this REDs.
        """
        from tortoise import capture_install as _ci
        from tortoise.hook_install import detect_artifact_install
        home = tmp_path / "home"
        self._seam(home, "// some other product's extension\n")
        kinds = {f.kind for f in detect_artifact_install(
            home / ".pi" / "agent" / "extensions", "pi")}
        assert kinds == {"foreign-artifact"}, (
            "the fixture must read as FOREIGN (a manual kind) and nothing "
            f"else, or this test is about a different state: {kinds}")
        root = home / ".pi" / "agent" / "extensions"
        (root / _ci.LEGACY_PI_DIRNAME).mkdir()
        (root / _ci.PI_DISABLED_DIRNAME).mkdir()
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "❌" in row, row
        assert "foreign-artifact" in row, row
        assert _ci.PI_DISABLED_DIRNAME in row, (
            "the collision outlives any manual step, so it must be named: "
            + row)
        assert "move it aside, then re-run `tortoise install pi`" in row, (
            "a MANUAL kind's detail is the instruction, not a bare command, "
            "so the row keeps it: " + row)

    def test_doctor_keeps_an_instruction_the_installer_is_not_part_of(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """The withholding must key on the DETAIL naming the installer, not on
        the existence of a collision.

        `not-readable` is a manual kind whose detail is `chmod it so the seam
        can load` — the collision has nothing to do with it, and the installer
        is not the remedy.  A collision-arm rule (tried in review round 5)
        withheld it and left the row with a pointer that does not always
        recover the text.

        Mutation: withhold on the collision alone (`_withhold = layout is None
        and bool(_legacy_obstacle) …`) — `chmod it` disappears and this REDs.
        """
        from tortoise import capture_install as _ci
        home = tmp_path / "home"
        seam = self._seam(home, "// tortoise-hook-version: 0\n// tortoise session\n")
        seam.chmod(0o000)
        root = home / ".pi" / "agent" / "extensions"
        (root / _ci.LEGACY_PI_DIRNAME).mkdir()
        (root / _ci.PI_DISABLED_DIRNAME).mkdir()
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert _ci.PI_DISABLED_DIRNAME in row, row
        assert "chmod it so the seam can load" in row, (
            "the collision does not make `chmod` refuse, so the instruction "
            "must survive: " + row)

    def test_doctor_keeps_a_non_manual_detail_that_names_no_command(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """A BLOCKING, non-manual kind whose detail carries no command must
        keep its detail: it names no repair the installer could refuse.

        `modified-artifact` is exactly that ("marker matches … but its bytes
        differ from the shipped seam"), and the manual-kind exemption does not
        cover it, so only the content half of the predicate saves it.  The
        collision is present, so the seam IS unrepairable — the case a
        kind-and-state rule withholds.

        Mutation: drop the content guard from `_artifact_detail_would_refuse`
        (keep the `unrepairable`/`blocking`/non-manual tests) — `bytes differ`
        disappears and this REDs.
        """
        from tortoise import capture_install as _ci
        from tortoise.hook_install import detect_artifact_install
        home = tmp_path / "home"
        (home / ".pi" / "agent").mkdir(parents=True)
        res = _ci.install_capture("pi", home=home)
        assert res.ok, res.error
        root = home / ".pi" / "agent" / "extensions"
        seam = root / _pi_seam_name()
        seam.write_bytes(seam.read_bytes() + b"\n// local edit\n")
        kinds = {f.kind for f in detect_artifact_install(root, "pi")}
        assert kinds == {"modified-artifact"}, (
            "the fixture must report exactly `modified-artifact` (blocking, "
            f"non-manual, command-free), or this is another state: {kinds}")
        (root / _ci.LEGACY_PI_DIRNAME).mkdir()
        (root / _ci.PI_DISABLED_DIRNAME).mkdir()
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert _ci.PI_DISABLED_DIRNAME in row, row
        assert "bytes differ" in row, (
            "no installer command is embedded, so nothing refuses — the "
            "detail must survive: " + row)

    def test_doctor_keeps_a_command_free_detail_when_the_path_looks_like_a_command(
            self, clear_db_env, tmp_path, monkeypatch, capsys):
        """The predicate matches a CLAUSE, not the bare command token.

        Every artifact detail interpolates the install PATH, and backticks are
        legal filename characters — so a $HOME containing the literal
        `` `tortoise install pi` `` makes the token appear in a detail that
        names no command at all.  A caller testing for the token alone withholds
        `bytes differ from the shipped seam` there and leaves the pointer in its
        place; the clause the detector actually writes does not occur in a path
        (`hook_install.ARTIFACT_INSTALLER_CLAUSES`).

        Mutation: match the bare token (`f"`tortoise install {harness}`" in
        detail`) — this REDs.
        """
        from tortoise import capture_install as _ci
        from tortoise.hook_install import detect_artifact_install
        home = tmp_path / "`tortoise install pi`" / "home"
        (home / ".pi" / "agent").mkdir(parents=True)
        res = _ci.install_capture("pi", home=home)
        assert res.ok, res.error
        root = home / ".pi" / "agent" / "extensions"
        seam = root / _pi_seam_name()
        seam.write_bytes(seam.read_bytes() + b"\n// local edit\n")
        kinds = {f.kind for f in detect_artifact_install(root, "pi")}
        assert kinds == {"modified-artifact"}, kinds
        assert "`tortoise install pi`" in str(seam), (
            "the fixture's PATH must carry the bare token, or the mutation "
            "this test pins is not exercised")
        (root / _ci.LEGACY_PI_DIRNAME).mkdir()
        (root / _ci.PI_DISABLED_DIRNAME).mkdir()
        monkeypatch.setenv("HOME", str(home))

        _run_doctor(["--path", "relative.db"])
        row = self._pi_row(capsys.readouterr().out)

        assert "bytes differ" in row, (
            "the path merely LOOKS like a command; the detail names none, so "
            "it must survive: " + row)


class TestCaptureSpoolHealthRow:
    """agent-infra#2100 — a quota-blocked box must be visible in the ONE health report an
    operator already runs.

    The measured failure: a cloud cell's capture spool held 320+ queued uploads,
    every one `waiting on backoff` against an exhausted graph allowance (402).
    The box could not run a lane at all, and nothing in `doctor` said why —
    "Capture hooks ✅" is true on a box that refuses every capture.

    The ICON is what carries the consequence: `fails = sum(icon == "❌")` and
    `_cmd_doctor` returns `0 if fails == 0 else 1`, while ⚠️/ℹ️ exit 0. A test
    that asserts only the ROW TEXT leaves the severity free to be downgraded to
    ⚠️, which would make a quota-blocked box merely warn (exit 0) on the very
    report this acceptance criterion names — so every state here pins its icon.
    """

    @staticmethod
    def _configured(monkeypatch, tmp_path):
        """A CONFIGURED box (a credential exists), so `INACTIVE` is not the
        answer and the state under test is the spool's own."""
        import os

        from tortoise import config as _config

        monkeypatch.setattr(
            _config, "DEFAULT_DB_PATH",
            os.path.join(str(tmp_path), ".tortoise", "tortoise.db"))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_doctor_test")

    def test_a_blocked_spool_is_reported_with_its_count_status_and_icon(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """MUTATION THAT REDS THIS: drop the `Capture spool` row (doctor reports
        the installed seam and says nothing about the refusal), or downgrade a
        BLOCKED line to ⚠️ (the report then exits 0 on a refused box)."""
        from tortoise import capture_spool as sp

        self._configured(monkeypatch, tmp_path)
        root = sp.spool_dir()
        for i in range(5):
            sp.write_spool_entry(root, sp.Snapshot(
                session_id=f"doctor-{i}",
                turns=[{"role": "user", "content": "hi"}],
                source="s.jsonl", machine_id="m" * 64, harness="claude"))
        sp.flush_spool(
            root,
            lambda payload: sp.PostOutcome(ok=False, status=402, detail="quota"),
            now=1000.0)

        _run_doctor(["--offline"])
        out = capsys.readouterr().out
        assert "capture: BLOCKED (5 queued, 402 quota)" in out, out
        assert "❌ Capture spool" in out, out

    def test_an_UNREADABLE_spool_warns_and_is_not_counted_as_a_pass(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """A check that could not RUN is not a passing check. An `else "✅"` arm
        rendered `UNREADABLE` GREEN — the same "cannot read → healthy" false
        PASS the state exists to prevent, one level up in the one report an
        operator runs.

        MUTATION THAT REDS THIS: restore `else "✅"` (or test only for BLOCKED)
        → an unreadable spool reports a passing check."""
        import os
        import stat

        from tortoise import capture_spool as sp

        self._configured(monkeypatch, tmp_path)
        root = sp.spool_dir()
        (root / "entries").mkdir(parents=True, exist_ok=True)
        (root / "entries").chmod(0o000)
        try:
            if os.access(root / "entries", os.R_OK):  # root ignores mode bits
                pytest.skip("running as root")
            _run_doctor(["--offline"])
            out = capsys.readouterr().out
            assert "capture: UNREADABLE (cannot list" in out, out
            assert "✅ Capture spool" not in out, out
            assert "⚠️ Capture spool" in out, out
        finally:
            (root / "entries").chmod(stat.S_IRWXU)

    def test_an_ordinary_spool_is_reported_as_a_pass(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """Positive control: the row must not cry BLOCKED — or even warn — on a
        healthy box, or an operator learns to ignore it.

        MUTATION THAT REDS THIS: make the row unconditional (always BLOCKED, or
        a non-✅ severity regardless of the line) → the report becomes noise and
        a real block stops standing out."""
        from tortoise import capture_spool as sp

        self._configured(monkeypatch, tmp_path)
        sp.spool_dir()  # the spool is created lazily; an empty one is `ok`

        _run_doctor(["--offline"])
        out = capsys.readouterr().out
        assert "capture: OK (0 queued)" in out, out
        assert "✅ Capture spool" in out, out
        assert "BLOCKED" not in out, out

    def test_a_DRAINING_or_INACTIVE_spool_is_information_not_a_warning(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """The alert-fatigue half, and the reason neither state may be ⚠️:

        * `DRAINING` is the NORMAL state of a box that is mid-session — the
          running session's own entry is deliberately unfiled until shutdown —
          and an agent running `tortoise doctor` from inside a lane IS that box.
        * a KEYLESS box is a normal install; capture is off by choice, and the
          existing `Session capture` row already reports that with ℹ️ for the
          same reason (#3615).

        Painting either as "1 warn" trains the operator to ignore the row, which
        is exactly how a real BLOCKED line gets missed.

        MUTATION THAT REDS THIS: map DRAINING or INACTIVE to ⚠️/✅.
        """
        import os

        from tortoise import capture_spool as sp
        from tortoise import config as _config

        monkeypatch.setattr(
            _config, "DEFAULT_DB_PATH",
            os.path.join(str(tmp_path), ".tortoise", "tortoise.db"))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)

        root = sp.spool_dir()
        sp.write_spool_entry(root, sp.Snapshot(
            session_id="live-session",
            turns=[{"role": "user", "content": "hi"}],
            source="s.jsonl", machine_id="m" * 64, harness="claude"))

        _run_doctor(["--offline"])
        out = capsys.readouterr().out
        # Keyless + one pending capture: the credential is the state that wins,
        # and the count is still named.
        assert "capture: INACTIVE (no TORTOISE_API_KEY, 1 queued)" in out, out
        assert "ℹ️ Capture spool" in out, out
        assert "⚠️ Capture spool" not in out, out
        assert "❌ Capture spool" not in out, out

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_doctor_test")
        _run_doctor(["--offline"])
        out = capsys.readouterr().out
        assert "capture: DRAINING (1 queued)" in out, out
        assert "ℹ️ Capture spool" in out, out
        assert "⚠️ Capture spool" not in out, out
        assert "❌ Capture spool" not in out, out

    def test_a_SATURATED_spool_FAILS_the_report_by_name_not_by_fallthrough(
            self, monkeypatch, clear_db_env, tmp_path, capsys):
        """A box that REFUSES every new capture is the box this row exists for,
        and the sixth state must be a DECISION in the map, not the unknown-state
        fallback.

        The row's own rule is "`❌` only for a refusal retrying cannot fix": a
        SATURATED spool is exactly that (nothing is evictable — only filing, a
        key, or deleting entries clears it), so `⚠️` would make `doctor` exit 0
        on a box capturing nothing. And when the state was added, this map was
        the one enumeration of the set left behind (the `CaptureState` type and
        `_print_capture_health`'s filter were both updated), so the comment's
        own claim — "every state is named here so a new one cannot fall through"
        — had already become false.

        MUTATION THAT REDS THIS: delete the `"saturated"` row -> the icon comes
        from the unknown-state fallback (⚠️) and the report exits 0.
        """
        from tortoise import capture_spool as sp

        self._configured(monkeypatch, tmp_path)
        root = sp.spool_dir()
        # The box is saturated at the ceilings THIS test can afford; the verdict
        # and the line are the real ones (`capture_health_line` with `tiny`), only
        # the doctor's own call is bound to those ceilings.
        tiny = sp.Bounds(max_entries=2, max_total_bytes=10_000_000,
                         max_entry_bytes=10_000)
        for i in range(2):
            sp.write_spool_entry(root, sp.Snapshot(
                session_id=f"sat-{i}",
                turns=[{"role": "user", "content": "hi"}],
                source="s.jsonl", machine_id="m" * 64, harness="claude"), tiny)
        refused = sp.write_spool_entry(root, sp.Snapshot(
            session_id="sat-refused",
            turns=[{"role": "user", "content": "hi"}],
            source="s.jsonl", machine_id="m" * 64, harness="claude"), tiny)
        assert refused["written"] is False
        assert refused["discards"][-1]["reason"] == "spool_saturated"

        real = sp.capture_health_line
        monkeypatch.setattr(
            sp, "capture_health_line",
            lambda r, *, api_key=None, bounds=None: real(r, api_key=api_key, bounds=tiny))

        assert _run_doctor(["--offline"]) == 1, "a refusing box is a FAILED report, not a warn"
        out = capsys.readouterr().out
        assert "capture: SATURATED (2 queued, refusing new captures)" in out, out
        assert "❌ Capture spool" in out, out
        assert "⚠️ Capture spool" not in out, out
        assert "✅ Capture spool" not in out, out
