"""#10488 — the comparison that makes a consent-key split one request instead of an investigation.

The subject is `tools/consent_key_check.py`: it reads the fingerprint each half of the consent bridge
publishes (`GET {api}/health` → `consent_token_key`, and `GET {app}/api/consent/key`) and says whether
the two deployments hold the SAME `CONSENT_TOKEN_SECRET`.

WHY THESE PINS MATTER MORE THAN "THE HAPPY PATH PASSES"
--------------------------------------------------------
  * ⛔ **The counterfactual is the point.** On 2026-11-01 the halves held different keys, the mint
    answered `200`, and the AS answered `401 signature does not verify` — every deploy green. A
    checker that only proved AGREE on agreeing halves would have been green through the whole
    incident, so the NEGATIVE arm (two real, different keys ⇒ `MISMATCH`, exit 1) is the test that
    earns this file its place.
  * ⛔ **No key material may reach the output.** CI logs and an issue body are longer-lived and wider
    read than any dashboard. So the tool never accepts a key, and it validates even the fingerprints
    it receives against a fixed shape before printing them: a deployment that answered with the key,
    a prefix of it, or a full digest is reported as `UNVERIFIABLE` and the offending value is NOT
    echoed. The pins below feed it exactly those payloads and assert the value does not appear.
  * ⛔ **"Cannot see" is never "agrees".** A half that 500s, times out, answers non-JSON or omits the
    field is `UNVERIFIABLE` (exit 2) — the fail-closed rule `check-deploy-staleness.sh` states for
    the same reason: a deaf checker is worse than no checker.

Everything here is network-free: the transport is a fake `urlopen`, so the assertions are about the
tool's decision, not about production's current state.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]

_KEY_A = "consent-key-A-0123456789abcdef0123456789abcdef"
_KEY_B = "consent-key-B-0123456789abcdef0123456789abcdef"


def _fp(key: str) -> str:
    """The SAME truncation the two halves publish — computed here so a pin cannot drift from it."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _load_tool() -> ModuleType:
    """Load `tools/consent_key_check.py` by path — it is a script, not an importable package module."""
    path = REPO / "tools" / "consent_key_check.py"
    spec = importlib.util.spec_from_file_location("consent_key_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["consent_key_check"] = module
    spec.loader.exec_module(module)
    return module


CKC = _load_tool()


# ── the transport stand-in ─────────────────────────────────────────────────

class _Response(io.BytesIO):
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False


def _serve(monkeypatch, api: Any, app: Any) -> list[str]:
    """Answer `{api}/health` and `{app}/api/consent/key` from literals; return the URLs asked for.

    A value here is either a JSON-able payload (served as 200) or an exception instance (raised, so
    the tool's transport arms are reachable without a network).
    """
    asked: list[str] = []

    def fake_urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        asked.append(url)
        payload = api if url.endswith(CKC.API_PATH) else app
        if isinstance(payload, Exception):
            raise payload
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(CKC.urllib.request, "urlopen", fake_urlopen)
    return asked


def _health(fingerprint: str | None, configured: bool = True, reason: str | None = None) -> dict:
    return {CKC.PAYLOAD_FIELD: {
        CKC.FINGERPRINT_FIELD: fingerprint,
        CKC.CONFIGURED_FIELD: configured,
        CKC.REASON_FIELD: reason,
    }}


def _pages(fingerprint: str | None, configured: bool = True, reason: str | None = None) -> dict:
    return {
        CKC.FINGERPRINT_FIELD: fingerprint,
        CKC.CONFIGURED_FIELD: configured,
        CKC.REASON_FIELD: reason,
    }


# ── the verdict table ─────────────────────────────────────────────────────

def test_agreement_exits_zero(monkeypatch, capsys) -> None:
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 0
    out = capsys.readouterr().out
    assert "CONSENT-KEY: AGREE" in out
    assert "both halves publish the same key fingerprint" in out, (
        "the AGREE detail must state the agreement — it is pasted into the public alert body"
    )
    # Both readings are SHOWN — an operator comparing terminals needs the numbers, and a checker
    # that only says "equal" cannot be reconciled against a dashboard by eye.
    assert out.count(f"fingerprint={_fp(_KEY_A)}") == 2
    # The two origins are named, so a failure points at the half to fix.
    assert "api.premiselabs.co" in out and "app.premiselabs.co" in out


def test_two_different_keys_are_a_MISMATCH_with_no_key_material(monkeypatch, capsys) -> None:
    """THE NEGATIVE ARM — the 2026-11-01 defect, reproduced with two real keys.

    Two INDEPENDENTLY CONFIGURED halves holding different keys is the whole incident: the mint signs
    with one, the verifier checks with the other, and every grant is refused `401 signature does not
    verify`. The reading is a measured disagreement (exit 1), not "no verdict" — and the output must
    carry neither key, nor a prefix of either, nor a full digest of either.
    """
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(_fp(_KEY_B)))
    assert CKC.main([]) == 1
    out = capsys.readouterr().out
    assert "CONSENT-KEY: MISMATCH" in out
    for secret in (_KEY_A, _KEY_B):
        assert secret not in out
        assert hashlib.sha256(secret.encode()).hexdigest() not in out
        for start in range(0, len(secret) - 8):
            assert secret[start:start + 8] not in out, "a key prefix reached the output"
    # The remedy is THREE steps and the output says so: correcting the dashboard alone leaves the
    # deployed Function signing with the old key (measured 2026-11-01).
    assert "REDEPLOY Pages" in out
    # ⛔ AND STEP 1 IS THE REPO SECRET. This hint used to open with `fly secrets set …`, while §2.5,
    # runbook §4.4 and the workflow's own alert body all say the GitHub repo secret is the source of
    # truth and that a hand-set Fly value is silently reverted by the next `deploy-hosted` run — and
    # those docs send the operator to THIS tool to confirm. An operator following it would rotate Fly,
    # see AGREE, and watch the next deploy revert it: the drift returns silently.
    assert "gh secret set CONSENT_TOKEN_SECRET" in out, (
        "the checker's remedy no longer starts from the GitHub repo secret (the durable source)"
    )
    assert "(1) `fly secrets set" not in out, (
        "the checker's step 1 is `fly secrets set` again — that half is reverted by the next deploy"
    )


def test_one_configured_half_is_a_SPLIT_not_agreement(monkeypatch, capsys) -> None:
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(None, configured=False, reason="not_set"))
    assert CKC.main([]) == 1
    out = capsys.readouterr().out
    assert "CONSENT-KEY: SPLIT" in out
    assert "reason=not_set" in out and "configured=false" in out
    # ⛔ WHICH HALF IS LIVE, pinned in this order. Cycle 8 measured swapping the `{live}`/`{dark}`
    # placeholders GREEN; the detail is the only sentence naming the broken half and it travels
    # verbatim into the public alert, so a swap sends an operator to set the secret on the half that
    # already has it — the mutation must red here, not be resolved by a human reading two log lines.
    assert "api is configured and app is not" in out, out
    # ⛔ AND THE CONSEQUENCE MUST FOLLOW THE DARK HALF. The halves are not interchangeable: `app`
    # MINTS and `api` VERIFIES. A review found this arm rendering "one half signs, the other cannot
    # verify" — which is right when `api` is dark and INVERTED when `app` is, i.e. here. Pinned as the
    # PAIR (the right sentence present, the other orientation's absent) because "contains 'cannot'"
    # would pass on either.
    assert "nothing can sign a consent token" in out, (
        "the api-configured SPLIT no longer says the missing `app` half is the one that MINTS: "
        f"{out!r}"
    )
    assert "every minted token is refused" not in out, (
        "the api-configured SPLIT carries the `api`-is-dark consequence — the roles are inverted, and "
        f"this sentence is pasted into the public alert: {out!r}"
    )


def test_the_OTHER_SPLIT_orientation_states_ITS_own_consequence(monkeypatch, capsys) -> None:
    """The mirror image: `app` configured, `api` dark. One orientation cannot see an inversion.

    With `app` live nothing can VERIFY, so every minted token is refused — the opposite consequence
    and the opposite remediation from the `api`-live arm above. A detail computed from a constant
    string instead of from `dark` renders the wrong one of the two.
    """
    _serve(monkeypatch, _health(None, configured=False, reason="not_set"), _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 1
    out = capsys.readouterr().out
    assert "CONSENT-KEY: SPLIT" in out
    assert "app is configured and api is not" in out, out
    assert "every minted token is refused" in out, (
        f"the app-configured SPLIT does not say the missing `api` half is the one that VERIFIES: {out!r}"
    )
    assert "nothing can sign a consent token" not in out, (
        f"the app-configured SPLIT carries the `app`-is-dark consequence — the roles are inverted: {out!r}"
    )


def test_a_MALFORMED_origin_is_UNVERIFIABLE_and_never_a_DISAGREEMENT(capsys) -> None:
    """⛔ A SCHEME-LESS ORIGIN IS NO MEASUREMENT, and it must not report as one.

    `_get_json` built its `urllib.request.Request` OUTSIDE the `try`, so a scheme-less origin — the
    natural typo in `--api-url api.premiselabs.co` — raised `ValueError("unknown url type")` past
    every handler and the process exited **1**. That is `EXIT_DISAGREE`: the code the runbook publishes
    as "a measured disagreement", i.e. the exact 2026-11-01 reading a human would act on by rotating a
    production secret. Measured at the head before this pin: `--api-url api.example.com` → exit 1, with
    the traceback on stderr.

    Both origins are malformed on purpose, so `Request(...)` raises before any socket is opened and
    this pin stays network-free.
    """
    assert CKC.main(["--api-url", "api.example.com", "--app-url", "app.example"]) == 2
    out = capsys.readouterr().out
    assert "CONSENT-KEY: UNVERIFIABLE" in out, (
        f"a malformed origin must be UNREADABLE, not a disagreement: {out!r}"
    )
    assert "invalid_url" in out, (
        f"the unreadable cause must name the malformed origin, not a generic transport fault: {out!r}"
    )


def test_neither_configured_is_OFF_and_never_AGREE(monkeypatch, capsys) -> None:
    """Consistent, and still not a green reading.

    A monitor that exited 0 here would report "the consent bridge is fine" about a bridge that is
    not enabled — a false PASS. It also must not claim a DRIFT (exit 1): nothing disagrees.
    """
    _serve(monkeypatch, _health(None, configured=False, reason="not_set"),
           _pages(None, configured=False, reason="not_set"))
    assert CKC.main([]) == 2
    out = capsys.readouterr().out
    assert "CONSENT-KEY: OFF" in out and "not enabled" in out
    # ⛔ AND THE ENABLING PATH NAMES THE SAME SOURCE OF TRUTH AS THE MISMATCH ARM. Cycle 6 measured this
    # arm still saying "(set Fly, set Pages, REDEPLOY Pages)" — the hand-set route the sibling arm
    # labels silently reverted by the next `deploy-hosted` run. It is the advice an operator reads when
    # they turn the bridge ON, so following it literally re-creates the 2026-11-01 split.
    assert "rotate the GitHub repo secret" in out, (
        "the OFF arm's enabling path no longer names the repo secret as the durable source"
    )
    assert "set Fly" not in out, (
        "the OFF arm tells the operator to hand-set Fly, which the next deploy silently reverts"
    )


# ── "cannot see" is never "agrees" ────────────────────────────────────────

def test_an_unreachable_half_is_UNVERIFIABLE(monkeypatch, capsys) -> None:
    import urllib.error

    _serve(monkeypatch, urllib.error.URLError("connection refused"), _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 2
    out = capsys.readouterr().out
    assert "CONSENT-KEY: UNVERIFIABLE" in out and "api(unreachable)" in out


def test_an_http_error_is_UNVERIFIABLE(monkeypatch, capsys) -> None:
    import urllib.error

    _serve(monkeypatch, urllib.error.HTTPError("u", 503, "no", {}, None), _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 2
    assert "CONSENT-KEY: UNVERIFIABLE" in capsys.readouterr().out


def test_a_health_payload_without_the_block_is_UNVERIFIABLE(monkeypatch, capsys) -> None:
    """An older deployment that never published the block cannot answer the question.

    Reading its silence as "no key" would compare a MISSING reading against a real one and report a
    SPLIT — a drift verdict about a deployment that was simply never taught the field.
    """
    _serve(monkeypatch, {"status": "ok"}, _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 2
    assert "consent_token_key_absent" in capsys.readouterr().out


def test_a_non_json_body_is_UNVERIFIABLE(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        return _Response(b"<html>WAF</html>")

    monkeypatch.setattr(CKC.urllib.request, "urlopen", fake_urlopen)
    assert CKC.main([]) == 2


def test_a_body_OVER_the_cap_is_refused_rather_than_parsed(monkeypatch) -> None:
    """The `read(MAX_BODY_BYTES)` bound is the OOM backstop, and it is BEHAVIOURAL here.

    `_get_json`'s contract calls the cap load-bearing — *"`response.read()` with no argument buffers
    whatever the peer sends — a hostile or proxy-streamed body could OOM the workflow's 10-minute job
    BEFORE any shape gate runs"*. Nothing else in this file exercised it: measured at this head,
    `response.read(MAX_BODY_BYTES)` -> `response.read()` left **52 passed**, so the stated invariant
    had no pin at all (review finding, cycle 7).

    ⛔ Asserted through the OUTCOME, not through the call's arguments. The payload below is VALID
    JSON — a `_health()` block plus an oversized filler — so it PARSES and yields AGREE if the read
    is unbounded, and TRUNCATES mid-JSON (the filler is the last key, so the closing braces go) and
    yields `body_not_json` if it is bounded. An argument scan would pin the spelling of one call; this
    pins the property the caller needs.
    """
    big = dict(_health(_fp(_KEY_A)))
    big["filler"] = "x" * (CKC.MAX_BODY_BYTES + 1024)
    assert len(json.dumps(big)) > CKC.MAX_BODY_BYTES, "the fixture must exceed the cap or this proves nothing"

    _serve(monkeypatch, big, _pages(_fp(_KEY_A)))
    assert CKC.main([]) == CKC.EXIT_NO_VERDICT, (
        "an oversized body produced a VERDICT — the read is unbounded, so the cap is decorative"
    )


def test_the_transport_bound_reaches_urlopen(monkeypatch) -> None:
    """The `timeout=` on `urlopen` is a second load-bearing bound with no pin.

    Measured at head `ffdcb499839a`: `urlopen(request, timeout=timeout)` -> `urlopen(request)` left
    **52 passed**. A request with no timeout blocks until the socket layer gives up, which on the
    workflow's 10-minute job is the same class of wedge the body cap exists to stop — and the
    tool would report `unreachable` for a hang it caused itself.

    ⛔ TWO DISTINCT VALUES, plus the no-flag default, because ONE value cannot tell the flag from a
    literal. An earlier cut asserted `seen == [7.0, 7.0]`, and the reviewer measured the hole that
    leaves: `urlopen(request, timeout=7.0)` — a hard-coded literal that makes `--timeout` a silent
    no-op — passed `42 passed`, while the docstring claimed "a hard-coded literal reds". Both the
    claim and the pin are fixed here: `7`, `3` and the default `15.0` are three different numbers, so
    no single literal, and no `None`, can satisfy the list.
    """
    seen: list[float | None] = []

    def fake_urlopen(request, timeout=None):
        seen.append(timeout)
        url = request.full_url if hasattr(request, "full_url") else str(request)
        payload = _health(_fp(_KEY_A)) if url.endswith(CKC.API_PATH) else _pages(_fp(_KEY_A))
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(CKC.urllib.request, "urlopen", fake_urlopen)
    assert CKC.main(["--timeout", "7"]) == CKC.EXIT_AGREE
    assert CKC.main(["--timeout", "3"]) == CKC.EXIT_AGREE
    assert CKC.main([]) == CKC.EXIT_AGREE
    assert seen == [7.0, 7.0, 3.0, 3.0, 15.0, 15.0], (
        f"each half must be read under the CLI's own bound and the default must be 15.0, got {seen!r} "
        "— a `None` here is an unbounded request (`urlopen(request)`), and any single repeated value "
        "is a hard-coded literal that makes `--timeout` a silent no-op"
    )


def test_a_DEEPLY_NESTED_body_is_UNVERIFIABLE_and_never_a_DISAGREEMENT(monkeypatch) -> None:
    """⛔ A parse failure that is NOT a `ValueError` still exited **1** — `EXIT_DISAGREE`.

    `json.loads` raises `RecursionError` (a `RuntimeError`, so neither `ValueError` nor
    `UnicodeDecodeError`) on a deeply nested document — measured: 2 000 levels parse fine, 20 000
    raise. That depth is 40 KB here, comfortably inside `MAX_BODY_BYTES`, so the guard (not body
    truncation) is what this pin tests. The guard
    listed only those two names, so the exception escaped `_get_json`, escaped `main`, and the
    process died with **exit 1**, which the module docstring, `--help` and runbook §4.4 publish as
    "a measured disagreement". Same defect class as the malformed-origin arm, one line below it: an
    instrument fault reported as the 2026-11-01 split. `_get_json`'s own docstring promises a problem
    CODE and never an exception, so the guard is the CONTRACT, not a list of exception names.
    """
    nested = b"[" * 20000 + b"]" * 20000

    def fake_urlopen(request, timeout=None):
        return _Response(nested)

    monkeypatch.setattr(CKC.urllib.request, "urlopen", fake_urlopen)
    assert CKC.main([]) == 2, "a document this tool cannot parse is NO MEASUREMENT, not a split"


def test_a_malformed_fingerprint_is_UNVERIFIABLE_and_the_VALUE_IS_NOT_ECHOED(monkeypatch, capsys) -> None:
    """THE DISCLOSURE GATE.

    A half that answered with the key itself, a prefix of it, or a full digest must be refused by
    SHAPE — before anything compares it and before anything prints it. The obvious "helpful"
    implementation (`f"bad fingerprint {value!r}"`) republishes exactly what the check exists to
    contain, into a CI log and an issue body. So each arm asserts the value is absent from BOTH the
    human render and the `--json` payload.
    """
    bad_values = [
        _KEY_A,                                        # the key itself
        _KEY_A[:12],                                   # a prefix of the KEY
        hashlib.sha256(_KEY_A.encode()).hexdigest(),   # the whole digest
        _fp(_KEY_A).upper(),                           # wrong case (a producer drift)
        _fp(_KEY_A) + "0",                             # too long
        "",                                            # empty
        # ⛔ A TRAILING NEWLINE. Python's `$` matches just before it, so a `$`-anchored gate accepts
        # this — then prints a broken log line and compares it as MISMATCH, sending an operator to
        # rotate a key that is not wrong. `\Z` is the anchor that refuses it (measured on `$`).
        _fp(_KEY_A) + "\n",
    ]
    for bad in bad_values:
        _serve(monkeypatch, _health(bad), _pages(_fp(_KEY_A)))
        assert CKC.main([]) == 2, bad
        out = capsys.readouterr().out
        assert "CONSENT-KEY: UNVERIFIABLE" in out
        assert "key_fingerprint_not_12_lowercase_hex" in out
        if bad:
            assert bad not in out, f"the malformed value {bad!r} was echoed into the output"

        _serve(monkeypatch, _health(bad), _pages(_fp(_KEY_A)))
        CKC.main(["--json"])
        blob = capsys.readouterr().out
        parsed = json.loads(blob)
        assert parsed["verdict"] == "UNVERIFIABLE"
        if bad:
            assert bad not in blob, f"the malformed value {bad!r} reached the JSON payload"


def test_an_unconfigured_half_that_still_publishes_a_fingerprint_is_UNVERIFIABLE(monkeypatch, capsys) -> None:
    """A deployment contradicting itself. Comparing either field would be guessing which it meant."""
    _serve(monkeypatch, _health(_fp(_KEY_A), configured=False, reason="not_set"), _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 2
    assert "unconfigured_but_fingerprint_present" in capsys.readouterr().out


def test_a_missing_configured_flag_is_UNVERIFIABLE_not_false(monkeypatch, capsys) -> None:
    """"did not say" and "has no key" are different readings, and only the second can be compared."""
    _serve(monkeypatch, {CKC.PAYLOAD_FIELD: {CKC.FINGERPRINT_FIELD: None}}, _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 2
    assert "configured_not_a_bool" in capsys.readouterr().out


def test_the_CONTRACTED_unreadable_payload_is_UNVERIFIABLE_and_NAMES_its_reason(monkeypatch, capsys) -> None:
    """⛔ `configured: null` IS A CONTRACTED ANSWER, not a malformed body.

    It is what a half publishes when its OWN resolver could not run: `hosted_api._consent_token_key_health()`
    returns exactly `{"key_fingerprint": None, "configured": None, "reason": "unreadable"}` on any
    exception, and SPEC §2.5, runbook §4.4 and this tool's own `REASON_CODES` comment all pair
    `unreadable` with `configured: null`. Refusing it as `configured_not_a_bool` accused the AS of a
    malformed body for the one answer that means "I could not state my key", and left the sixth reason
    code unproducible through the only shape that publishes it.
    """
    unreadable = _health(None, configured=None, reason="unreadable")
    _serve(monkeypatch, unreadable, _pages(_fp(_KEY_A)))
    assert CKC.main([]) == 2
    out = capsys.readouterr().out
    assert "CONSENT-KEY: UNVERIFIABLE" in out, out
    assert "unreadable" in out, f"the diagnosis must name the contracted reason, not a shape fault: {out!r}"
    assert "configured_not_a_bool" not in out, (
        f"the contracted `configured: null` answer was reported as a payload-shape fault: {out!r}"
    )


def test_BOTH_halves_unreadable_is_UNVERIFIABLE_and_never_OFF(monkeypatch, capsys) -> None:
    """⛔ "both resolvers crashed" must not be read as "the feature is disabled".

    `compare` coerces `configured` to bool for the OFF test, and `None` coerces to `False` — so if the
    unreadable gate ever moved below the OFF branch, two crashed halves would report OFF (a
    legitimate steady state) instead of UNVERIFIABLE. The gate is first on purpose; this pins the
    ORDER, which is the whole reason accepting `configured: null` is safe.
    """
    _serve(
        monkeypatch,
        _health(None, configured=None, reason="unreadable"),
        _pages(None, configured=None, reason="unreadable"),
    )
    assert CKC.main([]) == 2
    out = capsys.readouterr().out
    assert "CONSENT-KEY: UNVERIFIABLE" in out, (
        f"two unresolvable halves were read as a configured state: {out!r}"
    )
    assert "CONSENT-KEY: OFF" not in out, (
        f"an unreadable bridge was reported as a DISABLED one — `None` coerced to `False`: {out!r}"
    )


def test_a_sub_3_12_interpreter_exits_NO_VERDICT_not_a_disagreement() -> None:
    """⛔ The interpreter guard used to exit **1** — the tool's `EXIT_DISAGREE`.

    `SystemExit(<str>)` exits 1, which the module's own table, `--help` and runbook §4.4 publish as
    "MISMATCH/SPLIT — a measured disagreement". So an environment fault (a 3.11 `python3`, the
    shebang's own interpreter) reported itself as the 2026-11-01 key split — the third instance of a
    class this file fixes twice for a malformed origin and an unparseable body. NO measurement is
    exit 2. Run in a SUBPROCESS because the guard is module-level, and `sys.version_info` is patched
    before the source is compiled so the real guard executes.
    """
    guard = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.version_info=(3,11,0); "
            f"exec(compile(open({str(REPO / 'tools/consent_key_check.py')!r}).read(), 'ckc', 'exec'))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert guard.returncode == 2, (
        "a <3.12 interpreter must report NO VERDICT (2), not a measured disagreement: "
        f"got {guard.returncode}; stderr={guard.stderr[-300:]!r}"
    )
    assert "requires Python >= 3.12" in guard.stderr, (
        f"the refusal message is written separately (SystemExit(int) prints nothing): {guard.stderr!r}"
    )


# ── the tool cannot be handed a key ───────────────────────────────────────

def test_the_tool_has_no_way_to_be_given_a_key(capsys) -> None:
    """⛔ The strongest form of "no key material in the output": it never holds one.

    No `--secret`, no `--key`, no environment read at all. A checker that accepted the key could
    (and eventually would) print it into a workflow log, and the two halves' own fingerprints are
    all the comparison needs.

    ⛔ The assertion is on the OPTION SET and on the SOURCE, not on a rejection message: argparse
    echoes the offending argv in its own usage error, so "the key was not printed when we passed it"
    is not a property this tool can hold. What it can hold — and what is asserted — is that there is
    no option to pass it through and no env lookup that could pick it up.
    """
    import contextlib
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        CKC.main(["--help"])
    help_text = buf.getvalue()
    assert "--api-url" in help_text and "--timeout" in help_text, help_text
    assert "--secret" not in help_text and "--key" not in help_text, help_text
    source = (REPO / "tools" / "consent_key_check.py").read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source, (
        "the tool reads the environment — it could pick up CONSENT_TOKEN_SECRET and print it"
    )

    # And an unknown option is a usage error (exit 2), never a silent no-op that would leave the
    # operator believing a key had been compared.
    with pytest.raises(SystemExit) as exc:
        CKC.main(["--secret", "x"])
    assert exc.value.code == 2


def test_the_json_payload_carries_the_verdict_and_both_readings(monkeypatch, capsys) -> None:
    """The workflow files an issue from this payload, so it must be self-describing."""
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(_fp(_KEY_B)))
    assert CKC.main(["--json"]) == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["verdict"] == "MISMATCH"
    assert parsed["exit_code"] == 1
    assert {r["half"] for r in parsed["readings"]} == {"api", "app"}
    assert {r["fingerprint"] for r in parsed["readings"]} == {_fp(_KEY_A), _fp(_KEY_B)}
    assert parsed["fix"] and "THREE steps" in parsed["fix"]
    # ⛔ THE `detail` SENTENCE IS PASTED INTO THE PUBLIC ALERT, and it is the only line that says WHAT
    # is wrong. Cycle 8 measured swapping it for another arm's sentence GREEN here — an operator reading
    # a MISMATCH alert whose detail says "both halves publish the same key fingerprint" has no way to
    # tell which of the two contradictory statements to believe.
    assert parsed["detail"] == (
        "both halves are configured and hold DIFFERENT keys — every minted consent token is "
        "refused `401 signature does not verify`"
    ), parsed["detail"]


def test_the_urls_asked_for_are_the_two_contracted_surfaces(monkeypatch) -> None:
    """One request each, to the surfaces the halves actually publish on."""
    asked = _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(_fp(_KEY_A)))
    assert CKC.main(["--api-url", "https://api.example", "--app-url", "https://app.example"]) == 0
    assert sorted(asked) == ["https://api.example/health", "https://app.example/api/consent/key"]


def test_a_CREDENTIAL_BEARING_origin_is_never_echoed(monkeypatch, capsys) -> None:
    """⛔ The origin was the ONE string in the public issue body nothing gated.

    `Reading.url` is printed in the CI log AND `cat`-ed into an auto-filed issue in a PUBLIC repo, and
    `--api-url`/`--app-url` are free-form (the workflow's `workflow_dispatch` inputs). The natural
    shape of a protected preview origin is `https://user:token@host`, so echoing it verbatim
    republishes a credential — the disclosure-amplifier behaviour every other field here refuses.
    """
    secret = "s3cr3t-token-value"
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(_fp(_KEY_A)))
    assert CKC.main([
        "--api-url", f"https://user:{secret}@api.example/health?tok={secret}#frag",
        "--app-url", f"https://app.example/api/consent/key?tok={secret}",
        "--json",
    ]) == 0
    out = capsys.readouterr().out
    assert secret not in out, (
        f"a credential-bearing origin was republished into the issue body: {out[:400]!r}"
    )
    urls = [r["url"] for r in json.loads(out)["readings"]]
    assert all("user:" not in u and "?" not in u and "#" not in u for u in urls), urls
    assert any(u == "https://api.example/health" for u in urls), (
        f"redaction must keep scheme+host+path so the reading still names its half: {urls}"
    )


# ── the fourth disclosure gate: `reason` ──────────────────────────────────

def test_a_key_placed_in_reason_is_NEVER_ECHOED(monkeypatch, capsys) -> None:
    """THE GATE THE FIRST CUT MISSED, and the reason it matters is the ISSUE BODY.

    `reason` is a string chosen by a REMOTE deployment, and this tool's output is pasted verbatim into
    a PUBLIC auto-filed issue (`.github/workflows/consent-key-drift.yml` `cat`s the `--json` payload
    into the body) as well as into the CI log. An earlier cut shape-gated `key_fingerprint` and passed
    `reason` through untouched — so a half that answered with the key in `reason` (a debug build, a
    proxy error page, a future field mix-up) would have had it republished by the very monitor built to
    contain it. A raw `reason` is also the injection path into the issue body's ```json fence.

    The gate is a CLOSED SET, not a charset filter: `^[a-z][a-z0-9_]{0,31}$` would still admit a
    32-character all-lowercase key.
    """
    for hostile in (_KEY_A, _KEY_A[:12], "-----BEGIN PRIVATE KEY-----", "line1\nline2",
                    "```", "x" * 200, "NOT_SET",
                    # ⛔ THE TWO THIS DOCSTRING NAMES AS THE BYPASS, actually fed. Cycle 8 measured the
                    # gate degraded to `re.match(r"[a-z][a-z0-9_]{0,31}\Z", raw_reason)` GREEN here,
                    # because every value above is ALSO refused by a charset filter — so the bypass the
                    # comment documents was untested, and a 32-char all-lowercase key was published
                    # verbatim into the public issue body.
                    "passwordpasswordpasswordpassword",
                    "a" * 64,
                    "0f" * 32):
        _serve(monkeypatch, _health(_fp(_KEY_A), reason=hostile), _pages(_fp(_KEY_A)))
        assert CKC.main([]) == 2, hostile
        out = capsys.readouterr().out
        assert "CONSENT-KEY: UNVERIFIABLE" in out
        assert "reason_not_a_code" in out
        assert hostile not in out, f"a hostile `reason` ({hostile[:20]!r}…) was echoed into the log"

        _serve(monkeypatch, _health(_fp(_KEY_A), reason=hostile), _pages(_fp(_KEY_A)))
        CKC.main(["--json"])
        blob = capsys.readouterr().out
        assert json.loads(blob)["verdict"] == "UNVERIFIABLE"
        assert hostile not in blob, "a hostile `reason` reached the JSON payload (→ the issue body)"


def test_a_SPLIT_never_borrows_the_ROTATION_remedy(monkeypatch, capsys) -> None:
    """⛔ A SPLIT IS NOT A ROTATION, and the hint is pasted into the same body as the alert prose.

    One half holds NO usable key, so there is no second value to reconcile. The MISMATCH hint's step 1
    is `gh secret set CONSENT_TOKEN_SECRET` followed by a `deploy-hosted` run — a ONE-SIDED rotation,
    which from a SPLIT state MANUFACTURES the measured disagreement the tool exists to detect (the
    2026-11-01 incident). The workflow's own SPLIT arm forbids "a ONE-sided rotation" a few lines above
    the `cat` that embeds this `fix` string, so emitting the rotation here makes the public issue
    contradict itself AND names the harmful step.

    ⛔ THIS IS THE `app`-DARK ORIENTATION. The `api`-dark absence case is a DIFFERENT hint: Fly is
    CI-managed from the repo secret, so its repair IS `gh secret set` + `deploy-hosted` and only the
    bare `fly secrets set` is forbidden. Pinned separately below, because a single unconditional
    "never `gh secret set`" would send an `api`-dark operator to the one write the same body says the
    next deploy reverts.
    """
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(None, configured=False, reason="not_set"))
    assert CKC.main(["--json"]) == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["verdict"] == "SPLIT"
    hint = parsed["fix"]
    assert "do NOT rotate" in hint, f"the SPLIT hint must refuse the rotation: {hint!r}"
    assert "tortoise-dashboard" in hint and "REDEPLOY Pages" in hint, (
        f"the app-dark hint must name the Pages home and its redeploy: {hint!r}"
    )
    for forbidden in ("THREE steps", "gh secret set"):
        assert forbidden not in hint, (
            f"the SPLIT hint carries MISMATCH's rotation remedy ({forbidden!r}), which CREATES the "
            f"split it is meant to resolve: {hint!r}"
        )


def test_an_api_dark_SPLIT_sends_the_operator_to_the_REPO_SECRET_not_fly_secrets(monkeypatch, capsys):
    """⛔ THE ABSENCE REPAIR DEPENDS ON WHICH HOME IS DARK, and the wrong one clears then re-reds.

    Fly's copy is staged by CI from the GitHub repo secret (`fly-managed-secrets.txt` declares
    `CONSENT_TOKEN_SECRET gh-secret:CONSENT_TOKEN_SECRET`), so a hand `fly secrets set` is silently
    reverted by the next `deploy-hosted` run. An `api`-dark split therefore cannot be repaired the way
    an `app`-dark one is: the durable move is to put the LIVE (Pages) value into the repo secret and
    redeploy. The old hint said "set the SAME value on the dark half" without naming the route, and
    the same issue body warns — 37 lines later — that the direct Fly write is reverted. Following it
    produced `AGREE`, then `MISMATCH` after the next deploy, i.e. the "every consent grant is dead"
    finding for a fault that was `not_set`.
    """
    _serve(monkeypatch, _health(None, configured=False, reason="not_set"), _pages(_fp(_KEY_A)))
    assert CKC.main(["--json"]) == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["verdict"] == "SPLIT", "an api-dark half must compare as SPLIT, not UNVERIFIABLE"
    hint = parsed["fix"]
    assert "GITHUB REPO SECRET" in hint and "gh secret set" in hint, (
        f"the api-dark hint must route through the repo secret: {hint!r}"
    )
    assert "deploy-hosted" in hint, f"the api-dark hint must land the deploy that stages Fly: {hint!r}"
    assert "fly secrets set" in hint and "reverts" in hint, (
        f"the api-dark hint must warn that the direct Fly write is reverted: {hint!r}"
    )
    # ⛔ The SOURCE of the copied value (Pages) enforces NONE of the AS's floors, so the api-dark arm
    # must state the precondition and the fallback: without them the operator performs the prescribed
    # write, the AS refuses the short/padded value, and the monitor re-reds with a VALUE-FAULT reason
    # whose remedy ("do NOT rotate"/copy) is the opposite of the one just followed.
    assert "Pages accepts any non-empty string" in hint, (
        f"the api-dark hint must warn that the Pages source enforces no floors: {hint!r}"
    )
    assert "rotate BOTH halves instead" in hint, (
        f"the api-dark hint must name the fallback when the copied value fails the AS's floors: {hint!r}"
    )
    for code in ("too_short", "padded", "not_utf8", "reused_credential"):
        assert code in hint, f"the api-dark hint no longer names the floor it can fail (`{code}`)"
    assert "do NOT rotate" in hint, f"a one-sided copy is the repair here, not a rotation: {hint!r}"
    assert "TWO-SIDED" not in hint, f"a rotation is the needless repair here: {hint!r}"


def test_every_published_reason_code_is_accepted(monkeypatch, capsys) -> None:
    """The closed set is exactly what either half can publish — no wider, no narrower.

    Source of truth: `tortoise/consent_token.py::_SECRET_PROBLEM_CODES` (the five provisioning
    classes) plus `unreadable`, which `hosted_api._consent_token_key_health()` emits with
    `configured: null` when the resolver itself cannot run. Asserted against the CODE, so a code added
    on the publishing side without touching this tool reds here rather than going silently UNVERIFIABLE
    in production.
    """
    from tortoise import consent_token

    expected = set(consent_token._SECRET_PROBLEM_CODES) | {"unreadable"}
    assert expected == CKC.REASON_CODES, (
        "the checker's closed set drifted from what the halves publish — either extend both or the "
        "`reason` field goes UNVERIFIABLE on a real reading"
    )
    for code in sorted(expected):
        _serve(monkeypatch, _health(_fp(_KEY_A), reason=code), _pages(_fp(_KEY_A), reason=code))
        assert CKC.main([]) == 0, code
        assert f"reason={code}" in capsys.readouterr().out

    # ⛔ The remedy split is DERIVED, for the VALUE-FAULT side only: `VALUE_FAULT_REASONS` is
    # `REASON_CODES` minus the absence literal, so a seventh code the halves start publishing lands in
    # the ROTATION arm — the repair that always works — with nobody editing this split. That is the
    # fail-closed direction, and it is why the derivation sits on this side.
    # ⚠️ DECLARED RESIDUAL: a seventh code that is an ABSENCE fault would be given the rotation, which
    # it does not need. Nothing here can infer "absence" from a string, and a code the halves start
    # publishing cannot be seen as an omission at all — it simply joins `VALUE_FAULT_REASONS`.
    # The guard is the EXACT-SET equality below: `ABSENCE_REASONS` is pinned to the two literals, so any
    # later edit to it must be deliberate. It cannot detect an omission and does not claim to.
    # ⛔ The two set-algebra lines above are true by construction — `VALUE` is *defined* as
    # `REASON` minus `ABSENCE`, so a new code joins it automatically — and catch nothing.
    # ⛔ An earlier cut of this block added `assert len(CKC.ABSENCE_REASONS) == 2` and called it the
    # guard. It was unreachable: the exact-set line beneath it already requires that set to be the
    # two literals, so the length arm could never be the one that fails (measured: a third absence code
    # reds the exact-set line, never the length). A dead assertion advertised as the guard is the class
    # this PR removes, not ships.
    assert CKC.ABSENCE_REASONS | CKC.VALUE_FAULT_REASONS == CKC.REASON_CODES
    assert not (CKC.ABSENCE_REASONS & CKC.VALUE_FAULT_REASONS)
    assert {"not_set", "unreadable"} == CKC.ABSENCE_REASONS, (
        "the absence set moved — classify every new code here deliberately. An absence fault is "
        "repaired by copying the working half's value onto the empty one; every OTHER code is given "
        "the rotation, so an absence code left out of this literal sends an operator to rotate a key "
        "that a copy would have fixed"
    )


def test_a_value_fault_SPLIT_never_tells_the_operator_to_copy_the_working_value(
    monkeypatch, capsys
) -> None:
    """⛔ THE SPLIT REMEDY IS TWO REMEDIES, and the wrong one can never clear the alert.

    A SPLIT says one half is dark — but not that it is EMPTY. Reachable, measured: the Pages half has
    NO length floor (`consent-bridge.ts::consentKeyFingerprint` guards only `if (!secret)`) while
    `consent_token.py` refuses anything under 32 bytes with `too_short`. Set the SAME 20-byte key on
    BOTH halves and you get `configured: true` from Pages and `configured: false, reason: "too_short"`
    from the AS. The OLD hint told the operator to copy the working half's value onto the dark half —
    which reproduces `too_short` for as long as they keep following it, so the monitor stayed red and
    its own documented remedy was why. The same holds for `padded`, `not_utf8` and `reused_credential`.

    So each absence code must name the copy repair, each value-fault code must name the two-sided
    rotation, and NEITHER may offer a ONE-sided rotation (from a SPLIT state that manufactures the
    2026-11-01 disagreement).
    """
    for reason in sorted(CKC.VALUE_FAULT_REASONS):
        _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(None, configured=False, reason=reason))
        assert CKC.main(["--json"]) == 1, reason
        hint = json.loads(capsys.readouterr().out)["fix"]
        assert "TWO-SIDED rotation" in hint, (
            f"the `{reason}` SPLIT hint must name the two-sided rotation — copying the working value "
            f"reproduces the same refusal: {hint!r}"
        )
        assert "REDEPLOY" in hint and "§2.5" in hint, reason
        # ⛔ AND IT MUST NOT BE A CERTAINTY. The tool compares FINGERPRINTS only, so it cannot see
        # whether the working half holds a DIFFERENT usable value (a copy would clear the fault) or
        # the same unusable one (a copy reproduces it). Claiming the latter is what pushed an operator
        # whose copy would have WORKED into rotating the production signing key on both halves.
        assert "MIGHT" in hint, (
            f"the `{reason}` hint states the copy as impossible; it can only say it MAY reproduce the "
            f"refusal, because a copy might also clear it: {hint!r}"
        )
        assert "always works" in hint, (
            f"the `{reason}` hint must justify the rotation as the repair that ALWAYS works: {hint!r}"
        )
        # ⛔ It must still forbid the HALF-rotation, which is the harm this arm also has to prevent.
        assert "Never set only ONE half" in hint, f"{reason}: the one-sided prohibition is gone"

    for reason in sorted(CKC.ABSENCE_REASONS):
        _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(None, configured=False, reason=reason))
        assert CKC.main(["--json"]) == 1, reason
        hint = json.loads(capsys.readouterr().out)["fix"]
        assert "do NOT rotate" in hint and "TWO-SIDED" not in hint, (
            f"the `{reason}` SPLIT hint must keep the copy repair — that half holds nothing to "
            f"reproduce: {hint!r}"
        )


def test_the_workflow_prose_branches_the_SPLIT_remedy_the_same_way() -> None:
    """The alert body and the tool's `fix` string are read as ONE message, so they cannot disagree.

    The workflow echoes the SPLIT remedy and the `--json` payload (which carries `fix`) is `cat`ed into
    the SAME issue body. A workflow that still says "set the SAME value on that half" for every SPLIT
    would contradict the tool on exactly the value-fault case, and the operator would follow whichever
    line they read first.

    ⛔ PER-BULLET, not presence-only. The first cut of this pin asserted only that the two bullet
    MARKERS and the string "FULL rotation" appeared somewhere in the body — so SWAPPING the two
    remedies (giving `not_set` the full rotation and `too_short` the copy) left it GREEN, i.e. it
    could not fail on the exact defect its docstring names. Measured, not assumed. Each bullet is now
    extracted and its own remedy asserted, so a swap reds here.
    """

    def _bullet(marker: str) -> str:
        """The echoed text of ONE bullet, from its marker to the next bullet / the closing line."""
        body = "\n".join(s.get("run") or "" for s in _steps())
        lines = body.splitlines()
        starts = [i for i, line in enumerate(lines) if marker in line]
        assert len(starts) == 1, f"expected exactly one `{marker}` bullet, found {len(starts)}"
        out: list[str] = []
        for line in lines[starts[0] :]:
            if out and ("echo '- **" in line or "Never set ONE half and STOP" in line):
                break
            out.append(line)
        return "\n".join(out)

    absence = _bullet("`not_set` / `unreadable`")
    fault = _bullet("`padded` / `too_short` / `not_utf8` / `reused_credential`")

    # The absence arm: copy the live value, down the route that half is actually written through.
    assert "Copy the working half" in absence, "the absence arm lost its copy repair"
    assert "FULL rotation" not in absence, (
        "the absence arm was given the value-fault rotation — that half holds nothing, so a copy IS "
        "the repair and a rotation is the needless, disruptive one"
    )
    assert "gh secret set" in absence and "fly secrets set" in absence, (
        "the absence arm no longer names the api-dark route — for an `api`-dark split the durable "
        "repair is the repo secret, not `fly secrets set`, and every deploy reverts the latter"
    )

    # The value-fault arm: the always-works rotation, and never a copy stated as the repair.
    assert "FULL rotation" in fault, (
        "the value-fault arm no longer sends the operator to §2.5's full rotation"
    )
    assert "Copy the working half" not in fault, (
        "the value-fault arm was given the copy remedy — it reproduces the same refusal"
    )
    for code in ("padded", "too_short", "not_utf8", "reused_credential"):
        assert code in fault, f"the value-fault arm no longer names `{code}`"

    body = "\n".join(s.get("run") or "" for s in _steps())
    assert "Never set ONE half and STOP" in body, (
        "the SPLIT alert lost its one-sided-rotation prohibition"
    )
    # ⛔ AND IT MUST NOT FORBID THE REPAIR IT PRESCRIBES. Two earlier cuts failed this one way each.
    # The first said "Never set only ONE half, from either state" — six lines under a bullet telling
    # the operator to set the working half's value on the empty one, i.e. a remedy and an absolute
    # prohibition of that remedy in the same public issue. The second narrowed it to "set one half to
    # a NEW value … that CREATES this split", which still forbade step 1 of the FULL rotation the
    # value-fault bullet prescribes three lines above, and mis-described the state it produces (a
    # half-rotation yields MISMATCH, not this SPLIT). What is TRUE, and what is pinned now: setting
    # one half and STOPPING is the harm; both prescribed repairs pass through one-half changes and
    # each says so in its own bullet.
    assert "Never set only ONE half" not in body, (
        "the SPLIT alert forbids setting one half outright — which is exactly what its own absence "
        "bullet prescribes for a `not_set`/`unreadable` half"
    )
    assert "that CREATES this split" not in body, (
        "the SPLIT alert says a one-sided change CREATES this split — but the value-fault bullet "
        "prescribes a rotation whose step 1 is a one-sided change, and a half-rotation produces a "
        "MISMATCH, not a SPLIT"
    )
    # ⛔ The opening sentence must not contradict the value-fault bullet three lines below it: the
    # first cut said "rotating a key would not fix it" UNCONDITIONALLY, and the same body then
    # prescribed a rotation — three mutually exclusive instructions in one public issue.
    assert "a ONE-sided rotation would not fix it" in body, (
        "the SPLIT opening must forbid the ONE-SIDED rotation specifically; an unconditional "
        "\"rotating a key would not fix it\" contradicts the value-fault remedy below it"
    )


def test_a_null_reason_is_legitimate(monkeypatch) -> None:
    """A configured half has nothing to report — `None` is not a fault, and must not become one."""
    _serve(monkeypatch, _health(_fp(_KEY_A), reason=None), _pages(_fp(_KEY_A), reason=None))
    assert CKC.main([]) == 0


def test_the_REQUESTS_carry_our_OWN_user_agent(monkeypatch) -> None:
    """⛔ LOAD-BEARING, measured in production: the edge in front of the Pages project refuses
    urllib's DEFAULT agent (`Python-urllib/3.12` — exactly what the workflow's pinned
    `python-version: 3.12` produces) with `403 error code: 1010`, while curl, python-requests, a
    browser agent and even the lowercase `python-urllib/3.12` pass.

    So without an explicit agent the Pages half is PERMANENTLY unreadable from CI: the monitor files
    "the key could not be verified" every 30 minutes and never reaches a comparison. This test pins the
    header on both requests, because one request without it is the same defect with a shorter window.
    """
    seen: list[str | None] = []

    def fake_urlopen(request, timeout=None):
        seen.append(request.get_header("User-agent"))
        return _Response(json.dumps(_health(_fp(_KEY_A))).encode())

    monkeypatch.setattr(CKC.urllib.request, "urlopen", fake_urlopen)
    CKC.main([])
    assert len(seen) == 2, "expected one request per half"
    assert seen == [CKC.USER_AGENT, CKC.USER_AGENT]
    # …and the agent must NOT be urllib's default: that is the value the edge refuses.
    assert not (seen[0] or "").startswith("Python-urllib")


# ── the workflow is the monitor: its alerting contract cannot go hollow ────

WORKFLOW = REPO / ".github/workflows/consent-key-drift.yml"


def _workflow() -> dict:
    # `on:` parses as the BOOLEAN True under YAML 1.1, so every access below is by job/step, never by
    # that key — the shape `tests/test_deploy_workflow.py` established for this repo's workflows.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["check"]["steps"]


def _case_arm_title_vars(body: str) -> dict[str, str]:
    r"""Map each `case` arm label to the title VARIABLE its body binds, from the shell text.

    The workflow's filing step is a `case` over the verdict, and the arm -> title binding is what
    decides which standing issue a finding lands on. A regex over `\w+_title=` cannot see it: the
    arm's binding is `title="$split_title"`, whose left-hand side is `title`, so such a pattern
    matches only the three literal CONSTANTS and asserts that three distinct strings are distinct —
    true by construction, and GREEN with the SPLIT arm re-pointed at another arm's title (measured).
    Walking the lines is what mirrors the shell: an arm label line opens a scope, and the FIRST
    `title="$variable"` inside it is that arm's binding.
    """
    arm_vars: dict[str, str] = {}
    current: str | None = None
    for line in body.splitlines():
        arm = re.match(r"\s+([A-Za-z*][\w*]*)\)\s*$", line)
        if arm:
            current = arm.group(1)
            continue
        bind = re.search(r'title="\$(\w+)"', line)
        if bind and current is not None and current not in arm_vars:
            arm_vars[current] = bind.group(1)
    return arm_vars


def test_the_workflow_files_the_drift_ON_A_FAILING_READING() -> None:
    """⛔ `if: always() && !cancelled() && outcome != 'skipped'` IS THE ALERTING CONTRACT.

    A red verdict FAILS the check step (`exit "$rc"`), so `if: success()` would skip the filing step
    exactly when it must fire — the deaf-monitor class #2140/#2189 recorded. The second clause is not
    belt-and-braces in the other direction: an `always()` step also runs when an EARLIER step failed,
    and if checkout or setup-python failed then no reading exists — the body's fallback would present a
    synthetic `UNVERIFIABLE` as "The reading (this run)" and point an operator at two PRODUCTION
    deployments for what is a runner fault. A step that RAN and failed has outcome `failure`, not
    `skipped`, so the red path this step exists for is untouched.

    ⛔ AND THE THIRD CLAUSE, `!cancelled()`, is the same hazard through the last open door:
    `steps.<id>.outcome` is `success|failure|cancelled|skipped`, so a cancel landing INSIDE the
    compare step (a manual `gh run cancel`, a superseded scheduled run, a job-level cancel) yields
    `cancelled` — which SATISFIES `!= 'skipped'`, while `always()` is precisely the predicate that
    runs a step on a cancelled run. The filing step would then alert on a run that measured neither
    half and hand an operator two PRODUCTION deployments. Pinned because the two clauses above were
    each added after a reviewer found the previous cut could look green (or alert) on evidence it
    never had.
    """
    filing = [s for s in _steps() if "Auto-file" in (s.get("name") or "")]
    assert len(filing) == 1, "exactly one auto-file step"
    condition = filing[0]["if"]
    assert "always()" in condition, f"the filing step must survive a failing check step, got {condition!r}"
    assert "!cancelled()" in condition, (
        "a CANCELLED run must not file an alert: the cancelled check step's outcome is `cancelled`, "
        f"which passes `!= 'skipped'`, so the alert would assert a cause it never measured: {condition!r}"
    )
    assert "steps.check.outcome != 'skipped'" in condition, (
        f"a skipped check step must NOT file a fabricated reading, got {condition!r}"
    )

    # ⛔ AND THE CHECK STEP MUST STILL FAIL THE RUN. `if: always()` files the alert; `exit "$rc"` is
    # what makes a MEASURED disagreement a RED check rather than a green run with a note — the PR's
    # whole headline ("a red check, not a silent 401"). Both halves are pinned because either alone
    # leaves the instrument able to look green while the halves disagree: MEASURED at this head, the
    # mutation `exit "$rc"` -> `exit 0` left this file's 22 tests AND 444 workflow-wide tests GREEN.
    failing = [s for s in _steps() if (s.get("run") or "").rstrip().endswith('exit "$rc"')]
    assert len(failing) == 1, (
        "exactly one step must propagate the reading's rc, or a MISMATCH/SPLIT exits 0 and the \"red "
        f"check\" is green: {[s.get('name') for s in _steps()]}"
    )
    assert not failing[0].get("continue-on-error"), (
        "the check step must never `continue-on-error` — that turns a measured split into a green run"
    )

    # ⛔ AND THE STEP MUST STILL DO THE MEASURING. Cycle 2 measured the content of this step unpinned:
    # replacing the invocation with `rc=0` left 36/36 consent tests GREEN (a run that reads nothing
    # exits 0, so the auto-file step takes the GREEN path, RESOLVES both standing issues and files
    # nothing — a monitor reporting green forever while clearing its own ledger), and dropping the two
    # `--*-url` flags left 23/23 green (a `workflow_dispatch` with `api_url`/`app_url` — the ad-hoc
    # path §2.5 and runbook §4.4 both send the operator to — would silently read PRODUCTION).
    check_run = failing[0].get("run") or ""
    for needle in (
        "tools/consent_key_check.py",
        '--api-url "$API_URL"',
        '--app-url "$APP_URL"',
        "--json > ",
    ):
        assert needle in check_run, (
            f"the check step no longer invokes the checker with {needle!r} — the run would exit with no "
            f"reading (or measure the wrong origins) and still look green: {check_run[:400]!r}"
        )

    # The reading is handed between the check step and BOTH consumers through one path; if the writer
    # and a reader diverge, the reader's `|| echo '{"verdict": "UNVERIFIABLE"}'` fabricates a reading
    # into a PUBLIC issue body presented as "The reading (this run)" and the real evidence is lost —
    # the hazard the `outcome != 'skipped'` clause above closes, through a different door. Pinned as a
    # SET of every `/tmp/consent-key*.json` spelling in the file, so a one-sided rename reds here.
    paths = {
        m
        for s in _steps()
        for m in re.findall(r"/tmp/consent-key[\w.-]*\.json", s.get("run") or "")
    }
    assert paths == {"/tmp/consent-key.json"}, (
        f"the reading's path diverges between producer and consumer(s): {sorted(paths)}"
    )


def test_the_check_step_exits_with_the_code_the_checker_returned(tmp_path) -> None:
    """⛔ BEHAVIOURAL, because a TEXT SCAN CANNOT TELL A LIVE PREDICATE FROM A DEAD ONE.

    Cycle 3 measured the presence pin above as insufficient: `rc=$?` -> `rc=0` (invocation left
    intact), a commented-out invocation, and `rc=$? || true` ALL stayed green across the consent
    suite and 537 workflow-wide tests. A one-token edit therefore restores this PR's headline defect:
    a live MISMATCH exits 0, so the filing step takes its GREEN path and `resolve`s BOTH standing
    issues with "the two live deployments published the same key fingerprint (AGREE)" — a false
    AGREE written into the public ledger, no alert, green run list.

    So the step body is EXECUTED here against a stubbed `python3` that returns 1 with a MISMATCH
    reading, and the assertions are on the exit status and on the values it wrote to
    `$GITHUB_OUTPUT` — the two things the rest of the workflow actually consumes. No network.
    """
    import os
    import stat

    failing = [s for s in _steps() if (s.get("run") or "").rstrip().endswith('exit "$rc"')]
    assert len(failing) == 1, "the check step is gone — re-anchor this pin rather than deleting it"

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "python3"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "-c" ]; then exec {sys.executable!r} "$@"; fi\n'
        "echo '{\"verdict\": \"MISMATCH\", \"exit_code\": 1}'\n"
        "exit 1\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    gh_out = tmp_path / "github_output"
    gh_out.write_text("")

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(gh_out),
        "API_URL": "https://api.example.test",
        "APP_URL": "https://app.example.test",
    }
    import fcntl

    reading = Path("/tmp/consent-key.json")
    err = Path("/tmp/consent-key.err")
    # ⛔ PARALLEL-SAFE, and it deletes only what IT created. The step body's paths are literal, so this
    # test and another copy of itself write the same two files: measured, 4 concurrent copies x 5 rounds
    # gave 12/20 RED (`verdict=UNKNOWN`), a peer's cleanup unlinking the reading between the step's
    # write and its verdict extraction. A pre-existing `/tmp/consent-key.json` (an operator's real
    # reading) was also overwritten and then deleted. An exclusive lock on a companion path serialises
    # the copies without touching the shipped body, which is the thing under test.
    lock = Path("/tmp/consent-key.json.lock").open("a+")  # noqa: SIM115 — the flock handle must OUTLIVE a block scope, or closing it releases the lock; it is released explicitly in `finally`
    fcntl.flock(lock, fcntl.LOCK_EX)
    pre_existing = {p: p.read_bytes() for p in (reading, err) if p.exists()}
    try:
        # ⛔ NEUTRALISE BEFORE THE RUN, inside the lock. Otherwise a stale reading from any earlier run
        # (or a concurrent copy's in-flight output) is read by this pin as if it were the step's own:
        # measured, with `{"verdict":"MISMATCH"}` already at the path, `--json > /dev/null` — a step
        # that writes NO reading — stayed 27/27 GREEN, and the same arm without a stale file was RED.
        # And capturing the snapshot OUTSIDE the lock made a concurrent run "restore" a peer's stub
        # output over an operator's real file, i.e. it destroyed the bytes this pin promises to keep.
        for stale in (reading, err):
            stale.unlink(missing_ok=True)
        # `bash -e`, because Actions runs `run:` bodies as `bash -e {0}` — that is WHY the body carries
        # `set +e`. Under `bash -c` a deleted `set +e` was invisible (measured GREEN); under `-e` it
        # reds, which is what the real runner would do: abort at the checker before `$GITHUB_OUTPUT`.
        proc = subprocess.run(
            ["bash", "-e", "-c", failing[0]["run"]], env=env, capture_output=True, text=True, timeout=120
        )
        written = gh_out.read_text()
    finally:
        for leftover in (reading, err):
            if leftover in pre_existing:
                leftover.write_bytes(pre_existing[leftover])  # an operator's real reading is not ours
            else:
                leftover.unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    assert proc.returncode == 1, (
        "the check step did not propagate the checker's exit code — a MISMATCH would be a GREEN run "
        "and the filing step would resolve the standing ledger with a false AGREE. "
        f"got rc={proc.returncode}; stdout={proc.stdout[-400:]!r} stderr={proc.stderr[-400:]!r}"
    )
    assert "rc=1" in written, (
        "`$GITHUB_OUTPUT` must carry the checker's rc — the filing step branches on `RC` to decide "
        f"whether to RESOLVE the standing ledger. got {written!r}"
    )
    assert "verdict=MISMATCH" in written, (
        f"the verdict extracted from the reading must reach `$GITHUB_OUTPUT`, got {written!r}"
    )

    # ⛔ AND AN UNREADABLE READING MUST DEFAULT TO `UNKNOWN`, NEVER TO A DISAGREEMENT. Cycle 5 measured
    # `|| echo UNKNOWN` -> `|| echo MISMATCH` GREEN, because the pin above only ever tested a VALID
    # stub reading. A run that measured nothing (the checker OOM-killed, the runner's 10-minute
    # timeout, `python3` dying before the JSON landed) would then file "the two halves hold DIFFERENT
    # keys" and send the operator to rotate a production secret — and an operator who rotates one half
    # CREATES the split, which is the 2026-11-01 incident this PR exists to catch.
    (bindir / "python3").write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "-c" ]; then exec {sys.executable!r} "$@"; fi\n'
        "exit 2\n"
    )
    gh_out.write_text("")
    lock2 = Path("/tmp/consent-key.json.lock").open("a+")  # noqa: SIM115 — the flock handle must OUTLIVE a block scope, or closing it releases the lock; it is released explicitly in `finally`
    fcntl.flock(lock2, fcntl.LOCK_EX)
    keep = {p: p.read_bytes() for p in (reading, err) if p.exists()}
    try:
        for stale in (reading, err):
            stale.unlink(missing_ok=True)
        proc = subprocess.run(
            ["bash", "-e", "-c", failing[0]["run"]], env=env, capture_output=True, text=True, timeout=120
        )
        written = gh_out.read_text()
    finally:
        for leftover in (reading, err):
            if leftover in keep:
                leftover.write_bytes(keep[leftover])  # restored here too: this arm must not eat it either
            else:
                leftover.unlink(missing_ok=True)
        fcntl.flock(lock2, fcntl.LOCK_UN)
        lock2.close()
    assert proc.returncode == 2, proc.stderr[-300:]
    assert "verdict=UNKNOWN" in written, (
        "an UNREADABLE reading must default to UNKNOWN — a fallback to a disagreement files a rotation "
        f"alert for a run that measured nothing: {written!r}"
    )


def test_the_SCRIPT_exits_non_zero_on_a_measured_split(tmp_path) -> None:
    """⛔ THE PROCESS EXIT STATUS, which is the only thing the workflow actually consumes.

    Every other test here calls `CKC.main([])` IN PROCESS and asserts the RETURN VALUE. Cycle 7
    measured the consequence: turning `raise SystemExit(main())` into a bare `main()` makes the tool
    print `CONSENT-KEY: MISMATCH` and exit 0 — every test green — and the whole chain then goes green
    with the split live: `rc=0` -> the filing step's GREEN branch -> `resolve` BOTH standing issues
    with "the two live deployments published the same key fingerprint (AGREE)", dedupe key released,
    next tick files a fresh issue. Cycles C1/C3 pinned that the workflow PROPAGATES the rc; nothing
    pinned that the checker PRODUCES one.

    So this arm spawns the script as a PROCESS against a real local origin (a socket, not the
    in-process `urlopen` fake, because `__main__` is what is under test) and reads `returncode`.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    api_payload = json.dumps(_health(_fp(_KEY_A))).encode()
    app_payload = json.dumps(_pages(_fp(_KEY_B))).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # http.server's contract (N802 is not in the repo's select)
            body = api_payload if self.path.startswith(CKC.API_PATH) else app_payload
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools" / "consent_key_check.py"),
             "--api-url", base, "--app-url", base],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "CONSENT-KEY: MISMATCH" in proc.stdout, (proc.stdout[-500:], proc.stderr[-500:])
    assert proc.returncode == 1, (
        "the SCRIPT must exit non-zero on a measured split — `raise SystemExit(main())` is what makes "
        "the exit code the verdict; without it the workflow reads rc=0, resolves both standing issues "
        f"with a false AGREE and never alerts. got rc={proc.returncode}"
    )


def test_the_SCRIPT_exits_zero_on_agreement_and_two_when_nothing_was_measured() -> None:
    """The other two exit codes, as a PROCESS — an operator scripts against these."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    same = json.dumps(_health(_fp(_KEY_A))).encode()
    app_same = json.dumps(_pages(_fp(_KEY_A))).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # http.server's contract
            body = same if self.path.startswith(CKC.API_PATH) else app_same
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        agree = subprocess.run(
            [sys.executable, str(REPO / "tools" / "consent_key_check.py"),
             "--api-url", base, "--app-url", base],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert agree.returncode == 0, f"agreement must exit 0 as a process, got {agree.returncode}"

    # A closed port: nothing was measured, so it is 2 (UNVERIFIABLE), never 0 and never 1.
    unreachable = subprocess.run(
        [sys.executable, str(REPO / "tools" / "consent_key_check.py"),
         "--api-url", "http://127.0.0.1:1", "--app-url", "http://127.0.0.1:1"],
        capture_output=True, text=True, timeout=180,
    )
    assert unreachable.returncode == 2, (
        "an unreadable origin is NO MEASUREMENT (exit 2) — never 0 (agreement) and never 1 (a "
        f"measured disagreement), got {unreachable.returncode}: {unreachable.stdout[-300:]}"
    )


def test_the_filing_step_only_RESOLVES_the_ledger_on_a_MEASURED_AGREE() -> None:
    """The GREEN branch closes BOTH standing issues; nothing pinned its condition.

    Cycle 3 measured `if [ "${RC:-}" = "0" ]` -> `if [ "1" = "1" ]` GREEN: the run stays red but
    `resolve` is called unconditionally, closing the ledger with "the two live deployments published
    the same key fingerprint (AGREE)" while every consent grant is dead — and, because the drift
    issue is then gone, the next tick files a FRESH one (the dedupe key was released).

    Pinned LINE-ANCHORED and uncommented, so a `# `-prefixed condition (which bash would fail on, but
    which a plain substring check would accept) is not enough to satisfy it.
    """
    body = "\n".join(s.get("run") or "" for s in _steps())
    assert re.search(r'^\s*if \[ "\$\{RC:-\}" = "0" \]; then$', body, re.MULTILINE), (
        "the filing step no longer branches on a MEASURED AGREE (`RC` == 0) — it would resolve the "
        "standing ledger (and release the dedupe key) on a split"
    )
    agree = body.index('${RC:-}" = "0"')
    resolve = body.index("auto-file-issue.sh resolve")
    assert resolve > agree, (
        "`resolve` must sit INSIDE the RC == 0 branch, not before it: a split would clear the ledger"
    )


@pytest.fixture()
def clean_reading():
    """Neutralise `/tmp/consent-key.json` for the duration, under the lock the sibling pins use.

    The filing step's body `cat`s that ABSOLUTE path into the public issue body, so whatever is on
    disk becomes the body's "reading". A leftover real MISMATCH reading contains `401 signature does
    not verify` — which the SPLIT pin asserts must be ABSENT — so a correct change would red on a
    file this test never wrote. Only bytes THIS fixture removes are removed; a pre-existing file (an
    operator's real reading) is restored byte-for-byte, and the `flock` serialises concurrent copies
    of this suite, exactly as `test_the_check_step_exits_with_the_code_the_checker_returned` does.
    """
    import fcntl

    reading = Path("/tmp/consent-key.json")
    lock = Path("/tmp/consent-key.json.lock").open("a+")  # noqa: SIM115 — the flock handle must OUTLIVE a block scope, or closing it releases the lock; it is released explicitly in `finally`
    fcntl.flock(lock, fcntl.LOCK_EX)
    pre_existing = reading.read_bytes() if reading.exists() else None
    reading.unlink(missing_ok=True)
    try:
        yield reading
    finally:
        if pre_existing is None:
            reading.unlink(missing_ok=True)
        else:
            reading.write_bytes(pre_existing)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def test_the_filing_step_files_the_RIGHT_finding_and_never_resolves_a_split(
    tmp_path, clean_reading
) -> None:
    """EXECUTED, because the filing step's reading->ledger mapping was entirely unexercised.

    Cycle 4 measured six one-token edits GREEN on the decision half of this workflow: `RC:` set to a
    literal, `RC:` deleted, `VERDICT:` set to a literal, the verdict fallback flipped to MISMATCH, the
    two findings' titles swapped, and the AGREE branch's `resolve` removed. Each produces a wrong
    ledger: a split clearing both standing issues with "published the same key fingerprint (AGREE)"
    (and releasing the dedupe key, so the next tick files a duplicate), or an unmeasured run alerting
    "the two halves hold DIFFERENT keys", which is the claim the file's own comment forbids.

    So the body runs against a stubbed `auto-file-issue.sh` that records its argv.
    """
    import os
    import stat

    filing = [s for s in _steps() if "Auto-file" in (s.get("name") or "")]
    assert len(filing) == 1, "exactly one auto-file step"
    root = tmp_path / "root"
    (root / ".github" / "scripts").mkdir(parents=True)
    record = tmp_path / "calls.txt"
    stub = root / ".github" / "scripts" / "auto-file-issue.sh"
    # Contract-faithful: the real `af_file_or_comment` refuses a `file` with no readable `--body-file`
    # (`[ -f "$body_file" ] || af_err "body file not found"; return 2`). A stub that records argv and
    # models nothing let `--body-file "$body"` be DELETED with 40/40 green — an alert the substrate
    # refuses, i.e. no issue, no recurrence comment, no ledger entry, while every test passed.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$REC\"\n"
        'case "$1" in\n'
        "  file)\n"
        "    bf=\"\"\n"
        "    while [ $# -gt 0 ]; do [ \"$1\" = '--body-file' ] && bf=\"$2\"; shift; done\n"
        "    if [ -z \"$bf\" ] || [ ! -f \"$bf\" ]; then echo 'BODY-FILE-MISSING' >> \"$REC\"; exit 2; fi\n"
        "    cp \"$bf\" \"$REC.body\"\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    def run(rc: str, verdict: str | None) -> subprocess.CompletedProcess:
        record.write_text("")
        env = {
            **os.environ,
            "REC": str(record),
            "RC": rc,
            "GITHUB_SERVER_URL": "https://gh.example.test",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "1",
        }
        if verdict is not None:
            env["VERDICT"] = verdict
        return subprocess.run(
            ["bash", "-e", "-c", filing[0]["run"]],
            cwd=root, env=env, capture_output=True, text=True, timeout=120,
        )

    drift = "the consent bridge's two halves hold different keys"
    split = "the consent bridge is only HALF configured"
    unknown = "the consent bridge's key could not be verified"

    # A GREEN reading resolves ALL THREE titles and files nothing.
    proc = run("0", "AGREE")
    calls = [c for c in record.read_text().splitlines() if c.strip()]
    assert proc.returncode == 0, proc.stderr[-400:]
    assert len(calls) == 3, f"AGREE must resolve every finding in the ledger, got {calls}"
    assert all(c.startswith("resolve") for c in calls), calls
    assert any(drift in c for c in calls) and any(unknown in c for c in calls), calls
    assert any(split in c for c in calls), (
        f"AGREE did not resolve the HALF-configured issue, so a recovered split stays open forever: {calls}"
    )
    # ⛔ AND THE CLOSING COMMENT MUST ASSERT THE AGREEMENT, not a disagreement. Cycle 8 measured the
    # drift issue being CLOSED with `--why "…hold DIFFERENT keys"` GREEN: the ledger's own record of
    # recovery would contradict the recovery it records, on a public issue an operator reads later.
    assert all("(AGREE)" in c for c in calls), (
        f"each resolve comment must state the agreement it is closing on: {calls}"
    )
    assert not any("DIFFERENT keys" in c for c in calls), calls

    # A MEASURED split files the DRIFT title, resolves nothing, and does not clear the ledger.
    proc = run("1", "MISMATCH")
    calls = [c for c in record.read_text().splitlines() if c.strip()]
    assert proc.returncode == 0, proc.stderr[-400:]
    assert len(calls) == 1 and calls[0].startswith("file"), calls
    assert drift in calls[0] and unknown not in calls[0], (
        f"a MISMATCH must file the DRIFT finding, got {calls}"
    )
    assert not any(c.startswith("resolve") for c in calls), (
        "a split cleared the standing ledger — a false AGREE written into the public issue"
    )
    # ⛔ EACH ARM'S OWN PROSE. Cycle 7 measured the body assertions below reading the file produced by
    # the LAST run (the UNKNOWN arm), so the two arms' text was interchangeable: swapping the UNKNOWN
    # body's claim for the DRIFT arm's left every test green, and an operator holding a transient-503
    # alert would read "every consent grant is dead" and rotate a production secret — CREATING the
    # split. The comment at the `case` states this contract ("the alert never asserts a disagreement
    # that was not measured"); nothing observed it.
    drift_body = Path(str(record) + ".body").read_text()
    assert "hold DIFFERENT keys" in drift_body and "Every consent grant is dead" in drift_body, (
        "the DRIFT arm's body no longer asserts the measured disagreement it is filed for"
    )
    assert "could NOT be verified" not in drift_body, (
        "the DRIFT arm's body carries the UNVERIFIED claim — the two arms swapped their prose"
    )

    # An UNMEASURED run files the OTHER title: an unreadable reading is never a disagreement (§2.5).
    for verdict in ("UNVERIFIABLE", "OFF", "UNKNOWN"):
        proc = run("2", verdict)
        calls = [c for c in record.read_text().splitlines() if c.strip()]
        assert len(calls) == 1 and calls[0].startswith("file"), (verdict, calls)
        assert unknown in calls[0] and drift not in calls[0], (
            f"{verdict} measured neither half, so it must NOT assert a disagreement: {calls}"
        )

    # ⛔ AND WITH `VERDICT` ABSENT ENTIRELY — the state a body aborted by `bash -e` before
    # `$GITHUB_OUTPUT` leaves behind. Cycle 5 measured `case "${VERDICT:-UNKNOWN}"` ->
    # `case "${VERDICT:-MISMATCH}"` GREEN, because every arm above SET the variable. The default must
    # be UNKNOWN: a runner fault is not a measured key disagreement.
    proc = run("2", None)
    calls = [c for c in record.read_text().splitlines() if c.strip()]
    assert len(calls) == 1 and calls[0].startswith("file"), calls
    assert unknown in calls[0] and drift not in calls[0], (
        "with no VERDICT at all the alert must say the key could NOT be verified, not that the halves "
        f"differ: {calls}"
    )

    # The filed BODY carries the dedupe marker and the step with no reminder — both unpinned before.
    filed = Path(str(record) + ".body")
    assert filed.exists(), "the stub never received a readable --body-file — the alert could not be filed"
    body = filed.read_text()
    assert "<!-- consent-key-drift-alert -->" in body, "the dedupe marker is gone from the alert body"
    assert "REDEPLOY Pages" in body and "fly secrets set" in body, (
        "the alert body lost the rotation steps an operator acts on"
    )
    # ⛔ AND THE BODY MUST CARRY THE EVIDENCE. Cycle 8 measured the fenced reading replaced by `echo
    # '{}'` GREEN: the alert is this monitor's ONLY output, and `### The reading (this run)` with an
    # empty object sends the operator to the dashboard with nothing to compare.
    assert '"verdict"' in body, (
        f"the alert body no longer quotes the reading it was filed for: {body[:400]!r}"
    )
    # …and THIS arm (the last run above) is the UNMEASURED one, so its prose must say so.
    assert "could NOT be verified" in body and "not** a measured disagreement" in body, (
        "the UNKNOWN arm's body must state that nothing was compared — an unreadable reading is not a "
        f"disagreement: {body[:400]!r}"
    )
    assert "Every consent grant is dead" not in body, (
        "the UNKNOWN arm's body asserts a disagreement it did not measure"
    )

    # ⛔ SPLIT IS ITS OWN FINDING — it must not reuse the MISMATCH title or body. Nothing exercised
    # this arm before, so `MISMATCH|SPLIT)` was green while a half-configured bridge alerted "the two
    # halves hold DIFFERENT keys" and "the AS answers 401": with `app` dark there is no mint at all,
    # and with `api` dark the AS answers 503 before verifying. A rotation does not fix an unset key.
    proc = run("1", "SPLIT")
    calls = [c for c in record.read_text().splitlines() if c.strip()]
    assert proc.returncode == 0, proc.stderr[-400:]
    assert len(calls) == 1 and calls[0].startswith("file"), calls
    assert split in calls[0] and drift not in calls[0], (
        f"a SPLIT must file the HALF-configured finding, not the key-disagreement one: {calls}"
    )
    split_body = Path(str(record) + ".body").read_text()
    assert "HALF configured" in split_body and "NOT \"the two halves hold different keys\"" in split_body, (
        "the SPLIT body must say one half holds NO key, not that the two differ"
    )
    assert "Every consent grant is dead" not in split_body, (
        "the SPLIT body carries the MISMATCH prose, which names a rotation that does not fix it"
    )
    assert "401" not in split_body, (
        "the SPLIT body claims the 401 signature symptom — with a half dark the AS answers 503 "
        "(or nothing is minted at all), so the operator is sent to the wrong fault"
    )


def test_a_ZERO_BYTE_reading_never_renders_an_EMPTY_fence(tmp_path) -> None:
    """⛔ `cat` FAILS OPEN ON A 0-BYTE FILE, so `-s` — never `cat` alone — must trigger the fallback.

    The compare step opens the reading with `--json > /tmp/consent-key.json`, and the shell creates
    that file BEFORE `python3` has written a byte. So the run where the checker itself died (OOM, the
    runner's timeout, an import error) is exactly the run that leaves a 0-byte file behind: `cat`
    SUCCEEDS on it and prints nothing, so `|| echo …` never fires and the alert renders an EMPTY
    fenced block under "### The reading (this run)" — asserting a cause with no reading at all.

    Both arms, because `-s` alone is not the property under test: an unconditional `echo <fallback>`
    would satisfy a fallback-only pin while destroying EVERY real reading, which is the fail-closed
    error in the other direction (the alert discarding the evidence it exists to publish).
    """
    import fcntl
    import os
    import stat

    filing = [s for s in _steps() if "Auto-file" in (s.get("name") or "")]
    assert len(filing) == 1, "exactly one auto-file step"
    root = tmp_path / "root"
    (root / ".github" / "scripts").mkdir(parents=True)
    record = tmp_path / "calls.txt"
    stub = root / ".github" / "scripts" / "auto-file-issue.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$REC\"\n"
        'case "$1" in\n'
        "  file)\n"
        '    bf=""\n'
        "    while [ $# -gt 0 ]; do [ \"$1\" = '--body-file' ] && bf=\"$2\"; shift; done\n"
        "    if [ -z \"$bf\" ] || [ ! -f \"$bf\" ]; then echo 'BODY-FILE-MISSING' >> \"$REC\"; exit 2; fi\n"
        '    cp "$bf" "$REC.body"\n'
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    reading = Path("/tmp/consent-key.json")
    # Same parallel-safety contract as `test_the_check_step_exits_with_the_code_the_checker_returned`:
    # the body's paths are LITERAL, so an exclusive lock serialises copies and only bytes THIS test
    # wrote are removed — an operator's real reading is restored, never deleted.
    lock = Path("/tmp/consent-key.json.lock").open("a+")  # noqa: SIM115 — the flock handle must OUTLIVE a block scope, or closing it releases the lock; it is released explicitly in `finally`
    fcntl.flock(lock, fcntl.LOCK_EX)
    pre_existing = reading.read_bytes() if reading.exists() else None
    try:

        def rendered_reading() -> str:
            record.unlink(missing_ok=True)
            Path(str(record) + ".body").unlink(missing_ok=True)
            proc = subprocess.run(
                ["bash", "-e", "-c", filing[0]["run"]],
                cwd=root,
                env={
                    **os.environ,
                    "REC": str(record),
                    "RC": "2",
                    "GITHUB_SERVER_URL": "https://gh.example.test",
                    "GITHUB_REPOSITORY": "o/r",
                    "GITHUB_RUN_ID": "1",
                },
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert proc.returncode == 0, proc.stderr[-400:]
            body = Path(str(record) + ".body").read_text()
            block = re.search(r"### The reading.*?```json\n(.*?)```", body, re.S)
            assert block is not None, f"the body lost its reading fence: {body[:400]!r}"
            return block.group(1)

        # ARM 1 — the 0-byte file a dead checker leaves behind.
        reading.write_bytes(b"")
        empty_arm = rendered_reading()
        assert empty_arm.strip(), (
            "a 0-byte reading rendered an EMPTY fence under 'The reading (this run)': `cat` succeeds "
            "on an empty file, so the fallback never fired and the alert asserts a cause with no "
            "reading at all"
        )
        assert "the tool wrote no reading" in empty_arm, (
            f"the empty-reading arm did not render the fallback: {empty_arm!r}"
        )

        # ARM 2 — a real reading must still render ITS OWN bytes, not the fallback.
        reading.write_bytes(b'{"verdict": "MISMATCH"}\n')
        real_arm = rendered_reading()
        assert "MISMATCH" in real_arm, (
            f"a NON-EMPTY reading was replaced by the fallback: {real_arm!r}"
        )
        assert "the tool wrote no reading" not in real_arm, (
            "the fallback fires on a real reading — the alert would discard its own evidence"
        )
    finally:
        if pre_existing is None:
            reading.unlink(missing_ok=True)
        else:
            reading.write_bytes(pre_existing)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def test_the_step_outputs_are_WIRED_from_the_check_step_and_the_dispatch_inputs() -> None:
    """The CONSUMER half of the C4 pin: what the filing step READS must be what the check step WRITES.

    Cycle 4 measured `RC: ${{ steps.check.outputs.rc }}` -> `RC: '0'` GREEN, and deleting the line
    GREEN too — the producer side was pinned, the wiring was not. Same for the dispatch inputs: with
    `API_URL`/`APP_URL` hardcoded to production a `workflow_dispatch` with `api_url`/`app_url` — the
    ad-hoc path §2.5 and runbook §4.4 send the operator to — silently reads PRODUCTION.
    """
    filing = [s for s in _steps() if "Auto-file" in (s.get("name") or "")]
    assert len(filing) == 1
    assert filing[0]["env"]["RC"] == "${{ steps.check.outputs.rc }}", (
        "the filing step must read the check step's `rc`, not a literal — otherwise a split resolves "
        "the ledger with a false AGREE"
    )
    assert filing[0]["env"]["VERDICT"] == "${{ steps.check.outputs.verdict }}", (
        "the filing step must read the verdict the check step extracted, or every run alerts as if a "
        "disagreement was measured"
    )
    check_env = next(
        s for s in _steps() if (s.get("run") or "").rstrip().endswith('exit "$rc"')
    )["env"]
    assert "github.event.inputs.api_url" in check_env["API_URL"], (
        f"the dispatch's `api_url` input must reach the checker, got {check_env['API_URL']!r}"
    )
    assert "github.event.inputs.app_url" in check_env["APP_URL"], (
        f"the dispatch's `app_url` input must reach the checker, got {check_env['APP_URL']!r}"
    )
    assert not filing[0].get("continue-on-error"), (
        "the filing step must never `continue-on-error` — a 403 or a bare-pool runner without `jq` "
        "would then lose the alert and the recurrence comment while the run reported success (the "
        "deaf-detector class the file's own #2140 comment exists for)"
    )


def test_the_workflow_grants_the_alert_scope_and_runs_on_its_stated_cadence() -> None:
    """The two settings the monitor needs and NOTHING else asserted — both measured undefended.

    MEASURED at this head: dropping `issues: write` from `permissions:` left `test_consent_key_check.py`
    and the wider workflow suites GREEN (112 tests in the narrower run), while
    `.github/scripts/auto-file-issue.sh` would 403 and the monitor would file NOTHING on a red reading —
    the deaf-detector-while-looking-green class the file's own `#2140` comment exists for. And changing
    the cron to `'0 3 * * *'` was green everywhere, while the workflow header, §2.5, §4.4 and the alert
    body all state "every 30 minutes".

    The scope is asserted as an EQUALITY, not a presence check: an added `contents: write` is a real
    widening of what a scheduled job can do to the repo, and a presence test would not see it.
    """
    workflow = _workflow()
    assert workflow.get("permissions") == {"contents": "read", "issues": "write"}, (
        "the monitor's scopes moved — `contents: read` is all checkout needs, `issues: write` is the "
        "alert; without the latter the detector is deaf and still green: "
        f"{workflow.get('permissions')!r}"
    )
    # `on:` parses as the BOOLEAN True under YAML 1.1 (see `_workflow`), so it is reached that way.
    triggers = workflow[True]
    assert triggers["schedule"] == [{"cron": "11,41 * * * *"}], (
        "the stated cadence (every 30 min, offset off the hour boundary) is what the docs and the "
        f"alert body promise; got {triggers.get('schedule')!r}"
    )
    assert "workflow_dispatch" in triggers, (
        "§2.5 and §4.4 both tell the operator to trigger this run by hand for an ad-hoc rotation check"
    )
    # ⛔ The TRIGGER SET, not just the two expected members. The merge-rail exemption is by EVENT, not
    # by workflow (`NON_CODE_EVENTS`, `tools/merge_throughput.py`), so a non-`schedule` trigger makes
    # THIS job's `check` run attach to the ref's sha as a check — and `check` is one of the names
    # `docs/ci/required-contexts.json` now records this workflow as emitting. This job reads
    # PRODUCTION state, so it is red BY CONSTRUCTION during any rotation (`MISMATCH`) or edge 503
    # (`UNVERIFIABLE`); as a `push`/`pull_request` check that red would land on `main` or on an
    # unrelated PR. MEASURED at this head: adding `push: {branches: [main]}` — and separately
    # `pull_request:` — left this whole file GREEN (43 passed each), so a one-line edit converts a
    # monitor into a repo-wide red. Asserted as an EQUALITY for the same reason the scopes above are.
    assert set(triggers) == {"schedule", "workflow_dispatch"}, (
        "a non-`schedule` trigger makes this monitor's `check` a check-run on the merged ref — and it "
        "reads production, so a rotation or an edge 503 would red `main` and every PR. The monitor's "
        f"trigger set is load-bearing; got {sorted(map(str, triggers))!r}"
    )
    assert "concurrency" not in workflow or workflow["concurrency"].get("cancel-in-progress") is False, (
        "a cancelled tick measures nothing — `cancel-in-progress` must stay false"
    )


def test_the_THREE_dedupe_TITLES_are_stable_constants() -> None:
    """The title IS the dedupe key (`auto-file-issue.sh` searches `in:title` and comments a recurrence
    rather than creating a second issue). Renaming one abandons the standing ledger issue and starts a
    second streak — at 48 runs/day that is the duplicate spam the substrate exists to prevent. So all
    three titles are pinned byte-exactly, and they must differ: they assert DIFFERENT findings.

    ⛔ THREE, not two. `SPLIT` was filed under the MISMATCH title, whose body claims the halves "hold
    DIFFERENT keys" and that the AS answers `401 signature does not verify`. For a SPLIT both are
    false — one half holds NO key, so there is no second key to differ from, and with `api` dark the
    AS answers `503` before it ever verifies anything. An operator reading that alert is sent to
    rotate a key when the fault is an unset/padded secret on one half.
    """
    body = "\n".join(s.get("run") or "" for s in _steps())
    drift = "CONSENT_TOKEN_SECRET: the consent bridge's two halves hold different keys (#10488)"
    split = "CONSENT_TOKEN_SECRET: the consent bridge is only HALF configured (#10488)"
    unknown = "CONSENT_TOKEN_SECRET: the consent bridge's key could not be verified (#10488)"
    assert drift in body, "the drift title changed — that abandons the standing issue"
    assert split in body, (
        "the SPLIT title is gone — a half-configured bridge would file under the MISMATCH title, whose "
        "body asserts a rotation the fault does not need"
    )
    assert unknown in body, "the unknown title changed — that abandons the standing issue"
    # ⛔ THE DISTINCTNESS MUST BE READ OFF THE WORKFLOW's CASE ARMS, not off the three title
    # CONSTANTS. The form this replaced matched `(\w+_title)=(["'])(.+?)\2`, which occurs ONLY on the
    # three literal assignments (`drift_title="…"`, eight lines up) — so it re-asserted that three
    # distinct constants are distinct (true by construction) and never saw `title="$split_title"`,
    # the binding that actually decides WHICH ISSUE a finding files under. MEASURED, not reasoned: with
    # the SPLIT arm re-pointed at `$drift_title` that form stayed GREEN, i.e. it could not fail for the
    # exact defect its own message named — the dead-assertion class this PR has now shipped three
    # times. What is parsed here is arm -> title VARIABLE -> pinned title, end to end.
    arm_vars = _case_arm_title_vars(body)
    assert arm_vars == {"MISMATCH": "drift_title", "SPLIT": "split_title", "*": "unknown_title"}, (
        f"the case arms no longer bind the three title variables they are meant to: {arm_vars}"
    )
    constants = {
        name: title for name, _q, title in re.findall(r"(\w+_title)=([\"'])(.+?)\2", body)
    }
    assert set(constants) == {"drift_title", "split_title", "unknown_title"}, (
        f"the three pinned titles are no longer assigned: {sorted(constants)}"
    )
    assert len(set(constants.values())) == 3, (
        f"two arms would file under the SAME dedupe title, merging two findings into one issue: {constants}"
    )
    assert set(constants.values()) == {drift, split, unknown}, (
        f"an arm's title is not one of the three pinned findings: {constants}"
    )
    # ⛔ THE WHOLE PATH, so a re-pointed ARM reds too (the previous form's blind spot) and not only a
    # re-pointed constant: each case label must resolve, through its own binding, to its own finding.
    assert constants[arm_vars["MISMATCH"]] == drift, (
        "the MISMATCH arm does not file under the drift title — a measured disagreement would file "
        "under a title whose body says the halves are only half configured"
    )
    assert constants[arm_vars["SPLIT"]] == split, (
        "the SPLIT arm does not file under the split title — a half-configured bridge would file under "
        "a title whose body asserts a different fault, and an operator is sent to the wrong repair"
    )
    assert constants[arm_vars["*"]] == unknown, (
        "the default arm does not file under the unknown title — an unreadable reading would file as "
        "a measured fault it never measured"
    )


def test_the_workflow_steps_are_all_syntactically_valid_shell() -> None:
    """Every `run:` body is shell; a parse error is an `exit 127`-shaped instrument fault, not a verdict."""
    for step in _steps():
        body = step.get("run")
        if not body:
            continue
        result = subprocess.run(["bash", "-n", "-c", body], capture_output=True, text=True)
        assert result.returncode == 0, f"{step.get('name')}: {result.stderr[:300]}"


def test_the_workflow_asks_for_the_tools_the_ALERT_NEEDS() -> None:
    """`auto-file-issue.sh::af_have_gh` requires `gh` AND `jq`; providing only `gh` lets the filing step
    die with "gh and jq are required" on the fallback runner pool — i.e. this monitor's only output
    cannot be produced. Both are named in the runner tool contract."""
    contract = [s for s in _steps() if "runner-tools.sh" in (s.get("run") or "")]
    assert len(contract) == 1
    run = contract[0]["run"]
    assert "ensure gh jq" in run, f"the tool contract supplies only gh: {run!r}"
