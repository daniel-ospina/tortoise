#!/usr/bin/env python3
"""Consent-key drift detector — do the two halves of the consent bridge hold the SAME key?

WHY THIS EXISTS (#10488)
------------------------
``CONSENT_TOKEN_SECRET`` is ONE secret with TWO homes, and only one of them is managed:

  | half   | origin                                  | who sets the value              |
  |--------|-----------------------------------------|---------------------------------|
  | mint   | ``app.premiselabs.co`` (Cloudflare Pages)| a human, in the CF dashboard    |
  | verify | ``api.premiselabs.co`` (Fly machine)     | CI, from the GitHub repo secret |

Measured 2026-11-01: they held DIFFERENT values. The mint answered ``200`` with a well-formed
680-byte JWT (``aud=tortoise-consent``) and the AS answered::

    401 {"error":"invalid_token",
         "error_description":"Invalid consent token: signature does not verify."}

``tortoise/consent_token.py`` emits ``signature does not verify`` ONLY on an HMAC mismatch, so the
verifier was right both times: the two deployments simply held different keys. Nothing surfaced it —
``deploy-hosted`` was green, ``deploy-pages`` was green, mint returned 200, and the only user-visible
symptom was the consent page telling the visitor "Your approval expired before it was issued", which
accuses the visitor's session rather than the configuration. It cost a full diagnosis cycle.

⛔ WHY THE CONFIGURATION SIDE CANNOT ANSWER THIS. Cloudflare Pages does not return a ``secret_text``
env var's VALUE, and Fly never returns a secret's — so ``tools/check_pages_bindings.py`` (the #3616
gate) can prove the NAME exists on both platforms and can never prove the two VALUES agree. The
question is only answerable from the running deployments, which is why each half publishes a
truncated hash of the key it holds and this tool compares the two readings. One request each.

THE CONTRACT
------------
  * AS half:    ``GET {api}/health`` → ``consent_token_key: {key_fingerprint, configured, reason}``
  * Pages half: ``GET {app}/api/consent/key`` → the SAME three keys
  * ``key_fingerprint`` is ``sha256(key)[:12]`` (``CONSENT_KEY_FINGERPRINT_HEX_CHARS``) — never the
    key, never a prefix of it, never anything invertible.

⛔ THIS TOOL NEVER HOLDS, READS, OR ACCEPTS A KEY, and has no flag that would let it. That is the
strongest form of "no key material in the output": EVERY string copied from a remote is
shape-checked before it can reach a log line, a JSON body, or the issue body the workflow files —
`key_fingerprint` against `FINGERPRINT_RE` (12 lowercase hex) and `reason` against the CLOSED set
`REASON_CODES`. A half that answers with a key, a key prefix, or a whole hash in either field, or
with any other string in `reason`, is reported as UNVERIFIABLE and the offending value is never
echoed. ⛔ That gate is not decoration and this docstring is not a promise to take on trust: an
earlier cut validated `key_fingerprint` only, and `reason` is published verbatim into a PUBLIC
auto-filed issue. Without both checks this tool is a disclosure amplifier — it copies whatever the
remote says into a CI log and an issue body.

VERDICTS (exit codes)
---------------------
  ``AGREE``          0  both halves publish a fingerprint and they are EQUAL.
  ``MISMATCH``       1  both publish one and they DIFFER — the 2026-11-01 defect. Every consent
                        grant is dead; the mint answers 200 and the AS answers 401.
  ``SPLIT``          1  exactly one half is configured — one can sign, the other cannot. Also a
                        split, and also fatal; it is named separately because the REMEDY differs.
  ``OFF``            2  NEITHER half is configured. Consistent, and NOT agreement: a monitor that
                        went green here would read "the consent bridge is fine" about a bridge that
                        is not enabled. Exit 2 says "no drift verdict", which is the truth.
  ``UNVERIFIABLE``   2  a half could not be read, answered non-200, or published a field that is not
                        the contracted shape. ⛔ "I could not look" is never AGREE — the same
                        fail-closed rule as ``check-deploy-staleness.sh``: a deaf checker is worse
                        than no checker.

⚠️ Both exit codes 1 and 2 are RED for a monitor. They differ in what they claim: 1 is a measured
disagreement, 2 is no measurement. Do not collapse them — "cannot see" and "saw a split" need
different responses, and only one of them needs somebody to touch a dashboard.

USAGE
-----
    uv run python tools/consent_key_check.py                 # the two production origins
    uv run python tools/consent_key_check.py --json          # for a workflow / an issue body
    uv run python tools/consent_key_check.py --api-url http://127.0.0.1:8000 \\
                                             --app-url https://preview.example       # a preview pair

Wired into CI by ``.github/workflows/consent-key-drift.yml`` (scheduled, NOT per-deploy — a check
placed only inside ``deploy-pages.yml`` would miss Cloudflare's own Git integration, which builds
Pages directly on push and is the DOMINANT build path).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first with an unrelated message.
# ⛔ AND THE EXIT CODE IS `2`, NOT THE DEFAULT 1. `SystemExit(<str>)` exits **1**, which this tool
# publishes as `EXIT_DISAGREE` — "a measured disagreement" (the runbook §4.4 table an operator reads).
# An interpreter mismatch is NO MEASUREMENT of anything, so reporting it as the 2026-11-01 key split
# is the same false signal this file fixes twice below for a malformed origin and an unparseable
# body. The message is written separately because `SystemExit(int)` prints nothing.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    sys.stderr.write(
        f"tools/consent_key_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/consent_key_check.py`\n"
    )
    raise SystemExit(2)

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

#: The default production pair. Two origins, one secret — the whole point.
DEFAULT_API_URL = "https://api.premiselabs.co"
DEFAULT_APP_URL = "https://app.premiselabs.co"
#: Where each half publishes. Both are UNAUTHENTICATED on purpose: a checker that needed a session
#: could not run from CI, which is where the next rotation must be caught.
API_PATH = "/health"
APP_PATH = "/api/consent/key"
#: The field inside `{api}/health`, and the whole Pages body. Same name on both halves so the
#: comparison is field-for-field rather than a mapping this tool maintains.
PAYLOAD_FIELD = "consent_token_key"
FINGERPRINT_FIELD = "key_fingerprint"
CONFIGURED_FIELD = "configured"
REASON_FIELD = "reason"

#: ⛔ The SAME shape `tortoise/consent_token.py::CONSENT_KEY_FINGERPRINT_RE` and
#: `consent-bridge.ts::consentKeyFingerprint` emit: 12 LOWERCASE hex. Anchored, so `"abc"`, a
#: 40-hex prefix of a real key, an uppercase value, and the key itself all fail — and a failure is
#: UNVERIFIABLE, never a comparison. Case-sensitivity is deliberate: a case-insensitive compare would
#: hide a producer that started emitting something other than `hexdigest()[:12]`.
#: ⛔ `\Z`, NOT `$`: Python's `$` also matches immediately before a TRAILING NEWLINE, so
#: `"<12 hex>\n"` would pass this gate — printing a broken line and then comparing as MISMATCH,
#: which tells an operator to rotate a key that is not wrong. Measured on the `$` version.
FINGERPRINT_RE = re.compile(r"[0-9a-f]{12}\Z")

#: The `reason` vocabulary, as a CLOSED set — the FOURTH disclosure gate, and the one an earlier cut
#: of this file missed while claiming (§ "THE CONTRACT" above) that the fingerprints were the only
#: strings it could print. `reason` is a string chosen by a REMOTE deployment and this tool pastes
#: its output into a CI log and a PUBLIC auto-filed issue body, so an ungated `reason` is exactly the
#: disclosure amplifier the fingerprint gate exists to prevent: a half that answers with the key in
#: `reason` (a debug build, a proxy error page, a future field mix-up) would have it republished.
#: It is also the injection path into the issue body's ```json fence.
#:
#: ⛔ A CLOSED SET, not a charset filter. A filter like `^[a-z][a-z0-9_]{0,31}$` still admits a
#: 32-character all-lowercase key. These six are every code either half can publish —
#: `tortoise/consent_token.py::_SECRET_PROBLEM_CODES` (five, the provisioning classes) plus
#: `unreadable`, which `hosted_api._consent_token_key_health()` emits with `configured: null` when
#: the resolver itself cannot run. A half publishing anything else is UNVERIFIABLE, and the tool says
#: so with a code of its own (`reason_not_a_code`) rather than guessing. Growing this set is a
#: deliberate act that must touch this constant, §2.5 and `docs/infra-runbook.md` together.
REASON_CODES = frozenset(
    {"not_set", "padded", "reused_credential", "too_short", "not_utf8", "unreadable"}
)

#: ⛔ THE SPLIT REMEDY IS NOT ONE SENTENCE, and this set is what decides which one is true. A SPLIT
#: means one half is dark, but it does NOT mean that half is EMPTY. FOUR of the six `reason` codes
#: describe a value the dark half already HOLDS and refuses — `padded`, `reused_credential`,
#: `too_short`, `not_utf8` — and for those "copy the live half's value onto the dark half" is at best
#: a guess: the reading compares fingerprints only, so it cannot tell whether the live value itself
#: clears the dark half's refusal. It MAY clear it (Fly holds " key\n" as `padded` while Pages holds
#: the clean `key`) and it MAY reproduce it (both halves given the same unusable value). One fresh key
#: on BOTH halves is the only repair that always works — and stating the copy as IMPOSSIBLE instead is
#: what pushed an operator whose copy WOULD have worked into rotating the production signing key on
#: both halves.
#:
#: REACHABLE, not theoretical (measured): the Pages half has NO length floor
#: (`consent-bridge.ts::consentKeyFingerprint` guards only `if (!secret)`), while `consent_token.py`
#: refuses anything under `_MIN_SECRET_BYTES` (32) with `too_short`. So an operator who sets the SAME
#: 20-byte key on BOTH halves gets `configured: true` from Pages and `configured: false,
#: reason: "too_short"` from the AS -> SPLIT -> and "copy the live value across" reproduces
#: `too_short` for as long as they keep following it.
#:
#: The two absence codes are the ones where copying IS the repair: nothing is there to copy from.
#: ⚠️ The AS's floors still apply to whatever is copied in — see the `api`-dark arm of `_fix_hint`,
#: whose source (Pages) enforces none of them.
#: ⛔ FOUR value-fault codes, TWO absence codes — the counts are asserted in the suite. (An earlier cut
#: of this comment said "five of the six" beside a list of four; the sentence, not only the note
#: about it, is what the next reader acts on.)
#: ⛔ THE DERIVED SET IS `VALUE_FAULT_REASONS`, and that is the fail-closed direction: `ABSENCE_REASONS`
#: is the explicit literal and `VALUE_FAULT_REASONS` is `REASON_CODES` minus it, so a SEVENTH code
#: added on the publishing side lands in the ROTATION arm — the repair that always works — without
#: anyone having to edit this split. That is the whole reason the derivation is on this side rather
#: than on the absence side.
#: ⚠️ DECLARED RESIDUAL: a seventh code that is an ABSENCE fault would therefore be handed the
#: rotation, which it does not need. Nothing here can infer "absence" from a string, and a code the
#: halves start publishing simply joins `VALUE_FAULT_REASONS` — it does not read as an omission.
#:
#: ⛔ TWO GUARDS WERE CLAIMED HERE AND NEITHER DID THE JOB, so both are recorded rather than swapped:
#:   * `assert len(ABSENCE_REASONS) == 2` was UNREACHABLE — it sat behind an exact-set pin on the same
#:     literal, so any edit that would fail a length arm fails the exact-set arm first and the length
#:     arm can never be the one that reds. It guarded nothing and was removed.
#:   * the EXACT-SET pin on `ABSENCE_REASONS` is a real guard against an EDIT, but it cannot see an
#:     OMISSION: omitting a code from that literal leaves the set exactly as pinned. MEASURED in this
#:     worktree — a seventh ABSENCE-class code added to BOTH `REASON_CODES` and
#:     `consent_token._SECRET_PROBLEM_CODES`, `ABSENCE_REASONS` untouched, left the suite fully green,
#:     and the mutant answers `compare(api dark reason="brand_new", app live)` with "TWO-SIDED
#:     rotation" — an operator rotating the production signing key on both halves for a fault a
#:     one-sided copy repairs.
#:
#: So the guard that can see GROWTH is the suite's TOTAL pin on `REASON_CODES`: adding a code of
#: either class reds it until the addition is classified here deliberately. The set-algebra lines
#: beside it are true by construction (`VALUE` is *defined* as `REASON` minus `ABSENCE`) and catch
#: nothing at all.
ABSENCE_REASONS = frozenset({"not_set", "unreadable"})
VALUE_FAULT_REASONS = REASON_CODES - ABSENCE_REASONS

#: ⛔ THE USER-AGENT IS LOAD-BEARING, and finding out why cost a production measurement. Cloudflare's
#: edge in front of `app.premiselabs.co` refuses urllib's DEFAULT agent — `Python-urllib/3.12`, which
#: is exactly what the workflow's pinned `python-version: 3.12` produces — with
#: `403 error code: 1010`, while `curl`, `python-requests`, a browser agent and even the LOWERCASE
#: `python-urllib/3.12` all pass. Measured 2026-11-13, same URL, same minute, only the UA varied:
#: 403 for `Python-urllib/3.12`, 404 (route not yet deployed) for every other agent. So before this
#: constant existed, the Pages half could NEVER be read from CI: the monitor would have filed
#: "the key could not be verified" every 30 minutes forever and never reached a comparison.
#: ⛔ Do not "simplify" this to no headers: inheriting urllib's default is a permanent UNVERIFIABLE.
USER_AGENT = "consent-key-drift/1.0 (+https://github.com/daniel-ospina/tortoise)"

EXIT_AGREE = 0
EXIT_DISAGREE = 1
EXIT_NO_VERDICT = 2

#: The most body bytes this tool will read from one half. Both endpoints answer a tiny JSON object
#: (three fields, or the whole `/health` document); anything larger is not a reading. A bounded read
#: matters because `response.read()` with no argument buffers whatever the peer sends — a hostile or
#: proxy-streamed body could OOM the workflow's 10-minute job BEFORE any shape gate runs, which is a
#: failure the fingerprint/reason gates cannot see. A truncated legitimate body simply fails to parse
#: and becomes `body_not_json`, i.e. it fails CLOSED.
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Reading:
    """One half's answer, already reduced to what this tool is allowed to print.

    ⛔ `fingerprint` is `None` unless the remote's value matched `FINGERPRINT_RE` — an unvalidated
    string never reaches the output. `problem` is a CODE describing why the half could not be read,
    and it never embeds the offending value.
    """

    half: str
    url: str
    fingerprint: str | None = None
    configured: bool | None = None
    reason: str | None = None
    problem: str | None = None

    @property
    def readable(self) -> bool:
        return self.problem is None


@dataclass
class Verdict:
    verdict: str
    detail: str
    exit_code: int
    readings: list[Reading] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_AGREE


def parse_reading(half: str, url: str, payload: object) -> Reading:
    """Validate one half's payload into a `Reading` — never echoing what it was given.

    Every refusal is a `problem` CODE. The unvalidated value is deliberately NOT included: a
    hostile or misconfigured deployment answering with the key (or its full hash) must not have that
    value copied into a workflow log and, from there, into an issue body.
    """
    if not isinstance(payload, dict):
        return Reading(half, url, problem="payload_not_an_object")
    raw_reason = payload.get(REASON_FIELD)
    # ⛔ THE FOURTH DISCLOSURE GATE. `reason` is remote-chosen and this tool's output is pasted into a
    # PUBLIC issue body, so it is reduced to a CLOSED-SET code before it can be stored — see
    # `REASON_CODES`. `None`/absent is legitimate (a configured half has no fault to report); any
    # other string is a shape fault on the half, not a reason.
    reason: str | None = None
    if raw_reason is not None:
        if not isinstance(raw_reason, str) or raw_reason not in REASON_CODES:
            return Reading(half, url, problem="reason_not_a_code")
        reason = raw_reason
    if CONFIGURED_FIELD not in payload:
        # ⛔ ABSENT IS A SHAPE FAULT, and it is NOT `false`: "this deployment did not say" and "this
        # deployment has no key" are different readings, and only the second can be compared against
        # the other half's silence.
        return Reading(half, url, problem="configured_not_a_bool")
    configured = payload[CONFIGURED_FIELD]
    if configured is None:
        # ⛔ THE CONTRACT'S THIRD STATE, and refusing it was a contract bug. `configured: null` is what
        # a half publishes when its OWN resolver could not run —
        # `hosted_api._consent_token_key_health()` returns exactly
        # `{"key_fingerprint": None, "configured": None, "reason": "unreadable"}` on any exception —
        # and SPEC §2.5, runbook §4.4 and this file's own `REASON_CODES` comment all name `unreadable`
        # as the code paired with it. Reporting `configured_not_a_bool` accused the AS of a malformed
        # body for the one answer that means "I could not state my key", and left the sixth reason
        # code unproducible through the only shape that publishes it.
        # It stays UNREADABLE — `problem` is set, so `compare`'s first gate routes it to UNVERIFIABLE
        # before `configured` is ever coerced to bool. Never AGREE, and never OFF: "both halves
        # crashed" is not "the feature is disabled".
        return Reading(half, url, configured=None, reason=reason, problem="unreadable")
    if not isinstance(configured, bool):
        return Reading(half, url, problem="configured_not_a_bool")
    raw_fp = payload.get(FINGERPRINT_FIELD)
    if configured:
        if not isinstance(raw_fp, str) or not FINGERPRINT_RE.match(raw_fp):
            # ⛔ THE DISCLOSURE GATE, and the one place a "helpful" implementation goes wrong: the
            # obvious `f"bad fingerprint {raw_fp!r}"` would republish exactly what the shape check
            # exists to contain.
            return Reading(
                half, url, configured=True, reason=reason,
                problem=f"{FINGERPRINT_FIELD}_not_12_lowercase_hex",
            )
        return Reading(half, url, fingerprint=raw_fp, configured=True, reason=reason)
    # A half that says it is NOT configured must not ALSO publish a fingerprint — that is a
    # deployment contradicting itself, and comparing either field would be guessing which one it
    # meant.
    if raw_fp is not None:
        return Reading(
            half, url, configured=False, reason=reason,
            problem="unconfigured_but_fingerprint_present",
        )
    return Reading(half, url, configured=False, reason=reason)


def compare(api: Reading, app: Reading) -> Verdict:
    """The one decision. Pure: no I/O, so the exit-code table is testable without a network."""
    readings = [api, app]
    unreadable = [r for r in readings if not r.readable]
    if unreadable:
        return Verdict(
            "UNVERIFIABLE",
            "could not read " + ", ".join(f"{r.half}({r.problem})" for r in unreadable),
            EXIT_NO_VERDICT,
            readings,
        )
    configured = {r.half: bool(r.configured) for r in readings}
    if not any(configured.values()):
        return Verdict(
            "OFF",
            "neither half is configured — consistent, but the consent bridge is DISABLED, so this is "
            "no verdict on drift",
            EXIT_NO_VERDICT,
            readings,
        )
    if configured["api"] != configured["app"]:
        live = [r.half for r in readings if r.configured]
        dark = [r.half for r in readings if not r.configured]
        # ⛔ THE CONSEQUENCE FOLLOWS THE DARK HALF, because the two halves do NOT do the same thing:
        # `app` MINTS (its Pages Function signs the consent token) and `api` VERIFIES it. A single
        # static sentence — "one half signs, the other cannot verify" — was right in one orientation
        # and INVERTED in the other, and this detail is pasted verbatim into the PUBLIC alert body, so
        # the wrong version tells an operator that the working half is the broken one. Pinned in BOTH
        # orientations, because one orientation cannot see the inversion.
        # The `elif` is deliberate: an unrecognised state must NOT inherit a directional claim, so the
        # `else` fails closed with a sentence that asserts no role (unreachable while there are two
        # halves, but a third would otherwise be silently labelled with the `api` consequence).
        if dark == ["app"]:
            consequence = "the `app` half mints, so nothing can sign a consent token"
        elif dark == ["api"]:
            consequence = "the `api` half verifies, so every minted token is refused"
        else:                                           # pragma: no cover - unreachable today
            consequence = f"{'/'.join(dark)} is unreadable, so the bridge cannot be used"
        return Verdict(
            "SPLIT",
            f"{'/'.join(live)} is configured and {'/'.join(dark)} is not — {consequence}",
            EXIT_DISAGREE,
            readings,
        )
    if api.fingerprint != app.fingerprint:
        return Verdict(
            "MISMATCH",
            "both halves are configured and hold DIFFERENT keys — every minted consent token is "
            "refused `401 signature does not verify`",
            EXIT_DISAGREE,
            readings,
        )
    return Verdict("AGREE", "both halves publish the same key fingerprint", EXIT_AGREE, readings)


def _get_json(url: str, timeout: float) -> tuple[object | None, str | None]:
    """`(payload, problem)`. Any transport/parse failure is a `problem` CODE, never an exception."""
    # ⛔ `User-Agent` is NOT cosmetic here — see `USER_AGENT`: urllib's default is refused 403 by the
    # edge in front of the Pages project, which would make the Pages half permanently unreadable.
    try:
        # ⛔ `Request(...)` ITSELF CAN RAISE, so it belongs INSIDE this `try`. A scheme-less origin —
        # the natural typo in an operator's `--api-url api.premiselabs.co` — raises
        # `ValueError("unknown url type")`, and an ESCAPING exception exits **1**, which is
        # `EXIT_DISAGREE`: the tool would report an input/tool fault as the 2026-11-01 MEASURED SPLIT,
        # i.e. exactly the false signal this whole workflow exists to refuse. `ValueError` is neither
        # `OSError` nor `URLError`, so the broad tuple below never caught it — measured at this head,
        # `--api-url api.example.com` exited 1 with the traceback on stderr.
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "unreachable"
    except ValueError:
        # A malformed origin is NO MEASUREMENT — `UNVERIFIABLE` (exit 2), never a disagreement.
        return None, "invalid_url"
    except Exception:                                   # pragma: no cover - defensive
        return None, "transport_error"
    if status != 200:
        return None, f"http_{status}"
    try:
        return json.loads(body), None
    except (ValueError, UnicodeDecodeError):
        return None, "body_not_json"
    except Exception:
        # ⛔ `RecursionError` is neither `ValueError` nor `UnicodeDecodeError` (it is a
        # `RuntimeError`), so a deeply nested body — measured with 200k nested arrays — raised past
        # every handler and the process died with **exit 1**, which is `EXIT_DISAGREE`. That is the
        # SAME defect as the malformed-origin one above, one line away from it: a tool fault reported
        # as the 2026-11-01 measured split. The docstring promises "a problem CODE, never an
        # exception", so the parse is guarded by the contract, not by a list of exception names.
        return None, "body_not_json"


def _redact_url(url: str) -> str:
    """`scheme://host[:port]/path` only — userinfo, query and fragment are DROPPED.

    ⛔ THE ONE STRING IN THE PUBLIC ISSUE BODY THIS TOOL DID NOT GATE. `Reading.url` is printed in the
    CI log AND `cat`-ed into an auto-filed issue in a PUBLIC repo, and `--api-url`/`--app-url` are
    free-form values (the workflow's `workflow_dispatch` inputs). The natural shape of a protected
    preview origin is `https://user:token@host`, so echoing the origin verbatim republishes a
    credential — which is exactly the disclosure-amplifier behaviour every other field here refuses.
    The REQUEST still uses the full URL (a token may be needed to reach the half); only what is
    STORED and PRINTED is reduced. Unparseable input cannot be echoed either.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port is not None else host
        if not netloc:
            return "<unparseable origin>"
        return urllib.parse.urlunsplit((parts.scheme or "https", netloc, parts.path, "", ""))
    except ValueError:                                  # a bad port, an unbalanced IPv6 literal, …
        return "<unparseable origin>"


def read_api(api_url: str, timeout: float) -> Reading:
    """The Fly half — `{api}/health` → `consent_token_key`."""
    url = api_url.rstrip("/") + API_PATH
    payload, problem = _get_json(url, timeout)
    if problem:
        return Reading("api", _redact_url(url), problem=problem)
    block = payload.get(PAYLOAD_FIELD) if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        # ⛔ A `/health` WITHOUT this block is not "an older deployment is fine": it is a deployment
        # that cannot answer the question, which is the shape that made this drift invisible.
        return Reading("api", _redact_url(url), problem=f"{PAYLOAD_FIELD}_absent")
    return parse_reading("api", _redact_url(url), block)


def read_app(app_url: str, timeout: float) -> Reading:
    """The Pages half — `{app}/api/consent/key` is the payload itself."""
    url = app_url.rstrip("/") + APP_PATH
    payload, problem = _get_json(url, timeout)
    if problem:
        return Reading("app", _redact_url(url), problem=problem)
    return parse_reading("app", _redact_url(url), payload)


def _fix_hint(verdict: Verdict) -> str:
    """What to DO — and ⛔ a SPLIT is NOT a rotation, so it must not borrow MISMATCH's advice."""
    if verdict.verdict == "SPLIT":
        # ⛔ ONE HALF IS DARK, so there is no second value to reconcile and a ONE-SIDED rotation
        # MANUFACTURES the measured disagreement — the 2026-11-01 incident this check exists to catch.
        # Emitting MISMATCH's hint here was actively harmful: its step (1) is a one-sided
        # `gh secret set` + `deploy-hosted`. Named BEFORE the MISMATCH arm so the two cannot be
        # confused.
        #
        # ⛔ BUT "ONE HALF IS DARK" IS NOT ONE STATE, and a single sentence cannot serve it:
        #
        #   (a) THE REMEDY DEPENDS ON THE DARK HALF'S `reason`, and this tool sees only
        #       FINGERPRINTS. An ABSENCE code (`not_set`, `unreadable`) means nothing is there, so
        #       copying the live value IS the repair. A VALUE FAULT (`padded`, `too_short`,
        #       `not_utf8`, `reused_credential` — FOUR of the six, not five) means the dark half
        #       already HOLDS a value it refuses, so copying may reproduce the refusal. It may ALSO
        #       clear it, when the live half happens to hold a different usable value (e.g. Fly holds
        #       " key\n" as `padded` while Pages holds the clean `key`) — the reading cannot
        #       distinguish the two. So the claim is not "a copy can never work"; it is that a
        #       two-sided rotation is the only repair that ALWAYS works, which is what the hint says.
        #       Stating it as a certainty pushed an operator whose one-sided copy WOULD have cleared it
        #       into rotating the production signing key on both halves under a false premise.
        #
        #   (b) THE ABSENCE REPAIR DEPENDS ON WHICH HOME IS DARK, because the two are written
        #       differently. `app` (Pages) is set by hand in the dashboard, so the repair is
        #       "set it there, then redeploy Pages". `api` (Fly) is CI-managed from the GitHub repo
        #       secret, so the repair is `gh secret set` + `deploy-hosted` — NOT `fly secrets set`,
        #       which the next deploy silently reverts (`.github/scripts/fly-managed-secrets.txt`
        #       declares it `gh-secret:`). Naming the dark half as the write TARGET without naming
        #       the route sent an api-dark operator to the one write the same issue body says is
        #       reverted: they clear the alert, the next deploy re-stages the stale repo value, and
        #       the monitor re-reds as MISMATCH for a fault that was `not_set`.
        #
        # An unmapped `reason` never reaches here — it is UNVERIFIABLE, never SPLIT (the closed-set
        # gate is upstream), so the `else` below is the two absence codes.
        reasons = {r.reason for r in verdict.readings if not r.configured}
        dark_halves = sorted(r.half for r in verdict.readings if not r.configured)
        if reasons & VALUE_FAULT_REASONS:
            return (
                f"fix: the reliable repair is a TWO-SIDED rotation. The dark half "
                f"({'/'.join(dark_halves)}) reports a `reason` that means the value it HOLDS is "
                "unusable. Copying the working half's value across MIGHT clear it, but this check "
                "compares fingerprints only, so it cannot tell a working half that holds a DIFFERENT "
                "usable value from one that holds the same unusable one — and in the second case the "
                "copy reproduces the refusal. A fresh key on BOTH halves is the only repair that "
                "always works: (1) `gh secret set CONSENT_TOKEN_SECRET` (the GitHub repo secret is "
                "the SOURCE OF TRUTH: `fly secrets set` alone is silently reverted by the next "
                "deploy-hosted run, because `.github/scripts/fly-managed-secrets.txt` declares it "
                "`gh-secret:` and re-stages it), (2) set the same value on the "
                "`tortoise-dashboard` Pages project, (3) REDEPLOY Pages (a corrected value does NOT "
                "reach an already-deployed Function). ⛔ Do not STOP after step 1: a half-applied "
                "rotation reads as MISMATCH until Pages is redeployed — that is the rotation in "
                "progress, not a second fault, so finish step 3 rather than re-rotating. §2.5."
            )
        # ABSENCE (`not_set` / `unreadable`): nothing is on the dark half, so copying the live value
        # IS the repair — but WHERE you write it differs per half, and getting that wrong is how the
        # alert clears and then re-reds. Branched on the dark half, which the reading prints.
        if dark_halves == ["app"]:
            return (
                "fix: do NOT rotate a SINGLE side — the dark half (`app`) holds no usable key, so "
                "there is nothing to reconcile, and a one-sided rotation would leave the two halves "
                "holding DIFFERENT keys (a MISMATCH — the 2026-11-01 incident), not create another "
                "split. Set the SAME "
                "`tortoise-dashboard` Pages project (Cloudflare dashboard → Settings → Environment "
                "variables — it is not CI-written), then REDEPLOY Pages: Pages bakes env at deploy "
                "time, so a corrected dashboard value does NOT reach an already-deployed Function. "
                "⛔ If that live value cannot be OBTAINED (a dashboard `secret_text` is never read "
                "back, the Fly secret is never read back, and a repo secret is write-only), the copy "
                "is not executable — a rotation of BOTH halves together is then the repair that "
                "works, and it is the ONLY one. §2.5."
            )
        return (
            "fix: do NOT rotate a SINGLE side — the dark half (`api`) holds no usable key, so there "
            "is nothing to reconcile, and a one-sided rotation would leave the two halves holding "
            "DIFFERENT keys (a MISMATCH — the 2026-11-01 incident), not create another split. "
            "Set the SAME value the LIVE half (`app`) already holds in the GITHUB REPO SECRET "
            "(`gh secret set CONSENT_TOKEN_SECRET`), then land (or `workflow_dispatch`) the "
            "`deploy-hosted` run so Fly is staged from it. ⚠️ The AS enforces floors the Pages half "
            "does NOT (Pages accepts any non-empty string), so a copy can still be refused: if that "
            "value is under 32 bytes, padded, not UTF-8, or a reused credential, the check stays "
            "SPLIT with a `too_short`/`padded`/`not_utf8`/`reused_credential` reason — in that case, "
            "or if the live value cannot be OBTAINED, rotate BOTH halves instead. "
            "⛔ Do NOT hand-set it with `fly secrets set`:"
            "`.github/scripts/fly-managed-secrets.txt` declares the binding `gh-secret:`, so every "
            "`deploy-hosted` run re-stages the repo value and silently reverts a hand-set Fly value "
            "— after you had seen AGREE. §2.5."
        )
    if verdict.verdict == "MISMATCH":
        return (
            "fix: a rotation is THREE steps, because Pages bakes env at deploy time — "
            "(1) `gh secret set CONSENT_TOKEN_SECRET` (the GitHub repo secret is the SOURCE OF TRUTH: "
            "`fly secrets set` alone is silently reverted by the next deploy-hosted run, because "
            "`.github/scripts/fly-managed-secrets.txt` declares it `gh-secret:` and re-stages it), "
            "(2) set the same value on the `tortoise-dashboard` Pages project, "
            "(3) REDEPLOY Pages (a corrected value does NOT reach an already-deployed Function). "
            "Correcting the dashboard alone leaves the deployed Function signing with the old key — "
            "measured 2026-11-01."
        )
    if verdict.verdict == "OFF":
        # ⛔ NO "see above" here: `render()` prints exactly ONE hint, and the three-step rotation is
        # emitted only by the MISMATCH/SPLIT arm — so a cross-reference would dangle in this arm's own
        # output. Name the doc instead, which is where the runbook actually lives.
        return (
            "fix: nothing is wrong with the CONFIGURATION — the bridge is simply not enabled on "
            "either half, so there is no drift to fix. If it is meant to be live, the three-step "
            "rotation (rotate the GitHub repo secret, set Pages, REDEPLOY Pages) is in "
            "docs/engineering/auth/consent-identity-bridge.md §2.5."
        )
    return "fix: re-read both origins; a half that cannot be read is not a half that agrees."


def render(verdict: Verdict) -> str:
    lines = [f"CONSENT-KEY: {verdict.verdict}"]
    for reading in verdict.readings:
        if not reading.readable:
            lines.append(f"  {reading.half:<4} {reading.url}  UNREADABLE ({reading.problem})")
            continue
        shown = reading.fingerprint if reading.fingerprint else "—"
        lines.append(
            f"  {reading.half:<4} {reading.url}  fingerprint={shown} "
            f"configured={str(bool(reading.configured)).lower()}"
            + (f" reason={reading.reason}" if reading.reason else "")
        )
    lines.append(f"  detail: {verdict.detail}")
    if not verdict.ok:
        lines.append(f"  {_fix_hint(verdict)}")
    return "\n".join(lines)


def as_json(verdict: Verdict) -> str:
    return json.dumps(
        {
            "verdict": verdict.verdict,
            "exit_code": verdict.exit_code,
            "detail": verdict.detail,
            "fix": None if verdict.ok else _fix_hint(verdict).removeprefix("fix: "),
            "readings": [
                {
                    "half": r.half,
                    "url": r.url,
                    "fingerprint": r.fingerprint,
                    "configured": r.configured,
                    "reason": r.reason,
                    "problem": r.problem,
                }
                for r in verdict.readings
            ],
        },
        indent=2,
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the consent bridge's key fingerprint on its two origins (#10488).",
        epilog=(
            "Exit codes: 0 AGREE · 1 MISMATCH/SPLIT (measured disagreement) · "
            "2 OFF/UNVERIFIABLE (no verdict; both RED for a monitor)."
        ),
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=f"AS origin (default {DEFAULT_API_URL})")
    parser.add_argument("--app-url", default=DEFAULT_APP_URL, help=f"session origin (default {DEFAULT_APP_URL})")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds (default 15)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    verdict = compare(
        read_api(args.api_url, args.timeout),
        read_app(args.app_url, args.timeout),
    )
    print(as_json(verdict) if args.json else render(verdict))
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
