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
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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


def test_one_configured_half_is_a_SPLIT_not_agreement(monkeypatch, capsys) -> None:
    _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(None, configured=False, reason="not_set"))
    assert CKC.main([]) == 1
    out = capsys.readouterr().out
    assert "CONSENT-KEY: SPLIT" in out
    assert "reason=not_set" in out and "configured=false" in out


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


def test_the_urls_asked_for_are_the_two_contracted_surfaces(monkeypatch) -> None:
    """One request each, to the surfaces the halves actually publish on."""
    asked = _serve(monkeypatch, _health(_fp(_KEY_A)), _pages(_fp(_KEY_A)))
    assert CKC.main(["--api-url", "https://api.example", "--app-url", "https://app.example"]) == 0
    assert sorted(asked) == ["https://api.example/health", "https://app.example/api/consent/key"]
