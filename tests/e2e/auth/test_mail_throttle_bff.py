"""
The BFF's own email-request throttle on `/auth/reset` and `/auth/resend` (#4161).

WHAT THIS SUITE EXISTS FOR
--------------------------
The two mail-sending routes fold a provider 429 into an enumeration-safe 200, so
before this change the page's `resp.status === 429` branch was UNREACHABLE and a
throttled user was told an email had been sent when nothing was sent. The fix is
a throttle the BFF owns, keyed on the CALLER and never on the address, so it can
honestly answer 429 without becoming the account-existence oracle the fold exists
to prevent.

Two properties, and they pull against each other — which is why both are pinned
here rather than in one suite:

  1. HONESTY. A caller over the budget is refused, with the same 429 contract the
     sibling routes emit (`error: rate_limited` + `providerError` + `Retry-After`)
     and the provider is not contacted at all.
  2. NON-ENUMERATION. That refusal is IDENTICAL for a registered address and an
     unregistered one — the answer does not vary by address, so it carries no
     account signal. (The alternative, surfacing the provider's own 429, is the
     oracle: `/recover` and `/resend` answer an address with no account `200 {}`
     EARLY, before the email send that can be refused.)

Isolation: each test sends a distinct `CF-Connecting-IP`, so the module-scoped
`pages dev` runtime and its D1 file are shared without the budgets bleeding
across tests. That header is the EDGE's value in production and cannot be set by
a caller there; in `wrangler pages dev` it is passed through, which is exactly
what makes the key observable from a test.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from bff_test_helpers import pick_free_port, require_toolchain

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = Path(__file__).resolve().parent / "mock_email_flows_supabase.mjs"

# Mirrors MAIL_THROTTLE_LIMIT in
# website/apps/dashboard/functions/_shared/auth/mail-throttle.ts. Restated here on
# purpose: a test that reads the constant from the subject cannot fail when the
# subject changes it, and "the limit is what the code says" is not a property.
LIMIT = 5

APP_PORT = int(os.environ.get("MAIL_THROTTLE_TEST_APP_PORT", "9052"))
NOSTORE_PORT = int(os.environ.get("MAIL_THROTTLE_TEST_NOSTORE_PORT", "9053"))
MOCK_PORT = int(os.environ.get("MAIL_THROTTLE_TEST_MOCK_PORT", "9054"))
# The devtools ports. Fixed by default (9229), so two servers in one suite must
# claim two of them — see `_start_app`.
INSPECT_PORT = int(os.environ.get("MAIL_THROTTLE_TEST_INSPECT_PORT", "9352"))
INSPECT_NOSTORE_PORT = int(os.environ.get("MAIL_THROTTLE_TEST_INSPECT_NOSTORE_PORT", "9353"))

APP = ""       # assigned in the `stack` fixture (D1 bound)
NOSTORE = ""   # assigned in the `stack` fixture (no D1 binding at all)
MOCK_URL = ""

KNOWN = {"email": "known@example.test"}
UNKNOWN = {"email": "nobody@example.test"}
# The mock answers 429 for this address ONLY. Real GoTrue's email limiter is
# project-wide and has no key (see the module docstring), so the mock is a
# deliberate over-approximation: it produces the SAME differential a real refusal
# can produce (a refusal that a fresh address would not get), which is the
# property the fold has to survive.
RATELIMITED = {"email": "ratelimited@example.test"}


class Proc:
    """A `pages dev` (or mock) child whose output goes to a FILE, not a pipe.

    A pipe nobody drains is a 64 KiB buffer that BLOCKS the child once it fills —
    `wrangler` writes its boot banner, the compiled-worker summary and every
    request line, so a suite with two servers can wedge one of them before it
    ever binds, and the failure then reads "failed to start" with no cause. The
    file also makes a failed boot diagnosable instead of a black box.
    """

    def __init__(self, argv, cwd, env=None, log_path: Path | None = None):
        self.log_path = log_path
        self._fh = open(self.log_path, "wb") if self.log_path else subprocess.DEVNULL
        self.p = subprocess.Popen(
            argv, cwd=cwd, env=env or os.environ.copy(),
            stdout=self._fh, stderr=subprocess.STDOUT, start_new_session=True,
        )

    def stop(self):
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(self.p.pid), signal.SIGTERM)
        with contextlib.suppress(Exception):
            if self._fh is not subprocess.DEVNULL:
                self._fh.close()

    def tail(self, n: int = 2000) -> str:
        if not self.log_path:
            return "(no log)"
        with contextlib.suppress(Exception):
            return self.log_path.read_text("utf-8", "replace")[-n:]
        return "(unreadable)"


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _start_app(
    port: int, *, with_d1: bool, persist: Path | None, inspector_port: int,
    log: Path | None = None,
) -> Proc:
    argv = [
        shutil.which("wrangler"), "pages", "dev", "dist",
        "--port", str(port), "--ip", "127.0.0.1",
        # A FIXED default inspector port (9229) is why a second `pages dev` in one
        # suite used to die at boot with `Address already in use (127.0.0.1:9229)`
        # — the serving ports were claimed, but the devtools port is shared unless
        # it is named. Two servers in one suite therefore need two of these.
        "--inspector-port", str(inspector_port),
        "--compatibility-date=2026-08-26",
    ]
    if with_d1:
        argv += ["--d1", "SESSIONS"]
    if persist is not None:
        argv += ["--persist-to", str(persist)]
    argv += [
        "-b", f"SUPABASE_URL={MOCK_URL}",
        "-b", "SUPABASE_ANON_KEY=mock-anon-key",
        "-b", f"APP_ORIGIN=http://127.0.0.1:{port}",
    ]
    return Proc(argv, cwd=str(DASHBOARD_DIR), log_path=log)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one in CI.
    require_toolchain()

    global APP_PORT, NOSTORE_PORT, MOCK_PORT, INSPECT_PORT, INSPECT_NOSTORE_PORT
    global APP, NOSTORE, MOCK_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    NOSTORE_PORT = pick_free_port(NOSTORE_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    INSPECT_PORT = pick_free_port(INSPECT_PORT, claimed)
    INSPECT_NOSTORE_PORT = pick_free_port(INSPECT_NOSTORE_PORT, claimed)
    assert len({APP_PORT, NOSTORE_PORT, MOCK_PORT, INSPECT_PORT, INSPECT_NOSTORE_PORT}) == 5
    APP = f"http://127.0.0.1:{APP_PORT}"
    NOSTORE = f"http://127.0.0.1:{NOSTORE_PORT}"
    MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    logs = tmp_path_factory.mktemp("mail-throttle-logs")
    mock = Proc(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env,
        log_path=logs / "mock.log",
    )
    if not _wait(MOCK_PORT):
        mock.stop()
        pytest.fail(f"mock GoTrue failed to start:\n{mock.tail()}")

    # A private D1 for this suite alone, so a neighbouring suite's counters can
    # never decide an assertion here.
    persist = tmp_path_factory.mktemp("mail-throttle-d1")
    # BOTH servers get a private state dir. `--persist-to` is not D1-only — it
    # pins the whole local state root — and two `pages dev` instances sharing the
    # default one race at boot.
    app = _start_app(
        APP_PORT, with_d1=True, persist=persist / "d1",
        inspector_port=INSPECT_PORT, log=logs / "app-d1.log",
    )
    nostore = _start_app(
        NOSTORE_PORT, with_d1=False, persist=persist / "nostore",
        inspector_port=INSPECT_NOSTORE_PORT, log=logs / "app-nostore.log",
    )
    for port, proc, label in ((APP_PORT, app, "D1"), (NOSTORE_PORT, nostore, "no-D1")):
        if not _wait(port):
            for p in (app, nostore, mock):
                p.stop()
            pytest.fail(f"pages dev ({label}) failed to start:\n{proc.tail()}")
    time.sleep(2.5)

    yield {"app": APP, "nostore": NOSTORE, "mock": MOCK_URL}
    for p in (app, nostore, mock):
        p.stop()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(r) -> dict:
    h: dict[str, str] = {}
    for k, v in r.headers.items():
        h[k] = f"{h[k]}, {v}" if k in h else v
    return h


def _post(base: str, path: str, payload: dict | None, ip: str):
    """POST as an identified caller. `CF-Connecting-IP` is the throttle's key."""
    data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(f"{base}{path}", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("CF-Connecting-IP", ip)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace"), _headers(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _headers(e)


def _calls(reset: bool = False) -> list[dict]:
    data = json.dumps({"reset": reset}).encode()
    req = urllib.request.Request(f"{MOCK_URL}/__mock/calls", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["calls"]


def _spend(base: str, path: str, ip: str, email: dict) -> list[tuple[int, str]]:
    """Spend the whole budget for `ip`; returns the (status, body) of each call."""
    return [(lambda r: (r[0], r[1]))(_post(base, path, email, ip)) for _ in range(LIMIT)]


# ---------------------------------------------------------------------------
# HONESTY: over the budget the route refuses, and does not call the provider
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/auth/reset", "/auth/resend"])
def test_over_budget_is_a_429_with_the_shared_contract_and_no_provider_call(stack, path):
    ip = f"198.51.100.1-{path}"
    _calls(reset=True)

    spent = _spend(APP, path, ip, KNOWN)
    assert all(s == 200 for s, _ in spent), spent
    assert len(_calls()) == LIMIT, "each allowed request must have reached GoTrue"

    status, body, headers = _post(APP, path, KNOWN, ip)
    assert status == 429, (
        f"the {LIMIT + 1}th request from one caller must be refused, got {status} {body}"
    )
    payload = json.loads(body)
    # The contract shape the sibling routes already emit — same keys, same
    # `Retry-After` header, so the page's branch and tier mapping are unchanged.
    assert payload["error"] == "rate_limited", payload
    assert payload["providerError"] == "over_email_send_rate_limit", (
        "the client picks its hourly 'email limit reached' copy from this code; a "
        f"different value silently degrades to the minute tier: {payload}"
    )
    assert payload.get("message"), "the refusal must carry a message for API consumers"
    assert KNOWN["email"] not in body, "the refusal must not echo the submitted address"
    retry_after = headers.get("Retry-After")
    assert retry_after and retry_after.isdigit() and int(retry_after) > 0, headers
    assert "no-store" in headers.get("Cache-Control", "").lower(), headers.get("Cache-Control")

    assert len(_calls()) == LIMIT, (
        "the provider was contacted for a request the throttle refused — the budget "
        "is not protecting the upstream email bucket it exists to protect"
    )


def test_a_throttled_request_is_refused_before_the_provider_is_asked(stack):
    """The refusal must be the BFF's OWN decision, not a relayed provider 429.

    Distinguishing them matters: a relayed provider 429 is address-dependent and
    is exactly the oracle this route must not have.
    """
    ip = "198.51.100.2"
    _calls(reset=True)
    _spend(APP, "/auth/reset", ip, KNOWN)
    before = len(_calls())

    status, _, _ = _post(APP, "/auth/reset", KNOWN, ip)
    assert status == 429
    assert len(_calls()) == before, "the throttled request reached GoTrue"


# ---------------------------------------------------------------------------
# NON-ENUMERATION: the refusal carries no account signal
# ---------------------------------------------------------------------------
def test_the_refusal_is_identical_for_a_known_and_an_unknown_address(stack):
    """The load-bearing property: the ANSWER does not vary by address.

    A limiter keyed on the address would answer a registered address differently
    from an unregistered one — the same oracle the fold exists to prevent. Both
    classes are spent to the limit here, so the 200s and the 429s are compared on
    equal footing rather than one being taken on trust.
    """
    ip_known, ip_unknown = "198.51.100.3", "198.51.100.4"
    known_ok = _spend(APP, "/auth/reset", ip_known, KNOWN)
    unknown_ok = _spend(APP, "/auth/reset", ip_unknown, UNKNOWN)
    assert [s for s, _ in known_ok] == [200] * LIMIT, known_ok
    assert [s for s, _ in unknown_ok] == [200] * LIMIT, unknown_ok
    assert known_ok[0][1] == unknown_ok[0][1], (
        "under the limit a known and an unknown address must answer identically:\n"
        f"{known_ok[0][1]}\n{unknown_ok[0][1]}"
    )

    _calls(reset=True)
    s_known, b_known, _ = _post(APP, "/auth/reset", KNOWN, ip_known)
    s_unknown, b_unknown, _ = _post(APP, "/auth/reset", UNKNOWN, ip_unknown)

    assert s_known == 429 and s_unknown == 429, f"{s_known} / {s_unknown}"
    assert b_unknown == b_known, (
        "the throttled answer differs by address — that difference is the account "
        f"oracle:\n{b_known}\n{b_unknown}"
    )
    assert _calls() == [], "a refused request reached GoTrue"


def test_the_budget_is_per_route_not_shared(stack):
    """A reset loop must not spend the resend budget (and vice versa).

    The two routes cross the same provider bucket, but the caller's own budget is
    what this limiter meters; sharing one counter would let a page that shows both
    controls refuse the other one for a request the user never made.
    """
    ip = "198.51.100.5"
    _calls(reset=True)
    _spend(APP, "/auth/reset", ip, KNOWN)
    status, body, _ = _post(APP, "/auth/reset", KNOWN, ip)
    assert status == 429, f"precondition: the reset budget is spent, got {status} {body}"

    status, body, _ = _post(APP, "/auth/resend", {"email": "confirm@example.test"}, ip)
    assert status == 200, (
        f"resend must not inherit the spent reset budget, got {status} {body}"
    )


def test_the_budget_is_charged_to_the_caller_not_the_address(stack):
    """The key is the CALLER. A second caller asking for the SAME address is allowed.

    This is what pins the key, and it is a user-facing property, not a detail: an
    address-keyed limiter lets any caller charge a victim's budget — five requests
    to a colleague's address would lock that person out of password recovery for
    the rest of the window. It is also the shape the fold's own corrected
    mechanism retired: the provider's limiter is project-wide and has no key, so
    "per address" was never this system's model (see `mail-throttle.ts`).
    """
    ip_spender, ip_other = "198.51.100.10", "198.51.100.11"
    _calls(reset=True)
    _spend(APP, "/auth/reset", ip_spender, KNOWN)
    status, body, _ = _post(APP, "/auth/reset", KNOWN, ip_spender)
    assert status == 429, f"precondition: the first caller's budget is spent, got {status} {body}"

    status, body, _ = _post(APP, "/auth/reset", KNOWN, ip_other)
    assert status == 200, (
        "one caller's requests refused a DIFFERENT caller asking for the same "
        f"address — the budget is keyed on the address, not the caller: {status} {body}"
    )


def test_a_malformed_request_does_not_charge_the_budget(stack):
    """The budget meters MAIL requests.

    Validation runs first, so a request that can never send an email cannot spend
    the caller's quota. That ordering is also what keeps the existing suites'
    `400` assertions meaningful once the throttle is wired in.
    """
    ip = "198.51.100.6"
    for _ in range(LIMIT + 2):
        status, body, _ = _post(APP, "/auth/reset", {"email": "not-an-address"}, ip)
        assert status == 400, f"malformed input must stay a 400, got {status} {body}"

    status, body, _ = _post(APP, "/auth/reset", KNOWN, ip)
    assert status == 200, (
        f"malformed requests charged the mail budget, got {status} {body}"
    )


# ---------------------------------------------------------------------------
# The fold is UNCHANGED — the throttle does not replace it
# ---------------------------------------------------------------------------
def test_a_provider_email_bucket_429_is_still_an_indistinguishable_200(stack):
    """#4104's property survives the new limiter.

    Under the budget, a provider refusal must still be folded into the
    enumeration-safe 200: `/recover` answers an address with no account early, so
    surfacing the refusal would be the oracle. The BFF's own 429 is the only 429
    this route may emit.
    """
    ip_normal, ip_limited = "198.51.100.7", "198.51.100.8"
    _calls(reset=True)
    s_normal, b_normal, _ = _post(APP, "/auth/reset", KNOWN, ip_normal)
    s_limited, b_limited, _ = _post(APP, "/auth/reset", RATELIMITED, ip_limited)

    assert s_limited == 200, (
        f"a provider refusal must stay folded into the 200, got {s_limited} {b_limited}"
    )
    assert s_normal == 200, f"the normal address must also be 200, got {s_normal} {b_normal}"
    assert b_limited == b_normal, (
        "a provider refusal produced a different body:\n"
        f"{b_normal}\n{b_limited}"
    )
    assert {c["email"] for c in _calls()} == {KNOWN["email"], RATELIMITED["email"]}, _calls()


# ---------------------------------------------------------------------------
# Degradation: no session store -> no throttle, and no outage
# ---------------------------------------------------------------------------
def test_without_the_sessions_binding_the_route_degrades_to_no_throttle(stack):
    """Fail-open is DELIBERATE, and this pins it so it cannot be "fixed" into a 503.

    A throttle-store gap must not become a mail outage (the #3485 class), and
    failing open changes no security property: the route still folds the provider
    refusal exactly as it did before this change. `SESSIONS` is `required` for the
    dashboard project in `config/required-bindings.yml`, so production carries it.
    """
    ip = "198.51.100.9"
    _calls(reset=True)
    statuses = [s for s, _ in _spend(NOSTORE, "/auth/reset", ip, KNOWN)]
    assert statuses == [200] * LIMIT, statuses

    status, body, _ = _post(NOSTORE, "/auth/reset", KNOWN, ip)
    assert status == 200, (
        f"with no store the route must not refuse, got {status} {body}"
    )
    assert len(_calls()) == LIMIT + 1, "every request should have reached GoTrue"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
