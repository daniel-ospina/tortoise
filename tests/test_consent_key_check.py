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
                    "```", "x" * 200, "NOT_SET"):
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


def test_the_workflow_files_the_drift_ON_A_FAILING_READING() -> None:
    """⛔ `if: always() && outcome != 'skipped'` IS THE ALERTING CONTRACT — do not "tidy" it.

    A red verdict FAILS the check step (`exit "$rc"`), so `if: success()` would skip the filing step
    exactly when it must fire — the deaf-monitor class #2140/#2189 recorded. The second clause is not
    belt-and-braces in the other direction: an `always()` step also runs when an EARLIER step failed,
    and if checkout or setup-python failed then no reading exists — the body's fallback would present a
    synthetic `UNVERIFIABLE` as "The reading (this run)" and point an operator at two PRODUCTION
    deployments for what is a runner fault. A step that RAN and failed has outcome `failure`, not
    `skipped`, so the red path this step exists for is untouched.
    """
    filing = [s for s in _steps() if "Auto-file" in (s.get("name") or "")]
    assert len(filing) == 1, "exactly one auto-file step"
    condition = filing[0]["if"]
    assert "always()" in condition, f"the filing step must survive a failing check step, got {condition!r}"
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
    assert "concurrency" not in workflow or workflow["concurrency"].get("cancel-in-progress") is False, (
        "a cancelled tick measures nothing — `cancel-in-progress` must stay false"
    )


def test_the_two_dedupe_TITLES_are_stable_constants() -> None:
    """The title IS the dedupe key (`auto-file-issue.sh` searches `in:title` and comments a recurrence
    rather than creating a second issue). Renaming one abandons the standing ledger issue and starts a
    second streak — at 48 runs/day that is the duplicate spam the substrate exists to prevent. So both
    titles are pinned byte-exactly, and they must differ: they assert different findings (a MEASURED
    disagreement vs NO measurement).
    """
    body = "\n".join(s.get("run") or "" for s in _steps())
    drift = "CONSENT_TOKEN_SECRET: the consent bridge's two halves hold different keys (#10488)"
    unknown = "CONSENT_TOKEN_SECRET: the consent bridge's key could not be verified (#10488)"
    assert drift in body, "the drift title changed — that abandons the standing issue"
    assert unknown in body, "the unknown title changed — that abandons the standing issue"
    assert drift != unknown, "two findings must not share one dedupe key"


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
